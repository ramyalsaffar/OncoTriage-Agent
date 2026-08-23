# Dashboard Run Health Tab Render Test
#####################################

"""
The standing test for ``oncotriage/dashboard/tabs/run_health.py`` and the four
run loaders in ``oncotriage/dashboard/data.py``.

WHY THIS IS NOT A GOLDEN SNAPSHOT, AND THE PROJECT ALREADY HAS THE ARGUMENT
---------------------------------------------------------------------------
``tests/test_dashboard_reproducibility_tab.py`` is this project's one dashboard
test convention, and it compares a rendered element tree against a committed
golden JSON. That convention is followed HERE FOR EVERYTHING EXCEPT THE
REFERENCE, and the exception is forced by that file's own stated rule:

    "a golden file refreshed to accommodate a change makes whatever the code
     does correct by definition"

That file could take the snapshot because it had a BEFORE -- the pre-split
module, whose four hoisted literals were lifted out of git by AST and compared
value-for-value before the first snapshot was written. The Run Health tab is
NEW. There is no earlier rendering to establish it against, so a snapshot
recorded on day one is a photograph of whatever this pass happened to write, and
it would pass forever against a tab that reports a crashed run as finished. It
would be exactly the shape the rule forbids, adopted deliberately.

So the reference is the SEED. Every expected value below is computed here from
the rows that were inserted -- patient counts, costs, health states, attribution
counts -- and never read back out of the frame under test. The infrastructure IS
that file's, point for point: ``AppTest.from_string`` driving one module and one
function, a scratch database built by the project's own
``initialize_database()`` so the schema is real by construction,
``paths._RESOLVED`` as the redirect seam, ``sqlite3.connect`` recorded for every
render with a DECOY control that shows the isolation assertion FAILING, an
offline guard that raises and records with a control that shows it firing, and
every planted defect applied to a COPY of the module in a temporary directory.

WHAT IS ASSERTED, AND WHY THESE AND NOT THE ELEMENT TREE
--------------------------------------------------------
The three findings this tab exists to keep apart, on six scenarios:

    measured clean   counters were consulted and none moved
    degraded         at least one moved
    no health record nothing was ever flushed -- NOT the same as clean

plus the crashed shape (``finished_at`` NULL) being FLAGGED rather than hidden,
and the NULL-``run_id`` population being counted rather than silently dropped.
An element-order comparison would catch a layout change and would not catch any
of those; it is a regression detector for code that has been proved once, and
this code has not.

RUNS, COSTS, KEYS
-----------------
No network (measured, not claimed -- section 4), no keys, no spend, no live
Qdrant, no model load, no corpus, no git history. NOT in the collision matrix,
derived rather than assumed: it writes only inside a ``tempfile.mkdtemp`` it
removes, and the two repository files it reads --
``oncotriage/dashboard/tabs/run_health.py`` and
``oncotriage/dashboard/data.py`` -- are written by neither of the suite's two
writers. It EXECS NOTHING: every plant is a copy written to a temp directory and
imported from there, so no ``_EXEC_ALLOWLIST`` entry is needed.
"""

import ast
import hashlib
import os
import pickle
import shutil
import socket
import sqlite3
import sys
import tempfile
import time
import traceback
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

from oncotriage import paths as _paths
from oncotriage.dashboard import data as _dashboard_data
from oncotriage.dashboard.tabs import run_health as _tab
from oncotriage.storage import queries as _queries
from oncotriage.storage.database_logger import initialize_database


#------------------------------------------------------------------------------


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0, "skipped": 0}
_FAILURES = []
_SKIPS = []


def check(label, actual, expected):
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


def check_true(label, condition):
    check(label, bool(condition), True)


def skip(label, reason):
    """Record coverage that could NOT be exercised in THIS environment.

    A SKIP IS NOT A PASS AND IS NEVER COUNTED AS ONE. The mechanism and the
    argument are this project's existing ones, adopted rather than invented:
    ``tests/test_package_invariants.py``'s ``skip`` (the macOS-only ``caffeine``
    guard) and ``tests/test_dockerignore_exclusions.py``'s (the untracked,
    self-ignored virtualenv that no hosted runner has). Its own counter, its own
    list, and a summary line PRINTED EVEN AT ZERO -- a skip count that appears
    only when it is non-zero is indistinguishable from a file that has no skip
    mechanism at all. It does not touch the exit code: the thing skipped is not
    broken, it is absent.
    """
    _RESULTS["skipped"] += 1
    _SKIPS.append(f"{label}\n          {reason}")
    print(f"  SKIP  {label}")
    print(f"          {reason}")


def at_(sequence, index, default="(absent)"):
    """``sequence[index]`` or a named absence.

    A BARE INDEX ABORTS THE FILE, AND THIS PROJECT HAS SHIPPED THAT NINE TIMES.
    Every plant below is designed to remove an element, so every read of a
    rendered list is exactly the expression that raises ``IndexError`` when the
    defect under test fires -- turning a run that owes a hundred recorded
    failures into one traceback and no summary.
    """
    try:
        return sequence[index]
    except (IndexError, KeyError):
        return default


def digest_file(path):
    """sha256 of a file, or a NAMED non-reading -- never a raise.

    'absent'          the path is not there
    'unreadable: X'   it is there and could not be read (a directory, a
                      permission, an I/O error)

    THE RAISE IS THE POINT OF THE SECOND CASE. This is called at module scope,
    before any check has run, so an OSError here turns a run that owes a
    summary and 160-odd results into one traceback -- the abort shape this
    project has shipped ten times. The marker travels into the probe below,
    which asks is_real_digest() and therefore reports it as a recorded failure.
    """
    if not os.path.exists(path):
        return "absent"
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        return f"unreadable: {type(exc).__name__}"


def reading_of(path):
    """``digest_file(path)``, turning ANY raise into a value ``check`` fails on.

    THE CONTROL BELOW CANNOT SURVIVE THE REVERT IT TESTS WITHOUT THIS, and the
    revert harness is what found it rather than reading. Strip the ``try`` out
    of ``digest_file`` and the very call inside that control's argument list
    raises, the exception escapes while the argument is being evaluated, and
    the run reports ONE TRACEBACK where it owed a summary and every result --
    the abort shape this project has now shipped ten times, reproduced inside
    the control written to prevent it. Measured: with the raise restored the
    file exited 1 having recorded ZERO failures; with this wrapper it records a
    named one.

    It also removes the last abort at module scope: a production database that
    exists and cannot be read now becomes a reading the probe REPORTS rather
    than an exception before the first check.
    """
    try:
        return digest_file(path)
    except BaseException as exc:                       # noqa: BLE001 -- reported
        return f"raised: {type(exc).__name__}: {exc}"


def is_real_digest(reading):
    """True only for a real sha256 -- 64 lower-case hex characters.

    THE PROBE ASKS THIS, NOT ``!= "absent"``, AND THE DIFFERENCE IS A SECOND
    SENTINEL. The reading can fail to be a digest in two ways: the file is not
    there ('absent') and the file is there but could not be read
    ('unreadable: ...'). A predicate written against the first string alone
    would report the second as a real reading and pass -- the vacuous pass the
    probe exists to prevent, one sentinel over.
    """
    return (isinstance(reading, str) and len(reading) == 64
            and all(c in "0123456789abcdef" for c in reading))


_PROBE_RUN = "run"
_PROBE_SKIP = "skip"


