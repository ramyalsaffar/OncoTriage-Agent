# FastAPI Server
################

"""The REST API over the matching pipeline.

Moved out of ``17- FastAPI Server.py`` by item 20c, pass 3b. That file is now a
thin entry point: it re-exports ``app`` and keeps its ``uvicorn.run`` call.

Endpoints:
    POST /match           — FHIR bundle as JSON body → matched trials
    POST /match/file      — FHIR bundle as file upload → matched trials
    GET  /health          — Health check + pipeline readiness
    GET  /pipeline/info   — Pipeline configuration and trial count

TWO WAYS TO RUN IT, and both are supported:

    python "17- FastAPI Server.py"          the documented entry point
    uvicorn oncotriage.api.server:app       possible for the first time, because
                                            this is a module with an importable
                                            name

``docker-compose.yml`` runs a third form, ``uvicorn "17- FastAPI Server:app"``,
and it still works: ``importlib.import_module`` does not require a valid Python
identifier, only a file the path finder can locate, so a module name containing
a space and a leading digit imports fine as long as nobody writes an ``import``
STATEMENT for it. That was verified rather than assumed. It is why File 17 keeps
``app`` bound at module level.

WHAT IMPORTING THIS MODULE DOES
-------------------------------
It builds the FastAPI application object and the rate limiter, and prints the
rate-limiting banner. It does NOT compile the graph, open a client, load a
model, touch a database or read a file.

THE APP OBJECT AT MODULE LEVEL IS THE ONE DELIBERATE EXCEPTION to this package's
"importing a module does nothing" rule, and it is forced rather than chosen: the
ASGI convention is a ``module:attribute`` reference, so an application object has
to exist as an attribute before the server starts. ``create_app()`` is the
factory and ``app = create_app()`` is the single call, so a test that wants an
isolated application has one and does not have to reach around this module.

WHAT IS EXPENSIVE HAPPENS IN THE LIFESPAN HANDLER, on startup, exactly where
File 17 had it: ``build_matching_graph()`` runs in ``lifespan``, not at import.
That is what makes ``import oncotriage.api.server`` cheap enough to sit in File
47's per-module purity sweep beside every other module in the package.

PASS 20f-1: NO REQUEST TOUCHES THE FILESYSTEM ANY MORE. ``json``, ``os`` and
``tempfile`` were imported here so that ``_run_matching_pipeline`` could write
each incoming bundle to a temporary file for a parser that only took paths.
``oncotriage/fhir/parser.py:parse_fhir_bundle`` accepts a dict now, the round
trip is deleted, and ``os`` and ``tempfile`` went with it -- an import kept
after its only reader is exactly what ``tests/test_package_invariants.py`` check
2h reports. ``json`` stays: ``POST /match/file`` still decodes an upload with
it. The change reaches BOTH endpoints because the helper is shared, which is why
it was worth making at all: the endpoint that never had a file was paying for
one.

``os`` IS BACK, WITH A DIFFERENT READER, and the paragraph above is kept as the
record of why it left. The Stage 5 shutdown gate's signal handler announces
itself with ``os.write(2, ...)`` rather than a ``print`` or a log call, for the
signal-safety reason ``25- Batch Runner.py``'s handler already argues: a handler
that acquires a lock the main thread may hold is how a shutdown path deadlocks.
``tempfile`` did not come back, and no request touches the filesystem.

A SIGTERM BOUNDS WHAT STAGE 5 SPENDS
------------------------------------
This service was the last Stage 5 caller with no shutdown gate. The lifespan
handler arms a SIGNAL gate at startup and asks Stage 5 to stop at shutdown, so
`docker stop` costs one in-flight round rather than up to four. THE SIGNAL HALF
IS THE LOAD-BEARING ONE AND A LIFESPAN HOOK ALONE WOULD NOT HAVE WORKED -- see
THE STAGE 5 SHUTDOWN GATE below, which reads uvicorn's shutdown ordering rather
than assuming it.

THE ONE BEHAVIOUR CHANGE
------------------------
``log_inference`` is now serialized. This module calls it from
``loop.run_in_executor(...)``, i.e. from the event loop's thread pool, once per
in-flight request, and until pass 20c-3b there was NO LOCK ON THAT PATH -- the
only lock in the project was a monkeypatch inside "25- Batch Runner.py", which
protected the batch runner and nothing else. Two overlapping POST /match
requests were writing to one SQLite file through two connections with no
serialization. The lock moved into ``oncotriage/storage/database_logger.py``,
beside the writes it protects, so this module gets it without knowing it exists.
See the block above ``initialize_database`` there for what the race actually
cost, which is a lost row reported as a success.
"""

import asyncio
import json
import os
import signal
import time
import traceback
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, Optional

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from oncotriage import __version__
from oncotriage import config as _config
from oncotriage.agent.deps import get_qdrant_client
from oncotriage.agent.evaluation import (
    clear_stage5_shutdown,
    request_stage5_shutdown,
    stage5_shutdown_reason,
    stage5_shutdown_requested,
)
from oncotriage import spend
from oncotriage.agent.graph import build_matching_graph, match_patient_to_trials
from oncotriage.agent.readiness import (
    INDEX_POPULATED,
    READY,
    probe_index,
    serving_readiness,
)
from oncotriage.constants import NOT_FOR_CLINICAL_USE
from oncotriage.config import (
    COLLECTION_NAME,
    CROSS_ENCODER_MODEL,
    EMBEDDING_MODEL,
    ENABLE_RATE_LIMITING,
    MATCHING_CALL_MODE_GROUPED,
    MATCHING_CALL_MODE_PER_TRIAL,
    MATCHING_MODEL,
    MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS,
    MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS,
    MAX_LLM_CLASSIFIER_RETRIES,
    MAX_TRIALS_FOR_EVALUATION,
    MEDCPT_SCORE_FLOOR,
    OPENAI_SDK_MAX_RETRIES,
    Project_Name,
    QUALITY_THRESHOLD_PERCENTILE,
    RATE_LIMIT,
    TOP_K_CANDIDATES,
    matching_call_mode,
    matching_call_mode_pin,
    qdrant_endpoint_sources,
)
from oncotriage.fhir.parser import parse_fhir_bundle
from oncotriage.storage.database_logger import (
    log_inference,
    probe_serving_database,
)
from oncotriage.utils import deduplicate_by_display
from oncotriage.observability import console, get_logger

# The first structured logger in this module. Everything else here is still
# console output; see the logging pass's per-module worklist.
log = get_logger(__name__)


#------------------------------------------------------------------------------


# ===========================================================================
# GLOBAL STATE (built at server startup)
# ===========================================================================

graph      = None


