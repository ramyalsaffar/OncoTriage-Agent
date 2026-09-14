# Campaign Billing Record Test
##############################

"""A resumed campaign's budget includes every charge an earlier process made.

WHAT SHIPPED, AND WHY NOTHING FAILED
------------------------------------
``database_logger.campaign_spend_before`` seeded a resumed batch campaign's
budget by summing ``inferences.estimated_cost_usd`` over the ``runs`` chain.
Since the attempt-provenance pass that column describes a patient's FINAL Stage
5 attempt only, so a restarted campaign's budget omitted every earlier billed
attempt, every billed attempt of a patient whose row was never written, and
every Stage 2 dense embedding -- and, separately, a first process whose patients
all FAILED left no checkpoint, so the restart was a fresh campaign with a fresh
budget. The in-process ledger was exact and died with the process.

WHAT THIS FILE HOLDS
--------------------
    1. The schema (era 17), the vocabularies and their pins.
    2. The record's own arithmetic: reserve, settle, duplicate settlement,
       conflict, missing, every outcome's amount, an unsummable row.
    3. The retry policy's attempt hook, driven through ``call_matching_model``:
       response, not-billed, possibly-billed, abandoned -- each against the
       in-process ledger -- and a reservation that cannot be persisted, which
       must refuse the dispatch.
    4. A persistence failure through the REAL Stage 5 node: nothing sent, the
       run latched, the stop reason storable.
    5. P2 -- the Converse arm's cache-unconfirmed path through the REAL graph and
       the REAL writer: final-attempt fields from the final attempt, the
       cumulative record holding every billed attempt.
    6. CROSS-PROCESS PROOFS through the REAL ``main()`` in child processes:
       a billed attempt then a zero-usage failure then a FRESH process resuming;
       a process SIGKILLed between reservation and settlement; one SIGKILLed
       mid-settlement; two concurrent campaigns sharing one database; a covered
       historical campaign; uncoverable ones refused by name.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO MODEL LOAD, NO CORPUS, NO GIT
HISTORY. Every provider client is a stand-in installed through
``oncotriage/agent/deps.py``; every child is handed a closed Qdrant port and the
same stand-ins; every database and checkpoint directory is inside a
``tempfile.mkdtemp`` removed at the end and asserted gone. The production
``inferences.db`` digest is compared at the end. It EXECS NOTHING and loads no
module by location: children are SCRIPTS written into the temp tree and run with
``sys.executable``. NOT IN THE COLLISION MATRIX.

Run from terminal:
    python tests/test_campaign_billing_record.py
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
import hashlib
import json
import re
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import threading
import types
from pathlib import Path

from langgraph.graph import END, StateGraph

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
from oncotriage.agent import graph as _graph                     # noqa: E402
from oncotriage.agent import models as _models                   # noqa: E402
from oncotriage.agent import terminal as _terminal               # noqa: E402
from oncotriage.agent.patient import compute_patient_hash        # noqa: E402
from oncotriage.agent.state import TrialMatchState               # noqa: E402
from oncotriage.batch import runner as _runner                   # noqa: E402
from oncotriage.storage import database_logger as _dl            # noqa: E402

import _provider_pin                                             # noqa: E402

_PROVIDER_BEFORE_PIN = _provider_pin.pin_openai_arm(os.path.basename(__file__))


# ===========================================================================
# HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label, actual, expected):
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(label)
        print(f"  FAIL  {label}\n          expected: {expected!r}"
              f"\n          actual:   {actual!r}")


def section(title):
    print("\n" + "=" * 78 + "\n" + title + "\n" + "=" * 78)


class _Absent:
    def __init__(self, why):
        self._why = why

    def __eq__(self, other):
        return isinstance(other, _Absent) and other._why == self._why

    def __hash__(self):
        return hash(("_Absent", self._why))

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __repr__(self):
        return f"<absent: {self._why}>"


def at(container, key, why=None):
    try:
        return container[key]
    except Exception as exc:                                  # noqa: BLE001
        return _Absent(why or f"{type(exc).__name__}: {exc}")


def drive(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                              # noqa: BLE001
        return _Absent(f"raised {type(exc).__name__}: {exc}")


def raised(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
        return None
    except BaseException as exc:                              # noqa: BLE001
        return exc


def digest(path):
    if not os.path.exists(path):
        return "absent"
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def near(a, b, eps=1e-9):
    return (isinstance(a, (int, float)) and isinstance(b, (int, float))
            and abs(a - b) <= eps)


_REPO = os.path.dirname(os.path.dirname(os.path.abspath(oncotriage.__file__)))
_TESTS = os.path.dirname(os.path.abspath(__file__))
_TMP = tempfile.mkdtemp(prefix="oncotriage-billing-record-")
_PROD_DB = _paths.inferences_path
_PROD_DIGEST_BEFORE = digest(_PROD_DB)
_WIRE = config.matching_wire_model()
_JITTER_START = _pr.full_jitter_delay
_POLICY_START = _spend.policy()
_CONFIG_KEYS = ("MATCHING_PER_TRIAL_CALLS_ENABLED",
                "MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS", "SPEND_CAP_USD",
                "SPEND_CAP_ENFORCED", "MATCHING_PROVIDER")
_CONFIG_START = {k: getattr(config, k) for k in _CONFIG_KEYS}
_RESOLVED_START = dict(_paths._RESOLVED)

FIXED_FP = {"fingerprint_version": _rf.FINGERPRINT_VERSION}
for _field in _rf.FINGERPRINT_FIELDS:
    FIXED_FP[_field] = f"fixed-{_field}"
FIXED_FP.update(collection_points=12067, campaign_cohort_size=500,
                campaign_cohort_seed=42, matching_per_trial_empty_retries=1,
                matching_per_trial_parallel_bound=4)


def new_db(name):
    db = os.path.join(_TMP, name)
    _dl.initialize_database(db)
    return db


def billing_rows(db, where="1=1", params=()):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM billing_attempts WHERE {where} ORDER BY rowid",
            params)]
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


@contextlib.contextmanager
def sink_installed(db, campaign_id, run_id):
    _spend.SPEND_LEDGER.reset()
    _spend.SPEND_STOP.reset()
    _spend.BILLING_RECORD_FAULTS.clear()
    _spend.BILLING_RECORD.install(_dl.BillingRecordSink(db, campaign_id, run_id))
    try:
        yield
    finally:
        _spend.BILLING_RECORD.clear()
        _spend.SPEND_LEDGER.reset()
        _spend.SPEND_STOP.reset()


class _Usage:
    def __init__(self, prompt, completion):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.completion_tokens_details = None


def _openai_response(content, tokens, finish="stop"):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=content, refusal=None),
            finish_reason=finish)],
        usage=_Usage(*tokens), model=_WIRE)


class _CreateStub:
    """``client.chat.completions.create`` doing whatever ``behaviour`` says."""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = 0
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls += 1
        return self.behaviour(kwargs)


#------------------------------------------------------------------------------

section("SECTION 1 -- the schema, the vocabularies and their pins")

_DB1 = new_db("schema.db")
_conn = sqlite3.connect(_DB1)
try:
    _tables = {r[0] for r in _conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    _indexes = {r[0] for r in _conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='billing_attempts'")}
    _uv = _conn.execute("PRAGMA user_version").fetchone()[0]
finally:
    _conn.close()
check("1a a fresh database carries billing_attempts", "billing_attempts" in _tables,
      True)
check("1b ...with its campaign and run indexes",
      {"idx_billing_attempts_campaign_id", "idx_billing_attempts_run_id"}
      <= _indexes, True)
# ERA 18 AT THE BILLING CLOSURE PASS (runs.billing_campaign_id and
# run_counter_registry). The literal moves with the era on purpose: a stamp this
# file does not pin is one a schema change can move without anybody reading why.
check("1c ...and is stamped era 18", (_uv, _dl.SCHEMA_USER_VERSION), (18, 18))

# AN ERA-16 DATABASE MIGRATES: the table is dropped and the stamp lowered, then
# initialize_database runs again.
_DB16 = os.path.join(_TMP, "era16.db")
_dl.initialize_database(_DB16)
_conn = sqlite3.connect(_DB16)
_conn.execute("DROP TABLE billing_attempts")
_conn.execute("PRAGMA user_version = 16")
_conn.commit()
_conn.close()
_dl._INITIALIZED_DATABASES.discard(os.path.abspath(_DB16))
_dl.initialize_database(_DB16)
_conn = sqlite3.connect(_DB16)
check("1d an era-16 database gains the table on its next initialization",
      _conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE "
                    "name='billing_attempts'").fetchone()[0], 1)
_conn.close()

check("1e the storage outcome vocabulary is spend's plus historical_evidence, "
      "in order -- restated, pinned equal",
      _dl.BILLING_ATTEMPT_OUTCOMES,
      _spend.BILLING_OUTCOMES + ("historical_evidence",))
check("1f every historical unrecorded-billing counter is a REGISTERED "
      "degradation counter, so a rename cannot silently pass every campaign",
      sorted(set(_dl.HISTORICAL_UNRECORDED_BILLING_COUNTERS)
             - set(_degradation.registered_names())), [])
check("1f-i non-degeneracy: the list is not empty",
      len(_dl.HISTORICAL_UNRECORDED_BILLING_COUNTERS) >= 6, True)
check("1g the stop reason and the spend limit are the SAME string",
      _dl.RUN_STOP_REASON_BILLING_RECORD, _spend.SPEND_LIMIT_BILLING_RECORD)
check("1h BILLING_RECORD_FAULTS reaches the run-end report",
      "BILLING_RECORD_FAULTS" in _degradation.registered_names(), True)
check("1i the resume's spend reader is DELETED: nothing named "
      "campaign_spend_before survives in the storage layer",
      hasattr(_dl, "campaign_spend_before") or hasattr(_dl, "CampaignSpend"),
      False)
_runner_src = Path(_runner.__file__).read_text(encoding="utf-8")
_runner_tree = ast.parse(_runner_src)
_est_names = {n.attr if isinstance(n, ast.Attribute) else n.id
              for n in ast.walk(_runner_tree)
              if isinstance(n, (ast.Name, ast.Attribute))}
check("1j the runner reads no estimated_cost_usd and no campaign_spend_before "
      "anywhere as code",
      ("campaign_spend_before" in _est_names,
       "estimated_cost_usd" in [c.value for c in ast.walk(_runner_tree)
                                if isinstance(c, ast.Constant)
                                and isinstance(c.value, str)
                                and c.value == "estimated_cost_usd"]),
      (False, False))
_main = next((n for n in _runner_tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
_stop_assign = [n for n in ast.walk(_main) if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_stop_reason"
                        for t in n.targets)] if _main else []
check("1k main()'s one _stop_reason derivation names the billing-record reason",
      bool(_stop_assign) and "RUN_STOP_REASON_BILLING_RECORD" in
      ast.unparse(_stop_assign[0]), True)


#------------------------------------------------------------------------------

section("SECTION 2 -- reserve, settle, duplicate, conflict, every outcome")

_DB2 = new_db("record.db")
_run2 = _dl.start_run_record("batch", db_path=_DB2, fingerprint=FIXED_FP)
_CAMP = "campaign-two"


def _reserve(aid, usd, campaign=_CAMP, run=_run2):
    return _dl.reserve_billing_attempt(
        _DB2, attempt_id=aid, campaign_id=campaign, run_id=run,
        source="stage5", model=_WIRE, input_tokens=100, output_tokens=10,
        reserved_usd=usd)


check("2a a reservation commits and reads back",
      drive(_reserve, "a1", 0.50), "a1")
_t = _dl.campaign_billing_total(_CAMP, db_path=_DB2)
check("2b an UNSETTLED reservation is charged at its reservation, and counted "
      "unresolved", (_t.usd, _t.attempts, _t.unresolved, _t.unresolved_usd),
      (0.50, 1, 1, 0.50))
check("2c a settlement lands", _dl.settle_billing_attempt(
    _DB2, "a1", outcome="response", settled_usd=0.12, input_tokens=90,
    output_tokens=9), "settled")
check("2d ...and the total now carries the SETTLED amount, nothing unresolved",
      (_dl.campaign_billing_total(_CAMP, db_path=_DB2).usd,
       _dl.campaign_billing_total(_CAMP, db_path=_DB2).unresolved), (0.12, 0))
check("2e *** a repeated settlement is a DUPLICATE and moves nothing ***",
      (_dl.settle_billing_attempt(_DB2, "a1", outcome="response",
                                  settled_usd=0.12),
       _dl.campaign_billing_total(_CAMP, db_path=_DB2).usd),
      ("duplicate", 0.12))
check("2f *** a DIFFERENT settlement of a settled row is a CONFLICT and the "
      "first stands ***",
      (_dl.settle_billing_attempt(_DB2, "a1", outcome="possibly_billed",
                                  settled_usd=0.50),
       _dl.campaign_billing_total(_CAMP, db_path=_DB2).usd),
      ("conflict", 0.12))
check("2g a settlement of an attempt nobody reserved is MISSING",
      _dl.settle_billing_attempt(_DB2, "nope", outcome="response",
                                 settled_usd=0.01), "missing")
check("2h a repeated reservation of the same attempt is idempotent",
      (drive(_reserve, "a2", 0.30), drive(_reserve, "a2", 0.30),
       len(billing_rows(_DB2, "attempt_id='a2'"))), ("a2", "a2", 1))
check("2i ...and a colliding id describing a DIFFERENT charge raises rather "
      "than being mistaken for it",
      type(raised(_reserve, "a2", 0.31)).__name__, "BillingRecordWriteError")
check("2j an invalid reservation is refused before any write",
      type(raised(_reserve, "a3", -1.0)).__name__, "BillingRecordWriteError")

# Every outcome's amount, through the slot, which owns the arithmetic.
_DB2S = new_db("slot.db")
_run2s = _dl.start_run_record("batch", db_path=_DB2S, fingerprint=FIXED_FP)
_amounts = {}
with sink_installed(_DB2S, "slot-camp", _run2s):
    for _outcome in _spend.BILLING_OUTCOMES:
        _h = _spend.BILLING_RECORD.reserve("stage5", _WIRE, 1000, 500,
                                           where="test")
        _res = _spend.BILLING_RECORD.settle(
            _h, _outcome, model=_WIRE, prompt_tokens=200, completion_tokens=20)
        _row = billing_rows(_DB2S, "attempt_id=?", (_h.attempt_id,))[0]
        _amounts[_outcome] = (_res, _row["outcome"], _row["settled_usd"],
                              _row["reserved_usd"])
    _h_none = None
    _unpriced = _spend.BILLING_RECORD.reserve("stage5", _WIRE, 1000, 500,
                                              where="test")
    _spend.BILLING_RECORD.settle(_unpriced, _spend.BILLING_OUTCOME_RESPONSE,
                                 model=_WIRE, prompt_tokens="?",
                                 completion_tokens=None)
    _unpriced_row = billing_rows(_DB2S, "attempt_id=?",
                                 (_unpriced.attempt_id,))[0]
_RES_USD = _spend.price_usage(_WIRE, 1000, 500)[0]
_OBS_USD = _spend.price_usage(_WIRE, 200, 20)[0]
check("2k the reservation is the request's full upper bound, priced at the "
      "wire model", near(_amounts["response"][3], _RES_USD), True)
check("2l a response settles at its PRICED usage",
      (_amounts["response"][1], near(_amounts["response"][2], _OBS_USD)),
      ("response", True))
check("2m not_billed settles at ZERO",
      _amounts["not_billed"][1:3], ("not_billed", 0.0))
check("2n possibly_billed and abandoned settle AT THE RESERVATION",
      (near(_amounts["possibly_billed"][2], _RES_USD),
       near(_amounts["abandoned"][2], _RES_USD)), (True, True))
check("2o a response whose usage cannot be priced becomes response_unpriced at "
      "the reservation",
      (_unpriced_row["outcome"], near(_unpriced_row["settled_usd"], _RES_USD)),
      ("response_unpriced", True))
check("2o-i non-degeneracy: observed and reserved prices differ",
      _RES_USD > _OBS_USD > 0, True)
check("2p with NO sink installed, reserve returns None and settle is a no-op",
      (_spend.BILLING_RECORD.reserve("stage5", _WIRE, 1, 1, where="t"),
       _spend.BILLING_RECORD.settle(None, "response")), (None, None))

# An unsummable row makes the total RAISE rather than read as free.
_conn = sqlite3.connect(_DB2)
_conn.execute("UPDATE billing_attempts SET settled_usd = 'free' "
              "WHERE attempt_id = 'a1'")
_conn.commit()
_conn.close()
check("2q *** a row whose amount cannot be summed makes the total RAISE, "
      "never read as zero ***",
      type(raised(_dl.campaign_billing_total, _CAMP, db_path=_DB2)).__name__,
      "BillingRecordUnreadable")
check("2r an unreadable database raises too",
      type(raised(_dl.campaign_billing_total, _CAMP,
                  db_path=os.path.join(_TMP, "missing", "x.db"))).__name__,
      "BillingRecordUnreadable")


#------------------------------------------------------------------------------

section("SECTION 3 -- the retry policy's attempt hook, beside the ledger")

_DB3 = new_db("hook.db")
_run3 = _dl.start_run_record("batch", db_path=_DB3, fingerprint=FIXED_FP)
_pr.full_jitter_delay = lambda retry_number, rng=None: 0.0


def hook_drive(behaviour, classify=None):
    stub = _CreateStub(behaviour)
    saved_classify = _ev._classify_matching_failure
    deps.set_override(deps.OPENAI_CLIENT, stub)
    if classify is not None:
        _ev._classify_matching_failure = classify
    try:
        with sink_installed(_DB3, "hook-camp", _run3):
            before = {r["attempt_id"] for r in billing_rows(_DB3)}
            outcome = raised(_ev.call_matching_model, "system prompt",
                             "user prompt")
            measured = _spend.SPEND_LEDGER.measured
            new = [r for r in billing_rows(_DB3)
                   if r["attempt_id"] not in before]
    finally:
        _ev._classify_matching_failure = saved_classify
        deps.clear_override(deps.OPENAI_CLIENT)
    return {"exc": outcome, "rows": new, "calls": stub.calls,
            "measured": measured}


_RESERVE_EXPECTED = _spend.price_usage(
    _WIRE, _ev._reservation_input_tokens("system prompt", "user prompt"),
    config.MATCHING_MAX_TOKENS)[0]

_ok = hook_drive(lambda kw: _openai_response('{"evaluations": []}', (321, 45)))
check("3a a response: one row, settled 'response' at its priced usage",
      ([r["outcome"] for r in _ok["rows"]],
       near(at(at(_ok["rows"], 0), "settled_usd"),
            _spend.price_usage(_WIRE, 321, 45)[0])), (["response"], True))
check("3a-i ...reserved at the upper bound the ledger would charge",
      near(at(at(_ok["rows"], 0), "reserved_usd"), _RESERVE_EXPECTED), True)

_nb = hook_drive(lambda kw: (_ for _ in ()).throw(RuntimeError("refused")),
                 classify=lambda exc: _pr.verdict_for(_pr.CATEGORY_CLIENT))
check("3b a not-billed failure settles at ZERO, and the ledger charged nothing",
      ([r["outcome"] for r in _nb["rows"]],
       at(at(_nb["rows"], 0), "settled_usd"), _nb["measured"]),
      (["not_billed"], 0.0, 0.0))

_pb = hook_drive(lambda kw: (_ for _ in ()).throw(RuntimeError("lost")),
                 classify=lambda exc: _pr.verdict_for(_pr.CATEGORY_UNCLASSIFIED))
check("3c a possibly-billed failure settles AT THE RESERVATION -- the SAME "
      "number the ledger charged",
      ([r["outcome"] for r in _pb["rows"]],
       near(at(at(_pb["rows"], 0), "settled_usd"), _pb["measured"]),
       near(_pb["measured"], _RESERVE_EXPECTED)),
      (["possibly_billed"], True, True))


def _interrupt(kw):
    raise KeyboardInterrupt("operator")


_ab = hook_drive(_interrupt)
check("3d a Ctrl-C on the wire is ABANDONED at the reservation, re-raised, and "
      "agrees with the ledger",
      (type(_ab["exc"]).__name__, [r["outcome"] for r in _ab["rows"]],
       near(at(at(_ab["rows"], 0), "settled_usd"), _ab["measured"])),
      ("KeyboardInterrupt", ["abandoned"], True))

# A RESERVATION THAT CANNOT BE PERSISTED REFUSES THE DISPATCH.
_DB3B = new_db("hook_broken.db")
_run3b = _dl.start_run_record("batch", db_path=_DB3B, fingerprint=FIXED_FP)
_stub3 = _CreateStub(lambda kw: _openai_response("{}", (1, 1)))
deps.set_override(deps.OPENAI_CLIENT, _stub3)
try:
    with sink_installed(_DB3B, "broken-camp", _run3b):
        _c = sqlite3.connect(_DB3B)
        _c.execute("DROP TABLE billing_attempts")
        _c.commit()
        _c.close()
        _exc3 = raised(_ev.call_matching_model, "system prompt", "user prompt")
        _latch3 = (_spend.SPEND_STOP.requested, _spend.SPEND_STOP.limit)
        _faults3 = dict(_spend.BILLING_RECORD_FAULTS)
        _exc3b = raised(_ev.call_matching_model, "system prompt", "user prompt")
        _faults3b = dict(_spend.BILLING_RECORD_FAULTS)
finally:
    deps.clear_override(deps.OPENAI_CLIENT)
check("3e *** a reservation that cannot be persisted REFUSES THE DISPATCH: the "
      "provider was never called ***", _stub3.calls, 0)
check("3f ...raised as Stage5SpendStopped carrying the billing-record limit",
      (type(_exc3).__name__, getattr(_exc3, "limit", None)),
      ("Stage5SpendStopped", "billing_record"))
check("3g ...marked PRE-SEND, because nothing left the machine",
      _pr.is_pre_send_refusal(_exc3), True)
check("3h ...the run LATCHED under the billing-record limit",
      _latch3, (True, "billing_record"))
check("3i ...and the failure is COUNTED",
      any(k.startswith("reserve:") for k in _faults3), True)
check("3j a later attempt refuses WITHOUT trying the write",
      (_stub3.calls, _faults3b.get("refused_latched", 0)), (0, 1))


def _embed_create(**kwargs):
    _EMBED_CALLS.append(kwargs)
    return types.SimpleNamespace(data=[types.SimpleNamespace(embedding=[0.0])],
                                 usage=types.SimpleNamespace(prompt_tokens=7),
                                 model=config.EMBEDDING_MODEL)


_EMBED_CALLS = []
_embed_client = types.SimpleNamespace(
    embeddings=types.SimpleNamespace(create=_embed_create))
_DB3E = new_db("embed.db")
_run3e = _dl.start_run_record("batch", db_path=_DB3E, fingerprint=FIXED_FP)
deps.set_override(deps.OPENAI_CLIENT, _embed_client)
try:
    with sink_installed(_DB3E, "embed-camp", _run3e):
        _v = drive(_models.get_embedding, "breast cancer trial")
        _erows = billing_rows(_DB3E)
        _emeasured = _spend.SPEND_LEDGER.measured
        _c = sqlite3.connect(_DB3E)
        _c.execute("DROP TABLE billing_attempts")
        _c.commit()
        _c.close()
        _ebroken = raised(_models.get_embedding, "breast cancer trial")
finally:
    deps.clear_override(deps.OPENAI_CLIENT)
check("3k Stage 2's dense embedding is RESERVED and SETTLED in the record, at "
      "the ledger's figure",
      ([r["source"] for r in _erows], [r["outcome"] for r in _erows],
       near(at(at(_erows, 0), "settled_usd"), _emeasured)),
      (["query_embedding"], ["response"], True))
check("3l ...and a persistence failure refuses the embedding call too: one "
      "call was made before the table went, none after",
      (type(_ebroken).__name__, len(_EMBED_CALLS)),
      ("BillingRecordUnavailable", 1))


#------------------------------------------------------------------------------

section("SECTION 4 -- a persistence failure through the REAL Stage 5 node")

PATIENT = {
    "patient_id": "billing-record-patient",
    "demographics": {"age": 61, "sex": "female", "race": "white",
                     "ethnicity": "not hispanic or latino"},
    "conditions": [{"code": "254837009",
                    "display": "Malignant neoplasm of breast (disorder)",
                    "verification_status": "confirmed"}],
    "medications": [], "allergies": [], "observations": [], "procedures": [],
}


def trial(index):
    return {"trial": {
        "nct_id": "NCT%08d" % index, "title": f"Trial {index}",
        "phase": "PHASE2",
        "eligibility": {"inclusion_criteria": "Inclusion Criteria:\n- " + "x" * 200,
                        "exclusion_criteria": "Exclusion Criteria:\n- " + "y" * 200},
    }}


TRIALS = [trial(i) for i in range(3)]


def build_graph(attempts):
    def _stage5(state):
        out = _ev.node_llm_classifier_evaluation(state)
        attempts.append(dict(out))
        return out

    g = StateGraph(TrialMatchState)
    g.add_node("llm_classifier_evaluation", _stage5)
    g.add_node("finalize", _terminal.node_finalize)
    g.add_node("error_handler", _terminal.node_error_handler)
    g.set_entry_point("llm_classifier_evaluation")
    g.add_conditional_edges(
        "llm_classifier_evaluation", _graph.route_after_llm_classifier,
        {"finalize": "finalize",
         "llm_classifier_retry": "llm_classifier_evaluation",
         "error_handler": "error_handler"})
    g.add_edge("finalize", END)
    g.add_edge("error_handler", END)
    return g.compile()


def _eligible_body(ids):
    return json.dumps({"evaluations": [
        {"assessment": "No known disqualifiers.", "eligible": "eligible",
         "inclusion_criteria": [{"criterion": "Age 18+",
                                 "patient_value": "61", "status": "met"}],
         "exclusion_criteria": [], "match_score": 0.0, "nct_id": i}
        for i in ids]})


def _per_trial_reply(kw):
    user = kw["messages"][1]["content"]
    if user == config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE:
        return _openai_response("", (1000, 1), finish="length")
    return _openai_response(
        _eligible_body(re.findall(r"<<<TRIAL_DATA nct_id=(\S+) ", user)),
        (1000, 100))


_DB4 = new_db("node_broken.db")
_run4 = _dl.start_run_record("batch", db_path=_DB4, fingerprint=FIXED_FP)
_stub4 = _CreateStub(_per_trial_reply)
_attempts4 = []
deps.set_override(deps.OPENAI_CLIENT, _stub4)
try:
    with settings(MATCHING_PER_TRIAL_CALLS_ENABLED=True,
                  MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS=3,
                  SPEND_CAP_USD=None):
        with sink_installed(_DB4, "node-broken", _run4):
            _c = sqlite3.connect(_DB4)
            _c.execute("DROP TABLE billing_attempts")
            _c.commit()
            _c.close()
            _state = _graph.build_initial_state(PATIENT)
            _state.update(filtered_trials=TRIALS, mesh_filter_applied=True,
                          mesh_filter_skip_reason="applied")
            _final4 = drive(build_graph(_attempts4).invoke, _state)
            _latch4 = _spend.SPEND_STOP.limit
            _skips4 = dict(_spend.SPEND_GATE_SKIPS)
finally:
    deps.clear_override(deps.OPENAI_CLIENT)
_spend.SPEND_GATE_SKIPS.clear()
check("4a *** through the real node: NO request reached the provider ***",
      _stub4.calls, 0)
check("4b ...the patient FAILED rather than completing with a hole",
      bool(at(at(_final4, "result"), "error")), True)
check("4c ...and names the billing record, not the cap",
      "billing record" in str(at(at(_final4, "result"), "error")), True)
check("4c-i ...and the floor's sentence is the billing record's, not the "
      "cap's -- which would send an operator to raise a budget",
      "a spend limit was reached" in str(at(at(_final4, "result"), "error")),
      False)
check("4d the run is latched under the billing-record limit", _latch4,
      "billing_record")
check("4e later attempts were declined at the GATE, not re-tried against the "
      "database", any(k.endswith("billing_record") for k in _skips4), True)
check("4f a run row accepts stop_reason 'billing_record'",
      (_dl.finalize_run_record(_run4, "STOPPED", db_path=_DB4,
                               stop_reason="billing_record"),
       sqlite3.connect(_DB4).execute(
           "SELECT stop_reason FROM runs WHERE id=?", (_run4,)).fetchone()[0]),
      (True, "billing_record"))


#------------------------------------------------------------------------------

section("SECTION 5 -- P2: the Converse cache-unconfirmed path, real graph, real "
        "writer")


class _VirtualClock:
    def __init__(self):
        self._t = 1000.0
        self._lock = threading.Lock()

    def now(self):
        with self._lock:
            return self._t

    def sleep(self, seconds):
        with self._lock:
            self._t += max(0.0, float(seconds))


def _converse_reply(text, *, read, write, input_tokens, output_tokens, stop):
    return {"ResponseMetadata": {"RequestId": "stub"},
            "output": {"message": {"role": "assistant",
                                   "content": [{"text": text}]}},
            "stopReason": stop,
            "usage": {"inputTokens": input_tokens, "outputTokens": output_tokens,
                      "totalTokens": input_tokens + output_tokens,
                      "cacheReadInputTokens": read,
                      "cacheWriteInputTokens": write}}


class _ConverseStub:
    """Warmup 1 reports a zero cache write; warmup 2 writes; the wave reads."""

    def __init__(self):
        self.requests = []
        self.warmups = 0
        self._lock = threading.Lock()

    def converse(self, **kwargs):
        text = kwargs["messages"][0]["content"][0]["text"]
        with self._lock:
            self.requests.append(text)
            if text == config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE:
                self.warmups += 1
                warm = self.warmups
        if text == config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE:
            return _converse_reply("", read=0, write=(0 if warm == 1 else 9000),
                                   input_tokens=1200, output_tokens=1,
                                   stop="max_tokens")
        ids = re.findall(r"<<<TRIAL_DATA nct_id=(\S+) ", text)
        return _converse_reply(_eligible_body(ids), read=9000, write=0,
                               input_tokens=1000, output_tokens=100,
                               stop="end_turn")


_DB5 = new_db("converse.db")
_run5 = _dl.start_run_record("batch", db_path=_DB5, fingerprint=FIXED_FP)
_stub5 = _ConverseStub()
_attempts5 = []
_clock = _VirtualClock()
_prev_time = _pr.set_time_source(_clock.now, _clock.sleep)
_saved5 = deps.set_overrides({deps.BEDROCK_ANTHROPIC_CLIENT: _stub5})
try:
    with settings(MATCHING_PROVIDER=config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC,
                  MATCHING_PER_TRIAL_CALLS_ENABLED=True, SPEND_CAP_USD=None):
        with sink_installed(_DB5, "converse-camp", _run5):
            _state = _graph.build_initial_state(PATIENT)
            _state.update(filtered_trials=TRIALS, mesh_filter_applied=True,
                          mesh_filter_skip_reason="applied")
            _final5 = drive(build_graph(_attempts5).invoke, _state)
            _measured5 = _spend.SPEND_LEDGER.measured
            _calls5 = _spend.SPEND_LEDGER.calls
            _result5 = at(_final5, "result")
            if isinstance(_result5, dict):
                _result5["qdrant_collection"] = "stub"
                _result5["patient_data_hash"] = compute_patient_hash(PATIENT)
                drive(_dl.log_inference, _result5, PATIENT, run_id=_run5,
                      db_path=_DB5)
finally:
    deps.restore_overrides(_saved5)
    _pr.set_time_source(*_prev_time)
_rows5 = billing_rows(_DB5)
_row5 = sqlite3.connect(_DB5)
_row5.row_factory = sqlite3.Row
_inf5 = [dict(r) for r in _row5.execute("SELECT * FROM inferences")]
_row5.close()
_first5, _last5 = at(_attempts5, 0), at(_attempts5, -1)
check("5a non-degeneracy: two attempts ran, the first failing on an "
      "UNCONFIRMED cache write (the P2 path)",
      (len(_attempts5), "not cached" in str(at(_first5, "error"))), (2, True))
check("5a-i ...the first attempt was billed exactly its warmup, whose usage "
      "reported no cache write",
      (at(_first5, "llm_classifier_calls"),
       at(_first5, "llm_classifier_input_tokens")), (1, 1200))
check("5b the stored row describes the FINAL attempt: its warmup and its three "
      "trial calls, and no leftover of attempt 1",
      (len(_inf5), at(at(_inf5, 0), "llm_classifier_calls"),
       at(at(_inf5, 0), "llm_classifier_input_tokens"),
       at(at(_inf5, 0), "error")), (1, 4, 10200 + 3 * 10000, ""))
check("5c *** the CUMULATIVE record holds EVERY billed attempt: both warmups "
      "and the three trial calls, five rows, all settled on a response ***",
      ([r["outcome"] for r in _rows5], len(_stub5.requests)),
      (["response"] * 5, 5))
check("5d ...and its total equals the in-process ledger's, which the final row "
      "cannot",
      (near(sum(r["settled_usd"] for r in _rows5), _measured5), _calls5,
       near(at(at(_inf5, 0), "estimated_cost_usd") or -1.0, _measured5)),
      (True, 5, False))
with settings(MATCHING_PROVIDER=config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
    _CONVERSE_WIRE = config.matching_wire_model()
check("5e ...every row reserved at the Converse arm's wire model",
      sorted({r["model"] for r in _rows5}), [_CONVERSE_WIRE])
check("5e-i non-degeneracy: the Converse wire model is not the OpenAI one",
      _CONVERSE_WIRE != _WIRE, True)


#------------------------------------------------------------------------------

section("SECTION 6 -- cross-process proofs through the REAL main()")

_CHILD = os.path.join(_TMP, "billing_child.py")
Path(_CHILD).write_text(r'''
import json, os, re, signal, sqlite3, sys, types
# THE SAME STAND-INS SERVE TWO LAUNCHES (the P3 recovery). Run as a script, the
# config is argv[1] and this file calls runner.main() itself. Copied into a hook
# directory as `usercustomize.py`, the config arrives in ONC_BILLING_HOOK_CFG,
# this file only installs the stand-ins at interpreter startup, and the REAL
# entry point `25- Batch Runner.py` parses its own flags (--fresh) and calls
# main().
_HOOKED = bool(os.environ.get("ONC_BILLING_HOOK_CFG"))
cfg = json.loads(os.environ["ONC_BILLING_HOOK_CFG"] if _HOOKED else sys.argv[1])
sys.path.insert(0, cfg["repo"]); sys.path.insert(0, cfg["tests"])
os.environ["ONCOTRIAGE_DEFER_LOCAL_MODELS"] = "1"
from oncotriage import config, paths, spend, run_fingerprint
import _provider_pin
_provider_pin.pin_openai_arm("billing-child", out=lambda *a, **k: None)
from oncotriage import provider_resilience as pr
from oncotriage.agent import deps, evaluation as ev, graph as gr
from oncotriage.agent import terminal as term
from oncotriage.agent.patient import compute_patient_hash
from oncotriage.agent.state import TrialMatchState
from oncotriage.batch import runner
from oncotriage.storage import database_logger as dl
from langgraph.graph import END, StateGraph

paths._RESOLVED["data_fhir_path"] = cfg["corpus"] + os.sep
paths._RESOLVED["inferences_path"] = cfg["db"]
paths._RESOLVED["checkpoint_path"] = cfg["cp"] + os.sep
FIXED = cfg["fingerprint"]
run_fingerprint.current = lambda *a, **k: dict(FIXED)
pr.full_jitter_delay = lambda retry_number, rng=None: 0.0
config.MATCHING_PER_TRIAL_CALLS_ENABLED = True
config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS = 3
config.SPEND_CAP_ENFORCED = True
config.SPEND_CAP_USD = cfg.get("cap")
WIRE = config.matching_wire_model()
PATIENT = cfg["patient"]
TRIALS = cfg["trials"]

def dump(**kw):
    with open(cfg["out"], "w") as fh:
        json.dump(kw, fh)

def marker(name):
    open(os.path.join(cfg["cp"], name), "w").close()

class Usage:
    def __init__(self, p, c):
        self.prompt_tokens, self.completion_tokens = p, c
        self.completion_tokens_details = None

def response(content, tokens, finish="stop"):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(
            content=content, refusal=None), finish_reason=finish)],
        usage=Usage(*tokens), model=WIRE)

def body(ids):
    return json.dumps({"evaluations": [
        {"assessment": "ok", "eligible": "eligible",
         "inclusion_criteria": [{"criterion": "Age 18+", "patient_value": "61",
                                 "status": "met"}],
         "exclusion_criteria": [], "match_score": 0.0, "nct_id": i}
        for i in ids]})

class Stub:
    def __init__(self, fn):
        self.fn, self.calls = fn, 0
        self.chat = types.SimpleNamespace(completions=self)
    def create(self, **kw):
        self.calls += 1
        return self.fn(kw)

def entry(fhir_path, status, is_resample=False, error="planted by the harness"):
    return {"patient_id": os.path.basename(str(fhir_path)), "status": status,
            "eligible_matches": 0, "near_misses": 0, "not_evaluable": 0,
            "total_time": 0.01, "timestamp": "2026-09-13T00:00:00",
            "error": "" if status == "success" else error,
            "is_resample": is_resample}

class Tracking:
    def start_run(self, **k): pass
    def log_run_metrics(self, *a, **k): pass
    def end_run(self, **k): pass

runner.build_bm25_index_from_qdrant = lambda *a, **k: (object(), ["NCT1"])
runner.build_matching_graph = lambda *a, **k: object()
runner.tracking = Tracking()
runner.run_resample = lambda **k: None
mode = cfg["mode"]

if mode == "concurrent":
    db = cfg["db"]
    dl.initialize_database(db)
    rid = dl.start_run_record("batch", db_path=db, fingerprint=FIXED)
    cb = runner.establish_billing_campaign(False, FIXED, cfg["digest"], rid, db)
    if cfg.get("resume_only"):
        dump(campaign_id=cb.campaign_id, decision=cb.decision,
             seed_usd=cb.seed.usd, attempts=cb.seed.rows)
        sys.exit(0)
    spend.BILLING_RECORD.install(dl.BillingRecordSink(db, cb.campaign_id, rid))
    import time
    while not os.path.exists(cfg["release"]):
        time.sleep(0.01)
    for _ in range(cfg["n"]):
        h = spend.BILLING_RECORD.reserve("stage5", WIRE, 4000, 400, where="c")
        spend.BILLING_RECORD.settle(h, spend.BILLING_OUTCOME_RESPONSE,
                                    model=WIRE, prompt_tokens=cfg["pin"],
                                    completion_tokens=cfg["pout"])
    each = spend.price_usage(WIRE, cfg["pin"], cfg["pout"])[0]
    dump(campaign_id=cb.campaign_id, expected=each * cfg["n"],
         faults=dict(spend.BILLING_RECORD_FAULTS))
    sys.exit(0)

def patient(fhir_path=None, graph=None, is_resample=False, run_id=None,
            db_path=None):
    name = os.path.basename(str(fhir_path))
    if mode == "billed_then_zero":
        attempts, ref = [], [0]
        def fn(kw):
            user = kw["messages"][1]["content"]
            if user == config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE:
                return response("", (1000, 100), finish="length")
            ids = re.findall(r"<<<TRIAL_DATA nct_id=(\S+) ", user)
            if TRIALS[0]["trial"]["nct_id"] in ids:
                return response("{not json", (1000, 100))
            return response(body(ids), (1000, 100))
        stub = Stub(fn)
        deps.set_override(deps.OPENAI_CLIENT, stub)
        try:
            def s5(state):
                out = ev.node_llm_classifier_evaluation(state)
                attempts.append(dict(out))
                return out
            g = StateGraph(TrialMatchState)
            g.add_node("llm_classifier_evaluation", s5)
            g.add_node("finalize", term.node_finalize)
            g.add_node("error_handler", term.node_error_handler)
            g.set_entry_point("llm_classifier_evaluation")
            g.add_conditional_edges("llm_classifier_evaluation",
                                    gr.route_after_llm_classifier,
                                    {"finalize": "finalize",
                                     "llm_classifier_retry": "llm_classifier_evaluation",
                                     "error_handler": "error_handler"})
            g.add_edge("finalize", END); g.add_edge("error_handler", END)
            state = gr.build_initial_state(PATIENT)
            state.update(filtered_trials=TRIALS, mesh_filter_applied=True,
                         mesh_filter_skip_reason="applied")
            result = g.compile().invoke(state)["result"]
            result["qdrant_collection"] = "stub"
            result["patient_data_hash"] = compute_patient_hash(PATIENT)
            dl.log_inference(result, PATIENT, run_id=run_id, db_path=db_path)
        finally:
            deps.clear_override(deps.OPENAI_CLIENT)
        dump(measured=spend.SPEND_LEDGER.measured,
             ledger_calls=spend.SPEND_LEDGER.calls, requests=stub.calls,
             attempts=len(attempts),
             campaign_id=spend.BILLING_RECORD.installed_sink().campaign_id,
             row_input_tokens=result.get("llm_classifier_input_tokens"),
             runner_file=os.path.realpath(runner.__file__))
        return entry(fhir_path, "error")
    if mode == "observe":
        seed = spend.SPEND_LEDGER.seeded
        dump(seed=seed._asdict(), remaining=spend.remaining(spend.SPEND_SOURCE_STAGE5),
             measured=spend.SPEND_LEDGER.measured,
             campaign_id=spend.BILLING_RECORD.installed_sink().campaign_id,
             describe=spend.describe_seed(seed))
        return entry(fhir_path, "error")
    if mode == "kill_between":
        def fn(kw):
            marker("sent")
            os.kill(os.getpid(), signal.SIGKILL)
        deps.set_override(deps.OPENAI_CLIENT, Stub(fn))
        ev.call_matching_model("system prompt", "user prompt")
        return entry(fhir_path, "error")
    if mode == "kill_mid_settle":
        class KillConn(sqlite3.Connection):
            armed = False
            def commit(self):
                if KillConn.armed:
                    marker("settling")
                    os.kill(os.getpid(), signal.SIGKILL)
                return super().commit()
        orig = dl._open_connection
        def opener(db_path, read_only=False):
            if read_only:
                return orig(db_path, read_only=True)
            conn = sqlite3.connect(db_path, timeout=30.0, factory=KillConn)
            def trace(sql):
                if sql.lstrip().upper().startswith("UPDATE BILLING_ATTEMPTS"):
                    KillConn.armed = True
            conn.set_trace_callback(trace)
            return conn
        deps.set_override(deps.OPENAI_CLIENT,
                          Stub(lambda kw: response("{}", (500, 50))))
        dl._open_connection = opener
        try:
            ev.call_matching_model("system prompt", "user prompt")
        finally:
            dl._open_connection = orig
        return entry(fhir_path, "error")
    if mode == "legacy_p0":
        return entry(fhir_path, "success" if name == "patient0.json" else "error")
    raise SystemExit(f"unknown mode {mode}")

runner.process_patient = patient
if not _HOOKED:
    runner.main()
''', encoding="utf-8")


def make_corpus(root, count):
    os.makedirs(root, exist_ok=True)
    for index in range(count):
        with open(os.path.join(root, f"patient{index}.json"), "w",
                  encoding="utf-8") as handle:
            json.dump({"resourceType": "Bundle", "entry": []}, handle)
    return root


def child(mode, *, db, cp, corpus, cap=None, out=None, extra=None,
          timeout=240):
    os.makedirs(cp, exist_ok=True)
    out = out or os.path.join(cp, f"{mode}.json")
    if os.path.exists(out):
        os.remove(out)
    cfg = {"mode": mode, "repo": _REPO, "tests": _TESTS, "db": db, "cp": cp,
           "corpus": corpus, "cap": cap, "out": out, "fingerprint": FIXED_FP,
           "patient": PATIENT, "trials": TRIALS}
    cfg.update(extra or {})
    env = dict(os.environ)
    _control_harness.isolate_qdrant(env)
    env["ONCOTRIAGE_DEFER_LOCAL_MODELS"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run([sys.executable, _CHILD, json.dumps(cfg)],
                          capture_output=True, text=True, timeout=timeout,
                          env=env, cwd=_TMP)
    data = None
    if os.path.exists(out):
        with open(out) as fh:
            data = json.load(fh)
    return proc, data


def tail(proc, n=25):
    return "\n".join((proc.stdout + proc.stderr).splitlines()[-n:])


# ---- 6A: P1 -- a billed attempt, then a zero-usage failure, then a FRESH
#          process resuming the campaign.
_COST_SMALL = _spend.price_usage(_WIRE, 1000, 100)[0]
_CORP_A = make_corpus(os.path.join(_TMP, "corpus_a"), 1)
_DB_A = os.path.join(_TMP, "p1.db")
_CP_A = os.path.join(_TMP, "cp_a")
_p1, _p1d = child("billed_then_zero", db=_DB_A, cp=_CP_A, corpus=_CORP_A,
                  cap=_COST_SMALL * 3.5)
if _p1d is None:
    print(tail(_p1, 60))
check("6a process 1 ran main() to its end", _p1.returncode, 0)
check("6a-i non-degeneracy: attempt 1 was billed four responses and the final "
      "attempt issued nothing (three attempts, four requests)",
      (at(_p1d, "attempts"), at(_p1d, "requests"),
       near(at(_p1d, "measured"), _COST_SMALL * 4)), (3, 4, True))
_rows_a = billing_rows(_DB_A) if os.path.exists(_DB_A) else []
check("6b the durable record holds attempt 1's four billed responses",
      ([r["outcome"] for r in _rows_a],
       near(sum(r["settled_usd"] or 0 for r in _rows_a), _COST_SMALL * 4)),
      (["response"] * 4, True))
_conn = sqlite3.connect(f"file:{_DB_A}?mode=ro", uri=True)
_old_reader = _conn.execute(
    "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM inferences").fetchone()[0]
_conn.close()
check("6c CONTROL: the deleted reader's figure -- a sum over final-attempt "
      "costs -- is ZERO for the same campaign", _old_reader, 0.0)
check("6c-i ...and no checkpoint exists, so the next run is NOT a checkpoint "
      "resume (the zero-success hole)",
      os.path.exists(os.path.join(_CP_A, _runner.CHECKPOINT_FILENAME)), False)

_p2, _p2d = child("observe", db=_DB_A, cp=_CP_A, corpus=_CORP_A, cap=100.0)
if _p2d is None:
    print(tail(_p2, 60))
check("6d *** a FRESH process continues the SAME campaign ***",
      (_p2.returncode, at(_p2d, "campaign_id")), (0, at(_p1d, "campaign_id")))
check("6e *** and its budget INCLUDES every charge the first process made: "
      "seeded from the durable record ***",
      (near(at(at(_p2d, "seed"), "usd"), _COST_SMALL * 4),
       at(at(_p2d, "seed"), "source"), at(at(_p2d, "seed"), "rows")),
      (True, "billing_record", 4))
check("6f *** remaining = cap - every charge of process 1 ***",
      near(at(_p2d, "remaining"), 100.0 - _COST_SMALL * 4, 1e-7), True)


# ---- 6A-ii: THE SUMMARY SURFACE (the P3 recovery). 6d-6f prove the BUDGET
#          continues; these read what an operator reads -- campaign_summary,
#          run_summary and the Run Health tables -- over the database the two
#          REAL main() processes wrote, and require one campaign there too.
from oncotriage.storage import queries as _queries                 # noqa: E402
from oncotriage.dashboard.tabs import run_health as _run_health    # noqa: E402


def surface(db):
    """(runs, campaign_summary, run_summary), read through a mode=ro URI."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        runs = conn.execute(
            "SELECT id, resumed, billing_campaign_id, status FROM runs "
            "ORDER BY id").fetchall()
        camps = drive(_queries.run, conn, "campaign_summary")
        rsum = drive(_queries.run, conn, "run_summary")
    finally:
        conn.close()
    return runs, camps, rsum


