"""Ablation analysis, figures and statistical tests.

Moved out of ``27- Ablation Analysis.py`` by item 20c, pass 3d.
``27- Ablation Analysis.py`` survives as a THIN ENTRY POINT -- a ``__main__``
block and the one import it needs. It keeps no re-export shim, because nothing
in the repository chained it or read a name out of it: all 33 of its top-level
names were grepped against every ``.py``, ``.md``, ``.toml`` and ``.yml`` in the
tree and the only hits outside the file itself are ``ABLATION_DB`` and
``ABLATION_CONFIGS``, both of which are File 26's own definitions of the same
names, plus the exec-bootstrap locals every numbered file shares
(``_code_dir``, ``_bootstrap``, ``_fh``, ``_os_boot``) and the generic ``main``.

WHAT PASS 20c-3d CHANGED, and nothing else did
----------------------------------------------
1. ``ABLATION_DB`` and ``OUTPUT_DIR`` were module-level ``Path(result_ablation_path)``
   expressions. ``result_ablation_path`` is LAZY (``oncotriage/paths.py``), and a
   ``from oncotriage.paths import result_ablation_path`` -- or a bare read of it
   at module scope -- is an ATTRIBUTE READ that fires the resolver, so importing
   this module would have globbed the whole sibling data tree and raised on any
   machine without it. They became accessors, resolved on first call and cached
   under a lock, exactly the shape ``oncotriage/fhir/clean.py`` uses. Pass 20f-4
   moved both to ``oncotriage/ablation/common.py``; they resolve and create
   nothing there either, and the argument for keeping resolution and creation
   separate is recorded with them.

2. The four ``ABLATION_*`` tuning constants and ``Project_Name`` are imported
   from ``oncotriage.config`` instead of being read out of the shared exec
   namespace. Same objects.

Everything else -- every query, every statistic, every line of the report -- is
the line slice of File 27 between its constants block and its ``__main__``
guard, unmodified.

MATPLOTLIB IS NO LONGER IMPORTED HERE (pass 20f-4). The nine functions that
drew moved to ``oncotriage/ablation/figures.py``, and the module-scope
matplotlib import went with them -- so the second of the package's two
deliberate exceptions now lives in a 496-line file rather than a 1,976-line one.
This module still imports ``figures`` at module scope, because ``main()`` calls
all nine and check 1b forbids a package import inside a function body; what
changed is WHERE the exception is written down, not that importing this module
avoids matplotlib. scipy stays inside the three function bodies that use it --
the third-party-in-a-function-body exemption -- so importing this module does
not pull in scipy either.

THE CONFIG VOCABULARY AND THE TWO PATH ACCESSORS MOVED TO
``oncotriage/ablation/common.py`` in the same pass, because ``figures`` needs
them and importing them back from here would be a cycle.

THIS MODULE NEVER WRITES TO ``ablation_results.db``. It reads it. File 26 is the
writer; see ``oncotriage/ablation/study.py``.

``--db`` (pass 20f-4). ``main(db_path)`` analyses a database other than the
production one, and every table, figure and report follows it into that
database's directory. Before this pass ``ablation_db()`` took no argument at
all, so a study written with File 26's ``--db`` -- which pass 20f-1 added
precisely so a run could be isolated -- COULD NOT BE ANALYSED.
"""

import json
import sqlite3
import sys

import numpy as np
import pandas as pd

from oncotriage.ablation import figures
from oncotriage.ablation.common import (
    BASELINE,
    CONFIG_LABELS,
    CONFIG_ORDER,
    ablation_db,
    output_dir,
)
from oncotriage.config import (
    ABLATION_DESCRIPTIVE_METRICS,
    ABLATION_FDR_ALPHA,
    ABLATION_MIN_PAIRED,
    ABLATION_OUTCOME_METRICS,
    ABLATION_POWER_TARGET,
    Project_Name,
)
from oncotriage.observability import console


#------------------------------------------------------------------------------


# ===========================================================================
# DATA LOADING
# ===========================================================================

