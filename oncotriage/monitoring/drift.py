# Drift Detection for OncoTriage Agent
######################################

"""Has the pipeline moved, and against WHAT was that decided.

THE TWO DEFECTS THIS FILE WAS REDESIGNED AROUND, both measured before anything
was edited and both of them silent
-------------------------------------------------------------------------------
1. THE BASELINE WAS SELECTED BY TIME WINDOW. ``get_baseline_and_current_data``
   took the EARLIEST rows in ``inferences`` as the baseline and the LATEST as
   the comparison. Which rows the pipeline was measured against was therefore
   decided by insertion order; the two windows could straddle a prompt bump, a
   re-index or a model flip, so every configuration difference arrived as
   drift; and -- the fatal one -- A BASELINE CAPTURED AFTER THE THING WENT
   WRONG READS AS NO DRIFT AT ALL. That last is the exact failure
   ``ecog_unavailable_rate`` already refuses to be exposed to, in as many
   words, by being a threshold rather than a comparison. This file now applies
   that argument to the comparisons as well: a comparison is worth something
   only if a PERSON chose what it compares against, and the choice is recorded
   well enough to be checked later.

2. EVERY METRIC THAT COULD NOT RUN WROTE ``alert = 0``. A PSI over a column of
   NaNs, a KS test with one sample a side, a z-score whose baseline had no
   variance, a metric whose column the database does not have -- every early
   return of every metric wrote the same byte a healthy reading writes, and the
   dashboard rendered all of them as a green tick. "This was measured and it is
   fine" and "this was never measured" were one value.

WHAT REPLACES THEM
------------------
    SELECTION      ``oncotriage/monitoring/drift_reference.py`` -- a designated
                   reference campaign, recorded with its run ids, its row ids,
                   its fingerprint, its tunables and a CONTENT DIGEST over the
                   columns the metrics read. Resolution re-checks the ids AND
                   the digest, so a reference whose rows were edited in place
                   is a named refusal rather than a silent comparison.
    STATE          ``oncotriage/monitoring/drift_states.py`` -- two axes,
                   stored separately. STATUS says what the computation did;
                   POLICY says whether an alert is a possible outcome at all.
                   ``alert`` is NULL unless both say it means something.

WHAT ALERTS, AND IT IS ONE METRIC
---------------------------------
``ecog_unavailable_rate``, whose policy is ``baseline_independent``. EVERY
COMPARISON METRIC SHIPS ``reporting_only`` BY RULING: their thresholds are
industry defaults calibrated against nothing in this pipeline, and an
uncalibrated threshold produces alerts an operator learns to ignore. They are
computed, stored, rendered and compared by a human; what they do not do is
assert that a number is bad. The thresholds are still stored beside every
reading, because comparing a value against a published convention is exactly
what a reporting-only metric is for.

THE ORDER OF THE PIPELINE IS A CORRECTNESS PROPERTY
----------------------------------------------------
Availability computes and logs BEFORE the reference is resolved. It consults no
reference, so a reference failure must not be able to take it down -- and under
the shipped order it did: step 1 loaded the two windows and RAISED on
insufficient data, so the one metric that could have answered on a young
database was the one that never ran. A reference failure now downgrades exactly
the metrics that consume a reference, and every other reading is unaffected.

WHAT IMPORTING THIS MODULE DOES
-------------------------------
Nothing observable: no connection, no path resolution, no query. ``scipy.stats``
is imported at module scope, which is the one thing that costs anything, and it
has to be -- the availability flag is the module's answer to "can this run", and
deferring the import into a function body would mean the flag could not be read
until after the first call that needed it.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
ALERT CALIBRATION, deferred by ruling. Nothing in this file chooses a threshold
from this pipeline's own data. The reporting-only metrics are the instrument
that would make such a choice possible; making it is a separate act with its own
measurement, and shipping a guessed threshold in the meantime is what the
policy axis exists to prevent.
"""

import sqlite3
import traceback
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from oncotriage import paths
from oncotriage import settings
from oncotriage.config import (
    ECOG_UNAVAILABLE_RATE_THRESHOLD,
    KS_TEST_THRESHOLD,
    MIN_SAMPLES_BASELINE,
    MIN_SAMPLES_COMPARISON,
    PSI_BINS,
    PSI_THRESHOLD,
    RRF_POOL_SIZE,
    TOP_K_CANDIDATES,
    Z_SCORE_THRESHOLD,
)
from oncotriage.monitoring import drift_reference as _reference
from oncotriage.storage.database_logger import (
    campaign_run_ids as _campaign_run_ids,
)
from oncotriage.monitoring.drift_states import (
    ALERT_INVALID,
    POLICY_BASELINE_INDEPENDENT,
    POLICY_REPORTING_ONLY,
    STATUS_COMPUTED,
    STATUS_INSUFFICIENT_DATA,
    STATUS_MALFORMED_INPUTS,
    STATUS_NO_DATA,
    STATUS_NO_RUN_IDENTITY,
    STATUS_READ_FAILURE,
    STATUS_REFERENCE_ABSENT,
    STATUS_REFERENCE_MUTATED,
    STATUS_REFERENCE_UNRESOLVABLE,
    STATUS_REFUSED_COLUMN_ABSENT,
    STATUS_REFUSED_INCOMPARABLE,
    STATUS_UNVERIFIED_INPUTS,
    STATUS_ZERO_VARIANCE,
    display_label,
    require_policy,
    require_status,
    resolve_alert,
)
from oncotriage.observability import console


#------------------------------------------------------------------------------


# A REAL ImportError GUARD (pass 20c-3b).
#
# What this replaces, from "20- Drift Detection.py" lines 19-25:
#
#     # scipy is imported in 01- Imports.py (ks_2samp). This flag exists for
#     # environments where scipy might not be installed.
#     try:
#         ks_2samp  # verify it's in namespace (loaded by exec_chain)
#         SCIPY_AVAILABLE = True
#     except NameError:
#         SCIPY_AVAILABLE = False
#
# The comment stated the intent correctly -- "environments where scipy might not
# be installed" -- and the code tested something else entirely: whether a NAME
# happened to be present in a shared namespace somebody else had filled. Those
# two questions have different answers. A namespace that had never loaded
# "01- Imports.py" reported scipy missing on a machine where it was installed,
# and there was no arrangement in which the check could report the failure it
# was written for, because an absent scipy would have taken File 01 down first
# and File 20 would never have been reached.
#
# Now the import itself is the test. ks_2samp is bound to None on failure rather
# than left unbound: a caller that reaches past the flag then gets
# "TypeError: 'NoneType' object is not callable" pointing at the call site,
# instead of a NameError that points at nothing.
try:
    from scipy.stats import ks_2samp
    SCIPY_AVAILABLE = True
except ImportError:
    ks_2samp = None
    SCIPY_AVAILABLE = False


#------------------------------------------------------------------------------


def resolve_drift_db_path(db_path=None):
    """The database the drift functions read and write.

    Three tiers, first match wins, IDENTICAL to
    ``oncotriage/storage/database_logger.py:resolve_inference_db_path``:

        1. ``db_path`` -- an explicit argument, returned unmodified;
        2. ``ONCOTRIAGE_INFERENCES_DB`` (pass 20c-3i);
        3. ``oncotriage.paths.inferences_path``.

    STILL A SEPARATE FUNCTION rather than an import of that one, and pass
    20c-3i deliberately did not consolidate them while adding tier 2 to both.
    That one answers "where does the inference logger write"; this one answers
    "where does drift detection look". They resolve to the same file today, and
    the reason to keep them apart is that ``oncotriage.monitoring`` must not
    depend on ``oncotriage.storage`` for a path string -- drift detection reads
    a database, it does not use the logger. Both reach the variable through
    ``oncotriage.settings``, which is the module both already depend on
    transitively and the one place the variable is NAMED.

    THE DRIFT REDESIGN IMPORTS ``storage.database_logger`` TRANSITIVELY, through
    ``drift_reference``, AND THIS RULE IS UNCHANGED. What that module imports is
    the schema owner's VOCABULARY -- the campaign stitch rule and the
    fingerprint column list -- because the alternative is a second copy of both.
    It does not import a path, and neither does this: the resolution is here,
    and every function in ``drift_reference`` takes ``db_path`` as a REQUIRED
    argument for exactly that reason.

    Tier 2 is honoured HERE as well as there because the two tables live in one
    file. A run redirected at a scratch database for its inferences and left
    pointing at production for its drift metrics would write a verdict about
    data it had not read, into a table nobody asked it to touch -- which is a
    worse outcome than either half alone.

    THE ARGUMENT STILL WINS OVER THE VARIABLE.
    "tests/test_monitoring_ecog_availability_drift.py"
    passes an explicit scratch path and asserts on what
    ``log_drift_metrics`` returns; if a stray export outranked that argument the
    assertion would be reporting the export rather than the isolation it exists
    to check.

    It resolves and returns; it opens nothing. It can RAISE a RuntimeError from
    ``resolve_inferences_db`` when the variable names a path whose parent
    directory is absent; that is deliberate, and it is why the call sits before
    ``log_drift_metrics``'s try block.
    """
    if db_path is not None:
        return db_path
    override, _source = settings.resolve_inferences_db()
    if override is not None:
        return override
    return paths.inferences_path


#------------------------------------------------------------------------------


# ===========================================================================
# THE METRIC RECORD
# ===========================================================================

METRIC_KEYS = (
    "metric_value", "status", "policy", "threshold", "alert", "notes",
    "p_value", "baseline_mean", "baseline_std", "stratum",
    "sample_size", "reference_sample_size", "excluded", "counts",
)
"""Every key a metric result carries. TOTAL over every metric, ALWAYS.

ONE SHAPE FOR EVERY METRIC AND EVERY BRANCH OF EVERY METRIC. A consumer that
has to know which branch produced a dict in order to know which keys it has is
a consumer that will one day read the wrong branch's shape -- and the writer,
the console report and the dashboard are three such consumers. ``metric()``
below is the only constructor, so the shape cannot vary.
"""


def metric(status, policy, value=None, threshold=None, notes=None,
           alert=None, p_value=None, baseline_mean=None, baseline_std=None,
           stratum=None, sample_size=None, reference_sample_size=None,
           excluded=None, counts=None) -> Dict:
    """Build one metric result. THE ONLY CONSTRUCTOR.

    It VALIDATES both axes and RESOLVES the alert, so:

      * a status or policy outside its closed vocabulary raises here rather
        than reaching the database and being rendered under somebody's ``else``;
      * ``alert`` is ``None`` unless the two axes agree it means something,
        WHATEVER the caller passed. A metric that computes an alert and then
        reports ``insufficient_data`` has contradicted itself, and the safe
        reading of a contradiction is the one that does not assert a verdict;
      * a MISSING alert STAYS MISSING -- it is not converted to ``0``, which
        would read as OK -- and an INVALID one REJECTS THE READING as
        ``malformed_inputs``, whatever status the caller claimed. Neither is
        ever converted into a verdict. ``resolve_alert`` returns the outcome
        alongside the value precisely so this function cannot ignore the
        difference; the previous shape returned one value and turned every
        unreadable input into ``int(bool(...))``.

    THE REJECTION IS A DOWNGRADE AND NOT A RAISE. A metric body that produced
    a bad alert has a defect, and taking the whole drift run down over one
    reading would lose the other twenty -- including the only alerting one. The
    reading is stored saying what it is, which is what the status axis is for,
    and the caller's own status is REPLACED rather than kept: a result claiming
    ``computed`` beside an unreadable verdict is a result claiming something it
    has not established.

    ``excluded`` is a dict of ``{reason: count}`` for the rows a fraction did
    NOT divide -- a zero denominator, an unverifiable one, an absent value. It
    is stored in ``notes`` rather than as columns, and it is REQUIRED to be a
    dict so that "nothing was excluded" is ``{}`` and not ``None``: an empty
    dict is a measurement and ``None`` is a metric that did not look.

    ``counts`` is the metric's own DESCRIPTIVE numbers -- a numerator, a
    breakdown -- and it is a SEPARATE slot from ``excluded`` because the two
    answer different questions: ``excluded`` is "which rows did not take part",
    ``counts`` is "what did the rows that did take part measure". It exists so
    a consumer that needs more than the headline value gets it from THE ONE
    OWNER rather than re-deriving it: ``oncotriage/dashboard/tabs/performance.py``
    renders the ECOG numerator and its own comment forbids a second copy of the
    definition, and ``metric_value * sample_size`` is a float round trip rather
    than a count. Total over every metric, ``{}`` where there are none, for
    ``excluded``'s reason.
    """
    require_status(status)
    require_policy(policy)
    resolved, outcome = resolve_alert(status, policy, alert)
    if outcome == ALERT_INVALID:
        status = STATUS_MALFORMED_INPUTS
        value = None
        notes = _join_notes(
            notes,
            f"the alert this metric produced ({alert!r}) is not a verdict: "
            f"drift_metrics.alert holds 0, 1 or NULL. The reading is recorded "
            f"as malformed_inputs rather than converted into whichever verdict "
            f"Python's truthiness table would have made of it")
    return {
        "metric_value": value,
        "status": status,
        "policy": policy,
        "threshold": threshold,
        "alert": resolved,
        "notes": notes,
        "p_value": p_value,
        "baseline_mean": baseline_mean,
        "baseline_std": baseline_std,
        "stratum": stratum,
        "sample_size": sample_size,
        "reference_sample_size": reference_sample_size,
        "excluded": dict(excluded) if excluded is not None else {},
        "counts": dict(counts) if counts is not None else {},
    }


