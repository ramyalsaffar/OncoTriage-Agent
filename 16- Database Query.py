# Query the database
####################


# ===========================================================================
# EXEC CHAIN: 01 -> 02 -> 03
# ===========================================================================
# Five free names: inferences_path, pd and sqlite3 from 01, get_model_cost
# from 02, PRICING_CONFIG from 03. 03 is loaded through exec_chain rather
# than a raw exec because 03 itself calls load_env_keys(), which 02
# defines -- exec'ing 03 without 02 in place first would fail on that.
#
# Item 20a: this file sits in the code directory, so __file__ locates it with
# no hardcoded path. __file__ is bound when the file is run as a script (every
# documented entry point for it) and when Spyder runfile()s it. In a bare
# interactive paste it is not bound, and the working directory is the only
# remaining candidate -- taken, but announced, never silently.
import os as _os_boot
if "__file__" in globals():
    _code_dir = _os_boot.path.dirname(_os_boot.path.abspath(__file__)) + _os_boot.sep
else:
    _code_dir = _os_boot.getcwd() + _os_boot.sep
    print(f"[Bootstrap] __file__ unbound; using the working directory as the code directory: {_code_dir}")
del _os_boot

for _bootstrap in ("01- Imports.py", "02- Utility Functions.py"):
    with open(_code_dir + _bootstrap) as _fh:
        exec(_fh.read(), globals())

exec_chain(
    ["03- Config.py"],
    caller_file=_code_dir + "16- Database Query.py",
    caller_globals=globals(),
    chain_label="01 → 02 → 03",
)


#------------------------------------------------------------------------------


# Connect to the SQLite database server
conn = sqlite3.connect(inferences_path)


# Create SQL 
cursor = conn.cursor()


#------------------------------------------------------------------------------


# Check the tables in the database
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
print(cursor.fetchall())


#------------------------------------------------------------------------------


#=================
# Inferences Table
#=================


#------------------------------------------------------------------------------


# Collect all data from inferences table
cursor.execute("SELECT * FROM inferences")
print(cursor.fetchall())


# Using Pandas
df_inferences = pd.read_sql_query("SELECT * FROM inferences", conn)
print(df_inferences)


#------------------------------------------------------------------------------


df_timeout = pd.read_sql_query("SELECT total_time, gpt4o_evaluation_time, gpt4o_output_tokens FROM inferences", conn)
print(df_timeout.describe())


#------------------------------------------------------------------------------


df_timeout = pd.read_sql_query("""
    SELECT patient_id, age, condition_count, medication_count, 
           candidates_evaluated, total_time, gpt4o_evaluation_time, 
           gpt4o_input_tokens, gpt4o_output_tokens, error
    FROM inferences 
    ORDER BY total_time DESC 
    LIMIT 5
""", conn)
print(df_timeout)


#------------------------------------------------------------------------------

# Investigation

#------------------------------------------------------------------------------


# Query 1: Overall performance distribution
df_performance = pd.read_sql_query("""
    SELECT 
        total_time,
        gpt4o_evaluation_time,
        gpt4o_input_tokens,
        gpt4o_output_tokens,
        candidates_evaluated,
        estimated_cost_usd,
        error
    FROM inferences
    ORDER BY total_time DESC
""", conn)

print("=== PERFORMANCE DISTRIBUTION ===")
print(df_performance.describe())
print("\n")


# Query 2: Top 10 slowest patients
df_slowest = pd.read_sql_query("""
    SELECT 
        patient_id,
        age,
        sex,
        condition_count,
        medication_count,
        candidates_evaluated,
        total_time,
        gpt4o_evaluation_time,
        gpt4o_input_tokens,
        gpt4o_output_tokens,
        error
    FROM inferences
    ORDER BY total_time DESC
    LIMIT 10
""", conn)

print("=== TOP 10 SLOWEST PATIENTS ===")
print(df_slowest.to_string(index=False))
print("\n")


# Query 3: Patients with output tokens > 6000
df_verbose = pd.read_sql_query("""
    SELECT 
        patient_id,
        candidates_evaluated,
        gpt4o_output_tokens,
        gpt4o_output_tokens / NULLIF(candidates_evaluated, 0) as tokens_per_trial,
        total_time
    FROM inferences
    WHERE gpt4o_output_tokens > 4000
    ORDER BY gpt4o_output_tokens DESC
""", conn)

