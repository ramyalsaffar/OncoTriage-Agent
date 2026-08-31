# Degradation Counter Registry and Run-End Report
##################################################

"""The one place a run says what degraded, so silence is a statement.

THE GAP THIS CLOSES. Sixteen module-level degradation counters existed across
the package and NOTHING READ MOST OF THEM AT THE END OF A RUN. Each was written
carefully, each was argued at its declaration, and a 22,000-patient batch could
move every one of them and print a summary that said nothing about any of it.
An operator's only route to the numbers was an interactive Python session
against a process that had already exited.

That is a worse failure than a missing counter. A missing counter is a known
hole; a counter with no reader looks like coverage.

WHY MODULE-LEVEL COUNTERS AND NOT COLUMNS. Settled before this module existed
and not reopened here: a per-PATIENT observation becomes an ``inferences``
column through INFERENCE_COLUMN_ADDITIONS, and a per-RUN property stays a
counter. The argument is written out at
``storage/database_logger.py:INFERENCE_WRITE_FAILURES`` -- a column recording
that a row could not be written is circular. This module is what the per-run
half was missing.

WHAT IS IN THE REGISTRY, AND WHAT IS DELIBERATELY NOT
-----------------------------------------------------
IN: every module-level degradation counter a MATCHING RUN can move -- the agent
stages, the parser helpers the agent calls, the registries it resolves through,
the storage writer it ends at, and the two the observability layer keeps about
itself.

OUT, each for a stated reason rather than by omission:

  * ``retrieval/indexer.py``'s EIGHT: ADMISSION_SCREEN,
    ADMISSION_DROPPED_CATEGORIES, CRITERIA_SPLIT_METHODS, CRITERIA_RENORMALIZED,
    SCRAPE_RETRIES, SCRAPE_INTERRUPTIONS, EMBEDDING_USAGE and CLEANUP_FAILURES.
    (This list said "eight" and named SEVEN until the counter-reader audit
    walked the module: CRITERIA_RENORMALIZED was missing from it, so the one
    thing a reader would use this list for -- checking that a counter is
    accounted for -- would have reported a registered, read counter as
    unaccounted.) Index-time, not run-time, and importing the indexer here
    would put a scrape module in every batch run's import graph. ALL EIGHT now
    have a reader inside that module -- at the end of the scrape, of the embed
    or of the build, whichever phase owns them, which is why this says "its own
    blocks" and not "its own block". CLEANUP_FAILURES was the one this module
    recorded as a reported finding rather than a fix, and the counter-reader
    audit closed it AT THE INDEXER, which is what this exclusion asked for.
  * ``ablation/study.py``'s FOUR: ``CHECKPOINT_WRITE_FAILURES``,
    ``CHECKPOINT_FAULTS``, ``STOP_SWITCH_FAULTS`` and ``RUN_RECORD_FAILURES``.
    (This list named TWO until the operator-control pass gave that file a stop
    switch and a per-configuration run record; leaving it at two would have
    made the one thing a reader uses it for -- checking that a counter is
    accounted for -- report two live, read counters as unaccounted.) All four
    are read at the end of the study's own ``main()``, through
    ``print_study_close``, which is that entry point's equivalent of this
    module. Importing ``ablation.study`` here would drag the whole study --
    graph, fixtures, thread pool -- into ``25- Batch Runner.py``.

    THREE OF THE FOUR ARE SEPARATE OBJECTS SHARING A NAME WITH SOMETHING
    REGISTERED HERE: ``CHECKPOINT_FAULTS`` and ``STOP_SWITCH_FAULTS`` with
    ``batch/runner.py``'s, ``RUN_RECORD_FAILURES`` with
    ``storage/database_logger.py``'s. Each pair describes DIFFERENT FILES, so
    one number covering both would report a batch fault and a study fault as
    one finding -- and the name being taken is a second reason none of them
    could join even if the import graph allowed it, because ``register()``
    raises on a duplicate. ``tests/test_degradation_counter_readers.py``'s
    ``_DUAL_OWNED`` is what stops the shared name being read as coverage: the
    scan's ``_name in _registered`` branch credited the registered copy to the
    study's for both of the two added here, and this section passed with two
    brand-new write-only counters in the package until that table caught up.
  * ``mcp/server.py:TOOL_FAILURES``. Already has ``tool_failure_summary()``,
    and an MCP server is a long-lived process rather than a run: there is no
    end for a run-end report to attach to.
  * ``fhir/parser.py``'s four (BIRTH_DATE_PRECISION_COUNTS,
    DEMOGRAPHIC_SOURCE_COUNTS, ECOG_VALUE_SHAPE_COUNTS, ECOG_SELECTION_COUNTS).
    These are CHARACTERIZATION counters, not degradation ones -- every parse
    increments one of them, so "non-zero" is the normal state and printing them
    in a degradation report would bury the signal under a census. They are
    already printed by ``load_all_patients()``.
  * the four counters in ``_CENSUS_SPEC`` further down this file
    (PROCEDURE_RENDER_COUNTS, TEMPORAL_RENDER_COUNTS and the two
    TEMPORAL_CONFLICT_*_MARKERS). Same reason as the parser's four -- they move
    on correct behaviour -- but UNLIKE the parser's four they had no reader
    anywhere, so this module grew a second registry and a second block for
    them rather than leaving the exclusion to mean "no report at all". The
    argument is written out above ``_CENSUS_SPEC``.

HOW A COUNTER JOINS. Either name it in ``_REGISTRY_SPEC`` below, or -- when the
owning module imports THIS one and so cannot be imported BY it -- call
``register()`` at that module's own scope. ``oncotriage/batch/runner.py`` is the
only user of the second route today and says why at its call.

IMPORTING THIS MODULE OPENS NOTHING. It imports the modules that own the
counters, every one of which is already in a matching run's import graph, and it
binds the Counter OBJECTS rather than snapshots -- the same reason
``oncotriage/fhir/parser.py``'s docstring gives for never rebinding one:
``NAME = Counter()`` inside a function replaces the object the readers hold and
the reader then reports zero forever.
"""

