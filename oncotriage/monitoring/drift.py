# Drift Detection for OncoMatch Agent
#####################################

"""
Monitors data drift, retrieval drift, and performance drift in the clinical trial
matching pipeline. Uses statistical tests (KS, PSI, z-score) to detect distribution
shifts and performance degradation.

Alongside those, one THRESHOLD alert that deliberately does not compare against
a baseline: ecog_unavailable_rate. A baseline comparison answers "has this
moved", which is the wrong question when the baseline window may itself have
been captured after the thing went wrong. See that function for the reasoning.

Moved out of ``20- Drift Detection.py`` by item 20c, pass 3b.
``20- Drift Detection.py`` survives as a full re-export shim, because
``tests/test_monitoring_ecog_availability_drift.py`` exec-chains it and reads nine of these
names out of the shared namespace with no import of its own.

FILE 20 CONTAINED ZERO IMPORT STATEMENTS
----------------------------------------
Not "few". Zero. It reached for numpy, pandas, sqlite3, datetime, timezone,
Tuple, Dict, traceback, ks_2samp, inferences_path and eight config constants,
and every one of them resolved only because some OTHER file had exec'd
"01- Imports.py" and "03- Config.py" into the namespace first. So:

    python "20- Drift Detection.py"

could not work, and had not been able to work for as long as the file has
existed -- while the file's own ``__main__`` docstring told the user to run
exactly that, and "21- Streamlit Dashboard.py" line 3609 told them the same. It
would have died on ``PSI_BINS`` at the first def statement. This module has the
imports, so the entry point runs.

THREE OTHER THINGS CHANGED, and each was a defect rather than a tidy-up
----------------------------------------------------------------------

1. EVERY DATABASE READER AND WRITER TAKES ``db_path``.

   ``log_drift_metrics`` and ``get_baseline_and_current_data`` each did
   ``sqlite3.connect(inferences_path)`` against a bare global, and
   "tests/test_monitoring_ecog_availability_drift.py" line 357 REBOUND that global at a
   temporary database in order to keep its one round-trip test off the
   production inferences.db. A module function resolves its globals in its own
   module, so the moment this file became a module that rebinding would have
   reached nothing: File 41 would have written drift rows into the real database
   while printing the name of the temporary file it thought it was using.

   This is the same defect, in the same shape, that pass 20c-2b fixed for
   ``log_inference`` -- and File 41 was named in CLAUDE.md as "the one file that
   still rebinds inferences_path without passing a path", left that way until
   this pass. It is now the LAST such writer in the repository: nothing anywhere
   depends on rebinding a shared global to redirect a write.

   ``None`` still means the configured production database. File 41 passes its
   scratch path explicitly AND asserts on the path the function returns, so
   neither mechanism is a single point of failure.

2. ``log_drift_metrics`` RETURNS THE PATH IT WROTE TO.

   It returned ``None``. A caller could not assert where it had written, which
   is what makes an isolation test checkable rather than hopeful -- the same
   reasoning, and the same fix, as ``log_inference``'s return value. The
   docstring's "Returns: None (logs to database)" was true and useless.

3. ``SCIPY_AVAILABLE`` IS A REAL ImportError GUARD.

   File 20 lines 21-25 were:

       try:
           ks_2samp        # verify it's in namespace (loaded by exec_chain)
           SCIPY_AVAILABLE = True
       except NameError:
           SCIPY_AVAILABLE = False

   That tests whether somebody else's exec put a NAME in this namespace. It says
   nothing about whether scipy is installed, which is what the comment beside it
   claimed it was for. In a module the name is either imported or it is not, and
   the honest question is whether the IMPORT succeeds. It is a try/except
   ImportError around the import now, and ``ks_2samp`` is bound to ``None`` when
   it fails so that a caller reaching past the flag gets a TypeError naming the
   call rather than a NameError naming nothing.

WHAT IMPORTING THIS MODULE DOES
-------------------------------
Nothing observable: no connection, no path resolution, no query. ``scipy.stats``
is imported at module scope, which is the one thing that costs anything, and it
has to be -- the availability flag is the module's answer to "can this run", and
deferring the import into a function body would mean the flag could not be read
until after the first call that needed it.
"""

import sqlite3
import traceback
from datetime import datetime, timezone
from typing import Dict, Tuple

import numpy as np
import pandas as pd

from oncotriage import paths
from oncotriage import settings
from oncotriage.config import (
    BASELINE_WINDOW_DAYS,
    COMPARISON_WINDOW_DAYS,
    ECOG_UNAVAILABLE_RATE_THRESHOLD,
    KS_TEST_THRESHOLD,
    MIN_SAMPLES_BASELINE,
    MIN_SAMPLES_COMPARISON,
    PSI_BINS,
    PSI_THRESHOLD,
    Z_SCORE_THRESHOLD,
)


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

    IT DOES NOT CONSULT THE EXEC NAMESPACE, and that asymmetry is the point. The
    shim's wrappers are what read ``globals().get("inferences_path")``; this one
    always answers "what does a caller that passed nothing get", which is
    exactly the question "tests/test_monitoring_ecog_availability_drift.py" needs answered
    in order to show that passing its scratch path is doing any work. If this
    resolved through the namespace too, that test would be comparing a value
    against itself.

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
# STATISTICAL FUNCTIONS
# ===========================================================================

