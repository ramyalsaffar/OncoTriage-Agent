# The Run Spend Gate
####################

"""How much this campaign has spent, and whether it may spend more.

WHAT THIS IS FOR
----------------
Provider-side budget alarms are MONITORING. AWS Budgets fires 8 to 24 hours
after the money is gone; OpenAI's usage page is a report. Neither can stop a
request. Until this module existed the only brake on this pipeline was the
operator stop sentinel, which needs a human who already knows something is
wrong -- and the failure this is built for is the one nobody is watching: a
mis-set constant, a defect that re-issues calls, a corpus ten times the size
somebody meant, running overnight.

So the brake is a PRE-CALL GATE inside the application: measured cumulative
cost, checked immediately before every billed call, hard stop at the cap.

WHAT IT MEASURES, AND HOW THAT DIFFERS FROM WHAT THE ROW STORES
---------------------------------------------------------------
The ledger is charged from the provider's OWN usage block, priced by
``oncotriage/utils.py:get_model_cost`` against ``config.PRICING_CONFIG`` -- the
same arithmetic ``log_inference`` uses for ``inferences.estimated_cost_usd``.
Same rates, same rounding, same table.

IT IS NOT THE SAME NUMBER, and the difference is in this ledger's favour. The
stored row carries the token accumulators of ONE Stage 5 invocation, and a parse
failure routes the graph back into that node with every accumulator reset -- so
a patient that spent three attempts stores the last one's tokens. That is
recorded as a known under-count at ``run_harness.price_result`` and is surfaced
by its ``cost_complete`` flag. **This ledger has no such gap**: it is charged
once per RESPONSE, at the three sites where a response object is obtained, and
those sites are inside the retry rather than around it. Every attempt is
counted, including the responses per-trial mode pays for and abandons.

A gate built on an under-counting number under-enforces, which is why this is
not simply a sum over the rows.

WHAT IT STILL CANNOT SEE, stated rather than glossed:

  * transport-layer retries inside the provider SDK. The SDK retries a 5xx or a
    429 itself and reports one usage block; a request that failed before any
    response arrived reports none at all. Neither is visible to any Python in
    this process, and inventing a figure from prompt length would put a number
    in a measurement column that no provider ever reported.
  * anything billed outside Stage 5. Embeddings at index time
    (``retrieval/indexer.py``), the independent rater
    (``evaluation/rater.py``, a different vendor and a different price table)
    and the ragas harness are NOT instrumented. This gate is the batch runner's,
    and ``config.SPEND_CAP_USD`` says so.
  * a run whose prior fragments are unpriceable. See ``LedgerSeed`` below: a
    seeded baseline is a FLOOR when any prior row carried a NULL cost, and it
    says so rather than pretending otherwise.

WHY A MODULE-LEVEL LEDGER AND NOT A FIELD ON THE STATE
------------------------------------------------------
``TrialMatchState`` reaches the six graph nodes and nothing else, and the
question this answers -- "what has this PROCESS spent" -- spans patients, spans
threads and outlives every node. It is the same argument
``oncotriage/observability.py`` makes for the correlation ID being a ContextVar
rather than a state field, reaching the opposite conclusion for the opposite
reason: that value is per-patient and must not leak between them, this one is
per-run and must.

MODULE STATE THAT SURVIVES INTO THE NEXT RUN DESCRIBES THE WRONG RUN, so
``reset()`` is called by ``oncotriage/batch/runner.py:main()`` beside
``clear_write_ledger()``, ``run_fingerprint.clear_cache()``,
``STOP_SWITCH.reset()`` and ``clear_stage5_shutdown()`` -- the sixth piece of
per-run module state, cleared for the reason the five above it are.

THREAD SAFETY IS REQUIRED HERE, unlike the shutdown flag
--------------------------------------------------------
``evaluation._SHUTDOWN_REQUESTED`` is a bare boolean deliberately: it is
assigned in a signal handler, where taking a lock is how a shutdown path
deadlocks, and a read of a module global is atomic in CPython. This is
different in both directions. It is a read-MODIFY-write (``total += cost``),
which is not atomic; it is charged from ``MAX_WORKERS x
per_trial_parallel_bound()`` worker threads at once; and it is never touched
from a signal handler. So it takes a lock, and the lock is the reason the
number is exact rather than a floor under contention.

THE READ ON THE HOT PATH IS DELIBERATELY NOT LOCKED. ``cap_exceeded()`` reads
one float and compares it, on ``_start_patient_unless_stopped``'s precedent: a
lock there would serialize every worker through the gate for a value that only
ever grows, and the worst a torn read can do is admit ONE more request -- which
is already inside the overshoot bound ``config.SPEND_CAP_USD`` states.

THIS MODULE IMPORTS NO STORAGE LAYER
------------------------------------
The resume derivation -- what the interrupted run already spent, read out of
``inferences`` -- lives in ``oncotriage/storage/database_logger.py``, which owns
the ``runs`` table and the fingerprint columns the campaign chain is walked
over. It is handed here as a ``LedgerSeed``. Two reasons: this module is
imported by ``oncotriage/agent/evaluation.py``, so a storage import here would
put the whole writer in the agent's import graph; and
``oncotriage/degradation.py`` imports this module for its counters, so an edge
from here to storage would be a second path into a module degradation already
imports.
"""

