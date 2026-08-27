# The Clinical-Use Framing Reaches Every Verdict Surface
########################################################

"""The framing in ``oncotriage/constants.py`` is on every surface that emits verdicts.

WHY THIS FILE EXISTS
--------------------
``NOT_FOR_CLINICAL_USE`` and ``NOT_FOR_CLINICAL_USE_SHORT`` were authored in the
MCP pass, which said so at the constants themselves: *"ONLY THE MCP SERVER READS
IT TODAY. The API and the dashboard are NOT changed by the pass that added this;
widening a response shape is a contract change and belongs to a pass that
measures it."* This is that pass, and this file is the measurement.

WHAT IS ON EACH SURFACE NOW
    POST /match           a top-level ``not_for_clinical_use`` field, declared
    POST /match/file      ONCE on ``MatchResponse``, which both endpoints share
    GET  /pipeline/info   the same key, in a handler that shares no model with
                          the two above, so the KEY is written a second time and
                          the STRING is not
    the dashboard         one ``st.caption`` in ``dashboard/app.py:main()``,
                          above the tab strip and above both early returns

WHAT IT HOLDS
-------------
    1. THE CONSTANTS. Both exist, both are non-degenerate, they are DISTINCT
       (the split is load-bearing -- the long one qualifies a result, the short
       one is spent from a model's context window on every tool listing), and
       the object each surface reads is the one ``constants.py`` declares.
    2. THE THREE RESPONSES CARRY IT, BYTE-EQUAL. Driven through FastAPI's
       TestClient with the pipeline, the database writer and the Qdrant client
       replaced -- no model call, no row, no network.
    3. NO EXISTING FIELD MOVED. The top-level key set of each response is
       PINNED, and so is ``/pipeline/info``'s ``config`` sub-block, because
       "added a field" and "renamed a field" are the same diff to a client that
       only checks for the new one.
    4. A RETYPED COPY FAILS, ASSERTED BY AST. The long string occurs as a
       literal exactly ONCE in the whole package -- its own assignment -- and
       each of the three sites binds the imported NAME rather than a literal.
       Byte-equality alone cannot see the difference; a retyped copy is equal
       until somebody edits one of them.
    5. THE DASHBOARD RENDERS IT ONCE PER PAGE. All nine tabs are rendered from a
       seeded scratch database through streamlit's ``AppTest`` and the framing is
       counted across EVERY element, not just captions -- "once" is the claim, so
       twice must fail as loudly as none. It is also rendered on the no-data
       early return, which is the path a reader takes just before they add data
       and look at verdicts.
    6. THE MCP SERVER IS THE PRECEDENT AND IS UNTOUCHED. Confirmed against its
       SOURCE by AST rather than by importing it: the ``mcp`` package is a real
       dependency of that module and its absence must not read as a change to
       it.
    7. ELEVEN CONTROLS, each shown to fire. Three exec an in-memory copy of
       ``oncotriage/api/server.py``; two write a copy of
       ``oncotriage/dashboard/app.py`` into a temp directory (AppTest imports a
       module, so a copy has to be importable); five are AST scans over a
       doctored source; one makes a real outbound connection and requires the
       offline guard to block and name it.

WHY ``git show`` CANNOT SUPPLY THE CONTROLS. Every one of them is a state no
commit has ever had: the field on ``MatchResponse`` has no prior revision, and
the interesting plants revert ONE line while leaving the rest of the pass
correct -- the model field retyped while ``/pipeline/info`` still imports, the
caption duplicated, the import removed while the reader stays. An exec'd copy of
``server.py`` also binds the LIVE constants module, which is what makes a plant
in ``server.py`` itself the only thing a probe through it can observe.

NO NETWORK, NO KEYS, NO SPEND, NO GIT, NO CORPUS, NO MODEL CALL, and "no
network" is MEASURED rather than claimed: every API request and every dashboard
render runs with the four socket entry points replaced by a recorder that
raises, with a control that makes a real call and requires it to be blocked. It
needs the placeholder ``.env`` to EXIST -- ``/pipeline/info`` reports which
source supplied the Qdrant endpoint -- which is the same precondition
``tests/test_agent_retrieval_observability.py`` carries and which
``.github/scripts/provision_ci_paths.py`` writes. It writes only inside a
temporary directory, so it is NOT in the collision matrix: the three repository
files it reads are written by neither of the suite's two writers.

Run from terminal:
    python tests/test_clinical_use_framing.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# The package bootstrap every file in tests/ carries. The candidate directory
# is the PARENT of this file's, because the package sits beside tests/ rather
# than inside it. `pip install -e .` makes the whole block a no-op.
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

import ast
import hashlib
import json
import socket
import sqlite3
import tempfile
import time
import traceback
import types

import streamlit as st
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from oncotriage import constants as _constants
from oncotriage import paths as _paths
from oncotriage.agent import deps as _deps
from oncotriage.api import server as _server
from oncotriage.dashboard import app as _dash_app
from oncotriage.storage.database_logger import initialize_database


_T_START = time.time()

_LONG = _constants.NOT_FOR_CLINICAL_USE
_SHORT = _constants.NOT_FOR_CLINICAL_USE_SHORT


# ===========================================================================
# HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label, actual, expected):
    """Assert equality, record the outcome, never abort the run."""
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


def section(title):
    print()
    print("=" * 75)
    print(title)
    print("=" * 75)


# The files under test, located from their OWN __file__ rather than from this
# test's directory, so a future move of any of them cannot silently point a
# plant or a scan at a same-named copy.
_SERVER_SRC = os.path.abspath(_server.__file__)
_DASH_SRC = os.path.abspath(_dash_app.__file__)
_CONSTANTS_SRC = os.path.abspath(_constants.__file__)
_PKG_ROOT = os.path.dirname(_CONSTANTS_SRC)
_MCP_SRC = os.path.join(_PKG_ROOT, "mcp", "server.py")


def _read(path):
    return open(path, encoding="utf-8").read()


def _sha256_of(path):
    return hashlib.sha256(_read(path).encode()).hexdigest()


# Taken before any plant runs, so the restore assertion in section 7 compares
# against a real baseline rather than against itself.
_WATCHED = (_SERVER_SRC, _DASH_SRC, _CONSTANTS_SRC)
_SHA_BEFORE = {p: _sha256_of(p) for p in _WATCHED}

_TMP = tempfile.mkdtemp(prefix="clinical_use_framing_")


class _PlantFailed(Exception):
    """A plant that did not apply or did not compile. Never escapes a check."""


# TWO COUNTERS, BECAUSE THEY COUNT DIFFERENT THINGS and the first version of
# section 7 conflated them: it asserted "eleven plants ran" against a counter
# that only advanced when a doctored source was EXEC'd or WRITTEN, so the five
# AST-only controls -- which doctor a source and then merely parse it -- were
# invisible to it and it reported 6. `_PLANT_SEQ` counts every doctored source;
# `_COPY_SEQ` counts the subset that becomes a module, and is what makes each
# temp-file plant's name unique.
_PLANT_SEQ = [0]
_COPY_SEQ = [0]


def _doctor(path, subs):
    """The source at `path` with `subs` applied. Nothing on disk is touched.

    Raises _PlantFailed -- never IndexError, never SyntaxError -- so a plant
    whose target has moved is a RECORDED failure instead of a traceback that
    hides every check below it.
    """
    _PLANT_SEQ[0] += 1
    source = _read(path)
    for old, new in subs:
        if source.count(old) != 1:
            raise _PlantFailed(
                f"plant target occurs {source.count(old)} times, needs exactly "
                f"1: {old[:70]!r}...")
        source = source.replace(old, new, 1)
    return source


def _planted_module(path, subs):
    """Exec an in-memory COPY of `path` with `subs` applied, return the module."""
    _COPY_SEQ[0] += 1
    source = _doctor(path, subs)
    before = _sha256_of(path)
    try:
        module = types.ModuleType(f"planted_{_COPY_SEQ[0]}")
        module.__file__ = path
        exec(compile(source, path, "exec"), module.__dict__)
    except Exception as exc:              # noqa: BLE001 - reported, not raised
        raise _PlantFailed(f"{type(exc).__name__}: {exc}") from None
    finally:
        if _sha256_of(path) != before:
            raise AssertionError(f"{path} was modified on disk by a plant")
    return module


def _planted_file(path, subs, stem):
    """Write a COPY of `path` with `subs` applied into the temp directory.

    Used where an in-memory exec cannot be: ``AppTest`` runs a script that
    IMPORTS a module by name, so the plant has to be importable. Nothing in the
    repository is written -- the copy lands in a temp directory that is on
    sys.path only for the AppTest child script.
    """
    _COPY_SEQ[0] += 1
    source = _doctor(path, subs)
    before = _sha256_of(path)
    name = f"{stem}_{_COPY_SEQ[0]}"
    with open(os.path.join(_TMP, name + ".py"), "w", encoding="utf-8") as fh:
        fh.write(source)
    if _sha256_of(path) != before:
        raise AssertionError(f"{path} was modified on disk by a plant")
    return name


def control(label, probe, expected):
    """Run a control probe. A raise IS an outcome, not an abort."""
    try:
        actual = probe()
    except _PlantFailed as exc:
        check(f"{label}  [THE PLANT ITSELF FAILED: {exc}]", "plant-failed",
              expected)
        return
    except Exception as exc:              # noqa: BLE001 - a raise IS an outcome
        actual = f"raised {type(exc).__name__}: {exc}"
    check(label, actual, expected)


#------------------------------------------------------------------------------


# ===========================================================================
# THE OFFLINE GUARD
# ===========================================================================
#
# "No network" is a docstring claim in most files in this suite. Here it is
# measured, the way tests/test_dashboard_reproducibility_tab.py measures it: by
# making an outbound call FAIL and recording every attempt, rather than by
# reading the import list. The four primitives below are every way this process
# can open an outbound connection -- ``create_connection`` builds a
# ``socket.socket`` and calls ``connect`` on it, so patching the class method
# covers the helper, and both are patched anyway rather than reasoned about;
# ``getaddrinfo`` is included because a resolver call is an outbound packet even
# when no connection follows it.
#
# THE STAND-INS ARE NAMED FUNCTIONS, NOT LAMBDAS, and the guard's own frames are
# skipped BY NAME -- the reproducibility test shipped the lambda version, whose
# report named its own lambda whatever had actually called out, and whose control
# passed anyway because the lambda is in the same file the control is.

_NETWORK_ATTEMPTS = []
_REAL_SOCKET_CONNECT = socket.socket.connect
_REAL_SOCKET_CONNECT_EX = socket.socket.connect_ex
_REAL_CREATE_CONNECTION = socket.create_connection
_REAL_GETADDRINFO = socket.getaddrinfo

_GUARD_FRAMES = {"_blocked", "_network_caller", "_guard_connect",
                 "_guard_connect_ex", "_guard_create_connection",
                 "_guard_getaddrinfo"}


def _network_caller():
    """The innermost frame outside this guard and the socket module."""
    for frame in reversed(traceback.extract_stack()):
        if os.path.basename(frame.filename) == "socket.py":
            continue
        if frame.name in _GUARD_FRAMES:
            continue
        return f"{os.path.basename(frame.filename)}:{frame.lineno} in {frame.name}"
    return "unknown"


def _blocked(call_name, target):
    where = _network_caller()
    _NETWORK_ATTEMPTS.append({"call": call_name, "target": repr(target),
                              "caller": where})
    raise OSError(f"[offline guard] {call_name} to {target!r} blocked; "
                  f"attempted from {where}")


def _guard_connect(self, address, *a, **k):
    return _blocked("socket.connect", address)


def _guard_connect_ex(self, address, *a, **k):
    return _blocked("socket.connect_ex", address)


def _guard_create_connection(address, *a, **k):
    return _blocked("socket.create_connection", address)


def _guard_getaddrinfo(host, port, *a, **k):
    return _blocked("socket.getaddrinfo", (host, port))


def _arm_offline_guard():
    socket.socket.connect = _guard_connect
    socket.socket.connect_ex = _guard_connect_ex
    socket.create_connection = _guard_create_connection
    socket.getaddrinfo = _guard_getaddrinfo


def _disarm_offline_guard():
    socket.socket.connect = _REAL_SOCKET_CONNECT
    socket.socket.connect_ex = _REAL_SOCKET_CONNECT_EX
    socket.create_connection = _REAL_CREATE_CONNECTION
    socket.getaddrinfo = _REAL_GETADDRINFO


#------------------------------------------------------------------------------


# ===========================================================================
# THE STAND-INS
# ===========================================================================
#
# Nothing here reaches a model, a database or a Qdrant server. The pipeline and
# the writer are replaced ON THE SERVER MODULE (the live one, and each planted
# copy) because that is where the endpoint resolves them; the Qdrant client is
# replaced through oncotriage/agent/deps.py, because /pipeline/info reaches it
# through the agent's seam deliberately -- see the comment at that handler.

_STUB_RESULT = {
    "eligible_trials": [{"nct_id": "NCT-STUB-1", "match_score": 0.75}],
    "not_eligible_trials": [],
    "total_evaluated": 1,
}


def _stub_match(patient_data, graph):
    return dict(_STUB_RESULT)


def _stub_log(result, patient_data, db_path=None):
    return "stub://no-database-write"


class _StubQdrant:
    """Answers the two calls readiness.probe_index makes. No socket."""

    def collection_exists(self, collection_name):
        return True

    def count(self, collection_name, exact=True):
        return types.SimpleNamespace(count=12345)


# The smallest bundle oncotriage/fhir/parser.py accepts as a patient. A literal,
# so this file needs no corpus.
_BUNDLE = {
    "resourceType": "Bundle",
    "type": "collection",
    "entry": [
        {"resource": {"resourceType": "Patient", "id": "FRAMING-P1",
                      "gender": "female", "birthDate": "1962-04-11"}},
    ],
}


def _drive_api(module):
    """(match, match_file, info) responses from one server module.

    The lifespan handler is NOT run -- ``TestClient`` is used without its
    context manager -- so nothing here compiles a graph or probes readiness at
    startup. ``graph`` is set by hand because the two POST handlers read it only
    to decide whether to answer 503.
    """
    saved = (module.graph, module.match_patient_to_trials, module.log_inference)
    module.graph = object()
    module.match_patient_to_trials = _stub_match
    module.log_inference = _stub_log
    try:
        client = TestClient(module.app, raise_server_exceptions=False)
        match = client.post("/match", json={"fhir_bundle": _BUNDLE})
        upload = client.post(
            "/match/file",
            files={"file": ("patient.json", json.dumps(_BUNDLE),
                            "application/json")})
        info = client.get("/pipeline/info")
        return match, upload, info
    finally:
        (module.graph, module.match_patient_to_trials,
         module.log_inference) = saved


#------------------------------------------------------------------------------


# ===========================================================================
# THE AST SCANS
# ===========================================================================


def _package_sources():
    """{relpath: source} for every .py in oncotriage/, at any depth."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(_PKG_ROOT):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if not name.endswith(".py"):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, _PKG_ROOT).replace(os.sep, "/")
            out[rel] = _read(full)
    return out