def _nullable(value):
    return None if value is None or value != value else value


def campaign_rows(camps):
    """Sorted ``[(run_ids, billing_campaign_id)]`` of campaign_summary."""
    try:
        return sorted((str(r.run_ids), _nullable(r.billing_campaign_id))
                      for r in camps.itertuples())
    except Exception as exc:                                  # noqa: BLE001
        return _Absent(f"{type(exc).__name__}: {exc}")


def grouping_faults(db, runs, camps):
    """Everything the grouping duplicates or omits; ``[]`` when it is exact.

    Every run in exactly one summary row; a row carries at most one billing
    campaign and it is the one its runs carry; every billing row sits in the
    summary row of its own campaign; each campaign's durable total equals its
    rows; and the summary's cost column sums to the inference costs of the runs
    it covers.
    """
    faults = []
    try:
        rows = list(camps.itertuples())
    except Exception as exc:                                  # noqa: BLE001
        return [f"campaign_summary unreadable: {exc}"]
    member_of, bc_of_run = {}, {r[0]: r[2] for r in runs}
    for row in rows:
        ids = [int(x) for x in str(row.run_ids).split(" -> ")]
        for rid in ids:
            member_of.setdefault(rid, []).append(row.campaign_id)
        carried = {bc_of_run.get(i) for i in ids} - {None}
        if len(carried) > 1 or _nullable(row.billing_campaign_id) not in (
                carried or {None}):
            faults.append(("row billing id", row.run_ids, carried,
                           row.billing_campaign_id))
    if sorted((k, len(v)) for k, v in member_of.items()) != sorted(
            (r[0], 1) for r in runs):
        faults.append(("run membership", member_of))
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        attempts = conn.execute(
            "SELECT campaign_id, run_id FROM billing_attempts").fetchall()
        costs = conn.execute(
            "SELECT COALESCE(SUM(estimated_cost_usd), 0) FROM inferences "
            "WHERE run_id IS NOT NULL").fetchone()[0]
    finally:
        conn.close()
    row_of_campaign = {_nullable(r.billing_campaign_id):
                       {int(x) for x in str(r.run_ids).split(" -> ")}
                       for r in rows}
    for cid, rid in attempts:
        if rid not in row_of_campaign.get(cid, set()):
            faults.append(("billing row outside its campaign's summary row",
                           cid, rid))
    for cid in {a[0] for a in attempts}:
        total = drive(_dl.campaign_billing_total, cid, db_path=db)
        if getattr(total, "attempts", None) != sum(1 for a in attempts
                                                   if a[0] == cid):
            faults.append(("durable total disagrees with its rows", cid))
    if not near(float(sum(r.total_cost_usd for r in rows)), costs,
                1e-4 * max(1, len(rows))):
        faults.append(("cost column", float(sum(r.total_cost_usd
                                                for r in rows)), costs))
    return faults