print("=== PATIENTS WITH OUTPUT > 4000 TOKENS ===")
print(df_verbose.to_string(index=False))
print("\n")


# Query 4: Error distribution
df_errors = pd.read_sql_query("""
    SELECT 
        error,
        COUNT(*) as count
    FROM inferences
    WHERE error != ''
    GROUP BY error
""", conn)

print("=== ERROR TYPES ===")
print(df_errors.to_string(index=False))
print("\n")


# Query 5: Get one full prompt to test in ChatGPT
# (Get the slowest patient's prompt)
df_prompt = pd.read_sql_query("""
    SELECT 
        patient_id,
        gpt4o_prompt,
        gpt4o_output_tokens,
        total_time
    FROM inferences
    ORDER BY total_time DESC
    LIMIT 1
""", conn)

print("=== PROMPT FOR CHATGPT TESTING ===")
print(f"Patient: {df_prompt.iloc[0]['patient_id']}")
print(f"Output tokens: {df_prompt.iloc[0]['gpt4o_output_tokens']}")
print(f"Total time: {df_prompt.iloc[0]['total_time']:.1f}s")
print("\nCopy this prompt to ChatGPT:\n")
print("="*80)
print(df_prompt.iloc[0]['gpt4o_prompt'])
print("="*80)


# Query 6: Stage-level performance bottlenecks
df_stages = pd.read_sql_query("""
    SELECT 
        AVG(query_expansion_time) as avg_expansion,
        AVG(hybrid_retrieval_time) as avg_retrieval,
        AVG(cross_encoder_time) as avg_cross_encoder,
        AVG(rule_filter_time) as avg_filter,
        AVG(gpt4o_evaluation_time) as avg_gpt4o,
        MAX(query_expansion_time) as max_expansion,
        MAX(hybrid_retrieval_time) as max_retrieval,
        MAX(cross_encoder_time) as max_cross_encoder,
        MAX(rule_filter_time) as max_filter,
        MAX(gpt4o_evaluation_time) as max_gpt4o
    FROM inferences
""", conn)
print("=== STAGE-LEVEL BOTTLENECKS ===")
print(df_stages.T)
print("\n")


# Query 7: Retrieval quality - how many candidates make it through each stage?
df_funnel = pd.read_sql_query("""
    SELECT 
        AVG(candidates_retrieved) as avg_retrieved,
        AVG(candidates_reranked) as avg_reranked,
        AVG(candidates_filtered) as avg_filtered,
        AVG(candidates_evaluated) as avg_evaluated,
        AVG(eligible_matches) as avg_eligible,
        AVG(CAST(candidates_filtered AS FLOAT) / NULLIF(candidates_retrieved, 0)) as rerank_retention_rate,
        AVG(CAST(candidates_evaluated AS FLOAT) / NULLIF(candidates_filtered, 0)) as filter_retention_rate,
        AVG(CAST(eligible_matches AS FLOAT) / NULLIF(candidates_evaluated, 0)) as eligibility_rate
    FROM inferences
    WHERE candidates_retrieved > 0
""", conn)
print("=== PIPELINE FUNNEL ANALYSIS ===")
print(df_funnel.T)
print("\n")


# Query 8: Token efficiency - correlation between patient complexity and tokens
df_token_efficiency = pd.read_sql_query("""
    SELECT 
        condition_count,
        medication_count,
        candidates_evaluated,
        AVG(gpt4o_input_tokens) as avg_input_tokens,
        AVG(gpt4o_output_tokens) as avg_output_tokens,
        AVG(gpt4o_output_tokens / NULLIF(candidates_evaluated, 0)) as avg_tokens_per_trial,
        COUNT(*) as patient_count
    FROM inferences
    WHERE candidates_evaluated > 0
    GROUP BY condition_count, medication_count, candidates_evaluated
    ORDER BY avg_tokens_per_trial DESC
    LIMIT 20
""", conn)
print("=== TOKEN EFFICIENCY BY PATIENT COMPLEXITY ===")
print(df_token_efficiency.to_string(index=False))
print("\n")


