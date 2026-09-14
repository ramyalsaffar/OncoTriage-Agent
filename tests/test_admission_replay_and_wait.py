# Admission Replay And Wait Test
################################

"""E1b: a replayed reservation is recognised, and a held decline waits.

WHAT THIS FILE HOLDS
--------------------
    1. Vocabulary: the two restated verdict vocabularies agree, the fourth
       spend limit and stop reason exist, the new counter is registered, and
       the admission wait's timeout derivation and its override guard.
    2. ITEM 1, IN PROCESS. A committed reservation replayed with identical
       fields is recognised inside the transaction -- no new row, no new
       liability, no second admission -- while a DIFFERENT attempt of the same
       size is still declined. Every immutable field that disagrees is refused
       by name; a settled attempt cannot be replayed. A commit whose
       acknowledgement is lost is retried into a replay, admitted once and
       dispatched once. The process authority applies the same rule to a hold
       token. A new wire attempt gets its own reservation.
    3. ITEM 1, FRESH PROCESSES. A process commits a reservation and is killed
       before the acknowledgement returns; fresh processes replay it (no row,
       no liability, no provider call), refuse a mismatch, count it exactly
       once for a new attempt, and dispatch exactly once through Stage 5 after
       an acknowledgement failure.
    4. ITEM 2 WITH REAL WORKERS. A transient hold releases and the waiter is
       admitted, under both authorities, with no lock or pacer permit held
       across the wait (another worker settles during it); the deadline is not
       reset by rechecks; a timeout stops the run cleanly and the gates then
       issue nothing; a server's timeout does not latch; the shutdown flag, a
       spend stop and the drain interrupt promptly; permanent exhaustion still
       latches without waiting; the queue is first-in, first-out (with its
       in-process control); the cap boundary; settle-once and release-once
       across several waiters; a process killed while waiting holds nothing.
    5. The REAL ``main()`` in fresh processes: a wait that times out stops the
       run STOPPED with stop_reason ``admission_wait``, nothing unfinished is
       checkpointed or marked succeeded, and a fresh process resumes exactly the
       unfinished patients while completed work is preserved. A clean control
       dispatches, completes and resumes.
    6. The real Stage 5 node after an admission-wait stop issues no request and
       records no verdict.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO MODEL LOAD, NO CORPUS. Every
provider client is a stand-in; every database and checkpoint is inside a
``tempfile.mkdtemp`` removed at the end and asserted gone. The production
database is never opened. It EXECS NOTHING and loads no module by location:
children are SCRIPTS written into the temp tree and run with ``sys.executable``.
In-process controls rebind a module attribute inside try/finally and assert the
restore BY IDENTITY. NOT IN THE COLLISION MATRIX.

Run from terminal:
    python tests/test_admission_replay_and_wait.py
"""


# Run needed file
#----------------
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

os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

import contextlib
import glob
import json
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import types
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _control_harness                                          # noqa: E402

_control_harness.isolate_qdrant(os.environ)

from oncotriage import config                                    # noqa: E402
from oncotriage import degradation as _degradation               # noqa: E402
from oncotriage import provider_resilience as _pr                # noqa: E402
from oncotriage import run_fingerprint as _rf                    # noqa: E402
from oncotriage import spend as _spend                           # noqa: E402
from oncotriage.ablation import study as _study                  # noqa: E402
from oncotriage.agent import deps                                # noqa: E402
from oncotriage.agent import evaluation as _ev                   # noqa: E402
from oncotriage.storage import database_logger as _dl            # noqa: E402

import _provider_pin                                             # noqa: E402

_PROVIDER_BEFORE_PIN = _provider_pin.pin_openai_arm(os.path.basename(__file__))

print(f"[e1b] oncotriage imported from "
      f"{os.path.realpath(os.path.dirname(oncotriage.__file__))}")


# ===========================================================================
# HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label, actual, expected):
    ok = actual == expected
    _RESULTS["passed" if ok else "failed"] += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"          expected: {expected!r}")
        print(f"          actual:   {actual!r}")
        _FAILURES.append(label)


def section(title):
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


class _Absent:
    def __init__(self, why):
        self.why = why

    def __eq__(self, other):
        return False

    def __hash__(self):
        return 0

    def __bool__(self):
        return False

    def __repr__(self):
        return f"<absent: {self.why}>"


def at(container, key):
    try:
        return container[key]
    except Exception as exc:                                   # noqa: BLE001
        return _Absent(f"{type(exc).__name__}: {key!r}")


def drive(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                               # noqa: BLE001
        return _Absent(f"raised {type(exc).__name__}: {exc}")


def raised(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except BaseException as exc:                               # noqa: BLE001
        return exc
    return None


def wait_until(predicate, timeout=10.0, step=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except Exception:                                      # noqa: BLE001
            pass
        time.sleep(step)
    return False


def halt_with_live_worker(where):
    """A worker thread outlived every bound this file gives it. Restoring
    configuration, rebinding a module attribute back, or calling
    ``reset_spend()`` underneath a thread that is still inside the retry policy
    would corrupt whatever it does next, and every later check would measure
    that corruption. So record the failure, print the results so far, and end
    the process here. Reached only when a defect keeps a worker alive past
    both its bound and the cancellation meant to end it."""
    check(f"{where} *** the worker thread exited before any shared state was "
          f"restored ***", "still alive", "exited")
    print("\n" + "=" * 78)
    print(f"RESULTS: {_RESULTS['passed']} passed, {_RESULTS['failed']} failed")
    print("=" * 78)
    for _label in _FAILURES:
        print(f"  FAILED: {_label}")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1)


_TMP = tempfile.mkdtemp(prefix="oncotriage-admission-e1b-")
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(oncotriage.__file__)))
_TESTS = os.path.dirname(os.path.abspath(__file__))
_WIRE = config.matching_wire_model()
_S5 = _spend.SPEND_SOURCE_STAGE5
_CONFIG_KEYS = ("SPEND_CAP_USD", "SPEND_CAP_ENFORCED", "SERVING_SPEND_CAP_USD",
                "ADMISSION_WAIT_TIMEOUT_SECONDS",
                "ADMISSION_WAIT_RECHECK_SECONDS",
                "MATCHING_PER_TRIAL_CALLS_ENABLED",
                "MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS")
_CONFIG_START = {k: getattr(config, k) for k in _CONFIG_KEYS}
# A PLANTED DEFECT MUST FAIL FAST, NOT SIT OUT THE SHIPPED 600 s WAIT. Every
# section that is not about the timeout itself runs under this short bound, and
# children get the same default; the sections that ARE about it set their own
# inside settings(). Found by the C1 firing control, which stalled ten minutes
# in 2h at the shipped default instead of failing. Restored by 7a.
_FAST_WAIT_TIMEOUT_S = 5.0
config.ADMISSION_WAIT_TIMEOUT_SECONDS = _FAST_WAIT_TIMEOUT_S
_JITTER_START = _pr.full_jitter_delay
_pr.full_jitter_delay = lambda retry_number, rng=None: 0.0
_BOUND = config.stage5_attempt_bound(
    _WIRE, config.MATCHING_MAX_TOKENS,
    config.matching_sdk_attempts_per_call())["usd"]

FIXED_FP = {"fingerprint_version": _rf.FINGERPRINT_VERSION}
for _field in _rf.FINGERPRINT_FIELDS:
    FIXED_FP[_field] = f"fixed-{_field}"
FIXED_FP.update(collection_points=12067, campaign_cohort_size=500,
                campaign_cohort_seed=42, matching_per_trial_empty_retries=1,
                matching_per_trial_parallel_bound=4)


@contextlib.contextmanager
def settings(**knobs):
    saved = {k: getattr(config, k) for k in knobs}
    for key, value in knobs.items():
        setattr(config, key, value)
    try:
        yield
    finally:
        for key, value in saved.items():
            setattr(config, key, value)


@contextlib.contextmanager
def rebound(owner, name, replacement):
    original = getattr(owner, name)
    setattr(owner, name, replacement)
    try:
        yield original
    finally:
        setattr(owner, name, original)
    check(f"   (restore) {getattr(owner, '__name__', owner)}.{name} is the "
          f"original object again", getattr(owner, name) is original, True)


def reset_spend():
    _spend.SPEND_LEDGER.reset()
    _spend.SPEND_STOP.reset()
    _spend.ADMISSION_QUEUE.reset()
    _spend.BILLING_RECORD.clear()
    _spend.BILLING_RECORD.reset_liability()
    _spend.BILLING_RECORD_FAULTS.clear()
    _spend.SPEND_LEDGER_FAULTS.clear()
    _spend.SPEND_ADMISSION_DECLINES.clear()
    _spend.SPEND_ADMISSION_WAITS.clear()
    _spend.SPEND_GATE_SKIPS.clear()
    _ev.clear_stage5_shutdown()
    _spend.reset_policy()


def new_db(name):
    db = os.path.join(_TMP, name)
    _dl.initialize_database(db)
    return db


def new_run(db):
    return _dl.start_run_record("e1b-admission", db_path=db)


