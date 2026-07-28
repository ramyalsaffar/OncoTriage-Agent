# Query the database
####################


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
df_expansion = pd.read_sql_query("""
    SELECT 
        AVG(expansion_input_tokens) as avg_expansion_input,
        AVG(expansion_output_tokens) as avg_expansion_output,
        MAX(expansion_input_tokens) as max_expansion_input,
        MAX(expansion_output_tokens) as max_expansion_output,
        AVG(query_expansion_time) as avg_expansion_time,
        MAX(query_expansion_time) as max_expansion_time
    FROM inferences
    WHERE expansion_input_tokens > 0
""", conn)
print("=== EXPANSION MODEL STATS ===")
print(df_expansion.T)
print("\n")


# Query 10: Cost breakdown by model
# Warning: These must be manually updated when pricing changes
df_cost = pd.read_sql_query("""
    SELECT 
        SUM(expansion_input_tokens * 0.15 / 1000000) as total_expansion_input_cost,
        SUM(expansion_output_tokens * 0.60 / 1000000) as total_expansion_output_cost,
        SUM(gpt4o_input_tokens * 2.50 / 1000000) as total_gpt4o_input_cost,
        SUM(gpt4o_output_tokens * 10.00 / 1000000) as total_gpt4o_output_cost,
        SUM(estimated_cost_usd) as total_cost,
        AVG(estimated_cost_usd) as avg_cost_per_patient,
        COUNT(*) as total_patients
    FROM inferences
""", conn)
print("=== COST BREAKDOWN ===")
print(df_cost.T)
print(f"\nProjected cost for 1000 patients: ${df_cost.iloc[0]['avg_cost_per_patient'] * 1000:.2f}")
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
df_retrieval = pd.read_sql_query("""
    SELECT 
        AVG(bm25_retrieved) as avg_bm25,
        AVG(vector_retrieved) as avg_vector,
        AVG(candidates_retrieved) as avg_total_after_fusion,
        AVG(CAST(candidates_retrieved AS FLOAT) / (bm25_retrieved + vector_retrieved)) as fusion_efficiency
    FROM inferences
    WHERE bm25_retrieved > 0 AND vector_retrieved > 0
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


# Close connection
conn.close()


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Feb 12 20:51:26 2026

@author: ramyalsaffar
"""

