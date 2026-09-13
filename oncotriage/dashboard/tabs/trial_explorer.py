"""
Trial Explorer tab. Moved verbatim out of "21- Streamlit Dashboard.py"
(pass 20c-3c-1).
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from oncotriage.dashboard.data import load_trial_matches_data
from oncotriage.dashboard.populations import (NOT_EVALUATED_CLINICAL_UNCERTAINTY,
                                              DEFINITE_VERDICT_LABEL,
                                              VERDICT_NOT_EVALUABLE,
                                              is_definite_verdict,
                                              not_evaluated_kind,
                                              with_trial_status)
from oncotriage.dashboard.tiers import (TRIAL_STATUS_NOT_EVALUABLE_UNCERTAIN,
                                        TRIAL_STATUS_NOT_EVALUATED_FAILED,
                                        TRIAL_STATUS_NOT_EVALUATED_UNKNOWN,
                                        TRIAL_STATUS_ORDER, TRIAL_STATUS_PARTIAL,
                                        TRIAL_STATUS_REJECTED, TRIAL_STATUS_UNCONFIRMED,
                                        display_trial_title)


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
    
    # GROUPED BY TRIAL ID ALONE (the repair pass). The title is a DISPLAY
    # attribute of a trial, not part of its identity: an NCT id is the
    # identity, and grouping on the pair made a recorded title and a missing
    # one two different trials -- of which pandas then kept one and dropped the
    # other. See tiers.TRIAL_MISSING_TITLE_LABEL for what that cost, and
    # tiers.display_trial_title for how the surviving entry is named.
    # THE SELECTOR COUNTS PATIENTS WITH A DEFINITE ELIGIBILITY VERDICT AND
    # NAMES THE REST (the dashboard-truthfulness pass). It printed "(1
    # patients)" -- distinct patients with ANY row -- beside a tile reading
    # "Total Patients Evaluated 0", and on the smoke run that disagreement held
    # for 34 of 61 trials: every one of them had only failed calls. A patient
    # counts for a trial when at least one of their rows for it is eligible or
    # not_eligible; the rest -- failed calls AND declared clinical
    # uncertainties, neither of which is an eligibility decision -- are named
    # as "without", never dropped.
    filtered_matches['_definite'] = [is_definite_verdict(v)
                                     for v in filtered_matches['eligible']]
    _definite_by_trial = (filtered_matches[filtered_matches['_definite']]
                          .groupby('nct_id')['patient_id'].nunique())
    trial_summary = filtered_matches.groupby('nct_id').agg(
        trial_title=('trial_title', display_trial_title),
        total_patients=('patient_id', 'nunique'),
    ).reset_index()
    trial_summary['definite_patients'] = (
        trial_summary['nct_id'].map(_definite_by_trial).fillna(0).astype(int))
    trial_summary['without_definite_patients'] = (
        trial_summary['total_patients'] - trial_summary['definite_patients'])
    trial_summary = trial_summary.sort_values(
        ['definite_patients', 'total_patients', 'nct_id'],
        ascending=[False, False, True])

    trial_options = trial_summary.apply(
        lambda r: (f"{r['nct_id']} — {r['trial_title'][:55]}  "
                   f"({r['definite_patients']} with a {DEFINITE_VERDICT_LABEL}"
                   + (f" · {r['without_definite_patients']} without"
                      if r['without_definite_patients'] else "")
                   + ")"),
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
    definite_patients = eligible_patients + not_eligible_patients

    # NOT EVALUATED IS COUNTED, NOT OMITTED. The de-duplication ranks every
    # definite verdict above a not-evaluable row, so a patient lands here only
    # when NO attempt produced a definite eligibility verdict for this trial. A model-declared clinical uncertainty is
    # counted apart from a failure, because the two are different findings.
    _without_definite = trial_dedup[~_elig_mask & (trial_dedup['eligible'] != 'not_eligible')]
    _reasons = (_without_definite['not_evaluable_reason']
                if 'not_evaluable_reason' in _without_definite.columns
                else pd.Series([None] * len(_without_definite), index=_without_definite.index))
    uncertain_patients = sum(
        1 for v, r in zip(_without_definite['eligible'], _reasons)
        if str(v) == VERDICT_NOT_EVALUABLE
        and not_evaluated_kind(r) == NOT_EVALUATED_CLINICAL_UNCERTAINTY)
    not_evaluated_patients = len(_without_definite) - uncertain_patients

    # `selected_trial['trial_title']` is the aggregated DISPLAY title, so it is
    # MISSING_TITLE_LABEL rather than a NaN for a trial that recorded none.
    st.subheader(f"{selected_trial['trial_title']}")
    st.caption(f"NCT ID: {selected_nct}  |  Phase: {trial_data['trial_phase'].iloc[0]}")

    col1, col2, col3, col4, col5, col6 = st.columns(6)

    with col1:
        st.metric(
            "Patients With a Definite Eligibility Verdict",
            int(definite_patients),
            help=f"Distinct patients with a {DEFINITE_VERDICT_LABEL} (eligible "
                 f"or not eligible) on this trial. A patient whose every call "
                 f"for this trial failed, or whom the model declared clinically "
                 f"uncertain, is NOT here — see Not Evaluated."
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

    with col6:
        st.metric(
            "Not Evaluated",
            int(not_evaluated_patients),
            delta=(f"+{uncertain_patients} clinically uncertain"
                   if uncertain_patients else None),
            delta_color="off",
            help="Distinct patients with no definite eligibility verdict on "
                 "this trial: no attempt "
                 "produced a usable verdict (for example, the call failed) or "
                 "the stored reason cannot be classified. A model-declared "
                 "clinical uncertainty is shown separately in the delta. "
                 "Neither is a rejection."
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
    
    # THE ONE PER-TRIAL CLASSIFIER (the dashboard-truthfulness pass). This
    # mapped every non-eligible row to '❌ Not Eligible' -- measured on the
    # smoke run, NCT03026140's caption read "2 not eligible · 2 total" for two
    # failed calls from one patient.
    patient_details = with_trial_status(patient_details)
    # `.astype(int)` RAISED on a NULL score: pandas refuses "Cannot convert
    # non-finite values (NA or inf) to integer". The nullable 'Int64' dtype
    # carries <NA> to the renderer as an empty cell, which is the honest
    # rendering; `.fillna(0)` would print 0% for a score nobody recorded.
    patient_details['Match Score'] = (
        pd.to_numeric(patient_details['display_score'], errors='coerce') * 100
    ).round(0).astype('Int64')

    status_filter = st.selectbox(
        "Filter by Status",
        ["All"] + list(TRIAL_STATUS_ORDER),
        key="trial_explorer_status_filter"
    )
    
    if status_filter != "All":
        patient_details = patient_details[patient_details['Status'] == status_filter]
    
    if patient_details.empty:
        st.info("No patients match the selected status filter.")
        return
    
    status_order = {s: i for i, s in enumerate(TRIAL_STATUS_ORDER)}
    patient_details['_sort'] = (patient_details['Status'].map(status_order)
                                .fillna(len(TRIAL_STATUS_ORDER)))
    patient_details = patient_details.sort_values(
        by=['_sort', 'Match Score'],
        ascending=[True, False]
    )
    
    display_cols = ['Status', 'patient_id', 'age', 'sex', 'primary_condition', 'Match Score', 'reason', 'assessment']
    display_df = patient_details[display_cols].copy()
    
    display_df.columns = ['Status', 'Patient ID', 'Age', 'Sex', 'Primary Condition', 'Match Score', 'Not Evaluated Reason', 'Explanation']
    
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
        f"{(patient_details['Status'] == TRIAL_STATUS_NOT_EVALUATED_FAILED).sum()} not evaluated (no usable verdict) · "
        f"{(patient_details['Status'] == TRIAL_STATUS_NOT_EVALUABLE_UNCERTAIN).sum()} not evaluable (clinical uncertainty) · "
        f"{(patient_details['Status'] == TRIAL_STATUS_NOT_EVALUATED_UNKNOWN).sum()} not evaluated (reason unknown) · "
        # ROWS, AND SAID SO (the dashboard-truthfulness pass): the table has one
        # row per inference x trial, so a re-run patient appears once per run.
        f"{len(patient_details)} row(s) from {patient_details['patient_id'].nunique()} "
        f"distinct patient(s) — one row per inference, so a re-run patient appears once per run"
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