_runs_a, _camps_a, _rsum_a = surface(_DB_A)
_C_A = at(_p1d, "campaign_id")
check("6f-i the run rows: BOTH processes read resumed = 0 (no checkpoint was "
      "handed over) and both carry process 1's billing campaign",
      [(r[1], r[2]) for r in _runs_a], [(0, _C_A), (0, _C_A)])
check("6f-ii *** the SUMMARY SURFACE shows ONE campaign for that one budget: "
      "campaign_summary groups both runs under process 1's billing id ***",
      campaign_rows(_camps_a),
      [(f"{at(at(_runs_a, 0), 0)} -> {at(at(_runs_a, 1), 0)}", _C_A)])
check("6f-iii ...and the grouping duplicates and omits no run, billing row or "
      "charge", grouping_faults(_DB_A, _runs_a, _camps_a), [])
def column(frame, name):
    """``list(frame[name])``, or a named absence -- a missing column is the
    defect 6f-iv exists to catch, so it must fail the check, not abort the run."""
    try:
        return list(frame[name])
    except Exception as exc:                                  # noqa: BLE001
        return _Absent(f"{type(exc).__name__}: {exc}")


_ctab = drive(_run_health._build_campaign_table, _camps_a)
_rtab = drive(_run_health._build_run_table, _rsum_a)
check("6f-iv ...and the Run Health tables name that one billing campaign on "
      "the campaign row and on both run rows",
      (column(_ctab, "billing campaign"), column(_rtab, "billing campaign")),
      ([_C_A], [_C_A, _C_A]))
