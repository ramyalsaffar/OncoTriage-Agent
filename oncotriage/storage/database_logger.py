"""SQLite schema and the inference logger.

Moved out of ``14- Database Logger.py`` by item 20c, pass 2b.
``14- Database Logger.py`` survives as an explicit re-export shim over this
module, because Files 17, 25, 26, 32, 36, 37, 38, 40 and 45 exec-chain it and
read these names out of the shared exec namespace with no import statement of
their own.

TWO DELIBERATE CHANGES, and they are the reason this pass was not a straight move
--------------------------------------------------------------------------------

1. ``log_inference`` TAKES ``db_path``.

   It used to read a bare ``inferences_path`` out of the shared namespace. Five
   files rebind that name at a temporary database and only then load File 14 —
   36, 37, 38, 40 and 45 — and that redirect is the only thing standing between
   a test run and the production inferences.db. A module function cannot see a
   caller's globals, so the redirect would have gone quiet the moment this file
   became a module: five tests writing real rows into the real database, each
   still printing the name of the temporary file it thought it was using. The
   failure mode is silent in both directions, which is why the fix is a
   parameter and not a global.

   ``None`` means ``oncotriage.paths.inferences_path``. The shim's own
   ``log_inference`` wrapper passes ``globals().get("inferences_path")``, so an
   exec-chain caller keeps the late binding it always had; the five test files
   now ALSO pass the path explicitly, so their isolation no longer depends on
   that seam. See ``resolve_inference_db_path``.

2. ``_resolve_primary_cancer`` LEFT ALTOGETHER (pass 20c-2c).

   Pass 2b changed it from reading ``_CANCER_REGISTRY`` — which
   "13- LangGraph Agent.py" assigned at its own line 64, a layering violation
   that left the function raising NameError in any chain loading 14 without 13 —
   to calling ``load_registry()``. Pass 2c finished the job: it is a domain
   question about SNOMED and ICD-10 codes and it opens no database, so it now
   lives in ``oncotriage/registries/primary_cancer.py`` and is IMPORTED here.

   That direction is the point. The agent's three terminal nodes call it too, and
   while it lived here the agent depended on the storage layer for a registry
   lookup. Both callers now import it from the registries package and neither
   imports the other. It is still re-exported by ``14- Database Logger.py``,
   because nine files read the name out of the shared exec namespace.

WHAT IMPORTING THIS MODULE DOES
-------------------------------
Nothing observable. Item 20b turned schema creation into a function precisely so
that loading this file would stop opening the production database, and that
holds here: no connection, no CREATE, no path resolution, no registry
construction. ``load_registry()`` — reached through ``primary_cancer`` — builds
on first CALL and imports the ICD-10-CM release inside its own body.

COST ACCOUNTING FAILS LOUDLY, and the ordering that makes it do so is
load-bearing: ``get_model_cost()`` is called BEFORE ``log_inference``'s try
block, so an unpriced model raises ``UnknownModelPricingError`` out to the caller
instead of being swallowed by the broad except that exists to keep a database
fault from killing the pipeline. Never move it inside, and never wrap it in a
recovery path.
"""

import json
import os
import sqlite3
from typing import Dict, List, Optional

from oncotriage import paths
from oncotriage.config import MATCHING_MODEL, PRICING_CONFIG
from oncotriage.registries.primary_cancer import _resolve_primary_cancer
from oncotriage.utils import deduplicate_by_display, get_model_cost


#------------------------------------------------------------------------------


def resolve_inference_db_path(db_path=None):
    """The database ``log_inference`` will write to for this call.

    Args:
        db_path: An explicit path, or ``None`` for the configured production
            database (``oncotriage.paths.inferences_path``, resolved on this
            call — see that module for why resolution is lazy).

    Returns:
        The path string, unmodified when one was supplied.

    THIS FUNCTION DOES NOT CONSULT THE EXEC NAMESPACE, and that asymmetry is on
    purpose. The shim's ``log_inference`` wrapper is what reads
    ``globals().get("inferences_path")``; this one always answers "what does a
    caller that passed nothing get", which is exactly the question the five
    isolation tests need answered in order to show that passing the scratch path
    is doing any work. If this resolved through the namespace too, those tests
    would be comparing a value against itself.

    It resolves and returns; it opens nothing. Calling it is safe on a machine
    with a database it must not touch.
    """
    if db_path is not None:
        return db_path
    return paths.inferences_path

#------------------------------------------------------------------------------


