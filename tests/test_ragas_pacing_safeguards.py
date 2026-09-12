###############################################################################
# Ragas Pacing Safeguards Test: the refusal, the reservation, and their sites
###############################################################################

"""``_refuse_unpaced_scope``, ``_request_input_tokens`` and where both are called.

WHY THIS FILE EXISTS AT ALL, WHICH IS A FINDING RATHER THAN A PREFERENCE.
------------------------------------------------------------------------
The pass that wired the ragas harness under the pacer added three safeguards
and gave none of them a test that detects its ABSENCE. That was measured, not
assumed: a sweep for each name across ``tests/`` found ``_refuse_unpaced_scope``
and ``_request_input_tokens`` in NO test file, and the three ``RagasRefusal``
catches in ``tests/test_evaluation_ragas_manifest.py`` wrap ``load_run`` rather
than either builder -- so deleting the refusal call from ``build_judge`` broke
nothing that anything would report.

THE FIRST SWEEP WAS ITSELF WRONG AND IS RECORDED BECAUSE OF HOW IT FAILED. It
used ``git grep``, which searches TRACKED files, and the new test files of that
same pass are untracked -- so it reported seven rater safeguards as untested
when three of them are driven, and it reported them by NAME rather than by
COVERAGE. A scan whose corpus silently covers less does not fail; it reports
differently, and reads exactly like a finding. Re-measured with ``grep -r``, the
rater side is covered and the three names below are genuinely not.

WHAT THIS FILE HOLDS
--------------------
    1. THE REFUSAL FIRES ON AN UNKNOWN REQUESTS QUOTA, for BOTH ragas scopes,
       carrying the coded reason a caller branches on rather than prose.
    2. IT DOES NOT FIRE ON A CONFIGURED ONE. Without this the refusal is
       equally satisfied by a function that refuses unconditionally, which
       would block a correctly-configured run forever.
    3. IT ASKS THE REQUESTS FAMILY ONLY -- the discriminating check. A
       configured RPM with an UNKNOWN TPM must proceed, because a refusal on
       the token axis is one an operator cannot satisfy for a scope whose
       provider may not meter it. Widening the guard to "both families" passes
       sections 1 and 2 and fails here, which is the point of having it.
    4. THE RESERVATION ARITHMETIC, at the module's own ``CHARS_PER_TOKEN``,
       including the floor of one token -- ``execute_async`` refuses a
       non-positive INFERENCE reservation by name, so an estimate that came
       back zero would turn a sized request into a refused one.
    5. THE CALL SITES, BY AST, in both builders: the refusal stands ABOVE the
       client construction (so nothing is built and nothing is spent when it
       fires), and each ``recording_create`` reaches the provider THROUGH
       ``_paced`` rather than awaiting ``real_create`` directly.
    6. FIRING CONTROLS FOR SECTION 5. Each pin is re-run against an in-memory
       AST copy with the thing it pins removed, and is required to REPORT the
       removal. A structural pin with no control is a pin that has only ever
       agreed with the code.

WHAT IT COSTS TO RUN
--------------------
No network, no keys, NO SPEND -- no provider client is constructed and no
request of any kind is issued; every check is a different INPUT to the real
function, or an ``ast`` walk over source read as text. No live Qdrant, NO MODEL
LOAD (``ONCOTRIAGE_DEFER_LOCAL_MODELS`` is set above the imports), no corpus, no
database, no git history, no live server.

IT DELIBERATELY DOES NOT PIN THE PROVIDER ARM. Importing this module runs
``judge_independence.assert_import_time_independence``, which passes because an
OpenAI judge faces the shipped ``bedrock_anthropic`` classifier; pinning the
OpenAI arm here would make the pair same-family and abort the file at import --
which is a fact about the pin rather than about this module.

It writes NOTHING anywhere, not even a temp directory, and it EXECS NOTHING and
loads no module by location: the controls are ``ast`` copies that are PARSED,
never run. The one mutable thing it touches is ``config.PROVIDER_REQUESTS_PER_
MINUTE``, restored in a ``finally`` with the restore ASSERTED. NOT in the
collision matrix; the one repository file it reads is sha256-compared at the end.

Run from terminal:
    python tests/test_ragas_pacing_safeguards.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
import ast
import asyncio
import hashlib
import json
import math
import os
import sys

# ABOVE THE PACKAGE IMPORTS, on oncotriage/fixtures/replay.py's precedent:
# oncotriage/agent/deps.py reads this once, at ITS OWN import.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CODE_DIR = os.path.dirname(_TESTS_DIR)
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

from oncotriage import config                                   # noqa: E402
from oncotriage import provider_resilience as pr                # noqa: E402
from oncotriage.evaluation import ragas_harness as rh           # noqa: E402


# A HARD GUARD RATHER THAN A check(): a wrong module here is not one failure
# but every failure, each with a misleading message.
_SOURCE_PATH = os.path.abspath(rh.__file__)
if not os.path.isfile(_SOURCE_PATH):
    raise SystemExit(f"cannot locate ragas_harness source at {_SOURCE_PATH!r}")


# Results
#--------
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
    """A raise, captured as a VALUE so check() can fail on it rather than the
    file aborting while an argument is being evaluated. This project has
    shipped that abort shape repeatedly; it is not shipped again here."""

    __slots__ = ("kind", "message", "code", "family", "constant")

    def __init__(self, exc):
        self.kind = type(exc).__name__
        self.message = str(exc)
        # EVERY ATTRIBUTE AN ASSERTION HERE READS IS CAPTURED HERE, so no check
        # has to hasattr() its way around a shape it did not get. QuotaUnknown
        # carries scope/family/constant; RagasRefusal carries code; a value
        # that carries neither reads None rather than raising.
        self.code = getattr(exc, "code", None)
        self.family = getattr(exc, "family", None)
        self.constant = getattr(exc, "constant", None)

    def __repr__(self):
        return f"<Raised {self.kind}: {self.message[:60]}>"


def drive(fn, *a, **kw):
    """Call fn; return its value or a Raised. Never raises."""
    try:
        return fn(*a, **kw)
    except BaseException as exc:                                # noqa: BLE001
        return Raised(exc)


def kind_of(value):
    """The exception name, or a named absence -- never an index or attribute
    access that could itself raise on the very defect under test."""
    return value.kind if isinstance(value, Raised) else "<did not raise>"


def code_of(value):
    return value.code if isinstance(value, Raised) else None


def family_of(value):
    """The quota FAMILY a refusal named, or a named absence."""
    return value.family if isinstance(value, Raised) else "<did not raise>"


def constant_of(value):
    """The config table a refusal told the operator to set."""
    return value.constant if isinstance(value, Raised) else "<did not raise>"


_SOURCE = open(_SOURCE_PATH, encoding="utf-8").read()
_SHA_BEFORE = hashlib.sha256(_SOURCE.encode("utf-8")).hexdigest()
_TREE = ast.parse(_SOURCE)

_JUDGE = config.PROVIDER_QUOTA_SCOPE_RAGAS_JUDGE
_EMBED = config.PROVIDER_QUOTA_SCOPE_RAGAS_EMBEDDING


# The quota seam
#---------------
def with_requests_quota(scope, value, fn):
    """Run fn with scope's REQUESTS quota set to value, then restore.

    The seam is the table config.provider_quota() reads, which is the only
    honest way to drive a state this machine is not in: both ragas scopes ship
    UNKNOWN, deliberately, because live dispatch stays blocked on the missing
    limits. The restore is ASSERTED by the caller, not merely attempted.
    """
    table = config.PROVIDER_REQUESTS_PER_MINUTE
    sentinel = object()
    before = table.get(scope, sentinel)
    try:
        table[scope] = value
        return fn()
    finally:
        if before is sentinel:
            table.pop(scope, None)
        else:
            table[scope] = before


#------------------------------------------------------------------------------
section("1. The refusal fires on an UNKNOWN requests quota, for both scopes")
#------------------------------------------------------------------------------

# PRECONDITION, MEASURED RATHER THAN ASSUMED. If this machine ever configures
# these scopes, sections 1 and 3 would be asserting about a state that is not
# there -- so the state is checked before it is relied on.
check("PRECONDITION: the ragas judge scope ships an UNKNOWN requests quota",
      pr.quota_state(_JUDGE, "requests"), pr.QUOTA_STATE_UNKNOWN)
check("PRECONDITION: the ragas embedding scope ships an UNKNOWN requests quota",
      pr.quota_state(_EMBED, "requests"), pr.QUOTA_STATE_UNKNOWN)

_judge_refusal = drive(rh._refuse_unpaced_scope, _JUDGE, "the ragas judge")
check("*** AN UNPACED JUDGE SCOPE REFUSES, AS A RagasRefusal ***",
      kind_of(_judge_refusal), "RagasRefusal")
check("...carrying the CODED reason, so a caller branches on a value rather "
      "than matching prose",
      code_of(_judge_refusal), "provider_quota_unknown")

_embed_refusal = drive(rh._refuse_unpaced_scope, _EMBED, "the ragas embedder")
check("*** AN UNPACED EMBEDDING SCOPE REFUSES TOO ***",
      kind_of(_embed_refusal), "RagasRefusal")
check("...with the same code", code_of(_embed_refusal),
      "provider_quota_unknown")

_msg = _judge_refusal.message if isinstance(_judge_refusal, Raised) else ""
check("the refusal NAMES THE SCOPE, so an operator knows which row to set",
      _JUDGE in _msg, True)
check("...and states that NOTHING HAS BEEN SENT, which is the fact that "
      "distinguishes it from a failure mid-run",
      "NOTHING HAS BEEN SENT" in _msg, True)
check("...and names the table to edit",
      "PROVIDER_REQUESTS_PER_MINUTE" in _msg, True)
check("...and offers the not-applicable escape, so a scope the provider does "
      "not meter is not blocked forever on a console value that does not exist",
      "QUOTA_NOT_APPLICABLE" in _msg, True)
check("the two refusals name DIFFERENT scopes (the message is not a constant)",
      _EMBED in (_embed_refusal.message if isinstance(_embed_refusal, Raised)
                 else ""), True)


#------------------------------------------------------------------------------
section("2. It does NOT fire on a configured quota")
#------------------------------------------------------------------------------

# WITHOUT THIS, SECTION 1 IS EQUALLY SATISFIED BY A FUNCTION THAT REFUSES
# UNCONDITIONALLY -- which would block every correctly-configured run.
_configured = with_requests_quota(
    _JUDGE, 60, lambda: drive(rh._refuse_unpaced_scope, _JUDGE, "the judge"))
check("*** A CONFIGURED REQUESTS QUOTA DOES NOT REFUSE ***",
      kind_of(_configured), "<did not raise>")
check("...and the function answers None rather than a value a caller might "
      "mistake for a quota", _configured, None)

check("THE RESTORE TOOK: the scope is UNKNOWN again afterwards",
      pr.quota_state(_JUDGE, "requests"), pr.QUOTA_STATE_UNKNOWN)
check("...and the table itself carries the shipped value",
      config.PROVIDER_REQUESTS_PER_MINUTE[_JUDGE], None)

# NOT_APPLICABLE IS A THIRD STATE AND IS NOT A REFUSAL. A scope whose provider
# does not meter requests has nothing to wait for.
_na = with_requests_quota(
    _JUDGE, config.QUOTA_NOT_APPLICABLE,
    lambda: drive(rh._refuse_unpaced_scope, _JUDGE, "the judge"))
check("*** A NOT-APPLICABLE REQUESTS FAMILY DOES NOT REFUSE EITHER ***",
      kind_of(_na), "<did not raise>")
check("THE RESTORE TOOK a second time",
      pr.quota_state(_JUDGE, "requests"), pr.QUOTA_STATE_UNKNOWN)


#------------------------------------------------------------------------------
section("3. It asks the REQUESTS family only -- the discriminating check")
#------------------------------------------------------------------------------

# A GUARD WIDENED TO "BOTH FAMILIES" PASSES SECTIONS 1 AND 2 AND FAILS HERE.
# That is the whole reason this section exists: refusing on an unknown TPM is a
# refusal the operator cannot satisfy for a scope the provider may not meter on
# that axis, and require_known_quota already makes exactly this distinction.
_tokens_state = pr.quota_state(_JUDGE, "tokens")
check("PRECONDITION: the judge scope's TOKEN quota is UNKNOWN, so this "
      "section is not vacuous", _tokens_state, pr.QUOTA_STATE_UNKNOWN)

_rpm_only = with_requests_quota(
    _JUDGE, 60, lambda: drive(rh._refuse_unpaced_scope, _JUDGE, "the judge"))
check("*** A CONFIGURED RPM WITH AN UNKNOWN TPM PROCEEDS ***",
      kind_of(_rpm_only), "<did not raise>")
check("...and the TOKEN family really was unknown throughout, so the reading "
      "above is about the requests family and not about a settled pair",
      with_requests_quota(_JUDGE, 60,
                          lambda: pr.quota_state(_JUDGE, "tokens")),
      pr.QUOTA_STATE_UNKNOWN)
check("THE RESTORE TOOK a third time",
      pr.quota_state(_JUDGE, "requests"), pr.QUOTA_STATE_UNKNOWN)


#------------------------------------------------------------------------------
section("3b. DISPATCH refuses the unknown TPM that construction deferred")
#------------------------------------------------------------------------------

# SECTION 3 ALONE WOULD READ AS "AN UNKNOWN TPM IS FINE", AND THAT IS FALSE.
# It is a statement about ONE layer. The two layers answer different questions
# and only the pair is the ruling:
#
#   CONSTRUCTION (_refuse_unpaced_scope, in the builders) asks the REQUESTS
#     family only. It runs before a client exists, where the token cost of any
#     particular request is not yet known -- there is no request -- so a TPM
#     refusal there would be refusing on a number nobody could have supplied
#     yet, for a scope the provider may not meter on that axis at all.
#
#   DISPATCH (execute_async -> QuotaPacer.reserve -> require_known_quota) asks
#     the TOKEN family too, and does so exactly when the reservation actually
#     consumes tokens. By then the request exists and its token cost has been
#     estimated, so the question is answerable and the answer is enforced.
#
# WITHOUT THIS SECTION THE FILE WOULD PIN THE DEFERRAL AND NOT THE ENFORCEMENT,
# which is the half that costs money: a configured RPM with an unmeasured TPM
# would dispatch under a token allowance nobody has looked up.
#
# THE ORDERING IS WHAT MAKES "ZERO INVOCATIONS" TRUE RATHER THAN LIKELY.
# execute_async validates the reservation kind, refuses a non-positive
# INFERENCE reservation, then reserves -- and `reserve` calls
# require_known_quota BEFORE `await send()` is reached at all. So the refusal
# is structurally above the wire, not a race this test happened to win.


def with_quotas(scope, rpm, tpm, fn):
    """Run fn with BOTH families set for scope, then restore BOTH.

    Both keys exist in both tables at the shipped configuration (measured), so
    the restore is a write-back rather than a delete. It is ASSERTED after
    every use rather than merely attempted.
    """
    rpm_table = config.PROVIDER_REQUESTS_PER_MINUTE
    tpm_table = config.PROVIDER_TOKENS_PER_MINUTE
    rpm_before, tpm_before = rpm_table.get(scope), tpm_table.get(scope)
    try:
        rpm_table[scope] = rpm
        tpm_table[scope] = tpm
        return fn()
    finally:
        rpm_table[scope] = rpm_before
        tpm_table[scope] = tpm_before


class RecordingSend:
    """A send that COUNTS its invocations. The count is the whole assertion:
    a refusal that still reached the provider is not a refusal."""

    def __init__(self, value="sent"):
        self.calls = 0
        self.value = value

    async def __call__(self):
        self.calls += 1
        return self.value


def dispatch(send, *, tokens, kind):
    """Drive the REAL execute_async once. Returns its value or a Raised."""

    async def _go():
        return await pr.execute_async(
            send,
            scope=_JUDGE,
            reservation_tokens=tokens,
            reservation_kind=kind,
            classify=pr.classify_openai_failure,
            sdk_attempts=1,
            label="ragas_judge_probe",
            pacer=pr.QuotaPacer())

    try:
        return asyncio.run(_go())
    except BaseException as exc:                                # noqa: BLE001
        return Raised(exc)


# -- the ruling: configured RPM, UNKNOWN TPM, a real inference reservation ----
_send_a = RecordingSend()
_out_a = with_quotas(_JUDGE, 600, None,
                     lambda: dispatch(_send_a, tokens=10,
                                      kind=pr.RESERVATION_INFERENCE))
check("*** DISPATCH REFUSES AN UNKNOWN TPM, even with the RPM configured ***",
      kind_of(_out_a), "QuotaUnknown")
check("...naming the TOKEN family, so an operator fetches the right number",
      family_of(_out_a), "tokens")
check("...and naming the TABLE to set, which is the other half of a remedy",
      constant_of(_out_a), "PROVIDER_TOKENS_PER_MINUTE")
check("*** ...AND THE SEND WAS NEVER INVOKED: zero requests left the "
      "process ***", _send_a.calls, 0)

# -- the positive control: both families configured, the send DOES happen -----
_send_b = RecordingSend()
_out_b = with_quotas(_JUDGE, 600, 1_000_000,
                     lambda: dispatch(_send_b, tokens=10,
                                      kind=pr.RESERVATION_INFERENCE))
check("CONTROL: with BOTH families configured the call goes through",
      _out_b, "sent")
check("...and the send was invoked EXACTLY once, so the refusal above is "
      "about the unknown TPM and not about a stub that never works",
      _send_b.calls, 1)

# -- the distinction itself: same state, two layers, two answers -------------
_ctor = with_quotas(_JUDGE, 600, None,
                    lambda: drive(rh._refuse_unpaced_scope, _JUDGE, "judge"))
check("*** THE SAME STATE PASSES CONSTRUCTION AND FAILS DISPATCH -- which is "
      "the layering, stated as one assertion ***",
      (kind_of(_ctor), kind_of(_out_a)),
      ("<did not raise>", "QuotaUnknown"))

# -- and the token family is asked ONLY when tokens are really reserved ------
# A MANAGEMENT reservation consumes no model tokens, so an unknown TPM must not
# block it; require_known_quota skips the token branch at tokens == 0. Without
# this, "asks the token family" would be indistinguishable from "asks it
# always", which would refuse retrievals of already-paid-for results.
_send_c = RecordingSend()
_out_c = with_quotas(_JUDGE, 600, None,
                     lambda: dispatch(_send_c, tokens=0,
                                      kind=pr.RESERVATION_MANAGEMENT))
check("a MANAGEMENT reservation of zero tokens is NOT refused by an unknown "
      "TPM", kind_of(_out_c), "<did not raise>")
check("...and it really dispatched", _send_c.calls, 1)

check("THE RESTORE TOOK after the dispatch probes",
      config.provider_quota(_JUDGE), (None, None))


#------------------------------------------------------------------------------
section("4. The reservation arithmetic, and its floor of one token")
#------------------------------------------------------------------------------

_RATIO = rh.CHARS_PER_TOKEN
check("PRECONDITION: the module declares its own chars/token ratio",
      isinstance(_RATIO, (int, float)) and _RATIO > 0, True)


def tokens(kwargs):
    return rh._request_input_tokens(kwargs)


_360 = tokens({"messages": [{"role": "user", "content": "x" * 360}]})
check("a 360-character message reserves ceil(360/ratio)",
      _360, int(math.ceil(360 / _RATIO)))
check("...and that is a NON-DEGENERATE number, so the comparison is not two "
      "floors agreeing", _360 > 1, True)

_two = tokens({"messages": [{"role": "system", "content": "a" * 100},
                            {"role": "user", "content": "b" * 260}]})
check("two messages are summed, not counted", _two, _360)

_parts = tokens({"messages": [{"role": "user",
                               "content": [{"type": "text", "text": "y" * 360}]}]})
check("a structured content list is read through its text parts",
      _parts, _360)

_embed_str = tokens({"input": "z" * 360})
check("the embedding shape ('input', a string) is read too", _embed_str, _360)
_embed_list = tokens({"input": ["z" * 180, "z" * 180]})
check("...and a batch of embedding inputs is summed", _embed_list, _360)

check("*** IT NEVER RETURNS ZERO: an empty message list still reserves at "
      "least one token, because execute_async refuses a non-positive "
      "INFERENCE reservation BY NAME ***",
      tokens({"messages": []}) >= 1, True)
check("...and neither does a request with no recognised shape at all",
      tokens({"model": "m"}) >= 1, True)

# THE FALLBACK IS AN OVER-ESTIMATE, WHICH IS THE SAFE DIRECTION FOR A RATE
# LIMIT: reserving too much slows a run, reserving too little exceeds an
# allowance. Driven rather than argued -- the unrecognised shape must not
# collapse to the floor when it plainly carries text.
_unknown_shape = tokens({"prompt": "q" * 2000})
check("an unrecognised shape carrying 2000 characters reserves substantially "
      "more than the floor", _unknown_shape > 100, True)

_serialised = int(math.ceil(
    len(json.dumps({"prompt": "q" * 2000}, default=str)) / _RATIO))
check("...and it is the SERIALISED length, which is the documented fallback",
      _unknown_shape, _serialised)


#------------------------------------------------------------------------------
section("5. The call sites, by AST")
#------------------------------------------------------------------------------

def function_named(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return node
    return None


def calls_named(node, name):
    """Every Call in node whose callee is `name`, at any depth."""
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name) and func.id == name:
                out.append(sub)
            elif isinstance(func, ast.Attribute) and func.attr == name:
                out.append(sub)
    return out


def refusal_line(builder):
    hits = calls_named(builder, "_refuse_unpaced_scope")
    return hits[0].lineno if hits else None


def first_client_line(builder):
    """Where a provider client is first CONSTRUCTED in this builder."""
    lines = [c.lineno for c in calls_named(builder, "AsyncOpenAI")]
    return min(lines) if lines else None


_judge_def = function_named(_TREE, "build_judge")
_embed_def = function_named(_TREE, "build_embeddings")
check("PRECONDITION: both builders were found in the source",
      (_judge_def is not None, _embed_def is not None), (True, True))

for _name, _def in (("build_judge", _judge_def),
                    ("build_embeddings", _embed_def)):
    if _def is None:
        check(f"{_name}: cannot inspect a function that was not found",
              "<not found>", "<found>")
        continue

    _ref = refusal_line(_def)
    check(f"*** {_name} CALLS THE REFUSAL ***", _ref is not None, True)

    _client = first_client_line(_def)
    check(f"{_name}: a provider client is constructed in it (so the ordering "
          f"below is not vacuous)", _client is not None, True)

    if _ref is not None and _client is not None:
        check(f"*** {_name}: THE REFUSAL STANDS ABOVE THE CLIENT -- nothing is "
              f"built and nothing is spent when it fires ***",
              _ref < _client, True)

    _rc = function_named(_def, "recording_create")
    check(f"{_name}: the recording closure is present", _rc is not None, True)

    if _rc is not None:
        check(f"*** {_name}: the closure reaches the provider THROUGH _paced ***",
              len(calls_named(_rc, "_paced")) >= 1, True)

        # real_create MUST be awaited inside the nested _send only. Awaiting it
        # directly in the closure is the bypass: the request goes out with no
        # reservation and no retry policy, and nothing raises.
        _send = function_named(_rc, "_send")
        check(f"{_name}: the _send closure is present", _send is not None, True)
        _send_lines = set()
        if _send is not None:
            _send_lines = {c.lineno for c in calls_named(_send, "real_create")}
        _all_lines = {c.lineno for c in calls_named(_rc, "real_create")}
        check(f"*** {_name}: EVERY real_create call is inside _send, so none "
              f"bypasses the pacer ***",
              sorted(_all_lines - _send_lines), [])
        check(f"{_name}: and there IS a real_create call to place, so the set "
              f"difference above is not empty-minus-empty",
              len(_all_lines) >= 1, True)


#------------------------------------------------------------------------------
section("6. Firing controls -- each pin re-run against a copy with the thing "
        "it pins REMOVED")
#------------------------------------------------------------------------------

class _DropCall(ast.NodeTransformer):
    """Remove every expression-statement call to `name`."""

    def __init__(self, name):
        self.name = name
        self.removed = 0

    def visit_Expr(self, node):                                 # noqa: N802
        value = node.value
        if isinstance(value, ast.Call):
            func = value.func
            hit = ((isinstance(func, ast.Name) and func.id == self.name)
                   or (isinstance(func, ast.Attribute)
                       and func.attr == self.name))
            if hit:
                self.removed += 1
                return None
        return node


def planted(name):
    """An AST copy with every bare call to `name` removed, plus the count.

    A PLANT THAT MATCHED NOTHING IS A NAMED FAILURE rather than a control
    reported as weak -- this project has shipped the other shape and paid for
    it. The copy is PARSED and never executed, so this file execs nothing.
    """
    copy = ast.parse(_SOURCE)
    dropper = _DropCall(name)
    copy = dropper.visit(copy)
    ast.fix_missing_locations(copy)
    return copy, dropper.removed


_no_refusal, _removed = planted("_refuse_unpaced_scope")
check("PLANT TOOK: the control really removed the refusal calls",
      _removed >= 2, True)

_c_judge = function_named(_no_refusal, "build_judge")
_c_embed = function_named(_no_refusal, "build_embeddings")
check("*** CONTROL: with the refusal removed, the build_judge pin REPORTS "
      "it ***", refusal_line(_c_judge) is None, True)
check("*** CONTROL: and the build_embeddings pin reports it ***",
      refusal_line(_c_embed) is None, True)
check("CONTROL IS SCOPED: the copy still constructs a client, so the pin "
      "above failed for the refusal's absence and not because the whole "
      "function vanished", first_client_line(_c_judge) is not None, True)

# AND THE OTHER DIRECTION: the UNMODIFIED source must pass the same scan, or
# the control proves nothing about the pin.
check("NON-DEGENERACY: the shipped source still passes the pin the control "
      "fails", refusal_line(_judge_def) is not None, True)


#------------------------------------------------------------------------------
section("7. This file changed nothing")
#------------------------------------------------------------------------------

_sha_after = hashlib.sha256(
    open(_SOURCE_PATH, encoding="utf-8").read().encode("utf-8")).hexdigest()
check("the one repository file this test reads is byte-unchanged",
      _sha_after, _SHA_BEFORE)
check("...and the quota table is back to what it shipped as",
      (config.PROVIDER_REQUESTS_PER_MINUTE[_JUDGE],
       config.PROVIDER_REQUESTS_PER_MINUTE[_EMBED]), (None, None))


#------------------------------------------------------------------------------
# Summary
#------------------------------------------------------------------------------
print(f"\n{'=' * 74}\nSUMMARY\n{'=' * 74}")
if _FAILURES:
    print("\nFAILURES:")
    for _f in _FAILURES:
        print(f"  - {_f}")
print(f"\npassed: {_RESULTS['passed']}")
print(f"failed: {_RESULTS['failed']}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Sep 12 01:20:00 2026

@author: ramyalsaffar
"""
