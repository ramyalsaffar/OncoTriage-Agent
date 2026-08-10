"""SQLite schema and the inference logger.

Moved out of ``14- Database Logger.py`` by item 20c, pass 2b.
``14- Database Logger.py`` survived as an explicit re-export shim over this
module because Files 17, 25, 26, 32, 36, 37, 38, 40 and 45 exec-chained it, and
IS DELETED AS OF PASS 20e: all nine were measured and none is a chainer any more
(17, 25 and 26 became thin entry points; 32, 36, 37, 38 and 40 became modules
under ``tests/`` in pass 20d-1; 45 became ``oncotriage/fixtures/capture.py``).

THE SHIM'S ``log_inference`` WRAPPER WENT WITH IT, and the argument for it is
kept here because the argument is about THIS function. The wrapper was
``log_inference(result, patient_data, db_path=None)`` with
``db_path = globals().get("inferences_path")`` — defined inside the exec'd text,
so its ``__globals__`` WAS the shared namespace and the lookup stayed live. That
mattered because five files rebound ``inferences_path`` at a temporary database
and only then loaded File 14; without the wrapper all five would have written
real rows into the real ``inferences.db`` while printing the name of the
temporary file each thought it was using. Silent in both directions.

By pass 20d-1 all five ALSO passed ``db_path=`` explicitly and asserted on the
path this function returns, which is why the wrapper's removal changes nothing:
File 14's own docstring recorded "no remaining consumer in the repository" a
pass before it was deleted. THE RULE THAT OUTLIVES IT: this function must keep
taking the database as an argument and must keep RETURNING the path it wrote to,
because those two together are what let an isolation test assert where it wrote
instead of trusting that it wrote somewhere else.

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

   ``None`` means ``oncotriage.paths.inferences_path``, or
   ``ONCOTRIAGE_INFERENCES_DB`` when that is set — see
   ``resolve_inference_db_path`` for the three-tier order and for why the
   argument deliberately outranks the variable. The five test files pass the
   path explicitly, which is the only mechanism now that the shim's late-binding
   wrapper is gone (pass 20e).

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
   imports the other. This module re-exports it, which is what
   ``tests/test_fhir_birth_date_and_demographics.py`` section 9b reaches — the
   only place in the repository that touches the storage layer without the
   agent.

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
import threading
import time
from collections import Counter
from typing import Dict

from oncotriage import paths
from oncotriage import settings
from oncotriage.config import (
    CROSS_ENCODER_MODEL,
    MATCHING_MODEL,
    PRICING_CONFIG,
    SQLITE_BUSY_TIMEOUT_SECONDS,
    SQLITE_JOURNAL_MODE,
    SQLITE_WRITE_MAX_ATTEMPTS,
    SQLITE_WRITE_RETRY_BASE_DELAY,
)
from oncotriage.registries.primary_cancer import _resolve_primary_cancer
from oncotriage.utils import deduplicate_by_display, get_model_cost
from oncotriage.observability import console, get_logger

log = get_logger(__name__)


#------------------------------------------------------------------------------


def resolve_inference_db_path(db_path=None):
    """The database ``log_inference`` will write to for this call.

    Three tiers, first match wins:

        1. ``db_path`` -- an explicit argument, returned unmodified;
        2. ``ONCOTRIAGE_INFERENCES_DB`` (pass 20c-3i);
        3. ``oncotriage.paths.inferences_path``, the configured production
           database, resolved on this call -- see that module for why
           resolution is lazy.

    Returns:
        The path string.

    WHY TIER 2 EXISTS. "17- FastAPI Server.py" calls ``log_inference(result,
    patient_data)`` with no path, and it cannot sensibly do otherwise -- it is a
    server handling requests, not a test that knows where its output belongs. So
    every run of "18- FastAPI Server Test.py" or "19- FastAPI Server Batch
    Test.py" against a live server wrote real rows into the real production
    database. That is not hypothetical: six such rows dated 2026-08-05 are in
    it, and they changed which query "16- Database Query.py" dies at.

    The server is a separate process, so the redirect has to be settable from
    OUTSIDE the process that decides to log. An environment variable is the only
    channel that reaches it:

        ONCOTRIAGE_INFERENCES_DB=/tmp/t.db python "17- FastAPI Server.py"

    ``oncotriage/monitoring/drift.py:resolve_drift_db_path`` honours the same
    variable, deliberately without importing this function -- see its docstring.

    THIS FUNCTION DOES NOT CONSULT THE EXEC NAMESPACE, and that asymmetry is on
    purpose. The shim's ``log_inference`` wrapper is what reads
    ``globals().get("inferences_path")``; this one always answers "what does a
    caller that passed nothing get", which is exactly the question the five
    isolation tests need answered in order to show that passing the scratch path
    is doing any work. If this resolved through the namespace too, those tests
    would be comparing a value against itself.

    THE ARGUMENT STILL WINS OVER THE VARIABLE, and that ordering is what keeps
    those five tests meaningful. They pass an explicit scratch path and assert
    on the path returned; if the variable outranked the argument, a stray export
    in the operator's shell would silently redirect a test that had asked for
    somewhere specific, and the assertion would report the redirect as the
    answer it wanted.

    It resolves and returns; it opens nothing. Calling it is safe on a machine
    with a database it must not touch. The one thing it can RAISE is a
    RuntimeError from ``resolve_inferences_db`` when the variable names a path
    whose parent directory is absent -- deliberately, because both callers
    resolve outside their try block so a configuration defect reaches the
    operator rather than being swallowed as a logging fault.
    """
    if db_path is not None:
        return db_path
    override, _source = settings.resolve_inferences_db()
    if override is not None:
        return override
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
# tests/test_storage_ecog_logging.py reads INFERENCE_COLUMN_ADDITIONS directly. The
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
    # Count of entries the model returned an evaluation for that were never in
    # the candidate set sent to it. THE DETECTOR EXISTS NOW: it runs per chunk
    # in node_llm_classifier_evaluation, drops every such entry before it can
    # be enriched or scored, and writes the total into
    # result["hallucinated_trials"] via _pipeline_provenance.
    #
    # FABRICATED ONLY, WHICH IS WHAT THE SENTENCE ABOVE HAS ALWAYS SAID. The
    # detector drops two kinds of entry and this column counts one of them: an
    # id that is in no candidate set of the run. The other kind -- an id in the
    # node's sent set but not in the chunk that answered, which is the model
    # answering the whole batch to every call of a SPLIT request -- is dropped
    # by the same code and counted only in the `out_of_set_entry` log event,
    # under cross_chunk_count / cross_chunk_nct_ids. It costs the patient
    # nothing (that id's own chunk answers it, or the reconciliation records it
    # as omitted) and folding it in here would make a split run's number
    # incomparable with an unsplit run's.
    #
    # 0 IS A MEASUREMENT AND NULL IS NOT. A normal run stores 0, which asserts
    # that every returned entry was compared against the candidate set and
    # every one belonged to it. NULL means no such comparison was completed --
    # a row written before the detector existed, or a run that ended at an API
    # failure, a refusal or an unparseable response, where Stage 5's success
    # return was never reached. Never fold the two together, and never default
    # this to 0 in a reader.
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
    # Two counters because there are two budgets. llm_classifier_retries counts whole-
    # node retries for a malformed or failed response; llm_classifier_truncation_splits
    # counts levels of halving spent because a response was CUT OFF at the
    # model's output ceiling. Before this, a truncated response fell through to
    # the JSON parser, failed there, and was retried as an identical request
    # that truncated again -- so a truncation was logged as three parse
    # retries, and the two causes were indistinguishable in the record.
    #
    # llm_classifier_output_tokens_estimated is the pre-call estimate, stored beside the
    # actual in llm_classifier_output_tokens. That column pair is what the constants in
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
    # llm_classifier_calls is how many requests the stage actually issued. Without it a
    # split run and an unsplit one are indistinguishable in the token columns,
    # because the tokens are summed across chunks.
    "llm_classifier_truncation_splits":      "INTEGER",
    "llm_classifier_output_tokens_estimated": "INTEGER",
    "not_evaluable_truncated":      "INTEGER",
    "llm_classifier_calls":                  "INTEGER",

    # --- Reasoning-model accounting (item 29a, gpt-5.6-terra migration) ------
    #
    # The reasoning share OF llm_classifier_output_tokens. NOT an additional charge.
    # OpenAI's reasoning guide and a live probe on 2026-08-04 both put
    # usage.completion_tokens_details.reasoning_tokens INSIDE
    # usage.completion_tokens, billed at the output rate. So:
    #
    #     estimated_cost_usd already includes these tokens.
    #     llm_classifier_output_tokens already includes these tokens.
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
    "llm_classifier_reasoning_tokens":       "INTEGER",

    # --- Which Stage 5 system prompt produced this row ----------------------
    #
    # READ THIS BEFORE WRITING A QUERY AGAINST EITHER COLUMN.
    #
    # llm_classifier_prompt_sha256 IS NOT sha256(llm_classifier_prompt). The
    # prompt column holds the SYSTEM message and the USER message concatenated
    # ("[SYSTEM]\n...\n\n[USER]\n..."), and the user half carries this
    # patient's record, so its hash identifies the PATIENT. This column hashes
    # the SYSTEM message alone, which is what identifies the TEMPLATE and is
    # therefore the thing that can be grouped on across patients. The two
    # cannot be reconciled by re-hashing the stored text and must not be
    # compared with each other.
    #
    # llm_classifier_prompt_version is hand-maintained in
    # oncotriage/agent/prompts.py and says what a human intended; the hash is
    # computed per call and says what was actually sent. They can disagree --
    # an edit made without bumping the version leaves two runs sharing a
    # version and differing in hash -- and that disagreement is exactly what
    # the pair exists to make visible. Trust the hash for identity; read the
    # version for intent.
    #
    # NULL AND NOT-NULL MEAN DIFFERENT THINGS ON THE TWO COLUMNS:
    #
    #   version NULL   the row predates this migration, or was logged by a
    #                  caller that did not come from a pipeline terminal node.
    #   version SET    this build's template version. Set on EVERY terminal
    #                  path, including the ones where Stage 5 never ran, because
    #                  it is a property of the code rather than of the run.
    #   hash NULL      no system prompt was ever rendered for this row --
    #                  node_no_candidates, or a failure upstream of Stage 5.
    #                  It is NOT "the hash was not recorded".
    #   hash SET       these are the exact bytes the model was sent. One value
    #                  per inference even when the batch split into chunks: the
    #                  system message is rendered once and reused for every
    #                  chunk, and only the user message differs.
    #
    # So "did Stage 5 run" is `llm_classifier_prompt_sha256 IS NOT NULL`, never
    # a test on the version.
    "llm_classifier_prompt_version":         "TEXT",
    "llm_classifier_prompt_sha256":          "TEXT",
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
    # Per-trial marker for the same detection as inferences.hallucinated_trials.
    # Written from match["hallucinated"], which Stage 5 stamps onto every
    # surviving evaluation on its success path.
    #
    # TWO VALUES ARE REACHABLE AND THE THIRD IS NOT, BY CONSTRUCTION.
    #   0    = this row was checked and its NCT ID was in the candidate set.
    #   NULL = no check ran for this row: a run that ended before Stage 5
    #          completed, a result dict built outside the pipeline, or a row
    #          written before the detector existed.
    #   1    NEVER APPEARS. An entry outside the candidate set is dropped in
    #          node_llm_classifier_evaluation before enrichment, so it becomes
    #          no evaluation and therefore no row. The count of what was
    #          dropped lives in inferences.hallucinated_trials, which is the
    #          only place it can live -- there is no trial to hang it on.
    # The value is kept as a marker rather than removed because 0 against NULL
    # is what separates a checked row from an unchecked one, which is the whole
    # question this column answers.
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


#------------------------------------------------------------------------------


# ===========================================================================
# WRITE DURABILITY (the write-durability pass)
# ===========================================================================
#
# THE DEFECT. ``_write_inference_row`` catches ``sqlite3.Error``, rolls back,
# prints "Database logging failed (non-critical)" and continues -- and
# ``log_inference`` then returns ``db_path`` exactly as it does on success. The
# caller cannot tell the row was lost, so the patient is recorded as successful
# and the run reports complete. Every number in the paper comes from one final
# run; if that run loses rows and reports complete, the result looks whole and
# is not.
#
# NOT RE-RAISING IS STILL RIGHT. The existing comment is correct that a logging
# fault must not destroy a ~70-second pipeline result that cost a live Stage 5
# call. What was wrong was that it also did not TELL anyone. Three things close
# that, in the order they take effect:
#
#   1. WAL and an explicit busy timeout, so contention mostly does not happen;
#   2. a bounded retry, so transient contention that does happen is survived;
#   3. an outcome the caller can read, and a counter, so a write that is lost
#      anyway is visible from the return value, from the log, and from the
#      batch summary's reconciliation.
#
# WHAT THIS DELIBERATELY DOES NOT TOUCH: ``_WRITE_LOCK``, the schema, and the
# broad ``except Exception`` below ``except sqlite3.Error``. The lock closes the
# IN-PROCESS race and is measured doing so by
# ``tests/test_package_invariants.py`` section 5e; everything here is about the
# processes it cannot reach.


INFERENCE_WRITE_FAILURES = Counter()
"""Inference writes that were given up on, keyed ``{ExceptionType}:{retryable}``.

