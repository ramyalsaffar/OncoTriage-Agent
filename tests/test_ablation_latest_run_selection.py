# Which ablation_runs row is "the latest", and the two ways the old answer lied
############################################################################

"""Ablation Latest-Run Selection Test

WHAT THIS IS ABOUT. `oncotriage/ablation/study.py` has two readers that must
agree on ONE question -- which `ablation_runs` row is the latest for its
configuration:

  * `generate_summary`, which INNER JOINs that row's results and averages them
    into the printed table and `ablation_summary.json`;
  * `_summary_status_warning`, which reads that row's `status` and prints the
    qualification that says the averages above cover a PREFIX of the sample.

They were two hand-written copies of one SELECT, and both copies were the same
wrong SELECT:

    WHERE (config_name, run_timestamp) IN (
        SELECT config_name, MAX(run_timestamp) FROM ablation_runs
        GROUP BY config_name)

`_LATEST_RUN_PER_CONFIG_SQL` is the one owner now and it is `MAX(id)`. This file
pins both halves of that -- the CONSOLIDATION (section 4: exactly one SELECT, and
both readers interpolate it) and the CORRECTION (sections 2 and 3: the two ways
`MAX(run_timestamp)` picks the wrong row, each REPRODUCED against the pre-fix
SQL and then shown fixed).

THE TWO DEFECTS, AND WHY NEITHER RAISES.

1.  AN EXACT TIE SELECTS MORE THAN ONE ROW. `IN` matches every row carrying the
    maximum, so two runs of one configuration sharing a `run_timestamp` BOTH
    qualify. `_summary_status_warning` then reports that configuration twice --
    once per status -- and `generate_summary`'s INNER JOIN admits BOTH runs'
    results and averages them together, presenting a mean over two runs as the
    latest run's. Section 2 drives exactly that: two runs, one timestamp, one
    COMPLETE and one KILLED, with results that differ enough that the pooled
    mean is a third number belonging to neither.

    `run_timestamp` is `datetime.now().isoformat()`, which carries microseconds,
    so a tie needs two inserts inside one microsecond. `_create_run` holds
    `_ablation_db_lock` across its insert, which serialises them but does not
    make the clock advance. A coarse clock, a restored row, a row copied between
    databases or a hand-written fixture all produce one; the point is that the
    query has no defence against it and reports a wrong number rather than an
    error.

2.  LOCAL TIME IS NOT MONOTONE. `datetime.now()` is naive local time, so at the
    DST fall-back the wall clock repeats an hour: a run started at 01:30 EDT and
    a LATER one started at 01:30 EST write timestamps an hour apart IN THE WRONG
    DIRECTION. `MAX(run_timestamp)` picks the earlier run. Section 3 drives that
    with the two timestamps a real US/Eastern fall-back produces, and requires
    the superseded run's numbers to be the ones reported before the fix and the
    genuinely-latest run's after it.

    A study spanning the boundary is not exotic: seven configurations at one
    live Stage 5 call per pair is hours.

`id` HAS NEITHER FAULT, and section 5 says why in the only terms that matter
here -- it is `INTEGER PRIMARY KEY AUTOINCREMENT`, so it is unique (no tie is
POSSIBLE, and `MAX` therefore selects exactly one row per configuration) and
monotone in insert order. Section 5 asserts both against a database built by the
shipped `init_ablation_db` and `_create_run`, rather than against a schema
retyped here.

HOW THE CONTROLS WORK. The pre-fix SELECT is not lifted out of git -- it is
written out ONCE, in `_PRE_FIX_LATEST_SQL`, and the two readers' pre-fix bodies
are reconstructed by interpolating it into the SAME surrounding SQL the shipped
readers use. That is deliberate and it is the weaker of the two available
options, so it is stated rather than glossed: a `git show` control would prove
the shipped readers changed, and this proves the shipped readers behave
differently from the documented pre-fix predicate. What makes it honest is that
section 4 separately pins that the shipped readers contain NO `MAX(run_timestamp)`
and DO interpolate the one owner -- so the pre-fix SQL here cannot silently
become a copy of what ships.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO MODEL LOAD, NO CORPUS, NO GIT
HISTORY, NO LIVE SERVER. THE GRAPH IS NEVER INVOKED and no billed call is
reachable: nothing here calls the pipeline at all -- every row is an INSERT of
literals. NOT in `tests/run_serial_tests.py`'s collision matrix, derived rather
than asserted: every database is inside a `tempfile.mkdtemp()` this file removes
and then asserts gone, `paths._RESOLVED` is seeded so nothing can resolve to the
production tree, and the one repository file it READS
(`oncotriage/ablation/study.py`) is written by neither of the suite's two
writers and is sha256-compared at the end. IT EXECS NOTHING: every control is a
different SQL STRING handed to the same sqlite connection, which is the natural
control for a defect that IS a SQL string.

    python tests/test_ablation_latest_run_selection.py
"""

