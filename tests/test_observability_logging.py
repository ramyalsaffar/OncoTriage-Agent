# Structured logging test
#########################

"""The JSON logger, the correlation ID, the field allowlist, the console channel.

WHAT THIS FILE HOLDS

    1.  ``oncotriage/observability.py`` itself: the record shape, the UTC
        timestamp, the ``NO_CORRELATION`` sentinel, the reserved/allowlist
        disjointness that makes flattening safe.

    2.  The console channel: ``print``'s four keywords honoured, an explicit
        file handle honoured, and stderr -- never stdout.

    3.  THE CORRELATION ID UNDER ``MAX_WORKERS`` THREADS. Every line emitted by
        a worker carries the ID of the patient that worker was running, and no
        ID appears under two patients. Driven through the real accessor with a
        real ``ThreadPoolExecutor``, because the claim is about thread-local
        context and a single-threaded proof of it is no proof at all.

    4.  The bar-aware writer: a live bar takes both channels through
        ``tqdm.write``, and releasing it puts them back.

    5.  THE AGENT, converted. Every log call in ``oncotriage/agent/`` reaches
        allowlisted fields only, and none of them passes an f-string as the
        MESSAGE -- which is the static half of the allowlist, since a formatter
        cannot tell an interpolated string from a constant one.

    6.  STDOUT IS EMPTY across a full six-stage pipeline run. Captured at the
        file-descriptor level, not by rebinding ``sys.stdout``, because a
        library holding ``sys.__stdout__`` or a C extension writing to fd 1
        would walk straight past a Python-level redirect.

    7.  No ``print`` call survives anywhere in the package, and no
        ``builtins.print`` monkey-patch.

    8.  THE THREE PLANTED DEFECTS, each measured to fire: ID isolation, the
        allowlist, and the empty-stdout assertion.

HOW TO RUN

    python tests/test_observability_logging.py

No network, no keys, NO SPEND -- section 6 drives the real graph with the
Qdrant client, the cross-encoder and the OpenAI client all replaced through
``oncotriage/agent/deps.py``, so the six stages run for real over stand-in
data and nothing is billed. It writes nothing in the repository (the plants in
section 8 go to a temp COPY) and is NOT in ``tests/run_serial_tests.py``'s
collision matrix.
"""

import ast
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor


#------------------------------------------------------------------------------


_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CODE_DIR = os.path.dirname(_TESTS_DIR)
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)


passed = 0
failed = 0
_failures = []


def check(label, actual, expected):
    global passed, failed
    if actual == expected:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        _failures.append(label)
        print(f"  FAIL  {label}")
        print(f"        expected: {expected!r}")
        print(f"        actual:   {actual!r}")


def fail(label, why):
    global failed
    failed += 1
    _failures.append(label)
    print(f"  FAIL  {label}")
    print(f"        {why}")


def at(seq, i, default=None):
    """``seq[i]`` that returns a sentinel instead of raising.

    A short list is how a defect in the code under test shows up here, and an
    IndexError at module level would abort the run and report one traceback
    where it owed a summary and every remaining check. This project has shipped
    that shape three times (the query layer, the reproducibility tab, the Docker
    readiness pass); this is the fourth file to refuse to.
    """
    try:
        return seq[i]
    except (IndexError, KeyError, TypeError):
        return default


import oncotriage.observability as obs                        # noqa: E402
from oncotriage.observability import (                        # noqa: E402
    LOGGABLE_FIELDS, NO_CORRELATION, RESERVED_KEYS, console,
    correlation_scope, current_correlation_id, filter_fields, get_logger,
    new_correlation_id,
)
from oncotriage.config import MAX_WORKERS                     # noqa: E402


def capture(fn):
    """Run ``fn`` with stderr captured; return (stderr text, stdout text)."""
    err, out = io.StringIO(), io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
        fn()
    return err.getvalue(), out.getvalue()


def records(text):
    """Every line of ``text`` that parses as a JSON object."""
    got = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            got.append(parsed)
    return got


#------------------------------------------------------------------------------


print("=" * 70)
print("SECTION 1 -- the record shape")
print("=" * 70)

_log = get_logger("oncotriage.section1")

_err, _out = capture(lambda: _log.info("hello", stage=2, trials_out=75))
_recs = records(_err)