from collections import Counter
from typing import Dict, List, Optional

from oncotriage.agent import bedrock_adapter as _bedrock_adapter
from oncotriage.agent import bedrock_anthropic_adapter as _bedrock_anthropic_adapter
from oncotriage.agent import deps as _agent_deps
from oncotriage.agent import evaluation as _agent_evaluation
from oncotriage.agent import filtering as _agent_filtering
from oncotriage.agent import patient as _agent_patient
from oncotriage.agent import readiness as _agent_readiness
from oncotriage.extraction import stage as _extraction_stage
from oncotriage.observability import console, get_logger
from oncotriage import observability as _observability
from oncotriage import utils as _utils
from oncotriage.registries import cancer_code_registry as _cancer_code_registry
from oncotriage.registries import mesh as _mesh
from oncotriage.storage import database_logger as _database_logger
from oncotriage import run_fingerprint as _run_fingerprint
from oncotriage import tracking as _tracking


log = get_logger(__name__)


#------------------------------------------------------------------------------


# ===========================================================================
# READING THE REGISTRY WHILE THE RUN IS STILL WRITING TO IT
# ===========================================================================
#
# UNTIL THE HEALTH-PERSISTENCE PASS, ``snapshot()`` HAD EXACTLY ONE CALLER AND
# IT RAN AFTER BOTH THREAD POOLS HAD BEEN JOINED. It is now also called once per
# completed patient, from ``_on_done`` -- a done-CALLBACK, so on a WORKER
# thread, while MAX_WORKERS-1 other workers are still incrementing the very
# counters it is copying.
#
# WHAT THAT ACTUALLY RISKS, stated narrowly rather than gestured at. CPython's
# dict iterator compares the dict's size before and after each step and raises
# ``RuntimeError: dictionary changed size during iteration`` when it changed. A
# ``Counter[k] += 1`` on a key that is ALREADY THERE rebinds a value and does
# not change the size, so the common case is safe. A key that is NEW does -- a
# lab unit nobody had a conversion for yet, an exception type not seen before,
# a Qdrant function retried for the first time. Rare per patient, and a
# 22,000-patient run takes that gamble 22,000 times.
#
# WHY NOT A LOCK. The fix "take a lock" belongs on the WRITE side, and the write
# side is ~22 counters incremented from the hot path of every stage in the
# package. Putting a lock in front of ``AGE_PARSE_FAILURES[key] += 1`` to make a
# once-per-patient read tidy is the wrong trade by orders of magnitude, and it
# would be a change to every module that owns a counter rather than to this one.
#
# SO: ONE C-LEVEL COPY, THEN FILTER THE COPY. ``dict.copy(counter)`` is a single
# call into CPython's ``PyDict_Copy``; no Python bytecode runs inside it, so no
# other thread can be scheduled part-way through and the copy cannot observe a
# resize. Everything after it reads a dict nothing else has a reference to.
#
# THE ORIGINAL VERSION OF THIS FUNCTION WAS A DICT COMPREHENSION OVER
# ``counter.items()`` AND THAT IS PRECISELY THE THING THAT IS NOT SAFE: a
# comprehension executes Python bytecode per item, so the interpreter can switch
# threads between two of them, and a key inserted in that window raises. It was
# not reasoned out -- ``tests/test_storage_run_metrics_flush.py`` section 7 was
# written first, with a thread inserting a new key in a tight loop, and the
# first implementation ABANDONED a counter on the first run. The retry below
# alone was not enough for it either.
#
# THE BOUNDED RETRY IS KEPT AS A SECOND LINE OF DEFENCE, not as the mechanism.
# The atomicity above is a CPython implementation property rather than a
# language guarantee, so a runtime that does not share it degrades to the retry;
# and when even that fails, the counter is OMITTED from that snapshot and the
# omission is counted under ``:abandoned``, which is itself on this report. A
# silent partial snapshot -- one counter quietly missing from a health record
# that looks complete -- is the exact defect this module exists to remove, one
# level up.
#
# THE RUN-END SNAPSHOT CANNOT HIT ANY OF THIS. It is taken on the main thread
# after both pools are joined, so there is no writer to race.

SNAPSHOT_CONTENTION = Counter()
"""Registry reads that met a concurrent write, keyed ``{counter name}:{outcome}``.

``:retry`` is benign and is recorded anyway, because a rising retry count is the
only evidence that the retry is load-bearing rather than dead code. ``:abandoned``
is a counter genuinely missing from one flush's rows.

THE KEYS ARE COUNTER NAMES, which are code identifiers -- the same property that
makes ``totals()`` safe to persist. Nothing here carries a counter's own KEYS.
"""

_SNAPSHOT_COPY_ATTEMPTS = 4
"""How many times a contended counter copy is retaken before it is abandoned.

Four rather than two so that the ``:abandoned`` key means something has gone
genuinely wrong rather than "two threads were unlucky once". On CPython the
retry is not expected to be reached at all -- see the block above -- so a
non-zero ``:retry`` count is itself worth reading as a fact about the runtime.
"""


