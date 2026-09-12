# Provider Quota Lookup Test: five states, each driven, none of them guessed
############################################################################

"""``oncotriage/provider_quotas.py``: the read-only Service Quotas lookup.

WHAT THIS FILE HOLDS
--------------------
    1. The closed state vocabulary, and that every member is REACHABLE -- a
       state nothing can produce is a state a reader cannot act on.
    2. Each of the five states driven through the real function against a
       ``session_factory`` stand-in: ok, no signing credentials, a credential
       chain that raises, a code pair that is not recorded, and a call that
       failed. Plus the one that is easy to get wrong -- a response that
       ARRIVED carrying no number, which must NOT be ``ok``.
    3. THE PROPERTY THE WHOLE MODULE EXISTS FOR: it never substitutes a
       default and never writes configuration. Driven by comparing both quota
       tables before and after, and by requiring ``applied`` to be ``None``
       on every non-``ok`` state.
    4. ``agrees()`` answers ``None`` rather than ``False`` when the read did
       not happen, which is what stops a lookup that never ran from reading as
       "the provider disagrees with you".
    5. What this machine actually answers, asserted rather than described.

NO NETWORK, NO KEYS, NO SPEND, NO AWS CALL OF ANY KIND. Every session is a
stand-in; the one place the REAL credential chain is consulted is section 5,
which asks ``boto3.Session().get_credentials()`` through the shipped function
and is the measurement that this machine has no signing credentials -- it opens
no socket, because resolving a chain that finds nothing makes no request. It
writes NOTHING anywhere, not even a temp directory, and it EXECS NOTHING: every
control is a different INPUT to the real function, which is the natural control
for a function of its argument. NOT in the collision matrix.
"""

import ast
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

# ── THE ONE OUTBOUND ATTEMPT THIS FILE USED TO MAKE, REMOVED ────────────────
#
# **SECTION 5 DRIVES THE REAL CREDENTIAL CHAIN, AND THAT CHAIN PROBES THE EC2
# INSTANCE METADATA SERVICE.** MEASURED under a network tripwire over a full
# bucket-A run: one `getaddrinfo -> ('169.254.169.254', 80)` from this file,
# through botocore's own credential resolution. On a host with no IMDS it is a
# hanging connect that eventually times out; on an EC2 runner it RESOLVES a
# role. Either way it is a third party this file's subject has nothing to do
# with, and the comment at section 5 asserted the opposite.
#
# `AWS_EC2_METADATA_DISABLED` IS BOTOCORE'S OWN DOCUMENTED OPT-OUT, and it is
# the right instrument BECAUSE IT PRESERVES WHAT THE SECTION MEASURES: the
# chain still runs, through the shipped function, and still answers `None`
# here -- a stand-in session would have replaced the very thing section 5
# exists to exercise. MEASURED both ways on this machine: with the variable,
# `get_credentials()` is None and the tripwire records ZERO attempts; without
# it, it is None and the tripwire records ONE.
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

from oncotriage import config                                    # noqa: E402
from oncotriage import provider_quotas as pq                      # noqa: E402


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


class _Stand:
    """A boto3 session stand-in. Every branch is a different construction.

    ``credentials`` is the object ``get_credentials()`` returns -- ``None`` is
    the real "nothing resolved" answer. ``raise_on`` makes one named step
    raise, which is how the two failure states that are not absences are
    driven.
    """

    def __init__(self, *, credentials=object(), quota_value=9.0,
                 raise_on=None, region="us-east-1"):
        self._credentials = credentials
        self._quota_value = quota_value
        self._raise_on = raise_on
        self.region_name = region
        self.asked = []

    def get_credentials(self):
        if self._raise_on == "credentials":
            raise RuntimeError("the chain exploded")
        return self._credentials

    def client(self, name):
        if self._raise_on == "client":
            raise RuntimeError(f"no such client {name!r}")
        return self

    def get_service_quota(self, ServiceCode=None, QuotaCode=None):  # noqa: N803
        self.asked.append((ServiceCode, QuotaCode))
        if self._raise_on == "call":
            raise RuntimeError("AccessDeniedException")
        if self._quota_value is _NO_VALUE:
            return {"Quota": {"Unit": "None"}}
        return {"Quota": {"Value": self._quota_value}}


_NO_VALUE = object()
_SCOPE = config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC
_CODES = {_SCOPE: ("bedrock", "L-FAKECODE")}


def _codes(**over):
    """The lookup-code table with `_SCOPE` addressable, plus any override."""
    table = dict.fromkeys(config.PROVIDER_QUOTA_SCOPES, None)
    table.update(_CODES)
    table.update(over)
    return table


