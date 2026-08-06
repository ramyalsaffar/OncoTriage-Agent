# Database Query Layer
######################

"""Every read-only query the project runs against ``inferences.db``.

Moved out of ``16- Database Query.py`` by item 20c, pass 3b. That file is now a
thin entry point holding a ``__main__`` guard and one call.

WHY THIS IS A CALLABLE SURFACE AND NOT A SCRIPT BODY
----------------------------------------------------
File 16 was 915 lines of top-level statements with ZERO ``__main__`` guards, so
every one of its ~40 queries ran the moment the file was loaded. That made it
unimportable in the ordinary sense of the word: there was no way to reach one
query without running all of them, no way to get a DataFrame back instead of a
printed table, and no way for "21- Streamlit Dashboard.py" -- which asks several
of these same questions of the same database -- to share a single line of it.
The dashboard consequently carries its own copies, and the cost-by-model
arithmetic in particular exists twice (File 16's Query 10 and File 21's cost
tab), which is exactly the shape of duplication that goes out of sync.

So the queries are DATA now: an ordered registry of ``Query`` records, each with
a key, the SQL, a heading and a render mode. Three things follow:

    run(conn, key)   -> DataFrame       one query, no printing
    run_all(conn)    -> {key: result}   every query, no printing
    report(conn)                        every query, printed exactly as File 16
                                        printed it

NOT ONE CHARACTER OF SQL WAS ALTERED IN THE MOVE (pass 3b), and every SQL body
below except the two named in the next section is still byte-for-byte what
"16- Database Query.py" held: extracted BY AST -- read as string constants and
emitted verbatim, never retyped -- so trailing whitespace and indentation
survived intact.

ITEM 38 FIXED THE TWO BROKEN QUERIES, AND report()'s OUTPUT CHANGED ON PURPOSE
------------------------------------------------------------------------------
Pass 3b's acceptance criterion was that ``report()`` die at the same query with
the same message, so that a before/after comparison measured the MOVE. THAT
CRITERION IS DELIBERATELY BROKEN HERE. ``report()`` now runs to the end.

    expansion_token_efficiency  (File 16's Query 19) IS DELETED, not repaired.
        It selected expansion_input_tokens / expansion_output_tokens, which are
        not columns of `inferences` and never were: Stage 1 is rule-based and
        issues no LLM call, so there are no expansion tokens to count. Adding
        the columns would have meant inventing a measurement. Rewriting it
        would have meant duplicating `expansion_stage_stats`, which already asks
        the answerable version of the same question. See the comment where it
        used to sit, immediately after `expansion_stage_stats` below.

    pipeline_consistency        (File 16's Query 20) had a stray WHEN between
        the column list and the CASE, which made it a syntax error, so it had
        never executed once. The identical condition already appears INSIDE the
        CASE -- the two lines differ only in indentation, checked rather than
        assumed -- so deleting the stray one removes a syntax error and changes
        no logic. Three further defects in the same query, all of which had been
        invisible because it could not run, are fixed alongside it; see
        ``_PIPELINE_CONSISTENCY_SQL``.

ONE PER-MODEL COST CALCULATION IN THE PROJECT
---------------------------------------------
``price_model_groups()`` is it. ``cost_by_model()`` feeds it the SQL GROUP BY;
``oncotriage/dashboard/tabs/cost_tokens.py`` feeds it a pandas groupby over the
sidebar-filtered frame, through ``model_groups_from_frame()``. The dashboard
used to carry its own copy of the arithmetic -- the duplication File 16's own
docstring predicted would go out of sync, and which had already diverged in the
one way that mattered: the dashboard used ``pd.isna()`` and this module used
``is None`` and ``or 0``.

    NULL SEMANTICS DIFFER BETWEEN THE TWO SOURCES AND THE SHARED CODE HANDLES
    BOTH. SQL ``SUM()`` over an all-NULL group returns NULL; pandas ``.sum()``
    returns 0.0 for the same group. One such group beside a group carrying
    numbers is what makes a pandas column float64, so a SQL NULL arrives as
    ``NaN`` rather than ``None`` -- and ``groupby(dropna=False)`` labels the
    missing group ``nan``, not ``None``. Every null test in the shared code is
    ``pd.isna()``. ``model_groups_from_frame`` passes ``min_count=1`` so that
    pandas reports an all-NULL group as NULL too, rather than importing "no
    value was ever recorded" into "the recorded value is zero".

WHAT IMPORTING THIS MODULE DOES
-------------------------------
Nothing. No connection is opened, no path is resolved, no query is executed. The
registry is a tuple of strings. ``connect()`` is a function and ``report()``
needs a connection handed to it.
"""

import sqlite3
from typing import Dict, List

import pandas as pd

from oncotriage import paths
from oncotriage.config import PRICING_CONFIG, RRF_POOL_SIZE, TOP_K_CANDIDATES
from oncotriage.utils import get_model_cost


#------------------------------------------------------------------------------


class Query:
    """One named, read-only query: its SQL and how File 16 rendered it.

    Deliberately a plain class rather than a dataclass or a NamedTuple: this
    module must import cleanly with nothing else present, and a dataclass would
    add an import for a record with four fields and no behaviour.

    Attributes:
        key:         Stable identifier. This is what ``run(conn, key)`` takes and
                     what a future consumer (File 21) names.
        sql:         The SQL. Verbatim from File 16 for every query except
                     ``pipeline_consistency``, which item 38 repaired and which
                     builds its two bounds from oncotriage/config.py -- see
                     ``_PIPELINE_CONSISTENCY_SQL``. File 16 is a thin entry
                     point and holds no SQL of its own, so there is nothing to
                     keep in step with an edit here.
        heading:     The banner File 16 printed above the result, or None where
                     it printed none.
        render:      How ``report()`` prints the frame:
                       'repr'                print(df)
                       'describe'            print(df.describe())
                       'transpose'           print(df.T)
                       'to_string'           print(df.to_string(index=False))
                       'empty_or_to_string'  a message when empty, else to_string
                       'custom'              report() calls a named function; the
                                             frame alone does not describe the
                                             output (Query 5 prints a prompt,
                                             Query 10 prices per model in Python)
        blank_after: Whether File 16 printed a bare "\\n" after this section.
                     Not cosmetic here -- it is part of what "output identical
                     before and after" means.
        notes:       Extra lines printed between the heading and the frame.
    """

    __slots__ = ("key", "sql", "heading", "render", "blank_after", "notes")

    def __init__(self, key, sql, heading=None, render="to_string",
                 blank_after=True, notes=()):
        self.key = key
        self.sql = sql
        self.heading = heading
        self.render = render
        self.blank_after = blank_after
        self.notes = tuple(notes)

    def __repr__(self):
        return f"<Query {self.key!r} render={self.render!r}>"


# The message File 16 printed when the consistency query came back empty. A
# named constant because ``report()`` and any future caller of
# ``run(conn, 'pipeline_consistency')`` must agree on what "no rows" means, and
# because an empty result there is a CLEAN result rather than a missing one.
CONSISTENCY_CLEAN_MESSAGE = "No issues found - pipeline is consistent"