def _copy_counter(name, counter):
    """A plain ``{key: count}`` copy of ``counter``, or ``None`` if contended out.

    See the block above. Returns ``None`` rather than a partial dict: a copy that
    lost half its keys to a concurrent write would be reported as a set of
    totals, and a total that is wrong is worse than a total that is absent and
    says so.
    """
    for attempt in range(_SNAPSHOT_COPY_ATTEMPTS):
        try:
            # ONE C-level copy of the live counter -- see the block above for
            # why this and not a comprehension over `counter.items()`.
            raw = dict.copy(counter)
        except RuntimeError:
            # "dictionary changed size during iteration" -- a new key appeared
            # under us. Anything else with this type re-raises below.
            SNAPSHOT_CONTENTION[f"{name}:retry"] += 1
            if attempt == _SNAPSHOT_COPY_ATTEMPTS - 1:
                SNAPSHOT_CONTENTION[f"{name}:abandoned"] += 1
                return None
            continue
        # The zero filter runs on `raw`, which nothing else holds a reference
        # to, so it cannot race however long it takes.
        return {k: v for k, v in raw.items() if v}
    return None


#------------------------------------------------------------------------------


# ===========================================================================
# THE REGISTRY
# ===========================================================================
#
# (public name, the Counter object, one line saying what a non-zero means).
#
# THE THIRD MEMBER IS NOT DECORATION. A report that prints
# `AGE_UNIT_ASSUMPTIONS: 412` and stops has moved the archaeology from "find the
# counter" to "find the counter's docstring", which is the same session in a
# different file. The line is what makes the block actionable by itself.
#
# ORDER IS DECLARATION ORDER AND IT IS THE PIPELINE'S. Configuration and data
# first, then the stages in the order they run, then storage, then the output
# layer's record of itself -- so a reader scanning the block top to bottom is
# walking the run.
_REGISTRY_SPEC = (
    ("REGISTRY_DEGRADATIONS", _cancer_code_registry.REGISTRY_DEGRADATIONS,
     "a cancer-code layer was ABSENT and the run was allowed to continue "
     "without it (ONCOTRIAGE_ALLOW_DEGRADED_REGISTRIES)"),
    ("MESH_FILTER_DEGRADATIONS", _mesh.MESH_FILTER_DEGRADATIONS,
     "a MeSH lookup layer was ABSENT; the cancer site filter ran without it "
     "or did not run at all"),
    ("PARTIAL_DATE_DEGRADATIONS", _utils.PARTIAL_DATE_DEGRADATIONS,
     "a partial birthDate had an out-of-range component and was anchored"),
    ("M_CATEGORY_UNREADABLE", _extraction_stage.M_CATEGORY_UNREADABLE,
     "an AJCC clinical M observation (LOINC 21907-1) carried text the stage "
     "extractor could not read; that patient's M tier contributed nothing"),
    ("LAB_UNIT_DEGRADATIONS", _agent_patient.LAB_UNIT_DEGRADATIONS,
     "a lab value was not unit-converted; 'unconverted:' keys are the ones "
     "that want a new row in _LAB_UNIT_CONVERSIONS"),
    ("CROSS_ENCODER_LIMIT_DEGRADATIONS",
     _agent_deps.CROSS_ENCODER_LIMIT_DEGRADATIONS,
     "the loaded cross-encoder did not declare a sequence limit this code "
     "could compare, so config.CROSS_ENCODER_MAX_LENGTH went UNVERIFIED "
     "against that half; a genuine mismatch raises rather than counting, and "
     "the shipped MedCPT tokenizer reports undeclared_placeholder on every "
     "load"),
    ("QDRANT_RETRIES", _utils.QDRANT_RETRIES,
     "a Qdrant call failed and was retried; the run survived it, keyed by "
     "the function that was retried"),
    ("INDEX_PROBE_FAILURES", _agent_readiness.INDEX_PROBE_FAILURES,
     "the index readiness probe could not run; Stage 2 CONTINUED, so these "
     "patients were matched against an index nobody vouched for"),
    ("AGE_PARSE_FAILURES", _agent_filtering.AGE_PARSE_FAILURES,
     "a trial's min_age/max_age would not parse; the trial was KEPT and its "
     "age window was never tested"),
    ("AGE_UNIT_ASSUMPTIONS", _agent_filtering.AGE_UNIT_ASSUMPTIONS,
     "a trial's age bound was used with an ASSUMED unit rather than a stated "
     "one; the bound did filter, on a guess"),
    ("SEX_UNKNOWN_KEPT", _agent_filtering.SEX_UNKNOWN_KEPT,
     "sex-specific trials were KEPT because the patient's sex was not "
     "comparable; that requirement was never tested"),
    # STAGE 5 STARTS BEFORE THE CALL. Both decoders run in the RENDER path, on
    # the criteria text this process is about to put in front of the judge, so
    # they sit above the counters that describe what came back.
    ("MARKDOWN_ESCAPE_DECODE_UNRESOLVED",
     _agent_evaluation.MARKDOWN_ESCAPE_DECODE_UNRESOLVED,
     "a registry markdown escape was left as scraped rather than decoded, so "
     "the judge read the backslash; keyed reason:text, and the reasons are "
     "MARKDOWN_REFUSED_ESCAPED_BACKSLASH / MARKDOWN_REFUSED_REFERENCE_SYNTAX"),
    ("ESCAPED_ENTITY_DECODE_UNRESOLVED",
     _agent_evaluation.ESCAPED_ENTITY_DECODE_UNRESOLVED,
     "an escaped character reference was left as scraped rather than decoded, "
     "so the judge read the entity; keyed reason:text, and the reasons are "
     "ENTITY_REFUSED_PASS_CAP / ENTITY_REFUSED_REPLACEMENT_CHAR"),
    ("MALFORMED_EVALUATION_ENTRIES", _agent_evaluation.MALFORMED_EVALUATION_ENTRIES,
     "Stage 5 returned a top-level entry that was not an object; it was "
     "dropped and reached no verdict"),
    ("ASSESSMENT_COMPOSITION_ANOMALIES",
     _agent_evaluation.ASSESSMENT_COMPOSITION_ANOMALIES,
     "the stored assessment was composed from a verdict the normalizer should "
     "not have been able to hand it -- a rejection with no surviving "
     "disqualifier, or a trial-level label outside the three-member "
     "vocabulary; the assessment for that trial is the weakest of its cases"),
    ("REFUSALS_OBSERVED", _agent_evaluation.REFUSALS_OBSERVED,
     "the model DECLINED to answer; that patient ended at the error handler"),
    ("PER_TRIAL_CALL_FAILURES", _agent_evaluation.PER_TRIAL_CALL_FAILURES,
     "a Stage 5 PER-TRIAL request raised and was isolated to its own trial, "
     "which is recorded as not evaluable while the rest of the patient "
     "completed; keyed by exception type. A patient whose calls ALL failed is "
     "NOT here -- it returns the API-error result and is covered by "
     "MAX_LLM_CLASSIFIER_RETRIES instead. LIVE ON AN ORDINARY CAMPAIGN, "
     "because per-trial is the SHIPPED arm; it stays at zero only in the "
     "retained GROUPED arm (MATCHING_PER_TRIAL_CALLS_ENABLED False), where "
     "nothing reaches the branch that increments it"),
    ("PER_TRIAL_WARMUP_DEGRADATIONS",
     _agent_evaluation.PER_TRIAL_WARMUP_DEGRADATIONS,
     "the Stage 5 PER-TRIAL cache warmup did not do its job. A "
     "`minimal_output_rejected` or `prompt_cache_key_rejected` key means the "
     "provider refused the warmup's request SHAPE and the patient completed "
     "on the retired one-then-rest schedule -- the remedy is "
     "MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS or "
     "MATCHING_PER_TRIAL_PROMPT_CACHE_KEY_ENABLED. A `failed:{Type}` key means "
     "the cache could not be established at all, NO trial call was issued and "
     "the patient was failed deliberately so that MAX_LLM_CLASSIFIER_RETRIES "
     "and the batch checkpoint see it. LIVE ON AN ORDINARY CAMPAIGN, because "
     "per-trial is the SHIPPED arm; it stays at zero only in the retained "
     "GROUPED arm (MATCHING_PER_TRIAL_CALLS_ENABLED False)"),
    ("STAGE5_SHUTDOWN_SKIPS", _agent_evaluation.STAGE5_SHUTDOWN_SKIPS,
     "Stage 5 requests that were NOT issued because an operator asked the run "
     "to stop -- SIGTERM or Ctrl-C, never the STOP sentinel, which promises "
     "in-flight patients complete. A `warmup:` key means the gate fired before "
     "the cache writer and that patient sent NOTHING; a `wave:` key means "
     "requests were already in the air and only the queued ones were declined. "
     "IT NAMES MONEY NOT SPENT, and it is in this report rather than the "
     "census because it is the CAUSE of the error rows a stopped run leaves: "
     "without it, patients that were never judged read as patients that "
     "failed. Every such patient is failed deliberately and is NOT "
     "checkpointed, so a resume re-runs it. The total is a FLOOR -- it is "
     "incremented from worker threads without a lock; see the counter"),
    ("BEDROCK_ADAPTER_DEGRADATIONS",
     _bedrock_adapter.BEDROCK_ADAPTER_DEGRADATIONS,
     "Stage 5 ran on Amazon Bedrock and the request or the response was not "
     "what the adapter was built against -- a parameter that could not be "
     "expressed (seed), a response shape that had to be interpreted, or an "
     "error class the taxonomy does not name. Every key is a VERIFY-AT-GO-LIVE "
     "item in oncotriage/agent/bedrock_adapter.py that did not hold. STAYS AT "
     "ZERO WHILE MATCHING_PROVIDER IS 'openai', because nothing in that "
     "configuration reaches the adapter at all"),
    ("BEDROCK_ANTHROPIC_DEGRADATIONS",
     _bedrock_anthropic_adapter.BEDROCK_ANTHROPIC_DEGRADATIONS,
     "Stage 5 ran on Amazon Bedrock's CONVERSE API (Claude) and something was "
     "not what the adapter was built against. A SECOND COUNTER BESIDE "
     "BEDROCK_ADAPTER_DEGRADATIONS rather than a shared one, because the two "
     "Bedrock branches degrade in ways that are not comparable and a shared "
     "total could not say which had degraded: only this one can fail to "
     "express a reasoning effort, and only this one can fail to obtain a model "
     "echo -- which is the key to read first, because while it is non-zero "
     "inferences.matching_model records the model that was REQUESTED rather "
     "than one that answered, and MatchingModelMismatchError cannot fire. "
     "Every key is a VERIFY-AT-GO-LIVE item in "
     "oncotriage/agent/bedrock_anthropic_adapter.py that did not hold. STAYS "
     "AT ZERO WHILE MATCHING_PROVIDER IS 'openai', because nothing in that "
     "configuration reaches the adapter at all"),
    ("INFERENCE_WRITE_RETRIES", _database_logger.INFERENCE_WRITE_RETRIES,
     "a database write was retried and survived; contention, not loss"),
    ("WRITE_RETRY_OUTCOMES", _database_logger.WRITE_RETRY_OUTCOMES,
     "a write that went through run_with_write_retry -- the generic helper, "
     "not log_inference's own loop -- met contention. Keyed "
     "{outcome}:{ExceptionType}, and the outcome word is what makes it "
     "readable: `recovered:` is contention SURVIVED and `exhausted:` is the "
     "attempt budget running out while the error was still transient, which "
     "the CALLER's own counter says what it cost. A run with `retried:` and "
     "`recovered:` and no `exhausted:` lost nothing and is one increment of "
     "load away from losing something"),
    ("INFERENCE_WRITE_FAILURES", _database_logger.INFERENCE_WRITE_FAILURES,
     "a database write was GIVEN UP ON -- the row is lost; the reconciliation "
     "block above is the authority on which"),
    ("RUN_RECORD_FAILURES", _database_logger.RUN_RECORD_FAILURES,
     "a batch run's `runs` row could not be FINALIZED, so it still reads "
     "RUNNING with a NULL finished_at -- the rows it produced are fine and "
     "the record of the run that produced them is not. There is no start-side "
     "key here because start_run_record RAISES rather than counting"),
    ("RUN_METRICS_FLUSH_FAILURES", _database_logger.RUN_METRICS_FLUSH_FAILURES,
     "a run's HEALTH RECORD could not be written to `run_metrics`, so the "
     "persisted copy of this block is stale by at least one flush and a "
     "crashed campaign's would be stale by whatever moved after the last one "
     "that landed. THE COUNTER IS READ HERE AND NOT FROM THAT TABLE, "
     "necessarily: a row recording that the flush failed could only be written "
     "by the flush that just failed"),
    ("JOURNAL_MODE_DEGRADATIONS", _database_logger.JOURNAL_MODE_DEGRADATIONS,
     "the database is not in the journal mode SQLITE_JOURNAL_MODE asked for; "
     "keyed requested->actual"),
    ("ANALYZE_FAILURES", _database_logger.ANALYZE_FAILURES,
     "the run finished and SQLite's planner statistics could not be refreshed, "
     "so `sqlite_stat1` is stale or absent and later queries against this "
     "database are planned from built-in guesses rather than measured "
     "selectivity. Every answer is still correct; some plans are worse. Keyed "
     "by exception type, and the next run's ANALYZE repairs it"),
    ("FIELD_DROPS", _observability.FIELD_DROPS,
     "a log field was dropped for not being on LOGGABLE_FIELDS; the field "
     "NAME only, never its value"),
    ("EMIT_FAILURES", _observability.EMIT_FAILURES,
     "a console or log line could not be written; THIS REPORT IS ITSELF "
     "SUSPECT when this is non-zero"),
    ("SNAPSHOT_CONTENTION", SNAPSHOT_CONTENTION,
     "a counter was being written by one thread while another was reading this "
     "registry, keyed by counter name; ':retry' means the copy was retaken and "
     "the reading is sound, ':abandoned' means it was not and THAT COUNTER IS "
     "MISSING from the snapshot that flush wrote"),
    ("FINGERPRINT_DEGRADATIONS", _run_fingerprint.FINGERPRINT_DEGRADATIONS,
     "this run's own configuration could not be established -- the backing "
     "collection or its point count came back unknown -- so every resume gate "
     "consulted afterwards REFUSED rather than skipping work it could not "
     "vouch for"),
    # LAST, and after EMIT_FAILURES on purpose: it is the only counter here
    # that says nothing about the RUN. Every entry above it describes something
    # that happened to the pipeline; this one says the run was fine and its
    # INDEX is incomplete -- the git commit, the resolved collection, a metric
    # or an artifact did not reach the tracking store. A reader scanning the
    # block top to bottom walks the run and then reaches the record of the run.
    ("TRACKING_DEGRADATIONS", _tracking.TRACKING_DEGRADATIONS,
     "the run completed but its tracking record is incomplete -- 'unknown' "
     "metadata, a dropped metric or an unattached artifact; the numbers are "
     "sound and what produced them is less traceable"),
)

