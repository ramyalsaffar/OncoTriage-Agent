######################################################################
# The cross-process spend journal: one file, one lock, one cap
######################################################################

"""Spend Journal Test

``oncotriage/spend_journal.py`` is the store that makes
``config.RATER_SPEND_CAP_USD`` CUMULATIVE. Before it,
``rater.rater_spend_before`` seeded a judge session's ledger from THAT
SESSION'S OWN ``rater_state.json``, so a fresh ``--output-dir`` started at the
full cap and two ``rater_run.py`` invocations were two independent $50
budgets with nothing anywhere saying so.

**THE NON-DEGENERACY CONTROL IS THE FILE'S REASON FOR EXISTING, and it is TWO
REAL PROCESSES rather than two calls.** Section 5 spawns two subprocesses in
sequence, each of which appends and then reads; the second is required to see
the first's spend, and a third with the cap lowered under the running total is
required to REFUSE. A same-process check would pass against a module-global
ledger, which is exactly the mechanism the journal replaces -- so it would be
satisfied by the defect.

Section 6 then drives the CONCURRENT case for real: N processes appending at
once under the exclusive ``flock``, with the total required to be the sum and
the line count required to be N. A lock asserted rather than driven is a lock
nobody has seen refuse anything.

NO NETWORK, NO KEYS, **NO SPEND** -- no provider client of any kind is built
and no request is issued; every amount is a literal. NO MODEL LOAD
(``ONCOTRIAGE_DEFER_LOCAL_MODELS`` above the imports), no live Qdrant, no
corpus, no database, no git history, no live server.

**THE PRODUCTION JOURNAL IS NEVER WRITTEN AND NEVER READ.** Every call in this
file passes an explicit ``path=`` inside a ``tempfile.mkdtemp`` that is removed
and asserted gone, and section 8 asserts by sha256 that the real journal --
whether or not it exists -- is byte-unchanged across the run. NOT in
``tests/run_serial_tests.py``'s collision matrix: it writes only inside that
temp directory, and the two repository files it reads
(``oncotriage/spend_journal.py``, ``oncotriage/evaluation/rater.py``) are
written by neither of the suite's two writers.

It EXECS NOTHING and loads no module by location: every control is a different
INPUT to a function, a real file written into the temp tree, or a subprocess
running ``python -c`` against the installed package.

    python tests/test_spend_journal.py
"""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

try:
    import oncotriage                                          # noqa: F401
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

import ast as _ast                                             # noqa: E402
import inspect as _inspect                                     # noqa: E402
import textwrap as _textwrap                                   # noqa: E402

from oncotriage import settings as _SETTINGS                    # noqa: E402
from oncotriage import spend, spend_journal as J                # noqa: E402
from oncotriage.evaluation import rater as R                    # noqa: E402

_CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(J.__file__)))


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
        _FAILURES.append(f"{label}\n          expected: {expected!r}\n"
                         f"          actual:   {actual!r}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected!r}")
        print(f"          actual:   {actual!r}")


def check_true(label, condition):
    check(label, bool(condition), True)


def guarded(fn, *a, **kw):
    """Call ``fn``; return a marker instead of raising.

    A raise inside a ``check()`` argument aborts the file while the argument is
    being evaluated -- one traceback where the run owes a summary. Every call
    into the module under test goes through here.
    """
    try:
        return fn(*a, **kw)
    except Exception as exc:                                   # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


def sha256_or_absent(path):
    try:
        with io.open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except FileNotFoundError:
        return "<absent>"


_PROD_JOURNAL = J.journal_path()
_PROD_BEFORE = sha256_or_absent(_PROD_JOURNAL)
_SRC_BEFORE = {
    p: sha256_or_absent(os.path.join(_CODE_DIR, p))
    for p in ("oncotriage/spend_journal.py", "oncotriage/evaluation/rater.py")
}

TMP = tempfile.mkdtemp(prefix="oncotriage-spend-journal-test-")
JP = os.path.join(TMP, "journal.jsonl")


def fresh():
    """A journal path with nothing in it."""
    p = os.path.join(TMP, f"j{len(os.listdir(TMP))}.jsonl")
    return p


print("=" * 74)
print("1. ENTRY IDENTITY -- derived, so an append is idempotent")
print("=" * 74)

_a = J.entry_id("rater", "rater_batch", "/x/state.json", "batch_1")
_b = J.entry_id("rater", "rater_batch", "/x/state.json", "batch_1")
_c = J.entry_id("rater", "rater_batch", "/x/state.json", "batch_2")
_d = J.entry_id("rater", "rater_batch", "/y/state.json", "batch_1")
check("1a  the same charge computes the same id", _a, _b)
check("1b  a different batch computes a different id", _a == _c, False)
check("1c  the same batch id under a different scope is a different charge",
      _a == _d, False)
check("1d  ...and a different BUDGET is a different charge too, so one file "
      "can hold both without either reading the other's money",
      J.entry_id("campaign", "rater_batch", "/x/state.json", "batch_1") == _a,
      False)


print()
print("=" * 74)
print("2. APPEND AND READ")
print("=" * 74)

_p = fresh()
check("2a  an absent file reads as an empty list, not a refusal",
      guarded(J.read_entries, _p), [])
check("2a-i  ...and its total is a FRESH seed with no rows",
      guarded(lambda: J.total(spend.SPEND_BUDGET_RATER, path=_p).source),
      spend.SEED_SOURCE_NONE)

check("2b  the first append writes",
      guarded(J.record_batch, spend.SPEND_BUDGET_RATER,
              spend.SPEND_SOURCE_RATER, "/x/state.json", "b1", 1.25,
              "gpt-5.6-terra", path=_p), True)
check("2b-i  the SAME charge appended again writes NOTHING -- which is what "
      "makes --resume safe",
      guarded(J.record_batch, spend.SPEND_BUDGET_RATER,
              spend.SPEND_SOURCE_RATER, "/x/state.json", "b1", 1.25,
              "gpt-5.6-terra", path=_p), False)
check("2b-ii ...and the file holds ONE line",
      len(io.open(_p, encoding="utf-8").read().strip().splitlines()), 1)
check("2b-iii ...and the total is charged once",
      round(J.total(spend.SPEND_BUDGET_RATER, path=_p).usd, 6), 1.25)

J.record_batch(spend.SPEND_BUDGET_RATER, spend.SPEND_SOURCE_RATER,
               "/x/state.json", "b2", 0.75, "gpt-5.6-terra", path=_p)
check("2c  a second batch of the same session adds",
      round(J.total(spend.SPEND_BUDGET_RATER, path=_p).usd, 6), 2.0)
J.record_batch(spend.SPEND_BUDGET_RATER, spend.SPEND_SOURCE_RATER,
               "/y/state.json", "b9", 3.0, "gpt-5.6-terra", path=_p)
check("2c-i  ...and so does a DIFFERENT SESSION, which is the whole point",
      round(J.total(spend.SPEND_BUDGET_RATER, path=_p).usd, 6), 5.0)
check("2c-ii ...and the seed reports two sessions and the journal source",
      (J.total(spend.SPEND_BUDGET_RATER, path=_p).runs,
       J.total(spend.SPEND_BUDGET_RATER, path=_p).source),
      (2, spend.SEED_SOURCE_JOURNAL_RATER))
check("2c-iii the CAMPAIGN budget sees none of it -- a budget is a MEASURE as "
      "well as a cap",
      round(J.total(spend.SPEND_BUDGET_CAMPAIGN, path=_p).usd, 6), 0.0)

check("2d  every entry carries the four facts an operator needs",
      sorted(k for k in ("budget", "source", "scope", "unit", "usd",
                         "judge_model", "recorded_at_utc", "kind")
             if k in J.read_entries(_p)[0]),
      ["budget", "judge_model", "kind", "recorded_at_utc", "scope",
       "source", "unit", "usd"])


print()
print("=" * 74)
print("3. WHAT IS REFUSED, COUNTED AND SKIPPED")
print("=" * 74)

_p3 = fresh()
J.record_batch(spend.SPEND_BUDGET_RATER, spend.SPEND_SOURCE_RATER,
               "/x/s.json", "b1", 2.0, "m", path=_p3)
with io.open(_p3, "a", encoding="utf-8") as _fh:
    _fh.write("this is not json\n")
    _fh.write(json.dumps({"schema_version": J.SCHEMA_VERSION + 1,
                          "entry_id": "future", "budget": "rater",
                          "kind": "batch", "usd": 999.0}) + "\n")
    _fh.write(json.dumps({"entry_id": "noversion", "budget": "rater",
                          "kind": "batch", "usd": 999.0}) + "\n")
    _fh.write(json.dumps({"schema_version": J.SCHEMA_VERSION,
                          "entry_id": "badamount", "budget": "rater",
                          "kind": "batch", "usd": "free"}) + "\n")
    _fh.write(json.dumps({"schema_version": J.SCHEMA_VERSION,
                          "entry_id": "negative", "budget": "rater",
                          "kind": "batch", "usd": -5.0}) + "\n")
    _fh.write(json.dumps({"schema_version": J.SCHEMA_VERSION,
                          "entry_id": "unknownkind", "budget": "rater",
                          "kind": "sideways", "usd": 999.0}) + "\n")
check("3a  a line that will not parse, one from a FUTURE schema, one with no "
      "schema, a non-numeric amount, a negative amount and an unknown kind "
      "are ALL skipped -- the total is the one good entry",
      round(J.total(spend.SPEND_BUDGET_RATER, path=_p3).usd, 6), 2.0)
check("3a-i  ...and every skip is COUNTED, because an entry silently dropped "
      "is money the next session's cap will not see",
      all(k in J.JOURNAL_FAULTS for k in
          ("parse:not_json", f"schema:from_the_future:{J.SCHEMA_VERSION + 1}",
           "schema:absent", "bad_amount:str", "bad_amount:-5.0",
           "kind:sideways")), True)
check("3a-ii ...and the counter is in the run-end degradation registry, so "
      "it has a reader",
      "JOURNAL_FAULTS" in __import__(
          "oncotriage.degradation", fromlist=["x"]).registered_names(), True)

_p3b = fresh()
J.record_batch(spend.SPEND_BUDGET_RATER, spend.SPEND_SOURCE_RATER,
               "/x/s.json", "b1", 2.0, "m", path=_p3b)
_dup = io.open(_p3b, encoding="utf-8").read()
with io.open(_p3b, "a", encoding="utf-8") as _fh:
    _fh.write(_dup)
check("3b  a hand-concatenated duplicate is summed ONCE...",
      round(J.total(spend.SPEND_BUDGET_RATER, path=_p3b).usd, 6), 2.0)
check("3b-i  ...and the fact that it happened is not swallowed",
      J.JOURNAL_FAULTS["duplicate_entry_id"] >= 1, True)

check("3c  an entry with no id is never written -- an un-idempotent line "
      "would be charged again on every resume",
      guarded(J.append, {"budget": "rater", "kind": "batch", "usd": 1.0},
              path=fresh()), False)


print()
print("=" * 74)
print("4. THE MIGRATION -- summed from the artifacts on disk")
print("=" * 74)

_root = os.path.join(TMP, "runs")
for _name, _payload in (
    ("a/rater/rater_state.json",
     {"spend_usd": 1.5, "model": "judge-a",
      "batches": [{"id": "ba1"}, {"id": "ba2"}]}),
    ("b/rater_blind/rater_state_blind.json",
     {"spend_usd": 0.25, "model": "judge-b", "batches": [{"id": "bb1"}]}),
    ("c/rater/rater_state.json",
     {"model": "judge-c", "batches": [{"id": "bc1"}, {"id": "bc2"}]}),
    ("d/rater/rater_state.json", {"model": "judge-d"}),
):
    _fp = os.path.join(_root, _name)
    os.makedirs(os.path.dirname(_fp), exist_ok=True)
    with io.open(_fp, "w", encoding="utf-8") as _fh:
        json.dump(_payload, _fh)
# A file that is not a state file, and one that will not parse.
os.makedirs(os.path.join(_root, "e"), exist_ok=True)
with io.open(os.path.join(_root, "e", "manifest.json"), "w",
             encoding="utf-8") as _fh:
    _fh.write('{"spend_usd": 999.0}')
with io.open(os.path.join(_root, "e", "rater_state.json"), "w",
             encoding="utf-8") as _fh:
    _fh.write("{not json")

check("4a  every rater state file is found, in both modes, and nothing else",
      [os.path.relpath(p, _root) for p in J.find_state_files(_root)],
      ["a/rater/rater_state.json", "b/rater_blind/rater_state_blind.json",
       "c/rater/rater_state.json", "d/rater/rater_state.json",
       "e/rater_state.json"])