def _lookup(session, codes=None, scopes=(_SCOPE,)):
    """Run the REAL lookup against `session`, with `codes` in force."""
    saved = config.PROVIDER_QUOTA_LOOKUP_CODES
    config.PROVIDER_QUOTA_LOOKUP_CODES = (_codes() if codes is None else codes)
    try:
        return pq.lookup_applied_quotas(session_factory=lambda: session,
                                        scopes=scopes)[_SCOPE]
    finally:
        config.PROVIDER_QUOTA_LOOKUP_CODES = saved


# ===========================================================================
# SECTION 1 — THE VOCABULARY IS CLOSED AND EVERY MEMBER IS REACHABLE
# ===========================================================================

section("1. The state vocabulary")

check("the five states are exactly what the module declares",
      pq.LOOKUP_STATES,
      (pq.LOOKUP_OK, pq.LOOKUP_NO_SDK, pq.LOOKUP_CREDENTIALS_ABSENT,
       pq.LOOKUP_CODE_NOT_RECORDED, pq.LOOKUP_CALL_FAILED))
check("...and they are distinct, which the module refuses at import if not",
      len(set(pq.LOOKUP_STATES)), len(pq.LOOKUP_STATES))

# EVERY MEMBER IS PRODUCED BY THE FUNCTION'S OWN BODY, by AST, SCOPED TO THAT
# FUNCTION. A state nobody can produce is a state a reader cannot act on --
# and `no_sdk` is the one this file cannot DRIVE, because boto3 is installed
# here, so it is pinned structurally instead of being left unmentioned.
#
# THE SCOPE IS THE POINT AND THE FIRST VERSION DID NOT HAVE IT. Walking the
# WHOLE module for `ast.Name` is satisfied by the `LOOKUP_STATES` tuple alone
# -- every member appears there by construction -- so the check would have
# passed over a state the function can never return, which is exactly what it
# claims to rule out.
_PQ_TREE = ast.parse(open(pq.__file__, encoding="utf-8").read())
_LOOKUP_FN = next(n for n in ast.walk(_PQ_TREE)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "lookup_applied_quotas")
_PRODUCED = {n.id for n in ast.walk(_LOOKUP_FN) if isinstance(n, ast.Name)}
check("every state is PRODUCED inside lookup_applied_quotas itself, so none "
      "is declared and unreachable (no_sdk is the one this file cannot drive "
      "-- boto3 is installed here -- so it is pinned this way)",
      sorted(s for s in ("LOOKUP_OK", "LOOKUP_NO_SDK",
                         "LOOKUP_CREDENTIALS_ABSENT",
                         "LOOKUP_CODE_NOT_RECORDED", "LOOKUP_CALL_FAILED")
             if s not in _PRODUCED), [])
check("NON-DEGENERACY: the scoped walk really is narrower than the module -- "
      "it does NOT see a name only the module body binds, which is what the "
      "unscoped version was passing on",
      "LOOKUP_STATES" in _PRODUCED, False)


# ===========================================================================
# SECTION 2 — EACH STATE, DRIVEN
# ===========================================================================

section("2. Each state driven through the real function")

_ok = _lookup(_Stand(quota_value=9.0))
check("a successful read reports `ok` and carries the provider's number",
      (_ok.state, _ok.applied), (pq.LOOKUP_OK, 9.0))
check("...and it asked for the recorded (ServiceCode, QuotaCode) pair, not a "
      "pair it invented", (_ok.service_code, _ok.quota_code),
      ("bedrock", "L-FAKECODE"))

_absent = _lookup(_Stand(credentials=None))
check("no SIGNING credentials is its own state, not a call failure",
      _absent.state, pq.LOOKUP_CREDENTIALS_ABSENT)
check("...and the message separates a Bedrock BEARER token from signing "
      "credentials, because an operator who has set the bearer token has 'a "
      "Bedrock credential' and still cannot make this call",
      ("bedrock-runtime DATA PLANE only" in _absent.detail
       and "AWS_BEARER_TOKEN_BEDROCK" in _absent.detail), True)

_raised = _lookup(_Stand(raise_on="credentials"))
check("a credential chain that RAISES is credentials_absent too -- the two "
      "have one remedy", _raised.state, pq.LOOKUP_CREDENTIALS_ABSENT)

_norec = _lookup(_Stand(), codes=dict.fromkeys(config.PROVIDER_QUOTA_SCOPES,
                                               None))