check("6f-v non-degeneracy: the campaign really billed (rows under process 1 "
      "only; the observer bills nothing)",
      sorted({r["run_id"] for r in _rows_a}), [at(at(_runs_a, 0), 0)])


# ---- 6B: SIGKILL between reservation and settlement
_CORP_K = make_corpus(os.path.join(_TMP, "corpus_k"), 1)
_DB_K = os.path.join(_TMP, "kill_between.db")
_CP_K = os.path.join(_TMP, "cp_k")
_pk, _ = child("kill_between", db=_DB_K, cp=_CP_K, corpus=_CORP_K, cap=100.0)
_rows_k = billing_rows(_DB_K) if os.path.exists(_DB_K) else []
_expected_res = _spend.price_usage(
    _WIRE, _ev._reservation_input_tokens("system prompt", "user prompt"),
    config.MATCHING_MAX_TOKENS)[0]
check("6g non-degeneracy: the child reached the provider and was SIGKILLed",
      (_pk.returncode, os.path.exists(os.path.join(_CP_K, "sent"))),
      (-signal.SIGKILL, True))
check("6h *** the reservation was DURABLE before the send: one RESERVED row at "
      "the upper bound ***",
      ([r["state"] for r in _rows_k],
       near(at(at(_rows_k, 0), "reserved_usd"), _expected_res)),
      (["reserved"], True))
