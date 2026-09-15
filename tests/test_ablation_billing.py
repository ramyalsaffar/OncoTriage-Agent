"""Synthetic ablation durability tests; run under the OS/tripwire containment."""
import contextlib
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import select
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from oncotriage import config, spend, provider_resilience as resilience
from oncotriage.ablation import billing, study
from oncotriage.agent import evaluation, models, retrieval, terminal
from oncotriage.storage import database_logger as storage


def record():
    bound = config.stage5_attempt_bound(config.matching_wire_model(),
                                       config.MATCHING_MAX_TOKENS,
                                       config.matching_sdk_attempts_per_call())
    return evaluation._Stage5AttemptRecord(
        bound["model"], bound["input_tokens"], bound["output_tokens"],
        reserved_usd=bound["usd"], note=config.stage5_reservation_note(bound))


def response():
    return types.SimpleNamespace(model=config.matching_wire_model(),
        usage=types.SimpleNamespace(prompt_tokens=100, completion_tokens=10))


def barrier():
    print("BARRIER", flush=True)
    sys.stdin.readline()


def child(mode, db):
    config.SPEND_CAP_ENFORCED = False
    if mode == "read":
        with billing.Session(db, db + ".cp"):
            print(json.dumps(billing.read_seed(db)._asdict()), flush=True)
        return
    if mode == "marker":
        original = billing._sync_parent
        def sync(path):
            original(path)
            if str(path).endswith(".json"):
                barrier()
        billing._sync_parent = sync
    with billing.Session(db, db + ".cp") as session:
        session.install()
        rec = record()
        if mode == "uncommitted":
            original = storage._open_billing_connection
            class Connection:
                def __init__(self, conn):
                    self.conn = conn
                def __getattr__(self, name):
                    return getattr(self.conn, name)
                def commit(self):
                    barrier()
                    return self.conn.commit()
            storage._open_billing_connection = lambda path: Connection(original(path))
        begin = rec.begin
        settle = rec.response
        def begin_hook():
            token = begin()
            if mode == "reserved":
                barrier()
            return token
        def response_hook(token, value):
            if mode == "response":
                barrier()
            settle(token, value)
            if mode == "settled":
                barrier()
        rec.begin = begin_hook
        rec.response = response_hook
        def send():
            print("DISPATCH", flush=True)
            if mode == "inflight":
                barrier()
            return response()
        permit = types.SimpleNamespace(waited_s=0)
        pacer = types.SimpleNamespace(reserve=lambda *a, **kw: permit,
            wait=lambda *a: None, settle=lambda *a, **kw: None)
        resilience.execute(send, scope="openai", reservation_tokens=10,
            reservation_kind=resilience.RESERVATION_INFERENCE,
            classify=lambda exc: resilience.verdict_for(resilience.CATEGORY_CONNECTION_LOST),
            max_attempts=1, pacer=pacer, attempt_record=rec)


class BillingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / "study.db")
        self.cp = self.db + ".cp"
        self.cap = config.SPEND_CAP_USD
        self.enforced = config.SPEND_CAP_ENFORCED
        self.policy = spend.policy()
        self.sink = spend.BILLING_RECORD.installed_sink()
        config.SPEND_CAP_ENFORCED = False
        spend.set_policy(spend.SPEND_POLICY_CAMPAIGN)
        spend.SPEND_LEDGER.reset()
        spend.SPEND_STOP.reset()
        spend.BILLING_RECORD.clear()

    def tearDown(self):
        config.SPEND_CAP_USD = self.cap
        config.SPEND_CAP_ENFORCED = self.enforced
        spend.set_policy(self.policy)
        spend.BILLING_RECORD.install(self.sink)
        spend.SPEND_LEDGER.reset()
        spend.SPEND_STOP.reset()
        self.temp.cleanup()

    def test_real_stage5_record_all_attempts_and_no_results(self):
        with billing.Session(self.db, self.cp) as session:
            session.install()
            rec = record()
            token = rec.begin()
            self.assertEqual(billing.read_seed(self.db).unresolved, 1)
            rec.response(token, response())
            rec.response(token, response())  # idempotent resolution
            rec.failure(rec.begin(), types.SimpleNamespace(billing="not_billed"))
            rec.failure(rec.begin(), types.SimpleNamespace(billing="possibly_billed"))
            rec.abandoned(rec.begin())
            before = billing.read_seed(self.db)
            self.assertEqual(before.rows, 4)
            self.assertGreater(before.usd, 0)
        with billing.Session(self.db, self.cp) as resumed:
            self.assertEqual(resumed.campaign, session.campaign)
            self.assertNotEqual(resumed.run_id, session.run_id)
            self.assertEqual(billing.read_seed(self.db).usd, before.usd)
        self.assertFalse(Path(self.cp).exists())

    def test_embedding_wrapper_records_before_dispatch_and_failure(self):
        with billing.Session(self.db, self.cp) as session:
            session.install()
            calls = []
            def send(**kwargs):
                calls.append(kwargs)
                self.assertEqual(billing.read_seed(self.db).unresolved, 1)
                raise OSError("synthetic transport loss")
            client = types.SimpleNamespace(embeddings=types.SimpleNamespace(create=send))
            with patch.object(models.deps, "get_openai_client", return_value=client), \
                 patch.object(config, "matching_timeout", return_value=None, create=True):
                with self.assertRaises(OSError):
                    models.get_embedding("synthetic")
            self.assertEqual(len(calls), 1)
            self.assertGreater(billing.read_seed(self.db).usd, 0)

    def test_legacy_and_witness_loss_refuse(self):
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE ablation_runs(id INTEGER)")
        conn.execute("INSERT INTO ablation_runs VALUES(1)")
        conn.commit()
        conn.close()
        with self.assertRaises(billing.BillingRefusal):
            with billing.Session(self.db, self.cp):
                self.fail("legacy admitted")
        os.unlink(self.db)
        with billing.Session(self.db, self.cp):
            pass
        os.unlink(self.db)
        with self.assertRaises(billing.BillingRefusal):
            with billing.Session(self.db, self.cp):
                self.fail("missing database admitted")

    def test_wrong_schema_owner_refuses_before_reconciliation(self):
        with billing.Session(self.db, self.cp):
            pass
        conn = sqlite3.connect(self.db)
        conn.execute("DROP TABLE billing_attempts")
        storage.initialize_billing_table(conn.cursor())
        conn.commit()
        conn.close()
        with patch.object(storage, "reconcile_discrepancy_markers") as reconcile:
            with self.assertRaises(billing.BillingRefusal):
                with billing.Session(self.db, self.cp):
                    pass
            reconcile.assert_not_called()

    def test_prior_sink_and_alias_ownership(self):
        prior = object()
        spend.BILLING_RECORD.install(prior)
        alias = Path(self.temp.name) / "alias"
        alias.symlink_to(self.temp.name, target_is_directory=True)
        with billing.Session(self.db, self.cp) as session:
            self.assertIsNone(spend.BILLING_RECORD.installed_sink())
            session.install()
            with self.assertRaises(billing.BillingRefusal):
                with billing.Session(alias / "study.db", self.cp):
                    pass
            with self.assertRaises(billing.BillingRefusal):
                with billing.Session(Path(self.temp.name) / "other.db", self.cp):
                    pass
            other_process = subprocess.run(
                [sys.executable, __file__, "child", "read", str(alias / "study.db")],
                capture_output=True, text=True, timeout=30)
            self.assertNotEqual(other_process.returncode, 0)
            self.assertIn("Resource temporarily unavailable", other_process.stderr)
        self.assertIs(spend.BILLING_RECORD.installed_sink(), prior)
        with billing.Session(alias / "study.db", self.cp) as session:
            self.assertEqual(billing.read_seed(self.db).usd, 0)

    def test_unproven_open_stage5_refuses(self):
        with billing.Session(self.db, self.cp) as session:
            session.install()
            spend.BILLING_RECORD.installed_sink().reserve(
                attempt_id="bad", source="stage5", model="synthetic",
                input_tokens=1, output_tokens=1, reserved_usd=1)
        with self.assertRaises(billing.BillingRefusal):
            with billing.Session(self.db, self.cp):
                pass

    def test_pending_and_committed_leftover_discrepancies(self):
        with billing.Session(self.db, self.cp) as session:
            session.install()
            sink = spend.BILLING_RECORD.installed_sink()
            sink.reserve(attempt_id="wire", source="query_embedding", model=config.EMBEDDING_MODEL,
                         input_tokens=1, output_tokens=0, reserved_usd=1)
            sink.settle("wire", outcome="response", settled_usd=.5)
            payload = dict(attempt_id="wire", campaign_id=session.campaign,
                run_id=session.run_id, source="query_embedding", model=config.EMBEDDING_MODEL,
                result=storage.SETTLE_CONFLICT, live_usd=.7, durable_usd=.5,
                shortfall_usd=.2, db_path=self.db, version=storage.DISCREPANCY_MARKER_VERSION)
            storage.write_discrepancy_marker(str(session.discrepancies), payload)
        with self.assertRaises(billing.BillingRefusal):
            billing.read_seed(self.db)
        unlink = os.unlink
        def fail_marker(path, *args, **kwargs):
            if str(path).endswith(storage._DISCREPANCY_MARKER_SUFFIX):
                raise OSError("synthetic unlink failure")
            return unlink(path, *args, **kwargs)
        with patch.object(os, "unlink", side_effect=fail_marker):
            with billing.Session(self.db, self.cp):
                self.assertAlmostEqual(billing.read_seed(self.db).usd, .7)
        with billing.Session(self.db, self.cp):
            self.assertAlmostEqual(billing.read_seed(self.db).usd, .7)
            self.assertEqual(billing.read_seed(self.db).rows, 2)

    def test_wrong_marker_identity_refuses_without_reconciliation_writes(self):
        with billing.Session(self.db, self.cp) as session:
            pass
        base = dict(attempt_id="wrong", campaign_id=session.campaign,
            run_id=session.run_id, source="query_embedding", model=config.EMBEDDING_MODEL,
            result=storage.SETTLE_FAILED, live_usd=1, durable_usd=0,
            shortfall_usd=1, db_path=self.db, version=storage.DISCREPANCY_MARKER_VERSION)
        for key, value in (("campaign_id", "a" * 32), ("run_id", 999),
                           ("db_path", str(Path(self.temp.name) / "other.db"))):
            with self.subTest(key=key):
                payload = dict(base, **{key: value})
                path = storage.write_discrepancy_marker(str(session.discrepancies), payload)
                with patch.object(storage, "reconcile_discrepancy_markers") as reconcile:
                    with self.assertRaises(billing.BillingRefusal):
                        with billing.Session(self.db, self.cp):
                            pass
                    reconcile.assert_not_called()
                os.unlink(path)
                self.assertEqual(billing.read_seed(self.db).usd, 0)

    def test_concurrent_admission_and_reservation_replay(self):
        config.SPEND_CAP_ENFORCED = True
        config.SPEND_CAP_USD = 1
        with billing.Session(self.db, self.cp) as session:
            session.install()
            start = threading.Barrier(2)
            def attempt():
                start.wait(timeout=5)
                try:
                    return spend.begin_billed_attempt(spend.SPEND_SOURCE_EMBEDDING,
                        config.EMBEDDING_MODEL, 1, 0, where="synthetic", reserved_usd=1)
                except spend.BudgetAdmissionDeclined as exc:
                    return exc
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(attempt)
                second = pool.submit(attempt)
                results = [first.result(), second.result()]
            self.assertEqual(sum(isinstance(x, spend.BudgetAdmissionDeclined) for x in results), 1)
            token = next(x for x in results if not isinstance(x, Exception))
            self.assertEqual(billing.read_seed(self.db).rows, 1)
            token.resolve(spend.BILLING_OUTCOME_RESPONSE, response_usd=.5)
            fields = dict(attempt_id="replay", campaign_id=session.campaign,
                run_id=session.run_id, source="query_embedding", model=config.EMBEDDING_MODEL,
                input_tokens=1, output_tokens=0, reserved_usd=.5, admission_cap=1)
            storage.reserve_billing_attempt(self.db, **fields)
            storage.reserve_billing_attempt(self.db, **fields)
            self.assertEqual(billing.read_seed(self.db).rows, 2)
            self.assertEqual(billing.read_seed(self.db).usd, 1)

    def test_atomic_cap_and_prior_unresolved(self):
        with billing.Session(self.db, self.cp) as session:
            session.install()
            rec = record()
            token = rec.begin()
            bound = token.upper_bound_usd()
        config.SPEND_CAP_ENFORCED = True
        config.SPEND_CAP_USD = bound * 1.5
        spend.SPEND_LEDGER.reset()
        with billing.Session(self.db, self.cp) as session:
            session.install()
            spend.SPEND_LEDGER.seed(billing.read_seed(self.db))
            with self.assertRaises(evaluation.Stage5SpendStopped) as refused:
                record().begin()
            self.assertEqual(refused.exception.admission_reason, spend.ADMISSION_DECLINE_EXHAUSTED)
            self.assertEqual(billing.read_seed(self.db).rows, 1)
            spend.SPEND_STOP.reset()
            config.SPEND_CAP_USD = bound * 2.5
            rec = record()
            second = rec.begin()
            self.assertEqual(billing.read_seed(self.db).rows, 2)
            rec.response(second, response())
            self.assertEqual(billing.read_seed(self.db).unresolved, 1)
            self.assertGreater(billing.read_seed(self.db).usd, bound)

    def run_main(self, invoke, *, fresh=False, variants=None):
        """Real study pool/result writer/checkpoint; only external dependencies replaced."""
        patients = [{"patient_id": f"p{i}", "conditions": [], "medications": []}
                    for i in range(2)]
        fingerprint = types.SimpleNamespace(
            current=lambda: {"fingerprint_version": 99}, clear_cache=lambda: None,
            summary=lambda fp: "synthetic", compare=lambda a, b: ("match", ""),
            COLLECTION_IDENTITY=(), FP_MATCH="match", FP_ABSENT="absent",
            ResumeRefusal=RuntimeError)
        args = ["study", "--db", self.db, "--sample-size", "2", "--configs"]
        args += variants or ["full_pipeline"]
        if fresh:
            args += ["--fresh-start"]
        replacements = {
            "build_bm25_index_from_qdrant": lambda: ({}, ["synthetic"]),
            "build_matching_graph": lambda: types.SimpleNamespace(invoke=invoke),
            "load_all_patients": lambda path: patients,
            "restrict_to_campaign_cohort": lambda pats, path: (pats, None),
            "stratified_sample": lambda pats, size, seed: pats,
            "compute_patient_hash": lambda patient: "synthetic",
            "resolve_qdrant_collection": lambda: "synthetic",
            "_get_patient_group": lambda *args: "synthetic",
            "CaffeinateSession": lambda *args: contextlib.nullcontext(),
            "generate_summary": lambda **kwargs: None,
            "MAX_WORKERS": 2,
            "run_fingerprint": fingerprint,
            "tracking": types.SimpleNamespace(start_run=lambda **kw: None,
                end_run=lambda **kw: None, log_run_metrics=lambda **kw: None),
        }
        with contextlib.ExitStack() as stack:
            for name, value in replacements.items():
                stack.enter_context(patch.object(study, name, value))
            stack.enter_context(patch.object(study.deps, "get_cancer_registry", return_value=None))
            stack.enter_context(patch.object(sys, "argv", args))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            study.main()

    def test_main_variants_fresh_cleanup_and_two_databases(self):
        def invoke(state):
            rec = record()
            rec.response(rec.begin(), response())
            return {"result": {"error": "", "matches": [], "stage_timings": {}}}
        self.run_main(invoke)
        first = billing.read_seed(self.db)
        self.run_main(invoke, fresh=True, variants=["no_mesh_filter", "no_stage_filter"])
        self.assertEqual(billing.read_seed(self.db).rows, first.rows + 4)
        self.assertEqual(spend.SPEND_LEDGER.seeded.usd, first.usd)
        original = self.db
        self.db = str(Path(self.temp.name) / "other.db")
        self.run_main(invoke)
        self.assertEqual(billing.read_seed(self.db).rows, 2)
        self.assertEqual(billing.read_seed(original).rows, 6)

    def test_failed_start_preserves_checkpoint_and_prior_sink(self):
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE ablation_runs(id INTEGER)")
        conn.commit()
        conn.close()
        checkpoint = study._ablation_checkpoint_path(self.db)
        checkpoint.write_text("legacy evidence")
        prior = object()
        spend.BILLING_RECORD.install(prior)
        with self.assertRaises(SystemExit) as stopped:
            self.run_main(lambda state: self.fail("legacy dispatched"), fresh=True)
        self.assertEqual(stopped.exception.code, 1)
        self.assertEqual(checkpoint.read_text(), "legacy evidence")
        self.assertIs(spend.BILLING_RECORD.installed_sink(), prior)

    def test_billing_stop_remedy_names_ablation_and_preserves_budget(self):
        lines = []
        with billing.Session(self.db, self.cp) as session:
            session.install()
            with patch.object(spend.console, "out", side_effect=lambda *args: lines.append(str(args[0]) if args else "")):
                spend.SPEND_STOP.trip(spend.SPEND_LIMIT_BILLING_RECORD,
                    "synthetic missing row", spend.SPEND_SOURCE_STAGE5,
                    spend.BILLING_RECORD_CAUSE_MISSING)
        text = "\n".join(lines)
        self.assertIn(str(Path(self.db).resolve()), text)
        self.assertIn("--fresh-start does not reset", text)
        self.assertNotIn("--fresh starts", text)
        self.assertNotIn("inferences.billing_attempts", text)

    def test_dense_refusal_zero_candidates_pending_unrelated_success_kept(self):
        barrier_pair = threading.Barrier(2)
        def invoke(state):
            barrier_pair.wait(timeout=10)
            if state["patient_data"]["patient_id"] == "p0":
                state.update(expanded_query="synthetic", ablation_flags={"retrieval_mode": "vector_only"})
                with patch.object(retrieval, "require_populated_index"), \
                     patch.object(retrieval.deps, "get_bm25_query_model", return_value=None), \
                     patch.object(retrieval.deps, "get_qdrant_client", return_value=None), \
                     patch.object(models, "get_embedding", side_effect=spend.BillingRecordUnavailable("synthetic refusal")):
                    state.update(retrieval.node_hybrid_retrieval(state))
                spend.SPEND_STOP.trip(spend.SPEND_LIMIT_BILLING_RECORD,
                    "synthetic dense refusal", spend.SPEND_SOURCE_EMBEDDING)
                return terminal.node_no_candidates(state)
            # This pair is already admitted. A different worker's latch must
            # neither erase its completion nor turn it into refused work.
            return {"result": {"error": "", "matches": [], "stage_timings": {}}}
        self.run_main(invoke)
        checkpoint = json.loads(study._ablation_checkpoint_path(self.db).read_text())
        self.assertEqual(checkpoint["completed"], [["full_pipeline", "p1"]])
        conn = sqlite3.connect(self.db)
        try:
            rows = dict(conn.execute("SELECT patient_id, error FROM ablation_results"))
            self.assertTrue(rows["p0"])
            self.assertFalse(rows["p1"])
            self.assertEqual(conn.execute("SELECT stop_reason FROM ablation_runs").fetchone()[0], "billing_record")
        finally:
            conn.close()

    def test_real_retry_owner_reserves_before_each_send(self):
        with billing.Session(self.db, self.cp) as session:
            session.install()
            calls = []
            permit = types.SimpleNamespace(waited_s=0)
            pacer = types.SimpleNamespace(reserve=lambda *a, **kw: permit,
                wait=lambda *a: None, settle=lambda *a, **kw: None)
            def send():
                calls.append(billing.read_seed(self.db).rows)
                if len(calls) == 1:
                    raise OSError("synthetic retry")
                return response()
            with patch.object(resilience, "full_jitter_delay", return_value=0):
                resilience.execute(send, scope="openai", reservation_tokens=10,
                    reservation_kind=resilience.RESERVATION_INFERENCE,
                    classify=lambda exc: resilience.verdict_for(resilience.CATEGORY_CONNECTION_LOST),
                    max_attempts=2, pacer=pacer, attempt_record=record())
            self.assertEqual(calls, [1, 2])
            self.assertEqual(billing.read_seed(self.db).rows, 2)

    def kill_at(self, mode, db):
        process = subprocess.Popen([sys.executable, __file__, "child", mode, db],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output = b""
        deadline = time.monotonic() + 40
        try:
            while b"BARRIER\n" not in output:
                remaining = deadline - time.monotonic()
                self.assertGreater(remaining, 0, "child did not reach barrier before deadline")
                ready, _, _ = select.select([process.stdout], [], [], remaining)
                self.assertTrue(ready, "child did not reach barrier")
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk:
                    self.fail(process.stderr.read().decode())
                output += chunk
            dispatches = output.count(b"DISPATCH\n")
            process.kill()
            process.wait(timeout=10)
            self.assertEqual(dispatches, int(mode in ("inflight", "response", "settled")))
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)

    def test_sigkill_boundaries_and_fresh_process_recovery(self):
        for mode in ("reserved", "inflight", "response", "settled", "uncommitted"):
            with self.subTest(mode=mode):
                db = str(Path(self.temp.name) / (mode + ".db"))
                self.kill_at(mode, db)
                recovered = subprocess.run([sys.executable, __file__, "child", "read", db],
                    text=True, capture_output=True, timeout=45)
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                seed = json.loads(recovered.stdout.strip().splitlines()[-1])
                self.assertEqual(seed["rows"], 0 if mode == "uncommitted" else 1)
                self.assertEqual(seed["unresolved"], int(mode in ("reserved", "inflight", "response")))
                if mode != "uncommitted":
                    self.assertGreater(seed["usd"], 0)

    def test_initialization_crash_refuses(self):
        self.kill_at("marker", self.db)
        with self.assertRaises(billing.BillingRefusal):
            with billing.Session(self.db, self.cp):
                pass


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "child":
        child(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
