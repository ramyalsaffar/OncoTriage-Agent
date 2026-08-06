"""
Patient Demographics tab. Moved verbatim out of "21- Streamlit Dashboard.py"
(pass 20c-3c-1).
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


def render_patient_demographics_tab(df):
    """Render Patient Demographics tab with equity analysis."""
    
    st.header("👥 Patient Demographics & Equity Analysis")
    
    # --- Summary Metrics ---
    col1, col2, col3, col4, col5, col6, col7 = st.columns(7)

    overall_match_rate = (df['eligible_matches'] > 0).mean() * 100
    full_match_rate_demo = (df['match_tier'] == 'Full Match').mean() * 100
    partial_match_rate_demo = (df['match_tier'] == 'Partial Match').mean() * 100
    unconfirmed_match_rate_demo = (df['match_tier'] == 'Unconfirmed Match').mean() * 100

    with col1:
        st.metric("Total Patients", df['patient_id'].nunique(),
                  help="Unique patients in current filter")
    with col2:
        st.metric("Median Age", f"{df['age'].median():.0f}",
                  help="Median patient age")
    with col3:
        st.metric("Avg Conditions", f"{df['condition_count'].mean():.1f}",
                  help="Average number of conditions per patient")
    with col4:
        st.metric("✅ Full Match", f"{full_match_rate_demo:.1f}%",
                  help="Patients with at least 1 trial where ALL criteria confirmed (100% score)")
    with col5:
        st.metric("🟡 Partial Match", f"{partial_match_rate_demo:.1f}%",
                  help="Patients whose best trial had SOME criteria confirmed (0% < score < 100%)")
    with col6:
        st.metric("🔶 Unconfirmed", f"{unconfirmed_match_rate_demo:.1f}%",
                  help="Patients whose only eligible trials scored 0% — no disqualifier "
                       "found, but no criterion confirmed either")
    with col7:
        st.metric("Any Match", f"{overall_match_rate:.1f}%",
                  help="Patients with at least 1 eligible trial (full, partial, or "
                       "unconfirmed). Used as baseline in charts below")
    
    st.markdown("---")
    
    # =========================================================================
    # 1. Age & Sex — Match Success
    # =========================================================================
    st.subheader("Age & Sex — Match Success")
    
    col1, col2 = st.columns(2)
    
    with col1:
        df_age = df.copy()
        df_age['age_group'] = (df_age['age'] // 10) * 10
        df_age['age_label'] = df_age['age_group'].astype(int).astype(str) + 's'
        age_stats = df_age.groupby('age_label').agg(
            patient_count=('patient_id', 'count'),
            avg_matches=('eligible_matches', 'mean'),
            match_rate=('eligible_matches', lambda x: (x > 0).mean() * 100)
        ).reset_index().sort_values('age_label')
        
        fig_age = go.Figure()
        fig_age.add_trace(go.Bar(
            x=age_stats['age_label'], y=age_stats['match_rate'],
            text=[f"n={n}" for n in age_stats['patient_count']],
            textposition='outside', marker_color='#1f77b4',
            name='Match Rate %'
        ))
        fig_age.update_layout(
            title='Match Rate by Age Group',
            yaxis_title='Match Rate (%)',
            height=320, margin=dict(l=20, r=20, t=40, b=20),
            template='plotly_white', showlegend=False
        )
        fig_age.update_yaxes(range=[0, max(age_stats['match_rate'].max() * 1.2, 10)])
        st.plotly_chart(fig_age, use_container_width=True)
    
    with col2:
        sex_stats = df.groupby('sex').agg(
            patient_count=('patient_id', 'count'),
            avg_matches=('eligible_matches', 'mean'),
            match_rate=('eligible_matches', lambda x: (x > 0).mean() * 100)
        ).reset_index()
        
        fig_sex = go.Figure()
        fig_sex.add_trace(go.Bar(
            x=sex_stats['sex'], y=sex_stats['match_rate'],
            text=[f"n={n}" for n in sex_stats['patient_count']],
            textposition='outside', marker_color=['#ff7f0e', '#2ca02c', '#9467bd'][:len(sex_stats)],
            name='Match Rate %'
        ))
        fig_sex.update_layout(
            title='Match Rate by Sex',
            yaxis_title='Match Rate (%)',
            height=320, margin=dict(l=20, r=20, t=40, b=20),
            template='plotly_white', showlegend=False
        )
        fig_sex.update_yaxes(range=[0, max(sex_stats['match_rate'].max() * 1.2, 10)])
        st.plotly_chart(fig_sex, use_container_width=True)
    
    st.markdown("---")
    
    # =========================================================================
    # 2. Race/Ethnicity — Equity Analysis
    # =========================================================================
    st.subheader("Race & Ethnicity — Equity Analysis")
    
    col1, col2 = st.columns(2)
    
    with col1:
        race_stats = df.groupby('race').agg(
            patient_count=('patient_id', 'count'),
            avg_matches=('eligible_matches', 'mean'),
            match_rate=('eligible_matches', lambda x: (x > 0).mean() * 100)
        ).reset_index().sort_values('patient_count', ascending=False)
        
        # Only show groups with enough patients
        race_stats = race_stats[race_stats['patient_count'] >= 1]
        
        fig_race = go.Figure()
        fig_race.add_trace(go.Bar(
            y=race_stats['race'], x=race_stats['match_rate'],
            orientation='h',
            text=[f"n={n}  ({r:.0f}%)" for n, r in zip(race_stats['patient_count'], race_stats['match_rate'])],
            textposition='outside', textfont=dict(size=11), marker_color='#1f77b4',
            cliponaxis=False
        ))
        fig_race.update_layout(
            title='Match Rate by Race',
            xaxis_title='Match Rate (%)',
            height=max(280, len(race_stats) * 50),
            margin=dict(l=20, r=130, t=40, b=20),
            template='plotly_white', showlegend=False,
            yaxis=dict(autorange='reversed', tickfont=dict(size=12))
        )
        fig_race.update_xaxes(range=[0, max(race_stats['match_rate'].max() * 1.6, 10)])
        st.plotly_chart(fig_race, use_container_width=True)
    
    with col2:
        eth_stats = df.groupby('ethnicity').agg(
            patient_count=('patient_id', 'count'),
            avg_matches=('eligible_matches', 'mean'),
            match_rate=('eligible_matches', lambda x: (x > 0).mean() * 100)
        ).reset_index().sort_values('patient_count', ascending=False)
        
        eth_stats = eth_stats[eth_stats['patient_count'] >= 1]
        
        fig_eth = go.Figure()
        fig_eth.add_trace(go.Bar(
            y=eth_stats['ethnicity'], x=eth_stats['match_rate'],
            orientation='h',
            text=[f"n={n}  ({r:.0f}%)" for n, r in zip(eth_stats['patient_count'], eth_stats['match_rate'])],
            textposition='outside', textfont=dict(size=11), marker_color='#ff7f0e',
            cliponaxis=False
        ))
        fig_eth.update_layout(
            title='Match Rate by Ethnicity',
            xaxis_title='Match Rate (%)',
            height=max(280, len(eth_stats) * 50),
            margin=dict(l=20, r=130, t=40, b=20),
            template='plotly_white', showlegend=False,
            yaxis=dict(autorange='reversed', tickfont=dict(size=12))
        )
        fig_eth.update_xaxes(range=[0, max(eth_stats['match_rate'].max() * 1.6, 10)])
        st.plotly_chart(fig_eth, use_container_width=True)
    
    # Demographic Parity Metric
    if len(race_stats) > 1:
        max_rate = race_stats['match_rate'].max()
        min_rate = race_stats['match_rate'].min()
        parity_ratio = min_rate / max_rate if max_rate > 0 else 0
        
        max_group = race_stats.loc[race_stats['match_rate'].idxmax(), 'race']
        min_group = race_stats.loc[race_stats['match_rate'].idxmin(), 'race']
        
        col1, col2, col3 = st.columns(3)
        with col1:
            color = "normal" if parity_ratio >= 0.8 else "inverse"
            st.metric(
                "Demographic Parity Ratio",
                f"{parity_ratio:.2f}",
                delta="≥0.80 target" if parity_ratio >= 0.8 else f"{parity_ratio - 0.8:+.2f} from target",
                delta_color=color,
                help="Min group match rate / max group match rate. 1.0 = perfect parity, ≥0.80 = acceptable (four-fifths rule)"
            )
        with col2:
            st.metric("Highest Match Rate", f"{max_rate:.1f}%",
                      help=f"Group: {max_group}")
        with col3:
            st.metric("Lowest Match Rate", f"{min_rate:.1f}%",
                      help=f"Group: {min_group}")
    
    st.markdown("---")
    
    # =========================================================================
    # 3. Cancer Type / Primary Condition Distribution
    # =========================================================================
    st.subheader("Cancer Type & Condition Distribution")
    
    col1, col2 = st.columns(2)
    
    with col1:
        condition_stats = df.groupby('primary_condition').agg(
            patient_count=('patient_id', 'count'),
            avg_matches=('eligible_matches', 'mean'),
            match_rate=('eligible_matches', lambda x: (x > 0).mean() * 100)
        ).reset_index().sort_values('patient_count', ascending=False)
        
        # Top 15 conditions
        top_conditions = condition_stats.head(15).sort_values('patient_count', ascending=True)
        
        fig_cond = go.Figure()
        fig_cond.add_trace(go.Bar(
            y=top_conditions['primary_condition'].str[:40],
            x=top_conditions['patient_count'],
            orientation='h',
            text=[f"{r:.0f}% match" for r in top_conditions['match_rate']],
            textposition='outside',
            marker_color='#2ca02c'
        ))
        fig_cond.update_layout(
            title='Top Conditions (by patient count)',
            xaxis_title='Patients',
            height=max(400, len(top_conditions) * 32),
            margin=dict(l=20, r=120, t=40, b=20),
            template='plotly_white', showlegend=False
        )
        fig_cond.update_xaxes(range=[0, top_conditions['patient_count'].max() * 1.6])
        st.plotly_chart(fig_cond, use_container_width=True)
    
    with col2:
        # Conditions by match rate (min 2 patients to avoid noise)
        viable_conditions = condition_stats[condition_stats['patient_count'] >= 2].copy()
        
        if not viable_conditions.empty:
            # Show best and worst matched conditions
            best = viable_conditions.nlargest(8, 'match_rate')
            worst = viable_conditions.nsmallest(8, 'match_rate')
            combined = pd.concat([best, worst]).drop_duplicates()
            combined = combined.sort_values('match_rate', ascending=True)
            
            colors = ['#d62728' if r < overall_match_rate else '#2ca02c' for r in combined['match_rate']]
            
            fig_rate = go.Figure()
            fig_rate.add_trace(go.Bar(
                y=combined['primary_condition'].str[:40],
                x=combined['match_rate'],
                orientation='h',
                text=[f"n={n} ({r:.0f}%)" for n, r in zip(combined['patient_count'], combined['match_rate'])],
                textposition='outside',
                textfont=dict(size=11),
                marker_color=colors
            ))
            fig_rate.add_vline(
                x=overall_match_rate, line_dash="dash", line_color="gray",
                annotation_text=f"Avg: {overall_match_rate:.0f}%",
                annotation_position="top right"
            )
            fig_rate.update_layout(
                title='Match Rate by Condition (best & worst)',
                xaxis_title='Match Rate (%)',
                height=max(400, len(combined) * 32),
                margin=dict(l=20, r=120, t=40, b=20),
                template='plotly_white', showlegend=False
            )
            fig_rate.update_xaxes(range=[0, max(combined['match_rate'].max() * 1.5, 10)])
            st.plotly_chart(fig_rate, use_container_width=True)
        else:
            st.info("Not enough patients per condition for match rate analysis.")
    
    st.markdown("---")
    
    # =========================================================================
    # 4. Condition & Medication Burden
    # =========================================================================
    st.subheader("Comorbidity & Medication Burden")
    
    col1, col2 = st.columns(2)
    
    with col1:
        df_burden = df.copy()
        df_burden['condition_bucket'] = pd.cut(
            df_burden['condition_count'],
            bins=[-1, 2, 5, 10, 20, 100],
            labels=['0-2', '3-5', '6-10', '11-20', '20+'],
            right=True
        )
        burden_stats = df_burden.groupby('condition_bucket', observed=True).agg(
            patient_count=('patient_id', 'count'),
            match_rate=('eligible_matches', lambda x: (x > 0).mean() * 100),
            avg_matches=('eligible_matches', 'mean')
        ).reset_index()
        
        fig_burden = go.Figure()
        fig_burden.add_trace(go.Bar(
            x=burden_stats['condition_bucket'].astype(str),
            y=burden_stats['match_rate'],
            text=[f"n={n}" for n in burden_stats['patient_count']],
            textposition='outside', marker_color='#9467bd'
        ))
        fig_burden.update_layout(
            title='Match Rate by Condition Count',
            xaxis_title='Number of Conditions',
            yaxis_title='Match Rate (%)',
            height=320, margin=dict(l=20, r=20, t=40, b=20),
            template='plotly_white', showlegend=False
        )
        fig_burden.update_yaxes(range=[0, max(burden_stats['match_rate'].max() * 1.2, 10)])
        st.plotly_chart(fig_burden, use_container_width=True)
    
    with col2:
        df_meds = df.copy()
        df_meds['med_bucket'] = pd.cut(
            df_meds['medication_count'],
            bins=[-1, 3, 7, 12, 20, 100],
            labels=['0-3', '4-7', '8-12', '13-20', '20+'],
            right=True
        )
        med_stats = df_meds.groupby('med_bucket', observed=True).agg(
            patient_count=('patient_id', 'count'),
            match_rate=('eligible_matches', lambda x: (x > 0).mean() * 100),
            avg_matches=('eligible_matches', 'mean')
        ).reset_index()
        
        fig_meds = go.Figure()
        fig_meds.add_trace(go.Bar(
            x=med_stats['med_bucket'].astype(str),
            y=med_stats['match_rate'],
            text=[f"n={n}" for n in med_stats['patient_count']],
            textposition='outside', marker_color='#8c564b'
        ))
        fig_meds.update_layout(
            title='Match Rate by Medication Count',
            xaxis_title='Number of Medications',
            yaxis_title='Match Rate (%)',
            height=320, margin=dict(l=20, r=20, t=40, b=20),
            template='plotly_white', showlegend=False
        )
        fig_meds.update_yaxes(range=[0, max(med_stats['match_rate'].max() * 1.2, 10)])
        st.plotly_chart(fig_meds, use_container_width=True)
    
    st.caption(
        "Match rate = percentage of patients in each bucket with at least one eligible trial. "
        )
     
    st.markdown("---")
    
    # =========================================================================
    # 5. Population Overview
    # =========================================================================
    st.subheader("Population Overview")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        fig_age_dist = px.histogram(
            df, x='age', nbins=20,
            labels={'age': 'Age'},
            template='plotly_white',
            title='Age Distribution'
        )
        fig_age_dist.add_vline(
            x=df['age'].median(), line_dash="dash", line_color="red"
        )
        fig_age_dist.add_annotation(
            x=df['age'].median(), y=1, yref="paper",
            text=f"Median: {df['age'].median():.0f}",
            showarrow=True, arrowhead=0, ax=40, ay=-25,
            font=dict(size=11, color="red"),
            bgcolor="white", borderpad=2
        )
        fig_age_dist.update_traces(marker_color='#1f77b4')
        fig_age_dist.update_layout(height=340, margin=dict(l=20, r=20, t=40, b=20), showlegend=False)
        st.plotly_chart(fig_age_dist, use_container_width=True)
    
    with col2:
        race_counts = df['race'].value_counts().reset_index()
        race_counts.columns = ['Race', 'Count']
        fig_race_pie = px.pie(
            race_counts, values='Count', names='Race',
            template='plotly_white', title='Race Distribution'
        )
        fig_race_pie.update_traces(textposition='inside', textinfo='percent', textfont_size=11)
        fig_race_pie.update_layout(
            height=340, margin=dict(l=10, r=10, t=40, b=10),
            legend=dict(orientation='h', yanchor='top', y=-0.05, xanchor='center', x=0.5, font=dict(size=11))
        )
        st.plotly_chart(fig_race_pie, use_container_width=True)
    
    with col3:
        sex_counts = df['sex'].value_counts().reset_index()
        sex_counts.columns = ['Sex', 'Count']
        fig_sex_pie = px.pie(
            sex_counts, values='Count', names='Sex',
            template='plotly_white', title='Sex Distribution'
        )
        fig_sex_pie.update_traces(textposition='inside', textinfo='percent+label', textfont_size=12)
        fig_sex_pie.update_layout(
            height=340, margin=dict(l=10, r=10, t=40, b=10),
            legend=dict(orientation='h', yanchor='top', y=-0.05, xanchor='center', x=0.5, font=dict(size=11))
        )
        st.plotly_chart(fig_sex_pie, use_container_width=True)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