def calculate_psi(baseline: np.ndarray, current: np.ndarray, bins: int = PSI_BINS) -> Dict:
    """
    Calculate Population Stability Index (PSI).
    
    PSI measures distribution shift between two samples.
    Industry thresholds:
        PSI < 0.1  : No significant change
        PSI < 0.2  : Moderate change
        PSI >= 0.2 : Significant change (alert)
    
    Args:
        baseline: Baseline distribution (numpy array)
        current: Current distribution (numpy array)
        bins: Number of bins for discretization
        
    Returns:
        Dict with keys: metric_value, threshold, alert, notes
    """
    try:
        # Remove NaN values
        baseline_clean = baseline[~np.isnan(baseline)]
        current_clean = current[~np.isnan(current)]
        
        if len(baseline_clean) == 0 or len(current_clean) == 0:
            return {
                "metric_value": None,
                "threshold": PSI_THRESHOLD,
                "alert": 0,
                "notes": "Insufficient data (NaN values)"
            }
        
        # Create bins based on combined distribution range
        min_val = min(baseline_clean.min(), current_clean.min())
        max_val = max(baseline_clean.max(), current_clean.max())
        
        # Handle edge case: all values identical
        if max_val == min_val:
            return {
                "metric_value": 0.0,
                "threshold": PSI_THRESHOLD,
                "alert": 0,
                "notes": "No variance in data"
            }
        
        bin_edges = np.linspace(min_val, max_val, bins + 1)
        
        # Calculate proportions
        baseline_counts, _ = np.histogram(baseline_clean, bins=bin_edges)
        current_counts, _ = np.histogram(current_clean, bins=bin_edges)
        
        # Avoid division by zero with small constant
        baseline_props = (baseline_counts + 1e-6) / (baseline_counts.sum() + bins * 1e-6)
        current_props = (current_counts + 1e-6) / (current_counts.sum() + bins * 1e-6)
        
        # PSI formula: sum((current - baseline) * ln(current / baseline))
        psi_value = np.sum((current_props - baseline_props) * np.log(current_props / baseline_props))
        
        return {
            "metric_value": float(psi_value),
            "threshold": PSI_THRESHOLD,
            "alert": 1 if psi_value >= PSI_THRESHOLD else 0,
            "notes": None
        }
        
    except Exception as e:
        return {
            "metric_value": None,
            "threshold": PSI_THRESHOLD,
            "alert": 0,
            "notes": f"PSI calculation error: {str(e)}"
        }


def ks_test_drift(baseline: np.ndarray, current: np.ndarray) -> Dict:
    """
    Kolmogorov-Smirnov test for distribution drift.
    
    Tests null hypothesis that baseline and current come from same distribution.
    p-value < threshold indicates significant drift.
    
    Args:
        baseline: Baseline distribution (numpy array)
        current: Current distribution (numpy array)
        
    Returns:
        Dict with keys: metric_value, p_value, threshold, alert, notes
    """
    if not SCIPY_AVAILABLE:
        return {
            "metric_value": None,
            "p_value": None,
            "threshold": KS_TEST_THRESHOLD,
            "alert": 0,
            "notes": "scipy not installed"
        }
    
    try:
        # Remove NaN values
        baseline_clean = baseline[~np.isnan(baseline)]
        current_clean = current[~np.isnan(current)]
        
        if len(baseline_clean) < 2 or len(current_clean) < 2:
            return {
                "metric_value": None,
                "p_value": None,
                "threshold": KS_TEST_THRESHOLD,
                "alert": 0,
                "notes": "Insufficient samples for KS test (need >= 2 each)"
            }
        
        # Perform KS test
        ks_statistic, p_value = ks_2samp(baseline_clean, current_clean)
        
        return {
            "metric_value": float(ks_statistic),
            "p_value": float(p_value),
            "threshold": KS_TEST_THRESHOLD,
            "alert": 1 if p_value < KS_TEST_THRESHOLD else 0,
            "notes": None
        }
        
    except Exception as e:
        return {
            "metric_value": None,
            "p_value": None,
            "threshold": KS_TEST_THRESHOLD,
            "alert": 0,
            "notes": f"KS test error: {str(e)}"
        }