check("4a-i  ...and the basenames it looks for ARE the rater's own two "
      "constants, checked rather than trusted",
      tuple(sorted(J.STATE_BASENAMES)),
      tuple(sorted((R.STATE_FILENAME, R.STATE_FILENAME_BLIND))))

_p4 = fresh()
_written, _skipped, _usd = J.bootstrap_from_state_files(
    root=_root, path=_p4, out=lambda _m: None)
check("4b  the migration records one entry per readable state file",
      (_written, _skipped), (4, 0))
check("4b-i  ...and sums the amounts from the FILES, never from a total "
      "typed into this project",
      round(_usd, 6), 1.75)
check("4b-ii ...and the unparseable one is counted rather than aborting",
      J.JOURNAL_FAULTS["migrate:JSONDecodeError"] >= 1, True)

_seed = J.total(spend.SPEND_BUDGET_RATER, path=_p4)
check("4c  the cumulative reading is that sum",
      round(_seed.usd, 6), 1.75)
check("4c-i  a state file recording BATCHES and no spend is a FLOOR, not a "
      "zero -- it predates STATE_SPEND_KEY and DID spend",
      (_seed.unpriced, _seed.is_floor), (1, True))
check("4c-ii ...and the banner says so",
      "A FLOOR, NOT A TOTAL" in spend.describe_seed(_seed), True)

check("4d  running the migration again writes nothing",
      J.bootstrap_from_state_files(root=_root, path=_p4,
                                   out=lambda _m: None)[:2], (0, 4))
check("4d-i  ...and the total is unchanged",
      round(J.total(spend.SPEND_BUDGET_RATER, path=_p4).usd, 6), 1.75)

# THE DOUBLE-COUNT THE covers_batch_ids FIELD EXISTS FOR.
J.record_batch(spend.SPEND_BUDGET_RATER, spend.SPEND_SOURCE_RATER,
               os.path.join(_root, "a/rater/rater_state.json"), "ba1",
               1.5, "judge-a", path=_p4)
check("4e  a resumed session re-collecting a batch the MIGRATION already "
      "covers does not charge it twice",
      round(J.total(spend.SPEND_BUDGET_RATER, path=_p4).usd, 6), 1.75)
J.record_batch(spend.SPEND_BUDGET_RATER, spend.SPEND_SOURCE_RATER,
               os.path.join(_root, "a/rater/rater_state.json"), "ba9",
               0.5, "judge-a", path=_p4)
check("4e-i  ...but a batch it does NOT cover is charged, which is what "
      "keeps the suppression from being a hole",
      round(J.total(spend.SPEND_BUDGET_RATER, path=_p4).usd, 6), 2.25)


print()
print("=" * 74)
print("5. TWO REAL INVOCATIONS -- the non-degeneracy control")
print("=" * 74)

# THE DEFECT THIS FILE EXISTS FOR IS A PROCESS BOUNDARY, so the control has to
# cross one. Two calls in this interpreter would pass against a module-global
# ledger, which is the mechanism the journal replaces -- the check would be
# satisfied by the defect.
_SESSION = """
import json, os, sys
sys.path.insert(0, %(code)r)
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")
from oncotriage import spend, spend_journal as J
from oncotriage.evaluation import rater as R
cap, scope, amount = float(sys.argv[1]), sys.argv[2], float(sys.argv[3])
jp = sys.argv[4]
spend.SPEND_LEDGER.reset()
seed = R.rater_spend_before({}, journal=jp)
spend.SPEND_LEDGER.seed(seed)
import oncotriage.config as _cfg
_cfg.RATER_SPEND_CAP_USD = cap
before = spend.remaining(spend.SPEND_SOURCE_RATER)
refused = before is not None and amount > before
if not refused:
    spend.SPEND_LEDGER.charge_usd(amount, spend.SPEND_SOURCE_RATER)
    J.record_batch(spend.SPEND_BUDGET_RATER, spend.SPEND_SOURCE_RATER,
                   scope, "b1", amount, "judge", path=jp)
print(json.dumps({"seed_usd": round(seed.usd, 6), "seed_source": seed.source,
                  "remaining_before": None if before is None
                  else round(before, 6),
                  "refused": refused}))
""" % {"code": _CODE_DIR}


def run_session(cap, scope, amount, journal):
    proc = subprocess.run(
        [sys.executable, "-c", _SESSION, str(cap), scope, str(amount),
         journal],
        capture_output=True, text=True, cwd=TMP, timeout=180)
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    return {"stdout": proc.stdout, "stderr": proc.stderr[-2000:],
            "rc": proc.returncode}


_p5 = fresh()
_s1 = run_session(50.0, "/run/one/state.json", 12.0, _p5)
check("5a  session ONE starts fresh -- nothing has been recorded",
      (_s1.get("seed_usd"), _s1.get("refused")), (0.0, False))
check("5a-i  ...and its whole cap is available",
      _s1.get("remaining_before"), 50.0)

_s2 = run_session(50.0, "/run/two/state.json", 5.0, _p5)
check("5b  session TWO -- A DIFFERENT PROCESS -- SEES session one's spend",
      (_s2.get("seed_usd"), _s2.get("seed_source")),
      (12.0, spend.SEED_SOURCE_JOURNAL_RATER))
check("5b-i  ...so it starts with the REMAINDER and not with the full cap, "
      "which is the defect this whole file is about",
      _s2.get("remaining_before"), 38.0)
check("5b-ii ...and it is permitted, because 5.0 fits in 38.0",
      _s2.get("refused"), False)

_s3 = run_session(50.0, "/run/three/state.json", 40.0, _p5)
check("5c  session THREE is REFUSED: 40.0 does not fit in the 33.0 the "
      "first two left",
      (_s3.get("seed_usd"), _s3.get("remaining_before"), _s3.get("refused")),
      (17.0, 33.0, True))
check("5c-i  ...and a refused session recorded nothing, so the refusal is "
      "not itself a charge",
      round(J.total(spend.SPEND_BUDGET_RATER, path=_p5).usd, 6), 17.0)

_p5b = fresh()
_s4 = run_session(50.0, "/run/one/state.json", 12.0, _p5b)
_s5 = run_session(10.0, "/run/two/state.json", 1.0, _p5b)
# -2.0 AND NOT 0.0: `spend.remaining` deliberately does not clamp, so a reader
# sees the overshoot rather than a zero that hides it. The pin is the negative
# number, because a clamp introduced here would be a real change to what an
# operator is shown and it should fail somewhere.
check("5d  the CAP is compared against the LEDGER remainder, so lowering it "
      "under the running total refuses even a tiny charge",
      (_s5.get("remaining_before"), _s5.get("refused")), (-2.0, True))
check("5d-i  ...and the cumulative reading is what made it negative, not "
      "this session's own spend, which is zero",
      _s5.get("seed_usd"), 12.0)


print()
print("=" * 74)
print("6. THE LOCK, DRIVEN -- N processes appending at once")
print("=" * 74)

_CONCURRENT = """
import os, sys, time
sys.path.insert(0, %(code)r)
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")
from oncotriage import spend, spend_journal as J
jp, tag, gate = sys.argv[1], sys.argv[2], sys.argv[3]
# EVERY WORKER PARKS ON A FILE rather than sleeping, so the overlap is a
# statement about the lock and not about this machine's scheduler.
open(os.path.join(os.path.dirname(gate), "ready_" + tag), "w").close()
while not os.path.exists(gate):
    time.sleep(0.01)
J.record_batch(spend.SPEND_BUDGET_RATER, spend.SPEND_SOURCE_RATER,
               "/concurrent/" + tag, "b" + tag, 1.0, "judge", path=jp)
""" % {"code": _CODE_DIR}

_p6 = fresh()
_gate_dir = os.path.join(TMP, "gate")
os.makedirs(_gate_dir, exist_ok=True)
_gate = os.path.join(_gate_dir, "GO")
_N = 8
_procs = [subprocess.Popen([sys.executable, "-c", _CONCURRENT, _p6, str(i),
                            _gate], cwd=TMP,
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE)
          for i in range(_N)]
_deadline = __import__("time").time() + 120
while __import__("time").time() < _deadline:
    if sum(1 for i in range(_N)
           if os.path.exists(os.path.join(_gate_dir, f"ready_{i}"))) == _N:
        break
    __import__("time").sleep(0.02)
_all_ready = sum(1 for i in range(_N)
                 if os.path.exists(os.path.join(_gate_dir, f"ready_{i}")))
open(_gate, "w").close()
_rcs = [p.wait(timeout=180) for p in _procs]

check("6a  every worker reached the gate before any of them appended -- "
      "without this the overlap is a claim rather than a measurement",
      _all_ready, _N)
check("6a-i  ...and every one of them exited 0",
      sorted(set(_rcs)), [0])
check("6b  the file holds exactly one line per worker: no line was lost and "
      "none was interleaved into another",
      len([l for l in io.open(_p6, encoding="utf-8").read().splitlines()
           if l.strip()]), _N)
check("6b-i  ...and every line is well-formed JSON, which is what a torn "
      "concurrent write would not be",
      len(J.read_entries(_p6)), _N)
check("6c  the total is the sum of every worker's charge",
      round(J.total(spend.SPEND_BUDGET_RATER, path=_p6).usd, 6), float(_N))
check("6c-i  ...and it counts N distinct sessions",
      J.total(spend.SPEND_BUDGET_RATER, path=_p6).runs, _N)


print()
print("=" * 74)
print("7. THE SEAM: rater_spend_before reads the journal, and falls back")
print("=" * 74)

_p7 = fresh()
J.record_batch(spend.SPEND_BUDGET_RATER, spend.SPEND_SOURCE_RATER,
               "/x/s.json", "b1", 9.0, "judge", path=_p7)
_state = {"spend_usd": 1.0, "batches": [{"id": "z"}]}
check("7a  the CUMULATIVE reading wins over this session's own state file",
      (guarded(lambda: R.rater_spend_before(_state, journal=_p7).usd),
       guarded(lambda: R.rater_spend_before(_state, journal=_p7).source)),
      (9.0, spend.SEED_SOURCE_JOURNAL_RATER))
check("7a-i  ...and it is NOT the sum of the two, which would double-count "
      "every batch the journal already holds",
      guarded(lambda: R.rater_spend_before(_state, journal=_p7).usd), 9.0)

_p7b = fresh()
check("7b  with NOTHING in the journal it falls back to the session reading, "
      "which is the pre-journal behaviour",
      (guarded(lambda: R.rater_spend_before(_state, journal=_p7b).usd),
       guarded(lambda: R.rater_spend_before(_state, journal=_p7b).source)),
      (1.0, spend.SEED_SOURCE_RATER_STATE))
check("7b-i  ...and the two sources are DIFFERENT members, so the banner "
      "says which of them answered",
      spend.SEED_SOURCE_JOURNAL_RATER == spend.SEED_SOURCE_RATER_STATE, False)
check("7c  a fresh session with neither is a FRESH seed, not a refusal",
      guarded(lambda: R.rater_spend_before({}, journal=_p7b).source),
      spend.SEED_SOURCE_NONE)
check("7d  NEVER RAISES: a state that is not a dict, a NaN, a negative and a "
      "bool all yield a seed rather than an exception",
      [guarded(lambda s=_s: R.rater_spend_before(s, journal=_p7b).usd)
       for _s in (None, "text", {"spend_usd": float("nan")},
                  {"spend_usd": -1.0}, {"spend_usd": True})],
      [0.0, 0.0, 0.0, 0.0, 0.0])

check("7e  the journal seed lands in the RATER budget, so the gate compares "
      "it against RATER_SPEND_CAP_USD and not the campaign's",
      spend.seed_budget(J.total(spend.SPEND_BUDGET_RATER, path=_p7)),
      spend.SPEND_BUDGET_RATER)
check("7e-i  ...and a CAMPAIGN reading lands in the campaign budget",
      spend.BUDGET_FOR_SEED_SOURCE[spend.SEED_SOURCE_JOURNAL_CAMPAIGN],
      spend.SPEND_BUDGET_CAMPAIGN)
check("7f  SEED_SOURCE_FOR_BUDGET is TOTAL over the budgets, so no budget's "
      "cumulative history can be attributed to another",
      sorted(J.SEED_SOURCE_FOR_BUDGET), sorted(spend.SPEND_BUDGETS))


print()
print("=" * 74)
print("7b. THE ENVIRONMENT OVERRIDE -- how a caller redirects a store the "
      "code resolves for itself")
print("=" * 74)

