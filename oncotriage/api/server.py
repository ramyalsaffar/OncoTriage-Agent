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

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from oncotriage.agent.deps import get_qdrant_client
from oncotriage.agent.graph import build_matching_graph, match_patient_to_trials
from oncotriage.config import (
    COLLECTION_NAME,
    EMBEDDING_MODEL,
    ENABLE_RATE_LIMITING,
    MATCHING_MODEL,
    MAX_GPT4O_RETRIES,
    MAX_TRIALS_FOR_EVALUATION,
    Project_Name,
    RATE_LIMIT,
    RERANK_SCORE_THRESHOLD,
    TOP_K_CANDIDATES,
)
from oncotriage.fhir.parser import parse_fhir_bundle
from oncotriage.storage.database_logger import log_inference
from oncotriage.utils import deduplicate_by_display


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

    print("\n" + "="*60)
    print(f"{Project_Name} — Starting...")
    print("="*60 + "\n")

    print("[Startup] Compiling LangGraph pipeline...")
    graph = build_matching_graph()

    print(f"\n[Ready] Pipeline compiled (BM25 is Qdrant-native, no pre-build needed)\n")

    yield

    print("\n[Shutdown] Server stopping...")


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
    app = FastAPI(
        title=Project_Name,
        description="Clinical trial patient matching — LangGraph + hybrid RAG",
        version="2.0.0",
        lifespan=lifespan
    )

    # Rate limiting toggle
    limiter = Limiter(key_func=get_remote_address, enabled=ENABLE_RATE_LIMITING)

    app.state.limiter = limiter

    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    print(f"[Rate Limiting] {'ENABLED' if ENABLE_RATE_LIMITING else 'DISABLED'} {RATE_LIMIT}")

    # =======================================================================
    # ENDPOINTS
    # =======================================================================

    @app.get("/health")
    async def health_check():
        """Health check and pipeline readiness."""
        return {
            "status": "healthy",
            "pipeline_ready": graph is not None,
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
        # `version` STAYS "2.0.0" and it is hand-maintained. It matches the
        # FastAPI application version in create_app() above and nothing derives
        # either from the other; it is recorded as a follow-up rather than
        # invented here, because choosing where an API version number lives is a
        # release decision, not a staleness fix.
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
        if qdrant_client:
            trials_indexed = qdrant_client.get_collection(
                COLLECTION_NAME).points_count
            trials_indexed_note = None
        else:
            trials_indexed = None
            trials_indexed_note = (
                "no Qdrant client: oncotriage.agent.deps.get_qdrant_client() "
                "returned a falsy object, so the index could not be asked. This "
                "is not an empty index.")

        return {
            "version": "2.0.0",
            "architecture": "LangGraph StateGraph over the oncotriage package",
            "stages": [
                "1. Query Expansion (Deterministic MeSH C04 hierarchy)",
                "2. Hybrid Retrieval (BM25 + Vector + RRF)",
                "3. Cross-Encoder Rerank (ncbi/MedCPT-Cross-Encoder)",
                "4. Rule-Based Filtering",
                f"5. Criterion-Level Evaluation ({MATCHING_MODEL})",
                "6. Final Ranking"
            ],
            "config": {
                "collection_name": COLLECTION_NAME,
                "embedding_model": EMBEDDING_MODEL,
                "matching_model": MATCHING_MODEL,
                "top_k_candidates": TOP_K_CANDIDATES,
                "rerank_threshold": RERANK_SCORE_THRESHOLD,
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
