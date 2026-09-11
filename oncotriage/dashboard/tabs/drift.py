"""
Drift Detection tab.

WHAT THE DRIFT REDESIGN CHANGED HERE, and both halves were defects rather than
presentation choices
--------------------------------------------------------------------------
1. THE FOURTH CATEGORY WAS MISSING. ``data_availability`` has been written to
   ``drift_metrics`` since the ECOG availability metric existed, and this tab
   had THREE tiles and a THREE-member category filter -- so the only ALERTING
   metric in the project was stored on every drift run and rendered by nothing.
   A reader filtering "All" saw its rows in the table and had no tile to
   notice it by.

2. EVERY NON-ALERT RENDERED AS A GREEN TICK. The status column was

       display_df_clean['alert'].apply(
           lambda x: '🚨 ALERT' if x == 1 else '✅ OK')

   over a column that the writer filled with ``0`` from every early return of
   every metric -- a PSI over a column of NaNs, a KS test with one sample a
   side, a z-score whose baseline had no variance, a metric whose column the
   database does not have. So "measured and fine" and "never measured" were one
   cell, and the second was the one that looked reassuring.

   The two state axes are rendered BESIDE the value now, through
   ``oncotriage.monitoring.drift_states.display_label`` -- the SAME owner the
   console report reads, so the tab and the terminal cannot disagree about
   whether a reading is a verdict, a value or a refusal.

3. A REPORTING-ONLY ZERO IS A VALUE, NOT AN OK. Every comparison metric ships
   reporting-only by ruling; an underfill fraction of 0.0 is the best possible
   reading and it still must not carry the alert axis's tick, because this
   project has explicitly deferred the judgement that tick asserts.

4. THE CAPTION NAMES WHAT THE READING WAS TAKEN AGAINST. A drift number with no
   reference identity beside it is a number about an unnamed population, which
   is the selection defect the redesign removed from the engine; leaving it in
   the presentation would put it back.
"""

import plotly.graph_objects as go
import streamlit as st

from oncotriage.dashboard.data import (
    load_drift_metrics_data,
    load_drift_reference_data,
)
from oncotriage.dashboard.nullsafe import as_text, is_absent
from oncotriage.monitoring.drift_states import (
    DISPLAY_ALERT,
    DISPLAY_MARKERS,
    DISPLAY_NOT_COMPUTED,
    DISPLAY_OK,
    DISPLAY_REFUSED,
    DISPLAY_REPORTING,
    METRIC_STATUSES,
    display_state,
)


# The four categories, in the order the tiles render. THE FOURTH IS THE ONE
# THIS TAB DID NOT HAVE. It is written out here rather than imported from
# `oncotriage.monitoring.drift` because a dashboard tab must not put scipy,
# numpy and the whole drift engine into a Streamlit rerun's import graph --
# `drift_states` is the leaf that carries the vocabulary and it imports nothing
# at all. The two are pinned against each other by
# tests/test_monitoring_drift_redesign.py rather than by an import.
CATEGORY_LABELS = (
    ("data_availability", "Data Availability"),
    ("data_drift", "Data Drift"),
    ("retrieval_drift", "Retrieval Drift"),
    ("performance_drift", "Performance Drift"),
)

CATEGORY_HELP = {
    "data_availability": "Are the inputs the pipeline reasoned over usable? "
                         "This family consults NO reference, so it answers "
                         "even when the designated reference is absent, "
                         "unresolvable or mutated -- and it is the only family "
                         "that raises alerts.",
    "data_drift": "Has the patient population moved? Reporting-only.",
    "retrieval_drift": "Has retrieval moved, and how much of each stage's "
                       "intake was left unfilled? Reporting-only.",
    "performance_drift": "Has match quality, timing or the error rate moved? "
                         "Reporting-only.",
}


def _row_display_state(row):
    """The display state of one ``drift_metrics`` row.

    A ROW WRITTEN BEFORE SCHEMA ERA 16 HAS NO AXES, AND IT IS NOT GUESSED AT.
    Such a row carries ``alert = 0`` whether or not anything was measured --
    which is the defect -- so there is no evidence in it from which to recover
    which reading it was. It renders ``NOT COMPUTED`` with the reason stated in
    the legacy column, rather than being shown as an OK on the strength of a
    byte this tab now knows to distrust.
    """
    status = row.get("status")
    policy = row.get("alert_policy")
    if is_absent(status) or is_absent(policy) or status not in METRIC_STATUSES:
        return DISPLAY_NOT_COMPUTED
    alert = row.get("alert")
    return display_state(str(status), str(policy),
                         None if is_absent(alert) else alert)


