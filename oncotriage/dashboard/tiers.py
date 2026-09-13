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
MATCH_TIER_NO_MATCH = 'No Match'
"""The worst tier, named once.

IT WAS A LITERAL IN THREE PLACES -- ``MATCH_TIERS``, ``ANY_MATCH_TIERS``'s
derivation and ``assign_tier``'s fallback -- and the repair pass added a
fourth reader in the sidebar. A string typed out in four files is the shape
pass 20f-3 had to come back and fix for '✅ Full Match'."""

MATCH_TIERS = ['Full Match', 'Partial Match', 'Unconfirmed Match',
               MATCH_TIER_NO_MATCH]

MATCH_TIER_INCOMPLETE = 'Incomplete Evaluation'
"""The tier of a patient row whose evaluation cannot support a clinical tier.

IT IS NOT A MEMBER OF ``MATCH_TIERS``, AND THAT IS THE WHOLE POINT. The four
members of ``MATCH_TIERS`` are CLINICAL outcomes and they partition the rows
that were fully evaluated. A row that errored, or whose Stage 5 call failed for
some of its trials, was put in 'No Match' before the dashboard-truthfulness
pass -- measured on the smoke run, a patient with 12 of 15 trials with no usable verdict
read as a clinical No Match, and two errored retries read as two more. So this
value sits OUTSIDE the clinical vocabulary: every clinical percentage excludes
it, and every panel that shows tiers names it as its own count.

WHO DECIDES IT: ``oncotriage/dashboard/populations.py`` computes the
``evaluation_state`` column and ``enrich_match_tiers`` below reads it. The rule
needs the not-evaluable reason vocabulary, which this module cannot import
without ceasing to import nothing."""

MATCH_TIER_COLORS = {
    'Full Match':        '#2ca02c',
    'Partial Match':     '#ffbb33',
    'Unconfirmed Match': '#e67e22',
    'No Match':          '#d62728',
    'Incomplete Evaluation': '#7f7f7f',
}

ALL_MATCH_TIER_VALUES = tuple(MATCH_TIERS) + (MATCH_TIER_INCOMPLETE,)
"""Every value ``match_tier`` can hold, clinical tiers first. A TUPLE, on
``PATIENT_OUTCOME_LABELS``' argument: check 6a watches the two module-level
mutables and an immutable container adds nothing for it to watch. Charts that
group BY TIER use this, so an incomplete row is a visible category rather than
a row silently dropped by a ``reindex(MATCH_TIERS)``."""


# ---------------------------------------------------------------------------
# The evaluation-state vocabulary (the dashboard-truthfulness pass)
# ---------------------------------------------------------------------------
#
# Named here, beside the tier it decides, and COMPUTED in populations.py. A
# closed vocabulary: a state outside it is a defect in the producer.
EVALUATION_COMPLETE = 'complete'
"""Every trial sent to Stage 5 carries a usable result, including clinical
uncertainty -- a definite eligibility verdict or a model-declared uncertainty. The ONLY state a clinical tier is computed for."""

EVALUATION_ERRORED = 'errored'
"""The inference row carries an error: the pipeline did not finish this
attempt, so it holds no verdicts to tier."""

EVALUATION_MISSING_VERDICTS = 'missing_verdicts'
"""The attempt finished and at least one trial has no usable verdict -- a failed
call, an unusable response, a not-evaluable row whose reason cannot be
classified, or fewer trial rows than trials evaluated. NOT the mere presence of
a not_evaluable row: a trial the model itself declared not evaluable is a
legitimate clinical result and leaves the evaluation complete."""

EVALUATION_STATES = (EVALUATION_COMPLETE, EVALUATION_ERRORED,
                     EVALUATION_MISSING_VERDICTS)

EVALUATION_STATE_COLUMN = 'evaluation_state'
"""The column ``populations.annotate_evaluation_state`` writes and
``enrich_match_tiers`` reads."""

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

# NOT EVALUATED IS ITS OWN STATUS, IN THREE KINDS (the dashboard-truthfulness
# pass). Every per-trial classifier in the dashboard read
# `if row['eligible'] != 'eligible': return TRIAL_STATUS_REJECTED`, so a trial
# whose Stage 5 REQUEST FAILED was displayed as a clinical rejection with a
# score of 0 -- measured on the smoke run, 64 of 98 trial rows. `not_evaluable`
# is not `not_eligible`, and the STORED REASON is what separates the two things
# it can mean; `populations.not_evaluated_kind` owns that reading.
TRIAL_STATUS_NOT_EVALUATED_FAILED = '⚠️ Not Evaluated — no usable verdict'
"""The pipeline never obtained a usable verdict: the call failed, the response was
truncated or omitted, or the response could not be used as written."""