# Query 9: Expansion model performance
# Query 9: Stage 1 expansion stats
#
# THE TOKEN COLUMNS DO NOT EXIST. This query used to select
# expansion_input_tokens / expansion_output_tokens, which are not in
# inferences.db: File 13's terminal nodes emit the keys (always 0 — Stage 1 is
# deterministic and calls no LLM), but File 14 has never had columns for them
# and never inserted them. So the query raised OperationalError "no such
# column" on every run and took the whole of File 16 down with it before
# reaching anything below, including the cost breakdown.
#
# Rewritten to report what the table actually holds. If Stage 1 ever grows an
# LLM, the columns have to be added to File 14 first; there is nothing to
# select until then, and inventing a rate for them is what Query 10 below was
# doing.
df_expansion = pd.read_sql_query("""
    SELECT
        COUNT(*)                    as rows_n,
        AVG(query_expansion_time)   as avg_expansion_time,
        MAX(query_expansion_time)   as max_expansion_time,
        SUM(query_expansion_path = 'base_query_fallback') as fallback_runs,
        SUM(query_expansion_path IS NULL)                 as path_not_reported
    FROM inferences
""", conn)
print("=== EXPANSION (STAGE 1) STATS ===")
print("Stage 1 is rule-based and calls no LLM, so there are no expansion "
      "token columns to report.")
print(df_expansion.T)
print("\n")


# Query 10: Cost breakdown by model
#
# PRICED PER MODEL, NOT AT ONE RATE. This query used to have 2.50 and 10.00
# written into the SQL and summed the whole table against them. That was
# already a duplicate of PRICING_CONFIG that nothing kept in sync, and it
# became actively wrong on 2026-08-04 when the judge moved from
# gpt-4o-2024-08-06 to gpt-5.6-terra: inferences.db now holds rows from both,
# at different input AND output rates, and one blended rate misstates every row
# of at least one of them. The grouping key is matching_model, which File 14
# writes from the model that ANSWERED the call, so each group is priced by the
# model that actually produced its tokens.
#
# Rates come from get_model_cost() / PRICING_CONFIG (File 03), never from a
# literal here, so there is exactly one pricing table in the project and this
# query raises UnknownModelPricingError rather than quietly under-reporting
# when a model is missing from it.
#
# The expansion terms are gone rather than repriced. The old 0.15 / 0.60
# literals priced a model this project has never called, against two columns
# that do not exist in inferences.db at all (see Query 9 above) — so those two
# SUMs would have raised "no such column" if the query had ever run.
df_cost_by_model = pd.read_sql_query("""
    SELECT
        matching_model,
        COUNT(*)                     as rows_n,
        SUM(gpt4o_input_tokens)      as input_tokens,
        SUM(gpt4o_output_tokens)     as output_tokens,
        SUM(gpt4o_reasoning_tokens)  as reasoning_tokens,
        SUM(estimated_cost_usd)      as stored_cost
    FROM inferences
    GROUP BY matching_model
    ORDER BY rows_n DESC
""", conn)

_cost_rows = []
for _row in df_cost_by_model.itertuples(index=False):
    _in = int(_row.input_tokens or 0)
    _out = int(_row.output_tokens or 0)

    # matching_model IS NULL means no Stage 5 response was obtained for those
    # rows (node_no_candidates, or a failure before the first call returned),
    # so there is nothing to price and nothing to price it against. Reported
    # as a group rather than dropped: a NULL group carrying non-zero tokens
    # would be a logging defect, and silently excluding it is how that stays
    # invisible.
    if _row.matching_model is None:
        _in_cost = _out_cost = 0.0
        _note = ("no model recorded"
                 if (_in == 0 and _out == 0)
                 else "NO MODEL RECORDED BUT TOKENS PRESENT — logging defect")
    else:
        # Split into two calls purely to get the input and output halves
        # separately; get_model_cost returns their sum.
        _in_cost = get_model_cost(_row.matching_model, _in, 0)
        _out_cost = get_model_cost(_row.matching_model, 0, _out)
        _note = ""

    _cost_rows.append({
        "matching_model": _row.matching_model or "(none)",
        "rows": int(_row.rows_n),
        "input_tokens": _in,
        "output_tokens": _out,
        # NULL-safe: SUM() over a column that is NULL on every GPT-4o-era row
        # returns NULL for those groups. Printed as "n/a" rather than 0 —
        # GPT-4o reported no reasoning breakdown at all, which is not the same
        # as a reasoning model that did no thinking.
        "reasoning_tokens": ("n/a" if _row.reasoning_tokens is None
                             else int(_row.reasoning_tokens)),
        "input_cost": _in_cost,
        "output_cost": _out_cost,
        "recomputed_cost": _in_cost + _out_cost,
        "stored_cost": float(_row.stored_cost or 0.0),
        "note": _note,
    })