def _refusal(status, policy, detail, stratum=None) -> Dict:
    """A metric that did not run, carrying the reason in ``notes``."""
    return metric(status, policy, notes=detail, stratum=stratum)


#------------------------------------------------------------------------------


# ===========================================================================
# STATISTICAL FUNCTIONS
# ===========================================================================
#
# EACH RETURNS A STATUS AND NOT AN ALERT. The shipped versions each computed a
# threshold comparison and returned `alert: 0` from every early return -- a NaN
# column, a one-sample KS test, a zero-variance baseline -- so the byte that
# means "measured and fine" was written by code that had measured nothing.
# `metric()` is what resolves an alert now, from the two axes, once.
#
# THE THRESHOLD IS STILL RETURNED. A reporting-only reading beside the
# published convention it would have been judged against is more useful than
# the reading alone; what it is not is a verdict.


def calculate_psi(baseline: np.ndarray, current: np.ndarray,
                  policy=POLICY_REPORTING_ONLY, bins: int = PSI_BINS,
                  stratum=None) -> Dict:
    """Population Stability Index between two samples.

    Industry reading, which is why the threshold is stored beside the value:

        PSI < 0.1   no significant change
        PSI < 0.2   moderate change
        PSI >= 0.2  significant change

    ``0.0`` HAS TWO CAUSES AND THEY ARE NOT THE SAME READING. Two identical
    distributions score 0.0, and so does a sample in which every value is the
    same number -- because the binning collapses to one bin and the sum is zero
    by construction rather than by measurement. The second is
    ``not_computed_zero_variance``, and it is usually the more alarming of the
    two: a column that has SATURATED at one value is a pipeline that has
    stopped varying. The shipped version returned ``0.0`` with ``alert: 0`` for
    it, which rendered as a green tick.
    """
    try:
        baseline_clean = baseline[~np.isnan(baseline)]
        current_clean = current[~np.isnan(current)]

        if len(baseline_clean) == 0 or len(current_clean) == 0:
            return metric(
                STATUS_NO_DATA, policy, threshold=PSI_THRESHOLD,
                stratum=stratum,
                sample_size=int(len(current_clean)),
                reference_sample_size=int(len(baseline_clean)),
                notes="every value on at least one side is NaN")

        min_val = min(baseline_clean.min(), current_clean.min())
        max_val = max(baseline_clean.max(), current_clean.max())

        if max_val == min_val:
            # SATURATION IS NAMED. The value would be 0.0 and it is withheld:
            # a 0.0 here is not a reading of stability, it is the arithmetic of
            # a single bin.
            return metric(
                STATUS_ZERO_VARIANCE, policy, threshold=PSI_THRESHOLD,
                stratum=stratum,
                sample_size=int(len(current_clean)),
                reference_sample_size=int(len(baseline_clean)),
                notes=f"every value on both sides is {float(min_val)!r}; PSI "
                      f"over a single bin is 0.0 by construction, not by "
                      f"measurement. A column that has saturated is usually "
                      f"the finding, not the absence of one.")

        bin_edges = np.linspace(min_val, max_val, bins + 1)
        baseline_counts, _ = np.histogram(baseline_clean, bins=bin_edges)
        current_counts, _ = np.histogram(current_clean, bins=bin_edges)

        baseline_props = (baseline_counts + 1e-6) / (baseline_counts.sum() + bins * 1e-6)
        current_props = (current_counts + 1e-6) / (current_counts.sum() + bins * 1e-6)

        psi_value = float(np.sum((current_props - baseline_props)
                                 * np.log(current_props / baseline_props)))

        return metric(
            STATUS_COMPUTED, policy, value=psi_value,
            threshold=PSI_THRESHOLD, stratum=stratum,
            alert=psi_value >= PSI_THRESHOLD,
            sample_size=int(len(current_clean)),
            reference_sample_size=int(len(baseline_clean)))

    except Exception as e:                                 # noqa: BLE001
        return metric(STATUS_READ_FAILURE, policy, threshold=PSI_THRESHOLD,
                      stratum=stratum,
                      notes=f"PSI calculation error: {type(e).__name__}: {e}")


def ks_test_drift(baseline: np.ndarray, current: np.ndarray,
                  policy=POLICY_REPORTING_ONLY, stratum=None) -> Dict:
    """Two-sample Kolmogorov-Smirnov test for a distribution shift.

    ``metric_value`` is the KS statistic and ``p_value`` is the test's p; the
    threshold is on the p, which is why a reader comparing ``metric_value``
    against ``threshold`` would be comparing two different things. That was
    true of the shipped version too and it is stated here rather than left to
    be discovered, because the reporting-only rendering puts the two side by
    side in the same row.
    """
    if not SCIPY_AVAILABLE:
        return metric(
            STATUS_READ_FAILURE, policy, threshold=KS_TEST_THRESHOLD,
            stratum=stratum,
            notes="scipy is not installed, so no KS test can be run. This is "
                  "a missing dependency rather than a property of the data: "
                  "pip install scipy.")

    try:
        baseline_clean = baseline[~np.isnan(baseline)]
        current_clean = current[~np.isnan(current)]

        if len(baseline_clean) < 2 or len(current_clean) < 2:
            return metric(
                STATUS_INSUFFICIENT_DATA, policy, threshold=KS_TEST_THRESHOLD,
                stratum=stratum,
                sample_size=int(len(current_clean)),
                reference_sample_size=int(len(baseline_clean)),
                notes=f"KS needs >= 2 non-NaN values a side; got "
                      f"{len(baseline_clean)} reference and "
                      f"{len(current_clean)} comparison")

        ks_statistic, p_value = ks_2samp(baseline_clean, current_clean)

        return metric(
            STATUS_COMPUTED, policy, value=float(ks_statistic),
            p_value=float(p_value), threshold=KS_TEST_THRESHOLD,
            stratum=stratum, alert=p_value < KS_TEST_THRESHOLD,
            sample_size=int(len(current_clean)),
            reference_sample_size=int(len(baseline_clean)))

    except Exception as e:                                 # noqa: BLE001
        return metric(STATUS_READ_FAILURE, policy,
                      threshold=KS_TEST_THRESHOLD, stratum=stratum,
                      notes=f"KS test error: {type(e).__name__}: {e}")


def z_score_drift(baseline: np.ndarray, current: np.ndarray,
                  policy=POLICY_REPORTING_ONLY, stratum=None) -> Dict:
    """How many reference standard deviations the comparison mean has moved.

    A ZERO-VARIANCE REFERENCE IS ``not_computed_zero_variance`` AND NOT AN
    ERROR. The shipped version returned ``metric_value: None`` with
    ``alert: 0`` and a note about a small standard deviation, which rendered as
    a green tick over the case where the reference column never varied -- most
    often because the column was not being written at all. Naming it is what
    lets a reader tell "the pipeline is stable" from "this column is dead".
    """
    try:
        baseline_clean = baseline[~np.isnan(baseline)]
        current_clean = current[~np.isnan(current)]

        if len(baseline_clean) < 2 or len(current_clean) < 1:
            return metric(
                STATUS_INSUFFICIENT_DATA, policy, threshold=Z_SCORE_THRESHOLD,
                stratum=stratum,
                sample_size=int(len(current_clean)),
                reference_sample_size=int(len(baseline_clean)),
                notes=f"a z-score needs >= 2 reference and >= 1 comparison "
                      f"non-NaN values; got {len(baseline_clean)} and "
                      f"{len(current_clean)}")

        baseline_mean = float(np.mean(baseline_clean))
        baseline_std = float(np.std(baseline_clean, ddof=1))
        current_mean = float(np.mean(current_clean))

        if baseline_std < 1e-10:
            return metric(
                STATUS_ZERO_VARIANCE, policy, threshold=Z_SCORE_THRESHOLD,
                baseline_mean=baseline_mean, baseline_std=baseline_std,
                stratum=stratum,
                sample_size=int(len(current_clean)),
                reference_sample_size=int(len(baseline_clean)),
                notes=f"the reference has no variance (sd={baseline_std!r}), "
                      f"so a z-score is undefined. Reference mean "
                      f"{baseline_mean!r}, comparison mean {current_mean!r} -- "
                      f"compare those two directly; a column that never varies "
                      f"is usually the finding.")

        z_score = (current_mean - baseline_mean) / baseline_std

        return metric(
            STATUS_COMPUTED, policy, value=float(z_score),
            threshold=Z_SCORE_THRESHOLD, baseline_mean=baseline_mean,
            baseline_std=baseline_std, stratum=stratum,
            alert=abs(z_score) > Z_SCORE_THRESHOLD,
            sample_size=int(len(current_clean)),
            reference_sample_size=int(len(baseline_clean)))

    except Exception as e:                                 # noqa: BLE001
        return metric(STATUS_READ_FAILURE, policy,
                      threshold=Z_SCORE_THRESHOLD, stratum=stratum,
                      notes=f"Z-score calculation error: {type(e).__name__}: {e}")


#------------------------------------------------------------------------------


# ===========================================================================
# THRESHOLD ALERTS (no reference consulted)
# ===========================================================================

# Text carried in `notes` when the rate alerts. It reaches
# drift_metrics.notes, so the diagnosis is stored with the alert rather than
# only printed: whoever reads the row later needs to know what to check, and
# the number on its own does not say.
# THE SECOND CAUSE IS NAMED BECAUSE THE RATE STOPPED HAVING ONE. Until the ECOG
# pre-diagnosis pass, an unusable observation meant a date-handling problem and
# the only date that could cause it was the snapshot -- so a message that named
# DATA_SNAPSHOT_DATE and nothing else was a complete diagnosis. It is not any
# more: 'all_before_primary_diagnosis' rows are unusable for a CORRECTNESS
# reason, with a snapshot date that is perfectly correct, and an operator sent
# to check DATA_SNAPSHOT_DATE would find nothing wrong and have nowhere to go
# next. The message now names the breakdown as the discriminator rather than
# asserting one cause; the rate itself is right either way, because those
# patients' ECOG criteria really are not evaluable.
ECOG_UNAVAILABLE_DIAGNOSIS = (
    "Patients had an ECOG observation on file that could not be used. TWO "
    "different causes produce this, and the selection-path breakdown on the "
    "dashboard's Performance tab (or a GROUP BY ecog_selection) is what "
    "separates them. (1) A rate "
    "near 1.0 means DATA_SNAPSHOT_DATE (oncotriage/config.py) and the patient "
    "corpus "
    "disagree -- the corpus was regenerated with observations dated after the "
    "snapshot, so every one resolves to 'all_after_reference_date' and every "
    "ECOG criterion becomes not_evaluable. Check DATA_SNAPSHOT_DATE against the "
    "generated_at/observation dates in the corpus run manifest, then re-run "
    "the affected inferences. (2) 'all_before_primary_diagnosis' rows are NOT a "
    "date-handling fault: the observation is well-formed and inside the "
    "snapshot, and it was refused because it predates the patient's own cancer "
    "diagnosis. Nothing needs re-running for those; the corpus genuinely "
    "carries no post-diagnosis performance status for them."
)