# **THIS TIER EXISTS BECAUSE A TEST WROTE INTO A PRODUCTION ARTIFACT.**
# `ragas_harness.main()` records to the journal and resolves its own path, so
# tests/test_resume_capture_and_ragas.py -- which drives that main() ten times
# -- put ten entries in the real file the first time this pass was run. That
# is the same hole ONCOTRIAGE_INFERENCES_DB fills one store over, and it is
# closed the same way.
_env_before = os.environ.get(_SETTINGS.ENV_SPEND_JOURNAL)
_over = os.path.join(TMP, "override.jsonl")
try:
    os.environ[_SETTINGS.ENV_SPEND_JOURNAL] = _over
    check("7g  the variable moves the default path",
          guarded(J.journal_path), os.path.abspath(_over))
    check("7g-i  ...and an EXPLICIT argument still outranks it, because that "
          "answers a question about one call rather than about the machine",
          guarded(J.journal_path, _p7), os.path.abspath(_p7))
    os.environ[_SETTINGS.ENV_SPEND_JOURNAL] = "   "
    check("7g-ii a blank value is 'not set' rather than a path, so an "
          "exported-but-empty variable does not resolve to the cwd",
          guarded(J.journal_path), _PROD_JOURNAL)
    os.environ[_SETTINGS.ENV_SPEND_JOURNAL] = os.path.join(
        TMP, "no", "such", "dir", "j.jsonl")
    check("7g-iii a MISSING PARENT raises by name rather than being counted "
          "as a write fault once per batch -- the FILE not existing is the "
          "normal case, the directory not existing is a configuration defect",
          "RuntimeError" in str(guarded(J.journal_path)), True)
finally:
    if _env_before is None:
        os.environ.pop(_SETTINGS.ENV_SPEND_JOURNAL, None)
    else:
        os.environ[_SETTINGS.ENV_SPEND_JOURNAL] = _env_before
check("7g-iv restored: the default resolves again",
      guarded(J.journal_path), _PROD_JOURNAL)
# WALKED, NOT GREPPED. The resolver's own docstring ARGUES about `_from_env`
# -- it says why it is not used -- so a substring test reports the argument as
# the thing it argues against. This project has shipped that shape four times;
# the scan is over CALL nodes with the docstring stripped.
def _calls_in(fn):
    tree = _ast.parse(_textwrap.dedent(_inspect.getsource(fn)))
    return {n.func.id for n in _ast.walk(tree)
            if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name)}


check("7g-v  ...and the resolver does not CALL `_from_env`, which appends a "
      "trailing separator and would turn a journal path into one io.open "
      "refuses",
      "_from_env" in _calls_in(_SETTINGS.resolve_spend_journal), False)
check("7g-vi non-degeneracy: the walk sees the calls that ARE there, so 7g-v "
      "is not passing over an empty set",
      bool(_calls_in(_SETTINGS.resolve_spend_journal)), True)
check("7g-vii ...and the same walk over a resolver that DOES use the helper "
      "finds it, which is what says the scan can answer True at all",
      "_from_env" in _calls_in(_SETTINGS.resolve_code_path), True)


print()
print("=" * 74)
print("7d. AN UNRESOLVABLE DEFAULT PATH DEGRADES, IT DOES NOT RAISE")
print("=" * 74)

# **FIVE FUNCTIONS CLAIMED 'NEVER RAISES' AND ALL FIVE RAISED, MEASURED RATHER
# THAN REVIEWED.** The default path is built from
# `paths.testing_evaluation_path`, a LAZY GLOB whose `_glob_one` raises when
# nothing matches -- so on a machine with no `09- Testing/` tree (a wheel
# install, a CI checkout of the code alone, a container before its data volume
# is mounted) `rater_spend_before` refused to start a judge session because a
# DIRECTORY was missing. That is the exact failure its own docstring forbids.
#
# **DRIVEN IN A SUBPROCESS WITH ONCOTRIAGE_MAIN_PATH POINTED AT AN EMPTY
# DIRECTORY, AND THE FIRST VERSION OF THIS SECTION IS WHY.** It seeded
# `paths._RESOLVED` instead, the seeding did not take, every call resolved the
# REAL default -- and the section that exists to prove the journal degrades
# safely WROTE NINETEEN ENTRIES INTO THE PRODUCTION JOURNAL. Section 8a caught
# it. In a subprocess whose project root genuinely has no `09- Testing/`, a
# call that failed to degrade cannot reach the real file because there is no
# real file to reach: the isolation is a property of the environment rather
# than of the seeding having worked.
_DEGRADE_PROBE = """
import json, os, sys
sys.path.insert(0, %(code)r)
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")
os.environ["ONCOTRIAGE_MAIN_PATH"] = sys.argv[1]
os.environ.pop("ONCOTRIAGE_SPEND_JOURNAL", None)
from oncotriage import spend, spend_journal as J
from oncotriage.evaluation import rater as R


def outcome(fn):
    try:
        return {"value": fn()}
    except Exception as exc:
        return {"raised": type(exc).__name__}


seed = outcome(lambda: R.rater_spend_before({"spend_usd": 3.0}))
if "value" in seed:
    seed = {"value": [round(seed["value"].usd, 6), seed["value"].source]}
print(json.dumps({
    "journal_path": outcome(J.journal_path),
    "resolved": outcome(J.resolved_journal_path),
    "read_entries": outcome(J.read_entries),
    "total": outcome(lambda: J.total(spend.SPEND_BUDGET_RATER).source),
    "find_state_files": outcome(J.find_state_files),
    "bootstrap": outcome(lambda: list(
        J.bootstrap_from_state_files(out=lambda _m: None))),
    "append": outcome(lambda: J.append(
        {"entry_id": "x", "usd": 1.0, "budget": "rater", "kind": "batch"})),
    "rater_spend_before": seed,
    "counted": outcome(lambda: any(
        k.startswith("unresolvable_") for k in J.JOURNAL_FAULTS)),
}))
""" % {"code": _CODE_DIR}

_EMPTY_ROOT = os.path.join(TMP, "no-such-project-root")
os.makedirs(_EMPTY_ROOT, exist_ok=True)
_probe = subprocess.run([sys.executable, "-c", _DEGRADE_PROBE, _EMPTY_ROOT],
                        capture_output=True, text=True, cwd=TMP, timeout=180)
_D = {}
for _line in reversed((_probe.stdout or "").splitlines()):
    if _line.strip().startswith("{"):
        _D = json.loads(_line)
        break

check("7i  the probe ran at all -- without this every reading below is an "
      "empty dict compared with itself",
      sorted(_D) if _D else (_probe.stdout[-400:], _probe.stderr[-600:]),
      ["append", "bootstrap", "counted", "find_state_files", "journal_path",
       "rater_spend_before", "read_entries", "resolved", "total"])
check("7i-i  journal_path itself STILL RAISES -- it is asked where the file "
      "is and there is no honest answer",
      _D.get("journal_path"), {"raised": "RuntimeError"})
check("7i-ii ...but resolved_journal_path answers None instead",
      _D.get("resolved"), {"value": None})
for _label, _expected in (("read_entries", []),
                          ("total", "fresh"),
                          ("find_state_files", []),
                          ("bootstrap", [0, 0, 0.0]),
                          ("append", False)):
    check(f"7i  {_label} degrades rather than raising",
          _D.get(_label), {"value": _expected})
check("7i  ...and rater_spend_before falls back to the SESSION reading, "
      "which is the whole point: a judge must not refuse to start because a "
      "directory is missing",
      _D.get("rater_spend_before"),
      {"value": [3.0, spend.SEED_SOURCE_RATER_STATE]})
check("7i  ...and every one of those degradations is COUNTED, because "
      "'there is no journal' and 'I could not find the journal' are the same "
      "empty reading and only one is a machine that needs fixing",
      _D.get("counted"), {"value": True})


print()
print("=" * 74)
print("7c-2. append_with_outcome: FOUR ANSWERS WHERE THERE WAS A BOOL")
print("=" * 74)

# `append` returns False for "the id is already there, so the money IS
# recorded" AND for "the write failed, so the money is NOT recorded". Those
# have OPPOSITE consequences for a caller keeping a running total, and
# `RunSpendCheckpointer` was that caller: it advanced on both, and a failed
# $0.30 delta was lost while it reported the money as recorded. The four-valued
# answer is what makes the distinction expressible; section 7d-i is the repair
# that uses it.


# ══ THE SHARED HELPERS FOR EVERY JOURNAL SECTION BELOW ══════════════════
#
# THEY LIVE HERE, ABOVE THE FIRST SECTION THAT USES ANY OF THEM, and that is a
# correction rather than a preference: defining a helper in the section that
# happened to need it first and then using it from an EARLIER section is a
# `NameError` at import, which reports one traceback where this file owes a
# summary and 200 results. It happened three times while these sections were
# being written -- `_usd_all`, then `_cp`, then `_at` -- so the helpers are
# collected once and the sections below only use them.

def _at(seq, i):
    """``seq[i]``, or a named absence. NEVER RAISES.

    **THE SHAPE THIS REMOVES ABORTED THIS FILE ONCE, IN THE SESSION THAT ADDED
    IT.** ``_cH.pending[0]`` raises ``IndexError`` exactly when a defect makes
    a delta confirm that should have stayed pending -- which is when this file
    owes a summary and 184 results, not a traceback. Found by the revert matrix
    (R0), not by reading. It is the eighteenth time this project has met it.
    """
    try:
        return seq[i]
    except (IndexError, KeyError, TypeError):
        return _AbsentItem(f"<no item {i} in {seq!r}>")


class _AbsentItem(object):
    """A pending delta that is not there. Every field is a named absence.

    **``usd`` IS A NaN AND NOT THE MESSAGE STRING, AND THAT IS THE SECOND HALF
    OF THE FIX.** The first version returned the message for every field, and
    ``round("<no item 0 ...>", 6)`` raises ``TypeError`` -- so the guard MOVED
    the abort from an ``IndexError`` at the subscript to a ``TypeError`` one
    line later, which is not a fix. Found by the revert matrix (R0) a second
    time, having been "fixed" once. A NaN rounds, compares unequal to
    everything, and so makes the check FAIL and print what it got.
    """

    def __init__(self, why):
        self.why = why
        self.unit = why
        self.usd = float("nan")
        self.attempts = float("nan")
        self.last_outcome = why

    def __repr__(self):
        return self.why


def _cp(path, prefix="STAMP1", **kw):
    """A checkpointer over one journal, with an injectable clock."""
    kw.setdefault("min_usd", 0.25)
    kw.setdefault("min_seconds", 60.0)
    return J.RunSpendCheckpointer(
        spend.SPEND_BUDGET_CAMPAIGN, spend.SPEND_SOURCE_RAGAS_JUDGE,
        "/out", prefix, "judge", path=path, **kw)


def _usd_all(path):
    """Every campaign entry in the file, whoever wrote it."""
    return round(J.total(spend.SPEND_BUDGET_CAMPAIGN, path=path).usd, 6)


def _entry(unit, usd, scope="/out"):
    return {"entry_id": J.entry_id(spend.SPEND_BUDGET_CAMPAIGN,
                                   spend.SPEND_SOURCE_RAGAS_JUDGE, scope, unit),
            "kind": J.ENTRY_KIND_RUN, "budget": spend.SPEND_BUDGET_CAMPAIGN,
            "source": spend.SPEND_SOURCE_RAGAS_JUDGE, "scope": scope,
            "unit": unit, "usd": usd, "judge_model": "judge"}


_p7c2 = fresh()
check("7c-2a  a first append WROTE",
      J.append_with_outcome(_entry("u0", 1.0), path=_p7c2), J.APPEND_WROTE)
check("7c-2b  the same charge again is a DUPLICATE, not a failure -- the id, "
      "the charge identity AND the amount all match, so the money IS recorded "
      "and a retry may advance past it",
      J.append_with_outcome(_entry("u0", 1.0), path=_p7c2), J.APPEND_DUPLICATE)
check("7c-2b-i  ...and it wrote no second line",
      len(J.read_entries(_p7c2)), 1)
# THE RETRY'S OTHER FIELDS DIFFER AND MUST NOT MATTER. `recorded_at_utc` is
# stamped per attempt, so a whole-dict comparison would report every retry as a
# conflict -- the one thing that must not happen to a retry.
_later = _entry("u0", 1.0)
_later["recorded_at_utc"] = "2099-01-01T00:00:00+00:00"
check("7c-2b-ii ...and a retry whose recorded_at_utc differs is STILL a "
      "duplicate, which is what makes retrying possible at all",
      J.append_with_outcome(_later, path=_p7c2), J.APPEND_DUPLICATE)