import ast
import contextlib
import hashlib
import io
import os
import shutil
import sqlite3
import sys
import tempfile

# ABOVE THE PACKAGE IMPORTS ON PURPOSE. oncotriage/agent/deps.py reads this
# variable ONCE, at its own import; an assignment underneath the imports reaches
# nothing. Nothing here needs a local model, and this is the second line of
# defence that says so.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

try:
    import oncotriage                                          # noqa: F401
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

from oncotriage import paths


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
        _FAILURES.append(f"{label}\n          expected: {expected}\n"
                         f"          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


def check_true(label, condition):
    check(label, bool(condition), True)


def section(title):
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def guarded(fn, *args, **kwargs):
    """Call `fn` and convert a raise into a value `check` can fail on.

    A CONTROL THAT ABORTS IS NOT A CONTROL. This project has shipped that shape
    a dozen times: a defect makes the code under test raise, the raise escapes
    while `check()`'s argument is being evaluated, and the run reports one
    traceback where it owed a summary and every remaining result. Every call
    below that a defect could make raise goes through here.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:                                   # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


def at(seq, index, what):
    """`seq[index]` without `IndexError`. Same rule as `guarded`."""
    try:
        return seq[index]
    except Exception:                                          # noqa: BLE001
        return f"<MISSING {what}: only {len(seq)} item(s)>"


#------------------------------------------------------------------------------


# ===========================================================================
# ISOLATION: A SCRATCH TREE, AND paths._RESOLVED SEEDED SO NOTHING CAN ESCAPE
# ===========================================================================
#
# The study module resolves its production database through `paths` on the
# default branch. Every call below passes an explicit `db_path`, which outranks
# it -- but "the argument outranks the default" is exactly the kind of thing a
# defect breaks, so the default is ALSO pointed somewhere harmless. Seeding
# `paths._RESOLVED` is the seam `tests/test_ablation_db_isolation.py` already
# uses; it is restored at the end and the restoration is asserted.

_TMP = tempfile.mkdtemp(prefix="oncotriage-latest-run-")
_SCRATCH_RESULTS = os.path.join(_TMP, "results")
_SCRATCH_CHECKPOINT = os.path.join(_TMP, "checkpoint")
os.makedirs(_SCRATCH_RESULTS, exist_ok=True)
os.makedirs(_SCRATCH_CHECKPOINT, exist_ok=True)

_SAVED_RESOLVED = dict(paths._RESOLVED)
paths._RESOLVED["result_ablation_path"] = _SCRATCH_RESULTS + os.sep
paths._RESOLVED["checkpoint_path"] = _SCRATCH_CHECKPOINT + os.sep

from oncotriage.ablation import study                          # noqa: E402

_STUDY_PY = os.path.abspath(study.__file__)
_STUDY_SHA_BEFORE = hashlib.sha256(
    open(_STUDY_PY, "rb").read()).hexdigest()


#------------------------------------------------------------------------------


# ===========================================================================
# THE PRE-FIX PREDICATE, WRITTEN OUT ONCE
# ===========================================================================
#
# This is the SELECT both readers carried before the fix, reproduced verbatim
# from the shape recorded in `_LATEST_RUN_PER_CONFIG_SQL`'s docstring. It is a
# fragment of a WHERE clause rather than a standalone SELECT, because that is
# what it was: `(config_name, run_timestamp) IN (...)`.

_PRE_FIX_LATEST_SQL = """
            SELECT config_name, id AS run_id
            FROM ablation_runs
            WHERE (config_name, run_timestamp) IN (
                SELECT config_name, MAX(run_timestamp)
                FROM ablation_runs
                GROUP BY config_name
            )
"""


def _status_rows(conn, latest_sql):
    """`_summary_status_warning`'s query, over whichever predicate is handed in."""
    return conn.execute(f"""
        SELECT r.config_name, r.status
        FROM ablation_runs r
        INNER JOIN ({latest_sql}) latest
                ON r.id = latest.run_id
        ORDER BY r.config_name
    """).fetchall()


def _summary_rows(conn, latest_sql):
    """`generate_summary`'s join, reduced to the two facts these sections read.

    The shipped query computes eighteen aggregates; reproducing all of them here
    would be a second copy of `generate_summary` to keep in step. What decides
    the defect is WHICH RESULT ROWS THE JOIN ADMITS, so this asks for the count
    and one mean -- and section 4 separately drives the SHIPPED `generate_summary`
    end to end, so nothing rests on this reduction alone.
    """
    return conn.execute(f"""
        SELECT r.config_name,
               COUNT(*)                       AS n,
               ROUND(AVG(r.eligible_count), 3) AS avg_eligible
        FROM ablation_results r
        INNER JOIN ({latest_sql}) latest
                ON r.config_name = latest.config_name
               AND r.run_id      = latest.run_id
        GROUP BY r.config_name
        ORDER BY r.config_name
    """).fetchall()


def _new_db(name):
    """A fresh database with the SHIPPED schema. Returns its path."""
    path = os.path.join(_TMP, name)
    study.init_ablation_db(db_path=path)
    return path


def _insert_run(conn, timestamp, config_name, status):
    conn.execute(
        "INSERT INTO ablation_runs (run_timestamp, config_name, "
        "config_description, sample_size, status) VALUES (?, ?, ?, ?, ?)",
        (timestamp, config_name, "d", 3, status))
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def _insert_results(conn, run_id, config_name, eligible_counts):
    for i, eligible in enumerate(eligible_counts):
        conn.execute(
            "INSERT INTO ablation_results (run_id, config_name, patient_id, "
            "eligible_count) VALUES (?, ?, ?, ?)",
            (run_id, config_name, f"p{i}", eligible))


#------------------------------------------------------------------------------


# ===========================================================================
# 1.  THE OWNER EXISTS, IS THE SHAPE IT CLAIMS, AND IS NOT A TIMESTAMP QUERY
# ===========================================================================

section("1. _LATEST_RUN_PER_CONFIG_SQL: the one owner")

_OWNER = getattr(study, "_LATEST_RUN_PER_CONFIG_SQL", None)
check_true("1a  _LATEST_RUN_PER_CONFIG_SQL exists", _OWNER is not None)
check_true("1b  it is a string", isinstance(_OWNER, str))
_owner_text = (_OWNER or "")
check_true("1c  it selects MAX(id)", "MAX(id)" in _owner_text)
check_true("1d  it does NOT select MAX(run_timestamp)",
           "MAX(run_timestamp)" not in _owner_text)
check_true("1e  it groups by config_name", "GROUP BY config_name" in _owner_text)
check_true("1f  it exposes the column both readers join on (run_id)",
           "AS run_id" in _owner_text)

# NON-DEGENERACY. Every assertion above is satisfied by the empty string except
# 1a/1b, and 1d is satisfied by ANY string that does not contain the phrase --
# including one that selects nothing at all. So the owner is RUN.
_probe_db = _new_db("probe.db")
with sqlite3.connect(_probe_db) as _c:
    _r1 = _insert_run(_c, "2026-01-01T00:00:00", "alpha", study.RUN_STATUS_COMPLETE)
    _r2 = _insert_run(_c, "2026-01-01T00:00:00", "beta", study.RUN_STATUS_COMPLETE)
    _probe = guarded(lambda: _c.execute(
        f"SELECT config_name, run_id FROM ({_OWNER}) ORDER BY config_name"
    ).fetchall())
check("1g  the owner RUNS and returns one row per config (non-degeneracy)",
      _probe, [("alpha", _r1), ("beta", _r2)])


#------------------------------------------------------------------------------


# ===========================================================================
# 2.  THE EXACT TIE
# ===========================================================================

section("2. Two runs of one config sharing a run_timestamp")

_TIE_TS = "2026-03-01T12:00:00.000000"
_tie_db = _new_db("tie.db")
with sqlite3.connect(_tie_db) as _c:
    # The SUPERSEDED run: complete, and its patients matched a lot.
    _old_id = _insert_run(_c, _TIE_TS, "full_pipeline", study.RUN_STATUS_COMPLETE)
    _insert_results(_c, _old_id, "full_pipeline", [10, 10, 10])
    # The LATEST run: killed after one pair, and that pair matched nothing.
    _new_id = _insert_run(_c, _TIE_TS, "full_pipeline", study.RUN_STATUS_KILLED)
    _insert_results(_c, _new_id, "full_pipeline", [0])

check_true("2a  the two runs really do share a timestamp (non-degeneracy)",
           _old_id != _new_id)

with sqlite3.connect(_tie_db) as _c:
    _pre_status = guarded(_status_rows, _c, _PRE_FIX_LATEST_SQL)
    _pre_summary = guarded(_summary_rows, _c, _PRE_FIX_LATEST_SQL)
    _fix_status = guarded(_status_rows, _c, study._LATEST_RUN_PER_CONFIG_SQL)
    _fix_summary = guarded(_summary_rows, _c, study._LATEST_RUN_PER_CONFIG_SQL)

# --- the defect, reproduced -------------------------------------------------
check("2b  PRE-FIX: the status reader reports the config TWICE",
      _pre_status,
      [("full_pipeline", study.RUN_STATUS_COMPLETE),
       ("full_pipeline", study.RUN_STATUS_KILLED)])
check("2c  PRE-FIX: the summary pools BOTH runs' results (n=4, not 1)",
      _pre_summary, [("full_pipeline", 4, 7.5)])
check_true("2d  PRE-FIX: the pooled mean belongs to NEITHER run "
           "(7.5 is not 10.0 and not 0.0)",
           at(at(_pre_summary, 0, "row"), 2, "mean") == 7.5)

# --- the fix ----------------------------------------------------------------
check("2e  FIXED: exactly one row per config, and it is the LATEST run's status",
      _fix_status, [("full_pipeline", study.RUN_STATUS_KILLED)])
check("2f  FIXED: only the latest run's results are averaged (n=1, mean 0.0)",
      _fix_summary, [("full_pipeline", 1, 0.0)])


#------------------------------------------------------------------------------


# ===========================================================================
# 3.  THE DST FALL-BACK
# ===========================================================================

section("3. Local time is not monotone across a DST fall-back")

# A REAL US/EASTERN FALL-BACK, WRITTEN OUT RATHER THAN COMPUTED. `datetime.now()`
# is naive, so what lands in the column is the WALL CLOCK with no offset: 01:30
# happens twice, an hour apart in real time, and the SECOND occurrence sorts
# EQUAL to the first as a string -- so the discriminating pair is the one that
# straddles the repeat. 01:45 EDT is real-time EARLIER than 01:15 EST, and
# "01:45" > "01:15" lexicographically, so MAX picks the earlier run.
_DST_EARLIER_REAL_TIME = "2026-11-01T01:45:00.000000"   # EDT, before the repeat
_DST_LATER_REAL_TIME   = "2026-11-01T01:15:00.000000"   # EST, after the repeat

check_true("3a  the later run's timestamp really does sort BELOW the earlier "
           "one (non-degeneracy)",
           _DST_LATER_REAL_TIME < _DST_EARLIER_REAL_TIME)

_dst_db = _new_db("dst.db")
with sqlite3.connect(_dst_db) as _c:
    _superseded = _insert_run(_c, _DST_EARLIER_REAL_TIME, "no_mesh_filter",
                              study.RUN_STATUS_COMPLETE)
    _insert_results(_c, _superseded, "no_mesh_filter", [9, 9])
    _genuine_latest = _insert_run(_c, _DST_LATER_REAL_TIME, "no_mesh_filter",
                                  study.RUN_STATUS_STOPPED)
    _insert_results(_c, _genuine_latest, "no_mesh_filter", [1])

check_true("3b  the genuinely-later run has the higher id (non-degeneracy)",
           _genuine_latest > _superseded)

with sqlite3.connect(_dst_db) as _c:
    _pre_status = guarded(_status_rows, _c, _PRE_FIX_LATEST_SQL)
    _pre_summary = guarded(_summary_rows, _c, _PRE_FIX_LATEST_SQL)
    _fix_status = guarded(_status_rows, _c, study._LATEST_RUN_PER_CONFIG_SQL)
    _fix_summary = guarded(_summary_rows, _c, study._LATEST_RUN_PER_CONFIG_SQL)

# --- the defect, reproduced -------------------------------------------------
check("3c  PRE-FIX: reports the SUPERSEDED run's status (COMPLETE), so the "
      "stopped run is never qualified",
      _pre_status, [("no_mesh_filter", study.RUN_STATUS_COMPLETE)])
check("3d  PRE-FIX: averages the SUPERSEDED run's results",
      _pre_summary, [("no_mesh_filter", 2, 9.0)])

# --- the fix ----------------------------------------------------------------
check("3e  FIXED: reports the genuinely-latest run's status (STOPPED)",
      _fix_status, [("no_mesh_filter", study.RUN_STATUS_STOPPED)])
check("3f  FIXED: averages the genuinely-latest run's results",
      _fix_summary, [("no_mesh_filter", 1, 1.0)])


#------------------------------------------------------------------------------


# ===========================================================================
# 4.  ONE OWNER: BOTH SHIPPED READERS INTERPOLATE IT, AND NEITHER RESTATES IT
# ===========================================================================
#
# Sections 2 and 3 prove the OWNER behaves. They cannot prove the shipped
# READERS use it -- a `generate_summary` that still carried its own copy would
# pass every one of them. This section reads the shipped source.

section("4. Both readers interpolate the one owner")

_src = open(_STUDY_PY, encoding="utf-8").read()
_tree = ast.parse(_src)
_funcs = {n.name: n for n in ast.walk(_tree)
          if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _body_text(name):
    node = _funcs.get(name)
    if node is None:
        return f"<NO SUCH FUNCTION: {name}>"
    return ast.unparse(node)


def _interpolates_owner(name):
    """Does this function's body contain a JoinedStr naming the owner?

    Asked structurally rather than by substring, because the owner's NAME
    appears in this module's prose too -- and a check satisfied by a comment is
    the defect the Docker pass had to remove ("a file that argues about its own
    settings cannot be grepped for them").
    """
    node = _funcs.get(name)
    if node is None:
        return False
    for sub in ast.walk(node):
        if isinstance(sub, ast.JoinedStr):
            for value in sub.values:
                if isinstance(value, ast.FormattedValue):
                    for leaf in ast.walk(value):
                        if (isinstance(leaf, ast.Name)
                                and leaf.id == "_LATEST_RUN_PER_CONFIG_SQL"):
                            return True
    return False


for _name in ("_summary_status_warning", "generate_summary"):
    check_true(f"4a  {_name} interpolates _LATEST_RUN_PER_CONFIG_SQL",
               _interpolates_owner(_name))
    _text = _body_text(_name)
    check_true(f"4b  {_name} contains no MAX(run_timestamp) of its own",
               "MAX(run_timestamp)" not in _text)

# NON-DEGENERACY FOR 4a. A walker that finds nothing returns False for
# everything, so it is shown to return False for a function that genuinely does
# not interpolate the owner.
check("4c  the interpolation walk returns False for a function that does not "
      "interpolate it (non-degeneracy)",
      _interpolates_owner("_create_run"), False)

# NON-DEGENERACY FOR 4b. The phrase must be findable at all by this method.
check_true("4d  MAX(run_timestamp) is findable in a body that has it "
           "(non-degeneracy)",
           "MAX(run_timestamp)" in ast.unparse(
               ast.parse("def f():\n    return 'MAX(run_timestamp)'")))

# THE OWNER IS DECLARED EXACTLY ONCE.
_assigns = [n for n in _tree.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name)
                    and t.id == "_LATEST_RUN_PER_CONFIG_SQL" for t in n.targets)]
