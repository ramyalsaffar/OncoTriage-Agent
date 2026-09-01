# Database Query Layer Test
###########################

"""
Database Query Layer Test  (item 38)

WHY THIS FILE COULD NOT HAVE EXISTED BEFORE
-------------------------------------------
"16- Database Query.py" was 915 lines of top-level statements, so there was no
way to run one query, no way to get a frame back instead of a printed table, and
no way to run any of it against anything but the production database. Pass 20c-3b
turned the queries into a registry with ``run()`` / ``run_all()`` / ``report()``,
all of which return data and none of which resolve a path unless asked. That is
what makes the checks below possible; item 38 is the first pass to use it.

It also matters that the sweep could not previously have COMPLETED. File 16's
Query 19 selected two columns that do not exist in `inferences`, raised
"no such column", and took the process with it -- so no query after it in the
registry had ever executed, in any invocation of File 16, ever. The first check
in section 2 is therefore not a formality: it is the first time every query in
this project has been run.

WHAT ITEM 38 CHANGED, and what each section here holds it to
-----------------------------------------------------------
  1. ``expansion_token_efficiency`` DELETED, not repaired. Stage 1 is
     deterministic and issues no LLM call, so there are no expansion tokens; the
     answerable version of the question is already ``expansion_stage_stats``.
  2. ``pipeline_consistency`` repaired: the stray WHEN removed (proved to be a
     duplicate of the one inside the CASE, so the logic is unchanged), the two
     hardcoded pipeline sizes resolved from ``oncotriage/config.py``, `!=`
     replaced by `>` because both numbers are CAPS, the count identity corrected
     to include ``not_evaluable_trials``, and a row whose counters are NULL
     flagged instead of silently reported clean.
  3. The per-model cost arithmetic reduced to ONE copy,
     ``queries.price_model_groups``, fed by the SQL GROUP BY on one side and by
     ``oncotriage/dashboard/tabs/cost_tokens.py`` on the other. Its four null
     tests all use ``pd.isna``; two of them used to be ``int(x or 0)``, which
     raises ValueError on ``nan``, and one used to be ``is None``, which never
     fires once a column is float64.
  4. Two custom renderers that raised on an empty or partly-NULL table fixed.

THE RESIDUAL PASS AFTER ITEM 38 added sections 4b, 4c and 5b, and grew the seed
past the listing's cap so that the first of them has anything to measure:

  1. THE CONSISTENCY REPORT COULD NOT DISTINGUISH "20 ISSUES" FROM "20 OF 400",
     and the twenty it showed were whichever twenty SQLite chose -- ``LIMIT 20``
     sat on the outer select with no ORDER BY. A companion query counts by
     category over every row with no limit and prints immediately above the
     listing; the listing gained a total order. Both derive from ONE CASE
     expression, so they cannot disagree.
  2. THE NULL GUARD'S COLUMN LIST WAS UNENFORCED. Section 4c derives, from the
     SQL, which columns the CASE compares and which have a NULL treatment, and
     fails when a column has neither -- with a control for each direction,
     because "any new column fails" is a different and wrong rule.
  3. AN UNRECORDED COST PRINTED AS ZERO. ``cost_complete`` is the one field a
     consumer asks before summing ``recomputed_cost``, and every figure derived
     from that total now says when it is a floor.

Sections:
    1. The seeded temporary database: real schema from ``initialize_database``,
       rows chosen to exercise every hard case at once, and MORE inconsistent
       rows than the listing can show.
    2. EVERY query in the registry executes, and every one returns a NON-EMPTY
       frame on the seeded data -- so a query that silently returns nothing
       cannot pass as fixed. Then ``report()`` end to end.
    3. The deleted expansion query: gone from the registry, its columns absent
       from the schema, and the pre-fix SQL shown still to raise.
    4. The consistency query: the stray WHEN proved redundant against the
       PRE-FIX TEXT READ OUT OF GIT rather than retyped here; the pre-fix SQL
       shown to be a syntax error; the bounds shown to come from config and to
       be derived from the slices that produce the columns; and a flagged row
       and an unflagged row for every branch.
    4b. The companion totals, the listing's determinism over two runs, the clean
       case printing the clean message alone, the two queries proved to share
       one CASE by mutating it, and that CASE compared byte for byte against
       item 38's own committed blob.
    4c. Every column the CASE compares is guarded or NULL-aware, derived from
       the SQL, with both negative controls.
    5. The cost arithmetic on the float64 case, with the pre-fix function
       EXTRACTED FROM GIT AND EXEC'd as the negative control.
    5b. ``cost_complete``: what it is False for, what it is deliberately silent
       about, that it agrees with the note column, that the priced value is
       unchanged, and that every total says so -- with an all-complete control
       proving the report does not say it unconditionally.
    6. The dashboard consumes the query layer: identical frames, function
       identity, a structural check that no second copy remains, and the tab
       rendered end to end.
    7. The two custom renderers against an empty database, with the pre-fix
       renderer shown to raise.
    8. Neither docstring still claims the two queries are broken on purpose,
       with the scan shown to find the claim in the pre-fix text.
    9. The production database was never opened for writing and its row count
       is unchanged.

NOTHING IN THIS FILE MAY ABORT THE RUN, and two things did until the reverts
were actually run. ``QUERIES_BY_KEY["k"]`` and ``QUERY_KEYS.index("k")`` raise
when the companion is deleted -- the very edit the section exists to catch --
and ``text.split(marker)[1]`` raises when a report line is missing, which is
what a reverted ``cost_complete`` produces. Both crashed at module level and
hid every check below, so the run reported one traceback where it should have
reported ten failures. ``after()`` and ``registry_index()`` are the fix; see
their docstrings.

No network, no LLM, no API key, no Qdrant. Everything runs against a SQLite file
in a temporary directory that is removed at the end. The production database is
opened only through a ``mode=ro`` URI, and only to count rows.

Run from terminal (or F5 in Spyder):
    python tests/test_storage_query_layer.py
    (was: python "49- Database Query Layer Test.py")

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures

CONCURRENCY: it does NOT belong in run_serial_tests.py's collision matrix. That
matrix exists for Files 42, 43, 44 and 47, every one of which MUTATES A FILE IN
THE REPOSITORY -- planting defects into the registry, rewriting the
snapshot-date literal, or copytree()ing the package. This file writes only into
a fresh temporary directory, reads the repository's source text without
modifying a byte of it, and reads history through ``git show``, which touches no
working-tree file. Two copies of it could run at once.

The four source files it ASSERTS on -- queries.py, cost_tokens.py,
agent/retrieval.py, agent/terminal.py -- are not mutated by any of those four,
so nothing it checks can be caught mid-edit. It does IMPORT the package, which
means it shares with Files 30-41 the ordinary hazard of reading config.py or
cancer_code_registry.py inside File 44's or File 43's restore window. That is a
property of importing at all rather than a collision the matrix is for, and it
is why the four that MUTATE are the four that are serialized.
"""


# Run needed file
#----------------
import ast
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile


# Make the oncotriage package importable
#---------------------------------------
# The same block Files 04, 06, 11 and 12 carry, with the one difference pass
# 20d-2 forced: it looks at the PARENT of this file's directory, because this
# file now sits in tests/ and the package sits BESIDE tests/, not inside it.
# `pip install -e .` makes it a no-op; without it the code directory goes on
# sys.path and the fact is printed rather than left silent.
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

import pandas as pd

# THE CALL-MODE VOCABULARY IS IMPORTED AND NEVER RETYPED. Section 8b's arm pair
# and section 8c's comparison both turn on these two strings being the ones the
# writer stores, and a literal here would agree with a typo in the seed.
#
# IT DOES NOT PUT THIS FILE IN THE COLLISION MATRIX. That derivation turns on
# WHICH config VALUES this file depends on -- `tests/test_config_snapshot_date_
# rot.py` rewrites `oncotriage/config.py` in place and the value it rewrites is
# `DATA_SNAPSHOT_DATE`. These two are untouched by that rewrite, exactly as
# RRF_POOL_SIZE and TOP_K_CANDIDATES beside them are, and the module was already
# imported.
from oncotriage.config import (MATCHING_CALL_MODE_GROUPED,
                               MATCHING_CALL_MODE_PER_TRIAL,
                               MATCHING_CALL_MODES,
                               RRF_POOL_SIZE, TOP_K_CANDIDATES)
from oncotriage.storage import queries
from oncotriage.storage import database_logger as dblog
from oncotriage.storage.database_logger import initialize_database
from oncotriage.utils import UnknownModelPricingError, get_model_cost


# PASS 20d-2: the repository root, derived from the PACKAGE's own location
# rather than from this file's. `oncotriage/__init__.py` -> `oncotriage/` -> the
# code directory. This file already imports the package unconditionally above.
#
# IT IS ALSO THE git CWD, which is what makes this more than a cosmetic change:
# `_git("log", "--", "oncotriage/storage/queries.py")` run from tests/ still
# finds the repository (git walks up) but the PATHSPEC is resolved relative to
# the cwd, so it would match nothing and `_newest_revision_where` would return
# (None, None) for both revisions -- turning every negative control in sections
# 3, 4b, 5 and 7 into a reported failure. Loud, but for the wrong reason.
_CODE_DIR = os.path.dirname(
    os.path.dirname(os.path.abspath(oncotriage.__file__)))

# The two priced models the seed uses. Read out of PRICING_CONFIG rather than
# written here, so this file cannot drift from the pricing table and cannot
# accidentally seed a model the arithmetic would refuse.
_PRICED_MODELS = sorted(queries.PRICING_CONFIG["models"])


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================
#
# The same shape as "tests/test_degraded_dependencies.py"'s, deliberately: a check
# that aborts the run hides every check after it, which is the exact failure
# this file exists to have removed from File 16.

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label: str, actual, expected) -> None:
    """Assert equality, record the outcome, never abort the run."""
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(
            f"{label}\n          expected: {expected}\n          actual:   {actual}"
        )
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


def check_true(label: str, condition) -> None:
    check(label, bool(condition), True)


def check_raises(label: str, exc_type, fn, *args, **kwargs):
    """Assert `fn` raises `exc_type`. Returns the exception, or None.

    BOTH branches record and print, so this helper is never itself a silent
    handler.
    """
    try:
        fn(*args, **kwargs)
    except exc_type as exc:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
        return exc
    except Exception as exc:  # noqa: BLE001 - reporting the wrong type IS the point
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          raised {type(exc).__name__}: {exc}\n"
                         f"          expected {exc_type.__name__}")
        print(f"  FAIL  {label} — raised {type(exc).__name__}: {exc}")
        return None
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          nothing was raised")
    print(f"  FAIL  {label} — nothing was raised")
    return None


def check_does_not_raise(label: str, fn, *args, **kwargs):
    """Assert `fn` returns. Returns its value, or None.

    THE OTHER HALF OF EVERY RAISE IN THIS FILE. A check that only ever shows a
    raise firing cannot distinguish "fires on the broken input" from "fires on
    everything".
    """
    try:
        value = fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          raised {type(exc).__name__}: {exc}")
        print(f"  FAIL  {label} — raised {type(exc).__name__}: {exc}")
        return None
    _RESULTS["passed"] += 1
    print(f"  PASS  {label}")
    return value


class _RaisedFrame:
    """What `_frame_or_raise` returns instead of a DataFrame. NEVER equal.

    A PLAIN STRING WAS THE FIRST VERSION AND IT MOVED THE ABORT ONE LINE. The
    consumers here call `.itertuples()`, `len()` and `frame["col"]` on the
    result, so a string turned `MissingTableError` into
    `AttributeError: 'str' object has no attribute 'itertuples'` -- a different
    traceback in the same place. Measured by a revert harness, twice.

    It answers those three operations emptily so the file reaches its summary,
    and `__eq__` is ALWAYS False so no comparison can accidentally succeed
    against an expected empty value -- which is what an empty DataFrame would
    have allowed, and is the "no rows" / "could not ask" conflation this project
    treats as a defect.
    """

    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text

    def itertuples(self, *a, **k):
        return iter(())

    def __len__(self):
        return 0

    def __iter__(self):
        return iter(())

    def __getitem__(self, key):
        # PROPAGATES rather than collapsing to a list. These frames are indexed
        # in CHAINS -- `frame[frame["col"] == v]["other"]` -- and returning `[]`
        # made the SECOND index a TypeError, moving the abort one step along the
        # chain instead of removing it. Returning self means every step of any
        # chain stays a marker, `list(...)` of it is empty, and the comparison
        # at the end FAILS and prints what raised.
        return self

    def __getattr__(self, name):
        # ANY method call on a marker returns the marker. These frames are used
        # as `frame["col"].sum()`, `.mean()`, `.itertuples()` -- a chain that has
        # to terminate somewhere, and enumerating the methods meant finding the
        # next one each time a revert harness ran. This terminates all of them
        # at once. `_safe_int`/`_safe_float` are what turn the marker into a
        # recorded FAILURE at the comparison.
        def _still_the_marker(*args, **kwargs):
            return self
        return _still_the_marker

    # THE ORDERING OPERATORS ARE SPELLED OUT, and this project already records
    # why: CPython looks an implicit special method up on the TYPE, never
    # through `__getattr__`, so the catch-all above does NOT answer `>`, `<`,
    # `int()` or `len()`. A revert harness found each of those in turn. They
    # return False so a comparison against a marker FAILS rather than raising --
    # which is the difference between a recorded failure and an aborted file.
    def __eq__(self, other):
        return False

    def __lt__(self, other):
        return False

    def __le__(self, other):
        return False

    def __gt__(self, other):
        return False

    def __ge__(self, other):
        return False

    def __hash__(self):
        return hash(self.text)

    def __repr__(self):
        return f"<raised {self.text}>"


class _NoRow:
    """A named absence standing in for a run_summary row that is not there.

    Every attribute read answers with a string NAMING what was wanted, and no
    comparison can succeed -- so a check fails and prints the absence instead of
    aborting.
    """

    def __getattr__(self, name):
        return f"<no run_summary row: {name}>"

    def __eq__(self, other):
        return False

    def __hash__(self):
        return 0


class _RowIndex(dict):
    """`{run_id: row}` whose MISSING key yields `_NoRow` rather than KeyError.

    THE FIX IS AT THE SOURCE AND NOT AT THE SUBSCRIPTS. This index is DERIVED
    from a frame `_frame_or_raise` may have replaced with a marker, so when a
    planted defect makes `run_summary` unanswerable the dict is empty and every
    `_summary_by_id[...]` in the file raises. Guarding the fetch was not enough,
    and neither was guarding one subscript: a revert harness found the next one
    two lines down, then the conversion after that. Fixing the container fixes
    all of them at once and cannot be forgotten at a new call site.
    """

    def __missing__(self, key):
        return _NoRow()


def _summary_patients(frame):
    """`frame["patients"].sum()`, or the marker. NEVER RAISES.

    A `_RaisedFrame` indexes to itself and has no `.sum()`, so the chain has to
    stop somewhere -- here, at the one call site that sums a column.
    """
    try:
        return frame["patients"].sum()
    except BaseException as exc:                       # noqa: BLE001 -- reported
        return f"<no patients column: {type(exc).__name__}>"


def _safe_int(value):
    """`int(value)` or the value itself, so a marker FAILS rather than aborts."""
    try:
        return int(value)
    except BaseException:                              # noqa: BLE001 -- reported
        return value


def _safe_float(value, digits=None):
    """`float(value)` (optionally rounded) or the value itself.

    Same reason as `_safe_int`. A named-absence marker is a STRING, and
    `float("<no ... >")` raises ValueError -- which moves the abort from the
    lookup to the conversion, which is exactly where a revert harness found it
    next. The marker is returned unchanged instead, so the comparison FAILS and
    prints what was missing.
    """
    try:
        return float(value) if digits is None else round(float(value), digits)
    except BaseException:                              # noqa: BLE001 -- reported
        return value


def _frame_or_raise(key, conn=None):
    """`queries.run` for a key that may legitimately be refused. NEVER RAISES.

    A query declaring `requires_columns` raises `MissingTableError` on a
    database that does not have them -- correct behaviour, and exactly what a
    broken migration produces. A bare call would abort this file at the moment
    it owes a summary.
    """
    try:
        return queries.run(conn if conn is not None else _conn, key)
    except BaseException as exc:                       # noqa: BLE001 -- reported
        return _RaisedFrame(f"{type(exc).__name__}: {exc}")


def after(text, marker):
    """Everything in `text` after `marker`, or "" when the marker is absent.

    EVERY USE OF THIS WAS FIRST WRITTEN AS ``text.split(marker)[1]``, AND THAT
    ABORTS THE RUN. When the marker is missing -- which is precisely what a
    reverted fix produces -- the index raises IndexError at module level and
    every check below it never executes, so the run reports the ONE crash
    instead of the six failures it was built to report. Found by reverting the
    cost_complete fix in a copy of the package and watching this file crash
    rather than fail; the same shape as File 16's own original defect, in the
    file written to remove it.
    """
    _, separator, tail = text.partition(marker)
    return tail if separator else ""


def registry_index(key):
    """Position of `key` in QUERY_KEYS, or -1. ``.index()`` raises ValueError."""
    return (queries.QUERY_KEYS.index(key) if key in queries.QUERY_KEYS else -1)


class quiet:
    """Swallow stdout for a block. initialize_database prints a migration line
    per added column and report() prints ~40 tables; neither is under test."""

    def __enter__(self):
        self._saved = sys.stdout
        sys.stdout = io.StringIO()
        return self

    def __exit__(self, *exc):
        sys.stdout = self._saved
        return False


# ===========================================================================
# READING THE PRE-FIX SOURCE OUT OF GIT
# ===========================================================================
#
# EVERY NEGATIVE CONTROL IN THIS FILE USES THE REAL PRE-FIX TEXT, NOT A COPY OF
# IT TYPED HERE. That is not fastidiousness. The claim "removing the stray WHEN
# changes no logic" rests on the stray line being character-for-character the
# line already inside the CASE; if this file carried its own transcription of
# both lines, the check would compare my typing against my typing and agree by
# construction -- which is exactly the defect CLAUDE.md records File 42's
# boundary assertions having shipped with.
#
# THE COMMIT IS DERIVED, NOT DECLARED, AND THAT IS WHAT LETS THIS FILE SURVIVE
# BEING COMMITTED. `HEAD` is the pre-fix version only until item 38 lands, after
# which it is the fixed one and every control here would silently stop
# controlling anything. So: walk the file's history newest-first and take the
# first revision whose blob still DEFINES the query under discussion.
#
# THE SELECTOR IS STRUCTURAL, AND THE FIRST VERSION OF IT WAS NOT -- WHICH IS
# HOW IT BROKE. It searched for the literal string `expansion_input_tokens`,
# reasoning that only the broken query could name a column that does not exist.
# That was wrong the moment item 38 was committed, because the DELETION COMMENT
# left in its place quotes the query it removed, twice. The selector then picked
# item 38's own revision, `_pre_fix_function("cost_by_model")` returned the
# FIXED function, and two negative controls failed with NameError instead of
# controlling anything. A substring is not a definition; this version parses the
# blob and asks which query KEYS the registry actually declares, which prose can
# never satisfy.

_QUERIES_REL = "oncotriage/storage/queries.py"


def _git(*args):
    """Run git in the code directory and return stdout, or None on any failure."""
    try:
        completed = subprocess.run(
            ["git", *args], cwd=_CODE_DIR, capture_output=True, text=True)
    except (OSError, ValueError) as exc:            # git absent, bad argv
        print(f"  [git] {args[0]} unavailable: {type(exc).__name__}: {exc}")
        return None
    if completed.returncode != 0:
        # Recorded rather than swallowed -- this project does not allow a
        # handler that leaves no trace, and a missing control must be visible.
        print(f"  [git] {' '.join(args[:2])} failed (exit "
              f"{completed.returncode}): {completed.stderr.strip()[:200]}")
        return None
    return completed.stdout


