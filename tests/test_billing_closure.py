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
    8. P1c -- a settlement's STORED outcome decides, not its returned one: a
       commit whose acknowledgement was lost is verified landed; an unreadable
       row is handled conservatively and latches; the billing-record banner
       prints a sentence true of each cause. In process and in fresh processes.

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

import ast
import contextlib
import fcntl
import hashlib
import inspect
import json
import shutil
import signal
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


def with_settings_value(key, value, fn):
    """Call fn() with one config attribute set to value, restored after."""
    with settings(**{key: value}):
        return fn()


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

#
# THE EXPECTED AMOUNTS ARE DERIVED HERE, INDEPENDENTLY OF THE OWNER. The limits
# below are TYPED from the documents (AWS and OpenAI model cards, read
# 2026-09-14) rather than read out of config.STAGE5_ATTEMPT_LIMITS, and the
# arithmetic is re-done from PRICING_CONFIG's rates; the owner is then
# required to agree. A dollar literal pins each shipped result as well.
# ONE COLUMN IS NOT FROM A DOCUMENT: the Sonnet 4.6 long-context multipliers
# (2.0 / 1.5) are the ASSUMED ceiling. No AWS document states a long-context
# price for that model (R1b, 2026-09-14), so for the Sonnet rows the agreement
# below is agreement with an assumption, not with a proven ceiling. 9q pins
# the rates to the published prices and the label to "ASSUMED".

_DOC_LIMITS = {
    # model: (context window, max output, long-ctx input x, long-ctx output x,
    #         cache-write multiplier or None when the pricing row maps TTLs)
    "us.anthropic.claude-sonnet-4-6": (1_000_000, 64_000, 2.0, 1.5, None),
    "global.anthropic.claude-sonnet-4-6": (1_000_000, 64_000, 2.0, 1.5, None),
    "gpt-5.6-terra": (1_050_000, 128_000, 2.0, 1.5, 1.25),
    "us.openai.gpt-5.6-terra": (1_000_000, 128_000, 2.0, 1.5, 1.25),
}


def _indep_bound(model, requested_out, attempts=1, ttl="5m"):
    window, max_out, long_in, long_out, cw_mult = _DOC_LIMITS[model]
    row = config.PRICING_CONFIG["models"][model]
    rates = [row["input"], row.get("cache_read", 0.0)]
    if "cache_write" in row:
        cw = row["cache_write"]
        rates.append(cw[ttl] if ttl in cw else max(cw.values()))
    if cw_mult is not None:
        rates.append(row["input"] * cw_mult)
    in_tok = window * attempts
    out_tok = min(requested_out, max_out) * attempts
    return (in_tok, out_tok,
            (in_tok * max(rates) * long_in
             + out_tok * row["output"] * long_out) / 1e6)


# THE STAGE 5 RESERVATION IS THE DOCUMENTED-LIMIT BOUND (R1). It used to be the
# pacer's chars/3 input estimate plus the output ceiling -- $0.38 on this file's
# probe prompt -- which a real answer can exceed. Every expectation below that
# reads _RESERVE_S5 now reads the independent derivation, and the check pins it.
_RESERVE_S5 = _indep_bound(_WIRE, config.MATCHING_MAX_TOKENS,
                           config.matching_sdk_attempts_per_call())[2]
_S5_BOUND = config.stage5_attempt_bound(
    _WIRE, config.MATCHING_MAX_TOKENS, config.matching_sdk_attempts_per_call())
check("1-bound this file dispatches to gpt-5.6-terra (the pinned OpenAI arm); "
      "its Stage 5 reservation is the documented-limit bound, $5.826, derived "
      "independently and equal to the owner's",
      (_WIRE, near(_RESERVE_S5, 5.826), near(_S5_BOUND["usd"], _RESERVE_S5)),
      ("gpt-5.6-terra", True, True))


def _open_s5(db, attempt_id, campaign_id, run_id):
    """An UNRESOLVED Stage 5 reservation of the shape the shipped code now
    writes: at the bound, with its basis marker. A resume refuses an unmarked
    Stage 5 reservation by name (R1), so a recovery fixture that fabricated one
    at an arbitrary amount would test that refusal instead of the recovery."""
    return _dl.reserve_billing_attempt(
        db, attempt_id=attempt_id, campaign_id=campaign_id, run_id=run_id,
        source="stage5", model=_S5_BOUND["model"],
        input_tokens=_S5_BOUND["input_tokens"],
        output_tokens=_S5_BOUND["output_tokens"],
        reserved_usd=_S5_BOUND["usd"],
        note=config.stage5_reservation_note(_S5_BOUND))


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

# A RUN REFUSED BEFORE PAID WORK CARRIES NO BILLING CAMPAIGN (the P3 recovery).
# `establish_billing_campaign` raises before `set_run_billing_campaign_id` for
# every refusal it can decide without a campaign, so a resume it refused has
# `resumed = 1`, no billing campaign and a KILLED status. The older resume rule
# would stitch it onto the billing-era run before it and show a run that billed
# nothing inside a budget it never ran under; the rule attaches only to runs
# that carry no billing campaign either, so it stands alone.
_DB3R = new_db("p3_refused_after_billing_era.db")
_r1 = run_row(_DB3R, FIXED_FP, resumed=False, status="KILLED", bcid="camp-r")
_r2 = run_row(_DB3R, FIXED_FP, resumed=True, status="KILLED", bcid=None)
check("3h a refused resume (resumed = 1, no billing campaign) after a "
      "billing-era run is its OWN campaign, in SQL and in Python",
      (sorted((str(r.run_ids), r.billing_campaign_id if r.billing_campaign_id
               == r.billing_campaign_id else None)
              for r in summary(_DB3R, "campaign_summary").itertuples()),
       _dl.campaign_run_ids(_r2, db_path=_DB3R).run_ids),
      (sorted([(f"{_r1}", "camp-r"), (f"{_r2}", None)]), (_r2,)))
_DB3RC = new_db("p3_refused_control.db")
_rc1 = run_row(_DB3RC, FIXED_FP, resumed=False, status="KILLED", bcid=None)
_rc2 = run_row(_DB3RC, FIXED_FP, resumed=True, status="KILLED", bcid=None)
check("3h-i CONTROL: the same two rows with no billing campaign on the first "
      "DO stitch, so 3h is the billing-campaign filter at work and not a "
      "resume rule that never fires",
      (list(summary(_DB3RC, "campaign_summary")["run_ids"]),
       _dl.campaign_run_ids(_rc2, db_path=_DB3RC).run_ids),
      ([f"{_rc1} -> {_rc2}"], (_rc1, _rc2)))

_pin_mismatch = []
for _db in (_DB3, _DB3C, _DB3D, _DB3E, _DB3R, _DB3RC):
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

_table = drive(_run_health._build_campaign_table,
               summary(_DB3, "campaign_summary"))
_rtable = drive(_run_health._build_run_table, summary(_DB3, "run_summary"))
# GUARDED READS (the P3 recovery): a dropped column is the defect 3f exists to
# catch, and a bare subscript turned it into a KeyError that aborted the whole
# file with no summary -- measured by removing the column in a copy.
check("3f the dashboard's campaign and run tables show the billing campaign",
      (drive(lambda: list(_table["billing campaign"])),
       drive(lambda: sorted(_rtable["billing campaign"]))),
      (["camp-z"], ["camp-z", "camp-z"]))
_caption = " ".join(str(getattr(_run_health, "CAMPAIGN_CAPTION", "")).split())
check("3f-i the campaign caption STATES the billing-campaign rule (a run with a "
      "billing campaign continues the nearest preceding run carrying the same "
      "one, whatever its status or resume flag) and that a zero-success "
      "restart is one campaign; the stale resume-only help is gone",
      ("A run that carries a **billing campaign** continues the nearest "
       "preceding run carrying the same one, whatever that run's status and "
       "whether or not it resumed a checkpoint" in _caption,
       "`resumed` = 0" in _caption,
       "exactly when something was resumed" in Path(
           _run_health.__file__).read_text(encoding="utf-8")),
      (True, True, False))


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

# 4r: THE RESERVATION IS COMMITTED BEFORE THE DISPATCH (the P4 recovery). The
# stand-in provider reads the billing table through an INDEPENDENT read-only
# connection at the instant it is called. Under WAL that connection sees only
# committed transactions, so seeing the row there is seeing it on disk to the
# durability bound -- a reservation still inside an open transaction, or held
# only in memory, reads as nothing.
_DB_R = new_db("p4_reserve_before_dispatch.db")
_R_R = _dl.start_run_record("batch", db_path=_DB_R, fingerprint=FIXED_FP)
_SEEN_AT_DISPATCH = []


def _chat_reads_reservation(kw):
    _SEEN_AT_DISPATCH.append(ro_rows(
        _DB_R, "SELECT state, campaign_id, run_id FROM billing_attempts "
               "WHERE state = 'reserved'"))
    return _chat_response((100, 10))


class _MemorySink:
    """CONTROL: a sink that 'reserves' in memory and writes nothing."""

    def __init__(self):
        self.rows = {}

    def reserve(self, **fields):
        self.rows[fields["attempt_id"]] = fields
        return fields["attempt_id"]

    def settle(self, attempt_id, **fields):
        return _dl.SETTLE_SETTLED


_reserve_client = _Client(chat=_chat_reads_reservation)
deps.set_override(deps.OPENAI_CLIENT, _reserve_client)
try:
    with sink_installed(_DB_R, "camp-r", _R_R):
        _r_out = drive(_ev.call_matching_model, "system prompt", "user prompt")
    _reset_spend_state()
    _spend.BILLING_RECORD.install(_MemorySink())
    try:
        drive(_ev.call_matching_model, "system prompt", "user prompt")
    finally:
        _spend.BILLING_RECORD.clear()
finally:
    deps.clear_override(deps.OPENAI_CLIENT)
    _reset_spend_state()
check("4r *** at the instant the provider is called, an INDEPENDENT connection "
      "already reads the attempt's reservation: it was COMMITTED before the "
      "dispatch ***",
      (_reserve_client.calls, at(_SEEN_AT_DISPATCH, 0)),
      (2, [("reserved", "camp-r", _R_R)]))
check("4r-i CONTROL: a sink that reserves without writing is caught by the same "
      "read -- the independent connection sees NOTHING at dispatch",
      at(_SEEN_AT_DISPATCH, 1), [])
check("4r-ii ...and the real reservation is settled after the response",
      ro_rows(_DB_R, "SELECT state, outcome FROM billing_attempts"),
      [("settled", "response")])

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
    # THE DIRECTORY'S F_FULLFSYNC REFUSED (the P4 recovery). The first version
    # counted this and carried on; it is a refusal now, like the file's.
    _FAIL_FULLFSYNC_ON.add("dir")
    _w_dir_fail = raised(_runner.write_campaign_record, "camp-sync-dir",
                         FIXED_FP, "dig")
    _left_dir = sorted(os.listdir(_CP4))
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
    check("4f-i ...and the refusal names the stage that failed",
          "syncing the temp file" in str(_w_fail), True)
    check("4f-ii *** a DIRECTORY F_FULLFSYNC refusal also REFUSES by name, "
          "leaving the renamed record and no temp file (it used to be counted "
          "and ignored) ***",
          (getattr(_w_dir_fail, "reason", None),
           "syncing the directory after the rename" in str(_w_dir_fail),
           _left_dir),
          (_runner.CAMPAIGN_REFUSAL_RECORD_UNWRITABLE, True,
           [_runner.CAMPAIGN_RECORD_FILENAME]))
else:
    skip("4f F_FULLFSYNC refusal control", f"{sys.platform} has no F_FULLFSYNC")
    skip("4f-i F_FULLFSYNC refusal stage", f"{sys.platform} has no F_FULLFSYNC")
    skip("4f-ii directory F_FULLFSYNC refusal",
         f"{sys.platform} has no F_FULLFSYNC")
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

# ── 4q: A MISSING RECORD WITH NO CHECKPOINT (P4b) ─────────────────────────────
#
# THIS WAS PINNED AS A RESIDUAL AND IT WAS THE DEFECT. `resumed` means the
# checkpoint handed the run a COMPLETED patient, and recovery ran only when the
# record was corrupt or `resumed` was true -- so a campaign whose every patient
# failed, with its record deleted, started a NEW campaign at $0 while its
# settled charges and unresolved reservations sat in the billing record.
# Measured in two fresh processes before the fix: $1.65 of liabilities, seed
# $0.00. Recovery now runs whenever the record is missing, and `--fresh` stays
# separate through the watermark it records beside the checkpoint.


def billing_census(db):
    return at(drive(ro_rows, db, "SELECT COUNT(*), ROUND(COALESCE(SUM("
                                 "COALESCE(settled_usd, reserved_usd)), 0), 9) "
                                 "FROM billing_attempts"), 0)


_dbr, _cpr, _campr = campaign_setup("zero_success")
_open_s5(_dbr, "zero_success-open", _campr, 1)
_census_r = billing_census(_dbr)
os.remove(record_path(_cpr))
_rr = next_run(_dbr, resumed=False)
_br = drive(_runner.establish_billing_campaign, False, FIXED_FP, "dig", _rr, _dbr)
check("4q *** a MISSING record with NO checkpoint (zero completed patients) is "
      "RECOVERED: same campaign, and the budget carries its settled charge AND "
      "its unresolved reservation ***",
      (getattr(_br, "decision", None), getattr(_br, "campaign_id", None) == _campr,
       near(getattr(getattr(_br, "seed", None), "usd", None), 7.076),
       getattr(getattr(_br, "seed", None), "unresolved", None)),
      (_runner.CAMPAIGN_DECISION_RECOVERED, True, True, 1))
check("4q-i non-degeneracy: the billing record held two rows and $7.076 "
      "($1.25 settled + the $5.826 Stage 5 bound) before the recovery",
      _census_r, (2, 7.076))
os.remove(record_path(_cpr))
_rr2 = next_run(_dbr, resumed=False)
_br2 = drive(_runner.establish_billing_campaign, False, FIXED_FP, "dig", _rr2,
             _dbr)
check("4q-ii *** REPEATED recovery duplicates nothing: the same campaign, the "
      "same seed, and the billing record unchanged row for row ***",
      (getattr(_br2, "campaign_id", None) == _campr,
       near(getattr(getattr(_br2, "seed", None), "usd", None), 7.076),
       billing_census(_dbr)),
      (True, True, _census_r))

# --fresh CLOSES WHAT EXISTS, DURABLY, AND ONLY WHAT EXISTS -- BY IDENTITY (P4c).
_fresh_closed = drive(_runner.record_fresh_start, db_path=_dbr)
_fresh_marker = os.path.join(_cpr, _runner.FRESH_MARKER_FILENAME)
_marker_json = drive(lambda: json.loads(Path(_fresh_marker).read_text()))
_started_r = dict(ro_rows(_dbr, "SELECT id, started_at FROM runs"))
_first_r = min(_started_r)
check("4q-iii --fresh records the campaign it closed BY IDENTITY, with every "
      "run it had touched as (run id, started_at), in a version-2 marker "
      "written beside the checkpoint",
      (at(_marker_json, "version"),
       sorted(at(_marker_json, "closed_campaigns") or {}),
       at(at(_marker_json, "closed_campaigns"), _campr),
       _fresh_closed == at(_marker_json, "closed_campaigns")),
      (2, [_campr],
       [[r, _started_r.get(r)] for r in sorted({_first_r, _rr, _rr2})], True))
os.remove(record_path(_cpr))
_rf = next_run(_dbr, resumed=False)
_bf = drive(_runner.establish_billing_campaign, False, FIXED_FP, "dig", _rf, _dbr)
check("4q-iv *** after --fresh, a missing record starts a NEW campaign: the "
      "campaign it closed is not recovered ***",
      (getattr(_bf, "decision", None), getattr(_bf, "campaign_id", None) != _campr,
       near(getattr(getattr(_bf, "seed", None), "usd", None), 0.0)),
      (_runner.CAMPAIGN_DECISION_NEW, True, True))
_camp_after = getattr(_bf, "campaign_id", None)
_dl.reserve_billing_attempt(_dbr, attempt_id="after-fresh-1",
                            campaign_id=_camp_after, run_id=_rf, source="stage5",
                            model=_WIRE, input_tokens=1, output_tokens=1,
                            reserved_usd=0.75)
_dl.settle_billing_attempt(_dbr, "after-fresh-1", outcome="response",
                           settled_usd=0.75)
_dl.finalize_run_record(_rf, "FAILED", db_path=_dbr)
os.remove(record_path(_cpr))
_ra = next_run(_dbr, resumed=False)
_ba = drive(_runner.establish_billing_campaign, False, FIXED_FP, "dig", _ra, _dbr)
check("4q-v *** a campaign started AFTER --fresh is still recovered when its "
      "record goes missing -- the marker closes only what existed ***",
      (getattr(_ba, "decision", None), getattr(_ba, "campaign_id", None),
       near(getattr(getattr(_ba, "seed", None), "usd", None), 0.75)),
      (_runner.CAMPAIGN_DECISION_RECOVERED, _camp_after, True))
_prior_closed = dict(at(_marker_json, "closed_campaigns") or {})
_prior_closed["camp-closed-in-another-copy"] = [[7, "2026-01-01T00:00:00"]]
Path(_fresh_marker).write_text(json.dumps(
    {"version": _runner.FRESH_MARKER_VERSION,
     "closed_campaigns": _prior_closed}))
_again = drive(_runner.record_fresh_start, db_path=_dbr)
check("4q-vi *** a LATER --fresh keeps every closure an earlier one recorded "
      "-- including one for a campaign this database does not hold -- and adds "
      "the campaigns the database holds now ***",
      (sorted(_again) if isinstance(_again, dict) else _again,
       at(_again, "camp-closed-in-another-copy"),
       at(_again, _campr) == at(_prior_closed, _campr)),
      (sorted([_campr, _camp_after, "camp-closed-in-another-copy"]),
       [[7, "2026-01-01T00:00:00"]], True))
Path(_fresh_marker).write_text("{not json")
os.remove(record_path(_cpr))
_ru = next_run(_dbr, resumed=False)
_eu = raised(_runner.establish_billing_campaign, False, FIXED_FP, "dig", _ru, _dbr)
check("4q-vii an UNREADABLE --fresh marker beside a missing record is REFUSED by "
      "name, and nothing is written",
      (getattr(_eu, "reason", None), _runner.FRESH_MARKER_FILENAME in str(_eu),
       os.path.exists(record_path(_cpr))),
      (_runner.CAMPAIGN_REFUSAL_RECORD_UNREADABLE, True, False))
_abs_db = os.path.join(_TMP, "absent", "never.db")
check("4q-viii the closure snapshot of a database that does not exist is empty "
      "and does not create it",
      (drive(getattr(_dl, "campaign_closure_snapshot", None), _abs_db),
       os.path.exists(os.path.dirname(_abs_db))), ({}, False))

# AMBIGUITY STILL REFUSES WITHOUT A CHECKPOINT.
_dbq, _cpq, _campq = campaign_setup("ambiguous_zero")
_rq_other = _dl.start_run_record("batch", db_path=_dbq, fingerprint=FIXED_FP)
_set_digest(_dbq, _rq_other)
_dl.set_run_billing_campaign_id(_rq_other, "camp-other-zero", db_path=_dbq)
_dl.reserve_billing_attempt(_dbq, attempt_id="oz-1", campaign_id="camp-other-zero",
                            run_id=_rq_other, source="stage5", model=_WIRE,
                            input_tokens=1, output_tokens=1, reserved_usd=2.0)
_dl.finalize_run_record(_rq_other, "KILLED", db_path=_dbq)
os.remove(record_path(_cpq))
_rq = next_run(_dbq, resumed=False)
_eq = raised(_runner.establish_billing_campaign, False, FIXED_FP, "dig", _rq, _dbq)
check("4q-ix *** two open campaigns and NO checkpoint are REFUSED by name, not "
      "guessed at and not replaced by a new campaign ***",
      (getattr(_eq, "reason", None), _campq in str(_eq),
       "camp-other-zero" in str(_eq), os.path.exists(record_path(_cpq))),
      (_runner.CAMPAIGN_REFUSAL_IDENTITY_UNESTABLISHED, True, True, False))

# THE ENTRY POINT RECORDS THE WATERMARK BEFORE IT DISCARDS ANYTHING.
_guard_tree = ast.parse(Path(os.path.join(_REPO, "25- Batch Runner.py"))
                        .read_text(encoding="utf-8"))