def z_score_drift(baseline: np.ndarray, current: np.ndarray) -> Dict:
    """
    Calculate z-score for mean shift detection.
    
    Measures how many standard deviations the current mean is from baseline mean.
    |z| > threshold indicates significant shift.
    
    Args:
        baseline: Baseline values (numpy array)
        current: Current values (numpy array)
        
    Returns:
        Dict with keys: metric_value (z-score), baseline_mean, baseline_std, 
                       threshold, alert, notes
    """
    try:
        # Remove NaN values
        baseline_clean = baseline[~np.isnan(baseline)]
        current_clean = current[~np.isnan(current)]
        
        if len(baseline_clean) < 2 or len(current_clean) < 1:
            return {
                "metric_value": None,
                "baseline_mean": None,
                "baseline_std": None,
                "threshold": Z_SCORE_THRESHOLD,
                "alert": 0,
                "notes": "Insufficient samples for z-score (need >= 2 baseline, >= 1 current)"
            }
        
        baseline_mean = np.mean(baseline_clean)
        baseline_std = np.std(baseline_clean, ddof=1)
        current_mean = np.mean(current_clean)
        
        # Avoid division by zero
        if baseline_std < 1e-10:
            return {
                "metric_value": None,
                "baseline_mean": float(baseline_mean),
                "baseline_std": float(baseline_std),
                "threshold": Z_SCORE_THRESHOLD,
                "alert": 0,
                "notes": "Baseline std deviation too small (< 1e-10)"
            }
        
        z_score = (current_mean - baseline_mean) / baseline_std
        
        return {
            "metric_value": float(z_score),
            "baseline_mean": float(baseline_mean),
            "baseline_std": float(baseline_std),
            "threshold": Z_SCORE_THRESHOLD,
            "alert": 1 if abs(z_score) > Z_SCORE_THRESHOLD else 0,
            "notes": None
        }
        
    except Exception as e:
        return {
            "metric_value": None,
            "baseline_mean": None,
            "baseline_std": None,
            "threshold": Z_SCORE_THRESHOLD,
            "alert": 0,
            "notes": f"Z-score calculation error: {str(e)}"
        }


# ---------------------------------------------------------------------------
# Threshold alerts (no baseline comparison)
# ---------------------------------------------------------------------------

# Text carried in `notes` when the rate alerts. It reaches
# drift_metrics.notes, so the diagnosis is stored with the alert rather than
# only printed: whoever reads the row later needs to know what to check, and
# the number on its own does not say.
ECOG_UNAVAILABLE_DIAGNOSIS = (
    "Patients had an ECOG observation on file that could not be used. A rate "
    "near 1.0 means DATA_SNAPSHOT_DATE (03- Config.py) and the patient corpus "
    "disagree -- the corpus was regenerated with observations dated after the "
    "snapshot, so every one resolves to 'all_after_reference_date' and every "
    "ECOG criterion becomes not_evaluable. Check DATA_SNAPSHOT_DATE against the "
    "generated_at/observation dates in the corpus run manifest, then re-run "
    "the affected inferences."
)


def ecog_unavailable_rate(df: pd.DataFrame) -> Dict:
    """
    Fraction of reporting rows whose ECOG observation existed but was unusable.

        numerator   ecog_selection NOT NULL
                    AND ecog_selection <> 'none_recorded'
                    AND ecog_value IS NULL
        denominator ecog_selection NOT NULL

    A THRESHOLD alert, not a baseline comparison, and deliberately so. The
    failure this catches is a corpus regenerated with a DATA_SNAPSHOT_DATE older
    than its own observations: every patient resolves to
    'all_after_reference_date', every ECOG criterion becomes not_evaluable, and
    eligible-match counts fall across the board. A z-score against baseline
    would read ~0 if the baseline window were itself captured after that
    regeneration -- the metric would go silent in exactly the case it exists
    for. A proportion is alarming at 1.0 whatever the baseline was.

    Two exclusions, and they are not the same exclusion:

      - Rows with ecog_selection NULL leave the DENOMINATOR. They predate the
        ecog_* columns (or were logged by a caller outside the graph); nothing
        is known about their ECOG, and counting them as "fine" would dilute the
        rate toward zero exactly when the corpus is oldest.
      - Rows with ecog_selection = 'none_recorded' stay in the denominator but
        leave the NUMERATOR. Those patients genuinely carried no observation,
        which is a property of the source data, not a failure of this pipeline.
        A corpus where nobody has an ECOG scores 0.0 here, correctly: there is
        no reference-date mismatch to report.

    Args:
        df: Inference rows for the window being assessed.

    Returns:
        Dict with keys: metric_value, threshold, alert, notes, plus the
        descriptive counts (numerator, denominator, rows_pre_migration,
        rows_no_observation). Shape matches the baseline-comparison metrics
        above so log_drift_metrics() and print_drift_details() need no special
        case; the extra keys are read by neither.

        metric_value is None with alert 0 when the denominator is too small.
        A zero rate and no data are different claims and must not collapse:
        every row currently in inferences predates these columns, so the first
        run after this change reports insufficient data, not 0.0.
    """
    # Base for every early return. `notes` is present and None so the key set
    # never varies between an insufficient result and a computed one -- callers
    # that read the shape must not have to know which branch produced it. Every
    # return below overrides it with a reason.
    insufficient = {
        "metric_value": None,
        "threshold": ECOG_UNAVAILABLE_RATE_THRESHOLD,
        "alert": 0,
        "notes": None,
        "numerator": None,
        "denominator": 0,
        "rows_pre_migration": None,
        "rows_no_observation": None,
    }

    try:
        # A database that never ran File 14's migration has no such columns.
        # File 20 does not load File 14, so it cannot assume they exist.
        missing = [c for c in ("ecog_selection", "ecog_value")
                   if c not in df.columns]
        if missing:
            return {**insufficient,
                    "rows_pre_migration": len(df),
                    "notes": f"Column(s) {missing} absent — database predates "
                             f"the ecog_* migration in 14- Database Logger.py"}

        reported = df["ecog_selection"].notna()
        denominator = int(reported.sum())
        rows_pre_migration = int((~reported).sum())

        if denominator == 0:
            return {**insufficient,
                    "rows_pre_migration": rows_pre_migration,
                    "notes": f"No rows report an ECOG selection path; all "
                             f"{rows_pre_migration} predate the ecog_* columns"}

        # Same floor the comparison window itself uses. A denominator of 1 that
        # happens to be unusable is a rate of 1.0 on one patient, which is noise
        # wearing the costume of the exact alarm this metric raises.
        if denominator < MIN_SAMPLES_COMPARISON:
            return {**insufficient,
                    "denominator": denominator,
                    "rows_pre_migration": rows_pre_migration,
                    "notes": f"Only {denominator} row(s) report an ECOG selection "
                             f"path (need >= {MIN_SAMPLES_COMPARISON})"}

        no_observation = reported & (df["ecog_selection"] == "none_recorded")
        unusable = reported & ~no_observation & df["ecog_value"].isna()

        numerator = int(unusable.sum())
        rate = numerator / denominator
        alert = 1 if rate > ECOG_UNAVAILABLE_RATE_THRESHOLD else 0

        return {
            "metric_value": float(rate),
            "threshold": ECOG_UNAVAILABLE_RATE_THRESHOLD,
            "alert": alert,
            "numerator": numerator,
            "denominator": denominator,
            "rows_pre_migration": rows_pre_migration,
            "rows_no_observation": int(no_observation.sum()),
            "notes": (f"{numerator}/{denominator} reporting rows had an unusable "
                      f"ECOG observation. {ECOG_UNAVAILABLE_DIAGNOSIS}")
                     if alert else None,
        }

    except Exception as e:
        return {**insufficient,
                "notes": f"ECOG availability calculation error: {str(e)}"}