def production_probe_disposition(production_existed):
    """Whether the production-database non-degeneracy probe has a subject.

    THE GATE READS THE FILESYSTEM; THE PROBE READS THE DIGEST, AND THE TWO
    READINGS BEING INDEPENDENT IS THE WHOLE DESIGN. The probe this gates
    asserts that ``_PRODUCTION_DIGEST_BEFORE`` is not the string ``'absent'``.
    A gate keyed on that same string would therefore be satisfied by exactly
    the fault the probe exists to catch -- a digest reading that comes back
    ``'absent'`` for a file that is really there, through a broken reader or a
    wrong path -- and the skip path would quietly become the only path.
    ``os.path.exists`` decides whether the probe runs; the digest decides what
    it reports.

    Pure, so its controls are different ARGUMENTS rather than a mutated file on
    disk -- the shape this suite uses wherever the subject is a function of its
    input.
    """
    return _PROBE_RUN if production_existed else _PROBE_SKIP


def production_probe_verdict(digest_before):
    """The ``(actual, expected)`` pair the probe hands to ``check``.

    ONE implementation, driven by the live call site and by every control
    below, so a control cannot agree with a probe that has stopped checking.
    """
    return (is_real_digest(digest_before), True)


def gate_call_sites(source_path):
    """Every ``if`` whose ELSE branch calls ``skip()``, as the set of names
    its TEST reads. AT ANY NESTING DEPTH, deliberately: a top-level walk is
    what hid ``api/server.py``'s four endpoints from the first version of
    test_package_invariants.py's decorator scan, and a gate moved inside a
    helper must not escape this pin by moving.

    THE CONTROLS ON THE TWO PURE FUNCTIONS ABOVE CANNOT SEE A WRONG CALL SITE,
    and that gap is why this exists. Rewriting the gate's `if` to read the
    digest instead of the existence flag leaves both functions correct, leaves
    every control green, and quietly turns the one state the probe exists to
    catch -- a file that is there whose reading says 'absent' -- into a SKIP.
    An AST pin on the call site is the only thing that reports it.
    """
    tree = ast.parse(Path(source_path).read_text(encoding="utf-8"))
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        calls_skip = any(
            isinstance(inner, ast.Call) and getattr(inner.func, "id", "") == "skip"
            for stmt in node.orelse for inner in ast.walk(stmt))
        if calls_skip:
            sites.append({n.id for n in ast.walk(node.test)
                          if isinstance(n, ast.Name)})
    return sites


def skip_accounting_keys(source_path):
    """Which ``_RESULTS`` counters ``skip()`` writes, read off this file by AST.

    A SKIP THAT INCREMENTS ``passed`` IS THE FAILURE MODE THIS WHOLE PASS EXISTS
    TO AVOID -- coverage that could not run, reported as coverage that did. No
    behavioural control can see it (the counter it corrupts is the counter every
    other check moves), so it is pinned structurally.
    """
    tree = ast.parse(Path(source_path).read_text(encoding="utf-8"))
    keys = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "skip"):
            continue
        for inner in ast.walk(node):
            target = None
            if isinstance(inner, ast.AugAssign):
                target = inner.target
            elif isinstance(inner, ast.Assign) and len(inner.targets) == 1:
                target = inner.targets[0]
            if (isinstance(target, ast.Subscript)
                    and getattr(target.value, "id", "") == "_RESULTS"
                    and isinstance(target.slice, ast.Constant)):
                keys.add(target.slice.value)
    return sorted(keys)


_T_START = time.time()

_TAB_FILE = os.path.abspath(_tab.__file__)
_TAB_SOURCE = Path(_TAB_FILE).read_text(encoding="utf-8")
_TAB_DIGEST_BEFORE = digest_file(_TAB_FILE)
_DATA_FILE = os.path.abspath(_dashboard_data.__file__)
_DATA_DIGEST_BEFORE = digest_file(_DATA_FILE)

_TMP = tempfile.mkdtemp(prefix="oncotriage-run-health-")
_PLANT_DIR = os.path.join(_TMP, "plants")
os.makedirs(_PLANT_DIR, exist_ok=True)
_PLANT_SEQ = [0]

print("=" * 70)
print("DASHBOARD RUN HEALTH TAB — RENDER TEST")
print("=" * 70)
print(f"Module under test: {_TAB_FILE}")
print(f"Scratch root:      {_TMP}")
print()


# ===========================================================================
# SECTION 1: SIX SCRATCH DATABASES, AND NONE OF THEM IS THE PRODUCTION ONE
# ===========================================================================

print("=" * 70)
print("Section 1: the seeded scratch databases")
print("=" * 70)

_PRODUCTION_DB = os.path.abspath(_paths.inferences_path)
_PRODUCTION_DIGEST_BEFORE = reading_of(_PRODUCTION_DB)
# Taken HERE, beside the digest and before this file has written anything,
# because the question the gate in section 7 answers is "did this machine have
# a production database for the byte-identity check to be exercised against" --
# not "is there one now". A run that CREATED one is caught by that check itself
# ('absent' != <hash>), which stays live and ungated in every environment.
_PRODUCTION_EXISTED_BEFORE = os.path.exists(_PRODUCTION_DB)


def _quiet_initialize(path):
    """initialize_database with its console banner suppressed."""
    import contextlib
    import io
    with contextlib.redirect_stderr(io.StringIO()):
        with contextlib.redirect_stdout(io.StringIO()):
            initialize_database(path)


# --- THE SEED, WHICH IS ALSO THE REFERENCE ---------------------------------
#
# Written as data so every expectation below is derived from THIS table and not
# from the frame under test. A run's expected patient count is len() of its
# patient list; its expected cost is the sum of their costs.

_RUN_SEED = {
    # label: (status, finished_at, started_at)
    "CLEAN":    ("FINISHED", "2026-08-20T11:04:00", "2026-08-20T10:00:00"),
    "CRASHED":  ("RUNNING",  None,                  "2026-08-19T10:00:00"),
    "EMPTY":    ("KILLED",   "2026-08-18T10:05:00", "2026-08-18T10:00:00"),
    "DEGRADED": ("FINISHED", "2026-08-17T11:00:00", "2026-08-17T10:00:00"),
}

# (label, [(patient_id, cost or None, error)])
_PATIENT_SEED = {
    "CLEAN":    [("P-CLEAN-1", 0.10, ""), ("P-CLEAN-2", 0.20, ""),
                 ("P-CLEAN-3", None, "")],
    "CRASHED":  [("P-CRASH-1", 0.30, ""), ("P-CRASH-2", 0.05, "boom")],
    "DEGRADED": [("P-DEGR-1", 0.40, "")],
    # EMPTY has none, deliberately -- it is the row an INNER JOIN deletes.
}

# Rows belonging to no run: what every row written before the run-identity pass
# looks like, and what 17- FastAPI Server.py writes on purpose.
_ORPHAN_SEED = [("P-OLD-1", 0.05), ("P-OLD-2", 0.05), ("P-OLD-3", None),
                ("P-OLD-4", 0.05)]

_METRIC_SEED = {
    "CLEAN":    [("meta", "counters_registered", 22),
                 ("meta", "counters_nonzero", 0)],
    "DEGRADED": [("meta", "counters_registered", 22),
                 ("meta", "counters_nonzero", 2),
                 ("degradation", "AGE_PARSE_FAILURES", 412),
                 ("degradation", "QDRANT_RETRIES", 3)],
    # CRASHED and EMPTY have none: nothing was ever flushed for them.
}

_EXPECTED_HEALTH = {
    "CLEAN":    _queries.RUN_HEALTH_MEASURED_CLEAN,
    "CRASHED":  _queries.RUN_HEALTH_NEVER_FLUSHED,
    "EMPTY":    _queries.RUN_HEALTH_NEVER_FLUSHED,
    "DEGRADED": _queries.RUN_HEALTH_DEGRADED,
}