_pk2, _pk2d = child("observe", db=_DB_K, cp=_CP_K, corpus=_CORP_K, cap=100.0)
if _pk2d is None:
    print(tail(_pk2, 60))
check("6i *** a fresh process charges the unresolved reservation against its "
      "budget ***",
      (near(at(at(_pk2d, "seed"), "usd"), _expected_res),
       at(at(_pk2d, "seed"), "unresolved")), (True, 1))
check("6i-i ...and says so", "RESERVED upper bound" in str(at(_pk2d, "describe")),
      True)


# ---- 6C: SIGKILL mid-settlement
_CORP_M = make_corpus(os.path.join(_TMP, "corpus_m"), 1)
_DB_M = os.path.join(_TMP, "kill_settle.db")
_CP_M = os.path.join(_TMP, "cp_m")
_pm, _ = child("kill_mid_settle", db=_DB_M, cp=_CP_M, corpus=_CORP_M, cap=100.0)
_rows_m = billing_rows(_DB_M) if os.path.exists(_DB_M) else []
check("6j non-degeneracy: the child was SIGKILLed inside the settlement's "
      "commit", (_pm.returncode, os.path.exists(os.path.join(_CP_M, "settling"))),
      (-signal.SIGKILL, True))
check("6k *** the half-written settlement rolled back: the row is still "
      "RESERVED at the upper bound, never settled at a partial figure ***",
      ([r["state"] for r in _rows_m], [r["settled_usd"] for r in _rows_m]),
      (["reserved"], [None]))
