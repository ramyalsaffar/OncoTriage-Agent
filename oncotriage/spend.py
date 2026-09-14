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
``inferences.billing_attempts`` -- lives in ``oncotriage/storage/database_logger.py``, which owns
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
import uuid
from collections import Counter, deque
from typing import NamedTuple, Optional

from oncotriage import config
from oncotriage.observability import console, current_correlation_id, get_logger
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

BILLING_RECORD_FAULTS = Counter()
"""What the campaign's durable billing record could not do. See ``BILLING_RECORD``.

Keyed ``{phase}:{detail}``:

    ``reserve:{Type}``      a reservation could not be persisted, so the attempt
                            was NOT dispatched and the run latched.
    ``reserve:unpriced``    the reservation could not be priced -- the wire
                            model is absent from ``PRICING_CONFIG`` -- so the
                            attempt was not dispatched.
    ``refused_latched``     a later attempt refused without trying the write,
                            because the run is already latched.
    ``settle:{result}``     a settlement did not land cleanly: ``failed`` (the
                            row stays RESERVED at its upper bound, which is the
                            conservative reading), ``missing`` (no reservation
                            to settle) or ``conflict`` (already settled at a
                            different amount; the first settlement stands).
    ``settle:raised:{Type}`` the sink raised, which it is documented not to.

A ``settle:`` key never under-records: an unsettled reservation is charged at
its upper bound by every reader. A ``reserve:`` key never under-records either:
nothing was sent. What a non-zero total says is that the run stopped, or that a
resume will charge more than was billed.
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
SPEND_LIMIT_BILLING_RECORD = "billing_record"

SPEND_LIMITS = (SPEND_LIMIT_CAP, SPEND_LIMIT_CALL_CEILING,
                SPEND_LIMIT_BILLING_RECORD)
"""Which limit declined a request. CLOSED, and a caller may branch on it
exhaustively.

They are three findings with three remediations and must not be one key. The cap
means "this campaign has spent its budget" and is answered by raising the budget
or accepting the stop; the ceiling means "one Stage 5 invocation tried to issue
more calls than it can legitimately need" and is answered by reading the
traceback; ``billing_record`` means "the campaign's durable billing record can no
longer be trusted to count this run's charges, so no further billed request may
be dispatched" -- WHY is ``SpendStop.cause`` (``BILLING_RECORD_CAUSES``), and the
remedy depends on it. See ``BILLING_RECORD``.
"""

BILLING_RECORD_CAUSE_WRITE_FAILED = "write_failed"
BILLING_RECORD_CAUSE_UNPRICED = "unpriced"
BILLING_RECORD_CAUSE_DEFERRED = "deferred"
BILLING_RECORD_CAUSE_DISCREPANCY_UNRECORDED = "discrepancy_unrecorded"
BILLING_RECORD_CAUSE_MISSING = "missing"
BILLING_RECORD_CAUSE_CONFLICT = "conflict"
BILLING_RECORD_CAUSE_UNVERIFIED = "unverified"
BILLING_RECORD_CAUSES = (BILLING_RECORD_CAUSE_WRITE_FAILED,
                         BILLING_RECORD_CAUSE_UNPRICED,
                         BILLING_RECORD_CAUSE_DEFERRED,
                         BILLING_RECORD_CAUSE_DISCREPANCY_UNRECORDED,
                         BILLING_RECORD_CAUSE_MISSING,
                         BILLING_RECORD_CAUSE_CONFLICT,
                         BILLING_RECORD_CAUSE_UNVERIFIED)
"""Why a ``billing_record`` latch fired. CLOSED (P1c).

THE BANNER USED TO SAY "COULD NOT BE WRITTEN" FOR ALL OF THEM, which is false for
four: a reservation that could not be PRICED was never written at all; a row
that is MISSING or holds a CONFLICTING settlement was written and then changed by
something else; an UNVERIFIED settlement may well have been written. Each prints
its own sentence and its own remedy in ``SpendStop._latch``.

  ``write_failed``            a reservation could not be committed.
  ``unpriced``                a reservation could not be priced; not dispatched.
  ``deferred``                a settlement shortfall went to a marker file, not
                              the database.
  ``discrepancy_unrecorded``  a settlement shortfall reached neither.
  ``missing``                 a billing row this run committed is gone.
  ``conflict``                a row this run committed holds another settlement.
  ``unverified``              a settlement's stored outcome could not be read.
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

SEED_SOURCE_JOURNAL_RATER = "spend_journal_rater"
SEED_SOURCE_JOURNAL_CAMPAIGN = "spend_journal_campaign"
"""The CUMULATIVE readings, out of ``oncotriage/spend_journal.py``.

**TWO MEMBERS AND NOT ONE, BECAUSE ``BUDGET_FOR_SEED_SOURCE`` IS A TOTAL MAP
FROM SEED SOURCE TO BUDGET.** One ``spend_journal`` member could not be
assigned a budget: the journal records both, and which budget an entry belongs
to is a property of the ENTRY. Naming the budget in the seed source is what
keeps that table total and therefore checkable at import, which is worth more
than the small redundancy in the two names.

``rater_state`` IS RETAINED BESIDE THEM and is not dead: it is what
``rater.rater_spend_before`` still produces when the journal cannot be read at
all, which is the pre-journal behaviour and the under-enforcing direction, and
the banner then says which of the two answered.
"""

SEED_SOURCE_BILLING_RECORD = "billing_record"
"""A batch campaign resuming, seeded from its CUMULATIVE billing record.

``inferences.billing_attempts`` holds one row per billed wire attempt of the
campaign -- warmups, retries, abandoned requests and Stage 2's dense embedding
included -- each reserved before dispatch and settled against observed usage.
It REPLACES ``campaign_rows`` for the batch runner: that one summed
``inferences.estimated_cost_usd``, which describes a patient's FINAL Stage 5
attempt only, so every earlier attempt's charge was missing from a resumed
campaign's budget. ``campaign_rows`` stays a member because the ablation study
still seeds from its own database's rows under that name.
"""

SEED_SOURCES = (SEED_SOURCE_NONE, SEED_SOURCE_CAMPAIGN,
                SEED_SOURCE_RATER_STATE,
                SEED_SOURCE_JOURNAL_RATER, SEED_SOURCE_JOURNAL_CAMPAIGN,
                SEED_SOURCE_BILLING_RECORD)
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
# WHICH BUDGET GOVERNS WHICH PATH
# ===========================================================================

SPEND_BUDGET_CAMPAIGN = "campaign"
SPEND_BUDGET_RATER = "rater"

SPEND_BUDGETS = (SPEND_BUDGET_CAMPAIGN, SPEND_BUDGET_RATER)
"""The budgets this project's billed paths are bound by. CLOSED.

**BUDGETS ARE PER BILLED PROGRAM, NOT ONE NUMBER FOR ALL, AND THAT IS AN
OPERATOR RULING.** One cap over every path was the right first move -- it
closed the hole where a campaign and a judge could each spend the whole budget
and nothing would say so -- and it conflated two programs an operator runs and
stops separately. Under one number a campaign that ran long silently leaves the
judge nothing, and the judge's stop names a ledger belonging to a different
program: the hardest kind of stop to diagnose, because the cause is not in the
run that met it.

  ``campaign``  everything the evaluation campaign and the things that run
                beside it spend -- Stage 5, Stage 2's dense query embedding and
                both ragas paths. Cap: ``config.SPEND_CAP_USD``, or
                ``config.SERVING_SPEND_CAP_USD`` under the ``serving_window``
                policy (see ``SPEND_POLICIES``).
  ``rater``     the independent judge alone. Cap:
                ``config.RATER_SPEND_CAP_USD``.

**A BUDGET IS A CAP *AND* A MEASURE, AND SPLITTING ONLY THE CAP WOULD HAVE BEEN
WORSE THAN NOT SPLITTING.** ``budget_spend`` sums the ledger over THAT budget's
sources and adds the seed only when the seed belongs to it, so a judge run
after a campaign is not declined by money the campaign spent, and a campaign is
not charged for a judge. Giving the rater its own cap while still comparing it
against the whole ledger's total would have produced the exact defect this
ruling removes, wearing the costume of a fix.

WHY RAGAS IS UNDER ``campaign`` RATHER THAN BESIDE THE RATER, since both judge
and both reach Anthropic: the ruling is "every non-rater billed path keeps the
campaign cap", and the shapes agree with it -- ragas runs inside the evaluation
campaign's own harness against the campaign's runs, while the rater is a
separate invocation with a separate resume gesture and a separate state file.

THE TWO ARE STILL NOT NETTED AGAINST EACH OTHER ACROSS PROCESSES, and they
never were: a campaign seeds from the ``runs`` chain and a rater session from
``rater_state.json``, and no shared store exists that both write. What the
split changes is that this is now the DESIGN rather than an accident of which
process happened to run.
"""

BUDGET_FOR_SOURCE = {
    SPEND_SOURCE_STAGE5: SPEND_BUDGET_CAMPAIGN,
    SPEND_SOURCE_EMBEDDING: SPEND_BUDGET_CAMPAIGN,
    SPEND_SOURCE_RAGAS_JUDGE: SPEND_BUDGET_CAMPAIGN,
    SPEND_SOURCE_RAGAS_EMBEDDING: SPEND_BUDGET_CAMPAIGN,
    SPEND_SOURCE_RATER: SPEND_BUDGET_RATER,
}
"""Which budget each billed path is bound by. TOTAL over ``SPEND_SOURCES``.

TOTAL AND NOT DEFAULTED, enforced at import below. A source with no entry would
have to fall back to SOME budget, and whichever one was chosen would be a
guess about money made by the dispatch rather than by an operator -- the shape
``deps.OVERRIDE_KEYS`` refuses for a client and this refuses for a cap.
"""

BUDGET_SOURCES = {
    _b: tuple(_s for _s in SPEND_SOURCES if BUDGET_FOR_SOURCE[_s] == _b)
    for _b in SPEND_BUDGETS
}
"""``{budget: (source, ...)}``, DERIVED from the table above rather than typed.

It is the inverse of one mapping and a second hand-written copy is a second
chance for a path to be bound by one budget and measured against another --
which is a gate that declines the wrong program and never says so.
"""

BUDGET_FOR_SEED_SOURCE = {
    SEED_SOURCE_CAMPAIGN: SPEND_BUDGET_CAMPAIGN,
    SEED_SOURCE_RATER_STATE: SPEND_BUDGET_RATER,
    SEED_SOURCE_JOURNAL_RATER: SPEND_BUDGET_RATER,
    SEED_SOURCE_JOURNAL_CAMPAIGN: SPEND_BUDGET_CAMPAIGN,
    SEED_SOURCE_BILLING_RECORD: SPEND_BUDGET_CAMPAIGN,
}
"""Which budget a resumed baseline belongs to. TOTAL over ``SEED_SOURCES``
except ``fresh``, which belongs to none by construction -- it is a zero.

**THE SEED IS WHY A SHARED CAP WITH SPLIT LEDGERS WOULD STILL BE WRONG.** A
rater session seeded from ``rater_state.json`` carries a number about the
JUDGE; a campaign seeded from the ``runs`` chain carries one about the
CAMPAIGN. Adding either into the other budget's measure is the same
misattribution as charging one program's calls to the other, arriving through
the resume path instead of through the gate.
"""

BUDGET_CAP_CONSTANTS = {
    SPEND_BUDGET_CAMPAIGN: "config.SPEND_CAP_USD",
    SPEND_BUDGET_RATER: "config.RATER_SPEND_CAP_USD",
}
"""The constant an operator edits to move each budget. For MESSAGES only.

It exists because the remedy sentence in a refusal is the one thing an operator
acts on, and a refusal about the judge that named ``config.SPEND_CAP_USD``
would send them to raise a cap that had nothing to do with the stop. The
``serving_window`` policy's own constant is deliberately not in this table --
that is a POLICY over the campaign budget rather than a third budget, and
``describe_serving_cap()`` is where it is named.
"""