df_cost = pd.DataFrame(_cost_rows)
print("=== COST BREAKDOWN BY MODEL ===")
print(f"(priced from PRICING_CONFIG, last_updated {PRICING_CONFIG['last_updated']})")
print(df_cost.to_string(index=False))

_total_rows = int(df_cost["rows"].sum()) if len(df_cost) else 0
_recomputed_total = float(df_cost["recomputed_cost"].sum()) if len(df_cost) else 0.0
_stored_total = float(df_cost["stored_cost"].sum()) if len(df_cost) else 0.0
print(f"\nRows: {_total_rows}")
print(f"Recomputed total: ${_recomputed_total:.4f}")
print(f"Stored total (estimated_cost_usd): ${_stored_total:.4f}")

# The two totals should agree. They diverge when PRICING_CONFIG changed after
# rows were written — which is legitimate and is exactly why pricing_version is
# stored per row — so this is reported, not asserted.
if _stored_total:
    print(f"Divergence: {(_recomputed_total - _stored_total) / _stored_total * 100:+.2f}% "
          f"(non-zero means PRICING_CONFIG changed since some rows were written; "
          f"see the pricing_version column)")

if _total_rows:
    print(f"Projected cost for 1000 patients, at the current mix: "
          f"${_recomputed_total / _total_rows * 1000:.2f}")

print("\n")


# Query 11: Demographic impact on matching
df_demographics = pd.read_sql_query("""
    SELECT 
        age / 10 * 10 as age_group,
        sex,
        COUNT(*) as patient_count,
        AVG(eligible_matches) as avg_eligible_matches,
        AVG(near_misses) as avg_near_misses,
        AVG(total_time) as avg_time
    FROM inferences
    WHERE age IS NOT NULL
    GROUP BY age_group, sex
    ORDER BY age_group, sex
""", conn)
print("=== DEMOGRAPHIC MATCHING PATTERNS ===")
print(df_demographics.to_string(index=False))
print("\n")


# Query 12: Retrieval method comparison (BM25 vs Vector)
#
# bm25_retrieved is the count of DISTINCT trials returned across the three
# sparse field queries (title / conditions / criteria), not their sum, so the
# denominator below is the size of the pre-fusion candidate union and
# fusion_efficiency is the share of it that survived into the RRF pool.
#
# Rows written before the logging-contract fix hold BM25_RETRIEVAL_SIZE and
# VECTOR_RETRIEVAL_SIZE instead of observed counts. They are indistinguishable
# from a genuine run of that size, so a stddev of ~0 over a long window means
# the window predates the fix, not that retrieval is perfectly stable.
df_retrieval = pd.read_sql_query("""
    SELECT
        COUNT(*) as n_rows,
        AVG(bm25_retrieved) as avg_bm25,
        AVG(vector_retrieved) as avg_vector,
        AVG(candidates_retrieved) as avg_total_after_fusion,
        AVG(CAST(candidates_retrieved AS FLOAT)
            / NULLIF(bm25_retrieved + vector_retrieved, 0)) as fusion_efficiency
    FROM inferences
    WHERE bm25_retrieved IS NOT NULL
      AND vector_retrieved IS NOT NULL
      AND (bm25_retrieved + vector_retrieved) > 0
""", conn)
print("=== RETRIEVAL METHOD PERFORMANCE ===")
print(df_retrieval.T)
print("\n")


# Query 13: Quality filter effectiveness
df_quality_filter = pd.read_sql_query("""
    SELECT 
        AVG(candidates_reranked) as avg_before_quality_filter,
        AVG(candidates_after_quality_filter) as avg_after_quality_filter,
        AVG(CAST(candidates_after_quality_filter AS FLOAT) / NULLIF(candidates_reranked, 0)) as quality_retention_rate,
        COUNT(CASE WHEN candidates_after_quality_filter = 0 THEN 1 END) as patients_filtered_out_completely
    FROM inferences
""", conn)
print("=== QUALITY FILTER EFFECTIVENESS ===")
print(df_quality_filter.T)
print("\n")