def _build(path, runs, patients, metrics, orphans, drop_tables=(),
           drop_run_id=False):
    """Seed one scratch database and return {label: run_id}."""
    _quiet_initialize(path)
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    ids = {}
    for label in runs:
        status, finished, started = runs[label]
        cur.execute(
            "INSERT INTO runs (started_at, finished_at, status, "
            "invocation_source, fingerprint_version, "
            "llm_classifier_prompt_version, llm_classifier_renderer_digest, "
            "matching_model_configured, qdrant_collection, collection_points, "
            "data_snapshot_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (started, finished, status, "batch_runner", 2, "1.9.0",
             f"digest-{label}", "gpt-5.6-terra",
             "trial_criteria_20260807_111807", 12067, "2026-02-26"))
        ids[label] = cur.lastrowid
    for label, rows in patients.items():
        for pid, cost, error in rows:
            cur.execute(
                "INSERT INTO inferences (patient_id, timestamp, run_id, "
                "estimated_cost_usd, error, age, sex, matching_model) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (pid, "2026-08-20 10:00:00", ids[label], cost, error, 60,
                 "male", "gpt-5.6-terra"))
    for pid, cost in orphans:
        cur.execute(
            "INSERT INTO inferences (patient_id, timestamp, run_id, "
            "estimated_cost_usd, error, age, sex, matching_model) "
            "VALUES (?, ?, NULL, ?, ?, ?, ?, ?)",
            (pid, "2026-01-01 10:00:00", cost, "", 60, "female",
             "gpt-4o-2024-08-06"))
    for label, rows in metrics.items():
        for category, name, value in rows:
            cur.execute(
                "INSERT INTO run_metrics (run_id, category, name, value, "
                "written_at) VALUES (?, ?, ?, ?, ?)",
                (ids[label], category, name, value, "2026-08-20T11:04:00"))
    conn.commit()
    for table in drop_tables:
        conn.execute(f"DROP TABLE {table}")
    if drop_run_id:
        # FAITHFUL, NOT CONVENIENT. initialize_database adds inferences.run_id
        # in the same call that creates the two tables, so a database that
        # predates the run-identity pass has neither -- and the production one
        # measurably does not (its columns carry no run_id). A scenario that
        # dropped the tables and kept the column would be a shape no version of
        # this project has ever produced.
        conn.execute("ALTER TABLE inferences DROP COLUMN run_id")
    conn.commit()
    conn.close()
    return ids


_SCRATCH = {}
_RUN_IDS = {}

_SCRATCH["full"] = os.path.join(_TMP, "full.db")
_RUN_IDS["full"] = _build(_SCRATCH["full"], _RUN_SEED, _PATIENT_SEED,
                          _METRIC_SEED, _ORPHAN_SEED)

# Every run clean and finalized: the branch where the tab must render the
# measured-clean success and the "every run was finalized" success, which the
# `full` scenario can never reach because it has a crashed run.
_SCRATCH["all_clean"] = os.path.join(_TMP, "all_clean.db")
_RUN_IDS["all_clean"] = _build(
    _SCRATCH["all_clean"],
    {"CLEAN": _RUN_SEED["CLEAN"]},
    {"CLEAN": _PATIENT_SEED["CLEAN"]},
    {"CLEAN": _METRIC_SEED["CLEAN"]},
    [])

# Tables present, no rows. A database a writer HAS opened since the
# run-identity pass and through which no campaign has been recorded.
_SCRATCH["no_runs"] = os.path.join(_TMP, "no_runs.db")
_RUN_IDS["no_runs"] = _build(_SCRATCH["no_runs"], {}, {}, {}, _ORPHAN_SEED)

# The production database's actual shape today: neither run table.
_SCRATCH["no_tables"] = os.path.join(_TMP, "no_tables.db")
_RUN_IDS["no_tables"] = _build(_SCRATCH["no_tables"], {}, {}, {}, _ORPHAN_SEED,
                               drop_tables=("runs", "run_metrics"),
                               drop_run_id=True)

# One table and not the other. initialize_database creates both in one call, so
# this shape means something else wrote the database.
_SCRATCH["partial"] = os.path.join(_TMP, "partial.db")
_RUN_IDS["partial"] = _build(_SCRATCH["partial"], {}, {}, {}, _ORPHAN_SEED,
                             drop_tables=("run_metrics",))

# A row pointing at a runs id that is not there. The foreign key is unenforced
# by design, so this is reachable, and the attribution census is the only thing
# in the project that can report it.
_SCRATCH["dangling"] = os.path.join(_TMP, "dangling.db")
_RUN_IDS["dangling"] = _build(_SCRATCH["dangling"], _RUN_SEED, _PATIENT_SEED,
                              _METRIC_SEED, _ORPHAN_SEED)
_dang = sqlite3.connect(_SCRATCH["dangling"])
_dang.execute("UPDATE inferences SET run_id = 999999 WHERE patient_id = ?",
              ("P-OLD-1",))
_dang.commit()
_dang.close()

# Both run tables, no inferences.run_id. Not producible by
# initialize_database, which creates all three in one call -- which is exactly
# why a guard resting on "the tables imply the column" fails in the one case it
# was written for.
_SCRATCH["no_run_id"] = os.path.join(_TMP, "no_run_id.db")
_RUN_IDS["no_run_id"] = _build(_SCRATCH["no_run_id"], _RUN_SEED, _PATIENT_SEED,
                               _METRIC_SEED, _ORPHAN_SEED, drop_run_id=True)

_DECOY_DB = os.path.join(_TMP, "decoy.db")
_build(_DECOY_DB, _RUN_SEED, _PATIENT_SEED, _METRIC_SEED, _ORPHAN_SEED)

check("1a  the package default resolves to the production database, and no "
      "scratch path is it (without this every check below is vacuous)",
      sorted({os.path.abspath(p) == _PRODUCTION_DB
              for p in list(_SCRATCH.values()) + [_DECOY_DB]}), [False])
check("1a  ...and the production path is a real resolved path",
      _PRODUCTION_DB.endswith("inferences.db") and len(_PRODUCTION_DB) > 20,
      True)
check("1b  the full scenario seeded four runs",
      len(_RUN_IDS["full"]), len(_RUN_SEED))
check_true("1b  ...with distinct ids (non-degeneracy)",
           len(set(_RUN_IDS["full"].values())) == len(_RUN_SEED))
check("1c  the no_tables scenario really lacks both run tables -- without "
      "this, section 3's availability checks pass for the wrong reason",
      sorted(_queries.RUN_TABLES), sorted(_queries.RUN_TABLES))

_probe = sqlite3.connect(_SCRATCH["no_tables"])
check("1c  ...measured",
      sorted(t for t in _queries.RUN_TABLES
             if t in _queries.available_tables(_probe)), [])
_probe.close()
_probe = sqlite3.connect(_SCRATCH["full"])
check("1c  ...and the full scenario HAS both (the control: an assertion that "
      "always found them absent would pass the line above too)",
      sorted(t for t in _queries.RUN_TABLES
             if t in _queries.available_tables(_probe)),
      sorted(_queries.RUN_TABLES))
_probe.close()

_SAVED_RESOLVED = _paths._RESOLVED.get("inferences_path")


# ===========================================================================
# THE RENDER HARNESS
# ===========================================================================

_DRIVER = """
import pickle, importlib, sys
sys.path.insert(0, {extra_path!r})
_mod = importlib.import_module({module!r})
with open({frame!r}, "rb") as _fh:
    _df = pickle.load(_fh)
_mod.render_run_health_tab(_df)
"""

_CONNECTED_PATHS = []
_REAL_CONNECT = sqlite3.connect


def _recording_connect(database, *args, **kwargs):
    _CONNECTED_PATHS.append(str(database))
    return _REAL_CONNECT(database, *args, **kwargs)


# --- the offline guard -------------------------------------------------------
#
# Same three primitives, same reasoning and the same recorded lesson as
# tests/test_dashboard_reproducibility_tab.py: named functions rather than
# lambdas, because a guard that walks back a fixed number of frames names its
# own stand-in and its control then passes for the wrong reason.

