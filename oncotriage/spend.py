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
  * the four ungated sites named in ``BILLED_SITES``, each with the argument
    for leaving it out and each PRINTED by ``report_lines()`` on every run.
    **THIS BULLET USED TO SAY SOMETHING MUCH LARGER AND THE CORRECTION IS THE
    SPEND-COVERAGE PASS.** It read: "anything billed outside Stage 5.
    Embeddings at index time, the independent rater and the ragas harness are
    NOT instrumented. This gate is the batch runner's." All four billed paths
    are instrumented now -- ``SPEND_SOURCES`` enumerates them -- and what is
    left out is an index build, a validator's diagnostic, a flagged probe and a
    free endpoint.
  * spend in ANOTHER PROCESS. A campaign and a judge run separately, seed from
    separate stores, and no shared ledger exists for them to net against.
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
import time
from collections import Counter, deque
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

Keyed ``{phase}:{limit}``, where the limit is one of ``SPEND_LIMITS`` and the
phase names WHERE the request would have gone out:

  * Stage 5 keys by a ``SPEND_SKIP_KEY_PREFIXES`` member, because that node has
    three billed call sites and which one declined decides what the patient
    got: a ``warmup:`` skip means the patient sent nothing at all, a ``wave:``
    skip means some trials were judged and some were not.
  * every other billed path keys by its ``SPEND_SOURCES`` member, because each
    of them has exactly one call site and the useful distinction there is the
    PATH -- an operator reading ``rater_batch:spend_cap`` beside
    ``wave:spend_cap`` is being told which of the program's two spends the
    budget stopped.

The two key spaces are disjoint by construction (a Stage 5 prefix ends in a
colon and is not a ``SPEND_SOURCES`` member) and ``tests/test_spend_coverage.py``
requires them to stay so, because a key that could be read as either would make
this counter uninterpretable in exactly the report it exists for.

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

SEED_SOURCE_RATER_STATE = "rater_state"
"""A rater invocation resuming a batch session, seeded from its own state file.

IT IS A THIRD MEMBER RATHER THAN A REUSE OF ``campaign_rows``. That one names a
sum over ``inferences.estimated_cost_usd`` walked over the ``runs`` chain; this
one names a running total the rater writes into ``rater_state.json`` after each
batch is collected. Different store, different arithmetic, different price
table -- and an operator reading a resumed judge's banner is entitled to know
which of the two answered.
"""

SEED_SOURCES = (SEED_SOURCE_NONE, SEED_SOURCE_CAMPAIGN,
                SEED_SOURCE_RATER_STATE)
"""Where a ledger's starting balance came from. CLOSED.

``fresh`` is a run that is resuming nothing, and it is a VALUE rather than an
absence: "this campaign has no prior spend" and "nobody asked" are different
statements, and only the first supports a remaining-budget figure.
"""


# ===========================================================================
# WHERE THE MONEY WENT
# ===========================================================================

SPEND_SOURCE_STAGE5 = "stage5"
SPEND_SOURCE_EMBEDDING = "query_embedding"
SPEND_SOURCE_RATER = "rater_batch"
SPEND_SOURCE_RAGAS_JUDGE = "ragas_judge"
SPEND_SOURCE_RAGAS_EMBEDDING = "ragas_embedding"

SPEND_SOURCES = (SPEND_SOURCE_STAGE5, SPEND_SOURCE_EMBEDDING,
                 SPEND_SOURCE_RATER, SPEND_SOURCE_RAGAS_JUDGE,
                 SPEND_SOURCE_RAGAS_EMBEDDING)
"""Every billed path this ledger is charged from. CLOSED, and a caller may
branch on it exhaustively.

**THIS TUPLE IS THE ANSWER TO "WHAT DOES THE CAP COVER".** Until the
spend-coverage pass it had one member in all but name: the gate instrumented
Stage 5 and the module docstring said so, while three other billed paths --
Stage 2's dense query embedding, the independent rater, and the ragas harness
-- spent money the cap could not see. A budget that covers one door of a
building with four is not a budget.

WHAT IS **NOT** HERE IS AS LOAD-BEARING AS WHAT IS, and it is not an oversight:
see ``BILLED_SITE_EXEMPTIONS``, where every ungated billed call site in this
repository is named with the argument for leaving it out, and
``report_lines()``, which PRINTS them on every run so an operator reading a cap
figure knows exactly what it does not bound.

THE MEMBERS ARE PATHS, NOT VENDORS. ``rater_batch`` and ``ragas_judge`` both
reach Anthropic and are separate because they are separate *decisions* an
operator makes and separate money they can choose not to spend; ``stage5`` and
``query_embedding`` both reach OpenAI in the same process for the same patient
and are separate because one of them is 99.9% of the bill and the reader needs
to see that rather than infer it.
"""