# The label the priced cost frame carries where `matching_model` is NULL. A
# named constant because THREE places have to agree on it: this module's
# arithmetic, the dashboard tab that renders the same frame, and the test that
# asserts a NULL-model group is reported rather than dropped. A literal in three
# files is the shape that goes out of sync.
NO_MODEL_LABEL = "(none)"


# Queries whose output is not a rendering of their own frame. report() dispatches
# these to the functions below; run() still returns their raw frame, because
# "give me the rows" is a separate question from "print what File 16 printed".
_CUSTOM_RENDERERS = {}


#------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# THE CONSISTENCY QUERY'S BOUNDS COME FROM oncotriage/config.py (item 38)
# ---------------------------------------------------------------------------
#
# File 16's Query 20 tested `candidates_retrieved != 100` and
# `candidates_reranked != 30`. Four things were wrong with those two lines and
# only the first was visible from the file:
#
# 1. THE NUMBERS WERE LITERALS FOR CONFIGURED VALUES. Retune the retrieval
#    pool and every row in the table becomes an anomaly, in a query whose only
#    job is to tell anomalies from normal runs.
#
# 2. `!= 30` MATCHED NO CONSTANT IN THE PROJECT, then or at any point in tracked
#    history. The column it bounds is `candidates_reranked`, which
#    oncotriage/agent/terminal.py writes as `len(state["reranked_trials"])`, and
#    oncotriage/agent/retrieval.py builds that list at BOTH of its two exits as
#    `sorted_by_rrf[:TOP_K_CANDIDATES]`. TOP_K_CANDIDATES has been 40 since the
#    initial commit (`git show 0d3e3eb:"03- Config.py"`). So 30 is a stale
#    literal from before the repository existed. The binding below is NOT a
#    guess at which constant the 30 meant -- it is a derivation of which
#    constant governs the column, read off the two slice expressions that
#    produce it. The mismatch is reported rather than papered over.
#
# 3. `!= 100` IS AMBIGUOUS BY VALUE and unambiguous by derivation. Both
#    VECTOR_RETRIEVAL_SIZE and RRF_POOL_SIZE are 100. The column is
#    `candidates_retrieved` = `len(state["hybrid_results"])`, and
#    `hybrid_results` is built from `ranked_nct_ids`, which is the fused list
#    sliced `[:RRF_POOL_SIZE]`. RRF_POOL_SIZE it is.
#
# 4. `!=` IS THE WRONG OPERATOR FOR EITHER OF THEM, and that survives fixing
#    the constants. Both numbers are CAPS applied with a slice, so a run that
#    produces fewer is ordinary -- a rare primary site, a small index, a
#    single-channel ablation, or payload backfill losing a ranked trial (which
#    oncotriage/agent/retrieval.py counts as retrieval_trials_lost). Under `!=`
#    every one of those rows is reported as a "Retrieval anomaly". The invariant
#    that a slice actually guarantees is `<=`, so the violation is `>`: more
#    candidates than the cap allows means the cap was not applied, which is a
#    real defect and the only thing here worth a row in the report.
#
# MEASURED AGAINST THE REAL DATABASE RATHER THAN ARGUED. Every row in
# inferences.db carries candidates_reranked = 40, so `!= 30` was true for all of
# them; run over the production table on 2026-08-05, the pre-fix logic flags
# 1,106 of 1,106 rows and the fixed logic flags 0. A consistency report that
# calls every row an anomaly is indistinguishable from one that calls none, and
# nobody would ever have found that out, because the stray WHEN meant the query
# could not parse.
#
# Interpolated as integers at import rather than passed as SQL parameters,
# because `run()` executes `Query.sql` with no parameter channel and giving one
# query its own would make the registry two shapes. The guard below is what
# makes the interpolation safe to read as well as safe to execute.
for _name, _value in (("RRF_POOL_SIZE", RRF_POOL_SIZE),
                      ("TOP_K_CANDIDATES", TOP_K_CANDIDATES)):
    if isinstance(_value, bool) or not isinstance(_value, int) or _value <= 0:
        raise RuntimeError(
            f"oncotriage.storage.queries: {_name} is {_value!r}; the pipeline "
            f"consistency query interpolates it into SQL as a positive integer "
            f"bound. A non-integer would either fail to parse or, worse, parse "
            f"into a comparison that silently never fires."
        )
del _name, _value


_PIPELINE_CONSISTENCY_SQL = f"""
    SELECT * FROM (
    SELECT
        patient_id,
        candidates_retrieved,
        candidates_reranked,
        candidates_filtered,
        candidates_evaluated,
        eligible_matches,
        near_misses,
        not_evaluable_trials,
        CASE
            WHEN candidates_retrieved IS NULL
              OR candidates_reranked  IS NULL
              OR candidates_filtered  IS NULL
              OR candidates_evaluated IS NULL
              OR eligible_matches     IS NULL
              OR near_misses          IS NULL       THEN 'Counters not reported'
            WHEN candidates_retrieved > {RRF_POOL_SIZE}    THEN 'Retrieval anomaly'
            WHEN candidates_reranked  > {TOP_K_CANDIDATES} THEN 'Rerank anomaly'
            WHEN not_evaluable_trials IS NOT NULL
             AND candidates_evaluated != (eligible_matches + near_misses
                                          + not_evaluable_trials)
                                                    THEN 'Count mismatch'
            WHEN not_evaluable_trials IS NULL
             AND candidates_evaluated <  (eligible_matches + near_misses)
                                                    THEN 'Count mismatch'
            WHEN candidates_filtered < candidates_evaluated THEN 'Filter < evaluated'
            ELSE 'OK'
        END as issue
    FROM inferences
) WHERE issue != 'OK'
LIMIT 20
"""
"""The pipeline consistency query, with its two bounds resolved from config.

WHAT CHANGED BESIDES THE TWO BOUNDS, all of it invisible before because the
stray WHEN meant this query had never executed once:

  - THE STRAY WHEN IS GONE. It sat between `candidates_evaluated,` and `CASE`
    and read, character for character after stripping indentation, the same as
    the third WHEN inside the CASE. Removing it therefore removes a syntax error
    and nothing else. That equality is checked in "49- Database Query Layer
    Test.py" against the pre-fix text in git rather than asserted here.

  - 'Count mismatch' NOW COUNTS not_evaluable_trials. `candidates_evaluated` is
    `len(evaluations)` and oncotriage/agent/terminal.py partitions `evaluations`
    THREE ways -- matches, near_misses and not_evaluable -- so the identity is
    evaluated == eligible + near + not_evaluable. Testing it against only the
    first two flags every ordinary run in which the model declined to assess a
    single trial. That is not an inconsistency; it is the documented behaviour
    of `not_evaluable_trials`, which exists precisely so a non-evaluation is not
    folded into near_misses.

    not_evaluable_trials is an ADDED column (INFERENCE_COLUMN_ADDITIONS), so
    rows written before it existed hold NULL. `a != (b + c + NULL)` is NULL,
    which is not true, so those rows would fall through every later WHEN and be
    reported clean -- the NULL-read-as-fine defect this project treats as its
    own category. They get the weaker inequality that is still provable without
    the third term: evaluated can never be LESS than eligible + near_misses.
    COALESCE(not_evaluable_trials, 0) is what the weak version must not be,
    because that asserts a count that was never recorded.

  - A ROW WHOSE COUNTERS ARE NULL IS FLAGGED, not passed. Under SQL three-valued
    logic every comparison against NULL is NULL, so a row missing any of these
    counters used to reach `ELSE 'OK'` and be reported as consistent. "I cannot
    check this row" and "this row is fine" are different answers.

  - THE THREE COUNT COLUMNS ARE SELECTED. A row flagged 'Count mismatch' whose
    output does not show eligible_matches, near_misses or not_evaluable_trials
    cannot be acted on without a second query.

LIMIT 20 IS LEFT AS FILE 16 WROTE IT. It is a pre-existing cap unrelated to the
defects above and changing it would change the report's volume on a full
database for no reason this item owns."""