# Query 14: Extreme cases - patients with unusual patterns
df_extremes = pd.read_sql_query("""
    SELECT 
        patient_id,
        condition_count,
        medication_count,
        candidates_evaluated,
        gpt4o_output_tokens,
        total_time,
        eligible_matches,
        CASE 
            WHEN medication_count > 100 THEN 'High Med Count'
            WHEN gpt4o_output_tokens > 10000 THEN 'Verbose Output'
            WHEN total_time > 120 THEN 'Slow Processing'
            WHEN candidates_evaluated = 0 THEN 'No Candidates'
            ELSE 'Other'
        END as anomaly_type
    FROM inferences
    WHERE medication_count > 100 
       OR gpt4o_output_tokens > 10000 
       OR total_time > 120
       OR (candidates_retrieved > 0 AND candidates_evaluated = 0)
    ORDER BY total_time DESC
""", conn)
print("=== EXTREME CASES / ANOMALIES ===")
print(df_extremes.to_string(index=False))
print("\n")


# Query 15: Success rate by trial count
df_success_rate = pd.read_sql_query("""
    SELECT 
        candidates_evaluated,
        COUNT(*) as patient_count,
        AVG(eligible_matches) as avg_eligible,
        AVG(CAST(eligible_matches AS FLOAT) / NULLIF(candidates_evaluated, 0)) as eligibility_rate,
        AVG(total_time) as avg_time
    FROM inferences
    WHERE candidates_evaluated > 0
    GROUP BY candidates_evaluated
    ORDER BY candidates_evaluated
""", conn)
print("=== SUCCESS RATE BY TRIAL COUNT ===")
print(df_success_rate.to_string(index=False))
print("\n")


# Query 16: Detect medication duplication issues
df_med_duplicates = pd.read_sql_query("""
    SELECT 
        patient_id,
        medication_count,
        gpt4o_input_tokens,
        gpt4o_input_tokens / NULLIF(candidates_evaluated, 0) as tokens_per_trial,
        CASE 
            WHEN medication_count > 100 THEN 'High'
            WHEN medication_count > 50 THEN 'Medium'
            ELSE 'Low'
        END as med_complexity
    FROM inferences
    WHERE medication_count > 0
    ORDER BY medication_count DESC
    LIMIT 10
""", conn)
print("=== MEDICATION DUPLICATION SUSPECTS ===")
print(df_med_duplicates.to_string(index=False))
print("\n")


# Query 17: Rule filter drop-off analysis
df_filter_dropoff = pd.read_sql_query("""
    SELECT 
        AVG(candidates_reranked - candidates_filtered) as avg_dropped_by_rules,
        MAX(candidates_reranked - candidates_filtered) as max_dropped_by_rules,
        AVG(CAST(candidates_filtered AS FLOAT) / NULLIF(candidates_reranked, 0)) as retention_rate,
        COUNT(CASE WHEN candidates_filtered = 0 THEN 1 END) as patients_with_zero_after_filter
    FROM inferences
    WHERE candidates_reranked > 0
""", conn)
print("=== RULE FILTER DROP-OFF ===")
print(df_filter_dropoff.T)
print("\n")


# Query 18: GPT-4o efficiency by trial count
df_gpt4o_efficiency = pd.read_sql_query("""
    SELECT 
        candidates_evaluated as trial_count,
        COUNT(*) as patient_count,
        AVG(gpt4o_evaluation_time) as avg_time,
        AVG(gpt4o_output_tokens) as avg_output_tokens,
        AVG(gpt4o_output_tokens / NULLIF(candidates_evaluated, 0)) as tokens_per_trial
    FROM inferences
    WHERE candidates_evaluated > 0
    GROUP BY candidates_evaluated
    HAVING patient_count >= 2
    ORDER BY trial_count
""", conn)
print("=== GPT-4O EFFICIENCY BY TRIAL COUNT ===")
print(df_gpt4o_efficiency.to_string(index=False))
print("\n")