import threading
from collections import Counter
from typing import NamedTuple, Optional

from oncotriage import config
from oncotriage.observability import console, get_logger
from oncotriage.utils import UnknownModelPricingError, get_model_cost

log = get_logger(__name__)


# ===========================================================================
# COUNTERS
# ===========================================================================

SPEND_GATE_SKIPS = Counter()
"""Billed requests NOT issued because a spend limit was reached.

Keyed ``{phase}:{limit}`` -- the phase is one of ``SPEND_SKIP_KEY_PREFIXES``
and the limit is one of ``SPEND_LIMITS``.

MONEY NOT SPENT, RECORDED ANYWAY, on ``STAGE5_SHUTDOWN_SKIPS``' footing: every
other counter in the degradation registry names something that went wrong, and
this names something that went right -- but it is the CAUSE of the error rows a
gated run leaves behind, and an operator reading "40 patients errored" on a run
that hit its cap needs "and 613 Stage 5 requests were never sent" beside it or
the errors read as a fault.

INCREMENTED FROM WORKER THREADS, so it is a FLOOR under contention
(``Counter[k] += 1`` is a load-add-store). Acceptable for the same reason it is
acceptable for the shutdown skips: it counts things that did NOT happen, a
floor understates how much was saved, and no decision is made on it. The
LEDGER, which decisions are made on, is locked.
"""

SPEND_LEDGER_FAULTS = Counter()
"""A response could not be priced, so its cost is missing from the ledger.

Keyed ``{reason}:{detail}``. The two reasons:

    ``unpriced_model:``  ``get_model_cost`` raised ``UnknownModelPricingError``
                         -- the model that answered is absent from
                         ``PRICING_CONFIG``. Keyed by the model id.
    ``bad_usage:``       the response carried no readable ``prompt_tokens`` /
                         ``completion_tokens``. Keyed by what was found.

**EVERY KEY HERE IS SPEND THIS GATE CANNOT SEE, so a non-zero total means the
cap is being enforced against a number lower than the truth.** That is the one
direction a spend gate must not fail in silently, which is why this is a
registered degradation rather than a debug line.

IT DOES NOT RAISE, and the reason is that the loud failure already exists one
layer down and is better placed: ``log_inference`` calls ``get_model_cost``
OUTSIDE its try, so an unpriced model aborts the write of that patient's row
with the configuration defect named. Raising here as well would turn the same
defect into a per-request transport failure inside a worker thread, which is a
worse diagnosis of the same fact.
"""

SPEND_CEILING_TRIPS = Counter()
"""Stage 5 invocations that hit the per-invocation billed-call ceiling.

Keyed ``{call_mode}:{ceiling}``. See ``stage5_call_ceiling()``. A non-zero
value here is not a budget event: it means ONE Stage 5 invocation asked for more
billed calls than its configuration can legitimately produce, which is a defect
in this pipeline rather than a campaign that ran long.
"""


# ===========================================================================
# THE CLOSED VOCABULARIES
# ===========================================================================

SPEND_LIMIT_CAP = "spend_cap"
SPEND_LIMIT_CALL_CEILING = "call_ceiling"

SPEND_LIMITS = (SPEND_LIMIT_CAP, SPEND_LIMIT_CALL_CEILING)
"""Which limit declined a request. CLOSED, and a caller may branch on it
exhaustively.

They are two findings with two remediations and must not be one key. The cap
means "this campaign has spent its budget" and is answered by raising the budget
or accepting the stop; the ceiling means "one Stage 5 invocation tried to issue
more calls than it can legitimately need" and is answered by reading the
traceback.
"""