# ===========================================================================
# THE STAGE 5 SHUTDOWN GATE, AND WHY A LIFESPAN HOOK ALONE CANNOT BE IT
# ===========================================================================
#
# THIS SERVICE WAS THE LAST CALLER WITHOUT A GATE. `25- Batch Runner.py` and
# `26- Ablation Study.py` both ask Stage 5 to stop issuing requests the instant
# a SIGTERM arrives, so an in-flight patient bounds its drain at ONE in-flight
# round instead of finishing its whole wave. This module had nothing, and
# `docker-compose.yml`'s own arithmetic block says what that costs: a `docker
# stop` on `fastapi` could SIGKILL it with up to three further full-price
# rounds still to be issued -- 2400 s of billable work against a 620 s grace,
# arm-independent (per-trial reaches it in four rounds, grouped in four further
# packed chunks).
#
# THE OBVIOUS IMPLEMENTATION IS A LIFESPAN SHUTDOWN HOOK AND IT DOES NOT WORK
# ON ITS OWN. Read uvicorn 0.40's `Server.shutdown()` rather than reasoning
# about it:
#
#     for server in self.servers: server.close()      # stop accepting
#     for connection in ...: connection.shutdown()
#     await asyncio.wait_for(self._wait_tasks_to_complete(), ...)   # <-- HERE
#     if not self.force_exit:
#         await self.lifespan.shutdown()              # <-- ONLY THEN
#
# `_wait_tasks_to_complete()` spins while `self.server_state.tasks` is
# non-empty, and an in-flight `POST /match` IS one of those tasks. So the
# lifespan shutdown event is delivered AFTER the drain this gate exists to
# bound -- it would fire once the last 2400-second patient had finished, which
# is exactly too late to have saved anything. Worse: if the operator sends a
# second SIGINT, `force_exit` is set and the lifespan shutdown NEVER RUNS AT
# ALL.
#
# SO THE LOAD-BEARING HALF IS A SIGNAL HANDLER, chained onto whatever is
# already installed, exactly as the batch runner's is -- the flag has to be set
# in the handler frame, at the moment the signal is delivered, because that is
# the only point that precedes the drain.
#
# THE LIFESPAN HOOK IS KEPT AND IS NOT DECORATION. It covers every shutdown
# that arrives WITHOUT a signal, which is a real set rather than a hypothetical
# one: an embedding host setting `Server.should_exit`, `uvicorn.Server.serve()`
# run with `install_signal_handlers=False`, and -- the case that makes this
# testable at all -- `starlette.testclient.TestClient`, which runs the
# application on an anyio portal THREAD, where `signal.signal` raises
# `ValueError` and no signal gate can be installed. Two halves, two windows,
# neither redundant.
#
# CHAINING IS MANDATORY, NOT POLITE. uvicorn installs `Server.handle_exit` for
# SIGINT/SIGTERM in `capture_signals()`, which wraps `serve()` -- so the
# lifespan STARTUP runs inside it and `signal.getsignal(SIGTERM)` is uvicorn's
# handler at the moment we look. Replacing it without calling it would set our
# flag and then leave the server running forever, which trades a bounded drain
# for a container that never stops at all. Every branch of the previous
# disposition is reproduced below, including the two that are not callables.
#
# IT IS SIGNAL-SAFE, and that decided the implementation rather than its
# placement, on `25- Batch Runner.py`'s footing: `request_stage5_shutdown`
# assigns two module globals and takes no lock, and the announcement is
# `os.write(2, ...)` rather than a `print` or a logging call, because a handler
# that acquires a lock the main thread may already hold is how a shutdown path
# deadlocks.
SHUTDOWN_GATE_SIGNALS = (signal.SIGINT, signal.SIGTERM)
"""The signals this module chains a Stage 5 gate onto. CLOSED.

The two `uvicorn.server.HANDLED_SIGNALS` names on POSIX, and deliberately not
`SIGBREAK`: this project's container and its documented entry point are POSIX,
`signal.SIGBREAK` does not exist on this platform, and naming a signal that
cannot be looked up would make importing this module raise on the machine it is
meant to run on."""


SHUTDOWN_GATE_DEGRADATIONS = Counter()
"""The signal gate could NOT be installed, or could not be chained cleanly.

Keyed by what happened, never by a value:

    ``not_main_thread``      ``signal.signal`` refused -- the application is
                             not running on the main thread. The TestClient's
                             ordinary state, and an embedding host's. The
                             LIFESPAN half still covers it.
    ``install:{Type}``       any other refusal from ``signal.signal``.
    ``unchainable:{signame}`` the previous disposition was ``None`` -- a
                             handler installed from C that Python cannot call
                             -- so the gate declined to install rather than
                             swallow it.

READ BY THIS FILE AND BY NOTHING ELSE, which is why it is exempted in
``tests/test_degradation_counter_readers.py`` rather than registered in
``oncotriage/degradation.py`` -- on ``oncotriage/mcp/server.py``'s
``TOOL_FAILURES`` precedent, and for its reason: a long-lived server has no run
end for a run-end report to attach to. ``shutdown_gate_report_lines()`` is the
reader; the startup banner prints it, so an operator sees at BRING-UP that the
gate is not armed rather than discovering it from a bill after a `docker stop`.

IT ALSO CANNOT BE REGISTERED. ``oncotriage/degradation.py`` binds the counter
objects of the modules it names, and naming this one would put FastAPI,
slowapi and pydantic into every batch run's import graph."""


# The dispositions this module replaced, so the lifespan shutdown can put them
# back. Keyed by signal number. EMPTY when the gate is not armed, which is what
# `shutdown_gate_armed()` reports.
_PREVIOUS_SIGNAL_HANDLERS = {}


def shutdown_gate_armed() -> bool:
    """Is the signal half of the gate installed right now? A plain read."""
    return bool(_PREVIOUS_SIGNAL_HANDLERS)


def shutdown_gate_report_lines():
    """Console lines describing the gate. The reader of the counter above.

    Always returns at least one line, so a bring-up log says which of the two
    halves is covering this process rather than being silent about a gate
    nobody armed. Silence and "armed" would otherwise look identical.
    """
    lines = []
    if shutdown_gate_armed():
        _names = ", ".join(sorted(s.name for s in _PREVIOUS_SIGNAL_HANDLERS))
        lines.append(f"[Startup]   OK   Stage 5 shutdown gate armed on {_names}")
    else:
        lines.append(
            "[Startup]   WARN Stage 5 shutdown gate NOT armed on any signal. "
            "A SIGTERM will not bound this process's in-flight Stage 5 drain; "
            "only a shutdown that runs the lifespan handler will.")
    for key, count in sorted(SHUTDOWN_GATE_DEGRADATIONS.items()):
        lines.append(f"[Startup]        shutdown-gate degradation {key}: {count}")
    return lines


def _chain_to(previous, signum, frame) -> None:
    """Re-deliver ``signum`` to whatever handled it before this module did.

    Every disposition ``signal.getsignal`` can return is handled, and the two
    that are not callables are the ones a naive ``previous(signum, frame)``
    would crash on:

        a callable   uvicorn's ``Server.handle_exit`` in the deployed case --
                     called directly, which is what keeps `docker stop`
                     working.
        SIG_IGN      the signal was being ignored, so ignoring it is the
                     faithful reproduction. Nothing to do.
        SIG_DFL      restore the default and re-raise, so a SIGTERM still
                     terminates the process the way it would have. Doing
                     nothing here would convert a fatal signal into a no-op,
                     which is the failure mode "chaining is mandatory" names.
        None         the handler was installed outside Python and cannot be
                     called from it. UNREACHABLE from the install path, which
                     refuses to arm in that case and counts it -- kept here so
                     this function is total rather than relying on its one
                     caller's guard.
    """
    if callable(previous):
        previous(signum, frame)
        return
    if previous == signal.SIG_IGN or previous is None:
        return
    # SIG_DFL, and anything else this platform can hand back.
    signal.signal(signum, signal.SIG_DFL)
    signal.raise_signal(signum)


