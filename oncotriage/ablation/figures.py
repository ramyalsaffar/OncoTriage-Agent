"""The nine ablation figures (pass 20f-4).

SPLIT OUT OF ``oncotriage/ablation/analysis.py`` SO THAT THE MODULE-SCOPE
``matplotlib`` IMPORT LIVES IN A SMALL FILE. analysis.py was 1,976 lines with
24 top-level definitions, NINE of which touched ``plt`` -- measured with an AST
walk over every ``Name`` load in each definition, not by reading -- and the
matplotlib import at its top was the second of the two deliberate module-scope
exceptions in the package (``oncotriage/fhir/explore.py`` is the first).
The exception is unchanged in kind; it is now 460 lines wide instead of 1,976.

WHAT THIS BUYS, STATED HONESTLY. It does NOT make importing ``analysis``
matplotlib-free: ``analysis.main()`` calls all nine, so ``analysis`` imports
this module at module scope, and check 1b of
``tests/test_package_invariants.py`` forbids moving that import into the
function body. What it buys is that the exception is now confined to a file
whose entire subject is drawing, so a reader of the statistics can see at a
glance that no statistic depends on a plotting library, and anything that wants
the tables without the figures can import ``oncotriage.ablation.common`` and the
statistics functions directly.

EVERY BODY WAS EXTRACTED BY AST SPAN, NEVER RETYPED. Two mechanical edits were
applied to each, both asserted rather than assumed:

  * ``output_dir() / "name.png"`` became ``out_dir / "name.png"``;
  * the signature gained ``out_dir`` as a required second parameter.

``out_dir`` is REQUIRED rather than defaulting to ``common.output_dir()``. A
default would mean that a caller who forgot it during a ``--db`` run wrote nine
PNGs into the PRODUCTION results directory describing a scratch database, and
silently -- the same reasoning ``empty_database(db_path, flag)`` and
``download_all_collections(output_dir, ...)`` already carry. ``main()`` is the
only caller in the repository, verified by grep before the signature moved.

scipy is not imported here at all; it never was in these nine.
"""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from oncotriage.ablation.common import BASELINE, CONFIG_LABELS, CONFIG_ORDER
from oncotriage.observability import console


#------------------------------------------------------------------------------


# ===========================================================================
# 3. VISUALIZATIONS
# ===========================================================================

def plot_funnel_chart(df: pd.DataFrame, out_dir: Path) -> None:
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
    path = out_dir / "ablation_funnel_chart.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    console.out(f"  Saved: {path}")


def plot_delta_chart(table: pd.DataFrame, out_dir: Path) -> None:
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
    path = out_dir / "ablation_delta_chart.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    console.out(f"  Saved: {path}")


def plot_cost_efficiency(df: pd.DataFrame, out_dir: Path) -> None:
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
    path = out_dir / "ablation_cost_efficiency.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    console.out(f"  Saved: {path}")


def plot_score_distribution(df: pd.DataFrame, out_dir: Path) -> None:
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
    path = out_dir / "ablation_score_distribution.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    console.out(f"  Saved: {path}")


def plot_cancer_group_heatmap(df: pd.DataFrame, out_dir: Path) -> None:
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
        console.out("  Skipped: cancer group heatmap (no data)")
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
    path = out_dir / "ablation_cancer_group_heatmap.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    console.out(f"  Saved: {path}")


def plot_timing_breakdown(df: pd.DataFrame, out_dir: Path) -> None:
    """Stacked bar chart of per-stage latency by config."""
    timing_cols = [
        ("query_expansion_time",  "Query Expansion"),
        ("hybrid_retrieval_time", "Hybrid Retrieval"),
        ("cross_encoder_time",    "Cross-Encoder"),
        ("rule_filter_time",      "Rule Filter"),
        ("llm_classifier_evaluation_time", "GPT-4o Evaluation"),
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
    path = out_dir / "ablation_timing_breakdown.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    console.out(f"  Saved: {path}")


def plot_win_loss_chart(wl_table: pd.DataFrame, out_dir: Path) -> None:
    """Stacked horizontal bar: wins/ties/losses per config."""
    if wl_table.empty:
        console.out("  Skipped: win/loss chart (no data)")
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
    path = out_dir / "ablation_win_loss_chart.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    console.out(f"  Saved: {path}")


def plot_retrieval_venn(df: pd.DataFrame, out_dir: Path) -> None:
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
        console.out("  Skipped: retrieval venn (insufficient data)")
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
    path = out_dir / "ablation_retrieval_venn.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    console.out(f"  Saved: {path}")


def plot_patient_scatter(df: pd.DataFrame, out_dir: Path) -> None:
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
    path = out_dir / "ablation_patient_scatter.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    console.out(f"  Saved: {path}")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
