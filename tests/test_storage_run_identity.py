# Run Identity Test
##################

"""The `runs` table, the `inferences.run_id` reference, and what NULL means in it.

WHAT WAS MISSING
----------------
``inferences`` and ``trial_matches`` are per-PATIENT records. Neither carried
anything about the CAMPAIGN that produced them, so "which rows belong to one
batch run" was recovered by looking for gaps between consecutive ``timestamp``
values. That heuristic is wrong in four ways and silent in all of them: a
RESUMED run reads as two campaigns; two campaigns started back to back read as
one; an API row written by "17- FastAPI Server.py" during a batch run is
indistinguishable from a batch row; and no gap between timestamps says anything
about the CONFIGURATION, which is what a run-level number has to be attributed
to.

WHAT THIS FILE HOLDS
--------------------
    1. THE TWO RESTATED VOCABULARIES ROUND-TRIP. ``RUN_FINGERPRINT_COLUMNS``
       equals ``("fingerprint_version",) + run_fingerprint.FINGERPRINT_FIELDS``
       and ``RUN_RECORD_TERMINAL_STATUSES`` equals
       ``tracking.RUN_STATUSES`` -- both restated in the storage layer because
       importing either module would make storage depend on the AGENT layer, and
       both therefore able to drift. A test may import all three because a test
       is in nobody's import graph; that is the whole reason the check lives
       here rather than there.
    2. THE MIGRATION IS ADDITIVE AND IDEMPOTENT, through the real
       ``initialize_database``: fresh, run twice, and against a PRE-MIGRATION
       database built with the old `inferences` shape and no `runs` table at
       all, whose existing rows are required to survive.
    3. THE RUN ROW IS CREATED WITH THE STAMP AS COLUMNS and finalized with a
       terminal status and a real ``finished_at``.
    4. THE COERCION RULE, which is the one place the storage of a stamp is not a
       straight copy: ``collection_points`` NULLs an unresolved ``"unknown"``
       rather than letting a TEXT value into an INTEGER-affinity column, where
       SQLite would sort it above every real count.
    5. ``run_id`` IS WRITTEN ON THE BATCH PATH AND NULL ON A DIRECT CALL, read
       back out of SQLite, with the two shown to be separable in SQL.
    6. A SECOND ``main()`` IN ONE PROCESS CREATES A DISTINCT RUN, behaviourally
       (two ``start_run_record`` calls, two ids, two rows) and structurally
       (``main()`` holds the id as a LOCAL, threads it into both passes, and no
       module-level "current run" exists for a second call to inherit).
    7. ``run_batch`` AND ``run_resample`` FORWARD IT TO EVERY WORKER, driven for
       real through the shipped functions with a recording stand-in.
    8. FINALIZATION NEVER RAISES: no id, an unknown status, a row that is not
       there, and a database that cannot be opened -- each driven by creating
       the real condition, each counted, none of them raising.
    9. THE CRASHED-RUN SHAPE IS DISTINGUISHABLE IN SQL, and the query is shown
       to stop matching once the row is finalized.
   10. THE PRODUCTION DATABASE IS NEVER TOUCHED: its sha256 is taken at the top
       of this file and compared at the bottom, and the isolation is asserted to
       be non-degenerate before anything is written.
   11. TEN NEGATIVE CONTROLS, each shown to fire.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO MODEL LOAD, NO CORPUS, NO GIT
HISTORY, and NOT in the collision matrix: every database is a temp file every
call is pointed at explicitly, ``paths._RESOLVED`` is seeded so nothing can
resolve to the production tree, and the two repository files it READS --
``oncotriage/storage/database_logger.py`` and ``oncotriage/batch/runner.py`` --
are written by neither of the suite's two writers.

IT EXECS NOTHING, so it needs no ``_EXEC_ALLOWLIST`` entry. Every control is
either a different INPUT to a pure function, a real failing condition created on
disk, or an ``ast`` walk over an in-memory COPY of a source file -- parsed,
never executed.

Run from terminal:
    python tests/test_storage_run_identity.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# The package bootstrap every file in tests/ carries; the candidate directory
# is the PARENT of this file's. `pip install -e .` makes it a no-op.
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

# No local model is reached here, and the flag is set before the agent is
# imported anyway: a stand-in forgotten in a future edit becomes a named
# RuntimeError instead of a 110 MB download.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

import ast
import contextlib
import hashlib
import io
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from oncotriage import config
from oncotriage import paths as _paths
from oncotriage import run_fingerprint as _rf
from oncotriage import tracking as _tracking
from oncotriage.batch import runner as _runner
from oncotriage.storage import database_logger as _dl


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


def fail(label, detail):
    """Record a failure that is not an equality comparison."""
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {detail}")
    print(f"  FAIL  {label}")
    print(f"          {detail}")


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


def guarded(fn, *args, **kwargs):
    """Call into production code, turning ANY raise into a value check() fails on.

    NOT DEFENSIVE PADDING. Nine files in this suite have shipped the same
    defect: a bare call inside a ``check(...)`` argument, where a planted or
    reverted defect raises, the exception escapes while the argument is being
    evaluated, and the run reports ONE TRACEBACK where it owed a summary and N
    results. Section 8 deliberately creates failing conditions, so every driver
    goes through here.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                       # noqa: BLE001 -- reported
        return {"__raised__": f"{type(exc).__name__}: {exc}"}


def silence(fn, *args, **kwargs):
    """Run fn with BOTH output channels captured; return its value.

    The writer announces every ALTER TABLE, every row and every run. Nothing
    suppressed is asserted on: every assertion below reads the DATABASE, a
    returned value or a counter.
    """
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        return guarded(fn, *args, **kwargs)


def loud(fn, *args, **kwargs):
    """silence(), but returning (value, captured_text) for output assertions."""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        value = guarded(fn, *args, **kwargs)
    return value, buf.getvalue()


def digest(path):
    """sha256 of a file, or a NAMED non-reading -- never a raise.

    'absent'          the path is not there
    'unreadable: X'   it is there and could not be read (a directory, a
                      permission, an I/O error)

    THE RAISE IS THE POINT OF THE SECOND CASE. This is called at module scope,
    before any check has run, so an OSError here turns a run that owes a
    summary and 130-odd results into one traceback -- the abort shape this
    project has shipped ten times, and which `guarded()` twenty lines up exists
    to prevent everywhere else. The marker travels into the probe below, which
    asks is_real_digest() and therefore reports it as a recorded failure.
    """
    if not os.path.exists(path):
        return "absent"
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        return f"unreadable: {type(exc).__name__}"


def reading_of(path):
    """``digest(path)``, turning ANY raise into a value ``check`` fails on.

    THE CONTROL BELOW CANNOT SURVIVE THE REVERT IT TESTS WITHOUT THIS, and the
    revert harness is what found it rather than reading. Strip the ``try`` out
    of ``digest`` and the very call inside that control's argument list
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
        return digest(path)
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
    asserts that ``_PRODUCTION_SHA_BEFORE`` is not the string ``'absent'``. A
    gate keyed on that same string would therefore be satisfied by exactly the
    fault the probe exists to catch -- a digest reading that comes back
    ``'absent'`` for a file that is really there, through a broken reader or a
    wrong path -- and the skip path would quietly become the only path.
    ``os.path.exists`` decides whether the probe runs; the digest decides what
    it reports.

    Pure, so its controls are different ARGUMENTS rather than a mutated file on
    disk -- which is what this file's own header already claims of every
    control in it.
    """
    return _PROBE_RUN if production_existed else _PROBE_SKIP


def production_probe_verdict(sha_before):
    """The ``(actual, expected)`` pair the probe hands to ``check``.

    ONE implementation, driven by the live call site and by every control, so a
    control cannot agree with a probe that has stopped checking.
    """
    return (not is_real_digest(sha_before), False)


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


def at(mapping, key, default="<absent>"):
    """mapping[key] or a NAMED absence -- never a KeyError inside a check()."""
    try:
        return mapping[key]
    except (KeyError, IndexError, TypeError):
        return default


def columns_of(db, table):
    """Column names of `table`, read read-only.

    A plain sqlite3.connect on an absent path CREATES the file, so a check
    written that way would bring its own subject into existence.
    """
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return f"unreadable: {exc}"


