# MCP Server
############

"""Three tools over the matching pipeline, spoken to an MCP client on stdio.

    parse_fhir_bundle   a path to a FHIR bundle -> the DE-IDENTIFIED record
    match_patient       a path to a FHIR bundle -> ranked trials with verdicts
    lookup_trial        an NCT ID              -> the indexed trial record

EVERY RESPONSE THAT CARRIES PATIENT MATERIAL IS DE-IDENTIFIED AND THEN SCANNED.
The consumer of an MCP tool result is a MODEL, so this is a model-facing surface
in exactly the sense ``oncotriage/deid.py`` is written for. Patient material
leaves as a ``deid.DeidentifiedRecord`` -- pseudonym in place of ``patient_id``,
age capped at ``deid.AGE_CAP_YEARS``, no birth date, and exactly
``deid.RENDERED_FIELDS`` -- and every tool return is the return value of
``_guard_response``, which refuses rather than sending. See THE
DE-IDENTIFICATION BOUNDARY and THE RESPONSE BOUNDARY GATE below.

EVERY TOOL IS A WRAPPER AND NOTHING HERE MATCHES, RETRIEVES, RERANKS OR JUDGES.
The functions actually called, with their import paths:

    oncotriage.fhir.parser.parse_fhir_bundle          the FHIR parse
    oncotriage.agent.graph.build_matching_graph       compiles the 6-stage graph
    oncotriage.agent.graph.match_patient_to_trials    runs it
    oncotriage.agent.readiness.probe_index            the four-state index probe
    oncotriage.retrieval.trial_lookup.lookup_trial    the NCT read
    oncotriage.utils.deduplicate_by_display           the summary counts

Only one of those is new, and it is new because it did not exist: there was no
public "give me the trial with this NCT ID" anywhere in the package, only an
inline ``scroll`` inside ``node_hybrid_retrieval``'s payload backfill. The
argument for where it went instead of into this file is at the top of
``oncotriage/retrieval/trial_lookup.py``.


CONNECTING A CLIENT
-------------------
The transport is stdio, so the client starts this process itself and speaks to
it over the pipe. The entry point is ``mcp_server.py`` at the code directory
root; the config block is the same shape for Claude Desktop
(``claude_desktop_config.json``), Claude Code (``.mcp.json``) and any other
stdio client:

    {
      "mcpServers": {
        "oncotriage": {
          "command": "/ABSOLUTE/PATH/TO/python",
          "args": ["/ABSOLUTE/PATH/TO/03- Code/mcp_server.py"]
        }
      }
    }

BOTH PATHS ARE ABSOLUTE AND THAT IS DELIBERATE, not an oversight against this
project's "never write an absolute path" rule -- which is a rule about SOURCE.
A client launches the server from a working directory nobody here chooses, so:

  * ``command`` must be the interpreter that has this project's dependencies.
    A bare ``"python"`` resolves against the client's ``PATH``, which for a GUI
    application on macOS is not the shell's -- the usual symptom is
    ``ModuleNotFoundError: No module named 'mcp'`` in the client's log. Use
    ``python -c "import sys; print(sys.executable)"`` from the environment where
    the pipeline runs.
  * ``args`` names the script absolutely. NO ``cwd`` KEY IS NEEDED, and that is
    a property of the bootstrap rather than luck: ``mcp_server.py`` finds the
    package from ``os.path.dirname(os.path.abspath(__file__))`` before falling
    back to the working directory, so the code directory is located from the
    script's own path. Verified by running it from an unrelated directory: three
    tools listed, stdout clean. A ``"cwd"`` key is honoured by some clients and
    ignored by others, which is why nothing here depends on one.
  * The space in ``03- Code`` needs no escaping. It is one element of a JSON
    array, not a shell word.

Add ``"env": {"ONCOTRIAGE_QDRANT_URL": "..."}`` to point the two index-backed
tools at a different Qdrant; see ``oncotriage/settings.py`` for the full set.


STDOUT IS THE PROTOCOL CHANNEL
------------------------------
Over stdio the client parses this process's stdout as a stream of JSON-RPC
messages, one per line. One stray byte ends the session. This project has
neither structured logging nor a print-free pipeline -- ``match_patient_to_trials``
alone prints a banner, six stage lines and a cost summary -- so the question is
not whether the wrapped code prints but where those prints land.

THERE ARE TWO WINDOWS AND THEY ARE DEFENDED BY DIFFERENT THINGS. Getting this
wrong in either direction is silent, so both are stated with what was measured:

  1. **The import window** -- from process start until the transport starts
     serving. NOT protected by the SDK, and this project genuinely writes here:
     ``oncotriage/paths.py`` line 121 prints ``[Paths] Settings module loaded
     from ...`` at import, and lines 318/323 print ``Running on local machine``
     and ``[Paths] Project root: ...`` on the first path resolution. Measured:
     ``python -c "import oncotriage.fhir.parser"`` with stderr discarded emits
     that banner on STDOUT. ``mcp_server.py`` at the code root is what closes
     this, at the file-descriptor level, and it is the guard the negative
     control in ``tests/test_mcp_server_stdio_contract.py`` section 8c removes.
     (An ``oncotriage/mcp/__main__.py`` holding the same guard was built first
     and withdrawn: it had to import this module from inside a function, which
     is what ``tests/test_package_invariants.py`` check 1b forbids. The check
     caught it. A top-level script is not a package module, so the entry point
     moved out rather than the invariant being weakened.)

  2. **The serving window** -- while ``stdio_server()`` is running. Protected by
     the SDK ITSELF in mcp 2.0.0, which is worth knowing rather than
     re-implementing. Read out of ``mcp/server/stdio.py``: ``_claim_fd(1, ...)``
     duplicates the real stdout onto a private descriptor, points fd 1 at
     stderr (``_open_stdout_diversion`` is ``os.dup(2)``), writes the protocol
     to the private copy, and restores fd 1 on exit. Its own docstring says so:
     "While serving, fd 0 points at the null device and fd 1 at stderr, so
     handlers and children read EOF and their stray output misses the wire".
     So on this SDK version a ``print`` inside a tool cannot corrupt the stream.

WHY ``_stdout_to_stderr`` EXISTS ANYWAY, since window 2 is already covered. Two
reasons, and neither is belt-and-braces for its own sake:

  * **The SDK has a documented fallback in which the protection is absent.**
    ``_claim_fd`` returns ``stream.buffer`` unchanged when the descriptor cannot
    be duplicated or diverted (``except OSError: return stream.buffer, release``)
    and when ``_is_backed_by_fd`` is False -- which is every case where stdout is
    not a real fd, including a caller that passes its own streams to
    ``stdio_server()``. On that path fd 1 is NOT diverted and the protocol shares
    ``sys.stdout.buffer`` with every ``print`` in the pipeline, where two writers
    on one pipe tear frames. The SDK's own comment says exactly that.
  * **It is the only layer this file controls.** The fd-level protection is a
    property of a dependency at a pinned version. A guard that is load-bearing
    only until somebody upgrades is a guard that will be silently absent one day,
    and the failure it prevents is a corrupted stream rather than an exception.

It is a PYTHON-LEVEL redirect (``contextlib.redirect_stdout``) and deliberately
not an fd-level one. An fd-level ``dup2`` here would be process-global and would
capture whatever the transport wrote CONCURRENTLY -- another request's response,
a progress notification -- into stderr, losing protocol messages in order to
protect the protocol. The fd level belongs to the two places that own the whole
process at that moment: ``mcp_server.py`` before serving, and the SDK during
it.

NOTHING IN THIS FILE PRINTS. Diagnostics go to stderr through ``_log``.


IMPORTING THIS MODULE OPENS NOTHING, and the ``mcp`` import is inside
``build_server()`` for that reason. It is the project's documented third-party-
in-a-function-body exemption -- the same one that keeps ``import icd10`` inside
``_build_icd10_cancer_sets()`` and ``import torch`` inside
``stage2_retrieval_tests()`` -- and here it buys two things: the package's
per-module import sweep (``tests/test_package_invariants.py`` section 2c) can
import this file under its twelve traps without the SDK, starlette, uvicorn and
their mimetype tables arriving with it, and the three wrapped functions stay
callable, and therefore testable, with no server object in existence.
"""