_campaign_m = json.loads(Path(os.path.join(
    _CP_M, _runner.CAMPAIGN_RECORD_FILENAME)).read_text())["campaign_id"]
check("6l ...and the campaign total charges it there",
      near(_dl.campaign_billing_total(_campaign_m, db_path=_DB_M).usd,
           _expected_res), True)


# ---- 6D: two concurrent campaigns, one database
_DB_C = new_db("concurrent.db")
_REL = os.path.join(_TMP, "release")
_procs = []
for _tag, _pin, _pout in (("x", 1000, 100), ("y", 3000, 300)):
    _cp = os.path.join(_TMP, f"cp_{_tag}")
    os.makedirs(_cp, exist_ok=True)
    _cfg = {"mode": "concurrent", "repo": _REPO, "tests": _TESTS, "db": _DB_C,
            "cp": _cp, "corpus": _TMP, "cap": None,
            "out": os.path.join(_cp, "out.json"), "fingerprint": FIXED_FP,
            "patient": PATIENT, "trials": TRIALS, "digest": f"digest-{_tag}",
            "release": _REL, "n": 40, "pin": _pin, "pout": _pout}
    _env = dict(os.environ)
    _control_harness.isolate_qdrant(_env)
    _procs.append((_tag, _cfg, subprocess.Popen(
        [sys.executable, _CHILD, json.dumps(_cfg)], stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, text=True, env=_env, cwd=_TMP)))