SPEND_SKIP_WARMUP_KEY_PREFIX = "warmup:"
SPEND_SKIP_WAVE_KEY_PREFIX = "wave:"
SPEND_SKIP_SEND_KEY_PREFIX = "send:"

SPEND_SKIP_KEY_PREFIXES = (SPEND_SKIP_WARMUP_KEY_PREFIX,
                           SPEND_SKIP_WAVE_KEY_PREFIX,
                           SPEND_SKIP_SEND_KEY_PREFIX)
"""Every phase ``SPEND_GATE_SKIPS`` can be keyed by. CLOSED.

THEY ARE THE SAME THREE PHASES ``evaluation.SHUTDOWN_SKIP_KEY_PREFIXES`` NAMES,
and that is not a coincidence to be tidied away: both partition THE PLACES A
STAGE 5 REQUEST CAN BE DECLINED, and there are three of them because there are
three billed call sites. The two tuples are restated rather than shared because
the counters are separate -- a request declined for money and a request declined
for a shutdown are different findings -- and
``tests/test_spend_gate.py`` requires them to stay parallel, so a
fourth call site added to one and not the other fails rather than arriving in an
operator's report as an unclassified key.

    ``warmup:``  the gate fired before the per-trial cache writer, so that
                 patient sent NOTHING and cost nothing.
    ``wave:``    a queued per-trial request a worker declined to send.
    ``send:``    the node's own thread declining the next sequential call --
                 grouped mode's only phase, and per-trial mode's for a chunk
                 the reactive splitter built after dispatch.
"""

SEED_SOURCE_NONE = "fresh"
SEED_SOURCE_CAMPAIGN = "campaign_rows"

SEED_SOURCES = (SEED_SOURCE_NONE, SEED_SOURCE_CAMPAIGN)
"""Where a ledger's starting balance came from. CLOSED.

``fresh`` is a run that is resuming nothing, and it is a VALUE rather than an
absence: "this campaign has no prior spend" and "nobody asked" are different
statements, and only the first supports a remaining-budget figure.
"""


# ===========================================================================
# THE SEED
# ===========================================================================

class LedgerSeed(NamedTuple):
    """What a resumed campaign already spent, and how well that is known.

    ``usd``      the sum of ``inferences.estimated_cost_usd`` over the prior
                 runs of this campaign.
    ``rows``     how many inference rows that sum covers.
    ``unpriced`` how many of those rows carried a NULL cost. **A non-zero value
                 makes ``usd`` a FLOOR**, and every consumer says so rather than
                 presenting it as a total -- ``print_cost_by_model``'s
                 "<- A FLOOR, NOT A TOTAL" precedent, which item 38 had to add
                 because an unpriceable group contributing a real 0.0 is
                 indistinguishable from a group that genuinely spent nothing.
    ``runs``     how many prior run rows were walked.
    ``source``   a ``SEED_SOURCES`` member.
    """

    usd: float = 0.0
    rows: int = 0
    unpriced: int = 0
    runs: int = 0
    source: str = SEED_SOURCE_NONE

    @property
    def is_floor(self) -> bool:
        """Is ``usd`` a floor rather than a total?"""
        return self.unpriced > 0


# ===========================================================================
# THE LEDGER
# ===========================================================================

