# Provider Resilience: Client-Side Pacing and the One Retry Policy
##################################################################

"""Pace every billed provider attempt, and retry only what is transient.

WHAT THIS IS FOR, MEASURED RATHER THAN ARGUED
---------------------------------------------
The operator's 2026-09-11 five-patient smoke run against
``us.anthropic.claude-sonnet-4-6``, on this project's account whose Bedrock
allowance is applied at 10 requests per minute, put
``min(MAX_WORKERS, patients) x per_trial_parallel_bound() = 5 x 2 = 10`` Converse
requests in flight at once, every one of them free to start the instant a slot
opened. The provider answered with ``ThrottlingException: Too many requests``
storms. Read from that run's own rows (opened ``immutable=1``, never written):

  * the MAIN pass completed all five patients and recorded **37 of its 61
    trials** ``per_trial_call_failed``, with 64 of the run's 98 across both
    passes -- each throttled trial call exhausted botocore's four attempts
    (1 s base, 20 s cap: ``BEDROCK_ANTHROPIC_MAX_ATTEMPTS`` was 4 at the
    commit that run recorded, and is 1 here) and was isolated to its trial;
  * the RESAMPLE pass lost two patients outright: their warmups exhausted three
    node attempts of four botocore attempts each, with 1 s and 2 s of
    patient-level backoff between them -- all of it well inside one 60-second
    quota window, so every retry landed in the window that had refused it.

The semantics were right -- isolation, warmup-fails-the-patient, the checkpoint
keeping failures for a deliberate re-run. The timing and the pacing were not.
That log proves quota throttling under burst. It does NOT establish any
provider-side incident, and nothing here assumes one.

TWO MECHANISMS, ONE MODULE, AND WHY THEY ARE NOT IN AN ADAPTER
-------------------------------------------------------------
``QuotaPacer`` schedules every wire attempt against a per-scope request rate
and, when a token quota is configured, a token RESERVATION window.
``execute()`` is the one retry policy: exponential backoff with FULL JITTER,
transient errors only, one TOTAL attempt budget per logical call, and every
attempt it makes goes through the pacer first.

PROVIDER-AGNOSTIC, AND THAT IS THE POINT. Nothing here imports an adapter, an
SDK or the agent. The dispatch seam (``oncotriage/agent/evaluation.py``) hands
it a ``send`` callable, a classifier that maps its provider's exceptions onto
THIS module's closed category vocabulary, and a reservation. A cloud migration
brings a new classifier and a new reservation rule and keeps both mechanisms.

PROCESS-LOCAL, DECLARED, AND ONE PROCESS PER SCOPE IS NOT YET ENFORCED
----------------------------------------------------------------------
The pacer's state is this process's memory. Two processes against one account
do not see each other: each will pace to the full configured rate and together
they will exceed it. There is no shared state and this module does not pretend
to one. The documented rule, printed at every run start by ``describe_pacing``:
an operator running N processes against one account divides the configured
limits by N, or runs one process.

``exclusive_scope_lock`` IS WHAT MAKES THE SECOND HALF OF THAT RULE AN
ENFORCEMENT ON ONE HOST, AND IT IS NOW WIRED. Four entry points take it:
``25- Batch Runner.py`` and ``26- Ablation Study.py`` take the STAGE 5
allowance (``config.matching_quota_scope()``) nested inside their own run
locks, and ``rater_run.py`` and ``ragas_run.py`` take the allowances their own
harnesses draw on. So "divide by N or run one at a time" is refused rather than
requested whenever the second process is on this host.

THAT PARAGRAPH SAID THE OPPOSITE FOR TWO PASSES AND THE HISTORY IS KEPT,
because the obstacle it named was real and its removal is the ruling that
unblocked this. It read: not wired, because
``tests/test_runner_preflight_and_state_faults.py`` check ``5b`` requires a
batch run against a DIFFERENT checkpoint directory to PROCEED while another is
parked -- two run-lock keys and ONE quota scope -- so a scope lock refuses
exactly what that check demands. The collision was with a DECLARED property of
this project rather than with an incidental test, which is why it was reported
rather than worked around.

**THE OPERATOR RULED FOR ONE PROCESS PER ALLOWANCE, AND 5b IS UPDATED TO SAY
SO.** The two properties cannot both hold: "two deployments on one machine are
independent" and "two processes may not pace one account's allowance in
parallel" are contradictory the moment both deployments dispatch to the same
provider, which they do. What survives of 5b's original subject is check ``5c``
-- the two runs still take DIFFERENT RUN LOCKS -- which is what says the
refusal comes from the allowance rather than from a run lock that has quietly
become a global mutex.

WHAT IT DOES WHEN A CALLER ENTERS IT: a second live process on this host that
tries to pace the same scope is refused BY NAME, with the holder's pid, host,
user, start time and the SCOPE, instead of quietly pacing to the same number in
parallel and producing exactly the burst the pacer exists to prevent.
``tests/test_provider_scope_lock.py`` drives that, the crash recovery and the
CWD-independence of the key.

WHAT THE SUITE NEEDED IN ORDER FOR THE WIRING TO BE MEASURABLE, AND IT IS NOT A
WEAKER LOCK. Bucket A runs its files concurrently and four of them spawn real
``main()`` subprocesses, so once every entry point guards one ALLOWANCE those
children collide across files for a reason that is the suite's parallelism
rather than the code's behaviour. Each of the four hands ITS OWN children a
private ``ONCOTRIAGE_LOCK_DIR`` (``tests/_control_harness.py:isolate_locks``),
per FILE and not per invocation -- so the mechanism each exercises is the real
allowance name and the real flock, and only the directory is private. Per
invocation would have made the lock unable to refuse anything inside a harness,
which is precisely what 5b now measures.

WHAT A LOCAL FILE LOCK DOES AND DOES NOT COORDINATE, STATED RATHER THAN
IMPLIED. ``flock`` is an agreement between processes on ONE HOST that can see
one filesystem. It therefore does NOT coordinate: a second machine, a second
container with its own ``/tmp``, or a Lambda. A quota is an ACCOUNT-wide fact
and this lock is a HOST-wide mechanism, so the residual is real and named: two
hosts against one account still exceed the configured rate, and the remedy
there is still the divide-by-N rule above. What the lock removes is the case
that actually happens -- an operator starting a second run on the same machine,
which the run lock's own key (a checkpoint directory) does not catch because
two different checkpoint directories are two different keys.

WHAT IT DOES NOT DO
-------------------
It does not adapt its rate to throttling it observes (botocore's ``adaptive``
mode does; see ``config.BEDROCK_ANTHROPIC_RETRY_MODE``). A throttle while the
pacer is holding the configured rate means the configured quota is wrong or
another process shares the account; the retry policy rides it out and the
counters below report it, and the remedy is the configuration.

IMPORTS NOTHING FROM THE PROJECT BUT ``config``, ``observability`` AND
``control``, so the agent, the storage layer and ``oncotriage/degradation.py``
can all import it without a cycle.

THAT LINE NAMED TWO MODULES AND IS CORRECTED RATHER THAN LEFT. ``control``
joined it with ``exclusive_scope_lock``, and it costs no edge: ``control``
imports nothing from the project at all -- deliberately, so a
``usercustomize`` hook can load it at interpreter startup -- so the three
importers above are unaffected. Reusing it rather than growing a second lock
implementation is the same decision the ablation study and the serial runner
already made; what is decided HERE is this mechanism's key, its two exception
classes and the field its holder record names.
"""

import asyncio
import contextlib
import hashlib
import math
import os
import random
import threading
import time
from collections import Counter, deque
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

from oncotriage import config
from oncotriage import control
from oncotriage.observability import console, get_logger


log = get_logger(__name__)


# ===========================================================================
# THE CLOSED VOCABULARIES
# ===========================================================================

FAILURE_TRANSIENT = "transient"
FAILURE_NON_TRANSIENT = "non_transient"
FAILURE_CLASSES = (FAILURE_TRANSIENT, FAILURE_NON_TRANSIENT)
"""Whether a failed attempt may be retried. Closed; nothing else is ever retried.

AWS's own guidance, adopted rather than invented: retry throttling, model
timeouts, service-unavailable, internal-server and network errors; never a
validation or access-denied error, which will fail identically every time and
whose retry is waste at best and, for a request that was billed, money."""

BILLING_NOT_BILLED = "not_billed"
BILLING_POSSIBLY_BILLED = "possibly_billed"
BILLING_STATES = (BILLING_NOT_BILLED, BILLING_POSSIBLY_BILLED)
"""Whether a failed attempt may nevertheless have been billed.

``not_billed`` is for failures the provider refuses BEFORE inference -- a
throttle, a validation refusal, a connection that never opened. It is the
industry's documented behaviour and it is NOT VERIFIED against an AWS or OpenAI
billing statement by this project; see the report's evidence section.
``possibly_billed`` is every other failure: a timeout after the request was
accepted, a dropped connection, a server error mid-generation, a response that
arrived and could not be read, and anything this module cannot classify. For
those the spend ledger is charged an UPPER BOUND (the reservation) rather than
the zero it used to assume."""

RESERVATION_INFERENCE = "inference"
RESERVATION_MANAGEMENT = "management"
RESERVATION_KINDS = (RESERVATION_INFERENCE, RESERVATION_MANAGEMENT)
"""Whether one logical call consumes MODEL TOKENS. Closed, and REQUIRED.

WHY A DECLARED FACT AND NOT A NUMBER, WHICH IS THE WHOLE POINT OF THE
VOCABULARY. ``require_known_quota`` demands the token family only when a
reservation actually reserves tokens -- correct, because a zero-token
reservation cannot consume a tokens-per-minute allowance and refusing it for an
UNKNOWN TPM would be a refusal the caller could not satisfy. But "does this
consume tokens" was being answered by ``tokens > 0``, and that is a NUMBER A
BUG CAN PRODUCE: an inference call whose estimate came back 0 -- a renderer
that returned an empty prompt, a ``max_output`` of 0, an arithmetic slip in
``_reservation_input_tokens`` -- would have been admitted under an UNKNOWN
token quota and then burned tokens nobody reserved. The provider would meter
them; this process would not.

So the caller DECLARES which it is, and ``execute`` refuses an INFERENCE call
whose reservation is not positive (``ZeroTokenInferenceReservation``). With
that refusal in place ``tokens > 0`` is true of every inference call by
construction, so the token family is always demanded for one -- the bypass
closes without a second rule.

``management`` IS NOT AN ESCAPE HATCH AND THE ONE SCOPE THAT USES IT ARGUES
ITS CASE FROM THE API. ``config.PROVIDER_QUOTA_SCOPE_OPENAI_BATCH``'s four
calls -- ``files.create``, ``batches.create``, ``batches.retrieve``,
``files.content`` -- upload a file, enqueue it, read a status and download a
result. They consume no model tokens, which is why that scope's token row is
``QUOTA_NOT_APPLICABLE`` rather than ``None``. A caller that declares
``management`` for a call that generates tokens is stating something false
about the provider, exactly as a ``QUOTA_NOT_APPLICABLE`` row would be; the
vocabulary makes that a claim somebody wrote down rather than a zero nobody
noticed."""


CATEGORY_THROTTLED = "throttled"
CATEGORY_MODEL_NOT_READY = "model_not_ready"
CATEGORY_TIMEOUT = "timeout"
CATEGORY_CONNECT = "connect"
CATEGORY_CONNECTION_LOST = "connection_lost"
CATEGORY_SERVICE_UNAVAILABLE = "service_unavailable"
CATEGORY_SERVER = "server"
CATEGORY_MODEL_ERROR = "model_error"
CATEGORY_CLIENT = "client_error"
CATEGORY_LOCAL = "local"
CATEGORY_TRANSLATION = "translation"
CATEGORY_ABANDONED = "abandoned_in_flight"
CATEGORY_UNCLASSIFIED = "unclassified"

