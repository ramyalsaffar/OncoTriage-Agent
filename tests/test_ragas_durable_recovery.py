"""Synthetic crash/recovery tests. No provider calls; subprocesses inherit isolation."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import select
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oncotriage import spend, spend_journal
from oncotriage.evaluation import ragas_billing as rb


def fields(key="wire", amount=2.0):
    return dict(attempt_id=key, source=spend.SPEND_SOURCE_RAGAS_JUDGE,
                model="synthetic", reserved_usd=amount, input_tokens=1,
                output_tokens=1, correlation_id="synthetic")


def barrier():
    print("BARRIER", flush=True)
    sys.stdin.readline()


class CommitProxy:
    def __init__(self, conn, after=False, pause=False):
        self.conn, self.after, self.pause = conn, after, pause

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def commit(self):
        if self.after:
            self.conn.commit()
        if self.pause:
            barrier()
        raise OSError("synthetic lost ack" if self.after else "synthetic failed write")


def child(mode, journal, kind="judge"):
    if mode == "status":
        snapshot = rb.status(journal)
        snapshot["seed"] = snapshot["seed"]._asdict()
        print(json.dumps(snapshot), flush=True)
        return
    if mode == "open":
        try:
            with rb.Store(journal):
                print("OPEN", flush=True)
        except (rb.RecoveryRefusal, OSError):
            print("REFUSED", flush=True)
        return
    if mode == "init_marker":
        original = rb._publish
        def published(*args):
            original(*args)
            barrier()
        with patch.object(rb, "_publish", published):
            with rb.Store(journal):
                pass
        return
    if mode in ("init_uncommitted", "init_committed"):
        original = rb._connect
        with patch.object(rb, "_connect", side_effect=lambda *a, **kw:
                CommitProxy(original(*a, **kw), after=mode == "init_committed", pause=True)
                if kw.get("create") else original(*a, **kw)):
            with rb.Store(journal):
                pass
        return
    from test_ragas_billing import BillingTests, response, rh, JUDGE, EMBED
    fixture = BillingTests()
    fixture.setUp()
    try:
        with rb.Store(journal) as store:
            spend.BILLING_RECORD.install(store)
            spend.SPEND_LEDGER.seed(store.initial["seed"])
            if mode == "owner":
                barrier()
                return
            original_connect = rb._connect
            if mode == "reserve_uncommitted":
                fixture.stack.enter_context(patch.object(rb, "_connect",
                    side_effect=lambda *a, **kw: CommitProxy(original_connect(*a, **kw), pause=True)
                    if kw.get("writable") else original_connect(*a, **kw)))
            if mode == "before_send":
                original = rh._RagasAttemptRecord.begin
                def begin(record):
                    token = original(record)
                    barrier()
                    return token
                fixture.stack.enter_context(patch.object(rh._RagasAttemptRecord, "begin", begin))
            if mode == "response_unsettled":
                original = store.settle
                def settle(*a, **kw):
                    barrier()
                    return original(*a, **kw)
                fixture.stack.enter_context(patch.object(store, "settle", settle))
            async def provider():
                Path(journal + ".sent").write_text("one synthetic send")
                if mode == "inflight":
                    barrier()
                return response(kind)
            fn, kwargs, tally, reached = fixture.wrapper(kind, [provider])
            asyncio.run(fn(**kwargs))
            if mode == "settled":
                barrier()
    finally:
        spend.BILLING_RECORD.clear()
        fixture.doCleanups()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.journal = str(Path(self.tmp.name) / "spend.jsonl")
        spend.SPEND_STOP.reset()
        spend.BILLING_RECORD.clear()
        spend.reset_policy()

    def spawn(self, mode, journal=None, kind="judge"):
        proc = subprocess.Popen([sys.executable, __file__, "child", mode,
                                 journal or self.journal, kind],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: self.stop(proc))
        return proc

    @staticmethod
    def stop(proc):
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=30)

    def wait_barrier(self, proc):
        ready, _, _ = select.select([proc.stdout], [], [], 45)
        self.assertTrue(ready, "child did not reach crash boundary")
        line = proc.stdout.readline().strip()
        if line != "BARRIER":
            self.fail("unexpected child output: " + line + " " + proc.stderr.read())

    def fresh(self, mode="status", journal=None):
        proc = self.spawn(mode, journal)
        out, err = proc.communicate(timeout=45)
        self.assertEqual(proc.returncode, 0, err)
        return json.loads(out) if mode == "status" else out.strip()

    def test_precise_crashes_both_actual_wrappers(self):
        from test_ragas_billing import rh, JUDGE, EMBED
        for kind in ("judge", "embedding"):
            for mode in ("reserve_uncommitted", "before_send", "inflight", "response_unsettled", "settled"):
                with self.subTest(kind=kind, boundary=mode):
                    path = str(Path(self.tmp.name) / (kind + mode))
                    proc = self.spawn(mode, path, kind)
                    self.wait_barrier(proc)
                    self.stop(proc)
                    report = self.fresh(journal=path)
                    bound = rh.ragas_attempt_bound(kind, JUDGE if kind == "judge" else EMBED,
                        (), {"max_completion_tokens": 4096} if kind == "judge" else {"input": ["synthetic"]})[2]
                    expected = (0 if mode == "reserve_uncommitted" else
                                (0.000276 if kind == "judge" else 0.000002) if mode == "settled" else bound)
                    self.assertAlmostEqual(report["seed"]["usd"], expected)
                    unknown = mode not in ("reserve_uncommitted", "settled")
                    self.assertEqual(report["seed"]["unresolved"], int(unknown))
                    self.assertEqual(Path(path + ".sent").exists(), mode in ("inflight", "response_unsettled", "settled"))
                    self.assertEqual(self.fresh("open", path), "REFUSED" if unknown else "OPEN")

    def test_marker_interruption_and_missing_replaced_store(self):
        proc = self.spawn("init_marker")
        self.wait_barrier(proc)
        self.stop(proc)
        self.assertEqual(self.fresh("open"), "REFUSED")
        self.assertTrue(spend_journal.total(spend.SPEND_BUDGET_CAMPAIGN, self.journal).has_unreadable())
        other = self.journal + "other"
        with rb.Store(other):
            pass
        _, db, _ = rb.locations(other)
        Path(db).unlink()
        self.assertEqual(self.fresh("open", other), "REFUSED")
        replacement = self.journal + "replacement"
        with rb.Store(replacement):
            pass
        Path(db).write_bytes(Path(rb.locations(replacement)[1]).read_bytes())
        self.assertEqual(self.fresh("open", other), "REFUSED")

    def test_owner_alias_refusal_then_lock_release(self):
        proc = self.spawn("owner")
        self.wait_barrier(proc)
        alias = Path(self.tmp.name) / "alias"
        alias.symlink_to(self.tmp.name, target_is_directory=True)
        self.assertEqual(self.fresh("open", str(alias / "spend.jsonl")), "REFUSED")
        self.assertEqual(self.fresh()["seed"]["usd"], 0)
        self.stop(proc)
        self.assertEqual(self.fresh("open"), "OPEN")

    def test_legacy_once_and_changes_refuse(self):
        spend_journal.record_run(spend.SPEND_BUDGET_CAMPAIGN,
            spend.SPEND_SOURCE_RAGAS_JUDGE, self.tmp.name, "legacy", 3.0, "synthetic", self.journal)
        before = Path(self.journal).read_bytes()
        with rb.Store(self.journal) as store:
            store.reserve(**fields())
            self.assertEqual(store.settle("wire", outcome="response", settled_usd=0.25), "settled")
            self.assertEqual(store.settle("wire", outcome="response", settled_usd=0.25), "duplicate")
        self.assertEqual(Path(self.journal).read_bytes(), before)
        self.assertEqual(self.fresh()["seed"]["usd"], 3.25)
        self.assertEqual(spend_journal.total(spend.SPEND_BUDGET_CAMPAIGN, self.journal).usd, 3.25)
        with rb.Store(self.journal):
            pass
        self.assertEqual(self.fresh()["seed"]["usd"], 3.25)
        with open(self.journal, "ab") as stream:
            stream.write(b'{"torn":')
        self.assertEqual(self.fresh("open"), "REFUSED")

    def test_failed_and_lost_ack_commits(self):
        with rb.Store(self.journal) as store:
            original = rb._connect
            for after in (False, True):
                key = str(after)
                with patch.object(rb, "_connect", side_effect=lambda *a, **kw:
                        CommitProxy(original(*a, **kw), after=after) if kw.get("writable") else original(*a, **kw)):
                    with self.assertRaises(OSError):
                        store.reserve(**fields(key))
                self.assertEqual(store.stored_state(key)["state"], "reserved" if after else "absent")
            store.reserve(**fields("True"))  # same immutable write replay
            self.assertEqual(rb.status(self.journal)["seed"].usd, 2)
            spend.SPEND_LEDGER.reset()
            spend.BILLING_RECORD.install(store)
            token = spend.begin_billed_attempt(spend.SPEND_SOURCE_RAGAS_JUDGE,
                "synthetic", 1, 1, reserved_usd=2, where="test")
            with patch.object(rb, "_connect", side_effect=lambda *a, **kw:
                    CommitProxy(original(*a, **kw), after=True) if kw.get("writable") else original(*a, **kw)):
                token.resolve("response", response_usd=0.25)
            self.assertEqual(token.resolved_usd, 0.25)
            self.assertEqual(store.stored_state(token.handle.attempt_id)["settled_usd"], 0.25)
            token.resolve("response", response_usd=0.25)
            self.assertEqual(spend.SPEND_LEDGER.measured, 0.25)
            self.assertEqual(rb.status(self.journal)["seed"].usd, 2.25)

    def test_concurrent_atomic_admission(self):
        with rb.Store(self.journal) as store:
            def reserve(index):
                try:
                    store.reserve(**fields(str(index)), admission_cap=3)
                    return "admitted"
                except rb.AdmissionDeclined:
                    return "declined"
            with ThreadPoolExecutor(max_workers=4) as pool:
                outcomes = list(pool.map(reserve, range(4)))
            self.assertEqual(outcomes.count("admitted"), 1)
            self.assertEqual(outcomes.count("declined"), 3)
            self.assertEqual(rb.status(self.journal)["seed"].usd, 2)

    def test_corrupt_store_and_conflict(self):
        with rb.Store(self.journal) as store:
            store.reserve(**fields())
            store.settle("wire", outcome="response", settled_usd=0.25)
            self.assertEqual(store.settle("wire", outcome="response", settled_usd=1), "conflict")
        Path(rb.locations(self.journal)[1]).write_bytes(b"truncated SQLite")
        self.assertEqual(self.fresh("open"), "REFUSED")

    def test_integrated_missing_conflict_and_failed_settlement(self):
        for damage in ("missing", "conflict", "failed"):
            with self.subTest(damage=damage):
                path = self.journal + damage
                spend.SPEND_LEDGER.reset()
                spend.SPEND_STOP.reset()
                with rb.Store(path) as store:
                    spend.BILLING_RECORD.install(store)
                    token = spend.begin_billed_attempt(spend.SPEND_SOURCE_RAGAS_JUDGE,
                        "synthetic", 1, 1, reserved_usd=2, where="synthetic")
                    key = token.handle.attempt_id
                    if damage == "missing":
                        conn = sqlite3.connect(store.database)
                        try:
                            conn.execute("DELETE FROM attempts WHERE attempt_id=?", (key,))
                            conn.commit()
                        finally:
                            conn.close()
                    elif damage == "conflict":
                        store.settle(key, outcome="response", settled_usd=0.1)
                    if damage == "failed":
                        original = rb._connect
                        with patch.object(rb, "_connect", side_effect=lambda *a, **kw:
                                CommitProxy(original(*a, **kw)) if kw.get("writable") else original(*a, **kw)):
                            token.resolve("response", response_usd=0.25)
                        self.assertEqual(store.stored_state(key)["state"], "reserved")
                        self.assertEqual(token.resolved_usd, 2)
                    else:
                        token.resolve("response", response_usd=0.25)
                        self.assertTrue(Path(store.marker + ".fault").is_file())
                        self.assertEqual(spend.SPEND_STOP.limit, spend.SPEND_LIMIT_BILLING_RECORD)
                self.assertEqual(self.fresh("open", path), "REFUSED")

    def test_lost_reserve_ack_blocks_actual_wrapper(self):
        from test_ragas_billing import BillingTests, response
        fixture = BillingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with rb.Store(self.journal) as store:
            spend.BILLING_RECORD.install(store)
            fn, kwargs, tally, reached = fixture.wrapper("judge", [response("judge")])
            original = rb._connect
            with patch.object(rb, "_connect", side_effect=lambda *a, **kw:
                    CommitProxy(original(*a, **kw), after=True) if kw.get("writable") else original(*a, **kw)):
                with self.assertRaises(spend.BillingRecordUnavailable):
                    asyncio.run(fn(**kwargs))
            self.assertEqual(reached, [])
            snapshot = rb.status(self.journal)
            self.assertEqual(snapshot["seed"].unresolved, 1)
            self.assertGreater(snapshot["seed"].usd, 0)

    def test_main_refuses_before_clients_and_status_needs_no_run(self):
        from oncotriage.evaluation import ragas_harness as rh
        field = rh.DEFAULT_RESPONSE_FIELD
        run = rh.RunInput(self.tmp.name, {}, [rh.RetrievalSample(
            "synthetic", "summary", ["context"], "answer", field, 1)], [], [], field, {})
        with rb.Store(self.journal) as store:
            store.reserve(**fields())
        with patch.object(spend_journal, "journal_path", return_value=self.journal), \
                patch.object(rh, "load_run", return_value=run), \
                patch.object(rh, "apply_limit", side_effect=lambda run, limit: run), \
                patch.object(rh, "price_plan", return_value={}), \
                patch.object(rh, "environment_stamp", return_value={"packages": {}}), \
                patch.dict(sys.modules, {"ragas": types.ModuleType("ragas")}), \
                patch.object(rh.judge_independence, "require_independent_judge", return_value={}), \
                patch.object(rh, "build_judge", side_effect=rh.RagasRefusal(
                    "synthetic client construction reached", code="test_stop")) as judge, \
                patch.object(rh, "build_embeddings") as embedding:
            self.assertEqual(rh.main(["--run-dir", self.tmp.name]), 1)
            judge.assert_not_called()
            embedding.assert_not_called()
            self.assertEqual(rh.main(["--billing-status"]), 0)
            self.assertFalse(rh.dispatches_billed_calls(["--billing-status"]))

    def test_migration_commit_boundaries_preserve_history(self):
        for mode in ("init_uncommitted", "init_committed"):
            path = self.journal + mode
            spend_journal.record_run(spend.SPEND_BUDGET_CAMPAIGN,
                spend.SPEND_SOURCE_RAGAS_JUDGE, self.tmp.name, mode, 3.0, "synthetic", path)
            proc = self.spawn(mode, path)
            self.wait_barrier(proc)
            self.stop(proc)
            if mode == "init_uncommitted":
                self.assertEqual(self.fresh("open", path), "REFUSED")
            else:
                self.assertEqual(self.fresh(journal=path)["seed"]["usd"], 3)
                self.assertEqual(self.fresh("open", path), "OPEN")

    def test_valid_legacy_changes_and_unrelated_budget(self):
        with rb.Store(self.journal) as store:
            spend_journal.record_run(spend.SPEND_BUDGET_RATER,
                spend.SPEND_SOURCE_RATER, self.tmp.name, "other", 4.0, "synthetic", self.journal)
            store.reserve(**fields())
            store.settle("wire", outcome="response", settled_usd=0.25)
            self.assertEqual(rb.status(self.journal)["seed"].usd, 0.25)
            spend_journal.record_run(spend.SPEND_BUDGET_CAMPAIGN,
                spend.SPEND_SOURCE_RAGAS_JUDGE, self.tmp.name, "old_writer", 1.0, "synthetic", self.journal)
            with self.assertRaises(rb.RecoveryRefusal):
                store.reserve(**fields("another"))
        self.assertEqual(self.fresh("open"), "REFUSED")

    def test_observed_error_retry_still_has_two_durable_wire_ids(self):
        from test_ragas_billing import BillingTests, response, APITimeoutError
        fixture = BillingTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with rb.Store(self.journal) as store:
            spend.BILLING_RECORD.install(store)
            fn, kwargs, tally, reached = fixture.wrapper("judge", [APITimeoutError(), response("judge")])
            asyncio.run(fn(**kwargs))
            rows = rb.status(self.journal)["attempts"]
            self.assertEqual(len(reached), 2)
            self.assertEqual(len({row["attempt_id"] for row in rows}), 2)
            self.assertEqual({row["outcome"] for row in rows}, {"possibly_billed", "response"})
            self.assertEqual(reached[0], reached[1])
            self.assertEqual(reached[0]["service_tier"], "default")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "child":
        child(*sys.argv[2:])
    else:
        unittest.main()