def ecog_unavailable_rate(df: pd.DataFrame, stratum=None) -> Dict:
    """Fraction of reporting rows whose ECOG observation existed and was unusable.

        numerator   ecog_selection NOT NULL
                    AND ecog_selection <> 'none_recorded'
                    AND ecog_value IS NULL
        denominator ecog_selection NOT NULL

    THE ONE METRIC IN THIS MODULE THAT ALERTS, and its policy is
    ``baseline_independent`` rather than ``alerting`` because the distinction
    is load-bearing: both may alert, and only this one is still meaningful when
    the reference is absent, unresolvable or mutated. That is what lets a
    reference failure downgrade the reference-consuming metrics and leave this
    one alone -- a property a reader can CHECK on the stored row rather than
    take on trust.

    WHY A THRESHOLD AND NOT A COMPARISON, unchanged and now the model for the
    whole file: the failure this catches is a corpus regenerated with a
    DATA_SNAPSHOT_DATE older than its own observations. Every patient resolves
    to 'all_after_reference_date', every ECOG criterion becomes not_evaluable,
    and eligible-match counts fall across the board. A z-score against a
    reference would read ~0 if the reference were itself captured after that
    regeneration -- the metric would go silent in exactly the case it exists
    for. A proportion is alarming at 1.0 whatever the reference was.

    Two exclusions, and they are not the same exclusion:

      - Rows with ecog_selection NULL leave the DENOMINATOR. Nothing is known
        about their ECOG, and counting them as "fine" would dilute the rate
        toward zero exactly when the corpus is oldest.
      - Rows with ecog_selection = 'none_recorded' stay in the denominator but
        leave the NUMERATOR. Those patients genuinely carried no observation,
        which is a property of the source data, not a failure of this pipeline.
        A corpus where nobody has an ECOG scores 0.0 here, correctly: there is
        no reference-date mismatch to report.

    THE NUMERATOR IS DERIVED, NOT ENUMERATED, and that is what made it survive
    the pre-diagnosis pass with no edit. It is "reported, not 'none_recorded',
    no value" -- so a selection path added to
    ``oncotriage.constants.ECOG_SELECTION_VALUES`` joins it by construction the
    moment the parser starts writing it, which is correct: every such path means
    an observation existed and no grade came out of it.

    WHAT THAT COSTS, stated because the alert TEXT is narrower than the metric:
    the notes name a reference-date mismatch as the cause, and a corpus whose
    ECOGs mostly predate their diagnoses would raise the same alert for a
    different reason. The METRIC is right either way, and the selection-path
    breakdown in ``oncotriage/dashboard/tabs/performance.py`` separates them.

    IT REFUSES THROUGH THE SHARED VOCABULARY. The shipped version had a fixed
    two-state answer -- a rate, or "insufficient" -- so an absent column, a
    malformed one and a raise all arrived as the same ``alert: 0``. Each is its
    own status now, because each has its own remedy: migrate the database, look
    at what wrote the column, read the exception.
    """
    policy = POLICY_BASELINE_INDEPENDENT
    try:
        # `sample_size` IS THE DENOMINATOR ON EVERY BRANCH, and that is not a
        # detail: a key that means "reporting rows" on one branch and "rows in
        # the frame" on another is a key a consumer has to know the branch to
        # read, which is the shape `metric()` exists to remove. It is None here
        # because nothing was computed over anything, 0 where the denominator
        # is genuinely empty, and the reporting-row count otherwise.
        missing = [c for c in ("ecog_selection", "ecog_value")
                   if c not in df.columns]
        if missing:
            return metric(
                STATUS_REFUSED_COLUMN_ABSENT, policy,
                threshold=ECOG_UNAVAILABLE_RATE_THRESHOLD, stratum=stratum,
                excluded={"rows_not_examined": int(len(df))},
                counts={},
                notes=f"column(s) {missing} absent -- this database predates "
                      f"the ecog_* migration in "
                      f"oncotriage/storage/database_logger.py")

        reported = df["ecog_selection"].notna()
        denominator = int(reported.sum())
        rows_pre_migration = int((~reported).sum())

        if denominator == 0:
            return metric(
                STATUS_NO_DATA, policy,
                threshold=ECOG_UNAVAILABLE_RATE_THRESHOLD, stratum=stratum,
                sample_size=0,
                excluded={"no_selection_path_recorded": rows_pre_migration},
                counts={"reporting": 0, "unusable": 0,
                        "no_selection_path_recorded": rows_pre_migration},
                notes=f"no row reports an ECOG selection path; all "
                      f"{rows_pre_migration} predate the ecog_* columns or "
                      f"were written by a caller outside the graph")

        if denominator < MIN_SAMPLES_COMPARISON:
            # A denominator of 1 that happens to be unusable is a rate of 1.0
            # on one patient, which is noise wearing the costume of the exact
            # alarm this metric raises.
            return metric(
                STATUS_INSUFFICIENT_DATA, policy,
                threshold=ECOG_UNAVAILABLE_RATE_THRESHOLD, stratum=stratum,
                sample_size=denominator,
                excluded={"no_selection_path_recorded": rows_pre_migration},
                counts={"reporting": denominator,
                        "no_selection_path_recorded": rows_pre_migration},
                notes=f"only {denominator} row(s) report an ECOG selection "
                      f"path (need >= {MIN_SAMPLES_COMPARISON})")

        no_observation = reported & (df["ecog_selection"] == "none_recorded")
        # `ecog_value` MUST BE TESTED FOR NULLNESS AND NOT FOR TRUTH. ECOG 0 is
        # `fully active` -- the most eligible score there is -- and it is falsy.
        unusable = reported & ~no_observation & df["ecog_value"].isna()

        numerator = int(unusable.sum())
        rate = numerator / denominator
        alert = rate > ECOG_UNAVAILABLE_RATE_THRESHOLD

        return metric(
            STATUS_COMPUTED, policy, value=float(rate),
            threshold=ECOG_UNAVAILABLE_RATE_THRESHOLD, stratum=stratum,
            alert=alert, sample_size=denominator,
            excluded={"no_selection_path_recorded": rows_pre_migration,
                      "no_observation_on_file": int(no_observation.sum())},
            counts={"reporting": denominator, "unusable": numerator,
                    "no_selection_path_recorded": rows_pre_migration,
                    "no_observation_on_file": int(no_observation.sum())},
            notes=(f"{numerator}/{denominator} reporting rows had an unusable "
                   f"ECOG observation. {ECOG_UNAVAILABLE_DIAGNOSIS}")
                  if alert else
                  (f"{numerator}/{denominator} reporting rows had an unusable "
                   f"ECOG observation."))

    except Exception as e:                                 # noqa: BLE001
        return metric(STATUS_READ_FAILURE, policy,
                      threshold=ECOG_UNAVAILABLE_RATE_THRESHOLD,
                      stratum=stratum,
                      notes=f"ECOG availability calculation error: "
                            f"{type(e).__name__}: {e}")


#------------------------------------------------------------------------------


# ===========================================================================
# REPORTING-ONLY: UNDERFILL AND TRIALS LOST
# ===========================================================================
#
# THREE METRICS OVER ONE POPULATION, NO REFERENCE CONSULTED TO COMPUTE THEM.
# Each is a fraction whose denominator comes from the RUN that produced the row
# rather than from whatever `oncotriage/config.py` says today -- which is the
# whole reason `runs.tunables` exists. A campaign run six months ago at
# RRF_POOL_SIZE = 50 must be read against 50, and reading it against today's
# 100 would report a pool half empty that was in fact full.
#
# THE DENOMINATOR IS RESOLVED PER ROW AND NOT PER CAMPAIGN, and that is not
# fastidiousness: `tunables` is deliberately NOT a fingerprint field, so two
# runs of ONE campaign may legitimately carry different values. A per-campaign
# denominator would be a value taken from one run and applied to another's rows.
#
# NOTHING IS EVER DIVIDED BY A DENOMINATOR THAT IS ZERO, ABSENT OR
# UNVERIFIABLE. Such a row leaves the fraction and is COUNTED in its own bucket,
# and the buckets are reported with the value. A metric that silently dropped
# them would report a mean over a population it had chosen and not named.

EXCLUDED_NO_VALUE = "no_value_recorded"
EXCLUDED_ZERO_DENOMINATOR = "zero_denominator"
EXCLUDED_UNVERIFIED_DENOMINATOR = "denominator_unverifiable"
EXCLUDED_MALFORMED = "malformed_value"
EXCLUDED_OVER_DENOMINATOR = "value_exceeds_denominator"

EXCLUSION_REASONS = (EXCLUDED_NO_VALUE, EXCLUDED_ZERO_DENOMINATOR,
                     EXCLUDED_UNVERIFIED_DENOMINATOR, EXCLUDED_MALFORMED,
                     EXCLUDED_OVER_DENOMINATOR)
"""Why a row left a fraction. CLOSED, and every member is REPORTED.

FIVE AND NOT ONE, because the remedies differ and because three of them are
findings in their own right. ``zero_denominator`` on ``rerank_underfill`` means
retrieval returned nothing for that patient -- which is the most interesting
thing the row has to say and would be invisible as a silent skip.
``value_exceeds_denominator`` cannot happen if the pipeline is behaving; a
non-zero count there is a data-integrity finding, not a rounding artefact.
"""


def _positive_int(value):
    """``value`` as a positive int, or ``None`` when it is not one.

    ``bool`` IS EXCLUDED EXPLICITLY. ``isinstance(True, int)`` is True in
    Python, so a tunable that arrived as ``True`` would otherwise be read as a
    pool size of 1 and every row would report 0% underfill against a pool of
    one. This project has had to make that exclusion in four other places for
    the same reason.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(value) if value > 0 and float(value).is_integer() else None
    return None


def _non_negative_int(value):
    """``value`` as a non-negative int, or ``None``. ``bool`` excluded, as above."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 and float(value).is_integer() else None
    return None


class FractionOverRows:
    """Accumulates a per-row fraction and the reasons rows left it.

    A CLASS AND NOT A LIST COMPREHENSION because the exclusions are the half
    that matters: a mean is only interpretable beside the count of what was NOT
    in it, and keeping the two in one object is what stops a caller reporting
    one without the other.
    """

    def __init__(self):
        self.values: List[float] = []
        self.excluded: Dict[str, int] = {}

    def exclude(self, reason):
        if reason not in EXCLUSION_REASONS:
            raise ValueError(f"{reason!r} is not in EXCLUSION_REASONS")
        self.excluded[reason] = self.excluded.get(reason, 0) + 1

    def add(self, numerator, denominator):
        """One row's fraction, or an exclusion naming why there is none."""
        den = _positive_int(denominator)
        if den is None:
            self.exclude(EXCLUDED_ZERO_DENOMINATOR)
            return
        num = _non_negative_int(numerator)
        if num is None:
            self.exclude(EXCLUDED_MALFORMED)
            return
        if num > den:
            # NOT CLAMPED. Clamping would hide a row that contradicts its own
            # denominator inside a plausible mean; counting it names a
            # data-integrity finding the pipeline says cannot happen.
            self.exclude(EXCLUDED_OVER_DENOMINATOR)
            return
        self.values.append(num / den)

    @property
    def n(self) -> int:
        return len(self.values)

    @property
    def mean(self) -> Optional[float]:
        return float(np.mean(self.values)) if self.values else None


def _tunable_by_row(conn, row_ids: Sequence[int], name: str) -> Dict[int, Optional[int]]:
    """``{row_id: that row's run's <name>}``, ``None`` where unverifiable.

    THE VALUE COMES FROM ``runs.tunables``, WHICH IS THE RUN'S OWN RECORD. Three
    ways to get ``None`` and none of them is substituted for by today's config:
    the column is absent (a pre-era-15 database), the value is NULL (a run whose
    writer recorded none), or the JSON does not parse or does not carry the
    name. In all three the honest answer is that this run's denominator is not
    known -- and a fraction over an assumed denominator is a number about the
    assumption, which is what ``unverified_inputs`` exists to say.
    """
    if not row_ids:
        return {}
    if "run_id" not in _reference.table_columns(conn, "inferences"):
        return {int(r): None for r in row_ids}
    if "tunables" not in _reference.table_columns(conn, "runs"):
        return {int(r): None for r in row_ids}

    placeholders = ",".join("?" for _ in row_ids)
    rows = conn.execute(
        f"SELECT i.id, r.tunables FROM inferences i "
        f"LEFT JOIN runs r ON r.id = i.run_id "
        f"WHERE i.id IN ({placeholders})", tuple(row_ids)).fetchall()

    cache: Dict[str, Optional[int]] = {}
    out: Dict[int, Optional[int]] = {}
    for row_id, blob in rows:
        if blob is None:
            out[int(row_id)] = None
            continue
        if blob not in cache:
            decoded = _reference._json_or_default(blob, None)
            value = decoded.get(name) if isinstance(decoded, dict) else None
            cache[blob] = _positive_int(value)
        out[int(row_id)] = cache[blob]
    for row_id in row_ids:
        out.setdefault(int(row_id), None)
    return out


RETRIEVAL_UNDERFILL_COLUMNS = ("candidates_retrieved",)
RERANK_UNDERFILL_COLUMNS = ("candidates_retrieved", "candidates_reranked")
TRIALS_LOST_COLUMNS = ("candidates_retrieved", "retrieval_trials_lost")


def _fetch(conn, row_ids: Sequence[int], columns: Sequence[str]):
    """``{row_id: {column: value}}``, or a raise naming an absent column."""
    projection = ", ".join(["id"] + list(columns))
    placeholders = ",".join("?" for _ in row_ids)
    rows = conn.execute(
        f"SELECT {projection} FROM inferences WHERE id IN ({placeholders})",
        tuple(row_ids)).fetchall()
    return {int(r[0]): dict(zip(columns, r[1:])) for r in rows}


def _absent_columns(conn, columns):
    have = set(_reference.table_columns(conn, "inferences"))
    return [c for c in columns if c not in have]


def retrieval_underfill(conn, row_ids, stratum=None) -> Dict:
    """How much of the RRF fusion pool a patient's retrieval failed to fill.

        per row   1 - candidates_retrieved / RRF_POOL_SIZE
        reported  the MEAN over rows with a verifiable denominator

    THE DENOMINATOR IS ``RRF_POOL_SIZE`` FROM THE RUN'S RECORDED TUNABLES, and
    it is the right one because it is the cap the code applies:
    ``oncotriage/agent/retrieval.py:node_hybrid_retrieval`` builds
    ``ranked_nct_ids = sorted(fusion_scores.items(), ...)[:RRF_POOL_SIZE]``, so
    no run can retrieve more than its own pool size.

    ``candidates_retrieved`` IS ``len(hybrid_results)`` -- the trials that were
    ranked in AND whose payload was recovered -- so this fraction conflates two
    causes: a fusion set smaller than the pool (retrieval genuinely found few)
    and payload loss. ``trials_lost`` below is what attributes the second, and
    the two are reported side by side for exactly that reason.

    ROW RULE: deduplicated rows INCLUDING FAILURES. A patient whose Stage 5
    errored still retrieved, and excluding them would make this metric blind to
    a run that failed precisely because retrieval returned nothing.
    """
    policy = POLICY_REPORTING_ONLY
    absent = _absent_columns(conn, RETRIEVAL_UNDERFILL_COLUMNS)
    if absent:
        return _refusal(STATUS_REFUSED_COLUMN_ABSENT, policy,
                        f"column(s) {absent} absent from inferences", stratum)
    if not row_ids:
        return _refusal(STATUS_NO_DATA, policy,
                        "no rows survive this metric's row rule", stratum)

    values = _fetch(conn, row_ids, RETRIEVAL_UNDERFILL_COLUMNS)
    pools = _tunable_by_row(conn, row_ids, "RRF_POOL_SIZE")

    acc = FractionOverRows()
    for row_id in row_ids:
        row = values.get(int(row_id), {})
        pool = pools.get(int(row_id))
        if pool is None:
            acc.exclude(EXCLUDED_UNVERIFIED_DENOMINATOR)
            continue
        retrieved = row.get("candidates_retrieved")
        if retrieved is None:
            acc.exclude(EXCLUDED_NO_VALUE)
            continue
        filled = _non_negative_int(retrieved)
        if filled is None:
            acc.exclude(EXCLUDED_MALFORMED)
            continue
        if filled > pool:
            acc.exclude(EXCLUDED_OVER_DENOMINATOR)
            continue
        acc.values.append(1.0 - (filled / pool))

    return _finish_fraction(
        acc, policy, stratum,
        unverified_note=("no row carried a verifiable RRF_POOL_SIZE in its "
                         "run's recorded tunables"),
        computed_note="mean fraction of the RRF fusion pool left unfilled")


