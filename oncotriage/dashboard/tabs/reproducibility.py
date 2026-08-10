"""
Reproducibility tab. Moved verbatim out of "21- Streamlit Dashboard.py"
(pass 20c-3c-1); the one function it held was broken up in pass 20f-4.

WHAT PASS 20f-4 DID, AND WHAT IT DELIBERATELY DID NOT
-----------------------------------------------------
Pass 20c-3c-1 recorded this module as "1,400+ lines because it is ONE function"
and called breaking it up its own item. This is that item, and it is a
MECHANICAL split only: every piece moved out is either a LITERAL TABLE or a
function of its arguments alone -- no ``st`` call, no closure over the render
function's locals, no read of anything the caller did not hand it.

``render_reproducibility_tab`` keeps its name, its module and its ONE
``@st.fragment``. Nothing extracted carries a decorator, and that is a decision
rather than an omission: a helper CALLED FROM INSIDE the fragment changes
nothing about what re-runs, while a helper carrying its own ``@st.fragment``
would create a NESTED fragment and change it. ``tests/test_package_invariants.py``
section 2i is an exact dict comparison keyed by ``path::qualified_name``, so a
decorator added here fails rather than merely appearing.

The three widget KEYS -- ``repro_collection_filter``,
``flip_deep_dive_selector``, ``drift_deep_dive_selector`` -- are session state.
Every one of them stays on the same ``st.selectbox`` call with the same string;
moving one would silently reset a widget for every user whose session carried it.

WHAT STAYED, AND WHY (the judgement half, left rather than guessed)
------------------------------------------------------------------
Every ``st.*`` call, every early return, both ``st.expander`` blocks and the
whole control flow stay in the render function. They share ``grouped``,
``relevant_matches``, ``patient_groups`` and ``flipped_comps_enriched`` with
what follows them, so cutting at any of those points means threading four to six
arguments through a wrapper that renders and returns nothing. A smaller honest
split beats a complete one that guesses.

TWO PAIRS THAT LOOK LIKE DUPLICATES AND ARE MEASURED RATHER THAN ASSUMED
------------------------------------------------------------------------
The flip deep dive and the score-drift deep dive both parse ``criterion_details``
and both align criteria across runs, and in both cases the two copies were
character-identical -- so ``_parse_criterion_details`` and
``_ordered_criterion_keys`` are shared, and the two ``normalize_criterion`` /
``normalize_criterion_text`` closures collapse into one ``_normalize_criterion``.

Their DIFF-ROW BUILDERS ARE NOT the same and are NOT shared: the flip one
carries a ``_rejected_`` branch (GPT-4o stops evaluating after the first
disqualifier, so a rejected run has no criteria and must render
"🚫 Not Evaluated (Rejected)" rather than "—") and tracks patient values across
runs; the drift one has neither, because a run that reached the drift table was
eligible in every inference. Merging them would have introduced a rejected
branch into a table that cannot contain one.
"""

from collections import Counter
import json

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from oncotriage.dashboard.data import load_trial_matches_data


# ===========================================================================
# LITERAL TABLES  (pass 20f-4)
# ===========================================================================
#
# These four were function locals. Each is a LITERAL -- no expression in any of
# them reads a value computed above it -- which is what makes hoisting them a
# move rather than a behaviour change. They were rebuilt on every rerun of the
# fragment and are now built once, at import.
#
# `mode_colors` and `mode_fixes` are NOT here, and that is the same measurement
# reaching the opposite answer: both are DERIVED (a comprehension over
# _FAILURE_CATEGORIES, then one key assigned), so they stay where they are built.
# They are also MUTATED after construction, and a module-level mutable rebuilt
# by every rerun is the hazard section 6a of tests/test_package_invariants.py
# exists to catch for MATCH_TIERS / MATCH_TIER_COLORS.

# Criterion status -> display string. `not_evaluable` is deliberately absent:
# it is decided by _status_display_map from the patient value, not by lookup.
_STATUS_DISPLAY_BASE_FLIP = {
    "met": "✅ Met",
    "not_met": "❌ Not Met",
    "violated": "❌ Violated",
    "not_violated": "✅ Not Violated",
}

# Severity ordering for the flip-type bar chart. Rendered descending.
_FLIP_TYPE_SEVERITY = {
    'Rejection ↔ Full Match': 4,
    'Rejection ↔ Partial Match': 3,
    'Rejection ↔ Zero Score': 2,
    'Full Match ↔ Partial Match': 1,
    'Other': 0,
}

_FLIP_TYPE_COLORS = {
    'Rejection ↔ Full Match': '#d62728',
    'Rejection ↔ Partial Match': '#ff7f0e',
    'Rejection ↔ Zero Score': '#9467bd',
    'Full Match ↔ Partial Match': '#2ca02c',
    'Other': '#7f7f7f',
}