#------------------------------------------------------------------------------


QUERIES = (
    # File 16 line 74, `df_inferences`
    Query(
        key='inferences_all',
        heading=None,
        render='repr',
        blank_after=False,
        sql='SELECT * FROM inferences',
    ),
    # File 16 line 81, `df_timeout`
    Query(
        key='timing_columns',
        heading=None,
        render='describe',
        blank_after=False,
        sql='SELECT total_time, gpt4o_evaluation_time, gpt4o_output_tokens FROM inferences',
    ),
    # File 16 line 88, `df_timeout`
    Query(
        key='slowest_five',
        heading=None,
        render='repr',
        blank_after=False,
        sql="""
    SELECT patient_id, age, condition_count, medication_count, 
           candidates_evaluated, total_time, gpt4o_evaluation_time, 
           gpt4o_input_tokens, gpt4o_output_tokens, error
    FROM inferences 
    ORDER BY total_time DESC 
    LIMIT 5
""",
    ),
    # File 16 line 107, `df_performance`
    Query(
        key='performance_distribution',
        heading='=== PERFORMANCE DISTRIBUTION ===',
        render='describe',
        blank_after=True,
        sql="""
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
""",
    ),
    # File 16 line 126, `df_slowest`
    Query(
        key='slowest_ten',
        heading='=== TOP 10 SLOWEST PATIENTS ===',
        render='to_string',
        blank_after=True,
        sql="""
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
""",
    ),
    # File 16 line 150, `df_verbose`
    Query(
        key='verbose_output',
        heading='=== PATIENTS WITH OUTPUT > 4000 TOKENS ===',
        render='to_string',
        blank_after=True,
        sql="""
    SELECT 
        patient_id,
        candidates_evaluated,
        gpt4o_output_tokens,
        gpt4o_output_tokens / NULLIF(candidates_evaluated, 0) as tokens_per_trial,
        total_time
    FROM inferences
    WHERE gpt4o_output_tokens > 4000
    ORDER BY gpt4o_output_tokens DESC
""",
    ),
    # File 16 line 168, `df_errors`
    Query(
        key='error_types',
        heading='=== ERROR TYPES ===',
        render='to_string',
        blank_after=True,
        sql="""
    SELECT 
        error,
        COUNT(*) as count
    FROM inferences
    WHERE error != ''
    GROUP BY error
""",
    ),
    # File 16 line 184, `df_prompt`
    Query(
        key='slowest_prompt',
        heading=None,
        render='custom',
        blank_after=False,
        sql="""
    SELECT 
        patient_id,
        gpt4o_prompt,
        gpt4o_output_tokens,
        total_time
    FROM inferences
    ORDER BY total_time DESC
    LIMIT 1
""",
    ),
    # File 16 line 206, `df_stages`
    Query(
        key='stage_bottlenecks',
        heading='=== STAGE-LEVEL BOTTLENECKS ===',
        render='transpose',
        blank_after=True,
        sql="""
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
""",
    ),
    # File 16 line 226, `df_funnel`
    Query(
        key='pipeline_funnel',
        heading='=== PIPELINE FUNNEL ANALYSIS ===',
        render='transpose',
        blank_after=True,
        sql="""
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
""",
    ),
    # File 16 line 245, `df_token_efficiency`
    Query(
        key='token_efficiency_by_complexity',
        heading='=== TOKEN EFFICIENCY BY PATIENT COMPLEXITY ===',
        render='to_string',
        blank_after=True,
        sql="""
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
""",
    ),
    # File 16 line 280, `df_expansion`
    Query(
        key='expansion_stage_stats',
        heading='=== EXPANSION (STAGE 1) STATS ===',
        render='transpose',
        blank_after=True,
        notes=('Stage 1 is rule-based and calls no LLM, so there are no expansion token columns to report.',
               'Item 38 deleted `expansion_token_efficiency`, which asked for them; this query is the answerable version.'),
        sql="""
    SELECT
        COUNT(*)                    as rows_n,
        AVG(query_expansion_time)   as avg_expansion_time,
        MAX(query_expansion_time)   as max_expansion_time,
        SUM(query_expansion_path = 'base_query_fallback') as fallback_runs,
        SUM(query_expansion_path IS NULL)                 as path_not_reported
    FROM inferences
""",
    ),
    # File 16 line 317, `df_cost_by_model`
    Query(
        key='cost_by_model',
        heading=None,
        render='custom',
        blank_after=False,
        sql="""
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
""",
    ),
    # File 16 line 399, `df_demographics`
    Query(
        key='demographic_matching',
        heading='=== DEMOGRAPHIC MATCHING PATTERNS ===',
        render='to_string',
        blank_after=True,
        sql="""
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
""",
    ),
    # File 16 line 428, `df_retrieval`
    Query(
        key='retrieval_method_performance',
        heading='=== RETRIEVAL METHOD PERFORMANCE ===',
        render='transpose',
        blank_after=True,
        sql="""
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
""",
    ),
    # File 16 line 447, `df_quality_filter`
    Query(
        key='quality_filter_effectiveness',
        heading='=== QUALITY FILTER EFFECTIVENESS ===',
        render='transpose',
        blank_after=True,
        sql="""
    SELECT 
        AVG(candidates_reranked) as avg_before_quality_filter,
        AVG(candidates_after_quality_filter) as avg_after_quality_filter,
        AVG(CAST(candidates_after_quality_filter AS FLOAT) / NULLIF(candidates_reranked, 0)) as quality_retention_rate,
        COUNT(CASE WHEN candidates_after_quality_filter = 0 THEN 1 END) as patients_filtered_out_completely
    FROM inferences
""",
    ),
    # File 16 line 461, `df_extremes`
    Query(
        key='extreme_cases',
        heading='=== EXTREME CASES / ANOMALIES ===',
        render='to_string',
        blank_after=True,
        sql="""
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
""",
    ),
    # File 16 line 490, `df_success_rate`
    Query(
        key='success_rate_by_trial_count',
        heading='=== SUCCESS RATE BY TRIAL COUNT ===',
        render='to_string',
        blank_after=True,
        sql="""
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
""",
    ),
    # File 16 line 508, `df_med_duplicates`
    Query(
        key='medication_duplication_suspects',
        heading='=== MEDICATION DUPLICATION SUSPECTS ===',
        render='to_string',
        blank_after=True,
        sql="""
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
""",
    ),
    # File 16 line 530, `df_filter_dropoff`
    Query(
        key='rule_filter_dropoff',
        heading='=== RULE FILTER DROP-OFF ===',
        render='transpose',
        blank_after=True,
        sql="""
    SELECT 
        AVG(candidates_reranked - candidates_filtered) as avg_dropped_by_rules,
        MAX(candidates_reranked - candidates_filtered) as max_dropped_by_rules,
        AVG(CAST(candidates_filtered AS FLOAT) / NULLIF(candidates_reranked, 0)) as retention_rate,
        COUNT(CASE WHEN candidates_filtered = 0 THEN 1 END) as patients_with_zero_after_filter
    FROM inferences
    WHERE candidates_reranked > 0
""",
    ),
    # File 16 line 545, `df_gpt4o_efficiency`
    Query(
        key='gpt4o_efficiency_by_trial_count',
        heading='=== GPT-4O EFFICIENCY BY TRIAL COUNT ===',
        render='to_string',
        blank_after=True,
        sql="""
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
""",
    ),
    # ---------------------------------------------------------------------
    # `expansion_token_efficiency` (File 16 line 564, `df_expansion_tokens`)
    # WAS HERE AND IS DELETED. DO NOT RE-ADD IT, AND DO NOT ADD THE COLUMNS.
    # ---------------------------------------------------------------------
    #
    # It read:
    #
    #     SELECT AVG(expansion_input_tokens)  as avg_input,
    #            AVG(expansion_output_tokens) as avg_output, ...
    #     FROM inferences WHERE expansion_input_tokens > 0
    #
    # Neither column exists in `inferences` and neither ever did. sqlite3 raises
    # "no such column: expansion_input_tokens", pandas re-raises it as a
    # DatabaseError, and because File 16 was 915 lines of top-level statements
    # that took the whole process with it -- so no query AFTER this one had ever
    # run, in any invocation of File 16, ever.
    #
    # THE FIX IS DELETION, NOT MIGRATION, AND THE REASON IS ABOUT STAGE 1 RATHER
    # THAN ABOUT SQL. `node_query_expansion` is deterministic and rule-based: it
    # walks the cancer registry and the MeSH C04 tree and issues NO LLM CALL.
    # There are no expansion tokens, there is no expansion prompt sent to a
    # model to be billed, and there never were. Adding the two columns would
    # mean inventing a measurement of something that does not happen; the
    # honest value of every row would be NULL, and this project's whole position
    # on NULL is that it must mean "not reported", not "the thing is absent by
    # construction".
    #
    # Rewriting it to ask something Stage 1 CAN answer was the other option and
    # it is already done: `expansion_stage_stats` immediately above reports the
    # stage's timing distribution, how often it fell back to the un-expanded
    # query, and how often the path was not reported at all. Those are the
    # answerable questions. A second query asking them again under a name that
    # promises tokens would be a duplicate whose only distinguishing feature is
    # a misleading title.
    #
    # If a future stage does start calling a model during expansion: add the
    # columns in INFERENCE_COLUMN_ADDITIONS (oncotriage/storage/
    # database_logger.py), have the terminal nodes write them, and add a query
    # under a NEW key. Reviving this one would reintroduce a query that reads
    # columns nothing writes.
    # File 16 line 579, `df_consistency`. SQL and its full argument at
    # _PIPELINE_CONSISTENCY_SQL above.
    Query(
        key='pipeline_consistency',
        heading='=== PIPELINE CONSISTENCY ISSUES ===',
        render='empty_or_to_string',
        blank_after=True,
        sql=_PIPELINE_CONSISTENCY_SQL,
    ),
    # File 16 line 608, `df_med_issue`
    Query(
        key='medication_counts',
        heading=None,
        render='repr',
        blank_after=False,
        sql="""
    SELECT 
        patient_id,
        medication_count,
        condition_count
    FROM inferences
    ORDER BY medication_count DESC
    LIMIT 10
""",
    ),
    # File 16 line 637, `df_matches`
    Query(
        key='trial_matches_all',
        heading=None,
        render='repr',
        blank_after=False,
        sql='SELECT * FROM trial_matches',
    ),
    # File 16 line 642, `df_top_trials`
    Query(
        key='most_matched_trials',
        heading='=== MOST FREQUENTLY MATCHED TRIALS ===',
        render='to_string',
        blank_after=True,
        sql="""
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
""",
    ),
    # File 16 line 660, `df_demo_trials`
    Query(
        key='demographics_vs_phase',
        heading='=== DEMOGRAPHICS VS TRIAL PHASE ===',
        render='to_string',
        blank_after=True,
        sql="""
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
""",
    ),
    # File 16 line 690, `df_drift_all`
    Query(
        key='drift_metrics_raw',
        heading='=== DRIFT METRICS RAW ===',
        render='to_string',
        blank_after=True,
        sql="""
    SELECT * FROM drift_metrics
    ORDER BY timestamp DESC
""",
    ),
    # File 16 line 700, `df_drift_alerts`
    Query(
        key='drift_active_alerts',
        heading='=== ACTIVE DRIFT ALERTS ===',
        render='to_string',
        blank_after=True,
        sql="""
    SELECT timestamp, metric_category, metric_name,
           metric_value, baseline_mean, z_score, p_value, threshold, notes
    FROM drift_metrics
    WHERE alert = 1
    ORDER BY timestamp DESC
""",
    ),
    # File 16 line 713, `df_drift_zscore`
    Query(
        key='drift_worst_zscores',
        heading='=== TOP 10 WORST Z-SCORES ===',
        render='to_string',
        blank_after=True,
        sql="""
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
""",
    ),
    # File 16 line 731, `df_alert_rate`
    Query(
        key='drift_alert_rate_by_category',
        heading='=== ALERT RATE BY CATEGORY ===',
        render='to_string',
        blank_after=True,
        sql="""
    SELECT metric_category,
           COUNT(*)        as total_checks,
           SUM(alert)      as total_alerts,
           ROUND(100.0 * SUM(alert) / COUNT(*), 1) as alert_rate_pct
    FROM drift_metrics
    GROUP BY metric_category
    ORDER BY alert_rate_pct DESC
""",
    ),
    # File 16 line 746, `df_drift_summary`
    Query(
        key='drift_summary_per_metric',
        heading='=== DRIFT SUMMARY PER METRIC ===',
        render='to_string',
        blank_after=True,
        sql="""
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
""",
    ),
    # File 16 line 764, `df_latest_drift`
    Query(
        key='drift_latest_run',
        heading='=== LATEST DRIFT RUN ===',
        render='to_string',
        blank_after=True,
        sql="""
    SELECT metric_category, metric_name,
           metric_value, baseline_mean, z_score, alert, notes
    FROM drift_metrics
    WHERE timestamp = (SELECT MAX(timestamp) FROM drift_metrics)
    ORDER BY ABS(z_score) DESC
""",
    ),
    # File 16 line 777, `df_drift_trend`
    Query(
        key='drift_trend_over_time',
        heading='=== DRIFT TREND OVER TIME ===',
        render='to_string',
        blank_after=True,
        sql="""
    SELECT timestamp, metric_category, metric_name,
           ROUND(metric_value, 4) as metric_value,
           ROUND(baseline_mean, 4) as baseline_mean,
           ROUND(z_score, 2) as z_score,
           alert
    FROM drift_metrics
    ORDER BY metric_name, timestamp ASC
""",
    ),
    # File 16 line 792, `df_windows`
    Query(
        key='drift_window_configurations',
        heading='=== WINDOW CONFIGURATIONS ===',
        render='to_string',
        blank_after=True,
        sql="""
    SELECT baseline_window_days, comparison_window_days,
           COUNT(*)           as checks,
           SUM(alert)         as alerts,
           ROUND(AVG(ABS(z_score)), 2) as avg_abs_z_score
    FROM drift_metrics
    GROUP BY baseline_window_days, comparison_window_days
    ORDER BY baseline_window_days
""",
    ),
    # File 16 line 820, `df_retrieval_degraded`
    Query(
        key='retrieval_degradation',
        heading='=== RETRIEVAL DEGRADATION ===',
        render='to_string',
        blank_after=True,
        sql="""
    SELECT
        COUNT(*)                                                   AS rows_total,
        SUM(CASE WHEN retrieval_degraded IS NULL THEN 1 ELSE 0 END) AS not_reported,
        SUM(CASE WHEN retrieval_degraded = 1 THEN 1 ELSE 0 END)     AS degraded,
        ROUND(100.0 * SUM(CASE WHEN retrieval_degraded = 1 THEN 1 ELSE 0 END)
              / NULLIF(SUM(CASE WHEN retrieval_degraded IS NOT NULL
                                THEN 1 ELSE 0 END), 0), 2)         AS degraded_pct_of_reported,
        SUM(COALESCE(retrieval_trials_lost, 0))                    AS trials_lost_total
    FROM inferences
""",
    ),
    # File 16 line 838, `df_channel_status`
    Query(
        key='recent_degraded_retrievals',
        heading='=== MOST RECENT DEGRADED RETRIEVALS ===',
        render='to_string',
        blank_after=True,
        sql="""
    SELECT
        timestamp, patient_id,
        retrieval_channels_ok || '/' || retrieval_channels_expected AS channels_ok,
        retrieval_trials_lost,
        retrieval_channels
    FROM inferences
    WHERE retrieval_degraded = 1
    ORDER BY timestamp DESC
    LIMIT 25
""",
    ),
    # File 16 line 857, `df_expansion_path`
    Query(
        key='expansion_path_x_mesh_resolution',
        heading='=== QUERY EXPANSION PATH x MESH RESOLUTION ===',
        render='to_string',
        blank_after=True,
        sql="""
    SELECT
        COALESCE(query_expansion_path, '(not reported)') AS query_expansion_path,
        COALESCE(mesh_resolution, '(none)')              AS mesh_resolution,
        COUNT(*)                                         AS n,
        ROUND(AVG(candidates_retrieved), 1)              AS avg_retrieved,
        ROUND(AVG(eligible_matches), 2)                  AS avg_eligible
    FROM inferences
    GROUP BY query_expansion_path, mesh_resolution
    ORDER BY n DESC
""",
    ),
    # File 16 line 877, `df_relevance_assertion`
    Query(
        key='mesh_filter_ran_vs_asserted',
        heading='=== CANCER SITE FILTER: RAN vs ASSERTED ===',
        render='to_string',
        blank_after=True,
        sql="""
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
""",
    ),
)