def _declared_query_keys(src):
    """Every ``Query(key='...')`` the module source declares, as a set.

    AST rather than text search. A key is a keyword argument to a constructor
    call; a mention in a comment or a docstring is not, and the difference is
    exactly what the previous selector could not see.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    keys = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "Query"):
            continue
        for kw in node.keywords:
            if kw.arg == "key" and isinstance(kw.value, ast.Constant):
                keys.add(kw.value.value)
    return keys


def _newest_revision_where(predicate, label):
    """Newest revision of queries.py whose declared query keys satisfy predicate.

    Returns (revision, source) or (None, None). A failure is reported as a
    FAILED check by the caller rather than skipped: this repository has the
    history, and a control that quietly does not run is worse than one that
    fails.
    """
    log = _git("log", "--format=%H", "--", _QUERIES_REL)
    if not log:
        return None, None
    for rev in log.split():
        blob = _git("show", f"{rev}:{_QUERIES_REL}")
        if blob and predicate(_declared_query_keys(blob)):
            return rev, blob
    print(f"  [git] no revision of {_QUERIES_REL} matched: {label}")
    return None, None


# The last revision that still DECLARED the broken query. Everything in
# sections 3, 5 and 7 that says "and here is the thing it used to do" comes
# from this blob.
_PRE_FIX_REV, _PRE_FIX_SRC = _newest_revision_where(
    lambda keys: "expansion_token_efficiency" in keys,
    "declares expansion_token_efficiency")

# Item 38 as shipped: the consistency query exists, the companion totals query
# does not yet. Section 4b compares this pass's CASE against that blob, so
# "the classification is unchanged" is measured against the committed artefact
# rather than against a hash somebody typed.
_ITEM38_REV, _ITEM38_SRC = _newest_revision_where(
    lambda keys: ("pipeline_consistency" in keys
                  and "pipeline_consistency_totals" not in keys),
    "declares pipeline_consistency but not pipeline_consistency_totals")


def _pre_fix_string_constant(name_hint, must_contain):
    """Pull one SQL string constant out of the pre-fix module by content.

    Located by what it CONTAINS rather than by line number, because a line
    number would be a second thing to keep in step with history.
    """
    if not _PRE_FIX_SRC:
        return None
    for node in ast.walk(ast.parse(_PRE_FIX_SRC)):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and all(m in node.value for m in must_contain)):
            return node.value
    print(f"  [git] no pre-fix string constant matched {name_hint}")
    return None


def _pre_fix_function(name):
    """Unparse one top-level function out of the pre-fix module source."""
    if not _PRE_FIX_SRC:
        return None
    for node in ast.parse(_PRE_FIX_SRC).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    print(f"  [git] no pre-fix function named {name}")
    return None


# ===========================================================================
# SECTION 1 -- THE SEEDED TEMPORARY DATABASE
# ===========================================================================

print("=" * 78)
print("SECTION 1 -- the seeded temporary database")
print("=" * 78)

_TMP_DIR = tempfile.mkdtemp(prefix="oncotriage-queries-")
_DB_PATH = os.path.join(_TMP_DIR, "seeded.db")
_EMPTY_DB_PATH = os.path.join(_TMP_DIR, "empty.db")

# READ BEFORE ANY OF THIS FILE'S OWN DATABASE WORK. Section 9 compares this
# against the same count at the end; taken afterwards it would be comparing a
# number against itself and could never report a write.
_PRODUCTION_DB = queries.resolve_query_db_path(None)


def _production_inference_rows():
    """Count production rows through a mode=ro URI, or None if absent.

    ``mode=ro`` rather than a plain connect, on File 41's precedent: a plain
    ``sqlite3.connect`` on a missing path CREATES the file, so a guard written
    that way would bring its own database into existence, count 0 twice, and
    report success.
    """
    if not os.path.isfile(_PRODUCTION_DB):
        return None
    uri = "file:" + os.path.abspath(_PRODUCTION_DB).replace("?", "%3f") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM inferences").fetchone()[0]
    except sqlite3.Error as exc:
        print(f"  [production] could not be counted: {exc}")
        return None
    finally:
        conn.close()


_PRODUCTION_ROWS_BEFORE = _production_inference_rows()

# THE SCHEMA IS THE REAL ONE, produced by the writer rather than retyped here.
# A hand-written CREATE TABLE would let this file pass against a schema the
# pipeline does not have -- which is precisely how a query selecting a column
# that does not exist survived for the life of the project.
with quiet():
    initialize_database(_DB_PATH)
    initialize_database(_EMPTY_DB_PATH)

check_true("the seeded database file exists", os.path.isfile(_DB_PATH))

_conn = sqlite3.connect(_DB_PATH)
_SCHEMA_COLUMNS = {row[1] for row in
                   _conn.execute("PRAGMA table_info(inferences)")}
check_true("the inferences table came back with a real schema (non-degeneracy)",
           len(_SCHEMA_COLUMNS) > 50)


# --- the rows --------------------------------------------------------------
#
# Every hard case this item is about is present at once, because they interact:
# the float64 coercion that breaks the cost arithmetic only happens when an
# all-NULL group sits BESIDE a group carrying numbers, so seeding them in
# separate databases would test neither.

_MODEL_A = "gpt-4o-2024-08-06"
_MODEL_B = "gpt-5.6-terra"

_BASE_ROW = {
    "timestamp": "2026-08-01 10:00:00",
    "age": 60, "sex": "male", "race": "White", "ethnicity": "Not Hispanic",
    "primary_condition": "Malignant neoplasm of breast",
    "condition_count": 6, "medication_count": 12, "allergy_count": 1,
    "expanded_query": "breast carcinoma", "llm_classifier_prompt": "PROMPT TEXT",
    "bm25_retrieved": 60, "vector_retrieved": 80,
    "candidates_after_rule_filter": 20, "candidates_after_quality_filter": 15,
    "mesh_dropped": 3, "mesh_resolution": "snomed",
    "stage_dropped": 1, "histology_dropped": 0, "cross_vocab_remaps": 0,
    "query_expansion_time": 0.01, "hybrid_retrieval_time": 1.5,
    "cross_encoder_time": 2.5, "rule_filter_time": 0.2,
    "llm_classifier_evaluation_time": 60.0, "total_time": 64.2,
    # STAYS A LITERAL, and pass 20f-2 checked rather than assumed it should.
    # That pass gave the checkpoint one name, oncotriage.config.
    # CROSS_ENCODER_MODEL, and replaced the five other copies of the string --
    # but every one of those was a LOAD or a live REPORT, where a stale copy
    # means the row says one thing and the process did another. This is neither:
    # it is a stored value in a seeded row, standing in for what a database
    # written months ago holds, exactly like _MODEL_A ("gpt-4o-2024-08-06")
    # above it and "pricing_version" below. Importing the constant here would
    # make a fixed historical row track whatever the pipeline loads today, which
    # is the opposite of what a stored column means. The check that enforces the
    # single name (test_package_invariants.py 2f(ii)) is scoped to the package
    # for this reason, and says so.
    "cross_encoder_model": "ncbi/MedCPT-Cross-Encoder",
    "pricing_version": "2026-08-04", "qdrant_collection": "trial_criteria_x",
    "error": "", "patient_data_hash": "deadbeef",
    "llm_classifier_retries": 0, "ablation_flags": "{}",
    "retrieval_channels_expected": 4, "retrieval_channels_ok": 4,
    "retrieval_degraded": 0, "retrieval_trials_lost": 0,
    "query_expansion_path": "mesh_expanded",
    "mesh_filter_applied": 1, "mesh_filter_skip_reason": None,
    "age_reference_date": "2026-02-26", "birth_date_precision": "day",
    "ecog_value": 1, "ecog_selection": "most_recent_on_or_before_reference",
    "ecog_observations_found": 2,
    "llm_classifier_truncation_splits": 0, "llm_classifier_calls": 1,
    "not_evaluable_truncated": 0, "llm_classifier_output_tokens_estimated": 5000,
}

# --- STAGE 5 SPLIT PRESSURE: the shapes the two pressure queries read ------
#
# BUILT HERE RATHER THAN IMPORTED. These two blobs stand in for what a database
# written months ago holds, exactly as `cross_encoder_model` above does, so they
# are literals of this file and not a call into the writer that produces them.
# What DOES have to agree with the writer is the KEY SET, and that is asserted
# in section 2c against oncotriage/agent/evaluation.py's own report rather than
# claimed here.
#
# THE VALUES ARE CHOSEN SO NO PRESSURE READING CAN PASS DEGENERATELY. A seed in
# which every chunk sits at the same fraction of its budget satisfies "the query
# returned a number" while proving nothing about which number: peak, mean and
# the two bucket counters would all agree for the wrong reason. So the rows
# below span the whole range the guards can be in -- one run tight against both
# budgets with a relaxed cap and an over-budget chunk, one comfortably clear,
# one whose packer never published, and a large population carrying NULL in
# every one of these columns.

def _packing_blob(budget, chunks, *, relaxed=False, configured=20000,
                  fixed=4000, max_chunks=4):
    """A `llm_classifier_packing` value, in the writer's own shape.

    ``chunks`` is a list of ``(trials, tokens_estimated, over_budget)``.
    ``budget`` is the EFFECTIVE budget -- the one a relaxed run was raised to,
    which is the denominator every pressure reading uses.
    """
    return json.dumps({
        "enabled": True, "method": "chars", "fixed_tokens": fixed,
        "budget_tokens_configured": configured, "budget_tokens": budget,
        "max_chunks": max_chunks, "cap_relaxed_budget": relaxed,
        "over_budget_chunk": any(o for _, _, o in chunks),
        "trials": sum(t for t, _, _ in chunks),
        "chunks": [{"trials": t, "tokens_estimated": e, "over_budget": o}
                   for t, e, o in chunks],
        "prefix_sha256": "0" * 64,
    })


def _bypass_packing_blob(trials, *, configured=20000, fixed=4000,
                         bypassed_by=MATCHING_CALL_MODE_PER_TRIAL):
    """A `llm_classifier_packing` value written by a BYPASSED packer.

    The node writes this itself on its per-trial branch rather than getting it
    from `pack_trials_by_input_tokens`: `enabled` false, every number the packer
    would have selected None or False, no chunk, and `bypassed_by` naming what
    partitioned the batch instead. It is the only shape in which
    `llm_classifier_packing` is NOT NULL while
    `llm_classifier_packed_chunks` is.

    THE KEY SET IS COMPARED AGAINST THE WRITER'S OWN LITERAL in section 2c(a),
    by AST, so a fixture that drifted from the node could not go on satisfying
    the bucket checks below by accident.
    """
    return json.dumps({
        "enabled": False, "method": "chars", "fixed_tokens": fixed,
        "budget_tokens_configured": configured, "budget_tokens": None,
        "max_chunks": None, "cap_relaxed_budget": False,
        "over_budget_chunk": False, "trials": trials, "chunks": [],
        "bypassed_by": bypassed_by,
        "prefix_sha256": "0" * 64,
    })


def _call_details_blob(*completions):
    """A `llm_classifier_call_details` value: one entry per call issued.

    Only ``completion_tokens`` is read by the output-pressure query, but every
    key the writer emits is present -- a fixture carrying a SUBSET would let a
    query that reached for a missing sibling key pass by returning NULL.
    """
    return json.dumps([
        {"call_index": _i + 1, "depth": 0, "trials": 5,
         "prompt_tokens": 9000, "completion_tokens": _c,
         "cached_tokens": None, "reasoning_tokens": None,
         "finish_reason": "stop", "entries_emitted": 5}
        for _i, _c in enumerate(completions)])


def _per_trial_details_blob(warmup, *wave):
    """A per-trial ledger: ONE warmup row, then one row per trial call.

    ``warmup`` and each ``wave`` member are ``(prompt_tokens, cached_tokens)``;
    a ``cached_tokens`` of ``None`` is a response that carried no
    ``prompt_tokens_details.cached_tokens`` AT ALL, which is a different reading
    from 0 and is what `stage5_cache_effectiveness` has to keep out of both
    halves of its rate.

    THE WARMUP ROW IS SHAPED AS THE WRITER SHAPES IT and not as a trial row with
    a flag bolted on: ``warmup`` present (and present on no other row -- the
    absent-rather-than-empty convention the ledger already follows for
    ``unconsumed``), ``trials`` 0, ``depth`` None because 0 is a real split
    depth, ``entries_emitted`` None because nothing parsed it, and
    ``finish_reason`` "length" because a one-token ceiling is what it asks for.
    A fixture that got any of those wrong would let a query keying on the wrong
    one pass here and mis-read every real row.
    """
    rows = [{"call_index": 1, "depth": None, "trials": 0,
             "prompt_tokens": warmup[0], "completion_tokens": 1,
             "cached_tokens": warmup[1], "reasoning_tokens": None,
             "finish_reason": "length", "entries_emitted": None,
             "warmup": True}]
    for _i, (_p, _c) in enumerate(wave):
        rows.append({"call_index": _i + 2, "depth": 0, "trials": 1,
                     "prompt_tokens": _p, "completion_tokens": 500,
                     "cached_tokens": _c, "reasoning_tokens": None,
                     "finish_reason": "stop", "entries_emitted": 1})
    return json.dumps(rows)


def _grouped_details_blob(*calls):
    """A grouped ledger: one row per packed chunk, no warmup row at all.

    Grouped mode issues no warmup, so a fixture carrying one would make the
    "warmup_calls is 0 in grouped mode by construction" reading untestable.
    """
    return json.dumps([
        {"call_index": _i + 1, "depth": 0, "trials": 5,
         "prompt_tokens": _p, "completion_tokens": 500, "cached_tokens": _c,
         "reasoning_tokens": None, "finish_reason": "stop",
         "entries_emitted": 5}
        for _i, (_p, _c) in enumerate(calls)])


# The configured pair for the two eras the seed spans. The first is what
# oncotriage/config.py holds today (32,000 x 0.90); the second stands in for the
# GPT-4o era, whose ceiling was 16,000 -- which is the whole reason the
# threshold is a stored column and not a constant a reader looks up. A campaign
# spanning both must report min != max rather than one averaged number.
_THRESHOLD_NOW, _CEILING_NOW = 28800, 32000
_THRESHOLD_OLD, _CEILING_OLD = 14400, 16000


# (label, overrides). The consistency expectation for each is asserted in
# section 4 by patient_id, so the seed and the expectation are one table rather
# than two lists that can drift apart.
# The dangling run id. A literal far outside anything `runs` AUTOINCREMENT will
# reach in this file, asserted absent from `runs` after the seed.
_DANGLING_RUN_ID = 999_999
_DANGLING_COST = 0.1234

_SEED_ROWS = [
    # THE DANGLING ROW: ordinary in every respect except that its `run_id` names
    # a `runs` row that does not exist. That state is reachable because the
    # foreign key is declared and deliberately unenforced, and
    # `dangling_run_references` is the only thing in the project that reports
    # WHICH ids are in it -- so the seed has to contain one, or that query's
    # non-empty case would ship exercised by nothing.
    #
    # ITS COUNTERS ARE CONSISTENT (5 + 8 + 2 == 15) ON PURPOSE. A first attempt
    # left them NULL and `pipeline_consistency` promptly classified it as
    # "Counters not reported", putting a row about run attribution into three
    # expectations about something else. A seeded defect must be a defect in
    # exactly one dimension.
    ("P-DANGLING", dict(
        run_id=_DANGLING_RUN_ID,
        matching_model=_MODEL_A, llm_classifier_input_tokens=1000,
        llm_classifier_output_tokens=500, llm_classifier_reasoning_tokens=None,
        matching_call_mode=MATCHING_CALL_MODE_GROUPED,
        estimated_cost_usd=_DANGLING_COST, medication_count=4,
        condition_count=3, total_time=9.0, age=55,
        candidates_retrieved=90, candidates_reranked=30,
        candidates_filtered=15, candidates_evaluated=15,
        eligible_matches=5, near_misses=8, not_evaluable_trials=2)),
    # Consistent: 5 + 8 + 2 == 15. Slow and drug-heavy, which is what makes
    # `extreme_cases`, `medication_duplication_suspects` and `slowest_prompt`
    # non-empty.
    ("P-CONSISTENT-A", dict(
        matching_model=_MODEL_A, llm_classifier_input_tokens=10000,
        llm_classifier_output_tokens=5000, llm_classifier_reasoning_tokens=None,
        matching_call_mode=MATCHING_CALL_MODE_GROUPED,
        estimated_cost_usd=0.075, medication_count=120, condition_count=10,
        total_time=130.0, age=61,
        candidates_retrieved=100, candidates_reranked=40,
        candidates_filtered=15, candidates_evaluated=15,
        eligible_matches=5, near_misses=8, not_evaluable_trials=2,
        llm_classifier_packing=_packing_blob(
            20000, [(8, 19600, False), (7, 12000, False)]),
        llm_classifier_packed_chunks=2,
        llm_classifier_output_split_threshold=_THRESHOLD_NOW,
        llm_classifier_output_ceiling=_CEILING_NOW,
        llm_classifier_output_tokens_estimated=20625,
        # THE INPUT SCALAR, era 6. The largest of this patient's two planned
        # requests, against the budget in force. Written to AGREE with the
        # packing blob above -- 19,600 is that blob's tightest chunk -- so the
        # two pressure queries describe one run rather than two.
        llm_classifier_input_tokens_estimated=19600,
        llm_classifier_input_budget=20000,
        llm_classifier_call_details=_call_details_blob(9000, 7000))),
    # Consistent: 3 + 12 + 0 == 15. Same candidates_evaluated as the row above,
    # which is what satisfies `llm_classifier_efficiency_by_trial_count`'s HAVING >= 2,
    # and >4000 output tokens, which is what makes `verbose_output` non-empty.
    ("P-CONSISTENT-B", dict(
        matching_model=_MODEL_A, llm_classifier_input_tokens=20000,
        llm_classifier_output_tokens=4500, llm_classifier_reasoning_tokens=1200,
        matching_call_mode=MATCHING_CALL_MODE_PER_TRIAL,
        estimated_cost_usd=0.095, age=72, sex="female", medication_count=60,
        candidates_retrieved=87, candidates_reranked=40,
        candidates_filtered=15, candidates_evaluated=15,
        eligible_matches=3, near_misses=12, not_evaluable_trials=0,
        llm_classifier_packing=_packing_blob(20000, [(15, 9000, False)]),
        llm_classifier_packed_chunks=1,
        llm_classifier_output_split_threshold=_THRESHOLD_NOW,
        llm_classifier_output_ceiling=_CEILING_NOW,
        llm_classifier_output_tokens_estimated=16500,
        llm_classifier_input_tokens_estimated=9000,
        llm_classifier_input_budget=20000,
        llm_classifier_call_details=_call_details_blob(15900))),
    # THE BYPASSED ROW. Consistent (4 + 2 + 0 == 6), priced, per-trial arm --
    # ordinary in every respect except the one it is here for: its packer was
    # BYPASSED rather than absent, so `llm_classifier_packing` is present and
    # names what bypassed it while `llm_classifier_packed_chunks` is NULL.
    # It sits in RUN-CLEAN beside P-NULL-TOKENS, whose packer left no record at
    # all, so one group carries both populations and the pressure query has to
    # separate them rather than merely count one of them.
    ("P-BYPASSED", dict(
        matching_model=_MODEL_A, llm_classifier_input_tokens=12000,
        llm_classifier_output_tokens=3000, llm_classifier_reasoning_tokens=None,
        matching_call_mode=MATCHING_CALL_MODE_PER_TRIAL,
        estimated_cost_usd=0.055, age=68, sex="female", medication_count=9,
        condition_count=3, total_time=95.0,
        candidates_retrieved=90, candidates_reranked=40,
        candidates_filtered=6, candidates_evaluated=6,
        eligible_matches=4, near_misses=2, not_evaluable_trials=0,
        llm_classifier_packing=_bypass_packing_blob(6),
        # NULL, NOT 0: the packer did not run, so it has no chunk count. 0 is
        # reserved for a packer that ran and produced none, i.e. an empty
        # candidate set -- and this patient sent six requests.
        llm_classifier_packed_chunks=None,
        llm_classifier_output_split_threshold=_THRESHOLD_NOW,
        llm_classifier_output_ceiling=_CEILING_NOW,
        llm_classifier_output_tokens_estimated=6600,
        # THE ROW THE PACKING QUERY CANNOT MEASURE AND THIS ONE CAN. Its packer
        # was bypassed, so it contributes no chunk and no budget there -- and
        # its six requests each carried the shared prefix plus one trial, which
        # is a real per-request input size and is what nearly reached the
        # budget. 11,500 / 12,000 is deliberately the tightest reading in
        # RUN-CLEAN's per-trial arm, so a query that dropped bypassed rows
        # would lose the group's peak rather than merely a row.
        llm_classifier_input_tokens_estimated=11500,
        llm_classifier_input_budget=12000,
        llm_classifier_call_details=_call_details_blob(500, 500, 500,
                                                       500, 500, 500))),
    # THE ALL-NULL GROUP. Its own model, every token column and the stored cost
    # NULL. Beside the two rows above this is what makes the aggregate columns
    # float64 and turns `int(x or 0)` into a ValueError.
    ("P-NULL-TOKENS", dict(
        matching_model=_MODEL_B, llm_classifier_input_tokens=None,
        llm_classifier_output_tokens=None, llm_classifier_reasoning_tokens=None,
        estimated_cost_usd=None, age=55, total_time=50.0,
        retrieval_degraded=1, retrieval_channels_ok=3,
        retrieval_channels='{"title": {"status": "ok", "count": 60}}',
        retrieval_trials_lost=2,
        candidates_retrieved=100, candidates_reranked=40,
        candidates_filtered=10, candidates_evaluated=10,
        eligible_matches=2, near_misses=7, not_evaluable_trials=1,
        llm_classifier_packing=None,
        llm_classifier_packed_chunks=None,
        llm_classifier_output_split_threshold=_THRESHOLD_NOW,
        llm_classifier_output_ceiling=_CEILING_NOW,
        llm_classifier_output_tokens_estimated=16500,
        # THE LEGACY ROW FOR THE INPUT SCALAR TOO: the two era-6 columns are
        # DELIBERATELY ABSENT here, so they insert NULL. That is a row written
        # before era 6, and it is what makes `unmeasured` a live bucket rather
        # than a column that is always 0.
        llm_classifier_call_details="[]")),
    # NULL model, no tokens. A no-candidates run.
    ("P-NOMODEL-CLEAN", dict(
        matching_model=None, llm_classifier_input_tokens=0, llm_classifier_output_tokens=0,
        llm_classifier_reasoning_tokens=None, estimated_cost_usd=0.0, age=44,
        sex="female", medication_count=2, condition_count=1, total_time=5.0,
        query_expansion_path="base_query_fallback", mesh_filter_applied=0,
        mesh_filter_skip_reason="no_mesh_filter", mesh_resolution="unmapped",
        candidates_retrieved=0, candidates_reranked=0,
        candidates_filtered=0, candidates_evaluated=0,
        eligible_matches=0, near_misses=0, not_evaluable_trials=0)),
    # NULL model WITH tokens. The logging defect the note is for.
    ("P-NOMODEL-TOKENS", dict(
        matching_model=None, llm_classifier_input_tokens=1234, llm_classifier_output_tokens=567,
        llm_classifier_reasoning_tokens=None, estimated_cost_usd=0.0, age=66,
        candidates_retrieved=50, candidates_reranked=30,
        candidates_filtered=12, candidates_evaluated=12,
        eligible_matches=4, near_misses=8, not_evaluable_trials=0)),
    # 5 + 3 + 2 == 10, not 15. A genuine count mismatch.
    ("P-COUNT-MISMATCH", dict(
        matching_model=_MODEL_A, llm_classifier_input_tokens=9000,
        llm_classifier_output_tokens=3000, llm_classifier_reasoning_tokens=None,
        estimated_cost_usd=0.06, age=50, sex="female",
        candidates_retrieved=100, candidates_reranked=40,
        candidates_filtered=15, candidates_evaluated=15,
        eligible_matches=5, near_misses=3, not_evaluable_trials=2,
        llm_classifier_packing=_packing_blob(
            24000, [(9, 23900, True), (6, 21000, False)], relaxed=True),
        llm_classifier_packed_chunks=2,
        llm_classifier_output_split_threshold=_THRESHOLD_OLD,
        llm_classifier_output_ceiling=_CEILING_OLD,
        llm_classifier_output_tokens_estimated=15000,
        # MEASURED AGAINST THE CONFIGURED BUDGET, NOT THE RELAXED ONE. The
        # packing query above reads this run at 23900/24000 = 0.9958, which is
        # the packer comfortably inside the budget it RAISED ITSELF TO. Here
        # the same chunk reads 23900/20000 = 1.195 against what was
        # CONFIGURED, and the relaxation is exactly the pressure that ratio is
        # reporting. The two numbers are both true and answer different
        # questions; the seed carries both so the difference is visible.
        llm_classifier_input_tokens_estimated=23900,
        llm_classifier_input_budget=20000,
        llm_classifier_truncation_splits=1,
        llm_classifier_call_details=_call_details_blob(15900, 4000))),
    # One past the fusion-pool cap. Counts otherwise consistent, so this row can
    # only be flagged for the reason it is here for.
    ("P-CAP-RETRIEVAL", dict(
        matching_model=_MODEL_A, llm_classifier_input_tokens=1000,
        llm_classifier_output_tokens=500, llm_classifier_reasoning_tokens=None,
        estimated_cost_usd=0.008, age=58,
        candidates_retrieved=RRF_POOL_SIZE + 1, candidates_reranked=40,
        candidates_filtered=5, candidates_evaluated=5,
        eligible_matches=2, near_misses=2, not_evaluable_trials=1)),
    # One past the rerank cap, same discipline.
    ("P-CAP-RERANK", dict(
        matching_model=_MODEL_A, llm_classifier_input_tokens=1100,
        llm_classifier_output_tokens=520, llm_classifier_reasoning_tokens=None,
        estimated_cost_usd=0.009, age=59,
        candidates_retrieved=100, candidates_reranked=TOP_K_CANDIDATES + 1,
        candidates_filtered=5, candidates_evaluated=5,
        eligible_matches=2, near_misses=2, not_evaluable_trials=1)),
    # Counters absent. Under three-valued logic every comparison against these
    # is NULL, so before item 38 this row reached ELSE 'OK' and was reported as
    # consistent.
    ("P-NULL-COUNTERS", dict(
        matching_model=_MODEL_A, llm_classifier_input_tokens=100,
        llm_classifier_output_tokens=50, llm_classifier_reasoning_tokens=None,
        estimated_cost_usd=0.001, age=48,
        candidates_retrieved=None, candidates_reranked=None,
        candidates_filtered=None, candidates_evaluated=None,
        eligible_matches=None, near_misses=None, not_evaluable_trials=None)),
    # A failed run. Makes `error_types` non-empty, and retrieved > 0 with
    # evaluated == 0 makes `extreme_cases` non-empty for a second reason.
    ("P-ERROR", dict(
        matching_model=_MODEL_A, llm_classifier_input_tokens=0, llm_classifier_output_tokens=0,
        llm_classifier_reasoning_tokens=None, estimated_cost_usd=0.0, age=70,
        error="Stage 5 timeout after 300s",
        # THE FAILED ROW REPORTS REAL PRESSURE. Before era 6 it could not:
        # llm_classifier_packing is published on Stage 5's SUCCESS return only,
        # so a run that failed carried no input figure -- and a run that failed
        # BECAUSE its input was enormous is the row most worth asking. 13,000
        # against a 12,000 budget is above 1.0 on purpose: the packer relaxes
        # its budget when the cap binds and a single oversized trial ships
        # anyway, so pressure > 1 is a real reading and not a seed error.
        llm_classifier_input_tokens_estimated=13000,
        llm_classifier_input_budget=12000,
        candidates_retrieved=100, candidates_reranked=40,
        candidates_filtered=0, candidates_evaluated=0,
        eligible_matches=0, near_misses=0, not_evaluable_trials=0)),
    # A PRE-MIGRATION row: not_evaluable_trials NULL, and evaluated equal to
    # eligible + near_misses. The weak branch must NOT flag it.
    ("P-LEGACY-OK", dict(
        matching_model=_MODEL_A, llm_classifier_input_tokens=800,
        llm_classifier_output_tokens=400, llm_classifier_reasoning_tokens=None,
        estimated_cost_usd=0.006, age=64,
        candidates_retrieved=90, candidates_reranked=35,
        candidates_filtered=9, candidates_evaluated=9,
        eligible_matches=4, near_misses=5, not_evaluable_trials=None)),
    # A PRE-MIGRATION row that is provably wrong even without the third term:
    # 9 evaluated cannot be fewer than 6 + 5.
    ("P-LEGACY-BAD", dict(
        matching_model=_MODEL_A, llm_classifier_input_tokens=810,
        llm_classifier_output_tokens=410, llm_classifier_reasoning_tokens=None,
        estimated_cost_usd=0.006, age=65,
        candidates_retrieved=90, candidates_reranked=35,
        candidates_filtered=9, candidates_evaluated=9,
        eligible_matches=6, near_misses=5, not_evaluable_trials=None)),
]

# MORE INCONSISTENT ROWS THAN THE LISTING CAN SHOW, ACROSS TWO CATEGORIES.
#
# Without these the whole companion-query question is untestable: a totals query
# that agrees with a listing which never hit its cap agrees for the wrong reason,
# and "20 issues" versus "20 of 400" is precisely the confusion the companion
# exists to remove. The named rows above contribute five issues; these add
# CONSISTENCY_LISTING_LIMIT more, split across two categories, so the total is
# comfortably past the cap and neither category alone fills it.
#
# Every one of these is consistent in EVERY respect but the one it is here for,
# so a row appearing under the wrong category is a real failure rather than an
# ambiguity.
_BULK_COUNT_MISMATCH = queries.CONSISTENCY_LISTING_LIMIT // 2 + 3   # 13
_BULK_RETRIEVAL_ANOMALY = queries.CONSISTENCY_LISTING_LIMIT - _BULK_COUNT_MISMATCH + 3  # 10

for _n in range(_BULK_COUNT_MISMATCH):
    _SEED_ROWS.append((f"P-BULK-COUNT-{_n:03d}", dict(
        matching_model=_MODEL_A, llm_classifier_input_tokens=700 + _n,
        llm_classifier_output_tokens=300 + _n, llm_classifier_reasoning_tokens=None,
        estimated_cost_usd=0.005, age=40 + _n,
        candidates_retrieved=95, candidates_reranked=38,
        candidates_filtered=12, candidates_evaluated=12,
        # 4 + 4 + 1 == 9, not 12.
        eligible_matches=4, near_misses=4, not_evaluable_trials=1)))

for _n in range(_BULK_RETRIEVAL_ANOMALY):
    _SEED_ROWS.append((f"P-BULK-RETRIEVAL-{_n:03d}", dict(
        matching_model=_MODEL_A, llm_classifier_input_tokens=650 + _n,
        llm_classifier_output_tokens=280 + _n, llm_classifier_reasoning_tokens=None,
        estimated_cost_usd=0.005, age=45 + _n,
        candidates_retrieved=RRF_POOL_SIZE + 2 + _n, candidates_reranked=38,
        candidates_filtered=11, candidates_evaluated=11,
        eligible_matches=5, near_misses=5, not_evaluable_trials=1)))


# Which rows the consistency query must flag, and with what. Written beside the
# seed above rather than derived from the query, so the expectation is
# independent of the implementation it checks.
_EXPECTED_ISSUES = {
    "P-COUNT-MISMATCH": "Count mismatch",
    "P-CAP-RETRIEVAL":  "Retrieval anomaly",
    "P-CAP-RERANK":     "Rerank anomaly",
    "P-NULL-COUNTERS":  "Counters not reported",
    "P-LEGACY-BAD":     "Count mismatch",
}
_EXPECTED_ISSUES.update(
    {f"P-BULK-COUNT-{_n:03d}": "Count mismatch"
     for _n in range(_BULK_COUNT_MISMATCH)})
_EXPECTED_ISSUES.update(
    {f"P-BULK-RETRIEVAL-{_n:03d}": "Retrieval anomaly"
     for _n in range(_BULK_RETRIEVAL_ANOMALY)})

# The per-category totals the companion query must reproduce, counted from the
# expectation rather than from the query.
_EXPECTED_ISSUE_COUNTS = {}
for _issue in _EXPECTED_ISSUES.values():
    _EXPECTED_ISSUE_COUNTS[_issue] = _EXPECTED_ISSUE_COUNTS.get(_issue, 0) + 1

_cursor = _conn.cursor()
_INFERENCE_IDS = {}
for _label, _overrides in _SEED_ROWS:
    _row = dict(_BASE_ROW)
    _row.update(_overrides)
    _row["patient_id"] = _label
    _columns = [c for c in _row if c in _SCHEMA_COLUMNS]
    _cursor.execute(
        f"INSERT INTO inferences ({', '.join(_columns)}) "
        f"VALUES ({', '.join('?' for _ in _columns)})",
        [_row[c] for c in _columns])
    _INFERENCE_IDS[_label] = _cursor.lastrowid

# A column named in the seed that the schema does not have would be dropped by
# the filter above WITHOUT A WORD, which is the same silent-omission shape this
# item is removing. Checked rather than trusted.
_unknown_seed_columns = sorted(
    set(_BASE_ROW) | {k for _, o in _SEED_ROWS for k in o}
)
_unknown_seed_columns = [c for c in _unknown_seed_columns
                         if c not in _SCHEMA_COLUMNS]
check("every column the seed writes exists in the real schema",
      _unknown_seed_columns, [])

# THE FOUR ROWS ALSO CARRY THE STAGE 5 NORMALIZER PROVENANCE, AND THE VALUES
# ARE CHOSEN SO THE FOUR QUERIES OVER THEM CANNOT PASS FOR THE WRONG REASON.
# Every one of those queries groups on a COALESCE label that names the absence,
# so a seed leaving all five columns NULL would put every row in the
# "(not reported)" / "(not checked)" bucket and the non-empty check in section 2
# would be satisfied without a single measured branch ever being exercised.
# Row 0 is canonical and clean, row 1 is a recovered boolean label with two
# criterion remaps, row 2 is an entry the pipeline CONSTRUCTED (every column
# NULL, which is the population the reader has to be able to select), and row 3
# is an unreadable label that still ended eligible off its criteria.
# THE OWNING PATIENT IS NAMED PER ROW rather than derived from the index. It was
# `"P-CONSISTENT-A" if _i < 2 else "P-CONSISTENT-B"`, which is a rule that
# silently reassigns every row after any insertion -- and the omission row below
# has to hang off the GROUPED patient specifically, because an omission is a
# trial sent inside a BATCH and not answered for, which per-trial mode cannot
# produce by construction.
_TRIAL_MATCH_ROWS = [
    # patient, nct, phase, eligible, score, ne_reason, v_source, v_label,
    # v_type, remaps
    ("P-CONSISTENT-A", "NCT00000001", "Phase 2", "eligible", 0.91,
     None, "canonical", None, None, 0),
    ("P-CONSISTENT-A", "NCT00000002", "Phase 3", "not_eligible", 0.42,
     None, "normalized", "True", "bool", 2),
    ("P-CONSISTENT-B", "NCT00000003", "Phase 1", "not_evaluable", 0.55,
     "truncation_floor", None, None, None, None),
    ("P-CONSISTENT-B", "NCT00000001", "Phase 2", "eligible", 0.88,
     None, "unrecognized", "'MAYBE'", "str", 0),
    # THE OMISSION. Without it `call_mode_comparison`'s omission total is 0 on
    # every arm and every check over it compares 0 with 0 -- the vacuous shape
    # this project treats as no check at all. It is CONSTRUCTED by the pipeline,
    # so its verdict_source is NULL, which is what `not_evaluable_reasons`'
    # family CASE and its `never_had_a_model_label` column already assert about
    # this class.
    ("P-CONSISTENT-A", "NCT00000004", "Phase 2", "not_evaluable", 0.31,
     "omitted_from_model_response", None, None, None, None),
]

for _i, (_owner, _nct, _phase, _eligible, _score, _ne_reason, _v_source,
         _v_label, _v_type, _remaps) in enumerate(_TRIAL_MATCH_ROWS):
    _cursor.execute(
        "INSERT INTO trial_matches (inference_id, nct_id, trial_title, "
        "trial_phase, trial_number, rerank_score, match_score, eligible, "
        "assessment, criterion_details, not_evaluable_reason, verdict_source, "
        "verdict_original_label, verdict_original_type, criterion_remaps) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_INFERENCE_IDS[_owner],
         _nct, f"Trial {_nct}", _phase, _i + 1, 3.5 - _i * 0.1, _score,
         _eligible, "because", '{"inclusion": [], "exclusion": []}',
         _ne_reason, _v_source, _v_label, _v_type, _remaps))

# ...and the two run-level counters on the inferences the rows hang off, so
# `run_normalizer_provenance` has a row whose HAVING is satisfied by a measured
# count rather than only by the presence of a not_evaluable child. The remap
# EVENT total on P-CONSISTENT-B is deliberately left at the _BASE_ROW's 0 while
# its child rows sum to 0 as well (rows 2 and 3 carry NULL and 0), so the
# consistency column that query renders agrees on the seeded data.
_cursor.execute(
    "UPDATE inferences SET verdict_normalizations = ?, remapped_trials = ?, "
    "cross_vocab_remaps = ? WHERE patient_id = ?", (1, 1, 2, "P-CONSISTENT-A"))
_cursor.execute(
    "UPDATE inferences SET verdict_normalizations = ?, remapped_trials = ? "
    "WHERE patient_id = ?", (1, 0, "P-CONSISTENT-B"))

for _i, (_cat, _name, _value, _alert) in enumerate([
        ("performance", "total_time", 64.2, 0),
        ("retrieval", "candidates_retrieved", 91.0, 1),
        ("cost", "estimated_cost_usd", 0.08, 0)]):
    _cursor.execute(
        "INSERT INTO drift_metrics (timestamp, metric_category, metric_name, "
        "metric_value, baseline_mean, baseline_std, p_value, z_score, "
        "threshold, alert, baseline_window_days, comparison_window_days, notes)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (f"2026-08-0{_i + 1} 09:00:00", _cat, _name, _value, _value * 0.9,
         _value * 0.1, 0.03, 2.4 - _i, 3.0, _alert, 30, 7, "seeded"))

# THE RUN TABLES. Four runs, chosen so that every branch of the two run queries'
# shared CASE fragments is exercised by a row -- a registry that returns a frame
# for the wrong reason is what section 2's non-emptiness check cannot see.
#
#   RUN-CLEAN    FINISHED, finalized, has patients, meta rows say 22 counters
#                consulted and 0 moved  -> 'measured clean'
#   RUN-CRASHED  RUNNING with a NULL finished_at and no meta row at all -- the
#                shape a killed process leaves. -> 'RUNNING, no finished_at' and
#                'no health record'
#   RUN-EMPTY    KILLED and finalized, and NO inference row references it. This
#                is the row an INNER JOIN would delete, so it is what makes the
#                LEFT JOIN in `run_summary` load-bearing rather than incidental.
#   RUN-DEGRADED FINISHED with two non-zero counters -> 'degraded', and the only
#                run that contributes a row to the breakdown's non-NULL arm.
#
# A FIFTH SHAPE IS DELIBERATELY PRESENT AND IS NOT A RUN: the eleven-plus
# inference rows seeded above keep their NULL run_id, which is what every row
# written before the run-identity pass has. `run_summary` must not invent a run
# for them, and section 2b asserts it does not.
# THE ARM EACH RUN WAS STAMPED WITH IS PART OF THE SEED, AND RUN-CLEAN'S IS
# CHOSEN TO PRODUCE ALL THREE AGREEMENT STATES FROM ONE RUN. It is stamped
# `grouped` and owns three patient rows: one written `grouped` (the stamp
# matches), one written `per_trial` (the stamp DISAGREES, which is a run whose
# flag moved mid-process and which nothing in this project could state before)
# and one with no recorded arm at all. A seed in which the stamp and the rows
# always agree cannot tell a query that compares them from one that reports the
# stamp twice.
_RUN_ROWS = [
    # label, status, finished_at, invocation_source, stamped arm
    ("RUN-CLEAN",    "FINISHED", "2026-08-20T11:04:00", "batch_runner",
     MATCHING_CALL_MODE_GROUPED),
    ("RUN-CRASHED",  "RUNNING",  None,                  "batch_runner",
     MATCHING_CALL_MODE_PER_TRIAL),
    ("RUN-EMPTY",    "KILLED",   "2026-08-18T10:05:00", "batch_runner",
     MATCHING_CALL_MODE_GROUPED),
    # NO ARM AT ALL: the shape of every `runs` row written before era 4. It must
    # read '(not recorded)' and never 'grouped'.
    ("RUN-DEGRADED", "FINISHED", "2026-08-17T11:00:00", "batch_runner", None),
]
_RUN_IDS = {}
for _i, (_label, _status, _finished, _source, _arm) in enumerate(_RUN_ROWS):
    _cursor.execute(
        "INSERT INTO runs (started_at, finished_at, status, invocation_source, "
        "fingerprint_version, llm_classifier_prompt_version, "
        "llm_classifier_renderer_digest, matching_model_configured, "
        "matching_call_mode, qdrant_collection, collection_points, "
        "data_snapshot_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (f"2026-08-{20 - _i}T10:00:00", _finished, _status, _source,
         2, "1.9.0", f"digest-{_i}", "gpt-5.6-terra", _arm,
         "trial_criteria_20260807_111807", 12067, "2026-02-26"))
    _RUN_IDS[_label] = _cursor.lastrowid

# Which seeded patients belong to which run. Chosen off the named rows above so
# the expected patient counts and costs are written here rather than read back
# out of the query being checked.
_RUN_MEMBERSHIP = {
    "RUN-CLEAN":    ["P-CONSISTENT-A", "P-CONSISTENT-B", "P-NULL-TOKENS",
                     "P-BYPASSED"],
    "RUN-CRASHED":  ["P-ERROR", "P-NOMODEL-CLEAN"],
    "RUN-DEGRADED": ["P-COUNT-MISMATCH"],
    # RUN-EMPTY intentionally absent -- see above.
}
for _label, _patients in _RUN_MEMBERSHIP.items():
    for _patient in _patients:
        _cursor.execute("UPDATE inferences SET run_id = ? WHERE patient_id = ?",
                        (_RUN_IDS[_label], _patient))

# THE DANGLING ROW IS SEEDED IN `_SEED_ROWS` (search P-DANGLING), not inserted
# here. Two earlier attempts inserted it after the fact and both disturbed
# expectations that are DERIVED by iterating `_SEED_ROWS` -- the per-model token
# sums, the row count, the consistency classification -- because a row the seed
# list does not know about is a row those derivations cannot account for. Being
# in the list is what makes it ordinary everywhere except in the one respect it
# is about.
if _conn.execute("SELECT COUNT(*) FROM runs WHERE id = ?",
                 (_DANGLING_RUN_ID,)).fetchone()[0]:
    raise SystemExit("seed error: the dangling run id names a real run row")
if _conn.execute("SELECT COUNT(*) FROM inferences WHERE run_id = ?",
                 (_DANGLING_RUN_ID,)).fetchone()[0] != 1:
    raise SystemExit("seed error: the dangling inference row was not seeded")

# THE COUNTER NAMES ARE REAL REGISTERED ONES, not invented strings: a breakdown
# rendering a name no counter has ever had would look identical to one rendering
# a real name, and the point of the column is that an operator recognises it.
_RUN_METRIC_ROWS = [
    ("RUN-CLEAN",    dblog.RUN_METRIC_CATEGORY_META,
     dblog.RUN_METRIC_META_COUNTERS_REGISTERED, 22),
    ("RUN-CLEAN",    dblog.RUN_METRIC_CATEGORY_META,
     dblog.RUN_METRIC_META_COUNTERS_NONZERO, 0),
    ("RUN-DEGRADED", dblog.RUN_METRIC_CATEGORY_META,
     dblog.RUN_METRIC_META_COUNTERS_REGISTERED, 22),
    ("RUN-DEGRADED", dblog.RUN_METRIC_CATEGORY_META,
     dblog.RUN_METRIC_META_COUNTERS_NONZERO, 2),
    ("RUN-DEGRADED", dblog.RUN_METRIC_CATEGORY_DEGRADATION,
     "AGE_PARSE_FAILURES", 412),
    ("RUN-DEGRADED", dblog.RUN_METRIC_CATEGORY_DEGRADATION,
     "QDRANT_RETRIES", 3),
]
for _label, _category, _name, _value in _RUN_METRIC_ROWS:
    _cursor.execute(
        "INSERT INTO run_metrics (run_id, category, name, value, written_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (_RUN_IDS[_label], _category, _name, _value, "2026-08-20T11:04:00"))

# ── THE TWO ORPHANS (the database-completion pass) ─────────────────────────
#
# This schema declares three foreign keys and enforces none of them. A row on
# the wrong side of each is therefore REACHABLE, and `orphan_trial_matches` and
# `orphan_run_metrics` are the only things in the project that can report one --
# so the seed has to contain one of each, or those two queries' non-empty case
# ships exercised by nothing. `dangling_run_references`' P-DANGLING row above is
# the same argument for the third.
#
# THEY ARE SEEDED AFTER `_TRIAL_MATCH_ROWS` AND `_RUN_METRIC_ROWS` RATHER THAN
# IN THEM, which is the opposite of what P-DANGLING does, and the reason is that
# the two lists are used for different things. `_SEED_ROWS` is ITERATED to
# derive expectations -- per-model token sums, the consistency classification --
# so a row outside it is a row those derivations cannot account for, which is
# why P-DANGLING is in it. These two are never iterated for anything except the
# row counts immediately below, which name them.
#
# THE OWNER IDS ARE FAR OUTSIDE ANYTHING AUTOINCREMENT WILL REACH in this file,
# and both are asserted absent from their parent table after the seed.
_ORPHAN_INFERENCE_ID = 888_888
_ORPHAN_RUN_ID = 777_777

_cursor.execute(
    "INSERT INTO trial_matches (inference_id, nct_id, trial_title, "
    "trial_phase, trial_number, rerank_score, match_score, eligible, "
    "assessment, criterion_details) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    (_ORPHAN_INFERENCE_ID, "NCT09999999", "Trial NCT09999999", "Phase 1", 1,
     1.0, 0.5, "eligible", "because",
     '{"inclusion": [], "exclusion": []}'))

_cursor.execute(
    "INSERT INTO run_metrics (run_id, category, name, value, written_at) "
    "VALUES (?, ?, ?, ?, ?)",
    (_ORPHAN_RUN_ID, dblog.RUN_METRIC_CATEGORY_DEGRADATION,
     "QDRANT_RETRIES", 9, "2026-08-20T11:05:00"))

_conn.commit()

if _conn.execute("SELECT COUNT(*) FROM inferences WHERE id = ?",
                 (_ORPHAN_INFERENCE_ID,)).fetchone()[0]:
    raise SystemExit("seed error: the orphan inference_id names a real row")
if _conn.execute("SELECT COUNT(*) FROM runs WHERE id = ?",
                 (_ORPHAN_RUN_ID,)).fetchone()[0]:
    raise SystemExit("seed error: the orphan run_id names a real run row")

check("the seed wrote every inference row",
      _conn.execute("SELECT COUNT(*) FROM inferences").fetchone()[0],
      len(_SEED_ROWS))
check("...and the trial_matches rows, plus the one orphan",
      _conn.execute("SELECT COUNT(*) FROM trial_matches").fetchone()[0],
      len(_TRIAL_MATCH_ROWS) + 1)
check("...and the run_metrics rows, plus the one orphan",
      _conn.execute("SELECT COUNT(*) FROM run_metrics").fetchone()[0],
      len(_RUN_METRIC_ROWS) + 1)
check("...and the drift_metrics rows",
      _conn.execute("SELECT COUNT(*) FROM drift_metrics").fetchone()[0], 3)
check_true("the models the seed prices are all in PRICING_CONFIG "
           "(non-degeneracy: an unpriced one would make section 5 raise for "
           "the wrong reason)",
           _MODEL_A in _PRICED_MODELS and _MODEL_B in _PRICED_MODELS)


# ===========================================================================
# SECTION 2 -- EVERY QUERY RUNS, AND NONE OF THEM RUNS EMPTY
# ===========================================================================

print()
print("=" * 78)
print("SECTION 2 -- every query executes, non-empty, and report() completes")
print("=" * 78)

check_true("the registry is non-degenerate (non-empty, keys unique)",
           len(queries.QUERY_KEYS) > 30
           and len(set(queries.QUERY_KEYS)) == len(queries.QUERY_KEYS))

_all = queries.run_all(_conn, stop_on_error=False)
_raised = {k: v for k, v in _all.items() if isinstance(v, Exception)}
check("EVERY query in the registry executes without raising",
      sorted(f"{k}: {type(v).__name__}" for k, v in _raised.items()), [])

# NON-DEGENERACY. A query that silently comes back with nothing looks exactly
# like a query that ran and found nothing, and on THIS seed there is nothing
# any of them should legitimately find nothing about -- the rows were chosen so
# that each query has something to report. An empty frame here is a failure.
_empty = sorted(k for k, v in _all.items()
                if not isinstance(v, Exception) and len(v) == 0)
check("...and every one of them returns a NON-EMPTY frame on the seeded data",
      _empty, [])

check("run() on an unknown key raises KeyError rather than returning nothing",
      type(check_raises("  (unknown key)", KeyError, queries.run, _conn, "nope")
            ).__name__, "KeyError")

_report_lines = []
_report = check_does_not_raise(
    "report() runs end to end -- the first time in this project's history",
    queries.report, _conn, out=_report_lines.append)
check("...and it returns a result for every query in the registry",
      sorted(_report or {}), sorted(queries.QUERY_KEYS))
check_true("...having actually printed something (non-degeneracy)",
           len(_report_lines) > 40)

_report_text = "\n".join(str(line) for line in _report_lines)
for _expected in ("=== PIPELINE CONSISTENCY ISSUES ===",
                  "=== COST BREAKDOWN BY MODEL ===",
                  "=== EXPANSION (STAGE 1) STATS ===",
                  "=== LATEST DRIFT RUN ==="):
    check_true(f"report() reached {_expected!r}", _expected in _report_text)


# ===========================================================================
# SECTION 2b -- THE RUN TABLES (the run-reader pass)
# ===========================================================================
#
# Section 2 above proves the two run queries EXECUTE and come back non-empty.
# That is necessary and it is not enough: a `run_summary` that dropped its LEFT
# JOINs, or multiplied its aggregates across three children, or read
# `counters_registered` as "clean", satisfies it exactly. This section asserts
# the VALUES, computed here from the seed rather than read out of the query.

print()
print("=" * 78)
print("SECTION 2b -- runs and run_metrics")
print("=" * 78)

# THROUGH THE GUARD. `run_summary` declares `runs.resumed`, so on a database
# missing it `queries.run` raises MissingTableError BY DESIGN -- which is the
# state a broken migration produces and which this file must REPORT rather than
# abort on. Measured: reverting the `runs` migration aborted this file here.
_summary = _frame_or_raise("run_summary")
_summary_by_id = _RowIndex((int(r.run_id), r)
                           for r in _summary.itertuples())

check("run_summary returns exactly one row per run, and no more",
      len(_summary), len(_RUN_ROWS))
check("...keyed by every seeded run id",
      sorted(_summary_by_id), sorted(_RUN_IDS.values()))
check("...newest first, which is what ORDER BY r.id DESC means",
      list(_summary["run_id"]), sorted(_RUN_IDS.values(), reverse=True))

# --- THE ROW AN INNER JOIN WOULD DELETE -----------------------------------
_empty_run = _summary_by_id[_RUN_IDS["RUN-EMPTY"]]
check("a run NO inference row references is still in run_summary "
      "(this is what the LEFT JOIN buys)",
      _safe_int(_empty_run.run_id), _RUN_IDS["RUN-EMPTY"])
check("...with patients 0 -- a measured zero, not a NULL",
      _safe_int(_empty_run.patients), 0)
check("...and cost 0.0", _safe_float(_empty_run.cost_usd), 0.0)

# --- PATIENT ROLLUP, COUNTED FROM THE SEED --------------------------------
for _label, _patients in _RUN_MEMBERSHIP.items():
    _row = _summary_by_id[_RUN_IDS[_label]]
    check(f"{_label}: patients counted from the seed, not from the join",
          _safe_int(_row.patients), len(_patients))
    # THE MULTIPLICATION CONTROL. RUN-DEGRADED carries two degradation rows and
    # a meta pair; a naive three-way join would report its patient count
    # multiplied by four. A run with one patient makes that visible as 4.
    _expected_cost = round(sum(
        _conn.execute("SELECT COALESCE(estimated_cost_usd, 0) FROM inferences "
                      "WHERE patient_id = ?", (_p,)).fetchone()[0]
        for _p in _patients), 4)
    check(f"{_label}: cost_usd is the sum over its patients and nothing else",
          _safe_float(_row.cost_usd, 4), _expected_cost)

check_true("the patient counts are non-degenerate -- the three runs with "
           "patients do not all have the same number, so a multiplied or "
           "constant answer could not pass the checks above",
           len({len(v) for v in _RUN_MEMBERSHIP.values()}) > 1)

# P-NULL-TOKENS is in RUN-CLEAN and carries a NULL estimated_cost_usd, so the
# floor and the count that qualifies it must both be right.
_clean_row = _summary_by_id[_RUN_IDS["RUN-CLEAN"]]
check("an unpriced patient is counted, not silently folded into the total "
      "(cost_complete's rule, one table up)",
      _safe_int(_clean_row.rows_with_no_cost), 1)
check("...and the errored count is the seeded one",
      _safe_int(_clean_row.errored),
      _conn.execute(
          "SELECT COUNT(*) FROM inferences WHERE run_id = ? "
          "AND error IS NOT NULL AND error != ''",
          (_RUN_IDS["RUN-CLEAN"],)).fetchone()[0])

# --- health_record: THE THREE STATES, AND THEY ARE NOT THE SAME -----------
_expected_health = {
    "RUN-CLEAN":    queries.RUN_HEALTH_MEASURED_CLEAN,
    "RUN-CRASHED":  queries.RUN_HEALTH_NEVER_FLUSHED,
    "RUN-EMPTY":    queries.RUN_HEALTH_NEVER_FLUSHED,
    "RUN-DEGRADED": queries.RUN_HEALTH_DEGRADED,
}
for _label, _expected in _expected_health.items():
    check(f"{_label}: health_record",
          _summary_by_id[_RUN_IDS[_label]].health_record, _expected)

check_true("the three health_record states are all exercised (non-degeneracy: "
           "a CASE stuck on one arm would satisfy every check above that "
           "expects that arm)",
           set(_expected_health.values()) == set(queries.RUN_HEALTH_STATES))

check("a measured-clean run reports counters_nonzero = 0, which is the "
      "MEASUREMENT that separates it from a run that never flushed",
      _safe_int(_clean_row.counters_nonzero), 0)
check("...and a run that never flushed reports it as NULL, not 0",
      bool(pd.isna(_summary_by_id[_RUN_IDS["RUN-CRASHED"]].counters_nonzero)),
      True)

# --- finalization: the crashed shape is FLAGGED, not hidden ---------------
_expected_final = {
    "RUN-CLEAN":    queries.RUN_FINALIZATION_FINALIZED,
    "RUN-CRASHED":  queries.RUN_FINALIZATION_LIVE_OR_DIED,
    "RUN-EMPTY":    queries.RUN_FINALIZATION_FINALIZED,
    "RUN-DEGRADED": queries.RUN_FINALIZATION_FINALIZED,
}
for _label, _expected in _expected_final.items():
    check(f"{_label}: finalization", 
          _summary_by_id[_RUN_IDS[_label]].finalization, _expected)

# THE THIRD FINALIZATION STATE HAS NO SEEDED RUN, because a terminal status
# with a NULL finished_at is a shape finalize_run_record cannot produce. It is
# driven here directly rather than left unexercised -- an arm of a closed
# vocabulary that no test reaches is an arm nobody has ever seen fire.
_cursor.execute(
    "INSERT INTO runs (started_at, finished_at, status, invocation_source) "
    "VALUES (?, ?, ?, ?)",
    ("2026-08-10T10:00:00", None, "FAILED", "batch_runner"))
_UNSTAMPED_RUN = _cursor.lastrowid
_conn.commit()
_summary2 = _frame_or_raise("run_summary")
_unstamped = _summary2[_summary2["run_id"] == _UNSTAMPED_RUN]
check("a terminal status with no finished_at is named as its own state, not "
      "folded into 'RUNNING or died'",
      list(_unstamped["finalization"]), [queries.RUN_FINALIZATION_NOT_STAMPED])
check_true("...so all three finalization states are exercised",
           set(_expected_final.values()) | {queries.RUN_FINALIZATION_NOT_STAMPED}
           == set(queries.RUN_FINALIZATION_STATES))
_cursor.execute("DELETE FROM runs WHERE id = ?", (_UNSTAMPED_RUN,))
_conn.commit()
check("...and the probe row is removed, so every later check sees the seed",
      len(_frame_or_raise("run_summary")), len(_RUN_ROWS))

# --- ROWS WITH NO RUN ARE NOT INVENTED INTO ONE ---------------------------
_orphan_rows = _conn.execute(
    "SELECT COUNT(*) FROM inferences WHERE run_id IS NULL").fetchone()[0]
check_true("the seed has inference rows with a NULL run_id (non-degeneracy: "
           "without them the next check passes for free)", _orphan_rows > 0)
# THE EXPECTATION IS "ROWS WHOSE run_id NAMES A RUN THAT EXISTS", NOT "ROWS
# THAT CARRY A run_id", AND THE TWO USED TO BE THE SAME NUMBER.
#
# Before the seed carried a dangling row, every non-NULL run_id named a real
# run, so `WHERE run_id IS NOT NULL` and "attributable" counted the same rows
# and the check could not tell a correct run_summary from one that counted any
# non-NULL id. The dangling row separates them: it carries a run_id and must NOT
# be attributed, so this now fails against a run_summary that dropped its JOIN.
check("run_summary attributes no patient to a run that does not exist -- the "
      "sum over its patients column is the count of rows whose run_id names a "
      "run that EXISTS, which the dangling row makes a smaller number than the "
      "count of rows carrying one",
      _safe_int(_summary["patients"].sum()),
      _conn.execute("SELECT COUNT(*) FROM inferences i "
                    "JOIN runs r ON r.id = i.run_id").fetchone()[0])
check_true("...and the two counts really do differ, so the line above is not "
           "the pre-dangling check wearing a new expression",
           _conn.execute("SELECT COUNT(*) FROM inferences "
                         "WHERE run_id IS NOT NULL").fetchone()[0]
           > _safe_int(_summary["patients"].sum()))

# --- THE BREAKDOWN --------------------------------------------------------
_break = _frame_or_raise("run_degradation_breakdown")
_break_by_run = {}
for _r in _break.itertuples():
    _break_by_run.setdefault(int(_r.run_id), []).append((_r.counter, _r.events))

check("run_degradation_breakdown has a row for EVERY run, clean ones included",
      sorted(_break_by_run), sorted(_RUN_IDS.values()))
check("a clean run appears with the named no-counter label rather than being "
      "omitted",
      [_c for _c, _e in _break_by_run[_RUN_IDS["RUN-CLEAN"]]],
      [queries.RUN_HEALTH_NO_COUNTER_LABEL])
# pd.isna, NOT `is None`. The column holds numbers for other runs, so pandas
# makes it float64 and a SQL NULL arrives as nan -- which is TRUTHY and is not
# equal to None. That is the same trap this file's section 5 exists for, met in
# a new column, and the first version of this check compared against None.
check("...and its events cell is NULL, because no counter reported a number",
      [bool(pd.isna(_e)) for _c, _e in _break_by_run[_RUN_IDS["RUN-CLEAN"]]],
      [True])
check("the degraded run's counters come back worst-first, with the seeded "
      "totals",
      [(_c, int(_e)) for _c, _e in _break_by_run[_RUN_IDS["RUN-DEGRADED"]]],
      [("AGE_PARSE_FAILURES", 412), ("QDRANT_RETRIES", 3)])
check("the breakdown's per-run event total matches run_summary's, which are "
      "two different SQL shapes over the same rows",
      int(sum(int(_e) for _c, _e in _break_by_run[_RUN_IDS["RUN-DEGRADED"]])),
      _safe_int(_summary_by_id[_RUN_IDS["RUN-DEGRADED"]].degradation_events))

# --- ATTRIBUTION COVERAGE -------------------------------------------------
_cover = _frame_or_raise("run_attribution_coverage")
_cover_by_label = {r.attribution: r for r in _cover.itertuples()}

check("the coverage census reports the three attributions the seed has",
      sorted(_cover_by_label),
      sorted(queries.RUN_ATTRIBUTION_STATES))
check("...counting every row with a run_id",
      int(_cover_by_label[queries.RUN_ATTRIBUTION_ATTRIBUTED].inference_rows),
      sum(len(v) for v in _RUN_MEMBERSHIP.values()))
check("...and every row without one, which is the population requirement 3 is "
      "about",
      int(_cover_by_label[queries.RUN_ATTRIBUTION_NO_RUN].inference_rows),
      _orphan_rows)
check("...so the census covers the whole table and drops nothing",
      int(_cover["inference_rows"].sum()),
      _conn.execute("SELECT COUNT(*) FROM inferences").fetchone()[0])
check("a run-less row contributes no distinct run id",
      int(_cover_by_label[queries.RUN_ATTRIBUTION_NO_RUN].distinct_run_ids), 0)

# THE DANGLING ROW. The foreign key is unenforced by design, so this state is
# reachable and nothing but `dangling_run_references` can report WHICH ids are
# in it.
#
# IT IS IN THE SEED NOW RATHER THAN SET AND UNDONE HERE. The probe this replaces
# pointed a named patient at a missing run, asserted, and put it back -- which
# left the state exercised for four checks and absent for the rest of the file,
# including the registry-wide "every query returns a non-empty frame" contract
# that `dangling_run_references` has to meet. A permanently seeded row exercises
# all three attribution states for every check in the file and mutates nothing
# mid-run.
_cover2 = {r.attribution: r for r in
           _frame_or_raise("run_attribution_coverage").itertuples()}
check("a row pointing at a runs id that does not exist is named as its own "
      "state, not counted as attributed",
      int(_cover2[queries.RUN_ATTRIBUTION_DANGLING].inference_rows), 1)
check("...and run_summary attributes no patient to it",
      _safe_int(_summary_patients(_frame_or_raise("run_summary"))),
      sum(len(v) for v in _RUN_MEMBERSHIP.values()))
check_true("...so all three attribution states are exercised (non-degeneracy)",
           set(_cover2) == set(queries.RUN_ATTRIBUTION_STATES))

# --- THE AUDIT QUERY NAMES THE ID, WHICH THE CENSUS CANNOT -----------------
#
# THE FRAME IS FETCHED THROUGH A GUARD. `queries.run` raises KeyError for an
# unregistered key -- which is EXACTLY the defect these checks exist to catch --
# so a bare call here would abort the file and report one traceback where it
# owed a summary and ten results. A revert harness proved it: unregistering the
# query aborted this file rather than failing it.
def _frame(key):
    return _frame_or_raise(key)


def _col(frame, name, cast=int):
    """One column as a list, or a named absence. Never raises."""
    try:
        return [cast(v) for v in frame[name]]
    except BaseException as exc:                       # noqa: BLE001 -- reported
        return f"<no column {name}: {type(exc).__name__}>"


_dangling = _frame("dangling_run_references")
check("the audit lists exactly the dangling run ids, which is the question the "
      "census's count cannot answer",
      _col(_dangling, "run_id"), [_DANGLING_RUN_ID])
check("...with the rows attached to each",
      _col(_dangling, "inference_rows"), [1])
check("...and its patient count, so an operator can find them",
      _col(_dangling, "distinct_patients"), [1])
check("...and the spend those rows carry, which is what makes a dangling id "
      "worth chasing rather than deleting",
      _col(_dangling, "cost_usd", lambda v: round(float(v), 4)),
      [_DANGLING_COST])
# A NULL run_id IS NOT A DEFECT and must not be audited: every API request
# writes one, and so did every row written before run tracking existed.
#
# THE FIRST VERSION OF THIS CHECK CONTAINED `_DANGLING_RUN_ID not in (None,)`,
# which is true for every possible value -- a tautology inside an `and` chain,
# so the check could only ever have failed on the two clauses around it. Written
# out as two checks now, each of which can fail on its own.
check_true("the seed really has NULL-run_id rows (non-degeneracy: with none, "
           "the check below cannot distinguish a correct audit from one that "
           "reports nothing)",
           _conn.execute("SELECT COUNT(*) FROM inferences "
                         "WHERE run_id IS NULL").fetchone()[0] > 0)
check("...and the audit reports exactly one id -- the dangling one -- so it "
      "picked up neither the NULL rows nor the attributed ones",
      len(_dangling), 1)
_attributed_ids = {int(v) for v in _RUN_IDS.values()}
check("...and neither is a row whose run_id names a run that exists",
      sorted(_attributed_ids & set(_col(_dangling, "run_id")
                             if isinstance(_col(_dangling, "run_id"), list)
                             else [])), [])

# --- THE OTHER TWO UNENFORCED REFERENCES (the database-completion pass) -----
#
# Three foreign keys are declared and none is enforced, so a row on the wrong
# side of each is reachable and something has to be able to FIND it. The audit
# above is the one for `inferences.run_id`; these are the two for
# `trial_matches.inference_id` and `run_metrics.run_id`.
#
# EACH IS CHECKED FOR WHAT IT FINDS **AND** FOR WHAT IT DOES NOT. An audit that
# reported every row would satisfy "it found the orphan" exactly as well as one
# that works, which is why the attached rows are asserted absent from it -- the
# same pairing `dangling_run_references` carries two checks above.
_orphan_tm = _frame("orphan_trial_matches")
check("the trial audit lists exactly the orphaned inference_id",
      _col(_orphan_tm, "inference_id"), [_ORPHAN_INFERENCE_ID])
check("...with the row count attached, which is what says how much stored, "
      "billed work is invisible to every other query in this registry",
      _col(_orphan_tm, "trial_rows"), [1])
check("...and its distinct trials, so an operator can tell one lost patient "
      "from one lost verdict",
      _col(_orphan_tm, "distinct_trials"), [1])
check("...and it reports exactly one id -- so it picked up none of the "
      "attached rows (non-degeneracy: the seed has several)",
      len(_orphan_tm), 1)
check_true("...and the seed really has attached rows for it to have ignored",
           _conn.execute(
               "SELECT COUNT(*) FROM trial_matches tm "
               "JOIN inferences i ON i.id = tm.inference_id"
           ).fetchone()[0] > 0)

_orphan_rm = _frame("orphan_run_metrics")
check("the health audit lists exactly the orphaned run_id",
      _col(_orphan_rm, "run_id"), [_ORPHAN_RUN_ID])
check("...with the row count attached",
      _col(_orphan_rm, "metric_rows"), [1])
check("...and it reports exactly one id, so it picked up none of the rows "
      "belonging to the two real runs",
      len(_orphan_rm), 1)
check("...and none of the ids it reports is a run that exists",
      sorted(_attributed_ids & set(_col(_orphan_rm, "run_id")
                                   if isinstance(_col(_orphan_rm, "run_id"),
                                                 list) else [])), [])

# THE HARM, STATED AS A MEASUREMENT RATHER THAN AS PROSE. These two queries
# exist because the orphaned rows are invisible to everything else, and the way
# to say that is to ask something else and watch it not see them.
_rm_breakdown = _frame("run_degradation_breakdown")
check("the orphaned health row is ABSENT from run_degradation_breakdown, "
      "which is driven FROM `runs` -- the invisibility orphan_run_metrics "
      "exists to report",
      _ORPHAN_RUN_ID in set(_col(_rm_breakdown, "run", cast=str)
                            if isinstance(_col(_rm_breakdown, "run", cast=str),
                                          list) else []),
      False)

# --- THE MISSING-TABLE GUARD ----------------------------------------------
#
# THE CHECK THAT KEEPS ITEM 38's PROPERTY. Without `requires`, these two queries
# raise `no such table` against any database written before the run-identity
# pass -- which the production one is -- and report() would die there and stop
# executing everything after them in the registry.
#
# THE LEGACY DATABASE IS THE REAL SCHEMA WITH THE RUN TABLES REMOVED, not three
# stub CREATE TABLEs. A stub would make every OTHER query raise `no such column`,
# so `report()` would die for a reason that has nothing to do with what this
# block tests -- which is exactly what the first version of it did.
_LEGACY_DB = os.path.join(_TMP_DIR, "legacy.db")
with quiet():
    initialize_database(_LEGACY_DB)
_legacy_conn = sqlite3.connect(_LEGACY_DB)
_legacy_conn.execute("DROP TABLE runs")
_legacy_conn.execute("DROP TABLE run_metrics")
# THE INDEX HAS TO GO FIRST, and the reason is a property of SQLite worth
# knowing: `ALTER TABLE ... DROP COLUMN` REFUSES to drop a column an index
# references -- "error in index idx_inferences_run_id after drop column: no such
# column: run_id". That is the right behaviour in production (nothing there
# drops a column, and an index is a reason a column cannot silently vanish) and
# an obstacle only here, where a pre-migration shape is FABRICATED by removing
# things. A real database written before the run-identity pass has neither the
# column nor the index, which is exactly the state these two statements produce.
_legacy_conn.execute("DROP INDEX IF EXISTS idx_inferences_run_id")
_legacy_conn.execute("ALTER TABLE inferences DROP COLUMN run_id")
_legacy_conn.commit()
check("the legacy database really lacks the run tables (non-degeneracy: with "
      "them present every check below passes for the wrong reason)",
      sorted(t for t in ("runs", "run_metrics")
             if t in queries.available_tables(_legacy_conn)), [])
check("...and lacks inferences.run_id too, which is the state a database "
      "written before the run-identity pass is actually in",
      "run_id" in {r[1] for r in
                   _legacy_conn.execute("PRAGMA table_info(inferences)")},
      False)

# THE TWO PRESSURE QUERIES ARE HERE TOO AND DECLARE NO TABLE. They read
# `inferences` alone, so nothing on this list is a table for them -- what makes
# them unavailable is `inferences.run_id`, the same absent column run_summary
# names, which is why they appear beside two queries that need whole tables. It
# is also why the non-degeneracy count below cannot be `sum(1 for q if
# q.requires)`: that expression counted TABLE requirements and would silently
# stop describing this list the moment a column-only query joined it.
check("a database with no run tables reports every run query as unavailable",
      sorted(queries.unavailable(_legacy_conn)),
      ["call_mode_comparison", "campaign_summary", "dangling_run_references",
       "orphan_run_metrics",
       "run_attribution_coverage", "run_degradation_breakdown", "run_summary",
       "stage5_cache_effectiveness", "stage5_input_packing_pressure",
       "stage5_input_request_pressure", "stage5_output_split_pressure"])
# `orphan_trial_matches` IS DELIBERATELY NOT ON THAT LIST, and its absence is
# the check. It reads `trial_matches` and `inferences`, both of which every
# database this project has ever written has, so it must stay AVAILABLE here --
# a legacy database is exactly where an orphaned verdict is most likely to be,
# and an audit that declared a requirement it does not have would refuse to look
# at the one file most worth looking at. `dangling_run_references` declares
# `runs` alone for the same reason.
check("...and the trial-orphan audit is NOT unavailable, because it needs "
      "nothing a legacy database lacks",
      "orphan_trial_matches" in queries.unavailable(_legacy_conn), False)
check("...and it still RUNS there and finds the seeded orphan is not in THIS "
      "database (non-degeneracy: an audit that raised would satisfy the line "
      "above by never being asked)",
      len(queries.run(_legacy_conn, "orphan_trial_matches")), 0)
check("...including the call-mode comparison, which is the one that would "
      "otherwise die on `runs` AND on two additive columns -- and killing "
      "report() on a legacy database is exactly the defect item 38 removed",
      "call_mode_comparison" in queries.unavailable(_legacy_conn), True)
# BOTH ABSENT TABLES AND THE ABSENT COLUMN, and the column is named even
# though `runs` is missing too, because `inferences` IS present and its column
# genuinely is not there. One action -- let a writer open the database -- fixes
# all three, which is why they are reported together rather than in stages.
check("...naming the tables AND the column it does not have",
      queries.unavailable(_legacy_conn)["run_summary"],
      queries.RUN_TABLES + ("inferences.run_id",))
check("...and the query that needs no column names only the tables",
      queries.unavailable(_legacy_conn)["run_degradation_breakdown"],
      queries.RUN_TABLES)
# THIS ASKS "IS EVERY UNAVAILABLE QUERY ONE THAT DECLARED SOMETHING", NOT "ARE
# THE TWO COUNTS EQUAL", and the difference is what the schema-guards pass had
# to correct here. The count identity held only while the ONLY declarations in
# the registry were ones this particular database happens to violate -- a
# coincidence, not an invariant. It stopped holding the moment the gpt4o rename
# was declared: this database is a CURRENT one with the run tables and `run_id`
# removed, so it HAS every `llm_classifier_*` column, and 28 queries now declare
# while 5 are unavailable. The comment above already records the author fixing
# an earlier version of the same coincidence (`sum(1 for q if q.requires)`);
# this is the same lesson one step further. A subset test is the property the
# label claims and it cannot go stale as the registry grows.
check("...and no query WITHOUT a declaration is reported unavailable "
      "(non-degeneracy: a check that reported everything would pass the line "
      "above too)",
      sorted(k for k in queries.unavailable(_legacy_conn)
             if not (queries.QUERIES_BY_KEY[k].requires
                     or queries.QUERIES_BY_KEY[k].requires_columns)),
      [])
check("...and strictly more queries declare a requirement than this database "
      "violates, so the line above is a real subset rather than an identity",
      len(queries.unavailable(_legacy_conn))
      < sum(1 for q in queries.QUERIES if q.requires or q.requires_columns),
      True)
check("...and the count is not the whole registry, which is what the line "
      "above would also satisfy if `unavailable` had stopped discriminating",
      len(queries.unavailable(_legacy_conn)) < len(queries.QUERIES), True)
# A COLUMN-ONLY DECLARATION IS REPORTED AS THE COLUMN AND NEVER AS A TABLE.
# `inferences` is present on this database, so a query naming only columns of
# it must come back with column names alone -- the shape that tells an operator
# to let a writer open the file rather than to go looking for a missing table.
check("a query declaring only columns names only columns",
      queries.unavailable(_legacy_conn)["stage5_input_packing_pressure"],
      ("inferences.run_id",))
check("...and the output query names every additive column it reads, in "
      "declaration order",
      queries.unavailable(_legacy_conn)["stage5_output_split_pressure"],
      ("inferences.run_id",))
check("run() on such a database RAISES MissingTableError rather than returning "
      "an empty frame that reads as 'this run tracking recorded nothing'",
      type(check_raises("  (legacy db)", queries.MissingTableError,
                        queries.run, _legacy_conn, "run_summary")).__name__,
      "MissingTableError")
check("...and the seeded database, which HAS the tables, reports none "
      "unavailable (the control: a guard that always fires proves nothing)",
      queries.unavailable(_conn), {})

_legacy_lines = []
_legacy_result = check_does_not_raise(
    "report() on a database with no run tables RUNS rather than dying at the "
    "first one -- item 38's property, defended",
    queries.report, _legacy_conn, out=_legacy_lines.append)
_legacy_text = "\n".join(str(line) for line in _legacy_lines)
check_true("...and it SAYS which queries it skipped, rather than quietly "
           "covering less than its registry",
           "run_summary" in _legacy_text and "run_metrics" in _legacy_text)
check("...and the skipped keys are absent from the returned dict, not present "
      "with an empty frame",
      sorted(k for k in queries.QUERY_KEYS
             if queries.QUERIES_BY_KEY[k].requires
             and k in (_legacy_result or {})),
      [])

# THE COLUMN-ONLY SHAPE, which the `requires` tuple alone cannot survive. Both
# run tables present and `inferences.run_id` absent: the coverage query JOINS on
# that column, so without `requires_columns` it raises `no such column`, takes
# report() down, and everything registered after it stops executing -- the exact
# defect the whole mechanism exists to prevent, one granularity off.
#
# initialize_database creates the column and the tables in ONE call, so this
# shape is not producible by the pipeline today. That is a COUPLING, not an
# invariant, and a guard resting on it would fail in precisely the case it was
# written for.
_COLUMN_DB = os.path.join(_TMP_DIR, "no_run_id.db")
with quiet():
    initialize_database(_COLUMN_DB)
_column_conn = sqlite3.connect(_COLUMN_DB)
# THE INDEX FIRST, for the reason written at the legacy database above: SQLite
# refuses to drop a column an index references.
_column_conn.execute("DROP INDEX IF EXISTS idx_inferences_run_id")
_column_conn.execute("ALTER TABLE inferences DROP COLUMN run_id")
_column_conn.commit()

check("the column-only database HAS both run tables (non-degeneracy: with them "
      "missing this would be the previous case again)",
      sorted(t for t in queries.RUN_TABLES
             if t in queries.available_tables(_column_conn)),
      sorted(queries.RUN_TABLES))
check("...and does not have inferences.run_id",
      "run_id" in queries.table_columns(_column_conn, "inferences"), False)
# THE EXPECTATION IS DERIVED FROM THE DECLARATIONS, not retyped: exactly the
# queries declaring the column are unavailable, and the one that declares only
# the tables is still answerable. Written this way because the first version of
# this check named ONE key from a reading of the SQL and was wrong --
# run_summary's patient rollup joins on the same column, and the failure of this
# section is what found it.
_expects_run_id = sorted(
    q.key for q in queries.QUERIES
    if ("inferences", "run_id") in q.requires_columns)
check_true("more than one query declares inferences.run_id, and at least one "
           "run query does not (non-degeneracy: an all-or-nothing set would "
           "make the check below indistinguishable from a blanket skip)",
           len(_expects_run_id) >= 2
           and any(q.requires and ("inferences", "run_id") not in q.requires_columns
                   for q in queries.QUERIES))
check("exactly the queries that JOIN on the absent column are unavailable -- "
      "the run query that does not is still answerable",
      sorted(queries.unavailable(_column_conn)), _expects_run_id)
check("...and the absence is reported as the COLUMN, named table.column, "
      "rather than as a missing table nobody can add",
      sorted({v for tup in queries.unavailable(_column_conn).values()
              for v in tup}),
      ["inferences.run_id"])
check("run() on it raises MissingTableError rather than the raw sqlite error "
      "that would have escaped report()'s handler",
      type(check_raises("  (no run_id)", queries.MissingTableError,
                        queries.run, _column_conn,
                        "run_attribution_coverage")).__name__,
      "MissingTableError")
_column_lines = []
check_does_not_raise(
    "report() on it still runs the two run queries it CAN answer and reaches "
    "the end",
    queries.report, _column_conn, out=_column_lines.append)
check_true("...having reached the degradation breakdown, which needs neither "
           "`inferences` nor the absent column -- which is what says the skip "
           "was surgical rather than blanket",
           any("RUNS: DEGRADATION BY COUNTER" in str(line)
               for line in _column_lines))
_column_conn.close()

_legacy_conn.close()



# ===========================================================================
# SECTION 2c -- STAGE 5 SPLIT PRESSURE
# ===========================================================================
#
# THE MEASUREMENT IS A JOIN OF TWO HALVES AND ONLY ONE OF THEM IS A COLUMN.
# The INPUT half is derived, in SQL, out of the packing blob the writer already
# stores; the OUTPUT half needs two denominators that are CONFIGURATION and are
# therefore stored. So this section has to prove two different things: that the
# derivation reads the writer's own key names, and that the two new columns
# behave like every other measurement column in this schema -- additive, NULL
# where nothing was measured, and never defaulted.

print()
print("=" * 78)
class _AbsentGroup:
    """A run group the query did not produce, as a VALUE rather than a KeyError.

    ``_grp(_ip, run)`` and ``_grp(_op, run)`` are the natural way to read a per-run frame
    and the wrong way to read one INSIDE a check(): a defect that makes a group
    vanish -- which is exactly what dropping the unmeasured population does --
    raises while check()'s argument is being evaluated, so the run reports one
    traceback where it owed a summary and every check below it. Every attribute
    of this object is a marker string, so the comparison FAILS and names the
    absence instead.
    """

    def __getattr__(self, name):
        return f"<no such run group: .{name} unreachable>"


_ABSENT_GROUP = _AbsentGroup()


def _grp(frame_map, key):
    """One run's row out of a per-run frame, or a named absence."""
    return frame_map.get(key, _ABSENT_GROUP)


