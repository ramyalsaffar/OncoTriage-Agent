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

  * ``retrieval/indexer.py``'s eight (ADMISSION_SCREEN, CRITERIA_SPLIT_METHODS,
    SCRAPE_RETRIES, SCRAPE_INTERRUPTIONS, EMBEDDING_USAGE, CLEANUP_FAILURES,
    ADMISSION_DROPPED_CATEGORIES). Index-time, not run-time; seven of the eight
    are already printed by the indexer's own end-of-build block, and importing
    the indexer here would put a scrape module in every batch run's import
    graph. CLEANUP_FAILURES is the exception and it is a REPORTED FINDING of
    this pass, not something fixed by adding it here -- see the final report.
  * ``ablation/study.py:CHECKPOINT_WRITE_FAILURES``. Already read at the end of
    the study's own ``main()``, which is that entry point's equivalent of this
    module. Importing ``ablation.study`` here would drag the whole study --
    graph, fixtures, thread pool -- into ``25- Batch Runner.py``.
  * ``mcp/server.py:TOOL_FAILURES``. Already has ``tool_failure_summary()``,
    and an MCP server is a long-lived process rather than a run: there is no
    end for a run-end report to attach to.
  * ``fhir/parser.py``'s four (BIRTH_DATE_PRECISION_COUNTS,
    DEMOGRAPHIC_SOURCE_COUNTS, ECOG_VALUE_SHAPE_COUNTS, ECOG_SELECTION_COUNTS).
    These are CHARACTERIZATION counters, not degradation ones -- every parse
    increments one of them, so "non-zero" is the normal state and printing them
    in a degradation report would bury the signal under a census. They are
    already printed by ``load_all_patients()``.

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
from oncotriage import tracking as _tracking


log = get_logger(__name__)


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
    ("MALFORMED_EVALUATION_ENTRIES", _agent_evaluation.MALFORMED_EVALUATION_ENTRIES,
     "Stage 5 returned a top-level entry that was not an object; it was "
     "dropped and reached no verdict"),
    ("REFUSALS_OBSERVED", _agent_evaluation.REFUSALS_OBSERVED,
     "the model DECLINED to answer; that patient ended at the error handler"),
    ("INFERENCE_WRITE_RETRIES", _database_logger.INFERENCE_WRITE_RETRIES,
     "a database write was retried and survived; contention, not loss"),
    ("INFERENCE_WRITE_FAILURES", _database_logger.INFERENCE_WRITE_FAILURES,
     "a database write was GIVEN UP ON -- the row is lost; the reconciliation "
     "block above is the authority on which"),
    ("JOURNAL_MODE_DEGRADATIONS", _database_logger.JOURNAL_MODE_DEGRADATIONS,
     "the database is not in the journal mode SQLITE_JOURNAL_MODE asked for; "
     "keyed requested->actual"),
    ("FIELD_DROPS", _observability.FIELD_DROPS,
     "a log field was dropped for not being on LOGGABLE_FIELDS; the field "
     "NAME only, never its value"),
    ("EMIT_FAILURES", _observability.EMIT_FAILURES,
     "a console or log line could not be written; THIS REPORT IS ITSELF "
     "SUSPECT when this is non-zero"),
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
    for name, counter in _REGISTRY.items():
        live = {k: v for k, v in counter.items() if v}
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
