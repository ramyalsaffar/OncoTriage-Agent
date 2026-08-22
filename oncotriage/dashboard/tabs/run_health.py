"""
Run Health tab (the run-reader pass).

WHY THIS TAB EXISTS. ``runs`` and ``run_metrics`` were written by the
run-identity and health-persistence passes and READ BY NOTHING. A table nobody
reads rots: the writer keeps writing it, no consumer ever contradicts it, and
the first person to look discovers that a column has meant something else since
a pass nobody connected to it. The eight other tabs read ``inferences`` and
``trial_matches``, which are per-PATIENT records; this one reads the two tables
that describe a CAMPAIGN.

IT CARRIES NO SQL. Every frame comes from ``oncotriage/storage/queries.py``
through ``oncotriage/dashboard/data.py``, so the questions this tab asks and the
questions ``python "16- Database Query.py"`` prints are the same questions and
cannot drift. That is the direction the cost tab already established when it
stopped carrying its own per-model arithmetic.

IT IS NOT AFFECTED BY THE SIDEBAR FILTERS, AND IT SAYS SO ON SCREEN.
``main()`` hands every tab ``filtered_df``. A run's patient count and a run's
cost are properties OF THE RUN, so computing them off a date- or model-filtered
frame would report a subtotal under a heading that says total -- the precise
shape of ``print_cost_by_model``'s "<- A FLOOR, NOT A TOTAL" defect, which item
38 had to fix rather than explain. So the run figures come from the database
unfiltered. ``df`` IS still read, for exactly one honest purpose: to say how much
of what the reader is currently looking at in the other eight tabs belongs to a
run, labelled as the current selection and never mixed with the run totals.

WHAT THE DATABASE CANNOT TELL YOU, said here rather than guessed at:

  * A ``RUNNING`` row with no ``finished_at`` is EITHER a live campaign OR one
    whose process was killed. There is no pid, no heartbeat and no lease in the
    schema, so the two are the same row. This tab flags it as one state named
    for that ambiguity and puts ``started_at`` beside it, which is what a reader
    actually uses -- a RUNNING row from three weeks ago is not live.
  * "No degradation rows" means EITHER "no counter moved" OR "nothing was ever
    flushed for this run". ``run_metrics``' ``counters_registered`` meta row is
    what separates them, and separating them is the reason that row is written
    at all. A clean run renders as MEASURED CLEAN, with the number of counters
    consulted, and never as an empty panel.
"""

import pandas as pd
import plotly.express as px
import streamlit as st

from oncotriage.dashboard.data import (
    RUN_TRACKING_ABSENT,
    RUN_TRACKING_NO_DATABASE,
    RUN_TRACKING_PARTIAL,
    RUN_TRACKING_PRESENT,
    load_run_attribution_data,
    load_run_degradation_data,
    load_run_summary_data,
    load_run_tracking_availability,
)
from oncotriage.storage.queries import (
    RUN_ATTRIBUTION_ATTRIBUTED,
    RUN_ATTRIBUTION_DANGLING,
    RUN_ATTRIBUTION_NO_RUN,
    RUN_FINALIZATION_FINALIZED,
    RUN_HEALTH_DEGRADED,
    RUN_HEALTH_MEASURED_CLEAN,
    RUN_HEALTH_NEVER_FLUSHED,
    RUN_HEALTH_NO_COUNTER_LABEL,
    RUN_TABLES,
)


# ---------------------------------------------------------------------------
# Literal tables, at module scope
# ---------------------------------------------------------------------------
#
# HOISTED BECAUSE THEY ARE LITERALS, and NOT hoisted where they would be derived
# or mutated -- pass 20f-4's rule, which the reproducibility tab records: a
# module-level mutable is rebuilt by every streamlit rerun and shared by every
# viewer of the server, which is what section 6a of
# tests/test_package_invariants.py exists to catch. Nothing below is mutated.

HEALTH_ICONS = {
    RUN_HEALTH_MEASURED_CLEAN: "✅",
    RUN_HEALTH_DEGRADED: "⚠️",
    RUN_HEALTH_NEVER_FLUSHED: "❔",
}

# The one sentence that says what a clean run MEANS. A panel that just showed no
# rows would be read as "nothing was recorded", which is the opposite finding.
CLEAN_STATEMENT = (
    "**Measured clean.** {registered} degradation counters were consulted "
    "while this run executed and none of them moved. This is a measurement, "
    "not an absence of one: `run_metrics` carries the counters_registered row "
    "that says the consultation happened."
)