_REGISTRY: Dict[str, Counter] = {name: counter for name, counter, _ in _REGISTRY_SPEC}
_MEANINGS: Dict[str, str] = {name: meaning for name, _, meaning in _REGISTRY_SPEC}


def register(name: str, counter: Counter, meaning: str) -> None:
    """Add a counter this module cannot import for itself.

    FOR MODULES THAT IMPORT THIS ONE. ``oncotriage/batch/runner.py`` owns
    ``RESULTS_FILE_FAILURES`` and calls the report below, so this module cannot
    import it back. Registration at the owner's module scope is the way round
    that, and it is a plain dict insert -- no file, no client, no process --
    so it does not breach "importing a package module does nothing".

    A DUPLICATE NAME RAISES rather than overwriting. Two counters answering to
    one name in the report is the failure this whole module exists to remove,
    one level up.
    """
    if name in _REGISTRY:
        raise ValueError(
            f"degradation: {name!r} is already registered. Two counters under "
            f"one name would report as one, which is the defect this registry "
            f"exists to prevent. Rename one of them.")
    _REGISTRY[name] = counter
    _MEANINGS[name] = meaning


def registered_names() -> List[str]:
    """Every registered counter name, in declaration order.

    A reader, so the registry is not a declaration nothing consults, and the
    thing a test asserts a newly-added counter reached.
    """
    return list(_REGISTRY)


