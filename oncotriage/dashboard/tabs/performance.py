"""
Performance tab. Moved verbatim out of "21- Streamlit Dashboard.py" (pass 20c-3c-1).
"""

from plotly.subplots import make_subplots
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from oncotriage.config import MAX_TRIALS_FOR_EVALUATION
from oncotriage.dashboard import call_mode
from oncotriage.dashboard.data import load_trial_matches_data
from oncotriage.dashboard.nullsafe import is_absent
from oncotriage.dashboard.tiers import (MATCH_TIER_COLORS, TRIAL_STATUS_NO_SCORE,
                                        classify_trial_score)
# THE RATE IS NOT REIMPLEMENTED HERE. oncotriage/monitoring/drift.py already
# owns the definition -- numerator, denominator and the three exclusions -- and
# it is the definition the drift alert fires on. A second copy in the dashboard
# is a second copy to drift, and the two disagreeing about what "unavailable"
# means is precisely the failure that would make this panel worse than nothing.
# Importing it costs no filesystem work: drift.py resolves its paths lazily.
from oncotriage.constants import (
    ECOG_SELECTION_ALL_AFTER_REFERENCE,
    ECOG_SELECTION_ALL_BEFORE_PRIMARY_DIAGNOSIS,
    ECOG_SELECTION_MOST_RECENT,
    ECOG_SELECTION_NONE_RECORDED,
    ECOG_SELECTION_UNDATED_AMBIGUOUS,
    ECOG_SELECTION_UNDATED_SINGLE,
    ECOG_SELECTION_VALUES,
)
from oncotriage.monitoring.drift import (
    ECOG_UNAVAILABLE_RATE_THRESHOLD,
    ecog_unavailable_rate,
)


# ===========================================================================
# THE SCORE-DENSITY CURVE
# ===========================================================================


def _kde_curve(scores):
    """(x, y) for a Gaussian KDE over `scores`, or ``None`` when it has none.

    HOISTED OUT OF ``render_performance_tab`` (the campaign pass). It closed
    over nothing and took two arguments -- `color` and `name` -- that its body
    never read, so nesting it bought nothing and cost the only thing that
    matters here: it could not be driven, and therefore could not be shown to
    survive the input that crashed it.

    ``None`` MEANS "THERE IS NO CURVE TO DRAW", AND THERE ARE TWO WAYS TO GET
    THERE. The caller skips the trace either way; the difference is only in
    which one used to take the whole page down.

      * FEWER THAN THREE POINTS. Guarded since the tab was written -- a KDE
        over one or two observations is not a density estimate.

      * ZERO VARIANCE, WHICH WAS NOT GUARDED AND IS NOT RARE. ``gaussian_kde``
        raises ``numpy.linalg.LinAlgError`` ("the data appears to lie in a
        lower-dimensional subspace ... singular data covariance matrix") on any
        input whose values are all identical, and this is a REACHABLE state
        rather than a pathological one: `rerank_score` is NULL for every trial
        of a run that fell back to BM25-only, so the frame is filled with one
        value; a cohort with two trials in one arm scoring identically does it
        too. The raise happens inside ``render_performance_tab``, which
        ``main()`` calls with no handler, so ONE such arm took out ALL TEN TABS
        before the reader saw anything.

    THE TEST IS ``ptp() == 0`` AND NOT ``std() == 0``, and the difference is
    real. ``std`` of three identical values is a float computed through a sum
    of squares and can come back as a denormal rather than an exact zero, so a
    ``== 0`` on it can be False for input the estimator still refuses; the peak
    to peak of identical values is exactly ``0`` by construction. It is also
    the cheaper of the two.

    A NON-FINITE VALUE IS REFUSED FOR THE SAME REASON. ``np.ptp`` over an array
    holding ``inf`` returns ``nan``, which compares False against ``0`` and
    would slip past a bare equality test straight into the estimator; and
    ``np.linspace`` around an infinity produces no usable range even when the
    estimator does not raise.
    """
    scores = np.asarray(scores, dtype=float)
    if scores.size < 3:
        return None
    if not np.all(np.isfinite(scores)):
        return None
    if np.ptp(scores) == 0:
        return None
    from scipy.stats import gaussian_kde
    kde = gaussian_kde(scores, bw_method='scott')
    x_range = np.linspace(scores.min() - 0.05, scores.max() + 0.05, 300)
    y_range = kde(x_range)
    return x_range, y_range


# ===========================================================================
# ECOG AVAILABILITY
# ===========================================================================
#
# NOTHING IN THE DASHBOARD READ ecog_value, ecog_selection OR
# ecog_observations_found BEFORE THIS. Measured, not assumed: a repo-wide grep
# for all three names returns hits only in tests/, in oncotriage/monitoring/,
# in oncotriage/storage/ and in "Exception and Fallback Audit.md". The two
# dashboard hits for the string "ecog" are keyword lists that scan GPT-4o's
# free-text explanations (overview.py's clinical-gap categories,
# match_quality.py's criterion categories) -- they read what the JUDGE SAID
# about a missing performance status, never what the PIPELINE RECORDED about
# one, so they cannot distinguish "no ECOG on file" from "an ECOG existed and
# the reference date made it unusable".
#
# WHY IT BELONGS BESIDE THE RETRIEVAL QUALITY METRICS. When
# DATA_SNAPSHOT_DATE drifts past the corpus, every ECOG observation falls after
# the reference date, ecog_selection becomes 'all_after_reference_date',
# ecog_value goes NULL, and every ECOG criterion in Stage 5 becomes
# not_evaluable. Eligible matches fall across the board -- and the panels that
# show the fall had nothing beside them naming the cause. This row is that
# explanation.
#
# THE THREE-WAY DISTINCTION IS THE WHOLE POINT and the panel refuses to
# collapse it. ecog_value IS NULL means one of three different things:
#   selection NULL                          the row predates the migration
#   selection 'none_recorded'               the patient genuinely had none
#   selection 'all_after_reference_date' |  an observation existed and could
#             'undated_ambiguous'           not be used  <- the actionable one
# and ecog_value = 0 is a real, fully active patient -- the most eligible there
# is -- so it is never treated as missing.

