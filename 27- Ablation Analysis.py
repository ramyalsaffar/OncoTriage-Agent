# Ablation Study Analysis
##########################################

"""
Ablation Analysis & Visualization

Reads ablation_results.db produced by File 26 (Ablation Study) and generates
publication-ready tables, figures, and statistical tests for the paper.

Outputs (saved to result_ablation_path):
    Tables:
    - ablation_comparison_table.csv     Main comparison table with 95% CIs
                                        (each CI carries the n it was drawn on)
    - ablation_statistical_tests.csv    Wilcoxon tests, BH-FDR corrected, one
                                        row per (config, outcome metric), with
                                        raw p, adjusted p, signed effect size
                                        and a status for untested comparisons
    - ablation_descriptive_metrics.csv  Cost / latency / candidate deltas,
                                        reported without p-values by design
    - ablation_pairing_report.csv       Per-config paired and DROPPED patient
                                        sets with a reason for each drop
    - ablation_win_loss_table.csv       Per-patient pairwise wins/ties/losses

    Figures:
    - ablation_funnel_chart.png         Per-stage candidate funnel by config
    - ablation_delta_chart.png          Delta from baseline bar chart
    - ablation_cost_efficiency.png      Cost-per-eligible-match by config
    - ablation_score_distribution.png   Match score distributions (box plot)
    - ablation_cancer_group_heatmap.png Eligible count by config × cancer group
    - ablation_timing_breakdown.png     Stacked bar of per-stage latency
    - ablation_retrieval_venn.png       BM25 vs Vector unique trial contributions
    - ablation_win_loss_chart.png       Pairwise win/tie/loss bar chart
    - ablation_patient_scatter.png      Baseline vs ablated eligible per patient

    Reports:
    - ablation_full_report.txt          Plain-text report for quick review
    - ablation_analysis.json            Machine-readable summary

Statistics:
    The test family is (non-baseline configs) x ABLATION_OUTCOME_METRICS and is
    corrected with Benjamini-Hochberg FDR. Cost and latency are excluded from
    the family as deterministic consequences of the ablation, not hypotheses.
    Comparisons that could not be tested (identical values, scipy failure, too
    few pairs) are recorded with a status and excluded from the correction
    rather than entered as p=1.0. Effect sizes are SIGNED. The minimum
    detectable effect for the design is computed once and printed in the
    report's methods block. See the block comment above run_statistical_tests().

Architecture:
    - Exec chain: 01 -> 02 -> 03 (for paths, config constants)
    - Reads ablation_results.db (never writes to it)
    - All outputs go to result_ablation_path

Run from terminal:
    cd "/Users/ramyalsaffar/Ramy/C.V..V/07- LLM Projects/03- Clinical Trial Patient Match/03- Code/"
    python "27- Ablation Analysis.py"
"""


# ===========================================================================
# EXEC CHAIN: 01 -> 02 -> 03
# ===========================================================================

_code_dir = "/Users/ramyalsaffar/Ramy/C.V..V/07- LLM Projects/03- Clinical Trial Patient Match/03- Code/"

for _bootstrap in ("01- Imports.py", "02- Utility Functions.py"):
    with open(_code_dir + _bootstrap) as _fh:
        exec(_fh.read(), globals())

exec_chain(
    ["03- Config.py"],
    caller_file=_code_dir + "27- Ablation Analysis.py",
    caller_globals=globals(),
    chain_label="01 → 02 → 03",
)


#------------------------------------------------------------------------------


# ===========================================================================
# CONSTANTS
# ===========================================================================

ABLATION_DB = Path(result_ablation_path) / "ablation_results.db"
OUTPUT_DIR = Path(result_ablation_path)

# Config display order (matches File 26 ABLATION_CONFIGS)
CONFIG_ORDER = [
    "full_pipeline",
    "no_mesh_filter",
    "no_stage_filter",
    "no_histology_filter",
    "no_cross_encoder",
    "bm25_only",
    "vector_only",
]

CONFIG_LABELS = {
    "full_pipeline":       "Full Pipeline (baseline)",
    "no_mesh_filter":      "− MeSH Filter",
    "no_stage_filter":     "− Stage Filter",
    "no_histology_filter": "− Histology Filter",
    "no_cross_encoder":    "− Cross-Encoder",
    "bm25_only":           "BM25 Only",
    "vector_only":         "Vector Only",
}

BASELINE = "full_pipeline"


# ===========================================================================
# DATA LOADING
# ===========================================================================

def load_ablation_data() -> pd.DataFrame:
    """Load ablation_results table into a DataFrame."""
    if not ABLATION_DB.exists():
        print(f"ERROR: {ABLATION_DB} not found. Run File 26 first.")
        sys.exit(1)

    conn = sqlite3.connect(str(ABLATION_DB))
    try:
        df = pd.read_sql_query("""
            SELECT r.*, runs.config_description
            FROM ablation_results r
            JOIN ablation_runs runs ON r.run_id = runs.id
            WHERE r.error = '' OR r.error IS NULL
        """, conn)
    finally:
        conn.close()

    # Ordered categorical for consistent plotting
    df["config_name"] = pd.Categorical(
        df["config_name"], categories=CONFIG_ORDER, ordered=True
    )
    df["config_label"] = df["config_name"].map(CONFIG_LABELS)

    # Keep only the most recent run per (config_name, patient_id) by max run_id
    before = len(df)
    df = df.sort_values("run_id", ascending=False).drop_duplicates(
        subset=["config_name", "patient_id"], keep="first"
    )
    if len(df) < before:
        print(f"  WARNING: Dropped {before - len(df)} duplicate rows (kept most recent run)")

    # ── Match rate: the metric every conditional mean is conditioned on ─────
    df["has_match"] = (df["eligible_count"] > 0).astype(int)

    # ── Unconditional match score ───────────────────────────────────────────
    #
    # avg_match_score is NULL for a patient with no eligible trial, and both
    # SQL AVG() and pandas .mean() skip nulls. Averaging it therefore averages
    # over "patients this configuration managed to match" — a subpopulation the
    # configuration itself selects. A configuration that destroys recall keeps
    # only its most confident matches and scores HIGHER on that mean.
    #
    # avg_match_score_all fixes the population: a patient who received no
    # eligible trial received no match quality, which is 0.0, not missing.
    # File 26 writes the column; databases built before it did not, so it is
    # derived here with the same convention.
    if "avg_match_score_all" in df.columns:
        _missing_all = df["avg_match_score_all"].isna()
        if _missing_all.any():
            df.loc[_missing_all, "avg_match_score_all"] = (
                df.loc[_missing_all, "avg_match_score"].fillna(0.0)
            )
            print(f"  Backfilled avg_match_score_all for {int(_missing_all.sum())} "
                  f"pre-migration row(s) from avg_match_score (null -> 0.0)")
    else:
        df["avg_match_score_all"] = df["avg_match_score"].fillna(0.0)
        print("  avg_match_score_all absent from database (pre-migration): "
              "derived from avg_match_score with null -> 0.0")

    # ── Efficiency ratios ───────────────────────────────────────────────────
    #
    # These per-patient ratios are UNDEFINED when eligible_count == 0, so they
    # carry the same conditioning defect as the raw score: dropping the
    # zero-match patients drops exactly the patients whose spend bought
    # nothing. They are retained only for the distribution plots and are always
    # reported with their own n. The headline figure is the POOLED ratio
    # computed in build_comparison_table (total spend / total matches), which
    # keeps a failed patient's cost in the numerator.
    df["cost_per_eligible"] = df.apply(
        lambda row: row["estimated_cost_usd"] / row["eligible_count"]
        if row["eligible_count"] > 0 else None, axis=1
    )
    df["total_tokens"] = df["gpt4o_input_tokens"] + df["gpt4o_output_tokens"]
    df["tokens_per_eligible"] = df.apply(
        lambda row: row["total_tokens"] / row["eligible_count"]
        if row["eligible_count"] > 0 else None, axis=1
    )

    print(f"Loaded {len(df)} results ({df['config_name'].nunique()} configs, "
          f"{df['patient_id'].nunique()} patients)")

    return df


def load_error_data() -> pd.DataFrame:
    """Load error rows separately for error rate analysis."""
    conn = sqlite3.connect(str(ABLATION_DB))
    try:
        df = pd.read_sql_query("""
            SELECT config_name, patient_id, error
            FROM ablation_results
            WHERE error != '' AND error IS NOT NULL
        """, conn)
    finally:
        conn.close()
    return df


# ===========================================================================
# 1. COMPARISON TABLE (main paper table)
# ===========================================================================