def _assert_budget_tables_total():
    """Refuse at import if a source or a seed source has no budget.

    A ``RuntimeError`` and NOT an ``assert``: ``python -O`` deletes assertions,
    and this is the guard that keeps a billed path from being bound by
    whichever budget a ``.get`` default happened to name.
    """
    missing = sorted(set(SPEND_SOURCES) - set(BUDGET_FOR_SOURCE))
    extra = sorted(set(BUDGET_FOR_SOURCE) - set(SPEND_SOURCES))
    if missing or extra:
        raise RuntimeError(
            f"spend.BUDGET_FOR_SOURCE must name every SPEND_SOURCES member and "
            f"nothing else; missing {missing!r}, unknown {extra!r}. A billed "
            f"path with no declared budget would be bound by a default nobody "
            f"chose.")
    unknown = sorted({b for b in BUDGET_FOR_SOURCE.values()
                      if b not in SPEND_BUDGETS})
    if unknown:
        raise RuntimeError(
            f"spend.BUDGET_FOR_SOURCE names budget(s) {unknown!r} that are not "
            f"in SPEND_BUDGETS {SPEND_BUDGETS!r}.")
    empty = sorted(b for b in SPEND_BUDGETS if not BUDGET_SOURCES[b])
    if empty:
        raise RuntimeError(
            f"budget(s) {empty!r} govern no billed path. A budget with no "
            f"source is a cap that can never be reached and a report line that "
            f"can never be anything but zero.")
    seed_expected = set(SEED_SOURCES) - {SEED_SOURCE_NONE}
    if set(BUDGET_FOR_SEED_SOURCE) != seed_expected:
        raise RuntimeError(
            f"spend.BUDGET_FOR_SEED_SOURCE must name every SEED_SOURCES member "
            f"except {SEED_SOURCE_NONE!r}; it names "
            f"{sorted(BUDGET_FOR_SEED_SOURCE)!r} against "
            f"{sorted(seed_expected)!r}. A resumed baseline with no declared "
            f"budget would be added to the wrong program's spend.")
    seed_unknown = sorted({b for b in BUDGET_FOR_SEED_SOURCE.values()
                           if b not in SPEND_BUDGETS})
    if seed_unknown:
        raise RuntimeError(
            f"spend.BUDGET_FOR_SEED_SOURCE names budget(s) {seed_unknown!r} "
            f"that are not in SPEND_BUDGETS {SPEND_BUDGETS!r}.")
    if set(BUDGET_CAP_CONSTANTS) != set(SPEND_BUDGETS):
        raise RuntimeError(
            f"spend.BUDGET_CAP_CONSTANTS must name every budget; it names "
            f"{sorted(BUDGET_CAP_CONSTANTS)!r} against "
            f"{sorted(SPEND_BUDGETS)!r}. A refusal that could not name the "
            f"constant an operator edits is a refusal with no remedy.")


_assert_budget_tables_total()


def budget_for(source: str) -> str:
    """Which budget governs this billed path. ONE OWNER.

    RAISES on an unrecognised source rather than defaulting, on
    ``set_policy``'s footing: a source read as "the campaign budget" because
    nobody declared it is a path spending a budget it was never given.
    """
    try:
        return BUDGET_FOR_SOURCE[source]
    except KeyError:
        raise SpendCapConfigurationError(
            f"{source!r} is not a billed path this module knows about. The "
            f"closed vocabulary is {SPEND_SOURCES!r}, and every member is "
            f"assigned a budget at spend.BUDGET_FOR_SOURCE. A path with no "
            f"budget cannot be gated, and reading it as 'the campaign' would "
            f"charge one program for another's calls.") from None


def seed_budget(seed) -> Optional[str]:
    """Which budget a ``LedgerSeed`` belongs to, or None for a fresh one."""
    return BUDGET_FOR_SEED_SOURCE.get(getattr(seed, "source", None))


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
    ``unreadable``  how many items on this reading's record could NOT be read:
                 a journal line that would not decode or parse, an entry whose
                 kind or amount is unusable, a journal file that could not be
                 opened, a state file the fallback could not read. **A non-zero
                 value makes ``usd`` UNVERIFIED** -- the skipped items may carry
                 money, so any remainder computed from ``usd`` is potentially
                 OVERSTATED -- and it is NOT a floor in ``unpriced``'s sense,
                 because nothing proves an unreadable item is non-negative
                 money. The two are kept apart for that reason.
    ``unreadable_reasons``  the first few of those items, named, for the
                 refusal an operator reads. Bounded; ``unreadable`` is the count.
    ``unresolved``  ``billing_record`` seeds only: how many of ``rows`` are
                 reservations that were never settled -- an attempt interrupted
                 between dispatch and settlement. Each is inside ``usd`` at its
                 RESERVED upper bound, so a non-zero value makes ``usd`` a
                 CEILING on those attempts rather than a floor: conservative.
    """

    usd: float = 0.0
    rows: int = 0
    unpriced: int = 0
    runs: int = 0
    source: str = SEED_SOURCE_NONE
    unreadable: int = 0
    unreadable_reasons: tuple = ()
    unresolved: int = 0

    @property
    def is_floor(self) -> bool:
        """Is ``usd`` a floor rather than a total?"""
        return self.unpriced > 0

    def has_unreadable(self) -> bool:
        """Did this reading skip anything it could not read?

        A METHOD AND NOT A PROPERTY, deliberately: the decorator inventory in
        ``tests/test_package_invariants.py`` pins every property in the package,
        and this answer needs no attribute syntax to be read correctly.
        """
        return self.unreadable > 0


UNREADABLE_REASONS_KEPT = 10
"""How many unreadable items a reading NAMES. The count is always exact; the
names are for a refusal line, and a torn journal of ten thousand lines must not
print ten thousand of them."""


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
        # {budget: [reason, ...]} -- what this process could NOT read about a
        # budget's record. See `mark_unverified`.
        self._unverified = {}
        self._unverified_counts = Counter()
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
        cost, fault = price_usage(model, prompt_tokens, completion_tokens)
        if cost is None:
            SPEND_LEDGER_FAULTS[fault] += 1
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
        split is what lets a budget govern billed paths priced by four
        different tables. WHICH budget is `BUDGET_FOR_SOURCE`'s answer.

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
            # THE SOURCE RIDES WITH THE EVENT, which is what makes the
            # rolling window answerable PER BUDGET. Without it a serving
            # process's window would be one number over every path, and the
            # campaign budget's window would silently include a charge from
            # another budget -- the same misattribution the per-source
            # breakdown removes for the total.
            self._events.append((now, cost, source))
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

    def window_spend(self, seconds=None, sources=None) -> float:
        """What this process has been billed within the last ``seconds``.

        ``None`` asks ``config.SERVING_SPEND_WINDOW_SECONDS``. Prunes first, so
        the number is current rather than as-of the last charge -- which matters
        for exactly the case the window exists for: a server that has been idle
        long enough for its window to empty must be able to serve again WITHOUT
        a request having to arrive to trigger the pruning.

        Args:
            sources: restrict to these ``SPEND_SOURCES`` members. ``None`` --
                what ``GET /health`` passes -- is every path, which is the
                honest answer to "what has this process billed lately".
                ``budget_spend`` passes one budget's sources, because a budget
                may not be enforced against another budget's money.

        AN UNREADABLE WINDOW FALLS BACK TO THE UNWINDOWED MEASURE, which is the
        over-reporting and therefore over-enforcing direction -- and it is now
        restricted to ``sources`` as well, because a fallback that quietly
        widened to every path would make a typo in one constant charge one
        budget for another's spend.
        """
        if seconds is None:
            seconds = getattr(config, "SERVING_SPEND_WINDOW_SECONDS", None)
        keep = None if sources is None else set(sources)
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            if keep is None:
                return self._measured
            with self._lock:
                return sum(v for k, v in self._by_source.items() if k in keep)
        if seconds <= 0:
            return 0.0
        now = time.monotonic()
        cut = now - float(seconds)
        with self._lock:
            self._prune(now)
            return sum(cost for stamp, cost, src in self._events
                       if stamp >= cut and (keep is None or src in keep))

    def window_events(self, seconds, sources=None) -> list:
        """``[(monotonic, usd), ...]`` inside the window, OLDEST FIRST. A copy.

        THE ONE QUESTION THE SUMS CANNOT ANSWER, which is why it is a reader of
        its own rather than ``seconds_until_under_cap`` reaching into the
        deque: that function needs the events IN ORDER to find which one has to
        age out, and a caller iterating the live deque while workers append is
        the ``RuntimeError`` ``by_source()`` takes the lock to avoid. It was a
        documented private-attribute reach until the budget split needed it
        filtered as well; a filter written at the reach would have been a
        second place to forget it.
        """
        keep = None if sources is None else set(sources)
        now = time.monotonic()
        cut = now - float(seconds)
        with self._lock:
            return [(stamp, cost) for stamp, cost, src in self._events
                    if stamp >= cut and (keep is None or src in keep)]

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
        # AN UNREADABLE SEED MARKS ITS BUDGET, and the mark is ADDED rather
        # than replaced: `mark_unverified` may already have recorded something
        # the seed did not read (a state-file migration), and a seed that read
        # cleanly must not erase that.
        if isinstance(seed, LedgerSeed) and seed.has_unreadable():
            self.mark_unverified(seed_budget(seed), seed.unreadable_reasons,
                                 count=seed.unreadable)

    def mark_unverified(self, budget, reasons, count=None) -> None:
        """Record that part of ``budget``'s spend record could not be read.

        NEVER RAISES. ``budget`` None -- a seed attributed to no budget -- marks
        EVERY budget, because an item whose budget cannot be read cannot be
        ruled out of any of them. ``count`` defaults to ``len(reasons)``; a
        caller that kept only the first few names passes the true count.
        """
        names = [str(r) for r in (reasons or ())]
        n = len(names) if count is None else int(count)
        if n <= 0:
            return
        targets = (SPEND_BUDGETS if budget not in SPEND_BUDGETS
                   else (budget,))
        with self._lock:
            for b in targets:
                kept = self._unverified.setdefault(b, [])
                for name in names:
                    if len(kept) < UNREADABLE_REASONS_KEPT:
                        kept.append(name)
                self._unverified_counts[b] += n

    def unverified(self, budget) -> tuple:
        """``(count, reasons)`` for ``budget``; ``(0, ())`` when clean."""
        with self._lock:
            return (int(self._unverified_counts.get(budget, 0)),
                    tuple(self._unverified.get(budget, ())))

    def reset(self) -> None:
        """Forget everything an earlier run in this process spent."""
        with self._lock:
            self._measured = 0.0
            self._calls = 0
            self._seed = LedgerSeed()
            self._by_source.clear()
            self._calls_by_source.clear()
            self._events.clear()
            self._unverified.clear()
            self._unverified_counts.clear()

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


def price_usage(model, prompt_tokens, completion_tokens):
    """``(usd, None)`` for one response's usage, or ``(None, fault_key)``. PURE.

    THE ONE PRICING OF A BILLED RESPONSE. ``SpendLedger.charge`` and the durable
    billing record's settlement both call this, so the in-process ledger and the
    cross-process record cannot price the same response differently -- two
    copies of a pricing rule is how the resume figure and the cap it resumes
    under would come to disagree with nothing raising.

    ``model`` None falls back to ``config.matching_wire_model()``; see
    ``SpendLedger.charge`` for why that is exact in the one case it is reached.
    The fault key is ``SPEND_LEDGER_FAULTS``' vocabulary; counting is the
    caller's, because the two callers count into different counters.
    """
    _in = _as_token_count(prompt_tokens)
    _out = _as_token_count(completion_tokens)
    if _in is None or _out is None:
        return None, (f"bad_usage:{type(prompt_tokens).__name__}/"
                      f"{type(completion_tokens).__name__}")
    try:
        return (get_model_cost(model or config.matching_wire_model(), _in, _out),
                None)
    except UnknownModelPricingError:
        return None, f"unpriced_model:{model}"


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


def rater_spend_cap() -> Optional[float]:
    """The cap ONE JUDGE SESSION runs under, or None. ONE OWNER.

    ``spend_cap()``'s validation applied to ``config.RATER_SPEND_CAP_USD``, and
    a separate function for ``serving_spend_cap()``'s reason: the two bound
    different programs on different price tables, and a shared resolver would
    make a message about one able to name the other's constant -- which is the
    one sentence in a refusal an operator acts on.
    """
    cap = getattr(config, "RATER_SPEND_CAP_USD", None)
    if cap is None:
        return None
    if isinstance(cap, bool) or not isinstance(cap, (int, float)):
        raise SpendCapConfigurationError(
            f"config.RATER_SPEND_CAP_USD must be a number of US dollars or "
            f"None for no cap; it is {cap!r} ({type(cap).__name__}). A value "
            f"that is not a number cannot be read as 'unlimited' -- that "
            f"reading is how a typo becomes an unbounded judge session.")
    if cap < 0:
        raise SpendCapConfigurationError(
            f"config.RATER_SPEND_CAP_USD is {cap!r}. A negative cap is not "
            f"'unlimited'; set it to None if that is what you mean, which "
            f"prints a line on every judge banner saying so.")
    return float(cap)


def budget_cap(budget: str) -> Optional[float]:
    """The cap this BUDGET is enforced against, or None. May raise.

    THE POLICY REACHES THE CAMPAIGN BUDGET AND NOT THE RATER'S, and that is a
    decision rather than an omission. ``serving_window`` exists because a
    long-lived server's spend is a RATE; a server charges ``stage5`` and
    nothing else -- ``SPEND_SOURCE_RATER`` is charged at exactly one call site,
    in ``oncotriage/evaluation/rater.py``, which is not reachable from
    ``oncotriage/api/server.py`` or ``mcp_server.py``. So a windowed rater cap
    would be a rate nobody has ruled on, guarding a case that cannot occur; the
    monotone shape it keeps is the STRICTER of the two if it ever did.
    """
    if budget == SPEND_BUDGET_RATER:
        return rater_spend_cap()
    if policy() == SPEND_POLICY_WINDOW:
        return serving_spend_cap()
    return spend_cap()


def budget_spend(budget: str) -> float:
    """What this BUDGET has been billed, under the policy in force.

    **THE MEASURE IS SPLIT AS WELL AS THE CAP, AND SPLITTING ONLY THE CAP WOULD
    HAVE BEEN A DEFECT WEARING A FIX'S COSTUME.** A $50 judge cap compared
    against a ledger holding $250 of campaign Stage 5 declines the judge's
    first batch every time -- for money another program spent, which is the
    conflation the ruling removes.

    THE SEED IS ATTRIBUTED, NOT ADDED. ``LedgerSeed.source`` says which chain a
    resumed baseline was read from, and ``BUDGET_FOR_SEED_SOURCE`` says which
    budget that chain belongs to; a seed belonging to another budget
    contributes ZERO here rather than a number about a different program.
    """
    sources = BUDGET_SOURCES[budget]
    if budget != SPEND_BUDGET_RATER and policy() == SPEND_POLICY_WINDOW:
        return SPEND_LEDGER.window_spend(sources=sources)
    seed = SPEND_LEDGER.seeded
    base = seed.usd if seed_budget(seed) == budget else 0.0
    by = SPEND_LEDGER.by_source()
    return base + sum(by.get(src, 0.0) for src in sources)


def active_cap(source: str) -> Optional[float]:
    """The cap this billed path is enforced against, or None. May raise.

    ``source`` IS REQUIRED AND HAS NO DEFAULT, on ``empty_database(db_path,
    flag)``'s footing. A default would name a budget, and the one thing this
    split exists to prevent is a path being bound by a budget nobody chose --
    which is precisely what a caller who forgot the argument would get, and
    would get silently.
    """
    return budget_cap(budget_for(source))


def active_spend(source: str) -> float:
    """The quantity ``active_cap(source)`` is compared against.

    THE TWO POLICIES MEASURE DIFFERENT QUANTITIES AND THIS IS WHERE THAT LIVES.
    Campaign: the seeded baseline plus everything this process has been billed
    ON THAT BUDGET'S PATHS, monotone. Window: only what those paths were billed
    inside the last ``SERVING_SPEND_WINDOW_SECONDS``, which can go DOWN -- and
    going down is the whole point, because it is what lets a server recover on
    its own.
    """
    return budget_spend(budget_for(source))


def cap_exceeded(source: str) -> bool:
    """Has this billed path\'s BUDGET been spent, under the policy in force?

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
        cap = active_cap(source)
    except SpendCapConfigurationError:
        return False
    if cap is None:
        return False
    return active_spend(source) >= cap