def snapshot() -> Dict[str, Dict[str, int]]:
    """Every NON-ZERO counter as ``{name: {key: count}}``, in registry order.

    A COPY, taken at one instant. The report and the log event are both built
    from a single snapshot so the block a human reads and the record a machine
    reads describe the same moment -- the same reason ``main()`` reconciles once
    and hands the result to both ``_publish_reconciliation`` and
    ``print_summary``.

    Zero-valued KEYS inside a live counter are dropped too: ``Counter[k]`` on a
    missing key inserts nothing, but ``.clear()``-and-reuse patterns in the test
    suite can leave explicit zeros, and a report listing `foo: 0` under a
    heading that means "these fired" is a lie about which is which.
    """
    out: Dict[str, Dict[str, int]] = {}
    # list() FIRST, because register() can insert into _REGISTRY at import time
    # and this loop is now reached from worker threads. Copying each counter is
    # _copy_counter's job -- see the block at the top of this module for why a
    # bare comprehension over a live Counter is not safe here any more.
    for name, counter in list(_REGISTRY.items()):
        live = _copy_counter(name, counter)
        if live:
            out[name] = dict(sorted(live.items()))
    return out


def totals(snap: Optional[Dict[str, Dict[str, int]]] = None) -> Dict[str, int]:
    """``{counter name: sum of its values}`` for the non-zero counters.

    THE KEYS ARE COUNTER NAMES, WHICH ARE CODE IDENTIFIERS. That is what makes
    this safe to put in a structured log record while ``snapshot()`` is not:
    counter KEYS carry third-party and clinical text -- SEX_UNKNOWN_KEPT is
    keyed by the patient's recorded sex, M_CATEGORY_UNREADABLE by a capped copy
    of an observation's display -- and LOGGABLE_FIELDS exists to keep exactly
    that out of a durable, correlation-keyed record. The detail goes to the
    console, which is transient and unindexed, on the same footing as the Stage
    5 response preview and ``print_slowest_prompt``'s prompt dump.
    """
    if snap is None:
        snap = snapshot()
    return {name: sum(keys.values()) for name, keys in snap.items()}