# ===========================================================================
# WHICH LIMIT THIS PROCESS IS UNDER
# ===========================================================================

SPEND_POLICY_CAMPAIGN = "campaign"
SPEND_POLICY_WINDOW = "serving_window"

SPEND_POLICIES = (SPEND_POLICY_CAMPAIGN, SPEND_POLICY_WINDOW)
"""How this process's spend is bounded. CLOSED. Exactly one is in force.

**A CAMPAIGN CAP IS THE WRONG SHAPE FOR A SERVER, IN BOTH DIRECTIONS, AND THE
SHIPPED GATE HAD IT WRONG FOR EXACTLY THAT REASON.** ``campaign`` compares a
MONOTONE total against a fixed budget, which is right for a batch run -- it has
a beginning, an end, a cohort and a ``runs`` row, and when the money is gone
the right answer is to stop and let an operator decide. Apply the same rule to
``oncotriage/api/server.py`` or ``mcp_server.py`` and BOTH failure modes are
live at once:

  * **unbounded before the cap.** A server writes no ``runs`` row, so nothing
    seeds its ledger and nothing resets it; it is one process that may serve
    for months, and until it has spent the whole campaign budget by itself
    there is no brake at all.
  * **wrong refusals after it.** The total only grows, so the request AFTER the
    cap is reached is declined, and so is every request for the life of the
    process -- for money a campaign somewhere else was budgeted. The remedy an
    operator would reach for is a restart, which resets the ledger and hands
    the process a fresh unbounded budget: the brake is off exactly when it was
    working.

``serving_window`` is the shape that fits: a ROLLING window
(``config.SERVING_SPEND_WINDOW_SECONDS``) against
``config.SERVING_SPEND_CAP_USD``. It is bounded (a runaway loop is stopped
within one window's spend), it self-heals (the window rolls, so a server
recovers with no restart and no operator), and it cannot be defeated by a
restart loop -- restarting empties the window, which is exactly what waiting
would have done anyway.

IT IS PROCESS-GLOBAL AND THAT IS CORRECT RATHER THAN CONVENIENT. A process is a
batch runner, or an ablation study, or a server; it is never two. The policy is
installed once, by the entry point that knows which it is, and
``policy_source()`` reports who installed it so a banner can say so.
"""

_POLICY_LOCK = threading.Lock()
_POLICY = [SPEND_POLICY_CAMPAIGN, "default"]


def set_policy(name: str, source: str = "caller") -> str:
    """Install the limit shape this process runs under. Returns the previous.

    RAISES on an unrecognised name rather than defaulting, on
    ``deps.set_override``'s footing: a policy nobody recognises would silently
    fall back to whichever branch the dispatch tests last, and the two branches
    bound completely different quantities.
    """
    if name not in SPEND_POLICIES:
        raise SpendCapConfigurationError(
            f"{name!r} is not a spend policy. The closed vocabulary is "
            f"{SPEND_POLICIES!r}. A policy that is not recognised cannot be "
            f"read as 'the default' -- the two policies bound different "
            f"quantities against different caps.")
    with _POLICY_LOCK:
        previous = _POLICY[0]
        _POLICY[0] = name
        _POLICY[1] = source
    return previous


def policy() -> str:
    """The limit shape in force. ONE OWNER; every consumer asks this."""
    return _POLICY[0]


def policy_source() -> str:
    """Who installed the policy in force. Diagnostic; free text."""
    return _POLICY[1]