check("1a     one record was emitted", len(_recs), 1)
_r = at(_recs, 0, {})
check("1a     it is a JSON object with the envelope keys",
      sorted(k for k in _r if k in RESERVED_KEYS),
      ["correlation_id", "level", "logger", "message", "ts"])
check("1a     level", _r.get("level"), "INFO")
check("1a     logger name", _r.get("logger"), "oncotriage.section1")
check("1a     message", _r.get("message"), "hello")
check("1a     allowlisted fields are FLATTENED to the top level",
      (_r.get("stage"), _r.get("trials_out")), (2, 75))
check("1a     nothing was dropped", "dropped_fields" in _r, False)
check("1a     nothing reached stdout", _out, "")

# The Z suffix is only honest if the formatter converts to UTC. A local-time
# stamp suffixed Z parses cleanly, sorts cleanly and is wrong by the machine's
# offset -- the failure mode that survives review.
import datetime as _dt                                        # noqa: E402
_ts = _r.get("ts", "")
check("1b     the timestamp claims UTC", _ts.endswith("Z"), True)
try:
    _parsed_ts = _dt.datetime.strptime(_ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=_dt.timezone.utc)
    _skew = abs((_dt.datetime.now(_dt.timezone.utc) - _parsed_ts).total_seconds())
except ValueError:
    _skew = None
check("1b     ...and IS UTC (within 60s of now, not the local offset)",
      _skew is not None and _skew < 60, True)

check("1c     the envelope keys and the allowlist are disjoint",
      sorted(RESERVED_KEYS & LOGGABLE_FIELDS), [])
# Non-degeneracy: an empty allowlist would satisfy the line above for free.
check("1c     ...and neither set is empty",
      (len(RESERVED_KEYS) > 0, len(LOGGABLE_FIELDS) > 20), (True, True))

_err, _ = capture(lambda: _log.warning("warned"))
check("1d     severity is carried", at(records(_err), 0, {}).get("level"),
      "WARNING")

# A field that will not serialise must not lose the line.
class _Unserialisable:
    def __repr__(self):
        return "<unserialisable>"


_err, _ = capture(lambda: _log.info("odd", stage=_Unserialisable()))
_recs = records(_err)
check("1e     a non-serialisable field still produces a record", len(_recs), 1)
check("1e     ...rendered by default=str rather than dropped",
      at(_recs, 0, {}).get("stage"), "<unserialisable>")


#------------------------------------------------------------------------------


print()
print("=" * 70)
print("SECTION 2 -- the correlation ID")
print("=" * 70)

check("2a     outside any scope the sentinel is in force",
      current_correlation_id(), NO_CORRELATION)

_err, _ = capture(lambda: _log.info("orphan"))
check("2a     ...and it is on the record as a KEY, never missing",
      at(records(_err), 0, {}).get("correlation_id"), NO_CORRELATION)

_seen_id = []


def _inside():
    with correlation_scope() as cid:
        _seen_id.append(cid)
        _log.info("inside")


_err, _ = capture(_inside)
_cid = at(_seen_id, 0)
check("2b     a scoped line carries the scope's ID",
      at(records(_err), 0, {}).get("correlation_id"), _cid)
check("2b     the ID is 12 lowercase hex characters",
      bool(_cid) and len(_cid) == 12 and all(c in "0123456789abcdef" for c in _cid),
      True)
check("2b     the ID is not the sentinel", _cid == NO_CORRELATION, False)
check("2c     the scope was RESET on exit", current_correlation_id(),
      NO_CORRELATION)

check("2d     two IDs differ", new_correlation_id() == new_correlation_id(),
      False)

# Nesting: the inner scope wins, and the outer one is restored.
_nested = []


def _nest():
    with correlation_scope("aaaaaaaaaaaa"):
        _nested.append(current_correlation_id())
        with correlation_scope("bbbbbbbbbbbb"):
            _nested.append(current_correlation_id())
        _nested.append(current_correlation_id())


_nest()
check("2e     nested scopes restore the outer ID",
      _nested, ["aaaaaaaaaaaa", "bbbbbbbbbbbb", "aaaaaaaaaaaa"])

# A raise inside the scope must still reset -- the finally is the whole point.
with contextlib.suppress(RuntimeError):
    with correlation_scope("cccccccccccc"):
        raise RuntimeError("boom")
check("2f     a raise inside the scope still resets it",
      current_correlation_id(), NO_CORRELATION)


#------------------------------------------------------------------------------