_fresh_calls = []
for _node in ast.walk(_guard_tree):
    if (isinstance(_node, ast.If) and isinstance(_node.test, ast.Attribute)
            and _node.test.attr == "fresh"):
        for _sub in ast.walk(_node):
            if isinstance(_sub, ast.Call) and isinstance(_sub.func, ast.Name):
                _fresh_calls.append((_sub.lineno, _sub.col_offset, _sub.func.id))
_fresh_order = [n for _, _, n in sorted(_fresh_calls)
                if n in ("record_fresh_start", "clear_checkpoint")]
check("4q-x *** the entry point's --fresh records the watermark BEFORE "
      "clear_checkpoint() removes the identity record ***",
      _fresh_order, ["record_fresh_start", "clear_checkpoint"])

# ── 4y..4zf: THE --fresh MARKER BY IDENTITY (P4c) ─────────────────────────────
#
# THE P4b MARKER REMEMBERED A RUN NUMBER, AND RUN NUMBERS ARE REUSED. Restore an
# older copy of the database and its new runs take numbers at or below the
# marker's cutoff, so a campaign billed after the restore was skipped and the
# restart began at $0. Measured in fresh processes before the fix: $1.25 settled
# and $0.40 reserved at run 21 under a marker written at run 100, seed $0.00.


def census(db):
    return at(drive(ro_rows, db, "SELECT COUNT(*), ROUND(COALESCE(SUM(CASE WHEN "
                                 "state = 'settled' THEN settled_usd ELSE "
                                 "reserved_usd END), 0), 9) FROM "
                                 "billing_attempts"), 0)


def restore_copy(src, dst):
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(dst + suffix):
            os.remove(dst + suffix)
    shutil.copyfile(src, dst)


def write_marker(cp, payload):
    Path(os.path.join(cp, _runner.FRESH_MARKER_FILENAME)).write_text(
        json.dumps(payload) if not isinstance(payload, str) else payload)


def bill(db, campaign, run_id, tag, settled, reserved):
    _dl.reserve_billing_attempt(db, attempt_id=f"{tag}-s", campaign_id=campaign,
                                run_id=run_id, source="stage5", model=_WIRE,
                                input_tokens=1, output_tokens=1,
                                reserved_usd=settled)
    _dl.settle_billing_attempt(db, f"{tag}-s", outcome="response",
                               settled_usd=settled)
    if reserved:
        _open_s5(db, f"{tag}-o", campaign, run_id)


def seed_of(budget):
    seed = field(budget, "seed")
    return (field(budget, "decision"), field(budget, "campaign_id"),
            field(seed, "usd"), field(seed, "unresolved"))


# 4y: THE RESTORED OLDER DATABASE, BOTH CHARGE CLASSES PRESENT.
_dby, _cpy, _campy_old = campaign_setup("restored")      # run 1, $1.25, FAILED
_older_y = os.path.join(_TMP, "restored_older.db")
_src_y, _dst_y = sqlite3.connect(_dby), sqlite3.connect(_older_y)
_src_y.backup(_dst_y)
_src_y.close()
_dst_y.close()
for _ in range(4):
    _dl.finalize_run_record(next_run(_dby, resumed=False), "FINISHED",
                            db_path=_dby)
_latest_y = at(at(ro_rows(_dby, "SELECT MAX(id) FROM runs"), 0), 0)
_closed_y = drive(_runner.record_fresh_start, db_path=_dby)
_runner.clear_checkpoint()
restore_copy(_older_y, _dby)
_ry1 = next_run(_dby, resumed=False)
_by1 = drive(_runner.establish_billing_campaign, False, FIXED_FP, "dig", _ry1,
             _dby)
_campy_new = field(_by1, "campaign_id")
bill(_dby, _campy_new, _ry1, "restored-new", 0.75, True)
_dl.finalize_run_record(_ry1, "FAILED", db_path=_dby)
check("4y non-degeneracy: --fresh closed the old campaign by identity, the "
      "restored copy holds only its run 1, and the NEW campaign billed at a run "
      "number the newer database had already used, with a settled charge AND an "
      "unresolved reservation",
      (sorted(_closed_y) if isinstance(_closed_y, dict) else _closed_y,
       field(_by1, "decision"), _ry1 <= _latest_y, census(_dby)),
      ([_campy_old], _runner.CAMPAIGN_DECISION_NEW, True, (3, 7.826)))
os.remove(record_path(_cpy))
_ry2 = next_run(_dby, resumed=False)
_by2 = drive(_runner.establish_billing_campaign, False, FIXED_FP, "dig", _ry2,
             _dby)
check("4y-i *** THE P4c DEFECT: after the restore the missing record is "
      "RECOVERED -- the new campaign, its settled charge AND its unresolved "
      "reservation -- and the closed old campaign's $1.25 is not in it ***",
      (field(_by2, "decision"), field(_by2, "campaign_id") == _campy_new,
       near(field(field(_by2, "seed"), "usd"), 6.576),
       field(field(_by2, "seed"), "unresolved")),
      (_runner.CAMPAIGN_DECISION_RECOVERED, True, True, 1))
_census_y = census(_dby)
os.remove(record_path(_cpy))
_ry3 = next_run(_dby, resumed=False)
_by3 = drive(_runner.establish_billing_campaign, False, FIXED_FP, "dig", _ry3,
             _dby)
check("4y-ii *** REPEATED recovery adds no charge: the same campaign, the same "
      "seed, and the billing record unchanged row for row ***",
      (field(_by3, "campaign_id") == _campy_new,
       near(field(field(_by3, "seed"), "usd"), 6.576), census(_dby)),
      (True, True, _census_y))

# 4z: A CRASH BETWEEN THE MARKER AND THE CHECKPOINT REMOVAL.
_dbz, _cpz, _campz = campaign_setup("crash_fresh")        # run 1, $1.25, record
drive(_runner.record_fresh_start, db_path=_dbz)           # ... and then it dies
_rz1 = next_run(_dbz, resumed=False)
_bz1 = drive(_runner.establish_billing_campaign, False, FIXED_FP, "dig", _rz1,
             _dbz)
check("4z the marker was persisted and the process died before clearing: the "
      "next ordinary run CONTINUES the campaign the record still names",
      (field(_bz1, "decision"), field(_bz1, "campaign_id") == _campz,
       near(field(field(_bz1, "seed"), "usd"), 1.25),
       os.path.exists(os.path.join(_cpz, _runner.FRESH_MARKER_FILENAME))),
      (_runner.CAMPAIGN_DECISION_CONTINUED, True, True, True))
bill(_dbz, _campz, _rz1, "post-crash", 0.50, True)
_dl.finalize_run_record(_rz1, "FAILED", db_path=_dbz)
os.remove(record_path(_cpz))
_rz2 = next_run(_dbz, resumed=False)
_bz2 = drive(_runner.establish_billing_campaign, False, FIXED_FP, "dig", _rz2,
             _dbz)
check("4z-i *** the campaign touched a run its closure does not name, so the "
      "closure no longer covers it: a lost record RECOVERS it with every charge, "
      "pre- and post-crash, including the reservation ***",
      (field(_bz2, "decision"), field(_bz2, "campaign_id") == _campz,
       near(field(field(_bz2, "seed"), "usd"), 7.576),
       field(field(_bz2, "seed"), "unresolved")),
      (_runner.CAMPAIGN_DECISION_RECOVERED, True, True, 1))
_dbz3, _cpz3, _campz3 = campaign_setup("crash_fresh_nothing_since")
drive(_runner.record_fresh_start, db_path=_dbz3)
os.remove(record_path(_cpz3))
_rz3 = next_run(_dbz3, resumed=False)
_bz3 = drive(_runner.establish_billing_campaign, False, FIXED_FP, "dig", _rz3,
             _dbz3)
check("4z-ii ...while a crashed --fresh whose record is then lost, with nothing "
      "billed since, still closes the campaign: a NEW campaign at $0, which is "
      "the separation the operator asked for",
      (field(_bz3, "decision"), field(_bz3, "campaign_id") != _campz3,
       near(field(field(_bz3, "seed"), "usd"), 0.0)),
      (_runner.CAMPAIGN_DECISION_NEW, True, True))

# 4za: A RUN IS (id, started_at), NOT ITS NUMBER.
_dba4, _cpa4, _campa4 = campaign_setup("reused_run_id")
os.remove(record_path(_cpa4))
_ra4 = next_run(_dba4, resumed=False)
_run1_a4, _started1_a4 = at(ro_rows(_dba4, "SELECT id, started_at FROM runs "
                                           "ORDER BY id LIMIT 1"), 0)
_same_a4 = _dl.recover_campaign_identity(
    _ra4, "dig", db_path=_dba4,
    closed_campaigns={_campa4: [[_run1_a4, _started1_a4]]})
_other_a4 = _dl.recover_campaign_identity(
    _ra4, "dig", db_path=_dba4,
    closed_campaigns={_campa4: [[_run1_a4, "1999-01-01T00:00:00"]]})
check("4za a closure naming the campaign's run by (id, started_at) closes it",
      (_same_a4.state, _same_a4.campaign_id), (_dl.IDENTITY_NO_EVIDENCE, None))
check("4za-i *** a closure naming the SAME run NUMBER with a different "
      "started_at -- a restored database reusing the number -- does NOT close "
      "it: recovered ***",
      (_other_a4.state, _other_a4.campaign_id),
      (_dl.IDENTITY_RECOVERED, _campa4))
_bad_closures = ("not a map", {_campa4: [[True, None]]}, {"": []},
                 {_campa4: [[1]]}, {_campa4: [[-1, None]]},
                 {_campa4: [[1, 12345]]}, {_campa4: "1,x"})
check("4za-ii a closure map of the wrong shape is UNREADABLE, never applied",
      [_dl.recover_campaign_identity(_ra4, "dig", db_path=_dba4,
                                     closed_campaigns=b).state
       for b in _bad_closures],
      [_dl.IDENTITY_UNREADABLE] * len(_bad_closures))

# 4zb: LEGACY (version-1) MARKERS ARE NEVER CONVERTED.
_V1 = getattr(_runner, "FRESH_MARKER_LEGACY_VERSION", 1)
_UNVERIFIABLE = getattr(_runner, "CAMPAIGN_REFUSAL_FRESH_MARKER_UNVERIFIABLE",
                        "<absent>")
# On the restored database of 4y: ignoring a cutoff, the old campaign and the
# new one are both open (ambiguous); applying cutoff 5, both are skipped (no
# evidence). The two readings disagree, so the cutoff alone would decide.
_paths._RESOLVED["checkpoint_path"] = _cpy + os.sep
os.remove(record_path(_cpy))
write_marker(_cpy, {"version": _V1, "closed_through_run_id": _latest_y})
_census_b = census(_dby)
_rb1 = next_run(_dby, resumed=False)
_eb1 = raised(_runner.establish_billing_campaign, False, FIXED_FP, "dig", _rb1,
              _dby)
check("4zb *** a version-1 marker whose cutoff DECIDES the answer is refused by "
      "name before any billed call: no identity record written, nothing billed, "
      "and both readings named ***",
      (getattr(_eb1, "reason", None), os.path.exists(record_path(_cpy)),
       census(_dby), "ambiguous" in str(_eb1) and "no_evidence" in str(_eb1)),
      (_UNVERIFIABLE, False, _census_b, True))
_dbm, _cpm, _campm = campaign_setup("legacy_moot")          # run 1
_rm1 = next_run(_dbm, resumed=False)
drive(_runner.establish_billing_campaign, False, FIXED_FP, "dig", _rm1, _dbm)
_dl.finalize_run_record(_rm1, "FAILED", db_path=_dbm)       # campaign runs 1, 2
os.remove(record_path(_cpm))
write_marker(_cpm, {"version": _V1, "closed_through_run_id": 1})
_rm2 = next_run(_dbm, resumed=False)
_bm2 = drive(_runner.establish_billing_campaign, False, FIXED_FP, "dig", _rm2,
             _dbm)
check("4zb-i a version-1 marker whose cutoff decides NOTHING (the campaign has a "
      "run above it, so both readings recover it) does not block: recovered",
      (field(_bm2, "decision"), field(_bm2, "campaign_id") == _campm),
      (_runner.CAMPAIGN_DECISION_RECOVERED, True))
write_marker(_cpm, {"version": _V1, "closed_through_run_id": 99})
_rm3 = next_run(_dbm, resumed=False)
_bm3 = drive(_runner.establish_billing_campaign, False, FIXED_FP, "dig", _rm3,
             _dbm)
check("4zb-ii with the identity record PRESENT the marker is not consulted, "
      "even a version-1 marker whose cutoff would disagree: continued",
      (field(_bm3, "decision"), field(_bm3, "campaign_id") == _campm),
      (_runner.CAMPAIGN_DECISION_CONTINUED, True))
_paths._RESOLVED["checkpoint_path"] = _cpy + os.sep
write_marker(_cpy, {"version": _V1, "closed_through_run_id": _latest_y})
_conv = drive(_runner.record_fresh_start, db_path=_dby)
_conv_json = drive(lambda: json.loads(Path(os.path.join(
    _cpy, _runner.FRESH_MARKER_FILENAME)).read_text()))
check("4zb-iii *** --fresh over a version-1 marker writes version-2 closures "
      "from the CURRENT database by identity, keeps the old cutoff only as "
      "provenance, and does not turn the cutoff into closures ***",
      (at(_conv_json, "version"), at(_conv_json, "superseded_legacy_marker"),
       sorted(_conv) if isinstance(_conv, dict) else _conv),
      (2, {"version": 1, "closed_through_run_id": _latest_y},
       sorted([_campy_old, _campy_new])))
_conv2 = drive(_runner.record_fresh_start, db_path=_dby)
check("4zb-iv ...and a later --fresh carries that provenance forward",
      at(drive(lambda: json.loads(Path(os.path.join(
          _cpy, _runner.FRESH_MARKER_FILENAME)).read_text())),
         "superseded_legacy_marker"),
      {"version": 1, "closed_through_run_id": _latest_y})

# 4zc: MALFORMED MARKERS, AND --fresh OVER AN UNREADABLE ONE.
_malformed = ([], {"version": True, "closed_through_run_id": 1},
              {"version": 1, "closed_through_run_id": -1},
              {"version": 1, "closed_through_run_id": "5"},
              {"version": 2}, {"version": 3, "closed_campaigns": {}},
              {"version": 2, "closed_campaigns": {"c": [[True, None]]}},
              {"version": 2, "closed_campaigns": {},
               "superseded_legacy_marker": "cutoff 5"})
_paths._RESOLVED["checkpoint_path"] = _cpm + os.sep
_mal_out = []
for _payload in _malformed:
    write_marker(_cpm, _payload)
    _e = raised(getattr(_runner, "read_fresh_marker", None))
    _mal_out.append((getattr(_e, "reason", None),
                     _runner.FRESH_MARKER_FILENAME in str(_e)))
check("4zc every malformed marker (wrong type, bool or negative or text cutoff, "
      "missing closures, unknown version, bad closure pair, bad provenance) is "
      "REFUSED by name when read",
      _mal_out, [(_runner.CAMPAIGN_REFUSAL_RECORD_UNREADABLE, True)]
      * len(_malformed))
write_marker(_cpm, {"version": 2, "closed_campaigns": {}})
check("4zc-i non-degeneracy: a well-formed empty version-2 marker reads",
      field(drive(getattr(_runner, "read_fresh_marker", None)), "version"), 2)
write_marker(_cpm, "{garbage")
_faults_before = _runner.CHECKPOINT_FAULTS.get("fresh_marker_unreadable", 0)
_over = drive(_runner.record_fresh_start, db_path=_dbm)
_mdir = os.listdir(_cpm)
_copies = [n for n in _mdir if n.startswith(_runner.FRESH_MARKER_FILENAME)
           and n != _runner.FRESH_MARKER_FILENAME]
check("4zc-ii --fresh over an UNREADABLE marker copies it aside (bytes kept), "
      "counts it, and writes a version-2 marker naming the copy",
      (len(_copies) == 1,
       drive(lambda: Path(os.path.join(_cpm, _copies[0])).read_text())
       if _copies else None,
       _runner.CHECKPOINT_FAULTS.get("fresh_marker_unreadable", 0)
       - _faults_before,
       at(drive(lambda: json.loads(Path(os.path.join(
           _cpm, _runner.FRESH_MARKER_FILENAME)).read_text())), "version"),
       isinstance(_over, dict) and _campm in _over),
      (True, "{garbage", 1, 2, True))

# 4zd: A CAMPAIGN THE RECORD NAMES AND THE DATABASE DOES NOT (its stamp failed).
_dbd, _cpd, _campd = campaign_setup("record_only")
_rec_d = json.loads(Path(record_path(_cpd)).read_text())
_rec_d["campaign_id"] = "camp-record-only"
Path(record_path(_cpd)).write_text(json.dumps(_rec_d))
_closed_d = drive(_runner.record_fresh_start, db_path=_dbd)
check("4zd --fresh also closes the campaign a readable identity record names "
      "when the database holds no row for it",
      (at(_closed_d, "camp-record-only"), _campd in (_closed_d or {})),
      ([], True))


# ── ITEM 3 (P4b): AN IDENTITY-WRITE FAILURE WHILE RECOVERING A BILLED CAMPAIGN ──
#
# The refusal used to say a record left behind "names a campaign with no spend
# and is safe to continue or remove". A RECOVERED campaign already holds
# charges; removing its record to "start over" was advice to forget them.
_RETIRED_CLAIMS = ("no spend", "safe to continue or remove")
_SYNC_START = _runner._durable_sync
for _where in ("file", "dir"):
    _dbw, _cpw, _campw = campaign_setup(f"unwritable_{_where}")
    _census_w = billing_census(_dbw)
    os.remove(record_path(_cpw))
    _rw = next_run(_dbw, resumed=False)

    def _failing_sync(fd, *, directory=False, _w=_where):
        if directory == (_w == "dir"):
            raise OSError(5, f"planted {_w} sync failure")
        return _SYNC_START(fd, directory=directory)

    _runner._durable_sync = _failing_sync
    try:
        _ew = raised(_runner.establish_billing_campaign, False, FIXED_FP, "dig",
                     _rw, _dbw)
    finally:
        _runner._durable_sync = _SYNC_START
    _lines_w = str(drive(lambda: "\n".join(_ew.lines())))
    check(f"4w-{_where} *** recovering an ALREADY-BILLED campaign, a {_where} "
          f"sync failure REFUSES by name; nothing is billed and the run row "
          f"carries no campaign ***",
          (getattr(_ew, "reason", None), billing_census(_dbw),
           at(at(drive(ro_rows, _dbw, "SELECT billing_campaign_id FROM runs "
                                      "WHERE id = ?", (_rw,)), 0), 0)),
          (_runner.CAMPAIGN_REFUSAL_RECORD_UNWRITABLE, _census_w, None))
    check(f"4w-{_where}-i *** the printed refusal no longer calls the campaign "
          f"spend-free or safe to remove: it says it may hold charges, that the "
          f"next run carries them, and that --fresh is the deliberate new start ***",
          ([c for c in _RETIRED_CLAIMS if c in _lines_w],
           "ALREADY HOLD CHARGES" in _lines_w,
           "Do not remove the record to start over" in _lines_w,
           "--fresh starts a new campaign deliberately" in _lines_w,
           "NOTHING HAS BEEN BILLED BY THIS RUN." in _lines_w),
          ([], True, True, True, True))
    _rw2 = next_run(_dbw, resumed=False)
    _bw2 = drive(_runner.establish_billing_campaign, False, FIXED_FP, "dig", _rw2,
                 _dbw)
    check(f"4w-{_where}-ii *** the claim is true: the next run continues THAT "
          f"campaign with its charge -- "
          f"{'from the record left in place' if _where == 'dir' else 'recovered from the billing record'} "
          f"-- and duplicates nothing ***",
          (getattr(_bw2, "decision", None), getattr(_bw2, "campaign_id", None)
           == _campw, near(getattr(getattr(_bw2, "seed", None), "usd", None),
                           1.25), billing_census(_dbw)),
          ((_runner.CAMPAIGN_DECISION_CONTINUED if _where == "dir"
            else _runner.CAMPAIGN_DECISION_RECOVERED), True, True, _census_w))
check("4w-restore the runner's durable sync was restored, by identity",
      _runner._durable_sync is _SYNC_START, True)