def _install_shutdown_signal_gate() -> None:
    """Chain a Stage 5 shutdown request onto SIGINT and SIGTERM.

    Idempotent: a second call with the gate already armed does nothing, so a
    lifespan that runs twice in one process cannot capture its OWN handler as
    "the previous one" and build a chain that calls itself.
    """
    if _PREVIOUS_SIGNAL_HANDLERS:
        return

    for _sig in SHUTDOWN_GATE_SIGNALS:
        try:
            previous = signal.getsignal(_sig)
        except (ValueError, OSError) as exc:          # pragma: no cover
            SHUTDOWN_GATE_DEGRADATIONS[f"install:{type(exc).__name__}"] += 1
            continue

        if previous is None:
            # A handler Python cannot call. Installing over it would REPLACE a
            # working shutdown with one that cannot chain, so the gate declines
            # -- a bounded drain is not worth a server that will not stop.
            SHUTDOWN_GATE_DEGRADATIONS[f"unchainable:{_sig.name}"] += 1
            continue

        def _gate(signum, frame, _previous=previous):
            # DEFAULT-ARGUMENT BINDING, not a closure over `previous`: the loop
            # rebinds that name, so a closure would leave BOTH handlers
            # chaining to whichever disposition the loop saw last.
            request_stage5_shutdown(
                f"{signal.Signals(signum).name} (signal {signum}) at the API")
            os.write(2, (
                f"\n[{signal.Signals(signum).name}] Shutdown requested "
                f"(signal {signum}). Stage 5 will issue no further request; "
                f"calls already in flight finish and their patients fail "
                f"honestly.\n").encode("utf-8", "replace"))
            _chain_to(_previous, signum, frame)

        try:
            signal.signal(_sig, _gate)
        except ValueError as exc:
            # THE DOCUMENTED NON-MAIN-THREAD REFUSAL, and the ordinary state
            # under a TestClient. Recorded rather than swallowed; the lifespan
            # half still covers this process.
            #
            # IT RETURNS RATHER THAN CONTINUING, because the cause is
            # THREAD-WIDE: `signal.signal` refuses every signal off the main
            # thread, so trying the next one buys a second identical refusal
            # and a second log line saying the same thing.
            #
            # AND IT UNWINDS WHAT IT INSTALLED FIRST. The first draft cleared
            # the dict and returned -- which, if a LATER signal had failed
            # after an earlier one succeeded, would have left a live handler
            # installed with the disposition it replaced FORGOTTEN, so
            # `_remove_shutdown_signal_gate` could never put it back. That is
            # unreachable for THIS cause and reachable for the `OSError` one
            # below, and a partial state nobody can undo is not worth leaving
            # to depend on which exception arrived.
            SHUTDOWN_GATE_DEGRADATIONS["not_main_thread"] += 1
            log.warning("the Stage 5 shutdown signal gate could not be "
                        "installed; a signal will not bound this process's "
                        "in-flight Stage 5 drain",
                        event="shutdown_gate_not_installed",
                        reason="not_main_thread", degraded=True,
                        error_type=type(exc).__name__, error_message=str(exc))
            _remove_shutdown_signal_gate()
            return
        except OSError as exc:                        # pragma: no cover
            # PER-SIGNAL, so it CONTINUES: a platform refusing one signal says
            # nothing about the next, and a gate armed on SIGTERM alone still
            # bounds a `docker stop`. `shutdown_gate_report_lines()` names
            # WHICH signals are armed, so a partial arm is reported as one
            # rather than read as a whole one.
            SHUTDOWN_GATE_DEGRADATIONS[f"install:{type(exc).__name__}"] += 1
            log.warning("the Stage 5 shutdown signal gate could not be "
                        "installed", event="shutdown_gate_not_installed",
                        reason=f"install:{type(exc).__name__}", degraded=True,
                        error_type=type(exc).__name__, error_message=str(exc))
            continue

        _PREVIOUS_SIGNAL_HANDLERS[_sig] = (previous, _gate)


def _remove_shutdown_signal_gate() -> None:
    """Put back the dispositions this module replaced, if they are still ours.

    DEFINED BELOW ITS FIRST CALLER AND THAT IS FINE: the call is inside a
    function body, resolved at call time against the module globals, and this
    name is bound before any of them runs.

    ONLY IF THEY ARE STILL OURS. uvicorn's own ``capture_signals()`` restores
    the pre-uvicorn handler in its ``finally``, which runs after this, so in the
    deployed case this is a courtesy. It matters for a host that outlives the
    application -- a test process, an embedder starting a second app -- where
    leaving a handler behind that references this module's flag would make a
    later, unrelated SIGTERM set it.
    """
    for _sig, (previous, installed) in list(_PREVIOUS_SIGNAL_HANDLERS.items()):
        try:
            # BY IDENTITY, never by ``__name__``: several modules in one
            # process may define a nested handler called ``_gate``, and
            # restoring over somebody else's would be worse than leaving ours
            # in place.
            if signal.getsignal(_sig) is installed:
                signal.signal(_sig, previous)
        except (ValueError, OSError) as exc:
            # THE ENTRY IS KEPT WHEN THE RESTORE FAILS, and that is the
            # difference between this dictionary describing the process and
            # merely describing an intention. A failed restore means the gate's
            # handler is STILL LIVE and its predecessor is still needed, so
            # popping the entry would throw away the only record of what to put
            # back -- and would make ``shutdown_gate_armed()`` answer False
            # about a process that has this module's handler installed.
            SHUTDOWN_GATE_DEGRADATIONS[f"restore:{type(exc).__name__}"] += 1
            continue
        _PREVIOUS_SIGNAL_HANDLERS.pop(_sig, None)


# ===========================================================================
# LIFESPAN (startup / shutdown)
# ===========================================================================