_before_conflict = J.JOURNAL_FAULTS["append:conflict"]
check("7c-2c  the same id carrying a DIFFERENT amount is a CONFLICT -- the "
      "existing entry does not record this charge and never will",
      J.append_with_outcome(_entry("u0", 2.0), path=_p7c2), J.APPEND_CONFLICT)
check("7c-2c-i  ...counted rather than silent",
      J.JOURNAL_FAULTS["append:conflict"] - _before_conflict, 1)
check("7c-2c-ii ...and nothing was written, so the file still holds one line",
      (len(J.read_entries(_p7c2)), _usd_all(_p7c2)), (1, 1.0))
check("7c-2d  the bool wrapper is unchanged for every existing caller: "
      "wrote -> True, everything else -> False",
      (J.append(_entry("u1", 3.0), path=_p7c2),
       J.append(_entry("u1", 3.0), path=_p7c2),
       J.append(_entry("u1", 9.0), path=_p7c2)), (True, False, False))
# ── failed VERSUS uncertain, driven rather than described. The split is
#    decided by whether the write was ATTEMPTED, not by the exception type: a
#    caller that read "uncertain" as "nothing happened" and retried under a NEW
#    id would record the same money twice.
_p7c2b = os.path.join(TMP, "unwritable_dir")
os.makedirs(_p7c2b, exist_ok=True)
os.chmod(_p7c2b, 0o500)
try:
    check("7c-2e  an error BEFORE the write is `failed` -- nothing persisted",
          J.append_with_outcome(_entry("u0", 1.0),
                                path=os.path.join(_p7c2b, "j.jsonl")),
          J.APPEND_FAILED)
finally:
    os.chmod(_p7c2b, 0o700)
check("7c-2e-i  ...and it is counted under a key naming the phase",
      any(k.startswith("append:failed:") for k in J.JOURNAL_FAULTS), True)

# THE UNCERTAIN CASE NEEDS THE WRITE TO HAVE BEEN REACHED, so `os.fsync` is
# made to raise -- which is exactly the real shape: the bytes are in the file
# and the durability call failed. Restored inside a `finally` with the restore
# ASSERTED, because a leaked fsync patch would break every later section.
_p7c2c = fresh()
_real_fsync = os.fsync


def _fsync_raises(fd):
    raise OSError(5, "simulated I/O error after the write")


os.fsync = _fsync_raises
try:
    _unc = J.append_with_outcome(_entry("u9", 4.0), path=_p7c2c)
finally:
    os.fsync = _real_fsync
check("7c-2f  RESTORE: os.fsync is the real one again",
      os.fsync is _real_fsync, True)
check("7c-2f-i  an error AT OR AFTER the write is `uncertain`, not `failed`",
      _unc, J.APPEND_UNCERTAIN)
check("7c-2f-ii ...and it is the honest answer: the line IS in the file, so a "
      "caller that retried under a NEW id would record the money twice",
      _usd_all(_p7c2c), 4.0)
check("7c-2f-iii ...while a RETRY under the same id and amount resolves it to "
      "a duplicate, which is the whole reason the pair is frozen",
      J.append_with_outcome(_entry("u9", 4.0), path=_p7c2c),
      J.APPEND_DUPLICATE)
check("7c-2f-iv ...and still one line", len(J.read_entries(_p7c2c)), 1)

# ── THE SCOPED READ-BACK. The journal is SHARED, so a writer verifying its own
#    spend cannot sum the file.
_p7c2d = fresh()
J.append_with_outcome(_entry("MINE#0", 1.0, scope="/mine"), path=_p7c2d)
J.append_with_outcome(_entry("MINE#1", 2.0, scope="/mine"), path=_p7c2d)
J.append_with_outcome(_entry("OTHER#0", 99.0, scope="/mine"), path=_p7c2d)
J.append_with_outcome(_entry("MINE#0", 50.0, scope="/theirs"), path=_p7c2d)
J.record_batch(spend.SPEND_BUDGET_RATER, spend.SPEND_SOURCE_RATER,
               "/mine", "b1", 7.0, "judge", path=_p7c2d)
check("7c-2g  confirmed_usd_for_scope sums THIS writer's entries only",
      J.confirmed_usd_for_scope(spend.SPEND_BUDGET_CAMPAIGN,
                                spend.SPEND_SOURCE_RAGAS_JUDGE, "/mine",
                                path=_p7c2d), 102.0)
check("7c-2g-i  ...and narrowed by unit prefix it is THIS INVOCATION's",
      J.confirmed_usd_for_scope(spend.SPEND_BUDGET_CAMPAIGN,
                                spend.SPEND_SOURCE_RAGAS_JUDGE, "/mine",
                                unit_prefix="MINE#", path=_p7c2d), 3.0)
check("7c-2g-ii non-degeneracy: the file really holds the other rows, so the "
      "narrowing above is excluding something. 152.0 and not 159.0 -- the "
      "$7 record_batch is on the RATER budget, which _usd_all does not sum, "
      "and getting that wrong is the reason this check reads a total rather "
      "than trusting the seeding above it",
      round(_usd_all(_p7c2d), 6), 152.0)
check("7c-2g-iii a scope that wrote nothing reads 0.0, not the file's total",
      J.confirmed_usd_for_scope(spend.SPEND_BUDGET_CAMPAIGN,
                                spend.SPEND_SOURCE_RAGAS_JUDGE, "/nowhere",
                                path=_p7c2d), 0.0)
check("7c-2g-iv an absent journal reads 0.0 and does not raise",
      J.confirmed_usd_for_scope(spend.SPEND_BUDGET_CAMPAIGN,
                                spend.SPEND_SOURCE_RAGAS_JUDGE, "/mine",
                                path=os.path.join(TMP, "no", "such", "j")),
      0.0)


print()
print("=" * 74)
print("7c-3. AN INTERRUPTED WRITE LEAVES A BOUNDARY, AND IT IS RECOVERED")
print("=" * 74)

# ** THE DEFECT, REPRODUCED BEFORE IT WAS REPAIRED. **
#
# A journal whose FINAL LINE IS UNTERMINATED -- what a kill mid-`fh.write`
# leaves -- made the next append CONCATENATE onto it. Measured: a good entry,
# a killed partial write, then an append reported `wrote`; the merged line
# would not parse; `read_entries` counted-and-skipped it; the caller advanced
# its total for money the file could not return. The read-back at finalize saw
# the shortfall and could not recover it.
#
# TWO SHAPES SHARE THAT SIGNATURE and they are deliberately not distinguished
# at detection: an INCOMPLETE RECORD, and a COMPLETE RECORD MISSING ONLY ITS
# TERMINATOR. Both are repaired the same way -- one appended newline -- and
# their outcomes then differ, which is what the checks below separate.
#
# EVERY ASSERTION IS A SUM OR A COUNT READ BACK FROM THE FILE.


def _write_raw(path, blob):
    """Put bytes on the end of a journal, terminator and all as given."""
    with io.open(path, "ab") as fh:
        fh.write(blob)


def _lines_on_disk(path):
    with io.open(path, "rb") as fh:
        return fh.read().splitlines()


def _complete_record_bytes(unit, usd, scope="/out"):
    """A well-formed entry's bytes WITHOUT its terminator.

    Built the way the writer builds it -- through `_serialize_entry`, with the
    same defaults `append_with_outcome` applies -- and then stripped of the
    newline, so the fragment is genuinely "the write landed, the terminator did
    not" rather than a hand-shaped approximation of it.
    """
    payload = dict(_entry(unit, usd, scope=scope))
    payload["schema_version"] = J.SCHEMA_VERSION
    payload["recorded_at_utc"] = "2026-01-01T00:00:00+00:00"
    return J._serialize_entry(payload).rstrip(b"\n")


# --- (a) A PARTIAL TRAILING FRAGMENT ---------------------------------------
_pB1 = fresh()
J.append_with_outcome(_entry("b0", 1.0), path=_pB1)
_FRAGMENT = b'{"entry_id": "half", "usd": 5.0, "bud'
_write_raw(_pB1, _FRAGMENT)
check("7c-3a  precondition: the file's last byte is NOT a newline, which is "
      "what a kill mid-write leaves",
      io.open(_pB1, "rb").read()[-1:] != b"\n", True)
_before_bound = J.JOURNAL_FAULTS["append:boundary_recovered"]
_outA = J.append_with_outcome(_entry("b1", 2.0), path=_pB1)
check("7c-3a-i  the append reports WROTE -- and this time the file agrees",
      _outA, J.APPEND_WROTE)
check("7c-3a-ii the boundary recovery is COUNTED, not silent",
      J.JOURNAL_FAULTS["append:boundary_recovered"] - _before_bound, 1)
check("7c-3a-iii the earlier valid entry is PRESERVED and the new one landed "
      "-- BEFORE THE REPAIR this read 1 entry and $1.00, with the $2.00 merged "
      "into the fragment and lost",
      (len(J.read_entries(_pB1)), _usd_all(_pB1)), (2, 3.0))
check("7c-3a-iv  the fragment SURVIVES as evidence on its own line -- nothing "
      "was truncated, rewritten or deleted",
      (len(_lines_on_disk(_pB1)), _FRAGMENT in _lines_on_disk(_pB1)),
      (3, True))
check("7c-3a-v  ...and it surfaces through the existing unreadable-line fault "
      "counting rather than being absorbed",
      J.JOURNAL_FAULTS["parse:not_json"] > 0, True)
# THE AMOUNT LANDS EXACTLY ONCE. A retry after the recovery must see its own
# entry and refuse, not write a second.
check("7c-3a-vi a retry of the same charge is a DUPLICATE, so the amount is "
      "recorded once", J.append_with_outcome(_entry("b1", 2.0), path=_pB1),
      J.APPEND_DUPLICATE)
check("7c-3a-vii ...and the total is unmoved by that retry",
      (_usd_all(_pB1), len(J.read_entries(_pB1))), (3.0, 2))

# --- (b) A FRAGMENT CUT MID-UTF-8-CHARACTER --------------------------------
#
# **THIS CASE EXPOSED A SECOND DEFECT, IN THE READER.** Every reader here used
# to open the journal with `encoding="utf-8"` and read it whole, so ONE
# truncated multi-byte character raised `UnicodeDecodeError` -- a `ValueError`,
# not an `OSError`, so no handler in the module caught it. Measured before the
# repair: `read_entries` RAISED, and with it `total`, `describe`,
# `confirmed_usd_for_scope` and `rater_spend_before`, all of which say NEVER
# RAISES. One truncated byte took the cumulative cap out of service.
#
# THE REPAIR DOES NOT MEND THE ENCODING AND MUST NOT PRETEND TO. The bytes are
# not valid UTF-8 and nothing here knows what they were going to be; the line
# is isolated, skipped and counted, and everything either side of it stays
# readable.
_pB2 = fresh()
J.append_with_outcome(_entry("c0", 1.0), path=_pB2)
_MIDCHAR = b'{"unit": "caf\xc3'
_write_raw(_pB2, _MIDCHAR)
_before_utf8 = J.JOURNAL_FAULTS["parse:not_utf8"]
check("7c-3b  the reader SURVIVES a mid-character truncation and still "
      "returns the records around it -- before the repair this RAISED",
      len(J.read_entries(_pB2)), 1)
_outB = J.append_with_outcome(_entry("c1", 2.0), path=_pB2)
check("7c-3b-i  ...and so does the writer", _outB, J.APPEND_WROTE)
check("7c-3b-ii the damaged line is SKIPPED as a counted fault under its own "
      "key -- not-utf8 is a different diagnosis from not-json",
      J.JOURNAL_FAULTS["parse:not_utf8"] - _before_utf8 > 0, True)
check("7c-3b-iii the record BEFORE it and the record AFTER it are both "
      "readable and correctly summed",
      (len(J.read_entries(_pB2)), _usd_all(_pB2)), (2, 3.0))
check("7c-3b-iv  the damaged bytes are still on disk, unrepaired: this module "
      "does not invent characters it cannot decode",
      _MIDCHAR in _lines_on_disk(_pB2), True)
check("7c-3b-v  ...and it is NOT decoded with errors='replace', which would "
      "hand mojibake to json.loads and, if it parsed, put invented characters "
      "into a record this module reports as fact",
      J.decode_journal_line(_MIDCHAR), None)