CATEGORY_POLICY = {
    # (retry?, billed?)
    CATEGORY_THROTTLED: (FAILURE_TRANSIENT, BILLING_NOT_BILLED),
    CATEGORY_MODEL_NOT_READY: (FAILURE_TRANSIENT, BILLING_NOT_BILLED),
    CATEGORY_TIMEOUT: (FAILURE_TRANSIENT, BILLING_POSSIBLY_BILLED),
    # NEVER REACHED THE SERVICE: a DNS failure, a refused connection, a
    # connect-phase timeout. Retrying is safe and nothing was sent.
    CATEGORY_CONNECT: (FAILURE_TRANSIENT, BILLING_NOT_BILLED),
    # REACHED IT AND THEN LOST IT. The request may have been accepted.
    CATEGORY_CONNECTION_LOST: (FAILURE_TRANSIENT, BILLING_POSSIBLY_BILLED),
    CATEGORY_SERVICE_UNAVAILABLE: (FAILURE_TRANSIENT, BILLING_NOT_BILLED),
    CATEGORY_SERVER: (FAILURE_TRANSIENT, BILLING_POSSIBLY_BILLED),
    # NOT IN AWS's TRANSIENT LIST, so not retried: "the request failed due to
    # an error while processing the model" is a statement about this request.
    CATEGORY_MODEL_ERROR: (FAILURE_NON_TRANSIENT, BILLING_POSSIBLY_BILLED),
    CATEGORY_CLIENT: (FAILURE_NON_TRANSIENT, BILLING_NOT_BILLED),
    CATEGORY_LOCAL: (FAILURE_NON_TRANSIENT, BILLING_NOT_BILLED),
    # THE RESPONSE ARRIVED: it was generated and billed; it could not be read.
    CATEGORY_TRANSLATION: (FAILURE_NON_TRANSIENT, BILLING_POSSIBLY_BILLED),
    # THE REQUEST WAS ON THE WIRE AND THE CALLER WAS CANCELLED UNDER IT -- an
    # `asyncio.CancelledError` through `await send()`, a Ctrl-C through
    # `send()`. NOT RETRIED, because the caller is going away and a retry would
    # be work nobody is waiting for; POSSIBLY BILLED, because the provider was
    # asked and may well have answered into a socket nobody read.
    #
    # A CATEGORY OF ITS OWN RATHER THAN `unclassified`, on this module's own
    # two-findings-two-names rule: "the caller abandoned this" and "this error
    # is one nothing could classify" have the same POLICY and different CAUSES,
    # and `{scope}:{category}` is what the run-end unconfirmed-billing block
    # prints. Folding them would tell an operator who pressed Ctrl-C that their
    # run met an unclassifiable provider error.
    CATEGORY_ABANDONED: (FAILURE_NON_TRANSIENT, BILLING_POSSIBLY_BILLED),
    # UNKNOWN IS NOT RETRIED AND IS NOT ASSUMED FREE. Retrying spends budget on
    # something nobody has shown to be transient; charging nothing assumes a
    # zero nobody measured. Both are the unsafe direction for their question.
    CATEGORY_UNCLASSIFIED: (FAILURE_NON_TRANSIENT, BILLING_POSSIBLY_BILLED),
}
CATEGORIES = tuple(CATEGORY_POLICY)
"""Closed. Every provider classifier maps onto exactly these."""


def _assert_policy_table_is_closed() -> None:
    """Refuse at import if a row names a value outside the two vocabularies.

    A ``RuntimeError`` and not an ``assert``: ``python -O`` deletes asserts, and
    a mistyped billing state would make a failure silently un-charged."""
    bad = [cat for cat, (cls, bill) in CATEGORY_POLICY.items()
           if cls not in FAILURE_CLASSES or bill not in BILLING_STATES]
    if bad:
        raise RuntimeError(
            f"provider_resilience.CATEGORY_POLICY rows {bad} name a failure "
            f"class outside {FAILURE_CLASSES} or a billing state outside "
            f"{BILLING_STATES}")


_assert_policy_table_is_closed()


RETRY_OUTCOME_RETRIED = "retried"
RETRY_OUTCOME_RECOVERED = "recovered"
RETRY_OUTCOME_EXHAUSTED = "exhausted"
RETRY_OUTCOME_NOT_RETRIED = "not_retried"
RETRY_OUTCOME_CANCELLED = "cancelled"
RETRY_OUTCOMES = (RETRY_OUTCOME_RETRIED, RETRY_OUTCOME_RECOVERED,
                  RETRY_OUTCOME_EXHAUSTED, RETRY_OUTCOME_NOT_RETRIED,
                  RETRY_OUTCOME_CANCELLED)
"""The five things the policy can report about a logical call.

``retried`` is per BACKOFF (one per sleep); ``recovered`` is per CALL that
retried and then succeeded; ``exhausted`` is a call that ran out of
``config.MATCHING_CALL_MAX_ATTEMPTS`` while its error was still transient;
``not_retried`` is a call whose error was not transient; ``cancelled`` is a call
abandoned because its caller's cancellation fired. ``retried:`` alone is not
enough -- it is equally consistent with a call that retried and gave up -- which
is why the outcome word is in the key, on ``WRITE_RETRY_OUTCOMES``' footing."""


ATTEMPTS_ATTR = "oncotriage_provider_attempts"
"""The attribute a failed call's exception carries: how many wire attempts the
policy made before giving up. Read by the dispatch seam to decide that a
failure was a TRANSPORT failure the one budget already covered -- so no outer
layer may retry it again -- and to put the count in the stored error text."""

CATEGORY_ATTR = "oncotriage_provider_category"
"""The attribute a failed call's exception carries: its ``CATEGORIES`` member."""

PRE_SEND_ATTR = "oncotriage_provider_pre_send_refusal"
"""The attribute a refusal carries when DISPATCH HAD NOT STARTED when it raised.

WHAT IT IS FOR. A caller that must decide "did this request reach the provider"
-- the rater, whose answer decides whether an uncertain submission is
reconciled or simply refused -- cannot answer it from the exception's TYPE. The
types this module raises before a send (``QuotaUnknown``,
``ReservationExceedsQuota``, ``ZeroTokenInferenceReservation``,
``AttemptBudgetConfigurationError``, a bare ``ValueError`` for an unrecognised
kind) are SHARED: nothing stops a future raise site inside or after dispatch
from raising one of them, and an ``isinstance`` tuple over them would silently
adopt that site the day it is written. The marker is the evidence itself.

**IT IS SET BY REGION, NOT BY TYPE, AND THAT IS THE WHOLE DESIGN.** ``execute``
holds a flag that is False until the instant before ``send()`` is entered and
True forever after. Every exception leaving the pre-send region is marked;
nothing leaving the dispatch region is. So a raise site ADDED to the pre-send
region is marked without anybody remembering to, and a raise site added after
dispatch cannot acquire the marker by being the wrong class. That is the
difference between enumerating a set and measuring a fact.

**ABSENCE IS THE SAFE DIRECTION AND IS NOT "DISPATCH STARTED".** A caller must
read a MISSING marker as "this may have reached the provider", because that is
what an unmarked exception means when the marker could not be attached -- an
exception class that refuses attribute assignment, or a raise this module does
not own. `is_pre_send_refusal` therefore tests for the marker's PRESENCE and
never for its negation, so the unknown case falls to the conservative branch:
the rater reconciles, which costs a listing, rather than skipping it, which
costs a duplicate submission.

**A CANCELLED WAIT IS DELIBERATELY NOT MARKED.** ``_cancel_now`` raises out of
the ``except WaitCancelled`` handler, which is a sibling of the marking clause
and therefore not covered by it. On the first attempt nothing has in fact been
sent, so the marker would be TRUE -- but a cancelled caller is going away and
is not deciding whether to reconcile, and leaving it unmarked puts it on the
conservative branch that costs a listing nobody will run. Stated here because
it is the one pre-send path that is not marked, and a reader checking the
region rule would otherwise read it as a defect."""


def mark_pre_send_refusal(exc: BaseException) -> bool:
    """Record on ``exc`` that no dispatch had begun. Returns whether it stuck.

    NEVER RAISES, on ``_annotate``'s footing and for its reason: this runs while
    another exception is already propagating, and a second one raised here would
    replace the caller's real diagnosis with an unrelated one. A class that
    refuses attribute assignment (``__slots__``, a C extension) is COUNTED
    rather than silently left unmarked -- an unmarked refusal is read
    downstream as "this may have been dispatched", which is the safe direction
    but is also a lost fact somebody should be able to see.
    """
    try:
        setattr(exc, PRE_SEND_ATTR, True)
        return True
    except Exception:                                   # noqa: BLE001
        _bump(PROVIDER_RETRY_OUTCOMES,
              f"pre_send_mark_failed:{type(exc).__name__}")
        return False


def is_pre_send_refusal(exc: BaseException) -> bool:
    """Did this exception leave ``execute`` before any dispatch began?

    ``is True`` rather than a truthiness test: a caller that had set the
    attribute to a non-empty string or a 1 would otherwise be believed, and the
    whole value of this marker is that exactly one owner writes it.
    """
    return getattr(exc, PRE_SEND_ATTR, None) is True


# ===========================================================================
# THE COUNTERS
# ===========================================================================

PROVIDER_RETRY_OUTCOMES = Counter()
"""Keyed ``{outcome}:{scope}:{category}`` (``cancelled:{scope}:{ExceptionType}``).

In ``oncotriage/degradation.py``'s REGISTRY: a retry means the provider pushed
back, and on a correctly paced run it should read zero. INCREMENTED UNDER
``_COUNTER_LOCK`` because it is written from worker threads."""

PROVIDER_UNCONFIRMED_BILLING = Counter()
"""Failed attempts that MAY have been billed, keyed ``{scope}:{category}``.

In the REGISTRY. Each one was charged an UPPER BOUND to the spend ledger -- its
estimated input plus its ``max_tokens`` -- because the provider returned no
usage and this project will not assume a zero it did not measure. The dollar
figure is in ``report_lines``."""

PROVIDER_PACING_WAITS = Counter()
"""Keyed ``{scope}:acquired`` / ``{scope}:waited`` / ``{scope}:cancelled`` /
``{scope}:tokens_underreserved``.

In the CENSUS, not the registry: a pacing wait is the mechanism WORKING. A run
with many waits and no retries is exactly the healthy shape this module exists
to produce."""

_COUNTER_LOCK = threading.Lock()


def _bump(counter: Counter, key: str, n: int = 1) -> None:
    with _COUNTER_LOCK:
        counter[key] += n


# ===========================================================================
# THE TIME SOURCE (one seam, so a fake clock reaches every wait)
# ===========================================================================

_CLOCK: Callable[[], float] = time.monotonic
_SLEEP: Callable[[float], None] = time.sleep
_RNG = random.Random()


def set_time_source(clock: Callable[[], float],
                    sleep: Callable[[float], None]) -> Tuple[Callable, Callable]:
    """Replace the clock and the sleeper every wait in this module uses.

    Returns the previous pair so a caller can restore it. A TEST SEAM, on
    ``deps.set_override``'s footing: a fake clock makes pacing and backoff run
    in VIRTUAL time, so a test exercises the real schedule without spending
    minutes of wall time. It changes WHEN a wait ends, never WHETHER it happens.
    """
    global _CLOCK, _SLEEP
    previous = (_CLOCK, _SLEEP)
    _CLOCK, _SLEEP = clock, sleep
    return previous


def reset_time_source() -> None:
    """Restore the real monotonic clock and ``time.sleep``."""
    set_time_source(time.monotonic, time.sleep)


def _now() -> float:
    return _CLOCK()


def cancellable_wait(seconds: float,
                     cancelled: Optional[Callable[[], bool]] = None) -> bool:
    """Wait ``seconds``; return False the moment ``cancelled()`` turns True.

    POLLED, NOT AN EVENT, and that is signal safety rather than minimalism: the
    Stage 5 shutdown flag is a plain module boolean set from a signal handler
    precisely because ``Event.set()`` takes a lock. Promptness is therefore
    ``config.PROVIDER_WAIT_POLL_SECONDS``.

    Checked BEFORE the first sleep, so an already-cancelled caller does not wait
    one poll interval to be told.
    """
    deadline = _now() + max(0.0, float(seconds))
    while True:
        if cancelled is not None and cancelled():
            return False
        remaining = deadline - _now()
        if remaining <= 0:
            return True
        _SLEEP(min(remaining, config.PROVIDER_WAIT_POLL_SECONDS))


# ===========================================================================
# THE PACER
# ===========================================================================

class QuotaUnknown(RuntimeError):
    """A scope's quota is UNKNOWN, so nothing may be dispatched under it.

    RAISED AT RESERVATION, ABOVE THE SEND, so the refusal costs nothing and no
    request leaves the process. It is a ``RuntimeError`` and deliberately not a
    ``ValueError``: a stray ``except ValueError`` around a config read must not
    be able to eat it, and Stage 5's own ``except Exception`` classifies it as
    a LOCAL failure rather than a provider one -- which is what it is.

    WHY AN UNKNOWN QUOTA REFUSES RATHER THAN DISPATCHING FREELY. The pacer
    exists because this account throttles; a scope nobody has measured is
    exactly the scope most likely to throttle, and the pre-refusal behaviour --
    ``None`` meaning "not paced" -- turned "we have not looked this up" into
    "send as fast as the process can". The remedy is a number from the
    provider's console, and the message names the constant, the scope and the
    family so an operator can fetch precisely the value that is missing.
    """

    def __init__(self, scope: str, family: str, constant: str):
        self.scope, self.family, self.constant = scope, family, constant
        super().__init__(
            f"the {family} quota for scope {scope!r} is UNKNOWN, so nothing "
            f"may be dispatched under it. Set config.{constant}[{scope!r}] to "
            f"the value this account's provider console reports (an int, in "
            f"units of per minute), or -- only if that family genuinely does "
            f"not govern this scope -- to config.QUOTA_NOT_APPLICABLE with the "
            f"API semantics argued at the row. Nothing has been sent.")


class ReservationExceedsQuota(RuntimeError):
    """One request's token reservation is larger than the whole token window.

    A CONFIGURATION DEFECT and it is raised rather than waited on, because no
    amount of waiting admits it: the provider would throttle it forever, and a
    pacer that queued it would hang the run. The remedy is the configured quota
    (or a quota increase), never a smaller ``max_tokens`` -- request identity is
    frozen and this module will not change it to make pacing easier."""


class WaitCancelled(Exception):
    """Internal: a pacing wait was cancelled. Never escapes ``execute()``."""