import time as _time                                             # noqa: E402
_time.sleep(0.5)
_waited = _control_harness.wait_for(
    lambda: all(sqlite3.connect(_DB_C).execute(
        "SELECT COUNT(*) FROM runs").fetchone()[0] >= 2 for _ in (0,)), 120)
Path(_REL).touch()
_conc = {}
for _tag, _cfg, _proc in _procs:
    _outp, _ = _proc.communicate(timeout=240)
    _conc[_tag] = (json.loads(Path(_cfg["out"]).read_text())
                   if os.path.exists(_cfg["out"]) else {"__out__": _outp[-2000:]})
_tx = drive(_dl.campaign_billing_total, at(_conc["x"], "campaign_id"),
            db_path=_DB_C)
_ty = drive(_dl.campaign_billing_total, at(_conc["y"], "campaign_id"),
            db_path=_DB_C)
check("6m non-degeneracy: both campaigns wrote 40 attempts into ONE database, "
      "and their per-attempt prices differ",
      (len(billing_rows(_DB_C)), at(_conc["x"], "expected") !=
       at(_conc["y"], "expected")), (80, True))
check("6n *** each campaign's total is exactly its own charges ***",
      (near(getattr(_tx, "usd", None), at(_conc["x"], "expected"), 1e-7),
       near(getattr(_ty, "usd", None), at(_conc["y"], "expected"), 1e-7),
       getattr(_tx, "attempts", None), getattr(_ty, "attempts", None)),
      (True, True, 40, 40))
_rx, _rxd = child("concurrent", db=_DB_C, cp=os.path.join(_TMP, "cp_x"),
                  corpus=_TMP, extra={"digest": "digest-x",
                                      "resume_only": True})
check("6o ...and a fresh process in one campaign's directory continues THAT "
      "campaign with THAT budget",
      (at(_rxd, "campaign_id") == at(_conc["x"], "campaign_id"),
       at(_rxd, "decision"), near(at(_rxd, "seed_usd"),
                                  at(_conc["x"], "expected"), 1e-7)),
      (True, "continued", True))


# ---- 6E: historical campaigns
_CORP_H = make_corpus(os.path.join(_TMP, "corpus_h"), 2)
_DB_H = os.path.join(_TMP, "legacy.db")
_CP_H = os.path.join(_TMP, "cp_h")
_ph0, _ = child("legacy_p0", db=_DB_H, cp=_CP_H, corpus=_CORP_H, cap=100.0)
check("6p non-degeneracy: the legacy campaign left a checkpoint with a "
      "completed patient and a FAILED run",
      (_ph0.returncode,
       os.path.exists(os.path.join(_CP_H, _runner.CHECKPOINT_FILENAME)),
       sqlite3.connect(_DB_H).execute(
           "SELECT status FROM runs WHERE id = 1").fetchone()[0]),
      (0, True, "FAILED"))


def _make_legacy(tag, mutate_sql=None):
    """Copy the P0 state and turn it into a PRE-BILLING-ERA campaign."""
    db = os.path.join(_TMP, f"legacy_{tag}.db")
    cp = os.path.join(_TMP, f"cp_h_{tag}")
    shutil.copytree(_CP_H, cp)
    src = sqlite3.connect(_DB_H)
    dst = sqlite3.connect(db)
    src.backup(dst)
    src.close()
    os.remove(os.path.join(cp, _runner.CAMPAIGN_RECORD_FILENAME))
    dst.execute("DELETE FROM billing_attempts")
    # A PRE-BILLING-ERA BUILD STAMPED NO BILLING CAMPAIGN ON ITS RUN ROWS (era
    # 18, the billing closure pass). Leaving the id in place would describe a
    # billing-era campaign whose rows were deleted -- which the identity
    # recovery now correctly treats as a campaign to recover or refuse, not as
    # history.
    dst.execute("UPDATE runs SET billing_campaign_id = NULL")
    for pid, cost in (("patient0.json", 0.40), ("patient1.json", 0.10)):
        dst.execute(
            "INSERT INTO inferences (patient_id, timestamp, estimated_cost_usd, "
            "llm_classifier_retries, retrieval_channels, run_id) VALUES "
            "(?, '2026-09-01T00:00:00', ?, 0, ?, 1)",
            (pid, cost, json.dumps({"dense": {"status": "ablated"}})))
    if mutate_sql:
        for statement in ((mutate_sql,) if isinstance(mutate_sql, str)
                          else mutate_sql):
            dst.execute(statement)
    dst.commit()
    dst.close()
    return db, cp


_DBc, _CPc = _make_legacy("covered")
_phc, _phcd = child("observe", db=_DBc, cp=_CPc, corpus=_CORP_H, cap=100.0)
if _phcd is None:
    print(tail(_phc, 60))
check("6q *** a COVERED historical campaign resumes with the EVIDENCE-seeded "
      "figure ***",
      (_phc.returncode, near(at(at(_phcd, "seed"), "usd"), 0.50),
       at(at(_phcd, "seed"), "source")), (0, True, "billing_record"))
_hrows = billing_rows(_DBc)
check("6r ...recorded ONCE as a historical_evidence row, so the next resume "
      "carries it without re-deciding",
      ([r["kind"] for r in _hrows], [r["settled_usd"] for r in _hrows]),
      (["historical_evidence"], [0.50]))

