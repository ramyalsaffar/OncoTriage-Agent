"""
Dashboard data loaders, and the schema preflight that runs before them.

The three ``@st.cache_data(ttl=60)`` readers of ``inferences.db`` at the top of
this module were moved verbatim out of "21- Streamlit Dashboard.py" in pass
20c-3c-1. Seven more have joined them since -- the four run-table readers, the
drift designation reader and ``dashboard_schema_readiness`` -- and each is
argued where it sits.

THE DATABASE PATH IS READ THROUGH ``paths``, NOT IMPORTED BY NAME. Writing
``from oncotriage.paths import inferences_path`` at module scope would be an
ATTRIBUTE read, which fires the lazy resolver in ``oncotriage/paths.py`` and
globs the whole sibling data tree at IMPORT time -- the exact hole pass 20c-2c
found in ``oncotriage/registries/mesh.py``. Reading ``paths.inferences_path``
inside each function body resolves it on first CALL instead, which is when the
dashboard actually wants the database.

THESE THREE ARE NOT MERGED INTO ``oncotriage/storage/queries.py`` AND THAT IS
DELIBERATE. Consolidating the query layer is its own item; mixing a relocation
with a redesign is what makes an equivalence proof stop meaning anything. They
are moved exactly as they were, SQL included.

THE RUN LOADERS (the run-reader pass) ARE THE OTHER WAY ROUND, AND SO IS THEIR
CONNECTION
--------------------------------------------------------------------------
The four loaders added for the Run Health tab carry NO SQL OF THEIR OWN. They
call ``oncotriage.storage.queries.run(conn, key)``. The paragraph above is about
not MOVING three functions whose SQL already existed; it is not a licence to
write a fourth copy of a question the query layer already owns. The cost tab
already established the direction -- it reaches ``price_model_groups`` rather
than carrying the arithmetic -- and the duplication File 16's own docstring
predicted is what this avoids.

THEY OPEN THE DATABASE READ-ONLY, and the three above deliberately do not
change. ``sqlite3.connect(path)`` on a path that does not exist CREATES an empty
database; a ``file:...?mode=ro`` URI reports it instead. That matters more here
than above because the Run Health tab's whole subject is "what does this
database have", and a loader that answered by bringing a database into existence
would be File 41's guard-that-creates-its-own-evidence defect. The three
original loaders keep ``sqlite3.connect`` because changing them is a behaviour
change to eight tabs in a pass that owes one, and because ``main()`` has already
returned before they could matter.
"""

import ast
import os
import re
import sqlite3
import threading
from pathlib import Path

import pandas as pd
import streamlit as st

from oncotriage import paths
from oncotriage.storage import database_logger as _database_logger
from oncotriage.storage import queries
from oncotriage.storage.database_logger import SCHEMA_USER_VERSION


@st.cache_data(ttl=60)
def load_inferences_data():
    """
    Load all inference data from SQLite. Cached for 60 seconds.
    
    Returns empty DataFrame on error to allow Streamlit to handle gracefully.
    """
    conn = None
    try:
        conn = sqlite3.connect(paths.inferences_path)
        df = pd.read_sql_query("SELECT * FROM inferences", conn)
        
        if df.empty:
            return pd.DataFrame()  # Return empty DataFrame, not None
            
        # Convert timestamp to datetime
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        return df
        
    except Exception as e:
        st.error(f"Database error: {e}")
        return pd.DataFrame()  # Return empty DataFrame, not None
    
    finally:
        if conn:
            conn.close()


@st.cache_data(ttl=60)
def load_trial_matches_data():
    """Load trial matches from SQLite. Cached for 60 seconds."""
    conn = None
    try:
        conn = sqlite3.connect(paths.inferences_path)
        df = pd.read_sql_query("SELECT * FROM trial_matches", conn)
        
        if df.empty:
            return pd.DataFrame()
            
        return df
        
    except Exception as e:
        st.error(f"Database error: {e}")
        return pd.DataFrame()
    
    finally:
        if conn:
            conn.close()


@st.cache_data(ttl=60)
def load_drift_metrics_data():
    """Load drift metrics from SQLite. Cached for 60 seconds."""
    conn = None
    try:
        conn = sqlite3.connect(paths.inferences_path)
        df = pd.read_sql_query("SELECT * FROM drift_metrics ORDER BY timestamp DESC", conn)
        
        if df.empty:
            return pd.DataFrame()
            
        # Convert timestamp to datetime
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        return df
        
    except Exception as e:
        st.error(f"Drift metrics error: {e}")
        return pd.DataFrame()
    
    finally:
        if conn:
            conn.close()


#------------------------------------------------------------------------------


# ===========================================================================
# THE RUN TABLES (the run-reader pass)
# ===========================================================================