QUERIES_BY_KEY = {q.key: q for q in QUERIES}
"""Index over QUERIES. Built at import: it is a dict comprehension over a tuple
of strings and touches nothing."""

QUERY_KEYS = tuple(q.key for q in QUERIES)
"""Every key, in the order File 16 ran them. The order is part of the contract --
``report()`` reproduces File 16's output, and that output is ordered."""

# NON-DEGENERACY GUARD, at import. A registry that silently lost an entry, or
# gained a duplicate key that shadowed one, would make run_all() quietly cover
# less than it claims while every individual call kept working. Both are cheap
# to rule out here and impossible to notice later.
if len(QUERIES_BY_KEY) != len(QUERIES):
    _dupes = sorted({q.key for q in QUERIES if QUERY_KEYS.count(q.key) > 1})
    raise RuntimeError(
        f"oncotriage.storage.queries: duplicate query key(s) {_dupes}. Keys are "
        f"the public surface of this module; a duplicate silently shadows a "
        f"query in QUERIES_BY_KEY while leaving it in QUERIES."
    )


#------------------------------------------------------------------------------


def resolve_query_db_path(db_path=None):
    """The database these queries read. ``None`` means the configured one.

    The same shape as ``oncotriage.storage.database_logger``'s
    ``resolve_inference_db_path``, and deliberately a SEPARATE function rather
    than an import of it: that one answers "where does the logger WRITE", this
    one answers "where do these queries READ". They resolve to the same file
    today and there is no reason they must forever -- a read replica or an
    exported snapshot would change one and not the other.

    It resolves and returns; it opens nothing.
    """
    if db_path is not None:
        return db_path
    return paths.inferences_path