def _legacy_row(row):
    """Whether this row predates the two state axes."""
    return is_absent(row.get("status")) or is_absent(row.get("alert_policy"))


SELECTION_LABELS = {
    "explicit_run": "an operator named this campaign",
    "latest_opt_in": "whatever campaign ran last, asked for explicitly",
}
"""How ``drift_metrics.comparison_selection`` reads on the page.

WRITTEN OUT RATHER THAN IMPORTED, on the same footing as ``CATEGORY_LABELS``
below: a dashboard tab must not put scipy, numpy and the whole drift engine
into a Streamlit rerun's import graph, and ``oncotriage/monitoring/drift.py``
is where that vocabulary lives. The pin that keeps the two in step is in
``tests/test_monitoring_drift_redesign.py``, which may import both because a
test is in nobody's import graph.

A VALUE OUTSIDE THE MAP IS RENDERED VERBATIM AND NOT GUESSED AT. Every reading
that reaches this function came out of a database, and a row carrying something
this map does not know is a finding rather than a rendering problem.
"""


def _selection_clause(latest_run):
    """How the comparison campaign was chosen, as a clause, or ``""``.

    THE COLUMN IS WRITTEN BY EVERY DRIFT RUN AND WAS RENDERED BY NOTHING, which
    is half of a provenance repair: the row said whether a person chose the
    campaign or the tool picked it, and the page did not. That is the same shape
    as the availability tile the redesign had to add -- a value stored on every
    run and shown on none.

    ``""`` for a run that predates the column, so a historical caption gains no
    clause rather than gaining a false one.
    """
    if latest_run.empty or "comparison_selection" not in latest_run.columns:
        return ""
    values = sorted({str(v) for v in
                     latest_run["comparison_selection"].dropna().unique()})
    if not values:
        return ""
    # ONE RUN WRITES ONE SELECTION, so more than one value here is a reading
    # about two runs sharing a timestamp rather than about one run -- reported
    # rather than collapsed to the first.
    return " Comparison campaign: " + "; ".join(
        SELECTION_LABELS.get(v, f"recorded as {v!r}") for v in values) + "."


def _reference_caption(latest_run, reference_df):
    """One line naming the reference and comparison a reading was taken against.

    Reads the designation out of ``drift_reference`` by the ``reference_id``
    the drift rows carry, INCLUDING a retired one -- a caption that could only
    resolve the currently-active designation would print "unknown" over every
    historical run, which is exactly the provenance gap it exists to close.

    THE COMPARISON HALF IS APPENDED BY ``_selection_clause``. This docstring
    promised "reference AND comparison" for a pass while naming only the
    reference; the clause is what makes the sentence true.
    """
    if latest_run.empty or "reference_id" not in latest_run.columns:
        return ("This run predates the reference designation store, so what it "
                "was compared against is not recorded."
                + _selection_clause(latest_run))

    ids = sorted({int(v) for v in latest_run["reference_id"].dropna().unique()})
    if not ids:
        return ("No reading in this run consulted a reference. Either every "
                "metric is baseline-independent, or the designated reference "
                "could not be resolved -- the Status column says which."
                + _selection_clause(latest_run))

    parts = []
    for reference_id in ids:
        if reference_df.empty:
            parts.append(f"reference #{reference_id} (designation not readable "
                         f"in this database)")
            continue
        match = reference_df[reference_df["id"] == reference_id]
        if match.empty:
            parts.append(f"reference #{reference_id} (no such designation)")
            continue
        row = match.iloc[0]
        label = as_text(row.get("label"), default="")
        retired = "" if row.get("is_active") == 1 else ", retired"
        parts.append(
            f"reference #{reference_id}"
            + (f" '{label}'" if label and label != "—" else "")
            + f" — campaign runs {as_text(row.get('campaign_run_ids'))}, "
              f"{as_text(row.get('row_count'))} rows, "
              f"{as_text(row.get('patient_count'))} patients, digest "
              f"{as_text(row.get('content_digest'))[:16]}…{retired}")
    return ("Compared against " + "; ".join(parts) + "."
            + _selection_clause(latest_run))