# ===========================================================================
# DRIFT DETECTION FUNCTIONS
# ===========================================================================

def detect_data_availability(current_df: pd.DataFrame) -> Dict:
    """
    Assess whether the inputs the pipeline reasoned over were actually usable.

    Unlike the three detect_*_drift functions, this takes only the CURRENT
    window: it asks "is the data usable now", not "has it moved". Passing a
    baseline here would invite the comparison the metric is built to avoid.

    Args:
        current_df: Current window inferences DataFrame

    Returns:
        Dict with one entry per availability metric, same shape as the drift
        detectors.
    """
    return {
        "ecog_unavailable_rate": ecog_unavailable_rate(current_df),
    }


def detect_data_drift(baseline_df: pd.DataFrame, current_df: pd.DataFrame) -> Dict:
    """
    Detect drift in patient population characteristics.
    
    Tests:
        - Age distribution (KS test)
        - Condition count distribution (PSI)
        - Medication count distribution (PSI)
    
    Args:
        baseline_df: Baseline inferences DataFrame
        current_df: Current inferences DataFrame
        
    Returns:
        Dict with drift results for each metric
    """
    results = {}
    
    # Age distribution (KS test)
    age_result = ks_test_drift(
        baseline_df['age'].values.astype(float),
        current_df['age'].values.astype(float)
    )
    
    results['age_ks_test'] = age_result
    
    # Condition count distribution (PSI)
    condition_result = calculate_psi(
        baseline_df['condition_count'].values.astype(float),
        current_df['condition_count'].values.astype(float)
    )
    results['condition_count_psi'] = condition_result
    
    # Medication count distribution (PSI)
    medication_result = calculate_psi(
        baseline_df['medication_count'].values.astype(float),
        current_df['medication_count'].values.astype(float)
    )
    results['medication_count_psi'] = medication_result
    
    return results


def detect_retrieval_drift(baseline_df: pd.DataFrame, current_df: pd.DataFrame) -> Dict:
    """
    Detect drift in retrieval stage performance.
    
    Tests (all use z-score):
        - Average candidates retrieved
        - Average candidates reranked
        - Average candidates filtered
        - Average candidates evaluated
    
    Args:
        baseline_df: Baseline inferences DataFrame
        current_df: Current inferences DataFrame
        
    Returns:
        Dict with drift results for each metric
    """
    results = {}
    
    metrics = [
        'candidates_retrieved',
        'candidates_reranked',
        'candidates_filtered',
        'candidates_evaluated'
    ]
    
    for metric in metrics:
        result = z_score_drift(
            baseline_df[metric].values.astype(float),
            current_df[metric].values.astype(float)
        )
        results[f'{metric}_z_score'] = result
    
    return results