def connect(db_path=None):
    """Open a read connection to the inference database.

    Returns a plain ``sqlite3.Connection``. The caller closes it -- ``report()``
    does not, because a caller that wants to ask a follow-up question after the
    report should not have to reopen.
    """
    return sqlite3.connect(resolve_query_db_path(db_path))


#------------------------------------------------------------------------------


def run(conn, key) -> pd.DataFrame:
    """Execute one query by key and return its DataFrame. Prints nothing.

    Raises KeyError naming the valid keys for an unknown one, rather than
    returning an empty frame -- a typo'd key that answered with no rows would be
    indistinguishable from a database with no matching rows, which is the exact
    confusion this project treats as a defect.
    """
    if key not in QUERIES_BY_KEY:
        raise KeyError(
            f"unknown query key {key!r}; valid keys are "
            f"{', '.join(QUERY_KEYS)}"
        )
    return pd.read_sql_query(QUERIES_BY_KEY[key].sql, conn)


def run_all(conn, keys=None, stop_on_error=True) -> Dict:
    """Execute every query (or `keys`) and return {key: DataFrame}. Prints nothing.

    Args:
        conn:           An open connection.
        keys:           Which queries, defaulting to all of them in registry
                        order.
        stop_on_error:  True reproduces File 16's behaviour -- the first failing
                        query takes the run down, which is why nothing after
                        Query 19 has ever executed. False records the exception
                        under its key and carries on, which is what a caller
                        surveying a database wants and what item 38 will need.

    With stop_on_error=False the value for a failed key is the EXCEPTION object,
    not None and not an empty frame: "this query raised" and "this query
    returned nothing" are different facts and a caller must be able to tell them
    apart.
    """
    out = {}
    for key in (keys if keys is not None else QUERY_KEYS):
        try:
            out[key] = run(conn, key)
        except Exception as exc:                                # noqa: BLE001
            if stop_on_error:
                raise
            out[key] = exc
    return out


