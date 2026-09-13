"""The spend checkpointer's wall-clock backstop.

``RunSpendCheckpointer.checkpoint`` evaluates ``RUN_CHECKPOINT_SECONDS`` only
when it is CALLED, and the ragas harness calls it on PAIR COMPLETION -- so a
run whose pairs all hang, or whose event loop is blocked, offered nothing to
the cross-process journal however long it waited. ``start_backstop`` is a
daemon thread that runs the same threshold decision, under the same lock, on a
wall clock.

**WHAT THE BACKSTOP BOUNDS IS UNRECORDED TIME, NOT STORAGE FAILURE.** Section 3
drives a journal that refuses every write and shows the delta stays PENDING
and is reported at finalization -- the backstop offers it on schedule, and
offering is all it can do.

Sections:
  1. a GENUINELY stalled scoring loop -- the REAL ``ragas_harness.score_all``
     with every pair hanging, and again with the event loop itself blocked --
     each beside a control with no backstop that records nothing;
  2. concurrent fire: completions, ticks and finalization at once, and a
     refusing journal retried by many ticks, with no double count;
  3. shutdown mid-pending, including finalization while a tick holds the lock;
  4. never raises into the judging path;
  5. ``ragas_harness.main()`` starts it, before ``score_all``;
  6. isolation.

NO NETWORK, NO KEYS, NO SPEND: every metric is a stub that charges a local
counter and issues no request; no provider client of any kind is built. NO
MODEL LOAD (``ONCOTRIAGE_DEFER_LOCAL_MODELS`` above the imports). Every journal
is inside a ``tempfile.mkdtemp`` this file removes and asserts gone, and
``ONCOTRIAGE_SPEND_JOURNAL`` is pointed inside it before anything is imported,
so no default can resolve to the production file -- whose hash section 6
compares before and after. EXECS NOTHING and loads no module by location. NOT
in the collision matrix. Bucket A.

    python tests/test_spend_checkpoint_backstop.py
"""

import ast
import asyncio
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types

os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CODE_DIR = os.path.dirname(_TESTS_DIR)
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

TMP = tempfile.mkdtemp(prefix="oncotriage-spend-backstop-")
_ENV_BEFORE = os.environ.get("ONCOTRIAGE_SPEND_JOURNAL")

from oncotriage import spend, spend_journal as J                    # noqa: E402

# THE PRODUCTION PATH IS RESOLVED WITH THE OVERRIDE REMOVED, THEN THE OVERRIDE
# IS POINTED INTO TMP. A missing sibling tree resolves to None, which hashes as
# absent and is not a failure of this file.
os.environ.pop("ONCOTRIAGE_SPEND_JOURNAL", None)
_PROD_JOURNAL = J.resolved_journal_path()
os.environ["ONCOTRIAGE_SPEND_JOURNAL"] = os.path.join(TMP, "default.jsonl")

from oncotriage.evaluation import ragas_harness as rh               # noqa: E402

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


def sha256_or_absent(path):
    if not path or not os.path.isfile(path):
        return "<absent>"
    with io.open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


_PROD_BEFORE = sha256_or_absent(_PROD_JOURNAL)
_SRC = {rel: sha256_or_absent(os.path.join(_CODE_DIR, rel))
        for rel in ("oncotriage/spend_journal.py",
                    "oncotriage/evaluation/ragas_harness.py")}

_SCOPE = "/out"
_N = {"i": 0}


def fresh():
    _N["i"] += 1
    return os.path.join(TMP, f"journal_{_N['i']}.jsonl")


def cp(path, prefix, **kw):
    kw.setdefault("min_usd", 1000.0)
    kw.setdefault("min_seconds", 0.1)
    return J.RunSpendCheckpointer(spend.SPEND_BUDGET_CAMPAIGN,
                                  spend.SPEND_SOURCE_RAGAS_JUDGE, _SCOPE,
                                  prefix, "stub-judge", path=path, **kw)


def usd(path, prefix):
    return round(J.confirmed_usd_for_scope(
        spend.SPEND_BUDGET_CAMPAIGN, spend.SPEND_SOURCE_RAGAS_JUDGE, _SCOPE,
        unit_prefix=f"{prefix}#", path=path), 6)