print()
print("=" * 70)
print(f"SECTION 3 -- NO ID CROSSES BETWEEN PATIENTS ({MAX_WORKERS} threads)")
print("=" * 70)
#
# The claim this section exists for. A module-level global would collide here;
# a ContextVar does not, because a thread starts with an EMPTY context and a
# value set in one worker is unreachable from its siblings. Both halves are
# exercised: the isolation, and the RESET that stops a reused worker thread
# from carrying the previous patient's ID into the next one.

_PATIENTS = [f"patient-{i:03d}" for i in range(MAX_WORKERS * 6)]
_barrier = threading.Barrier(MAX_WORKERS)


def _one_patient(patient_id, sync):
    with correlation_scope():
        if sync:
            # Force every worker to be mid-scope simultaneously. Without this
            # the pool could run the tasks one after another and the test would
            # pass on a global too.
            with contextlib.suppress(threading.BrokenBarrierError):
                _barrier.wait(timeout=20)
        for stage in (1, 2, 3, 4, 5, 6):
            _log.info("stage", patient_id=patient_id, stage=stage)
            time.sleep(0.0005)


def _drive():
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        list(ex.map(lambda p: _one_patient(p, sync=True), _PATIENTS[:MAX_WORKERS]))
        # A second wave on the SAME pool: these land on reused worker threads,
        # which is where a set-without-reset leaks.
        list(ex.map(lambda p: _one_patient(p, sync=False), _PATIENTS[MAX_WORKERS:]))


_err, _out = capture(_drive)
_recs = [r for r in records(_err) if r.get("message") == "stage"]

check("3a     every patient emitted its six stage lines",
      len(_recs), len(_PATIENTS) * 6)
check("3a     ...and stdout stayed empty throughout", _out, "")

_by_patient = {}
_by_cid = {}
for _r in _recs:
    _by_patient.setdefault(_r.get("patient_id"), set()).add(_r.get("correlation_id"))
    _by_cid.setdefault(_r.get("correlation_id"), set()).add(_r.get("patient_id"))

check("3b     every patient's lines share ONE correlation ID",
      sorted({len(v) for v in _by_patient.values()}), [1])
check("3c     every correlation ID belongs to exactly ONE patient",
      sorted({len(v) for v in _by_cid.values()}), [1])
check("3d     as many distinct IDs as patients (nothing was reused)",
      len(_by_cid), len(_PATIENTS))
check("3e     no line fell back to the sentinel",
      NO_CORRELATION in _by_cid, False)
check("3f     the pool really was concurrent (the barrier released)",
      _barrier.broken, False)
check("3g     the scope was reset on the main thread afterwards",
      current_correlation_id(), NO_CORRELATION)


#------------------------------------------------------------------------------


print()
print("=" * 70)
print("SECTION 4 -- the field allowlist, enforced at the formatter")
print("=" * 70)

_kept, _dropped = filter_fields({"stage": 2, "condition_display": "x",
                                 "lab_value": 9.9})
check("4a     filter_fields keeps the allowlisted", _kept, {"stage": 2})
check("4a     ...and names the dropped, sorted",
      _dropped, ["condition_display", "lab_value"])

_CLINICAL = ["condition_display", "conditions", "lab_value", "labs",
             "medications", "patient_summary", "gpt4o_prompt", "prompt",
             "response_preview", "birth_date", "patient_trees", "mesh_terms",
             "expanded_query", "rerank_queries", "disease_query",
             "patient_stage", "criterion_details", "explanation"]
check("4b     no clinical-payload name is on the allowlist",
      sorted(n for n in _CLINICAL if n in LOGGABLE_FIELDS), [])


def _emit_clinical():
    _log.info("stage complete", stage=4, trials_out=15,
              condition_display="Malignant neoplasm of breast",
              lab_value=13.2, patient_summary="62yo F, stage IV ...")


_err, _ = capture(_emit_clinical)
_r = at(records(_err), 0, {})
check("4c     the allowlisted field survived", _r.get("trials_out"), 15)
check("4c     THE CLINICAL VALUES ARE ABSENT FROM THE RECORD",
      [k for k in ("condition_display", "lab_value", "patient_summary")
       if k in _r], [])
check("4c     ...and no clinical VALUE appears anywhere in the line",
      any(v in _err for v in ("Malignant neoplasm of breast", "13.2",
                              "stage IV")), False)