for _tag, _sql, _reason in (
        ("retries", "UPDATE inferences SET llm_classifier_retries = 2 "
                    "WHERE patient_id = 'patient1.json'",
         "attempt_history_not_on_rows"),
        ("dense", "UPDATE inferences SET retrieval_channels = "
                  "'{\"dense\": {\"status\": \"ok\"}}'",
         "embedding_spend_not_on_rows"),
        ("killed", "UPDATE runs SET status = 'KILLED' WHERE id = 1",
         "not_cleanly_finalized")):
    _dbu, _cpu = _make_legacy(_tag, _sql)
    _pu, _pud = child("observe", db=_dbu, cp=_cpu, corpus=_CORP_H, cap=100.0)
    _text = _pu.stdout + _pu.stderr
    _conn = sqlite3.connect(_dbu)
    _latest = _conn.execute(
        "SELECT status, note FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    _billed = _conn.execute("SELECT COUNT(*) FROM billing_attempts").fetchone()[0]
    _conn.close()
    check(f"6s-{_tag} *** an uncoverable historical campaign ({_reason}) is "
          f"REFUSED BY NAME before paid work ***",
          (_pu.returncode, _pud is None,
           "REFUSING TO START PAID WORK: historical_spend_uncovered" in _text,
           _reason in _text), (1, True, True, True))
    check(f"6s-{_tag}-i ...its run row is closed KILLED (runs.note stays the "
          f"operator's column), no billing row and no campaign record exist",
          (_latest, _billed,
           os.path.exists(os.path.join(_cpu, _runner.CAMPAIGN_RECORD_FILENAME))),
          (("KILLED", None), 0, False))

# ---- 6F: the counter registry through the REAL main() (the P2 recovery).
# 6q is the positive control for these: the same legacy state, its registry
# intact as the producing child wrote it, is COVERED. Each case below changes
# only the registry, so a refusal here is about the registry and nothing else.
for _tag, _sql, _names in (
        # A BUILD THAT NEVER REGISTERED ONE COUNTER: the writer keeps the
        # registry and the meta count in step, so both are one short.
        ("registry_lacks_counter",
         ("DELETE FROM run_counter_registry "
          "WHERE name = 'PROVIDER_UNCONFIRMED_BILLING'",
          "UPDATE run_metrics SET value = value - 1 WHERE category = 'meta' "
          "AND name = 'counters_registered'"),
         ("PROVIDER_UNCONFIRMED_BILLING",)),
        # A PRE-ERA-18 DATABASE: no registry table and an older stamp. The child
        # migrates it on open, so the table exists again and is EMPTY for the
        # predecessor -- exactly what a real era-17 campaign looks like to this
        # build.
        ("pre_era_18",
         ("DROP TABLE run_counter_registry", "PRAGMA user_version = 17"),
         tuple(_dl.HISTORICAL_UNRECORDED_BILLING_COUNTERS))):
    _dbu, _cpu = _make_legacy(_tag, _sql)
    _pu, _pud = child("observe", db=_dbu, cp=_cpu, corpus=_CORP_H, cap=100.0)
    _text = _pu.stdout + _pu.stderr
    _conn = sqlite3.connect(_dbu)
    _latest = _conn.execute(
        "SELECT status FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    _billed = _conn.execute("SELECT COUNT(*) FROM billing_attempts").fetchone()[0]
    _conn.close()
    check(f"6t-{_tag} *** refused through main() under "
          f"counter_registration_unproven, before paid work ***",
          (_pu.returncode, _pud is None,
           "REFUSING TO START PAID WORK: historical_spend_uncovered" in _text,
           "counter_registration_unproven" in _text), (1, True, True, True))
    check(f"6t-{_tag}-i ...and the printed refusal NAMES exactly the unproven "
          f"counters -- no fewer, and no others",
          f"unproven counter(s): {', '.join(_names)};" in _text, True)
    check(f"6t-{_tag}-ii ...run row KILLED, nothing billed, no campaign record",
          (_latest, _billed,
           os.path.exists(os.path.join(_cpu, _runner.CAMPAIGN_RECORD_FILENAME))),
          (("KILLED",), 0, False))


# ---- 6G: --fresh THROUGH THE REAL ENTRY POINT (the P3 recovery). A
#          zero-success run, then `25- Batch Runner.py --fresh`, then the entry
#          point again with no flag. The stand-ins arrive through a
#          `usercustomize` hook -- the child script copied, not exec'd -- so the
#          flag parsing, clear_checkpoint() and main() are the shipped ones.
_HOOK_DIR = os.path.join(_TMP, "entry_hook")
os.makedirs(_HOOK_DIR, exist_ok=True)
shutil.copyfile(_CHILD, os.path.join(_HOOK_DIR, "usercustomize.py"))
_ENTRY = os.path.join(_REPO, "25- Batch Runner.py")


def entry_point(mode, *flags, db, cp, corpus, cap=None, timeout=240):
    os.makedirs(cp, exist_ok=True)
    out = os.path.join(cp, f"entry_{mode}.json")
    if os.path.exists(out):
        os.remove(out)
    cfg = {"mode": mode, "repo": _REPO, "tests": _TESTS, "db": db, "cp": cp,
           "corpus": corpus, "cap": cap, "out": out, "fingerprint": FIXED_FP,
           "patient": PATIENT, "trials": TRIALS}
    env = dict(os.environ)
    _control_harness.isolate_qdrant(env)
    # The entry point takes the PROVIDER ALLOWANCE lock, which every harness
    # that launches it shares; this file's own directory keeps a concurrent
    # bucket-A file (or an operator's run) from refusing it with exit 3.
    _control_harness.isolate_locks(env, os.path.join(_TMP, "entry_locks"))
    env["ONC_BILLING_HOOK_CFG"] = json.dumps(cfg)
    env["ONCOTRIAGE_DEFER_LOCAL_MODELS"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        [_HOOK_DIR, _TESTS, _REPO]
        + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    proc = subprocess.run([sys.executable, _ENTRY, *flags],
                          capture_output=True, text=True, timeout=timeout,
                          env=env, cwd=_TMP)
    data = None
    if os.path.exists(out):
        with open(out) as fh:
            data = json.load(fh)
    return proc, data


_CORP_F = make_corpus(os.path.join(_TMP, "corpus_f"), 1)
_DB_F = os.path.join(_TMP, "fresh.db")
_CP_F = os.path.join(_TMP, "cp_f")
_REC_F = os.path.join(_CP_F, _runner.CAMPAIGN_RECORD_FILENAME)
_f1, _f1d = child("billed_then_zero", db=_DB_F, cp=_CP_F, corpus=_CORP_F,
                  cap=100.0)
_C1 = at(_f1d, "campaign_id")
check("6u non-degeneracy: process 1 billed, completed no patient, and left a "
      "campaign record naming its campaign and no checkpoint",
      (isinstance(_C1, str),
       drive(lambda: json.loads(Path(_REC_F).read_text())["campaign_id"]),
       os.path.exists(os.path.join(_CP_F, _runner.CHECKPOINT_FILENAME))),
      (True, _C1, False))
_f2, _f2d = entry_point("billed_then_zero", "--fresh", db=_DB_F, cp=_CP_F,
                        corpus=_CORP_F, cap=100.0)
if _f2d is None:
    print(tail(_f2, 60))
_f2text = _f2.stdout + _f2.stderr
check("6u-i PREFLIGHT: the REAL entry point ran with the stand-ins against this "
      "tree -- --fresh parsed, the patient stand-in dumped, the runner imported "
      "from the repository under test",
      ("[--fresh] Discarding the batch checkpoint" in _f2text, _f2d is not None,
       str(at(_f2d, "runner_file")).startswith(os.path.realpath(_REPO))),
      (True, True, True))
_C2 = at(_f2d, "campaign_id")
check("6v *** --fresh after a zero-success run starts a NEW billing campaign "
      "***", (isinstance(_C2, str), _C2 != _C1), (True, True))
_f3, _f3d = entry_point("observe", db=_DB_F, cp=_CP_F, corpus=_CORP_F,
                        cap=100.0)
if _f3d is None:
    print(tail(_f3, 60))
check("6w ...and the next run with no flag continues the FRESH campaign, not "
      "the discarded one", at(_f3d, "campaign_id"), _C2)
_runs_f, _camps_f, _rsum_f = surface(_DB_F)
_ids_f = [r[0] for r in _runs_f]
check("6x the run rows: resumed 0, 0, 0 (no checkpoint in any of them) and "
      "billing campaigns C1, C2, C2; every run finalized",
      ([(r[1], r[2]) for r in _runs_f],
       sorted({r[3] for r in _runs_f} - {"FAILED", "FINISHED"})),
      ([(0, _C1), (0, _C2), (0, _C2)], []))
check("6y *** the SUMMARY SURFACE shows the two budgets as two campaigns: "
      "run 1 alone, and the fresh run stitched to its no-flag restart ***",
      campaign_rows(_camps_f),
      sorted([(f"{at(_ids_f, 0)}", _C1),
              (f"{at(_ids_f, 1)} -> {at(_ids_f, 2)}", _C2)]))
check("6y-i ...and the grouping duplicates and omits no run, billing row or "
      "charge", grouping_faults(_DB_F, _runs_f, _camps_f), [])
check("6y-ii non-degeneracy: BOTH campaigns billed, so the grouping check "
      "compared two sets of charges",
      sorted({r["campaign_id"] for r in billing_rows(_DB_F)}),
      sorted([_C1, _C2]))
_c2_rows = billing_rows(_DB_F, "campaign_id = ?", (_C2,))
check("6z *** the budget the no-flag run was seeded with is exactly the "
      "campaign the summary groups it into: C2's rows, one prior run -- not "
      "C1's, and not both ***",
      (near(at(at(_f3d, "seed"), "usd"),
            sum(r["settled_usd"] or r["reserved_usd"] for r in _c2_rows), 1e-9),
       at(at(_f3d, "seed"), "rows"), at(at(_f3d, "seed"), "runs")),
      (True, len(_c2_rows), 1))


#------------------------------------------------------------------------------

section("SECTION 7 -- isolation held")

_pr.full_jitter_delay = _JITTER_START
_paths._RESOLVED.clear()
_paths._RESOLVED.update(_RESOLVED_START)
check("7a the production inferences.db is byte-unchanged",
      digest(_PROD_DB), _PROD_DIGEST_BEFORE)
check("7b every config knob this file set is restored",
      {k: getattr(config, k) for k in _CONFIG_KEYS if k != "MATCHING_PROVIDER"},
      {k: v for k, v in _CONFIG_START.items() if k != "MATCHING_PROVIDER"})
check("7c no billing sink is left installed",
      _spend.BILLING_RECORD.installed_sink(), None)
check("7d the spend policy is restored", _spend.policy(), _POLICY_START)
shutil.rmtree(_TMP, ignore_errors=True)
check("7e the scratch tree is gone", os.path.exists(_TMP), False)
_who, _prev, _restored = _provider_pin.release_openai_arm()
check("7f the provider pin is released and both tables restored", _restored,
      True)

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
Created on Sun Sep 13 2026

@author: ramyalsaffar
"""