def reset_policy() -> None:
    """Back to the campaign default. For a process that installed one and is
    done with it -- a test, an embedder that shut its server down."""
    with _POLICY_LOCK:
        _POLICY[0] = SPEND_POLICY_CAMPAIGN
        _POLICY[1] = "default"


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
        self._by_source = Counter()
        self._calls_by_source = Counter()
        # THE ROLLING WINDOW. One entry per charge, `(monotonic, usd)`, pruned
        # on every write and every read so a server that runs for months holds
        # one window's worth and not one process lifetime's.
        #
        # `time.monotonic` AND NOT `time.time`: an NTP step or a DST-driven
        # wall-clock change must not empty this window (which hands a server a
        # free budget) or fill it (which declines requests for money nobody
        # spent). The window is a DURATION, and monotonic is the clock that
        # measures durations.
        self._events = deque()

    # -- accumulation ------------------------------------------------------

    def charge(self, model, prompt_tokens, completion_tokens,
               source: str = SPEND_SOURCE_STAGE5) -> float:
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
            self._commit(0.0, source)
            return 0.0
        try:
            cost = get_model_cost(model or config.matching_wire_model(),
                                  _in, _out)
        except UnknownModelPricingError:
            SPEND_LEDGER_FAULTS[f"unpriced_model:{model}"] += 1
            self._commit(0.0, source)
            return 0.0
        self._commit(cost, source)
        return cost

    def charge_usd(self, usd, source: str) -> float:
        """Add one ALREADY-PRICED amount. Returns what was added. NEVER RAISES.

        THE SEAM FOR A VENDOR THIS MODULE CANNOT PRICE. ``charge()`` values a
        response against ``config.PRICING_CONFIG``, which is the OpenAI /
        Bedrock table and holds none of the Anthropic Batches rates, none of the
        batch discount and no cache-tier multipliers. The rater already owns
        that arithmetic in ONE function -- ``rater.price_usage``, over
        ``config.RATER_PRICING`` -- so this takes the number that function
        produced rather than growing a second copy of a price table it would
        have to be kept in step with. The ``CROSS_ENCODER_MODEL`` argument,
        applied to money.

        **THE PRICING STAYS WITH THE PATH AND THE LIMIT STAYS HERE.** That
        split is what lets one cap govern four billed paths priced four ways.

        Args:
            usd: a non-negative float. Anything else is a fault, counted and
                dropped -- a ledger that accepted a string or a negative would
                make the cap enforce against a number nobody measured, in the
                one direction a spend gate must not fail in silently.
            source: a ``SPEND_SOURCES`` member. An unrecognised source is
                CHARGED ANYWAY and counted as a fault: refusing the money
                because its label is unknown would understate the bill, which
                is worse than an unfamiliar key in a report.
        """
        if isinstance(usd, bool) or not isinstance(usd, (int, float)):
            SPEND_LEDGER_FAULTS[f"bad_amount:{type(usd).__name__}"] += 1
            self._commit(0.0, source)
            return 0.0
        if usd != usd or usd in (float("inf"), float("-inf")):
            # NaN AND THE INFINITIES, EXPLICITLY. `float('nan') < 0` is False,
            # so a NaN would pass the sign test below and then poison every
            # later comparison the cap is decided by -- `total >= cap` is False
            # for a NaN total, which is a gate that has silently turned itself
            # off. `inf` fails the other way and would decline every request
            # for ever.
            SPEND_LEDGER_FAULTS[f"bad_amount:{usd!r}"] += 1
            self._commit(0.0, source)
            return 0.0
        if usd < 0:
            SPEND_LEDGER_FAULTS[f"bad_amount:negative"] += 1
            self._commit(0.0, source)
            return 0.0
        self._commit(float(usd), source)
        return float(usd)

    def _commit(self, cost: float, source: str) -> None:
        """The ONE write. Every charge lands here; nothing else touches state.

        ONE OWNER SO THE TOTAL, THE WINDOW AND THE PER-SOURCE BREAKDOWN CANNOT
        DISAGREE. Two writers would be two chances for a path to move one and
        not the others, and the failure would be a report whose columns do not
        add up while the cap enforces against whichever one the dispatch reads.

        A ZERO-COST CHARGE STILL APPENDS AN EVENT AND STILL COUNTS A CALL. Both
        are deliberate: the call count is the denominator
        ``SPEND_LEDGER_FAULTS`` is read against, and a zero in the window is
        harmless while a missing one would make the pruning arithmetic depend on
        whether a response happened to be priceable.
        """
        now = time.monotonic()
        with self._lock:
            self._measured += cost
            self._calls += 1
            self._by_source[source] += cost
            self._calls_by_source[source] += 1
            self._events.append((now, cost))
            self._prune(now)

    def _prune(self, now: float) -> None:
        """Drop events older than the widest window anyone can ask about.

        CALLED WITH ``self._lock`` HELD. The horizon is read from configuration
        on every call rather than captured once, because
        ``SERVING_SPEND_WINDOW_SECONDS`` can move within a process (a test sets
        it) and a deque pruned against a stale horizon would answer a widened
        window with events it had already thrown away -- a window that silently
        reports less than it covers, which is the under-enforcing direction.

        AN UNREADABLE HORIZON PRUNES NOTHING. Growing is the safe failure here:
        a window that holds too much over-reports and therefore over-enforces,
        and the memory it costs is bounded by the run, while a window pruned to
        nothing is a brake that has been removed by a typo.
        """
        horizon = getattr(config, "SERVING_SPEND_WINDOW_SECONDS", None)
        if isinstance(horizon, bool) or not isinstance(horizon, (int, float)):
            return
        if horizon <= 0:
            return
        cut = now - float(horizon)
        while self._events and self._events[0][0] < cut:
            self._events.popleft()

    def window_spend(self, seconds=None) -> float:
        """What this process has been billed within the last ``seconds``.

        ``None`` asks ``config.SERVING_SPEND_WINDOW_SECONDS``. Prunes first, so
        the number is current rather than as-of the last charge -- which matters
        for exactly the case the window exists for: a server that has been idle
        long enough for its window to empty must be able to serve again WITHOUT
        a request having to arrive to trigger the pruning.
        """
        if seconds is None:
            seconds = getattr(config, "SERVING_SPEND_WINDOW_SECONDS", None)
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            return self._measured
        if seconds <= 0:
            return 0.0
        now = time.monotonic()
        cut = now - float(seconds)
        with self._lock:
            self._prune(now)
            return sum(cost for stamp, cost in self._events if stamp >= cut)

    def by_source(self) -> dict:
        """``{source: usd}``, a copy. The reader for "where did it go".

        ``dict(Counter)`` UNDER THE LOCK, not a live view: the caller iterates
        it while worker threads are still charging, and a Counter mutated during
        iteration raises ``RuntimeError`` -- the defect ``degradation.snapshot``
        had to fix once already, met here before it could happen a second time.
        """
        with self._lock:
            return dict(self._by_source)

    def calls_by_source(self) -> dict:
        """``{source: n}``, a copy. The denominator for the line above."""
        with self._lock:
            return dict(self._calls_by_source)

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
            self._by_source.clear()
            self._calls_by_source.clear()
            self._events.clear()

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


