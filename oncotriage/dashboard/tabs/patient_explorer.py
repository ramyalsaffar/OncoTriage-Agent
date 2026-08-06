"""
Patient Explorer tab. Moved verbatim out of "21- Streamlit Dashboard.py"
(pass 20c-3c-1).
"""

import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from oncotriage.dashboard.data import load_trial_matches_data
from oncotriage.dashboard.tiers import TRIAL_STATUS_PARTIAL, TRIAL_STATUS_REJECTED, TRIAL_STATUS_UNCONFIRMED, classify_trial_score


@st.fragment
def render_patient_explorer_tab(df):
    """Render Patient Explorer tab for individual patient drill-down."""
    
    st.header("🔎 Patient Explorer")
    
    patient_ids = sorted(df['patient_id'].unique().tolist())
    selected_patient = st.selectbox("Select Patient ID", patient_ids, key="patient_explorer_select")
    
    if not selected_patient:
        st.info("Select a patient above to view their details.")
        return
    
    # Sometimes the one patient has more than one inference! we should show all!
    patient_rows = df[df['patient_id'] == selected_patient].sort_values('timestamp', ascending=False)
    
    if len(patient_rows) > 1:
        inference_options = [
            f"#{i+1} — {row['timestamp'].strftime('%Y-%m-%d %H:%M')}"
            for i, (_, row) in enumerate(patient_rows.iterrows())
        ]
        selected_inf_idx = st.selectbox(
            "Select Inference Run",
            range(len(inference_options)),
            format_func=lambda i: inference_options[i],
            key="patient_inference_select"
        )
        patient_df = patient_rows.iloc[selected_inf_idx]
    else:
        patient_df = patient_rows.iloc[0]
    
    # --- Patient Profile ---
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.markdown("**Demographics**")
        st.metric("Age", patient_df['age'], help="Patient age at time of inference")
        st.metric("Sex", patient_df['sex'], help="Biological sex from FHIR record")
        st.metric("Race", patient_df['race'], help="Race from FHIR demographics")
        st.metric("Ethnicity", patient_df['ethnicity'], help="Ethnicity from FHIR demographics")
    
    with col2:
        st.markdown("**Clinical Profile**")
        condition_text = str(patient_df['primary_condition']) if pd.notna(patient_df['primary_condition']) else "Unknown"
        st.markdown(
            f"""<div style="font-size:14px; color:#555; margin-bottom:2px;">Primary Condition</div>
            <div style="font-size:16px; font-weight:600; word-wrap:break-word; white-space:normal; margin-bottom:16px;">{condition_text}</div>""",
            unsafe_allow_html=True,
        )
        st.metric("Conditions", int(patient_df['condition_count']), help="Total active conditions in FHIR record")
        st.metric("Medications", int(patient_df['medication_count']), help="Total active medications in FHIR record")
    
    with col3:
        st.markdown("**Match Results**")
        st.metric("✅ Full Matches", int(patient_df['full_match_count']), help="Trials where ALL criteria were confirmed met (100% score)")
        st.metric("🟡 Partial Matches", int(patient_df['partial_match_count']), help="Trials eligible with SOME criteria confirmed but not all (0% < score < 100%)")
        st.metric("🔶 Unconfirmed", int(patient_df['unconfirmed_match_count']), help="Trials eligible but scoring 0% — no disqualifier found and no criterion confirmed either")
        st.metric("Match Tier", patient_df['match_tier'], help="Overall patient classification: Full Match > Partial Match > Unconfirmed Match > No Match")
    
    st.markdown("---")
    
    # --- Export Patient Report ---
    trial_matches_for_export = load_trial_matches_data()
    patient_inference_id_export = patient_df['id']
    patient_trials_export = pd.DataFrame()
    
    if trial_matches_for_export is not None and not trial_matches_for_export.empty:
        patient_trials_export = trial_matches_for_export[
            trial_matches_for_export['inference_id'] == patient_inference_id_export
        ].copy()
    
    # Build export CSV: patient header rows + trial rows
    export_rows = []
    
    # Patient summary row
    export_rows.append({
        'Section':          'Patient Summary',
        'Patient ID':       selected_patient,
        'Age':              patient_df['age'],
        'Sex':              patient_df['sex'],
        'Race':             patient_df['race'],
        'Ethnicity':        patient_df['ethnicity'],
        'Primary Condition': patient_df['primary_condition'],
        'Conditions':       int(patient_df['condition_count']),
        'Medications':      int(patient_df['medication_count']),
        'Full Matches':     int(patient_df['full_match_count']),
        'Partial Matches':  int(patient_df['partial_match_count']),
        'Unconfirmed Matches': int(patient_df['unconfirmed_match_count']),
        'Match Tier':       patient_df['match_tier'],
        'Total Time (s)':   round(patient_df['total_time'], 2),
        'Cost (USD)':       round(patient_df['estimated_cost_usd'], 4),
        'Timestamp':        patient_df['timestamp'],
    })
    
    # Trial match rows
    if not patient_trials_export.empty:
        for _, t in patient_trials_export.iterrows():
            if t['eligible'] == 'eligible':
                status = classify_trial_score(t['match_score'])
            else:
                status = 'Not Eligible'
            export_rows.append({
                'Section':     'Trial Match',
                'NCT ID':      t.get('nct_id', ''),
                'Trial Title': t.get('trial_title', ''),
                'Phase':       t.get('trial_phase', ''),
                'Status':      status,
                'Match Score':  f"{t['match_score'] * 100:.0f}%",
                'Explanation': t.get('explanation', ''),
            })
    
    export_df = pd.DataFrame(export_rows)
    csv_data = export_df.to_csv(index=False)
    
    st.download_button(
        label="📥 Export Patient Report (CSV)",
        data=csv_data,
        file_name=f"oncomatch_patient_{selected_patient}_{patient_df['timestamp'].strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
        help="Download patient demographics, trial matches, and pipeline metrics as CSV"
    )
    
    st.markdown("---")
    
    # --- Patient Pipeline Funnel ---
    st.subheader("Pipeline Funnel")

    # Patient's values
    patient_stages = {
        'Retrieved':           int(patient_df['candidates_retrieved']),
        'Reranked':            int(patient_df['candidates_reranked']),
        'Rule Filter':         int(patient_df['candidates_after_rule_filter']) if 'candidates_after_rule_filter' in patient_df.index else int(patient_df['candidates_filtered']),
        'Quality Filter':      int(patient_df['candidates_after_quality_filter']),
        'Cost Cap':            int(patient_df['candidates_filtered']),
        'Evaluated':           int(patient_df['candidates_evaluated']),
        'Eligible (Any)':      int(patient_df['eligible_matches']),
        '  Full Match':        int(patient_df['full_match_count']),
        '  Partial':           int(patient_df['partial_match_count']),
        '  Unconfirmed':       int(patient_df['unconfirmed_match_count'])
    }
    
    # Population averages
    avg_stages = {
        'Retrieved':           df['candidates_retrieved'].mean(),
        'Reranked':            df['candidates_reranked'].mean(),
        'Rule Filter':         df['candidates_after_rule_filter'].mean() if 'candidates_after_rule_filter' in df.columns else df['candidates_filtered'].mean(),
        'Quality Filter':      df['candidates_after_quality_filter'].mean(),
        'Cost Cap':            df['candidates_filtered'].mean(),
        'Evaluated':           df['candidates_evaluated'].mean(),
        'Eligible (Any)':      df['eligible_matches'].mean(),
        '  Full Match':        df['full_match_count'].mean(),
        '  Partial':           df['partial_match_count'].mean(),
        '  Unconfirmed':       df['unconfirmed_match_count'].mean()
    }
        
    stage_names = list(patient_stages.keys())
    patient_vals = list(patient_stages.values())
    avg_vals = [round(v, 1) for v in avg_stages.values()]
    
    fig_funnel = go.Figure()
    
    max_val = max(max(patient_vals), max(avg_vals), 1)
    stage_colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', '#8c564b', '#e377c2', '#2ca02c', '#ffbb33']
    
    # Population average bars (gray, behind)
    fig_funnel.add_trace(go.Bar(
        y=stage_names,
        x=avg_vals,
        orientation='h',
        name='Population Avg',
        marker_color='rgba(180, 180, 180, 0.4)',
        marker_line=dict(color='rgba(130, 130, 130, 0.8)', width=1.5),
        hovertemplate='Avg: %{x}<extra></extra>',
        textposition='none',
    ))
    
    # Patient bars (colored, front) — labels always inside
    fig_funnel.add_trace(go.Bar(
        y=stage_names,
        x=patient_vals,
        orientation='h',
        name='This Patient',
        marker_color=stage_colors,
        text=[str(v) for v in patient_vals],
        textposition='inside',
        insidetextanchor='middle',
        textfont=dict(size=14, color='white', family='Arial Black'),
        constraintext='none',
        textangle=0,
        hovertemplate='Patient: %{x}<extra></extra>',
        showlegend=False,
    ))
    
    # Add avg annotations pinned to the far right — no overlap possible
    x_annotation = max_val * 1.15
    for i, (stage, avg) in enumerate(zip(stage_names, avg_vals)):
        fig_funnel.add_annotation(
            x=x_annotation, y=stage,
            text=f"avg: {avg}",
            showarrow=False,
            font=dict(size=13, color='gray'),
            xanchor='left',
        )
    
    fig_funnel.update_layout(
        barmode='overlay',
        height=360,
        margin=dict(l=20, r=120, t=10, b=20),
        template='plotly_white',
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1, font=dict(size=14)),
        xaxis_title=dict(text="Candidates", font=dict(size=14)),
        yaxis=dict(autorange='reversed', tickfont=dict(size=13)),
    )
    
    fig_funnel.update_xaxes(range=[0, max_val * 1.4], showticklabels=True, tickfont=dict(size=13))
    
    st.plotly_chart(fig_funnel, use_container_width=True)
    
    st.caption(
        "Colored bars show this patient's candidates at each pipeline stage. "
        "Gray bars show the population average for comparison."
    )
    
    # Rule filter drop breakdown (columns may not exist in older runs)
    mesh_dropped = int(patient_df.get('mesh_dropped', 0)) if 'mesh_dropped' in patient_df.index else 0
    stage_dropped = int(patient_df.get('stage_dropped', 0)) if 'stage_dropped' in patient_df.index else 0
    histology_dropped = int(patient_df.get('histology_dropped', 0)) if 'histology_dropped' in patient_df.index else 0
    total_dropped = mesh_dropped + stage_dropped + histology_dropped
    
    if total_dropped > 0:
        st.caption(
            f"Rule filter drops: "
            f"MeSH cancer site: {mesh_dropped} · "
            f"Stage mismatch: {stage_dropped} · "
            f"Histology mismatch: {histology_dropped}"
        )
    
    st.markdown("---")
    
    # --- Trial Results Table ---
    st.subheader("Trial Results")
    
    trial_matches = load_trial_matches_data()
    
    if trial_matches is not None and not trial_matches.empty:
        patient_inference_id = patient_df['id']
        patient_matches = trial_matches[trial_matches['inference_id'] == patient_inference_id].copy()
        
        if not patient_matches.empty:
            # Build display table
            
            def classify_status(row):
                if row['eligible'] != 'eligible':
                    return TRIAL_STATUS_REJECTED
                tier = classify_trial_score(row['match_score'])
                if tier == 'Full Match':
                    return '✅ Eligible'
                if tier == 'Partial Match':
                    return TRIAL_STATUS_PARTIAL
                return TRIAL_STATUS_UNCONFIRMED

            patient_matches['Status'] = patient_matches.apply(classify_status, axis=1)
            
            # Extract diagnostic fields from criterion_details JSON
            def _extract_diag(cd_raw, field, default):
                try:
                    if pd.notna(cd_raw) and cd_raw:
                        return json.loads(cd_raw).get(field, default)
                except (json.JSONDecodeError, TypeError):
                    pass
                return default
            
            display_df = patient_matches[[
                'Status', 'nct_id', 'trial_title', 'trial_phase', 'match_score', 'explanation'
            ]].copy()
            
            display_df = display_df.rename(columns={
                'nct_id':      'NCT ID',
                'trial_title': 'Trial Title',
                'trial_phase': 'Phase',
                'match_score': 'Match Score',
                'explanation': 'Explanation'
            })
            
            # Convert match score to percentage
            display_df['Match Score'] = (display_df['Match Score'] * 100).round(0).astype(int)
            
            # Default sort: eligible first, then by match score descending
            status_order = {'✅ Eligible': 0, TRIAL_STATUS_PARTIAL: 1,
                            TRIAL_STATUS_UNCONFIRMED: 2, TRIAL_STATUS_REJECTED: 3}
            display_df['_sort'] = display_df['Status'].map(status_order)
            
            display_df = display_df.sort_values(
                by=['_sort', 'Match Score'],
                ascending=[True, False]
            ).drop(columns='_sort').reset_index(drop=True)
            display_df.index = display_df.index + 1
            display_df.index.name = "Row"
            
            st.dataframe(
                display_df,
                use_container_width=True,
                hide_index=False,
                height=400,
                column_config={
                    'Match Score':      st.column_config.NumberColumn(format='%d%%'),
                    'Trial Title':      st.column_config.TextColumn(width='large'),
                    'Explanation':      st.column_config.TextColumn(width='large'),
                }
            )
            
            eligible_count = (patient_matches['Status'] == '✅ Eligible').sum()
            partial_count = (patient_matches['Status'] == TRIAL_STATUS_PARTIAL).sum()
            unconfirmed_count = (patient_matches['Status'] == TRIAL_STATUS_UNCONFIRMED).sum()
            not_eligible_count = (patient_matches['Status'] == TRIAL_STATUS_REJECTED).sum()

            st.caption(
                f"{eligible_count} eligible · "
                f"{partial_count} partial matches · "
                f"{unconfirmed_count} unconfirmed (eligible, 0% of criteria confirmed) · "
                f"{not_eligible_count} not eligible · "
                f"{len(patient_matches)} total"
            )
            
            # --- Criterion-Level Breakdown ---
            st.markdown("---")
            st.subheader("Criterion-Level Breakdown")
            
            # Trial selector — always shown, uses all trials for this patient
            trial_options = patient_matches.apply(
                lambda r: f"{r['nct_id']} — {r['trial_title']}", axis=1
            ).tolist()
            
            selected_trial_idx = st.selectbox(
                "Select a trial to view criteria evaluation",
                range(len(trial_options)),
                format_func=lambda i: trial_options[i],
                key="criterion_trial_select"
            )
            
            selected_row = patient_matches.iloc[selected_trial_idx]
            
            # Parse criterion_details if available
            has_criteria = False
            criteria = {"inclusion": [], "exclusion": []}
            
            if 'criterion_details' in patient_matches.columns and pd.notna(selected_row.get('criterion_details')):
                try:
                    criteria = json.loads(selected_row['criterion_details'])
                    has_criteria = True
                except (json.JSONDecodeError, TypeError):
                    has_criteria = False
            
            inclusion = criteria.get("inclusion", [])
            exclusion = criteria.get("exclusion", [])
            
            if has_criteria and (inclusion or exclusion):
                
                _STATUS_DISPLAY_BASE = {
                    "met":            "✅ Met",
                    "not_met":        "❌ Not Met",
                    "violated":       "❌ Violated",
                    "not_violated":   "✅ Not Violated",
                    "not_applicable": "➖ N/A",
                }

                def _format_status(status: str, patient_value: str) -> str:
                    pv = (patient_value or "").strip()
                    if pv.lower().startswith("not applicable"):
                        return "➖ Not Applicable"
                    if status == "not_evaluable":
                        if pv and pv.lower() != "not in patient record":
                            return "🔍 Unverifiable"
                        return "⚠️ Missing Data"
                    return _STATUS_DISPLAY_BASE.get(status, status)

                rows = []
                for c in inclusion:
                    rows.append({
                        "Type": "Inclusion",
                        "Criterion": c.get("criterion", ""),
                        "Patient Value": c.get("patient_value", ""),
                        "Result": _format_status(c.get("status", ""), c.get("patient_value", ""))
                    })
                for c in exclusion:
                    rows.append({
                        "Type": "Exclusion",
                        "Criterion": c.get("criterion", ""),
                        "Patient Value": c.get("patient_value", ""),
                        "Result": _format_status(c.get("status", ""), c.get("patient_value", ""))
                    })
                
                criteria_df = pd.DataFrame(rows)
                
                # Summary counts
                col1, col2, col3 = st.columns(3)
                
                confirmed = sum(1 for r in rows if r['Result'] in ('✅ Met', '✅ Not Violated'))
                
                failed = sum(1 for r in rows if r['Result'] in ('❌ Not Met', '❌ Violated'))
                
                missing = sum(1 for r in rows if r['Result'] in ('⚠️ Missing Data', '🔍 Unverifiable'))

                with col1:
                    st.metric("Confirmed", confirmed, help="Criteria confirmed by patient data")
                with col2:
                    st.metric("Failed", failed, help="Criteria contradicted by patient data")
                with col3:
                    st.metric("Missing Data", missing, help="Criteria that could not be evaluated due to missing patient data")
                
                st.dataframe(
                    criteria_df,
                    use_container_width=True,
                    hide_index=True,
                    height=min(400, 35 * (len(criteria_df) + 1)),
                    column_config={
                        'Criterion': st.column_config.Column(width='large'),
                        'Patient Value': st.column_config.Column(width='medium'),
                    }
                )
                
                st.caption(
                    "Each row is one inclusion or exclusion criterion from the trial's eligibility criteria. "
                    "'Missing Data' means no data in the patient record addresses this criterion. "
                    "'Unverifiable' means relevant data exists but is insufficient to confirm or deny the criterion."
                )
                
            else:
                st.info("Criterion-level details not available for this trial.")

        else:
            st.info("No match details available for this patient.")
            
    else:
        st.info("No trial match data available.")
    
    st.markdown("---")
    
    # --- Pipeline Performance for this patient ---
    st.subheader("Pipeline Performance")
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Total Time", f"{patient_df['total_time']:.2f}s", help="End-to-end pipeline latency for this patient")
    with col2:
        st.metric("Retrieved", int(patient_df['candidates_retrieved']), help="Trials retrieved from hybrid search")
    with col3:
        st.metric("Evaluated", int(patient_df['candidates_evaluated']), help="Trials sent to GPT-4o for eligibility evaluation")
    with col4:
        st.metric("Cost", f"${patient_df['estimated_cost_usd']:.4f}", help="Estimated API cost for this patient")
    
    # Labelled with the model THIS row was judged by, read from the row rather
    # than from MATCHING_MODEL. A patient evaluated by GPT-4o must not be
    # relabelled as the current judge just because the config moved on.
    _row_model = patient_df.get('matching_model')
    _judge = str(_row_model) if pd.notna(_row_model) else "Stage 5"

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(f"{_judge} Input Tokens", f"{int(patient_df['gpt4o_input_tokens']):,}",
                  help=f"Tokens sent to {_judge} (prompt + trial criteria + patient data)")
    with col2:
        st.metric(f"{_judge} Output Tokens", f"{int(patient_df['gpt4o_output_tokens']):,}",
                  help=f"Tokens generated by {_judge}, INCLUDING any reasoning "
                       f"tokens — the two are one billed total, not two.")
    with col3:
        # NULL is rendered as "n/a", never as 0: GPT-4o-era rows carry no
        # reasoning breakdown at all, which is a different fact from a
        # reasoning model that spent nothing thinking.
        _reasoning = patient_df.get('gpt4o_reasoning_tokens')
        st.metric(
            "…of which reasoning",
            "n/a" if pd.isna(_reasoning) else f"{int(_reasoning):,}",
            help="Reasoning tokens are a SUBSET of the output tokens above, "
                 "billed at the output rate and never shown to the reader. "
                 "'n/a' means this response reported no reasoning breakdown.",
        )

    if pd.notna(patient_df['error']) and patient_df['error'] != '':
        st.error(f"Error: {patient_df['error']}")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