def ro_rows(db, sql, params=()):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def liabilities(db, campaign, run_id):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return tuple(_dl.campaign_liabilities(conn.cursor(), campaign, run_id))
    finally:
        conn.close()


def install_sink(db, campaign, run_id):
    _spend.BILLING_RECORD.install(_dl.BillingRecordSink(db, campaign, run_id))


def begin(usd, source=_S5):
    return _spend.begin_billed_attempt(source, _WIRE, 1000, 100,
                                       where="e1b test", reserved_usd=usd)


class CountingPacer:
    """A stand-in pacer that paces nothing and counts permits NOT YET SETTLED,
    so a check can prove a waiting attempt holds no rate-limit slot."""

    def __init__(self):
        self._lock = threading.Lock()
        self.outstanding = 0
        self.reserved = 0

    def reserve(self, scope, tokens=0, *, slots=1):
        with self._lock:
            self.outstanding += 1
            self.reserved += 1
        return types.SimpleNamespace(waited_s=0.0, settled=False)

    def wait(self, permit, cancelled=None):
        return None

    def settle(self, permit, *, billing, actual_tokens=None):
        with self._lock:
            if not permit.settled:
                permit.settled = True
                self.outstanding -= 1


def s5_call(usd, *, calls, pacer, name="call", release=None, drain=False,
            fail_first=False, max_attempts=1):
    """One Stage 5-shaped logical call through the REAL retry policy and the
    REAL ``_Stage5AttemptRecord``. ``calls`` records every dispatch by name;
    ``release`` (an Event) holds the dispatch open until set."""
    record = _ev._Stage5AttemptRecord(_WIRE, 1000, 100, reserved_usd=usd)
    state = {"n": 0}

    class _Transient(Exception):
        pass

    def send():
        state["n"] += 1
        calls.append(name)
        if fail_first and state["n"] == 1:
            raise _Transient("transient")
        if release is not None:
            release.wait(30)
        return types.SimpleNamespace(
            usage=types.SimpleNamespace(prompt_tokens=0, completion_tokens=0),
            model=_WIRE)

    def classify(exc):
        return _pr.verdict_for(_pr.CATEGORY_THROTTLED
                               if isinstance(exc, _Transient)
                               else _pr.CATEGORY_LOCAL)

    return _pr.execute(send, scope=config.matching_quota_scope(),
                       reservation_tokens=1100,
                       reservation_kind=_pr.RESERVATION_INFERENCE,
                       classify=classify, sdk_attempts=1,
                       cancelled=_ev._stage5_cancellation(drain_applies=drain),
                       pacer=pacer, attempt_record=record,
                       max_attempts=max_attempts, usage_tokens_of=lambda r: 0,
                       label="e1b")


def spawn_call(usd, **kw):
    box = {}

    def run():
        try:
            box["result"] = s5_call(usd, **kw)
        except BaseException as exc:                           # noqa: BLE001
            box["exc"] = exc
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, box


def waits(key):
    return _spend.SPEND_ADMISSION_WAITS.get(f"{_S5}:{key}", 0)


# ===========================================================================
section("SECTION 1 -- vocabulary, the stop reason and the timeout")
# ===========================================================================

check("1a the retry policy's verdict vocabulary is spend's",
      _pr.ADMISSION_WAIT_VERDICTS, _spend.ADMISSION_WAIT_VERDICTS)
check("1b SPEND_LIMITS gained admission_wait as a fourth member",
      _spend.SPEND_LIMITS,
      (_spend.SPEND_LIMIT_CAP, _spend.SPEND_LIMIT_CALL_CEILING,
       _spend.SPEND_LIMIT_BILLING_RECORD, _spend.SPEND_LIMIT_ADMISSION_WAIT))
check("1c the runs and ablation stop-reason vocabularies carry it under the "
      "same string",
      (_dl.RUN_STOP_REASON_ADMISSION_WAIT in _dl.RUN_STOP_REASONS,
       _study.RUN_STOP_REASON_ADMISSION_WAIT in _study.RUN_STOP_REASONS,
       _dl.RUN_STOP_REASON_ADMISSION_WAIT, _study.RUN_STOP_REASON_ADMISSION_WAIT),
      (True, True, _spend.SPEND_LIMIT_ADMISSION_WAIT,
       _spend.SPEND_LIMIT_ADMISSION_WAIT))
check("1d SPEND_ADMISSION_WAITS is on the run-end degradation report",
      "SPEND_ADMISSION_WAITS" in _degradation.registered_names(), True)
check("1e the reservation outcome vocabulary is closed",
      _dl.RESERVATION_OUTCOMES, (_dl.RESERVATION_WRITTEN,
                                 _dl.RESERVATION_REPLAYED))
with settings(ADMISSION_WAIT_TIMEOUT_SECONDS=None):
    _derived = drive(config.admission_wait_timeout_seconds)
check("1f the derived timeout is RELEASE_ROUNDS x the request read budget x SDK "
      "attempts, and longer than one request's read budget (non-degenerate)",
      (_derived == float(config.ADMISSION_WAIT_RELEASE_ROUNDS
                         * config.MATCHING_REQUEST_TIMEOUT_SECONDS
                         * config.matching_sdk_attempts_per_call()),
       isinstance(_derived, float)
       and _derived > config.MATCHING_REQUEST_TIMEOUT_SECONDS),
      (True, True))
_bad_overrides = []
for _bad in (0, -1.0, True, float("nan"), float("inf"), "5"):
    with settings(ADMISSION_WAIT_TIMEOUT_SECONDS=_bad):
        _bad_overrides.append(type(raised(
            config.admission_wait_timeout_seconds)).__name__)
with settings(ADMISSION_WAIT_TIMEOUT_SECONDS=2.5):
    _good = drive(config.admission_wait_timeout_seconds)
check("1g an override that is not a positive finite number RAISES by name; a "
      "good one is used", (_bad_overrides, _good), (["ValueError"] * 6, 2.5))


# ===========================================================================
section("SECTION 2 -- ITEM 1 in process: a replay is recognised, a mismatch "
        "refused")
# ===========================================================================

reset_spend()
_DB2 = new_db("replay.db")
_RUN2 = new_run(_DB2)
_CAMP2 = "replay-campaign"
_FIELDS = dict(campaign_id=_CAMP2, run_id=_RUN2, source=_S5, model=_WIRE,
               input_tokens=1000, output_tokens=100, reserved_usd=9.0,
               note='{"bound": "e1b"}')
_AID2 = uuid.uuid4().hex
_w = drive(_dl.reserve_billing_attempt_outcome, _DB2, attempt_id=_AID2,
           admission_cap=10.0, **_FIELDS)
check("2a the first reservation is WRITTEN", getattr(_w, "outcome", _w),
      _dl.RESERVATION_WRITTEN)
_liab_before = liabilities(_DB2, _CAMP2, _RUN2)
_r = drive(_dl.reserve_billing_attempt_outcome, _DB2, attempt_id=_AID2,
           admission_cap=10.0, **_FIELDS)
check("2b *** REPLAY: the same attempt with identical fields under a cap its "
      "amount could not fit twice ($9 + $9 > $10) is REPLAYED -- one row, the "
      "campaign's liabilities unchanged ***",
      (getattr(_r, "outcome", _r), getattr(_r, "attempt_id", None) == _AID2,
       ro_rows(_DB2, "SELECT COUNT(*) FROM billing_attempts")[0][0],
       liabilities(_DB2, _CAMP2, _RUN2) == _liab_before),
      (_dl.RESERVATION_REPLAYED, True, 1, True))
_other = raised(_dl.reserve_billing_attempt_outcome, _DB2,
                attempt_id=uuid.uuid4().hex, admission_cap=10.0, **_FIELDS)
check("2c non-degeneracy: a DIFFERENT attempt of the same $9 is still declined "
      "headroom_held, so the replay's admission really was skipped",
      (type(_other).__name__, getattr(_other, "reason", None)),
      ("BillingAdmissionDeclined", "headroom_held"))

_mismatches = {}
for _name, _field, _value in (
        ("reserved_usd", "reserved_usd", 5.0),
        ("run_id", "run_id", new_run(_DB2)),
        ("source", "source", _spend.SPEND_SOURCE_EMBEDDING),
        ("model", "model", "another-model"),
        ("reserved_input_tokens", "input_tokens", 999),
        ("reserved_output_tokens", "output_tokens", 99),
        ("note", "note", '{"bound": "other"}'),
        ("campaign_id", "campaign_id", "another-campaign")):
    _changed = dict(_FIELDS, **{_field: _value})
    for _cap in (10.0, None):
        _exc = raised(_dl.reserve_billing_attempt_outcome, _DB2,
                      attempt_id=_AID2, admission_cap=_cap, **_changed)
        _mismatches[(_name, _cap)] = (type(_exc).__name__,
                                      _name in getattr(_exc, "fields", ()))
