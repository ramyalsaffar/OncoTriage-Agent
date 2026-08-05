# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**OncoTriage Agent** — matches oncology patients (Synthea FHIR bundles) to recruiting ClinicalTrials.gov trials using a 6-stage LangGraph pipeline over hybrid BM25 + vector RAG on Qdrant, with GPT-4o for criterion-level eligibility evaluation.

## The exec() chain and the `oncotriage` package — read this before touching any file

Files 04 to 46 are numbered, space-containing filenames (`25- Batch Runner.py`) that are `exec()`'d into the caller's `globals()`, replicating a Spyder shared-namespace workflow. **Those 43 files are still not importable** — spaces and leading digits — and nothing in this pass changed that.

Files 01, 02 and 03 are different as of item 20c. They are now **re-export shims over a real package**:

Files 01, 02, 03 (pass 20c-1), 08, 09, 10 (pass 20c-2a), 07, 14 (pass 20c-2b) and 13 (pass 20c-2c) are shims:

| Module | Holds | Imports |
|---|---|---|
| `oncotriage/settings.py` | `ENV_*` names, `resolve_*_path()` | nothing from the project |
| `oncotriage/paths.py` | `IS_DOCKER`, `_glob_one`, every path variable (**lazy**), `load_env_keys()` | `settings` |
| `oncotriage/constants.py` | `SYSTEM_KEY_ABSENT` / `SYSTEM_KEY_UNRECOGNIZED` | nothing at all |
| `oncotriage/config.py` | every tunable, `PRICING_CONFIG`, `DATA_SNAPSHOT_DATE`, lazy client factories | `paths` |
| `oncotriage/utils.py` | `get_model_cost`, `qdrant_retry`, `resolve_qdrant_collection`, `parse_partial_date`, `get_age_reference_date`, `exec_chain`, `CaffeinateSession` | `config` |
| `oncotriage/registries/cancer_code_registry.py` | File 08 whole — `CancerCodeRegistry`, `OncologyLabRegistry`, `load_registry`, `load_lab_registry` | `constants` |
| `oncotriage/registries/mesh.py` | File 09's filter half — `MeSHCancerFilter`, `load_mesh_filter`, `specific_cancer_trees` | `paths` |
| `oncotriage/registries/mesh_crosswalk_build.py` | File 09's five offline builders | nothing from the project |
| `oncotriage/extraction/negation.py` | `_is_negated` + the three constants only it reads | nothing from the project |
| `oncotriage/extraction/stage.py` | File 10 to line 698 — stage requirements | `extraction.negation` |
| `oncotriage/extraction/histology.py` | File 10 from line 699 — histology tags | `extraction.negation` |
| `oncotriage/fhir/parser.py` | File 07 whole — `parse_fhir_bundle`, `load_all_patients`, the four corpus Counters | `constants`, `utils` |
| `oncotriage/storage/database_logger.py` | File 14 whole — the three-table schema, `initialize_database`, `log_inference` | `paths`, `config`, `utils`, `registries.primary_cancer` |
| `oncotriage/registries/primary_cancer.py` | `_resolve_primary_cancer` — which condition is THE cancer | `registries.cancer_code_registry` |
| `oncotriage/agent/deps.py` | **the seam**: every client, model and registry, lazily resolved and overridable | `config`, `registries.*` |
| `oncotriage/agent/state.py` | `TrialMatchState`, the channel / expansion-path / MeSH-filter vocabularies | nothing from the project |
| `oncotriage/agent/text.py` | `tokenize_for_bm25` | nothing from the project |
| `oncotriage/agent/models.py` | `medcpt_score_pairs`, `score_pairs`, `get_embedding` | `config`, `agent.deps` |
| `oncotriage/agent/patient.py` | `compute_patient_hash`, `_create_patient_summary`, the relevance classifiers | `agent.deps`, `agent.state`, `config`, `utils` |
| `oncotriage/agent/mesh_expansion.py` | Stage 1's MeSH walk | `registries.mesh` |
| `oncotriage/agent/retrieval.py` | Stages 1–3 + `build_bm25_index_from_qdrant` | `agent.{deps,models,mesh_expansion,patient,state,text}`, `config`, `registries.mesh`, `utils` |
| `oncotriage/agent/filtering.py` | Stage 4 | `agent.{deps,retrieval,state}`, `config`, `extraction.*` |
| `oncotriage/agent/evaluation.py` | Stage 5 | `agent.{deps,patient,state}`, `config`, `utils` |
| `oncotriage/agent/terminal.py` | the three terminal nodes + `_pipeline_provenance` | `agent.state`, `registries.primary_cancer`, `utils` |
| `oncotriage/agent/graph.py` | `build_matching_graph`, `match_patient_to_trials` | every stage module |
| `oncotriage/agent/display.py` | console rendering | `config` |