Module-level, following ``AGE_PARSE_FAILURES`` and ``CHECKPOINT_WRITE_FAILURES``
rather than becoming a new column: this is a property of the RUN, and a new
column would mean a schema migration to record that a row could not be written,
which is circular.

The ``retryable`` half is the diagnosis. ``sqlite3.OperationalError:retryable``
means contention outlived ``SQLITE_WRITE_MAX_ATTEMPTS`` and the fix is more
attempts, a longer timeout or fewer writers. ``sqlite3.IntegrityError:terminal``
means the write was never going to succeed and retrying it would only have made
the run slower.
"""

INFERENCE_WRITE_RETRIES = Counter()
"""Retries actually made, keyed by exception type. Attempts, not calls.

Separate from the failure counter because the two answer different questions: a
run with 400 retries and 0 failures is one where this pass did its job, and a run
with 0 of each is one where there was no contention to survive. Folding them
together would make those two indistinguishable.
"""

JOURNAL_MODE_DEGRADATIONS = Counter()
"""Databases whose journal mode is not what ``SQLITE_JOURNAL_MODE`` asked for.

Keyed ``requested->actual``. WAL is a property of the FILE, not of the
connection, and it can fail to take -- a network filesystem cannot provide the
shared memory the wal-index needs, and a read-only directory cannot hold the
``-wal`` file. Both leave the pragma returning the OLD mode with nothing raised,
which is the silent-degradation shape this project exists to remove.
"""


class InferenceWriteResult(str):
    """The database path this call wrote to, plus whether the row landed.

    A ``str`` SUBCLASS, and the choice is forced rather than clever. Before this
    pass ``log_inference`` returned ``db_path``, and that return value is a
    pinned contract in five places:

        tests/test_storage_ecog_logging.py:328
        tests/test_storage_inference_logging_contract.py:811, :910
        tests/test_agent_retrieval_observability.py:994, :1027, :1061
        tests/test_fhir_birth_date_and_demographics.py:896

    each of which compares it with ``==`` against its own scratch path. That
    comparison is what makes those five isolation tests checkable at all, so it
    may not break. A subclass of ``str`` compares, hashes, formats,
    ``os.path``-joins and JSON-serialises exactly as the path string did, while
    carrying the four facts a caller now needs.

    WHAT CONSTRAINS THE SHAPE, read rather than assumed. The two production
    callers -- ``oncotriage/batch/runner.py`` line 370 and
    ``oncotriage/api/server.py`` line 280 -- both DISCARD the return value
    today. So a return value alone reaches neither, which is why the counters
    above and ``runner``'s ledger exist as well; the batch runner is changed to
    read ``.ok``, and the API server deliberately is not (see the note there).

    Attributes:
        ok:           True only if the row and its children are committed.
        error:        ``"{Type}: {message}"`` when not, else None.
        attempts:     Attempts made, 1 when it worked first time.
        inference_id: The ``inferences.id`` assigned, or None if nothing landed.
                      This is what makes reconciliation exact rather than
                      statistical -- see ``runner.reconcile_writes``.
    """

    # __slots__ so an instance cannot silently grow an attribute that a reader
    # then trusts; str subclasses get no __dict__ from this alone, which is the
    # point.
    __slots__ = ("ok", "error", "attempts", "inference_id")

    def __new__(cls, db_path, ok=True, error=None, attempts=1,
                inference_id=None):
        obj = super().__new__(cls, db_path)
        obj.ok = bool(ok)
        obj.error = error
        obj.attempts = int(attempts)
        obj.inference_id = inference_id
        return obj

    def __repr__(self):
        # NOT str.__repr__. A caller who prints this in a diagnosis must see the
        # outcome, not just a path that looks like a success. Equality, which is
        # what the five pinned tests use, is untouched: it comes from str.
        return (f"InferenceWriteResult({str.__repr__(self)}, ok={self.ok}, "
                f"attempts={self.attempts}, inference_id={self.inference_id!r})")


# THE RETRYABLE CLASS IS NARROW, AND THAT IS A DECISION WITH A CONTROL BEHIND IT.
#
# "Retry the write" is only correct for failures that are transient. SQLite
# reports contention as an OperationalError whose message names a lock or a busy
# database; those clear on their own and a retry is exactly right.
#
# WHAT IS DELIBERATELY NOT RETRIED, and this is the important half:
#
#   "duplicate column name: X" is ALSO a sqlite3.OperationalError, and retrying
#   it would in fact succeed -- the second thread's ALTER already added the
#   column, so a second attempt finds the schema complete and the INSERT lands.
#   It is excluded anyway. That error is the signature of the migration race
#   ``_WRITE_LOCK`` exists to close, and
#   tests/test_package_invariants.py section 5e proves the lock necessary by
#   STRIPPING it and requiring rows to be lost. A retry broad enough to repair
#   that race would repair the negative control too, and the check whose whole
#   job is to show the lock is load-bearing would start passing for free.
#   Silently deleting the evidence for a lock is worse than not retrying an
#   error that the lock already prevents.
#
#   IntegrityError, ProgrammingError, DatabaseError-on-corruption and a full
#   disk are not transient at all. Retrying them spends SQLITE_WRITE_MAX_ATTEMPTS
#   x SQLITE_BUSY_TIMEOUT_SECONDS of a batch run to arrive at the same failure.
#
# Matched on the MESSAGE because sqlite3 does not expose a distinct exception
# type for contention; `sqlite3.OperationalError` covers both cases above. The
# strings are SQLite's own and stable ("database is locked", "database table is
# locked", "database is busy"), and the match is substring-and-lowercase so a
# wrapped or prefixed message still resolves.
_RETRYABLE_MESSAGE_MARKERS = ("database is locked", "database table is locked",
                              "database is busy", "database schema is locked")


def _is_retryable(exc):
    """True if exc is transient contention worth another attempt.

    Returns False for every other sqlite3.Error, including the migration race --
    see the block above for why that exclusion is deliberate.
    """
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    message = str(exc).lower()
    return any(marker in message for marker in _RETRYABLE_MESSAGE_MARKERS)


def _open_connection(db_path):
    """``sqlite3.connect`` with this project's busy timeout applied.

    THE TIMEOUT IS PER CONNECTION, so it has to be set on every one of them --
    it is not a property of the file the way the journal mode is. Passed to
    ``connect()`` rather than issued as a PRAGMA afterwards because the
    connection attempt itself can meet a locked database, and a PRAGMA on the
    next line would be too late to help it.

    ``sqlite3.connect`` takes SECONDS as a float; ``PRAGMA busy_timeout`` takes
    MILLISECONDS as an integer. Mixing those up gives a 30-millisecond timeout
    that looks like a 30-second one, so the config constant is in seconds and
    the conversion happens in exactly one place, below.
    """
    return sqlite3.connect(db_path, timeout=SQLITE_BUSY_TIMEOUT_SECONDS)


def _apply_journal_mode(conn, db_path):
    """Set ``SQLITE_JOURNAL_MODE`` on db_path and VERIFY it took.

    Returns the mode the database is actually in, lowercased.

    WHY VERIFY. ``PRAGMA journal_mode=WAL`` does not raise when it cannot be
    honoured; it returns the mode still in force. On a network filesystem (no
    shared memory for the wal-index) or in a read-only directory, that is the
    old mode, and a caller that assumed it took would go on believing readers
    and the writer no longer block each other. The pragma statement RETURNS the
    resulting mode, so the check costs nothing extra -- but it has to be read,
    and reading it is the entire mechanism.

    LOUD, ONCE PER DATABASE PER PROCESS. This runs inside initialize_database,
    which runs once per resolved path per process, so a mismatch is one WARNING
    and one counter increment rather than one per row.
    """
    requested = str(SQLITE_JOURNAL_MODE).strip().lower()

    row = conn.execute(f"PRAGMA journal_mode = {requested}").fetchone()
    actual = str(row[0]).lower() if row else "unknown"

    if actual != requested:
        JOURNAL_MODE_DEGRADATIONS[f"{requested}->{actual}"] += 1
        console.out(
            f"⚠ SQLite journal mode: asked for {requested.upper()}, the "
            f"database is in {actual.upper()}. Concurrent readers and the "
            f"writer will block each other, so a second writing process can "
            f"still lose rows under contention.\n"
            f"    Database: {db_path}\n"
            f"    Usual causes: the file is on a network filesystem (WAL needs "
            f"shared memory the mount cannot provide), or its directory is not "
            f"writable.\n"
            f"    Set SQLITE_JOURNAL_MODE in oncotriage/config.py to "
            f"'{actual}' to accept this deliberately and stop this warning.")
        log.warning("sqlite journal mode not applied",
                    event="journal_mode_degraded",
                    journal_mode_requested=requested, journal_mode=actual,
                    db_path=str(db_path))
    else:
        log.info("sqlite journal mode applied", event="journal_mode",
                 journal_mode=actual, db_path=str(db_path))

    return actual


# ---------------------------------------------------------------------------
# THE WRITE LOCK (pass 20c-3b)
# ---------------------------------------------------------------------------
#
# WHERE IT USED TO LIVE, AND WHY THAT WAS WRONG.
#
# "25- Batch Runner.py" lines 65-73 did this, at module level, after chaining
# File 14:
#
#     _db_lock = threading.Lock()
#     _original_log_inference = log_inference
#     def _thread_safe_log_inference(*args, **kwargs):
#         with _db_lock:
#             return _original_log_inference(*args, **kwargs)
#     log_inference = _thread_safe_log_inference
#
# It worked -- for File 25. It is a MONKEYPATCH IN ONE CALLER, so every other
# concurrent caller of log_inference had no lock at all, and there is one:
#
#     "17- FastAPI Server.py" line 191 calls log_inference from
#     loop.run_in_executor(None, _run_matching_pipeline, ...), i.e. from the
#     default ThreadPoolExecutor, on as many threads as there are in-flight
#     requests. Two overlapping POST /match requests were writing to the same
#     SQLite file through two connections with no serialization whatever.
#
# WHAT THAT ACTUALLY RISKS, stated rather than gestured at. The write is not one
# statement: it is _ensure_database (DDL), an INSERT into inferences, a read of
# cursor.lastrowid, N INSERTs into trial_matches keyed on that id, and a commit.
# sqlite3's own locking makes each STATEMENT safe; it does not make that
# SEQUENCE atomic. Two unserialized writers on one file give you, in rising
# order of nastiness:
#
#   1. "database is locked" OperationalError under contention, which
#      log_inference CATCHES and reports as non-critical -- so the row is simply
#      lost and the run reports success. Silent data loss, which is the one
#      failure mode this project exists to remove.
#   2. a rolled-back inference INSERT whose trial_matches rows were already
#      committed by the other connection's commit, leaving trial_matches rows
#      pointing at an inference_id that is not there.
#
# So the lock moves HERE, beside the writes it protects, where every caller gets
# it and no caller has to know it exists. File 25's monkeypatch is deleted.
#
# THIS IS A DELIBERATE BEHAVIOUR CHANGE FOR FILE 17, and it is the point of the
# move: the API's concurrent writers are serialized now and were not before.
#
# ONE GLOBAL LOCK, NOT ONE PER PATH. A dict of per-path locks needs its own lock
# to populate safely, and it would buy nothing measurable: a process writes one
# database, the critical section is a handful of milliseconds of SQLite work,
# and it sits inside a per-patient pipeline whose measured median is ~68 seconds
# of Stage 5 alone. Twelve threads queueing microseconds behind each other at
# the end of a minute of work is not a bottleneck.
#
# AN RLock, NOT A Lock. log_inference takes it and then calls _ensure_database,
# which calls initialize_database, which takes it again. A plain Lock would
# deadlock the first time a batch run met an uninitialized database.
#
# WHAT IT DOES NOT COVER: get_model_cost(). That is called BEFORE the lock and
# before the try, for the reason written at log_inference -- it touches no
# database, and an unpriced model must reach the caller rather than be held up
# behind, or swallowed by, database machinery.
_WRITE_LOCK = threading.RLock()


def initialize_database(db_path):
    """Create the three tables at db_path and apply the additive migrations.

    Idempotent: every CREATE is IF NOT EXISTS and every ALTER is guarded by a
    PRAGMA table_info check, so calling this on an existing database adds only
    what is missing and destroys nothing.

    Returns the resolved absolute path, so a caller can log where it wrote.

    HOLDS THE WRITE LOCK (pass 20c-3b). This runs DDL and mutates
    _INITIALIZED_DATABASES; two threads meeting an uninitialized database would
    otherwise both run the migration loop and both mutate the set. The body is a
    separate function purely so the SQL below keeps its exact indentation --
    those CREATE statements are flush at column 0 inside their triple-quoted
    strings on purpose, because SQLite stores the CREATE text verbatim in
    sqlite_master.sql and re-indenting them would change the recorded schema.
    """
    with _WRITE_LOCK:
        return _initialize_database_locked(db_path)


def _initialize_database_locked(db_path):
    """initialize_database's body. Callers hold _WRITE_LOCK."""
    # Connect
    # It will create it if deos not exist, and it won't override if it does.
    #
    # Through _open_connection so this connection carries the same busy timeout
    # every other one does. It matters MORE here than on the insert path: this
    # is where the ALTER TABLE migrations run, and DDL takes an exclusive lock.
    conn = _open_connection(db_path)

    # THE JOURNAL MODE IS SET HERE AND NOWHERE ELSE, because it is a property of
    # the FILE: one successful application converts the database permanently and
    # every later connection inherits it. Doing it on the insert path instead
    # would issue a pragma per row to change nothing. It is applied BEFORE the
    # CREATE statements so the schema work itself runs under the mode the
    # database will keep.
    _apply_journal_mode(conn, db_path)

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
    llm_classifier_evaluation_time REAL,
    total_time REAL,
    llm_classifier_prompt TEXT,
    llm_classifier_input_tokens INTEGER,
    llm_classifier_output_tokens INTEGER,
    matching_model TEXT,
    cross_encoder_model TEXT,
    pricing_version TEXT,
    estimated_cost_usd REAL,
    qdrant_collection TEXT,
    error TEXT,
    patient_data_hash TEXT,
    expansion_prompt TEXT,
    llm_classifier_retries INTEGER,
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
            console.out(f"Schema migration: added inferences.{_column}")


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
    assessment TEXT,
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
            console.out(f"Schema migration: added trial_matches.{_column}")


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
    console.out(f"Database initialized at: {db_path}")

    _INITIALIZED_DATABASES.add(os.path.abspath(db_path))
    return os.path.abspath(db_path)