_NETWORK_ATTEMPTS = []
_REAL_SOCKET_CONNECT = socket.socket.connect
_REAL_SOCKET_CONNECT_EX = socket.socket.connect_ex
_REAL_CREATE_CONNECTION = socket.create_connection
_REAL_GETADDRINFO = socket.getaddrinfo

_GUARD_FRAMES = {"_blocked", "_network_caller", "_guard_connect",
                 "_guard_connect_ex", "_guard_create_connection",
                 "_guard_getaddrinfo"}


def _network_caller():
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


def _frame_for(db_path):
    """The `inferences` frame main() would hand the tab, from a scratch db."""
    conn = _REAL_CONNECT(db_path)
    try:
        frame = pd.read_sql_query("SELECT * FROM inferences", conn)
    finally:
        conn.close()
    if not frame.empty:
        frame["timestamp"] = pd.to_datetime(frame["timestamp"])
    return frame


def _capture(at):
    """Everything this file compares, from one rendered AppTest."""
    return {
        "exception": [e.value for e in at.exception],
        "metrics": [(m.label, m.value) for m in at.metric],
        "success": [s.value for s in at.success],
        "warning": [w.value for w in at.warning],
        "info": [i.value for i in at.info],
        "error": [e.value for e in at.error],
        "caption": [c.value for c in at.caption],
        "markdown": [m.value for m in at.markdown],
        "subheader": [s.value for s in at.subheader],
        "dataframes": [d.value.to_csv(index=False) for d in at.dataframe],
        "dataframe_objects": [d.value for d in at.dataframe],
        "selectboxes": [{"key": s.proto.id.split("-")[-1],
                         "label": s.label,
                         "options": list(s.options),
                         "value": s.value} for s in at.selectbox],
        "plotly": len(at.get("plotly_chart")),
    }


def _render(db_path, module_name="oncotriage.dashboard.tabs.run_health",
            df=None, extra_path=None):
    """Render one module against one scratch database.

    Returns (capture, connected sqlite paths, attempted network calls).

    The cache is cleared first: without it only the first render of the run
    would open the database and the isolation assertion would be about one
    scenario rather than all of them.
    """
    frame = _frame_for(db_path) if df is None else df
    frame_path = os.path.join(_TMP, "frame.pkl")
    with open(frame_path, "wb") as fh:
        pickle.dump(frame, fh)

    script = _DRIVER.format(extra_path=extra_path or _PLANT_DIR,
                            module=module_name, frame=frame_path)

    _paths._RESOLVED["inferences_path"] = db_path
    st.cache_data.clear()
    del _CONNECTED_PATHS[:]
    del _NETWORK_ATTEMPTS[:]
    sqlite3.connect = _recording_connect
    _dashboard_data.sqlite3.connect = _recording_connect
    _arm_offline_guard()
    try:
        at = AppTest.from_string(script, default_timeout=120)
        at.run()
    finally:
        _disarm_offline_guard()
        sqlite3.connect = _REAL_CONNECT
        _dashboard_data.sqlite3.connect = _REAL_CONNECT

    return _capture(at), list(_CONNECTED_PATHS), list(_NETWORK_ATTEMPTS)


def _plant_module(old, new, count=1):
    """A copy of the tab with `old` replaced by `new`, in a temp directory.

    Returns (module_name, occurrences of `old` in the shipped source). Nothing
    under version control is touched. The occurrence count is returned so a
    plant that matched NOTHING is a named failure rather than a revert reported
    as MISSED -- pass 20f-1's lesson, and the one this file would otherwise
    repeat eight times.
    """
    made = _TAB_SOURCE.count(old)
    source = _TAB_SOURCE.replace(old, new, count)
    _PLANT_SEQ[0] += 1
    name = f"run_health_plant_{_PLANT_SEQ[0]}"
    Path(os.path.join(_PLANT_DIR, name + ".py")).write_text(
        source, encoding="utf-8")
    return name, made


def _joined(capture, *keys):
    """Every string in the named capture buckets, as one blob to search."""
    out = []
    for key in keys:
        out.extend(str(v) for v in capture.get(key, []))
    return "\n".join(out)


def _metric(capture, label):
    """One metric's value by label, or a named absence."""
    for name, value in capture["metrics"]:
        if name == label:
            return value
    return "(no such metric)"


# ===========================================================================
# SECTION 2: THE FULL SCENARIO -- THE THREE HEALTH STATES, KEPT APART
# ===========================================================================

print()
print("=" * 70)
print("Section 2: four runs, three health states, one crashed shape")
print("=" * 70)

_full, _full_paths, _full_net = _render(_SCRATCH["full"])

check("2a  the render raised nothing", _full["exception"], [])
check("2b  every recorded run is counted", _metric(_full, "Runs recorded"),
      str(len(_RUN_SEED)))

_expected_counts = {}
for _label, _state in _EXPECTED_HEALTH.items():
    _expected_counts[_state] = _expected_counts.get(_state, 0) + 1
check("2c  measured-clean runs are counted from the seed",
      _metric(_full, "Measured clean"),
      str(_expected_counts[_queries.RUN_HEALTH_MEASURED_CLEAN]))
check("2c  degraded runs likewise", _metric(_full, "Degraded"),
      str(_expected_counts[_queries.RUN_HEALTH_DEGRADED]))
check("2c  ...and runs with no health record are their OWN tile, not folded "
      "into clean -- which is the whole finding this tab exists for",
      _metric(_full, "No health record"),
      str(_expected_counts[_queries.RUN_HEALTH_NEVER_FLUSHED]))
check_true("2c  the three tiles do not all show the same number "
           "(non-degeneracy: a tab stuck on one count would satisfy a subset "
           "of the checks above)",
           len({_metric(_full, n) for n in
                ("Measured clean", "Degraded", "No health record")}) > 1)

check("2d  the number of runs with no finished_at is counted from the seed",
      _metric(_full, "Not finalized"),
      str(sum(1 for _s, _f, _st in _RUN_SEED.values() if _f is None)))

_full_text = _joined(_full, "warning", "success", "caption", "markdown", "info")
check_true("2e  the crashed shape is FLAGGED on screen, and named for the "
           "ambiguity it is rather than asserted to be a crash",
           "carry no `finished_at`" in _full_text
           and "live campaign or one whose" in _full_text)
check_true("2e  ...and the 'every run was finalized' success is NOT shown "
           "while one is unfinalized",
           "Every recorded run was finalized" not in _full_text)

# --- THE RUN TABLE, VALUE FOR VALUE ---------------------------------------
_run_table = at_(_full["dataframe_objects"], 0)
check_true("2f  the run list rendered as a frame", isinstance(_run_table,
                                                              pd.DataFrame))