class _Slot:
    """One scheduled start in a token window. Mutable: settled on completion."""

    __slots__ = ("start", "tokens")

    def __init__(self, start: float, tokens: int):
        self.start = start
        self.tokens = tokens


class _ScopeState:
    __slots__ = ("next_free", "last_start", "slots")

    def __init__(self):
        self.next_free = float("-inf")
        self.last_start = float("-inf")
        self.slots = deque()


class Permit(NamedTuple):
    """What ``reserve`` scheduled. ``slot`` is None when tokens are unpaced."""

    scope: str
    start: float
    waited_s: float
    reserved_tokens: int
    slot: Optional[_Slot]


def _earliest_token_start(slots, t: float, need: int, cap: int,
                          window: float) -> float:
    """Earliest start >= ``t`` at which ``need`` more tokens fit.

    A slot is inside the window ending at ``t`` when ``start > t - window``
    (half-open), so dropping slot s from the window requires
    ``t >= s.start + window``. Slots are walked oldest first. A module function
    rather than a static method so the package's decorator inventory
    (``tests/test_package_invariants.py`` 2i) has nothing new to declare.
    """
    inside = [s for s in slots if s.start > t - window]
    total = sum(s.tokens for s in inside)
    if total + need <= cap:
        return t
    for s in inside:
        total -= s.tokens
        candidate = s.start + window
        if total + need <= cap:
            return max(t, candidate)
    return max(t, (inside[-1].start + window) if inside else t)


def effective_limits(scope: str) -> Tuple[Optional[float], Optional[int],
                                           float, Optional[int]]:
    """``(interval_s, tokens_per_window, window_s, starts_per_window)``.

    Read from ``config`` on EVERY call rather than captured, so a test (or a
    probe) that rebinds a limit on the module is what the pacer obeys.

    THE REQUEST LIMIT IS A MINIMUM SPACING, NOT A SLIDING-WINDOW COUNT. A
    sliding window of R starts per W seconds admits all R at once -- a BURST --
    and a provider whose own limiter is a token bucket with a small burst
    allowance throttles exactly that. Spacing starts ``W / R`` apart is strictly
    more conservative: it admits at most R in ANY window of length W, which is
    the sliding-window property, and never more than one at a time.
    """
    rpm, tpm = config.provider_quota(scope)
    headroom = config.PROVIDER_PACING_HEADROOM
    window = float(config.PROVIDER_QUOTA_WINDOW_SECONDS)
    # UNKNOWN AND NOT-APPLICABLE BOTH YIELD "no spacing" HERE, AND THAT IS NOT
    # A CONFLATION: this function only describes the arithmetic. Which of the
    # two a scope is in decides whether anything may be SENT, and that is
    # `require_known_quota`'s job, called by `reserve` above the send. Reporting
    # surfaces ask `quota_state` so they can print the difference.
    rpm_n = rpm if isinstance(rpm, int) and not isinstance(rpm, bool) else None
    tpm_n = tpm if isinstance(tpm, int) and not isinstance(tpm, bool) else None
    starts = None if rpm_n is None else max(1, math.floor(rpm_n * headroom))
    interval = None if starts is None else window / starts
    tokens = None if tpm_n is None else max(1, math.floor(tpm_n * headroom))
    return interval, tokens, window, starts


QUOTA_STATE_CONFIGURED = "configured"
QUOTA_STATE_UNKNOWN = "unknown"
QUOTA_STATE_NOT_APPLICABLE = "not_applicable"
QUOTA_STATES = (QUOTA_STATE_CONFIGURED, QUOTA_STATE_UNKNOWN,
                QUOTA_STATE_NOT_APPLICABLE)
"""What is known about one scope's one quota family. A CLOSED vocabulary.

Read by `require_known_quota` (which refuses on UNKNOWN), by `describe_pacing`
and by `report_lines`, so the run-start announcement and the closing block say
"UNKNOWN -- dispatch refused" and "not metered on this axis" in different words
rather than both printing "not paced"."""


def quota_state(scope: str, family: str) -> str:
    """``QUOTA_STATES`` member for ``scope``'s ``requests``/``tokens`` quota."""
    rpm, tpm = config.provider_quota(scope)
    value = rpm if family == "requests" else tpm
    if value == config.QUOTA_NOT_APPLICABLE:
        return QUOTA_STATE_NOT_APPLICABLE
    if isinstance(value, int) and not isinstance(value, bool):
        return QUOTA_STATE_CONFIGURED
    return QUOTA_STATE_UNKNOWN


def require_known_quota(scope: str, tokens: int = 0) -> None:
    """Raise ``QuotaUnknown`` for a family this reservation would consume.

    PER FAMILY, AND THE TOKEN FAMILY ONLY WHEN TOKENS ARE ACTUALLY RESERVED.
    The first version demanded BOTH families be settled for every reservation,
    on the argument that a scope can be throttled on the axis nobody measured.
    That argument is right about a reservation that BURNS tokens and wrong
    about one that does not, and the difference is not cosmetic: a zero-token
    reservation cannot consume a tokens-per-minute allowance, so refusing it
    for an unknown TPM is a refusal the caller cannot satisfy by any action
    except configuring a number nobody has. MEASURED: it aborted
    `tests/test_provider_resilience.py` at a bare `reserve(scope)` -- a call
    reserving nothing -- and took two earlier checks with it.

    THE REQUESTS FAMILY IS ALWAYS REQUIRED, because every reservation consumes
    a request slot by definition; that is what a reservation IS.

    WHAT MAKES THE NUMBER-BASED RULE SAFE IS NOT IN THIS FUNCTION, and that is
    worth saying here rather than leaving a reader to reconstruct it. Asking
    ``tokens > 0`` answers "does this consume a token allowance" with a value a
    BUG CAN PRODUCE: an inference call whose estimate came back zero would be
    admitted under an UNKNOWN token quota. ``execute`` closes that by making the
    caller DECLARE ``RESERVATION_INFERENCE`` or ``RESERVATION_MANAGEMENT`` and
    refusing a non-positive inference reservation outright, so by the time this
    guard is reached every inference call has ``tokens > 0`` by construction.
    This function is therefore correct for the callers that come through
    ``execute``; a caller reaching ``reserve`` directly with a fabricated zero
    is outside it, which is why the declaration lives on the billed door.
    """
    if quota_state(scope, "requests") == QUOTA_STATE_UNKNOWN:
        raise QuotaUnknown(scope, "requests", "PROVIDER_REQUESTS_PER_MINUTE")
    if int(tokens) > 0 and quota_state(scope, "tokens") == QUOTA_STATE_UNKNOWN:
        raise QuotaUnknown(scope, "tokens", "PROVIDER_TOKENS_PER_MINUTE")


class QuotaPacer:
    """Schedules wire attempts per quota scope. Thread-safe. FIFO by arrival.

    SCHEDULING IS DECIDED UNDER THE LOCK AND WAITED OUT OUTSIDE IT. ``reserve``
    computes the earliest start that satisfies every limit, RECORDS it (so the
    next arrival is scheduled after it), and returns; ``wait`` then sleeps until
    that start. That is what makes the schedule deterministic and checkable: the
    set of scheduled starts is a fact the pacer holds, and "no window ever held
    more than the limit" is a property of that set rather than of thread timing.

    STARTS ARE NON-DECREASING BY CONSTRUCTION -- every new start is at or after
    the previous one -- which is what lets the token window be checked only at
    the new start rather than at every later instant.
    """

    def __init__(self, clock: Optional[Callable[[], float]] = None,
                 sleep: Optional[Callable[[float], None]] = None):
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._scopes: Dict[str, _ScopeState] = {}
        self._stats: Dict[str, Dict[str, float]] = {}
        self._history: deque = deque(maxlen=8192)

    # -- time ---------------------------------------------------------------

    def _now(self) -> float:
        return (self._clock or _CLOCK)()

    def _do_sleep(self, seconds: float) -> None:
        (self._sleep or _SLEEP)(seconds)

    # -- state --------------------------------------------------------------

    def reset(self) -> None:
        """Forget every schedule and statistic. Called at a run's start."""
        with self._lock:
            self._scopes.clear()
            self._stats.clear()
            self._history.clear()

    def _stat(self, scope: str) -> Dict[str, float]:
        return self._stats.setdefault(scope, {
            "acquired": 0, "waited": 0, "wait_s_total": 0.0,
            "wait_s_max": 0.0, "cancelled": 0, "tokens_underreserved": 0})

    # -- scheduling ---------------------------------------------------------

    def reserve(self, scope: str, tokens: int = 0, *, slots: int = 1) -> Permit:
        """Schedule one policy attempt's start. Does not wait.

        Args:
            tokens: this attempt's reservation -- estimated input plus the
                request's max_tokens (see ``evaluation``'s reservation rule).
            slots: how many WIRE attempts this one policy attempt may become.
                1 when the SDK's own retries are disabled; ``1 + retries`` when
                an SDK retries internally where this module cannot reach it.
                Reserving every attempt the SDK MIGHT make is what keeps the
                rate bound true of the wire, not only of the policy.
        """
        # ABOVE EVERY OTHER LINE IN THIS FUNCTION, so an unknown quota costs a
        # refusal rather than a request. `reserve` is the one door every wire
        # attempt passes through -- sync and async, first attempt and retry --
        # which is what makes this the place the rule cannot be bypassed.
        require_known_quota(scope, int(tokens) * int(slots))
        interval, token_cap, window, _ = effective_limits(scope)
        need = int(tokens) * int(slots)
        # THE SCHEDULE IS KEYED ON THE ALLOWANCE, NOT ON THE CALLER. Two scopes
        # that draw on one provider limit -- `openai` and `ragas_judge` share
        # one account's chat-completions quota for one model -- must interleave
        # on ONE schedule, or each paces to the whole configured rate and
        # together they send twice it. That is the burst this module exists to
        # prevent, reached through the NAMING rather than through concurrency,
        # and keying `_scopes` by scope was how it would have happened.
        # `config.PROVIDER_QUOTA_BUCKETS` is where the mapping is argued.
        bucket = config.provider_quota_bucket(scope)
        with self._lock:
            now = self._now()
            # THE STATISTICS STAY PER SCOPE, and that asymmetry is deliberate:
            # the SCHEDULE is a property of the allowance, but "how long did the
            # ragas judge wait" is a question about a CALLER, and folding two
            # callers' waits into one row would make the run-end block unable to
            # say which of them was being held.
            stat = self._stat(scope)
            st = self._scopes.setdefault(bucket, _ScopeState())
            t = max(now, st.last_start)
            if interval is not None:
                t = max(t, st.next_free)
            slot = None
            if token_cap is not None:
                if need > token_cap:
                    raise ReservationExceedsQuota(
                        f"one {scope} request reserves {need} tokens and the "
                        f"configured window admits {token_cap} "
                        f"(config.PROVIDER_TOKENS_PER_MINUTE[{scope!r}] x "
                        f"PROVIDER_PACING_HEADROOM). No wait can admit it; raise "
                        f"the configured quota to the account's real one.")
                while st.slots and st.slots[0].start <= now - window:
                    st.slots.popleft()
                t = _earliest_token_start(st.slots, t, need, token_cap, window)
                slot = _Slot(t, need)
                st.slots.append(slot)
            if interval is not None:
                st.next_free = t + int(slots) * interval
            st.last_start = t
            waited = max(0.0, t - now)
            stat["acquired"] += 1
            if waited > 0:
                stat["waited"] += 1
                stat["wait_s_total"] += waited
                stat["wait_s_max"] = max(stat["wait_s_max"], waited)
            # THE BUCKET IS APPENDED RATHER THAN INSERTED. `history()` is read
            # positionally -- `h[0]` is the scope and `h[1]` the start -- so a
            # trailing field adds the allowance without moving anything a
            # reader already depends on.
            self._history.append((scope, t, need, int(slots), bucket))
        _bump(PROVIDER_PACING_WAITS, f"{scope}:acquired")
        if waited > 0:
            _bump(PROVIDER_PACING_WAITS, f"{scope}:waited")
        return Permit(scope, t, waited, need, slot)

    def wait(self, permit: Permit,
             cancelled: Optional[Callable[[], bool]] = None) -> None:
        """Sleep until ``permit.start``. Raises ``WaitCancelled``.

        A cancelled permit's token reservation is refunded; its place in the
        request spacing is NOT, because later arrivals were already scheduled
        behind it and pulling them forward would have to rewrite their starts.
        The gap is the conservative direction.
        """
        # A SLOT THAT IS ALREADY DUE IS NOT A WAIT, and consulting the
        # cancellation there would make the policy a SECOND gate in front of
        # every first attempt -- duplicating the shutdown and spend gates the
        # Stage 5 call sites already run immediately before the call, and
        # masking any defect in them. Cancellation governs WAITING; the gates
        # govern ISSUING.
        if permit.start - self._now() <= 0:
            return
        while True:
            if cancelled is not None and cancelled():
                with self._lock:
                    # BY BUCKET, matching `reserve`: the slot being refunded
                    # lives in the ALLOWANCE's window, not in a per-caller one.
                    st = self._scopes.get(
                        config.provider_quota_bucket(permit.scope))
                    if permit.slot is not None and st is not None:
                        try:
                            st.slots.remove(permit.slot)
                        except ValueError:
                            pass  # already pruned out of the window: nothing held
                    self._stat(permit.scope)["cancelled"] += 1
                _bump(PROVIDER_PACING_WAITS, f"{permit.scope}:cancelled")
                raise WaitCancelled(permit.scope)
            remaining = permit.start - self._now()
            if remaining <= 0:
                return
            self._do_sleep(min(remaining, config.PROVIDER_WAIT_POLL_SECONDS))

    def acquire(self, scope: str, tokens: int = 0, *, slots: int = 1,
                cancelled: Optional[Callable[[], bool]] = None) -> Permit:
        """``reserve`` then ``wait``. What every wire attempt calls."""
        permit = self.reserve(scope, tokens, slots=slots)
        self.wait(permit, cancelled)
        return permit

    def settle(self, permit: Permit, *, billing: str,
               actual_tokens: Optional[int] = None) -> None:
        """Adjust an attempt's token reservation to what it actually burned.

        ``not_billed`` refunds the reservation: the provider refused before
        inference. A success with a usage figure is adjusted to it -- Bedrock's
        own documented behaviour, reserve at dispatch and adjust on completion.
        A possibly-billed failure keeps the whole reservation, which is the
        conservative reading of "we do not know what it burned".
        """
        if permit.slot is None:
            return
        with self._lock:
            if billing == BILLING_NOT_BILLED:
                permit.slot.tokens = 0
            elif actual_tokens is not None:
                if actual_tokens > permit.slot.tokens:
                    self._stat(permit.scope)["tokens_underreserved"] += 1
                    _bump(PROVIDER_PACING_WAITS,
                          f"{permit.scope}:tokens_underreserved")
                permit.slot.tokens = int(actual_tokens)

    # -- reading ------------------------------------------------------------

    def history(self, scope: Optional[str] = None) -> List[Tuple]:
        """Scheduled starts, oldest first: ``(scope, start, tokens, slots)``."""
        with self._lock:
            return [h for h in self._history if scope is None or h[0] == scope]

    def stats(self) -> Dict[str, Dict[str, float]]:
        with self._lock:
            return {k: dict(v) for k, v in self._stats.items()}