TRIAL_STATUS_NOT_EVALUABLE_UNCERTAIN = '❔ Not Evaluable — clinical uncertainty'
"""The model itself declared the trial not evaluable for this patient. A
legitimate clinical result, and deliberately a different label from a failure."""

TRIAL_STATUS_NOT_EVALUATED_UNKNOWN = '⚪ Not Evaluated — reason unknown'
"""A not_evaluable row whose reason was never recorded (a row written before the
column) or is not a value this pipeline writes. Neither a failure nor an
uncertainty can be asserted, so it is named as neither."""

TRIAL_STATUS_UNRECOGNISED = '❓ Unrecognised Verdict'
"""An ``eligible`` value outside eligible / not_eligible / not_evaluable. It was
displayed as a rejection too; it is a record this dashboard cannot read."""

TRIAL_STATUS_ORDER = (
    '✅ Eligible', TRIAL_STATUS_PARTIAL, TRIAL_STATUS_UNCONFIRMED,
    TRIAL_STATUS_REJECTED, TRIAL_STATUS_NO_SCORE,
    TRIAL_STATUS_NOT_EVALUABLE_UNCERTAIN, TRIAL_STATUS_NOT_EVALUATED_FAILED,
    TRIAL_STATUS_NOT_EVALUATED_UNKNOWN, TRIAL_STATUS_UNRECOGNISED,
)
"""Every per-trial display status, in the order a trial table sorts them.
Definite eligibility verdicts first; every not-evaluated kind after them."""


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
PATIENT_OUTCOME_INCOMPLETE = '⚪ Incomplete Evaluation'
"""The display form of ``MATCH_TIER_INCOMPLETE``. Deliberately NOT in
``PATIENT_OUTCOME_LABELS``, which is zipped with the four CLINICAL tiers."""

if len(PATIENT_OUTCOME_LABELS) != len(MATCH_TIERS):
    raise RuntimeError(
        f"the per-patient label vocabulary has {len(PATIENT_OUTCOME_LABELS)} "
        f"entries and MATCH_TIERS has {len(MATCH_TIERS)}: "
        f"{MATCH_TIERS!r}. They are zipped together, so they must correspond."
    )


# ===========================================================================
# "ANY MATCH": ONE MEANING, ONE DERIVATION, ONE OWNER (the dashboard-fixes pass)
# ===========================================================================
#
# THE MEANING, SETTLED BEFORE THE OWNER. "Any Match" is a PER-PATIENT outcome
# and it is the complement of the worst tier: a patient is an Any Match exactly
# when `enrich_match_tiers` put them in Full, Partial or Unconfirmed. It is
# DERIVED from MATCH_TIERS rather than written out, so a fifth tier joins it by
# being added once.
#
# TWO SURFACES HAD TWO DERIVATIONS AND NEITHER NAMED THE OTHER:
#
#   tabs/overview.py     full_rate + partial_rate + unconfirmed_rate
#                        -- three percentages summed, tier-based;
#   tabs/demographics.py (df['eligible_matches'] > 0).mean() * 100
#                        -- the stored COLUMN, which is a count Stage 6 wrote.
#
# ON THE PRODUCTION TABLE THEY AGREE TO FOUR DECIMAL PLACES (97.1971% both,
# 1,106 rows, zero disagreeing rows -- measured, not assumed), which is exactly
# why this went unnoticed. They are not the same question and they come apart
# on shapes the pipeline really produces:
#
#   * `trial_matches` empty or not yet written, with `eligible_matches > 0` on
#     the inference rows. `enrich_match_tiers` assigns every patient
#     'No Match'; the column says they matched. 0% against N%.
#   * an eligible trial row whose `match_score` is NULL. Every tier bucket
#     compares against the score and NaN fails all three, so the patient is
#     'No Match' with `eligible_matches` counting the trial.
#   * `eligible_matches` NULL -- a row written before the column, or by a
#     failure return. `NaN > 0` is False, so the column says no match while the
#     tiers may say otherwise.
#
# WHY THE TIER SIDE IS THE MEANING AND NOT THE COLUMN. Every other tile in both
# panels -- Full, Partial, Unconfirmed, No Match -- is `match_tier`-based, so
# "Any Match" derived from a different source is a total that does not have to
# equal its own parts. It did not: the overview tile summed three
# tier-percentages while the demographics tile beside FOUR tier-percentages
# read the column. A total and its parts computed from two sources is the
# defect, whichever source is "righter".