#------------------------------------------------------------------------------


# ===========================================================================
# THE REPORT
# ===========================================================================

def report_lines(snap: Optional[Dict[str, Dict[str, int]]] = None) -> List[str]:
    """The summary block, as lines. Never empty.

    ALL-ZERO PRODUCES A STATEMENT, NOT AN ABSENCE, and that is the whole point
    of the block. A run that prints nothing about degradation is
    indistinguishable from a run whose degradation reporting was never wired up
    -- which is what every run before this pass looked like. So a clean run says
    it is clean, names how many counters were consulted, and is therefore
    evidence rather than silence.
    """
    if snap is None:
        snap = snapshot()

    lines = ["--- DEGRADATION COUNTERS ---"]
    if not snap:
        lines.append(f"  ✓ CLEAN: all {len(_REGISTRY)} degradation counters are "
                     f"zero for this process.")
        lines.append("      Every counter was read; none of them moved. This is "
                     "a measurement, not an absence of reporting.")
        lines.append("")
        return lines

    grand = sum(totals(snap).values())
    lines.append(f"  {len(snap)} of {len(_REGISTRY)} counters moved, "
                 f"{grand} event(s) in total.")
    lines.append("")
    for name, keys in snap.items():
        lines.append(f"  {name}  ({sum(keys.values())})")
        lines.append(f"      {_MEANINGS[name]}")
        for key, count in keys.items():
            lines.append(f"        {count:>8}  {key}")
    lines.append("")
    lines.append("      These are PER-PROCESS totals, not per-patient. The "
                 "per-patient record is in inferences -- degraded_run for the "
                 "one-glance answer, and the columns it summarises for the "
                 "detail.")
    lines.append("")
    return lines


def print_report(snap: Optional[Dict[str, Dict[str, int]]] = None,
                 out=None) -> None:
    """Write ``report_lines`` to the console channel.

    ``out`` is injectable for the same reason ``print_slowest_prompt``'s is:
    a test needs the text without the terminal. It defaults to the console
    channel, never to ``print`` -- there is no ``print`` in this package.
    """
    emit = out or console.out
    for line in report_lines(snap):
        emit(line)


def log_summary(snap: Optional[Dict[str, Dict[str, int]]] = None) -> Dict[str, int]:
    """Emit ONE structured event for the whole registry. Returns the totals.

    ONE EVENT AND NOT ONE PER COUNTER, because the fact a consumer acts on is
    "this run degraded, here is the shape of it" -- seventeen events would have
    to be re-joined by correlation ID to answer that, and the correlation ID on
    a run-end event is the no-correlation sentinel anyway.

    IT IS EMITTED WHEN THE REGISTRY IS CLEAN TOO, at INFO with
    ``status="clean"``. An event that only appears when something is wrong
    cannot distinguish a clean run from a run where the reporting was not
    reached, which is the same defect the console block above avoids.
    """
    if snap is None:
        snap = snapshot()
    counts = totals(snap)
    grand = sum(counts.values())

    if grand:
        log.warning("run degradation summary", event="degradation_summary",
                    status="degraded", total=grand, count=len(counts),
                    degradation_totals=counts)
    else:
        log.info("run degradation summary", event="degradation_summary",
                 status="clean", total=0, count=0, degradation_totals={})
    return counts