def rerank_underfill(conn, row_ids, stratum=None) -> Dict:
    """How much of the cross-encoder's intake a patient's retrieval failed to fill.

        per row   1 - candidates_reranked / min(candidates_retrieved,
                                                TOP_K_CANDIDATES)
        reported  the MEAN over rows with a NON-ZERO denominator

    THE DENOMINATOR IS THE MINIMUM AND NOT THE CONSTANT, because the reranker
    cannot rank more than it was handed: ``node_cross_encoder_rerank`` takes
    ``sorted_by_rrf[:TOP_K_CANDIDATES]`` out of ``hybrid_results``, so the
    achievable maximum is ``min(what retrieval produced, the cap)``. Using the
    constant alone would report a patient with 3 retrieved trials as 92.5%
    underfilled when their rerank was in fact complete.

    A ZERO DENOMINATOR IS NOT DIVIDED AND IS NOT A ZERO. ``candidates_retrieved
    = 0`` means retrieval returned nothing, so there was nothing to rerank and
    the fraction is undefined -- not 0.0 (which would read as a full rerank) and
    not 1.0 (which would read as a rerank that dropped everything). The row is
    excluded and counted under ``zero_denominator``, which is itself the
    finding.
    """
    policy = POLICY_REPORTING_ONLY
    absent = _absent_columns(conn, RERANK_UNDERFILL_COLUMNS)
    if absent:
        return _refusal(STATUS_REFUSED_COLUMN_ABSENT, policy,
                        f"column(s) {absent} absent from inferences", stratum)
    if not row_ids:
        return _refusal(STATUS_NO_DATA, policy,
                        "no rows survive this metric's row rule", stratum)

    values = _fetch(conn, row_ids, RERANK_UNDERFILL_COLUMNS)
    caps = _tunable_by_row(conn, row_ids, "TOP_K_CANDIDATES")

    acc = FractionOverRows()
    for row_id in row_ids:
        row = values.get(int(row_id), {})
        cap = caps.get(int(row_id))
        if cap is None:
            acc.exclude(EXCLUDED_UNVERIFIED_DENOMINATOR)
            continue
        retrieved = _non_negative_int(row.get("candidates_retrieved"))
        reranked = row.get("candidates_reranked")
        if row.get("candidates_retrieved") is None or reranked is None:
            acc.exclude(EXCLUDED_NO_VALUE)
            continue
        if retrieved is None:
            acc.exclude(EXCLUDED_MALFORMED)
            continue
        denominator = min(retrieved, cap)
        if denominator <= 0:
            acc.exclude(EXCLUDED_ZERO_DENOMINATOR)
            continue
        ranked = _non_negative_int(reranked)
        if ranked is None:
            acc.exclude(EXCLUDED_MALFORMED)
            continue
        if ranked > denominator:
            acc.exclude(EXCLUDED_OVER_DENOMINATOR)
            continue
        acc.values.append(1.0 - (ranked / denominator))

    return _finish_fraction(
        acc, policy, stratum,
        unverified_note=("no row carried a verifiable TOP_K_CANDIDATES in its "
                         "run's recorded tunables"),
        computed_note="mean fraction of the cross-encoder intake left unfilled")


def trials_lost_indicator(conn, row_ids, stratum=None) -> Dict:
    """The fraction of rows that lost at least one ranked-in trial.

    AN INDICATOR AND NOT A RATE, deliberately: it answers "how many patients
    were affected", which is the question an operator asks first, and it needs
    no denominator beyond the row count. ``trials_lost_fraction`` below answers
    "how much was lost when it happened", which does.

    A NULL ``retrieval_trials_lost`` MEANS STAGE 2 NEVER REPORTED and the row
    is excluded and counted -- never read as a zero. The column is written by
    ``node_hybrid_retrieval``; a row without it did not get that far.
    """
    policy = POLICY_REPORTING_ONLY
    absent = _absent_columns(conn, ("retrieval_trials_lost",))
    if absent:
        return _refusal(STATUS_REFUSED_COLUMN_ABSENT, policy,
                        f"column(s) {absent} absent from inferences", stratum)
    if not row_ids:
        return _refusal(STATUS_NO_DATA, policy,
                        "no rows survive this metric's row rule", stratum)

    values = _fetch(conn, row_ids, ("retrieval_trials_lost",))
    affected, considered = 0, 0
    excluded: Dict[str, int] = {}
    for row_id in row_ids:
        lost = values.get(int(row_id), {}).get("retrieval_trials_lost")
        if lost is None:
            excluded[EXCLUDED_NO_VALUE] = excluded.get(EXCLUDED_NO_VALUE, 0) + 1
            continue
        count = _non_negative_int(lost)
        if count is None:
            excluded[EXCLUDED_MALFORMED] = excluded.get(EXCLUDED_MALFORMED, 0) + 1
            continue
        considered += 1
        affected += 1 if count > 0 else 0

    if considered == 0:
        return metric(STATUS_NO_DATA, policy, stratum=stratum,
                      excluded=excluded,
                      notes="no row reported retrieval_trials_lost")

    return metric(STATUS_COMPUTED, policy,
                  value=affected / considered, stratum=stratum,
                  sample_size=considered, excluded=excluded,
                  notes=f"{affected}/{considered} rows lost at least one "
                        f"ranked-in trial to a payload the index did not return")


def trials_lost_fraction(conn, row_ids, stratum=None) -> Dict:
    """The mean fraction of a patient's RANKED-IN trials that were lost.

    THE DENOMINATOR IS QUOTED FROM THE CODE THAT RECORDS IT, and a sound one
    DOES exist in recorded data -- so this ships as a fraction rather than as
    an indicator alone. In ``oncotriage/agent/retrieval.py:node_hybrid_retrieval``:

        ranked_nct_ids = sorted(fusion_scores.items(), ...)[:RRF_POOL_SIZE]
        trials, missing_nct_ids, trials_lost = [], [], 0
        for nct_id, fusion_score in ranked_nct_ids:
            ... trials.append(...)  or  missing_nct_ids.append(nct_id)
        # backfill: each missing id either appends to `trials` or `trials_lost += 1`

    and ``oncotriage/agent/terminal.py`` records
    ``candidates_retrieved = len(state["hybrid_results"])``, which is
    ``len(trials)``. Every ranked-in id therefore ends in exactly one of the
    two, so

        ranked_in = candidates_retrieved + retrieval_trials_lost

    is an IDENTITY of the recording code rather than an estimate. That identity
    is what makes the fraction meaningful; the metric does not assume it holds,
    it uses the sum as the denominator, which is correct whether or not any
    future edit breaks the identity.

    A ZERO DENOMINATOR -- nothing ranked in at all -- is excluded and counted,
    never divided.
    """
    policy = POLICY_REPORTING_ONLY
    absent = _absent_columns(conn, TRIALS_LOST_COLUMNS)
    if absent:
        return _refusal(STATUS_REFUSED_COLUMN_ABSENT, policy,
                        f"column(s) {absent} absent from inferences", stratum)
    if not row_ids:
        return _refusal(STATUS_NO_DATA, policy,
                        "no rows survive this metric's row rule", stratum)

    values = _fetch(conn, row_ids, TRIALS_LOST_COLUMNS)
    acc = FractionOverRows()
    for row_id in row_ids:
        row = values.get(int(row_id), {})
        raw_lost = row.get("retrieval_trials_lost")
        raw_kept = row.get("candidates_retrieved")
        if raw_lost is None or raw_kept is None:
            acc.exclude(EXCLUDED_NO_VALUE)
            continue
        lost = _non_negative_int(raw_lost)
        kept = _non_negative_int(raw_kept)
        if lost is None or kept is None:
            acc.exclude(EXCLUDED_MALFORMED)
            continue
        ranked_in = kept + lost
        if ranked_in <= 0:
            acc.exclude(EXCLUDED_ZERO_DENOMINATOR)
            continue
        acc.values.append(lost / ranked_in)

    return _finish_fraction(
        acc, policy, stratum,
        unverified_note="",   # this metric needs no recorded tunable
        computed_note=("mean fraction of ranked-in trials lost to a payload "
                       "the index did not return"))


def _finish_fraction(acc: FractionOverRows, policy, stratum,
                     unverified_note, computed_note) -> Dict:
    """Turn an accumulator into a metric, naming why it did not compute.

    THE ORDER OF THE TWO EMPTY CASES IS THE DESIGN. When nothing was divided,
    the reason a reader needs is WHY -- and "every denominator was
    unverifiable" (``unverified_inputs``, an operator can go and look at
    ``runs.tunables``) is a different finding from "every row had a zero
    denominator" (``no_data``, a property of the run). Collapsing them into one
    empty result is exactly the conflation this redesign exists to remove.
    """
    if acc.n == 0:
        dominant = max(acc.excluded, key=acc.excluded.get) if acc.excluded else None
        if dominant == EXCLUDED_UNVERIFIED_DENOMINATOR:
            return metric(STATUS_UNVERIFIED_INPUTS, policy, stratum=stratum,
                          excluded=acc.excluded, notes=unverified_note)
        # `value_exceeds_denominator` IS MALFORMED DATA, NOT MISSING DATA, and
        # it belongs here rather than in the fall-through. A row whose value
        # exceeds its own denominator contradicts the recording code -- the
        # pipeline says it cannot happen -- so it is a data-integrity finding
        # with an owner, and `no_data` would send a reader to run the pipeline
        # again over rows that are already there and already wrong.
        #
        # FOUND BY PROBING THE METRICS WITH IMPOSSIBLE VALUES, not by reading:
        # the exclusion was counted correctly from the first version and the
        # STATUS it resolved to was the wrong one.
        if dominant in (EXCLUDED_MALFORMED, EXCLUDED_OVER_DENOMINATOR):
            return metric(STATUS_MALFORMED_INPUTS, policy, stratum=stratum,
                          excluded=acc.excluded,
                          notes="no row's value could be read as a "
                                "non-negative integer no larger than its own "
                                "denominator")
        return metric(STATUS_NO_DATA, policy, stratum=stratum,
                      excluded=acc.excluded,
                      notes="no row contributed a divisible fraction")

    return metric(STATUS_COMPUTED, policy, value=acc.mean, stratum=stratum,
                  sample_size=acc.n, excluded=acc.excluded,
                  notes=computed_note)


#------------------------------------------------------------------------------


# ===========================================================================
# SELECTION
# ===========================================================================

CATEGORY_AVAILABILITY = "data_availability"
CATEGORY_DATA = "data_drift"
CATEGORY_RETRIEVAL = "retrieval_drift"
CATEGORY_PERFORMANCE = "performance_drift"

METRIC_CATEGORIES = (CATEGORY_AVAILABILITY, CATEGORY_DATA,
                     CATEGORY_RETRIEVAL, CATEGORY_PERFORMANCE)
"""Every value ``drift_metrics.metric_category`` takes. CLOSED.

FOUR, AND THE DASHBOARD RENDERS FOUR TILES. ``data_availability`` was written
to the database by the shipped code and rendered by NOTHING -- the tab had
three tiles and a three-member filter -- so the one alerting metric in the
project was stored and never shown. Adding a fifth category would put the
underfill reporters in a tile of their own; they live under
``retrieval_drift`` instead, because they are readings of the retrieval stage
and a category is a place a reader looks rather than a taxonomy.
"""


class Population:
    """One selected population: which rows, from which campaign, and how.

    ``rows`` is the ``CampaignRows`` that produced it and is ``None`` for the
    reference, whose membership was fixed at designation and is READ rather
    than re-derived. That asymmetry is the design and not an omission: a
    reference whose rows were re-derived at every drift run would be "whichever
    rows that query returns today", which is precisely the selection this
    redesign replaced.
    """

    def __init__(self, run_ids=(), row_ids=(), label="", rows=None,
                 patients=None):
        self.run_ids = tuple(run_ids)
        self.row_ids = tuple(row_ids)
        self.label = label
        self.rows = rows
        self._patients = patients

    @property
    def patients(self):
        if self._patients is not None:
            return self._patients
        return self.rows.patients if self.rows is not None else len(self.row_ids)

    def describe(self):
        runs = ",".join(str(r) for r in self.run_ids) or "-"
        base = (f"{self.label}: runs {runs}, {len(self.row_ids)} rows, "
                f"{self.patients} patients")
        if self.rows is None:
            return base + " (designated membership, not re-derived)"
        return (base + f" ({self.rows.rows_before_dedup} before dedup, "
                       f"{self.rows.duplicate_rows} duplicate)")


COMPARISON_LATEST = "latest"
"""The one OPT-IN that selects the comparison campaign automatically.

AN EXPLICIT OPT-IN AND NOT A DEFAULT, and the difference is the whole of this
constant. ``run_drift_detection`` used to take no comparison argument at all and
resolve ``MAX(runs.id)`` itself, so "which campaign was measured" was decided by
insertion order -- which is the SAME defect the reference designation replaced,
surviving on the other side of the comparison. An operator who wants the latest
campaign says so, in as many words, and the row records that they said so.

IT IS A STRING RATHER THAN ``None`` because ``None`` is what an omitted
argument looks like, and the point is that the argument cannot be omitted.
"""