check("2d *** A MISMATCH IS REFUSED BY NAME, NOT REUSED: every immutable field, "
      "with and without admission, raises BillingReservationConflict naming "
      "that field ***",
      sorted(set(_mismatches.values())),
      [("BillingReservationConflict", True)])
check("2d-i ...and the stored row is untouched",
      ro_rows(_DB2, "SELECT COUNT(*), state, reserved_usd FROM billing_attempts"),
      [(1, "reserved", 9.0)])
_dl.settle_billing_attempt(_DB2, _AID2, outcome="response", settled_usd=0.5)
_settled = raised(_dl.reserve_billing_attempt_outcome, _DB2, attempt_id=_AID2,
                  admission_cap=10.0, **_FIELDS)
check("2e a SETTLED attempt cannot be replayed: its liability is resolved, so a "
      "replay would hand a caller a reservation covering nothing",
      (type(_settled).__name__, "state" in getattr(_settled, "fields", ())),
      ("BillingReservationConflict", True))
_disc = [drive(_dl.record_settlement_discrepancy, _DB2, attempt_id="d-1",
               campaign_id=_CAMP2, run_id=_RUN2, source=_S5, model=_WIRE,
               result="missing", live_usd=1.0, durable_usd=0.0,
               shortfall_usd=1.0) for _ in range(2)]
check("2f preserved: a settlement discrepancy recorded twice is recorded twice "
      "and is ONE row (its writer's idempotence survives the stricter check)",
      (_disc, ro_rows(_DB2, "SELECT COUNT(*) FROM billing_attempts WHERE "
                            "attempt_id = ?",
                      ("d-1" + _dl.DISCREPANCY_ID_SUFFIX,))[0][0]),
      ([_dl.DISCREPANCY_RECORDED] * 2, 1))


class _AckLost(Exception):
    pass


def ack_failure(times=1):
    """Wrap the billing connection so its commit LANDS and then raises a
    retryable error ``times`` times -- an acknowledgement lost after the commit."""
    real = _dl._open_billing_connection
    state = {"fired": 0}

    class Conn:
        def __init__(self, conn):
            self._conn = conn

        def cursor(self):
            return self._conn.cursor()

        def rollback(self):
            return self._conn.rollback()

        def close(self):
            return self._conn.close()

        def commit(self):
            self._conn.commit()
            if state["fired"] < times:
                state["fired"] += 1
                raise sqlite3.OperationalError("database is locked")
    return rebound(_dl, "_open_billing_connection",
                   lambda db_path: Conn(real(db_path))), state


reset_spend()
_DB2g = new_db("ack.db")
_RUN2g = new_run(_DB2g)
install_sink(_DB2g, "ack-camp", _RUN2g)
_ctx, _st = ack_failure()
with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=10.0), _ctx:
    _liab = drive(begin, 9.0)
check("2g *** ACKNOWLEDGEMENT LOST AFTER THE COMMIT: the write retry replays the "
      "committed row -- the attempt is ADMITTED once, one row, $9 held once, no "
      "decline ***",
      (isinstance(_liab, _spend.AttemptLiability), _st["fired"],
       ro_rows(_DB2g, "SELECT COUNT(*), state, reserved_usd FROM "
                      "billing_attempts")[0],
       _spend.SPEND_LEDGER.held_usd(), dict(_spend.SPEND_ADMISSION_DECLINES)),
      (True, 1, (1, "reserved", 9.0), 9.0, {}))
if isinstance(_liab, _spend.AttemptLiability):
    _liab.resolve(_spend.BILLING_OUTCOME_NOT_BILLED)

reset_spend()
_RUN2h = new_run(_DB2g)
install_sink(_DB2g, "ack-stage5", _RUN2h)


class _Client:
    def __init__(self, release=None):
        self.calls = 0
        self.release = release
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls += 1
        if self.release is not None:
            self.release.wait(30)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(content='{"evaluations": []}',
                                              refusal=None),
                finish_reason="stop")],
            usage=types.SimpleNamespace(prompt_tokens=1000,
                                        completion_tokens=100,
                                        completion_tokens_details=None),
            model=_WIRE)


_client2h = _Client()
deps.set_override(deps.OPENAI_CLIENT, _client2h)
_ctx, _st = ack_failure()
try:
    with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=_BOUND * 1.5), _ctx:
        _res2h = drive(_ev.call_matching_model, "system prompt", "user prompt")
finally:
    deps.clear_override(deps.OPENAI_CLIENT)
check("2h *** THROUGH STAGE 5: an acknowledgement lost after the commit ends in "
      "exactly ONE provider call and ONE row, settled -- not a declined, "
      "stranded reservation ***",
      (not isinstance(_res2h, _Absent), _client2h.calls, _st["fired"],
       ro_rows(_DB2g, "SELECT COUNT(*), MIN(state) FROM billing_attempts WHERE "
                      "campaign_id = 'ack-stage5'")[0]),
      (True, 1, 1, (1, "settled")))

reset_spend()
_sources = _spend.BUDGET_SOURCES[_spend.SPEND_BUDGET_CAMPAIGN]
_a1 = _spend.SPEND_LEDGER.admit_hold("tok", _S5, 9.0, sources=_sources,
                                     cap=10.0, windowed=False)
_a2 = drive(_spend.SPEND_LEDGER.admit_hold, "tok", _S5, 9.0, sources=_sources,
            cap=10.0, windowed=False)
_a3 = raised(_spend.SPEND_LEDGER.admit_hold, "tok", _S5, 5.0, sources=_sources,
             cap=10.0, windowed=False)
_a4 = raised(_spend.SPEND_LEDGER.admit_hold, "tok", _spend.SPEND_SOURCE_EMBEDDING,
             9.0, sources=_sources, cap=10.0, windowed=False)
check("2i *** PROCESS AUTHORITY: a hold token replayed with its own source and "
      "amount admits WITHOUT doubling the hold; the same token for another "
      "amount or source is refused ***",
      (_a1[0], _a2[0] if isinstance(_a2, tuple) else _a2,
       type(_a3).__name__, type(_a4).__name__,
       _spend.SPEND_LEDGER.held_count(), _spend.SPEND_LEDGER.held_usd()),
      (True, True, "ValueError", "ValueError", 1, 9.0))

reset_spend()
_RUN2j = new_run(_DB2g)
install_sink(_DB2g, "new-wire", _RUN2j)
_calls2j = []
with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=100.0):
    _res2j = drive(s5_call, 9.0, calls=_calls2j, pacer=CountingPacer(),
                   fail_first=True, max_attempts=2)
_rows2j = ro_rows(_DB2g, "SELECT attempt_id, state, outcome FROM billing_attempts "
                         "WHERE campaign_id = 'new-wire' ORDER BY reserved_at")
check("2j *** A NEW WIRE ATTEMPT GETS ITS OWN RESERVATION: a retry after a "
      "transient failure writes a second, distinct attempt row; nothing is "
      "replayed onto the first ***",
      (not isinstance(_res2j, _Absent), len(_calls2j), len(_rows2j),
       len({r[0] for r in _rows2j}), [r[1] for r in _rows2j]),
      (True, 2, 2, 2, ["settled", "settled"]))


# ===========================================================================
section("SECTION 3 -- ITEM 1 in fresh processes")
# ===========================================================================

