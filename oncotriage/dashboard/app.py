"""
The dashboard's top-level render call.

``main()`` -- the page config, the sidebar, and the nine tabs -- moved verbatim
out of "21- Streamlit Dashboard.py" in pass 20c-3c-1.

``st.set_page_config`` STAYS INSIDE ``main()``, where File 21 always had it,
rather than moving up to the entry point. Streamlit requires it to be the first
Streamlit call of the run; keeping it as ``main()``'s first statement preserves
that ordering exactly, and moving it to module scope would make importing this
module a Streamlit side effect, which is what section 2 of
"tests/test_package_invariants.py" exists to forbid.
"""

import streamlit as st

from oncotriage.config import Project_Name
from oncotriage.dashboard.data import load_inferences_data, load_trial_matches_data
from oncotriage.dashboard.sidebar import render_sidebar
from oncotriage.dashboard.tabs.cost_tokens import render_cost_tokens_tab
from oncotriage.dashboard.tabs.demographics import render_patient_demographics_tab
from oncotriage.dashboard.tabs.drift import render_drift_detection_tab
from oncotriage.dashboard.tabs.match_quality import render_match_quality_tab
from oncotriage.dashboard.tabs.overview import render_overview_tab
from oncotriage.dashboard.tabs.patient_explorer import render_patient_explorer_tab
from oncotriage.dashboard.tabs.performance import render_performance_tab
from oncotriage.dashboard.tabs.reproducibility import render_reproducibility_tab
from oncotriage.dashboard.tabs.trial_explorer import render_trial_explorer_tab
from oncotriage.dashboard.tiers import enrich_match_tiers


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


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