# --- (c) A COMPLETE FINAL RECORD MISSING ONLY ITS NEWLINE ------------------
# The other shape. Here recovery makes a REAL record readable, so the file
# gains an entry rather than a fault -- and the duplicate scan then sees it.
_pB3 = fresh()
J.append_with_outcome(_entry("d0", 1.0), path=_pB3)
_write_raw(_pB3, _complete_record_bytes("d9", 4.0))
# ** A CORRECTION TO THIS SECTION'S OWN FIRST DRAFT, WHICH ASSERTED THE
# ** OPPOSITE AND WAS WRONG. It claimed the unterminated record is INVISIBLE
# ** until it gains a newline. It is not: `read_entries` splits with
# ** `bytes.splitlines()`, which yields a final unterminated chunk as its own
# ** line, so such a record is ALREADY readable. Measured -- the check expected
# ** (1, $1.00) and the answer is (2, $5.00).
# **
# ** SO THE DANGER IS THE WRITE PATH AND NOT THE READ PATH, and requirement 2
# ** is the right framing precisely because of that: the record must remain
# ** fully readable AFTERWARD. Without boundary recovery the next append
# ** CONCATENATES onto it and destroys a record that was readable a moment
# ** earlier -- losing the new charge AND the old one. That is what 7c-3c-ii
# ** asserts and what plant (a) of the revert matrix reproduces.
check("7c-3c  precondition: an unterminated COMPLETE record is already "
      "readable -- splitlines() yields the final chunk as its own line",
      (len(J.read_entries(_pB3)), _usd_all(_pB3)), (2, 5.0))
check("7c-3c-i  ...and yet the file's last byte is not a newline, which is "
      "what the next writer would append onto",
      io.open(_pB3, "rb").read()[-1:] != b"\n", True)
_outC = J.append_with_outcome(_entry("d1", 2.0), path=_pB3)
check("7c-3c-ii after the append BOTH are readable: the record that was "
      "missing only its terminator SURVIVED it, and the new entry landed. "
      "Without recovery the append would have merged into that record and "
      "destroyed both",
      (_outC, len(J.read_entries(_pB3)), _usd_all(_pB3)),
      (J.APPEND_WROTE, 3, 7.0))
check("7c-3c-iii ...and NO duplicate charge: three lines, three entries, each "
      "amount once",
      sorted(round(e["usd"], 6) for e in J.read_entries(_pB3)),
      [1.0, 2.0, 4.0])
# AND THE RECORD IS VISIBLE TO THE DUPLICATE SCAN -- which it was BEFORE
# recovery too, and saying otherwise was this comment's own error.
# `splitlines()` yields the trailing chunk as its own line, so a
# complete-but-unterminated record is already readable to the scan and to
# `read_entries` alike.
#
# WHY RECOVERY STILL SITS ABOVE THE SCAN: a duplicate or a conflict returns
# WITHOUT writing, so recovery placed beside the write would skip those calls
# and leave the boundary torn. Above it, the file is left consistent whatever
# the outcome and the next append cannot join onto that record.
check("7c-3c-iv  the recovered record is seen by the duplicate scan, so its "
      "charge cannot be written again",
      J.append_with_outcome(_entry("d9", 4.0), path=_pB3), J.APPEND_DUPLICATE)
check("7c-3c-v  ...and the total is unmoved", _usd_all(_pB3), 7.0)

# --- (d) THE RESTART CASE, HONESTLY SCOPED --------------------------------
#
# **THIS REPAIR ADDS NO AUTOMATIC RECOVERY OF A DEAD PROCESS'S PENDING DATA,
# AND THE TEST SAYS SO RATHER THAN IMPLYING OTHERWISE.** `RunSpendCheckpointer`
# holds its pending deltas IN MEMORY; a SIGKILL takes them with it, and nothing
# on disk names them. What a restart can do is what a caller who kept the
# original id and amount elsewhere can do -- and this simulates exactly that,
# by SUPPLYING them.
_pB4 = fresh()
_c4 = _cp(_pB4, prefix="RESTART", min_usd=0.0)
_c4.checkpoint(0.10)
_ORIG = _at(_c4.entries, 0)
# The kill: a partial write lands after the confirmed entry, and the process
# holding the pending state dies.
_write_raw(_pB4, b'{"entry_id": "torn')
check("7c-3d  precondition: one confirmed entry and a torn tail",
      (_usd_all(_pB4), io.open(_pB4, "rb").read()[-1:] != b"\n"), (0.10, True))
# THE RESTART. A NEW checkpointer, told the ORIGINAL unit and amount -- which
# is the only way this can work, because nothing recovered them for it.
_c5 = J.RunSpendCheckpointer(
    spend.SPEND_BUDGET_CAMPAIGN, spend.SPEND_SOURCE_RAGAS_JUDGE, "/out",
    "RESTART", "judge", path=_pB4, min_usd=0.0)
_replay = J.append_with_outcome({
    "entry_id": J.entry_id(spend.SPEND_BUDGET_CAMPAIGN,
                           spend.SPEND_SOURCE_RAGAS_JUDGE, "/out", "RESTART#1"),
    "kind": J.ENTRY_KIND_RUN, "budget": spend.SPEND_BUDGET_CAMPAIGN,
    "source": spend.SPEND_SOURCE_RAGAS_JUDGE, "scope": "/out",
    "unit": "RESTART#1", "usd": 0.25, "judge_model": "judge"}, path=_pB4)
check("7c-3d-i  the replayed charge lands against the recovered boundary",
      (_replay, _usd_all(_pB4)), (J.APPEND_WROTE, 0.35))
check("7c-3d-ii ...exactly once: replaying it again is a duplicate",
      (J.append_with_outcome({
          "entry_id": J.entry_id(spend.SPEND_BUDGET_CAMPAIGN,
                                 spend.SPEND_SOURCE_RAGAS_JUDGE, "/out",
                                 "RESTART#1"),
          "kind": J.ENTRY_KIND_RUN, "budget": spend.SPEND_BUDGET_CAMPAIGN,
          "source": spend.SPEND_SOURCE_RAGAS_JUDGE, "scope": "/out",
          "unit": "RESTART#1", "usd": 0.25, "judge_model": "judge"},
          path=_pB4), _usd_all(_pB4)),
      (J.APPEND_DUPLICATE, 0.35))
check("7c-3d-iii and the ORIGINAL confirmed entry is untouched by any of it",
      _ORIG["unit"], "RESTART#0")
check("7c-3d-iv  NOT CLAIMED: nothing recovered the dead process's pending "
      "state. A fresh checkpointer over the same scope starts at zero and "
      "knows nothing about RESTART#1 until it is told",
      (round(_c5.recorded, 6), len(_c5.pending)), (0.0, 0))

# --- (e) READ-BACK CONFIRMATION IS WHAT MAKES `wrote` MEAN SOMETHING -------
# `fh.write` returning is not evidence that the file holds a record: the
# merged-line defect above is exactly a successful write that produced none.
_pB5 = fresh()
_c6 = _cp(_pB5, prefix="RB", min_usd=0.0)
_real_ser = J._serialize_entry
_before_rb = J.JOURNAL_FAULTS["append:readback_unreadable"]
try:
    J._serialize_entry = lambda payload: b"{not json at all\n"
    _c6.checkpoint(0.30)
finally:
    J._serialize_entry = _real_ser
check("7c-3e  RESTORE: _serialize_entry is the real one again",
      J._serialize_entry is _real_ser, True)
check("7c-3e-i  an append that cannot be parsed back is UNCERTAIN, not wrote",
      _at(_c6.entries, 0)["outcome"], J.APPEND_UNCERTAIN)
check("7c-3e-ii ...so `recorded` does NOT advance -- which is the whole "
      "point: a write that produced no readable entry must not be counted",
      round(_c6.recorded, 6), 0.0)
check("7c-3e-iii ...the file really holds no readable entry, so the refusal "
      "is right rather than merely cautious",
      (len(J.read_entries(_pB5)), _usd_all(_pB5)), (0, 0.0))
check("7c-3e-iv  ...it is a NAMED fault",
      J.JOURNAL_FAULTS["append:readback_unreadable"] - _before_rb, 1)
check("7c-3e-v   ...and the delta stays PENDING under its frozen id",
      (len(_c6.pending), _at(_c6.pending, 0).unit), (1, "RB#0"))
_c6.checkpoint(0.30)
check("7c-3e-vi  ...so a retry lands it, and the file and the class agree",
      (round(_c6.recorded, 6), _usd_all(_pB5)), (0.30, 0.30))
# --- (f) THE TWO HALVES ARE INDEPENDENT, AND THIS IS THE SECOND ONE -------
# Boundary recovery PREVENTS the merge; the read-back CATCHES one if it
# happens anyway. Neither subsumes the other, and this check is what says so:
# with recovery disabled, a merged write must be refused rather than confirmed.
#
# IT PATCHES `_needs_boundary`, which is the narrowest seam that removes the
# first half without touching the second -- and it is the same plant the revert
# matrix applies to the module, driven here so the property has a standing
# check rather than living only in a harness nobody runs.
_pB6 = fresh()
_write_raw(_pB6, b'{"entry_id": "half", "usd": 5.0, "bud')
_real_nb = J._needs_boundary
_c7 = _cp(_pB6, prefix="MERGE", min_usd=0.0)
try:
    J._needs_boundary = lambda fh: False
    _c7.checkpoint(0.30)
finally:
    J._needs_boundary = _real_nb
check("7c-3f  RESTORE: _needs_boundary is the real one again",
      J._needs_boundary is _real_nb, True)
check("7c-3f-i  with recovery disabled the write MERGES and the read-back "
      "refuses to confirm it -- the JSON half of `fragment + json` parses "
      "perfectly on its own, which is why the read-back reads the LINE",
      _at(_c7.entries, 0)["outcome"], J.APPEND_UNCERTAIN)
check("7c-3f-ii ...so `recorded` does not advance for money the file cannot "
      "return", round(_c7.recorded, 6), 0.0)
check("7c-3f-iii ...and the file really cannot return it",
      (len(J.read_entries(_pB6)), _usd_all(_pB6)), (0, 0.0))
# ** THIS CHECK'S FIRST DRAFT WAS A TAUTOLOGY AND IS RECORDED AS ONE.** It read
# `(lambda: (...))() is not None`, which is true of any lambda that returns a
# tuple -- the `or True` shape this project forbids, written into the very
# section that exists to prove a control discriminates. It asserts the outcome
# now, on the SAME fragment shape, with recovery left ON.
_pB7 = fresh()
_write_raw(_pB7, b'{"entry_id": "half", "usd": 5.0, "bud')
_c8 = _cp(_pB7, prefix="OK", min_usd=0.0)
_c8.checkpoint(0.30)
check("7c-3f-iv  ...and with recovery ON the SAME fragment shape is CONFIRMED "
      "-- so the refusal above is about the merge, not about the fragment "
      "being present",
      (_at(_c8.entries, 0)["outcome"], round(_c8.recorded, 6)),
      (J.APPEND_WROTE, 0.30))
check("7c-3f-v   ...and the file can return it",
      (len(J.read_entries(_pB7)), _usd_all(_pB7)), (1, 0.30))

check("7c-3e-vii ...and the unreadable bytes are STILL on disk: this module "
      "wrote them, did not decide they were wrong, and left the evidence",
      any(b"not json at all" in ln for ln in _lines_on_disk(_pB5)), True)


print()
print("=" * 74)
print("7d. THE CHECKPOINTER: SEGMENTED run ENTRIES, AND NO DOUBLE COUNT")
print("=" * 74)

# `record_run` writes ONE entry per invocation from a `finally`, and its own
# docstring names what that costs: "an invocation killed before it reaches its
# recording point contributes NOTHING to the next one's cap". A `finally`
# covers everything that UNWINDS -- a clean return, an exception, a Ctrl-C --
# and covers nothing that does not. `RunSpendCheckpointer` closes the rest by
# writing DELTAS as a run proceeds.
#
# THE ENTRIES ARE DELTAS BECAUSE `total()` SUMS. A checkpoint carrying the
# running TOTAL would be re-counted by every later one: a $1.20 run written as
# five cumulative checkpoints seeds the next session at $3.60. Every check
# below is ultimately about that one property.


def _usd(path):
    return round(J.total(spend.SPEND_BUDGET_CAMPAIGN, path=path).usd, 6)


# --- 7d-a  the deltas sum to what was measured -----------------------------
_p7d = fresh()
_clock = {"t": 0.0}
_c = _cp(_p7d, clock=lambda: _clock["t"])
_spent = 0.0
for _i in range(20):
    _spent += 0.06
    _c.checkpoint(_spent)
check("7d-a  twenty pairs at $0.06 with a $0.25 threshold write four "
      "checkpoints, not twenty -- the threshold is what makes this cheap",
      len(_c.entries), 4)