def remaining(source: str) -> Optional[float]:
    """This path\'s BUDGET left, in US dollars, or None when there is no cap.

    May be negative, and that is not clamped: a reader is entitled to see the
    overshoot rather than a zero that hides it.
    """
    try:
        cap = active_cap(source)
    except SpendCapConfigurationError:
        return None
    if cap is None:
        return None
    return cap - active_spend(source)


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


SPEND_RECORD_UNVERIFIED = "spend_record_unverified"
"""The refusal code for a paid dispatch declined because the budget's own spend
record could not be fully read. One spelling, read by the gate, by the rater's
two refusal sites, by the ragas harness and by the rater's manifest."""


class SpendRecordUnverified(RuntimeError):
    """A billed request was NOT issued: its budget's record is UNVERIFIED.

    **NOT A ``SpendLimitReached`` SUBCLASS, AND THAT IS A VOCABULARY DECISION.**
    ``SpendLimitReached.limit`` is ``SPEND_LIMITS``, a CLOSED tuple that Stage 5,
    the batch runner and the ablation study branch on exhaustively. No limit was
    reached here -- the remaining budget could not be established -- so putting
    a third value into ``.limit`` would break that promise, and reusing
    ``SPEND_LIMIT_CAP`` would tell an operator to raise a cap that is not the
    problem. Every caller that can meet this catches it by name.

    It is raised BEFORE any request is issued and it never latches
    ``SPEND_STOP``: a record an operator repairs is not a budget that ran out,
    and a latch would outlive the repair for the rest of the process.
    """

    def __init__(self, message, budget=None, source=None, reasons=(),
                 count=0):
        super().__init__(message)
        self.code = SPEND_RECORD_UNVERIFIED
        self.budget = budget
        self.source = source
        self.reasons = tuple(reasons)
        self.count = count


def unverified_record_refusal(source: str) -> Optional[str]:
    """The refusal text when ``source``'s budget record is unverified, or None.

    NEVER RAISES. It answers None -- no refusal -- in three cases, and the
    first two are decisions rather than omissions:

      * enforcement is off (``config.SPEND_CAP_ENFORCED`` False) -- the
        operator has put every budget in measurement mode, and a refusal
        computed from a remainder nothing enforces would be the only brake
        still working;
      * the budget has NO CAP in force (or its cap cannot be read, which
        ``require_budget``'s own caller reports) -- there is no remainder for a
        skipped item to overstate, so no dispatch decision rests on it;
      * nothing on the record was unreadable.

    THE REMAINDER IT QUOTES IS CALLED UNVERIFIED AND POTENTIALLY OVERSTATED,
    NEVER AN UPPER BOUND. It would be an upper bound on the true remainder only
    if every unreadable item were non-negative spend, and an item that could not
    be read cannot be shown to be anything.
    """
    try:
        budget = budget_for(source)
    except Exception:                                           # noqa: BLE001
        return None
    count, reasons = SPEND_LEDGER.unverified(budget)
    if count <= 0 or not config.SPEND_CAP_ENFORCED:
        return None
    try:
        cap = budget_cap(budget)
    except SpendCapConfigurationError:
        return None
    if cap is None:
        return None
    left = cap - budget_spend(budget)
    shown = "; ".join(reasons) or "no item was named"
    more = (f" (and {count - len(reasons)} more)"
            if count > len(reasons) else "")
    return (f"{SPEND_RECORD_UNVERIFIED}: {count} item(s) on the {budget} "
            f"budget's spend record could not be read -- {shown}{more}. The "
            f"remaining budget shown (${left:.2f} of ${cap:.2f}) is UNVERIFIED "
            f"and potentially OVERSTATED: the unreadable items may carry spend "
            f"it does not count. NO NEW PAID REQUEST IS ISSUED on this budget. "
            f"Already-submitted work is still collected. Nothing unreadable was "
            f"deleted or rewritten; repair or move the named item(s) and run "
            f"again.")