def _render_ecog_availability(df):
    """ECOG unavailable rate + selection-path breakdown, as a metrics row."""
    st.subheader("🩺 ECOG Performance Status Availability")

    if 'ecog_selection' not in df.columns:
        st.info(
            "This database predates the `ecog_*` columns in "
            "`oncotriage/storage/database_logger.py`, so nothing here can be "
            "said about ECOG availability. Not 0% — unknown."
        )
        return

    result = ecog_unavailable_rate(df)
    rate = result["metric_value"]
    denominator = result["denominator"]
    pre_migration = result["rows_pre_migration"] or 0

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        # None is rendered as "n/a", NEVER as 0%. ecog_unavailable_rate returns
        # None when the denominator is below the drift module's own minimum,
        # and "no usable sample" and "nothing was unavailable" are opposite
        # findings that would print identically as 0%.
        st.metric(
            "ECOG Unavailable Rate",
            f"{rate * 100:.1f}%" if rate is not None else "n/a",
            delta=(f"alert: > {ECOG_UNAVAILABLE_RATE_THRESHOLD * 100:.0f}%"
                   if result["alert"] else None),
            delta_color="inverse",
            help="Of the rows that REPORT an ECOG selection path, the fraction "
                 "that had an observation on file which could not be used. "
                 "Patients who genuinely carried no ECOG are in the "
                 "denominator and not in the numerator — counting them as "
                 "unavailable would make a cohort that never had the data look "
                 "like a pipeline fault. Same definition the drift alert uses."
        )

    with col2:
        st.metric(
            "Rows Reporting",
            f"{denominator:,}",
            delta=f"{pre_migration:,} pre-migration" if pre_migration else None,
            delta_color="off",
            help="Rows with a non-NULL ecog_selection. Rows without one are "
                 "excluded from the denominator entirely: they predate the "
                 "migration, so nothing is known about their ECOG, and "
                 "counting them as fine would dilute the rate."
        )

    with col3:
        _unusable = result["numerator"]
        st.metric(
            "Observation Present but Unusable",
            f"{_unusable:,}" if _unusable is not None else "n/a",
            help="The numerator. An ECOG observation existed and was rejected "
                 "— almost always because every one of them is dated after "
                 "DATA_SNAPSHOT_DATE. Every ECOG criterion for these patients "
                 "becomes not_evaluable in Stage 5."
        )

    with col4:
        _scored = int(df['ecog_value'].notna().sum()) \
            if 'ecog_value' in df.columns else 0
        st.metric(
            "Patients with a Usable Score",
            f"{_scored:,}",
            help="Rows carrying an actual ECOG grade. NOTE: a grade of 0 is "
                 "counted here — ECOG 0 is 'fully active', the most eligible a "
                 "patient can be, and treating it as missing is the single "
                 "most common way to misread this column."
        )

    # --- The selection-path breakdown ---------------------------------------
    #
    # The rate above says HOW MUCH; this says WHICH PATH, which is what decides
    # what an operator does next. 'all_after_reference_date' points at
    # DATA_SNAPSHOT_DATE; 'none_recorded' points at the cohort;
    # 'undated_ambiguous' points at the bundles.
    #
    # KEYED OFF oncotriage.constants, NOT OFF RETYPED STRINGS, and that is a
    # correction rather than a style choice. This table used to carry
    # "most_recent_on_or_before_reference" -- no trailing `_date` -- while
    # oncotriage/fhir/parser.py has always written
    # "most_recent_on_or_before_reference_date". So THE SINGLE MOST COMMON PATH
    # IN THE PIPELINE rendered as "unrecognised path -- not one of the five this
    # pipeline writes", on every dashboard, for every corpus, and nothing
    # failed: the fallback message is the only place a wrong key surfaces, and
    # it reads like a data problem rather than like a typo here. A constant
    # cannot drift from itself, which is why the fix is the import and not a
    # corrected literal.
    _PATH_MEANING = {
        ECOG_SELECTION_MOST_RECENT:
            "usable — the most recent observation dated on or before the "
            "reference date",
        ECOG_SELECTION_UNDATED_SINGLE:
            "usable — one undated observation, taken as the patient's",
        ECOG_SELECTION_NONE_RECORDED:
            "no ECOG observation existed for this patient at all",
        ECOG_SELECTION_ALL_AFTER_REFERENCE:
            "UNUSABLE — every observation is dated AFTER "
            "DATA_SNAPSHOT_DATE. Check the snapshot date against the corpus.",
        ECOG_SELECTION_UNDATED_AMBIGUOUS:
            "UNUSABLE — several undated observations and no way to order them",
        ECOG_SELECTION_ALL_BEFORE_PRIMARY_DIAGNOSIS:
            "UNUSABLE — every observation predates the patient's primary "
            "cancer diagnosis, so none of them describes the patient WITH the "
            "disease. Not a snapshot-date problem and not a staleness one: an "
            "old post-diagnosis score is still kept.",
    }

    # THE TABLE MUST COVER THE WHOLE VOCABULARY, or a member added to
    # oncotriage/constants.py renders under the "unrecognised path" message that
    # exists for a producer defect. Checked here rather than left to a test
    # because this panel is what an operator reads, and a missing explanation is
    # worse than a loud one: it says the pipeline wrote something it should not
    # have when in fact this file simply has not been told about it.
    _unexplained = [v for v in ECOG_SELECTION_VALUES if v not in _PATH_MEANING]
    if _unexplained:
        st.warning(
            "This panel has no explanation for "
            f"{', '.join(_unexplained)} — a selection path was added to "
            "`oncotriage/constants.py` and not to `_PATH_MEANING` in "
            "`oncotriage/dashboard/tabs/performance.py`. The breakdown below "
            "is still complete; only the explanations are missing."
        )

    counts = df['ecog_selection'].value_counts(dropna=False)
    total = int(counts.sum())
    rows = []
    for path, n in counts.items():
        label = "(not reported — row predates the migration)" \
            if pd.isna(path) else str(path)
        rows.append({
            "Selection Path": label,
            "Patients": int(n),
            "% of Rows": round(n / total * 100, 1) if total else 0.0,
            "What it means": _PATH_MEANING.get(
                label, "unrecognised path — not one of the five this pipeline "
                       "writes; check oncotriage/fhir/parser.py"),
        })

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # THE ALERT FLAG DECIDES THE SEVERITY, NOT THE PRESENCE OF A NOTE.
    # ecog_unavailable_rate() writes `notes` on two quite different occasions:
    # when the rate crossed the threshold (alert=1) and when it could not be
    # computed at all (alert=0, metric_value None). Rendering both as
    # st.warning -- which the first version of this panel did, found by driving
    # the two-row scenario rather than by reading -- paints a red box over "we
    # only have 2 rows", which reads as a pipeline fault and is not one.
    if result["notes"]:
        if result["alert"]:
            st.warning(result["notes"])
        else:
            st.info(result["notes"])

    st.caption(
        "A high unavailable rate does not mean the patients are ineligible — "
        "it means the pipeline could not tell. Eligible matches fall across "
        "every panel below when this rises, and the cause is upstream of "
        "retrieval: it is a reference-date or corpus-date question, not a "
        "ranking one."
    )

    st.markdown("---")


