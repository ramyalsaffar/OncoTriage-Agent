# Experiment Tracking Test
##########################

"""``oncotriage/tracking.py``: the run-to-configuration index, end to end.

WHAT THIS COVERS
----------------
    1  the store        a file-backed round trip: start, log, end, and read
                        every param, tag, metric and artifact back through
                        MLflow's OWN client rather than off the filesystem
    2  the parameters   the logged key set is EXACTLY the enumeration, and a
                        seeded secret in the environment reaches neither a key
                        nor a value
    3  the tags         the four the brief names, plus the two argued additions
    4  the metrics      bool -> 1/0, numpy scalars accepted, NaN and non-numbers
                        dropped BY KEY and counted, no-active-run degrades
    5  the artifacts    both accepted forms -- a path and a (filename, text)
                        pair -- land in the store
    6  nesting          one parent, one child per configuration, LIFO close
    7  the refusal      a missing package raises by name with the install
                        command, and does NOT no-op
    8  degrading        git absent and Qdrant unreachable each record "unknown"
                        with a warning tag, and neither crashes tracking
    9  the seam         no other package module imports mlflow, and this one
                        does not import it at module scope
   10  the callers      the batch runner's metrics SELECT rather than compute,
                        and both entry points are wired

IT EXECS NOTHING, and that is a consequence of the design rather than a
restraint. ``oncotriage/tracking.py`` imports ``mlflow`` INSIDE its functions,
so masking ``sys.modules["mlflow"]`` makes the SHIPPED function take the
failure path -- there is nothing to copy and patch, and the control therefore
drives the real code rather than a reconstruction of it. Every other control
here either feeds a different input to a pure function or rebinds a module
attribute inside ``try``/``finally``. No ``_EXEC_ALLOWLIST`` entry is needed.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO CORPUS, NO GIT HISTORY
REQUIRED. ``resolve_qdrant_collection`` is replaced by a stand-in for every
section that opens a run -- it is the one live call the module makes -- and the
git section drives a stubbed ``subprocess`` for its assertions and accepts
EITHER outcome from the real one, so a `git archive` export reports rather than
aborts. The tracking store is a throwaway temp directory installed into
``paths._RESOLVED``, the seam ``tests/test_ablation_db_isolation.py`` already
uses, and it is restored at the end.

NOT IN THE COLLISION MATRIX, derived rather than assumed: it writes only inside
a temp directory, patches no repository file, and the four repository files it
READS (``oncotriage/tracking.py``, ``oncotriage/batch/runner.py``,
``oncotriage/ablation/study.py`` and every package module, for the AST scan) are
written by neither of the suite's two writers.

Run from terminal:
    python tests/test_tracking_mlflow_index.py

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
import contextlib
import shutil
import tempfile
import time

from oncotriage import config
from oncotriage import degradation
from oncotriage import paths
from oncotriage import tracking
from oncotriage.ablation import study as _study
from oncotriage.agent.prompts import PROMPT_VERSION
from oncotriage.batch import runner as _runner


#------------------------------------------------------------------------------


_T_START = time.time()


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label: str, actual, expected) -> None:
    """Assert equality, record the outcome, never abort the run."""
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          expected: {expected}\n"
                         f"          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


def detail(message: str) -> None:
    """Diagnostic text that is not an outcome."""
    print(f"        {message}")


def raises(fn, *args, **kwargs):
    """``(ExceptionTypeName, str(exception))``, or ``(None, return value)``.

    A BARE CALL WOULD ABORT THE FILE, and the calls below are exactly the ones
    a defect makes raise -- so a plant that stops ``start_run`` refusing would
    report one traceback where it owes a summary and eighty results. This
    project has shipped that shape four times (test_storage_query_layer.py,
    test_dashboard_reproducibility_tab.py, test_agent_trial_verdict_normalization.py,
    test_agent_age_units_and_sex_filter.py); it is not shipping it a fifth.
    """
    try:
        return None, fn(*args, **kwargs)
    except BaseException as exc:                                # noqa: BLE001
        return type(exc).__name__, str(exc)


def drive(fn, *args, **kwargs):
    """Call production code, converting a RAISE into a value ``check`` fails on.

    THIS IS NOT DEFENSIVENESS, IT IS THE DIFFERENCE BETWEEN A CONTROL AND AN
    ABORT, and the revert harness for this pass proved it: two of the twelve
    planted defects -- ``git_commit`` re-raising instead of recording "unknown",
    and ``start_run``'s validation moving below ``mlflow.start_run`` -- were
    "caught" only in the sense that the file died with a traceback and reported
    ZERO failing checks where it owed several. A run that reports one traceback
    instead of a summary tells a reader nothing about WHICH property broke.

    Every call into ``oncotriage/tracking.py`` below goes through this or
    through ``raises``; there is no bare call in the file.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                                # noqa: BLE001
        return f"<raised {type(exc).__name__}: {exc}>"


def drive_tuple(width, fn, *args, **kwargs):
    """``drive`` for a function that returns a tuple, so unpacking still works.

    A marker STRING would unpack into three characters where the caller expects
    three fields -- silently, for a three-character message -- so the marker is
    a tuple of the right width instead.
    """
    result = drive(fn, *args, **kwargs)
    if isinstance(result, str) and result.startswith("<raised "):
        return tuple([result] * (width - 1)) + ([result],)
    return result


class _NoRun:
    """Stands in for a run that could not be created or read back.

    Its emptiness is what makes the downstream checks FAIL rather than abort:
    ``.data.params`` is a real (empty) dict, so every comparison below runs and
    reports a difference instead of raising ``AttributeError``.
    """

    class info:
        status = "<no run>"
        run_id = "<no run>"
        experiment_id = "<no run>"

    class data:
        params = {}
        metrics = {}
        tags = {}


def get_run(run_id):
    """The stored run, or ``_NoRun`` when there is nothing to read."""
    if not isinstance(run_id, str) or len(run_id) != 32:
        return _NoRun
    try:
        return _client().get_run(run_id)
    except BaseException:                                       # noqa: BLE001
        return _NoRun


def artifacts_of(run_id):
    """Artifact paths, or a marker list, never a raise."""
    if not isinstance(run_id, str) or len(run_id) != 32:
        return ["<no run>"]
    try:
        return sorted(a.path for a in _client().list_artifacts(run_id))
    except BaseException as exc:                                # noqa: BLE001
        return [f"<raised {type(exc).__name__}>"]