NEVER_FLUSHED_STATEMENT = (
    "**No health record.** Nothing was ever written to `run_metrics` for this "
    "run, so nothing is known about what degraded while it executed -- which "
    "is NOT the same as knowing that nothing did. Every run that finished "
    "before the health-persistence pass is in this state, and so is a run that "
    "died before its first patient completed."
)

FINALIZATION_HELP = (
    "A run is finalized when `finished_at` is stamped. A RUNNING row with no "
    "`finished_at` is either live right now or was killed -- the database "
    "cannot tell those apart, so neither does this column. Check `started_at`."
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _as_int(value, default=0):
    """An int, or `default` when the cell is NULL.

    ``pd.isna`` and not ``is None``: a column holding numbers for some rows and
    SQL NULL for others is float64, so a NULL arrives as ``nan``, which is
    TRUTHY and is not equal to ``None``. That is the trap item 38 had to fix in
    the cost arithmetic, and it is live in every column here that a LEFT JOIN
    can leave unmatched.
    """
    if value is None or pd.isna(value):
        return default
    return int(value)


def _as_float(value, default=0.0):
    """A float, or `default` when the cell is NULL. Same ``pd.isna`` rule."""
    if value is None or pd.isna(value):
        return default
    return float(value)


def _as_text(value, default="(not recorded)"):
    """A display string, or `default` when the cell is NULL."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return default
    return str(value)


def _optional_int_text(value, default="—"):
    """An integer rendered as text, or `default` when the cell is NULL.

    A SEPARATE HELPER FROM ``_as_int`` BECAUSE THE DEFAULT MUST NOT BE A NUMBER.
    ``counters_registered`` and ``degradation_events`` are exactly the two
    columns whose NULL means "never measured", so rendering either as 0 would
    print the measured-clean answer for a run that was never asked -- the one
    confusion this whole tab exists to remove. The cell says so instead.
    """
    if value is None or pd.isna(value):
        return default
    return str(int(value))


def _health_icon(health_record):
    """The icon for a health_record value, or a visible marker for an unknown one.

    NOT a silent fallback to a neutral glyph: ``RUN_HEALTH_STATES`` is closed, so
    a value outside it means the writer emitted something this tab has never
    heard of, and rendering it as "unknown" indistinguishably from a real
    "no health record" is how that goes unnoticed.
    """
    return HEALTH_ICONS.get(health_record, "❓NEW")


def _run_label(row):
    """The selectbox label for one run row: enough to pick it without guessing."""
    return (f"#{_as_int(row.run_id)} · {_as_text(row.status)} · "
            f"{_as_text(row.started_at)} · {_as_text(row.invocation_source)}")


def _build_run_table(summary):
    """The run list, as a display frame. Pure: takes a frame, returns a frame."""
    rows = []
    for row in summary.itertuples():
        finalized = row.finalization == RUN_FINALIZATION_FINALIZED
        rows.append({
            # THE CRASHED SHAPE IS A COLUMN, NOT A FOOTNOTE. It is the first
            # column so it cannot be scrolled off a wide table.
            "": "" if finalized else "⚠️",
            "run": _as_int(row.run_id),
            "source": _as_text(row.invocation_source),
            "status": _as_text(row.status),
            "finalization": _as_text(row.finalization),
            "started": _as_text(row.started_at),
            "finished": _as_text(row.finished_at, "—"),
            "patients": _as_int(row.patients),
            "errored": _as_int(row.errored),
            "cost $": round(_as_float(row.cost_usd), 4),
            "unpriced rows": _as_int(row.rows_with_no_cost),
            "health": f"{_health_icon(row.health_record)} "
                      f"{_as_text(row.health_record)}",
            "counters consulted": _optional_int_text(row.counters_registered),
            "degradation events": _optional_int_text(row.degradation_events),
            "prompt": _as_text(row.llm_classifier_prompt_version, "—"),
            "model": _as_text(row.matching_model_configured, "—"),
            "collection": _as_text(row.qdrant_collection, "—"),
        })
    return pd.DataFrame(rows)


def _health_counts(summary):
    """``{health_record: n}`` over every run, with all three states present.

    Every state is a key even at zero, so the three metrics below always render
    and "0 degraded" is a statement rather than a missing tile.
    """
    counts = {RUN_HEALTH_MEASURED_CLEAN: 0, RUN_HEALTH_DEGRADED: 0,
              RUN_HEALTH_NEVER_FLUSHED: 0}
    for value in summary["health_record"]:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _unfinalized_count(summary):
    """How many runs carry no ``finished_at``."""
    return int((summary["finalization"] != RUN_FINALIZATION_FINALIZED).sum())


def _selection_attribution(df):
    """(with a run, without a run, column present) for the sidebar's frame.

    THE ONLY THING ``df`` IS USED FOR. It answers "how much of what I am looking
    at in the other tabs belongs to a run", which is a question about the current
    selection and is labelled as one everywhere it appears.
    """
    if df is None or df.empty or "run_id" not in df.columns:
        return (0, 0, False)
    with_run = int(df["run_id"].notna().sum())
    return (with_run, int(len(df)) - with_run, True)


def _comparison_frame(summary, value_column, label):
    """A long frame for one run-over-run bar chart, oldest run first.

    Runs with a NULL value are DROPPED and the caller says how many, rather than
    plotted at zero: a run with no health record has no degradation total, and
    drawing it as a zero bar beside a genuinely clean run states something the
    database does not.
    """
    kept = summary[summary[value_column].notna()]
    return pd.DataFrame({
        "run": [f"#{_as_int(v)}" for v in kept["run_id"]],
        label: [float(v) for v in kept[value_column]],
        "health": list(kept["health_record"]),
    }).iloc[::-1].reset_index(drop=True)


# ---------------------------------------------------------------------------
# The render
# ---------------------------------------------------------------------------


@st.fragment
def render_run_health_tab(df):
    """Render the Run Health tab."""

    st.header("🩺 Run Health")
    st.caption(
        "One row per campaign, from the `runs` and `run_metrics` tables. "
        "**These figures are NOT filtered by the sidebar** — a run's patient "
        "count and cost are properties of the run, so a filtered subtotal "
        "under a total's heading would be a wrong number. The other eight tabs "
        "honour the filters; this one deliberately does not."
    )

    availability = load_run_tracking_availability()
    state = availability["availability"]

    if state != RUN_TRACKING_PRESENT:
        _render_unavailable(availability, df)
        return

    summary = load_run_summary_data()

    if summary.empty:
        # PRESENT AND EMPTY IS ITS OWN FINDING and is not an error: the tables
        # exist, so a writer has opened this database since the run-identity
        # pass, and no campaign has been recorded through it yet.
        st.info(
            "The run tables are present and hold no rows. A writer has opened "
            "this database since run tracking was added, and no campaign has "
            "been recorded through it yet — `17- FastAPI Server.py` writes "
            "inference rows with no run id by design, so a database fed only "
            "by the API looks exactly like this."
        )
        _render_attribution(df)
        return

    _render_run_overview(summary)
    st.markdown("---")
    _render_attribution(df)
    st.markdown("---")
    _render_selected_run(summary)
    st.markdown("---")
    _render_comparison(summary)


def _render_unavailable(availability, df):
    """The three states in which this database cannot answer the run questions."""
    state = availability["availability"]
    missing = ", ".join(f"`{t}`" for t in availability["missing"]) or "—"

    if state == RUN_TRACKING_ABSENT:
        st.info(
            f"This database has no run tracking yet. {missing} are created by "
            f"`oncotriage.storage.database_logger.initialize_database`, so a "
            f"database last written before the run-identity pass does not "
            f"carry them — **the next writer to open it adds them, and nothing "
            f"is wrong with the rows that are already there.**"
        )
    elif state == RUN_TRACKING_PARTIAL:
        st.warning(
            f"⚠️ This database has the run schema in pieces "
            f"(present: {', '.join('`' + t + '`' for t in availability['tables'])}; "
            f"missing: {missing}). `initialize_database` creates "
            f"{', '.join('`' + t + '`' for t in RUN_TABLES)} **and** "
            f"`inferences.run_id` in one call, so this shape was not produced "
            f"by the pipeline and a person should look at it. Nothing is "
            f"rendered below rather than a partial answer: two of the three run "
            f"queries join on `inferences.run_id`, so with it absent they "
            f"cannot be asked at all — which is not the same as their coming "
            f"back with no rows."
        )
    elif state == RUN_TRACKING_NO_DATABASE:
        st.error(
            f"The inference database could not be read read-only"
            + (f": {availability['error']}" if availability["error"]
               else " — the file is not there.")
        )
    else:
        # NOT A CATCH-ALL WEARING AN ELSE, AND THE FIRST DRAFT OF THIS FUNCTION
        # WAS EXACTLY THAT. ``RUN_TRACKING_STATES`` is closed, so every value it
        # can hold is named above; reaching here means the loader emitted
        # something this tab has never heard of, and rendering that as "the file
        # is not there" would send an operator to look for a missing file that
        # is sitting right where it should be. The dead import that fallthrough
        # left behind -- RUN_TRACKING_NO_DATABASE named and never read -- is
        # what tests/test_package_invariants.py check 2h reported, which is the
        # scan's whole purpose restated as an event.
        st.error(
            f"The run-tracking availability check returned an unrecognised "
            f"state {state!r}. Every value in "
            f"`oncotriage.dashboard.data.RUN_TRACKING_STATES` is handled above, "
            f"so this is a defect in the loader rather than a fact about the "
            f"database."
        )

    with_run, without_run, has_column = _selection_attribution(df)
    if has_column:
        st.caption(
            f"Current sidebar selection: {with_run + without_run} inference "
            f"rows, of which {without_run} carry no run id."
        )
    else:
        st.caption(
            "Current sidebar selection: `inferences` has no `run_id` column in "
            "this database, so every row in it predates run tracking."
        )


def _render_run_overview(summary):
    """The metric row and the run list."""
    counts = _health_counts(summary)
    unfinalized = _unfinalized_count(summary)

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Runs recorded", len(summary))
    with col2:
        st.metric("Not finalized", unfinalized,
                  delta=None if unfinalized == 0 else "live or killed",
                  delta_color="off", help=FINALIZATION_HELP)
    with col3:
        st.metric("Measured clean", counts[RUN_HEALTH_MEASURED_CLEAN],
                  help="Counters were consulted and none moved.")
    with col4:
        st.metric("Degraded", counts[RUN_HEALTH_DEGRADED],
                  help="At least one degradation counter was non-zero.")
    with col5:
        st.metric("No health record", counts[RUN_HEALTH_NEVER_FLUSHED],
                  help="Nothing was flushed. This is not the same as clean.")

    st.subheader("Runs")
    st.dataframe(_build_run_table(summary), use_container_width=True,
                 hide_index=True)

    if unfinalized:
        st.warning(
            f"⚠️ {unfinalized} run(s) carry no `finished_at`. A RUNNING row "
            f"with no finish time is **either a live campaign or one whose "
            f"process was killed** — the schema has no heartbeat, so the two "
            f"are the same row. Read `started_at` beside it. A row with a "
            f"terminal status and no finish time is a different and "
            f"unambiguous finding: `finalize_run_record` writes both in one "
            f"UPDATE, so something else wrote that row."
        )
    else:
        st.success(
            "✅ Every recorded run was finalized — each one carries the "
            "`finished_at` its terminal status was stamped with."
        )


def _render_attribution(df):
    """Requirement 3: rows with no run id are grouped and counted, never dropped."""
    st.subheader("Inference rows by run attribution")
    st.caption(
        "Whole database, unfiltered. A NULL `run_id` is a **value**, not a "
        "gap: `17- FastAPI Server.py` writes one per request on purpose, "
        "because a request is not a campaign, and every row written before the "
        "run-identity pass has one too. The database cannot separate those two "
        "populations and this table does not pretend to."
    )

    attribution = load_run_attribution_data()
    if attribution.empty:
        st.info("No inference rows to attribute.")
    else:
        st.dataframe(attribution, use_container_width=True, hide_index=True)

        dangling = attribution[
            attribution["attribution"] == RUN_ATTRIBUTION_DANGLING]
        if not dangling.empty:
            st.error(
                f"❌ {int(dangling['inference_rows'].iloc[0])} inference row(s) "
                f"carry a `run_id` with no matching `runs` row. The foreign key "
                f"is unenforced by design (see the `runs` CREATE TABLE), so "
                f"this is reachable — and this census is the only thing in the "
                f"project that can report it."
            )

        no_run = attribution[
            attribution["attribution"] == RUN_ATTRIBUTION_NO_RUN]
        attributed = attribution[
            attribution["attribution"] == RUN_ATTRIBUTION_ATTRIBUTED]
        st.caption(
            f"{int(no_run['inference_rows'].iloc[0]) if not no_run.empty else 0}"
            f" row(s) belong to no recorded run; "
            f"{int(attributed['inference_rows'].iloc[0]) if not attributed.empty else 0}"
            f" row(s) do. Nothing is excluded from this table."
        )

    with_run, without_run, has_column = _selection_attribution(df)
    if has_column:
        st.caption(
            f"**Current sidebar selection** (the frame the other eight tabs "
            f"are showing): {with_run + without_run} rows, {with_run} with a "
            f"run id and {without_run} without."
        )
    else:
        st.caption(
            "**Current sidebar selection**: `inferences` has no `run_id` "
            "column here, so every selected row predates run tracking."
        )


def _render_selected_run(summary):
    """One run's degradation breakdown, with clean stated rather than blank."""
    st.subheader("One run's degradation record")

    labels = [_run_label(row) for row in summary.itertuples()]
    choice = st.selectbox("Run", labels, index=0, key="run_health_run_selector")

    # `labels.index(choice)` RAISES ValueError when the session's stored
    # selection is no longer an option -- a session that had run #7 selected,
    # a refresh, and a database in which #7 is gone. That is a render-time
    # abort inside a fragment, which streamlit surfaces as a traceback where
    # the tab should be. Falling back to the newest run is the same thing
    # `index=0` already means for a fresh session.
    try:
        position = labels.index(choice)
    except ValueError:
        position = 0
    row = summary.iloc[position]
    run_id = _as_int(row["run_id"])

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Health", f"{_health_icon(row['health_record'])} "
                            f"{_as_text(row['health_record'])}")
    with col2:
        st.metric("Counters consulted",
                  _optional_int_text(row["counters_registered"]))
    with col3:
        st.metric("Degradation events",
                  _optional_int_text(row["degradation_events"]))

    breakdown = load_run_degradation_data()
    if breakdown.empty:
        st.warning(
            "The degradation breakdown came back with no rows at all, which "
            "should be impossible while a run exists — the query is driven "
            "from `runs`, so every run is a row in it."
        )
        return

    mine = breakdown[breakdown["run_id"] == run_id]
    moved = mine[mine["counter"] != RUN_HEALTH_NO_COUNTER_LABEL]

    if row["health_record"] == RUN_HEALTH_MEASURED_CLEAN:
        st.success(CLEAN_STATEMENT.format(
            registered=_as_int(row["counters_registered"])))
    elif row["health_record"] == RUN_HEALTH_NEVER_FLUSHED:
        st.warning(NEVER_FLUSHED_STATEMENT)

    if moved.empty:
        st.caption(
            "No counter reported a non-zero total for this run. "
            "`oncotriage/degradation.py`'s `totals()` drops every zero counter, "
            "so a clean run contributes no rows here by design — the metric "
            "above is what says whether that silence was measured."
        )
        return

    st.dataframe(
        moved[["counter", "events", "written_at"]].reset_index(drop=True),
        use_container_width=True, hide_index=True)
    st.caption(
        "`events` is the SUM of one counter's keys, not a count of them: a "
        "counter hit once under twelve different keys and one hit twelve times "
        "under one key both read 12. The keys are deliberately not stored — "
        "they carry third-party and clinical text. Run the pipeline's own "
        "end-of-run block, or read the console, for those."
    )

    figure = px.bar(
        moved.sort_values("events", ascending=True),
        x="events", y="counter", orientation="h",
        title=f"Run #{run_id}: degradation events by counter",
        labels={"events": "events", "counter": ""},
    )
    figure.update_layout(height=max(240, 40 * len(moved) + 140),
                         showlegend=False)
    st.plotly_chart(figure, use_container_width=True)