def _literal_sites(sources, value):
    """[(relpath, lineno)] for every string Constant equal to `value`.

    ADJACENT STRING LITERALS ARE FOLDED BY THE PARSER, which is why this finds
    the declaration in constants.py at all -- it is written as six adjacent
    fragments inside parentheses and arrives as ONE ast.Constant. It is also why
    a retyped copy written the same way is caught.

    THE STATED LIMIT, the same one check 2f of tests/test_package_invariants.py
    carries: a copy assembled at RUN time -- ``a + b``, an f-string, ``"".join``
    -- is not a literal and escapes this scan. The binding checks below are what
    close that for the three sites this pass owns; a fourth site built that way
    elsewhere would not be seen.
    """
    hits = []
    for rel, source in sorted(sources.items()):
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value == value):
                hits.append((rel, node.lineno))
    return sorted(hits)


def _imports_from_constants(source):
    """Names imported from oncotriage.constants at any level of `source`."""
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module == "oncotriage.constants":
            names |= {alias.name for alias in node.names}
    return names


def _model_field_binding(source, class_name, field):
    """Unparsed value of `class_name.field`, or None when it is not declared."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for stmt in node.body:
                if (isinstance(stmt, ast.AnnAssign)
                        and isinstance(stmt.target, ast.Name)
                        and stmt.target.id == field):
                    return None if stmt.value is None else ast.unparse(stmt.value)
    return None


def _dict_value_bindings(source, key):
    """Unparsed values bound to `key` in every dict DISPLAY in `source`."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == key:
                    out.append(ast.unparse(v))
    return sorted(out)


