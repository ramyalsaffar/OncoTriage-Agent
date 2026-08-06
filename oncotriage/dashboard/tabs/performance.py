"""
Performance tab. Moved verbatim out of "21- Streamlit Dashboard.py" (pass 20c-3c-1).
"""

from plotly.subplots import make_subplots
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from oncotriage.config import MAX_TRIALS_FOR_EVALUATION
from oncotriage.dashboard.data import load_trial_matches_data
from oncotriage.dashboard.tiers import MATCH_TIER_COLORS, classify_trial_score


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


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
