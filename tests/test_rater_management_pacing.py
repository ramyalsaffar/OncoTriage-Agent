# Rater Management Pacing Test: six Batch-API calls under the one policy
########################################################################

"""``rater._paced_management`` and the six OpenAI Batch-API calls it governs.

WHAT THIS FILE HOLDS
--------------------
    1. EVERY MANAGEMENT CALL IS PACED, AND NONE ESCAPED. Derived by AST over
       ``oncotriage/evaluation/rater.py`` rather than listed here: every
       ``client.files.*`` / ``client.batches.*`` attribute call in the module
       is either inside a ``_paced_management`` lambda or is named in a closed,
       argued exemption. A seventh call site added tomorrow fails this rather
       than silently dispatching unpaced.
    2. **A CREATE MAY NOT BE RE-ISSUED BY THE POLICY.** This is the money
       check. ``provider_resilience.execute`` retries a TRANSIENT failure up to
       ``config.MATCHING_CALL_MAX_ATTEMPTS`` (6), and ``batches.create`` is the
       one call in this project whose re-issue commits a whole second batch --
       up to ``MAX_REQUESTS_PER_BATCH`` billed requests, with a duplicate that
       is undetectable afterwards because both batches return valid ratings for
       the same custom_ids. Both arms are driven: the shipped ``max_attempts=1``
       issues exactly ONE wire attempt, and the same failure under the policy's
       own budget issues SIX. The second arm is the defect, measured, so the
       first is evidence rather than an assertion.
    3. A PRE-SEND REFUSAL AT THE CREATE SITE YIELDS ZERO CREATES **AND** ZERO
       RECONCILIATION CALLS, with the refusal preserved to the caller. An error
       AFTER dispatch stays in the uncertain path and DOES reconcile -- both
       directions, because "the refusal is re-raised" is only meaningful beside
       a case that is not.
    4. A FOCUSED ``poll_batch`` PACING TEST. ``poll_batch`` is reached only by
       ``main()``, which no test drives, so its pacing is asserted directly and
       never inferred from ``collect_results``' coverage.
    5. THE RESERVATION IS ``management`` AND RESERVES ZERO TOKENS, which is
       what lets this scope's token row stay ``QUOTA_NOT_APPLICABLE``.

WHAT IT COSTS TO RUN
--------------------
No network, no keys, NO SPEND -- every client is a stand-in and no provider
library is imported. No live Qdrant, NO MODEL LOAD
(``ONCOTRIAGE_DEFER_LOCAL_MODELS`` is set above the imports), no corpus, no
database, no git history, no live server. Backoff runs on a VIRTUAL clock
(``provider_resilience.set_time_source``), so the six-attempt arm costs
microseconds rather than the minutes its real schedule would. It writes only
inside a ``tempfile.mkdtemp`` it removes and asserts gone, and it EXECS
NOTHING: every control is a different stand-in, a different argument, or an AST
walk. NOT in the collision matrix.

Run from terminal:
    python tests/test_rater_management_pacing.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
import os
import sys

os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

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
import shutil
import tempfile
import types

from oncotriage import config, provider_resilience as P
from oncotriage.evaluation import rater as R

import _provider_pin                                             # noqa: E402

# EXPLICIT TEST QUOTA LIMITS, AND NO PROVIDER PIN. This file's subject is the
# batch harness under the SHIPPED provider; the scope it paces is UNKNOWN in
# the shipped config, which refuses before the send -- correctly, and in an
# offline test too, because the pacer cannot tell a stand-in from a provider.
# Section 3 REMOVES these limits deliberately, to drive that refusal.
_PIN_WHO = "test_rater_management_pacing.py"
_provider_pin.test_quotas_only(_PIN_WHO)


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


def section(title):
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


class Raised:
    """A raise, captured as a value a check can compare. Never re-raises."""

    __slots__ = ("kind", "message", "exc", "code", "pre_send")

    def __init__(self, exc):
        self.kind = type(exc).__name__
        self.message = str(exc)
        self.exc = exc
        self.code = getattr(exc, "code", None)
        self.pre_send = P.is_pre_send_refusal(exc)

    def __repr__(self):
        return f"<Raised {self.kind}: {self.message[:60]}>"


def drive(fn, *a, **kw):
    """Call fn; return its value or a Raised. Never raises."""
    try:
        return fn(*a, **kw)
    except BaseException as exc:                                 # noqa: BLE001
        return Raised(exc)


_TMP = tempfile.mkdtemp(prefix="ratermgmt_")
_SCOPE = config.PROVIDER_QUOTA_SCOPE_OPENAI_BATCH
_CHUNK = [{"custom_id": "c0", "params": {"model": "m", "messages": []}}]
_TAG = "primary"
_RATER_SRC = open(R.__file__, encoding="utf-8").read()
_RATER_AST = ast.parse(_RATER_SRC)


class _Throttled(Exception):
    """A TRANSIENT provider failure: `classify_openai_failure` maps 429 ->
    throttled -> retryable. Named rather than faked, so the classifier decides."""
    status_code = 429


class _Client:
    """A stand-in whose every endpoint records that it was reached."""

    def __init__(self, *, create_raises=None, retrieve_status="completed"):
        outer = self
        self.creates = 0
        self.uploads = 0
        self.listed_calls = 0
        self.content_calls = 0
        self.retrieves = 0
        self._create_raises = create_raises

        class _Files:
            def create(self, file=None, purpose=None, **_kw):
                outer.uploads += 1
                return types.SimpleNamespace(id=f"file_{outer.uploads}")

            def content(self, file_id):
                outer.content_calls += 1
                return "{}"

        class _Batches:
            def create(self, **kw):
                outer.creates += 1
                if outer._create_raises is not None:
                    raise outer._create_raises
                return types.SimpleNamespace(id=f"batch_{outer.creates}", **kw)

            def list(self, limit=None):
                outer.listed_calls += 1
                return types.SimpleNamespace(data=[])

            def retrieve(self, batch_id):
                outer.retrieves += 1
                return types.SimpleNamespace(
                    id=batch_id, status=retrieve_status,
                    output_file_id=None, error_file_id=None,
                    request_counts=types.SimpleNamespace(
                        total=1, completed=1, failed=0))

        self.files = _Files()
        self.batches = _Batches()


class _VirtualClock:
    """A clock and a sleeper that advance instantly. Backoff in virtual time."""

    def __init__(self):
        self.t = 0.0
        self.slept = 0.0

    def clock(self):
        return self.t

    def sleep(self, seconds):
        self.t += max(0.0, float(seconds))
        self.slept += max(0.0, float(seconds))


def _paced_calls():
    """Every `_paced_management(...)` call in rater.py, as (label, max_attempts).

    READ BY AST rather than by regex, so a call split across lines -- which five
    of the six are -- is one node rather than a line-matching problem.
    """
    out = []
    for node in ast.walk(_RATER_AST):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_paced_management"):
            continue
        label = None
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            label = node.args[1].value
        attempts = "<absent>"
        for kw in node.keywords:
            if kw.arg == "max_attempts":
                attempts = (kw.value.value if isinstance(kw.value, ast.Constant)
                            else "<expr>")
        out.append((label, attempts))
    return sorted(out)


# ===========================================================================
# SECTION 1 — EVERY MANAGEMENT CALL IS PACED, AND THE SET IS DERIVED
# ===========================================================================

section("1. The six sites, derived from the module rather than listed here")

_CALLS = _paced_calls()

# SIX DISTINCT API CALLS, EIGHT CALL SITES, AND THE DIFFERENCE IS NOT A
# TECHNICALITY. The ruling names six calls -- `batches.list`, `files.create`,
# `batches.create`, `batches.retrieve` (in `poll_batch`), `files.content` and
# `batches.retrieve` (in `collect_results`). Three of those NAMES appear at two
# call sites each: `files.create` and `batches.create` are issued once on the
# ordinary path and again on the post-reconciliation ABSENT path, and
# `batches.retrieve` is issued by both `poll_batch` and `collect_results`. The
# re-submission pair is the site that matters most -- it is the one deliberate
# re-send in the program -- so counting NAMES rather than SITES would let
# exactly that door go unpaced while this check still passed.
_EXPECTED_SITES = {"batches.list": 1, "files.create": 2, "batches.create": 2,
                   "batches.retrieve": 2, "files.content": 1}

_SITE_COUNTS = {}
for _label, _attempts in _CALLS:
    _SITE_COUNTS[_label] = _SITE_COUNTS.get(_label, 0) + 1

check("*** every Batch-API management call site goes through "
      "_paced_management, counted per SITE rather than per name: the "
      "re-submission pair is a second site for two of them and is the one "
      "that must not be missed ***",
      _SITE_COUNTS, _EXPECTED_SITES)
check("...which is eight sites over the six calls the ruling names",
      (len(_CALLS), sum(_EXPECTED_SITES.values())), (8, 8))
check("...and no label outside the declared set appears",
      sorted(set(_SITE_COUNTS) - set(_EXPECTED_SITES)), [])


def _unpaced_client_calls():
    """Attribute calls on a `client` that are NOT inside a `_paced_management`.

    The walk is over the WHOLE module, so a new `client.batches.foo(...)` added
    anywhere is reported. `model_is_visible`'s `models.retrieve` is the one
    argued exemption -- the free visibility check that must refuse EARLY and
    cheaply, and pacing it would put a quota refusal in front of the diagnostic
    whose purpose is to refuse before the quota matters.
    """
    paced_nodes = set()
    for node in ast.walk(_RATER_AST):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_paced_management"):
            for inner in ast.walk(node):
                paced_nodes.add(id(inner))
    found = []
    for node in ast.walk(_RATER_AST):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute):
            continue
        # client.<group>.<method>(...)
        owner = fn.value
        if not (isinstance(owner, ast.Attribute)
                and isinstance(owner.value, ast.Name)
                and owner.value.id == "client"):
            continue
        if id(node) in paced_nodes:
            continue
        found.append(f"client.{owner.attr}.{fn.attr}")
    return sorted(set(found))


check("NOTHING reaches the provider outside _paced_management except the one "
      "argued exemption: the FREE models.retrieve visibility check",
      _unpaced_client_calls(), ["client.models.retrieve"])


# ===========================================================================
# SECTION 2 — max_attempts IS REQUIRED, AND EACH SITE DECLARES THE RIGHT ONE
# ===========================================================================

section("2. A create may not be re-issued; a read may be retried")

import inspect                                                  # noqa: E402

_SIG = inspect.signature(R._paced_management)
check("max_attempts is REQUIRED, with no default, so a new call site cannot "
      "inherit an answer neither value is safe to inherit",
      (_SIG.parameters["max_attempts"].default is inspect.Parameter.empty,
       _SIG.parameters["max_attempts"].kind.name),
      (True, "KEYWORD_ONLY"))

_BY_LABEL = {}
for _label, _attempts in _CALLS:
    _BY_LABEL.setdefault(_label, set()).add(_attempts)

check("*** every CREATE site declares max_attempts=1, so the policy makes ONE "
      "wire attempt and a failure goes to verify-before-retry instead of "
      "committing a second batch ***",
      (sorted(_BY_LABEL.get("batches.create", set())),
       sorted(_BY_LABEL.get("files.create", set()))),
      ([1], [1]))
check("...and every READ site declares max_attempts=None, taking the policy's "
      "own budget: listing, polling and downloading are idempotent and a "
      "transient blip should ride out rather than end the run",
      (sorted(_BY_LABEL.get("batches.list", set()), key=str),
       sorted(_BY_LABEL.get("batches.retrieve", set()), key=str),
       sorted(_BY_LABEL.get("files.content", set()), key=str)),
      ([None], [None], [None]))


# ===========================================================================
# SECTION 3 — THE MONEY CONTROL: THE POLICY DOES NOT RE-ISSUE A CREATE
# ===========================================================================

section("3. A transient failure re-issues a READ six times and a CREATE once")

_CLOCK = _VirtualClock()
_PREV_TIME = P.set_time_source(_CLOCK.clock, _CLOCK.sleep)
try:
    P.PACER.reset()

    _attempts_create = {"n": 0}

    def _failing_create():
        _attempts_create["n"] += 1
        raise _Throttled("Too many requests")

    _create_out = drive(R._paced_management, _failing_create,
                        "batches.create", max_attempts=1)
    check("*** THE SHIPPED ARM: a throttled `batches.create` is issued EXACTLY "
          "ONCE. A second issue commits up to MAX_REQUESTS_PER_BATCH billed "
          "requests again, and the duplicate is undetectable afterwards ***",
          _attempts_create["n"], 1)
    check("...and the failure still reaches the caller, so the "
          "verify-before-retry path owns it",
          isinstance(_create_out, Raised) and _create_out.kind, "_Throttled")

    # THE CONTROL: the SAME failure under the policy's own budget. This is the
    # defect `max_attempts=1` prevents, measured rather than argued -- without
    # it the check above would be a number with nothing to compare against.
    P.PACER.reset()
    _attempts_read = {"n": 0}

    def _failing_read():
        _attempts_read["n"] += 1
        raise _Throttled("Too many requests")

    _read_out = drive(R._paced_management, _failing_read,
                      "batches.retrieve", max_attempts=None)
    check("*** THE CONTROL: the identical transient failure under the POLICY'S "
          "budget is issued MATCHING_CALL_MAX_ATTEMPTS times -- so the check "
          "above measures the cap rather than a stand-in that only ever fails "
          "once ***",
          _attempts_read["n"], config.MATCHING_CALL_MAX_ATTEMPTS)
    check("...and the two arms genuinely differ, which is what makes the "
          "create arm evidence",
          _attempts_create["n"] < _attempts_read["n"], True)
    check("...and the retry really did back off rather than spinning: virtual "
          "time advanced", _CLOCK.slept > 0, True)
finally:
    P.set_time_source(*_PREV_TIME)
check("the module's time source is restored, so no later file inherits a "
      "virtual clock", (P._CLOCK, P._SLEEP), _PREV_TIME)


# ===========================================================================
# SECTION 4 — A PRE-SEND REFUSAL: ZERO CREATES, ZERO RECONCILIATION
# ===========================================================================

section("4. A pre-send refusal reconciles NOTHING and is preserved")

# THE LIMITS ARE REMOVED FOR THIS SECTION ONLY, which is what makes the scope
# UNKNOWN and the pacer refuse -- the shipped production state.
_provider_pin.release_test_quotas(out=lambda *_a, **_k: None)

# ── RECORDING ENTRY INTO THE RECONCILIATION ITSELF ─────────────────────────
#
# **"ZERO RECONCILIATION CALLS" IS DIRECTLY TESTABLE AND AN EARLIER VERSION OF
# THIS FILE SAID IT WAS NOT.** The claim was that only the refusal's NAME could
# discriminate, because `listed_calls` is zero whether or not
# `reconcile_uncertain_submission` is entered -- its own `batches.list` is paced
# and refuses before reaching the stand-in. That is true of `listed_calls` and
# false of the question: `submit_batches` reaches the reconciliation through a
# MODULE-GLOBAL lookup, so wrapping `R.reconcile_uncertain_submission` counts
# ENTRIES directly. Both discriminations are kept -- the count says whether the
# function was entered, the name says which call the refusal came from, and a
# defect that defeated one would still have to defeat the other.
_reconcile_entries = {"n": 0}
_real_reconcile = R.reconcile_uncertain_submission


def _counting_reconcile(*a, **kw):
    _reconcile_entries["n"] += 1
    return _real_reconcile(*a, **kw)


R.reconcile_uncertain_submission = _counting_reconcile
try:
    P.PACER.reset()
    _refused_client = _Client()
    _refused_state = {}
    _refused = drive(R.submit_batches, _refused_client, [_CHUNK],
                     _refused_state, os.path.join(_TMP, "refused.json"), _TAG)
    _entries_on_refusal = _reconcile_entries["n"]
    _reconcile_entries["n"] = 0

    check("*** ZERO creates and ZERO uploads: the pacer refused ABOVE the send, "
          "so no request left this process ***",
          (_refused_client.creates, _refused_client.uploads), (0, 0))
    check("ZERO listing and ZERO downloads reached the stand-in",
          (_refused_client.listed_calls, _refused_client.content_calls), (0, 0))
    # *** THE DISCRIMINATING CHECK, AND THE COUNT ABOVE IS NOT IT. ***
    #
    # `listed_calls == 0` is satisfied BOTH ways and therefore measures
    # nothing about the re-raise: with it, `reconcile_uncertain_submission` is
    # never entered; without it, it IS entered and then refuses at its OWN
    # paced `batches.list` -- which never reaches the stand-in either, so the
    # count is zero in both worlds.
    #
    # MEASURED, NOT REASONED: the copytree revert matrix planted the removal of
    # the re-raise and this section still PASSED. That is the check being weak,
    # found by running rather than by reading.
    #
    # WHAT DISCRIMINATES IS WHICH CALL THE REFUSAL NAMES. `_paced_management`
    # puts the label in the message, so a refusal that travelled from the
    # CREATE says `files.create` and one produced by the reconciliation says
    # `batches.list`. The first is "we never got past the create"; the second
    # is "we went on to ask the provider about a submission that was never
    # made", which is precisely what the re-raise exists to prevent.
    check("*** ZERO RECONCILIATION, MEASURED AT THE FUNCTION ITSELF: "
          "`reconcile_uncertain_submission` was entered ZERO times. A refusal "
          "that fell through would ask the provider about a submission that "
          "was never made, and would report a configuration defect as "
          "`submission_uncertain`, whose remedy is not the remedy ***",
          _entries_on_refusal, 0)
    check("...and the refusal that reaches the caller names the CREATE call, "
          "not the listing -- the second, independent discrimination: the "
          "count says whether the function was ENTERED, the name says which "
          "call the refusal came FROM, and a defect defeating one would still "
          "have to defeat the other",
          (isinstance(_refused, Raised)
           and "files.create" in _refused.message,
           isinstance(_refused, Raised)
           and "batches.list" in _refused.message),
          (True, False))
    check("...the refusal is PRESERVED to the caller rather than swallowed",
          isinstance(_refused, Raised), True)
    check("...as a RaterRefusal, which is what makes main() exit 1 (a "
          "CONFIGURATION refusal) rather than dying on an unhandled "
          "RuntimeError with no diagnosis",
          isinstance(_refused, Raised) and _refused.kind, "RaterRefusal")
    check("...carrying the pacing code, so the refusal names its own cause",
          isinstance(_refused, Raised) and _refused.code, "pacing_refused")
    check("*** ...and the PRE-SEND MARKER is carried onto the converted "
          "refusal. Dropping it in the conversion would make every quota "
          "refusal look like a lost response ***",
          isinstance(_refused, Raised) and _refused.pre_send, True)
    check("...and the message names the constant an operator sets",
          isinstance(_refused, Raised)
          and "PROVIDER_REQUESTS_PER_MINUTE" in _refused.message, True)
    check("...and states that nothing was sent",
          isinstance(_refused, Raised)
          and "NOTHING WAS SENT" in _refused.message.upper(), True)
    check("...and no batch was recorded in the state file",
          _refused_state.get("batches", []), [])

    # THE OTHER DIRECTION, without which "the refusal is re-raised" is a claim
    # about one case. A failure AFTER dispatch is NOT pre-send and MUST still
    # reach the reconciliation.
    _uncertain = drive(R.reconcile_uncertain_submission, _Client(),
                       R._batch_identity(_CHUNK, _TAG, 0))
    check("...while `reconcile_uncertain_submission` itself now REFUSES rather "
          "than answering `unknown`: a listing that never left the process is "
          "not the provider failing to answer",
          isinstance(_uncertain, Raised) and _uncertain.code, "pacing_refused")
finally:
    R.reconcile_uncertain_submission = _real_reconcile
    _provider_pin.test_quotas_only(_PIN_WHO, out=lambda *_a, **_k: None)

check("the reconciliation was restored BY IDENTITY, so no later section "
      "measures a counting stand-in",
      R.reconcile_uncertain_submission is _real_reconcile, True)

# ── THE OTHER DIRECTION FOR THE COUNT, WITHOUT WHICH ZERO PROVES NOTHING ────
#
# A count of zero is equally consistent with "the re-raise worked" and "this
# drive never had anything to reconcile". So the SAME counter is installed over
# a failure that happens AFTER dispatch -- a create that raised having possibly
# been accepted, which is the one state the reconciliation exists for -- and it
# MUST be entered. With limits installed the pacer admits the call, so the
# failure reaching the handler is the provider's rather than the quota's.
_reconcile_entries["n"] = 0
R.reconcile_uncertain_submission = _counting_reconcile
try:
    P.PACER.reset()
    _post_client = _Client(create_raises=RuntimeError("connection reset "
                                                      "after accept"))
    _post = drive(R.submit_batches, _post_client, [_CHUNK], {},
                  os.path.join(_TMP, "post.json"), _TAG)
    _entries_after_dispatch = _reconcile_entries["n"]
finally:
    R.reconcile_uncertain_submission = _real_reconcile

check("*** CONTROL FOR THE COUNT: a failure AFTER dispatch DOES enter "
      "`reconcile_uncertain_submission` -- so the zero above is a measurement "
      "of the re-raise rather than of a drive with nothing to reconcile ***",
      _entries_after_dispatch >= 1, True)
check("...and the two arms genuinely differ, which is what makes the zero "
      "evidence", (_entries_on_refusal, _entries_after_dispatch >= 1),
      (0, True))

# WITH LIMITS BACK, the same drive completes: the refusal above was the quota
# and nothing else. Without this the section would be consistent with a
# submit_batches that was simply broken.
P.PACER.reset()
_ok_client = _Client()
_ok_state = {}
_ok = drive(R.submit_batches, _ok_client, [_CHUNK], _ok_state,
            os.path.join(_TMP, "ok.json"), _TAG)
check("CLEAN CONTROL: with explicit limits the identical drive submits once "
      "and reconciles nothing, so section 4's refusals were the quota",
      (_ok_client.creates, _ok_client.uploads, _ok_client.listed_calls,
       isinstance(_ok, Raised)),
      (1, 1, 0, False))


# ===========================================================================
# SECTION 5 — A FOCUSED poll_batch PACING TEST
# ===========================================================================

section("5. poll_batch is paced, asserted directly and not inferred")

# `poll_batch` IS REACHED ONLY BY `main()`, WHICH NO TEST DRIVES. Inferring its
# pacing from `collect_results`' coverage would be inferring it from a
# different function; this drives it.
P.PACER.reset()
_poll_client = _Client(retrieve_status="completed")
_before = sum(v for k, v in P.PROVIDER_PACING_WAITS.items()
              if k.startswith(f"{_SCOPE}:acquired"))
_polled = drive(R.poll_batch, _poll_client, "batch_x", 0, 60)
_after = sum(v for k, v in P.PROVIDER_PACING_WAITS.items()
             if k.startswith(f"{_SCOPE}:acquired"))
check("poll_batch returns the terminal batch",
      isinstance(_polled, Raised) is False
      and getattr(_polled, "status", None), "completed")
check("...and its `batches.retrieve` went through the pacer: the scope's "
      "acquired count moved by exactly the one poll it made",
      (_after - _before, _poll_client.retrieves), (1, 1))

# AND THE REFUSAL REACHES IT TOO -- a poll under an unknown quota must refuse
# rather than dispatching, which is the whole point of pacing a loop.
_provider_pin.release_test_quotas(out=lambda *_a, **_k: None)
try:
    P.PACER.reset()
    _poll_refused_client = _Client()
    _poll_refused = drive(R.poll_batch, _poll_refused_client, "batch_x", 0, 60)
    check("*** a poll under an UNKNOWN quota refuses and issues ZERO retrieves, "
          "so a poll loop cannot be the unpaced door ***",
          (_poll_refused_client.retrieves,
           isinstance(_poll_refused, Raised) and _poll_refused.code),
          (0, "pacing_refused"))
finally:
    _provider_pin.test_quotas_only(_PIN_WHO, out=lambda *_a, **_k: None)


# ===========================================================================
# SECTION 6 — THE RESERVATION IS management AND RESERVES NO TOKENS
# ===========================================================================

section("6. management, zero tokens, one SDK attempt")

_seen = {}
_real_execute = P.execute


def _recording_execute(send, **kw):
    _seen.update(kw)
    return _real_execute(send, **kw)


P.execute = _recording_execute
try:
    P.PACER.reset()
    drive(R._paced_management, lambda: "ok", "files.content", max_attempts=None)
finally:
    P.execute = _real_execute
check("the module-level execute is restored by identity", P.execute is _real_execute, True)

check("the reservation is MANAGEMENT and reserves ZERO tokens, which is what "
      "lets this scope's token row stay QUOTA_NOT_APPLICABLE",
      (_seen.get("reservation_kind"), _seen.get("reservation_tokens")),
      (P.RESERVATION_MANAGEMENT, 0))
check("...at the batch-management scope, not the Stage 5 one",
      _seen.get("scope"), _SCOPE)
check("...with sdk_attempts=1, agreeing with require_client's max_retries=0: "
      "declaring more would under-use the rate and fewer would let the wire "
      "exceed it", _seen.get("sdk_attempts"), 1)
check("...and NO on_possibly_billed / usage_tokens_of, because a management "
      "call is not model-billed and reports no usage -- passing either would "
      "put a fabricated number into the spend ledger",
      (_seen.get("on_possibly_billed"), _seen.get("usage_tokens_of")),
      (None, None))
check("...and the scope's token quota really is NOT_APPLICABLE rather than "
      "UNKNOWN, so the zero-token reservation is a declared fact",
      config.provider_quota(_SCOPE)[1], config.QUOTA_NOT_APPLICABLE)


# ===========================================================================
# SECTION 7 — WHAT THIS FILE LEAVES BEHIND
# ===========================================================================

section("7. What this file leaves behind")

P.PACER.reset()
shutil.rmtree(_TMP, ignore_errors=True)
check("the temp tree is removed", os.path.exists(_TMP), False)
check("rater.py was not written by this file",
      open(R.__file__, encoding="utf-8").read() == _RATER_SRC, True)

# RELEASED ABOVE THE SUMMARY, NEVER BELOW IT.
_QUOTA_WHO, _QUOTA_RESTORED = _provider_pin.release_test_quotas()
check("[provider pin] the test quota limits this file installed were released, "
      "and both tables are back to the shipped values",
      (_QUOTA_WHO, _QUOTA_RESTORED), (_PIN_WHO, True))


# ---------------------------------------------------------------------------
print("\n" + "=" * 74)
print(f"RESULTS: {_RESULTS['passed']} passed, {_RESULTS['failed']} failed")
print("=" * 74)
if _FAILURES:
    print("\nFailures:")
    for _f in _FAILURES:
        print(f"  - {_f}")
if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Sep 12 2026

@author: ramyalsaffar
"""
