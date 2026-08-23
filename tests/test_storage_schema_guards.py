# Storage Schema Guards Test
###########################

"""Which schema era a database file is, and which questions it can answer.

WHAT WAS MISSING
----------------
Three findings from one database review, sharing one subject: nothing could ask
a database file what it was.

    1. THE GUARD WAS BUILT FOR ADDITIVE ABSENCE AND THE RENAME IS NOT ADDITIVE.
       ``oncotriage/storage/queries.py`` carries ``requires`` /
       ``requires_columns`` so a query naming a table or column a database does
       not have is SKIPPED rather than taking ``report()`` down with it. It
       worked. Then the gpt4o -> llm_classifier naming pass renamed nine columns
       of ``inferences`` in place, and every older query naming one became a
       query against a column no pre-rename database has -- with no declaration,
       because nobody re-read forty-eight queries. MEASURED before this pass:
       ``report()`` against the production database died at its SECOND query on
       ``no such column: llm_classifier_evaluation_time``, having printed eight
       lines. A rule kept by hand across a registry this size is already broken.

    2. ``trial_matches`` HAD NO INDEX AT ALL. Its only access path is the child
       lookup by ``inference_id``, so every one of them was a full scan of every
       trial row the database has ever held. The review measured 169 ms -> 0.02 ms
       at 22,000-patient scale. It also measured that an index on ``nct_id`` is
       HARMFUL -- 32% slower -- which is a standing ruling this file pins so the
       absence cannot be mistaken for an oversight and "fixed".

    3. ``PRAGMA user_version`` WAS 0 EVERYWHERE. SQLite offers a caller-owned
       integer in the file header, costing no table and no row, and this project
       had never written it. So "which schema era is this file" was answerable
       only by reading ``PRAGMA table_info`` on three tables and comparing the
       result against a reading of ``database_logger`` -- a derivation a person
       does, which is exactly how finding 1 survived.

WHAT THIS FILE HOLDS
--------------------
    1. THE DERIVATION IS THE CHECKER, NOT THE SOURCE. ``requires_columns`` stays
       hand-written on each ``Query``; ``derive_requires_columns`` reads the same
       query's SQL, and section 1 asserts the two agree for EVERY registered
       query. A query can no longer ship naming an additive column it forgot to
       declare. The ruling in ``Query.requires_columns`` -- that a COLUMN
       requirement is not derivable from a TABLE requirement -- is untouched and
       is a different statement.
    2. THE DERIVATION'S FOUR FALSE-POSITIVE SHAPES, each pinned with the
       measured registry case that produced it. Three of them were found by
       running the first version of the function against the real registry, not
       by reading it.
    3. THE RENAME RECORD IS CHECKED AGAINST A REAL DATABASE: every old name
       absent from a fresh one, every new name present, and every base
       ``llm_classifier_*`` column accounted for -- so the record cannot describe
       a state that no longer exists, and a TENTH rename fails here.
    4. THE INDEX, by ``EXPLAIN QUERY PLAN`` on a seeded database and by the
       ``nct_id`` ruling.
    5. ``user_version``: stamped on a fresh database, preserved across a
       re-open, bumped when the constant moves, and NEVER LOWERED.
    6. ``report()`` DRIVEN END TO END against a PRE-MIGRATION database carrying
       the production schema shape -- the case finding 1 is about -- required to
       reach its last query and to name, per query, what it skipped.

WHAT IT COSTS TO RUN
--------------------
No network, no keys, no spend, no live Qdrant, no model load, no corpus, no git
history, and no live server. Bucket A, ~2 s.

NOT IN THE COLLISION MATRIX, derived rather than assumed: every database it
opens is inside a ``tempfile.mkdtemp`` it removes and then asserts gone, it
patches no repository file, and the two repository files it READS
(``oncotriage/storage/queries.py``, ``oncotriage/storage/database_logger.py``)
are written by neither of the suite's two writers. Both are sha256-compared at
the end.

IT EXECS NOTHING, so it needs no ``_EXEC_ALLOWLIST`` entry. Every control is a
different INPUT to a pure function, a real database built into a real failing
shape, or a module constant rebound inside try/finally with the restore
asserted.

THE PRODUCTION DATABASE IS NEVER OPENED, not even read-only. Section 6 builds
its schema shape from this module's own literal, so the file needs no sibling
data tree and cannot touch one.

Run from terminal:
    python tests/test_storage_schema_guards.py

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

# No local model is reached here. A stand-in forgotten in a future edit becomes
# a named RuntimeError instead of a 110 MB download.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

import contextlib
import hashlib
import io
import shutil
import sqlite3
import tempfile

from oncotriage.storage import database_logger as _dl
from oncotriage.storage import queries as _q


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


def check_true(label, actual):
    check(label, bool(actual), True)


def fail(label, detail):
    """Record a failure that is not an equality comparison."""
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {detail}")
    print(f"  FAIL  {label}")
    print(f"          {detail}")


def skip(label, reason):
    """Record coverage that could NOT be exercised in THIS environment.

    A SKIP IS NOT A PASS. The mechanism and the argument are this project's
    existing ones. The summary line is printed EVEN AT ZERO: a skip count that
    appears only when non-zero is indistinguishable from a file with no skip
    mechanism at all.
    """
    _RESULTS["skipped"] += 1
    _SKIPS.append(f"{label}\n          {reason}")
    print(f"  SKIP  {label}")
    print(f"          {reason}")


class Raised:
    """What ``guarded`` returns instead of a result. Empty, falsy, and NOT a dict.

    THE SHAPE IS THE POINT AND THE FIRST VERSION GOT IT WRONG. It returned
    ``{"__raised__": ...}`` -- a DICT -- and section 6's headline check asks
    ``isinstance(report_result, dict)``. So the one assertion that says
    "report() ran to the end rather than dying" PASSED when report() raised,
    which is precisely the defect the section exists to catch. Measured, by
    removing a single declaration in a copy: 6c passed and only a downstream
    count check noticed.

    Being falsy and zero-length is deliberate too, so the ``(x or {})`` and
    ``len(x)`` readers below degrade to "nothing" instead of raising a SECOND
    exception while reporting the first.
    """

    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text

    def __bool__(self):
        return False

    def __len__(self):
        return 0

    def __contains__(self, item):
        return False

    def __iter__(self):
        return iter(())

    def __repr__(self):
        return f"<raised {self.text}>"


def guarded(fn, *args, **kwargs):
    """Call into production code, turning ANY raise into a value check() fails on.

    NOT DEFENSIVE PADDING. Ten files in this suite have shipped the same defect:
    a bare call inside a ``check(...)`` argument, where a planted or reverted
    defect raises, the exception escapes while the argument is being evaluated,
    and the run reports ONE TRACEBACK where it owed a summary and N results.
    Several sections here deliberately create failing conditions.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                       # noqa: BLE001 -- reported
        return Raised(f"{type(exc).__name__}: {exc}")