class SpendLedger:
    """Measured billed spend for this process, in US dollars. Thread-safe.

    ONE INSTANCE, module-level, reset per run. See the module docstring for why
    it is not a state field and why the WRITE is locked while the READ is not.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._measured = 0.0
        self._calls = 0
        self._seed = LedgerSeed()

    # -- accumulation ------------------------------------------------------

    def charge(self, model, prompt_tokens, completion_tokens) -> float:
        """Add one response's measured cost. Returns what was added.

        NEVER RAISES. It is called immediately after a billed response arrives,
        on a worker thread, inside a ``try`` whose ``except`` would classify a
        raise here as a TRANSPORT FAILURE and retry the request -- so a pricing
        defect would become a second billed call. Everything that can go wrong
        is counted into ``SPEND_LEDGER_FAULTS`` instead, and the cost of that
        decision is stated at that counter: the gate then enforces against a
        number lower than the truth.

        Args:
            model: the id the provider ECHOED, not the configured one. They
                differ whenever an alias resolves to a dated snapshot, and the
                echoed id is what the provider bills and what
                ``inferences.matching_model`` stores -- so pricing against it is
                what makes this ledger and that column agree. ``None`` falls
                back to ``config.matching_wire_model()``, which is
                ``log_inference``'s own fallback and is exact in the one case it
                is reached: no response, therefore no tokens, therefore
                ``0 x rate`` whichever priced model is named.
            prompt_tokens: ``usage.prompt_tokens``.
            completion_tokens: ``usage.completion_tokens``.

        REASONING TOKENS ARE NOT ADDED and cached tokens are not discounted.
        Both decisions are ``get_model_cost``'s and are inherited rather than
        re-made here: reasoning is already inside ``completion_tokens`` and
        adding it would bill every one twice, and the cached-input discount is
        deliberately unmodelled so that this figure stays comparable with
        ``inferences.estimated_cost_usd``. **The consequence for the gate is
        stated: with prompt caching working, this ledger OVER-estimates, so the
        cap is enforced conservatively.** Over-enforcing is the safe direction
        and is the one this project already chose for that column.
        """
        _in = _as_token_count(prompt_tokens)
        _out = _as_token_count(completion_tokens)
        if _in is None or _out is None:
            SPEND_LEDGER_FAULTS[
                f"bad_usage:{type(prompt_tokens).__name__}/"
                f"{type(completion_tokens).__name__}"] += 1
            with self._lock:
                self._calls += 1
            return 0.0
        try:
            cost = get_model_cost(model or config.matching_wire_model(),
                                  _in, _out)
        except UnknownModelPricingError:
            SPEND_LEDGER_FAULTS[f"unpriced_model:{model}"] += 1
            with self._lock:
                self._calls += 1
            return 0.0
        with self._lock:
            self._measured += cost
            self._calls += 1
        return cost

    def seed(self, seed: LedgerSeed) -> None:
        """Install a resumed campaign's prior spend. Called once, from ``main()``.

        REPLACES rather than adds, deliberately: a second call with a second
        derivation of the same fact would double the baseline, and there is
        exactly one moment in a run at which this is known.
        """
        with self._lock:
            self._seed = seed

    def reset(self) -> None:
        """Forget everything an earlier run in this process spent."""
        with self._lock:
            self._measured = 0.0
            self._calls = 0
            self._seed = LedgerSeed()

    # -- reading -----------------------------------------------------------

    @property
    def measured(self) -> float:
        """What THIS process has been billed, in US dollars."""
        return self._measured

    @property
    def calls(self) -> int:
        """How many billed responses this process has charged.

        Every response, including the ones a pricing fault could not value --
        which is what makes ``calls`` beside ``SPEND_LEDGER_FAULTS`` readable:
        the fault count is the numerator and this is the denominator.
        """
        return self._calls

    @property
    def seeded(self) -> LedgerSeed:
        """The resumed baseline. ``LedgerSeed()`` on a fresh run."""
        return self._seed

    @property
    def total(self) -> float:
        """The campaign's spend: the seeded baseline plus this process's.

        THE NUMBER THE CAP IS COMPARED AGAINST. A resumed run that ignored its
        baseline would get a fresh cap every time a supervisor restarted it,
        which is the failure a per-run cap has and this does not.
        """
        return self._seed.usd + self._measured


SPEND_LEDGER = SpendLedger()
"""The one instance. Reset by ``oncotriage/batch/runner.py:main()``."""


def _as_token_count(value):
    """A usage figure as an int, or None when it is not one.

    ``bool`` IS EXCLUDED even though it is an ``int`` subclass, on this
    project's standing footing (``_cap_age``, ``collection_points``): a
    ``prompt_tokens`` of ``True`` priced as one token is a number nobody
    measured presented as a measurement.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


# ===========================================================================
# THE CAP
# ===========================================================================

class SpendCapConfigurationError(RuntimeError):
    """``config.SPEND_CAP_USD`` is not a cap.

    A ``RuntimeError`` subclass and deliberately not a ``ValueError``, on
    ``UnknownModelPricingError``'s and ``IndexVerificationError``'s precedent: a
    stray ``except ValueError`` around a configuration read must not be able to
    eat a refusal about money.
    """