# Item 20b: schema creation is a function, not a module body.
#
# Loading this file used to open the production database and run every CREATE
# TABLE and every additive migration as a side effect of the exec chain. Nine
# other files load 14 or are loaded beside it; each of them was touching
# inferences.db just by being read. A file must be loadable without writing to
# anything.
#
# What moved: only the executable statements. The two COLUMN_ADDITIONS dicts
# stay at module level, byte for byte, because they are pure data and because
# 40- ECOG Logging Test.py reads INFERENCE_COLUMN_ADDITIONS directly. The
# migration loops are unchanged; they are what adds a column without destroying
# rows, and items 29b and 20a both depend on that.
#
# The SQL is still written flush against column 0 inside its triple-quoted
# strings even though it now sits inside a function. Indenting those lines
# would change the CREATE text SQLite stores in sqlite_master.sql, so the
# schema would no longer be identical to the one this file produced before.


#------------------------------------------------------------------------------


# Schema migration for the inferences table.
#
# CREATE TABLE IF NOT EXISTS is a no-op once the table exists, so columns added
# after the first run must be applied explicitly. Rows written before a column
# existed keep NULL, which is the honest value: the counter was not recorded,
# as opposed to having been recorded as zero.
INFERENCE_COLUMN_ADDITIONS = {
    "not_evaluable_trials": "INTEGER",   # trials the model could not assess at all
    "cross_vocab_remaps":   "INTEGER",   # criterion labels resolved to not_evaluable
    # Which layer resolved the patient's MeSH C04 identity ("snomed",
    # "icd10+fuzzy_synonym", ...), or why none did ("pan_cancer_only",
    # "unmapped", "no_cancer_condition", "no_valid_condition",
    # "no_mesh_filter"). mesh_dropped = 0 is ambiguous on its own: it means
    # both "the filter found nothing to drop" and "the patient was never
    # resolved, so the filter never ran". This column separates the two.
    "mesh_resolution":      "TEXT",
    # Count of trials GPT-4o returned an evaluation for that were never in the
    # candidate set sent to it. The detector (item 33) writes
    # result["hallucinated_trials"]; until it does, no terminal node emits the
    # key and the column stays NULL on every row. NULL is the correct value:
    # inserting 0 would assert that the check ran and found nothing, which is
    # the exact confusion this project treats as a defect. The column exists
    # now so the detector has somewhere to write without a second migration.
    "hallucinated_trials":  "INTEGER",
    # --- Retrieval and expansion degradation (item 11b) ---------------------
    # Stage 2 runs four retrieval channels behind one try/except each. Before
    # these columns existed, a channel that raised was printed and dropped, and
    # fusion continued on the survivors: a dense-search outage produced the
    # same stored row as a clean run. bm25_retrieved / vector_retrieved cannot
    # substitute — 0 means both "returned nothing" and "never returned".
    #
    # retrieval_channels holds the per-channel record as JSON:
    #   {"title": {"status": "ok", "count": 75, "error": ""},
    #    "dense": {"status": "failed", "count": 0, "error": "..."}}
    # status is one of File 13's CHANNEL_* constants: ok | failed | ablated |
    # empty_query. The scalars beside it are the same fact in queryable form,
    # with ablated channels excluded from "expected" so a bm25_only ablation is
    # not reported as a degraded run.
    #
    # NULL on every one of them means Stage 2 did not report, which is not the
    # same as a clean run — see _pipeline_provenance() in File 13.
    "retrieval_channels":           "TEXT",
    "retrieval_channels_expected":  "INTEGER",
    "retrieval_channels_ok":        "INTEGER",
    "retrieval_degraded":           "INTEGER",  # 1 = an expected channel did not return
    # Trials ranked into the fusion pool whose payload could not be recovered,
    # so they never reached Stage 3. The batch-scroll fallback that loses them
    # used to print a line and keep going.
    "retrieval_trials_lost":        "INTEGER",
    # Which query Stage 1 searched with: "mesh_expanded" or
    # "base_query_fallback". The fallback printed a WARNING and nothing else,
    # so the rate at which the pipeline ran without any MeSH expansion was not
    # recoverable from the database. Distinct from mesh_resolution, which says
    # why resolution failed rather than what the run then did.
    "query_expansion_path":         "TEXT",
    # Whether Stage 4's cancer site filter actually ran (1/0), and why not.
    # Stage 5's system prompt asserts to the model that disease relevance was
    # confirmed; that assertion is now conditional on this flag, so the flag
    # belongs in the record of the inference it shaped.
    "mesh_filter_applied":          "INTEGER",
    "mesh_filter_skip_reason":      "TEXT",
    # --- Age provenance (item 12) -------------------------------------------
    # The date this run computed patient ages against (DATA_SNAPSHOT_DATE,
    # File 03), and how much of the patient's birthDate the record carried.
    #
    # age was previously derived from datetime.now(), so the stored age — and
    # the Stage 5 prompt built from it — moved with the clock while
    # patient_data_hash, which keys on birth_date, stayed identical. Rows
    # written before this column existed keep NULL, which is honest: their
    # reference date was whatever day they happened to run and is not
    # recoverable from the row.
    #
    # birth_date_precision is "day" for an exact age; "month"/"year" mean the
    # age was imputed from a mid-range anchor (File 02) because the record was
    # partial, which HIPAA Safe Harbor de-identification produces by design;
    # "missing"/"unparseable"/"after_reference" mean age is NULL and say why.
    # NULL here means the parser did not report — not that the date was exact.
    "age_reference_date":           "TEXT",
    "birth_date_precision":         "TEXT",
    # --- ECOG performance status (File 07 parses it, File 13 carries it) -----
    # The score that reached the Stage 5 prompt, and how it was arrived at.
    # ECOG 0-1 or 0-2 gates nearly every interventional oncology trial, so these
    # move the verdict directly; without them a corpus whose observations all
    # postdate DATA_SNAPSHOT_DATE would match systematically worse with nothing
    # in the row explaining it.
    #
    # READ THE CONVENTION BEFORE QUERYING THESE. ecog_value is NULL in three
    # different situations and cannot separate them on its own:
    #
    #   ecog_selection IS NULL          the row predates this migration, or the
    #                                   caller logged a result that never came
    #                                   from a pipeline terminal node. Nothing
    #                                   is known about this patient's ECOG.
    #   ecog_selection = 'none_recorded'  the patient genuinely carried no ECOG
    #                                   observation. ecog_observations_found = 0.
    #   ecog_selection = 'all_after_reference_date'
    #                   or 'undated_ambiguous'
    #                                   observations exist but none was usable.
    #                                   ecog_observations_found >= 1 says how many.
    #
    # So: absence is `ecog_selection = 'none_recorded'`, NEVER
    # `ecog_value IS NULL`. And a score of 0 is a real, fully-active patient --
    # the most eligible there is -- so ecog_value = 0 must never be treated as
    # missing either. Both confusions are the ones this column set exists to
    # prevent, which is why the selection path is stored beside the value rather
    # than being derivable from it.
    "ecog_value":                   "INTEGER",
    "ecog_selection":               "TEXT",
    "ecog_observations_found":      "INTEGER",

    # --- Stage 5 truncation control (item 19c) -----------------------------
    #
    # Two counters because there are two budgets. gpt4o_retries counts whole-
    # node retries for a malformed or failed response; gpt4o_truncation_splits
    # counts levels of halving spent because a response was CUT OFF at the
    # model's output ceiling. Before this, a truncated response fell through to
    # the JSON parser, failed there, and was retried as an identical request
    # that truncated again -- so a truncation was logged as three parse
    # retries, and the two causes were indistinguishable in the record.
    #
    # gpt4o_output_tokens_estimated is the pre-call estimate, stored beside the
    # actual in gpt4o_output_tokens. That column pair is what the constants in
    # 03- Config.py were derived from over 1,094 historical rows, and storing
    # the estimate is what lets the next derivation be measured rather than
    # guessed. NULL when Stage 5 never ran: "estimated nothing" is not "0".
    #
    # not_evaluable_truncated counts trials that entered Stage 5 and left with
    # no verdict because of truncation. It is a SUBSET of not_evaluable_trials
    # in the sense that both end up not evaluable, but the cause is different
    # and only this column separates "the model assessed it and could not
    # conclude" from "the model never got to answer".
    #
    # gpt4o_calls is how many requests the stage actually issued. Without it a
    # split run and an unsplit one are indistinguishable in the token columns,
    # because the tokens are summed across chunks.
    "gpt4o_truncation_splits":      "INTEGER",
    "gpt4o_output_tokens_estimated": "INTEGER",
    "not_evaluable_truncated":      "INTEGER",
    "gpt4o_calls":                  "INTEGER",

    # --- Reasoning-model accounting (item 29a, gpt-5.6-terra migration) ------
    #
    # The reasoning share OF gpt4o_output_tokens. NOT an additional charge.
    # OpenAI's reasoning guide and a live probe on 2026-08-04 both put
    # usage.completion_tokens_details.reasoning_tokens INSIDE
    # usage.completion_tokens, billed at the output rate. So:
    #
    #     estimated_cost_usd already includes these tokens.
    #     gpt4o_output_tokens already includes these tokens.
    #
    # Anyone adding this column into a cost calculation is double-billing.
    # It is stored because it is the only way to see what fraction of the
    # output spend bought reasoning rather than verdicts, and because it is
    # what MATCHING_OUTPUT_TOKENS_PER_TRIAL (File 03) must be calibrated
    # against now that reasoning tokens consume the same ceiling.
    #
    # NULL means the response carried no breakdown -- every row written while
    # GPT-4o was the judge, a replayed pre-migration fixture, or a run that
    # never reached Stage 5. That is NOT 0. A non-reasoning model that
    # genuinely reports reasoning_tokens=0 stores 0, and the two must stay
    # distinguishable: a query averaging this column has to exclude NULL, not
    # coalesce it.
    "gpt4o_reasoning_tokens":       "INTEGER",
}


