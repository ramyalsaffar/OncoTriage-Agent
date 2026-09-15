#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""THE CAMPAIGN DATABASE EXPORT AND THE RAGAS FAITHFULNESS PLACEHOLDER FILTER.

WHAT IS UNDER TEST
------------------
``oncotriage/evaluation/campaign_export.py`` turns one named campaign's first-run
rows into an evaluation run directory, and
``oncotriage/evaluation/faithfulness_filter.py`` decides which recorded
assessments Ragas faithfulness must not score. ``ragas_harness.py`` reads that
decision through ``metric_samples``.

HOW THE DATA IS BUILT
---------------------
Every database is built by the package's own writers -- ``initialize_database``
(schema era 18), ``start_run_record``, ``set_run_billing_campaign_id``,
``log_inference`` and ``finalize_run_record`` -- inside a
``tempfile.mkdtemp`` this file removes and asserts gone. No DDL is written here.
The corpus is FABRICATED FHIR bundles in the same temp tree, drawn with the
runner's own ``cohort_groups.group_map`` and ``cohort.select``. Every stored
prompt is rendered with the real ``render_system_prompt`` and the real
``_render_trial_blocks``, so the exporter's parse rules meet the pipeline's
exact byte format.

WHAT THIS FILE DOES NOT TOUCH
-----------------------------
No network (a socket guard raises on every outbound attempt, with a firing
control), no keys, NO SPEND, no model load (``ONCOTRIAGE_DEFER_LOCAL_MODELS``
above the imports), no live Qdrant, no corpus, no git history. The recorded
production database is refused before open for the whole file
(``tests/_db_snapshot.py``); section 9 reads it only as a verified frozen byte
copy and SKIPS, counted, on a checkout without it.

PLANTS
------
Every planted revert is a COPY of a shipped module with exact anchors replaced,
parsed with ``ast`` before use (a plant that matched nothing or does not parse
is a recorded PLANT-FAILED), written into the temp tree and imported by name.
Nothing is exec'd and nothing is loaded by location.
"""

import ast
import contextlib
import hashlib
import importlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ["ONCOTRIAGE_DEFER_LOCAL_MODELS"] = "1"

try:
    import oncotriage                                          # noqa: F401
except ImportError:
    for _candidate, _how in (
        (os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
         if "__file__" in globals() else None, "__file__"),
        (os.getcwd(), "cwd"),
    ):
        if _candidate and os.path.isdir(os.path.join(_candidate,
                                                     "oncotriage")):
            if _candidate not in sys.path:
                sys.path.insert(0, _candidate)
            print(f"[Bootstrap] oncotriage package found at {_candidate} "
                  f"(via {_how}); added to sys.path")
            break
    else:
        raise

# THE BOOTSTRAP ONLY PUTS THE PACKAGE ON sys.path; it does not import it. On a
# checkout where the package is not pip-installed, the name `oncotriage` is
# unbound here, and `_CODE_DIR` below read `oncotriage.__file__` from it -- a
# NameError that aborted the whole file. Found by the tree-level revert matrix,
# whose copied tree has no editable install.
import oncotriage                                               # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db_snapshot                                             # noqa: E402

from oncotriage import config                                   # noqa: E402
from oncotriage import paths as _paths                          # noqa: E402
from oncotriage import run_fingerprint as RF                    # noqa: E402
from oncotriage.agent import evaluation as EV                   # noqa: E402
from oncotriage.agent.patient import compute_patient_hash       # noqa: E402
from oncotriage.agent.prompts import (                          # noqa: E402
    PROMPT_VERSION, prompt_sha256, render_system_prompt)
from oncotriage.evaluation import campaign_export as CE         # noqa: E402
from oncotriage.evaluation import cohort, cohort_groups         # noqa: E402
from oncotriage.evaluation import faithfulness_filter as FF     # noqa: E402
from oncotriage.evaluation import ragas_harness as RH           # noqa: E402
from oncotriage.evaluation import rater as R                    # noqa: E402
from oncotriage.fhir.parser import parse_fhir_bundle            # noqa: E402
from oncotriage.storage import attempt_history as AH
from oncotriage.storage import database_logger as DL            # noqa: E402

_CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(oncotriage.__file__)))


#------------------------------------------------------------------------------
# Harness
#------------------------------------------------------------------------------

_RESULTS = {"passed": 0, "failed": 0, "skipped": 0}
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


def check_true(label, actual):
    check(label, bool(actual), True)


def fail(label, detail):
    _RESULTS["failed"] += 1
    _FAILURES.append(label)
    print(f"  FAIL  {label}\n          {detail}")


def skip(label, reason):
    _RESULTS["skipped"] += 1
    print(f"  SKIP  {label} -- {reason}")


def section(title):
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def drive(fn, *args, **kwargs):
    """A raise becomes a marker string, so a defect FAILS a check rather than
    aborting the file while a ``check`` argument is being evaluated."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:                                   # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


def refusal(fn, *args, **kwargs):
    """``(code, message)`` of an ExportRefusal from any module copy, or a marker."""
    try:
        fn(*args, **kwargs)
        return ("<no refusal>", "")
    except Exception as exc:                                   # noqa: BLE001
        code = getattr(exc, "code", None)
        if code is not None:
            return (code, str(exc))
        return (f"<{type(exc).__name__}>", str(exc))


def at(value, *keys, default="<absent>"):
    for key in keys:
        try:
            value = value[key]
        except Exception:                                      # noqa: BLE001
            return default
    return value


def sha_file(path):
    if not os.path.exists(path):
        return "absent"
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def quiet(*_a, **_k):
    return None


#------------------------------------------------------------------------------
# The offline guard and the production guard
#------------------------------------------------------------------------------

_NET_ATTEMPTS = []
_REAL_NET = (socket.socket.connect, socket.socket.connect_ex,
             socket.create_connection, socket.getaddrinfo)


def _net_refuse(kind, addr):
    _NET_ATTEMPTS.append((kind, repr(addr)))
    raise PermissionError(f"offline guard: outbound {kind} to {addr!r}")


def _g_connect(self, addr):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        return _net_refuse("connect", addr)
    return _REAL_NET[0](self, addr)


def _g_connect_ex(self, addr):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        return _net_refuse("connect_ex", addr)
    return _REAL_NET[1](self, addr)


def _g_create_connection(address, *a, **k):
    return _net_refuse("create_connection", address)


def _g_getaddrinfo(host, *a, **k):
    return _net_refuse("getaddrinfo", host)


socket.socket.connect = _g_connect
socket.socket.connect_ex = _g_connect_ex
socket.create_connection = _g_create_connection
socket.getaddrinfo = _g_getaddrinfo

try:
    _PROD_DB = os.path.abspath(_paths.inferences_path)
except Exception as _exc:                                      # noqa: BLE001
    _PROD_DB = None
_PROD_GUARD = _db_snapshot.ProductionConnectGuard(_PROD_DB).install()
_PROD_DIGESTS_BEFORE = (_db_snapshot.file_digests(_PROD_DB) if _PROD_DB
                        else None)

_WATCHED = {name: os.path.abspath(mod.__file__) for name, mod in (
    ("campaign_export.py", CE), ("faithfulness_filter.py", FF),
    ("ragas_harness.py", RH), ("rater.py", R), ("database_logger.py", DL),
    ("evaluation.py", EV))}
_DIGESTS_BEFORE = {k: sha_file(v) for k, v in _WATCHED.items()}

_TMP = tempfile.mkdtemp(prefix="oncotriage-campaign-export-")
_PLANT_DIR = os.path.join(_TMP, "plants")
os.makedirs(_PLANT_DIR)
sys.path.insert(0, _PLANT_DIR)
_SEQ = [0]


def plant(module, replacements, prefix):
    """A COPY of ``module``'s source with exact anchors replaced, imported by name.

    Returns the imported module, or None after recording PLANT-FAILED.
    """
    source_path = module.__file__
    text = Path(source_path).read_text(encoding="utf-8")
    for old, new in replacements:
        if text.count(old) != 1:
            fail(f"PLANT-FAILED {prefix}",
                 f"anchor occurs {text.count(old)} time(s): {old[:90]!r}")
            return None
        text = text.replace(old, new)
    try:
        ast.parse(text)
    except SyntaxError as exc:
        fail(f"PLANT-FAILED {prefix}", f"the planted copy does not parse: {exc}")
        return None
    _SEQ[0] += 1
    name = f"{prefix}_plant_{_SEQ[0]}"
    Path(os.path.join(_PLANT_DIR, name + ".py")).write_text(text,
                                                            encoding="utf-8")
    importlib.invalidate_caches()
    return importlib.import_module(name)