def serving_spend_cap() -> Optional[float]:
    """The rolling-window cap a SERVING process runs under, or None. ONE OWNER.

    ``spend_cap()``'s validation, applied to the other constant and for the same
    reasons -- a negative is not "unlimited" and a string is not a budget. It is
    a separate function rather than a parameter because the two caps mean
    different things and are read by different processes: this one bounds a RATE
    over ``config.SERVING_SPEND_WINDOW_SECONDS`` and that one bounds a CAMPAIGN
    total, and sharing a resolver would make a message about one able to name
    the other.
    """
    cap = getattr(config, "SERVING_SPEND_CAP_USD", None)
    if cap is None:
        return None
    if isinstance(cap, bool) or not isinstance(cap, (int, float)):
        raise SpendCapConfigurationError(
            f"config.SERVING_SPEND_CAP_USD must be a number of US dollars or "
            f"None for no cap; it is {cap!r} ({type(cap).__name__}).")
    if cap < 0:
        raise SpendCapConfigurationError(
            f"config.SERVING_SPEND_CAP_USD is {cap!r}. A negative cap is not "
            f"'unlimited'; set it to None if that is what you mean.")
    return float(cap)


def active_cap() -> Optional[float]:
    """The cap the policy in force is enforced against, or None. May raise."""
    if policy() == SPEND_POLICY_WINDOW:
        return serving_spend_cap()
    return spend_cap()


def active_spend() -> float:
    """The quantity the policy in force compares against ``active_cap()``.

    THE TWO POLICIES MEASURE DIFFERENT QUANTITIES AND THIS IS WHERE THAT LIVES.
    Campaign: the seeded baseline plus everything this process has been billed,
    monotone. Window: only what was billed inside the last
    ``SERVING_SPEND_WINDOW_SECONDS``, which can go DOWN -- and going down is the
    whole point, because it is what lets a server recover on its own.
    """
    if policy() == SPEND_POLICY_WINDOW:
        return SPEND_LEDGER.window_spend()
    return SPEND_LEDGER.total