@asynccontextmanager
async def lifespan(_app):
    """Compile LangGraph pipeline at startup. BM25 is Qdrant-native — no pre-build needed."""
    global graph

    console.out("\n" + "="*60)
    console.out(f"{Project_Name} — Starting...")
    console.out("="*60 + "\n")

    # ── THE STAGE 5 SHUTDOWN GATE ────────────────────────────────────────
    # ARMED BEFORE THE GRAPH IS COMPILED, so a SIGTERM arriving during a slow
    # startup is already gated. Compiling the graph is the longest thing this
    # handler does, and an operator restarting a stack does not wait for it.
    #
    # THE FLAG IS CLEARED FIRST, on `oncotriage/batch/runner.py:main()`'s
    # footing -- it clears the same flag beside `clear_write_ledger()` and
    # `run_fingerprint.clear_cache()`, because module state that survives into
    # the next run describes the wrong run. Here the cost of NOT clearing is
    # sharper than a wrong description: a second application lifespan in one
    # process (a test, an embedder) would inherit the first one's shutdown and
    # every request it served would fail with no call having been issued.
    #
    # IT IS SAFE IN THE DEPLOYED CASE because a uvicorn process runs the
    # startup half of the lifespan exactly once and never re-enters it after a
    # shutdown; nothing else in an API process shares this flag.
    #
    # A STARTUP THAT RAISES BELOW THIS LINE LEAVES THE HANDLER INSTALLED, and
    # that is a stated limit rather than an oversight. The statements after the
    # `yield` do not run when the body never reaches it, so `build_matching_
    # graph()` raising would leave this module's handler in place with nothing
    # to remove it. It is NOT wrapped in a `try`/`finally` for two reasons, in
    # order of weight: uvicorn's own `capture_signals()` restores the
    # pre-uvicorn disposition in ITS `finally` whatever happens here, so the
    # deployed case is covered by the host; and the process a failed startup
    # produces is one that is about to exit anyway. The reachable case is an
    # embedding host that catches the startup error and carries on -- there,
    # `_install_shutdown_signal_gate()` is idempotent and
    # `_remove_shutdown_signal_gate()` is public.
    clear_stage5_shutdown()
    _install_shutdown_signal_gate()
    for _line in shutdown_gate_report_lines():
        console.out(_line)

    # ── THE SPEND POLICY ─────────────────────────────────────────────────
    #
    # A SERVER MUST NOT RUN UNDER THE CAMPAIGN CAP, AND UNTIL THIS LINE IT
    # DID. `oncotriage/spend.py`'s ledger is charged by Stage 5 and by Stage
    # 2's dense channel whoever the caller is, and this process writes no
    # `runs` row -- so nothing seeded that ledger, nothing reset it, and
    # `config.SPEND_CAP_USD` compared against a MONOTONE total gave this server
    # no brake at all until it had spent a whole campaign's budget by itself,
    # and then declined every request it would ever serve. The remedy an
    # operator reaches for is a restart, which empties the ledger and hands the
    # process a fresh unbounded budget: the brake off exactly when it worked.
    # `spend.SPEND_POLICIES` carries the full argument.
    #
    # THE LEDGER IS RESET WITH IT, on the same footing as the shutdown flag
    # above: a second application lifespan in one process (a test, an embedder)
    # would otherwise inherit the first one's window and decline requests for
    # money this server did not spend.
    #
    # ARMED BEFORE THE GRAPH IS COMPILED for the shutdown gate's reason: the
    # first request can arrive the instant startup completes, and a policy
    # installed after it would serve that request under the wrong limit.
    spend.SPEND_LEDGER.reset()
    spend.SPEND_STOP.reset()
    spend.set_policy(spend.SPEND_POLICY_WINDOW, "oncotriage.api.server")
    console.out(spend.describe_serving_cap())

    console.out("[Startup] Compiling LangGraph pipeline...")
    graph = build_matching_graph()

    # ── SERVING READINESS ────────────────────────────────────────────────
    # "HEALTHY" USED TO MEAN "uvicorn IS ANSWERING", and the gap between that
    # and "can serve a request" was the whole of the pipeline's data
    # dependencies. Measured on a clean `docker compose down -v && up`: all six
    # containers reported healthy, `GET /health` returned 200 with
    # `"pipeline_ready": true`, and the first `POST /match` died inside Stage 1
    # because /app/data/mesh/ was empty. Nothing between the two said so.
    #
    # This runs the probes ONCE, at startup, and prints the result. It does NOT
    # raise: a container that dies here leaves only a log, while one that starts
    # and answers /health with the reason can be asked what is wrong over HTTP,
    # by `docker inspect`, and by the compose healthcheck — which is what turns
    # the failure into a red container instead of a green unusable one.
    #
    # It is re-run per request by /health (see there), so populating the missing
    # dependency makes the stack go green on its own without a restart.
    console.out("[Startup] Checking serving readiness...")
    # THE SAME COMPOSITION AS /health, and it has to be: a startup banner that
    # probed two dependencies while /health probed three would print OK for a
    # server /health is about to call unhealthy, and an operator reading the
    # bring-up log would look for the third fault everywhere except the place
    # the log told them was fine.
    report = serving_readiness(extra_checks=[probe_serving_database()])
    for _check in report["checks"]:
        console.out(f"[Startup]   {'OK  ' if _check['ok'] else 'FAIL'} "
              f"{_check['name']}: {_check['detail']}")
    if report["status"] == READY:
        console.out(f"\n[Ready] Pipeline compiled and serviceable "
              f"(BM25 is Qdrant-native, no pre-build needed)\n")
    else:
        console.out(f"\n[NOT READY] The pipeline compiled but CANNOT serve a match "
              f"request. GET /health reports 503 until the failures above are "
              f"fixed; no request is refused on the strength of this, so a "
              f"POST /match will still run and fail at the stage that needs "
              f"the missing dependency.\n")

    yield

    # ── SHUTDOWN ─────────────────────────────────────────────────────────
    # THE SECOND HALF OF THE GATE, AND IT IS NOT THE LOAD-BEARING ONE. See
    # THE STAGE 5 SHUTDOWN GATE above for why: uvicorn delivers this event
    # AFTER `_wait_tasks_to_complete()`, so on the signal path the flag was
    # already set in the handler frame and this call is the idempotent
    # no-op `request_stage5_shutdown` is documented to be. What it covers is
    # every shutdown that arrives WITHOUT a signal -- a host setting
    # `Server.should_exit`, `install_signal_handlers=False`, and the
    # TestClient, which runs on a portal thread where no signal gate can be
    # installed at all.
    #
    # WHICH HALF ACTUALLY FIRED IS PRINTED rather than inferred. The reason
    # recorded with the FIRST request wins, so a reason naming a signal says
    # the handler ran and a reason naming this handler says it did not --
    # which is the difference between a drain that was bounded and one that
    # was not, and an operator reading a bill needs it.
    _already = stage5_shutdown_requested()
    request_stage5_shutdown("the API lifespan shutdown event")
    console.out("\n[Shutdown] Server stopping...")
    console.out(f"[Shutdown] Stage 5 shutdown requested: "
                f"{stage5_shutdown_reason()!r} "
                f"({'signal gate' if _already else 'lifespan handler'}; "
                f"no further Stage 5 request will be issued).")

    _remove_shutdown_signal_gate()


# ===========================================================================
# MODELS
# ===========================================================================

class MatchRequest(BaseModel):
    fhir_bundle: Dict


class PatientSummary(BaseModel):
    patient_id: str
    age: Optional[int] = None
    sex: Optional[str] = None
    condition_count: int = 0
    medication_count: int = 0
    allergy_count: int = 0


class MatchResponse(BaseModel):
    patient_summary: PatientSummary
    result: Dict
    processing_time_seconds: float

    # THE FRAMING IS DECLARED ON THE MODEL, NOT AT THE CONSTRUCTION SITE, and
    # that is the nearest thing this file has to "one shared response-
    # construction path". There is exactly one place a MatchResponse is built
    # (`_run_matching_pipeline`) and two endpoints reach it, so a default here
    # is a single declaration that BOTH POST /match and POST /match/file carry,
    # and neither endpoint can omit it by forgetting a keyword.
    #
    # THERE IS NO SHARED PATH WITH GET /pipeline/info, which returns a bare dict
    # from its own handler and shares no model, no helper and no serializer with
    # these two. That endpoint therefore repeats the FIELD NAME and imports the
    # same constant; the string itself is still typed once, in
    # oncotriage/constants.py. Making the two share a construction path would
    # mean inventing an envelope model for a response whose only other field is
    # already a nested dict -- a response-shape change far larger than the one
    # this pass is here to make.
    #
    # WHY A DEFAULT AND NOT A COMPUTED FIELD: `response_model=MatchResponse`
    # makes FastAPI serialize exactly the declared fields, so the default is
    # what a client receives, and a test can assert the value is the constant
    # by identity of source rather than by string comparison alone.
    #
    # THE KEY NAME IS THE MCP SERVER'S. oncotriage/mcp/server.py has answered
    # with "not_for_clinical_use" since the MCP pass; two surfaces spelling one
    # fact two ways is the drift this project removes elsewhere by giving a
    # thing one name.
    not_for_clinical_use: str = NOT_FOR_CLINICAL_USE


# ===========================================================================
# HELPER
# ===========================================================================

BUDGET_DECLINED_STATUS = 503
"""The status a request declined for budget answers with.

**503 AND NOT 429, AND THE DISTINCTION IS ABOUT WHOSE FAULT IT IS.** 429 means
the CLIENT has sent too many requests and is the shape a per-client rate limit
takes -- which this server already has, at `RATE_LIMIT`, and which is a
different mechanism with a different remedy (that client backs off). A budget is
a SERVER-side resource that is temporarily exhausted for everyone; a
well-behaved client that sent one request meets it. 503 says "I cannot serve
this now, try later", which is exactly true, is what `Retry-After` is defined
against, and is the same code `/health` already uses for a dependency this
server is missing.

IT IS A NAMED CONSTANT because two endpoints answer with it and a test asserts
on it; a literal in three places is the drift this project removes elsewhere by
giving a thing one name.
"""


def _serving_cap_or_none():
    """The serving cap for the health body, or None when it cannot be read.

    ``/health`` MUST NOT RAISE OVER A MISCONFIGURED CAP. It is the endpoint an
    operator asks FIRST when something is wrong, and a 500 here would remove
    the report about every OTHER dependency in order to complain about one
    constant -- `/pipeline/info` routing its trial count through the index
    probe for the identical reason. A cap that cannot be read reads as `null`,
    beside `declining`, which is False in that state because `cap_exceeded()`
    swallows the same error and declines nothing.
    """
    try:
        return spend.serving_spend_cap()
    except spend.SpendCapConfigurationError:
        return None


