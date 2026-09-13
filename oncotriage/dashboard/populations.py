"""
Which rows a dashboard figure is computed over, and what a trial row's verdict is.

WHY THIS MODULE EXISTS (the dashboard-truthfulness pass)
--------------------------------------------------------
An audit of the rendered dashboard against a five-patient smoke run found two
defects repeated across every tab, each at one root:

  * FAILED CALLS DISPLAYED AS CLINICAL REJECTIONS. Every per-trial classifier
    read ``if eligible != 'eligible': rejected``, so 64 of 98 trial rows -- all
    of them ``not_evaluable`` with reason ``per_trial_call_failed`` -- rendered
    as "Not Eligible" with a score of 0, and the reproducibility tab counted a
    failed call against a definite verdict as the model changing its mind.

  * ROWS COUNTED AS PATIENTS. Tier percentages divided by ``len(df)``, which is
    inference rows: two resample reruns counted every patient twice, and two
    errored retries counted as two clinical "No Match" patients.

Both are fixed HERE, once, and read by every tab. A per-tab repair is how the
Any Match figure came to have three definitions before the repair pass.

WHAT A TRIAL ROW'S VERDICT IS
-----------------------------
``eligible`` holds one of three values. ``eligible`` and ``not_eligible`` are
DEFINITE ELIGIBILITY VERDICTS. ``not_evaluable`` is not one thing: its STORED
REASON says which of two different things happened, and ``not_evaluated_kind``
is the one reading of it --

  no usable verdict     the pipeline never obtained a usable response (a failed
                        call, a truncation, an omission, an unusable response).
                        An INFRASTRUCTURE or OUTPUT failure, never clinical.
  clinical uncertainty  the model declared the trial not evaluable. A
                        legitimate clinical result.
  reason unknown        no reason recorded, or one this pipeline does not write.

TWO POPULATIONS, TWO LABELS -- AND NEVER "ANSWERED"
---------------------------------------------------
"Answered" meant two different sets on two tiles, and the difference between
them is exactly a declared clinical uncertainty:

  definite eligibility verdict    ``eligible`` or ``not_eligible``
                                  (``is_definite_verdict``). Comparisons, flips,
                                  scores, tokens per trial, and "patients with
                                  a verdict" counts use this.
  usable result, including        a definite verdict OR a model-declared
  clinical uncertainty            clinical uncertainty (``is_usable_result``).
                                  The retention denominator uses this, and an
                                  evaluation is complete when every trial has
                                  one.

``DEFINITE_VERDICT_LABEL`` and ``USABLE_RESULT_LABEL`` are the words, one owner
each, so no panel can describe one population with the other's name.

The reason tuples come from ``oncotriage/storage/queries.py``, which restates
the agent's vocabulary and is pinned against it by
``tests/test_storage_query_layer.py``. They are imported rather than restated a
third time.

WHICH ROWS A PATIENT FIGURE IS COMPUTED OVER
--------------------------------------------
A patient can hold several inference rows: a resample rerun, an errored retry,
a resumed campaign, a second campaign. A patient figure takes ONE row per
patient -- the FIRST ATTEMPT, ``MIN(id)`` per patient within a campaign -- and
it is designated BEFORE any outcome filter, so filtering to "Any Match" cannot
promote a later attempt into "first". The campaign is the run chain
``queries.campaign_summary`` stitches; a patient in two campaigns has one first
attempt in each, and the population label says so when it happens.

A CLINICAL TIER is computed only for a first attempt whose evaluation is
COMPLETE. An errored attempt, or one with a trial that has no usable verdict,
is counted as INCOMPLETE and shown as its own count -- not folded into No Match,
and not dropped. The mere presence of a model-declared uncertainty does not make
an evaluation incomplete.

WHAT THIS MODULE IMPORTS
------------------------
``nullsafe`` and ``tiers`` (both leaves) and the reason tuples from
``storage.queries``. It opens no file and reads no database: every function is a
function of the frames it is handed.
"""

import re

import pandas as pd

