"""A run killed with SIGKILL still reports the money it spent.

``spend_journal.record_run``'s own docstring names the gap this closes: "an
invocation killed before it reaches its recording point contributes NOTHING to
the next one's cap". The ragas judge charges per RESPONSE and wrote ONE journal
entry per invocation, from a ``finally`` -- so everything that UNWINDS was
covered (a clean return, a spend stop, a ``KeyboardInterrupt``) and everything
that does not was not. A SIGKILL, an OOM kill and a power loss leave tens of
minutes of billed judging unrecorded, and the next session's cumulative cap
reads as though this one never ran.

``spend_journal.RunSpendCheckpointer`` closes it by writing DELTAS as the run
proceeds. The unit checks for it are ``tests/test_spend_journal.py`` section
7d; THIS file is the half that cannot be written in-process:

  * a REAL ``SIGKILL`` against a REAL subprocess, because a signal that a
    process can observe is not the signal under test -- SIGKILL cannot be
    caught, no ``finally`` runs, and there is no way to simulate that from
    inside the process making the assertion;
  * driving the REAL ``score_all`` rather than the checkpointer alone, so what
    is measured is that the hook is WIRED -- at the right point, after the
    pair's charge is in the ledger -- and not merely that a class works;
  * and a CONTROL that removes the periodic write and shows the same kill
    losing the same spend, which is the only thing that makes the first
    reading mean anything.

NO NETWORK, NO KEYS, NO SPEND. The judge is a stub metric that charges the
ledger and issues no request; ``build_judge`` is never called and no provider
client of any kind is constructed. NO MODEL LOAD --
``ONCOTRIAGE_DEFER_LOCAL_MODELS`` is set above the imports in both this process
and every child. No corpus, no database, no git history, no live server.

**THE PRODUCTION JOURNAL IS NEVER WRITTEN AND NEVER READ.** Every child is
handed ``ONCOTRIAGE_SPEND_JOURNAL`` pointed inside a ``tempfile.mkdtemp`` this
file removes and asserts gone, which is the seam ``spend_journal.journal_path``
documents for exactly this -- and section 4 hashes the real file before and
after. That guard is not decoration: ``tests/test_resume_capture_and_ragas.py``
records driving ``ragas_harness.main()`` ten times and putting ten entries into
the real journal before that variable was set.

NOT in the collision matrix: it writes only inside its own temp tree, and the
two repository files it reads (``oncotriage/spend_journal.py``,
``oncotriage/evaluation/ragas_harness.py``) are written by neither of the
suite's two writers and are sha256-compared at the end. It EXECS NOTHING and
loads no module by location: the child is a SCRIPT written into the temp tree
and run with ``sys.executable``, and the control is that same script given a
flag. Bucket A, ~6 s.

    python tests/test_spend_hard_kill_journaling.py
"""

import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CODE_DIR = os.path.dirname(_TESTS_DIR)
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

# THE PRODUCTION JOURNAL'S PATH IS RESOLVED BEFORE ANYTHING ELSE RUNS and its
# hash taken, so section 4 can say it is untouched. `resolved_journal_path`
# rather than `journal_path`: on a checkout with no sibling data tree the
# default cannot be resolved at all, and that is not a failure of this file.
from oncotriage import spend, spend_journal as J                   # noqa: E402

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


def sha256_or_absent(path):
    if not path or not os.path.isfile(path):
        return "<absent>"
    with io.open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


_PROD_JOURNAL = J.resolved_journal_path()
_PROD_BEFORE = sha256_or_absent(_PROD_JOURNAL)
_SRC_BEFORE = {rel: sha256_or_absent(os.path.join(_CODE_DIR, rel))
               for rel in ("oncotriage/spend_journal.py",
                           "oncotriage/evaluation/ragas_harness.py")}

TMP = tempfile.mkdtemp(prefix="hardkill_")