check("a scope with no recorded code pair reports `code_not_recorded` rather "
      "than guessing one", _norec.state, pq.LOOKUP_CODE_NOT_RECORDED)
check("...and says where the pair comes from, so the remedy is an operator "
      "action rather than a mystery",
      "PROVIDER_QUOTA_LOOKUP_CODES" in _norec.detail, True)

_failed = _lookup(_Stand(raise_on="call"))
check("a call that was made and refused reports `call_failed` with the "
      "provider's own words", (_failed.state,
                               "AccessDeniedException" in _failed.detail),
      (pq.LOOKUP_CALL_FAILED, True))

_noval = _lookup(_Stand(quota_value=_NO_VALUE))
check("*** a response that ARRIVED carrying no numeric Value is call_failed, "
      "NOT ok -- `ok` promises `applied` is a number and a consumer branching "
      "on the state must be able to rely on it ***",
      (_noval.state, _noval.applied), (pq.LOOKUP_CALL_FAILED, None))
check("CONTROL: the same stand-in WITH a number is ok, so the check above is "
      "about the missing Value and not about the stand-in",
      _lookup(_Stand(quota_value=3.0)).state, pq.LOOKUP_OK)

_booly = _lookup(_Stand(quota_value=True))
check("`True` is not a quota: a bool is refused where a number is wanted, on "
      "config.validate_provider_resilience_config()'s own rule",
      _booly.state, pq.LOOKUP_CALL_FAILED)


# ===========================================================================
# SECTION 3 — IT NEVER SUBSTITUTES A DEFAULT AND NEVER WRITES CONFIGURATION
# ===========================================================================

section("3. Read only: no default, no write")

check("every non-ok state carries applied=None, so no caller can read a "
      "number out of a read that did not happen",
      sorted({r.applied for r in (_absent, _raised, _norec, _failed, _noval)}),
      [None])

_RPM_BEFORE = dict(config.PROVIDER_REQUESTS_PER_MINUTE)
_TPM_BEFORE = dict(config.PROVIDER_TOKENS_PER_MINUTE)
_CODES_BEFORE = dict(config.PROVIDER_QUOTA_LOOKUP_CODES)
for _sess in (_Stand(quota_value=9.0), _Stand(credentials=None),
              _Stand(raise_on="call"), _Stand(quota_value=1.0)):
    _lookup(_sess)
check("*** neither quota table moved across every state above: the lookup "
      "REPORTS and a disagreeing figure is an edit an operator makes ***",
      (config.PROVIDER_REQUESTS_PER_MINUTE == _RPM_BEFORE,
       config.PROVIDER_TOKENS_PER_MINUTE == _TPM_BEFORE,
       config.PROVIDER_QUOTA_LOOKUP_CODES == _CODES_BEFORE),
      (True, True, True))
check("NON-DEGENERACY: the configured figure was a real number throughout, so "
      "'unchanged' is not 'both were empty'",
      isinstance(_RPM_BEFORE[_SCOPE], int) and _RPM_BEFORE[_SCOPE] > 0, True)
check("...and the module writes nothing at all: no assignment to a config "
      "attribute anywhere in it, by AST",
      [ast.unparse(t) for n in ast.walk(_PQ_TREE)
       if isinstance(n, ast.Assign) for t in n.targets
       if isinstance(t, ast.Attribute)
       and ast.unparse(t).startswith(("config.", "settings."))], [])


# ===========================================================================
# SECTION 4 — agrees() IS THREE-VALUED
# ===========================================================================

section("4. A read that did not happen does not 'disagree'")

check("*** a lookup that did not happen answers None rather than False: "
      "`applied == configured` would be False for every failure and read as "
      "'the provider disagrees with you' ***",
      sorted({repr(r.agrees())
              for r in (_absent, _raised, _norec, _failed, _noval)}),
      ["None"])
check("a successful read that MATCHES the configured figure agrees",
      _lookup(_Stand(quota_value=float(
          config.PROVIDER_REQUESTS_PER_MINUTE[_SCOPE]))).agrees(), True)
check("...and one that does not, DISAGREES -- which is the whole point of "
      "reading it back",
      _lookup(_Stand(quota_value=float(
          config.PROVIDER_REQUESTS_PER_MINUTE[_SCOPE]) + 1)).agrees(), False)


# ===========================================================================
# SECTION 5 — WHAT THIS MACHINE ANSWERS, AND THE PRINTED BLOCK
# ===========================================================================

section("5. The shipped answer on this machine")