def tables_of(db):
    """Every table this project declared, sorted. SQLite's own are excluded."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return sorted(r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"))
    finally:
        conn.close()


class _Row(dict):
    """A result row whose MISSING key is a named absence, not a KeyError.

    THE FIX BELONGS HERE AND NOT AT THE TWELVE CALL SITES. Callers read columns
    as `r["patient_id"]`, and when a planted defect makes a query fail or return
    a different shape, every one of those subscripts raises -- aborting the file
    at the moment it owes a summary. Guarding `rows()` alone was not enough: it
    made the FETCH safe and left the DERIVED read raising one line later, which
    a revert harness measured twice.

    The absence NAMES the columns that did come back, so a failure reads as
    "this query returned {...}" rather than as a bare key name.
    """

    def __missing__(self, key):
        return f"<no column {key!r}; row has {sorted(self)}>"


def rows(db, sql, params=()):
    """Every row of `sql` as a list of dicts. Read-only. NEVER RAISES.

    THE GUARD IS NOT PADDING AND IT WAS ADDED FROM A MEASURED ABORT. `one()`
    below returns a NAMED ABSENCE dict when a query finds nothing, which is
    correct for a `check()` comparison and fatal when that value is then BOUND
    as a parameter to the next query -- sqlite3 raises `type 'dict' is not
    supported`. A revert harness that broke the `runs` migration produced
    exactly that: no run row was created, the absence dict was bound, and this
    file reported ONE TRACEBACK where it owed a summary and 133 results.

    A failure is returned as a single named row so it reaches a `check()` and
    is REPORTED, rather than either aborting or -- worse -- coming back as an
    empty list that reads like "the query ran and found nothing".
    """
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except BaseException as exc:                       # noqa: BLE001 -- reported
        return [{"__query_failed__": f"connect: {type(exc).__name__}: {exc}"}]
    conn.row_factory = sqlite3.Row
    try:
        return [_Row(r) for r in conn.execute(sql, params)]
    except BaseException as exc:                       # noqa: BLE001 -- reported
        return [_Row({"__query_failed__": f"{type(exc).__name__}: {exc}"})]
    finally:
        conn.close()


def one(db, sql, params=()):
    """The first row of `sql`, or a NAMED absence."""
    found = rows(db, sql, params)
    return found[0] if found else {"__no_row__": sql}


#------------------------------------------------------------------------------


# ===========================================================================
# ISOLATION, ESTABLISHED BEFORE ANYTHING IS WRITTEN
# ===========================================================================
#
# The production database's sha256 is taken HERE, at the top, before this file
# has opened anything -- and compared at the very bottom. Taking it after the
# first write would compare a changed file with itself.
#
# paths._RESOLVED IS SEEDED, which is the seam tests/test_ablation_db_isolation.py
# and tests/test_dashboard_reproducibility_tab.py already use. Two keys:
# `inferences_path`, so a call that resolves rather than being told cannot reach
# production, and `checkpoint_path`, because run_batch's append_result writes
# the results file there.
#
# ONCOTRIAGE_INFERENCES_DB IS EXPLICITLY CLEARED. It outranks paths.inferences_path
# at tier 2 of resolve_inference_db_path, so an operator with it exported would
# otherwise redirect this file's "production" reading to their own scratch
# database and every isolation assertion below would compare two scratch paths
# and pass for the wrong reason.

_ENV_WAS = os.environ.pop("ONCOTRIAGE_INFERENCES_DB", None)

_PRODUCTION_DB = _paths.inferences_path
_PRODUCTION_SHA_BEFORE = reading_of(_PRODUCTION_DB)
# Taken HERE, beside the sha and before this file has written anything, because
# the question the gate in section 10 answers is "did this machine have a
# production database for the byte-identity check to be exercised against" --
# not "is there one now". A run that CREATED one is caught by that check itself
# ('absent' != <hash>), which stays live and ungated in every environment.
_PRODUCTION_EXISTED_BEFORE = os.path.exists(_PRODUCTION_DB)

_TMP = tempfile.mkdtemp(prefix="oncotriage-run-identity-")
_SCRATCH_DB = os.path.join(_TMP, "inferences.db")
_CHECKPOINT_DIR = os.path.join(_TMP, "checkpoint")
os.makedirs(_CHECKPOINT_DIR, exist_ok=True)

_PATHS_HAD_INF = "inferences_path" in _paths._RESOLVED
_PATHS_WAS_INF = _paths._RESOLVED.get("inferences_path")
_PATHS_HAD_CP = "checkpoint_path" in _paths._RESOLVED
_PATHS_WAS_CP = _paths._RESOLVED.get("checkpoint_path")

_paths._RESOLVED["inferences_path"] = _SCRATCH_DB
_paths._RESOLVED["checkpoint_path"] = _CHECKPOINT_DIR + os.sep

# The two source files this file READS. Hashed now, compared at the end: nothing
# here writes into the repository, and saying so is cheaper than being believed.
_DL_SRC = os.path.abspath(_dl.__file__)
_RUNNER_SRC = os.path.abspath(_runner.__file__)
_DL_SHA_BEFORE = digest(_DL_SRC)
_RUNNER_SHA_BEFORE = digest(_RUNNER_SRC)


# A stamp shaped exactly like run_fingerprint.current() but built from literals,
# so no Qdrant round trip and no model load happens anywhere in this file. The
# KEYS come from the module, not from a retyped list -- a field added to the
# stamp appears here automatically and is then required to appear as a column.
_STAMP_VALUES = {
    "fingerprint_version": _rf.FINGERPRINT_VERSION,
    "llm_classifier_prompt_version": "9.9.9-test",
    "llm_classifier_renderer_digest": "d" * 64,
    "matching_model_configured": "test-model",
    # A REAL MEMBER of config.MATCHING_CALL_MODES rather than a placeholder:
    # this column is what campaign_summary stitches on and what a resume gate
    # refuses across, and a value outside the vocabulary would exercise neither.
    "matching_call_mode": "grouped",
    "qdrant_collection": "trial_criteria_test_0001",
    "collection_points": 12345,
    "data_snapshot_date": "2026-01-31",
    # REAL INTEGERS, not placeholders, and for `matching_call_mode`'s reason
    # one line up: both land in INTEGER-affinity columns through
    # RUN_FINGERPRINT_INTEGER_COLUMNS, which NULLs anything that is not a plain
    # int -- so `test-campaign_cohort_size` would have exercised the NULL arm
    # in every check below rather than the round trip they are about.
    "campaign_cohort_size": 300,
    "campaign_cohort_seed": 42,
    # A REAL 40-HEX GIT OBJECT ID, on the two integers' reason one line up and
    # on `matching_call_mode`'s: it is a TEXT column, so a generated
    # "test-cross_encoder_revision" would round-trip perfectly and prove
    # nothing about the shape the pipeline actually writes. This is not
    # config.CROSS_ENCODER_REVISION -- a stamp literal must not be read off the
    # module the round trip is checking, or the check agrees with the code by
    # construction.
    "cross_encoder_revision": "0123456789abcdef0123456789abcdef01234567",
    # AN INT, on `campaign_cohort_size`'s reason: the column is INTEGER and it
    # is in RUN_FINGERPRINT_INTEGER_COLUMNS, so a generated
    # "test-matching_per_trial_empty_retries" would exercise the NULL arm in
    # every check below rather than the round trip they are about. 1 rather
    # than a read of config.MATCHING_PER_TRIAL_EMPTY_RETRIES, on
    # `cross_encoder_revision`'s: a stamp literal read off the module the round
    # trip is checking agrees with the code by construction.
    "matching_per_trial_empty_retries": 1,
}
# A FIELD WITH NO LITERAL ABOVE GETS A GENERATED ONE RATHER THAN A KeyError, and
# that is a repair rather than a convenience. The comment above this dict has
# always claimed the keys come from the module so "a field added to the stamp
# appears here automatically" -- and it did not: the VALUES were hand-written, so
# the first field added to FINGERPRINT_FIELDS raised KeyError AT MODULE LEVEL and
# took the whole file with it, reporting one traceback where it owed 134 results.
# That is the abort shape this project has shipped ten times. The claim is true
# now, and the fallback is REPORTED below rather than silent, because a
# placeholder standing in for a field somebody meant to give a real value is
# exactly what a generated default would otherwise hide.
_STAMP_DEFAULTED = [k for k in _rf.FINGERPRINT_FIELDS if k not in _STAMP_VALUES]
_STAMP = {k: _STAMP_VALUES.get(k, f"test-{k}") for k in
          ("fingerprint_version",) + _rf.FINGERPRINT_FIELDS}


#------------------------------------------------------------------------------


print("=" * 78)
print("1. THE RESTATED VOCABULARIES ROUND-TRIP")
print("=" * 78)
print()

# WHY THIS IS THE FIRST SECTION. oncotriage/storage/database_logger.py restates
# two tuples it may not import: oncotriage.tracking and
# oncotriage.run_fingerprint both import oncotriage.agent.prompts (and
# run_fingerprint imports agent.readiness, which builds a Qdrant client), so a
# storage module importing either would put the AGENT layer -- and a network
# probe's import graph -- behind `import oncotriage.storage.database_logger`.
# That is the edge pass 20c-2c moved _resolve_primary_cancer out of that module
# to remove.
#
# A RESTATED CONSTANT IS A CONSTANT THAT CAN DRIFT. This file is the thing that
# stops it, and it can be, because a test is in nobody's import graph.

check("RUN_FINGERPRINT_COLUMNS is exactly the stamp's keys, in order",
      list(_dl.RUN_FINGERPRINT_COLUMNS),
      ["fingerprint_version"] + list(_rf.FINGERPRINT_FIELDS))

check("...and the stamp really has eleven gated fields (non-degenerate: a "
      "check against an empty tuple would pass for free). SIX until the "
      "call-mode pass gated `matching_call_mode`, SEVEN until the cohort pass "
      "gated `campaign_cohort_size` and `campaign_cohort_seed`, NINE until the "
      "environment-record pass gated `cross_encoder_revision`, TEN until the "
      "empty-verdict retry pass gated `matching_per_trial_empty_retries`",
      len(_rf.FINGERPRINT_FIELDS), 11)

check("every gated field has a real literal in this file's stamp, so no check "
      "below is exercising a generated placeholder",
      _STAMP_DEFAULTED, [])

check("`matching_call_mode` is gated, and its stamp value is a real member of "
      "the pipeline's own two-member vocabulary rather than a placeholder",
      ("matching_call_mode" in _rf.FINGERPRINT_FIELDS,
       _STAMP["matching_call_mode"] in config.MATCHING_CALL_MODES),
      (True, True))

# THE TWO VOCABULARIES ARE NO LONGER ONE FACT, AND THE CHECK SAYS SO IN THE
# SHAPE THAT STILL FAILS. Until the operator stop switch they were value-
# identical and this asserted exactly that, which is the right check for two
# restated copies of one thing. `runs` now has a terminal status MLflow does not
# -- STOPPED -- and MLflow's vocabulary is not this project's to widen
# (tracking.RUN_STATUSES says so).
#
# THE WEAK VERSION OF THIS EDIT WOULD BE TO DELETE THE CHECK, or to relax it to
# a superset test. Either would stop noticing the thing it was written for: a
# status added to ONE side by accident. What is asserted instead is the exact
# composition, IN ORDER --
#
#     RUN_RECORD_TERMINAL_STATUSES == tracking.RUN_STATUSES
#                                     + RUN_RECORD_STATUSES_BEYOND_TRACKING
#
# -- so a new status still fails unless it is named on the side that owns it,
# and the divergence is a declaration rather than a gap.
check("RUN_RECORD_TERMINAL_STATUSES is tracking.RUN_STATUSES plus the "
      "deliberately-declared extras, in that order",
      tuple(_dl.RUN_RECORD_TERMINAL_STATUSES),
      tuple(_tracking.RUN_STATUSES) + tuple(_dl.RUN_RECORD_STATUSES_BEYOND_TRACKING))

check("...and the extras really are extra -- nothing in them is already a "
      "tracking status, which would make the concatenation report a duplicate "
      "as a divergence (non-degeneracy: without this the check above passes "
      "for an extras tuple that repeats KILLED)",
      sorted(set(_dl.RUN_RECORD_STATUSES_BEYOND_TRACKING)
             & set(_tracking.RUN_STATUSES)), [])

check("...and the extras tuple is NON-EMPTY, so the composition above is "
      "actually exercising the concatenation rather than degenerating to the "
      "equality it replaced",
      len(_dl.RUN_RECORD_STATUSES_BEYOND_TRACKING) > 0, True)

check("STOPPED is the declared extra, is terminal, and is NOT a tracking "
      "status -- the three facts the batch runner's KILLED mapping rests on",
      (_dl.RUN_RECORD_STATUS_STOPPED,
       _dl.RUN_RECORD_STATUS_STOPPED in _dl.RUN_RECORD_TERMINAL_STATUSES,
       _dl.RUN_RECORD_STATUS_STOPPED in _tracking.RUN_STATUSES),
      ("STOPPED", True, False))

check("...and RUNNING is deliberately NOT among them -- finalizing a run to "
      "'still going' is the one thing the end of a run must not do",
      _dl.RUN_RECORD_STATUS_RUNNING in _dl.RUN_RECORD_TERMINAL_STATUSES, False)

check("RUN_RECORD_STATUSES is the terminal set plus RUNNING, and nothing else",
      sorted(_dl.RUN_RECORD_STATUSES),
      sorted(set(_dl.RUN_RECORD_TERMINAL_STATUSES) |
             {_dl.RUN_RECORD_STATUS_RUNNING}))

# THE ADDITIONS ARE PART OF THE EXPECTATION AND ARE DERIVED LIKE THE REST.
# `runs` gained its first ALTER-added column with `resumed`; the INSERT binds
# positionally against this tuple, so the ORDER is what matters -- base facts,
# then the stamp, then the additions in dict order, which is the order ALTER
# TABLE appends them in.
#
# ONE COLUMN CAN BE NAMED BY BOTH SOURCES AND MUST APPEAR ONCE.
# `matching_call_mode` is a stamp field AND an additive column, for the two
# orthogonal reasons argued at RUN_COLUMN_ADDITIONS, and a plain concatenation
# names it twice -- which is `OperationalError: duplicate column name` at the
# INSERT, on the first run of every campaign. The expectation is written out
# here as "base, the stamp columns the additions do not also name, then the
# additions" rather than by calling _dl._last_wins, because an expectation
# computed by the function under test agrees with it by construction.
_EXPECTED_RUN_COLUMNS = (
    ["started_at", "finished_at", "status", "invocation_source"]
    + [c for c in _dl.RUN_FINGERPRINT_COLUMNS
       if c not in _dl.RUN_COLUMN_ADDITIONS]
    + list(_dl.RUN_COLUMN_ADDITIONS))
check("RUN_COLUMNS is the four run facts, the stamp columns, then the "
      "additions -- with a column named by both appearing ONCE, where the "
      "ALTER put it",
      list(_dl.RUN_COLUMNS), _EXPECTED_RUN_COLUMNS)
check("...and it names no column twice, which is what the INSERT would raise "
      "on", len(_dl.RUN_COLUMNS), len(set(_dl.RUN_COLUMNS)))
check("...and the de-duplication is really doing work here (non-degeneracy: "
      "with no overlap the two rules give the same answer and the check above "
      "cannot distinguish them)",
      sorted(set(_dl.RUN_FINGERPRINT_COLUMNS) & set(_dl.RUN_COLUMN_ADDITIONS)),
      ["campaign_cohort_seed", "campaign_cohort_size", "cross_encoder_revision",
       "matching_call_mode", "matching_per_trial_empty_retries"])
# DERIVED, NOT `RUN_COLUMNS[-1] == "matching_call_mode"`. That form pinned the
# overlapping column to the LAST position, which is only where ALTER TABLE puts
# it for as long as it is the last entry in RUN_COLUMN_ADDITIONS -- so the
# first column added to that dict after it (era 5's `note`) made this check
# fail while the property it names was still perfectly true. The property is
# that the additions form the TAIL of the tuple in dict order, which is exactly
# what puts a column named by BOTH sources where the ALTER put it rather than
# at its stamp position; it holds for any number of additions.
check("...and the additions form the TAIL of the tuple in dict order, so a "
      "column named by both sources sits where ALTER TABLE appended it rather "
      "than at its stamp position",
      list(_dl.RUN_COLUMNS[-len(_dl.RUN_COLUMN_ADDITIONS):]),
      list(_dl.RUN_COLUMN_ADDITIONS))
check("...and the overlapping column really is inside that tail rather than "
      "before it (non-degeneracy: the line above holds trivially if no "
      "addition is also a stamp column)",
      "matching_call_mode" in _dl.RUN_COLUMNS[-len(_dl.RUN_COLUMN_ADDITIONS):],
      True)
check("...and the additions really contribute (non-degeneracy: with an empty "
      "dict the line above is the pre-additions check wearing a new label)",
      len(_dl.RUN_COLUMN_ADDITIONS) > 0, True)

check("the integer columns are named and are a SUBSET of the stamp columns",
      sorted(_dl.RUN_FINGERPRINT_INTEGER_COLUMNS),
      sorted(set(_dl.RUN_FINGERPRINT_INTEGER_COLUMNS) &
             set(_dl.RUN_FINGERPRINT_COLUMNS)))

# CONTROL 1: the round trip is not vacuous. Comparing against a stamp with one
# field removed must DISAGREE -- without this, a check written as
# `sorted(a) == sorted(a)` would look identical and pass forever.
_short = ("fingerprint_version",) + tuple(_rf.FINGERPRINT_FIELDS)[:-1]
check("CONTROL: a stamp missing one field no longer matches the column tuple",
      list(_dl.RUN_FINGERPRINT_COLUMNS) == list(_short), False)

# CONTROL 2: and neither does one with an extra field.
_long = ("fingerprint_version",) + tuple(_rf.FINGERPRINT_FIELDS) + ("invented",)
check("CONTROL: nor does one with a field the columns do not carry",
      list(_dl.RUN_FINGERPRINT_COLUMNS) == list(_long), False)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("2. THE MIGRATION IS ADDITIVE AND IDEMPOTENT")
print("=" * 78)
print()

# --- (a) a fresh database ---------------------------------------------------

_FRESH = os.path.join(_TMP, "fresh.db")
silence(_dl.initialize_database, _FRESH)

# FIVE AT THE HEALTH-PERSISTENCE PASS, which added `run_metrics`; SIX at the
# environment-record pass, which added `run_environment` -- the resolved package
# list, keyed by its own digest so one row serves every run that saw that
# environment. The set is kept EXACT rather than widened to a subset test, for
# the reason the bedrock adapter's copy of this assertion states: exact is what
# makes it fail when a table is introduced under any name, which is how this
# line came to be edited.
check("a fresh database carries all six tables",
      tables_of(_FRESH),
      ["drift_metrics", "inferences", "run_environment", "run_metrics", "runs",
       "trial_matches"])

check("...and `runs` carries exactly RUN_COLUMNS plus its id",
      sorted(columns_of(_FRESH, "runs")),
      sorted(set(_dl.RUN_COLUMNS) | {"id"}))

check("...and `inferences` carries run_id",
      "run_id" in columns_of(_FRESH, "inferences"), True)

# THE AFFINITY IS WHAT THIS PINS, and it is asked of the DECLARATION and of the
# REAL SCHEMA separately, because the two can disagree. The declaration gained a
# `REFERENCES runs(id)` clause in the database-completion pass -- documentation
# for a constraint that is deliberately never enforced, which is why the
# affinity below is unchanged by it. Pinning the whole string would make this
# check about the constraint text; pinning the affinity keeps it about what the
# column can HOLD, which is what every NULL-versus-0 rule in this file rests on.
check("run_id is declared with INTEGER affinity in INFERENCE_COLUMN_ADDITIONS",
      _dl.INFERENCE_COLUMN_ADDITIONS.get("run_id", "").split()[0], "INTEGER")
check("...and its declaration carries the (unenforced) reference, so a reader "
      "of the schema can see the relationship the four-reason ruling declines "
      "to have SQLite police",
      "REFERENCES runs(id)" in _dl.INFERENCE_COLUMN_ADDITIONS.get("run_id", ""),
      True)
_c_fk = sqlite3.connect(f"file:{_FRESH}?mode=ro", uri=True)
check("...and SQLite recorded it: PRAGMA foreign_key_list names `runs` for "
      "both inferences and run_metrics",
      sorted({r[2] for r in
              _c_fk.execute("PRAGMA foreign_key_list(inferences)")}
             | {r[2] for r in
                _c_fk.execute("PRAGMA foreign_key_list(run_metrics)")}),
      ["runs"])
check("...and it is STILL NOT ENFORCED, which is the ruling and not an "
      "oversight -- see the four reasons at the `runs` CREATE TABLE",
      _c_fk.execute("PRAGMA foreign_keys").fetchone()[0], 0)
_c_fk.close()

# The declared affinity of every runs column, read out of the real schema. This
# is what section 4's NULL rule rests on: `collection_points` has INTEGER
# affinity, so a TEXT value stored there would order above every real count.
_RUN_DECL = {}
_c = sqlite3.connect(f"file:{_FRESH}?mode=ro", uri=True)
_RUN_DECL = {r[1]: r[2] for r in _c.execute("PRAGMA table_info(runs)")}
_c.close()

check("every integer stamp column is declared INTEGER in the real schema",
      {c: _RUN_DECL.get(c) for c in sorted(_dl.RUN_FINGERPRINT_INTEGER_COLUMNS)},
      {c: "INTEGER" for c in sorted(_dl.RUN_FINGERPRINT_INTEGER_COLUMNS)})

check("...and every other stamp column is TEXT",
      {c: _RUN_DECL.get(c) for c in _dl.RUN_FINGERPRINT_COLUMNS
       if c not in _dl.RUN_FINGERPRINT_INTEGER_COLUMNS},
      {c: "TEXT" for c in _dl.RUN_FINGERPRINT_COLUMNS
       if c not in _dl.RUN_FINGERPRINT_INTEGER_COLUMNS})

# PRAGMA table_info's fourth field is `notnull`. Read once, into a dict, rather
# than re-opened per column.
_c = sqlite3.connect(f"file:{_FRESH}?mode=ro", uri=True)
_RUN_NOTNULL = {r[1]: bool(r[3]) for r in _c.execute("PRAGMA table_info(runs)")}
_c.close()

check("started_at, status and invocation_source are NOT NULL; finished_at is "
      "NULLABLE, which is the entire crashed-run shape",
      {c: _RUN_NOTNULL.get(c) for c in
       ("started_at", "finished_at", "status", "invocation_source")},
      {"started_at": True, "finished_at": False,
       "status": True, "invocation_source": True})

# --- (b) idempotence on an EXISTING scratch database ------------------------

_sql_before = sorted(r["sql"] for r in rows(
    _FRESH, "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"))
_cols_before = {t: columns_of(_FRESH, t) for t in tables_of(_FRESH)}

# The cache is cleared so initialize_database does the FULL work again rather
# than being short-circuited by _INITIALIZED_DATABASES -- otherwise "idempotent"
# would be measuring a set membership test.
_dl._INITIALIZED_DATABASES.discard(os.path.abspath(_FRESH))
_second, _second_out = loud(_dl.initialize_database, _FRESH)
_dl._INITIALIZED_DATABASES.discard(os.path.abspath(_FRESH))
_third = silence(_dl.initialize_database, _FRESH)

check("a second full initialize_database does not raise",
      isinstance(_second, str), True)
check("...and issues NO schema migration -- every ALTER is already applied",
      "Schema migration:" in _second_out, False)
check("...and the CREATE text in sqlite_master is byte-identical",
      sorted(r["sql"] for r in rows(
          _FRESH, "SELECT sql FROM sqlite_master WHERE type='table' "
                  "AND name NOT LIKE 'sqlite_%'")),
      _sql_before)
check("...and no column moved on any table",
      {t: columns_of(_FRESH, t) for t in tables_of(_FRESH)}, _cols_before)

# --- (c) a PRE-MIGRATION database -------------------------------------------
#
# Built by hand with the shape a database written before this pass has: an
# `inferences` table with no run_id, and no `runs` table at all. Two rows are
# seeded, and they are required to survive -- an "additive" migration that
# rebuilt the table would pass every column check above and silently discard
# every row anybody had.

_LEGACY = os.path.join(_TMP, "legacy.db")
_c = sqlite3.connect(_LEGACY)
_c.execute("CREATE TABLE inferences ("
           "id INTEGER PRIMARY KEY AUTOINCREMENT, "
           "patient_id TEXT NOT NULL, timestamp TEXT NOT NULL)")
_c.execute("CREATE TABLE trial_matches ("
           "id INTEGER PRIMARY KEY AUTOINCREMENT, "
           "inference_id INTEGER NOT NULL, nct_id TEXT NOT NULL)")
_c.executemany("INSERT INTO inferences (patient_id, timestamp) VALUES (?, ?)",
               [("legacy-a", "2026-01-01T00:00:00"),
                ("legacy-b", "2026-01-02T00:00:00")])
_c.commit()
_c.close()

check("PRE-CHECK: the legacy database has no `runs` table",
      "runs" in tables_of(_LEGACY), False)
check("PRE-CHECK: ...and no inferences.run_id",
      "run_id" in columns_of(_LEGACY, "inferences"), False)
check("PRE-CHECK: ...and it holds two rows to lose",
      at(one(_LEGACY, "SELECT COUNT(*) AS n FROM inferences"), "n"), 2)

silence(_dl.initialize_database, _LEGACY)

check("the migration CREATES `runs` on an existing database",
      "runs" in tables_of(_LEGACY), True)
check("...and ADDS inferences.run_id",
      "run_id" in columns_of(_LEGACY, "inferences"), True)
check("...and the legacy rows survive, in order",
      [r["patient_id"] for r in
       rows(_LEGACY, "SELECT patient_id FROM inferences ORDER BY id")],
      ["legacy-a", "legacy-b"])
check("...carrying NULL run_id, which is the honest value for a row written "
      "before there was a run to attach it to",
      [r["run_id"] for r in
       rows(_LEGACY, "SELECT run_id FROM inferences ORDER BY id")],
      [None, None])


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("3. THE RUN ROW IS CREATED, THEN FINALIZED")
print("=" * 78)
print()

_RUN_DB = os.path.join(_TMP, "runs.db")

check("PRE-CHECK: the scratch database is NOT the production one "
      "(non-degenerate isolation)",
      os.path.abspath(_RUN_DB) == os.path.abspath(_PRODUCTION_DB), False)
check("PRE-CHECK: ...and an unaimed resolve does not reach production either, "
      "because paths._RESOLVED is seeded",
      os.path.abspath(_dl.resolve_inference_db_path(None))
      == os.path.abspath(_PRODUCTION_DB), False)

_rid = silence(_dl.start_run_record, "test_source",
               db_path=_RUN_DB, fingerprint=_STAMP)

check("start_run_record returns an integer id", isinstance(_rid, int), True)

_row = one(_RUN_DB, "SELECT * FROM runs WHERE id = ?", (_rid,))
check("the row records the invocation source it was given",
      at(_row, "invocation_source"), "test_source")
check("...opens as RUNNING", at(_row, "status"), _dl.RUN_RECORD_STATUS_RUNNING)
check("...with a NULL finished_at", at(_row, "finished_at"), None)
check("...and a started_at that parses as an ISO timestamp",
      isinstance(at(_row, "started_at"), str)
      and len(at(_row, "started_at")) >= 19, True)

check("every stamp field landed in its own column, verbatim",
      {c: at(_row, c) for c in _dl.RUN_FINGERPRINT_COLUMNS}, dict(_STAMP))

check("...and collection_points came back as a NUMBER, not as text",
      isinstance(at(_row, "collection_points"), int), True)

# --- finalize ---------------------------------------------------------------

_ok = silence(_dl.finalize_run_record, _rid, "FINISHED", db_path=_RUN_DB)
check("finalize_run_record reports success", _ok, True)

_row = one(_RUN_DB, "SELECT * FROM runs WHERE id = ?", (_rid,))
check("...the status is the terminal one it was given",
      at(_row, "status"), "FINISHED")
check("...finished_at is no longer NULL",
      at(_row, "finished_at") is None, False)
check("...and started_at was NOT rewritten",
      at(_row, "started_at") <= at(_row, "finished_at"), True)
check("...and the stamp columns were not touched by the UPDATE",
      {c: at(_row, c) for c in _dl.RUN_FINGERPRINT_COLUMNS}, dict(_STAMP))

# --- an absent stamp ---------------------------------------------------------

_rid_nostamp = silence(_dl.start_run_record, "test_source", db_path=_RUN_DB)
_row_nostamp = one(_RUN_DB, "SELECT * FROM runs WHERE id = ?", (_rid_nostamp,))
check("a run opened with NO stamp leaves every stamp column NULL",
      {c: at(_row_nostamp, c) for c in _dl.RUN_FINGERPRINT_COLUMNS},
      {c: None for c in _dl.RUN_FINGERPRINT_COLUMNS})
check("...which is exactly what `fingerprint_version IS NULL` selects, and "
      "nothing else does",
      [r["id"] for r in rows(
          _RUN_DB, "SELECT id FROM runs WHERE fingerprint_version IS NULL")],
      [_rid_nostamp])

# --- invocation_source is required ------------------------------------------

for _bad, _label in ((None, "None"), ("", "an empty string"),
                     ("   ", "whitespace"), (7, "an integer")):
    _raised = guarded(_dl.start_run_record, _bad, db_path=_RUN_DB)
    check(f"invocation_source of {_label} is refused by name",
          isinstance(_raised, dict)
          and "ValueError" in str(at(_raised, "__raised__", "")), True)

check("...and nothing was written by any of those four refusals",
      at(one(_RUN_DB, "SELECT COUNT(*) AS n FROM runs"), "n"), 2)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("4. THE COERCION RULE: AN UNRESOLVED COUNT IS NULL, NEVER 'unknown'")
print("=" * 78)
print()

# WHY THIS IS NOT COSMETIC. run_fingerprint degrades an unresolvable field to
# the STRING "unknown". The five TEXT columns store that verbatim, which is
# right for them. Storing it in an INTEGER-affinity column is the ecog_date trap
# one column type over: SQLite keeps a non-numeric string as TEXT whatever the
# declared affinity, and orders EVERY text value above EVERY integer -- so
# `WHERE collection_points > 1000` would return the rows where the count could
# not be established, and ORDER BY DESC would rank them as the largest
# collections there are.

check("an int stays an int",
      _dl._run_fingerprint_value("collection_points", {"collection_points": 12067}),
      12067)
check("...including zero, which is a MEASUREMENT (an empty collection) and not "
      "an absence",
      _dl._run_fingerprint_value("collection_points", {"collection_points": 0}), 0)
check("UNKNOWN becomes NULL",
      _dl._run_fingerprint_value("collection_points",
                                 {"collection_points": _rf.UNKNOWN}), None)
check("...and so does any other non-int",
      _dl._run_fingerprint_value("collection_points",
                                 {"collection_points": "12067"}), None)
check("a bool becomes NULL, because isinstance(True, int) is True and a "
      "collection_points of 1 that was really a True is a number nobody measured",
      _dl._run_fingerprint_value("collection_points", {"collection_points": True}),
      None)
check("a TEXT field keeps its 'unknown' verbatim -- that column can hold it and "
      "it is the reader's evidence that the NULL beside it is a degradation, "
      "not a missing stamp",
      _dl._run_fingerprint_value("qdrant_collection",
                                 {"qdrant_collection": _rf.UNKNOWN}),
      _rf.UNKNOWN)
check("a stamp of None leaves the field NULL",
      _dl._run_fingerprint_value("qdrant_collection", None), None)
check("...and so does a stamp that simply omits the field",
      _dl._run_fingerprint_value("qdrant_collection", {}), None)

# Driven end to end, because a pure-function check cannot see a writer that
# bypasses the helper.
_degraded_stamp = dict(_STAMP)
_degraded_stamp["collection_points"] = _rf.UNKNOWN
_degraded_stamp["qdrant_collection"] = _rf.UNKNOWN
_rid_deg = silence(_dl.start_run_record, "test_source",
                   db_path=_RUN_DB, fingerprint=_degraded_stamp)
_row_deg = one(_RUN_DB, "SELECT * FROM runs WHERE id = ?", (_rid_deg,))
check("end to end: an unresolved count is stored as SQL NULL",
      at(_row_deg, "collection_points"), None)
check("...while the unresolved NAME is stored as the string it is",
      at(_row_deg, "qdrant_collection"), _rf.UNKNOWN)
check("...so the two questions are answered by two different predicates: "
      "'a stamp was recorded' is fingerprint_version IS NOT NULL",
      at(_row_deg, "fingerprint_version"), _rf.FINGERPRINT_VERSION)

check("CONTROL: the degraded row is NOT returned by a numeric comparison, "
      "which is the exact query the string 'unknown' would have poisoned",
      [r["id"] for r in rows(
          _RUN_DB, "SELECT id FROM runs WHERE collection_points > 1000")],
      [_rid])


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("5. run_id ON THE ROW: WRITTEN ON THE BATCH PATH, NULL ON A DIRECT CALL")
print("=" * 78)
print()

_LOG_DB = os.path.join(_TMP, "log.db")


def _result(pid):
    """A minimal terminal-node-shaped result dict. No pipeline, no model."""
    return {"patient_id": pid, "timestamp": "2026-08-21T12:00:00",
            "matches": [], "near_misses": [], "not_evaluable": [],
            "stage_timings": {}}


_PATIENT = {"demographics": {}, "conditions": [], "medications": [],
            "allergies": []}

_run_for_log = silence(_dl.start_run_record, _runner.INVOCATION_SOURCE,
                       db_path=_LOG_DB, fingerprint=_STAMP)

_w_batch = silence(_dl.log_inference, _result("batch-1"), _PATIENT,
                   db_path=_LOG_DB, run_id=_run_for_log)
_w_direct = silence(_dl.log_inference, _result("direct-1"), _PATIENT,
                    db_path=_LOG_DB)

check("both writes landed",
      [getattr(_w_batch, "ok", None), getattr(_w_direct, "ok", None)],
      [True, True])

check("the batch-path row carries the run id",
      at(one(_LOG_DB, "SELECT run_id FROM inferences WHERE patient_id='batch-1'"),
         "run_id"),
      _run_for_log)
check("the direct call's row carries NULL -- 'not part of a recorded batch run'",
      at(one(_LOG_DB, "SELECT run_id FROM inferences WHERE patient_id='direct-1'"),
         "run_id"),
      None)

check("the two are separable in SQL by the join, not by a timestamp window",
      [r["patient_id"] for r in rows(
          _LOG_DB, "SELECT i.patient_id FROM inferences i "
                   "JOIN runs r ON r.id = i.run_id WHERE r.id = ?",
          (_run_for_log,))],
      ["batch-1"])

check("...and the API/direct population is `run_id IS NULL`",
      [r["patient_id"] for r in rows(
          _LOG_DB, "SELECT patient_id FROM inferences WHERE run_id IS NULL")],
      ["direct-1"])

check("both rows share a timestamp, which is what makes the heuristic this "
      "replaces unable to separate them",
      len({r["timestamp"] for r in
           rows(_LOG_DB, "SELECT timestamp FROM inferences")}), 1)

# The default is a value, not a fallback: a caller that omits run_id gets NULL
# and never a looked-up "current run".
check("run_id defaults to None in log_inference's signature",
      _dl.log_inference.__defaults__[-1], None)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("6. A SECOND main() IN ONE PROCESS CREATES A DISTINCT RUN")
print("=" * 78)
print()

# BEHAVIOURAL HALF. main() itself cannot be driven here -- it builds a BM25
# index from a live Qdrant, compiles the graph and makes one billed Stage 5 call
# per patient -- so what is driven is the thing main() does once per invocation,
# and what is asserted structurally is that main() does exactly that and holds
# no state between calls.

_SECOND_DB = os.path.join(_TMP, "second.db")
_r1 = silence(_dl.start_run_record, "batch_runner",
              db_path=_SECOND_DB, fingerprint=_STAMP)
_r2 = silence(_dl.start_run_record, "batch_runner",
              db_path=_SECOND_DB, fingerprint=_STAMP)

check("two invocations produce two different ids", _r1 == _r2, False)
check("...and two rows", at(one(_SECOND_DB, "SELECT COUNT(*) AS n FROM runs"), "n"), 2)
check("...both RUNNING until finalized",
      sorted(r["status"] for r in rows(_SECOND_DB, "SELECT status FROM runs")),
      ["RUNNING", "RUNNING"])

silence(_dl.finalize_run_record, _r1, "FINISHED", db_path=_SECOND_DB)
check("finalizing the first leaves the second alone",
      sorted((r["id"], r["status"]) for r in
             rows(_SECOND_DB, "SELECT id, status FROM runs")),
      sorted([(_r1, "FINISHED"), (_r2, "RUNNING")]))

# STRUCTURAL HALF, over the SHIPPED source of runner.main. The property is that
# there is nothing to carry over: the id is a LOCAL, so a second main() cannot
# inherit the first one's. Compare clear_write_ledger() and
# run_fingerprint.clear_cache() at the top of that function -- both exist
# because their state IS module-level and both are one forgotten line away from
# describing the wrong run.

# READ ONCE, HERE, and used by every structural check and every plant below.
# The first draft read it inside the third control, so the fourth -- written
# later and placed earlier -- died on a NameError at module level and took the
# summary and every remaining check with it. That is the abort class this suite
# has now met ten times; the fix is the same one every time, which is to make
# the value exist before anything can want it.
_RUNNER_TXT = Path(_RUNNER_SRC).read_text(encoding="utf-8")
_RUNNER_TREE = ast.parse(_RUNNER_TXT)
_MAIN = next((n for n in _RUNNER_TREE.body
              if isinstance(n, ast.FunctionDef) and n.name == "main"), None)

if _MAIN is None:
    fail("runner.main was located for the structural checks",
         "no top-level `def main` in oncotriage/batch/runner.py")
else:
    def _calls_named(node, name):
        return [n for n in ast.walk(node)
                if isinstance(n, ast.Call)
                and ((isinstance(n.func, ast.Name) and n.func.id == name)
                     or (isinstance(n.func, ast.Attribute) and n.func.attr == name))]

    def _assign_targets(node):
        out = []
        for n in ast.walk(node):
            if isinstance(n, ast.Assign):
                for tgt in n.targets:
                    if isinstance(tgt, ast.Name):
                        out.append((tgt.id, n.value))
        return out

    _starts = _calls_named(_MAIN, "start_run_record")
    check("main() opens exactly one run row", len(_starts), 1)

    _targets = [name for name, value in _assign_targets(_MAIN)
                if isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "start_run_record"]
    check("...assigning it to exactly one LOCAL name", len(_targets), 1)

    _ID_NAME = _targets[0] if _targets else "<none>"

    # The name must not ALSO be a module-level global, which is the only way a
    # second main() could inherit the first one's run.
    _module_assigned = {t.id for n in _RUNNER_TREE.body
                        if isinstance(n, ast.Assign)
                        for t in n.targets if isinstance(t, ast.Name)}
    check(f"...and {_ID_NAME!r} is not assigned at module scope",
          _ID_NAME in _module_assigned, False)
    check("...and main() declares no `global` at all, so it cannot publish one",
          [n for n in ast.walk(_MAIN) if isinstance(n, ast.Global)], [])

    def _forwards(call_name):
        for call in _calls_named(_MAIN, call_name):
            for kw in call.keywords:
                if (kw.arg == "run_id" and isinstance(kw.value, ast.Name)
                        and kw.value.id == _ID_NAME):
                    return True
        return False

    check("main() forwards it to run_batch", _forwards("run_batch"), True)
    check("...and to run_resample", _forwards("run_resample"), True)

    _finals = _calls_named(_MAIN, "finalize_run_record")
    check("main() finalizes on more than one path",
          len(_finals) >= 2, True)

    # At least one finalize must live inside an exception handler: a run that
    # crashed must not be left RUNNING when a handler could have said KILLED.
    _in_handler = [f for h in ast.walk(_MAIN)
                   if isinstance(h, ast.ExceptHandler)
                   for f in _calls_named(h, "finalize_run_record")]
    # TWO, AND KNOWING WHICH TWO IS WHAT MAKES THE CONTROL BELOW HONEST: the
    # guard around tracking.start_run (which raises when tracking is
    # unavailable, at a point where the run row is already open and no other
    # handler exists yet) and the guard around the whole body (a crash, a
    # Ctrl-C, a SystemExit). Removing one leaves the other, so a control that
    # removed one and expected zero would be testing its own arithmetic.
    check("...and exactly two of them are inside an `except` handler",
          len(_in_handler), 2)

    # THE SUCCESS-PATH FINALIZE MUST BE THE LAST STATEMENT BEFORE THE RETURN,
    # and this is a correctness property rather than a style one. Every other
    # statement in that `try` can raise -- tracking_metrics walks the results
    # list, _results_path resolves a path, report_lines formats a snapshot --
    # and the handler finalizes to KILLED. With the finalize anywhere ABOVE
    # them, a raise in between overwrites a FINISHED row with KILLED and
    # reports a completed campaign as a crashed one. Being last makes the two
    # paths mutually exclusive by construction, which is stronger than a flag.
    def _try_with_return(fn):
        """The Try in fn whose body ends in a Return, or None."""
        for n in ast.walk(fn):
            if (isinstance(n, ast.Try) and n.body
                    and isinstance(n.body[-1], ast.Return)):
                return n
        return None

    def _finalize_is_last_before_return(fn):
        node = _try_with_return(fn)
        if node is None or len(node.body) < 2:
            return "<no try ending in a return>"
        prev = node.body[-2]
        if not (isinstance(prev, ast.Expr) and isinstance(prev.value, ast.Call)
                and isinstance(prev.value.func, ast.Name)):
            return f"<{type(prev).__name__}>"
        return prev.value.func.id

    check("main() finalizes the run row as the LAST statement before its "
          "return, so the crash handler can never overwrite a FINISHED row",
          _finalize_is_last_before_return(_MAIN), "finalize_run_record")

    # CONTROL 5a: moving that call one statement earlier must break the check.
    # An `ast` transformation on a COPY of the parsed tree -- nothing is written
    # and nothing is executed.
    _copy = ast.parse(_RUNNER_TXT)
    _cmain = next(n for n in _copy.body
                  if isinstance(n, ast.FunctionDef) and n.name == "main")
    _ctry = _try_with_return(_cmain)
    if _ctry is None or len(_ctry.body) < 3:
        fail("CONTROL: main()'s try was located for the reordering plant",
             "no try ending in a return, or too few statements to reorder")
    else:
        _ctry.body.insert(len(_ctry.body) - 2, _ctry.body.pop(len(_ctry.body) - 3))
        check("CONTROL: with one statement moved after it, the check fails",
              _finalize_is_last_before_return(_cmain) == "finalize_run_record",
              False)

    # EVERY STATUS main() CAN WRITE, RESOLVED THROUGH THE NAMES IT NOW USES.
    #
    # THIS CHECK USED TO READ `ast.Constant` ONLY, AND THAT STOPPED BEING
    # ENOUGH -- which is the check working rather than the check breaking.
    # runner.py wrote its four terminal statuses out as bare string literals in
    # three places, two of which derived the SAME verdict independently; they
    # are `RUN_RECORD_STATUS_*` imported from the storage layer now, so a
    # Constant-only walk finds nothing and this section would have passed
    # VACUOUSLY over a main() that writes no status at all.
    #
    # A NAME IS RESOLVED AGAINST THE STORAGE MODULE, NOT AGAINST A TABLE HERE.
    # `getattr(_dl, name)` is what makes "every status it can write is a
    # TERMINAL one" a statement about the vocabulary's OWNER; a local mapping
    # would be a third copy of the thing this pass removed two copies of.
    _LOCAL_STATUS_EXPRS = {"_terminal_status"}

    def _status_values(call):
        """The status strings this finalize call can write, or a marker."""
        out = []
        for a in call.args[1:2]:                       # the positional `status`
            if isinstance(a, ast.Constant):
                out.append(a.value)
            elif isinstance(a, ast.Name):
                if a.id in _LOCAL_STATUS_EXPRS:
                    # A local computed from the constants; its own arms are
                    # checked separately below.
                    out.append(f"<local {a.id}>")
                else:
                    out.append(getattr(_dl, a.id, f"<unresolved {a.id}>"))
        return out

    _statuses = sorted({s for f in _finals for s in _status_values(f)}, key=str)
    check("...and every status it can write is a TERMINAL one, or the local "
          "computed from them",
          [s for s in _statuses
           if s not in _dl.RUN_RECORD_TERMINAL_STATUSES
           and not str(s).startswith("<local ")], [])
    check("...including KILLED, which is the crash path's own verdict and is "
          "NOT the same finding as FAILED",
          _dl.RUN_RECORD_STATUS_KILLED in _statuses, True)

    # NON-DEGENERACY: the walk must actually have found statuses. Without this,
    # a resolver that returned nothing for everything satisfies the filter above
    # -- an empty list has no non-terminal member.
    check("...and the status walk is not empty (non-degeneracy)",
          len(_statuses) >= 2, True)

    # ------------------------------------------------------------------
    # F7: THE CONSOLE LINE AND THE ROW READ THE SAME LOCAL
    # ------------------------------------------------------------------
    #
    # THE DEFECT THIS PINS. main() derived the run's terminal status TWICE: once
    # for `finalize_run_record`, and once -- sixty lines earlier -- for the
    # console block that PRINTS what the row will say. That block's own text is
    # the promise ("run row FINISHED -- NOT STOPPED, because STOPPED means the
    # campaign covers a PREFIX of the cohort"), so a comment argued that the two
    # must agree while the code kept them in step by hand.
    #
    # THEY AGREED ONLY BY COINCIDENCE OF THE GUARD. The console copy sits under
    # `if STOP_SWITCH.requested and not _stopped_mid_cohort:`, which collapses
    # the STOPPED arm -- so the shorter two-way expression there was correct
    # because of WHERE IT SAT rather than because of what it computed, and any
    # edit to either branch had to be made twice or the console would state a
    # status the row does not carry.
    #
    # WHAT IS ASSERTED IS THE SHARED NAME, not the shape of either expression.
    _term_assigns = [n for n in ast.walk(_MAIN)
                     if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name)
                             and t.id == "_terminal_status" for t in n.targets)]
    check("main() derives the terminal status exactly ONCE",
          len(_term_assigns), 1)

    _term_names = sorted({n.id for a in _term_assigns for n in ast.walk(a)
                          if isinstance(n, ast.Name)
                          and n.id.startswith("RUN_RECORD_STATUS_")})
    check("...from the named constants, not from literals",
          _term_names, ["RUN_RECORD_STATUS_FAILED",
                        "RUN_RECORD_STATUS_FINISHED",
                        "RUN_RECORD_STATUS_STOPPED"])

    _row_reads = [f for f in _finals
                  if any(isinstance(a, ast.Name) and a.id == "_terminal_status"
                         for a in f.args)]
    check("the run row is finalized with that local", len(_row_reads), 1)

    # THE CONSOLE READS IT TOO. The call is located by the text it prints, so
    # this fails if the line is deleted as well as if it goes back to computing
    # its own answer.
    def _console_calls_mentioning(fragment):
        found = []
        for call in [n for n in ast.walk(_MAIN) if isinstance(n, ast.Call)]:
            fn = call.func
            if isinstance(fn, ast.Attribute) and fn.attr == "out":
                if fragment in ast.unparse(call):
                    found.append(call)
        return found

    _row_line = _console_calls_mentioning("run row        ")
    check("the console prints a `run row` line", len(_row_line) >= 1, True)
    _reads_local = [c for c in _row_line
                    if any(isinstance(n, ast.Name)
                           and n.id == "_terminal_status" for n in ast.walk(c))]
    # EVERY SUCH LINE READS IT, NOT "EXACTLY ONE OF THEM DOES". This was
    # `len(_reads_local) == 1`, which was a count of the `run row` lines that
    # existed rather than a statement about them -- so the spend-gate pass,
    # which added a SECOND stop path with its own closing block, made a check
    # that was satisfied report a failure. The property is that no `run row`
    # line computes its own status; a count cannot say that and this can, and
    # it survives the third stop path without another edit.
    check("...and EVERY `run row` line reads the SAME local the row does, so "
          "none of them computes its own status",
          (len(_reads_local), len(_reads_local) == len(_row_line)),
          (len(_row_line), True))

    # NO SECOND DERIVATION ANYWHERE IN main(). This is the check that would have
    # caught the original defect: the console copy was an IfExp over
    # `main_errors` yielding literal statuses, and it was not the one assignment
    # above.
    def _literal_status_ifexps(fn):
        return [n for n in ast.walk(fn)
                if isinstance(n, ast.IfExp)
                and any(isinstance(x, ast.Name) and x.id == "main_errors"
                        for x in ast.walk(n.test))
                and any(isinstance(x, ast.Constant)
                        and x.value in _dl.RUN_RECORD_TERMINAL_STATUSES
                        for x in ast.walk(n))]

    check("main() contains no second, literal-valued derivation of the "
          "terminal status", _literal_status_ifexps(_MAIN), [])

    # CONTROL 5b: that walk must FIRE against a copy that has one. An `ast` walk
    # over an in-memory copy; nothing is written and nothing is executed.
    _C5B_OLD = "\n".join([
        '                console.out("  run row        "',
        "                            + _terminal_status",
    ])
    _C5B_NEW = "\n".join([
        '                console.out("  run row        "',
        '                            + ("FINISHED" if not main_errors '
        'else "FAILED")',
    ])
    _c5b_src = _RUNNER_TXT.replace(_C5B_OLD, _C5B_NEW, 1)
    if _c5b_src == _RUNNER_TXT:
        fail("CONTROL 5b: the second-derivation plant matched something",
             "the console `run row` anchor was not found in runner.py")
    else:
        _c5b_main = next(n for n in ast.parse(_c5b_src).body
                         if isinstance(n, ast.FunctionDef) and n.name == "main")
        check("CONTROL 5b: with the console line deriving its own answer "
              "again, the check fires",
              len(_literal_status_ifexps(_c5b_main)) >= 1, True)

    # ------------------------------------------------------------------
    # F7: THE MLflow TRANSLATION IS A DECLARED MAPPING, NOT A THIRD COPY
    # ------------------------------------------------------------------
    #
    # `tracking.end_run`'s status was a THIRD independent derivation of the same
    # three-way conditional, under a comment declaring a "divergence" from the
    # row -- which made that divergence a property of two expressions that
    # happened to differ in one arm rather than a mapping anybody had written
    # down. It is `TRACKING_STATUS_FOR[_terminal_status]` now.
    check("TRACKING_STATUS_FOR maps every terminal status and nothing else",
          sorted(_runner.TRACKING_STATUS_FOR),
          sorted(_dl.RUN_RECORD_TERMINAL_STATUSES))
    check("...onto MLflow's vocabulary and nothing outside it",
          [v for v in _runner.TRACKING_STATUS_FOR.values()
           if v not in _tracking.RUN_STATUSES], [])
    check("...with STOPPED -> KILLED, the one row that is not an identity",
          _runner.TRACKING_STATUS_FOR[_dl.RUN_RECORD_STATUS_STOPPED], "KILLED")
    check("...and it is NOT invertible, which is why the ROW and not the index "
          "is the authority on how a campaign ended",
          len(set(_runner.TRACKING_STATUS_FOR.values()))
          < len(_runner.TRACKING_STATUS_FOR), True)

    _end_run_calls = [n for n in ast.walk(_MAIN)
                      if isinstance(n, ast.Call)
                      and isinstance(n.func, ast.Attribute)
                      and n.func.attr == "end_run"]
    _translated = [c for c in _end_run_calls
                   for kw in c.keywords
                   if kw.arg == "status"
                   and any(isinstance(n, ast.Subscript)
                           and isinstance(n.value, ast.Name)
                           and n.value.id == "TRACKING_STATUS_FOR"
                           for n in ast.walk(kw.value))]
    check("the success-path tracking status is TRANSLATED from the row's, not "
          "re-derived", len(_translated), 1)

    # CONTROL 3: the forwarding check is not vacuous -- it must FAIL against a
    # copy with the keyword removed. An `ast` walk over an in-memory copy;
    # nothing is exec'd and nothing on disk is touched.
    _txt = _RUNNER_TXT
    _planted = _txt.replace("                results_list=results_list,\n"
                            "                run_id=_run_record_id,\n",
                            "                results_list=results_list,\n", 1)
    if _planted == _txt:
        fail("CONTROL: the run_batch forwarding plant matched something",
             "the anchor text was not found in oncotriage/batch/runner.py")
    else:
        _pt = ast.parse(_planted)
        _pmain = next(n for n in _pt.body
                      if isinstance(n, ast.FunctionDef) and n.name == "main")
        _found = False
        for call in [n for n in ast.walk(_pmain)
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                     and n.func.id == "run_batch"]:
            for kw in call.keywords:
                if kw.arg == "run_id":
                    _found = True
        check("CONTROL: with the keyword removed, the forwarding check fails",
              _found, False)

    # CONTROL 4: the except-handler check must fail when BOTH handler-side
    # finalizes are removed. Both, because there are two and the check asks
    # whether ANY handler finalizes -- a plant that removed one would leave the
    # property true and prove nothing.
    # THE ANCHOR IS THE CALL AS IT IS NOW WRITTEN -- two lines, with the status
    # as a NAMED CONSTANT rather than a literal. It was a one-line call with
    # `"KILLED"` typed into it; when runner.py stopped writing bare literals
    # this plant matched nothing and the control reported a working check as
    # broken, which is exactly what the match-count assertion below exists to
    # turn into a named failure instead of a silent one.
    _KILL_CALL = "\n".join([
        "            finalize_run_record(_run_record_id, "
        "RUN_RECORD_STATUS_KILLED,",
        "                                db_path=_reconcile_db)",
    ])
    _planted2 = _txt.replace(
        _KILL_CALL + '\n            tracking.end_run(status="FAILED")\n',
        '            tracking.end_run(status="FAILED")\n', 1)
    _planted2 = _planted2.replace(
        _KILL_CALL + "\n            raise\n",
        "            raise\n", 1)
    # THE PLANT ASSERTS ITS OWN MATCH COUNT. A plant that matched nothing
    # produces a "control" that agrees with the shipped code and reports a
    # working check as broken -- the failure mode this project has met before
    # and writes down each time.
    _removed = _txt.count(_KILL_CALL) - _planted2.count(_KILL_CALL)
    if _removed != 2:
        fail("CONTROL: the crash-path plant removed both handler calls",
             f"removed {_removed}, expected 2 -- an anchor was not found in "
             f"oncotriage/batch/runner.py, so this control would have reported "
             f"a working check as broken")
    else:
        _pt2 = ast.parse(_planted2)
        _pmain2 = next(n for n in _pt2.body
                       if isinstance(n, ast.FunctionDef) and n.name == "main")
        _handler_calls = [n for h in ast.walk(_pmain2)
                          if isinstance(h, ast.ExceptHandler)
                          for n in ast.walk(h)
                          if isinstance(n, ast.Call)
                          and isinstance(n.func, ast.Name)
                          and n.func.id == "finalize_run_record"]
        check("CONTROL: with the crash-path finalize removed, no `except` "
              "handler finalizes",
              len(_handler_calls), 0)

# process_patient must forward its argument to the writer rather than reading a
# global -- checked structurally, and driven for real in section 7.
_PP = next((n for n in _RUNNER_TREE.body
            if isinstance(n, ast.FunctionDef) and n.name == "process_patient"), None)
if _PP is None:
    fail("runner.process_patient was located", "no top-level def")
else:
    _li = [n for n in ast.walk(_PP) if isinstance(n, ast.Call)
           and isinstance(n.func, ast.Name) and n.func.id == "log_inference"]
    check("process_patient calls log_inference exactly once", len(_li), 1)
    check("...passing run_id through as its own parameter",
          [kw.value.id for c in _li for kw in c.keywords
           if kw.arg == "run_id" and isinstance(kw.value, ast.Name)],
          ["run_id"])

# THERE IS NO MODULE-LEVEL "CURRENT RUN" ANYWHERE. A scan of both modules for a
# global whose name suggests one, because the mechanism this pass relies on is
# precisely its absence.
for _mod, _src in (("runner", _RUNNER_SRC), ("database_logger", _DL_SRC)):
    _tree = ast.parse(Path(_src).read_text(encoding="utf-8"))
    _globals_assigned = {t.id for n in _tree.body if isinstance(n, ast.Assign)
                         for t in n.targets if isinstance(t, ast.Name)}
    _suspect = sorted(g for g in _globals_assigned
                      if "current_run" in g.lower()
                      or g.lower() in ("_run_id", "run_id", "_active_run",
                                       "_current_run_id"))
    check(f"{_mod} holds no module-level current-run state", _suspect, [])


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("7. run_batch AND run_resample FORWARD THE ID TO EVERY WORKER")
print("=" * 78)
print()

# DRIVEN THROUGH THE SHIPPED FUNCTIONS, with process_patient replaced by a
# recording stand-in. That is the seam the threading actually crosses -- an
# executor.submit whose keyword was dropped is invisible to any check on the
# function it submits.
#
# THE STAND-IN RETURNS status="error" DELIBERATELY. A "success" entry makes
# _on_done call save_checkpoint(), which with no fingerprint argument resolves
# run_fingerprint.current() -- a live Qdrant round trip. This file is offline,
# and a test that quietly acquires a network dependency is a test that stops
# running in CI.

_SEEN = []


def _recording_process_patient(fhir_path=None, graph=None, is_resample=False,
                              run_id=None, db_path=None):
    # `db_path` IS AN EXPLICIT PARAMETER, NOT **kwargs, and it is recorded.
    # The path-unification pass added it to the real process_patient, and this
    # stand-in's fixed signature is what turned that into a loud failure here
    # rather than a silent divergence -- which is the property worth keeping, so
    # the parameter is named rather than absorbed. Recording it is what lets
    # section 11 assert that every worker was handed the SAME destination.
    _SEEN.append({"stem": Path(fhir_path).stem, "is_resample": is_resample,
                  "run_id": run_id, "db_path": db_path})
    return {"patient_id": Path(fhir_path).stem, "status": "error",
            "eligible_matches": 0, "near_misses": 0, "not_evaluable": 0,
            "total_time": 0.01, "timestamp": "2026-08-21T12:00:00",
            "error": "stand-in", "is_resample": is_resample}


_FHIR_DIR = os.path.join(_TMP, "fhir")
os.makedirs(_FHIR_DIR, exist_ok=True)
_FILES = []
for _i in range(4):
    _p = os.path.join(_FHIR_DIR, f"patient-{_i}.json")
    Path(_p).write_text("{}")
    _FILES.append(_p)

_REAL_PP = _runner.process_patient
try:
    _runner.process_patient = _recording_process_patient

    _RUN_ID_UNDER_TEST = 4242
    _results = []
    silence(_runner.run_batch, fhir_files=_FILES, bm25_index=None, nct_ids=[],
            graph=None, completed_ids=set(), results_list=_results,
            run_id=_RUN_ID_UNDER_TEST)

    check("run_batch reached every pending patient (non-degenerate: an empty "
          "pass would satisfy every assertion below)",
          sorted(s["stem"] for s in _SEEN),
          sorted(Path(f).stem for f in _FILES))
    check("...and every worker was handed the run id",
          sorted({s["run_id"] for s in _SEEN}), [_RUN_ID_UNDER_TEST])

    _SEEN.clear()
    silence(_runner.run_resample, fhir_files=_FILES,
            completed_ids={Path(f).stem for f in _FILES},
            bm25_index=None, nct_ids=[], graph=None, results_list=_results,
            run_id=_RUN_ID_UNDER_TEST)

    check("the resample pass reached at least one patient (non-degenerate)",
          len(_SEEN) >= 1, True)
    check("...and it too carries the SAME run id -- a resample re-run is a "
          "second row of one campaign, not a second campaign",
          sorted({s["run_id"] for s in _SEEN}), [_RUN_ID_UNDER_TEST])
    check("...and is marked as a resample, so the two are still separable",
          sorted({s["is_resample"] for s in _SEEN}), [True])

    # CONTROL 5: the recorder can see an absent id, so the two checks above are
    # not satisfied by any value at all.
    _SEEN.clear()
    silence(_runner.run_batch, fhir_files=_FILES, bm25_index=None, nct_ids=[],
            graph=None, completed_ids=set(), results_list=_results)
    check("CONTROL: run_batch called with no run id forwards None, and the "
          "recorder sees the difference",
          sorted({s["run_id"] for s in _SEEN}), [None])
finally:
    _runner.process_patient = _REAL_PP

check("the real process_patient was restored",
      _runner.process_patient is _REAL_PP, True)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("8. FINALIZATION NEVER RAISES")
print("=" * 78)
print()

# IT RUNS AFTER THE MONEY IS SPENT. By the time main() reaches it the campaign
# has made one live Stage 5 call per patient and written its rows, and an index
# failure must not take those with it. Every condition below is created FOR
# REAL -- no source is patched and nothing is exec'd.

_dl.RUN_RECORD_FAILURES.clear()

# --- (a) no id --------------------------------------------------------------
_r = silence(_dl.finalize_run_record, None, "FINISHED", db_path=_RUN_DB)
check("finalize with no run id returns False and does not raise", _r, False)
check("...and is counted",
      _dl.RUN_RECORD_FAILURES.get("finalize:no_run_id"), 1)

# --- (b) an unknown status --------------------------------------------------
_rid_b = silence(_dl.start_run_record, "test_source", db_path=_RUN_DB)
_r = silence(_dl.finalize_run_record, _rid_b, "SPLENDID", db_path=_RUN_DB)
check("an unrecognised status does not raise", _r, True)
check("...and is replaced by FAILED, never by FINISHED -- a run whose ending "
      "could not be described is not a run that ended well",
      at(one(_RUN_DB, "SELECT status FROM runs WHERE id = ?", (_rid_b,)),
         "status"),
      "FAILED")
check("...and is counted, naming the value",
      _dl.RUN_RECORD_FAILURES.get("finalize:unknown_status:SPLENDID"), 1)

_rid_b2 = silence(_dl.start_run_record, "test_source", db_path=_RUN_DB)
_r = silence(_dl.finalize_run_record, _rid_b2,
             _dl.RUN_RECORD_STATUS_RUNNING, db_path=_RUN_DB)
check("RUNNING is unrecognised HERE even though it is a member of "
      "RUN_RECORD_STATUSES, and also becomes FAILED",
      at(one(_RUN_DB, "SELECT status FROM runs WHERE id = ?", (_rid_b2,)),
         "status"),
      "FAILED")

# --- (c) a row that is not there --------------------------------------------
#
# `UPDATE ... WHERE id = ?` against a missing id SUCCEEDS and updates nothing;
# SQLite reports no error for it. Reading rowcount is the entire mechanism.
_r = silence(_dl.finalize_run_record, 999_999, "FINISHED", db_path=_RUN_DB)
check("finalizing a row that is not there returns False", _r, False)
check("...and is counted",
      _dl.RUN_RECORD_FAILURES.get("finalize:row_not_found"), 1)

# --- (d) a database that cannot be opened -----------------------------------
#
# A DIRECTORY where the file should be. Real, unpatched, and sqlite3 answers it
# with an OperationalError out of connect().
_UNOPENABLE = os.path.join(_TMP, "not-a-database")
os.makedirs(_UNOPENABLE, exist_ok=True)
_r, _out = loud(_dl.finalize_run_record, 1, "FINISHED", db_path=_UNOPENABLE)
check("finalizing against an unopenable database returns False, and does not "
      "raise into a caller that has already spent the money", _r, False)
check("...and is counted under the exception type",
      _dl.RUN_RECORD_FAILURES.get("finalize:OperationalError"), 1)
check("...and says so on the console rather than failing silently",
      "could not be finalized" in _out, True)

check("CONTROL: the counter is not simply always moving -- a successful "
      "finalize adds nothing to it",
      (lambda before: (silence(_dl.finalize_run_record,
                               silence(_dl.start_run_record, "test_source",
                                       db_path=_RUN_DB),
                               "FINISHED", db_path=_RUN_DB),
                       sum(_dl.RUN_RECORD_FAILURES.values()) == before)[1]
       )(sum(_dl.RUN_RECORD_FAILURES.values())),
      True)

check("RUN_RECORD_FAILURES has no `start:` key, because start_run_record RAISES "
      "rather than counting and continuing",
      sorted(k for k in _dl.RUN_RECORD_FAILURES if k.startswith("start")), [])

check("the counter is on the run-end degradation report",
      "RUN_RECORD_FAILURES" in __import__(
          "oncotriage.degradation", fromlist=["x"]).registered_names(),
      True)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("9. THE CRASHED-RUN SHAPE IS DISTINGUISHABLE IN SQL")
print("=" * 78)
print()

_CRASH_DB = os.path.join(_TMP, "crash.db")

_crashed = silence(_dl.start_run_record, "batch_runner",
                   db_path=_CRASH_DB, fingerprint=_STAMP)
_clean = silence(_dl.start_run_record, "batch_runner",
                 db_path=_CRASH_DB, fingerprint=_STAMP)
_killed = silence(_dl.start_run_record, "batch_runner",
                  db_path=_CRASH_DB, fingerprint=_STAMP)

silence(_dl.finalize_run_record, _clean, "FINISHED", db_path=_CRASH_DB)
silence(_dl.finalize_run_record, _killed, "KILLED", db_path=_CRASH_DB)
# _crashed is deliberately never finalized: that is the SIGKILL / power-loss
# shape, where no handler ran at all.

_UNFINISHED_SQL = ("SELECT id FROM runs WHERE finished_at IS NULL "
                   "AND status = 'RUNNING' ORDER BY id")

check("the never-finalized run is selected by the crashed-run query",
      [r["id"] for r in rows(_CRASH_DB, _UNFINISHED_SQL)], [_crashed])

check("...and the run that CRASHED BUT RAN ITS HANDLER is a different finding, "
      "carrying KILLED and a real finished_at",
      [(r["status"], r["finished_at"] is not None) for r in
       rows(_CRASH_DB, "SELECT status, finished_at FROM runs WHERE id = ?",
            (_killed,))],
      [("KILLED", True)])

check("...and the clean run is neither",
      at(one(_CRASH_DB, "SELECT status FROM runs WHERE id = ?", (_clean,)),
         "status"),
      "FINISHED")

# CONTROL 6: the query stops matching once the row is finalized, so it is
# selecting the STATE and not simply the oldest row.
silence(_dl.finalize_run_record, _crashed, "FINISHED", db_path=_CRASH_DB)
check("CONTROL: once finalized, the same query matches nothing",
      [r["id"] for r in rows(_CRASH_DB, _UNFINISHED_SQL)], [])

# A crashed run's PATIENTS are still attributable, which is the point of writing
# the row first.
_orphan = silence(_dl.start_run_record, "batch_runner",
                  db_path=_CRASH_DB, fingerprint=_STAMP)
silence(_dl.log_inference, _result("mid-crash"), _PATIENT,
        db_path=_CRASH_DB, run_id=_orphan)
check("rows written by a run that never finished are still joinable to it",
      [r["patient_id"] for r in rows(
          _CRASH_DB, "SELECT i.patient_id FROM inferences i "
                     "JOIN runs r ON r.id = i.run_id "
                     "WHERE r.finished_at IS NULL")],
      ["mid-crash"])


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("10. NOTHING OUTSIDE THE SCRATCH DIRECTORY WAS TOUCHED")
print("=" * 78)
print()

# Restore the seams before the final comparison, so the production reading below
# is taken with the module in the state every other test will find it in.
if _PATHS_HAD_INF:
    _paths._RESOLVED["inferences_path"] = _PATHS_WAS_INF
else:
    _paths._RESOLVED.pop("inferences_path", None)
if _PATHS_HAD_CP:
    _paths._RESOLVED["checkpoint_path"] = _PATHS_WAS_CP
else:
    _paths._RESOLVED.pop("checkpoint_path", None)
if _ENV_WAS is not None:
    os.environ["ONCOTRIAGE_INFERENCES_DB"] = _ENV_WAS

check("the production database is byte-identical",
      digest(_PRODUCTION_DB), _PRODUCTION_SHA_BEFORE)

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
#                            126 checks, the only standing coverage of the
#                            `runs` table, the run_id thread and the two
#                            finalization paths -- from CI to preserve one.
#   gate the probe alone     keeps all of them, and loses one probe on a
#                            machine where that probe HAS NO SUBJECT. With no
#                            production database, "that comparison is not
#                            'absent' == 'absent'" is not a weaker question; it
#                            is a question about a file that does not exist.
#                            That is what `skip` means here, and what it must
#                            never be allowed to mean is "the check was
#                            inconvenient".
#
# NOTHING THE PROBE ASSERTS IS WEAKENED. Where a production database exists the
# probe runs unchanged, against the same sha, with the same expectation. The
# byte-identity check above is never gated, so the dangerous CI case -- a stray
# writer CREATING the production database, before='absent' and after=<hash> --
# still fails there.
_STANDIN = os.path.join(_TMP, "production-probe-standin.bin")
Path(_STANDIN).write_bytes(b"not a database: a file that exists")
_STANDIN_SHA = digest(_STANDIN)

check("control: a file that exists digests to a real sha256 rather than "
      "'absent' (non-degeneracy: every control below would be vacuous "
      "otherwise)", len(_STANDIN_SHA), 64)
check("control: with no production database on disk the probe is SKIPPED",
      production_probe_disposition(False), _PROBE_SKIP)
check("control: with a production database on disk the probe is RUN",
      production_probe_disposition(os.path.exists(_STANDIN)), _PROBE_RUN)

_HONEST_ACTUAL, _HONEST_EXPECTED = production_probe_verdict(_STANDIN_SHA)
check("control: RUN plus an honest reading of a file that exists -- the probe "
      "passes", _HONEST_ACTUAL == _HONEST_EXPECTED, True)

# THE FIRING CONTROL, and requirement 3 of this pass. The plant is the state
# the gate must not be able to absorb: the file IS there (so the gate says RUN)
# and the sha reading claims 'absent'. The probe must report a FAILURE, or the
# skip path has quietly become the only path.
for _sentinel in ("absent", "unreadable: IsADirectoryError"):
    _PLANTED_ACTUAL, _PLANTED_EXPECTED = production_probe_verdict(_sentinel)
    check(f"control: RUN plus a present file read as {_sentinel!r} -- the probe "
          f"FIRES, so a non-reading cannot pass as a skip",
          _PLANTED_ACTUAL == _PLANTED_EXPECTED, False)

# A DIRECTORY, not a chmod: `chmod 000` is bypassed by root, so a control built
# on it passes vacuously on any runner that runs as root. Path.read_bytes on a
# directory raises IsADirectoryError -- an OSError -- for every user there is.
check("control: an existing path that cannot be READ digests to a named marker "
      "rather than raising (a raise here aborts the file before its first "
      "check)", reading_of(_TMP).startswith("unreadable: "), True)
check("control: ...and that marker is not mistaken for a reading",
      is_real_digest(reading_of(_TMP)), False)

_GATE_SITES = gate_call_sites(os.path.abspath(__file__))
check("control: skip() writes ONLY the skipped counter -- a skip that "
      "increments passed would report unavailable coverage as coverage",
      skip_accounting_keys(os.path.abspath(__file__)), ["skipped"])
check("control: exactly one gated call site is present (non-degeneracy -- a "
      "walk that matched nothing would satisfy the two assertions below for "
      "free)", len(_GATE_SITES), 1)
check("control: the gate is decided by the EXISTENCE reading",
      "_PRODUCTION_EXISTED_BEFORE" in at(_GATE_SITES, 0, set()), True)
check("control: ...and NOT by the sha reading the probe itself asserts on -- a "
      "gate keyed on that string is satisfied by the exact fault the probe "
      "catches",
      "_PRODUCTION_SHA_BEFORE" in at(_GATE_SITES, 0, set()), False)

_PROBE_LABEL = ("...and it was READABLE, so that comparison is not two "
                "sentinels -- 'absent' == 'absent' or "
                "'unreadable' == 'unreadable'")
if production_probe_disposition(_PRODUCTION_EXISTED_BEFORE) == _PROBE_RUN:
    check(_PROBE_LABEL, *production_probe_verdict(_PRODUCTION_SHA_BEFORE))
else:
    skip(_PROBE_LABEL,
         f"no production database at {_PRODUCTION_DB}, so the byte-identity "
         f"check above had nothing to be exercised against. That check stayed "
         f"LIVE and would still have caught this run creating one. Expected on "
         f"a CI runner: provision_ci_paths.py creates the parent directory and "
         f"deliberately not the file.")
check("oncotriage/storage/database_logger.py is byte-identical",
      digest(_DL_SRC), _DL_SHA_BEFORE)
check("oncotriage/batch/runner.py is byte-identical",
      digest(_RUNNER_SRC), _RUNNER_SHA_BEFORE)
check("every database this file wrote is inside the scratch directory",
      sorted({p for p in (_FRESH, _LEGACY, _RUN_DB, _LOG_DB, _SECOND_DB,
                          _CRASH_DB, _SCRATCH_DB)
              if not os.path.abspath(p).startswith(os.path.abspath(_TMP))}),
      [])

# The scratch paths are dropped from the writer's initialized-database cache so
# a later import in the same process cannot believe a deleted file is migrated.
for _p in (_FRESH, _LEGACY, _RUN_DB, _LOG_DB, _SECOND_DB, _CRASH_DB,
           _SCRATCH_DB):
    _dl._INITIALIZED_DATABASES.discard(os.path.abspath(_p))

shutil.rmtree(_TMP, ignore_errors=True)
check("the scratch directory was removed", os.path.exists(_TMP), False)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("SUMMARY")
print("=" * 78)
print(f"  passed: {_RESULTS['passed']}")
print(f"  failed: {_RESULTS['failed']}")
# PRINTED EVEN AT ZERO. A skip count that appears only when it is non-zero is
# indistinguishable from a file that has no skip mechanism at all.
print(f"  skipped: {_RESULTS['skipped']}   (a skip is NOT a pass and is not "
      f"counted as one)")
if _SKIPS:
    print()
    print("SKIPPED:")
    for _s in _SKIPS:
        print(f"  - {_s}")
if _FAILURES:
    print()
    print("FAILURES:")
    for _f in _FAILURES:
        print(f"  - {_f}")
print("=" * 78)


if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Aug 21 2026

@author: ramyalsaffar
"""
