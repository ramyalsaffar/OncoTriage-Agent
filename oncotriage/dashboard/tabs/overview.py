"""
Overview tab. Moved verbatim out of "21- Streamlit Dashboard.py" (pass 20c-3c-1).
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from oncotriage.dashboard.data import load_trial_matches_data
from oncotriage.dashboard.tiers import MATCH_TIER_COLORS


def render_overview_tab(df):
    """Render Overview tab with KPIs and high-level visualizations."""
    
    st.header("📊 Pipeline Overview")
    
    # Custom CSS for smaller KPI values
    st.markdown("""
        <style>
        [data-testid="stMetricValue"] {
            font-size: 24px;
        }
        </style>
    """, unsafe_allow_html=True)
    
    # Top-level KPIs
    col1, col2, col3, col4, col5, col6, col7 = st.columns(7)

    total_inferences = len(df)
    unique_patients = df['patient_id'].nunique()
    avg_cost = df['estimated_cost_usd'].mean()

    full_rate = (df['match_tier'] == 'Full Match').sum() / len(df) * 100 if len(df) > 0 else 0
    partial_rate = (df['match_tier'] == 'Partial Match').sum() / len(df) * 100 if len(df) > 0 else 0
    unconfirmed_rate = (df['match_tier'] == 'Unconfirmed Match').sum() / len(df) * 100 if len(df) > 0 else 0
    no_match_rate = (df['match_tier'] == 'No Match').sum() / len(df) * 100 if len(df) > 0 else 0

    with col1:
        st.metric(
            "Total Inferences",
            f"{total_inferences:,}",
            delta=f"{unique_patients:,} patients",
            help="Pipeline runs and unique patients processed"
        )
    
    with col2:
        st.metric(
            "Avg Cost/Patient",
            f"${avg_cost:.4f}",
            help="Average API cost per patient inference"
        )
    
    with col3:
        st.metric(
            "✅ Full Match",
            f"{full_rate:.1f}%",
            delta=f"{(df['match_tier'] == 'Full Match').sum()} patients",
            help="Patients with at least 1 trial where ALL criteria were confirmed met (100% match score)"
        )
    
    with col4:
        st.metric(
            "🟡 Partial Match",
            f"{partial_rate:.1f}%",
            delta=f"{(df['match_tier'] == 'Partial Match').sum()} patients",
            help="Patients whose best trial had SOME criteria confirmed but not all (0% < score < 100%)"
        )

    with col5:
        st.metric(
            "🔶 Unconfirmed",
            f"{unconfirmed_rate:.1f}%",
            delta=f"{(df['match_tier'] == 'Unconfirmed Match').sum()} patients",
            delta_color="inverse",
            help=(
                "Patients whose only eligible trials scored 0% — no disqualifier "
                "was found, but not a single criterion could be confirmed. "
                "Eligible on paper, nothing established."
            )
        )

    with col6:
        st.metric(
            "❌ No Match",
            f"{no_match_rate:.1f}%",
            delta=f"{(df['match_tier'] == 'No Match').sum()} patients",
            delta_color="inverse",
            help="Patients with no eligible trial matches"
        )

    with col7:
        # "Any Match" counts every eligible trial including the unconfirmable
        # ones, so it is deliberately shown next to the tier split rather than
        # in place of it.
        any_match_rate = full_rate + partial_rate + unconfirmed_rate
        any_match_count = (
            (df['match_tier'] == 'Full Match').sum()
            + (df['match_tier'] == 'Partial Match').sum()
            + (df['match_tier'] == 'Unconfirmed Match').sum()
        )
        st.metric(
            "Any Match",
            f"{any_match_rate:.1f}%",
            delta=f"{any_match_count} patients",
            help="Patients with at least 1 eligible trial (full, partial, or unconfirmed)"
        )

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
            'GPT-4o Eval': df['gpt4o_evaluation_time'].mean()
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
        if col in df.columns:
            missing = df[col].isna() | (df[col].astype(str).str.strip().isin(['', 'None', 'Unknown', 'unknown']))
            missing_count = missing.sum()
            complete_pct = (1 - missing_count / len(df)) * 100 if len(df) > 0 else 0
            field_stats.append({
                'Field': label,
                'Complete': len(df) - missing_count,
                'Missing': missing_count,
                'Complete %': complete_pct
            })
    
    # Also check for suspiciously empty clinical records
    zero_conditions = (df['condition_count'] == 0).sum() if 'condition_count' in df.columns else 0
    zero_medications = (df['medication_count'] == 0).sum() if 'medication_count' in df.columns else 0
    
    field_stats.append({
        'Field': 'Conditions (≥1)',
        'Complete': len(df) - zero_conditions,
        'Missing': zero_conditions,
        'Complete %': (1 - zero_conditions / len(df)) * 100 if len(df) > 0 else 0
    })
    field_stats.append({
        'Field': 'Medications (≥1)',
        'Complete': len(df) - zero_medications,
        'Missing': zero_medications,
        'Complete %': (1 - zero_medications / len(df)) * 100 if len(df) > 0 else 0
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
        worst_field = completeness_df.loc[completeness_df['Complete %'].idxmin()]
        st.metric(
            "Weakest Field",
            worst_field['Field'],
            delta=f"{worst_field['Complete %']:.0f}% complete",
            delta_color="inverse" if worst_field['Complete %'] < 90 else "normal",
            help="Field with the lowest completeness rate"
        )
    
    with col4:
        all_complete_mask = pd.Series(True, index=df.index)
        for col_name in completeness_fields.keys():
            if col_name in df.columns:
                field_missing = df[col_name].isna() | (df[col_name].astype(str).str.strip().isin(['', 'None', 'Unknown', 'unknown']))
                all_complete_mask = all_complete_mask & ~field_missing
        if 'condition_count' in df.columns:
            all_complete_mask = all_complete_mask & (df['condition_count'] > 0)
        if 'medication_count' in df.columns:
            all_complete_mask = all_complete_mask & (df['medication_count'] > 0)
        patients_all_complete = all_complete_mask.sum()
        pct_all_complete = patients_all_complete / len(df) * 100 if len(df) > 0 else 0
        st.metric(
            "Patients Fully Complete",
            f"{pct_all_complete:.0f}%",
            delta=f"{patients_all_complete}/{len(df)}",
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
        "Missing includes null, blank, 'Unknown'. "
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
            explanations = filtered_tm['explanation'].fillna('').str.lower()
            
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
        retrieved_total = df['candidates_retrieved'].sum()
        reranked_total = df['candidates_reranked'].sum()
        rerank_retention = (reranked_total / retrieved_total * 100) if retrieved_total > 0 else 0
        st.metric(
            "Retrieved → Reranked",
            f"{rerank_retention:.1f}%",
            help="Percentage of retrieved candidates that survive cross-encoder reranking"
        )
    
    with col2:
        filtered_total = df['candidates_filtered'].sum()
        filter_retention = (filtered_total / reranked_total * 100) if reranked_total > 0 else 0
        st.metric(
            "Reranked → Rule Filter",
            f"{filter_retention:.1f}%",
            help="Percentage of reranked candidates that pass quality + rule filters"
        )
    
    with col3:
        evaluated_total = df['candidates_evaluated'].sum()
        eligible_total = df['eligible_matches'].sum()
        eligibility_rate = (eligible_total / evaluated_total * 100) if evaluated_total > 0 else 0
        st.metric(
            "Evaluated → Eligible",
            f"{eligibility_rate:.1f}%",
            help="Percentage of GPT-4o evaluated trials that are eligible (full + partial matches)"
        )


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