check("4d     the drop is REPORTED, by key name, so it is not silent",
      _r.get("dropped_fields"),
      ["condition_display", "lab_value", "patient_summary"])
check("4d     ...and counted", all(obs.FIELD_DROPS[k] >= 1 for k in
      ("condition_display", "lab_value", "patient_summary")), True)

# ENFORCEMENT IS AT THE FORMATTER, so a caller that bypasses StructuredLogger
# entirely is filtered too. This is the check that makes "at the logger, not at
# each call site" mean something.
import logging as _logging                                    # noqa: E402


def _bypass():
    _logging.getLogger("oncotriage.bypass").info(
        "went around the helper",
        extra={obs.FIELDS_ATTR: {"stage": 1, "lab_value": 42}})


_err, _ = capture(_bypass)
_r = at(records(_err), 0, {})
check("4e     a caller using logging.getLogger directly is still filtered",
      ("lab_value" in _r, _r.get("stage")), (False, 1))
check("4e     ...and is still stamped with a correlation ID",
      _r.get("correlation_id"), NO_CORRELATION)


#------------------------------------------------------------------------------


print()
print("=" * 70)
print("SECTION 5 -- the console channel and the progress bar")
print("=" * 70)

_err, _out = capture(lambda: console.out("a", "b", sep="-", end="!"))
check("5a     sep= and end= are honoured (the monkey-patch discarded both)",
      _err, "a-b!")
check("5a     ...on stderr, not stdout", _out, "")

_handle = io.StringIO()
_err, _out = capture(lambda: console.out("to a file", file=_handle))
check("5b     an explicit file handle is honoured", _handle.getvalue(),
      "to a file\n")
check("5b     ...and nothing leaked to the console", (_err, _out), ("", ""))

_err, _ = capture(lambda: console.out("x", file=sys.stdout))
check("5c     file=sys.stdout is redirected to the console, not obeyed",
      _err, "x\n")

_err, _ = capture(lambda: console.banner("one", "two"))
check("5d     banner emits one line per argument", _err, "one\ntwo\n")

# --- the bar-aware writer -------------------------------------------------
_bar_lines = []
_real_writer = obs._ACTIVE_WRITER
try:
    obs._ACTIVE_WRITER = lambda text, end="\n": _bar_lines.append(text)
    _err, _out = capture(lambda: (console.out("console line"),
                                  _log.info("log line", stage=9)))
    check("5e     a live bar takes the CONSOLE channel through tqdm.write",
          "console line" in _bar_lines, True)
    check("5e     a live bar takes the LOG channel through it too",
          any(l.startswith("{") and '"log line"' in l for l in _bar_lines), True)
    check("5e     ...so neither reached the raw stream and shredded the bar",
          (_err, _out), ("", ""))
finally:
    obs._ACTIVE_WRITER = _real_writer

check("5f     the writer is released afterwards", obs.bar_is_live(), False)
_err, _ = capture(lambda: console.out("after"))
check("5f     ...and output goes back to the stream", _err, "after\n")

# attach/detach nest, and an extra detach cannot poison the next bar.
_t1 = console.attach_bar()
_t2 = console.attach_bar()
console.detach_bar(_t2)
check("5g     nested attach keeps the bar registered", obs.bar_is_live(), True)
console.detach_bar(_t1)
check("5g     ...and the last detach releases it", obs.bar_is_live(), False)
console.detach_bar()
_t3 = console.attach_bar()
check("5h     an unbalanced detach does not stop the NEXT bar registering",
      obs.bar_is_live(), True)
console.detach_bar(_t3)

# --- 5i: THE REAL WRITER, NOT A STAND-IN ----------------------------------
#
# 5e installs a fake into _ACTIVE_WRITER, which proves the ROUTING and is blind
# to what the real writer does with the line. The real one is tqdm.write, whose
# signature is write(s, file=None, end="\n") and which resolves file=None to
# **sys.stdout**. A bare tqdm.write therefore sends every console line and every
# JSON record to stdout for as long as a bar is live -- the MCP protocol stream,
# and the stream section 8 asserts is empty. That defect was in this module,
# shipped past 5e, and was caught by driving a real bar. This is the check that
# would have caught it at rest.
_real_err, _real_out = io.StringIO(), io.StringIO()
with contextlib.redirect_stderr(_real_err), contextlib.redirect_stdout(_real_out):
    _token = console.attach_bar()
    try:
        console.out("console under a real bar")
        _log.info("log under a real bar", stage=7)
    finally:
        console.detach_bar(_token)