_CHILD = os.path.join(_TMP, "e1b_child.py")
Path(_CHILD).write_text(r'''
import glob, json, os, signal, sqlite3, sys, threading, time, types, uuid
cfg = json.loads(sys.argv[1])
sys.path.insert(0, cfg["repo"]); sys.path.insert(0, cfg["tests"])
os.environ["ONCOTRIAGE_DEFER_LOCAL_MODELS"] = "1"
import _control_harness
_control_harness.isolate_qdrant(os.environ)
from oncotriage import config, paths, spend, run_fingerprint
import _provider_pin
_provider_pin.pin_openai_arm("e1b-child", out=lambda *a, **k: None)
from oncotriage import provider_resilience as pr
from oncotriage.agent import deps, evaluation as ev
from oncotriage.storage import database_logger as dl
pr.full_jitter_delay = lambda retry_number, rng=None: 0.0
config.SPEND_CAP_ENFORCED = True
config.SPEND_CAP_USD = cfg["cap"]
config.ADMISSION_WAIT_TIMEOUT_SECONDS = (
    cfg["timeout"] if cfg.get("timeout") is not None else 5.0)
WIRE = config.matching_wire_model()
MODE = cfg["mode"]
DB = cfg.get("db")

def dump(**kw):
    with open(cfg["out"], "w") as fh:
        json.dump(kw, fh, default=str)

def rows(campaign):
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return c.execute("SELECT attempt_id, run_id, state, reserved_usd FROM "
                         "billing_attempts WHERE campaign_id = ?",
                         (campaign,)).fetchall()
    finally:
        c.close()

def liab(campaign, run_id):
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return list(dl.campaign_liabilities(c.cursor(), campaign, run_id))
    finally:
        c.close()

class Client:
    def __init__(self, block=False):
        self.calls, self.block = 0, block
        self.chat = types.SimpleNamespace(completions=self)
    def create(self, **kw):
        self.calls += 1
        if self.block:
            deadline = time.monotonic() + 60
            while (not spend.SPEND_STOP.requested
                   and not os.path.exists(cfg.get("release", "/nonexistent"))
                   and time.monotonic() < deadline):
                time.sleep(0.01)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(
                content="{}", refusal=None), finish_reason="stop")],
            usage=types.SimpleNamespace(prompt_tokens=1000, completion_tokens=100,
                                        completion_tokens_details=None),
            model=WIRE)

FIELDS = dict(campaign_id=cfg.get("campaign"), run_id=cfg.get("run_id"),
              source="stage5", model=WIRE, input_tokens=1000,
              output_tokens=100, reserved_usd=cfg.get("usd", 9.0),
              note='{"bound": "e1b-child"}')

if MODE == "commit_then_die":
    real = dl._open_billing_connection
    class Dying:
        def __init__(self, conn): self._c = conn
        def cursor(self): return self._c.cursor()
        def rollback(self): return self._c.rollback()
        def close(self): return self._c.close()
        def commit(self):
            self._c.commit()
            with open(cfg["marker"], "w") as fh:
                fh.write("committed; acknowledgement never returned")
            os.kill(os.getpid(), signal.SIGKILL)
    dl._open_billing_connection = lambda p: Dying(real(p))
    dl.reserve_billing_attempt_outcome(DB, attempt_id=cfg["attempt"],
                                       admission_cap=cfg["cap"], **FIELDS)
    dump(reached="after the commit -- the kill did not happen")
    sys.exit(3)

if MODE in ("replay", "mismatch"):
    client = Client()
    deps.set_override(deps.OPENAI_CLIENT, client)
    before = liab(cfg["campaign"], cfg["run_id"])
    fields = dict(FIELDS)
    if MODE == "mismatch":
        fields["reserved_usd"] = 5.0
    try:
        r = dl.reserve_billing_attempt_outcome(DB, attempt_id=cfg["attempt"],
                                               admission_cap=cfg["cap"],
                                               **fields)
        dump(result=r.outcome, rows=rows(cfg["campaign"]), before=before,
             after=liab(cfg["campaign"], cfg["run_id"]), calls=client.calls)
    except Exception as exc:
        dump(result=type(exc).__name__, fields=list(getattr(exc, "fields", ())),
             rows=rows(cfg["campaign"]), calls=client.calls)
    sys.exit(0)

if MODE == "new_attempt":
    spend.BILLING_RECORD.install(dl.BillingRecordSink(DB, cfg["campaign"],
                                                      cfg["run_id"]))
    out = {}
    for label, usd in (("over", 1.000001), ("fits", 1.0)):
        try:
            l = spend.begin_billed_attempt("stage5", WIRE, 1000, 100,
                                           where="e1b new attempt",
                                           reserved_usd=usd)
            out[label] = "admitted"
            l.resolve(spend.BILLING_OUTCOME_NOT_BILLED)
        except spend.BudgetAdmissionDeclined as exc:
            out[label] = exc.reason
    dump(**out)
    sys.exit(0)

if MODE == "ack_fail_stage5":
    spend.BILLING_RECORD.install(dl.BillingRecordSink(DB, cfg["campaign"],
                                                      cfg["run_id"]))
    real = dl._open_billing_connection
    fired = {"n": 0}
    class Conn:
        def __init__(self, conn): self._c = conn
        def cursor(self): return self._c.cursor()
        def rollback(self): return self._c.rollback()
        def close(self): return self._c.close()
        def commit(self):
            self._c.commit()
            if fired["n"] == 0:
                fired["n"] = 1
                raise sqlite3.OperationalError("database is locked")
    dl._open_billing_connection = lambda p: Conn(real(p))
    client = Client()
    deps.set_override(deps.OPENAI_CLIENT, client)
    try:
        ev.call_matching_model("system prompt", "user prompt")
        dump(result="dispatched", calls=client.calls, fired=fired["n"],
             rows=rows(cfg["campaign"]))
    except Exception as exc:
        dump(result=type(exc).__name__, calls=client.calls, fired=fired["n"],
             rows=rows(cfg["campaign"]), msg=str(exc))
    sys.exit(0)

if MODE == "die_while_waiting":
    spend.BILLING_RECORD.install(dl.BillingRecordSink(DB, cfg["campaign"],
                                                      cfg["run_id"]))
    holder = spend.begin_billed_attempt("stage5", WIRE, 1000, 100,
                                        where="e1b holder", reserved_usd=9.0)
    client = Client()
    deps.set_override(deps.OPENAI_CLIENT, client)
    real_bound = config.stage5_attempt_bound
    config.stage5_attempt_bound = lambda *a, **k: dict(real_bound(*a, **k),
                                                       usd=9.0)
    def waiter():
        try:
            ev.call_matching_model("system prompt", "user prompt")
        except BaseException:
            pass
    threading.Thread(target=waiter, daemon=True).start()
    deadline = time.monotonic() + 60
    while (spend.SPEND_ADMISSION_WAITS.get("stage5:entered", 0) < 1
           and time.monotonic() < deadline):
        time.sleep(0.01)
    with open(cfg["marker"], "w") as fh:
        fh.write(json.dumps({"entered": spend.SPEND_ADMISSION_WAITS.get(
            "stage5:entered", 0), "calls": client.calls}))
    os.kill(os.getpid(), signal.SIGKILL)

# ---- the REAL main() ----
from oncotriage.batch import runner
paths._RESOLVED["data_fhir_path"] = cfg["corpus"] + os.sep
paths._RESOLVED["inferences_path"] = DB
paths._RESOLVED["checkpoint_path"] = cfg["cp"] + os.sep
# THE RUNNER BINDS MAX_WORKERS BY NAME at import, so the module attribute is
# what its pool reads; config's is set too for anything reading it live.
config.MAX_WORKERS = cfg.get("workers", config.MAX_WORKERS)
runner.MAX_WORKERS = config.MAX_WORKERS
run_fingerprint.current = lambda *a, **k: dict(cfg["fingerprint"])
CLIENT = Client(block=cfg.get("block", False))
deps.set_override(deps.OPENAI_CLIENT, CLIENT)

class Tracking:
    def start_run(self, **k): pass
    def log_run_metrics(self, *a, **k): pass
    def end_run(self, **k): pass

runner.build_bm25_index_from_qdrant = lambda *a, **k: (object(), ["NCT1"])
runner.build_matching_graph = lambda *a, **k: object()
runner.tracking = Tracking()
runner.run_resample = lambda **k: None
LOCK, SEEN = threading.Lock(), {"patients": [], "errors": []}

def patient(fhir_path=None, graph=None, is_resample=False, run_id=None,
            db_path=None):
    stem = os.path.basename(str(fhir_path))
    with LOCK:
        SEEN["patients"].append(stem)
        SEEN.setdefault("seed", spend.SPEND_LEDGER.seeded._asdict())
    error = ""
    try:
        ev.call_matching_model("system prompt", "user prompt")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        with LOCK:
            SEEN["errors"].append(error)
    return {"patient_id": stem, "status": "error" if error else "success",
            "eligible_matches": 0, "near_misses": 0, "not_evaluable": 0,
            "total_time": 0.01, "timestamp": "2026-09-14T00:00:00",
            "error": error, "is_resample": False}

runner.process_patient = patient
campaign_file = os.path.join(cfg["cp"], runner.CAMPAIGN_RECORD_FILENAME)
EXIT = 0
try:
    runner.main()
except SystemExit as stop:
    EXIT = stop.code
checkpoint = None
for path in glob.glob(os.path.join(cfg["cp"], "*.json")):
    try:
        data = json.load(open(path))
    except Exception:
        continue
    if isinstance(data, dict) and "completed_stems" in data:
        checkpoint = sorted(data["completed_stems"])
dump(exit_code=EXIT, calls=CLIENT.calls, seen=SEEN, checkpoint=checkpoint,
     latch=[spend.SPEND_STOP.requested, spend.SPEND_STOP.limit],
     waits=dict(spend.SPEND_ADMISSION_WAITS),
     declines=dict(spend.SPEND_ADMISSION_DECLINES),
     campaign_id=(json.load(open(campaign_file))["campaign_id"]
                  if os.path.exists(campaign_file) else None))
''', encoding="utf-8")