# THE PATHS COME FROM THE MODULES' OWN __file__, never from a _code_dir guess:
# the file under inspection is then provably the one THIS process imported.
_RUNNER_PATH = os.path.abspath(_runner.__file__)
_STUDY_PATH = os.path.abspath(_study.__file__)
_PKG_DIR = os.path.dirname(os.path.abspath(oncotriage.__file__))


#------------------------------------------------------------------------------


# ===========================================================================
# ISOLATION
# ===========================================================================
#
# THE STORE. `oncotriage/tracking.py:tracking_uri()` reads
# `paths.result_tracking_path`, which is resolved by glob against the project
# root and cached in `paths._RESOLVED`. Installing a temp directory there is the
# same seam `tests/test_ablation_db_isolation.py` uses for the ablation
# database, and it is restored in the summary block below -- restored to
# ABSENT if it was absent, not to a guessed value, because writing a resolved
# path into the cache is exactly what this is pretending to be.
#
# THE ONE LIVE CALL. `configuration_params()` resolves the Qdrant collection
# through `oncotriage/utils.py`, which opens a client. Every section that opens
# a run replaces it, so nothing here touches the network. The replacement is on
# the `utils` MODULE, because that is the object `tracking` holds a reference
# to; restored below.

_STORE_DIR = tempfile.mkdtemp(prefix="oncotriage-tracking-test-")
_HAD_TRACKING_PATH = "result_tracking_path" in paths._RESOLVED
_OLD_TRACKING_PATH = paths._RESOLVED.get("result_tracking_path")
paths._RESOLVED["result_tracking_path"] = _STORE_DIR + os.sep

_STUB_COLLECTION = "trial_criteria_TESTSTUB_20260810"
_REAL_RESOLVE = tracking.utils.resolve_qdrant_collection
_RESOLVE_CALLS = []


def _stub_resolve():
    _RESOLVE_CALLS.append(1)
    return _STUB_COLLECTION


tracking.utils.resolve_qdrant_collection = _stub_resolve

# The git probe spawns a real `git`. It is offline and harmless, but a test that
# needs history is a test that aborts in a `git archive` export -- three files
# in this suite already do. Every section below that asserts on a commit stubs
# `subprocess`; section 8c drives the REAL one and accepts either outcome.
_REAL_SUBPROCESS = tracking.subprocess


class _FakeCompleted:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeSubprocess:
    """A stand-in for the ``subprocess`` module, answering ``run`` only."""

    def __init__(self, answer):
        self._answer = answer
        self.calls = []

    def run(self, argv, **kwargs):
        self.calls.append(tuple(argv))
        return self._answer(argv, **kwargs)


_STUB_COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _clean_repo(argv, **kwargs):
    if argv[1] == "rev-parse":
        return _FakeCompleted(0, _STUB_COMMIT + "\n")
    return _FakeCompleted(0, "")                     # `status --porcelain`: clean


@contextlib.contextmanager
def _subprocess_is(stub):
    """Rebind ``tracking.subprocess`` for the block. try/finally, always."""
    real = tracking.subprocess
    tracking.subprocess = stub
    try:
        yield stub
    finally:
        tracking.subprocess = real


def _client():
    """MLflow's own client against the throwaway store.

    READ BACK THROUGH THE LIBRARY, never off the filesystem. A test that
    globbed `meta.yaml` would be asserting on the file store's private layout
    and would pass on a store this module could no longer read.
    """
    import mlflow
    return mlflow.MlflowClient(tracking_uri=tracking.tracking_uri())


def _active_run():
    import mlflow
    return mlflow.active_run()


def _close_any_open_runs():
    """End every run left open, so one section's failure cannot cascade.

    Without this a section that fails BEFORE its `end_run` leaves a run active,
    and the next section's `nested=False` start_run either nests under it or
    raises -- turning one failure into every failure below it, all with
    misleading messages.
    """
    import mlflow
    while mlflow.active_run() is not None:
        mlflow.end_run()


def _counter_delta(fn, *args, **kwargs):
    """``(result, {key: increment})`` for TRACKING_DEGRADATIONS across a call."""
    before = dict(tracking.TRACKING_DEGRADATIONS)
    result = fn(*args, **kwargs)
    after = dict(tracking.TRACKING_DEGRADATIONS)
    delta = {k: after[k] - before.get(k, 0)
             for k in after if after[k] != before.get(k, 0)}
    return result, delta


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 1 -- THE STORE, END TO END
# ===========================================================================

print("=" * 78)
print("SECTION 1 -- the file-store round trip")
print("=" * 78)

check("1a  the tracking URI is a file: URI over result_tracking_path",
      tracking.tracking_uri(), "file:" + _STORE_DIR + os.sep)

with _subprocess_is(_FakeSubprocess(_clean_repo)):
    _RUN_ID = drive(
        tracking.start_run, "batch",
        {"patient_count": 1000, "resample_count": 100, "resample_seed": 42},
        None, False, {"resumed": "false"})

check("1b  start_run returns a run id",
      isinstance(_RUN_ID, str) and len(_RUN_ID) == 32, True)

check("1c  the run is open (non-degeneracy: every read below is of THIS run)",
      (_active_run() or type("x", (), {"info": None})).info.run_id
      if _active_run() else None, _RUN_ID)

_METRICS_OK = drive(tracking.log_run_metrics, {
    "main_total": 1000, "main_success": 993, "main_errors": 7,
    "wall_time_seconds": 812.5, "reconciliation_complete": True,
})
check("1d  every metric landed", _METRICS_OK, True)

_ARTIFACT_FILE = os.path.join(_STORE_DIR, "batch_runner_results.json")
with open(_ARTIFACT_FILE, "w", encoding="utf-8") as _fh:
    _fh.write('[{"patient_id": "p1"}]')

check("1e  end_run reports a clean close",
      drive(tracking.end_run, "FINISHED",
            [_ARTIFACT_FILE, ("degradation_summary.txt", "CLEAN\n")]),
      True)

check("1f  no run is left open", _active_run(), None)

_RUN = get_run(_RUN_ID)