from oncotriage.dashboard.nullsafe import is_absent
from oncotriage.dashboard.tiers import (
    ANY_MATCH_TIERS,
    EVALUATION_COMPLETE,
    EVALUATION_ERRORED,
    EVALUATION_MISSING_VERDICTS,
    EVALUATION_STATE_COLUMN,
    EVALUATION_STATES,
    MATCH_TIER_INCOMPLETE,
    MATCH_TIERS,
    TRIAL_STATUS_NO_SCORE,
    TRIAL_STATUS_NOT_EVALUABLE_UNCERTAIN,
    TRIAL_STATUS_NOT_EVALUATED_FAILED,
    TRIAL_STATUS_NOT_EVALUATED_UNKNOWN,
    TRIAL_STATUS_PARTIAL,
    TRIAL_STATUS_REJECTED,
    TRIAL_STATUS_UNCONFIRMED,
    TRIAL_STATUS_UNRECOGNISED,
    apply_evaluation_state,
    classify_trial_score,
)
from oncotriage.storage.queries import (
    NOT_EVALUABLE_REASONS_CONSTRUCTED,
    NOT_EVALUABLE_REASONS_CORRECTED,
    NOT_EVALUABLE_REASONS_DECLARED,
)


# ===========================================================================
# THE TRIAL VERDICT
# ===========================================================================

VERDICT_ELIGIBLE = 'eligible'
VERDICT_NOT_ELIGIBLE = 'not_eligible'
VERDICT_NOT_EVALUABLE = 'not_evaluable'
"""The three values Stage 5 writes into ``trial_matches.eligible``. Restated from
``oncotriage/agent/state.py`` (the dashboard does not import the agent);
``tests/test_dashboard_truthfulness.py`` pins them against it."""

DEFINITE_VERDICTS = (VERDICT_ELIGIBLE, VERDICT_NOT_ELIGIBLE)
"""The two values that are a DEFINITE ELIGIBILITY VERDICT. Every agreement, flip,
score and plot of an outcome is computed over these and nothing else."""

DEFINITE_VERDICT_LABEL = 'definite eligibility verdict'
"""The words for ``DEFINITE_VERDICTS``. A declared clinical uncertainty is NOT one."""

USABLE_RESULT_LABEL = 'usable result, including clinical uncertainty'
"""The words for ``is_usable_result``: a definite verdict or a declared uncertainty."""

NOT_EVALUATED_NO_USABLE_VERDICT = 'no usable verdict'
NOT_EVALUATED_CLINICAL_UNCERTAINTY = 'clinical uncertainty'
NOT_EVALUATED_REASON_UNKNOWN = 'reason unknown'
NOT_EVALUATED_KINDS = (NOT_EVALUATED_NO_USABLE_VERDICT,
                       NOT_EVALUATED_CLINICAL_UNCERTAINTY,
                       NOT_EVALUATED_REASON_UNKNOWN)

_NO_USABLE_VERDICT_REASONS = frozenset(NOT_EVALUABLE_REASONS_CONSTRUCTED) | \
    frozenset(NOT_EVALUABLE_REASONS_CORRECTED)
_CLINICAL_UNCERTAINTY_REASONS = frozenset(NOT_EVALUABLE_REASONS_DECLARED)

REASON_NOT_RECORDED_TEXT = '(reason not recorded)'
"""What a not-evaluated row's reason column shows when the database holds none."""


def not_evaluated_kind(reason) -> str:
    """Which of ``NOT_EVALUATED_KINDS`` a stored ``not_evaluable_reason`` is.

    THE ONE READING OF THE REASON. A constructed reason (the model never
    responded) and a corrected one (it responded unusably) are both NO USABLE
    VERDICT; the declared reason is CLINICAL UNCERTAINTY; anything else,
    absence included, is REASON UNKNOWN -- neither of the other two can be
    asserted about it.
    """
    if is_absent(reason):
        return NOT_EVALUATED_REASON_UNKNOWN
    text = str(reason)
    if text in _NO_USABLE_VERDICT_REASONS:
        return NOT_EVALUATED_NO_USABLE_VERDICT
    if text in _CLINICAL_UNCERTAINTY_REASONS:
        return NOT_EVALUATED_CLINICAL_UNCERTAINTY
    return NOT_EVALUATED_REASON_UNKNOWN