# Root-cause categories for the failure-mode analysis, keyed by display name.
# ORDER IS LOAD-BEARING: the bar chart and the recommended-fix table iterate
# this dict's keys, so it is the actionability ordering the caption promises.
_FAILURE_CATEGORIES = {
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


# ===========================================================================
# PURE HELPERS  (pass 20f-4) — no `st` call, no closure, no I/O
# ===========================================================================

def _build_patient_groups(grouped):
    """Patients with 2+ inferences on one collection, as a list of dicts.

    Groups on the patient hash when the column is present and non-empty, so
    only inferences over IDENTICAL patient data are ever compared; without it
    (old rows, written before the hash existed) it falls back to
    (patient, collection).
    """
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
    return patient_groups


def _build_comparisons(patient_groups, relevant_matches):
    """One record per (patient, trial) evaluated in 2+ of that patient's runs.

    A trial that appeared in only one inference is skipped: there is nothing to
    compare it against, and including it would report perfect agreement for a
    measurement never made.
    """
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
    return comparisons


def _group_metrics(group_df):
    """n, % identical scores, and mean/max/std spread for one category."""
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


def _summary_statistics(comp_df):
    """Every number the Summary block renders, and the three category frames.

    The three frames are returned rather than recomputed by the caller because
    two of them (``eligible_all``, ``flipped_comps``) are read again by the
    sections below, and a second ``comp_df[comp_df['category'] == ...]`` is a
    second chance to disagree about which rows a section covers.
    """
    total_comparisons = len(comp_df)
    flip_count = (~comp_df['all_classifications_identical']).sum()
    flip_rate = flip_count / total_comparisons * 100 if total_comparisons > 0 else 0
    identical_classification = (1 - flip_count / total_comparisons) * 100 if total_comparisons > 0 else 100.0

    # Split into 4 mutually exclusive groups
    eligible_all = comp_df[comp_df['category'] == 'eligible_all']
    not_eligible_all = comp_df[comp_df['category'] == 'not_eligible_all']
    flipped_comps = comp_df[comp_df['category'] == 'flipped']

    return {
        'total_comparisons': total_comparisons,
        'flip_count': flip_count,
        'flip_rate': flip_rate,
        'identical_classification': identical_classification,
        'eligible_all': eligible_all,
        'not_eligible_all': not_eligible_all,
        'flipped_comps': flipped_comps,
        'all_m': _group_metrics(comp_df),
        'elig_m': _group_metrics(eligible_all),
        'flip_m': _group_metrics(flipped_comps),
        'not_elig_m': _group_metrics(not_eligible_all),
    }


def _build_flip_detail(flipped_comps):
    """Per-flip counts of eligible vs not_eligible runs, and the eligible scores."""
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

    return pd.DataFrame(flip_rows)


def _format_flip_detail(flip_detail_df):
    """The flip table as displayed: renamed, sorted by spread, 1-based Row index."""
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
    return flip_display


def _classify_flip_type(classifications, scores):
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


def _with_flip_types(flipped_comps):
    """A copy of the flipped frame carrying a `flip_type` column."""
    flip_types = []
    for _, row in flipped_comps.iterrows():
        ft = _classify_flip_type(row['classifications'], row['scores'])
        flip_types.append(ft)

    flipped_comps_enriched = flipped_comps.copy()
    flipped_comps_enriched['flip_type'] = flip_types
    return flipped_comps_enriched


def _classify_failure_modes(flipped_comps_enriched, patient_groups,
                            relevant_matches):
    """Root-cause categories per flip, from the rejection explanation.

    A flip can match several categories; one that matches none is 'Other'. The
    explanation is taken from the FIRST not_eligible row for that (patient,
    trial) -- there is only one rejection reason per run and every rejected run
    of the same trial is rejecting it for the same class of reason.
    """
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
            explanation = str(rej_matches.iloc[0].get('assessment', ''))

        explanation_lower = explanation.lower()
        matched_modes = []

        for mode_name, mode_info in _FAILURE_CATEGORIES.items():
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
            'assessment': explanation,
            'failure_modes': matched_modes,
        })

    return flip_failure_modes


def _status_display_map(status: str, patient_value: str = "") -> str:
    """Criterion status as displayed, with the patient value deciding two cases."""
    pv = (patient_value or "").strip()
    if pv.lower().startswith("not applicable"):
        return "➖ Not Applicable"
    if status == "not_evaluable":
        if pv and pv.lower() != "not in patient record":
            return "🔍 Unverifiable"
        return "⚠️ Missing Data"
    return _STATUS_DISPLAY_BASE_FLIP.get(status, status)