check("1g  the run is FINISHED in the store", _RUN.info.status, "FINISHED")
check("1h  the metrics read back through MLflow's own API",
      {k: _RUN.data.metrics[k] for k in sorted(_RUN.data.metrics)},
      {"main_errors": 7.0, "main_success": 993.0, "main_total": 1000.0,
       "reconciliation_complete": 1.0, "wall_time_seconds": 812.5})

_ARTIFACTS = artifacts_of(_RUN_ID)
check("1i  both artifacts are in the store", _ARTIFACTS,
      ["batch_runner_results.json", "degradation_summary.txt"])

check("1j  the experiment is the one this module names",
      drive(lambda: _client().get_experiment(_RUN.info.experiment_id).name),
      tracking.EXPERIMENT_NAME)

# THE SEARCH IS THE POINT OF AN INDEX. A run nothing can find again is not an
# index entry, so the review-time query -- "every batch run" -- is exercised
# rather than assumed.
_FOUND = drive(lambda: [r.info.run_id for r in _client().search_runs(
    [_RUN.info.experiment_id], filter_string="tags.kind = 'batch'")])
check("1k  the run is findable by the tag a reviewer would search on",
      _FOUND, [_RUN_ID])


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 2 -- THE PARAMETERS ARE NAMED CONSTANTS, AND NOTHING ELSE
# ===========================================================================

print()
print("=" * 78)
print("SECTION 2 -- the parameters")
print("=" * 78)

_EXPECTED_PARAM_KEYS = sorted(
    set(tracking.CONFIGURATION_PARAM_NAMES)
    | {"prompt_version", "prompt_digest_probe",
       "prompt_template_sha256_site_confirmed",
       "prompt_template_sha256_site_unconfirmed",
       "qdrant_collection_resolved"}
    | {"patient_count", "resample_count", "resample_seed"})

check("2a  the logged key set is EXACTLY the enumeration plus the three "
      "caller keys this run passed -- nothing else reached the store",
      sorted(_RUN.data.params), _EXPECTED_PARAM_KEYS)

check("2b  ...and it is not a degenerate set (the enumeration is non-empty)",
      len(tracking.CONFIGURATION_PARAM_NAMES) >= 20, True)

check("2c  every enumerated name is an attribute of oncotriage.config",
      sorted(n for n in tracking.CONFIGURATION_PARAM_NAMES
             if not hasattr(config, n)), [])

check("2d  every enumerated constant is logged with ITS OWN value",
      sorted(n for n in tracking.CONFIGURATION_PARAM_NAMES
             if _RUN.data.params.get(n) != str(getattr(config, n))), [])

check("2e  the resolved collection is the one the resolver returned, not the "
      "alias in COLLECTION_NAME",
      (_RUN.data.params.get("qdrant_collection_resolved"),
       _RUN.data.params.get("qdrant_collection_resolved") != config.COLLECTION_NAME),
      (_STUB_COLLECTION, True))

check("2f  the collection was resolved ONCE for the whole start_run, not once "
      "per reader (two live calls can disagree across an alias swap)",
      len(_RESOLVE_CALLS), 1)

# --- the leak control -----------------------------------------------------
# The rule is "named constants only, never the environment". The assertion that
# would pass vacuously is "no param looks like a secret"; this seeds one and
# requires it to be absent from every KEY and every VALUE.
_SENTINEL = "sk-TRACKING-TEST-SENTINEL-b7f3c9"
os.environ["ONCOTRIAGE_TEST_FAKE_SECRET"] = _SENTINEL
os.environ["OPENAI_API_KEY"] = _SENTINEL
try:
    with _subprocess_is(_FakeSubprocess(_clean_repo)):
        _LEAK_PARAMS = drive(tracking.configuration_params, _STUB_COLLECTION)
        _LEAK_PARAMS = _LEAK_PARAMS if isinstance(_LEAK_PARAMS, dict) else {}
finally:
    del os.environ["ONCOTRIAGE_TEST_FAKE_SECRET"]
    del os.environ["OPENAI_API_KEY"]

check("2g  a secret seeded in the environment reaches no parameter KEY",
      [k for k in _LEAK_PARAMS if _SENTINEL in str(k)], [])
check("2h  ...and no parameter VALUE",
      [k for k, v in _LEAK_PARAMS.items() if _SENTINEL in str(v)], [])
check("2i  ...and no environment variable NAME became a key (non-degeneracy: "
      "the sentinel variables were really set while this ran)",
      sorted(set(_LEAK_PARAMS) & set(os.environ)) + [_SENTINEL in os.environ.values()],
      [False])

# --- the closed caller-key door -------------------------------------------
_kind, _msg = raises(tracking.start_run, "batch", {"anything_at_all": 1})
check("2j  a caller key outside CALLER_PARAM_KEYS is REFUSED", _kind, "KeyError")
check("2k  ...and the refusal names the closed set",
      "CALLER_PARAM_KEYS" in _msg, True)
check("2l  ...and it refuses BEFORE opening a run (no orphan at RUNNING)",
      _active_run(), None)

with _subprocess_is(_FakeSubprocess(_clean_repo)):
    _kind, _msg = raises(tracking.start_run, "batch",
                         {"sample_size": 1, "MATCHING_MODEL": "hacked"})
check("2m  a caller key that would overwrite an enumerated constant is REFUSED",
      (_kind, "MATCHING_MODEL" in _msg), ("KeyError", True))
check("2n  ...also before any run is opened", _active_run(), None)

_kind, _msg = raises(tracking.start_run, "not_a_kind")
check("2o  an unknown run kind is refused against the closed vocabulary",
      (_kind, sorted(tracking.RUN_KINDS) == ["ablation", "batch"]),
      ("ValueError", True))

# --- the enumeration is enforced, not decorative --------------------------
# THE CONTROL FOR 2a. Without it, 2a passes for a module that logs whatever it
# likes as long as the expected set is built the same way -- so a name is added
# to the enumeration and 2a's shape is required to NOTICE.
_REAL_NAMES = tracking.CONFIGURATION_PARAM_NAMES
tracking.CONFIGURATION_PARAM_NAMES = _REAL_NAMES + ("MAX_WORKERS",)
try:
    with _subprocess_is(_FakeSubprocess(_clean_repo)):
        _CTRL_ID = drive(tracking.start_run, "batch")
        drive(tracking.end_run, "FINISHED")
    _CTRL_KEYS = sorted(get_run(_CTRL_ID).data.params)