_KIND_STATUS = {
    NOT_EVALUATED_NO_USABLE_VERDICT: TRIAL_STATUS_NOT_EVALUATED_FAILED,
    NOT_EVALUATED_CLINICAL_UNCERTAINTY: TRIAL_STATUS_NOT_EVALUABLE_UNCERTAIN,
    NOT_EVALUATED_REASON_UNKNOWN: TRIAL_STATUS_NOT_EVALUATED_UNKNOWN,
}
if set(_KIND_STATUS) != set(NOT_EVALUATED_KINDS):
    raise RuntimeError(
        f"populations: every not-evaluated kind needs a display status; "
        f"kinds {NOT_EVALUATED_KINDS!r}, mapped {sorted(_KIND_STATUS)!r}")


def is_definite_verdict(verdict) -> bool:
    """True when ``verdict`` is a DEFINITE ELIGIBILITY VERDICT (eligible / not_eligible)."""
    return (not is_absent(verdict)) and str(verdict) in DEFINITE_VERDICTS


def is_usable_result(verdict, reason=None) -> bool:
    """True for a definite verdict OR a model-declared clinical uncertainty.

    A DIFFERENT POPULATION FROM ``is_definite_verdict``, deliberately: a declared
    uncertainty is a clinical result the model gave, so it is usable, but it is
    not an eligibility decision, so it is never a definite verdict. See
    ``USABLE_RESULT_LABEL``.
    """
    if is_definite_verdict(verdict):
        return True
    return ((not is_absent(verdict)) and str(verdict) == VERDICT_NOT_EVALUABLE
            and not_evaluated_kind(reason) == NOT_EVALUATED_CLINICAL_UNCERTAINTY)


def trial_display_status(verdict, match_score, reason=None) -> str:
    """The display status of ONE trial row. THE ONE PER-TRIAL CLASSIFIER.

    Replaces three copies -- Patient Explorer, Trial Explorer, the CSV export --
    that all mapped every non-eligible value to "Not Eligible".
    """
    if is_absent(verdict):
        return TRIAL_STATUS_UNRECOGNISED
    verdict = str(verdict)
    if verdict == VERDICT_ELIGIBLE:
        if is_absent(match_score):
            return TRIAL_STATUS_NO_SCORE
        tier = classify_trial_score(match_score)
        if tier == 'Full Match':
            return '✅ Eligible'
        if tier == 'Partial Match':
            return TRIAL_STATUS_PARTIAL
        return TRIAL_STATUS_UNCONFIRMED
    if verdict == VERDICT_NOT_ELIGIBLE:
        return TRIAL_STATUS_REJECTED
    if verdict == VERDICT_NOT_EVALUABLE:
        return _KIND_STATUS[not_evaluated_kind(reason)]
    return TRIAL_STATUS_UNRECOGNISED


def reason_text(verdict, reason) -> str:
    """The stored reason VERBATIM for a not-evaluated row; "" for a definite verdict."""
    if is_definite_verdict(verdict):
        return ''
    if is_absent(reason):
        return REASON_NOT_RECORDED_TEXT
    return str(reason)


def display_score(verdict, match_score):
    """The score to DISPLAY: the stored score for a definite verdict, ``None`` otherwise.

    A not-evaluated trial is stored with ``match_score`` 0.0. Rendering that 0
    is the defect: a score of zero is a measurement, and nothing was measured.
    """
    if not is_definite_verdict(verdict) or is_absent(match_score):
        return None
    return float(match_score)


def with_trial_status(trial_rows):
    """A COPY of ``trial_rows`` with ``Status``, ``display_score`` and ``reason``.

    ``not_evaluable_reason`` is an ADDITIVE column, so its absence is handled as
    "reason not recorded" rather than raised on.
    """
    out = trial_rows.copy()
    reasons = (out['not_evaluable_reason'] if 'not_evaluable_reason' in out.columns
               else pd.Series([None] * len(out), index=out.index))
    out['Status'] = [trial_display_status(v, s, r) for v, s, r in
                     zip(out['eligible'], out['match_score'], reasons)]
    out['display_score'] = [display_score(v, s) for v, s in
                            zip(out['eligible'], out['match_score'])]
    out['reason'] = [reason_text(v, r) for v, r in zip(out['eligible'], reasons)]
    return out


# ===========================================================================
# EVALUATION COMPLETENESS, PER INFERENCE ROW
# ===========================================================================

EVALUATION_COUNT_COLUMNS = ('trial_rows', 'trials_definite_verdict',
                            'trials_no_usable_verdict',
                            'trials_clinical_uncertainty',
                            'trials_reason_unknown', 'trials_unrecognised')