RUN_TRACKING_ABSENT = "absent"
"""``availability`` when the database has neither run table.

A MEASURED STATE, NOT AN ERROR, and the distinction is the whole reason this is
a named value rather than an exception. ``runs`` and ``run_metrics`` are created
by ``initialize_database``, so a database last written before the run-identity
pass simply does not have them yet and the next writer to open it adds them.
Nothing is wrong with the rows that ARE there."""

RUN_TRACKING_PARTIAL = "partial"
"""``availability`` when the run schema is there in pieces.

TWO CAUSES, AND THEY ARE NOT THE SAME FINDING. The tab names both because the
remedy differs and this loader cannot always tell them apart:

    AN ERA GAP. Every missing item is a COLUMN. ``runs.matching_call_mode`` and
    ``runs.resumed`` are ADDITIVE -- ``initialize_database`` adds them through
    ``RUN_COLUMN_ADDITIONS`` -- so a database last written between the
    run-identity pass and the call-mode pass has both run tables and lacks the
    column, which is an ordinary, blameless state whose remedy is exactly
    ``absent``'s: let a writer open the file.

    A SHAPE THE PIPELINE CANNOT PRODUCE. One run TABLE without the other.
    ``initialize_database`` creates both in one call, so this one wants a
    person.

THAT SECOND SENTENCE USED TO BE THE WHOLE DOCSTRING, and it was a false
statement about the first case: it told an operator with a perfectly ordinary
era-3 database that something had edited it. The era-3 case was not reachable
when it was written -- ``matching_call_mode`` did not exist -- and it became
reachable without anything here noticing, which is why the requirement is now
DERIVED from the queries' own declarations rather than hand-listed.

WHY THE COLUMN CASE IS NOT ``present``, MEASURED RATHER THAN ARGUED. With the
tables present and an additive column gone, ``queries.missing_requirements``
refuses the queries that name it and ``queries.run`` raises ``MissingTableError``
-- so this loader reporting ``present`` sends the tab down its normal path,
where ``_load_run_query`` catches the raise, calls ``st.error`` and hands back
an empty frame, and the tab then prints "the run tables are present and hold no
rows". That is a statement about a pipeline that has not run, made about a
database whose queries could not be asked, printed underneath the error that
says so."""

RUN_TRACKING_PRESENT = "present"
"""``availability`` when both run tables are there. Says nothing about rows."""

RUN_TRACKING_NO_DATABASE = "no_database"
"""``availability`` when the file itself is not there.

Reachable only through a misconfigured path: ``main()`` returns before any tab
renders when ``load_inferences_data()`` comes back empty, which it does when the
file is missing. Named anyway rather than left to fall through to ``absent``,
because "this database has not run the pipeline yet" and "this is not a
database" send an operator to different places."""

RUN_TRACKING_STATES = (RUN_TRACKING_NO_DATABASE, RUN_TRACKING_ABSENT,
                       RUN_TRACKING_PARTIAL, RUN_TRACKING_PRESENT)
"""Every value ``availability`` can take. CLOSED, on
``oncotriage.storage.queries.RUN_HEALTH_STATES``' footing: the tab branches on it
exhaustively and an unlisted value would fall through every branch and render
nothing."""

# ===========================================================================
# WHICH QUERIES THE RUN HEALTH TAB ASKS -- ONE OWNER PER KEY
# ===========================================================================
#
# THE AVAILABILITY LOADER DERIVES ITS REQUIREMENTS FROM THESE, and that is the
# whole of the era-3 fix. It used to hand-name ONE column, ``inferences.run_id``,
# because that was the only additive column the run queries touched on the day
# it was written. ``runs.resumed`` and then ``runs.matching_call_mode`` arrived
# afterwards, each declared on ``queries.run_summary`` and
# ``queries.campaign_summary`` -- and nothing here noticed either time. A
# database written between the run-identity pass and the call-mode pass
# therefore reported ``present`` and produced a query error inside the tab.
#
# A HAND-WRITTEN COLUMN LIST HERE WOULD BE A SECOND DECLARATION OF A FACT THE
# Query RECORDS ALREADY CARRY, and it would go stale the same way on the next
# additive column. ``queries.missing_requirements`` is the one owner of "can
# this database answer this query", it is what ``report()`` and
# ``queries.run`` already use, and it applies the rule this loader would
# otherwise have to repeat -- a column on an absent table is reported ONCE, as
# the table, because naming both tells an operator to add a column to a table
# that is not there.
#
# THE KEYS ARE NAMED CONSTANTS RATHER THAN LITERALS AT THE FOUR LOADERS,
# because the derivation and the loaders must ask about the SAME four queries.
# Two copies of that list is how a fifth run query joins the tab without
# joining its availability check.
RUN_SUMMARY_QUERY = "run_summary"
RUN_DEGRADATION_QUERY = "run_degradation_breakdown"
RUN_CAMPAIGN_QUERY = "campaign_summary"
RUN_ATTRIBUTION_QUERY = "run_attribution_coverage"

