"""
Drift Detection tab. Moved verbatim out of "21- Streamlit Dashboard.py"
(pass 20c-3c-1).

The "How to Enable Drift Detection" block tells the reader to run
``python "20- Drift Detection.py"``. That instruction was FALSE when it was
written -- File 20 held zero import statements and died at its first ``def`` --
and pass 20c-3b made it true. It was re-verified by running the command for
this pass, so the text is correct and is carried over unchanged.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from oncotriage.dashboard.data import load_drift_metrics_data


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


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
