"""
Overview tab. Moved verbatim out of "21- Streamlit Dashboard.py" (pass 20c-3c-1).
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from oncotriage.dashboard import call_mode
from oncotriage.dashboard.data import load_trial_matches_data
from oncotriage.dashboard.populations import (
    VERDICT_ELIGIBLE,
    ensure_evaluated,
    first_attempts,
    incomplete_caption,
    is_usable_result,
    patient_outcomes,
    USABLE_RESULT_LABEL,
)
from oncotriage.dashboard.tiers import (MATCH_TIER_COLORS,
                                        PATIENT_OUTCOME_FULL,
                                        PATIENT_OUTCOME_INCOMPLETE,
                                        rate_text)



# ===========================================================================
# STAGE RETENTION: ONE DERIVATION, AND A ZERO THAT IS NEVER INVENTED
# ===========================================================================

RETENTION_UNAVAILABLE = "—"
"""What a retention tile shows when it has no honest percentage to show.

AN EM DASH AND NOT "0.0%". The three tiles all read ``... if total > 0 else 0``,
which renders 0.0% for a selection that recorded no counts at all -- and 0.0%
is a MEASUREMENT: it says every candidate was dropped at that stage. A reader
cannot tell that from "nothing was recorded", and only one of the two is a
reason to go and look at the pipeline."""


def _retention(df, numerator_column, denominator_column):
    """``(display, note)`` for one retention tile. THE ONE DERIVATION.

    ``note`` is the sentence appended to the tile's help text, and it is empty
    exactly when the display is a real percentage.

    ``min_count=1`` IS THE LOAD-BEARING ARGUMENT. ``Series.sum()`` over a column
    that is NULL on every row returns ``0.0``, not NA -- so without it a
    selection that recorded nothing produces a denominator of 0, takes the
    ``else`` branch, and prints a percentage-shaped nothing. It is the same
    argument ``queries.model_groups_from_frame`` already makes for the cost
    sums, adopted rather than re-derived: "no count was ever recorded" and
    "these rows counted zero" are different facts and pandas collapses them by
    default.

    ALL THREE TILES SHARE IT. Only the middle one was named as defective, and
    the other two carry the identical ``else 0``; giving the three one owner is
    what stops the next edit repairing one and leaving two, which is how this
    one came to be alone.
    """
    numerator = df[numerator_column].sum(min_count=1)
    denominator = df[denominator_column].sum(min_count=1)

    if pd.isna(numerator) or pd.isna(denominator):
        absent = [c for c, v in ((denominator_column, denominator),
                                 (numerator_column, numerator))
                  if pd.isna(v)]
        return RETENTION_UNAVAILABLE, (
            f" NOT AVAILABLE: no row in this selection recorded "
            f"{' or '.join(absent)}, so there is no measurement to divide. "
            f"This is not a retention of zero.")

    if denominator <= 0:
        return RETENTION_UNAVAILABLE, (
            f" NOT AVAILABLE: this selection's total {denominator_column} is "
            f"{int(denominator)}, so the stage had nothing to retain. A "
            f"percentage of nothing is not zero percent.")

    return f"{numerator / denominator * 100:.1f}%", ""


def _money(value):
    """``$0.1234``, or the em dash when there is no amount to show."""
    return "—" if pd.isna(value) else f"${value:.4f}"


def _mean_or_nan(frame, column):
    """The mean of `column`, NaN when it is absent, all-NULL or the frame empty.

    ``estimated_cost_cached_usd`` is ADDITIVE, so a database written before it
    has no such column -- which is "no cached estimate", not a raise.
    """
    if column not in frame.columns or len(frame) == 0:
        return float("nan")
    return pd.to_numeric(frame[column], errors="coerce").mean()


def _verdict_retention(df, trial_matches):
    """``(display, note, without_usable)`` for the "Evaluated → Eligible" tile.

    THE DENOMINATOR IS TRIALS WITH A USABLE RESULT, INCLUDING CLINICAL
    UNCERTAINTY (``populations.USABLE_RESULT_LABEL``; the dashboard-truthfulness
    pass). It was ``SUM(eligible_matches) / SUM(candidates_evaluated)``, and
    ``candidates_evaluated`` counts every trial SENT to Stage 5 -- including
    every trial whose call failed and so has no verdict at all. Measured on the
    smoke run: 20 / 98 = 20.4%, against 20 / 34 = 58.8% over the usable results, with
    the 64 failed calls dragging the rate down as though they were rejections.

    A trial the model DECLARED not evaluable is a usable result -- a clinical
    one, though NOT a definite eligibility verdict -- and stays in the
    denominator. A trial with no usable verdict, or whose
    reason cannot be classified, is excluded and COUNTED, and the tile shows
    that count beside the rate.

    The population is trial verdict rows across the selection's INFERENCE ROWS,
    re-runs included, and the help text says so: a per-trial rate is legal over
    rows as long as it is labelled as one.
    """
    if trial_matches is None or len(trial_matches) == 0 or "id" not in df.columns:
        return RETENTION_UNAVAILABLE, (
            " NOT AVAILABLE: no trial verdict rows are recorded for this "
            "selection."), 0
    rows = trial_matches[trial_matches["inference_id"].isin(df["id"])]
    reasons = (rows["not_evaluable_reason"]
               if "not_evaluable_reason" in rows.columns
               else [None] * len(rows))
    eligible = usable = without_usable = 0
    for verdict, reason in zip(rows["eligible"], reasons):
        if is_usable_result(verdict, reason):
            usable += 1
            eligible += int(str(verdict) == VERDICT_ELIGIBLE)
        else:
            without_usable += 1
    scope = (f" Population: {len(rows):,} trial verdict row(s) across "
             f"{len(df):,} inference row(s), re-runs included.")
    if usable == 0:
        return RETENTION_UNAVAILABLE, (
            f" NOT AVAILABLE: none of those trials has a {USABLE_RESULT_LABEL}, "
            f"so there is no denominator. This is not a retention of zero."
            f"{scope}"), without_usable
    return (f"{eligible / usable * 100:.1f}%",
            f"{scope} {without_usable:,} trial(s) without a usable result are "
            f"excluded from the denominator, not counted as not eligible.",
            without_usable)


def render_overview_tab(df):
    """Render Overview tab with KPIs and high-level visualizations."""
    
    st.header("📊 Pipeline Overview")

    # INCOMPLETENESS DOES NOT DEPEND ON THE CALLER (the dashboard-truthfulness
    # pass). A no-op under main(), which annotated already; see
    # populations.ensure_evaluated.
    df = ensure_evaluated(df, load_trial_matches_data())
    
    # Custom CSS for smaller KPI values
    st.markdown("""
        <style>
        [data-testid="stMetricValue"] {
            font-size: 24px;
        }
        </style>
    """, unsafe_allow_html=True)
    
    # Top-level KPIs
    col1, col2, col3, col4, col5, col6, col7, col8 = st.columns(8)

    total_inferences = len(df)
    unique_patients = df['patient_id'].nunique()
    _trial_rows = load_trial_matches_data()

    # PATIENT FIGURES ARE COMPUTED OVER FIRST ATTEMPTS AND CLINICAL TIERS OVER
    # COMPLETE ONES (the dashboard-truthfulness pass). The four tier tiles
    # divided a count of ROWS by `len(df)` and printed it as "patients", so a
    # resample rerun counted a patient twice and an errored retry counted as a
    # clinical No Match. Measured on the smoke run: "No Match 30.0% · 3
    # patients", where 2 of the 3 were errored retries and the third had 12 of
    # its 15 trials with no usable verdict. See oncotriage/dashboard/populations.py.
    _outcomes = patient_outcomes(df)
    _patients_df = first_attempts(df)
    _complete = _outcomes['complete']
    _cost_fa = _mean_or_nan(_patients_df, 'estimated_cost_usd')
    _cost_fa_cached = _mean_or_nan(_patients_df, 'estimated_cost_cached_usd')
    _all_spend = pd.to_numeric(df['estimated_cost_usd'], errors='coerce').sum(min_count=1)
    _spend_per_patient = (_all_spend / unique_patients
                          if unique_patients and not pd.isna(_all_spend)
                          else float('nan'))

    with col1:
        st.metric(
            "Total Inferences",
            f"{total_inferences:,}",
            delta=f"{unique_patients:,} patients",
            help="Inference ROWS in this selection, and the distinct patients "
                 "they belong to. A re-run or retried patient is several rows."
        )

    with col2:
        # THE ONLY MODE-DEPENDENT FIGURE ON THIS TAB, and it is a per-patient
        # MEAN. Per-trial mode sends one billed request per patient-trial pair
        # plus a cache warmup; grouped sends one per packed chunk, so a mean
        # across both arms is a midpoint no patient in either resembles.
        #
        # ITS POPULATION IS ONE ROW PER PATIENT (the dashboard-truthfulness
        # pass). It was `AVG(estimated_cost_usd)` over every row, errored
        # retries and resample reruns included -- a number that is neither
        # "what a patient costs" nor "what was spent per patient".
        _mix = call_mode.describe(_patients_df)
        st.metric(
            "Cost/Patient (first attempt)" + ("  ⚠" if _mix["is_mixed"] else ""),
            _money(_cost_fa),
            delta=(f"cached-rate est. {_money(_cost_fa_cached)}"
                   if not pd.isna(_cost_fa_cached) else None),
            delta_color="off",
            help=(f"Mean `estimated_cost_usd` over {len(_patients_df):,} first "
                  f"attempt(s) — {_outcomes['label']} — priced at UNCACHED input "
                  f"rates. The delta is the same population priced at the "
                  f"cached-read rate (`estimated_cost_cached_usd`), which is what "
                  f"a working prompt cache bills. For comparison, ALL spend in "
                  f"this selection — re-runs and errored rows included — divided "
                  f"by {unique_patients:,} distinct patient(s) is "
                  f"{_money(_spend_per_patient)} at uncached rates.") + (
                f" BLENDED ACROSS STAGE 5 CALL MODES"
                f"{call_mode.label_suffix(_mix)} — the two arms cost different "
                f"amounts per patient, so this mean is over two populations. "
                f"The Cost & Tokens tab breaks it out per mode."
                if _mix["is_mixed"]
                else (f" All rows are {_mix['sole_bucket']}."
                      if _mix["sole_bucket"] else "")))

    _tier_help = (" Over COMPLETE first-attempt evaluations only; '—' means "
                  "there are none. Incomplete evaluations are counted in their "
                  "own tile and never as a tier.")

    with col3:
        st.metric(
            PATIENT_OUTCOME_FULL,
            rate_text(_outcomes['tier_rates']['Full Match']),
            delta=f"{_outcomes['tier_counts']['Full Match']} of {_complete} complete",
            help="Patients with at least 1 trial where ALL criteria were "
                 "confirmed met (100% match score)." + _tier_help
        )

    with col4:
        st.metric(
            "🟡 Partial Match",
            rate_text(_outcomes['tier_rates']['Partial Match']),
            delta=f"{_outcomes['tier_counts']['Partial Match']} of {_complete} complete",
            help="Patients whose best trial had SOME criteria confirmed but not "
                 "all (0% < score < 100%)." + _tier_help
        )

    with col5:
        st.metric(
            "🔶 Unconfirmed",
            rate_text(_outcomes['tier_rates']['Unconfirmed Match']),
            delta=f"{_outcomes['tier_counts']['Unconfirmed Match']} of {_complete} complete",
            delta_color="inverse",
            help=(
                "Patients whose only eligible trials scored 0% — no disqualifier "
                "was found, but not a single criterion could be confirmed. "
                "Eligible on paper, nothing established." + _tier_help
            )
        )

    with col6:
        st.metric(
            "❌ No Match",
            rate_text(_outcomes['tier_rates']['No Match']),
            delta=f"{_outcomes['tier_counts']['No Match']} of {_complete} complete",
            delta_color="inverse",
            help="Patients whose COMPLETE evaluation found no eligible trial. An "
                 "evaluation that errored or lost verdicts is never a No Match: "
                 "a No Match cannot be concluded from an evaluation that did "
                 "not finish." + _tier_help
        )

    with col7:
        st.metric(
            PATIENT_OUTCOME_INCOMPLETE,
            f"{_outcomes['incomplete']:,}",
            delta=f"of {_outcomes['first_attempts']:,} first attempts",
            delta_color="off",
            help="First attempts whose evaluation cannot support a clinical "
                 "tier: the attempt errored, or at least one trial has no usable "
                 "verdict (a failed call, a truncated or unusable response, or a "
                 "not-evaluable reason that cannot be classified). EXCLUDED from "
                 "every tier percentage and counted here instead. A trial the "
                 "model itself declared not evaluable does NOT make an "
                 "evaluation incomplete."
        )

    with col8:
        # THROUGH THE ONE OWNER: `populations.patient_outcomes` counts Any Match
        # from `match_tier` over the same complete first attempts as the four
        # tiles beside it, so the total and its parts share one population.
        st.metric(
            "Any Match",
            rate_text(_outcomes['any_match_rate']),
            delta=f"{_outcomes['any_match']} of {_complete} complete",
            help="Complete first-attempt evaluations with at least 1 eligible "
                 "trial (full, partial, or unconfirmed)." + _tier_help
        )

    st.caption(incomplete_caption(_outcomes))

    st.markdown("---")
    
    # Charts row
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Patients Processed Over Time")
        
        timeline_df = df.groupby(df['timestamp'].dt.date).size().reset_index()
        timeline_df.columns = ['Date', 'Patients']
        
        fig_timeline = px.line(
            timeline_df,
            x='Date',
            y='Patients',
            markers=True,
            template='plotly_white'
        )
        fig_timeline.update_traces(line_color='#1f77b4')
        fig_timeline.update_layout(
            height=300,
            margin=dict(l=20, r=20, t=20, b=20),
            xaxis_title="",
            yaxis_title="Patients Processed",
            xaxis=dict(
                tickmode='array',
                tickvals=timeline_df['Date'],
                tickformat="%b %d"
            )
        )
        st.plotly_chart(fig_timeline, use_container_width=True)
    
    with col2:
        st.subheader("Average Stage Latencies")
        
        stage_times = {
            'Hybrid Retrieval': df['hybrid_retrieval_time'].mean(),
            'Cross-Encoder': df['cross_encoder_time'].mean(),
            'Rule Filter': df['rule_filter_time'].mean(),
            # THE SERIES NAME IS THE STAGE, NOT A MODEL (the dashboard-fixes
            # pass). It read 'GPT-4o Eval' -- a label the naming pass left
            # behind when it renamed the column underneath it, so this chart
            # named a model that has not served Stage 5 since 2026-08-04 while
            # reading `llm_classifier_evaluation_time`. Its three siblings
            # above are all STAGE names; a model name here is the odd one out
            # AND the one that goes stale. Interpolating `MATCHING_MODEL` was the other
            # option and is wrong for a HISTORICAL chart: the mean is over rows
            # that may have been judged by several models, so naming today's
            # would be a claim about rows it did not produce.
            'LLM Classifier': df['llm_classifier_evaluation_time'].mean()
        }
        
        fig_stages = go.Figure(data=[
            go.Bar(
                x=list(stage_times.values()),
                y=list(stage_times.keys()),
                orientation='h',
                marker_color=['#1f77b4', '#ff7f0e', '#d62728', '#9467bd'],
                text=[f"{v:.2f}s" for v in stage_times.values()],
                textposition='outside',
                cliponaxis=False,
                textfont=dict(size=12)
            )
        ])
        fig_stages.update_layout(
            height=300,
            margin=dict(l=20, r=80, t=20, b=20),
            xaxis_title="Seconds",
            yaxis_title="",
            template='plotly_white',
            showlegend=False,
            yaxis=dict(autorange='reversed')
        )
        fig_stages.update_xaxes(range=[0, max(max(stage_times.values()), 0.01) * 1.4])
        st.plotly_chart(fig_stages, use_container_width=True)
    
    st.markdown("---")
    
    # Pipeline funnel
    st.subheader("Pipeline Funnel")
    
    funnel_data = {
        'Stage': [
            'Retrieved',
            'Re-Ranked',
            'Rule Filter',
            'Quality Filter',
            'Cost Cap',
            'Evaluated',
            'Eligible (Any)',
            '  └ Full Match',
            '  └ Partial Match',
            '  └ Unconfirmed'
        ],
        'Avg Count': [
            df['candidates_retrieved'].mean(),
            df['candidates_reranked'].mean(),
            df.get('candidates_after_rule_filter', df['candidates_filtered']).mean() if 'candidates_after_rule_filter' in df.columns else df['candidates_filtered'].mean(),
            df['candidates_after_quality_filter'].mean(),
            df['candidates_filtered'].mean(),
            df['candidates_evaluated'].mean(),
            df['eligible_matches'].mean(),
            df['full_match_count'].mean(),
            df['partial_match_count'].mean(),
            df['unconfirmed_match_count'].mean()
        ]
    }
    
    # Round values for clean display
    avg_counts = funnel_data['Avg Count']
    
    funnel_text = [f"{v:.1f} ({v/avg_counts[0]*100:.1f}%)" if avg_counts[0] > 0 else f"{v:.1f}" for v in avg_counts]
    
    fig_funnel = go.Figure(go.Funnel(
        y=funnel_data['Stage'],
        x=avg_counts,
        text=funnel_text,
        textinfo="text",
        marker=dict(color=['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
                           '#8c564b', '#e377c2',
                           MATCH_TIER_COLORS['Full Match'],
                           MATCH_TIER_COLORS['Partial Match'],
                           MATCH_TIER_COLORS['Unconfirmed Match']])
    ))
    
    fig_funnel.update_layout(
        height=480,
        margin=dict(l=20, r=20, t=20, b=20),
        template='plotly_white'
    )
    
    st.plotly_chart(fig_funnel, use_container_width=True)
    
    st.markdown("---")
    
    # =========================================================================
    # Data Completeness (Tier 1)
    # =========================================================================
    st.subheader("Data Completeness")
    
    # COMPLETENESS IS A PROPERTY OF A PATIENT, so it is computed over one row
    # per patient per campaign (the dashboard-truthfulness pass). It was
    # computed over inference rows and printed "10/10" beside a label
    # saying patients, for five patients.
    pdf = _patients_df
    # Define fields to check and their display names
    completeness_fields = {
        'age':               'Age',
        'sex':               'Sex',
        'race':              'Race',
        'ethnicity':         'Ethnicity',
        'primary_condition': 'Primary Condition',
    }
    
    # Compute per-field completeness
    field_stats = []
    for col, label in completeness_fields.items():
        if col in pdf.columns:
            missing = pdf[col].isna() | (pdf[col].astype(str).str.strip().isin(['', 'None', 'Unknown', 'unknown']))
            missing_count = missing.sum()
            complete_pct = (1 - missing_count / len(pdf)) * 100 if len(pdf) > 0 else 0
            field_stats.append({
                'Field': label,
                'Complete': len(pdf) - missing_count,
                'Missing': missing_count,
                'Complete %': complete_pct
            })
    
    # Also check for suspiciously empty clinical records
    zero_conditions = (pdf['condition_count'] == 0).sum() if 'condition_count' in pdf.columns else 0
    zero_medications = (pdf['medication_count'] == 0).sum() if 'medication_count' in pdf.columns else 0
    
    field_stats.append({
        'Field': 'Conditions (≥1)',
        'Complete': len(pdf) - zero_conditions,
        'Missing': zero_conditions,
        'Complete %': (1 - zero_conditions / len(pdf)) * 100 if len(pdf) > 0 else 0
    })
    field_stats.append({
        'Field': 'Medications (≥1)',
        'Complete': len(pdf) - zero_medications,
        'Missing': zero_medications,
        'Complete %': (1 - zero_medications / len(pdf)) * 100 if len(pdf) > 0 else 0
    })
    
    completeness_df = pd.DataFrame(field_stats)
    
    # Overall data quality score: average completeness across all fields
    overall_quality = completeness_df['Complete %'].mean()
    
    # Summary metrics row
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        quality_color = "normal" if overall_quality >= 90 else ("off" if overall_quality >= 70 else "inverse")
        st.metric(
            "Data Quality Score",
            f"{overall_quality:.0f}%",
            delta="≥90% target" if overall_quality >= 90 else f"{overall_quality - 90:+.0f}% from target",
            delta_color=quality_color,
            help="Average completeness across all tracked fields. 100% = every patient has every field populated"
        )
    
    with col2:
        fully_complete = sum(1 for _, row in completeness_df.iterrows() if row['Missing'] == 0)
        st.metric(
            "Fields 100% Complete",
            f"{fully_complete}/{len(completeness_df)}",
            help="Number of tracked fields with zero missing values"
        )
    
    with col3:
        # "NONE" WHEN NOTHING IS WEAK. `idxmin` over a tie returns the FIRST
        # field, so a selection with every field 100% complete named 'Age' as
        # its weakest field -- a finding about nothing.
        _min_pct = completeness_df['Complete %'].min()
        if _min_pct >= 100:
            _weakest, _weak_delta = "None", "every field 100% complete"
        else:
            worst_field = completeness_df.loc[completeness_df['Complete %'].idxmin()]
            _weakest = worst_field['Field']
            _weak_delta = f"{worst_field['Complete %']:.0f}% complete"
        st.metric(
            "Weakest Field",
            _weakest,
            delta=_weak_delta,
            delta_color="inverse" if _min_pct < 90 else "normal",
            help="Field with the lowest completeness rate, or None when every "
                 "tracked field is 100% complete"
        )
    
    with col4:
        all_complete_mask = pd.Series(True, index=pdf.index)
        for col_name in completeness_fields.keys():
            if col_name in pdf.columns:
                field_missing = pdf[col_name].isna() | (pdf[col_name].astype(str).str.strip().isin(['', 'None', 'Unknown', 'unknown']))
                all_complete_mask = all_complete_mask & ~field_missing
        if 'condition_count' in pdf.columns:
            all_complete_mask = all_complete_mask & (pdf['condition_count'] > 0)
        if 'medication_count' in pdf.columns:
            all_complete_mask = all_complete_mask & (pdf['medication_count'] > 0)
        patients_all_complete = all_complete_mask.sum()
        pct_all_complete = patients_all_complete / len(pdf) * 100 if len(pdf) > 0 else 0
        st.metric(
            "Patients Fully Complete",
            f"{pct_all_complete:.0f}%",
            delta=f"{patients_all_complete}/{len(pdf)}",
            help="Patients with ALL tracked fields complete (demographics + at least 1 condition and 1 medication)"
        )
    
    # Horizontal bar chart — completeness per field
    chart_df = completeness_df.sort_values('Complete %', ascending=True).copy()
    
    colors = ['#2ca02c' if pct >= 90 else '#ff7f0e' if pct >= 70 else '#d62728'
              for pct in chart_df['Complete %']]
    
    fig_quality = go.Figure()
    fig_quality.add_trace(go.Bar(
        y=chart_df['Field'],
        x=chart_df['Complete %'],
        orientation='h',
        marker_color=colors,
        text=[f"{pct:.0f}%  ({m} missing)" for pct, m in zip(chart_df['Complete %'], chart_df['Missing'])],
        textposition='outside',
        cliponaxis=False
    ))
    fig_quality.add_vline(
        x=90, line_dash="dash", line_color="red", line_width=1.5
    )
    fig_quality.add_annotation(
        x=90, y=1, yref="paper",
        text="<b>90% target</b>",
        showarrow=True, arrowhead=0, ax=45, ay=-20,
        font=dict(size=12, color="red"),
        bgcolor="white", bordercolor="red", borderwidth=1, borderpad=3
    )
    fig_quality.update_layout(
        height=max(280, len(chart_df) * 38 + 40),
        margin=dict(l=0, r=120, t=40, b=20),
        template='plotly_white',
        showlegend=False,
        xaxis=dict(title='Completeness (%)', range=[0, 115]),
        yaxis=dict(automargin=True)
    )
    st.plotly_chart(fig_quality, use_container_width=True)
    
    st.caption(
        "**Green** ≥90% · **Orange** 70-89% · **Red** <70% · "
        "Missing includes null, blank, 'Unknown'. Computed over first attempts "
        "(one row per patient per campaign), so a re-run patient counts once. "
        "Low completeness in demographic fields may reduce match rates independent of pipeline quality."
    )
    
    # =========================================================================
    # Data Completeness — Tier 2 (Explanation-based)
    # =========================================================================
    trial_matches = load_trial_matches_data()
    
    if trial_matches is not None and not trial_matches.empty:
        # Filter to current patient set
        filtered_ids = df['id'].tolist()
        filtered_tm = trial_matches[trial_matches['inference_id'].isin(filtered_ids)].copy()
        
        if not filtered_tm.empty:
            explanations = filtered_tm['assessment'].fillna('').str.lower()

            # THIS PANEL READS FREE PROSE AND THE COLUMN STOPPED BEING FREE
            # PROSE AT PROMPT_VERSION 1.5.0. `assessment` is now composed from
            # the criteria arrays for every eligible / not_eligible row
            # (oncotriage/agent/evaluation.py:compose_assessment), so a
            # model-written phrasing like "stage not specified" no longer
            # occurs: an undocumented criterion is rendered as
            # `Not documented in the patient record: "<criterion text>"`.
            # The phrase lists below therefore match ROWS WRITTEN BEFORE 1.5.0
            # and increasingly little else, and this panel under-reports on a
            # fresh corpus rather than reporting nothing -- single words that
            # occur in criterion text ("staging", "histology") still hit.
            #
            # THE FIX IS NOT A LONGER PHRASE LIST. The fact this panel wants is
            # in `criterion_details` on the same row, exactly and without
            # parsing prose: a criterion whose patient_value is "Not in patient
            # record" IS the missing data point, and its `criterion` text is
            # what says which category it belongs to. Rewriting the panel onto
            # that column is a recorded follow-up; it is a redesign of an
            # untested render path and was deliberately not folded into the
            # pass that changed the column's meaning.
            #
            # Keywords indicating missing/unavailable clinical data in GPT-4o explanations
            missing_data_keywords = {
                'Cancer Stage':       ['stage not specified', 'stage unknown', 'stage not documented',
                                       'unable to determine stage', 'staging information',
                                       'no staging', 'stage is not', 'stage not available',
                                       'stage not provided', 'stage information missing',
                                       'stage not mentioned', 'staging not'],
                'Histology/Pathology': ['histology not', 'histological type not', 'pathology not',
                                        'histological information', 'tissue type not',
                                        'no histology', 'histology unknown', 'pathology unknown',
                                        'histological subtype not'],
                'ECOG/Performance':   ['ecog not', 'performance status not', 'ecog unknown',
                                       'functional status not', 'performance score not',
                                       'no ecog', 'ecog status not', 'performance not documented'],
                'Biomarkers':         ['biomarker not', 'mutation status not', 'receptor status not',
                                       'her2 status not', 'marker not', 'genomic information',
                                       'biomarker unknown', 'marker status unknown',
                                       'no biomarker', 'molecular testing not'],
                'Lab Values':         ['lab values not', 'laboratory not', 'creatinine not',
                                       'lab results not', 'blood count not',
                                       'no lab', 'labs not available', 'lab data not'],
                'Prior Treatment':    ['treatment history not', 'prior therapy not',
                                       'previous treatment not', 'treatment not documented',
                                       'no treatment history', 'prior treatments unknown'],
            }
            
            gap_results = []
            for category, kw_list in missing_data_keywords.items():
                # Count unique patients (via inference_id) where any trial explanation mentions this gap
                matches_mask = explanations.apply(lambda x: any(kw in x for kw in kw_list))
                affected_inferences = filtered_tm.loc[matches_mask, 'inference_id'].nunique()
                affected_pct = affected_inferences / len(df) * 100 if len(df) > 0 else 0
                gap_results.append({
                    'Clinical Gap': category,
                    'Patients Affected': affected_inferences,
                    '% of Patients': affected_pct,
                    'Trial Mentions': matches_mask.sum()
                })
            
            gap_df = pd.DataFrame(gap_results)
            # Only show gaps that were actually detected
            gap_df = gap_df[gap_df['Patients Affected'] > 0].sort_values('Patients Affected', ascending=False)
            
            if not gap_df.empty:
                st.markdown("---")
                st.subheader("Clinical Data Gaps")
                st.caption(
                    "Detected from GPT-4o match explanations — fields the model could not evaluate "
                    "because data was missing from the patient record."
                )
                
                col1, col2 = st.columns([2, 1])
                
                with col1:
                    chart_gap = gap_df.sort_values('% of Patients', ascending=True)
                    
                    colors_gap = ['#d62728' if pct >= 30 else '#ff7f0e' if pct >= 10 else '#2ca02c'
                                  for pct in chart_gap['% of Patients']]
                    
                    fig_gaps = go.Figure()
                    fig_gaps.add_trace(go.Bar(
                        y=chart_gap['Clinical Gap'],
                        x=chart_gap['% of Patients'],
                        orientation='h',
                        marker_color=colors_gap,
                        text=[f"{pct:.0f}% ({n} patients)"
                              for pct, n in zip(chart_gap['% of Patients'], chart_gap['Patients Affected'])],
                        textposition='outside',
                        cliponaxis=False
                    ))
                    fig_gaps.update_layout(
                        title='Patients Missing Key Clinical Data',
                        height=max(220, len(chart_gap) * 40),
                        margin=dict(l=0, r=130, t=40, b=20),
                        template='plotly_white',
                        showlegend=False,
                        xaxis=dict(title='% of Patients', range=[0, min(chart_gap['% of Patients'].max() * 1.6, 110)]),
                        yaxis=dict(automargin=True)
                    )
                    st.plotly_chart(fig_gaps, use_container_width=True)
                
                with col2:
                    st.markdown("**Impact Summary**")
                    # Union of all inference_ids affected by ANY gap category
                    all_gap_inference_ids = set()
                    for category, kw_list in missing_data_keywords.items():
                        cat_mask = explanations.apply(lambda x: any(kw in x for kw in kw_list))
                        all_gap_inference_ids.update(filtered_tm.loc[cat_mask, 'inference_id'].unique())
                    total_gap_patients = len(all_gap_inference_ids)
                    st.metric(
                        "Patients with ≥1 Gap",
                        f"{total_gap_patients}",
                        delta=f"{total_gap_patients / len(df) * 100:.0f}% of cohort" if len(df) > 0 else "N/A",
                        delta_color="inverse",
                        help="Patients where GPT-4o flagged at least one missing clinical field during trial evaluation"
                    )
                    
                    st.metric(
                        "Most Common Gap",
                        gap_df.iloc[0]['Clinical Gap'],
                        delta=f"{gap_df.iloc[0]['% of Patients']:.0f}% affected",
                        delta_color="inverse",
                        help="The clinical data field most frequently missing across patient evaluations"
                    )
                    
                    total_mentions = gap_df['Trial Mentions'].sum()
                    st.metric(
                        "Total Gap Mentions",
                        f"{total_mentions:,}",
                        help="Total times a missing-data issue appeared across all trial-patient evaluations"
                    )
    
    st.markdown("---")
    
    # Retention metrics
    st.subheader("Stage Retention Rates")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        _value, _note = _retention(df, 'candidates_reranked',
                                   'candidates_retrieved')
        st.metric(
            "Retrieved → Reranked",
            _value,
            help="Percentage of retrieved candidates that survive cross-encoder "
                 "reranking." + _note
        )
    
    with col2:
        # THE COLUMN WAS WRONG AND THE LABEL WAS RIGHT (the dashboard-fixes
        # pass). This tile is headed "Reranked → Rule Filter" and divided by
        # `candidates_filtered`, which is `len(filtered_trials)` --
        # Stage 4's output AFTER the quality gate AND after the
        # MAX_TRIALS_FOR_EVALUATION cost cap, two stages further down the
        # funnel than the heading claims. `candidates_after_rule_filter` is the
        # rule filter's own survivor count, which is what the heading names.
        #
        # MEASURED ON THE PRODUCTION TABLE, 1,106 rows: the pre-fix figure is
        # 12,951 / 44,240 = 29.3% and the true one is 26,436 / 44,240 = 59.8%.
        # So the tile understated the rule filter's retention by half, and it
        # did it by silently reporting the cost cap's effect as the rule
        # filter's -- a number that moves when MAX_TRIALS_FOR_EVALUATION moves,
        # under a heading about a stage that cap has nothing to do with.
        _value, _note = _retention(df, 'candidates_after_rule_filter',
                                   'candidates_reranked')
        st.metric(
            "Reranked → Rule Filter",
            _value,
            help="Percentage of reranked candidates that survive the Stage 4 "
                 "RULE filter (MeSH site relevance, stage, histology, age, "
                 "sex). The quality gate and the cost cap are further down and "
                 "are not counted here." + _note
        )
    
    with col3:
        _value, _note, _without_usable = _verdict_retention(df, _trial_rows)
        st.metric(
            "Evaluated → Eligible",
            _value,
            delta=(f"{_without_usable:,} without a usable result excluded"
                   if _without_usable else None),
            delta_color="off",
            help=f"Eligible trial verdicts as a share of trials with a "
                 f"{USABLE_RESULT_LABEL} (eligible, not eligible, or declared "
                 f"clinically uncertain)." + _note
        )


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
