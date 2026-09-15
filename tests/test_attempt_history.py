"""Patient admission durability: real worker/writer, stubbed matching only.

Every database and bundle is disposable. Run under the project's OS network
sandbox and import-time tripwire. No graph, client, production DB or model is
invoked. Child processes die at specified persistence boundaries.
"""
import copy
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

os.environ["ONCOTRIAGE_DEFER_LOCAL_MODELS"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from oncotriage import config, run_fingerprint as RF
from oncotriage.agent.patient import compute_patient_hash
from oncotriage.agent.prompts import PROMPT_VERSION, prompt_sha256, render_system_prompt
from oncotriage.agent import evaluation as EV
from oncotriage.batch import runner as R
from oncotriage.evaluation import cohort, cohort_groups, campaign_export as CE
from oncotriage.evaluation import rater, ragas_harness
from oncotriage.storage import database_logger as DL, attempt_history as AH


def quiet(*a, **k):
    pass


class Fixture:
    def __init__(self, root):
        self.root = Path(root)
        self.db = str(self.root / "test.db")
        self.fhir = self.root / "fhir"
        self.fhir.mkdir()
        for i in range(3):
            bundle = {"resourceType": "Bundle", "entry": [
                {"resource": {"resourceType": "Patient", "id": f"P{i}",
                              "gender": "female", "birthDate": "1960-01-01"}},
                {"resource": {"resourceType": "Condition", "code": {"coding": [
                    {"system": "http://snomed.info/sct", "code": "254837009",
                     "display": "Malignant neoplasm of breast (disorder)"}]}}}]}
            (self.fhir / f"private-name-{i}.json").write_text(json.dumps(bundle))
        self.files = sorted(str(p) for p in self.fhir.glob("*.json"))
        self.selection = cohort.select(self.files, size=3, seed=1,
            judge_size=3, judge_seed=2, stability_size=3, stability_seed=3,
            group_of=cohort_groups.grouper(cohort_groups.group_map(
                self.files, out=quiet, use_cache=False)))
        self.fp = {k: "fixed-" + k for k in RF.FINGERPRINT_FIELDS}
        self.fp.update(fingerprint_version=RF.FINGERPRINT_VERSION,
                       collection_points=10, campaign_cohort_size=3,
                       campaign_cohort_seed=1, matching_per_trial_empty_retries=1,
                       matching_per_trial_parallel_bound=4)
        DL.initialize_database(self.db)

    def run(self, campaign="C", attach=True):
        rid = DL.start_run_record("batch_runner", db_path=self.db,
            fingerprint=self.fp, cohort=self.selection.record())
        if attach:
            DL.set_run_billing_campaign_id(rid, campaign, self.db)
        return rid

    def writer(self, rid, new=False, campaign="C"):
        return AH.open_writer(self.db, campaign, rid, self.selection.digest,
                              self.files, new_campaign=new)

    def work(self, writer, rid, i=0, resample=False, expect_write=True):
        entry = R._start_patient_unless_stopped(attempt_history=writer,
            fhir_path=self.files[i], graph=None, is_resample=resample,
            run_id=rid, db_path=self.db)
        if expect_write:
            if entry.get("db_row_written") is not True or not entry.get("inference_id"):
                raise AssertionError(f"fixture did not store its intended inference: {entry}")
        return entry

    def export(self, name="export", campaign="C"):
        return CE.export_campaign(self.db, campaign, str(self.root / name),
                                  str(self.fhir), out=quiet)


def good_result(patient_data, graph):
    system = render_system_prompt(True, "", "Patient: PT-TEST\nAge: 66\nConditions:\n- breast cancer")
    trial = {"trial": {"nct_id": "NCT00000001", "phase": "PHASE2", "title": "Trial",
        "eligibility": {"inclusion_criteria": "Inclusion Criteria:\n* age at least 18",
                        "exclusion_criteria": "Exclusion Criteria:\n* pregnant"}}}
    blocks = EV._render_trial_blocks([trial], log_events=False)
    return {"patient_id": patient_data["patient_id"], "error": "", "timestamp": "2026-09-15T00:00:00",
        "patient_data_hash": compute_patient_hash(patient_data),
        "matching_model": config.matching_wire_model(), "age_reference_date": "2026-08-03",
        "llm_classifier_prompt_version": PROMPT_VERSION,
        "llm_classifier_prompt_sha256": prompt_sha256(system),
        "llm_classifier_prompt": f"[SYSTEM]\n{system}\n\n[USER]\n\nCLINICAL TRIALS:\n{''.join(blocks)}\n",
        "candidates_evaluated": 1, "matches": [{"nct_id": "NCT00000001",
            "eligible": "eligible", "assessment": "Adult patient.", "trial_number": 1,
            "inclusion_criteria": [{"criterion": "age at least 18", "status": "met",
                                     "patient_value": "66"}], "exclusion_criteria": []}],
        "near_misses": [], "not_evaluable": []}


def failed_result(patient_data, graph):
    result = good_result(patient_data, graph)
    result.update(error="synthetic early failure", llm_classifier_prompt="",
                  candidates_evaluated=0, matches=[])
    return result


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="attempt-history-")
        self.addCleanup(self.tmp.cleanup)
        self.fx = Fixture(self.tmp.name)
        R.STOP_SWITCH.reset()
        R.spend.SPEND_STOP.reset()
        self.matcher = patch.object(R, "match_patient_to_trials", good_result)
        self.matcher.start()
        self.addCleanup(self.matcher.stop)

    def refusal(self, code, fn):
        with self.assertRaises((AH.HistoryRefusal, CE.ExportRefusal)) as cm:
            fn()
        self.assertEqual(cm.exception.code, code)

    def test_recorded_failure_then_success_and_loaders(self):
        rid = self.fx.run()
        with self.fx.writer(rid, new=True) as writer:
            with patch.object(R, "match_patient_to_trials", failed_result):
                first = self.fx.work(writer, rid)
            self.fx.work(writer, rid)
            self.fx.work(writer, rid, 1)
        manifest = self.fx.export()
        self.assertEqual(manifest["outcomes"]["P0"]["outcome"], CE.OUTCOME_FIRST_ATTEMPT_FAILED)
        self.assertEqual(manifest["outcomes"]["P0"]["inference_id"], first["inference_id"])
        self.assertTrue(manifest["outcomes"]["P0"]["later_success_in_campaign"])
        self.assertEqual(len(manifest["outcomes"]), 3)
        self.assertEqual(manifest["outcomes"]["P2"]["outcome"], CE.OUTCOME_NOT_IN_CAMPAIGN)
        self.assertEqual(len(rater.load_run(str(self.fx.root / "export")).decisions), 1)
        self.assertEqual(len(ragas_harness.load_run(str(self.fx.root / "export")).generation), 1)
        self.assertNotIn("private-name", json.dumps(manifest))

    def test_exception_without_inference_then_success(self):
        rid = self.fx.run()
        with self.fx.writer(rid, new=True) as writer:
            with patch.object(R, "match_patient_to_trials", side_effect=ValueError("synthetic")):
                self.assertEqual(self.fx.work(writer, rid, expect_write=False)["status"], "exception")
            self.fx.work(writer, rid)
        result = self.fx.export()["outcomes"]["P0"]
        self.assertEqual(result["outcome"], CE.OUTCOME_FIRST_ATTEMPT_FAILED)
        self.assertIsNone(result["inference_id"])
        self.assertTrue(result["later_success_in_campaign"])

    def test_lost_main_write_then_successful_resample(self):
        rid = self.fx.run()
        with self.fx.writer(rid, new=True) as writer:
            lost = DL.InferenceWriteResult(self.fx.db, ok=False, error="synthetic", attempts=1, inference_id=None)
            with patch.object(R, "log_inference", return_value=lost):
                self.assertEqual(self.fx.work(writer, rid, expect_write=False)["status"], "success")
            self.fx.work(writer, rid, resample=True)
        self.assertEqual(self.fx.export()["outcomes"]["P0"]["outcome"], CE.OUTCOME_UNAVAILABLE)

    def test_resume_preserves_first_admission(self):
        first = self.fx.run()
        with self.fx.writer(first, new=True) as w:
            with patch.object(R, "match_patient_to_trials", failed_result):
                self.fx.work(w, first)
        second = self.fx.run()
        with self.fx.writer(second) as w:
            self.fx.work(w, second)
        result = self.fx.export()["outcomes"]["P0"]
        self.assertEqual(result["run_id"], first)
        self.assertEqual(result["admission_sequence"], 1)

    def test_existing_campaign_cannot_create_missing_history(self):
        rid = self.fx.run()
        self.refusal("attempt_history_missing", lambda: self.fx.writer(rid).__enter__())
        self.refusal("attempt_history_missing", self.fx.export)

    def test_corruption_and_missing_run_coverage_refuse(self):
        rid = self.fx.run()
        with self.fx.writer(rid, new=True): pass
        self.fx.run()
        self.refusal("attempt_history_uncovered", self.fx.export)
        path = AH.history_path(self.fx.db, "C")
        path.write_text('{"truncated":')
        self.refusal("attempt_history_corrupt", self.fx.export)

    def test_identity_mismatch_and_invalid_structure_refuse(self):
        rid = self.fx.run()
        with self.fx.writer(rid, new=True): pass
        path = AH.history_path(self.fx.db, "C")
        body = AH.read(path)
        body["campaign_id"] = "different"
        AH._write(path, body)
        self.refusal("attempt_history_mismatch", self.fx.export)
        body["attempts"] = [{"sequence": 99}]
        AH._write(path, body)
        self.refusal("attempt_history_corrupt", lambda: AH.read(path))

    def test_start_failure_prevents_work_and_latches(self):
        rid = self.fx.run()
        with self.fx.writer(rid, new=True) as w:
            with patch.object(AH, "_write", side_effect=OSError("synthetic")), patch.object(R, "process_patient") as work:
                self.refusal("attempt_history_write_failed", lambda: self.fx.work(w, rid))
                work.assert_not_called()
            with patch.object(R, "process_patient") as work:
                self.refusal("attempt_history_write_failed", lambda: self.fx.work(w, rid, 1))
                work.assert_not_called()

    def test_completion_failure_retains_unknown_and_stops_admission(self):
        rid = self.fx.run()
        real = AH._write
        def fail_completion(path, body):
            if any(a["completion"] for a in body["attempts"]):
                raise OSError("synthetic")
            real(path, body)
        with self.fx.writer(rid, new=True) as w:
            with patch.object(AH, "_write", fail_completion):
                self.refusal("attempt_history_write_failed", lambda: self.fx.work(w, rid))
            self.refusal("attempt_history_write_failed", lambda: self.fx.work(w, rid, 1))
        with sqlite3.connect(self.fx.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM inferences").fetchone()[0], 1)
        self.assertEqual(self.fx.export()["outcomes"]["P0"]["outcome"], CE.OUTCOME_INTERRUPTED)

    def test_already_admitted_worker_can_complete_after_fault(self):
        rid = self.fx.run()
        entered, release = threading.Event(), threading.Event()
        results, errors = [], []
        def blocked_match(patient_data, graph):
            entered.set()
            if not release.wait(10): raise RuntimeError("test did not release worker")
            return good_result(patient_data, graph)
        def work(w):
            try: results.append(self.fx.work(w, rid))
            except BaseException as exc: errors.append(exc)
        with self.fx.writer(rid, new=True) as w:
            with patch.object(R, "match_patient_to_trials", blocked_match):
                thread = threading.Thread(target=work, args=(w,))
                thread.start()
                try:
                    self.assertTrue(entered.wait(10))
                    with patch.object(AH, "_write", side_effect=OSError("synthetic")):
                        self.refusal("attempt_history_write_failed", lambda: self.fx.work(w, rid, 1))
                finally:
                    release.set()
                    thread.join(10)
                self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 1)
            self.assertEqual(w.body["attempts"][0]["completion"]["inference_id"], results[0]["inference_id"])

    def test_queue_cancellation_precedes_start(self):
        rid = self.fx.run()
        with self.fx.writer(rid, new=True) as w:
            with patch.object(R, "STOP_SWITCH", type("Stopped", (), {"requested": True})()):
                with self.assertRaises(R.CancelledError): self.fx.work(w, rid)
            self.assertEqual(w.body["attempts"], [])

    def test_fatal_exception_is_preserved(self):
        rid = self.fx.run()
        with self.fx.writer(rid, new=True) as w:
            with patch.object(R, "process_patient", side_effect=SystemExit(29)):
                with self.assertRaises(SystemExit) as cm: self.fx.work(w, rid)
            self.assertEqual(cm.exception.code, 29)
            self.assertEqual(w.body["attempts"][0]["completion"]["outcome"], "exception")

    def test_concurrent_writer_and_exporter_excluded(self):
        rid = self.fx.run()
        with self.fx.writer(rid, new=True):
            self.refusal("attempt_history_busy", self.fx.export)
            self.refusal("attempt_history_busy", lambda: self.fx.writer(rid).__enter__())
            child = subprocess.run([sys.executable, __file__, "--lock", self.fx.db],
                                   capture_output=True, text=True, timeout=30)
            self.assertEqual(child.returncode, 0, child.stdout + child.stderr)

    def test_exact_links_and_unrecorded_rows_refuse(self):
        rid = self.fx.run()
        with self.fx.writer(rid, new=True) as w:
            first = self.fx.work(w, rid)
            self.fx.work(w, rid, 1)
        path = AH.history_path(self.fx.db, "C")
        clean = AH.read(path)
        bad = copy.deepcopy(clean)
        bad["attempts"][0]["completion"]["inference_id"] = 9999
        AH._write(path, bad)
        self.refusal("attempt_history_mismatch", self.fx.export)

        bad = copy.deepcopy(clean)
        bad["attempts"][0]["completion"]["patient_id"] = "P1"
        AH._write(path, bad)
        self.refusal("attempt_history_mismatch", self.fx.export)
        bad = copy.deepcopy(clean)
        bad["attempts"] = []
        AH._write(path, bad)
        self.refusal("attempt_history_uncovered", self.fx.export)
        AH._write(path, clean)
        with sqlite3.connect(self.fx.db) as conn:
            conn.execute("UPDATE inferences SET run_id=NULL WHERE id=?", (first["inference_id"],))
        self.refusal("attempt_history_mismatch", self.fx.export)

    def test_resample_without_main_cannot_mean_never_attempted(self):
        rid = self.fx.run()
        with self.fx.writer(rid, new=True) as w:
            self.fx.work(w, rid, resample=True)
        self.refusal("attempt_history_uncovered", self.fx.export)

    def test_initialization_before_membership_crash_and_resume(self):
        rid = self.fx.run(attach=False)
        with self.fx.writer(rid, new=True): pass
        # Identity was published, but membership was not. No work was admitted.
        second = self.fx.run()
        with self.fx.writer(second): pass
        self.assertEqual(self.fx.export()["counters"]["never_attempted"], 3)

    def test_cleanup_retains_history_and_fresh_is_isolated(self):
        rid = self.fx.run()
        with self.fx.writer(rid, new=True) as w: self.fx.work(w, rid)
        old = AH.history_path(self.fx.db, "C").read_bytes()
        checkpoint = self.fx.root / "checkpoint.json"
        checkpoint.write_text('{}')
        campaign = self.fx.root / "campaign.json"
        campaign.write_text('{}')
        with patch.object(R, "_checkpoint_path", return_value=checkpoint), patch.object(R, "_campaign_record_path", return_value=campaign):
            R.clear_checkpoint()
        self.assertEqual(AH.history_path(self.fx.db, "C").read_bytes(), old)
        new = self.fx.run("NEW")
        with self.fx.writer(new, new=True, campaign="NEW"): pass
        self.assertEqual(self.fx.export(campaign="NEW")["counters"]["never_attempted"], 3)

    def test_error_row_validation_and_missing_hash_diagnostic(self):
        rid = self.fx.run()
        with self.fx.writer(rid, new=True) as w:
            with patch.object(R, "match_patient_to_trials", failed_result): self.fx.work(w, rid)
        with sqlite3.connect(self.fx.db) as c:
            c.execute("UPDATE inferences SET candidates_evaluated=2")
        self.refusal("inconsistent_stored_data", self.fx.export)
        with sqlite3.connect(self.fx.db) as c:
            c.execute("UPDATE inferences SET candidates_evaluated=0, patient_data_hash=NULL")
        with self.assertRaises(CE.ExportRefusal) as cm: self.fx.export()
        self.assertEqual(cm.exception.code, "patient_data_hash_mismatch")
        self.assertIn("missing hash does not establish", str(cm.exception))

    def test_crash_windows_in_fresh_processes(self):
        for mode in ("before_start", "after_start", "after_inference"):
            with self.subTest(mode=mode):
                campaign = mode
                rid = self.fx.run(campaign)
                args = [sys.executable, __file__, "--crash", self.fx.db, str(rid),
                        campaign, self.fx.selection.digest, str(self.fx.fhir), mode]
                child = subprocess.run(args, capture_output=True, text=True, timeout=60)
                self.assertEqual(child.returncode, 17, child.stderr)
                with sqlite3.connect(self.fx.db) as conn:
                    count = conn.execute("SELECT COUNT(*) FROM inferences WHERE run_id=?", (rid,)).fetchone()[0]
                self.assertEqual(count, 1 if mode == "after_inference" else 0)
                manifest = self.fx.export(mode, campaign)
                expected = CE.OUTCOME_NOT_IN_CAMPAIGN if mode == "before_start" else CE.OUTCOME_INTERRUPTED
                self.assertEqual(manifest["outcomes"]["P0"]["outcome"], expected)

    def test_prompt_bearing_errors_require_full_integrity(self):
        rid = self.fx.run()
        with self.fx.writer(rid, new=True) as w:
            def error_with_prompt(patient_data, graph):
                result = good_result(patient_data, graph)
                result["error"] = "synthetic error with retained text"
                return result
            with patch.object(R, "match_patient_to_trials", error_with_prompt):
                result = self.fx.work(w, rid)
        iid = result["inference_id"]
        self.assertEqual(self.fx.export("valid-error")["outcomes"]["P0"]["outcome"], CE.OUTCOME_FIRST_ATTEMPT_FAILED)
        with sqlite3.connect(self.fx.db) as conn:
            prompt, digest = conn.execute("SELECT llm_classifier_prompt, llm_classifier_prompt_sha256 FROM inferences WHERE id=?", (iid,)).fetchone()
        for label, broken, hash_value in (
                ("hash", prompt, "bad"),
                ("fence", prompt.replace("<<<TRIAL_DATA", "<<<BROKEN_DATA"), digest),
                ("block-set", prompt.replace("NCT00000001", "NCT00000002"), digest)):
            with self.subTest(label=label):
                with sqlite3.connect(self.fx.db) as conn:
                    conn.execute("UPDATE inferences SET llm_classifier_prompt=?, llm_classifier_prompt_sha256=? WHERE id=?", (broken, hash_value, iid))
                self.refusal("inconsistent_stored_data", self.fx.export)
        with sqlite3.connect(self.fx.db) as conn:
            conn.execute("UPDATE inferences SET llm_classifier_prompt=?, llm_classifier_prompt_sha256=?, candidates_evaluated=0 WHERE id=?", (prompt, digest, iid))
            conn.execute("DELETE FROM trial_matches WHERE inference_id=?", (iid,))
        self.refusal("inconsistent_stored_data", self.fx.export)