**The dependency seam — `oncotriage/agent/deps.py` (pass 20c-2c).** Every client,
model and registry the agent uses is reached through an accessor there:
`get_openai_client`, `get_qdrant_client`, `get_bm25_query_model`,
`get_medcpt_tokenizer`, `get_medcpt_model`, `get_cancer_registry`,
`get_lab_registry`, `get_mesh_filter`. Each answers **override → cached → build
once**, so a test harness installs `deps.set_override(deps.QDRANT_CLIENT, stub)`
and every call site inside the agent sees it. `deps.OVERRIDE_KEYS` is closed —
an unknown key raises `KeyError` rather than being silently ignored, because a
dropped override is the failure this module exists to prevent.

*Why it exists.* Files 45 and 46 used to redirect the pipeline by rebinding four
names — `openai_client`, `qdrant_client`, `_bm25_query_model`,
`medcpt_score_pairs` — in the shared exec namespace, and Files 35, 36 and 37 did
the same for the registries, the MeSH filter and `get_embedding`. That worked
only because every file was `exec()`'d into one dict. A module function resolves
its globals in its own module, so once File 13 became a package **every one of
those rebindings would have reached nothing** — and `46- Fixture Replay.py`
would have sent all twelve fixtures' Stage 5 prompts to the real OpenAI endpoint,
been billed, and still printed that they replayed clean. Nothing would have
raised. All five files now install overrides and **assert by identity** that the
object `deps` hands the agent is theirs; File 46 runs that assertion as a
negative control first, with no override installed, and refuses to replay unless
it fails.

**`MEDCPT_SCORER` is the fourth override key and its default lives in
`models`, not `deps`.** The fixtures record *scores*, not tensors, so the whole
`(query, trial_texts) -> scores` function is the seam; `models.score_pairs()` is
the dispatcher, because `models` imports `deps` and the reverse edge would be a
cycle. Every caller inside the agent uses `score_pairs`, never
`medcpt_score_pairs` directly.

**Importing the agent loads no model.** File 13 built MedCPT (~110 MB) and
FastEmbed at `exec()` time, so all twelve files that chain it paid for both just
by being read. Both are lazy now; `ONCOTRIAGE_DEFER_LOCAL_MODELS` survives as the
second line of defence (a forgotten stand-in becomes a named `RuntimeError`
instead of a real load) but decides nothing at import. File 47 check 2d imports
the agent with that variable **unset** and requires `torch` and `transformers` to
be absent from `sys.modules`.

**The File 13 shim cannot bridge a rebinding, so it detects one.** The File 14
pattern — a wrapper reading `globals().get(...)` — works only because
`log_inference` is called *by* the caller, so the shim sits in the call path.
`qdrant_client` is used *inside* the agent and never called by the caller, so no
wrapper is ever in the path. Instead the shim records the identity of all nine
redirectable names at load and `match_patient_to_trials` refuses to run if any
was rebound, naming the `deps` key to use instead. `medcpt_tokenizer`,
`medcpt_model` and `_bm25_query_model` are bound to lazy proxies (File 12 calls
the first two directly); `_CANCER_REGISTRY`, `_LAB_REGISTRY` and `_MESH_FILTER`
are bound **eagerly to the real values** because `_MESH_FILTER is None` is a real
branch that a proxy would break.

**Path resolution is lazy (pass 20c-2b).** `oncotriage/paths.py` used to resolve
the whole sibling tree at import, so `import oncotriage.config` — which imports
`paths` for `load_env_keys` — raised on any machine without that tree: a wheel
install, a CI checkout of `03- Code` alone, a container built before its data
volume is mounted. Every path is now resolved on **first attribute read** and
cached, through a PEP 562 module `__getattr__`. `from oncotriage.paths import
data_fhir_path` and `paths.inferences_path` both still return a plain string, so
no consumer changed, and `01- Imports.py` imports all sixteen names by name so
the exec chain resolves exactly as eagerly as before. `_glob_one` reads the root
through `_resolve()` rather than as a bare global — a module `__getattr__` is
consulted for attribute access on the module, **not** for a global name lookup
inside a function body, so `main_path` written bare in there would be a
`NameError`. File 47 check 2b imports `config` with `ONCOTRIAGE_MAIN_PATH`
pointed at a directory that does not exist and requires the import to succeed,
`MAX_WORKERS` to be readable, and the first path *read* to still raise.

