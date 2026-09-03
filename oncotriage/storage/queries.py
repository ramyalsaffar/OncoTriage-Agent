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

A CAPPED LISTING IS NOT A REPORT (the residual pass after item 38)
------------------------------------------------------------------
``pipeline_consistency`` shows at most ``CONSISTENCY_LISTING_LIMIT`` rows. Until
this pass it did so with NO ORDER BY and NO STATEMENT OF HOW MANY THERE WERE, so
twenty issues and twenty thousand printed identically and the twenty shown could
differ between two executions of the same query on the same data. On a fresh
full-corpus run that is the difference between a clean pipeline and a broken one,
rendered the same way.

``pipeline_consistency_totals`` counts by category over every row with no limit
and prints IMMEDIATELY ABOVE the listing; the listing gained
``ORDER BY issue, patient_id, id``. Both queries interpolate ONE
``_CONSISTENCY_CASE_SQL`` and ONE ``_CONSISTENCY_CLASSIFIED_SQL``, so they cannot
drift apart -- there is no second copy to forget. On a database with no issues
the companion returns nothing and ``render='skip_if_empty'`` prints nothing at
all, so a clean run still shows exactly the one clean message it always did.

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

AN UNKNOWN COST IS NOT A ZERO COST, AND ``cost_complete`` IS HOW YOU ASK. A group
whose token sums are NULL, or whose model is NULL while it carries tokens, prices
at $0.00 -- a real float, not a NULL -- so ``recomputed_cost`` sums cleanly and
under-reports by exactly the unpriceable spend, with nothing in the number to say
so. The reason was in a ``note`` column, and prose is not a field. Both consumers
of the priced frame -- ``print_cost_by_model`` and the dashboard's cost tab --
ask the boolean and qualify their totals with it. The priced VALUE is
deliberately unchanged: NaN would propagate into every aggregate and produce no
number at all, which is worse than a number that says it is partial.

A QUERY MAY DECLARE THE TABLES IT NEEDS (the run-reader pass)
--------------------------------------------------------------
``Query.requires`` names tables a database may legitimately not have. Two
queries use it -- ``run_summary`` and ``run_degradation_breakdown``, over the
``runs`` and ``run_metrics`` tables added by the run-identity and
health-persistence passes.

THIS IS THE ITEM-38 PROPERTY DEFENDED, NOT A CONVENIENCE. Those two tables are
ADDITIVE: ``initialize_database`` creates them, so they appear in a database the
first time a writer opens it after those passes, and the production
``inferences.db`` on this machine does NOT have them (measured -- it holds
drift_metrics, inferences, sqlite_sequence and trial_matches). A query naming an
absent table raises ``no such table``, and ``report()`` runs the registry with
the first raise taking the process down. So registering the first ``runs`` query
without this field would have made ``python "16- Database Query.py"`` die
partway through and every query after it stop executing -- which is precisely
what ``expansion_token_efficiency`` did for the life of File 16.

``report()`` asks ``unavailable(conn)`` ONCE, before anything runs, PRINTS which
queries it is skipping and which tables are absent, and skips them. ``run()``
raises ``MissingTableError`` rather than returning an empty frame, because "this
database cannot answer that yet" and "the answer is no rows" are different
findings and an empty frame is the second.

WHAT IMPORTING THIS MODULE DOES
-------------------------------
Nothing. No connection is opened, no path is resolved, no query is executed. The
registry is a tuple of strings. ``connect()`` is a function and ``report()``
needs a connection handed to it.
"""

import os
import re
import sqlite3
from typing import Dict, List

import pandas as pd

from oncotriage import paths
from oncotriage.config import PRICING_CONFIG, RRF_POOL_SIZE, TOP_K_CANDIDATES
from oncotriage.observability import console
# THE RUN-TABLE VOCABULARY IS THE WRITER'S AND IS IMPORTED, NEVER RETYPED.
#
# `run_metrics.category` and the two `meta` row names are values
# `oncotriage/storage/database_logger.py` WRITES; the two queries below select on
# them. Written out as string literals here they would be the CROSS_ENCODER_MODEL
# shape one layer down -- two copies of one fact, no error when they disagree,
# and the only symptom a health panel that reports every run as clean because
# `WHERE category = 'degredation'` matches nothing. Same argument as
# `_PIPELINE_CONSISTENCY_SQL` interpolating RRF_POOL_SIZE rather than 100.
#
# THE EDGE IS SAFE AND WAS CHECKED RATHER THAN ASSUMED: database_logger imports
# paths, config, utils and registries.primary_cancer and does NOT import this
# module, so there is no cycle; and importing it opens nothing, which
# tests/test_package_invariants.py section 2 already proves for every module in
# the package.
from oncotriage.storage.database_logger import (
    INFERENCE_COLUMN_ADDITIONS,
    RENAMED_INFERENCE_COLUMNS,
    RUN_COLUMN_ADDITIONS,
    RUN_METRIC_CATEGORY_DEGRADATION,
    RUN_METRIC_CATEGORY_META,
    RUN_METRIC_META_COUNTERS_NONZERO,
    RUN_METRIC_META_COUNTERS_REGISTERED,
    CAMPAIGN_RESUMABLE_STATUSES,
    RUN_FINGERPRINT_COLUMNS,
    RUN_RECORD_STATUS_RUNNING,
    RUN_RECORD_TERMINAL_STATUSES,
    TRIAL_MATCH_COLUMN_ADDITIONS,
)
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
                       'skip_if_empty'       nothing at all when empty -- not
                                             even the heading -- else to_string
                       'custom'              report() calls a named function; the
                                             frame alone does not describe the
                                             output (Query 5 prints a prompt,
                                             Query 10 prices per model in Python)
        blank_after: Whether File 16 printed a bare "\\n" after this section.
                     Not cosmetic here -- it is part of what "output identical
                     before and after" means.
        notes:       Extra lines printed between the heading and the frame.
        requires:    Table names this query's SQL names and which a database may
                     legitimately not have yet. Default ``()`` -- every query
                     written before the run-tracking pass reads `inferences`,
                     `trial_matches` or `drift_metrics`, all three of which
                     ``initialize_database`` has created for the whole life of
                     the project, so there is nothing for those to declare.

                     WHY THIS EXISTS AT ALL, AND IT IS NOT A STYLE CHOICE. The
                     `runs` and `run_metrics` tables are ADDITIVE: they appear in
                     a database the first time a writer opens it after the
                     run-identity pass, and the production `inferences.db` on
                     this machine does not have them (measured -- its tables are
                     drift_metrics, inferences, sqlite_sequence, trial_matches).
                     A query naming an absent table raises
                     ``no such table``, and ``report()`` runs its registry with
                     the first raise taking the process down. So without this
                     field, adding the first `runs` query would have made
                     ``python "16- Database Query.py"`` die partway and every
                     query registered after it stop executing -- REINSTATING,
                     exactly, the defect item 38 removed.

        requires_columns:
                     ``(table, column)`` pairs the query names and which a
                     database may legitimately not have. Default ``()``.

                     A SEPARATE FIELD FROM ``requires`` BECAUSE MOST MISSING
                     COLUMNS ARE NOT A REASON TO SKIP ANYTHING. The
                     ``INFERENCE_COLUMN_ADDITIONS`` case -- a column the writer
                     adds on open -- is handled by every existing query
                     projecting NULL and by readers that separate NULL from
                     zero, and declaring those here would skip whole queries
                     over a column the queries already read correctly as absent.
                     What belongs here is a column whose ABSENCE MAKES THE SQL
                     UNPARSEABLE: ``run_attribution_coverage`` joins ON
                     ``i.run_id``, so without that column it raises ``no such
                     column`` and ``report()`` dies at it.

                     IT IS NOT DERIVABLE FROM ``requires``. ``inferences.run_id``
                     and the two run tables are created by ONE
                     ``initialize_database`` call today, so "runs exists"
                     implies "run_id exists" -- in a database this project
                     wrote. That is a coupling, not an invariant, and resting a
                     guard on it would mean the one shape it cannot survive is
                     the one it was written for.
    """

    __slots__ = ("key", "sql", "heading", "render", "blank_after", "notes",
                 "requires", "requires_columns", "clean_message")

    def __init__(self, key, sql, heading=None, render="to_string",
                 blank_after=True, notes=(), requires=(), requires_columns=(),
                 clean_message=None):
        self.key = key
        self.sql = sql
        self.heading = heading
        self.render = render
        self.blank_after = blank_after
        self.notes = tuple(notes)
        self.requires = tuple(requires)
        self.requires_columns = tuple(tuple(pair) for pair in requires_columns)
        # What ``render='empty_or_to_string'`` prints INSTEAD of an empty frame.
        # ``None`` keeps CONSISTENCY_CLEAN_MESSAGE, which is what the one
        # pre-existing user of that mode has always printed, so no existing
        # query's output moves.
        #
        # IT EXISTS BECAUSE THE MODE WAS SINGLE-USE BY ACCIDENT. The message was
        # a module constant read directly by the renderer, so the second query
        # wanting "print the rows, or say plainly that there are none" would
        # have had to either announce that the PIPELINE is consistent -- a claim
        # about something else entirely -- or become a custom renderer. A
        # per-query message makes an existing mode general instead of adding a
        # third way to print a frame.
        self.clean_message = clean_message

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


CONSISTENCY_GUARD_CATEGORY = 'Counters not reported'
"""The category the NULL guard emits.

Named because "tests/test_storage_query_layer.py" locates the guard branch inside
``_CONSISTENCY_CASE_SQL`` by this string in order to derive which columns are
guarded and which are merely compared. A literal in the test would be a second
copy of a value the SQL owns."""

CONSISTENCY_OK_CATEGORY = 'OK'
"""The CASE's ELSE. Both consistency queries filter on ``issue != 'OK'``, and the
test derives the set of reportable categories by removing this one."""

CONSISTENCY_LISTING_LIMIT = 20
"""How many rows the LISTING shows. File 16 wrote ``LIMIT 20`` and the number is
unchanged; it is a named constant now so that the companion's note can state the
cap, and so a caller can rebuild an uncapped variant from the shipped SQL rather
than retyping it."""


# ---------------------------------------------------------------------------
# ONE CASE, TWO QUERIES (the residual weakness pass after item 38)
# ---------------------------------------------------------------------------
#
# WHAT WAS WRONG WITH ONE QUERY. `LIMIT 20` sat on the outer select, after
# `WHERE issue != 'OK'`, with no ORDER BY. Three consequences, and on a fresh
# 22,000-patient run they are the difference between a clean report and a broken
# pipeline PRINTED IDENTICALLY:
#
#   1. it caps the rows and says nothing about how many there were, so twenty
#      issues and twenty thousand issues render the same;
#   2. with no ORDER BY, SQLite may return a different twenty on each execution,
#      so a reader cannot re-run the report and see the same sample, and a
#      diff between two runs is meaningless;
#   3. a reader who acts on the twenty has no way to know whether that is the
#      whole story.
#
# The companion query below counts by category over EVERY row with no limit, and
# report() prints it IMMEDIATELY ABOVE the listing. The listing keeps its cap and
# gains a total order.
#
# THE TWO AGREE BY CONSTRUCTION, NOT BY REVIEW. There is exactly one CASE
# expression in this module and exactly one classification select; both queries
# interpolate them. Editing the categories, the bounds or the NULL handling in
# one and not the other is not a mistake that can be made, because there is no
# "other" to edit. File 49 fires the demonstration: it mutates a copy of
# _CONSISTENCY_CASE_SQL, rebuilds both, and shows both changed.
_CONSISTENCY_CASE_SQL = f"""        CASE
            WHEN candidates_retrieved IS NULL
              OR candidates_reranked  IS NULL
              OR candidates_filtered  IS NULL
              OR candidates_evaluated IS NULL
              OR eligible_matches     IS NULL
              OR near_misses          IS NULL       THEN '{CONSISTENCY_GUARD_CATEGORY}'
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
            ELSE '{CONSISTENCY_OK_CATEGORY}'
        END as issue"""
"""THE classification. Every rule about what counts as an inconsistency is here
and nowhere else.

ITS TEXT IS UNCHANGED BY THIS PASS -- categories, bounds and NULL handling are
what item 38 shipped, and File 49 pins the block by sha256 measured before the
refactor as well as by behaviour. What moved is only that it is now a named
constant two queries interpolate instead of a run of lines inside one of them.

THE NULL GUARD IS THE FIRST BRANCH AND IT IS NOT ARBITRARY WHICH COLUMNS IT
NAMES. The rule File 49 enforces, derived from this text rather than listed in
the test: every column this CASE COMPARES must either be in the guard, or have
its own explicit NULL-aware branch. `not_evaluable_trials` is deliberately
absent from the guard and has the second treatment, because it is an added
column that is legitimately NULL on pre-migration rows -- flagging those as
"counters not reported" would report a schema migration as a pipeline defect.
Any seventh counter added later must pick one of the two treatments; the check
fails if it picks neither."""


_CONSISTENCY_CLASSIFIED_SQL = f"""
    SELECT
        id,
        patient_id,
        candidates_retrieved,
        candidates_reranked,
        candidates_filtered,
        candidates_evaluated,
        eligible_matches,
        near_misses,
        not_evaluable_trials,
{_CONSISTENCY_CASE_SQL}
    FROM inferences
"""
"""Every row with its issue category attached. The shared body of both
consistency queries.

`id` IS SELECTED BECAUSE THE LISTING'S ORDER BY NEEDS A TOTAL ORDER AND
patient_id IS NOT UNIQUE. Measured on the production database rather than
assumed: 1,106 rows carry 1,004 distinct patient_ids, up to 2 rows apiece --
"19- FastAPI Server Batch Test.py" re-POSTs the same two bundles, and a
re-run of the batch runner over a resumed checkpoint does the same. Ordering by
patient_id alone therefore leaves ties, and SQLite is free to break them
differently on each execution, which is the non-determinism this pass exists to
remove. `id` is `inferences`' INTEGER PRIMARY KEY, so (issue, patient_id, id) is
total. It is also the value you need in order to look the row up, which is why
selecting it is not purely mechanical."""


_PIPELINE_CONSISTENCY_SQL = f"""
    SELECT * FROM ({_CONSISTENCY_CLASSIFIED_SQL}) WHERE issue != '{CONSISTENCY_OK_CATEGORY}'
ORDER BY issue, patient_id, id
LIMIT {CONSISTENCY_LISTING_LIMIT}
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

WHAT THE RESIDUAL PASS ADDED, AND NOTHING ELSE
----------------------------------------------
``ORDER BY issue, patient_id, id`` before the cap, and ``id`` in the column list
so that ordering is TOTAL -- see ``_CONSISTENCY_CLASSIFIED_SQL`` for why
patient_id alone is not, measured on the production table. The CASE is
byte-identical to what item 38 shipped and File 49 pins it by sha256.

THE CAP IS STILL ``CONSISTENCY_LISTING_LIMIT`` ROWS AND IS NO LONGER SILENT.
``pipeline_consistency_totals`` runs immediately before this query in the
registry, counts every row by category with no limit, and says in its notes that
what follows is a capped sample. When there are no issues at all that query
returns nothing and prints nothing, so a clean database still shows the clean
message and only the clean message."""


_PIPELINE_CONSISTENCY_SUMMARY_SQL = f"""
    SELECT issue, COUNT(*) as n
    FROM ({_CONSISTENCY_CLASSIFIED_SQL}) WHERE issue != '{CONSISTENCY_OK_CATEGORY}'
    GROUP BY issue
    ORDER BY n DESC, issue
"""
"""How many rows fall in each issue category, over EVERY row.

NO LIMIT, AND THAT IS THE ENTIRE POINT. The listing beside it answers "show me
some bad rows"; this answers "how bad is it", which is the question a capped
listing cannot answer and had been silently declining to.

``ORDER BY n DESC, issue`` is total, because ``issue`` is the GROUP BY key and is
therefore unique in the result. Its second term is not a tiebreaker of last
resort -- with two categories at the same count, `n DESC` alone would leave the
order to SQLite, and this frame is printed."""


#------------------------------------------------------------------------------


# ===========================================================================
# THE RUN TABLES (the run-reader pass)
# ===========================================================================
#
# `runs` and `run_metrics` were written by the run-identity and
# health-persistence passes and READ BY NOTHING. A table nobody reads rots: the
# writer keeps writing it, no consumer ever contradicts it, and the first person
# to look discovers that a column has meant something else since a pass nobody
# connected to it. These two queries are the reader.
#
# WHAT MAKES THIS HARDER THAN A JOIN, and it is the reason for the fragments
# below rather than two self-contained SQL bodies:
#
#   * A RUN WITH NO PATIENTS AND A RUN WITH NO DEGRADATIONS MUST STILL APPEAR.
#     Both are real states -- a campaign killed before its first patient, and a
#     campaign that ran clean -- and an INNER JOIN reports each as a run that
#     does not exist. Every join below is LEFT and driven from `runs`.
#
#   * "NO DEGRADATION ROWS" HAS TWO MEANINGS AND THEY ARE OPPOSITE. `totals()`
#     drops every zero counter, so a run that degraded in no way contributes no
#     `degradation` rows -- and so does a run whose flushing was never wired up,
#     and so does a run that died before its first flush. The `meta` row
#     ``counters_registered`` is what separates them, which is the entire reason
#     `flush_run_metrics` writes it. ``_RUN_HEALTH_CASE_SQL`` is that reading,
#     written ONCE.
#
#   * BOTH QUERIES NEED THE SAME READING. Two copies of a CASE that must agree
#     is the shape `pipeline_consistency` and `pipeline_consistency_totals`
#     already declined; they interpolate one `_CONSISTENCY_CASE_SQL` for exactly
#     this reason and these two follow it.
#
# THE SUBQUERIES ARE PRE-AGGREGATED AND THAT IS NOT COSMETIC. `runs` LEFT JOIN
# `inferences` LEFT JOIN `run_metrics` in one FROM clause multiplies: a run with
# 20 patients and 3 counters produces 60 rows, and SUM(value) over it reports
# twenty times the degradation events. Each child is grouped to one row per
# run_id BEFORE it is joined.

RUN_HEALTH_NEVER_FLUSHED = "no health record"
"""``health_record`` for a run with no ``meta`` row: nothing ever flushed for it.

Says nothing about whether the run degraded. It is the absence of a measurement,
and it is the state every run written before the health-persistence pass is in,
plus any run that died before its first patient completed."""

RUN_HEALTH_MEASURED_CLEAN = "measured clean"
"""``health_record`` for a run whose counters were consulted and none moved.

THE ROW THIS WHOLE MECHANISM EXISTS FOR. It is a MEASUREMENT of health, not the
absence of one, and rendering it identically to ``RUN_HEALTH_NEVER_FLUSHED``
would throw away the only distinction the `meta` row buys."""

RUN_HEALTH_DEGRADED = "degraded"
"""``health_record`` for a run with at least one non-zero counter."""

RUN_HEALTH_NO_COUNTER_LABEL = "(none moved)"
"""The ``counter`` cell for a run that contributed no ``degradation`` row.

A LABEL, NOT A NULL, and the distinction is the point of the breakdown query.
A NULL there is read by every consumer as "this cell was not filled in";
"(none moved)" is a statement. Which of the two states produced it -- clean, or
never flushed -- is in ``health_record`` on the same row."""

RUN_HEALTH_STATES = (RUN_HEALTH_NEVER_FLUSHED, RUN_HEALTH_MEASURED_CLEAN,
                     RUN_HEALTH_DEGRADED)
"""Every value ``health_record`` can take. CLOSED, on ``RUN_METRIC_CATEGORIES``'
footing: a consumer may branch on it exhaustively, and the dashboard's Run Health
tab does."""


RUN_FINALIZATION_FINALIZED = "finalized"
"""``finalization`` for a run whose ``finished_at`` is set."""

RUN_FINALIZATION_LIVE_OR_DIED = "RUNNING, no finished_at -- live or died"
"""``finalization`` for a RUNNING row with no ``finished_at``.

DELIBERATELY ONE STATE AND NOT TWO. From the database alone a live campaign and
a campaign whose process was killed are the same row -- neither has a
``finished_at`` and both say RUNNING -- and there is no pid, no heartbeat and no
lease to tell them apart. Reporting it as "crashed" would be an invention;
reporting it as "running" would hide every crash. It is named for the ambiguity
it actually is, and ``started_at`` beside it is what a reader uses: a RUNNING row
from three weeks ago is not live."""

RUN_FINALIZATION_NOT_STAMPED = "terminal status, no finished_at -- finalize did not land"
"""``finalization`` for a row that reached a terminal status with no timestamp.

Unambiguous, and it is a defect rather than an ambiguity: ``finalize_run_record``
writes ``status`` and ``finished_at`` in one UPDATE, so this shape means the row
was written by something other than that function."""

