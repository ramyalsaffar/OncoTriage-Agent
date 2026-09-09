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