def cap_exceeded() -> bool:
    """Has this process spent its budget under the policy in force?

    A cheap read; never raises. See ``SPEND_POLICIES`` for why "its budget" is
    two different questions and why a server may not be asked the batch
    runner's.

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
        cap = active_cap()
    except SpendCapConfigurationError:
        return False
    if cap is None:
        return False
    return active_spend() >= cap


def remaining() -> Optional[float]:
    """Budget left, in US dollars, or None when there is no cap.

    May be negative, and that is not clamped: a reader is entitled to see the
    overshoot rather than a zero that hides it.
    """
    try:
        cap = active_cap()
    except SpendCapConfigurationError:
        return None
    if cap is None:
        return None
    return cap - active_spend()


class SpendLimitReached(RuntimeError):
    """A billed request was NOT issued because a spend limit was reached.

    THE NON-STAGE-5 PATHS' EQUIVALENT OF ``Stage5SpendStopped``, and it is a
    separate class rather than a shared one for a layering reason and a
    semantic one. ``oncotriage/agent/evaluation.py`` may not be imported by
    ``oncotriage/evaluation/rater.py`` (a different vendor, a different
    program, and it would drag the whole graph into a judge that never touches
    it), and Stage 5's class is a ``Stage5ShutdownRequested`` subclass
    precisely so the node's two shutdown-aware branches cover it -- semantics a
    rater has no analogue for.

    A ``RuntimeError`` subclass and deliberately NOT a ``ValueError``, on
    ``UnknownModelPricingError``'s precedent: a stray ``except ValueError``
    around a request must not be able to eat a refusal about money.

    ``limit`` AND ``source`` ARE ATTRIBUTES, not just words in the message,
    because two callers branch on them -- the API turns one into an HTTP status
    and the MCP server into a payload shape -- and parsing a reason out of an
    exception's text is how those two would drift apart from this one.
    """

    def __init__(self, message, limit=SPEND_LIMIT_CAP, source=None):
        super().__init__(message)
        self.limit = limit
        self.source = source


def seconds_until_under_cap() -> Optional[float]:
    """How long until the rolling window falls back under its cap. Seconds.

    ``None`` when the question does not apply -- no cap, enforcement off, the
    campaign policy (whose total never falls), or already under budget.

    **THIS IS WHAT MAKES A REFUSAL ACTIONABLE RATHER THAN JUST HONEST.** A
    server declining for budget is a TEMPORARY condition that heals with no
    operator, and the client's question is "when". Answering with the window
    width would be a bound rather than an answer, and on a server one request
    over its budget it is wrong by nearly the whole hour.

    HOW IT IS DERIVED. The events are ordered oldest first, so dropping a
    prefix of them is exactly what the passage of time will do. The smallest
    prefix whose removal brings the remainder under the cap identifies the last
    event that has to age out; that event leaves the window
    ``SERVING_SPEND_WINDOW_SECONDS`` after it was charged, and the answer is
    how far away that instant is.

    IT ROUNDS UP AND ADDS A SECOND. Returning the exact instant would have a
    client retry at the moment the comparison flips, where a float and a
    ``>=`` decide; one second is the difference between an answer and a race.

    IT IS A LOWER BOUND ON THE WAIT AND NOT A PROMISE, because other requests
    are being served meanwhile and each adds to the window. That is inherent to
    a shared budget and is why the caller sends it as ``Retry-After``, which
    HTTP defines as a hint, rather than as a guarantee.
    """
    if not config.SPEND_CAP_ENFORCED or policy() != SPEND_POLICY_WINDOW:
        return None
    try:
        cap = serving_spend_cap()
    except SpendCapConfigurationError:
        return None
    if cap is None:
        return None
    width = getattr(config, "SERVING_SPEND_WINDOW_SECONDS", None)
    if isinstance(width, bool) or not isinstance(width, (int, float)) \
            or width <= 0:
        return None
    now = time.monotonic()
    cut = now - float(width)
    with SPEND_LEDGER._lock:                        # noqa: SLF001
        # THE PRIVATE LOCK AND THE PRIVATE DEQUE, deliberately: this is the one
        # question that cannot be answered from the public readers, because it
        # needs the events IN ORDER rather than their sum. Adding a public
        # accessor that hands the deque out would let a caller iterate it while
        # workers append, which is the `RuntimeError` `by_source()` takes the
        # lock to avoid. The coupling is inside one module and is stated here.
        events = [(t, c) for t, c in SPEND_LEDGER._events if t >= cut]
    total = sum(c for _t, c in events)
    if total < cap:
        return None
    for stamp, cost in events:
        total -= cost
        if total < cap:
            return max(0.0, (stamp + float(width)) - now) + 1.0
    # EVERY EVENT AGED OUT AND THE WINDOW IS STILL AT OR OVER THE CAP, which
    # means the cap is zero or negative -- a legitimate rehearsal of the
    # unbilled path, and there is no instant at which it heals.
    return None


def latch_on_limit() -> bool:
    """Should reaching a limit LATCH the run stop? Derived from the policy.

    **THE LATCH IS A PROPERTY OF THE POLICY AND NOT A CHOICE EACH CALL SITE
    MAKES.** Under ``campaign`` the answer is acted on by cancelling queued
    work, the quantity only grows, and un-tripping is meaningless -- so it
    latches, which is what makes the announcement happen once instead of once
    per worker. Under ``serving_window`` the quantity can go DOWN, and that is
    the whole design: a latched server would decline for ever having once been
    briefly over its rate, which is the "wrong refusals" half of the defect
    ``SPEND_POLICIES`` describes, reintroduced through the back door.

    IT IS DERIVED RATHER THAN PASSED because a parameter is a thing a call site
    can get wrong, and there are five of them across four modules. A serving
    surface gets the right behaviour by installing its policy, which is the one
    thing it must do anyway.
    """
    return policy() != SPEND_POLICY_WINDOW


def require_budget(source: str, where: str, *, latch=None) -> None:
    """Raise ``SpendLimitReached`` if the policy in force says stop. Else return.

    **THE PRE-CALL GATE FOR EVERY BILLED PATH THAT IS NOT STAGE 5.** Stage 5 has
    ``evaluation._spend_gate``, which returns rather than raises because one of
    its three call sites runs on a worker thread and must hand its outcome back
    as a tagged pair. Nothing else in this project has that constraint, so
    everything else raises -- which is what makes the gate impossible to forget
    to check.

    Args:
        source: a ``SPEND_SOURCES`` member, for the counter key.
        where: free text naming the call site, for the log line.
        latch: whether to LATCH ``SPEND_STOP``. ``None`` -- the default and
            what every production call site passes -- asks
            ``latch_on_limit()``, which derives it from the policy in force and
            is where that decision is argued. An explicit ``True``/``False``
            forces it, and exists for a test that needs to drive one half
            against the other policy.
    """
    if not cap_exceeded():
        return
    SPEND_GATE_SKIPS[f"{source}:{SPEND_LIMIT_CAP}"] += 1
    if latch_on_limit() if latch is None else latch:
        SPEND_STOP.poll(where=where)
    try:
        cap = active_cap()
    except SpendCapConfigurationError:
        cap = None
    spent = active_spend()
    log.warning("a billed request was not issued because a spend limit was "
                "reached", status="stopped", event="spend_limit_declined",
                phase=source, reason=SPEND_LIMIT_CAP, mode=where,
                cost_usd=round(spent, 6),
                threshold=(round(cap, 6) if cap is not None else None),
                degraded=True)
    raise SpendLimitReached(
        f"the request was not issued: this process has spent "
        f"${spent:.2f} against a {policy()} limit of "
        f"{'no cap' if cap is None else f'${cap:.2f}'}",
        limit=SPEND_LIMIT_CAP, source=source)


# ===========================================================================
# WHAT THE CAP DOES NOT COVER
# ===========================================================================

DISPOSITION_GATED_HERE = "gated_here"
DISPOSITION_GATED_UPSTREAM = "gated_upstream"
DISPOSITION_EXEMPT = "exempt"

BILLED_SITE_DISPOSITIONS = (DISPOSITION_GATED_HERE, DISPOSITION_GATED_UPSTREAM,
                            DISPOSITION_EXEMPT)
"""How a billed call site stands with respect to the cap. CLOSED.

  ``gated_here``      the function itself calls ``require_budget`` before the
                      request, in its own body or in a closure inside it.
  ``gated_upstream``  a named caller gates it. The site is reached only through
                      that caller, so gating it twice would decline the same
                      request against the same ledger for the same reason.
  ``exempt``          not gated, on purpose, with the argument beside it.