check("7d-a  ...and what is on disk is what has been recorded, not what has "
      "been spent: the tail is still owed",
      (_usd(_p7d), round(_c.recorded, 6)), (1.2, 1.2))
_c.finalize(_spent)
check("7d-a  finalize writes the REMAINDER and the journal then equals the "
      "measured total exactly",
      _usd(_p7d), round(_spent, 6))
check("7d-a  ...and the deltas sum to it, which is the property `total()` "
      "summing depends on",
      round(sum(e["usd"] for e in _c.entries), 6), round(_spent, 6))
check("7d-a  every unit is distinct, so no entry_id collides inside one run",
      len({e["unit"] for e in _c.entries}), len(_c.entries))

# --- 7d-b  CONTROL: cumulative entries would triple the total --------------
# The rejected design, driven rather than argued. It is what makes 7d-a a
# statement about DELTAS rather than about a number that happened to match.
_p7db = fresh()
for _seq, _amount in enumerate((0.30, 0.60, 0.90, 1.20)):
    J.record_run(spend.SPEND_BUDGET_CAMPAIGN, spend.SPEND_SOURCE_RAGAS_JUDGE,
                 "/out", f"CUMULATIVE#{_seq}", _amount, "judge", path=_p7db)
check("7d-b  CONTROL: cumulative checkpoints of a $1.20 run seed the next "
      "session at $3.00 -- which is why the entries are deltas",
      _usd(_p7db), 3.0)

# --- 7d-c  finalize is idempotent, and writes even a zero delta ------------
_p7dc = fresh()
_c2 = _cp(_p7dc)
check("7d-c  a run that spent nothing still leaves a terminal entry, so "
      "'this run recorded itself' stays answerable -- which is exactly what "
      "the one-entry-per-invocation writer it replaces did",
      (_c2.finalize(0.0), len(J.read_entries(_p7dc))), (True, 1))
check("7d-c  ...and a second finalize writes nothing, so a `finally` inside "
      "another `finally` cannot produce two terminal entries",
      (_c2.finalize(0.0), len(J.read_entries(_p7dc))), (False, 1))
check("7d-c  ...and a checkpoint AFTER finalize is refused and counted",
      (_c2.checkpoint(5.0), _usd(_p7dc),
       any(k.startswith("checkpoint:after_finalize") for k in J.JOURNAL_FAULTS)),
      (False, 0.0, True))

# --- 7d-d  the time backstop -----------------------------------------------
# The money threshold is the BOUND; this is the liveness backstop for a run
# that is slow and cheap, which would otherwise record nothing until the end.
_p7dd = fresh()
_clock2 = {"t": 0.0}
_c3 = _cp(_p7dd, min_usd=1000.0, min_seconds=30.0, clock=lambda: _clock2["t"])
check("7d-d  under the money threshold and inside the window, nothing is "
      "written", (_c3.checkpoint(0.01), _usd(_p7dd)), (False, 0.0))
_clock2["t"] = 31.0
check("7d-d  ...and past the window it writes, even though the money "
      "threshold is nowhere near",
      (_c3.checkpoint(0.02), _usd(_p7dd)), (True, 0.02))
_clock2["t"] = 32.0
check("7d-d  ...and a zero delta writes nothing even past the window: there "
      "is nothing to record",
      (_c3.checkpoint(0.02), len(J.read_entries(_p7dd))), (False, 1))

# --- 7d-e  a ledger that goes backwards is counted, never written ----------
# Only reachable through `SPEND_LEDGER.reset()` mid-run, which no harness does.
# Counted rather than clamped: writing a negative into a cap is worse than not
# writing, and a backwards ledger is a defect somewhere else.
_p7de = fresh()
_c4 = _cp(_p7de, min_usd=0.0)
_c4.checkpoint(1.0)
_before_neg = sum(v for k, v in J.JOURNAL_FAULTS.items()
                  if k == "checkpoint:negative_delta")
check("7d-e  a measured total that DECREASED writes nothing",
      (_c4.checkpoint(0.5), _usd(_p7de)), (False, 1.0))
check("7d-e  ...and it is counted rather than silent",
      J.JOURNAL_FAULTS["checkpoint:negative_delta"] - _before_neg, 1)
for _bad, _label in ((None, "None"), ("1.0", "a string"), (True, "a bool"),
                     (float("nan"), "NaN")):
    check(f"7d-e  {_label} as a measured total writes nothing and does not "
          f"raise", (_c4.checkpoint(_bad), _usd(_p7de)), (False, 1.0))
check("7d-e  ...and each bad shape is counted under its own key",
      sorted({k for k in J.JOURNAL_FAULTS
              if k.startswith("checkpoint:bad_measured")}),
      ["checkpoint:bad_measured:NoneType", "checkpoint:bad_measured:bool",
       "checkpoint:bad_measured:nan", "checkpoint:bad_measured:str"])

# --- 7d-f  THE FOUR-SCENARIO MATRIX ---------------------------------------
# The design constraint, driven rather than asserted: no double counting across
# clean completion, exception abort, hard kill then re-run, and hard kill then
# resume. The kill is simulated HERE by abandoning a checkpointer without
# finalizing it -- which is exactly what an un-unwound process leaves behind --
# and driven with a REAL SIGKILL in tests/test_spend_hard_kill_journaling.py.
_p7df = fresh()
# (1) clean completion
_s1 = _cp(_p7df, prefix="RUN_A", min_usd=0.25)
for _i in range(1, 9):
    _s1.checkpoint(_i * 0.10)
_s1.finalize(0.80)
check("7d-f  (1) clean completion records exactly what it spent",
      _usd(_p7df), 0.80)
# (2) exception abort -- the finally still runs
_s2 = _cp(_p7df, prefix="RUN_B", min_usd=0.25)
try:
    for _i in range(1, 6):
        _s2.checkpoint(_i * 0.10)
    raise RuntimeError("boom")
except RuntimeError:
    pass
finally:
    _s2.finalize(0.50)
check("7d-f  (2) an exception abort adds its own spend and nothing else",
      _usd(_p7df), 1.30)
# (3) hard kill -- checkpoints on disk, no finalize
_s3 = _cp(_p7df, prefix="RUN_C", min_usd=0.25)
for _i in range(1, 8):
    _s3.checkpoint(_i * 0.10)
_killed_recorded = _usd(_p7df)
# DERIVED FROM THE CHECKPOINTER RATHER THAN RETYPED. The first version of this
# check hard-coded 0.50 and the answer is 0.60 -- with a $0.25 threshold the
# writes land at $0.30 and $0.60 and the $0.70 reading is under the threshold.
# A hand-computed expectation beside a thresholded writer is a second
# implementation of the thresholds.
check("7d-f  (3) a killed run's checkpoints survive it -- this is the whole "
      "item: before the checkpointer this contributed 0",
      round(_killed_recorded - 1.30, 6), round(_s3.recorded, 6))
check("7d-f  (3) non-degeneracy: the killed run really had recorded "
      "something, and really lost a TAIL -- which is the residual this "
      "mechanism bounds rather than removes",
      (_s3.recorded > 0, _s3.recorded < 0.70), (True, True))
# ...then a fresh re-run in the SAME output directory
_s4 = _cp(_p7df, prefix="RUN_D", min_usd=0.25)
for _i in range(1, 5):
    _s4.checkpoint(_i * 0.10)
_s4.finalize(0.40)
check("7d-f  (3) the re-run's spend is ADDED, not merged with the killed "
      "run's -- a new prefix means new entry_ids",
      _usd(_p7df), round(_killed_recorded + 0.40, 6))
# (4) hard kill then --resume: same shape, and the resume does not re-judge
# the pairs it carries forward, so the money is not spent twice either.
_s5 = _cp(_p7df, prefix="RUN_E", min_usd=0.25)
_s5.finalize(0.20)
check("7d-f  (4) a resume after a kill adds only what IT spent",
      _usd(_p7df), round(_killed_recorded + 0.60, 6))
check("7d-f  every entry is distinct, so the total is a sum of charges and "
      "not of re-reads",
      len({e.get("entry_id") for e in J.read_entries(_p7df)}),
      len(J.read_entries(_p7df)))
# CONTROL: the collision that WOULD double-count is refused, not written twice.
_s6 = _cp(_p7df, prefix="RUN_A", min_usd=0.0)
check("7d-f  CONTROL: a checkpointer reusing a prefix computes ids that are "
      "already there, and `append` REFUSES them -- so the failure mode of a "
      "clock collision is UNDER-recording, never double counting",
      (_s6.checkpoint(0.10), _usd(_p7df)),
      (False, round(_killed_recorded + 0.60, 6)))

# --- 7d-g  the kind is `run`, deliberately ---------------------------------
check("7d-g  segments are recorded as `run` entries",
      sorted({e.get("kind") for e in J.read_entries(_p7df)}),
      [J.ENTRY_KIND_RUN])
# A FOURTH KIND WOULD BE SILENTLY SKIPPED BY ANY BUILD THAT PREDATES IT, and
# `total()` counts-and-skips an unknown kind -- under-recording, the unsafe
# direction. Reusing `run` means an older reader sums these correctly.
_p7dg = fresh()
J.append({"entry_id": "future1", "kind": "segment", "budget":
          spend.SPEND_BUDGET_CAMPAIGN, "source": spend.SPEND_SOURCE_RAGAS_JUDGE,
          "scope": "/out", "unit": "u", "usd": 5.0}, path=_p7dg)
check("7d-g  CONTROL: a kind this build does not know is skipped, which is "
       "why a fourth kind was rejected -- an older reader would drop every "
       "segment", _usd(_p7dg), 0.0)
check("7d-g  ...and the skip is counted rather than silent",
      J.JOURNAL_FAULTS.get("kind:segment", 0) >= 1, True)
# AND `batch` WAS THE OTHER IDEMPOTENT KIND AND IS WRONG HERE: `total()` gives
# it migration-coverage handling built for the rater's Batch API.
check("7d-g  the checkpointer never writes a `batch` entry, whose "
      "covers_batch_ids handling has no meaning for a ragas segment",
      J.ENTRY_KIND_BATCH in {e.get("kind") for e in J.read_entries(_p7df)},
      False)

# --- 7d-h  a refused write does not leave the delta owed -------------------
# `append` returns False both for a refused DUPLICATE and for an OSError it has
# already counted. Treating either as "still owed" would make the next
# checkpoint carry the same delta and, if THAT one landed, record it twice.
#
# ** THE FIRST VERSION OF THIS CHECK TESTED NOTHING, AND THE REVERT MATRIX IS
# ** WHAT FOUND IT. It pointed the checkpointer at `TMP/no-such-dir/x/j.jsonl`
# ** and called it "a write that is refused" -- but `append` does
# ** `os.makedirs(parent, exist_ok=True)`, so the path was created and the
# ** write SUCCEEDED. The check passed, and the revert that makes `recorded`
# ** advance only on a successful write was reported as MISSED. A check whose
# ** label says "refused" over a path that succeeds is not a weak check; it is
# ** a check of something else.
#
# THE DUPLICATE IS THE REAL CASE ANYWAY. It is what `entry_id` idempotency
# produces in production -- a re-collected batch, or the stamp collision
# section 7d-f controls for -- so this drives that rather than a filesystem
# failure.
_p7dh = fresh()
_first = _cp(_p7dh, prefix="DUP", min_usd=0.0)
_first.checkpoint(1.0)
check("7d-h  precondition: the first checkpointer wrote DUP#0",
      (_usd(_p7dh), len(J.read_entries(_p7dh))), (1.0, 1))
_second = _cp(_p7dh, prefix="DUP", min_usd=0.0)
# ** THE RETURN MEANS "CONFIRMED ANYTHING", NOT "WROTE A LINE", AND THAT IS A
# ** DELIBERATE CHANGE. Before the repair this asserted False, because `append`
# ** returned False for a duplicate and the checkpointer forwarded it. A
# ** duplicate whose id, charge identity AND amount all match is a
# ** CONFIRMATION -- the money is in the file -- so the honest answer is True,
# ** and the OUTCOME is what says which of the two happened.
check("7d-h  a second checkpointer reusing the prefix CONFIRMS rather than "
      "writing: its DUP#0 is a duplicate, and a duplicate is proof the money "
      "is recorded", _second.checkpoint(1.0), True)
check("7d-h  ...and the recorded outcome says DUPLICATE, not WROTE, so the "
      "bool above is not hiding a second line",
      [e["outcome"] for e in _second.entries], [J.APPEND_DUPLICATE])
check("7d-h  ...and no second line was written",
      len(J.read_entries(_p7dh)), 1)