#------------------------------------------------------------------------------


# Schema migration for the trial_matches table (same reasoning as above).
#
# rerank_score stays the BOOSTED ranking score, so historical rows keep their
# meaning. The unboosted score and the MeSH boost are recorded separately so
# the boost's effect on ranking can be measured rather than inferred.
#
# match_score is confirmed/denominator over APPLICABLE criteria only (File 13
# excludes criteria the model marked "Not applicable -- ..." from both). Storing
# the three inputs makes the ratio auditable: a 0.0 score on a denominator of 8
# (nothing confirmable) is a different finding from 0.0 on a denominator of 0
# (no criterion applied to this patient), and neither is visible from the
# rounded score alone.
TRIAL_MATCH_COLUMN_ADDITIONS = {
    "rerank_score_raw": "REAL",   # fused rerank score before the MeSH boost
    "mesh_boost":       "REAL",   # additive boost, 0.0 when no tier matched
    "mesh_boost_tier":  "TEXT",   # "direct" | "pan_cancer" | "none"
    "score_confirmed":         "INTEGER",  # match_score numerator
    "score_denominator":       "INTEGER",  # match_score denominator (applicable only)
    "criteria_not_applicable": "INTEGER",  # criteria excluded from both
    # Per-trial marker for the same detection as inferences.hallucinated_trials:
    # 1 = this NCT ID was not in the candidate set sent to the model, 0 = it was,
    # NULL = the check did not run for this row. Written from match["hallucinated"].
    "hallucinated":            "INTEGER",
}