# ── THE CHILD ────────────────────────────────────────────────────────────
#
# A SCRIPT IN THE TEMP TREE, run with `sys.executable`, and NOT an exec of a
# string or a by-location module load: `tests/test_package_invariants.py`
# section 1c forbids both, unconditionally, and has already caught one test
# file doing the second.
#
# IT PARKS RATHER THAN SLEEPING. The parent waits for a progress file to reach
# a known number of completed pairs and only then signals, so what is asserted
# is a statement about how many pairs were CHARGED rather than about how fast
# this machine is. A sleep would make the reading depend on scheduling, which
# is the flake `tests/test_runner_stop_switch.py` had to remove.
_CHILD = r'''
import asyncio, io, json, os, sys, types

sys.path.insert(0, sys.argv[1])
os.environ["ONCOTRIAGE_DEFER_LOCAL_MODELS"] = "1"
os.environ["ONCOTRIAGE_SPEND_JOURNAL"] = sys.argv[2]

JOURNAL = sys.argv[2]
PROGRESS = sys.argv[3]
MODE = sys.argv[4]              # "checkpointed" | "finally_only"
PAIRS = int(sys.argv[5])
PARK_AFTER = int(sys.argv[6])   # 0 = never park; run to completion
PER_PAIR = float(sys.argv[7])
WORKERS = int(sys.argv[8]) if len(sys.argv) > 8 else 1

from oncotriage import spend, spend_journal as J
from oncotriage.evaluation import ragas_harness as rh

done = {"n": 0}


class Metric(object):
    """Charges the ledger exactly as a judged response does, and calls out."""

    async def ascore(self, **kwargs):
        spend.SPEND_LEDGER.charge_usd(PER_PAIR,
                                      spend.SPEND_SOURCE_RAGAS_JUDGE)
        done["n"] += 1
        with io.open(PROGRESS, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"n": done["n"],
                                 "measured": spend.SPEND_LEDGER.measured}) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        if PARK_AFTER and done["n"] >= PARK_AFTER:
            # PARKED FOREVER. The parent is what ends this process.
            while True:
                await asyncio.sleep(0.05)
        return types.SimpleNamespace(value=0.75)


generation = [rh.GenerationSample("p%d" % i, "NCT%04d" % i, "q%d" % i,
                                  ["ctx"], "assessment %d" % i, True,
                                  "eligible")
              for i in range(PAIRS)]
run = rh.RunInput("/runs/x", {}, [], generation, [])
active = {rh.DATASET_GENERATION: [rh.METRIC_FAITHFULNESS]}
metrics = {rh.METRIC_FAITHFULNESS: Metric()}

cp = J.RunSpendCheckpointer(spend.SPEND_BUDGET_CAMPAIGN,
                            spend.SPEND_SOURCE_RAGAS_JUDGE,
                            "/out", "CHILD_STAMP", "stub-judge",
                            path=JOURNAL, min_usd=PER_PAIR * 2,
                            min_seconds=10 ** 9)
hook = cp.checkpoint if MODE == "checkpointed" else None
try:
    asyncio.run(rh.score_all(run, metrics, WORKERS, active, progress=False,
                             spend_checkpoint=hook))
finally:
    # THE PRE-FIX SHAPE FOR THE CONTROL ARM: one entry, in a finally. It is
    # `record_run` verbatim, which is what shipped before the checkpointer.
    if MODE == "checkpointed":
        cp.finalize(spend.SPEND_LEDGER.measured)
    else:
        J.record_run(spend.SPEND_BUDGET_CAMPAIGN,
                     spend.SPEND_SOURCE_RAGAS_JUDGE, "/out", "CHILD_STAMP",
                     spend.SPEND_LEDGER.measured, "stub-judge", path=JOURNAL)
print("CHILD_DONE")
'''

import ast as _ast5                                              # noqa: E402

_CP_SRC = io.open(os.path.join(_CODE_DIR, "oncotriage",
                                "spend_journal.py"),
                  encoding="utf-8").read()

_CHILD_PATH = os.path.join(TMP, "child_run.py")
with io.open(_CHILD_PATH, "w", encoding="utf-8") as _fh:
    _fh.write(_CHILD)


def _progress_lines(path):
    if not os.path.isfile(path):
        return []
    with io.open(path, encoding="utf-8") as fh:
        return [json.loads(x) for x in fh if x.strip()]


