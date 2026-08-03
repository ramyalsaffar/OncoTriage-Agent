# Streamlit Monitoring Dashboard
################################

"""
Monitoring Dashboard

Real-time monitoring and analytics for the clinical trial matching pipeline.
Visualizes performance metrics, costs, and match quality from SQLite logs.

Run from terminal:
    cd ".../03- Code"
    cd "/Users/ramyalsaffar/Ramy/C.V..V/07- LLM Projects/03- Clinical Trial Patient Match/03- Code/"
    streamlit run "21- Streamlit Dashboard.py"
"""


# ===========================================================================
# EXEC CHAIN: Load dependencies from existing scripts
# ===========================================================================
_code_dir = "/Users/ramyalsaffar/Ramy/C.V..V/07- LLM Projects/03- Clinical Trial Patient Match/03- Code/"

for _bootstrap in ("01- Imports.py", "02- Utility Functions.py"):
    with open(_code_dir + _bootstrap) as _fh:
        exec(_fh.read(), globals())

exec_chain(
    ["03- Config.py"],
    caller_file=_code_dir + "21- Streamlit Dashboard.py",
    caller_globals=globals(),
    chain_label="01 → 02 → 03",
)


# ===========================================================================
# DATABASE CONNECTION
# ===========================================================================

@st.cache_data(ttl=60)
def load_inferences_data():
    """
    Load all inference data from SQLite. Cached for 60 seconds.
    
    Returns empty DataFrame on error to allow Streamlit to handle gracefully.
    """
    conn = None
    try:
        conn = sqlite3.connect(inferences_path)
        df = pd.read_sql_query("SELECT * FROM inferences", conn)
        
        if df.empty:
            return pd.DataFrame()  # Return empty DataFrame, not None
            
        # Convert timestamp to datetime
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        return df
        
    except Exception as e:
        st.error(f"Database error: {e}")
        return pd.DataFrame()  # Return empty DataFrame, not None
    
    finally:
        if conn:
            conn.close()


@st.cache_data(ttl=60)
def load_trial_matches_data():
    """Load trial matches from SQLite. Cached for 60 seconds."""
    conn = None
    try:
        conn = sqlite3.connect(inferences_path)
        df = pd.read_sql_query("SELECT * FROM trial_matches", conn)
        
        if df.empty:
            return pd.DataFrame()
            
        return df
        
    except Exception as e:
        st.error(f"Database error: {e}")
        return pd.DataFrame()
    
    finally:
        if conn:
            conn.close()


@st.cache_data(ttl=60)
def load_drift_metrics_data():
    """Load drift metrics from SQLite. Cached for 60 seconds."""
    conn = None
    try:
        conn = sqlite3.connect(inferences_path)
        df = pd.read_sql_query("SELECT * FROM drift_metrics ORDER BY timestamp DESC", conn)
        
        if df.empty:
            return pd.DataFrame()
            
        # Convert timestamp to datetime
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        return df
        
    except Exception as e:
        st.error(f"Drift metrics error: {e}")
        return pd.DataFrame()
    
    finally:
        if conn:
            conn.close()


# ===========================================================================
# SIDEBAR FILTERS
# ===========================================================================

def render_sidebar(df):
    """Render sidebar with filters and data refresh controls."""
    
    st.sidebar.header("⚙️ Filters")
    
    # Refresh button
    if st.sidebar.button("🔄 Refresh Data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    
    st.sidebar.markdown("---")
    
    # Date range filter
    st.sidebar.subheader("Date Range")
    valid_timestamps = df['timestamp'].dropna()
    if valid_timestamps.empty:
        st.sidebar.info("No valid timestamps available.")
        return df
    min_date = valid_timestamps.min().date()
    max_date = valid_timestamps.max().date()
    date_range = st.sidebar.date_input(
        "Select date range",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date
    )
    
    # Age range filter
    st.sidebar.subheader("Age Range")
    valid_ages = df['age'].dropna()
    min_age = int(valid_ages.min()) if not valid_ages.empty else 0
    max_age = int(valid_ages.max()) if not valid_ages.empty else 100
    if min_age == max_age:
        max_age = min_age + 1
    age_range = st.sidebar.slider(
        "Select age range",
        min_value=min_age,
        max_value=max_age,
        value=(min_age, max_age)
    )
    
    # Sex filter
    st.sidebar.subheader("Sex")
    sex_values = df['sex'].dropna().unique().tolist()
    sex_options = ['All'] + sorted(sex_values)
    selected_sex_option = st.sidebar.selectbox(
        "Select sex",
        options=sex_options,
        index=0
    )
    
    # Convert to list for filtering logic
    if selected_sex_option == 'All':
        selected_sex = sex_values if sex_values else df['sex'].unique().tolist()
    else:
        selected_sex = [selected_sex_option]
    
    # Condition count filter
    st.sidebar.subheader("Condition Count")
    valid_conditions = df['condition_count'].dropna()
    min_conditions = int(valid_conditions.min()) if not valid_conditions.empty else 0
    max_conditions = int(valid_conditions.max()) if not valid_conditions.empty else 50
    if min_conditions == max_conditions:
        max_conditions = min_conditions + 1
    condition_range = st.sidebar.slider(
        "Select condition count range",
        min_value=min_conditions,
        max_value=max_conditions,
        value=(min_conditions, max_conditions)
    )
    
    # Medication count filter
    st.sidebar.subheader("Medication Count")
    valid_meds = df['medication_count'].dropna()
    min_meds = int(valid_meds.min()) if not valid_meds.empty else 0
    max_meds = int(valid_meds.max()) if not valid_meds.empty else 50
    if min_meds == max_meds:
        max_meds = min_meds + 1
    medication_range = st.sidebar.slider(
        "Select medication count range",
        min_value=min_meds,
        max_value=max_meds,
        value=(min_meds, max_meds)
    )
    
    # Match Status filter
    st.sidebar.subheader("Match Status")
    match_status_option = st.sidebar.selectbox(
        "Select match status",
        options=['All', 'Any Match (Full + Partial)', 'No Match Only'],
        index=0
    )
    
    # Apply filters
    filtered_df = df.copy()
    
    if len(date_range) == 2:
        start_date, end_date = date_range
        filtered_df = filtered_df[
            (filtered_df['timestamp'].dt.date >= start_date) &
            (filtered_df['timestamp'].dt.date <= end_date)
        ]
    
    filtered_df = filtered_df[
        (filtered_df['age'] >= age_range[0]) &
        (filtered_df['age'] <= age_range[1])
    ]
    
    if selected_sex:
        filtered_df = filtered_df[filtered_df['sex'].isin(selected_sex)]
    
    filtered_df = filtered_df[
        (filtered_df['condition_count'] >= condition_range[0]) &
        (filtered_df['condition_count'] <= condition_range[1])
    ]
    
    filtered_df = filtered_df[
        (filtered_df['medication_count'] >= medication_range[0]) &
        (filtered_df['medication_count'] <= medication_range[1])
    ]
    
    if match_status_option == 'Any Match (Full + Partial)':
        filtered_df = filtered_df[filtered_df['eligible_matches'] > 0]
    elif match_status_option == 'No Match Only':
        filtered_df = filtered_df[filtered_df['eligible_matches'] == 0]
    
    # Show filter stats
    st.sidebar.markdown("---")
    st.sidebar.metric(
        "Showing", 
        f"{len(filtered_df):,} inferences",
        delta=f"{len(filtered_df) - len(df):+,}" if len(filtered_df) != len(df) else None,
        help="Pipeline runs matching current filters"
    )
    
    # Export section
    st.sidebar.markdown("---")
    st.sidebar.subheader("📥 Export Data")
    
    export_all = st.sidebar.checkbox("Export all columns", value=True)
    
    if export_all:
        selected_columns = filtered_df.columns.tolist()
    else:
        with st.sidebar.expander("Choose columns"):
            selected_columns = st.multiselect(
                "Select columns",
                options=filtered_df.columns.tolist(),
                default=filtered_df.columns.tolist(),
                label_visibility="collapsed"
            )
    
    if selected_columns:
        csv = filtered_df[selected_columns].to_csv(index=False, quoting=1)
        st.sidebar.download_button(
            label=f"Download CSV ({len(selected_columns)} cols)",
            data=csv,
            file_name=f"trialmatch_data_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv",
            use_container_width=True
        )
    
    return filtered_df


# ===========================================================================
# OVERVIEW TAB
# ===========================================================================

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


# ===========================================================================
# PERFORMANCE TAB
# ===========================================================================

def render_performance_tab(df):
    """Render Performance tab with latency analysis."""
    
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
        fig_gpt4o = px.histogram(
            df,
            x='gpt4o_evaluation_time',
            nbins=30,
            labels={'gpt4o_evaluation_time': 'GPT-4o Time (seconds)'},
            template='plotly_white',
            title='GPT-4o Evaluation Latency'
        )
        fig_gpt4o.add_vline(
            x=df['gpt4o_evaluation_time'].median(),
            line_dash="dash",
            line_color="red"
        )
        fig_gpt4o.add_annotation(
            x=df['gpt4o_evaluation_time'].median(), y=1, yref="paper",
            text=f"Median: {df['gpt4o_evaluation_time'].median():.1f}s",
            showarrow=True, arrowhead=0, ax=45, ay=-25,
            font=dict(size=11, color="red"),
            bgcolor="white", borderpad=2
        )
        fig_gpt4o.update_layout(
            height=300,
            margin=dict(l=20, r=20, t=40, b=20),
            showlegend=False
        )
        st.plotly_chart(fig_gpt4o, use_container_width=True)
    
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
        'gpt4o_evaluation_time'
    ]
    
    stage_labels = [
        'Hybrid Retrieval',
        'Cross-Encoder',
        'Rule Filter',
        'GPT-4o Evaluation'
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
    
    slowest = df.nlargest(10, 'total_time')[
        ['patient_id', 'age', 'sex', 'condition_count', 'medication_count',
         'candidates_evaluated', 'total_time', 'gpt4o_evaluation_time',
         'gpt4o_output_tokens']
    ].copy()
    
    slowest.insert(0, 'Rank', range(1, len(slowest) + 1))
    
    slowest.columns = [
        'Rank', 'Patient ID', 'Age', 'Sex', 'Conditions', 'Medications',
        'Trials Evaluated', 'Total Time (s)', 'GPT-4o Time (s)', 'Output Tokens'
    ]
    
    slowest['Total Time (s)'] = slowest['Total Time (s)'].round(1)
    slowest['GPT-4o Time (s)'] = slowest['GPT-4o Time (s)'].round(1)
    
    st.dataframe(
        slowest,
        use_container_width=True,
        hide_index=True
    )
    
    st.caption(
        "Top 10 patients by total end-to-end pipeline latency. "
        "Rank 1 = slowest. High latency typically correlates with more trials evaluated or larger GPT-4o output."
    )

    st.markdown("---")

    # ── Retrieval Quality Analysis ─────────────────────────────────────────
    st.subheader("🎯 Retrieval Quality: Rerank Score vs Match Outcome")

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
                tier = classify_trial_score(row['match_score'])
                return 'Eligible' if tier == 'Full Match' else tier

            tm_perf['match_status'] = tm_perf.apply(classify_match, axis=1)
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
                    name='Trials Sent to GPT-4o',
                    line=dict(color='#1f77b4', width=2.5, dash='dash')
                ))
                
                fig_recall.update_layout(
                    title='Recall vs Cost Tradeoff by Rerank Threshold',
                    height=350,
                    margin=dict(l=20, r=20, t=40, b=20),
                    template='plotly_white',
                    xaxis_title='Rerank Score Threshold (cutoff)',
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
                                f"Saves {cost_saved_safe}% GPT-4o cost"
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
                    "Each point on the x-axis is a candidate rerank score cutoff — trials scoring below it are filtered before GPT-4o. "
                    "Green = fraction of true matches still captured; dashed blue = fraction of all trials still sent (proxy for GPT-4o cost). "
                    "When both lines track closely, no aggressive score cutoff is safe without sacrificing recall — "
                    "confirming that MedCPT scores topical relevance uniformly and eligibility discrimination belongs to GPT-4o."
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
                    name='Trials Sent to GPT-4o',
                    line=dict(color='#1f77b4', width=2.5, dash='dash')
                ))

                fig_topn.update_layout(
                    title='Recall vs Cost Tradeoff by Top-N Cap (Trials Per Patient)',
                    height=350,
                    margin=dict(l=20, r=20, t=40, b=20),
                    template='plotly_white',
                    xaxis_title='Trials Sent to GPT-4o Per Patient (N)',
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
                                    f"❌ Lose {recall_lost}% of matches ({matched_lost} matched trials of current db never reach GPT-4o)<br>"
                                    f"✅ Save {cost_saved}% GPT-4o cost"
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
                                    f"Saves {cost_saved_safe}% GPT-4o cost"
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
                    "Each point on the x-axis is a per-patient top-N cap — only the N highest rerank-scoring trials "
                    "per patient are sent to GPT-4o. Green = fraction of true matches still captured; "
                    "dashed blue = fraction of all trials still sent (proxy for GPT-4o cost). "
                    "Use this chart to find the minimum N that preserves ≥95% recall, "
                    "directly informing the MAX_TRIALS_FOR_EVALUATION config value."
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
                    title='Rerank Score by Match Outcome (each dot = one trial)',
                    height=350,
                    margin=dict(l=20, r=20, t=40, b=20),
                    template='plotly_white',
                    xaxis_title='Match Status',
                    yaxis_title='Rerank Score',
                    xaxis=dict(categoryorder='array', categoryarray=category_order),
                )
                st.plotly_chart(fig_strip, use_container_width=True)

            with col2:
                eligible_scores    = tm_perf[tm_perf['eligible'] == 'eligible']['rerank_score'].dropna().values
                not_eligible_scores = tm_perf[tm_perf['eligible'] == 'not_eligible']['rerank_score'].dropna().values

                fig_kde = go.Figure()

                def _kde_curve(scores, color, name):
                    if len(scores) < 3:
                        return None
                    from scipy.stats import gaussian_kde
                    kde = gaussian_kde(scores, bw_method='scott')
                    x_range = np.linspace(scores.min() - 0.05, scores.max() + 0.05, 300)
                    y_range = kde(x_range)
                    return x_range, y_range

                for scores, color, fill_color, name in [
                    (eligible_scores,     '#2ca02c', 'rgba(44,160,44,0.15)',  'Eligible / Partial'),
                    (not_eligible_scores, '#d62728', 'rgba(214,39,40,0.15)',  'Not Eligible'),
                ]:
                    result = _kde_curve(scores, color, name)
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
                    title='Rerank Score Density by Outcome (aggregated)',
                    height=350,
                    margin=dict(l=20, r=20, t=40, b=20),
                    template='plotly_white',
                    xaxis_title='Rerank Score',
                    yaxis_title='Density',
                    legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
                )
                fig_kde.update_layout(height=400)
                st.plotly_chart(fig_kde, use_container_width=True)

            st.caption(
                "Left: each dot is one trial scored by MedCPT. Overlapping distributions across outcome groups are expected — "
                "MedCPT measures topical relevance, not eligibility. "
                "Right: aggregated score distributions confirm both groups are oncologically relevant; "
                "GPT-4o handles eligibility discrimination in the next stage. Dashed lines mark group medians."
            )

            # Summary metrics
            col1, col2, col3 = st.columns(3)
            with col1:
                median_match = tm_perf[tm_perf['eligible'] == 'eligible']['rerank_score'].median()
                st.metric("Median Rerank Score (Matches)", f"{median_match:.3f}" if not pd.isna(median_match) else "N/A")
            with col2:
                median_nonmatch = tm_perf[tm_perf['eligible'] == 'not_eligible']['rerank_score'].median()
                st.metric("Median Rerank Score (Not Eligible)", f"{median_nonmatch:.3f}" if not pd.isna(median_nonmatch) else "N/A")
            with col3:
                separation = median_match - median_nonmatch if not pd.isna(median_match) and not pd.isna(median_nonmatch) else None
                st.metric("Score Separation", f"{separation:.3f}" if separation is not None else "N/A",
                         help=(
                             "Difference in median MedCPT rerank scores between matches and non-matches. "
                             "Values near zero are expected — MedCPT is a topical relevance model (trained on PubMed search logs), "
                             "not an eligibility classifier. All retrieved trials are oncologically relevant by design; "
                             "eligibility discrimination is handled by GPT-4o in the next pipeline stage."
                         ))
        else:
            st.info("No rerank score data available for the selected filters.")
    else:
        st.info("No trial match data available.")

