"""
The match-tier vocabulary and the two functions that apply it.

Moved verbatim out of "21- Streamlit Dashboard.py" in pass 20c-3c-1. In the
original this block sat under a section header reading "MAIN", 1,500 lines
below its first use; the header was wrong and is not carried over. The code is
byte-identical.

``MATCH_TIERS`` and ``MATCH_TIER_COLORS`` ARE THE ONLY MODULE-LEVEL MUTABLE
OBJECTS THE DASHBOARD HAS. Under the old exec bootstrap they were rebuilt on
every Streamlit rerun; as a module they are built once per process. That is
safe here and was measured rather than assumed: nothing in the dashboard
mutates either one, the ``tier_colors = MATCH_TIER_COLORS`` alias in three tabs
is never written through, and handing the dict to plotly's
``color_discrete_map`` leaves it unchanged. Check 6a of
"tests/test_package_invariants.py" re-derives all three facts, so a future edit that
starts mutating them fails rather than corrupting every later rerun.
"""




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


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