def load_ablation_data(db_path=None) -> pd.DataFrame:
    """Load ablation_results table into a DataFrame."""
    if not ablation_db(db_path).exists():
        console.out(f"ERROR: {ablation_db(db_path)} not found. Run File 26 first.")
        sys.exit(1)

    conn = sqlite3.connect(str(ablation_db(db_path)))
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
        console.out(f"  WARNING: Dropped {before - len(df)} duplicate rows (kept most recent run)")

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
            console.out(f"  Backfilled avg_match_score_all for {int(_missing_all.sum())} "
                  f"pre-migration row(s) from avg_match_score (null -> 0.0)")
    else:
        df["avg_match_score_all"] = df["avg_match_score"].fillna(0.0)
        console.out("  avg_match_score_all absent from database (pre-migration): "
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
    df["total_tokens"] = df["llm_classifier_input_tokens"] + df["llm_classifier_output_tokens"]
    df["tokens_per_eligible"] = df.apply(
        lambda row: row["total_tokens"] / row["eligible_count"]
        if row["eligible_count"] > 0 else None, axis=1
    )

    console.out(f"Loaded {len(df)} results ({df['config_name'].nunique()} configs, "
          f"{df['patient_id'].nunique()} patients)")

    return df


def load_error_data(db_path=None) -> pd.DataFrame:
    """Load error rows separately for error rate analysis."""
    conn = sqlite3.connect(str(ablation_db(db_path)))
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
        input_tokens_mean   =("llm_classifier_input_tokens", "mean"),
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
                console.out(f"  WARNING: {config}/{col}: {len(vals)} non-null value(s), "
                      f"CI not computed")
                continue
            if len(vals) < len(config_data):
                console.out(f"  NOTE: {config}/{col}: CI bootstrapped on {len(vals)} of "
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
        console.out("  WARNING: Baseline config not found in results. Deltas skipped.")
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
            console.out(f"  EXCLUDED from test family: {config} has {len(shared)} "
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
                console.out(f"  WARNING: wilcoxon failed for {config}/{col}: "
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
        console.out("  WARNING: no comparisons produced; test family is empty")
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
    console.out(f"  Test family: {n_family} test(s) corrected (BH FDR, "
          f"alpha={ABLATION_FDR_ALPHA}); {n_excluded} comparison(s) recorded "
          f"but not tested; {n_scipy_failures} scipy failure(s)")
    console.out(f"  Rejected after correction: {int(stats['rejected_fdr'].sum())} "
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
            console.out(f"  WARNING: exact MDE solve failed at alpha={alpha:.6g} "
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

    console.out(f"  MDE at n={n}, {m} tests, power={ABLATION_POWER_TARGET}: "
          f"dz={out['dz_loose']} (alpha={alpha_loose}) to "
          f"dz={out['dz_strict']} (alpha={out['alpha_strict']}) "
          f"[{out['method']}]")

    return out


# ===========================================================================
# 3. VISUALIZATIONS -> oncotriage/ablation/figures.py  (pass 20f-4)
# ===========================================================================
#
# All nine plot functions moved there, unchanged apart from taking their output
# directory as an argument. They were the only definitions in this file that
# ever touched matplotlib -- measured by an AST walk over every Name load, not
# by reading -- which is why the module-scope import went with them. main()
# calls them through the `figures` import at the top.

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




# ===========================================================================
# 3c/3d. RETRIEVAL VENN and PER-PATIENT SCATTER -> figures.py (pass 20f-4)
# ===========================================================================


# ===========================================================================
# 4. PLAIN-TEXT REPORT
# ===========================================================================

def generate_report(df: pd.DataFrame, table: pd.DataFrame,
                    stats: pd.DataFrame, wl_table: pd.DataFrame,
                    errors: pd.DataFrame, pairing: pd.DataFrame,
                    descriptive: pd.DataFrame, mde: dict,
                    out_dir) -> None:
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
    console.out(report_text)

    # Save to file
    path = out_dir / "ablation_full_report.txt"
    with open(path, "w") as f:
        f.write(report_text)
    console.out(f"\n  Saved: {path}")


# ===========================================================================
# MAIN
# ===========================================================================

def main(db_path=None):
    """Run full ablation analysis.

    Args:
        db_path: ``None`` -- the default and what every documented command
            produces -- reads the production ``ablation_results.db`` and writes
            beside it. An explicit path reads THAT database and writes every
            table, figure and report into ITS directory, so an isolated study
            can be analysed without overwriting the production artifacts. The
            parent directory must exist; a missing one is refused by name
            (``common._require_writable_parent``, pass 20f-3), not by sqlite3.
    """

    console.out()
    console.out("=" * 70)
    console.out(f"{Project_Name}: ABLATION ANALYSIS")
    console.out("=" * 70)
    console.out()

    # ONE resolution, read by every writer below. The outputs FOLLOW the
    # database: with --db they land beside it, so a scratch analysis cannot
    # overwrite the production tables and figures with numbers computed from a
    # different database.
    out_dir = output_dir(db_path)
    if db_path is not None:
        console.out(f"  --db in effect: {ablation_db(db_path)}")
        console.out(f"  Outputs will go beside it: {out_dir}")
        console.out()

    # --- Load data ---
    console.out("[1/9] Loading data...")
    df = load_ablation_data(db_path)
    errors = load_error_data(db_path)

    if len(df) == 0:
        console.out("ERROR: No successful results found in ablation database.")
        sys.exit(1)

    # --- Build comparison table (with bootstrapped 95% CIs) ---
    console.out("[2/9] Building comparison table with 95% CIs...")
    table = build_comparison_table(df)
    csv_path = out_dir / "ablation_comparison_table.csv"
    table.to_csv(csv_path, index=False)
    console.out(f"  Saved: {csv_path}")

    # --- Pairing / dropped-patient accounting (feeds the tests) ---
    console.out("[3/9] Auditing patient pairing and dropped set...")
    pairing = build_pairing_report(df, errors)
    pairing_path = out_dir / "ablation_pairing_report.csv"
    pairing.to_csv(pairing_path, index=False)
    console.out(f"  Saved: {pairing_path}")

    # --- Statistical tests (BH-FDR corrected, signed effect sizes) ---
    console.out("[4/9] Running statistical tests (BH-FDR corrected)...")
    stats = run_statistical_tests(df, pairing)
    stats_path = out_dir / "ablation_statistical_tests.csv"
    stats.to_csv(stats_path, index=False)
    console.out(f"  Saved: {stats_path}")

    mde = compute_minimum_detectable_effect(df, stats)

    descriptive = build_descriptive_deltas(df)
    desc_path = out_dir / "ablation_descriptive_metrics.csv"
    descriptive.to_csv(desc_path, index=False)
    console.out(f"  Saved: {desc_path}")

    # --- Win/Tie/Loss pairwise analysis ---
    console.out("[5/9] Building win/tie/loss table...")
    wl_table = build_win_loss_table(df)
    wl_path = out_dir / "ablation_win_loss_table.csv"
    wl_table.to_csv(wl_path, index=False)
    console.out(f"  Saved: {wl_path}")

    # --- Visualizations (original + new) ---
    console.out("[6/9] Generating visualizations...")
    figures.plot_funnel_chart(df, out_dir)
    figures.plot_delta_chart(table, out_dir)
    figures.plot_cost_efficiency(df, out_dir)
    figures.plot_score_distribution(df, out_dir)
    figures.plot_cancer_group_heatmap(df, out_dir)
    figures.plot_timing_breakdown(df, out_dir)
    figures.plot_win_loss_chart(wl_table, out_dir)
    figures.plot_retrieval_venn(df, out_dir)
    figures.plot_patient_scatter(df, out_dir)

    # --- Full report ---
    console.out("[7/9] Generating report...")
    generate_report(df, table, stats, wl_table, errors, pairing, descriptive,
                    mde, out_dir)

    # --- Summary JSON (for programmatic use) ---
    console.out("[8/9] Exporting summary JSON...")
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
    json_path = out_dir / "ablation_analysis.json"
    with open(json_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    console.out(f"  Saved: {json_path}")

    # --- Worst-degradation patients (flagged for manual review) ---
    console.out("[9/9] Flagging worst-degradation patients...")
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
        worst_path = out_dir / "ablation_worst_degradation.csv"
        worst_df.to_csv(worst_path, index=False)
        console.out(f"  Top degradation cases saved: {worst_path}")
        console.out("  (Review these patients manually for qualitative error analysis)")

    console.out()
    console.out("=" * 70)
    console.out("  ANALYSIS COMPLETE")
    console.out(f"  All outputs in: {out_dir}")
    console.out("=" * 70)
    console.out()

#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
