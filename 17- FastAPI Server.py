# FastAPI Server
################

"""
FastAPI REST API Server

Zero imports. Zero redundancy. All libraries, config, and pipeline logic
come from exec()-chaining scripts 01 → 02 → 03 → 07 → 13 → 14 into this
file's namespace — replicating Spyder's shared namespace exactly.

The only code in this file is:
    - The exec() chain (loads everything)
    - FastAPI app, models, and endpoints (thin API layer)
    - Lifespan handler (builds BM25 index + compiles LangGraph pipeline)

Endpoints:
    POST /match           — FHIR bundle as JSON body → matched trials
    POST /match/file      — FHIR bundle as file upload → matched trials
    GET  /health          — Health check + pipeline readiness
    GET  /pipeline/info   — Pipeline configuration and trial count

Run from terminal:
    cd ".../03- Code"
    cd "/Users/ramyalsaffar/Ramy/C.V..V/07- LLM Projects/03- Clinical Trial Patient Match/03- Code/"
    python "17- FastAPI Server.py"
"""


# ===========================================================================
# EXEC CHAIN: 01 → 02 → 03 → 07 → 13 → 14
# ===========================================================================
# exec() runs each script directly into this module's globals.
# After this block, every import, path, config constant, client,
# and function from all 5 scripts is available here.
# ===========================================================================
_code_dir = "/Users/ramyalsaffar/Ramy/C.V..V/07- LLM Projects/03- Clinical Trial Patient Match/03- Code/"

for _bootstrap in ("01- Imports.py", "02- Utility Functions.py"):
    with open(_code_dir + _bootstrap) as _fh:
        exec(_fh.read(), globals())

exec_chain(
    ["03- Config.py", "07- FHIR Parser.py", "13- LangGraph Agent.py", "14- Database Logger.py"],
    caller_file=_code_dir + "17- FastAPI Server.py",
    caller_globals=globals(),
    chain_label="01 → 02 → 03 → 07 → 13 → 14",
)

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
# APP
# ===========================================================================

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
    # parse_fhir_bundle expects a file path, so write to temp file
    with tempfile.NamedTemporaryFile(
        mode='w', suffix='.json', delete=False
    ) as tmp:
        json.dump(fhir_bundle_dict, tmp)
        tmp_path = tmp.name

    try:
        patient_data = parse_fhir_bundle(tmp_path)
    finally:
        os.unlink(tmp_path)

    if not patient_data or not patient_data.get('patient_id'):
        raise HTTPException(status_code=400, detail="Invalid FHIR bundle.")

    result = match_patient_to_trials(
        patient_data=patient_data,
        graph=graph
    )
    
    # Log to database
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


# ===========================================================================
# ENDPOINTS
# ===========================================================================

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
    return {
        "version": "2.0.0",
        "architecture": "LangGraph StateGraph + exec() chain",
        "stages": [
            "1. Query Expansion (Deterministic MeSH C04 hierarchy)",
            "2. Hybrid Retrieval (BM25 + Vector + RRF)",
            "3. Cross-Encoder ncbi/MedCPT",
            "4. Rule-Based Filtering",
            "5. GPT-4o Criterion-Level Evaluation",
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
        "trials_indexed": qdrant_client.get_collection(COLLECTION_NAME).points_count if qdrant_client else 0
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


# ===========================================================================
# MAIN
# ===========================================================================

if __name__ == "__main__":
    # Run from terminal — NOT Spyder.
    uvicorn.run(app, host="0.0.0.0", port=8000)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 19:11:06 2026

@author: ramyalsaffar
"""