PACER = QuotaPacer()
"""The one process-wide pacer. See the module docstring: PROCESS-LOCAL."""


# ===========================================================================
# ONE PROCESS PER PROVIDER ALLOWANCE
# ===========================================================================
#
# THE KEY IS THE BUCKET, NOT THE SCOPE, AND THAT CORRECTION IS THE WHOLE OF
# THIS SECTION'S CONTRACT. A scope names a CALLER; a bucket names the PROVIDER
# LIMIT it consumes, and `QuotaPacer.reserve` schedules against the BUCKET
# precisely so two callers sharing one allowance cannot gain independent
# allowances by having two names. That is `config.PROVIDER_QUOTA_BUCKETS`'
# stated purpose.
#
# KEYED ON THE SCOPE, THIS LOCK DEFEATED THAT RULING AT THE PROCESS BOUNDARY.
# Measured: `openai` and `ragas_judge` both draw on `openai:chat-completions`
# and hashed to two DIFFERENT lock files, so a campaign and a ragas run took
# one lock each, both proceeded, and each paced the WHOLE configured rate for
# one shared allowance -- twice the limit, from two separate names, which is
# verbatim the defect the bucket layer exists to remove. The in-process half
# prevented it and the cross-process half reintroduced it.
#
# `bedrock_anthropic`'s bucket has exactly one member, so on the shipped arm
# the two keyings are indistinguishable and the defect is invisible. That is
# why it is stated here rather than left to be found by whoever first runs a
# ragas pass beside a campaign on the OpenAI arm.

SCOPE_LOCK_FILE_PREFIX = "oncotriage-provider-scope-"
"""The lock-file prefix for a quota scope. DISTINCT FROM EVERY OTHER PROGRAM'S.

Every lock in this project lives in ONE per-user 0700 directory and what keeps
them apart is the prefix -- ``control.lock_directory``'s own rule. A batch run,
an ablation study, a serial test run and a quota scope guard four different
things and must not refuse each other, so this prefix is load-bearing rather
than decorative."""


class AlreadyPacing(control.AlreadyRunning):
    """Another live process on this host is already pacing this quota scope.

    A SIBLING OF THE BATCH RUNNER'S AND THE STUDY'S REFUSALS, NOT A REUSE, on
    ``control.AlreadyRunning``'s own argument: the three are raised by
    different programs, name different consequences and are remediated
    differently, and a caller holding more than one lock must be able to tell
    from the TYPE which refusal it caught rather than by parsing a path out of
    a message. Here the consequence is specific: two processes each pacing to
    the whole configured quota produce twice the configured rate, which is the
    burst the pacer exists to prevent and which the provider answers with the
    ``ThrottlingException`` storm this module was built after."""


class ScopeLockUnavailable(control.LockUnavailable):
    """The scope lock could not be ATTEMPTED. Carries the path and the errno.

    A DIFFERENT FINDING FROM ``AlreadyPacing`` AND NOT A SUBCLASS OF IT -- they
    are siblings under ``control``'s two bases, so ``except ScopeLockUnavailable``
    cannot catch a held lock and vice versa. That one is benign and
    self-clearing; this means the lock file could not be opened at all and no
    amount of waiting fixes it."""


_HELD_ALLOWANCES = set()
"""Lock paths this PROCESS already holds. The re-entrancy record, NOT the lock.

THE FLOCK IS THE LOCK AND THE KERNEL RELEASES IT, however the process exits;
nothing here weakens that. This set answers only "have we already taken this
one", which flock itself cannot be asked -- a second ``LOCK_EX | LOCK_NB`` on a
second descriptor for the same file FAILS against this process's own hold
rather than reporting re-entry, which is the measurement at
``exclusive_scope_lock``."""

_HELD_LOCK = threading.Lock()
"""Guards ``_HELD_ALLOWANCES``.

A PLAIN ``Lock`` AND NOT AN ``RLock``: nothing taken under it calls back into
this module, and both critical sections are a set membership test and a
discard. An ``RLock`` here would be claiming a re-entrancy the code does not
have."""


def _lock_record_value(scope, path):
    """The holder record's one field: the CALLER and the ALLOWANCE it holds.

    ``control.hold_exclusive_lock`` offers a SINGLE extra ``record_key``, so
    both facts share it -- and both are needed, neither substituting for the
    other. The lock FILE is a digest of the bucket, so without the bucket a
    refusal names a path nobody can map back to an allowance; without the scope
    an operator cannot tell WHICH caller holds it. The actionable sentence is
    "you are running ragas_judge; openai already paces openai:chat-completions",
    and it needs both halves.

    A CALLER-SUPPLIED ``path`` GETS THE SCOPE ALONE, because no bucket was
    derived for it -- naming one would be inventing the fact this record exists
    to carry.
    """
    if path is not None:
        return str(scope)
    try:
        return f"{scope} (allowance: {config.provider_quota_bucket(scope)})"
    except ValueError:
        # UNREACHABLE FROM ``exclusive_scope_lock``, which refuses such a scope
        # above before any lock is taken. Kept because this function is
        # reachable on its own, and a record that RAISED while writing the
        # holder's identity would turn a lock this process successfully holds
        # into a failure -- reporting a configuration defect by discarding a
        # guarantee that was already established.
        return str(scope)


def bucket_lock_path(bucket: str) -> str:
    """The lock file for one provider ALLOWANCE, in this user's lock directory.

    THE KEY IS THE BUCKET STRING. Everything the section header above argues
    lands here: two scopes drawing on one allowance resolve to one path, so the
    second process is refused rather than pacing the same limit in parallel.

    PURE -- it creates nothing, on ``control.lock_directory``'s own split.
    """
    digest = hashlib.sha256(str(bucket).encode("utf-8")).hexdigest()
    return os.path.join(control.lock_directory(),
                        f"{SCOPE_LOCK_FILE_PREFIX}{digest[:16]}.lock")


def scope_lock_path(scope: str) -> str:
    """The lock file a given CALLER takes: ``bucket_lock_path`` of its bucket.

    A CALLER KNOWS ITS SCOPE AND NEVER ITS BUCKET, which is why this function
    survives the re-key rather than every call site learning the table. The
    derivation happens once, here.

    RAISES ``ScopeLockUnavailable`` FOR A SCOPE WITH NO BUCKET, and the
    conversion is the point rather than plumbing. ``config.provider_quota_bucket``
    raises ``ValueError`` by deliberate design -- a silent fallback to "the
    scope is its own bucket" would hand a newly-added scope its own allowance,
    which is the defect that table exists to remove. But a bare ``ValueError``
    is caught by neither ``except AlreadyPacing`` nor ``except
    ScopeLockUnavailable``, so at an entry point it would escape both clauses
    and reach CPython's default handler as a traceback: no diagnosis, no path,
    no statement that nothing has been billed. That is the exact failure the
    ``LockUnavailable`` clauses were added to remove, reintroduced one layer
    down. ``ScopeLockUnavailable`` is the honest class for it -- its own
    contract is "the lock could not be ATTEMPTED", which is true here, and the
    caller's action is identical: this run cannot establish that it is the only
    one pacing the allowance, and running without that guarantee is how two
    runs send twice the configured rate.
    """
    try:
        bucket = config.provider_quota_bucket(scope)
    except ValueError as exc:
        # ``LockUnavailable(path, cause)`` IS POSITIONAL and derives ``errno``
        # and ``strerror`` FROM THE CAUSE, so `provider_quota_bucket`'s own
        # message -- which already names the table and says what it is for --
        # becomes the diagnosis the refusal prints, rather than a second
        # sentence restating it.
        #
        # THERE IS NO PATH TO NAME, and inventing a plausible-looking one would
        # send an operator to look for a file that was never going to exist.
        # The marker says that instead.
        raise ScopeLockUnavailable(
            f"(no lock path: quota scope {scope!r} has no declared provider "
            f"allowance)", exc) from exc
    return bucket_lock_path(bucket)


def _legacy_scope_lock_path(scope: str) -> str:
    """The pre-re-key derivation, kept ONLY as the control's subject.

    Never called by the mechanism. ``tests/test_provider_scope_lock.py`` drives
    it to show that the scope-keyed form gives two different locks for one
    allowance, which is the defect the bucket key removes; a control that
    retyped the old body would be testing the retyping.

    ``control.lock_file_path`` IS DELIBERATELY NOT USED, AND THE REASON IS A
    DEFECT IT WOULD HAVE INTRODUCED. That helper keys on
    ``os.path.realpath(str(key))`` -- correct for its three existing callers,
    whose keys are a checkpoint directory, a database file and a checkout, and
    where resolving symlinks is exactly what makes "one directory, one lock"
    true. A quota scope is NOT a path: it is an opaque name like
    ``bedrock_anthropic``, and ``realpath`` of a bare name resolves it against
    the CURRENT WORKING DIRECTORY. Two runs of the same scope started from two
    directories would hash to two different digests, take two different lock
    files, and BOTH RUN -- which is the precise failure this lock exists to
    prevent, reintroduced by the helper meant to prevent it.

    So the digest is taken over the scope STRING itself, which is
    CWD-independent by construction, and everything else is reused: the 0700
    uid-keyed directory (``control.lock_directory``), the ``O_NOFOLLOW`` open,
    the holder record and the kernel's release.

    PURE -- it creates nothing, on ``control.lock_directory``'s own split, so a
    caller that only wants to PRINT the path does not bring a directory into
    existence by asking.
    """
    digest = hashlib.sha256(str(scope).encode("utf-8")).hexdigest()
    return os.path.join(control.lock_directory(),
                        f"{SCOPE_LOCK_FILE_PREFIX}{digest[:16]}.lock")


def scope_lock_refusal_lines(exc) -> List[str]:
    """A held-scope refusal, as the lines an entry point prints."""
    return control.already_running_lines(
        exc,
        header=("[Pacing] REFUSING TO RUN: another process on this host is "
                "already pacing this provider quota scope."),
        record_keys=("pid", "host", "user", "started", "scope"),
        key_width=9,
        body=[
            "",
            "        WHY THIS IS REFUSED RATHER THAN QUEUED. The pacer's state "
            "is this",
            "        process's memory, so two runs do not see each other: each "
            "would pace",
            "        to the WHOLE configured quota and together they would "
            "send twice it --",
            "        which is the burst the pacer exists to prevent and what "
            "the provider",
            "        answers with a throttling storm.",
            "",
            "        Either wait for the holder above to finish, or -- if two "
            "runs are",
            "        genuinely wanted -- halve "
            "config.PROVIDER_REQUESTS_PER_MINUTE for this",
            "        scope in BOTH of them, which is the divide-by-N rule "
            "described at",
            "        provider_resilience's PROCESS-LOCAL section.",
            "",
            "        NOTHING HAS BEEN SENT AND NOTHING HAS BEEN BILLED.",
        ])


def scope_lock_unavailable_lines(exc) -> List[str]:
    """An unopenable scope lock, as the lines an entry point prints."""
    return control.lock_unavailable_lines(
        exc,
        header=("[Pacing] REFUSING TO RUN: the provider scope lock could not "
                "be taken."),
        consequence=[
            "        This is NOT 'another process holds it' -- that is a "
            "different refusal.",
            "        The lock file could not be opened at all, so this run "
            "cannot establish",
            "        that it is the only one pacing this scope, and running "
            "without that",
            "        guarantee is how two runs send twice the configured rate.",
        ])