def _ensure_database(db_path):
    """Initialize db_path unless this process already did.

    Called by log_inference before its first write. Kept separate from
    initialize_database so an explicit caller always gets the real work done
    (a caller who deleted the file and wants it rebuilt calls that one), while
    the hot path pays the cost once.
    """
    with _WRITE_LOCK:
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
# tests/test_package_invariants.py re-derives with ast.unparse against git HEAD.
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

    THREAD SAFETY (pass 20c-3b). Everything that touches the database runs
    under ``_WRITE_LOCK``. That lock used to be a monkeypatch inside
    "25- Batch Runner.py", so the batch runner was serialized and
    "17- FastAPI Server.py" -- which calls this from the event loop's thread
    pool, once per in-flight request -- was not. See the block above
    ``initialize_database`` for what two unserialized writers on one SQLite file
    actually cost, which is a lost row reported as a success.

    The path resolution and ``get_model_cost()`` deliberately stay OUTSIDE the
    lock: neither touches the database, and holding a write lock while doing
    configuration lookups would serialize work that has no reason to be
    serialized.
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
    # carrying non-zero llm_classifier tokens would be the one case where they are not,
    # and File 16's Query 10 and File 21's cost tab both call that out.
    #
    # Reasoning tokens are NOT added to the output figure here. They are
    # already inside llm_classifier_output_tokens (see the schema note on
    # llm_classifier_reasoning_tokens); adding them would bill every one of them twice.
    total_cost = get_model_cost(
        matching_model_used or MATCHING_MODEL,
        result.get("llm_classifier_input_tokens", 0),
        result.get("llm_classifier_output_tokens", 0)
    )

    # EVERYTHING BELOW THIS LINE TOUCHES THE DATABASE, so it is serialized.
    # The body is a separate function rather than an indented `with` block for
    # one reason: the INSERT statements inside it are triple-quoted strings
    # whose indentation is part of nothing, but re-indenting 250 lines to add a
    # `with` would bury the actual change of this pass in a whitespace diff
    # nobody can review. The guarantee is identical.
    #
    # STILL EXACTLY THREE `with _WRITE_LOCK:` SITES IN THIS MODULE, and the
    # retry loop is INSIDE this one rather than around it. Two reasons, both
    # load-bearing: a retry that released and re-took the lock would let a
    # second thread interleave between attempts, which is the interleaving the
    # lock exists to forbid; and section 5e of tests/test_package_invariants.py
    # asserts `locks_stripped == 3`, so a fourth site would fail a check that is
    # measuring the lock rather than this pass.
    with _WRITE_LOCK:
        outcome = _write_inference_row_with_retry(
            result, patient_data, db_path, matching_model_used, total_cost)

    # AFTER the finally inside _write_inference_row, not inside it. A return
    # inside a finally block SWALLOWS any exception propagating out of the try
    # -- and one exception is meant to propagate from this function:
    # UnknownModelPricingError is raised above, so it never reaches here, but a
    # KeyboardInterrupt or a MemoryError raised inside the write would be
    # discarded by a `return` in the finally and the caller would be told the
    # write succeeded. It escapes the `with` above instead, releasing the lock
    # on the way, and this line is never reached.
    #
    # THE RETURN IS AN InferenceWriteResult, which IS db_path -- see that class
    # for why a str subclass rather than a tuple. `== db_path` and every other
    # string operation are unchanged; `.ok` is the new fact.
    return InferenceWriteResult(
        db_path,
        ok=outcome["ok"],
        error=outcome["error"],
        attempts=outcome["attempts"],
        inference_id=outcome["inference_id"],
    )