def seconds_until_under_cap(source: str) -> Optional[float]:
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
    budget = budget_for(source)
    if budget == SPEND_BUDGET_RATER:
        # THE RATER BUDGET DOES NOT HEAL WITH TIME, because `budget_cap` keeps
        # it monotone under every policy -- see the argument there. Returning a
        # number here would tell a caller to wait for something that will not
        # happen, which is what the `None` for the call-ceiling limit already
        # means one caller up.
        return None
    try:
        cap = budget_cap(budget)
    except SpendCapConfigurationError:
        return None
    if cap is None:
        return None
    width = getattr(config, "SERVING_SPEND_WINDOW_SECONDS", None)
    if isinstance(width, bool) or not isinstance(width, (int, float)) \
            or width <= 0:
        return None
    now = time.monotonic()
    # THE EVENTS OF THIS BUDGET ALONE, IN ORDER. `window_events` is the public
    # reader for the one question the sums cannot answer; it takes the ledger's
    # lock, so a caller cannot iterate the deque while workers append.
    events = SPEND_LEDGER.window_events(float(width),
                                        sources=BUDGET_SOURCES[budget])
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
    budget = budget_for(source)
    # ── AN UNVERIFIED RECORD REFUSES FIRST ────────────────────────────────
    #
    # Above the cap comparison because that comparison reads the very spend
    # figure that is in doubt: a session "under its cap" on a record with
    # unreadable items may not be. Raised, not latched -- see
    # `SpendRecordUnverified`.
    _unverified = unverified_record_refusal(source)
    if _unverified is not None:
        _count, _reasons = SPEND_LEDGER.unverified(budget)
        log.warning("a billed request was not issued because its budget's "
                    "spend record could not be fully read", status="refused",
                    event="spend_record_unverified", phase=source,
                    reason=SPEND_RECORD_UNVERIFIED, mode=where, degraded=True)
        raise SpendRecordUnverified(
            f"the request was not issued ({where}): {_unverified}",
            budget=budget, source=source, reasons=_reasons, count=_count)
    if not cap_exceeded(source):
        return
    SPEND_GATE_SKIPS[f"{source}:{SPEND_LIMIT_CAP}"] += 1
    if latch_on_limit() if latch is None else latch:
        SPEND_STOP.poll(where=where, source=source)
    try:
        cap = active_cap(source)
    except SpendCapConfigurationError:
        cap = None
    spent = active_spend(source)
    log.warning("a billed request was not issued because a spend limit was "
                "reached", status="stopped", event="spend_limit_declined",
                phase=source, reason=SPEND_LIMIT_CAP, mode=where,
                cost_usd=round(spent, 6),
                threshold=(round(cap, 6) if cap is not None else None),
                degraded=True)
    # THE MESSAGE NAMES THE BUDGET THAT STOPPED IT AND THE CONSTANT THAT MOVES
    # THAT BUDGET. A judge session refused with "raise config.SPEND_CAP_USD"
    # sends an operator to a cap that had nothing to do with the stop -- the
    # same misdiagnosis `_stop_reason_now()` avoids by putting the operator's
    # own request first, reached through a budget instead of through a switch.
    raise SpendLimitReached(
        f"the request was not issued: the {budget} budget has spent "
        f"${spent:.2f} against a {policy()} limit of "
        f"{'no cap' if cap is None else f'${cap:.2f}'} "
        f"({BUDGET_CAP_CONSTANTS[budget]})",
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
    # THE SINGLE-ATTEMPT SENDERS, SINCE THE PROVIDER-RESILIENCE PASS. The
    # billed attribute access moved one frame down: `call_matching_model` and
    # its warmup now wrap these in the one retry policy, and the node's three
    # call sites -- which the gate brackets -- still call the wrappers.
    "oncotriage/agent/evaluation.py::_send_matching_call": (
        DISPOSITION_GATED_UPSTREAM,
        "oncotriage/agent/evaluation.py::_spend_gate",
        "The Stage 5 node brackets all three of its billed call sites -- the "
        "gate immediately before the request and the charge immediately "
        "after -- which is what bounds the overshoot at the requests in "
        "flight rather than at a whole patient's wave. Gating inside this "
        "function as well would decline the same request twice and would "
        "break the counter's phase keys, which name WHICH of the three sites "
        "declined."),
    "oncotriage/agent/evaluation.py::_send_matching_warmup_call": (
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
        "The independent judge, on the OpenAI Batch API -- priced from "
        "`config.RATER_PRICING` and charged through "
        "`rater.charge_batch_to_ledger`. Gated PER CHUNK rather than once "
        "before the loop, so the overshoot is one batch. AND THE GATE IS NO "
        "LONGER THE ONLY BRAKE: `rater.require_reservation_fits` runs before "
        "this function is entered and refuses a submission whose WORST CASE "
        "cannot fit, because a batch reports no usage until it is collected "
        "and a measured gate therefore cannot stop the batch that broke the "
        "cap -- only the next one."),
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
    # ── TWO ENTRIES WERE REMOVED WHEN THE JUDGE MOVED TO OPENAI, AND THE
    # ── REASON IS THIS TABLE'S OWN CONTRACT RATHER THAN A JUDGEMENT ABOUT
    # ── WHETHER THEY MATTER.
    #
    # `oncotriage/evaluation/rater.py::calibrate_chars_per_token` was exempt
    # and named Anthropic's free `/v1/messages/count_tokens`. OpenAI publishes
    # no equivalent, so the replacement encodes locally with tiktoken and calls
    # nothing at all. A site that touches no endpoint is not a billed site.
    #
    # `oncotriage/evaluation/rater.py::model_is_visible` -- the free
    # `models.retrieve` visibility probe added with the port -- is
    # DELIBERATELY NOT DECLARED HERE, and it was tried and taken out. This
    # table is checked in BOTH directions: `tests/test_spend_coverage.py` 1a
    # requires every DERIVED site to be declared, and 1b requires every
    # DECLARED site to be derivable. The derivation matches on billed-endpoint
    # attribute suffixes, `models.retrieve` is not one, and adding it to that
    # list to admit this entry would make the scan report a zero-cost
    # visibility probe as a billed site everywhere it appears. The old entry's
    # own argument -- "a reader auditing the gate WILL find it" -- rested on
    # the site NAMING a billed attribute, which this one does not.
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
    "bedrock_probe.py::_probe_output_tokens": (
        DISPOSITION_EXEMPT, None,
        "THE PER-TRIAL OUTPUT-TOKEN MEASUREMENT, and it is exempt for "
        "`bedrock_probe.py::main`'s reason with one of its own on top. It is "
        "behind BOTH `--i-understand-this-bills` and `--probe-output-tokens`, "
        "it issues exactly one call per `--per-trial-user-file` and refuses "
        "before any of them when either input is missing, and what it MEASURES "
        "is `MATCHING_OUTPUT_TOKENS_PER_TRIAL` -- the constant Stage 5's "
        "pre-split guard is built from. It was derived on gpt-5.6-terra and "
        "was re-derived on the shipped judge by THIS phase on 2026-09-03; the "
        "exemption stands because the constant's own block requires the same "
        "re-derivation on every model, provider or effort change, so this is a "
        "recurring measurement rather than a settled one. A cap able to refuse "
        "it would be a cap refusing the measurement that tells an operator how "
        "big one verdict is."),
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

      PER-TRIAL MODE -- one cache warmup, then one call per candidate trial,
        each of which may be asked again once if it answered with an empty
        array:

          1 + MAX_TRIALS_FOR_EVALUATION x (1 + MATCHING_PER_TRIAL_EMPTY_RETRIES)

        and nothing else, because a per-trial chunk is a SINGLETON and
        ``_split_in_half`` refuses to halve one. Measured rather than assumed:
        driving every response to ``finish_reason == "length"`` produces four
        wave calls and ZERO truncation splits.

        THE EMPTY-VERDICT RETRY TERM IS NOT DECORATION AND IT IS NOT A MARGIN.
        ``oncotriage/agent/evaluation.py`` re-queues a per-trial chunk whose
        reply PARSED to an empty list, so that trial's second request is a call
        the configuration permits -- and without this term it would be the
        17th call of a 16-call ceiling on a full-cap patient. That refusal
        LATCHES THE WHOLE RUN (see ``SPEND_LIMIT_CALL_CEILING``), so the first
        empty verdict of a campaign would stop the campaign, reported as a
        pipeline defect, for a call nothing was wrong with. At
        ``MATCHING_PER_TRIAL_EMPTY_RETRIES = 0`` the term vanishes and this is
        the expression it was before the mechanism existed.

        WHAT IT COSTS AS A DEFECT DETECTOR, STATED. The ceiling doubles, 16 to
        31, so a loop inside one invocation has twice as far to run before it
        trips. That is the price of the ceiling staying EXACT: a margin would
        be a literal, and a ceiling below what the configuration permits is a
        false defect report, which is strictly worse than a later true one.

      GROUPED MODE -- the packer emits at most
        ``MATCHING_MAX_INPUT_PACKED_CHUNKS`` chunks and the reactive splitter
        may halve each of them to depth ``MAX_TRUNCATION_SPLITS``, which issues

          2 ** (MAX_TRUNCATION_SPLITS + 1) - 1

        requests for one chunk -- the identical expression
        ``HARNESS_POST_READ_TIMEOUT_SECONDS`` is already written over, for the
        identical reason: raising the split depth must move this with it. So

          MATCHING_MAX_INPUT_PACKED_CHUNKS x (2 ** (MAX_TRUNCATION_SPLITS + 1) - 1)

    AT THE SHIPPED CONSTANTS: 31 per-trial (1 + 15 x 2), 75 grouped.

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
        return (1 + config.MAX_TRIALS_FOR_EVALUATION
                * (1 + config.MATCHING_PER_TRIAL_EMPTY_RETRIES))
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
        self.budget = None
        self.cause = None

    def reset(self) -> None:
        """Forget a limit reached by an earlier run in this process."""
        with self._lock:
            self.requested = False
            self.limit = None
            self.detected_in = None
            self.spent = None
            self.cap = None
            self.budget = None
            self.cause = None

    def trip(self, limit: str, where: str, source: str,
             cause: "str | None" = None) -> bool:
        """Latch on a limit a CALL SITE has already decided. Announces once.

        Used by the Stage 5 call-ceiling gate, which reaches its verdict from a
        per-invocation counter this object cannot see.

        ``source`` IS REQUIRED HERE TOO even though the ceiling is not a budget
        event, because the block this prints reports a spend figure beside it
        and that figure has to be about ONE budget. A ceiling trip announcing
        the campaign's total inside a judge session would be a true number
        about the wrong program.

        ``cause`` (P1c) says WHY a ``billing_record`` latch fired, from
        ``BILLING_RECORD_CAUSES``; the banner's sentence and remedy depend on it.
        Ignored for the other limits. An unrecognised or absent cause prints a
        sentence that claims no mechanism.
        """
        return self._latch(limit, where, source, cause)

    def poll(self, where: str, source: str) -> bool:
        """Has a spend limit been reached? Reads the ledger.

        Args:
            where: which pass noticed. Free text; it never reaches a durable
                store and every caller passes a literal.
            source: the billed path being asked about, which selects the
                BUDGET. Required and with no default, on ``active_cap``'s
                footing: a default would name a budget, and this latch stops a
                whole run.

        THE LATCH IS SHARED ACROSS BUDGETS AND THAT IS DELIBERATE. One process
        runs one program -- a campaign, a study, or a judge -- so at most one
        budget can be reached in it, and a latch per budget would be state
        nothing could distinguish while making "has this run stopped" two
        questions for every caller on the hot path. What the latch RECORDS is
        per budget: ``budget``, ``spent`` and ``cap`` all describe the one that
        stopped it.
        """
        with self._lock:
            if self.requested:
                return True
        if not cap_exceeded(source):
            return False
        return self._latch(SPEND_LIMIT_CAP, where, source)

    def _latch(self, limit: str, where: str, source: str,
               cause: "str | None" = None) -> bool:
        with self._lock:
            if self.requested:
                return True
            self.requested = True
            self.limit = limit
            self.detected_in = where
            self.cause = cause if limit == SPEND_LIMIT_BILLING_RECORD else None
            try:
                self.budget = budget_for(source)
            except SpendCapConfigurationError:
                self.budget = None
            if self.budget is None:
                self.spent, self.cap = SPEND_LEDGER.total, None
            else:
                self.spent = budget_spend(self.budget)
                try:
                    self.cap = budget_cap(self.budget)
                except SpendCapConfigurationError:
                    self.cap = None
            _spent, _cap = self.spent, self.cap
            _budget = self.budget
            _cause = self.cause

        # OUTSIDE THE LOCK, for control.StopSwitch.poll's reason: the console
        # writer and the logger take locks of their own and this is reached
        # from MAX_WORKERS done-callbacks at once. Holding a lock across a
        # write to a bar-aware writer is how a shutdown path deadlocks.
        rule = "=" * 80
        console.out()
        console.out(rule)
        if limit == SPEND_LIMIT_CAP:
            # `_cap` CANNOT BE None ON THIS BRANCH and the formatting relies on
            # it: `poll()` only reaches here through `cap_exceeded()`, which is
            # False whenever the cap is absent or unreadable. Stated rather
            # than guarded, because a guard would suggest the state is
            # reachable and a reader would go looking for how.
            console.out(f"[SPEND] THE {_budget.upper()} SPEND CAP HAS BEEN "
                        f"REACHED: ${_spent:.2f} of ${_cap:.2f}")
        elif limit == SPEND_LIMIT_BILLING_RECORD:
            # NOT A BUDGET EVENT AND NOT A DEFECT IN THE PIPELINE: the record
            # that makes a resumed campaign's budget true can no longer be
            # trusted to count this run's charges, so dispatching further would
            # spend money a later process cannot count. WHY varies, and so does
            # the remedy (P1c): this block used to say "COULD NOT BE WRITTEN"
            # for every cause, which is false for a missing row, a conflicting
            # settlement, an unverified one and an unpriced reservation.
            _what, _remedy = _BILLING_RECORD_BANNER.get(
                _cause, _BILLING_RECORD_BANNER[None])
            console.out(f"[SPEND] {_what}; no further billed request may be "
                        f"dispatched.")
            console.out(f"[SPEND] {_budget or 'campaign'} spend so far: "
                        f"${_spent:.2f}. {_remedy}")
        else:
            console.out("[SPEND] A STAGE 5 INVOCATION HIT ITS BILLED-CALL "
                        "CEILING.")
            console.out(f"[SPEND] {_budget or 'campaign'} spend so far: "
                        f"${_spent:.2f}")
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
        if limit != SPEND_LIMIT_BILLING_RECORD:
            # NOT FOR A BILLING-RECORD STOP, which no cap change fixes (P1c): its
            # remedy is printed above, per cause.
            console.out(f"[SPEND] To continue, raise "
                        f"{BUDGET_CAP_CONSTANTS.get(_budget, 'config.SPEND_CAP_USD')} "
                        f"and run again -- a resumed run counts what this one "
                        f"spent.")
        console.out(rule)
        log.warning("a spend limit stopped the run",
                    event="spend_limit_reached", status="stopped",
                    mode=where, reason=limit, phase=_budget, degraded=True,
                    cost_usd=round(_spent, 6),
                    threshold=(round(_cap, 6) if _cap is not None else None))
        return True


_BILLING_RECORD_BANNER = {
    BILLING_RECORD_CAUSE_WRITE_FAILED: (
        "A BILLED ATTEMPT'S RESERVATION COULD NOT BE WRITTEN TO THE CAMPAIGN'S "
        "DURABLE BILLING RECORD",
        "Fix the database the record lives in (inferences.billing_attempts) and "
        "run again."),
    BILLING_RECORD_CAUSE_UNPRICED: (
        "A BILLED ATTEMPT'S RESERVATION COULD NOT BE PRICED, so it was not "
        "dispatched",
        "Add the model's rates to config.PRICING_CONFIG and run again."),
    BILLING_RECORD_CAUSE_DEFERRED: (
        "A SETTLEMENT SHORTFALL COULD NOT BE WRITTEN TO THE DATABASE AND WAS "
        "DEFERRED TO A MARKER FILE beside the checkpoint",
        "The next run commits the marker before any paid work; fix the database "
        "and run again, and do not delete the marker."),
    BILLING_RECORD_CAUSE_DISCREPANCY_UNRECORDED: (
        "A SETTLEMENT SHORTFALL COULD NOT BE WRITTEN TO THE DATABASE OR TO A "
        "MARKER FILE",
        "A resumed campaign's budget may be lower than this run's charges by the "
        "shortfall printed on the DISCREPANCY line above; fix the database and "
        "the checkpoint directory before running again."),
    BILLING_RECORD_CAUSE_MISSING: (
        "A BILLING ROW THIS RUN COMMITTED IS GONE FROM THE CAMPAIGN'S DURABLE "
        "BILLING RECORD",
        "Its charge is retained as a discrepancy where that could be written "
        "(see the DISCREPANCY line above); a resumed campaign refuses until the "
        "database is restored or --fresh starts a new campaign."),
    BILLING_RECORD_CAUSE_CONFLICT: (
        "A BILLING ROW THIS RUN COMMITTED HOLDS A SETTLEMENT THIS RUN DID NOT "
        "WRITE",
        "The shortfall is retained as a discrepancy where that could be written "
        "(see the DISCREPANCY line above); find what else writes "
        "inferences.billing_attempts before running again."),
    BILLING_RECORD_CAUSE_UNVERIFIED: (
        "A SETTLEMENT'S STORED OUTCOME COULD NOT BE READ BACK FROM THE "
        "CAMPAIGN'S DURABLE BILLING RECORD",
        "This run charged the conservative amount and retained the difference "
        "as a discrepancy where that could be written (see the DISCREPANCY line "
        "above); fix the database and run again."),
    None: (
        "THE CAMPAIGN'S DURABLE BILLING RECORD CAN NO LONGER BE TRUSTED TO COUNT "
        "THIS RUN'S CHARGES",
        "Inspect inferences.billing_attempts and the errors above before running "
        "again."),
}
"""``(what happened, remedy)`` per ``BILLING_RECORD_CAUSES`` member, for
``SpendStop._latch``. ``None`` is the sentence for an absent or unrecognised
cause, and it claims no mechanism. A test pins the keys equal to the vocabulary
plus None."""


SPEND_STOP = SpendStop()
"""The one instance. Reset by ``oncotriage/batch/runner.py:main()``."""


# ===========================================================================
# THE DURABLE BILLING RECORD (the cumulative-spend pass)
# ===========================================================================
#
# WHY THE LEDGER ABOVE IS NOT ENOUGH FOR A CAMPAIGN. ``SPEND_LEDGER`` is exact
# and dies with the process. A resumed campaign used to rebuild its baseline
# from ``inferences.estimated_cost_usd``, and that column describes a patient's
# FINAL Stage 5 attempt only -- so every earlier billed attempt (a parse failure
# answered at full price, a warmup whose cache write could not be confirmed, a
# timeout the provider may well have billed) was absent from the budget of the
# next process, which could therefore spend it again.
#
# THE MECHANISM IS RESERVE, DISPATCH, SETTLE. Immediately before a billed wire
# attempt a conservative RESERVATION -- the request's own estimated input plus
# its full output ceiling, priced at the wire model: the same upper bound
# ``provider_resilience`` already charges for a possibly-billed failure -- is
# COMMITTED to ``inferences.billing_attempts``. Only then is the request sent.
# When the attempt resolves, the row is SETTLED: a response at its priced usage,
# a not-billed failure at zero, anything whose billing cannot be observed
# (possibly billed, abandoned, unpriceable usage) at the reservation.
#
# WHAT A KILL COSTS IS THEREFORE BOUNDED IN THE SAFE DIRECTION. A process killed
# after the reservation commit and before the settlement leaves a RESERVED row,
# and every reader charges a reserved row at its reservation -- an upper bound
# on what that attempt could have cost. A process killed before the commit sent
# nothing. There is no window in which money leaves without a row.
#
# A RESERVATION THAT CANNOT BE PERSISTED REFUSES THE DISPATCH AND LATCHES THE
# RUN. Continuing would send requests whose charges a later process cannot
# count, which is the defect this exists to remove; the latch is ``SPEND_STOP``
# under ``SPEND_LIMIT_BILLING_RECORD``, so the batch runner stops starting
# patients and records ``runs.stop_reason = 'billing_record'``.
#
# ONLY A PROCESS THAT INSTALLS A SINK WRITES A RECORD. The batch runner installs
# one per invocation, keyed to its campaign and its run row. The API, the MCP
# server, the ablation study and every test that installs nothing keep exactly
# the behaviour they had: ``reserve`` returns None and ``settle(None, ...)`` is
# a no-op. That is a statement about who has a campaign, not an oversight.

BILLING_OUTCOME_RESPONSE = "response"
BILLING_OUTCOME_RESPONSE_UNPRICED = "response_unpriced"
BILLING_OUTCOME_POSSIBLY_BILLED = "possibly_billed"
BILLING_OUTCOME_NOT_BILLED = "not_billed"
BILLING_OUTCOME_ABANDONED = "abandoned"

BILLING_OUTCOMES = (BILLING_OUTCOME_RESPONSE, BILLING_OUTCOME_RESPONSE_UNPRICED,
                    BILLING_OUTCOME_POSSIBLY_BILLED, BILLING_OUTCOME_NOT_BILLED,
                    BILLING_OUTCOME_ABANDONED)
"""How a reserved attempt resolved. CLOSED; ``database_logger`` restates it and
a test pins the two equal.

  ``response``           a response arrived; settled at its PRICED usage.
  ``response_unpriced``  a response arrived and its usage could not be priced;
                         settled at the reservation, because nothing smaller is
                         known to be true.
  ``possibly_billed``    the attempt failed after dispatch in a way the provider
                         may have billed; settled at the reservation.
  ``not_billed``         the attempt failed in a way the provider provably did
                         not bill (refused before inference); settled at zero.
  ``abandoned``          a Ctrl-C, SystemExit or cancellation arrived while the
                         request was on the wire; settled at the reservation.
"""

BILLING_OUTCOMES_AT_RESERVATION = (BILLING_OUTCOME_RESPONSE_UNPRICED,
                                   BILLING_OUTCOME_POSSIBLY_BILLED,
                                   BILLING_OUTCOME_ABANDONED)
"""The outcomes whose settled amount IS the reservation -- every outcome that
observed no priceable usage and is not provably unbilled."""


def attempt_liability(outcome, reserved_usd, model=None, prompt_tokens=None,
                      completion_tokens=None):
    """What one billed wire attempt costs a budget. PURE; never raises.

    Returns ``(outcome, usd, settled_input, settled_output, fault)``.

    THE ONE LIABILITY RULE (the billing closure pass). The in-process ledger
    and the durable billing record used to price an attempt at two different
    call sites, and they disagreed on every possibly-billed failure that one
    of them did not see: a failed Stage 2 embedding was charged its
    reservation in ``inferences.billing_attempts`` and NOTHING in
    ``SPEND_LEDGER``, and a response whose usage could not be priced was
    charged its reservation durably and $0 in the ledger. So a live process
    could admit spending that a resumed process, reading the durable record,
    would refuse. Both halves now read THIS function through
    ``AttemptLiability.resolve``, so there is no second rule to drift.

    ``response``            priced usage; an unpriceable response becomes
                            ``response_unpriced`` at the reservation.
    ``response_unpriced``,
    ``possibly_billed``,
    ``abandoned``           the reservation.
    ``not_billed``          zero.
    anything else           ``possibly_billed`` at the reservation, with a
                            fault key -- an unknown outcome must not be free.

    ``usd`` is None only when the reservation itself was unpriceable AND the
    outcome is charged at it -- a state reachable only with no durable sink
    installed, because ``BillingRecord.reserve`` refuses an unpriceable
    reservation when one is. The caller counts it; it is never read as zero
    silently.
    """
    fault = None
    if outcome not in BILLING_OUTCOMES:
        fault = f"bad_outcome:{outcome}"
        outcome = BILLING_OUTCOME_POSSIBLY_BILLED
    if outcome == BILLING_OUTCOME_RESPONSE:
        usd, price_fault = price_usage(model, prompt_tokens, completion_tokens)
        if usd is not None:
            return (outcome, usd, _as_token_count(prompt_tokens),
                    _as_token_count(completion_tokens), fault)
        fault = price_fault
        outcome = BILLING_OUTCOME_RESPONSE_UNPRICED
    if outcome in BILLING_OUTCOMES_AT_RESERVATION:
        return outcome, reserved_usd, None, None, fault
    # `not_billed`, the one outcome left: provably refused before inference.
    return outcome, 0.0, None, None, fault


class BillingRecordUnavailable(RuntimeError):
    """A billed attempt was NOT dispatched because its reservation could not be
    persisted. A ``RuntimeError`` and not a ``ValueError``, on
    ``UnknownModelPricingError``'s footing. Stage 5 converts it into a
    ``Stage5SpendStopped`` so the patient fails rather than completing with a
    hole; Stage 2's dense channel degrades exactly as for any other failure."""


class BillingHandle(NamedTuple):
    """One reserved attempt. Carries its SINK, so a settlement lands in the
    record the reservation was written to even if the slot is cleared between
    the two -- a request still on the wire when a run ends."""

    attempt_id: str
    sink: object
    source: str
    model: str
    input_tokens: int
    output_tokens: int
    reserved_usd: float


class BillingRecord:
    """The process's installable durable billing sink. Thread-safe.

    INSTALLED BY ``oncotriage/batch/runner.py:main()`` AND CLEARED BY IT, beside
    ``SPEND_LEDGER.reset()``. Module-level for the ledger's reason: the question
    spans patients and threads and Stage 2 as well as Stage 5, and
    ``TrialMatchState`` reaches neither the embedding call nor the retry
    policy's attempt loop.

    THE SINK IS DUCK-TYPED (P1b adds two OPTIONAL methods,
    ``settled_usd(attempt_id)`` and ``record_discrepancy(**fields) -> str``; a
    sink without them has every discrepancy counted FAILED and the run latched.
    P1c adds a third, ``stored_state(attempt_id) -> dict | None``; a sink without
    it cannot VERIFY a settlement that reported failure, so every such settlement
    is handled as UNVERIFIED -- conservatively, and the run latched)
    -- ``reserve(**fields)`` and ``settle(attempt_id,
    **fields) -> str`` -- so this module imports no storage layer (see the
    module docstring); ``database_logger.BillingRecordSink`` is the one shipped
    implementation.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._sink = None
        # THE LIVE LIABILITY TALLY (the billing closure pass): per outcome,
        # ``[count, usd]``, plus the attempts created and not yet resolved under
        # ``LIABILITY_OPEN``. A plain dict and not a Counter, so it is not a
        # module-level degradation counter; it is what lets a reconciliation
        # compare SETTLED spend and UNRESOLVED reservations separately against
        # the durable record's own split.
        self._tally = {}
        self._tally_lock = threading.Lock()

    def _note_open(self, reserved_usd) -> None:
        with self._tally_lock:
            slot = self._tally.setdefault(LIABILITY_OPEN, [0, 0.0])
            slot[0] += 1
            slot[1] += float(reserved_usd or 0.0)

    def _note_resolved(self, reserved_usd, outcome, usd) -> None:
        with self._tally_lock:
            slot = self._tally.setdefault(LIABILITY_OPEN, [0, 0.0])
            slot[0] -= 1
            slot[1] -= float(reserved_usd or 0.0)
            done = self._tally.setdefault(outcome, [0, 0.0])
            done[0] += 1
            done[1] += float(usd or 0.0)

    def liability_snapshot(self) -> dict:
        """``{outcome or LIABILITY_OPEN: (count, usd)}``, a copy."""
        with self._tally_lock:
            return {k: (v[0], v[1]) for k, v in self._tally.items()}

    def reset_liability(self) -> None:
        """Forget an earlier run's tally. Called beside ``SPEND_LEDGER.reset()``."""
        with self._tally_lock:
            self._tally.clear()

    def install(self, sink) -> None:
        with self._lock:
            self._sink = sink

    def clear(self) -> None:
        with self._lock:
            self._sink = None

    def installed_sink(self):
        """The installed sink, or None. A method, not a property -- the decorator
        inventory pins every property in the package."""
        with self._lock:
            return self._sink

    def reserve(self, source, model, input_tokens, output_tokens, *, where,
                reserved_usd=None, note=None):
        """Persist a reservation before a billed dispatch. Returns a handle, or
        None when no sink is installed. RAISES ``BillingRecordUnavailable`` when
        a sink is installed and the reservation could not be made durable -- the
        caller must then NOT dispatch.

        ``where`` names the call site for the latch banner, on ``SpendStop``'s
        own convention.

        ``reserved_usd`` (R1) is an upper bound ALREADY PRICED by the owner of
        the bound: Stage 5's documented-limit bound prices input at its dearest
        class and a long-context multiplier, which ``price_usage`` cannot
        express. None prices the tokens at the base rates, exactly as before.
        An explicit amount that is not a positive finite number is UNPRICED.
        ``note`` is stored on the row verbatim and is passed to the sink only
        when it is not None, so a duck-typed sink that predates it still works.
        """
        sink = self.installed_sink()
        if sink is None:
            return None
        if SPEND_STOP.limit == SPEND_LIMIT_BILLING_RECORD:
            # ALREADY LATCHED: the run is stopping because an earlier write
            # failed. Trying again would make whether a request goes out depend
            # on whether a flapping database happened to answer this time.
            BILLING_RECORD_FAULTS["refused_latched"] += 1
            raise BillingRecordUnavailable(
                "the campaign's durable billing record latched earlier in this "
                "run (" + (SPEND_STOP.cause or "cause not recorded") + "), so "
                "no further billed request is dispatched")
        usd, fault = _reservation_price(model, input_tokens, output_tokens,
                                        reserved_usd)
        if usd is None:
            BILLING_RECORD_FAULTS["reserve:unpriced"] += 1
            SPEND_STOP.trip(SPEND_LIMIT_BILLING_RECORD, where, source,
                            BILLING_RECORD_CAUSE_UNPRICED)
            raise BillingRecordUnavailable(
                f"the reservation for this attempt could not be priced "
                f"({fault}), so it was not dispatched")
        attempt_id = uuid.uuid4().hex
        try:
            fields = dict(attempt_id=attempt_id, source=source, model=model,
                          input_tokens=int(input_tokens),
                          output_tokens=int(output_tokens), reserved_usd=usd,
                          correlation_id=current_correlation_id())
            if note is not None:
                fields["note"] = note
            sink.reserve(**fields)
        except Exception as exc:                                # noqa: BLE001
            BILLING_RECORD_FAULTS[f"reserve:{type(exc).__name__}"] += 1
            log.error("a billed attempt's reservation could not be persisted; "
                      "the attempt was not dispatched and the run is latched",
                      event="billing_reservation_failed", status="stopped",
                      phase=source, mode=where, error_type=type(exc).__name__,
                      error_message=str(exc), degraded=True)
            SPEND_STOP.trip(SPEND_LIMIT_BILLING_RECORD, where, source,
                            BILLING_RECORD_CAUSE_WRITE_FAILED)
            raise BillingRecordUnavailable(
                f"the reservation could not be persisted "
                f"({type(exc).__name__}: {exc}), so the attempt was not "
                f"dispatched") from exc
        return BillingHandle(attempt_id, sink, source, model,
                             int(input_tokens), int(output_tokens), usd)

    def settle(self, handle, outcome, *, model=None, prompt_tokens=None,
               completion_tokens=None):
        """Resolve a reservation. NEVER RAISES. Returns the sink's result, or
        None for a None handle.

        It runs after the money is spent, frequently while an exception is
        propagating, so a raise here would replace the caller's diagnosis with
        a bookkeeping one. A settlement that did not land leaves the row
        RESERVED, which every reader charges at its upper bound.

        THIS METHOD TRUSTS THE SINK'S RESULT AND READS NOTHING BACK. A ``failed``
        here may have committed (P1c). Billed attempts go through
        ``AttemptLiability.resolve``, which verifies the stored row; this entry
        point has no production caller.
        """
        if handle is None:
            return None
        if outcome not in BILLING_OUTCOMES:
            BILLING_RECORD_FAULTS[f"settle:bad_outcome:{outcome}"] += 1
        # THE ONE RULE. See `attempt_liability`: the ledger's half of the same
        # attempt reads the same function through `AttemptLiability.resolve`.
        outcome, usd, settled_in, settled_out, _fault = attempt_liability(
            outcome, handle.reserved_usd, model or handle.model, prompt_tokens,
            completion_tokens)
        return self._write_settlement(handle, outcome, usd, settled_in,
                                      settled_out)

    def _write_settlement(self, handle, outcome, usd, settled_in, settled_out):
        """The durable half of a settlement whose amount is ALREADY decided.
        NEVER RAISES. Shared by ``settle`` and ``AttemptLiability.resolve`` so
        the write and its fault accounting have one owner."""
        try:
            result = handle.sink.settle(handle.attempt_id, outcome=outcome,
                                        settled_usd=usd,
                                        input_tokens=settled_in,
                                        output_tokens=settled_out)
        except Exception as exc:                                # noqa: BLE001
            BILLING_RECORD_FAULTS[f"settle:raised:{type(exc).__name__}"] += 1
            return "failed"
        if result not in ("settled", "duplicate"):
            BILLING_RECORD_FAULTS[f"settle:{result}"] += 1
        return result

    def _stored_settlement(self, handle):
        """The settled amount the durable row now holds for ``handle``, or None.
        NEVER RAISES. None when the sink cannot say, which every caller reads as
        the conservative 0 (P1b)."""
        reader = getattr(handle.sink, "settled_usd", None)
        if reader is None:
            BILLING_RECORD_FAULTS["discrepancy:no_settled_reader"] += 1
            return None
        try:
            value = reader(handle.attempt_id)
        except Exception as exc:                                # noqa: BLE001
            BILLING_RECORD_FAULTS[
                f"discrepancy:settled_read_raised:{type(exc).__name__}"] += 1
            return None
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or value != value or value < 0 or value == float("inf")):
            return None
        return float(value)

    def _stored_state(self, handle):
        """What the durable row now holds for ``handle``, READ BACK (P1c). NEVER
        RAISES. Returns ``(STORED_STATE_ABSENT,)``, ``(STORED_STATE_RESERVED,
        reserved_usd)``, ``(STORED_STATE_SETTLED, reserved_usd, settled_usd,
        outcome)``, or None when the answer could not be established -- a sink
        with no reader, a reader that raised, or an answer that is malformed.
        None is never a state: the caller reads it as "not established"."""
        reader = getattr(handle.sink, "stored_state", None)
        if reader is None:
            BILLING_RECORD_FAULTS["verify:no_state_reader"] += 1
            return None
        try:
            value = reader(handle.attempt_id)
        except Exception as exc:                                # noqa: BLE001
            BILLING_RECORD_FAULTS[
                f"verify:read_raised:{type(exc).__name__}"] += 1
            return None
        state = value.get("state") if isinstance(value, dict) else None
        if state == STORED_STATE_ABSENT:
            return (STORED_STATE_ABSENT,)
        reserved = value.get("reserved_usd") if isinstance(value, dict) else None
        if state == STORED_STATE_RESERVED and _valid_usd(reserved):
            return (STORED_STATE_RESERVED, float(reserved))
        settled = value.get("settled_usd") if isinstance(value, dict) else None
        outcome = value.get("outcome") if isinstance(value, dict) else None
        if (state == STORED_STATE_SETTLED and _valid_usd(reserved)
                and _valid_usd(settled)
                and (outcome is None or isinstance(outcome, str))):
            return (STORED_STATE_SETTLED, float(reserved), float(settled),
                    outcome)
        if value is not None:
            BILLING_RECORD_FAULTS["verify:malformed"] += 1
        return None

    def _record_discrepancy(self, handle, result, live_usd, durable_usd,
                            shortfall_usd) -> str:
        """Hand one attempt's settlement shortfall to the sink. NEVER RAISES;
        returns a ``DISCREPANCY_WRITE_RESULTS`` member (P1b)."""
        writer = getattr(handle.sink, "record_discrepancy", None)
        if writer is None:
            BILLING_RECORD_FAULTS["discrepancy:no_writer"] += 1
            return DISCREPANCY_FAILED
        try:
            written = writer(attempt_id=handle.attempt_id,
                             source=handle.source, model=handle.model,
                             result=result, live_usd=float(live_usd),
                             durable_usd=float(durable_usd),
                             shortfall_usd=float(shortfall_usd),
                             correlation_id=current_correlation_id())
        except Exception as exc:                                # noqa: BLE001
            BILLING_RECORD_FAULTS[
                f"discrepancy:write_raised:{type(exc).__name__}"] += 1
            return DISCREPANCY_FAILED
        return (written if written in DISCREPANCY_WRITE_RESULTS
                else DISCREPANCY_FAILED)