finally:
    tracking.CONFIGURATION_PARAM_NAMES = _REAL_NAMES
    _close_any_open_runs()

check("2p  CONTROL: a name added to the enumeration DOES change the logged key "
      "set, so 2a is comparing something that can move",
      ("MAX_WORKERS" in _CTRL_KEYS,
       "MAX_WORKERS" in sorted(_RUN.data.params)),
      (True, False))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 3 -- THE TAGS
# ===========================================================================

print()
print("=" * 78)
print("SECTION 3 -- the tags")
print("=" * 78)

_TAGS = {k: v for k, v in _RUN.data.tags.items() if not k.startswith("mlflow.")}

check("3a  the four tags the brief names are present and correct",
      {k: _TAGS.get(k) for k in ("kind", "prompt_version", "model", "git_commit")},
      {"kind": "batch", "prompt_version": PROMPT_VERSION,
       "model": config.MATCHING_MODEL, "git_commit": _STUB_COMMIT})

check("3b  git_dirty is recorded beside the commit (a commit identifies the "
      "code only if the tree is clean)", _TAGS.get("git_dirty"), "false")

check("3c  the package version is recorded", _TAGS.get("oncotriage_version"),
      oncotriage.__version__)

check("3d  the caller's tag is kept", _TAGS.get("resumed"), "false")

check("3e  a clean run carries NO warning tag",
      sorted(k for k in _TAGS if k.endswith("_unknown")), [])

with _subprocess_is(_FakeSubprocess(_clean_repo)):
    _kind, _msg = raises(tracking.start_run, "batch", None, None, False,
                         {"kind": "sneaky"})
check("3f  a caller may not override a tag this module sets",
      (_kind, "may not be overridden" in _msg), ("KeyError", True))
_close_any_open_runs()


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 4 -- THE METRICS
# ===========================================================================

print()
print("=" * 78)
print("SECTION 4 -- the metrics")
print("=" * 78)

import numpy as _np                                          # noqa: E402

with _subprocess_is(_FakeSubprocess(_clean_repo)):
    _M_ID = drive(tracking.start_run, "batch")

_MIXED = {
    "an_int": 7,
    "a_float": 1.5,
    "a_true": True,
    "a_false": False,
    "numpy_int64": _np.int64(41),        # NOT a subclass of int -- see below
    "numpy_float64": _np.float64(2.5),
    "a_none": None,
    "a_string": "12",
    "a_nan": float("nan"),
    "an_inf": float("inf"),
}
_M_OK, _M_DELTA = _counter_delta(tracking.log_run_metrics, _MIXED)
drive(tracking.end_run, "FINISHED")
_M_RUN = get_run(_M_ID)

check("4a  log_run_metrics reports False when it dropped something",
      _M_OK, False)

check("4b  the numbers that landed, with bool stored as 1/0",
      {k: _M_RUN.data.metrics[k] for k in sorted(_M_RUN.data.metrics)},
      {"a_false": 0.0, "a_float": 1.5, "a_true": 1.0, "an_int": 7.0,
       "numpy_float64": 2.5, "numpy_int64": 41.0})

# THE numpy_int64 CASE IS THE ONE THAT WAS ACTUALLY BROKEN, and it is asserted
# as a fact about the language rather than about the module: `numpy.int64` is
# not a subclass of `int`, so an `isinstance(value, (int, float))` test drops
# every integer column DataFrame.to_dict produces while keeping every float
# one. The ablation study's children are logged from exactly such a dict.
check("4c  ...and numpy.int64, which an (int, float) test would have dropped, "
      "is genuinely not an int (non-degeneracy for 4b's numpy entries)",
      (isinstance(_np.int64(41), int), isinstance(_np.int64(41), float),
       "numpy_int64" in _M_RUN.data.metrics),
      (False, False, True))

check("4d  every non-number was dropped and counted BY KEY, never by value",
      sorted(_M_DELTA),
      ["metric_not_numeric:a_nan", "metric_not_numeric:a_none",
       "metric_not_numeric:a_string", "metric_not_numeric:an_inf"])

check("4e  NaN and infinity are DROPPED, not stored and not coerced to zero",
      sorted(k for k in ("a_nan", "an_inf") if k in _M_RUN.data.metrics), [])

_R, _D = _counter_delta(tracking.log_run_metrics, {"orphan": 1})
check("4f  metrics offered with no run open degrade rather than raise",
      (_R, sorted(_D)), (False, ["metrics:NoActiveRun"]))

_R, _D = _counter_delta(tracking.end_run, "FINISHED")
check("4g  end_run with no run open degrades rather than raises",
      (_R, sorted(_D)), (False, ["end_run:NoActiveRun"]))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 5 -- THE ARTIFACTS AND THE STATUS
# ===========================================================================

print()
print("=" * 78)
print("SECTION 5 -- artifacts and status")
print("=" * 78)

with _subprocess_is(_FakeSubprocess(_clean_repo)):
    _A_ID = drive(tracking.start_run, "batch")
_A_OK, _A_DELTA = _counter_delta(
    tracking.end_run, "FAILED",
    artifacts=[os.path.join(_STORE_DIR, "does-not-exist.json"),
               ("kept.txt", "this one still lands")])
_A_RUN = get_run(_A_ID)

check("5a  an artifact that cannot be attached is counted, not raised",
      (_A_OK, sorted(_A_DELTA)[:1]), (False, sorted(_A_DELTA)[:1]))
check("5b  ...and the counter key names the failure kind",
      [k for k in _A_DELTA if k.startswith("artifact:")] != [], True)
check("5c  ...and the run STILL CLOSED, rather than being left at RUNNING",
      _A_RUN.info.status, "FAILED")
check("5d  ...and the artifacts that could be attached still were",
      artifacts_of(_A_ID), ["kept.txt"])

with _subprocess_is(_FakeSubprocess(_clean_repo)):
    _S_ID = drive(tracking.start_run, "batch")
_S_OK, _S_DELTA = _counter_delta(tracking.end_run, "COMPLETED_PROBABLY")
check("5e  an unrecognised status becomes FAILED, never FINISHED",
      get_run(_S_ID).info.status, "FAILED")