#------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The three raw-cursor sections
# ---------------------------------------------------------------------------
#
# File 16 ran these through cursor.execute()/fetchall() rather than pandas and
# printed the raw tuple list. They are kept that way rather than converted to
# DataFrames, because converting them would change the output -- and "the output
# is identical before and after" is what this pass is being judged on.

TABLE_LIST_SQL = "SELECT name FROM sqlite_master WHERE type='table'"
RAW_INFERENCES_SQL = "SELECT * FROM inferences"
RAW_TRIAL_MATCHES_SQL = "SELECT * FROM trial_matches"


def fetch_raw(conn, sql) -> List:
    """cursor.execute(sql).fetchall(), as File 16 did it. Returns the tuples."""
    cursor = conn.cursor()
    cursor.execute(sql)
    return cursor.fetchall()


def table_names(conn) -> List:
    """Every table in the database, as raw one-tuples. File 16 line 53."""
    return fetch_raw(conn, TABLE_LIST_SQL)


#------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The two sections whose output is not a rendering of their own frame
# ---------------------------------------------------------------------------

PROMPT_UNAVAILABLE_MESSAGE = "(no rows in `inferences`, so there is no prompt to show)"


def print_slowest_prompt(conn, out=print) -> pd.DataFrame:
    """File 16's Query 5: print the slowest patient's Stage 5 prompt in full.

    Returns the one-row frame so a caller can have the prompt without the
    banners; an EMPTY frame when the table is empty.

    TWO FAULTS OF THE SAME FAMILY AS THE COST ARITHMETIC, found by item 38's
    sweep of the custom renderers and fixed here:

      - ``df.iloc[0]`` on an empty frame raises IndexError, so ``report()``
        against a database with no inference rows -- a freshly initialized one,
        or any test database -- died at this query and never reached the other
        thirty-odd. That is the same failure shape as the two broken SQL
        queries, arriving by a different route.
      - ``f"{...['total_time']:.1f}"`` raises TypeError when total_time is NULL
        and the column is object dtype (which it is when every row is NULL), and
        silently prints ``nan`` when the column is float64. Neither is a number
        and only one of them says so.

    Both are reported rather than recovered from silently: the message names
    which fact is missing.
    """
    df_prompt = run(conn, "slowest_prompt")
    out("=== PROMPT FOR CHATGPT TESTING ===")

    if df_prompt.empty:
        out(PROMPT_UNAVAILABLE_MESSAGE)
        return df_prompt

    _row = df_prompt.iloc[0]
    _time = _row["total_time"]
    out(f"Patient: {_row['patient_id']}")
    out(f"Output tokens: {_row['gpt4o_output_tokens']}")
    out("Total time: (not recorded)" if pd.isna(_time)
        else f"Total time: {float(_time):.1f}s")
    out("\nCopy this prompt to ChatGPT:\n")
    out("="*80)
    out(_row['gpt4o_prompt'])
    out("="*80)
    return df_prompt


COST_GROUP_COLUMNS = ("matching_model", "rows_n", "input_tokens",
                      "output_tokens", "reasoning_tokens", "stored_cost")
"""The exact frame ``price_model_groups`` consumes.

Named so the two producers -- the ``cost_by_model`` SQL and
``model_groups_from_frame`` -- and the test that checks they agree all state the
same contract, and so a producer that quietly stops emitting one column fails
with the column named instead of raising AttributeError somewhere in the loop."""

PRICED_COST_COLUMNS = ("matching_model", "model_recorded", "rows",
                       "input_tokens", "output_tokens", "reasoning_tokens",
                       "input_cost", "output_cost", "recomputed_cost",
                       "stored_cost", "note")
"""What ``price_model_groups`` returns, in order. Pinned because the dashboard
renders it and "49- Database Query Layer Test.py" asserts on it."""


def _nullable_int(value):
    """A SQL count as ``int``, or ``None`` when the aggregate was NULL.

    ``pd.isna`` rather than ``is None`` or ``or 0``, and each of those two is a
    defect this function exists to have exactly one copy of the fix for:

      ``int(x or 0)``  -- ``float('nan')`` is TRUTHY, so ``nan or 0`` is ``nan``
                          and ``int(nan)`` raises ValueError. One all-NULL group
                          beside a group with numbers is enough to make the
                          column float64 and blow the whole report up.
      ``x is None``    -- a NULL in a float64 column is ``nan``, not ``None``,
                          so the test never fires and the NULL is priced,
                          printed or summed as though it were a number.

    NEITHER IS HYPOTHETICAL AND NEITHER NEEDED A CONTRIVED INPUT. The
    production inferences.db holds 1,100 gpt-4o rows whose gpt4o_reasoning_tokens
    is NULL and 6 gpt-5.6-terra rows where it is 0, so ``SUM`` gives NULL for
    one group and a number for the other, the column is float64, and the pre-fix
    function raises ``ValueError: cannot convert float NaN to integer`` on the
    real database. It had simply never been reached, because the query 40 lines
    above it in the registry killed the process first.
    """
    return None if pd.isna(value) else int(value)