def child_env():
    env = dict(os.environ)
    _control_harness.isolate_qdrant(env)
    env["ONCOTRIAGE_DEFER_LOCAL_MODELS"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_child(cfg, timeout=240):
    base = {"repo": _REPO, "tests": _TESTS, "cap": 10.0}
    base.update(cfg)
    proc = subprocess.Popen([sys.executable, _CHILD, json.dumps(base)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=child_env(), cwd=_TMP)
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        output, _ = proc.communicate()
    out = base.get("out")
    data = (json.loads(Path(out).read_text())
            if out and os.path.exists(out) else None)
    return proc.returncode, data, output


_DB3 = new_db("fresh.db")
_RUN3 = new_run(_DB3)
_CAMP3 = "fresh-campaign"
_AID3 = uuid.uuid4().hex
_MARK3 = os.path.join(_TMP, "committed.marker")
_rc3, _d3, _log3 = run_child({"mode": "commit_then_die", "db": _DB3,
                              "campaign": _CAMP3, "run_id": _RUN3,
                              "attempt": _AID3, "marker": _MARK3,
                              "out": os.path.join(_TMP, "3a.json")})
check("3a non-degeneracy: process A committed its $9 reservation and was "
      "SIGKILLed before the acknowledgement returned",
      (_rc3, os.path.exists(_MARK3), _d3,
       ro_rows(_DB3, "SELECT COUNT(*), state FROM billing_attempts")[0]),
      (-9, True, None, (1, "reserved")))
_rc3b, _d3b, _ = run_child({"mode": "replay", "db": _DB3, "campaign": _CAMP3,
                            "run_id": _RUN3, "attempt": _AID3,
                            "out": os.path.join(_TMP, "3b.json")})
check("3b *** FRESH PROCESS B REPLAYS IT: outcome replayed, still one row, the "
      "campaign's liabilities unchanged, ZERO provider calls ***",
      (_rc3b, at(_d3b, "result"), len(at(_d3b, "rows") or []),
       at(_d3b, "before") == at(_d3b, "after"), at(_d3b, "calls")),
      (0, _dl.RESERVATION_REPLAYED, 1, True, 0))
_rc3c, _d3c, _ = run_child({"mode": "mismatch", "db": _DB3, "campaign": _CAMP3,
                            "run_id": _RUN3, "attempt": _AID3,
                            "out": os.path.join(_TMP, "3c.json")})
check("3c *** FRESH PROCESS C, SAME ID AT $5: refused by name, not reused; still "
      "one $9 row, zero calls ***",
      (_rc3c, at(_d3c, "result"), at(_d3c, "fields"),
       [r[3] for r in (at(_d3c, "rows") or [])], at(_d3c, "calls")),
      (0, "BillingReservationConflict", ["reserved_usd"], [9.0], 0))
_rc3d, _d3d, _ = run_child({"mode": "new_attempt", "db": _DB3,
                            "campaign": _CAMP3, "run_id": new_run(_DB3),
                            "out": os.path.join(_TMP, "3d.json")})
check("3d *** FRESH PROCESS D, A NEW ATTEMPT: A's committed $9 is counted EXACTLY "
      "ONCE -- $1.000001 more is budget_exhausted, $1 fits the $10 cap ***",
      (_rc3d, at(_d3d, "over"), at(_d3d, "fits")),
      (0, "budget_exhausted", "admitted"))
_rc3e, _d3e, _ = run_child({"mode": "ack_fail_stage5", "db": _DB3,
                            "campaign": "fresh-stage5", "run_id": new_run(_DB3),
                            "cap": _BOUND * 1.5,
                            "out": os.path.join(_TMP, "3e.json")})
check("3e *** FRESH PROCESS, STAGE 5, ACKNOWLEDGEMENT LOST AFTER THE COMMIT: one "
      "provider call, one row, settled ***",
      (_rc3e, at(_d3e, "result"), at(_d3e, "calls"), at(_d3e, "fired"),
       [r[2] for r in (at(_d3e, "rows") or [])]),
      (0, "dispatched", 1, 1, ["settled"]))


# ===========================================================================
section("SECTION 4 -- ITEM 2: a bounded, cancellable wait with real workers")
# ===========================================================================

reset_spend()
with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=10.0,
              ADMISSION_WAIT_TIMEOUT_SECONDS=20.0,
              ADMISSION_WAIT_RECHECK_SECONDS=0.2):
    _h = begin(9.0)
    _pacer = CountingPacer()
    _calls = []
    _t, _box = spawn_call(9.0, calls=_calls, pacer=_pacer, name="W")
    _entered = wait_until(lambda: waits("entered") == 1)
    check("4a non-degeneracy: the waiter entered the wait behind a $9 hold",
          (_entered, _calls, dict(_spend.SPEND_ADMISSION_DECLINES)),
          (True, [], {f"{_S5}:headroom_held": 1}))
    time.sleep(0.3)
    check("4b *** WHILE IT WAITS IT HOLDS NOTHING: no provider call, no pacer "
          "permit outstanding, no hold of its own ***",
          (_calls, _pacer.outstanding, _spend.SPEND_LEDGER.held_count()),
          ([], 0, 1))
    _locks = []
    for _lk in (_spend.SPEND_LEDGER._lock, _dl._WRITE_LOCK,
                _spend.ADMISSION_QUEUE._cond):
        _got = _lk.acquire(timeout=0.5)
        _locks.append(_got)
        if _got:
            _lk.release()
    check("4c *** NO LOCK IS HELD ACROSS THE WAIT: the ledger lock, the write "
          "lock and the queue's own lock are all acquirable during it ***",
          (_locks, _t.is_alive()), ([True, True, True], True))
    _settler = threading.Thread(
        target=lambda: _h.resolve(_spend.BILLING_OUTCOME_RESPONSE, model=_WIRE,
                                  prompt_tokens=0, completion_tokens=0))
    _settler.start()
    _settler.join(2.0)
    _t.join(10.0)
    check("4d *** ANOTHER WORKER SETTLES DURING THE WAIT, AND THE WAITER IS "
          "ADMITTED: one dispatch, no exception, the wait ended admitted, "
          "nothing held or queued at the end ***",
          (_settler.is_alive(), _t.is_alive(), "exc" in _box, _calls,
           waits("entered"), waits("admitted"), _pacer.outstanding,
           _spend.SPEND_LEDGER.held_count(),
           _spend.ADMISSION_QUEUE.depth(_spend.SPEND_BUDGET_CAMPAIGN)),
          (False, False, False, ["W"], 1, 1, 0, 0, 0))

reset_spend()
_DB4 = new_db("durable-wait.db")
_RUN4 = new_run(_DB4)
install_sink(_DB4, "durable-wait", _RUN4)
with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=10.0,
              ADMISSION_WAIT_TIMEOUT_SECONDS=20.0,
              ADMISSION_WAIT_RECHECK_SECONDS=0.2):
    _h = begin(9.0)
    _calls = []
    _t, _box = spawn_call(9.0, calls=_calls, pacer=CountingPacer(), name="W")
    _entered = wait_until(lambda: waits("entered") == 1)
    _wl = _dl._WRITE_LOCK.acquire(timeout=0.5)
    if _wl:
        _dl._WRITE_LOCK.release()
    _settler = threading.Thread(
        target=lambda: _h.resolve(_spend.BILLING_OUTCOME_RESPONSE, model=_WIRE,
                                  prompt_tokens=0, completion_tokens=0))
    _settler.start()
    _settler.join(5.0)
    _t.join(15.0)
    check("4e *** DURABLE AUTHORITY: the waiter entered, the write lock was free "
          "during the wait, a worker's DURABLE settlement (write lock + "
          "database) completed during it, and the waiter was admitted: two rows, "
          "both settled ***",
          (_entered, _wl, _settler.is_alive(), _t.is_alive(), "exc" in _box,
           _calls, waits("admitted"),
           ro_rows(_DB4, "SELECT COUNT(*), MIN(state), MAX(state) FROM "
                         "billing_attempts")[0]),
          (True, True, False, False, False, ["W"], 1, (2, "settled", "settled")))

reset_spend()
_deadlines = []
_orig_await = _spend.HeadroomWait.await_admission


def _recording_await(self, decline, cancelled=None):
    try:
        return _orig_await(self, decline, cancelled)
    finally:
        _deadlines.append((id(self), self.deadline))


with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=10.0,
              ADMISSION_WAIT_TIMEOUT_SECONDS=1.5,
              ADMISSION_WAIT_RECHECK_SECONDS=0.1), \
        rebound(_spend, "_admission_preview", lambda *a, **k: None), \
        rebound(_spend.HeadroomWait, "await_admission", _recording_await):
    _h = begin(9.0)
    _calls = []
    # THE CALL RUNS ON A THREAD WITH A BOUNDED JOIN. The only bound on this call
    # is the wait's own deadline, which is the thing a defect here would break,
    # so without an outside bound that defect HANGS the file instead of failing
    # it (the C4 firing control: deadline reset on each recheck). A worker that
    # outlives the bound is a recorded failure, is ended through the shutdown
    # flag, and must be seen to exit before anything above is restored.
    _4F_JOIN_S = 6.0
    _4f_box = {}
    _t0 = time.monotonic()

    def _4f_drive():
        _4f_box["exc"] = raised(s5_call, 9.0, calls=_calls,
                                pacer=CountingPacer())
        _4f_box["elapsed"] = time.monotonic() - _t0

    _4f_thread = threading.Thread(target=_4f_drive, daemon=True)
    _4f_thread.start()
    _4f_thread.join(_4F_JOIN_S)
    if _4f_thread.is_alive():
        check(f"4f-0 *** the timed wait returned within {_4F_JOIN_S} s of a "
              f"1.5 s timeout ***", "still waiting", "returned")
        _ev.request_stage5_shutdown("e1b 4f: the timed wait outlived its bound")
        _4f_thread.join(_4F_JOIN_S)
        if _4f_thread.is_alive():
            halt_with_live_worker("4f-0b")
        _ev.clear_stage5_shutdown()
    _exc = at(_4f_box, "exc")
    _elapsed = at(_4f_box, "elapsed")
