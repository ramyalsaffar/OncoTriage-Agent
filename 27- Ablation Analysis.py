# Ablation Study Analysis
##########################################

"""
Ablation Analysis & Visualization

Reads ablation_results.db produced by File 26 (Ablation Study) and generates
publication-ready tables, figures, and statistical tests for the paper.

Outputs (saved to result_ablation_path):
    Tables:
    - ablation_comparison_table.csv     Main comparison table with 95% CIs
    - ablation_statistical_tests.csv    Wilcoxon tests + effect sizes
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

    # Derived metrics
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
        score_mean          =("avg_match_score", "mean"),
        score_std           =("avg_match_score", "std"),
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
        cost_per_elig_mean  =("cost_per_eligible", "mean"),
        tokens_per_elig_mean=("tokens_per_eligible", "mean"),
        n_patients          =("patient_id", "count"),
    ).reset_index()

    
    # Bootstrapped 95% CIs (TrialGPT standard: Nature Communications)
    N_BOOT = 1000
    ci_cols = ["eligible_count", "avg_match_score", "estimated_cost_usd", "total_time"]
    for config in CONFIG_ORDER:
        config_data = df[df["config_name"] == config]
        idx_rows = table[table["config_name"] == config].index
        if len(idx_rows) == 0:
            continue
        idx = idx_rows[0]
        
        for col in ci_cols:
            vals = config_data[col].dropna().values
            if len(vals) < 2:
                table.loc[idx, f"{col}_ci_lo"] = np.nan
                table.loc[idx, f"{col}_ci_hi"] = np.nan
                continue
            rng = np.random.default_rng(42)
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

    for col in ["eligible_mean", "score_mean", "cost_mean", "time_mean",
                "evaluated_mean", "cost_per_elig_mean", "input_tokens_mean"]:
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

def run_statistical_tests(df: pd.DataFrame) -> pd.DataFrame:
    """
    Paired Wilcoxon signed-rank tests + effect sizes: each config vs baseline.

    Uses per-patient paired differences (same patient, different config).
    Wilcoxon is appropriate because:
        - Paired data (same patients across configs)
        - No normality assumption needed
        - Small-to-medium sample sizes

    Effect size: rank-biserial correlation r = 1 - (2W / (n*(n+1)/2))
    where W is the Wilcoxon statistic and n is the number of non-zero
    differences. Interpretation: |r| < 0.1 negligible, < 0.3 small,
    < 0.5 medium, >= 0.5 large.
    """
    from scipy.stats import wilcoxon

    baseline_df = df[df["config_name"] == BASELINE].set_index("patient_id")
    results = []

    test_cols = ["eligible_count", "avg_match_score", "estimated_cost_usd", "total_time",
                 "candidates_evaluated"]

    for config in CONFIG_ORDER:
        if config == BASELINE:
            continue

        config_df = df[df["config_name"] == config].set_index("patient_id")

        # Align on shared patients
        shared = baseline_df.index.intersection(config_df.index)
        if len(shared) < 10:
            continue

        row = {"config_name": config, "config_label": CONFIG_LABELS[config],
               "n_paired": len(shared)}

        for col in test_cols:
            bl_vals = baseline_df.loc[shared, col].values
            cf_vals = config_df.loc[shared, col].values
            diff = cf_vals - bl_vals

            # Skip if all differences are zero
            if np.all(diff == 0):
                row[f"{col}_stat"] = None
                row[f"{col}_p"] = 1.0
                row[f"{col}_sig"] = ""
                row[f"{col}_effect_r"] = 0.0
                row[f"{col}_effect_size"] = "zero"
                continue

            try:
                stat, p = wilcoxon(diff, alternative="two-sided")
                sig = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else ""

                # Rank-biserial effect size
                n_nonzero = np.count_nonzero(diff)
                if n_nonzero > 0:
                    r = 1 - (2 * stat) / (n_nonzero * (n_nonzero + 1) / 2)
                else:
                    r = 0.0
                abs_r = abs(r)
                size_label = ("large" if abs_r >= 0.5 else "medium" if abs_r >= 0.3
                              else "small" if abs_r >= 0.1 else "negligible")

                row[f"{col}_stat"] = round(stat, 2)
                row[f"{col}_p"] = round(p, 4)
                row[f"{col}_sig"] = sig
                row[f"{col}_effect_r"] = round(r, 3)
                row[f"{col}_effect_size"] = size_label
            except Exception:
                row[f"{col}_stat"] = None
                row[f"{col}_p"] = None
                row[f"{col}_sig"] = ""
                row[f"{col}_effect_r"] = None
                row[f"{col}_effect_size"] = ""

        results.append(row)

    return pd.DataFrame(results)


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
    """Cost-per-eligible-match by config (the key rule filter argument)."""
    # Only patients with at least 1 eligible match
    has_match = df[df["eligible_count"] > 0].copy()

    means = has_match.groupby("config_name", observed=True)["cost_per_eligible"].agg(
        ["mean", "std"]).loc[CONFIG_ORDER]
    means.index = [CONFIG_LABELS[c] for c in means.index]

    fig, ax = plt.subplots(figsize=(10, 6))
    bars = ax.bar(range(len(means)), means["mean"], yerr=means["std"],
                  capsize=4, color=["#3498db"] + ["#95a5a6"] * (len(means) - 1))
    bars[0].set_color("#2ecc71")  # Highlight baseline

    ax.set_xticks(range(len(means)))
    ax.set_xticklabels(means.index, rotation=30, ha="right")
    ax.set_ylabel("Cost per Eligible Match (USD)")
    ax.set_title("Cost Efficiency: Cost per Eligible Match by Configuration")

    for i, (m, s) in enumerate(zip(means["mean"], means["std"])):
        if not np.isnan(m):
            ax.text(i, m + s + 0.0002, f"${m:.4f}", ha="center", fontsize=8)

    plt.tight_layout()
    path = OUTPUT_DIR / "ablation_cost_efficiency.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {path}")


def plot_score_distribution(df: pd.DataFrame) -> None:
    """Box plot of avg_match_score distributions per config."""
    has_score = df[df["avg_match_score"].notna()].copy()

    fig, ax = plt.subplots(figsize=(12, 6))

    data = [has_score[has_score["config_name"] == c]["avg_match_score"].values
            for c in CONFIG_ORDER]
    labels = [CONFIG_LABELS[c] for c in CONFIG_ORDER]

    bp = ax.boxplot(data, labels=labels, patch_artist=True, showmeans=True,
                    meanprops={"marker": "D", "markerfacecolor": "red", "markersize": 5})

    colors = ["#2ecc71"] + ["#bdc3c7"] * (len(CONFIG_ORDER) - 1)
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)

    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_ylabel("Average Match Score")
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
                    errors: pd.DataFrame) -> None:
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

    # Main comparison table with 95% CIs
    lines.append("-" * 70)
    lines.append("COMPARISON TABLE (mean [95% CI])")
    lines.append("-" * 70)

    header = f"{'Config':<28} {'Eligible':>18} {'Score':>18} {'Cost (USD)':>18}"
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
        # Score with CI
        s_lo = row.get("avg_match_score_ci_lo", np.nan)
        s_hi = row.get("avg_match_score_ci_hi", np.nan)
        s_lo = np.nan if s_lo is None else float(s_lo)
        if not np.isnan(s_lo):
            score = f"{row['score_mean']:.3f} [{s_lo:.3f},{s_hi:.3f}]"
        else:
            score = f"{row['score_mean']:.3f}" if not np.isnan(row['score_mean']) else "N/A"
        # Cost with CI
        c_lo = row.get("estimated_cost_usd_ci_lo", np.nan)
        c_hi = row.get("estimated_cost_usd_ci_hi", np.nan)
        c_lo = np.nan if c_lo is None else float(c_lo)
        if not np.isnan(c_lo):
            cost = f"${row['cost_mean']:.4f} [{c_lo:.4f},{c_hi:.4f}]"
        else:
            cost = f"${row['cost_mean']:.4f}"
        lines.append(f"{name:<28} {elig:>18} {score:>18} {cost:>18}")

    lines.append("")

    # Deltas
    lines.append("-" * 70)
    lines.append("DELTAS vs BASELINE (full_pipeline)")
    lines.append("-" * 70)

    header2 = f"{'Config':<28} {'Δ Elig':>8} {'Δ Cost':>8} {'Δ Time':>8} {'Δ% Elig':>8} {'Δ% Cost':>8}"
    lines.append(header2)
    lines.append("-" * len(header2))

    for _, row in table[table["config_name"] != BASELINE].iterrows():
        name = CONFIG_LABELS.get(row["config_name"], row["config_name"])
        d_elig = f"{row['Δ_eligible_mean']:+.2f}"
        d_cost = f"{row['Δ_cost_mean']:+.4f}"
        d_time = f"{row['Δ_time_mean']:+.1f}s"
        dp_elig = f"{row.get('Δ%_eligible_mean', 0):+.1f}%"
        dp_cost = f"{row.get('Δ%_cost_mean', 0):+.1f}%"
        lines.append(f"{name:<28} {d_elig:>8} {d_cost:>8} {d_time:>8} {dp_elig:>8} {dp_cost:>8}")

    lines.append("")

    # Statistical significance with effect sizes
    if len(stats) > 0:
        lines.append("-" * 70)
        lines.append("STATISTICAL SIGNIFICANCE (Wilcoxon + rank-biserial effect size)")
        lines.append("-" * 70)

        for _, row in stats.iterrows():
            name = row["config_label"]
            lines.append(f"\n  {name} (n={row['n_paired']} paired patients):")
            for col in ["eligible_count", "avg_match_score", "estimated_cost_usd", "total_time"]:
                p = row.get(f"{col}_p")
                sig = row.get(f"{col}_sig", "")
                r = row.get(f"{col}_effect_r")
                size = row.get(f"{col}_effect_size", "")
                if p is not None:
                    col_label = col.replace("_", " ").title()
                    r_str = f"r={r:.3f} ({size})" if r is not None else ""
                    lines.append(f"    {col_label:<30} p={p:.4f} {sig:<4} {r_str}")

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

    # Token efficiency
    lines.append("-" * 70)
    lines.append("TOKEN EFFICIENCY (tokens per eligible match)")
    lines.append("-" * 70)

    has_match = df[df["eligible_count"] > 0]
    tpe = has_match.groupby("config_name", observed=True)["tokens_per_eligible"].mean()
    for config in CONFIG_ORDER:
        if config in tpe.index and not np.isnan(tpe[config]):
            name = CONFIG_LABELS[config]
            lines.append(f"  {name:<28} {tpe[config]:,.0f} tokens/match")

    lines.append("")

    # Cost efficiency
    lines.append("-" * 70)
    lines.append("COST EFFICIENCY (cost per eligible match)")
    lines.append("-" * 70)

    cpe = has_match.groupby("config_name", observed=True)["cost_per_eligible"].mean()
    for config in CONFIG_ORDER:
        if config in cpe.index:
            name = CONFIG_LABELS[config]
            lines.append(f"  {name:<28} ${cpe[config]:.4f}")

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
    print("[1/8] Loading data...")
    df = load_ablation_data()
    errors = load_error_data()

    if len(df) == 0:
        print("ERROR: No successful results found in ablation database.")
        sys.exit(1)

    # --- Build comparison table (with bootstrapped 95% CIs) ---
    print("[2/8] Building comparison table with 95% CIs...")
    table = build_comparison_table(df)
    csv_path = OUTPUT_DIR / "ablation_comparison_table.csv"
    table.to_csv(csv_path, index=False)
    print(f"  Saved: {csv_path}")

    # --- Statistical tests (with effect sizes) ---
    print("[3/8] Running statistical tests + effect sizes...")
    stats = run_statistical_tests(df)
    stats_path = OUTPUT_DIR / "ablation_statistical_tests.csv"
    stats.to_csv(stats_path, index=False)
    print(f"  Saved: {stats_path}")

    # --- Win/Tie/Loss pairwise analysis ---
    print("[4/8] Building win/tie/loss table...")
    wl_table = build_win_loss_table(df)
    wl_path = OUTPUT_DIR / "ablation_win_loss_table.csv"
    wl_table.to_csv(wl_path, index=False)
    print(f"  Saved: {wl_path}")

    # --- Visualizations (original + new) ---
    print("[5/8] Generating visualizations...")
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
    print("[6/8] Generating report...")
    generate_report(df, table, stats, wl_table, errors)

    # --- Summary JSON (for programmatic use) ---
    print("[7/8] Exporting summary JSON...")
    summary = {
        "comparison_table": table.to_dict(orient="records"),
        "statistical_tests": stats.to_dict(orient="records"),
        "win_loss_table": wl_table.to_dict(orient="records"),
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
    print("[8/8] Flagging worst-degradation patients...")
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