COMPARISON_SELECTION_EXPLICIT = "explicit_run"
COMPARISON_SELECTION_LATEST = "latest_opt_in"
COMPARISON_SELECTIONS = (COMPARISON_SELECTION_EXPLICIT,
                         COMPARISON_SELECTION_LATEST)
"""How the comparison campaign was chosen. CLOSED, and STORED on every row.

`drift_metrics.comparison_selection` carries it, so a reader asking "did
somebody choose this campaign, or did the tool pick it" gets an answer in plain
SQL rather than from a log line that has scrolled away. Two runs whose numbers
differ because one of them measured whatever ran last are two runs a reader must
be able to tell apart.
"""


class ComparisonSelectionError(RuntimeError):
    """The comparison argument is not a run id and is not the opt-in.

    A ``RuntimeError`` subclass and deliberately not a ``ValueError``, on
    ``UnknownModelPricingError``'s precedent: a broad ``except ValueError``
    anywhere above this must not be able to turn "nobody chose a comparison"
    into a silent default, which is the failure the argument exists to prevent.
    """


def require_selection(selection):
    """``selection`` if it is a member of ``COMPARISON_SELECTIONS`` or ``None``.

    THE THIRD CLOSED VOCABULARY ON THIS TABLE, AND IT NEEDED THE SAME GUARD THE
    OTHER TWO HAVE. ``status`` and ``alert_policy`` are validated at the write
    by ``require_status`` / ``require_policy``, for the reason written at the
    first of them: a vocabulary is only closed if something closes it, and a
    typo reaching the column makes every consumer's exhaustive branch fall
    through to its default. ``comparison_selection`` shipped in the repair
    WITHOUT one -- an asymmetry with no argument behind it, found by re-reading
    the write gate after the repair was green rather than by a failing check.

    ``None`` IS A MEMBER AND IS NOT A DEFAULT. It means "no drift run wrote this
    row" -- a direct ``log_drift_metrics`` call, a test, a future caller -- and
    it is what every pre-era-16 row holds. Folding it into one of the two real
    members would claim a selection nobody made, which is the whole defect this
    column exists to remove.

    IT RAISES RATHER THAN DOWNGRADING, unlike the alert one line below. An
    unreadable ALERT is a defect in one metric's body and the run has twenty
    others worth keeping; an unrecognised SELECTION is a caller passing a value
    this module does not define, which is a programming error rather than a
    reading, and it is the same for every row the call is about to write.
    """
    if selection is not None and selection not in COMPARISON_SELECTIONS:
        raise ComparisonSelectionError(
            f"{selection!r} is not a member of COMPARISON_SELECTIONS "
            f"{COMPARISON_SELECTIONS!r}, and is not None. "
            f"drift_metrics.comparison_selection is a closed vocabulary a "
            f"reader GROUPS on; a value outside it is a bucket no consumer "
            f"knows about.")
    return selection


def latest_campaign_anchor(conn) -> Optional[int]:
    """The most recent run row's id, or ``None`` when there is none.

    ``MAX(id)`` over ``runs``, which is AUTOINCREMENT and therefore monotone in
    creation order within one database -- the same property
    ``queries.campaign_summary`` reads run order off, and for the same reason:
    ``started_at`` is TEXT that two rows can share.

    IT IS REACHED ONLY THROUGH THE ``COMPARISON_LATEST`` OPT-IN. This function
    used to be called unconditionally, which made "whatever ran last" the
    default and therefore nobody's decision. It is still here because "the
    campaign that just finished" is the common case and an operator should not
    have to look up a run id to ask for it -- what changed is that they have to
    ASK.
    """
    row = conn.execute("SELECT MAX(id) FROM runs").fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def resolve_comparison_selection(comparison):
    """``(anchor_or_None, selection)`` for the comparison argument. RAISES.

    ``anchor`` is ``None`` for the opt-in, which means "ask the database"; an
    int otherwise. ``selection`` is a member of ``COMPARISON_SELECTIONS`` and is
    what the row records.

    IT RAISES ON ANYTHING ELSE, INCLUDING ``None``. An unrecognised comparison
    is a configuration defect that costs nothing to refuse -- this runs before
    any population is read -- and the one thing it must never do is fall back to
    a default, because a default is what this argument exists to remove.

    ``bool`` IS EXCLUDED EXPLICITLY. ``isinstance(True, int)`` is True in
    Python, so ``comparison=True`` would otherwise select run 1 -- a real
    campaign, silently, on a value that means nothing.
    """
    if comparison == COMPARISON_LATEST:
        return None, COMPARISON_SELECTION_LATEST
    if isinstance(comparison, bool) or not isinstance(comparison, int):
        raise ComparisonSelectionError(
            f"comparison={comparison!r} is neither a run id (an int) nor the "
            f"explicit opt-in {COMPARISON_LATEST!r}. There is no default: "
            f"which campaign a drift run measures is a decision, and a tool "
            f"that picked one silently is the defect the reference designation "
            f"already removed from the other side of the comparison.")
    return comparison, COMPARISON_SELECTION_EXPLICIT


def select_comparison_population(db_path, comparison):
    """The comparison population. CONSULTS NO REFERENCE, AND THAT IS THE POINT.

    Args:
        db_path: the database.
        comparison: a run id, or ``COMPARISON_LATEST``. REQUIRED, no default --
            see ``resolve_comparison_selection``.

    Returns ``(comparison_population, detail, selection)``. The population is
    ``None`` when the database cannot answer -- no run identity, no runs, no
    rows, or a run id that names nothing -- and ``detail`` says which of those
    it was, because they have different remedies. ``selection`` is recorded
    whether or not a population was found, so a refusal still says how the
    campaign was being chosen.

    IT TAKES NO ``reference`` ARGUMENT. It used to, in order to build both
    populations in one pass, and that made the reference resolution a
    PREREQUISITE of selecting the comparison -- which put it upstream of the
    availability metric, which is exactly the ordering ``run_drift_detection``
    exists to prevent. Splitting the two is what makes "availability computes
    and logs before any reference is resolved" a property of the call graph
    rather than a promise about which line comes first.
    """
    anchor, selection = resolve_comparison_selection(comparison)

    conn = _reference._connect(db_path)
    try:
        ok, detail = _reference.has_run_identity(conn)
        if not ok:
            return None, detail, selection

        if anchor is None:
            anchor = latest_campaign_anchor(conn)
            if anchor is None:
                return None, "the runs table holds no rows", selection
        else:
            # AN EXPLICIT RUN ID THAT NAMES NO ROW IS ITS OWN REFUSAL, and it
            # is checked HERE rather than being left to the campaign walk:
            # that walk reports `run_row_not_found`, which is true and does not
            # say that the id came from a person who can correct it.
            row = conn.execute("SELECT id FROM runs WHERE id = ?",
                               (anchor,)).fetchone()
            if row is None:
                return None, (f"run {anchor} does not exist in this database; "
                              f"no campaign can be selected from it"), selection

        membership = _campaign_run_ids(anchor, db_path=db_path)
        if not membership.resolved:
            return None, (f"run {anchor} does not name a campaign: "
                          f"{membership.reason}"), selection

        rows = _reference.campaign_rows(conn, membership.run_ids)
        if not rows.row_ids:
            return None, (f"campaign {membership.run_ids} holds no inference "
                          f"rows"), selection

        return (Population(membership.run_ids, rows.row_ids, "comparison",
                           rows=rows), "selected", selection)
    finally:
        conn.close()


def reference_population_of(reference):
    """The reference population, or ``None`` when the reference did not resolve.

    THE ROWS ARE THE DESIGNATED ONES AND ARE NOT RE-DERIVED. They were
    deduplicated at designation time and the stored list is what "the same
    population every time" means; a re-derivation here would make the reference
    whatever that query returns today, which is the selection this redesign
    replaced.
    """
    if reference is None or not reference.ok:
        return None
    return Population(reference.run_ids, reference.row_ids, "reference",
                      patients=len(reference.row_ids))


#------------------------------------------------------------------------------


# ===========================================================================
# THE COMPARISON METRICS
# ===========================================================================
#
# EVERY ONE OF THEM IS REPORTING-ONLY. What each RETAINS is stated at its
# declaration: the statistic, the two sample sizes, and -- for the z-scores --
# the reference mean and standard deviation, which are the two numbers a reader
# compares by eye when the z itself is deferred. Nothing is dropped because the
# alert is; the alert was the only part that was never calibrated.

COMPARISON_METRICS = (
    # (name, category, row rule, kind, column-or-derivation)
    #
    # THE ROW RULE IS DECLARED PER METRIC AND IT IS NOT DECORATION. Two of
    # these would be IDENTICALLY ZERO under the wrong one:
    #
    #   error_rate    must use ROW_RULE_ALL. Under the Stage-5 rule every
    #                 surviving row has a clean error by construction, so the
    #                 metric that exists to see failures would be computed over
    #                 the population with none in it and would report 0.0 for a
    #                 run that errored on half its cohort.
    #   match_quality must use ROW_RULE_STAGE5. Its denominator is
    #                 candidates_evaluated, and a row where that is 0 has no
    #                 quality to report -- the shipped code substituted 0 for
    #                 it with np.where, which pulled the mean toward zero for
    #                 every no-candidates patient and reported that as a
    #                 quality drop.
    ("age_ks_test", CATEGORY_DATA, _reference.ROW_RULE_ALL, "ks", "age"),
    ("condition_count_psi", CATEGORY_DATA, _reference.ROW_RULE_ALL, "psi",
     "condition_count"),
    ("medication_count_psi", CATEGORY_DATA, _reference.ROW_RULE_ALL, "psi",
     "medication_count"),

    ("candidates_retrieved_z_score", CATEGORY_RETRIEVAL,
     _reference.ROW_RULE_ALL, "z", "candidates_retrieved"),
    ("candidates_reranked_z_score", CATEGORY_RETRIEVAL,
     _reference.ROW_RULE_ALL, "z", "candidates_reranked"),
    ("candidates_filtered_z_score", CATEGORY_RETRIEVAL,
     _reference.ROW_RULE_ALL, "z", "candidates_filtered"),
    ("candidates_evaluated_z_score", CATEGORY_RETRIEVAL,
     _reference.ROW_RULE_STAGE5, "z", "candidates_evaluated"),

    ("eligible_matches_z_score", CATEGORY_PERFORMANCE,
     _reference.ROW_RULE_STAGE5, "z", "eligible_matches"),
    ("total_time_z_score", CATEGORY_PERFORMANCE,
     _reference.ROW_RULE_STAGE5, "z", "total_time"),
    ("error_rate_z_score", CATEGORY_PERFORMANCE,
     _reference.ROW_RULE_ALL, "z", "__error_rate__"),
    ("match_quality_z_score", CATEGORY_PERFORMANCE,
     _reference.ROW_RULE_STAGE5, "z", "__match_quality__"),
    ("llm_classifier_retry_rate_z_score", CATEGORY_PERFORMANCE,
     _reference.ROW_RULE_STAGE5, "z", "llm_classifier_retries"),
)
"""Every reference-consuming metric, with the row rule each declares.

THE TWO DERIVED SERIES are named with dunder sentinels rather than being column
names, so a reader of this table cannot mistake them for columns:

    __error_rate__     1.0 where COALESCE(error,'') <> '', else 0.0. Over the
                       ALL rule, so a failed patient is in it.
    __match_quality__  eligible_matches / candidates_evaluated. Over the
                       Stage-5 rule, so the denominator is never zero and
                       nothing has to be substituted for it.
"""

def _series(conn, row_ids, column):
    """One metric's values over ``row_ids``, as a float array.

    Returns ``(values, detail)``. ``values`` is ``None`` when the column or a
    derivation input is absent, and ``detail`` names it.
    """
    have = set(_reference.table_columns(conn, "inferences"))

    if column == "__error_rate__":
        needed = ["error"]
    elif column == "__match_quality__":
        needed = ["eligible_matches", "candidates_evaluated"]
    else:
        needed = [column]

    absent = [c for c in needed if c not in have]
    if absent:
        return None, f"column(s) {absent} absent from inferences"
    if not row_ids:
        return np.array([], dtype=float), "no rows"

    projection = ", ".join(needed)
    placeholders = ",".join("?" for _ in row_ids)
    rows = conn.execute(
        f"SELECT id, {projection} FROM inferences WHERE id IN ({placeholders}) "
        f"ORDER BY id ASC", tuple(row_ids)).fetchall()

    if column == "__error_rate__":
        # A row whose `error` is NULL or empty is a SUCCESS and contributes
        # 0.0. It is never a NaN: "no error was recorded" is the ordinary state
        # of a clean row, and dropping it would compute the error rate over the
        # errors alone.
        return (np.array([0.0 if (r[1] is None or str(r[1]) == "") else 1.0
                          for r in rows], dtype=float), "derived")

    if column == "__match_quality__":
        values = []
        for _row_id, eligible, evaluated in rows:
            # NOTHING IS SUBSTITUTED FOR A ZERO DENOMINATOR. The Stage-5 row
            # rule has already excluded those rows, so reaching one here means
            # the rule did not run -- and NaN is what makes that visible
            # downstream instead of a 0.0 that looks like a quality reading.
            if evaluated in (None, 0) or eligible is None:
                values.append(float("nan"))
            else:
                try:
                    values.append(float(eligible) / float(evaluated))
                except Exception:                          # noqa: BLE001
                    values.append(float("nan"))
        return np.array(values, dtype=float), "derived"

    out = []
    for row in rows:
        value = row[1]
        try:
            out.append(float("nan") if value is None else float(value))
        except Exception:                                  # noqa: BLE001
            # A VALUE THE COLUMN CANNOT HOLD BECOMES NaN AND IS THEREFORE
            # DROPPED BY EVERY STATISTIC HERE. It is not silently zero, which
            # is what `astype(float)` on an object column would have produced
            # for some inputs and a raise for others.
            out.append(float("nan"))
    return np.array(out, dtype=float), "column"