check("5i     with the REAL tqdm writer, nothing reaches stdout",
      _real_out.getvalue(), "")
check("5i     ...and the console line is on stderr",
      "console under a real bar" in _real_err.getvalue(), True)
_real_recs = [r for r in records(_real_err.getvalue())
              if r.get("message") == "log under a real bar"]
check("5i     ...and the log record is on stderr, whole and parseable",
      len(_real_recs), 1)


#------------------------------------------------------------------------------


print()
print("=" * 70)
print("SECTION 6 -- the agent, converted")
print("=" * 70)

_AGENT_DIR = os.path.join(_CODE_DIR, "oncotriage", "agent")
_LEVELS = {"debug", "info", "warning", "error", "exception"}


def _agent_log_calls():
    """Every ``log.<level>(...)`` call in oncotriage/agent/, with its file."""
    found = []
    for name in sorted(os.listdir(_AGENT_DIR)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(_AGENT_DIR, name)
        tree = ast.parse(open(path, encoding="utf-8").read(), path)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _LEVELS
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "log"):
                found.append((name, node))
    return found


_calls = _agent_log_calls()
check("6a     the agent has log calls to check at all (non-degeneracy)",
      len(_calls) >= 30, True)

_off_list = sorted({(f, kw.arg) for f, node in _calls for kw in node.keywords
                    if kw.arg is not None and kw.arg not in LOGGABLE_FIELDS})
check("6b     every field the agent logs is on the allowlist", _off_list, [])

_starstar = sorted({f for f, node in _calls for kw in node.keywords
                    if kw.arg is None})
check("6b     ...and none of them passes **kwargs, which would hide a field",
      _starstar, [])

# THE STATIC HALF OF THE ALLOWLIST. A formatter cannot tell an f-string from a
# constant -- by the time the message reaches it, it is a str either way. So
# the convention "the message is a template, the data goes in fields" is
# enforced here instead, by AST, where an f-string is still an ast.JoinedStr.
_fstring_messages = sorted({(f, at(node.args, 0).lineno)
                            for f, node in _calls
                            if isinstance(at(node.args, 0), ast.JoinedStr)})
check("6c     no agent log call interpolates data into its MESSAGE",
      _fstring_messages, [])

# Non-degeneracy for 6c: the walk must be able to see a JoinedStr at all.
_probe = ast.parse('log.info(f"x{y}")').body[0].value
check("6c     ...and the check can see an f-string when there is one",
      isinstance(at(_probe.args, 0), ast.JoinedStr), True)


#------------------------------------------------------------------------------


print()
print("=" * 70)
print("SECTION 7 -- no print, no monkey-patch, anywhere in the package")
print("=" * 70)


def _package_py():
    for dirpath, dirnames, filenames in os.walk(os.path.join(_CODE_DIR,
                                                             "oncotriage")):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


_print_sites = []
_patch_sites = []
_files = 0
for _path in _package_py():
    _files += 1
    _tree = ast.parse(open(_path, encoding="utf-8").read(), _path)
    _rel = os.path.relpath(_path, _CODE_DIR)
    for _node in ast.walk(_tree):
        # Every reference form, not just the call: `out=print` is a print
        # reaching the terminal exactly as much as `print(...)` is.
        if isinstance(_node, ast.Name) and _node.id == "print":
            _print_sites.append(f"{_rel}:{_node.lineno}")
        if (isinstance(_node, ast.Attribute) and _node.attr == "print"
                and isinstance(_node.value, ast.Name)
                and _node.value.id == "builtins"):
            _patch_sites.append(f"{_rel}:{_node.lineno}")

check("7a     the scan saw the whole package (non-degeneracy)",
      _files >= 80, True)
check("7b     no `print` reference of any form survives in the package",
      _print_sites, [])
check("7c     no `builtins.print` reference survives (the monkey-patch)",
      _patch_sites, [])


#------------------------------------------------------------------------------