def _cell(row, column):
    """One column of a per-run row, or a named absence. Never raises.

    ``_grp`` already answers the missing-ROW case; this is the missing-COLUMN
    one, and it is a separate hazard. A pandas ``itertuples`` row is a namedtuple
    whose fields are the columns the query actually returned, so a defect that
    DROPS a column -- exactly the revert a bucket check exists to catch -- makes
    every read of it an AttributeError at module level: one traceback where the
    section owes its failures. Measured, not predicted: reverting the
    bypassed_inferences bucket aborted this file before this helper existed.
    """
    return getattr(row, column, f"<no column: {column}>")


def _addn(*values):
    """Sum readings that may be named absences, without raising.

    `_num` answers a STRING when a column is missing or NULL, and `float + str`
    is a TypeError inside a check() argument -- an abort in place of the very
    failure a bucket check owes when a defect drops one of its columns.
    Measured: reverting the bypassed_inferences bucket aborted this file here
    even after `_cell` had closed the attribute half of the same hazard.
    """
    numbers = [v for v in values if isinstance(v, (int, float))
               and not isinstance(v, bool)]
    if len(numbers) != len(values):
        return f"<not all numbers: {values!r}>"
    return sum(numbers)


def _num(value, ndigits=None):
    """float(value), optionally rounded -- or a named absence. Never raises.

    A pressure column reads NULL whenever its denominator was not recorded, and
    pandas hands that back as None or NaN. ``float(None)`` raises TypeError, and
    inside a check() argument that is an abort rather than a failure -- so the
    one revert this file exists to catch (a query that stopped measuring) would
    take the whole run down instead of reporting eleven failures.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return f"<not a number: {value!r}>"
    if number != number:
        return "<nan>"
    return round(number, ndigits) if ndigits is not None else number


def _by_run(conn, key):
    """``{run: row}`` for a per-run query, or an EMPTY map when it raised.

    run() raises MissingTableError against a database that cannot answer, which
    is correct and is precisely what a defect removing the two columns from
    INFERENCE_COLUMN_ADDITIONS produces. At module level that raise is an ABORT:
    the file reports one traceback where it owes this section's failures and
    every section after it. An empty map instead makes every _grp() below return
    the named absence, so each check FAILS and says which reading was lost.
    """
    try:
        return {row.run: row for row in queries.run(conn, key).itertuples()}
    except Exception as exc:                       # noqa: BLE001 - reported
        print(f"  (frame unavailable for {key}: "
              f"{type(exc).__name__}: {str(exc)[:120]})")
        return {}


def _is_null(value) -> bool:
    """True for SQL NULL as pandas renders it: None or NaN. Never raises."""
    return value is None or (isinstance(value, float) and value != value)


print("SECTION 2c -- Stage 5 split pressure: input derived, output stored")
print("=" * 78)

# --- (a) the derivation reads the WRITER's key names -----------------------
#
# BY AST OVER THE SHIPPED PACKER, NOT BY IMPORTING IT. Importing
# oncotriage.agent.evaluation would pull openai, qdrant_client and langgraph
# into a file that needs none of them and is bucket A precisely because it does
# not. What is asked is narrow and static: every JSON key the input query
# extracts is a key the packer's report literal actually writes. A reader and a
# writer that disagree about one of these names produce no error at all --
# json_extract returns NULL, every pressure reads NULL, and the query reports a
# campaign with no measurable pressure.
_PACKER_SRC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "oncotriage", "agent", "evaluation.py")
check_true("the packer's source is where this file expects it (a wrong path "
           "would make every key check below pass over an empty set)",
           os.path.exists(_PACKER_SRC))

_packer_fn = None
for _node in ast.walk(ast.parse(io.open(_PACKER_SRC, encoding="utf-8").read())):
    if (isinstance(_node, ast.FunctionDef)
            and _node.name == "pack_trials_by_input_tokens"):
        _packer_fn = _node
check_true("pack_trials_by_input_tokens is still the packer's name",
           _packer_fn is not None)
_packer_keys = {k.value for _n in ast.walk(_packer_fn or ast.Module(body=[], type_ignores=[]))
                if isinstance(_n, ast.Dict)
                for k in _n.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)}
check_true("the packer writes a non-degenerate set of report keys",
           len(_packer_keys) > 8)

# THE PACKER IS NOT THE ONLY WRITER OF THIS BLOB, and reading only its function
# body is what made this check fail the moment the query learned to read
# `bypassed_by`. `llm_classifier_packing` is also written directly by
# node_llm_classifier_evaluation on its two non-packing branches -- the
# packing-OFF report and the per-trial BYPASS report -- and the bypass one is
# the only writer of `bypassed_by` anywhere.
#
# FOUND BY MARKER, NOT BY FUNCTION NAME OR LINE. Every literal that IS one of
# these reports carries `over_budget_chunk`; a report literal moved to another
# function, or a fourth branch added tomorrow, is picked up without editing
# this file, and a dict that is not a packing report cannot contribute a key to
# the corpus by accident. The union is what the property is actually about: the
# SQL may read any key SOME writer of this column emits.
_REPORT_MARKER = "over_budget_chunk"
_report_literals = []
for _n in ast.walk(ast.parse(io.open(_PACKER_SRC, encoding="utf-8").read())):
    if isinstance(_n, ast.Dict):
        _ks = {k.value for k in _n.keys
               if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        if _REPORT_MARKER in _ks:
            _report_literals.append(_ks)
check_true("the marker finds every packing-report literal in the module -- the "
           "packer's own plus the node's packing-OFF and per-trial-bypass "
           "reports. Fewer than three means a branch stopped being found and "
           "the key corpus below silently shrank",
           len(_report_literals) >= 3)
_writer_keys = _packer_keys.union(*_report_literals) if _report_literals \
    else _packer_keys
check_true("...and widening the corpus to those literals really added "
           "something the packer's own body does not write, which is the "
           "whole reason this is a union (non-degeneracy)",
           "bypassed_by" in _writer_keys and "bypassed_by" not in _packer_keys)

# THE FIXTURE IS COMPARED WITH THE WRITER, not merely used. `_bypass_packing_blob`
# is hand-written here because this file may not import the agent (bucket A: no
# openai, no qdrant_client, no langgraph), and a hand-written fixture is exactly
# the thing that drifts. The bucket checks further down would go on passing
# against a fixture that had stopped resembling the node -- json_extract returns
# NULL for a key nobody writes, and NULL is a legal bucket answer.
_bypass_literal = [_ks for _ks in _report_literals if "bypassed_by" in _ks]
check_true("exactly one packing-report literal names a bypass (more than one "
           "would mean the corpus below is a union of two shapes)",
           len(_bypass_literal) == 1)
check("...and this file's bypass fixture carries exactly the keys that "
      "literal writes, so a shipped key added or renamed fails here rather "
      "than reading as a NULL bucket",
      sorted(set(json.loads(_bypass_packing_blob(6))) - {"prefix_sha256"}),
      sorted(_bypass_literal[0]) if _bypass_literal else ["<no literal>"])

# The keys the SQL names, read out of the SQL rather than retyped, so a query
# edit that reaches for a new key is covered without touching this list.
_SQL_KEYS = set(re.findall(r"\$\.([A-Za-z_]+)",
                          queries.QUERIES_BY_KEY[
                              "stage5_input_packing_pressure"].sql))
check_true("the input-pressure SQL names a non-degenerate set of JSON keys",
           len(_SQL_KEYS) >= 4)
check("every JSON key the input-pressure query extracts is one SOME writer of "
      "llm_classifier_packing emits -- a name the two disagree about returns "
      "NULL and reads as 'no pressure', not as an error",
      sorted(_SQL_KEYS - _writer_keys), [])
check_true("...and the check discriminates: a key no writer emits is reported "
           "(negative control)",
           bool({"tokens_estimated_typo"} - _writer_keys))
check_true("...and the query really does read the bypass key, so the "
           "bypassed_inferences bucket is derived from the blob rather than "
           "from a column that does not exist",
           "bypassed_by" in _SQL_KEYS)

# --- (b) the two columns are additive, and their absence is declared -------
#
# THE PRE-THIS-PASS PRODUCTION SHAPE. run_id present, the two pressure columns
# absent -- which is every database written before this pass and, on this
# machine, the production one. The output query SELECTS both, so without the
# declaration it raises `no such column`, report() dies at it and every query
# after it stops executing: item 38's defect, one column granularity down.
_PRESSURE_DB = os.path.join(_TMP_DIR, "no_pressure_cols.db")
with quiet():
    initialize_database(_PRESSURE_DB)
_pressure_conn = sqlite3.connect(_PRESSURE_DB)
check("the two pressure columns are created by initialize_database, which is "
      "what makes them additive rather than a migration",
      sorted(c for c in ("llm_classifier_output_ceiling",
                         "llm_classifier_output_split_threshold")
             if c in queries.table_columns(_pressure_conn, "inferences")),
      ["llm_classifier_output_ceiling",
       "llm_classifier_output_split_threshold"])
# DROPPED ONLY IF PRESENT, and that guard is not defensive clutter: a defect
# that removes them from INFERENCE_COLUMN_ADDITIONS is one this section exists
# to catch, and a bare ALTER ... DROP COLUMN on a column that is not there
# raises OperationalError at module level -- so the run would report one
# traceback where it owes the failure above plus everything below it.
for _drop in ("llm_classifier_output_split_threshold",
              "llm_classifier_output_ceiling"):
    if _drop in queries.table_columns(_pressure_conn, "inferences"):
        _pressure_conn.execute(
            f"ALTER TABLE inferences DROP COLUMN {_drop}")
_pressure_conn.commit()
check("...and dropping them leaves run_id and both run tables in place "
      "(non-degeneracy: this must be a COLUMN case, not the earlier one)",
      ("run_id" in queries.table_columns(_pressure_conn, "inferences")
       and set(queries.RUN_TABLES) <= queries.available_tables(_pressure_conn)),
      True)
check("a database without them reports the OUTPUT query unavailable, naming "
      "both columns",
      queries.unavailable(_pressure_conn).get("stage5_output_split_pressure"),
      ("inferences.llm_classifier_output_split_threshold",
       "inferences.llm_classifier_output_ceiling"))
check("...and the INPUT query, which reads neither, is still answerable -- the "
      "skip is surgical rather than blanket",
      "stage5_input_packing_pressure" in queries.unavailable(_pressure_conn),
      False)
check("run() on it raises MissingTableError rather than the raw sqlite error",
      type(check_raises("  (no pressure cols)", queries.MissingTableError,
                        queries.run, _pressure_conn,
                        "stage5_output_split_pressure")).__name__,
      "MissingTableError")
check_does_not_raise(
    "report() on it reaches the end rather than dying at the output query",
    queries.report, _pressure_conn, out=lambda _line: None)
_pressure_conn.close()

# --- (c) the input distribution, per run, against the seed -----------------
#
# EVERY EXPECTATION IS THE ARITHMETIC WRITTEN OUT, not a value read back from
# the frame under test. The seed's chunk estimates and budgets are literals a
# few hundred lines up; the numbers below are what they imply.
_ip = _by_run(_conn, "stage5_input_packing_pressure")
_RUN_CLEAN = str(_RUN_IDS["RUN-CLEAN"])
_RUN_DEGRADED = str(_RUN_IDS["RUN-DEGRADED"])
_RUN_CRASHED = str(_RUN_IDS["RUN-CRASHED"])

check_true("the input-pressure frame covers every run AND the run-less rows "
           "(non-degeneracy: a frame with one group would satisfy most of "
           "what follows)",
           {_RUN_CLEAN, _RUN_DEGRADED, _RUN_CRASHED,
            queries.NO_RUN_LABEL} <= set(_ip))

# RUN-CLEAN: P-CONSISTENT-A packed 19,600 and 12,000 into a 20,000 budget,
# P-CONSISTENT-B packed 9,000, and P-NULL-TOKENS published no packing record.
check("chunks are counted per REQUEST, not per patient", _num(_grp(_ip, _RUN_CLEAN).chunks), 3)
check("...over four inference rows", _num(_grp(_ip, _RUN_CLEAN).inferences), 4)

# ── THE TWO UNMEASURED POPULATIONS ARE DIFFERENT FINDINGS ──────────────────
#
# RUN-CLEAN carries one of each. P-NULL-TOKENS published no packing record at
# all -- a failure return or a pre-packer row, the population this query cannot
# measure and should say so about. P-BYPASSED is HEALTHY: it sent six requests,
# and something other than the packer partitioned them, so there is no budget to
# be under. Folded together they read as two lost measurements, and a run whose
# every row is per-trial would report itself as entirely broken.
check("...one of which published no packing record and is counted as such "
      "rather than dropped", _num(_cell(_grp(_ip, _RUN_CLEAN), "unpacked_inferences")), 1)
check("...and the BYPASSED row is its own bucket, not folded into that one: "
      "its packer did not run because something else partitioned the batch, "
      "which is a healthy row rather than a lost measurement",
      _num(_cell(_grp(_ip, _RUN_CLEAN), "bypassed_inferences")), 1)
check("...the two buckets are DISJOINT and neither swallowed the other -- "
      "which is the whole split, and is what a single `budget IS NULL` bucket "
      "could not say (it would report 2 and name neither)",
      (_num(_cell(_grp(_ip, _RUN_CLEAN), "unpacked_inferences")),
       _num(_cell(_grp(_ip, _RUN_CLEAN), "bypassed_inferences")),
       _addn(_num(_cell(_grp(_ip, _RUN_CLEAN), "unpacked_inferences")),
             _num(_cell(_grp(_ip, _RUN_CLEAN), "bypassed_inferences")))),
      (1, 1, 2))
# STATED AS A SUM RATHER THAN A SUBTRACTION. Subtracting a named absence is the
# same abort one operator over, and "4 rows, 2 of them unmeasured for two
# different reasons" is the sentence the check is actually making.
check("...and together they are exactly the rows the ratios say nothing "
      "about: 4 inferences, 2 measured, 2 unmeasured for two different "
      "reasons",
      (_num(_cell(_grp(_ip, _RUN_CLEAN), "inferences")),
       _addn(_num(_cell(_grp(_ip, _RUN_CLEAN), "unpacked_inferences")),
             _num(_cell(_grp(_ip, _RUN_CLEAN), "bypassed_inferences")))),
      (4, 2))
check("a bypassed row contributes no CHUNK to the run, so it moves no "
      "pressure reading -- the peak, the mean and the headroom below are over "
      "the two packed rows exactly as they were before it was seeded",
      _num(_grp(_ip, _RUN_CLEAN).chunks), 3)
check("...and a run with no bypassed row reports 0 there rather than NULL, so "
      "the bucket is a measurement everywhere and not only where it fires",
      (_num(_cell(_grp(_ip, _RUN_DEGRADED), "bypassed_inferences")),
       _num(_cell(_grp(_ip, _RUN_CRASHED), "bypassed_inferences"))), (0, 0))
check("peak pressure is the tightest chunk over its own budget: 19600/20000",
      _num(_grp(_ip, _RUN_CLEAN).peak_pressure, 4), 0.98)
check("mean pressure averages the CHUNKS, not the patients: "
      "(19600 + 12000 + 9000) / 3 / 20000",
      _num(_grp(_ip, _RUN_CLEAN).mean_pressure, 4),
      round((19600 + 12000 + 9000) / 3 / 20000, 4))
check("headroom is stated in tokens, tightest first: 20000 - 19600",
      _num(_grp(_ip, _RUN_CLEAN).min_headroom_tokens), 400)
check("the 90% bucket counts only the chunk that reached it",
      _num(_grp(_ip, _RUN_CLEAN).chunks_at_90pct), 1)
check("...and the 75% bucket is a SUPERSET of it, never a disjoint band",
      _num(_grp(_ip, _RUN_CLEAN).chunks_at_75pct), 1)
check("a clean run reports no over-budget chunk and no relaxed cap",
      (_num(_grp(_ip, _RUN_CLEAN).over_budget_chunks),
       _num(_grp(_ip, _RUN_CLEAN).relaxed_inferences)), (0, 0))

# RUN-DEGRADED: the cap was relaxed to 24,000 and one chunk shipped over it.
check("a relaxed run measures against the EFFECTIVE budget it was raised to, "
      "not the configured one: 23900/24000",
      _num(_grp(_ip, _RUN_DEGRADED).peak_pressure, 4),
      round(23900 / 24000, 4))
check("...and the effective budget is what the frame reports",
      (_num(_grp(_ip, _RUN_DEGRADED).budget_min), _num(_grp(_ip, _RUN_DEGRADED).budget_max)),
      (24000, 24000))
check("...with the relaxation counted per INFERENCE and the over-budget chunk "
      "per CHUNK -- the guard firing, not approaching",
      (_num(_grp(_ip, _RUN_DEGRADED).relaxed_inferences),
       _num(_grp(_ip, _RUN_DEGRADED).over_budget_chunks)), (1, 1))
check("...and both of its chunks are past 75% while only one is past 90%",
      (_num(_grp(_ip, _RUN_DEGRADED).chunks_at_75pct),
       _num(_grp(_ip, _RUN_DEGRADED).chunks_at_90pct)), (2, 1))

# RUN-CRASHED: two inference rows, neither carrying a packing record. This is
# the NULL-heavy legacy population, and it must appear rather than vanish.
check("a run whose rows carry no packing record is a ROW in the frame, with "
      "its unmeasured population named -- an omission would read as an "
      "absence of pressure",
      (_num(_grp(_ip, _RUN_CRASHED).inferences),
       _num(_cell(_grp(_ip, _RUN_CRASHED), "unpacked_inferences")),
       _num(_grp(_ip, _RUN_CRASHED).chunks)), (2, 2, 0))
check("...and every pressure reading for it is NULL rather than 0, which "
      "would assert a measured floor",
      [_is_null(_v)
       for _v in (_grp(_ip, _RUN_CRASHED).peak_pressure,
                  _grp(_ip, _RUN_CRASHED).mean_pressure,
                  _grp(_ip, _RUN_CRASHED).min_headroom_tokens,
                  _grp(_ip, _RUN_CRASHED).budget_min)],
      [True, True, True, True])
check("the run-less rows are the bulk legacy population and are reported, not "
      "filtered away",
      _num(_grp(_ip, queries.NO_RUN_LABEL).inferences)
      == _num(_cell(_grp(_ip, queries.NO_RUN_LABEL), "unpacked_inferences"))
      and _num(_grp(_ip, queries.NO_RUN_LABEL).inferences) > 10, True)
check("...and none of them is a BYPASS: a legacy row predates the bypass and "
      "carries no such record, so the split did not reclassify the legacy "
      "population as healthy",
      _num(_cell(_grp(_ip, queries.NO_RUN_LABEL), "bypassed_inferences")), 0)

# THE BUCKETS PARTITION THE TABLE. Without this every check above is satisfied
# by a query that counted some rows twice or dropped some entirely.
_ip_total = _addn(*[_num(_cell(_r, "inferences")) for _r in _ip.values()])
_ip_bypassed = _addn(*[_num(_cell(_r, "bypassed_inferences"))
                       for _r in _ip.values()])
_ip_unpacked = _addn(*[_num(_cell(_r, "unpacked_inferences"))
                       for _r in _ip.values()])
check("across every group, inferences totals the table and the two unmeasured "
      "buckets are subsets of it that do not overlap",
      (_ip_total,
       _conn.execute("SELECT COUNT(*) FROM inferences").fetchone()[0],
       isinstance(_addn(_ip_bypassed, _ip_unpacked), (int, float))
       and isinstance(_ip_total, (int, float))
       and _ip_bypassed + _ip_unpacked <= _ip_total), 
      (_conn.execute("SELECT COUNT(*) FROM inferences").fetchone()[0],
       _conn.execute("SELECT COUNT(*) FROM inferences").fetchone()[0], True))
check("...and the bypassed total is exactly the rows whose blob names a "
      "bypass, counted independently of the query under test",
      _ip_bypassed,
      _conn.execute(
          "SELECT COUNT(*) FROM inferences WHERE llm_classifier_packing "
          "IS NOT NULL AND json_valid(llm_classifier_packing) "
          "AND json_extract(llm_classifier_packing, '$.bypassed_by') "
          "IS NOT NULL").fetchone()[0])
check("...(non-degeneracy: that independent count is not zero, so the "
      "agreement above is between two real numbers)",
      isinstance(_ip_bypassed, (int, float)) and _ip_bypassed >= 1, True)

# --- (c1) THE PER-ROW INPUT SCALAR, WHERE THE PACKER'S REPORT CANNOT GO -----
#
# `stage5_input_request_pressure` is the sibling of the query above, and the
# rows it exists for are the ones that query names and cannot measure:
# P-BYPASSED, whose packer did not run because per-trial mode partitioned the
# batch instead, and P-ERROR, which failed and therefore published no packing
# report at all. Both had NO input figure of any kind before era 6.
#
# EVERY EXPECTATION IS THE ARITHMETIC OF THE SEED LITERALS, never a value read
# back out of the frame under test.
_irp = {}
try:
    for _r in queries.run(_conn, "stage5_input_request_pressure").itertuples():
        _irp[(str(_r.run), str(_r.arm))] = _r
except Exception as _exc:                          # noqa: BLE001 - reported
    print(f"  [query] stage5_input_request_pressure raised: "
          f"{type(_exc).__name__}: {_exc}")


def _arm(run_key, arm_key):
    """One (run, arm) group, or a named absence. Never a KeyError in a check."""
    return _irp.get((run_key, arm_key), _ABSENT_GROUP)


check_true("the request-pressure frame is non-empty and separates the arms "
           "within one run (non-degeneracy: a frame with one group per run "
           "would satisfy most of what follows without grouping by arm at "
           "all)",
           len({_a for _r, _a in _irp if _r == _RUN_CLEAN}) >= 2)

# RUN-CLEAN, GROUPED ARM: P-CONSISTENT-A alone, 19,600 against 20,000.
check("the grouped arm reports the patient's LARGEST planned request over the "
      "budget recorded on the row: 19600/20000",
      (_num(_cell(_arm(_RUN_CLEAN, MATCHING_CALL_MODE_GROUPED), "inferences")),
       _num(_cell(_arm(_RUN_CLEAN, MATCHING_CALL_MODE_GROUPED),
                  "peak_pressure"), 4)),
      (1, 0.98))
check("...with headroom in tokens beside it, because a ratio alone cannot say "
      "whether 0.98 was 400 tokens of slack or 40",
      _num(_cell(_arm(_RUN_CLEAN, MATCHING_CALL_MODE_GROUPED),
                 "min_headroom_tokens")), 400)

# RUN-CLEAN, PER-TRIAL ARM: P-CONSISTENT-B (9,000/20,000) and P-BYPASSED
# (11,500/12,000). THE BYPASSED ROW IS THE GROUP'S PEAK, which is the whole
# point: a query that dropped it would lose the tightest reading in the arm.
_PT = MATCHING_CALL_MODE_PER_TRIAL
check("the per-trial arm is its own group, over the two per-trial patients",
      _num(_cell(_arm(_RUN_CLEAN, _PT), "inferences")), 2)
check("...and its peak comes from the BYPASSED row -- the row the packing "
      "query above counts as having no pressure to report: 11500/12000",
      _num(_cell(_arm(_RUN_CLEAN, _PT), "peak_pressure"), 4),
      round(11500 / 12000, 4))
check("...(non-degeneracy: the bypassed row's reading really is the tighter "
      "of the two, so the peak is not the other patient's by coincidence)",
      round(11500 / 12000, 4) > round(9000 / 20000, 4), True)
check("...the mean averages the two PATIENTS, one scalar each, which is what "
      "one-row-per-patient means",
      _num(_cell(_arm(_RUN_CLEAN, _PT), "mean_pressure"), 4),
      round((11500 / 12000 + 9000 / 20000) / 2, 4))
check("...the tightest headroom is the bypassed row's 500 tokens",
      _num(_cell(_arm(_RUN_CLEAN, _PT), "min_headroom_tokens")), 500)
check("...the 90% bucket counts only the row that reached it, and the 75% "
      "bucket is a SUPERSET of it rather than a disjoint band",
      (_num(_cell(_arm(_RUN_CLEAN, _PT), "inferences_at_90pct")),
       _num(_cell(_arm(_RUN_CLEAN, _PT), "inferences_at_75pct"))), (1, 1))
check("...and neither per-trial row is over budget or failed",
      (_num(_cell(_arm(_RUN_CLEAN, _PT), "inferences_over_budget")),
       _num(_cell(_arm(_RUN_CLEAN, _PT), "failed_inferences"))), (0, 0))

# THE TWO BUDGETS IN ONE ARM ARE SHOWN AS A RANGE, not averaged away.
check("a group whose rows carry two different budgets reports both, so a "
      "campaign that spanned a config change says so rather than averaging "
      "across it",
      (_num(_cell(_arm(_RUN_CLEAN, _PT), "budget_min")),
       _num(_cell(_arm(_RUN_CLEAN, _PT), "budget_max"))), (12000, 20000))

# THE LEGACY ROW. P-NULL-TOKENS carries neither era-6 column.
check("a row written before era 6 is COUNTED as unmeasured rather than "
      "dropped, and contributes no pressure reading",
      (_num(_cell(_arm(_RUN_CLEAN, queries.MODE_NOT_RECORDED_LABEL),
                  "inferences")),
       _num(_cell(_arm(_RUN_CLEAN, queries.MODE_NOT_RECORDED_LABEL),
                  "unmeasured"))),
      (1, 1))
check("...and its pressure columns are NULL rather than 0, which would assert "
      "a measured floor",
      [_is_null(_cell(_arm(_RUN_CLEAN, queries.MODE_NOT_RECORDED_LABEL), _c))
       for _c in ("peak_pressure", "mean_pressure", "min_headroom_tokens",
                  "budget_min", "peak_request_tokens")],
      [True, True, True, True, True])

# THE FAILED ROW. P-ERROR is in RUN-CRASHED beside P-NOMODEL-CLEAN, which has
# no scalar at all -- so the group carries a measured failure and an unmeasured
# row at once, and the two must not be confused.
_CRASHED_ARM = (_RUN_CRASHED, queries.MODE_NOT_RECORDED_LABEL)
check("A FAILED ROW REPORTS REAL PRESSURE, which is the whole of what era 6 "
      "bought: 13000 against a 12000 budget, on a run that never got an "
      "answer -- before this the row was NULL because the packing report is "
      "published on the success return only",
      (_num(_cell(_arm(*_CRASHED_ARM), "failed_inferences")),
       _num(_cell(_arm(*_CRASHED_ARM), "peak_request_tokens")),
       _num(_cell(_arm(*_CRASHED_ARM), "peak_pressure"), 4)),
      (1, 13000, round(13000 / 12000, 4)))
check("...and pressure ABOVE 1.0 is a real reading rather than a seed error: "
      "the packer relaxes its budget when the cap binds and a single "
      "oversized trial ships anyway, so the over-budget bucket fires",
      _num(_cell(_arm(*_CRASHED_ARM), "inferences_over_budget")), 1)
check("...with negative headroom stating how far over, in tokens",
      _num(_cell(_arm(*_CRASHED_ARM), "min_headroom_tokens")), -1000)
check("...and the unmeasured row beside it is counted separately, so 'failed' "
      "and 'unmeasured' are two buckets and not one",
      (_num(_cell(_arm(*_CRASHED_ARM), "inferences")),
       _num(_cell(_arm(*_CRASHED_ARM), "unmeasured"))), (2, 1))

# THE RELAXED RUN, MEASURED AGAINST WHAT WAS CONFIGURED.
check("the SAME chunk the packing query reads at 23900/24000 = 0.9958 against "
      "the budget the packer RAISED ITSELF TO reads 23900/20000 = 1.195 here "
      "against what was CONFIGURED -- the relaxation IS the pressure, and "
      "both numbers are true of the same run",
      (_num(_grp(_ip, _RUN_DEGRADED).peak_pressure, 4),
       _num(_cell(_arm(_RUN_DEGRADED, queries.MODE_NOT_RECORDED_LABEL),
                  "peak_pressure"), 4)),
      (round(23900 / 24000, 4), round(23900 / 20000, 4)))

# THE GROUPS PARTITION THE TABLE. Without this every check above is satisfied
# by a query that counted some rows twice or dropped some entirely.
check("across every (run, arm) group, inferences totals the table exactly",
      _addn(*[_num(_cell(_r, "inferences")) for _r in _irp.values()]),
      _conn.execute("SELECT COUNT(*) FROM inferences").fetchone()[0])
# THE INDEPENDENT COUNT GOES THROUGH `_scalar`, NOT A BARE `.execute`. It
# names the era-6 column, so a defect that removes it from
# INFERENCE_COLUMN_ADDITIONS -- which is exactly the revert these two checks
# exist to catch -- makes a bare read raise `no such column` at module level:
# one traceback where this section owes its failures and every section after
# it. MEASURED, not predicted.
def _scalar(sql):
    """One scalar out of the seeded database, or a named absence."""
    try:
        return _conn.execute(sql).fetchone()[0]
    except BaseException as exc:                    # noqa: BLE001 - reported
        return f"<count failed: {type(exc).__name__}: {exc}>"


_UNMEASURED_ROWS = _scalar("SELECT COUNT(*) FROM inferences WHERE "
                           "llm_classifier_input_tokens_estimated IS NULL")
_ALL_ROWS = _scalar("SELECT COUNT(*) FROM inferences")
check("...and the unmeasured total is exactly the rows carrying no scalar, "
      "counted independently of the query under test",
      _addn(*[_num(_cell(_r, "unmeasured")) for _r in _irp.values()]),
      _UNMEASURED_ROWS)
check("...(non-degeneracy: that independent count is neither zero nor the "
      "whole table, so the agreement above is between two real numbers)",
      isinstance(_UNMEASURED_ROWS, int) and isinstance(_ALL_ROWS, int)
      and 0 < _UNMEASURED_ROWS < _ALL_ROWS, True)

# THE DECLARATION AND THE DERIVATION AGREE, and the column requirement really
# fires: a database predating era 6 must report this query unavailable rather
# than letting report() die on `no such column`.
check("the hand declaration matches what derive_requires_columns reads out of "
      "the SQL",
      set(queries.derive_requires_columns(
          queries.QUERIES_BY_KEY["stage5_input_request_pressure"].sql)),
      set(queries.QUERIES_BY_KEY[
          "stage5_input_request_pressure"].requires_columns))

_ERA5_DB = os.path.join(_TMP_DIR, "pre_era6.db")
with quiet():
    initialize_database(_ERA5_DB)
_era5_conn = sqlite3.connect(_ERA5_DB)
for _drop in ("llm_classifier_input_tokens_estimated",
              "llm_classifier_input_budget"):
    # DROPPED ONLY IF PRESENT, on the sibling section's argument: a defect that
    # removes them from INFERENCE_COLUMN_ADDITIONS is one this check exists to
    # catch, and a bare DROP on an absent column raises at module level and
    # takes the rest of the file with it.
    if _drop in queries.table_columns(_era5_conn, "inferences"):
        _era5_conn.execute(f"ALTER TABLE inferences DROP COLUMN {_drop}")
_era5_conn.commit()
check("both era-6 columns are created by initialize_database, which is what "
      "makes them additive rather than a migration (non-degeneracy: the drops "
      "above had something to drop)",
      sorted(_c for _c in ("llm_classifier_input_budget",
                           "llm_classifier_input_tokens_estimated")
             if _c in queries.table_columns(_era5_conn, "inferences")), [])
check("a pre-era-6 database reports the request-pressure query unavailable, "
      "naming both columns, rather than raising inside report()",
      queries.unavailable(_era5_conn).get("stage5_input_request_pressure"),
      ("inferences.llm_classifier_input_budget",
       "inferences.llm_classifier_input_tokens_estimated"))
check("...and the PACKING query, which reads neither, is still answerable -- "
      "the skip is surgical rather than blanket",
      "stage5_input_packing_pressure" in queries.unavailable(_era5_conn),
      False)
check("run() on it raises MissingTableError rather than the raw sqlite error",
      type(check_raises("  (pre-era-6)", queries.MissingTableError,
                        queries.run, _era5_conn,
                        "stage5_input_request_pressure")).__name__,
      "MissingTableError")
check_does_not_raise(
    "report() on it reaches the end rather than dying at the new query",
    queries.report, _era5_conn, out=lambda _line: None)
_era5_conn.close()


# --- (c2) THE THREE ARMS IN ONE DATABASE, AND THE WHOLE REGISTRY OVER IT ---
#
# WHY A SECOND DATABASE AND NOT MORE SEED ROWS. The three shapes below have to
# sit in ONE run to be compared, and the main seed's runs are already load
# bearing for a dozen other expectations. This one holds exactly three rows and
# nothing else, so every number is the arithmetic of three literals.
#
# The arms:
#   grouped              -- the packer ran; packed_chunks an integer; the
#                           provider reported a cached figure.
#   per_trial healthy    -- the packer was BYPASSED; packed_chunks NULL; the
#                           wave reported a cached figure.
#   per_trial silent wave -- the same, except that no wave call reported the
#                           field at all, so the cached column is NULL. This is
#                           the row the warmup used to turn into a 0.
#
# WHAT IS BEING ASKED: nothing in the registry may crash on those NULLs, and
# nothing may misclassify the bypassed rows as unmeasured or the silent wave as
# "cached nothing".
_ARMS_DB = os.path.join(_TMP_DIR, "three_arms.db")
with quiet():
    initialize_database(_ARMS_DB)
_arms_conn = sqlite3.connect(_ARMS_DB)
_arms_cur = _arms_conn.cursor()
_arms_cur.execute(
    "INSERT INTO runs (started_at, finished_at, status, invocation_source, "
    "resumed, fingerprint_version, llm_classifier_renderer_digest, "
    "matching_model_configured, matching_call_mode, qdrant_collection, "
    "collection_points, data_snapshot_date) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    ("2026-08-22T09:00:00", "2026-08-22T10:00:00", "FINISHED", "batch_runner",
     0, 2, "digest-arms", "gpt-5.6-terra", MATCHING_CALL_MODE_PER_TRIAL,
     "trial_criteria_20260807_111807", 12067, "2026-02-26"))
_ARMS_RUN = _arms_cur.lastrowid

# THE THREE LEDGERS ARE HAND-CHOSEN SO EVERY COLUMN OF
# `stage5_cache_effectiveness` IS NON-DEGENERATE, and section 8d recomputes each
# rate from these numbers rather than reading it back out of the frame.
#
#   A-GROUPED           two chunk calls, 20,000 prompt each, the second one
#                       finding 18,000 cached. No warmup row: grouped mode
#                       issues none, which is what makes "warmup_calls is 0 by
#                       construction" a measurement rather than a claim.
#   A-PERTRIAL-CACHED   a warmup that found NOTHING cached (0, not NULL -- it is
#                       the request that WRITES the prefix) and three wave calls
#                       of which the first is also cold and the next two warm.
#                       That is the designed shape, and the wave rate below is
#                       what it produces.
#   A-PERTRIAL-SILENT   the same schedule with cached_tokens absent on every
#                       row. Its calls must be counted and must appear in
#                       NEITHER half of either rate; a query folding them in
#                       would report a silent provider as one that is not
#                       caching, which are opposite findings.
_WARMUP_PROMPT = 8000
_ARM_ROWS = [
    # (patient, call mode, packing blob, packed_chunks, cached tokens, ledger)
    ("A-GROUPED", MATCHING_CALL_MODE_GROUPED,
     _packing_blob(20000, [(10, 18000, False)]), 1, 18000,
     _grouped_details_blob((20000, 0), (20000, 18000))),
    ("A-PERTRIAL-CACHED", MATCHING_CALL_MODE_PER_TRIAL,
     _bypass_packing_blob(6), None, 15600,
     _per_trial_details_blob((_WARMUP_PROMPT, 0),
                             (9000, 0), (9000, 7800), (9000, 7800))),
    ("A-PERTRIAL-SILENT", MATCHING_CALL_MODE_PER_TRIAL,
     _bypass_packing_blob(6), None, None,
     _per_trial_details_blob((_WARMUP_PROMPT, None),
                             (9000, None), (9000, None))),
]
for _pid, _mode, _blob, _chunks, _cached, _ledger in _ARM_ROWS:
    _arms_cur.execute(
        "INSERT INTO inferences (patient_id, timestamp, run_id, "
        "matching_model, matching_call_mode, llm_classifier_input_tokens, "
        "llm_classifier_output_tokens, llm_classifier_cached_input_tokens, "
        "estimated_cost_usd, llm_classifier_packing, "
        "llm_classifier_packed_chunks, llm_classifier_output_split_threshold, "
        "llm_classifier_output_ceiling, "
        "llm_classifier_output_tokens_estimated, "
        "llm_classifier_call_details, candidates_retrieved, "
        "candidates_reranked, candidates_filtered, candidates_evaluated, "
        "eligible_matches, near_misses, not_evaluable_trials, total_time) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "?, ?, ?)",
        (_pid, "2026-08-22T09:30:00", _ARMS_RUN, _MODEL_A, _mode, 12000, 3000,
         _cached, 0.06, _blob, _chunks, _THRESHOLD_NOW, _CEILING_NOW, 6600,
         _ledger, 90, 40, 6, 6, 4, 2, 0, 90.0))
_arms_conn.commit()

check("(c2) the three-arm database really holds the three shapes it is named "
      "for -- one integer packed_chunks, two NULLs, one NULL cached figure "
      "and two measured ones (non-degeneracy: without this every check below "
      "could be reading a database that failed to seed)",
      _arms_conn.execute(
          "SELECT COUNT(*), "
          "SUM(llm_classifier_packed_chunks IS NULL), "
          "SUM(llm_classifier_cached_input_tokens IS NULL), "
          "SUM(json_extract(llm_classifier_packing, '$.bypassed_by') "
          "    IS NOT NULL) FROM inferences").fetchone(), (3, 2, 1, 2))

# THE WHOLE REGISTRY, not the three queries this pass touched. A NULL shape that
# crashes some other query is the same defect one report away.
check_does_not_raise(
    "(c2) report() runs the entire registry over the three arms and reaches "
    "the end -- no NULL shape introduced by this pass takes the process down",
    queries.report, _arms_conn, out=lambda _line: None)
_arms_unavailable = queries.unavailable(_arms_conn)
check("(c2) ...and it skipped nothing: a fresh database has every additive "
      "column, so the run above really executed every query rather than "
      "passing by declining most of them",
      sorted(_arms_unavailable), [])

_arms_ip = _by_run(_arms_conn, "stage5_input_packing_pressure")
_arms_row = _grp(_arms_ip, str(_ARMS_RUN))
check("(c2) the pressure query classifies the arms: three inferences, one "
      "packed chunk from the grouped row, two BYPASSED and none unmeasured",
      (_num(_cell(_arms_row, "inferences")), _num(_cell(_arms_row, "chunks")),
       _num(_cell(_arms_row, "bypassed_inferences")),
       _num(_cell(_arms_row, "unpacked_inferences"))), (3, 1, 2, 0))
check("(c2) ...and the pressure it reports is the grouped row's alone: "
      "18000/20000, undisturbed by two rows that packed nothing",
      (_num(_cell(_arms_row, "peak_pressure"), 4), _num(_cell(_arms_row, "mean_pressure"), 4),
       _num(_cell(_arms_row, "min_headroom_tokens"))), (0.9, 0.9, 2000))
# A RUN OF ONLY BYPASSED ROWS. Without it the readings above are all carried by
# the one grouped row, and a query that reported 0 for a bypassed-only run --
# a measured floor asserted about a run that measured nothing -- would pass.
_arms_cur.execute(
    "INSERT INTO runs (started_at, finished_at, status, invocation_source, "
    "resumed, fingerprint_version, matching_call_mode) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)",
    ("2026-08-22T11:00:00", "2026-08-22T11:30:00", "FINISHED", "batch_runner",
     0, 2, MATCHING_CALL_MODE_PER_TRIAL))
_ARMS_RUN_ALLBYPASS = _arms_cur.lastrowid
for _pid in ("A-ONLY-BYPASS-1", "A-ONLY-BYPASS-2"):
    _arms_cur.execute(
        "INSERT INTO inferences (patient_id, timestamp, run_id, "
        "matching_model, matching_call_mode, llm_classifier_input_tokens, "
        "llm_classifier_output_tokens, estimated_cost_usd, "
        "llm_classifier_packing, llm_classifier_packed_chunks) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_pid, "2026-08-22T11:10:00", _ARMS_RUN_ALLBYPASS, _MODEL_A,
         MATCHING_CALL_MODE_PER_TRIAL, 12000, 3000, 0.06,
         _bypass_packing_blob(6), None))
_arms_conn.commit()
_arms_ip2 = _by_run(_arms_conn, "stage5_input_packing_pressure")
_all_bypass = _grp(_arms_ip2, str(_ARMS_RUN_ALLBYPASS))
check("(c2) a run of ONLY bypassed rows is REPORTED, with both of them in the "
      "bypassed bucket and none in the unmeasured one -- a per-trial campaign "
      "must not read as a campaign whose measurements were lost",
      (_num(_cell(_all_bypass, "inferences")), _num(_cell(_all_bypass, "bypassed_inferences")),
       _num(_cell(_all_bypass, "unpacked_inferences")), _num(_cell(_all_bypass, "chunks"))),
      (2, 2, 0, 0))
check("(c2) ...and every pressure reading for it is NULL rather than 0, which "
      "would assert a measured floor about a run that packed nothing",
      [_is_null(_v) for _v in (_cell(_all_bypass, "peak_pressure"),
                               _cell(_all_bypass, "mean_pressure"),
                               _cell(_all_bypass, "min_headroom_tokens"),
                               _cell(_all_bypass, "budget_min"))],
      [True, True, True, True])
check("(c2) ...(non-degeneracy: the group really exists in the frame, so the "
      "four NULLs above are a row's readings and not a missing row)",
      str(_ARMS_RUN_ALLBYPASS) in _arms_ip2, True)

_arms_op = _by_run(_arms_conn, "stage5_output_split_pressure")
check("(c2) the OUTPUT pressure query is untouched by the arm: all three rows "
      "are measured there, because its inputs are stored columns the bypass "
      "does not write",
      (_num(_grp(_arms_op, str(_ARMS_RUN)).inferences),
       _num(_grp(_arms_op, str(_ARMS_RUN)).unmeasured)), (3, 0))

# THE PRICED FRAME, not the raw GROUP BY: `cost_complete` is added by
# price_model_groups and is the field a consumer asks, so reading the raw query
# would have tested the half of the cost path this pass cannot reach.
_arms_cost_df = queries.cost_by_model(_arms_conn)
_arms_cost = {str(_r.matching_model): _r
              for _r in _arms_cost_df.itertuples()}


def _cost_field(model, field):
    """One field of a priced cost row, or a named absence. Never raises.

    A bare attribute read here ABORTS the file when a defect drops the column
    or the group -- which is precisely when this section owes a failure. This
    file has had to close that shape twice already.
    """
    _row = _arms_cost.get(model)
    if _row is None:
        return f"<no cost row: {model}>"
    return getattr(_row, field, f"<no cost field: {field}>")


check("(c2) the cost query prices all five rows as one model group and is not "
      "disturbed by either NULL: 5 x 0.06 stored, and the recomputed figure "
      "is reported COMPLETE rather than qualified",
      (_MODEL_A in _arms_cost,
       round(float(_cost_field(_MODEL_A, "stored_cost")), 4)
       if _MODEL_A in _arms_cost else _cost_field(_MODEL_A, "stored_cost"),
       bool(_cost_field(_MODEL_A, "cost_complete"))
       if _MODEL_A in _arms_cost else _cost_field(_MODEL_A, "cost_complete")),
      (True, 0.3, True))
check("(c2) ...(non-degeneracy: `cost_complete` is a real field of the priced "
      "frame, so the True above is a reading rather than a truthy absence)",
      "cost_complete" in _arms_cost_df.columns, True)

_arms_modes = list(
    queries.run(_arms_conn, "call_mode_comparison").itertuples())
check("(c2) the call-mode comparison still separates the arms on this "
      "database, so a reader can attribute every NULL-packed row: four "
      "per-trial rows across the two runs, one grouped",
      (sorted({str(_r.row_mode) for _r in _arms_modes}),
       sum(_safe_int(_r.patients) for _r in _arms_modes
           if str(_r.row_mode) == MATCHING_CALL_MODE_PER_TRIAL),
       sum(_safe_int(_r.patients) for _r in _arms_modes
           if str(_r.row_mode) == MATCHING_CALL_MODE_GROUPED)),
      (sorted({MATCHING_CALL_MODE_GROUPED, MATCHING_CALL_MODE_PER_TRIAL}),
       4, 1))
# THE WHOLE REGISTRY AGAIN, over the FINAL shape -- five rows, two runs, one of
# them entirely bypassed. The first report() above ran before those rows
# existed, so without this the bypassed-only run (the shape whose every
# pressure reading is NULL) never passes through report()'s renderers at all.
check_does_not_raise(
    "(c2) ...and report() still reaches the end with a run whose every "
    "packing reading is NULL -- the renderers, not only the SQL",
    queries.report, _arms_conn, out=lambda _line: None)


# ===========================================================================
# SECTION 2d -- stage5_cache_effectiveness
# ===========================================================================
#
# THE MEASUREMENT PER-TRIAL MODE IS ONLY VIABLE ON. The mode multiplies Stage 5
# requests by MAX_TRIALS_FOR_EVALUATION and pays for itself only if the shared
# prefix is billed at the cached rate from the second call of a patient on.
# `llm_classifier_call_details` has carried the per-call evidence since the
# packing pass and NOTHING REGISTERED READ IT, so "is the discount landing" was
# answerable only by parsing JSON by hand.
#
# EVERY EXPECTATION BELOW IS RECOMPUTED FROM THE SEEDED NUMBERS, never read back
# out of the frame under test. The three ledgers are declared at `_ARM_ROWS`
# above with the reasoning for each.
#
# IT RUNS AGAINST THE THREE-ARM DATABASE and not the main seed, because the
# three shapes it has to separate -- a grouped ledger with no warmup, a
# per-trial ledger whose warmup found nothing cached, and a per-trial ledger
# SILENT on caching -- are exactly the three that database was built to hold.
print("\nSECTION 2d -- stage5_cache_effectiveness")

_cache_rows = {(str(_r.run), str(_r.call_mode)): _r
               for _r in _frame_or_raise("stage5_cache_effectiveness",
                                         conn=_arms_conn).itertuples()}
_RUN_TXT = str(_ARMS_RUN)
_GR = _cache_rows.get((_RUN_TXT, MATCHING_CALL_MODE_GROUPED))
_PT = _cache_rows.get((_RUN_TXT, MATCHING_CALL_MODE_PER_TRIAL))

_BYPASS_TXT = str(_ARMS_RUN_ALLBYPASS)
_NOLEDGER = _cache_rows.get((_BYPASS_TXT, MATCHING_CALL_MODE_PER_TRIAL))

check("2d-a the frame is one row per (run, arm): both arms of the mixed run "
      "and the per-trial run that has no ledger at all (non-degeneracy: every "
      "check below reads one of these three)",
      sorted(_cache_rows),
      sorted([(_RUN_TXT, MATCHING_CALL_MODE_GROUPED),
              (_RUN_TXT, MATCHING_CALL_MODE_PER_TRIAL),
              (_BYPASS_TXT, MATCHING_CALL_MODE_PER_TRIAL)]))

# --- the warmup is counted, and only in the arm that issues one -----------
check("2d-b the per-trial arm's TWO warmup calls are counted",
      None if _PT is None else _safe_int(_PT.warmup_calls), 2)
check("2d-c ...and the grouped arm's is 0 BY CONSTRUCTION -- grouped mode "
      "issues no warmup, so a non-zero reading there is a flag that moved "
      "mid-patient rather than a rounding artefact",
      None if _GR is None else _safe_int(_GR.warmup_calls), 0)

# --- the wave, and what is EXCLUDED from it ------------------------------
check("2d-d the per-trial arm's wave calls are counted across both rows "
      "(3 + 2), warmups excluded",
      None if _PT is None else _safe_int(_PT.wave_calls), 5)
check("2d-e ...of which the two SILENT ones are named, so a provider that "
      "reported nothing is separable from one that cached nothing",
      None if _PT is None else _safe_int(_PT.wave_calls_silent), 2)
check("2d-f ...and the rate's DENOMINATOR is the reporting calls only "
      "(3 x 9,000), not all five -- folding the silent pair in would report a "
      "silent provider as a provider that is not caching",
      None if _PT is None else _safe_int(_PT.wave_prompt_tokens), 27000)
check("2d-g ...and its NUMERATOR is those same calls' cached figures "
      "(0 + 7,800 + 7,800)",
      None if _PT is None else _safe_int(_PT.wave_cached_tokens), 15600)
check("2d-h ...so the hit rate is 15,600 / 27,000, recomputed here rather "
      "than read back out of the frame",
      None if _PT is None else _num(_PT.wave_cache_hit_rate, 4),
      round(15600 / 27000, 4))

# --- the warmup's own reading, BESIDE the wave and never inside it -------
check("2d-i the warmup's cached figure is 0 -- REPORTED and zero, which is "
      "the healthy reading: it is the request that WRITES the prefix",
      None if _PT is None else _safe_int(_PT.warmup_cached_tokens), 0)
check("2d-j ...over the ONE warmup that reported; the silent row's warmup is "
      "excluded from this denominator too",
      None if _PT is None else _safe_int(_PT.warmup_prompt_tokens),
      _WARMUP_PROMPT)
check("2d-k ...giving a warmup hit rate of 0.0, which must not be confused "
      "with the NULL a warmup that reported nothing produces",
      None if _PT is None else _num(_PT.warmup_cache_hit_rate, 4), 0.0)
check("2d-l THE SEPARATION IS THE POINT: the warmup's 8,000 prompt tokens are "
      "in NEITHER half of the wave rate. Folded in, the rate would be "
      "15,600/35,000 and a healthy warmup would read as a cache miss",
      (None if _PT is None else _safe_int(_PT.wave_prompt_tokens)) == 27000
      and _num(_PT.wave_cache_hit_rate, 4) != round(15600 / 35000, 4), True)

# --- the grouped baseline the per-trial arm has to beat -------------------
check("2d-m the grouped arm reports its own rate (18,000 / 40,000), so the "
      "comparison has a baseline rather than one arm and a blank",
      (None if _GR is None else (_safe_int(_GR.wave_calls),
                                 _safe_int(_GR.wave_prompt_tokens),
                                 _num(_GR.wave_cache_hit_rate, 4))),
      (2, 40000, round(18000 / 40000, 4)))
check("2d-n ...and NULL warmup readings, never 0 -- there was no warmup to "
      "report a figure for, which is not 'the warmup found nothing'",
      (None if _GR is None else (_GR.warmup_prompt_tokens is None
                                 or _num(_GR.warmup_prompt_tokens) != _GR.warmup_prompt_tokens
                                 or str(_GR.warmup_prompt_tokens) == "nan",
                                 str(_GR.warmup_cache_hit_rate))),
      (True, "nan"))

# --- the ROW column, and its three-way split -----------------------------
check("2d-o the per-trial arm holds one row SILENT on caching and one "
      "reporting it, and the two are counted apart",
      (None if _PT is None else (_safe_int(_PT.rows_silent_on_cache),
                                 _safe_int(_PT.rows_reporting_cache),
                                 _safe_int(_PT.rows_reporting_no_cache))),
      (1, 1, 0))
check("2d-p ...and the grouped row reports a figure",
      (None if _GR is None else (_safe_int(_GR.rows_silent_on_cache),
                                 _safe_int(_GR.rows_reporting_cache))),
      (0, 1))

# --- A RUN WITH NO LEDGER AT ALL IS REPORTED AND MEASURES NOTHING ---------
#
# The two all-bypass rows carry neither `llm_classifier_call_details` nor
# `llm_classifier_cached_input_tokens`. Every reading for that run must be a 0
# COUNT or a NULL RATE -- never a 0 rate, which would assert that the provider
# was asked and cached nothing when it was never asked at all. This is the same
# distinction the pressure query's own bypassed-run check makes one section up.
check("2d-u a per-trial run whose rows carry no ledger is REPORTED (it is a "
      "real run and dropping it would hide a whole campaign), with zero calls "
      "on both sides",
      (None if _NOLEDGER is None else (_safe_int(_NOLEDGER.inferences),
                                       _safe_int(_NOLEDGER.warmup_calls),
                                       _safe_int(_NOLEDGER.wave_calls))),
      (2, 0, 0))
check("2d-v ...and BOTH hit rates are NULL rather than 0 -- a rate of 0 would "
      "state that the provider reported and cached nothing, which is the one "
      "reading this query exists to keep separate from 'nobody asked'",
      [_is_null(_v) for _v in
       ((_NOLEDGER.wave_cache_hit_rate, _NOLEDGER.warmup_cache_hit_rate,
         _NOLEDGER.wave_prompt_tokens, _NOLEDGER.wave_cached_tokens)
        if _NOLEDGER is not None else (0, 0, 0, 0))],
      [True, True, True, True])
check("2d-w ...and both its rows are counted as silent on the row column, "
      "which is what says the absence was measured rather than skipped",
      None if _NOLEDGER is None else _safe_int(_NOLEDGER.rows_silent_on_cache),
      2)

# --- the query survives a database that predates the columns -------------
check("2d-q the query declares the four columns whose absence makes its SQL "
      "unparseable, and the derivation checker agrees with the declaration "
      "(the standing guard is section 1 of test_storage_schema_guards.py; this "
      "is the non-degeneracy half -- an empty declaration would satisfy that "
      "one too)",
      (queries.QUERIES_BY_KEY["stage5_cache_effectiveness"].requires_columns,
       len(queries.QUERIES_BY_KEY[
           "stage5_cache_effectiveness"].requires_columns) >= 4),
      (queries.derive_requires_columns(
          queries.QUERIES_BY_KEY["stage5_cache_effectiveness"].sql), True))
check("2d-r ...and declares NO table, so it still answers on a database with "
      "no run tables at all -- the arm is on the inference row and this query "
      "never joins `runs`",
      queries.QUERIES_BY_KEY["stage5_cache_effectiveness"].requires, ())

# --- the shared label constant ------------------------------------------
check("2d-s the two arm-grouped queries bucket an unrecorded mode under ONE "
      "label, so a reader can put cost beside hit rate row for row",
      (queries.MODE_NOT_RECORDED_LABEL
       in queries.QUERIES_BY_KEY["stage5_cache_effectiveness"].sql,
       queries.MODE_NOT_RECORDED_LABEL
       in queries.QUERIES_BY_KEY["call_mode_comparison"].sql), (True, True))
check("2d-t ...and it is NOT the grouped mode's name: a NULL is a row written "
      "before the column existed, and reading it as the default arm would "
      "attribute every pre-era row to an arm nobody measured",
      queries.MODE_NOT_RECORDED_LABEL == MATCHING_CALL_MODE_GROUPED, False)

_arms_conn.close()


# --- (d) the output distribution, per run ----------------------------------
_op = _by_run(_conn, "stage5_output_split_pressure")
check_true("the output-pressure frame covers the same four groups",
           {_RUN_CLEAN, _RUN_DEGRADED, _RUN_CRASHED,
            queries.NO_RUN_LABEL} <= set(_op))
check("split pressure is the batch estimate over the threshold THAT RAN: "
      "20625/28800, which is the config comment's own arithmetic",
      _num(_grp(_op, _RUN_CLEAN).peak_split_pressure, 4),
      round(20625 / 28800, 4))
check("...and the headroom beside it is the 8,175 tokens that note states",
      _num(_grp(_op, _RUN_CLEAN).min_split_headroom_tokens), 28800 - 20625)
check("a run under the threshold reports no inference over it",
      _num(_grp(_op, _RUN_CLEAN).inferences_over_threshold), 0)
check("ceiling pressure comes from the LARGEST SINGLE response, out of the "
      "per-call ledger: 15900/32000",
      _num(_grp(_op, _RUN_CLEAN).peak_call_output_tokens, 4)
      == 15900.0
      and _num(_grp(_op, _RUN_CLEAN).peak_ceiling_pressure, 4)
      == round(15900 / 32000, 4), True)
check("...which is NOT the summed output column, whose value for that run "
      "would give a different answer (non-degeneracy)",
      _num(_grp(_op, _RUN_CLEAN).peak_call_output_tokens)
      == _num(_conn.execute(
          "SELECT MAX(llm_classifier_output_tokens) FROM inferences "
          "WHERE run_id = ?", (_RUN_IDS["RUN-CLEAN"],)).fetchone()[0]),
      False)
check("a run at the OLD ceiling reports its own threshold, not today's",
      (_num(_grp(_op, _RUN_DEGRADED).split_threshold_min),
       _num(_grp(_op, _RUN_DEGRADED).output_ceiling_max)),
      (_THRESHOLD_OLD, _CEILING_OLD))
check("...and its estimate is OVER that threshold, so the headroom is "
      "negative and the row is counted",
      (_num(_grp(_op, _RUN_DEGRADED).peak_split_pressure, 4),
       _num(_grp(_op, _RUN_DEGRADED).min_split_headroom_tokens),
       _num(_grp(_op, _RUN_DEGRADED).inferences_over_threshold)),
      (round(15000 / 14400, 4), 14400 - 15000, 1))
check("...and its worst response came within 100 tokens of the ceiling",
      _num(_grp(_op, _RUN_DEGRADED).min_ceiling_headroom_tokens), 16000 - 15900)
check("the splits actually spent are carried beside the pressure that "
      "predicts them", _num(_grp(_op, _RUN_DEGRADED).splits_spent), 1)
check("a run that never measured reports its whole population as unmeasured "
      "-- which is not the same as low pressure",
      (_num(_grp(_op, _RUN_CRASHED).inferences), _num(_grp(_op, _RUN_CRASHED).unmeasured)),
      (2, 2))
check("...and every pressure reading for it is NULL, not 0",
      [_is_null(_v)
       for _v in (_grp(_op, _RUN_CRASHED).peak_split_pressure,
                  _grp(_op, _RUN_CRASHED).peak_ceiling_pressure,
                  _grp(_op, _RUN_CRASHED).split_threshold_min,
                  _grp(_op, _RUN_CRASHED).peak_call_output_tokens)],
      [True, True, True, True])
check("a campaign spanning ONE configuration reports min == max, which is what "
      "makes a min != max readable as a config change rather than as noise",
      (_num(_grp(_op, _RUN_CLEAN).split_threshold_min)
       == _num(_grp(_op, _RUN_CLEAN).split_threshold_max) == _THRESHOLD_NOW), True)
check("...and the two runs disagree about the threshold, so the pair of "
      "columns is doing work (non-degeneracy)",
      _num(_grp(_op, _RUN_CLEAN).split_threshold_min)
      != _num(_grp(_op, _RUN_DEGRADED).split_threshold_min), True)

# --- (e) nothing is stored twice, and the input ruling MOVED ---------------
#
# WHAT THIS CHECK USED TO PIN, AND WHY IT WAS RIGHT UNTIL IT WAS NOT. It read
# "no INPUT pressure quantity was given a column: the packing blob is the one
# home for the estimate and both budgets", and that was true of the run the
# packer described. It was never true of the two runs it could not describe:
# llm_classifier_packing is published on Stage 5's SUCCESS return only, and
# per-trial mode bypasses the packer that fills it -- so a failed row and every
# row of the shipped call mode had no input figure at all. Era 6 gives the
# input guard the per-row scalar the output guard has had since its own era.
#
# WHAT REPLACES IT IS THE SAME RULE STATED EXACTLY: the input side may store a
# SCALAR and its DENOMINATOR and nothing else. The per-chunk breakdown, the
# packer's effective budget and its two degradation flags stay in the blob,
# where they describe the packer -- so this still fails if a later pass starts
# copying chunk-level packing facts into columns.
_INF_COLS = set(dblog.INFERENCE_COLUMN_ADDITIONS)
check("the INPUT side stores exactly a scalar and its denominator -- era 6's "
      "two columns and no more",
      sorted(c for c in _INF_COLS if c.startswith("llm_classifier_input")),
      ["llm_classifier_input_budget",
       "llm_classifier_input_tokens_estimated"])
check("...and no PER-CHUNK packing fact acquired a column of its own: the "
      "chunk list, the effective budget and the two degradation flags stay in "
      "llm_classifier_packing, which is the one place they describe",
      sorted(c for c in _INF_COLS
             if "chunk" in c and c != "llm_classifier_packed_chunks"), [])
check("...(non-degeneracy: the scan really can see the era-6 columns, so the "
      "two checks above are not both passing over an empty haystack)",
      "llm_classifier_input_budget" in _INF_COLS, True)
check("...and the output side added exactly the two denominators",
      sorted(c for c in _INF_COLS if "output_split_threshold" in c
             or "output_ceiling" in c),
      ["llm_classifier_output_ceiling",
       "llm_classifier_output_split_threshold"])


# ===========================================================================
# SECTION 3 -- THE DELETED EXPANSION QUERY
# ===========================================================================

print()
print("=" * 78)
print("SECTION 3 -- expansion_token_efficiency is deleted, not patched")
print("=" * 78)

check_true("expansion_token_efficiency is gone from the registry",
           "expansion_token_efficiency" not in queries.QUERIES_BY_KEY)
check_raises("...and run() refuses the key rather than answering with an "
             "empty frame", KeyError, queries.run, _conn,
             "expansion_token_efficiency")

check("no surviving query names either column that does not exist",
      sorted(q.key for q in queries.QUERIES
             if "expansion_input_tokens" in q.sql
             or "expansion_output_tokens" in q.sql), [])
# Named exactly, not by an `expansion_` prefix: `expansion_prompt` IS a real
# column and a prefix scan would report it and fail for the wrong reason.
check("...and the schema does not have them either, so adding the query back "
      "would break again",
      sorted({"expansion_input_tokens", "expansion_output_tokens"}
             & _SCHEMA_COLUMNS), [])

check_true("the answerable version survives and says why the token version "
           "cannot exist",
           any("no LLM" in n for n in
               queries.QUERIES_BY_KEY["expansion_stage_stats"].notes))

# NEGATIVE CONTROL. "The query is gone" is also what a registry that lost an
# entry to a typo looks like. This shows the thing that was removed really was
# broken, against the same seeded database, using the SQL as it was committed.
_pre_expansion_sql = _pre_fix_string_constant(
    "expansion_token_efficiency", ["expansion_input_tokens", "over_limit_count"])
check_true(f"the pre-fix SQL was recovered from git (rev {_PRE_FIX_REV})",
           _pre_expansion_sql is not None)
if _pre_expansion_sql:
    check_raises("...and it still raises against the real schema, so the "
                 "deletion removed something genuinely broken",
                 pd.errors.DatabaseError,
                 pd.read_sql_query, _pre_expansion_sql, _conn)


# ===========================================================================
# SECTION 4 -- THE CONSISTENCY QUERY
# ===========================================================================

print()
print("=" * 78)
print("SECTION 4 -- pipeline_consistency")
print("=" * 78)

_pre_consistency_sql = _pre_fix_string_constant(
    "pipeline_consistency", ["Retrieval anomaly", "Rerank anomaly"])
check_true("the pre-fix consistency SQL was recovered from git",
           _pre_consistency_sql is not None)

if _pre_consistency_sql:
    # (a) THE STRAY WHEN WAS A DUPLICATE. Both lines come out of the committed
    # text; neither is retyped here, so this compares the code against itself
    # rather than against my transcription of it.
    _mismatch_lines = [l for l in _pre_consistency_sql.splitlines()
                       if "Count mismatch" in l]
    check("the pre-fix SQL carried the 'Count mismatch' line exactly twice",
          len(_mismatch_lines), 2)
    if len(_mismatch_lines) == 2:
        check("...and the two are identical once indentation is stripped, "
              "which is what makes removing the stray one a no-op for the logic",
              _mismatch_lines[0].strip(), _mismatch_lines[1].strip())
        # The comparison must be able to report inequality, or it proves nothing.
        check("...and the same comparison reports two DIFFERENT lines as "
              "different (negative control)",
              _mismatch_lines[0].strip()
              == _mismatch_lines[1].strip().replace("near_misses", "near_miss"),
              False)
        check_true("...and they really did differ in indentation, so the stray "
                   "one was a separate line and not a mis-parse",
                   _mismatch_lines[0] != _mismatch_lines[1])

    # (b) it was a syntax error, so it had never run once.
    check_raises("the pre-fix consistency SQL is a syntax error against the "
                 "real schema", pd.errors.DatabaseError,
                 pd.read_sql_query, _pre_consistency_sql, _conn)

    # (c) the two literals really were there, so replacing them is not cosmetic.
    check_true("the pre-fix SQL hardcoded 100 and 30",
               "!= 100" in _pre_consistency_sql and "!= 30" in _pre_consistency_sql)

_fixed_sql = queries.QUERIES_BY_KEY["pipeline_consistency"].sql
check_true("the fixed SQL carries no stray WHEN before its CASE",
           _fixed_sql.count("Count mismatch") == 2
           and _fixed_sql.index("CASE") < _fixed_sql.index("Count mismatch"))
check_true("the fused-pool bound is RRF_POOL_SIZE, interpolated from config",
           f"> {RRF_POOL_SIZE}" in _fixed_sql)
check_true("the rerank bound is TOP_K_CANDIDATES, interpolated from config",
           f"> {TOP_K_CANDIDATES}" in _fixed_sql)
check("...and neither literal survives as an equality test",
      [t for t in ("!= 100", "!= 30") if t in _fixed_sql], [])

# WHICH CONSTANT GOVERNS WHICH COLUMN IS DERIVED FROM THE CODE THAT PRODUCES
# THE COLUMN, NOT FROM THE NUMBER THAT USED TO BE THERE. `!= 100` was ambiguous
# by value (VECTOR_RETRIEVAL_SIZE and RRF_POOL_SIZE are both 100) and `!= 30`
# matched no constant in the project at all. The two slices below are the
# derivation; if either moves, this fails and the binding has to be re-derived
# rather than assumed to still hold.
_retrieval_src = open(os.path.join(_CODE_DIR, "oncotriage", "agent",
                                   "retrieval.py"), encoding="utf-8").read()
_terminal_src = open(os.path.join(_CODE_DIR, "oncotriage", "agent",
                                  "terminal.py"), encoding="utf-8").read()
check_true("candidates_retrieved is len(hybrid_results) and hybrid_results is "
           "capped at RRF_POOL_SIZE",
           '"candidates_retrieved": len(state.get("hybrid_results", []))' in _terminal_src
           and "[:RRF_POOL_SIZE]" in _retrieval_src)
check_true("candidates_reranked is len(reranked_trials) and reranked_trials is "
           "capped at TOP_K_CANDIDATES",
           '"candidates_reranked": len(state.get("reranked_trials", []))' in _terminal_src
           and "[:TOP_K_CANDIDATES]" in _retrieval_src)
check_true("the stale literal 30 is NOT the value of the constant that governs "
           "candidates_reranked -- reported, not guessed at",
           TOP_K_CANDIDATES != 30)

# (d) the behaviour, row by row.
#
# THE PER-ROW ASSERTIONS RUN AGAINST AN UNCAPPED VARIANT, and they have to. The
# shipped listing stops at CONSISTENCY_LISTING_LIMIT rows, and this seed
# deliberately produces more than that, so "exactly the inconsistent rows are
# flagged" is not a question the capped query can answer. The variant is BUILT
# FROM THE SHIPPED SQL by raising its cap, never retyped: the cap is a named
# constant interpolated into the SQL, so replacing its rendered value is a
# mechanical edit whose success is asserted below rather than assumed.
_uncapped_sql = _fixed_sql.replace(
    f"LIMIT {queries.CONSISTENCY_LISTING_LIMIT}", "LIMIT 1000000")
check_true("the uncapped variant really differs from the shipped listing "
           "(non-degeneracy: a failed replace would silently re-test the cap)",
           _uncapped_sql != _fixed_sql
           and f"LIMIT {queries.CONSISTENCY_LISTING_LIMIT}" not in _uncapped_sql)

_all_issues = pd.read_sql_query(_uncapped_sql, _conn)
_flagged = dict(zip(_all_issues["patient_id"], _all_issues["issue"]))
check_true("the consistency classification returns rows on the seeded data "
           "(non-degeneracy)", len(_all_issues) > 0)
check("exactly the rows that are inconsistent are flagged, and with the right "
      "category", dict(sorted(_flagged.items())),
      dict(sorted(_EXPECTED_ISSUES.items())))

for _clean in ("P-CONSISTENT-A", "P-CONSISTENT-B", "P-NULL-TOKENS",
               "P-NOMODEL-CLEAN", "P-ERROR", "P-LEGACY-OK"):
    check_true(f"...and {_clean} is NOT flagged", _clean not in _flagged)

check_true("a row whose only 'anomaly' is that it produced fewer candidates "
           "than the cap is clean -- which is what `>` buys over `!=`",
           "P-CONSISTENT-B" not in _flagged
           and int(_conn.execute(
               "SELECT candidates_retrieved FROM inferences "
               "WHERE patient_id = 'P-CONSISTENT-A'").fetchone()[0]) == RRF_POOL_SIZE
           and int(_conn.execute(
               "SELECT candidates_retrieved FROM inferences "
               "WHERE patient_id = 'P-CONSISTENT-B'").fetchone()[0]) < RRF_POOL_SIZE)

check_true("a row with not_evaluable trials that add up is clean -- which is "
           "what including not_evaluable_trials in the identity buys",
           "P-CONSISTENT-A" not in _flagged
           and int(_conn.execute(
               "SELECT not_evaluable_trials FROM inferences "
               "WHERE patient_id = 'P-CONSISTENT-A'").fetchone()[0]) > 0)

_issues = queries.run(_conn, "pipeline_consistency")
check("...and the flagged rows carry the counts a reader needs to act on them",
      [c for c in ("eligible_matches", "near_misses", "not_evaluable_trials")
       if c not in _issues.columns], [])

# NEGATIVE CONTROL for the identity change: under the PRE-FIX two-term identity
# (the one that ignores not_evaluable_trials) P-CONSISTENT-A would have been
# flagged. Computed here rather than asserted, using the seeded row itself.
_a = _conn.execute(
    "SELECT candidates_evaluated, eligible_matches, near_misses, "
    "not_evaluable_trials FROM inferences WHERE patient_id = 'P-CONSISTENT-A'"
).fetchone()
check_true("the two-term identity WOULD have flagged a perfectly ordinary row, "
           "which is why it was wrong", _a[0] != (_a[1] + _a[2]))
check("...and the three-term identity does not", _a[0], _a[1] + _a[2] + _a[3])


# ===========================================================================
# SECTION 4b -- THE COMPANION TOTALS, AND THE LISTING'S DETERMINISM
# ===========================================================================

print()
print("=" * 78)
print("SECTION 4b -- the totals beside the capped listing")
print("=" * 78)

# THE PRECONDITION. Everything below is about a listing that cannot show
# everything; if the seed never exceeded the cap, every check would pass for the
# wrong reason and the companion would be agreeing with a listing that had
# nothing left over to disagree about.
check_true("the seed produces MORE inconsistent rows than the listing can show "
           "(non-degeneracy: this is what the companion exists for)",
           len(_EXPECTED_ISSUES) > queries.CONSISTENCY_LISTING_LIMIT)
check_true("...across at least two categories, neither of which fills the cap "
           "on its own", len(_EXPECTED_ISSUE_COUNTS) >= 2
           and max(_EXPECTED_ISSUE_COUNTS.values())
           < queries.CONSISTENCY_LISTING_LIMIT)

# EVERY REFERENCE TO THE COMPANION GOES THROUGH A LOOKUP THAT CANNOT RAISE.
# `QUERIES_BY_KEY["..."]` and `QUERY_KEYS.index("...")` both abort the run when
# the key is absent, which is exactly what deleting the companion produces --
# so the check written to catch that deletion would have crashed instead of
# reporting it, hiding the ninety checks below. Demonstrated, not imagined.
_TOTALS_KEY = "pipeline_consistency_totals"
_totals_query = queries.QUERIES_BY_KEY.get(_TOTALS_KEY)
check_true(f"the registry declares {_TOTALS_KEY!r}", _totals_query is not None)

check("the companion runs immediately BEFORE the listing, so the totals print "
      "above the sample",
      registry_index("pipeline_consistency") - registry_index(_TOTALS_KEY), 1)

_totals = (queries.run(_conn, _TOTALS_KEY) if _totals_query is not None
           else pd.DataFrame(columns=["issue", "n"]))
check("the companion reports every category, with the counts the seed put there",
      dict(sorted(zip(_totals["issue"], (int(n) for n in _totals["n"])))),
      dict(sorted(_EXPECTED_ISSUE_COUNTS.items())))

check("the listing is capped at exactly CONSISTENCY_LISTING_LIMIT rows",
      len(_issues), queries.CONSISTENCY_LISTING_LIMIT)
check_true("...and the companion's total EXCEEDS what the listing shows, which "
           "is the fact a reader could not previously recover",
           int(_totals["n"].sum()) > len(_issues))
check("...and the companion's total equals the uncapped row count",
      int(_totals["n"].sum()), len(_all_issues))

check("every row the listing DOES show is one the classification flagged, with "
      "the same category",
      sorted({(p, i) for p, i in zip(_issues["patient_id"], _issues["issue"])}
             - {(p, i) for p, i in _EXPECTED_ISSUES.items()}), [])
check_true("...and the sample spans more than one category, so ordering by "
           "issue has not collapsed it onto the first one",
           len(set(_issues["issue"])) >= 2)

# --- DETERMINISM ----------------------------------------------------------
#
# The listing had no ORDER BY, so SQLite was free to return a different twenty
# on each execution. Two runs, compared as ORDERED SEQUENCES rather than as
# sets: a set comparison would pass on a query that returned the same rows in a
# different order, which is exactly the failure being ruled out.
_run_a = queries.run(_conn, "pipeline_consistency")
_run_b = queries.run(_conn, "pipeline_consistency")
check("two runs of the listing return the same patient_ids in the same order",
      list(_run_a["patient_id"]), list(_run_b["patient_id"]))
check("...and the same row ids, which is the part patient_id cannot pin",
      list(_run_a["id"]), list(_run_b["id"]))
check_true("the ordering is TOTAL, not merely stable-looking: the sequence is "
           "sorted by (issue, patient_id, id) and every key is distinct",
           list(zip(_run_a["issue"], _run_a["patient_id"], _run_a["id"]))
           == sorted(zip(_run_a["issue"], _run_a["patient_id"], _run_a["id"]))
           and len(set(_run_a["id"])) == len(_run_a))

# patient_id ALONE would not have been a total order, which is why `id` is
# selected. Measured on the seeded table the same way it was measured on
# production (1,106 rows, 1,004 distinct patient_ids) -- here by planting a
# duplicate, because the seed's own ids are unique by construction.
_cursor.execute(
    "INSERT INTO inferences (patient_id, timestamp, matching_model, error, "
    "candidates_retrieved, candidates_reranked, candidates_filtered, "
    "candidates_evaluated, eligible_matches, near_misses, "
    "not_evaluable_trials, llm_classifier_input_tokens, llm_classifier_output_tokens, "
    "estimated_cost_usd) VALUES ('P-BULK-COUNT-000', '2026-08-01', ?, '', "
    "95, 38, 12, 12, 4, 4, 1, 700, 300, 0.005)", (_MODEL_A,))
_conn.commit()
_dupe_ids = [r[0] for r in _conn.execute(
    "SELECT id FROM inferences WHERE patient_id = 'P-BULK-COUNT-000' ORDER BY id")]
check_true("a duplicated patient_id really produces two rows (non-degeneracy)",
           len(_dupe_ids) == 2)
_dupe_run = queries.run(_conn, "pipeline_consistency")
check("...and the listing still returns them in a fixed order, because `id` "
      "breaks the tie",
      [int(i) for i in _dupe_run.loc[
          _dupe_run["patient_id"] == "P-BULK-COUNT-000", "id"]],
      sorted(int(i) for i in _dupe_ids))
_conn.execute("DELETE FROM inferences WHERE id = ?", (max(_dupe_ids),))
_conn.commit()
check("the duplicate is removed again, so later sections see the seed they "
      "expect", _conn.execute("SELECT COUNT(*) FROM inferences").fetchone()[0],
      len(_SEED_ROWS))

# --- THE CLEAN CASE STILL READS AS CLEAN ----------------------------------
#
# The whole point of render='skip_if_empty'. On a database with no issues the
# companion must print NOTHING -- not its heading, not its note, not an empty
# table -- so the listing's clean message stands alone exactly as it always did.
_clean_lines = []
_clean_conn = sqlite3.connect(_EMPTY_DB_PATH)
try:
    queries.report(_clean_conn, out=_clean_lines.append)
finally:
    _clean_conn.close()
_clean_text = "\n".join(str(l) for l in _clean_lines)
_TOTALS_HEADING = (_totals_query.heading if _totals_query is not None
                   else "(no companion query in the registry)")
_TOTALS_NOTES = _totals_query.notes if _totals_query is not None else ()

check("on a clean database the clean message is printed exactly once",
      _clean_text.count(queries.CONSISTENCY_CLEAN_MESSAGE), 1)
check("...and the companion prints nothing at all above it -- not its heading",
      _TOTALS_HEADING in _clean_text, False)
check("...not its note either",
      any(n in _clean_text for n in _TOTALS_NOTES), False)

# ...and on the seeded database it prints all three.
_seeded_lines = []
queries.report(_conn, out=_seeded_lines.append)
_seeded_text = "\n".join(str(l) for l in _seeded_lines)
check_true("with issues present the companion DOES print its heading and note "
           "(negative control for the three checks above)",
           _TOTALS_HEADING in _seeded_text
           and bool(_TOTALS_NOTES)
           and all(n in _seeded_text for n in _TOTALS_NOTES))
check_true("...and the totals appear ABOVE the listing in the printed report",
           _TOTALS_HEADING in _seeded_text
           and _seeded_text.index(_TOTALS_HEADING)
           < _seeded_text.index(
               queries.QUERIES_BY_KEY["pipeline_consistency"].heading))
check("...and the clean message does NOT appear when there are issues",
      queries.CONSISTENCY_CLEAN_MESSAGE in _seeded_text, False)

# --- ONE CASE, NOT TWO ----------------------------------------------------
#
# The instruction was that the two queries agree "by construction rather than by
# two copies of the same CASE". They do: there is one _CONSISTENCY_CASE_SQL and
# one _CONSISTENCY_CLASSIFIED_SQL, and both queries interpolate them. Asserted
# by containment first, then DEMONSTRATED by mutating the shared source and
# showing both derived queries move together -- which is what "cannot be edited
# in one place only" means operationally.
_totals_sql = _totals_query.sql if _totals_query is not None else ""
check_true("the shared CASE appears verbatim in the listing",
           queries._CONSISTENCY_CASE_SQL in _fixed_sql)
check_true("...and verbatim in the companion",
           queries._CONSISTENCY_CASE_SQL in _totals_sql)
check("the CASE appears exactly once in each, so neither carries a second copy",
      (_fixed_sql.count(queries._CONSISTENCY_CASE_SQL),
       _totals_sql.count(queries._CONSISTENCY_CASE_SQL)), (1, 1))

_mutated_case = queries._CONSISTENCY_CASE_SQL.replace(
    "'Rerank anomaly'", "'MUTATED CATEGORY'")
check_true("the mutation applies (non-degeneracy)",
           _mutated_case != queries._CONSISTENCY_CASE_SQL)
_mutated_classified = queries._CONSISTENCY_CLASSIFIED_SQL.replace(
    queries._CONSISTENCY_CASE_SQL, _mutated_case)
_rebuilt_listing = _fixed_sql.replace(
    queries._CONSISTENCY_CLASSIFIED_SQL, _mutated_classified)
_rebuilt_totals = _totals_sql.replace(queries._CONSISTENCY_CLASSIFIED_SQL,
                                      _mutated_classified)
check_true("editing the ONE CASE changes both derived queries together -- there "
           "is no second copy to forget",
           "MUTATED CATEGORY" in _rebuilt_listing
           and "MUTATED CATEGORY" in _rebuilt_totals)
check_true("...and both rebuilt queries execute, so the shared text really is "
           "the whole classification and not a fragment of it",
           len(pd.read_sql_query(_rebuilt_listing, _conn)) > 0
           and len(pd.read_sql_query(_rebuilt_totals, _conn)) > 0)
check_true("...and the mutated category reaches the RESULTS of both, not just "
           "their text",
           "MUTATED CATEGORY" in set(pd.read_sql_query(_rebuilt_listing,
                                                       _conn)["issue"])
           and "MUTATED CATEGORY" in set(pd.read_sql_query(_rebuilt_totals,
                                                           _conn)["issue"]))

# --- THE CASE ITSELF IS UNCHANGED BY THIS PASS ----------------------------
#
# The residual pass was permitted to add an ORDER BY to the listing and nothing
# else: item 38's categories, bounds and NULL handling are correct and this is
# the mechanism for "and nothing else". TWO INDEPENDENT PINS, because they fail
# in different circumstances and neither subsumes the other.
#
# (i) THE COMMITTED TEXT. Pulled out of item 38's own blob, rendered through the
# same two config constants the source interpolates, and compared byte for byte
# against what this module holds now. This is the authoritative pin: it compares
# code against code with nothing retyped in between.
_ITEM38_CASE = None
if _ITEM38_SRC:
    _start = _ITEM38_SRC.find("        CASE\n")
    _end = _ITEM38_SRC.find("        END as issue", _start)
    if _start != -1 and _end != -1:
        _raw = _ITEM38_SRC[_start:_end + len("        END as issue")]
        try:
            # The committed text is f-string SOURCE, so it still carries
            # {RRF_POOL_SIZE} / {TOP_K_CANDIDATES}. Rendering it with the same
            # constants is what makes the comparison apples-to-apples; the
            # category literals this pass moved into named constants render back
            # to the same strings, which is the point of naming them.
            _ITEM38_CASE = _raw.format(RRF_POOL_SIZE=RRF_POOL_SIZE,
                                       TOP_K_CANDIDATES=TOP_K_CANDIDATES)
        except (KeyError, IndexError, ValueError) as _exc:
            print(f"  [git] item-38 CASE would not render: "
                  f"{type(_exc).__name__}: {_exc}")

check_true(f"item 38's own CASE was recovered from git (rev {_ITEM38_REV}) "
           f"and is non-degenerate",
           _ITEM38_CASE is not None and len(_ITEM38_CASE) > 500)
if _ITEM38_CASE:
    check("the CASE is byte-identical to the one item 38 committed",
          queries._CONSISTENCY_CASE_SQL, _ITEM38_CASE)
    check("...and the same comparison rejects a one-category change (negative "
          "control)", _mutated_case == _ITEM38_CASE, False)

# (ii) A sha256 MEASURED FROM THE SHIPPED ARTEFACT BEFORE THE REFACTOR. It
# duplicates (i) on a machine with history and is the only pin left on one
# without -- a shallow clone, an exported tarball, a container build. Recorded
# rather than derived precisely so it does not depend on git.
_CASE_SHA256_AS_SHIPPED_BY_ITEM_38 = (
    "c73948cffb6d276582a6533bf8b7b2ed792894f3b75a6da8afe17d3ec3eaee10")
check("the CASE block hashes to what was measured before the refactor",
      hashlib.sha256(queries._CONSISTENCY_CASE_SQL.encode()).hexdigest(),
      _CASE_SHA256_AS_SHIPPED_BY_ITEM_38)
check("...and the hash comparison notices a one-character change (negative "
      "control)",
      hashlib.sha256(_mutated_case.encode()).hexdigest()
      == _CASE_SHA256_AS_SHIPPED_BY_ITEM_38, False)
# The two pins must agree, or one of them is measuring something else.
if _ITEM38_CASE:
    check("...and the two pins agree with each other",
          hashlib.sha256(_ITEM38_CASE.encode()).hexdigest(),
          _CASE_SHA256_AS_SHIPPED_BY_ITEM_38)

# The listing's own additions, pinned separately from the CASE.
check_true("the listing orders by (issue, patient_id, id) before its LIMIT",
           "ORDER BY issue, patient_id, id" in _fixed_sql
           and _fixed_sql.index("ORDER BY issue, patient_id, id")
           < _fixed_sql.index("LIMIT"))
check_true("`id` is selected, which is what makes that order total",
           "\n        id,\n" in queries._CONSISTENCY_CLASSIFIED_SQL)


# ===========================================================================
# SECTION 4c -- THE NULL GUARD'S COLUMN SET IS DERIVED FROM THE SQL
# ===========================================================================

print()
print("=" * 78)
print("SECTION 4c -- every compared column is guarded or NULL-aware")
print("=" * 78)

# THE RULE, and it is not "every compared column is in the guard". The guard
# names six columns; not_evaluable_trials is deliberately NOT among them,
# because it is an added column that is legitimately NULL on pre-migration rows
# and flagging those as "counters not reported" would report a schema migration
# as a pipeline defect. It has the other treatment instead: its own pair of
# NULL-aware branches.
#
# So: EVERY COLUMN THE CASE COMPARES MUST EITHER BE IN THE NULL GUARD, OR HAVE
# AN EXPLICIT BRANCH HANDLING ITS NULL CASE. Both sets are derived from the SQL
# text below rather than listed here, so a seventh counter added later cannot
# quietly skip both.


def _sql_identifiers(text):
    """Real `inferences` columns named in a fragment of SQL.

    String literals are stripped first, so 'Count mismatch' contributes nothing.
    Intersecting with the REAL SCHEMA rather than filtering a keyword list is
    what keeps this honest: SQL keywords, aliases and numbers are excluded
    because they are not columns, not because somebody remembered to list them.
    """
    without_literals = re.sub(r"'[^']*'", " ", text)
    return {w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", without_literals)
            if w in _SCHEMA_COLUMNS}


def _null_guard_and_compared(case_sql):
    """Split a CASE into (guarded, compared, null_aware) column sets.

    The guard is located by the category it emits, not by position: a branch
    order change must not silently turn a different branch into "the guard".
    """
    marker = f"'{queries.CONSISTENCY_GUARD_CATEGORY}'"
    if marker not in case_sql:
        return None, None, None
    head, tail = case_sql.split(marker, 1)
    # The guard branch is the last WHEN before the marker.
    guard_branch = head[head.rindex("WHEN"):]
    guarded = _sql_identifiers(guard_branch)
    compared = _sql_identifiers(tail)
    null_aware = {m for m in re.findall(
        r"([A-Za-z_][A-Za-z0-9_]*)\s+IS\s+(?:NOT\s+)?NULL", tail)
        if m in _SCHEMA_COLUMNS}
    return guarded, compared, null_aware


_guarded, _compared, _null_aware = _null_guard_and_compared(
    queries._CONSISTENCY_CASE_SQL)

check_true("the guard branch was located and is non-degenerate",
           _guarded is not None and len(_guarded) >= 5)
check_true("the compared set is non-degenerate too -- an empty one would make "
           "the rule below vacuous", _compared and len(_compared) >= 5)
check_true("not_evaluable_trials is compared but deliberately NOT guarded, "
           "which is why the rule is a disjunction",
           "not_evaluable_trials" in _compared
           and "not_evaluable_trials" not in _guarded)
check_true("...and it is the NULL-aware set that covers it",
           "not_evaluable_trials" in _null_aware)

check("EVERY column the CASE compares is either in the NULL guard or has its "
      "own NULL-aware branch",
      sorted(_compared - _guarded - _null_aware), [])

# --- TWO NEGATIVE CONTROLS, and the second is the one that matters --------
#
# The first shows the rule catches an unguarded new column. Alone it would be
# satisfied by a check that simply rejects every new column, which is a
# different and wrong rule -- it would forbid the treatment not_evaluable_trials
# already uses. The second shows a new column WITH a NULL-aware branch passes.
_CONTROL_COLUMN = "mesh_dropped"      # a real column, absent from the CASE
check_true(f"the control column {_CONTROL_COLUMN!r} is real and not already in "
           f"the CASE (non-degeneracy)",
           _CONTROL_COLUMN in _SCHEMA_COLUMNS
           and _CONTROL_COLUMN not in _compared)

_control_untreated = queries._CONSISTENCY_CASE_SQL.replace(
    "            ELSE '",
    f"            WHEN {_CONTROL_COLUMN} > 5 THEN 'Planted anomaly'\n"
    f"            ELSE '")
check_true("the untreated control was planted (non-degeneracy)",
           _control_untreated != queries._CONSISTENCY_CASE_SQL)
_g1, _c1, _n1 = _null_guard_and_compared(_control_untreated)
check(f"a seventh compared column with NEITHER treatment is REPORTED",
      sorted(_c1 - _g1 - _n1), [_CONTROL_COLUMN])

_control_treated = queries._CONSISTENCY_CASE_SQL.replace(
    "            ELSE '",
    f"            WHEN {_CONTROL_COLUMN} IS NOT NULL\n"
    f"             AND {_CONTROL_COLUMN} > 5 THEN 'Planted anomaly'\n"
    f"            ELSE '")
check_true("the treated control was planted (non-degeneracy)",
           _control_treated != queries._CONSISTENCY_CASE_SQL)
_g2, _c2, _n2 = _null_guard_and_compared(_control_treated)
check("...and a seventh compared column WITH a NULL-aware branch PASSES -- "
      "without this the rule would collapse into 'any new column fails'",
      sorted(_c2 - _g2 - _n2), [])
check_true("...and the treated control really did add the column to the "
           "compared set, so it passed for the right reason",
           _CONTROL_COLUMN in _c2 and _CONTROL_COLUMN in _n2)

# Both control CASEs must still be valid SQL, or the controls are testing a
# string rather than a query.
for _label, _case in (("untreated", _control_untreated),
                      ("treated", _control_treated)):
    check_does_not_raise(
        f"the {_label} control CASE is executable SQL",
        pd.read_sql_query,
        queries._PIPELINE_CONSISTENCY_SQL.replace(
            queries._CONSISTENCY_CASE_SQL, _case), _conn)


# ===========================================================================
# SECTION 5 -- THE COST ARITHMETIC
# ===========================================================================

print()
print("=" * 78)
print("SECTION 5 -- price_model_groups on the float64 case")
print("=" * 78)

_groups_sql = queries.run(_conn, "cost_by_model")
check_true("the SQL group frame is non-degenerate (three model groups, one of "
           "them NULL)", len(_groups_sql) == 3)

# THE PRECONDITION THIS WHOLE SECTION RESTS ON. If the aggregate columns did
# not come back float64 with NaN in them, every check below would pass for the
# wrong reason.
check("the token aggregates are float64, which is what a NULL group beside a "
      "numeric one produces", str(_groups_sql["input_tokens"].dtype), "float64")
_null_group = _groups_sql[_groups_sql["matching_model"] == _MODEL_B].iloc[0]
check_true("...and the all-NULL group's SUM really is NaN rather than 0",
           pd.isna(_null_group["input_tokens"]))
check_true("...so `int(x or 0)` on it raises, because nan is TRUTHY",
           isinstance(check_raises(
               "  (int(nan or 0) raises ValueError)", ValueError,
               lambda: int(_null_group["input_tokens"] or 0)), ValueError))
check_true("...and `x is None` on it is FALSE while pd.isna is True, which is "
           "why the reasoning-token test never fired",
           (_null_group["reasoning_tokens"] is not None)
           and pd.isna(_null_group["reasoning_tokens"]))

_priced = check_does_not_raise("cost_by_model prices the float64 frame without "
                              "raising", queries.cost_by_model, _conn)
# `_priced or []` would raise: a DataFrame has no truth value. `is not None`,
# every time.
check("...into one row per model group",
      len(_priced) if _priced is not None else -1, 3)
check("...with the pinned column set",
      list(_priced.columns) if _priced is not None else [],
      list(queries.PRICED_COST_COLUMNS))

_by_label = {r.matching_model: r for r in _priced.itertuples(index=False)}

# A NULL IS NOT REPORTED AS A ZERO.
check_true("the all-NULL group's token counts come back <NA>, not 0",
           pd.isna(_by_label[_MODEL_B].input_tokens)
           and pd.isna(_by_label[_MODEL_B].output_tokens)
           and pd.isna(_by_label[_MODEL_B].reasoning_tokens))
check_true("...and its note says the SUM was NULL rather than zero",
           "SUM was NULL" in _by_label[_MODEL_B].note)
check_true("...and its stored_cost is <NA> too, not 0.0",
           pd.isna(_by_label[_MODEL_B].stored_cost))
check("...and it is priced at zero spend, without raising",
      float(_by_label[_MODEL_B].recomputed_cost), 0.0)

# A GROUP THAT REALLY DID RECORD ZERO IS DIFFERENT FROM ONE THAT RECORDED
# NOTHING, and the frame keeps them apart.
#
# The expected sums are SUMMED FROM THE SEED TABLE rather than written as a
# literal. A literal was the first version and it went stale the moment the
# consistency section needed more rows -- and a stale expectation in a check
# about arithmetic is the shape this project treats as a defect, because it
# fails for a reason that has nothing to do with the code under test.
_expected_a = {"in": 0, "out": 0, "reasoning": 0}
for _label, _overrides in _SEED_ROWS:
    if _overrides.get("matching_model") != _MODEL_A:
        continue
    for _key, _column in (("in", "llm_classifier_input_tokens"),
                          ("out", "llm_classifier_output_tokens"),
                          ("reasoning", "llm_classifier_reasoning_tokens")):
        _value = _overrides.get(_column)
        if _value is not None:
            _expected_a[_key] += _value
check_true("the expected sums are non-degenerate (a zero on both sides would "
           "make the comparison below vacuous)",
           all(v > 0 for v in _expected_a.values()))
check("the group with numbers reports them as integers",
      (int(_by_label[_MODEL_A].input_tokens),
       int(_by_label[_MODEL_A].output_tokens),
       int(_by_label[_MODEL_A].reasoning_tokens)),
      (_expected_a["in"], _expected_a["out"], _expected_a["reasoning"]))
check("...priced against its own model's rates, not a blended one",
      round(float(_by_label[_MODEL_A].recomputed_cost), 10),
      round(get_model_cost(_MODEL_A, int(_by_label[_MODEL_A].input_tokens), 0)
            + get_model_cost(_MODEL_A, 0, int(_by_label[_MODEL_A].output_tokens)), 10))

# THE NULL-MODEL GROUP IS REPORTED, NOT DROPPED.
check_true("the NULL-model group is present and labelled",
           queries.NO_MODEL_LABEL in _by_label)
check("...and marked as not having a recorded model",
      bool(_by_label[queries.NO_MODEL_LABEL].model_recorded), False)
check_true("...and, because it carries tokens, its note calls that a logging "
           "defect",
           "logging defect" in _by_label[queries.NO_MODEL_LABEL].note)

# The other half of that note, on a group that legitimately has no tokens.
_clean_null_model = queries.price_model_groups(pd.DataFrame({
    "matching_model": [None], "rows_n": [3], "input_tokens": [0],
    "output_tokens": [0], "reasoning_tokens": [None], "stored_cost": [0.0]}))
check("a NULL-model group with no tokens is reported as ordinary, not as a "
      "defect", _clean_null_model.iloc[0]["note"], "no model recorded")

# THE CONTRACT IS ENFORCED RATHER THAN ASSUMED.
check_raises("a group frame missing a required column raises with the column "
             "named, instead of producing a partial breakdown",
             ValueError, queries.price_model_groups,
             _groups_sql.drop(columns=["reasoning_tokens"]))
check_true("an unpriced model still raises, even with zero tokens",
           isinstance(check_raises(
               "  (unpriced model raises)", UnknownModelPricingError,
               queries.price_model_groups, pd.DataFrame({
                   "matching_model": ["not-a-model"], "rows_n": [1],
                   "input_tokens": [0], "output_tokens": [0],
                   "reasoning_tokens": [None], "stored_cost": [0.0]})),
               UnknownModelPricingError))

# NEGATIVE CONTROL: THE PRE-FIX ARITHMETIC, EXEC'd FROM ITS COMMITTED SOURCE.
# Not a transcription of it -- the function is unparsed out of the blob git
# holds and run against the very frame the fixed one just handled.
_pre_cost_src = _pre_fix_function("cost_by_model")
check_true("the pre-fix cost_by_model was recovered from git",
           _pre_cost_src is not None)
if _pre_cost_src:
    _ns = {"pd": pd, "get_model_cost": get_model_cost,
           "run": lambda conn, key: _groups_sql}
    exec(compile(_pre_cost_src, "<pre-fix cost_by_model>", "exec"), _ns)
    check_raises("...and it raises ValueError on exactly the frame the fixed "
                 "one prices, which is the defect item 38 removes",
                 ValueError, _ns["cost_by_model"], _conn)

# print_cost_by_model's totals.
_printed = []
_printed_frame = check_does_not_raise(
    "print_cost_by_model runs on the float64 frame",
    queries.print_cost_by_model, _conn, out=_printed.append)
_printed_text = "\n".join(str(l) for l in _printed)
check_true("...and its stored total excludes the NULL group instead of turning "
           "into nan", "nan" not in after(_printed_text.lower(), "recomputed")[:200])
check_true("...and it says how many groups recorded no stored cost at all",
           "recorded no stored cost" in _printed_text)


# ===========================================================================
# SECTION 5b -- AN UNRECORDED COST IS VISIBLE AT EVERY TOTAL
# ===========================================================================

print()
print("=" * 78)
print("SECTION 5b -- cost_complete")
print("=" * 78)

# THE DEFECT THIS CLOSES. An unpriceable group contributes a REAL 0.0 to
# recomputed_cost -- not a NULL -- so nothing about the column reveals that the
# total is a floor. A consumer summing it under-reports by exactly the
# unpriceable spend and cannot tell. The note column said so in prose; prose is
# not a field, and a published cost-per-patient figure is computed from the
# number.

check_true("cost_complete is in the priced frame's pinned column set",
           "cost_complete" in queries.PRICED_COST_COLUMNS)
check("...immediately after the column it qualifies, so the relationship is "
      "visible in the printed table",
      queries.PRICED_COST_COLUMNS[
          queries.PRICED_COST_COLUMNS.index("recomputed_cost") + 1],
      "cost_complete")

# The seed carries all three shapes at once, which is the only arrangement in
# which the flag can be shown to discriminate rather than to be constant.
check("the priced frame reports completeness per group, and it is NOT constant "
      "(non-degeneracy: an all-True or all-False column would satisfy every "
      "check below)",
      sorted(set(bool(v) for v in _priced["cost_complete"])), [False, True])

check("the group with real tokens and a real model is COMPLETE",
      bool(_by_label[_MODEL_A].cost_complete), True)
check("the group whose token SUMs are NULL is INCOMPLETE -- nothing is known "
      "about its spend", bool(_by_label[_MODEL_B].cost_complete), False)
check("the NULL-model group carrying tokens is INCOMPLETE -- its consumption is "
      "known and there is no rate to price it at",
      bool(_by_label[queries.NO_MODEL_LABEL].cost_complete), False)
check("a NULL-model group carrying NO tokens is COMPLETE -- an ordinary "
      "no-candidates run really did spend nothing",
      bool(_clean_null_model.iloc[0]["cost_complete"]), True)

# A group missing only its stored cost is still complete on the recomputed side.
# This is the distinction the docstring makes and it is easy to get wrong by
# folding both nulls into one flag.
_stored_only_missing = queries.price_model_groups(pd.DataFrame({
    "matching_model": [_MODEL_A], "rows_n": [2], "input_tokens": [1000],
    "output_tokens": [500], "reasoning_tokens": [None], "stored_cost": [None]}))
check("a group whose STORED cost is NULL but whose tokens are recorded is "
      "cost_complete -- the flag qualifies recomputed_cost and nothing else",
      bool(_stored_only_missing.iloc[0]["cost_complete"]), True)
check("...and its stored_cost is still <NA>, which is the separate signal a "
      "consumer asks for that sum",
      bool(pd.isna(_stored_only_missing.iloc[0]["stored_cost"])), True)

# THE BOOLEAN AND THE PROSE MUST NOT DISAGREE. Computed independently -- the
# flag from the data, the note from the same data by a different path -- so this
# compares two derivations rather than a value against itself.
_disagreements = []
for _row in _priced.itertuples(index=False):
    _note_says_incomplete = any(frag in _row.note
                                for frag in queries.COST_INCOMPLETE_NOTES)
    if _note_says_incomplete == bool(_row.cost_complete):
        _disagreements.append(
            f"{_row.matching_model}: cost_complete={_row.cost_complete} "
            f"note={_row.note!r}")
check("cost_complete and the note column agree on every group",
      _disagreements, [])
check_true("...and both fired on at least one group, so the agreement is not "
           "between two empty sets",
           any(frag in _row.note for _row in _priced.itertuples(index=False)
               for frag in queries.COST_INCOMPLETE_NOTES))

# THE PRICED VALUE IS UNCHANGED -- the instruction was explicit that an
# incomplete group must keep pricing at $0.00 rather than becoming NaN, because
# NaN would propagate into every aggregate and produce no number at all.
for _label in (_MODEL_B, queries.NO_MODEL_LABEL):
    check("an incomplete group still prices at 0.0, not NaN "
          f"({_label})", float(_by_label[_label].recomputed_cost), 0.0)
check_true("...so the recomputed total is a real number rather than NaN",
           not pd.isna(_priced["recomputed_cost"].sum()))

# THE TOTALS SAY SO. This is the fix: not a new note, but a qualifier on every
# figure derived from the total.
check_true("print_cost_by_model marks the recomputed total as a FLOOR",
           "A FLOOR, NOT A TOTAL" in _printed_text)
check_true("...names how many groups and rows could not be priced",
           "could not be priced from what was recorded" in _printed_text)
check_true("...names the groups themselves, so the reader can go and look",
           _MODEL_B in after(_printed_text, "could not be priced")[:400])
check_true("...points at the field to ask rather than only at the prose",
           "cost_complete" in _printed_text)
check_true("...and qualifies the 1000-patient projection too, which is the "
           "number most likely to be quoted",
           "(a FLOOR" in after(_printed_text, "Projected cost")[:200])

# NEGATIVE CONTROL: with every group complete, none of those lines appears.
# Without this, "the report says FLOOR" is satisfied by a report that says it
# unconditionally, which is the same defect one step along.
_complete_only = queries.price_model_groups(pd.DataFrame({
    "matching_model": [_MODEL_A, _MODEL_B], "rows_n": [4, 2],
    "input_tokens": [1000, 2000], "output_tokens": [500, 600],
    "reasoning_tokens": [None, 10], "stored_cost": [0.01, 0.02]}))
check("the control frame really is all-complete (non-degeneracy)",
      sorted(set(bool(v) for v in _complete_only["cost_complete"])), [True])

_complete_printed = []
_saved_cost_by_model = queries.cost_by_model
try:
    queries.cost_by_model = lambda conn: _complete_only
    queries.print_cost_by_model(None, out=_complete_printed.append)
finally:
    queries.cost_by_model = _saved_cost_by_model
check_true("the module-level rebinding was undone",
           queries.cost_by_model is _saved_cost_by_model)
_complete_text = "\n".join(str(l) for l in _complete_printed)
check("with every group complete, the FLOOR marker is absent",
      "A FLOOR, NOT A TOTAL" in _complete_text, False)
check("...and so is the incomplete-groups line",
      "could not be priced from what was recorded" in _complete_text, False)
check("...and the projection is unqualified",
      "(a FLOOR" in after(_complete_text, "Projected cost"), False)
check_true("...while the report itself still printed (non-degeneracy)",
           "Recomputed total:" in _complete_text)


# ===========================================================================
# SECTION 6 -- THE DASHBOARD CONSUMES THE QUERY LAYER
# ===========================================================================

print()
print("=" * 78)
print("SECTION 6 -- the duplication is gone, not moved")
print("=" * 78)

_full_frame = pd.read_sql_query("SELECT * FROM inferences", _conn)
check_true("the frame the dashboard path aggregates is non-empty "
           "(non-degeneracy)", len(_full_frame) == len(_SEED_ROWS))

_groups_pandas = queries.model_groups_from_frame(_full_frame)
check("the pandas aggregate carries the contract's columns",
      list(_groups_pandas.columns), list(queries.COST_GROUP_COLUMNS))

_priced_dashboard = check_does_not_raise(
    "the dashboard path prices the same seeded database without raising",
    queries.price_model_groups, _groups_pandas)

check_true(
    "THE DASHBOARD'S FIGURES EQUAL THE QUERY LAYER'S, frame for frame",
    _priced_dashboard is not None
    and _priced_dashboard.equals(_priced))

# NEGATIVE CONTROL: the equality check must be able to report a difference.
_perturbed = _priced_dashboard.copy()
_perturbed.loc[0, "recomputed_cost"] = float(_perturbed.loc[0, "recomputed_cost"]) + 1e-6
check("...and that comparison reports a one-microdollar difference as unequal",
      _perturbed.equals(_priced), False)

# min_count=1 IS WHAT MAKES THE TWO AGREE, and the disagreement it removes is
# demonstrated rather than described.
_naive = _full_frame.groupby("matching_model", dropna=False)[
    ["llm_classifier_input_tokens"]].sum()
_naive_b = _naive.loc[_MODEL_B, "llm_classifier_input_tokens"]
check("without min_count=1 pandas reports the all-NULL group as 0.0, where SQL "
      "reports NULL", float(_naive_b), 0.0)
check_true("...and with min_count=1 it agrees with SQL",
           pd.isna(_groups_pandas.loc[
               _groups_pandas["matching_model"] == _MODEL_B,
               "input_tokens"].iloc[0]))

# groupby(dropna=False) labels the missing group `nan`, NOT None -- so the
# `is None` test the query layer used would have called it a real model name
# and handed it to get_model_cost. Demonstrated on the actual label.
_pandas_labels = list(_groups_pandas["matching_model"])
_nan_labels = [l for l in _pandas_labels if pd.isna(l)]
check("the pandas group frame carries exactly one missing-model label",
      len(_nan_labels), 1)
check_true("...and `is None` does NOT recognise it, while pd.isna does -- which "
           "is the fault the consolidation would have inherited",
           (_nan_labels[0] is not None) and pd.isna(_nan_labels[0]))

# STRUCTURAL: no second copy of the arithmetic survives in the tab.
_TAB_REL = os.path.join("oncotriage", "dashboard", "tabs", "cost_tokens.py")
_tab_src = open(os.path.join(_CODE_DIR, _TAB_REL), encoding="utf-8").read()
_tab_tree = ast.parse(_tab_src)

_tab_calls = sorted({
    (node.func.attr if isinstance(node.func, ast.Attribute)
     else node.func.id if isinstance(node.func, ast.Name) else "")
    for node in ast.walk(_tab_tree) if isinstance(node, ast.Call)})
check_true("the cost tab no longer calls get_model_cost itself",
           "get_model_cost" not in _tab_calls)
check_true("...nor imports it",
           not any(isinstance(n, ast.ImportFrom)
                   and any(a.name == "get_model_cost" for a in n.names)
                   for n in ast.walk(_tab_tree)))
check_true("...and it does call the shared arithmetic",
           "price_model_groups" in _tab_calls
           and "model_groups_from_frame" in _tab_calls)

_matching_model_groupbys = [
    node for node in ast.walk(_tab_tree)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute) and node.func.attr == "groupby"
    and any(isinstance(a, ast.Constant) and a.value == "matching_model"
            for a in node.args)]
check("no groupby on matching_model survives in the tab -- the aggregate comes "
      "from the query layer now", len(_matching_model_groupbys), 0)

# The negative control for that scan: it must be able to see one.
_control_tree = ast.parse(
    "df.groupby('matching_model', dropna=False)[['a']].sum()")
check("...and the same scan DOES find a planted one (negative control)",
      len([n for n in ast.walk(_control_tree)
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
           and n.func.attr == "groupby"
           and any(isinstance(a, ast.Constant) and a.value == "matching_model"
                   for a in n.args)]), 1)

# IDENTITY, not just name. Importing the tab pulls in streamlit and plotly,
# which is what File 47 section 6 already does for these modules.
try:
    from oncotriage.dashboard.tabs import cost_tokens as _tab_module
except Exception as _exc:                                       # noqa: BLE001
    _tab_module = None
    print(f"  [import] the cost tab could not be imported: "
          f"{type(_exc).__name__}: {_exc}")
check_true("the tab module imports", _tab_module is not None)
if _tab_module is not None:
    check_true("...and the function it reaches IS this module's, by identity",
               _tab_module.queries.price_model_groups is queries.price_model_groups
               and _tab_module.queries.model_groups_from_frame
               is queries.model_groups_from_frame)

    # THE TAB ITSELF RENDERS. Identity proves it reaches the shared code;
    # this proves the render path around it still works with the reshaped
    # frame -- <NA> token counts, a model_recorded flag, and a NULL-model
    # group all at once. Streamlit runs its widget calls in "bare mode"
    # outside a script context; every call in this tab is a no-op there, which
    # is exactly what makes the traversal checkable without a browser.
    import logging as _logging
    from oncotriage.dashboard.tiers import enrich_match_tiers

    _st_logger = _logging.getLogger("streamlit")
    _st_level = _st_logger.level
    _st_logger.setLevel(_logging.CRITICAL)
    try:
        _tab_frame = _full_frame.copy()
        _tab_frame["timestamp"] = pd.to_datetime(_tab_frame["timestamp"])
        _tab_frame = enrich_match_tiers(
            _tab_frame, pd.read_sql_query("SELECT * FROM trial_matches", _conn))
        check_true("the frame handed to the tab carries all three group shapes "
                   "(non-degeneracy: a single priced model would exercise none "
                   "of what this item changed)",
                   len(_tab_frame["matching_model"].dropna().unique()) == 2
                   and _tab_frame["matching_model"].isna().any())
        with quiet():
            _rendered = True
            try:
                _tab_module.render_cost_tokens_tab(_tab_frame)
            except Exception as _render_exc:             # noqa: BLE001
                _rendered = _render_exc
        check("the cost tab renders end to end over the seeded frame",
              _rendered if _rendered is True
              else f"{type(_rendered).__name__}: {_rendered}", True)

        # NEGATIVE CONTROL for the render: drop a column the SHARED AGGREGATE
        # needs and the tab must fail loudly rather than draw a partial chart.
        # llm_classifier_reasoning_tokens is the right column to drop -- the tab itself
        # never reads it, so only model_groups_from_frame can notice, which is
        # what makes this a control on the consolidation rather than on the
        # tab's own indexing (dropping estimated_cost_usd raises KeyError at
        # the metrics row above, long before the costing block).
        with quiet():
            _control = check_raises(
                "  (a frame missing llm_classifier_reasoning_tokens)", ValueError,
                _tab_module.render_cost_tokens_tab,
                _tab_frame.drop(columns=["llm_classifier_reasoning_tokens"]))
        check_true("...and a frame missing a column only the shared aggregate "
                   "reads makes it raise rather than render a partial breakdown",
                   isinstance(_control, ValueError))
    finally:
        _st_logger.setLevel(_st_level)


# ===========================================================================
# SECTION 7 -- THE CUSTOM RENDERERS AGAINST AN EMPTY DATABASE
# ===========================================================================

print()
print("=" * 78)
print("SECTION 7 -- the custom renderers on an empty table")
print("=" * 78)

_empty_conn = sqlite3.connect(_EMPTY_DB_PATH)
check("the control database really is empty (non-degeneracy)",
      _empty_conn.execute("SELECT COUNT(*) FROM inferences").fetchone()[0], 0)

_empty_lines = []
check_does_not_raise(
    "report() completes against an EMPTY database, which it never used to",
    queries.report, _empty_conn, out=_empty_lines.append)
check_true("...and says the prompt is unavailable rather than printing a "
           "half-formed one",
           queries.PROMPT_UNAVAILABLE_MESSAGE
           in "\n".join(str(l) for l in _empty_lines))

check_does_not_raise("cost_by_model on an empty table returns an empty frame "
                     "instead of raising", queries.cost_by_model, _empty_conn)

# NEGATIVE CONTROL: the pre-fix renderer, exec'd from its committed source.
_pre_prompt_src = _pre_fix_function("print_slowest_prompt")
check_true("the pre-fix print_slowest_prompt was recovered from git",
           _pre_prompt_src is not None)
if _pre_prompt_src:
    _ns2 = {"pd": pd, "run": lambda conn, key: queries.run(conn, key)}
    exec(compile(_pre_prompt_src, "<pre-fix print_slowest_prompt>", "exec"), _ns2)
    check_raises("...and it raises IndexError on the empty table, which is why "
                 "report() could never have completed against one",
                 IndexError, _ns2["print_slowest_prompt"], _empty_conn,
                 out=lambda *a: None)


# ===========================================================================
# SECTION 8 -- NEITHER DOCSTRING STILL CLAIMS THE QUERIES ARE BROKEN
# ===========================================================================

print()
print("=" * 78)
print("SECTION 8 -- the docstrings describe the code")
print("=" * 78)

_STALE_CLAIMS = ("STILL BROKEN", "still dies at", "ARE STILL BROKEN")

_queries_doc = ast.get_docstring(ast.parse(
    open(os.path.join(_CODE_DIR, _QUERIES_REL), encoding="utf-8").read())) or ""
_file16_doc = ast.get_docstring(ast.parse(
    open(os.path.join(_CODE_DIR, "16- Database Query.py"),
         encoding="utf-8").read())) or ""

check_true("the queries module docstring is non-degenerate", len(_queries_doc) > 500)
check_true("File 16's docstring is non-degenerate", len(_file16_doc) > 500)

check("the queries module no longer claims its queries are broken on purpose",
      [c for c in _STALE_CLAIMS if c in _queries_doc], [])
check("File 16 no longer claims it either",
      [c for c in _STALE_CLAIMS if c in _file16_doc], [])

# NEGATIVE CONTROL: the scan has to be able to find the claim, or "[] found" is
# indistinguishable from a scan that looks at the wrong text.
if _PRE_FIX_SRC:
    _pre_doc = ast.get_docstring(ast.parse(_PRE_FIX_SRC)) or ""
    check_true("...and the same scan DOES find the claim in the pre-fix "
               "docstring (negative control)",
               any(c in _pre_doc for c in _STALE_CLAIMS))

check_true("the queries module says instead that report() completes",
           "runs to the end" in _queries_doc.lower()
           or "completes" in _queries_doc.lower())


# ===========================================================================
# SECTION 8b -- CAMPAIGNS: THE FRAGMENTS OF ONE RUN, STITCHED
# ===========================================================================
#
# ITS OWN SCRATCH DATABASE, AND THAT IS A DECISION RATHER THAN CONVENIENCE. The
# seed above is depended on by roughly forty checks that count its runs, sum its
# patients and pin its ordering; adding six campaign runs to it would move every
# one of those numbers, and a section that has to renumber its neighbours to
# exist is a section that will be got wrong. What the main seed DOES contribute
# is the ordinary case: its four runs carry no `resumed` value at all, so
# `campaign_summary` must report four campaigns of one -- which section 2 has
# already established is non-empty, and check 8b-a states explicitly.
#
# THE SHAPES, and each one is a different arm of the stitch rule:
#
#   CHAIN-1 KILLED   ─┐
#   CHAIN-2 KILLED  resumed=1, same fingerprint  ─┐  one campaign of three
#   CHAIN-3 FINISHED resumed=1, same fingerprint  ─┘  (transitive)
#   SOLO    FINISHED resumed=0                       a campaign of one
#   FPCRASH KILLED   fingerprint A                   its own campaign
#   FPRESUME FINISHED resumed=1, fingerprint B       MUST NOT STITCH
#   LEGACY  KILLED   resumed NULL, no fingerprint    its own campaign
#   MODECRASH  KILLED   fingerprint A, arm grouped      its own campaign
#   MODERESUME FINISHED resumed=1, fingerprint A,      MUST NOT STITCH
#                       arm per_trial
#
# THE LAST PAIR IS THE ONE THE CALL-MODE PASS ADDED, and it is a sharper case
# than FPCRASH/FPRESUME rather than a copy of it. Those two differ in EVERY
# fingerprint column, so any half of the predicate would separate them; this
# pair is IDENTICAL on all seven of the others and differs in the ARM ALONE. A
# stitch predicate that did not include the new column would merge them into one
# campaign and SUM a grouped fragment's cost and patients with a per-trial
# fragment's -- two incommensurable arms presented as one number, which is
# exactly what a campaign total exists to make impossible.
#
# The last one is the shape every `runs` row written before those columns
# existed has, and it is here because null-safe equality (`IS`) makes two
# all-NULL fingerprints compare EQUAL -- so without the "a stamp was recorded"
# guard, two unrelated legacy runs would stitch into one campaign on the
# strength of both being unknown.

print()
print("=" * 78)
print("SECTION 8b -- campaign_summary")
print("=" * 78)

_CAMPAIGN_DB = os.path.join(_TMP_DIR, "campaigns.db")
with quiet():
    initialize_database(_CAMPAIGN_DB)
_camp_conn = sqlite3.connect(_CAMPAIGN_DB)
_camp_cur = _camp_conn.cursor()

# (label, status, resumed, fingerprint key, arm, started_at, finished_at)
_GROUPED = MATCHING_CALL_MODE_GROUPED
_PER_TRIAL = MATCHING_CALL_MODE_PER_TRIAL
_CAMPAIGN_RUNS = [
    ("CHAIN-1",  "KILLED",   0,    "A", _GROUPED,   "2026-08-01T10:00:00", "2026-08-01T11:00:00"),
    ("CHAIN-2",  "KILLED",   1,    "A", _GROUPED,   "2026-08-01T12:00:00", "2026-08-01T13:00:00"),
    ("CHAIN-3",  "FINISHED", 1,    "A", _GROUPED,   "2026-08-01T14:00:00", "2026-08-01T15:00:00"),
    ("SOLO",     "FINISHED", 0,    "A", _GROUPED,   "2026-08-02T10:00:00", "2026-08-02T11:00:00"),
    ("FPCRASH",  "KILLED",   0,    "A", _GROUPED,   "2026-08-03T10:00:00", "2026-08-03T11:00:00"),
    ("FPRESUME", "FINISHED", 1,    "B", _GROUPED,   "2026-08-03T12:00:00", "2026-08-03T13:00:00"),
    ("LEGACY",   "KILLED",   None, None, None,      "2026-07-01T10:00:00", "2026-07-01T11:00:00"),
    # FINGERPRINT KEY "C" AND NOT "A", DELIBERATELY. The pair has to differ from
    # each other in the ARM ALONE -- which it does, both being "C" -- and it
    # must not become a candidate PARENT for anything else in this seed. With
    # "A" it did: MODECRASH is a KILLED grouped run with fingerprint A, so it
    # became the nearest preceding qualifying run for the open-span probe below
    # and quietly took that probe's campaign away from FPCRASH. Measured, not
    # anticipated -- two 8b-i checks failed for a reason that had nothing to do
    # with what they assert.
    ("MODECRASH",  "KILLED",   0, "C", _GROUPED,   "2026-08-05T10:00:00", "2026-08-05T11:00:00"),
    ("MODERESUME", "FINISHED", 1, "C", _PER_TRIAL, "2026-08-05T12:00:00", "2026-08-05T13:00:00"),
    # ── THE RESAMPLE-BEARING PAIR (the pre-migration pass) ─────────────────
    #
    # FINGERPRINT KEY "D", ITS OWN AND SHARED WITH NOTHING, and appended LAST
    # so these two ids are the highest in the seed: no existing fragment can
    # take either as a parent, and neither can take an existing one, so this
    # shape is added without moving a single expectation above it. MODECRASH's
    # note records what happens when that care is not taken.
    ("RESAMP-1", "KILLED",   0, "D", _PER_TRIAL, "2026-08-06T10:00:00", "2026-08-06T11:00:00"),
    ("RESAMP-2", "FINISHED", 1, "D", _PER_TRIAL, "2026-08-06T12:00:00", "2026-08-06T13:00:00"),
]
# Which patients each fragment wrote. THE POINT OF THE WHOLE QUERY is that these
# sum across a campaign, so the three CHAIN fragments deliberately carry
# different counts -- a query that reported any one fragment's number, or a
# constant, could not pass.
_CAMPAIGN_PATIENTS = {
    "CHAIN-1": [("C1-a", 0.10), ("C1-b", 0.10), ("C1-c", 0.10)],
    "CHAIN-2": [("C2-a", 0.20), ("C2-b", 0.20)],
    "CHAIN-3": [("C3-a", None)],
    "SOLO":    [("S-a", 0.50)],
    "FPCRASH": [("F1-a", 0.10)],
    "FPRESUME": [("F2-a", 0.10)],
    # DELIBERATELY DIFFERENT COUNTS AND COSTS. If the arm pair ever stitched,
    # the merged campaign would report 3 patients and 0.90 -- numbers neither
    # fragment produced -- so the check below can fail rather than agreeing
    # with a wrong answer by coincidence.
    "MODECRASH":  [("M1-a", 0.30), ("M1-b", 0.30)],
    "MODERESUME": [("M2-a", 0.30)],
    # ── A PATIENT IS NOT A ROW, AND THIS PAIR IS WHERE THEY DIVERGE ────────
    #
    # TWO REAL MECHANISMS, ONE INSIDE A FRAGMENT AND ONE ACROSS TWO, and the
    # query has to survive both:
    #
    #   R-a appears TWICE IN RESAMP-1. That is the RESAMPLE PASS: it re-runs a
    #       seeded subset of already-processed patients (RESAMPLE_COUNT is 100)
    #       and each re-run writes ANOTHER inferences row. Every real campaign
    #       this runner produces has ~100 of these.
    #   R-c appears in RESAMP-1 AND in RESAMP-2. That is a patient whose first
    #       attempt ERRORED: an errored patient is not checkpointed, so the
    #       resume runs it again -- and the two rows are in DIFFERENT
    #       fragments, which is why a per-run DISTINCT summed across fragments
    #       is still wrong and the count has to be taken over the campaign.
    #
    # 6 rows, 4 patients, and per-run distinct summed would give 5 -- three
    # different numbers, so no arithmetic accident can make the check below
    # pass.
    "RESAMP-1": [("R-a", 0.10), ("R-b", 0.10), ("R-c", 0.10), ("R-a", 0.10)],
    "RESAMP-2": [("R-c", 0.10), ("R-d", 0.10)],
}
_CAMPAIGN_IDS = {}
for _label, _status, _resumed, _fp, _arm, _started, _finished in _CAMPAIGN_RUNS:
    if _fp is None:
        _camp_cur.execute(
            "INSERT INTO runs (started_at, finished_at, status, "
            "invocation_source, resumed) VALUES (?, ?, ?, ?, ?)",
            (_started, _finished, _status, "batch_runner", _resumed))
    else:
        _camp_cur.execute(
            "INSERT INTO runs (started_at, finished_at, status, "
            "invocation_source, resumed, fingerprint_version, "
            "llm_classifier_prompt_version, llm_classifier_renderer_digest, "
            "matching_model_configured, matching_call_mode, "
            "qdrant_collection, collection_points, "
            "data_snapshot_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_started, _finished, _status, "batch_runner", _resumed, 2,
             f"1.9.0-{_fp}", f"digest-{_fp}", "gpt-5.6-terra", _arm,
             f"trial_criteria_{_fp}", 12067, "2026-02-26"))
    _CAMPAIGN_IDS[_label] = _camp_cur.lastrowid
for _label, _rows in _CAMPAIGN_PATIENTS.items():
    for _pid, _cost in _rows:
        _camp_cur.execute(
            "INSERT INTO inferences (patient_id, timestamp, run_id, "
            "estimated_cost_usd, error, age, sex) "
            "VALUES (?, ?, ?, ?, '', 60, 'male')",
            (_pid, "2026-08-20 10:00:00", _CAMPAIGN_IDS[_label], _cost))
_camp_conn.commit()

check_true("8b-seed: the seven campaign runs were written with distinct ids "
           "(non-degeneracy: every expectation below is keyed by one)",
           len(set(_CAMPAIGN_IDS.values())) == len(_CAMPAIGN_RUNS))
check("8b-seed: the resumed column really carries 1 on the three rows the "
      "stitch rule is supposed to act on, and NULL on the legacy row -- "
      "without this the whole section is vacuous",
      [_camp_conn.execute("SELECT resumed FROM runs WHERE id = ?",
                          (_CAMPAIGN_IDS[_l],)).fetchone()[0]
       for _l in ("CHAIN-2", "CHAIN-3", "FPRESUME", "SOLO", "LEGACY")],
      [1, 1, 1, 0, None])

_campaigns = queries.run(_camp_conn, "campaign_summary")
_camp_by_id = {int(r.campaign_id): r for r in _campaigns.itertuples()}

# --- THE THREE-FRAGMENT CAMPAIGN ------------------------------------------
_ROOT = _CAMPAIGN_IDS["CHAIN-1"]
_chain = _camp_by_id.get(_ROOT)
check("8b-a: the three fragments are ONE campaign, rooted at the first",
      None if _chain is None else _safe_int(_chain.runs), 3)
check("8b-a: ...with its run ids in ascending order, which is what makes the "
      "chain readable rather than a set",
      None if _chain is None else _chain.run_ids,
      " -> ".join(str(_CAMPAIGN_IDS[_l])
                  for _l in ("CHAIN-1", "CHAIN-2", "CHAIN-3")))
check("8b-a: ...and the statuses in the same order",
      None if _chain is None else _chain.statuses,
      "KILLED -> KILLED -> FINISHED")
check("8b-a: ...and the campaign is flagged as stitched",
      None if _chain is None else _safe_int(_chain.stitched), 1)
check("8b-a: ...and as mixed-status, because its fragments did not all end "
      "the same way",
      None if _chain is None else _safe_int(_chain.mixed_status), 1)

_EXPECTED_CHAIN_PATIENTS = sum(
    len(_CAMPAIGN_PATIENTS[_l]) for _l in ("CHAIN-1", "CHAIN-2", "CHAIN-3"))
check("8b-b: total_patients is SUMMED across the fragments -- this is the "
      "number run_summary cannot give, because each of its rows carries only "
      "the patients ITS process wrote",
      None if _chain is None else _safe_int(_chain.total_patients),
      _EXPECTED_CHAIN_PATIENTS)
check_true("8b-b: ...and the fragments carry different counts, so a query "
           "reporting any ONE of them, or a constant, could not pass",
           len({len(_CAMPAIGN_PATIENTS[_l])
                for _l in ("CHAIN-1", "CHAIN-2", "CHAIN-3")}) == 3)
check("8b-b: ...and the largest single fragment is smaller than the total, "
      "which is what says the sum happened",
      None if _chain is None
      else _safe_int(_chain.total_patients) > max(
          len(_CAMPAIGN_PATIENTS[_l])
          for _l in ("CHAIN-1", "CHAIN-2", "CHAIN-3")), True)

check("8b-c: the wall span runs from the FIRST fragment's start to the LAST "
      "fragment's finish",
      None if _chain is None else (_chain.first_started_at,
                                   _chain.last_finished_at),
      ("2026-08-01T10:00:00", "2026-08-01T15:00:00"))
check("8b-c: ...and no fragment is open, so the span is closed",
      None if _chain is None else _safe_int(_chain.unfinalized_runs), 0)
check("8b-c: ...and the cost is summed with the unpriced row COUNTED rather "
      "than silently contributing a zero nobody can see",
      None if _chain is None else (round(_safe_float(_chain.total_cost_usd), 4),
                                   _safe_int(_chain.rows_with_no_cost)),
      (0.7, 1))

# --- THE FINGERPRINT BREAK ------------------------------------------------
#
# THE CHECK THE WHOLE QUERY EXISTS FOR. A resumed run whose configuration
# differs from the crashed run before it must NOT be added to that campaign's
# total, because "which configuration produced this number" is the question a
# campaign total is asked.
check("8b-d: a resume whose fingerprint differs does NOT stitch -- the "
      "crashed run before it stays a campaign of one",
      _safe_int(_camp_by_id[_CAMPAIGN_IDS["FPCRASH"]].runs), 1)
check("8b-d: ...and the resume is its own campaign, reported as a fragment "
      "rather than silently added to a total it does not belong in",
      (_safe_int(_camp_by_id[_CAMPAIGN_IDS["FPRESUME"]].runs),
       _safe_int(_camp_by_id[_CAMPAIGN_IDS["FPRESUME"]].stitched)), (1, 0))
check("8b-d: ...and their patients are NOT summed together",
      (_safe_int(_camp_by_id[_CAMPAIGN_IDS["FPCRASH"]].total_patients),
       _safe_int(_camp_by_id[_CAMPAIGN_IDS["FPRESUME"]].total_patients)),
      (1, 1))
check_true("8b-d: ...and the two really do differ only in the fingerprint -- "
           "same resumed flag, same adjacency, same statuses as the chain "
           "above, so nothing but the fingerprint can explain the difference",
           _camp_conn.execute(
               "SELECT COUNT(*) FROM runs a, runs b WHERE a.id = ? AND b.id = ? "
               "AND b.resumed = 1 AND a.status = 'KILLED' AND b.id = a.id + 1 "
               "AND a.llm_classifier_renderer_digest != "
               "b.llm_classifier_renderer_digest",
               (_CAMPAIGN_IDS["FPCRASH"], _CAMPAIGN_IDS["FPRESUME"])
           ).fetchone()[0] == 1)

# --- THE UNSTITCHED SHAPES ------------------------------------------------
check("8b-e: a run that was never resumed onto is a campaign of one, not "
      "stitched, not mixed",
      (_safe_int(_camp_by_id[_CAMPAIGN_IDS["SOLO"]].runs),
       _safe_int(_camp_by_id[_CAMPAIGN_IDS["SOLO"]].stitched),
       _safe_int(_camp_by_id[_CAMPAIGN_IDS["SOLO"]].mixed_status)),
      (1, 0, 0))
check("8b-f: a LEGACY run -- resumed NULL and no fingerprint at all -- is its "
      "own campaign. Null-safe equality makes two unrecorded fingerprints "
      "compare EQUAL, so without the 'a stamp was recorded' guard two "
      "unrelated legacy runs would stitch on the strength of both being "
      "unknown",
      _safe_int(_camp_by_id[_CAMPAIGN_IDS["LEGACY"]].runs), 1)

check("8b-g: every run appears in exactly one campaign and none is lost -- "
      "the query is driven from `runs`, so the members must partition it",
      sum(_safe_int(r.runs) for r in _campaigns.itertuples()),
      len(_CAMPAIGN_RUNS))
check("8b-g: ...so there are eight campaigns over eleven runs -- the three "
      "CHAIN fragments are one, the resample-bearing pair is one, and every "
      "other run is its own",
      len(_campaigns), 8)


# --- A PATIENT IS NOT A ROW ------------------------------------------------
#
# THE DEFECT, NAMED: `total_patients` was `SUM(COUNT(*))` over the fragments'
# inference rows, under a docstring calling it "the campaign's real cohort
# size". It is not, and it is wrong on EVERY real campaign this runner
# produces: the resample pass writes a second row for each of RESAMPLE_COUNT
# (100) already-processed patients, so a 1,000-patient campaign reported 1,100
# -- and a reviewer dividing a cost or a rate by it used a denominator 10% too
# large, silently.
#
# THREE DIFFERENT NUMBERS ARE AVAILABLE HERE ON PURPOSE, so no arithmetic
# accident can pass: 6 rows, 4 distinct patients across the campaign, and 5 if
# the DISTINCT were taken per fragment and then summed (which is the plausible
# wrong fix -- it survives the resample overlap inside a fragment and not the
# retry overlap between two).
_RES_ROOT = _CAMPAIGN_IDS["RESAMP-1"]
_res = _camp_by_id.get(_RES_ROOT)
_RES_ROWS = sum(len(_CAMPAIGN_PATIENTS[_l]) for _l in ("RESAMP-1", "RESAMP-2"))
_RES_PATIENTS = len({_pid for _l in ("RESAMP-1", "RESAMP-2")
                     for _pid, _ in _CAMPAIGN_PATIENTS[_l]})
_RES_PER_RUN_DISTINCT = sum(
    len({_pid for _pid, _ in _CAMPAIGN_PATIENTS[_l]})
    for _l in ("RESAMP-1", "RESAMP-2"))
check_true("8b-l: the seed really carries all three numbers apart -- rows, "
           "distinct-across-the-campaign, and distinct-summed-per-fragment "
           "(non-degeneracy: on a seed where they coincided every check below "
           "would pass against any of the three implementations)",
           len({_RES_ROWS, _RES_PATIENTS, _RES_PER_RUN_DISTINCT}) == 3)
check("8b-l: the pair really is ONE campaign, so the counts below are about a "
      "stitched chain rather than two independent rows",
      None if _res is None else (_safe_int(_res.runs),
                                 _safe_int(_res.stitched)), (2, 1))
check("8b-l: *** total_patients is DISTINCT PATIENTS ACROSS THE CAMPAIGN. *** "
      "Not rows, and not per-fragment distinct summed: R-a has two rows in one "
      "fragment (the resample pass) and R-c has one row in EACH fragment (an "
      "errored patient the resume re-ran)",
      None if _res is None else _safe_int(_res.total_patients),
      _RES_PATIENTS)
check("8b-l: ...and inference_rows carries what it used to be called "
      "total_patients, because every cost and count in this row is summed "
      "over the ROWS and a reader needs the denominator that matches",
      None if _res is None else _safe_int(_res.inference_rows), _RES_ROWS)
check("8b-l: ...so the two columns DIFFER on this campaign, which is what "
      "says the split is real rather than two names for one query",
      None if _res is None
      else _safe_int(_res.inference_rows) > _safe_int(_res.total_patients),
      True)
check("8b-l: ...and the cost is still summed over the ROWS -- a re-run patient "
      "was billed twice and the campaign really did pay twice",
      None if _res is None else round(float(_res.total_cost_usd), 4),
      round(sum(c for _l in ("RESAMP-1", "RESAMP-2")
                for _, c in _CAMPAIGN_PATIENTS[_l]), 4))
check("8b-l: a campaign with NO repeats reports the two columns EQUAL, so the "
      "split costs nothing on an ordinary chain and 8b-l above is a "
      "measurement rather than a constant offset",
      None if _chain is None else (_safe_int(_chain.total_patients),
                                   _safe_int(_chain.inference_rows)),
      (_EXPECTED_CHAIN_PATIENTS, _EXPECTED_CHAIN_PATIENTS))

# --- THE ARM PAIR: IDENTICAL IN EVERY OTHER GATED COLUMN ------------------
#
# THE SHARPEST CASE IN THIS SECTION. FPCRASH/FPRESUME differ in every
# fingerprint column, so any fragment of the predicate separates them. These two
# differ in the ARM ALONE, so the ONLY thing that can keep them apart is the
# call-mode column being in the stitch. Without it they merge, and the merged
# campaign reports 3 patients and 0.90 -- a cost and a cohort neither arm
# produced, summed across two arms that are not commensurable.
def _run_col(run_id, column):
    """One `runs` column of one row, as a scalar. Local because this file has no
    sql_one helper and a section-local reader is clearer than a fourth
    frame-shaped one."""
    row = _camp_conn.execute(
        f"SELECT {column} FROM runs WHERE id = ?", (run_id,)).fetchone()
    return None if row is None else row[0]

_MODE_CRASH = _camp_by_id.get(_CAMPAIGN_IDS["MODECRASH"])
_MODE_RESUME = _camp_by_id.get(_CAMPAIGN_IDS["MODERESUME"])
check("8b-g-arm: the seed really differs in the arm ALONE (non-degeneracy: if "
      "any other fingerprint column differed, this pair would prove nothing "
      "the FPCRASH/FPRESUME pair does not already prove)",
      [c for c in dblog.RUN_FINGERPRINT_COLUMNS
       if _run_col(_CAMPAIGN_IDS["MODECRASH"], c)
       != _run_col(_CAMPAIGN_IDS["MODERESUME"], c)],
      ["matching_call_mode"])
check("8b-g-arm: ...and the resumed run really declares itself a resume, so "
      "the stitch rule's first half is satisfied and only the arm is stopping "
      "it",
      _run_col(_CAMPAIGN_IDS["MODERESUME"], "resumed"), 1)
check("8b-g-arm: a KILLED grouped run and a resumed PER-TRIAL run DO NOT "
      "STITCH -- two campaigns, not one",
      (None if _MODE_CRASH is None else _safe_int(_MODE_CRASH.runs),
       None if _MODE_RESUME is None else _safe_int(_MODE_RESUME.runs)),
      (1, 1))
check("8b-g-arm: ...so their patients are NOT summed, which is the harm",
      (None if _MODE_CRASH is None else _safe_int(_MODE_CRASH.total_patients),
       None if _MODE_RESUME is None else _safe_int(_MODE_RESUME.total_patients)),
      (2, 1))
check("8b-g-arm: ...and neither is their cost",
      (None if _MODE_CRASH is None else round(float(_MODE_CRASH.total_cost_usd), 2),
       None if _MODE_RESUME is None else round(float(_MODE_RESUME.total_cost_usd), 2)),
      (0.60, 0.30))
check("8b-g-arm: ...and each campaign reports its OWN arm, so a reviewer can "
      "attribute either total without opening a second query",
      (None if _MODE_CRASH is None else _MODE_CRASH.matching_call_mode,
       None if _MODE_RESUME is None else _MODE_RESUME.matching_call_mode),
      (_GROUPED, _PER_TRIAL))
check("8b-g-arm: ...(non-degeneracy: the two arms are distinct strings, so the "
      "line above is not one value compared with itself)",
      _GROUPED != _PER_TRIAL and len(set(MATCHING_CALL_MODES)) == 2, True)
check("8b-g-arm: ...while the three CHAIN fragments, which agree on the arm, "
      "still stitch -- the predicate was tightened, not broken",
      None if _chain is None else _safe_int(_chain.runs), 3)
check("8b-g: ...newest campaign first, which is what ORDER BY campaign_id "
      "DESC means and is the same ordering run_summary uses",
      list(_campaigns["campaign_id"]),
      sorted(_camp_by_id, reverse=True))

check("8b-h: the campaign carries the ROOT fragment's configuration, which "
      "every member matched transitively -- so a reviewer can attribute the "
      "total without opening a second query",
      None if _chain is None else (_chain.llm_classifier_prompt_version,
                                   _chain.qdrant_collection),
      ("1.9.0-A", "trial_criteria_A"))

# --- A CAMPAIGN THAT IS STILL OPEN ----------------------------------------
#
# `last_finished_at` is MAX(finished_at), which IGNORES NULLs -- so a campaign
# whose final fragment has not finished would otherwise report the PREVIOUS
# fragment's finish time as the end of the campaign, with nothing saying the
# span is open. `unfinalized_runs` is what says it.
# ITS ARM MATCHES FPCRASH'S DELIBERATELY. This probe exists to test the OPEN-SPAN
# reporting, not the stitch predicate, so every fingerprint column has to agree
# with the fragment it is meant to join -- and that now includes the arm.
# MEASURED RATHER THAN ANTICIPATED: the first version of this insert kept the
# pre-call-mode column list, so the probe carried a NULL arm against FPCRASH's
# 'grouped', did not stitch, and three checks below failed for a reason that had
# nothing to do with what they assert. That is the new column being sharp, and
# it is why an arm is written here rather than left to default.
_camp_cur.execute(
    "INSERT INTO runs (started_at, finished_at, status, invocation_source, "
    "resumed, fingerprint_version, llm_classifier_prompt_version, "
    "llm_classifier_renderer_digest, matching_model_configured, "
    "matching_call_mode, qdrant_collection, collection_points, "
    "data_snapshot_date) "
    "VALUES (?, NULL, 'RUNNING', 'batch_runner', 1, 2, '1.9.0-A', "
    "'digest-A', 'gpt-5.6-terra', ?, 'trial_criteria_A', 12067, '2026-02-26')",
    ("2026-08-04T10:00:00", _GROUPED))
_OPEN_RUN = _camp_cur.lastrowid
_camp_conn.commit()
_open_campaigns = queries.run(_camp_conn, "campaign_summary")
_open_by_id = {int(r.campaign_id): r for r in _open_campaigns.itertuples()}
_open_chain = _open_by_id.get(_CAMPAIGN_IDS["FPCRASH"])
check("8b-i: a RUNNING fragment with no finished_at joins the nearest "
      "preceding crashed run of the same configuration",
      None if _open_chain is None else _safe_int(_open_chain.runs), 2)
check("8b-i: ...and the campaign reports its open fragment rather than "
      "presenting the earlier fragment's finish as the end of the campaign",
      None if _open_chain is None else _safe_int(_open_chain.unfinalized_runs),
      1)
check("8b-i: ...with last_finished_at still the newest finish that EXISTS, "
      "never extrapolated to now",
      None if _open_chain is None else _open_chain.last_finished_at,
      "2026-08-03T11:00:00")
_camp_cur.execute("DELETE FROM runs WHERE id = ?", (_OPEN_RUN,))
_camp_conn.commit()
check("8b-i: ...and the probe row is removed",
      len(queries.run(_camp_conn, "campaign_summary")), 8)

# --- THE STITCH PREDICATE IS GENERATED, NOT RETYPED -----------------------
check("8b-j: the fingerprint match is built from the writer's own column "
      "tuple, so a newly gated field tightens the stitch by itself rather "
      "than leaving one axis along which two configurations merge silently",
      sorted(c for c in dblog.RUN_FINGERPRINT_COLUMNS
             if f"prev.{c} IS r.{c}" not in queries._CAMPAIGN_EDGE_SQL), [])
check("8b-j: ...and every one of them is really in the registered SQL "
      "(non-degeneracy: an empty tuple would satisfy the line above)",
      len(dblog.RUN_FINGERPRINT_COLUMNS) >= 7, True)
check("8b-j: ...and the stamp column the guard tests for NOT NULL is one of "
      "them", queries.CAMPAIGN_STAMP_COLUMN in dblog.RUN_FINGERPRINT_COLUMNS,
      True)
check("8b-j: ...and the resumable statuses are a PROPER subset of the "
      "terminal ones -- FINISHED must not be resumable, or a re-run of a "
      "completed cohort would be glued onto it",
      (set(queries.CAMPAIGN_RESUMABLE_STATUSES)
       < set(dblog.RUN_RECORD_TERMINAL_STATUSES),
       "FINISHED" in queries.CAMPAIGN_RESUMABLE_STATUSES), (True, False))

# --- THE MAIN SEED, WHICH HAS NO RESUMES ----------------------------------
check("8b-k: on the main seed -- four runs, none of them a resume -- every "
      "campaign is a campaign of one, so the ordinary database is unaffected "
      "by any of this",
      sorted(_safe_int(r.runs)
             for r in _frame_or_raise("campaign_summary").itertuples()),
      [1] * len(_RUN_ROWS))

_camp_conn.close()


# ===========================================================================
# SECTION 8c -- call_mode_comparison
# ===========================================================================
#
# THE ONE QUERY A THREE-ARM CAMPAIGN ACTUALLY READS. `config.matching_call_mode()`
# decides whether Stage 5 sends one request carrying several trials or one per
# trial, which is the single largest lever on what a patient costs -- and until
# era 4 no registered query named either the per-row column or the per-run one,
# so no number in this database could be attributed to an arm.
#
# IT RUNS AGAINST THE MAIN SEED, which was extended to carry both arms rather
# than given a scratch database of its own: the arms have to sit beside real
# costs, real trial_matches children and a real dangling row, and section 8b's
# reason for a separate database (it adds RUNS, which move forty neighbouring
# expectations) does not apply to a query that adds none.

print()
print("=" * 78)
print("SECTION 8c -- call_mode_comparison")
print("=" * 78)

_modes = _frame_or_raise("call_mode_comparison")
_mode_rows = {(str(r.run_id), str(r.row_mode)): r for r in _modes.itertuples()}

check("8c-a: the comparison is non-empty on the seed, which is the registry's "
      "own contract for every query in it", len(_modes) > 0, True)

# --- BOTH ARMS ARE PRESENT AND THE SEED CAN TELL THEM APART ----------------
check("8c-b: both arms of the vocabulary appear as their own rows "
      "(non-degeneracy: a seed carrying one arm cannot show that the GROUP BY "
      "separates them)",
      sorted({str(r.row_mode) for r in _modes.itertuples()}
             & set(MATCHING_CALL_MODES)),
      sorted(MATCHING_CALL_MODES))
check("8c-c: ...and a row whose arm was never recorded is its own bucket, "
      "labelled, rather than being counted as grouped",
      "(not recorded)" in {str(r.row_mode) for r in _modes.itertuples()}, True)

# --- THE THREE NUMBERS, AGAINST THE SEED RATHER THAN THE FRAME -------------
#
# EXPECTATIONS ARE WRITTEN FROM THE SEED, never read back out of the query under
# test. RUN-CLEAN's per_trial arm is P-CONSISTENT-B (0.095) and P-BYPASSED
# (0.055) -- the second is per_trial because a bypassed packer IS per-trial
# mode, and putting it on the grouped arm to keep this number at one patient
# would be a seed that lies about the arm to protect an expectation.
_CLEAN = str(_RUN_IDS["RUN-CLEAN"])
_CLEAN_PER_TRIAL = ["P-CONSISTENT-B", "P-BYPASSED"]
check("8c-d-pre: the seed really puts exactly those two patients on "
      "RUN-CLEAN's per-trial arm (non-degeneracy: the two numbers below are "
      "written from this list, so a list that had drifted would make them "
      "agree with the wrong seed)",
      sorted(_r for _r, _v in _SEED_ROWS
             if _r in _RUN_MEMBERSHIP["RUN-CLEAN"]
             and _v.get("matching_call_mode") == MATCHING_CALL_MODE_PER_TRIAL),
      sorted(_CLEAN_PER_TRIAL))
_per_trial_row = _mode_rows.get((_CLEAN, MATCHING_CALL_MODE_PER_TRIAL))
check("8c-d: the per-trial arm of RUN-CLEAN is exactly the patients seeded "
      "into it", None if _per_trial_row is None else _safe_int(_per_trial_row.patients),
      len(_CLEAN_PER_TRIAL))
check("8c-e: ...with those patients' stored costs summed, not the run's total",
      None if _per_trial_row is None else round(float(_per_trial_row.cost_usd), 3),
      round(0.095 + 0.055, 3))

_grouped_row = _mode_rows.get((_CLEAN, MATCHING_CALL_MODE_GROUPED))
check("8c-f: ...and the grouped arm of the SAME run is a SEPARATE row, so one "
      "run's two arms are never averaged into one number",
      None if _grouped_row is None else _safe_int(_grouped_row.patients), 1)
check("8c-g: ...(non-degeneracy: the two arms of that one run really carry "
      "different costs, so the split above is visible rather than incidental)",
      (None if _grouped_row is None else round(float(_grouped_row.cost_usd), 3))
      != (None if _per_trial_row is None else round(float(_per_trial_row.cost_usd), 3)),
      True)

# --- THE STAMP-VERSUS-ROWS READING ----------------------------------------
_agreements = {str(r.mode_agreement) for r in _modes.itertuples()}
check("8c-h: RUN-CLEAN is stamped `grouped` and holds a `per_trial` row, and "
      "the query SAYS SO rather than reporting the stamp twice",
      None if _per_trial_row is None else str(_per_trial_row.mode_agreement),
      "STAMP DISAGREES WITH ROWS")
check("8c-i: ...while the row that matches its run's stamp reads so",
      None if _grouped_row is None else str(_grouped_row.mode_agreement),
      "stamp matches rows")
check("8c-j: ...and the dangling row is named as a MISSING RUN ROW rather than "
      "as a missing column, which is a different fix",
      "run row is missing" in _agreements, True)
check("8c-k: ...(non-degeneracy: the agreement column really takes several "
      "values on this seed, so the three checks above are not all reading one "
      "constant)", len(_agreements) >= 3, True)

# --- OMISSIONS ------------------------------------------------------------
#
# `omitted_trials` COUNTS ROWS FOUND, so its zero is only a measurement where
# trials_recorded > 0. Both columns are checked together for that reason.
_omission_reason_rows = _conn.execute(
    "SELECT COUNT(*) FROM trial_matches WHERE not_evaluable_reason = ?",
    (queries.CALL_MODE_OMISSION_REASON,)).fetchone()[0]
check("8c-l-pre: the seed really carries an omission (non-degeneracy: with "
      "none, every omission check below compares zero with zero)",
      _omission_reason_rows > 0, True)
check("8c-l: the omission total over every arm equals the number of "
      "trial_matches rows carrying that reason -- nothing double-counted by "
      "the join, nothing lost",
      sum(_safe_int(r.omitted_trials) for r in _modes.itertuples()),
      _omission_reason_rows)
# THE EXPECTATION IS "EVERY ATTACHED ROW", NOT "EVERY ROW", AND THE DIFFERENCE
# IS A FINDING RATHER THAN AN ADJUSTMENT. This query reaches trial rows through
# `inferences`, as every query in this registry that reads a verdict does, so
# the seeded ORPHAN -- a trial_matches row whose inference_id names nothing --
# is invisible to it. That is exactly the harm `orphan_trial_matches` was
# registered to report: a stored, billed verdict that no number computed over
# the campaign includes. Comparing against the raw COUNT(*) would make this
# check fail for a reason that has nothing to do with what it asserts, and
# "adjusting" it without saying so would hide the property.
_attached_trial_rows = _conn.execute(
    "SELECT COUNT(*) FROM trial_matches tm "
    "JOIN inferences i ON i.id = tm.inference_id").fetchone()[0]
check("8c-m: ...and trials_recorded totals every ATTACHED trial_matches row, "
      "so a zero omission count on a row with recorded trials is a MEASUREMENT "
      "and one with none is not",
      sum(_safe_int(r.trials_recorded) for r in _modes.itertuples()),
      _attached_trial_rows)
check("8c-m2: ...and the orphan is the whole of the difference, which is the "
      "invisibility orphan_trial_matches exists to report (non-degeneracy: "
      "with no orphan the two counts are equal and 8c-m says nothing about it)",
      _conn.execute("SELECT COUNT(*) FROM trial_matches").fetchone()[0]
      - _attached_trial_rows,
      1)
check("8c-n: ...(non-degeneracy: the seed really contains trial_matches rows, "
      "so the two sums above are not both zero)",
      _conn.execute("SELECT COUNT(*) FROM trial_matches").fetchone()[0] > 0,
      True)

# --- THE OMISSION REASON IS THE PIPELINE'S OWN ----------------------------
#
# queries.py may not import the agent -- that is the edge pass 20c-2c removed --
# so the string is restated there and this is what stops it drifting. A test may
# import both because a test is in nobody's import graph.
from oncotriage.agent.evaluation import NOT_EVALUABLE_MODEL_OMITTED  # noqa: E402
check("8c-o: the restated omission reason is byte-identical to the constant "
      "the pipeline writes -- a drift here would make the comparison report "
      "zero omissions in every arm, forever, and look clean doing it",
      queries.CALL_MODE_OMISSION_REASON, NOT_EVALUABLE_MODEL_OMITTED)

# --- AND SO ARE THE THREE WRITER CLASSES ----------------------------------
#
# THE SAME TRADE ONE TUPLE WIDER, AND IT HAD ALREADY GONE WRONG ONCE. The
# family CASE in `not_evaluable_reasons` used to be four literals written out
# by hand; the per-trial pass added a fifth CONSTRUCTED reason
# (`per_trial_call_failed`) and the CASE was never widened, so a trial whose own
# REQUEST failed was reported under "corrected from a model verdict" -- a family
# that asserts the model answered -- and on the SHIPPED per-trial arm that is
# the constructed reason most likely to occur. The CASE is generated from these
# tuples now, and these three checks are what keep the restatement honest.
from oncotriage.agent.evaluation import (            # noqa: E402
    NOT_EVALUABLE_REASONS_CONSTRUCTED as _AGENT_CONSTRUCTED,
    NOT_EVALUABLE_REASONS_CORRECTED as _AGENT_CORRECTED,
    NOT_EVALUABLE_REASONS_DECLARED as _AGENT_DECLARED,
)
for _label, _restated, _owner in (
        ("CONSTRUCTED", queries.NOT_EVALUABLE_REASONS_CONSTRUCTED,
         _AGENT_CONSTRUCTED),
        ("CORRECTED", queries.NOT_EVALUABLE_REASONS_CORRECTED, _AGENT_CORRECTED),
        ("DECLARED", queries.NOT_EVALUABLE_REASONS_DECLARED, _AGENT_DECLARED)):
    check(f"8c-o-{_label.lower()}: the restated {_label} class equals the "
          "agent's, so the family CASE cannot report a reason under the wrong "
          "writer",
          sorted(_restated), sorted(_owner))
check("8c-o-vocab: and the union is the whole closed vocabulary, so a reason "
      "added to the agent and not to queries.py falls to the ELSE arm that "
      "NAMES itself rather than being silently called a correction",
      sorted(set(queries.NOT_EVALUABLE_REASONS_CONSTRUCTED)
             | set(queries.NOT_EVALUABLE_REASONS_CORRECTED)
             | set(queries.NOT_EVALUABLE_REASONS_DECLARED)),
      sorted(_ev_all := set(_AGENT_CONSTRUCTED) | set(_AGENT_CORRECTED)
             | set(_AGENT_DECLARED)))
check("non-degeneracy: the vocabulary compared is non-empty and plural",
      len(_ev_all) >= 11, True)
check("8c-o-sql: every member is interpolated into the rendered family CASE, "
      "which is the thing a reader's GROUP BY actually meets",
      [r for r in _ev_all
       if f"'{r}'" not in queries.QUERIES_BY_KEY['not_evaluable_reasons'].sql],
      [])

# --- AND THE CASE IS DRIVEN, NOT JUST READ --------------------------------
#
# THE INTERPOLATION CHECK ABOVE CANNOT SEE A MEMBER IN THE WRONG ARM: every
# reason appears in the SQL either way, and putting `per_trial_call_failed` in
# the CORRECTED list would satisfy it while reporting a transport failure as a
# model verdict -- which is the exact defect this replaced. So the CASE is
# EXECUTED, over a scratch in-memory table, once per member plus NULL plus a
# value from outside the vocabulary. Nothing here touches the seeded database
# beside it.
_fam = sqlite3.connect(":memory:")
_fam.execute("CREATE TABLE trial_matches (not_evaluable_reason TEXT)")
_fam.executemany("INSERT INTO trial_matches VALUES (?)",
                 [(r,) for r in sorted(_ev_all)]
                 + [(None,), ("a value from nowhere",)])
_families = dict(_fam.execute(
    f"SELECT COALESCE(tm.not_evaluable_reason, '<null>'), "
    f"{queries._NOT_EVALUABLE_FAMILY_SQL} FROM trial_matches tm").fetchall())
_fam.close()
check("8c-o-drive: every CONSTRUCTED reason is reported as constructed",
      sorted({_families.get(r) for r in _AGENT_CONSTRUCTED}),
      ["constructed by the pipeline"])
check("8c-o-drive: every CORRECTED reason is reported as corrected",
      sorted({_families.get(r) for r in _AGENT_CORRECTED}),
      ["corrected from a model verdict"])
check("8c-o-drive: every DECLARED reason is reported as declared, and NOT as "
      "a correction the pipeline never made",
      sorted({_families.get(r) for r in _AGENT_DECLARED}),
      ["declared by the model"])
check("8c-o-drive: NULL is reported as not reported, and a value from outside "
      "the vocabulary NAMES itself rather than falling into a real family",
      (_families.get("<null>"), _families.get("a value from nowhere")),
      ("(not reported)", "(not a value this pipeline writes)"))
check("8c-o-drive: non-degeneracy -- the drive really classified every row",
      len(_families), len(_ev_all) + 2)

# --- EVERY INFERENCE ROW IS ACCOUNTED FOR ---------------------------------
check("8c-o-arm: the omission is attributed to the GROUPED arm, which is the "
      "only arm that can produce one -- a per-trial request carrying one trial "
      "either answers it or fails",
      {str(r.row_mode): _safe_int(r.omitted_trials) for r in _modes.itertuples()
       if _safe_int(r.omitted_trials) > 0},
      {MATCHING_CALL_MODE_GROUPED: _omission_reason_rows})

check("8c-p: the arms partition `inferences` -- no row is dropped by the LEFT "
      "JOINs and none is counted twice",
      sum(_safe_int(r.patients) for r in _modes.itertuples()),
      _conn.execute("SELECT COUNT(*) FROM inferences").fetchone()[0])


# ===========================================================================
# SECTION 8d -- connect() IS READ-ONLY AND CREATES NOTHING
# ===========================================================================
#
# THE DEFECT IT REMOVES, and it is a reporting defect rather than a database
# one. `connect()` was a plain `sqlite3.connect(path)`, which CREATES an empty
# database when the path does not exist. So a mistyped path, a stale
# ONCOTRIAGE_INFERENCES_DB or a `--db` pointed one directory wrong did not fail:
# it brought a database into existence, `report()` ran the whole registry
# against it, and forty-odd queries printed empty frames and clean-audit
# messages that are indistinguishable from a real report on a healthy pipeline.
# The second reading is the one a person reaches for.
#
# THE PRECEDENT IS `oncotriage/dashboard/data.py:_readonly_connection`, which
# had already made the same argument for the run loaders.

print()
print("=" * 78)
print("SECTION 8d -- connect() is read-only")
print("=" * 78)

_ABSENT_DB = os.path.join(_TMP_DIR, "no_such_database.db")
check_true("the fixture path really does not exist (non-degeneracy: on an "
           "existing file every check below passes for the wrong reason)",
           not os.path.exists(_ABSENT_DB))
_ABSENT_RAISED = check_raises(
    "8d-a: connect() on an absent path RAISES rather than creating a database",
    queries.MissingDatabaseError, queries.connect, _ABSENT_DB)
check("8d-b: ...and NOTHING was created at that path, which is the half a "
      "typed exception alone does not give you",
      os.path.exists(_ABSENT_DB), False)
check_true("8d-c: ...and the message names the path, which "
           "sqlite3.OperationalError's 'unable to open database file' does not",
           _ABSENT_DB in str(_ABSENT_RAISED))
check_true("8d-d: MissingDatabaseError is NOT a sqlite3.Error, so a caller's "
           "broad `except sqlite3.Error` cannot swallow it and report an empty "
           "result -- MissingTableError's ruling, same reason",
           not issubclass(queries.MissingDatabaseError, sqlite3.Error))

_RO_CONN = queries.connect(_DB_PATH)
check_true("8d-e: connect() on a real database still READS (non-degeneracy: a "
           "connection that refused everything would satisfy 8d-f too)",
           _RO_CONN.execute("SELECT COUNT(*) FROM inferences").fetchone()[0] > 0)
_WRITE_RAISED = check_raises(
    "8d-f: ...and REFUSES to write, so the module's read-only contract is "
    "enforced by SQLite rather than promised in a docstring",
    sqlite3.OperationalError, _RO_CONN.execute,
    "INSERT INTO inferences (patient_id, timestamp) VALUES ('X', 'Y')")
check_true("8d-g: ...naming the reason", "readonly" in str(_WRITE_RAISED))
_RO_CONN.close()

_RO_ROWS_AFTER = _conn.execute(
    "SELECT COUNT(*) FROM inferences").fetchone()[0]
check("8d-h: ...and the refused write left the row count where it was",
      _RO_ROWS_AFTER, len(_SEED_ROWS))


# ===========================================================================
# SECTION 9 -- THE PRODUCTION DATABASE WAS NEVER TOUCHED
# ===========================================================================

print()
print("=" * 78)
print("SECTION 9 -- the production database is unchanged")
print("=" * 78)

check_true("resolve_query_db_path(None) is the production database and is NOT "
           "this file's scratch one -- which is what makes every check above "
           "discriminating rather than vacuous",
           _PRODUCTION_DB != _DB_PATH and _PRODUCTION_DB != _EMPTY_DB_PATH)

_PRODUCTION_ROWS_AFTER = _production_inference_rows()
if _PRODUCTION_ROWS_BEFORE is None:
    print("  NOTE  the production database is absent or unreadable on this "
          "machine, so there was nothing to compare. Nothing here could have "
          "written to it either: every path used above is under "
          f"{_TMP_DIR}.")
else:
    check_true("the production row count is non-degenerate -- a database with "
               "no rows would make the comparison below pass whatever "
               "happened", _PRODUCTION_ROWS_BEFORE > 0)
    check("the production inference row count is unchanged by this run",
          _PRODUCTION_ROWS_AFTER, _PRODUCTION_ROWS_BEFORE)
    # The comparison has to be able to report a change, or "unchanged" is
    # indistinguishable from a counter that always returns the same thing.
    check("...and the same comparison reports a difference as a difference "
          "(negative control)",
          _PRODUCTION_ROWS_AFTER == _PRODUCTION_ROWS_BEFORE + 1, False)


# ===========================================================================
# CLEANUP AND SUMMARY
# ===========================================================================

_conn.close()
_empty_conn.close()
shutil.rmtree(_TMP_DIR, ignore_errors=True)
check("the temporary directory is removed", os.path.isdir(_TMP_DIR), False)

print("\n" + "=" * 78)
print("SUMMARY")
print("=" * 78)
print(f"  passed: {_RESULTS['passed']}")
print(f"  failed: {_RESULTS['failed']}")

if _FAILURES:
    print("\nFAILURES:")
    for _failure in _FAILURES:
        print(f"  - {_failure}")

print()
sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug  5 2026

@author: ramyalsaffar
"""