ANY_MATCH_TIERS = tuple(t for t in MATCH_TIERS if t != MATCH_TIER_NO_MATCH)
"""The tiers that count as a match. DERIVED, so a new tier is included by
default and EXCLUDED only by a deliberate edit -- the safe direction for a
figure that is a complement."""

ANY_MATCH_COLUMN = "any_match"
"""The boolean column ``any_match_series`` produces.

NAMED because ``tabs/demographics.py`` has to aggregate it -- ``DataFrame.agg``
on a single column cannot see a second one, so the per-group match rates need
the predicate materialised rather than recomputed inside six lambdas."""


def any_match_series(df):
    """A boolean Series: is each row a patient with at least one eligible trial.

    THE ONE DERIVATION. Every "Any Match" figure in the dashboard is this
    Series counted, averaged or grouped.

    RAISES when ``match_tier`` is absent, and does NOT fall back to
    ``eligible_matches``. A fallback would be the second derivation this
    function exists to remove, reachable exactly when a caller forgot
    ``enrich_match_tiers`` -- and it would then disagree with every tier tile
    beside it while looking like it had worked.
    """
    if 'match_tier' not in df.columns:
        raise KeyError(
            "any_match_series: the frame has no 'match_tier' column. Every "
            "dashboard frame is enriched by enrich_match_tiers() in "
            "oncotriage/dashboard/app.py:main() before any tab sees it; a "
            "frame without it has not been through that call."
        )
    return df['match_tier'].isin(ANY_MATCH_TIERS)


def any_match_count(df) -> int:
    """How many rows in ``df`` are a clinical Any Match.

    An incomplete row is never counted: ``MATCH_TIER_INCOMPLETE`` is not in
    ``ANY_MATCH_TIERS``. Which ROWS ``df`` holds -- every inference, or one first
    attempt per patient -- is the caller's population and the caller's label.
    """
    return int(any_match_series(df).sum())


def clinical_rows(df):
    """The rows of ``df`` a clinical tier can be computed for.

    Every row whose ``match_tier`` is not ``MATCH_TIER_INCOMPLETE``. Raises on a
    frame with no ``match_tier`` for ``any_match_series``' reason.
    """
    if 'match_tier' not in df.columns:
        any_match_series(df)                      # raises the named KeyError
    return df[df['match_tier'] != MATCH_TIER_INCOMPLETE]


def any_match_rate(df) -> float:
    """The Any Match percentage over the COMPLETE rows of ``df``, or NaN.

    THE DENOMINATOR EXCLUDES INCOMPLETE EVALUATIONS (the dashboard-truthfulness
    pass). It was every row, so an errored retry and a patient whose calls
    failed both counted as a clinical non-match.

    NaN AND NOT 0.0 WHEN THERE IS NO COMPLETE ROW. 0.0% asserts that none of the
    patients matched, which is a measurement, and there is nobody complete to
    have measured. Callers render NaN through ``rate_text``.
    """
    complete = clinical_rows(df)
    if len(complete) == 0:
        return float('nan')
    return float(any_match_series(complete).mean() * 100)


def rate_text(rate, spec=".1f"):
    """A percentage for a tile, or "—" when it is undefined (NaN)."""
    if rate != rate:                               # NaN, with no numpy import
        return "—"
    return f"{format(rate, spec)}%"