check("7d-h  ...and `recorded` advances, so the confirmed delta is not still "
      "owed", round(_second.recorded, 6), 1.0)
_second.checkpoint(2.0)
check("7d-h  ...so the next checkpoint carries only the NEW dollar. Leaving "
      "the delta owed would write $2.00 here and the journal would read "
      "$3.00 for $2.00 of spend -- which is the double count this whole "
      "section is about", _usd(_p7dh), 2.0)


print()
print("=" * 74)
print("7c. NO TEST MAY READ THE PRODUCTION JOURNAL BY ACCIDENT")
print("=" * 74)

# **THIS SECTION EXISTS BECAUSE THE HAZARD WAS FOUND TWICE IN ONE PASS.**
# `ragas_harness.main()` WROTE to the real journal from a driven test, and two
# spend tests READ it -- both passed, because the file did not exist yet, and
# both would have started failing the day a real judge session created one. A
# test that breaks because production data appeared is the silent-pass shape
# this project removes, and neither instance was visible by reading.
#
# THE SCAN IS OVER CALL NODES with keywords, not a substring: the prose above
# names `rater_spend_before` several times and a grep would report this file's
# own argument as an offender -- the shape this project has met four times.
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_JOURNAL_READERS = ("rater_spend_before",)


def _unguarded_calls(src):
    """Calls to a journal reader that name no explicit journal path."""
    out = []
    for node in _ast.walk(_ast.parse(src)):
        if not isinstance(node, _ast.Call):
            continue
        name = (node.func.attr if isinstance(node.func, _ast.Attribute)
                else node.func.id if isinstance(node.func, _ast.Name) else None)
        if name not in _JOURNAL_READERS:
            continue
        if not any(k.arg == "journal" for k in node.keywords):
            out.append((name, node.lineno))
    return out


_offenders = {}
for _name in sorted(os.listdir(_TESTS_DIR)):
    if not _name.endswith(".py"):
        continue
    with io.open(os.path.join(_TESTS_DIR, _name), encoding="utf-8") as _fh:
        try:
            _bad = _unguarded_calls(_fh.read())
        except SyntaxError:
            _bad = []
    if _bad:
        _offenders[_name] = _bad
check("7h  no test calls a journal reader without naming a journal path, so "
      "none of them reads the production record",
      _offenders, {})
check("7h-i  non-degeneracy: the scan DOES find the guarded calls, so 7h is "
      "not passing over an empty corpus",
      sum(1 for _name in os.listdir(_TESTS_DIR) if _name.endswith(".py")
          and any(k in io.open(os.path.join(_TESTS_DIR, _name),
                               encoding="utf-8").read()
                  for k in _JOURNAL_READERS)) >= 3, True)
check("7h-ii ...and the scan can answer non-empty: a call with no journal "
      "keyword IS reported",
      _unguarded_calls("rater_spend_before({})\n"),
      [("rater_spend_before", 1)])
check("7h-iii ...while one that names it is not",
      _unguarded_calls("rater_spend_before({}, journal=p)\n"), [])
check("7h-iv ...and a MENTION in prose is not a call, which is why this is "
      "an AST walk rather than a grep",
      _unguarded_calls('"""rater_spend_before is discussed here."""\n'), [])


print()
print("=" * 74)
print("7d-j. THE FOUR-SCENARIO MATRIX, RE-DRIVEN ON THE REPAIRED CLASS")
print("=" * 74)

# Section 7d-f drove this matrix before the confirmed-write repair, and every
# total it asserts came from `total()` over the whole file. This re-drives all
# four on the repaired class and asserts each from the SCOPED read-back --
# `confirmed_usd_for_scope`, narrowed to one invocation's own `prefix#` units
# -- which is the oracle `finalize` itself uses and the only one that survives
# a shared journal.
#
# THE CONSTRAINT IS UNCHANGED: no double counting across clean completion,
# exception abort, hard kill then re-run, and hard kill then resume. What is
# NEW is that each scenario also asserts the class and the file AGREE, which
# is precisely what the repaired defect broke.
_pM = fresh()


def _scoped(prefix):
    return round(J.confirmed_usd_for_scope(
        spend.SPEND_BUDGET_CAMPAIGN, spend.SPEND_SOURCE_RAGAS_JUDGE, "/out",
        unit_prefix=f"{prefix}#", path=_pM), 6)


# (1) CLEAN COMPLETION
_m1 = _cp(_pM, prefix="M_CLEAN", min_usd=0.25)
for _i in range(1, 9):
    _m1.checkpoint(_i * 0.10)
_m1.finalize(0.80)
check("7d-j-1  clean completion: the FILE holds exactly what was spent",
      _scoped("M_CLEAN"), 0.80)
check("7d-j-1-i ...and the class agrees with the file, with no residual",
      (round(_m1.recorded, 6), round(_m1.residual_usd, 6)), (0.80, 0.0))

# (2) EXCEPTION ABORT -- the finally still runs
_m2 = _cp(_pM, prefix="M_ABORT", min_usd=0.25)
try:
    for _i in range(1, 6):
        _m2.checkpoint(_i * 0.10)
    raise RuntimeError("boom")
except RuntimeError:
    pass
finally:
    _m2.finalize(0.50)
check("7d-j-2  exception abort: the FILE holds what was spent before the raise",
      _scoped("M_ABORT"), 0.50)
check("7d-j-2-i ...and adds nothing to the previous scenario's total",
      _scoped("M_CLEAN"), 0.80)

# (3) HARD KILL -- checkpoints on disk, no finalize -- then a FRESH re-run
_m3 = _cp(_pM, prefix="M_KILLED", min_usd=0.25)
for _i in range(1, 8):
    _m3.checkpoint(_i * 0.10)
_killed = _scoped("M_KILLED")
check("7d-j-3  a killed run's confirmed checkpoints survive it",
      _killed > 0, True)
check("7d-j-3-i ...and the class's own reading matches the file exactly, "
      "which is what the repair guarantees at every instant and not only at "
      "finalization", round(_m3.recorded, 6), _killed)
check("7d-j-3-ii ...and a TAIL was genuinely lost, so 7d-j-3 is not satisfied "
      "by a run that recorded everything", _killed < 0.70, True)
_m4 = _cp(_pM, prefix="M_RERUN", min_usd=0.25)
for _i in range(1, 5):
    _m4.checkpoint(_i * 0.10)
_m4.finalize(0.40)
check("7d-j-3-iii the re-run's spend is its OWN, added rather than merged",
      (_scoped("M_RERUN"), _scoped("M_KILLED")), (0.40, _killed))

# (4) HARD KILL THEN --resume
_m5 = _cp(_pM, prefix="M_RESUME", min_usd=0.25)
_m5.finalize(0.20)
check("7d-j-4  a resume after a kill records only what IT spent",
      _scoped("M_RESUME"), 0.20)
check("7d-j-4-i  and the whole file is the sum of the five, with no id "
      "counted twice",
      round(sum(_scoped(p) for p in ("M_CLEAN", "M_ABORT", "M_KILLED",
                                     "M_RERUN", "M_RESUME")), 6),
      _usd_all(_pM))
check("7d-j-4-ii ...and every entry_id in the file is distinct, which is what "
      "'not counted twice' means mechanically",
      len({e.get("entry_id") for e in J.read_entries(_pM)}),
      len(J.read_entries(_pM)))


print()
print("=" * 74)
print("7d-i. A DELTA IS RECORDED ONLY WHEN THE WRITE IS CONFIRMED")
print("=" * 74)

# ** THE DEFECT, REPRODUCED BEFORE IT WAS REPAIRED. **
#
# `RunSpendCheckpointer` advanced its recorded total on `append`'s False --
# which is returned for "the id is already there, so the money IS recorded"
# AND for "the write failed, so the money is NOT recorded". A $0.30 delta whose
# append failed was never retried; the finalize computed its remainder against
# the already-advanced total and wrote $0.00; the class reported $0.50 against
# a journal holding $0.20. Nothing raised and no counter moved.
#
# EVERY CHECK BELOW ASSERTS A SUM READ BACK FROM THE FILE, never a return
# value and never the class's own counters -- because counters are precisely
# what the defect got wrong, and a total that verified itself against its own
# arithmetic would have reported the lost $0.30 as recorded just as
# confidently.


def _mine(path, prefix="STAMP1"):
    """What the FILE says this invocation recorded. The only oracle here."""
    return round(J.confirmed_usd_for_scope(
        spend.SPEND_BUDGET_CAMPAIGN, spend.SPEND_SOURCE_RAGAS_JUDGE,
        "/out", unit_prefix=f"{prefix}#", path=path), 6)