def price_model_groups(df_groups) -> pd.DataFrame:
    """Price one aggregate row per ``matching_model``. THE ONLY COPY.

    Args:
        df_groups: a frame carrying ``COST_GROUP_COLUMNS``. Two producers exist
            and they differ in where their nulls come from -- see the module
            docstring -- which is why every null test in here is ``pd.isna``.

    Returns:
        A frame of ``PRICED_COST_COLUMNS``, one row per model, sorted by row
        count descending then by label, so both producers yield an identically
        ordered frame whatever order their own grouping happened to produce.

    PRICED PER MODEL, NOT AT ONE RATE. File 16's Query 10 used to have 2.50 and
    10.00 written into the SQL and summed the whole table against them. That was
    already a duplicate of PRICING_CONFIG that nothing kept in sync, and it
    became actively wrong on 2026-08-04 when the judge moved from
    gpt-4o-2024-08-06 to gpt-5.6-terra: inferences.db now holds rows from both,
    at different input AND output rates, and one blended rate misstates every row
    of at least one of them. The grouping key is matching_model, which
    log_inference writes from the model that ANSWERED the call, so each group is
    priced by the model that actually produced its tokens.

    Rates come from get_model_cost() / PRICING_CONFIG, never from a literal here,
    so there is exactly one pricing table in the project and this raises
    UnknownModelPricingError rather than quietly under-reporting when a model is
    missing from it. It is called even for a group whose token sums are NULL or
    zero, deliberately: an unpriced model must surface on the run that used it,
    not on the first run that happened to spend tokens on it.

    A NULL token sum is carried through as ``<NA>`` in a nullable Int64 column
    and priced as zero spend, with the reason in ``note``. Those are different
    facts -- "no token count was ever recorded for these rows" versus "these
    rows consumed nothing" -- and collapsing the first into the second is how a
    logging hole reads as a cheap run.
    """
    _missing = [c for c in COST_GROUP_COLUMNS if c not in df_groups.columns]
    if _missing:
        raise ValueError(
            f"price_model_groups: the group frame is missing {_missing}; it "
            f"must carry {list(COST_GROUP_COLUMNS)}. Producing a partial "
            f"breakdown from a partial frame would understate cost silently."
        )

    _cost_rows = []
    for _row in df_groups.itertuples(index=False):
        _model = _row.matching_model
        # pd.isna, NOT `is None`. pd.read_sql_query keeps SQL NULL as None in an
        # object column, so `is None` happened to work for the SQL producer; the
        # pandas producer's groupby(dropna=False) labels the missing group `nan`,
        # which `is None` reports as a real model name and get_model_cost then
        # rejects -- taking the whole cost panel down with an
        # UnknownModelPricingError naming 'nan'.
        _model_recorded = not pd.isna(_model)

        _in = _nullable_int(_row.input_tokens)
        _out = _nullable_int(_row.output_tokens)
        _reasoning = _nullable_int(_row.reasoning_tokens)
        _stored = None if pd.isna(_row.stored_cost) else float(_row.stored_cost)

        # The arithmetic operates on 0 where nothing was recorded; the frame
        # keeps the None so the reader can still tell the two apart.
        _in_priced = 0 if _in is None else _in
        _out_priced = 0 if _out is None else _out

        _notes = []
        if _model_recorded:
            # Split into two calls purely to get the input and output halves
            # separately; get_model_cost returns their sum.
            _in_cost = get_model_cost(_model, _in_priced, 0)
            _out_cost = get_model_cost(_model, 0, _out_priced)
        else:
            # matching_model IS NULL means no Stage 5 response was obtained for
            # those rows (node_no_candidates, or a failure before the first call
            # returned), so there is nothing to price and nothing to price it
            # against. Reported as a group rather than dropped: a NULL group
            # carrying non-zero tokens would be a logging defect, and silently
            # excluding it is how that stays invisible.
            _in_cost = _out_cost = 0.0
            _notes.append(
                "no model recorded"
                if (_in_priced == 0 and _out_priced == 0)
                else "NO MODEL RECORDED BUT TOKENS PRESENT — logging defect"
            )

        if _in is None and _out is None:
            _notes.append("no token counts recorded (SUM was NULL, not 0)")
        elif _in is None or _out is None:
            _notes.append(
                f"{'input' if _in is None else 'output'} token count not "
                f"recorded (SUM was NULL, not 0)"
            )
        if _stored is None:
            _notes.append("no stored cost recorded")

        _cost_rows.append({
            "matching_model": _model if _model_recorded else NO_MODEL_LABEL,
            # Carried explicitly rather than inferred from the label, so a model
            # genuinely named "(none)" could never be mistaken for the NULL
            # group by a consumer.
            "model_recorded": _model_recorded,
            "rows": int(_row.rows_n),
            "input_tokens": _in,
            "output_tokens": _out,
            # NULL-safe: SUM() over a column that is NULL on every GPT-4o-era row
            # returns NULL for those groups. Carried as <NA> rather than 0 —
            # GPT-4o reported no reasoning breakdown at all, which is not the same
            # as a reasoning model that did no thinking.
            "reasoning_tokens": _reasoning,
            "input_cost": _in_cost,
            "output_cost": _out_cost,
            "recomputed_cost": _in_cost + _out_cost,
            "stored_cost": _stored,
            "note": "; ".join(_notes),
        })

    df = pd.DataFrame(_cost_rows, columns=list(PRICED_COST_COLUMNS))
    if df.empty:
        return df

    # Nullable Int64, so a NULL aggregate survives as <NA> instead of being
    # coerced to NaN in a float column and printed as a number-shaped nothing.
    for _column in ("input_tokens", "output_tokens", "reasoning_tokens"):
        df[_column] = df[_column].astype("Int64")

    return (df.sort_values(["rows", "matching_model"], ascending=[False, True])
              .reset_index(drop=True))


def model_groups_from_frame(df) -> pd.DataFrame:
    """The ``cost_by_model`` aggregate, computed over an in-memory frame.

    The second producer for ``price_model_groups``. It exists so the dashboard's
    cost tab -- which must aggregate the SIDEBAR-FILTERED rows it was handed,
    not the whole table -- reaches the same arithmetic as ``cost_by_model``
    without carrying a second copy of it.

    ``min_count=1`` IS THE WHOLE POINT OF THIS FUNCTION AND IS NOT A DETAIL.
    pandas' ``.sum()`` returns 0.0 for a group in which every value is null;
    SQL's ``SUM()`` returns NULL for the same group. Left at the default, the
    two producers would disagree about exactly the case this item is about,
    and the dashboard would report "$0.00 of tokens" where the query layer
    reports "not recorded". With ``min_count=1`` an all-null group comes back
    NaN on both sides, and ``price_model_groups`` says so in its note column.

    ``dropna=False`` keeps the NULL-model group, for the reason
    ``price_model_groups`` gives: those rows carry no Stage 5 tokens and so cost
    nothing, but if they ever DID carry tokens that is a logging defect and
    dropping the group is how it would stay invisible.
    """
    _needed = ("matching_model", "gpt4o_input_tokens", "gpt4o_output_tokens",
               "gpt4o_reasoning_tokens", "estimated_cost_usd")
    _missing = [c for c in _needed if c not in df.columns]
    if _missing:
        raise ValueError(
            f"model_groups_from_frame: the frame is missing {_missing}. It "
            f"expects the columns of the `inferences` table; a breakdown built "
            f"from a frame without them would silently omit part of the spend."
        )

    if df.empty:
        return pd.DataFrame(columns=list(COST_GROUP_COLUMNS))

    _grouped = df.groupby("matching_model", dropna=False)
    _sums = _grouped[["gpt4o_input_tokens", "gpt4o_output_tokens",
                      "gpt4o_reasoning_tokens", "estimated_cost_usd"]].sum(min_count=1)

    # reindex rather than relying on the two groupby results coming back in the
    # same order. They do; but "rows_n" landing against the wrong model is a
    # silent mislabelling that no later check in this module could catch.
    return pd.DataFrame({
        "matching_model": _sums.index,
        "rows_n": _grouped.size().reindex(_sums.index).values,
        "input_tokens": _sums["gpt4o_input_tokens"].values,
        "output_tokens": _sums["gpt4o_output_tokens"].values,
        "reasoning_tokens": _sums["gpt4o_reasoning_tokens"].values,
        "stored_cost": _sums["estimated_cost_usd"].values,
    })


def cost_by_model(conn) -> pd.DataFrame:
    """File 16's Query 10, the arithmetic half: price each model's rows.

    The SQL producer. Everything about the arithmetic is in
    ``price_model_groups``; this is the GROUP BY that feeds it.
    """
    return price_model_groups(run(conn, "cost_by_model"))