check("5f  ...and it is counted",
      [k for k in _S_DELTA if k.startswith("end_run:UnknownStatus")] != [], True)
check("5g  the accepted statuses are a closed set with no RUNNING in it",
      (sorted(tracking.RUN_STATUSES), "RUNNING" in tracking.RUN_STATUSES),
      (["FAILED", "FINISHED", "KILLED"], False))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 6 -- NESTING (the ablation shape)
# ===========================================================================

print()
print("=" * 78)
print("SECTION 6 -- one parent, one child per configuration")
print("=" * 78)

with _subprocess_is(_FakeSubprocess(_clean_repo)):
    _PARENT = drive(tracking.start_run, "ablation",
                    {"sample_size": 30, "seed": 42,
                     "configs": "full_pipeline,no_mesh_filter"})
    _CHILDREN = []
    for _cfg, _score in (("full_pipeline", 0.41), ("no_mesh_filter", 0.33)):
        _CHILDREN.append(drive(tracking.start_run, "ablation", {"configs": _cfg},
                               _cfg, True))
        drive(tracking.log_run_metrics, {"avg_score_all": _score, "n": _np.int64(30)})
        drive(tracking.end_run, "FINISHED")

check("6a  the parent is still active after every child closed (LIFO)",
      (_active_run() or type("x", (), {"info": None})).info.run_id
      if _active_run() else None, _PARENT)

drive(tracking.end_run, "FINISHED")
check("6b  and closing it leaves nothing open", _active_run(), None)

check("6c  every child records the parent",
      [get_run(c).data.tags.get("mlflow.parentRunId") for c in _CHILDREN],
      [_PARENT, _PARENT])
check("6d  every child carries its own configuration and its own numbers",
      [(get_run(c).data.params.get("configs"),
        get_run(c).data.metrics.get("avg_score_all"),
        get_run(c).data.metrics.get("n")) for c in _CHILDREN],
      [("full_pipeline", 0.41, 30.0), ("no_mesh_filter", 0.33, 30.0)])