BILLING_RECORD = BillingRecord()
"""The one instance. Installed and cleared by ``oncotriage/batch/runner.py:main()``."""


# ===========================================================================
# ONE LIABILITY, BOTH LEDGERS (the billing closure pass)
# ===========================================================================
#
# WHAT THIS CLOSES. The in-process ledger was charged at the Stage 5 call
# sites (a response) and in the retry policy's `on_possibly_billed` callback
# (a possibly-billed failure); the durable record was settled in the retry
# policy's attempt hook; Stage 2's embedding charged the ledger for a response
# and nothing for a failure. Three writers, two prices, and every attempt the
# two did not both see was a live-versus-resumed disagreement in the direction
# that lets a live process spend what a resume would refuse.
#
# THE MECHANISM: one object per billed wire attempt, created before dispatch
# (it persists the durable reservation when a sink is installed), resolved
# EXACTLY ONCE after. Resolution computes the liability through
# `attempt_liability`, charges `SPEND_LEDGER` that amount, and settles the
# durable row at the SAME amount. When the durable settlement does not report
# landing (failed, missing, conflict), `AttemptLiability._settlement_did_not_land`
# first READS THE ROW BACK for a result that could be a lost acknowledgement
# (P1c), then charges live the most conservative reading the stored row allows
# and writes the shortfall as its own durable row (or a marker the runner
# reconciles), so a resumed reading is never below the live charge (P1b). The
# one residual -- every durable write after the response failing -- is in
# RECOVERY_P1C_REPORT.md, item 2.
#
# WHAT IT DOES NOT COVER, STATED: billed paths that do not create one of these
# -- the rater's Batch API, the ragas harness -- keep their own ledger charges
# and have no durable record to agree with.