"""

BILLED_SITES = {
    # ── STAGE 5 ───────────────────────────────────────────────────────────
    "oncotriage/agent/evaluation.py::call_matching_model": (
        DISPOSITION_GATED_UPSTREAM,
        "oncotriage/agent/evaluation.py::_spend_gate",
        "The Stage 5 node brackets all three of its billed call sites -- the "
        "gate immediately before the request and the charge immediately "
        "after -- which is what bounds the overshoot at the requests in "
        "flight rather than at a whole patient's wave. Gating inside this "
        "function as well would decline the same request twice and would "
        "break the counter's phase keys, which name WHICH of the three sites "
        "declined."),
    "oncotriage/agent/evaluation.py::call_matching_model_warmup": (
        DISPOSITION_GATED_UPSTREAM,
        "oncotriage/agent/evaluation.py::_spend_gate",
        "The per-trial cache writer, gated at the `warmup:` phase. See the "
        "entry above."),
    "oncotriage/agent/bedrock_adapter.py::call_matching_model_bedrock": (
        DISPOSITION_GATED_UPSTREAM,
        "oncotriage/agent/evaluation.py::call_matching_model",
        "The Responses-API arm of Stage 5. `call_matching_model` DISPATCHES "
        "on `config.MATCHING_PROVIDER` and this is one of the three branches, "
        "so it is behind the same gate by construction -- and the gate must "
        "stay in the dispatcher rather than in each branch, or a fourth "
        "provider would arrive ungated with nothing saying so."),
    "oncotriage/agent/bedrock_anthropic_adapter.py::_issue_converse": (
        DISPOSITION_GATED_UPSTREAM,
        "oncotriage/agent/evaluation.py::call_matching_model",
        "The Converse arm of Stage 5. See the entry above."),

    # ── GATED IN THEIR OWN BODY ───────────────────────────────────────────
    "oncotriage/agent/models.py::get_embedding": (
        DISPOSITION_GATED_HERE, None,
        "Stage 2's dense retrieval channel: one billed call per patient, in "
        "the same process and the same pipeline as Stage 5, and invisible to "
        "the cap until the spend-coverage pass. It is cents against Stage "
        "5's hundreds of dollars, which is an argument about how much a hole "
        "leaks rather than about whether it is one."),
    "oncotriage/evaluation/rater.py::submit_batches": (
        DISPOSITION_GATED_HERE, None,
        "The independent judge, priced from `config.RATER_PRICING` and "
        "charged through `rater.charge_batch_to_ledger`. Gated PER CHUNK "
        "rather than once before the loop, so the overshoot is one batch."),
    "oncotriage/evaluation/ragas_harness.py::build_judge": (
        DISPOSITION_GATED_HERE, None,
        "The ragas judge. The gate is inside the `recording_create` closure "
        "this function installs, which is the one point where a request this "
        "harness does not own the loop for can be declined."),
    "oncotriage/evaluation/ragas_harness.py::build_embeddings": (
        DISPOSITION_GATED_HERE, None,
        "The ragas embedder. See the entry above."),

    # ── EXEMPT, EACH ARGUED ───────────────────────────────────────────────
    "oncotriage/retrieval/indexer.py::get_embeddings_batch::_call": (
        DISPOSITION_EXEMPT, None,
        "AN INDEX BUILD IS NOT A CAMPAIGN. `11- RAG Trial Indexer.py` is a "
        "separate operator command with a separate decision behind it; its "
        "cost is bounded by the corpus rather than by a cohort, and it runs "
        "in a process that opens no `runs` row and resumes no chain. Gating "
        "it on the campaign cap would let a campaign that spent its budget "
        "refuse the index rebuild a NEXT campaign needs -- and the money is "
        "in the wrong order of magnitude for that trade: 14,324 trials of "
        "text-embedding-3-small is cents. The brake an index build needs is a "
        "different one, and inventing it here would be a second budget nobody "
        "asked for."),
    "oncotriage/retrieval/index_validator.py::stage2_retrieval_tests": (
        DISPOSITION_EXEMPT, None,
        "A DIAGNOSTIC MUST NOT BE DISABLED BY THE THING IT DIAGNOSES. One "
        "embedding call inside `12- RAG Trial Indexer Validator.py`, whose "
        "job is to answer whether the index is healthy -- and a campaign that "
        "has just stopped on its cap is exactly when an operator runs it. "
        "`deps.peek` / `resolution_state` were added under this rule."),
    "oncotriage/evaluation/rater.py::calibrate_chars_per_token": (
        DISPOSITION_EXEMPT, None,
        "NOT A BILLED CALL. `/v1/messages/count_tokens` is free -- stated on "
        "the function and the reason it exists at all. It is named here "
        "rather than left out because it is an Anthropic API call inside a "
        "module this pass gated, so a reader auditing the gate WILL find it "
        "and is owed the answer in the same place as the others."),
    "oncotriage/fixtures/replay.py::main": (
        DISPOSITION_EXEMPT, None,
        "THE OPENAI TRIPWIRE, AND IT IS THE OPPOSITE OF A BILLED CALL. The "
        "replay harness calls `chat.completions.create` on a stand-in that "
        "RAISES, twice, as a negative control -- once through the shadowed "
        "path and once unshadowed -- and refuses to replay unless both do. "
        "Gating it would make a spend limit able to disable the check that "
        "proves no fixture replay reaches a live endpoint."),
    "bedrock_probe.py::main": (
        DISPOSITION_EXEMPT, None,
        "THE DELIBERATE FLAGGED SPEND. It refuses to do anything without "
        "`--i-understand-this-bills` (exit 2, nothing called, nothing "
        "billed), it is two to three calls, and its entire purpose is to bill "
        "them in order to settle a configuration question before a campaign's "
        "worth of money rests on it. A cap that could refuse the probe would "
        "refuse the measurement that tells an operator what the cap should "
        "be."),
    "bedrock_probe.py::_probe_bedrock_anthropic": (
        DISPOSITION_EXEMPT, None,
        "The Converse branch of the probe. See the entry above."),
    "bedrock_probe.py::_probe_throttle_ceiling::_one": (
        DISPOSITION_EXEMPT, None,
        "The probe's throttling measurement. See `bedrock_probe.py::main`."),
}
"""Every site in this repository that touches a billed provider endpoint, with
its disposition and the argument for it. CLOSED, and derived-against.