def _budget_declined(exc):
    """Turn a ``spend.SpendLimitReached`` into the HTTP answer. Pure.

    ``Retry-After`` IS COMPUTED, NOT GUESSED. `spend.seconds_until_under_cap()`
    derives the instant the rolling window falls back under its cap from the
    events actually in it, so a server one request over its budget tells a
    client to come back in seconds rather than in an hour. It is OMITTED rather
    than faked when the answer is unknown -- the call-ceiling limit does not
    heal with time, and a `Retry-After` on a condition that will not clear is
    worse than none.

    THE HEADER IS AN INTEGER STRING OF SECONDS, which is one of the two forms
    RFC 9110 defines and the one every client library parses.

    THE DETAIL NAMES THE LIMIT AND NOT THE BALANCE. `exc` carries the spend and
    the cap in its message; this deliberately does not forward them, because a
    served response is a public surface and how much this deployment has spent
    is not the requesting client's business. The operator reads the figures in
    the log line `require_budget` already wrote and in `GET /health`.
    """
    headers = {}
    _wait = spend.seconds_until_under_cap(spend.SPEND_SOURCE_STAGE5)
    if _wait is not None:
        headers["Retry-After"] = str(max(1, int(_wait)))
    return HTTPException(
        status_code=BUDGET_DECLINED_STATUS,
        detail=("This server has reached its spend limit and is not issuing "
                "billed requests. NO MATCHING WAS PERFORMED -- this is not a "
                "finding of zero eligible trials. The limit is a rolling "
                "window and clears on its own; retry later."),
        headers=headers or None)


def _run_matching_pipeline(fhir_bundle_dict):
    """
    Shared pipeline: FHIR bundle dict → MatchResponse.
    Used by both /match and /match/file.
    """

    start_time = time.time()

    # ── THE SPEND GATE, ABOVE EVERYTHING ─────────────────────────────────
    #
    # FIRST, BEFORE THE VALIDATION AND BEFORE THE PARSE, so a declined request
    # costs this server nothing at all -- not a bundle parse, not a Qdrant
    # round trip, not a thread of the event loop's pool held for a minute.
    #
    # IT IS A SEPARATE GATE FROM STAGE 5's AND BOTH ARE NEEDED. Stage 5's
    # declines the individual billed call and fails the patient HONESTLY, which
    # is right for a batch run: the row is written and the checkpoint resumes
    # it. That shape is wrong for a request -- the client gets HTTP 200 and a
    # result whose `error` field names a budget, which no HTTP client branches
    # on -- so this turns the same fact into a status code, and Stage 5's
    # remains as the backstop for a window that fills while a request is
    # already in flight.
    #
    # IT DOES NOT LATCH. `require_budget` derives that from the policy, and
    # this process installed `serving_window` in its lifespan: the quantity
    # falls as the window rolls, so the NEXT request asks the ledger again
    # rather than meeting a latch set minutes ago. See `spend.SPEND_POLICIES`.
    spend.require_budget(spend.SPEND_SOURCE_STAGE5,
                         "a served /match request")

    # ── FHIR structure validation ──────────────────────────────────────
    if not isinstance(fhir_bundle_dict, dict):
        raise HTTPException(status_code=422, detail="FHIR bundle must be a JSON object, not a list or primitive.")

    resource_type = fhir_bundle_dict.get("resourceType", "")
    if resource_type != "Bundle":
        raise HTTPException(
            status_code=422,
            detail=f"Expected resourceType 'Bundle', got '{resource_type or 'missing'}'."
        )

    entries = fhir_bundle_dict.get("entry", [])
    if not isinstance(entries, list) or len(entries) == 0:
        raise HTTPException(status_code=422, detail="FHIR Bundle has no entries.")

    # Check for at least one Patient resource
    has_patient = any(
        e.get("resource", {}).get("resourceType") == "Patient"
        for e in entries
        if isinstance(e, dict)
    )
    if not has_patient:
        raise HTTPException(status_code=422, detail="FHIR Bundle contains no Patient resource.")

    # ── Parse and run pipeline ─────────────────────────────────────────
    #
    # THE TEMPORARY FILE IS GONE (pass 20f-1). This used to be
    #
    #     with tempfile.NamedTemporaryFile(mode='w', suffix='.json',
    #                                      delete=False) as tmp:
    #         json.dump(fhir_bundle_dict, tmp)
    #         tmp_path = tmp.name
    #     try:
    #         patient_data = parse_fhir_bundle(tmp_path)
    #     finally:
    #         os.unlink(tmp_path)
    #
    # because parse_fhir_bundle took a file path and nothing else. THIS
    # FUNCTION IS SHARED BY BOTH ENDPOINTS, so the round trip was paid by
    # POST /match too -- a request that arrived as JSON and never came near a
    # file still caused a serialize, a write, a read, a decode and a delete,
    # once per request, on the event loop's thread pool where several are in
    # flight at once. The parser accepts a dict now; the file route it kept is
    # unchanged for load_all_patients, the batch runner and the fixture
    # harnesses.
    #
    # The dict is handed over as it is, not copied: the parser reads it and
    # never writes it, which is asserted rather than assumed in
    # tests/test_fhir_parser_dict_input.py.
    patient_data = parse_fhir_bundle(fhir_bundle_dict)

    if not patient_data or not patient_data.get('patient_id'):
        raise HTTPException(status_code=400, detail="Invalid FHIR bundle.")

    result = match_patient_to_trials(
        patient_data=patient_data,
        graph=graph
    )

    # Log to database.
    #
    # NO LOCK HERE, and that is the change of pass 20c-3b rather than an
    # omission: this function runs on the event loop's thread pool, so several
    # copies of it are in flight whenever several requests are, and the lock
    # that serializes them now lives inside log_inference itself. It used to be
    # a monkeypatch in "25- Batch Runner.py", which meant this call site -- the
    # only OTHER concurrent writer in the project -- had none at all.
    # THE OUTCOME IS READ, THE RESPONSE SHAPE IS NOT CHANGED (the
    # write-durability pass). log_inference returns an InferenceWriteResult --
    # still the database path, still `== db_path`, now carrying `.ok`. Before
    # this pass a lost row and a stored row were the same return value, so this
    # endpoint answered 200 with a well-formed body either way and nothing
    # anywhere recorded that the row was gone.
    #
    # WHAT IS DELIBERATELY NOT DONE HERE:
    #
    #   - the response body is NOT widened TO CARRY THIS OUTCOME. MatchResponse
    #     is this server's public contract and adding a field to it is a
    #     versioning decision, not an observability one. The client asked for
    #     matches and the matches are correct; what failed is this server's own
    #     record-keeping. (MatchResponse HAS since gained one field --
    #     `not_for_clinical_use` -- in the pass that attached the clinical-use
    #     framing. That was a deliberate contract change, argued at the model;
    #     it does not licence adding a write-outcome field here.)
    #   - the request is NOT failed. A 500 would discard a complete, paid-for
    #     pipeline result over a logging fault, which is the exact trade
    #     log_inference's broad handler exists to refuse.
    #
    # So it is recorded, at ERROR, with the patient id that is the join key back
    # into the table it is missing from. The operator-facing consequence is that
    # a server losing rows is now greppable in `docker logs`, where before it
    # printed one "non-critical" console line and no severity at all.
    write_result = log_inference(result, patient_data)
    if getattr(write_result, "ok", None) is False:
        log.error("inference row was NOT stored for a served request",
                  event="inference_write_lost",
                  patient_id=str(patient_data.get("patient_id", "")),
                  error_message=str(getattr(write_result, "error", "")),
                  attempts=getattr(write_result, "attempts", None),
                  db_path=str(write_result))

    elapsed = time.time() - start_time

    demographics = patient_data.get('demographics', {})

    return MatchResponse(
        patient_summary=PatientSummary(
            patient_id=patient_data['patient_id'],
            age=demographics.get('age'),
            sex=demographics.get('sex'),
            condition_count=len(deduplicate_by_display(patient_data.get("conditions", []))),
            medication_count=len(deduplicate_by_display(patient_data.get("medications", []))),
            allergy_count=len(patient_data.get("allergies", []))
        ),
        result=result,
        processing_time_seconds=round(elapsed, 3)
    )


