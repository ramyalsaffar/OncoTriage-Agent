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
#
# THERE WERE FOUR AND THERE ARE THREE (pass 20f-3). `TRIAL_STATUS_FULL =
# '✅ Full Match'` stood at the top of this block and was READ BY NOTHING --
# not here, not in any tab, and not in "21- Streamlit Dashboard.py" before the
# split, checked against `git show ae3f6c6^`. It was dead on the day it was
# written, which is why deleting it changes no rendered pixel.
#
# It was also WRONG, which is the part worth recording. The per-TRIAL
# classifiers in patient_explorer and trial_explorer return the literal
# '✅ Eligible' for their top bucket, so this constant named a value the
# per-trial vocabulary cannot produce -- the PASSWORD_SOURCE_ARGUMENT shape
# exactly: a constant a caller would assert against, whose assertion could only
# ever fail. Its string belonged to the PER-PATIENT vocabulary below, where it
# was being typed out as a literal in three tabs.
TRIAL_STATUS_PARTIAL     = '🟡 Partial Match'
TRIAL_STATUS_UNCONFIRMED = '🔶 Unconfirmed'
TRIAL_STATUS_REJECTED    = '❌ Not Eligible'

# THERE ARE FOUR AGAIN, AND THE FOURTH IS NOT A BUCKET OF `classify_trial_score`
# (the campaign pass). That function partitions a SCORE into three; this names
# the state in which THERE IS NO SCORE TO PARTITION, which it cannot express
# and must not be asked to:
#
#   `match_score` is a nullable REAL. A trial row written by one of Stage 5's
#   failure returns carries no score, and so does every row written before the
#   column was populated. `classify_trial_score(None)` RAISES TypeError on its
#   first comparison -- taking the whole page down, since no tab call site has a
#   handler -- and `classify_trial_score(nan)` returns 'Unconfirmed Match',
#   which is a real verdict about a measurement nobody made. Neither is a
#   rendering of "unknown", so the three tabs that classify a trial test for
#   absence FIRST and use this.
#
# IT LIVES HERE RATHER THAN IN ONE TAB because all three of them need it --
# patient_explorer, trial_explorer and performance -- and a status string typed
# out in three files is the shape pass 20f-3 had to come back and fix for
# '✅ Full Match'. `classify_trial_score` itself is deliberately UNCHANGED: it
# is a pure function of a score and stays a partition of one.
TRIAL_STATUS_NO_SCORE    = '❔ No Score Recorded'


# Per-PATIENT outcome labels: the display form of each `match_tier` value that
# `enrich_match_tiers()` assigns below.
#
# THIS IS THE HOME THE THREE LITERALS DID NOT HAVE (pass 20f-3). The strings
# were typed out in overview, demographics and match_quality -- five
# occurrences of '✅ Full Match' across three files, not the three that pass
# 20e's follow-up note recorded; the note counted files rather than sites, and
# the pie chart in match_quality carries two of them (its `Outcome` list and its
# `color_discrete_map` key, which had to be kept in step by hand).
#
# THE VALUES ARE UNCHANGED, character for character, so nothing renders
# differently. What changes is that the per-patient vocabulary stops borrowing
# from the per-trial one: match_quality's pie chart listed
# `['✅ Full Match', TRIAL_STATUS_PARTIAL, '🔶 Unconfirmed Match', '❌ No Match']`,
# so editing the per-TRIAL partial label would silently have moved a per-PATIENT
# chart's slice name and its colour key together. Two vocabularies that happen
# to share a string are still two vocabularies.
PATIENT_OUTCOME_FULL        = '✅ Full Match'
PATIENT_OUTCOME_PARTIAL     = '🟡 Partial Match'
PATIENT_OUTCOME_UNCONFIRMED = '🔶 Unconfirmed Match'
PATIENT_OUTCOME_NO_MATCH    = '❌ No Match'

# In MATCH_TIERS order, so a chart can zip the two together instead of repeating
# the labels beside the colours. A TUPLE rather than a list or a dict on purpose:
# check 6a of tests/test_package_invariants.py rests on MATCH_TIERS and
# MATCH_TIER_COLORS being the only module-level MUTABLE objects the dashboard
# has, and an immutable container adds nothing for that scan to watch.
PATIENT_OUTCOME_LABELS = (
    PATIENT_OUTCOME_FULL,
    PATIENT_OUTCOME_PARTIAL,
    PATIENT_OUTCOME_UNCONFIRMED,
    PATIENT_OUTCOME_NO_MATCH,
)

# A raise rather than an assert: `python -O` strips asserts, and an invariant
# that disappears under an interpreter flag is not one. Same shape as the
# two-table guard in oncotriage/paths.py. A tier added to MATCH_TIERS with no
# label here would otherwise reach the pie chart as a silently shorter list,
# whose slices would then be labelled by position with the wrong names.
if len(PATIENT_OUTCOME_LABELS) != len(MATCH_TIERS):
    raise RuntimeError(
        f"the per-patient label vocabulary has {len(PATIENT_OUTCOME_LABELS)} "
        f"entries and MATCH_TIERS has {len(MATCH_TIERS)}: "
        f"{MATCH_TIERS!r}. They are zipped together, so they must correspond."
    )


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
