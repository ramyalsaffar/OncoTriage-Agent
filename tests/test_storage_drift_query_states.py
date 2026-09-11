# Drift Query State-Awareness Test
##################################

"""
THE FIVE DRIFT QUERIES COMPUTED AN ALERT RATE OVER THE WRONG DENOMINATOR, and
this file is the measurement at the QUERY SURFACE -- exact results against
constructed rows whose truth is known before the query is asked.

    SELECT ... SUM(alert) as total_alerts,
           ROUND(100.0 * SUM(alert) / COUNT(*), 1) as alert_rate_pct
    FROM drift_metrics GROUP BY metric_category

Since the drift redesign, ``drift_metrics.alert`` is NULL for every reading
that is not a verdict: a reporting-only value (which is EVERY comparison metric
this project ships, by ruling), a metric that could not compute, and every row
written before schema era 16. ``SUM`` skips those NULLs; ``COUNT(*)`` counts
them. So the numerator counted verdicts and the denominator counted ROWS, and
the rate was diluted by exactly the readings that could never have alerted --
always in the reassuring direction, and worse the more reporting-only metrics
the project adds, which is the direction it is deliberately moving in.

THE SECOND DEFECT IS DOUBLE COUNTING. The drift writer emits every reading once
per cancer-group stratum AND once over the whole population with ``stratum``
NULL. A GROUP BY that does not separate the two counts every measurement
several times, over a denominator that is neither population.

AND ``drift_window_configurations`` IS RETIRED. Both of its grouping columns
are NULL from era 16 on -- the redesign DELETED the two config constants they
recorded when it replaced time-window baseline selection with a designated
reference campaign -- so it returned one ``(NULL, NULL)`` row aggregating the
whole table under a heading promising a per-configuration breakdown.

WHAT IS VERIFIED HERE AND WHAT IS NOT. This is the query/report surface: exact
frames from ``queries.run``, and ``report()``'s behaviour on a database that
cannot answer. NO DASHBOARD RENDERING IS DRIVEN, deliberately -- the dashboard
calls none of these five, so a render test would assert about a consumer that
does not exist.

RUNS, COSTS, KEYS
-----------------
No network, no keys, no spend, no live Qdrant, no model load, no corpus, no git
history, no live server. NOT in the collision matrix: every database is built
by the project's own ``initialize_database`` inside a ``tempfile.mkdtemp`` it
removes and asserts gone, ``paths._RESOLVED`` is seeded so nothing can resolve
to the production tree, and the production database is NEVER OPENED, not even
read-only. It EXECS NOTHING: every control is a different SQL string on one
connection, or a database built into a real failing shape.
"""

import contextlib
import hashlib
import io as _io
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pandas as pd

from oncotriage import paths as _paths
from oncotriage.monitoring import drift_states as _states
from oncotriage.storage import queries
from oncotriage.storage.database_logger import initialize_database


#------------------------------------------------------------------------------


_RESULTS = {"passed": 0, "failed": 0, "skipped": 0}
_FAILURES = []
_SKIPS = []


def check(label, actual, expected):
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


def fail(label, detail):
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {detail}")
    print(f"  FAIL  {label}\n          {detail}")


def skip(label, reason):
    """Coverage that could NOT be exercised here. NEVER counted as a pass."""
    _RESULTS["skipped"] += 1
    _SKIPS.append(f"{label}\n          {reason}")
    print(f"  SKIP  {label}\n          {reason}")


