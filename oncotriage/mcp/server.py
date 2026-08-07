# MCP Server
############

"""Three tools over the matching pipeline, spoken to an MCP client on stdio.

    parse_fhir_bundle   a path to a FHIR bundle -> the parsed patient record
    match_patient       a path to a FHIR bundle -> ranked trials with verdicts
    lookup_trial        an NCT ID              -> the indexed trial record

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
from oncotriage.agent.graph import build_matching_graph, match_patient_to_trials
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


#------------------------------------------------------------------------------


SERVER_NAME = "oncotriage"

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
    print(f"[{SERVER_NAME}-mcp] {message}", file=sys.stderr, flush=True)


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
    """Parse a bundle at ``path``, or raise ``ValueError`` naming the file.

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
    """
    try:
        with _stdout_to_stderr():
            patient_data = parse_fhir_bundle(path)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(
            f"{path} could not be read as JSON ({type(exc).__name__}: {exc}). "
            f"bundle_path must point at a FHIR bundle JSON file.") from exc

    if not patient_data or not patient_data.get("patient_id"):
        raise ValueError(
            f"{path} parsed as JSON but yielded no patient record with an id. "
            f"It must be a FHIR Bundle containing a Patient resource.")

    return patient_data


def _patient_summary(patient_data) -> dict:
    """The small block both bundle tools return, so the two cannot disagree.

    ``deduplicate_by_display`` is the same call ``oncotriage/api/server.py``
    makes for the same counts. ECOG is surfaced because it is the one field the
    parser routes out of ``observations`` deliberately, and because its ``None``
    is meaningful: ECOG 0 is FULLY ACTIVE, the most eligible a patient can be, so
    it is reported as ``None`` and never defaulted to 0.
    """
    demographics = patient_data.get("demographics", {}) or {}
    ecog = patient_data.get("ecog_performance_status", {}) or {}
    return {
        "patient_id": patient_data.get("patient_id"),
        "age": demographics.get("age"),
        "sex": demographics.get("sex"),
        "condition_count": len(deduplicate_by_display(
            patient_data.get("conditions", []))),
        "medication_count": len(deduplicate_by_display(
            patient_data.get("medications", []))),
        "allergy_count": len(patient_data.get("allergies", []) or []),
        "ecog_performance_status": ecog.get("value"),
    }


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
    """Parse a FHIR bundle file into the pipeline's patient record."""
    path = _resolve_bundle_path(bundle_path)
    patient_data = _parse_bundle(path)

    return {
        "not_for_clinical_use": NOT_FOR_CLINICAL_USE,
        "status": "ok",
        "source_path": path,
        "patient_summary": _patient_summary(patient_data),
        # The whole parsed record, because that is what this tool IS. It is
        # large -- conditions, medications with historical status labels,
        # observations, procedures, allergies, genomic variants. Trimming it
        # here would be this file deciding what a parse means, which is the one
        # thing a thin wrapper must not do.
        "patient_data": patient_data,
    }


@_counted("match_patient")
def match_patient_tool(bundle_path: str) -> dict[str, Any]:
    """Run the full six-stage matching pipeline for one patient bundle."""
    path = _resolve_bundle_path(bundle_path)

    # THE GATE IS BEFORE THE PARSE AND BEFORE THE GRAPH, deliberately: the point
    # of refusing is to not spend a billed Stage 5 call on an index that cannot
    # answer, and everything below this line is on the way to that call.
    unavailable = _require_index("match_patient")
    if unavailable is not None:
        return unavailable

    patient_data = _parse_bundle(path)

    graph = get_graph()

    with _stdout_to_stderr():
        result = match_patient_to_trials(patient_data=patient_data, graph=graph)

    return {
        "not_for_clinical_use": NOT_FOR_CLINICAL_USE,
        "status": "ok",
        "source_path": path,
        "patient_summary": _patient_summary(patient_data),
        "result": result,
    }


@_counted("lookup_trial")
def lookup_trial_tool(nct_id: str) -> dict[str, Any]:
    """Fetch one indexed clinical trial by its NCT ID."""
    unavailable = _require_index("lookup_trial")
    if unavailable is not None:
        return unavailable

    with _stdout_to_stderr():
        found = lookup_trial(nct_id)

    if not found["found"]:
        # A RESULT, NOT AN ERROR, and the distinction is real: the index was
        # reachable and answered. `lookup_trial` raises rather than reporting
        # `found: False` when it could not ask, so this branch means the server
        # said there is no such trial HERE -- which is a fact about this index,
        # not about ClinicalTrials.gov, and the message says so.
        return {
            "not_for_clinical_use": NOT_FOR_CLINICAL_USE,
            "status": "not_found",
            "nct_id": found["nct_id"],
            "collection": found["collection"],
            "message": (
                f"{found['nct_id']} is not in the indexed trial collection "
                f"{found['collection']!r}. The index holds recruiting oncology "
                f"trials as of the last index build, so this means the trial "
                f"was not scraped into it -- not that the trial does not exist."),
        }

    return {
        "not_for_clinical_use": NOT_FOR_CLINICAL_USE,
        "status": "ok",
        "nct_id": found["nct_id"],
        "collection": found["collection"],
        "trial": found["trial"],
    }


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
        "status, procedures, allergies and genomic variants. Takes a PATH to a "
        "file on the machine running this server, not the bundle's contents. "
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
        "is empty, absent or unverifiable. " + NOT_FOR_CLINICAL_USE_SHORT,
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