# Query 19: Expansion model token analysis
df_expansion_tokens = pd.read_sql_query("""
    SELECT 
        AVG(expansion_input_tokens) as avg_input,
        AVG(expansion_output_tokens) as avg_output,
        AVG(expansion_output_tokens / NULLIF(expansion_input_tokens, 0)) as output_input_ratio,
        COUNT(CASE WHEN expansion_output_tokens > 200 THEN 1 END) as over_limit_count
    FROM inferences
    WHERE expansion_input_tokens > 0
""", conn)
print("=== EXPANSION TOKEN EFFICIENCY ===")
print(df_expansion_tokens.T)
print("\n")


# Query 20: Pipeline consistency check
df_consistency = pd.read_sql_query("""
    SELECT * FROM (
    SELECT 
        patient_id,
        candidates_retrieved,
        candidates_reranked,
        candidates_filtered,
        candidates_evaluated,
        WHEN candidates_evaluated != (eligible_matches + near_misses) THEN 'Count mismatch'
        CASE 
            WHEN candidates_retrieved != 100 THEN 'Retrieval anomaly'
            WHEN candidates_reranked != 30 THEN 'Rerank anomaly'
            WHEN candidates_evaluated != (eligible_matches + near_misses) THEN 'Count mismatch'
            WHEN candidates_filtered < candidates_evaluated THEN 'Filter < evaluated'
            ELSE 'OK'
        END as issue
    FROM inferences
) WHERE issue != 'OK'
LIMIT 20
""", conn)
print("=== PIPELINE CONSISTENCY ISSUES ===")
if df_consistency.empty:
    print("No issues found - pipeline is consistent")
else:
    print(df_consistency.to_string(index=False))
print("\n")


# Query 21: Duplicate medications
df_med_issue = pd.read_sql_query("""
    SELECT 
        patient_id,
        medication_count,
        condition_count
    FROM inferences
    ORDER BY medication_count DESC
    LIMIT 10
""", conn)
print(df_med_issue)


#------------------------------------------------------------------------------


#====================
# Trial Matches Table
#====================


#------------------------------------------------------------------------------



# Query: Check trial_matches table
cursor.execute("SELECT * FROM trial_matches")
print(cursor.fetchall())

# Using Pandas
df_matches = pd.read_sql_query("SELECT * FROM trial_matches", conn)
print(df_matches)


# Query: Most frequently matched NCT IDs
df_top_trials = pd.read_sql_query("""
    SELECT nct_id, trial_title, trial_phase,
           COUNT(*)          as total_matched_patients,
           SUM(CASE WHEN eligible = 'eligible' THEN 1 ELSE 0 END) as eligible_count,
           SUM(CASE WHEN eligible = 'not_eligible' THEN 1 ELSE 0 END) as not_eligible_count,
           SUM(CASE WHEN eligible = 'not_evaluable' THEN 1 ELSE 0 END) as not_evaluable_count,
           ROUND(AVG(match_score), 3) as avg_match_score
    FROM trial_matches
    GROUP BY nct_id
    ORDER BY total_matched_patients DESC
    LIMIT 20
""", conn)
print("=== MOST FREQUENTLY MATCHED TRIALS ===")
print(df_top_trials.to_string(index=False))
print("\n")


# Query: Patient demographics vs trial phase matched
df_demo_trials = pd.read_sql_query("""
    SELECT i.age / 10 * 10 as age_group,
           i.sex,
           tm.trial_phase,
           COUNT(*)               as match_count,
           ROUND(AVG(tm.match_score), 3) as avg_score,
           SUM(CASE WHEN tm.eligible = 'eligible' THEN 1 ELSE 0 END) as eligible_count
    FROM trial_matches tm
    JOIN inferences i ON tm.inference_id = i.id
    WHERE i.age IS NOT NULL
    GROUP BY age_group, i.sex, tm.trial_phase
    ORDER BY age_group, i.sex, tm.trial_phase
""", conn)
print("=== DEMOGRAPHICS VS TRIAL PHASE ===")
print(df_demo_trials.to_string(index=False))
print("\n")


#------------------------------------------------------------------------------


#====================
# Drift Metrics Table
#====================


#------------------------------------------------------------------------------