RUN_TAB_QUERY_KEYS = (RUN_SUMMARY_QUERY, RUN_DEGRADATION_QUERY,
                      RUN_CAMPAIGN_QUERY, RUN_ATTRIBUTION_QUERY)
"""Every registered query the Run Health tab runs. CLOSED.

A key here that the registry does not carry raises at IMPORT (below) rather
than at the first render, where it would arrive as a caught exception and an
empty frame -- which is the shape this whole section exists to remove."""

# A RuntimeError AT IMPORT, NOT AN ``assert``: ``python -O`` deletes asserts,
# and this project's other import-time vocabulary guards (RESUME_SKIP_STATUSES,
# TRACKING_STATUS_FOR) are written the same way for the same reason.
_unknown_run_keys = tuple(k for k in RUN_TAB_QUERY_KEYS
                          if k not in queries.QUERIES_BY_KEY)
if _unknown_run_keys:                                    # pragma: no cover
    raise RuntimeError(
        f"RUN_TAB_QUERY_KEYS names {_unknown_run_keys}, which "
        f"oncotriage.storage.queries does not register. The Run Health tab "
        f"would fail at render time with a caught exception and an empty "
        f"frame; failing here names the key instead."
    )
del _unknown_run_keys



# ===========================================================================
# THE SCHEMA PREFLIGHT (the dashboard-fixes pass)
# ===========================================================================
#
# WHAT IT IS FOR, MEASURED RATHER THAN ARGUED. The production ``inferences.db``
# on this machine is SCHEMA ERA 0 -- ``PRAGMA user_version`` 0,
# ``application_id`` 0, 66 columns on ``inferences``, and the pre-naming-pass
# ``gpt4o_*`` spellings where the current schema has ``llm_classifier_*``.
# Driven through ``main()`` against a synthetic database of exactly that shape,
# the dashboard raised
#
#     KeyError: 'llm_classifier_evaluation_time'
#
# at ``tabs/overview.py``'s stage-timing chart, INSIDE ``main()``, which has no
# handler -- so the whole page, all ten tabs, rendered one traceback and no
# diagnosis. A reader is told a column name and nothing about what to do.
#
# WHY A PREFLIGHT AND NOT A try/except ROUND EACH TAB. A caught KeyError names
# whichever column the first tab happened to reach; it says nothing about the
# other twenty a database of that era is also missing, and the next release
# moves which one is first. The question a reader needs answered is "is this
# database one this dashboard was written against", which is answerable ONCE,
# before anything renders, and which names EVERY missing thing at the same time.
#
# THE VERSION STAMP IS REPORTED AND NEVER THE GATE, AND THAT IS THE HALF THAT
# IS EASY TO GET WRONG. ``PRAGMA user_version`` is written by
# ``initialize_database``; a database carrying the current number and missing a
# column -- a hand-built one, a partially-restored one, one whose migration was
# interrupted between the ALTER and the stamp -- would PASS a version-only gate
# and then raise the identical KeyError one tab later. So the columns are
# checked independently of the stamp, and the stamp is carried into the
# diagnosis as evidence rather than consulted as a verdict.
#
# THE REQUIREMENT IS DERIVED, NOT DECLARED. A hand-written column list here
# would be a second statement of what the dashboard reads, and it would rot in
# the one direction that matters: a tab that starts reading a new column
# silently stops being covered. So the requirement is the INTERSECTION of two
# things neither of which this module writes down --
#
#     what the dashboard NAMES  : every string constant in oncotriage/dashboard,
#                                 docstrings excluded, collected by AST;
#     what the schema HAS       : the column set ``initialize_database`` itself
#                                 creates, read off a throwaway database the
#                                 schema owner builds.
#
# -- so neither side can drift from its own source. The intersection
# over-approximates on the naming side (a string constant that happens to spell
# a column the code does not index is required anyway) and that is the SAFE
# direction: it can only widen a diagnosis about a database that is already
# behind, and it can never let a column a tab really reads go unchecked.


SCHEMA_NO_DATABASE = "no_database"
"""No file at the configured path. Not a schema finding at all."""

SCHEMA_UNREADABLE = "unreadable"
"""The file is there and could not be interrogated -- not a database, locked,
or a permission fault. REPORTED, never folded into ``no_database``: "there is
nothing here" and "there is something here I cannot read" send an operator to
different places."""

SCHEMA_TABLES_MISSING = "tables_missing"
"""A table the dashboard loads unconditionally is absent."""

SCHEMA_COLUMNS_MISSING = "columns_missing"
"""Every required table is there and at least one required column is not.

This is the ERA GAP, and it is the state the production database is in. It is
its own member rather than a shade of ``tables_missing`` because the remedy
differs: a missing table can be created by the next writer to open the file,
and a column the schema RENAMED cannot -- ``gpt4o_evaluation_time`` does not
become ``llm_classifier_evaluation_time`` by running the pipeline again."""