The real rule, replacing "nothing is importable":

- **New shared code goes in `oncotriage/`, and is `import`ed.** Only put something in a numbered file if it needs the shared exec namespace. `import` of files 04-46 is still impossible; `from oncotriage.config import MAX_WORKERS` is now the normal way to reach a tunable from anything that is not in the chain.
- **`oncotriage.config` must never import `oncotriage.utils`.** That was the cycle: File 02 read `PRICING_CONFIG` / `COLLECTION_NAME` / `qdrant_client` / `DATA_SNAPSHOT_DATE` out of File 03, while File 03 called `load_env_keys()` out of File 02. Under `exec()` both resolved at runtime; as modules it is an `ImportError`. `load_env_keys` moving out of the pair is what broke it — into `settings` in pass 20c-1, into `paths` in pass 20c-2a — and `47- Package Split Test.py` fails if the edge comes back. Note the reintroduced cycle is **order-dependent**: `import oncotriage.config` against it still succeeds, so the AST check is the guard, not the import test.
- **No `oncotriage` module may import another `oncotriage` module from inside a function body.** A deferred import is a dependency that no scan of an import block can see, and it never fails at import in any order, so nothing but a static scan finds it. File 47 scans for it and carries a negative control. **Third-party imports in function bodies are exempt and must stay** — `import icd10` inside `_build_icd10_cancer_sets()` is deliberate: hoisting it would make importing the cancer registry load the whole ICD-10-CM release.
- **Importing a package module opens no client, loads no model, touches no database, reads no file and resolves no directory.** `get_openai_client()` / `get_qdrant_client()` build once, on first call, and cache; `load_mesh_filter()` reads its four JSON lookups on call, never at import; `_build_icd10_cancer_sets()` imports `icd10` on first registry construction; every path in `oncotriage/paths.py` resolves on first read; MedCPT and FastEmbed load on first use through `oncotriage/agent/deps.py`. `03- Config.py` calls the client factories at shim load and binds the eager `openai_client` / `qdrant_client` names the chain expects — same objects, no second client. File 47 proves this by trapping `builtins.open`, `io.open`, `socket.socket`, `socket.create_connection` and `sqlite3.connect` **before** importing all twenty-seven modules and firing each trap afterwards to show it was armed.
  The `glob` in `paths` was the one exception until pass 20c-2b, and pass 20c-2c found that the fix had a hole: `oncotriage/registries/mesh.py` still wrote `from oncotriage.paths import data_MeSH_path` at module scope, and a `from X import name` is an **attribute read**, so it fired the lazy resolver — meaning importing the *agent* (which imports `deps`, which imports `mesh`) globbed the whole sibling tree and raised on any machine without it. Check 2b only covered `config`, which is how it survived a whole pass. Check 2c now imports **every** package module in its own subprocess with the root pointed at a directory that does not exist. Note that no `open` trap could ever have caught this: `glob.glob` uses `os.scandir`.
- The three functions that read a value out of the shared namespace at call time — `get_model_cost`, `resolve_qdrant_collection`, `get_age_reference_date` — take that value as an **optional argument** in the package, and `02- Utility Functions.py` wraps each one to pass `globals().get(...)`. That seam is load-bearing: Files 36, 37, 45 and 46 rebind `qdrant_client`, and File 38 rebinds `DATA_SNAPSHOT_DATE` and requires a raise.
- `pip install -e .` from `03- Code/` makes the package importable from anywhere. Without it, `01- Imports.py` puts the code directory on `sys.path` itself and prints that it did.

Everything else about the chain is unchanged:

- A function used in file N may be *defined* in file 1, 2, 3, 8, 9, or 10 with no import statement at its use site. To find a definition, grep across all `*.py` **and** `oncotriage/*.py`.
- Every entry-point file begins with the same bootstrap: raw `exec()` of `01- Imports.py` and `02- Utility Functions.py` (needed because `exec_chain` itself lives in 02), then `exec_chain([...])` for the rest. All 31 bootstraps load 01 first, and they have to — File 02 has always used `os`, `re`, `time`, `httpx` and `logging` out of File 01's import block.
- `01- Imports.py` keeps its **third-party import block verbatim**. Files 04-46 reach for `np`, `pd`, `Path`, `OpenAI`, `torch` and eighty more with no import of their own, and only an exec'd file can bind those in the caller's globals. Do not move that block into the package.
- `exec_chain` sets `__name__ = "_exec_chain_"` while exec'ing, so `if __name__ == "__main__":` blocks in chained files do **not** fire. That is the mechanism that lets a file be both a runnable script and a library.
- **Do not double-load.** `13- LangGraph Agent.py` already chains 08, 09, 10 — callers of 13 (17, 25, 26) must not list them again. See the warning comment in `26- Ablation Study.py`.
- `_code_dir` is **derived from `__file__`** at the top of each entry point (item 20a); there is no hardcoded absolute path in any tracked file except `FALLBACK_MAIN_PATH` in `oncotriage/settings.py`, which is the deliberate one-machine fallback for `ONCOTRIAGE_MAIN_PATH`. Docker mounts the code at `/app` and `oncotriage/paths.py` switches all data paths on `IS_DOCKER`.

Adding a new script means: copy the bootstrap block, list its deps in `exec_chain`, and put any new shared library in `01- Imports.py` / any new constant in `oncotriage/config.py`.

## Running things

All commands run from `03- Code/`. Filenames contain spaces — always quote them.