def detect_performance_drift(baseline_df: pd.DataFrame, current_df: pd.DataFrame) -> Dict:
    """
    Detect drift in pipeline performance metrics.
    
    Tests (all use z-score):
        - Eligible matches per patient
        - Total time per patient
        - Error rate
        - Match quality (eligible / total evaluated)
    
    Args:
        baseline_df: Baseline inferences DataFrame
        current_df: Current inferences DataFrame
        
    Returns:
        Dict with drift results for each metric
    """
    results = {}
    
    # Eligible matches
    eligible_result = z_score_drift(
        baseline_df['eligible_matches'].values,
        current_df['eligible_matches'].values
    )
    results['eligible_matches_z_score'] = eligible_result
    
    # Total time
    time_result = z_score_drift(
        baseline_df['total_time'].values,
        current_df['total_time'].values
    )
    results['total_time_z_score'] = time_result
    
    # Error rate
    baseline_errors = (baseline_df['error'].fillna('') != '').astype(float).values
    current_errors = (current_df['error'].fillna('') != '').astype(float).values
    
    error_result = z_score_drift(baseline_errors, current_errors)
    results['error_rate_z_score'] = error_result
    
    # Match quality (eligible / total evaluated)
    # Avoid division by zero
    baseline_quality = np.where(
        baseline_df['candidates_evaluated'] > 0,
        baseline_df['eligible_matches'] / baseline_df['candidates_evaluated'],
        0
    )
    current_quality = np.where(
        current_df['candidates_evaluated'] > 0,
        current_df['eligible_matches'] / current_df['candidates_evaluated'],
        0
    )
    
    quality_result = z_score_drift(baseline_quality, current_quality)
    results['match_quality_z_score'] = quality_result

    # GPT-4o retry rate — early signal of JSON output instability.
    #
    # Only meaningful for rows written after the logging-contract fix. Before
    # it, node_finalize never emitted the retry count and File 14 read a key
    # only node_error_handler wrote, so every successful inference logged 0 and
    # this z-score was computed over a column that could not vary. A baseline
    # window that straddles the fix understates the baseline mean.
    retry_result = z_score_drift(
        baseline_df['gpt4o_retries'].fillna(0).values.astype(float),
        current_df['gpt4o_retries'].fillna(0).values.astype(float)
    )
    results['gpt4o_retry_rate_z_score'] = retry_result

    return results


# ===========================================================================
# DATABASE LOGGING
# ===========================================================================