#------------------------------------------------------------------------------
# Fixture builders
#------------------------------------------------------------------------------

_CODES = (("254837009", "Malignant neoplasm of breast (disorder)"),
          ("363406005", "Malignant neoplasm of colon (disorder)"),
          ("399068003", "Malignant tumor of prostate (disorder)"))
_REF_DATE = "2026-08-03"
_WIRE_MODEL = config.matching_wire_model()


def write_corpus(directory, n):
    os.makedirs(directory, exist_ok=True)
    for i in range(n):
        code, display = _CODES[i % len(_CODES)]
        bundle = {"resourceType": "Bundle", "type": "collection", "entry": [
            {"resource": {"resourceType": "Patient", "id": f"pt-{i:02d}",
                          "gender": "female" if i % 2 else "male",
                          "birthDate": f"19{50 + i}-03-01"}},
            {"resource": {
                "resourceType": "Condition",
                "clinicalStatus": {"coding": [{"code": "active"}]},
                "verificationStatus": {"coding": [{"code": "confirmed"}]},
                "onsetDateTime": "2020-01-01",
                "code": {"coding": [{"system": "http://snomed.info/sct",
                                     "code": code, "display": display}]}}}]}
        Path(os.path.join(directory, f"Stem{i:02d}_pt-{i:02d}.json")).write_text(
            json.dumps(bundle), encoding="utf-8")
    return sorted(str(p) for p in Path(directory).glob("*.json"))


def fixed_fingerprint(cohort_size, cohort_seed):
    fp = {"fingerprint_version": RF.FINGERPRINT_VERSION}
    for field in RF.FINGERPRINT_FIELDS:
        fp[field] = f"fixed-{field}"
    fp.update(collection_points=100, campaign_cohort_size=cohort_size,
              campaign_cohort_seed=cohort_seed,
              matching_per_trial_empty_retries=1,
              matching_per_trial_parallel_bound=4)
    return fp


def trial_obj(nct):
    return {"trial": {"nct_id": nct, "phase": "PHASE2", "title": f"Trial {nct}",
                      "eligibility": {
                          "inclusion_criteria": f"Inclusion Criteria:\n* adult {nct}",
                          "exclusion_criteria": "Exclusion Criteria:\n* pregnant"}}}


def row_met(criterion, value):
    return {"criterion": criterion, "status": "met", "patient_value": value}


def spec_eligible(nct):
    return {"nct_id": nct, "eligible": "eligible", "match_score": 1.0,
            "assessment": "Eligible: no disqualifying row.",
            "inclusion_criteria": [row_met("adult", "age 60")],
            "exclusion_criteria": [], "not_evaluable_reason": None,
            "verdict_source": "canonical", "emission_index": 0, "call_index": 1}


def spec_rejected(nct):
    return {"nct_id": nct, "eligible": "not_eligible", "match_score": 0.0,
            "assessment": "Known disqualifier: pregnant.",
            "inclusion_criteria": [],
            "exclusion_criteria": [{"criterion": "pregnant", "status": "violated",
                                    "patient_value": "pregnant"}],
            "not_evaluable_reason": None, "verdict_source": "canonical",
            "emission_index": 0, "call_index": 2}


def spec_call_failed(nct):
    entry = EV._unevaluable_entry(trial_obj(nct), EV.NOT_EVALUABLE_CALL_FAILED)
    entry["verdict_source"] = None
    return entry


def spec_declared(nct):
    return {"nct_id": nct, "eligible": "not_evaluable", "match_score": 0.0,
            "assessment": "Not evaluable: the record has no staging.",
            "inclusion_criteria": [], "exclusion_criteria": [],
            "not_evaluable_reason": EV.UNEVALUABLE_MODEL_DECLARED,
            "verdict_source": "canonical", "emission_index": 0, "call_index": 4}


def spec_corrected(nct):
    return {"nct_id": nct, "eligible": "not_evaluable", "match_score": 0.0,
            "assessment": EV.ASSESSMENT_UNSUPPORTED_REJECTION_TEXT,
            "inclusion_criteria": [row_met("adult", "age 60"),
                                   row_met("ECOG 0-1", "ECOG 1")],
            "exclusion_criteria": [],
            "not_evaluable_reason": EV.UNEVALUABLE_REJECTION_UNSUPPORTED,
            "verdict_source": "canonical", "emission_index": 0, "call_index": 5}


class Campaigns(object):
    """One corpus, one cohort, and a database holding four campaigns' runs."""

    def __init__(self, root):
        self.root = root
        self.corpus = os.path.join(root, "fhir")
        self.files = write_corpus(self.corpus, 14)
        self.groups = cohort_groups.group_map(self.files, out=quiet,
                                              use_cache=False)
        self.selection = cohort.select(
            self.files, size=10, seed=42, stability_size=3, stability_seed=43,
            judge_size=7, judge_seed=44,
            group_of=cohort_groups.grouper(self.groups))
        self.fingerprint = fixed_fingerprint(10, 42)
        self.patients = {}
        for path in self.files:
            parsed = parse_fhir_bundle(path)
            self.patients[cohort.stem_of(path)] = parsed
        judged = sorted(self.selection.judge_stems)
        self.judged = [self.patients[s] for s in judged]
        non_judged = [s for s in self.selection.stems if s not in judged]
        self.non_judged = self.patients[non_judged[0]]
        self.db = os.path.join(root, "campaigns.db")
        DL.initialize_database(self.db)
        self._clock = [0]
        self.ids = {}
        self.histories = {}
        self.seen_campaigns = set()
        self._build()

    def _run(self, campaign, status, resumed):
        rid = DL.start_run_record("batch_runner", db_path=self.db,
                                  fingerprint=self.fingerprint, resumed=resumed,
                                  cohort=self.selection.record())
        DL.set_run_billing_campaign_id(rid, campaign, self.db)
        cm = AH.open_writer(self.db, campaign, rid, self.selection.digest,
                            [p for p in self.files if cohort.stem_of(p) in self.selection.stems],
                            new_campaign=campaign not in self.seen_campaigns)
        self.histories[rid] = (cm, cm.__enter__())
        self.seen_campaigns.add(campaign)
        return rid, status

    def _close(self, run):
        DL.finalize_run_record(run[0], run[1], db_path=self.db)
        self.histories[run[0]][0].__exit__(None, None, None)

    def add(self, label, patient, run_id, kind, specs=()):
        self._clock[0] += 1
        result = {
            "patient_id": patient["patient_id"],
            "timestamp": f"2026-09-10T10:{self._clock[0]:02d}:00",
            "matching_model": _WIRE_MODEL, "primary_condition": "cancer",
            "patient_data_hash": compute_patient_hash(patient),
            "llm_classifier_prompt_version": PROMPT_VERSION,
            "age_reference_date": _REF_DATE, "error": "",
            "matches": [], "near_misses": [], "not_evaluable": []}
        system = render_system_prompt(
            True, "", f"Patient: PT-{label}\nAge: 60\nConditions:\n- cancer")
        if kind == "exported":
            trials = [trial_obj(s["nct_id"]) for s in specs]
            blocks = EV._render_trial_blocks(trials, log_events=False)
            result["llm_classifier_prompt"] = (
                f"[SYSTEM]\n{system}\n\n[USER]\n\nCLINICAL TRIALS:\n"
                f"{''.join(blocks)}\n")
            result["llm_classifier_prompt_sha256"] = prompt_sha256(system)
            result["candidates_evaluated"] = len(specs)
            groups = {"eligible": "matches", "not_evaluable": "not_evaluable"}
            for position, spec in enumerate(specs, start=1):
                entry = dict(spec)
                entry["trial_number"] = position
                entry.setdefault("title", f"Trial {spec['nct_id']}")
                entry.setdefault("phase", "PHASE2")
                result[groups.get(spec["eligible"], "near_misses")].append(entry)
        elif kind == "failed":
            # A failed warmup row: a hash beside an EMPTY prompt, which is why
            # the export classifies on `error` and never on the hash.
            result["error"] = "Stage 5 per-trial cache warmup error (attempt 3)"
            result["llm_classifier_prompt"] = ""
            result["llm_classifier_prompt_sha256"] = prompt_sha256(system)
            result["candidates_evaluated"] = 0
        elif kind == "nothing":
            result["llm_classifier_prompt"] = ""
            result["llm_classifier_prompt_sha256"] = None
            result["candidates_evaluated"] = 0
        hist = self.histories.get(run_id)
        if hist:
            path = next(p for p in self.files if self.patients[cohort.stem_of(p)]["patient_id"] == patient["patient_id"])
            aid = hist[1].start(path, "resample" if "resample" in label else "main", run_id)
        written = DL.log_inference(result, patient, db_path=self.db,
                                   run_id=run_id)
        if not written.ok:
            raise RuntimeError(f"fixture write failed: {written.error}")
        if hist:
            hist[1].complete(aid, outcome="failure" if kind == "failed" else "success",
                             patient_id=patient["patient_id"], write_ok=True,
                             inference_id=written.inference_id)
        self.ids[label] = written.inference_id
        return written.inference_id

    def _build(self):
        j = self.judged
        two = lambda p: [spec_eligible(f"NCT9{p}01"), spec_rejected(f"NCT9{p}02")]
        # An API row (run_id NULL) FIRST, so a global MIN(id) picks it.
        self.add("J3@api", j[3], None, "exported", two(3))
        r1 = self._run("C1", "FINISHED", 0)
        self.add("J0@C1", j[0], r1[0], "exported", two(0))
        self.add("J5@C1", j[5], r1[0], "exported", two(5))
        self._close(r1)
        r2 = self._run("C2", "KILLED", 0)
        self.add("J0@C2r2", j[0], r2[0], "exported", two(0))
        self.add("J1@C2r2_err", j[1], r2[0], "failed")
        self.add("J3@C2r2", j[3], r2[0], "exported",
                 [spec_eligible("NCT00000301"), spec_rejected("NCT00000302"),
                  spec_call_failed("NCT00000303"), spec_declared("NCT00000304"),
                  spec_corrected("NCT00000305")])
        self.add("J6@C2r2", j[6], r2[0], "exported", two(6))
        self.add("NJ@C2r2", self.non_judged, r2[0], "exported", two(8))
        self._close(r2)
        r3 = self._run("C2", "FINISHED", 1)
        self.add("J1@C2r3_ok", j[1], r3[0], "exported", two(1))
        self.add("J2@C2r3_nothing", j[2], r3[0], "nothing")
        self.add("J4@C2r3", j[4], r3[0], "exported", two(4))
        self.add("J0@C2r3_resample", j[0], r3[0], "exported", two(0))
        self._close(r3)
        r4 = self._run("C3", "FINISHED", 0)
        self.add("J0@C3", j[0], r4[0], "exported", two(0))
        self.add("J2@C3", j[2], r4[0], "exported", two(2))
        self._close(r4)
        self.runs = {"r1": r1[0], "r2": r2[0], "r3": r3[0], "r4": r4[0]}

    def expected_c2(self):
        j = [p["patient_id"] for p in self.judged]
        return {
            j[0]: (CE.OUTCOME_EXPORTED, self.ids["J0@C2r2"]),
            j[1]: (CE.OUTCOME_FIRST_ATTEMPT_FAILED, self.ids["J1@C2r2_err"]),
            j[2]: (CE.OUTCOME_NOTHING_TO_EVALUATE, self.ids["J2@C2r3_nothing"]),
            j[3]: (CE.OUTCOME_EXPORTED, self.ids["J3@C2r2"]),
            j[4]: (CE.OUTCOME_EXPORTED, self.ids["J4@C2r3"]),
            j[5]: (CE.OUTCOME_NOT_IN_CAMPAIGN, None),
            j[6]: (CE.OUTCOME_EXPORTED, self.ids["J6@C2r2"]),
        }