#------------------------------------------------------------------------------


# ===========================================================================
# THE CENSUS REGISTRY -- SEPARATE, AND THE SEPARATION IS THE POINT
# ===========================================================================
#
# FOUR COUNTERS IN THE AGENT MOVE ON CORRECT BEHAVIOUR, so they cannot join the
# registry above without making its headline sentence false. That block reads
# "N of M counters moved" and every entry in it means something went wrong; a
# run that rendered two hundred procedures and dropped eighty of them by design
# would report a degradation that did not happen.
#
# THAT EXCLUSION IS A RULING, NOT AN OVERSIGHT, and two of the four argue it at
# their own declaration -- see TEMPORAL_CONFLICT_RESOLVED_MARKERS in
# oncotriage/agent/evaluation.py ("It is an observation, not a degradation").
# What was missing is the other half: an exclusion from ONE report is not a
# licence to have NO report, and all four were write-only in production. A
# counter with no reader looks like coverage, which is the sentence this whole
# module opens with.
#
# SO: A SECOND REGISTRY, A SECOND BLOCK, THE SAME MACHINERY. `_copy_counter`
# above is reused rather than reimplemented -- it took a threaded test to get
# right (see the block at the top of this file) and a second copy of it is a
# second thing to get wrong. The two registries share no name, enforced below.
#
# WHY THESE ARE NOT IN `run_metrics`. The persisted health record's `category`
# vocabulary (RUN_METRIC_CATEGORIES) is CLOSED at two members, and
# `run_metrics`' three registered queries plus the dashboard's Run Health tab
# read `category = 'degradation'` and derive `health_record` from
# `counters_registered` / `counters_nonzero`. Putting a census row in that
# table would either need a third category -- which those readers do not know,
# and teaching them is a change to a shipped consumer rather than to this
# module -- or would inflate `counters_nonzero`, which is the field that
# separates "measured clean" from "no health record". A census is a fact about
# what a run RENDERED; the health record is a fact about what went WRONG with
# it, and merging them costs the second its meaning.
#
# WHAT IS DELIBERATELY NOT HERE. `oncotriage/fhir/parser.py`'s four
# characterization counters, which are the same KIND of thing and already have
# a reader -- `load_all_patients()` prints them at the end of its own pass.
# Registering them here would print them twice on a batch run, and the parser's
# pass has an end of its own where this module's does not.
_CENSUS_SPEC = (
    ("PROCEDURE_RENDER_COUNTS", _agent_patient.PROCEDURE_RENDER_COUNTS,
     "procedure render candidates kept vs withheld from the Stage 5 summary; "
     "a run whose 'dropped' dwarfs its 'kept' is a relevance filter to look "
     "at, and neither number is a fault"),
    ("TEMPORAL_RENDER_COUNTS", _agent_patient.TEMPORAL_RENDER_COUNTS,
     "record dates that were PRESENT and could not anchor an elapsed phrase "
     "('*_unreadable:*', '*_after_reference' -- the second means the corpus "
     "outran DATA_SNAPSHOT_DATE), plus the 'lab_stale' census key, which its "
     "declaration argues is NOT a degradation; the mixture is why the whole "
     "counter is here rather than in the block above"),
    ("TEMPORAL_CONFLICT_RESOLVED_MARKERS",
     _agent_evaluation.TEMPORAL_CONFLICT_RESOLVED_MARKERS,
     "which resolved-state markers fired across the run; a member at zero is "
     "a candidate for deletion from the vocabulary and a member dominating "
     "the rest is a candidate for review. NOT a row count -- a row "
     "contributes every marker it matched"),
    ("TEMPORAL_CONFLICT_ACTIVE_MARKERS",
     _agent_evaluation.TEMPORAL_CONFLICT_ACTIVE_MARKERS,
     "which active-requirement markers fired across the run; see the counter "
     "above, including that it is not a row count"),
)

_CENSUS: Dict[str, Counter] = {name: c for name, c, _ in _CENSUS_SPEC}
_CENSUS_MEANINGS: Dict[str, str] = {name: m for name, _, m in _CENSUS_SPEC}

def assert_registries_disjoint(registry=None, census=None) -> None:
    """Raise if any name is in BOTH registries. Called at import, below.

    A NAME IN BOTH WOULD REPORT TWICE UNDER TWO DIFFERENT HEADINGS -- once as a
    fault and once as an observation -- which is ``register()``'s duplicate-name
    failure wearing a second costume, and worse, because the two blocks
    disagree about what it MEANS.

    RuntimeError rather than ``assert``: ``python -O`` deletes asserts, and this
    is a structural claim about the module rather than a debugging aid.

    BOTH ARGUMENTS DEFAULT TO THE LIVE REGISTRIES AND ARE OVERRIDABLE, which is
    what makes this checkable at all. The import-time form is one statement
    against two dicts that are correct today, so a test can only exercise it by
    breaking the module and then failing to import it -- an abort, not a
    recorded failure. Handed a colliding pair it raises on demand, which is the
    same reason ``print_report`` takes ``out``.
    """
    registry = _REGISTRY if registry is None else registry
    census = _CENSUS if census is None else census
    colliding = sorted(set(registry) & set(census))
    if colliding:
        raise RuntimeError(
            f"degradation: {colliding} is in BOTH the degradation registry and "
            f"the census registry. One counter reported under two headings is "
            f"the defect register() exists to prevent, one registry out.")