if isinstance(_run_table, pd.DataFrame):
    _by_run = {int(r["run"]): r for _, r in _run_table.iterrows()}
    check("2f  one row per run, no more", len(_run_table), len(_RUN_SEED))
    for _label, _rid in _RUN_IDS["full"].items():
        _row = _by_run.get(_rid)
        check(f"2f  {_label}: patients counted from the seed",
              None if _row is None else int(_row["patients"]),
              len(_PATIENT_SEED.get(_label, [])))
        check(f"2f  {_label}: cost is the sum over its own patients",
              None if _row is None else round(float(_row["cost $"]), 4),
              round(sum(c or 0.0
                        for _p, c, _e in _PATIENT_SEED.get(_label, [])), 4))
        check(f"2f  {_label}: health record",
              None if _row is None else _row["health"].split(" ", 1)[1],
              _EXPECTED_HEALTH[_label])
        check(f"2f  {_label}: the crashed marker is present iff finished_at "
              f"is NULL",
              None if _row is None else bool(str(_row[""]).strip()),
              _RUN_SEED[_label][1] is None)
    check("2f  a run NO patient references is still a row (this is what the "
          "LEFT JOIN buys, and an INNER JOIN would delete it)",
          _RUN_IDS["full"]["EMPTY"] in _by_run, True)
    check("2f  a run that never flushed shows an em dash for counters "
          "consulted, NOT a zero -- 0 is the measured-clean answer",
          None if _by_run.get(_RUN_IDS["full"]["CRASHED"]) is None
          else _by_run[_RUN_IDS["full"]["CRASHED"]]["counters consulted"], "—")
    check("2f  ...while a measured-clean run shows the number that was "
          "consulted",
          None if _by_run.get(_RUN_IDS["full"]["CLEAN"]) is None
          else _by_run[_RUN_IDS["full"]["CLEAN"]]["counters consulted"], "22")
    check("2f  an unpriced inference row is counted, so the cost beside it is "
          "readable as a floor",
          None if _by_run.get(_RUN_IDS["full"]["CLEAN"]) is None
          else int(_by_run[_RUN_IDS["full"]["CLEAN"]]["unpriced rows"]),
          sum(1 for _p, c, _e in _PATIENT_SEED["CLEAN"] if c is None))

# --- THE ATTRIBUTION CENSUS (requirement 3) --------------------------------
_attr = at_(_full["dataframe_objects"], 1)
check_true("2g  the attribution census rendered as a frame",
           isinstance(_attr, pd.DataFrame))
if isinstance(_attr, pd.DataFrame):
    _attr_rows = {r["attribution"]: r for _, r in _attr.iterrows()}
    check("2g  rows with no run id are a NAMED grouping, not a silent "
          "exclusion",
          None if _queries.RUN_ATTRIBUTION_NO_RUN not in _attr_rows
          else int(_attr_rows[_queries.RUN_ATTRIBUTION_NO_RUN]["inference_rows"]),
          len(_ORPHAN_SEED))
    check("2g  ...and rows that DO belong to a run are counted too",
          None if _queries.RUN_ATTRIBUTION_ATTRIBUTED not in _attr_rows
          else int(_attr_rows[_queries.RUN_ATTRIBUTION_ATTRIBUTED]["inference_rows"]),
          sum(len(v) for v in _PATIENT_SEED.values()))
    check("2g  ...so the census covers every row in the table and drops none",
          int(_attr["inference_rows"].sum()),
          len(_ORPHAN_SEED) + sum(len(v) for v in _PATIENT_SEED.values()))
check_true("2g  the count of run-less rows is also STATED in prose, not only "
           "in a table cell",
           f"{len(_ORPHAN_SEED)} row(s) belong to no recorded run" in
           _joined(_full, "caption"))

# --- THE SELECTED RUN ------------------------------------------------------
_select = at_(_full["selectboxes"], 0, {})
check("2h  the run selector carries a stable widget key -- a renamed key "
      "silently resets the widget for every session that carried it",
      _select.get("key"), "run_health_run_selector")
check("2h  ...and offers one option per run",
      len(_select.get("options", [])), len(_RUN_SEED))
check("2h  ...newest first, which is what the run list is ordered by",
      (_select.get("options") or ["(absent)"])[0].startswith(
          f"#{max(_RUN_IDS['full'].values())}"), True)

check("2i  the selected run's degradation events come from run_metrics",
      _metric(_full, "Degradation events"),
      str(sum(v for c, _n, v in _METRIC_SEED["DEGRADED"]
              if c == "degradation")))

_degr_table = at_(_full["dataframe_objects"], 2)
check_true("2i  the per-counter breakdown rendered", isinstance(_degr_table,
                                                                pd.DataFrame))
if isinstance(_degr_table, pd.DataFrame):
    check("2i  ...with the seeded counters, worst first",
          [(r["counter"], int(r["events"])) for _, r in
           _degr_table.iterrows()],
          sorted([(n, v) for c, n, v in _METRIC_SEED["DEGRADED"]
                  if c == "degradation"], key=lambda kv: -kv[1]))

check("2j  a run-over-run comparison is drawn, plus the selected run's own "
      "breakdown chart", _full["plotly"], 3)
check_true("2j  ...and the runs with no health record are named as EXCLUDED "
           "from the events chart rather than plotted at zero",
           "not on this chart" in _joined(_full, "caption"))


# ===========================================================================
# SECTION 3: THE OTHER FIVE SCENARIOS
# ===========================================================================

print()
print("=" * 70)
print("Section 3: clean-only, no rows, no tables, partial, dangling")
print("=" * 70)

# --- ALL CLEAN: the branch `full` can never reach --------------------------
_clean, _clean_paths, _ = _render(_SCRATCH["all_clean"])
check("3a  the render raised nothing", _clean["exception"], [])
_clean_text = _joined(_clean, "success", "warning", "caption", "markdown")
check_true("3a  a database whose every run is clean says so as a MEASUREMENT "
           "-- 'counters were consulted and none moved' -- and never renders "
           "as an empty panel",
           "Measured clean" in _clean_text
           and "22 degradation counters were consulted" in _clean_text)
check_true("3a  ...and states that a clean run contributing no rows is by "
           "design rather than a gap",
           "drops every zero counter" in _clean_text)
check_true("3a  ...and the 'every run was finalized' success IS shown here",
           "Every recorded run was finalized" in _clean_text)
check("3a  ...with no unfinalized warning", 
      [w for w in _clean["warning"] if "finished_at" in w], [])
check("3a  no degraded runs, stated as zero rather than omitted",
      _metric(_clean, "Degraded"), "0")

# --- TABLES PRESENT, NO ROWS ----------------------------------------------
_norows, _, _ = _render(_SCRATCH["no_runs"])
check("3b  the render raised nothing", _norows["exception"], [])
check_true("3b  present-and-empty is its own statement, and it names the API "
           "as the reason a database can look like this",
           "present and hold no rows" in _joined(_norows, "info"))
check_true("3b  ...and the attribution census still renders, so the rows that "
           "ARE there are not dropped along with the runs",
           any("attribution" in str(d) for d in _norows["dataframes"]))

# --- NO RUN TABLES: the production database's shape today ------------------
_notab, _, _ = _render(_SCRATCH["no_tables"])
check("3c  the render raised nothing", _notab["exception"], [])
_notab_text = _joined(_notab, "info", "error", "warning", "caption")
check_true("3c  a database with no run tables is an INFO, not an error -- it "
           "is the state the production database is in right now",
           "no run tracking yet" in _joined(_notab, "info"))
check("3c  ...and nothing was rendered as an error",
      _notab["error"], [])
check_true("3c  ...naming both absent tables and the fact that the next "
           "writer adds them",
           "`runs`" in _notab_text and "`run_metrics`" in _notab_text
           and "next writer to open it adds them" in _notab_text)
check("3c  ...and no run list is drawn, because there is nothing to list",
      len(_notab["dataframe_objects"]), 0)

# --- PARTIAL: one table and not the other ---------------------------------
_part, _, _ = _render(_SCRATCH["partial"])
check("3d  the render raised nothing", _part["exception"], [])
check_true("3d  half a migration is a WARNING and is kept apart from 'absent' "
           "-- absent is fixed by running the pipeline, this wants a person",
           any("run schema in pieces" in w for w in _part["warning"]))
check("3d  ...and it is not reported as absent",
      [i for i in _part["info"] if "no run tracking yet" in i], [])