#------------------------------------------------------------------------------


# Who calls initialize_database(), and when.
#
# Both, deliberately:
#
#   - Any caller may call it explicitly to build or migrate a database at a
#     path of its choosing. That is what makes it testable without
#     monkey-patching a global, which is why it takes db_path as an argument.
#
#   - log_inference() ensures the schema itself, once per resolved path,
#     immediately before its first write.
#
# The second is not redundancy, it is the answer to "what stops a caller that
# never called it from writing to a database with no tables". Relying on entry
# points alone would fail silently here: log_inference deliberately swallows
# sqlite3.Error so a logging fault cannot kill the pipeline, so a missing table
# would surface as one "Database logging failed" line per patient and a run
# that records nothing. Worse, the tests that repoint inferences_path at a
# temporary file (36, 37, 38, 40, 45) would each need a new explicit call, and
# any future caller that forgot one would get the same silent hole.
#
# Ensuring on first use makes the never-initialized state unreachable rather
# than merely detectable. The cost is one connection per distinct path per
# process; _INITIALIZED_DATABASES keys on the resolved absolute path so a test
# that repoints inferences_path is initialized again, and a batch run of 22k
# patients pays for it once.
#
# The path is recorded only after the work succeeds, so a failed attempt is
# retried on the next call instead of being remembered as done.
_INITIALIZED_DATABASES = set()


