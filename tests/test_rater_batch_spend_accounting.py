"""The rater's per-batch spend record, and the output ceiling on each rating.

TWO SAFEGUARDS, EACH VERIFIED AT THE SURFACE ITS CONSUMER MEETS.

1. **THE JOURNAL WRITE'S OUTCOME IS CONSUMED.** ``record_batch_spend`` returned
   ``spend_journal.record_batch``'s bool and both call sites in
   ``rater.main()`` discarded it, so a batch whose journal write failed looked
   like one recorded, nothing retried it, and the session could buy a retry
   batch while every later session's cumulative cap was missing the money.
   Sections 1-2 drive ``record_batch_with_outcome`` and
   ``BatchSpendAccounting`` at the JOURNAL LINE; section 3 drives the REAL
   ``main()`` and reads the PRINTED refusal, the MANIFEST and ``ratings.json``.

2. **EACH RATING CARRIES THE CEILING IT WAS PRODUCED UNDER**, read from the
   SUBMITTED REQUEST BODY -- the uploaded bytes, or the provider's input file
   for a resumed batch -- never inferred from the retry flag. Section 4 reads
   ``ratings.json``: a truncation retried at double, an api_error retried at the
   ORIGINAL ceiling (which a "retry means doubled" inference gets wrong), and a
   resumed retry batch read back from its input file.

NO NETWORK, NO KEYS, NO SPEND. The client is a stand-in that serves canned
batch output and never opens a socket; ``require_client``,
``model_is_visible``, ``poll_batch`` and ``_paced_management`` are rebound for
the drive and restored BY IDENTITY. Every journal, state file, run directory
and output directory is inside a ``tempfile.mkdtemp`` this file removes and
asserts gone; ``ONCOTRIAGE_SPEND_JOURNAL`` points inside it and
``paths._RESOLVED['testing_evaluation_path']`` is seeded to it, so neither the
journal nor the state-file migration can reach the production tree -- whose
journal is sha256-compared at the end. EXECS NOTHING. NOT in the collision
matrix. Bucket A.

    python tests/test_rater_batch_spend_accounting.py
"""

import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import types

os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CODE_DIR = os.path.dirname(_TESTS_DIR)
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

TMP = tempfile.mkdtemp(prefix="oncotriage-rater-accounting-")
_ENV_BEFORE = os.environ.get("ONCOTRIAGE_SPEND_JOURNAL")

from oncotriage import paths, spend, spend_journal as J             # noqa: E402

os.environ.pop("ONCOTRIAGE_SPEND_JOURNAL", None)
_PROD_JOURNAL = J.resolved_journal_path()
_PATHS_BEFORE = dict(paths._RESOLVED)
_STATE_ROOT = os.path.join(TMP, "evaluation_runs")
os.makedirs(_STATE_ROOT)
paths._RESOLVED["testing_evaluation_path"] = _STATE_ROOT
os.environ["ONCOTRIAGE_SPEND_JOURNAL"] = os.path.join(TMP, "default.jsonl")

from oncotriage.evaluation import rater as R                        # noqa: E402

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label, actual, expected):
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(label)
        print(f"  FAIL  {label}")
        print(f"          expected: {expected!r}")
        print(f"          actual:   {actual!r}")