def _parse_criterion_details(tm_row):
    """Inclusion + exclusion criteria out of one trial_matches row.

    A row with no criterion_details, or unparseable JSON, yields []: a rejected
    run has no criteria because GPT-4o stops evaluating at the first
    disqualifier, and that is a normal state rather than an error.

    SHARED by the flip deep dive and the score-drift deep dive, which carried
    character-identical copies of this block.
    """
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
    return criteria_list


def _normalize_criterion(text):
    """Whitespace- and case-insensitive key for aligning criteria across runs."""
    return ' '.join(text.lower().strip().split())


def _ordered_criterion_keys(run_criteria):
    """(type, normalized, display) for every distinct criterion, in run order.

    First appearance wins the display text, so the table reads in the order the
    earliest run produced. SHARED by both deep dives, which carried identical
    copies.
    """
    all_criteria_keys = []
    seen_keys = set()

    for run_idx in sorted(run_criteria.keys()):
        for c in run_criteria[run_idx]:
            norm_key = (c['type'], _normalize_criterion(c['criterion']))
            if norm_key not in seen_keys:
                seen_keys.add(norm_key)
                all_criteria_keys.append((c['type'], _normalize_criterion(c['criterion']), c['criterion']))
    return all_criteria_keys


def _build_patient_repro(comp_df):
    """Per-patient reproducibility rollup, worst score agreement first."""
    patient_repro = comp_df.groupby('patient_id').agg(
        trials_compared=('nct_id', 'count'),
        inferences=('num_inferences', 'max'),
        identical_scores=('all_scores_identical', 'sum'),
        flips=('all_classifications_identical', lambda x: (~x).sum()),
        mean_spread=('score_spread', 'mean'),
        max_spread=('score_spread', 'max'),
    ).reset_index()

    patient_repro['score_agreement'] = (patient_repro['identical_scores'] / patient_repro['trials_compared'] * 100).round(1)
    return patient_repro.sort_values('score_agreement', ascending=True)


# ===========================================================================
# FIGURE BUILDERS  (pass 20f-4) — each returns a figure and renders nothing
# ===========================================================================

def _figure_min_max_scatter(comp_df):
    """Min vs max score per (patient, trial), stable points and flips apart."""
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
    return fig_scatter


def _figure_spread_histogram(comp_df):
    """Distribution of (max - min) score, with the 0.05 target line."""
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
    return fig_hist