def crash_child():
    db, rid, campaign, digest, fhir, mode = sys.argv[2:]
    files = sorted(str(p) for p in Path(fhir).glob("*.json"))
    R.STOP_SWITCH.reset()
    R.spend.SPEND_STOP.reset()
    with AH.open_writer(db, campaign, int(rid), digest, files, new_campaign=True) as w:
        if mode == "before_start": os._exit(17)
        if mode == "after_start":
            w.start(files[0], "main", int(rid))
            os._exit(17)
        def committed_exit(*args, **kwargs):
            if kwargs.get("write_ok") is not True or not kwargs.get("inference_id"):
                os._exit(99)
            os._exit(17)
        with patch.object(R, "match_patient_to_trials", good_result), patch.object(w, "complete", side_effect=committed_exit):
            R._start_patient_unless_stopped(attempt_history=w, fhir_path=files[0],
                graph=None, run_id=int(rid), db_path=db)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--crash":
        crash_child()
    elif len(sys.argv) > 1 and sys.argv[1] == "--lock":
        try:
            with AH.snapshot_lock(sys.argv[2], "C"):
                raise AssertionError("child acquired writer's lock")
        except AH.HistoryRefusal as exc:
            if exc.code != "attempt_history_busy": raise
    else:
        unittest.main()
