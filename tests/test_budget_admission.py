# Budget Admission Test
#######################

"""E1: atomic budget admission.

WHAT THIS FILE HOLDS
--------------------
    1. Vocabulary: the durable and in-process decline reasons agree, the new
       counter is registered, the shipped sink declares admission, and the
       ledger's in-lock committed figure equals ``budget_spend``.
    2. RACES WITH REAL WORKERS. Threads released by a barrier cannot both take
       the last headroom -- under the in-process authority and under the durable
       one -- and child PROCESSES sharing one database cannot either.
    3. A full wave admitted, then released: held headroom returns, and only the
       unused difference of a settled attempt returns.
    4. A resumed balance plus live reservations: nothing is counted twice.
    5. The boundary -- just below, exactly at, just above the cap -- through
       Stage 5's public entry point, with zero provider calls on decline.
    6. Settle-once and release-once under duplicate and out-of-order completion.
    7. Death before the reservation commit and after it, before dispatch, in
       fresh processes.
    8. The operator surface and a clean control through the REAL ``main()``.
    9. Preservation: unverified records refuse first, a reservation that fails
       after admission releases its hold, a decline is never abandoned, the
       rolling window never latches.
   10. The durable authority's SQL summation equals ``campaign_billing_total``
       over every row shape it distinguishes.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO MODEL LOAD, NO CORPUS. Every
provider client is a stand-in; every database and checkpoint is inside a
``tempfile.mkdtemp`` removed at the end and asserted gone. The production
database is never opened. It EXECS NOTHING and loads no module by location:
children are SCRIPTS written into the temp tree and run with ``sys.executable``.
NOT IN THE COLLISION MATRIX.

Run from terminal:
    python tests/test_budget_admission.py
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
from oncotriage.agent import deps                                # noqa: E402
from oncotriage.agent import evaluation as _ev                   # noqa: E402
from oncotriage.agent import models as _models                   # noqa: E402
from oncotriage.batch import runner as _runner                   # noqa: E402
from oncotriage.storage import database_logger as _dl            # noqa: E402

import _provider_pin                                             # noqa: E402

_PROVIDER_BEFORE_PIN = _provider_pin.pin_openai_arm(os.path.basename(__file__))

print(f"[e1] oncotriage imported from "
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


_TOL = 1e-9


def near(a, b, eps=_TOL):
    return (isinstance(a, (int, float)) and isinstance(b, (int, float))
            and not isinstance(a, bool) and not isinstance(b, bool)
            and abs(a - b) <= eps)


_REPO = os.path.dirname(os.path.dirname(os.path.abspath(oncotriage.__file__)))
_TESTS = os.path.dirname(os.path.abspath(__file__))
_TMP = tempfile.mkdtemp(prefix="oncotriage-budget-admission-")
_WIRE = config.matching_wire_model()
_S5 = _spend.SPEND_SOURCE_STAGE5
_CONFIG_KEYS = ("SPEND_CAP_USD", "SPEND_CAP_ENFORCED", "SERVING_SPEND_CAP_USD")
_CONFIG_START = {k: getattr(config, k) for k in _CONFIG_KEYS}
_POLICY_START = _spend.policy()
_JITTER_START = _pr.full_jitter_delay
_pr.full_jitter_delay = lambda retry_number, rng=None: 0.0

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


def reset_spend():
    _spend.SPEND_LEDGER.reset()
    _spend.SPEND_STOP.reset()
    _spend.BILLING_RECORD.clear()
    _spend.BILLING_RECORD.reset_liability()
    _spend.BILLING_RECORD_FAULTS.clear()
    _spend.SPEND_LEDGER_FAULTS.clear()
    _spend.SPEND_ADMISSION_DECLINES.clear()


def new_db(name):
    db = os.path.join(_TMP, name)
    _dl.initialize_database(db)
    return db


def new_run(db):
    return _dl.start_run_record("e1-budget-admission", db_path=db)


def ro_rows(db, sql, params=()):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def install_sink(db, campaign, run_id):
    _spend.BILLING_RECORD.install(_dl.BillingRecordSink(db, campaign, run_id))


def begin(usd, source=_S5):
    return _spend.begin_billed_attempt(source, _WIRE, 1000, 100,
                                       where="e1 test", reserved_usd=usd)


def durable_row(db, campaign, run_id, usd, *, settled=None):
    """A prior liability on ``campaign``: RESERVED at ``usd``, or SETTLED at
    ``settled``, written by ``run_id`` through the real writer."""
    attempt = uuid.uuid4().hex
    _dl.reserve_billing_attempt(db, attempt_id=attempt, campaign_id=campaign,
                                run_id=run_id, source=_S5, model=_WIRE,
                                input_tokens=1000, output_tokens=100,
                                reserved_usd=usd)
    if settled is not None:
        _dl.settle_billing_attempt(db, attempt, outcome="response",
                                   settled_usd=settled)
    return attempt


class _Usage:
    def __init__(self, prompt, completion):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.completion_tokens_details = None


def _chat_response(tokens):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content='{"evaluations": []}',
                                          refusal=None),
            finish_reason="stop")],
        usage=_Usage(*tokens), model=_WIRE)


class _Client:
    def __init__(self):
        self.calls = 0
        self.chat = types.SimpleNamespace(completions=self)
        self.embeddings = types.SimpleNamespace(create=self._embed)

    def create(self, **kwargs):
        self.calls += 1
        return _chat_response((1000, 100))

    def _embed(self, **kwargs):
        self.calls += 1
        return types.SimpleNamespace(
            data=[types.SimpleNamespace(embedding=[0.0])],
            usage=types.SimpleNamespace(prompt_tokens=7),
            model=config.EMBEDDING_MODEL)


_PRICED = _spend.price_usage(_WIRE, 1000, 100)[0]
_S5_BOUND = config.stage5_attempt_bound(
    _WIRE, config.MATCHING_MAX_TOKENS,
    config.matching_sdk_attempts_per_call())["usd"]


# ===========================================================================
section("SECTION 1 -- vocabulary and the in-lock committed figure")
# ===========================================================================

check("1a the durable decline reasons are the in-process ones",
      _dl.BILLING_ADMISSION_DECLINE_REASONS,
      (_spend.ADMISSION_DECLINE_EXHAUSTED, _spend.ADMISSION_DECLINE_HELD))
check("1b SPEND_ADMISSION_DECLINES is on the run-end degradation report",
      "SPEND_ADMISSION_DECLINES" in _degradation.registered_names(), True)
check("1c the shipped sink declares admission support",
      _dl.BillingRecordSink.supports_admission, True)
check("1d non-degeneracy: a Stage 5 attempt's supplied reservation and a "
      "priced response are both positive and far apart",
      (_S5_BOUND > 1.0, 0 < _PRICED < 0.1), (True, True))

reset_spend()
_spend.SPEND_LEDGER.seed(_spend.LedgerSeed(
    usd=2.5, rows=3, runs=1, source=_spend.SEED_SOURCE_BILLING_RECORD))
_spend.SPEND_LEDGER.charge_usd(0.75, _S5)
_spend.SPEND_LEDGER.charge_usd(4.0, _spend.SPEND_SOURCE_RATER)
_spend.SPEND_LEDGER.record_hold("h1", _S5, 3.0)
_keep = set(_spend.BUDGET_SOURCES[_spend.SPEND_BUDGET_CAMPAIGN])
_now = time.monotonic()
check("1e campaign policy: the in-lock committed figure == budget_spend "
      "(seed attributed, the rater's charge excluded, holds excluded)",
      (_spend.SPEND_LEDGER._committed_locked(_keep, False, None, 2.5, _now),
       _spend.budget_spend(_spend.SPEND_BUDGET_CAMPAIGN)), (3.25, 3.25))
_spend.set_policy(_spend.SPEND_POLICY_WINDOW, source="e1 test")
check("1f window policy: the in-lock committed figure == budget_spend "
      "(no seed; the window's charges only)",
      (_spend.SPEND_LEDGER._committed_locked(
          _keep, True, config.SERVING_SPEND_WINDOW_SECONDS, 0.0, _now),
       _spend.budget_spend(_spend.SPEND_BUDGET_CAMPAIGN)), (0.75, 0.75))
check("1f-i an unreadable window falls back to the unwindowed charges, as "
      "window_spend does",
      _spend.SPEND_LEDGER._committed_locked(_keep, True, "bad", 0.0, _now), 0.75)
_spend.reset_policy()
check("1g the open holds are summed per source and reset clears them",
      (_spend.SPEND_LEDGER.held_usd(), _spend.SPEND_LEDGER.held_usd(
          [_spend.SPEND_SOURCE_RATER])), (3.0, 0.0))
reset_spend()
check("1g-i ...reset", _spend.SPEND_LEDGER.held_count(), 0)


# ===========================================================================
section("SECTION 2 -- races with real workers")
# ===========================================================================

def thread_race(n, usd, *, rounds, committed=0.0, db=None):
    """``rounds`` independent rounds: ``n`` threads released together each try
    to admit ``usd``. Returns one ``(admitted, reasons, authorities)`` per
    round. ``db`` selects the durable authority; ``committed`` is prior spend
    (a settled row on another run durably, a charge in process)."""
    out = []
    for r in range(rounds):
        reset_spend()
        if db is not None:
            campaign = f"race-{uuid.uuid4().hex[:8]}"
            if committed:
                durable_row(db, campaign, 9_000_000, committed,
                            settled=committed)
            install_sink(db, campaign, new_run(db))
        elif committed:
            _spend.SPEND_LEDGER.charge_usd(committed, _S5)
        barrier = threading.Barrier(n)
        lock = threading.Lock()
        admitted, reasons, authorities = [], [], []

        def worker():
            barrier.wait()
            try:
                liab = begin(usd)
                with lock:
                    admitted.append(liab)
                    authorities.append(liab.admission_authority)
            except _spend.BudgetAdmissionDeclined as exc:
                with lock:
                    reasons.append(exc.reason)
                    authorities.append(exc.authority)

        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        out.append((len(admitted), sorted(reasons), sorted(set(authorities)),
                    _spend.SPEND_STOP.requested))
        for liab in admitted:
            liab.resolve(_spend.BILLING_OUTCOME_NOT_BILLED)
    return out


with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=10.0):
    _r2a = thread_race(2, 9.0, rounds=40, committed=1.0)
    check("2a *** PROCESS AUTHORITY: two real threads, $1 committed, $9 each, "
          "cap $10 -- in all 40 rounds exactly one is admitted and the other "
          "is declined headroom_held, and a held decline never latches the "
          "run ***",
          sorted(set(map(repr, _r2a))),
          [repr((1, ["headroom_held"], ["process"], False))])
    _r2b = thread_race(8, 9.0, rounds=20)
    check("2b *** eight threads, $9 each, cap $10 -- exactly one admitted per "
          "round ***", sorted({r[0] for r in _r2b}), [1])
    _DBR = new_db("race.db")
    _r2c = thread_race(2, 9.0, rounds=15, committed=1.0, db=_DBR)
    check("2c *** DURABLE AUTHORITY: the same race through BEGIN IMMEDIATE -- "
          "exactly one admitted per round, declined headroom_held ***",
          sorted(set(map(repr, _r2c))),
          [repr((1, ["headroom_held"], ["durable"], False))])
    _r2d = thread_race(8, 9.0, rounds=8, db=_DBR)
    check("2d *** durable, eight threads -- exactly one admitted per round ***",
          sorted({r[0] for r in _r2d}), [1])
    _worst = ro_rows(_DBR, "SELECT MAX(t) FROM (SELECT campaign_id, "
                           "SUM(COALESCE(settled_usd, reserved_usd)) AS t "
                           "FROM billing_attempts GROUP BY campaign_id)")[0][0]
    check("2e no campaign's durable liabilities ever exceeded its $10 cap",
          _worst is not None and _worst <= 10.0 + _TOL, True)
reset_spend()

_CHILD = os.path.join(_TMP, "admission_child.py")
Path(_CHILD).write_text(r'''
import json, os, signal, sys, threading, time, types
cfg = json.loads(sys.argv[1])
sys.path.insert(0, cfg["repo"]); sys.path.insert(0, cfg["tests"])
os.environ["ONCOTRIAGE_DEFER_LOCAL_MODELS"] = "1"
import _control_harness
_control_harness.isolate_qdrant(os.environ)
from oncotriage import config, paths, spend, run_fingerprint
import _provider_pin
_provider_pin.pin_openai_arm("admission-child", out=lambda *a, **k: None)
from oncotriage import provider_resilience as pr
from oncotriage.agent import deps, evaluation as ev
from oncotriage.storage import database_logger as dl
pr.full_jitter_delay = lambda retry_number, rng=None: 0.0
config.SPEND_CAP_ENFORCED = True
config.SPEND_CAP_USD = cfg["cap"]
WIRE = config.matching_wire_model()
MODE = cfg["mode"]

def dump(**kw):
    with open(cfg["out"], "w") as fh:
        json.dump(kw, fh)

class Usage:
    def __init__(self, p, c):
        self.prompt_tokens, self.completion_tokens = p, c
        self.completion_tokens_details = None

class Client:
    def __init__(self, die=False):
        self.calls, self.die = 0, die
        self.chat = types.SimpleNamespace(completions=self)
    def create(self, **kw):
        if self.die:
            os.kill(os.getpid(), signal.SIGKILL)
        self.calls += 1
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(
                content="{}", refusal=None), finish_reason="stop")],
            usage=Usage(1000, 100), model=WIRE)

if MODE in ("race", "die_before_commit", "die_after_commit"):
    spend.BILLING_RECORD.install(dl.BillingRecordSink(
        cfg["db"], cfg["campaign"], cfg["run_id"]))

if MODE == "race":
    open(cfg["ready"], "w").close()
    while not os.path.exists(cfg["go"]):
        time.sleep(0.0005)
    try:
        liab = spend.begin_billed_attempt("stage5", WIRE, 1000, 100,
                                          where="e1 race child",
                                          reserved_usd=cfg["usd"])
        dump(admitted=True, authority=liab.admission_authority)
    except spend.BudgetAdmissionDeclined as exc:
        dump(admitted=False, reason=exc.reason, authority=exc.authority,
             committed=exc.committed_usd, held=exc.held_usd)
    sys.exit(0)

if MODE == "die_before_commit":
    real_open = dl._open_billing_connection
    class Dying:
        def __init__(self, conn):
            self._conn, self._inserted = conn, False
            conn.set_trace_callback(self._trace)
        def _trace(self, sql):
            if sql.strip().upper().startswith("INSERT OR IGNORE INTO BILLING_ATTEMPTS"):
                self._inserted = True
        def cursor(self):
            return self._conn.cursor()
        def rollback(self):
            return self._conn.rollback()
        def close(self):
            return self._conn.close()
        def commit(self):
            if self._inserted:
                with open(cfg["marker"], "w") as fh:
                    fh.write("inserted, not committed")
                os.kill(os.getpid(), signal.SIGKILL)
            return self._conn.commit()
    dl._open_billing_connection = lambda db_path: Dying(real_open(db_path))
    spend.begin_billed_attempt("stage5", WIRE, 1000, 100,
                               where="e1 die before commit",
                               reserved_usd=cfg["usd"])
    dump(reached="after begin -- the kill did not happen")
    sys.exit(3)

if MODE == "die_after_commit":
    deps.set_override(deps.OPENAI_CLIENT, Client(die=True))
    ev.call_matching_model("system prompt", "user prompt")
    dump(reached="after the call -- the kill did not happen")
    sys.exit(3)

# ---- the REAL main() ----
from oncotriage.batch import runner
paths._RESOLVED["data_fhir_path"] = cfg["corpus"] + os.sep
paths._RESOLVED["inferences_path"] = cfg["db"]
paths._RESOLVED["checkpoint_path"] = cfg["cp"] + os.sep
FIXED = cfg["fingerprint"]
run_fingerprint.current = lambda *a, **k: dict(FIXED)
CLIENT = Client()
deps.set_override(deps.OPENAI_CLIENT, CLIENT)

class Tracking:
    def start_run(self, **k): pass
    def log_run_metrics(self, *a, **k): pass
    def end_run(self, **k): pass

runner.build_bm25_index_from_qdrant = lambda *a, **k: (object(), ["NCT1"])
runner.build_matching_graph = lambda *a, **k: object()
runner.tracking = Tracking()
runner.run_resample = lambda **k: None
ONCE, LOCK, SEEN = {"done": False}, threading.Lock(), {}

def patient(fhir_path=None, graph=None, is_resample=False, run_id=None,
            db_path=None):
    go = True
    if MODE == "runner_boundary":
        with LOCK:
            go = not ONCE["done"]
            ONCE["done"] = True
    if go:
        with LOCK:
            SEEN.setdefault("seed", spend.SPEND_LEDGER.seeded._asdict())
        try:
            ev.call_matching_model("system prompt", "user prompt")
        except Exception as exc:
            with LOCK:
                SEEN.setdefault("errors", []).append(str(exc))
    return {"patient_id": os.path.basename(str(fhir_path)), "status": "error",
            "eligible_matches": 0, "near_misses": 0, "not_evaluable": 0,
            "total_time": 0.01, "timestamp": "2026-09-14T00:00:00",
            "error": "planted by the harness", "is_resample": False}

runner.process_patient = patient
EXIT = 0
try:
    runner.main()
except SystemExit as stop:
    EXIT = stop.code
dump(exit_code=EXIT, calls=CLIENT.calls, seen=SEEN,
     latch=[spend.SPEND_STOP.requested, spend.SPEND_STOP.limit],
     declines=dict(spend.SPEND_ADMISSION_DECLINES),
     report=spend.report_lines(),
     campaign_id=(json.load(open(os.path.join(cfg["cp"],
                  runner.CAMPAIGN_RECORD_FILENAME)))["campaign_id"]
                  if os.path.exists(os.path.join(cfg["cp"],
                  runner.CAMPAIGN_RECORD_FILENAME)) else None))
''', encoding="utf-8")


def child_env():
    env = dict(os.environ)
    _control_harness.isolate_qdrant(env)
    env["ONCOTRIAGE_DEFER_LOCAL_MODELS"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def spawn(cfg):
    base = {"repo": _REPO, "tests": _TESTS, "cap": 10.0}
    base.update(cfg)
    return subprocess.Popen([sys.executable, _CHILD, json.dumps(base)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=child_env(), cwd=_TMP)


def run_child(cfg, timeout=240):
    proc = spawn(cfg)
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        output, _ = proc.communicate()
    out = cfg.get("out")
    data = (json.loads(Path(out).read_text())
            if out and os.path.exists(out) else None)
    return proc.returncode, data, output


def process_race(db, *, n, usd, cap, rounds):
    results = []
    for r in range(rounds):
        campaign = f"xrace-{uuid.uuid4().hex[:8]}"
        rdir = os.path.join(_TMP, campaign)
        os.makedirs(rdir)
        go = os.path.join(rdir, "go")
        procs, outs = [], []
        for i in range(n):
            out = os.path.join(rdir, f"out{i}.json")
            outs.append(out)
            procs.append(spawn({"mode": "race", "db": db, "campaign": campaign,
                                "run_id": new_run(db), "cap": cap, "usd": usd,
                                "ready": os.path.join(rdir, f"ready{i}"),
                                "go": go, "out": out}))
        deadline = time.monotonic() + 120
        while (time.monotonic() < deadline and not all(
                os.path.exists(os.path.join(rdir, f"ready{i}"))
                for i in range(n))):
            time.sleep(0.01)
        Path(go).write_text("go")
        for proc in procs:
            try:
                proc.communicate(timeout=120)
            except subprocess.TimeoutExpired:
                proc.kill()
        data = [json.loads(Path(o).read_text()) if os.path.exists(o) else None
                for o in outs]
        rows = ro_rows(db, "SELECT COUNT(*), COALESCE(SUM(reserved_usd), 0) "
                           "FROM billing_attempts WHERE campaign_id = ?",
                       (campaign,))[0]
        results.append((sum(1 for d in data if d and d.get("admitted")),
                        sorted(d.get("reason") for d in data
                               if d and not d.get("admitted")),
                        rows))
    return results


_DBX = new_db("xrace.db")
_r2f = process_race(_DBX, n=4, usd=9.0, cap=10.0, rounds=3)
check("2f *** CROSS-PROCESS: four child processes, one database, one campaign, "
      "$9 each against $10, released together -- exactly one admitted, three "
      "declined budget_exhausted (a sibling's reservation is committed to "
      "them), one row of $9 ***",
      _r2f, [(1, ["budget_exhausted"] * 3, (1, 9.0))] * 3)


# ===========================================================================
section("SECTION 3 -- a full wave admitted, then released")
# ===========================================================================

def wave(n, usd, *, db=None):
    """Admit ``n`` attempts with real threads; return the liabilities."""
    barrier = threading.Barrier(n)
    lock = threading.Lock()
    got = []

    def worker():
        barrier.wait()
        try:
            liab = begin(usd)
            with lock:
                got.append(liab)
        except _spend.BudgetAdmissionDeclined:
            pass

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return got


def wave_scenario(label, db):
    reset_spend()
    if db is not None:
        install_sink(db, f"wave-{uuid.uuid4().hex[:8]}", new_run(db))
    cap = 24 * 9.0
    with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=cap):
        first = wave(24, 9.0)
        check(f"3a[{label}] *** 24 real threads x $9 against a $216 cap: all "
              f"24 admitted (equality is admitted) ***", len(first), 24)
        extra = raised(begin, 9.0)
        check(f"3b[{label}] a 25th is declined headroom_held with $216 held",
              (type(extra).__name__, getattr(extra, "reason", None),
               near(getattr(extra, "held_usd", None) or 0.0, 216.0)
               if db is None else True),
              ("BudgetAdmissionDeclined", "headroom_held", True))
        for i, liab in enumerate(first):
            if i % 2:
                liab.resolve(_spend.BILLING_OUTCOME_RESPONSE, model=_WIRE,
                             prompt_tokens=1000, completion_tokens=100)
            else:
                liab.resolve(_spend.BILLING_OUTCOME_NOT_BILLED)
        check(f"3c[{label}] *** every hold released and exactly the 12 priced "
              f"responses charged ***",
              (_spend.SPEND_LEDGER.held_count(),
               near(_spend.SPEND_LEDGER.measured, 12 * _PRICED)), (0, True))
        second = wave(24, 9.0)
        expected = int((cap - 12 * _PRICED + _TOL) // 9.0)
        check(f"3d[{label}] *** HELD HEADROOM RETURNS, ONLY THE UNUSED "
              f"DIFFERENCE: the second wave admits floor((216 - 12 x "
              f"${_PRICED:.4f}) / 9) = {expected} ***",
              (len(second), expected), (23, 23))
        for liab in second:
            liab.resolve(_spend.BILLING_OUTCOME_NOT_BILLED)
    reset_spend()


wave_scenario("process", None)
wave_scenario("durable", new_db("wave.db"))


# ===========================================================================
section("SECTION 4 -- a resumed balance plus live reservations, counted once")
# ===========================================================================

def resume_scenario(label, durable):
    reset_spend()
    db = new_db(f"resume-{label}.db")
    campaign = f"resume-{label}"
    run_a = new_run(db)
    durable_row(db, campaign, run_a, 4.0, settled=2.0)
    durable_row(db, campaign, run_a, 3.0)
    prior = _dl.campaign_billing_total(campaign, db_path=db)
    check(f"4a[{label}] non-degeneracy: the prior run left $2 settled and $3 "
          f"reserved (a dead process), total $5",
          (prior.usd, prior.unresolved, prior.unresolved_usd), (5.0, 1, 3.0))
    _spend.SPEND_LEDGER.seed(_spend.LedgerSeed(
        usd=prior.usd, rows=prior.attempts, runs=1,
        source=_spend.SEED_SOURCE_BILLING_RECORD, unresolved=prior.unresolved))
    run_b = new_run(db)
    if durable:
        install_sink(db, campaign, run_b)
    with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=10.0):
        # DRIVEN, NOT CALLED: a defect that declines this admission must be a
        # recorded failure below, not a traceback that hides every later check
        # (the P5a control aborted the file here before this guard).
        mine = drive(begin, 1.0)
        check(f"4b[{label}] run B holds $1 of its own",
              getattr(mine, "admission_authority", mine),
              "durable" if durable else "process")
        at_cap = drive(begin, 4.0)
        check(f"4c[{label}] *** $5 resumed + $1 held + $4 = $10 exactly: "
              f"ADMITTED. Counting the resumed balance twice would decline "
              f"it ***", isinstance(at_cap, _spend.AttemptLiability), True)
        if isinstance(at_cap, _spend.AttemptLiability):
            at_cap.resolve(_spend.BILLING_OUTCOME_NOT_BILLED)
        over = raised(begin, 4.000001)
        check(f"4d[{label}] ...$4.000001 is declined headroom_held, reporting "
              f"$5 committed and $1 held -- not $10 committed",
              (getattr(over, "reason", None),
               near(getattr(over, "committed_usd", None), 5.0),
               near(getattr(over, "held_usd", None), 1.0)),
              ("headroom_held", True, True))
        exhausted = raised(begin, 5.000001)
        check(f"4e[{label}] ...$5.000001 is budget_exhausted and latches the "
              f"run at spend_cap",
              (getattr(exhausted, "reason", None), _spend.SPEND_STOP.limit),
              ("budget_exhausted", _spend.SPEND_LIMIT_CAP))
        _spend.SPEND_STOP.reset()
        if isinstance(mine, _spend.AttemptLiability):
            mine.resolve(_spend.BILLING_OUTCOME_RESPONSE, model=_WIRE,
                         prompt_tokens=1000, completion_tokens=100)
        rest = drive(begin, 5.0 - _PRICED)
        check(f"4f[{label}] *** after run B's $1 settles at "
              f"${_PRICED:.4f}, exactly $5 - ${_PRICED:.4f} fits ***",
              isinstance(rest, _spend.AttemptLiability), True)
        if isinstance(rest, _spend.AttemptLiability):
            rest.resolve(_spend.BILLING_OUTCOME_NOT_BILLED)
    reset_spend()


resume_scenario("process", False)
resume_scenario("durable", True)


# ===========================================================================
section("SECTION 5 -- the boundary through Stage 5's public entry point")
# ===========================================================================

_DBB = new_db("boundary.db")


def boundary(label, cap):
    reset_spend()
    campaign = f"boundary-{label}"
    install_sink(_DBB, campaign, new_run(_DBB))
    client = _Client()
    deps.set_override(deps.OPENAI_CLIENT, client)
    try:
        with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=cap):
            exc = raised(_ev.call_matching_model, "system prompt", "user")
    finally:
        deps.clear_override(deps.OPENAI_CLIENT)
    rows = ro_rows(_DBB, "SELECT COUNT(*), COALESCE(SUM(reserved_usd), 0) FROM "
                         "billing_attempts WHERE campaign_id = ?", (campaign,))[0]
    return dict(exc=exc, calls=client.calls, rows=rows,
                declines=dict(_spend.SPEND_ADMISSION_DECLINES),
                latch=_spend.SPEND_STOP.limit)


_below = boundary("below", _S5_BOUND + 1e-6)
_equal = boundary("equal", _S5_BOUND)
_above = boundary("above", _S5_BOUND - 1e-6)
check("5a *** JUST BELOW the cap: dispatched, one provider call, one row at the "
      "supplied reservation ***",
      (_below["exc"], _below["calls"], _below["rows"][0],
       near(_below["rows"][1], _S5_BOUND)), (None, 1, 1, True))
check("5b *** EXACTLY AT the cap: dispatched (equality is admitted) ***",
      (_equal["exc"], _equal["calls"], _equal["rows"][0]), (None, 1, 1))
check("5c *** JUST ABOVE the cap: ZERO provider calls, NO row, a "
      "Stage5SpendStopped naming the admission decline ***",
      (type(_above["exc"]).__name__, _above["calls"], _above["rows"][0],
       "budget admission declined it [budget_exhausted, durable authority]"
       in str(_above["exc"])),
      ("Stage5SpendStopped", 0, 0, True))
check("5d ...counted once under stage5:budget_exhausted and latched at "
      "spend_cap; a Stage5SpendStopped is a shutdown subclass, so "
      "_account_unconsumed never counts it abandoned",
      (_above["declines"], _above["latch"],
       isinstance(_above["exc"], _ev.Stage5ShutdownRequested)),
      ({"stage5:budget_exhausted": 1}, _spend.SPEND_LIMIT_CAP, True))
_report = "\n".join(str(x) for x in drive(
    lambda: [line for name in ["SPEND_ADMISSION_DECLINES"]
             for line in _degradation.report_lines(_degradation.snapshot())
             if name in str(line)]) or [])
check("5e the run-end degradation report names the decline",
      "SPEND_ADMISSION_DECLINES" in _report, True)
reset_spend()


# ===========================================================================
section("SECTION 6 -- settle once, release once")
# ===========================================================================

def concurrent_resolve(db):
    reset_spend()
    if db is not None:
        install_sink(db, f"once-{uuid.uuid4().hex[:8]}", new_run(db))
    with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=100.0):
        liab = drive(begin, 9.0)
        if not isinstance(liab, _spend.AttemptLiability):
            return dict(values=[repr(liab)], measured=None, charges=None,
                        held=None, resolved=None, open=None, outcome=None,
                        conflicts=["admission failed: " + repr(liab)])
        barrier = threading.Barrier(9)
        outcomes = [_spend.BILLING_OUTCOME_RESPONSE,
                    _spend.BILLING_OUTCOME_POSSIBLY_BILLED,
                    _spend.BILLING_OUTCOME_NOT_BILLED] * 3
        got, lock = [], threading.Lock()

        def worker(outcome):
            barrier.wait()
            value = liab.resolve(outcome, model=_WIRE, prompt_tokens=1000,
                                 completion_tokens=100)
            with lock:
                got.append(value)

        threads = [threading.Thread(target=worker, args=(o,)) for o in outcomes]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    snap = _spend.BILLING_RECORD.liability_snapshot()
    resolved = sum(v[0] for k, v in snap.items() if k != _spend.LIABILITY_OPEN)
    return dict(values=sorted(set(got)), measured=_spend.SPEND_LEDGER.measured,
                charges=_spend.SPEND_LEDGER.calls, held=_spend.SPEND_LEDGER
                .held_count(), resolved=resolved,
                open=snap.get(_spend.LIABILITY_OPEN, (0, 0.0))[0],
                outcome=liab.resolved_outcome,
                conflicts=[k for k in _spend.BILLING_RECORD_FAULTS
                           if "conflict" in k or "discrepancy" in k])


for _label, _db in (("process", None), ("durable", new_db("once.db"))):
    _c = concurrent_resolve(_db)
    _expect_charge = {_spend.BILLING_OUTCOME_RESPONSE: (_PRICED, 1),
                      _spend.BILLING_OUTCOME_POSSIBLY_BILLED: (9.0, 1),
                      _spend.BILLING_OUTCOME_NOT_BILLED: (0.0, 0)}.get(
        _c["outcome"], (None, None))
    check(f"6a[{_label}] *** nine threads resolve one attempt with three "
          f"different outcomes at once: every caller reads ONE amount, the "
          f"ledger holds exactly the first outcome's charge, one resolution, "
          f"no hold left, no settlement conflict ***",
          (len(_c["values"]), near(_c["measured"], _expect_charge[0]),
           _c["charges"], _c["resolved"], _c["open"], _c["held"],
           _c["conflicts"]),
          (1, True, _expect_charge[1], 1, 0, 0, []))

reset_spend()
with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=10.0):
    _a, _b = drive(begin, 3.0), drive(begin, 4.0)
    if (isinstance(_a, _spend.AttemptLiability)
            and isinstance(_b, _spend.AttemptLiability)):
        _b.resolve(_spend.BILLING_OUTCOME_NOT_BILLED)
        _a.resolve(_spend.BILLING_OUTCOME_POSSIBLY_BILLED)
        _again = _a.resolve(_spend.BILLING_OUTCOME_RESPONSE, model=_WIRE,
                            prompt_tokens=1000, completion_tokens=100)
        _b_again = _b.resolve(_spend.BILLING_OUTCOME_POSSIBLY_BILLED)
        _released_again = _spend.SPEND_LEDGER.release_hold(_a._hold)
    else:
        _again = _b_again = _released_again = _Absent(f"{_a!r} / {_b!r}")
    check("6b *** out of order and duplicated: B then A then A again then B "
          "again -- A charged $3 once, B nothing, both holds released once ***",
          (_again, _b_again, _spend.SPEND_LEDGER.measured,
           _spend.SPEND_LEDGER.calls, _spend.SPEND_LEDGER.held_count(),
           _released_again),
          (3.0, 0.0, 3.0, 1, 0, False))
    _full = drive(begin, 7.0)
    check("6c ...and exactly the released headroom is available again: $3 "
          "committed + $7 = $10 admitted",
          isinstance(_full, _spend.AttemptLiability), True)
reset_spend()


# ===========================================================================
section("SECTION 7 -- death before commit, death after commit (fresh processes)")
# ===========================================================================

_DBD = new_db("death.db")
_camp_d1 = "death-before-commit"
_marker = os.path.join(_TMP, "inserted.marker")
_rc, _data, _log = run_child({"mode": "die_before_commit", "db": _DBD,
                              "campaign": _camp_d1, "run_id": new_run(_DBD),
                              "cap": 10.0, "usd": 9.0, "marker": _marker,
                              "out": os.path.join(_TMP, "d1.json")})
check("7a non-degeneracy: the child executed the INSERT inside the admission "
      "transaction and was SIGKILLed at its commit",
      (_rc, os.path.exists(_marker), _data), (-9, True, None))
check("7b *** AN UNCOMMITTED HOLD DISAPPEARS: no row survives the kill ***",
      ro_rows(_DBD, "SELECT COUNT(*) FROM billing_attempts WHERE campaign_id=?",
              (_camp_d1,))[0][0], 0)
reset_spend()
install_sink(_DBD, _camp_d1, new_run(_DBD))
with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=10.0):
    _fresh = drive(begin, 10.0)
check("7c *** ...and a fresh process admits the full $10 against that "
      "campaign ***", isinstance(_fresh, _spend.AttemptLiability), True)
reset_spend()

_camp_d2 = "death-after-commit"
_rc2, _data2, _log2 = run_child({"mode": "die_after_commit", "db": _DBD,
                                 "campaign": _camp_d2, "run_id": new_run(_DBD),
                                 "cap": 1000.0,
                                 "out": os.path.join(_TMP, "d2.json")})
_rows2 = ro_rows(_DBD, "SELECT state, reserved_usd FROM billing_attempts "
                       "WHERE campaign_id = ?", (_camp_d2,))
check("7d non-degeneracy: the child was SIGKILLed inside the provider call, "
      "after its reservation committed", (_rc2, _data2, len(_rows2)),
      (-9, None, 1))
check("7e *** A COMMITTED RESERVATION IS NOT RELEASED BY DEATH: the row is "
      "still RESERVED at the supplied bound ***",
      (_rows2[0][0] if _rows2 else None,
       near(_rows2[0][1], _S5_BOUND) if _rows2 else False), ("reserved", True))
reset_spend()
install_sink(_DBD, _camp_d2, new_run(_DBD))
with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=_S5_BOUND + 1.0):
    _over = raised(begin, 1.0 + 1e-6)
    _fits = drive(begin, 1.0)
check("7f *** ...a fresh process counts it: $1.000001 more is budget_exhausted, "
      "$1 exactly fits ***",
      (getattr(_over, "reason", None), near(getattr(_over, "committed_usd",
                                                     None), _S5_BOUND),
       isinstance(_fits, _spend.AttemptLiability)),
      ("budget_exhausted", True, True))
reset_spend()


# ===========================================================================
section("SECTION 8 -- the operator surface and a clean control, REAL main()")
# ===========================================================================

_CORPUS = os.path.join(_TMP, "corpus")
os.makedirs(_CORPUS)
for _i in range(2):
    Path(os.path.join(_CORPUS, f"patient{_i}.json")).write_text(
        json.dumps({"resourceType": "Bundle", "entry": []}))


def runner_child(mode, tag, cap):
    db = os.path.join(_TMP, f"{tag}.db")
    cp = os.path.join(_TMP, f"cp-{tag}")
    os.makedirs(cp, exist_ok=True)
    out = os.path.join(cp, f"{mode}.json")
    if os.path.exists(out):
        os.remove(out)
    return run_child({"mode": mode, "db": db, "cp": cp, "corpus": _CORPUS,
                      "cap": cap, "out": out, "fingerprint": FIXED_FP}) + (db,)


_rc8a, _d8a, _o8a, _db8a = runner_child("runner_boundary", "rb-equal", _S5_BOUND)
_rc8b, _d8b, _o8b, _db8b = runner_child("runner_boundary", "rb-above",
                                        _S5_BOUND - 1e-6)
check("8a *** main(), cap EXACTLY the supplied reservation: the one attempt is "
      "dispatched ***",
      (_rc8a, at(_d8a, "calls"), at(_d8a, "latch"),
       ro_rows(_db8a, "SELECT COUNT(*) FROM billing_attempts")[0][0]),
      (0, 1, [False, None], 1))
check("8b *** main(), cap $0.000001 below it: ZERO provider calls, no billing "
      "row, the run latched at spend_cap, the decline counted ***",
      (at(_d8b, "calls"), at(_d8b, "latch"), at(_d8b, "declines"),
       ro_rows(_db8b, "SELECT COUNT(*) FROM billing_attempts")[0][0]),
      (0, [True, _spend.SPEND_LIMIT_CAP], {"stage5:budget_exhausted": 1}, 0))
check("8c ...the operator reads the decline: the patient's error names it and "
      "the run row's stop_reason is spend_cap",
      ("budget admission declined it [budget_exhausted, durable authority]"
       in " ".join(at(at(_d8b, "seen"), "errors") or []),
       [r[0] for r in ro_rows(_db8b, "SELECT stop_reason FROM runs")]),
      (True, ["spend_cap"]))

_rc8c, _d8c, _o8c, _db8c = runner_child("runner_clean", "clean", 100.0)
_camp8 = at(_d8c, "campaign_id")
_rows8c = ro_rows(_db8c, "SELECT state, outcome FROM billing_attempts")
check("8d *** CLEAN CONTROL: two patients dispatch and complete -- two calls, "
      "two rows settled at their responses, no decline ***",
      (_rc8c, at(_d8c, "calls"), sorted(_rows8c), at(_d8c, "declines")),
      (0, 2, [("settled", "response")] * 2, {}))
_rc8d, _d8d, _o8d, _db8d = runner_child("runner_clean", "clean", 100.0)
_total8 = drive(_dl.campaign_billing_total, _camp8, db_path=_db8c)
check("8e *** ...and RESUME: the second invocation continues the same campaign, "
      "is seeded with the first's durable total, and dispatches again ***",
      (_rc8d, at(_d8d, "campaign_id") == _camp8, at(_d8d, "calls"),
       near(at(at(at(_d8d, "seen"), "seed"), "usd"), 2 * _PRICED),
       getattr(_total8, "attempts", None)),
      (0, True, 2, True, 4))


# ===========================================================================
section("SECTION 9 -- preservation")
# ===========================================================================

reset_spend()
_emb = _Client()
deps.set_override(deps.OPENAI_CLIENT, _emb)
try:
    with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=10.0):
        _spend.SPEND_LEDGER.mark_unverified(_spend.SPEND_BUDGET_CAMPAIGN,
                                            ["an unreadable test row"])
        _unv = raised(_models.get_embedding, "some text")
finally:
    deps.clear_override(deps.OPENAI_CLIENT)
check("9a *** an unverified record still refuses FIRST: the embedding raises "
      "SpendRecordUnverified before admission -- no hold, no decline, no call ***",
      (type(_unv).__name__, _spend.SPEND_LEDGER.held_count(),
       dict(_spend.SPEND_ADMISSION_DECLINES), _emb.calls),
      ("SpendRecordUnverified", 0, {}, 0))
reset_spend()


class _RaisingSink:
    def reserve(self, **fields):
        raise RuntimeError("the database went away")


_spend.BILLING_RECORD.install(_RaisingSink())
with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=10.0):
    _unav = raised(begin, 9.0)
check("9b *** a reservation that fails AFTER admission releases its hold, and "
      "the billing-record latch is unchanged ***",
      (type(_unav).__name__, _spend.SPEND_LEDGER.held_count(),
       _spend.SPEND_STOP.limit),
      ("BillingRecordUnavailable", 0, _spend.SPEND_LIMIT_BILLING_RECORD))
reset_spend()

_s9 = _Client()
deps.set_override(deps.OPENAI_CLIENT, _s9)
try:
    with settings(SPEND_CAP_ENFORCED=True, SPEND_CAP_USD=_S5_BOUND - 1e-6):
        _decl = raised(_ev.call_matching_model, "system prompt", "user")
finally:
    deps.clear_override(deps.OPENAI_CLIENT)
_snap9 = _spend.BILLING_RECORD.liability_snapshot()
check("9c *** a declined request is never issued and never counted as "
      "abandoned: no call, no liability opened or resolved ***",
      (type(_decl).__name__, _s9.calls, _snap9), ("Stage5SpendStopped", 0, {}))
reset_spend()

_spend.set_policy(_spend.SPEND_POLICY_WINDOW, source="e1 test")
try:
    with settings(SPEND_CAP_ENFORCED=True, SERVING_SPEND_CAP_USD=10.0):
        _w1 = drive(begin, 9.0)
        _w2 = raised(begin, 9.0)
        if isinstance(_w1, _spend.AttemptLiability):
            _w1.resolve(_spend.BILLING_OUTCOME_NOT_BILLED)
        _spend.SPEND_LEDGER.charge_usd(5.0, _S5)
        _w3 = raised(begin, 6.0)
        check("9d *** THE ROLLING WINDOW NEVER LATCHES: held and exhausted "
              "declines both leave SPEND_STOP clear ***",
              (getattr(_w2, "reason", None), getattr(_w3, "reason", None),
               _spend.SPEND_STOP.requested),
              ("headroom_held", "budget_exhausted", False))
finally:
    _spend.reset_policy()
reset_spend()

with settings(SPEND_CAP_ENFORCED=False, SPEND_CAP_USD=1.0):
    _off = [drive(begin, 9.0) for _ in range(3)]
check("9e measurement mode (SPEND_CAP_ENFORCED False) admits everything and "
      "still tracks the holds",
      (sum(isinstance(x, _spend.AttemptLiability) for x in _off),
       _spend.SPEND_LEDGER.held_count()), (3, 3))
reset_spend()


# ===========================================================================
section("SECTION 10 -- the SQL summation equals campaign_billing_total")
# ===========================================================================

_DBQ = new_db("shapes.db")
_SHAPES = {
    "settled": ("attempt", "settled", 1.0, 0.5, None),
    "reserved": ("attempt", "reserved", 2.0, None, None),
    "historical": ("historical_evidence", "settled", 3.0, 3.0, None),
    "disc_failed": ("settlement_discrepancy", "settled", 0.7, 0.7,
                    '{"result": "failed"}'),
    "disc_conflict": ("settlement_discrepancy", "settled", 0.2, 0.2,
                      '{"result": "conflict"}'),
    "disc_missing": ("settlement_discrepancy", "settled", 0.3, 0.3,
                     '{"result": "missing"}'),
    "disc_bad_json": ("settlement_discrepancy", "settled", 0.3, 0.3, "not json"),
    "disc_array": ("settlement_discrepancy", "settled", 0.3, 0.3, "[1]"),
    "disc_null_note": ("settlement_discrepancy", "settled", 0.3, 0.3, None),
    "disc_reserved": ("settlement_discrepancy", "reserved", 0.3, None,
                      '{"result": "failed"}'),
    "text_amount": ("attempt", "settled", 1.0, "1.5", None),
    "negative": ("attempt", "settled", 1.0, -1.0, None),
    "settled_null": ("attempt", "settled", 1.0, None, None),
    "unknown_state": ("attempt", "weird", 1.0, 1.0, None),
    "unknown_kind": ("weird", "settled", 1.0, 1.0, None),
    "infinite": ("attempt", "reserved", float("inf"), None, None),
}
_conn = sqlite3.connect(_DBQ)
for _name, (_kind, _state, _res, _set, _note) in _SHAPES.items():
    for _rowkind, _rowstate, _rowres, _rowset, _rownote in (
            (_kind, _state, _res, _set, _note),
            ("attempt", "settled", 1.25, 1.25, None)):
        _conn.execute(
            "INSERT INTO billing_attempts (attempt_id, campaign_id, run_id, "
            "kind, source, state, reserved_usd, settled_usd, reserved_at, "
            "note) VALUES (?, ?, 1, ?, 'stage5', ?, ?, ?, 'now', ?)",
            (uuid.uuid4().hex, _name, _rowkind, _rowstate, _rowres, _rowset,
             _rownote))
_conn.commit()
_conn.close()


def _python_reading(campaign):
    try:
        return ("ok", round(_dl.campaign_billing_total(campaign,
                                                       db_path=_DBQ).usd, 9))
    except _dl.BillingRecordIncomplete:
        return ("incomplete",)
    except _dl.BillingRecordUnreadable:
        return ("unreadable",)


def _sql_reading(campaign):
    conn = sqlite3.connect(f"file:{_DBQ}?mode=ro", uri=True)
    try:
        liab = _dl.campaign_liabilities(conn.cursor(), campaign, -1)
    finally:
        conn.close()
    if liab.bad:
        return ("unreadable",)
    if liab.incomplete:
        return ("incomplete",)
    return ("ok", round(liab.committed_usd + liab.held_usd, 9))


_py = {n: _python_reading(n) for n in _SHAPES}
_sq = {n: _sql_reading(n) for n in _SHAPES}
check("10a *** for every row shape, the admission SQL reads what "
      "campaign_billing_total reads ***", _sq, _py)
check("10a-i non-degeneracy: the shapes reach all three readings",
      sorted({v[0] for v in _py.values()}), ["incomplete", "ok", "unreadable"])
_conn = sqlite3.connect(f"file:{_DBQ}?mode=ro", uri=True)
try:
    _split = _dl.campaign_liabilities(_conn.cursor(), "reserved", 1)
finally:
    _conn.close()
check("10b the split: a reserved row of THIS run is held, the settled row is "
      "committed", (_split.committed_usd, _split.held_usd), (1.25, 2.0))


# ===========================================================================
section("SECTION 11 -- isolation held")
# ===========================================================================

_pr.full_jitter_delay = _JITTER_START
check("11a every config knob is restored",
      {k: getattr(config, k) for k in _CONFIG_KEYS}, _CONFIG_START)
check("11b no billing sink is left installed and the policy is restored",
      (_spend.BILLING_RECORD.installed_sink(), _spend.policy()),
      (None, _POLICY_START))
shutil.rmtree(_TMP, ignore_errors=True)
check("11c the scratch tree is gone", os.path.exists(_TMP), False)
_who, _prev, _restored = _provider_pin.release_openai_arm()
check("11d the provider pin is released", _restored, True)

print("\n" + "=" * 78)
print(f"RESULTS: {_RESULTS['passed']} passed, {_RESULTS['failed']} failed")
print("=" * 78)
if _FAILURES:
    for _f in _FAILURES:
        print(f"  - {_f}")
sys.exit(1 if _RESULTS["failed"] else 0)


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Sep 14 2026

@author: ramyalsaffar
"""