SCHEMA_READY = "ready"
"""Every required table and column is present. SAYS NOTHING ABOUT ROWS -- an
empty, freshly-initialized, current-era database is READY, and ``main()``'s own
"no data available" message is what covers it. Collapsing the two would make a
database that has simply not been run yet indistinguishable from one the
dashboard cannot read."""

SCHEMA_STATES = (SCHEMA_NO_DATABASE, SCHEMA_UNREADABLE, SCHEMA_TABLES_MISSING,
                 SCHEMA_COLUMNS_MISSING, SCHEMA_READY)
"""Every value ``state`` can take. CLOSED, on ``RUN_TRACKING_STATES``' footing:
``main()`` branches on it and an unlisted value would fall through every branch
and render nothing -- which is the silent-blank-page shape this preflight
exists to remove."""

DASHBOARD_TABLES = ("inferences", "trial_matches")
"""The tables ``main()`` loads before any tab renders, so their absence is fatal
to the page rather than to one panel.

``drift_metrics`` IS DELIBERATELY NOT HERE. The drift tab already answers for
its own absence in its own words -- ``load_drift_metrics_data`` returns an
empty frame and the tab renders a "how to enable drift detection" panel -- and
promoting it here would refuse the whole dashboard over a table whose absence
one tab handles correctly. ``runs`` and ``run_metrics`` likewise: that is what
``load_run_tracking_availability`` above is for."""


_SCHEMA_CACHE = {}
_SCHEMA_CACHE_LOCK = threading.RLock()


# The column-list line shapes `initialize_database`'s CREATE TABLE statements
# use, so a constraint clause is not mistaken for a column. Not a complete SQL
# grammar and does not need to be: what this must never do is INVENT a column
# name, and every rejection here can only narrow the parse -- which
# `_reference_schema` reports rather than absorbs.
_NOT_A_COLUMN = frozenset({
    "primary", "foreign", "unique", "check", "constraint", "key",
})

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*$",
    re.IGNORECASE | re.DOTALL)


def _columns_of_create_table(statement):
    """``(table, [columns])`` for one ``CREATE TABLE`` statement, or ``None``.

    The column name is the first token of each top-level comma-separated item,
    with table-level constraint clauses rejected by keyword.
    """
    match = _CREATE_TABLE_RE.search(statement.strip())
    if not match:
        return None
    table, body = match.group(1), match.group(2)
    columns, depth, item = [], 0, []
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            columns.append("".join(item))
            item = []
        else:
            item.append(char)
    columns.append("".join(item))
    names = []
    for column in columns:
        tokens = column.strip().split()
        if not tokens:
            continue
        first = tokens[0].strip('"`[]')
        if first.lower() in _NOT_A_COLUMN:
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", first):
            names.append(first)
    return table, names


def _reference_schema():
    """``{table: frozenset(columns)}`` for the schema THIS BUILD creates.

    DERIVED FROM ``oncotriage/storage/database_logger.py``'S OWN SOURCE and
    NOT by building a database, which is what the first version did. That
    version was correct and it broke an isolation guarantee one layer up:
    ``tests/test_dashboard_app_integration.py`` records every ``sqlite3.connect``
    a render makes and requires every one of them to be the scratch database
    under test, and a preflight that builds a throwaway reference database
    opens a second one. The check was right -- a page render should open the
    database it was pointed at and nothing else -- so the derivation moved
    rather than the check.

    TWO SOURCES, BOTH THE OWNER'S. The ``CREATE TABLE`` statements inside
    ``initialize_database`` (string constants, read by AST) and the
    ``*_COLUMN_ADDITIONS`` dicts beside them, which is every column a migration
    can add. Neither is a second declaration: they are the two halves of what
    that function creates.

    A PARSE THAT FINDS NOTHING IS REPORTED, NEVER TREATED AS AN EMPTY SCHEMA.
    A requirement set that silently shrank would report FEWER missing columns,
    which reads exactly like a healthier database -- the dangerous direction.
    ``_SCHEMA_CACHE["schema_parse_error"]`` carries it out to
    ``dashboard_schema_readiness``, which refuses to say READY on the strength
    of a derivation that produced nothing.

    CACHED FOR THE LIFE OF THE PROCESS, not for 60 seconds: it is a pure
    function of the code, so nothing a Streamlit rerun does can change it. The
    lock is ``agent/deps.py``'s shape and is here for its reason -- the read and
    the build are two operations and a Streamlit server runs several script
    threads.
    """
    with _SCHEMA_CACHE_LOCK:
        if "tables" in _SCHEMA_CACHE:
            return _SCHEMA_CACHE["tables"]
        tables = {}
        try:
            source = Path(os.path.abspath(_database_logger.__file__)).read_text(
                encoding="utf-8")
            for node in ast.walk(ast.parse(source)):
                if not (isinstance(node, ast.Constant)
                        and isinstance(node.value, str)
                        and "CREATE TABLE" in node.value.upper()):
                    continue
                parsed = _columns_of_create_table(node.value)
                if parsed is None:
                    continue
                table, names = parsed
                tables.setdefault(table, set()).update(names)
        except (OSError, SyntaxError) as exc:
            _SCHEMA_CACHE["schema_parse_error"] = f"{type(exc).__name__}: {exc}"

        # EVERY ADDITIVE COLUMN TOO. A CREATE TABLE describes the shape a FRESH
        # database is born with; a migrated one additionally carries whatever
        # the addition dicts declare, and the dashboard reads several of those
        # (`llm_classifier_call_details`, `matching_call_mode`, ...).
        for table, additions in (
                ("inferences", _database_logger.INFERENCE_COLUMN_ADDITIONS),
                ("trial_matches",
                 _database_logger.TRIAL_MATCH_COLUMN_ADDITIONS),
                ("drift_metrics",
                 _database_logger.DRIFT_METRIC_COLUMN_ADDITIONS),
                ("runs", _database_logger.RUN_COLUMN_ADDITIONS)):
            tables.setdefault(table, set()).update(additions)

        _SCHEMA_CACHE["tables"] = {t: frozenset(c) for t, c in tables.items()}
        return _SCHEMA_CACHE["tables"]


