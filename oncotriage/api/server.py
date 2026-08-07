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
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, Optional

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from oncotriage import __version__
from oncotriage.agent.deps import get_qdrant_client
from oncotriage.agent.graph import build_matching_graph, match_patient_to_trials
from oncotriage.agent.readiness import (
    INDEX_POPULATED,
    READY,
    probe_index,
    serving_readiness,
)
from oncotriage.config import (
    COLLECTION_NAME,
    CROSS_ENCODER_MODEL,
    EMBEDDING_MODEL,
    ENABLE_RATE_LIMITING,
    MATCHING_MODEL,
    MAX_GPT4O_RETRIES,
    MAX_TRIALS_FOR_EVALUATION,
    MEDCPT_SCORE_FLOOR,
    Project_Name,
    QUALITY_THRESHOLD_PERCENTILE,
    RATE_LIMIT,
    TOP_K_CANDIDATES,
    qdrant_endpoint_sources,
)
from oncotriage.fhir.parser import parse_fhir_bundle
from oncotriage.storage.database_logger import log_inference
from oncotriage.utils import deduplicate_by_display
from oncotriage.observability import console


#------------------------------------------------------------------------------


# ===========================================================================
# GLOBAL STATE (built at server startup)
# ===========================================================================

graph      = None


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
    report = serving_readiness()
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

    console.out("\n[Shutdown] Server stopping...")


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


# ===========================================================================
# HELPER
# ===========================================================================

def _run_matching_pipeline(fhir_bundle_dict):
    """
    Shared pipeline: FHIR bundle dict → MatchResponse.
    Used by both /match and /match/file.
    """

    start_time = time.time()

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
    log_inference(result, patient_data)

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
        report = serving_readiness()
        healthy = report["status"] == READY and graph is not None

        if not healthy:
            response.status_code = 503

        return {
            "status": "healthy" if healthy else "unhealthy",
            "pipeline_ready": graph is not None,
            "serving_ready": report["status"],
            "checks": report["checks"],
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
                "max_gpt4o_retries": MAX_GPT4O_RETRIES
            },
            "trials_indexed": trials_indexed,
            "trials_indexed_note": trials_indexed_note
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