@contextlib.contextmanager
def exclusive_scope_lock(scope: str, path: Optional[str] = None):
    """Hold an exclusive, non-blocking flock on ``scope``'s lock for the block.

    Yields the lock file's path. The mechanism -- the 0700 directory, the
    ``O_NOFOLLOW`` open, the non-blocking flock, the UTC holder record written
    only after the lock is held, the release BY THE KERNEL however the process
    exits -- is ``control.hold_exclusive_lock``. What is decided here is the
    key (``scope_lock_path``), the two exception classes and the extra field
    the record names.

    THE RECORD NAMES THE SCOPE, which is the one thing an operator reading a
    refusal needs and cannot derive: the file name is a digest, so without this
    field the refusal would name a path nobody can map back to a provider.

    THE DECORATOR IS NOT DECORATION. Without it ``with exclusive_scope_lock():``
    raises ``AttributeError`` on a generator, and
    ``tests/test_package_invariants.py``'s decorator inventory is what makes
    that loss visible in a bucket run rather than only when somebody starts a
    campaign.
    """
    target = scope_lock_path(scope) if path is None else path

    # ── RE-ENTRANT PER ALLOWANCE, AND IT IS A CORRECTNESS FIX ───────────────
    #
    # MEASURED, NOT ARGUED: taking one allowance's lock twice in one process
    # raises AlreadyPacing -- flock is per file DESCRIPTOR, so the second open
    # is a second fd and LOCK_EX|LOCK_NB on it fails against this process's own
    # hold. Keyed on the scope that could not happen (two scopes, two files);
    # keyed on the BUCKET it is one program away, the moment anything wraps two
    # callers of one allowance -- `openai` and `ragas_judge` being the pair that
    # exists today. The process would refuse ITSELF, naming its own pid as the
    # holder, which is the most confusing refusal this mechanism could emit.
    #
    # A NO-OP RATHER THAN A SECOND LOCK, because a process that already holds
    # the allowance IS the single process for it -- the invariant is already
    # satisfied and re-entry is correct semantics rather than a tolerated
    # weakening. The registry is keyed on the resolved PATH, so a caller that
    # named `path=` explicitly is covered by the same rule.
    with _HELD_LOCK:
        reentrant = target in _HELD_ALLOWANCES
        if not reentrant:
            _HELD_ALLOWANCES.add(target)
    if reentrant:
        yield target
        return

    try:
        with control.hold_exclusive_lock(
                target,
                already_running=AlreadyPacing,
                lock_unavailable=ScopeLockUnavailable,
                record_key="scope",
                # BOTH FACTS IN THE ONE FIELD `control` OFFERS. The file name is
                # a digest of the BUCKET, so without the bucket an operator
                # cannot map the refusal back to an allowance -- and without the
                # scope they cannot tell WHICH caller is holding it. The
                # actionable sentence is "you are running ragas_judge; openai
                # already paces openai:chat-completions", and it needs both.
                record_value=_lock_record_value(scope, path),
                # ONLY WHEN WE DERIVED THE PATH, on the batch runner's own
                # reasoning: a caller who named the lock file directly owns its
                # directory, and creating one under a path this function was
                # handed would be a side effect nobody asked for.
                ensure_directory=path is None) as held:
            yield held
    finally:
        # RELEASED ONLY BY THE ACQUIRER, in a `finally`, so a refusal or a raise
        # inside the block cannot leave this process believing it still holds an
        # allowance it does not. The FLOCK itself is released by the kernel; this
        # set is only the re-entrancy record.
        with _HELD_LOCK:
            _HELD_ALLOWANCES.discard(target)


# ===========================================================================
# THE BACKOFF
# ===========================================================================

def backoff_ceiling_seconds(retry_number: int) -> float:
    """``min(window, base * 2**(retry_number - 1))`` -- the full-jitter ceiling.

    CAPPED AT THE QUOTA WINDOW, which is what lets a late retry reach the next
    per-minute window rather than landing, as botocore's 20-second cap did in
    the smoke run, inside the window that refused it.
    """
    base = float(config.MATCHING_RETRY_BASE_SECONDS)
    cap = float(config.PROVIDER_QUOTA_WINDOW_SECONDS)
    return min(cap, base * (2 ** (max(1, int(retry_number)) - 1)))


def full_jitter_delay(retry_number: int,
                      rng: Optional[random.Random] = None) -> float:
    """AWS's FULL JITTER: ``uniform(0, ceiling)``.

    Full rather than "equal" or "decorrelated" jitter because it spreads a
    burst of simultaneous failures across the whole interval, which is the
    failure the smoke run had: every throttled request retrying on the same
    schedule re-creates the burst that was throttled.
    """
    return (rng or _RNG).uniform(0.0, backoff_ceiling_seconds(retry_number))


def worst_case_backoff_seconds(policy_attempts: int) -> float:
    """The most one logical call can spend backing off.

    ``(policy_attempts - 1) x window``, not the sum of the doubling ceilings:
    a provider's ``retry-after`` hint is honoured up to the window, so any retry
    can wait the whole window.
    """
    return max(0, int(policy_attempts) - 1) * float(
        config.PROVIDER_QUOTA_WINDOW_SECONDS)


def worst_case_pacing_wait_seconds(scope: str, concurrency: int,
                                   slots: int = 1) -> float:
    """The longest one attempt can wait for its slot with ``concurrency`` peers.

    FIFO scheduling puts an arrival behind at most ``concurrency - 1`` others,
    each holding ``slots`` intervals. Zero for an unpaced scope. A token window,
    when configured, is reported by ``describe_pacing`` separately rather than
    folded in, because its bound depends on reservation sizes nobody knows at
    run start.
    """
    interval, _tokens, _window, _starts = effective_limits(scope)
    if interval is None:
        return 0.0
    return max(0, int(concurrency) - 1) * int(slots) * interval


# ===========================================================================
# THE POLICY
# ===========================================================================

class AttemptBudgetConfigurationError(RuntimeError):
    """The SDK's own attempts exceed the total budget; the budget cannot hold."""


class ZeroTokenInferenceReservation(RuntimeError):
    """An INFERENCE call reserved no tokens. A DEFECT, refused before the send.

    An inference request consumes model tokens by definition, so a reservation
    of zero is not a small reservation -- it is an estimate that did not
    happen. Admitting it would exceed the token window by exactly the true
    usage AND, when the token quota is UNKNOWN, would slip past
    ``require_known_quota`` entirely, because that guard asks the number rather
    than the kind.

    RAISED RATHER THAN CORRECTED, and the alternative was considered: the pacer
    cannot know the true figure, and substituting one would put a number no
    renderer produced into the window the quota is enforced against. It raises
    at reservation, above the send, so the refusal costs nothing and names the
    caller's own arithmetic. A ``RuntimeError`` and not a ``ValueError``, on
    ``QuotaUnknown``'s footing: Stage 5's own ``except Exception`` classifies it
    as a LOCAL failure -- which is what a defect in this process is -- and a
    stray ``except ValueError`` around a token estimate must not eat it."""


class AttemptVerdict(NamedTuple):
    category: str
    failure_class: str
    billing: str
    retry_after_s: Optional[float]


def verdict_for(category: str,
                retry_after_s: Optional[float] = None) -> AttemptVerdict:
    """The policy row for a category. An unknown category is ``unclassified``."""
    if category not in CATEGORY_POLICY:
        category = CATEGORY_UNCLASSIFIED
    failure_class, billing = CATEGORY_POLICY[category]
    return AttemptVerdict(category, failure_class, billing, retry_after_s)


# ===========================================================================
# THE ONE OPENAI-SDK FAILURE CLASSIFIER
# ===========================================================================
#
# WHY IT IS HERE AND NOT AT A CALL SITE. Three billed paths in this project
# talk to an OpenAI-shaped SDK -- Stage 5's `openai` arm, the ragas judge and
# the ragas embedder, and the rater's four batch-management calls -- and until
# this section existed exactly one of them could classify a failure. The other
# three had no classifier at all, so every one of their failures would have
# reached `verdict_for` as `unclassified`: NEVER RETRIED (a 429 included) and
# charged as possibly billed. A second copy at each call site is the shape this
# project removes everywhere else; one owner is what makes "retry a throttle,
# never retry a validation error" one rule rather than four.
#
# THE CONVERSE ARM IS DELIBERATELY NOT HERE. It reads
# `bedrock_anthropic_adapter`'s own taxonomy, which is provider-specific and
# belongs beside that adapter; `oncotriage/agent/evaluation.py` keeps that
# branch and delegates this one.

def http_status_of(exc: BaseException) -> Optional[int]:
    """The HTTP status behind an SDK exception, or None.

    READ FROM THREE PLACES because three shapes reach this project: the OpenAI
    SDK carries it on the exception (``APIStatusError.status_code``); several
    clients -- and the stand-ins this project's tests install -- carry it on a
    ``response`` object; and a botocore ``ClientError`` carries a plain dict at
    ``.response`` whose ``ResponseMetadata.HTTPStatusCode`` is the only place it
    appears. Nothing is asserted to exist: this runs on a failure path and must
    not raise a second, unrelated exception while classifying the first.

    MOVED HERE FROM ``oncotriage/agent/evaluation.py`` UNCHANGED. That module
    keeps a one-line delegate, because ``classify_warmup_rejection`` reads it
    and is provider-specific.
    """
    _response = getattr(exc, "response", None)
    _metadata = (_response.get("ResponseMetadata")
                 if isinstance(_response, dict) else None)
    for _candidate in (getattr(exc, "status_code", None),
                       getattr(_response, "status_code", None),
                       (_metadata.get("HTTPStatusCode")
                        if isinstance(_metadata, dict) else None)):
        if isinstance(_candidate, int) and not isinstance(_candidate, bool):
            return _candidate
    return None


def retry_after_seconds(exc: BaseException) -> Optional[float]:
    """A ``retry-after`` hint in SECONDS off the exception's response, or None.

    Both shapes this project meets: a botocore ``ClientError`` carries a dict
    with ``ResponseMetadata.HTTPHeaders``; an OpenAI SDK error carries an httpx
    response with ``.headers``. Only the delta-seconds form is read; an
    HTTP-date or a malformed value is counted and ignored, never guessed.
    ``x-amz-retry-after`` is NOT read: its unit is not documented on a page this
    project has verified.

    THE COUNTER IT BUMPS IS THIS MODULE'S OWN, which is the other half of why
    this moved: it lived in ``evaluation`` and reached across into
    ``provider_resilience.PROVIDER_RETRY_OUTCOMES`` to record an unreadable
    header. The reader and the counter are in one module now.
    """
    _resp = getattr(exc, "response", None)
    if isinstance(_resp, dict):
        _headers = (_resp.get("ResponseMetadata") or {}).get("HTTPHeaders")
    else:
        _headers = getattr(_resp, "headers", None)
    if not _headers:
        return None
    try:
        _raw = _headers.get("retry-after")
    except Exception as _hdr_exc:                           # noqa: BLE001
        _bump(PROVIDER_RETRY_OUTCOMES,
              f"retry_after_unreadable:{type(_hdr_exc).__name__}")
        return None
    if _raw is None:
        return None
    try:
        _value = float(_raw)
    except (TypeError, ValueError):
        _bump(PROVIDER_RETRY_OUTCOMES, "retry_after_unreadable:not_seconds")
        return None
    if _value != _value or _value < 0 or _value == float("inf"):
        _bump(PROVIDER_RETRY_OUTCOMES, "retry_after_unreadable:out_of_range")
        return None
    return _value


_OPENAI_CONNECT_PHASE = frozenset({"ConnectError", "ConnectTimeout"})
"""httpx names, under an OpenAI SDK ``APIConnectionError``, for a connection
that never OPENED -- so nothing was sent and a retry is free. Everything else
under that class reached the service and may have been billed."""