RUN_FINALIZATION_STATES = (RUN_FINALIZATION_FINALIZED,
                           RUN_FINALIZATION_LIVE_OR_DIED,
                           RUN_FINALIZATION_NOT_STAMPED)
"""Every value ``finalization`` can take. CLOSED, and the dashboard branches on
it exhaustively."""


# The `meta` rows, one row per run. MAX(CASE ...) rather than two correlated
# subqueries: one pass over the index, and a run with no meta rows contributes
# no row at all, which is what makes the LEFT JOIN's NULL mean "never flushed".
_RUN_HEALTH_META_SQL = f"""
        SELECT run_id,
               MAX(CASE WHEN name = '{RUN_METRIC_META_COUNTERS_REGISTERED}'
                        THEN value END) AS counters_registered,
               MAX(CASE WHEN name = '{RUN_METRIC_META_COUNTERS_NONZERO}'
                        THEN value END) AS counters_nonzero
        FROM run_metrics
        WHERE category = '{RUN_METRIC_CATEGORY_META}'
        GROUP BY run_id"""

# The `degradation` rows, one row per run. `counters_moved` is a COUNT of
# counters and `degradation_events` a SUM of their totals; they are different
# numbers -- four events on one counter and one event on each of four give the
# same SUM -- and the per-run panel renders both, the same distinction
# `criterion_remap_incidence` already draws between trials and events.
_RUN_HEALTH_DEGRADATION_SQL = f"""
        SELECT run_id,
               COUNT(*)   AS counters_moved,
               SUM(value) AS degradation_events
        FROM run_metrics
        WHERE category = '{RUN_METRIC_CATEGORY_DEGRADATION}'
        GROUP BY run_id"""

# The patient rollup. `run_id IS NOT NULL` is not a filter that could drop a
# run's rows -- a row with a NULL run_id belongs to no run by definition (the API
# writes one per request, and every row written before the run-identity pass has
# one) -- it is there so the grouping cannot produce a NULL key that a LEFT JOIN
# would then try to match against `runs.id`.
#
# `cost_usd` is a SUM over COALESCE(...,0) and `rows_with_no_cost` is beside it,
# on `cost_complete`'s footing: an unpriced row contributes a real 0.0, so the
# total under-reports by exactly the unpriced spend and carries nothing in the
# number to say so. The count is what says so.
_RUN_HEALTH_PATIENTS_SQL = """
        SELECT run_id,
               COUNT(*)                                          AS patients,
               SUM(CASE WHEN error IS NOT NULL AND error != ''
                        THEN 1 ELSE 0 END)                       AS errored,
               ROUND(SUM(COALESCE(estimated_cost_usd, 0)), 4)    AS cost_usd,
               SUM(CASE WHEN estimated_cost_usd IS NULL
                        THEN 1 ELSE 0 END)                       AS rows_with_no_cost,
               MIN(timestamp)                                    AS first_patient_at,
               MAX(timestamp)                                    AS last_patient_at
        FROM inferences
        WHERE run_id IS NOT NULL
        GROUP BY run_id"""

_RUN_HEALTH_CASE_SQL = f"""        CASE
            WHEN m.counters_registered IS NULL
                 THEN '{RUN_HEALTH_NEVER_FLUSHED}'
            WHEN COALESCE(d.counters_moved, 0) = 0
                 THEN '{RUN_HEALTH_MEASURED_CLEAN}'
            ELSE '{RUN_HEALTH_DEGRADED}'
        END"""
"""The one reading of "what does this run's health record say". Interpolated by
both run queries below, so there is no second copy to forget."""

_RUN_FINALIZATION_CASE_SQL = f"""        CASE
            WHEN r.finished_at IS NOT NULL THEN '{RUN_FINALIZATION_FINALIZED}'
            WHEN r.status = '{RUN_RECORD_STATUS_RUNNING}'
                 THEN '{RUN_FINALIZATION_LIVE_OR_DIED}'
            ELSE '{RUN_FINALIZATION_NOT_STAMPED}'
        END"""
"""The one reading of "did this run finish". Interpolated by both queries."""


RUN_ATTRIBUTION_NO_RUN = "(no run_id -- before run tracking, or written outside a run)"
"""``attribution`` for an inference row whose ``run_id`` is NULL.

TWO POPULATIONS UNDER ONE LABEL, AND THE LABEL SAYS SO. Every row written before
the run-identity pass has a NULL here, and so does every row
``oncotriage/api/server.py`` writes -- deliberately, because a request is not a
campaign and a `runs` row per POST would put one row in that table per request.
The database cannot separate them and this label does not pretend to. What it
must not do is read as "the run is unknown", which is a third thing and is what
an unlabelled NULL reads as."""

RUN_ATTRIBUTION_DANGLING = "(run_id set, but no such run row)"
"""``attribution`` for a row pointing at a `runs` id that is not there.

THIS STATE IS REACHABLE, which is why it is a label rather than an assumption.
``inferences.run_id`` carries NO enforced foreign key -- argued at the `runs`
CREATE TABLE, in four points, of which the operative one here is that
``empty_database`` deletes every table in ``sqlite_master`` order and would raise
partway through with the constraint on. So a wipe, a partial restore or a
hand-edited database can leave one, and an aggregate that quietly counted it as
attributed would be the only thing that could ever notice."""

RUN_ATTRIBUTION_ATTRIBUTED = "attributed to a run"
"""``attribution`` for a row whose ``run_id`` resolves to a `runs` row."""

RUN_ATTRIBUTION_STATES = (RUN_ATTRIBUTION_ATTRIBUTED, RUN_ATTRIBUTION_DANGLING,
                          RUN_ATTRIBUTION_NO_RUN)
"""Every value ``attribution`` can take. CLOSED; the dashboard branches on it."""


_RUN_ATTRIBUTION_CASE_SQL = f"""        CASE
            WHEN i.run_id IS NULL THEN '{RUN_ATTRIBUTION_NO_RUN}'
            WHEN r.id IS NULL     THEN '{RUN_ATTRIBUTION_DANGLING}'
            ELSE '{RUN_ATTRIBUTION_ATTRIBUTED}'
        END"""
"""The one reading of "does this row belong to a recorded run"."""


NO_RUN_LABEL = "(no run)"
"""The GROUP key the two pressure queries use where inferences.run_id is NULL.

DELIBERATELY NOT ``RUN_ATTRIBUTION_NO_RUN``, which says the same thing in a
sentence. That constant is a member of ``RUN_ATTRIBUTION_STATES``, a CLOSED
vocabulary the dashboard branches on, so borrowing it would make a future
rewording of the census's own labels silently change how these two tables group
-- and its sentence is the width of the terminal, which is fine for a census of
three rows and not for a key column beside twelve numeric ones. A NULL run_id
means the same thing here as it does there and is not a defect: the API writes
one per request on purpose, and every row predating run tracking has one.
"""


MODE_NOT_RECORDED_LABEL = "(not recorded)"
"""The GROUP key a call-mode column carries where it is NULL.

WHY THIS IS A CONSTANT AND WAS TWO LITERALS. ``call_mode_comparison`` coalesced
``runs.matching_call_mode`` and ``inferences.matching_call_mode`` to this string
in two places, and ``stage5_cache_effectiveness`` needs the identical bucket --
the two queries are read side by side, and a reader comparing "the per-trial
arm's cache hit rate" with "the per-trial arm's cost" has to be able to line the
GROUP keys up. Three copies of one label is the shape this project removes
(CROSS_ENCODER_MODEL, BM25_SPARSE_MODEL_NAME, ABLATION_DB_FILENAME): nothing
raises when they disagree, and the only symptom is two tables that will not join.

IT IS NOT 'grouped', AND THAT IS THE WHOLE REASON IT HAS A NAME RATHER THAN
BEING FOLDED AWAY. A NULL here is a row or a run written BEFORE its column
existed -- ``matching_call_mode`` is additive on both tables -- so the mode it
ran in is not recorded anywhere and cannot be inferred. Reading it as the
default arm would attribute every pre-era-3 row to grouped mode, which is
probably true and is not MEASURED, and a three-arm comparison that quietly
adopts an unmeasured majority is the class of report this file exists to remove.
"""


# The ledger, guarded, as `json_each` needs it. NULL and a non-JSON value both
# become an empty array, so a row with no ledger contributes no calls rather
# than raising -- which is what lets this read a pre-era row beside a current
# one. Written ONCE because `stage5_cache_effectiveness` interpolates it eight
# times and eight copies of a guard is eight chances for one of them to lose it;
# `stage5_output_split_pressure` writes the same expression inline because it
# has exactly one use of it.
_LEDGER_JSON_SQL = ("""CASE WHEN json_valid(i.llm_classifier_call_details)
                            THEN i.llm_classifier_call_details
                            ELSE '[]' END""")


# ---------------------------------------------------------------------------
# STAGE 5 SPLIT PRESSURE -- how near the three guards a campaign ran
# ---------------------------------------------------------------------------
#
# WHAT IS DERIVED HERE RATHER THAN STORED, AND THE RULE THAT DECIDES IT. A
# quantity a reader can compute from a column already on the row is not given a
# column of its own; it is computed here, once, in the one place that owns the
# reading. So the whole INPUT side of this measurement is derivation: the
# per-chunk estimate, the configured budget, the EFFECTIVE budget (which the
# packer raises when the chunk cap binds) and the two degradation flags are all
# inside `inferences.llm_classifier_packing`, and nothing was added to the
# schema for them. What the OUTPUT side needed was the two DENOMINATORS -- see
# INFERENCE_COLUMN_ADDITIONS' note on
# llm_classifier_output_split_threshold / llm_classifier_output_ceiling -- and
# those are configuration, not a derivable function of any stored measurement.
#
# NO DASHBOARD PANEL IN THIS PASS, DELIBERATELY. The Run Health tab's declared
# scope is the DEGRADATION counters -- run_metrics' two categories and the
# health_record reading over them -- and split pressure is neither a
# degradation nor a counter: a run at 40% of its input budget is perfectly
# healthy and has nothing to report there. Rendering it would either widen that
# tab's subject without saying so or add a tenth-and-a-half panel with no
# vocabulary of its own. A pressure panel is its own decision, with its own
# question about which quantile a clinician-facing page should show; it is a
# recorded follow-up rather than a silent extension. These two queries are the
# whole reader for now, and `python "16- Database Query.py"` is where they run.
#
# THESE FRAGMENTS ARE NAMED FOR THE REASON _RUN_HEALTH_CASE_SQL IS: the ratio
# below is written into a MAX, an AVG and two bucket counters in one query, and
# four hand-copied arithmetic expressions are four things that can disagree
# about what "pressure" means while every one of them keeps returning a number.

_PACK_JSON_SQL = ("CASE WHEN json_valid(i.llm_classifier_packing) "
                  "THEN i.llm_classifier_packing ELSE '{}' END")
"""The packing blob, or an empty object when the column is NULL or not JSON.

json_each() RAISES on malformed text, and a raise inside report() is the defect
item 38 removed -- one bad row would stop every query registered after this one.
The writer json.dumps() this column so a malformed value should be impossible;
"should be impossible" is not a reason to let one row end the report. NULL is
already safe (json_each(NULL) yields no rows) and passes through this CASE as
'{}', which yields none either."""

def _pack_field_sql(field: str) -> str:
    """One field of the packing blob, read through the validity guard above.

    A function rather than three constants because json_extract RAISES on
    malformed text exactly as json_each does -- measured, not assumed -- so
    every read of this column has to go through the same CASE, and three
    hand-written copies of it are three places to forget one.

    It builds a SQL FRAGMENT and touches no database, so calling it at module
    scope opens nothing -- the property section 2 of
    tests/test_package_invariants.py holds over every module in the package.
    """
    return f"json_extract({_PACK_JSON_SQL}, '$.{field}')"

_PACK_BUDGET_SQL = _pack_field_sql("budget_tokens")
"""The EFFECTIVE input budget of THIS inference, not the configured one.

The packer raises the budget uniformly when the chunk cap binds
(`cap_relaxed_budget`), so two inferences of one run can legitimately have been
packed against different numbers. Every ratio below is therefore per chunk
against its OWN inference's effective budget; a run-level MAX of the budget
divided into a run-level MAX of the estimate would be a ratio between two
different requests. NULL when the packer did not run, which makes every
expression built on it NULL rather than a division by zero."""

_PACK_BYPASSED_SQL = _pack_field_sql("bypassed_by")
"""WHAT bypassed the packer on this inference, or NULL if nothing did.

Present on the bypass branch of node_llm_classifier_evaluation and on no other
-- the absent-rather-than-empty convention, argued there -- so `IS NOT NULL` is
the whole test and there is no sentinel to keep in step. Its value is a
MATCHING_CALL_MODE_* constant today; the column is not compared against one,
because a second mechanism that bypasses the packer tomorrow should land in the
same bucket rather than silently rejoin the unpacked population.

A bypassed row has no budget, so it is NULL under _PACK_BUDGET_SQL exactly as a
row whose packer never ran to completion is -- which is why the pressure query
cannot separate the two by the budget alone and reads this instead."""

_PACK_CHUNK_TOKENS_SQL = "json_extract(c.value, '$.tokens_estimated')"
"""One chunk's estimated INPUT tokens: the fixed prefix PLUS that chunk's own
trials. It is the number the budget is a budget ON -- see the packer, which
charges `fixed_tokens` to every chunk because the model reads one whole
prompt."""

_INPUT_PRESSURE_SQL = (f"({_PACK_CHUNK_TOKENS_SQL} * 1.0 / {_PACK_BUDGET_SQL})")
"""One chunk's input pressure: 1.0 is exactly at the effective budget.

`* 1.0` because both operands are integers and SQLite would otherwise do
integer division -- every pressure under the budget would read 0 and the whole
measurement would report a campaign with infinite headroom."""


RUN_TABLES = ("runs", "run_metrics")
"""The two tables the run queries need, and which a database may not have.

Named once so a `requires=` declaration and any consumer asking "can this
database answer the run questions" cannot disagree about the list."""


# ---------------------------------------------------------------------------
# CAMPAIGNS -- THE FRAGMENTS OF ONE RUN, STITCHED BACK TOGETHER
# ---------------------------------------------------------------------------
#
# A `runs` ROW IS A PROCESS, NOT A CAMPAIGN, AND THE DIFFERENCE IS INVISIBLE IN
# EVERY QUERY ABOVE. `oncotriage/batch/runner.py` opens one row before its first
# patient and finalizes it after its last; a process that dies leaves that row
# KILLED (or FAILED), and the NEXT invocation reads the checkpoint, opens a
# SECOND row, and writes the remaining patients under the SECOND id. So one
# campaign that crashed twice is three rows, and `run_summary` reports each of
# them as a run:
#
#   * its `patients` count is a FRAGMENT -- every patient an earlier process
#     completed carries the EARLIER run's id, which is exactly what the
#     `resumed` column was added to say;
#   * its `started_at` is when the LAST process started, not when the campaign
#     did;
#   * and its status is the status of a process, so a campaign that finished
#     has KILLED rows in it.
#
# This query is the reader that puts them back together. It is DERIVED, not
# stored: nothing was added to the schema, and `resumed` plus the seven
# fingerprint columns are what make the derivation possible.
#
# THE STITCH RULE, AND WHY EACH HALF OF IT IS THERE
# ------------------------------------------------
# A run with `resumed = 1` continues the campaign of the nearest PRECEDING run
# whose status is KILLED or FAILED **and** whose fingerprint columns are
# identical. Chains stitch transitively, so three fragments are one campaign.
#
#   `resumed = 1`         The writer's own record that this process started from
#                         a non-empty checkpoint. NULL (a row written before that
#                         column existed) is NOT a resume: `NULL = 1` is NULL, so
#                         such a row is its own campaign, which is the honest
#                         reading of "nobody recorded whether this was a resume".
#
#   KILLED or FAILED      A campaign that FINISHED has nothing left to resume.
#                         Requiring the predecessor to have ended badly is what
#                         stops an ordinary re-run of a completed cohort being
#                         glued onto it.
#
#   IDENTICAL FINGERPRINT THE REASON THIS QUERY EXISTS AT ALL. A reviewer's
#                         question is always "which configuration produced this
#                         number", so fragments produced under different
#                         configurations MUST NOT SUM. A prompt-version bump, a
#                         renderer edit, a re-index or a model change between the
#                         crash and the resume breaks the chain, and the resumed
#                         run becomes its own campaign -- which is a fragment,
#                         and is reported as one run rather than silently added
#                         to a total it does not belong in.
#
# THE PREDICATE IS BUILT FROM `RUN_FINGERPRINT_COLUMNS`, NEVER RETYPED. That
# tuple is `("fingerprint_version",) + run_fingerprint.FINGERPRINT_FIELDS`, and
# the whole point of a gated field is that a run under a different value of it is
# a different configuration. Hand-listing six of the seven here would leave one
# axis along which two configurations stitch into one campaign with nothing
# saying so -- and a hand-written list does not grow when the next field is
# gated. Generated, it does.
#
# WHAT `IS` BUYS AND WHAT IT COSTS. SQLite's `IS` is null-safe equality, so a
# field that degraded to NULL on both sides (an unresolvable `collection_points`)
# compares equal rather than making every comparison NULL and refusing every
# stitch. The cost is that two runs with NO STAMP AT ALL would compare equal on
# all seven, so both sides are additionally required to carry a
# `fingerprint_version`: an unknown configuration is not a matching
# configuration, and `run_fingerprint` itself keys FP_ABSENT on exactly that
# column.
#
# WHAT THIS DOES NOT DO, STATED RATHER THAN DISCOVERED:
#
#   * It reads the ORDER of runs off `runs.id`, which is AUTOINCREMENT and
#     therefore monotone in creation order within one database. `started_at` is a
#     TEXT the writer stamps and two rows can share one; the primary key cannot.
#     Two databases merged by hand would break that assumption, and so would
#     every other query here that reads an id.
#   * "Nearest preceding" is nearest among runs satisfying BOTH halves of the
#     rule, which is the literal reading of it. So a resume CAN attach across an
#     intervening crashed run of a different configuration -- two campaigns run
#     alternately produce two chains whose wall spans overlap. That is a
#     reporting artifact and not a misattribution: `run_ids` is emitted in order
#     so the gap is visible. The alternative reading -- refuse to stitch when the
#     immediately preceding crashed run has a different fingerprint -- would
#     report a genuine resume as a whole campaign, which IS a misattribution.
#   * It cannot see a campaign that was never recorded. A `runs` row is written
#     by `oncotriage/batch/runner.py` and `oncotriage/ablation/study.py`; the API
#     writes none, on purpose, so nothing it produces appears here at all.

"""The statuses a resumed run may attach to. `RUN_RECORD_TERMINAL_STATUSES`
minus FINISHED, and a strict subset of it by construction -- see the guard
below. A FINISHED run has nothing left to resume, so gluing a later invocation
onto one would turn a re-run into a continuation.

WRITTEN OUT RATHER THAN DERIVED as `[s for s in RUN_RECORD_TERMINAL_STATUSES if
s != 'FINISHED']`, which was the obvious form and is the wrong one: a terminal
status added tomorrow would silently become resumable without anybody deciding
that it should be. Listing them makes the guard below a real check -- it fails
on a member that is not a status at all -- and makes a new status a deliberate
edit here.

STOPPED IS RESUMABLE FOR THE SAME REASON KILLED IS, and more strongly. A stop is
an operator saying "pause this campaign"; the checkpoint is intact by
construction (the switch is polled at the checkpoint's own cadence, so every
completed patient is in it), and the whole point of the switch is that the next
invocation picks up where it left off. A stopped fragment that did NOT stitch
would report the resumed half as a separate campaign covering a fraction of the
cohort -- which is exactly the fragmentation this query exists to undo.

IT MOVED TO ``oncotriage/storage/database_logger.py`` (the spend-gate pass) AND
IS IMPORTED, NOT RESTATED. A second consumer appeared --
``campaign_spend_before``, which walks the SAME chain backwards to seed a
resumed run's budget -- and it lives one layer DOWN, in the module that owns the
`runs` table, so it cannot import this one (this imports it, and the reverse is
a cycle). The choice was between a third copy of a three-member tuple whose
whole value is that adding a status is a deliberate edit, or one owner in the
module that owns the vocabulary it is a subset of. The guard below is unchanged
and still runs HERE, where the SQL it protects is."""