```bash
# Pipeline services
python "17- FastAPI Server.py"                       # API on :8000 (/docs)
streamlit run "21- Streamlit Dashboard.py"           # dashboard on :8501
python "25- Batch Runner.py"                         # full-corpus run, no HTTP, checkpointed

# Data + index build (one-time / weekly)
python "04- FHIR Generate Data.py"                   # Synthea JAR -> ~22k patients
python "04- FHIR Generate Data.py" --population 3000 --seed 1 --output-dir <scratch>
python "04- FHIR Generate Data.py" --module-only     # rewrite the ECOG module, no generation
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

```bash
python "39- ECOG Performance Status Surfacing Test.py"   # needs the scratch corpus from 04-
python "47- Package Split Test.py"                       # no network, no keys, no corpus
pip install -e .                                         # makes `oncotriage` importable anywhere
```

**Tests** are not pytest — `18-` and `19-` are procedural scripts hitting a *live* server on `localhost:8000`; start `17-` in another terminal first. `19-` slices `fhir_files[410:412]` for a smoke run; widen that slice to go broader.

To exercise the graph directly without the API, set `RUN_TEST_ON_EXECUTE = True` near the bottom of `13- LangGraph Agent.py` and run it as `__main__`. The block survived the pass-20c-2c split and still lives in the shim.

## Layout outside this repo

Only `03- Code/` is version-controlled. Sibling directories under the project root are resolved by **glob prefix** in `oncotriage/paths.py` (`glob.glob(main_path + "/*Data/")[0]`, via `_glob_one`, which names the pattern and the root when nothing matches), so directories can be renumbered but not renamed past their suffix. The root itself comes from `ONCOTRIAGE_MAIN_PATH` or, unset, from `FALLBACK_MAIN_PATH` in `oncotriage/settings.py`:

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

`oncotriage/agent/` is the core (shim: `13- LangGraph Agent.py`, 5,565 lines before pass 20c-2c split it twelve ways). `build_matching_graph()` in `agent/graph.py` wires a `StateGraph` over `TrialMatchState` (`agent/state.py`):

1. **`node_query_expansion`** — deterministic, no LLM. Uses the cancer registry (08) + MeSH filter (09) to expand the patient's primary diagnosis into query terms.
2. **`node_hybrid_retrieval`** — Qdrant-native BM25 (FastEmbed sparse, `BM25_RETRIEVAL_SIZE=75`) + dense `text-embedding-3-small` (`VECTOR_RETRIEVAL_SIZE=100`), fused by RRF into `RRF_POOL_SIZE`. Falls back to BM25-only if vector search fails.
3. **`node_cross_encoder_rerank`** — MedCPT cross-encoder, multi-query with RRF across queries, stable argsort for determinism. `RERANK_SCORE_THRESHOLD = -10`.
4. **`node_rule_based_filter`** — MeSH site relevance, cancer stage ordinal, histology, age, sex + a dynamic quality threshold and cost cap (`MAX_TRIALS_FOR_EVALUATION = 15`).
5. **`node_gpt4o_evaluation`** — one GPT-4o call producing per-criterion verdicts; JSON-parse failures loop back up to `MAX_GPT4O_RETRIES = 3`.
6. **`node_finalize`** — splits eligible/not_eligible, normalizes labels.

Nodes 1–3 are in `agent/retrieval.py`, node 4 in `agent/filtering.py`, node 5 in `agent/evaluation.py`, node 6 and the other two terminal nodes in `agent/terminal.py`.

Conditional edges route to `node_no_candidates` when a stage empties the pool, and any exception lands in `node_error_handler`, which still emits a well-formed result. `match_patient_to_trials(patient_data, graph)` is the public entry point; it stamps `qdrant_collection` and `patient_data_hash` onto the result. **The shim wraps it** with the legacy-rebinding guard described above — that wrapper is the only thing in the shim that is not a re-export.

**Ablation flags** ride in the state dict (`state["ablation_flags"]`) and are read at three points (nodes 2, 3, 4). `26- Ablation Study.py` toggles one stage per config; nothing else forks the pipeline.

### Synthea generation and the ECOG module (File 04)

File 04 no longer just shells out to the JAR. It writes a custom Generic Module
Framework module (`SYNTHEA_MODULES_DIR`, resolved from `data_patient_path`) that
records an ECOG performance status, hands it to Synthea with `-d`, then
post-processes and documents the run:

- **`-m` must name the ECOG module explicitly.** `MODULE_FILTER = "*cancer*"`
  does not match it, and a module the filter misses is dropped **silently** —
  Synthea exits 0 with no warning. `build_module_filter_argument()` joins the
  two patterns; `generate_synthea_patients()` then greps the captured Synthea
  log for the module's "Loading module …" line and fails the run if it is
  absent. Never widen the filter without keeping that check.
- **`valueQuantity` → `valueInteger` is a post-export rewrite, not the module.**
  Synthea's `FhirR4.mapValueToFHIRType()` maps *every* numeric observation value
  to a Quantity — there is no integer path — and its Observation validator
  refuses to load a module whose numeric value has a blank unit. So the module
  is forced to emit `valueQuantity` with the UCUM annotation unit `{score}`, and
  `normalize_ecog_observations()` rewrites the exported bundles into the
  `valueInteger` mCODE requires. It is idempotent and it raises on any score
  that is non-integral or outside 0–4.
- **The guard is `Active Condition` over SNOMED codes, not `Attribute`.** Synthea's
  oncology modules set no common cancer flag (breast and colorectal set only
  downstream attributes), so there is nothing to key on. `ECOG_GUARD_CANCER_CODES`
  in File 04 is the code set, with its inclusions and exclusions argued inline.
- **`ECOG_SCORE_DISTRIBUTION` and `ECOG_MISSINGNESS_FRACTION` (`oncotriage/config.py`) are
  uncalibrated holding values.** Observed missingness always exceeds the
  configured fraction, because a patient who dies before the next encounter
  after diagnosis is also never scored. Both numbers land in the run manifest.
- **Every run writes `generation_run_manifest.json`** next to the data: command,
  seed, JAR sha256, module filename + sha256, the configured distribution and
  missingness, and what was actually observed. That manifest is what a
  regeneration needs; the stats dict File 04 used to print and drop was not.
- `generate_synthea_patients()` **refuses** to write into an output directory
  whose `fhir/` already holds bundles unless `--force` — Synthea appends rather
  than replaces, so the default target being the live corpus made that a
  one-keystroke way to interleave two populations.

### Supporting modules

These three moved into the package in pass 20c-2a. The numbered files survive as explicit re-export shims and every chain consumer (05, 06, 11, 13, 30, 31, 32, 33, 42, 43) still reads their names out of the shared namespace unchanged.

- **`oncotriage/registries/cancer_code_registry.py`** (shim: `08- Cancer Code Registry.py`) — primary-cancer detection: SNOMED exact → ICD-10-CM 2024 exact (`icd10-cm` package, handles `C34.10` and `C3410`) → display-term morphology fallback. Metastatic/secondary terms are rejected at every layer. Never assume the first condition in a FHIR bundle is the cancer. `import icd10` stays inside `_build_icd10_cancer_sets()`; do not hoist it. **This module's SOURCE TEXT is read by two tests** — `42-` extracts the inline comment beside every code as the claim under audit, and `43-` plants defects into it and hashes the restore. Both point here, not at the shim, and File 42 refuses to run if either claim dict comes back empty.
  Three names pass 2a re-exported are **gone as of pass 20c-2b**: `_REGISTRY` and `_LAB_REGISTRY` were snapshots of the module's private singleton slots, permanently `None` however the real slot was later filled, so they read as "no registry built yet" and could never read as anything else — use `load_registry()` / `load_lab_registry()`. `_var` was a leaked loop variable; `'_var'` is now the last entry in the cleanup tuple that deletes the other four, so the module binds it and then removes it. File 47 holds the pre-2a inventory unedited plus an explicit list of these three as the only permitted deletions, and checks each one is genuinely absent — a fourth name going missing still fails.
- **`oncotriage/registries/mesh.py`** (shim: `09- MeSH Cancer Site Relevance Filter.py`) — MeSH C04 tree ancestry match. Patient side maps SNOMED→CUI→MeSH via UMLS `MRCONSO`, falling back to fuzzy descriptor matching. Trial side is a direct lookup (ClinicalTrials.gov conditions *are* MeSH terms). **Conservative by design: unmappable on either side ⇒ KEEP.**
- **`oncotriage/registries/mesh_crosswalk_build.py`** — File 09's five offline builders (`build_mesh_lookup`, the three crosswalks, `build_all_lookups`). They parse `desc2026.xml` and the 1.5 GB `MRCONSO_2025AB.RRF` and write the JSON that `mesh.py` reads back. **Called from nowhere in the pipeline** — File 09's `__main__` block is the only call site, and `python "09- MeSH Cancer Site Relevance Filter.py"` still runs them. `mesh` does not import this module.
- **`oncotriage/extraction/{negation,stage,histology}.py`** (shim: `10- Structured Eligibility Extractor.py`) — index-time, rule-based, zero-LLM extraction of stage requirements into a structured dict, so stage matching in node 4 is an integer comparison. Unknown ⇒ `None` ⇒ trial passes. The three-way split rests on one measurement: walking every top-level definition in each half for `Name` loads resolving into the other half finds **exactly one edge**, `_is_histology_negated()` → `_is_negated()`. That helper is what `negation.py` holds. File 47 re-derives the measurement against the shipped modules, so a second shared name fails rather than accumulating.
- **`oncotriage/fhir/parser.py`** (shim: `07- FHIR Parser.py`) — `parse_fhir_bundle(path)` takes a **file path**, not a dict (the API writes a temp file to bridge this). Historical medications are deliberately retained with status labels so prior-treatment criteria are evaluable. **LOINC 89247-1 (ECOG) is routed out of `observations`** into `patient_data['ecog_performance_status']`, a dict that is present on every patient. `value` is `None` when nothing was recorded and is **never defaulted to 0** — ECOG 0 is *fully active*, the most eligible a patient can be, so every consumer must test `is None`, never truthiness. Both `valueInteger` (mCODE) and `valueQuantity` (raw Synthea, unit `{score}`) parse, and which was found is kept as `value_shape`; a non-integral or out-of-range grade **raises** rather than rounding. The winner is the most recent observation dated on or before `get_age_reference_date()`, never `datetime.now()`, with the counts and the selection path recorded alongside. `compute_patient_hash` (13) hashes value/date/count/selection but deliberately **not** `value_shape` — normalizing a corpus must not change a hash when the prompt text is identical — and emits nothing at all when no ECOG was present, so hashes already logged against an ECOG-free corpus stay comparable. Covered by `39- ECOG Performance Status Surfacing Test.py`. **This module's SOURCE TEXT is read by two tests, and both point here, not at the shim** — `38-` ast-parses it to prove `_calculate_age` and `_parse_demographics` contain no clock call (and now checks both functions are actually present, because a stale filename made that assertion pass on an empty result), and `39-` slices named function bodies out of it. The shim keeps File 07's `__main__` block, which is the only place in the original 1,491 lines that named a path; it now resolves `data_fhir_path` from the shared namespace when there is one and from `oncotriage.paths` otherwise, prints which, and — unlike before — works when the file is run directly.

### Index lifecycle (Qdrant)

`COLLECTION_NAME = "trial_criteria"` is an **alias**, never a collection. `11-` builds into a timestamped staging collection (`trial_criteria_20260226_140159`), creates payload indexes, then `swap_alias_atomic()` in a single `update_collection_aliases` call (zero downtime), then `cleanup_old_collections()`. Use `resolve_qdrant_collection()` (`oncotriage/utils.py`, re-exported by file 02) whenever the *real* collection name is needed for logging — it retries and falls back gracefully. File 02's wrapper hands it the shared namespace's `qdrant_client`, so a fixture proxy or a test stub is what it talks to.

`23- Airflow DAG.py` writes the `trial_refresh_weekly` DAG (Sundays 02:00) into `{airflow_path}/dags/`; `22-` initializes the Airflow DB and `24-` starts/stops/triggers via the REST API v2. The DAG file is generated as a string, so DAG logic edits go in `23-`, not in the `dags/` output.

**The generated DAG must be regenerated after item 20c.** Its `_config_literal()` / `_load_config()` read `oncotriage/config.py` now; a DAG file generated before this pass still reads `03- Config.py`, where the constants are no longer *assigned* — `AIRFLOW_DAG_SCHEDULE is not assigned at module level` at every scheduler parse. File 23 **will not overwrite** an existing DAG, so: `rm "{airflow_path}/dags/trial_refresh_weekly.py"` then `python "23- Airflow DAG.py"`.

## Persistence and observability

**`oncotriage/storage/database_logger.py`** (shim: `14- Database Logger.py`) opens no database at load time and never did since item 20b, which turned schema creation into a function because nine other files load 14 or are loaded beside it and every one of them was touching `inferences.db` just by being read. `initialize_database(db_path)` creates three tables: `inferences` (per-patient funnel counts, per-stage timings, token counts, cost), `trial_matches` (per-trial verdicts), `drift_metrics`. It is idempotent — every `CREATE` is `IF NOT EXISTS`, every `ALTER` is guarded by a `PRAGMA table_info` check — and `log_inference` ensures the schema once per resolved path before its first write. `16-` is a scratch query script; `15-` wipes all tables and is guarded by `Flag = False` — leave it False.

**`log_inference(result, patient_data, db_path=None)` takes the database as an argument, and the five isolation tests pass it.** `db_path=None` means `oncotriage.paths.inferences_path` — `resolve_inference_db_path()` is the resolver, and it deliberately does *not* consult the exec namespace. The **shim's** `log_inference` is a wrapper that supplies `globals().get("inferences_path")`, the same late-binding seam File 02 uses for `get_model_cost` / `resolve_qdrant_collection` / `get_age_reference_date`. That seam is what keeps the redirect working for Files 36, 37, 38, 40 and 45, all of which rebind `inferences_path` at a temporary database *before* loading File 14: a module function cannot see a caller's globals, so without the wrapper all five would have written real rows into the real `inferences.db` while still printing the name of the temporary file each thought it was using. All five now **also** pass `db_path` explicitly and assert on the path `log_inference` returns, so neither mechanism is a single point of failure. Each one first checks that `resolve_inference_db_path(None)` is the production database and is *not* its own scratch path, which is what makes the assertion discriminating rather than vacuous. **File 41 is the one file that still rebinds `inferences_path` without passing a path** — it does so for `log_drift_metrics` in File 20, never loads File 14, and stays as it is until File 20 converts in pass 20c-3.

`_resolve_primary_cancer` lives in **`oncotriage/registries/primary_cancer.py`** as of pass 20c-2c, and both the agent's three terminal nodes and the storage logger import it from there. Pass 2b had already stopped it reading File 13's `_CANCER_REGISTRY` global — a layering violation that left it raising `NameError` in any chain loading 14 without 13 — in favour of `load_registry()`. Pass 2c finished the job: while the function lived in the storage module, the *agent* depended on the *storage* layer for a registry lookup. **`38- Birth Date and Demographics Parser Test.py` section 9b is the only place that exercises it**, because File 38 is the only chain in the repository that loads 14 without 13; it calls the function directly on a stub condition list and asserts a real diagnosis comes back, having first asserted the result is not `None` (which an empty registry filter also returns).

`21- Streamlit Dashboard.py` (~5.2k lines) reads only from `inferences.db` via `@st.cache_data(ttl=60)`.

**Cost accounting fails loudly.** Costs come from `get_model_cost()` (`oncotriage/utils.py`, re-exported by file 02) against `PRICING_CONFIG` in `oncotriage/config.py`, dated `last_updated`. A model absent from that table raises `UnknownModelPricingError` (a `RuntimeError` subclass — deliberately *not* a `KeyError`, so a stray `except KeyError` cannot eat it); it does not return 0.0, because a zero cost row is indistinguishable from a genuinely free run and every aggregate over the column silently under-reports. Both writers — `log_inference` (14) and `log_ablation_result` (26) — call it **before** their `try` block for exactly this reason: their broad `except` exists to keep a database failure from killing the pipeline, and an unpriced model is a config defect that must reach the caller instead. If you add a model, add its pricing first; never wrap `get_model_cost()` in a recovery path.

**Degradation record.** A run that lost a retrieval channel, fell back to the un-expanded query, or skipped the cancer site filter must be identifiable from its stored row alone. The relevant state keys are written by the stage that owns them, carried to all three terminal nodes by `_pipeline_provenance()` (file 13), and logged to `inferences.retrieval_channels` / `retrieval_degraded` / `retrieval_trials_lost` / `query_expansion_path` / `mesh_filter_applied` / `mesh_filter_skip_reason`. **NULL in these columns means the stage never reported and is not the same as a clean value** — never default them to 0 in a new writer or fold NULL into 0 in a reader. Stage 5's Section 2 is conditional on `mesh_filter_applied`: it only asserts to the model that disease relevance was confirmed when the filter actually ran.

**Age reference date.** Patient age is computed against `DATA_SNAPSHOT_DATE` (`oncotriage/config.py`, re-exported by 03), never `datetime.now()`, and so is the Stage 5 prompt's RULE 4 "Reference date" — a clock-derived age changes the prompt while `compute_patient_hash` (which keys on `birth_date`) cannot see it. `parse_partial_date()` / `get_age_reference_date()` live in `oncotriage/utils.py`; `get_age_reference_date()` resolves the constant through `oncotriage.config` and **raises** rather than falling back to `today()`, and `44- Snapshot Date Rot Test.py` rewrites that literal in `oncotriage/config.py` — not in File 03, which only re-exports it; `birthDate` may legally be `YYYY`, `YYYY-MM`, `YYYY-MM-DD` or a full ISO datetime, and missing components are filled from a mid-range anchor with the shape recorded as `inferences.birth_date_precision` (same NULL semantics as above). Race and ethnicity are read from the US Core extensions **by sub-extension url** (`ombCategory` → `detailed` → `text`), never by array position. `Exception and Fallback Audit.md` inventories every `except` and fallback in the codebase with a verdict and the open items.

## Conventions

- **All tunables live in `oncotriage/config.py`** (`03- Config.py` re-exports them for the exec chain). Retrieval sizes, thresholds, temperatures (both 0 for determinism), rate limiting, drift windows, batch runner settings. Don't scatter magic numbers into node bodies.
- `ENABLE_RATE_LIMITING = False` by default so batch evaluation isn't throttled; flip it for production.
- Long local runs wrap in `with CaffeinateSession("label"):` to stop macOS sleeping.
- Qdrant calls use the shared `qdrant_retry` tenacity decorator (`oncotriage/utils.py`) for connect/timeout/`UnexpectedResponse`.
- Determinism is a deliberate property of the pipeline (temperature 0, stable argsort, seeded sampling with `RESAMPLE_SEED = 42`). Preserve it when editing ranking or sampling code.
- Files carry a Spyder-generated `#!/usr/bin/env python3` + creation-date docstring footer at the **bottom**; append new code above it. The `oncotriage/` modules keep the same footer.