def lines(path):
    if not os.path.isfile(path):
        return []
    with io.open(path, "rb") as fh:
        return [ln for ln in fh.read().splitlines() if ln.strip()]


def wait_until(predicate, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def faults(prefix):
    return {k: v for k, v in J.JOURNAL_FAULTS.items() if k.startswith(prefix)}


def unwritable(name):
    """A journal path whose PARENT is a regular file: every append fails."""
    parent = os.path.join(TMP, name)
    with io.open(parent, "w") as fh:
        fh.write("not a directory")
    return parent, os.path.join(parent, "journal.jsonl")


def make_writable(parent):
    """Turn the blocking FILE back into a directory. exist_ok, and the revert
    matrix is why: a live backstop thread's own append recreates a missing
    parent (append_with_outcome makes it with exist_ok=True), so between the
    remove and a bare makedirs the thread can win, and the first version of
    this helper died on FileExistsError under load."""
    os.remove(parent)
    os.makedirs(parent, exist_ok=True)


# ── THE REAL score_all, WITH STUB METRICS ────────────────────────────────
def _run(pairs):
    generation = [rh.GenerationSample("p%d" % i, "NCT%04d" % i, "q%d" % i,
                                      ["ctx"], "assessment %d" % i, True,
                                      "eligible")
                  for i in range(pairs)]
    return rh.RunInput("/runs/x", {}, [], generation, [])


_ACTIVE = {rh.DATASET_GENERATION: [rh.METRIC_FAITHFULNESS]}


def drive_stalled(with_backstop, prefix, per_pair=0.30, pairs=2):
    """Every pair charges and then HANGS forever. Returns (journal usd while
    the loop was still stalled, after how long, the checkpointer, path)."""
    path = fresh()
    local = {"usd": 0.0}

    class HangMetric(object):
        async def ascore(self, **kwargs):
            local["usd"] = round(local["usd"] + per_pair, 6)
            await asyncio.Event().wait()                     # never set

    c = cp(path, prefix, min_seconds=0.2)
    if with_backstop:
        check(f"1  [{prefix}] start_backstop returned True",
              c.start_backstop(lambda: local["usd"], interval=0.05), True)
    box = {}

    def loop_thread():
        loop = asyncio.new_event_loop()
        box["loop"] = loop
        task = loop.create_task(rh.score_all(
            _run(pairs), {rh.METRIC_FAITHFULNESS: HangMetric()}, pairs,
            _ACTIVE, progress=False,
            spend_checkpoint=lambda _measured: c.checkpoint(local["usd"])))
        box["task"] = task
        try:
            loop.run_until_complete(task)
        except BaseException:                                # noqa: BLE001
            pass
        finally:
            loop.close()

    t = threading.Thread(target=loop_thread, daemon=True)
    t.start()
    charged = wait_until(lambda: local["usd"] >= per_pair * pairs, 10)
    started = time.monotonic()
    if with_backstop:
        wait_until(lambda: usd(path, prefix) >= per_pair * pairs, 10)
    else:
        # A NEGATIVE CANNOT BE WAITED FOR, so it is given many thresholds.
        time.sleep(1.0)
    observed = usd(path, prefix)
    elapsed = time.monotonic() - started
    still_stalled = t.is_alive()
    box["loop"].call_soon_threadsafe(box["task"].cancel)
    t.join(10)
    return charged, observed, elapsed, still_stalled, c, path, local


section("1. A GENUINELY STALLED SCORING LOOP")

(_ch, _obs, _el, _alive, _c1, _p1, _l1) = drive_stalled(True, "STALL_BACKSTOP")
check("1a  non-degeneracy: every pair was charged before anything was asked",
      _ch, True)
check("1b  the loop was STILL STALLED when the journal was read -- no pair had "
      "completed, so no completion could have written", _alive, True)
check("1c  the backstop OFFERED AND RECORDED the in-flight charges while every "
      "pair hung", _obs, 0.60)
check("1d  ...within a bounded time (min_seconds 0.2 + interval 0.05, read "
      "generously for a loaded runner)", _el < 5.0, True)
_c1.finalize(_l1["usd"])
check("1e  finalize after the stall adds nothing: the backstop's delta was the "
      "whole spend", (usd(_p1, "STALL_BACKSTOP"), _c1.residual_usd or 0.0),
      (0.60, 0.0))

(_ch0, _obs0, _el0, _alive0, _c0, _p0, _l0) = drive_stalled(False,
                                                           "STALL_CONTROL")
check("1f  CONTROL: the identical stall with NO backstop records NOTHING, "
      "which is the defect -- without this reading 1c could be satisfied by a "
      "completion-driven write", (_ch0, _alive0, _obs0), (True, True, 0.0))
_c0.finalize(_l0["usd"])
check("1g  CONTROL: ...and only the unwinding finalize records it, which a "
      "hard kill would never reach", usd(_p0, "STALL_CONTROL"), 0.60)


def drive_blocked_loop(with_backstop, prefix, per_pair=0.30):
    """One pair that charges and then BLOCKS THE EVENT LOOP with a synchronous
    wait, reading the journal from INSIDE the stall."""
    path = fresh()
    local = {"usd": 0.0, "observed": None}
    c = cp(path, prefix, min_seconds=0.1)

    class BlockingMetric(object):
        async def ascore(self, **kwargs):
            local["usd"] = round(local["usd"] + per_pair, 6)
            # SYNCHRONOUS: no await, so no other coroutine and no loop callback
            # can run until this returns. A loop-scheduled backstop could not
            # fire here; a thread can.
            limit = 10.0 if with_backstop else 1.0
            deadline = time.monotonic() + limit
            while time.monotonic() < deadline:
                if usd(path, prefix) >= per_pair:
                    break
                time.sleep(0.01)
            local["observed"] = usd(path, prefix)
            return types.SimpleNamespace(value=0.75)

    if with_backstop:
        c.start_backstop(lambda: local["usd"], interval=0.02)
    asyncio.run(rh.score_all(
        _run(1), {rh.METRIC_FAITHFULNESS: BlockingMetric()}, 1, _ACTIVE,
        progress=False,
        spend_checkpoint=lambda _measured: c.checkpoint(local["usd"])))
    c.finalize(local["usd"])
    return local["observed"], usd(path, prefix), c


_bo, _bfinal, _bc = drive_blocked_loop(True, "BLOCKED_BACKSTOP")
check("1h  with the EVENT LOOP ITSELF BLOCKED, the backstop thread recorded the "
      "charge -- read from inside the blocked coroutine", _bo, 0.30)
_bo0, _bfinal0, _ = drive_blocked_loop(False, "BLOCKED_CONTROL")
check("1i  CONTROL: the same blocked loop with no backstop saw NOTHING recorded "
      "from inside the stall", _bo0, 0.0)
check("1j  ...and both arms end at the same total, so the backstop moved WHEN "
      "the money was recorded, not how much", (_bfinal, _bfinal0), (0.30, 0.30))
check("1k  the backstop thread is stopped and joined by finalize",
      (_bc.backstop_stopped, _bc._backstop.is_alive()), (True, False))


section("2. CONCURRENT FIRE: NO DOUBLE COUNT")

# 2a -- completions, ticks and finalize all at once, with every append slowed so
# the three genuinely interleave.
_p2 = fresh()
_c2 = cp(_p2, "CONC", min_usd=0.05, min_seconds=0.0)
_real_append = J.append_with_outcome


def _slow_append(entry, path=None):
    time.sleep(0.002)
    return _real_append(entry, path=path)


J.append_with_outcome = _slow_append
try:
    _meter = {"usd": 0.0}
    _lock2 = threading.Lock()
    _FINAL = 2.0
    _stop2 = threading.Event()
    check("2a  the backstop starts", _c2.start_backstop(
        lambda: _meter["usd"], interval=0.01), True)

    def charger():
        for _ in range(200):
            with _lock2:
                _meter["usd"] = round(min(_FINAL, _meter["usd"] + 0.01), 6)
            time.sleep(0.0005)

    def completer():
        while not _stop2.is_set():
            _c2.checkpoint(_meter["usd"])
            time.sleep(0.001)

    _threads = [threading.Thread(target=charger)] + [
        threading.Thread(target=completer) for _ in range(4)]
    for _t in _threads:
        _t.start()
    _threads[0].join(30)
    _fin = {}
    _ft = threading.Thread(
        target=lambda: _fin.setdefault("rv", _c2.finalize(_FINAL)))
    _ft.start()
    _ft.join(30)
    _stop2.set()
    for _t in _threads[1:]:
        _t.join(30)
finally:
    J.append_with_outcome = _real_append

_lines_after = len(lines(_p2))
time.sleep(0.2)
_ids = [json.loads(ln)["entry_id"] for ln in lines(_p2)]
check("2b  non-degeneracy: the run produced several segments, so the "
      "interleaving was real", len(_ids) > 3, True)
check("2c  the journal total equals the spend EXACTLY -- no delta was cut twice "
      "across completions, ticks and finalize", usd(_p2, "CONC"), _FINAL)
check("2d  every entry id is unique", len(set(_ids)), len(_ids))
check("2e  finalize reports no residual", round(_c2.residual_usd, 6), 0.0)
check("2f  NOTHING is written after finalize, by a tick or a late completion",
      len(lines(_p2)), _lines_after)
check("2g  the backstop thread is dead and was joined",
      (_c2._backstop.is_alive(), _c2.backstop_stopped), (False, True))
check("2h  non-degeneracy: the backstop actually ticked during the run",
      _c2.backstop_ticks > 0, True)

# 2i -- A REFUSING JOURNAL, RETRIED BY MANY TICKS. The double-count guard is
# that `_delta` measures against ISSUED rather than CONFIRMED, so a pending
# delta is never cut again. Ticks are the one caller that fires repeatedly
# while nothing completes, which is exactly when that guard is exercised.
_parent2, _p2i = unwritable("refusing_2i")
_c2i = cp(_p2i, "REFUSE", min_seconds=0.03)
_c2i.start_backstop(lambda: 0.40, interval=0.01)
check("2i  non-degeneracy: several attempts were made while the journal "
      "refused", wait_until(lambda: len(_c2i.entries) >= 5, 10), True)
check("2j  ...and ONE delta was cut for the money, however many ticks fired",
      (round(_c2i.issued, 6), len(_c2i.pending)), (0.40, 1))
make_writable(_parent2)
check("2k  once the journal accepts writes a tick confirms the pending delta",
      wait_until(lambda: not _c2i.pending, 10), True)
_c2i.finalize(0.40)
check("2l  ...and the file holds the money ONCE", (usd(_p2i, "REFUSE"),
                                                   len(lines(_p2i))),
      (0.40, 1))


section("3. SHUTDOWN MID-PENDING")

# 3a -- pending, then writable, then finalize while the backstop is still alive.
_parent3, _p3 = unwritable("refusing_3a")
_c3 = cp(_p3, "SHUT", min_seconds=0.03)
_c3.start_backstop(lambda: 0.40, interval=0.02)
check("3a  non-degeneracy: the backstop left a PENDING delta",
      wait_until(lambda: len(_c3.pending) == 1, 10), True)
make_writable(_parent3)
_c3.finalize(0.40)
check("3b  finalize confirms the pending delta exactly once",
      (usd(_p3, "SHUT"), len(lines(_p3)), len(_c3.pending),
       round(_c3.residual_usd, 6)), (0.40, 1, 0, 0.0))
check("3c  ...and the backstop thread is stopped and joined",
      (_c3._backstop.is_alive(), _c3.backstop_stopped), (False, True))
time.sleep(0.15)
check("3d  ...and no tick writes after it", len(lines(_p3)), 1)

# 3b -- finalize while the journal STILL refuses: the backstop bounded the
# unrecorded TIME, and could not bound a storage failure.
_parent3b, _p3b = unwritable("refusing_3b")
_c3b = cp(_p3b, "SHUTFAIL", min_seconds=0.03)
_c3b.start_backstop(lambda: 0.40, interval=0.02)
wait_until(lambda: len(_c3b.pending) == 1, 10)
_seen3b = []
_real_out = J.console.out
J.console.out = lambda *a, **k: _seen3b.append(" ".join(str(x) for x in a))
try:
    _c3b.finalize(0.40)
finally:
    J.console.out = _real_out
check("3e  a journal that refuses throughout leaves the money UNCONFIRMED -- "
      "the backstop OFFERED it on time and could not store it",
      (round(_c3b.residual_usd, 6), usd(_p3b, "SHUTFAIL")), (0.40, 0.0))
check("3f  ...and the residual is PRINTED at finalization",
      any("UNCONFIRMED SPEND" in ln for ln in _seen3b), True)
check("3g  ...and the thread is still stopped",
      (_c3b._backstop.is_alive(), _c3b.backstop_stopped), (False, True))

# 3c -- finalize while a TICK HOLDS THE LOCK inside a blocked append.
_p3c = fresh()
_c3c = cp(_p3c, "HELD", min_seconds=0.0)
_gate = threading.Event()
_entered = threading.Event()


def _blocking_append(entry, path=None):
    if threading.current_thread().name.startswith("spend-journal-backstop") \
            and not _gate.is_set():
        _entered.set()
        _gate.wait(10)
    return _real_append(entry, path=path)


J.append_with_outcome = _blocking_append
try:
    _c3c.start_backstop(lambda: 0.25, interval=0.01)
    check("3h  non-degeneracy: a tick is inside an append, holding the lock",
          _entered.wait(10), True)
    _fin3c = {}
    _ft3c = threading.Thread(
        target=lambda: _fin3c.setdefault("rv", _c3c.finalize(0.25)))
    _ft3c.start()
    time.sleep(0.2)
    check("3i  finalize WAITS for the tick rather than racing it",
          _ft3c.is_alive(), True)
    _gate.set()
    _ft3c.join(10)
finally:
    J.append_with_outcome = _real_append
check("3j  ...then completes, with the tick's delta recorded ONCE and no "
      "terminal duplicate", (_ft3c.is_alive(), usd(_p3c, "HELD"),
                             len(lines(_p3c))), (False, 0.25, 1))
check("3k  ...and the thread exited on its own after the lock was released",
      (_c3c._backstop.is_alive(), _c3c.backstop_stopped), (False, True))

# 3d -- A TICK THAT LOSES THE RACE TO finalize. The thread has already returned
# from its wait and read the ledger when finalize runs; it then reaches the
# lock AFTER the terminal entry, holding a reading LARGER than what finalize
# recorded. The in-lock `finalized` check is the only thing between that tick
# and a delta cut after the terminal one. Deterministic: the measure blocks
# until finalize has completed.
_p3d = fresh()
_c3d = cp(_p3d, "RACE", min_seconds=0.0)
_in_measure = threading.Event()
_after_final = threading.Event()


def _racing_measure():
    if not _in_measure.is_set():
        _in_measure.set()
        _after_final.wait(10)
        return 0.50                 # spend the finalize never saw
    return 0.50


_c3d.start_backstop(_racing_measure, interval=0.01)
check("3l  non-degeneracy: the tick is inside measure() when finalize starts",
      _in_measure.wait(10), True)
_waker = threading.Thread(target=lambda: (
    wait_until(lambda: _c3d.finalized, 10), _after_final.set()))
_waker.start()
_c3d.finalize(0.10)
_waker.join(10)
time.sleep(0.1)
check("3m  the late tick cuts NOTHING after the terminal entry: the journal "
      "holds what finalize recorded, once",
      (usd(_p3d, "RACE"), len(lines(_p3d))), (0.10, 1))
check("3n  ...and the thread is gone", _c3d._backstop.is_alive(), False)


section("4. NEVER RAISES INTO THE JUDGING PATH")

_p4 = fresh()
_c4 = cp(_p4, "MEASURE", min_seconds=0.03)
_calls4 = {"n": 0}


def _flaky_measure():
    _calls4["n"] += 1
    if _calls4["n"] <= 3:
        raise ZeroDivisionError("ledger unreadable")
    return 0.30


_f4 = faults("backstop:measure:ZeroDivisionError").get(
    "backstop:measure:ZeroDivisionError", 0)
_c4.start_backstop(_flaky_measure, interval=0.01)
check("4a  a measure that raises is COUNTED, and the thread SURVIVES to record "
      "once it recovers", wait_until(lambda: usd(_p4, "MEASURE") == 0.30, 10),
      True)
check("4b  ...each raise counted under its own key",
      J.JOURNAL_FAULTS.get("backstop:measure:ZeroDivisionError", 0) - _f4 >= 3,
      True)
_dup = J.JOURNAL_FAULTS.get("backstop:already_started", 0)
check("4c  a second start is refused, not a second thread",
      (_c4.start_backstop(_flaky_measure),
       J.JOURNAL_FAULTS.get("backstop:already_started", 0) - _dup), (False, 1))
_c4.finalize(0.30)
_late = J.JOURNAL_FAULTS.get("backstop:start_after_finalize", 0)
check("4d  a start after finalize is refused and counted -- a tick could "
      "otherwise cut a delta after the terminal one",
      (_c4.start_backstop(_flaky_measure),
       J.JOURNAL_FAULTS.get("backstop:start_after_finalize", 0) - _late),
      (False, 1))
_p4e = fresh()
_c4e = J.RunSpendCheckpointer(spend.SPEND_BUDGET_CAMPAIGN,
                              spend.SPEND_SOURCE_RAGAS_JUDGE, _SCOPE, "DEFAULT",
                              "stub-judge", path=_p4e)
_c4e.start_backstop(lambda: 0.0)
check("4e  the DEFAULT tick is derived from the constants, not typed",
      _c4e.backstop_interval,
      J.RUN_CHECKPOINT_SECONDS / J.BACKSTOP_TICKS_PER_THRESHOLD)
_t4e = time.monotonic()
_c4e.finalize(0.0)
check("4f  ...and a 15 s tick does not delay shutdown: the stop event wakes it",
      (time.monotonic() - _t4e < 2.0, _c4e.backstop_stopped), (True, True))
_c4g = cp(fresh(), "BADINT")
_bad = J.JOURNAL_FAULTS.get("backstop:bad_interval:str", 0)
_c4g.start_backstop(lambda: 0.0, interval="soon")
check("4g  a malformed interval is counted and floored, not raised",
      (J.JOURNAL_FAULTS.get("backstop:bad_interval:str", 0) - _bad,
       _c4g.backstop_interval), (1, J.BACKSTOP_MIN_INTERVAL_SECONDS))
_c4g.finalize(0.0)


section("5. ragas_harness.main() STARTS IT, BEFORE score_all")

_rh_src = io.open(os.path.join(_CODE_DIR, "oncotriage", "evaluation",
                               "ragas_harness.py"), encoding="utf-8").read()
_main = next((n for n in ast.walk(ast.parse(_rh_src))
              if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
check("5a  non-degeneracy: main() was found", _main is not None, True)
_calls = sorted((n.lineno, n) for n in ast.walk(_main or ast.parse(""))
                if isinstance(n, ast.Call)
                and getattr(n.func, "attr", getattr(n.func, "id", None))
                in ("start_backstop", "score_all", "finalize"))
_order = [getattr(n.func, "attr", getattr(n.func, "id", None))
          for _, n in _calls]
check("5b  main() calls start_backstop exactly once",
      _order.count("start_backstop"), 1)
check("5c  ...before score_all, so the stalled run it exists for is covered",
      (_order.index("start_backstop") < _order.index("score_all"))
      if "start_backstop" in _order and "score_all" in _order else None, True)
_bs = [n for _, n in _calls if getattr(n.func, "attr", None) == "start_backstop"]
check("5d  ...on the same checkpointer whose bound checkpoint is the hook, "
      "measuring the LEDGER (charged per response)",
      [ast.unparse(n) for n in _bs],
      ["_checkpointer.start_backstop(lambda: spend.SPEND_LEDGER.measured)"])


section("6. ISOLATION")

check("6a  the PRODUCTION journal is byte-unchanged",
      sha256_or_absent(_PROD_JOURNAL), _PROD_BEFORE)
for _rel, _h in _SRC.items():
    check(f"6b  {_rel} is byte-unchanged by this run",
          sha256_or_absent(os.path.join(_CODE_DIR, _rel)), _h)
if _ENV_BEFORE is None:
    os.environ.pop("ONCOTRIAGE_SPEND_JOURNAL", None)
else:
    os.environ["ONCOTRIAGE_SPEND_JOURNAL"] = _ENV_BEFORE
check("6c  the journal override is restored",
      os.environ.get("ONCOTRIAGE_SPEND_JOURNAL"), _ENV_BEFORE)
shutil.rmtree(TMP, ignore_errors=True)
check("6d  the temp tree is removed", os.path.exists(TMP), False)

print()
print("=" * 74)
print("SUMMARY")
print("=" * 74)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")
for _f in _FAILURES:
    print(f"  - {_f}")
sys.exit(1 if _RESULTS["failed"] else 0)