def section(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


def drive(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except BaseException as exc:                                # noqa: BLE001
        return "<RAISED %s: %s>" % (type(exc).__name__, str(exc)[:200])


def sha256_or_absent(path):
    if not path or not os.path.isfile(path):
        return "<absent>"
    with io.open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def at(obj, *keys):
    """``obj[k1][k2]...`` or a named absence. NEVER RAISES."""
    for k in keys:
        try:
            obj = obj[k]
        except (KeyError, IndexError, TypeError):
            return f"<no {k!r}>"
    return obj


def as_list(value):
    """``value`` when it is a list, else ``[]``. NEVER RAISES.

    A named absence from ``at`` is a STRING, so ``at(...) or []`` iterates its
    characters -- which aborted this file, on exactly the defect the check
    exists to catch, the first time the revert matrix removed a field."""
    return value if isinstance(value, list) else []


_PROD_BEFORE = sha256_or_absent(_PROD_JOURNAL)
_N = {"i": 0}


def fresh():
    _N["i"] += 1
    return os.path.join(TMP, f"journal_{_N['i']}.jsonl")


def unwritable(name):
    parent = os.path.join(TMP, name)
    with io.open(parent, "w") as fh:
        fh.write("not a directory")
    return parent, os.path.join(parent, "journal.jsonl")


_READONLY_DIRS = []


def readable_unwritable(name):
    """A journal path that can be READ (absent -> nothing recorded yet) and not
    WRITTEN: its directory exists and is read-only.

    ``unwritable`` above puts a FILE where the directory should be, so the
    journal cannot even be OPENED -- which, since unreadable records refuse new
    spend, stops a session before its first batch. The accounting scenarios
    need the other failure: a record every later session can read, and this
    session cannot add to."""
    parent = os.path.join(TMP, name)
    os.makedirs(parent)
    os.chmod(parent, 0o555)
    _READONLY_DIRS.append(parent)
    return parent, os.path.join(parent, "journal.jsonl")


def journal_lines(path):
    if not os.path.isfile(path):
        return []
    with io.open(path, "rb") as fh:
        return [json.loads(ln) for ln in fh.read().splitlines() if ln.strip()]


_B, _S = spend.SPEND_BUDGET_RATER, spend.SPEND_SOURCE_RATER


def record(path, bid="batch_1", usd=0.25, **kw):
    kw.setdefault("sleep", lambda _s: None)
    return drive(J.record_batch_with_outcome, _B, _S, "/state.json", bid, usd,
                 "judge", path=path, **kw)


section("1. record_batch_with_outcome: FOUR ANSWERS, AT THE JOURNAL LINE")

_p1 = fresh()
_r = record(_p1)
check("1a  a first write is CONFIRMED after one attempt",
      (at(_r, "outcome"), at(_r, "attempts")), (J.BATCH_RECORD_CONFIRMED, 1))
check("1b  ...and the journal holds exactly that charge",
      [(e["unit"], e["usd"]) for e in journal_lines(_p1)], [("batch_1", 0.25)])
check("1c  the same batch again is a DUPLICATE and writes no second line -- "
      "the --resume re-collection case",
      (at(record(_p1), "outcome"), len(journal_lines(_p1))),
      (J.BATCH_RECORD_DUPLICATE, 1))
_rc = record(_p1, usd=0.99)
check("1d  the same batch id with a DIFFERENT amount is CONFLICTED, and it is "
      "NOT retried: a collision is a fact about the file",
      (at(_rc, "outcome"), at(_rc, "attempts"), len(journal_lines(_p1))),
      (J.BATCH_RECORD_CONFLICTED, 1, 1))

_parent_u, _pu = unwritable("unwritable_1e")
_pauses = []
_ru = record(_pu, sleep=_pauses.append)
check("1e  a journal that refuses every write is UNCONFIRMED after the "
      "BOUNDED number of attempts",
      (at(_ru, "outcome"), at(_ru, "attempts")),
      (J.BATCH_RECORD_UNCONFIRMED, J.BATCH_RECORD_MAX_ATTEMPTS))
check("1f  ...with the writer's own outcome recorded per attempt, not a bool",
      at(_ru, "append_outcomes"), [J.APPEND_FAILED] * J.BATCH_RECORD_MAX_ATTEMPTS)
check("1g  ...and the pauses between attempts are the documented doubling",
      _pauses, [J.BATCH_RECORD_RETRY_SECONDS * (2 ** n)
                for n in range(J.BATCH_RECORD_MAX_ATTEMPTS - 1)])

_real_append = J.append_with_outcome


def _scripted(outcomes, then_delegate=True):
    """An append that answers the listed outcomes first. For `uncertain` it
    WRITES the line and then reports uncertainty -- the dangerous shape."""
    script = list(outcomes)

    def _append(entry, path=None):
        if script:
            got = script.pop(0)
            if got == J.APPEND_UNCERTAIN:
                _real_append(entry, path=path)
            return got
        return _real_append(entry, path=path) if then_delegate \
            else J.APPEND_FAILED
    return _append


_pt = fresh()
J.append_with_outcome = _scripted([J.APPEND_FAILED])
try:
    _rt = record(_pt)
finally:
    J.append_with_outcome = _real_append
check("1h  a TRANSIENT failure is retried and confirms on the second attempt",
      (at(_rt, "outcome"), at(_rt, "append_outcomes")),
      (J.BATCH_RECORD_CONFIRMED, [J.APPEND_FAILED, J.APPEND_WROTE]))
_pl = fresh()
J.append_with_outcome = _scripted([J.APPEND_UNCERTAIN])
try:
    _rl = record(_pl)
finally:
    J.append_with_outcome = _real_append
check("1i  an UNCERTAIN write that DID land is retried under the same id and "
      "resolves as a DUPLICATE -- the money recorded once, not twice",
      (at(_rl, "outcome"), len(journal_lines(_pl)),
       sum(e["usd"] for e in journal_lines(_pl))),
      (J.BATCH_RECORD_DUPLICATE, 1, 0.25))


def _raiser(entry, path=None):
    raise RuntimeError("the writer broke its contract")


J.append_with_outcome = _raiser
try:
    _rr = record(fresh())
finally:
    J.append_with_outcome = _real_append
check("1j  NEVER RAISES: a writer that raises is read as UNCONFIRMED, bounded",
      (isinstance(_rr, dict), at(_rr, "outcome"), at(_rr, "attempts")),
      (True, J.BATCH_RECORD_UNCONFIRMED, J.BATCH_RECORD_MAX_ATTEMPTS))
check("1k  record_batch keeps its bool for back-compatibility",
      J.record_batch(_B, _S, "/s", "b_compat", 0.1, "j", path=fresh()), True)


section("2. BatchSpendAccounting AND THE SUBMISSION GATE")


class _SubmitStub(object):
    def __init__(self):
        outer = self
        self.uploads = []

        class _Files(object):
            def create(self, file, purpose):
                outer.uploads.append(file[0])
                return types.SimpleNamespace(id="file-%d" % len(outer.uploads))

        class _Batches(object):
            def create(self, **kw):
                return types.SimpleNamespace(
                    id="batch-stub-%d" % len(outer.uploads),
                    input_file_id=kw.get("input_file_id"))

        self.files = _Files()
        self.batches = _Batches()


_REAL = {"out": R.console.out, "paced": R._paced_management,
         "client": R.require_client, "visible": R.model_is_visible,
         "poll": R.poll_batch}
_OUT = []


def _capture_on():
    _OUT[:] = []
    R.console.out = lambda *a, **k: _OUT.append(" ".join(str(x) for x in a))


def _capture_off():
    R.console.out = _REAL["out"]


R._paced_management = lambda call, label, *, max_attempts: call()

_parent2, _p2 = unwritable("unwritable_2")
_acct = R.BatchSpendAccounting("/state.json", "judge", journal=_p2,
                               sleep=lambda _s: None)
_capture_on()
try:
    _o2 = drive(_acct.record, "batch_A", 0.40)
finally:
    _capture_off()
check("2a  an unwritable journal leaves the batch UNCONFIRMED",
      _o2, J.BATCH_RECORD_UNCONFIRMED)
check("2b  ...and the operator is TOLD, at the moment it happens",
      any("NOT CONFIRMED in the journal" in ln for ln in _OUT), True)
_reason = _acct.refusal_reason()
check("2c  the refusal names its code, the batch, the amount and the resume "
      "remedy", (isinstance(_reason, str)
                 and R.SPEND_ACCOUNTING_UNRESOLVED in _reason
                 and "batch_A=unconfirmed" in _reason
                 and "$0.400000" in _reason and "--resume" in _reason), True)
_stub2 = _SubmitStub()
_state2 = os.path.join(TMP, "state_2.json")
_refused = drive(R.submit_batches, _stub2,
                 [[{"custom_id": "c0", "params": {"model": "m"}}]], {},
                 _state2, "retry", accounting=_acct)
check("2d  submit_batches REFUSES the paid submission by name",
      str(_refused).startswith("<RAISED SpendAccountingUnresolved"), True)
check("2e  ...before anything was uploaded", _stub2.uploads, [])

# RECOVERY: the journal becomes writable, reconcile offers the same charge again.
os.remove(_parent2)
os.makedirs(_parent2, exist_ok=True)
check("2f  reconcile settles the batch once the journal accepts writes",
      (_acct.reconcile(), _acct.refusal_reason()), (1, None))
check("2g  ...with the money on disk once",
      [(e["unit"], e["usd"]) for e in journal_lines(_p2)], [("batch_A", 0.40)])
_ceil2 = {}
_ok2 = drive(R.submit_batches, _stub2,
             [[{"custom_id": "c0", "params": {"model": "m",
                                              "max_completion_tokens": 777}}]],
             {}, _state2, "retry", accounting=_acct, ceilings=_ceil2)
check("2h  ...and the gate then admits the submission", (_ok2, _stub2.uploads),
      (["batch-stub-1"], ["oncotriage_rater_retry_1.jsonl"]))
check("2i  submit_batches records the ceiling parsed from the UPLOADED bytes",
      (at(_ceil2, "batch-stub-1", "source"),
       at(_ceil2, "batch-stub-1", "by_custom_id")),
      (R.CEILING_SOURCE_SUBMITTED_PAYLOAD, {"c0": 777}))

_p2c = fresh()
J.record_batch(_B, _S, "/state.json", "batch_C", 0.10, "judge", path=_p2c)
_acct_c = R.BatchSpendAccounting("/state.json", "judge", journal=_p2c,
                                 sleep=lambda _s: None)
_capture_on()
try:
    drive(_acct_c.record, "batch_C", 0.20)
    _attempts_before = _acct_c.records["batch_C"]["attempts"]
    _acct_c.reconcile()
finally:
    _capture_off()
check("2j  a CONFLICTED batch is never retried by reconcile and never clears",
      (_acct_c.records["batch_C"]["outcome"],
       _acct_c.records["batch_C"]["attempts"] == _attempts_before,
       _acct_c.refusal_reason() is not None),
      (J.BATCH_RECORD_CONFLICTED, True, True))
check("2k  the manifest block counts every outcome, including the zeros",
      at(_acct_c.manifest_block(), "outcomes"),
      {J.BATCH_RECORD_CONFIRMED: 0, J.BATCH_RECORD_DUPLICATE: 0,
       J.BATCH_RECORD_CONFLICTED: 1, J.BATCH_RECORD_UNCONFIRMED: 0})


section("3/4. THE REAL main(): THE REFUSAL, THE MANIFEST AND ratings.json")

BLIND_OK = ('{"assigned_status":"not_evaluable",'
            '"patient_value_support":"supported",'
            '"rationale":"the record states it"}')
_USAGE = {"prompt_tokens": 1200, "completion_tokens": 90, "total_tokens": 1290,
          "prompt_tokens_details": {"cached_tokens": 0},
          "completion_tokens_details": {"reasoning_tokens": 0}}


def _line_ok(text=BLIND_OK, stop="stop"):
    return {"response": {"status_code": 200, "request_id": "r",
                         "body": {"id": "c", "object": "chat.completion",
                                  "model": R.DEFAULT_MODEL, "usage": _USAGE,
                                  "choices": [{"index": 0,
                                               "finish_reason": stop,
                                               "message": {
                                                   "role": "assistant",
                                                   "content": text,
                                                   "refusal": None}}]}},
            "error": None}


def _line_error():
    return {"response": {"status_code": 500, "request_id": "r",
                         "body": {"error": {"type": "server_error",
                                            "message": "x"}}},
            "error": {"code": "server_error", "message": "x"}}


class _BatchStub(object):
    """files.create / batches.create / batches.retrieve / files.content.

    The FIRST batch answers one request truncated and one with a server error;
    every later batch answers everything. The output is rendered from the bytes
    that were actually uploaded for that batch, so a ceiling in a rating can
    only have come from those bytes.
    """

    def __init__(self):
        outer = self
        self.uploads = {}        # file id -> bytes
        self.input_for = {}      # batch id -> input file id
        self.reads = []
        self.special = {}
        self.fail = set()        # batch ids that end `failed`, with no output
        self.unreadable_inputs = set()   # batch ids whose input file 500s

        class _Files(object):
            def create(self, file, purpose):
                fid = "file-%d" % (len(outer.uploads) + 1)
                outer.uploads[fid] = file[1]
                return types.SimpleNamespace(id=fid)

            def content(self, file_id):
                outer.reads.append(file_id)
                if file_id.startswith("out-"):
                    return outer.render(file_id[4:])
                if any(outer.input_for.get(b) == file_id
                       for b in outer.unreadable_inputs):
                    raise RuntimeError("stub: input file unavailable")
                return outer.uploads[file_id].decode("utf-8")

        class _Batches(object):
            def create(self, input_file_id, **kw):
                bid = "batch-%d" % (len(outer.input_for) + 1)
                outer.input_for[bid] = input_file_id
                return types.SimpleNamespace(id=bid,
                                             input_file_id=input_file_id)

            def retrieve(self, batch_id):
                failed = batch_id in outer.fail
                return types.SimpleNamespace(
                    id=batch_id, status="failed" if failed else "completed",
                    output_file_id=None if failed else "out-" + batch_id,
                    error_file_id=None,
                    input_file_id=outer.input_for.get(batch_id),
                    request_counts=types.SimpleNamespace(
                        total=1, completed=1, failed=0))

        self.files = _Files()
        self.batches = _Batches()

    def cids(self, bid):
        blob = self.uploads[self.input_for[bid]].decode("utf-8")
        return [json.loads(ln) for ln in blob.splitlines() if ln.strip()]

    def render(self, bid):
        lines = self.cids(bid)
        first = bid == "batch-1"
        if first and not self.special:
            ids = sorted(ln["custom_id"] for ln in lines)
            self.special = {ids[0]: "truncate", ids[1]: "error"}
        out = []
        for ln in lines:
            kind = self.special.get(ln["custom_id"]) if first else None
            row = (_line_ok(BLIND_OK[:20], stop="length") if kind == "truncate"
                   else _line_error() if kind == "error" else _line_ok())
            row["custom_id"] = ln["custom_id"]
            out.append(json.dumps(row) + "\n")
        return "".join(out)


def _make_run_dir(root):
    run = os.path.join(root, "eval_run_sb")
    os.makedirs(run)
    with io.open(os.path.join(run, "p0.json"), "w", encoding="utf-8") as fh:
        json.dump({"patient_summary": {"text": "Patient: PT-sb\nAge: 61 years\n"},
                   "verdicts": [{"nct_id": "NCT09000001",
                                 "verdict_group": "matches",
                                 "inclusion_criteria": [
                                     {"criterion": "Confirmed carcinoma",
                                      "status": "met",
                                      "patient_value": "adenocarcinoma"},
                                     {"criterion": "Adequate organ function",
                                      "status": "met",
                                      "patient_value": "normal labs"}],
                                 "exclusion_criteria": [
                                     # `not_violated`, NOT `absent`: the
                                     # first draft used `absent`, which is no
                                     # exclusion status, so the run held two
                                     # decisions and 4b's non-degeneracy check
                                     # is what reported it.
                                     {"criterion": "Prior therapy",
                                      "status": "not_violated",
                                      "patient_value": "none"}]}]}, fh)
    with io.open(os.path.join(run, "manifest.json"), "w",
                 encoding="utf-8") as fh:
        json.dump({"runs": {"pat-sb": {"file": "p0.json"}}}, fh)
    return run


def run_main(stub, argv, journal):
    os.environ["ONCOTRIAGE_SPEND_JOURNAL"] = journal
    R.require_client = lambda: (stub, "stub-key-source")
    R.model_is_visible = lambda client, model: (True, model)
    R.poll_batch = lambda *a, **k: None
    _capture_on()
    try:
        rc = drive(R.main, argv)
    finally:
        _capture_off()
        R.require_client = _REAL["client"]
        R.model_is_visible = _REAL["visible"]
        R.poll_batch = _REAL["poll"]
    return rc, "\n".join(_OUT)


def read_json(path):
    try:
        with io.open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def rows_by_cid(out_dir):
    return {r["custom_id"]: r
            for r in at(read_json(os.path.join(out_dir, "ratings.json")),
                        "ratings") if isinstance(r, dict)} \
        if isinstance(at(read_json(os.path.join(out_dir, "ratings.json")),
                         "ratings"), list) else {}


_RUN = _make_run_dir(TMP)

# ── 3A. CLEAN: the journal accepts writes ────────────────────────────────
_out_a = os.path.join(TMP, "out_a")
_stub_a = _BatchStub()
_j_a = fresh()
_rc_a, _text_a = run_main(_stub_a, ["--blind", "--run-dir", _RUN,
                                    "--output-dir", _out_a, "--submit"], _j_a)
_man_a = read_json(os.path.join(_out_a, "rater_manifest.json"))
check("3a  CLEAN CONTROL: main() completes with every decision rated",
      _rc_a, 0)
check("3b  ...a retry batch WAS bought (primary + retry)",
      sorted(_stub_a.input_for), ["batch-1", "batch-2"])
check("3c  ...the manifest records both batches CONFIRMED and no refusal",
      (at(_man_a, "cost", "spend_journal", "outcomes",
          J.BATCH_RECORD_CONFIRMED),
       at(_man_a, "cost", "spend_journal", "unresolved_usd"),
       at(_man_a, "retry_pass_refused")), (2, 0.0, None))
check("3d  ...and the journal line for each batch is on disk",
      sorted(e["unit"] for e in journal_lines(_j_a)), ["batch-1", "batch-2"])

# ── 3B. REFUSED: the journal refuses every write ─────────────────────────
_out_b = os.path.join(TMP, "out_b")
_stub_b = _BatchStub()
_, _j_b = readable_unwritable("unwritable_3b")
_rc_b, _text_b = run_main(_stub_b, ["--blind", "--run-dir", _RUN,
                                    "--output-dir", _out_b, "--submit"], _j_b)
_man_b = read_json(os.path.join(_out_b, "rater_manifest.json"))
_rows_b = rows_by_cid(_out_b)
check("3e  with the journal refusing, NO NEW PAID BATCH is bought: only the "
      "primary batch exists", sorted(_stub_b.input_for), ["batch-1"])
check("3f  ...the operator reads the refusal, by name, on the console",
      ("REFUSED NEW PAID SUBMISSION" in _text_b
       and R.SPEND_ACCOUNTING_UNRESOLVED in _text_b), True)
check("3g  ...and the closing block says the spend is NOT confirmed",
      "NOT CONFIRMED IN THE JOURNAL" in _text_b, True)
check("3h  ...the manifest records the refusal and the unconfirmed batch",
      (str(at(_man_b, "retry_pass_refused")).startswith(
          R.SPEND_ACCOUNTING_UNRESOLVED),
       at(_man_b, "cost", "spend_journal", "outcomes",
          J.BATCH_RECORD_UNCONFIRMED),
       at(_man_b, "cost", "spend_journal", "unresolved_batch_ids")),
      (True, 1, ["batch-1"]))
check("3i  ALREADY-SUBMITTED RESULTS ARE PRESERVED: ratings.json is written, "
      "with the good decision rated and the two retryable ones left unrated",
      (len(_rows_b), sorted((r["rated"], r["unrated_reason"])
                            for r in _rows_b.values())),
      (3, sorted([(True, None), (False, "api_error"),
                  (False, "truncated_max_tokens")])))
check("3j  ...and the session exits 3 (unrated remain), not 1 (refused)",
      _rc_b, 3)

# ── 3C. TRANSIENT: fails through the primary record, then recovers ───────
_out_c = os.path.join(TMP, "out_c")
_stub_c = _BatchStub()
_j_c = fresh()
J.append_with_outcome = _scripted([J.APPEND_FAILED]
                                  * J.BATCH_RECORD_MAX_ATTEMPTS)
try:
    _rc_c, _text_c = run_main(_stub_c, ["--blind", "--run-dir", _RUN,
                                        "--output-dir", _out_c, "--submit"],
                              _j_c)
finally:
    J.append_with_outcome = _real_append
_man_c = read_json(os.path.join(_out_c, "rater_manifest.json"))
check("3k  a batch unconfirmed at collection is RECONCILED before the retry "
      "pass, so a transient fault does not cost the retry",
      (sorted(_stub_c.input_for), at(_man_c, "retry_pass_refused")),
      (["batch-1", "batch-2"], None))
_b1 = [b for b in at(_man_c, "cost", "spend_journal", "batches")
       if isinstance(b, dict) and b.get("batch_id") == "batch-1"]
check("3l  ...and the manifest shows the evidence: the failed attempts, then "
      "the confirming one", (len(_b1), at(_b1, 0, "outcome"),
                              at(_b1, 0, "append_outcomes")),
      (1, J.BATCH_RECORD_CONFIRMED,
       [J.APPEND_FAILED] * J.BATCH_RECORD_MAX_ATTEMPTS + [J.APPEND_WROTE]))

# ── 4. THE CEILING PER RATING, IN ratings.json ───────────────────────────
_rows_a = rows_by_cid(_out_a)
_primary_ceiling = at(_stub_a.cids("batch-1"), 0, "body",
                      "max_completion_tokens")
check("4a  non-degeneracy: the primary body carries an integer ceiling",
      isinstance(_primary_ceiling, int) and _primary_ceiling > 0, True)
_trunc = [c for c, k in _stub_a.special.items() if k == "truncate"]
_err = [c for c, k in _stub_a.special.items() if k == "error"]
_plain = sorted(set(_rows_a) - set(_trunc) - set(_err))
check("4b  non-degeneracy: one truncated, one api_error and one plain decision",
      (len(_trunc), len(_err), len(_plain)), (1, 1, 1))
check("4c  the TRUNCATED decision, retried, carries the doubled ceiling READ "
      "FROM THE RETRY BATCH'S SUBMITTED BODY",
      (at(_rows_a, _trunc[0] if _trunc else "", "retry"),
       at(_rows_a, _trunc[0] if _trunc else "", "max_completion_tokens"),
       at(_rows_a, _trunc[0] if _trunc else "",
          "max_completion_tokens_source")),
      (True, 2 * _primary_ceiling if isinstance(_primary_ceiling, int)
       else None, R.CEILING_SOURCE_SUBMITTED_PAYLOAD))
check("4d  the API-ERROR decision, ALSO retried, carries the ORIGINAL ceiling "
      "-- the case a 'retry means doubled' inference reports wrongly",
      (at(_rows_a, _err[0] if _err else "", "retry"),
       at(_rows_a, _err[0] if _err else "", "max_completion_tokens")),
      (True, _primary_ceiling))
check("4e  the plain decision carries the primary ceiling, not retried",
      (at(_rows_a, _plain[0] if _plain else "", "retry"),
       at(_rows_a, _plain[0] if _plain else "", "max_completion_tokens")),
      (False, _primary_ceiling))
check("4f  the manifest counts the per-rating ceilings from the same rows",
      at(_man_a, "request", "max_completion_tokens_by_rating"),
      {str(_primary_ceiling): 2, str(2 * _primary_ceiling): 1}
      if isinstance(_primary_ceiling, int) else "<n/a>")
check("4g  the unrated rows of the refused session carry THEIR ceilings too",
      sorted((r["unrated_reason"], r["max_completion_tokens"],
              r["max_completion_tokens_source"])
             for r in _rows_b.values() if not r["rated"]),
      sorted([("api_error", _primary_ceiling,
               R.CEILING_SOURCE_SUBMITTED_PAYLOAD),
              ("truncated_max_tokens", _primary_ceiling,
               R.CEILING_SOURCE_SUBMITTED_PAYLOAD)]))
check("4h  every row's source is a member of the closed vocabulary",
      all(r.get("max_completion_tokens_source") in R.CEILING_SOURCES
          for rows in (_rows_a, _rows_b) for r in rows.values()), True)

# ── 4i. A RESUMED RETRY: a new session collects the retry batch only ─────
_reads_before = list(_stub_a.reads)
_rc_r, _text_r = run_main(_stub_a, ["--blind", "--run-dir", _RUN,
                                    "--output-dir", _out_a, "--resume",
                                    "batch-2", "--no-retry"], _j_a)
_rows_r = rows_by_cid(_out_a)
_man_r = read_json(os.path.join(_out_a, "rater_manifest.json"))
check("4i  non-degeneracy: the resumed session READ THE PROVIDER'S INPUT FILE "
      "for the batch it did not submit",
      _stub_a.input_for.get("batch-2") in _stub_a.reads[len(_reads_before):],
      True)
check("4j  the resumed TRUNCATION retry carries the doubled ceiling, read from "
      "the provider's input file",
      (at(_rows_r, _trunc[0] if _trunc else "", "max_completion_tokens"),
       at(_rows_r, _trunc[0] if _trunc else "",
          "max_completion_tokens_source")),
      (2 * _primary_ceiling if isinstance(_primary_ceiling, int) else None,
       R.CEILING_SOURCE_PROVIDER_INPUT_FILE))
check("4k  the resumed api_error retry carries the original ceiling, same source",
      (at(_rows_r, _err[0] if _err else "", "max_completion_tokens"),
       at(_rows_r, _err[0] if _err else "", "max_completion_tokens_source")),
      (_primary_ceiling, R.CEILING_SOURCE_PROVIDER_INPUT_FILE))
check("4l  a decision the resumed batch did not carry has NO ceiling and says "
      "why, rather than one inferred",
      (at(_rows_r, _plain[0] if _plain else "", "max_completion_tokens"),
       at(_rows_r, _plain[0] if _plain else "",
          "max_completion_tokens_source")),
      (None, R.CEILING_ABSENT_NO_BATCH_RESULT))
check("4m  the resume re-offers batch-2's charge and the journal answers "
      "DUPLICATE -- idempotent on (state file, batch id)",
      (at(_man_r, "cost", "spend_journal", "outcomes",
          J.BATCH_RECORD_DUPLICATE),
       sorted(e["unit"] for e in journal_lines(_j_a))),
      (1, ["batch-1", "batch-2"]))

# ── 4n. THE PARSER, DIRECTLY ─────────────────────────────────────────────
_parsed = R.submitted_ceilings_from_jsonl(
    '{"custom_id":"a","body":{"max_completion_tokens":4096}}\n'
    '{"custom_id":"b","body":{"max_completion_tokens":true}}\n'
    '{"custom_id":"c","body":{}}\n'
    '{"custom_id":"d","body":{"max_completion_tokens":10}}\n'
    '{"custom_id":"d","body":{"max_completion_tokens":20}}\n'
    'not json\n', R.CEILING_SOURCE_PROVIDER_INPUT_FILE)
check("4n  a bool, a missing field and a duplicated id are all ABSENT, never a "
      "number", [R.ceiling_for(_parsed, c) for c in "abcde"],
      [(4096, R.CEILING_SOURCE_PROVIDER_INPUT_FILE),
       (None, R.CEILING_ABSENT_MALFORMED), (None, R.CEILING_ABSENT_MALFORMED),
       (None, R.CEILING_ABSENT_MALFORMED), (None, R.CEILING_ABSENT_NOT_IN_BODY)])
check("4o  an undecodable input file is unreadable, not empty",
      R.ceiling_for(R.submitted_ceilings_from_jsonl(b"\xff\xfe", "x"), "a"),
      (None, R.CEILING_ABSENT_INPUT_FILE_UNREADABLE))


section("6. A READABLE STATE FILE WITH A MALFORMED BATCH LIST (P3)")


def refusal_code(fn, *a, **kw):
    try:
        fn(*a, **kw)
        return "<did not raise>"
    except R.RaterRefusal as exc:
        return exc.code
    except BaseException as exc:                                # noqa: BLE001
        return "<RAISED %s>" % type(exc).__name__


check("6a  an absent, empty or well-formed batch list is no problem",
      [R.state_batches_problem(st) for st in
       ({}, {"batches": None}, {"batches": []}, {"batches": [{"id": "b"}]})],
      [None, None, None, None])
check("6b  every malformed shape is NAMED, never raised",
      [isinstance(R.state_batches_problem(st), str) for st in
       ({"batches": 7}, {"batches": [7]}, {"batches": [None]},
        {"batches": [{"id": 3}]}, {"batches": [{}]}, {"batches": [{"id": ""}]})],
      [True] * 6)
_other = os.path.join(TMP, "other_mode")
os.makedirs(_other)
with io.open(os.path.join(_other, R.state_filename(R.MODE_ANCHORED)), "w",
             encoding="utf-8") as _fh:
    json.dump({"batches": [7, None, {"id": "batch-x"}]}, _fh)
check("6c  the OTHER mode's malformed state file is a NAMED refusal -- it was "
      "an AttributeError traceback out of main()",
      refusal_code(R.refuse_batch_from_other_mode, ["batch-x"], R.MODE_BLIND,
                   _other, None), R.STATE_BATCHES_MALFORMED)

_out_m = os.path.join(TMP, "out_malformed")
os.makedirs(_out_m)
_state_m = read_json(os.path.join(_out_a, R.state_filename(R.MODE_BLIND)))
check("6d  non-degeneracy: the clean session's state file carries a real "
      "batch list to corrupt", isinstance(at(_state_m, "batches"), list)
      and len(_state_m["batches"]) >= 1, True)
if isinstance(_state_m, dict):
    _state_m["batches"] = [7] + list(_state_m.get("batches") or [])
with io.open(os.path.join(_out_m, R.state_filename(R.MODE_BLIND)), "w",
             encoding="utf-8") as _fh:
    json.dump(_state_m, _fh)
_stub_m = _BatchStub()
_rc_m, _text_m = run_main(_stub_m, ["--blind", "--run-dir", _RUN,
                                    "--output-dir", _out_m, "--resume",
                                    "batch-1", "--no-retry"], fresh())
check("6e  main() --resume against it REFUSES by name and exits 1, rather "
      "than escaping as a traceback",
      (_rc_m, "REFUSED" in _text_m and R.STATE_BATCHES_MALFORMED in _text_m),
      (1, True))
check("6f  ...having read nothing from and sent nothing to the provider",
      (_stub_m.reads, sorted(_stub_m.input_for), _stub_m.uploads), ([], [], {}))
_rc_s, _text_s = run_main(_stub_m, ["--blind", "--run-dir", _RUN,
                                    "--output-dir", _out_m, "--submit"],
                          fresh())
check("6g  main() --submit into the same directory refuses too, rather than "
      "OVERWRITING the only record of which paid batches exist",
      (_rc_s, R.STATE_BATCHES_MALFORMED in _text_s, _stub_m.uploads,
       at(read_json(os.path.join(_out_m, R.state_filename(R.MODE_BLIND))),
          "batches", 0)), (1, True, {}, 7))


from oncotriage import config                                     # noqa: E402

_REAL_REQUIRE_BUDGET = spend.require_budget


def state_of(out_dir):
    return read_json(os.path.join(out_dir, R.state_filename(R.MODE_BLIND)))


def journal_total(path):
    return round(J.total(_B, path=path).usd, 9)


section("7. P2 -- A FAILED BATCH AFFECTS ONLY ITS OWN REQUESTS")

# ── 7a-7d  collect_results, directly ────────────────────────────────────
_idx7 = types.SimpleNamespace(by_custom_id={"c1": None, "c2": None,
                                            "c3": None},
                              retest_ids=set(), mode=R.MODE_BLIND)


class _FailedBatchClient(object):
    def __init__(self, input_text=None):
        outer = self
        self.input_text = input_text

        class _B7(object):
            def retrieve(self, batch_id):
                return types.SimpleNamespace(
                    id=batch_id, status="failed", output_file_id=None,
                    error_file_id=None, input_file_id="in-7")

        class _F7(object):
            def content(self, file_id):
                if outer.input_text is None:
                    raise RuntimeError("stub: input file unavailable")
                return outer.input_text

        self.batches = _B7()
        self.files = _F7()


_sub7 =R.submitted_ceilings_from_jsonl(
    '{"custom_id":"c1","body":{"max_completion_tokens":10}}\n'
    '{"custom_id":"c2","body":{"max_completion_tokens":20}}\n',
    R.CEILING_SOURCE_SUBMITTED_PAYLOAD)
_got7 = drive(R.collect_results, _FailedBatchClient(), "batch-f", _idx7, "m",
              submitted_ceilings=_sub7)
check("7a  a FAILED batch marks exactly ITS OWN requests unrated -- not the "
      "third decision in the index it never carried",
      sorted(at(_got7, "unrated")) if isinstance(_got7, dict) else _got7,
      ["c1", "c2"])
check("7b  ...each with its own ceiling, and naming its batch",
      [(at(_got7, "unrated", c, R.CEILING_FIELD),
        at(_got7, "unrated", c, "batch_id")) for c in ("c1", "c2")],
      [(10, "batch-f"), (20, "batch-f")])
_got7u = drive(R.collect_results, _FailedBatchClient(input_text=None),
               "batch-u", _idx7, "m", submitted_ceilings=None)
check("7c  when the failed batch's membership CANNOT be read, it marks nothing "
      "and says so, rather than guessing the whole index",
      (at(_got7u, "unrated"), at(_got7u, "membership_unknown")), ({}, True))
_got7r = drive(R.collect_results, _FailedBatchClient(
    input_text='{"custom_id":"c3","body":{"max_completion_tokens":5}}\n'),
    "batch-r", _idx7, "m", submitted_ceilings=None)
check("7d  ...and a RESUMED failed batch reads its membership from the "
      "provider's input file",
      (sorted(at(_got7r, "unrated")), at(_got7r, "membership_unknown")),
      (["c3"], False))

# ── 7e-7h  the merge, directly ──────────────────────────────────────────
_rated, _unrated, _att, _retried = {}, {}, {}, set()
R.merge_batch_results(_rated, _unrated, _att, {
    "rated": {"c": {"batch_id": "b1", R.CEILING_FIELD: 100,
                    R.CEILING_SOURCE_FIELD: R.CEILING_SOURCE_SUBMITTED_PAYLOAD}},
    "unrated": {"d": {"reason": "truncated_max_tokens", "batch_id": "b1",
                      R.CEILING_FIELD: 100,
                      R.CEILING_SOURCE_FIELD:
                          R.CEILING_SOURCE_SUBMITTED_PAYLOAD}}}, "b1",
    "primary")
_na = R.merge_batch_results(_rated, _unrated, _att, {
    "rated": {},
    "unrated": {c: {"reason": "batch_failed", "batch_id": "b2",
                    R.CEILING_FIELD: 200,
                    R.CEILING_SOURCE_FIELD: R.CEILING_SOURCE_SUBMITTED_PAYLOAD}
                for c in ("c", "d")}}, "b2", "retry", retried=_retried)
check("7e  a LATER unsuccessful attempt never replaces an EARLIER success, "
      "and its ceiling does not restamp it",
      (sorted(_rated), at(_rated, "c", R.CEILING_FIELD), "c" in _unrated),
      (["c"], 100, False))
check("7f  ...the unsuccessful attempt is recorded SEPARATELY, in order",
      [(a["batch_id"], a["outcome"], a["reason"], a[R.CEILING_FIELD])
       for a in _att["c"]],
      [("b1", R.ATTEMPT_RATED, None, 100),
       ("b2", R.ATTEMPT_UNRATED, "batch_failed", 200)])
check("7g  a decision that was never rated takes the LATEST attempt's result "
      "AND that same attempt's ceiling, with the earlier attempt kept",
      (at(_unrated, "d", "reason"), at(_unrated, "d", R.CEILING_FIELD),
       [a["reason"] for a in _att["d"]]),
      ("batch_failed", 200, ["truncated_max_tokens", "batch_failed"]))
check("7h  ...and the merge reports the one attempt it did not adopt",
      (_na, sorted(_retried)), (1, ["c", "d"]))

# ── 7i-7o  THE REAL main(): primary partly unrated, retry batch FAILS ───
_out_7 = os.path.join(TMP, "out_p2")
_stub_7 = _BatchStub()
_stub_7.fail = {"batch-2"}
_rc_7, _text_7 = run_main(_stub_7, ["--blind", "--run-dir", _RUN,
                                    "--output-dir", _out_7, "--submit"],
                          fresh())
_rows_7 = rows_by_cid(_out_7)
_man_7 = read_json(os.path.join(_out_7, "rater_manifest.json"))
_t7 = [c for c, k in _stub_7.special.items() if k == "truncate"]
_e7 = [c for c, k in _stub_7.special.items() if k == "error"]
_p7 = sorted(set(_rows_7) - set(_t7) - set(_e7))
check("7i  non-degeneracy: a primary AND a failed retry batch were bought, "
      "and the three decisions split one plain / one truncated / one error",
      (sorted(_stub_7.input_for), len(_t7), len(_e7), len(_p7)),
      (["batch-1", "batch-2"], 1, 1, 1))
check("7j  THE PLAIN DECISION, rated by the primary, is untouched by the "
      "failed retry batch that never carried it: rated, not retried, one "
      "attempt, primary ceiling",
      (at(_rows_7, _p7[0] if _p7 else "", "rated"),
       at(_rows_7, _p7[0] if _p7 else "", "retry"),
       len(as_list(at(_rows_7, _p7[0] if _p7 else "", "attempts"))),
       at(_rows_7, _p7[0] if _p7 else "", "max_completion_tokens")),
      (True, False, 1, 1536))
check("7k  the manifest counts two unrated decisions, both from the failed "
      "retry, not three",
      (at(_man_7, "counts", "unrated"),
       at(_man_7, "counts", "unrated_by_reason")),
      (2, {"batch_failed": 2}))
check("7l  the TRUNCATED decision's result and ceiling come from ONE attempt "
      "(the failed retry at the doubled ceiling), with both attempts recorded",
      (at(_rows_7, _t7[0] if _t7 else "", "unrated_reason"),
       at(_rows_7, _t7[0] if _t7 else "", "max_completion_tokens"),
       [(a["tag"], a["reason"], a["max_completion_tokens"])
        for a in as_list(at(_rows_7, _t7[0] if _t7 else "", "attempts"))]),
      ("batch_failed", 3072,
       [("primary", "truncated_max_tokens", 1536),
        ("retry", "batch_failed", 3072)]))
check("7m  no row's ceiling source says the request was absent from the batch "
      "that produced it -- the restamp the whole-index marking caused",
      sorted({r.get("max_completion_tokens_source")
              for r in _rows_7.values()}),
      [R.CEILING_SOURCE_SUBMITTED_PAYLOAD])


section("8. P3 -- AN UNREADABLE RECORD REFUSES NEW SPEND, AT THE SURFACE")

_saved_cap = (config.RATER_SPEND_CAP_USD, config.SPEND_CAP_ENFORCED)
check("8-pre non-degeneracy: the rater cap is enforced and set, which is the "
      "only state in which the refusal applies",
      (config.SPEND_CAP_ENFORCED, isinstance(config.RATER_SPEND_CAP_USD,
                                             (int, float))), (True, True))


def torn(path, lines=(b"{this line is torn\n",)):
    with io.open(path, "ab") as fh:
        for ln in lines:
            fh.write(ln)


# ── 8a-8d  --submit against a journal with one unreadable line ──────────
_j8 = fresh()
J.record_batch(_B, _S, "/some/other/state.json", "old-batch", 1.25, "judge",
               path=_j8)
torn(_j8)
_out_8 = os.path.join(TMP, "out_p3_submit")
_stub_8 = _BatchStub()
_rc_8, _text_8 = run_main(_stub_8, ["--blind", "--run-dir", _RUN,
                                    "--output-dir", _out_8, "--submit"], _j8)
check("8a  --submit on a record with an unreadable line is REFUSED (exit 1) "
      "and NOTHING is uploaded", (_rc_8, _stub_8.uploads, _stub_8.input_for),
      (1, {}, {}))
check("8b  ...the operator reads, before any spend, the named refusal, that "
      "the remainder is UNVERIFIED and potentially OVERSTATED, and which item",
      ("REFUSED" in _text_8, spend.SPEND_RECORD_UNVERIFIED in _text_8,
       "UNVERIFIED" in _text_8, "potentially OVERSTATED" in _text_8,
       "journal line 2: not JSON" in _text_8), (True,) * 5)
check("8c  ...and the refusal never calls the figure an upper bound",
      "upper bound" in _text_8.split("REFUSED", 1)[-1].lower(), False)
check("8d  ...and the unreadable line is still on disk, byte for byte",
      open(_j8, "rb").read().endswith(b"{this line is torn\n"), True)

# CLEAN CONTROL, same journal minus the torn line: the submission goes out.
_j8c = fresh()
J.record_batch(_B, _S, "/some/other/state.json", "old-batch", 1.25, "judge",
               path=_j8c)
_out_8c = os.path.join(TMP, "out_p3_submit_clean")
_stub_8c = _BatchStub()
_rc_8c, _ = run_main(_stub_8c, ["--blind", "--run-dir", _RUN,
                                "--output-dir", _out_8c, "--submit",
                                "--no-retry"], _j8c)
check("8e  CLEAN CONTROL: the identical journal without the torn line submits",
      sorted(_stub_8c.input_for), ["batch-1"])

# ── 8f-8i  --resume collects, and the retry pass is refused by name ─────
torn(_j8c)
_rc_8r, _text_8r = run_main(_stub_8c, ["--blind", "--run-dir", _RUN,
                                       "--output-dir", _out_8c, "--resume",
                                       "batch-1"], _j8c)
_man_8r = read_json(os.path.join(_out_8c, "rater_manifest.json"))
check("8f  --resume on the now-unreadable record still COLLECTS what was "
      "submitted: ratings.json holds the rated decision",
      sum(1 for r in rows_by_cid(_out_8c).values() if r.get("rated")), 1)
check("8g  ...and the retry pass it would have bought is REFUSED by name -- "
      "no second batch", (sorted(_stub_8c.input_for),
                          "REFUSED NEW PAID SUBMISSION" in _text_8r,
                          str(at(_man_8r, "retry_pass_refused")).startswith(
                              spend.SPEND_RECORD_UNVERIFIED)),
      (["batch-1"], True, True))
check("8h  ...the manifest records the unverified record and the refusal",
      (at(_man_8r, "cost", "budget_record", "unreadable_items"),
       at(_man_8r, "cost", "budget_record", "verified"),
       str(at(_man_8r, "cost", "budget_record", "refusal")).startswith(
           spend.SPEND_RECORD_UNVERIFIED)), (1, False, True))
check("8i  ...and the session exits 3 (unrated remain), not 1",
      _rc_8r, 3)

# ── 8j-8k  a state file the migration cannot read refuses too ──────────
_bad_state_dir = os.path.join(_STATE_ROOT, "torn_session", "rater")
os.makedirs(_bad_state_dir)
with io.open(os.path.join(_bad_state_dir, R.STATE_FILENAME), "w") as _fh:
    _fh.write("{not json")
_stub_8s = _BatchStub()
_rc_8s, _text_8s = run_main(_stub_8s, ["--blind", "--run-dir", _RUN,
                                       "--output-dir",
                                       os.path.join(TMP, "out_p3_state"),
                                       "--submit"], fresh())
shutil.rmtree(os.path.join(_STATE_ROOT, "torn_session"))
check("8j  a pre-journal state file the migration cannot read makes the "
      "record unverified: --submit is refused and nothing is uploaded",
      (_rc_8s, _stub_8s.uploads), (1, {}))
check("8k  ...naming that state file", "torn_session" in _text_8s, True)


section("9. P4 -- RE-COLLECTION CHARGES NOTHING TWICE, IN ANY RECORD")

_out_9 = os.path.join(TMP, "out_p4")
_j9 = fresh()
_stub_9 = _BatchStub()
_rc_9, _ = run_main(_stub_9, ["--blind", "--run-dir", _RUN, "--output-dir",
                              _out_9, "--submit"], _j9)
_state_9 = state_of(_out_9)
_spent_9 = at(_state_9, R.STATE_SPEND_KEY)
_lines_9 = journal_lines(_j9)
check("9a  non-degeneracy: the session bought and recorded two batches with "
      "real money in all three records",
      (sorted(_stub_9.input_for), len(_lines_9),
       isinstance(_spent_9, float) and _spent_9 > 0,
       sorted(at(_state_9, R.STATE_SPEND_BY_BATCH_KEY) or {})),
      (["batch-1", "batch-2"], 2, True, ["batch-1", "batch-2"]))
check("9b  after the session, the ledger's rater budget equals the journal "
      "total (the one reset-and-seed per session)",
      round(spend.active_spend(spend.SPEND_SOURCE_RATER), 9),
      journal_total(_j9))

_rc_9r, _text_9r = run_main(_stub_9, ["--blind", "--run-dir", _RUN,
                                      "--output-dir", _out_9, "--resume",
                                      "batch-1,batch-2,batch-1", "--no-retry"],
                            _j9)
_man_9r = read_json(os.path.join(_out_9, "rater_manifest.json"))
check("9c  *** RESUMING BOTH BATCHES CHARGES THE CAP'S LEDGER NOTHING: the "
      "rater budget reads the journal total, not twice it ***",
      round(spend.active_spend(spend.SPEND_SOURCE_RATER), 9),
      journal_total(_j9))
check("9d  ...the state file's spend_usd is unchanged",
      at(state_of(_out_9), R.STATE_SPEND_KEY), _spent_9)
check("9e  ...the journal gained no line (its own idempotency, intact)",
      len(journal_lines(_j9)), 2)
check("9f  ...the repeated id was collected once, and both batches report "
      "they were already in the seed",
      (at(_man_9r, "collected_batch_ids"),
       [d.get("disposition") for d in
        as_list(at(_man_9r, "cost", "batch_spend_dispositions"))]),
      (["batch-1", "batch-2"], [R.BATCH_SPEND_IN_SEED] * 2))
check("9f-i  every disposition written is a member of the closed vocabulary "
      "the manifest publishes, and that vocabulary is the module's",
      (all(d.get("disposition") in
           as_list(at(_man_9r, "cost", "batch_spend_disposition_vocabulary"))
           for d in as_list(at(_man_9r, "cost",
                                  "batch_spend_dispositions"))),
       at(_man_9r, "cost", "batch_spend_disposition_vocabulary")),
      (True, list(R.BATCH_SPEND_DISPOSITIONS)))

# ── 9g-9j  money in the state file that the journal never got ──────────
_out_9b = os.path.join(TMP, "out_p4_unrecorded")
_dir_9b, _j9b = readable_unwritable("readonly_9b")
_stub_9b = _BatchStub()
run_main(_stub_9b, ["--blind", "--run-dir", _RUN, "--output-dir", _out_9b,
                    "--submit", "--no-retry"], _j9b)
_spent_9b = at(state_of(_out_9b), R.STATE_SPEND_KEY)
check("9g  non-degeneracy: the batch's money is in the state file and NOT in "
      "the unwritable journal", (isinstance(_spent_9b, float)
                                 and _spent_9b > 0, journal_lines(_j9b)),
      (True, []))
os.chmod(_dir_9b, 0o755)
# ANOTHER SESSION'S ENTRY, so the next seed is read from the journal and
# not from this state file's own total.
J.record_batch(_B, _S, "/elsewhere/state.json", "x-batch", 2.0, "judge",
               path=_j9b)
run_main(_stub_9b, ["--blind", "--run-dir", _RUN, "--output-dir", _out_9b,
                    "--resume", "batch-1", "--no-retry"], _j9b)
_man_9b = read_json(os.path.join(_out_9b, "rater_manifest.json"))
check("9h  the resume CHARGES the ledger for money the journal seed lacked, "
      "exactly once",
      round(spend.active_spend(spend.SPEND_SOURCE_RATER), 9),
      # GUARDED: a session the plant broke leaves no spend to add to, and
      # arithmetic on the named absence would abort the file here.
      round(2.0 + _spent_9b, 9) if isinstance(_spent_9b, float)
      else "<no recorded spend to expect>")
check("9i  ...does not add it to spend_usd a second time, and records it in "
      "the journal at last",
      (at(state_of(_out_9b), R.STATE_SPEND_KEY),
       sorted(e["unit"] for e in journal_lines(_j9b))),
      (_spent_9b, ["batch-1", "x-batch"]))
check("9j  ...with that disposition named in the manifest",
      [d.get("disposition") for d in
       as_list(at(_man_9b, "cost", "batch_spend_dispositions"))],
      [R.BATCH_SPEND_IN_STATE_NOT_SEED])


section("10. P5 -- A BUDGET STOP WRITES WHAT WAS COLLECTED, MARKED INCOMPLETE")


def _stop_at(tag):
    def _gate(source, where, **kw):
        if tag in where:
            raise spend.SpendLimitReached(
                f"the request was not issued: test-imposed limit at {where}",
                limit=spend.SPEND_LIMIT_CAP, source=source)
        return _REAL_REQUIRE_BUDGET(source, where, **kw)
    return _gate


_out_10 = os.path.join(TMP, "out_p5")
_stub_10 = _BatchStub()
spend.require_budget = _stop_at("retry")
try:
    _rc_10, _text_10 = run_main(_stub_10, ["--blind", "--run-dir", _RUN,
                                           "--output-dir", _out_10,
                                           "--submit"], fresh())
finally:
    spend.require_budget = _REAL_REQUIRE_BUDGET
_ratings_10 = read_json(os.path.join(_out_10, "ratings.json"))
_man_10 = read_json(os.path.join(_out_10, "rater_manifest.json"))
_sum_10 = read_json(os.path.join(_out_10, "summary.json"))
check("10a  the retry pass is stopped on budget, the exit code is still 3, "
      "and no retry batch exists",
      (_rc_10, "STOPPED ON BUDGET" in _text_10, sorted(_stub_10.input_for)),
      (3, True, ["batch-1"]))
check("10b  *** ratings.json IS WRITTEN on that exit, with the primary's "
      "collected results: one rated, two unrated ***",
      sorted((r.get("rated"), r.get("unrated_reason"))
             for r in as_list(at(_ratings_10, "ratings"))
             if isinstance(r, dict)),
      sorted([(True, None), (False, "api_error"),
              (False, "truncated_max_tokens")]))
check("10c  ...and all three artifacts say INCOMPLETE, with the stop reason",
      [(at(d, "session_status"), at(d, "incomplete"), at(d, "stop", "reason"))
       for d in (_ratings_10, _man_10, _sum_10)],
      [(R.SESSION_STOPPED_ON_BUDGET, True, "spend_limit_reached")] * 3)
check("10c-i  the status is a member of the closed vocabulary the manifest "
      "publishes", (at(_man_10, "session_status") in
                    as_list(at(_man_10, "session_status_vocabulary")),
                    at(_man_10, "session_status_vocabulary")),
      (True, list(R.SESSION_STATUSES)))
check("10d  ...the stop message the operator saw is the one recorded",
      "test-imposed limit" in str(at(_man_10, "stop", "message")), True)
check("10e  ...the console says the files are incomplete",
      "SESSION INCOMPLETE" in _text_10, True)
check("10f  ...and no temp file was left behind by the atomic writes",
      sorted(n for n in os.listdir(_out_10) if n.endswith(".tmp")), [])
check("10g  CLEAN CONTROL: a completed session's artifacts say COMPLETE",
      (at(read_json(os.path.join(_out_a, "ratings.json")), "session_status"),
       at(read_json(os.path.join(_out_a, "rater_manifest.json")),
          "incomplete")), (R.SESSION_COMPLETE, False))

_out_10n = os.path.join(TMP, "out_p5_nothing")
_stub_10n = _BatchStub()
spend.require_budget = _stop_at("primary")
try:
    _rc_10n, _text_10n = run_main(_stub_10n, ["--blind", "--run-dir", _RUN,
                                              "--output-dir", _out_10n,
                                              "--submit"], fresh())
finally:
    spend.require_budget = _REAL_REQUIRE_BUDGET
check("10h  a stop before anything was collected exits 3 and writes no "
      "ratings file, saying why",
      (_rc_10n, os.path.exists(os.path.join(_out_10n, "ratings.json")),
       "Nothing had been collected" in _text_10n), (3, False, True))

_wj = os.path.join(TMP, "atomic.json")
R.write_json(_wj, {"a": 1})
_before_wj = open(_wj, "rb").read()
_raised_wj = drive(R.write_json, _wj, {"a": object()})
check("10i  a write that fails mid-serialisation leaves the previous file "
      "byte-identical (atomic replacement)",
      (str(_raised_wj).startswith("<RAISED TypeError"),
       open(_wj, "rb").read() == _before_wj), (True, True))


section("5. ISOLATION AND RESTORES")

R._paced_management = _REAL["paced"]
for _d in _READONLY_DIRS:
    if os.path.isdir(_d):
        os.chmod(_d, 0o755)
check("5a  every rebound rater attribute is restored BY IDENTITY",
      (R.console.out is _REAL["out"], R._paced_management is _REAL["paced"],
       R.require_client is _REAL["client"],
       R.model_is_visible is _REAL["visible"], R.poll_batch is _REAL["poll"],
       J.append_with_outcome is _real_append,
       spend.require_budget is _REAL_REQUIRE_BUDGET),
      (True, True, True, True, True, True, True))
check("5b  the PRODUCTION journal is byte-unchanged",
      sha256_or_absent(_PROD_JOURNAL), _PROD_BEFORE)
check("5c  no state file was migrated from outside the temp tree",
      all(str(e.get("scope", "")).startswith(TMP) or e.get("kind") != "migration"
          for p in (_j_a, _j_c) for e in journal_lines(p)), True)
paths._RESOLVED.clear()
paths._RESOLVED.update(_PATHS_BEFORE)
if _ENV_BEFORE is None:
    os.environ.pop("ONCOTRIAGE_SPEND_JOURNAL", None)
else:
    os.environ["ONCOTRIAGE_SPEND_JOURNAL"] = _ENV_BEFORE
shutil.rmtree(TMP, ignore_errors=True)
check("5d  the temp tree is removed", os.path.exists(TMP), False)

print()
print("=" * 74)
print("SUMMARY")
print("=" * 74)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")
for _f in _FAILURES:
    print(f"  - {_f}")
sys.exit(1 if _RESULTS["failed"] else 0)