CALL_MODE_OMISSION_REASON = "omitted_from_model_response"
"""`trial_matches.not_evaluable_reason` for a trial the model was SENT and did
not answer for. The one omission measure the call-mode comparison reads.

WRITTEN OUT RATHER THAN IMPORTED, and the layering is the reason rather than
taste: the value's owner is
``oncotriage/agent/evaluation.py:NOT_EVALUABLE_MODEL_OMITTED``, and a storage
module importing the agent is the edge pass 20c-2c moved
``_resolve_primary_cancer`` out of ``database_logger`` to remove. It is the same
trade ``RUN_RECORD_TERMINAL_STATUSES`` makes one module over, with the same
mitigation: a restated constant is a constant that can drift, so
``tests/test_storage_query_layer.py`` imports both and requires them equal. A
test may import both because a test is in nobody's import graph.

WHY IT MATTERS TO THE CALL-MODE COMPARISON SPECIFICALLY. An omission is a trial
the model was sent inside a batch and did not answer for -- so it is a failure
mode that PER-TRIAL MODE CANNOT PRODUCE BY CONSTRUCTION: a request carrying one
trial either answers it or fails. A per-trial arm reading zero here is therefore
the expected result and not evidence the arm is better, and reading NON-zero
there is a finding about the reconciliation rather than about the model. Both
readings need the arm beside the number, which is what this query exists to put
there.

``not_evaluable_reasons`` next door NO LONGER writes this string as one of four
literals in a family CASE, and the paragraph that stood here said it did. That
CASE is now interpolated from the three tuples below -- which is what closed the
defect the hand-written list had already acquired: it named four reasons and the
per-trial pass had added a fifth (``per_trial_call_failed``), so a trial whose
own REQUEST failed was reported under "corrected from a model verdict", a
family that asserts the model answered. On the SHIPPED per-trial arm that is the
constructed reason most likely to occur."""

# ---------------------------------------------------------------------------
# The not_evaluable_reason vocabulary, by writer class
# ---------------------------------------------------------------------------
#
# RESTATED, NOT IMPORTED, for CALL_MODE_OMISSION_REASON's reason one screen up:
# the owner is ``oncotriage/agent/evaluation.py`` and a storage module importing
# the agent is the edge pass 20c-2c removed. Same trade, same mitigation --
# ``tests/test_storage_query_layer.py`` imports both sides and requires each
# tuple to equal its owner, which is what stops a restated constant drifting the
# way the hand-written CASE above already had.
NOT_EVALUABLE_REASONS_CONSTRUCTED = (
    "truncation_floor",
    "truncation_split_budget_exhausted",
    "omitted_from_model_response",
    "conflicting_duplicate_answers",
    "per_trial_call_failed",
)
"""The model never answered for this trial; the pipeline built the entry."""

NOT_EVALUABLE_REASONS_CORRECTED = (
    "trial-level verdict label not recognised",
    "model returned no criteria",
    "model rejection unsupported by its own criteria arrays",
    "no disqualifying row survived label normalisation",
    "trial-level verdict label unresolvable at finalization",
)
"""The model answered and the pipeline could not use the answer as written."""

NOT_EVALUABLE_REASONS_DECLARED = (
    "model declared this trial not evaluable",
)
"""The model declared the non-evaluation itself; nothing was corrected."""


def _sql_string_list(values) -> str:
    """``'a', 'b'`` -- a SQL IN-list literal, single quotes doubled.

    Every value this is handed today is a constant of this project with no
    apostrophe in it, so the escape changes nothing and is here for the day one
    of them gains a possessive. A reason is not user input and is never a
    parameter, because it is interpolated into a CASE rather than compared
    against a bound value.
    """
    return ", ".join("'" + str(v).replace("'", "''") + "'" for v in values)


# ONE CASE, THREE FAMILIES, GENERATED. A reader asks "who decided this trial was
# not evaluable" and gets an answer that cannot disagree with the code that
# decided it. The DECLARED family is the one a hand-written CASE gets wrong by
# omission rather than by staleness: without it a model-declared non-evaluation
# falls to the ELSE and is reported as a correction the pipeline never made.
_NOT_EVALUABLE_FAMILY_SQL = f"""        CASE
            WHEN tm.not_evaluable_reason IS NULL THEN '(not reported)'
            WHEN tm.not_evaluable_reason
                 IN ({_sql_string_list(NOT_EVALUABLE_REASONS_CONSTRUCTED)})
                 THEN 'constructed by the pipeline'
            WHEN tm.not_evaluable_reason
                 IN ({_sql_string_list(NOT_EVALUABLE_REASONS_CORRECTED)})
                 THEN 'corrected from a model verdict'
            WHEN tm.not_evaluable_reason
                 IN ({_sql_string_list(NOT_EVALUABLE_REASONS_DECLARED)})
                 THEN 'declared by the model'
            ELSE '(not a value this pipeline writes)'
        END"""
"""The family classifier, interpolated into ``not_evaluable_reasons``.

THE NULL ARM IS FIRST AND THE ELSE ARM NAMES ITSELF. Before this pass the NULL
arm sat SECOND, under a membership test -- harmless, because ``x IN (...)`` is
NULL rather than true for a NULL x, but it read as though a NULL could be
classified. And the ELSE was ``'corrected from a model verdict'``, so a value
from outside the vocabulary -- a hand-written row, a future writer, a restated
tuple that fell behind its owner -- was reported as a correction. It now says
that it is not a value this pipeline writes, which is the finding."""

CAMPAIGN_STAMP_COLUMN = "fingerprint_version"
"""The `runs` column whose presence says a configuration stamp was recorded.

NAMED RATHER THAN INDEXED OUT OF ``RUN_FINGERPRINT_COLUMNS[0]``: a positional
read of a tuple whose order is the stamp's own field order would silently start
testing a different column the day that order changes. The guard below is what
keeps the name honest."""

if CAMPAIGN_STAMP_COLUMN not in RUN_FINGERPRINT_COLUMNS:
    raise RuntimeError(
        f"CAMPAIGN_STAMP_COLUMN {CAMPAIGN_STAMP_COLUMN!r} is not one of "
        f"RUN_FINGERPRINT_COLUMNS {RUN_FINGERPRINT_COLUMNS!r}. The campaign "
        f"stitch tests it for NOT NULL to establish that a configuration was "
        f"recorded at all; a name that is not a column of `runs` makes that "
        f"test raise `no such column` inside report()."
    )

if not set(CAMPAIGN_RESUMABLE_STATUSES) < set(RUN_RECORD_TERMINAL_STATUSES):
    raise RuntimeError(
        f"CAMPAIGN_RESUMABLE_STATUSES {CAMPAIGN_RESUMABLE_STATUSES!r} must be a "
        f"PROPER subset of RUN_RECORD_TERMINAL_STATUSES "
        f"{RUN_RECORD_TERMINAL_STATUSES!r}. Equal would mean a FINISHED run can "
        f"be resumed onto, which turns a re-run into a continuation; a value "
        f"outside it is a status no `runs` row can hold, so no stitch would "
        f"ever be made."
    )
# A RuntimeError and not an `assert`: `python -O` deletes assert statements, and
# a guard that vanishes under an optimisation flag is not a guard. Same shape,
# same reason, as RESUME_SKIP_STATUSES' partition check in
# oncotriage/evaluation/run_harness.py.

_CAMPAIGN_STATUS_LIST_SQL = ", ".join(
    f"'{status}'" for status in CAMPAIGN_RESUMABLE_STATUSES)

_CAMPAIGN_FINGERPRINT_MATCH_SQL = "\n".join(
    f"                      AND prev.{column} IS r.{column}"
    for column in RUN_FINGERPRINT_COLUMNS)
"""One null-safe equality per gated fingerprint column, generated from the
writer's own tuple. Grows by itself when a field is gated."""

_CAMPAIGN_EDGE_SQL = f"""    edge AS (
        SELECT r.id AS edge_run_id,
               CASE WHEN r.resumed = 1 THEN (
                   SELECT MAX(prev.id)
                     FROM runs prev
                    WHERE prev.id < r.id
                      AND prev.status IN ({_CAMPAIGN_STATUS_LIST_SQL})
                      AND prev.{CAMPAIGN_STAMP_COLUMN} IS NOT NULL
{_CAMPAIGN_FINGERPRINT_MATCH_SQL}
               ) END AS parent_id
          FROM runs r
    )"""