check("4e  _LATEST_RUN_PER_CONFIG_SQL is assigned exactly once at module scope",
      len(_assigns), 1)

# AND NO OTHER PART OF THE MODULE CARRIES ONE -- IN CODE. The phrase is
# deliberately still in PROSE: `_LATEST_RUN_PER_CONFIG_SQL`'s own docstring
# quotes the predicate it replaced, twice, because that is what the correction
# is about. So the scan must strip documentation first.
#
# THE FIRST VERSION OF THIS CHECK DID NOT, AND FAILED AGAINST CORRECT CODE --
# reporting the argument as the thing it argues against, which is the trap 4a's
# own comment names, met one check later. `ast.unparse` does NOT strip
# docstrings; it renders them, because they are ordinary `Expr(Constant(str))`
# statements. Recorded rather than quietly corrected: it is how this class of
# check fails, and it failed here for a SECOND reason the obvious stripper would
# not have caught either -- the owner's docstring is an ATTRIBUTE docstring, a
# bare string statement FOLLOWING an assignment, so a stripper that only removes
# `body[0]` of Module/FunctionDef/ClassDef leaves it in place.
#
# The rule below is therefore the general one: an `Expr` whose value is a string
# constant is documentation, wherever it sits, and is never code.

def _strip_docs(node):
    """Remove every bare string-expression statement, at any depth."""
    for sub in ast.walk(node):
        body = getattr(sub, "body", None)
        if isinstance(body, list):
            sub.body = [s for s in body
                        if not (isinstance(s, ast.Expr)
                                and isinstance(s.value, ast.Constant)
                                and isinstance(s.value.value, str))]
    return node