import contextlib
import functools
import json
import os
import sys
import threading
import traceback
from collections import Counter
from typing import Any

from oncotriage import __version__
from oncotriage import deid
from oncotriage import spend
from oncotriage.agent.graph import build_matching_graph, match_patient_to_trials
from oncotriage.agent.patient import compute_patient_hash
from oncotriage.agent.readiness import (
    INDEX_ABSENT,
    INDEX_EMPTY,
    INDEX_POPULATED,
    INDEX_UNVERIFIABLE,
    probe_index,
)
from oncotriage.config import MATCHING_MODEL, MAX_TRIALS_FOR_EVALUATION, Project_Name
from oncotriage.constants import NOT_FOR_CLINICAL_USE, NOT_FOR_CLINICAL_USE_SHORT
from oncotriage.fhir.parser import parse_fhir_bundle
from oncotriage.retrieval.trial_lookup import lookup_trial
from oncotriage.utils import deduplicate_by_display
from oncotriage.observability import console


#------------------------------------------------------------------------------


SERVER_NAME = "oncotriage"

MAX_AGE_STATED_EXACTLY = deid.AGE_CAP_YEARS
"""What the tool description tells a caller about the age cap.

READ OFF ``oncotriage/deid.py`` RATHER THAN TYPED. A description that
said "89" while the stage capped at something else would be a second
declaration of one number -- the shape this project removed for the
BM25 model name, the MedCPT checkpoint and the per-model cost
arithmetic -- and a tool description is spent from a model's context
window on every listing, so a wrong one is read on every call."""

TOOL_FAILURES = Counter()
"""Every exception that leaves a tool, keyed ``{tool}:{ExceptionType}``.

Module-level, following ``PARTIAL_DATE_DEGRADATIONS`` in ``oncotriage/utils.py``
and the four counters item 11a added. The project's standing rule is that no
exception is caught without being re-raised or recorded; these handlers do BOTH,
because a stdio server has no other place to report from -- the client is told a
one-line message and the operator has only stderr and this counter. It is read
by ``failure_report()`` and by the contract test.
"""


def failure_report() -> dict:
    """A copy of ``TOOL_FAILURES``. The counter's reader, so it is not a
    declaration nothing consults (``tests/test_package_invariants.py`` 2h)."""
    return dict(TOOL_FAILURES)


def _log(message):
    """Write a diagnostic to stderr. NEVER stdout -- see the module docstring.

    ``flush=True`` because stderr is block-buffered when it is a pipe, which is
    what an MCP client gives it, and a diagnostic that arrives after the process
    dies is not a diagnostic.
    """
    console.out(f"[{SERVER_NAME}-mcp] {message}", file=sys.stderr, flush=True)


@contextlib.contextmanager
def _stdout_to_stderr():
    """Anything the wrapped pipeline prints goes to stderr for this call.

    Python level only, and the module docstring argues why the fd level is
    somebody else's job. Re-entrant and concurrency-safe in the only sense that
    matters here: ``redirect_stdout`` restores whatever ``sys.stdout`` was on
    entry, so overlapping tool calls can only ever leave it pointing at stderr
    or at the object the transport already diverted to stderr. Neither is the
    protocol wire.
    """
    with contextlib.redirect_stdout(sys.stderr):
        yield


#------------------------------------------------------------------------------


# ===========================================================================
# THE COMPILED GRAPH
# ===========================================================================
#
# LAZY AND LOCKED, matching oncotriage/agent/deps.py and the accessors in
# fhir/clean.py and fhir/explore.py. Lazy because importing a package module
# must compile no graph (section 2 of the invariants test imports this file with
# builtins.open trapped). Locked because `if x is None: x = build()` is two
# atomic operations and one non-atomic sequence, and the MCP SDK dispatches tool
# calls from a task group -- two overlapping match calls on a cold process is
# the ordinary case, not the exotic one, and two compiled graphs is a real cost
# rather than a tidiness point.

_GRAPH = None
_GRAPH_LOCK = threading.RLock()


def get_graph():
    """The compiled LangGraph pipeline, built once on first use.

    The build itself is inside ``_stdout_to_stderr`` because
    ``build_matching_graph()`` is not silent.
    """
    global _GRAPH
    with _GRAPH_LOCK:
        if _GRAPH is None:
            with _stdout_to_stderr():
                _GRAPH = build_matching_graph()
        return _GRAPH


#------------------------------------------------------------------------------


# ===========================================================================
# THE INDEX GATE
# ===========================================================================

_INDEX_GUIDANCE = {
    INDEX_EMPTY: (
        "The trial collection exists but holds zero points. Build it with: "
        "python \"11- RAG Trial Indexer.py\" --mode direct"),
    INDEX_ABSENT: (
        "No collection or alias of that name exists on the server. Create and "
        "populate it with: python \"11- RAG Trial Indexer.py\" --mode direct"),
    INDEX_UNVERIFIABLE: (
        "The server could not be asked at all -- transport error, auth "
        "failure, or an unreachable endpoint. This is NOT evidence that the "
        "index is empty. Check ONCOTRIAGE_QDRANT_URL / "
        "ONCOTRIAGE_QDRANT_API_KEY and the credentials file."),
}
"""What an operator should do about each blocking state. Keyed by the closed
vocabulary in ``oncotriage/agent/readiness.py``; ``INDEX_POPULATED`` is
deliberately absent, because there is nothing to do about it and a dict with an
entry meaning "fine" invites a lookup that treats it as a problem."""