_paths._RESOLVED["checkpoint_path"] = _CP4 + os.sep


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
# R1: the largest usage the documented limits admit for one trial attempt.
_BOUND = config.stage5_attempt_bound(WIRE, config.MATCHING_MAX_TOKENS,
                                     config.matching_sdk_attempts_per_call())
MAX_USAGE = (_BOUND["input_tokens"], _BOUND["output_tokens"])

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
        if user == "max":
            return response(WIRE, *MAX_USAGE)
        if user == "over":
            return response(WIRE, 10**7, 100)
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
import threading
ONCE, ONCE_LOCK = {"done": False}, threading.Lock()

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
    elif cfg["mode"] in ("s5max", "s5over"):
        # R1: ONE Stage 5 attempt for the whole run, answering at the
        # documented maximum ("max") or beyond it ("over").
        with ONCE_LOCK:
            go = not ONCE["done"]
            ONCE["done"] = True
        if go:
            try:
                ev.call_matching_model("system prompt",
                                       "max" if cfg["mode"] == "s5max"
                                       else "over")
            except Exception as exc:
                ONCE["exc"] = repr(exc)
    elif not OBSERVED:
        seed = spend.SPEND_LEDGER.seeded
        OBSERVED.update(
            seed=seed._asdict(), describe=spend.describe_seed(seed),
            remaining=spend.remaining(spend.SPEND_SOURCE_STAGE5),
            report=spend.report_lines(),
            campaign_id=spend.BILLING_RECORD.installed_sink().campaign_id)
    return entry(fhir_path)

runner.process_patient = patient

# P4 FAILURE INJECTION (the P4 recovery). Each plant breaks ONE durable write at
# the point it would really fail, below the code under test: a commit that
# raises, a file or directory sync that raises. Nothing above the plant is
# replaced, so what is measured is how main() reacts.
def inject(kind):
    if not kind:
        return
    import sqlite3, stat
    from oncotriage.storage import database_logger as dl
    if kind in ("commit_reserve", "commit_stamp"):
        needle = ("INSERT OR IGNORE INTO BILLING_ATTEMPTS"
                  if kind == "commit_reserve"
                  else "UPDATE RUNS SET BILLING_CAMPAIGN_ID")
        real = dl._open_billing_connection

        class FailingCommit:
            def __init__(self, conn):
                self._conn, self._hit = conn, False
                conn.set_trace_callback(self._trace)

            def _trace(self, sql):
                if sql.strip().upper().startswith(needle):
                    self._hit = True

            def cursor(self):
                return self._conn.cursor()

            def execute(self, *a):
                return self._conn.execute(*a)

            def commit(self):
                if self._hit:
                    raise sqlite3.OperationalError(
                        "disk I/O error (planted at commit)")
                return self._conn.commit()

            def close(self):
                return self._conn.close()

        dl._open_billing_connection = lambda db_path: FailingCommit(real(db_path))
    elif kind == "stamp_weak":
        real_open, real_billing = dl._open_connection, dl._open_billing_connection

        class Weak(sqlite3.Connection):
            def execute(self, sql, *a):
                if str(sql).strip().upper() == "PRAGMA SYNCHRONOUS = FULL":
                    sql = "PRAGMA synchronous = OFF"
                return super().execute(sql, *a)

        def weak_billing(db_path):
            dl._open_connection = lambda p, read_only=False: (
                real_open(p, read_only=True) if read_only
                else sqlite3.connect(p, timeout=30.0, factory=Weak))
            try:
                return real_billing(db_path)
            finally:
                dl._open_connection = real_open

        dl._open_billing_connection = weak_billing
    elif kind in ("file_sync", "dir_sync"):
        real_os, want_dir = runner.os, kind == "dir_sync"

        class OsProxy:
            def __getattr__(self, name):
                return getattr(real_os, name)

            @staticmethod
            def fsync(fd):
                is_dir = stat.S_ISDIR(real_os.fstat(fd).st_mode)
                if is_dir == want_dir:
                    raise OSError(5, "planted EIO on "
                                  + ("directory" if is_dir else "file")
                                  + " fsync")
                return real_os.fsync(fd)

        runner.os = OsProxy()
    elif kind == "dir_fullfsync":
        real_fcntl = runner.fcntl

        def fc(fd, cmd, *a):
            if (cmd == real_fcntl.F_FULLFSYNC
                    and stat.S_ISDIR(os.fstat(fd).st_mode)):
                raise OSError(45, "planted F_FULLFSYNC refusal on the directory")
            return real_fcntl.fcntl(fd, cmd, *a)

        runner.fcntl = types.SimpleNamespace(fcntl=fc,
                                             F_FULLFSYNC=real_fcntl.F_FULLFSYNC)
    elif kind.startswith("ack_"):
        # P1c: EVERY settlement really COMMITS and then loses its
        # acknowledgement -- it reports `failed` (`ack_failed`), or raises after
        # the commit (`ack_raised`). `ack_unverifiable` additionally makes the
        # stored row unreadable to the verification, so the liability must be
        # handled without knowing whether the commit landed.
        real_settle = dl.settle_billing_attempt

        def settle(db_path, attempt_id, **kw):
            res = real_settle(db_path, attempt_id, **kw)
            if res == "settled":
                if kind == "ack_raised":
                    raise OSError(5, "planted EIO after the commit")
                return "failed"
            return res

        dl.settle_billing_attempt = settle
        if kind == "ack_unverifiable":
            dl.billing_attempt_stored_state = lambda *a, **k: None
    elif kind == "kill_after_answer":
        # R1: the provider ANSWERS, and the process dies before anything after
        # the answer -- the settlement included -- can run.
        import signal
        real_create = CLIENT.create
        def create(**kw):
            real_create(**kw)
            os.kill(os.getpid(), signal.SIGKILL)
        CLIENT.create = create
        CLIENT.chat = types.SimpleNamespace(completions=CLIENT)
    elif kind == "total_fail":
        # R1 (P1c item 2): EVERY durable write after dispatch fails -- the
        # settlement, the discrepancy row and the marker file.
        dl.settle_billing_attempt = lambda *a, **k: dl.SETTLE_FAILED
        dl.record_settlement_discrepancy = lambda *a, **k: dl.DISCREPANCY_FAILED
        def no_marker(*a, **k):
            raise OSError(28, "planted ENOSPC writing the marker")
        dl.write_discrepancy_marker = no_marker
    elif kind.startswith("settle_"):
        # P1b: the FIRST settlement of the run finds its row DELETED (missing)
        # or already SETTLED at $0 by somebody else (conflict). `_deferred`
        # also refuses the discrepancy row in the database, so it must go to a
        # marker; `_unrecordable` refuses the marker too.
        import threading
        real_settle, lock, state = dl.settle_billing_attempt, threading.Lock(), {}

        def settle(db_path, attempt_id, **kw):
            with lock:
                first = not state
                state.setdefault("attempt_id", attempt_id)
            if first:
                if kind.startswith("settle_missing"):
                    c = sqlite3.connect(db_path)
                    c.execute("DELETE FROM billing_attempts WHERE attempt_id = ?",
                              (attempt_id,))
                    c.commit()
                    c.close()
                else:
                    real_settle(db_path, attempt_id, outcome="not_billed",
                                settled_usd=0.0)
            return real_settle(db_path, attempt_id, **kw)

        dl.settle_billing_attempt = settle
        if kind.endswith(("_deferred", "_unrecordable")):
            dl.record_settlement_discrepancy = (
                lambda *a, **k: dl.DISCREPANCY_FAILED)
        if kind.endswith("_unrecordable"):
            def no_marker(*a, **k):
                raise OSError(28, "planted ENOSPC writing the marker")
            dl.write_discrepancy_marker = no_marker
    else:
        raise SystemExit(f"unknown injection {kind!r}")

inject(cfg.get("inject"))
EXIT = 0
try:
    runner.main()
except SystemExit as _stop:
    EXIT = _stop.code
snap = spend.BILLING_RECORD.liability_snapshot()
EXTRA = dict(exit_code=EXIT, inject=cfg.get("inject"),
             billing_faults=dict(spend.BILLING_RECORD_FAULTS),
             latch=[spend.SPEND_STOP.requested, spend.SPEND_STOP.limit],
             latch_cause=spend.SPEND_STOP.cause)
if cfg["mode"] in ("campaign", "s5max", "s5over"):
    dump(measured=spend.SPEND_LEDGER.measured, total=spend.SPEND_LEDGER.total,
         remaining=spend.remaining(spend.SPEND_SOURCE_STAGE5),
         tally={k: list(v) for k, v in snap.items()},
         report=spend.report_lines(), calls=CLIENT.calls,
         faults=dict(spend.SPEND_LEDGER_FAULTS), once=ONCE, **EXTRA)
else:
    dump(**OBSERVED, calls=CLIENT.calls, **EXTRA)
if EXIT:
    sys.exit(EXIT)
