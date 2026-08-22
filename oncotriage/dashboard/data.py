"""
Dashboard data loaders.

The three ``@st.cache_data(ttl=60)`` readers of ``inferences.db``, moved
verbatim out of "21- Streamlit Dashboard.py" in pass 20c-3c-1.

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

import os
import sqlite3

import pandas as pd
import streamlit as st

from oncotriage import paths
from oncotriage.storage import queries


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

Either one run table without the other, or both tables without
``inferences.run_id``. Not producible by ``initialize_database``, which creates
all three in one call, so it means the database was edited by something else.
Kept apart from ``absent`` because the remedy differs: absent is fixed by
running the pipeline, partial wants a person.

THE COLUMN CASE IS IN HERE RATHER THAN IN ``present`` FOR A MEASURED REASON.
``run_summary`` and ``run_attribution_coverage`` both JOIN on
``inferences.run_id``, so with the tables present and the column gone they are
refused by ``queries.unavailable`` -- and this loader reporting ``present``
would send the tab down its normal path, where two empty frames would render as
"the run tables hold no rows". That is a statement about a pipeline that has not
run, made about a database whose queries could not be asked."""

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
        missing = sorted(t for t in queries.RUN_TABLES if t not in present)

        # The COLUMN, asked through the query layer's own helper so this module
        # does not carry a second PRAGMA that can disagree with the one the
        # `requires_columns` guard reads.
        has_run_id = "run_id" in queries.table_columns(conn, "inferences")

        if not found:
            availability = RUN_TRACKING_ABSENT
        elif missing:
            availability = RUN_TRACKING_PARTIAL
        elif not has_run_id:
            availability = RUN_TRACKING_PARTIAL
            missing = ["inferences.run_id"]
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
    a column without testing first -- the shape the three loaders above already
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
    return _load_run_query("run_summary")


@st.cache_data(ttl=60)
def load_run_degradation_data():
    """Per-run, per-counter degradation totals, clean runs included.

    Cached for 60 seconds. See ``queries.run_degradation_breakdown``.
    """
    return _load_run_query("run_degradation_breakdown")


@st.cache_data(ttl=60)
def load_run_attribution_data():
    """The inference-row census by run attribution. Cached for 60 seconds.

    See ``queries.run_attribution_coverage``. This is what the tab renders the
    "no run_id" population from -- a stated count, never a silent exclusion.
    """
    return _load_run_query("run_attribution_coverage")



#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