def checkpointed(db):
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    finally:
        conn.close()


def clone(src, name):
    checkpointed(src)
    dst = os.path.join(_TMP, name)
    shutil.copyfile(src, dst)
    if os.path.isdir(src + ".attempt-history"):
        shutil.copytree(src + ".attempt-history", dst + ".attempt-history")
    return dst


def mutate(db, sql, params=()):
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def outdir(name):
    return os.path.join(_TMP, "out", name)


os.makedirs(os.path.join(_TMP, "out"))


def outcome_map(manifest):
    return {pid: (o.get("outcome"), o.get("inference_id"))
            for pid, o in (at(manifest, "outcomes", default={}) or {}).items()}


def export_with(module, fx, campaign, name, **kw):
    return drive(module.export_campaign, fx.db, campaign, outdir(name),
                 fx.corpus, out=quiet, **kw)


#==============================================================================
section("0. CONTAINMENT CONTROLS")
#==============================================================================

_probe = drive(socket.create_connection, ("192.0.2.1", 9), 1)
check("0a  the offline guard refuses an outbound connection",
      str(_probe).startswith("<RAISED PermissionError"), True)
check("0b  ...and records it", len(_NET_ATTEMPTS), 1)
_NET_ATTEMPTS.clear()
check_true("0c  the production guard is installed on sqlite3.connect",
           sqlite3.connect == _PROD_GUARD._guard)


#==============================================================================
section("1. P5: THE REASON CLASSES, VERIFIED AGAINST THE CODE THAT WRITES THE TEXT")
#==============================================================================

check("1a  REASONS_CONSTRUCTED equals the agent's NOT_EVALUABLE_REASONS_CONSTRUCTED",
      FF.REASONS_CONSTRUCTED, tuple(EV.NOT_EVALUABLE_REASONS_CONSTRUCTED))
check("1b  the two fixed-correction reasons are the two corrected-rejection markers",
      set(FF.REASONS_FIXED_CORRECTION_TEXT),
      {EV.UNEVALUABLE_REJECTION_UNSUPPORTED, EV.UNEVALUABLE_REMAP_NO_SURVIVOR})
check("1c  the three classes partition NOT_EVALUABLE_REASONS exactly",
      sorted(FF.REASONS_CONSTRUCTED + FF.REASONS_FIXED_CORRECTION_TEXT
             + FF.REASONS_KEPT_MODEL_TEXT),
      sorted(EV.NOT_EVALUABLE_REASONS))
check("1c-i non-degeneracy: the vocabulary has eleven members",
      len(EV.NOT_EVALUABLE_REASONS), 11)
_texts = {EV._unevaluable_entry(trial_obj(n), r)["assessment"]
          for r in FF.REASONS_CONSTRUCTED for n in ("NCT00000001",
                                                    "NCT99999999")}
check("1d  a constructed assessment is a fixed sentence per reason, "
      "independent of the trial", len(_texts), len(FF.REASONS_CONSTRUCTED))
check_true("1e  ...and every one says the trial was not assessed",
           all("Not assessed." in t for t in _texts))
for _reason, _case, _const in (
        (EV.UNEVALUABLE_REJECTION_UNSUPPORTED,
         EV.ASSESSMENT_COMPOSED_UNSUPPORTED_REJECTION,
         EV.ASSESSMENT_UNSUPPORTED_REJECTION_TEXT),
        (EV.UNEVALUABLE_REMAP_NO_SURVIVOR, EV.ASSESSMENT_COMPOSED_REMAP_NO_SURVIVOR,
         EV.ASSESSMENT_REMAP_NO_SURVIVOR_TEXT)):
    _v = {"eligible": "not_evaluable", "not_evaluable_reason": _reason,
          "assessment": "Known disqualifier: model prose about THIS patient.",
          "inclusion_criteria": [row_met("x", "y")], "exclusion_criteria": []}
    check(f"1f  compose_assessment case for {_reason[:30]}...",
          EV.assessment_composition_case(_v), _case)
    check(f"1g  ...stores the constant whatever the draft said",
          EV.compose_assessment(_v), _const)
for _reason in FF.REASONS_KEPT_MODEL_TEXT:
    _v = {"eligible": "not_evaluable", "not_evaluable_reason": _reason,
          "assessment": "Not evaluable: model prose.", "inclusion_criteria": [],
          "exclusion_criteria": []}
    check(f"1h  a {_reason[:34]}... entry keeps the model's draft",
          EV.compose_assessment(_v), "Not evaluable: model prose.")


#==============================================================================
section("2. THE FILTER, AS A PURE FUNCTION")
#==============================================================================

_A, _D = FF.RESPONSE_FIELD_ASSESSMENT, FF.RESPONSE_FIELD_DRAFT
for _field in (_A, _D):
    _e = spec_call_failed("NCT00000009")
    check(f"2a  constructed placeholder excluded under {_field}",
          FF.classify(_e, _field),
          (f"{FF.EXCLUDE_CONSTRUCTED}:{EV.NOT_EVALUABLE_CALL_FAILED}", None))
    check(f"2b  model-declared kept under {_field}",
          FF.classify(spec_declared("NCT1"), _field), (None, None))
    check(f"2c  eligible kept under {_field}",
          FF.classify(spec_eligible("NCT1"), _field), (None, None))