def at(seq, index, default="<absent>"):
    """``seq[index]`` or a named absence.

    Same reason as ``guarded``: a defect that makes a list shorter must produce
    a recorded FAILURE naming what was missing, not an ``IndexError`` that takes
    the remaining sections with it.
    """
    try:
        return seq[index]
    except (IndexError, KeyError, TypeError):
        return default


@contextlib.contextmanager
def quiet():
    """Swallow console output. ``console.out`` writes to STDERR, so both go."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        yield buf


def fresh_db(path, version=None):
    """A real database through the real ``initialize_database``.

    ``version`` rebinds ``SCHEMA_USER_VERSION`` for the duration, inside
    try/finally, and the restore is asserted by the caller that uses it. The
    stamp reads the module global at call time, which is what makes this the
    honest way to simulate a future era rather than writing the pragma by hand.
    """
    _dl._INITIALIZED_DATABASES.discard(os.path.abspath(path))
    if version is None:
        with quiet() as buf:
            _dl.initialize_database(path)
        return buf.getvalue()
    original = _dl.SCHEMA_USER_VERSION
    try:
        _dl.SCHEMA_USER_VERSION = version
        with quiet() as buf:
            _dl.initialize_database(path)
        return buf.getvalue()
    finally:
        _dl.SCHEMA_USER_VERSION = original


def user_version(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


_TMP = tempfile.mkdtemp(prefix="oncotriage_schema_guards_")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(_q.__file__)))
_QUERIES_PY = os.path.abspath(_q.__file__)
_LOGGER_PY = os.path.abspath(_dl.__file__)
_QUERIES_SHA_BEFORE = sha256(_QUERIES_PY)
_LOGGER_SHA_BEFORE = sha256(_LOGGER_PY)


#------------------------------------------------------------------------------


# ===========================================================================
# 1. EVERY REGISTERED QUERY'S DECLARATION EQUALS WHAT ITS SQL DERIVES
# ===========================================================================
#
# THE STANDING GUARD, and the reason the other two findings in this pass are
# small and this one is not. A declaration kept by hand is kept until someone
# forgets; this makes forgetting a failing test rather than a report that dies
# at its second query.

print("\n=== 1. requires_columns: declared == derived, for all of them ===")

_MISMATCHED = []
for _query in _q.QUERIES:
    _derived = set(guarded(_q.derive_requires_columns, _query.sql))
    _declared = set(_query.requires_columns)
    if _derived != _declared:
        _MISMATCHED.append((_query.key, sorted(_derived - _declared),
                            sorted(_declared - _derived)))

if _MISMATCHED:
    for _key, _undeclared, _over in _MISMATCHED:
        fail(f"1a {_key}: declaration does not match its SQL",
             f"undeclared: {_undeclared}   over-declared: {_over}")
else:
    check("1a every registered query declares exactly the additive columns "
          "its own SQL names", len(_MISMATCHED), 0)

# NON-DEGENERACY. The line above passes for free against a registry in which
# nothing declares anything and nothing derives anything, which is precisely the
# state this pass found. Both counts are pinned as ">= " rather than "== " so
# adding a query does not fail a check that is about the mechanism working.
_WITH_DECL = [q for q in _q.QUERIES if q.requires_columns]
check_true("1b ...and this is not vacuous: many queries DO declare columns",
           len(_WITH_DECL) >= 20)
check_true("1c ...and many DO derive them (a derivation that returned () for "
           "everything would satisfy 1a as well)",
           sum(1 for q in _q.QUERIES if _q.derive_requires_columns(q.sql)) >= 20)
check_true("1d ...and NOT every query declares columns, so the derivation is "
           "discriminating rather than blanket",
           0 < len(_WITH_DECL) < len(_q.QUERIES))

# THE CONTROL: a query naming an additive column and declaring nothing must be
# CAUGHT. Built as a Query object rather than by editing the registry -- a
# different INPUT to a pure function is the natural control for a pure function,
# and it keeps the shipped file untouched.
_PLANT = _q.Query(
    key="__planted_undeclared__",
    sql="SELECT patient_id, llm_classifier_evaluation_time FROM inferences",
)
check("1e CONTROL a query naming a renamed column derives it",
      _q.derive_requires_columns(_PLANT.sql),
      (("inferences", "llm_classifier_evaluation_time"),))
check("1f ...and its empty declaration therefore MISMATCHES, which is what "
      "check 1a reports",
      set(_q.derive_requires_columns(_PLANT.sql)) == set(_PLANT.requires_columns),
      False)

_PLANT_TM = _q.Query(
    key="__planted_trial_matches__",
    sql="SELECT nct_id, verdict_source FROM trial_matches WHERE hallucinated = 0",
)
check("1g CONTROL the trial_matches side derives too, and finds BOTH of its "
      "additive columns",
      _q.derive_requires_columns(_PLANT_TM.sql),
      (("trial_matches", "hallucinated"), ("trial_matches", "verdict_source")))

# A query naming NO additive column derives nothing. Without this the whole
# section is satisfied by a function that returns every additive column always.
check("1h ...and a query naming no additive column derives nothing",
      _q.derive_requires_columns(
          "SELECT patient_id, total_time FROM inferences"),
      ())


#------------------------------------------------------------------------------


# ===========================================================================
# 2. THE FOUR FALSE-POSITIVE SHAPES THE DERIVATION MUST NOT FALL FOR
# ===========================================================================
#
# Three of these were found by RUNNING the first version of the function against
# the real registry, not by reading it. Each is pinned with the registry case
# that produced it, because a shape nobody can name is a shape the next edit
# reintroduces.

print("\n=== 2. the derivation's known false-positive shapes ===")

# (a) A TABLE THE QUERY DOES NOT REFERENCE. `run_degradation_breakdown` reads
#     `runs` and `run_metrics` and never touches `inferences`; `run_id` is a
#     column of run_metrics. A bare-name match derives `inferences.run_id` and
#     skips the query on every database that could answer it.
check("2a a bare additive name in a query that never references its table "
      "derives NOTHING",
      _q.derive_requires_columns(
          "SELECT r.id, rm.run_id FROM runs r "
          "LEFT JOIN run_metrics rm ON rm.run_id = r.id"),
      ())
check("2b ...and the real registry case agrees: run_degradation_breakdown "
      "declares no inferences column",
      [c for (t, c) in _q.QUERIES_BY_KEY["run_degradation_breakdown"]
       .requires_columns if t == "inferences"],
      [])

# (b) A QUALIFIER BINDING A DIFFERENT TABLE. `run_summary` selects
#     `r.llm_classifier_prompt_version` where r is `runs` -- and that column
#     name is ALSO in INFERENCE_COLUMN_ADDITIONS.
check("2c a column qualified with an alias for ANOTHER table is not this "
      "table's",
      _q.derive_requires_columns(
          "SELECT r.llm_classifier_prompt_version FROM runs r "
          "LEFT JOIN inferences i ON i.run_id = r.id"),
      (("inferences", "run_id"),))
check("2d ...and the real registry case agrees: run_summary declares run_id "
      "and NOT llm_classifier_prompt_version",
      sorted(c for (t, c) in _q.QUERIES_BY_KEY["run_summary"].requires_columns),
      ["run_id"])

# (c) `AS <name>` IS AN OUTPUT NAME, NOT A READ.
check("2e a bare `<expr> AS <additive name>` is not a reference",
      _q.derive_requires_columns(
          "SELECT COUNT(*) AS verdict_source FROM trial_matches"),
      ())
check("2f ...but a name that is BOTH a read and an output name still derives -- "
      "the expansion_path_x_mesh_resolution shape, where stripping the alias "
      "naively would lose a column the query really reads",
      _q.derive_requires_columns(
          "SELECT COALESCE(mesh_resolution, '(none)') AS mesh_resolution "
          "FROM inferences GROUP BY mesh_resolution"),
      (("inferences", "mesh_resolution"),))

# (d) A STRING LITERAL IS NOT AN IDENTIFIER. `not_evaluable_reasons` filters on
#     `eligible = 'not_evaluable'`, and `not_evaluable_reason` is a real column.
check("2g a column name appearing only inside a string literal derives nothing",
      _q.derive_requires_columns(
          "SELECT nct_id FROM trial_matches WHERE eligible = 'verdict_source'"),
      ())
check("2h ...and a `--` comment naming one derives nothing either",
      _q.derive_requires_columns(
          "SELECT nct_id FROM trial_matches -- verdict_source is not read here"),
      ())
check("2i ...and the comment ends at the LINE, not at the string: a real column "
      "BELOW a comment still derives (2h alone cannot tell the two apart, and "
      "an end-of-string regex would pass it)",
      _q.derive_requires_columns(
          "SELECT nct_id -- verdict_source is not read\n"
          "FROM trial_matches WHERE hallucinated = 0"),
      (("trial_matches", "hallucinated"),))

# A SUBQUERY ALIAS BINDS NO BASE TABLE, which is what makes `p.patients` in
# run_summary resolve to nothing rather than to whichever table sorts first.
check("2j a subquery alias binds no base table",
      _q.sql_table_aliases(
          "SELECT p.x FROM (SELECT 1 AS x FROM inferences) p").get("p"),
      None)
check("2k ...while a real table alias binds its table",
      _q.sql_table_aliases("SELECT * FROM trial_matches tm")["tm"],
      "trial_matches")
check("2l ...and a table always binds itself, so an unaliased qualifier works",
      _q.sql_table_aliases("SELECT * FROM inferences")["inferences"],
      "inferences")
check("2m ...and a keyword following the table name is not read as an alias",
      sorted(_q.sql_table_aliases(
          "SELECT * FROM inferences WHERE run_id IS NOT NULL")),
      ["inferences"])

# A COMMA-SEPARATED FROM LIST BINDS EVERY TABLE IN IT. Losing one is the
# DANGEROUS direction and is invisible to section 1: a table nothing binds
# derives no column, the query then declares none, and declaration and
# derivation AGREE about the same blind spot. It surfaces only as a crash inside
# report(), which is what this whole pass exists to remove.
check("2n a comma-separated FROM list binds EVERY table, not just the first",
      sorted(set(_q.sql_table_aliases(
          "SELECT 1 FROM inferences, trial_matches").values())),
      ["inferences", "trial_matches"])
check("2o ...with their aliases",
      _q.sql_table_aliases(
          "SELECT 1 FROM inferences i, trial_matches tm")["tm"],
      "trial_matches")
check("2p ...and both tables' columns are therefore derived",
      _q.derive_requires_columns(
          "SELECT i.run_id, tm.verdict_source FROM inferences i, trial_matches tm"),
      (("inferences", "run_id"), ("trial_matches", "verdict_source")))
check("2q ...while a SELECT-list comma binds nothing: the continuation is "
      "anchored at the end of the previous FROM item, so it cannot reach a "
      "projection list",
      sorted(_q.sql_table_aliases("SELECT a, b, c FROM inferences")),
      ["inferences"])


#------------------------------------------------------------------------------


# ===========================================================================
# 3. THE RENAME RECORD DESCRIBES A STATE THAT REALLY EXISTS
# ===========================================================================
#
# ``RENAMED_INFERENCE_COLUMNS`` is a hand-written record of what the gpt4o ->
# llm_classifier pass renamed, and the guard takes its keys into the additive
# set. A hand-written record of a past event is exactly the shape that goes
# stale, so it is checked against a REAL database rather than against itself.

print("\n=== 3. the rename record, against a real fresh database ===")

_FRESH = os.path.join(_TMP, "fresh.db")
_FRESH_LOG = fresh_db(_FRESH)
_FRESH_CONN = sqlite3.connect(_FRESH)
_FRESH_INF = frozenset(r[1] for r in
                       _FRESH_CONN.execute("PRAGMA table_info(inferences)"))
_FRESH_TM = frozenset(r[1] for r in
                      _FRESH_CONN.execute("PRAGMA table_info(trial_matches)"))

check_true("3a the fresh database is non-degenerate (a check against an empty "
           "column set would satisfy everything below)",
           len(_FRESH_INF) > 60 and len(_FRESH_TM) > 10)
check("3b every CURRENT name in the rename record is a column of a fresh "
      "inferences table",
      sorted(n for n in _dl.RENAMED_INFERENCE_COLUMNS if n not in _FRESH_INF),
      [])
check("3c ...and NO pre-rename name is, which is what says the rename actually "
      "happened and this record is not describing today's schema",
      sorted(o for o in _dl.RENAMED_INFERENCE_COLUMNS.values()
             if o in _FRESH_INF),
      [])
# THE STALENESS GUARD IN THE OTHER DIRECTION. A tenth column renamed into the
# base CREATE TABLE without an entry here would be additive-shaped and
# undeclarable, and nothing else in this suite would see it. "Base" is derived,
# not listed: a current column that is NOT in INFERENCE_COLUMN_ADDITIONS is one
# the CREATE TABLE carries, and the only way an `llm_classifier_*` name got
# there is the rename.
_BASE_LLM = sorted(c for c in _FRESH_INF
                   if c.startswith("llm_classifier_")
                   and c not in _dl.INFERENCE_COLUMN_ADDITIONS)
check_true("3d ...and the base table really does carry renamed columns "
           "(non-degeneracy for 3e)", len(_BASE_LLM) >= 5)
check("3e every base `llm_classifier_*` column is in the rename record, so a "
      "TENTH rename fails here rather than shipping undeclarable",
      [c for c in _BASE_LLM if c not in _dl.RENAMED_INFERENCE_COLUMNS], [])
check("3f trial_matches was not touched by the rename, so its additive set "
      "needs no rename entries",
      [c for c in _FRESH_TM if c.startswith("llm_classifier_")], [])

# THE GUARD'S SET IS THE UNION, and both halves are really in it.
check("3g the derivation's additive set for inferences is exactly the union of "
      "the additions dict and the rename record",
      _q.ADDITIVE_COLUMNS["inferences"],
      frozenset(_dl.INFERENCE_COLUMN_ADDITIONS)
      | frozenset(_dl.RENAMED_INFERENCE_COLUMNS))
check_true("3h ...and the rename record contributes names the additions dict "
           "does not, so the union is not decoration",
           bool(frozenset(_dl.RENAMED_INFERENCE_COLUMNS)
                - frozenset(_dl.INFERENCE_COLUMN_ADDITIONS)))
# THE MAP DECLARES NO RUN TABLE, and the day a RUN_COLUMN_ADDITIONS dict is
# created -- which database_logger's own schema comment instructs -- this fails
# and demands the entry. Without it a column added to `runs` would be
# individually absent from older databases and underivable.
check("3i no run table is in the additive map, and this fails the day "
      "RUN_COLUMN_ADDITIONS is created without wiring it in",
      hasattr(_dl, "RUN_COLUMN_ADDITIONS")
      and "runs" not in _q.ADDITIVE_COLUMNS,
      False)


#------------------------------------------------------------------------------


# ===========================================================================
# 4. THE INDEX ON trial_matches(inference_id) -- AND THE ONE THAT IS NOT THERE
# ===========================================================================

print("\n=== 4. the trial_matches child-lookup index ===")

_FRESH_INDEXES = sorted(
    r[0] for r in _FRESH_CONN.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name NOT LIKE 'sqlite_%'"))
check("4a initialize_database creates the child-lookup index",
      "idx_trial_matches_inference_id" in _FRESH_INDEXES, True)
check("4b ...beside the run_metrics index it was modelled on, so the precedent "
      "is still there",
      "idx_run_metrics_run_id" in _FRESH_INDEXES, True)
# THE RULING, PINNED. An index on nct_id was MEASURED harmful (32% slower): the
# queries that group by it read the whole table anyway, so the planner gains
# nothing and every insert maintains a second B-tree. Pinned as an EXACT set so
# adding one fails here with the reason attached, rather than being a silent
# regression nobody re-measures.
check("4c ...and NO index on nct_id, which is a measured ruling and not an "
      "oversight (32% slower; see the comment at the CREATE INDEX)",
      _FRESH_INDEXES, ["idx_run_metrics_run_id", "idx_trial_matches_inference_id"])
# Re-open the SAME database through the real initialize_database. Written as
# two statements rather than one expression: the first version buried the
# re-open inside a conditional whose evaluation ORDER was what made the check
# work, which is a check nobody can read and the next edit breaks silently.
fresh_db(_FRESH)
_REOPENED_INDEXES = sorted(
    r[0] for r in sqlite3.connect(_FRESH).execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name NOT LIKE 'sqlite_%'"))
check("4d re-opening the database creates nothing new (IF NOT EXISTS)",
      _REOPENED_INDEXES, _FRESH_INDEXES)

# THE PLANNER ACTUALLY USES IT, on a seeded database, against a database built
# WITHOUT it as the control.
#
# TWO MECHANICAL TRAPS, both met while writing this and both encoded here:
#   1. python's sqlite3 CACHES PREPARED STATEMENTS (128 by default). Dropping an
#      index and re-running the same SQL string on the same connection replays
#      the OLD plan -- which reported "uses the index" against a database that
#      no longer had one. Every plan below is read on a connection opened with
#      cached_statements=0.
#   2. The two arms are SEPARATE DATABASES built independently, rather than one
#      database with the index dropped part-way, so no ANALYZE statistics or
#      page cache survives from the other arm.
_CHILD_SQL = "SELECT * FROM trial_matches WHERE inference_id = 7"


def build_seeded(path, keep_index):
    """A seeded database with the index kept or dropped.

    `IF EXISTS` ON THE DROP, AND IT IS NOT DEFENSIVE PADDING. A revert harness
    removing the CREATE INDEX from initialize_database made a bare DROP raise
    `no such index` -- uncaught, at module level, in the section written to
    catch exactly that removal. The run reported ONE TRACEBACK where it owed a
    summary and thirty-odd results, and 4a..4d had already PASSED above it, so
    the transcript read like a partial success. That is this project's
    most-repeated defect and this file had it too.

    The control arm asserts the index is really gone (4h), so tolerating an
    absent index here cannot make the arm silently identical to the other one.
    """
    fresh_db(path)
    conn = sqlite3.connect(path)
    try:
        if not keep_index:
            conn.execute("DROP INDEX IF EXISTS idx_trial_matches_inference_id")
            conn.commit()
        conn.executemany(
            "INSERT INTO trial_matches (inference_id, nct_id) VALUES (?, ?)",
            [(i // 15, "NCT%07d" % i) for i in range(30000)])
        conn.commit()
    finally:
        conn.close()
    return path


def query_plan(path):
    conn = sqlite3.connect(path, cached_statements=0)
    try:
        return [row[-1] for row in
                conn.execute("EXPLAIN QUERY PLAN " + _CHILD_SQL)]
    finally:
        conn.close()


_WITH = build_seeded(os.path.join(_TMP, "seeded_with_index.db"), True)
_WITHOUT = build_seeded(os.path.join(_TMP, "seeded_no_index.db"), False)

_PLAN_WITH = at(guarded(query_plan, _WITH), 0)
_PLAN_WITHOUT = at(guarded(query_plan, _WITHOUT), 0)

check("4e the child lookup SEARCHes using the index rather than scanning",
      "SEARCH" in str(_PLAN_WITH)
      and "idx_trial_matches_inference_id" in str(_PLAN_WITH), True)
check("4f CONTROL the same query on the same shape WITHOUT the index scans -- "
      "so 4e is about the index and not about the query",
      "SCAN" in str(_PLAN_WITHOUT)
      and "idx_trial_matches_inference_id" not in str(_PLAN_WITHOUT), True)
check("4g ...and the two arms really do differ (a plan reader that returned "
      "the same string for both would pass 4e and 4f in some future sqlite)",
      _PLAN_WITH != _PLAN_WITHOUT, True)
check("4h ...and the control arm really lacks the index, so 4f is not about a "
      "planner preference",
      "idx_trial_matches_inference_id" in [
          r[0] for r in sqlite3.connect(_WITHOUT).execute(
              "SELECT name FROM sqlite_master WHERE type='index'")], False)


#------------------------------------------------------------------------------


# ===========================================================================
# 5. PRAGMA user_version: STAMPED, PRESERVED, BUMPED, NEVER LOWERED
# ===========================================================================

print("\n=== 5. the schema era stamp ===")

check("5a the constant starts at 1, not 0 -- 0 has to keep meaning 'unstamped, "
      "era unknown', which is what every database written before this pass "
      "reports",
      _dl.SCHEMA_USER_VERSION >= 1, True)

_STAMP = os.path.join(_TMP, "stamp.db")
_STAMP_LOG = fresh_db(_STAMP)
check("5b a fresh database is stamped with the current era",
      user_version(_STAMP), _dl.SCHEMA_USER_VERSION)
check_true("5c ...and the stamp is ANNOUNCED on its way from 0, so a reader of "
           "the console knows the file moved era",
           "Schema stamp" in _STAMP_LOG and "0 -> " in _STAMP_LOG)

# PRESERVED. Re-opening at the same era must not print a transition it did not
# make -- a message on every open is a message people stop reading.
_REOPEN_LOG = fresh_db(_STAMP)
check("5d re-opening at the same era leaves the stamp where it is",
      user_version(_STAMP), _dl.SCHEMA_USER_VERSION)
check("5e ...and says nothing, because nothing moved",
      "Schema stamp" in _REOPEN_LOG, False)

# BUMPED. The one thing the constant is FOR: a schema change bumps it in the
# same commit, and the next writer to open an older file moves that file.
_BUMPED_LOG = fresh_db(_STAMP, version=_dl.SCHEMA_USER_VERSION + 1)
check("5f a database opened by a LATER era is moved to it",
      user_version(_STAMP), _dl.SCHEMA_USER_VERSION + 1)
check_true("5g ...and the transition is announced with both numbers",
           "Schema stamp" in _BUMPED_LOG
           and str(_dl.SCHEMA_USER_VERSION + 1) in _BUMPED_LOG)
check("5h ...and the rebinding was restored, so no later check is reading a "
      "patched constant", _dl.SCHEMA_USER_VERSION, 1)

# NEVER LOWERED. The database is now at era N+1 and this code is era N. Because
# this schema is strictly additive -- nothing here drops a column, a table or an
# index -- the file still HAS everything era N+1 gave it. Writing N over the N+1
# would replace a true statement with a false one.
_LOWER_LOG = fresh_db(_STAMP)
check("5i an older writer does NOT lower a newer file's stamp",
      user_version(_STAMP), _dl.SCHEMA_USER_VERSION + 1)
check_true("5j ...and refuses out loud, naming both eras -- a silent no-op here "
           "is indistinguishable from a stamp that worked",
           "LEFT AT" in _LOWER_LOG
           and str(_dl.SCHEMA_USER_VERSION + 1) in _LOWER_LOG)
# AND IT STILL DID ITS JOB. Refusing the stamp must not mean refusing the
# migration: the older writer still ensures everything it knows about.
_LOWERED_CONN = sqlite3.connect(_STAMP)
check("5k ...while still migrating what it knows about, so the refusal is "
      "about the LABEL and not about the work",
      frozenset(r[1] for r in
                _LOWERED_CONN.execute("PRAGMA table_info(inferences)"))
      >= frozenset(_dl.INFERENCE_COLUMN_ADDITIONS), True)
_LOWERED_CONN.close()

# AN UNSTAMPED DATABASE READS 0, which is what makes 0 usable as "ask
# table_info". Built by hand rather than by this module, because that is the
# state of every file written before this pass.
_UNSTAMPED = os.path.join(_TMP, "unstamped.db")
_UNSTAMPED_CONN = sqlite3.connect(_UNSTAMPED)
_UNSTAMPED_CONN.execute("CREATE TABLE inferences (id INTEGER PRIMARY KEY)")
_UNSTAMPED_CONN.commit()
_UNSTAMPED_CONN.close()
check("5l a database nobody stamped reads 0, so era 0 stays available as "
      "'unknown, ask table_info'", user_version(_UNSTAMPED), 0)

# THE STAMP IS WRITTEN LAST, over a file that HAS the era it claims. Asserted
# structurally, because the ordering is the property and no runtime observation
# of a SUCCESSFUL init can distinguish it from a stamp written first.
_INIT_SRC = _dl.initialize_database.__code__
with open(_LOGGER_PY, encoding="utf-8") as _handle:
    _LOGGER_TEXT = _handle.read()
_STAMP_AT = _LOGGER_TEXT.find("PRAGMA user_version = {")
_LAST_ALTER_AT = _LOGGER_TEXT.rfind("ALTER TABLE trial_matches ADD COLUMN")
_LAST_INDEX_AT = _LOGGER_TEXT.rfind("CREATE INDEX IF NOT EXISTS")
check_true("5m the three landmarks are all present (non-degeneracy: -1 would "
           "make the ordering checks below compare nothing)",
           _STAMP_AT > 0 and _LAST_ALTER_AT > 0 and _LAST_INDEX_AT > 0)
check("5n the stamp is written AFTER the last column migration",
      _STAMP_AT > _LAST_ALTER_AT, True)
check("5o ...and after the last index creation, so it labels a file that has "
      "everything the era gives it", _STAMP_AT > _LAST_INDEX_AT, True)


#------------------------------------------------------------------------------


# ===========================================================================
# 6. report() AGAINST A PRE-MIGRATION DATABASE RUNS TO ITS LAST QUERY
# ===========================================================================
#
# THE CASE FINDING 1 IS ABOUT. Before this pass, report() against a database in
# this shape died at its SECOND query having printed eight lines.
#
# THE SHAPE IS BUILT MECHANICALLY FROM THIS MODULE'S OWN CONSTANTS, never read
# from the production file: a fresh database, every renamed column renamed BACK,
# every additive column dropped, both run tables dropped. That is a database
# strictly OLDER than production, so it exercises the same path with no sibling
# data tree, no read-only URI, and no possibility of touching a real file.

print("\n=== 6. report() against a pre-migration database ===")

_OLD = os.path.join(_TMP, "pre_migration.db")
fresh_db(_OLD)
_OLD_CONN = sqlite3.connect(_OLD)
for _new, _old_name in _dl.RENAMED_INFERENCE_COLUMNS.items():
    if _new in _dl.INFERENCE_COLUMN_ADDITIONS:
        continue          # dropped below; renaming first would lose the drop
    _OLD_CONN.execute(
        f"ALTER TABLE inferences RENAME COLUMN {_new} TO {_old_name}")
for _column in _dl.INFERENCE_COLUMN_ADDITIONS:
    _OLD_CONN.execute(f"ALTER TABLE inferences DROP COLUMN {_column}")
for _column in _dl.TRIAL_MATCH_COLUMN_ADDITIONS:
    _OLD_CONN.execute(f"ALTER TABLE trial_matches DROP COLUMN {_column}")
_OLD_CONN.execute("DROP TABLE run_metrics")
_OLD_CONN.execute("DROP TABLE runs")
_OLD_CONN.commit()

_OLD_INF = frozenset(r[1] for r in
                     _OLD_CONN.execute("PRAGMA table_info(inferences)"))
check_true("6a the pre-migration database really is pre-migration: it carries "
           "the OLD names and none of the new ones",
           "gpt4o_evaluation_time" in _OLD_INF
           and "llm_classifier_evaluation_time" not in _OLD_INF)
check("6b ...and has neither run table",
      sorted(t for t in _q.RUN_TABLES
             if t in _q.available_tables(_OLD_CONN)), [])

_LINES = []
_REPORT = guarded(_q.report, _OLD_CONN, out=_LINES.append)
# 6c IS THE UNDER-DERIVATION GUARD, AND THAT IS WHY IT IS DRIVEN HERE RATHER
# THAN ONLY ASSERTED IN SECTION 1.
#
# Section 1 compares a declaration against a derivation. It cannot see a column
# BOTH of them miss: if the reader cannot see a reference, the derivation omits
# it, the author copies the derivation into the declaration, and the two agree
# about the same blind spot. Nothing in a comparison of the two can catch that.
#
# THIS database is missing EVERY additive column. So a query that reads one and
# declares it is skipped, and a query that reads one and does NOT is run --
# where it raises `no such column` and takes report() with it. Reaching the end
# is therefore an empirical statement that no query in the registry reads an
# additive column the reader could not see.
#
# The result is tested for NOT BEING A `Raised` MARKER rather than for being a
# dict. The first version asked `isinstance(_REPORT, dict)` while `guarded`
# returned a dict on failure, so the one check that says "it ran to the end"
# passed when it had not -- measured by removing a single declaration in a copy.
check("6c report() RUNS TO THE END rather than dying at its second query -- and "
      "since this database is missing EVERY additive column, that is also the "
      "empirical guard against a column BOTH the declaration and the derivation "
      "missed, which section 1 cannot see",
      isinstance(_REPORT, dict) and not isinstance(_REPORT, Raised), True)
_SKIPPED = guarded(_q.unavailable, _OLD_CONN)
check_true("6d ...and something really was skipped, so 6c is not the trivial "
           "case of a database that can answer everything",
           isinstance(_SKIPPED, dict) and len(_SKIPPED) > 0)
check_true("6e ...and something really RAN, so 6c is not the equally trivial "
           "case of a report that skipped its whole registry",
           isinstance(_REPORT, dict) and len(_REPORT) > 0)
check("6f every registered query either ran or was skipped -- none silently "
      "vanished",
      len(_REPORT) + len(_SKIPPED), len(_q.QUERIES))
check("6g a skipped query is ABSENT from the returned dict rather than present "
      "with an empty frame",
      sorted(k for k in _SKIPPED if k in (_REPORT or {})), [])

_TEXT = "\n".join(str(line) for line in _LINES)
check_true("6h the skip is PRINTED with a count, not silently covered less",
           "SKIPPED" in _TEXT and f"of {len(_q.QUERIES)}" in _TEXT)
# PER QUERY, WHICH IS THE CORRECTION. The old block printed the union of every
# absent name attributed to the alphabetically FIRST skipped key -- readable
# while three queries skipped together, actively false at twenty-one.
_MISATTRIBUTED = []
for _key, _absent in sorted((_SKIPPED or {}).items()):
    _line = [ln for ln in _TEXT.splitlines() if ln.strip().startswith(_key + ":")]
    if not _line:
        _MISATTRIBUTED.append(f"{_key}: no per-query line printed")
        continue
    _named = {n.strip() for n in at(_line, 0).split(":", 1)[1].split(",")}
    if _named != set(_absent):
        _MISATTRIBUTED.append(f"{_key}: printed {sorted(_named)} "
                              f"want {sorted(_absent)}")
check("6i ...and every skipped query gets its OWN line naming its OWN absent "
      "columns", _MISATTRIBUTED, [])
# THE REMEDY THE MESSAGE OFFERS MUST BE ONE THAT WORKS.
#
# The skip paragraph ends "the next writer to open the file adds it". That is
# true of an INFERENCE_COLUMN_ADDITIONS entry and FALSE of a renamed one: the
# migration loop can only ADD, so no writer will ever produce
# `llm_classifier_evaluation_time` on a pre-rename database. An operator told to
# run a writer would watch it change nothing and have no next step -- and on the
# production database that was the MAJORITY of the skipped queries.
_ADDITIVE_ONLY = ("runs", "run_metrics", "inferences.run_id")
_RENAMED_ONE = ("inferences.llm_classifier_evaluation_time",)
check("6p a renamed column is identified as one, and named with its pre-rename "
      "spelling",
      _q.renamed_predecessor("inferences.llm_classifier_evaluation_time"),
      "gpt4o_evaluation_time")
check("6q ...while an ordinary additive column is not (non-degeneracy: a "
      "classifier that said 'renamed' to everything would pass 6p)",
      _q.renamed_predecessor("inferences.run_id"), None)
check("6r ...and neither is a TABLE, which no rename can apply to",
      _q.renamed_predecessor("runs"), None)
check_true("6s a message about a renamed column says NO WRITER WILL REPAIR IT",
           "NO WRITER WILL REPAIR" in
           _q.missing_table_message("timing_columns", _RENAMED_ONE))
check("6t ...and a message about purely additive absence does NOT, so the "
      "warning is not boilerplate on every skip",
      "NO WRITER WILL REPAIR" in
      _q.missing_table_message("run_summary", _ADDITIVE_ONLY), False)
check("6u ...and the note names the pre-rename spelling the data is under",
      "gpt4o_evaluation_time" in _q.rename_note(_RENAMED_ONE), True)
# THE PRINTED REPORT CARRIES IT TOO. This database's skip list is mostly
# renames, so the clause has to be there or the same misdirection is on screen.
check_true("6v report()'s skip banner carries the rename warning when a renamed "
           "column is among the absences",
           "NO WRITER WILL REPAIR" in _TEXT)
check_true("6w ...and this database's absences really do include renames "
           "(non-degeneracy for 6v)",
           any(_q.renamed_predecessor(n)
               for tup in (_SKIPPED or {}).values() for n in tup))

check_true("6j ...and there is more than one distinct absence across the skip "
           "list, so 6i could actually have caught a union",
           len({tuple(v) for v in (_SKIPPED or {}).values()}) > 1)

# THE SKIP DECISIONS ARE TRUE OF THIS DATABASE, both ways. Without these, a
# guard that skipped everything, or one that skipped by declaration without
# consulting the file, would pass every check above.
_WRONGLY_SKIPPED, _WRONGLY_RAN = [], []
for _query in _q.QUERIES:
    _absent_here = []
    for _table, _column in _query.requires_columns:
        if _table in _q.available_tables(_OLD_CONN) \
                and _column not in _q.table_columns(_OLD_CONN, _table):
            _absent_here.append(f"{_table}.{_column}")
    _absent_here += [t for t in _query.requires
                     if t not in _q.available_tables(_OLD_CONN)]
    if _absent_here and _query.key not in (_SKIPPED or {}):
        _WRONGLY_RAN.append(_query.key)
    if not _absent_here and _query.key in (_SKIPPED or {}):
        _WRONGLY_SKIPPED.append(_query.key)
check("6k no query was skipped whose declared requirements this database "
      "actually meets", _WRONGLY_SKIPPED, [])
check("6l ...and none ran whose requirements it does not", _WRONGLY_RAN, [])

# AND THE CONTROL: the SAME registry against a CURRENT database skips nothing.
# A guard that always fires proves nothing about a guard.
_CURRENT_SKIPPED = guarded(_q.unavailable, _FRESH_CONN)
check("6m CONTROL the same registry against a current database skips nothing",
      _CURRENT_SKIPPED, {})
_CURRENT_LINES = []
_CURRENT_REPORT = guarded(_q.report, _FRESH_CONN, out=_CURRENT_LINES.append)
check("6n ...and answers every query in the registry",
      len(_CURRENT_REPORT), len(_q.QUERIES))
check("6o ...printing no skip banner at all",
      "SKIPPED" in "\n".join(str(line) for line in _CURRENT_LINES), False)

_OLD_CONN.close()
_FRESH_CONN.close()


#------------------------------------------------------------------------------


# ===========================================================================
# 7. THIS FILE WROTE NOTHING IN THE REPOSITORY
# ===========================================================================

print("\n=== 7. isolation ===")

check("7a oncotriage/storage/queries.py is byte-identical",
      sha256(_QUERIES_PY), _QUERIES_SHA_BEFORE)
check("7b oncotriage/storage/database_logger.py is byte-identical",
      sha256(_LOGGER_PY), _LOGGER_SHA_BEFORE)
check_true("7c every database this file opened is inside its own temp directory",
           all(os.path.abspath(p).startswith(os.path.abspath(_TMP))
               for p in (_FRESH, _STAMP, _OLD, _UNSTAMPED, _WITH, _WITHOUT)))

for _path in (_FRESH, _STAMP, _OLD, _UNSTAMPED, _WITH, _WITHOUT):
    _dl._INITIALIZED_DATABASES.discard(os.path.abspath(_path))
shutil.rmtree(_TMP, ignore_errors=True)
check("7d the scratch directory was removed", os.path.exists(_TMP), False)


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
Created on Sat Aug 23 2026

@author: ramyalsaffar
"""