def _names_the_dashboard_uses():
    """Every string constant in ``oncotriage/dashboard``, docstrings excluded.

    Collected by AST rather than by a regex, so a column name inside a comment
    is invisible and one inside an f-string's literal half is not missed.

    DOCSTRINGS ARE STRIPPED, and it is the same rule
    ``oncotriage/run_fingerprint.py`` applies to the renderer digest: this
    module's own prose names a dozen columns while arguing about them, and a
    scan that counted prose would report the argument as the thing it argues
    about -- a shape this project has now met six times.

    Cached for the life of the process beside ``_reference_schema``: both are
    pure functions of the source on disk.
    """
    with _SCHEMA_CACHE_LOCK:
        if "names" in _SCHEMA_CACHE:
            return _SCHEMA_CACHE["names"]
        root = os.path.dirname(os.path.abspath(__file__))
        found = set()
        for directory, _sub, files in os.walk(root):
            if os.path.basename(directory) == "__pycache__":
                continue
            for filename in sorted(files):
                if not filename.endswith(".py"):
                    continue
                try:
                    tree = ast.parse(
                        Path(os.path.join(directory, filename))
                        .read_text(encoding="utf-8"))
                except (OSError, SyntaxError):        # noqa: PERF203 -- reported
                    # A module that cannot be parsed contributes no names. It
                    # cannot be a silent pass either: a requirement set that
                    # shrank would report FEWER missing columns, which reads
                    # exactly like a healthier database. `_parse_failures`
                    # carries it out to the caller.
                    _SCHEMA_CACHE.setdefault("parse_failures", []).append(
                        os.path.join(directory, filename))
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Module, ast.ClassDef,
                                         ast.FunctionDef, ast.AsyncFunctionDef)):
                        body = getattr(node, "body", [])
                        if (body and isinstance(body[0], ast.Expr)
                                and isinstance(body[0].value, ast.Constant)
                                and isinstance(body[0].value.value, str)):
                            body[0].value.value = ""
                for node in ast.walk(tree):
                    if (isinstance(node, ast.Constant)
                            and isinstance(node.value, str)):
                        found.add(node.value)
        _SCHEMA_CACHE["names"] = frozenset(found)
        return _SCHEMA_CACHE["names"]


def dashboard_column_requirements():
    """``{table: sorted(columns)}`` the dashboard needs on a current database.

    The intersection argued at the top of this section, MINUS the columns a
    writer will supply on its own. Returns only the tables in
    ``DASHBOARD_TABLES``, because those are the two whose absence is fatal to
    the page.
    """
    schema = _reference_schema()
    named = _names_the_dashboard_uses()
    tolerated = _tolerated_columns()
    return {table: sorted((schema.get(table, frozenset()) & named)
                          - tolerated.get(table, frozenset()))
            for table in DASHBOARD_TABLES}