def spend_cap() -> Optional[float]:
    """The configured cap in US dollars, or None for no cap. ONE OWNER.

    A FUNCTION rather than a module constant, on ``config.matching_call_mode``'s
    footing: the value can move WITHIN a process -- a test sets it, an embedder
    sets it -- and a consumer that read it through a from-import would move
    nothing.

    RAISES on a value that is not a cap. A negative number is not "unlimited"
    and a string is not a budget; reading either as "no cap" is how a
    configuration typo becomes an unbounded campaign. Zero IS a cap and is
    honoured -- it stops the run before its first billed call, which is a
    legitimate rehearsal of the unbilled path.
    """
    cap = config.SPEND_CAP_USD
    if cap is None:
        return None
    if isinstance(cap, bool) or not isinstance(cap, (int, float)):
        raise SpendCapConfigurationError(
            f"config.SPEND_CAP_USD must be a number of US dollars or None for "
            f"no cap; it is {cap!r} ({type(cap).__name__}). A value that is "
            f"not a number cannot be read as 'unlimited' -- that reading is "
            f"how a typo becomes an unbounded campaign.")
    if cap < 0:
        raise SpendCapConfigurationError(
            f"config.SPEND_CAP_USD is {cap!r}. A negative cap is not "
            f"'unlimited'; set it to None if that is what you mean, which "
            f"prints a line on every run banner saying so.")
    return float(cap)


def cap_exceeded() -> bool:
    """Has this campaign spent its budget? A cheap read; never raises.

    THE HOT-PATH QUESTION. It is called before every billed request and from
    ``_start_patient_unless_stopped`` once per patient, so it does one float
    comparison and takes no lock -- see the module docstring for why a torn read
    is inside the stated overshoot bound.

    ``>=`` AND NOT ``>``: at exactly the cap the budget is spent. The other
    reading lets a run cross a $300 cap by one request and call it compliance.

    A CAP THAT CANNOT BE READ DOES NOT STOP THE RUN. ``spend_cap()`` raises on a
    malformed value and that raise is deliberately allowed through at the run's
    START (``describe_cap()`` is called from the banner, before anything is
    spent) and deliberately NOT here: this runs inside a worker thread after the
    money is committed, and a configuration defect surfacing as a per-request
    transport failure is a worse diagnosis of the same fact.
    """
    if not config.SPEND_CAP_ENFORCED:
        return False
    try:
        cap = spend_cap()
    except SpendCapConfigurationError:
        return False
    if cap is None:
        return False
    return SPEND_LEDGER.total >= cap


def remaining() -> Optional[float]:
    """Budget left, in US dollars, or None when there is no cap.

    May be negative, and that is not clamped: a reader is entitled to see the
    overshoot rather than a zero that hides it.
    """
    try:
        cap = spend_cap()
    except SpendCapConfigurationError:
        return None
    if cap is None:
        return None
    return cap - SPEND_LEDGER.total


# ===========================================================================
# THE PER-INVOCATION CALL CEILING
# ===========================================================================

def stage5_call_ceiling(call_mode=None) -> int:
    """The most billed calls ONE Stage 5 invocation can legitimately make.

    Args:
        call_mode: the arm to derive for. ``None`` asks
            ``config.matching_call_mode()``. The node passes its OWN reading, so
            the ceiling and the partition it bounds are decided from one call to
            an owner whose answer can move within a process.

    DERIVED FROM CONFIGURATION, NOT CHOSEN. Both arms:

      PER-TRIAL MODE -- one cache warmup, then one call per candidate trial:

          1 + MAX_TRIALS_FOR_EVALUATION

        and nothing else, because a per-trial chunk is a SINGLETON and
        ``_split_in_half`` refuses to halve one. Measured rather than assumed:
        driving every response to ``finish_reason == "length"`` produces four
        wave calls and ZERO truncation splits.

      GROUPED MODE -- the packer emits at most
        ``MATCHING_MAX_INPUT_PACKED_CHUNKS`` chunks and the reactive splitter
        may halve each of them to depth ``MAX_TRUNCATION_SPLITS``, which issues

          2 ** (MAX_TRUNCATION_SPLITS + 1) - 1

        requests for one chunk -- the identical expression
        ``HARNESS_POST_READ_TIMEOUT_SECONDS`` is already written over, for the
        identical reason: raising the split depth must move this with it. So

          MATCHING_MAX_INPUT_PACKED_CHUNKS x (2 ** (MAX_TRUNCATION_SPLITS + 1) - 1)

    AT THE SHIPPED CONSTANTS: 16 per-trial, 75 grouped.

    IT IS PER INVOCATION AND NOT PER PATIENT, and that grain is the point. A
    retry re-enters the node with fresh state, and re-entry is already bounded
    at ``1 + MAX_LLM_CLASSIFIER_RETRIES`` by the router -- so a per-patient
    ceiling would be this number times four and would need the node to know its
    own patient across invocations, which it does not. The failure mode this
    catches is a LOOP INSIDE one invocation, which is what "a defect that
    re-issues calls" means.

    THERE IS NO MARGIN AND THAT IS DELIBERATE. The ceiling is the exact number
    the configuration permits, so a run that hits it has issued a call the
    configuration cannot account for. A multiplier would be a literal, and a
    literal is what this ceiling exists to avoid.
    """
    if call_mode is None:
        call_mode = config.matching_call_mode()
    if call_mode == config.MATCHING_CALL_MODE_PER_TRIAL:
        return 1 + config.MAX_TRIALS_FOR_EVALUATION
    return (config.MATCHING_MAX_INPUT_PACKED_CHUNKS
            * (2 ** (config.MAX_TRUNCATION_SPLITS + 1) - 1))