_code_only = ast.unparse(_strip_docs(ast.parse(_src)))
check("4f  the whole module contains no MAX(run_timestamp) in CODE",
      _code_only.count("MAX(run_timestamp)"), 0)

# NON-DEGENERACY, BOTH DIRECTIONS. Without the first, 4f is satisfied by a
# stripper that removed everything; without the second, by one that removes only
# the docstrings a naive stripper already handles -- which is precisely the
# version that failed here.
check_true("4g  the stripper leaves real code behind (non-degeneracy)",
           "MAX(id)" in _code_only)

_ATTR_DOC_CONTROL = "\n".join([
    "X = 1",
    '"MAX(run_timestamp)"',
    "",
])
check("4h  the stripper removes an ATTRIBUTE docstring -- the case the first "
      "version of 4f missed (non-degeneracy)",
      "MAX(run_timestamp)" in ast.unparse(
          _strip_docs(ast.parse(_ATTR_DOC_CONTROL))),
      False)


#------------------------------------------------------------------------------


# ===========================================================================
# 5.  WHY id IS THE RIGHT KEY: UNIQUE, AND MONOTONE IN INSERT ORDER
# ===========================================================================
#
# Against a database built by the SHIPPED `init_ablation_db` and the SHIPPED
# `_create_run`, not against a schema retyped here -- so a future change to the
# column's declaration fails this rather than agreeing with a copy.