# ===========================================================================
# COST & TOKENS TAB
# ===========================================================================

def render_cost_tokens_tab(df):
    """Render Cost & Tokens tab."""
    
    st.header("💰 Cost & Token Analysis")
    
    st.subheader("Cost Analysis")
    
    # Exclude failed inferences (API errors) from cost analysis
    df = df[df['error'].fillna('') == ''].copy()
    
    col1, col2, col3, col4 = st.columns(4)
    
    total_cost = df['estimated_cost_usd'].sum()
    avg_cost = df['estimated_cost_usd'].mean()
    median_cost = df['estimated_cost_usd'].median()
    projected_1000 = avg_cost * 1000
    
    with col1:
        st.metric("Total Cost", f"${total_cost:.2f}", help="Total API cost for all inferences")
    
    with col2:
        st.metric("Average Cost", f"${avg_cost:.4f}", help="Average cost per patient inference")
    
    with col3:
        st.metric("Median Cost", f"${median_cost:.4f}", help="Median cost per patient")
    
    with col4:
        st.metric("Projected (1000)", f"${projected_1000:.2f}", help="Estimated cost for 1000 patients")
    
    st.markdown("---")
    st.subheader("Cost Breakdown by Model")
    
    # This recomputes cost from raw tokens rather than reading
    # estimated_cost_usd, so it needs the same pricing table the writer used.
    # It used to default an unpriced model to {"input": 0.0, "output": 0.0},
    # which rendered a $0.00 breakdown and a pie chart of nothing — a reader
    # cannot tell that from a genuinely cheap run. get_model_cost() now raises
    # instead, and the tab says so rather than drawing an empty chart.
    try:
        gpt4o_input_cost  = get_model_cost(
            MATCHING_MODEL, int(df['gpt4o_input_tokens'].sum()), 0
        )
        gpt4o_output_cost = get_model_cost(
            MATCHING_MODEL, 0, int(df['gpt4o_output_tokens'].sum())
        )
    except UnknownModelPricingError as e:
        st.error(
            f"Cost breakdown unavailable: {e}"
        )
        return

    recalc_total = gpt4o_input_cost + gpt4o_output_cost
    
    df_cost = pd.DataFrame({
        'Component': ['GPT-4o Output', 'GPT-4o Input'],
        'Cost': [gpt4o_output_cost, gpt4o_input_cost],
        'Percentage': [
            gpt4o_output_cost / recalc_total * 100 if recalc_total > 0 else 0,
            gpt4o_input_cost / recalc_total * 100 if recalc_total > 0 else 0
        ]
    })
    
    col1, col2 = st.columns(2)
    
    with col1:
        fig_pie = px.pie(df_cost, values='Cost', names='Component', template='plotly_white', title='Cost Distribution')
        fig_pie.update_traces(textposition='auto', textinfo='percent+label', textfont_size=14)
        fig_pie.update_layout(height=350, margin=dict(l=20, r=20, t=40, b=20))
        st.plotly_chart(fig_pie, use_container_width=True)
    
    with col2:
        # Stacked bars: token volume vs cost share — exposes the 4× pricing asymmetry
        total_input_tokens = df['gpt4o_input_tokens'].sum()
        total_output_tokens = df['gpt4o_output_tokens'].sum()

        total_tokens = total_input_tokens + total_output_tokens
        input_tok_pct = total_input_tokens / total_tokens * 100 if total_tokens > 0 else 0
        output_tok_pct = total_output_tokens / total_tokens * 100 if total_tokens > 0 else 0

        input_cost_pct = gpt4o_input_cost / recalc_total * 100 if recalc_total > 0 else 0
        output_cost_pct = gpt4o_output_cost / recalc_total * 100 if recalc_total > 0 else 0

        fig_asym = go.Figure()

        # Input share (bottom of each stack)
        fig_asym.add_trace(go.Bar(
            x=['Token Volume', 'Cost Share'],
            y=[input_tok_pct, input_cost_pct],
            name='Input',
            marker_color='#1f77b4',
            text=[
                f"{input_tok_pct:.0f}%<br>({total_input_tokens:,.0f} tok)",
                f"{input_cost_pct:.0f}%<br>(${gpt4o_input_cost:.3f})"
            ],
            textposition='inside',
            insidetextanchor='middle',
            textfont=dict(size=11, color='white'),
        ))

        # Output share (top of each stack)
        fig_asym.add_trace(go.Bar(
            x=['Token Volume', 'Cost Share'],
            y=[output_tok_pct, output_cost_pct],
            name='Output (4× price/tok)',
            marker_color='#ff7f0e',
            text=[
                f"{output_tok_pct:.0f}%<br>({total_output_tokens:,.0f} tok)",
                f"{output_cost_pct:.0f}%<br>(${gpt4o_output_cost:.3f})"
            ],
            textposition='inside',
            insidetextanchor='middle',
            textfont=dict(size=11, color='white'),
        ))

        fig_asym.update_layout(
            barmode='stack',
            title='Token Volume vs Cost Share',
            yaxis_title='Share (%)',
            yaxis=dict(range=[0, 105]),
            height=350,
            margin=dict(l=20, r=20, t=45, b=20),
            template='plotly_white',
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='center', x=0.5),
        )

        st.plotly_chart(fig_asym, use_container_width=True)
    
    st.markdown("**Detailed Breakdown:**")
    df_cost['Cost'] = df_cost['Cost'].apply(lambda x: f"${x:.4f}")
    df_cost['Percentage'] = df_cost['Percentage'].apply(lambda x: f"{x:.1f}%")
    st.dataframe(df_cost, use_container_width=True, hide_index=True)
    
    st.caption(
        "GPT-4o input tokens carry the prompt and patient/trial data; output tokens carry the eligibility assessments. "
        "Output tokens cost 4x more per token than input tokens."
    )
    
    st.markdown("---")
    st.subheader("Token Usage & Efficiency")
    
    col1, col2 = st.columns(2)
    
    with col1:
        df_tpt = df[df['candidates_evaluated'] > 0].copy()
        df_tpt['input_per_trial'] = df_tpt['gpt4o_input_tokens'] / df_tpt['candidates_evaluated']
        df_tpt['output_per_trial'] = df_tpt['gpt4o_output_tokens'] / df_tpt['candidates_evaluated']
        
        tier_order = list(MATCH_TIERS)
        tier_colors = MATCH_TIER_COLORS
        
        tpt_stats = df_tpt.groupby('match_tier').agg(
            avg_input=('input_per_trial', 'mean'),
            avg_output=('output_per_trial', 'mean'),
        ).reindex(tier_order).dropna()
        
        fig_tpt = go.Figure()
        fig_tpt.add_trace(go.Bar(
            x=tpt_stats.index,
            y=tpt_stats['avg_input'],
            name='Input Tokens',
            marker_color='#1f77b4',
            text=[f"{v:,.0f}" for v in tpt_stats['avg_input']],
            textposition='inside',
            insidetextanchor='middle',
        ))
        fig_tpt.add_trace(go.Bar(
            x=tpt_stats.index,
            y=tpt_stats['avg_output'],
            name='Output Tokens (4× cost)',
            marker_color='#ff7f0e',
            text=[f"{v:,.0f}" for v in tpt_stats['avg_output']],
            textposition='inside',
            insidetextanchor='middle',
        ))
        fig_tpt.update_layout(
            barmode='stack',
            title='Avg Tokens per Trial by Match Tier',
            yaxis_title='Tokens per Trial',
            height=350,
            margin=dict(l=20, r=20, t=40, b=20),
            template='plotly_white',
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        )
        st.plotly_chart(fig_tpt, use_container_width=True)
    
    with col2:
        df_efficiency = df[df['candidates_evaluated'] > 0].copy()
        df_efficiency['tokens_per_trial'] = (df_efficiency['gpt4o_input_tokens'] + df_efficiency['gpt4o_output_tokens']) / df_efficiency['candidates_evaluated']
        
        fig_efficiency = px.histogram(df_efficiency, x='tokens_per_trial', nbins=30, labels={'tokens_per_trial': 'Tokens/Trial'}, template='plotly_white', title='Token Efficiency (Total per Trial)')
        fig_efficiency.add_vline(x=df_efficiency['tokens_per_trial'].median(), line_dash="dash", line_color="red", annotation_text=f"Median: {df_efficiency['tokens_per_trial'].median():.0f}")
        fig_efficiency.update_layout(height=350, margin=dict(l=20, r=20, t=40, b=20), showlegend=False)
        st.plotly_chart(fig_efficiency, use_container_width=True)
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.metric("Avg Input Tokens", f"{df['gpt4o_input_tokens'].mean():,.0f}", help="Average GPT-4o input tokens per patient (prompt + criteria + patient data)")
    with col2:
        st.metric("Avg Output Tokens", f"{df['gpt4o_output_tokens'].mean():,.0f}", help="Average GPT-4o output tokens per patient (eligibility assessments)")
    with col3:
        st.metric("Avg Tokens/Trial", f"{df_efficiency['tokens_per_trial'].mean():.0f}" if len(df_efficiency) > 0 else "N/A", help="Average total tokens consumed per trial evaluation")
    
    st.caption(
        "Patient Complexity = condition count + medication count. "
        "Token usage scales primarily with the number of trials evaluated, not patient complexity. "
        "Tokens/Trial reflects full criterion-level evaluation — all criteria are evaluated and returned "
        "for both eligible and not-eligible trials to enable auditability and post-hoc analysis."
    )
    
    st.markdown("---")
    st.subheader("Cost Efficiency")

    col1, col2 = st.columns(2)

    with col1:
        # --- Dumbbell Chart: Cost per Patient vs Cost per Match ---
        tier_order = list(MATCH_TIERS)
        tier_colors = MATCH_TIER_COLORS

        tier_stats = df.groupby('match_tier').agg(
            avg_cost=('estimated_cost_usd', 'mean'),
            count=('patient_id', 'count'),
        ).reindex(tier_order).dropna()

        # Cost per match for tiers with matches
        df_with_matches = df[df['eligible_matches'] > 0].copy()
        df_with_matches['cost_per_match'] = df_with_matches['estimated_cost_usd'] / df_with_matches['eligible_matches']
        cpm_by_tier = df_with_matches.groupby('match_tier')['cost_per_match'].mean()

        fig_unit = go.Figure()

        # Draw connecting lines + dots for each tier (horizontal dumbbell)
        for i, tier in enumerate(tier_stats.index):
            cost_patient = tier_stats.loc[tier, 'avg_cost']
            n = int(tier_stats.loc[tier, 'count'])
            color = tier_colors.get(tier, '#999')

            # Cost per match (only for tiers that produce matches)
            has_cpm = tier in cpm_by_tier.index
            cost_match = cpm_by_tier[tier] if has_cpm else None

            if has_cpm:
                # Connecting line between the two dots
                fig_unit.add_trace(go.Scatter(
                    x=[cost_patient, cost_match],
                    y=[tier, tier],
                    mode='lines',
                    line=dict(color='#888', width=3),
                    showlegend=False,
                    hoverinfo='skip',
                ))

            # Dot: cost per patient (circle)
            fig_unit.add_trace(go.Scatter(
                x=[cost_patient],
                y=[tier],
                mode='markers+text',
                marker=dict(size=14, color=color, symbol='circle',
                            line=dict(width=1.5, color='white')),
                text=[f"${cost_patient:.4f}"],
                textposition='bottom center',
                textfont=dict(size=10),
                name='Cost / Patient' if i == 0 else None,
                showlegend=(i == 0),
                legendgroup='patient',
            ))

            # Dot: cost per match (diamond) — only for tiers with matches
            if has_cpm:
                fig_unit.add_trace(go.Scatter(
                    x=[cost_match],
                    y=[tier],
                    mode='markers+text',
                    marker=dict(size=14, color='black', symbol='diamond',
                                line=dict(width=1.5, color='white')),
                    text=[f"${cost_match:.4f}"],
                    textposition='top center',
                    textfont=dict(size=10),
                    name='Cost / Match' if i == 0 else None,
                    showlegend=(i == 0),
                    legendgroup='match',
                ))

            # Patient count annotation
            fig_unit.add_annotation(
                x=0, y=tier,
                text=f"n={n}",
                showarrow=False,
                xanchor='right', xshift=-10,
                font=dict(size=9, color='gray'),
            )

        fig_unit.update_layout(
            title='Cost Efficiency: Per Patient vs Per Match',
            xaxis_title='Cost (USD)',
            height=380,
            margin=dict(l=100, r=20, t=45, b=40),
            template='plotly_white',
            showlegend=True,
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='center', x=0.5),
            yaxis=dict(categoryorder='array', categoryarray=list(reversed(tier_order))),
        )
        st.plotly_chart(fig_unit, use_container_width=True)

    with col2:
        # --- Cost Distribution with Tier Overlay ---
        fig_dist = go.Figure()

        for tier in tier_order:
            tier_data = df[df['match_tier'] == tier]['estimated_cost_usd']
            if len(tier_data) > 0:
                fig_dist.add_trace(go.Histogram(
                    x=tier_data,
                    name=tier,
                    marker_color=tier_colors[tier],
                    opacity=0.7,
                    nbinsx=25,
                ))

        fig_dist.add_vline(x=avg_cost, line_dash="dash", line_color="black",
                           annotation_text=f"Mean: ${avg_cost:.4f}",
                           annotation_font_color="black")

        fig_dist.update_layout(
            barmode='overlay',
            title='Cost Distribution by Match Tier',
            xaxis_title='Cost ($)',
            yaxis_title='Patient Count',
            height=380,
            margin=dict(l=20, r=20, t=45, b=20),
            template='plotly_white',
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        )
        st.plotly_chart(fig_dist, use_container_width=True)

    st.caption(
        "**Left:** Colored circles show average cost per patient by tier. Black diamonds show cost per eligible match. "
        "The connecting line reveals the efficiency gap — shorter lines mean better cost efficiency. "
        "**Right:** Cost distributions colored by match tier — overlap shows that cost alone does not predict match success."
    )