assert_registries_disjoint()


def census_names() -> List[str]:
    """Every registered census counter name, in declaration order."""
    return list(_CENSUS)


def census_snapshot() -> Dict[str, Dict[str, int]]:
    """Every NON-ZERO census counter as ``{name: {key: count}}``.

    ``snapshot()``'s twin, sharing ``_copy_counter`` -- so it is safe to call
    from a worker thread for the same reason and with the same ``:retry`` /
    ``:abandoned`` accounting, which lands in ``SNAPSHOT_CONTENTION`` and is
    therefore reported by the DEGRADATION block. That is the right side: a
    census that could not be copied is a fault, even though what it counts is
    not.
    """
    out: Dict[str, Dict[str, int]] = {}
    for name, counter in list(_CENSUS.items()):
        live = _copy_counter(name, counter)
        if live:
            out[name] = dict(sorted(live.items()))
    return out


def census_totals(snap: Optional[Dict[str, Dict[str, int]]] = None) -> Dict[str, int]:
    """``{counter name: sum of its values}`` for the non-zero census counters.

    Counter NAMES only, on ``totals()``'s reasoning -- but note that a census
    total is much less useful than a degradation total, because the interesting
    thing about these counters is the SPLIT between their keys. 'kept' against
    'dropped' is the whole content of PROCEDURE_RENDER_COUNTS and their sum is
    just how many procedures a run saw. Provided for symmetry and for a caller
    that wants one number; the block below prints the keys.
    """
    if snap is None:
        snap = census_snapshot()
    return {name: sum(keys.values()) for name, keys in snap.items()}


def census_report_lines(
        snap: Optional[Dict[str, Dict[str, int]]] = None) -> List[str]:
    """The census block, as lines. Never empty.

    ALL-ZERO PRODUCES A STATEMENT, on ``report_lines``' argument: a run that
    prints nothing here is indistinguishable from a run whose census reporting
    was never wired up, which is what every run before this pass looked like.

    IT DOES NOT SAY "CLEAN" AND IT DOES NOT SAY "DEGRADED". Zero here means
    nothing was rendered or nothing was flagged -- on a run that matched
    patients that is itself worth looking at, and on a run that matched none it
    is expected. The wording says what was counted and leaves the verdict to
    the reader, because there is no verdict a census can carry.
    """
    if snap is None:
        snap = census_snapshot()

    lines = ["--- RENDER AND MARKER CENSUS (observations, NOT degradations) ---"]
    if not snap:
        lines.append(f"  All {len(_CENSUS)} census counters are zero for this "
                     f"process: nothing was rendered and nothing was flagged.")
        lines.append("      Every counter was read. On a run that matched "
                     "patients this is a finding, not a clean bill.")
        lines.append("")
        return lines

    sums = census_totals(snap)
    lines.append(f"  {len(snap)} of {len(_CENSUS)} census counters have "
                 f"something to report, {sum(sums.values())} observation(s) "
                 f"in total.")
    lines.append("")
    for name, keys in snap.items():
        lines.append(f"  {name}  ({sums[name]})")
        lines.append(f"      {_CENSUS_MEANINGS[name]}")
        for key, count in keys.items():
            lines.append(f"        {count:>8}  {key}")
    lines.append("")
    lines.append("      PER-PROCESS totals. None of these is a fault; the "
                 "faults are in the DEGRADATION COUNTERS block.")
    lines.append("")
    return lines


def print_census_report(snap: Optional[Dict[str, Dict[str, int]]] = None,
                        out=None) -> None:
    """Write ``census_report_lines`` to the console channel.

    CONSOLE ONLY, and there is no ``log_summary`` twin. A census is what the
    console channel is for -- a human reading a run's tail -- and its keys are
    a mixture this module would rather not put in a durable, correlation-keyed
    record: PROCEDURE_RENDER_COUNTS and the two marker counters are keyed by
    our own vocabulary, but TEMPORAL_RENDER_COUNTS' keys carry a
    ``parse_partial_date`` precision string from third-party data. One rule for
    the whole block beats a per-counter exemption nobody will maintain.
    """
    emit = out or console.out
    for line in census_report_lines(snap):
        emit(line)


def clear_census() -> None:
    """Zero every registered CENSUS counter. For a harness, never for a run.

    SEPARATE FROM ``clear_all()`` RATHER THAN FOLDED INTO IT. Every existing
    caller of ``clear_all()`` saves and restores ``_REGISTRY`` around it and
    nothing else, so widening it would silently zero four counters those
    harnesses never put back -- a harness quietly destroying state it did not
    declare, which is the shape ``run_serial_tests.py`` locks against one level
    up.
    """
    for counter in _CENSUS.values():
        counter.clear()


#------------------------------------------------------------------------------


def clear_all() -> None:
    """Zero every registered counter. For a harness, never for a run.

    A run must NOT clear these: they are cumulative over the process, and a
    reset between passes would make the main pass's degradations vanish from the
    summary the resample pass prints. The batch runner does not call it.
    """
    for counter in _REGISTRY.values():
        counter.clear()


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Aug 10 2026

@author: ramyalsaffar
"""