def _journal_usd(path):
    return round(J.total(spend.SPEND_BUDGET_CAMPAIGN, path=path).usd, 6)


def _run_child(tag, mode, pairs, park_after, per_pair=0.10, kill=True,
               timeout=60.0, workers=1):
    """Start a child, wait for ``park_after`` pairs, SIGKILL it. Never raises.

    Returns a dict the checks read. ``waited`` is False when the child did not
    reach the parked count inside ``timeout`` -- a NAMED failure rather than a
    silent pass, because a scenario that never reached its precondition proves
    nothing about the kill.
    """
    d = os.path.join(TMP, tag)
    os.makedirs(d, exist_ok=True)
    journal = os.path.join(d, "spend_journal.jsonl")
    progress = os.path.join(d, "progress.jsonl")
    env = dict(os.environ)
    env["ONCOTRIAGE_SPEND_JOURNAL"] = journal
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.Popen(
        [sys.executable, _CHILD_PATH, _CODE_DIR, journal, progress, mode,
         str(pairs), str(park_after), str(per_pair), str(workers)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=TMP)
    waited = True
    if kill:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(_progress_lines(progress)) >= park_after:
                break
            if proc.poll() is not None:
                break
            time.sleep(0.02)
        else:
            waited = False
        os.kill(proc.pid, signal.SIGKILL)
    out, err = proc.communicate(timeout=timeout)
    return {"journal": journal, "progress": progress, "rc": proc.returncode,
            "waited": waited, "pairs_charged": len(_progress_lines(progress)),
            "measured": (_progress_lines(progress)[-1]["measured"]
                         if _progress_lines(progress) else 0.0),
            "usd": _journal_usd(journal),
            "entries": J.read_entries(journal),
            "stderr": (err or b"").decode("utf-8", "replace")[-400:]}


print("=" * 74)
print("1. A SIGKILLED RUN'S SPEND IS IN THE JOURNAL")
print("=" * 74)

# 12 pairs at $0.10, checkpointing every $0.20, parked after 9 charges. The
# child is then SIGKILLed: no `finally` runs, no terminal entry is written, and
# what is on disk is whatever the checkpoints put there.
_K = _run_child("killed", "checkpointed", pairs=12, park_after=9)
check("1a  the child reached its parked pair count before it was signalled -- "
      "without which every reading below is about a process that had not "
      "started judging", (_K["waited"], _K["pairs_charged"] >= 9), (True, True))
check("1b  it was killed by SIGKILL and did NOT unwind -- returncode -9, and "
      "no `finally` ran", _K["rc"], -signal.SIGKILL)
check("1c  the journal holds the spend the run made before the kill",
      _K["usd"] > 0, True)
check("1d  ...and it is the checkpointed prefix of it, never more than was "
      "charged -- a journal that over-recorded would refuse a later session "
      "for money nobody spent",
      _K["usd"] <= round(_K["measured"], 6), True)
# ── THE BOUND, DERIVED, AND THE FIRST VERSION OF THIS CHECK WAS WRONG IN A
#    WAY WORTH RECORDING. It asserted `usd >= 0.80` from exact decimal
#    arithmetic -- nine charges of $0.10 at a $0.20 threshold "must" write four
#    times -- and the measured answer is $0.70 in three entries. The cause is
#    FLOAT DRIFT: the sixth charge leaves a delta of 0.19999999999999996, which
#    is under the threshold, so that checkpoint defers to the seventh. It is
#    harmless -- a deferral of at most one pair, self-correcting at the next
#    comparison -- but it means a hand-computed entry count is a second,
#    wrong implementation of the thresholds.
#
#    WHAT THE MECHANISM ACTUALLY PROMISES is that the UNRECORDED tail is
#    bounded, and that is what is asserted: at a kill, everything but (the
#    threshold not yet reached + the pair in flight, whose checkpoint runs
#    after `_score_one` returns and so never ran) is on disk.
_THRESHOLD = 0.10 * 2
_TAIL = round(_K["measured"] - _K["usd"], 6)
check("1e  ...and the unrecorded tail is bounded by one threshold plus the "
      "pair that was in flight -- which is the guarantee, rather than an "
      "entry count a reader would have to recompute",
      _TAIL <= _THRESHOLD + 0.10 + 1e-9, True)
check("1e-i  non-degeneracy: the tail is real, so the bound above is not "
      "satisfied by a run that recorded everything -- a kill DOES lose the "
      "checkpoint interval, which this bounds rather than removes",
      _TAIL > 0, True)
check("1f  every entry is a `run` entry, so an older reader that has never "
      "heard of a segment still sums them",
      sorted({e.get("kind") for e in _K["entries"]}), [J.ENTRY_KIND_RUN])
check("1g  ...and their units are the invocation stamp with a sequence, which "
      "is what makes them distinct inside one run",
      (len({e.get("unit") for e in _K["entries"]}) == len(_K["entries"]),
       all(str(e.get("unit", "")).startswith("CHILD_STAMP#")
           for e in _K["entries"])), (True, True))

print()
print("=" * 74)
print("2. CONTROL: WITHOUT THE PERIODIC WRITE THE SAME KILL LOSES IT ALL")
print("=" * 74)

# THE SAME CHILD, THE SAME PARK, THE SAME SIGNAL -- and `spend_checkpoint=None`
# plus `record_run` in the `finally`, which is the shape that shipped. This is
# the reading that makes section 1 mean something: without it, "the journal has
# money in it" is equally consistent with a mechanism that was never needed.
_C = _run_child("control", "finally_only", pairs=12, park_after=9)
check("2a  the control child reached the same parked count, so the two arms "
      "are comparable", (_C["waited"], _C["pairs_charged"] >= 9), (True, True))
check("2b  it was killed the same way", _C["rc"], -signal.SIGKILL)
check("2c  it CHARGED real money -- the ledger inside the child saw it",
      _C["measured"] > 0, True)
check("2d  ...and the journal records NONE of it: the `finally` never ran, "
      "which is the entire defect", _C["usd"], 0.0)
check("2e  ...and the file holds no entry at all, so the next session's cap "
      "reads as though this run never happened", _C["entries"], [])
check("2f  the two arms differ, which is the whole measurement",
      _K["usd"] > _C["usd"], True)

print()
print("=" * 74)
print("3. THE FOUR-SCENARIO MATRIX, END TO END, WITH REAL PROCESSES")
print("=" * 74)

# Every scenario is driven through the real `score_all` in a real subprocess,
# and each asserts a TOTAL. The constraint is that no scenario double counts.
_CLEAN = _run_child("clean", "checkpointed", pairs=10, park_after=0, kill=False)
check("3a  (1) CLEAN COMPLETION: the child exits 0 having judged every pair",
      (_CLEAN["rc"], _CLEAN["pairs_charged"]), (0, 10))
check("3b  ...and the journal equals what was charged, to the cent -- the "
      "deltas summed and the terminal entry carried the remainder",
      _CLEAN["usd"], round(_CLEAN["measured"], 6))
check("3c  ...and it took more than one entry to get there, so 3b is a "
      "statement about summed deltas rather than about a single write",
      len(_CLEAN["entries"]) > 1, True)

# (3) HARD KILL THEN A FRESH RE-RUN, INTO THE SAME JOURNAL AND THE SAME SCOPE.
# The re-run is a second process, so it takes its own invocation stamp; the
# question is whether the two sum or collide.
_R1 = _run_child("rerun", "checkpointed", pairs=12, park_after=9)
_killed_usd = _R1["usd"]
_d = os.path.join(TMP, "rerun")
_env = dict(os.environ)
_env["ONCOTRIAGE_SPEND_JOURNAL"] = _R1["journal"]
_env["PYTHONDONTWRITEBYTECODE"] = "1"
# A DIFFERENT STAMP IS WHAT SEPARATES THEM, and the child hardcodes
# "CHILD_STAMP" -- so this arm patches the stamp through argv to model a second
# invocation honestly. Reusing the stamp is section 3g's control.
_CHILD2 = _CHILD.replace('"CHILD_STAMP"', 'os.environ["ONC_STAMP"]')
_CHILD2_PATH = os.path.join(TMP, "child_run2.py")
with io.open(_CHILD2_PATH, "w", encoding="utf-8") as _fh:
    _fh.write(_CHILD2)
_env["ONC_STAMP"] = "SECOND_INVOCATION"
_p2 = subprocess.run(
    [sys.executable, _CHILD2_PATH, _CODE_DIR, _R1["journal"],
     os.path.join(_d, "progress2.jsonl"), "checkpointed", "6", "0", "0.10"],
    capture_output=True, env=_env, cwd=TMP, timeout=120)
_rerun_total = _journal_usd(_R1["journal"])
check("3d  (3) HARD KILL THEN RE-RUN: the second invocation completes",
      _p2.returncode, 0)
check("3e  ...and the journal is the killed run's checkpoints PLUS the "
      "re-run's spend -- neither lost nor counted twice",
      round(_rerun_total, 6), round(_killed_usd + 0.60, 6))
check("3f  ...and every entry_id in that file is distinct, which is what "
      "'not counted twice' means mechanically",
      len({e.get("entry_id") for e in J.read_entries(_R1["journal"])}),
      len(J.read_entries(_R1["journal"])))

# (3-control) THE COLLISION, AND IT FAILS SAFE. A second invocation that
# somehow reused the first's stamp computes ids that are already on disk;
# `append` REFUSES them. The failure mode of a clock collision is
# UNDER-recording, never double counting.
_env["ONC_STAMP"] = "CHILD_STAMP"
_before_collide = _journal_usd(_R1["journal"])
_p3 = subprocess.run(
    [sys.executable, _CHILD2_PATH, _CODE_DIR, _R1["journal"],
     os.path.join(_d, "progress3.jsonl"), "checkpointed", "6", "0", "0.10"],
    capture_output=True, env=_env, cwd=TMP, timeout=120)
check("3g  CONTROL: an invocation that REUSED the stamp adds at most the "
       "entries the first one had not written, and never re-adds one it had "
       "-- so a collision under-records rather than double counting",
      _journal_usd(_R1["journal"]) >= _before_collide
      and _journal_usd(_R1["journal"]) < _before_collide + 0.60, True)

# (2) EXCEPTION ABORT and (4) RESUME are the two whose distinguishing feature
# is in-process, so they are driven in tests/test_spend_journal.py 7d-f where
# they can be asserted at the level they differ at. What is driven HERE is the
# one thing that file cannot do.
print("  NOTE  (2) exception abort and (4) kill-then-resume are driven in")
print("        tests/test_spend_journal.py 7d-f: both unwind, so a real")
print("        process adds nothing a checkpointer object does not show.")

print()
print("=" * 74)
print("5. main() IS WIRED TO IT, WHICH THE SCENARIOS ABOVE CANNOT SEE")
print("=" * 74)

# ** THIS SECTION EXISTS BECAUSE THE REVERT MATRIX REPORTED A MISS. **
#
# Sections 1-3 drive `score_all` directly, which is right -- it is where the
# hook fires and where a kill can be aimed. But it means NOTHING above notices
# if `main()` stops handing the hook over: `score_all(spend_checkpoint=None)`
# is a perfectly good call, every scenario here still passes because each
# builds its own checkpointer, and the shipped harness silently goes back to
# one-entry-per-invocation. Planted, and MISSED by every check in this file
# and in tests/test_spend_journal.py.
#
# IT IS STRUCTURAL AND NOT A DRIVE, deliberately. Driving the real `main()` far
# enough to observe a second journal entry needs a run that SPENDS -- the
# checkpointer writes on a dollar threshold -- so a behavioural version would
# have to stub the ledger as well as the judge, and would then be asserting
# about the stub's charges rather than about the wiring. The wiring is a
# property of the source, so the source is what is read.
_RH_PATH = os.path.join(_CODE_DIR, "oncotriage", "evaluation",
                        "ragas_harness.py")
_RH_TREE = _ast5.parse(io.open(_RH_PATH, encoding="utf-8").read())
_MAIN5 = next((n for n in _ast5.walk(_RH_TREE)
               if isinstance(n, _ast5.FunctionDef) and n.name == "main"), None)
check("5a  non-degeneracy: ragas_harness.main() was found, without which "
      "every check below passes over an empty walk", _MAIN5 is not None, True)

_MAIN_TXT5 = _ast5.unparse(_MAIN5) if _MAIN5 else ""
_SCORE_CALLS = [n for n in _ast5.walk(_MAIN5 or _ast5.parse(""))
                if isinstance(n, _ast5.Call)
                and getattr(n.func, "id", None) == "score_all"]
check("5b  main() calls score_all exactly once (probe, so the keyword check "
      "below cannot pass over an absent call)", len(_SCORE_CALLS), 1)
_KW = {k.arg for c in _SCORE_CALLS for k in c.keywords}
check("5c  ...and it passes spend_checkpoint, so the periodic write is wired "
      "to the run that actually spends money",
      "spend_checkpoint" in _KW, True)
_HOOK = [_ast5.unparse(k.value) for c in _SCORE_CALLS for k in c.keywords
         if k.arg == "spend_checkpoint"]
check("5d  ...and what it passes is a checkpointer's own bound method, not "
      "None and not a lambda that swallows it",
      [h for h in _HOOK if h.endswith(".checkpoint")], _HOOK)
check("5e  non-degeneracy: the hook expression is non-empty, so 5d is not "
      "comparing two empty lists", len(_HOOK), 1)
# AND THE TERMINAL ENTRY IS STILL WRITTEN FROM A `finally`. The checkpoints
# fire on a threshold, so an invocation that unwinds always has a tail they did
# not reach -- and one that spent nothing has no checkpoint at all and still
# has to leave a record that it ran.
_FINALLY5 = [n for n in _ast5.walk(_MAIN5 or _ast5.parse(""))
             if isinstance(n, _ast5.Try) and n.finalbody
             and any(isinstance(c, _ast5.Call)
                     and getattr(c.func, "attr", None) == "finalize"
                     for stmt in n.finalbody for c in _ast5.walk(stmt))]
check("5f  finalize is called from a `finally`, so an exception abort and a "
      "Ctrl-C still record their tail", len(_FINALLY5), 1)
check("5g  ...and main() no longer calls record_run directly, which would "
      "write the WHOLE run beside the deltas and double count",
      "spend_journal.record_run(" in _MAIN_TXT5, False)


print()
print("=" * 74)
print("6. WHAT A KILL LOSES AT max_workers > 1, MEASURED NOT BOUNDED")
print("=" * 74)

# ** THE CLASS USED TO CLAIM A BOUND IT DOES NOT HAVE, AND BOTH HALVES OF THE
# ** CLAIM WERE WRONG. **
#
# It said a kill loses "at most one threshold plus the pair in flight".
#
#   * `checkpoint` IS NOT A TIMER. It runs when the caller calls it, which in
#     `score_all` is on PAIR COMPLETION inside the event loop. So
#     `RUN_CHECKPOINT_SECONDS` is not a wall-clock backstop: it is evaluated
#     only at the next completion, and a run whose pairs are all hanging
#     records nothing further however long it waits.
#   * CONCURRENT PAIRS ARE OUTSIDE ANY THRESHOLD. The ledger is charged when a
#     RESPONSE arrives; the checkpoint runs when a PAIR completes. At
#     `--max-workers N` up to N responses can be charged between two
#     completions, so the unconfirmed amount is proportional to N and to the
#     price of a pair.
#
# THIS SECTION MEASURES IT rather than asserting a formula: N workers, all
# charged, all parked, then killed. What is on disk afterwards is the answer.
_W = 4
_PER = 0.10
_CONC = _run_child("concurrent", "checkpointed", pairs=12, park_after=_W,
                   per_pair=_PER, workers=_W)
check("6a  the child reached the parked count with every worker charged",
      (_CONC["waited"], _CONC["pairs_charged"] >= _W), (True, True))
check("6b  it was killed by SIGKILL", _CONC["rc"], -signal.SIGKILL)
_LOST = round(_CONC["measured"] - _CONC["usd"], 6)
print(f"  MEASURED at max_workers={_W}, ${_PER:.2f}/pair, "
      f"threshold ${_PER * 2:.2f}: charged ${_CONC['measured']:.2f}, "
      f"journalled ${_CONC['usd']:.2f}, LOST ${_LOST:.2f}")
check("6c  a kill at max_workers>1 loses REAL money -- the point of the "
      "measurement is that this is not zero, so the class must not promise a "
      "bound that reads as though it were", _LOST > 0, True)
# THE HONEST BOUND, and it is the one the class states now: the sub-threshold
# remainder, plus everything charged by pairs that had not completed. With N
# workers parked mid-flight that second term is up to N charges.
_HONEST = _PER * 2 + _W * _PER
check("6d  ...and it is inside the bound the class actually claims -- the "
      "sub-threshold remainder plus the in-flight charges at this worker "
      "count -- rather than inside the one-pair bound it used to claim",
      _LOST <= _HONEST + 1e-9, True)
check("6e  ...and the one-pair claim is REFUTED at this worker count: the "
      "loss exceeds one threshold plus one pair, which is what the old "
      "docstring promised",
      _LOST > _PER * 2 + _PER, True)
# THE DOCSTRING IS PINNED ON THE TWO SUBSTANTIVE CORRECTIONS rather than on a
# turn of phrase, and on the ABSENCE of the retracted claim. A wording check
# would go red on an edit that changed nothing and green on one that quietly
# reinstated the old promise.
_CP_CLASS = _ast5.unparse(next(
    (n for n in _ast5.walk(_ast5.parse(_CP_SRC))
     if isinstance(n, _ast5.ClassDef) and n.name == "RunSpendCheckpointer"),
    _ast5.parse("class _x: pass")))
check("6f  non-degeneracy: the class and its docstring were found",
      len(_CP_CLASS) > 2000, True)
check("6f-i  the docstring states that checkpoint is NOT a timer, which is "
      "the first half of the correction",
      "IS NOT A TIMER" in _CP_CLASS, True)
check("6f-ii ...and that the loss is NOT bounded by RUN_CHECKPOINT_USD, which "
      "is the second half and the one this section measured",
      "NOT bounded by ``RUN_CHECKPOINT_USD``" in _CP_CLASS, True)
check("6f-iii ...and the retracted claim is GONE: it no longer promises one "
      "threshold plus the pair in flight",
      "plus whatever the request in flight had already been charged"
      in _CP_CLASS, False)
check("6f-iv ...and it points at this section rather than arguing the bound",
      "test_spend_hard_kill_journaling.py" in _CP_CLASS, True)


print()
print("=" * 74)
print("4. ISOLATION")
print("=" * 74)

check("4a  the PRODUCTION journal is byte-unchanged by this run",
      sha256_or_absent(_PROD_JOURNAL), _PROD_BEFORE)
check("4a-i  ...and every journal this file wrote is inside the temp tree",
      all(os.path.abspath(p).startswith(os.path.abspath(TMP))
          for p in (_K["journal"], _C["journal"], _CLEAN["journal"],
                    _R1["journal"])), True)
for _rel, _before in _SRC_BEFORE.items():
    check(f"4b  {_rel} is byte-unchanged",
          sha256_or_absent(os.path.join(_CODE_DIR, _rel)), _before)
check("4b-i  non-degeneracy: the two hashes differ, so 4b is not one file "
      "compared with itself", len(set(_SRC_BEFORE.values())), 2)
check("4c  no child built a provider client -- the stub metric is the only "
      "thing that 'answered', so nothing was billed",
      any("openai" in (_K["stderr"] + _C["stderr"]).lower()
          for _ in (1,)), False)

shutil.rmtree(TMP, ignore_errors=True)
check("4d  the temp tree is removed and asserted gone", os.path.exists(TMP),
      False)

print()
print("=" * 74)
print("SUMMARY")
print("=" * 74)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")
for _f in _FAILURES:
    print(f"  - {_f}")
sys.exit(1 if _RESULTS["failed"] else 0)