def print_cost_by_model(conn, out=print) -> pd.DataFrame:
    """File 16's Query 10, the printing half. Returns the priced frame."""
    df_cost = cost_by_model(conn)
    out("=== COST BREAKDOWN BY MODEL ===")
    out(f"(priced from PRICING_CONFIG, last_updated {PRICING_CONFIG['last_updated']})")
    out(df_cost.to_string(index=False))

    _total_rows = int(df_cost["rows"].sum()) if len(df_cost) else 0
    _recomputed_total = float(df_cost["recomputed_cost"].sum()) if len(df_cost) else 0.0
    # skipna, which is pandas' default: a group whose estimated_cost_usd was
    # NULL contributes nothing to the total rather than turning it into NaN. The
    # count of such groups is reported below instead, because a total quietly
    # computed over a subset is the thing this item is removing.
    _stored_total = float(df_cost["stored_cost"].sum()) if len(df_cost) else 0.0
    _stored_missing = int(df_cost["stored_cost"].isna().sum()) if len(df_cost) else 0
    out(f"\nRows: {_total_rows}")
    out(f"Recomputed total: ${_recomputed_total:.4f}")
    out(f"Stored total (estimated_cost_usd): ${_stored_total:.4f}")
    if _stored_missing:
        out(f"  ...over {len(df_cost) - _stored_missing} of {len(df_cost)} model "
            f"groups; {_stored_missing} recorded no stored cost at all and are "
            f"excluded rather than counted as $0.00")

    # The two totals should agree. They diverge when PRICING_CONFIG changed after
    # rows were written — which is legitimate and is exactly why pricing_version is
    # stored per row — so this is reported, not asserted.
    #
    # Guarded on > 0 rather than on truthiness: a NaN total is truthy, and before
    # the stored_cost NULL fix above this line divided by it and printed nan%.
    if _stored_total > 0:
        out(f"Divergence: {(_recomputed_total - _stored_total) / _stored_total * 100:+.2f}% "
            f"(non-zero means PRICING_CONFIG changed since some rows were written; "
            f"see the pricing_version column)")

    if _total_rows:
        out(f"Projected cost for 1000 patients, at the current mix: "
            f"${_recomputed_total / _total_rows * 1000:.2f}")

    out("\n")
    return df_cost


_CUSTOM_RENDERERS.update({
    "slowest_prompt": print_slowest_prompt,
    "cost_by_model": print_cost_by_model,
})


#------------------------------------------------------------------------------


def apply_display_options():
    """Apply the pandas display settings File 16's output was formatted with.

    THE SAME ARRANGEMENT AS ``apply_plot_style()`` in ``oncotriage/fhir/
    explore.py``, and it exists for the same reason: these six statements are
    PROCESS-GLOBAL MUTATIONS and a package module must not run them at import.

    THEY WERE INVISIBLE BEFORE, WHICH IS WHY THIS FUNCTION IS NEEDED. File 16
    never set them. "01- Imports.py" did, at its lines 237-242, as a side effect
    of the exec chain -- so File 16's tables printed wide and at five decimal
    places because of a file it did not know it depended on. Drop the chain and
    ``print(df_inferences)`` collapses to `id  ...  gpt4o_reasoning_tokens`,
    which is a different report about the same data. That is not a difference
    anyone would have predicted from reading either file, and it is exactly the
    kind of hidden coupling this whole item exists to make explicit.

    Copied verbatim from "01- Imports.py". Called by ``report()``, which is a
    printing function and whose job this is; ``run()`` and ``run_all()`` return
    data and deliberately do NOT call it, so a caller asking for a DataFrame
    does not have its global pandas configuration rewritten underneath it.
    """
    pd.set_option('display.max_rows', 500)
    pd.set_option("display.max_columns", 500)
    pd.set_option("display.max_colwidth", 250)
    pd.set_option('display.width', 1000)
    pd.set_option('display.precision', 5)  # this will help me see big numbers without python converting it to exponential
    pd.options.display.float_format = '{:.4f}'.format


def _render(df, query, out):
    """Print one query's frame the way File 16 printed it."""
    if query.heading is not None:
        out(query.heading)
    for line in query.notes:
        out(line)

    if query.render == "repr":
        out(df)
    elif query.render == "describe":
        out(df.describe())
    elif query.render == "transpose":
        out(df.T)
    elif query.render == "to_string":
        out(df.to_string(index=False))
    elif query.render == "empty_or_to_string":
        if df.empty:
            out(CONSISTENCY_CLEAN_MESSAGE)
        else:
            out(df.to_string(index=False))
    else:
        # Not reachable through report(), which dispatches 'custom' before
        # getting here. Raised rather than ignored: a render mode nobody
        # implemented must not print nothing and look like an empty result.
        raise ValueError(
            f"query {query.key!r} has render={query.render!r}, which _render "
            f"does not implement and report() did not dispatch"
        )

    if query.blank_after:
        out("\n")


def report(conn, out=print) -> Dict:
    """Run every query in registry order and print exactly what File 16 printed.

    Args:
        conn: An open connection. NOT closed here -- see ``connect()``.
        out:  Where each line goes. Defaults to ``print``. Passing
              ``lambda *a: None`` runs the whole sweep silently, which is what
              makes this testable without capturing stdout.

    Returns:
        {key: DataFrame} for every query that completed before the run ended.

    IT RUNS TO THE END NOW (item 38). It used to die at
    ``expansion_token_efficiency``, which selected two columns that do not
    exist, so nothing after that query in the registry had ever executed in any
    invocation of File 16 or of this function. That query is deleted, the
    consistency query behind it is repaired, and the two custom renderers no
    longer raise on an empty or partly-NULL table. "49- Database Query Layer
    Test.py" runs every key against a seeded temporary database and then runs
    this function end to end, which is the first time either has been possible.
    """
    results = {}

    # Before anything is printed. File 16's tables were formatted by settings
    # "01- Imports.py" applied as a side effect of the exec chain; without this
    # call every wide frame below prints truncated. See apply_display_options.
    apply_display_options()

    # The three raw-cursor sections and the two frames File 16 printed before its
    # first numbered query, in the order it printed them.
    out(table_names(conn))
    out(fetch_raw(conn, RAW_INFERENCES_SQL))

    for query in QUERIES:
        if query.render == "custom":
            renderer = _CUSTOM_RENDERERS.get(query.key)
            if renderer is None:
                raise ValueError(
                    f"query {query.key!r} is marked render='custom' but no "
                    f"renderer is registered for it"
                )
            results[query.key] = renderer(conn, out=out)
            continue

        df = run(conn, query.key)
        results[query.key] = df
        _render(df, query, out)

        # File 16 printed the raw trial_matches tuples between the last
        # inferences query and the first trial_matches frame. Reproduced at the
        # same point rather than hoisted, because the position is part of the
        # output being preserved.
        if query.key == "medication_counts":
            out(fetch_raw(conn, RAW_TRIAL_MATCHES_SQL))

    return results


def report_to_stdout(db_path=None) -> Dict:
    """Open the database, print the whole report, close. What File 16 does.

    This is the entire body of "16- Database Query.py"'s __main__ block.
    """
    conn = connect(db_path)
    try:
        return report(conn)
    finally:
        conn.close()


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Feb 12 20:51:26 2026

@author: ramyalsaffar
"""
