"""
Dashboard sidebar: filters, the refresh button and the CSV export.

``render_sidebar(df)`` returns the filtered frame every tab is then handed.
Moved verbatim out of "21- Streamlit Dashboard.py" in pass 20c-3c-1.

The refresh button calls ``st.cache_data.clear()``, which clears BY CACHE, not
by function -- it empties every ``@st.cache_data`` entry in the process,
including the three loaders in ``oncotriage.dashboard.data``, which now live in
a different module than this one. That still works, and check 6b of
"47- Package Split Test.py" is what says so.
"""

from datetime import datetime

import streamlit as st


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


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