def render_performance_tab(df):
    """Render Performance tab with latency analysis."""

    # THE JUDGE COMES FROM THE DATA, never from MATCHING_MODEL and never from a
    # literal, exactly as the cost breakdown does it: this table can hold rows
    # from more than one judge, and naming the configured one relabels history
    # every time the config moves. "GPT-4o" was written into three trace names,
    # two axis titles, a chart title, a stage label and four captions while the
    # configured judge was something else.
    #
    # RESOLVED AT THE TOP OF THE FUNCTION, not beside the first chart that uses
    # it. The first use is the latency histogram immediately below, ~250 lines
    # above the retrieval-quality block where an earlier version of this edit
    # put the assignment -- a NameError on every render, caught by running the
    # tab rather than by reading it.
    # MEASURED ON THE REAL DATABASE, 2026-08-07: this table holds TWO judges,
    # gpt-4o-2024-08-06 and gpt-5.6-terra. The literal "GPT-4o" these labels
    # carried was therefore not merely stale -- it was actively wrong about
    # every gpt-5.6-terra row on the same chart.
    _judges_present = sorted(
        str(m) for m in df['matching_model'].dropna().unique()
    ) if 'matching_model' in df.columns else []

    # Named while the list is short enough to read; counted once it is not. An
    # axis title is not a place to enumerate eight model IDs, and truncating to
    # the FIRST two would name some judges on a chart that plots all of them --
    # which is the same defect as the literal, in a smaller font.
    if not _judges_present:
        _judge = "Stage 5 judge"
    elif len(_judges_present) <= 2:
        _judge = " / ".join(_judges_present)
    else:
        _judge = f"{len(_judges_present)} Stage 5 judges"

    st.header("⚡ Pipeline Performance")

    # Latency Overview
    st.subheader("Latency Distribution")
    
    col1, col2 = st.columns(2)
    
    with col1:
        fig_latency = px.histogram(
            df,
            x='total_time',
            nbins=30,
            labels={'total_time': 'Total Time (seconds)'},
            template='plotly_white',
            title='Total Pipeline Latency'
        )
        fig_latency.add_vline(
            x=df['total_time'].median(),
            line_dash="dash",
            line_color="red"
        )
        fig_latency.add_annotation(
            x=df['total_time'].median(), y=1, yref="paper",
            text=f"Median: {df['total_time'].median():.1f}s",
            showarrow=True, arrowhead=0, ax=45, ay=-25,
            font=dict(size=11, color="red"),
            bgcolor="white", borderpad=2
        )
        fig_latency.update_layout(
            height=300,
            margin=dict(l=20, r=20, t=40, b=20),
            showlegend=False
        )
        st.plotly_chart(fig_latency, use_container_width=True)
    
    with col2:
        fig_llm_classifier = px.histogram(
            df,
            x='llm_classifier_evaluation_time',
            nbins=30,
            labels={'llm_classifier_evaluation_time': f'{_judge} Time (seconds)'},
            template='plotly_white',
            title=f'{_judge} Evaluation Latency'
        )
        fig_llm_classifier.add_vline(
            x=df['llm_classifier_evaluation_time'].median(),
            line_dash="dash",
            line_color="red"
        )
        fig_llm_classifier.add_annotation(
            x=df['llm_classifier_evaluation_time'].median(), y=1, yref="paper",
            text=f"Median: {df['llm_classifier_evaluation_time'].median():.1f}s",
            showarrow=True, arrowhead=0, ax=45, ay=-25,
            font=dict(size=11, color="red"),
            bgcolor="white", borderpad=2
        )
        fig_llm_classifier.update_layout(
            height=300,
            margin=dict(l=20, r=20, t=40, b=20),
            showlegend=False
        )
        st.plotly_chart(fig_llm_classifier, use_container_width=True)
    
    # Latency stats
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric(
            "Median Total Time",
            f"{df['total_time'].median():.1f}s",
            help="Median end-to-end pipeline latency"
        )
    
    with col2:
        st.metric(
            "95th Percentile",
            f"{df['total_time'].quantile(0.95):.1f}s",
            help="95% of patients complete within this time"
        )
    
    with col3:
        st.metric(
            "Max Latency",
            f"{df['total_time'].max():.1f}s",
            help="Slowest patient processing time"
        )

    with col4:
        throughput = 3600 / df['total_time'].median() if df['total_time'].median() > 0 else 0
        st.metric(
            "Throughput",
            f"{throughput:.0f}/hour",
            help="Estimated sequential patients per hour (3600 / median latency)"
        )
    
    st.markdown("---")
    
    # --- Latency Over Time ---
    st.subheader("Latency Over Time")
    
    daily = df.groupby(df['timestamp'].dt.date).agg(
        median_latency=('total_time', 'median'),
        p25_latency=('total_time', lambda x: x.quantile(0.25)),
        p75_latency=('total_time', lambda x: x.quantile(0.75)),
        inference_count=('total_time', 'count')
    ).reset_index()
    daily.columns = ['Date', 'Median', 'P25', 'P75', 'Inferences']
    daily = daily.sort_values('Date')
    
    fig_trend = make_subplots(specs=[[{"secondary_y": True}]])
    
    # Background: daily inference volume (gray bars, right y-axis)
    fig_trend.add_trace(
        go.Bar(
            x=daily['Date'],
            y=daily['Inferences'],
            name='Inferences',
            marker_color='rgba(200, 200, 200, 0.5)',
            hovertemplate='%{y} inferences<extra></extra>',
        ),
        secondary_y=True,
    )
    
    # Shaded band: P25-P75 (only if more than one day has variance)
    if len(daily) >= 1:
        fig_trend.add_trace(
            go.Scatter(
                x=pd.concat([daily['Date'], daily['Date'][::-1]]).reset_index(drop=True),
                y=pd.concat([daily['P75'], daily['P25'][::-1]]).reset_index(drop=True),
                fill='toself',
                fillcolor='rgba(31, 119, 180, 0.15)',
                line=dict(color='rgba(0,0,0,0)'),
                name='P25–P75 Range',
                hoverinfo='skip',
                showlegend=True,
            ),
            secondary_y=False,
        )
    
    # Primary line: median latency (left y-axis)
    fig_trend.add_trace(
        go.Scatter(
            x=daily['Date'],
            y=daily['Median'],
            mode='lines+markers',
            name='Median Latency',
            line=dict(color='#1f77b4', width=2.5),
            marker=dict(size=8),
            hovertemplate='%{x}<br>Median: %{y:.1f}s<extra></extra>',
        ),
        secondary_y=False,
    )
    
    fig_trend.update_layout(
        height=350,
        margin=dict(l=20, r=20, t=20, b=20),
        template='plotly_white',
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        hovermode='x unified',
    )
    fig_trend.update_xaxes(
        title_text="",
        tickformat="%b %d",
    )
    fig_trend.update_yaxes(
        title_text="Latency (seconds)",
        secondary_y=False,
        rangemode='tozero',
    )
    fig_trend.update_yaxes(
        title_text="Inferences",
        secondary_y=True,
        rangemode='tozero',
        showgrid=False,
    )
    
    st.plotly_chart(fig_trend, use_container_width=True)
    
    st.caption(
        "Daily median pipeline latency with P25–P75 range. "
        "Gray bars show daily inference volume. "
        "Rising latency may indicate API degradation, increased trial database size, or more complex patient profiles."
    )
    
    st.markdown("---")
    
    # Stage Bottleneck Analysis
    st.subheader("Stage-Level Bottleneck Analysis")
    
    stage_cols = [
        'hybrid_retrieval_time',
        'cross_encoder_time',
        'rule_filter_time',
        'llm_classifier_evaluation_time'
    ]
    
    stage_labels = [
        'Hybrid Retrieval',
        'Cross-Encoder',
        'Rule Filter',
        f'{_judge} Evaluation'
    ]
    
    median_times = [df[col].median() for col in stage_cols]
    max_times = [df[col].max() for col in stage_cols]
    
    col1, col2 = st.columns(2)
    
    with col1:
        fig_median = go.Figure(data=[
            go.Bar(
                x=median_times,
                y=stage_labels,
                orientation='h',
                marker_color=['#1f77b4', '#ff7f0e', '#d62728', '#9467bd'],
                text=[f"{t:.2f}s" for t in median_times],
                textposition='outside'
            )
        ])
        fig_median.update_layout(
            title='Median Stage Latencies',
            height=300,
            margin=dict(l=20, r=20, t=40, b=20),
            xaxis_title="Seconds",
            yaxis_title="",
            template='plotly_white',
            showlegend=False
        )
        fig_median.update_xaxes(range=[0, max(median_times) * 1.2])
        st.plotly_chart(fig_median, use_container_width=True)
    
    with col2:
        fig_max = go.Figure(data=[
            go.Bar(
                x=max_times,
                y=stage_labels,
                orientation='h',
                marker_color=['#1f77b4', '#ff7f0e', '#d62728', '#9467bd'],
                text=[f"{t:.2f}s" for t in max_times],
                textposition='outside'
            )
        ])
        fig_max.update_layout(
            title='Maximum Stage Latencies',
            height=300,
            margin=dict(l=20, r=20, t=40, b=20),
            xaxis_title="Seconds",
            yaxis_title="",
            template='plotly_white',
            showlegend=False
        )
        fig_max.update_xaxes(range=[0, max(max_times) * 1.2])
        st.plotly_chart(fig_max, use_container_width=True)
    
    st.markdown("---")
    
    # Slowest Patients Table
    st.subheader("Slowest Patients (Top 10)")
    
    # THE CALL MODE COLUMN IS HERE BECAUSE TWO OF THIS TABLE'S COLUMNS ARE
    # DECIDED BY IT. `{_judge} Time (s)` and `Output Tokens` are per-patient
    # figures whose magnitude follows the arm -- per-trial issues one billed
    # request per patient-trial pair behind a warmup, grouped one per packed
    # chunk -- so a reader comparing row 1 with row 7 in a table holding both
    # arms is comparing two different measurements.
    #
    # THIS TABLE IS NOT AN AGGREGATE, so it is not split and carries no mixed-
    # mode warning: every row stands for itself, and naming the arm ON the row
    # is the whole repair. Splitting a top-10-by-latency listing by mode would
    # turn one ranking into two and answer a question nobody asked.
    _slow_cols = ['patient_id', 'age', 'sex', 'condition_count',
                  'medication_count', 'candidates_evaluated', 'total_time',
                  'llm_classifier_evaluation_time',
                  'llm_classifier_output_tokens']
    _slow_names = ['Patient ID', 'Age', 'Sex', 'Conditions', 'Medications',
                   'Trials Evaluated', 'Total Time (s)', f'{_judge} Time (s)',
                   'Output Tokens']

    slowest = df.nlargest(10, 'total_time').copy()
    # ANNOTATE BEFORE SLICING, and from a COPY: `annotate` derives the display
    # bucket through the same mapping every other panel groups by, including
    # the column-absent case, so a database predating era 3 renders the
    # not-recorded bucket here rather than raising a KeyError.
    slowest = call_mode.annotate(slowest)[_slow_cols + ['call_mode_label']]
    slowest.insert(0, 'Rank', range(1, len(slowest) + 1))

    slowest.columns = ['Rank'] + _slow_names + ['Call Mode']
    
    slowest['Total Time (s)'] = slowest['Total Time (s)'].round(1)
    slowest[f'{_judge} Time (s)'] = slowest[f'{_judge} Time (s)'].round(1)
    
    st.dataframe(
        slowest,
        use_container_width=True,
        hide_index=True
    )
    
    st.caption(
        "Top 10 patients by total end-to-end pipeline latency. "
        f"Rank 1 = slowest. High latency typically correlates with more trials "
        f"evaluated or larger {_judge} output."
    )

    st.markdown("---")

    # ── Retrieval Quality Analysis ─────────────────────────────────────────
    #
    # WHAT IS ACTUALLY PLOTTED IN THIS WHOLE SECTION, and it is not what the
    # labels used to say. trial_matches.rerank_score is Stage 3's FUSED RRF
    # score AFTER the MeSH relevance boost -- oncotriage/agent/terminal.py
    # writes the boosted value into that column and rerank_score_raw beside it.
    # It is not a MedCPT score. Every chart here read "Rerank Score" and every
    # caption attributed it to MedCPT, which is true of the input to the fusion
    # and false of the number on the axis: a fused RRF value is a function of
    # pool size and query count, so the axis is not a relevance scale and a
    # cutoff on it does not mean what a cutoff on a MedCPT score would.
    #
    # THE LABEL IS A CONSTANT, ONCE. Six charts, axes and captions named the
    # quantity; they now cannot disagree with each other.
    _SCORE_LABEL = "Fused Rerank Score (RRF, MeSH-boosted)"

    _render_ecog_availability(df)

    st.subheader(f"🎯 Retrieval Quality: {_SCORE_LABEL} vs Match Outcome")

    trial_matches_perf = load_trial_matches_data()

    if trial_matches_perf is not None and not trial_matches_perf.empty:
        filtered_ids = df['id'].tolist()
        tm_perf = trial_matches_perf[
            trial_matches_perf['inference_id'].isin(filtered_ids)
        ].copy()

        if not tm_perf.empty and 'rerank_score' in tm_perf.columns:

            # Classify match status. An eligible trial scoring exactly 0.0 gets
            # its own bucket: nothing about it was confirmable, so grouping it
            # with a 90%-confirmed trial would put two different findings on the
            # same point of the rerank-score axis.
            def classify_match(row):
                if row['eligible'] != 'eligible':
                    return 'Not Eligible'
                # ABSENCE FIRST -- see TRIAL_STATUS_NO_SCORE in tiers.py.
                # `classify_trial_score` RAISES TypeError on a None
                # `match_score`, with no handler between here and main(), so a
                # single such row took every tab down.
                if is_absent(row.get('match_score')):
                    return TRIAL_STATUS_NO_SCORE
                tier = classify_trial_score(row['match_score'])
                return 'Eligible' if tier == 'Full Match' else tier

            tm_perf['match_status'] = tm_perf.apply(classify_match, axis=1)
            # UNSCORED TRIALS ARE EXCLUDED FROM THIS PANEL AND COUNTED, NOT
            # GIVEN A COLOUR. Every chart below relates the cross-encoder score
            # to the OUTCOME -- recall against a threshold, density by outcome,
            # recall against a top-N cap -- and a trial with no outcome score
            # has no outcome to relate; plotting it as a fifth series would put
            # a band on the recall curve that no denominator accounts for. The
            # exclusion is stated in the caption rather than made silently,
            # which is the same ruling `_comparison_frame` makes in the Run
            # Health tab for a run with no degradation total.
            _unscored = int((tm_perf['match_status'] == TRIAL_STATUS_NO_SCORE).sum())
            if _unscored:
                tm_perf = tm_perf[tm_perf['match_status'] != TRIAL_STATUS_NO_SCORE]
                st.caption(
                    f"⚠️ {_unscored} trial row(s) in the current selection carry "
                    f"no `match_score` and are EXCLUDED from every chart in this "
                    f"panel — a trial with no recorded outcome cannot contribute "
                    f"to a recall or a density by outcome. They are not counted "
                    f"as not-eligible."
                )
            tm_perf = tm_perf.dropna(subset=['rerank_score'])

            color_map = {
                'Eligible':          '#2ca02c',
                'Partial Match':     '#ff7f0e',
                'Unconfirmed Match': MATCH_TIER_COLORS['Unconfirmed Match'],
                'Not Eligible':      '#d62728'
            }

            # ── Row 1: Recall vs Cost Tradeoff (full width) ──────────────────
            matches_only = tm_perf[tm_perf['eligible'] == 'eligible'].copy()
            all_trials   = tm_perf.copy()

            if not matches_only.empty:
                thresholds = sorted(tm_perf['rerank_score'].unique())
                recall_data = []
                total_matches = len(matches_only)

                for t in thresholds:
                    captured   = (matches_only['rerank_score'] >= t).sum()
                    total_sent = (all_trials['rerank_score'] >= t).sum()
                    recall_data.append({
                        'Rerank Threshold': t,
                        'Match Recall (%)': round(captured / total_matches * 100, 1) if total_matches > 0 else 0,
                        'Trials Sent (%)':  round(total_sent / len(all_trials) * 100, 1) if len(all_trials) > 0 else 0
                    })

                recall_df = pd.DataFrame(recall_data)

                fig_recall = go.Figure()
                fig_recall.add_trace(go.Scatter(
                    x=recall_df['Rerank Threshold'],
                    y=recall_df['Match Recall (%)'],
                    mode='lines',
                    name='Match Recall',
                    line=dict(color='#2ca02c', width=2.5)
                ))
                fig_recall.add_trace(go.Scatter(
                    x=recall_df['Rerank Threshold'],
                    y=recall_df['Trials Sent (%)'],
                    mode='lines',
                    name=f'Trials Sent to {_judge}',
                    line=dict(color='#1f77b4', width=2.5, dash='dash')
                ))
                
                fig_recall.update_layout(
                    title=f'Recall vs Cost Tradeoff by {_SCORE_LABEL} Cutoff',
                    height=350,
                    margin=dict(l=20, r=20, t=40, b=20),
                    template='plotly_white',
                    xaxis_title=f'{_SCORE_LABEL} — cutoff',
                    yaxis_title='Percentage (%)',
                    legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
                    hovermode='x unified'
                )
                
                try:
                    # Safe score threshold: lowest threshold where recall >= 95%
                    RECALL_FLOOR = 95.0
                    patients_count = tm_perf['inference_id'].nunique()
                    safe_threshold = None
                    safe_trials_sent = None
                    safe_recall = None
                    prev_t = None
                    prev_sent = None
                    prev_recall = None
                    for t in sorted(tm_perf['rerank_score'].unique(), reverse=True):
                        captured_t = (matches_only['rerank_score'] >= t).sum()
                        recall_t = captured_t / total_matches * 100 if total_matches > 0 else 0
                        sent_t = (tm_perf['rerank_score'] >= t).sum()
                        if recall_t < RECALL_FLOOR:
                            prev_t = t
                            prev_sent = sent_t
                            prev_recall = round(recall_t, 1)
                        else:
                            safe_threshold = prev_t
                            safe_trials_sent = prev_sent
                            safe_recall = prev_recall
                            break

                    if safe_threshold is not None and safe_trials_sent is not None and patients_count > 0:
                        avg_safe_trials = round(safe_trials_sent / patients_count, 1)
                        cost_saved_safe = round((1 - safe_trials_sent / len(tm_perf)) * 100, 1)
                        fig_recall.add_vline(
                            x=safe_threshold,
                            line=dict(color='#2ca02c', width=1.5, dash='dot'),
                            annotation_text=f"95% recall floor",
                            annotation_position='top left',
                            annotation_font=dict(size=13, color='#2ca02c')
                        )
                        fig_recall.add_annotation(
                            x=safe_threshold,
                            y=safe_recall,
                            text=(
                                f"✅ Safe score cutoff (≥95% recall):<br>"
                                f"~{avg_safe_trials} trials/patient avg<br>"
                                f"Saves {cost_saved_safe}% {_judge} cost"
                            ),
                            showarrow=True,
                            arrowhead=2,
                            arrowcolor='#2ca02c',
                            bgcolor='white',
                            bordercolor='#2ca02c',
                            borderwidth=1,
                            font=dict(size=13, color='#2ca02c'),
                            ax=110,
                            ay=-50
                        )

                except Exception:
                    pass

                st.plotly_chart(fig_recall, use_container_width=True)

                st.caption(
                    f"Each point on the x-axis is a candidate cutoff on the **{_SCORE_LABEL}** — "
                    f"the value stored in trial_matches.rerank_score, which is Stage 3's RRF fusion of the "
                    f"per-query MedCPT rankings with the MeSH relevance boost added. It is NOT a MedCPT score: "
                    f"an RRF value is a function of pool size and query count, so this axis is a ranking "
                    f"position in disguise and not a calibrated relevance scale. The absolute knob in the "
                    f"Stage 4 quality gate reads medcpt_score_max instead, which is not yet a column of "
                    f"trial_matches and so cannot be plotted here. "
                    f"Green = fraction of true matches still captured; dashed blue = fraction of all trials "
                    f"still sent (proxy for {_judge} cost). "
                    f"When both lines track closely, no aggressive cutoff on this quantity is safe without "
                    f"sacrificing recall."
                )

                # ── Top-N Cap: Recall vs Cost by Trials Per Patient ───────────
                current_max = MAX_TRIALS_FOR_EVALUATION
                n_values = list(range(1, current_max + 1))
                topn_data = []

                for n in n_values:
                    top_n = (
                        tm_perf
                        .sort_values('rerank_score', ascending=False)
                        .groupby('inference_id')
                        .head(n)
                    )
                    captured_n   = top_n[top_n['eligible'] == 'eligible'].shape[0]
                    total_sent_n = top_n.shape[0]
                    topn_data.append({
                        'Trials Per Patient': n,
                        'Match Recall (%)':   round(captured_n / total_matches * 100, 1) if total_matches > 0 else 0,
                        'Trials Sent (%)':    round(total_sent_n / len(tm_perf) * 100, 1) if len(tm_perf) > 0 else 0,
                        'captured':           captured_n,
                        'total_sent':         total_sent_n,
                    })

                topn_df = pd.DataFrame(topn_data)

                fig_topn = go.Figure()
                fig_topn.add_trace(go.Scatter(
                    x=topn_df['Trials Per Patient'],
                    y=topn_df['Match Recall (%)'],
                    mode='lines',
                    name='Match Recall',
                    line=dict(color='#2ca02c', width=2.5)
                ))
                fig_topn.add_trace(go.Scatter(
                    x=topn_df['Trials Per Patient'],
                    y=topn_df['Trials Sent (%)'],
                    mode='lines',
                    name=f'Trials Sent to {_judge}',
                    line=dict(color='#1f77b4', width=2.5, dash='dash')
                ))

                fig_topn.update_layout(
                    title='Recall vs Cost Tradeoff by Top-N Cap (Trials Per Patient)',
                    height=350,
                    margin=dict(l=20, r=20, t=40, b=20),
                    template='plotly_white',
                    xaxis_title=f'Trials Sent to {_judge} Per Patient (N)',
                    yaxis_title='Percentage (%)',
                    legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
                    hovermode='x unified'
                )

                try:
                    # Mark current MAX_TRIALS_FOR_EVALUATION
                    current_row = topn_df[topn_df['Trials Per Patient'] == current_max]
                    if not current_row.empty:
                        current_recall_n = current_row.iloc[0]['Match Recall (%)']
                        current_captured = current_row.iloc[0]['captured']
                        fig_topn.add_vline(
                            x=current_max,
                            line=dict(color='#1f77b4', width=1.5, dash='dot'),
                            annotation_text=f"Current<br>({current_max}/patient)",
                            annotation_position='top left',
                            annotation_font=dict(size=13, color='#1f77b4')
                        )

                    # Alt: current_max - 5
                    alt_max = current_max - 5
                    if alt_max > 0:
                        alt_row = topn_df[topn_df['Trials Per Patient'] == alt_max]
                        if not alt_row.empty:
                            alt_recall_n  = alt_row.iloc[0]['Match Recall (%)']
                            alt_captured  = alt_row.iloc[0]['captured']
                            recall_lost   = round(current_recall_n - alt_recall_n, 1)
                            matched_lost  = int(current_captured - alt_captured)
                            cost_saved    = round((1 - alt_max / current_max) * 100, 1)
                            fig_topn.add_vline(
                                x=alt_max,
                                line=dict(color='#d62728', width=1.5, dash='dot'),
                                annotation_text=f"{alt_max}-trial cap",
                                annotation_position='top right',
                                annotation_font=dict(size=13, color='#d62728')
                            )
                            fig_topn.add_annotation(
                                x=alt_max,
                                y=alt_recall_n,
                                text=(
                                    f"Cut {current_max}→{alt_max} trials per patient:<br>"
                                    f"❌ Lose {recall_lost}% of matches ({matched_lost} matched trials of current db never reach {_judge})<br>"
                                    f"✅ Save {cost_saved}% {_judge} cost"
                                ),
                                showarrow=True,
                                arrowhead=2,
                                arrowcolor='#d62728',
                                bgcolor='white',
                                bordercolor='#d62728',
                                borderwidth=1,
                                font=dict(size=13, color='#d62728'),
                                ax=-300,
                                ay=-40
                            )

                    # Safe N: smallest N where recall >= 95%
                    safe_rows_n = topn_df[topn_df['Match Recall (%)'] >= RECALL_FLOOR]
                    if not safe_rows_n.empty:
                        safe_row_n      = safe_rows_n.iloc[0]
                        safe_n          = int(safe_row_n['Trials Per Patient'])
                        safe_recall_n   = safe_row_n['Match Recall (%)']
                        cost_saved_safe = round((1 - safe_n / current_max) * 100, 1)
                        if safe_n < current_max:
                            fig_topn.add_vline(
                                x=safe_n,
                                line=dict(color='#2ca02c', width=1.5, dash='dot'),
                                annotation_text=f"95%<br>recall floor",
                                annotation_position='top left',
                                annotation_yshift=-10,
                                annotation_font=dict(size=13, color='#2ca02c')
                            )
                            fig_topn.add_annotation(
                                x=safe_n,
                                y=safe_recall_n,
                                text=(
                                    f"✅ Safe cap (≥95% recall):<br>"
                                    f"{safe_n} trials/patient<br>"
                                    f"Saves {cost_saved_safe}% {_judge} cost"
                                ),
                                showarrow=True,
                                arrowhead=2,
                                arrowcolor='#2ca02c',
                                bgcolor='white',
                                bordercolor='#2ca02c',
                                borderwidth=1,
                                font=dict(size=13, color='#2ca02c'),
                                ax=-180,
                                ay=-50
                            )

                except Exception:
                    pass

                st.plotly_chart(fig_topn, use_container_width=True)

                st.caption(
                    f"Each point on the x-axis is a per-patient top-N cap — only the N trials with the highest "
                    f"**{_SCORE_LABEL}** per patient are sent to {_judge}. Green = fraction of true matches still "
                    f"captured; dashed blue = fraction of all trials still sent (proxy for {_judge} cost). "
                    f"Use this chart to find the minimum N that preserves ≥95% recall, "
                    f"directly informing the MAX_TRIALS_FOR_EVALUATION config value."
                )

            # ── Row 2: Strip plot + KDE side by side ─────────────────────────
            col1, col2 = st.columns(2)

            # Strip plot: every trial as a dot, jittered by match status
            with col1:
                
                
                category_order = ['Eligible', 'Partial Match',
                                  'Unconfirmed Match', 'Not Eligible']
                fig_strip = go.Figure()

                for status in category_order:
                    subset = tm_perf[tm_perf['match_status'] == status]
                    if subset.empty:
                        continue
                    fig_strip.add_trace(go.Box(
                        y=subset['rerank_score'],
                        x=[status] * len(subset),
                        name=status,
                        marker_color=color_map[status],
                        boxpoints='all',
                        jitter=0.4,
                        pointpos=0,
                        line=dict(width=2, color=color_map[status]),
                        fillcolor='rgba(0,0,0,0)',
                        marker=dict(size=7, opacity=0.75),
                        showlegend=False
                    ))

                fig_strip.update_layout(
                    title=f'{_SCORE_LABEL} by Match Outcome (each dot = one trial)',
                    height=350,
                    margin=dict(l=20, r=20, t=40, b=20),
                    template='plotly_white',
                    xaxis_title='Match Status',
                    yaxis_title=_SCORE_LABEL,
                    xaxis=dict(categoryorder='array', categoryarray=category_order),
                )
                st.plotly_chart(fig_strip, use_container_width=True)

            with col2:
                eligible_scores    = tm_perf[tm_perf['eligible'] == 'eligible']['rerank_score'].dropna().values
                not_eligible_scores = tm_perf[tm_perf['eligible'] == 'not_eligible']['rerank_score'].dropna().values

                fig_kde = go.Figure()

                for scores, color, fill_color, name in [
                    (eligible_scores,     '#2ca02c', 'rgba(44,160,44,0.15)',  'Eligible / Partial'),
                    (not_eligible_scores, '#d62728', 'rgba(214,39,40,0.15)',  'Not Eligible'),
                ]:
                    result = _kde_curve(scores)
                    if result is None:
                        continue
                    x_range, y_range = result
                    fig_kde.add_trace(go.Scatter(
                        x=x_range,
                        y=y_range,
                        mode='lines',
                        name=name,
                        line=dict(color=color, width=2.5),
                        fill='tozeroy',
                        fillcolor=fill_color,
                        
                    ))
                    # Median dashed line with fully positioned annotation
                    median_val = np.median(scores)
                    fig_kde.add_shape(
                        type='line', x0=median_val, x1=median_val, y0=0, y1=1,
                        yref='paper', line=dict(color=color, width=1.5, dash='dash'),
                    )
                    x_anchor = 'left' if name.startswith('Elig') else 'right'
                    label = 'Eligible Median' if name.startswith('Elig') else 'Not Eligible Median'
                    fig_kde.add_annotation(
                        x=median_val, y=0.90, yref='paper',
                        text=f'{label}: {median_val:.2f}',
                        font=dict(size=10, color=color),
                        showarrow=False,
                        xanchor=x_anchor,
                        xshift=20 if name.startswith('Elig') else -20,
                    )

                fig_kde.update_layout(
                    title=f'{_SCORE_LABEL} Density by Outcome (aggregated)',
                    height=350,
                    margin=dict(l=20, r=20, t=40, b=20),
                    template='plotly_white',
                    xaxis_title=_SCORE_LABEL,
                    yaxis_title='Density',
                    legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
                )
                fig_kde.update_layout(height=400)
                st.plotly_chart(fig_kde, use_container_width=True)

            st.caption(
                f"Left: each dot is one trial, at its **{_SCORE_LABEL}**. Overlapping distributions across "
                f"outcome groups are expected — the underlying MedCPT cross-encoder measures topical relevance, "
                f"not eligibility, and the RRF fusion plotted here compresses even that into a rank-derived "
                f"value. Right: the aggregated distributions. "
                f"{_judge} handles eligibility discrimination in the next stage. Dashed lines mark group medians."
            )

            # Summary metrics
            col1, col2, col3 = st.columns(3)
            with col1:
                median_match = tm_perf[tm_perf['eligible'] == 'eligible']['rerank_score'].median()
                st.metric(f"Median {_SCORE_LABEL} (Matches)",
                          f"{median_match:.3f}" if not pd.isna(median_match) else "N/A")
            with col2:
                median_nonmatch = tm_perf[tm_perf['eligible'] == 'not_eligible']['rerank_score'].median()
                st.metric(f"Median {_SCORE_LABEL} (Not Eligible)",
                          f"{median_nonmatch:.3f}" if not pd.isna(median_nonmatch) else "N/A")
            with col3:
                separation = median_match - median_nonmatch if not pd.isna(median_match) and not pd.isna(median_nonmatch) else None
                st.metric("Score Separation", f"{separation:.3f}" if separation is not None else "N/A",
                         help=(
                             f"Difference in the median {_SCORE_LABEL} between matches and non-matches. "
                             f"Values near zero are expected, for TWO reasons that are usually conflated: the "
                             f"underlying MedCPT cross-encoder is a topical relevance model (trained on PubMed "
                             f"search logs) and not an eligibility classifier, AND this axis is an RRF value, "
                             f"which is derived from rank position within a fixed-size pool and therefore has a "
                             f"narrow range by construction. All retrieved trials are oncologically relevant by "
                             f"design; eligibility discrimination is handled by {_judge} in the next stage."
                         ))
        else:
            st.info("No rerank score data available for the selected filters.")
    else:
        st.info("No trial match data available.")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
