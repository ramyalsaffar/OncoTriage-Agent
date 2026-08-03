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
"""


#------------------------------------------------------------------------------


# scipy is imported in 01- Imports.py (ks_2samp). This flag exists for
# environments where scipy might not be installed.
try:
    ks_2samp  # verify it's in namespace (loaded by exec_chain)
    SCIPY_AVAILABLE = True
except NameError:
    SCIPY_AVAILABLE = False

    
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
    comparison_window_days: int
) -> None:
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
    
    Returns:
        None (logs to database)
    
    Raises:
        ValueError: If drift_results is empty
        Exception: If database operation fails
    """
    # Validate input
    if not drift_results or not any(drift_results.values()):
        raise ValueError("drift_results cannot be empty")
    
    conn = None
    try:
        conn = sqlite3.connect(inferences_path)
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


def get_baseline_and_current_data(
    baseline_days: int = BASELINE_WINDOW_DAYS,
    comparison_days: int = COMPARISON_WINDOW_DAYS
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load baseline and current data from database.
    
    Strategy:
        - Baseline: Earliest records (first N days after first inference)
        - Current: Most recent records (last M days)
    
    Args:
        baseline_days: Number of days for baseline window
        comparison_days: Number of days for comparison window
    
    Returns:
        Tuple of (baseline_df, current_df)
    
    Raises:
        ValueError: If insufficient data for baseline or comparison
        Exception: If database operation fails
    
    Notes:
        Loads entire inferences table into memory. For production with 10,000+
        inferences, consider implementing chunked loading or database-side windowing.
    """
    conn = None
    try:
        conn = sqlite3.connect(inferences_path)
        
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
    log_to_db: bool = True
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
            comparison_days=comparison_days
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
                comparison_days
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


# ===========================================================================
# COMMAND-LINE EXECUTION
# ===========================================================================

if __name__ == "__main__":
    """
    Run drift detection when script is executed directly.
    
    Usage:
        python "20- Drift Detection.py"
    
    This will:
        1. Load last 30 days as baseline
        2. Load last 7 days as comparison
        3. Detect drift across all categories
        4. Log results to drift_metrics table
        5. Print detailed analysis
    """
    
    try:
        results = run_drift_detection()
        print_drift_details(results)
        
    except ValueError as e:
        print(f"\n✗ Drift detection cannot run: {e}")
        print("\nTo enable drift detection:")
        print("1. Run the pipeline to generate more inferences")
        print("2. Ensure inferences span at least 30 days")
        print("3. Have at least 20 inferences in the baseline period")
        
    except Exception as e:
        print(f"\n✗ Drift detection failed: {e}")
        traceback.print_exc()

#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb 16 21:09:14 2026

@author: ramyalsaffar
"""