def _tolerated_columns():
    """``{table: frozenset}`` -- named by the dashboard, and NOT worth refusing.

    THE DISTINCTION IS THE PROJECT'S OWN AND IT IS ALREADY WRITTEN DOWN, in
    ``oncotriage/storage/queries.py``'s skip banner: a column a migration ADDS
    is the ordinary state of a database written before it, and the next writer
    to open the file supplies it -- while a column the naming pass RENAMED is
    one no writer will ever repair, because the migration loop can only ADD.

    SO THE TWO GET DIFFERENT ANSWERS. A renamed column missing is a REFUSAL:
    that database cannot be rendered and no command short of an archive will
    change it. An additive column missing is a WARNING: the page renders, and
    the tabs that read one already guard it, which is checkable rather than
    hoped for -- ``tabs/run_health.py`` returns early on
    ``"run_id" not in df.columns`` and says so on screen, and
    ``dashboard/call_mode.py`` answers a not-recorded bucket.

    WITHOUT THIS SPLIT THE PREFLIGHT REGRESSES A WORKING PATH, and it did:
    ``run_id`` is additive, the Run Health tab handles its absence correctly
    and reports "partial", and the first version of this requirement named it
    -- which would have refused the WHOLE dashboard for a database whose only
    fault is that it predates one column. Measured in this pass, not reasoned
    about.

    THE RESIDUAL IS STATED RATHER THAN HIDDEN. An additive column read by a tab
    that does NOT guard it still raises, exactly as it did before this preflight
    existed: the preflight narrows the failure, it does not claim to remove it.
    ``dashboard_schema_readiness`` reports every tolerated column the database
    lacks in ``tolerated_missing``, and the page prints them, so an operator
    meeting such a traceback has already been told which columns are absent.
    """
    return {
        "inferences": (frozenset(_database_logger.INFERENCE_COLUMN_ADDITIONS)
                       - frozenset(_database_logger.RENAMED_INFERENCE_COLUMNS)),
        "trial_matches": frozenset(
            _database_logger.TRIAL_MATCH_COLUMN_ADDITIONS),
    }


@st.cache_data(ttl=60)
def dashboard_schema_readiness():
    """Whether this database is one the dashboard can render. Cached 60s.

    Returns a dict with:
        state             one of ``SCHEMA_STATES``
        db_path           the resolved path, always, so a diagnosis can name it
        recorded_version  ``PRAGMA user_version``, or None when unread
        required_version  ``database_logger.SCHEMA_USER_VERSION``
        version_note      how the two compare, in words, or ``""``
        missing_tables    sorted, of ``DASHBOARD_TABLES``
        missing_columns   sorted ``"table.column"``, empty when a table is gone
        tolerated_missing sorted ``"table.column"`` a writer WILL supply --
                          reported, never a refusal; see ``_tolerated_columns``
        checked_columns   how many were checked -- a non-degeneracy figure, so a
                          derivation that silently produced nothing cannot read
                          as a clean database
        parse_failures    dashboard modules the name scan could not parse
        error             the exception text, or None

    IT NEVER RAISES. It is the thing that runs before the page has anything to
    render an error INTO, so an exception here would be the blank page it exists
    to prevent. Every failure becomes a state.

    MISSING COLUMNS ARE NOT REPORTED FOR AN ABSENT TABLE. Naming both tells an
    operator to add a column to a table that is not there -- the rule
    ``queries.missing_requirements`` already applies one layer down, adopted
    rather than re-argued.
    """
    db_path = paths.inferences_path
    blank = {"state": SCHEMA_READY, "db_path": db_path,
             "recorded_version": None, "required_version": SCHEMA_USER_VERSION,
             "version_note": "", "missing_tables": [], "missing_columns": [],
             "tolerated_missing": [], "checked_columns": 0,
             "parse_failures": [], "error": None}

    if not os.path.isfile(db_path):
        return dict(blank, state=SCHEMA_NO_DATABASE)

    conn = None
    try:
        conn = _readonly_connection()
        if conn is None:                                # raced with a deletion
            return dict(blank, state=SCHEMA_NO_DATABASE)

        recorded = conn.execute("PRAGMA user_version").fetchone()[0]
        present = queries.available_tables(conn)
        requirements = dashboard_column_requirements()
        parse_failures = list(_SCHEMA_CACHE.get("parse_failures", []))
        schema_error = _SCHEMA_CACHE.get("schema_parse_error")

        # A DERIVATION THAT PRODUCED NOTHING IS NOT A CLEAN DATABASE. Reporting
        # READY on the strength of an empty requirement set is the failure this
        # whole section is built to avoid, arriving through the derivation
        # rather than through the database.
        if schema_error or not all(requirements.get(t)
                                   for t in DASHBOARD_TABLES):
            return dict(
                blank, state=SCHEMA_UNREADABLE, recorded_version=recorded,
                error=("the dashboard's column requirement could not be "
                       "derived from oncotriage/storage/database_logger.py"
                       + (f": {schema_error}" if schema_error else
                          " -- the parse produced no columns for "
                          + ", ".join(t for t in DASHBOARD_TABLES
                                      if not requirements.get(t)))))

        missing_tables = sorted(t for t in DASHBOARD_TABLES if t not in present)
        missing_columns = []
        tolerated_missing = []
        tolerated = _tolerated_columns()
        named = _names_the_dashboard_uses()
        checked = 0
        for table, columns in requirements.items():
            if table in missing_tables:
                continue
            have = queries.table_columns(conn, table)
            checked += len(columns)
            missing_columns.extend(f"{table}.{c}" for c in columns
                                   if c not in have)
            # REPORTED AND NOT REFUSED. The page renders without these and the
            # tabs that read them guard them; naming them is what stops an
            # operator meeting an unguarded one as a bare traceback.
            tolerated_missing.extend(
                f"{table}.{c}"
                for c in sorted(tolerated.get(table, frozenset()) & named)
                if c not in have)
        missing_columns.sort()
        tolerated_missing.sort()

        if recorded > SCHEMA_USER_VERSION:
            note = (f"the database records schema era {recorded} and this "
                    f"build knows era {SCHEMA_USER_VERSION}: THIS CODE IS "
                    f"BEHIND THE DATABASE, not the other way round. Do not "
                    f"archive it.")
        elif recorded < SCHEMA_USER_VERSION:
            note = (f"the database records schema era {recorded} and this "
                    f"build creates era {SCHEMA_USER_VERSION}.")
        else:
            note = (f"the database records the current schema era "
                    f"{recorded}, so the stamp and the columns DISAGREE -- "
                    f"which is itself a finding: this file was not built by "
                    f"initialize_database, or a migration stopped between the "
                    f"ALTER and the stamp."
                    if missing_columns or missing_tables else "")

        if missing_tables:
            state = SCHEMA_TABLES_MISSING
        elif missing_columns:
            state = SCHEMA_COLUMNS_MISSING
        else:
            state = SCHEMA_READY

        return {"state": state, "db_path": db_path,
                "recorded_version": recorded,
                "required_version": SCHEMA_USER_VERSION,
                "version_note": note, "missing_tables": missing_tables,
                "missing_columns": missing_columns,
                "tolerated_missing": tolerated_missing,
                "checked_columns": checked,
                "parse_failures": parse_failures, "error": None}

    except Exception as exc:                       # noqa: BLE001 -- reported
        return dict(blank, state=SCHEMA_UNREADABLE,
                    error=f"{type(exc).__name__}: {exc}")
    finally:
        if conn:
            conn.close()