class Stage5CallCounter:
    """Billed calls issued by ONE Stage 5 invocation. Thread-safe.

    Created per invocation, so it needs no reset and cannot describe the wrong
    patient. ``take()`` is a claim on a call that is ABOUT to be issued, so the
    ceiling bounds what is SENT rather than what came back -- a request that
    raised still counted against the budget the provider will bill.
    """

    def __init__(self, ceiling: int, call_mode: str):
        self._lock = threading.Lock()
        self._ceiling = ceiling
        self._call_mode = call_mode
        self._issued = 0
        self._refused = 0

    @property
    def ceiling(self) -> int:
        return self._ceiling

    @property
    def call_mode(self) -> str:
        """The call mode the ceiling was DERIVED from, captured at construction.

        IT IS CARRIED RATHER THAN RE-READ, and that is two properties rather
        than one convenience. ``config.matching_call_mode()`` can move within a
        process -- a pin sets it, a test sets it -- so a gate that asked again
        at trip time could key its counter with a mode the ceiling beside it was
        not computed for, which is an uninterpretable pair. And it keeps the
        NODE the only place in ``oncotriage/agent/evaluation.py`` that reads
        that function, which section 1f of
        ``tests/test_agent_stage5_per_trial_calls.py`` pins: one interpretation
        of the flag per invocation, not three.
        """
        return self._call_mode

    @property
    def issued(self) -> int:
        return self._issued

    @property
    def refusals(self) -> int:
        """How many calls this invocation was refused. Diagnostic only."""
        return self._refused

    def take(self):
        """Claim one call. Returns ``(granted, first_refusal)``.

        THE INCREMENT AND THE TEST ARE ONE LOCKED OPERATION, which is what makes
        the ceiling exact under the wave's own pool: ``if issued < ceiling:
        issued += 1`` split across a lock boundary is a check-then-act race that
        admits one extra call per worker.

        ``first_refusal`` IS COMPUTED INSIDE THAT SAME LOCK AND IS NOT DERIVED
        BY THE CALLER, which is the difference between a counter that reports
        INVOCATIONS and one that reports requests. A caller reading
        ``self.refusals == 1`` after the lock was released would race: with the
        wave's four workers refused at once, none of them, or several, could see
        the 1. It is returned rather than exposed for the same reason
        ``take()``'s own test is not two statements.

        WHY THE DISTINCTION IS WORTH A RETURN VALUE. ``SPEND_GATE_SKIPS``
        already counts every declined REQUEST; a second counter reporting that
        same number would be the conflation ``degradation.register`` refuses a
        duplicate name for. What no request count can say is how many PATIENTS
        hit a ceiling, and that is the number a defect report is read off.
        """
        if not config.SPEND_CALL_CEILING_ENFORCED:
            with self._lock:
                self._issued += 1
            return True, False
        with self._lock:
            if self._issued >= self._ceiling:
                self._refused += 1
                return False, self._refused == 1
            self._issued += 1
            return True, False


# ===========================================================================
# THE RUN-LEVEL LATCH
# ===========================================================================