''', encoding="utf-8")


def child(mode, *, db, cp, corpus, cap, timeout=240, inject=None):
    out = os.path.join(cp, f"{mode}.json")
    if os.path.exists(out):
        os.remove(out)
    cfg = {"mode": mode, "repo": _REPO, "tests": _TESTS, "db": db, "cp": cp,
           "corpus": corpus, "cap": cap, "out": out, "fingerprint": FIXED_FP,
           "inject": inject}
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
section("SECTION 4 (continued) -- P4 failure injection through the REAL main()")
# ===========================================================================
#
# EACH DURABLE WRITE BROKEN ONCE, AT THE POINT IT WOULD REALLY FAIL, and each
# followed by an ordinary restart against the same database and checkpoint
# directory. What is asserted is the property the P4 brief names, per failure:
# NO PAID DISPATCH (the stand-in provider was never called), A NAMED REFUSAL
# (printed and, for the stamp, recorded on the run row as KILLED with no billing
# campaign id -- the P3 interaction), and SAFE RESTART BEHAVIOUR (the next run
# bills normally under one campaign, with no orphaned reservation and nothing
# counted twice).

_DARWIN_FULLFSYNC = sys.platform == "darwin" and hasattr(fcntl, "F_FULLFSYNC")
_REFUSAL_INJECTIONS = (
    # kind,            refusal reason,                                record left?, restart decision
    ("commit_stamp", _runner.CAMPAIGN_REFUSAL_BILLING_UNWRITABLE, True,
     _runner.CAMPAIGN_DECISION_CONTINUED),
    ("stamp_weak", _runner.CAMPAIGN_REFUSAL_BILLING_UNWRITABLE, True,
     _runner.CAMPAIGN_DECISION_CONTINUED),
    ("file_sync", _runner.CAMPAIGN_REFUSAL_RECORD_UNWRITABLE, False,
     _runner.CAMPAIGN_DECISION_NEW),
    ("dir_sync", _runner.CAMPAIGN_REFUSAL_RECORD_UNWRITABLE, True,
     _runner.CAMPAIGN_DECISION_CONTINUED),
) + ((("dir_fullfsync", _runner.CAMPAIGN_REFUSAL_RECORD_UNWRITABLE, True,
       _runner.CAMPAIGN_DECISION_CONTINUED),) if _DARWIN_FULLFSYNC else ())
if not _DARWIN_FULLFSYNC:
    skip("4s-dir_fullfsync injection", f"{sys.platform} has no F_FULLFSYNC")


def _decision_line(proc):
    text = (getattr(proc, "stdout", "") or "") + (getattr(proc, "stderr", "") or "")
    return next((l.strip() for l in text.splitlines()
                 if l.strip().startswith("[Campaign] ") and l.strip().endswith(")")
                 and " (" in l), None)


def _restart_is_safe(label, db, cp, decision, first_statuses=("KILLED",),
                     first_has_id=False):
    """The ordinary restart after a refusal: it bills, under ONE campaign."""
    proc, data = child("campaign", db=db, cp=cp, corpus=_CORPUS, cap=_E2E_CAP)
    rec = drive(lambda: json.loads(Path(record_path(cp)).read_text()))
    cid = at(rec, "campaign_id")
    runs = drive(ro_rows, db, "SELECT id, status, billing_campaign_id FROM runs "
                              "ORDER BY id")
    rows = drive(ro_rows, db, "SELECT DISTINCT campaign_id, run_id, state FROM "
                              "billing_attempts")
    total = drive(_dl.campaign_billing_total, cid, db_path=db)
    settled_sum = at(at(drive(ro_rows, db, "SELECT COALESCE(SUM(settled_usd), 0) "
                                           "FROM billing_attempts"), 0), 0)
    check(f"4s-{label}-restart *** SAFE RESTART: the next run exits 0, dispatches "
          f"(12 wire attempts), decides {decision!r}, stamps ITS run row with "
          f"the recorded campaign, and every billing row is that run's, "
          f"settled ***",
          (proc.returncode, at(data, "calls"),
           str(_decision_line(proc) or "").endswith(f"({decision})"),
           len(runs) if isinstance(runs, list) else runs,
           at(at(runs, 0), 1) in first_statuses,
           (at(at(runs, 0), 2) == cid) is first_has_id,
           (at(at(runs, 1), 1), at(at(runs, 1), 2) == cid),
           sorted({(r[0] == cid, r[1], r[2]) for r in rows})
           if isinstance(rows, list) else rows),
          (0, 12, True, 2, True, True, ("FAILED", True),
           [(True, 2, "settled")]))
    check(f"4s-{label}-restart-i ...and its budget is exactly its own billing "
          f"rows: nothing from the refused run, nothing reserved, nothing twice",
          (getattr(total, "attempts", None), getattr(total, "unresolved", None),
           near(getattr(total, "usd", None), settled_sum)),
          (12, 0, True))


for _kind, _reason, _record_left, _decision in _REFUSAL_INJECTIONS:
    _dbx = os.path.join(_TMP, f"inject_{_kind}.db")
    _cpx = os.path.join(_TMP, f"cp_inject_{_kind}")
    os.makedirs(_cpx)
    _px, _dx = child("campaign", db=_dbx, cp=_cpx, corpus=_CORPUS, cap=_E2E_CAP,
                     inject=_kind)
    _outx = (_px.stdout or "") + (_px.stderr or "")
    check(f"4s-{_kind} *** NO PAID DISPATCH and a NAMED REFUSAL: exit 1, the "
          f"stand-in provider never called, the refusal block names "
          f"{_reason!r} and says nothing was billed ***",
          (_px.returncode, at(_dx, "exit_code"), at(_dx, "calls"),
           f"REFUSING TO START PAID WORK: {_reason}" in _outx,
           "NOTHING HAS BEEN BILLED" in _outx),
          (1, 1, 0, True, True))
    check(f"4s-{_kind}-i *** THE RUN ROW: KILLED, finished, and carrying NO "
          f"billing campaign id; no billing row exists ***",
          (drive(ro_rows, _dbx, "SELECT status, finished_at IS NOT NULL, "
                                "billing_campaign_id FROM runs"),
           at(at(drive(ro_rows, _dbx, "SELECT COUNT(*) FROM billing_attempts"),
                 0), 0)),
          ([("KILLED", 1, None)], 0))
    check(f"4s-{_kind}-ii ...the identity record is "
          f"{'left, naming a campaign with no spend' if _record_left else 'absent'}"
          f", and no temp file is left behind",
          (os.path.exists(record_path(_cpx)),
           sorted(n for n in os.listdir(_cpx) if n.endswith(".tmp"))),
          (_record_left, []))
    if _kind == "file_sync":
        check("4s-file_sync-iii ...and the refusal names the failed stage",
              "failed while syncing the temp file" in _outx, True)
    if _kind in ("dir_sync", "dir_fullfsync"):
        check(f"4s-{_kind}-iii ...and the refusal names the failed stage",
              "failed while syncing the directory after the rename" in _outx,
              True)
    if _kind == "stamp_weak":
        check("4s-stamp_weak-iii ...and the refusal says the connection could "
              "not be made synchronous", "synchronous" in _outx, True)
    _restart_is_safe(_kind, _dbx, _cpx, _decision)

# A FAILED COMMIT OF A RESERVATION, MID-RUN. The campaign is established and
# stamped; the first billed attempt's reservation commit raises. Nothing may be
# dispatched, the run must say why it stopped, and the restart must continue
# the campaign with a budget that holds nothing from the refused attempt.
_dbc = os.path.join(_TMP, "inject_commit_reserve.db")
_cpc = os.path.join(_TMP, "cp_inject_commit_reserve")
os.makedirs(_cpc)
_pc, _dc = child("campaign", db=_dbc, cp=_cpc, corpus=_CORPUS, cap=_E2E_CAP,
                 inject="commit_reserve")
_cidc = at(drive(lambda: json.loads(Path(record_path(_cpc)).read_text())),
           "campaign_id")
check("4t *** A FAILED RESERVATION COMMIT: NO PAID DISPATCH, the run latches "
      "under the billing-record limit, and no billing row exists ***",
      (at(_dc, "calls"), at(_dc, "latch"),
       at(at(drive(ro_rows, _dbc, "SELECT COUNT(*) FROM billing_attempts"), 0), 0),
       any(str(k).startswith("reserve:") for k in (at(_dc, "billing_faults") or {}))),
      (0, [True, "billing_record"], 0, True))
check("4t-i *** NAMED: the run row records stop_reason 'billing_record' and "
      "carries the campaign it was stamped with ***",
      drive(ro_rows, _dbc, "SELECT stop_reason, billing_campaign_id = ? FROM runs",
            (_cidc,)),
      [(_dl.RUN_STOP_REASON_BILLING_RECORD, 1)])
# THE REFUSED RUN HERE WAS STAMPED (its campaign was established before the
# reservation failed), and its terminal status is a SCHEDULING fact rather than
# a durability one: FAILED when both patients had started before the latch (the
# cohort was covered, every patient failed), STOPPED when the latch kept the
# second from starting (the run covers a prefix). Both are the runner's honest
# statuses -- measured, the plant matrix saw each -- so either is accepted, and
# 4t-i pins what must not vary: stop_reason 'billing_record'.
_restart_is_safe("commit_reserve", _dbc, _cpc, _runner.CAMPAIGN_DECISION_CONTINUED,
                 first_statuses=("FAILED", "STOPPED"), first_has_id=True)


# ── 4u: THE P4b RECOVERY GAP, THROUGH THE REAL main(), IN FRESH PROCESSES ─────
#
# Process 1 runs a campaign to its end with every patient failing, so no
# checkpoint exists; one reservation is then left UNRESOLVED under it, and the
# identity record is deleted. Every later process is a new interpreter running
# the shipped main().
_dbu = os.path.join(_TMP, "p4b_zero_success.db")
_cpu = os.path.join(_TMP, "cp_p4b_zero_success")
os.makedirs(_cpu)
_pu1, _du1 = child("campaign", db=_dbu, cp=_cpu, corpus=_CORPUS, cap=_E2E_CAP)
_campu = at(drive(lambda: json.loads(Path(record_path(_cpu)).read_text())),
            "campaign_id")
_run1u = at(at(drive(ro_rows, _dbu, "SELECT MIN(id) FROM runs"), 0), 0)
drive(_open_s5, _dbu, "p4b-open", _campu, _run1u)
_totu = drive(_dl.campaign_billing_total, _campu, db_path=_dbu)
check("4u non-degeneracy: process 1 ran main() to its end with ZERO completed "
      "patients (no checkpoint), twelve settled attempts, and one reservation "
      "left UNRESOLVED",
      (_pu1.returncode,
       os.path.exists(os.path.join(_cpu, _runner.CHECKPOINT_FILENAME)),
       getattr(_totu, "attempts", None), getattr(_totu, "unresolved", None),
       getattr(_totu, "usd", 0) > 0.40),
      (0, False, 13, 1, True))
os.remove(record_path(_cpu))
_pu2, _du2 = child("observe", db=_dbu, cp=_cpu, corpus=_CORPUS, cap=_E2E_CAP)
check("4u-i *** a FRESH process with the identity record deleted and NO "
      "checkpoint recovers the campaign through main(): exit 0, the decision "
      "printed, the same campaign, and a seed carrying every settled charge AND "
      "the unresolved reservation ***",
      (_pu2.returncode,
       str(_decision_line(_pu2) or "").endswith(
           f"({_runner.CAMPAIGN_DECISION_RECOVERED})"),
       at(_du2, "campaign_id") == _campu,
       near(at(at(_du2, "seed"), "usd"), getattr(_totu, "usd", None)),
       at(at(_du2, "seed"), "unresolved")),
      (0, True, True, True, 1))
_census_u = billing_census(_dbu)
os.remove(record_path(_cpu))
_pu3, _du3 = child("observe", db=_dbu, cp=_cpu, corpus=_CORPUS, cap=_E2E_CAP)
check("4u-ii *** REPEATED recovery in a third process: the same campaign, the "
      "same seed, and the billing record unchanged row for row ***",
      (_pu3.returncode, at(_du3, "campaign_id") == _campu,
       near(at(at(_du3, "seed"), "usd"), getattr(_totu, "usd", None)),
       billing_census(_dbu)),
      (0, True, True, _census_u))

# ITEM 3 THROUGH main(): the identity rewrite of that ALREADY-BILLED campaign
# fails, and the refusal must be true of it.
for _kind in ("file_sync", "dir_sync"):
    _census_before = billing_census(_dbu)
    if os.path.exists(record_path(_cpu)):
        os.remove(record_path(_cpu))
    _pi, _di = child("campaign", db=_dbu, cp=_cpu, corpus=_CORPUS, cap=_E2E_CAP,
                     inject=_kind)
    _outi = (_pi.stdout or "") + (_pi.stderr or "")
    check(f"4u-{_kind} *** recovering an ALREADY-BILLED campaign, the identity "
          f"rewrite fails ({_kind}): exit 1, zero provider calls, refused by "
          f"name, nothing billed, and the refusal says the campaign may hold "
          f"charges rather than that it is spend-free and safe to remove ***",
          (_pi.returncode, at(_di, "calls"),
           f"REFUSING TO START PAID WORK: "
           f"{_runner.CAMPAIGN_REFUSAL_RECORD_UNWRITABLE}" in _outi,
           "ALREADY HOLD CHARGES" in _outi,
           [c for c in _RETIRED_CLAIMS if c in _outi], billing_census(_dbu)),
          (1, 0, True, True, [], _census_before))
    _pr_i, _dr_i = child("observe", db=_dbu, cp=_cpu, corpus=_CORPUS,
                         cap=_E2E_CAP)
    check(f"4u-{_kind}-i ...and the next fresh process continues THAT campaign "
          f"with the same seed and duplicates nothing",
          (_pr_i.returncode, at(_dr_i, "campaign_id") == _campu,
           near(at(at(_dr_i, "seed"), "usd"), getattr(_totu, "usd", None)),
           billing_census(_dbu)),
          (0, True, True, _census_before))

# AMBIGUITY THROUGH main(): a second open campaign under the same configuration
# and cohort, and the record deleted. Nothing may be dispatched.
_run_other_u = drive(_dl.start_run_record, "batch", db_path=_dbu,
                     fingerprint=FIXED_FP)
_conn = sqlite3.connect(_dbu)
_conn.execute("UPDATE runs SET cohort_digest = (SELECT cohort_digest FROM runs "
              "WHERE id = ?) WHERE id = ?", (_run1u, _run_other_u))
_conn.commit()
_conn.close()
drive(_dl.set_run_billing_campaign_id, _run_other_u, "camp-p4b-other",
      db_path=_dbu)
drive(_dl.reserve_billing_attempt, _dbu, attempt_id="p4b-other-1",
      campaign_id="camp-p4b-other", run_id=_run_other_u, source="stage5",
      model=_WIRE, input_tokens=1, output_tokens=1, reserved_usd=0.5)
drive(_dl.finalize_run_record, _run_other_u, "KILLED", db_path=_dbu)
if os.path.exists(record_path(_cpu)):
    os.remove(record_path(_cpu))
_census_amb = billing_census(_dbu)
_pam, _dam = child("campaign", db=_dbu, cp=_cpu, corpus=_CORPUS, cap=_E2E_CAP)
_outam = (_pam.stdout or "") + (_pam.stderr or "")
check("4u-iii *** AMBIGUOUS through main(): exit 1, zero provider calls, "
      "refused as campaign_identity_unestablished naming both campaigns, and "
      "no identity record written ***",
      (_pam.returncode, at(_dam, "calls"),
       f"REFUSING TO START PAID WORK: "
       f"{_runner.CAMPAIGN_REFUSAL_IDENTITY_UNESTABLISHED}" in _outam,
       _campu in _outam and "camp-p4b-other" in _outam,
       os.path.exists(record_path(_cpu)), billing_census(_dbu)),
      (1, 0, True, True, False, _census_amb))


# ===========================================================================
section("SECTION 7 -- P1b: the embedding reservation's bound, and settlements "
        "that did not land (runs before the isolation checks)")
# ===========================================================================
#
# ADDED BY THE P1b RECOVERY SESSION.
#
# (1) THE EMBEDDING RESERVATION was `len(text)/3 + 1` tokens, an ESTIMATE a
# tokenizer can exceed; a response priced above it whose settlement then failed
# left the durable row, read at the reservation, below the live charge. It is
# now the provider's DOCUMENTED per-input maximum (8,192) for one string and the
# per-request maximum (300,000) otherwise, and an undocumented model is refused
# before dispatch.
#
# (2) A SETTLEMENT THAT RETURNS missing, conflict or failed used to top the live
# ledger up to the reservation and leave the durable record where it was -- for
# `missing` that is NOTHING, so a fresh process read less than live charged. The
# shortfall is now its own settled `settlement_discrepancy` row (or a synced
# marker the runner reconciles before paid work); `missing` also makes a resume
# REFUSE, and `missing`/`conflict` latch the live run.

_EMB = config.EMBEDDING_MODEL
_BOUND = _models.EMBEDDING_MAX_INPUT_TOKENS_PER_INPUT.get(_EMB)


def _price_emb(tokens, model=None):
    return _spend.price_usage(model or _EMB, tokens, 0)[0]


_RES_EMB = _price_emb(_BOUND or 0)
check("7a the shipped embedding model's documented per-input bound is 8,192 "
      "tokens and the per-request bound 300,000, and the reservation prices",
      (_EMB, _BOUND, _models.EMBEDDING_MAX_INPUT_TOKENS_PER_REQUEST,
       near(_RES_EMB, 8192 * 0.02 / 1_000_000)),
      ("text-embedding-3-small", 8192, 300000, True))

_ADVERSARIAL = {
    "empty": "", "one_char": "a", "nul_bytes": "\x00" * 5,
    "emoji_4byte": "\U0001F600" * 5000, "digits": "1" * 100_000,
    "combining": "é" * 3000, "lone_surrogate": "\ud800" * 10,
    "cjk": "癌" * 20_000, "megabyte_ascii": "x" * 1_000_000,
}
check("7b *** every single-string input reserves the documented per-input "
      "maximum, whatever its length, byte width or Unicode content ***",
      {k: drive(_models.embedding_reservation_input_tokens, v, _EMB)
       for k, v in _ADVERSARIAL.items()},
      {k: 8192 for k in _ADVERSARIAL})
_NON_STR = {"list_of_str": ["a", "b"], "tuple_of_str": ("a",),
            "token_array": [1, 2, 3], "token_arrays": [[1], [2]],
            "bytes": b"abc", "none": None}
check("7c *** any other input -- a batch, token arrays, bytes, None -- reserves "
      "the documented per-REQUEST maximum ***",
      {k: drive(_models.embedding_reservation_input_tokens, v, _EMB)
       for k, v in _NON_STR.items()},
      {k: 300000 for k in _NON_STR})
_UNBOUNDED = {"large": "text-embedding-3-large", "none": None, "empty": "",
              "unhashable": ["text-embedding-3-small"]}
check("7d a model with no documented bound is REFUSED by name, including an "
      "unhashable one",
      {k: type(raised(_models.embedding_reservation_input_tokens, "x", m)).__name__
       for k, m in _UNBOUNDED.items()},
      {k: "EmbeddingReservationUnbounded" for k in _UNBOUNDED})


def _old_estimate(text):
    return int(len(str(text)) / float(config.PROVIDER_RESERVATION_CHARS_PER_TOKEN)) + 1


check("7e *** THE OLD ESTIMATE WAS NOT A BOUND: 5,000 four-byte characters "
      "billed one token each exceed len/3+1; 100,000 digits billed at 30,000 "
      "tokens exceed it too; neither can exceed the documented bound of a "
      "request the endpoint accepted ***",
      (_price_emb(5000) > _price_emb(_old_estimate(_ADVERSARIAL["emoji_4byte"])),
       _old_estimate("\U0001F600" * 5000) < 5000,
       _price_emb(5000) <= _RES_EMB, _price_emb(8192) <= _RES_EMB),
      (True, True, True, True))

_DL_PATCHABLE = ("settle_billing_attempt", "record_settlement_discrepancy",
                 "write_discrepancy_marker", "billing_attempt_settled_usd",
                 "billing_attempt_stored_state")
_DL_START = {n: getattr(_dl, n) for n in _DL_PATCHABLE}
_REAL_SETTLE = _DL_START["settle_billing_attempt"]


def _restore_dl():
    for _n, _fn in _DL_START.items():
        setattr(_dl, _n, _fn)


def p1b_case(name, client, call, *, patches=None, discrepancy_dir=None,
             knobs=None):
    """Drive one billed attempt with a sink installed and the storage layer
    patched as named; then read what a FRESH process would, with every patch
    removed first -- the reader is the shipped one."""
    db = new_db(f"p1b_{name}.db")
    run = _dl.start_run_record("batch", db_path=db, fingerprint=FIXED_FP)
    camp = f"camp-p1b-{name}"
    deps.set_override(deps.OPENAI_CLIENT, client)
    live = {}
    try:
        with settings(SPEND_CAP_USD=_CAP, SPEND_CAP_ENFORCED=True,
                      **(knobs or {})):
            _reset_spend_state()
            for _n, _fn in (patches or {}).items():
                setattr(_dl, _n, _fn)
            _spend.BILLING_RECORD.install(_dl.BillingRecordSink(
                db, camp, run, discrepancy_dir=discrepancy_dir))
            try:
                exc = raised(call)
                live = {"measured": _spend.SPEND_LEDGER.measured,
                        "remaining": _spend.remaining(_spend.SPEND_SOURCE_STAGE5),
                        "tally": _spend.BILLING_RECORD.liability_snapshot(),
                        "record_faults": dict(_spend.BILLING_RECORD_FAULTS),
                        "latch": (_spend.SPEND_STOP.requested,
                                  _spend.SPEND_STOP.limit),
                        "cause": _spend.SPEND_STOP.cause}
            finally:
                _spend.BILLING_RECORD.clear()
                _restore_dl()
            durable_exc = raised(_dl.campaign_billing_total, camp, db_path=db)
            durable = drive(_dl.campaign_billing_total, camp, db_path=db)
            resumed_remaining = None
            if isinstance(durable, _dl.CampaignBilling):
                _spend.SPEND_LEDGER.reset()
                _spend.SPEND_STOP.reset()
                _spend.SPEND_LEDGER.seed(_spend.LedgerSeed(
                    usd=durable.usd, rows=durable.attempts, runs=1,
                    source=_spend.SEED_SOURCE_BILLING_RECORD,
                    unresolved=durable.unresolved))
                resumed_remaining = _spend.remaining(_spend.SPEND_SOURCE_STAGE5)
    finally:
        _restore_dl()
        deps.clear_override(deps.OPENAI_CLIENT)
        _reset_spend_state()
    rows = ro_rows(db, "SELECT attempt_id, kind, state, outcome, reserved_usd, "
                       "settled_usd, note, reserved_input_tokens FROM "
                       "billing_attempts ORDER BY rowid")
    settled_durable = sum(r[5] for r in rows if r[2] == "settled")
    reserved_durable = [(1, r[4]) for r in rows if r[2] == "reserved"]
    tally = live.get("tally") or {}
    return {"exc": exc, "live": live, "durable": durable,
            "durable_exc": durable_exc, "resumed_remaining": resumed_remaining,
            "rows": rows, "calls": client.calls, "db": db, "camp": camp,
            "run": run,
            "settled_durable": settled_durable,
            "reserved_durable": (len(reserved_durable),
                                 sum(u for _n, u in reserved_durable)),
            "settled_live": sum(v[1] for k, v in tally.items()
                                if k != _spend.LIABILITY_OPEN),
            "open_live": tuple(tally.get(_spend.LIABILITY_OPEN, (0, 0.0)))}


def _settle_missing(db_path, attempt_id, **kw):
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM billing_attempts WHERE attempt_id = ?",
                 (attempt_id,))
    conn.commit()
    conn.close()
    return _REAL_SETTLE(db_path, attempt_id, **kw)


def _settle_conflict_at(amount, outcome):
    def settle(db_path, attempt_id, **kw):
        _REAL_SETTLE(db_path, attempt_id, outcome=outcome, settled_usd=amount)
        return _REAL_SETTLE(db_path, attempt_id, **kw)
    return settle


def _returns(value):
    return lambda *a, **k: value


def _marker_refused(*a, **k):
    raise OSError(28, "planted ENOSPC writing the marker")


_EMB_KW = []


def _emb_client(tokens=9, echo=None):
    def respond(kw):
        _EMB_KW.append(kw)
        return _embedding(prompt_tokens=tokens, model=echo)
    return _Client(embed=respond)


_EMB_TEXT = _ADVERSARIAL["emoji_4byte"]


def _emb_call():
    return _models.get_embedding(_EMB_TEXT)


def _kinds(case):
    return [(r[1], r[2]) for r in case["rows"]]


def _note_result(row):
    try:
        return json.loads(row[6]).get("result")
    except Exception:                                          # noqa: BLE001
        return None


def parity_holds(case):
    """THE P1b INVARIANT at the accounting precision.

    (0) SETTLED: the live tally's resolved total equals the durable settled
        total plus the reservations of attempts whose settlement did not land
        (a FAILED settlement leaves its row reserved, and live charged that
        reservation).
    (1) UNRESOLVED: nothing open live, and the durable reserved rows are exactly
        the settlements the LIVE process recorded as not landed for a reason
        other than missing/conflict -- counted from its own fault keys, not
        from the durable side.
    (2) TOTAL and (3) REMAINING, through ``spend.remaining``.
    """
    faults = case["live"].get("record_faults") or {}
    unlanded = sum(v for k, v in faults.items()
                   if k.startswith("ledger_topped_up:")
                   and k.split(":", 1)[1]
                   not in _spend.SETTLEMENT_INTEGRITY_RESULTS)
    return (near(case["settled_live"],
                 case["settled_durable"] + case["reserved_durable"][1]),
            case["open_live"][0] == 0
            and case["reserved_durable"][0] == unlanded,
            near(case["live"].get("measured"), getattr(case["durable"], "usd",
                                                        None)),
            near(case["live"].get("remaining"), case["resumed_remaining"]))


# ── 7f..7k: THE BOUND, THROUGH THE REAL get_embedding ───────────────────────
_EMB_KW.clear()
_c_ok = p1b_case("emb_ok", _emb_client(), _emb_call)
check("7f *** a 5,000-emoji query is RESERVED at the documented bound, token "
      "count and price, and settles at its usage ***",
      ([(r[1], r[2], r[3], r[7]) for r in _c_ok["rows"]],
       near(at(at(_c_ok["rows"], 0), 4), _RES_EMB)),
      ([("attempt", "settled", "response", 8192)], True))
check("7f-i *** THE REQUEST IS UNCHANGED: the same model, the same input object "
      "and the same timeout key, nothing added ***",
      (len(_EMB_KW), sorted(at(_EMB_KW, 0) or {}),
       at(at(_EMB_KW, 0), "input") is _EMB_TEXT, at(at(_EMB_KW, 0), "model")),
      (1, ["input", "model", "timeout"], True, _EMB))
check("7f-ii parity holds for an ordinary response",
      parity_holds(_c_ok), (True, True, True, True))

_EMB_KW.clear()
_c_unb = p1b_case("emb_unbounded", _emb_client(), _emb_call,
                  knobs={"EMBEDDING_MODEL": "text-embedding-3-large"})
check("7g *** an embedding model with no documented bound is REFUSED BEFORE "
      "DISPATCH: named exception, zero provider calls, zero billing rows, zero "
      "spend ***",
      (type(_c_unb["exc"]).__name__, _c_unb["calls"], len(_EMB_KW),
       _c_unb["rows"], _c_unb["live"].get("measured")),
      ("EmbeddingReservationUnbounded", 0, 0, [], 0.0))

_c_at = p1b_case("emb_at_bound_failed", _emb_client(tokens=8192), _emb_call,
                 patches={"settle_billing_attempt": _returns(_dl.SETTLE_FAILED)})
check("7h a response AT the bound whose settlement fails leaves only the "
      "reserved row: no shortfall, no discrepancy row, no latch",
      (_kinds(_c_at), at(_c_at["live"], "latch")),
      ([("attempt", "reserved")], (False, None)))
check("7h-i ...and parity holds exactly", parity_holds(_c_at),
      (True, True, True, True))

_c_over = p1b_case("emb_over_bound_failed", _emb_client(tokens=9000), _emb_call,
                   patches={"settle_billing_attempt":
                            _returns(_dl.SETTLE_FAILED)})
_disc_over = [r for r in _c_over["rows"] if r[1] == "settlement_discrepancy"]
check("7i *** a provider that bills BEYOND its documented bound, with the "
      "settlement failing: the shortfall is its own settled row, and a fresh "
      "reading equals live ***",
      (_kinds(_c_over),
       near(at(at(_disc_over, 0), 5), _price_emb(9000) - _RES_EMB),
       _note_result(at(_disc_over, 0) or [None] * 7)),
      ([("attempt", "reserved"), ("settlement_discrepancy", "settled")], True,
       "failed"))
check("7i-i ...parity holds, and a recorded discrepancy does not latch",
      (parity_holds(_c_over), at(_c_over["live"], "latch")),
      ((True, True, True, True), (False, None)))

_c_echo = p1b_case("emb_echo_pricier_failed", _emb_client(tokens=8000,
                                                          echo=_WIRE),
                   _emb_call,
                   patches={"settle_billing_attempt":
                            _returns(_dl.SETTLE_FAILED)})
check("7j *** PRICING, ADVERSARIALLY: an echo naming a model with a higher rate "
      "prices above the reservation; with the settlement failing the "
      "discrepancy row carries the difference and parity holds ***",
      (_spend.price_usage(_WIRE, 8000, 0)[0] > _RES_EMB,
       [k for k, _s in _kinds(_c_echo)], parity_holds(_c_echo)),
      (True, ["attempt", "settlement_discrepancy"], (True, True, True, True)))

# ── 7k..7q: missing, conflict, failed, unrecognised -- Stage 5 and Stage 2 ──
_c_s5_missing = p1b_case("s5_missing",
                         _Client(chat=lambda kw: _chat_response((321, 45))),
                         _s5_call, patches={"settle_billing_attempt":
                                            _settle_missing})
_disc = [r for r in _c_s5_missing["rows"] if r[1] == "settlement_discrepancy"]
check("7k *** MISSING (Stage 5): the attempt row is gone, the discrepancy row "
      "retains the live charge, and the live charge is the reservation ***",
      (_kinds(_c_s5_missing), near(_c_s5_missing["live"].get("measured"),
                                   _RESERVE_S5),
       near(at(at(_disc, 0), 5), _c_s5_missing["live"].get("measured")),
       _note_result(at(_disc, 0) or [None] * 7)),
      ([("settlement_discrepancy", "settled")], True, True, "missing"))
check("7k-i *** a FRESH READING REFUSES by name (BillingRecordIncomplete), "
      "naming the retained amount -- it does not return a total lower than "
      "live ***",
      (type(_c_s5_missing["durable_exc"]).__name__,
       near(getattr(_c_s5_missing["durable_exc"], "retained_usd", None),
            _c_s5_missing["live"].get("measured")),
       f"${_c_s5_missing['live'].get('measured', 0):.6f} is retained"
       in str(_c_s5_missing["durable_exc"]),
       isinstance(_c_s5_missing["durable_exc"], _dl.BillingRecordUnreadable)),
      ("BillingRecordIncomplete", True, True, True))
check("7k-ii ...the live run is LATCHED, and the fault names the path",
      (at(_c_s5_missing["live"], "latch"),
       _c_s5_missing["live"]["record_faults"].get("discrepancy:missing:recorded")),
      ((True, _spend.SPEND_LIMIT_BILLING_RECORD), 1))
check("7k-iii settled compared separately: durable settled == live settled; "
      "nothing unresolved on either side",
      (near(_c_s5_missing["settled_live"], _c_s5_missing["settled_durable"]),
       _c_s5_missing["open_live"][0], _c_s5_missing["reserved_durable"][0]),
      (True, 0, 0))

_c_emb_missing = p1b_case("emb_missing", _emb_client(), _emb_call,
                          patches={"settle_billing_attempt": _settle_missing})
check("7l MISSING (Stage 2): the same, at the embedding's reservation",
      (_kinds(_c_emb_missing), near(_c_emb_missing["live"].get("measured"),
                                    _RES_EMB),
       type(_c_emb_missing["durable_exc"]).__name__,
       at(_c_emb_missing["live"], "latch")),
      ([("settlement_discrepancy", "settled")], True, "BillingRecordIncomplete",
       (True, _spend.SPEND_LIMIT_BILLING_RECORD)))

_c_conf_lo = p1b_case("s5_conflict_lower",
                      _Client(chat=lambda kw: _chat_response((321, 45))),
                      _s5_call, patches={"settle_billing_attempt":
                                         _settle_conflict_at(0.0, "not_billed")})
check("7m *** CONFLICT, stored BELOW live: the stored row stands, the "
      "discrepancy row carries the shortfall, and a fresh reading EQUALS live "
      "at the accounting precision ***",
      (_kinds(_c_conf_lo), parity_holds(_c_conf_lo),
       near(getattr(_c_conf_lo["durable"], "discrepancy_usd", None),
            _RESERVE_S5)),
      ([("attempt", "settled"), ("settlement_discrepancy", "settled")],
       (True, True, True, True), True))
check("7m-i ...and the conflict LATCHES the live run",
      at(_c_conf_lo["live"], "latch"),
      (True, _spend.SPEND_LIMIT_BILLING_RECORD))

# The planted stored amount was $5.00, above the old $0.38 estimate. Under
# R1 the live charge of an unverified Stage 5 attempt is the $5.826 bound, so
# "stored ABOVE live" needs an amount above the bound.
_ABOVE_BOUND = 7.0
check("7n-0 non-degeneracy: the planted stored amount is ABOVE the Stage 5 "
      "reservation", _ABOVE_BOUND > _RESERVE_S5 + 1e-6, True)
_c_conf_hi = p1b_case("s5_conflict_higher",
                      _Client(chat=lambda kw: _chat_response((321, 45))),
                      _s5_call, patches={"settle_billing_attempt":
                                         _settle_conflict_at(_ABOVE_BOUND,
                                                             "possibly_billed")})
check("7n *** CONFLICT, stored ABOVE live: live is raised to the stored amount, "
      "so live and resumed agree; the discrepancy row is $0 ***",
      (near(_c_conf_hi["live"].get("measured"), _ABOVE_BOUND),
       parity_holds(_c_conf_hi),
       [r[5] for r in _c_conf_hi["rows"] if r[1] == "settlement_discrepancy"]),
      (True, (True, True, True, True), [0.0]))

_c_conf_blind = p1b_case(
    "s5_conflict_reader_down",
    _Client(chat=lambda kw: _chat_response((321, 45))), _s5_call,
    patches={"settle_billing_attempt": _settle_conflict_at(0.001, "not_billed"),
             "billing_attempt_settled_usd": _returns(None)})
check("7o CONFLICT whose stored amount cannot be read: assumed 0, so the "
      "fresh reading OVER-counts by exactly the stored amount (the safe "
      "direction), never under",
      (near(getattr(_c_conf_blind["durable"], "usd", 0)
            - _c_conf_blind["live"].get("measured", 0), 0.001),
       _c_conf_blind["resumed_remaining"] is not None
       and _c_conf_blind["resumed_remaining"]
       < _c_conf_blind["live"].get("remaining", 0)),
      (True, True))

_c_weird = p1b_case("s5_unrecognised_result",
                    _Client(chat=lambda kw: _chat_response((321, 45))),
                    _s5_call, patches={"settle_billing_attempt":
                                       _returns("weird")})
check("7p an UNRECOGNISED settlement result is read as failed: the row stays "
      "reserved, no discrepancy is owed, parity holds, no latch",
      (_kinds(_c_weird), parity_holds(_c_weird), at(_c_weird["live"], "latch")),
      ([("attempt", "reserved")], (True, True, True, True), (False, None)))

_c_s5_over = p1b_case(
    "s5_over_reservation_failed",
    _Client(chat=lambda kw: _chat_response((10 ** 7, 45))), _s5_call,
    patches={"settle_billing_attempt": _returns(_dl.SETTLE_FAILED)})
check("7q *** Stage 5 priced ABOVE its reservation (the input estimate under-"
      "counted) with the settlement failing: recorded as a discrepancy, and "
      "parity holds ***",
      (_spend.price_usage(_WIRE, 10 ** 7, 45)[0] > _RESERVE_S5,
       [k for k, _s in _kinds(_c_s5_over)], parity_holds(_c_s5_over)),
      (True, ["attempt", "settlement_discrepancy"], (True, True, True, True)))

# ── 7r..7x: DEFERRED TO A MARKER, REPEATED RECONCILIATION, NOTHING DURABLE ──
_MARKERS = os.path.join(_TMP, "p1b_markers_missing")
_c_def = p1b_case("emb_missing_deferred", _emb_client(), _emb_call,
                  patches={"settle_billing_attempt": _settle_missing,
                           "record_settlement_discrepancy":
                               _returns(_dl.DISCREPANCY_FAILED)},
                  discrepancy_dir=_MARKERS)
_marker_files = sorted(os.listdir(_MARKERS)) if os.path.isdir(_MARKERS) else []
check("7r *** the database refused the discrepancy row: a synced MARKER holds "
      "it, the run is latched, and until reconciled the record alone reads "
      "LOW -- which is why the runner reconciles before paid work ***",
      (len(_marker_files), _kinds(_c_def), at(_c_def["live"], "latch"),
       _c_def["live"]["record_faults"].get("discrepancy:missing:deferred"),
       near(getattr(_c_def["durable"], "usd", None), 0.0)),
      (1, [], (True, _spend.SPEND_LIMIT_BILLING_RECORD), 1, True))
_rec1 = _dl.reconcile_discrepancy_markers(_MARKERS, _c_def["db"])
_after1 = raised(_dl.campaign_billing_total, _c_def["camp"], db_path=_c_def["db"])
check("7r-i *** reconciliation commits it, removes the marker, and the fresh "
      "reading then REFUSES retaining the live amount ***",
      (len(_rec1.reconciled), _rec1.unreconciled, drive(os.listdir, _MARKERS),
       type(_after1).__name__,
       near(getattr(_after1, "retained_usd", None),
            _c_def["live"].get("measured"))),
      (1, (), [], "BillingRecordIncomplete", True))
_rec2 = _dl.reconcile_discrepancy_markers(_MARKERS, _c_def["db"])
_marker_payload = {"attempt_id": _rec1.reconciled[0][0] if _rec1.reconciled
                   else "absent", "campaign_id": _c_def["camp"],
                   "run_id": _c_def["run"], "source": "query_embedding",
                   "model": _EMB, "result": "missing",
                   "live_usd": _RES_EMB, "durable_usd": 0.0,
                   "shortfall_usd": _RES_EMB, "db_path": _c_def["db"],
                   "version": _dl.DISCREPANCY_MARKER_VERSION}
_dl.write_discrepancy_marker(_MARKERS, _marker_payload)
_rec3 = _dl.reconcile_discrepancy_markers(_MARKERS, _c_def["db"])
_disc_rows = ro_rows(_c_def["db"], "SELECT COUNT(*), SUM(settled_usd) FROM "
                                   "billing_attempts WHERE kind = "
                                   "'settlement_discrepancy'")[0]
check("7s *** REPEATED RECONCILIATION DOES NOT DOUBLE-COUNT: a second pass is a "
      "no-op, and a marker LEFT IN PLACE after its row committed is recorded "
      "again as the SAME row ***",
      (_rec2, len(_rec3.reconciled), _disc_rows[0],
       near(_disc_rows[1], _RES_EMB)),
      (_dl.DiscrepancyReconciliation(), 1, 1, True))
check("7s-i the retained amount is unchanged after the third pass",
      near(getattr(raised(_dl.campaign_billing_total, _c_def["camp"],
                          db_path=_c_def["db"]), "retained_usd", None),
           _RES_EMB), True)

check("7t a colliding discrepancy at a DIFFERENT amount is FAILED; the stored "
      "row stands",
      (_dl.record_settlement_discrepancy(
          _c_def["db"], attempt_id=_marker_payload["attempt_id"],
          campaign_id=_c_def["camp"], run_id=_c_def["run"],
          source="query_embedding", model=_EMB, result="missing",
          live_usd=1.0, durable_usd=0.0, shortfall_usd=1.0),
       ro_rows(_c_def["db"], "SELECT COUNT(*) FROM billing_attempts WHERE "
                             "kind = 'settlement_discrepancy'")[0][0]),
      (_dl.DISCREPANCY_FAILED, 1))

_BAD = os.path.join(_TMP, "p1b_markers_bad")
os.makedirs(_BAD)
Path(os.path.join(_BAD, "junk.json")).write_text("{not json")
Path(os.path.join(_BAD, "notes.txt")).write_text("x")
Path(os.path.join(_BAD, ".half.tmp")).write_text("{")
_other = dict(_marker_payload, attempt_id="otherdb", db_path="/nonexistent/x.db")
_dl.write_discrepancy_marker(_BAD, _other)
_wrong_name = dict(_marker_payload, attempt_id="renamed")
Path(os.path.join(_BAD, "mismatch.json")).write_text(json.dumps(_wrong_name))
_recbad = _dl.reconcile_discrepancy_markers(_BAD, _c_def["db"])
check("7u malformed markers are UNRECONCILED by name -- unparseable, not a "
      "marker, another database, a name that is not its attempt id -- and a "
      "half-written .tmp is ignored",
      (sorted(n for n, _d in _recbad.unreconciled), _recbad.reconciled),
      (["junk.json", "mismatch.json", "notes.txt", "otherdb.json"], ()))

_c_nodir = p1b_case("emb_missing_unrecordable_nodir", _emb_client(), _emb_call,
                    patches={"settle_billing_attempt": _settle_missing,
                             "record_settlement_discrepancy":
                                 _returns(_dl.DISCREPANCY_FAILED)})
_NOMARK = os.path.join(_TMP, "p1b_markers_refused")
_c_nomark = p1b_case("emb_missing_unrecordable_marker", _emb_client(),
                     _emb_call,
                     patches={"settle_billing_attempt": _settle_missing,
                              "record_settlement_discrepancy":
                                  _returns(_dl.DISCREPANCY_FAILED),
                              "write_discrepancy_marker": _marker_refused},
                     discrepancy_dir=_NOMARK)
check("7v *** NOTHING DURABLE (the database and the marker both refused): the "
      "live run LATCHES and counts it failed. THE STATED BOUND: a fresh reading "
      "is then LOWER than live by exactly this attempt -- no durable write "
      "remained to prevent it ***",
      ([at(c["live"], "latch") for c in (_c_nodir, _c_nomark)],
       [c["live"]["record_faults"].get("discrepancy:missing:failed")
        for c in (_c_nodir, _c_nomark)],
       [near(c["live"].get("measured", 0) - getattr(c["durable"], "usd", 0),
             _RES_EMB) for c in (_c_nodir, _c_nomark)],
       os.path.isdir(_NOMARK) and os.listdir(_NOMARK)),
      ([(True, _spend.SPEND_LIMIT_BILLING_RECORD)] * 2, [1, 1], [True, True],
       False if not os.path.isdir(_NOMARK) else []))

# ── 7w: the storage vocabulary and the reader's refusals ─────────────────────
check("7w the discrepancy vocabularies are restated equal across the layers",
      (_dl.DISCREPANCY_WRITE_RESULTS == _spend.DISCREPANCY_WRITE_RESULTS,
       _dl.DISCREPANCY_RESULTS == (_spend.SETTLEMENT_MISSING,
                                   _spend.SETTLEMENT_CONFLICT,
                                   _spend.SETTLEMENT_FAILED),
       _dl.BILLING_ATTEMPT_KIND_DISCREPANCY in _dl.BILLING_ATTEMPT_KINDS),
      (True, True, True))
_DBW = new_db("p1b_reader.db")
_RW = _dl.start_run_record("batch", db_path=_DBW, fingerprint=FIXED_FP)
for _cid, _note, _state in (("c-unparseable", "{bad", "settled"),
                            ("c-conflict", '{"result": "conflict"}', "settled"),
                            ("c-reserved", '{"result": "conflict"}', "reserved")):
    _conn = sqlite3.connect(_DBW)
    _conn.execute(
        "INSERT INTO billing_attempts (attempt_id, campaign_id, run_id, kind, "
        "source, state, reserved_usd, settled_usd, reserved_at, note) VALUES "
        "(?, ?, ?, 'settlement_discrepancy', 'stage5', ?, 0.5, ?, 'now', ?)",
        (f"{_cid}:d", _cid, _RW, _state, 0.5 if _state == "settled" else None,
         _note))
    _conn.commit()
    _conn.close()
check("7w-i the reader: an UNPARSEABLE discrepancy note refuses as incomplete "
      "(the refusing reading); a conflict one is summed; a RESERVED one is "
      "unreadable",
      (type(raised(_dl.campaign_billing_total, "c-unparseable",
                   db_path=_DBW)).__name__,
       near(getattr(drive(_dl.campaign_billing_total, "c-conflict",
                          db_path=_DBW), "usd", None), 0.5),
       type(raised(_dl.campaign_billing_total, "c-reserved",
                   db_path=_DBW)).__name__),
      ("BillingRecordIncomplete", True, "BillingRecordUnreadable"))

# ── 7x..7z: A FRESH PROCESS, THROUGH THE REAL main() ─────────────────────────


def p1b_e2e(name, inject):
    db = os.path.join(_TMP, f"p1b_e2e_{name}.db")
    cp = os.path.join(_TMP, f"cp_p1b_{name}")
    os.makedirs(cp)
    p1, d1 = child("campaign", db=db, cp=cp, corpus=_CORPUS, cap=_E2E_CAP,
                   inject=inject)
    out1 = (p1.stdout or "") + (p1.stderr or "")
    markers = sorted(os.listdir(os.path.join(cp, _runner.DISCREPANCY_DIRNAME))) \
        if os.path.isdir(os.path.join(cp, _runner.DISCREPANCY_DIRNAME)) else []
    p2, d2 = child("observe", db=db, cp=cp, corpus=_CORPUS, cap=_E2E_CAP)
    out2 = (p2.stdout or "") + (p2.stderr or "")
    markers2 = sorted(os.listdir(os.path.join(cp, _runner.DISCREPANCY_DIRNAME))) \
        if os.path.isdir(os.path.join(cp, _runner.DISCREPANCY_DIRNAME)) else []
    p3, d3 = child("observe", db=db, cp=cp, corpus=_CORPUS, cap=_E2E_CAP)
    out3 = (p3.stdout or "") + (p3.stderr or "")
    rows = ro_rows(db, "SELECT kind, state, reserved_usd, settled_usd, note FROM "
                       "billing_attempts")
    return {"p": (p1, p2, p3), "d": (d1, d2, d3), "out": (out1, out2, out3),
            "markers": (markers, markers2), "rows": rows,
            "durable_sum": sum((r[3] if r[1] == "settled" else r[2])
                               for r in rows),
            "discrepancies": [r for r in rows if r[0] == "settlement_discrepancy"]}


_REFUSE_INCOMPLETE = ("REFUSING TO START PAID WORK: "
                      + _runner.CAMPAIGN_REFUSAL_BILLING_INCOMPLETE)
_e_miss = p1b_e2e("missing", "settle_missing")
_d1, _d2, _d3 = _e_miss["d"]
check("7x *** MISSING, PROCESS 1: the live run latches on the billing record "
      "and PRINTS the discrepancy ***",
      (at(_d1, "latch"), "BILLING RECORD DISCREPANCY (missing)"
       in _e_miss["out"][0], len(_e_miss["discrepancies"])),
      ([True, _spend.SPEND_LIMIT_BILLING_RECORD], True, 1))
check("7x-i *** MISSING, FRESH PROCESS 2: REFUSES paid work by name, exit 1, "
      "zero provider calls, and prints the retained amount ***",
      (_e_miss["p"][1].returncode, _REFUSE_INCOMPLETE in _e_miss["out"][1],
       at(_d2, "calls"),
       f"${at(at(_e_miss['discrepancies'], 0), 3) or 0:.6f} is retained"
       in _e_miss["out"][1]),
      (1, True, 0, True))
check("7x-ii *** the durable record retains at least what process 1 charged "
      "(settled and reserved summed), at the accounting precision ***",
      at(_d1, "measured") is not None
      and _e_miss["durable_sum"] >= at(_d1, "measured") - _TOL, True)
check("7x-iii process 3 refuses again; still ONE discrepancy row",
      (_e_miss["p"][2].returncode, _REFUSE_INCOMPLETE in _e_miss["out"][2],
       at(_d3, "calls"), len(ro_rows(_TMP and os.path.join(
           _TMP, "p1b_e2e_missing.db"), "SELECT 1 FROM billing_attempts WHERE "
           "kind = 'settlement_discrepancy'"))),
      (1, True, 0, 1))

_e_conf = p1b_e2e("conflict", "settle_conflict")
_d1, _d2, _d3 = _e_conf["d"]
check("7y *** CONFLICT, FRESH PROCESS 2: continues the campaign; its seed "
      "EQUALS process 1's live ledger and its remaining EQUALS process 1's, at "
      "the accounting precision ***",
      (at(_d1, "latch"), _e_conf["p"][1].returncode,
       near(at(at(_d2, "seed"), "usd"), at(_d1, "measured")),
       near(at(_d2, "remaining"), at(_d1, "remaining")),
       len(_e_conf["discrepancies"])),
      ([True, _spend.SPEND_LIMIT_BILLING_RECORD], 0, True, True, 1))
check("7y-i *** settled and unresolved separately: process 1's live settled "
      "total equals the durable settled total, and nothing is open or reserved "
      "on either side ***",
      (near(sum(v[1] for k, v in (at(_d1, "tally") or {}).items()
                if k != _spend.LIABILITY_OPEN),
            sum(r[3] for r in _e_conf["rows"] if r[1] == "settled")),
       [r for r in _e_conf["rows"] if r[1] == "reserved"],
       (at(_d1, "tally") or {}).get(_spend.LIABILITY_OPEN, [0])[0]),
      (True, [], 0))
check("7y-ii process 3 reads the SAME seed (repeat reads do not grow it)",
      near(at(at(_d3, "seed"), "usd"), at(at(_d2, "seed"), "usd")), True)

_e_cdef = p1b_e2e("conflict_deferred", "settle_conflict_deferred")
_d1, _d2, _d3 = _e_cdef["d"]
check("7z *** CONFLICT DEFERRED: process 1 leaves ONE marker and no "
      "discrepancy row; process 2 RECONCILES it, prints the amount, removes "
      "the marker, and its seed EQUALS process 1's live ledger ***",
      (len(_e_cdef["markers"][0]), _e_cdef["markers"][1],
       "Reconciled a deferred billing discrepancy (conflict)"
       in _e_cdef["out"][1],
       _e_cdef["p"][1].returncode,
       near(at(at(_d2, "seed"), "usd"), at(_d1, "measured")),
       len(_e_cdef["discrepancies"])),
      (1, [], True, 0, True, 1))
check("7z-i *** process 3: nothing left to reconcile, the SAME seed -- no "
      "double count ***",
      ("Reconciled a deferred" in _e_cdef["out"][2],
       near(at(at(_d3, "seed"), "usd"), at(at(_d2, "seed"), "usd"))),
      (False, True))

_e_mdef = p1b_e2e("missing_deferred", "settle_missing_deferred")
check("7z-ii *** MISSING DEFERRED: process 2 reconciles the marker and then "
      "REFUSES as incomplete; process 3 refuses without reconciling again ***",
      (len(_e_mdef["markers"][0]), _e_mdef["markers"][1],
       "Reconciled a deferred billing discrepancy (missing)" in _e_mdef["out"][1],
       _REFUSE_INCOMPLETE in _e_mdef["out"][1], _e_mdef["p"][1].returncode,
       "Reconciled a deferred" in _e_mdef["out"][2],
       _REFUSE_INCOMPLETE in _e_mdef["out"][2], len(_e_mdef["discrepancies"])),
      (1, [], True, True, 1, False, True, 1))

_e_unr = p1b_e2e("missing_unrecordable", "settle_missing_unrecordable")
_d1, _d2, _d3 = _e_unr["d"]
check("7z-iii *** THE STATED BOUND, THROUGH main(): with the database AND the "
      "marker refusing, process 1 latches and prints 'failed'; process 2 finds "
      "no durable trace and its seed is BELOW process 1's live ledger ***",
      (at(_d1, "latch"), "failed." in _e_unr["out"][0],
       (at(_d1, "billing_faults") or {}).get("discrepancy:missing:failed"),
       _e_unr["markers"], at(at(_d2, "seed"), "usd") is not None
       and at(at(_d2, "seed"), "usd") < at(_d1, "measured")),
      ([True, _spend.SPEND_LIMIT_BILLING_RECORD], True, 1, ([], []), True))


# ===========================================================================
section("SECTION 8 -- P1c: a settlement's STORED outcome, not its returned one")
# ===========================================================================
#
# A SETTLEMENT CAN COMMIT AND THEN LOSE ITS ACKNOWLEDGEMENT. Measured before the
# P1c change, in fresh processes through the real main(): the real settlement
# committed $0.0032 and reported `failed`; the live run charged the $0.38484
# reservation and a fresh process seeded at $0.0032, with no discrepancy row --
# a resumed figure below the live conservative total. `AttemptLiability` now
# reads the row back and decides from what is STORED.

_P_LOW = _spend.price_usage(_WIRE, 321, 45)[0]
_P_HIGH = _spend.price_usage(_WIRE, 10 ** 7, 45)[0]
_CONSOLE_START = _spend.console


def _commit_then(exc=None):
    """The REAL settlement commits; then its acknowledgement is lost."""
    def settle(db_path, attempt_id, **kw):
        res = _REAL_SETTLE(db_path, attempt_id, **kw)
        if res != _dl.SETTLE_SETTLED:
            return res
        if exc is not None:
            raise exc
        return _dl.SETTLE_FAILED
    return settle


def _foreign_settle_then_failed(amount, outcome):
    """Something else settles the row; this process's settlement reports
    `failed` without writing."""
    def settle(db_path, attempt_id, **kw):
        _REAL_SETTLE(db_path, attempt_id, outcome=outcome, settled_usd=amount)
        return _dl.SETTLE_FAILED
    return settle


def _sql_then_failed(sql, params_of):
    def settle(db_path, attempt_id, **kw):
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(sql, params_of(attempt_id))
            conn.commit()
        finally:
            conn.close()
        return _dl.SETTLE_FAILED
    return settle


def _s5_low():
    return _Client(chat=lambda kw: _chat_response((321, 45)))


def _fault(case, key):
    faults = at(case["live"], "record_faults")
    return faults.get(key) if isinstance(faults, dict) else faults


def _meas(case):
    return case["live"].get("measured")


check("8a non-degeneracy: the priced response is BELOW its reservation and the "
      "large one ABOVE it, so trusting `failed` and trusting the stored row "
      "give different amounts",
      (_P_LOW < _RESERVE_S5 - 1e-6, _P_HIGH > _RESERVE_S5 + 1e-6), (True, True))

_c8a = p1b_case("p1c_landed_ack_failed", _s5_low(), _s5_call,
                patches={"settle_billing_attempt": _commit_then()})
check("8a-i *** COMMITTED, THEN REPORTED `failed`: the row is read back and the "
      "settlement recognised as LANDED -- live is the priced amount, no top-up, "
      "no discrepancy row, no latch ***",
      (_kinds(_c8a), near(_meas(_c8a), _P_LOW), at(_c8a["live"], "latch"),
       _fault(_c8a, "settle:verified_landed:failed")),
      ([("attempt", "settled")], True, (False, None), 1))
check("8a-ii *** ...and a fresh reading EQUALS live: settled, unresolved, total "
      "and remaining ***", parity_holds(_c8a), (True, True, True, True))

_c8b = p1b_case("p1c_landed_ack_raised", _s5_low(), _s5_call,
                patches={"settle_billing_attempt":
                         _commit_then(OSError(5, "planted EIO after commit"))})
check("8b *** COMMITTED, THEN THE SINK RAISED: verified landed the same way, and "
      "a fresh reading equals live ***",
      (_kinds(_c8b), near(_meas(_c8b), _P_LOW), at(_c8b["live"], "latch"),
       _fault(_c8b, "settle:raised:OSError"),
       _fault(_c8b, "settle:verified_landed:failed"), parity_holds(_c8b)),
      ([("attempt", "settled")], True, (False, None), 1, 1,
       (True, True, True, True)))

_c8c = p1b_case("p1c_over_landed_ack_failed",
                _Client(chat=lambda kw: _chat_response((10 ** 7, 45))),
                _s5_call, patches={"settle_billing_attempt": _commit_then()})
check("8c a response priced ABOVE its reservation that LANDED and lost its "
      "acknowledgement: verified at the priced amount, no discrepancy owed, "
      "parity holds",
      (_kinds(_c8c), near(_meas(_c8c), _P_HIGH), parity_holds(_c8c)),
      ([("attempt", "settled")], True, (True, True, True, True)))

_c8d = p1b_case("p1c_not_landed_verified", _s5_low(), _s5_call,
                patches={"settle_billing_attempt": _returns(_dl.SETTLE_FAILED)})
check("8d a `failed` that really did NOT land is VERIFIED reserved: read at the "
      "reservation as before, no discrepancy, no latch, parity holds",
      (_kinds(_c8d), near(_meas(_c8d), _RESERVE_S5), at(_c8d["live"], "latch"),
       _fault(_c8d, "settle:verified_reserved:failed"), parity_holds(_c8d)),
      ([("attempt", "reserved")], True, (False, None), 1,
       (True, True, True, True)))

_c8e = p1b_case("p1c_unverified_landed", _s5_low(), _s5_call,
                patches={"settle_billing_attempt": _commit_then(),
                         "billing_attempt_stored_state": _returns(None)})
_disc8e = [r for r in _c8e["rows"] if r[1] == "settlement_discrepancy"]
check("8e *** THE STORED OUTCOME CANNOT BE READ and the commit HAD landed: live "
      "keeps the conservative reservation, the discrepancy row carries "
      "reservation - priced, and a fresh reading EQUALS live ***",
      (_kinds(_c8e), near(_meas(_c8e), _RESERVE_S5),
       near(at(at(_disc8e, 0), 5), _RESERVE_S5 - _P_LOW),
       near(getattr(_c8e["durable"], "usd", None), _meas(_c8e)),
       near(_c8e["resumed_remaining"], _c8e["live"].get("remaining"))),
      ([("attempt", "settled"), ("settlement_discrepancy", "settled")], True,
       True, True, True))
check("8e-i ...settled and unresolved separately: live settled == durable "
      "settled; nothing open live, nothing reserved durably",
      (near(_c8e["settled_live"], _c8e["settled_durable"]),
       _c8e["open_live"][0], _c8e["reserved_durable"][0]), (True, 0, 0))
check("8e-ii ...the run LATCHES with cause `unverified`, and the faults name the "
      "path",
      (at(_c8e["live"], "latch"), at(_c8e["live"], "cause"),
       _fault(_c8e, "settle:unverified:failed"),
       _fault(_c8e, "discrepancy:unverified:recorded")),
      ((True, _spend.SPEND_LIMIT_BILLING_RECORD),
       _spend.BILLING_RECORD_CAUSE_UNVERIFIED, 1, 1))

_c8f = p1b_case("p1c_unverified_not_landed", _s5_low(), _s5_call,
                patches={"settle_billing_attempt": _returns(_dl.SETTLE_FAILED),
                         "billing_attempt_stored_state": _returns(None)})
check("8f *** THE STORED OUTCOME CANNOT BE READ and the commit had NOT landed: "
      "the fresh reading is ABOVE live by exactly reservation - priced (the "
      "safe direction, never below), and the run latches ***",
      (_kinds(_c8f), near(_meas(_c8f), _RESERVE_S5),
       near((getattr(_c8f["durable"], "usd", None) or 0) - (_meas(_c8f) or 0),
            _RESERVE_S5 - _P_LOW),
       isinstance(_c8f["resumed_remaining"], float)
       and _c8f["resumed_remaining"] < _c8f["live"].get("remaining", 0),
       at(_c8f["live"], "latch")),
      ([("attempt", "reserved"), ("settlement_discrepancy", "settled")], True,
       True, True, (True, _spend.SPEND_LIMIT_BILLING_RECORD)))

_c8g = p1b_case("p1c_verified_conflict", _s5_low(), _s5_call,
                patches={"settle_billing_attempt":
                         _foreign_settle_then_failed(0.0, "not_billed")})
_disc8g = [r for r in _c8g["rows"] if r[1] == "settlement_discrepancy"]
check("8g *** `failed` reported while the row holds a settlement this process "
      "did not write: VERIFIED as a CONFLICT -- the stored row stands, the "
      "shortfall is its own row, the run latches with cause `conflict`, and a "
      "fresh reading EQUALS live ***",
      (_kinds(_c8g), _note_result(at(_disc8g, 0) or [None] * 7),
       at(_c8g["live"], "cause"), _fault(_c8g, "settle:verified_conflict:failed"),
       parity_holds(_c8g)),
      ([("attempt", "settled"), ("settlement_discrepancy", "settled")],
       "conflict", _spend.BILLING_RECORD_CAUSE_CONFLICT, 1,
       (True, True, True, True)))

_c8h = p1b_case("p1c_verified_missing", _s5_low(), _s5_call,
                patches={"settle_billing_attempt": _sql_then_failed(
                    "DELETE FROM billing_attempts WHERE attempt_id = ?",
                    lambda aid: (aid,))})
check("8h `failed` reported while the row is GONE: VERIFIED missing -- a "
      "discrepancy row retains the live charge, the run latches with cause "
      "`missing`, and a fresh reading refuses as incomplete",
      (_kinds(_c8h), at(_c8h["live"], "cause"),
       type(_c8h["durable_exc"]).__name__,
       near(getattr(_c8h["durable_exc"], "retained_usd", None), _meas(_c8h))),
      ([("settlement_discrepancy", "settled")],
       _spend.BILLING_RECORD_CAUSE_MISSING, "BillingRecordIncomplete", True))

_c8i = p1b_case("p1c_rereserved", _s5_low(), _s5_call,
                patches={"settle_billing_attempt": _sql_then_failed(
                    "UPDATE billing_attempts SET reserved_usd = 7.0 WHERE "
                    "attempt_id = ?", lambda aid: (aid,))})
check("8i `failed` reported while the RESERVED row carries an amount this "
      "process did not reserve: a CONFLICT at that amount -- live is raised to "
      "it, the run latches, and a fresh reading EQUALS live",
      (near(_meas(_c8i), _ABOVE_BOUND), at(_c8i["live"], "cause"),
       near(getattr(_c8i["durable"], "usd", None), _meas(_c8i)),
       near(_c8i["resumed_remaining"], _c8i["live"].get("remaining"))),
      (True, _spend.BILLING_RECORD_CAUSE_CONFLICT, True, True))


class _NoStateSink(_dl.BillingRecordSink):
    """A duck-typed sink of the P1b shape: it cannot read a row back."""
    stored_state = None


_db8j = new_db("p1c_nostate.db")
_run8j = _dl.start_run_record("batch", db_path=_db8j, fingerprint=FIXED_FP)
_live8j = {}
deps.set_override(deps.OPENAI_CLIENT, _s5_low())
try:
    with settings(SPEND_CAP_USD=_CAP, SPEND_CAP_ENFORCED=True):
        _reset_spend_state()
        _dl.settle_billing_attempt = _returns(_dl.SETTLE_FAILED)
        _spend.BILLING_RECORD.install(_NoStateSink(_db8j, "camp-nostate",
                                                   _run8j))
        try:
            raised(_s5_call)
            _live8j = {"measured": _spend.SPEND_LEDGER.measured,
                       "faults": dict(_spend.BILLING_RECORD_FAULTS),
                       "cause": _spend.SPEND_STOP.cause}
        finally:
            _spend.BILLING_RECORD.clear()
            _restore_dl()
finally:
    _restore_dl()
    deps.clear_override(deps.OPENAI_CLIENT)
    _reset_spend_state()
check("8j a sink that CANNOT read a row back never has a reported failure "
      "trusted: handled as unverified -- conservative live, a discrepancy row, "
      "latched -- and the missing reader is counted",
      (near(_live8j.get("measured"), _RESERVE_S5),
       (_live8j.get("faults") or {}).get("verify:no_state_reader"),
       _live8j.get("cause"),
       [(r[0], r[1]) for r in ro_rows(_db8j, "SELECT kind, state FROM "
                                             "billing_attempts ORDER BY rowid")]),
      (True, 1, _spend.BILLING_RECORD_CAUSE_UNVERIFIED,
       [("attempt", "reserved"), ("settlement_discrepancy", "settled")]))

_c8k = p1b_case("p1c_malformed_state", _s5_low(), _s5_call,
                patches={"settle_billing_attempt": _returns(_dl.SETTLE_FAILED),
                         "billing_attempt_stored_state": _returns(
                             {"state": "settled", "reserved_usd": 1.0,
                              "settled_usd": "x", "outcome": "response"})})
check("8k a MALFORMED read-back is not a state: unverified, counted, latched",
      (_fault(_c8k, "verify:malformed"), at(_c8k["live"], "cause"),
       near(_meas(_c8k), _RESERVE_S5)),
      (1, _spend.BILLING_RECORD_CAUSE_UNVERIFIED, True))

_db8l = new_db("p1c_reader.db")
_run8l = _dl.start_run_record("batch", db_path=_db8l, fingerprint=FIXED_FP)
for _aid, _usd in (("r8-res", 0.5), ("r8-set", 0.7), ("r8-bad", 0.9)):
    _dl.reserve_billing_attempt(_db8l, attempt_id=_aid, campaign_id="camp-r8",
                                run_id=_run8l, source="stage5", model=_WIRE,
                                input_tokens=1, output_tokens=1,
                                reserved_usd=_usd)
_dl.settle_billing_attempt(_db8l, "r8-set", outcome="response", settled_usd=0.25)
_conn8l = sqlite3.connect(_db8l)
_conn8l.execute("UPDATE billing_attempts SET reserved_usd = 'x' WHERE "
                "attempt_id = 'r8-bad'")
_conn8l.commit()
_conn8l.close()
check("8l the real stored-state reader: absent, reserved and settled are read "
      "back with their amounts; an unopenable database and an unsummable "
      "amount are NOT established (None), never a state",
      (drive(_dl.billing_attempt_stored_state, _db8l, "r8-none"),
       drive(_dl.billing_attempt_stored_state, _db8l, "r8-res"),
       drive(_dl.billing_attempt_stored_state, _db8l, "r8-set"),
       drive(_dl.billing_attempt_stored_state, _db8l, "r8-bad"),
       drive(_dl.billing_attempt_stored_state,
             os.path.join(_TMP, "no-such-dir", "x.db"), "r8-res")),
      ({"state": "absent"},
       {"state": "reserved", "reserved_usd": 0.5, "outcome": None},
       {"state": "settled", "reserved_usd": 0.7, "settled_usd": 0.25,
        "outcome": "response"}, None, None))
check("8m the stored-state and banner vocabularies are restated equal across "
      "the layers, the causes are distinct, and the settled-amount reader is a "
      "projection of the stored-state reader",
      ((_spend.STORED_STATE_ABSENT, _spend.STORED_STATE_RESERVED,
        _spend.STORED_STATE_SETTLED)
       == (_dl.STORED_STATE_ABSENT, _dl.BILLING_ATTEMPT_STATE_RESERVED,
           _dl.BILLING_ATTEMPT_STATE_SETTLED),
       set(_spend._BILLING_RECORD_BANNER) == set(_spend.BILLING_RECORD_CAUSES)
       | {None},
       len(set(_spend.BILLING_RECORD_CAUSES)) == len(_spend.BILLING_RECORD_CAUSES),
       drive(_dl.billing_attempt_settled_usd, _db8l, "r8-set"),
       drive(_dl.billing_attempt_settled_usd, _db8l, "r8-res")),
      (True, True, True, 0.25, None))


def _banner(limit, cause):
    """The latch banner, captured at the console surface, and the recorded cause."""
    lines = []
    _spend.SPEND_STOP.reset()
    _spend.console = types.SimpleNamespace(
        out=lambda *a, **k: lines.append(" ".join(str(x) for x in a)))
    try:
        with settings(SPEND_CAP_USD=_CAP, SPEND_CAP_ENFORCED=True):
            _spend.SPEND_STOP.trip(limit, "a P1c banner probe",
                                   _spend.SPEND_SOURCE_STAGE5, cause)
            recorded = _spend.SPEND_STOP.cause
    finally:
        _spend.console = _CONSOLE_START
        _spend.SPEND_STOP.reset()
    return "\n".join(lines), recorded


# PHRASES WRITTEN HERE, NOT READ FROM THE TABLE, so a wrong table entry fails.
_PHRASE = {
    _spend.BILLING_RECORD_CAUSE_WRITE_FAILED: "RESERVATION COULD NOT BE WRITTEN",
    _spend.BILLING_RECORD_CAUSE_UNPRICED: "RESERVATION COULD NOT BE PRICED",
    _spend.BILLING_RECORD_CAUSE_DEFERRED: "DEFERRED TO A MARKER FILE",
    _spend.BILLING_RECORD_CAUSE_DISCREPANCY_UNRECORDED:
        "COULD NOT BE WRITTEN TO THE DATABASE OR TO A MARKER FILE",
    _spend.BILLING_RECORD_CAUSE_MISSING: "THIS RUN COMMITTED IS GONE FROM",
    _spend.BILLING_RECORD_CAUSE_CONFLICT:
        "HOLDS A SETTLEMENT THIS RUN DID NOT WRITE",
    _spend.BILLING_RECORD_CAUSE_UNVERIFIED: "COULD NOT BE READ BACK",
    None: "CAN NO LONGER BE TRUSTED",
    "not-a-cause": "CAN NO LONGER BE TRUSTED",
}
_B = {c: _banner(_spend.SPEND_LIMIT_BILLING_RECORD, c) for c in _PHRASE}
_CEILING = _banner(_spend.SPEND_LIMIT_CALL_CEILING,
                   _spend.BILLING_RECORD_CAUSE_MISSING)
check("8n *** THE BANNER, AT THE CONSOLE: each billing-record cause prints its "
      "own sentence ***",
      {str(c): _PHRASE[c] in _B[c][0] for c in _PHRASE},
      {str(c): True for c in _PHRASE})
check("8n-i *** 'COULD NOT BE WRITTEN' is printed ONLY where a write failed -- "
      "not for a missing row, a conflict, an unverified outcome, an unpriced "
      "reservation, or an unknown cause ***",
      {str(c): "COULD NOT BE WRITTEN" in _B[c][0] for c in _PHRASE},
      {str(c): c in (_spend.BILLING_RECORD_CAUSE_WRITE_FAILED,
                     _spend.BILLING_RECORD_CAUSE_DEFERRED,
                     _spend.BILLING_RECORD_CAUSE_DISCREPANCY_UNRECORDED)
       for c in _PHRASE})
check("8n-ii no billing-record banner tells the operator to raise the spend cap; "
      "CONTROL: the call-ceiling banner still does",
      ({str(c): "raise config.SPEND_CAP_USD" in _B[c][0] for c in _PHRASE},
       "raise config.SPEND_CAP_USD" in _CEILING[0]),
      ({str(c): False for c in _PHRASE}, True))
check("8n-iii non-degeneracy: every banner was printed and states the stop; the "
      "latch records the billing-record cause, and records NONE for another "
      "limit",
      ({str(c): "no further billed request may be dispatched" in _B[c][0]
        for c in _PHRASE}, _B[_spend.BILLING_RECORD_CAUSE_CONFLICT][1],
       _CEILING[1], _spend.console is _CONSOLE_START),
      ({str(c): True for c in _PHRASE}, _spend.BILLING_RECORD_CAUSE_CONFLICT,
       None, True))

_db8p = new_db("p1c_latched_message.db")
_run8p = _dl.start_run_record("batch", db_path=_db8p, fingerprint=FIXED_FP)
_reset_spend_state()
_spend.console = types.SimpleNamespace(out=lambda *a, **k: None)
try:
    _spend.SPEND_STOP.trip(_spend.SPEND_LIMIT_BILLING_RECORD, "probe",
                           _spend.SPEND_SOURCE_STAGE5,
                           _spend.BILLING_RECORD_CAUSE_CONFLICT)
    _spend.BILLING_RECORD.install(_dl.BillingRecordSink(_db8p, "camp-8p",
                                                        _run8p))
    _exc8p = raised(_spend.BILLING_RECORD.reserve, _spend.SPEND_SOURCE_STAGE5,
                    _WIRE, 1, 1, where="probe")
finally:
    _spend.console = _CONSOLE_START
    _reset_spend_state()
check("8p a reservation refused because the run is latched names the CAUSE, and "
      "no longer says the record 'failed'",
      (type(_exc8p).__name__, "(conflict)" in str(_exc8p),
       "failed earlier" in str(_exc8p)),
      ("BillingRecordUnavailable", True, False))

check("8o *** AT THE PRINTED SURFACE, THROUGH main() (section 7's runs): the "
      "MISSING run prints the missing sentence and the CONFLICT run the "
      "conflict sentence; neither prints 'COULD NOT BE WRITTEN' or tells the "
      "operator to raise the cap; the child recorded each cause ***",
      (_PHRASE[_spend.BILLING_RECORD_CAUSE_MISSING] in _e_miss["out"][0],
       _PHRASE[_spend.BILLING_RECORD_CAUSE_CONFLICT] in _e_conf["out"][0],
       "COULD NOT BE WRITTEN" in _e_miss["out"][0] + _e_conf["out"][0],
       "raise config.SPEND_CAP_USD" in _e_miss["out"][0] + _e_conf["out"][0],
       at(_e_miss["d"][0], "latch_cause"), at(_e_conf["d"][0], "latch_cause")),
      (True, True, False, False, _spend.BILLING_RECORD_CAUSE_MISSING,
       _spend.BILLING_RECORD_CAUSE_CONFLICT))


def _live_settled(d):
    tally = at(d, "tally")
    return (sum(v[1] for k, v in tally.items() if k != _spend.LIABILITY_OPEN)
            if isinstance(tally, dict) else tally)


def _live_open(d):
    tally = at(d, "tally")
    return ((tally.get(_spend.LIABILITY_OPEN) or [0])[0]
            if isinstance(tally, dict) else tally)


def _faults_of(d):
    faults = at(d, "billing_faults")
    return faults if isinstance(faults, dict) else {}


for _inject, _key in (("ack_failed", "settle:failed"),
                      ("ack_raised", "settle:raised:OSError")):
    _e = p1b_e2e(f"p1c_{_inject}", _inject)
    _x1, _x2, _x3 = _e["d"]
    check(f"8q-{_inject} non-degeneracy: process 1 made every wire attempt, EVERY "
          f"settlement committed and then lost its acknowledgement, and at least "
          f"one priced liability is below its reservation",
          (at(_x1, "calls"), _faults_of(_x1).get(_key),
           sum(1 for r in _e["rows"] if r[1] == "settled" and r[3] is not None
               and r[3] < r[2] - 1e-6) > 0),
          (12, 12, True))
    check(f"8q-{_inject}-i *** FRESH PROCESSES: every settlement VERIFIED landed; "
          f"process 2 continues with a seed EQUAL to process 1's live ledger and "
          f"a remaining EQUAL to process 1's; process 3 reads the same seed ***",
          (_faults_of(_x1).get("settle:verified_landed:failed"), at(_x1, "latch"),
           _e["p"][1].returncode, near(at(at(_x2, "seed"), "usd"),
                                       at(_x1, "measured")),
           near(at(_x2, "remaining"), at(_x1, "remaining")),
           near(at(at(_x3, "seed"), "usd"), at(at(_x2, "seed"), "usd"))),
          (12, [False, None], 0, True, True, True))
    check(f"8q-{_inject}-ii *** settled and unresolved SEPARATELY: live settled "
          f"== durable settled; nothing open live, nothing reserved durably, "
          f"zero unresolved in the resumed seed, no discrepancy row ***",
          (near(_live_settled(_x1),
                sum(r[3] for r in _e["rows"] if r[1] == "settled")),
           [r for r in _e["rows"] if r[1] == "reserved"], _live_open(_x1),
           at(at(_x2, "seed"), "unresolved"), len(_e["discrepancies"])),
          (True, [], 0, 0, 0))

_e_un = p1b_e2e("p1c_ack_unverifiable", "ack_unverifiable")
_u1, _u2, _u3 = _e_un["d"]
check("8s *** THE STORED OUTCOME UNREADABLE, FRESH PROCESSES: process 1 latches "
      "with cause `unverified` and prints so, not 'COULD NOT BE WRITTEN'; "
      "process 2 continues with a seed EQUAL to process 1's live ledger and an "
      "equal remaining; process 3 reads the same seed ***",
      (at(_u1, "latch"), at(_u1, "latch_cause"),
       _PHRASE[_spend.BILLING_RECORD_CAUSE_UNVERIFIED] in _e_un["out"][0],
       "COULD NOT BE WRITTEN" in _e_un["out"][0], _e_un["p"][1].returncode,
       near(at(at(_u2, "seed"), "usd"), at(_u1, "measured")),
       near(at(_u2, "remaining"), at(_u1, "remaining")),
       near(at(at(_u3, "seed"), "usd"), at(at(_u2, "seed"), "usd"))),
      ([True, _spend.SPEND_LIMIT_BILLING_RECORD],
       _spend.BILLING_RECORD_CAUSE_UNVERIFIED, True, False, 0, True, True, True))
check("8s-i settled and unresolved separately: live settled == durable settled "
      "(attempt rows plus their discrepancy rows); nothing open or reserved; at "
      "least one settlement was unverified and at least one discrepancy row "
      "exists",
      (near(_live_settled(_u1),
            sum(r[3] for r in _e_un["rows"] if r[1] == "settled")),
       [r for r in _e_un["rows"] if r[1] == "reserved"], _live_open(_u1),
       (_faults_of(_u1).get("settle:unverified:failed") or 0) >= 1,
       len(_e_un["discrepancies"]) >= 1),
      (True, [], 0, True, True))
check("8t the patched storage functions and the console are restored",
      (_dl.billing_attempt_stored_state is _DL_START["billing_attempt_stored_state"],
       _dl.settle_billing_attempt is _SETTLE_START,
       _spend.console is _CONSOLE_START), (True, True, True))


# ===========================================================================
section("SECTION 9 -- R1: a Stage 5 reservation is the documented-limit bound "
        "(for Sonnet 4.6 on Bedrock, conditional on ASSUMED long-context "
        "multipliers; see 9q)")
# ===========================================================================
#
# The independent derivation (_DOC_LIMITS, _indep_bound) is defined once, at
# SECTION 1, because the P1..P1c expectations above read it too.


def _owner(model, requested_out, attempts=1):
    b = drive(config.stage5_attempt_bound, model, requested_out, attempts)
    return ((b["input_tokens"], b["output_tokens"], b["usd"])
            if isinstance(b, dict) else b)


def _same(a, b):
    return (isinstance(a, tuple) and isinstance(b, tuple) and a[:2] == b[:2]
            and near(a[2], b[2]))


# A PLANTED DEFECT CAN LEAVE A VALUE ABSENT OR NONE; these keep the checks below
# from ABORTING the file on a comparison and report a failure instead.
def _round9(v):
    return (round(v, 9) if isinstance(v, (int, float))
            and not isinstance(v, bool) else v)


def _both_numbers(a, b):
    return all(isinstance(x, (int, float)) and not isinstance(x, bool)
               for x in (a, b))


_M = config.MATCHING_MAX_TOKENS
check("9a *** THE OWNER AGREES WITH THE INDEPENDENT DERIVATION for every wire "
      "model the three arms dispatch to, trial and warmup ceilings ***",
      {m: (_same(_owner(m, _M), _indep_bound(m, _M)),
           _same(_owner(m, 1), _indep_bound(m, 1))) for m in _DOC_LIMITS},
      {m: (True, True) for m in _DOC_LIMITS})
check("9a-i the shipped arm's numbers, as dollar literals: $9.042 a trial "
      "attempt, $8.250025 a warmup, on 1,000,000 / 32,000 tokens",
      (_M, at(_owner("us.anthropic.claude-sonnet-4-6", _M), 0),
       near(at(_owner("us.anthropic.claude-sonnet-4-6", _M), 2), 9.042, 1e-6),
       near(at(_owner("us.anthropic.claude-sonnet-4-6", 1), 2), 8.25002475,
            1e-9)),
      (32000, 1_000_000, True, True))
check("9a-ii the dormant arms, as literals: gpt-5.6-terra $5.826, "
      "us.openai.gpt-5.6-terra $6.1336",
      (near(at(_owner("gpt-5.6-terra", _M), 2), 5.826, 1e-9),
       near(at(_owner("us.openai.gpt-5.6-terra", _M), 2), 6.1336, 1e-9)),
      (True, True))

_SONNET = "us.anthropic.claude-sonnet-4-6"
check("9b *** THE CACHE-WRITE CLASS IS INCLUDED: the request's TTL is priced; a "
      "1h TTL raises the bound; no cache point prices the dearest TTL; Terra's "
      "1.25x write is above its input ***",
      (with_settings_value("BEDROCK_ANTHROPIC_CACHE_TTL", "1h",
                           lambda: _same(_owner(_SONNET, _M),
                                         _indep_bound(_SONNET, _M, ttl="1h"))),
       with_settings_value("BEDROCK_ANTHROPIC_CACHE_TTL", None,
                           lambda: _same(_owner(_SONNET, _M),
                                         _indep_bound(_SONNET, _M, ttl=None))),
       at(_owner(_SONNET, _M), 2) < _indep_bound(_SONNET, _M, ttl="1h")[2],
       config.stage5_attempt_bound("gpt-5.6-terra", _M, 1)[
           "input_rate_per_mtok"] == 2.00 * 1.25 * 2.0),
      (True, True, True, True))
check("9b-i the output ceiling is the REQUEST's, clamped to the model's "
      "documented maximum; wire attempts multiply BOTH halves",
      (at(_owner(_SONNET, 100_000), 1), at(_owner(_SONNET, 1), 1),
       _same(_owner(_SONNET, _M, 3), _indep_bound(_SONNET, _M, 3)),
       near(at(_owner(_SONNET, _M, 3), 2), 3 * at(_owner(_SONNET, _M), 2),
            1e-9)),
      (64_000, 1, True, True))


def _old_estimate_usd(text_chars, max_out):
    schema = len(str(_ev.build_response_format()))
    tokens = int((text_chars + schema) / 3.0) + 1
    return _spend.price_usage(_WIRE, tokens, max_out)[0], tokens


_old_cjk, _old_cjk_tokens = _old_estimate_usd(20_000, _M)
_schema_chars = len(str(_ev.build_response_format()))
check("9c *** THE OLD ESTIMATE WAS NOT A BOUND: 20,000 CJK characters a "
      "tokenizer bills one token each exceed chars/3; the documented bound "
      "covers that and the whole window at the dearest class ***",
      (_old_cjk_tokens < 20_000 + _schema_chars,
       _spend.price_usage(_WIRE, 20_000 + _schema_chars, _M)[0] > _old_cjk,
       _spend.price_usage(_WIRE, 1_050_000, _M)[0]
       <= at(_owner(_WIRE, _M), 2),
       near(at(_owner(_WIRE, _M), 2),
            (1_050_000 * 5.00 + _M * 18.00) / 1e6, 1e-9)),
      (True, True, True, True))

# THE BOUND DOES NOT READ THE TEXT, measured through the REAL executor with the
# policy stubbed out so nothing is dispatched: the attempt record carries the
# same reservation for every prompt, while the PACER's estimate follows it.
_EXEC_SEEN = []
_REAL_EXECUTE = _pr.execute


def _capture_execute(send, **kw):
    _EXEC_SEEN.append(kw)
    return "not-dispatched"


_PROMPTS = {"empty": "", "nul": "\x00" * 10, "one": "a",
            "cjk": "中" * 200_000, "emoji": "\U0001F600" * 50_000,
            "combining": "é" * 30_000, "four_mb": "x" * (4 * 1024 * 1024)}
_pr.execute = _capture_execute
try:
    for _name, _text in _PROMPTS.items():
        drive(_ev._execute_matching_call, lambda: None, _text, _text,
              max_output=_M, drain_applies=False)
finally:
    _pr.execute = _REAL_EXECUTE
_recs = [kw.get("attempt_record") for kw in _EXEC_SEEN]
check("9d *** EVERY PROMPT -- empty, NUL, 1 char, 200k CJK, 50k emoji, combining "
      "marks, 4 MB -- reserves the SAME documented bound; the pacer's estimate "
      "is unchanged and still follows the text ***",
      (len(_recs), sorted({(getattr(r, "_input", None),
                            getattr(r, "_output", None),
                            _round9(getattr(r, "_reserved_usd", None)))
                           for r in _recs}, key=repr),
       [kw.get("reservation_tokens") for kw in _EXEC_SEEN]
       == [_ev._reservation_input_tokens(t, t) + _M for t in _PROMPTS.values()],
       len({kw.get("reservation_tokens") for kw in _EXEC_SEEN}) > 1),
      (len(_PROMPTS), [(1_050_000, _M, round(5.826, 9))], True, True))
_note = drive(json.loads, getattr(at(_recs, 0), "_note", None))
check("9d-i the reservation note records the basis, its version, the request's "
      "ceiling and the wire attempts",
      _note, {"basis_version": config.STAGE5_RESERVATION_BASIS_VERSION,
              "requested_output_tokens": _M,
              "reservation_basis": config.STAGE5_RESERVATION_BASIS,
              "wire_attempts": 1})
check("9d-ii the policy and the helper are restored", _pr.execute is _REAL_EXECUTE,
      True)

# REFUSAL: the shipped check refuses every shape that is not a sound bound.
_REFUSE_CASES = {
    "unknown model": ("no-such-model", _M, 1),
    "priced, undocumented model": ("gpt-4o-2024-08-06", _M, 1),
    "None model": (None, _M, 1),
    "unhashable model": ([], _M, 1),
    "bool ceiling": (_SONNET, True, 1),
    "zero ceiling": (_SONNET, 0, 1),
    "zero wire attempts": (_SONNET, _M, 0),
    "bool wire attempts": (_SONNET, _M, True),
}
check("9e *** NO SOUND BOUND IS REFUSED BY NAME: unknown, priced-but-"
      "undocumented, None and unhashable models, and bad ceilings or attempts "
      "***",
      {k: type(raised(config.stage5_attempt_bound, *v)).__name__
       for k, v in _REFUSE_CASES.items()},
      {k: "Stage5ReservationUnbounded" for k in _REFUSE_CASES})


def _with_limits(model, **changes):
    saved = config.STAGE5_ATTEMPT_LIMITS.get(model)
    config.STAGE5_ATTEMPT_LIMITS[model] = dict(saved, **changes)
    try:
        return type(raised(config.stage5_attempt_bound, model, _M, 1)).__name__
    finally:
        config.STAGE5_ATTEMPT_LIMITS[model] = saved


check("9e-i a malformed documented-limits entry refuses: a bool window, a zero "
      "output limit, a multiplier below 1, no cache-write class at all",
      (_with_limits(_SONNET, context_window_tokens=True),
       _with_limits(_SONNET, max_output_tokens=0),
       _with_limits("gpt-5.6-terra", long_context_input_multiplier=0.5),
       _with_limits("gpt-5.6-terra", cache_write_multiplier=None),
       config.STAGE5_ATTEMPT_LIMITS[_SONNET]["context_window_tokens"]),
      ("Stage5ReservationUnbounded",) * 4 + (1_000_000,))

_c_refuse = p1b_case("r1_refused",
                     _Client(chat=lambda kw: _chat_response((1000, 100))),
                     lambda: _ev.call_matching_model("system prompt",
                                                     "user prompt"),
                     knobs={"MATCHING_MODEL": "gpt-4o-2024-08-06"})
check("9f *** REFUSED BEFORE DISPATCH, THROUGH THE REAL call_matching_model: a "
      "priced wire model with no documented limits raises by name with ZERO "
      "provider calls, ZERO billing rows and ZERO spend ***",
      (type(_c_refuse["exc"]).__name__, _c_refuse["calls"], _c_refuse["rows"],
       _c_refuse["live"].get("measured")),
      ("Stage5ReservationUnbounded", 0, [], 0.0))
check("9f-i the node-top guard refuses the same configuration once, and passes "
      "the shipped one in both call modes",
      (with_settings_value("MATCHING_MODEL", "gpt-4o-2024-08-06",
                           lambda: type(raised(
                               _ev.assert_stage5_reservation_bounded,
                               per_trial=True)).__name__),
       raised(_ev.assert_stage5_reservation_bounded, per_trial=True),
       raised(_ev.assert_stage5_reservation_bounded, per_trial=False)),
      ("Stage5ReservationUnbounded", None, None))
# BY AST, NOT BY TEXT: the node's own comments name call_matching_model
# hundreds of lines above the guard, so a text search reports prose.
_node_calls = sorted(
    (n.lineno, getattr(n.func, "id", None) or getattr(n.func, "attr", ""))
    for n in ast.walk(ast.parse(inspect.getsource(
        _ev.node_llm_classifier_evaluation)))
    if isinstance(n, ast.Call))
_guard_lines = [ln for ln, nm in _node_calls
                if nm == "assert_stage5_reservation_bounded"]
_dispatch_lines = [ln for ln, nm in _node_calls
                   if nm.startswith("call_matching_model")]
check("9f-ii the node CALLS the guard before its first dispatch call (by AST)",
      (len(_guard_lines), bool(_dispatch_lines),
       bool(_guard_lines) and bool(_dispatch_lines)
       and min(_guard_lines) < min(_dispatch_lines)), (1, True, True))

# THE REQUEST IS UNCHANGED: the kwargs the provider receives are identical
# whether the reservation is the documented bound or a trivial stand-in.
_KW = []
_rec_client = _Client(chat=lambda kw: (_KW.append(kw),
                                       _chat_response((1000, 100)))[1])
p1b_case("r1_request_bound", _rec_client,
         lambda: _ev.call_matching_model("system prompt", "user prompt"))
_REAL_BOUND = config.stage5_attempt_bound
config.stage5_attempt_bound = lambda m, r, a: dict(
    _REAL_BOUND(m, r, a), usd=0.001, input_tokens=1, output_tokens=1)
try:
    p1b_case("r1_request_trivial", _Client(
        chat=lambda kw: (_KW.append(kw), _chat_response((1000, 100)))[1]),
        lambda: _ev.call_matching_model("system prompt", "user prompt"))
finally:
    config.stage5_attempt_bound = _REAL_BOUND
check("9g *** THE REQUEST IS UNCHANGED BY THE BOUND: identical kwargs with the "
      "documented bound and with a trivial one; the ceiling is the request's ***",
      (len(_KW), repr(at(_KW, 0)) == repr(at(_KW, 1)),
       at(at(_KW, 0), "max_completion_tokens"),
       config.stage5_attempt_bound is _REAL_BOUND),
      (2, True, _M, True))

# THE DURABLE ROW, AND A RESPONSE AT / A FAILURE UNDER THE BOUND.
_B_WIRE = _indep_bound(_WIRE, _M)
_c_resp = p1b_case("r1_response", _Client(
    chat=lambda kw: _chat_response((_B_WIRE[0], _B_WIRE[1]))),
    lambda: _ev.call_matching_model("system prompt", "user prompt"))
check("9h *** THE ROW IS RESERVED AT THE BOUND: amount, input and output tokens; "
      "a response at the documented maximum usage settles at its priced amount, "
      "which is at or below the reservation; the resume reader proves it ***",
      (near(at(at(_c_resp["rows"], 0), 4), _B_WIRE[2]),
       at(at(_c_resp["rows"], 0), 7), at(at(_c_resp["rows"], 0), 3),
       _both_numbers(at(at(_c_resp["rows"], 0), 5),
                     at(at(_c_resp["rows"], 0), 4))
       and at(at(_c_resp["rows"], 0), 5) <= at(at(_c_resp["rows"], 0), 4),
       drive(_dl.stage5_unproven_liabilities, _c_resp["camp"],
             db_path=_c_resp["db"]),
       parity_holds(_c_resp)),
      (True, 1_050_000, "response", True, [], (True, True, True, True)))
_c_pb = p1b_case("r1_possibly", _Client(
    chat=lambda kw: _throw(RuntimeError("possibly billed"))),
    lambda: _ev.call_matching_model("system prompt", "user prompt"))
check("9h-i a possibly-billed failure is charged THE BOUND live and durably, and "
      "the resume reader proves the settled-at-reservation row",
      (at(at(_c_pb["rows"], 0), 3), near(at(at(_c_pb["rows"], 0), 5), _B_WIRE[2]),
       near(_c_pb["live"].get("measured"), _B_WIRE[2]),
       drive(_dl.stage5_unproven_liabilities, _c_pb["camp"], db_path=_c_pb["db"])),
      ("possibly_billed", True, True, []))
_c_over = p1b_case("r1_over", _Client(
    chat=lambda kw: _chat_response((10**7, 100))),
    lambda: _ev.call_matching_model("system prompt", "user prompt"))
check("9h-ii a response BEYOND the documented window is priced above the bound "
      "and NAMED as a broken bound (counted), still charged at its priced "
      "amount, and settled durably at it",
      (_fault(_c_over, "bound_exceeded:stage5"),
       near(_c_over["live"].get("measured"),
            _spend.price_usage(_WIRE, 10**7, 100)[0]),
       at(at(_c_over["rows"], 0), 3), parity_holds(_c_over)),
      (1, True, "response", (True, True, True, True)))

# THE RESUME READER, OVER FABRICATED ROWS.
_DB9 = new_db("r1_reader.db")
_RUN9 = _dl.start_run_record("batch", db_path=_DB9, fingerprint=FIXED_FP)
_GOOD_NOTE = config.stage5_reservation_note(
    config.stage5_attempt_bound(_WIRE, _M, 1))


def _fab(aid, *, source="stage5", state="reserved", outcome=None, usd=None,
         note=_GOOD_NOTE, model=_WIRE, in_tok=1_050_000, out_tok=_M, camp="c9"):
    usd = _B_WIRE[2] if usd is None else usd
    _dl.reserve_billing_attempt(_DB9, attempt_id=aid, campaign_id=camp,
                                run_id=_RUN9, source=source, model=model,
                                input_tokens=in_tok, output_tokens=out_tok,
                                reserved_usd=usd, note=note)
    if state == "settled":
        _dl.settle_billing_attempt(_DB9, aid, outcome=outcome, settled_usd=usd)


_fab("ok-reserved")
_fab("ok-possibly", state="settled", outcome="possibly_billed")
_fab("old-reserved", usd=0.38484, note=None, in_tok=13304)
_fab("old-possibly", state="settled", outcome="possibly_billed", usd=0.38484,
     note=None, in_tok=13304)
_fab("old-response", state="settled", outcome="response", usd=0.01, note=None,
     in_tok=13304)
_fab("old-notbilled", state="settled", outcome="not_billed", usd=0.0, note=None)
_fab("embedding-old", source="query_embedding", usd=0.0001, note=None,
     model=config.EMBEDDING_MODEL)
_fab("old-but-huge", usd=1000.0, note=None)
_fab("old-version", note=json.dumps(dict(json.loads(_GOOD_NOTE),
                                         basis_version=0)))
_fab("tampered-low", usd=_B_WIRE[2] - 0.01)
_fab("tokens-low", in_tok=13304)
_fab("model-undocumented", model="gpt-4o-2024-08-06")
_fab("note-garbage", note="{not json")
_unproven9 = drive(_dl.stage5_unproven_liabilities, "c9", db_path=_DB9)
check("9i *** THE RESUME READER: an estimate-era reservation or settled-at-"
      "reservation row is UNPROVEN whatever its amount (even $1,000); a proven "
      "row is not; a settled response, a not-billed zero and an embedding row are "
      "not examined; an older basis version, an amount or token count below the "
      "recomputed bound, an undocumented model and a garbage note are unproven "
      "***",
      sorted(u.attempt_id for u in _unproven9)
      if isinstance(_unproven9, list) else _unproven9,
      sorted(["old-reserved", "old-possibly", "old-but-huge", "old-version",
              "tampered-low", "tokens-low", "model-undocumented",
              "note-garbage"]))
check("9i-i the reader raises BillingRecordUnreadable on a database it cannot "
      "open, and names no campaign as empty",
      (type(raised(_dl.stage5_unproven_liabilities, "c9",
                   db_path=os.path.join(_TMP, "absent", "x.db"))).__name__,
       type(raised(_dl.stage5_unproven_liabilities, "")).__name__),
      ("BillingRecordUnreadable", "BillingRecordUnreadable"))
check("9i-ii the storage layer's restated vocabularies equal spend's",
      (_dl.BILLING_SOURCE_STAGE5 == _spend.SPEND_SOURCE_STAGE5,
       set(_dl.STAGE5_AT_RESERVATION_OUTCOMES)
       == set(_spend.BILLING_OUTCOMES_AT_RESERVATION)), (True, True))

# ── FRESH PROCESSES, THROUGH THE REAL main() ───────────────────────────────
_B_E2E = _indep_bound(_WIRE, _M)


def r1_e2e(name, mode, inject=None, prepare=None):
    db = os.path.join(_TMP, f"r1_e2e_{name}.db")
    cp = os.path.join(_TMP, f"cp_r1_{name}")
    os.makedirs(cp)
    p1, d1 = child(mode, db=db, cp=cp, corpus=_CORPUS, cap=_E2E_CAP,
                   inject=inject)
    if prepare is not None:
        prepare(db)
    p2, d2 = child("observe", db=db, cp=cp, corpus=_CORPUS, cap=_E2E_CAP)
    p3, d3 = child("observe", db=db, cp=cp, corpus=_CORPUS, cap=_E2E_CAP)
    rows = (ro_rows(db, "SELECT kind, state, outcome, reserved_usd, settled_usd, "
                        "note, source FROM billing_attempts ORDER BY rowid")
            if os.path.exists(db) else [])
    return {"p": (p1, p2, p3), "d": (d1, d2, d3),
            "out": tuple((p.stdout or "") + (p.stderr or "")
                         for p in (p1, p2, p3)), "rows": rows}


_k = r1_e2e("kill_after_answer", "s5max", inject="kill_after_answer")
_k1, _k2, _k3 = _k["d"]
_priced_max = _spend.price_usage(_WIRE, _B_E2E[0], _B_E2E[1])[0]
check("9j *** SIGKILL AFTER THE PROVIDER ANSWERED AT ITS DOCUMENTED MAXIMUM: "
      "process 1 died; the durable row is RESERVED at the bound; fresh process "
      "2's seed equals the bound and COVERS the answer's priced charge; process "
      "3 reads the same seed ***",
      (_k["p"][0].returncode, [(r[1], near(r[3], _B_E2E[2])) for r in _k["rows"]],
       _k["p"][1].returncode, near(at(at(_k2, "seed"), "usd"), _B_E2E[2]),
       _both_numbers(at(at(_k2, "seed"), "usd"), _priced_max)
       and at(at(_k2, "seed"), "usd") >= _priced_max,
       at(at(_k2, "seed"), "unresolved"),
       near(at(at(_k3, "seed"), "usd"), at(at(_k2, "seed"), "usd"))),
      (-signal.SIGKILL, [("reserved", True)], 0, True, True, 1, True))

_t = r1_e2e("total_fail", "s5max", inject="total_fail")
_t1, _t2, _t3 = _t["d"]
check("9k *** EVERY DURABLE WRITE AFTER THE ANSWER FAILS (settlement, "
      "discrepancy row, marker) at the documented maximum usage: process 1's "
      "live ledger is the reservation; fresh process 2's seed EQUALS it and "
      "covers the priced charge; no latch was needed; process 3 agrees ***",
      (at(_t1, "calls"), near(at(_t1, "measured"), _B_E2E[2]),
       _both_numbers(at(_t1, "measured"), _priced_max)
       and at(_t1, "measured") >= _priced_max, at(_t1, "latch"),
       _t["p"][1].returncode,
       near(at(at(_t2, "seed"), "usd"), at(_t1, "measured")),
       near(at(at(_t3, "seed"), "usd"), at(at(_t2, "seed"), "usd")),
       [r[1] for r in _t["rows"]]),
      (1, True, True, [False, None], 0, True, True, ["reserved"]))

_o = r1_e2e("total_fail_over", "s5over", inject="total_fail")
_o1, _o2, _o3 = _o["d"]
check("9l *** THE P1c $20 CASE (10,000,000 prompt tokens: TEN TIMES the "
      "documented window) with every durable write failing: process 1 NAMES the "
      "broken bound and latches; the fresh seed is the reservation and is below "
      "the live charge -- the one cell outside R1's documented assumption, "
      "pinned rather than hidden ***",
      (_faults_of(_o1).get("bound_exceeded:stage5"), at(_o1, "latch"),
       near(at(_o1, "measured"), _spend.price_usage(_WIRE, 10**7, 100)[0]),
       near(at(at(_o2, "seed"), "usd"), _B_E2E[2]),
       _both_numbers(at(at(_o2, "seed"), "usd"), at(_o1, "measured"))
       and at(at(_o2, "seed"), "usd") < at(_o1, "measured")),
      (1, [True, _spend.SPEND_LIMIT_BILLING_RECORD], True, True, True))


def _plant_old(state, outcome=None):
    def prepare(db):
        run = ro_rows(db, "SELECT MAX(id) FROM runs")[0][0]
        camp = ro_rows(db, "SELECT campaign_id FROM billing_attempts LIMIT 1")[0][0]
        _dl.reserve_billing_attempt(db, attempt_id=f"old-{state}",
                                    campaign_id=camp, run_id=run,
                                    source="stage5", model=_WIRE,
                                    input_tokens=13304, output_tokens=_M,
                                    reserved_usd=0.38484)
        if state == "settled":
            _dl.settle_billing_attempt(db, f"old-{state}", outcome=outcome,
                                       settled_usd=0.38484)
    return prepare


_REFUSE_UNBOUNDED = ("REFUSING TO START PAID WORK: "
                     + _runner.CAMPAIGN_REFUSAL_RESERVATION_UNBOUNDED)
_old_r = r1_e2e("old_reserved", "s5max", prepare=_plant_old("reserved"))
_old_p = r1_e2e("old_possibly", "s5max",
                prepare=_plant_old("settled", "possibly_billed"))
check("9m *** AN OLD UNDERESTIMATED RESERVATION, IN A FRESH PROCESS: process 2 "
      "REFUSES paid work by name, exit 1, ZERO provider calls, naming the row "
      "and its amount; process 3 refuses again. Same for a settled-at-"
      "reservation possibly_billed row ***",
      [(e["p"][0].returncode, e["p"][1].returncode,
        _REFUSE_UNBOUNDED in e["out"][1], at(e["d"][1], "calls"),
        "old-" in e["out"][1] and "$0.384840" in e["out"][1],
        e["p"][2].returncode, _REFUSE_UNBOUNDED in e["out"][2])
       for e in (_old_r, _old_p)],
      [(0, 1, True, 0, True, 1, True)] * 2)
check("9m-i the refusal is in the closed vocabulary and its remedy names the "
      "provider's bill and --fresh",
      (_runner.CAMPAIGN_REFUSAL_RESERVATION_UNBOUNDED
       in _runner.CAMPAIGN_REFUSAL_REASONS,
       "provider's bill" in _old_r["out"][1], "--fresh" in _old_r["out"][1]),
      (True, True, True))

_clean = r1_e2e("clean", "s5max")
_c1, _c2, _c3 = _clean["d"]
check("9n *** CLEAN CONTROL: an ordinary run dispatches (one call, one settled "
      "response) and resumes normally -- process 2 continues with a seed EQUAL "
      "to process 1's live ledger, and process 3 agrees ***",
      (_clean["p"][0].returncode, at(_c1, "calls"),
       [(r[1], r[2]) for r in _clean["rows"]], _clean["p"][1].returncode,
       near(at(at(_c2, "seed"), "usd"), at(_c1, "measured")),
       _clean["p"][2].returncode,
       near(at(at(_c3, "seed"), "usd"), at(at(_c2, "seed"), "usd"))),
      (0, 1, [("settled", "response")], 0, True, 0, True))

# ADVERSARIAL PRICING AND THE WARMUP, IN PROCESS.
_PRICIEST = max(config.PRICING_CONFIG["models"],
                key=lambda m: config.PRICING_CONFIG["models"][m].get("input", 0))
_wire_at_max = _spend.price_usage(_WIRE, _B_WIRE[0], _B_WIRE[1])[0]
_echo_price = _spend.price_usage(_PRICIEST, _B_WIRE[0], _B_WIRE[1])[0]
_c_echo9 = p1b_case("r1_echo_pricier", _Client(
    chat=lambda kw: _chat_response((_B_WIRE[0], _B_WIRE[1]), model=_PRICIEST)),
    lambda: _ev.call_matching_model("system prompt", "user prompt"))
check("9o *** AN ECHO NAMING THE PRICIEST PRICED MODEL at the wire's documented "
      "maximum usage: priced above what the wire model charges, still AT OR "
      "BELOW the reservation (input is bounded at the dearest class x the "
      "long-context multiplier), not counted as a broken bound, parity holds ***",
      (_PRICIEST != _WIRE,
       _both_numbers(_echo_price, _wire_at_max) and _echo_price > _wire_at_max,
       _both_numbers(_echo_price, _B_WIRE[2]) and _echo_price <= _B_WIRE[2],
       _fault(_c_echo9, "bound_exceeded:stage5"), parity_holds(_c_echo9)),
      (True, True, True, None, (True, True, True, True)))


def _truncated_response(tokens):
    resp = _chat_response(tokens)
    resp.choices[0].finish_reason = "length"
    return resp


_c_trunc = p1b_case("r1_truncated", _Client(
    chat=lambda kw: _truncated_response((_B_WIRE[0], _B_WIRE[1]))),
    lambda: _ev.call_matching_model("system prompt", "user prompt"))
_row_t = at(_c_trunc["rows"], 0)
check("9o-i a TRUNCATED response (finish_reason 'length') at the output ceiling "
      "and the documented window settles as a response at its priced amount, at "
      "or below the reservation; the resume reader proves the row; parity holds",
      (at(_row_t, 3),
       _both_numbers(at(_row_t, 5), at(_row_t, 4))
       and at(_row_t, 5) <= at(_row_t, 4),
       drive(_dl.stage5_unproven_liabilities, _c_trunc["camp"],
             db_path=_c_trunc["db"]), parity_holds(_c_trunc)),
      ("response", True, [], (True, True, True, True)))

_B_WARM = _indep_bound(_WIRE, config.MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS)
_c_warm = p1b_case("r1_warmup_possibly", _Client(
    chat=lambda kw: _throw(RuntimeError("warmup lost"))),
    lambda: _ev.call_matching_model_warmup("system prompt"))
_row_w = at(_c_warm["rows"], 0)
check("9p *** THE WARMUP, through the real call_matching_model_warmup, reserves "
      "ITS OWN documented bound (the full window at the dearest class plus its "
      "one-token ceiling: $5.250018 on this wire), and a possibly-billed warmup is "
      "charged exactly that, live and durably ***",
      (config.MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS,
       near(_B_WARM[2], 5.250018), near(at(_row_w, 4), _B_WARM[2]),
       at(_row_w, 7), at(_row_w, 3),
       near(_c_warm["live"].get("measured"), _B_WARM[2])),
      (1, True, True, 1_050_000, "possibly_billed", True))


# ---------------------------------------------------------------------------
# 9q -- R1b: THE SHIPPED ARM'S TERMS AGAINST AWS's OWN DOCUMENTATION.
#
# The rates are TYPED from AWS's Amazon Bedrock pricing page as rendered from
# its own data endpoint (b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/
# bedrockfoundationmodels/USD/current/bedrockfoundationmodels.json, publication
# 2026-09-11, retrieved 2026-09-14), never read out of PRICING_CONFIG: a rate
# edited without new evidence fails here. Per 1M tokens, (input, output, cache
# read, 5m cache write, 1h cache write):
#   "Global Cross-region Inference"            3.00 15.00 0.30 3.75  6.00
#   "Geo and In-region Cross-region Inference" 3.30 16.50 0.33 4.125 6.60
# The long-context multipliers are NOT on any AWS page; they are pinned here
# as the assumption they are, and labelled so in the owner.
_PUBLISHED_GLOBAL = (3.00, 15.00, 0.30, 3.75, 6.00)
_PUBLISHED_GEO = (3.30, 16.50, 0.33, 4.125, 6.60)
_PUBLISHED_SONNET = {
    "global.anthropic.claude-sonnet-4-6": _PUBLISHED_GLOBAL,
    "us.anthropic.claude-sonnet-4-6": _PUBLISHED_GEO,
    "eu.anthropic.claude-sonnet-4-6": _PUBLISHED_GEO,
    "au.anthropic.claude-sonnet-4-6": _PUBLISHED_GEO,
    "jp.anthropic.claude-sonnet-4-6": _PUBLISHED_GEO,
    "anthropic.claude-sonnet-4-6": _PUBLISHED_GEO,
}


def _row_rates(model):
    row = at(config.PRICING_CONFIG["models"], model, {})
    cw = at(row, "cache_write", {})
    return (at(row, "input"), at(row, "output"), at(row, "cache_read"),
            at(cw, "5m"), at(cw, "1h"))


def _rate_of(model, key):
    b = drive(config.stage5_attempt_bound, model, _M, 1)
    return b.get(key) if isinstance(b, dict) else b


check("9q *** EVERY SONNET 4.6 ROW CARRIES THE RATES AWS PUBLISHES (pricing "
      "page, retrieved 2026-09-14): Global 3.00/15.00/0.30/3.75/6.00, Geo and "
      "In-region 3.30/16.50/0.33/4.125/6.60 ***",
      {m: _row_rates(m) for m in _PUBLISHED_SONNET}, _PUBLISHED_SONNET)
check("9q-0 non-degeneracy: all six Sonnet 4.6 wire ids are checked, and every "
      "one of them is a documented Stage 5 wire model",
      (len(_PUBLISHED_SONNET),
       sorted(m for m in _PUBLISHED_SONNET
              if m not in config.STAGE5_ATTEMPT_LIMITS)),
      (6, []))
check("9q-i *** NO GEO FACTOR ON TOP OF A GEO ROW: the shipped bound prices input "
      "at the published 5m write rate x the assumed 2.0 and output at the "
      "published rate x 1.5, and the geo bound's rates are exactly 1.1x the "
      "global ones (published, not multiplied again) ***",
      (_rate_of(_SONNET, "input_rate_per_mtok"),
       _rate_of(_SONNET, "output_rate_per_mtok"),
       _rate_of("global.anthropic.claude-sonnet-4-6", "input_rate_per_mtok"),
       _rate_of("global.anthropic.claude-sonnet-4-6", "output_rate_per_mtok"),
       _both_numbers(_rate_of(_SONNET, "input_rate_per_mtok"),
                     _rate_of("global.anthropic.claude-sonnet-4-6",
                              "input_rate_per_mtok"))
       and round(_rate_of(_SONNET, "input_rate_per_mtok")
                 / _rate_of("global.anthropic.claude-sonnet-4-6",
                            "input_rate_per_mtok"), 9),
       _both_numbers(_rate_of(_SONNET, "output_rate_per_mtok"),
                     _rate_of("global.anthropic.claude-sonnet-4-6",
                              "output_rate_per_mtok"))
       and round(_rate_of(_SONNET, "output_rate_per_mtok")
                 / _rate_of("global.anthropic.claude-sonnet-4-6",
                            "output_rate_per_mtok"), 9)),
      (4.125 * 2.0, 16.50 * 1.5, 3.75 * 2.0, 15.00 * 1.5, 1.1, 1.1))
check("9q-ii *** THE ASSUMED MULTIPLIERS ARE LABELLED ASSUMED: every Sonnet 4.6 "
      "wire model's bound basis says ASSUMED, and no GPT-5.6 Terra basis does ***",
      {m: ("ASSUMED" in str(_rate_of(m, "basis")))
       for m in config.STAGE5_ATTEMPT_LIMITS},
      {m: ("claude-sonnet-4-6" in m) for m in config.STAGE5_ATTEMPT_LIMITS})
check("9q-iii the assumed Sonnet 4.6 multipliers are exactly 2.0 input / 1.5 "
      "output on every row: missing documentation is not treated as a zero "
      "premium, and a change needs new evidence",
      {m: (at(at(config.STAGE5_ATTEMPT_LIMITS, m),
              "long_context_input_multiplier"),
           at(at(config.STAGE5_ATTEMPT_LIMITS, m),
              "long_context_output_multiplier"))
       for m in _PUBLISHED_SONNET},
      {m: (2.0, 1.5) for m in _PUBLISHED_SONNET})


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