def build_comparison_table(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build the main comparison table with per-config averages and deltas.

    Columns: config, avg_eligible, avg_score, avg_cost, avg_time,
    avg_candidates_evaluated, avg_mesh_dropped, avg_stage_dropped,
    avg_histology_dropped, plus delta columns vs baseline.
    """
    table = df.groupby("config_name", observed=True).agg(
        eligible_mean       =("eligible_count", "mean"),
        eligible_std        =("eligible_count", "std"),
        # Headline quality metric: unconditional, over all sampled patients.
        score_mean          =("avg_match_score_all", "mean"),
        score_std           =("avg_match_score_all", "std"),
        # Recall, reported as a first-class metric rather than left implicit in
        # the null pattern of the conditional score.
        match_rate          =("has_match", "mean"),
        n_scored            =("has_match", "sum"),
        # Conditional twin, kept for reference and never reported without
        # n_scored beside it.
        score_cond_mean     =("avg_match_score", "mean"),
        score_cond_std      =("avg_match_score", "std"),
        cost_mean           =("estimated_cost_usd", "mean"),
        cost_std            =("estimated_cost_usd", "std"),
        time_mean           =("total_time", "mean"),
        time_std            =("total_time", "std"),
        evaluated_mean      =("candidates_evaluated", "mean"),
        after_rules_mean    =("candidates_after_rule_filter", "mean"),
        after_quality_mean  =("candidates_after_quality_filter", "mean"),
        mesh_dropped_mean   =("mesh_dropped", "mean"),
        stage_dropped_mean  =("stage_dropped", "mean"),
        histo_dropped_mean  =("histology_dropped", "mean"),
        input_tokens_mean   =("gpt4o_input_tokens", "mean"),
        # Conditional per-patient ratios. Reported only with n_scored.
        cost_per_elig_mean  =("cost_per_eligible", "mean"),
        tokens_per_elig_mean=("tokens_per_eligible", "mean"),
        n_patients          =("patient_id", "count"),
        _total_cost         =("estimated_cost_usd", "sum"),
        _total_tokens       =("total_tokens", "sum"),
        _total_eligible     =("eligible_count", "sum"),
    ).reset_index()

    # ── Pooled efficiency: the reportable cost/token per eligible match ─────
    #
    # total spend over total matches, across ALL sampled patients. A patient
    # the configuration failed to match still contributes their tokens and
    # dollars to the numerator and a zero to the denominator, so a
    # recall-destroying configuration cannot appear efficient by having its
    # failures dropped as nulls. NaN only when the config matched nothing at all.
    table["cost_per_elig_pooled"] = (
        table["_total_cost"] / table["_total_eligible"].replace(0, np.nan)
    )
    table["tokens_per_elig_pooled"] = (
        table["_total_tokens"] / table["_total_eligible"].replace(0, np.nan)
    )
    table = table.drop(columns=["_total_cost", "_total_tokens", "_total_eligible"])

    # Bootstrapped 95% CIs (TrialGPT standard: Nature Communications)
    #
    # The generator is seeded ONCE PER CONFIGURATION, outside the column loop.
    # Reseeding inside it made every metric of every configuration resample the
    # same index sequence: reproducible, but the intervals were not independent
    # draws and correlated errors across metrics were invisible.
    #
    # avg_match_score_all and has_match are the CI'd quality metrics; the
    # conditional avg_match_score is deliberately absent, because its
    # resampling population differs by configuration and the resulting
    # intervals would not be comparable.
    #
    # Each interval resamples col.dropna(), so the population it is drawn from
    # is not necessarily the population the mean beside it was computed over,
    # and its size varies by configuration. {col}_ci_n records the number of
    # values actually resampled and is printed with every interval; an interval
    # whose n differs from n_patients is decorating a mean it does not describe.
    N_BOOT = 1000
    BOOT_SEED = 42
    ci_cols = ["eligible_count", "avg_match_score_all", "has_match",
               "estimated_cost_usd", "total_time"]
    for config in CONFIG_ORDER:
        config_data = df[df["config_name"] == config]
        idx_rows = table[table["config_name"] == config].index
        if len(idx_rows) == 0:
            continue
        idx = idx_rows[0]

        rng = np.random.default_rng(BOOT_SEED)
        for col in ci_cols:
            vals = config_data[col].dropna().values
            table.loc[idx, f"{col}_ci_n"] = len(vals)
            if len(vals) < 2:
                table.loc[idx, f"{col}_ci_lo"] = np.nan
                table.loc[idx, f"{col}_ci_hi"] = np.nan
                print(f"  WARNING: {config}/{col}: {len(vals)} non-null value(s), "
                      f"CI not computed")
                continue
            if len(vals) < len(config_data):
                print(f"  NOTE: {config}/{col}: CI bootstrapped on {len(vals)} of "
                      f"{len(config_data)} patients ({len(config_data) - len(vals)} "
                      f"null); interval and mean cover different populations")
            boot_means = [
                rng.choice(vals, size=len(vals), replace=True).mean()
                for _ in range(N_BOOT)
            ]
            table.loc[idx, f"{col}_ci_lo"] = np.percentile(boot_means, 2.5)
            table.loc[idx, f"{col}_ci_hi"] = np.percentile(boot_means, 97.5)


    # Compute deltas vs baseline
    bl_rows = table[table["config_name"] == BASELINE]
    if bl_rows.empty:
        print("  WARNING: Baseline config not found in results. Deltas skipped.")
        table["config_label"] = table["config_name"].map(CONFIG_LABELS)
        return table

    baseline = bl_rows.iloc[0]

    # cost_per_elig_mean is excluded: it is a conditional mean whose population
    # differs between the two configs being differenced, so its delta is not
    # attributable to the ablation. The pooled ratio is differenced instead.
    for col in ["eligible_mean", "score_mean", "match_rate", "cost_mean", "time_mean",
                "evaluated_mean", "cost_per_elig_pooled", "input_tokens_mean"]:
        delta_col = f"Δ_{col}"
        table[delta_col] = table[col] - baseline[col]


    # Percent change
    for col in ["eligible_mean", "cost_mean", "time_mean"]:
        pct_col = f"Δ%_{col}"
        if baseline[col] != 0:
            table[pct_col] = ((table[col] - baseline[col]) / baseline[col] * 100).round(1)
        else:
            table[pct_col] = 0.0
            
            
    # Add labels
    table["config_label"] = table["config_name"].map(CONFIG_LABELS)

    return table


# ===========================================================================
# 2. STATISTICAL TESTS (paired per-patient comparisons)
# ===========================================================================
#
# Design of the test family, stated here because it is the thing a reader has
# to trust:
#
#   - Family    = (every non-baseline config) x (ABLATION_OUTCOME_METRICS).
#                 With 6 configs and 3 metrics that is 18 tests.
#   - Excluded  = cost, latency and candidate counts. Removing the
#                 cross-encoder makes the pipeline cheaper by construction;
#                 that is an accounting identity, not a hypothesis, and
#                 carrying it in the family only spends error budget. These
#                 are reported descriptively by build_descriptive_deltas().
#   - Excluded  = any (config, metric) where the two configurations produced
#                 identical values for every patient. No test is possible, so
#                 none is recorded. Writing p = 1.0 there would insert a
#                 fabricated observation into the family and, because BH ranks
#                 p-values, a fabricated p = 1.0 inflates the denominator and
#                 makes the surviving tests harder to reject.
#   - Excluded  = any config with fewer than ABLATION_MIN_PAIRED paired
#                 patients, reported as an exclusion rather than dropped.
#   - Correction= Benjamini-Hochberg FDR at ABLATION_FDR_ALPHA over whatever
#                 tests actually ran. Both raw and adjusted p are reported;
#                 significance stars are assigned on the adjusted p only.
#
# Wilcoxon-adjacent facts (not tunable, hence inline):

# Asymptotic relative efficiency of the Wilcoxon signed-rank test against the
# paired t-test under normality. Used only to convert a t-test power
# calculation into a Wilcoxon-appropriate minimum detectable effect.
WILCOXON_ARE_VS_T = 0.955

# Rank-biserial magnitude bands (Cohen's conventional cutoffs).
EFFECT_BANDS = ((0.5, "large"), (0.3, "medium"), (0.1, "small"))


def benjamini_hochberg(pvals: list) -> tuple:
    """
    Benjamini-Hochberg step-up FDR control.

    Returns (adjusted_pvals, rejected) in the caller's original order.
    Adjusted p is the BH q-value: the smallest FDR level at which that test
    would be declared significant, enforced monotone non-decreasing in p and
    capped at 1.0. `rejected` uses the standard step-up rule, which is
    equivalent to adjusted_p <= alpha.

    An empty family returns empty lists rather than raising: a family can
    legitimately be empty when every comparison was identical or excluded.
    """
    m = len(pvals)
    if m == 0:
        return [], []

    order = sorted(range(m), key=lambda i: pvals[i])
    adjusted = [0.0] * m

    # Step up from the largest p, carrying the running minimum so the adjusted
    # values are monotone in p.
    running_min = 1.0
    for rank_from_one, idx in reversed(list(enumerate(order, start=1))):
        q = pvals[idx] * m / rank_from_one
        running_min = min(running_min, q)
        adjusted[idx] = min(1.0, running_min)

    rejected = [adjusted[i] <= ABLATION_FDR_ALPHA for i in range(m)]
    return adjusted, rejected


def signed_rank_biserial(diff: np.ndarray) -> tuple:
    """
    Signed matched-pairs rank-biserial correlation for a paired sample.

    r = (R+ - R-) / (R+ + R-), where R+ and R- are the summed ranks of the
    absolute non-zero differences that were positive and negative. r is bounded
    by [-1, +1] and carries direction: with diff = ablated - baseline, r > 0
    means the ablated configuration scored HIGHER than baseline.

    This replaces r = 1 - 2W / (n(n+1)/2). scipy's two-sided Wilcoxon statistic
    is min(R+, R-), so that formula is a function of the smaller rank sum only
    and is non-negative by construction: a configuration that is uniformly
    worse and one that is uniformly better both returned r = +1.000, which
    reports that something moved without reporting which way.

    Returns (r, n_nonzero). r is None when every difference is zero.
    """
    from scipy.stats import rankdata

    nonzero = diff[diff != 0]
    if len(nonzero) == 0:
        return None, 0

    ranks = rankdata(np.abs(nonzero))
    r_plus = float(ranks[nonzero > 0].sum())
    r_minus = float(ranks[nonzero < 0].sum())
    total = r_plus + r_minus
    if total == 0:
        return None, len(nonzero)

    return (r_plus - r_minus) / total, len(nonzero)


def _effect_band(r) -> str:
    """Magnitude label for a signed effect size; direction stays in the sign."""
    if r is None:
        return ""
    a = abs(r)
    for cutoff, label in EFFECT_BANDS:
        if a >= cutoff:
            return label
    return "negligible"


def build_pairing_report(df: pd.DataFrame, errors: pd.DataFrame) -> pd.DataFrame:
    """
    Per-configuration account of which patients were paired against the
    baseline and which were dropped, with a reason for each drop.

    n_paired alone says how many patients survived; it does not say that the
    surviving set is the SAME set across configurations. load_ablation_data
    filters errored rows per configuration, so each configuration can silently
    lose a different subset, and every cross-configuration comparison is then
    made over a slightly different population. This function names the dropped
    patients so that stops being invisible.

    Drop reasons:
        errored          - the run raised; excluded by load_ablation_data
        missing_from_run - never present in the database for this config
        baseline_missing - present here but the baseline lacks this patient,
                           so no pair exists
    """
    baseline_ids = set(df[df["config_name"] == BASELINE]["patient_id"])

    # Universe = every patient the study attempted anywhere, successful or not.
    universe = set(df["patient_id"])
    if len(errors):
        universe |= set(errors["patient_id"])

    err_by_config = {}
    if len(errors):
        for cfg, grp in errors.groupby("config_name"):
            err_by_config[cfg] = dict(zip(grp["patient_id"], grp["error"]))

    rows = []
    for config in CONFIG_ORDER:
        present = set(df[df["config_name"] == config]["patient_id"])
        cfg_errors = err_by_config.get(config, {})

        dropped = {}
        for pid in sorted(universe - present):
            if pid in cfg_errors:
                dropped[pid] = f"errored: {str(cfg_errors[pid])[:80]}"
            else:
                dropped[pid] = "missing_from_run"

        if config == BASELINE:
            shared = present
            baseline_missing = set()
        else:
            shared = present & baseline_ids
            baseline_missing = present - baseline_ids
            for pid in sorted(baseline_missing):
                dropped[pid] = "baseline_missing"

        rows.append({
            "config_name": config,
            "config_label": CONFIG_LABELS[config],
            "n_universe": len(universe),
            "n_present": len(present),
            "n_paired": len(shared),
            "n_dropped": len(dropped),
            "n_errored": len(cfg_errors),
            "dropped_patient_ids": ";".join(sorted(dropped)),
            "dropped_reasons": ";".join(f"{p}={r}" for p, r in sorted(dropped.items())),
            "in_test_family": (config != BASELINE
                               and len(shared) >= ABLATION_MIN_PAIRED),
        })

    return pd.DataFrame(rows)


def run_statistical_tests(df: pd.DataFrame, pairing: pd.DataFrame) -> pd.DataFrame:
    """
    Paired Wilcoxon signed-rank tests, one row per (config, outcome metric),
    Benjamini-Hochberg corrected across the whole family.

    Long format, not one wide row per config, because the correction is a
    property of the family and not of any single configuration.

    Columns:
        status       tested | not_tested_identical | not_tested_error
        p_raw        uncorrected two-sided p (None unless status == tested)
        p_adj        BH-adjusted q-value across the family
        sig          stars from p_adj, never from p_raw
        effect_r     SIGNED rank-biserial; > 0 means ablated > baseline
        in_family    whether this row contributed to the correction

    Rows with status != tested carry no p at all. They are recorded so the
    reader can see the comparison was attempted, and excluded from `in_family`
    so they cannot dilute the correction.

    Only ABLATION_OUTCOME_METRICS are tested. avg_match_score_all is used
    rather than avg_match_score: the conditional column is NULL for every
    zero-match patient and the null pattern differs between the two configs
    being paired, so the paired difference would be NaN for exactly the
    patients whose loss the ablation caused. has_match tests the recall change
    itself, which is what the conditional column was hiding.
    """
    from scipy.stats import wilcoxon

    baseline_df = df[df["config_name"] == BASELINE].set_index("patient_id")
    pairing_idx = pairing.set_index("config_name")
    results = []
    n_scipy_failures = 0

    for config in CONFIG_ORDER:
        if config == BASELINE:
            continue

        config_df = df[df["config_name"] == config].set_index("patient_id")
        shared = baseline_df.index.intersection(config_df.index)

        n_dropped = int(pairing_idx.loc[config, "n_dropped"]) \
            if config in pairing_idx.index else 0

        if len(shared) < ABLATION_MIN_PAIRED:
            # Previously a bare `continue`: the configuration vanished from the
            # output with no row and no warning, and a reader counting rows had
            # no way to tell an excluded configuration from one never run.
            print(f"  EXCLUDED from test family: {config} has {len(shared)} "
                  f"paired patient(s), below ABLATION_MIN_PAIRED="
                  f"{ABLATION_MIN_PAIRED}")
            for col in ABLATION_OUTCOME_METRICS:
                results.append({
                    "config_name": config, "config_label": CONFIG_LABELS[config],
                    "metric": col, "n_paired": len(shared), "n_dropped": n_dropped,
                    "n_nonzero": None, "stat": None, "p_raw": None,
                    "effect_r": None, "effect_size": "",
                    "status": "not_tested_insufficient_pairs",
                    "detail": f"n_paired={len(shared)} < {ABLATION_MIN_PAIRED}",
                    "in_family": False,
                })
            continue

        for col in ABLATION_OUTCOME_METRICS:
            bl_vals = baseline_df.loc[shared, col].values.astype(float)
            cf_vals = config_df.loc[shared, col].values.astype(float)
            diff = cf_vals - bl_vals

            row = {
                "config_name": config, "config_label": CONFIG_LABELS[config],
                "metric": col, "n_paired": len(shared), "n_dropped": n_dropped,
            }

            if np.all(diff == 0):
                # No test is possible and none is recorded. The old code wrote
                # p = 1.0 / effect 0.0 here, which is an observation that was
                # never made.
                row.update({
                    "n_nonzero": 0, "stat": None, "p_raw": None,
                    "effect_r": None, "effect_size": "",
                    "status": "not_tested_identical",
                    "detail": "all paired differences zero; no test performed",
                    "in_family": False,
                })
                results.append(row)
                continue

            try:
                stat, p = wilcoxon(diff, alternative="two-sided")
            except Exception as exc:
                # Recorded as a distinct status with the exception text, so a
                # scipy failure can never be read as a genuine null.
                n_scipy_failures += 1
                print(f"  WARNING: wilcoxon failed for {config}/{col}: "
                      f"{type(exc).__name__}: {exc}")
                row.update({
                    "n_nonzero": int(np.count_nonzero(diff)), "stat": None,
                    "p_raw": None, "effect_r": None, "effect_size": "",
                    "status": "not_tested_error",
                    "detail": f"{type(exc).__name__}: {exc}",
                    "in_family": False,
                })
                results.append(row)
                continue

            r, n_nonzero = signed_rank_biserial(diff)
            row.update({
                "n_nonzero": n_nonzero,
                "stat": round(float(stat), 2),
                "p_raw": float(p),
                "effect_r": None if r is None else round(r, 3),
                "effect_size": _effect_band(r),
                "status": "tested",
                "detail": "",
                "in_family": True,
            })
            results.append(row)

    stats = pd.DataFrame(results)
    if stats.empty:
        print("  WARNING: no comparisons produced; test family is empty")
        return stats

    # ── Benjamini-Hochberg over the family that actually ran ────────────────
    family_mask = stats["in_family"].fillna(False).astype(bool)
    family_p = stats.loc[family_mask, "p_raw"].tolist()
    adjusted, rejected = benjamini_hochberg(family_p)

    stats["p_adj"] = None
    stats["sig"] = ""
    stats["rejected_fdr"] = False
    stats.loc[family_mask, "p_adj"] = [round(a, 4) for a in adjusted]
    stats.loc[family_mask, "rejected_fdr"] = rejected

    # Stars come from the ADJUSTED p. Applying them to raw p is what made 18
    # tests look like 18 independent chances to be right.
    stats.loc[family_mask, "sig"] = [
        "***" if a < 0.001 else "**" if a < 0.01 else "*" if a < 0.05 else ""
        for a in adjusted
    ]
    stats["p_raw"] = stats["p_raw"].apply(
        lambda v: None if pd.isna(v) else round(float(v), 4)
    )

    n_family = int(family_mask.sum())
    n_excluded = len(stats) - n_family
    print(f"  Test family: {n_family} test(s) corrected (BH FDR, "
          f"alpha={ABLATION_FDR_ALPHA}); {n_excluded} comparison(s) recorded "
          f"but not tested; {n_scipy_failures} scipy failure(s)")
    print(f"  Rejected after correction: {int(stats['rejected_fdr'].sum())} "
          f"of {n_family}")

    return stats


def build_descriptive_deltas(df: pd.DataFrame) -> pd.DataFrame:
    """
    Paired per-patient deltas for the metrics deliberately kept OUT of the test
    family: cost, latency, candidates evaluated.

    These move because the configuration changed what the pipeline does, not
    because of anything sampling could have produced. They get means, medians
    and a paired IQR -- everything except a p-value.
    """
    baseline_df = df[df["config_name"] == BASELINE].set_index("patient_id")
    rows = []

    for config in CONFIG_ORDER:
        if config == BASELINE:
            continue
        config_df = df[df["config_name"] == config].set_index("patient_id")
        shared = baseline_df.index.intersection(config_df.index)
        if len(shared) == 0:
            continue

        for col in ABLATION_DESCRIPTIVE_METRICS:
            bl_vals = baseline_df.loc[shared, col].values.astype(float)
            cf_vals = config_df.loc[shared, col].values.astype(float)
            diff = cf_vals - bl_vals
            rows.append({
                "config_name": config,
                "config_label": CONFIG_LABELS[config],
                "metric": col,
                "n_paired": len(shared),
                "baseline_mean": round(float(np.mean(bl_vals)), 6),
                "config_mean": round(float(np.mean(cf_vals)), 6),
                "mean_delta": round(float(np.mean(diff)), 6),
                "median_delta": round(float(np.median(diff)), 6),
                "delta_q25": round(float(np.percentile(diff, 25)), 6),
                "delta_q75": round(float(np.percentile(diff, 75)), 6),
                "pct_change": (round(float(np.mean(diff) / np.mean(bl_vals) * 100), 1)
                               if np.mean(bl_vals) != 0 else None),
                "tested": False,
            })

    return pd.DataFrame(rows)


def compute_minimum_detectable_effect(df: pd.DataFrame,
                                      stats: pd.DataFrame) -> dict:
    """
    Minimum detectable effect for the corrected family, reported once.

    Without this, a non-significant result is ambiguous between "the ablation
    had no effect" and "this design could not have seen an effect of that
    size". The calculation solves the exact paired t-test power equation for
    the standardized effect dz, using the noncentral t distribution:

        power = P(|T| > t_crit | ncp = dz*sqrt(n)),  df = n - 1

    then divides by sqrt(ARE) to convert it to a Wilcoxon-appropriate figure.

    The closed-form normal approximation dz = (z_{1-a/2} + z_power)/sqrt(n) is
    used only if the root-find fails, and is ANTI-conservative: it understates
    the MDE by ~0.004 at n=75 but ~0.056 at n=15. Understating is the wrong
    direction for a number whose job is to defend a null, so the exact solve is
    the primary path and which path ran is recorded in `method`.

    Reported at two alpha values that bracket what BH actually enforces:
        - alpha = ABLATION_FDR_ALPHA          the most significant test in the
                                              family, BH's loosest threshold
        - alpha = ABLATION_FDR_ALPHA / m      the least significant test, BH's
                                              strictest threshold (the
                                              Bonferroni bound)

    A real study's power sits between these; the pessimistic figure is the one
    to quote when defending a null. dz is standardized, so it is also converted
    into each metric's own units using the observed SD of that metric's paired
    differences.
    """
    from scipy.stats import norm, nct, t as t_dist
    from scipy.optimize import brentq

    tested = stats[stats["status"] == "tested"] if len(stats) else stats
    m = len(tested)
    if m == 0:
        return {"error": "no tests in family; MDE undefined"}

    # Conservative: the smallest paired n any tested configuration had.
    n = int(tested["n_paired"].min())
    n_max = int(tested["n_paired"].max())

    z_power = norm.ppf(ABLATION_POWER_TARGET)
    scale = np.sqrt(WILCOXON_ARE_VS_T)
    methods_used = set()

    def _dz(alpha):
        """Exact noncentral-t solve, with a logged normal-approximation fallback."""
        approx = float((norm.ppf(1 - alpha / 2) + z_power) / np.sqrt(n))
        df = n - 1
        if df < 1:
            methods_used.add("normal_approx (df<1)")
            return approx / scale
        t_crit = t_dist.ppf(1 - alpha / 2, df)

        def shortfall(dz):
            ncp = dz * np.sqrt(n)
            power = (1 - nct.cdf(t_crit, df, ncp)) + nct.cdf(-t_crit, df, ncp)
            if not np.isfinite(power):
                # scipy's noncentral t loses numerical support at large ncp
                # (NaN from ~ncp>34 at df=74). That region is far above the
                # root -- power has already saturated at 1.0 well before it --
                # so clamping to 1.0 keeps the bracket valid and monotone
                # instead of aborting the solve on a NaN.
                power = 1.0
            return power - ABLATION_POWER_TARGET

        try:
            exact = brentq(shortfall, 1e-6, 20.0, xtol=1e-8)
            methods_used.add("exact_noncentral_t")
            return float(exact) / scale
        except Exception as exc:
            methods_used.add("normal_approx")
            print(f"  WARNING: exact MDE solve failed at alpha={alpha:.6g} "
                  f"({type(exc).__name__}: {exc}); fell back to normal "
                  f"approximation, which UNDERSTATES the MDE")
            return approx / scale

    alpha_loose = ABLATION_FDR_ALPHA
    alpha_strict = ABLATION_FDR_ALPHA / m

    dz_loose = _dz(alpha_loose)
    dz_strict = _dz(alpha_strict)

    out = {
        "n_paired_min": n,
        "n_paired_max": n_max,
        "n_tests_in_family": m,
        "power_target": ABLATION_POWER_TARGET,
        "are_wilcoxon_vs_t": WILCOXON_ARE_VS_T,
        "alpha_loose": alpha_loose,
        "alpha_strict": round(alpha_strict, 6),
        "dz_loose": round(dz_loose, 3),
        "dz_strict": round(dz_strict, 3),
        "method": "+".join(sorted(methods_used)),
        "per_metric": {},
    }

    # Translate the standardized effect into each metric's units, using the SD
    # of the paired differences pooled across the tested configurations.
    baseline_df = df[df["config_name"] == BASELINE].set_index("patient_id")
    for col in ABLATION_OUTCOME_METRICS:
        diffs = []
        for config in tested[tested["metric"] == col]["config_name"].unique():
            config_df = df[df["config_name"] == config].set_index("patient_id")
            shared = baseline_df.index.intersection(config_df.index)
            diffs.append(config_df.loc[shared, col].values.astype(float)
                         - baseline_df.loc[shared, col].values.astype(float))
        if not diffs:
            continue
        sd = float(np.std(np.concatenate(diffs), ddof=1))
        out["per_metric"][col] = {
            "sd_paired_diff": round(sd, 4),
            "mde_units_loose": round(out["dz_loose"] * sd, 4),
            "mde_units_strict": round(out["dz_strict"] * sd, 4),
        }

    print(f"  MDE at n={n}, {m} tests, power={ABLATION_POWER_TARGET}: "
          f"dz={out['dz_loose']} (alpha={alpha_loose}) to "
          f"dz={out['dz_strict']} (alpha={out['alpha_strict']}) "
          f"[{out['method']}]")

    return out


# ===========================================================================
# 3. VISUALIZATIONS
# ===========================================================================

def plot_funnel_chart(df: pd.DataFrame) -> None:
    """Per-stage candidate funnel by config (grouped bar chart)."""
    stages = [
        ("candidates_retrieved",           "Retrieved"),
        ("candidates_reranked",            "Reranked"),
        ("candidates_after_rule_filter",   "After Rules"),
        ("candidates_after_quality_filter","After Quality"),
        ("candidates_evaluated",           "Evaluated"),
        ("eligible_count",                 "Eligible"),
    ]

    means = df.groupby("config_name", observed=True)[[s[0] for s in stages]].mean()
    means = means.loc[CONFIG_ORDER]
    means.columns = [s[1] for s in stages]
    means.index = [CONFIG_LABELS[c] for c in means.index]

    fig, ax = plt.subplots(figsize=(14, 7))
    means.plot(kind="bar", ax=ax, width=0.8)
    ax.set_ylabel("Average Candidate Count")
    ax.set_title("Pipeline Funnel by Ablation Configuration")
    ax.legend(title="Stage", bbox_to_anchor=(1.02, 1), loc="upper left")
    ax.set_xticklabels(ax.get_xticklabels(), rotation=30, ha="right")
    plt.tight_layout()
    path = OUTPUT_DIR / "ablation_funnel_chart.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {path}")


def plot_delta_chart(table: pd.DataFrame) -> None:
    """Delta from baseline bar chart for key metrics."""
    fig, axes = plt.subplots(1, 3, figsize=(16, 5))

    non_baseline = table[table["config_name"] != BASELINE].copy()
    labels = non_baseline["config_label"].values

    for ax, col, title, fmt in [
        (axes[0], "Δ_eligible_mean",  "Δ Eligible Count",    "{:.2f}"),
        (axes[1], "Δ_cost_mean",      "Δ Cost (USD)",        "{:.4f}"),
        (axes[2], "Δ_time_mean",      "Δ Latency (sec)",     "{:.1f}"),
    ]:
        vals = non_baseline[col].values
        colors = ["#e74c3c" if v < 0 else "#2ecc71" for v in vals]
        # For cost and time, positive delta = worse (red), negative = better (green)
        if "cost" in col or "time" in col:
            colors = ["#2ecc71" if v < 0 else "#e74c3c" for v in vals]

        ax.barh(range(len(labels)), vals, color=colors)
        ax.set_yticks(range(len(labels)))
        ax.set_yticklabels(labels)
        ax.axvline(0, color="black", linewidth=0.8)
        ax.set_title(title)

        for i, v in enumerate(vals):
            ax.text(v, i, f" {fmt.format(v)}", va="center",
                    ha="left" if v >= 0 else "right", fontsize=8)

    plt.suptitle("Ablation Impact vs Full Pipeline Baseline", fontsize=13, y=1.02)
    plt.tight_layout()
    path = OUTPUT_DIR / "ablation_delta_chart.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {path}")


def plot_cost_efficiency(df: pd.DataFrame) -> None:
    """
    Pooled cost-per-eligible-match by config (the key rule filter argument).

    Pooled (total spend / total matches) over ALL sampled patients, not a mean
    of the per-patient ratio over the subset that matched. The per-patient
    ratio is undefined at zero matches, so averaging it discards precisely the
    patients whose spend returned nothing and flatters whichever configuration
    failed most often. There is no error bar because a pooled ratio is a single
    quantity, not a distribution over patients.
    """
    grouped = df.groupby("config_name", observed=True).agg(
        total_cost=("estimated_cost_usd", "sum"),
        total_eligible=("eligible_count", "sum"),
        n_patients=("patient_id", "count"),
    ).reindex(CONFIG_ORDER)

    pooled = grouped["total_cost"] / grouped["total_eligible"].replace(0, np.nan)
    labels = [CONFIG_LABELS[c] for c in grouped.index]

    fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.bar(range(len(pooled)), pooled.values,
                  color=["#3498db"] + ["#95a5a6"] * (len(pooled) - 1))
    bars[0].set_color("#2ecc71")  # Highlight baseline

    ax.set_xticks(range(len(pooled)))
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_ylabel("Cost per Eligible Match (USD, pooled)")
    ax.set_title("Cost Efficiency: Pooled Cost per Eligible Match by Configuration")

    for i, (m, n) in enumerate(zip(pooled.values, grouped["n_patients"].values)):
        if not np.isnan(m):
            ax.text(i, m, f"${m:.4f}\n(n={int(n)})", ha="center",
                    va="bottom", fontsize=8)

    ax.set_xlabel("Pooled over all sampled patients; zero-match patients keep "
                  "their cost in the numerator", fontsize=8)

    plt.tight_layout()
    path = OUTPUT_DIR / "ablation_cost_efficiency.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {path}")


def plot_score_distribution(df: pd.DataFrame) -> None:
    """
    Box plot of match score distributions per config, over ALL sampled patients.

    Every box covers the same patients. Dropping the zero-match patients (the
    old behaviour) made each box cover a different, configuration-selected
    subpopulation, so the boxes were not comparable to each other. The match
    rate is annotated under each box: a high box over a low match rate is a
    configuration that scores well on the few patients it did not lose.
    """
    fig, ax = plt.subplots(figsize=(12, 6))

    data = [df[df["config_name"] == c]["avg_match_score_all"].dropna().values
            for c in CONFIG_ORDER]
    rates = [df[df["config_name"] == c]["has_match"].mean() for c in CONFIG_ORDER]
    counts = [len(d) for d in data]
    labels = [
        f"{CONFIG_LABELS[c]}\nn={n}, matched {r:.0%}"
        for c, n, r in zip(CONFIG_ORDER, counts, rates)
    ]

    bp = ax.boxplot(data, labels=labels, patch_artist=True, showmeans=True,
                    meanprops={"marker": "D", "markerfacecolor": "red", "markersize": 5})

    colors = ["#2ecc71"] + ["#bdc3c7"] * (len(CONFIG_ORDER) - 1)
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)

    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("Average Match Score (all patients, no match = 0.0)")
    ax.set_title("Match Quality Distribution by Configuration")
    plt.tight_layout()
    path = OUTPUT_DIR / "ablation_score_distribution.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {path}")


def plot_cancer_group_heatmap(df: pd.DataFrame) -> None:
    """Heatmap: avg eligible count by config × cancer group."""
    pivot = df.pivot_table(
        values="eligible_count",
        index="cancer_group",
        columns="config_name",
        aggfunc="mean",
    )
    # Reorder columns
    pivot = pivot[[c for c in CONFIG_ORDER if c in pivot.columns]]
    pivot.columns = [CONFIG_LABELS.get(c, c) for c in pivot.columns]

    if pivot.empty:
        print("  Skipped: cancer group heatmap (no data)")
        return

    fig, ax = plt.subplots(figsize=(14, max(6, len(pivot) * 0.5 + 2)))
    im = ax.imshow(pivot.values, cmap="YlOrRd", aspect="auto")

    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels(pivot.columns, rotation=35, ha="right")
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels(pivot.index)

    # Annotate cells
    for i in range(len(pivot.index)):
        for j in range(len(pivot.columns)):
            val = pivot.values[i, j]
            if not np.isnan(val):
                ax.text(j, i, f"{val:.1f}", ha="center", va="center", fontsize=8)

    plt.colorbar(im, ax=ax, label="Avg Eligible Count")
    ax.set_title("Eligible Matches by Cancer Group × Configuration")
    plt.tight_layout()
    path = OUTPUT_DIR / "ablation_cancer_group_heatmap.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {path}")


def plot_timing_breakdown(df: pd.DataFrame) -> None:
    """Stacked bar chart of per-stage latency by config."""
    timing_cols = [
        ("query_expansion_time",  "Query Expansion"),
        ("hybrid_retrieval_time", "Hybrid Retrieval"),
        ("cross_encoder_time",    "Cross-Encoder"),
        ("rule_filter_time",      "Rule Filter"),
        ("gpt4o_evaluation_time", "GPT-4o Evaluation"),
    ]

    means = df.groupby("config_name", observed=True)[[t[0] for t in timing_cols]].mean()
    means = means.loc[CONFIG_ORDER]
    means.columns = [t[1] for t in timing_cols]
    means.index = [CONFIG_LABELS[c] for c in means.index]

    fig, ax = plt.subplots(figsize=(12, 6))
    means.plot(kind="barh", stacked=True, ax=ax,
               color=["#3498db", "#2ecc71", "#e74c3c", "#f39c12", "#9b59b6"])
    ax.set_xlabel("Average Latency (seconds)")
    ax.set_title("Pipeline Latency Breakdown by Configuration")
    ax.legend(title="Stage", bbox_to_anchor=(1.02, 1), loc="upper left")
    plt.tight_layout()
    path = OUTPUT_DIR / "ablation_timing_breakdown.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {path}")


# ===========================================================================
# 3b. WIN/TIE/LOSS PAIRWISE ANALYSIS
# ===========================================================================

def build_win_loss_table(df: pd.DataFrame) -> pd.DataFrame:
    """
    Per-patient pairwise comparison: each config vs baseline.

    For each patient, compare eligible_count:
        Win  = ablated config finds MORE eligible trials than baseline
        Tie  = same count
        Loss = ablated config finds FEWER eligible trials than baseline

    This is more informative than averages because it shows HOW MANY
    patients are affected, not just by how much on average.
    """
    baseline_df = df[df["config_name"] == BASELINE].set_index("patient_id")
    results = []

    for config in CONFIG_ORDER:
        if config == BASELINE:
            continue
        config_df = df[df["config_name"] == config].set_index("patient_id")
        shared = baseline_df.index.intersection(config_df.index)
        if len(shared) == 0:
            continue

        bl = baseline_df.loc[shared, "eligible_count"].values
        cf = config_df.loc[shared, "eligible_count"].values

        wins = int(np.sum(cf > bl))
        ties = int(np.sum(cf == bl))
        losses = int(np.sum(cf < bl))
        n = len(shared)

        results.append({
            "config_name": config,
            "config_label": CONFIG_LABELS[config],
            "n_patients": n,
            "wins": wins,
            "ties": ties,
            "losses": losses,
            "win_pct": round(wins / n * 100, 1),
            "tie_pct": round(ties / n * 100, 1),
            "loss_pct": round(losses / n * 100, 1),
        })

    return pd.DataFrame(results)


def plot_win_loss_chart(wl_table: pd.DataFrame) -> None:
    """Stacked horizontal bar: wins/ties/losses per config."""
    if wl_table.empty:
        print("  Skipped: win/loss chart (no data)")
        return

    labels = wl_table["config_label"].values
    wins = wl_table["win_pct"].values
    ties = wl_table["tie_pct"].values
    losses = wl_table["loss_pct"].values

    fig, ax = plt.subplots(figsize=(10, 5))
    y = range(len(labels))

    ax.barh(y, losses, color="#e74c3c", label="Loss (fewer eligible)")
    ax.barh(y, ties, left=losses, color="#bdc3c7", label="Tie (same)")
    ax.barh(y, wins, left=[l + t for l, t in zip(losses, ties)],
            color="#2ecc71", label="Win (more eligible)")

    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.set_xlabel("% of Patients")
    ax.set_title("Per-Patient Win/Tie/Loss vs Baseline (Eligible Count)")
    ax.legend(loc="lower right")
    ax.set_xlim(0, 100)

    plt.tight_layout()
    path = OUTPUT_DIR / "ablation_win_loss_chart.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {path}")


# ===========================================================================
# 3c. RETRIEVAL MODE UNIQUE CONTRIBUTION (BM25 vs Vector Venn)
# ===========================================================================

def plot_retrieval_venn(df: pd.DataFrame) -> None:
    """
    Compare bm25_only vs vector_only vs hybrid: unique trial contributions.

    Uses eligible_nct_ids column to compute exact trial-level overlap:
      - Trials found by BOTH retrieval modes
      - Trials found ONLY by BM25
      - Trials found ONLY by vector
      - Trials found ONLY by hybrid (synergy from fusion)

    Falls back to eligible_count proxy if eligible_nct_ids is unavailable.
    """
    has_nct_ids = ("eligible_nct_ids" in df.columns
                   and df["eligible_nct_ids"].notna().any()
                   and (df["eligible_nct_ids"] != "").any())

    baseline_df = df[df["config_name"] == "full_pipeline"].set_index("patient_id")
    bm25_df = df[df["config_name"] == "bm25_only"].set_index("patient_id")
    vector_df = df[df["config_name"] == "vector_only"].set_index("patient_id")

    shared = baseline_df.index.intersection(bm25_df.index).intersection(vector_df.index)
    if len(shared) < 5:
        print("  Skipped: retrieval venn (insufficient data)")
        return

    if has_nct_ids:
        # True trial-level overlap analysis using NCT IDs
        bm25_only_total = 0
        vector_only_total = 0
        both_total = 0
        hybrid_synergy_total = 0

        for pid in shared:
            bl_ids = set(baseline_df.loc[pid, "eligible_nct_ids"].split(",")) - {""}
            bm_ids = set(bm25_df.loc[pid, "eligible_nct_ids"].split(",")) - {""}
            vc_ids = set(vector_df.loc[pid, "eligible_nct_ids"].split(",")) - {""}

            both = bm_ids & vc_ids
            bm25_unique = bm_ids - vc_ids
            vec_unique = vc_ids - bm_ids
            hybrid_unique = bl_ids - bm_ids - vc_ids

            bm25_only_total += len(bm25_unique)
            vector_only_total += len(vec_unique)
            both_total += len(both)
            hybrid_synergy_total += len(hybrid_unique)

        fig, axes = plt.subplots(1, 2, figsize=(14, 5))

        # Left: Venn-style bar chart
        ax = axes[0]
        categories = ["BM25 Only", "Both", "Vector Only", "Hybrid Synergy"]
        values = [bm25_only_total, both_total, vector_only_total, hybrid_synergy_total]
        colors = ["#e74c3c", "#9b59b6", "#3498db", "#2ecc71"]
        ax.bar(categories, values, color=colors)
        ax.set_ylabel("Total Eligible Trials (across all patients)")
        ax.set_title("Trial-Level Retrieval Contributions")
        for i, v in enumerate(values):
            ax.text(i, v + 0.5, str(v), ha="center", fontsize=10)

        # Right: per-patient who contributes more
        bm25_better = 0
        vector_better = 0
        equal = 0
        for pid in shared:
            bm_ids = set(bm25_df.loc[pid, "eligible_nct_ids"].split(",")) - {""}
            vc_ids = set(vector_df.loc[pid, "eligible_nct_ids"].split(",")) - {""}
            if len(bm_ids) > len(vc_ids):
                bm25_better += 1
            elif len(vc_ids) > len(bm_ids):
                vector_better += 1
            else:
                equal += 1

        ax = axes[1]
        ax.bar(["BM25 Better", "Equal", "Vector Better"],
               [bm25_better, equal, vector_better],
               color=["#e74c3c", "#bdc3c7", "#3498db"])
        ax.set_ylabel("Patient Count")
        ax.set_title("Per-Patient: Which Retrieval Mode Finds More?")
        for i, v in enumerate([bm25_better, equal, vector_better]):
            ax.text(i, v + 0.3, str(v), ha="center", fontsize=10)

    else:
        # Fallback: proxy analysis using eligible_count recovery rates
        bl = baseline_df.loc[shared, "eligible_count"].values
        bm = bm25_df.loc[shared, "eligible_count"].values
        vc = vector_df.loc[shared, "eligible_count"].values

        bm25_recovery = np.where(bl > 0, bm / bl, 0)
        vector_recovery = np.where(bl > 0, vc / bl, 0)

        fig, axes = plt.subplots(1, 2, figsize=(14, 5))

        ax = axes[0]
        ax.hist(bm25_recovery, bins=20, alpha=0.6, label="BM25 Only", color="#e74c3c")
        ax.hist(vector_recovery, bins=20, alpha=0.6, label="Vector Only", color="#3498db")
        ax.axvline(1.0, color="black", linestyle="--", linewidth=0.8, label="100% recovery")
        ax.set_xlabel("Recovery Rate (eligible found / baseline eligible)")
        ax.set_ylabel("Patient Count")
        ax.set_title("Eligible Match Recovery by Retrieval Mode")
        ax.legend()

        ax = axes[1]
        ax.scatter(bm25_recovery, vector_recovery, alpha=0.5, s=30, c="#9b59b6")
        max_r = max(bm25_recovery.max(), vector_recovery.max(), 1.5)
        ax.plot([0, max_r], [0, max_r], "k--", linewidth=0.8, label="Equal recovery")
        ax.set_xlabel("BM25 Recovery Rate")
        ax.set_ylabel("Vector Recovery Rate")
        ax.set_title("BM25 vs Vector: Per-Patient Recovery")
        ax.legend()

        bm25_better = int(np.sum(bm25_recovery > vector_recovery))
        vector_better = int(np.sum(vector_recovery > bm25_recovery))
        equal = int(np.sum(bm25_recovery == vector_recovery))
        ax.text(0.02, 0.98, f"BM25 better: {bm25_better}\nVector better: {vector_better}\nEqual: {equal}",
                transform=ax.transAxes, va="top", fontsize=9,
                bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5))

    plt.suptitle("Hybrid Retrieval Justification: BM25 vs Vector Unique Contributions", y=1.02)
    plt.tight_layout()
    path = OUTPUT_DIR / "ablation_retrieval_venn.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {path}")


# ===========================================================================
# 3d. PER-PATIENT SCATTER: BASELINE vs ABLATED
# ===========================================================================

def plot_patient_scatter(df: pd.DataFrame) -> None:
    """
    Scatter plot: baseline eligible count vs ablated eligible count per patient.
    One subplot per config. Points below the diagonal = degradation.
    Shows WHERE each config fails, not just averages.
    """
    baseline_df = df[df["config_name"] == BASELINE].set_index("patient_id")
    non_baseline = [c for c in CONFIG_ORDER if c != BASELINE]

    n_configs = len(non_baseline)
    cols = 3
    rows = (n_configs + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(5 * cols, 4.5 * rows), squeeze=False)

    for idx, config in enumerate(non_baseline):
        ax = axes[idx // cols][idx % cols]
        config_df = df[df["config_name"] == config].set_index("patient_id")
        shared = baseline_df.index.intersection(config_df.index)

        bl = baseline_df.loc[shared, "eligible_count"].values
        cf = config_df.loc[shared, "eligible_count"].values

        if len(bl) == 0:
            ax.text(0.5, 0.5, "No shared patients", transform=ax.transAxes,
                    ha="center", va="center", fontsize=9)
            ax.set_title(CONFIG_LABELS[config], fontsize=10)
            continue

        ax.scatter(bl, cf, alpha=0.5, s=25, c="#3498db")
        max_val = max(bl.max(), cf.max(), 1) + 1
        
        ax.plot([0, max_val], [0, max_val], "k--", linewidth=0.8)
        ax.set_xlabel("Baseline Eligible")
        ax.set_ylabel(f"{CONFIG_LABELS[config]} Eligible")
        ax.set_title(CONFIG_LABELS[config], fontsize=10)
        ax.set_xlim(-0.5, max_val)
        ax.set_ylim(-0.5, max_val)

        # Count degraded patients
        degraded = np.sum(cf < bl)
        improved = np.sum(cf > bl)
        ax.text(0.02, 0.98, f"Degraded: {degraded}\nImproved: {improved}",
                transform=ax.transAxes, va="top", fontsize=8,
                bbox=dict(boxstyle="round", facecolor="lightyellow", alpha=0.7))

    # Hide unused subplots
    for idx in range(n_configs, rows * cols):
        axes[idx // cols][idx % cols].set_visible(False)

    plt.suptitle("Per-Patient Eligible Count: Baseline vs Each Ablation", fontsize=13, y=1.01)
    plt.tight_layout()
    path = OUTPUT_DIR / "ablation_patient_scatter.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {path}")


# ===========================================================================
# 4. PLAIN-TEXT REPORT
# ===========================================================================

def generate_report(df: pd.DataFrame, table: pd.DataFrame,
                    stats: pd.DataFrame, wl_table: pd.DataFrame,
                    errors: pd.DataFrame, pairing: pd.DataFrame,
                    descriptive: pd.DataFrame, mde: dict) -> None:
    """Generate a plain-text report for quick review."""
    lines = []
    lines.append("=" * 70)
    lines.append(Project_Name + ": ABLATION STUDY REPORT")
    lines.append("=" * 70)
    lines.append("")

    # Overview
    lines.append(f"Patients:       {df['patient_id'].nunique()}")
    lines.append(f"Configurations: {df['config_name'].nunique()}")
    lines.append(f"Total runs:     {len(df)}")
    if len(errors):
        lines.append(f"Errors:         {len(errors)} (excluded from analysis)")
    lines.append("")

    # ── METHODS ────────────────────────────────────────────────────────────
    #
    # Stated in the report itself, not only in the code, because every number
    # below is conditional on it.
    lines.append("-" * 70)
    lines.append("STATISTICAL METHODS")
    lines.append("-" * 70)
    n_family = int((stats["status"] == "tested").sum()) if len(stats) else 0
    n_not_tested = len(stats) - n_family if len(stats) else 0
    lines.append(
        "Test:        Two-sided Wilcoxon signed-rank on per-patient paired\n"
        "             differences (ablated - baseline), zeros dropped."
    )
    lines.append(
        f"Family:      {n_family} test(s) = (non-baseline configs) x\n"
        f"             {ABLATION_OUTCOME_METRICS}."
    )
    lines.append(
        "Correction:  Benjamini-Hochberg FDR at alpha="
        f"{ABLATION_FDR_ALPHA} across the whole\n"
        "             family. Both raw and adjusted p are reported; the\n"
        "             significance stars come from the ADJUSTED p only."
    )
    lines.append(
        "Excluded:    Cost, latency and candidate counts are near-deterministic\n"
        "             consequences of the configuration, not hypotheses. They\n"
        "             are reported descriptively and carry no p-value, which\n"
        "             keeps the family at 18 tests rather than 30."
    )
    if n_not_tested:
        lines.append(
            f"Not tested:  {n_not_tested} comparison(s) recorded with a status and\n"
            "             no p-value (identical values, scipy failure, or too\n"
            "             few pairs). They are excluded from the correction\n"
            "             rather than entered as p=1.0."
        )
    lines.append(
        "Effect size: Signed matched-pairs rank-biserial correlation,\n"
        "             r = (R+ - R-)/(R+ + R-), range [-1,+1]. r > 0 means the\n"
        "             ABLATED config scored higher than baseline."
    )

    # Minimum detectable effect
    if "error" in mde:
        lines.append(f"Power:       not computable ({mde['error']})")
    else:
        lines.append(
            f"Power:       At n={mde['n_paired_min']} paired patients, "
            f"{mde['n_tests_in_family']} tests and\n"
            f"             power={mde['power_target']:.0%}, the smallest effect this\n"
            f"             design resolves is dz={mde['dz_loose']} at the loosest BH\n"
            f"             threshold (alpha={mde['alpha_loose']}) and dz="
            f"{mde['dz_strict']} at the\n"
            f"             strictest (alpha={mde['alpha_strict']:.5f}, the Bonferroni\n"
            "             bound). Wilcoxon ARE vs paired t = "
            f"{mde['are_wilcoxon_vs_t']}, solved by\n"
            f"             {mde['method']}.\n"
            "             A null below these thresholds is an absence of power,\n"
            "             not evidence of an absence of effect."
        )
        if mde.get("per_metric"):
            lines.append("             In each metric's own units:")
            for col, d in mde["per_metric"].items():
                lines.append(
                    f"               {col:<22} SD(paired diff)="
                    f"{d['sd_paired_diff']:.4f} -> MDE "
                    f"{d['mde_units_loose']:.4f} to {d['mde_units_strict']:.4f}"
                )
    lines.append("")

    # Main comparison table with 95% CIs
    lines.append("-" * 70)
    lines.append("COMPARISON TABLE (mean [95% CI], all sampled patients)")
    lines.append("-" * 70)
    lines.append("Score = mean match score over ALL sampled patients; a patient")
    lines.append("        with no eligible trial contributes 0.0.")
    lines.append("Match% = proportion of sampled patients with >= 1 eligible trial.")
    lines.append("")

    header = (f"{'Config':<28} {'Eligible':>18} {'Score':>18} {'Match%':>16} "
              f"{'Cost (USD)':>18}")
    lines.append(header)
    lines.append("-" * len(header))

    for _, row in table.iterrows():
        name = CONFIG_LABELS.get(row["config_name"], row["config_name"])
        # Eligible with CI
        e_lo = row.get("eligible_count_ci_lo", np.nan)
        e_hi = row.get("eligible_count_ci_hi", np.nan)
        e_lo = np.nan if e_lo is None else float(e_lo)
        if not np.isnan(e_lo):
            elig = f"{row['eligible_mean']:.2f} [{e_lo:.2f},{e_hi:.2f}]"
        else:
            elig = f"{row['eligible_mean']:.2f}"
        # Score with CI (unconditional: avg_match_score_all)
        s_lo = row.get("avg_match_score_all_ci_lo", np.nan)
        s_hi = row.get("avg_match_score_all_ci_hi", np.nan)
        s_lo = np.nan if s_lo is None else float(s_lo)
        if not np.isnan(s_lo):
            score = f"{row['score_mean']:.3f} [{s_lo:.3f},{s_hi:.3f}]"
        else:
            score = f"{row['score_mean']:.3f}" if not np.isnan(row['score_mean']) else "N/A"
        # Match rate with CI
        m_lo = row.get("has_match_ci_lo", np.nan)
        m_hi = row.get("has_match_ci_hi", np.nan)
        m_lo = np.nan if m_lo is None else float(m_lo)
        if not np.isnan(m_lo):
            mrate = f"{row['match_rate']:.2f} [{m_lo:.2f},{m_hi:.2f}]"
        else:
            mrate = f"{row['match_rate']:.2f}"
        # Cost with CI
        c_lo = row.get("estimated_cost_usd_ci_lo", np.nan)
        c_hi = row.get("estimated_cost_usd_ci_hi", np.nan)
        c_lo = np.nan if c_lo is None else float(c_lo)
        if not np.isnan(c_lo):
            cost = f"${row['cost_mean']:.4f} [{c_lo:.4f},{c_hi:.4f}]"
        else:
            cost = f"${row['cost_mean']:.4f}"
        lines.append(f"{name:<28} {elig:>18} {score:>18} {mrate:>16} {cost:>18}")

    lines.append("")

    # ── n behind each bootstrap interval ───────────────────────────────────
    #
    # An interval is resampled from that column's non-null values, which is not
    # always the population its mean was computed over. Printing the n makes a
    # narrowed or shifted resampling population visible instead of implied.
    ci_report_cols = ["eligible_count", "avg_match_score_all", "has_match",
                      "estimated_cost_usd", "total_time"]
    lines.append("  Bootstrap CI sample sizes (n resampled / n patients):")
    ci_hdr = "  " + f"{'Config':<26}" + "".join(f"{c[:18]:>20}" for c in ci_report_cols)
    lines.append(ci_hdr)
    for _, row in table.iterrows():
        name = CONFIG_LABELS.get(row["config_name"], row["config_name"])
        cells = []
        for c in ci_report_cols:
            n_ci = row.get(f"{c}_ci_n", np.nan)
            n_ci = "?" if pd.isna(n_ci) else str(int(n_ci))
            cells.append(f"{n_ci}/{int(row['n_patients'])}")
        lines.append("  " + f"{name:<26}" + "".join(f"{c:>20}" for c in cells))
    lines.append("  (1000 bootstrap resamples, percentile method, seed 42)")
    lines.append("")

    # Conditional score, always with its own n. Kept separate from the table
    # above so it can never be read as a like-for-like comparison: each row
    # averages over a different set of patients, chosen by the configuration.
    lines.append("-" * 70)
    lines.append("CONDITIONAL MATCH SCORE (matched patients only -- NOT comparable")
    lines.append("across configs; each row averages a different patient set)")
    lines.append("-" * 70)
    cond_header = f"{'Config':<28} {'Score|matched':>14} {'n_scored':>10} {'n_total':>9}"
    lines.append(cond_header)
    lines.append("-" * len(cond_header))
    for _, row in table.iterrows():
        name = CONFIG_LABELS.get(row["config_name"], row["config_name"])
        cond = row.get("score_cond_mean", np.nan)
        cond_s = "N/A" if pd.isna(cond) else f"{cond:.3f}"
        lines.append(
            f"{name:<28} {cond_s:>14} {int(row['n_scored']):>10} "
            f"{int(row['n_patients']):>9}"
        )
    lines.append("")

    # Deltas
    lines.append("-" * 70)
    lines.append("DELTAS vs BASELINE (full_pipeline)")
    lines.append("-" * 70)

    header2 = (f"{'Config':<28} {'Δ Elig':>8} {'Δ Score':>8} {'Δ Match%':>9} "
               f"{'Δ Cost':>8} {'Δ Time':>8} {'Δ% Elig':>8} {'Δ% Cost':>8}")
    lines.append(header2)
    lines.append("-" * len(header2))

    for _, row in table[table["config_name"] != BASELINE].iterrows():
        name = CONFIG_LABELS.get(row["config_name"], row["config_name"])
        d_elig = f"{row['Δ_eligible_mean']:+.2f}"
        d_score = f"{row['Δ_score_mean']:+.3f}"
        d_rate = f"{row['Δ_match_rate']:+.3f}"
        d_cost = f"{row['Δ_cost_mean']:+.4f}"
        d_time = f"{row['Δ_time_mean']:+.1f}s"
        dp_elig = f"{row.get('Δ%_eligible_mean', 0):+.1f}%"
        dp_cost = f"{row.get('Δ%_cost_mean', 0):+.1f}%"
        lines.append(f"{name:<28} {d_elig:>8} {d_score:>8} {d_rate:>9} "
                     f"{d_cost:>8} {d_time:>8} {dp_elig:>8} {dp_cost:>8}")

    lines.append("")

    # ── PATIENT PAIRING / DROPPED SET ──────────────────────────────────────
    lines.append("-" * 70)
    lines.append("PATIENT PAIRING (which patients each config was tested on)")
    lines.append("-" * 70)
    lines.append("n_paired counts the patients shared with the baseline. Dropped")
    lines.append("patients are named below: the retained SETS can differ across")
    lines.append("configs even when the counts match.")
    lines.append("")
    pr_header = (f"{'Config':<28} {'paired':>7} {'present':>8} {'dropped':>8} "
                 f"{'errored':>8} {'in family':>10}")
    lines.append(pr_header)
    lines.append("-" * len(pr_header))
    for _, row in pairing.iterrows():
        lines.append(
            f"{row['config_label']:<28} {row['n_paired']:>7} {row['n_present']:>8} "
            f"{row['n_dropped']:>8} {row['n_errored']:>8} "
            f"{('yes' if row['in_test_family'] else 'no'):>10}"
        )
    any_dropped = pairing[pairing["n_dropped"] > 0]
    if len(any_dropped):
        lines.append("")
        lines.append("  Dropped patients by config:")
        for _, row in any_dropped.iterrows():
            lines.append(f"    {row['config_label']}:")
            for entry in row["dropped_reasons"].split(";"):
                lines.append(f"      {entry}")
    else:
        lines.append("")
        lines.append("  No patients dropped: every config covers the full sample.")
    lines.append("")

    # Statistical significance with effect sizes
    if len(stats) > 0:
        lines.append("-" * 70)
        lines.append("STATISTICAL SIGNIFICANCE (Wilcoxon, BH-FDR corrected)")
        lines.append("-" * 70)
        lines.append(f"p_adj = Benjamini-Hochberg q over the {n_family}-test family.")
        lines.append("Stars reflect p_adj. r is signed: r>0 = ablated ABOVE baseline.")
        lines.append("")

        st_header = (f"  {'Metric':<24} {'p_raw':>8} {'p_adj':>8} {'sig':<4} "
                     f"{'r':>7} {'magnitude':<11} {'status'}")
        for config in CONFIG_ORDER:
            if config == BASELINE:
                continue
            sub = stats[stats["config_name"] == config]
            if sub.empty:
                continue
            n_paired = sub.iloc[0]["n_paired"]
            n_dropped = sub.iloc[0]["n_dropped"]
            lines.append(f"\n  {CONFIG_LABELS[config]} "
                         f"(n_paired={n_paired}, n_dropped={n_dropped}):")
            lines.append(st_header)
            for _, row in sub.iterrows():
                metric = row["metric"]
                if row["status"] != "tested":
                    lines.append(
                        f"  {metric:<24} {'--':>8} {'--':>8} {'':<4} {'--':>7} "
                        f"{'':<11} {row['status']}: {row['detail']}"
                    )
                    continue
                p_raw = row["p_raw"]
                p_adj = row["p_adj"]
                r = row["effect_r"]
                lines.append(
                    f"  {metric:<24} {p_raw:>8.4f} {p_adj:>8.4f} "
                    f"{row['sig']:<4} {r:>+7.3f} {row['effect_size']:<11} "
                    f"{'rejected' if row['rejected_fdr'] else 'not rejected'}"
                )

        # What survives correction, spelled out.
        tested = stats[stats["status"] == "tested"]
        n_raw_sig = int((tested["p_raw"] < ABLATION_FDR_ALPHA).sum())
        n_adj_sig = int(tested["rejected_fdr"].sum())
        lines.append("")
        lines.append(f"  Significant at raw p<{ABLATION_FDR_ALPHA}:      {n_raw_sig}"
                     f" of {n_family}")
        lines.append(f"  Surviving BH-FDR at q<{ABLATION_FDR_ALPHA}:     {n_adj_sig}"
                     f" of {n_family}")
        if n_raw_sig > n_adj_sig:
            lines.append(f"  ({n_raw_sig - n_adj_sig} result(s) did not survive "
                         f"correction and must not be reported as significant.)")
        lines.append("")

    # ── DESCRIPTIVE (deliberately untested) ────────────────────────────────
    if len(descriptive) > 0:
        lines.append("-" * 70)
        lines.append("COST / LATENCY / CANDIDATES (descriptive -- NOT tested)")
        lines.append("-" * 70)
        lines.append("Paired per-patient deltas vs baseline. No p-values: these move")
        lines.append("because the configuration changed what the pipeline does.")
        lines.append("")
        d_header = (f"  {'Config':<26} {'Metric':<22} {'mean Δ':>12} "
                    f"{'median Δ':>12} {'IQR of Δ':>22} {'Δ%':>8}")
        lines.append(d_header)
        lines.append("  " + "-" * (len(d_header) - 2))
        for _, row in descriptive.iterrows():
            pct = "N/A" if row["pct_change"] is None else f"{row['pct_change']:+.1f}%"
            iqr = f"[{row['delta_q25']:+.4g}, {row['delta_q75']:+.4g}]"
            lines.append(
                f"  {row['config_label']:<26} {row['metric']:<22} "
                f"{row['mean_delta']:>+12.4g} {row['median_delta']:>+12.4g} "
                f"{iqr:>22} {pct:>8}"
            )
        lines.append("")

    # Win/Tie/Loss table
    if len(wl_table) > 0:
        lines.append("-" * 70)
        lines.append("PER-PATIENT WIN/TIE/LOSS vs BASELINE (eligible count)")
        lines.append("-" * 70)

        wl_header = f"{'Config':<28} {'Win':>6} {'Tie':>6} {'Loss':>6} {'Win%':>7} {'Loss%':>7}"
        lines.append(wl_header)
        lines.append("-" * len(wl_header))
        for _, row in wl_table.iterrows():
            lines.append(
                f"{row['config_label']:<28} {row['wins']:>6} {row['ties']:>6} "
                f"{row['losses']:>6} {row['win_pct']:>6.1f}% {row['loss_pct']:>6.1f}%"
            )
        lines.append("")

    # ── Efficiency: pooled headline, conditional mean beside it with its n ──
    #
    # Pooled = total over all sampled patients / total matches. Conditional =
    # mean of the per-patient ratio over only the patients that matched, which
    # is undefined for the rest; it is printed with n_scored so it can never be
    # mistaken for a like-for-like comparison across configurations.
    table_idx = table.set_index("config_name")

    lines.append("-" * 70)
    lines.append("TOKEN EFFICIENCY (tokens per eligible match)")
    lines.append("-" * 70)
    eff_header = (f"  {'Config':<28} {'pooled (all n)':>16} "
                  f"{'cond. mean':>12} {'n_scored':>9}")
    lines.append(eff_header)
    for config in CONFIG_ORDER:
        if config not in table_idx.index:
            continue
        r = table_idx.loc[config]
        pooled = r["tokens_per_elig_pooled"]
        cond = r["tokens_per_elig_mean"]
        lines.append(
            f"  {CONFIG_LABELS[config]:<28} "
            f"{('N/A' if pd.isna(pooled) else f'{pooled:,.0f}'):>16} "
            f"{('N/A' if pd.isna(cond) else f'{cond:,.0f}'):>12} "
            f"{int(r['n_scored']):>9}"
        )

    lines.append("")

    lines.append("-" * 70)
    lines.append("COST EFFICIENCY (cost per eligible match, USD)")
    lines.append("-" * 70)
    lines.append(eff_header)
    for config in CONFIG_ORDER:
        if config not in table_idx.index:
            continue
        r = table_idx.loc[config]
        pooled = r["cost_per_elig_pooled"]
        cond = r["cost_per_elig_mean"]
        lines.append(
            f"  {CONFIG_LABELS[config]:<28} "
            f"{('N/A' if pd.isna(pooled) else f'${pooled:.4f}'):>16} "
            f"{('N/A' if pd.isna(cond) else f'${cond:.4f}'):>12} "
            f"{int(r['n_scored']):>9}"
        )

    lines.append("")
    lines.append("  pooled     = sum(cost) / sum(eligible) over ALL sampled patients;")
    lines.append("               a zero-match patient's spend stays in the numerator.")
    lines.append("  cond. mean = mean of the per-patient ratio over the n_scored")
    lines.append("               patients that matched. Population differs by config.")
    lines.append("")

    # Cancer group breakdown
    lines.append("-" * 70)
    lines.append("CANCER GROUP BREAKDOWN (avg eligible count)")
    lines.append("-" * 70)

    pivot = df.pivot_table(values="eligible_count", index="cancer_group",
                           columns="config_name", aggfunc="mean")
    if BASELINE in pivot.columns:
        pivot = pivot.sort_values(BASELINE, ascending=False)
    pivot_display = pivot[[c for c in CONFIG_ORDER if c in pivot.columns]]

    lines.append(f"{'Group':<20} " + " ".join(f"{CONFIG_LABELS[c][:12]:>12}" for c in CONFIG_ORDER if c in pivot_display.columns))
    for group in pivot_display.index:
        vals = " ".join(
            f"{pivot_display.loc[group, c]:>12.1f}" if not np.isnan(pivot_display.loc[group, c]) else f"{'N/A':>12}"
            for c in pivot_display.columns
        )
        lines.append(f"{str(group):<20} {vals}")

    lines.append("")
    lines.append("=" * 70)
    lines.append("  END OF REPORT")
    lines.append("=" * 70)

    report_text = "\n".join(lines)

    # Print to terminal
    print(report_text)

    # Save to file
    path = OUTPUT_DIR / "ablation_full_report.txt"
    with open(path, "w") as f:
        f.write(report_text)
    print(f"\n  Saved: {path}")


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    """Run full ablation analysis."""

    print()
    print("=" * 70)
    print(f"{Project_Name}: ABLATION ANALYSIS")
    print("=" * 70)
    print()

    # --- Load data ---
    print("[1/9] Loading data...")
    df = load_ablation_data()
    errors = load_error_data()

    if len(df) == 0:
        print("ERROR: No successful results found in ablation database.")
        sys.exit(1)

    # --- Build comparison table (with bootstrapped 95% CIs) ---
    print("[2/9] Building comparison table with 95% CIs...")
    table = build_comparison_table(df)
    csv_path = OUTPUT_DIR / "ablation_comparison_table.csv"
    table.to_csv(csv_path, index=False)
    print(f"  Saved: {csv_path}")

    # --- Pairing / dropped-patient accounting (feeds the tests) ---
    print("[3/9] Auditing patient pairing and dropped set...")
    pairing = build_pairing_report(df, errors)
    pairing_path = OUTPUT_DIR / "ablation_pairing_report.csv"
    pairing.to_csv(pairing_path, index=False)
    print(f"  Saved: {pairing_path}")

    # --- Statistical tests (BH-FDR corrected, signed effect sizes) ---
    print("[4/9] Running statistical tests (BH-FDR corrected)...")
    stats = run_statistical_tests(df, pairing)
    stats_path = OUTPUT_DIR / "ablation_statistical_tests.csv"
    stats.to_csv(stats_path, index=False)
    print(f"  Saved: {stats_path}")

    mde = compute_minimum_detectable_effect(df, stats)

    descriptive = build_descriptive_deltas(df)
    desc_path = OUTPUT_DIR / "ablation_descriptive_metrics.csv"
    descriptive.to_csv(desc_path, index=False)
    print(f"  Saved: {desc_path}")

    # --- Win/Tie/Loss pairwise analysis ---
    print("[5/9] Building win/tie/loss table...")
    wl_table = build_win_loss_table(df)
    wl_path = OUTPUT_DIR / "ablation_win_loss_table.csv"
    wl_table.to_csv(wl_path, index=False)
    print(f"  Saved: {wl_path}")

    # --- Visualizations (original + new) ---
    print("[6/9] Generating visualizations...")
    plot_funnel_chart(df)
    plot_delta_chart(table)
    plot_cost_efficiency(df)
    plot_score_distribution(df)
    plot_cancer_group_heatmap(df)
    plot_timing_breakdown(df)
    plot_win_loss_chart(wl_table)
    plot_retrieval_venn(df)
    plot_patient_scatter(df)

    # --- Full report ---
    print("[7/9] Generating report...")
    generate_report(df, table, stats, wl_table, errors, pairing, descriptive, mde)

    # --- Summary JSON (for programmatic use) ---
    print("[8/9] Exporting summary JSON...")
    _tested = stats[stats["status"] == "tested"] if len(stats) else stats
    summary = {
        "comparison_table": table.to_dict(orient="records"),
        "statistical_tests": stats.to_dict(orient="records"),
        "descriptive_metrics": descriptive.to_dict(orient="records"),
        "pairing_report": pairing.to_dict(orient="records"),
        "win_loss_table": wl_table.to_dict(orient="records"),
        "minimum_detectable_effect": mde,
        "test_family": {
            "correction": "benjamini_hochberg_fdr",
            "alpha": ABLATION_FDR_ALPHA,
            "outcome_metrics_tested": ABLATION_OUTCOME_METRICS,
            "descriptive_metrics_not_tested": ABLATION_DESCRIPTIVE_METRICS,
            "n_tests_in_family": int(len(_tested)),
            "n_comparisons_recorded_not_tested": int(len(stats) - len(_tested)),
            "n_significant_raw": (int((_tested["p_raw"] < ABLATION_FDR_ALPHA).sum())
                                  if len(_tested) else 0),
            "n_significant_adjusted": (int(_tested["rejected_fdr"].sum())
                                       if len(_tested) else 0),
        },
        "metadata": {
            "n_patients": int(df["patient_id"].nunique()),
            "n_configs": int(df["config_name"].nunique()),
            "n_runs": len(df),
            "n_errors": len(errors),
            "baseline": BASELINE,
        },
    }
    json_path = OUTPUT_DIR / "ablation_analysis.json"
    with open(json_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"  Saved: {json_path}")

    # --- Worst-degradation patients (flagged for manual review) ---
    print("[9/9] Flagging worst-degradation patients...")
    baseline_df = df[df["config_name"] == BASELINE].set_index("patient_id")
    worst = []
    for config in CONFIG_ORDER:
        if config == BASELINE:
            continue
        config_df = df[df["config_name"] == config].set_index("patient_id")
        shared = baseline_df.index.intersection(config_df.index)
        if len(shared) == 0:
            continue
        bl = baseline_df.loc[shared, "eligible_count"]
        cf = config_df.loc[shared, "eligible_count"]
        delta = cf - bl
        worst_patients = delta.nsmallest(3)
        for pid, d in worst_patients.items():
            if d < 0:
                worst.append({
                    "config": config, "patient_id": pid,
                    "baseline_eligible": int(bl.loc[pid]),
                    "ablated_eligible": int(cf.loc[pid]),
                    "delta": int(d),
                })
    if worst:
        worst_df = pd.DataFrame(worst).sort_values("delta")
        worst_path = OUTPUT_DIR / "ablation_worst_degradation.csv"
        worst_df.to_csv(worst_path, index=False)
        print(f"  Top degradation cases saved: {worst_path}")
        print("  (Review these patients manually for qualitative error analysis)")

    print()
    print("=" * 70)
    print("  ANALYSIS COMPLETE")
    print(f"  All outputs in: {OUTPUT_DIR}")
    print("=" * 70)
    print()


#------------------------------------------------------------------------------


if __name__ == "__main__":
    main()


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Mar  3 10:17:15 2026

@author: ramyalsaffar
"""