print()
print("=" * 70)
print("SECTION 8 -- stdout is EMPTY across a real pipeline run")
print("=" * 70)
#
# Captured at the FILE-DESCRIPTOR level in a subprocess, not by rebinding
# sys.stdout in this one. A Python-level redirect is invisible to a library
# holding sys.__stdout__, to a C extension, and to a subprocess -- all three of
# which are in this dependency graph. A subprocess with stdout on a pipe is the
# only capture that covers every writer.
#
# The run drives all six stages of the real graph. Qdrant, the cross-encoder and
# OpenAI are replaced through oncotriage/agent/deps.py, so nothing is billed and
# no network is touched; what is real is every line of stage code between them.

_DRIVER = r'''
import json, sys, types
from oncotriage.agent import deps, models
from oncotriage.agent.graph import build_matching_graph, match_patient_to_trials
from oncotriage.agent.display import display_match_results

class _Point:
    def __init__(self, nct):
        self.id = nct
        self.score = 0.9
        self.payload = {
            "nct_id": nct, "title": f"A trial {nct}", "phase": "Phase 2",
            "bm25_text": "breast cancer trial", "criteria_text": "inclusion",
            "conditions": ["Breast Neoplasms"], "min_age": 18, "max_age": 99,
            "sex": "ALL", "inclusion_criteria": ["adult"],
            "exclusion_criteria": [],
        }

class _Res:
    def __init__(self, pts): self.points = pts

class _Qdrant:
    def query_points(self, **kw): return _Res([_Point(f"NCT0000000{i}") for i in range(1, 6)])
    def scroll(self, **kw): return ([_Point(f"NCT0000000{i}") for i in range(1, 6)], None)
    def get_collection(self, *a, **k):
        return types.SimpleNamespace(points_count=12000)
    def get_collection_aliases(self, *a, **k):
        return types.SimpleNamespace(aliases=[])

class _Msg:
    content = json.dumps([{"nct_id": f"NCT0000000{i}", "eligible": "eligible",
                           "explanation": "ok", "criteria_details": []}
                          for i in range(1, 6)])
class _Choice:
    message = _Msg(); finish_reason = "stop"
class _Usage:
    prompt_tokens = 100; completion_tokens = 50; total_tokens = 150
    completion_tokens_details = None
class _Completion:
    choices = [_Choice()]; usage = _Usage(); model = "gpt-4o-2024-08-06"
class _OpenAI:
    class chat:
        class completions:
            @staticmethod
            def create(**kw): return _Completion()

deps.set_override(deps.QDRANT_CLIENT, _Qdrant())
deps.set_override(deps.OPENAI_CLIENT, _OpenAI())
deps.set_override(deps.MEDCPT_SCORER,
                  lambda q, texts: [1.0 - i * 0.01 for i in range(len(texts))])

# Every key the agent reads, derived by grepping the agent for
# patient_data[...] / patient_data.get(...) rather than guessed -- a missing
# key is a KeyError inside a node, which would make section 8 report a stdout
# result about a run that never happened.
patient = {
    "patient_id": "stdout-probe-1",
    "demographics": {"age": 63, "sex": "female", "birth_date": "1962-04-03",
                     "race": "White", "ethnicity": "Not Hispanic or Latino"},
    "conditions": [{"code": "254837009", "system": "http://snomed.info/sct",
                    "display": "Malignant neoplasm of breast",
                    "clinical_status": "active",
                    "verification_status": "confirmed", "onset": "2020-01-01"}],
    "observations": [], "medications": [], "procedures": [], "allergies": [],
    "cancer_stage_observations": [], "cancer_metastasis_observations": [],
    "cancer_genomic_variants": [],
    "ecog_performance_status": {"value": 1, "date": "2024-01-01",
                                "value_shape": "valueInteger",
                                "observation_count": 1,
                                "selection_path": "most_recent"},
}

graph = build_matching_graph()
result = match_patient_to_trials(patient, graph)
display_match_results(result)
sys.stderr.write("DRIVER-OK matches=%d\n" % len(result.get("matches") or []))
'''