def trial_evaluation_counts(trial_matches):
    """One row per ``inference_id`` with the counts in ``EVALUATION_COUNT_COLUMNS``."""
    if trial_matches is None or len(trial_matches) == 0:
        return pd.DataFrame(columns=('inference_id',) + EVALUATION_COUNT_COLUMNS)
    reasons = (trial_matches['not_evaluable_reason']
               if 'not_evaluable_reason' in trial_matches.columns
               else pd.Series([None] * len(trial_matches),
                              index=trial_matches.index))
    verdicts = trial_matches['eligible']
    kinds = [not_evaluated_kind(r) if (not is_absent(v)
                                       and str(v) == VERDICT_NOT_EVALUABLE)
             else None for v, r in zip(verdicts, reasons)]
    frame = pd.DataFrame({
        'inference_id': trial_matches['inference_id'].values,
        'trial_rows': 1,
        'trials_definite_verdict': [int(is_definite_verdict(v)) for v in verdicts],
        'trials_no_usable_verdict':
            [int(k == NOT_EVALUATED_NO_USABLE_VERDICT) for k in kinds],
        'trials_clinical_uncertainty':
            [int(k == NOT_EVALUATED_CLINICAL_UNCERTAINTY) for k in kinds],
        'trials_reason_unknown':
            [int(k == NOT_EVALUATED_REASON_UNKNOWN) for k in kinds],
        'trials_unrecognised':
            [int((not is_definite_verdict(v)) and (is_absent(v)
                 or str(v) != VERDICT_NOT_EVALUABLE)) for v in verdicts],
    })
    return frame.groupby('inference_id', as_index=False).sum()


def _state_of(row):
    """The evaluation state of one inference row. See ``tiers.EVALUATION_*``."""
    error = row.get('error')
    if not is_absent(error) and str(error) != '':
        return EVALUATION_ERRORED
    if (row['trials_no_usable_verdict'] > 0 or row['trials_reason_unknown'] > 0
            or row['trials_unrecognised'] > 0):
        return EVALUATION_MISSING_VERDICTS
    evaluated = row.get('candidates_evaluated')
    if not is_absent(evaluated):
        try:
            if row['trial_rows'] < int(evaluated):
                return EVALUATION_MISSING_VERDICTS
        except (TypeError, ValueError):
            pass
    return EVALUATION_COMPLETE


def annotate_evaluation_state(df, trial_matches):
    """A COPY of ``df`` carrying the counts and ``EVALUATION_STATE_COLUMN``.

    THE ONE INCOMPLETENESS RULE, in order:

      1. the row carries an error                          -> errored
      2. any trial has no usable verdict, an unknown reason,
         or an unrecognised verdict value                  -> missing verdicts
      3. fewer trial rows than ``candidates_evaluated``    -> missing verdicts
      4. otherwise                                         -> complete

    A trial the model declared not evaluable does NOT trip rule 2. A row with no
    ``candidates_evaluated`` recorded cannot be checked by rule 3 and is not
    presumed short. A copy, for ``call_mode.annotate``'s reason: the frame a tab
    is handed is shared with every other tab.
    """
    out = df.copy()
    counts = trial_evaluation_counts(trial_matches)
    for column in EVALUATION_COUNT_COLUMNS:
        if column in out.columns:
            out = out.drop(columns=column)
    if 'id' in out.columns and len(counts):
        out = out.merge(counts, left_on='id', right_on='inference_id',
                        how='left').drop(columns='inference_id',
                                         errors='ignore')
    for column in EVALUATION_COUNT_COLUMNS:
        if column not in out.columns:
            out[column] = 0
        out[column] = out[column].fillna(0).astype(int)
    out[EVALUATION_STATE_COLUMN] = (
        [_state_of(r) for _, r in out.iterrows()] if len(out) else [])
    # A CLOSED VOCABULARY, CHECKED WHERE IT IS PRODUCED. `enrich_match_tiers`
    # treats anything but COMPLETE as incomplete, so a stray value would be
    # silently absorbed there; it is refused here instead.
    _stray = set(out[EVALUATION_STATE_COLUMN]) - set(EVALUATION_STATES)
    if _stray:
        raise RuntimeError(f"annotate_evaluation_state produced states outside "
                           f"EVALUATION_STATES: {sorted(_stray)!r}")
    return out