# ===========================================================================
# PATIENT DEMOGRAPHICS TAB
# ===========================================================================

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
    



# ===========================================================================
# PATIENT EXPLORER TAB
# ===========================================================================

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
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.metric("GPT-4o Input Tokens", f"{int(patient_df['gpt4o_input_tokens']):,}", help="Tokens sent to GPT-4o (prompt + trial criteria + patient data)")
    with col2:
        st.metric("GPT-4o Output Tokens", f"{int(patient_df['gpt4o_output_tokens']):,}", help="Tokens generated by GPT-4o (eligibility assessments)")
    
    if pd.notna(patient_df['error']) and patient_df['error'] != '':
        st.error(f"Error: {patient_df['error']}")


# ===========================================================================
# MATCH QUALITY TAB
# ===========================================================================

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
        st.metric("✅ Full Match", f"{full_cnt}", delta=f"{full_cnt / len(df) * 100:.1f}%" if len(df) > 0 else "0%", help="Patients with at least 1 trial where ALL criteria confirmed (100% score)")
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
        sd = pd.DataFrame({
            'Outcome': ['✅ Full Match', TRIAL_STATUS_PARTIAL, '🔶 Unconfirmed Match', '❌ No Match'],
            'Count': [full_cnt, partial_cnt, unconfirmed_cnt, no_match_cnt]
        })

        fig_s = px.pie(sd, values='Count', names='Outcome', template='plotly_white',
                       title='Patient Match Distribution', color='Outcome',
                       color_discrete_map={
                           '✅ Full Match':        MATCH_TIER_COLORS['Full Match'],
                           TRIAL_STATUS_PARTIAL:   MATCH_TIER_COLORS['Partial Match'],
                           '🔶 Unconfirmed Match': MATCH_TIER_COLORS['Unconfirmed Match'],
                           '❌ No Match':          MATCH_TIER_COLORS['No Match'],
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


# ===========================================================================
# TRIAL EXPLORER TAB
# ===========================================================================

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
    
    display_cols = ['Status', 'patient_id', 'age', 'sex', 'primary_condition', 'Match Score', 'explanation']
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


# ===========================================================================
# DRIFT DETECTION TAB
# ===========================================================================

@st.fragment
def render_drift_detection_tab(df):
    """Render drift detection monitoring tab."""
    
    st.header("🔬 Drift Detection")
    
    # Load drift metrics
    drift_df = load_drift_metrics_data()
    
    # Check if drift detection has been run
    if drift_df.empty:
        st.info("📊 No drift detection results available yet.")
        
        # Check if data is sufficient for drift detection
        if df.empty:
            st.warning("⚠️ No inferences in database. Run the pipeline first.")
            return
        
        # Calculate data span
        first_date = df['timestamp'].min()
        last_date = df['timestamp'].max()
        time_span = (last_date - first_date).days
        total_inferences = len(df)
        
        st.subheader("Requirements for Drift Detection")
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            status_icon = "✅" if time_span >= 30 else "❌"
            st.metric(
                "Time Span",
                f"{time_span} days",
                delta=f"{status_icon} Need 30+ days",
                help="Drift detection requires at least 30 days of data"
            )
        
        with col2:
            status_icon = "✅" if total_inferences >= 20 else "❌"
            st.metric(
                "Total Inferences",
                total_inferences,
                delta=f"{status_icon} Need 20+",
                help="Minimum 20 inferences required for baseline"
            )
        
        with col3:
            recent_df = df[df['timestamp'] >= (last_date - pd.Timedelta(days=7))]
            recent_count = len(recent_df)
            status_icon = "✅" if recent_count >= 5 else "❌"
            st.metric(
                "Recent (7 days)",
                recent_count,
                delta=f"{status_icon} Need 5+",
                help="Minimum 5 recent inferences for comparison"
            )
        
        st.markdown("---")
        
        st.subheader("How to Enable Drift Detection")
        st.markdown("""
        1. **Generate More Inferences**: Run the pipeline over time to collect 30+ days of data
        2. **Run Drift Detection**: Execute `python "20- Drift Detection.py"`
        3. **Refresh Dashboard**: Results will appear here automatically
        
        **What Drift Detection Monitors:**
        - 📊 **Data Drift**: Changes in patient population (age, conditions, medications)
        - 🔍 **Retrieval Drift**: Changes in RAG performance (candidates retrieved/filtered)
        - ⚡ **Performance Drift**: Changes in pipeline speed and match quality
        """)
        
        return
    
    # === DRIFT METRICS AVAILABLE ===
    
    # Get latest drift detection run
    latest_timestamp = drift_df['timestamp'].max()
    latest_run = drift_df[drift_df['timestamp'] == latest_timestamp].copy()
    
    # Summary metrics
    st.subheader("Latest Drift Detection Results")
    st.caption(f"Last run: {latest_timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
    
    col1, col2, col3, col4 = st.columns(4)
    
    total_alerts = (latest_run['alert'] == 1).sum()
    total_metrics = len(latest_run)
    data_alerts = (latest_run[latest_run['metric_category'] == 'data_drift']['alert'] == 1).sum()
    retrieval_alerts = (latest_run[latest_run['metric_category'] == 'retrieval_drift']['alert'] == 1).sum()
    performance_alerts = (latest_run[latest_run['metric_category'] == 'performance_drift']['alert'] == 1).sum()
    
    with col1:
        st.metric(
            "🚨 Total Alerts",
            total_alerts,
            delta=f"{total_alerts}/{total_metrics} metrics",
            help="Number of metrics that triggered alerts"
        )
    
    with col2:
        alert_color = "🟢" if data_alerts == 0 else "🔴"
        st.metric(
            f"{alert_color} Data Drift",
            data_alerts,
            help="Changes in patient demographics and characteristics"
        )
    
    with col3:
        alert_color = "🟢" if retrieval_alerts == 0 else "🔴"
        st.metric(
            f"{alert_color} Retrieval Drift",
            retrieval_alerts,
            help="Changes in retrieval stage performance"
        )
    
    with col4:
        alert_color = "🟢" if performance_alerts == 0 else "🔴"
        st.metric(
            f"{alert_color} Performance Drift",
            performance_alerts,
            help="Changes in match quality and processing time"
        )
    
    st.markdown("---")
    
    # Detailed metrics table
    st.subheader("Detailed Metrics")
    
    category_filter = st.selectbox(
        "Filter by Category",
        ["All", "Data Drift", "Retrieval Drift", "Performance Drift"],
        key="drift_category_filter"
    )
    
    if category_filter == "All":
        display_df = latest_run.copy()
    else:
        category_map = {
            "Data Drift": "data_drift",
            "Retrieval Drift": "retrieval_drift",
            "Performance Drift": "performance_drift"
        }
        display_df = latest_run[latest_run['metric_category'] == category_map[category_filter]].copy()
    
    # Format table
    available_cols = ['metric_name', 'metric_value', 'threshold', 'alert', 'p_value', 'z_score', 'notes']
    display_cols = [col for col in available_cols if col in display_df.columns]
    display_df_clean = display_df[display_cols].copy()
    
    # Add status emoji
    display_df_clean['status'] = display_df_clean['alert'].apply(lambda x: '🚨 ALERT' if x == 1 else '✅ OK')
    
    # Reorder columns
    final_cols = ['status'] + [col for col in display_cols if col != 'alert']
    display_df_clean = display_df_clean[final_cols]
    
    st.dataframe(
        display_df_clean,
        use_container_width=True,
        hide_index=True
    )
    
    st.caption(
        "Each row is a monitored metric from the latest drift detection run. "
        "'ALERT' indicates the metric exceeded its statistical threshold (z-score or p-value). "
        "Investigate alerts to determine if pipeline behavior has shifted."
    )
    
    st.markdown("---")
    
    # Time-series plots
    st.subheader("Historical Trends")
    
    unique_runs = drift_df['timestamp'].nunique()
    
    if unique_runs < 2:
        st.info("📈 Run drift detection multiple times to see historical trends.")
    else:
        metric_names = drift_df['metric_name'].unique()
        
        selected_metric = st.selectbox(
            "Select Metric to Plot",
            sorted(metric_names),
            key="drift_metric_select"
        )
        
        metric_data = drift_df[drift_df['metric_name'] == selected_metric].sort_values('timestamp').copy()
        
        if metric_data.empty:
            st.warning(f"No data available for {selected_metric}")
            return
        
        # Create time-series plot
        fig = go.Figure()
        
        # Add metric value line
        fig.add_trace(go.Scatter(
            x=metric_data['timestamp'],
            y=metric_data['metric_value'],
            mode='lines+markers',
            name='Metric Value',
            line=dict(color='#1f77b4', width=2),
            marker=dict(size=8)
        ))
        
        # Add threshold line
        if 'threshold' in metric_data.columns and metric_data['threshold'].notna().any():
            threshold_values = metric_data['threshold'].dropna().unique()
            if len(threshold_values) == 1:
                threshold_value = threshold_values[0]
                fig.add_hline(
                    y=threshold_value,
                    line_dash="dash",
                    line_color="#d62728",
                    annotation_text=f"Threshold: {threshold_value}",
                    annotation_position="right"
                )
        
        # Highlight alerts
        alert_points = metric_data[metric_data['alert'] == 1]
        if not alert_points.empty:
            fig.add_trace(go.Scatter(
                x=alert_points['timestamp'],
                y=alert_points['metric_value'],
                mode='markers',
                name='Alerts',
                marker=dict(
                    size=15,
                    color='#d62728',
                    symbol='x',
                    line=dict(width=2)
                )
            ))
        
        fig.update_layout(
            title=f"{selected_metric.replace('_', ' ').title()} Over Time",
            xaxis_title="Date",
            yaxis_title="Value",
            hovermode='x unified',
            height=400,
            margin=dict(l=20, r=20, t=40, b=20),
            template='plotly_white'
        )
        
        st.plotly_chart(fig, use_container_width=True)
        
        # Statistics
        if len(metric_data) > 0 and metric_data['metric_value'].notna().any():
            col1, col2, col3 = st.columns(3)
            
            with col1:
                latest_val = metric_data['metric_value'].iloc[-1]
                if pd.notna(latest_val):
                    st.metric(
                        "Latest Value",
                        f"{latest_val:.4f}",
                        help="Most recent drift metric value"
                    )
                else:
                    st.metric("Latest Value", "N/A", help="No data available for this metric")
            
            with col2:
                mean_val = metric_data['metric_value'].mean()
                if pd.notna(mean_val):
                    st.metric(
                        "Mean",
                        f"{mean_val:.4f}",
                        help="Average value across all runs"
                    )
                else:
                    st.metric("Mean", "N/A", help="No data available for this metric")
            
            with col3:
                std_val = metric_data['metric_value'].std()
                if pd.notna(std_val):
                    st.metric(
                        "Std Dev",
                        f"{std_val:.4f}",
                        help="Standard deviation across all runs"
                    )
                else:
                    st.metric("Std Dev", "N/A", help="No data available for this metric")
        
        st.caption(
            "Each point is one drift detection run. Red ✕ markers indicate the metric exceeded its alert threshold. "
            "A rising trend may indicate the pipeline or patient population is changing over time."
        )


# ===========================================================================
# MAIN
# ===========================================================================

# Match tier vocabulary. Ordered best -> worst; every tier_order / tier_colors
# list in this file is built from these two so a tier can never be defined in
# one chart and dropped from another.
MATCH_TIERS = ['Full Match', 'Partial Match', 'Unconfirmed Match', 'No Match']

MATCH_TIER_COLORS = {
    'Full Match':        '#2ca02c',
    'Partial Match':     '#ffbb33',
    'Unconfirmed Match': '#e67e22',
    'No Match':          '#d62728',
}

# Per-trial status labels, same partition applied to a single trial row.
TRIAL_STATUS_FULL        = '✅ Full Match'
TRIAL_STATUS_PARTIAL     = '🟡 Partial Match'
TRIAL_STATUS_UNCONFIRMED = '🔶 Unconfirmed'
TRIAL_STATUS_REJECTED    = '❌ Not Eligible'


def classify_trial_score(match_score) -> str:
    """
    Bucket one ELIGIBLE trial's match_score into its tier.

    match_score is confirmed criteria / applicable criteria (File 13). A score
    of exactly 0.0 on an eligible trial means the model confirmed NOTHING: it
    found no disqualifier, but it also could not affirm a single criterion.
    That is a materially different finding from a trial where 9 of 10 criteria
    were confirmed, and lumping the two together as "Partial" hid it behind the
    strongest example in the bucket.
    """
    if match_score >= 1.0:
        return 'Full Match'
    if match_score > 0.0:
        return 'Partial Match'
    return 'Unconfirmed Match'


def enrich_match_tiers(df, trial_matches):
    """
    Enrich inferences df with per-patient match tier columns derived from trial_matches.

    Adds columns:
        full_match_count:        eligible trials with match_score == 1.0
        partial_match_count:     eligible trials with 0.0 < match_score < 1.0
        unconfirmed_match_count: eligible trials with match_score == 0.0
        match_tier:              'Full Match' | 'Partial Match' |
                                 'Unconfirmed Match' | 'No Match'

    'Unconfirmed Match' is its own tier, not a corner of 'Partial Match'. An
    eligible trial scoring 0.0 cleared the disqualifier check with nothing
    confirmable behind it; presenting it beside a 90%-confirmed trial overstates
    what the pipeline established about the patient.
    """
    if trial_matches is None or trial_matches.empty:
        df['full_match_count'] = 0
        df['partial_match_count'] = 0
        df['unconfirmed_match_count'] = 0
        df['match_tier'] = 'No Match'
        return df

    eligible = trial_matches[trial_matches['eligible'] == 'eligible'].copy()

    full = eligible[eligible['match_score'] >= 1.0].groupby('inference_id').size().reset_index(name='full_match_count')
    partial = eligible[
        (eligible['match_score'] > 0.0) & (eligible['match_score'] < 1.0)
    ].groupby('inference_id').size().reset_index(name='partial_match_count')
    unconfirmed = eligible[eligible['match_score'] <= 0.0].groupby('inference_id').size().reset_index(name='unconfirmed_match_count')

    df = df.merge(full, left_on='id', right_on='inference_id', how='left').drop(columns='inference_id', errors='ignore')
    df = df.merge(partial, left_on='id', right_on='inference_id', how='left').drop(columns='inference_id', errors='ignore')
    df = df.merge(unconfirmed, left_on='id', right_on='inference_id', how='left').drop(columns='inference_id', errors='ignore')

    df['full_match_count'] = df['full_match_count'].fillna(0).astype(int)
    df['partial_match_count'] = df['partial_match_count'].fillna(0).astype(int)
    df['unconfirmed_match_count'] = df['unconfirmed_match_count'].fillna(0).astype(int)

    # Tier: Full > Partial > Unconfirmed > No Match
    def assign_tier(row):
        if row['full_match_count'] > 0:
            return 'Full Match'
        elif row['partial_match_count'] > 0:
            return 'Partial Match'
        elif row['unconfirmed_match_count'] > 0:
            return 'Unconfirmed Match'
        return 'No Match'

    df['match_tier'] = df.apply(assign_tier, axis=1)

    return df


#------------------------------------------------------------------------------


# ===========================================================================
# REPRODUCIBILITY TAB
# ===========================================================================

@st.fragment
def render_reproducibility_tab(df):
    """
    Analyze pipeline reproducibility by comparing multiple inferences
    for the same patient within the same Qdrant collection (trial corpus).
    
    Only patients with 2+ inferences on the same collection are analyzed,
    ensuring a fair comparison (same trials, same patient, different runs).
    """
    
    st.header("🔁 Reproducibility Analysis")
    
    st.caption(
        "Measures the LLM evaluation determinism: given the same patient record and the same trial corpus, "
        "does the pipeline produce identical eligibility decisions and match scores across independent runs. "
        "Only inferences with identical patient data (verified by hash) on the same clinical trials collection are compared."
    )
    
    st.caption(
        "**Match Score:** Ranges from 0.0 to 1.0. Calculated as confirmed criteria / total criteria evaluated. "
        "For example, if a trial has 10 inclusion + exclusion criteria and GPT-4o confirms 7 (met or not_violated), "
        "the score is 0.70. A score of 1.0 means all criteria were confirmed. "
        "Not eligible trials are hardcoded to 0.0 regardless of criteria."
    )
    
    # Check if qdrant_collection column exists
    if 'qdrant_collection' not in df.columns:
        st.info("📊 No `qdrant_collection` data available. Re-run the pipeline with the updated schema to enable reproducibility analysis.")
        return
    
    # Load trial matches
    trial_matches = load_trial_matches_data()
    
    if trial_matches is None or trial_matches.empty:
        st.info("No trial match data available. Run the pipeline first.")
        return
    
    # Find patients with 2+ inferences on the same collection
    # Only compare inferences with identical patient data (same hash)
    # Empty hash = old data before hash was added — group by (patient, collection) as fallback
    if 'patient_data_hash' in df.columns and df['patient_data_hash'].notna().any() and (df['patient_data_hash'] != '').any():
        grouped = df.groupby(['patient_id', 'qdrant_collection', 'patient_data_hash']).filter(lambda g: len(g) >= 2)
    else:
        grouped = df.groupby(['patient_id', 'qdrant_collection']).filter(lambda g: len(g) >= 2)
    
    if grouped.empty:
        st.info("📊 No patients with multiple inferences on the same trial corpus yet. "
                "Run the batch pipeline with resampling to generate reproducibility data.")
        
        # Show what we have
        col1, col2 = st.columns(2)
        with col1:
            multi_inference = df.groupby('patient_id').size()
            patients_with_multi = (multi_inference >= 2).sum()
            st.metric("Patients with 2+ Inferences", patients_with_multi,
                      help="Patients run multiple times (any collection)")
        with col2:
            collections = df['qdrant_collection'].nunique()
            st.metric("Distinct Trial Corpora", collections,
                      help="Number of different Qdrant collections used")
        return
    
    # Compare all groups, or select one group
    collections = grouped['qdrant_collection'].unique().tolist()
    if len(collections) > 1:
        selected_collection = st.selectbox(
            "Filter by Trial Corpus",
            ["All"] + sorted(collections, reverse=True),
            key="repro_collection_filter"
        )
        if selected_collection != "All":
            grouped = grouped[grouped['qdrant_collection'] == selected_collection]
    
    # Collect all inference IDs for patients with 2+ inferences
    group_keys = ['patient_id', 'qdrant_collection', 'patient_data_hash'] if 'patient_data_hash' in grouped.columns and (grouped['patient_data_hash'] != '').any() else ['patient_id', 'qdrant_collection']
    
    patient_groups = []
    for group_key, group in grouped.groupby(group_keys):
        pid = group_key[0]
        col_name = group_key[1]
        inf_ids = group['id'].tolist()
        if len(inf_ids) < 2:
            continue
        patient_groups.append({
            'patient_id': pid,
            'qdrant_collection': col_name,
            'inference_ids': inf_ids,
            'num_inferences': len(inf_ids),
        })
    
    if not patient_groups:
        st.info("No valid comparison groups found.")
        return
    
    total_patients_retested = len(patient_groups)
    
    # Gather all inference IDs across all groups
    all_inf_ids = set()
    for pg in patient_groups:
        all_inf_ids.update(pg['inference_ids'])
    
    relevant_matches = trial_matches[trial_matches['inference_id'].isin(all_inf_ids)].copy()
    
    # Build per-(patient, trial) comparisons across ALL inferences
    # For each (patient, nct_id), collect all scores and classifications from every inference
    comparisons = []
    for pg in patient_groups:
        pid = pg['patient_id']
        col_name = pg['qdrant_collection']
        inf_ids = pg['inference_ids']
        
        # Get all trial matches for this patient's inferences
        patient_matches = relevant_matches[relevant_matches['inference_id'].isin(inf_ids)]
        
        # Group by nct_id — each trial evaluated across multiple inferences
        for nct_id, trial_group in patient_matches.groupby('nct_id'):
            # Only include trials present in ALL inferences for this patient
            inferences_with_trial = trial_group['inference_id'].nunique()
            if inferences_with_trial < 2:
                continue  # trial only appeared in 1 inference, cannot compare
            
            scores = trial_group['match_score'].tolist()
            classifications = trial_group['eligible'].tolist()
            
            score_min = min(scores)
            score_max = max(scores)
            score_spread = score_max - score_min
            all_scores_identical = len(set(scores)) == 1
            all_classifications_identical = len(set(classifications)) == 1
            
            # Determine group category
            unique_classifications = set(classifications)
            if unique_classifications == {'eligible'}:
                category = 'eligible_all'
            elif unique_classifications == {'not_eligible'}:
                category = 'not_eligible_all'
            else:
                category = 'flipped'
            
            comparisons.append({
                'patient_id': pid,
                'qdrant_collection': col_name,
                'nct_id': nct_id,
                'num_inferences': inferences_with_trial,
                'scores': scores,
                'classifications': classifications,
                'score_min': score_min,
                'score_max': score_max,
                'score_spread': score_spread,
                'all_scores_identical': all_scores_identical,
                'all_classifications_identical': all_classifications_identical,
                'category': category,
            })
    
    if not comparisons:
        st.info("No overlapping trials found across inferences. "
                "This may happen if the trial corpus changed entirely between inferences.")
        return
    
    comp_df = pd.DataFrame(comparisons)
    
    # =====================================================================
    # Summary Metrics
    # =====================================================================
    st.subheader("Summary")
    
    total_comparisons = len(comp_df)
    flip_count = (~comp_df['all_classifications_identical']).sum()
    flip_rate = flip_count / total_comparisons * 100 if total_comparisons > 0 else 0
    identical_classification = (1 - flip_count / total_comparisons) * 100 if total_comparisons > 0 else 100.0
    
    # Split into 4 mutually exclusive groups
    eligible_all = comp_df[comp_df['category'] == 'eligible_all']
    not_eligible_all = comp_df[comp_df['category'] == 'not_eligible_all']
    flipped_comps = comp_df[comp_df['category'] == 'flipped']
    
    def _group_metrics(group_df):
        n = len(group_df)
        if n == 0:
            return {'n': 0, 'identical_scores': 100.0, 'mean': 0.0, 'max': 0.0, 'std': 0.0}
        identical = group_df['all_scores_identical'].sum()
        return {
            'n': n,
            'identical_scores': identical / n * 100,
            'mean': group_df['score_spread'].mean(),
            'max': group_df['score_spread'].max(),
            'std': group_df['score_spread'].std() if n > 1 else 0.0,
        }
    
    all_m = _group_metrics(comp_df)
    elig_m = _group_metrics(eligible_all)
    flip_m = _group_metrics(flipped_comps)
    not_elig_m = _group_metrics(not_eligible_all)
    
    # === Critical Alerts ===
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Patients Re-Tested", total_patients_retested,
                  help="Patients with 2+ inferences on the same trial corpus with identical patient data")
    with col2:
        color = "normal" if identical_classification >= 98 else "inverse"
        st.metric("Identical Classification", f"{identical_classification:.1f}%",
                  delta=f"{total_comparisons - flip_count} of {total_comparisons} trial evaluations",
                  delta_color=color,
                  help="% of (patient, trial) evaluations where GPT-4o made the same eligible/not_eligible decision across ALL inferences. Measures decision-level determinism — did the verdict ever change?")
    with col3:
        color = "normal" if flip_rate < 2 else "inverse"
        st.metric("Eligibility Decision Changed", f"{flip_rate:.1f}%",
                  delta=f"{flip_count} flips out of {total_comparisons} trail evaluations",
                  delta_color="inverse" if flip_count > 0 else "off",
                  help="A flip means GPT-4o classified the same trial differently across inferences (e.g. 'eligible' in one inference, 'not_eligible' in another). Target: <2%")
    
    st.markdown("")
    
    # === All Trials ===
    st.markdown("**All Trials**")
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("Evaluations Compared", f"{all_m['n']:,}",
                  help="Total (patient, trial) groups compared across inferences. Each group has 2+ inferences. Sum of the three groups below.")
    with c2:
        color = "normal" if all_m['identical_scores'] >= 95 else "inverse"
        st.metric("Identical Match Scores", f"{all_m['identical_scores']:.1f}%",
                  delta="≥95% target" if all_m['identical_scores'] >= 95 else f"{all_m['identical_scores'] - 95:+.1f}%",
                  delta_color=color,
                  help="% of (patient, trial) groups where GPT-4o produced the exact same match score (0.0–1.0) across ALL inferences. Inflated by Not Eligible trials (always 0.0). Check Eligible row for the honest number.")
    with c3:
        st.metric("Avg Score Spread", f"{all_m['mean']:.4f}",
                  help="Mean of (max_score - min_score) per (patient, trial) group across all inferences. Lower = more deterministic. Diluted by Not Eligible trials (always 0.0 spread).")
    with c4:
        color = "normal" if all_m['max'] < 0.10 else "inverse"
        st.metric("Max Score Spread", f"{all_m['max']:.4f}",
                  delta_color=color,
                  help="Largest (max_score - min_score) observed for any single (patient, trial) group across all its inferences.")
    with c5:
        st.metric("Spread Std Dev", f"{all_m['std']:.4f}",
                  help="Standard deviation of score spreads. Low = consistently small variance. High = unpredictable — some trials are stable, others swing wildly.")
    
    # === Eligible in All Inferences ===
    st.markdown("**Eligible in All Inferences** — *pure GPT-4o scoring determinism*")
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("Evaluations Compared", f"{elig_m['n']:,}",
                  help="Trials where GPT-4o classified as 'eligible' in EVERY inference. Scores are computed from criterion-level evaluation (confirmed / total). Differences here = GPT-4o evaluated individual criteria differently across inferences.")
    with c2:
        color = "normal" if elig_m['identical_scores'] >= 90 else "inverse"
        st.metric("Identical Match Scores", f"{elig_m['identical_scores']:.1f}%",
                  delta="≥90% target" if elig_m['identical_scores'] >= 90 else f"{elig_m['identical_scores'] - 90:+.1f}%",
                  delta_color=color,
                  help="The honest reproducibility metric. % of consistently-eligible trials where GPT-4o produced the exact same score across ALL inferences. A difference means GPT-4o classified at least one criterion differently.")
    with c3:
        st.metric("Avg Score Spread", f"{elig_m['mean']:.4f}",
                  help="Mean (max - min) score for consistently-eligible trials. Since score = confirmed_criteria / total_criteria, a spread of 0.05 ≈ 1 criterion classified differently across inferences.")
    with c4:
        color = "normal" if elig_m['max'] < 0.10 else "inverse"
        st.metric("Max Score Spread", f"{elig_m['max']:.4f}",
                  delta_color=color,
                  help="Worst-case score spread among consistently-eligible trials. A value of 0.20 means GPT-4o classified ~20% of criteria differently across inferences for that trial.")
    with c5:
        st.metric("Spread Std Dev", f"{elig_m['std']:.4f}",
                  help="Standard deviation of eligible score spreads. High = GPT-4o is consistent on most trials but erratic on some.")
    
    # === Eligibility Flipped ===
    st.markdown("**Eligibility Flipped** — *GPT-4o changed the eligible/not_eligible decision across inferences*")
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("Evaluations Compared", f"{flip_m['n']:,}",
                  help="Trials where GPT-4o said 'eligible' in some inferences and 'not_eligible' in others. The most severe form of non-determinism — the decision itself changed.")
    with c2:
        st.metric("Identical Match Scores", f"{flip_m['identical_scores']:.1f}%",
                  help="Expected to be ~0%. Flipped trials almost always have different scores (eligible inferences have computed scores, not_eligible inferences have 0.0).")
    with c3:
        st.metric("Avg Score Spread", f"{flip_m['mean']:.4f}",
                  help="Mean (max - min) score for flipped trials. Typically large because eligible inferences score e.g. 0.70 while not_eligible inferences score 0.0.")
    with c4:
        st.metric("Max Score Spread", f"{flip_m['max']:.4f}",
                  help="Largest score swing from a flip. A value of 1.0 means one inference scored it perfect (1.0) and another rejected it (0.0).")
    with c5:
        st.metric("Spread Std Dev", f"{flip_m['std']:.4f}",
                  help="Spread of flip score ranges. Low = flips are consistently severe. High = some flips are near the boundary, others are extreme.")
    
    # === Not Eligible in All Inferences ===
    st.markdown("**Not Eligible in All Inferences** — *expected to be perfect (scores hardcoded to 0.0)*")
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("Evaluations Compared", f"{not_elig_m['n']:,}",
                  help="Trials where every inference classified as 'not_eligible'. Scores are hardcoded to 0.0 by the pipeline, so all metrics here should be perfect.")
    with c2:
        color = "normal" if not_elig_m['identical_scores'] >= 100 else "inverse"
        st.metric("Identical Match Scores", f"{not_elig_m['identical_scores']:.1f}%",
                  delta="100% expected" if not_elig_m['identical_scores'] >= 100 else f"{not_elig_m['identical_scores'] - 100:+.1f}%",
                  delta_color=color,
                  help="Should be 100%. Every inference returned not_eligible with score 0.0. Any deviation here indicates a pipeline bug, not GPT-4o variance.")
    with c3:
        st.metric("Avg Score Spread", f"{not_elig_m['mean']:.4f}",
                  help="Should be 0.0000. Not_eligible scores are hardcoded to 0.0 by the pipeline.")
    with c4:
        st.metric("Max Score Spread", f"{not_elig_m['max']:.4f}",
                  help="Should be 0.0000. Any non-zero value here is a bug.")
    with c5:
        st.metric("Spread Std Dev", f"{not_elig_m['std']:.4f}",
                  help="Should be 0.0000. Any non-zero value here is a bug.")
    
    st.caption(
        "**Threshold rationale:** "
        "Identical Classification >=98% and Flips <2%: LLM outputs with temperature=0 are near-deterministic but not perfectly so; "
        "2% allows for rare tokenization or batching non-determinism in the OpenAI API. "
        "All Trials Identical Scores >=95%: inflated by Not Eligible trials (hardcoded 0.0), so a lenient target suffices. "
        "Eligible Identical Scores >=90%: the honest metric; criterion-level evaluation has inherent ambiguity on borderline cases, "
        "so 90% accounts for 1-2 criteria flipping per 10-20 evaluated. "
        "Max Score Spread <0.10: a spread of 0.10 means ~1 criterion out of 10 was classified differently, acceptable for LLM variance. "
        "Score Spread histogram target 0.05: stricter visual reference; most spreads should cluster below this. "
        "Not Eligible 100%: scores are hardcoded to 0.0 by the pipeline, so any deviation is a bug, not LLM variance."
    )
    
    st.markdown("---")
    
    # =====================================================================
    # Score Reproducibility Charts
    # =====================================================================
    st.subheader("Score Reproducibility")
    
    col1, col2 = st.columns(2)
    
    with col1:
        # Scatter: min score vs max score per (patient, trial)
        fig_scatter = go.Figure()
        
        # Diagonal reference line
        fig_scatter.add_trace(go.Scatter(
            x=[0, 1], y=[0, 1],
            mode='lines',
            line=dict(color='gray', dash='dash', width=1),
            name='Perfect Agreement',
            showlegend=True
        ))
        
        # Stable points (same classification across all inferences)
        stable = comp_df[comp_df['all_classifications_identical']]
        if not stable.empty:
            fig_scatter.add_trace(go.Scatter(
                x=stable['score_min'],
                y=stable['score_max'],
                mode='markers',
                marker=dict(color='#2ca02c', size=6, opacity=0.6),
                name='Stable Classification',
                hovertemplate='Patient: %{customdata[0]}<br>Trial: %{customdata[1]}<br>Min Score: %{x:.2f}<br>Max Score: %{y:.2f}<br>Inferences: %{customdata[2]}<extra></extra>',
                customdata=stable[['patient_id', 'nct_id', 'num_inferences']].values
            ))
        
        # Flipped points (classification changed across inferences)
        flipped_chart = comp_df[~comp_df['all_classifications_identical']]
        if not flipped_chart.empty:
            fig_scatter.add_trace(go.Scatter(
                x=flipped_chart['score_min'],
                y=flipped_chart['score_max'],
                mode='markers',
                marker=dict(color='#d62728', size=10, symbol='x', line=dict(width=2)),
                name='Eligibility Flipped',
                hovertemplate='Patient: %{customdata[0]}<br>Trial: %{customdata[1]}<br>Min Score: %{x:.2f}<br>Max Score: %{y:.2f}<br>Inferences: %{customdata[2]}<extra></extra>',
                customdata=flipped_chart[['patient_id', 'nct_id', 'num_inferences']].values
            ))
        
        fig_scatter.update_layout(
            title='Min vs Max Match Score Across Inferences',
            xaxis_title='Minimum Score (across inferences)',
            yaxis_title='Maximum Score (across inferences)',
            height=450,
            margin=dict(l=20, r=20, t=40, b=20),
            template='plotly_white',
            xaxis=dict(range=[-0.05, 1.05]),
            yaxis=dict(range=[-0.05, 1.05]),
        )
        st.plotly_chart(fig_scatter, use_container_width=True)
    
    with col2:
        # Score spread distribution
        fig_hist = px.histogram(
            comp_df, x='score_spread', nbins=30,
            labels={'score_spread': 'Score Spread (max - min)'},
            template='plotly_white',
            title='Score Spread Distribution'
        )
        fig_hist.add_vline(
            x=0.05, line_dash="dash", line_color="red",
            annotation_text="0.05 target", annotation_position="top right"
        )
        fig_hist.update_traces(marker_color='#1f77b4')
        fig_hist.update_layout(
            height=450,
            margin=dict(l=20, r=20, t=40, b=20),
            showlegend=False
        )
        st.plotly_chart(fig_hist, use_container_width=True)
    
    
    st.caption(
        "Left: each point is one (patient, trial) pair. Points on the diagonal have identical scores across inferences. "
        "Red ✕ markers indicate the eligibility decision itself flipped. "
        "Right: distribution of score spreads. Most should fall below the 0.05 target line."
    )
    
    st.markdown("---")
    
    # =====================================================================
    # Eligibility Flips Detail
    # =====================================================================
    if flip_count > 0:
        st.subheader(f"Eligibility Flips ({flip_count})")
        
        # Build flip detail from flipped_comps
        # For each flipped (patient, trial), show how many inferences said eligible vs not_eligible
        flip_rows = []
        for _, row in flipped_comps.iterrows():
            classifications = row['classifications']
            scores = row['scores']
            n_eligible = sum(1 for c in classifications if c == 'eligible')
            n_not_eligible = sum(1 for c in classifications if c == 'not_eligible')
            eligible_scores = [s for s, c in zip(scores, classifications) if c == 'eligible']
            
            flip_rows.append({
                'patient_id': row['patient_id'],
                'nct_id': row['nct_id'],
                'num_inferences': row['num_inferences'],
                'n_eligible': n_eligible,
                'n_not_eligible': n_not_eligible,
                'eligible_scores': eligible_scores,
                'score_spread': row['score_spread'],
            })
        
        flip_detail_df = pd.DataFrame(flip_rows)
        
        # Direction summary: majority eligible or majority not_eligible
        majority_eligible = len(flip_detail_df[flip_detail_df['n_eligible'] > flip_detail_df['n_not_eligible']])
        majority_not_eligible = len(flip_detail_df[flip_detail_df['n_not_eligible'] > flip_detail_df['n_eligible']])
        evenly_split = len(flip_detail_df[flip_detail_df['n_eligible'] == flip_detail_df['n_not_eligible']])
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Majority Eligible", majority_eligible,
                      delta=f"{majority_eligible/flip_count*100:.0f}% of flips" if flip_count > 0 else "",
                      delta_color="off",
                      help="Trials where MORE inferences said 'eligible' than 'not_eligible'. "
                           "The not_eligible inferences are likely GPT-4o errors — it found a false disqualifier.")
        with col2:
            st.metric("Majority Not Eligible", majority_not_eligible,
                      delta=f"{majority_not_eligible/flip_count*100:.0f}% of flips" if flip_count > 0 else "",
                      delta_color="off",
                      help="Trials where MORE inferences said 'not_eligible' than 'eligible'. "
                           "The eligible inferences are likely GPT-4o errors — it missed a disqualifier.")
        with col3:
            st.metric("Evenly Split", evenly_split,
                      delta=f"{evenly_split/flip_count*100:.0f}% of flips" if flip_count > 0 else "",
                      delta_color="off",
                      help="Trials where equal numbers of inferences said 'eligible' and 'not_eligible'. "
                           "Maximum uncertainty — GPT-4o is genuinely inconsistent on these cases.")
        
        st.markdown("")
        
        # Detail table
        flip_display = flip_detail_df.copy()
        flip_display['Eligible Score(s)'] = flip_display['eligible_scores'].apply(
            lambda s: f"{min(s)*100:.0f}%–{max(s)*100:.0f}%" if len(s) > 1 else (f"{s[0]*100:.0f}%" if len(s) == 1 else "—")
        )
        
        flip_display['Classification'] = flip_display.apply(
            lambda r: f"{r['n_eligible']} eligible / {r['n_not_eligible']} not eligible", axis=1
        )
        
        flip_display = flip_display.rename(columns={
            'patient_id': 'Patient ID',
            'nct_id': 'NCT ID',
            'num_inferences': 'Inferences',
        })

        flip_display = flip_display.sort_values('score_spread', ascending=False)
        
        flip_display = flip_display[['Patient ID', 'NCT ID', 'Inferences', 'Classification', 'Eligible Score(s)']]
        flip_display = flip_display.reset_index(drop=True)
        flip_display.index = flip_display.index + 1
        flip_display.index.name = "Row"
        
        st.dataframe(flip_display, use_container_width=True, hide_index=False)
        
        st.caption("Each row is a (patient, trial) where GPT-4o changed the eligibility decision across inferences. "
                   "'Classification' shows how many inferences said eligible vs not_eligible. "
                   "'Eligible Score Range' shows the min–max match score among the eligible inferences only. "
                   "Investigate criterion-level differences for these cases.")
    else:
        st.success("✓ No eligibility flips detected. Pipeline decisions are fully reproducible across all inferences.")
    
    st.markdown("---")
    
    
    # =====================================================================
    # Decision Flip Deep Dive
    # =====================================================================
    # Handles arbitrary N runs per patient (1, 2, 3, ... inferences).
    # Flip type is derived from the SET of (decision, score_tier) observed
    # across all runs, not from pairwise Run1→Run2 direction.
    # =====================================================================
    
    if flip_count > 0:
        st.subheader("🔬 Decision Flip Deep Dive")
        st.caption(
            "Detailed analysis of every eligibility flip: what type of flip occurred, "
            "which criteria GPT-4o evaluated differently across runs, and aggregate patterns. "
            "Supports any number of runs per patient."
        )
        
        # Shared status display mapping for criterion-level diffs
        _STATUS_DISPLAY_BASE_FLIP = {
            "met": "✅ Met",
            "not_met": "❌ Not Met",
            "violated": "❌ Violated",
            "not_violated": "✅ Not Violated",
        }

        def status_display_map(status: str, patient_value: str = "") -> str:
            pv = (patient_value or "").strip()
            if pv.lower().startswith("not applicable"):
                return "➖ Not Applicable"
            if status == "not_evaluable":
                if pv and pv.lower() != "not in patient record":
                    return "🔍 Unverifiable"
                return "⚠️ Missing Data"
            return _STATUS_DISPLAY_BASE_FLIP.get(status, status)
        
        # -----------------------------------------------------------------
        # 1. Flip Type Classification
        # -----------------------------------------------------------------
        
        def classify_flip_type(classifications, scores):
            """Classify the flip scenario from N runs."""
            tiers_seen = set()
            for cls, score in zip(classifications, scores):
                if cls == 'not_eligible':
                    tiers_seen.add('Rejected')
                elif score >= 1.0:
                    tiers_seen.add('Full Match')
                elif score > 0.0:
                    tiers_seen.add('Partial Match')
                else:
                    tiers_seen.add('Zero Score')
            
            if 'Rejected' in tiers_seen and 'Full Match' in tiers_seen:
                return 'Rejection ↔ Full Match'
            elif 'Rejected' in tiers_seen and 'Partial Match' in tiers_seen:
                return 'Rejection ↔ Partial Match'
            elif 'Rejected' in tiers_seen and 'Zero Score' in tiers_seen:
                return 'Rejection ↔ Zero Score'
            elif 'Full Match' in tiers_seen and 'Partial Match' in tiers_seen:
                return 'Full Match ↔ Partial Match'
            else:
                return 'Other'
        
        flip_type_severity = {
            'Rejection ↔ Full Match': 4,
            'Rejection ↔ Partial Match': 3,
            'Rejection ↔ Zero Score': 2,
            'Full Match ↔ Partial Match': 1,
            'Other': 0,
        }
        
        flip_type_colors = {
            'Rejection ↔ Full Match': '#d62728',
            'Rejection ↔ Partial Match': '#ff7f0e',
            'Rejection ↔ Zero Score': '#9467bd',
            'Full Match ↔ Partial Match': '#2ca02c',
            'Other': '#7f7f7f',
        }
        
        flip_types = []
        for _, row in flipped_comps.iterrows():
            ft = classify_flip_type(row['classifications'], row['scores'])
            flip_types.append(ft)
        
        flipped_comps_enriched = flipped_comps.copy()
        flipped_comps_enriched['flip_type'] = flip_types
        
        # --- Flip Type Breakdown Chart ---
        st.markdown("#### Flip Type Breakdown")
        
        type_counts = flipped_comps_enriched['flip_type'].value_counts()
        sorted_types = sorted(type_counts.index, key=lambda t: flip_type_severity.get(t, 0), reverse=True)
        
        fig_flip_types = go.Figure()
        fig_flip_types.add_trace(go.Bar(
            x=[type_counts[t] for t in sorted_types],
            y=sorted_types,
            orientation='h',
            marker_color=[flip_type_colors.get(t, '#7f7f7f') for t in sorted_types],
            text=[f"{type_counts[t]} ({type_counts[t]/flip_count*100:.0f}%)" for t in sorted_types],
            textposition='auto',
        ))
        fig_flip_types.update_layout(
            height=max(200, len(sorted_types) * 60),
            margin=dict(l=20, r=20, t=10, b=20),
            template='plotly_white',
            xaxis_title='Number of Flips',
            yaxis=dict(autorange='reversed'),
            showlegend=False,
        )
        st.plotly_chart(fig_flip_types, use_container_width=True)
        
        st.caption(
            "**Rejection ↔ Full Match** (red): Most severe — trial went from all-criteria-confirmed to rejected or vice versa. "
            "**Rejection ↔ Partial Match** (orange): Trial crossed the eligibility boundary with a partial score. "
            "**Rejection ↔ Zero Score** (purple): Edge case — eligible with 0.0 score vs rejected. "
            "**Full Match ↔ Partial Match** (green): Score changed within eligible — least severe, no eligibility boundary crossing."
        )
        
        st.markdown("")
        
        # -----------------------------------------------------------------
        # 2. Failure Mode Analysis
        # -----------------------------------------------------------------
        # Categorizes each flip by ROOT CAUSE, not just severity.
        # Uses rejection explanations to identify why GPT-4o flipped.
        # -----------------------------------------------------------------
        
        st.markdown("#### Failure Mode Analysis")
        st.caption(
            "Categorizes each flip by root cause using the rejection explanation. "
            "A flip can belong to multiple categories. Categories are ordered by "
            "actionability — top categories have concrete pipeline fixes."
        )
        
        failure_categories = {
            'Temporal / Resolved Status': {
                'keywords': [
                    'resolved', 'no current', 'not current', 'no active',
                    'historical', 'long-standing', 'years ago',
                    'requires active', 'requires current', 'requires patient undergoing',
                ],
                'color': '#d62728',
                'fix': 'Present-tense rule (prompt)',
            },
            'Missing Data as Disqualifier': {
                'keywords': [
                    'not confirmed', 'no data on', 'lacks evidence',
                    'does not indicate', 'not in record',
                    'has no such', 'status not confirmed',
                    'does not confirm', 'no record of',
                    'lacks this information', 'no evidence of',
                    'but no evidence of', 'record lacks',
                ],
                'color': '#ff7f0e',
                'fix': 'Reinforce Rule 1 (prompt)',
            },
            'Disease Stage / Extent': {
                'keywords': [
                    'metastatic', 'non-metastatic', 'oligometastatic',
                    'oligoprogressive', 'recurrent', 'locally advanced',
                    'requires metastatic', 'requires recurrent',
                    'primary small cell', 'recurrent small cell',
                    'not metastatic',
                ],
                'color': '#e45756',
                'fix': 'Stage/extent mismatch — clinical ambiguity',
            },
            'Procedure Ambiguity': {
                'keywords': [
                    'prior lumpectomy', 'prior breast surgery',
                    'ipsilateral breast surgery', 'prior ipsilateral',
                    'previous resection', 'polypectomy',
                    'partial resection', 'colectomy',
                    'excision of lesion',
                    'previous breast surgery', 'history of lumpectomy',
                    'axillary lymph node excision', 'axillary lymph node',
                ],
                'color': '#9467bd',
                'fix': 'Irreducible — clinical ambiguity',
            },
            'Biomarker / Subtype': {
                'keywords': [
                    'her2', 'triple-negative', 'triple negative',
                    'er+', 'er-', 'hr+', 'hr-',
                    'hormone receptor', 'hormone-receptor',
                    'pik3ca', 'ccne1', 'brca',
                    'biomarker', 'mutation', 'amplification',
                    'ductal carcinoma in situ', 'mammaprint',
                ],
                'color': '#2ca02c',
                'fix': 'Receptor/biomarker tagging (pipeline)',
            },
            'Medical Concept Error': {
                'keywords': [
                    'anemia', 'hypertension', 'diabetes',
                    'inflammatory colonic conditions (anemia',
                    'inflammatory colonic conditions',
                    'not a malignancy', 'benign',
                    'seizures', 'seizure',
                ],
                'color': '#e377c2',
                'fix': 'Neoplasm tagging (pipeline)',
            },
            'Terminology Mismatch': {
                'keywords': [
                    'adenocarcinoma', 'without specified',
                    'malignant neoplasm of colon',
                    'does not have recurrent pelvic',
                ],
                'color': '#17becf',
                'fix': 'Terminology matching rule (prompt Step 3)',
            },
            'Lab / Age Threshold': {
                'keywords': [
                    'creatinine', 'gfr', 'hemoglobin', 'bilirubin',
                    'platelet', 'neutrophil', 'albumin',
                    'organ function', 'renal function',
                    'exceeds the age', 'age limit', 'older than',
                ],
                'color': '#bcbd22',
                'fix': 'Legitimate catch — no fix needed',
            },
            'Treatment Requirement': {
                'keywords': [
                    'prior chemotherapy', 'received chemotherapy',
                    'received prior', 'recent chemotherapy',
                    'neoadjuvant', 'preoperative',
                    'progression on', 'endocrine therapy',
                    'completed all', 'ongoing chemotherapy',
                    'prior therapy for', 'cisplatin', 'paclitaxel',
                    'received cisplatin', 'prior therapy',
                    'no standard protocol', 'multiline standard therapy',
                ],
                'color': '#7f7f7f',
                'fix': 'Mixed — some temporal, some missing data',
            },
        }
        
        # Categorize each flip
        flip_failure_modes = []
        for _, row in flipped_comps_enriched.iterrows():
            pid = row['patient_id']
            nct = row['nct_id']
            
            # Get rejection explanation for this (patient, trial)
            patient_inf_ids_local = []
            for pg in patient_groups:
                if pg['patient_id'] == pid:
                    patient_inf_ids_local = pg['inference_ids']
                    break
            
            rej_matches = relevant_matches[
                (relevant_matches['nct_id'] == nct) &
                (relevant_matches['eligible'] == 'not_eligible') &
                (relevant_matches['inference_id'].isin(patient_inf_ids_local))
            ]
            
            explanation = ''
            if not rej_matches.empty:
                explanation = str(rej_matches.iloc[0].get('explanation', ''))
            
            explanation_lower = explanation.lower()
            matched_modes = []
            
            for mode_name, mode_info in failure_categories.items():
                for kw in mode_info['keywords']:
                    if kw.lower() in explanation_lower:
                        matched_modes.append(mode_name)
                        break
            
            if not matched_modes:
                matched_modes = ['Other']
            
            flip_failure_modes.append({
                'patient_id': pid,
                'nct_id': nct,
                'flip_type': row['flip_type'],
                'explanation': explanation,
                'failure_modes': matched_modes,
            })
        
        failure_df = pd.DataFrame(flip_failure_modes)
        
        # Count failure modes
        all_modes = []
        for modes in failure_df['failure_modes']:
            all_modes.extend(modes)
        mode_counts = Counter(all_modes)
        
        # Display as horizontal bar chart
        sorted_modes = []
        for mode_name in failure_categories.keys():
            if mode_name in mode_counts:
                sorted_modes.append(mode_name)
        if 'Other' in mode_counts:
            sorted_modes.append('Other')
        
        mode_colors = {name: info['color'] for name, info in failure_categories.items()}
        mode_colors['Other'] = '#aaaaaa'
        
        mode_fixes = {name: info['fix'] for name, info in failure_categories.items()}
        mode_fixes['Other'] = 'Review manually'
        
        fig_modes = go.Figure()
        fig_modes.add_trace(go.Bar(
            x=[mode_counts.get(m, 0) for m in sorted_modes],
            y=[f"{m} ({mode_counts.get(m, 0)})" for m in sorted_modes],
            orientation='h',
            marker_color=[mode_colors.get(m, '#aaaaaa') for m in sorted_modes],
            text=[f"{mode_counts.get(m, 0)/len(flipped_comps)*100:.0f}%" for m in sorted_modes],
            textposition='auto',
        ))
        fig_modes.update_layout(
            height=max(250, len(sorted_modes) * 45),
            margin=dict(l=20, r=20, t=10, b=20),
            template='plotly_white',
            xaxis_title='Number of Flips (a flip can appear in multiple categories)',
            yaxis=dict(autorange='reversed'),
            showlegend=False,
        )
        st.plotly_chart(fig_modes, use_container_width=True)
        
        # Recommended fixes table
        fix_rows = []
        for mode_name in sorted_modes:
            count = mode_counts.get(mode_name, 0)
            fix_rows.append({
                'Failure Mode': mode_name,
                'Flips': count,
                '% of Total': f"{count/len(flipped_comps)*100:.0f}%",
                'Recommended Fix': mode_fixes.get(mode_name, 'Review manually'),
            })
        
        fix_df = pd.DataFrame(fix_rows)
        st.dataframe(fix_df, use_container_width=True, hide_index=True)
        
        st.caption(
            "Categories are based on keyword analysis of rejection explanations. "
            "A flip can belong to multiple categories (e.g., a temporal issue involving treatment history). "
            "'Recommended Fix' indicates whether the issue is addressable via prompt engineering, "
            "pipeline enrichment, or is irreducible clinical ambiguity."
        )
        
        # Show 'Other' flips if any
        other_flips = failure_df[failure_df['failure_modes'].apply(lambda x: 'Other' in x)]
        if not other_flips.empty:
            with st.expander(f"'Other' flips ({len(other_flips)}) — uncategorized rejection reasons"):
                for i, (_, row) in enumerate(other_flips.iterrows(), 1):
                    st.markdown(f"**{i}.** [{row['nct_id']}] {row['explanation']}")
        
        st.markdown("")
        
        # -----------------------------------------------------------------
        # 3. Criterion-Level Diff
        # -----------------------------------------------------------------
        
        st.markdown("#### Criterion-Level Diff")
        st.caption(
            "Select a flipped (patient, trial) pair to see side-by-side criterion classifications "
            "across all runs. Criteria with differing statuses are highlighted. "
            "Rejected runs have no criterion details (GPT-4o stops evaluation after the first disqualifier)."
        )
        
        flip_options = []
        for idx, row in flipped_comps_enriched.iterrows():
            n_elig = sum(1 for c in row['classifications'] if c == 'eligible')
            n_rej = sum(1 for c in row['classifications'] if c == 'not_eligible')
            
            label = (
                f"{row['patient_id']} | {row['nct_id']} | "
                f"{row['flip_type']} | {n_elig}E/{n_rej}R"
            )
            
            flip_options.append((label, row))
        
        if flip_options:
            selected_idx = st.selectbox(
                "Select a flipped evaluation to inspect",
                range(len(flip_options)),
                format_func=lambda i: flip_options[i][0],
                key="flip_deep_dive_selector"
            )
            
            selected_flip = flip_options[selected_idx][1]
            sel_pid = selected_flip['patient_id']
            sel_nct = selected_flip['nct_id']
            sel_col = selected_flip['qdrant_collection']
            
            patient_inf_ids = []
            for pg in patient_groups:
                if pg['patient_id'] == sel_pid and pg['qdrant_collection'] == sel_col:
                    patient_inf_ids = pg['inference_ids']
                    break
            
            flip_matches = relevant_matches[
                (relevant_matches['inference_id'].isin(patient_inf_ids)) &
                (relevant_matches['nct_id'] == sel_nct)
            ].sort_values('inference_id')
            
            if not flip_matches.empty:
                run_summary_rows = []
                for run_idx, (_, tm_row) in enumerate(flip_matches.iterrows(), 1):
                    run_summary_rows.append({
                        'Run': f"Run {run_idx}",
                        'Inference ID': tm_row['inference_id'],
                        'Decision': tm_row['eligible'],
                        'Score': f"{tm_row['match_score']:.2f}" if tm_row['eligible'] == 'eligible' else "0.00 (rejected)",
                    })
                
                run_summary_df = pd.DataFrame(run_summary_rows)
                
                # Show patient clinical data for context
                patient_row = grouped[grouped['patient_id'] == sel_pid].iloc[0]
                
                with st.expander("📋 Patient Record", expanded=False):
                    
                    p_col1, p_col2, p_col3, p_col4 = st.columns(4)
                    with p_col1:
                        st.markdown(f"**Age:** {patient_row.get('age', 'N/A')}")
                    with p_col2:
                        st.markdown(f"**Sex:** {patient_row.get('sex', 'N/A')}")
                    with p_col3:
                        st.markdown(f"**Conditions:** {patient_row.get('condition_count', 'N/A')}")
                    with p_col4:
                        st.markdown(f"**Medications:** {patient_row.get('medication_count', 'N/A')}")
                    
                    st.markdown(f"**Primary Condition:** {patient_row.get('primary_condition', 'N/A')}")
                    
                    # Extract patient record from GPT-4o prompt if available
                    prompt = patient_row.get('gpt4o_prompt', '')
                    if prompt and str(prompt) != 'nan':
                        # The patient record is between [USER] and CLINICAL TRIALS:
                        user_start = str(prompt).find('PATIENT RECORD:')
                        trials_start = str(prompt).find('CLINICAL TRIALS:')
                        if user_start != -1 and trials_start != -1:
                            patient_text = str(prompt)[user_start:trials_start].strip()
                            st.text(patient_text)
                        else:
                            st.text("Full patient record not available in prompt format.")
                    else:
                        st.text("No prompt data stored for this inference.")
                
                
                st.markdown(f"**Run Summary: {sel_nct}** — {len(flip_matches)} runs")
                
                st.dataframe(run_summary_df, use_container_width=True, hide_index=True)
                
                run_criteria = {}
                run_explanations = {}
                
                for run_idx, (_, tm_row) in enumerate(flip_matches.iterrows(), 1):
                    criteria_list = []
                    explanation = tm_row.get('explanation', '')
                    run_explanations[run_idx] = explanation
                    
                    if pd.notna(tm_row.get('criterion_details')) and tm_row['criterion_details']:
                        try:
                            parsed = json.loads(tm_row['criterion_details'])
                            for c in parsed.get('inclusion', []):
                                criteria_list.append({
                                    'type': 'Inclusion',
                                    'criterion': c.get('criterion', ''),
                                    'status': c.get('status', ''),
                                    'patient_value': c.get('patient_value', ''),
                                })
                            for c in parsed.get('exclusion', []):
                                criteria_list.append({
                                    'type': 'Exclusion',
                                    'criterion': c.get('criterion', ''),
                                    'status': c.get('status', ''),
                                    'patient_value': c.get('patient_value', ''),
                                })
                        except (json.JSONDecodeError, TypeError, AttributeError):
                            pass
                    
                    run_criteria[run_idx] = criteria_list
                
                def normalize_criterion(text):
                    return ' '.join(text.lower().strip().split())
                
                all_criteria_keys = []
                seen_keys = set()
                
                for run_idx in sorted(run_criteria.keys()):
                    for c in run_criteria[run_idx]:
                        norm_key = (c['type'], normalize_criterion(c['criterion']))
                        if norm_key not in seen_keys:
                            seen_keys.add(norm_key)
                            all_criteria_keys.append((c['type'], normalize_criterion(c['criterion']), c['criterion']))
                
                diff_rows = []
                for crit_type, norm_crit, display_crit in all_criteria_keys:
                    row_data = {
                        'Type': crit_type,
                        'Criterion': display_crit,
                    }
                    
                    statuses_across_runs = []
                    values_across_runs = []
                    inf_id_list = list(flip_matches['inference_id'])
                    
                    for run_idx in sorted(run_criteria.keys()):
                        found = None
                        for c in run_criteria[run_idx]:
                            if c['type'] == crit_type and normalize_criterion(c['criterion']) == norm_crit:
                                found = c
                                break
                        
                        if found:
                            pval = found.get('patient_value', '')
                            status_str = status_display_map(found['status'], pval)
                            row_data[f'Run {run_idx}'] = f"{status_str}  ·  {pval}" if pval else status_str
                            statuses_across_runs.append(found['status'])
                            values_across_runs.append(pval)
                            
                        else:
                            actual_inf_id = inf_id_list[run_idx - 1] if run_idx - 1 < len(inf_id_list) else None
                            decision_rows = flip_matches[flip_matches['inference_id'] == actual_inf_id]['eligible'].values if actual_inf_id else []
                            if len(decision_rows) > 0 and decision_rows[0] == 'not_eligible':
                                row_data[f'Run {run_idx}'] = '🚫 Not Evaluated (Rejected)'
                                statuses_across_runs.append('_rejected_')
                                values_across_runs.append('')
                            else:
                                row_data[f'Run {run_idx}'] = '—'
                                statuses_across_runs.append('_missing_')
                                values_across_runs.append('')
                    
                    unique_statuses = set(statuses_across_runs)
                    row_data['Changed'] = '⚡' if len(unique_statuses) > 1 else ''
                    
                    diff_rows.append(row_data)
                
                if diff_rows:
                    diff_df = pd.DataFrame(diff_rows)
                    
                    n_changed = sum(1 for r in diff_rows if r['Changed'] == '⚡')
                    n_total = len(diff_rows)
                    
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Total Criteria", n_total)
                    with col2:
                        st.metric("Changed Across Runs", n_changed,
                                  delta=f"{n_changed/n_total*100:.0f}%" if n_total > 0 else "",
                                  delta_color="inverse" if n_changed > 0 else "off")
                    with col3:
                        st.metric("Consistent", n_total - n_changed)
                    
                    run_cols = sorted([c for c in diff_df.columns if c.startswith('Run ')])
                    ordered_cols = ['Changed', 'Type', 'Criterion'] + run_cols
                    ordered_cols = [c for c in ordered_cols if c in diff_df.columns]
                    diff_df = diff_df[ordered_cols]
                    
                    diff_df = diff_df.sort_values('Changed', ascending=False)
                    
                    with st.expander("💬 GPT-4o Explanations (per run)", expanded=True):
                        for run_idx in sorted(run_explanations.keys()):
                            decision = run_summary_rows[run_idx - 1]['Decision']
                            emoji = '✅' if decision == 'eligible' else '❌'
                            st.markdown(f"**Run {run_idx}** ({emoji} {decision}):")
                            explanation = run_explanations.get(run_idx, '')
                            if explanation and str(explanation) != 'nan':
                                st.text(str(explanation))
                            else:
                                st.text("(No explanation recorded)")
                            st.markdown("")
                    
                    st.markdown("**Criterion-Level Comparison**")
                    
                    # Build column config with wide Run columns to show status + patient value
                    col_config = {
                        'Changed': st.column_config.Column(width='small'),
                        'Type': st.column_config.Column(width='small'),
                        'Criterion': st.column_config.Column(width='large'),
                    }
                    for rc in run_cols:
                        col_config[rc] = st.column_config.Column(width='large')
                    
                    st.dataframe(
                        diff_df,
                        use_container_width=True,
                        hide_index=True,
                        height=min(600, 35 * (len(diff_df) + 1)),
                        column_config=col_config,
                    )
                    
                    st.caption(
                        "⚡ = criterion status differed across runs. "
                        "Each Run column shows: Status · Patient Value extracted by GPT-4o. "
                        "'🚫 Not Evaluated (Rejected)' = GPT-4o stopped evaluating criteria after finding a disqualifier in that run. "
                        "Criteria are aligned by normalized text across all runs."
                    )
                
                else:
                    st.info("No criterion details available for this flip. "
                            "This can happen if criterion_details were not stored in the database.")
        
        st.markdown("")
        
        # -----------------------------------------------------------------
        # 4. Flip Pattern Analysis
        # -----------------------------------------------------------------
        
        st.markdown("#### Flip Pattern Analysis")
        
        flipped_patients = flipped_comps_enriched[['patient_id', 'nct_id', 'flip_type']].copy()
        
        patient_conditions = grouped[['patient_id', 'primary_condition']].drop_duplicates('patient_id')
        flipped_patients = flipped_patients.merge(patient_conditions, on='patient_id', how='left')
        
        trial_phases = relevant_matches[['nct_id', 'trial_phase']].drop_duplicates('nct_id')
        flipped_patients = flipped_patients.merge(trial_phases, on='nct_id', how='left')
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("**Flips by Cancer Type**")
            if 'primary_condition' in flipped_patients.columns:
                condition_counts = flipped_patients['primary_condition'].value_counts().head(10)
                if not condition_counts.empty:
                    fig_cond = go.Figure()
                    fig_cond.add_trace(go.Bar(
                        y=condition_counts.index,
                        x=condition_counts.values,
                        orientation='h',
                        marker_color='#ff7f0e',
                        text=condition_counts.values,
                        textposition='auto',
                    ))
                    fig_cond.update_layout(
                        height=max(250, len(condition_counts) * 40),
                        margin=dict(l=20, r=20, t=10, b=20),
                        template='plotly_white',
                        xaxis_title='Number of Flips',
                        yaxis=dict(autorange='reversed'),
                    )
                    st.plotly_chart(fig_cond, use_container_width=True)
                else:
                    st.info("No condition data available.")
            else:
                st.info("No condition data available.")
        
        with col2:
            st.markdown("**Flips by Trial Phase**")
            if 'trial_phase' in flipped_patients.columns:
                phase_counts = flipped_patients['trial_phase'].fillna('Not Specified').value_counts()
                if not phase_counts.empty:
                    fig_phase = go.Figure()
                    fig_phase.add_trace(go.Bar(
                        y=phase_counts.index,
                        x=phase_counts.values,
                        orientation='h',
                        marker_color='#9467bd',
                        text=phase_counts.values,
                        textposition='auto',
                    ))
                    fig_phase.update_layout(
                        height=max(250, len(phase_counts) * 40),
                        margin=dict(l=20, r=20, t=10, b=20),
                        template='plotly_white',
                        xaxis_title='Number of Flips',
                        yaxis=dict(autorange='reversed'),
                    )
                    st.plotly_chart(fig_phase, use_container_width=True)
                else:
                    st.info("No trial phase data available.")
            else:
                st.info("No trial phase data available.")
        
        st.markdown("**Most Frequently Flipped Trials**")
        trial_flip_counts = flipped_comps_enriched['nct_id'].value_counts().head(10)
        if not trial_flip_counts.empty:
            trial_flip_df = trial_flip_counts.reset_index()
            trial_flip_df.columns = ['NCT ID', 'Flip Count']
            
            if 'trial_title' in relevant_matches.columns:
                trial_titles = relevant_matches[['nct_id', 'trial_title']].drop_duplicates('nct_id')
                trial_flip_df = trial_flip_df.merge(trial_titles, left_on='NCT ID', right_on='nct_id', how='left')
                trial_flip_df = trial_flip_df.drop(columns='nct_id', errors='ignore')
                trial_flip_df = trial_flip_df.rename(columns={'trial_title': 'Trial Title'})
            
            # Add criteria counts (inclusion, exclusion, total) from criterion_details
            criteria_counts = []
            for nct in trial_flip_df['NCT ID']:
                nct_matches = relevant_matches[
                    (relevant_matches['nct_id'] == nct) &
                    (relevant_matches['criterion_details'].notna()) &
                    (relevant_matches['criterion_details'] != '')
                ]
                n_inc, n_exc = 0, 0
                if not nct_matches.empty:
                    # Use the first available criterion_details for this trial
                    for _, row in nct_matches.iterrows():
                        try:
                            parsed = json.loads(row['criterion_details'])
                            n_inc = len(parsed.get('inclusion', []))
                            n_exc = len(parsed.get('exclusion', []))
                            if n_inc > 0 or n_exc > 0:
                                break  # Found valid criteria, stop
                        except (json.JSONDecodeError, TypeError, AttributeError):
                            continue
                criteria_counts.append({'NCT ID': nct, 'Inclusion': n_inc, 'Exclusion': n_exc, 'Total Criteria': n_inc + n_exc})
            
            criteria_df = pd.DataFrame(criteria_counts)
            trial_flip_df = trial_flip_df.merge(criteria_df, on='NCT ID', how='left')
            
            # Calculate flip rate per criterion
            trial_flip_df['Flip/Criterion'] = trial_flip_df.apply(
                lambda r: f"{r['Flip Count'] / r['Total Criteria']:.2f}" if r['Total Criteria'] > 0 else '—', axis=1
            )
            
            # Order columns
            col_order = ['NCT ID']
            if 'Trial Title' in trial_flip_df.columns:
                col_order.append('Trial Title')
            col_order += ['Flip Count', 'Inclusion', 'Exclusion', 'Total Criteria', 'Flip/Criterion']
            col_order = [c for c in col_order if c in trial_flip_df.columns]
            trial_flip_df = trial_flip_df[col_order]
            
            st.dataframe(trial_flip_df, use_container_width=True, hide_index=True)
        
        st.caption(
            "Trials that flip frequently across multiple patients may have borderline "
            "criteria that GPT-4o evaluates inconsistently. "
            "'Flip/Criterion' = flip count normalized by total criteria — higher values suggest "
            "the trial's criteria are inherently ambiguous rather than just numerous. "
            "Consider reviewing these trials' eligibility criteria for ambiguous language."
        )
        
        st.markdown("")
        
        # -----------------------------------------------------------------
        # 5. Score Drift Within Eligible Trials
        # -----------------------------------------------------------------
        
        st.markdown("#### Score Drift Within Eligible Trials")
        st.caption(
            "Trials classified as 'eligible' in ALL runs but with different match scores. "
            "These represent criterion-level evaluation differences that did not cross "
            "the eligibility boundary — less severe than flips, but still indicating "
            "GPT-4o non-determinism."
        )
        
        score_drifts = eligible_all[~eligible_all['all_scores_identical']].copy()
        
        if not score_drifts.empty:
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Trials with Score Drift", len(score_drifts),
                          delta=f"{len(score_drifts)/len(eligible_all)*100:.1f}% of eligible-in-all" if len(eligible_all) > 0 else "",
                          delta_color="off")
            with col2:
                st.metric("Avg Score Drift", f"{score_drifts['score_spread'].mean():.4f}")
            with col3:
                st.metric("Max Score Drift", f"{score_drifts['score_spread'].max():.4f}")
            
            drift_rows = []
            for _, row in score_drifts.iterrows():
                scores_str = ', '.join(f"{s:.2f}" for s in row['scores'])
                drift_rows.append({
                    'Patient ID': row['patient_id'],
                    'NCT ID': row['nct_id'],
                    'Scores (per run)': scores_str,
                    'Spread': f"{row['score_spread']:.4f}",
                    'Runs': row['num_inferences'],
                })
            
            drift_df = pd.DataFrame(drift_rows)
            drift_df = drift_df.sort_values('Spread', ascending=False)
            
            st.dataframe(
                drift_df,
                use_container_width=True,
                hide_index=True,
                height=min(400, 35 * (len(drift_df) + 1)),
            )
            
            st.markdown("**Inspect Score Drift Criteria**")
            
            drift_options = []
            for _, row in score_drifts.iterrows():
                scores_str = ', '.join(f"{s:.2f}" for s in row['scores'])
                
                label = f"{row['patient_id']} | {row['nct_id']} | scores: {scores_str}"
                
                drift_options.append((label, row))
            
            if drift_options:
                selected_drift_idx = st.selectbox(
                    "Select a score-drift evaluation to inspect",
                    range(len(drift_options)),
                    format_func=lambda i: drift_options[i][0],
                    key="drift_deep_dive_selector"
                )
                
                selected_drift = drift_options[selected_drift_idx][1]
                drift_pid = selected_drift['patient_id']
                drift_nct = selected_drift['nct_id']
                drift_col_name = selected_drift['qdrant_collection']
                
                drift_inf_ids = []
                for pg in patient_groups:
                    if pg['patient_id'] == drift_pid and pg['qdrant_collection'] == drift_col_name:
                        drift_inf_ids = pg['inference_ids']
                        break
                
                drift_matches = relevant_matches[
                    (relevant_matches['inference_id'].isin(drift_inf_ids)) &
                    (relevant_matches['nct_id'] == drift_nct)
                ].sort_values('inference_id')
                
                if not drift_matches.empty:
                    drift_run_criteria = {}
                    for run_idx, (_, tm_row) in enumerate(drift_matches.iterrows(), 1):
                        criteria_list = []
                        if pd.notna(tm_row.get('criterion_details')) and tm_row['criterion_details']:
                            try:
                                parsed = json.loads(tm_row['criterion_details'])
                                for c in parsed.get('inclusion', []):
                                    criteria_list.append({
                                        'type': 'Inclusion',
                                        'criterion': c.get('criterion', ''),
                                        'status': c.get('status', ''),
                                        'patient_value': c.get('patient_value', ''),
                                    })
                                for c in parsed.get('exclusion', []):
                                    criteria_list.append({
                                        'type': 'Exclusion',
                                        'criterion': c.get('criterion', ''),
                                        'status': c.get('status', ''),
                                        'patient_value': c.get('patient_value', ''),
                                    })
                            except (json.JSONDecodeError, TypeError, AttributeError):
                                pass
                        drift_run_criteria[run_idx] = criteria_list
                    
                    def normalize_criterion_text(text):
                        return ' '.join(text.lower().strip().split())
                    
                    drift_all_keys = []
                    drift_seen = set()
                    for ri in sorted(drift_run_criteria.keys()):
                        for c in drift_run_criteria[ri]:
                            nk = (c['type'], normalize_criterion_text(c['criterion']))
                            if nk not in drift_seen:
                                drift_seen.add(nk)
                                drift_all_keys.append((c['type'], normalize_criterion_text(c['criterion']), c['criterion']))
                    
                    drift_diff_rows = []
                    for ctype, ncrit, dcrit in drift_all_keys:
                        rd = {'Type': ctype, 'Criterion': dcrit}
                        statuses = []
                        for ri in sorted(drift_run_criteria.keys()):
                            found = None
                            for c in drift_run_criteria[ri]:
                                if c['type'] == ctype and normalize_criterion_text(c['criterion']) == ncrit:
                                    found = c
                                    break
                            if found:
                                pval = found.get('patient_value', '')
                                status_str = status_display_map(found['status'], pval)
                                rd[f'Run {ri}'] = f"{status_str}  ·  {pval}" if pval else status_str
                                statuses.append(found['status'])
                            else:
                                rd[f'Run {ri}'] = '—'
                                statuses.append('_missing_')
                        
                        rd['Changed'] = '⚡' if len(set(statuses)) > 1 else ''
                        drift_diff_rows.append(rd)
                    
                    if drift_diff_rows:
                        drift_diff_df = pd.DataFrame(drift_diff_rows)
                        drift_run_cols = sorted([c for c in drift_diff_df.columns if c.startswith('Run ')])
                        drift_diff_df = drift_diff_df[['Changed', 'Type', 'Criterion'] + drift_run_cols]
                        drift_diff_df = drift_diff_df.sort_values('Changed', ascending=False)
                        
                        n_drift_changed = sum(1 for r in drift_diff_rows if r['Changed'] == '⚡')
                        st.caption(f"{n_drift_changed} of {len(drift_diff_rows)} criteria differ across runs. "
                                   "Each Run column shows: Status · Patient Value extracted by GPT-4o.")
                        
                        drift_col_config = {
                            'Changed': st.column_config.Column(width='small'),
                            'Type': st.column_config.Column(width='small'),
                            'Criterion': st.column_config.Column(width='large'),
                        }
                        for rc in drift_run_cols:
                            drift_col_config[rc] = st.column_config.Column(width='large')
                        
                        st.dataframe(
                            drift_diff_df,
                            use_container_width=True,
                            hide_index=True,
                            height=min(400, 35 * (len(drift_diff_df) + 1)),
                            column_config=drift_col_config,
                        )
                        
        else:
            st.success("✓ All eligible-in-all trials have identical scores across runs. No score drift detected.")
    
    st.markdown("---")
    
    
    # =====================================================================
    # Per-Patient Reproducibility
    # =====================================================================
    st.subheader("Per-Patient Reproducibility")
    
    patient_repro = comp_df.groupby('patient_id').agg(
        trials_compared=('nct_id', 'count'),
        inferences=('num_inferences', 'max'),
        identical_scores=('all_scores_identical', 'sum'),
        flips=('all_classifications_identical', lambda x: (~x).sum()),
        mean_spread=('score_spread', 'mean'),
        max_spread=('score_spread', 'max'),
    ).reset_index()
    
    patient_repro['score_agreement'] = (patient_repro['identical_scores'] / patient_repro['trials_compared'] * 100).round(1)
    patient_repro = patient_repro.sort_values('score_agreement', ascending=True)
    
    display = patient_repro.rename(columns={
        'patient_id': 'Patient ID',
        'trials_compared': 'Trials Compared',
        'inferences': 'Inferences',
        'identical_scores': 'Identical Scores',
        'flips': 'Classification Flips',
        'mean_spread': 'Avg Score Spread',
        'max_spread': 'Max Score Spread',
        'score_agreement': 'Score Agreement %',
    })
    
    display = display.reset_index(drop=True)
    display.index = display.index + 1
    display.index.name = "Row"
    
    st.dataframe(
        display,
        use_container_width=True,
        hide_index=False,
        column_config={
            'Avg Score Spread': st.column_config.NumberColumn(format='%.4f'),
            'Max Score Spread': st.column_config.NumberColumn(format='%.4f'),
            'Score Agreement %': st.column_config.NumberColumn(format='%.1f%%'),
        }
    )
    
    st.caption(
        "Each row summarizes one patient's reproducibility across all trials compared. "
        "'Score Agreement %' is the fraction of trials where all inferences produced identical match scores. "
        "Low agreement or high score spread indicates GPT-4o inconsistency for that patient's profile."
    )