def _figure_flip_types(type_counts, sorted_types, flip_count):
    """Horizontal bar of flip types, most severe at the top."""
    fig_flip_types = go.Figure()
    fig_flip_types.add_trace(go.Bar(
        x=[type_counts[t] for t in sorted_types],
        y=sorted_types,
        orientation='h',
        marker_color=[_FLIP_TYPE_COLORS.get(t, '#7f7f7f') for t in sorted_types],
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
    return fig_flip_types


def _figure_failure_modes(sorted_modes, mode_counts, mode_colors, n_flipped):
    """Horizontal bar of failure-mode counts. A flip can appear in several."""
    fig_modes = go.Figure()
    fig_modes.add_trace(go.Bar(
        x=[mode_counts.get(m, 0) for m in sorted_modes],
        y=[f"{m} ({mode_counts.get(m, 0)})" for m in sorted_modes],
        orientation='h',
        marker_color=[mode_colors.get(m, '#aaaaaa') for m in sorted_modes],
        text=[f"{mode_counts.get(m, 0)/n_flipped*100:.0f}%" for m in sorted_modes],
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
    return fig_modes


def _figure_flip_counts(counts, color):
    """Horizontal bar of a value_counts series.

    ONE builder for the two Flip Pattern charts. They were character-identical
    apart from `marker_color` -- '#ff7f0e' for cancer type, '#9467bd' for trial
    phase -- which is the whole of the difference and is now the argument.
    """
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=counts.index,
        x=counts.values,
        orientation='h',
        marker_color=color,
        text=counts.values,
        textposition='auto',
    ))
    fig.update_layout(
        height=max(250, len(counts) * 40),
        margin=dict(l=20, r=20, t=10, b=20),
        template='plotly_white',
        xaxis_title='Number of Flips',
        yaxis=dict(autorange='reversed'),
    )
    return fig


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
    patient_groups = _build_patient_groups(grouped)

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
    comparisons = _build_comparisons(patient_groups, relevant_matches)

    if not comparisons:
        st.info("No overlapping trials found across inferences. "
                "This may happen if the trial corpus changed entirely between inferences.")
        return
    
    comp_df = pd.DataFrame(comparisons)
    
    # =====================================================================
    # Summary Metrics
    # =====================================================================
    st.subheader("Summary")
    
    _summary = _summary_statistics(comp_df)
    total_comparisons = _summary['total_comparisons']
    flip_count = _summary['flip_count']
    flip_rate = _summary['flip_rate']
    identical_classification = _summary['identical_classification']
    eligible_all = _summary['eligible_all']
    flipped_comps = _summary['flipped_comps']
    all_m = _summary['all_m']
    elig_m = _summary['elig_m']
    flip_m = _summary['flip_m']
    not_elig_m = _summary['not_elig_m']

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
        st.plotly_chart(_figure_min_max_scatter(comp_df), use_container_width=True)

    with col2:
        # Score spread distribution
        st.plotly_chart(_figure_spread_histogram(comp_df), use_container_width=True)
    
    
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
        flip_detail_df = _build_flip_detail(flipped_comps)

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
        st.dataframe(_format_flip_detail(flip_detail_df),
                     use_container_width=True, hide_index=False)
        
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
        
        # -----------------------------------------------------------------
        # 1. Flip Type Classification
        # -----------------------------------------------------------------

        flipped_comps_enriched = _with_flip_types(flipped_comps)

        # --- Flip Type Breakdown Chart ---
        st.markdown("#### Flip Type Breakdown")

        type_counts = flipped_comps_enriched['flip_type'].value_counts()
        sorted_types = sorted(type_counts.index, key=lambda t: _FLIP_TYPE_SEVERITY.get(t, 0), reverse=True)

        st.plotly_chart(_figure_flip_types(type_counts, sorted_types, flip_count),
                        use_container_width=True)
        
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
        
        flip_failure_modes = _classify_failure_modes(
            flipped_comps_enriched, patient_groups, relevant_matches)

        failure_df = pd.DataFrame(flip_failure_modes)
        
        # Count failure modes
        all_modes = []
        for modes in failure_df['failure_modes']:
            all_modes.extend(modes)
        mode_counts = Counter(all_modes)
        
        # Display as horizontal bar chart
        sorted_modes = []
        for mode_name in _FAILURE_CATEGORIES.keys():
            if mode_name in mode_counts:
                sorted_modes.append(mode_name)
        if 'Other' in mode_counts:
            sorted_modes.append('Other')

        # DERIVED, so they stay here rather than being hoisted with the four
        # literal tables: each is a comprehension over _FAILURE_CATEGORIES with
        # one key assigned afterwards. Hoisting a derived table -- and a MUTATED
        # one -- is a behaviour change wearing the costume of a move.
        mode_colors = {name: info['color'] for name, info in _FAILURE_CATEGORIES.items()}
        mode_colors['Other'] = '#aaaaaa'

        mode_fixes = {name: info['fix'] for name, info in _FAILURE_CATEGORIES.items()}
        mode_fixes['Other'] = 'Review manually'

        st.plotly_chart(
            _figure_failure_modes(sorted_modes, mode_counts, mode_colors,
                                  len(flipped_comps)),
            use_container_width=True)
        
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
                    st.markdown(f"**{i}.** [{row['nct_id']}] {row['assessment']}")
        
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
                    prompt = patient_row.get('llm_classifier_prompt', '')
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
                    run_explanations[run_idx] = tm_row.get('assessment', '')
                    run_criteria[run_idx] = _parse_criterion_details(tm_row)

                all_criteria_keys = _ordered_criterion_keys(run_criteria)
                
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
                            if c['type'] == crit_type and _normalize_criterion(c['criterion']) == norm_crit:
                                found = c
                                break

                        if found:
                            pval = found.get('patient_value', '')
                            status_str = _status_display_map(found['status'], pval)
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
                    st.plotly_chart(
                        _figure_flip_counts(condition_counts, '#ff7f0e'),
                        use_container_width=True)
                else:
                    st.info("No condition data available.")
            else:
                st.info("No condition data available.")
        
        with col2:
            st.markdown("**Flips by Trial Phase**")
            if 'trial_phase' in flipped_patients.columns:
                phase_counts = flipped_patients['trial_phase'].fillna('Not Specified').value_counts()
                if not phase_counts.empty:
                    st.plotly_chart(
                        _figure_flip_counts(phase_counts, '#9467bd'),
                        use_container_width=True)
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
                        drift_run_criteria[run_idx] = _parse_criterion_details(tm_row)

                    drift_all_keys = _ordered_criterion_keys(drift_run_criteria)
                    
                    drift_diff_rows = []
                    for ctype, ncrit, dcrit in drift_all_keys:
                        rd = {'Type': ctype, 'Criterion': dcrit}
                        statuses = []
                        for ri in sorted(drift_run_criteria.keys()):
                            found = None
                            for c in drift_run_criteria[ri]:
                                if c['type'] == ctype and _normalize_criterion(c['criterion']) == ncrit:
                                    found = c
                                    break
                            if found:
                                pval = found.get('patient_value', '')
                                status_str = _status_display_map(found['status'], pval)
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
    
    patient_repro = _build_patient_repro(comp_df)

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


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