section("5. ablation_runs.id is unique and monotone")

_id_db = _new_db("ids.db")
_ids = [study._create_run("cfg", "d", 3, db_path=_id_db) for _ in range(5)]
check("5a  five _create_run calls give five distinct ids", len(set(_ids)), 5)
check("5b  and they ascend in insert order", _ids, sorted(_ids))

with sqlite3.connect(_id_db) as _c:
    _decl = _c.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name='ablation_runs'").fetchone()[0]
check_true("5c  the shipped schema declares id INTEGER PRIMARY KEY AUTOINCREMENT",
           "INTEGER PRIMARY KEY AUTOINCREMENT" in " ".join(_decl.split()))

# MAX(id) CAN ONLY EVER SELECT ONE ROW PER CONFIG -- which is the property the
# tie in section 2 shows MAX(run_timestamp) does not have. Driven, not argued.
with sqlite3.connect(_id_db) as _c:
    _rows = guarded(lambda: _c.execute(
        f"SELECT config_name, COUNT(*) FROM ({study._LATEST_RUN_PER_CONFIG_SQL}) "
        f"GROUP BY config_name").fetchall())
check("5d  MAX(id) selects exactly one row for a config with five runs",
      _rows, [("cfg", 1)])


#------------------------------------------------------------------------------