#------------------------------------------------------------------------------


def _readonly_connection():
    """A read-only connection to the configured database, or ``None``.

    ``None`` when the file is not there. Deliberately NOT a plain
    ``sqlite3.connect``: that CREATES the file, so a reader asking "does this
    database have the run tables" would answer by making a database that has
    nothing at all.

    The path is read INSIDE the function, never imported at module scope -- see
    this module's docstring for why that is not a style preference.
    """
    db_path = paths.inferences_path
    if not os.path.isfile(db_path):
        return None
    uri = "file:" + os.path.abspath(db_path).replace("?", "%3f") + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


@st.cache_data(ttl=60)
def load_drift_reference_data():
    """The drift REFERENCE designations. Cached for 60 seconds.

    An empty frame WITH THE COLUMNS the tab reads whenever the table is absent
    or unreadable, so a caller can index a column without testing first -- the
    shape the loaders above already use, and what lets the drift tab render a
    caption for a database that predates the designation store.

    IT OPENS READ-ONLY, unlike the three ORIGINAL loaders at the top of this module. Those were left on a
    plain ``sqlite3.connect`` because changing them is a behaviour change to
    eight tabs; this one is new, and a plain connect CREATES the file, so a
    reader asking "does this database have a designation" would answer by
    making a database that has nothing at all.

    THE SUPERSEDED ROWS COME TOO. ``is_active`` is projected rather than
    filtered on, because a drift row names the designation it was taken against
    in ``reference_id`` and that designation may since have been retired -- a
    caption that could only resolve the ACTIVE one would print "unknown
    reference" over every historical run.
    """
    columns = ["id", "designated_at", "designated_by", "label", "note",
               "is_active", "anchor_run_id", "campaign_head_run_id",
               "campaign_run_ids", "row_count", "patient_count",
               "digest_algorithm", "content_digest"]
    conn = None
    try:
        conn = _readonly_connection()
        if conn is None:
            return pd.DataFrame(columns=columns)
        return pd.read_sql_query(
            f"SELECT {', '.join(columns)} FROM drift_reference "
            f"ORDER BY id DESC", conn)
    except Exception:                                  # noqa: BLE001
        # NOT `st.error`. An absent `drift_reference` is the ordinary state of
        # a database written before schema era 16, and the drift tab says so in
        # its own words; a red banner would report a migration as a fault.
        return pd.DataFrame(columns=columns)
    finally:
        if conn:
            conn.close()