def classify_openai_failure(exc: BaseException, *,
                            translation_types: Tuple = ()) -> AttemptVerdict:
    """One failed OpenAI-SDK attempt -> an ``AttemptVerdict``. ONE OWNER.

    Reads the SDK's exception classes by NAME along the MRO -- no ``import
    openai`` here, so this module stays importable on a machine without the SDK
    and a stand-in that merely NAMES itself ``APITimeoutError`` classifies like
    the real one, which is what lets every one of these branches be driven
    offline.

    Args:
        translation_types: exception classes meaning "the response ARRIVED and
            could not be read". Passed in rather than imported: the only such
            class today is ``bedrock_adapter.BedrockResponseTranslationError``,
            and importing an adapter here would put a provider inside the
            provider-agnostic module. Empty for callers that have no such class,
            which is every caller but Stage 5.

    THE CONNECT/LOST SPLIT IS THE ONE JUDGEMENT IN HERE. ``APIConnectionError``
    covers both "the socket never opened" (nothing sent, not billed) and "the
    connection dropped mid-request" (accepted, possibly billed), and the two
    have opposite billing answers. The httpx cause name is the only evidence
    available, so it decides; an unrecognised cause takes the conservative
    branch, which is ``connection_lost``.
    """
    _names = {cls.__name__ for cls in type(exc).__mro__}
    _status = http_status_of(exc)
    _cause = type(getattr(exc, "__cause__", None)).__name__
    if translation_types and isinstance(exc, tuple(translation_types)):
        _policy = CATEGORY_TRANSLATION
    elif "APITimeoutError" in _names:
        _policy = (CATEGORY_CONNECT if _cause in _OPENAI_CONNECT_PHASE
                   else CATEGORY_TIMEOUT)
    elif "APIConnectionError" in _names:
        _policy = (CATEGORY_CONNECT if _cause in _OPENAI_CONNECT_PHASE
                   else CATEGORY_CONNECTION_LOST)
    elif _status == 429:
        _policy = CATEGORY_THROTTLED
    elif _status == 408:
        _policy = CATEGORY_TIMEOUT
    elif _status == 503:
        _policy = CATEGORY_SERVICE_UNAVAILABLE
    elif _status is not None and _status >= 500:
        _policy = CATEGORY_SERVER
    elif _status is not None and 400 <= _status < 500:
        _policy = CATEGORY_CLIENT
    else:
        _policy = CATEGORY_UNCLASSIFIED
    return verdict_for(_policy, retry_after_seconds(exc))


_CALL_STATS: Dict[str, Dict[str, float]] = {}
_UNCONFIRMED_USD: Dict[str, float] = {}


def _note_call(scope: str, *, waited_s: float, retried: bool,
               exhausted: bool) -> None:
    with _COUNTER_LOCK:
        s = _CALL_STATS.setdefault(scope, {
            "calls": 0, "retried_calls": 0, "exhausted_calls": 0,
            "wait_s_total": 0.0, "wait_s_max": 0.0})
        s["calls"] += 1
        s["retried_calls"] += int(retried)
        s["exhausted_calls"] += int(exhausted)
        s["wait_s_total"] += waited_s
        s["wait_s_max"] = max(s["wait_s_max"], waited_s)


def _annotate(exc: BaseException, attempts: int, category: str) -> None:
    """Tag the exception with the policy's facts. Never raises.

    A class that refuses attribute assignment (``__slots__``, a C extension)
    is counted rather than silently left untagged: an untagged transport
    failure is read by the dispatch seam as a LOCAL failure, which an outer
    layer may retry, and that is a budget leak somebody should see.
    """
    try:
        setattr(exc, ATTEMPTS_ATTR, attempts)
        setattr(exc, CATEGORY_ATTR, category)
    except Exception:                                   # noqa: BLE001
        _bump(PROVIDER_RETRY_OUTCOMES, f"annotate_failed:{type(exc).__name__}")


def _settle_abandoned(pacer, permit, scope: str, on_possibly_billed) -> None:
    """A request that was ON THE WIRE when its caller was cancelled.

    THE GAP THIS CLOSES, AND IT IS A REAL ONE RATHER THAN A TIDYING.
    ``asyncio.CancelledError`` is a ``BaseException`` and NOT an ``Exception``,
    so it travels straight through both twins' ``except Exception`` -- and with
    it went every one of that clause's obligations: the pacer's token
    reservation was never settled, ``on_possibly_billed`` never charged, and no
    counter moved. A cancelled ragas run therefore abandoned requests the
    provider had already been asked for and recorded a spend of ZERO for them,
    which is exactly the "assume a zero nobody measured" this module's billing
    vocabulary exists to refuse. ``KeyboardInterrupt`` and ``SystemExit`` reach
    the synchronous twin the same way.

    IT CHARGES THE FULL RESERVATION, never a fraction: nothing here knows what
    the request burned, and the upper bound is the conservative reading -- the
    same one a timeout or a dropped connection already gets.

    IT NEVER RAISES. It runs while a cancellation is propagating, and an
    exception here would replace the caller's own shutdown with an unrelated
    one. The charge callback's failure is counted, exactly as it is on the
    ordinary failure path.
    """
    verdict = verdict_for(CATEGORY_ABANDONED)
    try:
        pacer.settle(permit, billing=verdict.billing)
    except Exception as settle_exc:                     # noqa: BLE001
        _bump(PROVIDER_RETRY_OUTCOMES,
              f"settle_failed:{scope}:{type(settle_exc).__name__}")
    _bump(PROVIDER_RETRY_OUTCOMES,
          f"{RETRY_OUTCOME_CANCELLED}:{scope}:{CATEGORY_ABANDONED}")
    _bump(PROVIDER_UNCONFIRMED_BILLING, f"{scope}:{CATEGORY_ABANDONED}")
    if on_possibly_billed is None:
        return
    try:
        usd = float(on_possibly_billed(verdict) or 0.0)
    except Exception as charge_exc:                     # noqa: BLE001
        _bump(PROVIDER_RETRY_OUTCOMES,
              f"charge_failed:{scope}:{type(charge_exc).__name__}")
        usd = 0.0
    with _COUNTER_LOCK:
        _UNCONFIRMED_USD[scope] = _UNCONFIRMED_USD.get(scope, 0.0) + usd