check("2d  a fixed correction sentence is excluded under assessment",
      at(FF.classify(spec_corrected("NCT1"), _A), 0),
      f"{FF.EXCLUDE_FIXED_CORRECTION}:{EV.UNEVALUABLE_REJECTION_UNSUPPORTED}")
check("2e  ...and kept under assessment_draft, where it is the model's prose",
      FF.classify(spec_corrected("NCT1"), _D), (None, None))
_conflict = dict(spec_call_failed("NCT00000009"), emission_index=2)
check("2f  a constructed reason contradicted by provenance is NOT excluded",
      at(FF.classify(_conflict, _A), 0), None)
check_true("2g  ...and the contradiction is returned as a note",
           "contradicts itself" in str(at(FF.classify(_conflict, _A), 1)))
check("2h  no reason: kept, with a note",
      (at(FF.classify({"eligible": "not_evaluable"}, _A), 0),
       bool(at(FF.classify({"eligible": "not_evaluable"}, _A), 1))), (None, True))
check("2i  an unknown reason: kept, with a note",
      at(FF.classify({"eligible": "not_evaluable",
                      "not_evaluable_reason": "zzz"}, _A), 0), None)
check_true("2j  an unknown response field raises",
           str(drive(FF.classify, {}, "nope")).startswith("<RAISED ValueError"))
_moved_reason = plant(FF, [(
    '    "model declared this trial not evaluable",\n'
    '    "trial-level verdict label unresolvable at finalization",\n)',
    '    "trial-level verdict label unresolvable at finalization",\n)')],
    "identity_moves")
if _moved_reason is not None:
    check_true("2l  FILTER_IDENTITY is derived: dropping a reason changes it",
               _moved_reason.FILTER_IDENTITY != FF.FILTER_IDENTITY)
check_true("2k  an absent provenance key is not a conflict (pre-provenance records)",
           at(FF.classify({"eligible": "not_evaluable",
                           "not_evaluable_reason": "per_trial_call_failed"}, _A),
              0) is not None)


#==============================================================================
section("3. THE SYNTHETIC CAMPAIGNS")
#==============================================================================

_FX = Campaigns(os.path.join(_TMP, "fx"))
check("3a  the database is schema era 18",
      sqlite3.connect(_FX.db).execute("PRAGMA user_version").fetchone()[0], 18)
check("3b  four runs across three campaigns",
      [r for r in sqlite3.connect(_FX.db).execute(
          "SELECT billing_campaign_id FROM runs ORDER BY id")],
      [("C1",), ("C2",), ("C2",), ("C3",)])
check("3c  the judge sample has seven patients", len(_FX.judged), 7)
_EXPECTED = _FX.expected_c2()


#==============================================================================
section("4. THE CLEAN EXPORT: SELECTION, ACCOUNTING, LOADERS")
#==============================================================================

_M = export_with(CE, _FX, "C2", "clean")
check_true("4a  CLEAN CONTROL: the export refuses nothing", isinstance(_M, dict))
check("4b  every judged patient's outcome and first-row id", outcome_map(_M),
      _EXPECTED)
_j1 = _FX.judged[1]["patient_id"]
check("4c  the failed first attempt records its later success, not substituted",
      at(_M, "outcomes", _j1, "later_success_in_campaign"), True)
_nolater = clone(_FX.db, "no_later_success.db")
check("4c-i non-degeneracy: the later success is turned into a failure",
      mutate(_nolater, "UPDATE inferences SET error = 'x' WHERE id = ?",
             (_FX.ids["J1@C2r3_ok"],)), 1)
_hist_path = AH.history_path(_nolater, "C2")
_hist_body = AH.read(_hist_path)
for _a in _hist_body["attempts"]:
    if _a["completion"] and _a["completion"]["inference_id"] == _FX.ids["J1@C2r3_ok"]:
        _a["completion"]["outcome"] = "failure"
AH._write(_hist_path, _hist_body)
_MN = drive(CE.export_campaign, _nolater, "C2", outdir("no_later"), _FX.corpus,
            out=quiet)
check("4c-ii ...then later_success_in_campaign is False and not counted",
      (at(_MN, "outcomes", _j1, "later_success_in_campaign"),
       at(_MN, "counters", "first_attempt_failed_with_later_success")),
      (False, 0))
check("4c-iii the finished campaign names no unfinalized run",
      at(_M, "campaign", "unfinalized_run_ids"), [])
_unfin = clone(_FX.db, "unfinalized.db")
mutate(_unfin, "UPDATE runs SET finished_at = NULL, status = 'RUNNING' "
               "WHERE id = ?", (_FX.runs["r3"],))
_MU = drive(CE.export_campaign, _unfin, "C2", outdir("unfinalized"), _FX.corpus,
            out=quiet)
check("4c-iv a run that never finalized is named in the manifest",
      at(_MU, "campaign", "unfinalized_run_ids"), [_FX.runs["r3"]])
check("4d  counters", {k: at(_M, "counters", k) for k in (
    "judge_sample", "exported", "first_attempt_failed",
    "first_attempt_failed_with_later_success", "nothing_to_evaluate",
    "never_attempted")},
      {"judge_sample": 7, "exported": 4, "first_attempt_failed": 1,
       "first_attempt_failed_with_later_success": 1, "nothing_to_evaluate": 1,
       "never_attempted": 1})
check("4e  runs holds ONLY the exported patients",
      sorted(at(_M, "runs", default={})),
      sorted(p for p, (o, _) in _EXPECTED.items() if o == CE.OUTCOME_EXPORTED))
check_true("4f  the failed patient is in outcomes and not in runs",
           _j1 in at(_M, "outcomes", default={})
           and _j1 not in at(_M, "runs", default={}))
_j3 = _FX.judged[3]["patient_id"]
check("4g  lost trials for the patient with a failed trial call",
      (at(_M, "runs", _j3, "lost_trials"),
       at(_M, "runs", _j3, "lost_trials_by_reason")),
      (1, {EV.NOT_EVALUABLE_CALL_FAILED: 1}))
check("4h  lost trials by reason, campaign-wide",
      at(_M, "counters", "lost_trials_by_reason"),
      {EV.NOT_EVALUABLE_CALL_FAILED: 1})
check("4i  the campaign's run ids", at(_M, "campaign", "run_ids"),
      [_FX.runs["r2"], _FX.runs["r3"]])
check("4j  the fingerprint row is the storage layer's own tuple",
      sorted(at(_M, "fingerprint", default={})), sorted(DL.RUN_FINGERPRINT_COLUMNS))
check("4k  the recorded and recomputed cohort digests",
      (at(_M, "cohort", "recorded_cohort_digest"),
       at(_M, "cohort", "recomputed_cohort_digest")),
      (_FX.selection.digest, _FX.selection.digest))
check("4l  environment.age_reference_date for the rater's gate",
      at(_M, "environment", "age_reference_date"), _REF_DATE)

_rec_path = os.path.join(outdir("clean"), str(at(_M, "runs", _j3, "file")))
_REC = drive(lambda: json.loads(Path(_rec_path).read_text(encoding="utf-8")))
check("4m  the record's summary is the SENT text recovered by R2",
      at(_REC, "patient_summary", "text"),
      "Patient: PT-J3@C2r2\nAge: 60\nConditions:\n- cancer")
check("4n  verdicts are in stored block order",
      [v.get("nct_id") for v in at(_REC, "verdicts", default=[])],
      [f"NCT0000030{k}" for k in range(1, 6)])
check("4o  each verdict keeps its reason and provenance",
      [(v.get("not_evaluable_reason"), v.get("verdict_source"),
        v.get("emission_index")) for v in at(_REC, "verdicts", default=[])][2:],
      [(EV.NOT_EVALUABLE_CALL_FAILED, None, None),
       (EV.UNEVALUABLE_MODEL_DECLARED, "canonical", 0),
       (EV.UNEVALUABLE_REJECTION_UNSUPPORTED, "canonical", 0)])
_blocks = EV._render_trial_blocks([trial_obj(f"NCT0000030{k}")
                                   for k in range(1, 6)], log_events=False)
check("4p  each context is byte-identical to the pipeline's own block",
      [c.get("trial_text") for c in at(_REC, "contexts", default=[])], _blocks)

_ABSENT_BEFORE = dict(R.CRITERIA_REFERENCE_ABSENT)
_RATER = drive(R.load_run, outdir("clean"))
check_true("4q  rater.load_run reads the export", hasattr(_RATER, "decisions"))
check("4r  ...with no problems", getattr(_RATER, "problems", "<none>"), [])
check("4s  ...and every exported trial has a verified reference",
      (len(getattr(_RATER, "criteria", {})),
       getattr(_RATER, "criteria_absent", None)),
      (at(_M, "totals", "verdicts"), {}))