def ensure_evaluated(df, trial_matches):
    """The frame a tier-reading panel may use: state annotated, tiers applied.

    ``oncotriage/dashboard/app.py:main()`` annotates before any tab renders, so
    on that path this returns ``df`` itself. A tab rendered ANY other way -- a
    test harness, an embedding -- would otherwise tier an errored or
    verdict-less row as a clinical outcome, which is the defect this module
    removes; making incompleteness depend on the caller is how it would come
    back. A frame with no ``match_tier`` is returned untouched, so the named
    KeyError ``tiers.any_match_series`` raises still reaches its reader.
    """
    if EVALUATION_STATE_COLUMN in df.columns or 'match_tier' not in df.columns:
        return df
    return apply_evaluation_state(annotate_evaluation_state(df, trial_matches))


# ===========================================================================
# CAMPAIGNS AND FIRST ATTEMPTS
# ===========================================================================

CAMPAIGN_KEY_COLUMN = 'campaign_key'
FIRST_ATTEMPT_COLUMN = 'first_attempt'

CAMPAIGN_KEY_NO_RUN_ID = 'no run id'
"""The campaign bucket of a row with a NULL ``run_id`` -- every API request and
every row written before run tracking. One bucket, stated rather than guessed."""

CAMPAIGN_KEY_NO_TRACKING = 'no run tracking'
"""The single bucket used when the frame has no ``run_id`` column at all."""


def campaign_of_run(campaigns) -> dict:
    """``{run_id: campaign_id}`` from ``queries.campaign_summary``'s frame.

    ``run_ids`` is the ordered string that query builds ("1 -> 3 -> 5"); every
    integer in it belongs to that campaign. An empty or absent frame maps
    nothing, and every run then keys as its own unstitched campaign.
    """
    mapping = {}
    if campaigns is None or len(campaigns) == 0 or 'run_ids' not in campaigns:
        return mapping
    for campaign_id, run_ids in zip(campaigns['campaign_id'],
                                    campaigns['run_ids']):
        if is_absent(run_ids):
            continue
        for token in re.findall(r'\d+', str(run_ids)):
            mapping[int(token)] = int(campaign_id)
    return mapping


def attach_campaign_key(df, campaigns=None):
    """A COPY of ``df`` with ``CAMPAIGN_KEY_COLUMN``.

    campaign N           the run belongs to stitched campaign N
    run N (unstitched)   the run is not in the campaign frame (run tables
                         absent, or a dangling ``run_id``)
    no run id            ``run_id`` is NULL
    no run tracking      the frame has no ``run_id`` column
    """
    out = df.copy()
    if 'run_id' not in out.columns:
        out[CAMPAIGN_KEY_COLUMN] = CAMPAIGN_KEY_NO_TRACKING
        return out
    mapping = campaign_of_run(campaigns)

    def _key(run_id):
        if is_absent(run_id):
            return CAMPAIGN_KEY_NO_RUN_ID
        try:
            run = int(run_id)
        except (TypeError, ValueError):
            return CAMPAIGN_KEY_NO_RUN_ID
        if run in mapping:
            return f'campaign {mapping[run]}'
        return f'run {run} (unstitched)'

    out[CAMPAIGN_KEY_COLUMN] = [_key(v) for v in out['run_id']]
    return out


def designate_first_attempts(df):
    """A COPY of ``df`` with ``FIRST_ATTEMPT_COLUMN``: MIN(id) per (campaign, patient).

    Computed over EXACTLY the rows handed in, which is the selection. The
    sidebar calls this after its non-outcome filters and BEFORE its match-status
    filter, so the designation is never made after an outcome filter.
    """
    out = df.copy()
    if len(out) == 0:
        out[FIRST_ATTEMPT_COLUMN] = pd.Series(dtype=bool)
        return out
    keys = ([CAMPAIGN_KEY_COLUMN] if CAMPAIGN_KEY_COLUMN in out.columns else []) \
        + ['patient_id']
    grouping = out[keys].astype(object).where(out[keys].notna(), '(none)')
    first_ids = out['id'].groupby([grouping[k] for k in keys]).transform('min')
    out[FIRST_ATTEMPT_COLUMN] = (out['id'] == first_ids).values
    return out