# Query: All drift metrics (raw)
df_drift_all = pd.read_sql_query("""
    SELECT * FROM drift_metrics
    ORDER BY timestamp DESC
""", conn)
print("=== DRIFT METRICS RAW ===")
print(df_drift_all.to_string(index=False))
print("\n")


# Query: Active drift alerts
df_drift_alerts = pd.read_sql_query("""
    SELECT timestamp, metric_category, metric_name,
           metric_value, baseline_mean, z_score, p_value, threshold, notes
    FROM drift_metrics
    WHERE alert = 1
    ORDER BY timestamp DESC
""", conn)
print("=== ACTIVE DRIFT ALERTS ===")
print(df_drift_alerts.to_string(index=False))
print("\n")


# Query: Worst z-scores across all metrics
df_drift_zscore = pd.read_sql_query("""
    SELECT metric_category, metric_name,
           ROUND(metric_value, 4)   as metric_value,
           ROUND(baseline_mean, 4)  as baseline_mean,
           ROUND(baseline_std, 4)   as baseline_std,
           ROUND(z_score, 2)        as z_score,
           ROUND(p_value, 4)        as p_value,
           alert
    FROM drift_metrics
    ORDER BY ABS(z_score) DESC
    LIMIT 10
""", conn)
print("=== TOP 10 WORST Z-SCORES ===")
print(df_drift_zscore.to_string(index=False))
print("\n")


# Query: Alert rate by category
df_alert_rate = pd.read_sql_query("""
    SELECT metric_category,
           COUNT(*)        as total_checks,
           SUM(alert)      as total_alerts,
           ROUND(100.0 * SUM(alert) / COUNT(*), 1) as alert_rate_pct
    FROM drift_metrics
    GROUP BY metric_category
    ORDER BY alert_rate_pct DESC
""", conn)
print("=== ALERT RATE BY CATEGORY ===")
print(df_alert_rate.to_string(index=False))
print("\n")


# Query: Per-metric summary
df_drift_summary = pd.read_sql_query("""
    SELECT metric_category, metric_name,
           COUNT(*)                      as run_count,
           ROUND(AVG(metric_value), 4)   as avg_value,
           ROUND(AVG(baseline_mean), 4)  as avg_baseline,
           ROUND(AVG(z_score), 2)        as avg_z_score,
           ROUND(MAX(ABS(z_score)), 2)   as max_abs_z_score,
           SUM(alert)                    as total_alerts
    FROM drift_metrics
    GROUP BY metric_category, metric_name
    ORDER BY total_alerts DESC, max_abs_z_score DESC
""", conn)
print("=== DRIFT SUMMARY PER METRIC ===")
print(df_drift_summary.to_string(index=False))
print("\n")


# Query: Latest drift run
df_latest_drift = pd.read_sql_query("""
    SELECT metric_category, metric_name,
           metric_value, baseline_mean, z_score, alert, notes
    FROM drift_metrics
    WHERE timestamp = (SELECT MAX(timestamp) FROM drift_metrics)
    ORDER BY ABS(z_score) DESC
""", conn)
print("=== LATEST DRIFT RUN ===")
print(df_latest_drift.to_string(index=False))
print("\n")


# Query: Drift trend over time
df_drift_trend = pd.read_sql_query("""
    SELECT timestamp, metric_category, metric_name,
           ROUND(metric_value, 4) as metric_value,
           ROUND(baseline_mean, 4) as baseline_mean,
           ROUND(z_score, 2) as z_score,
           alert
    FROM drift_metrics
    ORDER BY metric_name, timestamp ASC
""", conn)
print("=== DRIFT TREND OVER TIME ===")
print(df_drift_trend.to_string(index=False))
print("\n")


# Query: Baseline vs comparison window configurations used
df_windows = pd.read_sql_query("""
    SELECT baseline_window_days, comparison_window_days,
           COUNT(*)           as checks,
           SUM(alert)         as alerts,
           ROUND(AVG(ABS(z_score)), 2) as avg_abs_z_score
    FROM drift_metrics
    GROUP BY baseline_window_days, comparison_window_days
    ORDER BY baseline_window_days
""", conn)
print("=== WINDOW CONFIGURATIONS ===")
print(df_windows.to_string(index=False))
print("\n")


#------------------------------------------------------------------------------