@st.fragment
def render_drift_detection_tab(df):
    """Render drift detection monitoring tab."""

    st.header("🔬 Drift Detection")

    drift_df = load_drift_metrics_data()
    reference_df = load_drift_reference_data()

    if drift_df.empty:
        st.info("📊 No drift detection results available yet.")

        if df.empty:
            st.warning("⚠️ No inferences in database. Run the pipeline first.")
            return

        st.subheader("What drift detection needs")

        col1, col2 = st.columns(2)
        with col1:
            st.metric(
                "Inferences on file", len(df),
                help="Drift detection selects its comparison population from "
                     "the most recent campaign, so what matters is that a "
                     "campaign has run -- not how many days the table spans.")
        with col2:
            designated = (0 if reference_df.empty
                          else int((reference_df["is_active"] == 1).sum()))
            st.metric(
                "Designated references", designated,
                delta=("✅ ready" if designated else "❌ none designated"),
                help="A comparison is only meaningful against a population "
                     "somebody CHOSE. Without a designation the comparison "
                     "metrics refuse by name; availability still answers.")

        st.markdown("---")
        st.subheader("How to enable drift detection")
        st.markdown("""
        1. **Run a campaign**: `python "25- Batch Runner.py"`
        2. **Designate a reference** — the campaign every later run is measured
           against, chosen deliberately rather than by date:
           `python "20- Drift Detection.py" --designate-reference <run_id>`
        3. **Run drift detection**: `python "20- Drift Detection.py"`
        4. **Refresh** — results appear here automatically.

        **What it monitors**
        - 🩺 **Data Availability** — are the inputs usable? *The only family
          that alerts, and the only one that answers with no reference.*
        - 📊 **Data Drift** — has the patient population moved? *Reporting-only.*
        - 🔍 **Retrieval Drift** — has retrieval moved, and how much of each
          stage's intake went unfilled? *Reporting-only.*
        - ⚡ **Performance Drift** — quality, timing, error rate.
          *Reporting-only.*

        Reporting-only means the number is computed, stored and shown and
        **never raises an alert**: those thresholds are industry defaults
        calibrated against nothing in this pipeline, and calibration is
        deliberately deferred.
        """)
        return

    # === DRIFT METRICS AVAILABLE ===

    latest_timestamp = drift_df['timestamp'].max()
    latest_run = drift_df[drift_df['timestamp'] == latest_timestamp].copy()

    latest_run["display_state"] = latest_run.apply(_row_display_state, axis=1)
    latest_run["is_legacy"] = latest_run.apply(_legacy_row, axis=1)

    st.subheader("Latest Drift Detection Results")
    st.caption(f"Last run: {latest_timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
    st.caption(_reference_caption(latest_run, reference_df))

    total_alerts = int((latest_run["display_state"] == DISPLAY_ALERT).sum())
    total_metrics = len(latest_run)
    refused = int((latest_run["display_state"] == DISPLAY_REFUSED).sum())
    not_computed = int((latest_run["display_state"] == DISPLAY_NOT_COMPUTED).sum())
    reporting = int((latest_run["display_state"] == DISPLAY_REPORTING).sum())

    st.metric(
        f"{DISPLAY_MARKERS[DISPLAY_ALERT]} Total Alerts", total_alerts,
        delta=f"{total_alerts}/{total_metrics} metrics",
        help="Only a baseline-independent metric can raise an alert. Every "
             "comparison metric is reporting-only by ruling, so a zero here "
             "is NOT a statement that nothing drifted -- read the values.")
    st.caption(
        f"{DISPLAY_MARKERS[DISPLAY_REPORTING]} {reporting} reporting-only "
        f"value(s) · {DISPLAY_MARKERS[DISPLAY_REFUSED]} {refused} refused · "
        f"{DISPLAY_MARKERS[DISPLAY_NOT_COMPUTED]} {not_computed} not computed. "
        f"A refusal or a not-computed reading is never shown as OK.")

    # FOUR TILES. The fourth is `data_availability`, which this tab did not
    # have. Each counts ALERTS and states, beside it, how many readings in that
    # category were refused or not computed -- because a category whose alert
    # count is zero because nothing in it ran is not a healthy category.
    columns = st.columns(len(CATEGORY_LABELS))
    for column, (category, label) in zip(columns, CATEGORY_LABELS):
        rows = latest_run[latest_run["metric_category"] == category]
        alerts = int((rows["display_state"] == DISPLAY_ALERT).sum())
        computed = int(rows["display_state"].isin(
            [DISPLAY_OK, DISPLAY_REPORTING, DISPLAY_ALERT]).sum())
        with column:
            if rows.empty:
                marker = DISPLAY_MARKERS[DISPLAY_NOT_COMPUTED]
            elif alerts:
                marker = DISPLAY_MARKERS[DISPLAY_ALERT]
            elif computed == len(rows):
                marker = DISPLAY_MARKERS[DISPLAY_OK]
            else:
                marker = DISPLAY_MARKERS[DISPLAY_REFUSED]
            st.metric(f"{marker} {label}", alerts,
                      delta=f"{computed}/{len(rows)} computed",
                      delta_color="off",
                      help=CATEGORY_HELP.get(category, ""))

    st.markdown("---")

    # Detailed metrics table
    st.subheader("Detailed Metrics")

    category_filter = st.selectbox(
        "Filter by Category",
        ["All"] + [label for _key, label in CATEGORY_LABELS],
        key="drift_category_filter"
    )

    if category_filter == "All":
        display_df = latest_run.copy()
    else:
        category_map = {label: key for key, label in CATEGORY_LABELS}
        display_df = latest_run[
            latest_run['metric_category'] == category_map[category_filter]
        ].copy()

    # AN EMPTY SELECTION IS A TRUTHFUL STATE, NOT A CRASH (the dashboard-fixes
    # pass), AND IT IS REACHABLE ON AN ORDINARY RUN.
    #
    # `DataFrame.apply(..., axis=1)` over a frame with NO ROWS returns an empty
    # DATAFRAME carrying the source's columns rather than a Series, so the three
    # assignments below become `df["Status"] = <a frame of 20 columns>` and
    # pandas raises
    #
    #     ValueError: Cannot set a DataFrame with multiple columns to the
    #                 single column Status
    #
    # -- measured, by selecting "Data Drift" on a run holding only
    # `data_availability` rows, which is exactly what a run produces when the
    # designated reference is absent, unresolvable or mutated: the availability
    # family answers with no reference and every comparison family refuses. An
    # operator diagnosing a refused reference is therefore the reader most
    # likely to reach for this filter, and the page went down under them.
    #
    # IT IS NOT `return`. The Historical Trends section below is about
    # `drift_df` -- every run, every category -- and has nothing to do with the
    # category selected here, so returning would hide a working panel because a
    # different one is empty.
    if display_df.empty:
        st.info(
            f"**No {category_filter} reading in the latest drift run.** "
            f"The run at {latest_timestamp.strftime('%Y-%m-%d %H:%M:%S')} "
            f"recorded {len(latest_run)} reading(s), none of them in this "
            f"category. That is a statement about THIS run and not about the "
            f"metric family: a run whose designated reference could not be "
            f"resolved records the baseline-independent family and refuses "
            f"every comparison family, so the refused families contribute no "
            f"row at all. Select **All** to see what the run did record."
        )
    else:
        display_df["Status"] = display_df.apply(
            lambda row: f"{DISPLAY_MARKERS[row['display_state']]} "
                        f"{row['display_state']}", axis=1)
        display_df["Computation"] = display_df.apply(
            lambda row: "(pre-era-16 row: no state recorded)" if row["is_legacy"]
            else as_text(row.get("status")), axis=1)
        display_df["Alert policy"] = display_df.apply(
            lambda row: "(pre-era-16 row)" if row["is_legacy"]
            else as_text(row.get("alert_policy")), axis=1)

        available_cols = ['Status', 'Computation', 'Alert policy', 'metric_name',
                          'stratum', 'metric_value', 'threshold', 'p_value',
                          'z_score', 'baseline_mean', 'notes']
        display_cols = [col for col in available_cols if col in display_df.columns]

        st.dataframe(
            display_df[display_cols],
            use_container_width=True,
            hide_index=True
        )

        st.caption(
            "Each row is one monitored metric from the latest drift detection "
            "run. **Status** is the rendered state, **Computation** is what the "
            "metric actually did, and **Alert policy** is whether an alert is a "
            "possible outcome at all. A reporting-only row shows a VALUE and "
            "never an OK: its threshold is reported for comparison and is not "
            "applied. `stratum` is the cancer group a reading is over; blank "
            "means the whole population."
        )

    st.markdown("---")

    # Time-series plots
    st.subheader("Historical Trends")

    unique_runs = drift_df['timestamp'].nunique()

    if unique_runs < 2:
        st.info("📈 Run drift detection multiple times to see historical trends.")
        return

    metric_names = drift_df['metric_name'].unique()

    selected_metric = st.selectbox(
        "Select Metric to Plot",
        sorted(metric_names),
        key="drift_metric_select"
    )

    metric_data = drift_df[
        drift_df['metric_name'] == selected_metric
    ].sort_values('timestamp').copy()

    if metric_data.empty:
        st.warning(f"No data available for {selected_metric}")
        return

    metric_data["display_state"] = metric_data.apply(_row_display_state, axis=1)

    # A POINT IS ONLY PLOTTED WHERE THERE IS A VALUE. A metric that could not be
    # computed has `metric_value` NULL, and plotly's default is to bridge the
    # gap -- drawing a line straight through the runs where the reading does
    # not exist, which reads as continuity that was never measured.
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=metric_data['timestamp'],
        y=metric_data['metric_value'],
        mode='lines+markers',
        name='Metric Value',
        connectgaps=False,
        line=dict(color='#1f77b4', width=2),
        marker=dict(size=8)
    ))

    if 'threshold' in metric_data.columns and metric_data['threshold'].notna().any():
        threshold_values = metric_data['threshold'].dropna().unique()
        if len(threshold_values) == 1:
            threshold_value = threshold_values[0]
            reporting_only = bool(
                (metric_data.get("alert_policy") == "reporting_only").any()
            ) if "alert_policy" in metric_data.columns else False
            fig.add_hline(
                y=threshold_value,
                line_dash="dash",
                line_color="#999999" if reporting_only else "#d62728",
                annotation_text=(f"Threshold: {threshold_value}"
                                 + (" (reported, not applied)"
                                    if reporting_only else "")),
                annotation_position="right"
            )

    alert_points = metric_data[metric_data["display_state"] == DISPLAY_ALERT]
    if not alert_points.empty:
        fig.add_trace(go.Scatter(
            x=alert_points['timestamp'],
            y=alert_points['metric_value'],
            mode='markers',
            name='Alerts',
            marker=dict(size=15, color='#d62728', symbol='x',
                        line=dict(width=2))
        ))

    # THE RUNS WHERE THE METRIC DID NOT COMPUTE ARE MARKED ON THE AXIS. Without
    # them a gap in the line reads as "no drift run happened", when what it
    # actually says is "this metric refused on that run" -- which is the
    # finding.
    missing = metric_data[metric_data['metric_value'].isna()]
    if not missing.empty:
        fig.add_trace(go.Scatter(
            x=missing['timestamp'],
            y=[0] * len(missing),
            mode='markers',
            name='Not computed',
            marker=dict(size=10, color='#999999', symbol='line-ns-open',
                        line=dict(width=2))
        ))

    fig.update_layout(
        title=f"{selected_metric.replace('_', ' ').title()} Over Time",
        xaxis_title="Date",
        yaxis_title="Value",
        hovermode='x unified',
        height=400,
        margin=dict(l=20, r=20, t=40, b=20),
        template='plotly_white'
    )

    st.plotly_chart(fig, use_container_width=True)

    # Statistics over the runs that ACTUALLY COMPUTED. A mean over a column
    # whose NULLs mean "not computed" is a mean over a population nobody chose;
    # pandas skips them silently, so the count is stated beside the numbers.
    computed = metric_data[metric_data['metric_value'].notna()]
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Latest Value",
                  f"{computed['metric_value'].iloc[-1]:.4f}"
                  if not computed.empty else "not computed",
                  help="The most recent run in which this metric computed.")
    with col2:
        st.metric("Mean",
                  f"{computed['metric_value'].mean():.4f}"
                  if not computed.empty else "not computed",
                  help="Across the runs in which this metric computed.")
    with col3:
        st.metric("Std Dev",
                  f"{computed['metric_value'].std():.4f}"
                  if len(computed) > 1 else "not computed",
                  help="Needs at least two runs in which this metric computed.")

    st.caption(
        f"{len(computed)} of {len(metric_data)} runs computed this metric; the "
        f"statistics above are over those {len(computed)}. Grey ticks on the "
        f"axis mark the runs where it did not. Red ✕ marks an alert — only a "
        f"baseline-independent metric can carry one."
    )


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