class SpendStop:
    """Has this run hit a spend limit? Latching, thread-safe, announced once.

    ``control.StopSwitch``'s SHAPE AND ITS SEMANTICS, WITHOUT ITS SENTINEL. The
    batch runner's integration reads the same way for both -- a ``poll(where=)``
    in the done-callback and in the submit loop, a ``requested`` attribute on
    the hot path -- because they are the same operational event reached for two
    reasons, and an operator who has learned one should not have to learn the
    other.

    IT IS NOT A ``control.StopSwitch`` SUBCLASS, and the reason is that class's
    own contract: every part of it -- ``_resolve_path``, the note reader, the
    clear vocabulary, the stale-sentinel preflight -- is about a FILE an
    operator creates. There is no file here and nothing to clear; the state is
    the ledger, and the ledger is the only thing that can un-set it (by a
    ``reset()`` between runs). Inheriting would give this class four methods
    that answer about a sentinel that does not exist, which is the silent-no-op
    shape ``_StopSwitch.arm`` had to be re-broken to remove.

    LATCHING FOR ``control.StopSwitch``'s FIRST REASON AND NOT ITS SECOND. The
    answer is acted on by CANCELLING QUEUED WORK, which is not reversible. Its
    second reason -- that an operator deletes the sentinel while the run is
    still finishing -- has no analogue: the ledger only grows within a run, so
    this could not un-trip anyway. The latch is what makes the announcement
    happen once rather than once per worker.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.requested = False
        self.limit = None
        self.detected_in = None
        self.spent = None
        self.cap = None

    def reset(self) -> None:
        """Forget a limit reached by an earlier run in this process."""
        with self._lock:
            self.requested = False
            self.limit = None
            self.detected_in = None
            self.spent = None
            self.cap = None

    def trip(self, limit: str, where: str) -> bool:
        """Latch on a limit a CALL SITE has already decided. Announces once.

        Used by the Stage 5 call-ceiling gate, which reaches its verdict from a
        per-invocation counter this object cannot see.
        """
        return self._latch(limit, where)

    def poll(self, where: str = "run") -> bool:
        """Has a spend limit been reached? Reads the ledger.

        Args:
            where: which pass noticed. Free text; it never reaches a durable
                store and every caller passes a literal.
        """
        with self._lock:
            if self.requested:
                return True
        if not cap_exceeded():
            return False
        return self._latch(SPEND_LIMIT_CAP, where)

    def _latch(self, limit: str, where: str) -> bool:
        with self._lock:
            if self.requested:
                return True
            self.requested = True
            self.limit = limit
            self.detected_in = where
            self.spent = SPEND_LEDGER.total
            try:
                self.cap = spend_cap()
            except SpendCapConfigurationError:
                self.cap = None
            _spent, _cap = self.spent, self.cap

        # OUTSIDE THE LOCK, for control.StopSwitch.poll's reason: the console
        # writer and the logger take locks of their own and this is reached
        # from MAX_WORKERS done-callbacks at once. Holding a lock across a
        # write to a bar-aware writer is how a shutdown path deadlocks.
        rule = "=" * 80
        console.out()
        console.out(rule)
        if limit == SPEND_LIMIT_CAP:
            console.out(f"[SPEND] THE SPEND CAP HAS BEEN REACHED: "
                        f"${_spent:.2f} of ${_cap:.2f}")
        else:
            console.out("[SPEND] A STAGE 5 INVOCATION HIT ITS BILLED-CALL "
                        "CEILING.")
            console.out(f"[SPEND] Campaign spend so far: ${_spent:.2f}")
        # WHAT THIS BLOCK MAY SAY IS BOUNDED BY WHAT IT KNOWS, AND THE FIRST
        # DRAFT EXCEEDED IT. It read "No further patient will be STARTED ... the
        # checkpoint is current, and the run will be recorded STOPPED", which is
        # true of a BATCH RUN and of nothing else -- and this latch is reachable
        # from every Stage 5 caller, including the API, which has no patients,
        # no checkpoint and no `runs` row. A banner that promises a checkpoint
        # to a process that has none is the same class of wrong statement as a
        # closing line promising a resume over an unwritable directory, which
        # `describe_checkpoint_state` exists to remove.
        #
        # So this says what is true of the MECHANISM -- no further billed
        # request is issued, work already in flight completes -- and the batch
        # runner's own closing block says what is true of a RUN.
        console.out(f"[SPEND] Noticed during {where}. No further billed request "
                    f"will be ISSUED; work already in flight completes and is "
                    f"written.")
        console.out(f"[SPEND] To continue, raise config.SPEND_CAP_USD and run "
                    f"again -- a resumed campaign counts what this one spent.")
        console.out(rule)
        log.warning("a spend limit stopped the run",
                    event="spend_limit_reached", status="stopped",
                    mode=where, reason=limit, degraded=True,
                    cost_usd=round(_spent, 6),
                    threshold=(round(_cap, 6) if _cap is not None else None))
        return True


SPEND_STOP = SpendStop()
"""The one instance. Reset by ``oncotriage/batch/runner.py:main()``."""


# ===========================================================================
# REPORTING
# ===========================================================================

def describe_cap() -> str:
    """One line for the run banner, printed before the first billed call.

    IT PRINTS ON EVERY RUN, including the uncapped one, and that is the whole
    point of the unset semantics argued at ``config.SPEND_CAP_USD``: an
    unlimited campaign is reachable and must announce itself. A banner that said
    nothing when there was no cap would make the dangerous state the quiet one.
    """
    try:
        cap = spend_cap()
    except SpendCapConfigurationError as exc:
        return f"[Spend] REFUSING TO READ THE CAP: {exc}"
    if cap is None:
        return ("[Spend] NO SPEND CAP IS SET (config.SPEND_CAP_USD is None). "
                "This run may spend without limit.")
    if not config.SPEND_CAP_ENFORCED:
        return (f"[Spend] Cap ${cap:.2f} -- MEASURED ONLY. "
                f"config.SPEND_CAP_ENFORCED is False, so nothing will be "
                f"declined.")
    return f"[Spend] Cap ${cap:.2f} per campaign."


def describe_seed(seed: LedgerSeed) -> str:
    """One line for the run banner about what a resume inherited."""
    if seed.source == SEED_SOURCE_NONE or seed.runs == 0:
        return ("[Spend] Fresh campaign: no prior run contributes to this "
                "budget.")
    floor = (f" -- A FLOOR, NOT A TOTAL: {seed.unpriced} of {seed.rows} prior "
             f"rows carry no cost and are counted as $0"
             if seed.is_floor else "")
    return (f"[Spend] Resumed campaign: ${seed.usd:.2f} already spent across "
            f"{seed.runs} prior run(s) and {seed.rows} row(s){floor}.")


def report_lines() -> list:
    """The run's closing spend block. Always non-empty.

    A RUN THAT SPENT NOTHING STILL PRINTS, unlike the degradation block: silence
    there means "nothing degraded", which is a statement; silence here would be
    indistinguishable from a run whose ledger was never wired up. The census
    block's argument, applied to the one number an operator asks for first.
    """
    lines = ["SPEND", "-" * 60]
    seed = SPEND_LEDGER.seeded
    # THREE STATES, NOT TWO, AND THEY ARE DECIDED BEFORE ANYTHING IS PRINTED.
    # A cap that could not be READ is not a cap that is ABSENT, and the first
    # version of this function conflated them by testing `cap is not None`
    # afterwards -- so an unreadable cap printed its own diagnosis AND then
    # "NONE -- this run was unbounded" underneath it, two lines making
    # different claims about the same value.
    cap, cap_error = None, None
    try:
        cap = spend_cap()
    except SpendCapConfigurationError as exc:
        cap_error = exc
    lines.append(f"  this process        ${SPEND_LEDGER.measured:.4f} "
                 f"over {SPEND_LEDGER.calls} billed call(s)")
    if seed.runs:
        lines.append(f"  inherited           ${seed.usd:.4f} "
                     f"over {seed.rows} row(s) from {seed.runs} prior run(s)"
                     + ("  <- A FLOOR, NOT A TOTAL" if seed.is_floor else ""))
    lines.append(f"  campaign total      ${SPEND_LEDGER.total:.4f}")
    if cap_error is not None:
        lines.append(f"  cap                 UNREADABLE: {cap_error}")
    elif cap is None:
        lines.append("  cap                 NONE -- this run was unbounded")
    else:
        lines.append(f"  cap                 ${cap:.2f}"
                     + ("" if config.SPEND_CAP_ENFORCED
                        else "   (MEASURED ONLY -- not enforced)"))
        lines.append(f"  remaining           ${cap - SPEND_LEDGER.total:.4f}")
    if SPEND_LEDGER_FAULTS:
        lines.append(f"  UNPRICED RESPONSES  {sum(SPEND_LEDGER_FAULTS.values())}"
                     f"  <- the total above is LOWER than the truth")
    return lines


def print_report(out=None) -> None:
    """Print the closing spend block.

    ``out`` IS INJECTABLE on ``degradation.print_report``'s argument: the one
    caller is inside a ``main()`` that cannot be driven without spending money,
    so the line that reports the spend has to be exercisable on its own.
    """
    emit = console.out if out is None else out
    for line in report_lines():
        emit(line)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Aug 31 2026

@author: ramyalsaffar
"""