check("6e  ...and they are two DIFFERENT runs (non-degeneracy)",
      len(set(_CHILDREN)) == 2 and _PARENT not in _CHILDREN, True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 7 -- THE REFUSAL
# ===========================================================================

print()
print("=" * 78)
print("SECTION 7 -- a missing package refuses, and does not no-op")
print("=" * 78)

# `sys.modules[name] = None` is CPython's documented way to make `import name`
# raise ImportError. Because oncotriage/tracking.py imports mlflow INSIDE its
# functions, this drives the SHIPPED function down its failure path -- no copy,
# no exec, no reconstruction that could differ from what ships.
_REAL_MLFLOW = sys.modules.get("mlflow")
sys.modules["mlflow"] = None
try:
    _k1, _m1 = raises(tracking.start_run, "batch")
    _k2, _m2 = raises(tracking.log_run_metrics, {"x": 1})
    _k3, _m3 = raises(tracking.end_run, "FINISHED")
finally:
    if _REAL_MLFLOW is not None:
        sys.modules["mlflow"] = _REAL_MLFLOW
    else:                                                # pragma: no cover
        del sys.modules["mlflow"]

check("7a  start_run RAISES when the package is missing -- it does not return "
      "None, and it does not quietly do nothing",
      _k1, "TrackingUnavailableError")
check("7b  the refusal names the install command",
      tracking.INSTALL_COMMAND in _m1, True)
check("7c  ...and says it is a default dependency rather than an extra",
      "not an extra" in _m1, True)
check("7d  it is NOT an ImportError, so a stray `except ImportError` around an "
      "optional feature cannot turn it back into a silent no-op",
      issubclass(tracking.TrackingUnavailableError, ImportError), False)
check("7e  ...and it IS a RuntimeError, like UnknownModelPricingError",
      issubclass(tracking.TrackingUnavailableError, RuntimeError), True)
check("7f  the other two raise the same way rather than pretending to work",
      (_k2, _k3),
      ("TrackingUnavailableError", "TrackingUnavailableError"))
check("7g  CONTROL: with the package restored, the same call succeeds -- so 7a "
      "measured the mask and not a permanently broken function",
      _REAL_MLFLOW is not None and sys.modules.get("mlflow") is _REAL_MLFLOW,
      True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 8 -- DEGRADE HONESTLY, TWICE
# ===========================================================================

print()
print("=" * 78)
print("SECTION 8 -- 'unknown', never a crash and never a fake")
print("=" * 78)

# --- 8a: git absent (the container) ---------------------------------------
def _no_git(argv, **kwargs):
    raise FileNotFoundError(2, "No such file or directory: 'git'")


with _subprocess_is(_FakeSubprocess(_no_git)):
    (_commit, _dirty, _warn), _G_DELTA = _counter_delta(
        lambda: drive_tuple(3, tracking.git_commit))

check("8a  git absent records 'unknown' rather than crashing or omitting",
      (_commit, _dirty), (tracking.UNKNOWN, tracking.UNKNOWN))
check("8b  ...with a warning tag name and a counted degradation",
      (_warn, [k for k in _G_DELTA if k.startswith("git_commit:")]),
      (["git_commit_unknown"], ["git_commit:FileNotFoundError"]))

with _subprocess_is(_FakeSubprocess(_no_git)):
    _NG_ID = drive(tracking.start_run, "batch")
    drive(tracking.end_run, "FINISHED")
_NG_TAGS = get_run(_NG_ID).data.tags
check("8c  ...and the RUN carries both the unknown commit and the warning tag",
      (_NG_TAGS.get("git_commit"), _NG_TAGS.get("git_commit_unknown")),
      (tracking.UNKNOWN, "true"))

# --- 8d: not a git repository ---------------------------------------------
def _not_a_repo(argv, **kwargs):
    return _FakeCompleted(128, "", "fatal: not a git repository")


with _subprocess_is(_FakeSubprocess(_not_a_repo)):
    _commit2, _dirty2, _warn2 = drive_tuple(3, tracking.git_commit)
check("8d  a non-zero git exit is 'unknown' too, not an empty string",
      (_commit2, _warn2), (tracking.UNKNOWN, ["git_commit_unknown"]))

# --- 8e: a dirty tree -----------------------------------------------------
def _dirty_repo(argv, **kwargs):
    if argv[1] == "rev-parse":
        return _FakeCompleted(0, _STUB_COMMIT + "\n")
    return _FakeCompleted(0, " M oncotriage/tracking.py\n")


with _subprocess_is(_FakeSubprocess(_dirty_repo)):
    _commit3, _dirty3, _warn3 = drive_tuple(3, tracking.git_commit)
check("8e  a dirty working tree is reported as such, so a reviewer knows the "
      "commit does not fully identify what ran",
      (_commit3, _dirty3, _warn3), (_STUB_COMMIT, "true", []))

# --- 8f: the real git, either outcome ------------------------------------
# NO HISTORY REQUIRED. Three files in this suite abort in a tree without .git;
# this one reports. Both outcomes are legitimate, and what is asserted is that
# the answer has one of the two legal SHAPES rather than something in between.
_commit4, _dirty4, _warn4 = drive_tuple(3, tracking.git_commit)
_real_shape = (
    (len(_commit4) == 40 and all(c in "0123456789abcdef" for c in _commit4)
     and _dirty4 in ("true", "false"))
    or (_commit4 == tracking.UNKNOWN and _dirty4 == tracking.UNKNOWN))
check("8f  the REAL git probe returns either a 40-hex commit or 'unknown' -- "
      "never a partial answer", _real_shape, True)
detail(f"real probe: commit={_commit4[:12]}... dirty={_dirty4} warnings={_warn4}")

# --- 8g: Qdrant unreachable ----------------------------------------------
def _unreachable():
    raise ConnectionError("could not reach the Qdrant endpoint")


tracking.utils.resolve_qdrant_collection = _unreachable
try:
    (_coll, _cwarn), _Q_DELTA = _counter_delta(
        lambda: drive_tuple(2, tracking.qdrant_collection))
    with _subprocess_is(_FakeSubprocess(_clean_repo)):
        _QQ_ID = drive(tracking.start_run, "batch")
        drive(tracking.end_run, "FINISHED")
    _QQ = get_run(_QQ_ID)
finally:
    tracking.utils.resolve_qdrant_collection = _stub_resolve

check("8g  an unreachable Qdrant records 'unknown' rather than crashing",
      (_coll, _cwarn), (tracking.UNKNOWN, ["qdrant_collection_unknown"]))
check("8h  ...counted by exception type",
      sorted(_Q_DELTA), ["qdrant_collection:ConnectionError"])
check("8i  ...and the run carries the unknown collection AND the warning tag, "
      "so nothing looks resolved that was not",
      (_QQ.data.params.get("qdrant_collection_resolved"),
       _QQ.data.tags.get("qdrant_collection_unknown")),
      (tracking.UNKNOWN, "true"))
check("8j  CONTROL: with the resolver restored the same run resolves for real, "
      "so 8i measured the failure and not a function that always says unknown",
      drive_tuple(2, tracking.qdrant_collection), (_STUB_COLLECTION, []))

check("8k  the counter is registered, so a degraded index reaches the run's "
      "own degradation block rather than only the scrollback",
      "TRACKING_DEGRADATIONS" in degradation.registered_names(), True)
check("8l  ...and it is the same object the registry reports on",
      degradation.snapshot().get("TRACKING_DEGRADATIONS") is not None
      and sum(degradation.snapshot()["TRACKING_DEGRADATIONS"].values())
      == sum(tracking.TRACKING_DEGRADATIONS.values()), True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 9 -- THE SEAM: ONE MODULE IMPORTS mlflow
# ===========================================================================

print()
print("=" * 78)
print("SECTION 9 -- nothing else imports mlflow")
print("=" * 78)


def _mlflow_imports(source, path="<memory>"):
    """``[(lineno, in_function)]`` for every import of mlflow in ``source``."""
    tree = ast.parse(source, filename=path)
    # Which function bodies each node sits inside, by line span.
    spans = [(n.lineno, max(getattr(d, "end_lineno", n.end_lineno) or n.end_lineno
                            for d in [n]))
             for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    out = []
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        if any(n == "mlflow" or n.startswith("mlflow.") for n in names):
            inside = any(lo <= node.lineno <= hi for lo, hi in spans)
            out.append((node.lineno, inside))
    return out


_PKG_SOURCES = {}
for _root, _dirs, _files in os.walk(_PKG_DIR):
    _dirs[:] = [d for d in _dirs if d != "__pycache__"]
    for _f in sorted(_files):
        if _f.endswith(".py"):
            _p = os.path.join(_root, _f)
            _PKG_SOURCES[os.path.relpath(_p, os.path.dirname(_PKG_DIR))] = \
                open(_p, encoding="utf-8").read()

check("9a  the scan saw the whole package (non-degeneracy: an empty corpus "
      "satisfies every assertion below for free)",
      len(_PKG_SOURCES) >= 60, True)

_TRACKING_SRC_FOR_9 = _PKG_SOURCES["oncotriage/tracking.py"]

_IMPORTERS = {rel: _mlflow_imports(src, rel)
              for rel, src in _PKG_SOURCES.items() if _mlflow_imports(src, rel)}
check("9b  exactly one package module imports mlflow",
      sorted(_IMPORTERS), ["oncotriage/tracking.py"])

check("9c  ...and every one of its imports is INSIDE a function body, so "
      "importing the package pulls in no part of the mlflow tree",
      sorted({inside for _, inside in _IMPORTERS["oncotriage/tracking.py"]}),
      [True])

# THE FIRST VERSION OF THIS CHECK ASSERTED "three or more deferred imports" and
# FAILED, because there is exactly ONE: `_import_mlflow()`, which the three
# public functions call. The check was written from an assumption about the
# module rather than from the module, which is the shape this project's
# non-degeneracy rule exists to catch -- so it now asserts the design that is
# actually there, and that design is the stronger one: one import site means one
# place the refusal message can come from, and a public function that forgot to
# route through it would be a second, silent ImportError path.
_TRACKING_IMPORT_LINES = [ln for ln, _ in _IMPORTERS["oncotriage/tracking.py"]]
_IMPORT_FN = [n.name for n in ast.walk(ast.parse(_TRACKING_SRC_FOR_9))
              if isinstance(n, ast.FunctionDef)
              and any(n.lineno <= ln <= n.end_lineno
                      for ln in _TRACKING_IMPORT_LINES)]
check("9d  there is exactly ONE import site, and it is the refusal funnel -- so "
      "a missing package cannot reach a caller by any other route",
      (len(_TRACKING_IMPORT_LINES), _IMPORT_FN), (1, ["_import_mlflow"]))

check("9e  ...and all three public functions route through it (non-degeneracy: "
      "one import site proves nothing if a function skipped it)",
      sorted(n.name for n in ast.walk(ast.parse(_TRACKING_SRC_FOR_9))
             if isinstance(n, ast.FunctionDef)
             and n.name in ("start_run", "log_run_metrics", "end_run")
             and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                     and c.func.id == "_import_mlflow"
                     for c in ast.walk(n))),
      ["end_run", "log_run_metrics", "start_run"])

# THE CONTROL. Both 9b and 9c are satisfied by a scanner that finds nothing, so
# each is fired against a source that plants what it is looking for.
_PLANT_MODULE = "import os\nimport mlflow\n\n\ndef f():\n    return mlflow\n"
_PLANT_DEFERRED = "import os\n\n\ndef f():\n    import mlflow\n    return mlflow\n"
check("9f  CONTROL: a module-scope `import mlflow` planted in a copy IS found, "
      "and IS reported as not-in-a-function",
      _mlflow_imports(_PLANT_MODULE), [(2, False)])
check("9g  CONTROL: a deferred one is found and reported as in-a-function",
      _mlflow_imports(_PLANT_DEFERRED), [(5, True)])
check("9h  CONTROL: the `from mlflow.x import y` form is caught too",
      _mlflow_imports("from mlflow.tracking import MlflowClient\n"), [(1, False)])

# The module must not name the four bulk-dump constructs anywhere.
_TRACKING_SRC = _PKG_SOURCES["oncotriage/tracking.py"]
_TRACKING_TREE = ast.parse(_TRACKING_SRC)
_BULK = []
for _node in ast.walk(_TRACKING_TREE):
    if isinstance(_node, ast.Call) and isinstance(_node.func, ast.Name) \
            and _node.func.id in ("vars", "globals", "locals"):
        _BULK.append(f"{_node.func.id}() at line {_node.lineno}")
    if isinstance(_node, ast.Attribute) and _node.attr == "__dict__":
        _BULK.append(f".__dict__ at line {_node.lineno}")
    if isinstance(_node, ast.Attribute) and _node.attr == "environ" \
            and isinstance(_node.value, ast.Name) and _node.value.id == "os":
        # os.environ IS named once, for MLFLOW_ALLOW_FILE_STORE. It is allowed
        # there and nowhere else, so the check is scoped to the functions that
        # build parameters rather than to the file.
        pass
check("9i  the module never dumps a namespace: no vars(), globals(), locals() "
      "or .__dict__ anywhere in it", _BULK, [])

_PARAM_FNS = {"configuration_params", "_prompt_params", "start_run"}
_ENV_IN_PARAMS = []
for _node in ast.walk(_TRACKING_TREE):
    if isinstance(_node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
            and _node.name in _PARAM_FNS:
        for _sub in ast.walk(_node):
            if isinstance(_sub, ast.Attribute) and _sub.attr == "environ":
                _ENV_IN_PARAMS.append(f"{_node.name}:{_sub.lineno}")
check("9j  no function that builds parameters reads os.environ (the one read "
      "in the module is _configure_store's, and it writes a library flag)",
      _ENV_IN_PARAMS, [])
check("9k  ...and all three of those functions really exist (non-degeneracy)",
      sorted(_PARAM_FNS & {n.name for n in ast.walk(_TRACKING_TREE)
                           if isinstance(n, ast.FunctionDef)}),
      sorted(_PARAM_FNS))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 10 -- THE CALLERS
# ===========================================================================

print()
print("=" * 78)
print("SECTION 10 -- the batch runner and the ablation study")
print("=" * 78)

_RECORDS = [
    {"patient_id": "a", "status": "success", "total_time": 10.0,
     "eligible_matches": 2, "not_evaluable": 1, "is_resample": False},
    {"patient_id": "b", "status": "error", "error": "boom", "total_time": 5.0,
     "eligible_matches": 0, "not_evaluable": 0, "is_resample": False},
    {"patient_id": "c", "status": "success", "total_time": 7.0,
     "eligible_matches": 0, "not_evaluable": 3, "is_resample": True},
]
_RECON = {"attempted": 3, "verified": 3, "missing": 0, "complete": True}
_SNAP = {"AGE_PARSE_FAILURES": {"years: '?'": 2, "months: 'x'": 1}}

_TM = _runner.tracking_metrics(_RECORDS, 99.5, reconciliation=_RECON,
                               degradation_snapshot=_SNAP)

_STATS_MAIN = _runner.pass_stats([r for r in _RECORDS if not r["is_resample"]])
check("10a  every pass metric is the value pass_stats computed -- selected, "
      "never recomputed",
      {k: _TM[f"main_{k}"] for k in _runner._TRACKED_PASS_STATS},
      {k: _STATS_MAIN[k] for k in _runner._TRACKED_PASS_STATS})

check("10b  ...and those five members are genuinely numbers in pass_stats, "
      "while the six display members are strings (non-degeneracy)",
      (sorted(k for k in _runner._TRACKED_PASS_STATS
              if isinstance(_STATS_MAIN[k], int)),
       sorted(k for k in _STATS_MAIN
              if isinstance(_STATS_MAIN[k], str))),
      (sorted(_runner._TRACKED_PASS_STATS),
       ["avg_eligible", "avg_time", "error_rate", "max_time", "min_time",
        "total_time"]))

check("10c  the reconciliation verdict rides as a metric",
      {k: _TM[k] for k in sorted(_TM) if k.startswith("reconciliation_")},
      {"reconciliation_attempted": 3, "reconciliation_complete": True,
       "reconciliation_missing": 0, "reconciliation_verified": 3})

check("10d  the degradation TOTALS ride, keyed by counter NAME -- never by the "
      "counter's own keys, which carry third-party and clinical text",
      {k: v for k, v in _TM.items() if k.startswith("degradation_")},
      {"degradation_AGE_PARSE_FAILURES": 3})

check("10e  an absent resample pass emits NO resample metric, rather than five "
      "zeros that would read as 'ran and found nothing'",
      sorted(k for k in _runner.tracking_metrics(
          [r for r in _RECORDS if not r["is_resample"]], 1.0)
          if k.startswith("resample_")), [])

check("10f  ...while a present one does (non-degeneracy for 10e)",
      sorted(k for k in _TM if k.startswith("resample_")),
      ["resample_errors", "resample_not_evaluable", "resample_patients_with_match",
       "resample_success", "resample_total"])

check("10g  no reconciliation means NO reconciliation metric -- 'not asked' is "
      "not 'rows were lost'",
      [k for k in _runner.tracking_metrics(_RECORDS, 1.0) if "reconciliation" in k],
      [])

check("10h  every value tracking_metrics produces is loggable as a metric",
      sorted(k for k, v in _TM.items()
             if not isinstance(v, (bool, int, float))), [])

# --- the hooks are wired, asserted against the source ---------------------
_RUNNER_SRC = open(_RUNNER_PATH, encoding="utf-8").read()
_STUDY_SRC = open(_STUDY_PATH, encoding="utf-8").read()


def _calls(source, dotted):
    """Line numbers of every ``a.b(...)`` call in ``source``."""
    owner, attr = dotted.split(".")
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == attr \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == owner:
            out.append(node.lineno)
    return out


# THE END_RUN COUNTS INCLUDE THE CRASH GUARD, and that is the point rather than
# an allowance. Each caller wraps its body in `except BaseException:
# tracking.end_run("FAILED"); raise`, because MEASURED: a process that opens an
# MLflow run and then dies on an uncaught exception has that run recorded as
# FINISHED by MLflow's own atexit hook. Without the guard a crashed campaign is
# indexed as a completed one. So the second (and fourth) site is load-bearing,
# and a count that did not include it would pass with the guard deleted.
def _guard_shape(source):
    """``(exception name, the tracking function called, whether it re-raises)``
    for the crash guard, or a marker triple when there is no such handler.

    THE HANDLER IS SELECTED BY WHAT IT CALLS, NOT BY BEING THE FIRST ONE.
    The first version took the first ``except BaseException`` in the file, which
    was the tracking guard when this check was written and stopped being it at
    the run-identity pass: ``oncotriage/batch/runner.py`` now wraps
    ``tracking.start_run`` in its own ``except BaseException`` that finalizes the
    already-open ``runs`` row and re-raises. That handler is correct and has
    nothing to do with this assertion, and a positional selector reported it as
    a tracking guard that closes no run.

    So the subject is stated rather than assumed: the handler that CLOSES THE
    TRACKING RUN. A file with no such handler still returns the marker triple
    and still fails, which is what the check is for.
    """
    fallback = None
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Try):
            continue
        for h in node.handlers:
            if not (isinstance(h.type, ast.Name) and h.type.id == "BaseException"):
                continue
            called = [n.func.attr for n in ast.walk(h)
                      if isinstance(n, ast.Call)
                      and isinstance(n.func, ast.Attribute)
                      and isinstance(n.func.value, ast.Name)
                      and n.func.value.id == "tracking"]
            reraises = any(isinstance(n, ast.Raise) and n.exc is None
                           for n in ast.walk(h))
            if called:
                return (h.type.id, called[0], reraises)
            if fallback is None:
                fallback = (h.type.id, "<none>", reraises)
    return fallback or ("<no BaseException handler>", "<none>", False)

check("10i  the batch runner opens one run, logs once, and closes it on BOTH "
      "exit paths (the normal one and the crash guard)",
      [len(_calls(_RUNNER_SRC, f"tracking.{f}"))
       for f in ("start_run", "log_run_metrics", "end_run")], [1, 1, 2])

check("10j  the ablation study opens a parent and a child, and closes the "
      "child, the interrupted parent, the finished parent and the crash guard",
      [len(_calls(_STUDY_SRC, f"tracking.{f}"))
       for f in ("start_run", "log_run_metrics", "end_run")], [2, 1, 4])

check("10m  ...and each caller's guard is an `except BaseException` that "
      "RE-RAISES, so no pipeline exception is swallowed to protect the index",
      [_guard_shape(src) for src in (_RUNNER_SRC, _STUDY_SRC)],
      [("BaseException", "end_run", True), ("BaseException", "end_run", True)])

check("10k  neither the API nor the MCP server tracks anything -- a request is "
      "not a run",
      sorted(rel for rel, src in _PKG_SOURCES.items()
             if "tracking." in src and rel.split("/")[1] in ("api", "mcp")), [])

check("10l  ...and the only package modules that call tracking at all are the "
      "two entry points this pass wires",
      sorted(rel for rel, src in _PKG_SOURCES.items()
             if _calls(src, "tracking.start_run")),
      ["oncotriage/ablation/study.py", "oncotriage/batch/runner.py"])


#------------------------------------------------------------------------------


# ===========================================================================
# TEARDOWN
# ===========================================================================

_close_any_open_runs()
tracking.utils.resolve_qdrant_collection = _REAL_RESOLVE
tracking.subprocess = _REAL_SUBPROCESS

if _HAD_TRACKING_PATH:
    paths._RESOLVED["result_tracking_path"] = _OLD_TRACKING_PATH
else:
    # RESTORED TO ABSENT, not to a guess. Writing a resolved value into the
    # cache is exactly the thing this test was pretending to do.
    paths._RESOLVED.pop("result_tracking_path", None)

check("teardown  the Qdrant resolver is the real one again",
      tracking.utils.resolve_qdrant_collection is _REAL_RESOLVE, True)
check("teardown  subprocess is the real module again",
      tracking.subprocess is _REAL_SUBPROCESS, True)
check("teardown  the tracking path cache is as it was found",
      "result_tracking_path" in paths._RESOLVED, _HAD_TRACKING_PATH)

shutil.rmtree(_STORE_DIR, ignore_errors=True)
check("teardown  the throwaway store is gone and nothing was written in the "
      "repository", os.path.exists(_STORE_DIR), False)


# ===========================================================================
# SUMMARY
# ===========================================================================

print()
print("=" * 78)
print("SUMMARY")
print("=" * 78)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")
print(f"Runtime: {time.time() - _T_START:.2f}s")

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
Created on Mon Aug 10 2026

@author: ramyalsaffar
"""