def _render_comparison(summary):
    """Run over run: total degradation events, and total cost."""
    st.subheader("Run over run")

    events = _comparison_frame(summary, "degradation_events", "events")
    dropped = len(summary) - len(events)

    col1, col2 = st.columns(2)

    with col1:
        if events.empty:
            st.info(
                "No run has a degradation total to compare — every recorded "
                "run is in the 'no health record' state."
            )
        else:
            figure = px.bar(events, x="run", y="events", color="health",
                            title="Total degradation events per run")
            figure.update_layout(height=340)
            st.plotly_chart(figure, use_container_width=True)
        if dropped:
            st.caption(
                f"{dropped} run(s) are not on this chart: they have no health "
                f"record, so they have no total. They are NOT plotted at zero "
                f"— a zero bar would state that nothing degraded, which is "
                f"exactly what is not known about them."
            )

    with col2:
        cost = _comparison_frame(summary, "cost_usd", "cost_usd")
        if cost.empty:
            st.info("No run has a cost to compare.")
        else:
            figure = px.bar(cost, x="run", y="cost_usd", color="health",
                            title="Total cost per run (USD)")
            figure.update_layout(height=340)
            st.plotly_chart(figure, use_container_width=True)
        unpriced = int(summary["rows_with_no_cost"].fillna(0).sum())
        if unpriced:
            st.caption(
                f"⚠️ {unpriced} inference row(s) across all runs carry no "
                f"`estimated_cost_usd`. They contribute a real 0.0 to the bars "
                f"above, so **every cost here is a floor, not a total**, and "
                f"the number in the bar carries nothing to say so. This "
                f"sentence is what says so."
            )


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Aug 22 2026

@author: ramyalsaffar
"""