# THE REAL CREDENTIAL CHAIN, THROUGH THE SHIPPED FUNCTION. MEASURED
# 2026-09-11: boto3.Session().get_credentials() is None here.
#
# **THIS BLOCK SAID "It opens no socket: a chain that resolves nothing makes no
# request" AND THAT WAS FALSE.** Measured under a network tripwire: the chain
# probes the EC2 instance metadata service at 169.254.169.254 before concluding
# it has nothing, so this line DID make a request -- one per run, to a third
# party this file is not about. The claim is corrected rather than deleted
# because it is exactly the kind of comfortable sentence that stops anyone
# looking.
#
# IT IS TRUE AGAIN NOW, and by construction rather than by hope:
# `AWS_EC2_METADATA_DISABLED` is set at the top of this file, so the chain runs
# in full and skips the probe. The credential answer is unchanged.
_REAL = pq.lookup_applied_quotas()
check("every scope is present in the answer, so a scope added without a code "
      "pair appears as a state rather than being silently absent",
      sorted(_REAL), sorted(config.PROVIDER_QUOTA_SCOPES))
check("...and every state is a member of the closed vocabulary",
      sorted({r.state for r in _REAL.values()} - set(pq.LOOKUP_STATES)), [])
check("on THIS machine the shipped arm lands on a non-ok state and carries no "
      "number, which is what config.PROVIDER_REQUESTS_PER_MINUTE's docstring "
      "promises a reader",
      (_REAL[_SCOPE].state != pq.LOOKUP_OK, _REAL[_SCOPE].applied),
      (True, None))
# WHICH non-ok STATE IS A PROPERTY OF THE MACHINE, NOT OF THE CODE, AND PINNING
# ONE WOULD BE A LANDMINE. This developer tree has no signing credentials, so
# it answers `credentials_absent`; a machine with `~/.aws` or an EC2 role
# resolves a credential and then answers `code_not_recorded`, because no
# (ServiceCode, QuotaCode) pair is recorded for any scope. Both are "the read
# did not happen, for a named reason", which is the property worth asserting --
# a check pinning the first would fail on a colleague's laptop for something
# that is not a defect. The observed value is PRINTED so the record says which
# one this run met.
_EXPECTED_NON_OK = (pq.LOOKUP_CREDENTIALS_ABSENT, pq.LOOKUP_CODE_NOT_RECORDED)
print(f"        [measured here] {_SCOPE} -> {_REAL[_SCOPE].state}")
check("...and it is one of the two states that mean 'the read did not happen "
      "for a named reason': credentials_absent on a tree with no signing "
      "credentials (a Bedrock bearer token cannot sign this call, which is the "
      "reason that docstring gives), or code_not_recorded on one that has them",
      _REAL[_SCOPE].state in _EXPECTED_NON_OK, True)

_LINES = pq.report_lines(_REAL)
check("the printed block is never empty and names the read-only rule",
      (len(_LINES) > 3,
       any("never writes configuration" in l for l in _LINES)), (True, True))
check("...and says the comparison did NOT happen rather than printing a bare "
      "number that would read as a confirmation",
      any("not compared" in l for l in _LINES), True)
check("CONTROL: with a successful read the same block says AGREES, so the "
      "line above is about the state and not a constant string",
      any("AGREES" in l for l in pq.report_lines(
          {_SCOPE: _lookup(_Stand(quota_value=float(
              config.PROVIDER_REQUESTS_PER_MINUTE[_SCOPE])))})), True)

# ===========================================================================
# SECTION 6 — THE TEST QUOTA HARNESS IS RELEASED BY EVERY FILE THAT INSTALLS IT
# ===========================================================================
#
# WHY THIS LIVES HERE. `tests/_provider_pin.py` mutates `config`'s two quota
# tables for the life of a process, and NOTHING asserted on that protocol --
# measured: no file under `tests/` names `ProviderPinError` outside the harness
# itself. This file is the one whose subject IS those tables and which installs
# no limits of its own, so it can drive the collision without first unwinding a
# pin it owns.
#
# THE DEFECT THIS CATCHES, WHICH WAS LIVE. Every file that called
# `install_test_quotas` imported `restore_test_quotas` and none of them called
# it. One file per process -- which is how CI bucket A runs them -- hides it
# completely; `pytest tests/` imports every one of them into ONE interpreter,
# where the SECOND install refuses BY DESIGN and aborts collection before a
# single check runs.
#
# THE INSTALLER SET IS DERIVED, NEVER LISTED. A hard-coded list of the four
# files that had the defect would go stale the moment a fifth installs limits,
# and would then assert nothing about it -- which is the shape this project
# removes. Every `tests/*.py` is parsed and the ones that CALL
# `install_test_quotas` are the subjects.