check("4f non-degeneracy: the preview always said 'fits', so the waiter went "
      "back to real admission and was declined again at least three times",
      (len(_deadlines) >= 3,
       _spend.SPEND_ADMISSION_DECLINES.get(f"{_S5}:headroom_held", 0) >= 3),
      (True, True))
check("4g *** THE DEADLINE IS NOT RESET BY RETRIES: one wait object, one "
      "deadline across every recheck, and it timed out ~1.5 s after it entered "
      "rather than 1.5 s after its last recheck ***",
      (len({d[0] for d in _deadlines}), len({d[1] for d in _deadlines}),
       1.5 <= _elapsed < 2.4),
      (1, 1, True))
check("4h *** A TIMEOUT STOPS THE RUN CLEANLY: Stage5SpendStopped naming "
      "admission_wait, the run latched at admission_wait, no dispatch, the wait "
      "counted timed_out ***",
      (type(_exc).__name__, getattr(_exc, "limit", None), _calls,
       [_spend.SPEND_STOP.requested, _spend.SPEND_STOP.limit],
       waits("timed_out"), waits("entered")),
      ("Stage5SpendStopped", _spend.SPEND_LIMIT_ADMISSION_WAIT, [],
       [True, _spend.SPEND_LIMIT_ADMISSION_WAIT], 1, 1))
_emb = raised(_spend.require_budget, _spend.SPEND_SOURCE_EMBEDDING, "e1b test")
_counter = _spend.Stage5CallCounter(10, config.matching_call_mode())
_gate = _ev._spend_gate(_spend.SPEND_SKIP_WAVE_KEY_PREFIX, _counter,
                        where="e1b test")
check("4i *** ...AND AFTER IT NOTHING FURTHER IS ISSUED: Stage 5's gate and the "
      "non-Stage-5 gate both refuse at admission_wait ***",
      (type(_gate).__name__, getattr(_gate, "limit", None),
       type(_emb).__name__, getattr(_emb, "limit", None)),
      ("Stage5SpendStopped", _spend.SPEND_LIMIT_ADMISSION_WAIT,
       "SpendLimitReached", _spend.SPEND_LIMIT_ADMISSION_WAIT))
check("   (restore) the recorded deadlines were read from the SHIPPED "
      "await_admission",
      _spend.HeadroomWait.await_admission is _orig_await, True)
_h.resolve(_spend.BILLING_OUTCOME_NOT_BILLED)

reset_spend()
_spend.set_policy(_spend.SPEND_POLICY_WINDOW, source="e1b test")
with settings(SPEND_CAP_ENFORCED=True, SERVING_SPEND_CAP_USD=10.0,
              ADMISSION_WAIT_TIMEOUT_SECONDS=0.8,
              ADMISSION_WAIT_RECHECK_SECONDS=0.1):
    _h = begin(9.0)
    _exc = raised(s5_call, 9.0, calls=[], pacer=CountingPacer())
    _h.resolve(_spend.BILLING_OUTCOME_NOT_BILLED)
check("4j a SERVER's timeout (rolling-window policy) refuses that request and "
      "does NOT latch the process",
      (getattr(_exc, "limit", None), _spend.SPEND_STOP.requested,
       getattr(getattr(_exc, "__cause__", None), "latched", None)),
      (_spend.SPEND_LIMIT_ADMISSION_WAIT, False, False))
_spend.reset_policy()


def cancel_case(trigger, *, drain=False):
    reset_spend()
    with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=10.0,
                  ADMISSION_WAIT_TIMEOUT_SECONDS=30.0,
                  ADMISSION_WAIT_RECHECK_SECONDS=0.2):
        # GUARDED: a decline here (a hold left by an earlier case) is a
        # recorded failure of the caller's check, not a traceback.
        holder = drive(begin, 9.0)
        if isinstance(holder, _Absent):
            return (False, float("inf"), None, {"exc": holder}, [],
                    (False, _spend.SPEND_LEDGER.held_count(),
                     _spend.ADMISSION_QUEUE.depth(_spend.SPEND_BUDGET_CAMPAIGN)))
        calls = []
        thread, box = spawn_call(9.0, calls=calls, pacer=CountingPacer(),
                                 drain=drain)
        entered = wait_until(lambda: waits("entered") == 1)
        t0 = time.monotonic()
        trigger()
        thread.join(5.0)
        took = time.monotonic() - t0
        alive = thread.is_alive()
        drive(holder.resolve, _spend.BILLING_OUTCOME_NOT_BILLED)
        thread.join(5.0)
        # What the checks read is taken HERE, before the cleanup below can let a
        # waiter that ignored its trigger go on to dispatch.
        seen_box, seen_calls = dict(box), list(calls)
        # CLEANUP: forget the shutdown and the drain, then give a waiter still
        # running its own wait timeout to end; a SPEND_STOP is left latched, so
        # such a waiter ends at that timeout rather than being re-admitted. Any
        # reservation the waiter took is resolved by the retry policy as it
        # returns. The caller's check fails if the thread is still alive or a
        # hold or queue entry is left, and the file stops before the next
        # case's reset_spend() can run underneath it.
        _ev.clear_stage5_shutdown()
        thread.join(float(config.ADMISSION_WAIT_TIMEOUT_SECONDS) + 5.0)
        cleanup = (thread.is_alive(), _spend.SPEND_LEDGER.held_count(),
                   _spend.ADMISSION_QUEUE.depth(_spend.SPEND_BUDGET_CAMPAIGN))
    _ev.clear_stage5_shutdown()
    return entered, took, alive, seen_box, seen_calls, cleanup


_prompt = config.PROVIDER_WAIT_POLL_SECONDS + 0.5
_e, _took, _alive, _box, _calls, _cleanup = cancel_case(
    lambda: _ev.request_stage5_shutdown("e1b SIGTERM stand-in"))
check("4k *** SHUTDOWN (the SIGTERM / Ctrl-C flag) interrupts a 30 s wait within "
      "one poll interval, as Stage5ShutdownRequested, with no dispatch; the "
      "waiter exited and left no hold or queue entry ***",
      (_e, _took < _prompt, _alive, type(at(_box, "exc")).__name__, _calls,
       waits("cancelled"), _cleanup),
      (True, True, False, "Stage5ShutdownRequested", [], 1, (False, 0, 0)))
if _cleanup[0]:
    halt_with_live_worker("4k-0")
_e, _took, _alive, _box, _calls, _cleanup = cancel_case(
    lambda: _spend.SPEND_STOP.trip(_spend.SPEND_LIMIT_CALL_CEILING, "e1b", _S5))
check("4l *** A SPEND STOP latched elsewhere interrupts it promptly, as "
      "Stage5SpendStopped; the waiter exited and left no hold or queue "
      "entry ***",
      (_e, _took < _prompt, _alive, type(at(_box, "exc")).__name__, _calls,
       _cleanup),
      (True, True, False, "Stage5SpendStopped", [], (False, 0, 0)))
if _cleanup[0]:
    halt_with_live_worker("4l-0")
_e, _took, _alive, _box, _calls, _cleanup = cancel_case(
    lambda: _ev.request_stage5_drain("e1b STOP stand-in"), drain=True)
check("4m *** THE OPERATOR'S STOP (drain) interrupts a wait where drain applies "
      "(nothing paid in the current attempt), as Stage5DrainRequested; the "
      "waiter exited and left no hold or queue entry ***",
      (_e, _took < _prompt, _alive, type(at(_box, "exc")).__name__, _calls,
       _cleanup),
      (True, True, False, "Stage5DrainRequested", [], (False, 0, 0)))
if _cleanup[0]:
    halt_with_live_worker("4m-0")
_e, _took, _alive, _box, _calls, _cleanup = cancel_case(
    lambda: _ev.request_stage5_drain("e1b STOP stand-in"), drain=False)
check("4n ...and, as the drain's existing contract says, it does NOT cancel a "
      "wait where drain does not apply: that attempt was STILL WAITING after the "
      "drain, and was admitted when the hold settled; it then exited and left "
      "no hold or queue entry",
      (_e, _alive, "exc" in _box, _calls, _cleanup),
      (True, True, False, ["call"], (False, 0, 0)))
if _cleanup[0]:
    halt_with_live_worker("4n-0")

# A STOP SEEN BY THE RETRY POLICY ITSELF, after a positive preview and before
# re-admission, ends the wait CANCELLED (the E1b self-review defect: it was
# counted failed). The preview stand-in requests the shutdown and answers
# "fits", so the only place the stop can be seen is that re-check.
reset_spend()


def _preview_then_shutdown(*_a, **_k):
    _ev.request_stage5_shutdown("e1b stop between preview and re-admission")
    return None


