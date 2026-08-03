# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**OncoTriage Agent** — matches oncology patients (Synthea FHIR bundles) to recruiting ClinicalTrials.gov trials using a 6-stage LangGraph pipeline over hybrid BM25 + vector RAG on Qdrant, with GPT-4o for criterion-level eligibility evaluation.

## The exec() chain — read this before touching any file

This codebase does **not** use Python imports between its own files. Every script is a numbered, space-containing filename (`13- LangGraph Agent.py`) that is `exec()`'d into the caller's `globals()`, replicating a Spyder shared-namespace workflow. Consequences:

- **Nothing is importable.** `import` of a project file is impossible (spaces, leading digits). Never add one.
- A function used in file N may be *defined* in file 1, 2, 3, 8, 9, or 10 with no import statement at its use site. To find a definition, grep across all `*.py` — e.g. `load_env_keys()` is called in `03- Config.py` but defined in `02- Utility Functions.py`; `PRICING_CONFIG` is defined in 03 and consumed in 02.
- Every entry-point file begins with the same bootstrap: raw `exec()` of `01- Imports.py` and `02- Utility Functions.py` (needed because `exec_chain` itself lives in 02), then `exec_chain([...])` for the rest.
- `exec_chain` (`02- Utility Functions.py`) sets `__name__ = "_exec_chain_"` while exec'ing, so `if __name__ == "__main__":` blocks in chained files do **not** fire. That is the mechanism that lets a file be both a runnable script and a library.
- **Do not double-load.** `13- LangGraph Agent.py` already chains 08, 09, 10 — callers of 13 (17, 25, 26) must not list them again. See the warning comment in `26- Ablation Study.py`.
- `_code_dir` is a **hardcoded absolute macOS path** at the top of most entry-point files. Docker mounts the code at `/app`, and `01- Imports.py` switches all data paths on `IS_DOCKER`, but `_code_dir` itself is not switched.

Adding a new script means: copy the bootstrap block, list its deps in `exec_chain`, and put any new shared library/constant in `01- Imports.py` / `03- Config.py` rather than importing locally.

## Running things

All commands run from `03- Code/`. Filenames contain spaces — always quote them.

```bash
# Pipeline services
python "17- FastAPI Server.py"                       # API on :8000 (/docs)
streamlit run "21- Streamlit Dashboard.py"           # dashboard on :8501
python "25- Batch Runner.py"                         # full-corpus run, no HTTP, checkpointed

# Data + index build (one-time / weekly)
python "04- FHIR Generate Data.py"                   # Synthea JAR -> ~22k patients
python "05- FHIR Clean Data.py"                      # in-place DELETE of non-cancer patients
python "11- RAG Trial Indexer.py" --mode staging     # staging + atomic alias swap (default)
python "11- RAG Trial Indexer.py" --mode direct      # rebuilds in place, causes downtime
python "12- RAG Trial Indexer Validator.py"          # exit 1 on any CRITICAL check failure

# Evaluation / monitoring
python "26- Ablation Study.py" --sample-size 30 --configs full_pipeline no_mesh_filter
python "26- Ablation Study.py" --summary-only        # report from existing ablation_results.db
python "27- Ablation Analysis.py"                    # tables + figures from ablation_results.db
python "20- Drift Detection.py"                      # KS / PSI / z-score vs 30-day baseline

# Docker (all five services)
docker compose build && docker compose up -d
docker compose logs -f fastapi
```

**Tests** are not pytest — `18-` and `19-` are procedural scripts hitting a *live* server on `localhost:8000`; start `17-` in another terminal first. `19-` slices `fhir_files[410:412]` for a smoke run; widen that slice to go broader.

To exercise the graph directly without the API, set `RUN_TEST_ON_EXECUTE = True` near the bottom of `13- LangGraph Agent.py` and run it as `__main__`.

## Layout outside this repo