check("4t  the absent-reference counter did not move",
      dict(R.CRITERIA_REFERENCE_ABSENT), _ABSENT_BEFORE)
check("4u  decisions read equal the manifest's total",
      len(getattr(_RATER, "decisions", [])),
      at(_M, "totals", "criterion_decisions"))
_rubric, _meta = R.lift_rubric()
_INDEX = drive(R.build_requests, _RATER,
               R.build_system_prompt(_rubric, mode=R.MODE_BLIND), _meta,
               "gpt-5.6-terra", 4096, None, mode=R.MODE_BLIND,
               arm_definitions=R.lift_arm_status_definitions(_rubric))
_REF_META = getattr(_INDEX, "reference_meta", {}) or {}
check("4v  build_requests (offline) builds one request per decision",
      len(getattr(_INDEX, "requests", []) or []),
      len(getattr(_RATER, "decisions", [])))
check("4w  ...at request shape 2, every request carrying the reference",
      (_REF_META.get("request_shape_version"), _REF_META.get("without_reference")),
      (R.REQUEST_SHAPE_CRITERIA_REFERENCE, 0))
check_true("4w-i non-degeneracy: there are requests to count",
           _REF_META.get("requests", 0) > 0)
check("4x  no outbound attempt while loading and building", _NET_ATTEMPTS, [])

_RAGAS = drive(RH.load_run, outdir("clean"))
# A refusal upstream leaves a marker STRING here, and every read below must fail
# a check on it rather than abort the file. The revert matrix found the abort:
# the plant that reads config defaults made the clean export refuse.
_RAGAS_GEN = list(getattr(_RAGAS, "generation", []) or [])
check_true("4y  ragas_harness.load_run reads the export",
           hasattr(_RAGAS, "generation"))
check("4z  ...retrieval and generation sample counts",
      (len(getattr(_RAGAS, "retrieval", [])),
       len(getattr(_RAGAS, "generation", []))),
      (at(_M, "counters", "exported"), at(_M, "totals", "verdicts")))
check("4z-i ...with no problems", getattr(_RAGAS, "problems", "<none>"), [])

# THE SURFACE AN OPERATOR MEETS: the entry point, as a subprocess.
_ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
_cli = subprocess.run(
    [sys.executable, os.path.join(_CODE_DIR, "campaign_export.py"),
     "--source-db", _FX.db, "--campaign-id", "C2",
     "--output-dir", outdir("cli"), "--fhir-dir", _FX.corpus],
    cwd=_CODE_DIR, env=_ENV, capture_output=True, text=True, timeout=600)
check("4za the entry point exits 0 on a valid campaign", _cli.returncode, 0)
check_true("4zb ...prints the export summary",
           "[Export] campaign C2: judge sample 7, exported 4" in
           (_cli.stdout + _cli.stderr))
_cli_manifest = drive(lambda: json.loads(Path(outdir("cli"), "manifest.json")
                                         .read_text(encoding="utf-8")))
check("4zc ...and writes the same outcomes as the in-process export",
      outcome_map(_cli_manifest), _EXPECTED)


#==============================================================================
section("5. PLANTED WRONG ADMISSION SELECTIONS")

for _name, _replacement in (
    ("latest_main", ('            first.setdefault(a["bundle"], a)',
                     '            first[a["bundle"]] = a')),
    ("first_success", ('        if a["kind"] == "main":',
                       '        if a["kind"] == "main" and c is not None and c["outcome"] == "success":')),
    ("anchor_run", ('        if a["kind"] == "main":',
                    '        if a["kind"] == "main" and a["run_id"] == history["runs"][0]["id"]:'))):
    _mod = plant(CE, [_replacement], _name)
    if _mod is not None:
        _pm = export_with(_mod, _FX, "C2", _name)
        check_true(f"5 {_name} control ran", isinstance(_pm, dict))
        _wrong = dict(_EXPECTED)
        if _name in ("latest_main", "first_success"):
            _wrong[_FX.judged[1]["patient_id"]] = (CE.OUTCOME_EXPORTED, _FX.ids["J1@C2r3_ok"])
        else:
            for _idx in (2, 4):
                _wrong[_FX.judged[_idx]["patient_id"]] = (CE.OUTCOME_NOT_IN_CAMPAIGN, None)
        check(f"5 {_name} is detected by its specific wrong first admission outcomes",
              outcome_map(_pm), _wrong)

#==============================================================================
section("6. THE COHORT IS REDRAWN FROM RECORDED VALUES, NOT TODAY'S DEFAULTS")
#==============================================================================

_DEFAULT_NAMES = ("CAMPAIGN_COHORT_SIZE", "CAMPAIGN_COHORT_SEED",
                  "CAMPAIGN_JUDGE_SAMPLE_SIZE", "CAMPAIGN_JUDGE_SEED",
                  "CAMPAIGN_STABILITY_SAMPLE_SIZE", "CAMPAIGN_STABILITY_SEED")
_PATCH = dict(zip(_DEFAULT_NAMES, (4, 999, 2, 7, 1, 5)))
_SAVED = {m: {n: getattr(mod, n) for n in _DEFAULT_NAMES}
          for m, mod in (("config", config), ("cohort", cohort))}


@contextlib.contextmanager
def patched_defaults():
    try:
        for mod in (config, cohort):
            for n, v in _PATCH.items():
                setattr(mod, n, v)
        yield
    finally:
        for m, mod in (("config", config), ("cohort", cohort)):
            for n in _DEFAULT_NAMES:
                setattr(mod, n, _SAVED[m][n])


with patched_defaults():
    _default_draw = cohort.select(_FX.files,
                                  group_of=cohort_groups.grouper(_FX.groups))
    check_true("6a  control: the patch reaches cohort.select's defaults",
               _default_draw.judge_stems != _FX.selection.judge_stems)
    _MP = export_with(CE, _FX, "C2", "defaults_patched")
    check("6b  the shipped export is unchanged under patched defaults",
          outcome_map(_MP) if isinstance(_MP, dict) else _MP, _EXPECTED)
    _mod = plant(CE, [(
        'size=recorded["campaign_cohort_size"],\n'
        '            seed=recorded["campaign_cohort_seed"],\n'
        '            stability_size=recorded["stability_sample_size"],\n'
        '            stability_seed=recorded["stability_sample_seed"],\n'
        '            judge_size=recorded["judge_sample_size"],\n'
        '            judge_seed=recorded["judge_sample_seed"],',
        'size=None, seed=None, stability_size=None, stability_seed=None,\n'
        '            judge_size=None, judge_seed=None,')], "reads_defaults")
    if _mod is not None:
        _code, _msg = refusal(_mod.export_campaign, _FX.db, "C2",
                              outdir("reads_defaults"), _FX.corpus, out=quiet)
        check("6c  PLANT reading the defaults is CAUGHT by the digest check",
              _code, CE.REFUSE_COHORT_DIGEST)
check("6d  the defaults are restored on both modules, by identity",
      all(getattr(mod, n) is _SAVED[m][n]
          for m, mod in (("config", config), ("cohort", cohort))
          for n in _DEFAULT_NAMES), True)


#==============================================================================
section("7. ONE READ TRANSACTION, CLOSED BEFORE ANY WRITE")
#==============================================================================

_j3_id = _FX.ids["J3@C2r2"]


def _concurrent_delete(stage):
    if stage == "first_rows":
        mutate(_TX_DB, "DELETE FROM trial_matches WHERE inference_id = ? AND "
                       "nct_id = 'NCT00000305'", (_j3_id,))


_TX_DB = clone(_FX.db, "tx.db")
_TX = drive(CE.export_campaign, _TX_DB, "C2", outdir("tx"), _FX.corpus,
            out=quiet, _between_reads=_concurrent_delete)
check("7a  the concurrent delete is not seen: the export succeeds",
      outcome_map(_TX) if isinstance(_TX, dict) else _TX, _EXPECTED)
check("7b  ...with the patient's five trials",
      at(_TX, "runs", _j3, "verdicts"), 5)
check("7c  non-degeneracy: the delete really committed during the read",
      sqlite3.connect(_TX_DB).execute(
          "SELECT COUNT(*) FROM trial_matches WHERE inference_id = ?",
          (_j3_id,)).fetchone()[0], 4)