## Important Rules
Tunable values go in the config module. Facts about an external
standard (MeSH tree numbers, LOINC codes, FHIR resource names)
stay inline as named constants.

Never catch an exception without either re-raising it or
recording it in a counter. Silent recovery is the specific
defect this project exists to remove.

If you add a fallback path, log which path was taken.

Every new assertion must be shown to FAIL when the condition it
checks is broken, and the demonstration recorded. An assertion
that has only ever passed is not evidence that it can catch
anything. Break the thing under test, run the assertion, record
that it failed, restore, record that it passes again.

This is not a style preference. Three defects of exactly this
class have already shipped: File 42's boundary assertions were
written from the constants they were meant to check, so they
agreed with the code by construction; an "is not None" assertion
sat inside a file whose entire purpose is catching unchecked
claims; and item 29a's reasoning-token cost check passed through
a zero, because the shipped reasoning_effort is "none" and
0 == 0 made "cost with reasoning added" equal "cost without" for
the wrong reason.

Prefer a demonstration that mutates a COPY of the source and
execs it (see the proof harness for File 36's Test 7) over one
that edits a file in place. Where in-place is unavoidable, hash
the file before and after and assert the restore was
byte-identical, the way Files 43 and 44 do.

Where an assertion could be satisfied by a degenerate value —
a zero, an empty set, a None on both sides — assert first that
the value is non-degenerate, so the test fails rather than
passing vacuously when someone later changes it.

Data and keys live outside this folder. Never write an
absolute path. The one exception already exists and is
argued in place: FALLBACK_MAIN_PATH in oncotriage/settings.py.

When you finish, state which parts you verified by running
something, and which parts you only read.