# Degradation queries (item 11b)
#
# Everything below reads the columns that make a partly-degraded run visible.
# The convention across all of them: NULL means the stage never reported, and
# is counted separately from 0. A query that folds NULL into 0 re-creates the
# defect these columns exist to remove.


# Query: how often did the pipeline run on fewer retrieval channels than
# configured? retrieval_degraded already excludes channels the ablation
# switched off, so a 1 here is a real loss.
df_retrieval_degraded = pd.read_sql_query("""
    SELECT
        COUNT(*)                                                   AS rows_total,
        SUM(CASE WHEN retrieval_degraded IS NULL THEN 1 ELSE 0 END) AS not_reported,
        SUM(CASE WHEN retrieval_degraded = 1 THEN 1 ELSE 0 END)     AS degraded,
        ROUND(100.0 * SUM(CASE WHEN retrieval_degraded = 1 THEN 1 ELSE 0 END)
              / NULLIF(SUM(CASE WHEN retrieval_degraded IS NOT NULL
                                THEN 1 ELSE 0 END), 0), 2)         AS degraded_pct_of_reported,
        SUM(COALESCE(retrieval_trials_lost, 0))                    AS trials_lost_total
    FROM inferences
""", conn)
print("=== RETRIEVAL DEGRADATION ===")
print(df_retrieval_degraded.to_string(index=False))
print("\n")


# Query: which channels dropped out, and how. retrieval_channels is JSON, so
# the status is matched as a substring per channel name rather than parsed.
df_channel_status = pd.read_sql_query("""
    SELECT
        timestamp, patient_id,
        retrieval_channels_ok || '/' || retrieval_channels_expected AS channels_ok,
        retrieval_trials_lost,
        retrieval_channels
    FROM inferences
    WHERE retrieval_degraded = 1
    ORDER BY timestamp DESC
    LIMIT 25
""", conn)
print("=== MOST RECENT DEGRADED RETRIEVALS ===")
print(df_channel_status.to_string(index=False))
print("\n")


# Query: MeSH expansion fallback rate. The fallback searches on demographics
# plus the raw diagnosis display, with no MeSH vocabulary at all, and used to
# leave nothing behind but a printed WARNING.
df_expansion_path = pd.read_sql_query("""
    SELECT
        COALESCE(query_expansion_path, '(not reported)') AS query_expansion_path,
        COALESCE(mesh_resolution, '(none)')              AS mesh_resolution,
        COUNT(*)                                         AS n,
        ROUND(AVG(candidates_retrieved), 1)              AS avg_retrieved,
        ROUND(AVG(eligible_matches), 2)                  AS avg_eligible
    FROM inferences
    GROUP BY query_expansion_path, mesh_resolution
    ORDER BY n DESC
""", conn)
print("=== QUERY EXPANSION PATH x MESH RESOLUTION ===")
print(df_expansion_path.to_string(index=False))
print("\n")


# Query: how often was the judge told relevance was confirmed, and how often
# did the cancer site filter actually run? mesh_dropped = 0 cannot answer this
# on its own — it is the same value whether the filter checked and dropped
# nothing or never ran.
df_relevance_assertion = pd.read_sql_query("""
    SELECT
        CASE mesh_filter_applied
             WHEN 1 THEN 'filter ran (prompt asserts confirmed)'
             WHEN 0 THEN 'filter skipped (prompt says unconfirmed)'
             ELSE '(not reported)'
        END                                          AS relevance_assertion,
        COALESCE(mesh_filter_skip_reason, '(none)')  AS skip_reason,
        COUNT(*)                                     AS n,
        ROUND(AVG(mesh_dropped), 2)                  AS avg_mesh_dropped,
        ROUND(AVG(candidates_evaluated), 2)          AS avg_evaluated,
        ROUND(AVG(eligible_matches), 2)              AS avg_eligible
    FROM inferences
    GROUP BY mesh_filter_applied, mesh_filter_skip_reason
    ORDER BY n DESC
""", conn)
print("=== CANCER SITE FILTER: RAN vs ASSERTED ===")
print(df_relevance_assertion.to_string(index=False))
print("\n")


#------------------------------------------------------------------------------


# Close connection
conn.close()


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Feb 12 20:51:26 2026

@author: ramyalsaffar
"""