_TX_DB2 = clone(_FX.db, "tx2.db")
_mod = plant(CE, [('        conn.execute("BEGIN")\n', ''),
                  ('        conn.execute("COMMIT")\n', '')], "separate_reads")
if _mod is not None:
    _TX_DB = _TX_DB2
    _code, _msg = refusal(_mod.export_campaign, _TX_DB2, "C2", outdir("tx2"),
                          _FX.corpus, out=quiet,
                          _between_reads=_concurrent_delete)
    check("7d  PLANT with separate reads is CAUGHT (it sees the delete)",
          _code, CE.REFUSE_INCONSISTENT)

_OPENED = []
_real_open = CE._open_readonly
_real_write = CE._write_json
_WRITE_SAW_OPEN = []


def _recording_open(path):
    conn = _real_open(path)
    _OPENED.append(conn)
    return conn


def _is_closed(conn):
    try:
        conn.execute("SELECT 1")
        return False
    except sqlite3.ProgrammingError:
        return True


def _checking_write(path, payload):
    _WRITE_SAW_OPEN.append(any(not _is_closed(c) for c in _OPENED))
    return _real_write(path, payload)


try:
    CE._open_readonly = _recording_open
    CE._write_json = _checking_write
    drive(CE.export_campaign, _FX.db, "C2", outdir("closed"), _FX.corpus,
          out=quiet)
finally:
    CE._open_readonly = _real_open
    CE._write_json = _real_write
check_true("7e  non-degeneracy: the export opened a connection and wrote files",
           len(_OPENED) == 1 and len(_WRITE_SAW_OPEN) >= 2)
check("7f  every connection was closed before the first write",
      any(_WRITE_SAW_OPEN), False)
_probe_conn = _real_open(_FX.db)
check("7g  control: the closed-connection probe detects an open one",
      _is_closed(_probe_conn), False)
_probe_conn.close()
check("7h  the two seams are restored by identity",
      (CE._open_readonly is _real_open, CE._write_json is _real_write),
      (True, True))


#==============================================================================
section("8. EACH REFUSAL, ON ITS OWN TRIGGER")
#==============================================================================

def expect_refusal(label, code, db, *, campaign="C2", corpus=None, name=None,
                   names=None):
    got, msg = refusal(CE.export_campaign, db, campaign,
                       outdir(name or label.split()[0]),
                       corpus or _FX.corpus, out=quiet)
    check(f"{label} -> {code}", got, code)
    if names:
        check_true(f"{label} ...names {names!r}", names in msg)
    return msg


_nonempty = outdir("nonempty")
os.makedirs(_nonempty)
Path(_nonempty, "keep.txt").write_text("x", encoding="utf-8")
check("8a  output directory not empty",
      refusal(CE.export_campaign, _FX.db, "C2", _nonempty, _FX.corpus,
              out=quiet)[0], CE.REFUSE_OUTPUT_NOT_EMPTY)
check("8b  output parent missing",
      refusal(CE.export_campaign, _FX.db, "C2",
              os.path.join(_TMP, "no", "such", "dir"), _FX.corpus,
              out=quiet)[0], CE.REFUSE_OUTPUT_PARENT_MISSING)
expect_refusal("8c  source missing", CE.REFUSE_SOURCE_MISSING,
               os.path.join(_TMP, "absent.db"))
_garbage = os.path.join(_TMP, "not_a_database.db")
Path(_garbage).write_bytes(b"this is not an sqlite database file" * 50)
expect_refusal("8c-i source unreadable", CE.REFUSE_SOURCE_UNREADABLE, _garbage,
               name="8c-i")
_era = clone(_FX.db, "era17.db")
mutate(_era, "PRAGMA user_version = 17")
expect_refusal("8d  schema era 17", CE.REFUSE_SCHEMA_ERA, _era, names="era 17")
expect_refusal("8e  campaign not found", CE.REFUSE_CAMPAIGN_NOT_FOUND, _FX.db,
               campaign="C-nope", names="C-nope")
for _col in CE.RUN_AGREEMENT_COLUMNS:
    _db = clone(_FX.db, f"disagree_{_col}.db")
    _val = sqlite3.connect(_db).execute(f"SELECT {_col} FROM runs WHERE id = ?",
                                        (_FX.runs["r3"],)).fetchone()[0]
    _new = (_val + 1) if isinstance(_val, int) else f"{_val}-changed"
    mutate(_db, f"UPDATE runs SET {_col} = ? WHERE id = ?", (_new, _FX.runs["r3"]))
    expect_refusal(f"8f  runs disagree on {_col}", CE.REFUSE_RUNS_DISAGREE, _db,
                   name=f"disagree_{_col}", names=_col)
check_true("8f-i non-degeneracy: every fingerprint column is an agreement column",
           set(DL.RUN_FINGERPRINT_COLUMNS) <= set(CE.RUN_AGREEMENT_COLUMNS))
_db = clone(_FX.db, "unrecorded.db")
mutate(_db, "UPDATE runs SET judge_sample_seed = NULL")
expect_refusal("8g  cohort values unrecorded", CE.REFUSE_COHORT_UNRECORDED, _db,
               names="judge_sample_seed")
_empty_corpus = os.path.join(_TMP, "empty_corpus")
os.makedirs(_empty_corpus)
expect_refusal("8h  corpus empty", CE.REFUSE_CORPUS_EMPTY, _FX.db,
               corpus=_empty_corpus)
_moved = os.path.join(_TMP, "moved_corpus")
shutil.copytree(_FX.corpus, _moved)
os.remove(os.path.join(_moved, os.path.basename(_FX.files[0])))
_moved_files = sorted(str(p) for p in Path(_moved).glob("*.json"))
_moved_sel = cohort.select(_moved_files, size=10, seed=42, stability_size=3,
                           stability_seed=43, judge_size=7, judge_seed=44,
                           group_of=cohort_groups.grouper(cohort_groups.group_map(
                               _moved_files, out=quiet, use_cache=False)))
check_true("8i-i non-degeneracy: the moved corpus draws a different cohort",
           _moved_sel.digest != _FX.selection.digest)
expect_refusal("8i  cohort digest mismatch", CE.REFUSE_COHORT_DIGEST, _FX.db,
               corpus=_moved)
_twin = [os.path.join(_TMP, "a", "S.json"), os.path.join(_TMP, "b", "S.json")]
check("8j  a judge stem with two bundles",
      refusal(CE.map_judge_stems, ["S"], _twin)[0], CE.REFUSE_STEM_DUPLICATE)
check("8k  a judge stem repeated in the sample",
      refusal(CE.map_judge_stems, ["S", "S"], _twin[:1])[0],
      CE.REFUSE_STEM_DUPLICATE)
check("8l  a judge stem with no bundle",
      refusal(CE.map_judge_stems, ["missing"], _FX.files)[0],
      CE.REFUSE_STEM_NO_BUNDLE)
check("8l-i clean control: map_judge_stems on the real sample",
      len(drive(CE.map_judge_stems, _FX.selection.judge_stems, _FX.files)), 7)
_bad = os.path.join(_TMP, "bad.json")
Path(_bad).write_text("{not json", encoding="utf-8")
check("8m  an unreadable judge bundle",
      refusal(CE.identify_judged, {"S": _bad})[0], CE.REFUSE_BUNDLE_UNREADABLE)
check("8n  one patient_id behind two judge bundles",
      refusal(CE.identify_judged, {"A": _FX.files[0], "B": _FX.files[0]})[0],
      CE.REFUSE_PATIENT_ID_DUPLICATE)
_db = clone(_FX.db, "hash.db")
mutate(_db, "UPDATE inferences SET patient_data_hash = 'deadbeef' WHERE id = ?",
       (_j3_id,))
expect_refusal("8o  patient_data_hash mismatch", CE.REFUSE_HASH_MISMATCH, _db,
               names=f"inference {_j3_id}")