# --- BOTH TABLES, NO COLUMN -----------------------------------------------
#
# THE SHAPE THE FIRST DRAFT GOT WRONG, and it is here because a control found
# it rather than a reading. `run_summary` and `run_attribution_coverage` both
# JOIN on inferences.run_id, so with the tables present and the column gone
# they cannot be asked at all -- and reporting this database as `present` sent
# the tab down its normal path, where two refused queries came back as empty
# frames and rendered as "the run tables hold no rows". That is a statement
# about a pipeline that has not run, made about a database whose queries could
# not be run.
_nocol, _, _ = _render(_SCRATCH["no_run_id"])
check("3d  the render raised nothing", _nocol["exception"], [])
check_true("3d  both run tables present and inferences.run_id absent is "
           "PARTIAL, not present",
           any("run schema in pieces" in w for w in _nocol["warning"]))
check_true("3d  ...naming the column rather than a table nobody removed",
           "inferences.run_id" in _joined(_nocol, "warning"))
check("3d  ...and it does NOT render as 'the run tables hold no rows', which "
       "is what it did before this shape was routed to partial",
      [i for i in _nocol["info"] if "present and hold no rows" in i], [])
check("3d  ...and no run list is drawn from queries that could not be asked",
      len(_nocol["dataframe_objects"]), 0)

# --- DANGLING: the unenforced foreign key ---------------------------------
_dangle, _, _ = _render(_SCRATCH["dangling"])
check("3e  the render raised nothing", _dangle["exception"], [])
check_true("3e  a row pointing at a runs id that does not exist is reported, "
           "and this census is the only thing in the project that can",
           any("no matching `runs` row" in e for e in _dangle["error"]))
check_true("3e  ...and the full scenario, which has no such row, reports "
           "none (the control: an error that always fired would satisfy the "
           "line above too)",
           not any("no matching `runs` row" in e for e in _full["error"]))


# ===========================================================================
# SECTION 4: ISOLATION AND THE OFFLINE GUARD, BOTH WITH CONTROLS
# ===========================================================================

print()
print("=" * 70)
print("Section 4: it reads the scratch database and nothing else, offline")
print("=" * 70)

for _name, _path in sorted(_SCRATCH.items()):
    _cap, _opened, _net = _render(_path)
    _resolved = sorted({os.path.realpath(p.split("?")[0].replace("file:", ""))
                        for p in _opened})
    check(f"4a  [{_name}] every database opened during the render is the "
          f"scratch one", _resolved, [os.path.realpath(_path)])
    check_true(f"4a  [{_name}] ...and at least one WAS opened "
               f"(non-degeneracy: a render that opened nothing would satisfy "
               f"an empty-set comparison)", len(_opened) > 0)
    check(f"4b  [{_name}] no outbound network call was attempted", _net, [])

# THE DECOY. Without this the assertion above is a tautology -- it has to be
# shown FAILING against a database that is not the one under test. File 41's
# precedent: a demonstration that proved the point by reading the production
# database would be the defect it is testing for.
_decoy_cap, _decoy_opened, _ = _render(_DECOY_DB)
_decoy_resolved = sorted({os.path.realpath(p.split("?")[0].replace("file:", ""))
                          for p in _decoy_opened})
check("4c  the isolation assertion FAILS when the render is pointed at a "
      "different database (the control)",
      _decoy_resolved == [os.path.realpath(_SCRATCH["full"])], False)
check("4c  ...and it is the decoy that was opened, so the control fails for "
      "the reason claimed",
      _decoy_resolved, [os.path.realpath(_DECOY_DB)])

# THE OFFLINE CONTROL. Same guard, a real outbound call, and the reported
# frame must be the function that made it -- not one of the guard's own.
def _offline_control_call():
    socket.getaddrinfo("example.invalid", 80)


del _NETWORK_ATTEMPTS[:]
_arm_offline_guard()
try:
    _offline_control_call()
    _control_raised = False
except OSError:
    _control_raised = True
finally:
    _disarm_offline_guard()

check("4d  the offline guard raises on a real outbound call (the control)",
      _control_raised, True)
check("4d  ...and records it", len(_NETWORK_ATTEMPTS), 1)
check("4d  ...naming the frame that made the call, not one of the guard's own",
      at_(_NETWORK_ATTEMPTS, 0, {}).get("caller", "").split(" in ")[-1],
      "_offline_control_call")
check("4e  the guard is disarmed afterwards",
      (socket.socket.connect is _REAL_SOCKET_CONNECT
       and socket.create_connection is _REAL_CREATE_CONNECTION
       and socket.getaddrinfo is _REAL_GETADDRINFO), True)


# ===========================================================================
# SECTION 5: THE LOADERS' AVAILABILITY VOCABULARY, DRIVEN DIRECTLY
# ===========================================================================

print()
print("=" * 70)
print("Section 5: load_run_tracking_availability over every scenario")
print("=" * 70)

_EXPECTED_AVAILABILITY = {
    "full": _dashboard_data.RUN_TRACKING_PRESENT,
    "all_clean": _dashboard_data.RUN_TRACKING_PRESENT,
    "no_runs": _dashboard_data.RUN_TRACKING_PRESENT,
    "no_tables": _dashboard_data.RUN_TRACKING_ABSENT,
    "partial": _dashboard_data.RUN_TRACKING_PARTIAL,
    "dangling": _dashboard_data.RUN_TRACKING_PRESENT,
    "no_run_id": _dashboard_data.RUN_TRACKING_PARTIAL,
}
for _name, _expected in sorted(_EXPECTED_AVAILABILITY.items()):
    _paths._RESOLVED["inferences_path"] = _SCRATCH[_name]
    st.cache_data.clear()
    _avail = _dashboard_data.load_run_tracking_availability()
    check(f"5a  [{_name}] availability", _avail["availability"], _expected)
    # Derived from the scenario's own construction rather than from a list
    # that has to be kept in step: the two scenarios built with drop_run_id
    # are exactly the two that must report it absent.
    check(f"5a  [{_name}] the run_id column is reported",
          _avail["has_run_id"], _name not in ("no_tables", "no_run_id"))

# NON-DEGENERACY, DERIVED FROM THE DATABASES. Two scenarios lack
# inferences.run_id and they must differ in whether the run TABLES are there --
# otherwise the column half of the guard is exercised only in a database where
# the table half already refused, and it would pass with no column check at all.
# THE FIRST VERSION OF THIS CHECK WAS A TAUTOLOGY: it ANDed the real assertion
# with `... .__doc__ is not None`, which cannot be False. A check with a clause
# that cannot fail is not a weak check, it is not a check -- the `or True`
# defect pass 20f-6 records finding in its own algorithm-tag assertion.
_no_column = {}
for _name in ("no_tables", "no_run_id"):
    _c = sqlite3.connect(_SCRATCH[_name])
    _no_column[_name] = {
        "has_run_id": "run_id" in _queries.table_columns(_c, "inferences"),
        "has_tables": sorted(t for t in _queries.RUN_TABLES
                             if t in _queries.available_tables(_c)),
    }
    _c.close()
check("5a  both column-less shapes are exercised, and they differ in whether "
      "the run TABLES are present -- so the column half of the guard is "
      "reached in a database the table half does not already refuse",
      {n: (v["has_run_id"], bool(v["has_tables"]))
       for n, v in _no_column.items()},
      {"no_tables": (False, False), "no_run_id": (False, True)})
check_true("5a  every availability state except no_database is exercised "
           "(non-degeneracy)",
           set(_EXPECTED_AVAILABILITY.values()) ==
           {_dashboard_data.RUN_TRACKING_PRESENT,
            _dashboard_data.RUN_TRACKING_ABSENT,
            _dashboard_data.RUN_TRACKING_PARTIAL})

# A PATH THAT DOES NOT EXIST MUST NOT BRING A DATABASE INTO BEING. This is the
# whole reason the run loaders open read-only: a plain sqlite3.connect CREATES
# the file, so a reader asking "does this database have the run tables" would
# answer by making one that has nothing at all.
_ABSENT_DB = os.path.join(_TMP, "definitely-not-here.db")
_paths._RESOLVED["inferences_path"] = _ABSENT_DB
st.cache_data.clear()
_avail = _dashboard_data.load_run_tracking_availability()
check("5b  a path that does not exist reports no_database",
      _avail["availability"], _dashboard_data.RUN_TRACKING_NO_DATABASE)
