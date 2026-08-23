"""
Patient Explorer tab. Moved verbatim out of "21- Streamlit Dashboard.py"
(pass 20c-3c-1).

ONE SPARSE ROW USED TO TAKE THE WHOLE PAGE DOWN (the campaign pass)
-------------------------------------------------------------------
Every numeric cell this tab renders went through a bare ``int()``, ``round()``
or f-string format, and most of the columns it reads are legitimately NULL:

  * ``INFERENCE_COLUMN_ADDITIONS`` columns are absent on any row written before
    they existed, and arrive as NULL from a database that has since been
    migrated;
  * an error-handler row and a no-candidates row record almost none of the
    funnel;
  * ``full_match_count`` / ``partial_match_count`` / ``unconfirmed_match_count``
    are not columns at all -- ``enrich_match_tiers`` computes them in ``main()``
    and leaves them NaN for a patient with no ``trial_matches`` rows.

``int(nan)`` raises ``ValueError``, ``int(None)`` and ``round(None, 2)`` raise
``TypeError``, ``f"{None:.2f}"`` raises ``TypeError``, ``pd.NaT.strftime(...)``
raises ``ValueError``, and ``.astype(int)`` over a column with one NaN raises.
NONE of those is caught anywhere: ``oncotriage/dashboard/app.py`` calls this
inside ``main()`` with no handler, so the raise propagates out of the script run
and streamlit renders a traceback WHERE THE ENTIRE DASHBOARD SHOULD BE -- all
ten tabs, for every reader, because of one cell.

Every conversion in this file now goes through ``oncotriage/dashboard/nullsafe``
and renders the absence instead. WHICH helper is the judgement, and it is made
per column rather than once:

  ``optional_int_text``  where NULL means "never measured" -- a count that was
                         not recorded must not print as 0, which is the
                         MEASURED answer.
  ``as_int``             where the value feeds a chart, which cannot draw an
                         unknown. The funnel does this and then NAMES the
                         stages it drew at zero underneath, so a bar at zero is
                         never silently a bar for a missing number.
  ``None``               in the CSV export, which is what an empty cell in a
                         numeric column means to every consumer of a CSV. A
                         dash there would make the column text.

A trial whose ``match_score`` is NULL gets ``TRIAL_STATUS_NO_SCORE`` rather than
being handed to ``classify_trial_score``, which raises on ``None`` and answers
'Unconfirmed Match' -- a real verdict -- on ``nan``. The constant is in
``tiers.py`` with the other three, because two other tabs need it for the same
reason.
"""

import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from oncotriage.dashboard.data import load_trial_matches_data
from oncotriage.dashboard.nullsafe import (
    ABSENT_TEXT,
    as_float,
    as_int,
    as_text,
    format_number,
    format_timestamp,
    is_absent,
    optional_int_text,
)
from oncotriage.dashboard.tiers import (TRIAL_STATUS_NO_SCORE, TRIAL_STATUS_PARTIAL,
                                        TRIAL_STATUS_REJECTED, TRIAL_STATUS_UNCONFIRMED,
                                        classify_trial_score)




def _seconds_text(value):
    """`value` as "12.34s", or the absent marker. Never "nans" and never "—s"."""
    if is_absent(value):
        return ABSENT_TEXT
    return format_number(value, ".2f") + "s"


def _dollars_text(value):
    """`value` as "$0.1234", or the absent marker."""
    if is_absent(value):
        return ABSENT_TEXT
    return "$" + format_number(value, ".4f")


def _csv_int(value):
    """An int for the CSV export, or ``None`` -- which pandas writes as blank.

    NOT the em dash the screen uses. A CSV column holding "—" for its missing
    rows is a TEXT column to every tool that opens it, so summing it silently
    fails or silently coerces; an empty cell is what every consumer of a CSV
    already reads as missing.
    """
    return as_int(value, default=None)


def _csv_round(value, digits):
    """A rounded float for the CSV export, or ``None``. Same reasoning."""
    if is_absent(value):
        return None
    return round(as_float(value), digits)