def compute_comparison_metrics(conn, comparison, reference_population,
                               reference) -> Dict[str, Dict]:
    """Every reference-consuming metric, keyed by name.

    THE PAIRING IS PER METRIC, because the row rule is. A metric under the
    Stage-5 rule pairs over a smaller population than one under the ALL rule,
    and pairing once over the ALL rule and then filtering would leave a patient
    paired against a row that the filter had removed from the other side.

    EVERY RESULT CARRIES THE PAIRING COUNTS IN ``notes`` -- surviving,
    unverified and both unpaired leftovers -- so a number is never reported
    without the population it was computed over.
    """
    results: Dict[str, Dict] = {}

    if reference is None or not reference.ok:
        status = _STATUS_FOR_REFERENCE_OUTCOME.get(
            reference.outcome if reference is not None else
            _reference.REFERENCE_ABSENT, STATUS_REFERENCE_UNRESOLVABLE)
        detail = (reference.detail if reference is not None
                  else "no reference resolution was attempted")
        for name, _category, _rule, _kind, _column in COMPARISON_METRICS:
            results[name] = _refusal(status, POLICY_REPORTING_ONLY, detail)
        return results

    # The two row sets per rule, and the pairing over them. Computed once per
    # RULE rather than once per metric: eleven metrics share two rules, and the
    # pairing is the expensive half.
    paired_by_rule = {}
    for rule in _reference.ROW_RULES:
        ref_rows = _reference.eligible_row_ids(conn, reference_population.row_ids, rule)
        cmp_rows = _reference.eligible_row_ids(conn, comparison.row_ids, rule)
        paired_by_rule[rule] = (ref_rows, cmp_rows,
                                _reference.pair_rows(conn, ref_rows, cmp_rows))

    for name, _category, rule, kind, column in COMPARISON_METRICS:
        ref_rows, cmp_rows, pairs = paired_by_rule[rule]
        counts = pairs.counts
        pairing_note = (
            f"rule={rule}; verified pairs={counts['verified_pairs']}, "
            f"unverified={counts['unverified_pairs']}, "
            f"incomparable={counts['incomparable_pairs']}, "
            f"reference-only patients={counts['reference_only_patients']}, "
            f"comparison-only patients={counts['comparison_only_patients']}")
        # WHICH RECORDED FACT DISAGREED, AND WHICH COULD NOT BE READ. Without
        # this the refusal says "no verified pair survives" and an operator
        # cannot tell a model change from a re-indexed corpus from a database
        # that never recorded a patient hash -- three findings with three
        # remedies, reported as one sentence.
        if pairs.incomparable_on:
            pairing_note += ("; incomparable on " + ", ".join(
                f"{k}={v}" for k, v in sorted(pairs.incomparable_on.items())))
        if pairs.unverified_on:
            pairing_note += ("; unverifiable on " + ", ".join(
                f"{k}={v}" for k, v in sorted(pairs.unverified_on.items())))

        if not pairs.verified:
            results[name] = _refusal(
                STATUS_REFUSED_INCOMPARABLE, POLICY_REPORTING_ONLY,
                f"no verified pair survives this metric's rule. {pairing_note}")
            continue

        ref_ids = [p[0] for p in pairs.verified]
        cmp_ids = [p[1] for p in pairs.verified]

        if len(ref_ids) < MIN_SAMPLES_BASELINE or len(cmp_ids) < MIN_SAMPLES_COMPARISON:
            results[name] = metric(
                STATUS_INSUFFICIENT_DATA, POLICY_REPORTING_ONLY,
                sample_size=len(cmp_ids), reference_sample_size=len(ref_ids),
                notes=f"{len(ref_ids)} verified pairs; the floors are "
                      f"{MIN_SAMPLES_BASELINE} reference and "
                      f"{MIN_SAMPLES_COMPARISON} comparison rows. "
                      f"{pairing_note}")
            continue

        ref_values, ref_detail = _series(conn, ref_ids, column)
        cmp_values, cmp_detail = _series(conn, cmp_ids, column)
        if ref_values is None or cmp_values is None:
            results[name] = _refusal(
                STATUS_REFUSED_COLUMN_ABSENT, POLICY_REPORTING_ONLY,
                f"{ref_detail if ref_values is None else cmp_detail}. "
                f"{pairing_note}")
            continue

        if kind == "ks":
            result = ks_test_drift(ref_values, cmp_values)
        elif kind == "psi":
            result = calculate_psi(ref_values, cmp_values)
        else:
            result = z_score_drift(ref_values, cmp_values)

        result["notes"] = _join_notes(result["notes"], pairing_note)
        results[name] = result

    return results


_STATUS_FOR_REFERENCE_OUTCOME = {
    _reference.REFERENCE_ABSENT: STATUS_REFERENCE_ABSENT,
    _reference.REFERENCE_UNRESOLVABLE: STATUS_REFERENCE_UNRESOLVABLE,
    _reference.REFERENCE_MUTATED: STATUS_REFERENCE_MUTATED,
    _reference.REFERENCE_NO_RUN_IDENTITY: STATUS_NO_RUN_IDENTITY,
    _reference.REFERENCE_READ_FAILURE: STATUS_READ_FAILURE,
}
"""One metric status per reference refusal. TOTAL over the refusing outcomes.

MAPPED RATHER THAN SHARED. The two vocabularies are deliberately separate
objects -- one is about a designation, the other about a metric -- and a single
enum would make every consumer of one a consumer of the other. The mapping is
checked to be total below, so a new refusal outcome cannot arrive at a metric as
a ``KeyError`` or, worse, as a default.
"""

_UNMAPPED = tuple(o for o in _reference.REFERENCE_OUTCOMES
                  if o != _reference.REFERENCE_OK
                  and o not in _STATUS_FOR_REFERENCE_OUTCOME)
if _UNMAPPED:                                              # pragma: no cover
    raise RuntimeError(
        f"_STATUS_FOR_REFERENCE_OUTCOME does not map {_UNMAPPED!r}. Every "
        f"refusing member of REFERENCE_OUTCOMES must have a metric status, or "
        f"a reference failure reaches a metric with no status of its own.")
del _UNMAPPED


#------------------------------------------------------------------------------


# ===========================================================================
# THE BASELINE-INDEPENDENT FAMILY, AND THE REPORTING-ONLY FAMILY
# ===========================================================================

def detect_data_availability(conn, comparison) -> Dict[str, Dict]:
    """Whether the inputs the pipeline reasoned over were usable.

    CONSULTS NO REFERENCE, BY DESIGN, and is computed FIRST -- see
    ``run_drift_detection``'s step order, where that is a correctness property
    rather than a preference.

    ROW RULE: deduplicated rows INCLUDING FAILURES. A patient whose Stage 5
    errored still has an ECOG selection path recorded by the parser, and
    excluding failures would make this metric blind to a run that failed
    BECAUSE its inputs were unusable.
    """
    row_ids = _reference.eligible_row_ids(conn, comparison.row_ids,
                                          _reference.ROW_RULE_ALL)
    frame = _frame_for(conn, row_ids, ("ecog_selection", "ecog_value"))
    return {"ecog_unavailable_rate": ecog_unavailable_rate(frame)}


def _frame_for(conn, row_ids, columns) -> pd.DataFrame:
    """A DataFrame of ``columns`` over ``row_ids``.

    AN ABSENT COLUMN IS ABSENT FROM THE FRAME rather than being filled with
    NULLs, because ``ecog_unavailable_rate`` distinguishes "this database has
    no such column" (``refused_column_absent``, migrate it) from "the column is
    there and empty" (``no_data``, run the pipeline), and a filled frame would
    collapse the two.
    """
    have = set(_reference.table_columns(conn, "inferences"))
    wanted = [c for c in columns if c in have]
    if not wanted or not row_ids:
        return pd.DataFrame(columns=wanted)
    projection = ", ".join(wanted)
    placeholders = ",".join("?" for _ in row_ids)
    rows = conn.execute(
        f"SELECT {projection} FROM inferences WHERE id IN ({placeholders}) "
        f"ORDER BY id ASC", tuple(row_ids)).fetchall()
    return pd.DataFrame(rows, columns=wanted)


REPORTING_ONLY_METRICS = (
    ("retrieval_underfill", retrieval_underfill),
    ("rerank_underfill", rerank_underfill),
    ("trials_lost_indicator", trials_lost_indicator),
    ("trials_lost_fraction", trials_lost_fraction),
)
"""The single-population reporting metrics, all under ``retrieval_drift``.

THEY CONSUME NO REFERENCE TO COMPUTE, which is why a reference failure does not
downgrade them -- and it is what makes "a reference failure downgrades only
reference-consuming metrics" a property a reader can check rather than a claim.
The reference's own value for each IS reported when the reference resolves, in
``baseline_mean``, so a reader gets the comparison for free without the metric
depending on it.
"""


def detect_reporting_only(conn, comparison, reference_population
                          ) -> Dict[str, Dict]:
    """The underfill and trials-lost reporters, overall and per stratum.

    ONE ROW PER STRATUM PLUS ONE OVERALL ROW. The overall row carries
    ``stratum = NULL``, which is the schema's own word for "over the whole
    population"; the per-stratum rows carry a member of
    ``registries.primary_cancer.CANCER_GROUPS``, and a row whose
    ``primary_condition`` was never recorded lands in the EXPLICIT
    ``unknown`` stratum rather than being folded into ``other`` -- see
    ``drift_reference.STRATUM_UNKNOWN`` for why those two are different facts.

    THE REFERENCE'S VALUE GOES IN ``baseline_mean`` AND NOT IN A ROW OF ITS
    OWN. It is the same quantity over the other population, which is exactly
    what that column already means for every z-score in this module, and one
    row per (metric, stratum) keeps the table's grain uniform.
    """
    rule = _reference.ROW_RULE_ALL
    cmp_rows = _reference.eligible_row_ids(conn, comparison.row_ids, rule)
    ref_rows = (_reference.eligible_row_ids(conn, reference_population.row_ids,
                                            rule)
                if reference_population is not None else ())

    cmp_strata = _reference.strata_for_rows(conn, cmp_rows)
    ref_strata = _reference.strata_for_rows(conn, ref_rows)

    results: Dict[str, Dict] = {}
    for name, fn in REPORTING_ONLY_METRICS:
        overall = fn(conn, cmp_rows)
        if ref_rows:
            reference_value = fn(conn, ref_rows)
            overall["baseline_mean"] = reference_value.get("metric_value")
            overall["reference_sample_size"] = reference_value.get("sample_size")
        results[name] = overall

        # THE STRATA ARE DERIVED FROM THE COMPARISON POPULATION AND NOT FROM
        # THE VOCABULARY. Emitting a row for every member of CANCER_GROUPS
        # would put fifteen `no_data` rows in the table for every campaign that
        # ran one cancer type, which is noise rather than coverage. What IS
        # guaranteed is that every row of the population lands in exactly one
        # stratum, `unknown` included.
        for stratum in sorted(set(cmp_strata.values())):
            sub_cmp = [r for r in cmp_rows if cmp_strata.get(int(r)) == stratum]
            sub = fn(conn, sub_cmp, stratum=stratum)
            if ref_rows:
                sub_ref = [r for r in ref_rows
                           if ref_strata.get(int(r)) == stratum]
                if sub_ref:
                    sub["baseline_mean"] = fn(conn, sub_ref,
                                              stratum=stratum).get("metric_value")
            results[f"{name}[{stratum}]"] = sub

    return results


#------------------------------------------------------------------------------


# ===========================================================================
# DATABASE LOGGING
# ===========================================================================