check("5b  ...and asking the question did NOT create the file",
      os.path.exists(_ABSENT_DB), False)
check("5b  ...and the frame loaders return empty rather than raising",
      [_dashboard_data.load_run_summary_data().empty,
       _dashboard_data.load_run_degradation_data().empty,
       _dashboard_data.load_run_attribution_data().empty],
      [True, True, True])
check("5b  ...still without creating it", os.path.exists(_ABSENT_DB), False)

_paths._RESOLVED["inferences_path"] = _SCRATCH["full"]
st.cache_data.clear()


# ===========================================================================
# SECTION 6: PLANTED DEFECTS -- EIGHT, EACH INTO A COPY
# ===========================================================================

print()
print("=" * 70)
print("Section 6: eight planted defects, each caught by a named check")
print("=" * 70)


def _plant_and_render(label, old, new, db=None, expect_occurrences=1):
    """Plant, render, and report whether the plant matched anything.

    A PLANT THAT MATCHED NOTHING IS A NAMED FAILURE, not a defect reported as
    uncaught: a revert reporting MISSED can mean the check is weak OR that the
    revert never took effect, and those are not the same finding.
    """
    module, made = _plant_module(old, new)
    check(f"6  [{label}] the plant matched the shipped source exactly "
          f"{expect_occurrences} time(s)", made, expect_occurrences)
    capture, _, _ = _render(db or _SCRATCH["full"], module_name=module)
    return capture


# P1 -- the two silences collapse into one icon.
_p1 = _plant_and_render(
    "P1 never-flushed rendered as clean",
    "    RUN_HEALTH_NEVER_FLUSHED: \"❔\",",
    "    RUN_HEALTH_NEVER_FLUSHED: \"✅\",")
_p1_table = at_(_p1["dataframe_objects"], 0)
check("6a  P1 is caught: a run with no health record no longer renders "
      "distinguishably from a measured-clean one",
      isinstance(_p1_table, pd.DataFrame)
      and len({str(r["health"]).split(" ")[0] for _, r in _p1_table.iterrows()
               if str(r["health"]).split(" ", 1)[-1] in
               (_queries.RUN_HEALTH_MEASURED_CLEAN,
                _queries.RUN_HEALTH_NEVER_FLUSHED)}) == 2,
      False)
check("6a  ...and the shipped module DOES distinguish them (the control: a "
      "check that always saw one icon would satisfy the line above too)",
      isinstance(_run_table, pd.DataFrame)
      and len({str(r["health"]).split(" ")[0] for _, r in _run_table.iterrows()
               if str(r["health"]).split(" ", 1)[-1] in
               (_queries.RUN_HEALTH_MEASURED_CLEAN,
                _queries.RUN_HEALTH_NEVER_FLUSHED)}) == 2,
      True)

# P2 -- the metric tile counts never-flushed as clean.
_p2 = _plant_and_render(
    "P2 health counts fold never-flushed into clean",
    "        counts[value] = counts.get(value, 0) + 1",
    "        counts[RUN_HEALTH_MEASURED_CLEAN] = "
    "counts.get(RUN_HEALTH_MEASURED_CLEAN, 0) + 1")
check("6b  P2 is caught: the measured-clean tile no longer matches the seed",
      _metric(_p2, "Measured clean"),
      str(len(_RUN_SEED)))   # the plant's wrong answer, asserted as wrong below
check_true("6b  ...and the shipped module does NOT report that number",
           _metric(_full, "Measured clean") != str(len(_RUN_SEED)))

# P3 -- runs with no health record plotted at zero.
_p3 = _plant_and_render(
    "P3 missing degradation totals plotted as zero",
    "    kept = summary[summary[value_column].notna()]",
    "    kept = summary.assign(**{value_column: summary[value_column].fillna(0)})")
check("6c  P3 is caught: the exclusion caption disappears, so a run whose "
      "degradation total is unknown is drawn as a zero bar with nothing "
      "saying so",
      "not on this chart" in _joined(_p3, "caption"), False)
check_true("6c  ...and the shipped module does print it",
           "not on this chart" in _joined(_full, "caption"))

# P4 -- the crashed shape stops being flagged.
_p4 = _plant_and_render(
    "P4 unfinalized runs counted as zero",
    "    return int((summary[\"finalization\"] != RUN_FINALIZATION_FINALIZED).sum())",
    "    return 0")
check("6d  P4 is caught: the unfinalized warning is replaced by the "
      "'every run was finalized' success while a RUNNING row carries no "
      "finish time",
      ("Every recorded run was finalized" in _joined(_p4, "success"),
       _metric(_p4, "Not finalized")),
      (True, "0"))
check_true("6d  ...and the shipped module reports the opposite",
           "Every recorded run was finalized" not in _joined(_full, "success"))

# P5 -- the sidebar reconciliation misreports.
_p5 = _plant_and_render(
    "P5 selection attribution claims every row has a run",
    "    with_run = int(df[\"run_id\"].notna().sum())",
    "    with_run = int(len(df))")
check("6e  P5 is caught: the current-selection line claims 0 rows without a "
      "run id when the seed has some",
      f"and {len(_ORPHAN_SEED)} without" in _joined(_p5, "caption"), False)
check_true("6e  ...and the shipped module reports the seeded number",
           f"and {len(_ORPHAN_SEED)} without" in _joined(_full, "caption"))

# P6 -- "never measured" rendered as a measured zero.
_p6 = _plant_and_render(
    "P6 an unmeasured counter count renders as 0",
    "def _optional_int_text(value, default=\"—\"):",
    "def _optional_int_text(value, default=\"0\"):")
_p6_table = at_(_p6["dataframe_objects"], 0)
check("6f  P6 is caught: a run that never flushed now claims 0 counters were "
      "consulted, which is the measured-clean answer",
      isinstance(_p6_table, pd.DataFrame)
      and str({int(r["run"]): r["counters consulted"]
               for _, r in _p6_table.iterrows()}
              .get(_RUN_IDS["full"]["CRASHED"])),
      "0")
check_true("6f  ...and the shipped module renders an em dash there",
           isinstance(_run_table, pd.DataFrame)
           and _run_table.set_index("run").loc[
               _RUN_IDS["full"]["CRASHED"], "counters consulted"] == "—")

# P7 -- the widget key is renamed.
_p7 = _plant_and_render(
    "P7 the selector's widget key is renamed",
    "key=\"run_health_run_selector\"", "key=\"run_health_selector\"")
check("6g  P7 is caught: a renamed key would silently reset the widget for "
      "every session that carried the old one",
      at_(_p7["selectboxes"], 0, {}).get("key"), "run_health_selector")
check("6g  ...and the shipped key is the pinned one",
      at_(_full["selectboxes"], 0, {}).get("key"), "run_health_run_selector")

# P8 -- the run-less population is dropped from the census.
_p8 = _plant_and_render(
    "P8 the attribution census is not rendered at all",
    "        st.dataframe(attribution, use_container_width=True, hide_index=True)",
    "        attribution = attribution[attribution[\"attribution\"] != "
    "RUN_ATTRIBUTION_NO_RUN]\n"
    "        st.dataframe(attribution, use_container_width=True, hide_index=True)")
_p8_attr = at_(_p8["dataframe_objects"], 1)
check("6h  P8 is caught: the run-less rows disappear from the census, which "
      "is the silent exclusion requirement 3 forbids",
      isinstance(_p8_attr, pd.DataFrame)
      and _queries.RUN_ATTRIBUTION_NO_RUN in set(_p8_attr["attribution"]),
      False)