def _index_unavailable_result(tool, verdict) -> dict:
    """The result a tool returns instead of an answer when the index is unusable.

    THE SHAPE IS THE POINT. There is no ``matches`` key, no ``trial`` key and no
    count -- not an empty list, not a zero. Stage 2 against an empty collection
    SUCCEEDS and returns nothing, the graph routes to ``node_no_candidates``,
    and the well-formed "no eligible trials found" that comes back is
    indistinguishable from a genuinely unmatchable patient. That is the exact
    defect ``oncotriage/agent/readiness.py`` was written to remove, and a tool
    result carrying ``"matches": []`` beside a warning would reintroduce it one
    layer up: a model reading the payload would have a plausible answer sitting
    next to a caveat, and models summarise caveats away.
    """
    return {
        "not_for_clinical_use": NOT_FOR_CLINICAL_USE,
        "status": "index_unavailable",
        "tool": tool,
        "index_state": verdict["state"],
        "collection": verdict["collection"],
        "endpoint": verdict["endpoint"],
        "probe_error": verdict["error"],
        "message": (
            f"The trial index is not usable ({verdict['state']}), so this tool "
            f"cannot answer. NO RESULT IS BEING REPORTED -- this is not a "
            f"finding of zero matching trials, it is the absence of an index to "
            f"search. " + _INDEX_GUIDANCE[verdict["state"]]),
    }


def _budget_unavailable_result(tool, exc) -> dict:
    """The result a tool returns instead of an answer when the budget is spent.

    THE SHAPE IS ``_index_unavailable_result``'s, FOR ITS EXACT ARGUMENT.
    There is no ``matches`` key, no ``result`` key, no ``trial`` key and no
    count -- not an empty list, not a zero. A model reading a payload that
    carried ``"matches": []`` beside a warning would have a plausible answer
    sitting next to a caveat, and models summarise caveats away. "The pipeline
    was not run" and "the pipeline found nothing" must not be the same payload,
    whether the cause is an empty index or an empty budget.

    ``retry_after_seconds`` IS THE ONE FIELD THAT IS NOT IN THE INDEX VERSION,
    and it is here because the two conditions differ in exactly that way: an
    absent index needs an operator, and this clears itself as the rolling
    window rolls. It is ``None`` when the wait cannot be derived -- the
    call-ceiling limit does not heal with time, and a number there would tell a
    caller to wait for something that will not happen.
    """
    return {
        "not_for_clinical_use": NOT_FOR_CLINICAL_USE,
        "status": "spend_limit_reached",
        "tool": tool,
        "limit": getattr(exc, "limit", None),
        "retry_after_seconds": spend.seconds_until_under_cap(
            spend.SPEND_SOURCE_STAGE5),
        "message": (
            "This server has reached its spend limit, so this tool cannot "
            "answer. NO RESULT IS BEING REPORTED -- this is not a finding of "
            "zero matching trials, it is a refusal to issue the billed "
            "requests that would produce one. The limit is a rolling window "
            "and clears on its own; try again later."),
    }


def _require_budget(tool):
    """``None`` when a billed tool may proceed, else the result it must return.

    POLICY, WRITTEN HERE BECAUSE THIS IS THE CALL SITE THAT APPLIES IT --
    ``_require_index``'s shape, one concern over. ``spend.require_budget``
    RAISES, which is right for the four callers that want an exception; this
    server answers in payloads, so the raise is caught once, here, and turned
    into one.

    IT DOES NOT LATCH, and it does not have to say so: ``require_budget``
    derives that from the policy, and ``main()`` installs ``serving_window``
    for the reason ``spend.SPEND_POLICIES`` gives. A latch here would make one
    over-budget minute refuse every tool call for the life of the client
    session.
    """
    try:
        spend.require_budget(spend.SPEND_SOURCE_STAGE5,
                             f"the MCP {tool} tool")
    except spend.SpendLimitReached as exc:
        _log(f"{tool}: refusing, {exc}")
        return _budget_unavailable_result(tool, exc)
    return None


def _require_index(tool):
    """``None`` when the index is usable, else the result the tool must return.

    POLICY, WRITTEN HERE BECAUSE THIS IS THE CALL SITE THAT APPLIES IT.
    ``oncotriage/agent/readiness.py`` answers what is true and leaves the
    decision to each caller; it already has two callers with different policies
    and this is the third.

      populated                  -> proceed.
      empty / absent             -> refuse. Retrieval would return an empty
                                    list and report success.
      unverifiable               -> REFUSE, which is the API STARTUP policy and
                                    NOT Stage 2's. Stage 2 continues on an
                                    unverifiable probe because it must not turn
                                    a transient hiccup into a total outage and
                                    it records per-channel failure that somebody
                                    reads afterwards. Neither holds here: an MCP
                                    caller sees one JSON payload and has no
                                    channel report, and ``match`` would spend a
                                    live billed Stage 5 call on a retrieval
                                    result nobody could vouch for. A tool call
                                    that costs money can afford to demand proof.

    ``probe_index`` is called rather than ``require_populated_index`` because the
    latter caches a populated verdict process-wide and raises on the blocking
    states; this needs the verdict as data in order to report it, and the cache
    is Stage 2's optimisation to own.
    """
    with _stdout_to_stderr():
        verdict = probe_index()

    if verdict["state"] == INDEX_POPULATED:
        return None

    _log(f"{tool}: refusing, index state is {verdict['state']} "
         f"({verdict['collection']!r} at {verdict['endpoint']})")
    return _index_unavailable_result(tool, verdict)


#------------------------------------------------------------------------------


# ===========================================================================
# SHARED HELPERS
# ===========================================================================

def _resolve_bundle_path(bundle_path) -> str:
    """Validate a bundle path and return it, or raise ``ValueError`` saying why.

    A PATH AND NOT INLINE JSON, which is what the tools take. A bundle is a
    multi-megabyte document and an MCP argument is spent from the caller's
    context window; handing over a path costs a line and keeps the parser on the
    file route it already had.

    ``~`` is expanded because a human writes a client config by hand. The path is
    NOT made absolute against anything but the process's own working directory,
    which is what the client controls through the ``cwd`` field.
    """
    if not isinstance(bundle_path, str):
        raise ValueError(
            f"bundle_path must be a string, got {type(bundle_path).__name__}.")

    cleaned = os.path.expanduser(bundle_path.strip())
    if not cleaned:
        raise ValueError("bundle_path is empty.")

    if not os.path.exists(cleaned):
        raise ValueError(
            f"No such file: {cleaned}. bundle_path must be a path to a FHIR "
            f"bundle JSON file on the machine running this server, not the "
            f"bundle's contents.")

    if not os.path.isfile(cleaned):
        raise ValueError(f"Not a file: {cleaned}. Expected a FHIR bundle JSON "
                         f"file, not a directory.")

    return cleaned


