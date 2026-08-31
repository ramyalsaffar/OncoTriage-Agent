##################################################
# Stage 5 PER-TRIAL calls and cache-aware dispatch
##################################################

"""Stage 5 can send one trial per request, and the switch is OFF.

WHY THIS FILE EXISTS
--------------------
``oncotriage/config.py``'s input-packing block records a measured fault that a
size budget cannot remove: "reasoning demonstrably leaks between trials inside
one prompt, which is the thing constraint C4 asks the model not to do and cannot
enforce". Only a partition of one trial per request removes it, and the reason
that was not the first answer is price -- N requests re-send the system message
N times. PROMPT_VERSION 1.6.0 moved the patient record INTO the system message,
so every request of one patient now shares a byte-identical prefix that the
provider discounts from the second request on, and this pass builds the arm that
uses it. Which mode a published number is computed under is a MEASUREMENT and is
not decided here; both arms have to exist first.

WHAT IT HOLDS
-------------
    1. THE CONFIGURATION SURFACE: the switch defaults OFF, the vocabulary is
       closed, and ``config.matching_call_mode()`` is the ONE owner -- read by
       the node that partitions and by the writer that records the column, so a
       row cannot name a mode the node did not run.
    2. THE PARTITION with the switch ON: one request per trial, each carrying
       exactly its own trial, the union complete and disjoint, and the packer
       recorded as BYPASSED rather than as having run.
    3. THE SCHEDULING SHAPE, proven by a stub that records ORDER rather than by
       timing: the first call has COMPLETED before any parallel call is issued,
       the in-flight bound is respected, and the parallel calls really do
       overlap (a barrier they must all reach, which a sequential
       implementation cannot satisfy).
    4. DETERMINISTIC MERGE: responses arrive in whatever order the scheduler
       chooses and everything the node publishes is in TRIAL order -- the
       verdicts, ``call_details``' 1..N numbering, and each entry's
       ``call_index``. Driven with a stub that answers in REVERSE order on
       purpose.
    5. PER-CALL FAILURE ISOLATION: one raised call costs that trial, which is
       recorded with its own reason and its own counter; the patient completes.
       And the floor -- when EVERY call fails the node returns the API-error
       result, so a total outage is covered by MAX_LLM_CLASSIFIER_RETRIES
       instead of being reported as a run of not-evaluable trials.
    6. OUT-OF-SET SEMANTICS AGAINST SINGLETON CHUNK IDS: with one trial per
       call, an entry naming another REAL candidate is a cross-chunk repeat and
       costs the patient nothing, while an invented id is a fabrication and
       reaches ``hallucinated_trials``.
    7. THE CACHE, MEASURED AND NOT ASSUMED: the provider's own
       ``cached_tokens`` reaches ``call_details`` per call and the patient total
       reaches ``llm_classifier_cached_input_tokens``, with absence carried as
       None rather than as 0.
    8. THE RETAINED GROUPED ARM IS EXACT: with the switch explicitly off the
       node issues the identical requests, field for field, that a copy of the
       module with the whole mechanism compiled out issues -- and publishes an
       identical packing record and an identical stored prompt. Per-trial is
       the SHIPPED default, so this is no longer a statement about a dormant
       branch: it is what makes the migration's comparison arm worth
       measuring against.
    9. THE CONTROLS. Every assertion above is shown to FAIL when the thing it
       checks is broken. Every plant goes into an in-memory COPY of the module;
       the source file is hashed before any plant and compared at the end, with
       a non-degeneracy probe on the comparison itself.

THE ARM IS ALWAYS SET EXPLICITLY, AND THAT IS THE FILE'S IDIOM
--------------------------------------------------------------
EVERY drive in this file goes through ``run_node``, which takes ``per_trial``
as a REQUIRED-IN-PRACTICE keyword and writes ``config.MATCHING_PER_TRIAL_
CALLS_ENABLED`` on the module for the duration of the call, restoring it in a
``finally``. No section reads the shipped default to decide what it exercises.

THAT IDIOM WAS NOT CHANGED WHEN THE DEFAULT FLIPPED TO PER-TRIAL, and the
choice is deliberate rather than inertia. A file whose ON-arm sections read the
default would (i) exercise NOTHING the day the default moved back for a
comparison campaign, silently -- the arm sections would all become second
copies of section 8 -- and (ii) make every assertion in it a statement about
two things at once, so a failure could not distinguish "the mechanism broke"
from "somebody moved the default". Explicit on both sides means this file
measures the MECHANISM and check 1a alone measures the DECISION.

THE ONE PLACE THE DEFAULT IS READ IS CHECK 1a, which is where the shipped
decision is written down, plus check 10e's restore -- and 10e reads
``_START_CONFIG``, captured at import, rather than a literal.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
The packer's arithmetic and the OFF-switch equivalence of PACKING
(``tests/test_agent_stage5_input_packing.py``), the prompt's bytes
(``tests/test_agent_prompt_version.py``), the block-slicing identity this file
depends on (``tests/test_agent_stage5_render_slice_equality.py``), and the
generic merge/duplicate/reconciliation behaviour over chunks
(``tests/test_agent_out_of_set_detector.py``). Per-trial mode changes only how
the first generation of chunks is produced and how those requests are
DISPATCHED; it reuses all of that unchanged.

NO NETWORK, NO KEYS, NO SPEND, NO SUBPROCESS, NO FIXTURE, NO GIT, NO CORPUS,
NO MODEL, NO LIVE SERVER. Every response is a literal served by a stub installed
through ``oncotriage.agent.deps``.

IT DOES OPEN SQLite, in section 9b and nowhere else: the column this pass adds
is written by ``oncotriage/storage/database_logger.py`` and a round trip is the
only thing that can say it survives the INSERT. Every database is a scratch file
inside a ``tempfile.mkdtemp`` that is asserted to differ from the production
path before anything is written, removed at the end, and asserted gone.

NOT IN THE COLLISION MATRIX, derived rather than assumed: it writes nothing in
the repository, and the two source files it reads --
``oncotriage/agent/evaluation.py`` and
``oncotriage/storage/database_logger.py`` -- are written by neither of the
suite's two writers. Both are sha256-compared at the end.

IT EXECS -- one in-memory copy of ``oncotriage/agent/evaluation.py`` per
control, fourteen of them, each with a different plant. Argued at
``_EXEC_ALLOWLIST`` in ``tests/test_package_invariants.py``: the branches under
test are NEW, so ``git show`` has no revision carrying a version with one of
them broken, and several controls relax a guard while leaving the rest of the
module correct -- a state no commit has ever had.

Run from terminal:
    python tests/test_agent_stage5_per_trial_calls.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# The package bootstrap every file in tests/ carries. The candidate directory
# is the PARENT of this file's, because the package sits beside tests/ rather
# than inside it. `pip install -e .` makes the whole block a no-op.
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

import ast
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import types

from oncotriage import config
from oncotriage.agent import deps
from oncotriage import degradation as _degradation
from oncotriage.agent import evaluation as _evaluation
from oncotriage.fixtures import capture as _capture
from oncotriage.storage import database_logger as _dl
from oncotriage.agent.evaluation import (
    NOT_EVALUABLE_CALL_FAILED,
    NOT_EVALUABLE_MODEL_OMITTED,
    NOT_EVALUABLE_TRUNCATION_FLOOR,
    PER_TRIAL_CALL_FAILURES,
    PER_TRIAL_WARMUP_DEGRADATIONS,
    WARMUP_FAILURE_KEY_PREFIX,
    WARMUP_FALLBACK_WRITER_FAILURE_KEY_PREFIX,
    WARMUP_REJECTED_CACHE_KEY,
    WARMUP_REJECTED_MINIMAL_OUTPUT,
    MatchingModelMismatchError,
    PackingBlockMismatchError,
    PerTrialParallelismError,
    PerTrialProviderUnsupportedError,
    assert_per_trial_provider_supported,
    classify_warmup_rejection,
    node_llm_classifier_evaluation,
)


_EVALUATION_PATH = os.path.abspath(_evaluation.__file__)
_DB_LOGGER_PATH = os.path.abspath(
    os.path.join(os.path.dirname(os.path.dirname(_EVALUATION_PATH)),
                 "storage", "database_logger.py"))

# Hashed BEFORE anything else runs, so section 10's comparison is against the
# tree as it was found rather than against whatever a plant left behind.
_BASELINE_HASHES = {
    p: hashlib.sha256(open(p, "rb").read()).hexdigest()
    for p in (_EVALUATION_PATH, _DB_LOGGER_PATH)
}

# CAPTURED FOR THE SAME REASON AND AT THE SAME MOMENT: the two config constants
# this file writes, read once before anything writes either. Section 10's
# restore check compares against THESE rather than against literals, so a
# legitimate change to the shipped default -- which this file has now seen
# once -- moves check 1a and nothing else. A literal in the restore check is a
# second copy of a shipped default that fails while naming a restore.
_START_CONFIG = (config.MATCHING_PER_TRIAL_CALLS_ENABLED,
                 config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS)


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

    THE ABORT CLASS THIS PROJECT HAS SHIPPED TEN TIMES. A bare
    ``result["key"]`` or ``events[0]`` inside a ``check(...)`` argument list
    raises while the argument is being EVALUATED -- and it raises on precisely
    the defect the check exists to catch, so the run prints one traceback where
    it owes a summary and a list of named failures. Every raise-capable read in
    this file goes through ``at()`` or ``drive()`` and comes back as one of
    these instead.
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
    """``container[key]``, or an _Absent naming what was missing."""
    try:
        return container[key]
    except Exception as exc:                                  # noqa: BLE001
        return _Absent(why or f"{type(exc).__name__}: {exc}")


def drive(fn, *args, **kwargs):
    """Call into production code; a RAISE becomes a value a check can fail on."""
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                              # noqa: BLE001
        return _Absent(f"raised {type(exc).__name__}: {exc}")


PATIENT = {
    "patient_id": "stage5-per-trial-patient",
    "demographics": {"age": 61, "sex": "female", "race": "white",
                     "ethnicity": "not hispanic or latino"},
    "conditions": [{"code": "254837009",
                    "display": "Malignant neoplasm of breast (disorder)",
                    "verification_status": "confirmed"}],
    "medications": [], "allergies": [], "observations": [], "procedures": [],
}


def trial(index, criteria_chars=400, nct_id=None):
    """A trial object in the shape ``_build_trials_text`` reads."""
    half = "x" * (criteria_chars // 2)
    return {
        "trial": {
            "nct_id": nct_id or "NCT%08d" % index,
            "title": f"Trial {index}",
            "phase": "PHASE2",
            "eligibility": {
                "inclusion_criteria": "Inclusion Criteria:\n- " + half,
                "exclusion_criteria": "Exclusion Criteria:\n- " + half,
            },
        }
    }


def ids_in(request):
    """The nct_ids fenced into one request's user message, in order."""
    return re.findall(r"<<<TRIAL_DATA nct_id=(\S+) ",
                      request["messages"][1]["content"])


# ===========================================================================
# THE STUB
# ===========================================================================
#
# Every response is a literal. NOTHING HERE COSTS A CENT: the client is replaced
# through oncotriage.agent.deps, which is THE seam.
#
# IT RECORDS ORDER, NOT TIME, and that is what makes section 3 a proof rather
# than a race. Each call takes a monotonic ticket on entry and another on exit
# from one lock-protected counter, so "the first call had finished before the
# second started" is an integer comparison over events the stub itself observed.
# A sleep-and-measure version of the same assertion would pass on a fast machine
# and fail on a loaded one, and would prove nothing on either.


class _StubUsage:
    def __init__(self, cached=None, prompt_tokens=1000):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = 100
        self.completion_tokens_details = None
        if cached is not None:
            self.prompt_tokens_details = type(
                "_D", (), {"cached_tokens": cached})()


class _StubMessage:
    def __init__(self, content):
        self.content = content
        self.refusal = None


class _StubChoice:
    def __init__(self, content, finish_reason="stop"):
        self.message = _StubMessage(content)
        self.finish_reason = finish_reason


class _StubResponse:
    def __init__(self, content, cached=None, prompt_tokens=1000):
        self.choices = [_StubChoice(content)]
        self.usage = _StubUsage(cached, prompt_tokens)
        self.model = config.MATCHING_MODEL


def _eligible_body(nct_ids):
    return json.dumps({"evaluations": [
        {"assessment": "No known disqualifiers.", "eligible": "eligible",
         "inclusion_criteria": [{"criterion": "Age 18+",
                                 "patient_value": "61", "status": "met"}],
         "exclusion_criteria": [], "match_score": 0.0, "nct_id": i}
        for i in nct_ids]})


# ── THE WARMUP IS A REQUEST WITH NO TRIAL IN IT ──────────────────────────
#
# Recognised by its USER MESSAGE, which the node reads from
# `config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE`, rather than by "the first
# request" or "a request the id regex found nothing in". Both of those are
# properties of the schedule, and the schedule is what is under test: a defect
# that stopped sending the warmup would make request 0 a trial call, and a test
# that DEFINED request 0 as the warmup could not see it.


def is_warmup(request):
    """Is this recorded request the cache warmup?"""
    return (request["messages"][1]["content"]
            == config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE)


class _WarmupRefused(Exception):
    """A provider 400 that NAMES a parameter, as the SDK presents one.

    ``status_code`` on the exception is the OpenAI SDK's own shape
    (``APIStatusError``); the message is what
    ``evaluation.classify_warmup_rejection`` matches a parameter name in. Built
    here rather than imported from openai because the classifier is documented
    to key on the SHAPE of an exception and not on its class, and a control
    that used the real class would leave that claim untested.
    """

    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


class _Stub:
    """Answers the trials it was ASKED about, and records the call order.

    ``answer`` overrides the body per nct_id; ``fail_for`` raises for the named
    trials; ``barrier_size`` makes every post-warmup call wait for that many
    peers, which a sequential dispatcher cannot satisfy and which therefore
    proves the parallelism rather than assuming it.

    ``warmup_raise`` is the exception the WARMUP call raises -- a transport
    failure when it is an ordinary Exception, a refusal of the request shape
    when it is a ``_WarmupRefused``. ``warmup_model`` overrides the answering
    model on the warmup response only, which is what separates "the wrong judge
    answered" from "a trial call went wrong".
    """

    def __init__(self, *, cached=None, fail_for=(), answer=None,
                 barrier_size=None, delay=0.0, cached_first_only=False,
                 barrier_all=False, barrier_timeout=15.0, refuse_for=(),
                 bad_json_for=(), warmup_raise=None, warmup_model=None,
                 warmup_cached=None, warmup_prompt_tokens=None,
                 truncate_for=(), interrupt_for=(), hold=None,
                 model_for=None):
        self.requests = []          # arrival order
        self.events = []            # ("enter"|"exit", call_no, ticket)
        self.cached = cached
        self.cached_first_only = cached_first_only
        self.fail_for = set(fail_for)
        # A REFUSAL and MALFORMED JSON are RESPONSES, not raised calls: the
        # request succeeded and was billed. They are what section 5b measures,
        # because they are the two shapes that end the node while N-1 other
        # responses are already paid for and sitting unread.
        self.refuse_for = set(refuse_for)
        self.bad_json_for = set(bad_json_for)
        # A TRUNCATED RESPONSE is a third such shape: the request succeeded and
        # was billed, and `finish_reason == "length"` is the API stating that it
        # ran out of room. It is what section 2b drives the reactive splitter
        # with, because that branch is read from finish_reason and is reached
        # BEFORE the body is parsed.
        self.truncate_for = set(truncate_for)
        # A KeyboardInterrupt RAISED INSIDE A WORKER, which is exactly the
        # propagation shape a real SIGINT produces at `future.result()`:
        # `_issue` catches `Exception`, so a BaseException that is not an
        # Exception travels out of the worker, into the future, and is re-raised
        # on the node thread. A REAL signal is deliberately not used -- this
        # file runs beside sixty others in CI bucket A and a stray SIGINT
        # delivered a moment early or late aborts the run instead of measuring
        # it, which is the abort class this file's `_Absent` exists to prevent.
        self.interrupt_for = set(interrupt_for)
        self.interrupt_raised = threading.Event()
        # A gate every OTHER trial call parks on, so the pool's workers are
        # provably occupied and cannot drain the queue while the node is
        # cancelling it. Released by the scenario's own watchdog; see section 2c.
        self.hold = hold
        self.answer = answer or {}
        self.warmup_raise = warmup_raise
        self.warmup_model = warmup_model
        # THE ANSWERING MODEL, PER TRIAL. `warmup_model` covers the warmup and
        # nothing else, which separates "the wrong judge answered" from "a trial
        # call went wrong" -- and leaves a third case uncovered: the wrong judge
        # answering a trial call whose response the send loop never READS. That
        # is section 5c's subject, and this is what lets it be driven.
        self.model_for = dict(model_for or {})
        self.warmup_cached = warmup_cached
        self.warmup_prompt_tokens = warmup_prompt_tokens
        self.delay = delay
        self.in_flight = 0
        self.max_in_flight = 0
        self.barrier_broken = False
        self._barrier = (threading.Barrier(barrier_size)
                         if barrier_size else None)
        # `barrier_all` makes the PRIMING call wait too, which the shipped
        # scheduler cannot satisfy -- it awaits that call alone, so the barrier
        # times out and breaks. That is the control for "one first, then the
        # rest": a dispatcher that fired everything at once WOULD satisfy it.
        self.barrier_all = barrier_all
        self.barrier_timeout = barrier_timeout
        self._lock = threading.Lock()
        self._ticket = 0
        self.chat = type("_C", (), {"completions": self})()

    def _tick(self):
        self._ticket += 1
        return self._ticket

    def create(self, **kwargs):
        ids = re.findall(r"<<<TRIAL_DATA nct_id=(\S+) ",
                         kwargs["messages"][1]["content"])
        warmup = is_warmup(kwargs)
        with self._lock:
            call_no = len(self.requests)
            self.requests.append(kwargs)
            self.events.append(("enter", call_no, self._tick()))
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            # THE WARMUP IS ANSWERED BEFORE THE BARRIER, and it has to be: the
            # node awaits it alone, so a warmup that joined a barrier sized for
            # the wave would deadlock every scenario rather than measure one.
            if warmup:
                if self.warmup_raise is not None:
                    raise self.warmup_raise
                _resp = _StubResponse(
                    "", cached=(self.warmup_cached
                                if self.warmup_cached is not None
                                else self.cached),
                    prompt_tokens=(self.warmup_prompt_tokens
                                   if self.warmup_prompt_tokens is not None
                                   else 1000))
                _resp.choices[0].finish_reason = "length"
                if self.warmup_model is not None:
                    _resp.model = self.warmup_model
                return _resp
            if self._barrier is not None and (self.barrier_all or call_no > 0):
                try:
                    self._barrier.wait(timeout=self.barrier_timeout)
                except threading.BrokenBarrierError:
                    self.barrier_broken = True
            if self.interrupt_for and set(ids) & self.interrupt_for:
                # RECORDED BEFORE IT IS RAISED, so a watchdog can key on the
                # event rather than on a wall-clock guess about when the node
                # got here.
                self.interrupt_raised.set()
                raise KeyboardInterrupt("simulated operator interrupt")
            if self.hold is not None:
                self.hold.wait(timeout=30.0)
            _d = self._delay_for(ids)
            if _d:
                time.sleep(_d)
            if self.fail_for and set(ids) & self.fail_for:
                raise RuntimeError(f"stub failure for {sorted(set(ids))}")
            body = self.answer.get(ids[0]) if len(ids) == 1 else None
            if body is None:
                body = _eligible_body(ids)
            if set(ids) & self.bad_json_for:
                body = "{not json at all"
            cached = self.cached
            if cached is not None and self.cached_first_only and call_no > 0:
                cached = None
            resp = _StubResponse(body, cached=cached)
            if set(ids) & self.truncate_for:
                resp.choices[0].finish_reason = "length"
            if set(ids) & self.refuse_for:
                resp.choices[0].message.content = None
                resp.choices[0].message.refusal = "I cannot help with that."
            for _i in ids:
                if _i in self.model_for:
                    resp.model = self.model_for[_i]
                    break
            return resp
        finally:
            with self._lock:
                self.in_flight -= 1
                self.events.append(("exit", call_no, self._tick()))

    def _delay_for(self, ids):
        """How long THIS call sleeps. A HOOK, and it takes its ids as an
        argument rather than writing to an attribute, because several calls run
        at once: a subclass that set ``self.delay`` per call would be sharing
        one slot between concurrent workers and the delay a call observed would
        be whichever peer wrote last. That is not a hypothetical -- it is how
        the first version of ``_ReverseStub`` below was written, and check 4f's
        non-degeneracy probe is what reported it."""
        return self.delay

    # -- readings the assertions use -------------------------------------
    def ids_by_call(self):
        return [ids_in(r) for r in self.requests]

    # THE THREE READINGS BELOW SPLIT THE RECORD RATHER THAN FILTERING IT
    # QUIETLY. `requests` stays every request the node made, warmup included,
    # so a check that means "the node issued N requests in total" can still say
    # so; these name the two populations explicitly, so a check that means "the
    # node issued one request per trial" cannot accidentally be satisfied by an
    # infrastructure call.
    def warmup_requests(self):
        return [r for r in self.requests if is_warmup(r)]

    def wave_requests(self):
        return [r for r in self.requests if not is_warmup(r)]

    def wave_ids(self):
        return [ids_in(r) for r in self.wave_requests()]

    def wave_call_nos(self):
        """The ``call_no`` of every non-warmup request, in arrival order."""
        return [n for n, r in enumerate(self.requests) if not is_warmup(r)]

    def enter_ticket(self, call_no):
        for kind, n, t in self.events:
            if kind == "enter" and n == call_no:
                return t
        return _Absent(f"no enter event for call {call_no}")

    def exit_ticket(self, call_no):
        for kind, n, t in self.events:
            if kind == "exit" and n == call_no:
                return t
        return _Absent(f"no exit event for call {call_no}")

    def completion_order(self):
        """The nct_ids in the order their RESPONSES were returned.

        NOT ``self.requests``, which is ENTRY order: with a wide pool every
        worker starts within microseconds of the others, so entry order is
        essentially submission order and says nothing about who finished first.
        Response order is what the node's merge has to be immune to, so it is
        what the non-degeneracy probe below measures."""
        done = [(t, n) for kind, n, t in self.events if kind == "exit"]
        return [ids_in(self.requests[n]) for _, n in sorted(done)]


# THE STRONGEST FORM: force the responses to COMPLETE in reverse batch order
# and require the merge to be unmoved.
#
# FORCED, NOT ENCOURAGED, AND THAT IS THE WHOLE POINT OF THIS CLASS. The first
# version of this scenario gave the last trial a 0s delay and everything else
# 0.05s and then asserted that the last trial finished early. That is a
# statement about how fast the machine is, and it FAILED the first time this
# file ran inside CI bucket A, where 61 test processes compete -- a check that
# passes alone and fails in the suite is worse than no check, because it
# reports a scheduling defect that is not there. The order is now produced by
# a barrier plus a hand-off, so it is the same on an idle laptop and a
# saturated runner.


class _OrderedStub(_Stub):
    """Completes the TRIAL calls in an EXPLICIT order.

    Every trial call first joins a barrier -- so they are provably all in
    flight together, which is what makes a reordering possible at all -- and
    then waits for its turn on a condition. Turn `i` is handed to the id at
    `order[i]`, so the completion sequence is decided here rather than by the
    scheduler.

    ``order`` NOW COVERS EVERY TRIAL, where it once covered every trial but the
    priming one. That is the schedule change stated in the harness: no trial
    call is awaited alone any more, so there is no trial the hand-off has to
    exclude. The WARMUP is excluded, and by its own shape rather than by name --
    ``_Stub.create`` answers it above the barrier, and ``_delay_for`` never
    sees a request with no ids in the hand-off.
    """

    def __init__(self, order, **kw):
        super().__init__(barrier_size=len(order), **kw)
        self._order = list(order)
        self._cond = threading.Condition()
        self._turn = 0
        self.order_timed_out = False

    def _delay_for(self, ids):
        # The warmup is not part of the hand-off: it has already completed
        # before any of these exist, and it carries no trial to rank.
        if not ids:
            return 0.0
        try:
            rank = self._order.index(ids[0])
        except ValueError:
            return 0.0
        deadline = time.monotonic() + 15.0
        with self._cond:
            while self._turn != rank:
                if not self._cond.wait(timeout=max(0.0,
                                                   deadline - time.monotonic())):
                    self.order_timed_out = True
                    return 0.0
            self._turn += 1
            self._cond.notify_all()
        return 0.0


def run_node(trials, *, per_trial=True, parallel=None, node=None, stub=None,
             patient_data=None, **stub_kwargs):
    """Drive Stage 5 once and return ``(result, stub)``.

    THE SWITCH IS SET ON ``config``, NOT ON THE NODE'S GLOBALS, and that is the
    seam the production code chose rather than a convenience here: the node
    reads ``config.matching_call_mode()`` live, precisely so the column
    ``oncotriage/storage/database_logger.py`` writes from the same function
    cannot disagree with it. Check 1e is the standing proof that setting it here
    really does reach the node.
    """
    node = node or node_llm_classifier_evaluation
    stub = stub if stub is not None else _Stub(**stub_kwargs)
    saved = (config.MATCHING_PER_TRIAL_CALLS_ENABLED,
             config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS)
    deps.set_override(deps.OPENAI_CLIENT, stub)
    try:
        config.MATCHING_PER_TRIAL_CALLS_ENABLED = per_trial
        if parallel is not None:
            config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS = parallel
        state = {
            "patient_data": patient_data or PATIENT,
            "filtered_trials": trials,
            "llm_classifier_retries": 0,
            "mesh_filter_applied": True,
            "mesh_filter_skip_reason": "applied",
            "stage_timings": {},
        }
        return drive(node, state), stub
    finally:
        (config.MATCHING_PER_TRIAL_CALLS_ENABLED,
         config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS) = saved
        deps.clear_override(deps.OPENAI_CLIENT)


def module_from(source, name):
    """exec a patched copy of evaluation.py into its own namespace.

    A PATCHED IN-MEMORY COPY, never an edit to the file: this project's stated
    preference, and what keeps this file out of the collision matrix.

    A REAL ModuleType rather than a throwaway class, because a function's
    globals ARE the dict it was exec'd into -- so the copy's own module
    ``__dict__`` is what its functions read, and a class attribute would not be.
    """
    module = types.ModuleType(name)
    module.__file__ = _EVALUATION_PATH
    exec(compile(source, f"<{name}>", "exec"), module.__dict__)
    return module


_EVAL_SRC = open(_EVALUATION_PATH, encoding="utf-8").read()
_SIX = [trial(i) for i in range(6)]


# ===========================================================================
# SECTION 1 -- THE CONFIGURATION SURFACE AND ITS ONE OWNER
# ===========================================================================

section("SECTION 1 -- the switch, the vocabulary, and who reads them")

# THIS CHECK USED TO PIN THE SWITCH **OFF** and it is inverted rather than
# deleted, because the property it holds is unchanged: the shipped default is a
# DECISION and this file is where it is written down, so a flip that nobody
# meant fails here with the arm named. It read "the switch ships OFF -- this
# pass builds the arm and does not turn it on"; the arm was built, probed
# against stubs, gated for shutdown, given a warmup with cache-or-nothing
# semantics, and then turned on. Grouped is retained behind the same switch as
# the migration's comparison arm and section 8 is what keeps it exact.
check("1a  the switch ships ON -- per-trial is the pipeline's design and the "
      "shipped arm; grouped is the retained comparison arm",
      config.MATCHING_PER_TRIAL_CALLS_ENABLED, True)
