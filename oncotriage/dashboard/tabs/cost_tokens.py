"""
Cost & Tokens tab. Moved verbatim out of "21- Streamlit Dashboard.py" (pass 20c-3c-1).

THE COSTING WAS ALREADY CORRECT HERE; THE DOCSTRING THAT SAID OTHERWISE WAS NOT.
The paragraph this replaces claimed the tab "prices through ``get_model_cost``
and labels its chart GPT-4o", and item 38 checked it against the code rather
than believing it. Both halves were stale. The tab already grouped by
``matching_model`` with ``dropna=False``, priced each group against its OWN
model, tested for the missing group with ``pd.isna``, let
``UnknownModelPricingError`` reach the reader instead of defaulting a model to
zero, named every slice from the data, and derived the output:input price ratio
from observed spend rather than from gpt-4o's 4x literal. What it still says
"GPT-4o" about is one legend string in the tokens-per-trial chart, called out
at its own line below.

WHAT ITEM 38 ACTUALLY CHANGED HERE: THE DUPLICATION. The per-model breakdown
was computed in this file AND in ``oncotriage/storage/queries.py``, from the
same columns, by two loops that had already diverged -- this one used
``pd.isna`` and the query layer used ``is None`` and ``int(x or 0)``, so the
query layer raised ValueError on exactly the input this tab handled. The
arithmetic now lives once, in ``queries.price_model_groups``; this file supplies
the group sums for the SIDEBAR-FILTERED rows it was handed, through
``queries.model_groups_from_frame``, and renders the result.

AN UNPRICEABLE GROUP IS SURFACED AT THE TOTALS, NOT ONLY AT ITS CAUSE. A group
whose token sums are NULL, or whose model is NULL while it carries tokens,
prices at $0.00 — which is a floor, not a cost. The two warnings naming those
causes were already here; what was missing was anything qualifying the FIGURES
built on them, and a reader who scrolled past a warning had no way to tell a
partial total from a cheap run. ``queries.price_model_groups`` exposes
``cost_complete`` for exactly that question and this tab asks it, so this tab
and File 16's Query 10 make the same statement about the same database.

THE CONSOLIDATION DOES NOT IMPORT SQL'S NULL SEMANTICS INTO A PLACE THAT WAS
SAFE, and the direction of that risk is worth stating because it is the reverse
of the obvious one. This tab was never exposed to the ``int(nan)`` fault, for a
reason that has nothing to do with care: pandas' ``.sum()`` returns 0.0 where
SQL's ``SUM()`` returns NULL, so the NaN the query layer chokes on never
reached this loop. ``model_groups_from_frame`` passes ``min_count=1``, which
makes pandas agree with SQL and report an all-null group as null -- so this tab
now CAN see a null token sum, and the shared code is written in ``pd.isna``
throughout precisely so that it survives one. The gain is that "no token count
was ever recorded" stops being rendered as "$0.00 of tokens".
"""

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from oncotriage.storage import queries
from oncotriage.utils import UnknownModelPricingError
from oncotriage.dashboard.tiers import MATCH_TIERS, MATCH_TIER_COLORS


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
    #
    # PRICED PER ROW'S OWN MODEL. It used to price the whole table against
    # MATCHING_MODEL — the model configured at the moment the dashboard was
    # opened — and label the chart "GPT-4o". Both were wrong from the moment
    # inferences.db held rows from two judges: after the 2026-08-04 migration
    # to gpt-5.6-terra ($2.00/$12.00 per 1M) the GPT-4o rows ($2.50/$10.00)
    # would have been repriced at the new rate, understating their input spend
    # and overstating their output spend, while the chart went on calling both
    # "GPT-4o". The grouping key is matching_model, which File 14 writes from
    # the model that ANSWERED, so a row is priced and labelled by the model
    # that produced it.
    #
    # THE ARITHMETIC IS NOT HERE ANY MORE (item 38). It is
    # queries.price_model_groups, the one copy in the project, shared with
    # File 16's Query 10. What this file still owns is the SOURCE of the group
    # sums, and that has to stay local: `df` is the sidebar-filtered selection
    # with error rows already dropped, so calling queries.cost_by_model(conn)
    # would silently re-price the WHOLE table and ignore every filter the user
    # set. model_groups_from_frame produces the same aggregate the SQL does,
    # over the rows actually on screen.
    #
    # dropna=False keeps the NULL-model group, inside model_groups_from_frame.
    # Those are rows where Stage 5 never produced a response
    # (node_no_candidates, or a pre-response failure); they carry no Stage 5
    # tokens and so contribute no cost, but dropping them silently would hide a
    # logging defect if they ever did.
    try:
        _priced = queries.price_model_groups(queries.model_groups_from_frame(df))
    except UnknownModelPricingError as e:
        st.error(
            f"Cost breakdown unavailable: {e}"
        )
        return

    cost_components = []      # one row per (model, input|output)
    gpt4o_input_cost = 0.0
    gpt4o_output_cost = 0.0
    unpriceable_tokens = 0
    unrecorded_token_models = []

    for _row in _priced.itertuples(index=False):
        # pd.isna, because a NULL token SUM now reaches here as pandas' <NA>
        # rather than as 0. min_count=1 in model_groups_from_frame is what makes
        # that possible, and it is deliberate: 0.0 said "these rows cost
        # nothing", which is not what "no token count was ever recorded" means.
        _in = 0 if pd.isna(_row.input_tokens) else int(_row.input_tokens)
        _out = 0 if pd.isna(_row.output_tokens) else int(_row.output_tokens)

        if pd.isna(_row.input_tokens) and pd.isna(_row.output_tokens):
            unrecorded_token_models.append(_row.matching_model)

        if not _row.model_recorded:
            # Nothing to price against. Surfaced only if it carries
            # tokens, because a no-candidates run legitimately has none.
            unpriceable_tokens += _in + _out
            continue

        gpt4o_input_cost += _row.input_cost
        gpt4o_output_cost += _row.output_cost
        cost_components.append((f"{_row.matching_model} Output", _row.output_cost))
        cost_components.append((f"{_row.matching_model} Input", _row.input_cost))

    if unpriceable_tokens:
        st.warning(
            f"{unpriceable_tokens:,} Stage 5 tokens are on rows with no "
            f"matching_model recorded and are excluded from this breakdown. "
            f"A row with tokens but no model is a logging defect — the model "
            f"that produced them was not carried out of Stage 5."
        )

    if unrecorded_token_models:
        st.warning(
            f"No token count is recorded on ANY row for: "
            f"{', '.join(unrecorded_token_models)}. Those groups are shown at "
            f"$0.00 because nothing is known about their spend, which is not "
            f"the same as their having spent nothing."
        )

    # THE TWO WARNINGS ABOVE NAME THE CAUSES; THIS ONE QUALIFIES THE NUMBERS.
    # Both of them describe groups whose recomputed cost is $0.00 for want of
    # information rather than for want of spend, and every figure below —
    # recalc_total, the pie's percentages, the cost-share bars — is built on
    # that total. A reader who skipped a warning about tokens has no way to know
    # the chart under it is a floor. cost_complete is the single field the query
    # layer exposes for exactly this question, and asking it here is what keeps
    # this tab and File 16's Query 10 saying the same thing about the same
    # database.
    _incomplete = _priced[~_priced["cost_complete"]]
    _incomplete_rows = int(_incomplete["rows"].sum()) if len(_incomplete) else 0
    if len(_incomplete):
        st.warning(
            f"**The cost figures below are a FLOOR, not a total.** "
            f"{len(_incomplete)} of {len(_priced)} model groups "
            f"({_incomplete_rows:,} of {int(_priced['rows'].sum()):,} rows) "
            f"could not be priced from what was recorded — "
            f"{', '.join(_incomplete['matching_model'].tolist())} — and "
            f"contribute $0.00 instead of their real spend. Every percentage "
            f"and projection on this tab is computed over the priced remainder."
        )

    if not cost_components:
        # Every row has a NULL matching_model, so there is nothing to price
        # and nothing to name. Said out loud rather than drawn as an empty
        # pie chart, which is the failure mode this whole block was rewritten
        # to remove.
        st.info(
            "No row in this selection records which model produced it "
            "(matching_model is NULL on all of them), so no cost breakdown "
            "can be computed. Rows written by a pipeline terminal node always "
            "carry the model that answered."
        )
        return

    recalc_total = gpt4o_input_cost + gpt4o_output_cost

    df_cost = pd.DataFrame({
        'Component': [c for c, _ in cost_components],
        'Cost': [v for _, v in cost_components],
        'Percentage': [
            v / recalc_total * 100 if recalc_total > 0 else 0
            for _, v in cost_components
        ]
    })

    # Effective blended output:input price ratio over whatever models this
    # selection actually contains. Derived ONCE here because two charts want it:
    # the volume-vs-cost stack below, and the tokens-per-trial stack further
    # down whose legend carried "4× cost" as a literal. That literal was
    # gpt-4o's ratio ($10.00 / $2.50) and is wrong for any table holding a
    # second judge — the one thing in this tab that really did still say GPT-4o.
    # Guarded on both denominators: a selection with no input tokens has no
    # ratio to state, and both legends then say so instead of inventing one.
    total_input_tokens = df['gpt4o_input_tokens'].sum()
    total_output_tokens = df['gpt4o_output_tokens'].sum()
    _in_rate = (gpt4o_input_cost / total_input_tokens) if total_input_tokens else 0
    _out_rate = (gpt4o_output_cost / total_output_tokens) if total_output_tokens else 0
    _price_ratio = (_out_rate / _in_rate) if (_in_rate > 0 and _out_rate > 0) else None

    col1, col2 = st.columns(2)

    with col1:
        fig_pie = px.pie(df_cost, values='Cost', names='Component', template='plotly_white', title='Cost Distribution')
        fig_pie.update_traces(textposition='auto', textinfo='percent+label', textfont_size=14)
        fig_pie.update_layout(height=350, margin=dict(l=20, r=20, t=40, b=20))
        st.plotly_chart(fig_pie, use_container_width=True)
    
    with col2:
        # Stacked bars: token volume vs cost share — exposes the pricing
        # asymmetry. The multiplier used to be written into the legend as "4×",
        # which was gpt-4o's ratio ($10.00 / $2.50). It is derived from the
        # observed spend above so it stays true across a mixed-model table: with
        # two judges at different ratios there is no single literal that is right.
        total_tokens = total_input_tokens + total_output_tokens
        input_tok_pct = total_input_tokens / total_tokens * 100 if total_tokens > 0 else 0
        output_tok_pct = total_output_tokens / total_tokens * 100 if total_tokens > 0 else 0

        input_cost_pct = gpt4o_input_cost / recalc_total * 100 if recalc_total > 0 else 0
        output_cost_pct = gpt4o_output_cost / recalc_total * 100 if recalc_total > 0 else 0

        _ratio_label = ("Output" if _price_ratio is None
                        else f"Output ({_price_ratio:.1f}x price/tok)")

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
            name=_ratio_label,
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
    
    # Model names come from the data, not from a literal and not from
    # MATCHING_MODEL: this table can hold rows from more than one judge, and
    # naming the configured one would relabel history every time the config
    # changes.
    _models_present = sorted(
        str(m) for m in df['matching_model'].dropna().unique()
    ) or ["(none recorded)"]
    st.caption(
        f"Stage 5 judge(s) in this table: {', '.join(_models_present)}. "
        "Input tokens carry the prompt and patient/trial data; output tokens "
        "carry the eligibility assessments — and, on a reasoning model, the "
        "reasoning tokens that are billed at the output rate but never shown. "
        "Each model's tokens are priced at that model's own rates."
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
            # Was the literal '4× cost'. See _price_ratio above.
            name=('Output Tokens' if _price_ratio is None
                  else f'Output Tokens ({_price_ratio:.1f}× cost)'),
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


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