def log_drift_metrics(
    drift_results: Dict,
    baseline_window_days: int,
    comparison_window_days: int,
    db_path=None
):
    """
    Log drift detection results to SQLite database.

    Args:
        drift_results: Nested dict with structure:
            {
                "data_drift": {
                    "age_ks_test": {...},
                    "condition_count_psi": {...},
                    ...
                },
                "retrieval_drift": {...},
                "performance_drift": {...}
            }
        baseline_window_days: Number of days in baseline window
        comparison_window_days: Number of days in comparison window
        db_path: Database to write to. None means the configured production
            database -- see resolve_drift_db_path. File 41 passes a temporary
            path; before pass 20c-3b it rebound a global instead, which a module
            function cannot see.

    Returns:
        The database path this call actually used, so a caller can ASSERT where
        it wrote rather than assuming. It used to return None, which made an
        isolation test unable to check the one thing it exists to check. Same
        reasoning, and the same fix, as log_inference's return value.

        Returned only on success: every failure path below RAISES, unlike
        log_inference, whose contract is that a database fault must not kill the
        pipeline. Drift detection has no pipeline to protect, and
        run_drift_detection catches this at its own call site and reports it as
        non-critical there.

    Raises:
        ValueError: If drift_results is empty
        Exception: If database operation fails
    """
    # Validate input
    if not drift_results or not any(drift_results.values()):
        raise ValueError("drift_results cannot be empty")

    # Resolved BEFORE the try, so "which database did you aim at" is answerable
    # whether or not the write landed, and so a path that cannot be resolved
    # surfaces as itself rather than as "Database error".
    db_path = resolve_drift_db_path(db_path)

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        timestamp = datetime.now(timezone.utc).isoformat()
        rows_inserted = 0
        
        # Flatten nested structure and insert each metric
        for category, metrics in drift_results.items():
            if not metrics:
                continue
                
            for metric_name, metric_data in metrics.items():
                
                # Extract values (all optional except threshold and alert)
                metric_value = metric_data.get("metric_value")
                baseline_mean = metric_data.get("baseline_mean")
                baseline_std = metric_data.get("baseline_std")
                p_value = metric_data.get("p_value")
                z_score = metric_data.get("z_score")
                threshold = metric_data.get("threshold")
                alert = metric_data.get("alert", 0)
                notes = metric_data.get("notes")
                
                # For z-score metrics, z_score field equals metric_value
                # Only if metric explicitly computed z_score (has baseline_mean/std)
                if z_score is None and baseline_mean is not None and baseline_std is not None:
                    z_score = metric_value
                
                cursor.execute('''
                    INSERT INTO drift_metrics (
                        timestamp, metric_category, metric_name, metric_value,
                        baseline_mean, baseline_std, p_value, z_score, threshold,
                        alert, baseline_window_days, comparison_window_days, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    timestamp,
                    category,
                    metric_name,
                    metric_value,
                    baseline_mean,
                    baseline_std,
                    p_value,
                    z_score,
                    threshold,
                    alert,
                    baseline_window_days,
                    comparison_window_days,
                    notes
                ))
                
                rows_inserted += 1
        
        conn.commit()
        print(f"✓ Logged {rows_inserted} drift metrics to database")

    except sqlite3.Error as e:
        if conn:
            conn.rollback()
        raise Exception(f"Database error: {e}")

    except Exception as e:
        if conn:
            conn.rollback()
        raise Exception(f"Logging error: {e}")

    finally:
        if conn:
            conn.close()

    # AFTER the finally, not inside it. A return inside a finally block swallows
    # any exception propagating out of the try -- and every failure path above
    # is meant to propagate. Same reasoning as log_inference's return.
    return db_path


def get_baseline_and_current_data(
    baseline_days: int = BASELINE_WINDOW_DAYS,
    comparison_days: int = COMPARISON_WINDOW_DAYS,
    db_path=None
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load baseline and current data from database.
    
    Strategy:
        - Baseline: Earliest records (first N days after first inference)
        - Current: Most recent records (last M days)
    
    Args:
        baseline_days: Number of days for baseline window
        comparison_days: Number of days for comparison window
        db_path: Database to read from. None means the configured production
            database -- see resolve_drift_db_path.

    Returns:
        Tuple of (baseline_df, current_df)

    Raises:
        ValueError: If insufficient data for baseline or comparison
        Exception: If database operation fails

    Notes:
        Loads entire inferences table into memory. For production with 10,000+
        inferences, consider implementing chunked loading or database-side windowing.
    """
    db_path = resolve_drift_db_path(db_path)

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        
        # First, check total row count to warn about memory
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM inferences")
        total_rows = cursor.fetchone()[0]
        
        if total_rows == 0:
            raise ValueError("No inferences in database. Run pipeline first to generate data.")
        
        # Warn if dataset is large (production consideration)
        if total_rows > 10000:
            print(f"⚠ Large dataset detected ({total_rows} rows). Consider implementing chunked loading for better memory efficiency.")
        
        # Get all data sorted by timestamp
        query = "SELECT * FROM inferences ORDER BY timestamp ASC"
        df = pd.read_sql_query(query, conn)
        
        # Convert timestamp to datetime
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        # Get first and last timestamps
        first_timestamp = df['timestamp'].min()
        last_timestamp = df['timestamp'].max()
        
        # Calculate time span
        time_span = (last_timestamp - first_timestamp).days
        
        # Validate sufficient time span
        if time_span < baseline_days:
            raise ValueError(
                f"Insufficient time span: {time_span} days < {baseline_days} days baseline window. "
                f"Run more inferences over time."
            )
        
        # Define baseline window (first N days from start)
        baseline_end = first_timestamp + pd.Timedelta(days=baseline_days)
        baseline_df = df[df['timestamp'] <= baseline_end].copy()
        
        # Define comparison window (last M days from end)
        # Exclude any rows already in the baseline window to prevent overlap
        comparison_start = last_timestamp - pd.Timedelta(days=comparison_days)
        current_df = df[
            (df['timestamp'] >= comparison_start) &
            (df['timestamp'] > baseline_end)
        ].copy()
        
        # Validate minimum samples
        if len(baseline_df) < MIN_SAMPLES_BASELINE:
            raise ValueError(
                f"Insufficient baseline samples: {len(baseline_df)} < {MIN_SAMPLES_BASELINE}. "
                f"Need at least {MIN_SAMPLES_BASELINE} inferences in first {baseline_days} days. "
                f"Currently have {total_rows} total inferences."
            )
        
        if len(current_df) < MIN_SAMPLES_COMPARISON:
            raise ValueError(
                f"Insufficient comparison samples: {len(current_df)} < {MIN_SAMPLES_COMPARISON}. "
                f"Need at least {MIN_SAMPLES_COMPARISON} inferences in last {comparison_days} days. "
                f"Currently have {total_rows} total inferences."
            )
        
        print(f"✓ Loaded baseline: {len(baseline_df)} samples ({first_timestamp.date()} to {baseline_end.date()})")
        print(f"✓ Loaded current: {len(current_df)} samples ({comparison_start.date()} to {last_timestamp.date()})")
        
        return baseline_df, current_df
        
    except sqlite3.Error as e:
        raise Exception(f"Database error: {e}")
    
    except pd.errors.DatabaseError as e:
        raise Exception(f"Database query error: {e}")
    
    except Exception as e:
        # Re-raise ValueError as-is, wrap others
        if isinstance(e, ValueError):
            raise
        raise Exception(f"Data loading error: {e}")
    
    finally:
        if conn:
            conn.close()


# ===========================================================================
# MAIN EXECUTION
# ===========================================================================

def run_drift_detection(
    baseline_days: int = BASELINE_WINDOW_DAYS,
    comparison_days: int = COMPARISON_WINDOW_DAYS,
    log_to_db: bool = True,
    db_path=None
) -> Dict[str, Dict]:
    """
    Execute drift detection pipeline and optionally log results.
    
    Workflow:
        1. Load baseline and current data from database
        2. Detect data drift (age, conditions, medications)
        3. Detect retrieval drift (candidates at each stage)
        4. Detect performance drift (eligible matches, timing, errors)
        5. Assess data availability (threshold alerts, current window only)
        6. Log results to drift_metrics table

    Args:
        baseline_days: Number of days for baseline window (default: 30)
        comparison_days: Number of days for comparison window (default: 7)
        log_to_db: Whether to log results to database (default: True)
        db_path: Database to read from and, when log_to_db, write to. None
            means the configured production database. Threaded through to BOTH
            get_baseline_and_current_data and log_drift_metrics -- a run that
            read a scratch database and wrote its verdict into the production
            one would be worse than either.


    Returns:
        Dict with structure:
        {
            "data_drift": {...},
            "retrieval_drift": {...},
            "performance_drift": {...},
            "data_availability": {...},
            "summary": {
                "total_alerts": int,
                "baseline_samples": int,
                "comparison_samples": int,
                "baseline_period": str,
                "comparison_period": str
            }
        }
    
    Raises:
        ValueError: If insufficient data for drift detection
        Exception: If drift detection or logging fails
    """
    
    print("=" * 70)
    print("DRIFT DETECTION PIPELINE")
    print("=" * 70)
    print(f"Baseline window: {baseline_days} days")
    print(f"Comparison window: {comparison_days} days")
    print()
    
    # Step 1: Load data
    print("[1/5] Loading baseline and current data...")
    try:
        baseline_df, current_df = get_baseline_and_current_data(
            baseline_days=baseline_days,
            comparison_days=comparison_days,
            db_path=db_path
        )
        
        # Validate required columns exist
        required_cols = [
            'timestamp', 'age', 'condition_count', 'medication_count',
            'candidates_retrieved', 'candidates_reranked', 'candidates_filtered',
            'candidates_evaluated', 'eligible_matches', 'total_time', 'error',
            'gpt4o_retries', 'ablation_flags'
        ]
        
        missing_cols = [col for col in required_cols if col not in baseline_df.columns]
        if missing_cols:
            raise ValueError(f"Missing required columns in data: {missing_cols}")
            
    except ValueError as e:
        print(f"✗ {e}")
        raise
    except Exception as e:
        print(f"✗ Data loading failed: {e}")
        raise
    print()
    
    # Step 2: Detect data drift
    print("[2/5] Detecting data drift...")
    try:
        data_drift = detect_data_drift(baseline_df, current_df)
        data_alerts = sum(1 for m in data_drift.values() if m.get("alert") == 1)
        print(f"✓ Data drift: {data_alerts} alert(s)")
    except Exception as e:
        print(f"✗ Data drift detection failed: {e}")
        raise
    print()
    
    # Step 3: Detect retrieval drift
    print("[3/5] Detecting retrieval drift...")
    try:
        retrieval_drift = detect_retrieval_drift(baseline_df, current_df)
        retrieval_alerts = sum(1 for m in retrieval_drift.values() if m.get("alert") == 1)
        print(f"✓ Retrieval drift: {retrieval_alerts} alert(s)")
    except Exception as e:
        print(f"✗ Retrieval drift detection failed: {e}")
        raise
    print()
    
    # Step 4: Detect performance drift
    print("[4/6] Detecting performance drift...")
    try:
        performance_drift = detect_performance_drift(baseline_df, current_df)
        performance_alerts = sum(1 for m in performance_drift.values() if m.get("alert") == 1)
        print(f"✓ Performance drift: {performance_alerts} alert(s)")
    except Exception as e:
        print(f"✗ Performance drift detection failed: {e}")
        raise
    print()

    # Step 5: Assess input availability (threshold alerts, current window only)
    print("[5/6] Assessing data availability...")
    try:
        data_availability = detect_data_availability(current_df)
        availability_alerts = sum(1 for m in data_availability.values() if m.get("alert") == 1)
        print(f"✓ Data availability: {availability_alerts} alert(s)")
    except Exception as e:
        print(f"✗ Data availability assessment failed: {e}")
        raise
    print()

    # Compile results
    results = {
        "data_drift": data_drift,
        "retrieval_drift": retrieval_drift,
        "performance_drift": performance_drift,
        "data_availability": data_availability,
        "summary": {
            "total_alerts": (data_alerts + retrieval_alerts + performance_alerts
                             + availability_alerts),
            "baseline_samples": len(baseline_df),
            "comparison_samples": len(current_df),
            "baseline_period": f"{baseline_df['timestamp'].min().date()} to {baseline_df['timestamp'].max().date()}",
            "comparison_period": f"{current_df['timestamp'].min().date()} to {current_df['timestamp'].max().date()}"
        }
    }
    
    # Step 6: Log to database
    if log_to_db:
        print("[6/6] Logging results to database...")
        try:
            log_drift_metrics(
                {k: v for k, v in results.items() if k != "summary"},
                baseline_days,
                comparison_days,
                db_path=db_path
            )
        except Exception as e:
            print(f"⚠ Database logging failed (non-critical): {e}")
            # Don't raise - logging failure should not break drift detection
    else:
        print("[6/6] Skipping database logging (log_to_db=False)")
    print()
    
    # Print summary
    print("=" * 70)
    print("DRIFT DETECTION SUMMARY")
    print("=" * 70)
    print(f"Total alerts: {results['summary']['total_alerts']}")
    print(f"  - Data drift: {data_alerts}")
    print(f"  - Retrieval drift: {retrieval_alerts}")
    print(f"  - Performance drift: {performance_alerts}")
    print(f"  - Data availability: {availability_alerts}")
    print()
    print(f"Baseline: {results['summary']['baseline_samples']} samples ({results['summary']['baseline_period']})")
    print(f"Current: {results['summary']['comparison_samples']} samples ({results['summary']['comparison_period']})")
    print("=" * 70)
    
    return results


def print_drift_details(results: Dict[str, Dict]) -> None:
    """
    Pretty-print drift detection results with alert highlighting.
    
    Args:
        results: Output from run_drift_detection()
    """
    
    print("\n" + "=" * 70)
    print("DETAILED DRIFT ANALYSIS")
    print("=" * 70)
    
    for category in ["data_drift", "retrieval_drift", "performance_drift",
                     "data_availability"]:
        if category not in results:
            continue
            
        print(f"\n{category.upper().replace('_', ' ')}")
        print("-" * 70)
        
        metrics = results[category]
        for metric_name, metric_data in metrics.items():
            alert = metric_data.get("alert", 0)
            value = metric_data.get("metric_value")
            threshold = metric_data.get("threshold")
            notes = metric_data.get("notes")
            
            # Alert indicator
            indicator = "🚨" if alert else "✓"
            
            # Format metric name
            display_name = metric_name.replace("_", " ").title()
            
            print(f"{indicator} {display_name}")
            
            # Value and threshold (check for None to avoid format errors)
            if value is not None and threshold is not None:
                print(f"   Value: {value:.4f} | Threshold: {threshold}")
            elif notes:
                print(f"   Status: {notes}")

            # An alerting metric prints its notes even when it has a value.
            # Previously notes were shown only on the no-value branch, so a
            # threshold alert that carries its diagnosis in notes -- which is
            # the whole reason it carries one -- printed the number and
            # swallowed the explanation.
            if value is not None and notes:
                print(f"   Note: {notes}")

            # Additional details
            p_value = metric_data.get("p_value")
            if p_value is not None:
                print(f"   P-value: {p_value:.4f}")
            
            baseline_mean = metric_data.get("baseline_mean")
            baseline_std = metric_data.get("baseline_std")
            if baseline_mean is not None and baseline_std is not None:
                print(f"   Baseline: μ={baseline_mean:.2f}, σ={baseline_std:.2f}")

            print()

    print("=" * 70)


#------------------------------------------------------------------------------


# ===========================================================================
# COMMAND-LINE EXECUTION
# ===========================================================================

def main(db_path=None):
    """Run drift detection and print the detailed analysis.

    THE BODY OF "20- Drift Detection.py"'s ``__main__`` BLOCK, moved here so
    that block can be three lines.

    It could not previously run at all. File 20 contained zero import
    statements, so ``python "20- Drift Detection.py"`` died on ``PSI_BINS`` at
    the first ``def`` -- while the ``__main__`` docstring told the user to run
    exactly that command, and "21- Streamlit Dashboard.py" line 3609 told them
    the same. Both instructions are true for the first time.

    Args:
        db_path: Database to read and write. None means the configured
            production database.

    Returns:
        The results dict on success, or None when drift detection could not run
        or failed. Returning rather than exiting: an exit code is the entry
        point's decision, and a caller embedding this needs the difference
        between "ran and found nothing" and "could not run".

    Both handlers below print and swallow, exactly as File 20's __main__ did. A
    ValueError here means "not enough data yet", which is the ordinary state of
    a young database and not a failure of the process; anything else prints its
    traceback, so the cause is on the terminal rather than only in the message.
    """
    try:
        results = run_drift_detection(db_path=db_path)
        print_drift_details(results)
        return results

    except ValueError as e:
        print(f"\n✗ Drift detection cannot run: {e}")
        print("\nTo enable drift detection:")
        print("1. Run the pipeline to generate more inferences")
        print("2. Ensure inferences span at least 30 days")
        print("3. Have at least 20 inferences in the baseline period")
        return None

    except Exception as e:
        print(f"\n✗ Drift detection failed: {e}")
        traceback.print_exc()
        return None


# ===========================================================================
# COMMAND-LINE EXECUTION
# ===========================================================================


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb 16 21:09:14 2026

@author: ramyalsaffar
"""