with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=10.0,
              ADMISSION_WAIT_TIMEOUT_SECONDS=30.0,
              ADMISSION_WAIT_RECHECK_SECONDS=0.05), \
        rebound(_spend, "_admission_preview", _preview_then_shutdown):
    _h = begin(9.0)
    _calls = []
    _pacer = CountingPacer()
    _t0 = time.monotonic()
    _exc = raised(s5_call, 9.0, calls=_calls, pacer=_pacer)
    _took = time.monotonic() - _t0
    _after = (waits("entered"), waits("cancelled"), waits("failed"),
              _spend.ADMISSION_QUEUE.depth(_spend.SPEND_BUDGET_CAMPAIGN),
              _pacer.outstanding)
    _h.resolve(_spend.BILLING_OUTCOME_NOT_BILLED)
_ev.clear_stage5_shutdown()
check("4n-i *** A STOP SEEN AT THE RE-CHECK AFTER A POSITIVE PREVIEW ends the "
      "wait CANCELLED, not failed: Stage5ShutdownRequested, no dispatch, the "
      "wait counted once as cancelled, nothing queued, no permit outstanding ***",
      (type(_exc).__name__, _calls, _after, _took < 5.0),
      ("Stage5ShutdownRequested", [], (1, 1, 0, 0, 0), True))
check("   (restore) the preview stand-in is removed",
      _spend._admission_preview.__name__, "_admission_preview")

reset_spend()
_spend.SPEND_LEDGER.charge_usd(9.5, _S5)
with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=10.0):
    _calls = []
    _exc = raised(s5_call, 1.0, calls=_calls, pacer=CountingPacer())
check("4o *** PERMANENT EXHAUSTION STILL LATCHES AND DOES NOT WAIT: $9.50 "
      "committed + $1 > $10 -- budget_exhausted, latched spend_cap, no wait "
      "entered, no dispatch ***",
      (type(_exc).__name__, getattr(_exc, "admission_reason", None),
       [_spend.SPEND_STOP.requested, _spend.SPEND_STOP.limit],
       waits("entered"), _calls),
      ("Stage5SpendStopped", "budget_exhausted",
       [True, _spend.SPEND_LIMIT_CAP], 0, []))


def fifo_case():
    reset_spend()
    order = []
    go = threading.Event()
    hold_w1 = threading.Event()
    details = []
    real_preview = _spend._admission_preview
    real_declined = _spend.admission_declined
    state = {"first": True}

    def gated_preview(*a, **k):
        if state["first"]:
            state["first"] = False
            go.wait(10)
        return real_preview(*a, **k)

    def recording_declined(reason, **kw):
        details.append(kw.get("detail"))
        return real_declined(reason, **kw)

    with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=10.0,
                  ADMISSION_WAIT_TIMEOUT_SECONDS=20.0,
                  ADMISSION_WAIT_RECHECK_SECONDS=0.2), \
            rebound(_spend, "_admission_preview", gated_preview), \
            rebound(_spend, "admission_declined", recording_declined):
        holder = begin(9.0)
        t1, b1 = spawn_call(9.0, calls=order, pacer=CountingPacer(), name="W1",
                            release=hold_w1)
        wait_until(lambda: waits("entered") == 1)
        holder.resolve(_spend.BILLING_OUTCOME_NOT_BILLED)
        time.sleep(0.3)
        t2, b2 = spawn_call(9.0, calls=order, pacer=CountingPacer(), name="N")
        wait_until(lambda: len(order) >= 1 or waits("entered") >= 2, timeout=3)
        first_seen = list(order)
        go.set()
        wait_until(lambda: "W1" in order and "N" not in order, timeout=5)
        hold_w1.set()
        t1.join(10)
        t2.join(10)
    return first_seen, order, details, b1, b2


_first, _order, _details, _b1, _b2 = fifo_case()
check("4p *** FIRST-IN, FIRST-OUT: with the head waiter W1 checking and the "
      "headroom free, a NEW arrival N is declined behind it (named as queued), "
      "and W1 is admitted before N ***",
      (_first, _order, any("earlier attempt" in (d or "") for d in _details),
       "exc" in _b1, "exc" in _b2),
      ([], ["W1", "N"], True, False, False))
with rebound(_spend.HeadroomWait, "ahead", lambda self: 0):
    _cfirst, _corder, _cdetails, _, _ = fifo_case()
check("4p-i CONTROL: the same scenario with the queue check removed lets N take "
      "the headroom released for W1 -- N is dispatched first",
      (_cfirst, _corder[:1]), (["N"], ["N"]))

reset_spend()
with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=10.0,
              ADMISSION_WAIT_TIMEOUT_SECONDS=20.0,
              ADMISSION_WAIT_RECHECK_SECONDS=0.2):
    _calls = []
    _exact = drive(s5_call, 10.0, calls=_calls, pacer=CountingPacer())
    _over = raised(s5_call, 10.000001, calls=_calls, pacer=CountingPacer())
    _spend.SPEND_STOP.reset()
    _h1 = begin(1.0)
    _just = drive(s5_call, 9.0, calls=_calls, pacer=CountingPacer())
    _t, _box = spawn_call(9.000001, calls=_calls, pacer=CountingPacer(),
                          name="above")
    _waited = wait_until(lambda: waits("entered") == 1)
    _h1.resolve(_spend.BILLING_OUTCOME_NOT_BILLED)
    _t.join(10)
check("4q *** THE BOUNDARY: $10 against a $10 cap dispatches (equality "
      "admitted); $10.000001 is budget_exhausted; with $1 held, $9 dispatches "
      "and $9.000001 WAITS and is admitted once the $1 settles ***",
      (not isinstance(_exact, _Absent), getattr(_over, "admission_reason", None),
       not isinstance(_just, _Absent), _waited, "exc" in _box,
       _calls.count("call"), _calls.count("above")),
      (True, "budget_exhausted", True, True, False, 2, 1))

reset_spend()
with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=10.0,
              ADMISSION_WAIT_TIMEOUT_SECONDS=20.0,
              ADMISSION_WAIT_RECHECK_SECONDS=0.2):
    _h = begin(9.0)
    _calls = []
    _threads = [spawn_call(4.0, calls=_calls, pacer=CountingPacer(),
                           name=f"w{i}") for i in range(3)]
    _all_in = wait_until(lambda: waits("entered") == 3)
    _h.resolve(_spend.BILLING_OUTCOME_RESPONSE, model=_WIRE, prompt_tokens=0,
               completion_tokens=0)
    for _th, _ in _threads:
        _th.join(15)
    _again = _h.resolve(_spend.BILLING_OUTCOME_NOT_BILLED)
check("4r *** SETTLE ONCE, RELEASE ONCE ACROSS SEVERAL WAITERS: three waiters "
      "behind one hold are all admitted in turn; the hold's second resolution "
      "returns its first amount and charges nothing; nothing is held or "
      "queued; entered == admitted ***",
      (_all_in, sorted(_calls), any("exc" in b for _, b in _threads),
       _again, _spend.SPEND_LEDGER.held_count(),
       _spend.ADMISSION_QUEUE.depth(_spend.SPEND_BUDGET_CAMPAIGN),
       waits("entered"), waits("admitted"), _spend.SPEND_LEDGER.calls),
      (True, ["w0", "w1", "w2"], False, 0.0, 0, 0, 3, 3, 4))

_DB4s = new_db("die-waiting.db")
_MARK4 = os.path.join(_TMP, "waiting.marker")
_rc4s, _, _ = run_child({"mode": "die_while_waiting", "db": _DB4s,
                         "campaign": "die-waiting", "run_id": new_run(_DB4s),
                         "marker": _MARK4, "timeout": 60.0,
                         "out": os.path.join(_TMP, "4s.json")})
_mark4 = json.loads(Path(_MARK4).read_text()) if os.path.exists(_MARK4) else {}
check("4s non-degeneracy: the child's waiter entered the wait and the child was "
      "SIGKILLed while it waited, having issued no call",
      (_rc4s, at(_mark4, "entered"), at(_mark4, "calls")), (-9, 1, 0))
check("4t *** A PROCESS KILLED WHILE WAITING RESERVED NOTHING FOR THE WAITER: "
      "only the holder's row exists ***",
      ro_rows(_DB4s, "SELECT COUNT(*), MAX(reserved_usd) FROM billing_attempts"),
      [(1, 9.0)])
_rc4u, _d4u, _ = run_child({"mode": "new_attempt", "db": _DB4s,
                            "campaign": "die-waiting", "run_id": new_run(_DB4s),
                            "out": os.path.join(_TMP, "4u.json")})
check("4u *** ...and a fresh process counts the holder once and the waiter not "
      "at all: $1 fits the $10 cap, $1.000001 is budget_exhausted ***",
      (_rc4u, at(_d4u, "over"), at(_d4u, "fits")),
      (0, "budget_exhausted", "admitted"))


# ===========================================================================
section("SECTION 5 -- the REAL main(): a timeout stops the run, a fresh process "
        "resumes")
