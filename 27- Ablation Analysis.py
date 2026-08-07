# Ablation Study Analysis
#########################

"""
Ablation Analysis & Visualization — entry point.

Reads ``ablation_results.db`` (produced by File 26) and generates the tables,
figures and statistical tests for the paper. It NEVER writes to that database.

The analysis itself is ``oncotriage/ablation/analysis.py``. Item 20c pass 3d
moved it there; this file is a ``__main__`` block and the one import it needs.

Outputs, all into result_ablation_path
--------------------------------------
    Tables
      ablation_comparison_table.csv     main table with 95% CIs, each carrying
                                        the n it was drawn on
      ablation_statistical_tests.csv    Wilcoxon, BH-FDR corrected, one row per
                                        (config, outcome metric), with raw p,
                                        adjusted p, a SIGNED effect size and a
                                        status for untested comparisons
      ablation_descriptive_metrics.csv  cost / latency / candidate deltas,
                                        reported without p-values by design
      ablation_pairing_report.csv       per-config paired and DROPPED patient
                                        sets, with a reason for each drop
      ablation_win_loss_table.csv       per-patient pairwise wins/ties/losses

    Figures
      ablation_funnel_chart.png         per-stage candidate funnel by config
      ablation_delta_chart.png          delta from baseline
      ablation_cost_efficiency.png      cost per eligible match
      ablation_score_distribution.png   match score distributions
      ablation_cancer_group_heatmap.png eligible count by config x cancer group
      ablation_timing_breakdown.png     stacked per-stage latency
      ablation_retrieval_venn.png       BM25 vs vector unique contributions
      ablation_win_loss_chart.png       pairwise win/tie/loss
      ablation_patient_scatter.png      baseline vs ablated eligible per patient

    Reports
      ablation_full_report.txt
      ablation_analysis.json

Statistics
----------
The test family is (non-baseline configs) x ABLATION_OUTCOME_METRICS, corrected
with Benjamini-Hochberg FDR. Cost and latency are excluded from the family as
deterministic consequences of the ablation rather than hypotheses. Comparisons
that could not be tested (identical values, a scipy failure, too few pairs) are
recorded with a status and excluded from the correction rather than entered as
p = 1.0. Effect sizes are SIGNED. The minimum detectable effect for the design is
computed once and printed in the report's methods block.

``--db PATH`` (pass 20f-4) analyses a database other than the production
``ablation_results.db``, and every table, figure and report above lands in THAT
database's directory rather than beside the production one. It matches
``26- Ablation Study.py --db`` exactly: same flag, same shared
``ABLATION_DB_FILENAME``, same parent-directory guard, so a study written to a
scratch path can now be analysed. Before this pass ``analysis.ablation_db()``
took no argument at all and hardcoded the filename, which meant the isolation
File 26 gained in pass 20f-1 stopped at the writer -- and it also meant the
outputs would have been written over the production tables and figures while
describing a different database.

THIS COSTS NOTHING. It makes no model call and no network call; it reads one
SQLite file and writes files beside it.

NO RE-EXPORT SHIM. All 33 of this file's top-level names were grepped against
every .py, .md, .toml and .yml in the tree; the only hits are File 26's own
``ABLATION_DB`` over the same directory, three unrelated uses of the word
``BASELINE`` in Files 43 and 44, ``OUTPUT_DIR`` in File 06 and
``oncotriage/fhir/explore.py`` (a different constant, since removed there), and
the exec-bootstrap locals every numbered file shares.

Run from terminal:
    cd ".../03- Code"
    python "27- Ablation Analysis.py"
    python "27- Ablation Analysis.py" --db /tmp/scratch/ablation_results.db
"""

import argparse
import os
import sys


# Make the oncotriage package importable
#---------------------------------------
# See the same block in "04- FHIR Generate Data.py" and "29- Download Qdrant
# Data.py". `pip install -e .` from 03- Code/ makes it a no-op.
try:
    import oncotriage  # noqa: F401
except ImportError:
    for _candidate, _how in (
        (os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals()
         else None, "__file__"),
        (os.getcwd(), "cwd"),
    ):
        if _candidate and os.path.isdir(os.path.join(_candidate, "oncotriage")):
            if _candidate not in sys.path:
                sys.path.insert(0, _candidate)
            print(f"[Bootstrap] oncotriage package found at {_candidate} "
                  f"(via {_how}); added to sys.path")
            break
    else:
        raise
    del _candidate, _how

from oncotriage.ablation.analysis import main


#------------------------------------------------------------------------------


def parse_args():
    """--db, matching "26- Ablation Study.py"'s flag of the same name.

    Default None, which is the production database and every documented
    command's behaviour. The help says out loud that the OUTPUTS follow the
    database, because a run that quietly stopped updating the production tables
    -- or, worse, one that overwrote them from a scratch database -- would be a
    surprise. Same wording discipline as File 26's --db help.
    """
    parser = argparse.ArgumentParser(
        description="Ablation analysis: tables, figures and statistical tests."
    )
    parser.add_argument(
        "--db", default=None, metavar="PATH",
        help="Analyse this SQLite database instead of the production "
             "ablation_results.db. Every table, figure and report is written "
             "beside it. Default: the production database."
    )
    return parser.parse_args()


if __name__ == "__main__":
    main(db_path=parse_args().db)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Mar  3 10:17:15 2026

@author: ramyalsaffar
"""