# DERIVED FROM THE CONSTANT, NOT RESTATED. 1a is the ONE place the shipped
# decision is written down; a second literal here would fail twice for one
# change and the second failure would name the owner rather than the decision.
# What 1a-ii holds is the pair of facts 1a cannot: the owner AGREES with the
# constant, and NO PIN is installed -- a pin left behind by an earlier import
# (tests/test_agent_stage5_input_packing.py and six siblings install one) would
# make every default-arm statement in this file describe something else, and it
# would do it silently, because a pin is designed to be indistinguishable from
# the default to every consumer.
check("1a-ii ...and the owner agrees with it, with no pin installed in this "
      "process",
      (config.matching_call_mode(), config.matching_call_mode_pin()),
      (config.MATCHING_CALL_MODE_PER_TRIAL
       if config.MATCHING_PER_TRIAL_CALLS_ENABLED
       else config.MATCHING_CALL_MODE_GROUPED, None))
check("1b  the in-node parallel bound is a usable integer >= 1",
      (isinstance(config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS, int),
       not isinstance(config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS, bool),
       config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS >= 1),
      (True, True, True))
check("1c  the vocabulary is closed and its two members are the two named "
      "constants", (config.MATCHING_CALL_MODES,
                    len(set(config.MATCHING_CALL_MODES))),
      (("grouped", "per_trial"), 2))

# `matching_call_mode()` is the ONE owner. Both directions, because a function
# that returned a constant would satisfy one of them.
_saved_mode = config.MATCHING_PER_TRIAL_CALLS_ENABLED
try:
    config.MATCHING_PER_TRIAL_CALLS_ENABLED = True
    _mode_on = config.matching_call_mode()
    config.MATCHING_PER_TRIAL_CALLS_ENABLED = False
    _mode_off = config.matching_call_mode()
finally:
    config.MATCHING_PER_TRIAL_CALLS_ENABLED = _saved_mode
check("1d  matching_call_mode() reads the flag LIVE off the module, in both "
      "directions (a bound name would answer the same both times)",
      (_mode_on, _mode_off),
      (config.MATCHING_CALL_MODE_PER_TRIAL, config.MATCHING_CALL_MODE_GROUPED))
# DERIVED FROM THE SAVED VALUE, NOT FROM A LITERAL. A restore check written as
# `== False` is a second copy of the shipped default, and it goes red on a flip
# while naming a restore that worked perfectly.
check("1d  ...and it was restored", config.MATCHING_PER_TRIAL_CALLS_ENABLED,
      _saved_mode)

# THE SEAM ITSELF. Without this, every ON-arm assertion below could be
# exercising the shipped default and reporting success.
#
# SEVEN, NOT SIX: one cache warmup and then six trial calls. Counting total
# requests here rather than wave requests is deliberate -- this check exists to
# say the flag reaches the node at all, and the total is the coarsest reading
# that can say it.
check("1e  setting the flag on `config` genuinely reaches the node: ON over "
      "six trials issues a warmup plus six trial requests, OFF issues one",
      (len(run_node(_SIX, per_trial=True)[1].requests),
       len(run_node(_SIX, per_trial=False)[1].requests)), (7, 1))

# The three warmup constants, on the same footing as 1a/1b: a value the mode
# cannot work with is a configuration defect, and config refuses it at import.
check("1f  the warmup output ceiling is a usable integer >= 1",
      (isinstance(config.MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS, int),
       not isinstance(config.MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS, bool),
       config.MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS >= 1),
      (True, True, True))
check("1f  ...and it is 1, the smallest answer a provider can be asked for",
      config.MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS, 1)
check("1f  ...and the warmup user message is a non-empty string",
      (isinstance(config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE, str),
       bool(config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE)), (True, True))
check("1f  ...and the cache-routing hint is a bool",
      isinstance(config.MATCHING_PER_TRIAL_PROMPT_CACHE_KEY_ENABLED, bool),
      True)

# The routing key is a pure function of the prefix digest, both directions.
_saved_key_flag = config.MATCHING_PER_TRIAL_PROMPT_CACHE_KEY_ENABLED
try:
    config.MATCHING_PER_TRIAL_PROMPT_CACHE_KEY_ENABLED = True
    _key_on = _evaluation.per_trial_prompt_cache_key("aabbcc")
    _key_on_2 = _evaluation.per_trial_prompt_cache_key("aabbcc")
    _key_other = _evaluation.per_trial_prompt_cache_key("ddeeff")
    config.MATCHING_PER_TRIAL_PROMPT_CACHE_KEY_ENABLED = False
    _key_off = _evaluation.per_trial_prompt_cache_key("aabbcc")
finally:
    config.MATCHING_PER_TRIAL_PROMPT_CACHE_KEY_ENABLED = _saved_key_flag
check("1g  the routing key is stable for one prefix and different for another "
      "-- which is the whole property, since two requests are routed together "
      "exactly when they have a prefix to share",
      (_key_on == _key_on_2, _key_on == _key_other,
       _key_on is None), (True, False, False))
check("1g  ...it is namespaced rather than a bare digest, so an unrelated "
      "workload on the same account cannot ask to be routed with us",
      _key_on.startswith("oncotriage-"), True)
check("1g  ...and the flag switches it off to None, which is what makes the "
      "kwarg expansion empty", _key_off, None)
check("1g  ...and the flag was restored",
      config.MATCHING_PER_TRIAL_PROMPT_CACHE_KEY_ENABLED, _saved_key_flag)

# The rejection vocabulary is closed and both members are the named constants.
check("1h  the warmup rejection vocabulary is closed and its two members are "
      "the two named constants",
      (_evaluation.WARMUP_REJECTIONS,
       len(set(_evaluation.WARMUP_REJECTIONS))),
      ((_evaluation.WARMUP_REJECTED_MINIMAL_OUTPUT,
        _evaluation.WARMUP_REJECTED_CACHE_KEY), 2))

# THE PROVIDER GUARD FIRES BEFORE A CENT IS SPENT, which is what makes a
# misconfiguration one named error rather than MAX_LLM_CLASSIFIER_RETRIES
# identical failed patients. Driven on `config` because that is the seam the
# node reads, and restored.
_saved_provider = config.MATCHING_PROVIDER
try:
    config.MATCHING_PROVIDER = config.MATCHING_PROVIDER_BEDROCK
    _prov_raised = drive(assert_per_trial_provider_supported)
    _prov_node, _prov_stub = run_node(_SIX, per_trial=True)
    config.MATCHING_PROVIDER = config.MATCHING_PROVIDER_OPENAI
    _prov_ok = drive(assert_per_trial_provider_supported)
finally:
    config.MATCHING_PROVIDER = _saved_provider
check("1i  per-trial mode on a provider whose warmup is not built is REFUSED "
      "by name, and NOTHING is sent -- not the warmup and not a trial call",
      (PerTrialProviderUnsupportedError.__name__ in repr(_prov_raised),
       PerTrialProviderUnsupportedError.__name__ in repr(_prov_node),
       len(_prov_stub.requests)), (True, True, 0))
check("1i  ...and the refusal names BOTH constants, because either is a "
      "legitimate fix and only the operator knows which they meant",
      ("MATCHING_PER_TRIAL_CALLS_ENABLED" in repr(_prov_node),
       "MATCHING_PROVIDER" in repr(_prov_node)), (True, True))
check("1i  ...it is a RuntimeError subclass, deliberately not a ValueError, "
      "so a stray `except ValueError` cannot eat it",
      (issubclass(PerTrialProviderUnsupportedError, RuntimeError),
       issubclass(PerTrialProviderUnsupportedError, ValueError)),
      (True, False))
check("1i  ...non-degeneracy: on the shipped provider it does not fire, so "
      "the rows above are a measurement rather than a guard that always raises",
      _prov_ok, None)
check("1i  ...and the provider was restored",
      config.MATCHING_PROVIDER, _saved_provider)

# NO THIRD RETRY BUDGET WAS INVENTED, ASSERTED STRUCTURALLY. The warmup's
# retry coverage is the two budgets this project already reconciles:
# OPENAI_SDK_MAX_RETRIES inside the SDK, with the SDK's own backoff honouring
# Retry-After, and MAX_LLM_CLASSIFIER_RETRIES above the node, which re-enters
# it through route_after_llm_classifier when the API-error result is returned.
# A loop around the warmup would be a fourth number in a file that already
# works out the worst-case wall time from three, and its cost would not appear
# in that arithmetic.
# Parsed HERE rather than reusing `_eval_tree`, which is built further down
# this section: a check that depended on a name defined below it would work
# only for as long as nobody moved either line.
_eval_tree_1k = ast.parse(_EVAL_SRC)
_warmup_calls = [n for n in ast.walk(_eval_tree_1k)
                 if isinstance(n, ast.Call)
                 and ast.unparse(n.func).endswith("call_matching_model_warmup")]
_loops = [n for n in ast.walk(_eval_tree_1k)
          if isinstance(n, (ast.For, ast.While, ast.AsyncFor))]
_in_a_loop = any(c in set(ast.walk(loop))
                 for loop in _loops for c in _warmup_calls)
check("1k  the warmup is issued from exactly ONE call site and that site is "
      "not inside any loop -- the retry budget is the existing one, not a "
      "third one invented here",
      (len(_warmup_calls), _in_a_loop), (1, False))
check("1k  ...non-degeneracy: the walk really does find loops in this module, "
      "so `not in a loop` is a finding rather than a walk that matched "
      "nothing", len(_loops) > 5, True)

# THE UNREACHABLE GUARD IS STILL DRIVEN. config refuses an empty warmup message
# at import, so this fires only when something rebound the constant WITHIN a
# process -- which is exactly what a probe or a REPL does, and is why it raises
# rather than substituting a default nobody can reproduce.
_saved_msg = config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE
try:
    config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE = ""
    _msg_raised = drive(_evaluation.call_matching_model_warmup, "SYS")
finally:
    config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE = _saved_msg
check("1k  an empty warmup message is REFUSED rather than defaulted, so a "
      "warmup can never write a cache under a request nobody can reproduce",
      ("_WarmupUserMessageError" in repr(_msg_raised),
       "MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE" in repr(_msg_raised)),
      (True, True))
check("1k  ...and the message was restored",
      config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE, _saved_msg)

# ONE OWNER, TWO CONSUMERS -- asserted BY AST against both files, because the
# whole value of the function is that neither side re-reads the constant.
_eval_tree = ast.parse(_EVAL_SRC)
_db_src = open(_DB_LOGGER_PATH, encoding="utf-8").read()


def _calls_to(tree, attr):
    """Every `<something>.attr(...)` call site in `tree`."""
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == attr]


def _names_loaded(tree, name):
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Name) and n.id == name
            and isinstance(n.ctx, ast.Load)]


check("1f  the NODE decides its partition from config.matching_call_mode() "
      "and not from a bound copy of the flag",
      (len(_calls_to(_eval_tree, "matching_call_mode")),
       len(_names_loaded(_eval_tree, "MATCHING_PER_TRIAL_CALLS_ENABLED"))),
      (1, 0))
check("1g  ...and the WRITER records the column from the same function",
      len(_calls_to(ast.parse(_db_src), "matching_call_mode")), 1)
check("1h  ...and neither file re-reads the flag as an attribute either "
      "(config.MATCHING_PER_TRIAL_CALLS_ENABLED anywhere would be a second "
      "interpretation of one constant)",
      (_EVAL_SRC.count("config.MATCHING_PER_TRIAL_CALLS_ENABLED"),
       _db_src.count("config.MATCHING_PER_TRIAL_CALLS_ENABLED")), (0, 0))
check("1i  ...non-degeneracy: the AST walk finds attribute calls at all, so "
      "1f/1g are measurements rather than a walk that matched nothing",
      len(_calls_to(_eval_tree, "matching_wire_model")) >= 1, True)

# The bound is validated, and it is validated ONLY in the mode that reads it.
check("1j  a bound below 1 refuses BY NAME, before any request",
      type(drive(lambda: run_node(_SIX, per_trial=True, parallel=0)[0])
           .__class__).__name__ if False else
      isinstance(run_node(_SIX, per_trial=True, parallel=0)[0], _Absent),
      True)
_bad = run_node(_SIX, per_trial=True, parallel=0)
check("1j  ...as PerTrialParallelismError, and nothing was sent",
      (PerTrialParallelismError.__name__ in repr(_bad[0]),
       len(_bad[1].requests)), (True, 0))
check("1k  ...and grouped mode does NOT read it, so a bad bound cannot fail a "
      "campaign that never uses it",
      len(run_node(_SIX, per_trial=False, parallel=0)[1].requests), 1)


# ── THE FIXTURE HARNESS REFUSES AN UNPINNED PER-TRIAL PROCESS ───────────────
#
# ``RecordingSink.add`` stamps ``call_index = len(bucket)`` under its lock, so
# a Stage 5 recording's index is its ARRIVAL ordinal -- deterministic while the
# stage is sequential and decided by the scheduler the moment it is not. The
# deterministic prefix then projects ``request_sha256_by_call`` and
# ``finish_reasons`` as LISTS in that order, so a capture taken under this mode
# would write a fixture whose "deterministic" prefix is not, and a replay would
# report a permutation as a difference. Nothing would raise.
#
# THE REFUSAL IS NO LONGER THE HARNESS'S WHOLE ANSWER, and this block is scoped
# accordingly. ``fixture_capture.py`` and ``fixture_replay.py`` now PIN the mode
# to grouped for their own process through ``config.pin_matching_call_mode`` --
# a flat refusal would have taken the free twelve-fixture replay gate out of
# service the day the default flips, which is the day it is worth the most.
# What is checked HERE is what is left: the guard still exists, still reads the
# ONE owner ``config.matching_call_mode()``, and still bites for every path
# that did not come through that pin. The pin itself, the loud line and all
# four (pin x constant) combinations are
# ``tests/test_fixture_call_mode_pin.py``'s subject.
#
# NO HOOKS ARE INSTALLED AND NO CLIENT IS TOUCHED: the guard is a pure function
# of the config module and is called BEFORE any seam is reached, which is what
# lets this file exercise it without a network, a key or a fixture. No pin is
# installed either, so the arm below is the unpinned one.
_saved1b = config.MATCHING_PER_TRIAL_CALLS_ENABLED
try:
    config.MATCHING_PER_TRIAL_CALLS_ENABLED = True
    _refused = drive(_capture.assert_call_mode_is_hookable, "probe")
    config.MATCHING_PER_TRIAL_CALLS_ENABLED = False
    _allowed = drive(_capture.assert_call_mode_is_hookable, "probe")
finally:
    config.MATCHING_PER_TRIAL_CALLS_ENABLED = _saved1b

check("1l  the fixture harness REFUSES to hook per-trial mode",
      _capture.UnsupportedCallModeError.__name__ in repr(_refused), True)
check("1l  ...and does NOT refuse grouped mode, so 1l is a measurement rather "
      "than a guard that raises unconditionally", _allowed, None)
check("1l  ...and the refusal names BOTH inputs to the owner it read, so a "
      "reader can tell an inherited default from a deliberate pin",
      ("MATCHING_PER_TRIAL_CALLS_ENABLED" in str(_refused),
       "pin=" in str(_refused)), (True, True))
check("1l  ...and it is a RuntimeError subclass, deliberately not a "
      "ValueError, so a stray `except ValueError` cannot eat it",
      (issubclass(_capture.UnsupportedCallModeError, RuntimeError),
       issubclass(_capture.UnsupportedCallModeError, ValueError)),
      (True, False))

# BOTH HARNESSES CALL IT, asserted BY AST rather than by installing hooks --
# which would open a real OpenAI client and a real Qdrant client.
_cap_tree = ast.parse(open(
    os.path.abspath(_capture.__file__), encoding="utf-8").read())
# READ BY PATH, NEVER IMPORTED. ``oncotriage/fixtures/replay.py`` sets
# ONCOTRIAGE_DEFER_LOCAL_MODELS at module scope -- the one deliberate
# import-time side effect anywhere in the package -- and importing it here to
# read six lines of its source would change this process's environment for
# every check after it. The path is derived from ``capture``'s own __file__ so
# a future move cannot silently point this at a same-named copy.
_REPLAY_PATH = os.path.join(os.path.dirname(os.path.abspath(_capture.__file__)),
                            "replay.py")
_rep_tree = ast.parse(open(_REPLAY_PATH, encoding="utf-8").read())


def _guarded_functions(tree, attr):
    return sorted(fn.name for fn in ast.walk(tree)
                  if isinstance(fn, ast.FunctionDef)
                  and any(isinstance(n, ast.Call)
                          and ((isinstance(n.func, ast.Name)
                                and n.func.id == attr)
                               or (isinstance(n.func, ast.Attribute)
                                   and n.func.attr == attr))
                          for n in ast.walk(fn)))


check("1m  install_recording_hooks calls the guard",
      _guarded_functions(_cap_tree, "assert_call_mode_is_hookable"),
      ["install_recording_hooks"])
check("1m  ...and so does install_replay_hooks",
      _guarded_functions(_rep_tree, "assert_call_mode_is_hookable"),
      ["install_replay_hooks"])
check("1m  ...non-degeneracy: the same walk finds the PROVIDER guard in both, "
      "so an empty result would be a finding rather than a walk that matched "
      "nothing",
      (_guarded_functions(_cap_tree, "assert_provider_is_hookable"),
       _guarded_functions(_rep_tree, "assert_provider_is_hookable")),
      (["install_recording_hooks"], ["install_replay_hooks"]))



# ===========================================================================
# SECTION 1c -- THE PARALLELISM BOUND IS VALIDATED AT IMPORT, NOT ONLY AT THE NODE
# ===========================================================================
#
# THE DEFECT. The node tests `_parallel_bound < 1` and raises
# `PerTrialParallelismError`, which is right and is not enough: a bare `<`
# comparison is not a type check, and this number becomes
# `ThreadPoolExecutor(max_workers=...)`. Every non-int the constant can
# plausibly be mistyped as gets PAST that test, and each fails differently and
# late:
#
#   * `True` -- `True < 1` is False, so the guard passes, and `max_workers=True`
#     is `max_workers=1`. A campaign silently runs per-trial mode SEQUENTIALLY
#     while every report says it ran at the configured concurrency. NOTHING
#     RAISES, EVER. This is the one that matters.
#   * `4.5` -- passes the guard, then `ThreadPoolExecutor` raises inside the
#     node, per patient, AFTER the warmup has been issued and billed.
#   * `"4"` -- `"4" < 1` raises `TypeError`, which is not
#     `PerTrialParallelismError`: it leaves the node as an unrelated failure
#     with no mention of the constant that caused it.
#
# `oncotriage/config.py` now carries the full isinstance / not-bool / >= 1
# guard its warmup sibling already had, AT IMPORT AND UNCONDITIONALLY. The
# node's own check STAYS -- the two ask different questions and both are kept:
# this one asks "is this constant a usable integer at all", which is true or
# false whether the mode is on or not; the node's asks "is this bound usable FOR
# THE MODE ABOUT TO RUN" and names the mode, which is the operator's other way
# out.
#
# IT IS DRIVEN IN A SUBPROCESS AGAINST A COPY, never by editing config.py in
# place -- which is what keeps this file out of the collision matrix. The copy
# is a package tree in a tempfile.mkdtemp() this section removes; the shipped
# config.py is read and never written, and section 10 hashes it.

section("SECTION 1c -- MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS is checked at import")

_1c_TMP = tempfile.mkdtemp(prefix="per-trial-bound-")
_1c_PKG_SRC = os.path.dirname(os.path.dirname(_EVALUATION_PATH))
_1c_PKG = os.path.join(_1c_TMP, "oncotriage")
shutil.copytree(_1c_PKG_SRC, _1c_PKG,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
_1c_CONFIG = os.path.join(_1c_PKG, "config.py")
_1c_CONFIG_TEXT = open(_1c_CONFIG, encoding="utf-8").read()
_1c_ANCHOR = "MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS = 4"


def _import_config_with(value_literal):
    """Import a COPY of config.py with the bound set to `value_literal`.

    Returns ``("ok", <repr of the value config ended up with>)`` or
    ``("raised", "<ExceptionType>: <message>")``.

    A SUBPROCESS, because the guard runs AT IMPORT and this process has already
    imported config -- re-importing it here would either be a no-op from
    sys.modules or would leave a second, differently-configured copy behind for
    every check after it.
    """
    if _1c_CONFIG_TEXT.count(_1c_ANCHOR) != 1:
        return ("plant-failed",
                f"the anchor appears {_1c_CONFIG_TEXT.count(_1c_ANCHOR)} times")
    planted = _1c_CONFIG_TEXT.replace(
        _1c_ANCHOR,
        f"MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS = {value_literal}", 1)
    open(_1c_CONFIG, "w", encoding="utf-8").write(planted)
    env = dict(os.environ)
    env["PYTHONPATH"] = _1c_TMP
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["ONCOTRIAGE_DEFER_LOCAL_MODELS"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c",
         "import os, sys\n"
         "from oncotriage import config\n"
         "assert os.path.realpath(config.__file__).startswith("
         "  os.path.realpath(sys.argv[1])), config.__file__\n"
         "print('OK', repr(config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS))",
         _1c_TMP],
        capture_output=True, text=True, env=env, cwd=_1c_TMP)
    if proc.returncode == 0:
        return ("ok", proc.stdout.strip().split(" ", 1)[-1])
    tail = [ln for ln in proc.stderr.strip().splitlines() if ln.strip()]
    return ("raised", tail[-1] if tail else "<no message>")


# THE COPY IS WHAT IMPORTS -- asserted inside the subprocess above, and proved
# here by importing it UNCHANGED first. Without this, every "raised" result
# below could be a broken copy rather than the guard.
_1c_baseline = _import_config_with("4")
check("1c(a) the copied config imports unchanged and reports the shipped value "
      "(non-degeneracy: the COPY is what runs)", _1c_baseline, ("ok", "4"))

for _label, _literal, _want in (
        ("1c(b) 0 is refused", "0", "0"),
        ("1c(c) a negative is refused", "-1", "-1"),
        ("1c(d) True is refused -- it would silently mean max_workers=1",
         "True", "True"),
        ("1c(e) a float is refused -- ThreadPoolExecutor would raise per "
         "patient, after the warmup was billed", "4.5", "4.5"),
        ("1c(f) a string is refused -- the node's `< 1` would raise TypeError, "
         "naming nothing", "'4'", "'4'"),
):
    _kind, _detail = _import_config_with(_literal)
    check(_label, _kind, "raised")
    check(f"       ...naming the constant and the offending value",
          ("MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS" in _detail,
           _want in _detail), (True, True))
    check("       ...as a RuntimeError, not an assert (python -O deletes those)",
          _detail.split(":")[0].strip().endswith("RuntimeError"), True)

# 1 IS LEGAL AND MEANS SEQUENTIAL. Without this the guard could be `> 1` and
# every check above would still pass, while the documented way to turn the
# scheduling off without turning the mode off would refuse at import.
check("1c(g) 1 is ACCEPTED -- it is the documented sequential setting",
      _import_config_with("1"), ("ok", "1"))

# THE SHIPPED FILE IS RESTORED IN THE COPY AND THE TEMP TREE IS REMOVED.
open(_1c_CONFIG, "w", encoding="utf-8").write(_1c_CONFIG_TEXT)
shutil.rmtree(_1c_TMP, ignore_errors=True)
check("1c(h) the temp package tree is gone", os.path.exists(_1c_TMP), False)

# AND THE NODE'S OWN CHECK IS STILL THERE. The import guard does not replace it:
# a caller that sets the attribute AFTER import -- which `run_node` in this very
# file does -- bypasses the import guard entirely, so the node's check is the
# only thing standing between that caller and a bad bound.
_1c_node_src = ast.parse(_EVAL_SRC)
_1c_raises = [n for n in ast.walk(_1c_node_src)
              if isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call)
              and isinstance(n.exc.func, ast.Name)
              and n.exc.func.id == "PerTrialParallelismError"]
check("1c(i) the node still raises PerTrialParallelismError for a bad bound",
      len(_1c_raises), 1)