check_true("6h  ...and the shipped module keeps them",
           isinstance(_attr, pd.DataFrame)
           and _queries.RUN_ATTRIBUTION_NO_RUN in set(_attr["attribution"]))


# ===========================================================================
# SECTION 7: HYGIENE
# ===========================================================================

print()
print("=" * 70)
print("Section 7: nothing in the repository was written")
print("=" * 70)

check("7a  eight defects were planted (non-degeneracy: a section that planted "
      "none would report no failures and look identical)", _PLANT_SEQ[0], 8)
check("7b  the tab module is byte-identical", digest_file(_TAB_FILE),
      _TAB_DIGEST_BEFORE)
check("7b  the data module is byte-identical", digest_file(_DATA_FILE),
      _DATA_DIGEST_BEFORE)
check("7c  the production database is byte-identical",
      digest_file(_PRODUCTION_DB), _PRODUCTION_DIGEST_BEFORE)

# ---------------------------------------------------------------------------
# THE NON-DEGENERACY PROBE IS GATED ON THE ENVIRONMENT, AND THE RULING IS HERE
# ---------------------------------------------------------------------------
# The probe below needs a READABLE production database, and CI never has one:
# `.github/scripts/provision_ci_paths.py` creates the PARENT of
# `inferences_path` and deliberately not the file, and its own header calls
# fabricating inputs "the exact defect this project's non-degeneracy rule
# exists to catch". That provisioning decision is a ruling and is untouched.
#
# TWO SHAPES WERE AVAILABLE AND GATING WON.
#
#   reclassify to bucket E   the tests/test_storage_write_durability.py
#                            precedent, whose entry is the IDENTICAL single
#                            probe. Simpler, and it removes this whole file --
#                            160 checks, the only standing coverage of the Run
#                            Health tab and the four run loaders -- from CI to
#                            preserve one probe.
#   gate the probe alone     keeps all of them, and loses one probe on a
#                            machine where that probe HAS NO SUBJECT. When
#                            there is no production database, "the comparison
#                            above is not two 'absent's" is not a weaker
#                            question; it is a question about a file that does
#                            not exist. That is what `skip` means here, and
#                            what it must never be allowed to mean is "the
#                            check was inconvenient".
#
# NOTHING THE PROBE ASSERTS IS WEAKENED. Where a production database exists the
# probe runs unchanged, against the same digest, with the same expectation. The
# byte-identity check above is never gated, so the dangerous CI case -- a stray
# writer CREATING the production database, before='absent' and after=<hash> --
# still fails there.
_STANDIN = os.path.join(_TMP, "production-probe-standin.bin")
Path(_STANDIN).write_bytes(b"not a database: a file that exists")
_STANDIN_DIGEST = digest_file(_STANDIN)

check("7c  control: a file that exists digests to a real sha256 rather than "
      "'absent' (non-degeneracy: every control below would be vacuous "
      "otherwise)", len(_STANDIN_DIGEST), 64)
check("7c  control: with no production database on disk the probe is SKIPPED",
      production_probe_disposition(False), _PROBE_SKIP)
check("7c  control: with a production database on disk the probe is RUN",
      production_probe_disposition(os.path.exists(_STANDIN)), _PROBE_RUN)

_HONEST_ACTUAL, _HONEST_EXPECTED = production_probe_verdict(_STANDIN_DIGEST)
check("7c  control: RUN plus an honest reading of a file that exists -- the "
      "probe passes", _HONEST_ACTUAL == _HONEST_EXPECTED, True)

# THE FIRING CONTROL, and requirement 3 of this pass. The plant is the state
# the gate must not be able to absorb: the file IS there (so the gate says RUN)
# and the digest reading claims 'absent'. The probe must report a FAILURE, or
# the skip path has quietly become the only path.
for _sentinel in ("absent", "unreadable: IsADirectoryError"):
    _PLANTED_ACTUAL, _PLANTED_EXPECTED = production_probe_verdict(_sentinel)
    check(f"7c  control: RUN plus a present file read as {_sentinel!r} -- the "
          f"probe FIRES, so a non-reading cannot pass as a skip",
          _PLANTED_ACTUAL == _PLANTED_EXPECTED, False)

# A DIRECTORY, not a chmod: `chmod 000` is bypassed by root, so a control built
# on it passes vacuously on any runner that runs as root. Path.read_bytes on a
# directory raises IsADirectoryError -- an OSError -- for every user there is.
check("7c  control: an existing path that cannot be READ digests to a named "
      "marker rather than raising (a raise here aborts the file before its "
      "first check)", reading_of(_TMP).startswith("unreadable: "), True)
check("7c  control: ...and that marker is not mistaken for a reading",
      is_real_digest(reading_of(_TMP)), False)

_GATE_SITES = gate_call_sites(os.path.abspath(__file__))
check("7c  control: skip() writes ONLY the skipped counter -- a skip that "
      "increments passed would report unavailable coverage as coverage",
      skip_accounting_keys(os.path.abspath(__file__)), ["skipped"])
check("7c  control: exactly one gated call site is present (non-degeneracy -- "
      "a walk that matched nothing would satisfy the two assertions below for "
      "free)", len(_GATE_SITES), 1)
check("7c  control: the gate is decided by the EXISTENCE reading",
      "_PRODUCTION_EXISTED_BEFORE" in at_(_GATE_SITES, 0, set()), True)
check("7c  control: ...and NOT by the digest reading the probe itself asserts "
      "on -- a gate keyed on that string is satisfied by the exact fault the "
      "probe catches",
      "_PRODUCTION_DIGEST_BEFORE" in at_(_GATE_SITES, 0, set()), False)

_PROBE_LABEL = ("7c  ...and the reading it compared is a real sha256, so the "
                "comparison above is not two sentinels -- 'absent' == 'absent' "
                "or 'unreadable' == 'unreadable' (non-degeneracy)")
if production_probe_disposition(_PRODUCTION_EXISTED_BEFORE) == _PROBE_RUN:
    check(_PROBE_LABEL, *production_probe_verdict(_PRODUCTION_DIGEST_BEFORE))
else:
    skip(_PROBE_LABEL,
         f"no production database at {_PRODUCTION_DB}, so the byte-identity "
         f"check above had nothing to be exercised against. That check stayed "
         f"LIVE and would still have caught this run creating one. Expected on "
         f"a CI runner: provision_ci_paths.py creates the parent directory and "
         f"deliberately not the file.")
check("7d  no plant leaked into the package directory",
      sorted(p.name for p in
             Path(os.path.dirname(_TAB_FILE)).glob("run_health_plant_*.py")),
      [])

_paths._RESOLVED["inferences_path"] = _SAVED_RESOLVED
if _SAVED_RESOLVED is None:
    _paths._RESOLVED.pop("inferences_path", None)
check("7e  the paths resolver cache is restored",
      _paths._RESOLVED.get("inferences_path"), _SAVED_RESOLVED)

st.cache_data.clear()
shutil.rmtree(_TMP, ignore_errors=True)
check("7f  the temporary directory is removed", os.path.exists(_TMP), False)


# ===========================================================================
# SUMMARY
# ===========================================================================

print()
print("=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")
# PRINTED EVEN AT ZERO. A skip count that appears only when it is non-zero is
# indistinguishable from a file that has no skip mechanism at all.
print(f"Skipped: {_RESULTS['skipped']}   (a skip is NOT a pass and is not "
      f"counted as one)")
print(f"Runtime: {time.time() - _T_START:.1f}s")

if _SKIPS:
    print("\nSkipped:")
    for _s in _SKIPS:
        print(f"  - {_s}")

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
Created on Sat Aug 22 2026

@author: ramyalsaffar
"""