def initialize_database(db_path):
    """Create the three tables at db_path and apply the additive migrations.

    Idempotent: every CREATE is IF NOT EXISTS and every ALTER is guarded by a
    PRAGMA table_info check, so calling this on an existing database adds only
    what is missing and destroys nothing.

    Returns the resolved absolute path, so a caller can log where it wrote.
    """
    # Connect
    # It will create it if deos not exist, and it won't override if it does.
    conn = sqlite3.connect(db_path)

    # Create cursor
    cursor = conn.cursor()

    # Inferences table
    # candidates_filtered INTEGER is for trials sent to GPT-4o (after quality threshold + cost cap)
    cursor.execute('''
CREATE TABLE IF NOT EXISTS inferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    age INTEGER,
    sex TEXT,
    race TEXT, 
    ethnicity TEXT,
    primary_condition TEXT,
    condition_count INTEGER,
    medication_count INTEGER,
    allergy_count INTEGER,
    expanded_query TEXT,
    candidates_retrieved INTEGER,
    candidates_reranked INTEGER,
    bm25_retrieved INTEGER,
    vector_retrieved INTEGER,
    candidates_after_rule_filter INTEGER,
    candidates_after_quality_filter INTEGER,
    candidates_filtered INTEGER,
    mesh_dropped INTEGER,
    mesh_resolution TEXT,
    stage_dropped INTEGER,
    histology_dropped INTEGER,
    candidates_evaluated INTEGER,
    eligible_matches INTEGER,
    near_misses INTEGER,
    not_evaluable_trials INTEGER,
    cross_vocab_remaps INTEGER,
    query_expansion_time REAL,
    hybrid_retrieval_time REAL,
    cross_encoder_time REAL,
    rule_filter_time REAL,
    gpt4o_evaluation_time REAL,
    total_time REAL,
    gpt4o_prompt TEXT,
    gpt4o_input_tokens INTEGER,
    gpt4o_output_tokens INTEGER,
    matching_model TEXT,
    cross_encoder_model TEXT,
    pricing_version TEXT,
    estimated_cost_usd REAL,
    qdrant_collection TEXT,
    error TEXT,
    patient_data_hash TEXT,
    expansion_prompt TEXT,
    gpt4o_retries INTEGER,
    ablation_flags TEXT,
    hallucinated_trials INTEGER,
    ecog_value INTEGER,
    ecog_selection TEXT,
    ecog_observations_found INTEGER
)
''')


    _existing_inference_columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(inferences)")
    }
    for _column, _sql_type in INFERENCE_COLUMN_ADDITIONS.items():
        if _column not in _existing_inference_columns:
            cursor.execute(f"ALTER TABLE inferences ADD COLUMN {_column} {_sql_type}")
            print(f"Schema migration: added inferences.{_column}")


    # Trial matches table
    cursor.execute('''
CREATE TABLE IF NOT EXISTS trial_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inference_id INTEGER NOT NULL,
    nct_id TEXT NOT NULL,
    trial_title TEXT,
    trial_phase TEXT,
    trial_number INTEGER,
    rerank_score REAL,
    rerank_score_raw REAL,
    mesh_boost REAL,
    mesh_boost_tier TEXT,
    match_score REAL,
    eligible TEXT,
    explanation TEXT,
    criterion_details TEXT,
    hallucinated INTEGER,
    FOREIGN KEY (inference_id) REFERENCES inferences(id)
)
''')


    _existing_trial_match_columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(trial_matches)")
    }
    for _column, _sql_type in TRIAL_MATCH_COLUMN_ADDITIONS.items():
        if _column not in _existing_trial_match_columns:
            cursor.execute(f"ALTER TABLE trial_matches ADD COLUMN {_column} {_sql_type}")
            print(f"Schema migration: added trial_matches.{_column}")


    # Drift metrics table
    cursor.execute('''
CREATE TABLE IF NOT EXISTS drift_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    metric_category TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value REAL,
    baseline_mean REAL,
    baseline_std REAL,
    p_value REAL,
    z_score REAL,
    threshold REAL,
    alert INTEGER,
    baseline_window_days INTEGER,
    comparison_window_days INTEGER,
    notes TEXT
)
''')


    conn.commit()
    conn.close()
    print(f"Database initialized at: {db_path}")

    _INITIALIZED_DATABASES.add(os.path.abspath(db_path))
    return os.path.abspath(db_path)


def _ensure_database(db_path):
    """Initialize db_path unless this process already did.

    Called by log_inference before its first write. Kept separate from
    initialize_database so an explicit caller always gets the real work done
    (a caller who deleted the file and wants it rebuilt calls that one), while
    the hot path pays the cost once.
    """
    resolved = os.path.abspath(db_path)
    if resolved in _INITIALIZED_DATABASES:
        return resolved
    return initialize_database(db_path)


#------------------------------------------------------------------------------


# _resolve_primary_cancer MOVED OUT in pass 20c-2c.
#
# It lives in oncotriage/registries/primary_cancer.py now and is imported at the
# top of this module. It is a domain question about SNOMED and ICD-10 codes, it
# opens no database, and it sat here only because this is where the answer was
# first needed. The consequence was an import edge pointing the wrong way:
# File 13's three terminal nodes called it, so the AGENT depended on the STORAGE
# layer for a registry lookup.
#
# Both callers -- oncotriage/agent/terminal.py and log_inference below -- now
# import it from the registries package, and neither imports the other. The
# function itself is byte-identical to the one pass 2b left here, which
# 47- Package Split Test.py re-derives with ast.unparse against git HEAD.
#
# It is still re-exported by "14- Database Logger.py", because Files 17, 25, 26,
# 32, 36, 37, 38, 40 and 45 read the name out of the shared exec namespace.


#------------------------------------------------------------------------------