@st.cache_data(ttl=60)
def load_run_tracking_availability():
    """What this database can answer about runs. Cached for 60 seconds.

    Returns a dict with:
        availability    one of ``RUN_TRACKING_STATES``
        tables          the run tables that ARE present, sorted
        missing         the run tables that are not, sorted
        has_run_id      whether ``inferences.run_id`` exists
        error           the exception text, or None

    ASKED SEPARATELY FROM THE DATA, because the two answers are different
    findings and a loader that returned an empty frame for both would collapse
    them. "This database has no run rows" is a statement about a pipeline that
    has not run; "this database has no run TABLE" is a statement about a
    database that predates the feature. Rendering them the same way is the
    defect the ``counters_registered`` meta row exists to prevent, one layer up.

    ``has_run_id`` is checked even though ``initialize_database`` creates the
    column and the tables in one call, so the two cannot disagree in a database
    this project wrote. It is cheap, and the tab reads the column directly.
    """
    conn = None
    try:
        conn = _readonly_connection()
        if conn is None:
            return {"availability": RUN_TRACKING_NO_DATABASE, "tables": [],
                    "missing": sorted(queries.RUN_TABLES), "has_run_id": False,
                    "error": None}

        present = queries.available_tables(conn)
        found = sorted(t for t in queries.RUN_TABLES if t in present)

        # WHAT THE TAB'S OWN QUERIES CANNOT BE ASKED, DERIVED FROM THEIR OWN
        # DECLARATIONS. `present` is passed so the whole registry sweep costs
        # one `available_tables` call, and so a query declaring neither
        # requirement is answered without touching the database at all.
        #
        # THE UNION IS ORDER-PRESERVING AND DE-DUPLICATED. All four queries
        # declare `inferences.run_id`, and reporting it four times would turn
        # the tab's one-line "missing:" list into a wall.
        missing = []
        for _key in RUN_TAB_QUERY_KEYS:
            for _absent in queries.missing_requirements(conn, _key,
                                                        present=present):
                if _absent not in missing:
                    missing.append(_absent)
        missing.sort()

        # KEPT AS A FIELD OF ITS OWN even though `missing` now carries it: it
        # is the one requirement the tab's own selection-attribution caption
        # asks about directly, and it is a plain boolean rather than a name to
        # search a list for.
        has_run_id = "run_id" in queries.table_columns(conn, "inferences")

        if not found:
            availability = RUN_TRACKING_ABSENT
        elif missing:
            availability = RUN_TRACKING_PARTIAL
        else:
            availability = RUN_TRACKING_PRESENT

        return {"availability": availability, "tables": found,
                "missing": missing, "has_run_id": has_run_id, "error": None}

    except Exception as exc:                       # noqa: BLE001 -- reported
        # RECORDED, NOT SWALLOWED. Returning ``absent`` here would tell an
        # operator to run the pipeline when the real fault is an unreadable
        # file, so the state carries the exception text and the tab prints it.
        return {"availability": RUN_TRACKING_NO_DATABASE, "tables": [],
                "missing": sorted(queries.RUN_TABLES), "has_run_id": False,
                "error": f"{type(exc).__name__}: {exc}"}
    finally:
        if conn:
            conn.close()


def _load_run_query(key):
    """Run one registered query read-only and return its frame.

    An empty frame WITH THE QUERY'S COLUMNS on any failure, so a caller can index
    a column without testing first -- the shape the three ORIGINAL loaders at the top of this module already
    use. The availability loader is what distinguishes "failed" from "no rows";
    a caller that has not asked it has not earned an answer.
    """
    conn = None
    try:
        conn = _readonly_connection()
        if conn is None:
            return pd.DataFrame()
        return queries.run(conn, key)
    except Exception as exc:                       # noqa: BLE001 -- reported
        st.error(f"Run tracking query {key!r} failed: "
                 f"{type(exc).__name__}: {exc}")
        return pd.DataFrame()
    finally:
        if conn:
            conn.close()


@st.cache_data(ttl=60)
def load_run_summary_data():
    """One row per run. Cached for 60 seconds. See ``queries.run_summary``."""
    return _load_run_query(RUN_SUMMARY_QUERY)


@st.cache_data(ttl=60)
def load_run_degradation_data():
    """Per-run, per-counter degradation totals, clean runs included.

    Cached for 60 seconds. See ``queries.run_degradation_breakdown``.
    """
    return _load_run_query(RUN_DEGRADATION_QUERY)


@st.cache_data(ttl=60)
def load_run_campaign_data():
    """One row per CAMPAIGN -- runs stitched across crash and resume.

    Cached for 60 seconds. See ``queries.campaign_summary``, where the stitch
    rule and everything it deliberately does not do are argued.

    IT DECLARES ``runs`` AND NOT ``RUN_TABLES``, so it can be answered on a
    database that has `runs` and no `run_metrics` -- and the tab therefore has
    to be prepared for an EMPTY frame in a state where ``load_run_summary_data``
    returned rows, which is the same shape ``_render_selected_run`` already
    handles for the breakdown. An empty frame here on a database that HAS runs
    is a defect in the query, not a fact about the database: the query is driven
    from `runs`, so every run is in exactly one campaign.
    """
    return _load_run_query(RUN_CAMPAIGN_QUERY)


@st.cache_data(ttl=60)
def load_run_attribution_data():
    """The inference-row census by run attribution. Cached for 60 seconds.

    See ``queries.run_attribution_coverage``. This is what the tab renders the
    "no run_id" population from -- a stated count, never a silent exclusion.
    """
    return _load_run_query(RUN_ATTRIBUTION_QUERY)



#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