# ===========================================================================
# 6.  THE SHIPPED generate_summary AND _summary_status_warning, END TO END
# ===========================================================================
#
# Sections 2-3 drive REDUCED queries; this drives the real functions, so nothing
# rests on the reduction. The tie database is the input, because it is the case
# where the two answers differ most: n=4 pooled versus n=1.

section("6. The shipped functions on the tie database")

_buf = io.StringIO()
with contextlib.redirect_stderr(_buf):
    _df = guarded(study.generate_summary, db_path=_tie_db)

check_true("6a  generate_summary returned a frame", hasattr(_df, "columns"))
if hasattr(_df, "columns"):
    check("6b  it reports ONE row for the one configuration", len(_df), 1)
    check("6c  n is the latest run's sample size (1), not the pooled 4",
          int(_df.iloc[0]["n"]), 1)
    check("6d  avg_eligible is the latest run's 0.0, not the pooled 7.5",
          float(_df.iloc[0]["avg_eligible"]), 0.0)
else:
    check("6b  it reports ONE row for the one configuration", _df, "<a frame>")
    check("6c  n is the latest run's sample size", _df, "<a frame>")
    check("6d  avg_eligible is the latest run's", _df, "<a frame>")

with sqlite3.connect(_tie_db) as _c:
    _lines = guarded(study._summary_status_warning, _c)
