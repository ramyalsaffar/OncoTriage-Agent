"""
Match Quality tab. Moved verbatim out of "21- Streamlit Dashboard.py"
(pass 20c-3c-1).
"""

import pandas as pd
import plotly.express as px
import streamlit as st

from oncotriage.dashboard.data import load_trial_matches_data
from oncotriage.dashboard.tiers import (
    MATCH_TIERS,
    MATCH_TIER_COLORS,
    PATIENT_OUTCOME_FULL,
    PATIENT_OUTCOME_LABELS,
)


def render_match_quality_tab(df):
    """Render Match Quality tab."""
    
    st.header("🔍 Match Quality Analysis")
    
    st.subheader("Patient Complexity vs Match Success")
    
    tier_order = list(MATCH_TIERS)
    tier_colors = MATCH_TIER_COLORS
    
    col1, col2 = st.columns(2)
    
    with col1:
        fig_cond_box = px.box(
            df, x='match_tier', y='condition_count',
            category_orders={'match_tier': tier_order},
            color='match_tier',
            color_discrete_map=tier_colors,
            labels={'match_tier': 'Match Tier', 'condition_count': 'Condition Count'},
            template='plotly_white',
            title='Condition Count by Match Tier'
        )
        fig_cond_box.update_layout(
            height=350, margin=dict(l=20, r=20, t=40, b=20),
            showlegend=False
        )
        st.plotly_chart(fig_cond_box, use_container_width=True)
    
    with col2:
        fig_med_box = px.box(
            df, x='match_tier', y='medication_count',
            category_orders={'match_tier': tier_order},
            color='match_tier',
            color_discrete_map=tier_colors,
            labels={'match_tier': 'Match Tier', 'medication_count': 'Medication Count'},
            template='plotly_white',
            title='Medication Count by Match Tier'
        )
        fig_med_box.update_layout(
            height=350, margin=dict(l=20, r=20, t=40, b=20),
            showlegend=False
        )
        st.plotly_chart(fig_med_box, use_container_width=True)
        
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Avg Conditions", f"{df['condition_count'].mean():.1f}", help="Average conditions per patient in current filter")
    with col2:
        st.metric("Avg Medications", f"{df['medication_count'].mean():.1f}", help="Average medications per patient in current filter")
    with col3:
        st.metric("Max Conditions", f"{df['condition_count'].max()}", help="Most conditions for a single patient")
    with col4:
        st.metric("Max Medications", f"{df['medication_count'].max()}", help="Most medications for a single patient")
    
    st.markdown("---")
    
    # --- Match Score Distribution (all eligible inferences) ---
    trial_matches = load_trial_matches_data()
    
    if trial_matches is not None and not trial_matches.empty:
        filtered_ids = df['id'].tolist()
        filtered_matches = trial_matches[trial_matches['inference_id'].isin(filtered_ids)]
        eligible_inferences = filtered_matches[filtered_matches['eligible'] == 'eligible'].copy()
        
        if not eligible_inferences.empty:
            st.subheader("Match Score Distribution")
            
            scores_pct = (eligible_inferences['match_score'] * 100).round(0)
            
            col1, col2 = st.columns([2, 1])
            
            with col1:
                fig_score_dist = px.histogram(
                    x=scores_pct,
                    nbins=20,
                    labels={'x': 'Match Score (%)', 'count': 'Inferences'},
                    template='plotly_white',
                    title='Match Scores Across All Eligible Inferences'
                )
                fig_score_dist.update_traces(marker_color='#2ca02c')
                fig_score_dist.update_layout(
                    height=350,
                    margin=dict(l=20, r=20, t=40, b=20),
                    showlegend=False,
                    xaxis=dict(range=[0, 105], dtick=10),
                    yaxis_title="Inferences",
                    bargap=0.05
                )
                st.plotly_chart(fig_score_dist, use_container_width=True)
            
            with col2:
                full_match_inferences = (scores_pct == 100).sum()
                full_match_pct = full_match_inferences / len(scores_pct) * 100
                st.metric(
                    "Full Match Rate",
                    f"{full_match_pct:.1f}%",
                    delta=f"{full_match_inferences:,} / {len(scores_pct):,} inferences",
                    help="Percentage of eligible inferences with 100% match score (all criteria confirmed met)"
                )
                st.metric(
                    "Median Score",
                    f"{scores_pct.median():.0f}%",
                    help="Median match score across all eligible inferences"
                )
                st.metric(
                    "Score Spread",
                    f"{scores_pct.min():.0f}% – {scores_pct.max():.0f}%",
                    help="Range from lowest to highest match score among eligible inferences"
                )
            
            st.caption(
                "Each bar counts eligible patient-trial inferences at that match score. "
                "100% = all criteria confirmed met. Scores below 100% indicate missing patient data "
                "prevented full evaluation. One patient can appear multiple times across different trials."
            )
    
    st.markdown("---")
    st.subheader("Match Failure Analysis")
    
    if trial_matches is not None and not trial_matches.empty:
        filtered_matches = trial_matches[trial_matches['inference_id'].isin(filtered_ids)]
        partial_matches = filtered_matches[filtered_matches['eligible'] == 'not_eligible']
        
        if not partial_matches.empty:
            col1, col2 = st.columns(2)
            
            with col1:
                keywords = {
                    'Age': ['age', 'years old', 'older', 'younger'],
                    'Prior Treatment': ['prior', 'previous', 'chemotherapy', 'radiation'],
                    'Disease Stage': ['stage', 'metastatic', 'advanced'],
                    'Biomarkers': ['biomarker', 'mutation', 'receptor'],
                    'Performance': ['performance', 'ecog'],
                    'Lab Values': ['lab', 'creatinine', 'liver'],
                    'Comorbidities': ['comorbid', 'condition'],
                    'Pregnancy': ['pregnant', 'pregnancy']
                }
                
                counts = {}
                for cat, kws in keywords.items():
                    
                    explanations = partial_matches['explanation'].fillna('').str.lower()
                    count = explanations.apply(lambda x: any(kw in x for kw in kws)).sum()
                    
                    if count > 0:
                        counts[cat] = count
                
                total = len(partial_matches)
                rates = {cat: (cnt / total * 100) for cat, cnt in counts.items()}
                
                if counts:
                    kw_df = pd.DataFrame({'Exclusion Reason': list(counts.keys()), 'Count': list(counts.values())}).sort_values('Count', ascending=True)
                    
                    fig_ex = px.bar(kw_df, y='Exclusion Reason', x='Count', orientation='h', text='Count', template='plotly_white', title='Common Exclusions')
                    fig_ex.update_traces(textposition='outside', marker_color='#d62728')
                    fig_ex.update_layout(height=350, margin=dict(l=20, r=20, t=40, b=20), showlegend=False)
                    fig_ex.update_xaxes(range=[0, kw_df['Count'].max() * 1.2])
                    st.plotly_chart(fig_ex, use_container_width=True)
                else:
                    st.info("No clear patterns")
            
            with col2:
                if rates:
                    rate_df = pd.DataFrame({'Category': list(rates.keys()), 'Rate': list(rates.values())}).sort_values('Rate', ascending=False)
                    
                    fig_rt = px.bar(rate_df, x='Category', y='Rate', text='Rate', template='plotly_white', title='Exclusion Rate (%)')
                    fig_rt.update_traces(texttemplate='%{text:.1f}%', textposition='outside', marker_color='#ff7f0e')
                    fig_rt.update_layout(height=350, margin=dict(l=20, r=20, t=40, b=20), showlegend=False, xaxis_tickangle=-45)
                    fig_rt.update_yaxes(range=[0, rate_df['Rate'].max() * 1.2])
                    st.plotly_chart(fig_rt, use_container_width=True)
                else:
                    st.info("No clear patterns")
            
            st.caption(
                "Exclusion reasons are detected by keyword matching on GPT-4o free-text explanations. "
                "Counts are approximate and a single explanation may match multiple categories."
            )
            
            st.markdown("**Top Partial Match Examples:**")
            # Partial matches: eligible trials with 0 < score < 1.0 (some criteria
            # confirmed, others not evaluable). Trials scoring exactly 0.0 are
            # excluded and counted separately below — nothing was confirmed on
            # them, so they are not "close to full eligibility" in any sense and
            # listing them here would misrepresent what the pipeline established.
            eligible_matches_only = filtered_matches[filtered_matches['eligible'] == 'eligible']
            partial_misses = eligible_matches_only[
                (eligible_matches_only['match_score'] > 0.0)
                & (eligible_matches_only['match_score'] < 1.0)
            ]
            unconfirmed_misses = eligible_matches_only[
                eligible_matches_only['match_score'] <= 0.0
            ]
            if not partial_misses.empty:
                top = partial_misses.nlargest(5, 'match_score')[['nct_id', 'trial_title', 'match_score', 'explanation']].copy()
                top.columns = ['NCT ID', 'Trial', 'Score', 'Reason']
                top['Score'] = (top['Score'] * 100).round(0).astype(int)
                top['Trial'] = top['Trial'].str[:80]
                top['Reason'] = top['Reason'].str[:120]
                st.dataframe(top, use_container_width=True, hide_index=True,
                             column_config={
                                 'Score': st.column_config.NumberColumn(format='%d%%'),
                             })
                
                st.caption(
                    "Partial matches are eligible trials where some criteria could not be evaluated due to missing patient data. "
                    "Highest-scoring partials are the closest to full eligibility and may benefit most from additional clinical data."
                )
                
            else:
                st.info("No partial matches found — no eligible trial scored between 0% and 100%.")

            if not unconfirmed_misses.empty:
                st.markdown("**Unconfirmed Eligible Trials (0% of criteria confirmed):**")
                unc = unconfirmed_misses[['nct_id', 'trial_title', 'explanation']].head(5).copy()
                unc.columns = ['NCT ID', 'Trial', 'Reason']
                unc['Trial'] = unc['Trial'].str[:80]
                unc['Reason'] = unc['Reason'].str[:120]
                st.dataframe(unc, use_container_width=True, hide_index=True)
                st.caption(
                    f"{len(unconfirmed_misses)} eligible trial(s) scored 0%: no disqualifying "
                    "criterion was found, but not a single criterion could be confirmed either. "
                    "These are the weakest possible eligible result and are reported apart from "
                    "partial matches so they are not read as near-misses."
                )
        else:
            st.success("✓ No near-misses!")
    else:
        st.info("No match data")
    
    st.markdown("---")
    st.subheader("Quality Monitoring")
    
    col1, col2, col3, col4, col5 = st.columns(5)

    err_cnt = (df['error'].fillna('') != '').sum()
    err_rate = err_cnt / len(df) * 100 if len(df) > 0 else 0

    full_cnt = (df['match_tier'] == 'Full Match').sum()
    partial_cnt = (df['match_tier'] == 'Partial Match').sum()
    unconfirmed_cnt = (df['match_tier'] == 'Unconfirmed Match').sum()
    no_match_cnt = (df['match_tier'] == 'No Match').sum()

    with col1:
        st.metric("Error Rate", f"{err_rate:.1f}%", delta=f"{err_cnt} errors" if err_cnt > 0 else None, delta_color="inverse", help="Percentage of inferences that encountered pipeline errors")
    with col2:
        st.metric(PATIENT_OUTCOME_FULL, f"{full_cnt}", delta=f"{full_cnt / len(df) * 100:.1f}%" if len(df) > 0 else "0%", help="Patients with at least 1 trial where ALL criteria confirmed (100% score)")
    with col3:
        st.metric("🟡 Partial Match", f"{partial_cnt}", delta=f"{partial_cnt / len(df) * 100:.1f}%" if len(df) > 0 else "0%", help="Patients whose best trial had SOME criteria confirmed (0% < score < 100%)")
    with col4:
        st.metric("🔶 Unconfirmed", f"{unconfirmed_cnt}", delta=f"{unconfirmed_cnt / len(df) * 100:.1f}%" if len(df) > 0 else "0%", delta_color="inverse", help="Patients whose only eligible trials scored 0% — no disqualifier found, no criterion confirmed")
    with col5:
        st.metric("❌ No Match", f"{no_match_cnt}", delta=f"{no_match_cnt / len(df) * 100:.1f}%" if len(df) > 0 else "0%", delta_color="inverse", help="Patients with no eligible trial matches")