LIABILITY_OPEN = "open"
"""The live tally's key for attempts created and not yet resolved -- the
in-process mirror of a durable row still ``reserved``."""

# THE SETTLEMENT RESULTS THIS MODULE BRANCHES ON (P1b). Restated from
# ``database_logger.SETTLE_RESULTS`` for the reason ``BILLING_OUTCOMES`` is: this
# module imports no storage layer, and the sink is duck-typed.
SETTLEMENT_MISSING = "missing"
SETTLEMENT_CONFLICT = "conflict"
SETTLEMENT_FAILED = "failed"
SETTLEMENTS_LANDED = ("settled", "duplicate")
"""A settlement whose row now carries exactly this attempt's liability."""
SETTLEMENT_INTEGRITY_RESULTS = (SETTLEMENT_MISSING, SETTLEMENT_CONFLICT)
"""Results that prove the durable record was changed under this process."""

DISCREPANCY_RECORDED = "recorded"
DISCREPANCY_DEFERRED = "deferred"
DISCREPANCY_FAILED = "failed"
DISCREPANCY_WRITE_RESULTS = (DISCREPANCY_RECORDED, DISCREPANCY_DEFERRED,
                             DISCREPANCY_FAILED)
"""What a sink's ``record_discrepancy`` did. CLOSED.

  ``recorded``  the shortfall row is committed in the billing record.
  ``deferred``  the database would not take it; a synced marker file holds it
                and a later run must reconcile it before paid work.
  ``failed``    neither landed. Nothing durable carries the shortfall; the run
                is latched and the operator must not trust a resume's budget.
"""