**AN EXEMPTION WITHOUT A PINNED ARGUMENT IS THE NEXT HOLE WAITING TO BE
FOUND.** Four of the sites below were, until the spend-coverage pass, simply
absent from everyone's mental model of what the cap covered -- which is how
"the gate instruments Stage 5" became "the project has a spend gate" in every
later reading of it. `tests/test_spend_coverage.py` DERIVES the site list from
source, by walking every `.py` in the repository for an ATTRIBUTE ACCESS of a
billed endpoint name at any nesting depth, and requires the result to equal
this dict's keys EXACTLY, in both directions -- so a new billed path fails, and
an entry whose site no longer exists fails too, and the table cannot rot into a
permission slip.

**THE SCAN IS ON ATTRIBUTE ACCESS AND NOT ON CALLS, and that is not
fastidiousness: it is the only rule that catches
`oncotriage/evaluation/ragas_harness.py`.** That module captures
`real_create = client.messages.create` and calls it later through the
reference, so a call-shaped scan reports the file as touching no billed
endpoint at all -- which is exactly what the first version of this derivation
reported, about a module that spends real money on two vendors. You cannot bill
without naming one of these attributes; you can bill without a call node the
scanner recognises.

WHAT IT STILL CANNOT SEE, stated: an endpoint reached by `getattr(client,
"converse")`, and any billed API this project does not yet use. Both are named
in the test so the limit travels with the check.
"""

BILLED_SITE_EXEMPTIONS = {
    site: why for site, (disposition, _gate, why) in BILLED_SITES.items()
    if disposition == DISPOSITION_EXEMPT
}
"""The ungated subset, DERIVED from the table above rather than listed beside
it. ``report_lines()`` prints the keys on every run: an operator reading
``cap $300.00`` is entitled to know, in the same block, the places that figure
does not bound -- which is the difference between a budget and a number.
"""


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


def describe_serving_cap() -> str:
    """One line for a SERVING process's startup banner.

    A SEPARATE FUNCTION FROM ``describe_cap()`` BECAUSE IT DESCRIBES A
    DIFFERENT QUANTITY, and a server that printed "Cap $300.00 per campaign"
    would be announcing a bound it does not run under. It prints on every start,
    uncapped included, for ``describe_cap()``'s reason: the dangerous state must
    not be the quiet one.
    """
    try:
        cap = serving_spend_cap()
    except SpendCapConfigurationError as exc:
        return f"[Spend] REFUSING TO READ THE SERVING CAP: {exc}"
    window = getattr(config, "SERVING_SPEND_WINDOW_SECONDS", None)
    if cap is None:
        return ("[Spend] NO SERVING SPEND CAP IS SET "
                "(config.SERVING_SPEND_CAP_USD is None). This server may spend "
                "without limit.")
    minutes = (f"{float(window) / 60.0:.0f} min"
               if isinstance(window, (int, float))
               and not isinstance(window, bool) and window > 0
               else "an unreadable window")
    if not config.SPEND_CAP_ENFORCED:
        return (f"[Spend] Serving cap ${cap:.2f} per {minutes} -- MEASURED "
                f"ONLY. config.SPEND_CAP_ENFORCED is False, so nothing will be "
                f"declined.")
    return (f"[Spend] Serving cap ${cap:.2f} per rolling {minutes}. Requests "
            f"are declined while the window is over budget and resume on their "
            f"own as it rolls -- no restart, no operator.")


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
    lines.append(f"  policy              {policy()}  "
                 f"(installed by {policy_source()})")
    seed = SPEND_LEDGER.seeded
    # THREE STATES, NOT TWO, AND THEY ARE DECIDED BEFORE ANYTHING IS PRINTED.
    # A cap that could not be READ is not a cap that is ABSENT, and the first
    # version of this function conflated them by testing `cap is not None`
    # afterwards -- so an unreadable cap printed its own diagnosis AND then
    # "NONE -- this run was unbounded" underneath it, two lines making
    # different claims about the same value.
    cap, cap_error = None, None
    try:
        # active_cap(), NOT spend_cap(): identical under the campaign policy
        # this block was written for, and correct for a serving process, which
        # would otherwise print the campaign cap it does not run under.
        cap = active_cap()
    except SpendCapConfigurationError as exc:
        cap_error = exc
    lines.append(f"  this process        ${SPEND_LEDGER.measured:.4f} "
                 f"over {SPEND_LEDGER.calls} billed call(s)")
    if seed.runs:
        lines.append(f"  inherited           ${seed.usd:.4f} "
                     f"over {seed.rows} row(s) from {seed.runs} prior run(s)"
                     + ("  <- A FLOOR, NOT A TOTAL" if seed.is_floor else ""))
    lines.append(f"  campaign total      ${SPEND_LEDGER.total:.4f}")
    # WHERE IT WENT. Printed only when more than one path spent, because on a
    # batch run every dollar is Stage 5's and a one-row breakdown under a total
    # it equals is noise -- but the moment a second path contributes, a reader
    # asked to act on the total needs to know which of the program's spends
    # moved it.
    _by = {k: v for k, v in SPEND_LEDGER.by_source().items() if v}
    if len(_by) > 1:
        _calls_by = SPEND_LEDGER.calls_by_source()
        for _src in sorted(_by, key=lambda k: (-_by[k], k)):
            lines.append(f"    {_src:<18}${_by[_src]:.4f}  over "
                         f"{_calls_by.get(_src, 0)} charge(s)")
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
    # WHAT THE FIGURE ABOVE DOES NOT BOUND. UNCONDITIONAL, on describe_cap()'s
    # argument: a reader handed a cap is owed, in the same block, the places it
    # does not reach. Naming them only when one of them ran would make the
    # silence read as coverage.
    lines.append(f"  NOT COVERED BY THE CAP -- {len(BILLED_SITE_EXEMPTIONS)} "
                 f"billed-looking site(s), each argued at "
                 f"spend.BILLED_SITE_EXEMPTIONS:")
    for _site in sorted(BILLED_SITE_EXEMPTIONS):
        lines.append(f"    {_site}")
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