def _column_mean(frame, column):
    """The population mean of `column`, or 0.0 when it is absent or all-NULL.

    ``frame[column]`` RAISES KeyError when the column is not there at all, which
    is the shape a database predating an additive column has, and ``.mean()``
    over an all-NULL column returns ``nan`` -- which then propagates through
    ``max()`` into a plotly axis range of ``[0, nan]`` and renders an empty
    chart with no error anywhere. Both come back as 0.0, and the caller says
    which stages that happened to.
    """
    if column not in frame.columns:
        return 0.0
    return as_float(frame[column].mean(), 0.0)


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
            f"#{i+1} — {format_timestamp(row['timestamp'])}"
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
        st.metric("Age", format_number(patient_df.get('age')), help="Patient age at time of inference")
        st.metric("Sex", as_text(patient_df.get('sex'), ABSENT_TEXT), help="Biological sex from FHIR record")
        st.metric("Race", as_text(patient_df.get('race'), ABSENT_TEXT), help="Race from FHIR demographics")
        st.metric("Ethnicity", as_text(patient_df.get('ethnicity'), ABSENT_TEXT), help="Ethnicity from FHIR demographics")
    
    with col2:
        st.markdown("**Clinical Profile**")
        condition_text = as_text(patient_df.get('primary_condition'), "Unknown")
        st.markdown(
            f"""<div style="font-size:14px; color:#555; margin-bottom:2px;">Primary Condition</div>
            <div style="font-size:16px; font-weight:600; word-wrap:break-word; white-space:normal; margin-bottom:16px;">{condition_text}</div>""",
            unsafe_allow_html=True,
        )
        st.metric("Conditions", optional_int_text(patient_df.get('condition_count')), help="Total active conditions in FHIR record")
        st.metric("Medications", optional_int_text(patient_df.get('medication_count')), help="Total active medications in FHIR record")
    
    with col3:
        st.markdown("**Match Results**")
        st.metric("✅ Full Matches", optional_int_text(patient_df.get('full_match_count')), help="Trials where ALL criteria were confirmed met (100% score)")
        st.metric("🟡 Partial Matches", optional_int_text(patient_df.get('partial_match_count')), help="Trials eligible with SOME criteria confirmed but not all (0% < score < 100%)")
        st.metric("🔶 Unconfirmed", optional_int_text(patient_df.get('unconfirmed_match_count')), help="Trials eligible but scoring 0% — no disqualifier found and no criterion confirmed either")
        st.metric("Match Tier", as_text(patient_df.get('match_tier'), ABSENT_TEXT), help="Overall patient classification: Full Match > Partial Match > Unconfirmed Match > No Match")
    
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
        'Age':              patient_df.get('age'),
        'Sex':              patient_df.get('sex'),
        'Race':             patient_df.get('race'),
        'Ethnicity':        patient_df.get('ethnicity'),
        'Primary Condition': patient_df.get('primary_condition'),
        'Conditions':       _csv_int(patient_df.get('condition_count')),
        'Medications':      _csv_int(patient_df.get('medication_count')),
        'Full Matches':     _csv_int(patient_df.get('full_match_count')),
        'Partial Matches':  _csv_int(patient_df.get('partial_match_count')),
        'Unconfirmed Matches': _csv_int(patient_df.get('unconfirmed_match_count')),
        'Match Tier':       patient_df.get('match_tier'),
        'Total Time (s)':   _csv_round(patient_df.get('total_time'), 2),
        'Cost (USD)':       _csv_round(patient_df.get('estimated_cost_usd'), 4),
        'Timestamp':        patient_df.get('timestamp'),
    })
    
    # Trial match rows
    if not patient_trials_export.empty:
        for _, t in patient_trials_export.iterrows():
            if is_absent(t.get('match_score')):
                # SAME RULE AS THE TABLE BELOW: a trial with no recorded score
                # is not an unconfirmed match, and calling classify_trial_score
                # on it either raises (None) or answers 'Unconfirmed Match'
                # (NaN) about a measurement nobody made.
                status = TRIAL_STATUS_NO_SCORE
            elif t['eligible'] == 'eligible':
                status = classify_trial_score(t['match_score'])
            else:
                status = 'Not Eligible'
            export_rows.append({
                'Section':     'Trial Match',
                'NCT ID':      t.get('nct_id', ''),
                'Trial Title': t.get('trial_title', ''),
                'Phase':       t.get('trial_phase', ''),
                'Status':      status,
                'Match Score':  (ABSENT_TEXT if is_absent(t.get('match_score'))
                                 else format_number(as_float(t['match_score']) * 100, ".0f") + "%"),
                'Assessment': t.get('assessment', ''),
            })
    
    export_df = pd.DataFrame(export_rows)
    csv_data = export_df.to_csv(index=False)
    
    st.download_button(
        label="📥 Export Patient Report (CSV)",
        data=csv_data,
        file_name=f"oncomatch_patient_{selected_patient}_"
                  f"{format_timestamp(patient_df.get('timestamp'), '%Y%m%d_%H%M', 'no-timestamp')}.csv",
        mime="text/csv",
        help="Download patient demographics, trial matches, and pipeline metrics as CSV"
    )
    
    st.markdown("---")
    
    # --- Patient Pipeline Funnel ---
    st.subheader("Pipeline Funnel")

    # THE STAGE -> COLUMN MAP, WRITTEN ONCE. It was two dict literals, the
    # patient's and the population's, listing the same ten stages in the same
    # order with the same fallback spelled out twice -- so a column renamed in
    # one and not the other would have plotted a patient against a different
    # stage's average with nothing saying so.
    #
    # 'Rule Filter' carries a FALLBACK column and the others do not:
    # `candidates_after_rule_filter` is additive and a row written before it
    # existed records only `candidates_filtered`, which is the post-cost-cap
    # count -- a worse answer, and the one this tab has always used there.
    _FUNNEL = (
        ('Retrieved',      ('candidates_retrieved',)),
        ('Reranked',       ('candidates_reranked',)),
        ('Rule Filter',    ('candidates_after_rule_filter', 'candidates_filtered')),
        ('Quality Filter', ('candidates_after_quality_filter',)),
        ('Cost Cap',       ('candidates_filtered',)),
        ('Evaluated',      ('candidates_evaluated',)),
        ('Eligible (Any)', ('eligible_matches',)),
        ('  Full Match',   ('full_match_count',)),
        ('  Partial',      ('partial_match_count',)),
        ('  Unconfirmed',  ('unconfirmed_match_count',)),
    )

    def _patient_stage(columns):
        """(value, was it recorded) for one funnel stage of this patient."""
        for column in columns:
            if column in patient_df.index and not is_absent(patient_df[column]):
                return as_int(patient_df[column]), True
        return 0, False

    patient_stages = {}
    unrecorded_stages = []
    for _stage, _columns in _FUNNEL:
        _value, _recorded = _patient_stage(_columns)
        patient_stages[_stage] = _value
        if not _recorded:
            unrecorded_stages.append(_stage.strip())

    # Population averages, over whichever of the two columns the frame carries.
    avg_stages = {}
    for _stage, _columns in _FUNNEL:
        _present = [c for c in _columns if c in df.columns]
        avg_stages[_stage] = _column_mean(df, _present[0]) if _present else 0.0

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

    # A BAR CANNOT DRAW "UNKNOWN", SO THE CAPTION SAYS WHICH BARS ARE NOT
    # MEASUREMENTS. Every stage above is plotted as an integer because a chart
    # has no third state, and a stage this row never recorded is therefore
    # indistinguishable at zero from a stage that genuinely passed nothing on --
    # which is the confusion the run tables were given a meta row to remove, one
    # layer up. Naming them is what keeps the chart honest; dropping the stage
    # instead would silently shorten the funnel.
    if unrecorded_stages:
        st.caption(
            f"⚠️ Not recorded for this patient and therefore **drawn at zero, "
            f"not measured as zero**: {', '.join(unrecorded_stages)}. A row "
            f"written before one of these columns existed, or an inference "
            f"that ended at the error handler, carries no value for them."
        )
    
    # Rule filter drop breakdown (columns may not exist in older runs)
    mesh_dropped = as_int(patient_df.get('mesh_dropped'))
    stage_dropped = as_int(patient_df.get('stage_dropped'))
    histology_dropped = as_int(patient_df.get('histology_dropped'))
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
                if is_absent(row.get('match_score')):
                    return TRIAL_STATUS_NO_SCORE
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
                'Status', 'nct_id', 'trial_title', 'trial_phase', 'match_score', 'assessment'
            ]].copy()
            
            display_df = display_df.rename(columns={
                'nct_id':      'NCT ID',
                'trial_title': 'Trial Title',
                'trial_phase': 'Phase',
                'match_score': 'Match Score',
                'assessment': 'Assessment'
            })
            
            # Convert match score to percentage.
            #
            # `.astype(int)` RAISED HERE. pandas refuses "Cannot convert
            # non-finite values (NA or inf) to integer", so one trial row with
            # a NULL match_score took the whole page down -- and
            # `trial_matches.match_score` is a nullable REAL that a Stage 5
            # failure return leaves unset. `to_numeric(errors='coerce')` turns
            # a non-numeric cell into NaN rather than raising, and the nullable
            # 'Int64' dtype carries <NA> through to the renderer, which draws it
            # as an empty cell. NOT `.fillna(0)`: a blank score rendered as 0%
            # is a measurement nobody made.
            display_df['Match Score'] = (
                pd.to_numeric(display_df['Match Score'], errors='coerce') * 100
            ).round(0).astype('Int64')
            
            # Default sort: eligible first, then by match score descending
            status_order = {'✅ Eligible': 0, TRIAL_STATUS_PARTIAL: 1,
                            TRIAL_STATUS_UNCONFIRMED: 2, TRIAL_STATUS_REJECTED: 3,
                            # LAST, and it has to be IN this map: `_sort` is
                            # `.map(status_order)`, which yields NaN for an
                            # unlisted status, and `sort_values` puts NaN last
                            # only by accident of its default -- while the
                            # rendered order would then depend on a default
                            # nothing here states.
                            TRIAL_STATUS_NO_SCORE: 4}
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
            no_score_count = (patient_matches['Status'] == TRIAL_STATUS_NO_SCORE).sum()

            st.caption(
                f"{eligible_count} eligible · "
                f"{partial_count} partial matches · "
                f"{unconfirmed_count} unconfirmed (eligible, 0% of criteria confirmed) · "
                f"{not_eligible_count} not eligible · "
                # PRINTED EVEN AT ZERO would be noise on the ordinary row, so
                # this term appears only when there IS such a trial -- and the
                # counts above already sum to the total when it is absent, so a
                # reader can tell the difference without being told.
                + (f"{no_score_count} with no recorded score · " if no_score_count else "")
                + f"{len(patient_matches)} total"
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
        st.metric("Total Time", _seconds_text(patient_df.get('total_time')), help="End-to-end pipeline latency for this patient")
    with col2:
        st.metric("Retrieved", optional_int_text(patient_df.get('candidates_retrieved')), help="Trials retrieved from hybrid search")
    with col3:
        st.metric("Evaluated", optional_int_text(patient_df.get('candidates_evaluated')), help="Trials sent to GPT-4o for eligibility evaluation")
    with col4:
        st.metric("Cost", _dollars_text(patient_df.get('estimated_cost_usd')), help="Estimated API cost for this patient")
    
    # Labelled with the model THIS row was judged by, read from the row rather
    # than from MATCHING_MODEL. A patient evaluated by GPT-4o must not be
    # relabelled as the current judge just because the config moved on.
    _row_model = patient_df.get('matching_model')
    _judge = str(_row_model) if pd.notna(_row_model) else "Stage 5"

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(f"{_judge} Input Tokens", format_number(patient_df.get('llm_classifier_input_tokens'), ",.0f"),
                  help=f"Tokens sent to {_judge} (prompt + trial criteria + patient data)")
    with col2:
        st.metric(f"{_judge} Output Tokens", format_number(patient_df.get('llm_classifier_output_tokens'), ",.0f"),
                  help=f"Tokens generated by {_judge}, INCLUDING any reasoning "
                       f"tokens — the two are one billed total, not two.")
    with col3:
        # NULL is rendered as "n/a", never as 0: GPT-4o-era rows carry no
        # reasoning breakdown at all, which is a different fact from a
        # reasoning model that spent nothing thinking.
        _reasoning = patient_df.get('llm_classifier_reasoning_tokens')
        st.metric(
            "…of which reasoning",
            # "n/a" AND NOT THE EM DASH THE REST OF THIS FILE USES, kept as
            # shipped: this cell has always said n/a and its help text below
            # explains that exact string. The reading is the same one
            # `optional_int_text` makes everywhere else -- NULL is not 0.
            format_number(_reasoning, ",.0f", default="n/a"),
            help="Reasoning tokens are a SUBSET of the output tokens above, "
                 "billed at the output rate and never shown to the reader. "
                 "'n/a' means this response reported no reasoning breakdown.",
        )

    _error = patient_df.get('error')
    if not is_absent(_error) and str(_error) != '':
        st.error(f"Error: {_error}")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