# =============================================================================
#     with col5:
#         total_evaluated = df['candidates_evaluated'].sum()
#         total_eligible = df['eligible_matches'].sum()
#         precision = total_eligible / total_evaluated if total_evaluated > 0 else 0
#         st.metric("Match Precision", f"{precision:.1%}", help="Eligible matches (full + partial) / total trials evaluated by GPT-4o")
# =============================================================================
    
    st.markdown("---")
    
    col1, col2 = st.columns(2)
    
    with col1:
        avg_full = df['full_match_count'].mean()
        avg_partial = df['partial_match_count'].mean()
        avg_unconfirmed = df['unconfirmed_match_count'].mean()
        mq = pd.DataFrame({
            'Category': ['Full Match', 'Partial Match', 'Unconfirmed Match'],
            'Avg Count': [avg_full, avg_partial, avg_unconfirmed]
        })

        fig_q = px.bar(mq, x='Category', y='Avg Count', text='Avg Count', template='plotly_white',
                       title='Avg Eligible Trials per Patient by Confirmation Level',
                       color='Category', color_discrete_map=MATCH_TIER_COLORS)
        fig_q.update_traces(texttemplate='%{text:.2f}', textposition='outside')
        fig_q.update_layout(height=350, margin=dict(l=20, r=20, t=40, b=20), showlegend=False)
        fig_q.update_yaxes(range=[0, max(mq['Avg Count'].max() * 1.3, 0.5)])
        st.plotly_chart(fig_q, use_container_width=True)
    
    with col2:
        # PATIENT_OUTCOME_LABELS is in MATCH_TIERS order and 'Count' is built in
        # that same order, so the two zip. Before pass 20f-3 the four labels were
        # typed here TWICE -- once as the `Outcome` list and once as the keys of
        # the colour map -- with one of the four borrowed from the per-TRIAL
        # vocabulary (TRIAL_STATUS_PARTIAL). Same strings, same colours, one
        # place to change them, and the per-patient chart no longer moves when
        # the per-trial labels do.
        sd = pd.DataFrame({
            'Outcome': list(PATIENT_OUTCOME_LABELS),
            'Count': [full_cnt, partial_cnt, unconfirmed_cnt, no_match_cnt]
        })

        fig_s = px.pie(sd, values='Count', names='Outcome', template='plotly_white',
                       title='Patient Match Distribution', color='Outcome',
                       color_discrete_map={
                           label: MATCH_TIER_COLORS[tier]
                           for label, tier in zip(PATIENT_OUTCOME_LABELS,
                                                  MATCH_TIERS)
                       })
        fig_s.update_traces(
            textposition='inside',
            textinfo='percent+label',
            textfont_size=13,
        )
        fig_s.update_layout(
            height=350, margin=dict(l=20, r=20, t=60, b=10),
            legend=dict(orientation='h', yanchor='top', y=-0.05, xanchor='center', x=0.5)
        )
        st.plotly_chart(fig_s, use_container_width=True)
    
    st.markdown("---")
    
    if err_cnt > 0:
        st.subheader("Error Details")
        
        err_df = df[df['error'].fillna('') != ''][['patient_id', 'timestamp', 'error', 'total_time', 'candidates_evaluated']].copy()
        err_df.columns = ['Patient ID', 'Timestamp', 'Error', 'Time (s)', 'Candidates']
        err_df['Timestamp'] = pd.to_datetime(err_df['Timestamp']).dt.strftime('%Y-%m-%d %H:%M')
        
        err_df['Time (s)'] = err_df['Time (s)'].round(1)
        err_df = err_df.reset_index(drop=True)
        err_df.index = err_df.index + 1
        err_df.index.name = "Row"
        st.dataframe(err_df, use_container_width=True, hide_index=False,
                     column_config={
                         'Error': st.column_config.TextColumn(width='large'),
                     })
        
        st.caption(
            "Pipeline errors that occurred during inference. Common causes include API timeouts, rate limits, "
            "and malformed patient data. Errors are non-fatal; the pipeline logs them and continues to the next patient."
        )
        
    else:
        st.success("✓ No errors")
    
    st.markdown("---")
    st.subheader("Top Matched Trials")
    
    if trial_matches is not None and not trial_matches.empty:
        filt_ids = df['id'].tolist()
        filt = trial_matches[trial_matches['inference_id'].isin(filt_ids)]
        
        if not filt.empty:
            elig = filt[filt['eligible'] == 'eligible']
            
            if not elig.empty:
                # Avg Score covers EVERY eligible inference for the trial,
                # including the ones scoring 0.0. Dropping those made the
                # average conditional on something having been confirmable,
                # so a trial nobody could confirm anything about showed the
                # same score as one confirmed on every criterion. The count of
                # zero-score inferences is reported beside it instead.
                elig = elig.copy()
                top = elig.groupby(['nct_id', 'trial_title']).agg(
                    match_count=('inference_id', 'count'),
                    avg_score=('match_score', 'mean'),
                    unconfirmed=('match_score', lambda s: int((s <= 0).sum())),
                ).reset_index()
                top.columns = ['NCT ID', 'Trial', 'Match Count', 'Avg Score', 'Unconfirmed']
                top = top.sort_values('Match Count', ascending=False).head(10)

                top['Avg Score'] = (top['Avg Score'] * 100).round(0).astype(int)
                top = top.reset_index(drop=True)
                top.index = top.index + 1
                top.index.name = "Row"
                st.dataframe(top, use_container_width=True, hide_index=False,
                             column_config={
                                 'Avg Score': st.column_config.NumberColumn(format='%d%%'),
                                 'Trial': st.column_config.TextColumn(width='large'),
                                 'Unconfirmed': st.column_config.NumberColumn(
                                     help='Eligible inferences scoring 0% — no criterion confirmable'),
                             })

                st.caption(
                    "Trials ranked by how many patients they matched (eligible). "
                    "Avg Score is the mean match score across ALL eligible inferences for that "
                    "trial, including those scoring 0%. Unconfirmed counts how many of those "
                    "inferences confirmed nothing at all: a high Unconfirmed count next to a "
                    "high Match Count means the trial passes patients through without the "
                    "pipeline establishing anything about their fit."
                )
                
            else:
                st.info("No eligible matches")
        else:
            st.info("No matches")
    else:
        st.info("No match data")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