# ===========================================================================

_CORPUS = os.path.join(_TMP, "corpus")
os.makedirs(_CORPUS)
for _i in range(3):
    Path(os.path.join(_CORPUS, f"patient{_i}.json")).write_text(
        json.dumps({"resourceType": "Bundle", "entry": []}))
_DB5 = os.path.join(_TMP, "runner.db")
_CP5 = os.path.join(_TMP, "cp-runner")
os.makedirs(_CP5)


def runner_child(tag, **cfg):
    out = os.path.join(_CP5, f"{tag}.json")
    base = {"mode": "runner", "db": _DB5, "cp": _CP5, "corpus": _CORPUS,
            "out": out, "fingerprint": FIXED_FP}
    base.update(cfg)
    return run_child(base)


_rc5, _d5, _log5 = runner_child("stop", cap=_BOUND * 1.5, timeout=2.0,
                                block=True, workers=2)
_seen5 = at(_d5, "seen") or {}
_runs5 = ro_rows(_DB5, "SELECT id, status, stop_reason, billing_campaign_id "
                       "FROM runs ORDER BY id")
check("5a non-degeneracy: two patients started, one was dispatched and held its "
      "reservation, the other waited",
      (_rc5, at(_d5, "calls"), len(at(_seen5, "patients") or []),
       (at(_d5, "waits") or {}).get("stage5:entered")),
      (0, 1, 2, 1))
check("5b *** THE WAIT TIMED OUT AND THE RUN STOPPED CLEANLY: run row STOPPED "
      "with stop_reason admission_wait, latched admission_wait, the timed-out "
      "patient's error names the wait ***",
      (_runs5[-1][1:3] if _runs5 else None, at(_d5, "latch"),
       (at(_d5, "waits") or {}).get("stage5:timed_out"),
       any("admission wait" in e for e in (at(_seen5, "errors") or []))),
      (("STOPPED", "admission_wait"), [True, "admission_wait"], 1, True))
_ckpt5 = at(_d5, "checkpoint") or []
check("5c *** NOTHING UNFINISHED IS MARKED COMPLETED: the checkpoint holds "
      "exactly ONE patient, the dispatched one; the timed-out and the never-"
      "started patients are not in it ***",
      (len(_ckpt5), len(at(_seen5, "errors") or [])), (1, 1))
check("5c-i ...and the billing record holds ONE settled row: nothing was "
      "reserved for the patient whose wait timed out",
      ro_rows(_DB5, "SELECT COUNT(*), MIN(state) FROM billing_attempts"),
      [(1, "settled")])
_camp5 = at(_d5, "campaign_id")
_rc5r, _d5r, _log5r = runner_child("resume", cap=100.0, timeout=None,
                                   block=False, workers=2)
_seen5r = at(_d5r, "seen") or {}
_runs5r = ro_rows(_DB5, "SELECT id, status, stop_reason, billing_campaign_id "
                        "FROM runs ORDER BY id")
check("5d *** A FRESH PROCESS RESUMES EXACTLY THE UNFINISHED PATIENTS: two "
      "calls, the completed patient NOT re-run, no error ***",
      (_rc5r, at(_d5r, "calls"),
       sorted(at(_seen5r, "patients") or []) == sorted(
           p for p in ("patient0.json", "patient1.json", "patient2.json")
           if p.replace(".json", "") not in _ckpt5),
       at(_seen5r, "errors")),
      (0, 2, True, []))
# THE RESUMED RUN'S OWN STATUS IS NOT ASSERTED, deliberately. The runner derives
# it from `load_results()`, the PERSISTED results list, which still carries run
# 1's errored entry for the timed-out patient, so a resume that completes every
# remaining patient is recorded FAILED. That is pre-existing behaviour shared by
# every stop that fails an in-flight patient (a cap stop, SIGTERM) and it is
# reported in RECOVERY_E1B_REPORT.md rather than pinned here.
check("5e *** COMPLETED WORK PRESERVED: the resumed run continued the SAME "
      "billing campaign, seeded with the first run's durable total, and every "
      "patient is now settled exactly once -- three settled rows in all ***",
      (bool(_camp5) and _runs5r[-1][3] == _camp5 if _runs5r else False,
       (at(at(_seen5r, "seed"), "usd") or 0) > 0,
       _runs5r[-1][1] in ("FINISHED", "FAILED") if _runs5r else False,
       ro_rows(_DB5, "SELECT COUNT(*), MIN(state), MAX(state) FROM "
                     "billing_attempts")[0]),
      (True, True, True, (3, "settled", "settled")))

_DB5c = os.path.join(_TMP, "clean.db")
_CP5c = os.path.join(_TMP, "cp-clean")
os.makedirs(_CP5c)
_rc5c, _d5c, _ = run_child({"mode": "runner", "db": _DB5c, "cp": _CP5c,
                            "corpus": _CORPUS, "cap": 100.0, "block": False,
                            "workers": 2, "fingerprint": FIXED_FP,
                            "out": os.path.join(_CP5c, "clean.json")})
check("5f *** CLEAN CONTROL: three patients dispatch and complete, no wait, no "
      "latch, run FINISHED ***",
      (_rc5c, at(_d5c, "calls"), at(_d5c, "waits"), at(_d5c, "latch"),
       ro_rows(_DB5c, "SELECT status FROM runs")),
      (0, 3, {}, [False, None], [("FINISHED",)]))
_rc5d, _d5d, _ = run_child({"mode": "runner", "db": _DB5c, "cp": _CP5c,
                            "corpus": _CORPUS, "cap": 100.0, "block": False,
                            "workers": 2, "fingerprint": FIXED_FP,
                            "out": os.path.join(_CP5c, "clean2.json")})
check("5g ...and a second clean invocation on a finished campaign dispatches "
      "the cohort again (a finished run clears its checkpoint)",
      (_rc5d, at(_d5d, "calls")), (0, 3))


# ===========================================================================
section("SECTION 6 -- the real Stage 5 node after an admission-wait stop")
# ===========================================================================

reset_spend()
_PATIENT = {
    "patient_id": "e1b-patient",
    "demographics": {"age": 61, "sex": "female", "race": "white",
                     "ethnicity": "not hispanic or latino"},
    "conditions": [{"code": "254837009",
                    "display": "Malignant neoplasm of breast (disorder)",
                    "verification_status": "confirmed"}],
    "medications": [], "allergies": [], "observations": [], "procedures": [],
}


def _trial(index):
    half = "x" * 200
    return {"trial": {"nct_id": "NCT%08d" % index, "title": f"Trial {index}",
                      "phase": "PHASE2",
                      "eligibility": {
                          "inclusion_criteria": "Inclusion Criteria:\n- " + half,
                          "exclusion_criteria": "Exclusion Criteria:\n- " + half}},
            "score": 0.5, "rerank_score": 0.5}


_node_client = _Client()
deps.set_override(deps.OPENAI_CLIENT, _node_client)
try:
    with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=1000.0,
                  MATCHING_PER_TRIAL_CALLS_ENABLED=True,
                  MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS=1):
        _spend.SPEND_STOP.trip(_spend.SPEND_LIMIT_ADMISSION_WAIT, "e1b", _S5)
        _node = drive(_ev.node_llm_classifier_evaluation, {
            "patient_data": _PATIENT, "filtered_trials": [_trial(i)
                                                          for i in range(3)],
            "llm_classifier_retries": 0, "mesh_filter_applied": True,
            "mesh_filter_skip_reason": "applied", "stage_timings": {}})
finally:
    deps.clear_override(deps.OPENAI_CLIENT)
check("6a *** AFTER AN ADMISSION-WAIT STOP THE REAL NODE ISSUES NO REQUEST, "
      "RECORDS NO VERDICT, AND ITS ERROR NAMES THE WAIT -- the patient is not "
      "marked completed or clinically failed ***",
      (_node_client.calls, at(_node, "evaluations"),
       "admission wait" in str(at(_node, "error"))),
      (0, [], True))


# ===========================================================================
section("SECTION 7 -- isolation held")
# ===========================================================================

reset_spend()
for _k, _v in _CONFIG_START.items():
    setattr(config, _k, _v)
_pr.full_jitter_delay = _JITTER_START
check("7a configuration restored",
      {k: getattr(config, k) for k in _CONFIG_KEYS}, _CONFIG_START)
check("7b the jitter stand-in is removed", _pr.full_jitter_delay is _JITTER_START,
      True)
shutil.rmtree(_TMP, ignore_errors=True)
check("7c the temp directory was removed", os.path.exists(_TMP), False)
_who, _prev, _restored = _provider_pin.release_openai_arm()
check("7d the provider pin is released", _restored, True)

print("\n" + "=" * 78)
print(f"RESULTS: {_RESULTS['passed']} passed, {_RESULTS['failed']} failed")
print("=" * 78)
if _FAILURES:
    for _label in _FAILURES:
        print(f"  FAILED: {_label}")
sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Sep 14 2026

@author: ramyalsaffar
"""