Only `03- Code/` is version-controlled. Sibling directories under the project root are resolved by **glob prefix** in `01- Imports.py` (`glob.glob(main_path + "/*Data/")[0]`), so directories can be renumbered but not renamed past their suffix:

| Path var | Sibling dir | Holds |
|---|---|---|
| `data_fhir_path` | `02- Data/…/Patients/fhir/` | Synthea patient bundles |
| `inferences_path` | `02- Data/03- Inferences Storage/inferences.db` | SQLite log (gitignored) |
| `data_MeSH_path` | `02- Data/…/MeSH/` | MeSH C04 + UMLS crosswalk JSONs |
| `keys_path` | `05- Keys/.env` | `OPENAI_API_KEY`, `QDRANT_URL`, `QDRANT_API_KEY` |
| `checkpoint_path` | `08- Checkpoint/` | batch runner resume state |
| `requirements_path` | `07- Requirements/requirements.txt` | pip deps (copied into Dockerfile) |

`docker-compose.yml` mounts `.env` from a mix of `../04- Keys/` and `../05- Keys/`; only `05- Keys/` exists.

## Pipeline architecture

`13- LangGraph Agent.py` (~3.4k lines) is the core. `build_matching_graph()` wires a `StateGraph` over `TrialMatchState` (TypedDict, ~line 111):

1. **`node_query_expansion`** — deterministic, no LLM. Uses the cancer registry (08) + MeSH filter (09) to expand the patient's primary diagnosis into query terms.
2. **`node_hybrid_retrieval`** — Qdrant-native BM25 (FastEmbed sparse, `BM25_RETRIEVAL_SIZE=75`) + dense `text-embedding-3-small` (`VECTOR_RETRIEVAL_SIZE=100`), fused by RRF into `RRF_POOL_SIZE`. Falls back to BM25-only if vector search fails.
3. **`node_cross_encoder_rerank`** — MedCPT cross-encoder, multi-query with RRF across queries, stable argsort for determinism. `RERANK_SCORE_THRESHOLD = -10`.
4. **`node_rule_based_filter`** — MeSH site relevance, cancer stage ordinal, histology, age, sex + a dynamic quality threshold and cost cap (`MAX_TRIALS_FOR_EVALUATION = 15`).
5. **`node_gpt4o_evaluation`** — one GPT-4o call producing per-criterion verdicts; JSON-parse failures loop back up to `MAX_GPT4O_RETRIES = 3`.
6. **`node_finalize`** — splits eligible/not_eligible, normalizes labels.

Conditional edges route to `node_no_candidates` when a stage empties the pool, and any exception lands in `node_error_handler`, which still emits a well-formed result. `match_patient_to_trials(patient_data, graph)` is the public entry point; it stamps `qdrant_collection` and `patient_data_hash` onto the result.

**Ablation flags** ride in the state dict (`state["ablation_flags"]`) and are read at three points (nodes 2, 3, 4). `26- Ablation Study.py` toggles one stage per config; nothing else forks the pipeline.

### Supporting modules

- **`08- Cancer Code Registry.py`** — primary-cancer detection: SNOMED exact → ICD-10-CM 2024 exact (`icd10-cm` package, handles `C34.10` and `C3410`) → display-term morphology fallback. Metastatic/secondary terms are rejected at every layer. Never assume the first condition in a FHIR bundle is the cancer.
- **`09- MeSH Cancer Site Relevance Filter.py`** — MeSH C04 tree ancestry match. Patient side maps SNOMED→CUI→MeSH via UMLS `MRCONSO`, falling back to fuzzy descriptor matching. Trial side is a direct lookup (ClinicalTrials.gov conditions *are* MeSH terms). **Conservative by design: unmappable on either side ⇒ KEEP.**
- **`10- Structured Eligibility Extractor.py`** — index-time, rule-based, zero-LLM extraction of stage requirements into a structured dict, so stage matching in node 4 is an integer comparison. Unknown ⇒ `None` ⇒ trial passes.
- **`07- FHIR Parser.py`** — `parse_fhir_bundle(path)` takes a **file path**, not a dict (the API writes a temp file to bridge this). Historical medications are deliberately retained with status labels so prior-treatment criteria are evaluable.