"""Each run and the run it continues, or NULL.

The alias is `prev` rather than `p` DELIBERATELY. `p` is bound to a different
subquery in the same statement, and while SQL scoping keeps them apart,
``sql_table_aliases`` is a lexical reader that would bind the name once for the
whole string -- an over-derivation, whose cost is a query skipped on a database
that could have answered it. Costing nothing to avoid, it is avoided."""


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
        requires_columns=(("inferences", "llm_classifier_evaluation_time"),
                          ("inferences", "llm_classifier_output_tokens"),),
        sql='SELECT total_time, llm_classifier_evaluation_time, llm_classifier_output_tokens FROM inferences',
    ),
    # File 16 line 88, `df_timeout`
    Query(
        key='slowest_five',
        heading=None,
        render='repr',
        blank_after=False,
        requires_columns=(("inferences", "llm_classifier_evaluation_time"),
                          ("inferences", "llm_classifier_input_tokens"),
                          ("inferences", "llm_classifier_output_tokens"),),
        sql="""
    SELECT patient_id, age, condition_count, medication_count, 
           candidates_evaluated, total_time, llm_classifier_evaluation_time, 
           llm_classifier_input_tokens, llm_classifier_output_tokens, error
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
        requires_columns=(("inferences", "llm_classifier_evaluation_time"),
                          ("inferences", "llm_classifier_input_tokens"),
                          ("inferences", "llm_classifier_output_tokens"),),
        sql="""
    SELECT 
        total_time,
        llm_classifier_evaluation_time,
        llm_classifier_input_tokens,
        llm_classifier_output_tokens,
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
        requires_columns=(("inferences", "llm_classifier_evaluation_time"),
                          ("inferences", "llm_classifier_input_tokens"),
                          ("inferences", "llm_classifier_output_tokens"),),
        sql="""
    SELECT 
        patient_id,
        age,
        sex,
        condition_count,
        medication_count,
        candidates_evaluated,
        total_time,
        llm_classifier_evaluation_time,
        llm_classifier_input_tokens,
        llm_classifier_output_tokens,
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
        requires_columns=(("inferences", "llm_classifier_output_tokens"),),
        sql="""
    SELECT 
        patient_id,
        candidates_evaluated,
        llm_classifier_output_tokens,
        llm_classifier_output_tokens / NULLIF(candidates_evaluated, 0) as tokens_per_trial,
        total_time
    FROM inferences
    WHERE llm_classifier_output_tokens > 4000
    ORDER BY llm_classifier_output_tokens DESC
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
        requires_columns=(("inferences", "llm_classifier_output_tokens"),
                          ("inferences", "llm_classifier_prompt"),),
        sql="""
    SELECT 
        patient_id,
        llm_classifier_prompt,
        llm_classifier_output_tokens,
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
        requires_columns=(("inferences", "llm_classifier_evaluation_time"),),
        sql="""
    SELECT 
        AVG(query_expansion_time) as avg_expansion,
        AVG(hybrid_retrieval_time) as avg_retrieval,
        AVG(cross_encoder_time) as avg_cross_encoder,
        AVG(rule_filter_time) as avg_filter,
        AVG(llm_classifier_evaluation_time) as avg_llm_classifier,
        MAX(query_expansion_time) as max_expansion,
        MAX(hybrid_retrieval_time) as max_retrieval,
        MAX(cross_encoder_time) as max_cross_encoder,
        MAX(rule_filter_time) as max_filter,
        MAX(llm_classifier_evaluation_time) as max_llm_classifier
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
        requires_columns=(("inferences", "llm_classifier_input_tokens"),
                          ("inferences", "llm_classifier_output_tokens"),),
        sql="""
    SELECT 
        condition_count,
        medication_count,
        candidates_evaluated,
        AVG(llm_classifier_input_tokens) as avg_input_tokens,
        AVG(llm_classifier_output_tokens) as avg_output_tokens,
        AVG(llm_classifier_output_tokens / NULLIF(candidates_evaluated, 0)) as avg_tokens_per_trial,
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
        requires_columns=(("inferences", "query_expansion_path"),),
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
        requires_columns=(("inferences", "llm_classifier_input_tokens"),
                          ("inferences", "llm_classifier_output_tokens"),
                          ("inferences", "llm_classifier_reasoning_tokens"),),
        sql="""
    SELECT
        matching_model,
        COUNT(*)                     as rows_n,
        SUM(llm_classifier_input_tokens)      as input_tokens,
        SUM(llm_classifier_output_tokens)     as output_tokens,
        SUM(llm_classifier_reasoning_tokens)  as reasoning_tokens,
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
        requires_columns=(("inferences", "llm_classifier_output_tokens"),),
        sql="""
    SELECT 
        patient_id,
        condition_count,
        medication_count,
        candidates_evaluated,
        llm_classifier_output_tokens,
        total_time,
        eligible_matches,
        CASE 
            WHEN medication_count > 100 THEN 'High Med Count'
            WHEN llm_classifier_output_tokens > 10000 THEN 'Verbose Output'
            WHEN total_time > 120 THEN 'Slow Processing'
            WHEN candidates_evaluated = 0 THEN 'No Candidates'
            ELSE 'Other'
        END as anomaly_type
    FROM inferences
    WHERE medication_count > 100 
       OR llm_classifier_output_tokens > 10000 
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
        requires_columns=(("inferences", "llm_classifier_input_tokens"),),
        sql="""
    SELECT 
        patient_id,
        medication_count,
        llm_classifier_input_tokens,
        llm_classifier_input_tokens / NULLIF(candidates_evaluated, 0) as tokens_per_trial,
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
        key='llm_classifier_efficiency_by_trial_count',
        heading='=== GPT-4O EFFICIENCY BY TRIAL COUNT ===',
        render='to_string',
        blank_after=True,
        requires_columns=(("inferences", "llm_classifier_evaluation_time"),
                          ("inferences", "llm_classifier_output_tokens"),),
        sql="""
    SELECT 
        candidates_evaluated as trial_count,
        COUNT(*) as patient_count,
        AVG(llm_classifier_evaluation_time) as avg_time,
        AVG(llm_classifier_output_tokens) as avg_output_tokens,
        AVG(llm_classifier_output_tokens / NULLIF(candidates_evaluated, 0)) as tokens_per_trial
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
    # THE TOTALS COME FIRST, AND THE ORDER IS THE POINT. `report()` walks
    # QUERIES in order, so putting this entry immediately before the listing is
    # what puts "how many, by category, over every row" above "here are up to
    # twenty of them". Reversed, a reader meets the sample first and has already
    # formed a judgement by the time the totals arrive.
    #
    # render='skip_if_empty' prints NOTHING -- not the heading, not the notes,
    # not an empty table -- when there are no issues, so a clean database still
    # renders exactly the one clean message the listing has always printed.
    Query(
        key='pipeline_consistency_totals',
        heading='=== PIPELINE CONSISTENCY: ISSUE COUNTS (ALL ROWS) ===',
        render='skip_if_empty',
        blank_after=True,
        notes=(f'Counted over every row, with no limit. The listing below is a '
               f'SAMPLE capped at {CONSISTENCY_LISTING_LIMIT} rows, ordered by '
               f'(issue, patient_id, id).',),
        requires_columns=(("inferences", "not_evaluable_trials"),),
        sql=_PIPELINE_CONSISTENCY_SUMMARY_SQL,
    ),
    # File 16 line 579, `df_consistency`. SQL and its full argument at
    # _PIPELINE_CONSISTENCY_SQL above.
    Query(
        key='pipeline_consistency',
        heading='=== PIPELINE CONSISTENCY ISSUES ===',
        render='empty_or_to_string',
        blank_after=True,
        requires_columns=(("inferences", "not_evaluable_trials"),),
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
        requires_columns=(("inferences", "retrieval_degraded"),
                          ("inferences", "retrieval_trials_lost"),),
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
        requires_columns=(("inferences", "retrieval_channels"),
                          ("inferences", "retrieval_channels_expected"),
                          ("inferences", "retrieval_channels_ok"),
                          ("inferences", "retrieval_degraded"),
                          ("inferences", "retrieval_trials_lost"),),
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
        requires_columns=(("inferences", "mesh_resolution"),
                          ("inferences", "query_expansion_path"),),
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
        requires_columns=(("inferences", "mesh_filter_applied"),
                          ("inferences", "mesh_filter_skip_reason"),),
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
    # ── Stage 5 normalizer provenance (the provenance-persistence pass) ─────
    #
    # FOUR QUERIES, ONE PER CAMPAIGN QUESTION, AND NOT ONE OF THEM PARSES JSON.
    # Every artifact each reads was computed by Stage 5 and, before that pass,
    # either dropped at the write (`not_evaluable_reason`), discarded after a
    # log line (`verdict_normalizations`) or reduced to a run-level event count
    # (`label_remaps`, as `cross_vocab_remaps`). They are scalar columns
    # precisely so these are GROUP BYs rather than blob scans.
    #
    # NULL IS PROJECTED, NEVER FOLDED. Each groups on a COALESCE label that
    # names the absence -- '(not reported)' -- rather than dropping the row or
    # counting it as a measured zero, because the whole point of the columns is
    # that "the normalizer found none" and "no normalizer ran" are different
    # findings. A COUNT(*) with the NULL rows dropped would report a clean audit
    # over a population that was never audited.
    Query(
        key='not_evaluable_reasons',
        heading='=== NOT-EVALUABLE TRIALS BY REASON ===',
        render='to_string',
        blank_after=True,
        notes=(
            "NULL reason on a not_evaluable row means ONE thing now: the row",
            "predates the column. Every path that records a non-evaluation",
            "stamps a reason -- including the three that did not (Stage 5",
            "Step 2's no-criteria arms, an unreadable label over criteria",
            "that disqualify nobody, and a model-DECLARED non-evaluation).",
            "The three families below say WHO decided; 'declared by the",
            "model' is not a correction. See TRIAL_MATCH_COLUMN_ADDITIONS.",
        ),
        requires_columns=(("trial_matches", "not_evaluable_reason"),
                          ("trial_matches", "verdict_source"),),
        sql=f"""
    SELECT
        COALESCE(tm.not_evaluable_reason, '(not reported)') AS not_evaluable_reason,
{_NOT_EVALUABLE_FAMILY_SQL}
                                                    AS family,
        COUNT(*)                                            AS trials,
        COUNT(DISTINCT tm.inference_id)                     AS runs,
        SUM(CASE WHEN tm.verdict_source IS NULL THEN 1 ELSE 0 END)
                                                            AS never_had_a_model_label
    FROM trial_matches tm
    WHERE tm.eligible = 'not_evaluable'
    GROUP BY tm.not_evaluable_reason
    ORDER BY trials DESC, not_evaluable_reason
""",
    ),
    Query(
        key='verdict_normalization_sources',
        heading='=== TRIAL VERDICT LABELS: HOW EACH WAS READ ===',
        render='to_string',
        blank_after=True,
        notes=(
            "'canonical' is a MEASUREMENT -- the label was read and needed no",
            "recovery. '(not checked)' is the entries Stage 5 CONSTRUCTED,",
            "which never carried a model-written label, plus rows written",
            "before the column existed.",
        ),
        requires_columns=(("trial_matches", "verdict_original_type"),
                          ("trial_matches", "verdict_source"),),
        sql="""
    SELECT
        COALESCE(tm.verdict_source, '(not checked)')       AS verdict_source,
        COALESCE(tm.verdict_original_type, '(n/a)')        AS original_type,
        COUNT(*)                                           AS trials,
        COUNT(DISTINCT tm.inference_id)                    AS runs,
        SUM(CASE WHEN tm.eligible = 'eligible' THEN 1 ELSE 0 END)      AS ended_eligible,
        SUM(CASE WHEN tm.eligible = 'not_eligible' THEN 1 ELSE 0 END)  AS ended_not_eligible,
        SUM(CASE WHEN tm.eligible = 'not_evaluable' THEN 1 ELSE 0 END) AS ended_not_evaluable
    FROM trial_matches tm
    GROUP BY tm.verdict_source, tm.verdict_original_type
    ORDER BY trials DESC, verdict_source, original_type
""",
    ),
    Query(
        key='criterion_remap_incidence',
        heading='=== CRITERION LABEL REMAPS: TRIALS AND RUNS ===',
        render='to_string',
        blank_after=True,
        notes=(
            "remap EVENTS and remapped TRIALS are different numbers: four",
            "remaps on one trial and one on each of four trials give the same",
            "event total. inferences.cross_vocab_remaps carries the event",
            "total; the per-trial column is what makes the second countable.",
        ),
        requires_columns=(("trial_matches", "criterion_remaps"),),
        sql="""
    SELECT
        COUNT(*)                                                        AS trial_rows,
        SUM(CASE WHEN tm.criterion_remaps IS NULL THEN 1 ELSE 0 END)    AS not_checked,
        SUM(CASE WHEN tm.criterion_remaps = 0 THEN 1 ELSE 0 END)        AS checked_clean,
        SUM(CASE WHEN tm.criterion_remaps > 0 THEN 1 ELSE 0 END)        AS trials_with_a_remap,
        COUNT(DISTINCT CASE WHEN tm.criterion_remaps > 0
                            THEN tm.inference_id END)                   AS runs_with_a_remap,
        SUM(COALESCE(tm.criterion_remaps, 0))                           AS remap_events
    FROM trial_matches tm
""",
    ),
    Query(
        key='run_normalizer_provenance',
        heading='=== PER-RUN NORMALIZER PROVENANCE (worst first) ===',
        render='to_string',
        blank_after=True,
        notes=(
            "remap_events_stored is inferences.cross_vocab_remaps and",
            "remap_events_summed is the sum of the per-trial column. They are",
            "the same list counted at the same line, so a row where they",
            "disagree is a defect in the carry rather than a finding about the",
            "model. The check is only meaningful where the per-trial column is",
            "populated, which is why remap_rows_checked is beside it.",
        ),
        requires_columns=(("inferences", "cross_vocab_remaps"),
                          ("inferences", "remapped_trials"),
                          ("inferences", "verdict_normalizations"),
                          ("trial_matches", "criterion_remaps"),),
        sql="""
    SELECT
        i.id, i.patient_id, i.timestamp,
        i.candidates_evaluated,
        i.verdict_normalizations,
        i.remapped_trials,
        i.cross_vocab_remaps                                  AS remap_events_stored,
        SUM(COALESCE(tm.criterion_remaps, 0))                 AS remap_events_summed,
        SUM(CASE WHEN tm.criterion_remaps IS NOT NULL THEN 1 ELSE 0 END)
                                                              AS remap_rows_checked,
        SUM(CASE WHEN tm.eligible = 'not_evaluable' THEN 1 ELSE 0 END)
                                                              AS not_evaluable_rows
    FROM inferences i
    LEFT JOIN trial_matches tm ON tm.inference_id = i.id
    GROUP BY i.id
    HAVING COALESCE(i.verdict_normalizations, 0) > 0
        OR COALESCE(i.remapped_trials, 0) > 0
        OR not_evaluable_rows > 0
    ORDER BY COALESCE(i.verdict_normalizations, 0)
             + COALESCE(i.remapped_trials, 0) DESC,
             i.id
    LIMIT 25
""",
    ),
    # ── The run tables (the run-reader pass) ───────────────────────────────
    #
    # BOTH DECLARE `requires`, and that is what keeps report() running to the
    # end against a database that predates the run-identity pass -- which the
    # production one on this machine does. See Query.requires.
    Query(
        key='run_summary',
        heading='=== RUNS: ONE ROW PER CAMPAIGN ===',
        render='to_string',
        blank_after=True,
        requires=RUN_TABLES,
        # ITS PATIENT ROLLUP JOINS ON inferences.run_id, so the column is a
        # requirement here too. This was MISSED on the first draft, which
        # declared the column only on run_attribution_coverage on the reasoning
        # that it was "the only query that joins on an additive column" -- and
        # the control that exercises the column-only shape is what found it,
        # not reading. See tests/test_storage_query_layer.py section 2b.
        #
        # `runs.resumed` IS THE SECOND, AND IT IS A COLUMN OF A TABLE THIS QUERY
        # ALREADY DECLARES WHOLE. `requires` says "this database has a `runs`
        # table"; a database written between the run-identity pass and the
        # resumed column has one AND lacks the column, which is precisely the
        # distinction `requires` cannot draw and `requires_columns` exists for.
        #
        # `runs.matching_call_mode` IS THE THIRD, and it is here for `resumed`'s
        # reason one era later: a database written between the resumed column
        # and the call-mode one has a `runs` table AND lacks this column. It is
        # PROJECTED rather than joined on, so its absence would give NULL rather
        # than raising -- but `derive_requires_columns` reads the SQL and
        # tests/test_storage_schema_guards.py section 1 compares the derivation
        # against this declaration, so an additive column named anywhere in the
        # SQL is declared here whether or not the query would survive without
        # it. Declaring more than strictly necessary costs a skip on a database
        # that could half-answer; not declaring it costs the derivation check.
        requires_columns=(("inferences", "run_id"),
                          ("runs", "matching_call_mode"), ("runs", "resumed")),
        notes=(
            "patients / errored / cost_usd are 0 for a run no inference row",
            "references -- that is a measured zero, because a LEFT JOIN with no",
            "match here means no patient claimed the run.",
            "",
            "degradation_events is NOT defaulted to 0, because 'no counter",
            "moved' and 'nothing was ever flushed' would then be the same",
            "number. health_record is the column that separates them.",
            "",
            "NOT CAPPED. There is one row per campaign, not one per patient.",
        ),
        sql=f"""
    SELECT
        r.id                                            AS run_id,
        r.invocation_source,
        r.status,
{_RUN_FINALIZATION_CASE_SQL}                                    AS finalization,
        r.started_at,
        r.finished_at,
        COALESCE(p.patients, 0)                         AS patients,
        COALESCE(p.errored, 0)                          AS errored,
        COALESCE(p.cost_usd, 0.0)                       AS cost_usd,
        COALESCE(p.rows_with_no_cost, 0)                AS rows_with_no_cost,
        p.first_patient_at,
        p.last_patient_at,
{_RUN_HEALTH_CASE_SQL}                                    AS health_record,
        m.counters_registered,
        m.counters_nonzero,
        d.counters_moved,
        d.degradation_events,
        -- WAS THIS CAMPAIGN A RESUME, and it qualifies two columns above it.
        -- A resumed row's `started_at` is when the LAST process started, not
        -- when the campaign did, and its `patients` count covers only the
        -- patients THIS process wrote -- every patient an earlier process
        -- completed carries the EARLIER run's id. So on a resumed row both are
        -- fragments, and this is the only column that says so. NULL means not
        -- recorded (a row written before the column existed), which is why it
        -- is not COALESCEd to 0: a measured "not a resume" and an unrecorded
        -- one are different facts.
        r.resumed,
        r.fingerprint_version,
        r.llm_classifier_prompt_version,
        r.llm_classifier_renderer_digest,
        -- WHICH STAGE 5 ARM THIS RUN WAS STAMPED WITH. NULL is "not recorded"
        -- and is NOT 'grouped'; see RUN_COLUMN_ADDITIONS. It sits with the
        -- other stamp columns because it IS one -- a resume across two arms is
        -- refused by the same gate a prompt bump is -- and it is projected here
        -- rather than only in call_mode_comparison because a run row that does
        -- not say which arm produced it is a row nothing can attribute.
        r.matching_call_mode,
        r.matching_model_configured,
        r.qdrant_collection,
        r.collection_points,
        r.data_snapshot_date
    FROM runs r
    LEFT JOIN ({_RUN_HEALTH_PATIENTS_SQL}
    ) p ON p.run_id = r.id
    LEFT JOIN ({_RUN_HEALTH_DEGRADATION_SQL}
    ) d ON d.run_id = r.id
    LEFT JOIN ({_RUN_HEALTH_META_SQL}
    ) m ON m.run_id = r.id
    ORDER BY r.id DESC
""",
    ),
    Query(
        key='run_degradation_breakdown',
        heading='=== RUNS: DEGRADATION BY COUNTER ===',
        render='to_string',
        blank_after=True,
        requires=RUN_TABLES,
        notes=(
            "DRIVEN FROM `runs`, so a clean run is a row here too -- with",
            f"counter '{RUN_HEALTH_NO_COUNTER_LABEL}' and health_record",
            f"'{RUN_HEALTH_MEASURED_CLEAN}'. A breakdown driven from",
            "run_metrics would omit exactly the runs a reader most wants",
            "confirmed, and an omission reads as an absence of evidence.",
            "",
            "events is a SUM of one counter's keys, not a count of keys: a",
            "counter keyed by 12 distinct units with one hit each and a counter",
            "keyed by one unit hit 12 times both read 12 here. The keys",
            "themselves are deliberately not in this table -- they carry",
            "third-party and clinical text. See run_metrics' CREATE TABLE.",
        ),
        sql=f"""
    SELECT
        r.id                                            AS run_id,
        r.invocation_source,
        r.status,
{_RUN_HEALTH_CASE_SQL}                                    AS health_record,
        COALESCE(rm.name, '{RUN_HEALTH_NO_COUNTER_LABEL}') AS counter,
        rm.value                                        AS events,
        rm.written_at,
        m.counters_registered
    FROM runs r
    LEFT JOIN run_metrics rm
           ON rm.run_id = r.id
          AND rm.category = '{RUN_METRIC_CATEGORY_DEGRADATION}'
    LEFT JOIN ({_RUN_HEALTH_DEGRADATION_SQL}
    ) d ON d.run_id = r.id
    LEFT JOIN ({_RUN_HEALTH_META_SQL}
    ) m ON m.run_id = r.id
    ORDER BY r.id DESC, rm.value DESC, rm.name
""",
    ),
    Query(
        key='run_attribution_coverage',
        heading='=== INFERENCE ROWS BY RUN ATTRIBUTION ===',
        render='to_string',
        blank_after=True,
        requires=RUN_TABLES,
        # It JOINS on inferences.run_id, so the column is a requirement --
        # where a query merely SELECTing a missing column would be the
        # INFERENCE_COLUMN_ADDITIONS case the rest of the registry already
        # handles by projecting NULL. run_summary carries the same declaration
        # for the same reason; run_degradation_breakdown does not, because it
        # touches only `runs` and `run_metrics`.
        requires_columns=(("inferences", "run_id"),),
        notes=(
            "A NULL run_id is a VALUE, not a legacy. The API writes one per",
            "request on purpose. So this is a census, not a defect list -- with",
            "one exception, the dangling row, which IS a defect and is the only",
            "thing that can ever report one.",
        ),
        sql=f"""
    SELECT
{_RUN_ATTRIBUTION_CASE_SQL}                                    AS attribution,
        COUNT(*)                                        AS inference_rows,
        COUNT(DISTINCT i.run_id)                        AS distinct_run_ids,
        MIN(i.timestamp)                                AS first_at,
        MAX(i.timestamp)                                AS last_at,
        ROUND(SUM(COALESCE(i.estimated_cost_usd, 0)), 4) AS cost_usd,
        SUM(CASE WHEN i.estimated_cost_usd IS NULL
                 THEN 1 ELSE 0 END)                     AS rows_with_no_cost
    FROM inferences i
    LEFT JOIN runs r ON r.id = i.run_id
    GROUP BY attribution
    ORDER BY inference_rows DESC, attribution
""",
    ),
    # ── CAMPAIGNS: the fragments of one run, stitched (the campaign pass) ──
    #
    # The rule, its three halves and everything it deliberately does not do are
    # argued at CAMPAIGN_RESUMABLE_STATUSES above, beside the fragments this SQL
    # interpolates. What is worth repeating HERE is the acceptance criterion:
    # fragments produced under DIFFERENT configurations must not sum, because
    # "which configuration produced this number" is the question a campaign
    # total exists to answer.
    Query(
        key='campaign_summary',
        heading='=== CAMPAIGNS: RUNS STITCHED ACROSS CRASH AND RESUME ===',
        render='to_string',
        blank_after=True,
        # `runs` ALONE, and NOT RUN_TABLES. This query never reads
        # `run_metrics`, so declaring it would skip the campaign view on a
        # database that can answer it -- `dangling_run_references`' ruling, for
        # the same reason.
        requires=("runs",),
        # BOTH ARE JOIN/PREDICATE COLUMNS, not projected ones: `inferences.run_id`
        # is what the patient rollup groups on and `runs.resumed` is the whole
        # stitch predicate, so their absence makes the SQL unparseable rather
        # than merely NULL. The declaration is checked against
        # derive_requires_columns by tests/test_storage_schema_guards.py
        # section 1, which is why it is written in ADDITIVE_COLUMNS key order.
        # `runs.matching_call_mode` joins them at era 4, and here it really is
        # a PREDICATE column and not merely a projected one: the stitch
        # predicate is generated from RUN_FINGERPRINT_COLUMNS, so this column is
        # named in the `edge` CTE's WHERE clause and its absence makes the SQL
        # unparseable exactly as `resumed`'s does.
        # `runs.campaign_cohort_size` and `runs.campaign_cohort_seed` join
        # them at era 8, for the SAME reason `matching_call_mode` did: they
        # are RUN_FINGERPRINT_COLUMNS members, the stitch predicate is
        # GENERATED from that tuple, so both are named in the `edge` CTE's
        # WHERE clause and their absence makes the SQL unparseable rather than
        # merely NULL. A gated field added to the stamp and NOT declared here
        # would take report() down on any database written before it -- which
        # is item 38's own defect, reached through a tuple somebody widened
        # three modules away. THAT IS WHY THIS DECLARATION IS DERIVED-CHECKED
        # RATHER THAN TRUSTED: tests/test_storage_schema_guards.py section 1a
        # reads the rendered SQL, and it is what caught this pair.
        # `runs.cross_encoder_revision` joins them at era 9, on exactly the
        # same footing and found the same way: it is a RUN_FINGERPRINT_COLUMNS
        # member as of FINGERPRINT_VERSION 5, so the GENERATED stitch predicate
        # names it, and the derived-versus-declared check is what reported it
        # before a pre-era-9 database could. Third time that mechanism has
        # caught a stamp field widened three modules away.
        # `runs.matching_per_trial_empty_retries` joins them at era 11, on the
        # same footing and found the same way for the FOURTH time: it is a
        # RUN_FINGERPRINT_COLUMNS member as of FINGERPRINT_VERSION 6, so the
        # GENERATED stitch predicate names it. `runs.matching_temperature_sent`
        # joins them at era 12 on the same footing and found the same way for
        # the FIFTH time: it is a RUN_FINGERPRINT_COLUMNS member as of
        # FINGERPRINT_VERSION 7. That this mechanism has now caught five
        # consecutive stamp widenings is the argument for the derived check
        # rather than for a bigger comment.
        requires_columns=(("inferences", "run_id"),
                          ("runs", "campaign_cohort_seed"),
                          ("runs", "campaign_cohort_size"),
                          ("runs", "cross_encoder_revision"),
                          ("runs", "matching_call_mode"),
                          ("runs", "matching_per_trial_empty_retries"),
                          ("runs", "matching_temperature_sent"),
                          ("runs", "resumed")),
        notes=(
            "One row per CAMPAIGN, not per run. A campaign that never crashed",
            "is a campaign of one, and `stitched` is 0 for it -- which is what",
            "makes 'this total covers three processes' a visible fact rather",
            "than an invisible one.",
            "",
            "total_patients is DISTINCT patients across the whole campaign;",
            "inference_rows is the rows they produced. THEY DIFFER ON EVERY",
            "REAL CAMPAIGN: the resample pass writes a SECOND row for each of",
            "RESAMPLE_COUNT already-processed patients, and a patient whose",
            "attempt errored is re-run by the resume, so it has a row in two",
            "fragments. Costs and counts elsewhere in this row are summed over",
            "the ROWS. run_summary's per-run `patients` is also a row count and",
            "is a fragment of inference_rows whenever `resumed` is 1.",
            "",
            "last_finished_at IS THE END OF THE SPAN, NOT NECESSARILY THE END",
            "OF THE CAMPAIGN: unfinalized_runs > 0 means at least one fragment",
            "carries no finished_at (live, or killed without a stamp), so the",
            "span is open at that end. It is NOT defaulted to `now`.",
            "",
            "The fingerprint columns are the ROOT fragment's. Every member",
            "matched its parent on ALL of them, transitively, so one campaign has",
            "one configuration by construction -- that is the invariant the",
            "stitch enforces, and reporting it here is what lets a reviewer",
            "attribute the total without opening another query.",
            "",
            "NOT CAPPED. There is one row per campaign.",
        ),
        sql=f"""
WITH RECURSIVE
{_CAMPAIGN_EDGE_SQL},
    -- WALK UP FROM EVERY RUN TO ITS ROOT. `parent_id` is strictly less than
    -- `edge_run_id` by construction (the subquery above takes MAX(prev.id)
    -- WHERE prev.id < r.id), so this terminates and cannot cycle -- which is
    -- the property that makes an unbounded recursive CTE safe here.
    walk(walk_run_id, cursor_id) AS (
        SELECT edge_run_id, edge_run_id FROM edge
        UNION ALL
        SELECT w.walk_run_id, e.parent_id
          FROM walk w
          JOIN edge e ON e.edge_run_id = w.cursor_id
         WHERE e.parent_id IS NOT NULL
    ),
    -- The campaign a run belongs to IS the smallest id its walk reached, which
    -- is the root. Every member's chain ends at that root and ids only
    -- decrease along a chain, so the root is also the smallest MEMBER id --
    -- which is what lets the ordered path below start from `campaign_id`.
    member AS (
        SELECT walk_run_id AS member_run_id, MIN(cursor_id) AS campaign_id
          FROM walk GROUP BY walk_run_id
    ),
    -- Pre-aggregated per RUN before it is joined, for the reason
    -- _RUN_HEALTH_PATIENTS_SQL records: a run with 20 patients joined
    -- unaggregated multiplies every other child of the same row.
    --
    -- `n_rows` IS ROWS AND IT IS NOT THE COHORT SIZE. It was called
    -- `n_patients` and summed into a column called `total_patients`, which was
    -- wrong on every campaign this runner has ever produced: the RESAMPLE pass
    -- re-runs a seeded subset of already-processed patients (RESAMPLE_COUNT is
    -- 100) and each re-run writes ANOTHER inference row. So a 1,000-patient
    -- campaign reported 1,100 patients, and a reviewer reading "total_patients
    -- is the campaign's real cohort size" divided by a number 10% too large.
    -- The rows are still worth reporting -- they are what every cost and count
    -- in this table is summed over -- so they keep a column of their own under
    -- an honest name.
    patients AS (
        SELECT i.run_id AS patient_run_id,
               COUNT(*) AS n_rows,
               SUM(COALESCE(i.estimated_cost_usd, 0)) AS cost,
               SUM(CASE WHEN i.estimated_cost_usd IS NULL
                        THEN 1 ELSE 0 END) AS unpriced
          FROM inferences i
         WHERE i.run_id IS NOT NULL
         GROUP BY i.run_id
    ),
    -- THE COHORT, COUNTED ACROSS THE WHOLE CAMPAIGN AND NOT PER FRAGMENT.
    --
    -- A per-run DISTINCT summed would be right only if fragments never shared
    -- a patient, and they do: a patient whose main-pass attempt ERRORED is not
    -- checkpointed, so the resume re-runs it and BOTH fragments carry a row
    -- for it. Counting distinct over the campaign's whole membership is the
    -- literal reading of the question -- how many patients did this campaign
    -- cover -- and it is the only form that survives both the resample overlap
    -- within a fragment and the retry overlap between fragments.
    --
    -- It joins `member` rather than re-deriving the walk, so there is one
    -- statement of what a campaign contains.
    cohort AS (
        SELECT mc.campaign_id AS cohort_campaign_id,
               COUNT(DISTINCT ic.patient_id) AS n_patients
          FROM member mc
          JOIN inferences ic ON ic.run_id = mc.member_run_id
         GROUP BY mc.campaign_id
    ),
    stats AS (
        SELECT m.campaign_id                                AS campaign_id,
               COUNT(*)                                     AS runs,
               MAX(m.member_run_id)                         AS last_run_id,
               MIN(r.started_at)                            AS first_started_at,
               MAX(r.finished_at)                           AS last_finished_at,
               SUM(CASE WHEN r.finished_at IS NULL
                        THEN 1 ELSE 0 END)                  AS unfinalized_runs,
               COUNT(DISTINCT r.status)                     AS distinct_statuses,
               COALESCE(SUM(p2.n_rows), 0)                  AS inference_rows,
               ROUND(COALESCE(SUM(p2.cost), 0), 4)          AS total_cost_usd,
               COALESCE(SUM(p2.unpriced), 0)                AS rows_with_no_cost
          FROM member m
          JOIN runs r ON r.id = m.member_run_id
          LEFT JOIN patients p2 ON p2.patient_run_id = m.member_run_id
         GROUP BY m.campaign_id
    ),
    -- THE ORDERED LIST, BUILT BY RECURSION RATHER THAN BY group_concat.
    -- SQLite's group_concat leaves its order arbitrary, and an ORDER BY inside
    -- it needs 3.44+, which a CI runner's system SQLite may not be. Determinism
    -- is a stated property of this project, so the string is assembled one
    -- member at a time in ascending id order, which is guaranteed on every
    -- version.
    path AS (
        SELECT root.id                     AS campaign_id,
               root.id                     AS at_run,
               CAST(root.id AS TEXT)       AS run_ids,
               root.status                 AS statuses
          FROM runs root
         WHERE root.id IN (SELECT campaign_id FROM member)
        UNION ALL
        SELECT pth.campaign_id,
               nxt.id,
               pth.run_ids || ' -> ' || nxt.id,
               pth.statuses || ' -> ' || nxt.status
          FROM path pth
          JOIN runs nxt
            ON nxt.id = (SELECT MIN(m2.member_run_id) FROM member m2
                          WHERE m2.campaign_id = pth.campaign_id
                            AND m2.member_run_id > pth.at_run)
    )
SELECT s.campaign_id,
       pa.run_ids,
       s.runs,
       CASE WHEN s.runs > 1 THEN 1 ELSE 0 END              AS stitched,
       pa.statuses,
       CASE WHEN s.distinct_statuses > 1 THEN 1 ELSE 0 END AS mixed_status,
       -- DISTINCT PATIENTS, then the rows those patients produced. Both are
       -- projected because a reader needs both: the first is the cohort the
       -- campaign covered, the second is what every cost in this row is summed
       -- over. Their difference is the resample pass plus any patient a
       -- fragment retried.
       COALESCE(c.n_patients, 0)                           AS total_patients,
       s.inference_rows,
       s.first_started_at,
       s.last_finished_at,
       s.unfinalized_runs,
       s.total_cost_usd,
       s.rows_with_no_cost,
       head.invocation_source,
       head.fingerprint_version,
       head.llm_classifier_prompt_version,
       head.llm_classifier_renderer_digest,
       head.matching_model_configured,
       -- THE ARM. A campaign is stitched only across fragments that agree on
       -- it, so this is one value for the whole campaign by construction --
       -- which is the point: a grouped fragment and a per-trial fragment are
       -- two campaigns here, and their patients and costs are never summed.
       head.matching_call_mode,
       head.qdrant_collection,
       head.collection_points,
       head.data_snapshot_date
  FROM stats s
  -- `pa.at_run = s.last_run_id` picks the COMPLETE path row: the recursion
  -- emits one row per prefix, and the prefix that ends at the campaign's
  -- highest member id is the whole list.
  JOIN path pa ON pa.campaign_id = s.campaign_id
              AND pa.at_run = s.last_run_id
  JOIN runs head ON head.id = s.campaign_id
  -- LEFT, not INNER: a campaign whose every fragment was killed before its
  -- first patient has no inference row at all and must still be a row here,
  -- with total_patients 0. An INNER JOIN would delete exactly the campaigns
  -- worth looking at.
  LEFT JOIN cohort c ON c.cohort_campaign_id = s.campaign_id
 ORDER BY s.campaign_id DESC
""",
    ),
    # ── THE THREE-ARM COMPARISON: WHAT DID EACH CALL MODE COST ────────────
    #
    # `config.matching_call_mode()` decides whether Stage 5 sends ONE request
    # carrying several trials or one request PER TRIAL. That is the single
    # largest lever on what a patient costs, and until era 4 nothing could put a
    # cost beside the arm that produced it: the mode reached
    # `inferences.matching_call_mode` per row and `runs.matching_call_mode` per
    # run, and no registered query named either. This is the query a campaign
    # comparing the arms actually reads.
    #
    # IT GROUPS ON BOTH MODES, NOT ONE, AND THAT IS THE DESIGN RATHER THAN
    # BELT-AND-BRACES. They are two different facts:
    #
    #   runs.matching_call_mode        what the run was STAMPED with, once, on
    #                                  its main thread before its first patient.
    #                                  This is the value `run_fingerprint` gates
    #                                  a resume on and `campaign_summary`
    #                                  stitches on.
    #   inferences.matching_call_mode  what the writer read off
    #                                  `config.matching_call_mode()` at the
    #                                  moment each row was written.
    #
    # They agree on every ordinary run and CAN disagree, because the flag is a
    # module attribute that a process may move -- `bedrock_probe.py` sets it, a
    # test sets it. Grouping on the row mode alone would average two arms
    # together under one run; grouping on the run mode alone would report the
    # stamp and hide what was actually sent. Grouping on the pair makes a
    # disagreement two rows with `mode_agreement` naming it, and costs nothing
    # on a run where there is none.
    #
    # A ROW WITH NO RUN IS ITS OWN BUCKET AND IS NEVER DROPPED. `run_id IS NULL`
    # means "not part of a recorded batch run" -- every API request is one, on
    # purpose -- so a LEFT JOIN and a labelled bucket, on
    # `run_attribution_coverage`'s ruling. An INNER JOIN here would silently
    # exclude every API row from a cost comparison.
    Query(
        key='call_mode_comparison',
        heading='=== STAGE 5 CALL MODE: COST, PATIENTS AND OMISSIONS PER ARM ===',
        render='to_string',
        blank_after=True,
        # `runs` ALONE, and not RUN_TABLES: this query never reads
        # `run_metrics`, so declaring it would skip the arm comparison on a
        # database that can answer it -- `dangling_run_references`' ruling.
        requires=("runs",),
        # ADDITIVE_COLUMNS key order, then column order within a table, which is
        # what derive_requires_columns produces and what
        # tests/test_storage_schema_guards.py section 1 compares against.
        # `inferences.matching_call_mode` is era 3 and `runs.matching_call_mode`
        # is era 4, so a database can legitimately have one and not the other;
        # both are declared because the SQL names both.
        # `trial_matches.not_evaluable_reason` IS THE FOURTH AND IT WAS MISSED
        # ON THE FIRST DRAFT -- derive_requires_columns reported it, reading did
        # not. The omission CTE tests it, so on a database predating that column
        # this query raises `no such column` and report() dies at it, which is
        # precisely the defect item 38 removed and precisely what this field
        # exists to prevent.
        requires_columns=(("inferences", "matching_call_mode"),
                          ("inferences", "run_id"),
                          ("trial_matches", "not_evaluable_reason"),
                          ("runs", "matching_call_mode")),
        notes=(
            "One row per (run, observed mode). A run whose flag never moved is",
            "one row, and mode_agreement reads 'stamp matches rows'.",
            "",
            "`patients` COUNTS INFERENCE ROWS, which is run_summary's meaning of",
            "the same column name: the batch runner's resample pass writes a",
            "SECOND row for a re-run patient, so a run with a resample reports",
            "more rows than distinct patients. Comparing arms on it is still",
            "right -- the cost beside it is per row too.",
            "",
            "cost_usd IS A FLOOR WHERE rows_with_no_cost > 0. A NULL",
            "estimated_cost_usd contributes 0 to the sum and 1 to that counter;",
            "an arm compared on a floor is compared on a floor.",
            "",
            "omitted_trials COUNTS ROWS FOUND, so it is a measurement only",
            "where trials_recorded > 0. A patient with no trial_matches rows",
            "contributes 0 to both, and the two columns together say which.",
            "",
            "AN OMISSION IS A TRIAL SENT INSIDE A BATCH AND NOT ANSWERED FOR, so",
            "per-trial mode cannot produce one by construction: a request",
            "carrying one trial either answers it or fails. Zero in that arm is",
            "the expected reading and not evidence that the arm is better.",
            "",
            "'(not recorded)' IS NOT 'grouped'. It is a row or a run written",
            "before its column existed. Never fold the two together.",
            "",
            "mode_agreement 'STAMP DISAGREES WITH ROWS' is a run whose flag",
            "moved mid-process. It is a finding, not a rounding error: the two",
            "arms in that run are not commensurable and its totals are a mix.",
            "'run row is missing' is a dangling run_id -- see",
            "dangling_run_references, which names the ids.",
            "",
            "NOT CAPPED. There is one row per run per observed mode.",
        ),
        sql=f"""
    WITH omissions AS (
        SELECT tm.inference_id,
               COUNT(*)                                     AS trials_recorded,
               SUM(CASE WHEN tm.not_evaluable_reason
                             = '{CALL_MODE_OMISSION_REASON}'
                        THEN 1 ELSE 0 END)                  AS omitted_trials
          FROM trial_matches tm
         GROUP BY tm.inference_id
    )
    SELECT
        COALESCE(CAST(i.run_id AS TEXT), '(no run)')        AS run_id,
        COALESCE(r.invocation_source, '(no run)')           AS invocation_source,
        COALESCE(r.matching_call_mode, '{MODE_NOT_RECORDED_LABEL}')    AS run_mode,
        COALESCE(i.matching_call_mode, '{MODE_NOT_RECORDED_LABEL}')    AS row_mode,
        CASE
            WHEN i.run_id IS NULL                THEN 'no run to compare with'
            -- THE DANGLING CASE, NAMED RATHER THAN FOLDED INTO THE ONE BELOW.
            -- `run_id` names a `runs` row that is not in this database, which
            -- the unenforced foreign key permits and `dangling_run_references`
            -- reports. Left to fall through, it reads 'one side not recorded'
            -- and sends a reader looking for a missing COLUMN when what is
            -- missing is a ROW.
            WHEN r.id IS NULL                    THEN 'run row is missing'
            WHEN r.matching_call_mode IS NULL
              OR i.matching_call_mode IS NULL    THEN 'one side not recorded'
            WHEN r.matching_call_mode = i.matching_call_mode
                                                 THEN 'stamp matches rows'
            ELSE 'STAMP DISAGREES WITH ROWS'
        END                                                 AS mode_agreement,
        COUNT(*)                                            AS patients,
        SUM(CASE WHEN i.error IS NOT NULL AND i.error != ''
                 THEN 1 ELSE 0 END)                         AS errored,
        ROUND(SUM(COALESCE(i.estimated_cost_usd, 0)), 4)    AS cost_usd,
        SUM(CASE WHEN i.estimated_cost_usd IS NULL
                 THEN 1 ELSE 0 END)                         AS rows_with_no_cost,
        SUM(COALESCE(o.trials_recorded, 0))                 AS trials_recorded,
        SUM(COALESCE(o.omitted_trials, 0))                  AS omitted_trials,
        SUM(CASE WHEN COALESCE(o.omitted_trials, 0) > 0
                 THEN 1 ELSE 0 END)                         AS patients_with_an_omission
    FROM inferences i
    LEFT JOIN runs r ON r.id = i.run_id
    LEFT JOIN omissions o ON o.inference_id = i.id
    GROUP BY i.run_id, r.matching_call_mode, i.matching_call_mode
    -- ORDERED ON THE INTEGER `i.run_id`, NOT ON THE PROJECTED `run_id`. That
    -- alias is `CAST(... AS TEXT)` so a bare `run_id` here would sort
    -- lexically, putting run 10 before run 2 -- deterministic and wrong, which
    -- is the worse of the two ways an ordering can be wrong. `i.run_id IS NULL`
    -- ahead of it puts the no-run bucket last on both SQLite orderings rather
    -- than relying on where NULLs happen to fall.
    ORDER BY row_mode, i.run_id IS NULL, i.run_id
""",
    ),
    # THE AUDIT SIDE OF AN UNENFORCED FOREIGN KEY.
    #
    # `inferences.run_id` REFERENCES `runs(id)` in the CREATE TABLE and the
    # constraint is deliberately NOT enforced -- four reasons are argued at that
    # table in oncotriage/storage/database_logger.py, of which the operative one
    # is that `PRAGMA foreign_keys` is per CONNECTION and this project opens the
    # file from seven modules that do not set it. A ruling that a constraint
    # will not be enforced is a ruling that something else has to be able to
    # find its violations; this is that something.
    #
    # WHY IT IS NOT THE CENSUS ABOVE. That query COUNTS the dangling rows, as
    # one bucket of three, and a count is where an operator's question starts:
    # the next one is always WHICH run ids, and no aggregate can answer it. This
    # names them, with the rows and the spend attached to each, which is what
    # turns "3 rows are dangling" into something a person can act on.
    #
    # HOW A DANGLING ROW HAPPENS, so the output can be read. A run row and its
    # patient rows are written to the SAME resolved database -- main() resolves
    # once and threads it -- so the ordinary path cannot produce one. What can:
    # a `runs` row deleted by hand or by "15- Database Wipe All Tables.py",
    # which DELETEs every table with no cascade and in sqlite_master order; two
    # databases merged; or a patient row written by a process whose run row went
    # somewhere else, which is the exact failure the path-unification pass
    # closed and which this query is the standing detector for.
    #
    # `empty_or_to_string` WITH ITS OWN CLEAN MESSAGE, not `skip_if_empty`. A
    # clean audit must SAY it is clean: silence cannot be told apart from an
    # audit that did not run, and this one is skipped entirely on a database
    # with no `runs` table, where silence would be exactly the wrong reading.
    Query(
        key='dangling_run_references',
        heading='=== INFERENCE ROWS WHOSE run_id NAMES NO RUN ===',
        render='empty_or_to_string',
        blank_after=True,
        clean_message=(
            "No dangling run_id values - every inference row that names a run "
            "names one that exists"
        ),
        # `runs` ALONE, not RUN_TABLES. This query never reads `run_metrics`, so
        # declaring it would skip the audit on a database that can answer it --
        # and the database most likely to hold a dangling reference is exactly
        # the damaged one an over-declaration would refuse to examine.
        requires=("runs",),
        requires_columns=(("inferences", "run_id"),),
        notes=(
            "A row here is a real defect, not a census bucket: its run_id names",
            "a `runs` row that is not in this database. The foreign key is",
            "declared and deliberately unenforced, so nothing prevented it.",
            "",
            "run_id IS NULL is NOT reported here and is not a defect -- it means",
            "the row was written outside a batch run (every API request is one)",
            "or before run tracking existed. The census above counts those.",
        ),
        sql="""
    SELECT
        i.run_id,
        COUNT(*)                                         AS inference_rows,
        MIN(i.timestamp)                                 AS first_at,
        MAX(i.timestamp)                                 AS last_at,
        COUNT(DISTINCT i.patient_id)                     AS distinct_patients,
        ROUND(SUM(COALESCE(i.estimated_cost_usd, 0)), 4) AS cost_usd
    FROM inferences i
    LEFT JOIN runs r ON r.id = i.run_id
    WHERE i.run_id IS NOT NULL
      AND r.id IS NULL
    GROUP BY i.run_id
    ORDER BY inference_rows DESC, i.run_id
""",
    ),
    # ── THE OTHER TWO UNENFORCED REFERENCES, AUDITED THE SAME WAY ──────────
    #
    # This schema declares THREE foreign keys and enforces none of them:
    # `trial_matches.inference_id -> inferences(id)`, which has been declared
    # since the table was written; and `inferences.run_id -> runs(id)` and
    # `run_metrics.run_id -> runs(id)`, which the database-completion pass
    # declared. `dangling_run_references` above is the audit for the second.
    # These two are the audits for the first and the third, and the rule they
    # follow is the one that ruling created: a constraint that will not be
    # ENFORCED is a constraint something else has to be able to FIND the
    # violations of.
    #
    # WHY THEY ARE WORTH HAVING RATHER THAN THEORETICAL. Every one of them is
    # reachable today, by a route this project has code for:
    #
    #   * "15- Database Wipe All Tables.py" DELETEs every table `sqlite_master`
    #     reports, in catalogue order, with no cascade -- so an interrupted wipe
    #     leaves children whose parents are gone. That is not hypothetical: the
    #     wipe is one `Flag = True` away and it deletes parents FIRST.
    #   * `oncotriage/evaluation/sampling.py` copies a SUBSET of `inferences`
    #     into a second database along with the `trial_matches` and `runs` rows
    #     they reference. A subset copy is precisely where a reference is left
    #     hanging if the copy's membership rule and its child rule disagree.
    #   * Two databases merged by hand, which is what an operator does with an
    #     archived campaign file after the fresh-database procedure.
    #
    # WHAT AN ORPHANED CHILD COSTS, and it is different for the two:
    #
    #   * An orphaned `trial_matches` row is a VERDICT WITH NO PATIENT. Every
    #     query in this registry that reads trial verdicts reaches them through
    #     `inference_id`, so an orphan is invisible to all of them -- it is
    #     billed work, stored, and excluded from every number computed over the
    #     campaign. Nothing else here can report one.
    #   * An orphaned `run_metrics` row is a DEGRADATION COUNT ATTRIBUTED TO NO
    #     RUN. `run_summary` and `run_degradation_breakdown` are both driven
    #     FROM `runs`, so an orphan is dropped by both and the events it counts
    #     are silently absent from every health reading.
    #
    # `empty_or_to_string` WITH ITS OWN CLEAN MESSAGE, on
    # `dangling_run_references`' argument: a clean audit must SAY it is clean,
    # because silence cannot be told apart from an audit that did not run.
    Query(
        key='orphan_trial_matches',
        heading='=== TRIAL ROWS WHOSE inference_id NAMES NO INFERENCE ===',
        render='empty_or_to_string',
        blank_after=True,
        clean_message=(
            "No orphaned trial_matches rows - every trial verdict names an "
            "inference row that exists"
        ),
        # NO `requires` AND NO `requires_columns`. Both tables and both columns
        # are in the original CREATE statements -- neither is additive -- so
        # every database this project has ever written can answer this,
        # including one written before `runs` existed. Declaring a requirement
        # that is always satisfied would be a line that can only ever be wrong.
        notes=(
            "A row here is a real defect: a stored trial verdict that no query",
            "in this registry can reach, because every one of them joins",
            "through inference_id. It was billed for and it is excluded from",
            "every number computed over the campaign.",
            "",
            "The foreign key is declared and deliberately unenforced -- the",
            "four reasons are at the `runs` CREATE TABLE in",
            "oncotriage/storage/database_logger.py -- so nothing prevented it.",
        ),
        sql="""
    SELECT
        tm.inference_id,
        COUNT(*)                        AS trial_rows,
        COUNT(DISTINCT tm.nct_id)       AS distinct_trials,
        MIN(tm.id)                      AS first_row_id,
        MAX(tm.id)                      AS last_row_id
    FROM trial_matches tm
    LEFT JOIN inferences i ON i.id = tm.inference_id
    WHERE i.id IS NULL
    GROUP BY tm.inference_id
    ORDER BY trial_rows DESC, tm.inference_id
""",
    ),
    Query(
        key='orphan_run_metrics',
        heading='=== HEALTH ROWS WHOSE run_id NAMES NO RUN ===',
        render='empty_or_to_string',
        blank_after=True,
        clean_message=(
            "No orphaned run_metrics rows - every health row names a run that "
            "exists"
        ),
        # RUN_TABLES, because this reads BOTH of them. Unlike
        # `dangling_run_references` -- which declares `runs` alone precisely so
        # a damaged database can still be examined -- there is nothing to
        # examine here without `run_metrics`: it is the table the orphans are
        # IN. No `requires_columns`: `run_metrics.run_id` and `runs.id` are both
        # in their CREATE statements.
        requires=RUN_TABLES,
        notes=(
            "A row here is a real defect: a degradation count attributed to a",
            "run that is not in this database. run_summary and",
            "run_degradation_breakdown are both driven FROM `runs`, so these",
            "rows are dropped by both and the events they count are absent",
            "from every health reading.",
            "",
            "The clean case is the common one. A run row is written before its",
            "first flush and finalized after its last, by the same process,",
            "into the same file, under the same lock.",
        ),
        sql="""
    SELECT
        rm.run_id,
        COUNT(*)                          AS metric_rows,
        COUNT(DISTINCT rm.name)           AS distinct_counters,
        COUNT(DISTINCT rm.category)       AS distinct_categories,
        MIN(rm.written_at)                AS first_written_at,
        MAX(rm.written_at)                AS last_written_at
    FROM run_metrics rm
    LEFT JOIN runs r ON r.id = rm.run_id
    WHERE r.id IS NULL
    GROUP BY rm.run_id
    ORDER BY metric_rows DESC, rm.run_id
""",
    ),
    # ── Stage 5 split pressure (this pass) ─────────────────────────────────
    #
    # THESE TWO ARE THE WHOLE READER, AND THERE IS DELIBERATELY NO DASHBOARD
    # PANEL. The Run Health tab's declared subject is the DEGRADATION counters
    # -- run_metrics' two categories and the health_record reading over them --
    # and split pressure is neither a degradation nor a counter: a run at 40% of
    # its input budget is perfectly healthy and has nothing to say there.
    # Rendering it would either widen that tab's subject without saying so or
    # add a panel with no vocabulary of its own, and it would have to answer a
    # question this pass does not: which quantile a page a person reads should
    # show, when the honest headline is a MAX over chunks. A pressure panel is
    # its own decision and is a recorded follow-up; `python "16- Database
    # Query.py"` is where these run today.
    #
    # BOTH DECLARE requires_columns AND NEITHER DECLARES requires. They read
    # `inferences` alone -- run_id is a COLUMN of it, so no run TABLE is
    # touched and a database that has the columns can answer them whether or
    # not the run-identity pass has ever opened it. Every column named in the
    # two `requires_columns` lists below is an INFERENCE_COLUMN_ADDITIONS entry
    # -- a column a writer adds on open and which the production database on
    # this machine, last written before these passes, does not have. Selecting
    # one that is absent raises `no such column`, and report() runs its
    # registry with the first raise taking the process down; see
    # Query.requires_columns for why that is a declaration and not a shrug.
    Query(
        key='stage5_input_packing_pressure',
        heading='=== STAGE 5: INPUT PACKING PRESSURE, PER RUN ===',
        render='to_string',
        blank_after=True,
        requires_columns=(("inferences", "run_id"),
                          ("inferences", "llm_classifier_packing")),
        notes=(
            "ONE ROW PER CHUNK feeds this, not one per patient: the packer's",
            "budget is spent per REQUEST, and a patient sent as three chunks",
            "has three separate distances from the guard.",
            "",
            "pressure is estimated input tokens / the EFFECTIVE budget of that",
            "chunk's own inference. 1.0 is exactly at the budget. The effective",
            "budget is not always the configured one -- the packer raises it",
            "when the chunk cap binds -- which is why relaxed_inferences is",
            "beside it and why the ratio is never taken across inferences.",
            "",
            "headroom is in TOKENS and is the tightest chunk of the run. It is",
            "stated beside the ratio because a ratio alone cannot say whether",
            "0.98 was 200 tokens of slack or 20.",
            "",
            "unpacked_inferences is one of the two populations this query",
            "CANNOT measure: rows whose packer did not run to completion,",
            "which is every Stage 5 failure return (the chunk list is a plan,",
            "and Stage 5 publishes it on the success return only) plus every",
            "row written before the packer existed. It is counted rather than",
            "filtered away, because an omission reads as an absence of",
            "pressure.",
            "",
            "bypassed_inferences is the OTHER one, and it is split out because",
            "it is not the same finding. These rows are healthy: something",
            "partitioned the batch instead of the packer -- per-trial call",
            "mode does -- so there is no budget to be under and no pressure to",
            "report, rather than a measurement that went missing. Folded into",
            "unpacked_inferences they read as failures. The two together are",
            "the rows the ratios above say nothing about; a run whose",
            "inferences are all bypassed has no packing pressure BECAUSE IT",
            "DID NOT PACK, which is a different sentence from low pressure and",
            "from a lost measurement alike.",
            "",
            "NEITHER BUCKET IS THE END OF THE STORY ANY MORE, and that is what",
            "changed under them. Both still mean exactly what they say about",
            "THE PACKER -- and stage5_input_request_pressure answers the",
            "question underneath, from a per-row scalar every Stage 5 return",
            "carries: how close did the largest single request come to the",
            "configured budget. Read the two together. A run whose inferences",
            "are all bypassed reads unpacked here and reports real per-request",
            "pressure there; a run whose inferences all failed reads unpacked",
            "here and reports the pressure its plan carried there.",
            "",
            "over_budget_chunks counts chunks that could not be made to fit by",
            "any amount of packing -- a single trial larger than the whole",
            "allowance. That is the guard FIRING, not approaching.",
        ),
        sql=f"""
    SELECT
        COALESCE(CAST(i.run_id AS TEXT), '{NO_RUN_LABEL}')  AS run,
        COUNT(DISTINCT i.id)                                  AS inferences,
        COUNT(DISTINCT CASE WHEN {_PACK_BUDGET_SQL} IS NULL
                             AND {_PACK_BYPASSED_SQL} IS NULL
                            THEN i.id END)                    AS unpacked_inferences,
        COUNT(DISTINCT CASE WHEN {_PACK_BYPASSED_SQL} IS NOT NULL
                            THEN i.id END)                    AS bypassed_inferences,
        SUM(CASE WHEN c.value IS NOT NULL THEN 1 ELSE 0 END)   AS chunks,
        MIN({_PACK_BUDGET_SQL})                               AS budget_min,
        MAX({_PACK_BUDGET_SQL})                               AS budget_max,
        MAX({_PACK_CHUNK_TOKENS_SQL})                         AS peak_chunk_tokens,
        ROUND(MAX({_INPUT_PRESSURE_SQL}), 4)                  AS peak_pressure,
        ROUND(AVG({_INPUT_PRESSURE_SQL}), 4)                  AS mean_pressure,
        MIN({_PACK_BUDGET_SQL} - {_PACK_CHUNK_TOKENS_SQL})    AS min_headroom_tokens,
        SUM(CASE WHEN {_INPUT_PRESSURE_SQL} >= 0.75
                 THEN 1 ELSE 0 END)                           AS chunks_at_75pct,
        SUM(CASE WHEN {_INPUT_PRESSURE_SQL} >= 0.90
                 THEN 1 ELSE 0 END)                           AS chunks_at_90pct,
        SUM(CASE WHEN json_extract(c.value, '$.over_budget')
                 THEN 1 ELSE 0 END)                           AS over_budget_chunks,
        COUNT(DISTINCT CASE WHEN {_pack_field_sql('cap_relaxed_budget')}
                            THEN i.id END)                    AS relaxed_inferences
    FROM inferences i
    LEFT JOIN json_each({_PACK_JSON_SQL}, '$.chunks') c
    GROUP BY run
    ORDER BY peak_pressure DESC, run
""",
    ),
    # ── THE INPUT GUARD, PER ROW, ON EVERY ARM AND EVERY OUTCOME ──────────
    #
    # A SIBLING RATHER THAN MORE COLUMNS ON THE QUERY ABOVE, and its own notes
    # are what settle that: "ONE ROW PER CHUNK feeds this, not one per
    # patient". That query is an aggregate over the packer's chunk list, keyed
    # on a JSON array, measured against the EFFECTIVE budget of each chunk's
    # own inference. This one is an aggregate over INFERENCE ROWS, keyed on two
    # scalar columns, measured against the CONFIGURED budget. Folding them
    # together would produce rows whose counts mean two different things --
    # `inferences` counted once per chunk in half the columns and once per
    # patient in the other half -- which is the shape `campaign_summary` had to
    # be repaired for one table over.
    #
    # GROUPED BY (run, ARM), which the query above is not and cannot be. The
    # two call modes have genuinely different per-request input profiles by
    # design: grouped packs several trials into one request up to the budget,
    # per-trial sends prefix-plus-one and is affordable only because that
    # prefix is cached. Averaging them into one pressure figure would describe
    # neither. It is the same (run, arm) grouping `call_mode_comparison` and
    # `stage5_cache_effectiveness` use, and through the same
    # MODE_NOT_RECORDED_LABEL bucket, so a reader can put cost, cache hit rate
    # and input pressure beside each other row for row.
    Query(
        key='stage5_input_request_pressure',
        heading='=== STAGE 5: INPUT REQUEST PRESSURE, PER RUN AND ARM ===',
        render='to_string',
        blank_after=True,
        # HAND-DECLARED, in ADDITIVE_COLUMNS key order then column order within
        # a table, which is what derive_requires_columns produces and what
        # tests/test_storage_schema_guards.py compares this against. Every one
        # is an INFERENCE_COLUMN_ADDITIONS entry, so selecting it on a database
        # a writer has not opened since era 6 raises `no such column` and takes
        # report() down with it -- the defect item 38 removed from File 16.
        # `error` is deliberately absent: it is a BASE column of `inferences`,
        # present since the table was created, so it is not an additive
        # requirement and declaring it would disagree with the derivation.
        requires_columns=(
            ("inferences", "llm_classifier_input_budget"),
            ("inferences", "llm_classifier_input_tokens_estimated"),
            ("inferences", "matching_call_mode"),
            ("inferences", "run_id"),
        ),
        notes=(
            "ONE ROW PER PATIENT, and the scalar is a MAXIMUM over that",
            "patient's requests -- the largest single request Stage 5 planned",
            "for them. MATCHING_INPUT_TOKEN_BUDGET is a budget on ONE request,",
            "so the biggest request is the one that approaches it; a sum",
            "across a patient's chunks would rise with the chunk count, which",
            "is the packer working rather than pressure.",
            "",
            "THIS IS THE QUESTION stage5_input_packing_pressure ABOVE CANNOT",
            "ANSWER FOR TWO WHOLE POPULATIONS, and both of them matter more",
            "than the ones it can. That query reads llm_classifier_packing,",
            "which Stage 5 publishes on its SUCCESS return only and which",
            "per-trial mode fills with a bypass note and no numbers. So a",
            "FAILED row had no input figure at all -- and a run that failed",
            "because its input was enormous is the row most worth asking -- and",
            "the SHIPPED call mode had none on its successful rows either.",
            "",
            "pressure is the estimate over llm_classifier_input_budget, the",
            "CONFIGURED budget recorded on the row. Above 1.0 is real and is",
            "not an error: the packer relaxes its budget when the chunk cap",
            "binds, and a single trial larger than the whole allowance ships",
            "anyway. The relaxation IS the pressure, so it is measured against",
            "what was configured rather than against what the packer settled",
            "for -- that figure stays in llm_classifier_packing, where it",
            "describes the packer.",
            "",
            "budget_min and budget_max are both shown for the reason the",
            "output query shows its thresholds twice: a campaign that spanned",
            "a config change says so here rather than averaging across it.",
            "",
            "failed_inferences is how many of the rows behind these numbers",
            "carry a recorded error -- which is a Stage 5 failure return in",
            "the rows that have a scalar, and can be an UPSTREAM failure in",
            "the rows that do not, since those never entered Stage 5 at all.",
            "Read it beside unmeasured: failed AND measured is the population",
            "this query was built for, and failed AND unmeasured is a run that",
            "died before the judge. It is a BREAKDOWN, not an exclusion -- the",
            "pressure figures are over every measured row of the group, which",
            "is the whole point of measuring at plan time.",
            "",
            "unmeasured is rows that never entered Stage 5 (no candidates, or",
            "a failure upstream of it) or that predate era 6. SQL aggregates",
            "skip NULL, so a group reading unmeasured = inferences has no",
            "pressure to report -- which is not the same as low pressure.",
            "",
            "THE PER-CALL FIGURES ARE NOT HERE and are not duplicated",
            "anywhere: llm_classifier_call_details carries one row per request",
            "issued, and stage5_cache_effectiveness reads it.",
        ),
        sql=f"""
    SELECT
        run,
        arm,
        COUNT(*)                                              AS inferences,
        SUM(CASE WHEN estimate IS NULL THEN 1 ELSE 0 END)     AS unmeasured,
        SUM(CASE WHEN failed THEN 1 ELSE 0 END)               AS failed_inferences,
        MIN(budget)                                           AS budget_min,
        MAX(budget)                                           AS budget_max,
        MAX(estimate)                                         AS peak_request_tokens,
        ROUND(MAX(estimate * 1.0 / budget), 4)                AS peak_pressure,
        ROUND(AVG(estimate * 1.0 / budget), 4)                AS mean_pressure,
        MIN(budget - estimate)                                AS min_headroom_tokens,
        SUM(CASE WHEN estimate * 1.0 / budget >= 0.75
                 THEN 1 ELSE 0 END)                           AS inferences_at_75pct,
        SUM(CASE WHEN estimate * 1.0 / budget >= 0.90
                 THEN 1 ELSE 0 END)                           AS inferences_at_90pct,
        SUM(CASE WHEN estimate > budget THEN 1 ELSE 0 END)    AS inferences_over_budget
    FROM (
        SELECT
            COALESCE(CAST(i.run_id AS TEXT), '{NO_RUN_LABEL}') AS run,
            COALESCE(i.matching_call_mode,
                     '{MODE_NOT_RECORDED_LABEL}')             AS arm,
            i.llm_classifier_input_tokens_estimated           AS estimate,
            i.llm_classifier_input_budget                     AS budget,
            CASE WHEN i.error IS NOT NULL AND i.error <> ''
                 THEN 1 ELSE 0 END                            AS failed
        FROM inferences i
    ) x
    GROUP BY run, arm
    ORDER BY peak_pressure DESC, run, arm
""",
    ),
    Query(
        key='stage5_output_split_pressure',
        heading='=== STAGE 5: OUTPUT SPLIT PRESSURE, PER RUN ===',
        render='to_string',
        blank_after=True,
        requires_columns=(
            ("inferences", "run_id"),
            ("inferences", "llm_classifier_output_split_threshold"),
            ("inferences", "llm_classifier_output_ceiling"),
            ("inferences", "llm_classifier_output_tokens_estimated"),
            ("inferences", "llm_classifier_call_details"),
            ("inferences", "llm_classifier_truncation_splits"),
        ),
        notes=(
            "TWO GUARDS, TWO RATIOS, AND THEY ARE NOT INTERCHANGEABLE.",
            "",
            "split_pressure is the PROACTIVE guard: the whole batch's",
            "pre-call output estimate over the threshold that was in force.",
            "At 1.0 the batch is halved before the first request. The",
            "threshold is on the row rather than recomputed, because both",
            "constants behind it have moved once already and a ratio against",
            "an unrecorded threshold is uninterpretable afterwards.",
            "",
            "ceiling_pressure is the REACTIVE guard: the LARGEST SINGLE",
            "response of the run over the per-request output ceiling. Its",
            "numerator comes from llm_classifier_call_details, per call --",
            "NOT from llm_classifier_output_tokens, which is summed across",
            "chunks and cannot be compared with a per-request ceiling. At 1.0",
            "a response is cut off, comes back finish_reason 'length' and the",
            "chunk is halved.",
            "",
            "A threshold column that is the same in every row of a run is the",
            "expected reading; min and max are both shown so a campaign that",
            "spanned a config change says so rather than averaging across it.",
            "",
            "unmeasured is rows that never entered Stage 5 (no candidates, or",
            "a failure upstream of it) or that predate these columns. The",
            "pressures above are over the measured rows only -- SQL",
            "aggregates skip NULL -- so a run reading unmeasured = inferences",
            "has no pressure to report, which is not the same as low pressure.",
        ),
        sql=f"""
    SELECT
        run,
        COUNT(*)                                              AS inferences,
        SUM(CASE WHEN threshold IS NULL THEN 1 ELSE 0 END)    AS unmeasured,
        MIN(threshold)                                        AS split_threshold_min,
        MAX(threshold)                                        AS split_threshold_max,
        MIN(ceiling)                                          AS output_ceiling_min,
        MAX(ceiling)                                          AS output_ceiling_max,
        MAX(estimate)                                         AS peak_estimate,
        ROUND(MAX(estimate * 1.0 / threshold), 4)             AS peak_split_pressure,
        MIN(threshold - estimate)                             AS min_split_headroom_tokens,
        SUM(CASE WHEN estimate > threshold THEN 1 ELSE 0 END) AS inferences_over_threshold,
        SUM(COALESCE(splits, 0))                              AS splits_spent,
        MAX(peak_call)                                        AS peak_call_output_tokens,
        ROUND(MAX(peak_call * 1.0 / ceiling), 4)              AS peak_ceiling_pressure,
        MIN(ceiling - peak_call)                              AS min_ceiling_headroom_tokens
    FROM (
        SELECT
            COALESCE(CAST(i.run_id AS TEXT), '{NO_RUN_LABEL}') AS run,
            i.llm_classifier_output_split_threshold           AS threshold,
            i.llm_classifier_output_ceiling                   AS ceiling,
            i.llm_classifier_output_tokens_estimated          AS estimate,
            i.llm_classifier_truncation_splits                AS splits,
            (SELECT MAX(json_extract(d.value, '$.completion_tokens'))
               FROM json_each(CASE WHEN json_valid(i.llm_classifier_call_details)
                                   THEN i.llm_classifier_call_details
                                   ELSE '[]' END) d)          AS peak_call
        FROM inferences i
    ) x
    GROUP BY run
    ORDER BY peak_split_pressure DESC, peak_ceiling_pressure DESC, run
""",
    ),
    # ── DID THE SHARED PREFIX ACTUALLY GET REUSED ─────────────────────────
    #
    # THE MEASUREMENT PER-TRIAL MODE IS ONLY VIABLE ON, AND IT HAD NO READER.
    # Per-trial mode multiplies the number of Stage 5 requests by
    # MAX_TRIALS_FOR_EVALUATION and is affordable only because the system
    # message -- instructions plus the whole patient record, the bulk of every
    # request -- is billed at the CACHED rate from the second call of a patient
    # on. `oncotriage/agent/evaluation.py` issues a dedicated warmup whose only
    # job is to write that prefix, and records what every request was billed in
    # `llm_classifier_call_details`. Nothing registered read it, so the one
    # question the mode has to answer before a campaign -- IS THE DISCOUNT
    # LANDING -- was answerable only by parsing JSON by hand.
    #
    # THE ROW COLUMN AND THE LEDGER ANSWER DIFFERENT QUESTIONS AND BOTH ARE
    # HERE. `inferences.llm_classifier_cached_input_tokens` is one scalar per
    # patient and cannot say WHEN the cache warmed: 5,000 cached tokens over
    # three calls is equally consistent with a cache that warmed after the first
    # request and one that never warmed, and those have opposite implications
    # for what the mode costs. The ledger is one row per request ISSUED, so the
    # rate below is over calls rather than over patients.
    #
    # NULL AND 0 ARE DIFFERENT READINGS AND THE COLUMNS SAY SO, which is the
    # whole reason this is not one hit-rate number. `cached_tokens` NULL means
    # the response carried no `prompt_tokens_details.cached_tokens` at all --
    # a stub, a pre-field recording, a provider that does not report it -- and 0
    # means the response DID report and the provider cached nothing. Averaging
    # them would let a provider that has gone silent read as a provider that is
    # not caching, and only the second is a reason to turn the mode off. So the
    # rate is computed over REPORTING calls only, both numerator and
    # denominator, and the silent calls are counted beside it. A run whose
    # `wave_calls_silent` equals `wave_calls` has no hit rate, which is not the
    # same as a hit rate of zero.
    #
    # THE WARMUP IS REPORTED BESIDE THE WAVE, NEVER INSIDE IT. It is the request
    # that WRITES the prefix, so it reports 0 cached on a perfectly healthy
    # patient -- and folding it in would drag every arm's rate down by one
    # call's worth of prompt and make a healthy warmup look like a cache miss.
    # Read the two columns together: `warmup_cache_hit_rate` near 0 with
    # `wave_cache_hit_rate` high is the DESIGNED outcome (the warmup paid full
    # price for the prefix and the wave got it discounted); both near 0 is a
    # provider that is not caching; a HIGH warmup rate means the prefix was
    # already warm when the warmup ran, which on a per-patient key should not
    # happen and is worth investigating.
    #
    # GROUPED MODE IS IN THE TABLE ON PURPOSE. It issues one request per patient
    # (or one per packed chunk), so its prefix is reused only across chunks and
    # its rate is expected to be low or absent. That is the BASELINE the
    # per-trial arm has to beat for the mode to pay for itself, and a
    # comparison with the baseline missing is not a comparison.
    #
    # `stage5_cache_effectiveness` AND `call_mode_comparison` GROUP THE SAME WAY
    # ON PURPOSE -- (run, mode), both bucketing an unrecorded mode under
    # MODE_NOT_RECORDED_LABEL -- so a reader can put cost beside hit rate row
    # for row. That is what the shared constant is for.
    #
    # IT DECLARES `inferences.run_id` AND `matching_call_mode` AND NOT `runs`.
    # It never joins the run table: the arm is on the inference row, written by
    # the same `config.matching_call_mode()` the node reads, so this query
    # answers on a database that has no run tables at all. Declaring `runs`
    # would skip it on exactly the databases it can still measure --
    # `dangling_run_references`' ruling.
    Query(
        key='stage5_cache_effectiveness',
        heading='=== STAGE 5: PROMPT-CACHE EFFECTIVENESS, PER RUN AND ARM ===',
        render='to_string',
        blank_after=True,
        # ADDITIVE_COLUMNS key order, then column order within a table, which is
        # what derive_requires_columns produces and what
        # tests/test_storage_schema_guards.py section 1 compares against.
        # ALL FOUR MAKE THE SQL UNPARSEABLE WHEN ABSENT -- they are projected,
        # grouped on or read inside json_each, not merely selected -- so a
        # database predating any one of them must skip this query rather than
        # take report() down at it, which is the defect item 38 removed.
        requires_columns=(
            ("inferences", "llm_classifier_cached_input_tokens"),
            ("inferences", "llm_classifier_call_details"),
            ("inferences", "matching_call_mode"),
            ("inferences", "run_id"),
        ),
        notes=(
            "ONE ROW PER (run, arm). Read it beside call_mode_comparison,",
            "which groups the same way: that one has the cost, this one has",
            "the reason the cost is what it is.",
            "",
            "THE RATES ARE OVER REPORTING CALLS ONLY. A call whose response",
            "carried no cached_tokens field is in wave_calls_silent and in",
            "NEITHER the numerator nor the denominator. wave_calls_silent ==",
            "wave_calls means there is no hit rate to read -- which is not a",
            "hit rate of zero, and the two must never be folded together.",
            "",
            "wave_cache_hit_rate IS cached/prompt OVER THE WAVE, so it is",
            "bounded by the share of a request that IS the shared prefix. It",
            "cannot reach 1.0: the trial block and the response schema are",
            "never cached. A per-trial run in which it is near zero while",
            "warmup_calls is non-zero means the warmup is not writing the",
            "prefix, and the mode is paying full price N times per patient.",
            "",
            "warmup_cache_hit_rate NEAR 0 IS THE HEALTHY READING, not a",
            "defect: the warmup is the request that writes the prefix. A HIGH",
            "one means it found the prefix ALREADY WARM, and there are three",
            "causes -- two of them benign and neither of them a defect:",
            "  * A PARSE RETRY. A malformed body ends the node and the graph",
            "    re-enters it, issuing a FRESH warmup against a prefix the",
            "    failed attempt's own wave has already written N times.",
            "    inferences.llm_classifier_retries > 0 is the tell, and the",
            "    stored ledger describes only the LAST attempt -- so the",
            "    earlier attempts' calls are billed and are not in it.",
            "  * THE SAME PATIENT RE-RUN. The cache key is derived from the",
            "    system prompt, so a resample row, a resumed patient or a",
            "    re-scored one asks to be routed to the machine that already",
            "    holds its prefix. That is the key working.",
            "  * ANYTHING ELSE is worth investigating before the wave rate",
            "    beside it is trusted, because it means two patients shared a",
            "    prefix that was supposed to be per-patient.",
            "The rate alone cannot tell the three apart. Read it beside",
            "llm_classifier_retries and beside whether the patient has more",
            "than one row.",
            "",
            "warmup_calls IS 0 IN GROUPED MODE BY CONSTRUCTION. A grouped run",
            "reporting a non-zero one is a row written by a process whose flag",
            "moved mid-patient; see call_mode_comparison's mode_agreement.",
            "",
            "rows_silent_on_cache COUNTS PATIENTS, the three columns beside it",
            "partition the same population: NULL (no response of that run",
            "reported the field), 0 (reported and nothing was cached) and > 0.",
            "A pre-era row is NULL for a third reason -- the column did not",
            "exist -- and this query cannot tell those apart; the ledger",
            "columns can, because a row with no ledger has no calls either.",
            "",
            "NOT CAPPED. There is one row per run per observed arm.",
        ),
        sql=f"""
    WITH per_row AS (
        SELECT
            i.run_id                                            AS run_key,
            COALESCE(CAST(i.run_id AS TEXT), '{NO_RUN_LABEL}')  AS run,
            COALESCE(i.matching_call_mode,
                     '{MODE_NOT_RECORDED_LABEL}')               AS call_mode,
            i.llm_classifier_cached_input_tokens                AS row_cached,
            -- THE WARMUP FLAG IS READ FOR TRUTH, NOT FOR PRESENCE. The writer
            -- emits `warmup: True` on that row and the key on no other, so
            -- `IS NOT NULL` would be equivalent today -- and would also class a
            -- future `warmup: false` as a warmup. COALESCE to 0 reads the value.
            (SELECT COUNT(*) FROM json_each({_LEDGER_JSON_SQL}) d
              WHERE COALESCE(json_extract(d.value, '$.warmup'), 0))
                                                                AS warmups,
            (SELECT COUNT(*) FROM json_each({_LEDGER_JSON_SQL}) d
              WHERE NOT COALESCE(json_extract(d.value, '$.warmup'), 0))
                                                                AS waves,
            (SELECT COUNT(*) FROM json_each({_LEDGER_JSON_SQL}) d
              WHERE NOT COALESCE(json_extract(d.value, '$.warmup'), 0)
                AND json_extract(d.value, '$.cached_tokens') IS NOT NULL)
                                                                AS waves_reporting,
            (SELECT SUM(json_extract(d.value, '$.cached_tokens'))
               FROM json_each({_LEDGER_JSON_SQL}) d
              WHERE NOT COALESCE(json_extract(d.value, '$.warmup'), 0)
                AND json_extract(d.value, '$.cached_tokens') IS NOT NULL)
                                                                AS wave_cached,
            (SELECT SUM(json_extract(d.value, '$.prompt_tokens'))
               FROM json_each({_LEDGER_JSON_SQL}) d
              WHERE NOT COALESCE(json_extract(d.value, '$.warmup'), 0)
                AND json_extract(d.value, '$.cached_tokens') IS NOT NULL)
                                                                AS wave_prompt,
            (SELECT SUM(json_extract(d.value, '$.cached_tokens'))
               FROM json_each({_LEDGER_JSON_SQL}) d
              WHERE COALESCE(json_extract(d.value, '$.warmup'), 0)
                AND json_extract(d.value, '$.cached_tokens') IS NOT NULL)
                                                                AS warmup_cached,
            (SELECT SUM(json_extract(d.value, '$.prompt_tokens'))
               FROM json_each({_LEDGER_JSON_SQL}) d
              WHERE COALESCE(json_extract(d.value, '$.warmup'), 0)
                AND json_extract(d.value, '$.cached_tokens') IS NOT NULL)
                                                                AS warmup_prompt
        FROM inferences i
    )
    SELECT
        run,
        call_mode,
        COUNT(*)                                             AS inferences,
        SUM(CASE WHEN row_cached IS NULL THEN 1 ELSE 0 END)  AS rows_silent_on_cache,
        SUM(CASE WHEN row_cached = 0 THEN 1 ELSE 0 END)      AS rows_reporting_no_cache,
        SUM(CASE WHEN row_cached > 0 THEN 1 ELSE 0 END)      AS rows_reporting_cache,
        SUM(warmups)                                         AS warmup_calls,
        SUM(waves)                                           AS wave_calls,
        SUM(waves) - SUM(waves_reporting)                    AS wave_calls_silent,
        SUM(wave_prompt)                                     AS wave_prompt_tokens,
        SUM(wave_cached)                                     AS wave_cached_tokens,
        ROUND(SUM(wave_cached) * 1.0 / NULLIF(SUM(wave_prompt), 0), 4)
                                                             AS wave_cache_hit_rate,
        SUM(warmup_prompt)                                   AS warmup_prompt_tokens,
        SUM(warmup_cached)                                   AS warmup_cached_tokens,
        ROUND(SUM(warmup_cached) * 1.0 / NULLIF(SUM(warmup_prompt), 0), 4)
                                                             AS warmup_cache_hit_rate
    FROM per_row
    GROUP BY run_key, call_mode
    -- ORDERED ON THE INTEGER `run_key`, NOT ON THE PROJECTED `run`. That alias
    -- is CAST(... AS TEXT) so a bare `run` here would put run 10 before run 2 --
    -- deterministic and wrong, which is the worse of the two ways an ordering
    -- can be wrong. `run_key IS NULL` ahead of it puts the no-run bucket last on
    -- both SQLite orderings rather than relying on where NULLs happen to fall.
    -- call_mode_comparison orders the same way for the same reason.
    ORDER BY call_mode, run_key IS NULL, run_key
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


class MissingDatabaseError(RuntimeError):
    """There is no database at that path, and this module will not create one.

    A ``RuntimeError`` subclass and deliberately NOT a ``sqlite3.Error``, on
    ``MissingTableError``'s footing and for the same reason: a caller with a
    broad ``except sqlite3.Error`` around its reads would swallow this and go on
    to report an empty result, which is exactly the answer that must not be
    produced for a path that does not exist.

    It exists because the alternative diagnosis is useless. Opening a
    ``mode=ro`` URI on an absent file raises
    ``sqlite3.OperationalError: unable to open database file`` -- which does not
    name the file, does not say whether the problem is the path or the
    permissions, and is the same message a dozen other faults produce.
    """


def connect(db_path=None):
    """Open a READ-ONLY connection to the inference database.

    Returns a ``sqlite3.Connection`` opened through a ``file:...?mode=ro`` URI.
    The caller closes it -- ``report()`` does not, because a caller that wants
    to ask a follow-up question after the report should not have to reopen.

    WHY READ-ONLY, AND WHAT IT REPLACES. This was a plain
    ``sqlite3.connect(path)``, which CREATES an empty database when the path
    does not exist. So a mistyped path, a stale ``ONCOTRIAGE_INFERENCES_DB`` or
    a `--db` pointed one directory wrong did not fail: it brought a database
    into existence, `report()` ran the whole registry against it, and forty-odd
    queries printed empty frames and clean-audit messages that are
    indistinguishable from a real report on a healthy pipeline. A reader has no
    way to tell that from "the campaign produced nothing", and the second
    reading is the one a person reaches for.

    THE PRECEDENT IS ``oncotriage/dashboard/data.py:_readonly_connection``,
    which is documented there for the same reason and which this deliberately
    matches rather than re-argues -- including the ``?`` escaping, because a
    literal question mark in a path would otherwise start the URI's query
    string.

    IT ALSO MAKES THE MODE MATCH THE CONTRACT. Every Query in this registry is a
    SELECT and this module's docstring calls itself read-only; a connection that
    could write was a promise nothing enforced.

    THE FILE-EXISTS TEST IS SEPARATE FROM THE OPEN, and it is not redundant with
    it: ``mode=ro`` on an absent path raises an OperationalError naming nothing,
    while this raises ``MissingDatabaseError`` naming the path and how it was
    chosen. The remaining ``OperationalError`` cases -- unreadable permissions,
    a corrupt header, a WAL database whose ``-shm`` cannot be created in a
    read-only directory -- are left to propagate as themselves, because each has
    a different remedy and inventing one message for all of them would be worse
    than sqlite's.

    ONE KNOWN LIMIT, MEASURED. A WAL database whose ``-wal`` file is live and
    whose ``-shm`` is absent CANNOT be opened read-only inside a directory that
    is not writable -- SQLite needs to create the shared-memory index and cannot
    (measured on sqlite 3.45.3: ``unable to open database file``). That is the
    crashed-writer-plus-read-only-directory case; an ordinary writable data
    directory recreates the ``-shm`` and reads fine, which was measured too. It
    is stated here rather than worked around because the workaround --
    ``immutable=1`` -- lies to SQLite about a file another process may be
    writing.
    """
    resolved = resolve_query_db_path(db_path)
    if not os.path.isfile(resolved):
        raise MissingDatabaseError(
            f"No database at {resolved!r}.\n"
            f"    These queries are read-only and will not create one -- an "
            f"empty database would answer every question in this registry with "
            f"an empty frame, which reads exactly like a campaign that produced "
            f"nothing.\n"
            f"    The path came from " + ("the db_path argument."
                                          if db_path is not None else
                                          "oncotriage.paths.inferences_path "
                                          "(override it with "
                                          "ONCOTRIAGE_INFERENCES_DB)."))
    uri = "file:" + os.path.abspath(resolved).replace("?", "%3f") + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


#------------------------------------------------------------------------------


class MissingTableError(RuntimeError):
    """A query was asked of a database that does not have a table it names.

    A ``RuntimeError`` subclass and deliberately NOT a ``ValueError`` or a
    ``sqlite3.Error``, on ``UnknownModelPricingError``'s precedent: a caller with
    a broad ``except sqlite3.Error`` around its reads -- the shape every writer
    in this project has -- would swallow this and report an empty result, which
    is the one answer that must not be produced. "This database cannot answer
    that question yet" and "the answer is nothing" are different findings.
    """


def available_tables(conn) -> frozenset:
    """Every table name in the database this connection is open on.

    One ``sqlite_master`` read, so a caller checking many queries pays for it
    once. ``table_names(conn)`` already asks the same question and is left alone:
    it returns raw one-tuples because File 16 PRINTED them, and changing its
    shape would change ``report()``'s output.
    """
    return frozenset(row[0] for row in fetch_raw(conn, TABLE_LIST_SQL))


def table_columns(conn, table) -> frozenset:
    """Every column name on `table`, or an empty set when the table is absent.

    ``PRAGMA table_info`` on a table that does not exist returns no rows rather
    than raising, which is why the caller must ask about the TABLE first: an
    empty set here means "no such table" and "a table with no columns" alike,
    and only the first is possible in SQLite.
    """
    return frozenset(row[1] for row in
                     fetch_raw(conn, f"PRAGMA table_info({table})"))


# ---------------------------------------------------------------------------
# WHICH ADDITIVE COLUMNS A QUERY NAMES, DERIVED FROM ITS OWN SQL
# ---------------------------------------------------------------------------
#
# `requires_columns` STAYS HAND-WRITTEN ON THE Query RECORD. This is the
# CHECKER, not the source: tests/test_storage_schema_guards.py asserts that
# every registered query's declaration equals what this derives, so a query can
# no longer ship naming an additive column it forgot to declare. The ruling in
# Query.requires_columns -- that a column requirement is NOT derivable from a
# table requirement -- is untouched and is a different statement; that one is
# about `runs` existing not implying `inferences.run_id` existing, and this is
# about reading the SQL.
#
# WHY IT HAD TO EXIST. The guard was built for ADDITIVE absence and worked. Then
# the gpt4o -> llm_classifier rename renamed nine columns in place, and every
# older query naming one became a query against a column the production database
# does not have -- with no declaration, because nobody re-read forty-eight
# queries. MEASURED: `report()` against the production database died at its
# SECOND query on `no such column: llm_classifier_evaluation_time`, having
# printed eight lines of forty-eight queries' worth of report. Twenty-one
# queries were affected. A rule kept by hand across a registry this size is a
# rule that is already broken.
#
# THE DERIVATION, and every step of it is there because a simpler version was
# wrong on this registry:
#
#   1. Strip `--` comments and single-quoted literals. `WHERE eligible =
#      'not_evaluable'` must not make `not_evaluable` an identifier, and
#      `not_evaluable_reason` IS an additive column of trial_matches.
#   2. Bind table aliases from FROM/JOIN. `FROM runs r` binds r -> runs.
#      A subquery alias (`) p ON ...`) binds NOTHING, which is the safe answer:
#      `p.patients` then resolves to no base table and derives nothing.
#   3. A pair (table, column) is derived only if the query REFERENCES that
#      table. Without this, `run_degradation_breakdown` -- which reads `runs`
#      and `run_metrics` and never touches `inferences` -- derives
#      `inferences.run_id` from the bare `run_id` that is a column of
#      run_metrics, and the query gets skipped on databases that can answer it.
#   4. A QUALIFIED reference wins over a bare one. `run_summary` selects
#      `r.llm_classifier_prompt_version` where r is `runs`, and that column name
#      is ALSO in INFERENCE_COLUMN_ADDITIONS -- a bare-name match derives
#      `inferences.llm_classifier_prompt_version`, which that query does not
#      read, and skips it on every pre-migration database for a column it never
#      names. This is handled by step 6's stripping and NOT by a special branch:
#      a first version carried an explicit "every qualifier names another table,
#      so skip" test, and a revert harness showed that DELETING it changes the
#      answer for no query in the registry and for no shape this function is
#      written against. The one input where the two differ -- a name that is
#      both foreign-qualified AND bare -- is SQL that SQLite itself rejects
#      ("ambiguous column name"), so the branch could only ever have guarded a
#      query that cannot run. It was deleted rather than kept with a control
#      nothing could fire.
#   5. `AS <name>` is stripped before the bare search, so `COUNT(*) AS run_id`
#      is not a reference. It is stripped rather than subtracted, so
#      `COALESCE(query_expansion_path, '(not reported)') AS query_expansion_path`
#      -- where the same token is both a real read and an output name -- still
#      derives, correctly.
#   6. Every `<qualifier>.<name>` occurrence is stripped before the bare search
#      too, so a column reached ONLY through another table's alias leaves no
#      bare token behind and derives nothing. That is what makes step 4 true.
#
# Steps 3, 4 and 5 each removed a FALSE POSITIVE measured on this registry, not
# a hypothetical one. The first version of this function reported 25 mismatches;
# two of them were queries that read no such column.
#
# WHAT IT DOES NOT DO, STATED. It is a lexical reader, not a SQL parser.
#   - A bare additive name in a query that references its table is derived even
#     if it belongs to another table also referenced there. That is an
#     OVER-derivation, so its cost is a query skipped on a database that could
#     have answered it -- never a crash. Deliberately the safe direction.
#   - A column reached through a name this module cannot see (a `WITH` clause
#     aliasing a base table, a quoted `"identifier"`, a name built by string
#     concatenation) is not derived. The registry uses none of those, and the
#     standing test is what says so: it compares ALL forty-eight declarations,
#     so a query written in a shape this cannot read fails as a mismatch rather
#     than passing quietly.

# The additive columns of each table, by table. THE UNION IS THE POINT: an
# INFERENCE_COLUMN_ADDITIONS entry and a RENAMED_INFERENCE_COLUMNS key fail
# identically against an old database -- `no such column` -- so the guard has no
# reason to tell them apart, and taking the union here is what lets one
# derivation cover both classes.
#
# `run_metrics` IS ABSENT ON PURPOSE and `runs` IS NOT, and the difference is
# the whole reason this map is per-table rather than a flat set of names.
#
# `run_metrics` is wholly additive: it arrived complete, nothing has been added
# to it, so no column of it can be individually absent from a database that has
# the table and `requires` covers it entirely.
#
# `runs` ARRIVED THE SAME WAY AND HAS SINCE GAINED A COLUMN. `resumed` is an
# ALTER on an existing table, so a database written between the run-identity
# pass and this one HAS `runs` and does NOT have `resumed` -- the two facts
# `requires` cannot separate, because it answers about the table. That is
# exactly the shape `requires_columns` exists for, and the entry below is what
# the schema-guards test's check 3i has been waiting to demand: it fails the day
# RUN_COLUMN_ADDITIONS appears without this line.
#
# THERE IS NO RENAME SET FOR `runs`. The gpt4o pass renamed columns of
# `inferences` only -- measured, and the schema-guards test asserts it of
# `trial_matches` too -- so the union here has one term.
ADDITIVE_COLUMNS = {
    "inferences": frozenset(INFERENCE_COLUMN_ADDITIONS)
                  | frozenset(RENAMED_INFERENCE_COLUMNS),
    "trial_matches": frozenset(TRIAL_MATCH_COLUMN_ADDITIONS),
    "runs": frozenset(RUN_COLUMN_ADDITIONS),
}

_SQL_TABLE_REF = re.compile(
    r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?:(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*))?", re.IGNORECASE)

# One more `, <table> [alias]` in a comma-separated FROM list, anchored at the
# end of the previous item so it cannot match a SELECT-list comma.
_SQL_FROM_LIST_ITEM = re.compile(
    r"\s*,\s*([A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?:(?:AS\s+)?([A-Za-z_][A-Za-z0-9_]*))?")

# Words that can follow a table name without being an alias. Not a complete SQL
# keyword list and does not need to be: a wrongly-bound alias can only make a
# QUALIFIED reference resolve to the wrong table, and every qualified reference
# in this registry uses an alias that is bound here.
_NOT_AN_ALIAS = frozenset({
    "where", "group", "order", "on", "and", "or", "left", "right", "inner",
    "outer", "cross", "full", "join", "by", "as", "having", "limit", "union",
    "when", "then", "else", "end", "using", "natural", "select",
})


def _strip_sql_noise(sql) -> str:
    """`sql` with comments and string literals replaced by whitespace.

    The literal becomes `' '` rather than nothing so two identifiers either side
    of one cannot be fused into a third that appears in neither. A `--` comment
    becomes spaces up to its newline, and the newline is kept.

    A LEFT-TO-RIGHT SCANNER AND NOT TWO REGEX PASSES, AND THE DIFFERENCE IS A
    SHIPPED DEFECT THIS REPLACES. It was one regex substitution nested inside
    another -- string literals masked FIRST, comments second -- so an
    APOSTROPHE INSIDE A `--` COMMENT was read as the start of a string literal
    and swallowed everything up to the next quote anywhere in the query,
    comments and code alike.

    Two ordinary English comments (``every member's chain``, ``SQLite's
    group_concat``) were enough to hide a whole CTE, and the CTE hidden was the
    one naming ``i.run_id``. The failure is SILENT AND IN THE DANGEROUS
    DIRECTION: the query derives fewer additive columns than it names, so a new
    query that declares nothing agrees with a derivation that found nothing,
    ``tests/test_storage_schema_guards.py`` check 1a passes, and ``report()``
    dies on ``no such column`` against a database that predates the column --
    which is the exact defect item 38 removed from File 16. Measured, not
    reasoned about: ``campaign_summary`` derived ``(('runs', 'resumed'),)``
    under the old implementation and ``(('inferences', 'run_id'), ('runs',
    'resumed'))`` under this one.

    Reversing the two passes would only move the hazard: a ``--`` inside a
    string literal would then be read as a comment. One scanner that knows both
    forms is the only version that is right about both, and SQL's doubled-quote
    escape (``'it''s'``) falls out of it for free.
    """
    out = []
    i = 0
    n = len(sql)
    while i < n:
        char = sql[i]
        if char == "'":
            # A STRING LITERAL. '' inside one is an escaped quote and does not
            # end it, which is why this cannot be a non-greedy regex either.
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            else:
                # UNTERMINATED. SQLite would refuse this SQL outright, so the
                # only question is what a lexical reader should do with it. It
                # masks to the end rather than treating the quote as ordinary
                # text: a name recovered from inside a broken literal is a
                # derivation from something that is not code.
                j = n
            out.append(" ' ' ")
            i = j
            continue
        if char == "-" and i + 1 < n and sql[i + 1] == "-":
            j = sql.find("\n", i)
            if j == -1:
                out.append(" " * (n - i))
                i = n
            else:
                out.append(" " * (j - i))
                i = j
            continue
        out.append(char)
        i += 1
    return "".join(out)


def sql_table_aliases(sql) -> Dict:
    """`{name: table}` for every base table and alias `sql` binds in FROM/JOIN.

    A table binds itself, so `inferences.x` and `i.x` both resolve when the
    query wrote `FROM inferences i`. Subquery aliases are absent by
    construction: the pattern anchors on an identifier after FROM/JOIN, and a
    subquery opens with `(`.
    """
    text = _strip_sql_noise(sql)
    bound = {}
    for match in _SQL_TABLE_REF.finditer(text):
        table, alias = match.group(1), match.group(2)
        bound[table] = table
        if alias and alias.lower() not in _NOT_AN_ALIAS:
            bound[alias] = table
        # THE COMMA-SEPARATED FROM LIST, continued from where the match ended.
        # `FROM inferences, trial_matches` binds BOTH; without this it bound the
        # first and silently lost the second, and losing one is the DANGEROUS
        # direction -- a table nothing binds derives no column for that table,
        # the query then declares nothing, and the standing test agrees with it
        # because both halves missed the same thing. It ends in a crash inside
        # report() rather than a skip.
        #
        # It is a loop from the match END rather than a `,` alternative in the
        # pattern itself, because `,` alone would match every separator in a
        # SELECT list and bind selected columns as tables.
        #
        # NO REGISTRY QUERY USES THIS FORM TODAY -- measured -- so this is a
        # guard against the next one, and section 2 pins it.
        position = match.end()
        while True:
            more = _SQL_FROM_LIST_ITEM.match(text, position)
            if not more:
                break
            bound[more.group(1)] = more.group(1)
            if more.group(2) and more.group(2).lower() not in _NOT_AN_ALIAS:
                bound[more.group(2)] = more.group(1)
            position = more.end()
    return bound


def derive_requires_columns(sql) -> tuple:
    """The `(table, column)` additive pairs `sql` names, in declaration order.

    THE ORDER IS `ADDITIVE_COLUMNS` KEY ORDER THEN COLUMN ORDER WITHIN A TABLE,
    not order of appearance in the SQL, so the value is a function of the schema
    rather than of how a query happens to be laid out -- which is what makes a
    declaration comparable against it without either side sorting.
    """
    text = _strip_sql_noise(sql)
    bound = sql_table_aliases(sql)
    derived = []
    for table, columns in ADDITIVE_COLUMNS.items():
        if table not in bound.values():
            continue
        for column in sorted(columns):
            escaped = re.escape(column)
            qualifiers = {m.group(1) for m in re.finditer(
                r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*" + escaped + r"\b", text)}
            if any(bound.get(q) == table for q in qualifiers):
                derived.append((table, column))
                continue
            bare = re.sub(r"\bAS\s+" + escaped + r"\b", " ", text,
                          flags=re.IGNORECASE)
            bare = re.sub(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\.\s*" + escaped + r"\b",
                          " ", bare)
            if re.search(r"\b" + escaped + r"\b", bare):
                derived.append((table, column))
    return tuple(derived)


def missing_requirements(conn, key, present=None) -> tuple:
    """What `key` needs and this database does not have.

    Args:
        conn:    an open connection.
        key:     a registry key.
        present: an ``available_tables()`` result to reuse. Supplied by
                 ``report()``, which asks once for the whole registry.

    Returns absent table names and absent ``'table.column'`` names, tables
    first, each in declaration order; ``()`` when the database can answer. A
    query declaring neither requirement returns ``()`` without touching the
    database when `present` is supplied.

    A COLUMN ON AN ABSENT TABLE IS REPORTED ONCE, AS THE TABLE. Naming both
    would tell an operator to add a column to a table that is not there, and the
    one action -- let a writer open the database -- fixes both.
    """
    if key not in QUERIES_BY_KEY:
        raise KeyError(
            f"unknown query key {key!r}; valid keys are "
            f"{', '.join(QUERY_KEYS)}"
        )
    query = QUERIES_BY_KEY[key]
    if not query.requires and not query.requires_columns:
        return ()
    if present is None:
        present = available_tables(conn)

    absent = [t for t in query.requires if t not in present]

    seen = {}
    for table, column in query.requires_columns:
        if table not in present:
            if table not in absent:
                absent.append(table)
            continue
        if table not in seen:
            seen[table] = table_columns(conn, table)
        if column not in seen[table]:
            absent.append(f"{table}.{column}")

    return tuple(absent)


def unavailable(conn) -> Dict:
    """``{key: (absent table, ...)}`` for every query this database cannot answer.

    THE PUBLIC WAY TO ASK BEFORE RUNNING, so a consumer does not have to catch an
    exception to find out. ``report()`` uses it; the dashboard's Run Health tab
    asks the same question of its own connection.
    """
    present = available_tables(conn)
    out = {}
    for query in QUERIES:
        absent = missing_requirements(conn, query.key, present=present)
        if absent:
            out[query.key] = absent
    return out


# The half of the skip message that names nothing in particular. HOISTED OUT OF
# ``missing_table_message`` so ``report()`` can print it ONCE above a list of
# skipped queries instead of once per query -- twenty-one repetitions of the
# same paragraph is a wall a reader skips, and the names are the part they need.
# ``run()`` still gets the whole sentence, because a caller meeting a single
# MissingTableError in a traceback has no list to read it above.
SCHEMA_ERA_EXPLANATION = (
    "Most of what this registry can declare is ADDITIVE: "
    "oncotriage.storage.database_logger.initialize_database creates it, so a "
    "database last written before it was introduced does not carry it and the "
    "next writer to open the file adds it. Nothing is wrong with the rows that "
    "are there."
)


# THE SECOND CLASS, AND IT NEEDS ITS OWN SENTENCE BECAUSE THE FIRST ONE'S
# ADVICE DOES NOT WORK ON IT.
#
# The paragraph above used to be the whole message and it ended "the next writer
# to open the file adds it". That is TRUE of an INFERENCE_COLUMN_ADDITIONS entry
# and FALSE of a renamed one -- MEASURED, by renaming a column back on a fresh
# database and running the real initialize_database over it: the new name did
# not appear, the old one stayed, and nothing was reported. The migration loop
# can only ADD, and the CREATE TABLE above it is IF NOT EXISTS, so there is no
# code path that renames anything.
#
# So an operator meeting a renamed column was told to do the one thing that
# cannot help, would watch it change nothing, and would have no next step. On
# the production database that was TWELVE of twenty-one skipped queries -- the
# majority -- which is why this is a separate sentence rather than a hedge in
# the first.
RENAME_ERA_EXPLANATION = (
    "A RENAMED COLUMN IS DIFFERENT AND NO WRITER WILL REPAIR IT: the "
    "gpt4o -> llm_classifier pass renamed these in place, and the migration "
    "loop can only ADD columns, so opening this database with a writer will "
    "NOT produce them. The data is present under the pre-rename name shown "
    "beside each. These queries can only run against a database written since "
    "the rename."
)


def renamed_predecessor(absent):
    """The pre-rename spelling of `absent`, or ``None`` when it is not a rename.

    `absent` is one entry as ``missing_requirements`` reports it: a bare table
    name, or ``'table.column'``. A table is never a rename, and only
    ``inferences`` was renamed, so both fall through to ``None``.
    """
    if "." not in absent:
        return None
    table, _, column = absent.partition(".")
    if table != "inferences":
        return None
    return RENAMED_INFERENCE_COLUMNS.get(column)


def rename_note(absent) -> str:
    """``'new (was old), ...'`` for the renamed entries in `absent`, else ``''``.

    ONE OWNER, because ``report()`` prints it under a skip list and
    ``missing_table_message`` appends it to a single raise, and a reader meeting
    a column in a log and in a traceback must be told the same thing about it.
    """
    pairs = [(name, renamed_predecessor(name)) for name in absent]
    renamed = [f"{name} (was {old})" for name, old in pairs if old]
    if not renamed:
        return ""
    return f" {RENAME_ERA_EXPLANATION} Affected here: {', '.join(renamed)}."


def missing_table_message(key, absent) -> str:
    """The one sentence printed and raised when a table is not there.

    ONE OWNER, because ``report()`` prints it and ``run()`` raises it and a
    reader who meets it in a log and in a traceback should meet the same words.
    """
    # THE EXPLANATION NAMES NO PARTICULAR TABLE ANY MORE, and that is a
    # correction rather than a generalisation for its own sake. It read
    # "`runs` and `run_metrics` are created by ... so a database last written
    # before the run-identity pass does not carry them" -- true of the only two
    # consumers it had when it was written, and FALSE the moment a query
    # declared a column instead: the split-pressure pair would have been
    # reported with a sentence naming two tables that are not what is missing
    # and a pass that has nothing to do with them. The mechanism is the same for
    # every additive name in this schema, so the sentence describes the
    # mechanism and lets the first clause name what is actually absent.
    return (f"query {key!r} needs {', '.join(absent)}, which this database "
            f"does not have. {SCHEMA_ERA_EXPLANATION}{rename_note(absent)}")


def run(conn, key) -> pd.DataFrame:
    """Execute one query by key and return its DataFrame. Prints nothing.

    Raises KeyError naming the valid keys for an unknown one, rather than
    returning an empty frame -- a typo'd key that answered with no rows would be
    indistinguishable from a database with no matching rows, which is the exact
    confusion this project treats as a defect.

    Raises ``MissingTableError`` when the database does not have a table the
    query declares in ``requires``, for the same reason and with the same shape:
    an empty frame there would say "this run tracking recorded nothing" about a
    database that has never been asked. The check costs one ``sqlite_master``
    read and ONLY for a query that declares a requirement, so the forty-three
    queries that declare none are unaffected.
    """
    if key not in QUERIES_BY_KEY:
        raise KeyError(
            f"unknown query key {key!r}; valid keys are "
            f"{', '.join(QUERY_KEYS)}"
        )
    absent = missing_requirements(conn, key)
    if absent:
        raise MissingTableError(missing_table_message(key, absent))
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


def print_slowest_prompt(conn, out=console.out) -> pd.DataFrame:
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
    out(f"Output tokens: {_row['llm_classifier_output_tokens']}")
    out("Total time: (not recorded)" if pd.isna(_time)
        else f"Total time: {float(_time):.1f}s")
    out("\nCopy this prompt to ChatGPT:\n")
    out("="*80)
    out(_row['llm_classifier_prompt'])
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
                       "cost_complete", "stored_cost", "note")
"""What ``price_model_groups`` returns, in order. Pinned because the dashboard
renders it and "tests/test_storage_query_layer.py" asserts on it.

``cost_complete`` sits immediately after ``recomputed_cost`` because it is that
column's qualifier and nothing else's -- see ``price_model_groups``."""


COST_INCOMPLETE_NOTES = (
    "no token counts recorded (SUM was NULL, not 0)",
    "input token count not recorded (SUM was NULL, not 0)",
    "output token count not recorded (SUM was NULL, not 0)",
    "NO MODEL RECORDED BUT TOKENS PRESENT — logging defect",
)
"""The note fragments that accompany ``cost_complete = False``.

Declared so "tests/test_storage_query_layer.py" can assert the boolean and the
prose never disagree -- a False with no explanation, or an explanation with a
True beside it, are both worse than either alone. ``price_model_groups``
computes the boolean from the DATA rather than from these strings; the strings
are what a human reads and the test is what keeps the two in step."""


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

    ``cost_complete`` IS THE ONE FIELD A CONSUMER HAS TO ASK, and it exists
    because the note column was not enough. ``recomputed_cost`` is a float on
    every row including the ones that could not be priced, so anything that SUMS
    the column -- a total, a per-patient average, a projection to 1000 patients,
    a published figure -- under-reports by exactly the unpriceable spend and
    reads as a cheap run unless somebody happened to read the prose beside it.
    "Unknown reported as zero" is the one error a reader cannot detect from the
    number, and cost per patient is a number this project publishes.

    It is False in exactly two situations, both meaning "this group's
    recomputed_cost is not an accounting of its spend":

        the token SUMs are NULL     -- nothing is known about what was consumed;
        the model is NULL AND the group carries tokens -- consumption is known
                                       and there is no rate to price it at.

    It is TRUE for a NULL-model group carrying zero tokens, which is the
    ordinary no-candidates run: nothing was spent and $0.00 is the whole truth.

    IT DELIBERATELY SAYS NOTHING ABOUT ``stored_cost``. That column carries its
    own NA and a consumer can ask ``df["stored_cost"].isna()`` directly;
    ``recomputed_cost`` cannot be asked, which is the whole reason it needs a
    separate flag. One boolean spanning two different sums would answer neither
    question exactly.

    THE PRICED VALUE IS UNCHANGED. An incomplete group still prices at $0.00
    rather than NaN. Turning it into NaN would propagate through every aggregate
    and make a partially-observable table produce no number at all, which is a
    different and worse failure than a number that says it is partial.
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

        # COMPUTED FROM THE DATA, NOT FROM THE NOTE STRINGS ABOVE. A boolean
        # derived by searching prose would agree with the prose by construction
        # and could never catch the two disagreeing, which is the failure this
        # project has shipped before. File 49 asserts they agree, against
        # COST_INCOMPLETE_NOTES, which is a different thing from computing one
        # out of the other.
        #
        # `_stored is None` is deliberately NOT a term: this flag qualifies
        # recomputed_cost. See the docstring.
        _cost_complete = ((_in is not None and _out is not None)
                          and (_model_recorded
                               or (_in_priced == 0 and _out_priced == 0)))

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
            # Whether the line above is an accounting of this group's spend or
            # a floor on it. The ONE field a consumer asks before summing.
            "cost_complete": _cost_complete,
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
    _needed = ("matching_model", "llm_classifier_input_tokens", "llm_classifier_output_tokens",
               "llm_classifier_reasoning_tokens", "estimated_cost_usd")
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
    _sums = _grouped[["llm_classifier_input_tokens", "llm_classifier_output_tokens",
                      "llm_classifier_reasoning_tokens", "estimated_cost_usd"]].sum(min_count=1)

    # reindex rather than relying on the two groupby results coming back in the
    # same order. They do; but "rows_n" landing against the wrong model is a
    # silent mislabelling that no later check in this module could catch.
    return pd.DataFrame({
        "matching_model": _sums.index,
        "rows_n": _grouped.size().reindex(_sums.index).values,
        "input_tokens": _sums["llm_classifier_input_tokens"].values,
        "output_tokens": _sums["llm_classifier_output_tokens"].values,
        "reasoning_tokens": _sums["llm_classifier_reasoning_tokens"].values,
        "stored_cost": _sums["estimated_cost_usd"].values,
    })


def cost_by_model(conn) -> pd.DataFrame:
    """File 16's Query 10, the arithmetic half: price each model's rows.

    The SQL producer. Everything about the arithmetic is in
    ``price_model_groups``; this is the GROUP BY that feeds it.
    """
    return price_model_groups(run(conn, "cost_by_model"))


def print_cost_by_model(conn, out=console.out) -> pd.DataFrame:
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

    # THE SAME TREATMENT FOR THE RECOMPUTED SIDE, which is what the residual
    # pass added. The stored total already said how many groups it excluded; the
    # recomputed total said nothing, because an unpriceable group contributes a
    # real 0.0 rather than a NULL and so cannot be spotted by looking at the
    # column. Every line derived from this total is qualified below, not just
    # the total itself: a projection built on a floor is a floor.
    _incomplete_groups = (df_cost.loc[~df_cost["cost_complete"], "matching_model"]
                          .tolist() if len(df_cost) else [])
    _incomplete_rows = (int(df_cost.loc[~df_cost["cost_complete"], "rows"].sum())
                        if len(df_cost) else 0)

    out(f"\nRows: {_total_rows}")
    out(f"Recomputed total: ${_recomputed_total:.4f}"
        + ("" if not _incomplete_groups else "   <- A FLOOR, NOT A TOTAL"))
    if _incomplete_groups:
        out(f"  ...{len(_incomplete_groups)} of {len(df_cost)} model groups "
            f"({_incomplete_rows} of {_total_rows} rows) could not be priced "
            f"from what was recorded and contribute $0.00 rather than their "
            f"real spend: {', '.join(_incomplete_groups)}. Ask cost_complete "
            f"before summing recomputed_cost; the note column says why.")
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
            f"${_recomputed_total / _total_rows * 1000:.2f}"
            + ("" if not _incomplete_groups
               else "  (a FLOOR -- see the incomplete groups above)"))

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
    ``print(df_inferences)`` collapses to `id  ...  llm_classifier_reasoning_tokens`,
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
    # BEFORE the heading, deliberately. 'skip_if_empty' means "this section does
    # not exist when there is nothing in it", and a heading with nothing under
    # it is a section. `pipeline_consistency_totals` is the only user: on a
    # clean database it must leave the listing's clean message standing alone,
    # not sit above it announcing an empty count table. `blank_after` is skipped
    # with it, so nothing at all is emitted -- including whitespace.
    if query.render == "skip_if_empty" and df.empty:
        return

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
    elif query.render in ("to_string", "skip_if_empty"):
        # skip_if_empty reaches here only when the frame is NOT empty -- the
        # guard at the top returned otherwise -- so from here it is to_string.
        out(df.to_string(index=False))
    elif query.render == "empty_or_to_string":
        if df.empty:
            out(query.clean_message or CONSISTENCY_CLEAN_MESSAGE)
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


def report(conn, out=console.out) -> Dict:
    """Run every query in registry order and print exactly what File 16 printed.

    Args:
        conn: An open connection. NOT closed here -- see ``connect()``.
        out:  Where each line goes. Defaults to ``console.out``. Passing
              ``lambda *a: None`` runs the whole sweep silently, which is what
              makes this testable without capturing stdout.

    Returns:
        {key: DataFrame} for every query that completed before the run ended.

        A QUERY SKIPPED FOR A MISSING TABLE IS ABSENT FROM THE DICT, not present
        with an empty frame. A caller doing ``report(conn)["run_summary"]``
        against a database that predates the run tables gets a KeyError naming
        the key, which is a question it can answer, rather than a frame with no
        rows, which is an answer about the runs that is not true. The skip is
        printed above the report, and ``unavailable(conn)`` is how to ask
        without running anything.

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

    # WHICH QUESTIONS THIS DATABASE CANNOT ANSWER, ASKED ONCE, BEFORE ANYTHING
    # RUNS -- and ANNOUNCED, not silently skipped.
    #
    # This is the guard that keeps the item-38 property. `runs` and `run_metrics`
    # are additive tables; the production inferences.db does not have them until
    # a writer next opens it. Without this block the first run query would raise
    # `no such table`, take the process down, and every query registered after it
    # would never execute -- exactly what `expansion_token_efficiency` did.
    #
    # A SKIP IS PRINTED, LOUDLY, AND NAMES THE TABLES AND THE KEYS. A report that
    # quietly covers less than its registry is a report that reads as complete.
    # Same reasoning as `pipeline_consistency_totals` printing the count beside a
    # capped listing.
    skipped = unavailable(conn)

    # The three raw-cursor sections and the two frames File 16 printed before its
    # first numbered query, in the order it printed them.
    out(table_names(conn))
    out(fetch_raw(conn, RAW_INFERENCES_SQL))

    if skipped:
        # THE SKIP LIST NAMES, PER QUERY, WHAT THAT QUERY IS MISSING.
        #
        # It used to print one union of every absent name and one sentence
        # naming the ALPHABETICALLY FIRST skipped key -- so the sentence read
        # "query 'run_attribution_coverage' needs <twenty names>", attributing
        # to one query nineteen columns it does not read. That was survivable
        # while three queries were ever skipped together and the union was
        # nearly one query's worth. It is not survivable now: against the
        # production database twenty-one queries skip on sixteen distinct
        # absent names, and an operator's next question is always "which
        # query lost what", which the union cannot answer.
        #
        # The aggregate lines are KEPT above the per-query ones. The counts are
        # what says the report covers less than its registry, and a reader
        # scanning for "is anything missing" should not have to total a list.
        _absent = sorted({t for tables in skipped.values() for t in tables})
        out(f"=== {len(skipped)} QUERY/QUERIES SKIPPED: TABLE(S) OR COLUMN(S) "
            f"NOT IN THIS DATABASE ===")
        out(f"absent ({len(_absent)}): {', '.join(_absent)}")
        out(f"skipped ({len(skipped)} of {len(QUERIES)}): "
            f"{', '.join(sorted(skipped))}")
        out(SCHEMA_ERA_EXPLANATION)
        # THE RENAME CLASS, ONCE, ABOVE THE LIST. Printed only when one is
        # actually absent, so a database whose skips are purely additive is not
        # told about a mechanism that did not affect it.
        _rename_note = rename_note(_absent)
        if _rename_note:
            out(_rename_note.strip())
        for _key in sorted(skipped):
            out(f"  {_key}: {', '.join(skipped[_key])}")
        out("\n")

    for query in QUERIES:
        if query.key in skipped:
            continue
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