def first_attempts(df):
    """The first-attempt rows of ``df``.

    Uses the sidebar's designation when the frame carries it; otherwise
    designates over ``df`` itself, which is then the selection. That fallback is
    what a tab rendered outside ``main()`` gets, and it is stated here rather
    than hidden: it is correct for any frame no outcome filter has touched.
    """
    if FIRST_ATTEMPT_COLUMN not in df.columns:
        df = designate_first_attempts(df)
    return df[df[FIRST_ATTEMPT_COLUMN].astype(bool)]


# ===========================================================================
# THE PATIENT OUTCOME SUMMARY
# ===========================================================================

def patient_outcomes(df):
    """Every number a patient-tier panel shows, over first attempts.

    Keys:
        first_attempts      rows in the population
        patients            distinct patient_id among them
        campaigns           distinct campaign keys among them
        complete            first attempts whose evaluation is complete
        incomplete          first attempts that are not
        errored             ...of which errored
        missing_verdicts    ...of which missing verdicts
        incomplete_with_eligible  incomplete first attempts with >=1 eligible
                            trial -- a LOWER BOUND on Any Match among them, not
                            a tier
        tier_counts         {tier: n} over COMPLETE first attempts
        tier_rates          {tier: % of complete}, NaN when complete == 0
        any_match           complete first attempts in an Any Match tier
        any_match_rate      % of complete, NaN when complete == 0
        label               the population, in words
    """
    tiers = list(MATCH_TIERS)
    rows = first_attempts(df)
    n = int(len(rows))
    patients = int(rows['patient_id'].nunique()) if n else 0
    campaigns = (int(rows[CAMPAIGN_KEY_COLUMN].nunique())
                 if n and CAMPAIGN_KEY_COLUMN in rows.columns else (1 if n else 0))
    if EVALUATION_STATE_COLUMN in rows.columns:
        state = rows[EVALUATION_STATE_COLUMN]
    else:
        state = rows['match_tier'].map(
            lambda t: EVALUATION_MISSING_VERDICTS
            if t == MATCH_TIER_INCOMPLETE else EVALUATION_COMPLETE)
    complete_mask = (state == EVALUATION_COMPLETE).values
    complete = rows[complete_mask]
    incomplete = rows[~complete_mask]
    c = int(len(complete))
    tier_counts = {t: int((complete['match_tier'] == t).sum()) for t in tiers}
    tier_rates = {t: (tier_counts[t] / c * 100 if c else float('nan'))
                  for t in tiers}
    any_match = int(complete['match_tier'].isin(ANY_MATCH_TIERS).sum())
    with_eligible = 0
    if len(incomplete):
        present = [col for col in ('full_match_count', 'partial_match_count',
                                   'unconfirmed_match_count')
                   if col in incomplete.columns]
        if present:
            eligible = incomplete[present].fillna(0).sum(axis=1)
            with_eligible = int((eligible > 0).sum())
    if n == patients:
        label = f"{patients} patient(s), first attempt per patient"
    else:
        label = (f"{n} first attempts from {patients} patient(s) across "
                 f"{campaigns} campaign(s) — one per patient per campaign")
    return {
        'first_attempts': n,
        'patients': patients,
        'campaigns': campaigns,
        'complete': c,
        'incomplete': int(len(incomplete)),
        'errored': int((state == EVALUATION_ERRORED).sum()),
        'missing_verdicts': int((state == EVALUATION_MISSING_VERDICTS).sum()),
        'incomplete_with_eligible': with_eligible,
        'tier_counts': tier_counts,
        'tier_rates': tier_rates,
        'any_match': any_match,
        'any_match_rate': (any_match / c * 100) if c else float('nan'),
        'label': label,
    }


def incomplete_caption(summary) -> str:
    """The sentence every tier panel prints about what it excluded."""
    if summary['incomplete'] == 0:
        return (f"Population: {summary['label']}. Every first attempt is a "
                f"complete evaluation.")
    return (
        f"Population: {summary['label']}. **{summary['incomplete']} incomplete "
        f"evaluation(s) are excluded from every tier percentage** "
        f"({summary['errored']} errored, {summary['missing_verdicts']} with at "
        f"least one trial that has no usable verdict); percentages are over the "
        f"{summary['complete']} complete one(s). "
        f"{summary['incomplete_with_eligible']} of the incomplete had at least "
        f"one eligible trial — a lower bound, not a tier: a No Match cannot be "
        f"concluded from an evaluation that did not finish.")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Sep 13 2026

@author: ramyalsaffar
"""