### Index lifecycle (Qdrant)

`COLLECTION_NAME = "trial_criteria"` is an **alias**, never a collection. `11-` builds into a timestamped staging collection (`trial_criteria_20260226_140159`), creates payload indexes, then `swap_alias_atomic()` in a single `update_collection_aliases` call (zero downtime), then `cleanup_old_collections()`. Use `resolve_qdrant_collection()` (file 02) whenever the *real* collection name is needed for logging — it retries and falls back gracefully.

`23- Airflow DAG.py` writes the `trial_refresh_weekly` DAG (Sundays 02:00) into `{airflow_path}/dags/`; `22-` initializes the Airflow DB and `24-` starts/stops/triggers via the REST API v2. The DAG file is generated as a string, so DAG logic edits go in `23-`, not in the `dags/` output.

## Persistence and observability

`14- Database Logger.py` opens the SQLite connection at load time and creates three tables: `inferences` (per-patient funnel counts, per-stage timings, token counts, cost), `trial_matches` (per-trial verdicts), `drift_metrics`. `log_inference(result, patient_data)` is called by the API and batch runner. `16-` is a scratch query script; `15-` wipes all tables and is guarded by `Flag = False` — leave it False.

`21- Streamlit Dashboard.py` (~5.2k lines) reads only from `inferences.db` via `@st.cache_data(ttl=60)`.

Costs come from `get_model_cost()` against `PRICING_CONFIG` in `03- Config.py`, dated `last_updated` — an unknown model logs a warning and returns 0.0 rather than raising.

**Degradation record.** A run that lost a retrieval channel, fell back to the un-expanded query, or skipped the cancer site filter must be identifiable from its stored row alone. The relevant state keys are written by the stage that owns them, carried to all three terminal nodes by `_pipeline_provenance()` (file 13), and logged to `inferences.retrieval_channels` / `retrieval_degraded` / `retrieval_trials_lost` / `query_expansion_path` / `mesh_filter_applied` / `mesh_filter_skip_reason`. **NULL in these columns means the stage never reported and is not the same as a clean value** — never default them to 0 in a new writer or fold NULL into 0 in a reader. Stage 5's Section 2 is conditional on `mesh_filter_applied`: it only asserts to the model that disease relevance was confirmed when the filter actually ran. `Exception and Fallback Audit.md` inventories every `except` and fallback in the codebase with a verdict and the open items.

## Conventions

- **All tunables live in `03- Config.py`.** Retrieval sizes, thresholds, temperatures (both 0 for determinism), rate limiting, drift windows, batch runner settings. Don't scatter magic numbers into node bodies.
- `ENABLE_RATE_LIMITING = False` by default so batch evaluation isn't throttled; flip it for production.
- Long local runs wrap in `with CaffeinateSession("label"):` to stop macOS sleeping.
- Qdrant calls use the shared `qdrant_retry` tenacity decorator (file 02) for connect/timeout/`UnexpectedResponse`.
- Determinism is a deliberate property of the pipeline (temperature 0, stable argsort, seeded sampling with `RESAMPLE_SEED = 42`). Preserve it when editing ranking or sampling code.
- Files carry a Spyder-generated `#!/usr/bin/env python3` + creation-date docstring footer at the **bottom**; append new code above it.

## Important Rules
Tunable values go in the config module. Facts about an external
standard (MeSH tree numbers, LOINC codes, FHIR resource names)
stay inline as named constants.

Never catch an exception without either re-raising it or
recording it in a counter. Silent recovery is the specific
defect this project exists to remove.

If you add a fallback path, log which path was taken.

Data and keys live outside this folder. Never write an
absolute path.

When you finish, state which parts you verified by running
something, and which parts you only read.