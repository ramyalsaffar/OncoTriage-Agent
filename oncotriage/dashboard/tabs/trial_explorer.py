"""
Trial Explorer tab. Moved verbatim out of "21- Streamlit Dashboard.py"
(pass 20c-3c-1).
"""

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from oncotriage.dashboard.data import load_trial_matches_data
from oncotriage.dashboard.tiers import TRIAL_STATUS_PARTIAL, TRIAL_STATUS_REJECTED, TRIAL_STATUS_UNCONFIRMED, classify_trial_score


@st.fragment
def render_trial_explorer_tab(df):
    """Render Trial Explorer tab — select a trial, see which patients matched."""
    
    st.header("🧬 Trial Explorer")
    
    trial_matches = load_trial_matches_data()
    
    if trial_matches is None or trial_matches.empty:
        st.info("No trial match data available. Run the pipeline first.")
        return
    
    # Filter to trials in the current filtered dataset
    filtered_ids = df['id'].tolist()
    filtered_matches = trial_matches[trial_matches['inference_id'].isin(filtered_ids)].copy()
    
    if filtered_matches.empty:
        st.info("No trial matches in the current filter selection.")
        return
    
    # Build trial selector: NCT ID + title, sorted by unique patient count
    # Join patient_id onto trial matches to deduplicate resampled inferences
    filtered_matches = filtered_matches.merge(
        df[['id', 'patient_id']],
        left_on='inference_id',
        right_on='id',
        how='left',
        suffixes=('', '_inf')
    ).drop(columns='id_inf', errors='ignore')
    
    trial_summary = filtered_matches.groupby(['nct_id', 'trial_title']).agg(
        total_patients=('patient_id', 'nunique'),
        eligible_count=('eligible', lambda x: (x == 'eligible').sum()),
        avg_score=('match_score', 'mean')
    ).reset_index().sort_values('total_patients', ascending=False)
    
    trial_options = trial_summary.apply(
        lambda r: f"{r['nct_id']} — {r['trial_title'][:55]}  ({r['total_patients']} patients)",
        axis=1
    ).tolist()
    
    selected_idx = st.selectbox(
        "Select Trial",
        range(len(trial_options)),
        format_func=lambda i: trial_options[i],
        key="trial_explorer_select"
    )
    
    selected_trial = trial_summary.iloc[selected_idx]
    selected_nct = selected_trial['nct_id']
    
    # --- Trial Summary Metrics ---
    trial_data = filtered_matches[filtered_matches['nct_id'] == selected_nct].copy()
    
    # Deduplicate by patient_id: keep the best inference per patient for this trial
    # (highest match_score, then eligible over not_eligible)
    eligibility_rank = {'eligible': 0, 'not_eligible': 1}
    trial_dedup = trial_data.copy()
    trial_dedup['_elig_rank'] = trial_dedup['eligible'].map(eligibility_rank).fillna(2)
    trial_dedup = (
        trial_dedup
        .sort_values(['_elig_rank', 'match_score'], ascending=[True, False])
        .drop_duplicates(subset='patient_id', keep='first')
        .drop(columns='_elig_rank')
    )
    
    _elig_mask = trial_dedup['eligible'] == 'eligible'
    eligible_patients = _elig_mask.sum()
    # Split eligible by what was actually confirmed. A patient scoring 0.0 on
    # this trial is eligible only in the sense that no disqualifier was found;
    # counting them as a partial match asserts partial confirmation that never
    # happened.
    partial_patients = (_elig_mask
                        & (trial_dedup['match_score'] > 0.0)
                        & (trial_dedup['match_score'] < 1.0)).sum()
    unconfirmed_patients = (_elig_mask & (trial_dedup['match_score'] <= 0.0)).sum()
    full_eligible = eligible_patients - partial_patients - unconfirmed_patients
    not_eligible_patients = (trial_dedup['eligible'] == 'not_eligible').sum()
    total_patients = eligible_patients + not_eligible_patients

    st.subheader(f"{selected_trial['trial_title']}")
    st.caption(f"NCT ID: {selected_nct}  |  Phase: {trial_data['trial_phase'].iloc[0]}")

    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric(
            "Total Patients Evaluated",
            total_patients,
            help="Number of patients whose pipeline evaluated this trial"
        )
    with col2:
        st.metric(
            "Eligible",
            full_eligible,
            help="Patients with 100% criteria confirmed"
        )
    with col3:
        st.metric(
            "Partial Match",
            partial_patients,
            help="Patients eligible with SOME criteria confirmed but not all (0% < score < 100%)"
        )

    with col4:
        st.metric(
            "Unconfirmed",
            unconfirmed_patients,
            help="Patients eligible at 0% — no disqualifier found, but no criterion confirmed either"
        )

    with col5:
        st.metric(
            "Not Eligible",
            not_eligible_patients,
            help="Patients with at least one disqualifying criterion"
        )
    
    st.markdown("---")
    
    # --- Patient Table ---
    st.subheader("Matched Patients")
    
    patient_details = trial_data.merge(
        df[['id', 'patient_id', 'age', 'sex', 'primary_condition', 'condition_count', 'medication_count']],
        left_on='inference_id',
        right_on='id',
        how='left',
        suffixes=('', '_inf')
    )
    
    def classify_trial_status(row):
        if row['eligible'] != 'eligible':
            return TRIAL_STATUS_REJECTED
        tier = classify_trial_score(row['match_score'])
        if tier == 'Full Match':
            return '✅ Eligible'
        if tier == 'Partial Match':
            return TRIAL_STATUS_PARTIAL
        return TRIAL_STATUS_UNCONFIRMED

    patient_details['Status'] = patient_details.apply(classify_trial_status, axis=1)
    patient_details['Match Score'] = (patient_details['match_score'] * 100).round(0).astype(int)

    status_filter = st.selectbox(
        "Filter by Status",
        ["All", "✅ Eligible", TRIAL_STATUS_PARTIAL, TRIAL_STATUS_UNCONFIRMED,
         TRIAL_STATUS_REJECTED],
        key="trial_explorer_status_filter"
    )
    
    if status_filter != "All":
        patient_details = patient_details[patient_details['Status'] == status_filter]
    
    if patient_details.empty:
        st.info("No patients match the selected status filter.")
        return
    
    status_order = {'✅ Eligible': 0, TRIAL_STATUS_PARTIAL: 1,
                    TRIAL_STATUS_UNCONFIRMED: 2, TRIAL_STATUS_REJECTED: 3}
    patient_details['_sort'] = patient_details['Status'].map(status_order)
    patient_details = patient_details.sort_values(
        by=['_sort', 'Match Score'],
        ascending=[True, False]
    )
    
    display_cols = ['Status', 'patient_id', 'age', 'sex', 'primary_condition', 'Match Score', 'assessment']
    display_df = patient_details[display_cols].copy()
    
    display_df.columns = ['Status', 'Patient ID', 'Age', 'Sex', 'Primary Condition', 'Match Score', 'Explanation']
    
    display_df = display_df.reset_index(drop=True)
    display_df.index = display_df.index + 1
    display_df.index.name = "Row"
    
    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=False,
        height=min(500, 35 * (len(display_df) + 1)),
        column_config={
            'Match Score': st.column_config.NumberColumn(format='%d%%'),
            'Primary Condition': st.column_config.Column(width='medium'),
            'Explanation': st.column_config.Column(width='large'),
        }
    )
    
    st.caption(
        f"{(patient_details['Status'] == '✅ Eligible').sum()} eligible · "
        f"{(patient_details['Status'] == TRIAL_STATUS_PARTIAL).sum()} partial · "
        f"{(patient_details['Status'] == TRIAL_STATUS_UNCONFIRMED).sum()} unconfirmed · "
        f"{(patient_details['Status'] == TRIAL_STATUS_REJECTED).sum()} not eligible · "
        f"{len(patient_details)} total"
    )
    
    st.markdown("---")
    
    # --- Demographics Breakdown for This Trial ---
    st.subheader("Patient Demographics for This Trial")
    
    # Use all evaluated patients (not just filtered by status)
    all_patients = trial_data.merge(
        df[['id', 'patient_id', 'age', 'sex', 'race', 'ethnicity', 'primary_condition']],
        left_on='inference_id', right_on='id', how='left', suffixes=('', '_inf')
    )
    eligible_data = all_patients
    
    col1, col2 = st.columns(2)
    
    with col1:
        if not eligible_data.empty:
            fig_age = px.histogram(
                eligible_data, x='age', nbins=10,
                labels={'age': 'Age'},
                template='plotly_white',
                title='Age Distribution (Evaluated Patients)'
            )
            fig_age.update_layout(height=300, margin=dict(l=20, r=20, t=40, b=20), showlegend=False)
            fig_age.update_traces(marker_color='#2ca02c')
            st.plotly_chart(fig_age, use_container_width=True)
        else:
            st.info("No patients evaluated for age distribution.")
    
    with col2:
        if not eligible_data.empty:
            sex_counts = eligible_data['sex'].value_counts().reset_index()
            sex_counts.columns = ['Sex', 'Count']
            fig_sex = px.pie(
                sex_counts, values='Count', names='Sex',
                template='plotly_white',
                title='Sex Distribution (Evaluated Patients)'
            )
            fig_sex.update_traces(textposition='inside', textinfo='percent+label', textfont_size=13)
            fig_sex.update_layout(
                height=300, margin=dict(l=10, r=10, t=40, b=10),
                legend=dict(orientation='h', yanchor='top', y=-0.05, xanchor='center', x=0.5)
            )
            st.plotly_chart(fig_sex, use_container_width=True)
        else:
            st.info("No patients evaluated for sex distribution.")
    
    col1, col2 = st.columns(2)
    
    with col1:
        if not eligible_data.empty and 'race' in eligible_data.columns:
            race_counts = eligible_data['race'].value_counts().reset_index()
            race_counts.columns = ['Race', 'Count']
            fig_race = px.pie(
                race_counts, values='Count', names='Race',
                template='plotly_white',
                title='Race Distribution (Evaluated Patients)'
            )
            fig_race.update_traces(textposition='inside', textinfo='percent', textfont_size=11)
            fig_race.update_layout(
                height=300, margin=dict(l=10, r=10, t=40, b=10),
                legend=dict(orientation='h', yanchor='top', y=-0.05, xanchor='center', x=0.5, font=dict(size=11))
            )
            st.plotly_chart(fig_race, use_container_width=True)
        else:
            st.info("No patients evaluated for race distribution.")
    
    with col2:
        if not eligible_data.empty and 'primary_condition' in eligible_data.columns:
            cond_counts = eligible_data['primary_condition'].value_counts().head(10).reset_index()
            cond_counts.columns = ['Condition', 'Count']
            fig_cond = go.Figure(go.Bar(
                y=cond_counts['Condition'],
                x=cond_counts['Count'],
                orientation='h',
                marker_color='#9467bd',
                text=cond_counts['Count'],
                textposition='outside',
                cliponaxis=False
            ))
            fig_cond.update_layout(
                title='Top Conditions (Evaluated Patients)',
                height=max(300, len(cond_counts) * 35),
                margin=dict(l=0, r=60, t=40, b=20),
                template='plotly_white', showlegend=False,
                yaxis=dict(autorange='reversed', automargin=True)
            )
            fig_cond.update_xaxes(range=[0, max(cond_counts['Count'].max() * 1.3, 1)])
            st.plotly_chart(fig_cond, use_container_width=True)
        else:
            st.info("No patients evaluated for condition distribution.")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