def called(fn, *args, **kwargs):
    """``fn(*args)``, or a marker string when it raises.

    Every read of a query result below is exactly the expression that raises
    when the defect under test fires. A bare call inside ``check``'s argument
    list turns a run that owes a summary into one traceback -- the shape this
    project has shipped eighteen times.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                       # noqa: BLE001 -- reported
        return f"raised: {type(exc).__name__}: {exc}"


def one(frame, **where):
    """The single row of `frame` matching `where`, as a dict, or a named absence."""
    if not isinstance(frame, pd.DataFrame):
        return {"__absent__": f"not a frame: {frame!r}"}
    sub = frame
    for column, value in where.items():
        if column not in sub.columns:
            return {"__absent__": f"no column {column!r}"}
        sub = sub[sub[column] == value]
    if len(sub) != 1:
        return {"__absent__": f"{len(sub)} row(s) match {where!r}, expected 1"}
    return sub.iloc[0].to_dict()


def cell(row, key, default="(absent)"):
    return row.get(key, default) if isinstance(row, dict) else default


def digest_file(path):
    if not os.path.exists(path):
        return "absent"
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        return f"unreadable: {type(exc).__name__}"


_TMP = tempfile.mkdtemp(prefix="oncotriage-drift-queries-")
_PRODUCTION_DB = os.path.abspath(_paths.inferences_path)
_PRODUCTION_DIGEST_BEFORE = digest_file(_PRODUCTION_DB)
_PRODUCTION_EXISTED_BEFORE = os.path.exists(_PRODUCTION_DB)
_SAVED_RESOLVED = _paths._RESOLVED.get("inferences_path")
_paths._RESOLVED["inferences_path"] = os.path.join(_TMP, "never-opened.db")

_QUERIES_FILE = os.path.abspath(queries.__file__)
_QUERIES_DIGEST_BEFORE = digest_file(_QUERIES_FILE)

print("=" * 74)
print("DRIFT QUERY STATE-AWARENESS TEST")
print("=" * 74)
print(f"Scratch root: {_TMP}")
print()

_FIVE = ("drift_alert_rate_by_category", "drift_summary_per_metric",
         "drift_latest_run", "drift_worst_zscores", "drift_trend_over_time")


def _quiet_initialize(path):
    with contextlib.redirect_stderr(_io.StringIO()):
        with contextlib.redirect_stdout(_io.StringIO()):
            initialize_database(path)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 1: THE SEED, AND ITS TRUTH IS KNOWN BEFORE ANY QUERY IS ASKED
# ===========================================================================

print("=" * 74)
print("Section 1: the seed")
print("=" * 74)

_TS = "2026-09-01 09:00:00"
_OLDER = "2026-08-01 09:00:00"

# ONE CATEGORY, ONE SCOPE, EVERY CLASS. The counts below are the expectations
# for every check in sections 2 and 3; nothing is read back out of a query to
# build them.
_EXPECTED_OVERALL = {
    "rows_total": 9,
    "alertable": 2,          # computed + alerting + a real verdict
    "alerts": 1,             # ...of which one alerted
    "verdict_missing": 1,    # computed + alerting + alert NULL
    "verdict_invalid": 1,    # computed + alerting + alert = 2
    "not_computed": 1,       # alerting + status that produced no reading
    "reporting_only": 3,     # policy that can never alert
    "pre_era_16": 1,         # no status, no policy, and alert = 1 on the row
}
_EXPECTED_RATE = 50.0        # 1 of 2 alertable

# THE PRE-ERA-16 ROW CARRIES alert = 1 ON PURPOSE. It is the row that makes the
# old arithmetic and the new one differ in the NUMERATOR as well as the
# denominator: SUM(alert) counts it, and it is a byte whose meaning this
# project has ruled is unrecoverable.
_PRE_ERA_ALERT = 1

# What the RETIRED arithmetic answers on the same rows. Computed here so the
# comparison in section 2 is against a stated number rather than against
# whatever the old SQL happens to return.
_OLD_NUMERATOR = _EXPECTED_OVERALL["alerts"] + _PRE_ERA_ALERT + 2  # +invalid(2)
_OLD_DENOMINATOR = _EXPECTED_OVERALL["rows_total"]
_OLD_RATE = round(100.0 * _OLD_NUMERATOR / _OLD_DENOMINATOR, 1)

_DB = os.path.join(_TMP, "drift.db")
_quiet_initialize(_DB)
_conn = sqlite3.connect(_DB)


def _row(category, name, status, policy, alert, stratum=None, value=1.0,
         z=None, timestamp=_TS):
    _conn.execute(
        "INSERT INTO drift_metrics (timestamp, metric_category, metric_name, "
        "metric_value, z_score, alert, status, alert_policy, stratum) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (timestamp, category, name, value, z, alert, status, policy, stratum))


def _legacy_row(category, name, alert, z=None, timestamp=_TS):
    """A row with NO state axes -- what the pre-era-16 writer produced."""
    _conn.execute(
        "INSERT INTO drift_metrics (timestamp, metric_category, metric_name, "
        "metric_value, z_score, alert) VALUES (?,?,?,?,?,?)",
        (timestamp, category, name, 1.0, z, alert))


_CAT = "data_availability"
_row(_CAT, "m_alert_yes", "computed", "alerting", 1, z=4.0)
_row(_CAT, "m_alert_no", "computed", "alerting", 0, z=0.2)
_row(_CAT, "m_missing", "computed", "alerting", None, z=1.0)
_row(_CAT, "m_invalid", "computed", "alerting", 2, z=1.1)
_row(_CAT, "m_notcomp", _states.STATUS_NO_DATA, "alerting", None)
for _i in range(3):
    _row(_CAT, f"m_rep{_i}", "computed", "reporting_only", None, z=0.5)
_legacy_row(_CAT, "m_legacy", _PRE_ERA_ALERT, z=9.9)

# THE SAME MEASUREMENT, PER STRATUM. Without a scope split these two are
# counted into the same group as the nine above.
for _s in ("breast", "lung"):
    _row(_CAT, "m_alert_yes", "computed", "alerting", 1, stratum=_s, z=4.0)

# A SECOND CATEGORY IN WHICH NOTHING CAN ALERT.
_row("data_drift", "psi_x", "computed", "reporting_only", None, z=0.3)

# A SECOND RUN, so the trend query has a series to order. IT IS A DIFFERENT
# METRIC IN A DIFFERENT CATEGORY, deliberately: these queries aggregate over
# EVERY run (they always did), so a second row of `m_alert_yes` in `_CAT` would
# join the group whose exact composition every check in section 2 pins -- and
# the first draft of this seed did exactly that, which those checks reported.
_row("performance_drift", "m_trend", "computed", "alerting", 0, z=0.4,
     timestamp=_OLDER)
_row("performance_drift", "m_trend", "computed", "alerting", 1, z=3.0)
_conn.commit()

check("1a  the seed wrote every class of row",
      _conn.execute("SELECT COUNT(*) FROM drift_metrics").fetchone()[0], 14)
check("1b  ...nine of them in the overall scope of the category under test, "
      "ACROSS EVERY RUN -- which is the population section 2's expectations "
      "are over, because these queries are not scoped to one timestamp",
      _conn.execute(
          "SELECT COUNT(*) FROM drift_metrics WHERE metric_category = ? "
          "AND stratum IS NULL", (_CAT,)).fetchone()[0],
      _EXPECTED_OVERALL["rows_total"])
check("1c  ...and the pre-era-16 row really has no state axes, which is what "
      "makes it unrecoverable rather than merely old",
      _conn.execute(
          "SELECT COUNT(*) FROM drift_metrics WHERE status IS NULL "
          "AND alert_policy IS NULL AND alert = 1").fetchone()[0], 1)
check_true("1d  the OLD arithmetic and the NEW one give different answers on "
           "this seed -- without this every check below is a tautology",
           abs(_OLD_RATE - _EXPECTED_RATE) > 1.0)
check("1e  ...and the old one is the RECORDED reading, computed here rather "
      "than read back out of anything",
      _conn.execute(
          "SELECT ROUND(100.0 * SUM(alert) / COUNT(*), 1) FROM drift_metrics "
          "WHERE metric_category = ? AND stratum IS NULL",
          (_CAT,)).fetchone()[0], _OLD_RATE)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 2: THE ALERT RATE, EXACT
# ===========================================================================

print()
print("=" * 74)
print("Section 2: drift_alert_rate_by_category")
print("=" * 74)

_rate = called(queries.run, _conn, "drift_alert_rate_by_category")
_overall = one(_rate, metric_category=_CAT, scope=queries.DRIFT_SCOPE_OVERALL)

for _column, _expected in _EXPECTED_OVERALL.items():
    check(f"2a  {_column} = {_expected}", cell(_overall, _column), _expected)

check("2b  the rate divides by `alertable` and NOTHING else",
      cell(_overall, "alert_rate_pct_of_alertable"), _EXPECTED_RATE)
check_true("2b  ...and is NOT the diluted figure the retired arithmetic gives",
           cell(_overall, "alert_rate_pct_of_alertable") != _OLD_RATE)

_classes = [c for c in queries.DRIFT_ROW_CLASSES]
check("2c  the per-class columns PARTITION the group: they sum to rows_total, "
      "so nothing is counted twice and nothing is dropped",
      sum(int(cell(_overall, c, 0)) for c in _classes),
      _EXPECTED_OVERALL["rows_total"])
check_true("2c  ...and more than one class is non-zero (non-degeneracy: a "
           "single class holding every row would satisfy the sum above)",
           sum(1 for c in _classes if int(cell(_overall, c, 0)) > 0) >= 4)

_stratum = one(_rate, metric_category=_CAT, scope=queries.DRIFT_SCOPE_STRATUM)
check("2d  the per-stratum rows are a SEPARATE group, so a total across the "
      "two scopes cannot double-count a measurement",
      (cell(_stratum, "rows_total"), cell(_stratum, "alertable"),
       cell(_stratum, "alerts")), (2, 2, 2))
check("2d  ...and the two scopes are exactly the closed vocabulary",
      sorted(set(_rate["scope"])) if isinstance(_rate, pd.DataFrame) else [],
      sorted(queries.DRIFT_SCOPES))

_nothing = one(_rate, metric_category="data_drift",
               scope=queries.DRIFT_SCOPE_OVERALL)
check("2e  a group in which NOTHING can alert has no rate",
      bool(pd.isna(cell(_nothing, "alert_rate_pct_of_alertable"))), True)
check_true("2e  ...and NOT a rate of zero, which would be a measurement about "
           "a population that recorded none",
           cell(_nothing, "alert_rate_pct_of_alertable") != 0.0)
check("2e  ...with its one reading counted as reporting-only",
      (cell(_nothing, "alertable"), cell(_nothing, "reporting_only")), (0, 1))

# --- THE TWO DILUTION SHAPES, ISOLATED -----------------------------------
#
# Each is a database holding ONE alertable reading that alerted, plus rows of
# one other class. The true rate is 100% in every one; the retired arithmetic
# reports something else, and the number it reports is what these checks pin.
def _isolated(extra_rows):
    path = os.path.join(_TMP, "iso_%d.db" % len(os.listdir(_TMP)))
    _quiet_initialize(path)
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO drift_metrics (timestamp, metric_category, metric_name, "
        "metric_value, alert, status, alert_policy) VALUES "
        "(?, 'data_availability', 'm', 1.0, 1, 'computed', 'alerting')", (_TS,))
    for status, policy, alert in extra_rows:
        conn.execute(
            "INSERT INTO drift_metrics (timestamp, metric_category, "
            "metric_name, metric_value, alert, status, alert_policy) VALUES "
            "(?, 'data_availability', 'x', 1.0, ?, ?, ?)",
            (_TS, alert, status, policy))
    conn.commit()
    return conn


for _label, _rows, _old_expected in (
        ("never-computed rows", [(_states.STATUS_NO_DATA, "alerting", None)] * 3,
         25.0),
        ("missing verdicts", [("computed", "alerting", None)] * 3, 25.0),
        ("reporting-only values", [("computed", "reporting_only", None)] * 3,
         25.0)):
    _iso = _isolated(_rows)
    _iso_frame = called(queries.run, _iso, "drift_alert_rate_by_category")
    _iso_row = one(_iso_frame, scope=queries.DRIFT_SCOPE_OVERALL)
    check(f"2f  {_label} do NOT dilute the rate",
          cell(_iso_row, "alert_rate_pct_of_alertable"), 100.0)
    check(f"2f  ...and the retired arithmetic on the same rows reports "
          f"{_old_expected}",
          _iso.execute("SELECT ROUND(100.0 * SUM(alert) / COUNT(*), 1) "
                       "FROM drift_metrics").fetchone()[0], _old_expected)
    check(f"2f  ...and they are counted in a column of their own",
          sum(int(cell(_iso_row, c, 0)) for c in _classes),
          1 + len(_rows))
    _iso.close()


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 3: THE OTHER FOUR
# ===========================================================================

print()
print("=" * 74)
print("Section 3: per-metric, latest-run, worst-z and trend")
print("=" * 74)

_summary = called(queries.run, _conn, "drift_summary_per_metric")
_sum_row = one(_summary, metric_name="m_alert_yes",
               scope=queries.DRIFT_SCOPE_OVERALL)
check("3a  a metric's run_count is every row at that scope",
      cell(_sum_row, "run_count"), 1)
check("3a  ...and its alerts are over `alertable` alone",
      (cell(_sum_row, "alertable"), cell(_sum_row, "alerts")), (1, 1))
_notcomp_row = one(_summary, metric_name="m_notcomp",
                   scope=queries.DRIFT_SCOPE_OVERALL)
check("3b  a metric that has NEVER computed reports 0 alerts beside 0 "
      "alertable, so it is distinguishable from one that computed and never "
      "alerted",
      (cell(_notcomp_row, "alertable"), cell(_notcomp_row, "alerts"),
       cell(_notcomp_row, "not_computed")), (0, 0, 1))
check("3b  ...and has no rate at all",
      bool(pd.isna(cell(_notcomp_row, "alert_rate_pct_of_alertable"))), True)
check("3b  ...while m_alert_no DID compute and did not alert, which is a rate "
      "of 0.0 and a different finding",
      cell(one(_summary, metric_name="m_alert_no",
               scope=queries.DRIFT_SCOPE_OVERALL),
           "alert_rate_pct_of_alertable"), 0.0)

_latest = called(queries.run, _conn, "drift_latest_run")
if isinstance(_latest, pd.DataFrame):
    check("3c  the latest run holds only the most recent timestamp's readings",
          len(_latest), 13)                    # 14 rows minus the older one
    _leaked = _latest[(_latest["alert_state"] != queries.DRIFT_CLASS_ALERTABLE)
                      & _latest["alert"].notna()]
    check("3d  an alert is projected ONLY where the state says it is a "
          "verdict -- so a pre-era-16 row's byte cannot render as one",
          len(_leaked), 0)
    check("3d  ...and the pre-era-16 row, which carries alert = 1, has none",
          bool(pd.isna(cell(one(_latest, metric_name="m_legacy"), "alert"))),
          True)
    check_true("3d  ...while an alertable row DOES carry its verdict "
               "(non-degeneracy: a query projecting NULL everywhere would "
               "satisfy the two lines above)",
               cell(one(_latest, metric_name="m_alert_yes",
                        scope=queries.DRIFT_SCOPE_OVERALL), "alert") == 1)
    check("3e  every projected alert column travels with a status",
          [c for c in ("computation", "alert_policy", "alert_state")
           if c not in _latest.columns], [])
else:
    fail("3c..3e  drift_latest_run did not return a frame", str(_latest))

_worst = called(queries.run, _conn, "drift_worst_zscores")
if isinstance(_worst, pd.DataFrame):
    check("3f  the worst-z query carries the status beside the score",
          [c for c in ("computation", "alert_state", "alert")
           if c not in _worst.columns], [])
    check("3f  ...and excludes rows with no z-score at all",
          int(_worst["z_score"].isna().sum()), 0)
    _wleaked = _worst[(_worst["alert_state"] != queries.DRIFT_CLASS_ALERTABLE)
                      & _worst["alert"].notna()]
    check("3f  ...and projects no alert without an alertable state",
          len(_wleaked), 0)
else:
    fail("3f  drift_worst_zscores did not return a frame", str(_worst))

_trend = called(queries.run, _conn, "drift_trend_over_time")
if isinstance(_trend, pd.DataFrame):
    check("3g  the trend query carries the state axes, so a gap in the value "
          "column can be told from a run that did not compute",
          [c for c in ("computation", "alert_policy", "alert_state", "scope")
           if c not in _trend.columns], [])
    _series = _trend[(_trend["metric_name"] == "m_trend")
                     & (_trend["scope"] == queries.DRIFT_SCOPE_OVERALL)]
    check("3g  ...and orders a metric's own scope as a series",
          list(_series["timestamp"]), [_OLDER, _TS])
else:
    fail("3g  drift_trend_over_time did not return a frame", str(_trend))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 4: THE VOCABULARY COMES FROM ITS OWNER
# ===========================================================================

print()
print("=" * 74)
print("Section 4: one owner for the classification")
print("=" * 74)

# READ OUT OF THE `IN (...)` LIST, NOT BY SUBSTRING. `reporting_only` is BOTH a
# policy name and a row-class name, so `"'reporting_only'" in <the sql>` is true
# for a SQL that names it only as a class -- which is what the first draft of
# this check reported. The membership test has to be the one the SQL performs.
import re as _re
_policy_list = _re.search(r"alert_policy NOT IN \(([^)]*)\)",
                          queries._DRIFT_ROW_CLASS_SQL)
check_true("4a  the classification tests alert_policy against a list "
           "(non-degeneracy: no list means the check below is vacuous)",
           _policy_list is not None)
check("4a  ...and that list is the owner's ALERTING_POLICIES, not literals",
      sorted(v.strip().strip("'") for v in _policy_list.group(1).split(","))
      if _policy_list else [], sorted(_states.ALERTING_POLICIES))
_status_list = _re.search(r"status NOT IN \(([^)]*)\)",
                          queries._DRIFT_ROW_CLASS_SQL)
check_true("4b  the classification tests status against a list",
           _status_list is not None)
check("4b  ...and that list is the owner's STATUSES_WITH_VALUE",
      sorted(v.strip().strip("'") for v in _status_list.group(1).split(","))
      if _status_list else [], sorted(_states.STATUSES_WITH_VALUE))
check("4c  every one of the five queries interpolates the SAME classification, "
      "so there is no second copy to forget",
      sorted(k for k in _FIVE
             if queries._DRIFT_ROW_CLASS_SQL in queries.QUERIES_BY_KEY[k].sql),
      sorted(_FIVE))
check("4d  the class vocabulary is closed and every member distinct",
      len(set(queries.DRIFT_ROW_CLASSES)), len(queries.DRIFT_ROW_CLASSES))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 5: THEY SKIP CLEANLY ON A PRE-ERA-16 DATABASE
# ===========================================================================

print()
print("=" * 74)
print("Section 5: a database that cannot answer")
print("=" * 74)

# BUILT BY THE SCHEMA OWNER AND THEN NARROWED, not hand-written.
# `tests/test_storage_schema_guards.py`'s ruling, adopted: a hand-written
# "pre-era" table describes a shape no era of this schema has ever had, and
# `report()` then dies on a column that is missing for a reason unrelated to
# what is being tested -- which is what the first draft of this fixture did.
# Dropping exactly the three columns the classification names produces the era
# that really exists on disk.
_OLD_DB = os.path.join(_TMP, "pre_era_16.db")
_quiet_initialize(_OLD_DB)
_old_conn = sqlite3.connect(_OLD_DB)
for _table, _column in queries.DRIFT_STATE_REQUIREMENTS:
    _old_conn.execute(f"ALTER TABLE {_table} DROP COLUMN {_column}")
_old_conn.execute("INSERT INTO drift_metrics (timestamp, metric_category, "
                  "metric_name, metric_value, alert) VALUES "
                  "('2026-01-01', 'data_drift', 'x', 1.0, 0)")
_old_conn.commit()
check("5-fixture  the narrowed database really lacks the three state columns "
      "(non-degeneracy: a fixture that still had them would make every skip "
      "check below vacuous)",
      sorted(c for _t, c in queries.DRIFT_STATE_REQUIREMENTS
             if c in queries.table_columns(_old_conn, "drift_metrics")), [])

for _key in _FIVE:
    check(f"5a  {_key} declares the requirement that makes it skippable",
          tuple(queries.QUERIES_BY_KEY[_key].requires_columns),
          queries.DRIFT_STATE_REQUIREMENTS)
    check(f"5b  ...and its declaration equals what its own SQL derives",
          sorted(queries.derive_requires_columns(
              queries.QUERIES_BY_KEY[_key].sql)),
          sorted(queries.QUERIES_BY_KEY[_key].requires_columns))
    # `missing_requirements` reports a column as the STRING "table.column",
    # which is what a diagnosis prints; the declaration is a (table, column)
    # pair. The two shapes are deliberate and the first draft of this check
    # compared them directly.
    check(f"5c  ...and on a pre-era-16 database it is reported as unanswerable",
          queries.missing_requirements(_old_conn, _key),
          tuple(f"{t}.{c}" for t, c in queries.DRIFT_STATE_REQUIREMENTS))
    check(f"5d  ...raising rather than returning an empty frame, because "
          f"'cannot be asked' and 'no rows' are different findings",
          str(called(queries.run, _old_conn, _key)).startswith(
              "raised: MissingTableError"), True)

_out, _err = _io.StringIO(), _io.StringIO()
with contextlib.redirect_stdout(_out), contextlib.redirect_stderr(_err):
    _report = called(queries.report, _old_conn)
_both = _out.getvalue() + _err.getvalue()
check_true("5e  report() RUNS TO THE END on that database rather than dying at "
           "the first drift query -- which is item 38's defect, and a "
           "state-aware query is exactly how it would come back",
           isinstance(_report, dict) and "Traceback" not in _both)
check("5e  ...and none of the five is in the returned dict, so a caller "
      "indexing one gets a KeyError it can act on rather than zeros about "
      "runs nobody asked about",
      [k for k in _FIVE if k in _report] if isinstance(_report, dict)
      else ["report did not return a dict"], [])
check_true("5e  ...and the skip banner names them",
           all(k in _both for k in _FIVE))
check_true("5e  ...and queries that CAN be answered still were "
           "(non-degeneracy: a report that answered nothing would satisfy the "
           "lines above)",
           isinstance(_report, dict) and len(_report) > 5)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 6: THE RETIRED QUERY
# ===========================================================================

print()
print("=" * 74)
print("Section 6: drift_window_configurations is retired")
print("=" * 74)

check("6a  the registry does not carry it",
      "drift_window_configurations" in queries.QUERIES_BY_KEY, False)
check("6a  ...and its key is gone from the ordered key list",
      "drift_window_configurations" in queries.QUERY_KEYS, False)
check("6b  asking for it by name raises a KeyError naming the valid keys, "
      "rather than returning an empty frame",
      str(called(queries.run, _conn, "drift_window_configurations")
          ).startswith("raised: KeyError"), True)

_qsrc = Path(_QUERIES_FILE).read_text(encoding="utf-8")
check_true("6c  the retirement is RECORDED where the query used to sit, with "
           "the do-not-re-add instruction -- `expansion_token_efficiency`'s "
           "convention",
           "WAS HERE AND IS RETIRED" in _qsrc
           and "DO NOT RE-ADD IT" in _qsrc)
check_true("6d  ...and the reason names what replaced the window: a designated "
           "reference, not a pair of day counts",
           "drift_reference" in _qsrc.split("WAS HERE AND IS RETIRED")[1][:3000])

# THE DEGRADED SHAPE IT WOULD HAVE HAD. Not an error -- one row of NULLs
# aggregating the whole table under a heading promising a breakdown.
_retired_sql = ("SELECT baseline_window_days, comparison_window_days, "
                "COUNT(*) as checks, SUM(alert) as alerts FROM drift_metrics "
                "GROUP BY baseline_window_days, comparison_window_days")
_retired = pd.read_sql_query(_retired_sql, _conn)
check("6e  the retired query on an era-16 database returns ONE row -- it does "
      "not fail, which is why retiring it rather than leaving it is the fix",
      len(_retired), 1)
check("6e  ...whose grouping columns are both NULL",
      (bool(pd.isna(_retired.iloc[0]["baseline_window_days"])),
       bool(pd.isna(_retired.iloc[0]["comparison_window_days"]))), (True, True))
check("6e  ...and whose alert total carries the same denominator defect",
      int(_retired.iloc[0]["checks"]),
      int(_conn.execute("SELECT COUNT(*) FROM drift_metrics").fetchone()[0]))

_conn.close()
_old_conn.close()


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 7: NOTHING OUTSIDE THE SCRATCH TREE WAS TOUCHED
# ===========================================================================

print()
print("=" * 74)
print("Section 7: isolation")
print("=" * 74)

_paths._RESOLVED["inferences_path"] = _SAVED_RESOLVED
if _SAVED_RESOLVED is None:
    _paths._RESOLVED.pop("inferences_path", None)

check("7a  the production database was not created, deleted or modified -- "
      "this file never opens it, not even read-only",
      digest_file(_PRODUCTION_DB), _PRODUCTION_DIGEST_BEFORE)
check("7a  ...and its existence is unchanged",
      os.path.exists(_PRODUCTION_DB), _PRODUCTION_EXISTED_BEFORE)
check("7b  oncotriage/storage/queries.py is byte-identical afterwards",
      digest_file(_QUERIES_FILE), _QUERIES_DIGEST_BEFORE)
check_true("7b  ...and that reading is a real digest (non-degeneracy: two "
           "'absent' readings compare equal)",
           len(_QUERIES_DIGEST_BEFORE) == 64)

shutil.rmtree(_TMP, ignore_errors=True)
check("7c  the scratch tree is removed", os.path.exists(_TMP), False)


#------------------------------------------------------------------------------


print()
print("=" * 74)
print("RESULTS")
print("=" * 74)
print(f"passed:  {_RESULTS['passed']}")
print(f"failed:  {_RESULTS['failed']}")
print(f"skipped: {_RESULTS['skipped']}")
if _FAILURES:
    print()
    print("FAILURES")
    for item in _FAILURES:
        print("  - " + item)
if _SKIPS:
    print()
    print("SKIPS")
    for item in _SKIPS:
        print("  - " + item)
print("=" * 74)

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Sep 10 2026

@author: ramyalsaffar
"""