def _validated_attempt_budget(scope: str, reservation_kind: str,
                              reservation_tokens: int, sdk_attempts: int,
                              max_attempts: Optional[int]
                              ) -> Tuple[int, int, int]:
    """The pre-send argument guards. Returns ``(budget, sdk_attempts, policy)``.

    ONE COPY RATHER THAN TWO, AND THE DUPLICATION WAS REAL. These guards stood
    CHARACTER-FOR-CHARACTER IDENTICAL in ``execute`` and ``execute_async`` --
    the same three raises, the same three messages, the same arithmetic -- which
    is two places for one rule to drift and two places to remember when a fourth
    guard is added. The twins already share every other decision through a
    module-level function (the backoff, the category vocabulary, the counters);
    this is the one block that was copied instead.

    IT IS ALSO WHAT GIVES THE PRE-SEND MARKER ONE SITE TO COVER. Both twins wrap
    exactly this call, so ``PRE_SEND_ATTR`` is attached by one clause per twin
    rather than by one clause per raise -- and a guard added HERE is marked
    without the author of it knowing the marker exists, which is the property
    the ruling asks for.

    EVERY RAISE IS UNCHANGED IN TYPE AND IN MESSAGE. This is an extraction, not
    a redesign: a caller catching ``ValueError`` for an unrecognised kind, or
    ``ZeroTokenInferenceReservation`` for a zero inference reservation, catches
    exactly what it caught before.
    """
    if reservation_kind not in RESERVATION_KINDS:
        raise ValueError(
            f"reservation_kind must be one of {RESERVATION_KINDS}; it is "
            f"{reservation_kind!r}. It is REQUIRED because the alternative is "
            f"deciding 'does this consume model tokens' from the number, which "
            f"is a value a bug can produce.")
    if (reservation_kind == RESERVATION_INFERENCE
            and int(reservation_tokens) <= 0):
        raise ZeroTokenInferenceReservation(
            f"a {scope} INFERENCE call reserved {reservation_tokens!r} tokens. "
            f"An inference request consumes model tokens by definition, so a "
            f"non-positive reservation is an estimate that did not happen -- "
            f"and where config.PROVIDER_TOKENS_PER_MINUTE[{scope!r}] is "
            f"UNKNOWN it would also slip past require_known_quota, which asks "
            f"the NUMBER rather than the KIND, and dispatch under a token "
            f"quota nobody has measured. NOTHING HAS BEEN SENT. Fix the "
            f"caller's token estimate, or declare "
            f"provider_resilience.RESERVATION_MANAGEMENT if this call really "
            f"consumes no model tokens.")
    budget = int(config.MATCHING_CALL_MAX_ATTEMPTS if max_attempts is None
                 else max_attempts)
    sdk_attempts = max(1, int(sdk_attempts))
    if sdk_attempts > budget:
        raise AttemptBudgetConfigurationError(
            f"one {scope} send may make {sdk_attempts} wire attempts on its own "
            f"and the total budget is {budget}; the budget cannot be total. "
            f"Lower the SDK's attempt count or raise "
            f"config.MATCHING_CALL_MAX_ATTEMPTS.")
    return budget, sdk_attempts, max(1, budget // sdk_attempts)


def execute(send: Callable[[], object], *, scope: str,
            reservation_tokens: int,
            reservation_kind: str,
            classify: Callable[[BaseException], AttemptVerdict],
            sdk_attempts: int = 1,
            cancelled: Optional[Callable[[], Optional[BaseException]]] = None,
            on_possibly_billed: Optional[Callable[[AttemptVerdict], float]] = None,
            usage_tokens_of: Optional[Callable[[object], Optional[int]]] = None,
            label: str = "call",
            pacer: Optional[QuotaPacer] = None,
            rng: Optional[random.Random] = None,
            max_attempts: Optional[int] = None):
    """Send one logical call under the pacer and the one retry policy.

    Args:
        send: issues ONE policy attempt. It may raise anything.
        scope: the quota scope (``config.provider_quota``'s key).
        reservation_tokens: one wire attempt's token reservation.
        reservation_kind: a ``RESERVATION_KINDS`` member. REQUIRED, with no
            default, on ``empty_database(db_path, flag)``'s footing: a default
            would let a new billed call site inherit whichever value was chosen
            here, and neither choice is safe to inherit -- ``management``
            re-opens the bypass this argument closes, and ``inference`` refuses
            a batch-management call that is perfectly correct. An ``inference``
            call whose reservation is not positive is refused by name; see
            ``ZeroTokenInferenceReservation``.
        classify: the provider's exception -> ``AttemptVerdict`` mapping.
        sdk_attempts: how many wire attempts one ``send()`` may make on its
            own. The policy divides the TOTAL budget by it and the pacer
            reserves that many slots per attempt, so the budget and the rate
            bound are both statements about the WIRE.
        cancelled: returns an exception to raise, or None to continue. Asked
            before every attempt and throughout every wait, so a shutdown ends
            a sleeping retry within ``config.PROVIDER_WAIT_POLL_SECONDS``.
        on_possibly_billed: charges an upper bound for a failed attempt that
            may have been billed; returns the dollars charged. Never allowed to
            raise out of here.
        usage_tokens_of: a success's actual token total, to settle the
            reservation; None leaves the reservation standing.
        max_attempts: the TOTAL wire-attempt budget; default
            ``config.MATCHING_CALL_MAX_ATTEMPTS``.

    Returns:
        ``send()``'s result.

    Raises:
        The last attempt's own exception -- tagged with ``ATTEMPTS_ATTR`` and
        ``CATEGORY_ATTR`` -- when it was not transient or the budget ran out;
        the exception ``cancelled()`` returned when a wait was cancelled.
    """
    pacer = PACER if pacer is None else pacer
    # ABOVE EVERYTHING ELSE IN THIS FUNCTION, so a malformed reservation costs
    # a refusal rather than a request, and so the diagnosis names the caller's
    # own arithmetic instead of arriving later as a token window that was
    # quietly exceeded by exactly the amount nobody reserved.
    #
    # A ``ValueError`` FOR AN UNRECOGNISED KIND, matching ``provider_quota``'s
    # refusal of an unrecognised scope one section up: both are a caller naming
    # something outside a closed vocabulary, which is a programming error rather
    # than a provider condition.
    #
    # MARKED PRE-SEND, AND THE CLAUSE COVERS THE REGION RATHER THAN A LIST OF
    # TYPES. Everything `_validated_attempt_budget` can raise happens before any
    # dispatch by construction -- it is called above the attempt loop -- so a
    # fourth guard added inside it is marked without its author knowing the
    # marker exists. See `PRE_SEND_ATTR`.
    try:
        budget, sdk_attempts, policy_attempts = _validated_attempt_budget(
            scope, reservation_kind, reservation_tokens, sdk_attempts,
            max_attempts)
    except BaseException as _guard_exc:
        mark_pre_send_refusal(_guard_exc)
        raise

    def _is_cancelled() -> bool:
        return cancelled is not None and cancelled() is not None

    def _cancel_now(cause: Optional[BaseException]):
        exc = cancelled() if cancelled is not None else None
        if exc is None:
            exc = WaitCancelled(scope)
        _bump(PROVIDER_RETRY_OUTCOMES,
              f"{RETRY_OUTCOME_CANCELLED}:{scope}:{type(exc).__name__}")
        _note_call(scope, waited_s=waited_total, retried=last_category is not None,
                   exhausted=False)
        if cause is not None:
            raise exc from cause
        raise exc

    waited_total = 0.0
    last_category: Optional[str] = None
    last_exc: Optional[BaseException] = None
    # FALSE UNTIL THE INSTANT BEFORE THE FIRST `send()` AND TRUE FOREVER AFTER.
    # This is the evidence `PRE_SEND_ATTR` carries, and it is a REGION rather
    # than a set of exception types precisely so a raise site added on either
    # side of the dispatch lands on the correct side without anybody deciding.
    dispatch_begun = False
    for attempt in range(1, policy_attempts + 1):
        # RE-ASKED BEFORE A RETRY ONLY. The FIRST attempt is gated by its call
        # site immediately before this function is entered (the Stage 5 shutdown
        # and spend gates); asking again here would be a second, redundant gate
        # that hides a defect in the first. A retry has no call-site gate in
        # front of it -- this IS its gate.
        if attempt > 1 and _is_cancelled():
            _cancel_now(last_exc)
        try:
            permit = pacer.reserve(scope, reservation_tokens, slots=sdk_attempts)
            waited_total += permit.waited_s
            pacer.wait(permit, _is_cancelled)
        except WaitCancelled:
            _cancel_now(last_exc)
        except BaseException as _reserve_exc:
            # `reserve` IS WHERE `QuotaUnknown` AND `ReservationExceedsQuota`
            # COME FROM, and on the first attempt nothing has been sent. The
            # guard is `dispatch_begun` rather than `attempt == 1` because they
            # are different facts: a retry whose reserve refuses follows a send
            # that DID happen, and marking it pre-send would tell the rater that
            # a request which may well have been accepted never left.
            if not dispatch_begun:
                mark_pre_send_refusal(_reserve_exc)
            raise
        if permit.waited_s > 0:
            log.debug("a provider attempt waited for its paced slot", stage=5,
                      provider=scope, phase="pacing", attempts=attempt,
                      delay_s=round(permit.waited_s, 3))
        try:
            # SET BEFORE THE CALL, NEVER AFTER IT. A `send()` that raises on its
            # way out may still have put bytes on the wire, so the conservative
            # reading -- "dispatch began" -- has to be in force before control
            # enters it. Setting it afterwards would mark a failed send's
            # exception pre-send, which is the one wrong answer that costs money.
            dispatch_begun = True
            result = send()
        except Exception as exc:                        # noqa: BLE001
            verdict = classify(exc)
            pacer.settle(permit, billing=verdict.billing)
            if (verdict.billing == BILLING_POSSIBLY_BILLED
                    and on_possibly_billed is not None):
                _bump(PROVIDER_UNCONFIRMED_BILLING, f"{scope}:{verdict.category}")
                try:
                    usd = float(on_possibly_billed(verdict) or 0.0)
                except Exception as charge_exc:           # noqa: BLE001
                    _bump(PROVIDER_RETRY_OUTCOMES,
                          f"charge_failed:{scope}:{type(charge_exc).__name__}")
                    usd = 0.0
                with _COUNTER_LOCK:
                    _UNCONFIRMED_USD[scope] = _UNCONFIRMED_USD.get(scope, 0.0) + usd
            _annotate(exc, attempt * sdk_attempts, verdict.category)
            last_category, last_exc = verdict.category, exc
            if verdict.failure_class != FAILURE_TRANSIENT:
                _bump(PROVIDER_RETRY_OUTCOMES,
                      f"{RETRY_OUTCOME_NOT_RETRIED}:{scope}:{verdict.category}")
                _note_call(scope, waited_s=waited_total, retried=attempt > 1,
                           exhausted=False)
                raise
            if attempt == policy_attempts:
                _bump(PROVIDER_RETRY_OUTCOMES,
                      f"{RETRY_OUTCOME_EXHAUSTED}:{scope}:{verdict.category}")
                _note_call(scope, waited_s=waited_total, retried=True,
                           exhausted=True)
                log.error("a provider call exhausted its attempt budget", stage=5,
                          status="error", event="provider_call_exhausted",
                          provider=scope, reason=verdict.category,
                          attempts=attempt * sdk_attempts, total=budget,
                          error_type=type(exc).__name__,
                          error_message=str(exc), degraded=True)
                raise
            delay = full_jitter_delay(attempt, rng)
            if verdict.retry_after_s is not None:
                delay = max(delay, float(verdict.retry_after_s))
            delay = min(delay, float(config.PROVIDER_QUOTA_WINDOW_SECONDS))
            _bump(PROVIDER_RETRY_OUTCOMES,
                  f"{RETRY_OUTCOME_RETRIED}:{scope}:{verdict.category}")
            log.warning("a transient provider failure; retrying after a jittered "
                        "backoff", stage=5, event="provider_call_retry",
                        provider=scope, reason=verdict.category,
                        attempts=attempt * sdk_attempts, total=budget,
                        phase="backoff", delay_s=round(delay, 3),
                        error_type=type(exc).__name__, degraded=True)
            started = _now()
            finished = cancellable_wait(delay, _is_cancelled)
            waited_total += max(0.0, _now() - started)
            if not finished:
                _cancel_now(exc)
            continue
        except BaseException:
            # NOT AN `Exception`: a Ctrl-C or a SystemExit delivered while this
            # request was on the wire. The clause above cannot see it, so
            # without this the reservation stands unsettled and the money is
            # assumed to be zero. See `_settle_abandoned`. Re-raised unchanged
            # -- a shutdown is never swallowed to tidy up accounting.
            _settle_abandoned(pacer, permit, scope, on_possibly_billed)
            raise
        if usage_tokens_of is not None:
            try:
                actual = usage_tokens_of(result)
            except Exception as usage_exc:                # noqa: BLE001
                _bump(PROVIDER_RETRY_OUTCOMES,
                      f"usage_unreadable:{scope}:{type(usage_exc).__name__}")
                actual = None
            pacer.settle(permit, billing=BILLING_POSSIBLY_BILLED,
                         actual_tokens=actual)
        if last_category is not None:
            _bump(PROVIDER_RETRY_OUTCOMES,
                  f"{RETRY_OUTCOME_RECOVERED}:{scope}:{last_category}")
        _note_call(scope, waited_s=waited_total, retried=attempt > 1,
                   exhausted=False)
        return result
    # Unreachable: every iteration returns, raises or continues, and the last
    # iteration cannot continue. Stated so a reader need not prove it.
    raise AssertionError("provider_resilience.execute fell out of its loop")


async def cancellable_wait_async(seconds: float,
                                 cancelled: Optional[Callable[[], bool]] = None
                                 ) -> bool:
    """``cancellable_wait`` for a coroutine: the sleep leaves the event loop.

    Returns False the moment ``cancelled()`` turns True, exactly as the
    synchronous twin does, and checks BEFORE the first sleep for its reason: an
    already-cancelled caller must not wait one poll interval to be told.

    THE SLEEP IS ``_SLEEP`` ON A WORKER THREAD AND NOT ``asyncio.sleep``, AND
    THAT IS TWO REQUIREMENTS AT ONCE RATHER THAN A STYLE. (1) ``_SLEEP`` is this
    module's injectable seam, so ``set_time_source`` reaches this wait and a
    virtual clock drives a coroutine's backoff in virtual time -- with
    ``asyncio.sleep`` the async path would be the one path a test could not
    drive, which is where an untested policy would hide. (2) Handing a BLOCKING
    sleep to ``asyncio.to_thread`` is what keeps the event loop free, which is
    the whole point of an async policy: a ragas run has tens of scoring
    coroutines on one loop, and a pacing wait that blocked it would stall every
    one of them, not just the one waiting for its slot.

    THE POLL INTERVAL IS THE SAME CONSTANT, so promptness is one fact about this
    module rather than two. A cancelled coroutine therefore returns within
    ``config.PROVIDER_WAIT_POLL_SECONDS`` of the flag being set, like every
    other wait here.
    """
    deadline = _now() + max(0.0, float(seconds))
    while True:
        if cancelled is not None and cancelled():
            return False
        remaining = deadline - _now()
        if remaining <= 0:
            return True
        await asyncio.to_thread(
            _SLEEP, min(remaining, config.PROVIDER_WAIT_POLL_SECONDS))


async def execute_async(send: Callable[[], object], *, scope: str,
                        reservation_tokens: int,
                        reservation_kind: str,
                        classify: Callable[[BaseException], AttemptVerdict],
                        sdk_attempts: int = 1,
                        cancelled: Optional[Callable[[], Optional[BaseException]]] = None,
                        on_possibly_billed: Optional[Callable[[AttemptVerdict], float]] = None,
                        usage_tokens_of: Optional[Callable[[object], Optional[int]]] = None,
                        label: str = "call",
                        pacer: Optional[QuotaPacer] = None,
                        rng: Optional[random.Random] = None,
                        max_attempts: Optional[int] = None):
    """``execute`` for an awaitable ``send``. ONE policy, expressed twice.

    Every argument means what it means in ``execute``; ``send`` is awaited
    rather than called, and it may be a coroutine function or return an
    awaitable. The result, the exceptions, the annotations, the counters and the
    reservation settling are identical -- this is the SAME policy on an event
    loop, not a second policy for async callers.

    WHY IT IS A TWIN RATHER THAN A WRAPPER, AND THE ALTERNATIVES WERE BOTH
    WORSE. Running ``execute`` in a thread (``asyncio.to_thread(execute, ...)``)
    would work and would put the caller's ``await``ed request INSIDE that
    thread's synchronous ``send()``, which a coroutine cannot be. Driving the
    loop from inside ``execute`` (``asyncio.run``) is worse: the ragas harness
    is already inside ``asyncio.run`` at ``score_all``, and a nested run raises.
    So the loop is expressed once more, with the two blocking waits moved off
    the event loop, and the duplication is made safe the only way duplication
    can be -- ``tests/test_provider_resilience.py``'s async section drives BOTH
    functions through the same scenarios and requires the same outcomes, so a
    policy change made in one and not the other fails there.

    WHAT IS DELIBERATELY NOT DUPLICATED: every decision. The budget arithmetic,
    the reservation guards, the category vocabulary, the backoff, the
    retry-after floor, the window cap and all five counters are the module-level
    functions both twins call. What differs is only WHERE the waiting happens.

    THE PACER'S OWN WAIT IS ALSO OFF THE LOOP. ``pacer.wait`` polls with
    ``_SLEEP``; awaiting it through ``asyncio.to_thread`` is what makes the
    scheduled start a real wait for this coroutine without stopping the others.
    ``reserve`` is NOT threaded: it takes the pacer's lock briefly and returns
    without sleeping, and moving a lock acquisition to another thread would buy
    nothing and make the schedule harder to reason about.
    """
    pacer = PACER if pacer is None else pacer
    # THE SAME GUARDS, IN THE SAME ORDER, ABOVE EVERYTHING ELSE, AND NOW THE
    # SAME CODE. See `execute` -- including the pre-send marking, which is one
    # clause here for the same reason it is one clause there.
    try:
        budget, sdk_attempts, policy_attempts = _validated_attempt_budget(
            scope, reservation_kind, reservation_tokens, sdk_attempts,
            max_attempts)
    except BaseException as _guard_exc:
        mark_pre_send_refusal(_guard_exc)
        raise

    def _is_cancelled() -> bool:
        return cancelled is not None and cancelled() is not None

    def _cancel_now(cause: Optional[BaseException]):
        exc = cancelled() if cancelled is not None else None
        if exc is None:
            exc = WaitCancelled(scope)
        _bump(PROVIDER_RETRY_OUTCOMES,
              f"{RETRY_OUTCOME_CANCELLED}:{scope}:{type(exc).__name__}")
        _note_call(scope, waited_s=waited_total,
                   retried=last_category is not None, exhausted=False)
        if cause is not None:
            raise exc from cause
        raise exc

    waited_total = 0.0
    last_category: Optional[str] = None
    last_exc: Optional[BaseException] = None
    # THE SAME REGION FLAG AS `execute`'s, for the same reason and with the same
    # meaning. Expressing the policy twice is this module's declared shape; a
    # marker that existed on only one twin would make the async path the one
    # path a caller could not classify.
    dispatch_begun = False
    for attempt in range(1, policy_attempts + 1):
        # RE-ASKED BEFORE A RETRY ONLY, for `execute`'s reason: the first
        # attempt's call site gates it, and a second gate here would hide a
        # defect in that one.
        if attempt > 1 and _is_cancelled():
            _cancel_now(last_exc)
        try:
            permit = pacer.reserve(scope, reservation_tokens, slots=sdk_attempts)
            waited_total += permit.waited_s
            await asyncio.to_thread(pacer.wait, permit, _is_cancelled)
        except WaitCancelled:
            _cancel_now(last_exc)
        except BaseException as _reserve_exc:
            if not dispatch_begun:
                mark_pre_send_refusal(_reserve_exc)
            raise
        if permit.waited_s > 0:
            log.debug("a provider attempt waited for its paced slot", stage=5,
                      provider=scope, phase="pacing", attempts=attempt,
                      delay_s=round(permit.waited_s, 3))
        try:
            dispatch_begun = True
            result = await send()
        except Exception as exc:                        # noqa: BLE001
            verdict = classify(exc)
            pacer.settle(permit, billing=verdict.billing)
            if (verdict.billing == BILLING_POSSIBLY_BILLED
                    and on_possibly_billed is not None):
                _bump(PROVIDER_UNCONFIRMED_BILLING, f"{scope}:{verdict.category}")
                try:
                    usd = float(on_possibly_billed(verdict) or 0.0)
                except Exception as charge_exc:           # noqa: BLE001
                    _bump(PROVIDER_RETRY_OUTCOMES,
                          f"charge_failed:{scope}:{type(charge_exc).__name__}")
                    usd = 0.0
                with _COUNTER_LOCK:
                    _UNCONFIRMED_USD[scope] = _UNCONFIRMED_USD.get(scope, 0.0) + usd
            _annotate(exc, attempt * sdk_attempts, verdict.category)
            last_category, last_exc = verdict.category, exc
            if verdict.failure_class != FAILURE_TRANSIENT:
                _bump(PROVIDER_RETRY_OUTCOMES,
                      f"{RETRY_OUTCOME_NOT_RETRIED}:{scope}:{verdict.category}")
                _note_call(scope, waited_s=waited_total, retried=attempt > 1,
                           exhausted=False)
                raise
            if attempt == policy_attempts:
                _bump(PROVIDER_RETRY_OUTCOMES,
                      f"{RETRY_OUTCOME_EXHAUSTED}:{scope}:{verdict.category}")
                _note_call(scope, waited_s=waited_total, retried=True,
                           exhausted=True)
                log.error("a provider call exhausted its attempt budget", stage=5,
                          status="error", event="provider_call_exhausted",
                          provider=scope, reason=verdict.category,
                          attempts=attempt * sdk_attempts, total=budget,
                          error_type=type(exc).__name__,
                          error_message=str(exc), degraded=True)
                raise
            delay = full_jitter_delay(attempt, rng)
            if verdict.retry_after_s is not None:
                delay = max(delay, float(verdict.retry_after_s))
            delay = min(delay, float(config.PROVIDER_QUOTA_WINDOW_SECONDS))
            _bump(PROVIDER_RETRY_OUTCOMES,
                  f"{RETRY_OUTCOME_RETRIED}:{scope}:{verdict.category}")
            log.warning("a transient provider failure; retrying after a jittered "
                        "backoff", stage=5, event="provider_call_retry",
                        provider=scope, reason=verdict.category,
                        attempts=attempt * sdk_attempts, total=budget,
                        phase="backoff", delay_s=round(delay, 3),
                        error_type=type(exc).__name__, degraded=True)
            started = _now()
            finished = await cancellable_wait_async(delay, _is_cancelled)
            waited_total += max(0.0, _now() - started)
            if not finished:
                _cancel_now(exc)
            continue
        except BaseException:
            # `asyncio.CancelledError` IS THE ONE THAT MATTERS HERE and it is a
            # BaseException, so the clause above never saw it: a cancelled
            # scoring task abandoned a request the provider had already been
            # asked for and recorded nothing for it. See `_settle_abandoned`.
            # Re-raised unchanged -- a cancellation must never be swallowed,
            # which is what would happen if this returned instead.
            _settle_abandoned(pacer, permit, scope, on_possibly_billed)
            raise
        if usage_tokens_of is not None:
            try:
                actual = usage_tokens_of(result)
            except Exception as usage_exc:                # noqa: BLE001
                _bump(PROVIDER_RETRY_OUTCOMES,
                      f"usage_unreadable:{scope}:{type(usage_exc).__name__}")
                actual = None
            pacer.settle(permit, billing=BILLING_POSSIBLY_BILLED,
                         actual_tokens=actual)
        if last_category is not None:
            _bump(PROVIDER_RETRY_OUTCOMES,
                  f"{RETRY_OUTCOME_RECOVERED}:{scope}:{last_category}")
        _note_call(scope, waited_s=waited_total, retried=attempt > 1,
                   exhausted=False)
        return result
    # Unreachable, for `execute`'s reason. Stated so a reader need not prove it.
    raise AssertionError("provider_resilience.execute_async fell out of its loop")


# ===========================================================================
# RESET AND REPORTING
# ===========================================================================

def reset() -> None:
    """Forget this process's pacing schedule and per-call statistics.

    Called at a run's start beside ``clear_stage5_shutdown``. The counters are
    NOT cleared here: they belong to ``oncotriage/degradation.py``'s registry
    and census, which clear them with every other counter.
    """
    PACER.reset()
    with _COUNTER_LOCK:
        _CALL_STATS.clear()
        _UNCONFIRMED_USD.clear()


def stage5_concurrency(workers: int) -> int:
    """How many Stage 5 requests ``workers`` patients can have queued at once.

    ``workers x per_trial_parallel_bound()`` in per-trial mode, ``workers`` in
    grouped mode, which sends one request at a time per patient. The bound the
    run-start announcement quotes its worst-case wait against."""
    if config.matching_call_mode() == config.MATCHING_CALL_MODE_PER_TRIAL:
        return int(workers) * int(config.per_trial_parallel_bound())
    return int(workers)


def _ttl_seconds(ttl) -> Optional[float]:
    return {"5m": 300.0, "1h": 3600.0}.get(ttl)


def describe_pacing(scope: Optional[str] = None,
                    concurrency: Optional[int] = None) -> str:
    """The run-start announcement. Always non-empty; printed beside the cap.

    UNCONDITIONAL, on ``spend.describe_cap``'s argument: an unpaced scope is
    reachable by an ordinary configuration and must SAY so, or the dangerous
    state is the quiet one.
    """
    scope = config.matching_quota_scope() if scope is None else scope
    interval, token_cap, window, starts = effective_limits(scope)
    rpm, tpm = config.provider_quota(scope)
    sdk = config.matching_sdk_attempts_per_call()
    policy = config.matching_policy_attempts()
    lines = []
    if starts is None:
        # THE TWO REASONS A SCOPE HAS NO SPACING ARE OPPOSITE FACTS AND THE
        # ANNOUNCEMENT SAYS WHICH. "Not paced" used to cover both, so a scope
        # nobody had measured read exactly like one the API does not meter --
        # and the first is a run that will be refused at its first request.
        if quota_state(scope, "requests") == QUOTA_STATE_NOT_APPLICABLE:
            lines.append(f"[Pacing] Stage 5 scope {scope!r}: requests NOT "
                         f"METERED on this axis by the provider "
                         f"(config.QUOTA_NOT_APPLICABLE, argued at the row).")
        else:
            lines.append(f"[Pacing] Stage 5 scope {scope!r}: requests/min "
                         f"UNKNOWN -- DISPATCH WILL BE REFUSED at the first "
                         f"request. Set "
                         f"config.PROVIDER_REQUESTS_PER_MINUTE[{scope!r}] to "
                         f"the value this account's console reports.")
    else:
        lines.append(f"[Pacing] Stage 5 scope {scope!r}: quota {rpm} requests/"
                     f"min x headroom {config.PROVIDER_PACING_HEADROOM:g} -> at "
                     f"most {starts} starts per {window:g}s window, spaced "
                     f"{interval:.2f}s apart (no bursts).")
    if token_cap is None:
        if quota_state(scope, "tokens") == QUOTA_STATE_NOT_APPLICABLE:
            lines.append(f"[Pacing] tokens: NOT METERED on this axis for "
                         f"{scope!r} (config.QUOTA_NOT_APPLICABLE, argued at "
                         f"the row) -- nothing to configure.")
        else:
            lines.append(f"[Pacing] tokens/min UNKNOWN for {scope!r} -- "
                         f"DISPATCH WILL BE REFUSED at the first request. Set "
                         f"config.PROVIDER_TOKENS_PER_MINUTE[{scope!r}] to the "
                         f"value this account's console reports.")
    else:
        lines.append(f"[Pacing] tokens: quota {tpm}/min x headroom -> "
                     f"{token_cap} reserved per {window:g}s; each attempt "
                     f"reserves its estimated input plus its max_tokens at "
                     f"dispatch and is adjusted to actual usage on completion.")
    lines.append("[Pacing] PROCESS-LOCAL: no state is shared with any other "
                 "process. Two processes against one account must each run at "
                 "half these limits, or run one at a time.")
    lines.append(f"[Retry] one policy for every Stage 5 call: at most "
                 f"{config.MATCHING_CALL_MAX_ATTEMPTS} wire attempts per "
                 f"logical call ({sdk} per policy attempt on this arm -> "
                 f"{policy} policy attempt(s)); transient errors only; full "
                 f"jitter from {config.MATCHING_RETRY_BASE_SECONDS:g}s, capped "
                 f"at the {window:g}s window; every attempt is paced.")
    if concurrency is not None:
        per_attempt = worst_case_pacing_wait_seconds(scope, concurrency, sdk)
        backoff = worst_case_backoff_seconds(policy)
        lines.append(f"[Retry] worst-case waiting per logical call at "
                     f"{concurrency} concurrent requester(s): "
                     f"{policy * per_attempt + backoff:.0f}s ({per_attempt:.0f}s "
                     f"pacing per attempt x {policy} + {backoff:.0f}s backoff).")
        ttl = _ttl_seconds(getattr(config, "BEDROCK_ANTHROPIC_CACHE_TTL", None))
        if (scope == config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC
                and ttl is not None and interval is not None):
            verdict = ("inside" if per_attempt < ttl else
                       "EXCEEDS -- a patient's warm prefix can expire between "
                       "its calls and every such call pays a full cache write")
            lines.append(f"[Pacing] worst-case gap between one patient's paced "
                         f"calls {per_attempt:.0f}s vs prompt-cache TTL "
                         f"{ttl:.0f}s: {verdict}.")
    return "\n".join(lines)


def report_lines() -> List[str]:
    """The run's closing pacing-and-retry block. Always non-empty."""
    lines = ["PROVIDER PACING AND RETRIES", "-" * 60]
    pacer_stats = PACER.stats()
    with _COUNTER_LOCK:
        calls = {k: dict(v) for k, v in _CALL_STATS.items()}
        usd = dict(_UNCONFIRMED_USD)
        outcomes = dict(PROVIDER_RETRY_OUTCOMES)
        unconfirmed = dict(PROVIDER_UNCONFIRMED_BILLING)
    scopes = sorted(set(pacer_stats) | set(calls))
    if not scopes:
        lines.append("  no billed provider call was made through the policy")
    for scope in scopes:
        interval, token_cap, window, starts = effective_limits(scope)
        # THE CLOSING BLOCK SAYS WHICH OF THE TWO "no spacing" STATES THIS IS,
        # for `describe_pacing`'s reason: a scope whose quota nobody has looked
        # up and a scope the provider does not meter print the same absence of
        # a number, and only the first is a run that would be refused.
        limit = ({QUOTA_STATE_NOT_APPLICABLE: "requests not metered",
                  QUOTA_STATE_UNKNOWN: "requests/min UNKNOWN (dispatch refused)"}
                 [quota_state(scope, "requests")] if starts is None else
                 f"{starts} starts/{window:g}s (one per {interval:.2f}s)")
        tokens = ({QUOTA_STATE_NOT_APPLICABLE: "tokens not metered",
                   QUOTA_STATE_UNKNOWN: "tokens/min UNKNOWN (dispatch refused)"}
                  [quota_state(scope, "tokens")] if token_cap is None else
                  f"{token_cap} tokens/{window:g}s")
        lines.append(f"  scope {scope:<20} {limit}; {tokens}")
        p = pacer_stats.get(scope, {})
        lines.append(f"    attempts paced     {int(p.get('acquired', 0))}, "
                     f"waited {int(p.get('waited', 0))} "
                     f"(total {p.get('wait_s_total', 0.0):.1f}s, max "
                     f"{p.get('wait_s_max', 0.0):.1f}s), cancelled "
                     f"{int(p.get('cancelled', 0))}, tokens under-reserved "
                     f"{int(p.get('tokens_underreserved', 0))}")
        c = calls.get(scope, {})
        lines.append(f"    logical calls      {int(c.get('calls', 0))}: retried "
                     f"{int(c.get('retried_calls', 0))}, exhausted "
                     f"{int(c.get('exhausted_calls', 0))}; max total wait per "
                     f"call {c.get('wait_s_max', 0.0):.1f}s")
        mine = {k: v for k, v in outcomes.items() if f":{scope}:" in k}
        # EVERY OUTCOME, EVEN AT ZERO, iterated off the closed vocabulary. A
        # line that printed only the non-zero keys read identically for "the
        # policy measured no retries" and "nothing was ever counted", which is
        # the confusion a run-end report exists to remove.
        totals = {o: sum(v for k, v in mine.items() if k.split(":", 1)[0] == o)
                  for o in RETRY_OUTCOMES}
        lines.append("    outcomes           " + ", ".join(
            f"{o} {totals[o]}" for o in RETRY_OUTCOMES))
        detail = {k: v for k, v in mine.items()
                  if k.split(":", 1)[0] in RETRY_OUTCOMES}
        if detail:
            lines.append("    by category        " + ", ".join(
                f"{k.replace(f':{scope}:', ':')} {v}"
                for k, v in sorted(detail.items())))
        faults = {k: v for k, v in mine.items()
                  if k.split(":", 1)[0] not in RETRY_OUTCOMES}
        if faults:
            lines.append("    accounting faults  " + ", ".join(
                f"{k.replace(f':{scope}:', ':')} {v}"
                for k, v in sorted(faults.items())))
        n_unconfirmed = sum(v for k, v in unconfirmed.items()
                            if k.startswith(f"{scope}:"))
        lines.append(f"    unconfirmed billing {n_unconfirmed} failed "
                     f"attempt(s) charged an upper bound of "
                     f"${usd.get(scope, 0.0):.4f} to the spend ledger")
    return lines


def print_report(out: Optional[Callable[[str], None]] = None) -> None:
    """Print ``report_lines`` through ``out`` (default ``console.out``)."""
    emit = console.out if out is None else out
    for line in report_lines():
        emit(line)
    emit("")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Sep 11 2026

@author: ramyalsaffar
"""