def _as_unparsed(expression_source):
    """`expression_source` as ``ast.unparse`` would render it.

    THE QUOTE STYLE IS NORMALIZED BY ``ast.unparse`` AND THIS FILE SHIPPED THE
    BUG THAT COMES OF FORGETTING IT: control 6h planted a double-quoted literal
    and compared the scan's output against the double-quoted SOURCE, while
    unparse re-emits it single-quoted. The control failed for a reason that had
    nothing to do with what it controls. Every expectation about an unparsed
    node goes through here, so the comparison is between two renderings of the
    same AST rather than between a rendering and a hand-typed string.
    """
    return ast.unparse(ast.parse(expression_source, mode="eval").body)


def _call_args(source, attr):
    """[[unparsed args]] for every ``<anything>.attr(...)`` call in `source`."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == attr):
            out.append([ast.unparse(a) for a in node.args])
    return out


#------------------------------------------------------------------------------


# ===========================================================================
# THE DASHBOARD SEED AND RENDER
# ===========================================================================
#
# EVERY COLUMN IS FILLED, BY DECLARED TYPE, FROM THE PROJECT'S OWN SCHEMA rather
# than from a retyped column list -- initialize_database() creates the tables and
# PRAGMA table_info reports what they are, so a column added tomorrow is seeded
# rather than left NULL. Two seeds were discarded before this one and both are
# worth recording, because each was a render that ABORTED PART WAY and would
# have made "the framing appears exactly once" true for the wrong reason: a NULL
# `total_time` raised TypeError inside the patient explorer, and identical match
# scores across every row raised LinAlgError inside a gaussian_kde density plot.
# The values below are varied per row for exactly that reason.

_TEXT_SEED = {
    "timestamp": None,                       # set per row
    "sex": "female", "race": "White", "ethnicity": "Not Hispanic or Latino",
    "primary_condition": "Malignant neoplasm of breast",
    "expanded_query": "breast carcinoma", "mesh_resolution": "snomed",
    "matching_model": "gpt-5.6-terra",
    "cross_encoder_model": "ncbi/MedCPT-Cross-Encoder",
    "pricing_version": "2026-08-01",
    "qdrant_collection": "trial_criteria_20260101_000000",
    "error": None, "patient_data_hash": "hash-p1",
    "llm_classifier_prompt": "[SYSTEM] stub prompt", "expansion_prompt": None,
    "ablation_flags": None, "ecog_selection": "most_recent_on_or_before",
    "retrieval_channels": "bm25+vector", "query_expansion_path": "mesh",
    "mesh_filter_skip_reason": None, "age_reference_date": "2026-03-01",
    "birth_date_precision": "day", "llm_classifier_prompt_version": "v3",
    "llm_classifier_prompt_sha256": "0" * 16,
}
_INT_SEED = {
    "age": 61, "condition_count": 7, "medication_count": 4, "allergy_count": 1,
    "candidates_retrieved": 100, "candidates_reranked": 40,
    "bm25_retrieved": 75, "vector_retrieved": 100,
    "candidates_after_rule_filter": 30, "candidates_after_quality_filter": 20,
    "candidates_filtered": 60, "mesh_dropped": 5, "stage_dropped": 3,
    "histology_dropped": 2, "candidates_evaluated": 15, "eligible_matches": 2,
    "near_misses": 12, "not_evaluable_trials": 1, "cross_vocab_remaps": 0,
    "llm_classifier_input_tokens": 9000, "llm_classifier_output_tokens": 1200,
    "llm_classifier_retries": 0, "hallucinated_trials": 0, "ecog_value": 1,
    "ecog_observations_found": 2, "retrieval_channels_expected": 2,
    "retrieval_channels_ok": 2, "retrieval_degraded": 0,
    "retrieval_trials_lost": 0, "mesh_filter_applied": 1,
    "llm_classifier_truncation_splits": 0,
    "llm_classifier_output_tokens_estimated": 0, "not_evaluable_truncated": 0,
    "llm_classifier_calls": 1, "llm_classifier_reasoning_tokens": 300,
}
_REAL_SEED = {
    "query_expansion_time": 0.01, "hybrid_retrieval_time": 1.2,
    "cross_encoder_time": 2.3, "rule_filter_time": 0.05,
    "llm_classifier_evaluation_time": 40.0, "total_time": 43.6,
    "estimated_cost_usd": 0.18,
}

_CRITERIA = json.dumps({
    "inclusion": [{"criterion": "ECOG 0-1", "status": "met",
                   "patient_value": "ECOG 1"}],
    "exclusion": [{"criterion": "Pregnancy", "status": "not_violated",
                   "patient_value": "Not applicable"}],
})


def _columns(conn, table):
    return [(row[1], row[2]) for row in conn.execute(f"PRAGMA table_info({table})")]


def _seed_row(cols, overrides):
    out = []
    for name, typ in cols:
        if name in overrides:
            out.append(overrides[name])
        elif name == "id":
            out.append(None)
        elif typ == "TEXT":
            out.append(_TEXT_SEED.get(name, f"{name}-value"))
        elif typ == "REAL":
            out.append(_REAL_SEED.get(name, 0.5))
        else:
            out.append(_INT_SEED.get(name, 1))
    return out


def _build_database(db_path, populated=True):
    """Create the real schema at `db_path`; fill it unless `populated` is False."""
    initialize_database(db_path)
    conn = sqlite3.connect(db_path)
    try:
        if not populated:
            return
        icols = _columns(conn, "inferences")
        tcols = _columns(conn, "trial_matches")
        patients = [("P1", "trial_criteria_20260101_000000"),
                    ("P1", "trial_criteria_20260101_000000"),
                    ("P2", "trial_criteria_20260202_000000"),
                    ("P3", "trial_criteria_20260202_000000"),
                    ("P4", "trial_criteria_20260101_000000"),
                    ("P5", "trial_criteria_20260202_000000")]
        inf, tms = [], []
        for i, (pid, coll) in enumerate(patients, start=1):
            inf.append(_seed_row(icols, {
                "id": i, "patient_id": pid,
                "timestamp": f"2026-03-0{i} 09:00:00",
                "qdrant_collection": coll, "patient_data_hash": "h-" + pid,
                "age": 40 + 7 * i, "sex": "female" if i % 2 else "male",
                "primary_condition": ("Malignant neoplasm of breast",
                                      "Malignant neoplasm of colon",
                                      "Malignant neoplasm of lung")[i % 3],
                "total_time": 30.0 + 3.5 * i,
                "estimated_cost_usd": 0.10 + 0.013 * i,
                "llm_classifier_evaluation_time": 20.0 + 2.1 * i,
                "hybrid_retrieval_time": 0.8 + 0.11 * i,
                "cross_encoder_time": 1.5 + 0.23 * i,
                "rule_filter_time": 0.02 + 0.003 * i,
                "query_expansion_time": 0.005 + 0.001 * i,
                "eligible_matches": i % 3, "near_misses": 14 - (i % 3),
                "llm_classifier_input_tokens": 8000 + 137 * i,
                "llm_classifier_output_tokens": 900 + 41 * i,
                "llm_classifier_reasoning_tokens": 200 + 13 * i,
            }))
            for j, nct in enumerate(("NCT-1", "NCT-2", "NCT-3"), start=1):
                tms.append(_seed_row(tcols, {
                    "id": None, "inference_id": i, "nct_id": nct,
                    "trial_number": j,
                    "eligible": "eligible" if (i + j) % 3 else "not_eligible",
                    "match_score": round(0.05 * ((i * 3 + j) % 17), 3),
                    "rerank_score": 0.4 + 0.017 * (i * j),
                    "rerank_score_raw": -2.0 + 0.31 * (i * j),
                    "mesh_boost": 0.01 * j, "criterion_details": _CRITERIA,
                    "assessment": ("Confirmed." if (i + j) % 3
                                   else "Stage mismatch."),
                }))
        conn.executemany(
            f"INSERT INTO inferences ({','.join(n for n, _ in icols)}) "
            f"VALUES ({','.join('?' * len(icols))})", inf)
        conn.executemany(
            f"INSERT INTO trial_matches ({','.join(n for n, _ in tcols)}) "
            f"VALUES ({','.join('?' * len(tcols))})", tms)
        conn.commit()
    finally:
        conn.close()


_RENDER_SCRIPT = """
import sys
sys.path.insert(0, {tmp!r})
import {module} as _app
_app.main()
"""


def _render_dashboard(module_name="oncotriage.dashboard.app"):
    """Render one dashboard module through AppTest. Returns (exceptions, counts).

    `counts` is how many rendered elements carry the framing string, split by
    the element kind that carries it -- the framing is counted across EVERY
    element rather than only captions, because "once" is the claim and a second
    copy rendered as a markdown block would satisfy a caption-only count.
    """
    st.cache_data.clear()
    del _NETWORK_ATTEMPTS[:]
    _arm_offline_guard()
    try:
        at = AppTest.from_string(
            _RENDER_SCRIPT.format(tmp=_TMP, module=module_name),
            default_timeout=300)
        at.run()
    finally:
        _disarm_offline_guard()

    captions = sum(1 for e in at.caption if e.value == _LONG)
    others = 0
    for kind in ("markdown", "header", "subheader", "title", "info",
                 "warning", "error", "success"):
        for element in getattr(at, kind, []):
            if _LONG in str(getattr(element, "value", "")):
                others += 1
    return ([e.value for e in at.exception],
            {"caption": captions, "other": others},
            at,
            list(_NETWORK_ATTEMPTS))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 1: THE CONSTANTS
# ===========================================================================

section("SECTION 1: the two constants, and that they are not degenerate")

check("1a  the long framing exists and is a str", isinstance(_LONG, str), True)
check("1b  the short framing exists and is a str", isinstance(_SHORT, str), True)
check("1c  non-degeneracy: the long one is a paragraph, not an empty string "
      "(every byte-equality check below would pass on '' either side)",
      len(_LONG) > 400, True)
check("1d  non-degeneracy: the short one is a sentence and is SHORTER than the "
      "long one — the split is what the constants file argues for",
      0 < len(_SHORT) < len(_LONG), True)
check("1e  the two are DISTINCT strings, so a surface reading one cannot be "
      "satisfied by the other", _LONG == _SHORT, False)
check("1f  both open with the same four words, so neither is a caveat about "
      "something else",
      (_LONG.startswith("NOT FOR CLINICAL USE"),
       _SHORT.startswith("NOT FOR CLINICAL USE")), (True, True))

# The object each surface reads is the one constants.py DECLARES -- established
# by evaluating the module's own assignment out of its source rather than by
# comparing the import with itself, which is true by construction.
_CONSTANTS_TREE = ast.parse(_read(_CONSTANTS_SRC))
_DECLARED = {}
for _node in _CONSTANTS_TREE.body:
    if (isinstance(_node, ast.Assign) and len(_node.targets) == 1
            and isinstance(_node.targets[0], ast.Name)
            and _node.targets[0].id.startswith("NOT_FOR_CLINICAL_USE")):
        _DECLARED[_node.targets[0].id] = ast.literal_eval(_node.value)

check("1g  constants.py declares exactly the two framing names at module scope",
      sorted(_DECLARED), ["NOT_FOR_CLINICAL_USE", "NOT_FOR_CLINICAL_USE_SHORT"])
check("1h  the imported long constant is byte-equal to what the source declares",
      _DECLARED.get("NOT_FOR_CLINICAL_USE"), _LONG)
check("1h  ...and the short one likewise",
      _DECLARED.get("NOT_FOR_CLINICAL_USE_SHORT"), _SHORT)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 2: THE THREE RESPONSES
# ===========================================================================

section("SECTION 2: POST /match, POST /match/file and GET /pipeline/info")

_SAVED_DEPS = _deps.set_overrides({_deps.QDRANT_CLIENT: _StubQdrant()})
try:
    del _NETWORK_ATTEMPTS[:]
    _arm_offline_guard()
    try:
        _MATCH, _UPLOAD, _INFO = _drive_api(_server)
    finally:
        _disarm_offline_guard()
    _API_NETWORK = list(_NETWORK_ATTEMPTS)

    check("2a  POST /match answered 200 (without this every check below is "
          "about an error body)", _MATCH.status_code, 200)
    check("2a  POST /match/file answered 200", _UPLOAD.status_code, 200)
    check("2a  GET /pipeline/info answered 200", _INFO.status_code, 200)

    _MATCH_JSON = _MATCH.json()
    _UPLOAD_JSON = _UPLOAD.json()
    _INFO_JSON = _INFO.json()

    check("2b  POST /match carries a top-level not_for_clinical_use",
          "not_for_clinical_use" in _MATCH_JSON, True)
    check("2b  ...whose value is BYTE-EQUAL to oncotriage.constants",
          _MATCH_JSON.get("not_for_clinical_use"), _LONG)
    check("2c  POST /match/file carries it too — one declaration on "
          "MatchResponse, two endpoints",
          _UPLOAD_JSON.get("not_for_clinical_use"), _LONG)
    check("2d  GET /pipeline/info carries it, byte-equal",
          _INFO_JSON.get("not_for_clinical_use"), _LONG)
    check("2e  the two POST endpoints answer with the SAME string, not two "
          "copies that have drifted",
          _MATCH_JSON.get("not_for_clinical_use")
          == _UPLOAD_JSON.get("not_for_clinical_use")
          == _INFO_JSON.get("not_for_clinical_use"), True)
    check("2f  it is the LONG framing and not the short one — the short one is "
          "sized for a tool listing, not for a result",
          _MATCH_JSON.get("not_for_clinical_use") == _SHORT, False)

    # --- 2g. NO EXISTING FIELD MOVED ----------------------------------------
    # "Added a field" and "renamed a field" are the same diff to a client that
    # only looks for the new one, so the key sets are PINNED rather than
    # inspected for the one addition.
    check("2g  POST /match's top-level key set is exactly the old three plus "
          "the new one",
          sorted(_MATCH_JSON),
          ["not_for_clinical_use", "patient_summary", "processing_time_seconds",
           "result"])
    check("2g  POST /match/file's key set is identical to POST /match's",
          sorted(_UPLOAD_JSON), sorted(_MATCH_JSON))
    check("2g  patient_summary's own shape is untouched",
          sorted(_MATCH_JSON["patient_summary"]),
          ["age", "allergy_count", "condition_count", "medication_count",
           "patient_id", "sex"])
    check("2g  GET /pipeline/info's top-level key set is the old six plus the "
          "new one",
          sorted(_INFO_JSON),
          ["architecture", "config", "not_for_clinical_use", "stages",
           "trials_indexed", "trials_indexed_note", "version"])
    # THE `call_mode` BLOCK IS THE ONE ADDITION SINCE THIS PIN WAS WRITTEN, and
    # it is added to the expectation rather than the pin being loosened: the
    # pin's whole job is that a field arrives on purpose. Which Stage 5 arm the
    # process is running was absent from the endpoint that describes the
    # pipeline while it was the single largest lever on what a patient costs.
    # Its own SHAPE is pinned by tests/test_api_call_mode_and_db_health.py,
    # which is where the block's meaning is tested; here it is one key.
    check("2g  ...and its config sub-block is the old nine plus call_mode",
          sorted(_INFO_JSON["config"]),
          ["call_mode", "collection_name", "embedding_model", "matching_model",
           "max_llm_classifier_retries", "max_trials_for_evaluation",
           "medcpt_score_floor", "qdrant_endpoint",
           "quality_threshold_percentile", "top_k_candidates"])

    # --- 2h. the result really came from the pipeline seam -------------------
    # Without this the response could be an error body that happened to carry
    # the framing, and every check above would be about the wrong object.
    check("2h  non-degeneracy: the response body carries the stub pipeline's "
          "result, so the framing was attached to a real match response",
          _MATCH_JSON["result"].get("eligible_trials"),
          _STUB_RESULT["eligible_trials"])
    check("2h  ...and the patient the bundle names came back",
          _MATCH_JSON["patient_summary"]["patient_id"], "FRAMING-P1")

    check("2i  the whole API exercise made no outbound network call",
          _API_NETWORK, [])
finally:
    _deps.restore_overrides(_SAVED_DEPS)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 3: A RETYPED COPY FAILS (AST)
# ===========================================================================
#
# Byte-equality cannot tell one constant read three times from three identical
# literals. They are equal today and diverge the first time anyone edits one --
# which is the whole reason the string lives in constants.py rather than at each
# surface, and is what the CROSS_ENCODER_MODEL and BM25_SPARSE_MODEL_NAME checks
# exist to hold elsewhere in this project.

section("SECTION 3: one literal in the package, and every site binds the name")

_PKG_SOURCES = _package_sources()
_LONG_SITES = _literal_sites(_PKG_SOURCES, _LONG)
_SHORT_SITES = _literal_sites(_PKG_SOURCES, _SHORT)

check("3a  non-degeneracy: the package scan found files to scan",
      len(_PKG_SOURCES) > 40, True)
check("3b  the long framing occurs as a literal exactly ONCE in the package",
      len(_LONG_SITES), 1)
check("3b  ...and that one occurrence is its own declaration in constants.py",
      [rel for rel, _ in _LONG_SITES], ["constants.py"])
check("3c  the short framing likewise",
      [rel for rel, _ in _SHORT_SITES], ["constants.py"])

_SERVER_SOURCE = _read(_SERVER_SRC)
_DASH_SOURCE = _read(_DASH_SRC)
_MCP_SOURCE = _read(_MCP_SRC)

check("3d  oncotriage/api/server.py imports the long framing by name",
      "NOT_FOR_CLINICAL_USE" in _imports_from_constants(_SERVER_SOURCE), True)
check("3d  oncotriage/dashboard/app.py imports it by name",
      "NOT_FOR_CLINICAL_USE" in _imports_from_constants(_DASH_SOURCE), True)

check("3e  MatchResponse's field is bound to the imported NAME, not a literal",
      _model_field_binding(_SERVER_SOURCE, "MatchResponse",
                           "not_for_clinical_use"),
      "NOT_FOR_CLINICAL_USE")
check("3f  /pipeline/info's entry is bound to the imported NAME, and appears "
      "in exactly one dict display",
      _dict_value_bindings(_SERVER_SOURCE, "not_for_clinical_use"),
      ["NOT_FOR_CLINICAL_USE"])
check("3g  the dashboard renders it through st.caption, bound to the name",
      _call_args(_DASH_SOURCE, "caption"), [["NOT_FOR_CLINICAL_USE"]])
check("3g  ...exactly one st.caption call in app.py, so 'once per page' is a "
      "property of the source and not only of one render",
      len(_call_args(_DASH_SOURCE, "caption")), 1)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 4: THE DASHBOARD RENDERS IT ONCE PER PAGE
# ===========================================================================

section("SECTION 4: the dashboard page, rendered headlessly")

_SCRATCH_DB = os.path.join(_TMP, "scratch_inferences.db")
_EMPTY_DB = os.path.join(_TMP, "empty_inferences.db")
_PRODUCTION_DB = os.path.abspath(_paths.inferences_path)

check("4a  the package default resolves to the production database and the "
      "scratch path is NOT it (without this every render below could be "
      "reading the real one)",
      os.path.abspath(_SCRATCH_DB) != _PRODUCTION_DB, True)

_build_database(_SCRATCH_DB, populated=True)
_build_database(_EMPTY_DB, populated=False)

_probe = sqlite3.connect(_SCRATCH_DB)
try:
    _N_INF = _probe.execute("SELECT COUNT(*) FROM inferences").fetchone()[0]
    _N_TM = _probe.execute("SELECT COUNT(*) FROM trial_matches").fetchone()[0]
    _SCHEMA_COLS = {r[1] for r in _probe.execute("PRAGMA table_info(inferences)")}
finally:
    _probe.close()

check("4b  the scratch database is seeded", (_N_INF, _N_TM), (6, 18))
check("4b  ...from the project's own schema rather than a retyped column list "
      "(a column only initialize_database() adds is present)",
      "mesh_filter_skip_reason" in _SCHEMA_COLS, True)

_SAVED_RESOLVED = _paths._RESOLVED.get("inferences_path")
_paths._RESOLVED["inferences_path"] = _SCRATCH_DB
try:
    _EXC, _COUNTS, _AT, _NET = _render_dashboard()

    check("4c  the whole page rendered with no exception — an aborted render "
          "makes 'exactly once' true for the wrong reason", _EXC, [])
    check("4c  non-degeneracy: all nine tabs really rendered, so the count "
          "below is over the whole page",
          len(_AT.tabs) >= 9, True)
    check("4c  ...and the page produced substantive content, not an error card",
          len(_AT.caption) > 5 and len(_AT.markdown) > 20, True)

    check("4d  the framing renders exactly ONCE, as a caption",
          _COUNTS["caption"], 1)
    check("4d  ...and no other element carries it — not per row, not per tab",
          _COUNTS["other"], 0)
    check("4e  the render made no outbound network call", _NET, [])

    # --- 4f. it survives the no-data early return ---------------------------
    _paths._RESOLVED["inferences_path"] = _EMPTY_DB
    _EXC2, _COUNTS2, _AT2, _NET2 = _render_dashboard()
    check("4f  an EMPTY database still renders the framing exactly once",
          (_EXC2, _COUNTS2["caption"], _COUNTS2["other"]), ([], 1, 0))
    check("4f  ...on the early-return path, which is what makes that a "
          "different render and not a repeat of 4d",
          any("No data available" in str(e.value) for e in _AT2.error), True)
finally:
    if _SAVED_RESOLVED is None:
        _paths._RESOLVED.pop("inferences_path", None)
    else:
        _paths._RESOLVED["inferences_path"] = _SAVED_RESOLVED
    st.cache_data.clear()

check("4g  the resolver cache was restored, so nothing after this file reads "
      "the scratch database",
      _paths._RESOLVED.get("inferences_path"), _SAVED_RESOLVED)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 5: THE MCP SERVER IS THE PRECEDENT AND IS UNTOUCHED
# ===========================================================================
#
# READ AS SOURCE, NOT IMPORTED. oncotriage/mcp/server.py imports the `mcp`
# package, which is a real dependency and may be absent on a machine that has
# only the pipeline extras installed -- and an ImportError there would read as
# "this pass changed the MCP server", which is the opposite of what it means.

section("SECTION 5: the MCP server still reads both constants (source only)")

_MCP_IMPORTS = _imports_from_constants(_MCP_SOURCE)
check("5a  it still imports BOTH framing names",
      sorted(n for n in _MCP_IMPORTS if n.startswith("NOT_FOR_CLINICAL_USE")),
      ["NOT_FOR_CLINICAL_USE", "NOT_FOR_CLINICAL_USE_SHORT"])

_MCP_NAMES = [node.id for node in ast.walk(ast.parse(_MCP_SOURCE))
              if isinstance(node, ast.Name)
              and node.id.startswith("NOT_FOR_CLINICAL_USE")]
check("5b  the LONG one is read on the result payloads — five sites, unchanged "
      "by this pass",
      _MCP_NAMES.count("NOT_FOR_CLINICAL_USE") >= 5, True)
check("5c  the SHORT one is read on the tool descriptions",
      _MCP_NAMES.count("NOT_FOR_CLINICAL_USE_SHORT") >= 3, True)
check("5d  and it spells the field the same way this pass spells it, so the "
      "two surfaces answer one fact with one key",
      _dict_value_bindings(_MCP_SOURCE, "not_for_clinical_use"),
      ["NOT_FOR_CLINICAL_USE"] * 5)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 6: THE CONTROLS
# ===========================================================================
#
# Every assertion above must be shown to FAIL when the thing it checks is
# broken. Nothing on disk in the repository is touched: the API controls exec an
# in-memory copy, the dashboard controls write a copy into a temp directory
# (AppTest imports a module by name, so a plant has to be importable), and the
# AST controls parse a doctored string.

section("SECTION 6: eleven planted defects, each required to fire")

_MODEL_FIELD_LINE = "    not_for_clinical_use: str = NOT_FOR_CLINICAL_USE"
_INFO_ENTRY_LINE = '            "not_for_clinical_use": NOT_FOR_CLINICAL_USE,\n'
_CAPTION_LINE = "    st.caption(NOT_FOR_CLINICAL_USE)"
_IMPORT_LINE = "from oncotriage.constants import NOT_FOR_CLINICAL_USE\n"
_RETYPED = repr(_LONG)          # the same VALUE, a different literal token
_NEARLY = ('"NOT FOR CLINICAL USE. Research demonstration; output is '
           'unvalidated."')


def _control_api(subs, extract):
    """Plant into server.py, drive the three endpoints, return `extract(...)`."""
    module = _planted_module(_SERVER_SRC, subs)
    saved = _deps.set_overrides({_deps.QDRANT_CLIENT: _StubQdrant()})
    try:
        return extract(*_drive_api(module))
    finally:
        _deps.restore_overrides(saved)


control("6a  removing the field from MatchResponse makes POST /match answer "
        "without it",
        lambda: _control_api([(_MODEL_FIELD_LINE, "    pass")],
                             lambda m, u, i: "not_for_clinical_use" in m.json()),
        False)

control("6b  ...and POST /match/file with it, since both share the model",
        lambda: _control_api([(_MODEL_FIELD_LINE, "    pass")],
                             lambda m, u, i: "not_for_clinical_use" in u.json()),
        False)

control("6c  a PLAUSIBLE SUMMARY typed in place of the constant is not equal to "
        "it — the byte-equality check in 2b is not satisfied by any paraphrase",
        lambda: _control_api(
            [(_MODEL_FIELD_LINE,
              f"    not_for_clinical_use: str = {_NEARLY}")],
            lambda m, u, i: m.json().get("not_for_clinical_use") == _LONG),
        False)

control("6d  removing /pipeline/info's entry makes that response drop it, "
        "while /match keeps it — so 2d is a check about that endpoint and not "
        "about the model",
        lambda: _control_api(
            [(_INFO_ENTRY_LINE, "")],
            lambda m, u, i: ("not_for_clinical_use" in i.json(),
                             "not_for_clinical_use" in m.json())),
        (False, True))

control("6e  a RETYPED copy of the constant elsewhere in the package makes the "
        "one-literal scan report two sites",
        lambda: len(_literal_sites(
            {"constants.py": _read(_CONSTANTS_SRC),
             "api/server.py": _doctor(
                 _SERVER_SRC,
                 [(_MODEL_FIELD_LINE,
                   f"    not_for_clinical_use: str = {_RETYPED}")])},
            _LONG)),
        2)

control("6f  ...and makes the MatchResponse binding check report a literal "
        "rather than the name",
        lambda: _model_field_binding(
            _doctor(_SERVER_SRC,
                    [(_MODEL_FIELD_LINE,
                      f"    not_for_clinical_use: str = {_RETYPED}")]),
            "MatchResponse", "not_for_clinical_use") == "NOT_FOR_CLINICAL_USE",
        False)

control("6g  a retyped literal at the /pipeline/info site is caught by the "
        "dict-binding scan",
        lambda: _dict_value_bindings(
            _doctor(_SERVER_SRC,
                    [(_INFO_ENTRY_LINE,
                      f'            "not_for_clinical_use": {_RETYPED},\n')]),
            "not_for_clinical_use"),
        [_as_unparsed(_RETYPED)])

control("6h  a retyped literal in the dashboard caption is caught too",
        lambda: _call_args(
            _doctor(_DASH_SRC, [(_CAPTION_LINE, f"    st.caption({_NEARLY})")]),
            "caption"),
        [[_as_unparsed(_NEARLY)]])

control("6i  dropping the import while keeping a reader is caught by the "
        "import scan",
        lambda: "NOT_FOR_CLINICAL_USE" in _imports_from_constants(
            _doctor(_SERVER_SRC, [(_IMPORT_LINE, "")])),
        False)


# --- the two dashboard renders ----------------------------------------------
_paths._RESOLVED["inferences_path"] = _SCRATCH_DB
try:
    _NO_CAPTION = _planted_file(_DASH_SRC, [(_CAPTION_LINE, "    pass")],
                                "plant_dash")
    _TWICE = _planted_file(
        _DASH_SRC, [(_CAPTION_LINE, _CAPTION_LINE + "\n" + _CAPTION_LINE)],
        "plant_dash")

    _E1, _C1, _A1, _ = _render_dashboard(_NO_CAPTION)
    check("6j  removing the caption renders the framing ZERO times",
          (_E1, _C1["caption"] + _C1["other"]), ([], 0))

    _E2, _C2, _A2, _ = _render_dashboard(_TWICE)
    check("6k  rendering it TWICE fails the 'exactly once' count — without "
          "this, 4d would be satisfied by any number above zero",
          (_E2, _C2["caption"]), ([], 2))
finally:
    if _SAVED_RESOLVED is None:
        _paths._RESOLVED.pop("inferences_path", None)
    else:
        _paths._RESOLVED["inferences_path"] = _SAVED_RESOLVED
    st.cache_data.clear()


# --- the offline guard's own control ----------------------------------------
# Every "made no network call" reading above is vacuous if the guard cannot
# see a call. This makes a real one and requires it to be blocked, recorded,
# and attributed to the function that made it.
def _offline_control_call():
    return socket.create_connection(("192.0.2.1", 9), timeout=0.1)


del _NETWORK_ATTEMPTS[:]
_arm_offline_guard()
try:
    _offline_control_call()
    _CONTROL_OUTCOME = "not blocked"
except OSError as _exc:
    _CONTROL_OUTCOME = "blocked" if "offline guard" in str(_exc) else str(_exc)
finally:
    _disarm_offline_guard()

check("6l  the offline guard blocks a real outbound call", _CONTROL_OUTCOME,
      "blocked")
check("6l  ...records it", len(_NETWORK_ATTEMPTS), 1)
check("6l  ...and names the frame that made it, not one of its own",
      _NETWORK_ATTEMPTS[0]["caller"].endswith("in _offline_control_call"), True)
del _NETWORK_ATTEMPTS[:]


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 7: NOTHING IN THE REPOSITORY MOVED
# ===========================================================================

section("SECTION 7: no repository file was written")

_SHA_AFTER = {p: _sha256_of(p) for p in _WATCHED}
check("7a  every source file this run reads or plants into is byte-identical",
      _SHA_AFTER, _SHA_BEFORE)
check("7b  non-degeneracy: the three baseline hashes are distinct, so 7a is not "
      "comparing one file with itself",
      len(set(_SHA_BEFORE.values())), 3)
check("7c  non-degeneracy: eleven defects were actually planted", _PLANT_SEQ[0], 11)
check("7c  ...six of which became a module — four exec'd in memory, two written "
      "as an importable copy for AppTest", _COPY_SEQ[0], 6)
check("7d  every plant landed in the temp directory, never beside the package",
      os.path.commonpath([_TMP, _PKG_ROOT]) not in (_PKG_ROOT,), True)


# ===========================================================================
# SUMMARY
# ===========================================================================

print()
print("=" * 75)
print(f"RESULTS: {_RESULTS['passed']} passed, {_RESULTS['failed']} failed"
      f"  ({time.time() - _T_START:.2f}s)")
print("=" * 75)
if _FAILURES:
    print()
    print("FAILURES")
    for _f in _FAILURES:
        print(f"  {_f}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Aug 10 2026

@author: ramyalsaffar
"""