TRIAL_MISSING_TITLE_LABEL = "(no title recorded)"
"""How a trial with no recorded title is NAMED on a panel that lists trials.

A LABEL AND NOT A DROP, AND THE DROP IS WHAT USED TO HAPPEN. A panel that
grouped by ``['nct_id', 'trial_title']`` lost rows silently, because
``DataFrame.groupby`` discards NaN group keys by default:

    a trial whose every row had a NULL title VANISHED from the panel entirely
        -- no entry, no error, and every patient evaluated against it
        unreachable from it;

    a trial with a MIXED title -- some rows recorded, some NULL -- appeared
        ONCE carrying only its titled rows, so its count was silently short by
        the untitled ones. Measured on a two-patient fixture: 1 shown, 2 real.

Both are reproduced in ``tests/test_dashboard_repair_pass.py``. Neither
raised, which is why neither had been noticed.

AN EARLIER REPORT BLAMED ``r['trial_title'][:55]`` FOR RAISING ON A NULL, and
that was wrong: nothing with a NULL title ever reached that slice, because the
groupby had already dropped it. The slice is safe for a different reason --
the title it slices is THIS LABEL when nothing was recorded.

IT LIVES HERE RATHER THAN IN THE TAB THAT FIRST NEEDED IT, because a SECOND
panel needs it. The Trial Explorer's selector and Match Quality's "Top Matched
Trials" both name a trial, and a user-visible label typed out in two files is
the shape ``PATIENT_OUTCOME_FULL`` above was introduced to remove -- there the
two copies had already drifted into two vocabularies that happened to share a
string."""


def display_trial_title(titles) -> str:
    """The title to SHOW for one trial, given that trial's own recorded titles.

    THE FIRST RECORDED ONE AMONG THEM, so a trial with a mixed title is named
    by the title it HAS rather than by the absence of one, and a trial with
    none at all is named by ``TRIAL_MISSING_TITLE_LABEL`` rather than dropped.
    Either way it is ONE entry, which is what makes the trial ID alone a usable
    group key.

    A RECORDED TITLE IS A NON-BLANK STRING, and that definition is the whole
    of the second defect this function had to be widened for. The first
    version asked ``first_valid_index()``, which answers "not NaN" -- and
    ``storage/database_logger.py`` writes ``match.get("title", "")``, so the
    value THIS PIPELINE produces for a trial with no title is the EMPTY
    STRING and never NaN. Measured consequences of the narrower reading:

        ``groupby`` does NOT discard an empty-string key, it is a perfectly
            good one -- so on the pair the empty-title rows were never
            DROPPED, they were SPLIT off into a second entry. Rows preserved,
            identity halved, which is the quieter half of the same defect and
            the ONLY half reachable from this project's own writer;

        ``first_valid_index()`` calls ``""`` valid, so a trial carrying a
            recorded title on one row and the writer's default on another was
            named by whichever came first -- a BLANK cell beside a title the
            database holds.

    Whitespace-only is blank too: ``"   "`` is not a title, and a panel that
    printed it would show an entry a reader cannot name.

    THE SURVIVING VALUE IS RETURNED VERBATIM, never stripped: this renders
    RECORDED data, and ``.strip()`` is used to decide emptiness rather than to
    edit what the database holds.

    A NON-STRING IS NOT A TITLE EITHER, which is a deliberate narrowing of the
    old ``str(...)`` coercion: NaN, None and ``pandas.NA`` are all skipped by
    the same test, with no import and no equality comparison -- ``value !=
    value`` catches NaN and RAISES on ``pandas.NA``, whose truthiness is
    undefined.

    A group with no recorded title anywhere -- including an EMPTY group, which
    ``dropna().iloc[0]`` would raise IndexError on -- answers the label.

    IT TAKES THE SERIES ``groupby(...).agg`` HANDS AN AGGREGATOR and only
    iterates it, so this module still imports nothing at all.
    """
    for value in titles:
        if isinstance(value, str) and value.strip():
            return value
    return TRIAL_MISSING_TITLE_LABEL


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
        df['match_tier'] = MATCH_TIER_NO_MATCH
        return apply_evaluation_state(df)

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
        return MATCH_TIER_NO_MATCH

    df['match_tier'] = df.apply(assign_tier, axis=1)

    return apply_evaluation_state(df)


def apply_evaluation_state(df):
    """Overwrite ``match_tier`` with ``MATCH_TIER_INCOMPLETE`` where it applies.

    Reads ``EVALUATION_STATE_COLUMN`` when the caller computed it -- which
    ``oncotriage/dashboard/app.py:main()`` does before calling
    ``enrich_match_tiers`` -- and leaves the four clinical tiers untouched when
    the column is absent, which is every caller that has not asked the question.
    The three match counts are left as computed: they describe what the trial
    rows say, and the Patient Explorer shows them for an incomplete row too.
    """
    if EVALUATION_STATE_COLUMN in df.columns:
        incomplete = df[EVALUATION_STATE_COLUMN] != EVALUATION_COMPLETE
        df.loc[incomplete, 'match_tier'] = MATCH_TIER_INCOMPLETE
    return df


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