class _Outcomes(object):
    """Forces `append_with_outcome` to a scripted answer, then restores it.

    A CONTEXT MANAGER SO THE RESTORE IS UNCONDITIONAL, and the restore is
    ASSERTED by 7d-i-z below: a leaked patch would make every later section
    measure a stand-in.
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self._real = None

    def __enter__(self):
        self._real = J.append_with_outcome

        def _patched(entry, path=None):
            self.calls.append((entry.get("unit"), entry.get("usd")))
            forced = self.script.pop(0) if self.script else None
            if forced is None:
                return self._real(entry, path=path)
            if forced == "PERSIST_THEN_FAIL":
                # THE UNCERTAIN CASE, and it is the honest simulation: the
                # bytes really go in, and the caller really is told it cannot
                # be sure. A stand-in that skipped the write would let a
                # retry-under-a-new-id look safe when it is not.
                self._real(entry, path=path)
                return J.APPEND_UNCERTAIN
            return forced

        J.append_with_outcome = _patched
        return self

    def __exit__(self, *exc):
        J.append_with_outcome = self._real
        return False


# --- (1) FAILURE BEFORE THE WRITE PERSISTS ---------------------------------
_pA = fresh()
_cA = _cp(_pA, min_usd=0.0)
_cA.checkpoint(0.20)
with _Outcomes([J.APPEND_FAILED]):
    _cA.checkpoint(0.50)
check("7d-i-a  a delta whose write FAILED is not recorded",
      (_mine(_pA), round(_cA.recorded, 6)), (0.20, 0.20))
check("7d-i-a-i ...and the class and the FILE agree, which is the whole "
      "repair: before it these read 0.50 and 0.20",
      round(_cA.recorded, 6), _mine(_pA))
check("7d-i-a-ii ...and the money is held as PENDING with its amount frozen, "
      "not dropped",
      (len(_cA.pending), round(_cA.unconfirmed, 6)), (1, 0.30))

# --- (2) FAILURE AFTER PERSISTENCE, BEFORE CONFIRMATION (uncertain) --------
# The bytes are in the file and the writer cannot prove it. A caller that read
# this as "nothing happened" and retried under a NEW id would record the same
# money twice; the frozen id and amount are what make the retry resolve to a
# duplicate instead.
_pB = fresh()
_cB = _cp(_pB, min_usd=0.0)
with _Outcomes(["PERSIST_THEN_FAIL"]):
    _cB.checkpoint(0.40)
check("7d-i-b  the line IS on disk even though the attempt was uncertain",
      _mine(_pB), 0.40)
check("7d-i-b-i ...and the class does NOT advance on it, because it cannot "
      "prove it -- under-reporting is the safe direction here",
      (round(_cB.recorded, 6), len(_cB.pending)), (0.0, 1))
_cB.checkpoint(0.40)
check("7d-i-b-ii the retry under the SAME id and amount resolves to a "
      "DUPLICATE and confirms it",
      (round(_cB.recorded, 6), len(_cB.pending)), (0.40, 0))
check("7d-i-b-iii ...and the money is recorded ONCE, not twice, which is what "
      "the frozen pair buys", (_mine(_pB), len(J.read_entries(_pB))),
      (0.40, 1))

# --- (3) A CONFIRMED DUPLICATE ADVANCES ------------------------------------
_pC = fresh()
_cC = _cp(_pC, min_usd=0.0)
with _Outcomes([J.APPEND_DUPLICATE]):
    _cC.checkpoint(0.15)
check("7d-i-c  a confirmed duplicate ADVANCES the recorded total -- the money "
      "is in the file, put there by an earlier attempt",
      (round(_cC.recorded, 6), len(_cC.pending)), (0.15, 0))

# --- (4) CONFLICT: NAMED FAULT, NEVER ADVANCED PAST ------------------------
# An entry with this id records something ELSE. This charge is not recorded by
# it and never will be, so retrying cannot fix it and inventing a new id would
# abandon the question of which charge the existing entry is.
_pD = fresh()
_cD = _cp(_pD, prefix="DUPPREFIX", min_usd=0.0)
J.append_with_outcome({
    "entry_id": J.entry_id(spend.SPEND_BUDGET_CAMPAIGN,
                           spend.SPEND_SOURCE_RAGAS_JUDGE, "/out",
                           "DUPPREFIX#0"),
    "kind": J.ENTRY_KIND_RUN, "budget": spend.SPEND_BUDGET_CAMPAIGN,
    "source": spend.SPEND_SOURCE_RAGAS_JUDGE, "scope": "/out",
    "unit": "DUPPREFIX#0", "usd": 99.0, "judge_model": "someone else"},
    path=_pD)
_before_conf = J.JOURNAL_FAULTS["checkpoint:conflict_unresolved"]
_cD.checkpoint(0.25)
check("7d-i-d  a CONFLICT does not advance the recorded total",
      round(_cD.recorded, 6), 0.0)
check("7d-i-d-i ...it is a NAMED fault",
      J.JOURNAL_FAULTS["checkpoint:conflict_unresolved"] - _before_conf, 1)
check("7d-i-d-ii ...the delta is held as conflicted rather than pending, so "
      "it is never retried -- a collision does not heal",
      (len(_cD.conflicted), len(_cD.pending), round(_cD.unconfirmed, 6)),
      (1, 0, 0.25))
_cD.checkpoint(0.25)
check("7d-i-d-iii ...and a later checkpoint does not retry it",
      len([e for e in _cD.entries if e["unit"] == "DUPPREFIX#0"]), 1)

# --- (5) RECOVERY ON A LATER CHECKPOINT ------------------------------------
_pE = fresh()
_cE = _cp(_pE, min_usd=0.0)
with _Outcomes([J.APPEND_FAILED]):
    _cE.checkpoint(0.30)
check("7d-i-e  precondition: nothing recorded, one pending",
      (_mine(_pE), len(_cE.pending)), (0.0, 1))
_cE.checkpoint(0.50)
check("7d-i-e-i  a later checkpoint retries the frozen delta AND cuts the new "
      "one, and the file holds both", _mine(_pE), 0.50)
check("7d-i-e-ii ...as TWO entries, because the pending amount was frozen and "
      "the new spend went into its own delta",
      sorted(e["usd"] for e in J.read_entries(_pE)), [0.20, 0.30])
check("7d-i-e-iii ...and the class agrees with the file",
      (round(_cE.recorded, 6), round(_cE.unconfirmed, 6)), (0.50, 0.0))

# --- (6) RECOVERY ONLY AT FINALIZATION -------------------------------------
_pF = fresh()
_cF = _cp(_pF, min_usd=0.0)
with _Outcomes([J.APPEND_FAILED]):
    _cF.checkpoint(0.30)
_cF.finalize(0.30)
check("7d-i-f  a delta that only recovers at finalization is still recorded",
      _mine(_pF), 0.30)
check("7d-i-f-i  ...with no residual and nothing left pending",
      (round(_cF.residual_usd, 6), len(_cF.pending)), (0.0, 0))

# --- (7) PERMANENT FAILURE THROUGH FINALIZATION: THE LOUD RESIDUAL ---------
_pG = fresh()
_cG = _cp(_pG, min_usd=0.0)
_cG.checkpoint(0.20)
_before_res = J.JOURNAL_FAULTS["checkpoint:unconfirmed_residual"]
with _Outcomes([J.APPEND_FAILED, J.APPEND_FAILED, J.APPEND_FAILED]):
    _cG.checkpoint(0.50)
    _cG.finalize(0.50)
check("7d-i-g  THE REVIEWER'S SCENARIO. The class reports what the FILE holds "
      "-- not what it hoped it held",
      (round(_cG.recorded, 6), _mine(_pG)), (0.20, 0.20))
check("7d-i-g-i  ...and the $0.30 is reported as a residual with its exact "
      "amount, never marked recorded", round(_cG.residual_usd, 6), 0.30)
check("7d-i-g-ii ...as a NAMED degradation",
      J.JOURNAL_FAULTS["checkpoint:unconfirmed_residual"] - _before_res, 1)
check("7d-i-g-iii ...and verified_usd is READ BACK from the file rather than "
      "computed, so it cannot agree with a wrong counter",
      round(_cG.verified_usd, 6), _mine(_pG))
check("7d-i-g-iv  BEFORE THE REPAIR this read 0.50 against a journal of 0.20. "
      "The two numbers now agree by construction",
      round(_cG.recorded, 6) == _mine(_pG), True)

# --- (7b) THE VERIFICATION IS AGAINST THE FILE, AND HERE IS WHAT THAT BUYS --
#
# ** THIS SCENARIO EXISTS BECAUSE THE REVERT MATRIX REPORTED A MISS. **
# Reverting `finalize`'s read-back to `self._confirmed_usd` changed NOTHING
# any check could see -- because under the repair the counters and the file
# always agree, which is the repair's own guarantee. A verification that can
# only be exercised when it agrees is not being exercised.
#
# WHAT IT ACTUALLY PROTECTS AGAINST is a divergence the checkpointer CANNOT
# know about: another writer, a truncated file, a line whose schema version
# this build refuses, an amount `confirmed_usd_for_scope` skips. The file is
# the authority and the counters are a model of it, and only reading the file
# can catch the model being wrong.
#
# DRIVEN by removing a confirmed line from the journal after it was written,
# which is the truncation case in its smallest honest form.
_pI = fresh()
_cI = _cp(_pI, min_usd=0.0)
_cI.checkpoint(0.10)
_cI.checkpoint(0.30)
check("7d-i-i  precondition: two confirmed deltas, counters and file agree",
      (round(_cI.recorded, 6), _mine(_pI)), (0.30, 0.30))
_kept = [ln for ln in io.open(_pI, encoding="utf-8").read().splitlines()
         if ln.strip() and "STAMP1#1" not in ln]
with io.open(_pI, "w", encoding="utf-8") as _fh:
    _fh.write("\n".join(_kept) + "\n")
check("7d-i-i-i  a line this invocation had CONFIRMED is now gone from the "
      "file -- something outside this process removed it", _mine(_pI), 0.10)
_before_res_i = J.JOURNAL_FAULTS["checkpoint:unconfirmed_residual"]
_cI.finalize(0.30)
check("7d-i-i-ii finalize READS THE FILE and reports the gap, rather than "
      "confirming its own arithmetic back to itself",
      (round(_cI.verified_usd, 6), round(_cI.residual_usd, 6)), (0.10, 0.20))
check("7d-i-i-iii ...and `recorded` goes DOWN to what the file says, which is "
      "the honest direction and the one a counter-based check cannot reach",
      round(_cI.recorded, 6), 0.10)
check("7d-i-i-iv ...as a NAMED degradation",
      J.JOURNAL_FAULTS["checkpoint:unconfirmed_residual"] - _before_res_i, 1)

# --- (7c) THE SYMMETRIC CASE: THE FILE HOLDS MORE THAN THIS RUN SPENT ------
# Only reachable through a prefix collision. The earlier design note said a
# collision "fails safe", and it does for DOUBLE COUNTING -- the amounts differ,
# so the second attempt is a conflict -- but a reader of this scope's total is
# then handed two runs' money under one invocation's units, and only the
# read-back comparison can see it. Reported rather than passed over.
_pJ = fresh()
J.append_with_outcome(_entry("STAMP1#7", 5.0), path=_pJ)   # somebody else's
_cJ = _cp(_pJ, min_usd=0.0)
_cJ.checkpoint(0.10)
_before_over = J.JOURNAL_FAULTS["checkpoint:overrecorded_scope"]
_cJ.finalize(0.10)
check("7d-i-j  an over-recorded scope is REPORTED, not silently accepted",
      J.JOURNAL_FAULTS["checkpoint:overrecorded_scope"] - _before_over, 1)
check("7d-i-j-i  ...and the residual is negative by the amount the file holds "
      "in excess", round(_cJ.residual_usd, 6), -5.0)
check("7d-i-j-ii ...and a run whose file matches reports NEITHER direction",
      (J.JOURNAL_FAULTS["checkpoint:overrecorded_scope"] - _before_over,
       round(_m1.residual_usd, 6)), (1, 0.0))

# --- (8) PENDING FROZEN WHILE NEW SPEND ACCRUES ----------------------------
# New spend must NOT grow a pending entry: its amount is frozen, because the
# duplicate check that makes a retry safe compares the amount.
_pH = fresh()
_cH = _cp(_pH, min_usd=0.0)
with _Outcomes([J.APPEND_FAILED, J.APPEND_FAILED, J.APPEND_FAILED]):
    _cH.checkpoint(0.10)
    _frozen = _at(_cH.pending, 0)
    _cH.checkpoint(0.35)
check("7d-i-h  the pending delta's amount is UNCHANGED by later spend",
      round(_frozen.usd, 6), 0.10)
check("7d-i-h-i  ...and its unit is unchanged too, so a retry computes the "
      "same entry_id", _frozen.unit, "STAMP1#0")
check("7d-i-h-ii the new spend is a SEPARATE delta with its own id",
      [(p.unit, round(p.usd, 6)) for p in _cH.pending],
      [("STAMP1#0", 0.10), ("STAMP1#1", 0.25)])
_cH.finalize(0.35)
# THE AMOUNTS ARE COMPARED ROUNDED AND THE COUNT EXACTLY. 0.35 - 0.10 is
# 0.24999999999999997 in binary floating point, and a hand-typed 0.25 beside a
# subtraction is a second, wrong implementation of the arithmetic -- the same
# finding the entry-count expectation in 1e produced.
check("7d-i-h-iii ...and when the store recovers BOTH land, as two distinct "
      "entries summing to what was spent",
      (_mine(_pH), sorted(round(e["usd"], 6) for e in J.read_entries(_pH))),
      (0.35, [0.10, 0.25]))
check("7d-i-h-iii-a ...and NO terminal $0 marker was cut, because deltas were "
      "already issued -- the marker exists for a run that offered nothing",
      len(J.read_entries(_pH)), 2)
check("7d-i-h-iv  RETRY ORDER: the frozen pending entry is confirmed BEFORE "
      "the new delta is written",
      [e["unit"] for e in _cH.entries if e["outcome"] == J.APPEND_WROTE][:2],
      ["STAMP1#0", "STAMP1#1"])

# --- (9) SETTLING IS TERMINAL ---------------------------------------------
# A delta counted into the confirmed total twice is silent over-counting -- the
# same class of defect as the one this class was repaired for, pointed the
# other way. Not reachable through the two callers, and guarded anyway.
_pK = fresh()
_cK = _cp(_pK, min_usd=0.0)
_cK.checkpoint(0.40)
_before_re = J.JOURNAL_FAULTS["checkpoint:resettle_ignored"]
_settled = _at(_cK.entries, 0)
_item = _PendingProbe = None
# Reach the settled delta through the public surface: it is gone from
# `pending`, so the only handle is a fresh attempt at the same unit, which the
# journal answers as a duplicate. Re-settling it must not add its money again.
_cK.checkpoint(0.40)
check("7d-i-k  a settled delta is not counted twice by a later confirmation",
      round(_cK.recorded, 6), 0.40)
check("7d-i-k-i  ...and the file agrees", _mine(_pK), 0.40)

# --- THE RESTORE, ASSERTED -------------------------------------------------
_pZ = fresh()
check("7d-i-z  RESTORE: append_with_outcome is the real one again -- a leaked "
      "patch would make every section after this measure a stand-in",
      J.append_with_outcome(_entry("z0", 1.0), path=_pZ), J.APPEND_WROTE)


print()
print("=" * 74)
print("8. ISOLATION")
print("=" * 74)

check("8a  the PRODUCTION journal is byte-unchanged by this run",
      sha256_or_absent(_PROD_JOURNAL), _PROD_BEFORE)
check("8a-i  ...and every path this file wrote is inside the temp tree",
      all(os.path.abspath(p).startswith(os.path.abspath(TMP))
          for p in (_p, _p3, _p3b, _p4, _p5, _p5b, _p6, _p7, _p7b, _root)),
      True)
for _rel, _before in _SRC_BEFORE.items():
    check(f"8b  {_rel} is byte-unchanged",
          sha256_or_absent(os.path.join(_CODE_DIR, _rel)), _before)
check("8b-i  non-degeneracy: the two hashes differ, so 8b is not one file "
      "compared with itself",
      len(set(_SRC_BEFORE.values())), 2)

shutil.rmtree(TMP, ignore_errors=True)
check("8c  the temp tree is removed and asserted gone",
      os.path.exists(TMP), False)


print()
print("=" * 74)
print("SUMMARY")
print("=" * 74)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")

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
Created on Mon Sep 8 2026

@author: ramyalsaffar
"""