def log_drift_metrics(drift_results: Dict, db_path=None, reference_id=None,
                      timestamp=None, selection=None):
    """Write drift results to ``drift_metrics``.

    Args:
        drift_results: ``{category: {metric_name: result}}``. Every result must
            have come from ``metric()``; the two state columns are read from it
            by name and a dict that lacks them raises here rather than storing
            NULLs that would read as a pre-era-16 row.
        db_path: the database. ``None`` means the configured production one.
        reference_id: the designation every reference-consuming reading was
            taken against, or ``None``. Stored per row, and NULL on a
            baseline-independent row because that metric consulted none.
        selection: how the comparison campaign was chosen -- a member of
            ``COMPARISON_SELECTIONS``, or ``None`` when a caller wrote rows
            outside a drift run. Stored on EVERY row including the
            baseline-independent one: unlike the reference, the comparison
            population is what every metric was computed over, so "how was it
            chosen" is a fact about all of them.
        timestamp: the UTC ISO stamp to write on every row. ``None`` means now.

            IT IS AN ARGUMENT BECAUSE ONE DRIFT RUN WRITES TWICE. Availability
            is logged in its own transaction before the reference is resolved,
            and everything else afterwards; if each call stamped its own
            ``now()`` the two batches would differ by microseconds and every
            consumer that groups a run by ``MAX(timestamp)`` -- the dashboard
            does, to find the latest run -- would see ONE run as two, with the
            alerting family in the half it then discards.

            MEASURED RATHER THAN PREDICTED: with the two stamps independent,
            the tab's "latest run" excluded the availability row entirely and
            the tile that this redesign exists to add rendered over nothing.

    Returns:
        The database path this call actually used, so a caller can ASSERT where
        it wrote rather than assuming.

    THE TWO WINDOW COLUMNS ARE WRITTEN NULL AND THAT IS THE POINT. Selection is
    no longer by window; a number there would describe a mechanism that did not
    run, and NULL is also what lets a reader separate a row written by the
    window selector from one written by the designation selector without
    parsing a timestamp.

    Raises:
        ValueError: drift_results is empty.
        UnknownDriftStateError: a result carries a status or policy outside its
            vocabulary -- raised by ``require_status`` / ``require_policy``,
            HERE rather than at the dashboard.
        Exception: the database operation failed.
    """
    if not drift_results or not any(drift_results.values()):
        raise ValueError("drift_results cannot be empty")

    # VALIDATED ONCE, ABOVE THE CONNECTION. It is the same value for every row
    # this call writes, so asking per row would be the same question repeated;
    # and it is checked before the database is opened, so a caller passing a
    # value outside the vocabulary is refused having touched nothing.
    selection = require_selection(selection)

    db_path = resolve_drift_db_path(db_path)

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        timestamp = timestamp or datetime.now(timezone.utc).isoformat()
        rows_inserted = 0

        for category, metrics in drift_results.items():
            if not metrics:
                continue
            for metric_name, metric_data in metrics.items():
                status = require_status(metric_data["status"])
                policy = require_policy(metric_data["policy"])

                metric_value = metric_data.get("metric_value")
                baseline_mean = metric_data.get("baseline_mean")
                baseline_std = metric_data.get("baseline_std")
                p_value = metric_data.get("p_value")
                threshold = metric_data.get("threshold")
                # THE ALERT IS RE-RESOLVED AT THE WRITE. `metric()` already did
                # it, and doing it again here is what makes the guarantee a
                # property of the TABLE rather than of the constructor: a
                # result assembled by any other means still cannot store a
                # verdict its axes do not support.
                #
                # AN INVALID ALERT REJECTS THE ROW'S STATUS HERE TOO, on the
                # same rule and for the same reason: a hand-built result
                # carrying `alert = "yes"` must not be stored under the status
                # it claims. It is a DOWNGRADE and not a raise -- this is the
                # logging path, whose contract is that a database fault must
                # not kill the run -- and the reason is written into `notes`,
                # so the rejection is on the row rather than only in a log line.
                alert, alert_outcome = resolve_alert(status, policy,
                                                     metric_data.get("alert"))
                notes = _notes_with_exclusions(metric_data)
                if alert_outcome == ALERT_INVALID:
                    status = require_status(STATUS_MALFORMED_INPUTS)
                    metric_value = None
                    notes = _join_notes(
                        notes,
                        f"the stored alert {metric_data.get('alert')!r} is not "
                        f"a verdict: drift_metrics.alert holds 0, 1 or NULL. "
                        f"Recorded as malformed_inputs rather than converted.")

                # For z-score metrics the z IS the value, and it is stored in
                # both columns so a reader who asks for `z_score` gets one.
                z_score = metric_value if (
                    baseline_mean is not None and baseline_std is not None
                ) else None

                cursor.execute('''
                    INSERT INTO drift_metrics (
                        timestamp, metric_category, metric_name, metric_value,
                        baseline_mean, baseline_std, p_value, z_score, threshold,
                        alert, baseline_window_days, comparison_window_days,
                        notes, status, alert_policy, reference_id, stratum,
                        comparison_selection
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?)
                ''', (
                    timestamp, category, metric_name, metric_value,
                    baseline_mean, baseline_std, p_value, z_score, threshold,
                    alert, None, None, notes, status, policy,
                    None if policy == POLICY_BASELINE_INDEPENDENT
                    else reference_id,
                    metric_data.get("stratum"),
                    selection,
                ))
                rows_inserted += 1

        conn.commit()
        console.out(f"✓ Logged {rows_inserted} drift metrics to database")

    except sqlite3.Error as e:
        if conn:
            conn.rollback()
        raise Exception(f"Database error: {e}")

    except Exception:
        if conn:
            conn.rollback()
        raise

    finally:
        if conn:
            conn.close()

    # AFTER the finally, not inside it. A return inside a finally block swallows
    # any exception propagating out of the try -- and every failure path above
    # is meant to propagate.
    return db_path


def _join_notes(*parts) -> Optional[str]:
    """Join note fragments with exactly one separator between them.

    ONE OWNER FOR THE JOIN, because the obvious inline form
    ``f"{a + '. ' if a else ''}{b}"`` produces ``"...one.. rule=..."`` whenever
    the first fragment already ends in a full stop -- which every metric note in
    this module does. Cosmetic, and it is in every stored ``notes`` and every
    console line, which is where a reader forms their impression of whether the
    rest was written carefully.
    """
    kept = [str(p).strip() for p in parts if p]
    kept = [p for p in kept if p]
    if not kept:
        return None
    return ". ".join(p.rstrip(". ") for p in kept)


def _notes_with_exclusions(metric_data) -> Optional[str]:
    """``notes`` with the exclusion census appended, when there is one.

    THE EXCLUSIONS TRAVEL WITH THE VALUE OR THEY ARE NOT REPORTED. A mean over
    rows that were silently dropped is a mean over a population the writer
    chose and never named, and the console and the dashboard both read `notes`.
    """
    notes = metric_data.get("notes")
    excluded = {k: v for k, v in (metric_data.get("excluded") or {}).items()
                if v}
    if not excluded:
        return notes
    census = ", ".join(f"{k}={v}" for k, v in sorted(excluded.items()))
    return _join_notes(notes, f"excluded rows: {census}")


#------------------------------------------------------------------------------


# ===========================================================================
# MAIN EXECUTION
# ===========================================================================

def _refuse_every_metric(results, summary, db_path, log_to_db, detail,
                         timestamp=None, selection=None):
    """No comparison population: every metric refuses, BY NAME, and is WRITTEN.

    A database that cannot say which run a row belongs to gets
    ``no_run_identity`` on every row -- a statement a reader can act on -- and
    it is written to ``drift_metrics``, so the refusal is durable rather than a
    line on somebody's terminal that scrolls away.

    IT IS A FUNCTION RATHER THAN A BRANCH INSIDE ``run_drift_detection``, and
    that is a structural decision rather than tidiness. This path resolves the
    reference too -- for the REPORT only, since nothing here consumes one -- and
    while it was an inline branch that call sat LEXICALLY ABOVE the availability
    computation. Nothing was wrong with the behaviour (the branch returns before
    availability is reachable), but "availability is computed before any
    reference is resolved" then stopped being checkable from the source, and a
    property that can only be verified by running it on the inputs somebody
    happened to choose is a weaker property. Moved here, ``run_drift_detection``
    has exactly one ``resolve_reference`` call and it is after the availability
    write.

    AN OPERATOR IS STILL TOLD ABOUT THE REFERENCE. "No run identity" and "no
    reference designated" are two different things to fix and they can both be
    true, so a report that named only the first would send somebody back for the
    second.
    """
    status = (STATUS_NO_RUN_IDENTITY if str(detail).startswith("absent:")
              else STATUS_NO_DATA)
    results[CATEGORY_AVAILABILITY]["ecog_unavailable_rate"] = _refusal(
        status, POLICY_BASELINE_INDEPENDENT, detail)
    for name, _fn in REPORTING_ONLY_METRICS:
        results[CATEGORY_RETRIEVAL][name] = _refusal(
            status, POLICY_REPORTING_ONLY, detail)
    for name, category, _rule, _kind, _column in COMPARISON_METRICS:
        results[category][name] = _refusal(
            status, POLICY_REPORTING_ONLY, detail)

    reference = _reference.resolve_reference(db_path)
    summary["reference"] = reference.outcome
    summary["reference_detail"] = reference.detail
    summary["reference_id"] = reference.reference_id
    return _finish_run(results, summary, db_path, log_to_db, None,
                       already_logged=(), timestamp=timestamp,
                       selection=selection)


def run_drift_detection(comparison, db_path=None, log_to_db: bool = True
                        ) -> Dict[str, Dict]:
    """Execute drift detection and, by default, log what it found.

    THE STEP ORDER IS A CORRECTNESS PROPERTY AND NOT A PREFERENCE:

        1. SELECT the comparison population. Needs run identity; needs no
           reference. ``select_comparison_population`` takes no reference
           argument, so this is a property of the call graph rather than of
           which line comes first.
        2. AVAILABILITY -- computed AND LOGGED, before any reference is
           resolved. It consults no reference, so nothing about a reference can
           reach it: not a refusal, not a mutation, not a raise out of the
           resolver, not a hang.
        3. RESOLVE the reference. Only now.
        4. REPORTING-ONLY underfill and trials-lost. They compute without a
           reference; the reference's own value rides along in
           ``baseline_mean`` when there is one, which is why they are here and
           not in step 2.
        5. COMPARISON metrics -- the only family a reference failure
           downgrades.
        6. LOG steps 4 and 5.

    Under the shipped order, step 1 loaded two time windows and RAISED on
    insufficient data, so the one metric that could have answered on a young
    database -- the only alerting metric in the project -- was the one that
    never ran.

    AVAILABILITY IS LOGGED IN ITS OWN TRANSACTION, which is the half that makes
    the ordering worth anything. Computing it first and writing it at the end
    with everything else would leave it on disk only if every later step
    survived; a separate write means a crash in the reference machinery costs
    the comparison metrics and not the alerting one. The cost is one extra
    transaction per drift run.

    IT DOES NOT RAISE ON AN EMPTY OR YOUNG DATABASE. Every refusal is a status
    on a metric, which is the whole point of the status axis: "this database
    cannot answer yet" is a reading, and the shipped code turned it into an
    exception that took the other readings with it.

    Args:
        comparison: WHICH campaign to measure -- a run id, or the explicit
            opt-in ``COMPARISON_LATEST``. REQUIRED AND FIRST, with no default.
            This function used to take no such argument and resolve
            ``MAX(runs.id)`` itself, so which campaign was measured was decided
            by insertion order -- the SAME defect the reference designation
            replaced, surviving on the other side of the comparison. The chosen
            mode is recorded on every row in
            ``drift_metrics.comparison_selection``, so "somebody chose this" and
            "the tool picked whatever ran last" are two readings a consumer can
            tell apart in SQL.
        db_path: the database to read and, when ``log_to_db``, write. ``None``
            means the configured production one. Threaded through to BOTH, for
            the reason the shipped version already gave: a run that read a
            scratch database and wrote its verdict into the production one
            would be worse than either.
        log_to_db: whether to write the results.

    Raises:
        ComparisonSelectionError: ``comparison`` is neither a run id nor the
            opt-in. Raised BEFORE anything is read or written -- a refusal that
            costs nothing, and the one outcome that must never be a silent
            default.

    Returns:
        ``{category: {metric: result}}`` plus a ``summary`` dict carrying the
        selection, the reference resolution and the counts.
    """
    db_path = resolve_drift_db_path(db_path)
    # ONE STAMP FOR THE WHOLE RUN, taken before the first write. See
    # `log_drift_metrics`' `timestamp` argument for what two stamps cost.
    run_timestamp = datetime.now(timezone.utc).isoformat()

    console.out("=" * 70)
    console.out("DRIFT DETECTION")
    console.out("=" * 70)
    console.out(f"Database: {db_path}")
    console.out()

    results: Dict[str, Dict] = {c: {} for c in METRIC_CATEGORIES}
    summary = {
        "database": str(db_path),
        "selection": None,
        "comparison_selection": None,
        "comparison_requested": comparison,
        "reference": _reference.REFERENCE_ABSENT,
        "reference_detail": None,
        "reference_id": None,
        "reference_label": None,
        "reference_run_ids": (),
        "comparison_run_ids": (),
        "comparison_rows": 0,
        "comparison_patients": 0,
        "duplicate_rows": 0,
        "order_agrees": True,
        "order_disagreements": (),
        "availability_logged_first": False,
        "alerts": 0,
        "computed": 0,
        "refused": 0,
        "reporting_only": 0,
        "metrics": 0,
    }

    # ---- Step 1: the comparison population, WITHOUT a reference ---------
    console.out("[1/6] Selecting the comparison population "
                "(no reference consulted)...")
    population, detail, selection = select_comparison_population(
        db_path, comparison)
    summary["selection"] = detail
    summary["comparison_selection"] = selection
    console.out(f"  comparison chosen by: {selection} "
                f"(requested {comparison!r})")

    if population is None:
        return _refuse_every_metric(results, summary, db_path, log_to_db,
                                    detail, timestamp=run_timestamp,
                                    selection=selection)
    comparison = population

    summary["comparison_run_ids"] = comparison.run_ids
    summary["comparison_rows"] = len(comparison.row_ids)
    summary["comparison_patients"] = comparison.patients
    if comparison.rows is not None:
        summary["duplicate_rows"] = comparison.rows.duplicate_rows
        summary["order_agrees"] = comparison.rows.order_agrees
        summary["order_disagreements"] = comparison.rows.order_disagreements
    console.out(f"✓ {comparison.describe()}")
    if comparison.rows is not None and not comparison.rows.order_agrees:
        # REPORTED, NEVER ACTED ON. The id answer is the one with a guarantee
        # behind it; a disagreement is something an operator should be told
        # about and is not a reason to refuse a reading.
        console.out(f"⚠ id order and timestamp order disagree about which row "
                    f"is a patient's first, for "
                    f"{len(comparison.rows.order_disagreements)} patient(s). "
                    f"The id order was used.")
        for line in comparison.rows.order_disagreements[:5]:
            console.out(f"    {line}")
    console.out()

    conn = _reference._connect(db_path)
    try:
        # ---- Step 2: availability, computed AND LOGGED first ------------
        console.out("[2/6] Assessing data availability "
                    "(no reference resolved yet)...")
        try:
            results[CATEGORY_AVAILABILITY] = detect_data_availability(
                conn, comparison)
        except Exception as e:                             # noqa: BLE001
            results[CATEGORY_AVAILABILITY] = {
                "ecog_unavailable_rate": _refusal(
                    STATUS_READ_FAILURE, POLICY_BASELINE_INDEPENDENT,
                    f"{type(e).__name__}: {e}")}
        _print_family(results[CATEGORY_AVAILABILITY])
        if log_to_db:
            try:
                log_drift_metrics(
                    {CATEGORY_AVAILABILITY: results[CATEGORY_AVAILABILITY]},
                    db_path=db_path, reference_id=None,
                    timestamp=run_timestamp, selection=selection)
                summary["availability_logged_first"] = True
            except Exception as e:                         # noqa: BLE001
                console.out(f"⚠ Availability logging failed (non-critical): "
                            f"{type(e).__name__}: {e}")
        console.out()

        # ---- Step 3: the reference. NOT BEFORE HERE. --------------------
        console.out("[3/6] Resolving the designated reference...")
        reference = _reference.resolve_reference(db_path)
        reference_population = reference_population_of(reference)
        summary["reference"] = reference.outcome
        summary["reference_detail"] = reference.detail
        summary["reference_id"] = reference.reference_id
        summary["reference_label"] = reference.label
        summary["reference_run_ids"] = reference.run_ids
        console.out(("✓ " if reference.ok else "⛔ ") + reference.describe())
        if not reference.ok:
            console.out(f"    {reference.detail}")
        console.out()

        # ---- Step 4: the reporting-only family --------------------------
        console.out("[4/6] Computing reporting-only retrieval metrics...")
        try:
            results[CATEGORY_RETRIEVAL].update(
                detect_reporting_only(conn, comparison, reference_population))
        except Exception as e:                             # noqa: BLE001
            for name, _fn in REPORTING_ONLY_METRICS:
                results[CATEGORY_RETRIEVAL][name] = _refusal(
                    STATUS_READ_FAILURE, POLICY_REPORTING_ONLY,
                    f"{type(e).__name__}: {e}")
        _print_family(results[CATEGORY_RETRIEVAL])
        console.out()

        # ---- Step 5: the comparison metrics -----------------------------
        console.out("[5/6] Computing reference-consuming comparison "
                    "metrics...")
        try:
            comparison_results = compute_comparison_metrics(
                conn, comparison, reference_population, reference)
        except Exception as e:                             # noqa: BLE001
            comparison_results = {
                name: _refusal(STATUS_READ_FAILURE, POLICY_REPORTING_ONLY,
                               f"{type(e).__name__}: {e}")
                for name, _c, _r, _k, _col in COMPARISON_METRICS}
        for name, category, _rule, _kind, _column in COMPARISON_METRICS:
            results[category][name] = comparison_results[name]
        for category in (CATEGORY_DATA, CATEGORY_PERFORMANCE):
            _print_family(results[category])
        console.out()
    finally:
        conn.close()

    return _finish_run(
        results, summary, db_path, log_to_db,
        reference.reference_id if reference.ok else None,
        already_logged=((CATEGORY_AVAILABILITY,)
                        if summary["availability_logged_first"] else ()),
        timestamp=run_timestamp, selection=selection)