# Logging function
def log_inference(result: Dict, patient_data: Dict, db_path=None):
    """
    Log inference result to SQLite database.

    Non-critical operation: Errors are logged but not raised to avoid
    breaking the main pipeline if database logging fails.

    The one exception is UnknownModelPricingError. Cost is computed BEFORE the
    try block below precisely so it cannot be caught by it: an unpriced model
    is a configuration defect, not a database failure, and swallowing it would
    either drop the row entirely (with a message blaming logging) or, before
    get_model_cost() learned to raise, write a row asserting the run was free.
    Either way the operator is not told that the cost column has stopped
    meaning anything. It propagates to the caller instead.

    Args:
        result:       The pipeline result dict from a terminal node.
        patient_data: The parsed patient dict, used for the fallbacks.
        db_path:      Database to write to. None means the configured
                      production database -- see resolve_inference_db_path.
                      Files 36, 37, 38, 40 and 45 pass a temporary path; before
                      pass 20c-2b they rebound a global instead, which a module
                      function cannot see.

    Returns:
        The database path this call actually used, so a caller can ASSERT where
        it wrote rather than assuming. That return value is what makes the five
        isolation tests checkable: each of them compares it against its own
        temporary file. It is returned even when the write fails, because the
        path is resolved before the try block and "which database did you aim
        at" is answerable whether or not the shot landed.
    """

    # Resolved BEFORE the try, alongside get_model_cost() and for the same
    # reason: a path that cannot be resolved is a configuration defect, not a
    # database failure, and the broad except below exists only for the latter.
    # A caller that passes db_path resolves nothing at all.
    db_path = resolve_inference_db_path(db_path)

    # The model that ACTUALLY answered, read off response.model by Stage 5 and
    # carried to all three terminal nodes by _pipeline_provenance() (File 13).
    # Not MATCHING_MODEL: that is what was asked for, and an alias can resolve
    # to a dated snapshot, so pricing and logging against it would attribute a
    # row to a model that may never have served it. It is also read at log time,
    # which means a config edit between the run and the log would relabel the
    # row -- exactly the class of drift this project treats as a defect.
    #
    # None when no Stage 5 response was obtained: node_no_candidates, or a
    # failure before the first call returned. The column then stores NULL,
    # which says "no model produced this row" rather than naming one that did
    # not run.
    matching_model_used = result.get("matching_model")

    # Calculate cost using pricing config. Outside the try — see the docstring.
    #
    # MATCHING_MODEL is the pricing key ONLY in the None case above, where
    # there are no Stage 5 tokens to price and the arithmetic is 0 x rate = 0
    # whichever priced model is named. This is not a recovery path around
    # get_model_cost(): the lookup still happens, still raises
    # UnknownModelPricingError for an unpriced model, and still sits outside
    # the try block so an unpriced model aborts the whole log rather than
    # writing a row that claims the run was free. What it is not allowed to do
    # is raise on a no-candidates run purely because that run has no model name
    # to look up.
    #
    # WHICH PATH WAS TAKEN IS RECORDED, as this project requires of any
    # fallback: matching_model is written NULL on exactly the rows where the
    # fallback key was used, so "priced against the model that answered" and
    # "priced against the configured model because nothing answered" are
    # separable in the table without a second column. A NULL matching_model row
    # carrying non-zero gpt4o tokens would be the one case where they are not,
    # and File 16's Query 10 and File 21's cost tab both call that out.
    #
    # Reasoning tokens are NOT added to the output figure here. They are
    # already inside gpt4o_output_tokens (see the schema note on
    # gpt4o_reasoning_tokens); adding them would bill every one of them twice.
    total_cost = get_model_cost(
        matching_model_used or MATCHING_MODEL,
        result.get("gpt4o_input_tokens", 0),
        result.get("gpt4o_output_tokens", 0)
    )

    conn = None
    try:
        # Item 20b: the schema is no longer created when this file is loaded,
        # so it is ensured here, once per resolved path, before the first
        # write. Inside the try on purpose: a table that cannot be created is
        # a database failure, and this function's contract is that database
        # failures are reported and do not kill the pipeline. That is the
        # opposite of get_model_cost() above, which is outside the try because
        # an unpriced model is a configuration defect, not a database one.
        _ensure_database(db_path)

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        demographics = patient_data.get("demographics", {})
        conditions = patient_data.get("conditions", [])
        timings = result.get("stage_timings", {})

        # ECOG performance status. Preferred source is the result dict, where
        # _pipeline_provenance() (File 13) puts it on all three terminal paths;
        # the patient dict is the fallback for a caller logging a result that
        # did not come from the graph.
        #
        # The source is chosen ONCE for all three columns rather than per field.
        # Per-field fallback could take the value from one patient and the
        # selection path from another, producing a row that describes no patient
        # at all -- and the three columns are only interpretable together.
        #
        # ecog_selection is the marker for "did this report", the same role it
        # plays in the schema comment above: a terminal node sets it to a string
        # whenever the parsed field was present and leaves it None when it was
        # not. It is used instead of ecog_value because ecog_value is
        # legitimately None for a patient with no observation, and legitimately
        # 0 -- falsy, and the most eligible score there is -- for a fully active
        # one. Neither can mark presence.
        _patient_ecog = patient_data.get("ecog_performance_status") or {}
        if result.get("ecog_selection") is not None:
            ecog_value              = result.get("ecog_value")
            ecog_selection          = result.get("ecog_selection")
            ecog_observations_found = result.get("ecog_observations_found")
        else:
            ecog_value              = _patient_ecog.get("value")
            ecog_selection          = _patient_ecog.get("selection")
            ecog_observations_found = _patient_ecog.get("observations_found")

        # Sum of stage durations only — excludes LangGraph routing overhead (~50-200ms)
        total_time = sum(timings.values())

        cursor.execute('''
            INSERT INTO inferences (
                patient_id, timestamp, age, sex, race, ethnicity, primary_condition,
                condition_count, medication_count, allergy_count, expanded_query,
                candidates_retrieved, candidates_reranked, 
                bm25_retrieved, vector_retrieved, 
                candidates_after_rule_filter,
                candidates_after_quality_filter,
                candidates_filtered, mesh_dropped, mesh_resolution,
                stage_dropped, histology_dropped,
                candidates_evaluated,
                eligible_matches, near_misses,
                not_evaluable_trials, cross_vocab_remaps,
                query_expansion_time, hybrid_retrieval_time, cross_encoder_time,
                rule_filter_time, gpt4o_evaluation_time, total_time,
                gpt4o_prompt, gpt4o_input_tokens, gpt4o_output_tokens,
                matching_model, cross_encoder_model,
                pricing_version, estimated_cost_usd, qdrant_collection, error,
                patient_data_hash, expansion_prompt,
                gpt4o_retries, ablation_flags, hallucinated_trials,
                retrieval_channels, retrieval_channels_expected,
                retrieval_channels_ok, retrieval_degraded,
                retrieval_trials_lost, query_expansion_path,
                mesh_filter_applied, mesh_filter_skip_reason,
                age_reference_date, birth_date_precision,
                ecog_value, ecog_selection, ecog_observations_found,
                gpt4o_truncation_splits, gpt4o_output_tokens_estimated,
                not_evaluable_truncated, gpt4o_calls,
                gpt4o_reasoning_tokens
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            result["patient_id"],
            result["timestamp"],
            demographics.get("age"),
            demographics.get("sex"),
            demographics.get("race"),
            demographics.get("ethnicity"),
            result.get("primary_condition") or _resolve_primary_cancer(conditions),
            result.get("condition_count", len(deduplicate_by_display(patient_data.get("conditions", [])))),
            result.get("medication_count", len(deduplicate_by_display(patient_data.get("medications", [])))),
            result.get("allergy_count", len(patient_data.get("allergies", []))),
            result.get("expanded_query", ""),
            result.get("candidates_retrieved", 0),
            result.get("candidates_reranked", 0),
            # Observed per-channel counts from Stage 2, not the configured
            # request sizes. Inserting BM25_RETRIEVAL_SIZE / VECTOR_RETRIEVAL_SIZE
            # here made both columns constant across every row, so any ratio
            # built on them (File 16's fusion_efficiency) described the config
            # rather than the run, and a single-channel ablation still logged
            # both channels as full. NULL when the key is absent, which means a
            # result dict that did not come from a pipeline terminal node.
            result.get("bm25_retrieved"),
            result.get("vector_retrieved"),
            result.get("candidates_after_rule_filter", 0),
            result.get("candidates_after_quality_filter", 0),
            result.get("candidates_filtered", 0),
            result.get("mesh_dropped", 0),
            result.get("mesh_resolution", ""),
            result.get("stage_dropped", 0),
            result.get("histology_dropped", 0),
            result.get("candidates_evaluated", 0),
            len(result.get("matches", [])),
            len(result.get("near_misses", [])),
            # Non-evaluations are counted here, never folded into near_misses:
            # a trial that could not be assessed is not a rejection.
            result.get("not_evaluable_trials", len(result.get("not_evaluable", []))),
            result.get("cross_vocab_remaps", 0),
            timings.get("query_expansion", 0),
            timings.get("hybrid_retrieval", 0),
            timings.get("cross_encoder", 0),
            timings.get("rule_filter", 0),
            timings.get("gpt4o_evaluation", 0),
            total_time,
            result.get("gpt4o_prompt", ""),
            result.get("gpt4o_input_tokens", 0),
            result.get("gpt4o_output_tokens", 0),
            # Resolved above, outside the tuple, because the same value is what
            # get_model_cost() was called with. Reading it twice could price a
            # row against one model and label it with another.
            matching_model_used,
            "ncbi/MedCPT-Cross-Encoder",
            PRICING_CONFIG["last_updated"],
            total_cost,
            result.get("qdrant_collection", ""),
            result.get("error", ""),
            result.get("patient_data_hash", ""),
            result.get("expansion_prompt", ""),
            # Written by all three terminal nodes via _pipeline_provenance()
            # (File 13). Reading "gpt4o_retries_exhausted" here logged 0 for
            # every run that did not end in node_error_handler, because that
            # node was the only writer of the old key.
            result.get("gpt4o_retries", 0),                  # gpt4o_retries
            json.dumps(result.get("ablation_flags") or {}),  # ablation_flags
            # NULL until item 33's detector writes the key: see the migration
            # note above for why this is not defaulted to 0.
            result.get("hallucinated_trials"),               # hallucinated_trials
            # Degradation record. Every one of these is .get() with no default,
            # so a result dict that never reached the stage in question writes
            # NULL rather than a value that would read as "checked, all clean".
            # retrieval_channels is serialized only when present: json.dumps(None)
            # would store the string 'null', which is not the same as SQL NULL.
            (json.dumps(result["retrieval_channels"])
             if result.get("retrieval_channels") else None),
            result.get("retrieval_channels_expected"),
            result.get("retrieval_channels_ok"),
            result.get("retrieval_degraded"),
            result.get("retrieval_trials_lost"),
            result.get("query_expansion_path"),
            # bool -> 0/1 for SQLite, but None stays None: "the filter did not
            # report" is a third state and must not collapse into "did not run".
            (None if result.get("mesh_filter_applied") is None
             else int(bool(result["mesh_filter_applied"]))),
            result.get("mesh_filter_skip_reason"),
            # Age provenance. The reference date comes from the result, written
            # by _pipeline_provenance() (File 13) on all three terminal paths;
            # it falls back to the patient dict only for a caller that logs a
            # result it did not get from the graph. Both stay NULL when neither
            # reported: the age in this row is then not reproducible, and that
            # must not read as "computed against today".
            (result.get("age_reference_date")
             or demographics.get("age_reference_date")),
            (result.get("birth_date_precision")
             or demographics.get("birth_date_precision")),
            # ECOG. Resolved above, outside the tuple, because the value needs an
            # `is None` test rather than the `or` chain used for the age columns:
            # `or` would treat a legitimate ECOG 0 -- fully active, the most
            # eligible a patient can be -- as absent.
            ecog_value,
            ecog_selection,
            ecog_observations_found,
            # Stage 5 truncation record. The three counts default to 0 because
            # a run that ended before Stage 5 genuinely performed zero splits
            # and lost zero trials to truncation; the ESTIMATE has no default,
            # because a run that never estimated anything did not estimate 0.
            result.get("gpt4o_truncation_splits", 0),
            result.get("gpt4o_output_tokens_estimated"),
            result.get("not_evaluable_truncated", 0),
            result.get("gpt4o_calls", 0),
            # No default. A response that carried no reasoning breakdown, and a
            # response that spent zero reasoning tokens, are different facts;
            # .get() with no default stores NULL for the first and 0 for the
            # second. Defaulting to 0 here would make every GPT-4o-era row and
            # every stubbed run look like a reasoning run that did no thinking.
            result.get("gpt4o_reasoning_tokens"),
        ))
        
        inference_id = cursor.lastrowid
        
        # not_evaluable trials are written too, with eligible = "not_evaluable",
        # so the criterion-level record exists for anything that reads back the
        # non-evaluations rather than only their count.
        all_trials = (
            result.get("matches", [])
            + result.get("near_misses", [])
            + result.get("not_evaluable", [])
        )

        for match in all_trials:
            # Build criterion details JSON from inclusion/exclusion arrays
            inclusion = match.get("inclusion_criteria", [])
            exclusion = match.get("exclusion_criteria", [])
            inclusion = inclusion if isinstance(inclusion, list) else []
            exclusion = exclusion if isinstance(exclusion, list) else []
            criterion_json = json.dumps({
                "inclusion":       inclusion,
                "exclusion":       exclusion,
            })
            
            cursor.execute('''
                INSERT INTO trial_matches (
                    inference_id, nct_id, trial_title, trial_phase,
                    trial_number, rerank_score, rerank_score_raw, mesh_boost, mesh_boost_tier,
                    match_score, eligible, explanation, criterion_details,
                    score_confirmed, score_denominator, criteria_not_applicable,
                    hallucinated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                inference_id,
                match.get("nct_id", ""),
                match.get("title", ""),
                match.get("phase", ""),
                match.get("trial_number"),
                match.get("rerank_score"),
                match.get("rerank_score_raw"),
                match.get("mesh_boost"),
                match.get("mesh_boost_tier"),
                match.get("match_score", 0.0),
                match.get("eligible", "not_eligible"),
                match.get("explanation", ""),
                criterion_json,
                match.get("score_confirmed"),
                match.get("score_denominator"),
                match.get("criteria_not_applicable"),
                match.get("hallucinated"),   # NULL until item 33's detector runs
            ))
        
        conn.commit()
        print(f"✓ Logged inference for patient {result['patient_id']} (ID: {inference_id})")
        
    except sqlite3.Error as e:
        if conn:
            conn.rollback()
        print(f"⚠ Database logging failed (non-critical): {e}")
        # DO NOT re-raise - logging failure should not break pipeline
    
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"⚠ Logging error (non-critical): {e}")
        # DO NOT re-raise - logging failure should not break pipeline
    
    finally:
        if conn:
            conn.close()

    # AFTER the finally, not inside it. A return inside a finally block
    # SWALLOWS any exception propagating out of the try -- and one exception is
    # meant to propagate from this function: UnknownModelPricingError is raised
    # above the try, so it never reaches here, but a KeyboardInterrupt or a
    # MemoryError raised inside the try would be discarded by a `return` in the
    # finally and the caller would be told the write succeeded.
    return db_path


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Feb 12 13:26:56 2026
@author: ramyalsaffar
"""