def _parse_bundle(path):
    """Parse a bundle at ``path``. Returns ``(patient_data, source_bundle)``.

    BOTH BUNDLE TOOLS GO THROUGH THIS, so the two cannot disagree about what a
    bad bundle looks like -- and it exists because the first version let the
    parser's own exception through. Measured: a text file that is not JSON came
    back to the client as

        Error executing tool parse_fhir_bundle: Expecting value: line 1 column 1

    which is json's message, is technically accurate, and NAMES NEITHER THE FILE
    NOR THE PARAMETER. A caller that passed one of several paths cannot tell
    which one was wrong, and a model reading it has nothing to correct. The
    exception is re-raised (chained, so the operator's stderr keeps the original
    traceback) rather than swallowed, which is the project's rule, and it is
    re-raised as ``ValueError`` so it joins the other input faults instead of
    arriving as a third exception type a caller has to know about.

    THE DECODED BUNDLE IS RETURNED BESIDE THE PARSED RECORD, and that is the
    de-identification pass's third layer finally being used. ``oncotriage/deid.py``
    names three inventory layers -- the parsed record's ``patient_id``, the
    provenance-free shape rules, and ``harvest_identifiers`` "when a caller has
    the bundle to hand ... the complete answer" -- and says the production graph
    path holds only ``patient_data`` by the time Stage 5 runs. THIS SURFACE HAS
    THE FILE. So the guard here scans against every name, address line, telephone
    number, record number and government identifier the SOURCE carried, which is
    strictly more than Stage 5 can see.

    The JSON is decoded HERE rather than inside the parser so that the decoded
    document is available for that harvest. ``parse_fhir_bundle`` takes either a
    dict or a path (pass 20f-1) and the dict route is "same everything below",
    so nothing about the parse moves.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            source_bundle = json.load(handle)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(
            f"{path} could not be read as JSON ({type(exc).__name__}: {exc}). "
            f"bundle_path must point at a FHIR bundle JSON file.") from exc

    if not isinstance(source_bundle, dict):
        # A JSON ARRAY OR SCALAR, which decodes cleanly and is not a Bundle.
        # Named here rather than left to the parser: handing a list to
        # `parse_fhir_bundle` takes its PATH branch and dies in `open()` with a
        # TypeError naming neither the file nor the parameter -- the same defect
        # this function was written to remove one shape over.
        raise ValueError(
            f"{path} decoded as JSON {type(source_bundle).__name__} rather than "
            f"an object. bundle_path must point at a FHIR Bundle containing a "
            f"Patient resource.")

    with _stdout_to_stderr():
        patient_data = parse_fhir_bundle(source_bundle)

    if not patient_data or not patient_data.get("patient_id"):
        raise ValueError(
            f"{path} parsed as JSON but yielded no patient record with an id. "
            f"It must be a FHIR Bundle containing a Patient resource.")

    return patient_data, source_bundle


#------------------------------------------------------------------------------


# ===========================================================================
# THE DE-IDENTIFICATION BOUNDARY
# ===========================================================================
#
# WHAT THIS SURFACE HANDS BACK, AND TO WHOM. The consumer of an MCP tool result
# is a MODEL: the client puts the payload into a language model's context and
# the model summarises it. So this file is a model-facing surface in exactly the
# sense `oncotriage/deid.py` is written for, and until this pass it was the one
# such surface with no de-identification stage in front of it. Three leaks were
# recorded by that pass and left, deliberately, because closing them changes
# response shapes and it refused to ship that unverified:
#
#   _patient_summary        returned `patient_id` -- which on this corpus is
#                           byte-identical to the Medical Record Number in the
#                           bundle's `identifier[]` -- and the EXACT age, with
#                           no 90-or-older cap.
#   parse_fhir_bundle_tool  returned the whole parsed record, `birth_date`
#                           and all.
#   match_patient_tool      returned `result`, which carries `patient_id`.
#
# A FOURTH WAS FOUND BY BUILDING THE GATE AND NOT BY READING: every successful
# response carried `source_path`, the caller's bundle path, and this corpus's
# filenames are `Adela471_Virginia437_Verduzco...json` -- the patient's given
# and family names, in the response, on every call. It is gone; see
# `_source_path_note` below for what replaced it and what did not.
#
# THE FIX IS THE STAGE THAT ALREADY EXISTS, NOT A SECOND ONE. Every response
# that carries patient material is built from a `deid.DeidentifiedRecord` --
# the same object `render_patient_record` is handed and the only thing
# `deid.deidentify` produces -- so the pseudonym, the age cap and the
# `RENDERED_FIELDS` key set are one implementation with one owner. A
# server-local redaction would be a second copy of a rule, which is the drift
# this project has removed four times (the BM25 model name, the MedCPT
# checkpoint, the per-model cost arithmetic, the latest-run-per-config SQL).
#
# AND THEN IT IS ENFORCED. `_guard_response` scans what is about to leave and
# raises on a hit, so the guarantee is a gate rather than a promise. It is
# STRICTLY STRONGER THAN STAGE 5's, because this surface has the source bundle
# and Stage 5 does not.


def _deidentified_record(patient_data, source_bundle):
    """The de-identification stage, run over one parsed record.

    ``build_patient_record`` in ``oncotriage/agent/patient.py`` is the pairing
    of these two lines that Stage 5 uses, and it is deliberately NOT called
    here: it also RENDERS, and rendering increments ``PROCEDURE_RENDER_COUNTS``
    and ``TEMPORAL_RENDER_COUNTS`` -- census counters an operator reads at the
    end of a run. `oncotriage/run_fingerprint.py` rejected a probe render for
    exactly that reason ("pollute ... with a render no patient asked for"), and
    a parse tool that has no prompt to build must not put a phantom render into
    a campaign's census. The identity derivation is the same call in both
    places, which is what keeps the pseudonym here equal to the one in the
    prompt.
    """
    return deid.deidentify(
        patient_data,
        identity=compute_patient_hash(patient_data),
        source_bundle=source_bundle,
    )


def _no_patient_record():
    """The record used to guard a response that carries no patient at all.

    A REAL ``DeidentifiedRecord`` RATHER THAN ``None``, so `_guard_response`
    has one signature and every tool return goes through the identical call.
    Its pseudonym is ``deid.PSEUDONYM_UNKNOWN`` and its inventory is empty,
    which is the honest statement for a trial lookup or a refusal: there is no
    patient in this payload, so there is nothing to scan it against. The
    provenance-free shape rules still run.

    Built per call rather than at module scope: a shared mutable record is one
    edit away from a caller stashing something on it, and building this costs
    nothing (`deidentify` opens nothing and renders nothing).
    """
    return deid.deidentify({}, identity=None)


def _patient_summary(record) -> dict:
    """The small block both bundle tools return, so the two cannot disagree.

    IT IS BUILT FROM THE DE-IDENTIFIED RECORD AND NOT FROM ``patient_data``,
    and that is the whole of the fix rather than a stylistic preference. The
    previous version read ``patient_data["patient_id"]`` and
    ``demographics["age"]`` directly, so "no direct identifier is reported" was
    a property of which four keys these ten lines happened to read -- and they
    read the wrong ones. ``record.fields`` carries exactly
    ``deid.RENDERED_FIELDS`` and its demographics carry exactly
    ``deid.DEMOGRAPHIC_FIELDS``, so ``patient_id`` and ``birth_date`` are not in
    scope at the point a line could be written from them, and the age is
    whatever ``deid._cap_age`` returned.

    ``pseudonym`` REPLACES ``patient_id``. It is stable across runs and across
    machines, it is the same token the Stage 5 prompt prints for this patient,
    and it identifies nobody without the local ``inferences`` database -- which
    is where ``oncotriage/deid.py`` rules the crosswalk must live.

    ``deduplicate_by_display`` is the same call ``oncotriage/api/server.py``
    makes for the same counts. ECOG is surfaced because it is the one field the
    parser routes out of ``observations`` deliberately, and because its ``None``
    is meaningful: ECOG 0 is FULLY ACTIVE, the most eligible a patient can be, so
    it is reported as ``None`` and never defaulted to 0.
    """
    fields = record.fields
    demographics = fields.get("demographics") or {}
    ecog = fields.get("ecog_performance_status") or {}
    return {
        "pseudonym": record.pseudonym,
        "age": demographics.get("age"),
        "sex": demographics.get("sex"),
        "condition_count": len(deduplicate_by_display(
            fields.get("conditions") or [])),
        "medication_count": len(deduplicate_by_display(
            fields.get("medications") or [])),
        "allergy_count": len(fields.get("allergies") or []),
        "ecog_performance_status": ecog.get("value"),
    }


def _deidentification_block(record, scanned, skipped) -> dict:
    """What the stage DID, reported so a caller need not infer it.

    COUNTS ONLY, NEVER CLASSES AND NEVER VALUES. A refusal names the identifier
    CLASSES it found, because that reaches an operator who has to act; a routine
    successful response naming which classes this patient's bundle carried would
    be telling a model that this record has, say, a government identifier in it.
    ``oncotriage/deid.py``'s own rule -- minimisation is cheaper than review.

    ``identifier_values_not_scanned`` IS THE GUARD'S OWN BLIND SPOT AND IT IS
    REPORTED RATHER THAN SWALLOWED, which is `scan_for_identifiers`'s stated
    reason for returning it at all: a scan that silently declined to look for
    part of its inventory reads exactly like a clean one.
    """
    return {
        "pseudonym": record.pseudonym,
        "age_capped": record.age_capped,
        "age_cap_years": deid.AGE_CAP_YEARS,
        "identifier_values_scanned": scanned,
        "identifier_values_not_scanned": skipped,
    }


def _deidentified_result(result, record) -> dict:
    """``match_patient_to_trials``'s result with the identity replaced.

    ONE FIELD SUBSTITUTION USING THE OWNER'S OWN FUNCTION, not a redaction pass
    over the result. ``patient_id`` is dropped and ``patient_pseudonym`` takes
    its POSITION, so a reader of the payload finds the identity where it always
    was and a client that blindly read ``result["patient_id"]`` gets a KeyError
    rather than a plausible-looking value that is not an id.

    ``patient_data_hash`` IS DELIBERATELY LEFT IN PLACE. It is not a direct
    identifier -- it is a hash of the clinical record -- it is never logged, and
    it is the column ``inferences`` carries beside ``patient_id``, which
    ``oncotriage/deid.py`` names as the one permitted crosswalk. Removing it
    would break the coordinator workflow (an authorised operator resolving a
    match back to a patient THROUGH THE LOCAL DATABASE) and buy nothing: holding
    the pair (hash, pseudonym) yields no identity, because the pseudonym is
    derived from the hash by a public function and the hash is derived from the
    clinical record. Anyone who can act on it already holds the database.

    ``setdefault`` at the end is not decoration: a result shape that stops
    carrying ``patient_id`` must still carry the pseudonym, or a future
    consumer would silently lose the only identity the payload has.
    """
    out = {}
    for key, value in result.items():
        if key == "patient_id":
            out["patient_pseudonym"] = record.pseudonym
        else:
            out[key] = value
    out.setdefault("patient_pseudonym", record.pseudonym)
    return out


# ===========================================================================
# THE RESPONSE BOUNDARY GATE
# ===========================================================================
#
# A RESPONSE PATH THAT MERELY DOES NOT PRINT IDENTIFIERS IS A PROMISE. This
# project ships gates: Stage 5 scans the rendered prompt before any model call
# and fails the patient rather than sending it, and this is the same enforcement
# point one surface over. Every tool return in this file is the return value of
# `_guard_response`, so there is no response path around it -- which is a
# structural property a test can assert, and does.
#
# WHAT IS SCANNED, AND WHY IT IS NOT EVERYTHING. The scan runs over the
# PATIENT-DERIVED part of the payload. Scanning the trial records too was tried
# first and MEASURED, against the live 14,324-trial collection and the real
# corpus:
#
#   * EXACT MATCHES ON TRIAL TEXT. One corpus patient lives in Ontario. Their
#     bundle's `address.city` is therefore the harvested value "Ontario", and
#     "Ontario" appears in 7 of 200 indexed trials -- Ontario, Canada, in a
#     `locations[].city`. 3.5% of `match_patient` calls would refuse a patient
#     over a trial site in another country. It is the exact false positive
#     `oncotriage/deid.py` names in its own cost section ("a city called
#     Ontario") and it is real here rather than hypothetical.
#   * SHAPE RULES ON TRIAL TEXT. 2 URLs and 1 email in the same 200 trials.
#     ClinicalTrials.gov records carry an `overall_contact` and reference links;
#     they are public data about a professional contact and they are what the
#     tool exists to return.
#
# So the trial subtree is exempt, and the exemption is NOT a hole:
# `matches`/`near_misses`/`not_evaluable` are the model's verdicts on trials,
# derived from the Stage 5 prompt -- and `result["llm_classifier_prompt"]`, the
# prompt itself, IS scanned here, with the FULL bundle inventory. A patient
# identifier that reached a verdict must have passed through that prompt, and
# the prompt is exactly what this gate reads hardest.
#
# THE DEFAULT IS SCANNED. A key added to a response, or to `result`, is scanned
# unless somebody names it below with an argument. That is the fail-safe
# direction: a forgotten key gets read, and if it turns out to be trial text the
# symptom is a refusal an operator fixes with one tuple entry -- not a leak
# nobody sees.

UNSCANNED_RESPONSE_KEYS = ("trial", "endpoint", "probe_error")
"""Top-level response keys the gate does not read, each argued.