_joined = "\n".join(_lines) if isinstance(_lines, list) else str(_lines)
check("6e  the qualification names the configuration exactly once",
      _joined.count("full_pipeline"), 1)
check_true("6f  and it names the KILLED status it read from the latest run",
           study.RUN_STATUS_KILLED in _joined)

# NON-DEGENERACY: on a database whose only run is COMPLETE, the qualification is
# EMPTY. Without this, 6e/6f would also pass against a function that returned a
# fixed banner for every input.
_clean_db = _new_db("clean.db")
with sqlite3.connect(_clean_db) as _c:
    _cid = _insert_run(_c, "2026-05-05T05:05:05", "full_pipeline",
                       study.RUN_STATUS_COMPLETE)
    _insert_results(_c, _cid, "full_pipeline", [3, 3])
with sqlite3.connect(_clean_db) as _c:
    _clean_lines = guarded(study._summary_status_warning, _c)
check("6g  a clean database produces NO qualification at all (non-degeneracy)",
      _clean_lines, [])


#------------------------------------------------------------------------------


# ===========================================================================
# 7.  ISOLATION, AND THE FILE THIS TEST READ
# ===========================================================================

section("7. Isolation")

_sha_after = hashlib.sha256(open(_STUDY_PY, "rb").read()).hexdigest()
check("7a  oncotriage/ablation/study.py is byte-unchanged",
      _sha_after, _STUDY_SHA_BEFORE)

_prod = os.path.join(_SCRATCH_RESULTS, "ablation_results.db")
check_true("7b  every database this test made is inside the temp tree",
           all(p.startswith(_TMP) for p in
               (_tie_db, _dst_db, _id_db, _probe_db, _clean_db)))
check_true("7c  the seeded results root is inside the temp tree too, so even "
           "the DEFAULT path could not reach production",
           str(paths._RESOLVED.get("result_ablation_path", "")).startswith(_TMP))

paths._RESOLVED.clear()
paths._RESOLVED.update(_SAVED_RESOLVED)
check("7d  paths._RESOLVED restored exactly", dict(paths._RESOLVED),
      _SAVED_RESOLVED)

shutil.rmtree(_TMP, ignore_errors=True)
check("7e  the temp tree is gone", os.path.exists(_TMP), False)


#------------------------------------------------------------------------------


print(f"\n{'=' * 74}")
print(f"  {_RESULTS['passed']} passed, {_RESULTS['failed']} failed")
print(f"{'=' * 74}")
if _FAILURES:
    print("\nFAILURES:")
    for _f in _FAILURES:
        print(f"  - {_f}")
sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
