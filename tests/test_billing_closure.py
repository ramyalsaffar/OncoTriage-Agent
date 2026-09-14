# Billing Closure Test
######################

"""The billing closure pass: ledger parity, counter proof, campaign identity,
durability.

WHAT THIS FILE HOLDS
--------------------
    1. P1 -- ONE LIABILITY RULE. For every possibly-billed failure class (and the
       three that are not failures), the in-process ledger and the durable
       billing record charge the SAME amount, so a live process's remaining
       budget and a resumed process's are equal. Driven through the real
       ``call_matching_model`` and the real ``get_embedding``.
    2. P2 -- the counter registry. A flush records which counters it consulted;
       historical reuse refuses by name when that record does not prove each
       required counter was registered.
    3. P3 -- one campaign identity in runs, summaries and billing. A zero-success
       restart continuing a budget is ONE campaign in ``campaign_summary``;
       ``campaign_parent_map`` and the SQL stitch agree on every run.
    4. P4 -- durability. Every billing write's connection sets and reads back
       ``synchronous`` FULL (and ``fullfsync`` on darwin) before its
       transaction; the identity file is synced in the right order; a lost or
       corrupt identity file is recovered only where the rows establish it.
    5. END TO END through the REAL ``main()`` in child processes: one synthetic
       campaign, then a fresh process resuming it; live and resumed figures
       reconciled per outcome, settled and unresolved separately, printed lines
       compared, summary grouping read.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO MODEL LOAD, NO CORPUS. Every
provider client is a stand-in installed through ``oncotriage/agent/deps.py``;
children get a closed Qdrant port and the same stand-ins; every database and
checkpoint directory is inside a ``tempfile.mkdtemp`` removed at the end and
asserted gone. The production ``inferences.db`` digest is compared at the end.
It EXECS NOTHING and loads no module by location: children are SCRIPTS written
into the temp tree and run with ``sys.executable``. NOT IN THE COLLISION MATRIX.

Run from terminal:
    python tests/test_billing_closure.py
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
import fcntl
import hashlib
import json
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import types
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _control_harness                                          # noqa: E402

_control_harness.isolate_qdrant(os.environ)

from oncotriage import config                                    # noqa: E402
from oncotriage import degradation as _degradation               # noqa: E402
from oncotriage import paths as _paths                           # noqa: E402
from oncotriage import provider_resilience as _pr                # noqa: E402
from oncotriage import run_fingerprint as _rf                    # noqa: E402
from oncotriage import spend as _spend                           # noqa: E402
from oncotriage.agent import deps                                # noqa: E402
from oncotriage.agent import evaluation as _ev                   # noqa: E402
from oncotriage.agent import models as _models                   # noqa: E402
from oncotriage.batch import runner as _runner                   # noqa: E402
from oncotriage.dashboard.tabs import run_health as _run_health  # noqa: E402
from oncotriage.storage import database_logger as _dl            # noqa: E402
from oncotriage.storage import queries as _queries               # noqa: E402

import _provider_pin                                             # noqa: E402

_PROVIDER_BEFORE_PIN = _provider_pin.pin_openai_arm(os.path.basename(__file__))


# ===========================================================================
# HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0, "skipped": 0}
_FAILURES = []


def check(label, actual, expected):
    ok = actual == expected
    _RESULTS["passed" if ok else "failed"] += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"          expected: {expected!r}")
        print(f"          actual:   {actual!r}")
        _FAILURES.append(label)


def skip(label, reason):
    _RESULTS["skipped"] += 1
    print(f"  SKIP  {label} -- {reason}")


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


def at(container, key, why=None):
    try:
        return container[key]
    except Exception as exc:                                   # noqa: BLE001
        return _Absent(why or f"{type(exc).__name__}: {key!r}")


def field(obj, name):
    """An ATTRIBUTE read that cannot abort the file -- `at` subscripts, and a
    NamedTuple field read by subscript raises TypeError."""
    try:
        return getattr(obj, name)
    except Exception as exc:                                   # noqa: BLE001
        return _Absent(f"{type(exc).__name__}: .{name}")


def raised(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except BaseException as exc:                               # noqa: BLE001
        return exc
    return None


def drive(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                               # noqa: BLE001
        return _Absent(f"raised {type(exc).__name__}: {exc}")


def digest(path):
    if not os.path.exists(path):
        return "absent"
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# THE ACCOUNTING PRECISION. Both ledgers hold IEEE doubles of the SAME addends
# (one liability per attempt, priced once), and a reconciliation sums at most a
# few dozen of them in possibly different orders -- a difference bounded by a
# small multiple of the machine epsilon times the total, far below 1e-12 for
# totals under $1,000. The printed surfaces round to 4 dp (report) and 2 dp
# (seed), so 1e-9 is both far looser than the arithmetic can drift and far
# tighter than anything an operator reads. A genuine disagreement -- one
# attempt charged in one ledger and not the other -- is at least one priced
# token, which for the cheapest priced model here is above 1e-8.
_TOL = 1e-9


def near(a, b, eps=_TOL):
    return (isinstance(a, (int, float)) and isinstance(b, (int, float))
            and not isinstance(a, bool) and not isinstance(b, bool)
            and abs(a - b) <= eps)


_REPO = os.path.dirname(os.path.dirname(os.path.abspath(oncotriage.__file__)))
_TESTS = os.path.dirname(os.path.abspath(__file__))
_TMP = tempfile.mkdtemp(prefix="oncotriage-billing-closure-")
_PROD_DB = _paths.inferences_path
_PROD_DIGEST_BEFORE = digest(_PROD_DB)
_WIRE = config.matching_wire_model()
_JITTER_START = _pr.full_jitter_delay
_POLICY_START = _spend.policy()
_CONFIG_KEYS = ("SPEND_CAP_USD", "SPEND_CAP_ENFORCED", "MATCHING_PROVIDER")
_CONFIG_START = {k: getattr(config, k) for k in _CONFIG_KEYS}
_RESOLVED_START = dict(_paths._RESOLVED)
_CLASSIFY_START = _ev._classify_matching_failure
_OPEN_START = _dl._open_connection
_SETTLE_START = _dl.settle_billing_attempt
_RUNNER_OS_START = _runner.os
_RUNNER_FCNTL_START = _runner.fcntl
_CAP = 10.0

FIXED_FP = {"fingerprint_version": _rf.FINGERPRINT_VERSION}
for _field in _rf.FINGERPRINT_FIELDS:
    FIXED_FP[_field] = f"fixed-{_field}"
FIXED_FP.update(collection_points=12067, campaign_cohort_size=500,
                campaign_cohort_seed=42, matching_per_trial_empty_retries=1,
                matching_per_trial_parallel_bound=4)
_OTHER_FP = dict(FIXED_FP, llm_classifier_prompt_version="other-version")


def new_db(name):
    db = os.path.join(_TMP, name)
    _dl.initialize_database(db)
    return db


def ro_rows(db, sql, params=()):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


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


def _reset_spend_state():
    _spend.SPEND_LEDGER.reset()
    _spend.SPEND_STOP.reset()
    _spend.BILLING_RECORD.clear()
    _spend.BILLING_RECORD.reset_liability()
    _spend.BILLING_RECORD_FAULTS.clear()
    _spend.SPEND_LEDGER_FAULTS.clear()


@contextlib.contextmanager
def sink_installed(db, campaign_id, run_id):
    _reset_spend_state()
    _spend.BILLING_RECORD.install(_dl.BillingRecordSink(db, campaign_id, run_id))
    try:
        yield
    finally:
        _spend.BILLING_RECORD.clear()


class _Usage:
    def __init__(self, prompt, completion):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.completion_tokens_details = None


def _chat_response(tokens, model=None):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content='{"evaluations": []}',
                                          refusal=None),
            finish_reason="stop")],
        usage=_Usage(*tokens), model=model or _WIRE)


class _Client:
    """A chat and an embeddings stand-in, each doing what ``behaviour`` says."""

    def __init__(self, chat=None, embed=None):
        self._chat, self._embed = chat, embed
        self.calls = 0
        self.chat = types.SimpleNamespace(completions=self)
        self.embeddings = types.SimpleNamespace(create=self._embed_create)

    def create(self, **kwargs):
        self.calls += 1
        return self._chat(kwargs)

    def _embed_create(self, **kwargs):
        self.calls += 1
        return self._embed(kwargs)


def _embedding(prompt_tokens=7, model=None):
    return types.SimpleNamespace(
        data=[types.SimpleNamespace(embedding=[0.0])],
        usage=types.SimpleNamespace(prompt_tokens=prompt_tokens),
        model=model or config.EMBEDDING_MODEL)


def _throw(exc):
    raise exc


_pr.full_jitter_delay = lambda retry_number, rng=None: 0.0


# ===========================================================================
section("SECTION 1 -- P1: one liability rule, both ledgers, every class")
# ===========================================================================

_RESERVE_S5 = _spend.price_usage(
    _WIRE, _ev._reservation_input_tokens("system prompt", "user prompt"),
    config.MATCHING_MAX_TOKENS)[0]


def parity(name, client, call, classify=None):
    """Drive one attempt with a sink installed; return what both ledgers say.

    LIVE is this process: its ledger measure and ``spend.remaining``. RESUMED is
    what a fresh process would compute: the durable total seeded into an empty
    ledger, read through the SAME ``remaining``. That is the operator surface a
    resume reads, not a re-derivation of it.
    """
    db = new_db(f"p1_{name}.db")
    run = _dl.start_run_record("batch", db_path=db, fingerprint=FIXED_FP)
    camp = f"camp-{name}"
    deps.set_override(deps.OPENAI_CLIENT, client)
    if classify is not None:
        _ev._classify_matching_failure = classify
    try:
        with settings(SPEND_CAP_USD=_CAP, SPEND_CAP_ENFORCED=True), \
                sink_installed(db, camp, run):
            exc = raised(call)
            live = {"measured": _spend.SPEND_LEDGER.measured,
                    "remaining": _spend.remaining(_spend.SPEND_SOURCE_STAGE5),
                    "tally": _spend.BILLING_RECORD.liability_snapshot(),
                    "faults": dict(_spend.SPEND_LEDGER_FAULTS),
                    "record_faults": dict(_spend.BILLING_RECORD_FAULTS)}
            durable = _dl.campaign_billing_total(camp, db_path=db)
            _spend.BILLING_RECORD.clear()
            _spend.SPEND_LEDGER.reset()
            _spend.SPEND_LEDGER.seed(_spend.LedgerSeed(
                usd=durable.usd, rows=durable.attempts, runs=1,
                source=_spend.SEED_SOURCE_BILLING_RECORD,
                unresolved=durable.unresolved))
            resumed_remaining = _spend.remaining(_spend.SPEND_SOURCE_STAGE5)
    finally:
        _ev._classify_matching_failure = _CLASSIFY_START
        deps.clear_override(deps.OPENAI_CLIENT)
        _reset_spend_state()
    rows = ro_rows(db, "SELECT state, outcome, reserved_usd, settled_usd "
                       "FROM billing_attempts")
    return {"exc": exc, "live": live, "durable": durable,
            "resumed_remaining": resumed_remaining, "rows": rows,
            "calls": client.calls}


def _s5_call():
    return _ev.call_matching_model("system prompt", "user prompt")


def _embed_call():
    return _models.get_embedding("breast cancer trial")


_CASES = {}
_CASES["s5_response"] = parity(
    "s5_response", _Client(chat=lambda kw: _chat_response((321, 45))), _s5_call)
_CASES["s5_response_unpriced"] = parity(
    "s5_response_unpriced",
    _Client(chat=lambda kw: _chat_response((321, 45),
                                           model="closure-unpriced-model")),
    _s5_call)
_CASES["s5_bad_usage"] = parity(
    "s5_bad_usage", _Client(chat=lambda kw: _chat_response((None, 45))),
    _s5_call)
_CASES["s5_possibly_billed"] = parity(
    "s5_possibly_billed",
    _Client(chat=lambda kw: _throw(RuntimeError("lost"))), _s5_call,
    classify=lambda exc: _pr.verdict_for(_pr.CATEGORY_UNCLASSIFIED))
_CASES["s5_not_billed"] = parity(
    "s5_not_billed",
    _Client(chat=lambda kw: _throw(RuntimeError("refused"))), _s5_call,
    classify=lambda exc: _pr.verdict_for(_pr.CATEGORY_CLIENT))
_CASES["s5_abandoned"] = parity(
    "s5_abandoned",
    _Client(chat=lambda kw: _throw(KeyboardInterrupt("operator"))), _s5_call)


def _failing_settle(*args, **kwargs):
    return _dl.SETTLE_FAILED


_dl.settle_billing_attempt = _failing_settle
try:
    _CASES["s5_settlement_failed"] = parity(
        "s5_settlement_failed",
        _Client(chat=lambda kw: _chat_response((321, 45))), _s5_call)
finally:
    _dl.settle_billing_attempt = _SETTLE_START
check("1-restore the settlement stand-in was removed, by identity",
      _dl.settle_billing_attempt is _SETTLE_START, True)

_CASES["emb_response"] = parity(
    "emb_response", _Client(embed=lambda kw: _embedding()), _embed_call)
_CASES["emb_response_unpriced"] = parity(
    "emb_response_unpriced",
    _Client(embed=lambda kw: _embedding(prompt_tokens=None)), _embed_call)
_CASES["emb_possibly_billed"] = parity(
    "emb_possibly_billed",
    _Client(embed=lambda kw: _throw(RuntimeError("embed endpoint lost"))),
    _embed_call)
_CASES["emb_abandoned"] = parity(
    "emb_abandoned",
    _Client(embed=lambda kw: _throw(KeyboardInterrupt("operator"))),
    _embed_call)

for _name, _c in _CASES.items():
    _durable_usd = getattr(_c["durable"], "usd", None)
    check(f"1a-{_name} *** the live ledger measure EQUALS the durable total ***",
          near(_c["live"]["measured"], _durable_usd), True)
    check(f"1b-{_name} *** live remaining EQUALS resumed remaining, read "
          f"through spend.remaining ***",
          near(_c["live"]["remaining"], _c["resumed_remaining"]), True)
    check(f"1c-{_name} non-degeneracy: the provider stand-in was called once",
          _c["calls"], 1)

check("1d non-degeneracy: every class but not_billed carries a POSITIVE "
      "liability, so the equalities above are not 0 == 0",
      sorted(n for n, c in _CASES.items()
             if not (getattr(c["durable"], "usd", 0) > 0)),
      ["s5_not_billed"])
check("1e not_billed is zero in both ledgers and the durable row says so",
      (_CASES["s5_not_billed"]["live"]["measured"],
       [r[1] for r in _CASES["s5_not_billed"]["rows"]]),
      (0.0, ["not_billed"]))
check("1f *** an UNPRICEABLE Stage 5 response is charged the RESERVATION in the "
      "LEDGER -- the asymmetry this pass closes (it used to charge $0) ***",
      (near(_CASES["s5_response_unpriced"]["live"]["measured"], _RESERVE_S5),
       [r[1] for r in _CASES["s5_response_unpriced"]["rows"]]),
      (True, ["response_unpriced"]))
check("1f-i ...and the pricing fault is still COUNTED, not silent",
      any(k.startswith("unpriced_model:")
          for k in _CASES["s5_response_unpriced"]["live"]["faults"]), True)
check("1g *** a FAILED embedding is charged its reservation in the LEDGER, not "
      "only durably -- the brief's reported asymmetry ***",
      (_CASES["emb_possibly_billed"]["live"]["measured"] > 0,
       [r[1] for r in _CASES["emb_possibly_billed"]["rows"]]),
      (True, ["possibly_billed"]))
check("1g-i ...and an interrupted one too, re-raised unchanged",
      (type(_CASES["emb_abandoned"]["exc"]).__name__,
       [r[1] for r in _CASES["emb_abandoned"]["rows"]],
       _CASES["emb_abandoned"]["live"]["measured"] > 0),
      ("KeyboardInterrupt", ["abandoned"], True))
_sf = _CASES["s5_settlement_failed"]
check("1h *** a settlement that did not land leaves the row RESERVED and the "
      "ledger is TOPPED UP to that reservation ***",
      ([r[0] for r in _sf["rows"]], near(_sf["live"]["measured"], _RESERVE_S5),
       _sf["live"]["record_faults"].get("ledger_topped_up:failed")),
      (["reserved"], True, 1))
check("1h-i non-degeneracy: the priced response is BELOW the reservation, so "
      "the top-up moved the ledger",
      (_spend.price_usage(_WIRE, 321, 45)[0] < _RESERVE_S5), True)

for _name, _c in _CASES.items():
    _tally = _c["live"]["tally"]
    _live_by = {k: (v[0], round(v[1], 12)) for k, v in _tally.items()
                if k != _spend.LIABILITY_OPEN and v[0]}
    _dur_by = {}
    for _state, _outcome, _reserved, _settled in _c["rows"]:
        if _state == "settled":
            n, u = _dur_by.get(_outcome, (0, 0.0))
            _dur_by[_outcome] = (n + 1, u + _settled)
    _dur_by = {k: (v[0], round(v[1], 12)) for k, v in _dur_by.items()}
    _unres_durable = sum(1 for r in _c["rows"] if r[0] == "reserved")
    if _name == "s5_settlement_failed":
        # The durable row is RESERVED; the live tally recorded the attempt's
        # resolution. Unresolved is compared through the durable read.
        check(f"1i-{_name} the durable record counts it UNRESOLVED",
              (_unres_durable, _c["durable"].unresolved), (1, 1))
        continue
    check(f"1i-{_name} SETTLED spend per outcome: live tally == durable rows",
          _live_by, _dur_by)
    check(f"1j-{_name} UNRESOLVED: nothing open live, nothing reserved durably",
          (at(_tally, _spend.LIABILITY_OPEN, "none")[0]
           if not isinstance(at(_tally, _spend.LIABILITY_OPEN), _Absent) else 0,
           _unres_durable), (0, 0))

# AN ATTEMPT IN FLIGHT: open live, reserved durably, at one amount.
_DB_OPEN = new_db("p1_open.db")
_RUN_OPEN = _dl.start_run_record("batch", db_path=_DB_OPEN, fingerprint=FIXED_FP)
with sink_installed(_DB_OPEN, "camp-open", _RUN_OPEN):
    _att = _spend.begin_billed_attempt(_spend.SPEND_SOURCE_STAGE5, _WIRE, 4000,
                                       400, where="a test attempt")
    _open_live = _spend.BILLING_RECORD.liability_snapshot().get(
        _spend.LIABILITY_OPEN)
    _open_dur = _dl.campaign_billing_total("camp-open", db_path=_DB_OPEN)
    check("1k *** UNRESOLVED, in flight: the live open tally equals the durable "
          "reserved rows, count and amount ***",
          (at(_open_live, 0), near(at(_open_live, 1), _open_dur.unresolved_usd),
           _open_dur.unresolved), (1, True, 1))
    _first = _att.resolve(_spend.BILLING_OUTCOME_RESPONSE, model=_WIRE,
                          prompt_tokens=100, completion_tokens=10)
    _second = _att.resolve(_spend.BILLING_OUTCOME_POSSIBLY_BILLED)
    check("1l resolution is idempotent: a second resolve charges nothing and "
          "returns the first amount",
          (near(_first, _second), near(_spend.SPEND_LEDGER.measured, _first),
           _spend.BILLING_RECORD.liability_snapshot()[_spend.LIABILITY_OPEN][0]),
          (True, True, 0))
_reset_spend_state()

check("1m the pure rule: an unknown outcome is charged the reservation, never "
      "zero, with a fault",
      _spend.attempt_liability("nonsense", 1.5)[:2] + (
          _spend.attempt_liability("nonsense", 1.5)[4] is not None,),
      ("possibly_billed", 1.5, True))
check("1n the pure rule: a priced response is priced; not_billed is zero",
      (near(_spend.attempt_liability("response", 9.0, _WIRE, 100, 10)[1],
            _spend.price_usage(_WIRE, 100, 10)[0]),
       _spend.attempt_liability("not_billed", 9.0)[1]), (True, 0.0))


# ── 1o..1w: THE FAILURE PATH'S OWN FAULTS, A RETRY, THE WARMUP, THE ASYNC TWIN ──
#
# ADDED BY THE P1 RECOVERY SESSION. The cases above drive a dispatch failure
# whose CLASSIFICATION and PACER SETTLEMENT both work. In the retry policy's
# `except Exception` branch those two calls ran BEFORE the attempt liability
# was resolved, so a raise from either left the in-process ledger uncharged
# while the durable row stayed RESERVED at its upper bound -- measured, before
# the fix: live remaining 10.000000 against resumed remaining 9.615158. And
# nothing drove a retry inside one logical call, the per-trial warmup, or the
# async twin, all three of which create one liability per wire attempt through
# the same hook.

def _boom_classify(exc):
    raise KeyError("a classifier bug on an unexpected exception shape")


def _boom_settle(permit, **kwargs):
    raise RuntimeError("pacer settle raised")


def _counter_delta(counter, before, prefix):
    return {k: v - before.get(k, 0) for k, v in counter.items()
            if k.startswith(prefix) and v - before.get(k, 0)}


def _policy_counters():
    return (dict(_pr.PROVIDER_RETRY_OUTCOMES),
            dict(_pr.PROVIDER_UNCONFIRMED_BILLING))


class _Steps:
    """A chat stand-in answering each call with the next step, in order."""

    def __init__(self, *steps):
        self.steps = list(steps)

    def __call__(self, kwargs):
        return self.steps.pop(0)()


_SCOPE = config.matching_quota_scope()
_CASES_2 = {}
_COUNTERS_2 = {}


def _with_counters(name, fn):
    before_outcomes, before_unconfirmed = _policy_counters()
    _CASES_2[name] = fn()
    _COUNTERS_2[name] = {
        "classify_failed": _counter_delta(_pr.PROVIDER_RETRY_OUTCOMES,
                                          before_outcomes, "classify_failed:"),
        "unconfirmed": _counter_delta(_pr.PROVIDER_UNCONFIRMED_BILLING,
                                      before_unconfirmed, f"{_SCOPE}:"),
    }


_with_counters("s5_classify_raises", lambda: parity(
    "s5_classify_raises",
    _Client(chat=lambda kw: _throw(RuntimeError("lost on the wire"))),
    _s5_call, classify=_boom_classify))

_pr.PACER.settle = _boom_settle
try:
    _with_counters("s5_pacer_settle_raises", lambda: parity(
        "s5_pacer_settle_raises",
        _Client(chat=lambda kw: _throw(RuntimeError("lost on the wire"))),
        _s5_call,
        classify=lambda exc: _pr.verdict_for(_pr.CATEGORY_UNCLASSIFIED)))
finally:
    vars(_pr.PACER).pop("settle", None)
check("1o-restore the pacer settle stand-in was removed from the instance",
      "settle" in vars(_pr.PACER), False)

_with_counters("s5_retry_then_response", lambda: parity(
    "s5_retry_then_response",
    _Client(chat=_Steps(lambda: _throw(RuntimeError("dropped mid-response")),
                        lambda: _chat_response((321, 45)))),
    _s5_call,
    classify=lambda exc: _pr.verdict_for(_pr.CATEGORY_CONNECTION_LOST)))

_with_counters("s5_warmup_possibly_billed", lambda: parity(
    "s5_warmup_possibly_billed",
    _Client(chat=lambda kw: _throw(RuntimeError("warmup lost"))),
    lambda: _ev.call_matching_model_warmup("system prompt"),
    classify=lambda exc: _pr.verdict_for(_pr.CATEGORY_UNCLASSIFIED)))

_with_counters("s5_warmup_response", lambda: parity(
    "s5_warmup_response",
    _Client(chat=lambda kw: _chat_response((321, 1))),
    lambda: _ev.call_matching_model_warmup("system prompt")))

_ASYNC_SENDS = {"n": 0}


def _async_call(classify):
    import asyncio

    async def _send():
        _ASYNC_SENDS["n"] += 1
        raise RuntimeError("async request lost")

    return lambda: asyncio.run(_pr.execute_async(
        _send, scope=_SCOPE, reservation_tokens=4400,
        reservation_kind=_pr.RESERVATION_INFERENCE, classify=classify,
        attempt_record=_ev._Stage5AttemptRecord(_WIRE, 4000, 400),
        label="p1-async-twin"))


_with_counters("async_classify_raises", lambda: parity(
    "async_classify_raises", _Client(), _async_call(_boom_classify)))
_with_counters("async_possibly_billed", lambda: parity(
    "async_possibly_billed", _Client(),
    _async_call(lambda exc: _pr.verdict_for(_pr.CATEGORY_UNCLASSIFIED))))

for _name, _c in _CASES_2.items():
    _durable = _c["durable"]
    _outcomes = [r[1] for r in _c["rows"]]
    _states = [r[0] for r in _c["rows"]]
    _open = at(_c["live"]["tally"], _spend.LIABILITY_OPEN, "none")
    check(f"1o-{_name} *** live ledger measure EQUALS the durable total ***",
          near(_c["live"]["measured"], getattr(_durable, "usd", None)), True)
    check(f"1p-{_name} *** live remaining EQUALS resumed remaining ***",
          near(_c["live"]["remaining"], _c["resumed_remaining"]), True)
    check(f"1q-{_name} every durable row was SETTLED -- none left reserved, "
          f"and the durable reader counts none unresolved",
          (all(s == "settled" for s in _states), bool(_states),
           getattr(_durable, "unresolved", None)), (True, True, 0))
    check(f"1r-{_name} nothing is left OPEN in the live tally",
          (at(_open, 0) if not isinstance(_open, _Absent) else 0), 0)
    check(f"1s-{_name} non-degeneracy: the liability is POSITIVE, so the "
          f"equalities above are not 0 == 0",
          (getattr(_durable, "usd", 0) or 0) > 0, True)

check("1t *** a classifier that RAISES: the attempt resolves possibly_billed at "
      "its reservation, the classifier's own exception still propagates (the "
      "pre-fix behaviour), and the provider's is its __context__ ***",
      ([r[1] for r in _CASES_2["s5_classify_raises"]["rows"]],
       near(_CASES_2["s5_classify_raises"]["live"]["measured"], _RESERVE_S5),
       type(_CASES_2["s5_classify_raises"]["exc"]).__name__,
       type(getattr(_CASES_2["s5_classify_raises"]["exc"], "__context__",
                    None)).__name__),
      (["possibly_billed"], True, "KeyError", "RuntimeError"))
check("1t-i ...and the classifier fault is COUNTED under classify_failed, and "
      "the attempt reaches the unconfirmed-billing tally like any "
      "possibly-billed failure",
      (_COUNTERS_2["s5_classify_raises"]["classify_failed"],
       _COUNTERS_2["s5_classify_raises"]["unconfirmed"]),
      ({f"classify_failed:{_SCOPE}:KeyError": 1},
       {f"{_SCOPE}:{_pr.CATEGORY_UNCLASSIFIED}": 1}))
check("1u *** a PACER SETTLEMENT that raises: the attempt was already resolved, "
      "and the settlement's exception still propagates (the pre-fix "
      "behaviour) ***",
      ([r[1] for r in _CASES_2["s5_pacer_settle_raises"]["rows"]],
       str(_CASES_2["s5_pacer_settle_raises"]["exc"])),
      (["possibly_billed"], "pacer settle raised"))
check("1v a RETRY inside one logical call is TWO liabilities -- the dropped "
      "attempt at its reservation, the answered one at its price -- and the "
      "call succeeds",
      (_CASES_2["s5_retry_then_response"]["calls"],
       [r[1] for r in _CASES_2["s5_retry_then_response"]["rows"]],
       _CASES_2["s5_retry_then_response"]["exc"],
       near(_CASES_2["s5_retry_then_response"]["live"]["measured"],
            _RESERVE_S5 + _spend.price_usage(_WIRE, 321, 45)[0])),
      (2, ["possibly_billed", "response"], None, True))
check("1w the WARMUP's failure and response resolve through the same hook "
      "(one liability each)",
      ([r[1] for r in _CASES_2["s5_warmup_possibly_billed"]["rows"]],
       [r[1] for r in _CASES_2["s5_warmup_response"]["rows"]]),
      (["possibly_billed"], ["response"]))
check("1w-i the ASYNC twin: a raising classifier and an ordinary failure both "
      "resolve possibly_billed, and send ran once per case",
      ([r[1] for r in _CASES_2["async_classify_raises"]["rows"]],
       [r[1] for r in _CASES_2["async_possibly_billed"]["rows"]],
       _ASYNC_SENDS["n"],
       type(_CASES_2["async_classify_raises"]["exc"]).__name__),
      (["possibly_billed"], ["possibly_billed"], 2, "KeyError"))
check("1w-ii the async twin with NO on_possibly_billed callback: the classifier "
      "fault is counted, and the unconfirmed-billing tally is NOT bumped -- "
      "gated exactly as the ordinary failure branch gates it",
      (_COUNTERS_2["async_classify_raises"]["classify_failed"],
       _COUNTERS_2["async_classify_raises"]["unconfirmed"],
       _COUNTERS_2["async_possibly_billed"]["unconfirmed"]),
      ({f"classify_failed:{_SCOPE}:KeyError": 1}, {}, {}))


# ===========================================================================
section("SECTION 2 -- P2: the counter registry proves a zero or refuses by name")
# ===========================================================================

_NAMES = list(_degradation.registered_names())
_DB2 = new_db("p2_flush.db")
_R2 = _dl.start_run_record("batch", db_path=_DB2, fingerprint=FIXED_FP)

check("2a a flush with names records exactly those names",
      (_dl.flush_run_metrics(_R2, {}, len(_NAMES), db_path=_DB2,
                             registered_names=_NAMES),
       sorted(r[0] for r in ro_rows(
           _DB2, "SELECT name FROM run_counter_registry WHERE run_id = ?",
           (_R2,)))), (True, sorted(_NAMES)))
check("2a-i non-degeneracy: the registry is not empty and holds every "
      "historical unrecorded-billing counter",
      (len(_NAMES) > 10, sorted(set(_dl.HISTORICAL_UNRECORDED_BILLING_COUNTERS)
                                - set(_NAMES))), (True, []))
for _label, _names, _count, _totals in (
        ("count disagrees", _NAMES[:-1], len(_NAMES), {}),
        ("duplicate name", _NAMES[:-1] + [_NAMES[0]], len(_NAMES), {}),
        ("a non-zero total that is not registered", _NAMES, len(_NAMES),
         {"NOT_A_REGISTERED_COUNTER": 3}),
        ("a non-identifier", _NAMES[:-1] + ["not an identifier"], len(_NAMES),
         {})):
    _before = ro_rows(_DB2, "SELECT COUNT(*) FROM run_counter_registry")[0][0]
    check(f"2b refused, whole flush, nothing written: {_label}",
          (_dl.flush_run_metrics(_R2, _totals, _count, db_path=_DB2,
                                 registered_names=_names),
           ro_rows(_DB2, "SELECT COUNT(*) FROM run_counter_registry")[0][0]),
          (False, _before))
_R2b = _dl.start_run_record("batch", db_path=_DB2, fingerprint=FIXED_FP)
check("2c the runner's flush_health records the live registry's names",
      (_runner.flush_health(_R2b, db_path=_DB2),
       sorted(r[0] for r in ro_rows(
           _DB2, "SELECT name FROM run_counter_registry WHERE run_id = ?",
           (_R2b,)))), (True, sorted(_NAMES)))


def legacy_chain(tag, mutate=None, reflush=None):
    """A predecessor run that would be COVERED, and the run resuming it.

    ``reflush(db, run_id)`` runs after the registered flush and before
    ``mutate`` -- a second writer-side flush, which is the only honest way to
    produce a registry the WRITER left behind rather than one hand-edited."""
    db = new_db(f"p2_legacy_{tag}.db")
    r1 = _dl.start_run_record("batch", db_path=db, fingerprint=FIXED_FP,
                              resumed=False)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE runs SET cohort_digest = 'dig' WHERE id = ?", (r1,))
    conn.execute(
        "INSERT INTO inferences (patient_id, timestamp, estimated_cost_usd, "
        "llm_classifier_retries, retrieval_channels, run_id) VALUES "
        "('p1', '2026-09-01T00:00:00', 0.4, 0, ?, ?)",
        (json.dumps({"dense": {"status": "ablated"}}), r1))
    conn.commit()
    conn.close()
    _dl.flush_run_metrics(r1, {}, len(_NAMES), db_path=db,
                          registered_names=_NAMES)
    if reflush is not None:
        reflush(db, r1)
    _dl.finalize_run_record(r1, "FAILED", db_path=db)
    if mutate is not None:
        conn = sqlite3.connect(db)
        mutate(conn, r1)
        conn.commit()
        conn.close()
    r2 = _dl.start_run_record("batch", db_path=db, fingerprint=FIXED_FP,
                              resumed=True)
    return _dl.historical_campaign_evidence(r2, "dig", db_path=db)


_covered = legacy_chain("covered")
check("2d CLEAN CONTROL: a predecessor whose registry proves every required "
      "counter is COVERED",
      (_covered.covered, _covered.reasons, near(_covered.usd, 0.4)),
      (True, (), True))
_dropped = legacy_chain("no_table",
                        lambda c, r: c.execute("DROP TABLE run_counter_registry"))
check("2e *** a database with NO counter registry (pre-era-18) is REFUSED, by "
      "name -- an absent counter row no longer reads as zero ***",
      (_dropped.covered, "counter_registration_unproven" in _dropped.reasons,
       "recorded no counter registry" in _dropped.detail), (False, True, True))
def _build_lacking(counter):
    """What a build that NEVER REGISTERED ``counter`` writes: the writer keeps
    the registry and the meta count in step, so BOTH are one short. Deleting
    the row alone would model a corrupt registry instead (2i-iii)."""
    def mutate(c, r):
        c.execute("DELETE FROM run_counter_registry WHERE run_id = ? AND "
                  "name = ?", (r, counter))
        c.execute("UPDATE run_metrics SET value = value - 1 WHERE run_id = ? "
                  "AND category = 'meta' AND name = 'counters_registered'", (r,))
    return mutate


_missing = legacy_chain("missing_one",
                        _build_lacking("PROVIDER_UNCONFIRMED_BILLING"))
check("2f *** a registry that omits ONE required counter is REFUSED, naming "
      "that counter ***",
      (_missing.covered, "counter_registration_unproven" in _missing.reasons,
       "PROVIDER_UNCONFIRMED_BILLING" in _missing.detail), (False, True, True))
_disagree = legacy_chain(
    "disagrees",
    lambda c, r: c.execute("INSERT INTO run_counter_registry (run_id, name, "
                           "written_at) VALUES (?, 'EXTRA_NAME', 'x')", (r,)))
check("2g a registry that disagrees with its own health record's count is "
      "REFUSED",
      (_disagree.covered, "counter_registration_unproven" in _disagree.reasons,
       "health record says" in _disagree.detail), (False, True, True))
check("2h the reason is a member of the closed vocabulary, and every OTHER "
      "condition still stands beside it",
      ("counter_registration_unproven" in _dl.HISTORICAL_COVERAGE_REASONS,
       len(_dl.HISTORICAL_COVERAGE_REASONS)), (True, 11))

# ---- the P2 recovery: the refusal NAMES its counters, and a registry counts
#      only when it is the health record's own. Every scenario below was driven
#      as a defect against the inherited code before the fix existed.
_REQUIRED = tuple(_dl.HISTORICAL_UNRECORDED_BILLING_COUNTERS)
check("2i the clean control names no unproven counter",
      (field(_covered, "unproven_counters"), "unproven" in str(_covered.detail)),
      ((), False))
check("2i-i a database with no registry leaves EVERY required counter "
      "unproven, by name",
      field(_dropped, "unproven_counters"), _REQUIRED)
check("2i-ii a build that never registered one counter names exactly that "
      "counter -- and nothing else is unproven",
      (field(_missing, "unproven_counters"),
       "not its health record's own" in _missing.detail),
      (("PROVIDER_UNCONFIRMED_BILLING",), False))
_corrupt = legacy_chain(
    "row_deleted_count_kept",
    lambda c, r: c.execute("DELETE FROM run_counter_registry WHERE run_id = ? "
                           "AND name = 'PROVIDER_UNCONFIRMED_BILLING'", (r,)))
check("2i-iii a registry row deleted with the meta count left as written is a "
      "registry that is NOT the health record's own: every required counter "
      "unproven, not just the deleted one",
      (_corrupt.covered, field(_corrupt, "unproven_counters"),
       "not its health record's own" in _corrupt.detail),
      (False, _REQUIRED, True))


def legacy_chain_many(tag, n, mutate):
    """``n`` stitched predecessors, each would-be covered, then ``mutate``."""
    db = new_db(f"p2_many_{tag}.db")
    prior = []
    for i in range(n):
        r = _dl.start_run_record("batch", db_path=db, fingerprint=FIXED_FP,
                                 resumed=bool(i))
        conn = sqlite3.connect(db)
        conn.execute("UPDATE runs SET cohort_digest = 'dig' WHERE id = ?", (r,))
        conn.execute(
            "INSERT INTO inferences (patient_id, timestamp, estimated_cost_usd, "
            "llm_classifier_retries, retrieval_channels, run_id) VALUES "
            "(?, '2026-09-01T00:00:00', 0.1, 0, ?, ?)",
            (f"p{i}", json.dumps({"dense": {"status": "ablated"}}), r))
        conn.commit()
        conn.close()
        _dl.flush_run_metrics(r, {}, len(_NAMES), db_path=db,
                              registered_names=_NAMES)
        _dl.finalize_run_record(r, "FAILED", db_path=db)
        prior.append(r)
    conn = sqlite3.connect(db)
    mutate(conn)
    conn.commit()
    conn.close()
    r_next = _dl.start_run_record("batch", db_path=db, fingerprint=FIXED_FP,
                                  resumed=True)
    return _dl.historical_campaign_evidence(r_next, "dig", db_path=db)


def _crowd(conn):
    conn.execute("DELETE FROM run_counter_registry "
                 "WHERE name = 'PROVIDER_UNCONFIRMED_BILLING'")
    conn.execute("UPDATE run_metrics SET value = value - 1 WHERE "
                 "category = 'meta' AND name = 'counters_registered'")
    conn.execute("UPDATE runs SET cohort_digest = 'other', finished_at = NULL")


_many = legacy_chain_many("crowded", 6, _crowd)
check("2j non-degeneracy: six predecessors each carry a cohort AND a "
      "finalization note, so ten other notes fill the cap before the registry "
      "notes are reached",
      (_many.reasons, _many.run_ids),
      (("cohort_mismatch", "not_cleanly_finalized",
        "counter_registration_unproven"), (1, 2, 3, 4, 5, 6)))
check("2j-i *** the refusal still NAMES the counter, in the field AND in the "
      "printed detail -- it used to be truncated away ***",
      (field(_many, "unproven_counters"),
       "PROVIDER_UNCONFIRMED_BILLING" in str(_many.detail)),
      (("PROVIDER_UNCONFIRMED_BILLING",), True))

_reflushed = legacy_chain(
    "reflushed",
    reflush=lambda db, r: _dl.flush_run_metrics(r, {}, len(_NAMES), db_path=db))
check("2k *** a flush WITHOUT names removes the previous flush's registry, so "
      "the health record is no longer read beside another flush's counters ***",
      (_reflushed.covered, "counter_registration_unproven" in _reflushed.reasons,
       field(_reflushed, "unproven_counters")), (False, True, _REQUIRED))

_stale = legacy_chain(
    "stale_stamp",
    lambda c, r: c.execute("UPDATE run_counter_registry SET written_at = "
                           "'2000-01-01T00:00:00' WHERE run_id = ?", (r,)))
check("2l *** a registry stamped by a different flush than the health record "
      "is REFUSED, every required counter unproven ***",
      (_stale.covered, "different flush" in _stale.detail,
       field(_stale, "unproven_counters")), (False, True, _REQUIRED))

_orphan = legacy_chain(
    "orphan_value",
    lambda c, r: c.execute(
        "INSERT INTO run_metrics (run_id, category, name, value, written_at) "
        "SELECT run_id, 'degradation', 'NOT_A_REGISTERED_COUNTER', 3, "
        "written_at FROM run_metrics WHERE run_id = ? AND category = 'meta' "
        "LIMIT 1", (r,)))
check("2m *** a health record valuing a counter its registry does not name is "
      "REFUSED -- the two were not produced together ***",
      (_orphan.covered, "NOT_A_REGISTERED_COUNTER" in _orphan.detail,
       field(_orphan, "unproven_counters")), (False, True, _REQUIRED))

_repeat = legacy_chain(
    "repeated_name",
    lambda c, r: c.execute(
        "INSERT INTO run_counter_registry (run_id, name, written_at) SELECT "
        "run_id, name, written_at FROM run_counter_registry WHERE run_id = ? "
        "LIMIT 1", (r,)))
check("2n a registry repeating a name (distinct count still equals the meta "
      "count) is REFUSED",
      (_repeat.covered, "repeated name" in _repeat.detail),
      (False, True))
_two_meta = legacy_chain(
    "two_meta_rows",
    lambda c, r: c.execute(
        "INSERT INTO run_metrics (run_id, category, name, value, written_at) "
        "SELECT run_id, category, name, value, written_at FROM run_metrics "
        "WHERE run_id = ? AND category = 'meta' AND "
        "name = 'counters_registered'", (r,)))
check("2n-i a health record with TWO counters_registered meta rows is not one "
      "flush's record: REFUSED, every required counter unproven",
      (_two_meta.covered, "2 meta rows" in _two_meta.detail,
       field(_two_meta, "unproven_counters")), (False, True, _REQUIRED))
_R2c = _dl.start_run_record("batch", db_path=_DB2, fingerprint=FIXED_FP)
_dl.flush_run_metrics(_R2c, {}, len(_NAMES), db_path=_DB2,
                      registered_names=_NAMES)
check("2o the writer: a names-less flush leaves this run with NO registry rows "
      "and another run's untouched",
      (_dl.flush_run_metrics(_R2c, {}, len(_NAMES), db_path=_DB2),
       ro_rows(_DB2, "SELECT COUNT(*) FROM run_counter_registry WHERE "
                     "run_id = ?", (_R2c,))[0][0],
       ro_rows(_DB2, "SELECT COUNT(*) FROM run_counter_registry WHERE "
                     "run_id = ?", (_R2,))[0][0]),
      (True, 0, len(_NAMES)))


# ===========================================================================
section("SECTION 3 -- P3: one campaign identity in runs, summaries and billing")
# ===========================================================================

def run_row(db, fp, *, resumed, status, bcid):
    rid = _dl.start_run_record("batch", db_path=db, fingerprint=fp,
                               resumed=resumed)
    if bcid is not None:
        _dl.set_run_billing_campaign_id(rid, bcid, db_path=db)
    if status is not None:
        _dl.finalize_run_record(rid, status, db_path=db)
    return rid


def summary(db, key):
    conn = sqlite3.connect(db)
    try:
        return _queries.run(conn, key)
    finally:
        conn.close()


_DB3 = new_db("p3_zero_success.db")
_z1 = run_row(_DB3, FIXED_FP, resumed=False, status="FAILED", bcid="camp-z")
_z2 = run_row(_DB3, FIXED_FP, resumed=False, status="FAILED", bcid="camp-z")
_camps = summary(_DB3, "campaign_summary")
check("3a *** a zero-success restart (resumed = 0 on both rows) that continues "
      "one billing campaign is ONE campaign in campaign_summary ***",
      (len(_camps), list(_camps["run_ids"]), list(_camps["billing_campaign_id"])),
      (1, [f"{_z1} -> {_z2}"], ["camp-z"]))
_runs = summary(_DB3, "run_summary")
check("3a-i ...and run_summary names that campaign on both rows, while "
      "`resumed` still reads 0 -- the column is unchanged in meaning",
      (sorted(_runs["billing_campaign_id"]), sorted(_runs["resumed"])),
      (["camp-z", "camp-z"], [0, 0]))
_DB3C = new_db("p3_control.db")
run_row(_DB3C, FIXED_FP, resumed=False, status="FAILED", bcid=None)
run_row(_DB3C, FIXED_FP, resumed=False, status="FAILED", bcid=None)
check("3b CONTROL: the same two rows WITHOUT the billing campaign id are two "
      "campaigns -- the display defect P3 names",
      len(summary(_DB3C, "campaign_summary")), 2)

_DB3D = new_db("p3_concurrent.db")
_x1 = run_row(_DB3D, FIXED_FP, resumed=False, status="KILLED", bcid="camp-x")
_y1 = run_row(_DB3D, FIXED_FP, resumed=False, status="KILLED", bcid="camp-y")
_x2 = run_row(_DB3D, FIXED_FP, resumed=True, status="KILLED", bcid="camp-x")
_y2 = run_row(_DB3D, FIXED_FP, resumed=True, status=None, bcid="camp-y")
_cd = {r.billing_campaign_id: r.run_ids
       for r in summary(_DB3D, "campaign_summary").itertuples()}
check("3c two INTERLEAVED campaigns sharing one configuration stay two, each "
      "stitched to its own runs",
      _cd, {"camp-x": f"{_x1} -> {_x2}", "camp-y": f"{_y1} -> {_y2}"})

_DB3E = new_db("p3_legacy_then_campaign.db")
_l1 = run_row(_DB3E, FIXED_FP, resumed=False, status="KILLED", bcid=None)
_l2 = run_row(_DB3E, FIXED_FP, resumed=True, status="STOPPED", bcid=None)
_l3 = run_row(_DB3E, FIXED_FP, resumed=True, status=None, bcid="camp-hist")
_l4 = run_row(_DB3E, _OTHER_FP, resumed=True, status=None, bcid=None)
check("3d a historical chain continued by a billing-era run is ONE campaign; a "
      "run under another configuration is its own",
      sorted((r.run_ids, r.billing_campaign_id)
             for r in summary(_DB3E, "campaign_summary").itertuples()),
      sorted([(f"{_l1} -> {_l2} -> {_l3}", "camp-hist"), (f"{_l4}", None)]))

_pin_mismatch = []
for _db in (_DB3, _DB3C, _DB3D, _DB3E):
    _by_run = {}
    for _row in summary(_db, "campaign_summary").itertuples():
        _members = tuple(int(x) for x in str(_row.run_ids).split(" -> "))
        for _m in _members:
            _by_run[_m] = _members
    for _rid, _members in _by_run.items():
        _py = _dl.campaign_run_ids(_rid, db_path=_db).run_ids
        if _py != _members:
            _pin_mismatch.append((_db, _rid, _py, _members))
check("3e *** campaign_parent_map (Python) and the SQL stitch agree on EVERY "
      "run of every database above ***", _pin_mismatch, [])
check("3e-i non-degeneracy: the pin compared stitched campaigns, not only "
      "campaigns of one",
      len(_dl.campaign_run_ids(_l3, db_path=_DB3E).run_ids), 3)

# AN OLDER DATABASE STILL ANSWERS. The column is OPTIONAL: dropped here, both run
# views must still run, render it NULL, and stitch by the pre-era-18 rule -- and
# the Run Health tab's availability must not fall to an era gap over it, which
# is what hid the run table on the read-only production database in CI bucket A
# before the column was made optional.
_DB3O = new_db("p3_older.db")
_o1 = run_row(_DB3O, FIXED_FP, resumed=False, status="KILLED", bcid=None)
_o2 = run_row(_DB3O, FIXED_FP, resumed=True, status="STOPPED", bcid=None)
_conn = sqlite3.connect(_DB3O)
_conn.execute("ALTER TABLE runs DROP COLUMN billing_campaign_id")
_conn.commit()
_conn.close()
_old_camps = drive(summary, _DB3O, "campaign_summary")
_old_runs = drive(summary, _DB3O, "run_summary")
check("3g *** on a database WITHOUT runs.billing_campaign_id both run views "
      "still answer, stitching by the older rule and reading the id as NULL ***",
      (list(getattr(_old_camps, "run_ids", [])),
       [None if v != v else v for v in getattr(
           _old_camps, "billing_campaign_id", [1])],
       len(getattr(_old_runs, "index", []))),
      ([f"{_o1} -> {_o2}"], [None], 2))
_ro3 = sqlite3.connect(f"file:{_DB3O}?mode=ro", uri=True)
try:
    _missing_old = {k: _queries.missing_requirements(_ro3, k)
                    for k in ("run_summary", "campaign_summary")}
    _rendered_old = _queries.render_sql(_ro3, "campaign_summary")
finally:
    _ro3.close()
check("3g-i ...and neither query reports anything missing, so no reader skips "
      "it; the rendered SQL names the column nowhere",
      (_missing_old, "billing_campaign_id =" in _rendered_old),
      ({"run_summary": (), "campaign_summary": ()}, False))
check("3g-ii non-degeneracy: on the era-18 database the column IS rendered",
      "billing_campaign_id" in drive(lambda: _queries.render_sql(
          sqlite3.connect(_DB3), "campaign_summary")), True)

_table = _run_health._build_campaign_table(summary(_DB3, "campaign_summary"))
_rtable = _run_health._build_run_table(summary(_DB3, "run_summary"))
check("3f the dashboard's campaign and run tables show the billing campaign",
      (list(_table["billing campaign"]), sorted(_rtable["billing campaign"])),
      (["camp-z"], ["camp-z", "camp-z"]))


# ===========================================================================
section("SECTION 4 -- P4: durability, read back, and identity recovery")
# ===========================================================================

_DB4 = new_db("p4.db")
_conn = _dl._open_billing_connection(_DB4)
try:
    _sync = _conn.execute("PRAGMA synchronous").fetchone()[0]
    _full = _conn.execute("PRAGMA fullfsync").fetchone()[0]
finally:
    _conn.close()
check("4a a billing connection reads back synchronous FULL or stronger",
      _sync >= _dl.BILLING_SYNCHRONOUS_MINIMUM, True)
if sys.platform in _dl.BILLING_FULLFSYNC_PLATFORMS:
    check("4a-i ...and fullfsync ON on darwin", _full, 1)
else:
    skip("4a-i fullfsync read-back", f"{sys.platform} ignores the pragma")

_STATEMENTS = []


def _tracing_open(db_path, read_only=False):
    conn = _OPEN_START(db_path, read_only=read_only)
    conn.set_trace_callback(lambda sql: _STATEMENTS.append(sql.strip()))
    return conn


def _first_index(statements, prefix):
    return next((i for i, s in enumerate(statements)
                 if s.upper().startswith(prefix)), None)


_R4 = _dl.start_run_record("batch", db_path=_DB4, fingerprint=FIXED_FP)
_dl._open_connection = _tracing_open
try:
    _STATEMENTS.clear()
    drive(_dl.reserve_billing_attempt, _DB4, attempt_id="t4-a",
          campaign_id="camp-4", run_id=_R4, source="stage5", model=_WIRE,
          input_tokens=10, output_tokens=1, reserved_usd=0.5)
    _reserve_trace = list(_STATEMENTS)
    _STATEMENTS.clear()
    drive(_dl.settle_billing_attempt, _DB4, "t4-a", outcome="response",
          settled_usd=0.25)
    _settle_trace = list(_STATEMENTS)
    _STATEMENTS.clear()
    drive(_dl.set_run_billing_campaign_id, _R4, "camp-4", db_path=_DB4)
    _stamp_trace = list(_STATEMENTS)
finally:
    _dl._open_connection = _OPEN_START
check("4b-restore the tracing opener was removed, by identity",
      _dl._open_connection is _OPEN_START, True)
for _label, _trace, _write in (("reservation", _reserve_trace, "INSERT"),
                               ("settlement", _settle_trace, "UPDATE"),
                               ("run campaign stamp", _stamp_trace, "UPDATE")):
    _s = _first_index(_trace, "PRAGMA SYNCHRONOUS = FULL")
    _w = _first_index(_trace, _write)
    check(f"4b *** the {_label} connection sets synchronous FULL BEFORE its "
          f"write ***", (_s is not None, _w is not None
                         and _s is not None and _s < _w), (True, True))


class _WeakConnection(sqlite3.Connection):
    """A connection that silently turns the durability pragma into OFF."""

    def execute(self, sql, *args):
        if str(sql).strip().upper() == "PRAGMA SYNCHRONOUS = FULL":
            sql = "PRAGMA synchronous = OFF"
        return super().execute(sql, *args)


def _weak_open(db_path, read_only=False):
    if read_only:
        return _OPEN_START(db_path, read_only=True)
    return sqlite3.connect(db_path, timeout=30.0, factory=_WeakConnection)


_stub4 = _Client(chat=lambda kw: _chat_response((1, 1)))
deps.set_override(deps.OPENAI_CLIENT, _stub4)
_dl._open_connection = _weak_open
try:
    with sink_installed(_DB4, "camp-weak", _R4):
        _exc4 = raised(_ev.call_matching_model, "system prompt", "user prompt")
        _latch4 = (_spend.SPEND_STOP.requested, _spend.SPEND_STOP.limit)
    _weak_settle = _dl.settle_billing_attempt(_DB4, "t4-a", outcome="response",
                                              settled_usd=0.1)
finally:
    _dl._open_connection = _OPEN_START
    deps.clear_override(deps.OPENAI_CLIENT)
    _reset_spend_state()
check("4c *** CONTROL: a connection that reads back synchronous OFF REFUSES the "
      "reservation and NOTHING IS DISPATCHED ***",
      (_stub4.calls, type(_exc4).__name__, getattr(_exc4, "limit", None),
       "synchronous" in str(_exc4)),
      (0, "Stage5SpendStopped", "billing_record", True))
check("4c-i ...the run latches under the billing-record limit",
      _latch4, (True, "billing_record"))
check("4c-ii ...and a settlement on such a connection does not land",
      _weak_settle, _dl.SETTLE_FAILED)

_DB_OK = new_db("p4_billing_count.db")
_R_OK = _dl.start_run_record("batch", db_path=_DB_OK, fingerprint=FIXED_FP)
_COMMITS = []


class _CountingConnection(sqlite3.Connection):
    def commit(self):
        _COMMITS.append(1)
        return super().commit()


def _counting_open(db_path, read_only=False):
    if read_only:
        return _OPEN_START(db_path, read_only=True)
    return sqlite3.connect(db_path, timeout=30.0, factory=_CountingConnection)


deps.set_override(deps.OPENAI_CLIENT,
                  _Client(chat=lambda kw: _chat_response((100, 10))))
_dl._open_connection = _counting_open
try:
    with sink_installed(_DB_OK, "camp-count", _R_OK):
        for _ in range(16):
            _ev.call_matching_model("system prompt", "user prompt")
finally:
    _dl._open_connection = _OPEN_START
    deps.clear_override(deps.OPENAI_CLIENT)
    _reset_spend_state()
check("4d MEASURED: sixteen Stage 5 attempts (one per-trial patient's warmup "
      "and fifteen trials) make exactly 32 billing commits -- one reservation "
      "and one settlement each",
      (len(_COMMITS), len(ro_rows(_DB_OK, "SELECT 1 FROM billing_attempts"))),
      (32, 16))

# THE IDENTITY FILE'S SYNC ORDER.
_SYNC_LOG = []


class _OsProxy:
    def __getattr__(self, name):
        return getattr(_RUNNER_OS_START, name)

    @staticmethod
    def fsync(fd):
        kind = "dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
        _SYNC_LOG.append(("fsync", kind))
        return _RUNNER_OS_START.fsync(fd)

    @staticmethod
    def replace(src, dst):
        _SYNC_LOG.append(("replace",))
        return _RUNNER_OS_START.replace(src, dst)


_FAIL_FULLFSYNC_ON = set()


def _recording_fcntl(fd, cmd, *args):
    if cmd == getattr(fcntl, "F_FULLFSYNC", None):
        kind = "dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
        _SYNC_LOG.append(("fullfsync", kind))
        if kind in _FAIL_FULLFSYNC_ON:
            raise OSError(45, "planted F_FULLFSYNC refusal")
    return fcntl.fcntl(fd, cmd, *args)


_fcntl_ns = types.SimpleNamespace(fcntl=_recording_fcntl)
if hasattr(fcntl, "F_FULLFSYNC"):
    _fcntl_ns.F_FULLFSYNC = fcntl.F_FULLFSYNC
_CP4 = os.path.join(_TMP, "cp4")
os.makedirs(_CP4)
_paths._RESOLVED["checkpoint_path"] = _CP4 + os.sep
_runner.os, _runner.fcntl = _OsProxy(), _fcntl_ns
try:
    _SYNC_LOG.clear()
    _w = raised(_runner.write_campaign_record, "camp-sync", FIXED_FP, "dig")
    _order = list(_SYNC_LOG)
    _SYNC_LOG.clear()
    _FAIL_FULLFSYNC_ON.add("file")
    os.remove(os.path.join(_CP4, _runner.CAMPAIGN_RECORD_FILENAME))
    _w_fail = raised(_runner.write_campaign_record, "camp-sync", FIXED_FP, "dig")
    _left = sorted(os.listdir(_CP4))
    _FAIL_FULLFSYNC_ON.clear()
    _SYNC_LOG.clear()
    _runner.write_campaign_record("camp-sync", FIXED_FP, "dig")
    _SYNC_LOG.clear()
    _runner.clear_campaign_record()
    _clear_order = list(_SYNC_LOG)
finally:
    _runner.os, _runner.fcntl = _RUNNER_OS_START, _RUNNER_FCNTL_START
check("4e-restore the runner's os and fcntl were restored, by identity",
      (_runner.os is _RUNNER_OS_START, _runner.fcntl is _RUNNER_FCNTL_START),
      (True, True))
_darwin = sys.platform == "darwin" and hasattr(fcntl, "F_FULLFSYNC")
_expected_order = ([("fsync", "file")] + ([("fullfsync", "file")] if _darwin
                                          else [])
                   + [("replace",), ("fsync", "dir")]
                   + ([("fullfsync", "dir")] if _darwin else []))
check("4e *** the identity file is synced (and F_FULLFSYNC'd on darwin) BEFORE "
      "the rename, and the directory after it ***", (_w, _order),
      (None, _expected_order))
if _darwin:
    check("4f *** CONTROL: an F_FULLFSYNC refusal on the file REFUSES the "
          "campaign by name and leaves no record or temp file ***",
          (getattr(_w_fail, "reason", None), _left),
          (_runner.CAMPAIGN_REFUSAL_RECORD_UNWRITABLE, []))
else:
    skip("4f F_FULLFSYNC refusal control", f"{sys.platform} has no F_FULLFSYNC")
check("4g a cleared record's removal is synced through its directory",
      ("fsync", "dir") in _clear_order, True)


def campaign_setup(tag, *, stamp=FIXED_FP):
    """A first run that establishes a campaign and bills once, then FAILS."""
    db = new_db(f"p4_{tag}.db")
    cp = os.path.join(_TMP, f"cp_{tag}")
    os.makedirs(cp)
    _paths._RESOLVED["checkpoint_path"] = cp + os.sep
    r1 = _dl.start_run_record("batch", db_path=db, fingerprint=stamp,
                              resumed=False)
    _set_digest(db, r1)
    budget = _runner.establish_billing_campaign(False, stamp, "dig", r1, db)
    _dl.reserve_billing_attempt(db, attempt_id=f"{tag}-1",
                                campaign_id=budget.campaign_id, run_id=r1,
                                source="stage5", model=_WIRE, input_tokens=1,
                                output_tokens=1, reserved_usd=1.25)
    _dl.settle_billing_attempt(db, f"{tag}-1", outcome="response",
                               settled_usd=1.25)
    _dl.finalize_run_record(r1, "FAILED", db_path=db)
    return db, cp, budget.campaign_id


def _set_digest(db, rid):
    conn = sqlite3.connect(db)
    conn.execute("UPDATE runs SET cohort_digest = 'dig' WHERE id = ?", (rid,))
    conn.commit()
    conn.close()


def next_run(db, *, resumed, stamp=FIXED_FP):
    rid = _dl.start_run_record("batch", db_path=db, fingerprint=stamp,
                               resumed=resumed)
    _set_digest(db, rid)
    return rid


def record_path(cp):
    return os.path.join(cp, _runner.CAMPAIGN_RECORD_FILENAME)


_STATES_SEEN = []

# 4h: MISSING RECORD, CHECKPOINT PRESENT -> RECOVERED.
_dbh, _cph, _camph = campaign_setup("missing")
os.remove(record_path(_cph))
_rh = next_run(_dbh, resumed=True)
_rec = _dl.recover_campaign_identity(_rh, "dig", db_path=_dbh)
_STATES_SEEN.append(_rec.state)
_bh = drive(_runner.establish_billing_campaign, True, FIXED_FP, "dig", _rh, _dbh)
check("4h *** a MISSING identity record on resume is RECOVERED from the billing "
      "record: same campaign, the budget includes its prior spend ***",
      (getattr(_bh, "decision", None), getattr(_bh, "campaign_id", None) == _camph,
       near(getattr(getattr(_bh, "seed", None), "usd", None), 1.25)),
      (_runner.CAMPAIGN_DECISION_RECOVERED, True, True))
# GUARDED READS: under a defect that skips recovery the record is never written
# and the run row carries nothing, and a bare read there raised inside check()'s
# argument list -- the revert matrix's no-recovery plant ABORTED this file
# instead of failing it. `drive` turns the raise into a value check() fails on.
check("4h-i ...the record is rewritten with that id and the run row carries it",
      (at(drive(lambda: json.loads(Path(record_path(_cph)).read_text())),
          "campaign_id") == _camph,
       at(at(drive(ro_rows, _dbh, "SELECT billing_campaign_id FROM runs "
                              "WHERE id = ?", (_rh,)), 0), 0) == _camph),
      (True, True))

# 4i: CORRUPT RECORD, NO CHECKPOINT (a zero-success restart) -> RECOVERED.
_dbi, _cpi, _campi = campaign_setup("corrupt")
Path(record_path(_cpi)).write_text("{not json")
_ri = next_run(_dbi, resumed=False)
_bi = drive(_runner.establish_billing_campaign, False, FIXED_FP, "dig", _ri, _dbi)
check("4i *** a CORRUPT identity record is RECOVERED where the rows establish "
      "the campaign, and the unreadable file is KEPT beside it ***",
      (getattr(_bi, "decision", None), getattr(_bi, "campaign_id", None) == _campi,
       any(n.startswith(_runner.CAMPAIGN_RECORD_FILENAME + ".corrupt")
           for n in os.listdir(_cpi))),
      (_runner.CAMPAIGN_DECISION_RECOVERED, True, True))

# 4j: CORRUPT RECORD AND NO EVIDENCE -> REFUSED UNREADABLE.
_dbj = new_db("p4_corrupt_nothing.db")
_cpj = os.path.join(_TMP, "cp_corrupt_nothing")
os.makedirs(_cpj)
_paths._RESOLVED["checkpoint_path"] = _cpj + os.sep
Path(record_path(_cpj)).write_text("{not json")
_rj = next_run(_dbj, resumed=False)
_ej = raised(_runner.establish_billing_campaign, False, FIXED_FP, "dig", _rj,
             _dbj)
check("4j a corrupt record the rows cannot explain is REFUSED by name",
      getattr(_ej, "reason", None), _runner.CAMPAIGN_REFUSAL_RECORD_UNREADABLE)

# 4k: TWO OPEN CAMPAIGNS -> AMBIGUOUS, REFUSED.
_dbk, _cpk, _campk = campaign_setup("ambiguous")
_rk_other = _dl.start_run_record("batch", db_path=_dbk, fingerprint=FIXED_FP)
_set_digest(_dbk, _rk_other)
_dl.set_run_billing_campaign_id(_rk_other, "camp-other", db_path=_dbk)
_dl.reserve_billing_attempt(_dbk, attempt_id="other-1", campaign_id="camp-other",
                            run_id=_rk_other, source="stage5", model=_WIRE,
                            input_tokens=1, output_tokens=1, reserved_usd=2.0)
_dl.finalize_run_record(_rk_other, "KILLED", db_path=_dbk)
os.remove(record_path(_cpk))
_rk = next_run(_dbk, resumed=True)
_STATES_SEEN.append(_dl.recover_campaign_identity(_rk, "dig", db_path=_dbk).state)
_ek = raised(_runner.establish_billing_campaign, True, FIXED_FP, "dig", _rk, _dbk)
check("4k *** two OPEN campaigns sharing the configuration and cohort are "
      "REFUSED, naming both, and no identity record is written ***",
      (getattr(_ek, "reason", None), _campk in str(_ek), "camp-other" in str(_ek),
       os.path.exists(record_path(_cpk))),
      (_runner.CAMPAIGN_REFUSAL_IDENTITY_UNESTABLISHED, True, True, False))
_lines_k = str(drive(lambda: "\n".join(_ek.lines())))
check("4k-i OPERATOR SURFACE: the printed refusal block names the reason, both "
      "campaigns, the remedy and that nothing was billed",
      ("REFUSING TO START PAID WORK: campaign_identity_unestablished" in _lines_k,
       _campk in _lines_k and "camp-other" in _lines_k, "--fresh" in _lines_k,
       "NOTHING HAS BEEN BILLED" in _lines_k), (True, True, True, True))

# 4l: RUN RECORDED BILLED WORK, BILLING ROWS GONE -> INCONSISTENT, REFUSED.
_dbl, _cpl, _campl = campaign_setup("lost_rows")
_conn = sqlite3.connect(_dbl)
_conn.execute("DELETE FROM billing_attempts")
_conn.execute("INSERT INTO inferences (patient_id, timestamp, estimated_cost_usd, "
              "llm_classifier_calls, run_id) VALUES ('p', 'x', 0.9, 3, 1)")
_conn.commit()
_conn.close()
os.remove(record_path(_cpl))
_rl = next_run(_dbl, resumed=True)
_STATES_SEEN.append(_dl.recover_campaign_identity(_rl, "dig", db_path=_dbl).state)
_el = raised(_runner.establish_billing_campaign, True, FIXED_FP, "dig", _rl, _dbl)
check("4l *** a campaign whose run recorded billed work and holds NO billing "
      "row is REFUSED, never recovered at $0 ***",
      (getattr(_el, "reason", None), "hold no billing row" in str(_el)),
      (_runner.CAMPAIGN_REFUSAL_IDENTITY_UNESTABLISHED, True))

# 4m: THE CAMPAIGN IS CLOSED -> NOT RECOVERED.
_dbm, _cpm, _campm = campaign_setup("closed")
_conn = sqlite3.connect(_dbm)
_conn.execute("UPDATE runs SET status = 'FINISHED'")
_conn.commit()
_conn.close()
os.remove(record_path(_cpm))
_rm = next_run(_dbm, resumed=True)
_recm = _dl.recover_campaign_identity(_rm, "dig", db_path=_dbm)
_STATES_SEEN.append(_recm.state)
check("4m a CLOSED campaign (its latest run FINISHED) is not recovered",
      (_recm.state, _recm.campaign_id), (_dl.IDENTITY_NO_EVIDENCE, None))

# 4n: A PRE-ERA-18 SHAPE -- rows carry the campaign, run rows do not.
_dbn, _cpn, _campn = campaign_setup("era17")
_conn = sqlite3.connect(_dbn)
_conn.execute("UPDATE runs SET billing_campaign_id = NULL")
_conn.commit()
_conn.close()
os.remove(record_path(_cpn))
_rn = next_run(_dbn, resumed=True)
_recn = _dl.recover_campaign_identity(_rn, "dig", db_path=_dbn)
_STATES_SEEN.append(_recn.state)
check("4n an era-17 database whose billing rows name the campaign is recovered "
      "from those rows alone", (_recn.state, _recn.campaign_id == _campn),
      (_dl.IDENTITY_RECOVERED, True))

# 4o: INCONSISTENT BY CONFIGURATION.
_dbo, _cpo, _campo = campaign_setup("cross_config")
_ro_other = _dl.start_run_record("batch", db_path=_dbo, fingerprint=_OTHER_FP)
_set_digest(_dbo, _ro_other)
_dl.reserve_billing_attempt(_dbo, attempt_id="xc-1", campaign_id=_campo,
                            run_id=_ro_other, source="stage5", model=_WIRE,
                            input_tokens=1, output_tokens=1, reserved_usd=0.5)
_ro = next_run(_dbo, resumed=True)
_reco = _dl.recover_campaign_identity(_ro, "dig", db_path=_dbo)
_STATES_SEEN.append(_reco.state)
check("4o a campaign whose rows reach a run under ANOTHER configuration is "
      "inconsistent", _reco.state, _dl.IDENTITY_INCONSISTENT)

check("4p every state recovery returned is a member of the closed vocabulary",
      sorted(set(_STATES_SEEN) - set(_dl.IDENTITY_RECOVERY_STATES)), [])
check("4p-i non-degeneracy: four different states were exercised",
      len(set(_STATES_SEEN)) >= 4, True)

# THE STATED RESIDUAL, MEASURED RATHER THAN IMPLIED.
_dbr, _cpr, _campr = campaign_setup("residual")
os.remove(record_path(_cpr))
_rr = next_run(_dbr, resumed=False)
_br = drive(_runner.establish_billing_campaign, False, FIXED_FP, "dig", _rr, _dbr)
check("4q RESIDUAL, PINNED: a MISSING record with NO checkpoint is a NEW "
      "campaign -- indistinguishable here from the run after --fresh",
      (getattr(_br, "decision", None), getattr(_br, "campaign_id", None) != _campr),
      (_runner.CAMPAIGN_DECISION_NEW, True))


# ===========================================================================
section("SECTION 5 -- END TO END: one synthetic campaign, live vs resumed")
# ===========================================================================

_CHILD = os.path.join(_TMP, "closure_child.py")
Path(_CHILD).write_text(r'''
import json, os, sys, types
cfg = json.loads(sys.argv[1])
sys.path.insert(0, cfg["repo"]); sys.path.insert(0, cfg["tests"])
os.environ["ONCOTRIAGE_DEFER_LOCAL_MODELS"] = "1"
from oncotriage import config, paths, spend, run_fingerprint
import _provider_pin
_provider_pin.pin_openai_arm("closure-child", out=lambda *a, **k: None)
from oncotriage import provider_resilience as pr
from oncotriage.agent import deps, evaluation as ev, models
from oncotriage.batch import runner

paths._RESOLVED["data_fhir_path"] = cfg["corpus"] + os.sep
paths._RESOLVED["inferences_path"] = cfg["db"]
paths._RESOLVED["checkpoint_path"] = cfg["cp"] + os.sep
FIXED = cfg["fingerprint"]
run_fingerprint.current = lambda *a, **k: dict(FIXED)
pr.full_jitter_delay = lambda retry_number, rng=None: 0.0
config.SPEND_CAP_ENFORCED = True
config.SPEND_CAP_USD = cfg["cap"]
WIRE = config.matching_wire_model()

def dump(**kw):
    with open(cfg["out"], "w") as fh:
        json.dump(kw, fh)

class Usage:
    def __init__(self, p, c):
        self.prompt_tokens, self.completion_tokens = p, c
        self.completion_tokens_details = None

def response(model, p, c):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(
            content="{}", refusal=None), finish_reason="stop")],
        usage=Usage(p, c), model=model)

class Client:
    def __init__(self):
        self.calls = 0
        self.chat = types.SimpleNamespace(completions=self)
        self.embeddings = types.SimpleNamespace(create=self.embed)
    def create(self, **kw):
        self.calls += 1
        user = kw["messages"][1]["content"]
        if user == "priced":
            return response(WIRE, 1000, 100)
        if user == "unpriced":
            return response("closure-unpriced-model", 1000, 100)
        if user == "possibly":
            raise RuntimeError("possibly billed")
        raise RuntimeError("notbilled")
    def embed(self, **kw):
        self.calls += 1
        if kw["input"] == "embed-fail":
            raise RuntimeError("embedding endpoint lost")
        return types.SimpleNamespace(data=[types.SimpleNamespace(embedding=[0.0])],
                                     usage=types.SimpleNamespace(prompt_tokens=9),
                                     model=config.EMBEDDING_MODEL)

CLIENT = Client()
# INSTALLED ONCE, FOR THE WHOLE PROCESS, AND NEVER CLEARED. The first version set
# and cleared it per patient; main() runs patients CONCURRENTLY, so one patient's
# `clear_override` left the other's remaining calls to resolve a REAL client --
# CI bucket A's run recorded one outbound attempt to api.openai.com (refused by
# the tripwire, and the OS sandbox would have refused it too) and 11 stub calls
# where 12 were owed. A stand-in that can be withdrawn mid-run is not a stand-in.
deps.set_override(deps.OPENAI_CLIENT, CLIENT)
ev._classify_matching_failure = lambda exc: pr.verdict_for(
    pr.CATEGORY_CLIENT if "notbilled" in str(exc) else pr.CATEGORY_UNCLASSIFIED)

def entry(fhir_path):
    return {"patient_id": os.path.basename(str(fhir_path)), "status": "error",
            "eligible_matches": 0, "near_misses": 0, "not_evaluable": 0,
            "total_time": 0.01, "timestamp": "2026-09-13T00:00:00",
            "error": "planted by the harness", "is_resample": False}

class Tracking:
    def start_run(self, **k): pass
    def log_run_metrics(self, *a, **k): pass
    def end_run(self, **k): pass

runner.build_bm25_index_from_qdrant = lambda *a, **k: (object(), ["NCT1"])
runner.build_matching_graph = lambda *a, **k: object()
runner.tracking = Tracking()
runner.run_resample = lambda **k: None
OBSERVED = {}

def patient(fhir_path=None, graph=None, is_resample=False, run_id=None,
            db_path=None):
    if cfg["mode"] == "campaign":
        for text in ("embed-ok", "embed-fail"):
            try:
                models.get_embedding(text)
            except Exception:
                pass
        for user in ("priced", "unpriced", "possibly", "notbilled"):
            try:
                ev.call_matching_model("system prompt", user)
            except Exception:
                pass
    elif not OBSERVED:
        seed = spend.SPEND_LEDGER.seeded
        OBSERVED.update(
            seed=seed._asdict(), describe=spend.describe_seed(seed),
            remaining=spend.remaining(spend.SPEND_SOURCE_STAGE5),
            report=spend.report_lines(),
            campaign_id=spend.BILLING_RECORD.installed_sink().campaign_id)
    return entry(fhir_path)

runner.process_patient = patient
runner.main()
snap = spend.BILLING_RECORD.liability_snapshot()
if cfg["mode"] == "campaign":
    dump(measured=spend.SPEND_LEDGER.measured, total=spend.SPEND_LEDGER.total,
         remaining=spend.remaining(spend.SPEND_SOURCE_STAGE5),
         tally={k: list(v) for k, v in snap.items()},
         report=spend.report_lines(), calls=CLIENT.calls,
         faults=dict(spend.SPEND_LEDGER_FAULTS))
else:
    dump(**OBSERVED)
''', encoding="utf-8")


def child(mode, *, db, cp, corpus, cap, timeout=240):
    out = os.path.join(cp, f"{mode}.json")
    if os.path.exists(out):
        os.remove(out)
    cfg = {"mode": mode, "repo": _REPO, "tests": _TESTS, "db": db, "cp": cp,
           "corpus": corpus, "cap": cap, "out": out, "fingerprint": FIXED_FP}
    env = dict(os.environ)
    _control_harness.isolate_qdrant(env)
    env["ONCOTRIAGE_DEFER_LOCAL_MODELS"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run([sys.executable, _CHILD, json.dumps(cfg)],
                          capture_output=True, text=True, timeout=timeout,
                          env=env, cwd=_TMP)
    data = json.loads(Path(out).read_text()) if os.path.exists(out) else None
    if data is None:
        print("\n".join((proc.stdout + proc.stderr).splitlines()[-40:]))
    return proc, data


def report_line(lines, prefix):
    return next((l.strip() for l in (lines or ())
                 if l.strip().startswith(prefix)), None)


_CORPUS = os.path.join(_TMP, "corpus5")
os.makedirs(_CORPUS)
for _i in range(2):
    Path(os.path.join(_CORPUS, f"patient{_i}.json")).write_text(
        json.dumps({"resourceType": "Bundle", "entry": []}))
_DB5 = os.path.join(_TMP, "e2e.db")
_CP5 = os.path.join(_TMP, "cp5")
os.makedirs(_CP5)
_E2E_CAP = 100.0

_p1, _d1 = child("campaign", db=_DB5, cp=_CP5, corpus=_CORPUS, cap=_E2E_CAP)
check("5a process 1 ran main() to its end", _p1.returncode, 0)
_camp5 = json.loads(Path(os.path.join(_CP5, _runner.CAMPAIGN_RECORD_FILENAME))
                    .read_text())["campaign_id"] if os.path.exists(
    os.path.join(_CP5, _runner.CAMPAIGN_RECORD_FILENAME)) else None
_dur5 = drive(_dl.campaign_billing_total, _camp5, db_path=_DB5)
check("5b non-degeneracy: twelve wire attempts across two patients, zero "
      "successes (no checkpoint), and a positive durable total",
      (at(_d1, "calls"), getattr(_dur5, "attempts", None),
       os.path.exists(os.path.join(_CP5, _runner.CHECKPOINT_FILENAME)),
       getattr(_dur5, "usd", 0) > 0), (12, 12, False, True))

_p2, _d2 = child("observe", db=_DB5, cp=_CP5, corpus=_CORPUS, cap=_E2E_CAP)
check("5c process 2 continues the SAME campaign after a zero-success run",
      (_p2.returncode, at(_d2, "campaign_id")), (0, _camp5))

# THE RECONCILIATION, AT THE ACCOUNTING PRECISION.
check("5d *** TOTAL: process 1's live ledger == the durable record == process "
      "2's seed ***",
      (near(at(_d1, "measured"), getattr(_dur5, "usd", None)),
       near(at(at(_d2, "seed"), "usd"), getattr(_dur5, "usd", None))),
      (True, True))
check("5e *** REMAINING: process 1's live remaining == process 2's resumed "
      "remaining ***", near(at(_d1, "remaining"), at(_d2, "remaining")), True)
_dur_by5 = {}
for _o, _n, _u in ro_rows(_DB5, "SELECT outcome, COUNT(*), SUM(settled_usd) FROM "
                                "billing_attempts WHERE state = 'settled' "
                                "GROUP BY outcome"):
    _dur_by5[_o] = (_n, round(_u, 12))
_live_by5 = {k: (v[0], round(v[1], 12)) for k, v in (at(_d1, "tally") or {}).items()
             if k != _spend.LIABILITY_OPEN and v[0]}
check("5f *** SETTLED, per outcome: the live tally equals the durable rows ***",
      _live_by5, _dur_by5)
check("5f-i non-degeneracy: four different outcomes were settled",
      sorted(_dur_by5), ["not_billed", "possibly_billed", "response",
                         "response_unpriced"])
check("5g *** UNRESOLVED: nothing open live, nothing reserved durably, and the "
      "resumed seed says zero unresolved ***",
      (at(at(_d1, "tally"), _spend.LIABILITY_OPEN, "absent")[0]
       if isinstance(at(at(_d1, "tally"), _spend.LIABILITY_OPEN), list) else 0,
       ro_rows(_DB5, "SELECT COUNT(*) FROM billing_attempts "
                     "WHERE state = 'reserved'")[0][0],
       at(at(_d2, "seed"), "unresolved")), (0, 0, 0))
_live_line = report_line(at(_d1, "report"), "remaining campaign")
_resumed_line = report_line(at(_d2, "report"), "remaining campaign")
check("5h *** PRINTED: the 'remaining campaign' line is byte-identical in the "
      "live closing report and the resumed process's report ***",
      (_live_line is not None, _live_line == _resumed_line), (True, True))
check("5h-i ...and the resumed banner prints the durable total to the cent",
      f"${getattr(_dur5, 'usd', 0):.2f}" in str(at(_d2, "describe")), True)
_camps5 = summary(_DB5, "campaign_summary")
check("5i *** SUMMARY GROUPING: the two processes are ONE campaign in "
      "campaign_summary, keyed by the budget's campaign id ***",
      (len(_camps5), list(_camps5["runs"]), list(_camps5["billing_campaign_id"])),
      (1, [2], [_camp5]))
check("5i-i ...while both run rows still read resumed = 0",
      [r[0] for r in ro_rows(_DB5, "SELECT resumed FROM runs ORDER BY id")],
      [0, 0])


# ===========================================================================
section("SECTION 6 -- isolation held")
# ===========================================================================

_pr.full_jitter_delay = _JITTER_START
_paths._RESOLVED.clear()
_paths._RESOLVED.update(_RESOLVED_START)
check("6a the production inferences.db is byte-unchanged",
      digest(_PROD_DB), _PROD_DIGEST_BEFORE)
check("6b every config knob and patched attribute is restored",
      ({k: getattr(config, k) for k in _CONFIG_KEYS if k != "MATCHING_PROVIDER"},
       _ev._classify_matching_failure is _CLASSIFY_START,
       _dl._open_connection is _OPEN_START,
       _dl.settle_billing_attempt is _SETTLE_START),
      ({k: v for k, v in _CONFIG_START.items() if k != "MATCHING_PROVIDER"},
       True, True, True))
check("6c no billing sink is left installed",
      _spend.BILLING_RECORD.installed_sink(), None)
check("6d the spend policy is restored", _spend.policy(), _POLICY_START)
shutil.rmtree(_TMP, ignore_errors=True)
check("6e the scratch tree is gone", os.path.exists(_TMP), False)
_who, _prev, _restored = _provider_pin.release_openai_arm()
check("6f the provider pin is released and both tables restored", _restored,
      True)

print("\n" + "=" * 78)
print(f"RESULTS: {_RESULTS['passed']} passed, {_RESULTS['failed']} failed, "
      f"{_RESULTS['skipped']} skipped")
print("=" * 78)
if _FAILURES:
    for _f in _FAILURES:
        print(f"  - {_f}")
sys.exit(1 if _RESULTS["failed"] else 0)


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Sep 13 2026

@author: ramyalsaffar
"""