``trial``          the indexed ClinicalTrials.gov record `lookup_trial` exists
                   to return. Public data, and measured above to carry URLs and
                   a contact email that the shape rules fire on.
``endpoint``       the Qdrant URL, reported by an index refusal so an operator
                   can fix it. It is `config.get_qdrant_url()`'s answer -- server
                   infrastructure, which no patient material can reach -- and it
                   is a literal ``https://`` URL, so the URL shape rule fires on
                   every single index-unavailable refusal if it is scanned.
                   FOUND BY RUNNING THE GATE, not by reading it.
``probe_error``    the transport error beside it, same provenance, same reason.
"""

UNSCANNED_RESULT_KEYS = ("matches", "near_misses", "not_evaluable")
"""Keys INSIDE ``result`` the gate does not read: the three per-trial verdict
lists. See the measurement above. Everything else in ``result`` is scanned,
including ``llm_classifier_prompt``, which is the rendered patient record."""


def _scannable_text(payload) -> str:
    """The patient-derived part of one response, as text to scan.

    ``sort_keys`` so two scans of one payload report identical offsets, which is
    what makes a refusal comparable across runs -- the same reason
    `deid.scan_for_identifiers` sorts its findings. ``default=str`` so an object
    that is not JSON-serialisable is READ rather than raising: a gate that threw
    on an unexpected type would be a gate that stopped scanning.
    """
    scannable = {key: value for key, value in payload.items()
                 if key not in UNSCANNED_RESPONSE_KEYS}
    result = scannable.get("result")
    if isinstance(result, dict):
        scannable["result"] = {key: value for key, value in result.items()
                               if key not in UNSCANNED_RESULT_KEYS}
    return json.dumps(scannable, default=str, sort_keys=True)


def _guard_response(tool, payload, record):
    """Return ``payload``, or raise ``deid.IdentifierLeakError`` if it leaks.

    THE ENFORCEMENT POINT FOR THIS SURFACE. `deid.assert_no_identifiers` is
    called rather than reimplemented, so the classes, the rules, the length and
    distinctiveness floors and the ``DEID_REFUSALS`` counter are the same ones
    Stage 5 uses -- one owner, and a run's degradation block reports an MCP
    refusal exactly as it reports a Stage 5 one.

    IT RAISES RATHER THAN RETURNING A REFUSAL PAYLOAD, unlike the index and
    spend gates one section up, and the difference is what the two conditions
    ARE. An unusable index and a spent budget are expected operational states
    with an operator remedy, so they are answered as data. A leak is a DEFECT --
    in the parser, the stage, or the bundle -- and the honest answer to "here is
    the patient's record" is no payload at all rather than a payload plus a
    caveat. An exception also cannot leak: `deid.IdentifierLeakError` names the
    classes and the rules and deliberately quotes no value, and the tool result
    the client receives is that one sentence.

    `_counted` COUNTS IT AS ``{tool}:IdentifierLeakError``, so the per-tool
    tally an operator reads through `failure_report()` distinguishes a leak from
    a bad input without a second counter having to be invented for it.
    """
    text = _scannable_text(payload)
    skipped = deid.assert_no_identifiers(text, record)
    if skipped:
        # THE BLIND SPOT, REPORTED. Values below the length floor or made of one
        # repeated character were not looked for; a scan that declined silently
        # would read exactly like a clean one. Stage 5 logs the same fact.
        _log(f"{tool}: the de-identification scan skipped {skipped} "
             f"identifier value(s) it could not distinguish from noise")
    return payload


#------------------------------------------------------------------------------


def _counted(tool):
    """Decorator: record every exception leaving a tool, then re-raise it.

    BOTH, not either. The project's rule permits re-raising OR counting; a stdio
    server needs both, because the client is handed one sentence by the SDK
    (``Tool.run`` wraps anything raised as ``ToolError(f"Error executing tool
    {name}: {e}")`` -- read out of ``mcp/server/mcpserver/tools/base.py`` line
    181, which is why a wrong input reaches the caller as a message and never as
    a traceback) and the operator is left with stderr.

    The full traceback is written to stderr, where it cannot corrupt the
    protocol and is the only copy anyone will get.

    ``functools.wraps`` IS LOAD-BEARING AND NOT COSMETIC HERE, which is worth a
    sentence because it is exactly the kind of decorator detail that looks like
    tidiness. The SDK derives each tool's JSON Schema by calling
    ``inspect.signature`` on the registered callable; a wrapper declared
    ``(*args, **kwargs)`` therefore advertises two required string parameters
    literally named ``args`` and ``kwargs``, and the real parameter -- the one
    the caller has to supply -- disappears from the contract entirely. That is
    what the first version of this file shipped, and it was caught by printing
    the schemas rather than by reading the code:

        {"properties": {"args": {"type": "string"},
                        "kwargs": {"type": "string"}},
         "required": ["args", "kwargs"]}

    Copying ``__name__``, ``__doc__`` and ``__annotations__`` by hand -- which is
    what it did -- fixes none of it, because none of them is the signature.
    ``functools.wraps`` sets ``__wrapped__``, and ``inspect.signature`` follows
    ``__wrapped__`` by default, so the schema is derived from the real function.
    ``tests/test_mcp_server_stdio_contract.py`` section 2 asserts the parameter
    NAMES, so this cannot regress quietly.
    """
    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                TOOL_FAILURES[f"{tool}:{type(exc).__name__}"] += 1
                _log(f"{tool} failed: {type(exc).__name__}: {exc}")
                traceback.print_exc(file=sys.stderr)
                raise
        return wrapper
    return decorate


#------------------------------------------------------------------------------


# ===========================================================================
# THE THREE TOOLS
# ===========================================================================
#
# Each returns a dict whose FIRST key is the framing. Ordering a dict is not a
# guarantee about how a client renders it, and it is not relied on for
# correctness -- but Python preserves insertion order, json.dumps preserves it,
# and a caveat at the top of the payload is read more often than one at the
# bottom. It costs nothing to put it there.

@_counted("parse_fhir_bundle")
def parse_fhir_bundle_tool(bundle_path: str) -> dict[str, Any]:
    """Parse a FHIR bundle file into the pipeline's de-identified record.

    IT RETURNS ``patient_record`` AND NOT ``patient_data``, AND THE RENAME IS
    THE POINT. The previous version returned ``parse_fhir_bundle``'s output
    whole, under the argument that "trimming it here would be this file deciding
    what a parse means". That argument was right about a thin wrapper and wrong
    about the value: it made `birth_date` and `patient_id` reach a model on
    every call, which is the thing `oncotriage/deid.py` exists to prevent.

    THE TOOL'S PURPOSE SURVIVES INTACT, checked against its own advertised
    contract rather than against taste. `TOOL_SPECS` describes this tool as
    returning "demographics, conditions with coding systems, medications with
    historical status, observations, ECOG performance status, procedures,
    allergies and genomic variants" -- eight names, and `deid.RENDERED_FIELDS`
    is those eight plus the two cancer-staging collections. The de-identified
    record IS the structured patient record this pipeline uses; it is what the
    renderer is handed and what every prompt is built from.

    WHAT A CALLER LOSES, STATED RATHER THAN DISCOVERED. Measured against a real
    corpus bundle, ``deid.deidentify`` drops exactly ``patient_id`` at the top
    level and, inside demographics, ``birth_date``, ``birth_date_precision``,
    ``age_reference_date``, ``race_source`` and ``ethnicity_source``. The first
    two are the leak. The other three are PROVENANCE -- which US Core
    sub-extension answered, and how precise the birth date was -- and losing
    them costs a caller the ability to see that an age was derived from a
    year-only date. That is a real loss and it is accepted: the renderer does
    not read them either, and `deid.DEMOGRAPHIC_FIELDS` argues that a field
    travelling to a boundary unread is one edit from being printed.

    THE ALTERNATIVE WAS CONSIDERED AND REJECTED. Returning the raw record with
    `patient_id` and `birth_date` deleted in place is a SECOND redaction rule,
    living here, drifting from `RENDERED_FIELDS` the first time the parser
    carries a new field -- the drift this project has removed four times. And it
    would still be a promise rather than a guarantee: nothing would stop the
    next field.

    REMOVING THE TOOL WAS ALSO CONSIDERED. It was rejected because the tool's
    purpose is NOT defeated by de-identification -- an eligibility question is
    answered by stage, histology, ECOG, labs and medications, none of which is
    an identifier -- so what it returns after this pass is still the whole of
    what it was for.
    """
    path = _resolve_bundle_path(bundle_path)
    patient_data, source_bundle = _parse_bundle(path)
    record = _deidentified_record(patient_data, source_bundle)
    scanned, skipped = deid.count_scannable(record.inventory)

    return _guard_response("parse_fhir_bundle", {
        "not_for_clinical_use": NOT_FOR_CLINICAL_USE,
        "status": "ok",
        # `source_path` USED TO BE HERE AND IS GONE. On this corpus a bundle
        # filename is `Adela471_Virginia437_Verduzco_<uuid>.json` -- the
        # patient's given and family names -- so echoing the caller's path put a
        # name into every successful response. The gate is what found it: with
        # the path still in the payload, the exact-match layer fires on the
        # harvested `name` values and every call refuses. The caller supplied
        # the path and does not need it back; `patient_summary.pseudonym` is the
        # correlation key, and it is stable across calls and across runs.
        # RESIDUAL, STATED: the input-validation ValueErrors above still name
        # the path, because a caller that passed one of several paths has to be
        # told which one was wrong and no patient has been identified at that
        # point.
        "patient_summary": _patient_summary(record),
        "patient_record": record.fields,
        "deidentification": _deidentification_block(record, scanned, skipped),
    }, record)


@_counted("match_patient")
def match_patient_tool(bundle_path: str) -> dict[str, Any]:
    """Run the full six-stage matching pipeline for one patient bundle."""
    path = _resolve_bundle_path(bundle_path)

    # THE GATE IS BEFORE THE PARSE AND BEFORE THE GRAPH, deliberately: the point
    # of refusing is to not spend a billed Stage 5 call on an index that cannot
    # answer, and everything below this line is on the way to that call.
    unavailable = _require_index("match_patient")
    if unavailable is not None:
        return _guard_response("match_patient", unavailable,
                               _no_patient_record())

    # THE BUDGET GATE IS BELOW THE INDEX GATE AND ABOVE THE PARSE. Below,
    # because an unusable index is a fault an operator must fix while a spent
    # budget clears itself, and a caller shown the second when the first is
    # also true would fix nothing and come back to the same refusal. Above the
    # parse, for `_require_index`'s own stated reason: everything past this
    # line is on the way to a billed call.
    #
    # `lookup_trial` AND `parse_fhir_bundle` ARE DELIBERATELY NOT GATED. Both
    # are free -- one is a Qdrant scroll and one is a file read -- and refusing
    # a free diagnostic because a billed one ran out of money is the rule
    # `spend.BILLED_SITE_EXEMPTIONS` states for the index validator, met here.
    over_budget = _require_budget("match_patient")
    if over_budget is not None:
        return _guard_response("match_patient", over_budget,
                               _no_patient_record())

    patient_data, source_bundle = _parse_bundle(path)
    record = _deidentified_record(patient_data, source_bundle)
    scanned, skipped = deid.count_scannable(record.inventory)

    graph = get_graph()

    with _stdout_to_stderr():
        result = match_patient_to_trials(patient_data=patient_data, graph=graph)

    # THE PIPELINE IS GIVEN THE RAW PARSED RECORD, NOT THE DE-IDENTIFIED ONE,
    # and that is deliberate. Stage 5 runs `build_patient_record` itself and
    # runs its own guard on the rendered prompt; handing it a pre-stripped
    # record would change what the graph evaluates on this surface only, and a
    # serving surface whose pipeline differs from the batch runner's is a
    # surface whose numbers are not comparable. What changes is what LEAVES.
    return _guard_response("match_patient", {
        "not_for_clinical_use": NOT_FOR_CLINICAL_USE,
        "status": "ok",
        "patient_summary": _patient_summary(record),
        "result": _deidentified_result(result, record),
        "deidentification": _deidentification_block(record, scanned, skipped),
    }, record)


@_counted("lookup_trial")
def lookup_trial_tool(nct_id: str) -> dict[str, Any]:
    """Fetch one indexed clinical trial by its NCT ID."""
    unavailable = _require_index("lookup_trial")
    if unavailable is not None:
        return _guard_response("lookup_trial", unavailable,
                               _no_patient_record())

    with _stdout_to_stderr():
        found = lookup_trial(nct_id)

    if not found["found"]:
        # A RESULT, NOT AN ERROR, and the distinction is real: the index was
        # reachable and answered. `lookup_trial` raises rather than reporting
        # `found: False` when it could not ask, so this branch means the server
        # said there is no such trial HERE -- which is a fact about this index,
        # not about ClinicalTrials.gov, and the message says so.
        return _guard_response("lookup_trial", {
            "not_for_clinical_use": NOT_FOR_CLINICAL_USE,
            "status": "not_found",
            "nct_id": found["nct_id"],
            "collection": found["collection"],
            "message": (
                f"{found['nct_id']} is not in the indexed trial collection "
                f"{found['collection']!r}. The index holds recruiting oncology "
                f"trials as of the last index build, so this means the trial "
                f"was not scraped into it -- not that the trial does not exist."),
        }, _no_patient_record())

    return _guard_response("lookup_trial", {
        "not_for_clinical_use": NOT_FOR_CLINICAL_USE,
        "status": "ok",
        "nct_id": found["nct_id"],
        "collection": found["collection"],
        "trial": found["trial"],
    }, _no_patient_record())


#------------------------------------------------------------------------------


# ===========================================================================
# TOOL REGISTRATION
# ===========================================================================
#
# EVERY DESCRIPTION CARRIES THE FRAMING, and it is the SHORT form. The long form
# is what results carry: it is read once, next to the numbers it qualifies. A
# description is spent from the model's context window on every tool listing
# whether the tool is ever called or not, and a caveat long enough to be skimmed
# is a caveat that does not arrive. Both strings live in oncotriage/constants.py
# so the three surfaces that should carry them cannot drift.

TOOL_SPECS = (
    (
        "parse_fhir_bundle",
        parse_fhir_bundle_tool,
        "Parse a FHIR bundle JSON file into the structured patient record this "
        "pipeline uses: demographics, conditions with coding systems, "
        "medications with historical status, observations, ECOG performance "
        "status, procedures, allergies, genomic variants and cancer staging. "
        "Takes a PATH to a file on the machine running this server, not the "
        "bundle's contents. THE RECORD IS DE-IDENTIFIED: the patient is "
        "identified by a stable pseudonym rather than a record number, there "
        "is no name, address, telephone number or birth date, and an age over "
        f"{MAX_AGE_STATED_EXACTLY} is reported as a category. "
        "Reads only; nothing is written and no model is called. "
        + NOT_FOR_CLINICAL_USE_SHORT,
    ),
    (
        "match_patient",
        match_patient_tool,
        "Match one patient to recruiting oncology clinical trials by running "
        "the full six-stage pipeline: MeSH query expansion, hybrid BM25 + "
        "vector retrieval, cross-encoder rerank, rule-based filtering, and "
        f"criterion-level eligibility evaluation by {MATCHING_MODEL}. Takes a "
        "PATH to a FHIR bundle JSON file. SLOW AND NOT FREE: it makes a live "
        f"billed language-model call and evaluates up to "
        f"{MAX_TRIALS_FOR_EVALUATION} trials, taking minutes. Refuses with an "
        "explanation, rather than reporting zero matches, when the trial index "
        "is empty, absent or unverifiable. The patient is identified in the "
        "result by a stable pseudonym rather than a record number. "
        + NOT_FOR_CLINICAL_USE_SHORT,
    ),
    (
        "lookup_trial",
        lookup_trial_tool,
        "Fetch one clinical trial from the indexed collection by its NCT ID "
        "(the form NCT01234567). Returns the title, phase and the full scraped "
        "trial record including eligibility criteria, conditions, "
        "interventions and locations. Reads only; no model is called. Reports "
        "what THIS index holds as of the last build, which is not the same as "
        "what ClinicalTrials.gov holds now. " + NOT_FOR_CLINICAL_USE_SHORT,
    ),
)
"""``(tool name, function, description)``. A table rather than three
``@server.tool()`` decorators, because the decorator form would need the SDK
imported at module scope -- which is the one thing the import-purity rule and
this file's own testability both forbid. The functions stay plain and callable."""


def build_server():
    """Construct the ``MCPServer`` and register the three tools.

    ``from mcp.server import MCPServer`` is INSIDE this function. That is the
    project's documented third-party-in-a-function-body exemption and the
    argument is in the module docstring: it keeps importing this module free of
    the SDK, starlette and uvicorn, which is what lets the package-wide import
    sweep cover it.

    The pattern is the one mcp 2.0.0's own README documents -- ``MCPServer(...)``
    plus ``@mcp.tool()`` -- with the decorator applied by call rather than by
    syntax, which is the same operation. Argument schemas are derived by the SDK
    from the functions' type hints; there is no hand-written JSON Schema here,
    and there should not be, because a second declaration of the same signature
    is a second declaration that can drift.
    """
    from mcp.server import MCPServer

    server = MCPServer(
        name=SERVER_NAME,
        title=Project_Name,
        version=__version__,
        instructions=(
            f"{Project_Name}: match oncology patients to recruiting clinical "
            f"trials.\n\n{NOT_FOR_CLINICAL_USE}\n\n"
            f"parse_fhir_bundle and lookup_trial are free and fast. "
            f"match_patient makes a live billed {MATCHING_MODEL} call and takes "
            f"minutes -- do not call it speculatively or in a loop."),
    )

    for name, function, description in TOOL_SPECS:
        server.tool(name=name, description=description)(function)

    return server


def main():
    """Serve the three tools over stdio until the client disconnects.

    Called by ``oncotriage/mcp/__main__.py``, which holds the import-window
    stdout guard around the import of this module. Running this file directly
    would skip that guard, which is why it has no ``__main__`` block of its own
    -- the documented invocation is ``python -m oncotriage.mcp``.
    """
    # ── THE SPEND POLICY ─────────────────────────────────────────────────
    #
    # A LONG-LIVED SERVER RUNS UNDER A ROLLING WINDOW AND NOT THE CAMPAIGN CAP.
    # This process writes no `runs` row, so nothing seeded its ledger and
    # nothing resets it: under the campaign policy it had no brake at all until
    # it had spent a whole campaign's budget by itself, and then refused every
    # tool call for the life of the client session. `spend.SPEND_POLICIES`
    # carries the argument; `oncotriage/api/server.py` installs the same policy
    # in its lifespan for the same reason.
    #
    # THE LEDGER IS RESET WITH IT, so a second `main()` in one interpreter (a
    # test) does not inherit the first one's window.
    #
    # THE BANNER GOES TO STDERR like every other line this module writes:
    # stdout is the JSON-RPC stream and one stray byte ends the session.
    # `_log` is the one writer that knows that.
    spend.SPEND_LEDGER.reset()
    spend.SPEND_STOP.reset()
    spend.set_policy(spend.SPEND_POLICY_WINDOW, "oncotriage.mcp.server")
    _log(spend.describe_serving_cap())

    server = build_server()
    _log(f"serving {len(TOOL_SPECS)} tools on stdio: "
         f"{', '.join(name for name, _f, _d in TOOL_SPECS)}")
    server.run("stdio")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