_INCONSISTENT = {
    "empty_prompt_with_candidates": (
        "UPDATE inferences SET llm_classifier_prompt = '' WHERE id = ?",
        (_j3_id,)),
    "r1_no_hash_match": (
        "UPDATE inferences SET llm_classifier_prompt_sha256 = 'x' WHERE id = ?",
        (_j3_id,)),
    "r3_not_lossless": (
        "UPDATE inferences SET llm_classifier_prompt = replace("
        "llm_classifier_prompt, 'CLINICAL TRIALS:\n', 'CLINICAL TRIALS:\njunk\n')"
        " WHERE id = ?", (_j3_id,)),
    "block_set_differs": (
        "UPDATE trial_matches SET nct_id = 'NCT77777777' WHERE inference_id = ? "
        "AND nct_id = 'NCT00000305'", (_j3_id,)),
    "duplicate_nct_in_inference": (
        "UPDATE trial_matches SET nct_id = 'NCT00000301' WHERE inference_id = ? "
        "AND nct_id = 'NCT00000305'", (_j3_id,)),
    "row_count_vs_candidates": (
        "UPDATE inferences SET candidates_evaluated = 6 WHERE id = ?", (_j3_id,)),
    "trial_number_vs_position": (
        "UPDATE trial_matches SET trial_number = 9 WHERE inference_id = ? AND "
        "nct_id = 'NCT00000302'", (_j3_id,)),
    "criterion_details_malformed": (
        "UPDATE trial_matches SET criterion_details = '{' WHERE inference_id = ? "
        "AND nct_id = 'NCT00000301'", (_j3_id,)),
    "age_reference_date_not_uniform": (
        "UPDATE inferences SET age_reference_date = '2026-01-01' WHERE id = ?",
        (_FX.ids["J4@C2r3"],)),
}
# EACH REFUSAL MUST NAME ITS OWN GUARD. The code alone is shared by ten guards,
# and several overlap: a duplicated nct_id also breaks the set comparison, so a
# check on the code would stay green with the duplicate guard deleted.
_INCONSISTENT_NAMES = {
    "empty_prompt_with_candidates": "no stored prompt beside",
    "r1_no_hash_match": "hash to llm_classifier_prompt_sha256",
    "r2_patient_fence_removed": "patient-record open",
    "r3_not_lossless": "does not partition into fenced blocks",
    "block_set_differs": "without a trial row",
    "duplicate_nct_in_inference": "repeats among this inference's trial rows",
    "row_count_vs_candidates": "against candidates_evaluated",
    "trial_number_vs_position": "has trial_number",
    "criterion_details_malformed": "criterion_details is not an object",
    "age_reference_date_not_uniform": "different age_reference_date",
}
def mutate_system_half(db, inference_id, edit):
    """Edit the SYSTEM half of a stored prompt and re-stamp its hash.

    An SQL replace over the whole prompt would change the system half without
    re-stamping llm_classifier_prompt_sha256, so R1 would refuse first and a
    case written for R2 would silently test R1 again.
    """
    conn = sqlite3.connect(db)
    try:
        prompt = conn.execute("SELECT llm_classifier_prompt FROM inferences "
                              "WHERE id = ?", (inference_id,)).fetchone()[0]
        head, marker = "[SYSTEM]\n", "\n\n[USER]\n"
        cut = [i for i in range(len(prompt)) if prompt.startswith(marker, i)][-1]
        system = edit(prompt[len(head):cut])
        cur = conn.execute(
            "UPDATE inferences SET llm_classifier_prompt = ?, "
            "llm_classifier_prompt_sha256 = ? WHERE id = ?",
            (head + system + prompt[cut:], prompt_sha256(system), inference_id))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


_INCONSISTENT["r2_patient_fence_removed"] = (
    lambda db: mutate_system_half(
        db, _j3_id, lambda sysh: sysh.replace("\n<<<END_PATIENT_RECORD>>>\n",
                                              "\nEND\n")), None)
for _name, (_sql, _params) in _INCONSISTENT.items():
    _db = clone(_FX.db, f"inc_{_name}.db")
    _changed = (_sql(_db) if callable(_sql) else mutate(_db, _sql, _params))
    check_true(f"8p  non-degeneracy: the {_name} mutation changed a row",
               _changed == 1)
    expect_refusal(f"8p  inconsistent: {_name}", CE.REFUSE_INCONSISTENT, _db,
                   name=f"inc_{_name}", names=_INCONSISTENT_NAMES[_name])
check("8q  R1 unit: two [USER] markers, one hash match, still splits",
      drive(CE.split_stored_prompt,
            "[SYSTEM]\nA\n\n[USER]\nB\n\n[USER]\nC", prompt_sha256("A")),
      ("A", "B\n\n[USER]\nC"))
check("8r  nothing was written by any refusal",
      sorted(n for n in os.listdir(os.path.join(_TMP, "out"))
             if n.startswith(("disagree_", "inc_", "8", "unrecorded", "hash"))),
      [])

_cli_ref = subprocess.run(
    [sys.executable, os.path.join(_CODE_DIR, "campaign_export.py"),
     "--source-db", _era, "--campaign-id", "C2",
     "--output-dir", outdir("cli_refused"), "--fhir-dir", _FX.corpus],
    cwd=_CODE_DIR, env=_ENV, capture_output=True, text=True, timeout=600)
check("8s  the entry point exits 1 on a refusal", _cli_ref.returncode, 1)
check_true("8t  ...and prints the named refusal",
           f"REFUSED ({CE.REFUSE_SCHEMA_ERA})" in (_cli_ref.stdout
                                                   + _cli_ref.stderr))
check_true("8u  ...having written nothing", not os.path.exists(outdir("cli_refused")))


#==============================================================================
section("9. THE SMOKE DATABASE, READ ONLY AS A VERIFIED FROZEN COPY")
#==============================================================================

if _PROD_DB and os.path.isfile(_PROD_DB):
    _frozen = drive(_db_snapshot.frozen_copy, _PROD_DB,
                    os.path.join(_TMP, "smoke"))
    if isinstance(_frozen, tuple):
        _code, _msg = refusal(CE.export_campaign, _frozen[0], "any",
                              outdir("smoke"), _empty_corpus, out=quiet)
        check("9a  the exporter refuses the smoke database by name", _code,
              CE.REFUSE_SCHEMA_ERA)
        check_true("9b  ...naming its era", "schema era 16" in _msg)
        check("9c  the frozen copy did not change", sha_file(_frozen[0]),
              _frozen[1])
    else:
        fail("9a  the smoke snapshot could not be taken", str(_frozen))
else:
    skip("9a-9c the smoke database", "no recorded production database here")


#==============================================================================
section("10. THE RAGAS FAITHFULNESS FILTER")
#==============================================================================

_gen = {s.nct_id: s for s in getattr(_RAGAS, "generation", [])
        if s.patient_id == _j3}
check("10a placeholder trials carry their exclusion",
      {n: s.faithfulness_exclusion for n, s in _gen.items()},
      {"NCT00000301": None, "NCT00000302": None,
       "NCT00000303": f"{FF.EXCLUDE_CONSTRUCTED}:{EV.NOT_EVALUABLE_CALL_FAILED}",
       "NCT00000304": None,
       "NCT00000305": (f"{FF.EXCLUDE_FIXED_CORRECTION}:"
                       f"{EV.UNEVALUABLE_REJECTION_UNSUPPORTED}")})
_n_gen = len(getattr(_RAGAS, "generation", []))
_faith = drive(RH.metric_samples, _RAGAS, RH.DATASET_GENERATION,
               RH.METRIC_FAITHFULNESS)
check("10b faithfulness scores every generation sample but the two placeholders",
      len(_faith) if isinstance(_faith, list) else _faith, _n_gen - 2)
check("10c response relevancy scores every generation sample",
      len(drive(RH.metric_samples, _RAGAS, RH.DATASET_GENERATION,
                RH.METRIC_RESPONSE_RELEVANCY) or []), _n_gen)
check("10d counts by exclusion", FF.exclusion_counts(_RAGAS_GEN),
      {f"{FF.EXCLUDE_CONSTRUCTED}:{EV.NOT_EVALUABLE_CALL_FAILED}": 1,
       f"{FF.EXCLUDE_FIXED_CORRECTION}:{EV.UNEVALUABLE_REJECTION_UNSUPPORTED}": 1})
check("10e the export's own placeholder count agrees",
      at(_M, "counters", "faithfulness_placeholders_by_exclusion"),
      FF.exclusion_counts(_RAGAS_GEN))
_retr = [s for s in getattr(_RAGAS, "retrieval", []) if s.patient_id == _j3]
check("10f retrieval contexts are unchanged: every trial's block, in order",
      at(_retr, 0).retrieved_contexts if _retr else "<none>", _blocks)
_ACTIVE = RH.active_dataset_metrics(RH.ALL_METRICS)
_PLAN = drive(RH.plan_calls, _RAGAS, _ACTIVE)
check("10g the plan prices faithfulness over the filtered samples",
      at(_PLAN, RH.METRIC_FAITHFULNESS, "samples"), _n_gen - 2)


class _Args(object):
    judge_model = "gpt-5.6-terra"
    temperature = None
    max_tokens = 4096
    reasoning_effort = "medium"
    embedding_model = "text-embedding-3-small"