def _run_driver(extra_env=None, driver=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = _CODE_DIR + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"
    env["ONCOTRIAGE_DEFER_LOCAL_MODELS"] = "1"
    env.update(extra_env or {})
    return subprocess.run([sys.executable, "-c", driver or _DRIVER],
                          capture_output=True, text=True, cwd=_CODE_DIR,
                          timeout=600, env=env)


_run = _run_driver()

if "DRIVER-OK" not in _run.stderr:
    fail("8a     the pipeline run completed",
         f"rc={_run.returncode}; stderr tail: {_run.stderr[-900:]!r}")
else:
    check("8a     the pipeline run completed", True, True)

check("8b     STDOUT IS EMPTY across a full six-stage run", _run.stdout, "")

_driver_recs = records(_run.stderr)
check("8c     the run emitted structured records on stderr",
      len(_driver_recs) >= 6, True)
check("8c     every one of them parses as JSON with the five envelope keys",
      all(RESERVED_KEYS - {"dropped_fields"} <= set(r) for r in _driver_recs),
      True)
check("8c     every one of them carries a correlation ID",
      all(r.get("correlation_id") for r in _driver_recs), True)

_stage_recs = [r for r in _driver_recs if r.get("patient_id") == "stdout-probe-1"]
check("8d     the patient's lines share ONE correlation ID",
      len({r["correlation_id"] for r in _stage_recs}), 1)
check("8d     ...and it is not the sentinel",
      NO_CORRELATION in {r["correlation_id"] for r in _stage_recs}, False)

check("8e     no stage put a clinical value in the record",
      any("Malignant neoplasm of breast" in json.dumps(r)
          for r in _driver_recs), False)
# ...and the console DID render the human report, which is the other half of
# "both channels stay". display.py is console UI by its own docstring.
check("8e     the human match report still rendered, on the console channel",
      "MATCH RESULTS FOR PATIENT stdout-probe-1" in _run.stderr, True)


#------------------------------------------------------------------------------


print()
print("=" * 70)
print("SECTION 9 -- THE PLANTED DEFECTS")
print("=" * 70)
#
# Three assertions in this file would be worth nothing if they had only ever
# passed. Each is broken here and required to FAIL. The plants go into a COPY
# of the module in a temp directory -- never the shipped file -- so there is no
# in-place edit to restore and no window in which the repository is wrong.

_SHIPPED = os.path.join(_CODE_DIR, "oncotriage", "observability.py")
_SHIPPED_SRC = open(_SHIPPED, encoding="utf-8").read()


def _with_plant(old, new):
    """A module object built from a patched COPY of observability.py."""
    if _SHIPPED_SRC.count(old) != 1:
        return None
    src = _SHIPPED_SRC.replace(old, new, 1)
    module = type(sys)("observability_plant")
    module.__dict__["__name__"] = "observability_plant"
    exec(compile(src, "<plant>", "exec"), module.__dict__)
    return module


# --- PLANT 1: ID isolation -- a module-level global instead of a ContextVar ---
#
# This is the defect the brief names: 12 worker threads, one slot. The plant
# replaces the ContextVar's get/set with a plain global, which is what a
# reasonable person would write first.
_plant1 = _with_plant(
    '_CORRELATION_ID = contextvars.ContextVar("oncotriage_correlation_id",\n'
    '                                         default=NO_CORRELATION)',
    'class _GlobalSlot:\n'
    '    """The defect: one slot, twelve threads."""\n'
    '    value = NO_CORRELATION\n'
    '    def get(self): return _GlobalSlot.value\n'
    '    def set(self, v):\n'
    '        prev = _GlobalSlot.value; _GlobalSlot.value = v; return prev\n'
    '    def reset(self, prev): _GlobalSlot.value = prev\n'
    '_CORRELATION_ID = _GlobalSlot()')

if _plant1 is None:
    fail("9a     PLANT 1 could not be applied",
         "the ContextVar construction was not found verbatim in "
         "observability.py; the plant needs re-targeting")
else:
    _plant_log = _plant1.get_logger("oncotriage.plant1")
    _pbarrier = threading.Barrier(MAX_WORKERS)

    def _plant_patient(patient_id):
        with _plant1.correlation_scope():
            with contextlib.suppress(threading.BrokenBarrierError):
                _pbarrier.wait(timeout=20)
            for _ in range(6):
                _plant_log.info("stage", patient_id=patient_id)
                time.sleep(0.001)

    _perr = io.StringIO()
    with contextlib.redirect_stderr(_perr):
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as _ex:
            list(_ex.map(_plant_patient, _PATIENTS[:MAX_WORKERS]))

    _precs = [r for r in records(_perr.getvalue()) if r.get("message") == "stage"]
    _pby_patient, _pby_cid = {}, {}
    for _r in _precs:
        _pby_patient.setdefault(_r.get("patient_id"), set()).add(
            _r.get("correlation_id"))
        _pby_cid.setdefault(_r.get("correlation_id"), set()).add(
            _r.get("patient_id"))
    _per_patient = sorted(len(v) for v in _pby_patient.values())
    _per_cid = sorted(len(v) for v in _pby_cid.values())

    # WHICH ASSERTION THE PLANT VIOLATES WAS MEASURED, NOT ASSUMED, and the
    # first version of this control asserted the wrong one. A single shared
    # global does not give one patient several IDs (3b) -- every thread sets
    # it, the last writer wins, and they all read that one value. It gives
    # MANY PATIENTS ONE ID, which is section 3c, "every correlation ID belongs
    # to exactly one patient". That is also the literal property the brief
    # asks for: no ID crosses between patients. Both directions are computed
    # and the plant must break at least one, so a future plant that fails the
    # other way is still caught.
    _broke_3b = _per_patient and max(_per_patient) > 1
    _broke_3c = _per_cid and max(_per_cid) > 1
    if not (_broke_3b or _broke_3c):
        fail("9a     PLANT 1 (global instead of ContextVar) IS CAUGHT",
             f"the planted global produced clean IDs anyway "
             f"(per-patient {_per_patient}, per-ID {_per_cid}), so section 3 "
             f"would pass against it and proves nothing")
    else:
        check("9a     PLANT 1 (global instead of ContextVar) IS CAUGHT",
              _broke_3b or _broke_3c, True)
        check("9a     ...specifically: an ID crossed between patients (3c)",
              _broke_3c, True)
        print(f"  [info] plant 1: IDs per patient {_per_patient}; "
              f"patients per ID {_per_cid}")

# --- PLANT 2: the allowlist stops filtering --------------------------------
_plant2 = _with_plant(
    "        if key in LOGGABLE_FIELDS:\n"
    "            kept[key] = fields[key]",
    "        if True:  # THE PLANT: the allowlist is not consulted\n"
    "            kept[key] = fields[key]")

if _plant2 is None:
    fail("9b     PLANT 2 could not be applied",
         "the allowlist branch was not found verbatim in observability.py")
else:
    _plant_log2 = _plant2.get_logger("oncotriage.plant2")
    _perr = io.StringIO()
    with contextlib.redirect_stderr(_perr):
        _plant_log2.info("stage complete", stage=4,
                         condition_display="Malignant neoplasm of breast",
                         lab_value=13.2)
    _pr = at(records(_perr.getvalue()), 0, {})
    # Section 4c asserts these are absent. Against the plant they are present.
    check("9b     PLANT 2 (allowlist not consulted) IS CAUGHT",
          ("condition_display" in _pr,
           "Malignant neoplasm of breast" in _perr.getvalue()),
          (True, True))

# --- PLANT 3: the empty-stdout assertion -----------------------------------
#
# Not a plant in observability.py: the assertion under test is section 8b, and
# what has to be shown is that it FAILS when something writes to stdout. The
# same driver is run with one line prepended that writes to fd 1 -- standing in
# for a reintroduced print, or a dependency's banner.
_plant3 = _run_driver(driver=(
    'import os\n'
    'os.write(1, b"PLANTED-STDOUT-WRITE\\n")\n' + _DRIVER))

if "DRIVER-OK" not in _plant3.stderr:
    fail("9c     PLANT 3 (a stdout write) IS CAUGHT",
         f"the planted driver did not complete, so the comparison is not "
         f"about stdout; stderr tail: {_plant3.stderr[-500:]!r}")
else:
    check("9c     PLANT 3 (a stdout write) IS CAUGHT",
          (_plant3.stdout != "", "PLANTED-STDOUT-WRITE" in _plant3.stdout),
          (True, True))
    check("9c     ...and the two runs differ in exactly that way",
          (_run.stdout, _plant3.stdout.strip()),
          ("", "PLANTED-STDOUT-WRITE"))

check("9d     the shipped observability.py was never edited",
      open(_SHIPPED, encoding="utf-8").read(), _SHIPPED_SRC)


#------------------------------------------------------------------------------


print()
print("=" * 70)
print(f"RESULT: {passed} passed, {failed} failed")
if _failures:
    print("Failed checks:")
    for _label in _failures:
        print(f"  - {_label}")
print("=" * 70)


if __name__ == "__main__":
    sys.exit(1 if failed else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Aug  7 2026

@author: ramyalsaffar
"""