# `run_node` returns `(result, stub)` and its own `drive()` has already turned
# the raise into an `_Absent`, so the result is UNPACKED rather than tested as
# one -- a check written against the tuple would be False for every input and
# would have reported this working guard as broken.
_1c_after, _ = run_node([trial(0)], per_trial=True, parallel=0)
check("1c(j) ...and it FIRES: setting the bound to 0 AFTER import is refused "
      "by the node, naming the mode",
      isinstance(_1c_after, _Absent)
      and "PerTrialParallelismError" in str(_1c_after), True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 2 -- THE PARTITION
# ===========================================================================

section("SECTION 2 -- one request per trial, and the packer is bypassed")

_R2, _S2 = run_node(_SIX, per_trial=True)
_ids2 = _S2.wave_ids()

check("2a  six trials produce six TRIAL requests, plus exactly one warmup",
      (len(_ids2), len(_S2.warmup_requests())), (6, 1))
check("2b  ...each trial request carrying exactly one trial",
      sorted({len(i) for i in _ids2}), [1])
check("2c  ...the union is the whole batch, once each",
      sorted(i for call in _ids2 for i in call),
      sorted(t["trial"]["nct_id"] for t in _SIX))
# ARRIVAL ORDER IS NOT TRIAL ORDER AND MUST NOT BE ASSERTED TO BE. All six of
# these trial requests are issued concurrently, so `stub.requests` is ordered
# by whichever worker got the lock first -- a fact about the pool. The one
# thing that IS deterministic about arrival is that the WARMUP came first,
# which is section 3's subject; section 4 owns everything the node PUBLISHES,
# which is where trial order is a promise.
check("2d  the FIRST request to arrive is the warmup, and it carries no trial "
      "at all -- which is what makes it infrastructure rather than a trial "
      "call doubling as one",
      (is_warmup(_S2.requests[0]), _S2.ids_by_call()[0]), (True, []))
check("2d  ...and the warmup's user message is the configured one, byte for "
      "byte, rather than anything this node invented",
      _S2.requests[0]["messages"][1]["content"],
      config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE)
check("2d  ...its output ceiling is the configured minimum, not the batch "
      "ceiling -- the whole reason a warmup is affordable",
      (_S2.requests[0].get("max_completion_tokens"),
       config.MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS
       != config.MATCHING_MAX_TOKENS),
      (config.MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS, True))
check("2d  ...and it asks for no response_format, which is the one asymmetry "
      "with a trial call and is argued at call_matching_model_warmup",
      ("response_format" in _S2.requests[0],
       all("response_format" in r for r in _S2.wave_requests())),
      (False, True))
check("2d  ...while every request of the patient, warmup included, carries "
      "the SAME routing key -- a warmup routed apart from its wave would warm "
      "a machine the wave never reaches",
      (len({r.get("prompt_cache_key") for r in _S2.requests}),
       _S2.requests[0].get("prompt_cache_key") is not None), (1, True))
check("2e  every trial got a verdict; nothing was lost or duplicated",
      sorted(e.get("nct_id") for e in at(_R2, "evaluations")),
      sorted(t["trial"]["nct_id"] for t in _SIX))

_packing2 = at(_R2, "llm_classifier_packing")
check("2f  the packer is recorded as NOT having run",
      at(_packing2, "enabled"), False)
check("2g  ...and as having been BYPASSED, by this mode -- which is what "
      "separates it from a run where the packing switch alone was off",
      at(_packing2, "bypassed_by"), config.MATCHING_CALL_MODE_PER_TRIAL)
check("2h  ...with no budget and no cap selected, because none was applied",
      (at(_packing2, "budget_tokens"), at(_packing2, "max_chunks"),
       at(_packing2, "cap_relaxed_budget"), at(_packing2, "over_budget_chunk")),
      (None, None, False, False))
# ── packed_chunks IS NULL ON A BYPASS, AND 0 WOULD HAVE BEEN A LIE ─────────
#
# The column's documented tri-state (oncotriage/storage/database_logger.py)
# reserves 0 for "the packer RAN and produced no chunk" -- an empty candidate
# set. This patient sent SIX requests. Storing 0 made it read identically to a
# patient with no candidates at all, in a column whose whole job is to say what
# the packer did, and the NULL branch of the same tri-state ("the packer's
# record does not describe this run") is a true statement about a bypass.
check("2i  ...and packed_chunks is NULL, not 0 and not 6: the packer did not "
      "run, so it has no chunk count -- and 0 is reserved for a packer that "
      "RAN and produced none, which is an empty candidate set",
      at(_R2, "llm_classifier_packed_chunks"), None)
check("2i  ...non-degeneracy: this NULL is about a patient that really did "
      "send six requests, which is what makes 0 the wrong value rather than "
      "an equivalent one",
      (len(_S2.wave_requests()), at(_R2, "llm_classifier_calls")), (6, 7))
check("2i  ...and the REASON survives beside it: the scalar is NULL and the "
      "blob still carries the bypass record, so the pair says 'bypassed' and "
      "not 'unmeasured' -- packing NOT NULL with packed_chunks NULL IS the "
      "bypass, which both-NULL (a failure return) is not",
      (at(_R2, "llm_classifier_packing") is None,
       at(at(_R2, "llm_classifier_packing"), "bypassed_by"),
       at(at(_R2, "llm_classifier_packing"), "enabled")),
      (False, config.MATCHING_CALL_MODE_PER_TRIAL, False))

_R2off, _ = run_node(_SIX, per_trial=False)
check("2j  the OTHER arm says the opposite, so 2f/2g are measurements: the "
      "packer RAN and nothing bypassed it",
      ((at(_R2off, "llm_classifier_packing") or {}).get("enabled"),
       "bypassed_by" in (at(_R2off, "llm_classifier_packing") or {})),
      (True, False))
# `>=` IS NOT WRITTEN BARE HERE. The value under test is now legitimately None
# on one branch, and `None >= 1` raises inside check()'s argument list -- an
# abort in place of the failure this very check owes. Measured: an over-broad
# revert (packed_chunks always None) aborted this file before the guard.
_off_chunks = at(_R2off, "llm_classifier_packed_chunks")
check("2j  ...and its packed_chunks is an INTEGER, not the bypass NULL -- so "
      "2i is a measurement of the branch rather than of the column being "
      "unwritten everywhere. The default packing switch is ON, so this is the "
      "shipped OFF-arm reading and it did not move",
      (isinstance(_off_chunks, int),
       isinstance(_off_chunks, int) and _off_chunks >= 1), (True, True))

# THE 0 CASE IS STILL A 0, driven rather than argued. The packer is handed an
# empty candidate set directly -- the node routes such a patient to
# node_no_candidates, so this is the packer's own reading of the population the
# column reserves 0 for -- and it must be distinguishable from the bypass above.
_pk_empty_chunks, _pk_empty = _evaluation.pack_trials_by_input_tokens(
    [], 100, config.MATCHING_INPUT_TOKEN_BUDGET,
    config.MATCHING_MAX_INPUT_PACKED_CHUNKS, blocks=[])
check("2i  ...while the packer's own empty-candidate-set reading is still 0 "
      "and NOT the bypass NULL: it RAN (enabled), it named no bypass, and it "
      "produced no chunk. That is the reading the tri-state reserves 0 for",
      (_pk_empty["enabled"], "bypassed_by" in _pk_empty,
       len(_pk_empty["chunks"]), _pk_empty_chunks), (True, False, 0, []))

# The request bytes are the ones a single-trial render would have produced.
# This is what says the block slice used for dispatch is the send text and not
# a second, subtly different render.
_blocks = _evaluation._render_trial_blocks(_SIX, log_events=False)
_expected_user = {t["trial"]["nct_id"]: "\nCLINICAL TRIALS:\n" + b + "\n"
                  for t, b in zip(_SIX, _blocks)}
check("2k  each per-trial user message is byte-identical to the message a "
      "one-trial render would have built (the slice identity the dispatch "
      "depends on, measured through the node). Keyed by nct_id, because the "
      "requests arrive in pool order and this is a claim about CONTENT",
      {ids_in(r)[0]: r["messages"][1]["content"]
       for r in _S2.wave_requests()},
      _expected_user)
check("2k  ...non-degeneracy: those messages are real, distinct and non-empty",
      (len(_expected_user), len(set(_expected_user.values())),
       min(len(v) for v in _expected_user.values()) > 100), (6, 6, True))
check("2l  ...and every request carried the SAME system message, which is the "
      "shared prefix the whole mode is priced on. THE WARMUP IS IN THIS SET: "
      "a warmup carrying a different prefix would warm a cache the wave cannot "
      "use, which is the one way this design fails silently",
      (len({r["messages"][0]["content"] for r in _S2.requests}),
       len(_S2.requests)), (1, 7))


# ===========================================================================
# SECTION 2b -- THE DISPATCH KEY IS UNIQUE, AND THAT IS WHAT CLOSES THE
#               SPLITTER'S LIVE-CALL PATH
# ===========================================================================
#
# `_prompts` and `_prefetched` are keyed by `_chunk_key` -- the chunk's nct_ids.
# TWO CHUNKS UNDER ONE KEY ARE THREE FAULTS AT ONCE AND NONE OF THEM RAISES:
# the second `_prompts` write wins, so both requests carry the second trial's
# rendered block and the first trial's criteria are never sent; the second
# `_prefetched` write wins, so the send loop's second pop finds nothing and
# `_obtain` issues a live request for a response that was already paid for; and
# the overwritten response is folded by nobody, because `_account_unconsumed`
# folds what is LEFT in `_prefetched` and an overwritten value was never left.
#
# The per-INDEX guard cannot see it. It asks whether chunk i holds trials[i],
# which is TRUE for both members of a repeat -- the repeat is in `trials`.
#
# UNREACHABLE TODAY IS NOT IMPOSSIBLE, and that is the whole argument for the
# guard: Stage 2 de-duplicates by nct_id, which is a property of a stage three
# modules away that nothing here can hold.
#
# AND THE PAYOFF IS THE SECOND HALF OF THIS SECTION. With keys unique, every
# dispatched chunk is filed and popped exactly once, and every per-trial chunk
# is a SINGLETON -- which the reactive splitter refuses to halve. So `_obtain`'s
# live-call path, which is a real and deliberate path in grouped mode, cannot be
# reached in per-trial mode at all. That is asserted here, twice: by driving
# every response to `finish_reason == "length"` and counting requests, and
# structurally, so an edit that moved the floor below the split fails even if no
# scenario happened to cover it.

section("SECTION 2b -- duplicate keys are refused, and the splitter cannot fire")


def _marked_trial(nct_id, marker):
    """A trial whose rendered block is identifiable by its criteria text.

    THE TITLE IS NOT RENDERED -- `_render_trial_blocks` emits the nct_id, the
    phase and the two criteria bodies and nothing else, measured rather than
    assumed -- so a marker in the criteria is the only way to say WHICH of two
    trials sharing an nct_id reached the provider.
    """
    return {"trial": {"nct_id": nct_id, "title": f"marked {marker}",
                      "phase": "PHASE2",
                      "eligibility": {
                          "inclusion_criteria": f"Inclusion Criteria:\n- {marker}",
                          "exclusion_criteria": f"Exclusion Criteria:\n- {marker}"}}}


_DUP_ID = "NCT09999001"
_DUP_A = "AAAAAAAAAAAA"
_DUP_B = "BBBBBBBBBBBB"
# TWO ENTRIES SHARING ONE nct_id, with different criteria, plus one ordinary
# trial so the batch is not degenerately all-duplicates.
_DUP = [_marked_trial(_DUP_ID, _DUP_A), _marked_trial(_DUP_ID, _DUP_B),
        trial(7)]

_R2d, _S2d = run_node(_DUP, per_trial=True, parallel=4)

check("2b(a) a batch carrying one nct_id twice is REFUSED, and the refusal is "
      "PackingBlockMismatchError -- the exception this file already raises for "
      "a dispatch that has stopped being positionally parallel, one mechanism "
      "over",
      (isinstance(_R2d, _Absent),
       PackingBlockMismatchError.__name__ in repr(_R2d)), (True, True))
check("2b(b) ...BEFORE ANY REQUEST IS ISSUED, the warmup included: the guard "
      "sits above the dispatch, so a repeat costs nothing at all rather than "
      "one infrastructure call plus an extra billed trial call",
      (len(_S2d.requests), len(_S2d.warmup_requests()),
       len(_S2d.wave_requests())), (0, 0, 0))
check("2b(c) ...and the message NAMES the repeated id, so the fix is not a "
      "hunt through the batch",
      _DUP_ID in repr(_R2d), True)
check("2b(d) ...non-degeneracy: the SAME three trials with distinct ids run "
      "clean, so the refusal is about the repeat and not about the shape of "
      "these trial objects",
      (lambda r: (isinstance(r[0], dict), len(at(r[0], "evaluations")),
                  len(r[1].wave_requests())))(
          run_node([_marked_trial("NCT09999001", _DUP_A),
                    _marked_trial("NCT09999002", _DUP_B), trial(7)],
                   per_trial=True, parallel=4)),
      (True, 3, 3))

# ── THE SPLITTER'S LIVE-CALL PATH IS UNREACHABLE HERE ─────────────────────
#
# Every trial call answers `finish_reason == "length"`. In GROUPED mode that is
# the reactive splitter's trigger: halve the chunk and send both halves, which
# is where `_obtain`'s live path exists for. In per-trial mode every chunk holds
# one trial, so the floor above the split fires instead and the trial is
# recorded rather than re-sent. Nothing is halved, nothing new is queued, and
# every request the node makes is one it already prefetched.
_TRUNC4 = [trial(i) for i in range(4)]
_R2t, _S2t = run_node(_TRUNC4, per_trial=True, parallel=4,
                      stub=_Stub(truncate_for=[t["trial"]["nct_id"]
                                               for t in _TRUNC4]))

check("2b(e) every response truncating produces NO extra request: four trials, "
      "four wave calls and one warmup -- the splitter cannot halve a singleton, "
      "so no chunk `_obtain` has no response for can ever reach it",
      (len(_S2t.wave_requests()), len(_S2t.warmup_requests()),
       at(_R2t, "llm_classifier_truncation_splits")), (4, 1, 0))
check("2b(f) ...and every trial is recorded at the truncation FLOOR rather "
      "than dropped or retried, which is what the singleton branch does "
      "instead of splitting",
      sorted({e.get("not_evaluable_reason")
              for e in at(_R2t, "evaluations")}),
      [NOT_EVALUABLE_TRUNCATION_FLOOR])
check("2b(f) ...non-degeneracy: all four trials came back, so the reading "
      "above is over a full batch rather than an empty set",
      len(at(_R2t, "evaluations")), 4)

# STRUCTURALLY, so an edit that moved the floor below the split is caught even
# by a run in which nothing truncates.
_node_fns = [n for n in ast.walk(_eval_tree) if isinstance(n, ast.FunctionDef)
             and n.name == "node_llm_classifier_evaluation"]
_length_branches = [
    n for f in _node_fns for n in ast.walk(f)
    if isinstance(n, ast.If)
    and "FINISH_REASON_LENGTH" in {x.id for x in ast.walk(n.test)
                                   if isinstance(x, ast.Name)}]


def _singleton_floor(branch):
    """(lineno, ends_in_continue) of the ``len(chunk) == 1`` guard, or (None, False)."""
    for st in branch.body:
        if not isinstance(st, ast.If):
            continue
        calls = {n.func.id for n in ast.walk(st.test)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        names = {n.id for n in ast.walk(st.test) if isinstance(n, ast.Name)}
        # `n.value == 1` alone would also match `True`, which is not the same
        # statement; bools are excluded explicitly.
        ones = [n for n in ast.walk(st.test) if isinstance(n, ast.Constant)
                and n.value == 1 and not isinstance(n.value, bool)]
        if "len" in calls and "chunk" in names and ones:
            return st.lineno, isinstance(st.body[-1], ast.Continue)
    return None, False


def _first_before(lineno, linenos):
    """Is `lineno` strictly above every member of `linenos`, in source order?

    FALSE, NEVER A RAISE, when either side is missing. `min()` over an empty
    list raises ValueError and `_Absent` has no ordering, and BOTH would raise
    on exactly the defect these checks exist to catch -- the floor deleted, or
    the splitter call gone -- so the run would print one traceback where it owes
    named failures. The non-degeneracy checks beside these say which side was
    empty.
    """
    try:
        return bool(linenos) and isinstance(lineno, int) and lineno < min(linenos)
    except Exception:                                             # noqa: BLE001
        return False


_floor_line, _floor_continues = (_singleton_floor(_length_branches[0])
                                 if _length_branches else (None, False))
# SCOPED TO THE REACTIVE BRANCH, and that scoping is the point rather than a
# convenience: the node calls `_split_in_half` TWICE. The other call is the
# PROACTIVE splitter, which runs before dispatch and shapes `initial_chunks` in
# grouped mode only -- per-trial mode replaces that whole computation with
# `[[t] for t in trials]`. Counting both would have this check comparing the
# floor against a call that cannot run in this mode at all.
_split_lines = [n.lineno for n in ast.walk(_length_branches[0])
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "_split_in_half"] if _length_branches else []
_split_lines_all = [n.lineno for f in _node_fns for n in ast.walk(f)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "_split_in_half"]

check("2b(g) there is exactly one truncation branch, exactly one call to the "
      "splitter inside it, and the `len(chunk) == 1` floor is ABOVE that call "
      "and ends in `continue` -- so a singleton is recorded and never halved",
      (len(_length_branches), len(_split_lines), _floor_continues,
       _floor_line is not None and bool(_split_lines)
       and _first_before(_floor_line, _split_lines)),
      (1, 1, True, True))
check("2b(g) ...non-degeneracy: both line numbers were really found, so the "
      "comparison above is not two Nones agreeing",
      (isinstance(_floor_line, int), all(isinstance(n, int)
                                         for n in _split_lines)),
      (True, True))
check("2b(g) ...and the node's OTHER call to the splitter is accounted for: "
      "two in total, one reactive and one proactive, so the scoping above is "
      "excluding a known second call rather than silently missing one",
      # `ast.walk` yields breadth-first, so the list is NOT in source order and
      # is compared as a SET of line numbers rather than as a sequence. `min()`
      # over an empty list RAISES, and it would raise on exactly the defect
      # this check exists to catch -- the abort class this file's `_Absent`
      # exists to prevent -- so both are read through `_lo`.
      (len(_split_lines_all), set(_split_lines) <= set(_split_lines_all),
       _first_before(min(_split_lines_all) if _split_lines_all else None,
                     _split_lines)), (2, True, True))


# ===========================================================================
# SECTION 3 -- THE SCHEDULING SHAPE, PROVEN BY ORDER
# ===========================================================================
#
# THE ASSERTION IS AN INTEGER COMPARISON OVER TICKETS THE STUB ISSUED, not a
# measurement of elapsed time. "The first call had finished before the second
# started" is exactly `exit_ticket(first) < min(enter_ticket(others))`, and it
# is true or false regardless of how fast the machine is. A sleep-based version
# would pass on an idle laptop and fail under load while testing nothing.

section("SECTION 3 -- the warmup first, then the whole wave, bounded")

_R3, _S3 = run_node(_SIX, per_trial=True, parallel=6)
_warm_exit = _S3.exit_ticket(0)
_wave_enters = [_S3.enter_ticket(n) for n in _S3.wave_call_nos()]

check("3a  the warmup took ticket 1 -- it is the first request issued, and it "
      "is not a trial call",
      (_S3.enter_ticket(0), is_warmup(_S3.requests[0])), (1, True))
check("3b  ...and it had COMPLETED before ANY trial call was ISSUED. This is "
      "the cache-or-nothing rule stated as an integer comparison over tickets "
      "the stub itself issued: no trial request is ever sent against a cold "
      "prefix",
      all(_warm_exit < e for e in _wave_enters), True)
check("3b  ...non-degeneracy: there ARE trial calls to have been held back "
      "(the same assertion over an empty set is vacuously true)",
      len(_wave_enters), 6)
check("3c  ...and the warmup ran alone: nothing was in flight beside it",
      _S3.exit_ticket(0), 2)

# LIVENESS. With `bound` trial calls and a barrier of that size, the barrier
# can only be passed if all of them are genuinely in flight together. A
# sequential dispatcher makes the first waiter time out and BREAK the barrier,
# which is what this measures. FOUR TRIALS AND A BARRIER OF FOUR: under the
# retired schedule one of them was the priming call and could not join, so this
# scenario is also the direct measurement that none is held back now.
_R3b, _S3b = run_node([trial(i) for i in range(4)], per_trial=True, parallel=4,
                      stub=_Stub(barrier_size=4))
check("3d  all four trial calls really do overlap: all four reach a 4-party "
      "barrier. A sequential implementation cannot, and neither could a "
      "schedule that held one of the four back to write the cache",
      (_S3b.barrier_broken, _S3b.max_in_flight), (False, 4))
check("3d  ...and a warmup plus four trial requests were issued and answered",
      (len(_S3b.requests), len(_S3b.wave_requests()),
       len(at(_R3b, "evaluations"))), (5, 4, 4))

# SAFETY, AND IT IS FORCED RATHER THAN OBSERVED. Seven trial calls against a
# bound of 2, with a barrier that ALL SEVEN would have to reach: under the
# bound only two can ever be waiting, so the barrier times out and BREAKS --
# which is a fact about the ceiling, not about how fast the machine is. The
# `max_in_flight <= 2` reading beside it is the direct measurement; the barrier
# is what makes control c4 below able to disagree.
_R3c, _S3c = run_node([trial(i) for i in range(7)], per_trial=True, parallel=2,
                      stub=_Stub(barrier_size=7, barrier_timeout=1.0))
check("3e  the in-flight bound is a CEILING: with a bound of 2 and seven "
      "trial calls, never more than two were in flight, and a seven-party "
      "barrier could not be satisfied",
      (_S3c.max_in_flight <= 2, _S3c.barrier_broken), (True, True))
check("3e  ...non-degeneracy: calls were made at all, and all seven answered",
      (len(_S3c.wave_requests()), len(at(_R3c, "evaluations"))), (7, 7))

# A bound of 1 is the honest way to say "sequential" without leaving the mode.
_R3d, _S3d = run_node([trial(i) for i in range(4)], per_trial=True, parallel=1,
                      stub=_Stub(delay=0.01))
check("3f  a bound of 1 means sequential: never two in flight, and all four "
      "trials still evaluated",
      (_S3d.max_in_flight, len(at(_R3d, "evaluations"))), (1, 4))

# ONE TRIAL: the warmup still runs -- a patient with one trial is exactly the
# patient for whom a shared prefix buys the least, and issuing the warmup
# anyway is the cost of a rule that has no exceptions to reason about.
_R3e, _S3e = run_node([trial(0)], per_trial=True, parallel=4)
check("3g  a single-trial patient issues a warmup and one trial call, and "
       "never has two in flight",
      (len(_S3e.requests), len(_S3e.wave_requests()), _S3e.max_in_flight,
       len(at(_R3e, "evaluations"))), (2, 1, 1, 1))

# THE WARMUP IS CONSUMED BEFORE DISPATCH, WHICH IS WHAT MAKES
# `_account_unconsumed()` PROVABLY UNAFFECTED BY IT. Asserted rather than
# reasoned: a refusal on the first trial abandons every other wave response and
# folds them as `unconsumed`, so if the warmup could ever land in `_prefetched`
# it would appear there. It does not, and the warmup's own row is present and
# is NOT marked unconsumed.
_R3h, _S3h = run_node(_SIX, per_trial=True, parallel=6,
                      stub=_Stub(refuse_for=[_SIX[0]["trial"]["nct_id"]]))
_details3h = at(_R3h, "llm_classifier_call_details") or []
_warm_rows3h = [d for d in _details3h if d.get("warmup")]
check("3j  a refusal abandons the rest of the wave and folds it as unconsumed "
      "-- and the warmup row is NOT among the folded ones, because it was "
      "consumed on the node thread before any of them was issued",
      (len(_warm_rows3h),
       [d.get("unconsumed") for d in _warm_rows3h],
       sum(1 for d in _details3h if d.get("unconsumed"))),
      (1, [None], 5))
check("3j  ...non-degeneracy: the run really did refuse and really did abandon "
      "responses, so the zero above is a finding",
      (bool(at(_R3h, "llm_classifier_refusal")), len(_details3h)), (True, 7))

# WHAT RUNS ON A WORKER, AS A STRUCTURAL CLAIM. Every increment of
# MARKDOWN_ESCAPE_DECODE_UNRESOLVED and ESCAPED_ENTITY_DECODE_UNRESOLVED
# happens inside `_render_trial_blocks`'s decoders, and `Counter[k] += 1` is a
# load-add-store the interpreter may switch threads inside -- so a render moved
# onto a worker would silently lose counts from the two counters that report
# third-party text reaching the judge. The render is therefore done on the node
# thread before dispatch, and the worker body is asserted to contain nothing
# but the call.
_issue_fn = [n for n in ast.walk(_eval_tree)
             if isinstance(n, ast.FunctionDef) and n.name == "_issue"]
_issue_names = ({n.id for f in _issue_fn for n in ast.walk(f)
                 if isinstance(n, ast.Name)}
                | {n.attr for f in _issue_fn for n in ast.walk(f)
                   if isinstance(n, ast.Attribute)})
check("3i  there is exactly one worker body and it renders NOTHING: no "
      "renderer, no prompt builder and no counter is reachable from it",
      (len(_issue_fn),
       sorted(_issue_names & {"_render_trial_blocks", "_build_trials_text",
                              "_user_prompt_for", "_wrap_trials",
                              "MARKDOWN_ESCAPE_DECODE_UNRESOLVED",
                              "ESCAPED_ENTITY_DECODE_UNRESOLVED",
                              "PER_TRIAL_CALL_FAILURES"})), (1, []))
check("3i  ...non-degeneracy: the walk sees the worker's real contents, so an "
      "empty intersection is a finding rather than a walk that matched nothing",
      "call_matching_model" in _issue_names, True)

# GROUPED MODE CREATES NO POOL AT ALL, which is the OFF-arm promise stated as
# a measurement rather than as a claim about source text.
_R3f, _S3f = run_node(_SIX, per_trial=False, parallel=4)
check("3h  grouped mode issues one call and never has two in flight",
      (len(_S3f.requests), _S3f.max_in_flight), (1, 1))


# ===========================================================================
# SECTION 3b -- CACHE-OR-NOTHING: WHAT HAPPENS WHEN THE WARMUP DOES NOT
# ===========================================================================
#
# THE RULE HAS EXACTLY THREE OUTCOMES AND EACH IS MEASURED HERE.
#
#   * the warmup ANSWERS  -> the whole wave goes out behind it (section 3)
#   * the warmup FAILS    -> NO trial call is issued at all and the patient is
#                            failed through the existing zero-success floor, so
#                            MAX_LLM_CLASSIFIER_RETRIES sees it and the batch
#                            checkpoint resumes it. There is no uncached
#                            fallback anywhere.
#   * the warmup is REFUSED for its SHAPE -> the provider will refuse it again
#                            however many times it is retried, so the patient
#                            degrades to the retired one-then-rest schedule
#                            with a named counter rather than being failed over
#                            an infrastructure request.
#
# The third is the one that must be DETECTED rather than assumed: nobody here
# can read this provider's validation rules for a one-token ceiling, and a
# design that assumed either answer would be wrong on half of them.

section("SECTION 3b -- the warmup fails, and no trial call is issued")

_FOUR = [trial(i) for i in range(4)]

_before_wu = dict(PER_TRIAL_WARMUP_DEGRADATIONS)
_before_wu_calls = dict(PER_TRIAL_CALL_FAILURES)
_R3w, _S3w = run_node(_FOUR, per_trial=True, parallel=4,
                      stub=_Stub(warmup_raise=RuntimeError("endpoint down")))
_after_wu = dict(PER_TRIAL_WARMUP_DEGRADATIONS)
_after_wu_calls = dict(PER_TRIAL_CALL_FAILURES)

check("3w(a) a warmup that fails issues ZERO trial calls -- the whole point "
      "of cache-or-nothing: fifteen full-price requests against a cold prefix "
      "are worse than one patient re-run",
      (len(_S3w.wave_requests()), len(_S3w.warmup_requests()),
       len(_S3w.requests)), (0, 1, 1))
check("3w(b) ...and the node returns the API-error result, through the same "
      "floor a total outage takes, so the retry router sees one shape",
      (at(_R3w, "evaluations"), at(_R3w, "llm_classifier_retries"),
       bool(at(_R3w, "error"))), ([], 1, True))
check("3w(c) ...and the error names the WARMUP and the endpoint's own "
      "diagnosis, so an operator is not sent looking at the judge or at a "
      "trial",
      ("warmup" in str(at(_R3w, "error")).lower(),
       "endpoint down" in str(at(_R3w, "error")),
       "RuntimeError" in str(at(_R3w, "error"))), (True, True, True))
check("3w(d) ...it carries the provenance every failure return carries, so "
      "the row is not anonymous",
      (at(_R3w, "llm_classifier_prompt_version") is not None,
       isinstance(at(_R3w, "llm_classifier_prompt_sha256"), str),
       at(_R3w, "llm_classifier_output_ceiling")
       == config.MATCHING_MAX_TOKENS), (True, True, True))
check("3w(e) ...and it reports NO tokens and an EMPTY ledger, because the "
      "warmup raised before any usage object existed -- absent rather than a "
      "zero nobody measured",
      ("llm_classifier_input_tokens" in _R3w
       if isinstance(_R3w, dict) else _Absent("no result"),
       at(_R3w, "llm_classifier_call_details")), (False, []))
check("3w(f) FAILURE IS NOT SILENCE: the counter moved by exactly one, under "
      "a key that names the exception type and separates a transport failure "
      "from a refusal of the request shape",
      (sum(_after_wu.values()) - sum(_before_wu.values()),
       _after_wu.get(f"{WARMUP_FAILURE_KEY_PREFIX}RuntimeError", 0)
       - _before_wu.get(f"{WARMUP_FAILURE_KEY_PREFIX}RuntimeError", 0)),
      (1, 1))
check("3w(g) ...and NOT under the per-trial CALL counter, which would report "
      "four trials that were never sent as four failed calls",
      sum(_after_wu_calls.values()) - sum(_before_wu_calls.values()), 0)

# THE WARMUP TOKENS ARE BILLED WHEN THERE WERE ANY. `_billed_so_far()` is empty
# above because the warmup RAISED; the meaningful case is a warmup that
# answered and a wave that then failed entirely, which is section 5's 5i --
# repeated here in its own terms because it is the half of requirement "the
# warmup's billed tokens are carried" that a raise cannot demonstrate.
_R3x, _S3x = run_node(_FOUR, per_trial=True, parallel=4,
                      stub=_Stub(fail_for=[t["trial"]["nct_id"]
                                           for t in _FOUR]))
check("3w(h) a warmup that ANSWERED and a wave that then failed entirely "
      "still bills the warmup, and the floor still fires -- which is only "
      "true because the floor asks about verdicts rather than about calls",
      (at(_R3x, "llm_classifier_calls"),
       at(_R3x, "llm_classifier_input_tokens"), bool(at(_R3x, "error")),
       at(_R3x, "evaluations")), (1, 1000, True, []))

# ── THE MODEL CHECK RUNS ON THE WARMUP, WHICH IS WHY IT IS CHEAP ───────────
_R3y, _S3y = run_node(_FOUR, per_trial=True, parallel=4,
                      stub=_Stub(warmup_model="some-other-judge"))
check("3w(i) a mismatched answering model raises on the WARMUP, before the "
      "wave -- one one-token request rather than four full-price ones",
      (isinstance(_R3y, _Absent),
       MatchingModelMismatchError.__name__ in repr(_R3y),
       len(_S3y.wave_requests())), (True, True, 0))
check("3w(i) ...non-degeneracy: the run really did reach the provider, so the "
      "zero above is a finding rather than a node that never dispatched",
      len(_S3y.warmup_requests()), 1)

# ── THE PROVIDER REFUSES THE SHAPE: FALL BACK, DO NOT FAIL ────────────────
#
# THE FALLBACK IS THE RETIRED SCHEDULE, AND IT IS PROVEN BY THE BARRIER RATHER
# THAN BY A LOG LINE. Four trials and a four-party barrier: one-then-rest
# awaits the first alone, so it waits for peers that cannot come and BREAKS the
# barrier -- exactly the reading section 3d uses to prove the shipped schedule
# holds nothing back, run in reverse.
_before_rej = dict(PER_TRIAL_WARMUP_DEGRADATIONS)
_R3z, _S3z = run_node(
    _FOUR, per_trial=True, parallel=4,
    stub=_Stub(barrier_size=4, barrier_timeout=1.0,
               warmup_raise=_WarmupRefused(
                   "Invalid value for 'max_completion_tokens': must be >= 16")))
_after_rej = dict(PER_TRIAL_WARMUP_DEGRADATIONS)

check("3w(j) a provider that REFUSES the minimal-output request shape does "
      "not fail the patient: every trial is still evaluated",
      (bool(at(_R3z, "error")),
       sorted(e.get("nct_id") for e in at(_R3z, "evaluations"))),
      (False, sorted(t["trial"]["nct_id"] for t in _FOUR)))
check("3w(k) ...and the schedule that runs is ONE-THEN-REST: a four-party "
      "barrier over four trial calls is BROKEN, because one of them is awaited "
      "alone as the cache writer. A wave with nothing held back satisfies it, "
      "which is what 3d measures on the shipped path",
      (_S3z.barrier_broken, len(_S3z.wave_requests())), (True, 4))
check("3w(l) ...the fallback SAYS SO: the counter carries the reason, under "
      "the rejection key rather than the transport-failure key",
      ({k: _after_rej.get(k, 0) - _before_rej.get(k, 0) for k in _after_rej
        if _after_rej.get(k, 0) != _before_rej.get(k, 0)}),
      {WARMUP_REJECTED_MINIMAL_OUTPUT: 1})
check("3w(m) ...and the warmup contributes no ledger row, because it raised: "
      "four billed calls for four trials",
      (at(_R3z, "llm_classifier_calls"),
       [c.get("warmup")
        for c in at(_R3z, "llm_classifier_call_details")]),
      (4, [None, None, None, None]))

# THE OTHER REJECTION, and its one extra consequence: the routing hint is
# dropped for the wave too. Carrying a parameter the provider has just refused
# into the fallback's calls would refuse every one of them and turn a
# recoverable configuration finding into a failed patient.
_before_key = dict(PER_TRIAL_WARMUP_DEGRADATIONS)
_R3k, _S3k = run_node(
    _FOUR, per_trial=True, parallel=4,
    stub=_Stub(warmup_raise=_WarmupRefused(
        "Unrecognized request argument supplied: prompt_cache_key")))
_after_key = dict(PER_TRIAL_WARMUP_DEGRADATIONS)
check("3w(n) a refused ROUTING HINT is its own reason, and the patient still "
      "completes",
      ({k: _after_key.get(k, 0) - _before_key.get(k, 0) for k in _after_key
        if _after_key.get(k, 0) != _before_key.get(k, 0)},
       len(at(_R3k, "evaluations"))), ({WARMUP_REJECTED_CACHE_KEY: 1}, 4))
check("3w(o) ...and the hint is DROPPED for the wave, so the fallback's calls "
      "cannot be refused for the parameter that was just refused",
      sorted({r.get("prompt_cache_key") for r in _S3k.wave_requests()},
             key=lambda v: (v is not None, v or "")), [None])
check("3w(o) ...non-degeneracy: the shipped path DOES send it, so the None "
      "above is the drop working rather than a hint nothing ever sends",
      sorted({r.get("prompt_cache_key") is not None
              for r in _S3w.warmup_requests()}), [True])

# ── THE CLASSIFIER IS NARROW, AND BOTH HALVES OF THE CONJUNCTION MATTER ───
check("3w(p) a 400 that names the output parameter is a refusal of the shape",
      classify_warmup_rejection(
          _WarmupRefused("unsupported max_completion_tokens")),
      WARMUP_REJECTED_MINIMAL_OUTPUT)
check("3w(p) ...a 400 that names the routing hint is the other one, and it is "
      "asked FIRST so a request refused for both stops sending the hint",
      (classify_warmup_rejection(
          _WarmupRefused("unknown argument prompt_cache_key")),
       classify_warmup_rejection(_WarmupRefused(
           "prompt_cache_key and max_completion_tokens are both invalid"))),
      (WARMUP_REJECTED_CACHE_KEY, WARMUP_REJECTED_CACHE_KEY))
check("3w(q) ...a 400 that names NEITHER is not a refusal of the shape: a "
      "context overflow and an invalid schema are 400s about this patient's "
      "request and will fail every trial call too, so falling back for them "
      "would replace one clean failure with N identical ones",
      classify_warmup_rejection(
          _WarmupRefused("context_length_exceeded")), None)
check("3w(r) ...and the parameter name ALONE is not enough: a 500 whose body "
      "quotes the request is a transport failure, not a refusal",
      (classify_warmup_rejection(
          _WarmupRefused("max_completion_tokens", status_code=500)),
       classify_warmup_rejection(RuntimeError("max_completion_tokens"))),
      (None, None))
check("3w(s) ...and it reads a status carried on a `response` object too, "
      "which is the shape several clients use",
      classify_warmup_rejection(
          type("_E", (Exception,), {})(
              "bad max_completion_tokens")) is None, True)
_resp_shaped = RuntimeError("bad max_completion_tokens")
_resp_shaped.response = type("_R", (), {"status_code": 400})()
check("3w(s) ...positively: an exception carrying the status on `response` is "
      "classified, which is what makes the reading above a finding",
      classify_warmup_rejection(_resp_shaped),
      WARMUP_REJECTED_MINIMAL_OUTPUT)
check("3w(t) ...and it never raises on an exception carrying neither, which "
      "would replace a named transport failure with an AttributeError",
      drive(classify_warmup_rejection, KeyboardInterrupt()), None)


# ===========================================================================
# SECTION 3c -- THE FALLBACK'S CACHE WRITER OBEYS THE SAME RULE
# ===========================================================================
#
# CACHE-OR-NOTHING IS A PROPERTY OF THE NODE, NOT OF THE DEDICATED WARMUP. When
# the provider refuses the warmup's SHAPE the patient degrades to the retired
# one-then-rest schedule, which has a cache writer of its own: the first trial
# call, held back and awaited alone. Until this section existed that writer's
# outcome was FILED AND NEVER READ -- so a writer that exhausted its transport
# retries released N-1 full-price requests against a prefix nothing had written,
# with no counter, no error and a patient reported as a clean run. The only
# trace was one isolated per-trial failure among N-1 ordinary successes, which
# is what an unlucky trial looks like.
#
# THE REMEDY IS THE ONE ALREADY BUILT. `pending.clear()`, the warmup-error
# state, the existing zero-success floor, the existing API-error return: the
# retry router and the batch checkpoint already handle that shape, and a second
# failure shape for one event would be a second thing for every consumer to
# agree about.

section("SECTION 3c -- the fallback's held-back writer is inspected")

_FB_REJECT = _WarmupRefused(
    "Invalid value for 'max_completion_tokens': must be >= 16")
# THE WRITER IS THE FIRST TRIAL IN DISPATCH ORDER, which is the batch's first.
_FB_WRITER_ID = _FOUR[0]["trial"]["nct_id"]

_before_fb = dict(PER_TRIAL_WARMUP_DEGRADATIONS)
_before_fb_calls = dict(PER_TRIAL_CALL_FAILURES)
_R3f, _S3fb = run_node(
    _FOUR, per_trial=True, parallel=4,
    stub=_Stub(warmup_raise=_FB_REJECT, fail_for=[_FB_WRITER_ID]))
_after_fb = dict(PER_TRIAL_WARMUP_DEGRADATIONS)
_after_fb_calls = dict(PER_TRIAL_CALL_FAILURES)

check("3fw(a) a fallback whose cache writer FAILS issues no further trial "
      "call: exactly ONE trial request, the writer, and not the three "
      "full-price uncached ones the un-inspected outcome released. The warmup "
      "request is counted too -- the provider REFUSED it, which means it "
      "reached the provider",
      (len(_S3fb.wave_requests()), len(_S3fb.warmup_requests()),
       len(_S3fb.requests)), (1, 1, 2))
check("3fw(b) ...and the one request that WAS issued is the writer, so the "
      "count above is the held-back call rather than an arbitrary survivor",
      _S3fb.wave_ids(), [[_FB_WRITER_ID]])
check("3fw(c) ...the patient is failed through the SAME floor a warmup "
      "failure takes, so the retry router and the checkpoint see one shape",
      (at(_R3f, "evaluations"), at(_R3f, "llm_classifier_retries"),
       bool(at(_R3f, "error"))), ([], 1, True))
check("3fw(d) ...and the error says which writer failed rather than claiming "
      "no trial call was issued, which would be false about a request the "
      "provider really answered for",
      ("fallback" in str(at(_R3f, "error")).lower(),
       "no trial call was issued" in str(at(_R3f, "error")),
       "stub failure" in str(at(_R3f, "error"))), (True, False, True))
check("3fw(e) FAILURE IS NOT SILENCE: the counter moved under a key that "
      "names the FALLBACK writer and the exception type, separately from the "
      "dedicated warmup's transport-failure key -- only this one says the "
      "rejection classification is worth revisiting",
      ({k: _after_fb.get(k, 0) - _before_fb.get(k, 0) for k in _after_fb
        if _after_fb.get(k, 0) != _before_fb.get(k, 0)}),
      {WARMUP_REJECTED_MINIMAL_OUTPUT: 1,
       f"{WARMUP_FALLBACK_WRITER_FAILURE_KEY_PREFIX}RuntimeError": 1})
check("3fw(f) ...and the writer is NOT also counted as a per-trial call "
      "failure or folded as an abandoned response: one request, one finding",
      (sum(_after_fb_calls.values()) - sum(_before_fb_calls.values()),
       at(_R3f, "llm_classifier_call_details")), (0, []))
check("3fw(g) ...and no token figure is invented for it: the writer raised, so "
      "no usage object ever existed and the keys are ABSENT rather than zero",
      ("llm_classifier_input_tokens" in _R3f
       if isinstance(_R3f, dict) else _Absent("no result"),
       "llm_classifier_calls" in _R3f
       if isinstance(_R3f, dict) else _Absent("no result")), (False, False))

# THE HEALTHY FALLBACK IS UNCHANGED, which is the other half of the claim: the
# inspection must not turn a working degraded schedule into a failed patient.
_before_ok = dict(PER_TRIAL_WARMUP_DEGRADATIONS)
_R3g, _S3g = run_node(_FOUR, per_trial=True, parallel=4,
                      stub=_Stub(warmup_raise=_WarmupRefused(
                          "Invalid value for 'max_completion_tokens': "
                          "must be >= 16")))
_after_ok = dict(PER_TRIAL_WARMUP_DEGRADATIONS)
check("3fw(h) a fallback whose writer SUCCEEDS is untouched: all four trials "
      "evaluated, four wave calls, no error",
      (bool(at(_R3g, "error")), len(_S3g.wave_requests()),
       sorted(e.get("nct_id") for e in at(_R3g, "evaluations"))),
      (False, 4, sorted(t["trial"]["nct_id"] for t in _FOUR)))
check("3fw(i) ...and it records ONLY the rejection -- no fallback-writer key, "
      "so the counter separates 'degraded and worked' from 'degraded and "
      "lost'",
      ({k: _after_ok.get(k, 0) - _before_ok.get(k, 0) for k in _after_ok
        if _after_ok.get(k, 0) != _before_ok.get(k, 0)}),
      {WARMUP_REJECTED_MINIMAL_OUTPUT: 1})
check("3fw(j) ...and the writer's own response is CONSUMED rather than "
      "re-issued: four billed calls for four trials, each answering once",
      (at(_R3g, "llm_classifier_calls"),
       sorted(len(ids) for ids in _S3g.wave_ids())), (4, [1, 1, 1, 1]))


# ===========================================================================
# SECTION 3d -- AN INTERRUPT MID-WAVE CANCELS WHAT HAS NOT STARTED
# ===========================================================================
#
# `ThreadPoolExecutor` AS A CONTEXT MANAGER CALLS `shutdown(wait=True)` WITH
# `cancel_futures` DEFAULTING TO FALSE. So an exception out of the result loop
# -- KeyboardInterrupt included -- let every QUEUED call run to completion
# before the exception surfaced. An operator pressing Ctrl-C mid-wave got
# minutes of continued billing that reads as a hang: the process is not
# stopping, and the reason is that it is still buying responses nobody will
# read.
#
# THE INTERRUPT IS RAISED INSIDE A WORKER, NOT AS A REAL SIGNAL. `_issue`
# catches `Exception`, so a BaseException that is not an Exception travels out
# of the worker, into the future, and is re-raised on the node thread at
# `future.result()` -- byte for byte the propagation a real SIGINT produces
# there. A real `os.kill(..., SIGINT)` in a process that also runs sixty other
# test files in CI bucket A is a way to abort the run rather than measure it.
#
# THE WORKERS ARE PARKED, WHICH IS WHAT MAKES "QUEUED CALLS NEVER FIRE"
# MEASURABLE. With a bound of 2 and eight trials, the two workers are held on
# an Event while six tasks sit in the queue; the interrupt fires, the node
# cancels the queue, and only the tasks that had already STARTED can ever have
# reached the stub. The watchdog releases the Event a grace period after the
# stub itself recorded the interrupt, so the wait is bounded and the control
# below is fast rather than draining a queue at full speed.

section("SECTION 3d -- Ctrl-C mid-wave stops buying responses")

_EIGHT = [trial(i) for i in range(8)]
_INT_ID = _EIGHT[0]["trial"]["nct_id"]
# Generous relative to the critical path it has to cover -- a future that is
# already done being read on the node thread, then `shutdown(...)`. It is not
# the mechanism, only the ceiling on how long the parked workers hold.
_INT_GRACE = 3.0


def _release_after_interrupt(stub, hold, grace):
    stub.interrupt_raised.wait(timeout=30.0)
    time.sleep(grace)
    hold.set()


def _stage5_threads():
    return [t.name for t in threading.enumerate()
            if t.name.startswith("stage5")]


def _interrupt_probe(node=None):
    """Drive one interrupted wave; return (result, stub, leaked_thread_names).

    IT DRIVES THE NODE ITSELF RATHER THAN CALLING ``run_node``, and the reason
    is a hazard this file's revert harness found rather than a preference.
    ``run_node`` clears the dependency override in its ``finally``, the instant
    the node returns. On a node whose executor is NOT joined before the
    exception leaves it -- which is exactly the defect shape section 3d exists
    to catch -- worker threads are still running at that moment, and their next
    ``deps.get_openai_client()`` therefore resolves to WHATEVER IS INSTALLED
    NEXT: the following scenario's stub, whose request count they then corrupt,
    or, if nothing is installed, a REAL client built from the real credentials
    file. A test that can make a billed call when the code under test regresses
    is not a stub-only test.
    So the override is held until every worker is gone, and the leak is
    MEASURED first -- at the moment the node returned, which is what the
    assertion is about -- rather than being waited away before it is read.
    """
    hold = threading.Event()
    stub = _Stub(interrupt_for=[_INT_ID], hold=hold)
    watcher = threading.Thread(target=_release_after_interrupt,
                               args=(stub, hold, _INT_GRACE), daemon=True)
    watcher.start()
    node = node or node_llm_classifier_evaluation
    saved = (config.MATCHING_PER_TRIAL_CALLS_ENABLED,
             config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS)
    deps.set_override(deps.OPENAI_CLIENT, stub)
    # Bound BEFORE the try so the return below cannot NameError if anything in
    # it goes wrong -- `drive` catches BaseException today, and a helper that
    # depends on that staying true is one edit from an abort.
    result, leaked = _Absent("the probe body did not run"), []
    try:
        config.MATCHING_PER_TRIAL_CALLS_ENABLED = True
        config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS = 2
        result = drive(node, {
            "patient_data": PATIENT, "filtered_trials": _EIGHT,
            "llm_classifier_retries": 0, "mesh_filter_applied": True,
            "mesh_filter_skip_reason": "applied", "stage_timings": {}})
        leaked = _stage5_threads()
    finally:
        hold.set()
        watcher.join(timeout=30.0)
        _deadline = time.monotonic() + 30.0
        while _stage5_threads() and time.monotonic() < _deadline:
            time.sleep(0.02)
        (config.MATCHING_PER_TRIAL_CALLS_ENABLED,
         config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS) = saved
        deps.clear_override(deps.OPENAI_CLIENT)
    return result, stub, leaked


_R3i, _S3i, _int_threads = _interrupt_probe()
_int_seen = {ids[0] for ids in _S3i.wave_ids() if ids}
_int_never = [t["trial"]["nct_id"] for t in _EIGHT
              if t["trial"]["nct_id"] not in _int_seen]

check("3int(a) the interrupt SURFACES rather than being swallowed: the node "
      "raises KeyboardInterrupt out to its caller, which is what an interrupt "
      "is for -- it is not converted into a failure return and not absorbed "
      "by `_issue`'s `except Exception`",
      (isinstance(_R3i, _Absent), "KeyboardInterrupt" in repr(_R3i)),
      (True, True))
check("3int(b) QUEUED CALLS NEVER FIRE: with a bound of 2 over eight trials, "
      "at most the bound plus the interrupter can ever have reached the "
      "provider, and at least five were cancelled before being issued",
      (len(_S3i.wave_requests()) <= 3, len(_int_never) >= 5), (True, True))
check("3int(c) ...non-degeneracy: the warmup ran and the interrupting call "
      "really did reach the provider, so the cancellations above are a "
      "finding rather than a node that never dispatched",
      (len(_S3i.warmup_requests()), _INT_ID in _int_seen), (1, True))
check("3int(d) A CANCELLED CALL IS BILLED BY NOBODY AND APPEARS IN NO LEDGER: "
      "the node published no result at all, so there is no `call_details` and "
      "no token total that could carry a request that was never issued -- and "
      "`_account_unconsumed` cannot misclassify one, because only a RESOLVED "
      "future is ever filed into `_prefetched`",
      isinstance(_R3i, dict), False)
check("3int(e) NO THREAD LEAKS: the explicit shutdown joins every worker "
      "before the exception leaves the node, so an in-flight request is "
      "waited for exactly once and none outlives the patient. Measured AT the "
      "moment the node returned, not after a wait",
      _int_threads, [])
# THE ACCOUNTING CLAIM, DIRECTLY. 3int(d) says it by consequence -- there is no
# published result, so there is no ledger to misclassify anything into. This
# says it about the CODE: the only statement that files anything into
# `_prefetched` inside the executor block is the one that reads a future's
# `.result()`, so a future that was cancelled rather than resolved cannot enter
# the dict `_account_unconsumed` folds. It is structural because there is no
# scenario in which a cancelled future is BOTH filed and observable: the node
# raises on every path that cancels one.
_fut_loops = [n for f in _node_fns for n in ast.walk(f) if isinstance(n, ast.For)
              and isinstance(n.iter, ast.Name) and n.iter.id == "_futures"]
_prefetched_stores = [
    n for f in _node_fns for n in ast.walk(f)
    if isinstance(n, ast.Assign)
    and any(isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
            and t.value.id == "_prefetched" for t in n.targets)]
_stores_in_loop = [n for lp in _fut_loops for n in ast.walk(lp)
                   if n in _prefetched_stores]
_result_reads = [n for lp in _fut_loops for n in ast.walk(lp)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "result"]

check("3int(g) A CANCELLED FUTURE CANNOT BE FILED, structurally: there is one "
      "loop over `_futures`, every `_prefetched` store inside the executor "
      "block is in it, and it reaches the dict only through `.result()` -- so "
      "only a RESOLVED future is ever recorded and `_account_unconsumed` "
      "cannot see a call that was never issued",
      (len(_fut_loops), len(_stores_in_loop), len(_result_reads) >= 1),
      (1, 1, True))
check("3int(g) ...non-degeneracy: the node really does file into `_prefetched` "
      "elsewhere too (the fallback writer), so the count above is a scoping "
      "finding rather than a walk that matched nothing",
      len(_prefetched_stores) > len(_stores_in_loop), True)
check("3int(f) ...and NO REAL CLIENT WAS EVER BUILT: `peek` is UNSET, so no "
      "worker resolved the seam after the override came off. This is the "
      "no-spend guarantee stated as a measurement -- a leaked worker is one "
      "`deps.get_openai_client()` away from a real, billed request",
      deps.peek(deps.OPENAI_CLIENT) is deps.UNSET, True)


# ===========================================================================
# SECTION 4 -- DETERMINISTIC MERGE, IN TRIAL ORDER
# ===========================================================================
#
# The requests are concurrent; everything the node PUBLISHES is not. The stub
# below answers in the pool's order and the barrier in 3d already showed that
# order is not trial order, so the two runs compared here are a real test of
# the merge rather than of a queue that happened to stay sorted.

section("SECTION 4 -- responses merge in trial order, run after run")


def _merge_shape(result, stub):
    """Everything about a result whose ORDER the merge decides."""
    return {
        "verdict_ids": [e.get("nct_id") for e in at(result, "evaluations")],
        "call_index_by_trial": {e.get("nct_id"): e.get("call_index")
                                for e in at(result, "evaluations")},
        "call_indices": [c.get("call_index")
                         for c in at(result, "llm_classifier_call_details")],
        "call_trials": [c.get("trials")
                        for c in at(result, "llm_classifier_call_details")],
        "calls": result.get("llm_classifier_calls")
                 if isinstance(result, dict) else _Absent("no result"),
    }


_TEN = [trial(i) for i in range(10)]
_TEN_IDS = [t["trial"]["nct_id"] for t in _TEN]

# THE CALL INDEX OF TRIAL i. The warmup is call 1 -- it is a billed request and
# is numbered like one -- so the first trial is call 2. Written as an
# expression rather than as a literal range so a future change to what precedes
# the wave moves one line here instead of four expectations.
_WARMUP_CALLS = 1


def _trial_call_index(i):
    return _WARMUP_CALLS + 1 + i


# THE TWO RUNS COMPLETE IN OPPOSITE ORDERS, ON PURPOSE. `_OrderedStub` (defined
# below, used here) holds every trial call at a barrier and then hands out
# completion turns in a sequence this file chooses, so "the pool answered them
# in a different order" is a fact rather than a hope. A pair of runs with a
# sleep in them would be the same assertion decided by the scheduler, which is
# what CI bucket A -- 61 test processes at once -- turns into a flake.
_R4a, _S4a = run_node(_TEN, per_trial=True, parallel=16,
                      stub=_OrderedStub(_TEN_IDS))
_R4b, _S4b = run_node(_TEN, per_trial=True, parallel=16,
                      stub=_OrderedStub(list(reversed(_TEN_IDS))))

check("4a  two runs of the same batch publish the IDENTICAL merge shape, "
      "though their responses completed in OPPOSITE orders",
      _merge_shape(_R4a, _S4a), _merge_shape(_R4b, _S4b))
check("4a  ...non-degeneracy: the completion orders really were opposite, and "
      "neither run's forced hand-off timed out or broke its barrier",
      ([i[0] for i in _S4a.completion_order() if i]
       == list(reversed([i[0] for i in _S4b.completion_order() if i])),
       _S4a.order_timed_out, _S4b.order_timed_out,
       _S4a.barrier_broken, _S4b.barrier_broken),
      (True, False, False, False, False))
check("4b  call_details is numbered 1..N with no gaps, which is the join key "
      "trial_matches.call_index uses. N is 11: one warmup and ten trials",
      [c.get("call_index")
       for c in at(_R4a, "llm_classifier_call_details")],
      list(range(1, 12)))
check("4c  ...and every TRIAL call carried exactly one trial, while the "
      "warmup carried none -- which is what excludes it from any per-trial "
      "accounting that groups on this field",
      (sorted({c.get("trials")
               for c in at(_R4a, "llm_classifier_call_details")
               if not c.get("warmup")}),
       [c.get("trials") for c in at(_R4a, "llm_classifier_call_details")
        if c.get("warmup")]), ([1], [0]))
check("4d  each trial's entry names the call that answered for it, and the "
      "assignment is trial order: trial i was answered by call i+2, the "
      "warmup having taken call 1",
      [_merge_shape(_R4a, _S4a)["call_index_by_trial"][t["trial"]["nct_id"]]
       for t in _TEN],
      [_trial_call_index(i) for i in range(10)])
check("4e  ...and llm_classifier_calls agrees with the ledger's length, "
      "warmup included: it is a billed call and is counted as one",
      (at(_R4a, "llm_classifier_calls"),
       len(at(_R4a, "llm_classifier_call_details"))), (11, 11))
check("4e  ...and no evaluation was ever attributed to the warmup's call "
      "index, which would be a verdict credited to a request that carried no "
      "trial",
      sorted(set(_merge_shape(_R4a, _S4a)["call_index_by_trial"].values())
             & {1}), [])

# A BOUND WIDE ENOUGH FOR EVERY TRIAL CALL TO START AT ONCE, which the hand-off
# below requires: ten calls must all be in flight before any of them may be
# told to finish.
_R4c, _S4c = run_node(_TEN, per_trial=True, parallel=16,
                      stub=_OrderedStub(list(reversed(_TEN_IDS))))
_completion = [ids[0] for ids in _S4c.completion_order() if ids]
check("4f  the last trial's response arrived before every earlier one, and "
      "the merge is STILL in trial order",
      _merge_shape(_R4c, _S4c)["call_index_by_trial"],
      {t["trial"]["nct_id"]: _trial_call_index(i)
       for i, t in enumerate(_TEN)})
check("4f  ...non-degeneracy: the responses really did complete in REVERSE "
      "batch order -- forced, not hoped for, so 4f is a measurement and not a "
      "queue that happened to stay sorted",
      (_S4c.barrier_broken, _S4c.order_timed_out,
       _completion), (False, False, list(reversed(_TEN_IDS))))


# ===========================================================================
# SECTION 5 -- PER-CALL FAILURE ISOLATION, AND ITS FLOOR
# ===========================================================================

section("SECTION 5 -- one failed call costs one trial, not the patient")

_FIVE = [trial(i) for i in range(5)]
_VICTIM = _FIVE[2]["trial"]["nct_id"]

_before = dict(PER_TRIAL_CALL_FAILURES)
_R5, _S5 = run_node(_FIVE, per_trial=True, parallel=4,
                    stub=_Stub(fail_for=[_VICTIM]))
_after = dict(PER_TRIAL_CALL_FAILURES)

check("5a  the patient COMPLETED: no error, and every trial is accounted for "
      "exactly once",
      (at(_R5, "error"),
       sorted(e.get("nct_id") for e in at(_R5, "evaluations"))),
      ("", sorted(t["trial"]["nct_id"] for t in _FIVE)))
_by_id5 = {e.get("nct_id"): e for e in at(_R5, "evaluations")}
check("5b  the failed trial is recorded not-evaluable, under its OWN reason",
      (at(_by_id5, _VICTIM).get("eligible")
       if isinstance(at(_by_id5, _VICTIM), dict) else _Absent("no entry"),
       at(_by_id5, _VICTIM).get("not_evaluable_reason")
       if isinstance(at(_by_id5, _VICTIM), dict) else _Absent("no entry")),
      ("not_evaluable", NOT_EVALUABLE_CALL_FAILED))
check("5c  ...and NOT under the model-omission reason, which would blame the "
      "judge for a transport failure it never saw",
      at(_by_id5, _VICTIM).get("not_evaluable_reason")
      == NOT_EVALUABLE_MODEL_OMITTED, False)
check("5d  the other four were evaluated normally",
      sorted(i for i, e in _by_id5.items() if e.get("eligible") == "eligible"),
      sorted(t["trial"]["nct_id"] for t in _FIVE
             if t["trial"]["nct_id"] != _VICTIM))
check("5e  the four surviving calls were still billed and are in the ledger, "
      "with the warmup beside them -- five billed requests for four verdicts",
      (at(_R5, "llm_classifier_calls"),
       len(at(_R5, "llm_classifier_call_details")),
       sum(1 for d in at(_R5, "llm_classifier_call_details")
           if d.get("warmup"))), (5, 5, 1))
check("5f  ISOLATION IS NOT SILENCE: the module counter moved by exactly one, "
      "keyed by the exception type",
      (sum(_after.values()) - sum(_before.values()),
       _after.get("RuntimeError", 0) - _before.get("RuntimeError", 0)), (1, 1))

# THE FLOOR. Every call failing is an outage, not fifteen unevaluable trials.
_R5g, _S5g = run_node(_FIVE, per_trial=True, parallel=4,
                      stub=_Stub(fail_for=[t["trial"]["nct_id"]
                                           for t in _FIVE]))
check("5g  when EVERY call fails the node returns the API-error result rather "
      "than a clean run of not-evaluable trials",
      (at(_R5g, "evaluations"), at(_R5g, "llm_classifier_retries"),
       bool(at(_R5g, "error"))), ([], 1, True))
check("5g  ...and the error names the mode and the count, so an operator is "
      "not sent looking at the judge",
      ("per-trial" in str(at(_R5g, "error")),
       "all 5" in str(at(_R5g, "error"))), (True, True))
check("5h  ...and it carries the provenance every failure return carries, so "
      "the row is not anonymous",
      (at(_R5g, "llm_classifier_prompt_version") is not None,
       isinstance(at(_R5g, "llm_classifier_prompt_sha256"), str),
       at(_R5g, "llm_classifier_output_ceiling")
       == config.MATCHING_MAX_TOKENS), (True, True, True))
check("5i  ...and the tokens it reports are the WARMUP's and only the "
      "warmup's: every trial call raised, so no trial produced a usage object "
      "and none is invented. One ledger row, marked warmup, no trials on it",
      (at(_R5g, "llm_classifier_calls"),
       [d.get("warmup") for d in at(_R5g, "llm_classifier_call_details")],
       [d.get("trials") for d in at(_R5g, "llm_classifier_call_details")],
       at(_R5g, "llm_classifier_input_tokens")), (1, [True], [0], 1000))
check("5i  ...which is the floor working through `calls_made` NOT being the "
      "test any more: a successful warmup makes calls_made non-zero, and a "
      "floor that asked it would have reported this outage as a clean patient",
      (at(_R5g, "llm_classifier_calls") > 0, bool(at(_R5g, "error"))),
      (True, True))

# GROUPED MODE IS UNCHANGED: one raised call is still the whole patient.
_R5j, _S5j = run_node(_FIVE, per_trial=False,
                      stub=_Stub(fail_for=[t["trial"]["nct_id"]
                                           for t in _FIVE]))
check("5j  grouped mode still fails the patient on a raised call -- the "
      "isolation is confined to the mode that can isolate",
      (at(_R5j, "evaluations"), at(_R5j, "llm_classifier_retries"),
       bool(at(_R5j, "error"))), ([], 1, True))

# NO TRIAL CALL IS SPECIAL ANY MORE, WHICH IS THE POINT OF THE CHANGE. Under
# the retired schedule the FIRST trial call was also the cache writer, so its
# failure had a second meaning; now it is one trial among N and this is the
# check that says so.
_R5k, _S5k = run_node(_FIVE, per_trial=True, parallel=4,
                      stub=_Stub(fail_for=[_FIVE[0]["trial"]["nct_id"]]))
check("5k  a failed FIRST trial call does not stop the rest: the remaining "
      "four are still issued and evaluated, and only the first is lost",
      (len(_S5k.wave_requests()), at(_R5k, "llm_classifier_calls"),
       {e.get("nct_id"): e.get("not_evaluable_reason")
        for e in at(_R5k, "evaluations")
        if e.get("not_evaluable_reason")}),
      (5, 5, {_FIVE[0]["trial"]["nct_id"]: NOT_EVALUABLE_CALL_FAILED}))
check("5k  ...and the cache was written by the warmup rather than by that "
      "call, so the other four did NOT go out against a cold prefix -- which "
      "is the cost leak the retired schedule had and this one does not",
      (len(_S5k.warmup_requests()),
       all(_S5k.exit_ticket(0) < _S5k.enter_ticket(n)
           for n in _S5k.wave_call_nos())), (1, True))


# ===========================================================================
# SECTION 5b -- A CALL THAT WAS PAID FOR IS IN THE RECORD, READ OR NOT
# ===========================================================================
#
# THE DEFECT PER-TRIAL DISPATCH INTRODUCES, AND THE ONE THIS PASS FOUND IN ITS
# OWN WORK. In grouped mode a refusal or an unparseable answer on chunk k ends
# the node with chunks k+1..N never SENT, so `_billed_so_far()` is exact. Per-
# trial mode issues every call before the loop begins, so those same two
# returns abandon N-1 responses that have already been paid for -- and without
# `_account_unconsumed()` the record would carry only the first one. That is
# the "a token figure no provider produced" shape this file's own module
# removed from four failure returns once already, arriving from the other
# direction: not a false zero, a false TOTAL.

section("SECTION 5b -- abandoned prefetched calls are still billed, and "
        "still recorded")

_FIRST_ID = _FIVE[0]["trial"]["nct_id"]

_R5b, _S5b = run_node(_FIVE, per_trial=True, parallel=4,
                      stub=_Stub(refuse_for=[_FIRST_ID], cached=300))
check("5b(a) a refusal on the FIRST trial still ends the node -- the premise "
      "it declined is the system message, which every call of this patient "
      "shares",
      (at(_R5b, "evaluations"), bool(at(_R5b, "llm_classifier_refusal"))),
      ([], True))
check("5b(b) ...and all five trial requests were issued behind the warmup, "
      "because per-trial mode dispatches before it reads",
      (len(_S5b.wave_requests()), len(_S5b.warmup_requests())), (5, 1))
check("5b(c) ...and ALL SIX are in the ledger: the warmup, one trial call "
      "read, four folded in as unconsumed, none dropped",
      (len(at(_R5b, "llm_classifier_call_details")),
       at(_R5b, "llm_classifier_calls")), (6, 6))
check("5b(d) ...with the token total covering every one of them, warmup "
      "included, not a prefix",
      (at(_R5b, "llm_classifier_input_tokens"),
       at(_R5b, "llm_classifier_output_tokens")), (6000, 600))
check("5b(e) ...the four folded rows say so, and neither the one that was "
      "read nor the warmup does -- 'the node stopped before reading this', "
      "'this response was unusable' and 'this was never a trial call' are "
      "three different facts",
      (sorted(bool(c.get("unconsumed"))
              for c in at(_R5b, "llm_classifier_call_details")),
       [bool(c.get("unconsumed"))
        for c in at(_R5b, "llm_classifier_call_details")
        if c.get("warmup")]),
      ([False, False, True, True, True, True], [False]))
check("5b(f) ...the ledger is still numbered 1..N with no gaps",
      [c.get("call_index")
       for c in at(_R5b, "llm_classifier_call_details")], [1, 2, 3, 4, 5, 6])
check("5b(g) ...and none of the folded rows claims to have emitted entries",
      sorted({c.get("entries_emitted")
              for c in at(_R5b, "llm_classifier_call_details")
              if c.get("unconsumed")}), [None])

# ── THE CACHED FIGURE ON A FAILURE RETURN: LEDGER YES, TOTAL NO ────────────
#
# THE COLUMN'S OWN CONTRACT, pinned here because this is the only arm that can
# state it. oncotriage/storage/database_logger.py documents
# llm_classifier_cached_input_tokens as NULL for "a run that ended at a failure
# return -- the totals are not carried out of those, the per-call ledger is,
# and it is where a failed run's cache reading lives". `_billed_so_far()`
# carries input, output and calls and deliberately NOT this, so the reading
# survives per call and nowhere else.
#
# A CONSEQUENCE WORTH KNOWING, and it is why a revert that stopped
# `_account_unconsumed` accumulating this figure was MEASURED to change nothing
# at all: those writes reach no return of this node. They are the run's true
# state and would become observable the day `_billed_so_far()` carries the
# field; today they are unobservable, and the ledger below is the whole record.
# THE KEY'S ABSENCE IS THE ASSERTION, tested on the dict rather than through
# `at()`: a _Absent compares on its MESSAGE, so an equality against one would
# pin an exception string rather than the contract.
check("5b(g2) the failure return carries NO cached TOTAL, which is the "
      "column's documented reading for a run that did not finish -- a number "
      "here would be a partial sum presented as the run's. NULL is what "
      "_pipeline_provenance() then stores",
      ("llm_classifier_cached_input_tokens" in _R5b,
       "llm_classifier_input_tokens" in _R5b), (False, True))
check("5b(g2) ...while the LEDGER carries every one of the six readings, "
      "abandoned rows included: the record of what was paid for survives the "
      "abandonment even though the total does not",
      [c.get("cached_tokens")
       for c in at(_R5b, "llm_classifier_call_details")], [300] * 6)
# `sorted` WITH A TOTAL KEY. A defect that drops the figure leaves this list
# full of Nones, and `sorted` over them raises -- an abort on exactly the input
# the check exists to reject. Measured: the plant that empties this field
# aborted the file before the key was added.
check("5b(g2) ...and the four FOLDED rows are among them, which is what says "
      "`_account_unconsumed` records the figure rather than dropping it",
      sorted((c.get("cached_tokens")
              for c in at(_R5b, "llm_classifier_call_details")
              if c.get("unconsumed")),
             key=lambda v: (v is None, v or 0)), [300] * 4)

# The identical property on the OTHER return that abandons a paid queue.
_R5h, _S5h = run_node(_FIVE, per_trial=True, parallel=4,
                      stub=_Stub(bad_json_for=[_FIRST_ID]))
check("5b(h) the same holds for an unparseable answer, which is the second "
      "return that abandons a queue that was already sent",
      (len(at(_R5h, "llm_classifier_call_details")),
       at(_R5h, "llm_classifier_input_tokens"),
       bool(at(_R5h, "error"))), (6, 6000, True))

# DETERMINISM. The fold happens on the node thread in nct_id order, so two runs
# that abandon the same set produce the identical tail.
_R5i, _ = run_node(_FIVE, per_trial=True, parallel=4,
                   stub=_Stub(refuse_for=[_FIRST_ID], cached=300))
check("5b(i) two runs abandoning the same set fold it identically",
      json.dumps(at(_R5b, "llm_classifier_call_details"), sort_keys=True),
      json.dumps(at(_R5i, "llm_classifier_call_details"), sort_keys=True))

# GROUPED MODE IS UNTOUCHED: nothing is prefetched, so there is nothing to
# fold, and no row is marked.
_R5j2, _S5j2 = run_node(_FIVE, per_trial=False,
                        stub=_Stub(refuse_for=[_FIRST_ID]))
check("5b(j) grouped mode folds nothing and marks nothing -- its queue was "
      "genuinely unissued",
      (len(at(_R5j2, "llm_classifier_call_details")),
       any(c.get("unconsumed")
           for c in at(_R5j2, "llm_classifier_call_details"))), (1, False))

# ABANDONED FAILURES ARE COUNTED APART. A call that RAISED produced no usage
# object, so it contributes no tokens; it is still a request that was made.
_before5b = dict(PER_TRIAL_CALL_FAILURES)
_R5k2, _ = run_node(_FIVE, per_trial=True, parallel=4,
                    stub=_Stub(refuse_for=[_FIRST_ID],
                               fail_for=[_FIVE[4]["trial"]["nct_id"]]))
_after5b = dict(PER_TRIAL_CALL_FAILURES)
check("5b(k) an abandoned call that RAISED is counted under its own key and "
      "contributes no tokens -- there was no usage object to fold. 5000 is "
      "the warmup plus the three abandoned answers plus the refusal itself",
      ({k: _after5b.get(k, 0) - _before5b.get(k, 0)
        for k in _after5b if _after5b.get(k, 0) != _before5b.get(k, 0)},
       at(_R5k2, "llm_classifier_input_tokens")),
      ({"abandoned:RuntimeError": 1}, 5000))


# ===========================================================================
# SECTION 5c -- THE ANSWERING-MODEL CHECK REACHES THE UNCONSUMED PATH
# ===========================================================================
#
# THE DEFECT. `MatchingModelMismatchError` exists so that a campaign cannot be
# run half on one judge and half on another: the model that ANSWERS is compared
# against the one that was requested, at the first response that disagrees,
# before any verdict from it reaches a result dict. There are THREE places a
# response's `model` field is folded into `model_answered` -- the warmup, the
# send loop, and `_account_unconsumed` -- and only the first two checked it.
#
# WHY THE THIRD MATTERED, WHICH IS NOT OBVIOUS FROM ITS NAME. Its own docstring
# used to argue that repeating the check would be wrong: this runs on a path
# that is already failing and already has a diagnosis. True of the DIAGNOSIS,
# and it overlooked what the function WRITES. `model_answered` is returned as
# `matching_model` by all four of the failure returns that call this, and
# `log_inference` STORES it and `get_model_cost` PRICES it -- so an unchecked
# echo folded here became the stored identity of the judge, on exactly the rows
# a reviewer reads when something went wrong.
#
# WHY RAISING IS THE RIGHT PRECEDENCE. The failures that call this are
# RECOVERABLE: a refusal, a parse failure and a non-list body all return to
# `route_after_llm_classifier`, which re-enters the node up to
# MAX_LLM_CLASSIFIER_RETRIES times. A model mismatch is NOT -- every retry after
# it spends more money on a judge nobody chose. Replacing a retryable diagnosis
# with the terminal one is an upgrade in severity, and the original diagnosis is
# not destroyed: three of the four callers invoke this from inside an `except`,
# so the refusal or the JSONDecodeError travels as `__context__`.

section("SECTION 5c -- a mismatched judge on an UNCONSUMED response")

_5c_TRIALS = [trial(i) for i in range(4)]
_5c_FIRST = _5c_TRIALS[0]["trial"]["nct_id"]
_5c_LAST = _5c_TRIALS[3]["trial"]["nct_id"]
_5c_WRONG = "gpt-4o-2024-08-06"

check("5c(a) the wrong model is genuinely a different string from the one "
      "requested (non-degeneracy)",
      _5c_WRONG == config.matching_wire_model(), False)

# THE FIRST TRIAL REFUSES, so the send loop ends the node at chunk 0 and chunks
# 1..3 are left in `_prefetched`, already paid for and unread. The LAST of them
# carries the wrong model -- a response the send loop provably never reads.
_R5c, _S5c = run_node(_5c_TRIALS, per_trial=True, parallel=4,
                      stub=_Stub(refuse_for=[_5c_FIRST],
                                 model_for={_5c_LAST: _5c_WRONG}))

check("5c(b) the node raises MatchingModelMismatchError from the unconsumed "
      "fold", isinstance(_R5c, _Absent)
      and "MatchingModelMismatchError" in str(_R5c), True)
check("5c(c) ...and the message names BOTH strings, so the decision can be "
      "made from the traceback alone",
      (config.matching_wire_model() in str(_R5c), _5c_WRONG in str(_R5c)),
      (True, True))

# THE RESPONSE REALLY WAS UNCONSUMED. Without this, 5c(b) would also pass if the
# send loop had somehow read the mismatched call itself -- which is the case the
# OTHER two fold sites already cover, and would leave this section testing them.
check("5c(d) the mismatched trial's request was issued (so its response was "
      "paid for)",
      _5c_LAST in {i for ids in _S5c.ids_by_call() for i in ids}, True)
check("5c(e) ...and the refusal was on a DIFFERENT, earlier trial, so the send "
      "loop returned before reaching it",
      _5c_FIRST != _5c_LAST, True)

# THE ORIGINAL DIAGNOSIS SURVIVES AS __context__. This is the concrete answer to
# the paragraph the fix replaced: the named failure is not lost, it is the
# second exception in the same report.
# `run_node` goes through `drive()`, which converts a raise into a value -- so
# the exception OBJECT is not reachable through it. This one scenario calls the
# node directly, and does its own override bookkeeping in a `finally`, because
# what it needs to read is `__context__` and only the live exception has it.
def _raise_through(trials, stub):
    """Run the node with no `drive()` in the way. Returns the exception."""
    saved = (config.MATCHING_PER_TRIAL_CALLS_ENABLED,
             config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS)
    deps.set_override(deps.OPENAI_CLIENT, stub)
    try:
        config.MATCHING_PER_TRIAL_CALLS_ENABLED = True
        config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS = 4
        node_llm_classifier_evaluation({
            "patient_data": PATIENT, "filtered_trials": trials,
            "llm_classifier_retries": 0, "mesh_filter_applied": True,
            "mesh_filter_skip_reason": "applied", "stage_timings": {},
        })
        return None
    except BaseException as exc:                               # noqa: BLE001
        return exc
    finally:
        (config.MATCHING_PER_TRIAL_CALLS_ENABLED,
         config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS) = saved
        deps.clear_override(deps.OPENAI_CLIENT)


_5c_exc = _raise_through(_5c_TRIALS,
                         _Stub(refuse_for=[_5c_FIRST],
                               model_for={_5c_LAST: _5c_WRONG}))
check("5c(f) the raise really is MatchingModelMismatchError",
      type(_5c_exc).__name__, MatchingModelMismatchError.__name__)

# WHICH CALLERS CHAIN, MEASURED IN BOTH DIRECTIONS.
#
# THE FIRST VERSION OF THIS CHECK ASSERTED THAT THE REFUSAL PATH CHAINS, ON A
# DOCSTRING THAT SAID "three of the four callers invoke this from inside an
# `except`". BOTH WERE WRONG, and the test is what found it: the count is TWO of
# four, and the refusal is not one of them. `_account_unconsumed` is called from
# an `except` on the API-error and JSON-parse paths, and from an ordinary `if`
# on the refusal and non-list paths -- where the response was well formed, no
# exception is live, and there is nothing to chain.
#
# BOTH ARMS ARE ASSERTED so the asymmetry is a measurement rather than a claim,
# and so that a future edit which moved a branch into or out of a handler fails
# here instead of quietly changing what an operator sees in a traceback.
check("5c(g) the refusal path does NOT chain -- it is an ordinary `if` over a "
      "well-formed response, so the mismatch is the only exception",
      _5c_exc is not None and _5c_exc.__context__ is None, True)

_5c_exc_parse = _raise_through(_5c_TRIALS,
                               _Stub(bad_json_for=[_5c_FIRST],
                                     model_for={_5c_LAST: _5c_WRONG}))
check("5c(h) the JSON-parse path raises the mismatch too",
      type(_5c_exc_parse).__name__, MatchingModelMismatchError.__name__)
check("5c(i) ...and THAT one chains: it is called from inside an `except`, so "
      "the parse failure travels as __context__ and the traceback prints both",
      (_5c_exc_parse is not None
       and type(_5c_exc_parse.__context__).__name__), "JSONDecodeError")

# AND THE COUNT ITSELF, READ OFF THE SHIPPED SOURCE. Without this, 5c(g)/(i)
# describe two paths and say nothing about the other two.
def _unconsumed_call_sites(tree):
    sites = {n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_account_unconsumed"}
    handled = {n.lineno for h in ast.walk(tree)
               if isinstance(h, ast.ExceptHandler)
               for n in ast.walk(h)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "_account_unconsumed"}
    return len(sites), len(handled)


_5c_total, _5c_handled = _unconsumed_call_sites(ast.parse(_EVAL_SRC))
# FIVE, NOT FOUR, SINCE THE CACHE-CONFIRMATION PASS -- and the SECOND number
# is the one this check is really about and it did not move. The fifth site is
# the zero-success FLOOR, which folds a fallback writer that succeeded, was
# billed, and was then withheld from the send loop because its cache write
# could not be confirmed; it is an ordinary statement rather than a handler, so
# "exactly two chain their original diagnosis" is as true as it was. The count
# is pinned EXACTLY rather than as a floor: a sixth site appearing without an
# argument in the docstring is precisely what this is for.
check("5c(j) _account_unconsumed has five call sites, and exactly two of them "
      "are inside an `except` -- the numbers the docstring states",
      (_5c_total, _5c_handled), (5, 2))

# --- AND THE ORDINARY PATH IS UNMOVED --------------------------------------
#
# NON-DEGENERACY FOR THE WHOLE SECTION. Every assertion above is satisfied by a
# node that raises on EVERY per-trial run. The identical scenario with the model
# left correct must complete, so the raise is about the mismatch and not about
# the shape of the run.
_R5c_ok, _S5c_ok = run_node(_5c_TRIALS, per_trial=True, parallel=4,
                            stub=_Stub(refuse_for=[_5c_FIRST]))
check("5c(k) with every echo correct, the SAME scenario returns the refusal "
      "return it always did (non-degeneracy)",
      (isinstance(_R5c_ok, _Absent), bool(at(_R5c_ok, "llm_classifier_refusal"))),
      (False, True))
check("5c(l) ...and its ledger still carries every call that was billed",
      len(at(_R5c_ok, "llm_classifier_call_details")), 5)

# --- THE STRUCTURAL HALF ---------------------------------------------------
#
# A DRIVEN CHECK CANNOT SEE A FOURTH FOLD SITE ADDED TOMORROW. This walks the
# shipped source and requires every assignment to `model_answered` to be guarded
# by a comparison against `config.matching_wire_model()` in the same function.

def _model_answered_writers(tree):
    """Functions that assign `model_answered`, and whether each checks first."""
    out = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        writes = [n for n in ast.walk(fn)
                  if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == "model_answered"
                          for t in n.targets)]
        if not writes:
            continue
        raises = any(
            isinstance(n, ast.Raise) and isinstance(n.exc, ast.Call)
            and isinstance(n.exc.func, ast.Name)
            and n.exc.func.id == "MatchingModelMismatchError"
            for n in ast.walk(fn))
        out[fn.name] = raises
    return out


_5c_writers = _model_answered_writers(ast.parse(_EVAL_SRC))
check("5c(m) every function that assigns model_answered also raises "
      "MatchingModelMismatchError",
      sorted(n for n, ok in _5c_writers.items() if not ok), [])
check("5c(n) ...and there are three of them, so the walk is not empty "
      "(non-degeneracy)", len(_5c_writers) >= 3, True)
check("5c(o) ...including _account_unconsumed, which is the one that did not",
      "_account_unconsumed" in _5c_writers, True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 6 -- OUT-OF-SET SEMANTICS AGAINST SINGLETON CHUNK IDS
# ===========================================================================
#
# With one trial per call the sent set of every call is a single id, so the
# out-of-set detector is exercised on every response rather than only on a
# split run. The distinction it draws is what matters here: an entry naming
# ANOTHER candidate of this run is a cross-chunk repeat -- dropped, counted
# apart, and costing the patient nothing because that id's own call answers it
# -- while an invented id is a fabrication and reaches hallucinated_trials.

section("SECTION 6 -- cross-chunk versus fabricated, at chunk size one")

_A, _B = _FIVE[0]["trial"]["nct_id"], _FIVE[1]["trial"]["nct_id"]

# Trial A's call answers for A and for B. B is real, and B's own call answers
# it too, so nothing is lost.
_R6, _S6 = run_node(_FIVE, per_trial=True, parallel=4,
                    stub=_Stub(answer={_A: _eligible_body([_A, _B])}))
check("6a  an entry for another REAL candidate is dropped as cross-chunk and "
      "does NOT reach hallucinated_trials",
      at(_R6, "hallucinated_trials"), 0)
check("6b  ...and nothing is lost: that trial's own call answered it, so all "
      "five have exactly one verdict",
      sorted(e.get("nct_id") for e in at(_R6, "evaluations")),
      sorted(t["trial"]["nct_id"] for t in _FIVE))
check("6c  ...and B's verdict is attributed to B's OWN call, not to A's. "
      "Call 1 is the warmup, so A is 2 and B is 3",
      {e.get("nct_id"): e.get("call_index")
       for e in at(_R6, "evaluations")}.get(_B), _trial_call_index(1))

# An id that is in no sent set of this run at all.
_R6d, _S6d = run_node(_FIVE, per_trial=True, parallel=4,
                      stub=_Stub(answer={_A: _eligible_body([_A,
                                                             "NCT99999999"])}))
check("6d  an INVENTED id is a fabrication and is counted as one",
      at(_R6d, "hallucinated_trials"), 1)
check("6d  ...and it reaches no verdict, while the five real trials all do",
      sorted(e.get("nct_id") for e in at(_R6d, "evaluations")),
      sorted(t["trial"]["nct_id"] for t in _FIVE))

# A call that answers for a DIFFERENT trial and not its own leaves its own
# trial to the reconciliation -- with the omission reason, which is correct
# here because a response WAS obtained and simply did not mention it.
_R6e, _S6e = run_node(_FIVE, per_trial=True, parallel=4,
                      stub=_Stub(answer={_A: _eligible_body([_B])}))
check("6e  a call that answered only for someone else leaves its own trial to "
      "the reconciliation, under the OMISSION reason -- which is the right "
      "one here: a response arrived and did not name it",
      {e.get("nct_id"): e.get("not_evaluable_reason")
       for e in at(_R6e, "evaluations")
       if e.get("not_evaluable_reason")},
      {_A: NOT_EVALUABLE_MODEL_OMITTED})


# ===========================================================================
# SECTION 7 -- THE CACHE IS MEASURED, NOT ASSUMED
# ===========================================================================

section("SECTION 7 -- the provider's cached-token report, per call")

# THE SHAPE A WORKING WARMUP PRODUCES, which is the whole point of the design
# and is therefore what the stub simulates here: the warmup pays full price for
# the prefix (cached 0, because nothing had written it) and every trial call
# behind it is served from cache. If the accounting path for cached tokens were
# only PRESENT rather than exercised, this is the run it would be wrong about.
_R7, _S7 = run_node(_FIVE, per_trial=True, parallel=4,
                    stub=_Stub(cached=700, warmup_cached=0))
_rows7 = at(_R7, "llm_classifier_call_details")
check("7a  every call records the provider's own cached figure: the warmup "
      "reports 0 because it wrote the prefix, and every trial call behind it "
      "reports the cached figure the provider served",
      ([c.get("cached_tokens") for c in _rows7 if c.get("warmup")],
       [c.get("cached_tokens") for c in _rows7 if not c.get("warmup")]),
      ([0], [700] * 5))
check("7b  ...and the patient-level total is the WAVE's: 700 from each of "
      "five trial calls, with the warmup's own figure excluded",
      at(_R7, "llm_classifier_cached_input_tokens"), 3500)
check("7b  ...non-degeneracy: the warmup's 0 is a MEASURED zero and not an "
      "absence -- it is present in the ledger and simply not summed, which is "
      "what makes 7c's NULL a decision rather than a missing reading",
      ([c.get("cached_tokens") for c in _rows7 if c.get("warmup")] == [0],
       len(_rows7)), (True, 6))
check("7b  ...and the total is EXACTLY the wave's sum, so the warmup is "
      "excluded by arithmetic rather than by having happened to be zero",
      (at(_R7, "llm_classifier_cached_input_tokens"),
       sum(c["cached_tokens"] for c in _rows7 if not c.get("warmup"))),
      (3500, 3500))

# THE PATHOLOGY THE PER-CALL FIGURES EXIST TO EXPOSE: a warmup that reported a
# figure while no trial call behind it did. That is "the warmup warmed a prefix
# the wave does not share" -- the one way this design fails silently -- and a
# summed total cannot separate it from a cache that warmed normally.
_R7c, _S7c = run_node(_FIVE, per_trial=True, parallel=4,
                      stub=_Stub(cached=700, cached_first_only=True))
check("7c  a cache that reports on the WARMUP only is visible per call, which "
      "is exactly what a summed total cannot show -- and is the reading that "
      "would say the warmup is warming the wrong prefix",
      sorted((c.get("cached_tokens")
              for c in at(_R7c, "llm_classifier_call_details")),
             key=lambda v: (v is not None, v or 0)),
      [None, None, None, None, None, 700])
# THE DEFECT THIS EXCLUSION EXISTS FOR, driven end to end. The warmup reports a
# figure and the WAVE says nothing at all. Folding the warmup in made the column
# non-NULL, which is the column asserting "responses of this run reported the
# field" about a wave that reported nothing -- and it is the same value a wave
# that HAD reported would produce, so the two readings the column exists to
# separate collapsed into one.
check("7c  ...and the patient column stays NULL, because NO WAVE CALL "
      "reported the field. A number here would be the warmup's, presented as "
      "the run's, and would say the provider answered about caching when it "
      "did not",
      at(_R7c, "llm_classifier_cached_input_tokens"), None)
check("7c  ...non-degeneracy: the warmup DID report, so this NULL is an "
      "exclusion and not an absence of any reading anywhere",
      [c.get("cached_tokens")
       for c in at(_R7c, "llm_classifier_call_details") if c.get("warmup")],
      [700])

# THE OTHER HALF, and without it 7c would also pass against a node that had
# stopped accumulating cached tokens altogether: a warmup reporting 0 (the
# healthy shape) beside a wave that DOES report must give the wave's sum, not
# NULL and not the warmup's zero.
_R7c2, _ = run_node(_FIVE, per_trial=True, parallel=4,
                    stub=_Stub(cached=300, warmup_cached=0))
check("7c  ...while a warmup reporting 0 beside a wave that DOES report gives "
      "the wave's sum -- so the exclusion removes the warmup's reading and "
      "not the accumulator",
      (at(_R7c2, "llm_classifier_cached_input_tokens"),
       [c.get("cached_tokens")
        for c in at(_R7c2, "llm_classifier_call_details")
        if c.get("warmup")]), (1500, [0]))

_R7d, _S7d = run_node(_FIVE, per_trial=True, parallel=4, stub=_Stub())
check("7d  a provider that reports NO cached figure is carried as absent, "
      "never as zero -- a stub, an old recording and a provider that caches "
      "nothing are three different facts",
      ([c.get("cached_tokens")
        for c in at(_R7d, "llm_classifier_call_details")],
       at(_R7d, "llm_classifier_cached_input_tokens")),
      ([None] * 6, None))
check("7e  ...and a genuine ZERO is kept as a zero, which is the reading that "
      "says the provider cached nothing",
      at(run_node(_FIVE, per_trial=True, stub=_Stub(cached=0))[0],
         "llm_classifier_cached_input_tokens"), 0)

# THE ACCOUNTING PATH IS EXERCISED, NOT MERELY PRESENT. The two figures below
# come out of DIFFERENT code paths in the node -- the wave's rows are written
# by the send loop, the warmup's by `_account_warmup` on the node thread -- and
# a defect in either would leave the other intact, so they are asserted apart.
check("7f  the wave's cached figures are the stub's own simulated marker, "
      "per call, on every trial call and on no other row",
      ([c.get("cached_tokens") for c in _rows7 if not c.get("warmup")],
       sum(c.get("cached_tokens") or 0 for c in _rows7
           if not c.get("warmup"))), ([700] * 5, 3500))
check("7f  ...and a run whose wave reports a DIFFERENT figure moves the "
      "column with it, so 7b is a measurement rather than a constant",
      at(run_node(_FIVE, per_trial=True, parallel=4,
                  stub=_Stub(cached=100, warmup_cached=0))[0],
         "llm_classifier_cached_input_tokens"), 500)
# THE WARMUP'S FIGURE MOVES AND THE COLUMN DOES NOT. Every check above holds a
# warmup at 0, where "excluded" and "added" are indistinguishable. This is the
# one reading that separates them.
_R7g, _ = run_node(_FIVE, per_trial=True, parallel=4,
                   stub=_Stub(cached=100, warmup_cached=9999))
check("7g  a warmup reporting a LARGE figure changes the column by nothing: "
      "it is the wave's 5 x 100 and not 10,499. Without this, every check "
      "above is satisfied by a node that still folds the warmup in, because "
      "each of them holds the warmup at zero",
      (at(_R7g, "llm_classifier_cached_input_tokens"),
       [c.get("cached_tokens")
        for c in at(_R7g, "llm_classifier_call_details") if c.get("warmup")]),
      (500, [9999]))
check("7g  ...and the ledger is the place that figure survives, untouched -- "
      "the sum of every ROW still includes it, so nothing was discarded",
      sum(c.get("cached_tokens") or 0
          for c in at(_R7g, "llm_classifier_call_details")), 10499)

# THE FALLBACK'S CACHE WRITER IS A TRIAL CALL AND ITS FIGURE COUNTS. When the
# provider refuses the DEDICATED warmup's shape the node degrades to the
# retired one-then-rest schedule, where the prefix is written by the first
# TRIAL call -- there is no warmup row at all, so every reading of that run is
# the wave's and the exclusion has nothing to remove. An exclusion keyed on
# "the first call" rather than on the warmup would silently drop a real trial's
# figure here, which is why this arm is asserted rather than assumed.
_R7i, _S7i = run_node(
    _FIVE, per_trial=True, parallel=4,
    stub=_Stub(cached=200, warmup_raise=_WarmupRefused(
        "Unrecognized request argument supplied: prompt_cache_key")))
check("7i  on the fallback schedule there is no warmup row, and the column is "
      "the sum over all five trial calls -- the writer among them included, "
      "because it IS a trial call",
      (at(_R7i, "llm_classifier_cached_input_tokens"),
       [c.get("warmup") for c in at(_R7i, "llm_classifier_call_details")],
       len(_S7i.wave_requests())), (1000, [None] * 5, 5))

# GROUPED MODE IS UNTOUCHED. There is no warmup on that arm, so the exclusion
# must be invisible there -- and a change that removed the accumulator instead
# of the warmup's contribution would show up here and nowhere else.
_R7h, _S7h = run_node(_FIVE, per_trial=False, stub=_Stub(cached=440))
check("7h  grouped mode is unchanged: no warmup exists on that arm and the "
      "single batched call's cached figure is the column",
      (at(_R7h, "llm_classifier_cached_input_tokens"), len(_S7h.requests),
       [c.get("warmup") for c in at(_R7h, "llm_classifier_call_details")]),
      (440, 1, [None]))
check("7h  ...and a grouped run whose provider reports nothing is still NULL, "
      "never 0",
      at(run_node(_FIVE, per_trial=False, stub=_Stub())[0],
         "llm_classifier_cached_input_tokens"), None)


# ===========================================================================
# SECTION 8 -- THE RETAINED GROUPED ARM, AS BYTES
# ===========================================================================
#
# THIS IS AN EXPLICIT-OFF SECTION AND ALWAYS WAS: every drive below passes
# `per_trial=False` to `run_node`, so nothing here reads the shipped default
# and the flip did not move a single assertion in it. What the flip changes is
# what the section is FOR. It was "the off switch is exact"; it is now "the
# comparison arm is exact", which is a stronger reason to keep it -- every
# migration number is grouped-against-per-trial, and a grouped arm that had
# drifted would make the comparison a measurement of the drift.
#
# THE REFERENCE IS A COPY OF THE MODULE WITH THE WHOLE MECHANISM COMPILED OUT,
# not a description of what it used to do. Two substitutions, each asserted to
# have applied exactly once:
#
#   * `_per_trial_calls` is forced False, so every branch this pass added is
#     dead -- the partition, the dispatch, the isolation and the floor.
#   * the send loop's `_obtain(chunk)` is put back to the inline
#     `call_matching_model(system_prompt, _user_prompt_for(chunk))` it replaced,
#     so the comparison covers the indirection as well as the branches.
#
# Without the second, section 8 would prove the branches inert and say nothing
# about whether `_obtain` issues the same request the loop used to.

section("SECTION 8 -- the grouped arm is byte-equivalent to the pre-pass node")

_NEEDLE_FLAG = """    _per_trial_calls = (config.matching_call_mode()
                        == MATCHING_CALL_MODE_PER_TRIAL)"""
_NEEDLE_OBTAIN = "            response = _obtain(chunk)"

check("8a  both needles are present in the shipped source exactly once (a "
      "needle that matched nothing would make this whole section vacuous)",
      (_EVAL_SRC.count(_NEEDLE_FLAG), _EVAL_SRC.count(_NEEDLE_OBTAIN)), (1, 1))

_PRE_SRC = _EVAL_SRC.replace(_NEEDLE_FLAG, "    _per_trial_calls = False", 1)
_PRE_SRC = _PRE_SRC.replace(
    _NEEDLE_OBTAIN,
    "            response = call_matching_model("
    "system_prompt, _user_prompt_for(chunk))", 1)
check("8a  ...and the pre-pass copy differs from the shipped source",
      _PRE_SRC != _EVAL_SRC, True)

_pre = module_from(_PRE_SRC, "oncotriage.agent._pre_per_trial_copy")


def comparable(requests):
    """Everything about a request that decides what the model receives.

    `timeout` is excluded on the grounds oncotriage/agent/evaluation.py already
    gives for not recording it: it is client-side and cannot change the
    response. Everything else -- both messages, the model, the ceiling, the
    seed, the response format -- is compared.
    """
    return [{k: v for k, v in r.items() if k != "timeout"} for r in requests]


def request_json(requests):
    return json.dumps(comparable(requests), sort_keys=True, default=str)


_R8, _S8 = run_node(_SIX, per_trial=False)
_R8p, _S8p = run_node(_SIX, per_trial=False,
                      node=_pre.node_llm_classifier_evaluation)

check("8b  the same NUMBER of requests", len(_S8.requests), len(_S8p.requests))
check("8c  ...and the requests are IDENTICAL, field for field",
      request_json(_S8.requests), request_json(_S8p.requests))
check("8d  ...non-degeneracy: a real request was sent, carrying real trials",
      (len(_S8.requests),
       "<<<TRIAL_DATA" in "".join(r["messages"][1]["content"]
                                  for r in _S8.requests)), (1, True))
check("8e  ...and the stored prompt agrees byte for byte",
      at(_R8, "llm_classifier_prompt"), at(_R8p, "llm_classifier_prompt"))
check("8f  ...and the packing record agrees exactly, including the ABSENCE of "
      "bypassed_by -- adding a None there would have changed the stored JSON "
      "of every grouped-mode row for a fact those rows already state by "
      "omission",
      json.dumps(at(_R8, "llm_classifier_packing"), sort_keys=True,
                 default=str),
      json.dumps(at(_R8p, "llm_classifier_packing"), sort_keys=True,
                 default=str))
check("8g  ...and every other published key agrees, except the ones that "
      "cannot (timings are wall clock)",
      {k: v for k, v in _R8.items() if k != "stage_timings"}
      if isinstance(_R8, dict) else _Absent("no result"),
      {k: v for k, v in _R8p.items() if k != "stage_timings"}
      if isinstance(_R8p, dict) else _Absent("no result"))

# THE COMPARISON DISCRIMINATES. Without this, 8c would also pass against two
# arms that had both silently stopped sending anything.
_R8h, _S8h = run_node(_SIX, per_trial=True)
check("8h  the same comparison SEPARATES the on and off arms, so 8c is a "
      "measurement rather than a tautology",
      request_json(_S8h.requests) == request_json(_S8.requests), False)

# AND THE PACKING SWITCH IS UNTOUCHED BY THIS PASS: grouped mode with packing
# on and grouped mode with packing off both still behave as they did, which is
# what says the `elif` reordering moved no branch.
_saved_pack = _evaluation.MATCHING_INPUT_PACKING_ENABLED
try:
    _evaluation.MATCHING_INPUT_PACKING_ENABLED = False
    _R8i, _S8i = run_node(_SIX, per_trial=False)
    _pre.MATCHING_INPUT_PACKING_ENABLED = False
    _R8ip, _S8ip = run_node(_SIX, per_trial=False,
                            node=_pre.node_llm_classifier_evaluation)
finally:
    _evaluation.MATCHING_INPUT_PACKING_ENABLED = _saved_pack
check("8i  with PACKING off as well, the two arms still issue identical "
      "requests -- the `elif` this pass introduced did not move the branch it "
      "guards", request_json(_S8i.requests), request_json(_S8ip.requests))
check("8j  ...and both report the packer as not having run, with nothing "
      "bypassing it",
      ((at(_R8i, "llm_classifier_packing") or {}).get("enabled"),
       "bypassed_by" in (at(_R8i, "llm_classifier_packing") or {})),
      (False, False))
check("8k  ...and the packing switch was restored",
      _evaluation.MATCHING_INPUT_PACKING_ENABLED, _saved_pack)

# ── THE SEAM ITSELF, NOT ONLY THE NODE ────────────────────────────────────
#
# `call_matching_model` gained an optional routing hint, and the OFF arm's
# guarantee is that this changed NOTHING about the request it builds.
# `openai.NOT_GIVEN` would have been equivalent on the wire and is NOT what is
# used, because oncotriage/fixtures/capture.py records this call's kwargs DICT
# and oncotriage/fixtures/replay.py looks a recording up by a digest of it -- a
# key that is always present, whatever its value, would change that digest for
# every grouped-mode request and cost a re-capture of all twelve
# characterization fixtures at live model prices.
_S8l = _Stub()
deps.set_override(deps.OPENAI_CLIENT, _S8l)
try:
    drive(_evaluation.call_matching_model, "SYS", "USR")
    drive(_evaluation.call_matching_model, "SYS", "USR",
          prompt_cache_key="k-1")
finally:
    deps.clear_override(deps.OPENAI_CLIENT)
check("8l  with no routing hint the client is handed the SAME keyword set it "
      "always was, with no prompt_cache_key key present at all -- an empty "
      "expansion, not a sentinel value",
      ("prompt_cache_key" in _S8l.requests[0],
       sorted(_S8l.requests[0])),
      (False, sorted(["model", "messages", "max_completion_tokens",
                      "reasoning_effort", "seed", "response_format",
                      "timeout"])))
check("8m  ...and WITH one it is present and carries the value, so 8l is a "
      "measurement rather than a parameter nothing ever sends",
      (_S8l.requests[1].get("prompt_cache_key"),
       sorted(set(_S8l.requests[1]) - set(_S8l.requests[0]))),
      ("k-1", ["prompt_cache_key"]))


# ===========================================================================
# SECTION 8B -- THE SHUTDOWN FLAG AND THE BOUNDED DRAIN
# ===========================================================================
#
# WHAT IT IS FOR, AND WHY THE WAVE'S OWN `cancel_futures=True` IS NOT IT. In a
# batch run the Stage 5 node executes on a WORKER thread of
# `oncotriage/batch/runner.py`'s pool, and CPython delivers a signal only to
# the MAIN thread. So a real SIGTERM raises SystemExit where the main thread is
# -- inside `future.result()` -- and the wave's `finally`, which is where
# `cancel_futures=True` lives, is on a thread nothing has interrupted. Every
# in-flight patient then finishes its WHOLE wave: ceil(N / parallel) rounds of
# live billed requests, each bounded only by MATCHING_REQUEST_TIMEOUT_SECONDS
# and the SDK's own retries, while `shutdown(wait=True)` blocks. Against a
# `docker stop` grace period whose default is TEN SECONDS the orchestrator
# SIGKILLs partway through and the run leaves no crash record, no finalized row
# and a set of requests billed and abandoned mid-read.
#
# THE FLAG IS THE REAL FIX and this section is what says it works: `_issue`
# checks it before each queued call, the gate above the warmup makes a patient
# entered after the request send NOTHING at all, and -- the half that is not
# about money -- the patient FAILS rather than completing with a hole in it.
#
# WHY FAILING IS THE POINT. Every other exception the send loop meets in
# per-trial mode is ISOLATED to its trial: that trial is recorded not evaluable
# and the patient completes. Isolating a shutdown the same way would produce a
# patient recorded SUCCESS with four verdicts and eleven "not evaluable" -- and
# `_on_done` CHECKPOINTS a success, so a resume would skip that patient
# forever. The cohort would carry a silent hole shaped like whenever somebody
# pressed Ctrl-C. c30 plants exactly that.
#
# THE OPERATOR STOP SENTINEL DELIBERATELY DOES NOT SET THIS. See the flag's own
# note: STOP promises in-flight patients run to completion, and truncating them
# would both break that and COST MORE -- their paid round is discarded and the
# resume re-bills the whole patient.

section("SECTION 8B -- the shutdown flag bounds the drain, and fails the "
        "patient rather than half-completing it")


class _ShutdownStub(_Stub):
    """A stub that asks for a shutdown while the wave is in flight.

    ``after`` is how many WAVE calls are allowed through before the flag is
    set; the warmup never counts. The flag is set on ``module`` so a control
    copy sets its OWN global rather than the shipped module's.

    DETERMINISTIC BY CONSTRUCTION, NOT BY TIMING. Driven at ``parallel=1`` the
    wave's pool has one worker, so the calls are issued one at a time in
    dispatch order and "the flag was set during call k" is a fact about the
    sequence rather than about how fast this machine happens to be. No sleep,
    no barrier, no clock.
    """

    def __init__(self, *, module, after=1, reason="test shutdown", **kw):
        super().__init__(**kw)
        self._module = module
        self._after = after
        self._reason = reason
        self.set_at = None

    def create(self, **kwargs):
        if not is_warmup(kwargs):
            with self._lock:
                seen = sum(1 for r in self.requests if not is_warmup(r))
            if seen == self._after - 1 and self.set_at is None:
                self.set_at = seen + 1
                self._module.request_stage5_shutdown(self._reason)
        return super().create(**kwargs)


def _with_clean_flag(fn, module=_evaluation):
    """Run `fn`, then clear the flag on `module` whatever happened.

    THE `finally` IS LOAD-BEARING RATHER THAN TIDY: this flag is module-level
    and every later scenario in this file, and every other test file sharing
    the process under `pytest`, would see a run they never asked to stop.
    """
    try:
        return drive(fn)
    finally:
        module.clear_stage5_shutdown()


# ── the accessors, and the reset main() depends on ─────────────────────────

check("8b-a the flag ships CLEAR, so nothing above this line has left one set",
      (_evaluation.stage5_shutdown_requested(),
       _evaluation.stage5_shutdown_reason()),
      (False, None))
_evaluation.request_stage5_shutdown("first reason")
_evaluation.request_stage5_shutdown("second reason")
check("8b-b a request sets the flag and keeps the FIRST reason, so a second "
      "signal arriving during teardown cannot overwrite the diagnosis of the "
      "one that is being acted on",
      (_evaluation.stage5_shutdown_requested(),
       _evaluation.stage5_shutdown_reason()),
      (True, "first reason"))
_evaluation.clear_stage5_shutdown()
check("8b-c clear_stage5_shutdown() resets BOTH, which is what main() calls "
      "beside clear_write_ledger() so a second run in one process does not "
      "inherit the first one's stop",
      (_evaluation.stage5_shutdown_requested(),
       _evaluation.stage5_shutdown_reason()),
      (False, None))
# ── set BEFORE the node: not one request, not even the warmup ──────────────

_SKIPS_BEFORE = dict(_evaluation.STAGE5_SHUTDOWN_SKIPS)
_evaluation.request_stage5_shutdown("SIGTERM (signal 15)")
_R_PRE, _S_PRE = _with_clean_flag(
    lambda: run_node(_SIX, per_trial=True, parallel=4))
_SKIPS_PRE = {k: v - _SKIPS_BEFORE.get(k, 0)
              for k, v in _evaluation.STAGE5_SHUTDOWN_SKIPS.items()
              if v - _SKIPS_BEFORE.get(k, 0)}

check("8b-e a patient whose node is entered AFTER the shutdown was requested "
      "sends NOTHING -- not the wave, and not the warmup either. This is the "
      "largest single saving in the mechanism and a per-call check inside "
      "`_issue` cannot make it: the warmup is issued before any queued call "
      "exists",
      len(_S_PRE.requests), 0)
check("8b-f ...and the patient FAILS, so the batch checkpoint does not record "
      "it as done and a resume runs it whole",
      (bool(at(_R_PRE, "error")), at(_R_PRE, "evaluations")),
      (True, []))
check("8b-g ...with the floor's sentence saying a shutdown was requested and "
      "NOT that a warmup failed. An operator reading a row they produced by "
      "pressing Ctrl-C must not be sent looking for an endpoint fault",
      ("shutdown was requested" in str(at(_R_PRE, "error")),
       "could not be warmed" in str(at(_R_PRE, "error"))),
      (True, False))
check("8b-h the skip is counted under the `warmup:` phase, which is what "
      "separates 'we stopped before this patient' from 'we stopped in the "
      "middle of it'",
      (sorted(_SKIPS_PRE), sum(_SKIPS_PRE.values())),
      ([_evaluation.SHUTDOWN_SKIP_WARMUP_KEY_PREFIX + "SIGTERM (signal 15)"], 1))
check("8b-i ...and the counter is REGISTERED, so it reaches the run-end "
      "degradation block and `run_metrics` without a second wiring step",
      "STAGE5_SHUTDOWN_SKIPS" in _degradation.registered_names(), True)


# ── set DURING the wave: one in-flight round, and no partial success ───────

_SKIPS_BEFORE = dict(_evaluation.STAGE5_SHUTDOWN_SKIPS)
_FAIL_BEFORE = dict(_evaluation.PER_TRIAL_CALL_FAILURES)
_MID_STUB = _ShutdownStub(module=_evaluation, after=1,
                          reason="Ctrl-C during the main batch pass")
_R_MID, _ = _with_clean_flag(
    lambda: run_node(_SIX, per_trial=True, parallel=1, stub=_MID_STUB))
_SKIPS_MID = {k: v - _SKIPS_BEFORE.get(k, 0)
              for k, v in _evaluation.STAGE5_SHUTDOWN_SKIPS.items()
              if v - _SKIPS_BEFORE.get(k, 0)}
_FAIL_MID = {k: v - _FAIL_BEFORE.get(k, 0)
             for k, v in _evaluation.PER_TRIAL_CALL_FAILURES.items()
             if v - _FAIL_BEFORE.get(k, 0)}

check("8b-j the flag was set during the FIRST wave call (non-degeneracy: a "
      "scenario in which it was set before the wave began would measure "
      "8b-e over again and say nothing about the queued calls)",
      _MID_STUB.set_at, 1)
check("8b-k EXACTLY ONE WAVE REQUEST WAS ISSUED, out of six. The one already "
      "in flight cannot be interrupted; every queued one returns without "
      "being sent",
      (len([r for r in _MID_STUB.requests if not is_warmup(r)]),
       len(_MID_STUB.warmup_requests())),
      (1, 1))
check("8b-l the five that were not sent are counted under the `wave:` phase",
      (sorted(_SKIPS_MID),
       sum(_SKIPS_MID.values())),
      ([_evaluation.SHUTDOWN_SKIP_WAVE_KEY_PREFIX
        + "Ctrl-C during the main batch pass"], 5))
check("8b-m *** THE PATIENT FAILS RATHER THAN COMPLETING WITH A HOLE. *** "
      "Isolating a shutdown to its trial would publish a SUCCESS carrying one "
      "verdict and five not-evaluable entries -- and `_on_done` checkpoints a "
      "success, so a resume would skip that patient forever",
      (bool(at(_R_MID, "error")), at(_R_MID, "evaluations")),
      (True, []))
check("8b-n THE ROUND ALREADY PAID FOR IS STILL IN THE RECORD. The request "
      "that was in flight was answered and billed, and a failure return that "
      "omitted it would report a token total no provider produced -- the "
      "'false TOTAL' shape `_account_unconsumed` exists for, reached from a "
      "direction that used to be grouped-only",
      (at(_R_MID, "llm_classifier_calls"),
       at(_R_MID, "llm_classifier_input_tokens") > 0,
       len([c for c in at(_R_MID, "llm_classifier_call_details") or []
            if not c.get("warmup")])),
      (2, True, 1))
check("8b-o a request that was never issued is NOT counted as an abandoned "
      "one. `abandoned:` means paid for and not read, and it is what a "
      "transport-failure reader acts on; a declined call is neither, and is "
      "already counted once at the moment it was declined",
      [k for k in _FAIL_MID if k.startswith("abandoned:")], [])


# ── the closed vocabulary, and the limit this pass did NOT close ───────────

check("8b-p WARMUP_SOURCES is closed and holds exactly the three the floor "
      "branches on, so a fourth member added without a branch falls through "
      "to the `warmup` wording and is caught here",
      (_evaluation.WARMUP_SOURCES,
       len(set(_evaluation.WARMUP_SOURCES))),
      ((_evaluation.WARMUP_SOURCE_WARMUP,
        _evaluation.WARMUP_SOURCE_FALLBACK_WRITER,
        _evaluation.WARMUP_SOURCE_SHUTDOWN), 3))
check("8b-q ...and every member is named in the floor, by AST rather than by "
      "grep, so a branch deleted while the constant survives fails here",
      sorted({n.id for n in ast.walk(ast.parse(_EVAL_SRC))
              if isinstance(n, ast.Name) and n.id in
              {"WARMUP_SOURCE_WARMUP", "WARMUP_SOURCE_FALLBACK_WRITER",
               "WARMUP_SOURCE_SHUTDOWN"}}),
      ["WARMUP_SOURCE_FALLBACK_WRITER", "WARMUP_SOURCE_SHUTDOWN",
       "WARMUP_SOURCE_WARMUP"])

# ── GROUPED MODE IS GATED TOO (the operator-control pass) ──────────────────
#
# THIS CHECK USED TO PIN THE OPPOSITE, and the note it carried is kept here as
# the record of what changed rather than deleted. It read: "GROUPED MODE IS NOT
# GATED, and that is a stated limit rather than an oversight: its calls are
# issued sequentially from the node's own thread, and widening the gate would
# change the grouped send loop's failure mode."
#
# THE LIMIT WAS REAL AND IT COVERED THE ARM THAT SHIPPED AT THE TIME.
# MATCHING_PER_TRIAL_CALLS_ENABLED was False, so every batch run stopped by
# Ctrl-C or SIGTERM had its in-flight patients carry on issuing every REMAINING
# chunk of the packer's plan at full price. One chunk is the common case and it
# is not the bound: the proactive packer splits on the input budget and the
# reactive splitter halves on a `length` finish.
#
# GROUPED IS THE RETAINED COMPARISON ARM NOW, WHICH MAKES THIS CHECK MORE
# VALUABLE RATHER THAN LESS: a comparison arm whose stop semantics differed
# from the shipped arm's would put an artefact of the SHUTDOWN into a
# measurement of the PARTITION. The two arms answer a Ctrl-C identically, and
# that is what these five checks hold.
#
# THE FAILURE MODE DID CHANGE, WHICH IS WHAT THE OLD NOTE WARNED ABOUT, AND IT
# CHANGED TO THE ONE PER-TRIAL MODE ALREADY HAD. The raise is
# Stage5ShutdownRequested, the send loop's isolation branch is guarded by
# `_per_trial_calls` and therefore unreachable in grouped mode, so it falls
# straight through to the API-error return: the patient FAILS rather than
# publishing a partial success. That is the c33 lesson applied identically --
# `_on_done` checkpoints a success, so a partial patient would be skipped by
# every resume forever.
_SKIPS_BEFORE = dict(_evaluation.STAGE5_SHUTDOWN_SKIPS)
_evaluation.request_stage5_shutdown("SIGTERM (signal 15)")
_R_GRP, _S_GRP = _with_clean_flag(
    lambda: run_node(_SIX, per_trial=False))
_GRP_SKIPS = {k: v - _SKIPS_BEFORE.get(k, 0)
              for k, v in _evaluation.STAGE5_SHUTDOWN_SKIPS.items()
              if v - _SKIPS_BEFORE.get(k, 0)}
check("8b-r GROUPED MODE ISSUES NO REQUEST once a shutdown is asked for. The "
      "stub counts what actually reached a provider, so this is a statement "
      "about money and not about a code path",
      len(_S_GRP.requests), 0)
check("8b-s ...and the decline is COUNTED under its own `send:` phase, which "
      "is neither `warmup:` (the patient sent nothing at all) nor `wave:` (a "
      "per-trial worker declining an already-submitted task). An operator "
      "reading a stopped run's report should not have to know which arm was "
      "configured to read the number",
      (sorted(_GRP_SKIPS), sum(_GRP_SKIPS.values()) > 0),
      ([f"{_evaluation.SHUTDOWN_SKIP_SEND_KEY_PREFIX}SIGTERM (signal 15)"],
       True))
check("8b-t ...and the patient FAILS rather than publishing a partial "
      "success. A grouped patient that returned `evaluations` for the chunks "
      "it had already sent would be checkpointed by _on_done and skipped by "
      "every resume forever -- the c33 argument, reached from the RETAINED "
      "GROUPED arm",
      (bool(at(_R_GRP, "error")), at(_R_GRP, "evaluations")),
      (True, []))
check("8b-u the three phase prefixes are CLOSED and partition the places a "
      "Stage 5 request can be declined, so a fourth gate added without a "
      "member fails here rather than arriving in a report as an "
      "unclassified key",
      (_evaluation.SHUTDOWN_SKIP_KEY_PREFIXES,
       len(set(_evaluation.SHUTDOWN_SKIP_KEY_PREFIXES))),
      ((_evaluation.SHUTDOWN_SKIP_WARMUP_KEY_PREFIX,
        _evaluation.SHUTDOWN_SKIP_WAVE_KEY_PREFIX,
        _evaluation.SHUTDOWN_SKIP_SEND_KEY_PREFIX), 3))
# THE NON-DEGENERACY PROBE, WITHOUT WHICH 8b-r WOULD PASS AGAINST A NODE THAT
# NEVER RUNS. A stub with zero requests is also what a node that raised before
# its first call produces, so the SAME node and the SAME trials are driven with
# the flag CLEAR and required to send.
_R_GRP_OK, _S_GRP_OK = _with_clean_flag(
    lambda: run_node(_SIX, per_trial=False))
check("8b-v ...and with no shutdown asked for the identical drive DOES send "
      "and DOES publish verdicts, so 8b-r is measuring the gate rather than a "
      "node that could not run",
      (len(_S_GRP_OK.requests) > 0, len(at(_R_GRP_OK, "evaluations")) > 0,
       bool(at(_R_GRP_OK, "error"))),
      (True, True, False))


# ===========================================================================
# SECTION 9 -- THE CONTROLS
# ===========================================================================
#
# Every assertion above is shown to FAIL when the thing it checks is broken.
# EVERY PLANT GOES INTO AN IN-MEMORY COPY of evaluation.py: the file on disk is
# never written, which is what keeps this file out of the collision matrix, and
# section 10 hashes it to say so.
#
# A PLANT THAT MATCHED NOTHING IS A NAMED FAILURE, NOT A CONTROL THAT PASSED.
# `control()` counts its needle first and records a PLANT-FAILED result if the
# count is not 1 -- the lesson pass 20f-1 wrote down twice: a revert reporting
# MISSED can mean the check is weak OR that the revert never took effect, and
# those are not the same finding.

section("SECTION 9 -- every assertion is shown to fire")


def _worker_names(module):
    """The renderer names reachable from the copy's ``_issue`` body.

    Reads the COPY's own source out of the plant rather than the file on disk,
    which is what makes c13 a statement about the plant. The module carries the
    text it was compiled from on ``__control_src__``.
    """
    tree = ast.parse(getattr(module, "__control_src__", ""))
    fns = [n for n in ast.walk(tree)
           if isinstance(n, ast.FunctionDef) and n.name == "_issue"]
    names = {n.id for f in fns for n in ast.walk(f) if isinstance(n, ast.Name)}
    return sorted(names & {"_render_trial_blocks", "_build_trials_text",
                           "_user_prompt_for", "_wrap_trials"})


def control(label, subs, probe, expected):
    """Run `probe` against a copy of evaluation.py with `subs` applied."""
    src = _EVAL_SRC
    for old, new in subs:
        if src.count(old) != 1:
            check(f"{label} [PLANT-FAILED: needle appears "
                  f"{src.count(old)} times, expected 1]", src.count(old), 1)
            return
        src = src.replace(old, new, 1)
    module = drive(module_from, src, "oncotriage.agent._control_copy")
    if not isinstance(module, _Absent):
        module.__control_src__ = src
    if isinstance(module, _Absent):
        check(f"{label} [PLANT-FAILED: the copy would not import]",
              module, "an importable module")
        return
    check(label, drive(probe, module), expected)


def node_of(module):
    return module.node_llm_classifier_evaluation


# --- c1: the partition is not one trial per chunk ---------------------------
# THE FIRST THING A BROKEN PARTITION MEETS IS THE ALIGNMENT GUARD, and that is
# worth showing rather than working around: with the per-trial branch disabled
# the packer produces ONE chunk for six blocks, the dispatch refuses BY NAME
# before a request is issued, and nothing is silently sent under the wrong key.
control(
    "c1  a node that packs instead of partitioning is REFUSED by name, before "
    "any request [2a/2b]",
    [("    if _per_trial_calls:\n        initial_chunks = [[t] for t in trials]",
      "    if False:\n        initial_chunks = [[t] for t in trials]")],
    lambda m: (type(run_node(_SIX, per_trial=True,
                             node=node_of(m))[0]).__name__,
               len(run_node(_SIX, per_trial=True,
                            node=node_of(m))[1].requests)),
    ("_Absent", 0),
)
# ...and the refusal is the packer's own mismatch error rather than an
# AttributeError or a KeyError, which is what makes it actionable.
control(
    "c1  ...and it is PackingBlockMismatchError specifically",
    [("    if _per_trial_calls:\n        initial_chunks = [[t] for t in trials]",
      "    if False:\n        initial_chunks = [[t] for t in trials]")],
    lambda m: PackingBlockMismatchError.__name__ in repr(
        run_node(_SIX, per_trial=True, node=node_of(m))[0]),
    True,
)
# THE PARTITION ITSELF, falsified with the guard relaxed so the check under
# test is 2a/2b rather than the refusal above. TWO SUBSTITUTIONS, stated: a
# control that needed only one would be testing the guard again.
control(
    "c1  a partition into PAIRS, with the alignment guard relaxed, is CAUGHT "
    "[2a/2b] -- three requests carrying two trials each",
    [("        initial_chunks = [[t] for t in trials]",
      "        initial_chunks = [trials[i:i + 2]\n"
      "                          for i in range(0, len(trials), 2)]"),
     ("        if len(_dispatch_order) != len(trial_blocks):",
      "        if False:"),
     ("            if _chunk_key(_c) != (trials[_i][\"trial\"][\"nct_id\"],):",
      "            if False:"),
     ("            _prompts[_chunk_key(_c)] = _wrap_trials(trial_blocks[_i])",
      "            _prompts[_chunk_key(_c)] = _user_prompt_for(_c)")],
    lambda m: (len(run_node(_SIX, per_trial=True,
                            node=node_of(m))[1].wave_requests()),
               sorted({len(ids_in(r)) for r in run_node(
                   _SIX, per_trial=True,
                   node=node_of(m))[1].wave_requests()})),
    (3, [2]),
)

# --- c2: the bypass is not recorded -----------------------------------------
control(
    "c2  a packing record that does not say it was BYPASSED is CAUGHT [2g] -- "
    "which is the whole reason the column exists, since enabled=False alone "
    "cannot separate this run from a packing-switch-off run",
    [('                          "bypassed_by": MATCHING_CALL_MODE_PER_TRIAL}',
      "                          }")],
    lambda m: "bypassed_by" in (
        run_node(_SIX, per_trial=True, node=node_of(m))[0]
        .get("llm_classifier_packing") or {}),
    False,
)

# --- c3: a REAL TRIAL doubles as the cache writer ---------------------------
# THE RETIRED DESIGN, PLANTED BACK. This is the exact shape this pass removed:
# the cache is written by the first TRIAL call rather than by a dedicated
# warmup, so a trial's request is also infrastructure and its failure is also a
# scheduling event. The plant swaps the warmup's call for the first trial's,
# leaving everything else -- the awaiting, the accounting, the ledger row -- in
# place, so what changes is only WHAT the first request carries.
control(
    "c3  a first TRIAL call doing the cache warmup's job is CAUGHT [2d/3a] -- "
    "the entanglement this pass exists to remove",
    [("                    _warmup_response = call_matching_model_warmup(\n"
      "                        system_prompt, prompt_cache_key=_cache_key)",
      "                    _warmup_response = _issue(\n"
      "                        _dispatch_pairs[0][0],\n"
      "                        _prompts[_chunk_key(_dispatch_pairs[0][0])],\n"
      "                        _cache_key)[1]")],
    lambda m: (lambda st: (len(st.warmup_requests()),
                           is_warmup(st.requests[0])))(
        run_node([trial(i) for i in range(4)], per_trial=True, parallel=4,
                 node=node_of(m))[1]),
    (0, False),
)
# ...and the SHIPPED node says the opposite on the identical probe, which is
# what makes the row above a measurement rather than a statement about the stub.
check("c3  SHIPPED: the first request carries no trial and is the warmup [3a]",
      (lambda st: (len(st.warmup_requests()), is_warmup(st.requests[0])))(
          run_node([trial(i) for i in range(4)], per_trial=True,
                   parallel=4)[1]), (1, True))

# --- c4: the in-flight bound is ignored -------------------------------------
control(
    "c4  a pool sized by the work rather than by the bound is CAUGHT [3e] -- "
    "the six-party barrier the bound makes unsatisfiable becomes satisfiable",
    [("                _bound = min(_parallel_bound, len(_rest))",
      "                _bound = len(_rest)")],
    lambda m: (lambda st: (st.max_in_flight <= 2, st.barrier_broken))(
        run_node([trial(i) for i in range(7)], per_trial=True, parallel=2,
                 node=node_of(m),
                 stub=_Stub(barrier_size=7, barrier_timeout=15.0))[1]),
    (False, False),
)

# --- c5: the dispatch order stops agreeing with the batch's order -----------
# The guard is what makes 2d and 4d hold rather than luck, so it gets its own
# control first: reorder the queue and the node refuses by name.
control(
    "c5  a dispatch order that no longer agrees with the batch is REFUSED by "
    "name [2d]",
    # DROPPING `reversed` IS THE WHOLE PLANT. `pending` is a LIFO, so seeding
    # it in batch order makes it POP in reverse batch order -- the one-line
    # slip that would silently invert the dispatch. (A `sorted(...,
    # reverse=True)` here would NOT do it: it reverses the seeding and the pop
    # order comes back to batch order, which is what the first attempt at this
    # control did and why it reported the plant as uncaught.)
    [("        pending = [(c, 0) for c in reversed(initial_chunks)]",
      "        pending = [(c, 0) for c in initial_chunks]")],
    lambda m: PackingBlockMismatchError.__name__ in repr(
        run_node(_TEN, per_trial=True, parallel=4, node=node_of(m))[0]),
    True,
)
# ...and the merge ITSELF, falsified with the guard relaxed and the prompts
# built per chunk so the broken node is otherwise coherent. Consuming in
# reverse batch order renumbers every trial's answering call.
control(
    "c5  a node that consumes its responses in an order other than the "
    "batch's is CAUGHT [4d]",
    [("        pending = [(c, 0) for c in reversed(initial_chunks)]",
      "        pending = [(c, 0) for c in initial_chunks]"),
     ("            if _chunk_key(_c) != (trials[_i][\"trial\"][\"nct_id\"],):",
      "            if False:"),
     ("            _prompts[_chunk_key(_c)] = _wrap_trials(trial_blocks[_i])",
      "            _prompts[_chunk_key(_c)] = _user_prompt_for(_c)")],
    lambda m: [
        {e.get("nct_id"): e.get("call_index")
         for e in run_node(_TEN, per_trial=True, parallel=4,
                           node=node_of(m))[0]["evaluations"]}[
            t["trial"]["nct_id"]]
        for t in _TEN],
    # 11 down to 2: the warmup still takes call 1, and the ten trials are
    # consumed in reverse batch order behind it.
    list(range(11, 1, -1)),
)

# --- c6: a failed call is not isolated --------------------------------------
control(
    "c6  a per-trial call failure that ends the PATIENT is CAUGHT [5a]",
    [("            if _per_trial_calls and not isinstance(e, Stage5ShutdownRequested):\n"
      "                PER_TRIAL_CALL_FAILURES[type(e).__name__] += 1",
      "            if False:\n"
      "                PER_TRIAL_CALL_FAILURES[type(e).__name__] += 1")],
    lambda m: (run_node(_FIVE, per_trial=True, node=node_of(m),
                        stub=_Stub(fail_for=[_VICTIM]))[0]["evaluations"],
               ),
    ([],),
)

# --- c7: the floor is removed -----------------------------------------------
control(
    "c7  a total outage reported as a clean run of not-evaluable trials is "
    "CAUGHT [5g] -- the failure the isolation would otherwise manufacture",
    [("    if _per_trial_calls and not per_trial_succeeded and (\n"
      "            _warmup_error is not None or per_trial_failed_calls):",
      "    if False:")],
    lambda m: (len(run_node(_FIVE, per_trial=True, node=node_of(m),
                            stub=_Stub(fail_for=[t["trial"]["nct_id"]
                                                 for t in _FIVE]))[0]
                   ["evaluations"]),
               bool(run_node(_FIVE, per_trial=True, node=node_of(m),
                             stub=_Stub(fail_for=[t["trial"]["nct_id"]
                                                  for t in _FIVE]))[0]
                    ["error"])),
    (5, False),
)

# --- c8: the failure is blamed on the model ---------------------------------
control(
    "c8  a transport failure recorded under the model-OMISSION reason is "
    "CAUGHT [5b/5c]",
    [("                    _unevaluable_entry(t, NOT_EVALUABLE_CALL_FAILED)",
      "                    _unevaluable_entry(t, NOT_EVALUABLE_MODEL_OMITTED)")],
    lambda m: {e.get("nct_id"): e.get("not_evaluable_reason")
               for e in run_node(_FIVE, per_trial=True, node=node_of(m),
                                 stub=_Stub(fail_for=[_VICTIM]))[0]
               ["evaluations"] if e.get("not_evaluable_reason")},
    {_VICTIM: NOT_EVALUABLE_MODEL_OMITTED},
)

# --- c9: the counter stops moving -------------------------------------------
def _counter_delta(module):
    before = sum(PER_TRIAL_CALL_FAILURES.values())
    run_node(_FIVE, per_trial=True, node=node_of(module),
             stub=_Stub(fail_for=[_VICTIM]))
    return sum(PER_TRIAL_CALL_FAILURES.values()) - before


control(
    "c9  an isolated failure that moves no counter is CAUGHT [5f] -- "
    "isolation without a record is the silent recovery this project removes",
    [("                PER_TRIAL_CALL_FAILURES[type(e).__name__] += 1\n"
      "                per_trial_failed_calls += 1",
      "                per_trial_failed_calls += 1")],
    _counter_delta,
    0,
)

# --- c10: the prefetched response is not consumed ---------------------------
# THE DOUBLE-BILLING SHAPE: every chunk is fetched in parallel AND then called
# again by the send loop. Both sets are real, billed requests.
control(
    "c10 a send loop that ignores the prefetched response and calls again is "
    "CAUGHT [2a] -- every trial billed twice",
    [("            outcome = _prefetched.pop(_chunk_key(chunk), None)",
      "            outcome = None")],
    lambda m: (lambda st: (len(st.wave_requests()),
                           len(st.warmup_requests())))(
        run_node(_SIX, per_trial=True, node=node_of(m))[1]),
    (12, 1),
)

# --- c11: the cached figure stops being recorded per call -------------------
control(
    "c11 a ledger that drops the provider's cached figure is CAUGHT [7a] -- "
    "and a summed total alone could not tell",
    [('            "cached_tokens": _cached,', '            "cached_tokens": None,')],
    lambda m: [c.get("cached_tokens") for c in
               run_node(_FIVE, per_trial=True, node=node_of(m),
                        stub=_Stub(cached=700, warmup_cached=0))[0]
               ["llm_classifier_call_details"]
               if not c.get("warmup")],
    [None] * 5,
)

# --- c12: the OFF arm takes the per-trial branch ----------------------------
control(
    "c12 a switch that is not consulted is CAUGHT [8c/1e]",
    [("    _per_trial_calls = (config.matching_call_mode()\n"
      "                        == MATCHING_CALL_MODE_PER_TRIAL)",
      "    _per_trial_calls = True")],
    lambda m: len(run_node(_SIX, per_trial=False,
                           node=node_of(m))[1].requests),
    7,
)

# --- c13: the render moves onto the worker ----------------------------------
control(
    "c13 a worker that renders its own prompt is CAUGHT [3i] -- the shape "
    "that would lose increments from the two decode counters",
    [('                return ("ok", call_matching_model(\n'
      '                    system_prompt, prompt_, prompt_cache_key=cache_key_))',
      '                return ("ok", call_matching_model(\n'
      '                    system_prompt, _user_prompt_for(chunk_),\n'
      '                    prompt_cache_key=cache_key_))')],
    _worker_names,
    ["_user_prompt_for"],
)

# --- c14: the block index slips ---------------------------------------------
control(
    "c14 a dispatch that pairs a trial with ANOTHER trial's rendered block is "
    "CAUGHT [2k] -- the model would be asked about a trial nobody selected",
    [("            _prompts[_chunk_key(_c)] = _wrap_trials(trial_blocks[_i])",
      "            _prompts[_chunk_key(_c)] = _wrap_trials(\n"
      "                trial_blocks[(_i + 1) % len(trial_blocks)])")],
    # THE DAMAGE IS THE MEASUREMENT. A rotation leaves the SET of messages
    # unchanged, so comparing them keyed by the id inside each message would
    # pass -- which is how the first version of this control was written and
    # why it reported the plant as uncaught. What actually breaks is the
    # FILING: the response to the call filed under trial i answers about trial
    # i+1, the out-of-set detector drops it as cross-chunk, and every trial in
    # the batch ends up omitted.
    lambda m: (
        sum(1 for e in run_node(_SIX, per_trial=True,
                                node=node_of(m))[0]["evaluations"]
            if e.get("eligible") == "eligible"),
        sorted({e.get("not_evaluable_reason")
                for e in run_node(_SIX, per_trial=True,
                                  node=node_of(m))[0]["evaluations"]})),
    (0, [NOT_EVALUABLE_MODEL_OMITTED]),
)


# --- c15: the abandoned calls are dropped from the record -------------------
# THE DEFECT THIS PASS FOUND IN ITS OWN WORK, planted back: four requests that
# were issued and billed, reported as one.
control(
    "c15 a failure return that abandons a paid queue without recording it is "
    "CAUGHT [5b(c)/5b(d)] -- a token TOTAL that is really a prefix",
    [("            _account_unconsumed()\n"
      "            elapsed = time.time() - start\n"
      "            REFUSALS_OBSERVED[MATCHING_MODEL] += 1",
      "            elapsed = time.time() - start\n"
      "            REFUSALS_OBSERVED[MATCHING_MODEL] += 1")],
    lambda m: (len(run_node(_FIVE, per_trial=True, parallel=4, node=node_of(m),
                            stub=_Stub(refuse_for=[_FIRST_ID]))[0]
                   ["llm_classifier_call_details"]),
               run_node(_FIVE, per_trial=True, parallel=4, node=node_of(m),
                        stub=_Stub(refuse_for=[_FIRST_ID]))[0]
               ["llm_classifier_input_tokens"]),
    (2, 2000),
)

# --- c16: the folded rows stop saying what they are -------------------------
control(
    "c16 a folded row indistinguishable from one the node actually read is "
    "CAUGHT [5b(e)] -- 'the node stopped before reading this' and 'this "
    "response was unusable' would collapse into one fact",
    [('                "unconsumed": True,', '                "unconsumed": False,')],
    lambda m: sorted(bool(c.get("unconsumed")) for c in
                     run_node(_FIVE, per_trial=True, parallel=4, node=node_of(m),
                              stub=_Stub(refuse_for=[_FIRST_ID]))[0]
                     ["llm_classifier_call_details"]),
    [False] * 6,
)

# --- c17: a raised abandoned call is folded as though it had usage ----------
control(
    "c17 an abandoned call that RAISED, counted as a billed call, is CAUGHT "
    "[5b(k)] -- it produced no usage object and inventing one is exactly what "
    "_billed_so_far's calls_made guard refuses to do",
    [("                abandoned_errors += 1\n                continue",
      "                abandoned_errors += 1\n"
      "                calls_made += 1\n                continue")],
    lambda m: run_node(_FIVE, per_trial=True, parallel=4, node=node_of(m),
                       stub=_Stub(refuse_for=[_FIRST_ID],
                                  fail_for=[_FIVE[4]["trial"]["nct_id"]]))[0]
    ["llm_classifier_calls"],
    6,
)


# --- c18: the warmup failure does not stop the wave -------------------------
# THE DEFECT THE WHOLE PASS EXISTS TO PREVENT, planted back: a warmup that
# could not be established, and the trial calls going out anyway -- every one
# of them against a prefix nothing wrote, at full input price, with the patient
# reported as an ordinary success. `pending.clear()` is the one line that makes
# "there is no uncached fallback anywhere" true.
control(
    "c18 a warmup failure that still lets the wave go out is CAUGHT [3w(a)] "
    "-- N full-price requests against a cold prefix, reported as a clean "
    "patient",
    [("                        _warmup_error = _wu_exc\n"
      "                        pending.clear()",
      "                        _warmup_error = None")],
    lambda m: (lambda r: (len(r[1].wave_requests()),
                          bool(at(r[0], "error"))))(
        run_node(_FOUR, per_trial=True, parallel=4, node=node_of(m),
                 stub=_Stub(warmup_raise=RuntimeError("endpoint down")))),
    (4, False),
)

# --- c19: every failure read as a refusal of the request shape --------------
# The classifier's conjunction is what keeps a transport failure from being
# read as "the provider refuses this shape". Remove it and an unreachable
# endpoint degrades to the retired schedule and sends the wave anyway.
control(
    "c19 a classifier that reads EVERY failure as a refusal of the request "
    "shape is CAUGHT [3w(a)/3w(f)] -- an outage would silently become a "
    "schedule change",
    [("    if _http_status_of(exc) != 400:\n        return None",
      "    if False:\n        return None")],
    lambda m: (lambda r: (len(r[1].wave_requests()),
                          bool(at(r[0], "error"))))(
        run_node(_FOUR, per_trial=True, parallel=4, node=node_of(m),
                 stub=_Stub(warmup_raise=RuntimeError(
                     "connection reset while sending max_completion_tokens")))),
    (4, False),
)

# --- c20: the warmup row stops being marked ---------------------------------
control(
    "c20 a warmup row indistinguishable from a trial call is CAUGHT [4c] -- "
    "any per-trial accounting that groups on `trials` would fold an "
    "infrastructure call into the per-trial figures",
    [('                "entries_emitted": None,\n                "warmup": True,',
      '                "entries_emitted": None,')],
    lambda m: [c.get("warmup") for c in
               run_node(_FIVE, per_trial=True, parallel=4, node=node_of(m))[0]
               ["llm_classifier_call_details"]],
    [None] * 6,
)
control(
    "c20 ...and a warmup row claiming to have carried a trial is CAUGHT [4c] "
    "-- it carried none, and 1 would put it in the per-trial denominator",
    [('                "depth": None,\n                "trials": 0,',
      '                "depth": 0,\n                "trials": 1,')],
    lambda m: sorted({c.get("trials") for c in
                      run_node(_FIVE, per_trial=True, parallel=4,
                               node=node_of(m))[0]
                      ["llm_classifier_call_details"]}),
    [1],
)

# --- c21: the floor asks about CALLS rather than about VERDICTS -------------
# THE CORRECTION THIS PASS HAD TO MAKE, planted back. Counting the warmup in
# `calls_made` is right -- it is billed -- but it makes `calls_made` non-zero
# for every patient that got as far as dispatching, so a floor that tested it
# would stop firing for the total outage it exists to catch.
control(
    "c21 a zero-success floor that tests `calls_made` rather than verdicts is "
    "CAUGHT [3w(h)/5g] -- a successful warmup would satisfy it and a total "
    "outage would be reported as a patient with no matches and no error",
    [("    if _per_trial_calls and not per_trial_succeeded and (\n"
      "            _warmup_error is not None or per_trial_failed_calls):",
      "    if _per_trial_calls and not calls_made and (\n"
      "            _warmup_error is not None or per_trial_failed_calls):")],
    lambda m: (lambda r: (len(at(r, "evaluations")), bool(at(r, "error"))))(
        run_node(_FIVE, per_trial=True, parallel=4, node=node_of(m),
                 stub=_Stub(fail_for=[t["trial"]["nct_id"]
                                      for t in _FIVE]))[0]),
    (5, False),
)

# --- c22: the answering model is not checked on the warmup ------------------
control(
    "c22 a mismatched judge discovered only in the send loop is CAUGHT "
    "[3w(i)] -- the wave would already have been issued and billed before "
    "anything raised",
    [("            _expected = config.matching_wire_model()\n"
      "            _returned = getattr(response_, \"model\", None)\n"
      "            if _returned is not None and _returned != _expected:\n"
      "                raise MatchingModelMismatchError(_expected, _returned)",
      "            _returned = getattr(response_, \"model\", None)")],
    lambda m: len(run_node(_FOUR, per_trial=True, parallel=4, node=node_of(m),
                           stub=_Stub(warmup_model="some-other-judge"))[1]
                  .wave_requests()),
    4,
)

# --- c23: the warmup asks for the batch's output ceiling --------------------
control(
    "c23 a warmup that asks for the full output ceiling is CAUGHT [2d] -- it "
    "would be an ordinary-priced request that evaluates nothing",
    [("        max_completion_tokens=config."
      "MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS,",
      "        max_completion_tokens=MATCHING_MAX_TOKENS,")],
    lambda m: (lambda st: st.requests[0].get("max_completion_tokens"))(
        run_node(_FOUR, per_trial=True, parallel=4, node=node_of(m))[1]),
    config.MATCHING_MAX_TOKENS,
)

# --- c24: the warmup and the wave are routed apart --------------------------
# A ROUTING HINT THAT DIFFERS BETWEEN THE WARMUP AND ITS WAVE IS WORSE THAN NO
# HINT: it asks the provider to put the request that WRITES the prefix on one
# machine and every request that would READ it on another.
control(
    "c24 a warmup routed apart from its own wave is CAUGHT [2d] -- the hint "
    "would send the writer and the readers to different machines",
    [("                    _warmup_response = call_matching_model_warmup(\n"
      "                        system_prompt, prompt_cache_key=_cache_key)",
      "                    _warmup_response = call_matching_model_warmup(\n"
      "                        system_prompt,\n"
      "                        prompt_cache_key=(_cache_key or \"\") + \"-warmup\")")],
    lambda m: (lambda st: len({r.get("prompt_cache_key")
                               for r in st.requests}))(
        run_node(_FOUR, per_trial=True, parallel=4, node=node_of(m))[1]),
    2,
)

# --- c25: the fallback keeps a hint the provider has just refused -----------
control(
    "c25 a cache-key rejection that leaves the hint on the wave is CAUGHT "
    "[3w(o)] -- every fallback call would be refused for the parameter that "
    "was just refused, and a recoverable finding would fail the patient",
    [("                        if _rejection == WARMUP_REJECTED_CACHE_KEY:\n"
      "                            # DROPPED FOR THE WAVE TOO. The provider refused this\n"
      "                            # parameter, so carrying it into the fallback's calls\n"
      "                            # would refuse every one of them and turn a recoverable\n"
      "                            # configuration finding into a failed patient.\n"
      "                            _cache_key = None",
      "                        if False:\n"
      "                            _cache_key = None")],
    lambda m: sorted(
        {r.get("prompt_cache_key") is not None
         for r in run_node(_FOUR, per_trial=True, parallel=4, node=node_of(m),
                           stub=_Stub(warmup_raise=_WarmupRefused(
                               "Unrecognized request argument supplied: "
                               "prompt_cache_key")))[1].wave_requests()}),
    [True],
)

# --- c26: the warmup is folded as an abandoned response ---------------------
# `_account_unconsumed()` is unaffected by the warmup because the warmup is
# consumed before `_prefetched` is populated. Planting it INTO `_prefetched`
# is what makes 3j a measurement rather than an argument about ordering.
control(
    "c26 a warmup filed among the prefetched responses is CAUGHT [3j] -- it "
    "would be folded as an abandoned trial response on every failure return",
    [("        _prefetched = {}\n        if _dispatch_pairs:",
      "        _prefetched = {(\"__warmup__\",): (\"ok\", None, 0)}\n"
      "        if _dispatch_pairs:")],
    lambda m: sum(1 for d in run_node(
        _SIX, per_trial=True, parallel=6, node=node_of(m),
        stub=_Stub(refuse_for=[_SIX[0]["trial"]["nct_id"]]))[0]
        ["llm_classifier_call_details"] if d.get("unconsumed")),
    6,
)


# --- c27: the provider guard is not consulted -------------------------------
# Without it the warmup's own refusal is caught by the dispatch's `except`,
# classified as a transport failure and retried MAX_LLM_CLASSIFIER_RETRIES
# times -- so a configuration defect arrives as three identical failed patients
# rather than as one named error, and the operator is sent to the endpoint.
def _c27(module):
    saved = config.MATCHING_PROVIDER
    try:
        config.MATCHING_PROVIDER = config.MATCHING_PROVIDER_BEDROCK
        result, stub = run_node(_FOUR, per_trial=True, parallel=4,
                                node=node_of(module))
        # A RAISE versus a RETURNED FAILURE is the whole distinction, and the
        # first version of this probe missed it: the exception's NAME appears
        # in the returned error string either way, because the floor
        # interpolates `type(...).__name__`. What changes is whether the node
        # refused before spending anything or spent a retry on it.
        return (isinstance(result, _Absent), bool(at(result, "error")),
                len(stub.requests))
    finally:
        config.MATCHING_PROVIDER = saved


control(
    "c27 a node that does not check the provider before dispatching is CAUGHT "
    "[1i] -- the refusal degrades into a retried transport failure instead of "
    "a raise, so MAX_LLM_CLASSIFIER_RETRIES spends three patients on it",
    [("    if _per_trial_calls:\n"
      "        assert_per_trial_provider_supported()",
      "    if False:\n"
      "        assert_per_trial_provider_supported()")],
    _c27,
    # 0 requests either way: the warmup's own guard raises before the client is
    # reached, so what the plant changes is WHERE the refusal surfaces -- a
    # returned failure the retry router will spend a budget on, rather than a
    # raise the operator sees once.
    (False, True, 0),
)

# --- c28: the fallback's cache writer is issued and never read --------------
# THE PRE-FIX CODE PATH, PLANTED BACK. The held-back writer's outcome was filed
# into `_prefetched` and the wave went out regardless, so a writer that raised
# released N-1 full-price requests against a prefix nothing had written -- the
# exact leak the dedicated warmup exists to prevent, reached through the door
# opened for a provider that refuses the warmup's shape.
control(
    "c28 a fallback writer whose outcome is never inspected is CAUGHT "
    "[3fw(a)/3fw(c)] -- three uncached full-price requests behind a writer "
    "that raised, and a patient reported as a clean run",
    [('                    if _writer[0] == "error":', "                    if False:")],
    lambda m: (lambda r: (len(r[1].wave_requests()), bool(at(r[0], "error")),
                          len(at(r[0], "evaluations"))))(
        run_node(_FOUR, per_trial=True, parallel=4, node=node_of(m),
                 stub=_Stub(warmup_raise=_WarmupRefused(
                     "Invalid value for 'max_completion_tokens': must be >= 16"),
                     fail_for=[_FB_WRITER_ID]))),
    # Four requests, no error, four verdicts -- three of them bought against a
    # cold prefix and one of them the writer's own isolated failure, which is
    # indistinguishable from an unlucky trial.
    (4, False, 4),
)
# ...and the COUNTER, separately: without the inspection there is nothing to
# count, so a run that lost its fallback writer is invisible to the run-end
# degradation report.
def _c28_counter(module):
    before = dict(PER_TRIAL_WARMUP_DEGRADATIONS)
    run_node(_FOUR, per_trial=True, parallel=4, node=node_of(module),
             stub=_Stub(warmup_raise=_WarmupRefused(
                 "Invalid value for 'max_completion_tokens': must be >= 16"),
                 fail_for=[_FB_WRITER_ID]))
    after = dict(PER_TRIAL_WARMUP_DEGRADATIONS)
    return sum(v for k, v in after.items()
               if k.startswith(WARMUP_FALLBACK_WRITER_FAILURE_KEY_PREFIX)) - \
        sum(v for k, v in before.items()
            if k.startswith(WARMUP_FALLBACK_WRITER_FAILURE_KEY_PREFIX))


control(
    "c28 ...and the fallback-writer counter stops moving [3fw(e)] -- "
    "recovery without a record is the silent recovery this project removes",
    [('                    if _writer[0] == "error":', "                    if False:")],
    _c28_counter,
    0,
)

# --- c29: the duplicate-key guard is removed --------------------------------
control(
    "c29 a batch carrying one nct_id twice, with the uniqueness guard "
    "removed, is CAUGHT [2b(a)/2b(b)] -- three dispatched calls collapse onto "
    "two keys and the send loop's second pop buys a fourth",
    [("        if len(set(_dispatch_keys)) != len(_dispatch_keys):",
      "        if False:")],
    lambda m: (lambda st: (len(st.wave_requests()),
                           sum(1 for r in st.requests
                               if _DUP_A in r["messages"][1]["content"]),
                           sum(1 for r in st.requests
                               if _DUP_B in r["messages"][1]["content"])))(
        run_node(_DUP, per_trial=True, parallel=4, node=node_of(m))[1]),
    # FOUR wave calls for three trials -- the extra one is the live request
    # `_obtain` makes for a key the collision already consumed. And the first
    # trial's criteria reach the provider ZERO times: `_prompts` is fully built
    # before dispatch, so the second write won and BOTH calls under that key
    # carried the second trial's block.
    (4, 0, 3),
)

# --- c30: the splitter's live path, shown to be reachable when chunks are ---
#          not singletons -----------------------------------------------------
# THE PAYOFF STATED AS A CONTROL. 2b(e) says a truncated response produces no
# extra request; that is only true because every chunk is a singleton. Partition
# into PAIRS -- with the two dispatch guards relaxed so the broken node is
# otherwise coherent -- and the same responses drive the splitter, which queues
# chunks nothing prefetched and `_obtain` calls them live.
control(
    "c30 a partition that is not one trial per chunk makes `_obtain`'s "
    "live-call path REACHABLE again [2b(e)] -- the splitter halves the pair, "
    "and each half is a chunk no dispatch ever filed",
    [("        initial_chunks = [[t] for t in trials]",
      "        initial_chunks = [trials[i:i + 2]\n"
      "                          for i in range(0, len(trials), 2)]"),
     ("        if len(_dispatch_order) != len(trial_blocks):",
      "        if False:"),
     ('            if _chunk_key(_c) != (trials[_i]["trial"]["nct_id"],):',
      "            if False:"),
     ("            _prompts[_chunk_key(_c)] = _wrap_trials(trial_blocks[_i])",
      "            _prompts[_chunk_key(_c)] = _user_prompt_for(_c)")],
    lambda m: (lambda r: (len(r[1].wave_requests()),
                          at(r[0], "llm_classifier_truncation_splits")))(
        run_node(_TRUNC4, per_trial=True, parallel=4, node=node_of(m),
                 stub=_Stub(truncate_for=[t["trial"]["nct_id"]
                                          for t in _TRUNC4]))),
    # Two dispatched pair-calls, two splits, four live singleton calls behind
    # them: six requests where the shipped node makes four.
    (6, 2),
)

# --- c31: the executor is shut down without cancelling the queue ------------
# THE `with` FORM, PLANTED BACK AS ITS EXACT EQUIVALENT: `shutdown(wait=True)`
# with `cancel_futures` at its default. Everything queued runs.
control(
    "c31 an executor shut down without cancelling its queue is CAUGHT "
    "[3int(b)] -- all eight calls are bought after the interrupt, which is "
    "the 'hang' an operator sees after pressing Ctrl-C",
    [("                        _ex.shutdown(wait=True, cancel_futures=True)",
      "                        _ex.shutdown(wait=True)")],
    lambda m: (lambda r: (len(r[1].wave_requests()),
                          isinstance(r[0], _Absent),
                          "KeyboardInterrupt" in repr(r[0]),
                          r[2]))(
        _interrupt_probe(node=node_of(m))),
    # The plant moves the REQUEST COUNT and moves nothing else: `wait=True` is
    # on both sides, so the empty leak list is what says 3int(e) measures the
    # join rather than the cancellation.
    (8, True, True, []),
)


# ===========================================================================
# SECTION 9b -- THE COLUMN, ROUND TRIP
# ===========================================================================
#
# `inferences.matching_call_mode` follows `matching_provider` exactly: an
# additive TEXT column, read from config at INSERT time so it lands on EVERY
# row this writer produces -- including the no-candidates rows and the Stage 5
# failure returns, which are the rows a mode comparison most needs to be able
# to attribute.
#
# EVERY DATABASE HERE IS A SCRATCH FILE IN A TEMP DIRECTORY, asserted to differ
# from the production path before anything is written, and removed at the end.

section("SECTION 9b -- inferences.matching_call_mode, written and read back")

_TMP = tempfile.mkdtemp(prefix="oncotriage-per-trial-")


def scratch_db(name):
    path = os.path.join(_TMP, name)
    _dl._INITIALIZED_DATABASES.discard(os.path.abspath(path))
    return path


def result_dict(patient_id, **extra):
    """The minimum a terminal node emits that log_inference accepts."""
    base = {
        "patient_id": patient_id,
        "timestamp": "2026-08-23T00:00:00",
        "matching_model": "gpt-5.6-terra",
        "llm_classifier_input_tokens": 100,
        "llm_classifier_output_tokens": 20,
        "matches": [], "near_misses": [], "not_evaluable": [],
        "stage_timings": {},
    }
    base.update(extra)
    return base


def read_mode(db, patient_id):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return [r[0] for r in conn.execute(
            "SELECT matching_call_mode FROM inferences WHERE patient_id = ?",
            (patient_id,)).fetchall()]
    finally:
        conn.close()


check("9b(a) the column is declared in the migration dict, as TEXT",
      _dl.INFERENCE_COLUMN_ADDITIONS.get("matching_call_mode"), "TEXT")
check("9b(b) ...and the schema era was bumped in the same commit, which is "
      "the rule that makes the stamp worth reading",
      _dl.SCHEMA_USER_VERSION >= 3, True)

_db9 = scratch_db("call_mode.db")
check("9b(c) the scratch database is NOT the production one",
      os.path.abspath(_db9) == os.path.abspath(
          _dl.resolve_inference_db_path(None)), False)

_saved9 = config.MATCHING_PER_TRIAL_CALLS_ENABLED
try:
    config.MATCHING_PER_TRIAL_CALLS_ENABLED = False
    drive(_dl.log_inference, result_dict("grouped"), dict(PATIENT),
          db_path=_db9)
    config.MATCHING_PER_TRIAL_CALLS_ENABLED = True
    drive(_dl.log_inference, result_dict("per-trial"), dict(PATIENT),
          db_path=_db9)
    # A run that never reached Stage 5 at all: the mode is still a fact about
    # the PROCESS, so the column must be filled.
    config.MATCHING_PER_TRIAL_CALLS_ENABLED = True
    drive(_dl.log_inference,
          result_dict("no-candidates", matching_model=None,
                      llm_classifier_input_tokens=0,
                      llm_classifier_output_tokens=0),
          dict(PATIENT), db_path=_db9)
finally:
    config.MATCHING_PER_TRIAL_CALLS_ENABLED = _saved9

check("9b(d) a grouped run stores 'grouped'", read_mode(_db9, "grouped"),
      [config.MATCHING_CALL_MODE_GROUPED])
check("9b(e) a per-trial run stores 'per_trial' -- and the two differ, so "
      "9b(d) is a measurement",
      read_mode(_db9, "per-trial"), [config.MATCHING_CALL_MODE_PER_TRIAL])
check("9b(f) a row from a run that never reached Stage 5 carries it too, "
      "which is what 'read from config, not from the result dict' buys",
      read_mode(_db9, "no-candidates"), [config.MATCHING_CALL_MODE_PER_TRIAL])
check("9b(g) every stored value is inside the declared vocabulary",
      sorted({v for pid in ("grouped", "per-trial", "no-candidates")
              for v in read_mode(_db9, pid)}
             - set(config.MATCHING_CALL_MODES)), [])

# A PRE-MIGRATION DATABASE READS NULL AND IS NOT BACKFILLED. Built by creating
# the table and DROPPING the column, so the "before" shape comes from the
# writer's own constants rather than from a retyped CREATE.
_db9h = scratch_db("pre_migration.db")
drive(_dl.initialize_database, _db9h)
_conn9 = sqlite3.connect(_db9h)
try:
    _conn9.execute("ALTER TABLE inferences DROP COLUMN matching_call_mode")
    _conn9.commit()
    _pre_cols = [r[1] for r in _conn9.execute(
        "PRAGMA table_info(inferences)").fetchall()]
finally:
    _conn9.close()
check("9b(h) the pre-migration shape really lacks the column (without this "
      "the next two checks are about a database that already had it)",
      "matching_call_mode" in _pre_cols, False)

_dl._INITIALIZED_DATABASES.discard(os.path.abspath(_db9h))
drive(_dl.log_inference, result_dict("migrated"), dict(PATIENT), db_path=_db9h)
check("9b(i) the additive migration adds it on the next open, and the new row "
      "carries the live mode",
      read_mode(_db9h, "migrated"), [config.matching_call_mode()])

_conn9b = sqlite3.connect(f"file:{_db9h}?mode=ro", uri=True)
try:
    _legacy = _conn9b.execute(
        "SELECT COUNT(*) FROM inferences WHERE matching_call_mode IS NULL"
    ).fetchone()[0]
finally:
    _conn9b.close()
check("9b(j) ...and nothing was backfilled: a row written before the column "
      "existed would still read NULL. (Zero here because this scratch "
      "database had no such rows; the check is that the migration writes no "
      "default, which the DEFAULT-less ALTER guarantees)", _legacy, 0)



# --- c32..c35: the shutdown flag ---------------------------------------------
#
# Each drives a COPY whose own module-level flag is set, so the shipped
# module's flag is untouched and section 8B's readings above cannot be
# disturbed by a control below them.

def _shutdown_probe(module, *, before=False, trials=None, parallel=1):
    """Drive `module`'s node with a shutdown asked for, and return the run.

    `before=True` asks before the node is entered (nothing should be sent at
    all); otherwise a `_ShutdownStub` asks during the first WAVE call, which is
    deterministic at parallel=1 because the wave's pool then has one worker and
    issues in dispatch order.
    """
    trials = trials if trials is not None else _SIX
    stub = None
    try:
        if before:
            module.request_stage5_shutdown("SIGTERM (signal 15)")
        else:
            stub = _ShutdownStub(module=module, after=1,
                                 reason="Ctrl-C during the main batch pass")
        result, stub = run_node(trials, per_trial=True, parallel=parallel,
                                node=node_of(module), stub=stub)
        return result, stub
    finally:
        module.clear_stage5_shutdown()


control(
    "c32 a wave worker that does not consult the flag is CAUGHT [8b-k] -- "
    "every queued request goes out at full price after the operator asked the "
    "run to stop, which is the whole drain this pass bounds",
    [('            if _SHUTDOWN_REQUESTED:\n'
      '                STAGE5_SHUTDOWN_SKIPS[\n'
      '                    f"{SHUTDOWN_SKIP_WAVE_KEY_PREFIX}"\n'
      '                    f"{_SHUTDOWN_REASON or \'unspecified\'}"] += 1\n'
      '                return ("error", Stage5ShutdownRequested(\n'
      '                    f"the request was not issued: {_SHUTDOWN_REASON}"))',
      '            if False:\n'
      '                pass')],
    lambda m: (lambda r: (len(r[1].wave_requests()),
                          bool(at(r[0], "error"))))(_shutdown_probe(m)),
    (6, False),
)

control(
    "c33 *** a send loop that ISOLATES a shutdown to its trial is CAUGHT "
    "[8b-m]. *** The patient is published as a SUCCESS carrying one verdict "
    "and five not-evaluable entries -- and `_on_done` checkpoints a success, "
    "so a resume skips it forever and the cohort keeps a hole shaped like the "
    "moment somebody pressed Ctrl-C",
    [("            if _per_trial_calls and not isinstance(e, Stage5ShutdownRequested):",
      "            if _per_trial_calls:")],
    lambda m: (lambda r: (bool(at(r[0], "error")),
                          len(at(r[0], "evaluations") or []),
                          sorted({str(e.get("not_evaluable_reason"))
                                  for e in (at(r[0], "evaluations") or [])})))(
        _shutdown_probe(m)),
    (False, 6, ["None", NOT_EVALUABLE_CALL_FAILED]),
)

control(
    "c34 a node that skips the gate above the warmup is CAUGHT [8b-e] -- a "
    "patient entered after the shutdown pays for one infrastructure request to "
    "warm a prefix no trial request will ever use, and pays it again on every "
    "one of MAX_LLM_CLASSIFIER_RETRIES re-entries",
    [('            if _SHUTDOWN_REQUESTED:\n'
      '                STAGE5_SHUTDOWN_SKIPS[\n'
      '                    f"{SHUTDOWN_SKIP_WARMUP_KEY_PREFIX}"',
      '            if False:\n'
      '                STAGE5_SHUTDOWN_SKIPS[\n'
      '                    f"{SHUTDOWN_SKIP_WARMUP_KEY_PREFIX}"')],
    lambda m: (lambda r: (len(r[1].warmup_requests()),
                          len(r[1].wave_requests())))(
        _shutdown_probe(m, before=True)),
    (1, 0),
)

control(
    "c35 a fold that counts a DECLINED request as an ABANDONED one is CAUGHT "
    "[8b-o] -- `abandoned:` means paid for and not read, and inflating it with "
    "the consequences of an interrupt sends a reader after a transport fault "
    "that never happened. It is also what proves `_account_unconsumed()` is "
    "REACHED on this return: without the call there would be nothing to "
    "miscount",
    [("                if isinstance(_payload, Stage5ShutdownRequested):\n"
      "                    continue",
      "                if False:\n"
      "                    continue")],
    lambda m: (lambda r: sorted(
        k for k in m.PER_TRIAL_CALL_FAILURES if k.startswith("abandoned:")))(
        _shutdown_probe(m)),
    ["abandoned:Stage5ShutdownRequested"],
)


# --- c36: the grouped send gate, in the RETAINED COMPARISON ARM -------------
#
# Until the operator-control pass the flag bounded the per-trial WAVE and
# nothing else, and MATCHING_PER_TRIAL_CALLS_ENABLED was False -- so every
# grouped patient in flight when an operator pressed Ctrl-C carried on issuing
# every REMAINING chunk of the packer's plan at full price. Grouped is no
# longer the shipped arm; it is the arm every migration comparison is measured
# against, so its stop semantics have to match the shipped arm's or the
# comparison carries a shutdown artefact.
#
# THE PROBE REUSES `_ShutdownStub` AT after=1, which is deterministic in
# grouped mode for a sharper reason than in per-trial mode: grouped chunks are
# issued SEQUENTIALLY from the node's own thread, so there is no pool and no
# ordering to depend on. The gate reads the flag BEFORE handing the request to
# the stub, so call 1 goes out, the stub sets the flag, and call 2 is where the
# gate is measured.

# ENOUGH TRIALS, AND LONG ENOUGH CRITERIA, THAT THE PACKER MAKES SEVERAL
# CHUNKS. With one chunk there is no second request to decline and the control
# would pass for free -- a plant that is not a behaviour change is not a test
# of the harness (pass 20c-3d's rule, restated as a precondition).
_MANY = [trial(i, criteria_chars=8000) for i in range(15)]
_BASE_CHUNKS = len(run_node(_MANY, per_trial=False)[1].requests)
check("9-pre THE PACKER REALLY MAKES SEVERAL CHUNKS for this input, so there "
      "IS a later request for the gate to decline",
      _BASE_CHUNKS >= 3, True)


def _grouped_probe(module):
    """(requests issued, was the patient failed, verdicts published)."""
    stub = _ShutdownStub(module=module, after=1,
                         reason="Ctrl-C during the main batch pass")
    try:
        result, stub = run_node(_MANY, per_trial=False, node=node_of(module),
                                stub=stub)
        return (len(stub.requests), bool(at(result, "error")),
                len(at(result, "evaluations") or []))
    finally:
        module.clear_stage5_shutdown()


check("9-pre-b ...and the SHIPPED node issues exactly ONE and then declines "
      "the rest, failing the patient rather than publishing a partial success",
      drive(_grouped_probe, _evaluation), (1, True, 0))

control(
    "c36 *** A GROUPED SEND LOOP THAT DOES NOT CONSULT THE FLAG IS CAUGHT "
    "[8b-r]. *** Every remaining chunk of the packer's plan goes out at full "
    "price after the operator asked the run to stop -- and the patient is then "
    "published as a SUCCESS carrying all its verdicts, so `_on_done` "
    "checkpoints it and a resume skips it forever",
    [('        if _SHUTDOWN_REQUESTED:\n'
      '            STAGE5_SHUTDOWN_SKIPS[\n'
      '                f"{SHUTDOWN_SKIP_SEND_KEY_PREFIX}"',
      '        if False:\n'
      '            STAGE5_SHUTDOWN_SKIPS[\n'
      '                f"{SHUTDOWN_SKIP_SEND_KEY_PREFIX}"')],
    _grouped_probe,
    (_BASE_CHUNKS, False, 15),
)


# ===========================================================================
# SECTION 10 -- NOTHING ON DISK WAS WRITTEN, NOTHING WAS LEFT INSTALLED
# ===========================================================================

section("SECTION 10 -- no repository file was written")

_final = {p: hashlib.sha256(open(p, "rb").read()).hexdigest()
          for p in _BASELINE_HASHES}
check("10a both source files are byte-identical to their pre-run state -- "
      "every plant went into an in-memory copy",
      _final, _BASELINE_HASHES)
check("10b non-degeneracy: the two baseline hashes are distinct, so 10a is "
      "not comparing one file with itself",
      len(set(_BASELINE_HASHES.values())), 2)
check("10c ...and neither is the digest of an empty read",
      hashlib.sha256(b"").hexdigest() in _BASELINE_HASHES.values(), False)
check("10d every dependency override this file installed was cleared",
      deps.is_resolved(deps.OPENAI_CLIENT)
      and deps.peek(deps.OPENAI_CLIENT) is not deps.UNSET
      and isinstance(deps.peek(deps.OPENAI_CLIENT), _Stub), False)
# DERIVED FROM THE VALUES THIS FILE SAW AT IMPORT, never from literals: a
# literal here is a third copy of the shipped defaults and turns any legitimate
# change to either constant into a failure that names a restore rather than the
# change. `_START_CONFIG` is captured beside the source hashes at the top of the
# file, before anything writes either constant.
check("10e the two config constants this file writes are back where they "
      "started",
      (config.MATCHING_PER_TRIAL_CALLS_ENABLED,
       config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS), _START_CONFIG)
check("10f non-degeneracy: the captured start values are the real ones, not a "
      "pair this file could have satisfied by writing nothing",
      (isinstance(_START_CONFIG[0], bool),
       isinstance(_START_CONFIG[1], int)
       and not isinstance(_START_CONFIG[1], bool)
       and _START_CONFIG[1] >= 1), (True, True))

# THE MODULE COUNTER IS LEFT NON-ZERO ON PURPOSE AND IS SAID SO. It is a
# process-lifetime counter, this file drove real failures through the real
# branch, and zeroing it here would be the test tidying away the evidence that
# its own controls fired.
shutil.rmtree(_TMP, ignore_errors=True)
check("10g the scratch directory every database went into is gone",
      os.path.exists(_TMP), False)
check("10h ...and it was never anywhere near the production database",
      os.path.abspath(_TMP) in os.path.abspath(
          _dl.resolve_inference_db_path(None)), False)
check("10i the failure counter recorded this file's own planted failures, "
      "which is what says the increment path was really taken",
      sum(PER_TRIAL_CALL_FAILURES.values()) > 0, True)


# ===========================================================================
# SUMMARY
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

sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Aug 23 19:00:00 2026

@author: ramyalsaffar
"""