_ENV_STAMP = {"packages": {"ragas": "absent"}}
_IDENT = drive(RH.resume_identity, _RAGAS, _Args(), _ENV_STAMP)
_IDENT = _IDENT if isinstance(_IDENT, dict) else {}
check("10h the resume identity carries the filter",
      _IDENT.get("faithfulness_filter"), FF.FILTER_IDENTITY)
_pre_filter = {k: v for k, v in _IDENT.items() if k != "faithfulness_filter"}
_IDENT_PRE_FILTER = _pre_filter
_changed = RH.identity_disagreement(_pre_filter, _IDENT)
check("10i a resume from a pre-filter partial refuses, naming the filter",
      [c.split(":")[0] for c in _changed], ["faithfulness_filter"])
_placeholder = _gen.get("NCT00000303")
_reuse = drive(RH.reusable_scores,
               {"scores": [{"pair_key": RH.pair_key(
                   RH.DATASET_GENERATION, RH.METRIC_FAITHFULNESS,
                   _placeholder.as_join() if _placeholder else {}),
                   "inputs_sha256": "x"}]}, _RAGAS, _ACTIVE, _IDENT)
_report = _reuse[1] if isinstance(_reuse, tuple) and len(_reuse) == 2 else {}
check("10j a paid faithfulness score for a placeholder is not in the plan",
      (_report.get("not_in_plan"), _report.get("reused")), (1, 0))

# The draft field: an evaluation-run-shaped copy carrying assessment_draft.
_draft_dir = outdir("draft")
if os.path.isdir(outdir("clean")):
    shutil.copytree(outdir("clean"), _draft_dir)
for _entry in (at(_M, "runs", default={}) or {}).values():
    _p = Path(_draft_dir, str(_entry.get("file")))
    if not _p.is_file():
        continue
    _r = json.loads(_p.read_text(encoding="utf-8"))
    for _v in _r["verdicts"]:
        _v["assessment_draft"] = ("Known disqualifier: model prose."
                                  if _v.get("not_evaluable_reason")
                                  == EV.UNEVALUABLE_REJECTION_UNSUPPORTED
                                  else _v["assessment"])
    _p.write_text(json.dumps(_r), encoding="utf-8")
_DRAFT = drive(RH.load_run, _draft_dir, RH.RESPONSE_FIELD_DRAFT)
check("10k under assessment_draft only the constructed placeholder is excluded",
      FF.exclusion_counts(getattr(_DRAFT, "generation", [])),
      {f"{FF.EXCLUDE_CONSTRUCTED}:{EV.NOT_EVALUABLE_CALL_FAILED}": 1})

# REVERT CONTROLS, each a planted copy of the harness or the filter.
_R_PLANTS = {
    "R1_classify_not_called": (RH, [(
        "            _exclusion, _filter_note = faithfulness_filter.classify(\n"
        "                verdict, response_field)",
        "            _exclusion, _filter_note = None, None")]),
    "R2_metric_samples_unfiltered": (RH, [(
        "        return [s for s in run.generation if s.faithfulness_exclusion "
        "is None]", "        return run.generation")]),
    "R3_identity_key_dropped": (RH, [(
        '        "run_dir": os.path.realpath(run.run_dir),\n'
        '        "faithfulness_filter": faithfulness_filter.FILTER_IDENTITY,\n',
        '        "run_dir": os.path.realpath(run.run_dir),\n'), (
        '                        "embedding_model", "response_field", "run_dir",\n'
        '                        "faithfulness_filter")',
        '                        "embedding_model", "response_field", "run_dir")')]),
    "R6_placeholder_contexts_dropped": (RH, [(
        "            contexts.append((context.get(\"rank\"), nct_id, text))",
        "            if not any(v.get(\"nct_id\") == nct_id and "
        "faithfulness_filter.classify(v, response_field)[0] for v in "
        "(record.get(\"verdicts\") or [])):\n"
        "                contexts.append((context.get(\"rank\"), nct_id, text))")]),
}
for _name, (_module, _repl) in _R_PLANTS.items():
    _mod = plant(_module, _repl, _name.lower())
    if _mod is None:
        continue
    _run = drive(_mod.load_run, outdir("clean"))
    check_true(f"10l {_name}: the planted harness loaded the export",
               hasattr(_run, "generation"))
    if _name.startswith("R1"):
        check(f"10l {_name} is CAUGHT: nothing is excluded",
              FF.exclusion_counts(getattr(_run, "generation", [])), {})
    elif _name.startswith("R2"):
        check(f"10l {_name} is CAUGHT: faithfulness plans every sample",
              len(drive(_mod.metric_samples, _run, RH.DATASET_GENERATION,
                        RH.METRIC_FAITHFULNESS) or []), _n_gen)
    elif _name.startswith("R3"):
        _i = drive(_mod.resume_identity, _run, _Args(), _ENV_STAMP)
        check(f"10l {_name} is CAUGHT: a pre-filter partial resumes unrefused",
              drive(_mod.identity_disagreement, _IDENT_PRE_FILTER, _i), [])
    else:
        _r3 = [s for s in getattr(_run, "retrieval", []) if s.patient_id == _j3]
        check(f"10l {_name} is CAUGHT: the placeholder trials' contexts vanish",
              at(_r3, 0).retrieved_contexts if _r3 else None,
              [b for k, b in enumerate(_blocks) if k not in (2, 4)])

_F_PLANTS = {
    "R4_fixed_correction_kept": [(
        "        if response_field == RESPONSE_FIELD_ASSESSMENT:\n"
        "            return f\"{EXCLUDE_FIXED_CORRECTION}:{reason}\", None",
        "        if False:\n"
        "            return f\"{EXCLUDE_FIXED_CORRECTION}:{reason}\", None")],
    "R5_model_text_excluded": [(
        "    if reason in REASONS_KEPT_MODEL_TEXT:\n        return None, None",
        "    if reason in REASONS_KEPT_MODEL_TEXT:\n"
        "        return f\"{EXCLUDE_CONSTRUCTED}:{reason}\", None")],
}
for _name, _repl in _F_PLANTS.items():
    _fmod = plant(FF, _repl, _name.lower())
    if _fmod is None:
        continue
    _hmod = plant(RH, [(
        "from oncotriage.evaluation import faithfulness_filter, "
        "judge_independence\n",
        f"import {_fmod.__name__} as faithfulness_filter\n"
        "from oncotriage.evaluation import judge_independence\n")],
        _name.lower() + "_harness")
    if _hmod is None:
        continue
    _run = drive(_hmod.load_run, outdir("clean"))
    check_true(f"10l {_name}: the planted harness loaded the export",
               hasattr(_run, "generation"))
    _want = ({f"{FF.EXCLUDE_CONSTRUCTED}:{EV.NOT_EVALUABLE_CALL_FAILED}": 1}
             if _name.startswith("R4") else
             {f"{FF.EXCLUDE_CONSTRUCTED}:{EV.NOT_EVALUABLE_CALL_FAILED}": 1,
              f"{FF.EXCLUDE_CONSTRUCTED}:{EV.UNEVALUABLE_MODEL_DECLARED}": 1,
              f"{FF.EXCLUDE_FIXED_CORRECTION}:"
              f"{EV.UNEVALUABLE_REJECTION_UNSUPPORTED}": 1})
    check(f"10l {_name} is CAUGHT with its own wrong exclusions",
          FF.exclusion_counts(getattr(_run, "generation", [])), _want)


#==============================================================================
section("11. HYGIENE")
#==============================================================================

check("11a no outbound network attempt after the control", _NET_ATTEMPTS, [])
check("11b no connect to the production database was attempted",
      _PROD_GUARD.attempts, [])
if _PROD_DB:
    check("11c the production database files are byte-unchanged",
          _db_snapshot.file_digests(_PROD_DB), _PROD_DIGESTS_BEFORE)
check("11d every watched package file is unchanged",
      {k: sha_file(v) for k, v in _WATCHED.items()}, _DIGESTS_BEFORE)
check_true("11e the production guard is released", _PROD_GUARD.uninstall())
(socket.socket.connect, socket.socket.connect_ex, socket.create_connection,
 socket.getaddrinfo) = _REAL_NET
sys.path.remove(_PLANT_DIR)
shutil.rmtree(_TMP, ignore_errors=True)
check("11f the temp tree is removed", os.path.exists(_TMP), False)

print("\n" + "=" * 78)
print(f"RESULTS: {_RESULTS['passed']} passed, {_RESULTS['failed']} failed, "
      f"{_RESULTS['skipped']} skipped")
for _label in _FAILURES:
    print(f"  FAILED: {_label}")
print("=" * 78)

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)