#------------------------------------------------------------------------------

def main():
    """Main application."""
    
    st.set_page_config(
        page_title=f"{Project_Name} Dashboard",
        page_icon="🏥",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    st.title(f"🏥 {Project_Name}: Clinical Trial Matching Dashboard")
    st.markdown("Real-time monitoring and analytics for the patient-trial matching pipeline")
    
    df = load_inferences_data()
    
    if df is None or df.empty:
        st.error("No data available. Please run some inferences first.")
        return
    
    filtered_df = render_sidebar(df)
    
    if filtered_df.empty:
        st.warning("No data matches the current filters.")
        return
    
    # Enrich with match tier columns (Full Match / Partial Match / No Match)
    trial_matches = load_trial_matches_data()
    filtered_df = enrich_match_tiers(filtered_df, trial_matches)
    
    # CSS for larger tab labels
    st.markdown("""
        <style>
        .stTabs [data-baseweb="tab-list"] button [data-testid="stMarkdownContainer"] p {
            font-size: 20px;
            font-weight: bold;
        }
        </style>
    """, unsafe_allow_html=True)
    
    # Tab navigation
    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs([
        "📊  Overview",
        "🔍  Match Quality",
        "🔎  Patient Explorer",
        "🧬  Trial Explorer",
        "👥  Patient Demographics",
        "⚡  Performance",
        "💰  Cost & Tokens",
        "🔬  Drift Detection",
        "🔁  Reproducibility"
    ])
    
    with tab1:
        render_overview_tab(filtered_df)
    
    with tab2:
        render_match_quality_tab(filtered_df)
    
    with tab3:
        render_patient_explorer_tab(filtered_df)
    
    with tab4:
        render_trial_explorer_tab(filtered_df)
    
    with tab5:
        render_patient_demographics_tab(filtered_df)
    
    with tab6:
        render_performance_tab(filtered_df)
    
    with tab7:
        render_cost_tokens_tab(filtered_df)
    
    with tab8:
        render_drift_detection_tab(filtered_df)

    with tab9:
        render_reproducibility_tab(filtered_df)


#------------------------------------------------------------------------------


if __name__ == "__main__":
    main()


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""