DISCREPANCY_EPSILON_USD = 1e-9
"""Below this a shortfall is float noise, not a liability. Equal to the storage
layer's amount tolerance and to the closure tests' accounting precision."""

# WHAT A STORED ROW IS, READ BACK (P1c). Restated from ``database_logger``
# (``STORED_STATE_ABSENT`` and its two row states) for ``BILLING_OUTCOMES``'
# reason; a test pins them equal.
STORED_STATE_ABSENT = "absent"
STORED_STATE_RESERVED = "reserved"
STORED_STATE_SETTLED = "settled"


def _valid_usd(value) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and value == value and value != float("inf") and value >= 0)


class AttemptLiability:
    """One billed wire attempt's liability. Resolve it EXACTLY ONCE.

    ``begin_billed_attempt`` is the constructor to use. It RAISES
    ``BillingRecordUnavailable`` when a durable sink is installed and the
    reservation could not be made durable -- the caller must then not
    dispatch. With no sink it never raises.
    """

    def __init__(self, source, model, input_tokens, output_tokens, *, where,
                 reserved_usd=None, note=None):
        self.source = source
        self.model = model
        self.where = where
        # PRICED ONCE, HERE, whether or not a sink is installed: the ledger's
        # possibly-billed charge needs it on every process, and the durable
        # reservation (below) is priced by the same function, so the two cannot
        # hold different upper bounds for one attempt. An explicit, already
        # priced bound (R1) is used verbatim by BOTH, through one helper.
        self.reserved_usd, self.reservation_fault = _reservation_price(
            model, input_tokens, output_tokens, reserved_usd)
        # A BOUNDED attempt is one whose reservation claims to be a proven
        # upper bound; a response priced above it breaks that claim (R1).
        self.bounded = reserved_usd is not None
        self.handle = BILLING_RECORD.reserve(source, model, input_tokens,
                                             output_tokens, where=where,
                                             reserved_usd=reserved_usd,
                                             note=note)
        self.resolved_outcome = None
        self.resolved_usd = None
        BILLING_RECORD._note_open(self.reserved_usd)

    def upper_bound_usd(self) -> float:
        """The reservation, for reporting. Charges nothing. Zero when it could
        not be priced (a no-sink process only; the fault is counted at
        resolution)."""
        return float(self.reserved_usd or 0.0)

    def resolve(self, outcome, *, model=None, prompt_tokens=None,
                completion_tokens=None) -> float:
        """Charge the ledger and settle the durable row at ONE amount.

        NEVER RAISES; idempotent -- a second call returns the first call's
        amount and charges nothing, because an attempt is billed once whatever
        path reports it.

        ORDER: the LEDGER first, then the durable write. ``charge_usd`` cannot
        raise, so the in-process budget moves even if everything after it
        fails; and a durable write that does not land is handled by
        ``_settlement_did_not_land``, which keeps a resumed reading at or above
        this process's charge (P1b).
        """
        if self.resolved_outcome is not None:
            return self.resolved_usd
        try:
            out, usd, s_in, s_out, fault = attempt_liability(
                outcome, self.reserved_usd, model or self.model, prompt_tokens,
                completion_tokens)
        except Exception as exc:                                # noqa: BLE001
            # Unreachable by construction; conservative if reached.
            BILLING_RECORD_FAULTS[f"liability:raised:{type(exc).__name__}"] += 1
            out, usd, s_in, s_out, fault = (BILLING_OUTCOME_POSSIBLY_BILLED,
                                            self.reserved_usd, None, None, None)
        if fault is not None:
            SPEND_LEDGER_FAULTS[fault] += 1
        if usd is None:
            # AN UNPRICEABLE RESERVATION CHARGED AT ITSELF. Only reachable with
            # no sink (see `attempt_liability`); counted, never silent.
            SPEND_LEDGER_FAULTS[f"unpriced_reservation:{self.model}"] += 1
            usd = 0.0
        if (self.bounded and out == BILLING_OUTCOME_RESPONSE
                and self.reserved_usd is not None
                and float(usd) > float(self.reserved_usd) + 1e-9):
            # THE BOUND WAS BROKEN (R1). A reservation derived from documented
            # provider limits was exceeded by a PRICED response -- a provider
            # billing beyond its own limit, or an echo naming a pricier model.
            # The liability below is still charged at the priced amount, and a
            # settlement that does not land still writes the shortfall; what
            # this adds is that the broken assumption is NAMED, because a
            # process death on a later attempt would be under-covered by it.
            BILLING_RECORD_FAULTS[f"bound_exceeded:{self.source}"] += 1
            log.error("a billed response was priced above its documented-limit "
                      "reservation; the reservation is not an upper bound for "
                      "this provider or model",
                      event="billing_bound_exceeded", status="error",
                      phase=self.source, mode=self.where, degraded=True)
        self.resolved_outcome, self.resolved_usd = out, float(usd)
        if out != BILLING_OUTCOME_NOT_BILLED:
            SPEND_LEDGER.charge_usd(float(usd), self.source)
        result = None
        if self.handle is not None:
            result = BILLING_RECORD._write_settlement(self.handle, out,
                                                      float(usd), s_in, s_out)
            if result not in SETTLEMENTS_LANDED:
                self._settlement_did_not_land(result)
        BILLING_RECORD._note_resolved(self.reserved_usd, out,
                                      self.resolved_usd)
        return self.resolved_usd

    def _settlement_did_not_land(self, result) -> None:
        """Keep a resumed process's reading at or above this process's. NEVER RAISES.

        THE INVARIANT (P1b): for every attempt, what a FRESH process reads from
        the durable record is never less than what THIS process charged. The
        settlement did not land, so the attempt's own row does not carry the
        liability; what it carries instead depends on the result:

          ``missing``   no row. The durable reading is 0.
          ``conflict``  a row settled by something else. The reading is its
                        stored amount (0 when that cannot be read).
          ``failed``    and anything unrecognised, including a sink that raised:
                        THE RETURNED RESULT IS NOT EVIDENCE (P1c). A settlement
                        can commit and then lose its acknowledgement, so the row
                        is READ BACK (``BillingRecord._stored_state``):
                          settled at this liability's amount and outcome -- it
                            LANDED; nothing further is owed, and live is not
                            topped up;
                          reserved at this reservation -- ``failed``, read at
                            the reservation;
                          settled otherwise, or reserved at another amount --
                            ``conflict`` with that stored amount;
                          absent -- ``missing``;
                          not established -- UNVERIFIED: the row holds either
                            the reservation or the priced amount, so the
                            durable reading taken is the SMALLER of the two,
                            live is the larger, and the run latches.

        (P1c, measured before the change: a settlement that committed $0.0032
        and reported ``failed`` left live at the $0.38484 reservation and a
        fresh process seeded at $0.0032, with no discrepancy row.)

        LIVE is charged the most conservative amount any reading supports --
        ``max(priced, reservation, stored)`` -- so live never under-states the
        record either. The SHORTFALL ``live - durable`` is then written as its
        own settled ``settlement_discrepancy`` row, deterministic in the attempt
        id so a repeat cannot double-count, or deferred to a synced marker file
        beside the checkpoint when the database will not take it; the batch
        runner reconciles such markers before any later paid work, and refuses
        by name when it cannot.

        ``missing`` AND ``conflict`` ALSO LATCH THE RUN. Both prove the record
        was changed under a running process -- a row this process committed is
        gone, or holds a settlement this process did not write -- so nothing
        further is dispatched against it. A ``missing`` row additionally makes
        a later resume REFUSE (``BillingRecordIncomplete``): the record lost at
        least one row this campaign wrote, and no reader can know how many
        more. A discrepancy that could not be made durable at all latches too.
        """
        reserved = float(self.reserved_usd or 0.0)
        priced = float(self.resolved_usd)
        stored = None
        verified = True
        if result in SETTLEMENT_INTEGRITY_RESULTS:
            # Already established by the settlement's own read inside its
            # transaction.
            result_class = result
            if result_class == SETTLEMENT_CONFLICT:
                stored = BILLING_RECORD._stored_settlement(self.handle)
        else:
            row = BILLING_RECORD._stored_state(self.handle)
            if row is None:
                result_class, verified = SETTLEMENT_FAILED, False
                BILLING_RECORD_FAULTS[f"settle:unverified:{result}"] += 1
            elif row[0] == STORED_STATE_ABSENT:
                result_class = SETTLEMENT_MISSING
                BILLING_RECORD_FAULTS[f"settle:verified_missing:{result}"] += 1
            elif row[0] == STORED_STATE_SETTLED:
                if (abs(row[2] - priced) <= DISCREPANCY_EPSILON_USD
                        and row[3] == self.resolved_outcome):
                    # THE SETTLEMENT LANDED; ONLY ITS ACKNOWLEDGEMENT WAS LOST.
                    # The row carries exactly this liability, so there is no
                    # shortfall, no top-up and nothing to latch on.
                    BILLING_RECORD_FAULTS[
                        f"settle:verified_landed:{result}"] += 1
                    return
                result_class, stored = SETTLEMENT_CONFLICT, row[2]
                BILLING_RECORD_FAULTS[f"settle:verified_conflict:{result}"] += 1
            elif abs(row[1] - reserved) <= DISCREPANCY_EPSILON_USD:
                result_class = SETTLEMENT_FAILED
                BILLING_RECORD_FAULTS[f"settle:verified_reserved:{result}"] += 1
            else:
                # Reserved at an amount this process did not reserve: the
                # committed row was changed.
                result_class, stored = SETTLEMENT_CONFLICT, row[1]
                BILLING_RECORD_FAULTS[f"settle:verified_conflict:{result}"] += 1
        if result_class == SETTLEMENT_MISSING:
            durable = 0.0
        elif result_class == SETTLEMENT_CONFLICT:
            durable = stored if stored is not None else 0.0
        elif verified:
            durable = reserved
        else:
            # NOT ESTABLISHED: the row holds the reservation (the write did not
            # land) or the priced liability (it did). Taking the SMALLER as the
            # durable reading and the larger as live keeps the durable total at
            # or above live in both worlds, over-counting by at most
            # |reserved - priced| (the safe direction).
            durable = min(reserved, priced)
        live = max(priced, reserved, stored if stored is not None else 0.0)
        if live - priced > 0:
            SPEND_LEDGER.charge_usd(live - priced, self.source)
            self.resolved_usd = live
        label = result_class if verified else "unverified"
        BILLING_RECORD_FAULTS[f"ledger_topped_up:{label}"] += 1
        shortfall = max(live - durable, 0.0)
        integrity = result_class in SETTLEMENT_INTEGRITY_RESULTS
        written = None
        if integrity or shortfall > DISCREPANCY_EPSILON_USD:
            written = BILLING_RECORD._record_discrepancy(
                self.handle, result_class, live, durable, shortfall)
            BILLING_RECORD_FAULTS[f"discrepancy:{label}:{written}"] += 1
            log.error("a billed attempt's settlement did not land; the "
                      "shortfall was written as its own billing row, deferred "
                      "to a marker, or could not be recorded",
                      event="billing_settlement_discrepancy", status=written,
                      phase=self.source, mode=self.where, reason=label,
                      degraded=True)
            console.out(f"[SPEND] BILLING RECORD DISCREPANCY ({label}): "
                        f"attempt {self.handle.attempt_id} charged "
                        f"${live:.6f} live, durable reading ${durable:.6f}, "
                        f"shortfall ${shortfall:.6f} {written}.")
        if integrity or not verified or (written is not None
                                         and written != DISCREPANCY_RECORDED):
            cause = (BILLING_RECORD_CAUSE_MISSING
                     if result_class == SETTLEMENT_MISSING
                     else BILLING_RECORD_CAUSE_CONFLICT
                     if result_class == SETTLEMENT_CONFLICT
                     else BILLING_RECORD_CAUSE_UNVERIFIED if not verified
                     else BILLING_RECORD_CAUSE_DEFERRED
                     if written == DISCREPANCY_DEFERRED
                     else BILLING_RECORD_CAUSE_DISCREPANCY_UNRECORDED)
            SPEND_STOP.trip(SPEND_LIMIT_BILLING_RECORD, self.where, self.source,
                            cause)