def _write_inference_row_with_retry(result: Dict, patient_data: Dict, db_path,
                                    matching_model_used, total_cost):
    """Attempt the write up to ``SQLITE_WRITE_MAX_ATTEMPTS`` times.

    CALLERS HOLD ``_WRITE_LOCK``; see log_inference for why the loop is inside
    it rather than around it.

    Returns a dict: ok, error, attempts, inference_id. RAISES NOTHING that
    ``_write_inference_row`` did not already raise, which is nothing except the
    two that must escape (KeyboardInterrupt, MemoryError) -- so the contract
    "a database fault does not kill the pipeline" is unchanged.

    Only the transient class is retried. ``_is_retryable`` is where that is
    decided and the block above it is why the migration race is excluded.
    """
    # max(1, ...) so a misconfigured 0 -- or a negative -- still makes ONE
    # attempt rather than skipping the loop entirely, which would leave
    # `outcome` None and turn a config typo into an AttributeError inside the
    # writer. A logging config defect must not become a pipeline crash; that is
    # the same reasoning as the broad handler below it.
    max_attempts = max(1, int(SQLITE_WRITE_MAX_ATTEMPTS))

    attempts = 0
    outcome = None

    while attempts < max_attempts:
        attempts += 1
        outcome = _write_inference_row(result, patient_data, db_path,
                                       matching_model_used, total_cost)
        outcome["attempts"] = attempts

        if outcome["ok"]:
            if attempts > 1:
                # Recovered. Recorded at INFO rather than silently, because a
                # run that needed 400 retries to lose nothing is a run whose
                # next increment of load loses rows.
                log.info("inference write succeeded after retrying",
                         event="inference_write_retried",
                         patient_id=str(result.get("patient_id", "")),
                         attempts=attempts, db_path=str(db_path))
            return outcome

        exc = outcome["exception"]
        if not _is_retryable(exc) or attempts >= max_attempts:
            break

        INFERENCE_WRITE_RETRIES[type(exc).__name__] += 1
        delay = SQLITE_WRITE_RETRY_BASE_DELAY * (2 ** (attempts - 1))
        console.out(f"  ↻ Retrying inference write in {delay:.2f}s "
                    f"(attempt {attempts + 1}/{max_attempts}): "
                    f"{type(exc).__name__}: {exc}")
        log.warning("inference write contended, retrying",
                    event="inference_write_retry",
                    patient_id=str(result.get("patient_id", "")),
                    attempts=attempts, max_retries=max_attempts,
                    delay_s=round(delay, 3),
                    error_type=type(exc).__name__, error_message=str(exc),
                    db_path=str(db_path))
        time.sleep(delay)

    # Given up. The pipeline result is NOT destroyed -- that is still the
    # contract -- but the loss is now recorded in three places a reader can
    # reach: this counter, this log record, and the returned object's `.ok`.
    exc = outcome["exception"]
    retryable = "retryable" if _is_retryable(exc) else "terminal"
    INFERENCE_WRITE_FAILURES[f"{type(exc).__name__}:{retryable}"] += 1
    log.error("inference write LOST after exhausting attempts",
              event="inference_write_lost",
              patient_id=str(result.get("patient_id", "")),
              attempts=attempts, max_retries=max_attempts,
              status=retryable,
              error_type=type(exc).__name__, error_message=str(exc),
              db_path=str(db_path))
    return outcome