section("6. install_test_quotas is released by every file that installs it")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _provider_pin as _pin                                     # noqa: E402

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _calls(tree, name):
    """Line numbers of every call to the bare function `name` in `tree`."""
    return sorted(node.lineno for node in ast.walk(tree)
                  if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Name)
                  and node.func.id == name)


def _summary_line(tree):
    """The first line that PRINTS the pass count, or None.

    This is what "above the summary" is measured against: a release below it
    still decides the exit code while being absent from the number the summary
    printed, which is a run reporting "0 failed" and exiting non-zero.
    """
    lines = [node.lineno for node in ast.walk(tree)
             if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "print"
             and any("_RESULTS" in ast.unparse(a) and "passed" in ast.unparse(a)
                     for a in node.args)]
    return min(lines) if lines else None


_INSTALLERS = {}
for _name in sorted(os.listdir(_TESTS_DIR)):
    if not (_name.startswith("test_") and _name.endswith(".py")):
        continue
    try:
        _tree = ast.parse(open(os.path.join(_TESTS_DIR, _name),
                               encoding="utf-8").read())
    except SyntaxError:
        continue
    if _calls(_tree, "install_test_quotas"):
        _INSTALLERS[_name] = _tree

check("NON-DEGENERACY: the scan really found the files that install test quota "
      "limits, so the per-file checks below are not a loop over an empty set",
      len(_INSTALLERS) >= 4, True)
check("...and it found them by CALL rather than by import, so a file that "
      "imports the helper and never uses it is not counted",
      all(_calls(_t, "install_test_quotas") for _t in _INSTALLERS.values()), True)

for _name in sorted(_INSTALLERS):
    _tree = _INSTALLERS[_name]
    _rel = _calls(_tree, "restore_test_quotas") + _calls(_tree,
                                                         "release_test_quotas")
    _sum = _summary_line(_tree)
    check(f"*** {_name} RELEASES the limits it installed -- without this the "
          f"tables stay mutated for the life of the process and the next "
          f"install in it is refused ***", bool(_rel), True)
    check(f"...and {_name} does it ABOVE its summary, so the outcome is inside "
          f"the number the summary prints rather than only inside the exit code",
          (_sum is not None and bool(_rel) and min(_rel) < _sum), True)

# ── THE COLLISION ITSELF, DRIVEN ───────────────────────────────────────────
#
# The checks above are structural: they say the call is there. This says what
# the call is FOR. Both are needed -- a release present but broken satisfies
# the scan, and a collision driven on this harness says nothing about whether
# any file actually calls it.
_QUOTAS_BEFORE = (dict(config.PROVIDER_REQUESTS_PER_MINUTE),
                  dict(config.PROVIDER_TOKENS_PER_MINUTE))


def _raises(fn, *a, **kw):
    """Return the exception type name, or the value if it did not raise."""
    try:
        return fn(*a, **kw)
    except BaseException as exc:                                 # noqa: BLE001
        return type(exc).__name__


_pin.install_test_quotas("quotas-lookup-probe-a")
check("NON-DEGENERACY: installing really changed the tables, so the collision "
      "below is about a live installation",
      config.PROVIDER_REQUESTS_PER_MINUTE != _QUOTAS_BEFORE[0], True)
check("*** a SECOND install in one process is REFUSED by name -- this is what "
      "`pytest tests/` met, and it aborts collection rather than failing a "
      "check ***",
      _raises(_pin.install_test_quotas, "quotas-lookup-probe-b"),
      "ProviderPinError")
_PROBE_WHO, _PROBE_OK = _pin.restore_test_quotas()
check("...the release names the installer and reports both tables restored "
      "against _provider_pin's own import-time reading",
      (_PROBE_WHO, _PROBE_OK), ("quotas-lookup-probe-a", True))
check("*** ...and AFTER the release the same install SUCCEEDS, which is the "
      "property the four files' releases buy: the collision is no longer "
      "reachable through them ***",
      _raises(_pin.install_test_quotas, "quotas-lookup-probe-b"),
      (_QUOTAS_BEFORE[0], _QUOTAS_BEFORE[1]))
_pin.restore_test_quotas()
check("this section leaves both quota tables exactly as it found them",
      (config.PROVIDER_REQUESTS_PER_MINUTE,
       config.PROVIDER_TOKENS_PER_MINUTE), _QUOTAS_BEFORE)


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
Created on Fri Sep 11 2026

@author: ramyalsaffar
"""