def _reservation_price(model, input_tokens, output_tokens, reserved_usd):
    """``(usd, None)`` for a reservation, or ``(None, fault_key)``. PURE.

    None prices the tokens at the base rates (``price_usage``). An explicit
    amount (R1) must be a positive finite number and is used verbatim; anything
    else is unpriced, never zero.
    """
    if reserved_usd is None:
        return price_usage(model, input_tokens, output_tokens)
    if (isinstance(reserved_usd, bool)
            or not isinstance(reserved_usd, (int, float))
            or reserved_usd != reserved_usd
            or reserved_usd in (float("inf"), float("-inf"))
            or reserved_usd <= 0):
        return None, f"bad_reserved_usd:{type(reserved_usd).__name__}"
    return float(reserved_usd), None


def begin_billed_attempt(source, model, input_tokens, output_tokens, *,
                         where, reserved_usd=None,
                         note=None) -> AttemptLiability:
    """Create one billed attempt's liability, BEFORE dispatch. RAISES
    ``BillingRecordUnavailable`` when a durable sink is installed and the
    reservation could not be persisted; the caller must then not dispatch.

    ``reserved_usd`` and ``note`` (R1): see ``BillingRecord.reserve``."""
    return AttemptLiability(source, model, input_tokens, output_tokens,
                            where=where, reserved_usd=reserved_usd, note=note)


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


def describe_rater_cap() -> str:
    """One line for a JUDGE SESSION's banner, printed before the first batch.

    A SEPARATE FUNCTION FROM ``describe_cap()`` FOR ``describe_serving_cap()``'S
    REASON, AND THE SHIPPED RATER PROVED IT: it printed ``describe_cap()``, so
    every judge session announced "Cap $300.00 per campaign" -- a bound it does
    not run under, naming a constant that would not move its own limit. It
    prints on every session, uncapped included: the dangerous state must not be
    the quiet one.
    """
    try:
        cap = rater_spend_cap()
    except SpendCapConfigurationError as exc:
        return f"[Spend] REFUSING TO READ THE RATER CAP: {exc}"
    if cap is None:
        return ("[Spend] NO RATER SPEND CAP IS SET "
                "(config.RATER_SPEND_CAP_USD is None). This judge session may "
                "spend without limit.")
    if not config.SPEND_CAP_ENFORCED:
        return (f"[Spend] Rater cap ${cap:.2f} -- MEASURED ONLY. "
                f"config.SPEND_CAP_ENFORCED is False, so nothing will be "
                f"declined.")
    return (f"[Spend] Rater cap ${cap:.2f} per judge session -- ITS OWN "
            f"budget, not the campaign's. The smallest unit it can decline is "
            f"one batch.")


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
    """One line for the run banner about what a resume inherited.

    IT NAMES THE BUDGET THE BASELINE LANDS IN, because it does not land in all
    of them: ``BUDGET_FOR_SEED_SOURCE`` decides, and a judge session resuming
    $12 must not read as a campaign that has already spent it.
    """
    # THE UNVERIFIED CLAUSE COMES FIRST AND IS CHECKED BEFORE THE "FRESH" TEST,
    # because a reading that found no readable row AND skipped unreadable ones
    # is not a fresh run -- it is a record nobody could read, and "Fresh run"
    # would be the most confident possible wrong statement about it.
    unverified = (f" -- UNVERIFIED: {seed.unreadable} item(s) on this record "
                  f"could not be read, so the figure may be LOWER than the "
                  f"truth and any remainder computed from it potentially "
                  f"OVERSTATED"
                  if isinstance(seed, LedgerSeed) and seed.has_unreadable()
                  else "")
    if seed.source == SEED_SOURCE_BILLING_RECORD:
        # THE CUMULATIVE RECORD SAYS WHAT IT HOLDS IN ITS OWN UNITS -- billed
        # attempts, not inference rows -- and it can hold spend from a prior
        # invocation that checkpointed nothing, so "no prior run" is decided by
        # the attempt count rather than by `runs`.
        if seed.rows == 0 and not unverified:
            return ("[Spend] Fresh campaign: its durable billing record holds "
                    "no billed attempt yet.")
        _open = (f"; {seed.unresolved} of them were interrupted before "
                 f"settlement and are charged at their RESERVED upper bound"
                 if seed.unresolved else "")
        return (f"[Spend] Resumed {seed_budget(seed)} budget from the durable "
                f"billing record: ${seed.usd:.2f} across {seed.rows} billed "
                f"attempt(s) in {seed.runs} prior invocation(s){_open}"
                f"{unverified}.")
    if (seed.source == SEED_SOURCE_NONE or seed.runs == 0) and not unverified:
        return ("[Spend] Fresh run: no prior run contributes to any budget "
                "here.")
    floor = (f" -- A FLOOR, NOT A TOTAL: {seed.unpriced} of {seed.rows} prior "
             f"rows carry no cost and are counted as $0"
             if seed.is_floor else "")
    return (f"[Spend] Resumed {seed_budget(seed) or 'unattributed'} budget: "
            f"${seed.usd:.2f} already spent across {seed.runs} prior run(s) "
            f"and {seed.rows} row(s){floor}{unverified}.")


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
    lines.append(f"  this process        ${SPEND_LEDGER.measured:.4f} "
                 f"over {SPEND_LEDGER.calls} billed call(s)")
    if seed.runs:
        # THE SEED NAMES ITS BUDGET. It is added to ONE budget's measure (see
        # `BUDGET_FOR_SEED_SOURCE`), so a line reporting it without saying
        # which would leave a reader to guess which of the figures below it is
        # inside -- and the guess most readers would make is "all of them".
        lines.append(f"  inherited           ${seed.usd:.4f} "
                     f"over {seed.rows} row(s) from {seed.runs} prior run(s) "
                     f"-> {seed_budget(seed) or 'no budget'}"
                     + ("  <- A FLOOR, NOT A TOTAL" if seed.is_floor else ""))
    # "ALL BUDGETS" AND NOT "CAMPAIGN TOTAL", WHICH IS WHAT THIS LINE SAID AND
    # WHAT IT STOPPED BEING. It is the whole ledger -- the seeded baseline plus
    # everything this process billed on every path -- and once budgets are
    # plural that is a number NO cap is compared against. Leaving the old name
    # would have put a figure labelled "campaign" beside a campaign cap it can
    # exceed without the campaign having spent a cent of it.
    lines.append(f"  all budgets         ${SPEND_LEDGER.total:.4f}"
                 f"   <- the whole ledger; no cap is compared against it")
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
    # ONE CAP GROUP PER BUDGET, UNCONDITIONALLY, AND EVERY BUDGET EVERY TIME.
    # A budget printed only when it spent would make its silence read as
    # coverage -- the argument the "NOT COVERED BY THE CAP" block below already
    # makes, applied to a budget instead of to an exemption. A batch run
    # therefore prints the rater budget at $0.0000, which is the honest
    # statement that the judge is separately bounded and this run did not touch
    # it.
    #
    # THREE STATES PER BUDGET, NOT TWO, AND THEY ARE DECIDED BEFORE ANYTHING IS
    # PRINTED. A cap that could not be READ is not a cap that is ABSENT, and
    # the first version of this function conflated them by testing
    # `cap is not None` afterwards -- so an unreadable cap printed its own
    # diagnosis AND then "NONE -- this run was unbounded" underneath it, two
    # lines making different claims about one value.
    for _budget in SPEND_BUDGETS:
        _cap, _cap_error = None, None
        try:
            # budget_cap(), NOT spend_cap(): it is the one owner of "which
            # constant does this budget read", policy included -- so a serving
            # process prints the window cap it runs under rather than the
            # campaign cap it does not.
            _cap = budget_cap(_budget)
        except SpendCapConfigurationError as exc:
            _cap_error = exc
        _spent = budget_spend(_budget)
        lines.append(f"  spent {_budget:<14}${_spent:.4f}")
        if _cap_error is not None:
            lines.append(f"  cap {_budget:<16}UNREADABLE: {_cap_error}")
        elif _cap is None:
            lines.append(f"  cap {_budget:<16}NONE -- this budget was "
                         f"unbounded")
        else:
            lines.append(f"  cap {_budget:<16}${_cap:.2f}"
                         + ("" if config.SPEND_CAP_ENFORCED
                            else "   (MEASURED ONLY -- not enforced)"))
            _n_unread, _ = SPEND_LEDGER.unverified(_budget)
            lines.append(f"  remaining {_budget:<10}${_cap - _spent:.4f}"
                         + (f"   <- UNVERIFIED, potentially OVERSTATED: "
                            f"{_n_unread} unreadable item(s) on this "
                            f"budget's record" if _n_unread else ""))
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