def _write_inference_row(result: Dict, patient_data: Dict, db_path,
                         matching_model_used, total_cost):
    """The database half of log_inference. CALLERS HOLD ``_WRITE_LOCK``.

    Split out of log_inference in pass 20c-3b so the lock could be taken with a
    `with` statement without re-indenting the whole body. Everything here is
    byte-for-byte what log_inference did; nothing was reordered.

    RETURNS AN OUTCOME DICT as of the write-durability pass -- ``ok``,
    ``error``, ``exception``, ``attempts``, ``inference_id``. It used to return
    nothing at all, which is precisely the defect: the two handlers below print
    "non-critical" and the caller was told the same thing on both paths.

    Raising is still confined to what raised before (nothing but
    KeyboardInterrupt and MemoryError, which are not Exception subclasses and
    are meant to escape), so the "a logging fault does not kill the pipeline"
    contract is unchanged. The single caller,
    ``_write_inference_row_with_retry``, decides what to do with a failure.

    ONE CALL IS ONE TRANSACTION, which is what makes a retry safe. sqlite3's
    default isolation opens an implicit transaction at the first INSERT and
    ``conn.rollback()`` in both handlers below discards the inference row AND
    its trial_matches children together, so a retried attempt cannot duplicate a
    partially-written row. The only statement after ``conn.commit()`` is a
    console line; if THAT raised, the generic handler records a terminal (not
    retryable) failure, so a committed row is never written twice.
    """
    conn = None
    inference_id = None
    try:
        # Item 20b: the schema is no longer created when this file is loaded,
        # so it is ensured here, once per resolved path, before the first
        # write. Inside the try on purpose: a table that cannot be created is
        # a database failure, and this function's contract is that database
        # failures are reported and do not kill the pipeline. That is the
        # opposite of get_model_cost() above, which is outside the try because
        # an unpriced model is a configuration defect, not a database one.
        _ensure_database(db_path)

        # Through _open_connection: the busy timeout is per connection and this
        # is the one that meets the other process's writes.
        conn = _open_connection(db_path)
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
                rule_filter_time, llm_classifier_evaluation_time, total_time,
                llm_classifier_prompt, llm_classifier_input_tokens, llm_classifier_output_tokens,
                matching_model, cross_encoder_model,
                pricing_version, estimated_cost_usd, qdrant_collection, error,
                patient_data_hash, expansion_prompt,
                llm_classifier_retries, ablation_flags, hallucinated_trials,
                retrieval_channels, retrieval_channels_expected,
                retrieval_channels_ok, retrieval_degraded,
                retrieval_trials_lost, query_expansion_path,
                mesh_filter_applied, mesh_filter_skip_reason,
                age_reference_date, birth_date_precision,
                ecog_value, ecog_selection, ecog_observations_found,
                llm_classifier_truncation_splits, llm_classifier_output_tokens_estimated,
                not_evaluable_truncated, llm_classifier_calls,
                llm_classifier_reasoning_tokens,
                llm_classifier_prompt_version, llm_classifier_prompt_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            timings.get("llm_classifier_evaluation", 0),
            total_time,
            result.get("llm_classifier_prompt", ""),
            result.get("llm_classifier_input_tokens", 0),
            result.get("llm_classifier_output_tokens", 0),
            # Resolved above, outside the tuple, because the same value is what
            # get_model_cost() was called with. Reading it twice could price a
            # row against one model and label it with another.
            matching_model_used,
            # WAS A LITERAL "ncbi/MedCPT-Cross-Encoder" (pass 20f-2). It is the
            # same fact as the checkpoint oncotriage/agent/deps.py loads, and a
            # row that names one model while Stage 3 ran another is a row that
            # cannot be reasoned about later. Note the asymmetry with
            # matching_model_used directly above, which is read off the Stage 5
            # RESPONSE rather than from config: the API can answer with a dated
            # snapshot of the model it was asked for, so there the request and
            # the answer are two different facts. The cross-encoder runs in this
            # process, so what was asked for IS what ran.
            CROSS_ENCODER_MODEL,
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
            result.get("llm_classifier_retries", 0),                  # llm_classifier_retries
            json.dumps(result.get("ablation_flags") or {}),  # ablation_flags
            # No default, and 0 is now a real value rather than an unreached
            # one: Stage 5's detector writes the key on its success return, so
            # NULL here means the check did not complete. See the migration
            # note above.
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
            result.get("llm_classifier_truncation_splits", 0),
            result.get("llm_classifier_output_tokens_estimated"),
            result.get("not_evaluable_truncated", 0),
            result.get("llm_classifier_calls", 0),
            # No default. A response that carried no reasoning breakdown, and a
            # response that spent zero reasoning tokens, are different facts;
            # .get() with no default stores NULL for the first and 0 for the
            # second. Defaulting to 0 here would make every GPT-4o-era row and
            # every stubbed run look like a reasoning run that did no thinking.
            result.get("llm_classifier_reasoning_tokens"),
            # Which Stage 5 system prompt produced this row. Neither is
            # defaulted: a result dict that did not come from a pipeline
            # terminal node reports NULL for both, which is honest -- nothing
            # is known about which template it used. Note that the two NULLs
            # are read differently once a terminal node HAS written them; see
            # the migration comment above.
            result.get("llm_classifier_prompt_version"),
            result.get("llm_classifier_prompt_sha256"),
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
                    match_score, eligible, assessment, criterion_details,
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
                match.get("assessment", ""),
                criterion_json,
                match.get("score_confirmed"),
                match.get("score_denominator"),
                match.get("criteria_not_applicable"),
                # 0 when Stage 5's out-of-set detector checked this row, NULL
                # when it never ran. 1 is unreachable: see the migration note.
                match.get("hallucinated"),
            ))
        
        conn.commit()
        console.out(f"✓ Logged inference for patient {result['patient_id']} (ID: {inference_id})")
        outcome = {"ok": True, "error": None, "exception": None,
                   "attempts": 1, "inference_id": inference_id}

    except sqlite3.Error as e:
        if conn:
            conn.rollback()
        console.out(f"⚠ Database logging failed (non-critical): {e}")
        # DO NOT re-raise - logging failure should not break pipeline
        #
        # BUT DO REPORT IT. Before the write-durability pass this handler ended
        # here and log_inference returned db_path exactly as on success, so
        # "the row is stored" and "the row is gone" were the same answer to the
        # caller. The outcome below is what makes them different; the retry
        # decision on top of it is _write_inference_row_with_retry's.
        outcome = {"ok": False, "error": f"{type(e).__name__}: {e}",
                   "exception": e, "attempts": 1, "inference_id": None}

    except Exception as e:
        if conn:
            conn.rollback()
        console.out(f"⚠ Logging error (non-critical): {e}")
        # DO NOT re-raise - logging failure should not break pipeline
        outcome = {"ok": False, "error": f"{type(e).__name__}: {e}",
                   "exception": e, "attempts": 1, "inference_id": None}

    finally:
        if conn:
            conn.close()

    # RETURNED HERE, AFTER the finally and never inside it. A `return` inside a
    # finally block swallows any exception propagating out of the try -- and two
    # are meant to propagate (KeyboardInterrupt, MemoryError, neither an
    # Exception subclass, so neither is caught above). Returning here leaves
    # them escaping exactly as they did before this pass.
    return outcome


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Feb 12 13:26:56 2026
@author: ramyalsaffar
"""