def _print_family(family: Dict[str, Dict]) -> None:
    """One console line per metric, through the SHARED display owner."""
    for name in sorted(family):
        data = family[name]
        console.out(f"  {display_label(data['status'], data['policy'], data['alert'])}"
                    f"  {name}"
                    + (f" = {data['metric_value']:.4f}"
                       if data["metric_value"] is not None else ""))


def _finish_run(results, summary, db_path, log_to_db, reference_id,
                already_logged=(), timestamp=None, selection=None):
    """Count, log and print. The one exit of ``run_drift_detection``.

    ``already_logged`` names the categories step 2 wrote in its own
    transaction, so they are not written twice. AN EXPLICIT ARGUMENT AND NOT A
    FLAG ON THE RESULTS DICT: a caller assembling results by other means must
    not be able to suppress a write by having set a key.
    """
    for category in METRIC_CATEGORIES:
        for data in results.get(category, {}).values():
            summary["metrics"] += 1
            if data["alert"] == 1:
                summary["alerts"] += 1
            if data["status"] == STATUS_COMPUTED:
                summary["computed"] += 1
                if data["policy"] == POLICY_REPORTING_ONLY:
                    summary["reporting_only"] += 1
            else:
                summary["refused"] += 1

    results["summary"] = summary

    console.out("[6/6] Logging results...")
    if log_to_db:
        try:
            log_drift_metrics(
                {k: v for k, v in results.items()
                 if k != "summary" and k not in already_logged},
                db_path=db_path, reference_id=reference_id,
                timestamp=timestamp, selection=selection)
        except Exception as e:                             # noqa: BLE001
            console.out(f"⚠ Database logging failed (non-critical): "
                        f"{type(e).__name__}: {e}")
    else:
        console.out("  skipped (log_to_db=False)")
    console.out()

    console.out("=" * 70)
    console.out("DRIFT DETECTION SUMMARY")
    console.out("=" * 70)
    console.out(f"Metrics:        {summary['metrics']}")
    console.out(f"  computed:     {summary['computed']} "
                f"({summary['reporting_only']} of them reporting-only)")
    console.out(f"  not computed: {summary['refused']}")
    console.out(f"Alerts:         {summary['alerts']}  "
                f"(only baseline-independent metrics can raise one; every "
                f"comparison metric is reporting-only by ruling)")
    console.out(f"Comparison:     runs "
                f"{','.join(str(r) for r in summary['comparison_run_ids']) or '-'}, "
                f"{summary['comparison_rows']} rows, "
                f"{summary['comparison_patients']} patients")
    console.out(f"                chosen by {summary['comparison_selection']} "
                f"(requested {summary['comparison_requested']!r})")
    console.out(f"Reference:      {summary['reference']}"
                + (f" (id {summary['reference_id']}"
                   + (f", '{summary['reference_label']}'"
                      if summary['reference_label'] else "")
                   + f", runs "
                     f"{','.join(str(r) for r in summary['reference_run_ids']) or '-'})"
                   if summary["reference_id"] is not None else ""))
    if summary["reference_detail"]:
        console.out(f"                {summary['reference_detail']}")
    console.out("=" * 70)

    return results


def print_drift_details(results: Dict[str, Dict]) -> None:
    """Print every reading with BOTH axes beside it.

    THE DISPLAY STATE COMES FROM ``drift_states.display_label`` -- the same
    owner the dashboard reads -- so the console and the tab cannot disagree
    about whether a reading is an OK, a value or a refusal. They rendered
    independently before this existed, which is how the tab came to print a
    green tick beside a metric the console had already reported as
    insufficient.
    """
    console.out("\n" + "=" * 70)
    console.out("DETAILED DRIFT ANALYSIS")
    console.out("=" * 70)

    for category in METRIC_CATEGORIES:
        if category not in results:
            continue
        console.out(f"\n{category.upper().replace('_', ' ')}")
        console.out("-" * 70)

        metrics = results[category]
        for metric_name in sorted(metrics):
            data = metrics[metric_name]
            status = data["status"]
            policy = data["policy"]
            value = data.get("metric_value")
            threshold = data.get("threshold")
            notes = _notes_with_exclusions(data)

            console.out(f"{display_label(status, policy, data['alert'])}  "
                        f"{metric_name.replace('_', ' ')}")
            console.out(f"   status: {status} | policy: {policy}")

            if value is not None:
                console.out(f"   Value: {value:.4f}"
                            + (f" | Threshold: {threshold}"
                               if threshold is not None else "")
                            + ("  (threshold is reported, not applied: this "
                               "metric does not alert)"
                               if policy == POLICY_REPORTING_ONLY
                               and threshold is not None else ""))

            if notes:
                console.out(f"   Note: {notes}")

            p_value = data.get("p_value")
            if p_value is not None:
                console.out(f"   P-value: {p_value:.4f}")

            baseline_mean = data.get("baseline_mean")
            baseline_std = data.get("baseline_std")
            if baseline_mean is not None and baseline_std is not None:
                console.out(f"   Reference: mean={baseline_mean:.4f}, "
                            f"sd={baseline_std:.4f}")
            elif baseline_mean is not None:
                console.out(f"   Reference value: {baseline_mean:.4f}")

            sample = data.get("sample_size")
            ref_sample = data.get("reference_sample_size")
            if sample is not None or ref_sample is not None:
                console.out(f"   n: comparison={sample}, reference={ref_sample}")

            console.out()

    console.out("=" * 70)


#------------------------------------------------------------------------------


# ===========================================================================
# OPERATOR COMMANDS
# ===========================================================================

def designate_reference(run_id, db_path=None, label=None, note=None) -> Dict:
    """Designate the campaign containing ``run_id`` as THE drift reference.

    THE THIN WRAPPER IS WHERE THE PATH IS RESOLVED, and that is the layering
    this module keeps: ``drift_reference.designate_reference`` takes ``db_path``
    as a REQUIRED argument with no default, on ``empty_database(db_path,
    flag)``'s precedent, so the store itself can never point at production by
    omission. The default belongs here, at the operator-facing edge, where the
    operator's intent is "the database I run against".

    Returns the designation record. Raises ``DesignationError`` when the
    campaign cannot be named -- see that function for which cases and why each
    is a refusal rather than a best-effort designation.
    """
    db_path = resolve_drift_db_path(db_path)
    record = _reference.designate_reference(db_path, run_id, label=label,
                                            note=note)
    console.out("=" * 70)
    console.out("DRIFT REFERENCE DESIGNATED")
    console.out("=" * 70)
    console.out(f"Database:   {db_path}")
    console.out(f"Reference:  id {record['reference_id']}"
                + (f"  '{record['label']}'" if record["label"] else ""))
    console.out(f"Campaign:   runs "
                f"{','.join(str(r) for r in record['campaign_run_ids'])} "
                f"(head {record['campaign_head_run_id']}, "
                f"anchor {record['anchor_run_id']})")
    console.out(f"Rows:       {record['row_count']} deduplicated "
                f"({record['rows_before_dedup']} before dedup, "
                f"{record['duplicate_rows']} duplicate), "
                f"{record['patient_count']} patients")
    console.out(f"Digest:     {record['content_digest'][:16]}... "
                f"({record['digest_algorithm']}, "
                f"{len(record['digest_columns'])} columns)")
    if not record["order_agrees"]:
        console.out(f"⚠ id order and timestamp order disagree for "
                    f"{len(record['order_disagreements'])} patient(s); the id "
                    f"order was used")
        for line in record["order_disagreements"][:5]:
            console.out(f"    {line}")
    if record["campaign_membership_reason"]:
        console.out(f"⚠ campaign membership: "
                    f"{record['campaign_membership_reason']}")
    console.out("=" * 70)
    return record


def show_reference(db_path=None) -> "_reference.ReferenceResolution":
    """Resolve and print the active designation without running anything."""
    db_path = resolve_drift_db_path(db_path)
    resolution = _reference.resolve_reference(db_path)
    console.out("=" * 70)
    console.out("DRIFT REFERENCE")
    console.out("=" * 70)
    console.out(f"Database: {db_path}")
    console.out(resolution.describe())
    if resolution.detail:
        console.out(f"  {resolution.detail}")
    if resolution.expected_digest:
        console.out(f"  expected digest: {resolution.expected_digest[:16]}...")
    if resolution.actual_digest:
        console.out(f"  actual digest:   {resolution.actual_digest[:16]}...")
    console.out("=" * 70)
    return resolution


def clear_reference(db_path=None) -> int:
    """Retire the active designation. Returns how many rows were retired."""
    db_path = resolve_drift_db_path(db_path)
    retired = _reference.clear_reference(db_path)
    console.out(f"✓ Retired {retired} active drift reference row(s) in "
                f"{db_path}")
    return retired


def main(comparison, db_path=None):
    """Run drift detection and print the detailed analysis.

    Args:
        comparison: WHICH campaign to measure -- a run id, or the explicit
            opt-in ``COMPARISON_LATEST``. REQUIRED AND FIRST, with no default,
            for the reason written at ``run_drift_detection``. A ``main()``
            that defaulted it would put the implicit selection straight back
            one layer up.
        db_path: the database to read and write. ``None`` means the configured
            production one.

    Returns:
        The results dict, or ``None`` when the run itself failed. IT NO LONGER
        RETURNS ``None`` FOR AN EMPTY OR YOUNG DATABASE: that is a set of
        refusals with names, which is a result, and the shipped version's
        ``except ValueError`` turned the most common state of a new database
        into "could not run".
    """
    try:
        results = run_drift_detection(comparison, db_path=db_path)
        print_drift_details(results)
        return results

    except Exception as e:                                 # noqa: BLE001
        console.out(f"\n✗ Drift detection failed: {type(e).__name__}: {e}")
        traceback.print_exc()
        return None


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb 16 21:09:14 2026

@author: ramyalsaffar
"""