#------------------------------------------------------------------------------


# ===========================================================================
# APP
# ===========================================================================

def create_app():
    """Build the FastAPI application, its rate limiter and its routes.

    A FACTORY, with ``app = create_app()`` below as the single call. File 17 had
    the app, the limiter and every route as top-level statements, which is the
    normal FastAPI shape and is fine in a script; in a module it means there is
    exactly one application per process and no way for a caller to build an
    isolated one. The factory costs one indentation level and buys that back.

    IT OPENS NOTHING. FastAPI() and Limiter() are object construction. The route
    handlers are registered, not called. The graph is compiled in ``lifespan``,
    on startup — the expensive thing stays where File 17 had it.
    """
    # THE VERSION IS oncotriage.__version__ AND THERE IS NOW ONE OF IT
    # (pass 20f-2). Three declarations disagreed: this line said "2.0.0",
    # /pipeline/info repeated "2.0.0" as a second hand-maintained literal, and
    # pyproject.toml declared version = "0.1.0" -- so `pip show oncotriage` and
    # the API contradicted each other by two major versions, and the follow-up
    # recorded in /pipeline/info did not mention the third site because nobody
    # had looked at the packaging metadata.
    #
    # WHY 2.0.0 AND NOT 0.1.0, since one of the two had to lose. 2.0.0 is what
    # the API has been TELLING CLIENTS for its whole life; 0.1.0 was written
    # when pyproject.toml's own description called the package "the importable
    # foundation: settings, paths, config, utils", which was true for pass
    # 20c-1 and stopped being true when the last conversion pass landed. Moving
    # the packaging metadata up is invisible to every consumer. Moving the API
    # down would announce a two-major-version regression over HTTP to anyone
    # who checks, for a number that never described a smaller API.
    #
    # WHY ONE NUMBER FOR BOTH, stated so the next release does not have to guess:
    # this project ships ONE artifact. The package, the container and the HTTP
    # surface are cut from the same commit and there is no version of the API
    # that is not a version of the package. If the HTTP contract ever needs to
    # move independently -- a v2 route family served beside a v1 -- that is a
    # SECOND named constant with its own argument, not a re-divergence of this
    # one.
    #
    # oncotriage/__init__.py is the source, read as a plain module attribute, so
    # this stays free of the filesystem: importlib.metadata.version() would read
    # the installed dist-info, and `app = create_app()` runs at import, which
    # tests/test_package_invariants.py section 2 imports under a trapped
    # builtins.open. pyproject.toml takes the same attribute through
    # [tool.setuptools.dynamic], which setuptools reads from the AST at BUILD
    # time -- so the wheel, `pip show`, the FastAPI app and /pipeline/info all
    # carry one string that is typed once.
    app = FastAPI(
        title=Project_Name,
        description="Clinical trial patient matching — LangGraph + hybrid RAG",
        version=__version__,
        lifespan=lifespan
    )

    # Rate limiting toggle
    limiter = Limiter(key_func=get_remote_address, enabled=ENABLE_RATE_LIMITING)

    app.state.limiter = limiter

    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    console.out(f"[Rate Limiting] {'ENABLED' if ENABLE_RATE_LIMITING else 'DISABLED'} {RATE_LIMIT}")

    # =======================================================================
    # ENDPOINTS
    # =======================================================================

    @app.get("/health")
    async def health_check(response: Response):
        """Health check and pipeline readiness. 503 when a dependency is missing.

        THE STATUS CODE IS THE POINT. docker-compose.yml probes this endpoint
        with `curl -f`, which fails on any 4xx/5xx, so a 503 here is what makes
        `docker compose ps` say `unhealthy` instead of green. Before this, a
        stack with an empty /app/data/mesh/ and an empty Qdrant collection
        reported six healthy containers and could not answer a single request.

        IT RE-RUNS THE PROBES rather than reporting what startup found, and the
        cost is bounded: `serving_readiness()` calls `deps.get_mesh_filter()`,
        which is cached by the seam after the first success, and
        `readiness.probe_index()`, which caches only a POPULATED verdict — so a
        healthy server pays one cached lookup and no network call, and an
        unhealthy one pays a `collection_exists` + `count` per probe interval.
        That asymmetry is deliberate: it is what lets a stack recover on its own
        the moment the operator populates the index, with no restart, and the
        only process paying for it is one that is already failing.

        ``pipeline_ready`` is KEPT and still means what it always meant — the
        graph compiled. It is now one field among several rather than the whole
        answer, because it was the field that reported true while the server was
        unusable.
        """
        # THE THIRD CHECK IS THE INFERENCE DATABASE, and it closes a hole in
        # which nothing anywhere reported a fault.
        #
        # MEASURED: point this server at a database written by a NEWER schema
        # era and every POST /match runs the whole pipeline, makes its billed
        # Stage 5 calls, returns HTTP 200 with a complete and correct body, and
        # stores nothing. `assert_database_is_compatible` refuses the file
        # correctly; `_write_inference_row`'s handler catches the refusal like
        # any other logging fault -- which is right, a paid result must not be
        # discarded over a write -- and hands back `ok=False`. Before this,
        # /health probed the MeSH lookups and the index, knew nothing about the
        # database, and stayed GREEN while the server billed for every request
        # and kept no record of any of them.
        #
        # The same is true of a data volume that failed to mount and of a
        # mistyped ONCOTRIAGE_INFERENCES_DB; the probe names all three.
        #
        # THE PROBE IS COMPOSED HERE RATHER THAN CALLED FROM INSIDE
        # serving_readiness(), because `oncotriage/agent/retrieval.py` imports
        # that module and a storage import inside it would put the storage
        # layer into the AGENT's import graph -- the coupling pass 20c-2c moved
        # `_resolve_primary_cancer` out of the storage layer to remove. This
        # file already imports both.
        #
        # IT IS RE-PROBED PER REQUEST like the other two, so archiving the
        # newer-era file makes the stack go green on its own with no restart.
        # The cost is one PRAGMA read on a local file per probe interval.
        report = serving_readiness(extra_checks=[probe_serving_database()])
        healthy = report["status"] == READY and graph is not None

        if not healthy:
            response.status_code = 503

        # ── THE BUDGET IS REPORTED AND DELIBERATELY DOES NOT DECIDE
        #    `healthy` ────────────────────────────────────────────────────
        #
        # THE OBVIOUS VERSION IS ACTIVELY HARMFUL AND THAT IS WHY THIS IS A
        # SEPARATE FIELD. docker-compose.yml probes this endpoint with
        # `curl -f`, so folding a budget stop into `healthy` would make the
        # container UNHEALTHY, and an unhealthy container is RESTARTED -- which
        # empties the rolling window and hands the process a fresh budget. The
        # health check would become the mechanism that defeats the brake, on a
        # loop, and the only symptom would be a server that restarts every hour
        # and never declines anything.
        #
        # SO A BUDGET STOP IS A TEMPORARY, SELF-HEALING, PER-REQUEST condition
        # answered at `/match` with a 503 and a `Retry-After`, and this
        # endpoint's job is to let an operator SEE it. The three checks above
        # are the opposite kind of fact -- a missing dependency does not heal
        # and a restart is a reasonable response to it.
        _spend_over = spend.cap_exceeded(spend.SPEND_SOURCE_STAGE5)
        return {
            "status": "healthy" if healthy else "unhealthy",
            "pipeline_ready": graph is not None,
            "serving_ready": report["status"],
            "checks": report["checks"],
            "spend": {
                "policy": spend.policy(),
                # ROUNDED TO CENTS, on the response's own footing rather than
                # the ledger's: this is a served field and six decimal places
                # of a rolling window is precision nobody can act on.
                "window_usd": round(spend.SPEND_LEDGER.window_spend(), 4),
                "window_seconds": getattr(
                    _config, "SERVING_SPEND_WINDOW_SECONDS", None),
                "cap_usd": _serving_cap_or_none(),
                "declining": _spend_over,
                "retry_after_seconds": (
                    None if not _spend_over
                    else spend.seconds_until_under_cap(
                        spend.SPEND_SOURCE_STAGE5)),
            },
            "timestamp": datetime.now().isoformat()
        }

    @app.get("/pipeline/info")
    async def pipeline_info():
        """Pipeline configuration and statistics."""
        # The Qdrant client is reached through the AGENT's seam, not through
        # oncotriage.config, and deliberately: this endpoint reports on the
        # index the AGENT will query, so a stub installed for a test must be
        # what it describes. File 17 read a bare `qdrant_client` out of the
        # shared exec namespace, which is the pattern pass 20c-2c replaced.
        #
        # get_qdrant_client() BUILDS on first call, which is why it is called
        # here inside the handler and not at import: this module must import
        # without opening a client.
        # ===================================================================
        # TWO OF THESE STRINGS WERE STALE AND ONE OF THEM CONTRADICTED THE
        # FIELD THREE LINES BELOW IT (pass 20g)
        # ===================================================================
        #
        # Measured against a live container on 2026-08-06 rather than read off
        # the source: GET /pipeline/info returned
        #
        #     "architecture": "LangGraph StateGraph + exec() chain"
        #     "5. GPT-4o Criterion-Level Evaluation"
        #     "matching_model": "gpt-5.6-terra"
        #
        # The first names a mechanism pass 20e DELETED -- there is no exec
        # chain, `exec_chain` itself is gone from oncotriage/utils.py, and
        # tests/test_package_invariants.py section 1c fails the build if one
        # comes back. The second and third are the same response disagreeing
        # with itself about which model Stage 5 calls.
        #
        # WHY THE FIX IS DERIVATION AND NOT A RETYPED STRING. "GPT-4o" was
        # correct when it was written and rotted when MATCHING_MODEL changed,
        # because nothing connected the two. Retyping "gpt-5.6-terra" here buys
        # one correct release and re-arms the same trap for the next model
        # change. The stage line is interpolated from the constant the stage
        # actually calls, so the two cannot disagree again. Same reasoning as
        # item 38's `pipeline_consistency`, which replaced the literals 100 and
        # 30 with the config values that produce the columns.
        #
        # THE OTHER FIELDS WERE CHECKED, NOT ASSUMED, and they are current:
        # stage 1 is deterministic and walks MeSH C04 (agent/mesh_expansion.py,
        # no LLM call); stage 2 is BM25 + dense + RRF (agent/retrieval.py);
        # stage 4 is the rule-based filter (agent/filtering.py); stage 6 is
        # node_finalize (agent/terminal.py). The seven `config` values are read
        # from oncotriage.config, so they cannot be stale by construction --
        # which is exactly what the two literals above were not.
        #
        # `version` IS NO LONGER HAND-MAINTAINED, and pass 20g's follow-up here
        # is closed. It was "2.0.0" typed a second time beside the identical
        # literal in create_app() above, and pyproject.toml declared a THIRD
        # value, 0.1.0, which pass 20g did not mention because it had only
        # compared the two inside this file. All three are now
        # oncotriage.__version__; the release decision that picked 2.0.0 over
        # 0.1.0 is argued in full at create_app().
        #
        # WHAT A READER OF THIS ENDPOINT SHOULD SEE: the same string that
        # `pip show oncotriage` prints, that /openapi.json reports as
        # info.version, and that the image was built from -- one number for one
        # artifact. It is NOT an independent HTTP-contract version, and this
        # response does not claim to carry one.
        #
        # `collection_name` reports COLLECTION_NAME, which is the ALIAS and not
        # the collection -- deliberately, because it is under `config` and the
        # alias is what is configured. `trials_indexed` below resolves through
        # that alias, so the count is the collection the alias points at.
        qdrant_client = get_qdrant_client()

        # A COUNT OF ZERO MUST NOT BE INVENTED. This was
        # `...points_count if qdrant_client else 0`, and 0 is a real, plausible
        # answer -- "the index is empty" -- for a branch that means "there was
        # no client to ask". get_qdrant_client() raises or returns a client and
        # never returns None, so the branch is unreachable through the server;
        # it IS reachable through deps.set_override(QDRANT_CLIENT, None), which
        # is how a harness redirects this seam. Either way an unanswerable
        # question is reported as unanswered, not as zero.
        #
        # THE COUNT NOW COMES THROUGH readiness.probe_index AND NOT THROUGH A
        # BARE get_collection, and the reason is a real regression this pass
        # would otherwise have introduced. `get_collection(COLLECTION_NAME)`
        # RAISES UnexpectedResponse 404 when no such collection or alias exists
        # -- measured -- which is precisely the state a clean
        # `docker compose down -v && up` leaves the compose `qdrant` service in
        # now that the container uses it. This endpoint is the first thing an
        # operator asks in that state, and it would have answered with a 500 and
        # a traceback about a missing collection instead of describing the
        # pipeline. `probe_index` raises nothing and returns a named state, so
        # the diagnostic survives the failure it is being used to diagnose.
        if qdrant_client:
            _verdict = probe_index(client=qdrant_client)
            trials_indexed = _verdict["points"]        # None unless counted
            trials_indexed_note = (
                None if _verdict["state"] == INDEX_POPULATED
                else f"index state: {_verdict['state']}"
                     + (f" ({_verdict['error']})" if _verdict["error"] else "")
                     + f"; endpoint {_verdict['endpoint']}")
        else:
            trials_indexed = None
            trials_indexed_note = (
                "no Qdrant client: oncotriage.agent.deps.get_qdrant_client() "
                "returned a falsy object, so the index could not be asked. This "
                "is not an empty index.")

        return {
            "version": __version__,
            "architecture": "LangGraph StateGraph over the oncotriage package",
            "stages": [
                "1. Query Expansion (Deterministic MeSH C04 hierarchy)",
                "2. Hybrid Retrieval (BM25 + Vector + RRF)",
                # Interpolated for the reason the stage-5 line above it already
                # was: pass 20g derived that one from MATCHING_MODEL after
                # finding it still said "GPT-4o", and this line was the same
                # shape of literal one row up -- correct today, and connected to
                # nothing that would move it when the checkpoint changed.
                f"3. Cross-Encoder Rerank ({CROSS_ENCODER_MODEL})",
                "4. Rule-Based Filtering",
                f"5. Criterion-Level Evaluation ({MATCHING_MODEL})",
                "6. Final Ranking"
            ],
            "config": {
                "collection_name": COLLECTION_NAME,
                # WHICH SERVER, AND WHO SAID SO. Until this pass there was only
                # one possible Qdrant endpoint -- whatever the .env named -- so
                # a response naming the collection named the index. There are
                # two now (the .env, and ONCOTRIAGE_QDRANT_URL), and a report
                # that says "trial_criteria, 12067 points" without saying WHERE
                # cannot distinguish the cloud index from a local one that was
                # populated to a different depth. This is a response-shape
                # change and it is the one this pass owes: it makes an existing
                # field unambiguous rather than adding a new fact.
                #
                # qdrant_endpoint_sources() NEVER RETURNS THE KEY, only the name
                # of what supplied it, so this endpoint cannot leak a credential
                # however it is called.
                "qdrant_endpoint": qdrant_endpoint_sources(),
                "embedding_model": EMBEDDING_MODEL,
                # NOT ADDING "cross_encoder_model" HERE, deliberately. It would
                # be the third model identity in a block that already carries
                # two, which reads like an omission being corrected -- but the
                # stage-3 line above already reports it, from the same constant,
                # so the only thing a second field buys is a response that says
                # one fact twice. Adding a key is also a response-shape change,
                # and this pass's job in this file was to stop it carrying three
                # version numbers, not to widen what it answers.
                "matching_model": MATCHING_MODEL,
                "top_k_candidates": TOP_K_CANDIDATES,
                # RENAMED, not retyped. This key was "rerank_threshold" and
                # carried RERANK_SCORE_THRESHOLD = -10, a floor on the FUSED
                # RRF score -- which runs about 0.01 .. 0.06, so the value it
                # reported could never fire. The constant is deleted and the
                # Stage 4 absolute knob is a floor on the MedCPT cross-encoder
                # score instead. Keeping the old key over the new quantity
                # would leave one name covering two different measurements,
                # which is the defect this change exists to remove; a client
                # reading "rerank_threshold" gets a KeyError and looks, rather
                # than silently reading a MedCPT score as an RRF one.
                "medcpt_score_floor": MEDCPT_SCORE_FLOOR,
                "quality_threshold_percentile": QUALITY_THRESHOLD_PERCENTILE,
                "max_trials_for_evaluation": MAX_TRIALS_FOR_EVALUATION,
                "max_llm_classifier_retries": MAX_LLM_CLASSIFIER_RETRIES,
                # ===========================================================
                # WHICH STAGE 5 ARM, AND THE CONSTANTS THAT GOVERN IT
                # ===========================================================
                #
                # THE SINGLE LARGEST LEVER ON WHAT A PATIENT COSTS WAS ABSENT
                # FROM THE ONE ENDPOINT THAT DESCRIBES THE PIPELINE. Per-trial
                # mode sends one billed request per patient-trial pair behind a
                # cache warmup -- up to MAX_TRIALS_FOR_EVALUATION + 1 requests
                # where grouped sends between one and
                # MATCHING_MAX_INPUT_PACKED_CHUNKS -- and a program integrating
                # against this server could read every other field here, and
                # `max_trials_for_evaluation` beside it, without being able to
                # tell which arm produces the numbers it is about to be billed
                # for.
                #
                # A NESTED BLOCK, NOT FIVE FLAT KEYS, on `qdrant_endpoint`'s
                # precedent one field up: three of these are meaningless in
                # grouped mode, and flattened among the always-applicable
                # tunables they would read as though they always applied.
                #
                # THE DEFAULT AND WHAT IS IN FORCE ARE BOTH REPORTED, AND THEY
                # CAN DIFFER. `config.matching_call_mode()` resolves pin-then-
                # constant, and a pin is process-global: fixture_capture.py and
                # fixture_replay.py install one, and so does a test. A response
                # reporting only the constant would describe a process that is
                # doing something else, which is precisely the class of defect
                # the `architecture` and stage-5 strings above were fixed for.
                # `pin` is None in every ordinary serving process and names the
                # override when there is one.
                #
                # DERIVED, NEVER RETYPED. `configured_default` is computed from
                # MATCHING_PER_TRIAL_CALLS_ENABLED through the same two-member
                # vocabulary `config.matching_call_mode()` uses, so this
                # endpoint cannot disagree with what the pipeline does or with
                # what `inferences.matching_call_mode` stores.
                "call_mode": {
                    # READ OFF THE MODULE AT CALL TIME, NOT THROUGH THE
                    # FROM-IMPORT BINDING ABOVE. A `from X import NAME` binds
                    # the VALUE at import, and `create_app()` runs at import --
                    # so a process that later rebinds
                    # `config.MATCHING_PER_TRIAL_CALLS_ENABLED` on the module
                    # would get a response reporting a default that disagrees
                    # with `in_force` below, with `pin` null and therefore
                    # nothing to explain the disagreement. That is not
                    # hypothetical: tests/test_agent_stage5_per_trial_calls.py
                    # and tests/test_agent_per_trial_trial_cap.py both do it.
                    #
                    # It is the patch-point lesson this project has now paid
                    # for twice -- the Bedrock pass's `matching_provider`
                    # (a from-import that reached nothing when the flag moved)
                    # and tests/test_agent_rrf_config_ownership.py's whole
                    # subject. `matching_call_mode()` already resolves live, so
                    # reading the default any other way is what lets the two
                    # halves of one field disagree.
                    "configured_default": (
                        MATCHING_CALL_MODE_PER_TRIAL
                        if _config.MATCHING_PER_TRIAL_CALLS_ENABLED
                        else MATCHING_CALL_MODE_GROUPED),
                    "in_force": matching_call_mode(),
                    "pin": matching_call_mode_pin(),
                    "per_trial_max_parallel_calls":
                        MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS,
                    "per_trial_warmup_max_output_tokens":
                        MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS,
                    # THERE IS NO DEDICATED WARMUP RETRY BUDGET AND THE FIELD
                    # SAYS SO RATHER THAN BEING OMITTED. A reader looking for
                    # one and not finding a key cannot tell "this endpoint does
                    # not report it" from "there isn't one"; an explicit null
                    # settles it. The warmup's coverage is the two budgets that
                    # already exist: the SDK's transport retries below, and
                    # `max_llm_classifier_retries` one level up, which re-enters
                    # the whole node through route_after_llm_classifier.
                    "per_trial_warmup_dedicated_retries": None,
                    "per_trial_warmup_sdk_max_retries": OPENAI_SDK_MAX_RETRIES,
                },
            },
            "trials_indexed": trials_indexed,
            "trials_indexed_note": trials_indexed_note,
            # THE SAME FIELD NAME AND THE SAME CONSTANT AS MatchResponse, and
            # this is the one place in this file the name is written twice. It
            # is written twice because there is nothing to share: this handler
            # returns a bare dict and MatchResponse is a pydantic model, so
            # there is no envelope, no serializer and no helper between them.
            # The STRING is still typed exactly once, in
            # oncotriage/constants.py; what is repeated is a key, which
            # tests/test_clinical_use_framing.py asserts by AST is bound to the
            # imported name and not to a retyped literal.
            #
            # WHY THIS ENDPOINT AT ALL, when it returns no verdicts: it is the
            # first thing a program integrating against this server reads, and
            # it is what /docs shows an operator. A framing that appears only on
            # the response carrying the verdicts is a framing the integrator
            # meets after they have already written the client.
            "not_for_clinical_use": NOT_FOR_CLINICAL_USE,
        }

    @app.post("/match", response_model=MatchResponse)

    @limiter.limit(RATE_LIMIT)

    async def match_patient_endpoint(body: MatchRequest, request: Request):

        """Match a patient to clinical trials via JSON body."""
        if graph is None:
            raise HTTPException(status_code=503, detail="Pipeline not ready.")

        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _run_matching_pipeline, body.fhir_bundle)
        except spend.SpendLimitReached as e:
            raise _budget_declined(e)
        except HTTPException:
            raise
        except Exception as e:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Pipeline error: {e}")

    @app.post("/match/file", response_model=MatchResponse)

    @limiter.limit(RATE_LIMIT)

    async def match_patient_file(request: Request, file: UploadFile = File(...)):

        """Match a patient to clinical trials via file upload."""
        if graph is None:
            raise HTTPException(status_code=503, detail="Pipeline not ready.")

        if not file.filename.endswith('.json'):
            raise HTTPException(status_code=400, detail="Only .json files accepted.")

        try:
            content = await file.read()
            bundle = json.loads(content)
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _run_matching_pipeline, bundle)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON file.")
        except spend.SpendLimitReached as e:
            raise _budget_declined(e)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Pipeline error: {e}")

    return app


# THE ASGI ENTRY POINT. `uvicorn oncotriage.api.server:app`,
# `uvicorn "17- FastAPI Server:app"` (what docker-compose.yml uses) and
# `python "17- FastAPI Server.py"` all end up here, at the same object.
app = create_app()


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 19:11:06 2026

@author: ramyalsaffar
"""
