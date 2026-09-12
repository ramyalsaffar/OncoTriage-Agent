# Rater Verify-Before-Retry Test: an uncertain submission is never re-sent blind
###############################################################################

"""``reconcile_uncertain_submission`` and the ``submit_batches`` path that uses it.

WHAT THIS FILE HOLDS
--------------------
    1. THE CLOSED OUTCOME VOCABULARY, and that ``unknown`` is its own member.
       Folding it into ``absent`` is the duplicate submission the whole
       mechanism exists to prevent; folding it into ``found`` stops a session
       over a batch nobody created.
    2. THE THREE OUTCOMES, DRIVEN THROUGH THE REAL ``submit_batches``:
         found    -- the batch is ADOPTED and ``batches.create`` is called
                     EXACTLY ONCE in total (the failed attempt), never twice;
         absent   -- it is submitted exactly once more, and no more;
         unknown  -- a ``RaterRefusal`` and ZERO further creates.
    3. THE IDENTITY IS THE BYTES. A candidate whose parameters match but whose
       input file holds DIFFERENT requests is not this submission, and a
       candidate whose file cannot be READ raises rather than answering
       "absent" -- because "absent" is acted on by submitting again.
    4. THE HAPPY PATH REACHES NEITHER ``batches.list`` NOR ``files.content``.
       Every stand-in in this suite answers the two create endpoints and
       nothing else, so a reconciliation on the ordinary path would break all
       of them -- and would make a routine submission depend on two endpoints
       it has no reason to touch.
    5. A SPEND STOP IS NOT AN UNCERTAIN CREATE. ``SpendLimitReached`` is raised
       by the gate ABOVE the create, never by the provider, so it must travel
       to ``main()``'s own handler unchanged rather than being reconciled as a
       lost response -- which would ask the provider a question about a request
       that was never made.

WHAT IT COSTS TO RUN
--------------------
No network, no keys, NO SPEND -- every client is a stand-in and no provider
library is imported. No live Qdrant, NO MODEL LOAD
(``ONCOTRIAGE_DEFER_LOCAL_MODELS`` is set above the imports), no corpus, no
database, no git history, no live server. It writes only inside a
``tempfile.mkdtemp`` it removes and asserts gone, and it EXECS NOTHING: every
control is a different stand-in handed to the real function, which is the
natural instrument for a function whose whole subject is what it does with a
provider's answers. NOT in the collision matrix.

Run from terminal:
    python tests/test_rater_verify_before_retry.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
import os
import sys

# ABOVE THE PACKAGE IMPORTS, on oncotriage/fixtures/replay.py's precedent:
# oncotriage/agent/deps.py reads this once, at ITS OWN import.
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

import hashlib
import shutil
import tempfile
import types

from oncotriage import spend
from oncotriage.evaluation import rater as R

import _provider_pin                                             # noqa: E402

# EXPLICIT TEST QUOTA LIMITS, AND NO PROVIDER PIN. This file drives the REAL
# `submit_batches`, whose six OpenAI Batch-API management calls are now paced
# under `config.PROVIDER_QUOTA_SCOPE_OPENAI_BATCH` -- whose requests/minute the
# shipped config leaves UNKNOWN, so `provider_resilience.reserve` REFUSES
# before the send. That refusal is correct for production and it fires in an
# offline test too, because the pacer cannot tell a stand-in from a provider
# and MUST NOT TRY.
#
# MEASURED BEFORE THIS LINE EXISTED: 11 passed, 15 failed, with six
# `pacing_refused` refusals -- every create declined before the stand-in was
# reached. So this is the file saying what limit it drives under, not a harness
# configuring its way around the code.
#
# `test_quotas_only` AND NOT `pin_openai_arm`: this file's subject is the batch
# harness under the SHIPPED provider, and pinning Stage 5 to OpenAI would change
# what it measures to make an unrelated refusal go away.
_provider_pin.test_quotas_only(os.path.basename(__file__))


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
    __slots__ = ("kind", "message", "exc", "code")

    def __init__(self, exc):
        self.kind = type(exc).__name__
        self.message = str(exc)
        self.exc = exc
        self.code = getattr(exc, "code", None)


def drive(fn, *a, **kw):
    """Call fn; return its value or a Raised. Never raises."""
    try:
        return fn(*a, **kw)
    except BaseException as exc:                                 # noqa: BLE001
        return Raised(exc)


_TMP = tempfile.mkdtemp(prefix="raterverify_")
_CHUNK = [{"custom_id": "c0", "params": {"model": "m", "messages": []}},
          {"custom_id": "c1", "params": {"model": "m", "messages": []}}]
_TAG = "primary"
_INDEX = 0
_IDENTITY = R._batch_identity(_CHUNK, _TAG, _INDEX)
_BYTES = R.batch_jsonl(_CHUNK)


def _state_path(name):
    return os.path.join(_TMP, f"state_{name}.json")


class _Stub:
    """A provider stand-in whose every answer is chosen by the scenario.

    ``fail_create`` makes ``batches.create`` raise on its Nth call, which is
    how an UNCERTAIN create is produced without a network. Every endpoint
    records that it was reached, so "the happy path touches neither
    ``batches.list`` nor ``files.content``" is a MEASUREMENT rather than a
    reading of the source.
    """

    def __init__(self, *, fail_create_on=None, listed=None,
                 file_bodies=None, list_raises=False, content_raises=False):
        outer = self
        self.creates = 0
        self.uploads = 0
        self.listed_calls = 0
        self.content_calls = 0
        self.fail_create_on = fail_create_on
        self._listed = listed or []
        self._file_bodies = file_bodies or {}
        self._list_raises = list_raises
        self._content_raises = content_raises

        class _Files:
            def create(self, file=None, purpose=None, **_kw):
                outer.uploads += 1
                return types.SimpleNamespace(id=f"file_{outer.uploads}")

            def content(self, file_id):
                outer.content_calls += 1
                if outer._content_raises:
                    raise RuntimeError("download failed")
                return outer._file_bodies.get(file_id)

        class _Batches:
            def create(self, **kw):
                outer.creates += 1
                if outer.fail_create_on == outer.creates:
                    raise RuntimeError("connection reset after accept")
                return types.SimpleNamespace(id=f"batch_{outer.creates}", **kw)

            def list(self, limit=None):
                outer.listed_calls += 1
                if outer._list_raises:
                    raise RuntimeError("cannot list")
                return types.SimpleNamespace(data=list(outer._listed))

        self.files = _Files()
        self.batches = _Batches()


def _candidate(batch_id="batch_existing", file_id="file_existing",
               endpoint=None, window=None, metadata=None):
    """A provider-side batch that matches this submission unless told not to."""
    return types.SimpleNamespace(
        id=batch_id,
        input_file_id=file_id,
        endpoint=R.BATCH_ENDPOINT if endpoint is None else endpoint,
        completion_window=(R.BATCH_COMPLETION_WINDOW if window is None
                           else window),
        metadata=({"harness": "oncotriage-rater", "tag": _TAG,
                   "chunk": str(_INDEX)} if metadata is None else metadata))


# ===========================================================================
# SECTION 1 — THE CLOSED VOCABULARY
# ===========================================================================

section("1. Three outcomes, and 'unknown' is one of them")

check("*** the outcome vocabulary is CLOSED and has exactly three members. "
      "`unknown` is its own: folding it into `absent` resubmits a batch that "
      "may already be running, and folding it into `found` stops a session "
      "over a batch nobody created ***",
      sorted(R.RECONCILE_OUTCOMES),
      sorted(("found", "absent", "unknown")))
check("...and the three names are distinct, so a caller may branch on them "
      "exhaustively", len(set(R.RECONCILE_OUTCOMES)), 3)

# THE IDENTITY IS THE BYTES, and the digest is over exactly what the submitter
# uploads -- not over a re-derivation that could drift from it.
check("*** the identity digest is over `batch_jsonl(chunk)` -- the exact "
      "bytes submit_batches uploads -- so a batch whose input file hashes to "
      "it carries THIS chunk's requests ***",
      _IDENTITY["jsonl_sha256"],
      hashlib.sha256(_BYTES.encode("utf-8")).hexdigest())
check("...and the narrowing clues travel with it: the endpoint, the window "
      "and the metadata, because one file can be submitted twice with "
      "different parameters and such a batch is not this submission",
      (_IDENTITY["endpoint"], _IDENTITY["completion_window"],
       _IDENTITY["metadata"]["tag"]),
      (R.BATCH_ENDPOINT, R.BATCH_COMPLETION_WINDOW, _TAG))


# ===========================================================================
# SECTION 2 — THE THREE OUTCOMES, THROUGH THE REAL submit_batches
# ===========================================================================

section("2. found adopts, absent submits once, unknown refuses")

# --- FOUND: the batch is already there, and nothing is sent again ----------
_found_stub = _Stub(fail_create_on=1,
                    listed=[_candidate()],
                    file_bodies={"file_existing": _BYTES})
_found_state = {}
_found = drive(R.submit_batches, _found_stub, [_CHUNK], _found_state,
               _state_path("found"), _TAG)
check("*** FOUND: an uncertain create whose batch IS on the provider is "
      "ADOPTED, and `batches.create` was called EXACTLY ONCE in total -- the "
      "attempt that raised. A second call would commit the chunk's requests a "
      "second time and the duplicate is undetectable afterwards ***",
      (_found_stub.creates, isinstance(_found, Raised)), (1, False))
check("...and the adopted batch id is what was recorded, so a resume collects "
      "the batch the provider actually has",
      [b["id"] for b in _found_state.get("batches", [])], ["batch_existing"])
check("...and its input_file_id is the PROVIDER's, not a fabricated one: that "
      "field is the only evidence of a batch's request shape that exists "
      "anywhere, so recording a made-up value would poison the one check a "
      "later resume can run",
      [b["input_file_id"] for b in _found_state.get("batches", [])],
      ["file_existing"])

# --- ABSENT: nothing is there, so submit once -----------------------------
_absent_stub = _Stub(fail_create_on=1, listed=[])
_absent_state = {}
_absent = drive(R.submit_batches, _absent_stub, [_CHUNK], _absent_state,
                _state_path("absent"), _TAG)
check("*** ABSENT: the provider has no such batch, so it is submitted EXACTLY "
      "once more -- two creates in total, the failed one and the replacement, "
      "and not a loop ***",
      (_absent_stub.creates, isinstance(_absent, Raised)), (2, False))
check("...and the newly created batch is what was recorded",
      [b["id"] for b in _absent_state.get("batches", [])], ["batch_2"])

# --- UNKNOWN: the provider could not be asked ------------------------------
_unknown_stub = _Stub(fail_create_on=1, list_raises=True)
_unknown_state = {}
_unknown = drive(R.submit_batches, _unknown_stub, [_CHUNK], _unknown_state,
                 _state_path("unknown"), _TAG)
check("*** UNKNOWN: when the provider cannot be listed, this REFUSES rather "
      "than guessing ***",
      (_unknown.kind if isinstance(_unknown, Raised) else "<did not raise>"),
      "RaterRefusal")
check("*** ...and it RESUBMITTED NOTHING: still one create, the one that "
      "raised. This is the whole mechanism -- 'I could not tell' must never "
      "be acted on by sending again ***", _unknown_stub.creates, 1)
check("...and the refusal carries a code a wrapper can branch on",
      (_unknown.code if isinstance(_unknown, Raised) else None),
      "submission_uncertain")
check("...and it says nothing was resubmitted, which is the standing an "
      "operator reads before deciding what to do",
      ("RESUBMITTED" in (_unknown.message if isinstance(_unknown, Raised)
                         else "")), True)


# ===========================================================================
# SECTION 3 — THE IDENTITY DISCRIMINATES
# ===========================================================================

section("3. Matching parameters are not enough; the bytes decide")

# A candidate whose PARAMETERS match and whose BYTES do not is a different
# submission -- the case an operator produces by submitting two chunks of one
# run, which carry the same tag and the same endpoint.
_other_stub = _Stub(fail_create_on=1,
                    listed=[_candidate()],
                    file_bodies={"file_existing": "{\"custom_id\": \"zz\"}\n"})
_other_state = {}
_other = drive(R.submit_batches, _other_stub, [_CHUNK], _other_state,
               _state_path("other"), _TAG)
check("*** a candidate whose parameters match but whose INPUT FILE holds "
      "different requests is NOT this submission, so the outcome is absent "
      "and it is submitted once ***",
      (_other_stub.creates, isinstance(_other, Raised)), (2, False))

# A candidate whose metadata names another chunk is ruled out WITHOUT the
# download, which is what keeps the common case cheap.
_meta_stub = _Stub(fail_create_on=1,
                   listed=[_candidate(metadata={"harness": "oncotriage-rater",
                                                "tag": _TAG, "chunk": "7"})],
                   file_bodies={"file_existing": _BYTES})
_meta = drive(R.submit_batches, _meta_stub, [_CHUNK], {},
              _state_path("meta"), _TAG)
check("...and a candidate whose metadata names a DIFFERENT chunk is ruled out "
      "without downloading anything, which is what keeps the ordinary case "
      "cheap", (_meta_stub.content_calls, _meta_stub.creates), (0, 2))

# --- THE ONE THAT MUST NOT ANSWER "absent" --------------------------------
_unreadable_stub = _Stub(fail_create_on=1,
                         listed=[_candidate()],
                         content_raises=True)
_unreadable_state = {}
_unreadable = drive(R.submit_batches, _unreadable_stub, [_CHUNK],
                    _unreadable_state, _state_path("unreadable"), _TAG)
check("*** a candidate whose input file CANNOT BE DOWNLOADED raises rather "
      "than answering 'not this submission'. False there would be acted on by "
      "SUBMITTING AGAIN, so 'I could not read the evidence' must not be able "
      "to produce that action ***",
      (_unreadable.kind if isinstance(_unreadable, Raised)
       else "<did not raise>"), "RaterRefusal")
check("*** ...and it too resubmitted NOTHING ***", _unreadable_stub.creates, 1)
check("...naming the unreadable file as the reason, so an operator is not "
      "sent to look at their configuration",
      (_unreadable.code if isinstance(_unreadable, Raised) else None),
      "reconcile_unreadable")

# A candidate carrying no input file at all: same rule, different reason.
_nofile_stub = _Stub(fail_create_on=1,
                     listed=[_candidate(file_id=None)])
_nofile = drive(R.submit_batches, _nofile_stub, [_CHUNK], {},
                _state_path("nofile"), _TAG)
check("...and a candidate with NO input_file_id raises rather than being "
      "ruled out: its requests cannot be compared, so it can be neither "
      "adopted nor dismissed",
      ((_nofile.code if isinstance(_nofile, Raised) else None),
       _nofile_stub.creates), ("reconcile_no_input_file", 1))


# ===========================================================================
# SECTION 4 — THE HAPPY PATH TOUCHES NEITHER NEW ENDPOINT
# ===========================================================================

section("4. An ordinary submission reaches no reconciliation endpoint")

_clean_stub = _Stub()
_clean_state = {}
_clean = drive(R.submit_batches, _clean_stub, [_CHUNK], _clean_state,
               _state_path("clean"), _TAG)
check("*** CLEAN CONTROL: a submission that does not fail reaches neither "
      "`batches.list` nor `files.content`. Every stand-in in this suite "
      "answers the two create endpoints and nothing else, so a reconciliation "
      "on the ordinary path would break all of them ***",
      (_clean_stub.listed_calls, _clean_stub.content_calls), (0, 0))
check("...and it created exactly one batch and recorded it",
      (_clean_stub.creates, [b["id"] for b in _clean_state.get("batches", [])]),
      (1, ["batch_1"]))
check("NON-DEGENERACY: the failing scenarios above DID reach the listing, so "
      "the zero here is a statement about the happy path rather than about a "
      "stub nobody called",
      (_found_stub.listed_calls >= 1, _unknown_stub.listed_calls >= 1),
      (True, True))


# ===========================================================================
# SECTION 5 — A SPEND STOP IS NOT AN UNCERTAIN CREATE
# ===========================================================================

section("5. A budget stop travels unchanged")

# THE GATE IS ABOVE THE CREATE, so `SpendLimitReached` is never a lost
# response. Reconciling it would ask the provider about a request that was
# never made, and -- worse -- would convert a budget stop (exit 3, everything
# resumable) into a refusal (exit 1, "the configuration is wrong").
_saved_cap = spend.SPEND_LEDGER.total
_spend_stub = _Stub()
try:
    spend.SPEND_LEDGER.reset()
    spend.SPEND_LEDGER.charge_usd(10_000_000.0, spend.SPEND_SOURCE_RATER)
    _stopped = drive(R.submit_batches, _spend_stub, [_CHUNK], {},
                     _state_path("spend"), _TAG)
finally:
    spend.SPEND_LEDGER.reset()

check("*** a spend stop reaches the caller as SpendLimitReached, NOT as a "
      "RaterRefusal: main() maps the two to different exit codes and to "
      "opposite remedies ***",
      (_stopped.kind if isinstance(_stopped, Raised) else "<did not raise>"),
      "SpendLimitReached")
check("*** ...and it asked the provider NOTHING. The gate is above the create, "
      "so there is no lost response to reconcile ***",
      (_spend_stub.creates, _spend_stub.listed_calls), (0, 0))


# ===========================================================================
# SECTION 6 — HYGIENE
# ===========================================================================

section("6. What this file leaves behind")

shutil.rmtree(_TMP, ignore_errors=True)
check("the temp tree is removed", os.path.exists(_TMP), False)
check("the spend ledger is back to zero, so no later file in this process "
      "inherits a charged ledger", spend.SPEND_LEDGER.total, 0.0)


# ---------------------------------------------------------------------------
# RELEASED ABOVE THE SUMMARY, NEVER BELOW IT. A release below the results line
# still decides the exit code while being absent from the number the summary
# printed -- a run that reports "0 failed" and exits 1, which this project has
# shipped three times.
_QUOTA_WHO, _QUOTA_RESTORED = _provider_pin.release_test_quotas()
check("[provider pin] the test quota limits this file installed were released, "
      "and both tables are back to the shipped values",
      (_QUOTA_WHO, _QUOTA_RESTORED), (os.path.basename(__file__), True))

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
