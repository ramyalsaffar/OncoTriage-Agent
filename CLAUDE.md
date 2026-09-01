# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**OncoTriage Agent** — matches oncology patients (Synthea FHIR bundles) to recruiting ClinicalTrials.gov trials using a 6-stage LangGraph pipeline over hybrid BM25 + vector RAG on Qdrant, with an LLM classifier (`config.MATCHING_MODEL`, `gpt-5.6-terra` since 2026-08-04) for criterion-level eligibility evaluation.

## THE EXEC CHAIN IS DEAD (pass 20e) — read this before touching any file

Files 04 to 49 are numbered, space-containing filenames (`25- Batch Runner.py`).
**None of them is importable** — spaces and leading digits — and nothing has
changed that. What HAS changed is that none of them is `exec()`'d either.

**PASS 20e DELETED SEVEN NUMBERED SHIMS AND `exec_chain` ITSELF.** Every
numbered file that survives is a **thin entry point** — a `__main__` guard, the
imports it needs, and nothing else — or a runnable service. None re-exports a
name for anybody. None `exec()`s another file. Nothing loads a module by
location.

| Deleted | Its content is now |
|---|---|
| `01- Imports.py` | third-party imports are each module's own; the `sys.path` bootstrap is the six-line block in every entry point; paths resolve lazily in `oncotriage/paths.py` |
| `02- Utility Functions.py` | `oncotriage/utils.py`. **`exec_chain` is deleted outright** |
| `03- Config.py` | `oncotriage/config.py` |
| `08- Cancer Code Registry.py` | `oncotriage/registries/cancer_code_registry.py` |
| `10- Structured Eligibility Extractor.py` | `oncotriage/extraction/{negation,stage,histology}.py` |
| `14- Database Logger.py` | `oncotriage/storage/database_logger.py` |
| `oncotriage_settings.py` | `oncotriage/settings.py` |

**THE READING ORDER MOVED TO [`PIPELINE SEQUENCE.md`](PIPELINE%20SEQUENCE.md).**
Reading 01 → 29 in order used to be how a person learned the pipeline. The
numbers now say **what you can run**, with gaps, and that document says what the
pipeline does, stage by stage, with the package module that holds each — plus
the eight modules that never had a number, several of which
(`agent/deps.py`, `embedding.py`, `registries/primary_cancer.py`) are the most
load-bearing code in the project. **Survivors were not renumbered:** gaps are
cheaper than every note in the project resolving to the wrong file.

**[`FIXTURE CAPTURE RECORD.md`](FIXTURE%20CAPTURE%20RECORD.md) is the provenance
of the twelve characterization fixtures** — what each capture cost, which model,
prompt version and Qdrant collection produced it, the per-file sha256 of the set
on disk and of the set it replaced, and which verdicts moved. The fixtures live
outside the repository and are not in git, so a capture leaves no commit and
this file is the only place any of that lands.

**FIVE SHIMS BECAME THIN ENTRY POINTS RATHER THAN BEING DELETED**, because each
keeps a runnable `__main__`: `05- FHIR Clean Data.py` (the cohort deletion),
`07- FHIR Parser.py` (a corpus smoke run), `09- MeSH Cancer Site Relevance
Filter.py` (the five offline lookup builders), `13- LangGraph Agent.py` (a
one-patient smoke run, `RUN_TEST_ON_EXECUTE = False`) and `20- Drift
Detection.py`.

**HOW THE DELETIONS WERE DECIDED — MEASURED, NOT INHERITED.** Every shim
docstring's consumer list predated the passes that emptied it, and each of the
last three chainers was chaining for callers converted one or two passes
earlier: File 05 for File 34 (converted at 20c-3d), File 13 for twelve files all
converted by 20d-1, File 09 for five. So each shim's re-exported names AND its
FILENAME AS A STRING were grepped across every `.py`, `.md`, `.toml` and `.yml`
in the tree, and each hit was classified by AST as comment / docstring / live
string. **The last two live roots were Files 18 and 19**, which raw-exec'd 01
(and 02) for six free names; pass 20e gave them ordinary imports. Their free-name
sets were re-derived with **symtable**, and both of the comments that listed
them were **wrong** — each file also used `os`, `shutil`, `sqlite3`, `tempfile`,
`datetime`, `timezone` and `inferences_path` out of File 01 without saying so.

**FOUR FILES RENAMED, NUMBERS KEPT.** The number is what notes resolve by, so it
never moves; the words after it were wrong. `06- FHIR Explore.py` →
**`06- FHIR Dataset Characterization.py`** ("explore" reads as a scratch
notebook; item 9 makes it the source of the characterisation).
`15- Database Empty.py` → **`15- Database Wipe All Tables.py`** ("empty" is a
state; the file issues `DELETE FROM` against every table).
`28- Select 30 Samples.py` → **`28- Select Evaluation Sample.py`** (the old name
baked the sample size into a filename nothing checks).
`34- Cohort Selector Diff.py` → **`34- Cohort Selector Diff Read Only.py`** (it
never writes, and it sits one number from the file that unlinks bundles using
the same selector).

**WHAT ENFORCES ALL OF THIS.** `tests/test_package_invariants.py` **section 1c**
scans every `.py` in the repository for a call to `exec_chain`, a call to
`exec()` outside a **closed argued allowlist** (some members exec git blobs,
most exec a patched in-memory copy — see the rule below), or a by-location
module load — **re-parsing string literals as Python** so that
`ns["exec_chain"](...)` hidden in a subprocess probe is caught and so that prose
about the exec chain is not. Six planted controls, each fired. **Section 5**
asserts that no numbered file imports a package name at module scope it never
reads, which is what a re-export IS.

**ELEVEN TEST FILES MOVED IN PASS 20d-1** — Files 30, 31, 32, 33, 35, 36, 37,
38, 39, 40 and 41 — into `tests/` under names describing what they cover.
**[`tests/FILE NUMBER MAPPING.md`](tests/FILE%20NUMBER%20MAPPING.md) is the
old-to-new mapping for every number this project has ever moved**, including
pass 20e's deletions and renames. See "The component tests (pass 20d-1)".

**THE SEQUENCE DOCUMENT'S CLAIM THAT FILES 30 TO 44 ARE ALL TEST FILES IS WRONG, in two directions.** **File 34 is not a test** — it is a read-only cohort-selector comparison, converted at pass 20c-3d to `oncotriage/evaluation/cohort_diff.py` with File 34 as its thin entry point. And Files 42, 43 and 44 *are* tests but were **not** in pass 20d-1: they mutate files in the repository and belong to the collision matrix. **Pass 20d-2 moved them, with Files 47, 48, 49 and the runner** — see "The rest of the suite (pass 20d-2)".

**File 17 keeps one re-exported name, `app`, and it is load-bearing.**
`docker-compose.yml` runs `uvicorn "17- FastAPI Server:app"`, and that WORKS —
`importlib.import_module` does not require a valid Python identifier, only a
file the path finder can locate, so a module name with a space and a leading
digit imports fine as long as nobody writes an `import` STATEMENT for it.
Verified rather than assumed. It is the same object as
`oncotriage.api.server:app`.

| Module | Holds | Imports |
|---|---|---|
| `oncotriage/settings.py` | `ENV_*` names, `resolve_*_path()`, `DegradedDependencyError` + `resolve_allow_degraded_registries()` (item 11a), `resolve_qdrant_url()` / `resolve_qdrant_api_key()` (Docker pass) | nothing from the project |
| `oncotriage/agent/readiness.py` | the index probe (`probe_index`, four named states), Stage 2's gate (`require_populated_index`, `EmptyIndexError`), the API's startup gate (`serving_readiness`) | `config`, `agent.deps`, `registries.mesh`, `settings` |
| `oncotriage/paths.py` | `IS_DOCKER`, `_glob_one`, every path variable (**lazy**), `load_env_keys()` | `settings` |
| `oncotriage/constants.py` | `SYSTEM_KEY_ABSENT` / `SYSTEM_KEY_UNRECOGNIZED` | nothing at all |
| `oncotriage/observability.py` | **the one place output goes** — the structured JSON logger (`get_logger`, `StructuredLogger`), the correlation ID (`correlation_scope`, `NO_CORRELATION`), the field allowlist (`LOGGABLE_FIELDS`, `filter_fields`, `FIELD_DROPS`), the console UI channel (`console.out/banner/attach_bar/detach_bar`) and `_emit_line`, the single choke point both channels write through | `settings` |
| `oncotriage/config.py` | every tunable, `PRICING_CONFIG`, `DATA_SNAPSHOT_DATE`, lazy client factories | `paths` |
| `oncotriage/tracking.py` | **the run-to-configuration index** — `start_run` / `log_run_metrics` / `end_run`, the parameter enumeration, `TRACKING_DEGRADATIONS`. **The one module that imports `mlflow`, and it imports it inside the function bodies** | `config`, `paths`, `utils`, `agent.prompts`, `observability` |
| `oncotriage/utils.py` | `get_model_cost`, `qdrant_retry`, `resolve_qdrant_collection`, `parse_partial_date`, `get_age_reference_date`, `CaffeinateSession`. **`exec_chain` was here and is deleted (pass 20e)** | `config` |
| `oncotriage/embedding.py` | **the one** `SparseTextEmbedding("Qdrant/bm25")` construction site — `get_bm25_sparse_model`, `BM25_SPARSE_MODEL_NAME` | `paths`, `observability` (the portability pass; it was `nothing from the project`, and the import is what lets the accessor pin FastEmbed's cache inside the tree) |
| `oncotriage/registries/cancer_code_registry.py` | File 08 whole — `CancerCodeRegistry`, `OncologyLabRegistry`, `load_registry`, `load_lab_registry`, `REGISTRY_DEGRADATIONS` | `constants`, `settings` |
| `oncotriage/registries/mesh.py` | File 09's filter half — `MeSHCancerFilter`, `load_mesh_filter`, `specific_cancer_trees`, `MESH_FILTER_DEGRADATIONS` | `paths`, `settings` |
| `oncotriage/registries/mesh_crosswalk_build.py` | File 09's five offline builders | nothing from the project |
| `oncotriage/extraction/negation.py` | `_is_negated` + the three constants only it reads | nothing from the project |
| `oncotriage/extraction/stage.py` | File 10 to line 698 — stage requirements | `extraction.negation` |
| `oncotriage/extraction/histology.py` | File 10 from line 699 — histology tags | `extraction.negation` |
| `oncotriage/fhir/parser.py` | File 07 whole — `parse_fhir_bundle`, `load_all_patients`, the four corpus Counters | `constants`, `utils` |
| `oncotriage/fhir/clean.py` | File 05 whole — `has_cancer_diagnosis`, `patient_death_status`, `filter_cancer_patients_inplace`, the manifest writers | `paths`, `config`, `fhir.parser`, `registries.cancer_code_registry` |
| `oncotriage/fhir/generate.py` | File 04 whole — the ECOG module builder, the Synthea subprocess, the mCODE normalizer, the run manifest | `paths`, `config` |
| `oncotriage/fhir/explore.py` | File 06 whole — the descriptive analyses and their plots | `paths`, `config`, `extraction.stage`, `registries.cancer_code_registry` |
| `oncotriage/retrieval/indexer.py` | File 11 whole — scrape, embed, index, alias swap, cleanup | `paths`, `config`, `embedding`, `extraction.*`, `utils` |
| `oncotriage/retrieval/index_validator.py` | File 12 whole — the nine index-health / retrieval checks | `config`, `agent.deps` |
| `oncotriage/storage/database_logger.py` | File 14 whole — the three-table schema, `initialize_database`, `log_inference`, **the write lock** | `paths`, `config`, `utils`, `registries.primary_cancer` |
| `oncotriage/storage/maintenance.py` | File 15 whole — `empty_database` | nothing from the project |
| `oncotriage/storage/queries.py` | File 16 whole — the `Query` registry, `run`, `run_all`, `report` | `paths`, `config`, `utils` |
| `oncotriage/api/server.py` | File 17 whole — `create_app`, `app`, the four endpoints | `agent.{deps,graph}`, `config`, `fhir.parser`, `storage.database_logger`, `utils` |
| `oncotriage/monitoring/drift.py` | File 20 whole — KS / PSI / z-score, the ECOG threshold alert, `log_drift_metrics` | `paths`, `config` |
| `oncotriage/batch/runner.py` | File 25 whole — checkpoint, the two thread pools, the summary | `paths`, `config`, `agent.{graph,retrieval,evaluation}`, `fhir.parser`, `storage.database_logger`, `utils` |
| `oncotriage/dashboard/data.py` | File 21's three `@st.cache_data(ttl=60)` loaders | `paths` |
| `oncotriage/dashboard/sidebar.py` | File 21's `render_sidebar` — filters, Refresh, CSV export | nothing from the project |
| `oncotriage/dashboard/tiers.py` | `MATCH_TIERS`, `MATCH_TIER_COLORS`, the **three** `TRIAL_STATUS_*` (per-trial) and the **four** `PATIENT_OUTCOME_*` + `PATIENT_OUTCOME_LABELS` (per-patient; pass 20f-3), `classify_trial_score`, `enrich_match_tiers` | nothing at all |
| `oncotriage/dashboard/app.py` | File 21's `main` — page config, sidebar, the nine tabs | `config`, `dashboard.{data,sidebar,tiers}`, `dashboard.tabs.*` |
| `oncotriage/dashboard/tabs/*.py` | one `render_*_tab` each, nine of them | `dashboard.{data,tiers}`, `config`, `utils` |
| `oncotriage/retrieval/qdrant_backup.py` | File 29 whole — `default_output_dir`, `download_all_collections` | `config`, `paths` |
| `oncotriage/orchestration/home.py` | **the one** `airflow_path` read — `resolve_airflow_home` | `paths` |
| `oncotriage/orchestration/airflow_setup.py` | File 22 whole — `setup_airflow` | `orchestration.home` |
| `oncotriage/orchestration/dag_generator.py` | File 23 whole — the three DAG string pieces, `build_path_block`, `build_dag_content`, `write_dag_file` | `paths`, `settings`, `orchestration.home` |
| `oncotriage/orchestration/airflow_manager.py` | File 24 whole — start/stop, the four-tier password route, status, trigger | `settings`, `orchestration.home` |
| `oncotriage/ablation/study.py` | File 26 whole — seven configs, the stratified sample, the checkpoint, the thread pool, the `ablation_results.db` writer | `paths`, `config`, `agent.{deps,graph,patient,retrieval,state,evaluation}`, `fhir.parser`, `utils` |
| `oncotriage/ablation/common.py` | **the one** `ABLATION_DB_FILENAME` / `ABLATION_SUMMARY_FILENAME` / `_require_writable_parent`, plus `CONFIG_ORDER` / `CONFIG_LABELS` / `BASELINE` and the analysis side's `output_dir()` / `ablation_db()` (pass 20f-4) | `paths` |
| `oncotriage/ablation/figures.py` | File 27's **nine figures** — the one module-scope `matplotlib` import on the ablation side (pass 20f-4) | `ablation.common` |
| `oncotriage/ablation/analysis.py` | File 27's statistics — the comparison table, the BH-FDR Wilcoxon family, the MDE, two reports, `main`. READS the database, never writes it. No matplotlib | `ablation.{common,figures}`, `config` |
| `oncotriage/evaluation/sampling.py` | File 28 whole — the seeded 10/10/10 draw into a second database | `paths` |
| `oncotriage/evaluation/cohort_diff.py` | File 34 whole — LEGACY vs CURRENT cohort selector, read only | `paths`, `config`, `fhir.{clean,parser}`, `registries.cancer_code_registry` |
| `oncotriage/fixtures/capture.py` | File 45 whole — the schema, the sink, the four proxies, `build_deterministic_prefix`, the fixture I/O, the three recipes, the cohort scan | `paths`, `config`, `agent.*`, `extraction.stage`, `fhir.parser`, `storage.database_logger`, `utils` |
| `oncotriage/fixtures/replay.py` | File 46 whole — the replay stand-ins, the OpenAI tripwire, the field diff, the five refusals | `config`, `paths`, `agent.{deps,graph,patient}`, `fhir.parser`, `fixtures.capture`, `utils` |
| `oncotriage/registries/primary_cancer.py` | `_resolve_primary_cancer` — which condition is THE cancer | `registries.cancer_code_registry` |
| `oncotriage/agent/deps.py` | **the seam**: every client, model and registry, lazily resolved and overridable | `config`, `registries.*` |
| `oncotriage/agent/state.py` | `TrialMatchState`, the channel / expansion-path / MeSH-filter vocabularies | nothing from the project |
| `oncotriage/agent/text.py` | `tokenize_for_bm25` | nothing from the project |
| `oncotriage/agent/models.py` | `medcpt_score_pairs`, `score_pairs`, `get_embedding` | `config`, `agent.deps` |
| `oncotriage/agent/patient.py` | `compute_patient_hash`, `_create_patient_summary`, the relevance classifiers | `agent.deps`, `agent.state`, `config`, `utils` |
| `oncotriage/agent/mesh_expansion.py` | Stage 1's MeSH walk | `registries.mesh` |
| `oncotriage/agent/retrieval.py` | Stages 1–3 + `build_bm25_index_from_qdrant` | `agent.{deps,models,mesh_expansion,patient,state,text}`, `config`, `registries.mesh`, `utils` |
| `oncotriage/agent/filtering.py` | Stage 4 | `agent.{deps,retrieval,state}`, `config`, `extraction.*` |
| `oncotriage/agent/evaluation.py` | Stage 5 | `agent.{bedrock_adapter,deps,patient,state}`, `config`, `utils` |
| `oncotriage/agent/bedrock_adapter.py` | Stage 5's Amazon Bedrock translation for **GPT-5.6 Terra**, behind `config.MATCHING_PROVIDER == "bedrock"` (OFF) — the Responses-API request, the ChatCompletion-shaped reply, the error taxonomy, the numbered VERIFY-AT-GO-LIVE list | `config`, `agent.{deps,response_schema}`, `observability` |
| `oncotriage/agent/bedrock_anthropic_adapter.py` | Stage 5's Amazon Bedrock translation for **Claude Sonnet 4.6**, behind `config.MATCHING_PROVIDER == "bedrock_anthropic"` (OFF) — the **Converse** request (a `cachePoint`, a schema serialized to a STRING), the ChatCompletion-shaped reply (the disjoint usage counts summed back), the botocore error taxonomy, the lettered A1..A10 list. **Shares no code with the module above**: different client library, credential chain, request shape, response shape and error classes. boto3 is imported inside the two functions that need it | `config`, `agent.{deps,response_schema}`, `observability` |
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
those rebindings would have reached nothing** — and `fixture_replay.py`
would have sent all twelve fixtures' Stage 5 prompts to the real OpenAI endpoint,
been billed, and still printed that they replayed clean. Nothing would have
raised. All five files now install overrides and **assert by identity** that the
object `deps` hands the agent is theirs; File 46 runs that assertion as a
negative control first, with no override installed, and refuses to replay unless
it fails.

**The override read is fully locked (pass 20c-3a).** `deps._resolve` used to read
`_OVERRIDES` and then `_CACHE` *outside* the lock on the fast path, taking the
lock only to build. The GIL makes each dict read atomic, but not the SEQUENCE: a
thread that reads `_OVERRIDES` (absent), is descheduled, and resumes after a
harness installs an override goes on to read `_CACHE` and hand back the REAL
client while an override is installed — a live billed call inside a harness that
reports it made none. The whole sequence is inside the RLock now. The cost is one
uncontended acquire per dependency read; `25- Batch Runner.py` drives twelve
threads through this, and pass 3a put the indexer and the validator on the same
seam.

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

**THE FILE 13 SHIM'S TWO MECHANISMS ARE GONE (pass 20e), AND THEIR ARGUMENTS
LIVE IN `oncotriage/agent/deps.py`.** The shim carried a `_LazyAgentDependency`
proxy (bound to `medcpt_tokenizer`, `medcpt_model` and `_bm25_query_model`) and
an `_assert_no_legacy_rebinding()` guard wired into `match_patient_to_trials`.
Both existed **only** because an exec-chain caller reads a NAME out of a
namespace: it cannot call an accessor, and it can rebind a name where nothing
would notice. There is no such caller and no such namespace now — every consumer
calls `deps.get_medcpt_model()` and friends, which is lazier than the proxy was,
and `deps` is the only way to redirect anything the agent reaches. Two things
were carried into `deps.py` rather than deleted with the file:

- **The rule the proxy taught.** CPython looks an implicit special method up on
  the TYPE, never through `__getattr__`, so a proxy forwarding only
  `__getattr__` and `__call__` answers `bool()`, `==`, `len`, `iter`, `in` and
  `repr()` **about itself** — confidently, and wrongly, about an object it never
  consulted. `proxy == other` returned False even when the wrapped object *was*
  `other`, which is precisely the question a fixture harness asks of this seam.
  Whoever writes the next proxy needs that; `deps.py` has it.
- **Why `peek` / `resolution_state` / `is_resolved` / `cached_keys` must not
  build.** They were added in pass 20c-3b so the proxy's `__repr__` could be
  honest without downloading 110 MB — pass 3a had made it delegate, so a
  debugger rendering locals, a log line formatting the object or a bare
  `medcpt_model` at a prompt triggered a real load and then printed
  transformers' multi-thousand-line module tree, **on the diagnostic path**,
  where the tool used to inspect the state must not be the thing that changes
  it. The proxy is gone; the rule is not, and it is now checked directly:
  `tests/test_package_invariants.py` **section 5c** counts factory calls across
  the unresolved / override / cached states and requires zero, with the accessor
  asserted to hand back the override so the counts are about the seam rather
  than about a private dict.

**Asking `deps` without building (pass 20c-3b).** `peek(key)`, `resolution_state(key)`,
`is_resolved(key)` and `cached_keys()` answer "what is installed for this key
right now" **without calling a factory**. They exist because File 13's lazy proxy
renders its `__repr__` from them; see the next paragraph. `peek` returns `UNSET`
when nothing is installed or cached, which is what distinguishes it from a
legitimately-cached `None` (`MESH_FILTER`). They are diagnostic, not an access
path — every consumer inside the agent calls a typed accessor.

**The seam is proven correct under `MAX_WORKERS` threads (pass 20c-3b).** Pass 3a
locked the whole override-then-cache sequence on the argument that
`25- Batch Runner.py` drives twelve threads through it. That argument was right
and **untested** — every harness that had ever exercised `deps` ran
single-threaded. File 47 check 5d now drives `MAX_WORKERS` threads through all
eight accessors simultaneously, behind a `threading.Barrier`, with counting
factories installed, and asserts **one shared object per key** and **exactly one
build per key**. Identity alone would not be enough: it also holds if the factory
ran twelve times and eleven results were discarded, which for a client that opens
a connection pool is a real cost identity cannot see.

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

**Exactly one construction site for the BM25 sparse model (pass 20c-3a).**
`SparseTextEmbedding("Qdrant/bm25")` used to be built in three independent
places: File 11 at index time (module level, eager), `agent/deps.py` at query
time, and File 12 inside `stage2_retrieval_tests()`. The first two are the two
halves of one job — File 11 writes each trial's three BM25 fields into Qdrant's
sparse vectors, and the agent encodes the query scored against them. BM25 sparse
vectors are **token-ID vectors over the model's vocabulary**, so if the two sides
ever named different models the query's indices would address different terms
than the documents'. Qdrant computes a dot product over whatever indices it is
handed: it keeps returning results, nothing raises, no counter moves, and only
retrieval quality falls. The third was worse — a validator carrying its own
encoder cannot detect the drift it exists to catch. All three now reach
`oncotriage/embedding.py:get_bm25_sparse_model()`; `deps._build_bm25_query_model`
delegates to it *after* consulting `ONCOTRIAGE_DEFER_LOCAL_MODELS` (the deferral
is an agent-replay concern and must not reach an index build). **File 47 check 2f
asserts by AST that the construction count in the package is exactly 1**, with a
negative control that plants a second one in a copy.

The rules, in force and enforced:

- **New shared code goes in `oncotriage/`, and is `import`ed.** A numbered file holds a `__main__` block and nothing else. `import` of files 04-49 is still impossible; `from oncotriage.config import MAX_WORKERS` is the way to reach a tunable from anywhere.
- **A module-level import name must not be shadowed by a function-local.** In Python a name assigned anywhere in a function is local for the whole of it, so a module that does `from oncotriage import config` and a function that does `config = info.config.params.vectors` turns every earlier `config.X` in that function into `UnboundLocalError`. Pass 3a hit this twice — `index_validator.stage1_index_health` (`config`) and `indexer._flush_embed_buffer` (`embedding`, a `zip()` loop variable) — and neither shows up at import, only at run time. Both were fixed by importing the *names* rather than the module. **Check 2g scans for it** and carries a negative control.
- **`oncotriage.config` must never import `oncotriage.utils`.** That was the cycle: File 02 read `PRICING_CONFIG` / `COLLECTION_NAME` / `qdrant_client` / `DATA_SNAPSHOT_DATE` out of File 03, while File 03 called `load_env_keys()` out of File 02. Under `exec()` both resolved at runtime; as modules it is an `ImportError`. `load_env_keys` moving out of the pair is what broke it — into `settings` in pass 20c-1, into `paths` in pass 20c-2a — and `tests/test_package_invariants.py` fails if the edge comes back. Note the reintroduced cycle is **order-dependent**: `import oncotriage.config` against it still succeeds, so the AST check is the guard, not the import test.
- **No `oncotriage` module may import another `oncotriage` module from inside a function body.** A deferred import is a dependency that no scan of an import block can see, and it never fails at import in any order, so nothing but a static scan finds it. Check 1b scans for it and carries a negative control. **Third-party imports in function bodies are exempt and must stay** — `import icd10` inside `_build_icd10_cancer_sets()` is deliberate: hoisting it would make importing the cancer registry load the whole ICD-10-CM release.
- **Importing a package module opens no client, loads no model, touches no database, reads no file, creates no directory, resolves no directory and spawns no process.** `get_openai_client()` / `get_qdrant_client()` build once, on first call, and cache; `load_mesh_filter()` reads its four JSON lookups on call, never at import; `_build_icd10_cancer_sets()` imports `icd10` on first registry construction; every path in `oncotriage/paths.py` resolves on first read; MedCPT and FastEmbed load on first use through `oncotriage/agent/deps.py` and `oncotriage/embedding.py`. `tests/test_package_invariants.py` section 2 proves it by trapping twelve entry points — `builtins.open`, `io.open`, `socket.socket`, `socket.create_connection`, `sqlite3.connect`, `subprocess.run`/`Popen`, `os.system`/`posix_spawn`/`execv`/`fork` — **before** importing every package module, and firing each trap afterwards to show it was armed. **This got stricter in pass 20e, for free**: `03- Config.py` used to call the client factories at shim load, so any process that touched the chain opened both clients; nothing does that now.
  Pass 20c-3a's three converted files were the worst offenders in the project: File 11 built the FastEmbed model at module level, File 06 resolved three globs, **created a directory**, built the whole ICD-10-CM registry and mutated matplotlib's global style, and File 05 resolved two globs and built the registry. Each is now behind an accessor — `patients_dir()`, `manifest_path()`, `cancer_registry()`, `csv_dir()`, `json_dir()`, `output_dir()`, `ensure_output_dir()`, `apply_plot_style()`, `synthea_jar_path()`, `synthea_modules_dir()`, `output_dir_full()`.
  **`oncotriage/fhir/explore.py` imports matplotlib, seaborn and pandas at module scope** and that is the one deliberate exception, with **`oncotriage/ablation/figures.py`** the second: seven of explore's twelve functions plot and all nine of figures' do, and section 2 pre-imports those three before arming its traps — the same allowance it makes for openai, qdrant_client, numpy and langgraph. **Pass 20f-4 moved the second exception out of `analysis.py`**, which was 1,976 lines with 24 top-level definitions of which nine touched `plt`; it is 1,503 lines and imports no plotting library now. That does **not** make importing `analysis` matplotlib-free — `main()` calls all nine, so `analysis` imports `figures` at module scope and check 1b forbids deferring it — it makes the exception 495 lines wide instead of 1,976, and it lets anything that wants the statistics without the figures import `ablation.common` and the statistics functions directly.
  The `glob` in `paths` was the one exception until pass 20c-2b, and pass 20c-2c found that the fix had a hole: `oncotriage/registries/mesh.py` still wrote `from oncotriage.paths import data_MeSH_path` at module scope, and a `from X import name` is an **attribute read**, so it fired the lazy resolver — meaning importing the *agent* globbed the whole sibling tree and raised on any machine without it. Check 2c now imports **every** package module in its own subprocess with the root pointed at a directory that does not exist. Note that no `open` trap could ever have caught this: `glob.glob` uses `os.scandir`.
  **The same trap applies to a numbered entry point's module scope**, which is why `07- FHIR Parser.py`, `09- MeSH Cancer Site Relevance Filter.py`, `13- LangGraph Agent.py` and `20- Drift Detection.py` import their lazy paths INSIDE the `__main__` guard.
- **Nothing calls `exec_chain`, calls `exec()`, or loads a module by location.** `exec_chain` no longer exists. The allowed `exec()`s live in a **closed allowlist**, each entry argued at `_EXEC_ALLOWLIST` in `tests/test_package_invariants.py` and each checked for staleness (an entry whose file no longer execs anything fails). **THE MEMBERSHIP IS NOT ENUMERATED HERE ANY MORE, AND THAT IS THE CORRECTION.** This note said "one", then "five", and was wrong by four the first time and by ten the second — a prose list of a set that grows every pass is a guaranteed staleness site, and it went stale three times. Read `_EXEC_ALLOWLIST` for the members and the argument beside each; it is the declaration the check actually enforces, so it cannot disagree with itself. Two shapes are in it: files that unparse pre-fix code out of a **git blob** so their negative controls run what actually shipped, and files that exec a **patched in-memory copy** of a shipped module to plant a defect their controls then require to fire. The two shapes are not interchangeable: git is right for code that was REPLACED, and a patched copy is right for a fix that is AT HEAD, where a git blob would compare the fixed module with itself. Section 1c enforces all of it with six planted controls.
- **A numbered file must not import a package name at module scope that it never reads.** That is what a re-export IS, and it is the first half of rebuilding a shim. Section 5 scans for it with a planted control. One exemption, argued: `24- Airflow Manager.py` imports two names its byte-verbatim COMMENTED menu calls, and comments are invisible to an AST walk.
- The three functions that used to read a value out of the shared namespace at call time — `get_model_cost`, `resolve_qdrant_collection`, `get_age_reference_date` — **took that value as an optional argument, and pass 20f-3 deleted all four parameters** (`pricing_config`, `client`, `collection_name`, `snapshot_date`). Re-measured by AST first: 29 call sites across the package, the entry points and the tests, not one passing any of them. **It is a behaviour change** — three public signatures narrowed, so an outside caller passing one now gets a `TypeError`. The one thing pass 20e said had to be settled first was `get_age_reference_date`'s docstring, which called its argument "the supported patch point"; it was not, and had not been since pass 20d-1 — `tests/test_fhir_birth_date_and_demographics.py` section 3 sets `config.DATA_SNAPSHOT_DATE`, which the function reads at **call** time. The private sentinel `_SNAPSHOT_NOT_SUPPLIED` went with the parameter.
- `pip install -e .` from `03- Code/` makes the package importable from anywhere. Without it, each entry point's own six-line block puts the code directory on `sys.path` and prints that it did.

Everything else worth knowing:

- To find a definition, grep across all `*.py` **and** `oncotriage/**/*.py`. There is no longer any file whose names arrive from somewhere else.
- Every entry point begins with the same **six-line package bootstrap**: `try: import oncotriage`, and on ImportError try `__file__`'s directory then the working directory, inserting the winner into `sys.path` and *printing* that it did. `pip install -e .` makes it a no-op. That is what makes `python "11- RAG Trial Indexer.py" --help` print an argument list without importing torch, transformers, streamlit and langgraph and without building an OpenAI and a Qdrant client.
- **There is nothing to double-load any more.** The old warning — "13 already chains 08, 09, 10, so callers of 13 must not list them again" — described `exec_chain`, and an `import` is idempotent.
- `_code_dir` is **derived from `__file__`** at the top of each entry point (item 20a); there is no hardcoded absolute path in any tracked file except `FALLBACK_MAIN_PATH` in `oncotriage/settings.py`, the deliberate one-machine fallback for `ONCOTRIAGE_MAIN_PATH`. Docker mounts the code at `/app` and `oncotriage/paths.py` switches all data paths on `IS_DOCKER`.

Adding a new script means: copy the six-line package bootstrap, put the logic in a package module, and leave a `__main__` block that imports what it calls. New constants go in `oncotriage/config.py`. **There is no other shape** — the alternative the old rule allowed ("unless the script genuinely has to feed the shared exec namespace") no longer exists.

## Running things

All commands run from `03- Code/`. Filenames contain spaces — always quote them.

```bash
# Pipeline services
python "17- FastAPI Server.py"                       # API on :8000 (/docs)
uvicorn oncotriage.api.server:app --port 8000        # the same app, package route
python mcp_server.py                                 # MCP server on stdio (a client starts it; by hand it looks like a hang)
streamlit run "21- Streamlit Dashboard.py"           # dashboard on :8501
python "25- Batch Runner.py"                         # full-corpus run, no HTTP, checkpointed
python "25- Batch Runner.py" --clear-stop            # delete the STOP sentinel, then run (resume after a stop)
python "25- Batch Runner.py" --fresh                 # discard the checkpoint; RE-BILLS THE WHOLE COHORT
touch "<checkpoint dir>/STOP"                        # STOP A RUNNING BATCH CLEANLY; the run banner prints the path
python "15- Database Wipe All Tables.py"             # no-op unless Flag = True near its top
python "16- Database Query.py"                       # ~40 read-only queries; runs to the end since item 38

# Data + index build (one-time / weekly)
python "04- FHIR Generate Data.py"                   # Synthea JAR -> ~22k patients
python "04- FHIR Generate Data.py" --population 3000 --seed 1 --output-dir <scratch>
python "04- FHIR Generate Data.py" --module-only     # rewrite the ECOG module, no generation
python "05- FHIR Clean Data.py"                      # in-place DELETE of non-cancer patients
python "05- FHIR Clean Data.py" --dry-run            # report what it would delete, delete nothing
python "11- RAG Trial Indexer.py" --mode staging     # staging + atomic alias swap (default)
python "11- RAG Trial Indexer.py" --mode direct      # rebuilds in place, causes downtime
python "12- RAG Trial Indexer Validator.py"          # exit 1 on any CRITICAL check failure

# Airflow (orchestration) — run in this order the first time
python "22- Airflow Database.py"                     # airflow db migrate + check, rewrites airflow.cfg
python "23- Airflow DAG.py"                          # writes {airflow_path}/dags/trial_refresh_weekly.py
python "24- Airflow Manager.py" start                # argparse CLI (pass 20f-3): start | stop | status | trigger
python "24- Airflow Manager.py" status               # a bare invocation now prints usage and exits 2
python "24- Airflow Manager.py" trigger --password-stdin   # the only way to pass a password; never on argv

# Qdrant backup
python "29- Download Qdrant Data.py"                 # -> {data_path}/06- Qdrant Downloaded Data.../
python "29- Download Qdrant Data.py" --output-dir <scratch>

# Evaluation / monitoring
python "26- Ablation Study.py" --sample-size 30 --configs full_pipeline no_mesh_filter
python "26- Ablation Study.py" --summary-only        # report from existing ablation_results.db
python "27- Ablation Analysis.py"                    # tables + figures from ablation_results.db
python "27- Ablation Analysis.py" --db <scratch>/ablation_results.db   # analyse an isolated study; outputs land beside it
python "28- Select Evaluation Sample.py"             # 10 breast + 10 colon + 10 lung, seed 42
python "28- Select Evaluation Sample.py" --output-db <scratch>/sample.db
python "34- Cohort Selector Diff Read Only.py"       # LEGACY vs CURRENT selector, read only
python fixture_capture.py --scan-only               # cohort scan + selection, captures nothing
python fixture_capture.py                           # COSTS MONEY: 12 real end-to-end runs
python fixture_capture.py --resume                  # finish an interrupted capture; re-pays for nothing already current
python fixture_replay.py                            # free; exit 0 only if all 12 replay clean
python ragas_run.py --dry-run                        # free: counts, prices and the --resume preview
python ragas_run.py --resume                        # COSTS MONEY, but only for the pairs an interrupted run had not scored
python "20- Drift Detection.py"                      # KS / PSI / z-score vs 30-day baseline
python "06- FHIR Dataset Characterization.py"        # cohort tables + figures (item 9's source)
python "07- FHIR Parser.py"                          # smoke run: parse the corpus, print the count
python "09- MeSH Cancer Site Relevance Filter.py"    # rebuild the MeSH C04 + UMLS lookups
python "13- LangGraph Agent.py"                      # no-op unless RUN_TEST_ON_EXECUTE = True; COSTS MONEY

# Docker (all six services)
# TWO VARIABLES MUST BE SET FIRST OR EVERY COMPOSE SUBCOMMAND EXITS 1 NAMING
# THEM -- ONCOTRIAGE_AIRFLOW_SECRET_KEY and ONCOTRIAGE_AIRFLOW_FERNET_KEY. Put
# them in "03- Code/.env" (which is what compose reads for ${...}; it does NOT
# read "../05- Keys/.env"), or export them, or pass --env-file. The three
# routes and their costs are argued at x-airflow-environment in
# docker-compose.yml. `down` and `logs` need them too, not just `up`.
make up                                              # build + up -d; `make build` alone builds
docker compose logs -f fastapi
# The Airflow login is user `admin` with a GENERATED 16-character password, not
# `admin`/`admin` -- that pair never worked:
docker compose exec airflow-webserver cat /app/airflow_home/simple_auth_manager_passwords.json.generated
# A clean `docker compose down -v` + `up` leaves the API deliberately UNHEALTHY:
# its Qdrant volume is gone and an empty index raises rather than answering.
# The MeSH lookups need no manual copy. See "DOCKER CLEAN BRING-UP.md" §5.
ONCOTRIAGE_QDRANT_URL=http://localhost:6333 python "11- RAG Trial Indexer.py" --mode direct
```

**STOPPING A RUNNING BATCH: THE `STOP` SENTINEL.** A file named **STOP** in the
checkpoint directory (`08- Checkpoint/`, beside `batch_runner_checkpoint.json`;
`oncotriage/batch/runner.py:stop_switch_path()` is the one owner, and the
runner's own setup banner prints the absolute path on every run). It may be
empty -- `touch` is the documented gesture -- or carry a note, which is logged
and printed in the run's closing block. It is polled between patients, at the
checkpoint's own cadence, in BOTH passes:

* no further patient is STARTED; every queued one is cancelled before it can
  issue a billed call;
* patients already in flight run to completion and their rows are written;
* the checkpoint is current, so a resume skips exactly what was done;
* the RESAMPLE pass does not run at all;
* the `runs` row is finalized **STOPPED** -- a fourth terminal status, neither
  KILLED (the process died) nor FINISHED (the cohort was covered);
* the summary and both console report blocks print, and the process exits 0.

**THE SENTINEL IS NOT DELETED BY THE RUN THAT HONOURED IT, and the next run
REFUSES to start while it is there.** A self-clearing switch would let a cron
entry or a restart loop honour a stop nobody asked for that day and report
success every time. Deleting it is the resume gesture; `--clear-stop` does it in
the same command.

**THREE WAYS TO STOP A RUN, AND THEY ARE DIFFERENT REQUESTS.**

| gesture | needs | run row | exit | resample pass |
|---|---|---|---|---|
| `touch <checkpoint dir>/STOP` | a shared filesystem | **STOPPED**, or FINISHED if the main pass had already covered the cohort | 0 | never entered |
| Ctrl-C | a terminal | KILLED | 130 | never entered |
| SIGTERM (`docker stop`, systemd) | a pid | KILLED | 143 | never entered |

**A STOP THAT LANDS IN THE RESAMPLE PASS IS NOT A STOPPED RUN (the pre-migration
pass).** `main()` read `STOP_SWITCH.requested` at four sites, which is a
question about whether a sentinel was SEEN and not about whether the cohort was
COVERED. Drive a 40-patient corpus to completion and write the sentinel while
the resample pass is running and the switch latches -- but the main pass had
already run every patient. The run was recorded STOPPED, whose entire meaning is
"this campaign covers a PREFIX of the cohort, so no rate computed over it is a
rate about the cohort", and the checkpoint was KEPT "because patients remain"
with none remaining. `run_batch`'s second return member is now the honest
answer -- `stop_unsubmitted == 0 and batch_cancelled == 0`, the only two ways a
patient can be left unattempted -- and `main()` reads it. The stop is still
ANNOUNCED either way; what changes is which of the two things it is reported as
having cut short.

**STARTING A CAMPAIGN ON A FRESH DATABASE -- ARCHIVE, DO NOT DELETE, AND DO IT
BEFORE THE FIRST BILLED CALL.** The migrations in
`oncotriage/storage/database_logger.py` are additive, which is right for a
database being carried forward and wrong for the file a campaign's published
numbers are computed from. Three reasons that schema cannot fix in place: NULL
is ambiguous across an era boundary (`matching_call_mode IS NULL` means "written
before era 3" for some rows and nothing at all for others, and `run_id` is NULL
on every row written before run tracking); `SQLITE_PAGE_SIZE` reaches a database
only at CREATION, so an existing file keeps 4096 until it is VACUUMed; and the
journal mode converts on first open, so a carried-forward file spent its history
in whatever mode it was created in. The procedure is two commands and the second
is the ordinary one:

```bash
mv "02- Data/03- Inferences Storage/inferences.db" \
   "02- Data/03- Inferences Storage/inferences-2026-08-archive.db"
python "25- Batch Runner.py"       # the first write builds the new file
```

The first write is `start_run_record`, which calls `initialize_database`, which
creates a file with **all** columns present from the first row, all five tables,
WAL from the first write, `page_size` = `SQLITE_PAGE_SIZE`, both header stamps
(`user_version` = `SCHEMA_USER_VERSION`, `application_id` =
`ONCOTRIAGE_APPLICATION_ID`) and every index. Nothing else has to be done and no
migration is run. **The archive is MOVED, not deleted**: it is the only copy of
every historical row and every query in `oncotriage/storage/queries.py` still
reads it through `--db`.

**AND A DATABASE FROM A NEWER SCHEMA ERA IS NOW REFUSED, LOUDLY, WITHOUT BEING
TOUCHED.** `initialize_database` reads `PRAGMA application_id` and
`PRAGMA user_version` as its FIRST statements -- above the page size and above
the journal mode, both of which write the header -- and raises
`IncompatibleDatabaseError` (a `RuntimeError` subclass, so a broad
`except sqlite3.Error` cannot eat it) when the file is another application's, or
when its era is HIGHER than this code's. It is permissive DOWNWARD, which is
what the additive migration is for. This REPLACES a branch that left the stamp
alone and carried on writing; that branch rested on "this schema is strictly
additive", which is a true statement about the eras that EXIST and a promise
about eras that do not, made by the code that cannot see them.

**ONE RUN AT A TIME, PER CHECKPOINT DIRECTORY.** Nothing stopped two
invocations from starting against one directory: both read the same resume state
and both processed the SAME patients at one live Stage 5 call each, silently.
`25- Batch Runner.py`'s guard takes an exclusive `flock` keyed on the checkpoint
directory, held for the process's life and released by the KERNEL however it
exits, and a second invocation exits **3** naming the holder's pid, host, user
and start time having touched nothing. See THE RUN LOCK in
`oncotriage/batch/runner.py` for why it is not a pid file, and why the key is
the checkpoint directory rather than the code directory.

**THE STALE-SENTINEL PREFLIGHT RUNS ABOVE `--fresh`.** It lived only inside
`main()`, which is called after the guard has processed its flags -- so `--fresh`
beside a stale sentinel deleted the checkpoint and THEN refused, printing
"NOTHING HAS BEEN RUN AND NOTHING HAS BEEN BILLED" over a cohort the next run
would re-bill in full. `--clear-stop` SATISFIES the preflight rather than being
blocked by it: it is the resume gesture the refusal itself names, and the line
is destructive/non-destructive, not "any flag".

**A CHECKPOINT THAT COULD NOT BE WRITTEN IS COUNTED.** `save_checkpoint`'s and
`append_result`'s `except OSError` printed and moved on, touching no counter --
so a read-only checkpoint directory produced a run whose degradation block said
CLEAN and whose closing line said the checkpoint had been kept. Both now count
under `write:{Type}` (and `tmp_unlink:{Type}`) into the already-registered
`CHECKPOINT_FAULTS` / `RESULTS_FILE_FAILURES`, so the run-end block and
`run_metrics` get it for free, and every checkpoint verdict `main()` prints goes
through `describe_checkpoint_state()`, which reports **STALE** or **ABSENT**
rather than "kept".

**Ctrl-C USED TO BE ABSORBED AND THE RUN CARRIED ON SPENDING.** Both pool
handlers caught the `KeyboardInterrupt`, printed "Checkpoint saved. Safe to
resume." and **returned normally** -- so `main()` went straight into the
RESAMPLE pass at one live billed Stage 5 call per patient (`RESAMPLE_COUNT` is
100) and then finalized the run **FINISHED**, making an interrupted campaign
indistinguishable from one that covered its cohort. Both handlers re-raise now.
Measured, both arms, in `tests/test_runner_stop_switch.py` section 7: on that
harness's 40-patient corpus the pre-fix form makes **12** further billed calls
after the interrupt and records FINISHED, and the shipped form makes **0** and
records KILLED. In production the pre-fix number is
`min(RESAMPLE_COUNT, patients completed)`, so up to 100.

**`STOPPED` IS A TERMINAL STATUS THIS TABLE HAS AND MLflow DOES NOT.**
`RUN_RECORD_TERMINAL_STATUSES` is no longer value-identical to
`tracking.RUN_STATUSES`; the divergence is DECLARED as
`RUN_RECORD_STATUSES_BEYOND_TRACKING` and
`tests/test_storage_run_identity.py` asserts the exact composition in order, so
a status added to one side and named in neither still fails. `tracking.end_run`
receives MLflow's **KILLED** for a stop -- its own "run killed by user", the
closest true statement that three-member vocabulary can carry; passing
"STOPPED" would be silently replaced by FAILED. `campaign_summary` treats a
STOPPED-then-resumed chain exactly like a KILLED-then-resumed one, because
`CAMPAIGN_RESUMABLE_STATUSES` gained the member and the SQL predicate is
generated from it.

```bash
# The eleven component tests (pass 20d-1). No quoting: the names have no spaces.
# None needs a network, a key, a live server, or a cent of spend.
python tests/test_extraction_histology.py                          # 133 (was 103; the promotion pass added Test 9, the two lung abbreviations' case-insensitivity)
python tests/test_agent_mesh_boost_and_quality_gate.py             #  89 (was 54; the two-knob quality gate added Test 7)
python tests/test_registries_mesh_pan_cancer_resolution.py         #  58
python tests/test_registries_cancer_codes_and_stage_extraction.py  # 136
python tests/test_agent_ablation_flag_passthrough.py               #  39
python tests/test_storage_inference_logging_contract.py            # 101 (was 79 when this line was written, then 98; the token-persistence pass added Test 2's three scoping/spread checks)
python tests/test_agent_retrieval_observability.py                 # 103
python tests/test_fhir_birth_date_and_demographics.py              # 172
python tests/test_fhir_ecog_surfacing.py                           # 113 (was 108; the pre-diagnosis ECOG pass made section 7's present-but-unusable assertion name the FAMILY rather than one member -- the scratch corpus's only unusable path is now all_before_primary_diagnosis and all_after_reference_date occurs zero times there, so the old one-member check was about to fail for a reason unrelated to what it tests. This line said 105, and the file has not reported that since the ECOG-surfacing checks were extended; MEASURED 2026-08-20); needs 04-'s scratch corpus
python tests/test_storage_ecog_logging.py                          # 155 (this line said 104 and was stale by 51; MEASURED 2026-08-20). Needs 04-'s scratch corpus too
python tests/test_monitoring_ecog_availability_drift.py            # 111 (was 112; see pass 20e)

# The rest of the suite (pass 20d-2). Same shape, same directory.
python tests/test_registries_cancer_code_claims_audit.py           # 197
python tests/test_registries_cancer_code_claims_audit_control.py   #  16; 14 planted, 14 caught
python tests/test_config_snapshot_date_rot.py                      #  10; 6 subprocess runs, ~6 min
python tests/test_package_invariants.py                            # 260/0/0 on macOS (was 247 before section 2f(iii)); 245/2/2 on Linux was measured at 247 and has not been re-measured there (was 234/6 there before commit ec2033a gave it a SKIP mechanism). No network, no keys, no corpus. NOT in CI — see below
python tests/test_degraded_dependencies.py                         # 174 (was 172 in this note, and 170 before pass 20e; the 172 was never true of the file). Item 11a
python tests/test_storage_query_layer.py                           # 434 (was 427; the pre-migration pass added section 8b-l over campaign_summary's patient/row split and the resample-bearing fragment its seed needed); item 38, temp SQLite only

# The four added by pass 20f-1. Same shape, same directory, no network, no keys,
# no spend, and none of them writes anything in the repository.
python tests/test_paths_glob_determinism.py                        #  25
python tests/test_storage_wipe_all_tables.py                       #  22
python tests/test_fhir_parser_dict_input.py                        #  31 (was 28 passed / 1 FAILED on a developer tree -- bucket E, so CI never ran it. The API-shutdown-gate pass re-added `import os` to oncotriage/api/server.py for its async-signal-safe os.write(2, ...) and left this file asserting the server imports neither os nor tempfile. The import ban was a PROXY for 'no temp-file round trip'; the pre-diagnosis ECOG pass replaced it with the property itself -- no filesystem call is reached anywhere in the module -- which is strictly stronger and survives)
python tests/test_ablation_db_isolation.py                         #  72 (was 43; pass 20f-3 added section 5b for the --db parent guard and the checkpoint)

# The portability pass. Same shape, same directory. No network, no keys, no
# spend, no live Qdrant, no corpus, no database, no git history -- and NO MODEL
# IS LOADED: ONCOTRIAGE_DEFER_LOCAL_MODELS is set above the imports and section
# 8p asserts torch and transformers never entered sys.modules (fastembed and
# huggingface_hub deliberately are NOT on that list -- qdrant_client imports
# both at module scope, which is the fact section 8 exists to measure). NOT in
# the collision matrix: every project root it resolves is FABRICATED under a
# tempfile.mkdtemp it removes and asserts gone, reached by seeding
# paths._RESOLVED and restoring it, and the five repository files it reads
# (paths.py, fixtures/capture.py, evaluation/run_harness.py, agent/deps.py,
# embedding.py) are written by neither of the suite's two writers. It EXECS
# NOTHING. Bucket A, ~2.6 s.
python tests/test_paths_portability_roots.py                       # 103

# The render-snapshot test (pass 20f-5, extended by 20f-6). Same shape, same
# directory, no keys, no spend, and "no network" is now MEASURED rather than
# claimed. It reads a SEEDED SCRATCH database and never the production one,
# writes nothing in the repository, and is not in the collision matrix.
python tests/test_dashboard_reproducibility_tab.py                 # 200 (was 163; pass 20f-6 added the template-pool controls, the offline guard and the enrichment-divergence check); ~1.7 s
python tests/test_dashboard_reproducibility_tab.py --update-snapshot  # regenerate the golden snapshot ON PURPOSE

# The run-reader pass. Same shape, same directory. No network (MEASURED: every
# render runs with socket.connect/connect_ex/create_connection/getaddrinfo
# replaced by a recorder that RAISES, with a control that makes a real call),
# no keys, no spend, no live Qdrant, no model load, no corpus, no git history,
# and NOT in the collision matrix -- six scratch databases inside a
# tempfile.mkdtemp it removes, paths._RESOLVED repointed and restored, and the
# two package files it reads (dashboard/tabs/run_health.py, dashboard/data.py)
# are written by neither of the suite's two writers. It EXECS NOTHING: its
# eight plants are COPIES written to that temp directory and imported from
# there. UNLIKE the reproducibility tab's test it has NO GOLDEN SNAPSHOT and is
# therefore not pinned to a streamlit version's element vocabulary -- see its
# docstring for why a snapshot recorded on day one of a NEW tab would be the
# "correct by definition" shape that file's own rule forbids. ~0.9 s.
python tests/test_dashboard_run_health.py                          # 196 (was 192; the pre-migration pass added 8f over the campaigns panel's patients/rows split)

# The campaign pass. Same shape, same directory. It is the ONLY thing in this
# project that renders oncotriage.dashboard.app:main() -- the ten-tab wiring
# had no test at all, so a tab dropped or renamed failed nothing -- and it is
# also where the null-resilience of the tabs is measured, because both are
# answered by rendering and one seeded database serves both. No network
# (MEASURED: every render runs with socket.connect / connect_ex /
# create_connection / getaddrinfo replaced by a recorder that RAISES, with a
# control that makes a real call and is NAMED in the record), no keys, no
# spend, no live Qdrant, no model load, no corpus, no git history, no live
# server. NOT in the collision matrix -- it writes only inside a
# tempfile.mkdtemp it removes, and the six repository files it reads are
# written by neither of the suite's two writers and are sha256-compared at the
# end. It EXECS NOTHING: every plant is a COPY in that temp directory. ~2 s.
python tests/test_dashboard_app_integration.py                     # 110 (this line said 155 and was stale by 12 before the campaign pass, which added section 8 over the campaigns panel and its two plants; MEASURED 2026-08-23)

# The call-mode-labelling pass. Same shape, same directory. No network, no
# keys, NO SPEND -- the API sections install a stub Qdrant client and a stub
# MeSH filter through oncotriage/agent/deps.py and the graph is compiled but
# NEVER INVOKED; the dashboard sections touch no client at all. No live Qdrant,
# no model load, no corpus, no git history, no live server, no Docker daemon.
# NOT in the collision matrix: every database is built by the project's own
# initialize_database() inside a tempfile.mkdtemp it removes and asserts gone,
# paths._RESOLVED is repointed and restored, and the eight package files it
# reads are written by neither of the suite's two writers and are
# sha256-compared at the end. It EXECS NOTHING and loads no module by location
# -- every plant is a COPY written to the temp tree and imported from there --
# so it needs no _EXEC_ALLOWLIST entry. Its ONE skip is section 9c's
# non-degeneracy probe on the production inferences.db, which a runner does not
# have; the COMPARISON it qualifies is NOT gated, so a run that CREATED a
# production database still fails there (test_storage_write_durability.py's
# gating shape, adopted for its reason). Bucket A, ~12 s.
python tests/test_api_call_mode_and_db_health.py                    # 151/0/0 on the developer tree; 150 passed / 0 failed / 1 SKIPPED against ONLY the CI directory skeleton

# The counter-reader pass. Same shape, same directory. No network, no keys, no
# spend, no live Qdrant, no model load, no corpus, no git history, and NOT in
# the collision matrix -- it writes nothing outside a tempfile.mkdtemp it
# removes and asserts gone, and the four package files it reads
# (degradation.py, retrieval/indexer.py, ablation/study.py, batch/runner.py)
# are sha256-compared at the end and are written by neither of the suite's two
# writers. It EXECS NOTHING: every control is a different INPUT to a function
# of its argument -- including a control MODULE written to that temp directory
# and PARSED, never imported -- an ast walk, or a registry entry removed inside
# try/finally with the restore asserted. Bucket A, ~3 s.
python tests/test_degradation_counter_readers.py                    # 157 (was 155; the pre-diagnosis ECOG pass added ECOG_ANCHOR_COUNTS to _READER_EXEMPTIONS on the parser's other four counters' footing -- a census read by load_all_patients(), whose own pass has an end where oncotriage/degradation.py's does not -- and the table drives its own follow-up checks. Before that 154; the de-identification pass added DEID_CENSUS to the census registry -- a capped age is the stage working -- and derived the "All N census counters are zero" number from the registry instead of the literal 4 it had gone stale as. Before that 152; the API-shutdown-gate pass added SHUTDOWN_GATE_DEGRADATIONS to _READER_EXEMPTIONS on oncotriage/mcp/server.py's TOOL_FAILURES precedent -- a long-lived SERVER has no run end, and degradation.py binds counter OBJECTS, so registering it would put FastAPI in every batch run's import graph. Before that 138; the operator-control pass added the two new dual-owned counters and replaced the adjacency pin on report_checkpoint_faults with a transitive call-graph walk)

# The Docker pass. Same shape, same directory. No network, no keys, no spend,
# and no Docker daemon: every Qdrant client is a stand-in and section 1's
# subprocesses import oncotriage.config only. Not in the collision matrix.
python tests/test_docker_qdrant_override_and_readiness.py           # 122

# The resume pass. Same shape, same directory. No network, no keys, no spend,
# no live judge, no live Qdrant, no corpus, no git history, and NOT in the
# collision matrix. It DOES exec -- one in-memory copy of
# oncotriage/fixtures/capture.py with the --resume gate reverted to "the file
# exists, skip it", argued at _EXEC_ALLOWLIST. Bucket A, ~6 s.
python tests/test_resume_capture_and_ragas.py                       # 210 (was 207; the default-flip pass clears capture.main()'s process-global call-mode pin in drive_main's finally and added the three 2a-pin checks that make the clear a measurement)

# The MCP pass. Same shape, same directory. No keys and NO SPEND -- the judging
# is stubbed through oncotriage/agent/deps.py. It is NOT offline: sections 4, 5
# and 6 make real Qdrant round trips, because the readiness gate and the trial
# lookup are what it exists to prove. Not in the collision matrix. ~2 min.
python tests/test_mcp_server_stdio_contract.py                      # 142 (was 135; the logging pass's section 8c raised it, and said so 500 lines below while this line stayed at 135)

# The structured-logging pass. Same shape, same directory. No keys and NO SPEND
# -- section 8 drives all six stages of the real graph with the Qdrant client,
# the cross-encoder and the OpenAI client replaced through
# oncotriage/agent/deps.py. Not in the collision matrix. ~40 s.
python tests/test_observability_logging.py                          #  82

# The scrape-admission pass. Same shape, same directory. No network, no keys, no
# spend, not in the collision matrix -- but it DOES need git history, and in a
# tree without `.git` it aborts rather than failing (see its own section below).
# It also needs the UMLS-derived mesh_non_oncology_lookup.json, which is
# deliberately not vendored, so it is bucket E in ci_test_buckets.py.
# THIS COUNT WAS STALE BY 228 and the line above it said the opposite -- the
# file has grown with the criteria-split gate, the cross-encoder pass and the
# alias-ownership pass since 175 was measured. MEASURED 2026-08-20.
python tests/test_indexer_admission_filters.py                      # 403 (was 175 in this line, then 359 before section 4b)

# The promotion pass. Same shape, same directory. No network, no keys, no spend,
# no git history, and NOT in the collision matrix (it writes nothing anywhere --
# every plant goes into an in-memory copy, and the two source files it reads are
# written by neither of the suite's two writers). ~3 s.
python tests/test_agent_age_units_and_sex_filter.py                 # 112

# The AJCC M-category pass. Same shape, same directory. No network, no keys, no
# spend, no git history, no corpus -- every fixture in it is a literal dict --
# and NOT in the collision matrix. ~2 s.
python tests/test_extraction_stage_m_category.py                    # 134 (was 119; the staging-date pass added Test 1b -- the date of the cM1 that ANSWERED, never a sibling's -- and three controls)

# The CKD / non-oncology guard pass. Same shape, same conditions, same
# directory, also not in the collision matrix. ~2 s.
python tests/test_extraction_stage_non_oncology_guard.py            #  80

# The trial-verdict pass. Same shape, same directory. No network, no keys, no
# spend -- every model response is a literal served by a stub installed through
# oncotriage/agent/deps.py -- no git history, no corpus, and NOT in the
# collision matrix. ~25 s.
python tests/test_agent_trial_verdict_normalization.py              # 166 (was 165; the default-flip pass PINS this file to the retained GROUPED arm -- every scenario counts what ONE response did, and a per-trial stub serving N calls produces N of everything -- and counts the pin's release. Before that 165; this line said 161 and was stale by 4; MEASURED 2026-08-21)

# The emission-provenance pass. Same shape, same directory. No network, no keys,
# no spend, no git history, no corpus, and NOT in the collision matrix -- every
# plant goes into an in-memory copy of oncotriage/agent/evaluation.py or
# oncotriage/storage/database_logger.py, both hashed before any plant and
# compared at the end, and every database write goes to a scratch file in a temp
# directory that is asserted to differ from the production path, and removed at
# the end. ~1 s.
python tests/test_agent_emission_provenance.py                      # 185 (was 184; the default-flip pass PINS this file to the retained GROUPED arm -- its subject is the packer's per-CALL provenance, which per-trial mode bypasses -- and counts the pin's release. Before that 184; this line said 162 and was stale by 22; MEASURED 2026-08-21)

# The write-durability pass. Same shape, same directory. No network, no keys,
# no spend, no git history, no corpus, NOT in the collision matrix, and it execs
# nothing -- every control is driven through the real shipped module by creating
# the failing condition for real (an exclusive lock from a second connection, an
# unwritable path, a deleted row). ~4 s, most of it deliberate lock contention.
python tests/test_storage_write_durability.py                       # 111 passed / 0 failed / 1 SKIPPED against ONLY the CI directory skeleton, and 111/0/0 against the developer tree. BUCKET E -> BUCKET A (the signal-safe-restore pass): its single production-database non-degeneracy probe was keeping a hundred checks that need nothing at all out of CI, and that probe is GATED now on tests/test_dashboard_run_health.py's pattern -- nine controls, an AST pin on the gate's call site, and the 9c COMPARISON never gated, so a run that CREATED a production database still fails on a runner. THE SAME PASS FIXED 9c, WHICH COULD NOT FAIL: its BEFORE reading was captured on the line above the comparison, after every driver had run, so it was rows(db) == rows(db) microseconds apart -- measured, a planted mid-run write left it GREEN. The capture is at module scope now and the same plant makes it FAIL. (was 100; the run-identity pass split section 5c's lock-site pin into the comparison and its own non-degeneracy probe when the expected number stopped being retyped there)

# The reproducibility-hash pass. Same shape, same directory. No network, no
# keys, no spend, no git history, not in the collision matrix, and it execs
# nothing -- every control is a different INPUT to the shipped function, which
# is the natural control for a pure function of its argument. It DOES need the
# corpus (sections 4 and 7 parse real bundles read-only) and says so as a
# recorded failure rather than a silent skip. ~2 min, almost all of it parsing.
python tests/test_agent_patient_hash_coverage.py                    #  73 (was 71; the allergy-onset pass moved allergies.onset_date from the NOT-hashed loop to the hashed one -- the renderer prints it now, which creates the consumer the old exclusion's own argument turned on -- and added 3a-ii, the raw-stamp-versus-rendered-slice pin, with its non-degeneracy twin. Before that 69; the pre-diagnosis ECOG pass added 3d-i over the new ecog.primary_diagnosis_date sub-field -- NOT hashed, because it explains a refusal whose outcome already rides in `selection`, which is -- and 3d-ii, its non-degeneracy twin)

# The tracking pass. Same shape, same directory. No network, no keys, no spend,
# no live Qdrant, no corpus, no git history required, not in the collision
# matrix, and it execs nothing -- the missing-package control masks
# sys.modules['mlflow'], which drives the SHIPPED function because the import is
# deferred into it. ~1.4 s.
python tests/test_tracking_mlflow_index.py                          # 104 (was 99; the call-mode pass added the arm parameter and its both-directions drive)

# The token-persistence pass's BEHAVIOURAL half -- the structural half is
# Test 2 of tests/test_storage_inference_logging_contract.py, and neither
# replaces the other: an AST scan cannot see a value carried and then
# serialized wrongly, and a round trip cannot see a return never written.
# Same shape, same directory. No network, no keys, no spend, no live
# Qdrant, no corpus, no git history, and NOT in the collision matrix (it
# writes only inside a temp directory; the two package files it reads are
# sha256-compared at the end). It DOES exec -- five controls plant into
# in-memory copies of database_logger.py and evaluation.py, argued at
# _EXEC_ALLOWLIST. Bucket A, <1 s against only the CI skeleton.
python tests/test_storage_packing_and_cache_columns.py              # 125 (was 124; the default-flip pass PINS this file to the retained GROUPED arm -- llm_classifier_packed_chunks and llm_classifier_packing are NULL by design in per-trial mode -- and counts the pin's release)

# The provenance-persistence pass. Same shape, same directory. No network, no
# keys, no spend, no live Qdrant, no model load, no corpus, no git history, and
# NOT in the collision matrix -- it writes only inside a tempfile.mkdtemp it
# removes and asserts gone, and the four package files it reads
# (storage/database_logger.py, agent/evaluation.py, agent/response_schema.py,
# fixtures/capture.py) are sha256-compared at the end and are written by
# neither of the suite's two writers. It DOES exec: five controls plant into
# in-memory copies of database_logger.py and evaluation.py, argued at
# _EXEC_ALLOWLIST. Bucket A, ~2.5 s.
python tests/test_storage_provenance_persistence.py                 # 126

# The health-persistence pass. Same shape, same directory. No network, no keys,
# no spend, no live Qdrant, no model load, no corpus, no git history, and NOT in
# the collision matrix -- every database is a temp file, paths._RESOLVED is
# seeded so nothing can resolve to the production tree, and the three package
# files it reads (storage/database_logger.py, batch/runner.py,
# evaluation/sampling.py) are written by neither of the suite's two writers. It
# EXECS NOTHING: every control is a real failing condition created on disk, an
# alternative implementation written out for comparison, or an ast walk over a
# parsed source file. Bucket A, ~12 s (section 7 drives MAX_WORKERS threads
# through the flush behind a barrier while another inserts counter keys).
python tests/test_storage_run_metrics_flush.py                      # 124 (was 123; the duplicated-derivation pass had to stop locating the KILLED crash handler by searching ast.dump for the string 'KILLED' -- runner.py names the constant now -- and added the non-degeneracy probe that the handler was found at all)

# The run-identity pass. Same shape, same directory. No network, no keys, no
# spend, no live Qdrant, no model load, no corpus, no git history, and NOT in
# the collision matrix -- every database is a temp file, paths._RESOLVED is
# seeded so nothing can resolve to the production tree, and the two package
# files it reads (storage/database_logger.py, batch/runner.py) are written by
# neither of the suite's two writers. It EXECS NOTHING: every control is a
# different INPUT to a pure function, a real failing condition created on disk,
# or an ast walk over an in-memory copy. Bucket A, ~1.5 s.
python tests/test_storage_run_identity.py                           # 155 (was 142; the duplicated-derivation pass added F7's checks -- one derivation of the terminal status, read by BOTH the row and the console line, and the declared MLflow mapping -- and had to widen the status walk past ast.Constant, which would otherwise have passed VACUOUSLY over a main() that writes no status at all. Before that 139; the stop-switch pass replaced the terminal-status EQUALITY check with the composition tracking.RUN_STATUSES + RUN_RECORD_STATUSES_BEYOND_TRACKING, which still fails on a status added to one side and named in neither)

# The schema-guards pass. Same shape, same directory. No network, no keys, no
# spend, no live Qdrant, no model load, no corpus, no git history, no live
# server, and NOT in the collision matrix -- every database is inside a
# tempfile.mkdtemp it removes and asserts gone, and the two package files it
# reads (storage/queries.py, storage/database_logger.py) are written by neither
# of the suite's two writers and are sha256-compared at the end. THE PRODUCTION
# DATABASE IS NEVER OPENED, not even read-only: the pre-migration shape section
# 6 drives report() against is BUILT from database_logger's own constants by
# renaming and dropping columns on a fresh database, which is why the CI
# skeleton and the developer tree give the same number. It EXECS NOTHING: every
# control is a different INPUT to a pure function, a real database built into a
# real failing shape, or a module constant rebound inside try/finally with the
# restore asserted. Bucket A, ~1.6 s.
python tests/test_storage_schema_guards.py                          # 110 (this line said 101 and was stale by 2 before the call-mode pass, which added the era-record staleness pin and the arm's round trip to section 3b; MEASURED 2026-08-23)

# The crash-record / path-unification pass. Same shape, same directory. No
# network, no keys, NO SPEND, no live Qdrant, no model load, no corpus, no git
# history, no live server, and NOT in the collision matrix -- every database and
# FHIR file is inside a tempfile.mkdtemp it removes and asserts gone, and the
# two package files it reads (batch/runner.py, storage/database_logger.py) are
# written by neither of the suite's two writers and are sha256-compared at the
# end. It DRIVES THE REAL main() four times: a planted mid-batch crash, a clean
# run, a mid-run ONCOTRIAGE_INFERENCES_DB hijack and a fresh/resumed pair. The
# BM25 index, the graph, the tracking module and process_patient are stand-ins
# and THE GRAPH IS NEVER INVOKED, so no billed call is reachable; run_batch,
# _on_done, flush_health, start_run_record, finalize_run_record,
# reconcile_writes, print_summary and both crash handlers are the real thing.
# It EXECS NOTHING. Bucket A, ~11 s (two real thread pools per drive).
python tests/test_runner_crash_record_and_db_unification.py         #  65

# The SIGTERM pass. Same shape, same directory. No network, no keys, NO SPEND,
# no live Qdrant, no model load, no corpus, no git history, no live server --
# process_patient is a stand-in and THE GRAPH IS NEVER INVOKED. It DOES use
# subprocesses and real signals, which is the point: a signal cannot be
# delivered to the process asserting about it, and an in-process `raise
# SystemExit` would test the test rather than the shipped handler. main(),
# run_batch, _on_done, flush_health, start_run_record, finalize_run_record and
# both crash handlers are the real thing: the subprocess IS
# `python "25- Batch Runner.py"`, so the guard that installs the handler is the
# shipped one. The four stand-ins arrive through a `usercustomize` hook rather
# than runpy or exec, because test_package_invariants.py section 1c forbids
# loading a module by location -- unconditionally, with no allowlist escape --
# and it CAUGHT the first version of this file doing exactly that, inside a
# string literal. Every worker PARKS on a
# release file, so the started count is a statement about cancellation rather
# than about scheduling -- the first version slept instead and was measured
# FLAKY under bucket-A load. NOT in the collision matrix. It EXECS NOTHING: the
# one control is a copy of the package in a temp directory. Bucket A, ~6 s.
python tests/test_runner_sigterm_shutdown.py                        #  87 (was 86; the consolidation pass rewrote 3b-j's walk to cover BOTH modules -- `cancel_queued` moved to oncotriage/control.py, so a walk over the runner alone found two of four, reported an empty list and PASSED -- and added 3b-j2, its non-degeneracy probe. Before that 75; the pre-migration pass added section 3b, which reads the Stage 5 shutdown flag FROM INSIDE A LIVE WORKER -- the only place the question can be asked -- with an uninterrupted arm as its non-degeneracy control)

# The stop-switch pass. Same shape, same directory. No network, no keys, NO
# SPEND, no live Qdrant, no model load, no corpus, no git history, no live
# server -- process_patient, the BM25 index, the graph, the tracking module and
# run_fingerprint.current are stand-ins and THE GRAPH IS NEVER INVOKED. It uses
# a subprocess and a real SIGINT for one scenario, for the sigterm file's
# reason. run_fingerprint.current is the stand-in that file does NOT have, and
# it is forced: this file's patients SUCCEED (they must, or the resample pass is
# unreachable and the money case cannot be measured), and a successful patient
# makes the real save_checkpoint resolve the stamp over the wire. NOT in the
# collision matrix. It EXECS NOTHING. Bucket A, ~14 s.
python tests/test_runner_stop_switch.py                             # 140 (was 138; the consolidation pass added 1m-x/1m-y over the batch switch's REFUSAL to be armed -- the shared base offers `arm` for the study, and an inherited no-op would replace an AttributeError with a caller believing the switch watched a file it did not). Before that 133; the operator-control pass added the read-only-directory diagnosis and the three-member clear vocabulary. Before that 122; the pre-migration pass reversed scenario C -- a stop that lands in the RESAMPLE pass is FINISHED, not STOPPED -- and added section 5b, its control)

# The pre-migration pass. Same shape, same directory. No network, no keys, NO
# SPEND, no live Qdrant, no model load, no corpus, no git history, no live
# server -- process_patient, the BM25 index, the graph, the tracking module and
# run_fingerprint.current are stand-ins and THE GRAPH IS NEVER INVOKED. It uses
# REAL CONCURRENT SUBPROCESSES and a REAL SIGKILL on purpose: a lock released by
# the kernel cannot be observed from inside the process that held it. NOT in the
# collision matrix -- every database, checkpoint, sentinel and FHIR file is
# inside a tempfile.mkdtemp it removes and asserts gone, and the two repository
# files it reads (batch/runner.py, "25- Batch Runner.py") are sha256-compared at
# the end. It EXECS NOTHING. Bucket A, ~18 s alone / ~30 s under bucket-A load.
python tests/test_runner_preflight_and_state_faults.py              # 120 (was 116; the CI-green pass rewrote 1e-e's non-degeneracy probe, which asserted realpath != abspath and so was a statement about macOS's /var -> /private/var rather than about the code -- it FAILED on every hosted Linux runner while its subject matched. It CONSTRUCTS the symlink now, on tests/test_serial_runner_lock.py section 2's pattern. Before that 116, unchanged across the consolidation pass, which repointed five structural checks at oncotriage/control.py -- their subject moved and a walk over the runner would have found nothing and passed. Was 76; the lock-hardening pass added section 8 -- the symlink substitution, the unopenable lock, the UTC record and the stripped truncation guard -- and the symlinked two-process drive in section 5)

# The CI-hygiene pair. Same shape, same directory. Neither imports anything
# from the package -- their subjects are `.github/scripts/` and
# `.dockerignore` -- so neither needs the corpus, a key, a database, a live
# Qdrant, a Docker daemon or git history, and neither is in the collision
# matrix. Bucket A.
#
# The staleness one DRIVES THE REAL SCRIPT AS A SUBPROCESS rather than
# importing or exec'ing it, so the exit code (0/1/2, three different
# instructions to a human) is what is asserted and no _EXEC_ALLOWLIST entry
# is needed. ~0.9 s.
python tests/test_trivyignore_staleness.py                          # 181 (was 173; the cleanup pass split 13h into four checks and five controls when _ID_RE was tightened)
#
# The .dockerignore one CARRIES A SKIP COUNTER, and that is load-bearing
# rather than decoration: the only virtualenv this project has is untracked
# and self-ignored, so no hosted runner has one and the tree-dependent half
# records 2 SKIPS there instead of failing. Every control still runs on a
# runner -- they drive pure functions with fabricated inputs. ~0.04 s.
python tests/test_dockerignore_exclusions.py                        #  36 passed / 0 skipped here; 33 / 2 on a checkout with no virtualenv

# The RRF ownership pass. Same shape, same directory. No network, no keys, no
# spend, no live Qdrant, no model, no corpus, no database, NO GIT HISTORY (run
# green in a tree with no `.git` at all), and NOT in the collision matrix -- it
# writes nothing anywhere, its one plant goes into an in-memory `ast` copy, and
# the one repository file it reads, oncotriage/agent/retrieval.py, is written by
# neither of the suite's two writers. It EXECS NOTHING, so it needs no
# _EXEC_ALLOWLIST entry. ~0.8 s.
python tests/test_agent_rrf_config_ownership.py                     #  31

# The cross-encoder sequence-limit pass. Same shape, same directory. NO MODEL
# IS LOADED -- ONCOTRIAGE_DEFER_LOCAL_MODELS is set above the imports and
# section 4 asserts torch and transformers never entered sys.modules -- so no
# model download, no network, no keys, no spend, no live Qdrant, no corpus, no
# database, no git history, and NOT in the collision matrix (the one repository
# file it reads, oncotriage/agent/deps.py, is written by neither of the suite's
# two writers). It EXECS NOTHING: every control is a different INPUT to a pure
# function, plus one override installed inside try/finally and asserted
# removed. ~1.1 s.
python tests/test_agent_cross_encoder_sequence_limit.py             #  42

# The per-trial-call pass, extended by the cache-warmup pass and turned ON by
# the default-flip pass. Same shape, same directory. Stage 5 sends ONE billed
# call per patient-trial pair, and MATCHING_PER_TRIAL_CALLS_ENABLED now ships
# **True** -- that is the pipeline's design, ruled by the operator; grouped is
# retained behind the same switch as the migration's comparison arm. THE ARM IS
# ALWAYS SET EXPLICITLY IN THIS FILE and the flip moved no assertion in it
# except check 1a, which is where the shipped decision is written down. No network, no
# keys, NO SPEND (every response is a literal served by a stub installed
# through oncotriage.agent.deps), no subprocess, no fixture, no git history, no
# corpus, no model, no live server.
# The scheduling assertion -- the WARMUP has COMPLETED before any trial call is
# ISSUED -- is an integer comparison over tickets the stub itself issued, never
# a measurement of elapsed time, so it does not depend on runner speed. It DOES open SQLite, in section 9b only, to round-trip the additive
# inferences.matching_call_mode column through the real writer; every database
# is a scratch file inside a tempfile.mkdtemp asserted to differ from the
# production path, removed at the end and asserted gone. NOT in the collision
# matrix -- it writes nothing in the repository, and the two files it reads
# (agent/evaluation.py, storage/database_logger.py) are written by neither of
# the suite's two writers and are sha256-compared at the end. It DOES exec:
# twenty-four in-memory copies of agent/evaluation.py, one plant each, argued
# at _EXEC_ALLOWLIST. Bucket A, ~4 s.
# The spend-gate pass. Same shape, same directory. No network, no keys, NO
# SPEND -- every model response is a literal served by a stub installed through
# oncotriage/agent/deps.py and the graph is never invoked -- no live Qdrant, NO
# MODEL LOAD (ONCOTRIAGE_DEFER_LOCAL_MODELS above the imports; torch and
# transformers asserted absent at the end), no corpus, no git history, no live
# server. It DRIVES THE REAL Stage 5 node AND THE REAL run_batch, with
# process_patient a stand-in that charges the ledger -- so the submit loop, the
# sweep, _on_done, _start_patient_unless_stopped and the executor lifecycle are
# the shipped ones. NOT in the collision matrix: every database is inside a
# tempfile.mkdtemp it removes and asserts gone, paths._RESOLVED is seeded so
# nothing can resolve to the production tree, and the four repository files it
# reads (agent/evaluation.py, spend.py, storage/database_logger.py,
# batch/runner.py) are written by neither of the suite's two writers and are
# sha256-compared at the end. It DOES exec: six in-memory copies of
# agent/evaluation.py, one plant each, argued at _EXEC_ALLOWLIST. Bucket A,
# ~1.6 s (MEASURED; the first version was 21.6 s because its own harness
# deadlocked and a timeout hid it -- see the pass's own findings).
python tests/test_spend_gate.py                                     # 152 (was 151; the spend-coverage pass moved 1j's SEED_SOURCES pin from two members to three -- `rater_state` is a third seed source, not a reuse of `campaign_rows` -- and added 1j-i, its distinctness probe. The pin stays EXACT, which is what makes a fourth member added without an argument fail there)

# The spend-coverage pass. Same shape, same directory. No network, no keys, NO
# SPEND -- every provider client is a stand-in, the ablation study's
# match_patient_ablation is a stand-in that CHARGES the ledger and issues no
# request, and the graph is never invoked. NO MODEL LOAD
# (ONCOTRIAGE_DEFER_LOCAL_MODELS above the imports; torch and transformers
# asserted absent at the end), no live Qdrant, no corpus, no git history, no
# live server, no Docker daemon. It DRIVES the REAL ablation `main()` to its
# cap and back, the REAL rater `submit_batches`, the REAL FastAPI endpoints
# through starlette's TestClient and the REAL MCP tool. NOT in the collision
# matrix: every database, checkpoint and plant lives inside a
# tempfile.mkdtemp it removes and asserts gone, paths._RESOLVED is seeded so
# nothing can resolve to the production tree, and the five repository files it
# reads (spend.py, ablation/study.py, evaluation/rater.py,
# evaluation/ragas_harness.py, config.py) are sha256-compared at the end. It
# EXECS NOTHING and loads no module by location -- the plant is a COPY written
# into the temp tree and PARSED, never imported. Bucket A, ~6 s.
python tests/test_spend_coverage.py                                 # 161

python tests/test_agent_stage5_per_trial_calls.py                   # 321 (this line said 320 and was stale by one; MEASURED 2026-09-01 against HEAD in a git worktree as well as against the working tree, so the correction is about the note rather than about any change. Was 318; the default-flip pass inverted 1a, added 1a-ii over the unpinned owner, derived 10e's restore from a value captured at import rather than a literal, and added 10f's non-degeneracy probe on that capture. Before that 283; the duplicated-derivation pass added section 1c over the import-time parallelism guard and section 5c over the answering-model check on the UNCONSUMED fold path. Before that 276; the operator-control pass rewrote 8b-r from a pinned limit to the grouped gate's contract and added c36. Before that 255; the pre-migration pass added section 8B over the Stage 5 shutdown flag and controls c32-c35. ~10 s: section 3d parks two workers for a bounded grace on each of its two arms)

# The harness-budget pass. Same shape, same directory. No network, no keys, no
# spend, NO LIVE SERVER and no live Qdrant -- it starts nothing and issues no
# request; Files 18 and 19 are read as TEXT and parsed, which is why a test
# whose subject is two bucket-D files is itself bucket A. No corpus, no
# database, no git history. NOT in the collision matrix: every plant goes into
# an in-memory ast copy and both harness files are re-read and compared at the
# end. It execs nothing (section 1 evaluates ONE arithmetic expression node
# through eval, which is not exec), so it needs no _EXEC_ALLOWLIST entry.
# ~0.9 s.
python tests/test_harness_endpoint_budget.py                        #  38

# The sample-naming pass. Same shape, same directory. No network, no keys, no
# spend, no live Qdrant, no model, no corpus, no database, no git history --
# and no sibling data tree either: paths._RESOLVED is seeded with a scratch
# results root, so no glob fires and default_output_db() never reaches it. NOT
# in the collision matrix -- it writes nothing outside a tempfile.mkdtemp it
# removes and then asserts gone, and the three repository files it reads
# (oncotriage/evaluation/sampling.py, oncotriage/evaluation/medcpt_calibration.py
# and "28- Select Evaluation Sample.py") are written by neither of the suite's
# two writers. It EXECS NOTHING -- the five plants are `ast` walks over
# in-memory copies -- so it needs no _EXEC_ALLOWLIST entry. ~1.0 s.
python tests/test_evaluation_sample_naming.py                       #  72

# The call-mode-pin pass. Same shape, same directory. No network, no keys, NO
# SPEND, no live Qdrant, NO MODEL LOAD (ONCOTRIAGE_DEFER_LOCAL_MODELS above the
# imports; torch and transformers asserted absent in-process AND in every
# subprocess), no corpus, no database, no git history, no live server. It DOES
# use four subprocesses -- oncotriage/fixtures/replay.py sets that variable at
# module scope, so importing it in-process would change the environment for
# every check after it, and a pin is process-global by design -- each handed
# ONCOTRIAGE_QDRANT_URL pointed at a closed port. It EXECS NOTHING and writes
# nothing anywhere, so it needs no _EXEC_ALLOWLIST entry and is NOT in the
# collision matrix; it DOES read oncotriage/config.py, which
# tests/test_config_snapshot_date_rot.py rewrites, so all three files it reads
# are sha256-compared at the end. Bucket A, ~4.8 s.
python tests/test_fixture_call_mode_pin.py                          #  82 (unchanged across the consolidation pass, which moved its closed-port literal to tests/_control_harness.py. Was 81; the default-flip pass found 1c's non-degeneracy probe pinned the LITERAL per_trial, which agrees with the default after the flip -- it pins the OPPOSITE arm, derived, and gained a cleared-pin check)

# The Bedrock-adapter pass. Same shape, same directory. NO AWS CALL AND NO
# BILLED CALL OF ANY KIND -- every client is a stand-in installed through
# oncotriage/agent/deps.py and every model response is a literal dict. No
# network (run_fingerprint.current() is deliberately NOT called: it probes the
# index over the wire), no keys, no spend, no live Qdrant, no model load, no
# corpus, no git history. NOT in the collision matrix -- it writes only inside
# a tempfile.mkdtemp it removes and asserts gone. It DOES exec: nine in-memory
# copies of oncotriage/agent/bedrock_adapter.py, one mapping broken in each,
# argued at _EXEC_ALLOWLIST (the module is new, so `git show` has no revision
# carrying a version with one mapping missing). ~2 s.
python tests/test_agent_bedrock_adapter.py                          # 281 (was 275; the Converse pass moved two pins -- the provider tuple 2 -> 3 members and call_matching_model's return count 2 -> 3 -- and re-asserted what each protected in a stronger form. Before that 273; the cache-warmup pass added the `**` expansion pin)

# The Converse pass: the SECOND Bedrock branch, Claude Sonnet 4.6. Same shape,
# same directory. NO AWS CALL AND NO BILLED CALL OF ANY KIND -- every client is
# a stand-in installed through oncotriage/agent/deps.py and every response is a
# literal dict. No network, no keys, no spend, no live Qdrant, no model load, no
# corpus, no git history, no database -- and NO boto3, which is asserted rather
# than assumed (the request builder and the response translator import no AWS
# library, and section 1c requires boto3 and botocore to be absent from
# sys.modules). It writes NOTHING anywhere, not even a temp directory. NOT in
# the collision matrix -- but it DOES read oncotriage/config.py, which
# tests/test_config_snapshot_date_rot.py rewrites in place, so all three files
# it reads are sha256-compared at the end and an interleaved serial run is
# visible rather than silent. It DOES exec: ten in-memory copies of
# oncotriage/agent/bedrock_anthropic_adapter.py, one plant each, argued at
# _EXEC_ALLOWLIST. Bucket A, ~0.8 s.
python tests/test_agent_bedrock_anthropic_adapter.py                # 261

# The Bedrock go-live probe. NOT a test, NOT in tests/, NOT in any bucket, and
# it REFUSES to do anything without its flag (exit 2, nothing called, nothing
# billed). It is day one's first command.
python bedrock_probe.py                                  # prints the refusal, exit 2
python bedrock_probe.py --i-understand-this-bills        # COSTS MONEY: 2 live calls
python bedrock_probe.py --i-understand-this-bills --probe-seed   # + 1 more
# The SECOND Bedrock branch (Claude Sonnet 4.6 over Converse). A DIFFERENT
# lettered list -- A1..A10 at the top of
# oncotriage/agent/bedrock_anthropic_adapter.py -- because both lists have an
# item about structured output and they are about different APIs. NOT RUN YET:
# every A-item is documentation until it is.
python bedrock_probe.py --i-understand-this-bills --provider bedrock_anthropic
python bedrock_probe.py --i-understand-this-bills --provider bedrock_anthropic \
    --probe-truncation                                   # + 1 more, settles A7
# PER-TRIAL ON THE CONVERSE BRANCH -- three more calls, settling A11 (is the
# warmup's `maxTokens = 1` shape accepted) and A12 (does the warmup's write get
# reported, and do the calls behind it read it). READ THE ANSWER OUT OF THE
# USAGE BLOCK, never the wall clock. The built-in prefix is BELOW Bedrock's
# 1,024-token cache floor, so point --per-trial-prefix-file at a real rendered
# system prompt before drawing any conclusion about the cache.
python bedrock_probe.py --i-understand-this-bills --provider bedrock_anthropic \
    --probe-per-trial                                    # + 3 more
python bedrock_probe.py --i-understand-this-bills --provider bedrock_anthropic \
    --probe-per-trial --per-trial-prefix-file <a real rendered system prompt>

# ═══════════════════════════════════════════════════════════════════════════
#  GO-LIVE, PER-TRIAL: NO PAID RUN BEFORE THE THREE-CALL PROBE.
#  MATCHING_PER_TRIAL_CALLS_ENABLED SHIPS **True** AND TWO OF ITS PREMISES
#  HAVE NEVER BEEN OBSERVED AGAINST THE LIVE PROVIDER.
# ═══════════════════════════════════════════════════════════════════════════
#  The probe is THREE CALLS and answers both, out of the USAGE BLOCK and never
#  the wall clock. It is the migration window's FIRST command, on
#  bedrock_probe.py's footing -- a deliberate, flagged, tiny spend that settles
#  a configuration question before a campaign's worth of money rests on it:
#
#    1. WARMUP ACCEPTANCE -- does MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS
#       = 1 come back 200 or 400? A reasoning model bills reasoning against
#       that ceiling. A 400 does NOT stop the campaign: evaluation.py
#       classifies it and falls back to the retired one-then-rest schedule PER
#       PATIENT, with no process memo -- $0 in refused warmups (a 400 is
#       refused before generation) and ~22 minutes added over 1,000 patients
#       at MAX_WORKERS = 12.
#    2. PREFIX WARMING -- after two identical-prefix trial calls, does call 3
#       report cached_tokens > 0? If the provider does not cache this prefix,
#       per-trial costs MAX_TRIALS_FOR_EVALUATION x the grouped input price and
#       NOTHING RAISES: every request succeeds, every verdict is produced, and
#       the only trace is cached_tokens reading 0 in
#       inferences.llm_classifier_call_details.
#
#  AND THE FIXTURE GATE DOES NOT COVER THE SHIPPED ARM. fixture_capture.py and
#  fixture_replay.py PIN themselves to grouped and print that they did, so the
#  twelve fixtures characterize the GROUPED arm. PER-TRIAL FIXTURES ARE THE
#  STANDING MIGRATION ITEM -- RecordingSink numbers Stage 5 recordings by
#  ARRIVAL, so a per-trial capture's 'deterministic' prefix would be ordered by
#  the thread scheduler; closing it needs a trial-stable ordering for the
#  chat_completions bucket plus a paid recapture of all twelve, a fixture-
#  FORMAT change with a SCHEMA_VERSION bump. Until then the shipped arm's
#  Stage 5 behaviour is covered by
#  tests/test_agent_stage5_per_trial_calls.py alone.

# Fixture state, CURRENT rather than as of any pass below: SCHEMA_VERSION
# is 8, the twelve recordings on disk are v8, and `python fixture_replay.py`
# is 12/12 clean with no recapture. Two accounts further down state
# `SCHEMA_VERSION` is 3 and one states the recordings are unreadable; both
# were true when written and are kept as written, per the rule that a
# past-tense account keeps its wording.


# The secret-gate pass. Same shape, same directory. No network, no keys, NO
# SPEND, no live Qdrant, no model load, no corpus, no database, no Docker
# daemon, no live server. It needs `git`, which every runner has. Its subject is
# .github/scripts/secret_scan_gate.py and .githooks/pre-commit, both DRIVEN AS
# SUBPROCESSES (the hook through a real `git commit`), so the EXIT CODE is what
# is asserted -- 0/1/2/3 are four different instructions to a human and an
# in-process call produces none of them. Every planted secret is ASSEMBLED at
# run time from a prefix and an index arithmetic; section 8 scans this file with
# the scanner it tests and greps every tracked file for each value it can
# generate, and it CAUGHT ITS OWN AUTHOR (the first draft carried AWS's
# documented example key as a literal). The gitleaks half is SKIPPED, counted
# and printed when the binary is absent -- which is the state of the `tests` job
# on a hosted runner. NOT in the collision matrix. It EXECS NOTHING. Bucket A,
# ~22 s with gitleaks / ~16 s without.
python tests/test_secret_scan_gate.py                               #  92 with gitleaks; 86 passed / 0 failed / 3 SKIPPED without it
#   (was 87/81; the CI-green pass added 2a-0 and 2c-b..2c-e over the oid
#   validation parse_fingerprint gained. Check 4f used to fail on a hosted
#   x86_64 runner ONLY: it harvests --emit-accepted through that parser from
#   stdout AND stderr, and onnxruntime writes a colon-rich device-discovery
#   warning to stderr on that hardware, which a colon count alone read as a
#   fingerprint. The new checks fail on revert with no environment needed.)

pip install -e .                                         # makes `oncotriage` importable anywhere
```

**THE COLLISION MATRIX IS DERIVED FROM THE CODE, NOT DECLARED (pass 20d-2).**
Every candidate was walked for the calls that can WRITE a repository file
(`open(..., "w"/"a")`, `os.replace/remove/rename`, `shutil.copy*/move/rmtree`,
`Path.write_*`) and for those that READ one, with each path expression resolved
through the file's own module-level assignments. `str.replace` had to be
separated from `os.replace` — matching on the bare name conflates them, and the
first version of the derivation reported writes that do not exist.

**There are exactly TWO writers in the whole suite**, and everything else is
read-only with respect to the repository:

| Writer | Writes | Restored by |
|---|---|---|
| `tests/test_registries_cancer_code_claims_audit_control.py` | `oncotriage/registries/cancer_code_registry.py`, and `rmtree`s its `__pycache__` | `shutil.copy2` from a backup taken at start, sha256-verified per case and at the end |
| `tests/test_config_snapshot_date_rot.py` | `oncotriage/config.py` | same shape |

Membership follows from the intersection, **in either direction**:

- **audit × audit control** — the audit extracts the inline comment beside every
  code in `cancer_code_registry.py` as the claim under audit; the control plants
  defects into that exact text.
- **package invariants × both** — it `copytree()`s the whole package in five
  checks, which brings both written files along, and check 4 then rewrites the
  snapshot date in its own copy.
- **degraded dependencies × audit control** — **NEW IN PASS 20d-2, and the
  derivation is what found it.** This file was excluded on the "edits no file"
  rule, which is true and is only half the rule: a file that writes nothing
  cannot corrupt anyone, but it can still BE corrupted. It asserts
  `sorted(_p) == ["C34.10", "C50.911", "C97"]` on the ICD-10 seed and exercises
  SNOMED `254837009` — and the control plants into **both** of those exact
  regions (case 4 is `C97 -> C99`, case 12 is `254837009 -> 396275006`).
- **storage query layer STAYS OUT**, checked rather than carried forward: it
  reads `queries.py`, `agent/retrieval.py`, `agent/terminal.py`, the cost tab and
  File 16 — none of them written by either writer — and the only config values
  it imports are `RRF_POOL_SIZE` and `TOP_K_CANDIDATES`, which the snapshot-date
  rewrite does not touch. It writes only into a temp directory and reads history
  through `git show`.

```bash
make serial-tests          # runs the five, one at a time, ~9 min
make serial-tests-list     # prints the order and why, runs nothing
python tests/run_serial_tests.py   # the same thing without make
```

The order is load-bearing: the audit first against a pristine registry; the
control plants and restores; degraded dependencies immediately after that
restore, where an incomplete restore surfaces as its failure rather than as a
mystery later; the snapshot-date test; package invariants **last**, over a tree
every earlier file has put back, so a failure there means it found something
rather than that it caught a neighbour mid-edit. It runs all five and reports
every exit code rather than stopping at the first failure.

**NEVER EDIT THE REPOSITORY WHILE `run_serial_tests.py` IS RUNNING, and re-grep
anything Files 43 and 44 touch after a run.** Both restore from a copy taken at
their own START, so any edit made to `oncotriage/registries/cancer_code_registry.py`
or `oncotriage/config.py` while the runner is executing is **silently reverted**
when they finish — no error, no warning, and the file looks untouched. This is
not hypothetical: **pass 20d-1 lost an edit to `oncotriage/config.py` exactly
this way**, applied while File 44 held its backup, and found it only because a
later re-grep still reported the old string. The two files to re-check after any
serial run are those two.

File 47's per-module import sweep (check 2c) runs one subprocess per package
module through a `ThreadPoolExecutor`. Serially it took about nine minutes and
the module count has gone 26 → 33 (pass 3a) → 42 (pass 3b) → 50 (pass 3c-1,
which added thirteen dashboard modules, each of which imports streamlit in its
own subprocess) → 55 (pass 3c-2: the four orchestration modules and the
Qdrant backup) → **61** (pass 3d: the ablation pair, the two offline
measurements and the fixture harness); a test nobody runs
because it is slow protects nothing. A THREAD pool rather than a process pool is the right
tool, not a compromise: each unit of work is already its own subprocess, so the
parent thread spends its life blocked in `subprocess.run()` with the GIL
released.

**Tests** are not pytest — **including every file in `tests/`, whose `test_` prefix is for discovery and for whatever item 22 decides, not a claim of pytest compatibility.** Every check runs at module level and the exit code is set in a `__main__` block, so `pytest tests/` imports each module (running every check, printing every result) and then reports "no tests collected", exit 5. Non-zero, so it cannot read as a false green; still not how to run them. `18-` and `19-` are procedural scripts hitting a *live* server on `localhost:8000`; start `17-` in another terminal first. `19-` slices `fhir_files[410:412]` for a smoke run; widen that slice to go broader.

**FILES 18 AND 19 ARE TESTS THAT NO CONVERSION PASS HAS REACHED, and pass 20d-1 deliberately did not either.** They sit inside the pipeline numbering rather than with the other tests for a reason: they need a live server in a second terminal and **they cost money** — every POST is a live billed Stage 5 call, measured at $0.13–$0.17 per patient from six real rows in `inferences.db`. Converting them means deciding how a test that spends money is run and how its server is redirected, which is a spending and orchestration decision, not a mechanical move. Pass 20e.

**START THAT SERVER WITH `ONCOTRIAGE_INFERENCES_DB` SET, OR FILES 18 AND 19 WILL FAIL YOU (pass 20c-3i).**

```bash
ONCOTRIAGE_INFERENCES_DB=/tmp/oncotriage-test.db python "17- FastAPI Server.py"
```

`oncotriage/api/server.py` calls `log_inference(result, patient_data)` with no path — correctly; it is a server, not a test that knows where its output belongs — so it resolves to the production `inferences.db`. Files 18 and 19 POST **real** bundles to it, so every run of either was writing real inference rows and their `trial_matches` children into the real database. Six such rows are in it, dated 2026-08-05, three runs of two patients each. They surfaced only because they changed **which query File 16 dies at**; nothing reported them.

`ONCOTRIAGE_INFERENCES_DB` is named in `oncotriage/settings.py` and resolved by `resolve_inferences_db()`, which is **deliberately not `_from_env`** — that helper appends a trailing separator, correct for every directory and, for a database file, a path `sqlite3.connect` refuses with an `OperationalError` that `log_inference` *catches by design*. One trailing slash would have produced one "Database logging failed (non-critical)" line per patient and a run that recorded nothing while reporting success. Same reasoning as `resolve_airflow_password`, different victim. It strips whitespace, expands `~`, and **raises** if the parent directory is absent — resolution happens outside both writers' `try` blocks precisely so a configuration defect reaches the operator instead of being swallowed as a logging fault.

**Both** `resolve_inference_db_path` (`storage/database_logger.py`) and `resolve_drift_db_path` (`monitoring/drift.py`) honour it, at tier 2 of three: explicit argument → variable → `paths.inferences_path`. They stay separate functions — `monitoring` must not depend on `storage` for a path string — and both reach the variable through `settings`, the module that names it. **The argument still outranks the variable**, and that ordering is what keeps the six isolation tests meaningful: they pass an explicit scratch path and assert on what comes back, and a stray export that outranked the argument would have those assertions reporting the export as the answer they wanted.

Files 18 and 19 cannot redirect the server — it is a separate process with its own environment — so they **detect** instead, on the `_production_drift_rows()` precedent from File 41: read the production inference row count before the run, read it again after, exit 1 naming the variable if it moved. The comparison is shown to discriminate before it is trusted: each file builds a scratch database carrying the **production `inferences` schema, read out of `sqlite_master` rather than retyped**, seeds two rows, inserts a third, and refuses to run unless the counter reports 2 then 3. The connections are `mode=ro` URIs, because a plain `sqlite3.connect` on an absent path *creates* the file — a guard that brought its own database into existence, counted 0 twice and reported success would be worse than no guard. The block is duplicated verbatim in both files on purpose: item 20d converts them, and a self-contained harness belongs in that pass.

**File 19 runs two patients, not the corpus.** `main()` overwrites the file list with `fhir_files[410:412]` under a comment reading "For testing purposes", while its title, its `Found N patients` line and its `Batch evaluation complete` summary all describe a full-corpus run. Reported, documented in the file's docstring, and deliberately left — widening it is a spending decision. **Pass 20g did not widen it and did make it audible**: a slice that selects nothing is now a recorded failure and exit 1, because a corpus of fewer than 411 bundles used to produce `Success: 0/0`, `Errors: 0` and exit 0. Both files also state their cost in their docstrings: every POST is a live billed Stage 5 call, measured at $0.13–$0.17 per patient from the six rows above.

**BOTH FILES EXIT NON-ZERO WHEN THEY TESTED NOTHING (pass 20g).** File 19's `error_count` was printed and never read; File 18 had two silent-skip branches behind a corpus precondition. See "Files 18 and 19 exit non-zero when they tested nothing" for the ten-scenario stub demonstration and for why File 19's is a contract change rather than a bug fix.

To exercise the graph directly without the API, set `RUN_TEST_ON_EXECUTE = True` near the bottom of `13- LangGraph Agent.py` and run it as `__main__`. **IT COSTS MONEY** — one live billed Stage 5 call, $0.13–$0.17 — which is why it is edit-to-arm rather than a CLI flag; `fixture_replay.py` exercises the same six stages over twelve patients for nothing. The block survived the pass-20c-2c split and pass 20e's deletion of the shim around it; every import it needs is inside the guard AND inside the flag, so reading the file imports no model and resolves no path.

## Layout outside this repo

Only `03- Code/` is version-controlled. Sibling directories under the project root are resolved by **glob prefix** in `oncotriage/paths.py` (`sorted(glob.glob(main_path + "/*Data/"))`, via `_glob_one`, which names the pattern and the root when nothing matches **and raises naming every candidate when more than one matches** — pass 20f-1; see "The path glob is deterministic" below), so directories can be renumbered but not renamed past their suffix. The root itself comes from `ONCOTRIAGE_MAIN_PATH` or, unset, from `FALLBACK_MAIN_PATH` in `oncotriage/settings.py`:

| Path var | Sibling dir | Holds |
|---|---|---|
| `data_fhir_path` | `02- Data/…/Patients/fhir/` | Synthea patient bundles |
| `inferences_path` | `02- Data/03- Inferences Storage/inferences.db` | SQLite log (gitignored) |
| `data_MeSH_path` | `02- Data/…/MeSH/` | MeSH C04 + UMLS crosswalk JSONs |
| `keys_path` | `05- Keys/.env` | `OPENAI_API_KEY`, `QDRANT_URL`, `QDRANT_API_KEY` |
| `checkpoint_path` | `08- Checkpoint/` | batch runner resume state |
| `result_tracking_path` | `04- Results/06- MLflow Tracking/` | the MLflow file-backed tracking store (the tracking pass) |
| `testing_path` | `09- Testing/` | the parent of the two below; read by their resolvers and by nothing outside `paths.py` (the portability pass) |
| `testing_fixture_path` | `09- Testing/Characterization Fixtures/` | the twelve characterization fixtures. **Was a private glob in `oncotriage/fixtures/capture.py` that INVENTED `{root}/09- Testing` when nothing matched** |
| `testing_evaluation_path` | `09- Testing/Evaluation Runs/` | one timestamped directory per evaluation campaign. **Was the same private glob, in `oncotriage/evaluation/run_harness.py`** |
| `model_cache_path` | `02- Data/07- Model Cache/` | the MedCPT (836 MB, MEASURED) and FastEmbed caches. Under the DATA tree locally and `/opt/models/` in the container, which is why the name has no `data_` prefix |
| ~~`requirements_path`~~ | `07- Requirements/` | **DELETED (pass 20f-3)**, from both path tables, with the in-repo `requirements/` directory. It was read by no code, ever; `pyproject.toml` is the one dependency list. The stale sibling outside the repository is untouched and nothing resolves to it any more |

`ONCOTRIAGE_ALLOW_DEGRADED_REGISTRIES` permits a run to continue with the MeSH
site-relevance layer or the ICD-10-CM layer ABSENT rather than raising; it is
named in `oncotriage/settings.py`, does **not** go through `_from_env`, and does
**not** reach `oncotriage/fhir/clean.py`'s deletion path. See "Degraded
dependencies (item 11a)" below.

`ONCOTRIAGE_QDRANT_URL` moves the Qdrant endpoint, and `ONCOTRIAGE_QDRANT_API_KEY` the credential; both are named in `oncotriage/settings.py` and neither goes through `_from_env`. See "The Qdrant endpoint has one deliberate override (the Docker pass)" below — the short version is that `QDRANT_URL` in the environment is an ACCIDENT and still loses to the .env, because `load_env_keys()` pops it, and this one is a DECISION and wins.

`ONCOTRIAGE_INFERENCES_DB` overrides `inferences_path` for **both** database writers (`resolve_inference_db_path`, `resolve_drift_db_path`) and is the only way to redirect a running FastAPI server; it is named in `oncotriage/settings.py` and does **not** go through `_from_env`. See the Tests paragraph above for what it is for and why the helper would corrupt it.


`ONCOTRIAGE_S3_STAGING_REGION` and `ONCOTRIAGE_BEDROCK_REGION` move the two AWS
Regions this project names — the one `oncotriage/staging/` stages to and the one
interpolated into the Bedrock base URL. Both are named in
`oncotriage/settings.py`, both have their own resolver, and neither goes through
`_from_env`: that helper appends a separator, and `"us-east-1/"` is not a Region
— it lands inside a HOSTNAME
(`bedrock-runtime.us-east-1/.amazonaws.com`), which is a failure that names
neither the slash nor the variable. **They are the sixth and seventh victims of
that helper**, after `ENV_AIRFLOW_PASSWORD`, `ENV_INFERENCES_DB`,
`ENV_ALLOW_DEGRADED_REGISTRIES`, `ENV_LOG_LEVEL` and `ENV_BEDROCK_API_KEY`.
**The defaults stay in `oncotriage/config.py`** as `S3_STAGING_REGION_DEFAULT`
and `BEDROCK_REGION_DEFAULT`, both still `us-east-1`, so nothing moves for
anybody who sets nothing. **The resolvers never raise** — both are resolved at
config's MODULE SCOPE, so a raise would make `import oncotriage.config` fail for
every process in the project over a typo in a variable that concerns two of
them; validation of the VALUE stays lazy and provider-gated in
`config.validate_matching_provider_config()`, which now also refuses a Region
carrying whitespace or a `/` and names WHICH of the two sources supplied it.
The S3 preflight's wrong-region refusal is **unchanged in behaviour** — a
session in another Region still refuses, because a bucket's Region is fixed for
its lifetime — and gains a remedy that is not a source edit. See "The hardcoding
audit record" below.

**THE `../04- Keys/` MOUNT IS FIXED AND THIS PARAGRAPH USED TO SAY IT WAS NOT** — corrected during the Docker pass, which measured the compose file rather than re-reading this note. What was true: pass 20c-3c-2 found a **two-two split, not a stray line** — `fastapi` and `airflow-webserver` mounted `../04- Keys/.env`, which does not exist, so Docker created an empty *directory* at that host path and bind-mounted it as `/app/.env`, and `load_env_keys()` failed with `.env file not found` or an `IsADirectoryError` depending on how it was reached; `streamlit` and `airflow-scheduler` were correct. **Item 21 closed it**: all four (now five, with `airflow-dag-processor`) name `../05- Keys/.env`, and every one carries `create_host_path: false`, which turns a missing or misspelled credentials path from a silently-mounted empty directory into a failure at `up` that names the path. The only occurrences of `04- Keys` left in `docker-compose.yml` are the two comments recording the fix.

**The `AIRFLOW__CORE__DAGS_FOLDER` line is NOT a defect, and pass 20c-3c-2 checked rather than repeated the claim.** `docker-compose.yml` lines 148 and 192 set it to `/app/airflow_home/dags`; `oncotriage/paths.py` line 291 sets the Docker-branch `airflow_path` to `/app/airflow_home/`; and `write_dag_file(dags_root)` writes to `Path(dags_root) / 'dags'`. Those are the same directory, and `AIRFLOW_HOME=/app/airflow_home` agrees with both. What IS true is that **nothing in the container ever runs File 23** — the webserver's command is `mkdir -p /app/airflow_home/dags && airflow db migrate && airflow api-server`, and the scheduler's is `sleep 30 && airflow scheduler`. So the DAG folder is created empty and the scheduler parses an empty directory forever. That is the real Docker-item defect in this area, and it is a missing generation step, not a path mismatch.

## Pipeline architecture

`oncotriage/agent/` is the core (thin entry point: `13- LangGraph Agent.py`, 5,565 lines before pass 20c-2c split it twelve ways and pass 20e removed the shim it left). `build_matching_graph()` in `agent/graph.py` wires a `StateGraph` over `TrialMatchState` (`agent/state.py`):

1. **`node_query_expansion`** — deterministic, no LLM. Uses the cancer registry (08) + MeSH filter (09) to expand the patient's primary diagnosis into query terms.
2. **`node_hybrid_retrieval`** — Qdrant-native BM25 (FastEmbed sparse, `BM25_RETRIEVAL_SIZE=75`) + dense `text-embedding-3-small` (`VECTOR_RETRIEVAL_SIZE=100`), fused by weighted RRF into `RRF_POOL_SIZE`. Falls back to BM25-only if vector search fails. **The fusion constants are owned by `oncotriage/config.py`** — `RRF_K` plus the four channel multipliers `RRF_WEIGHT_TITLE` / `RRF_WEIGHT_CONDITIONS` / `RRF_WEIGHT_CRITERIA` / `RRF_WEIGHT_DENSE` (title and conditions weighted higher because a disease-name match there is the strongest relevance signal). They were function-local literals in `node_hybrid_retrieval`; the values did not change.
3. **`node_cross_encoder_rerank`** — MedCPT cross-encoder, multi-query with RRF across queries, stable argsort for determinism. It fuses on the **same** `config.RRF_K` Stage 2 does — the module-level `RERANK_RRF_K` that used to hold a second literal `60`, under a comment asserting the two were equal, is deleted; one owner makes that claim true by construction. The four channel weights are Stage 2 only: this stage fuses queries, not fields, and weights none of them. It also **retains the raw MedCPT score**: `medcpt_score_max` (the best score across the rerank queries, `None` when no query scored the trial — never `0.0`) and `medcpt_queries_scored`. RRF keeps ranks and throws the scores away, which is right for fusion and leaves nothing calibrated for an absolute gate to read.
4. **`node_rule_based_filter`** — MeSH site relevance, cancer stage ordinal, histology, age, sex + a **two-knob quality gate** and cost cap (`MAX_TRIALS_FOR_EVALUATION = 15`). Both knobs must pass: `QUALITY_THRESHOLD_PERCENTILE = 25` of the **unboosted fused** score within the pool, and `MEDCPT_SCORE_FLOOR` on `medcpt_score_max`. A trial with no MedCPT score is not dropped by the floor — absence of a score is not a low score. Each knob reports its own count (`quality_dropped_percentile` / `quality_dropped_floor` / `quality_dropped_floor_only`); they **overlap**, so they do not sum to `quality_dropped`.

**`RERANK_SCORE_THRESHOLD` IS DELETED AND THE REASON IS THE POINT.** It was `-10`, a floor on the *fused RRF* score, under a comment describing MedCPT's `-25 .. +10` range — true of the code it was written for, false of the code it sat above. A fused RRF value runs about **0.01 .. 0.06** and is a function of pool size and query count, not of quality (a trial ranked first by all three queries scores ~0.050 however good it is). The gate took `max(percentile, floor)`, so the floor could never be selected — **not rarely, never** — and the relative percentile was doing 100% of the filtering, cutting one trial from a patient whose four survivors were all excellent. `MEDCPT_SCORE_FLOOR` is measured, not chosen: `python measure_medcpt_scores.py` (thin entry point over `oncotriage/evaluation/medcpt_calibration.py`) runs Stages 1–3 only over a seeded 10-breast/10-colon/10-lung sample and reports the distribution plus what the floor would drop *that the percentile does not*. Re-run it after an index rebuild, a rerank-query change, or a cross-encoder checkpoint change.
5. **`node_llm_classifier_evaluation`** — `MATCHING_MODEL` calls producing per-criterion verdicts; JSON-parse failures loop back up to `MAX_LLM_CLASSIFIER_RETRIES = 3`. **HOW MANY CALLS IS THE SHIPPED ARM'S DEFINING FACT AND `MATCHING_PER_TRIAL_CALLS_ENABLED` DECIDES IT.** `True` (the default) is ONE call per patient-trial pair behind a dedicated cache warmup that is awaited alone — the packer is bypassed — and `False` is the retained comparison arm, where the input packer emits between one and `MATCHING_MAX_INPUT_PACKED_CHUNKS` calls per patient. `config.matching_call_mode()` is the ONE owner of that answer; every consumer (Stage 5's partition, `inferences.matching_call_mode`, the resume fingerprint, the tracking index) calls it rather than reading the constant. **The sentence that stood here — "one `MATCHING_MODEL` call" — had been false since input packing and was false twice over after the flip.** **As of `PROMPT_VERSION` 1.5.0 the STORED `assessment` of an `eligible` or `not_eligible` trial is composed from that trial's own criterion / patient_value / status rows** — so it cannot assert anything the arrays do not carry — **and the model's own prose is kept beside it as `assessment_draft`, in memory only, with no database column**; a `not_evaluable` trial's arrays are empty by contract, so it keeps the model's text unchanged.
6. **`node_finalize`** — splits eligible/not_eligible, normalizes labels.

Nodes 1–3 are in `agent/retrieval.py`, node 4 in `agent/filtering.py`, node 5 in `agent/evaluation.py`, node 6 and the other two terminal nodes in `agent/terminal.py`.

**STAGE 5 HAS THREE PROVIDER ARMS AND ONLY ONE OF THEM RUNS.**
`config.MATCHING_PROVIDER` is a closed three-member vocabulary and
`evaluation.call_matching_model` dispatches on it: `"openai"` (the shipped
default — Chat Completions, unchanged), `"bedrock"` (the Responses API, GPT-5.6
Terra) and `"bedrock_anthropic"` (Converse, Claude Sonnet 4.6). The two Bedrock
arms are separate providers rather than modes of one because they share no
client library, credential chain, request shape, response shape or error class.
**An unrecognised name RAISES rather than falling through to OpenAI**, which is
the silent-wrong-provider failure that tuple exists to prevent. With the flag at
its default the OpenAI request is byte-identical to the one that shipped before
either adapter existed — proved against `git show HEAD:` rather than asserted.

Conditional edges route to `node_no_candidates` when a stage empties the pool, and any exception lands in `node_error_handler`, which still emits a well-formed result. `match_patient_to_trials(patient_data, graph)` is the public entry point; it stamps `qdrant_collection` and `patient_data_hash` onto the result. **Pass 20e removed the shim's wrapper around it** — the legacy-rebinding guard could only ever see rebindings in the shim's own namespace, and there is no such namespace now; `oncotriage/agent/deps.py` records why it needs no replacement.

**Ablation flags** ride in the state dict (`state["ablation_flags"]`) and are read at three points (nodes 2, 3, 4). `26- Ablation Study.py` toggles one stage per config; nothing else forks the pipeline.

### Synthea generation and the ECOG module (`oncotriage/fhir/generate.py`, entry point File 04)

The generator writes a custom Generic Module Framework module
(`synthea_modules_dir()`, resolved lazily from `data_patient_path`) that
records an ECOG performance status, hands it to Synthea with `-d`, then
post-processes and documents the run. **The three path constants are functions**
as of pass 20c-3a — `synthea_jar_path()`, `synthea_modules_dir()`,
`output_dir_full()` — because a package module may not resolve the sibling data
tree at import. The ECOG module's own `remarks` strings are left byte-identical
and still name `03- Config.py`: they are written verbatim into the generated
module JSON whose sha256 goes into the run manifest, so rewording them would
report the module as "updated" for a documentation edit.

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
  in `oncotriage/fhir/generate.py` is the code set, with its inclusions and
  exclusions argued inline.
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

These three moved into the package in pass 20c-2a. The numbered files survive as explicit re-export shims and every remaining chain consumer (05, 13, 30, 31, 32, 33, 42, 43) still reads their names out of the shared namespace unchanged. Files 06 and 11 used to be in that list and are not any more: as of pass 20c-3a they import `oncotriage.registries.cancer_code_registry` and `oncotriage.extraction.*` directly.

- **`oncotriage/registries/cancer_code_registry.py`** (its shim, `08- Cancer Code Registry.py`, was deleted in pass 20e) — primary-cancer detection: SNOMED exact → ICD-10-CM 2024 exact (`icd10-cm` package, handles `C34.10` and `C3410`) → display-term morphology fallback. Metastatic/secondary terms are rejected at every layer. Never assume the first condition in a FHIR bundle is the cancer. `import icd10` stays inside `_build_icd10_cancer_sets()`; do not hoist it. **This module's SOURCE TEXT is read by two tests** — `42-` extracts the inline comment beside every code as the claim under audit, and `43-` plants defects into it and hashes the restore. Both point here, not at the shim, and File 42 refuses to run if either claim dict comes back empty.
  Three names pass 2a re-exported are **gone as of pass 20c-2b**: `_REGISTRY` and `_LAB_REGISTRY` were snapshots of the module's private singleton slots, permanently `None` however the real slot was later filled, so they read as "no registry built yet" and could never read as anything else — use `load_registry()` / `load_lab_registry()`. `_var` was a leaked loop variable; `'_var'` is now the last entry in the cleanup tuple that deletes the other four, so the module binds it and then removes it. **The pinned pre-2a inventory that enforced this retired with the shim in pass 20e** — a check whose subject is "what does File 08's shim bind" has no subject once File 08 is deleted. The reasoning now lives at the top of `oncotriage/registries/cancer_code_registry.py`, where it is about that module's own surface rather than about a shim over it, and `tests/test_package_invariants.py` check 2h would report any of the three if it were reintroduced and left unread.
- **`oncotriage/registries/mesh.py`** (thin entry point: `09- MeSH Cancer Site Relevance Filter.py`, which runs the offline builders) — MeSH C04 tree ancestry match. Patient side maps SNOMED→CUI→MeSH via UMLS `MRCONSO`, falling back to fuzzy descriptor matching. Trial side is a direct lookup (ClinicalTrials.gov conditions *are* MeSH terms). **Conservative by design: unmappable on either side ⇒ KEEP.**
- **`oncotriage/registries/mesh_crosswalk_build.py`** — File 09's five offline builders (`build_mesh_lookup`, the three crosswalks, `build_all_lookups`). They parse `desc2026.xml` and the 1.5 GB `MRCONSO_2025AB.RRF` and write the JSON that `mesh.py` reads back. **Called from nowhere in the pipeline** — File 09's `__main__` block is the only call site, and `python "09- MeSH Cancer Site Relevance Filter.py"` still runs them. `mesh` does not import this module.
- **`oncotriage/extraction/{negation,stage,histology}.py`** (its shim, `10- Structured Eligibility Extractor.py`, was deleted in pass 20e) — index-time, rule-based, zero-LLM extraction of stage requirements into a structured dict, so stage matching in node 4 is an integer comparison. Unknown ⇒ `None` ⇒ trial passes. The three-way split rests on one measurement: walking every top-level definition in each half for `Name` loads resolving into the other half finds **exactly one edge**, `_is_histology_negated()` → `_is_negated()`. That helper is what `negation.py` holds. File 47 re-derives the measurement against the shipped modules, so a second shared name fails rather than accumulating.
- **`oncotriage/fhir/parser.py`** (thin entry point: `07- FHIR Parser.py`, a corpus smoke run) — `parse_fhir_bundle(bundle_or_path)` takes **either a decoded bundle (a dict) or a file path**; the dict form was added by pass 20f-1 and the file form is unchanged. The API used to write a temp file to bridge this, on *both* endpoints, and no longer does. Historical medications are deliberately retained with status labels so prior-treatment criteria are evaluable. **LOINC 89247-1 (ECOG) is routed out of `observations`** into `patient_data['ecog_performance_status']`, a dict that is present on every patient. `value` is `None` when nothing was recorded and is **never defaulted to 0** — ECOG 0 is *fully active*, the most eligible a patient can be, so every consumer must test `is None`, never truthiness. Both `valueInteger` (mCODE) and `valueQuantity` (raw Synthea, unit `{score}`) parse, and which was found is kept as `value_shape`; a non-integral or out-of-range grade **raises** rather than rounding. The winner is the most recent observation dated on or before `get_age_reference_date()`, never `datetime.now()`, with the counts and the selection path recorded alongside. `compute_patient_hash` (13) hashes value/date/count/selection but deliberately **not** `value_shape` — normalizing a corpus must not change a hash when the prompt text is identical — and emits nothing at all when no ECOG was present, so hashes already logged against an ECOG-free corpus stay comparable. Covered by `tests/test_fhir_ecog_surfacing.py`. **This module's SOURCE TEXT is read by two tests, and both point here, not at the shim** — `38-` ast-parses it to prove `_calculate_age` and `_parse_demographics` contain no clock call (and now checks both functions are actually present, because a stale filename made that assertion pass on an empty result), and `39-` slices named function bodies out of it. The shim keeps File 07's `__main__` block, which is the only place in the original 1,491 lines that named a path; it now resolves `data_fhir_path` from the shared namespace when there is one and from `oncotriage.paths` otherwise, prints which, and — unlike before — works when the file is run directly.

### Data preparation (pass 20c-3a)

- **`oncotriage/fhir/clean.py`** (thin entry point: `05- FHIR Clean Data.py`) — the cohort filter. Three phases, in this order: `non_cancer`, `deceased`, `over_cap`. The deceased phase runs BEFORE the cap so the cap samples alive patients only. Every unlink is manifest-backed. `patients_dir()`, `manifest_path()` and `cancer_registry()` are lazy accessors; the shim calls all three at load and binds the eager `PATIENTS_DIR` / `_MANIFEST_PATH` / `_CANCER_REGISTRY` names, because `34- Cohort Selector Diff Read Only.py` reads `_CANCER_REGISTRY` straight out of the shared namespace. The registry comes from `load_registry()`, **not** from `oncotriage.agent.deps` — a stub installed for an agent test must not change which bundles a deletion pass removes.
- **`oncotriage/fhir/explore.py`** (entry point: `06- FHIR Dataset Characterization.py`) — descriptive analysis. `output_dir()` **resolves and creates**: File 06 ran the `mkdir` at module level, and folding it into the accessor keeps the directory present before any write on every call path while keeping the import free of filesystem work. `apply_plot_style()` holds the `sns.set_style` / `plt.rcParams` statements that used to run at import; it is called by `main()` and by all seven functions that draw, so no call path loses the styling and no importer gains it.
- **`oncotriage/retrieval/indexer.py`** (entry point: `11- RAG Trial Indexer.py`) — reaches its clients through `oncotriage.config.get_*_client()`, deliberately **not** through `agent.deps`. Gets the BM25 encoder from `oncotriage.embedding`.
- **`oncotriage/retrieval/index_validator.py`** (entry point: `12- RAG Trial Indexer Validator.py`) — reaches everything through `agent.deps`, including both MedCPT halves, and imports `torch` **inside** `stage2_retrieval_tests()` (the third-party-in-a-function-body exemption; at module scope it would mean importing the validator pulled torch in).
- **`oncotriage/fhir/explore.py`: `output_dir()` is PURE as of pass 20c-3b.** Pass 3a folded the mkdir into it, so `print(f"Output directory: {output_dir()}")` created a directory as a side effect of printing and a caller who only wanted the path could not ask without creating it. The mkdir has its own name now — `ensure_output_dir()` — called by `main()` and by each of the eight functions that write, exactly the arrangement `apply_plot_style()` already had and for the same reason: a caller invoking `analyze_demographics()` directly must not lose the directory.
- **The lazy caches in `fhir/clean.py` and `fhir/explore.py` are locked (pass 20c-3b)**, matching `agent/deps.py` and `paths.py`. `if k not in d: d[k] = build()` is two atomic operations and one non-atomic sequence. Neither module runs multi-threaded today; the lock is about the pattern being copied when the next accessor is added.

### The serving layer (pass 20c-3b)

- **`oncotriage/storage/maintenance.py`** (thin entry point: `15- Database Wipe All Tables.py`) — `empty_database(db_path, flag)`. **Both arguments stay required and neither gets a default.** `db_path=None` meaning "production" would turn `empty_database()` — a plausible thing to type while exploring a module — into a command that wipes the real `inferences.db`, and `flag=False` as a default would make `empty_database(path)` a no-op that looks like it worked. `Flag = False` stays at module level in File 15: it is data, and the one-line edit that arms the script belongs where a reader looks for it.
- **`oncotriage/storage/queries.py`** (thin entry point: `16- Database Query.py`) — the ~40 queries as an ordered registry of `Query` records. `run(conn, key)` returns one frame, `run_all(conn)` returns them all, `report(conn)` prints what File 16 printed. **The SQL was extracted BY AST and never retyped**, so it is byte-for-byte what it was — except the two queries **item 38 has now fixed**; see "The query layer and the cost arithmetic (item 38)" below. **File 16 gained a `__main__` guard**, which is a behaviour change: it had none, so loading it ran forty queries against the production database as a side effect. `apply_display_options()` holds the six `pd.set_option` calls File 16 inherited invisibly from `01- Imports.py`; without them every wide frame prints truncated, which is a different report about the same data.
- **`oncotriage/api/server.py`** (thin entry point: `17- FastAPI Server.py`) — `create_app()` is the factory and `app = create_app()` the single call. **The app object at module level is the one deliberate exception to "importing a package module does nothing"**, and it is forced: ASGI takes a `module:attribute` reference. Building it opens no client, loads no model, touches no database and reads no file — the graph is compiled in the `lifespan` handler, on startup, where File 17 always had it. `/pipeline/info` reaches Qdrant through `agent.deps.get_qdrant_client()`, inside the handler, not at import.
- **`oncotriage/monitoring/drift.py`** (thin entry point: `20- Drift Detection.py`) — **File 20 contained ZERO import statements.** Not few; zero. It resolved only inside a namespace somebody else had filled, so `python "20- Drift Detection.py"` — the command in its own `__main__` docstring, and in `21- Streamlit Dashboard.py` line 3609 — died on `PSI_BINS` at the first `def`. Both instructions are true for the first time. `SCIPY_AVAILABLE` is a real `except ImportError` around a real import now, not a `NameError` guard on somebody else's namespace, and `ks_2samp` is bound to `None` on failure so a caller reaching past the flag gets a `TypeError` at the call site.
- **`oncotriage/batch/runner.py`** (thin entry point: `25- Batch Runner.py`) — the checkpoint, the two `ThreadPoolExecutor` passes and the summary. Its `_db_lock` is gone (see below); what remains is `_results_lock`, renamed for what it actually guards — File 25 used one lock for the database *and* for `append_result`'s read-modify-write, so every results-file write queued behind every database write.

**THE INFERENCE WRITE LOCK LIVES IN `oncotriage/storage/database_logger.py` (pass 20c-3b), and moving it there is a deliberate behaviour change that fixes File 17.** It used to be a monkeypatch at `25- Batch Runner.py` lines 65-73, rebinding `log_inference` to a lock-wrapped copy **in that file's namespace**. That protected the batch runner and nothing else, and there is a second concurrent writer: `17- FastAPI Server.py` calls `log_inference` from `loop.run_in_executor(...)`, once per in-flight request, on the event loop's thread pool. Two overlapping `POST /match` requests wrote to one SQLite file through two connections with nothing serializing them.

**Where the race actually is, measured rather than assumed.** On the steady-state INSERT path it is *not* observable at this project's contention: SQLite's own file locking plus sqlite3's 5-second busy timeout already serialize two connections, and File 47 check 5e asserts that both the locked and unlocked arms land every row there. The lock earns its keep on the **schema migration**: `ALTER TABLE ADD COLUMN` has no `IF NOT EXISTS` form, so the `PRAGMA table_info` check *is* the guard, and two threads arriving at a fresh database both read it and both issue the ALTER. The second gets `duplicate column name`, which `log_inference`'s `except sqlite3.Error` — the handler that exists so a logging fault cannot kill the pipeline — catches, prints as non-critical, and **loses the row while the run reports success**. Measured over 20 trials at 24 threads: the unlocked arm lost rows in 18, the locked arm in 0. File 47 check 5e runs 8 trials of each with the control built by stripping the `with` statements out of an ast COPY of the module.

It is an `RLock` because `log_inference` takes it and then calls `_ensure_database` → `initialize_database`, which take it again. `get_model_cost()` and the path resolution stay **outside** it: neither touches the database, and an unpriced model must reach the caller rather than be held behind database machinery.

### The dashboard (pass 20c-3c-1)

`21- Streamlit Dashboard.py` was 5,481 lines and is now a thin entry point over
`oncotriage/dashboard/` — fifteen modules: `data`, `sidebar`, `tiers`, `app`,
and one per tab under `tabs/`. **Nothing in the repository chained it or read a
name out of it**, verified the same way as pass 3b: all 22 top-level names
grepped against every `.py`, `.md`, `.toml` and `.yml`, with every hit inside
File 21 itself, prose in a `.md`, or the `streamlit run` command. So it keeps
**no re-export shim** — the first converted file that needed neither a shim nor
a single re-exported name.

**STREAMLIT RE-RUNS THE WHOLE SCRIPT ON EVERY INTERACTION, and that is why the
exec bootstrap had to go.** `exec_chain` caches nothing — it opens and `exec()`s
every file on every call — so every button, filter and tab click re-read and
re-executed Files 01 and 02 and re-chained File 03. Because `03- Config.py`
calls the client factories at shim load, **every interaction also constructed an
OpenAI client and a Qdrant client**, for a dashboard that uses neither and never
did. This was measured, not inferred: exec'ing the old File 21 emits Qdrant's
version-mismatch warning, and the converted one does not.

**The semantic change that buys is module-level state persisting across reruns
instead of being rebuilt.** The dashboard has exactly two module-level mutable
objects, `MATCH_TIERS` (list) and `MATCH_TIER_COLORS` (dict) in
`dashboard/tiers.py`; everything else it binds at module level is a `str`, and
everything it reads out of the package (`Project_Name`,
`MAX_TRIALS_FOR_EVALUATION`, `inferences_path`) is immutable. Neither is
mutated, the `tier_colors = MATCH_TIER_COLORS` alias in three tabs is never
written through, and plotly leaves a `color_discrete_map` unchanged — all three
measured. **Check 6a of File 47 re-derives it and carries a planted-mutation
control** covering the three shapes it claims to cover (mutating method,
subscript store, write through an alias), so a future edit that starts mutating
either object fails rather than leaking into every later rerun for every user of
that server.

**The 60-second cache TTL is unaffected, and this rests on a fact about
streamlit rather than about this project.** A cached function's identity in the
cache is `md5(__module__, __qualname__, source)` — read out of
`streamlit/runtime/caching/cache_utils.py:_make_function_key` — and **not the
function object**. So the cache already survived reruns before this pass (had it
keyed on identity, `ttl=60` would never have had anything to expire), and moving
the loaders into a module changes only the `__module__` component: a different
key, still stable, still 60 seconds, with one cold miss on first launch. Check
6b asserts that against the installed streamlit, because if a future version
keys on identity the dashboard silently stops caching — three full-table SQLite
reads per widget interaction — and nothing else here would notice. Verified
empirically too: stale at 30s and 58s, refreshed at 62s, and
`st.cache_data.clear()` behind the sidebar's Refresh button empties the loaders
**even though they now live in a different module than the button**, because it
is a cache-wide clear.

**`dashboard/data.py` reads `paths.inferences_path` inside each function body,
never `from oncotriage.paths import inferences_path` at module scope.** A
`from X import name` is an **attribute read**, so at module scope it fires the
lazy resolver and globs the whole sibling data tree at import — the exact hole
pass 20c-2c found in `registries/mesh.py`. **The three loaders were moved as
they are and their SQL is untouched**; consolidating them into
`oncotriage/storage/queries.py` is its own item, and mixing a relocation with a
redesign is what makes an equivalence proof stop meaning anything.

**Equivalence was proven by `ast.unparse` against `git show HEAD:`**, and it
earned its keep immediately: the first extraction sliced from each function's
`def` line, and **ast reports a decorated function's `lineno` at the `def`, not
at the decorator**, so `@st.fragment` was silently dropped from four tabs — a
real behaviour change, since a fragment re-runs in isolation and without it
every interaction in those tabs re-runs the whole app. The extractor derives its
spans from `min(decorator linenos + def lineno)` now and asserts contiguity.
Final state: **19 of 22 definitions byte-identical after `ast.unparse`, 3
differing, all three the same one-line `inferences_path` → `paths.inferences_path`
change.** The rendered output was compared too — the original and the package
render identical element counts and identical label/value pairs across all 9
tabs, 118 metrics, 48 subheaders and 18 dataframes, with zero exceptions.

**THE SAME SLICING APPROACH MOVED FILES 07 THROUGH 25, so pass 20c-3i swept all of it.** The `@st.fragment` loss was caught by one pass's equivalence proof; nothing said the other seven passes had been as lucky. Every `FunctionDef` / `AsyncFunctionDef` / `ClassDef` in the package was compared against the pre-split version of the numbered file it came from, with the origin commit **derived** (`git log --diff-filter=A` for the module, then the numbered files that *lost* definitions in that commit) rather than declared. **404 definitions, 314 matched to an origin, 18 carrying decorators, zero mismatches, and zero decorated origin definitions that failed to reproduce.** Three negative controls — `api/server.py`, `dashboard/tabs/drift.py`, `agent/retrieval.py` — were each stripped in an AST *copy* and each stopped agreeing with its origin.

**The sweep had to run at every nesting depth, and the first attempt did not.** A top-level walk reported `api/server.py`'s four endpoints as having no counterpart at all, because `create_app()` nests them — so the four definitions in the package carrying the **most** decorators were the four it could not see. Those same four separate their decorators from the `def` with **blank lines**, which is invisible to ast (`decorator_list` hangs off the node) and fatal to any check written against adjacency. What survives as a standing check is File 47 section **2i**: the exact decorator list of every decorated definition in the package, keyed by *qualified* name — which is also what distinguishes `CancerCodeRegistry._invert_date` from `OncologyLabRegistry._date_sort_key`, two `@staticmethod`s in one module that a bare-name inventory would confuse. The git comparison stays a one-time audit: it needs history, and a check that re-derives its expectation from whatever HEAD happens to be agrees with the code by construction.

Section 6 of `tests/test_package_invariants.py` is separate from section 2 **on
purpose**. Section 2 asserts no model-bearing library arrives and **streamlit is
on that list** — it is what says importing the agent does not drag the dashboard
in. The dashboard's modules import streamlit at module scope because every
render function needs it, so they get their own trap run with streamlit and
plotly pre-imported (the same allowance section 2 makes for matplotlib and
seaborn), and with torch / transformers / icd10 still forbidden.

`oncotriage.dashboard.tabs.reproducibility` was ~1,450 lines because it was
**one function**, and the tab boundary was the finest cut available without
restructuring it. **Pass 20f-4 is that item** — see "The two function splits
(pass 20f-4)" below. `render_reproducibility_tab` keeps its name, its module and
its single `@st.fragment`; what came out is 4 literal tables and 19 pure helpers, and the function
itself is 928 lines instead of 1,444;
proven element-for-element identical through streamlit's `AppTest`.

### The query layer and the cost arithmetic (item 38)

**This item DELIBERATELY BROKE pass 20c-3b's acceptance criterion.** That pass
required `report()`'s output to be identical before and after the move,
*including the failure*. It runs to the end now, and File 16 does too — verified
against the production database on 2026-08-05: exit 0, 2,629 lines, 35 headings.

- **`expansion_token_efficiency` is DELETED, not repaired, and `48-`-style notes
  where it sat say so.** It selected `expansion_input_tokens` /
  `expansion_output_tokens`, which are not columns of `inferences` and never
  were: **Stage 1 is deterministic and issues no LLM call**, so there are no
  expansion tokens to count. Adding the columns would have meant inventing a
  measurement; rewriting it would have duplicated `expansion_stage_stats`, which
  already asks the answerable version (timing, fallback rate, path-not-reported
  count). Because File 16 was top-level statements, this query raising
  `no such column` **took the process with it — so no query after it in the
  registry had ever executed, in any invocation of File 16, ever.**
- **`pipeline_consistency` had four defects, and only one of them was visible.**
  The stray `WHEN` between the column list and the `CASE` made it a syntax
  error, so it had never run once; the identical condition already sits *inside*
  the `CASE` (the two lines differ only in indentation, checked against the
  committed text rather than assumed), so removing the stray one changes no
  logic. Behind it: `!= 100` and `!= 30` were literals for configured values,
  **`30` matched no constant in the project at any point in tracked history**
  (`TOP_K_CANDIDATES` has been 40 since `0d3e3eb`), `100` was ambiguous by value
  (`VECTOR_RETRIEVAL_SIZE` and `RRF_POOL_SIZE` are both 100), and `!=` is the
  wrong operator for either because both numbers are **caps applied with a
  slice** — a run producing fewer is ordinary. The bounds are derived from the
  code that produces the columns (`hybrid_results` is `[:RRF_POOL_SIZE]`,
  `reranked_trials` is `[:TOP_K_CANDIDATES]`), interpolated from
  `oncotriage/config.py`, and compared with `>`. **Measured, not argued: the
  pre-fix logic flags 1,106 of 1,106 production rows and the fixed logic flags
  0.** Two further fixes: `'Count mismatch'` now uses the three-term identity
  (`evaluated == eligible + near_misses + not_evaluable_trials`, which is how
  `agent/terminal.py` partitions `evaluations`) with a weaker `<` branch for
  pre-migration rows whose `not_evaluable_trials` is NULL — never
  `COALESCE(..., 0)`, which would assert a count nothing recorded — and a row
  whose counters are NULL is **flagged** rather than falling through
  three-valued logic to `ELSE 'OK'`.
- **THERE IS NOW EXACTLY ONE PER-MODEL COST CALCULATION:
  `queries.price_model_groups`.** `cost_by_model()` feeds it the SQL `GROUP BY`;
  `oncotriage/dashboard/tabs/cost_tokens.py` feeds it a pandas groupby over the
  **sidebar-filtered** frame through `queries.model_groups_from_frame()` — the
  tab cannot call `cost_by_model(conn)`, because that would silently re-price
  the whole table and ignore every filter the user set. The two copies had
  already diverged in the way that mattered: the dashboard used `pd.isna` and
  the query layer used `is None` and `int(x or 0)`.
- **`nan` is TRUTHY, which is the whole bug.** `int(x or 0)` on a NULL `SUM`
  raises `ValueError`, and `x is None` never fires because a NULL in a float64
  column is `nan`. **Both fire on the real database**: 1,100 gpt-4o rows report
  NULL `gpt4o_reasoning_tokens` and 6 gpt-5.6-terra rows report 0, so the column
  is float64 and the pre-fix function raises on production — it had simply never
  been reached. All four null tests are `pd.isna` now, and a NULL aggregate is
  carried as nullable `Int64` `<NA>` with the reason in a `note` column, never
  as 0.
- **`model_groups_from_frame` passes `min_count=1`, and that is not a detail.**
  pandas' `.sum()` returns 0.0 where SQL's `SUM()` returns NULL, which is why
  the dashboard was never exposed to the fault — so the consolidation had to
  make pandas agree with SQL rather than the reverse, or the two paths would
  disagree about exactly the case this item is about. `groupby(dropna=False)`
  also labels the missing group **`nan`, not `None`**, so `is None` would have
  handed `nan` to `get_model_cost` and taken the whole cost panel down.
- **The cost tab's stale MODULE DOCSTRING was the actual defect there.** It
  claimed the tab "prices through `get_model_cost` and labels its chart GPT-4o";
  both halves were false, checked against the code. One real leftover was found
  and fixed: the tokens-per-trial legend carried `4× cost`, gpt-4o's ratio,
  which is now derived from observed spend like the chart beside it.
- Two custom renderers were swept for the same idioms. `print_slowest_prompt`
  did `df.iloc[0]` on a possibly-empty frame (so `report()` against an empty
  database died there) and `f"{...:.1f}"` on a possibly-NULL `total_time`
  (`TypeError` on object dtype, silent `nan` on float64). Both report the
  missing fact by name now.
**Three residual weaknesses, closed in a follow-up pass.** All three mislead on a
NORMAL full-corpus run, not only on a defective one.

- **A CAPPED LISTING IS NOT A REPORT.** `pipeline_consistency`'s `LIMIT 20` sat
  on the outer select with **no ORDER BY**, so it hid how many issues there were
  *and* returned whichever twenty SQLite chose — twenty issues and twenty
  thousand printed identically, and two runs of the same query on the same data
  need not agree. `pipeline_consistency_totals` counts by category over every
  row with no limit and prints **immediately above** the listing; the listing
  gained `ORDER BY issue, patient_id, id`. **`id` had to be selected, because
  patient_id is not unique** — measured on production, 1,106 rows carry 1,004
  distinct patient_ids, so ordering on it alone leaves ties and is not
  deterministic. Both queries interpolate **one** `_CONSISTENCY_CASE_SQL` and
  **one** `_CONSISTENCY_CLASSIFIED_SQL`, so they cannot drift; File 49 mutates
  the shared CASE and shows both derived queries move together. The CASE itself
  is pinned twice — against **item 38's own committed blob**, rendered through
  the same config constants, and against a sha256 measured before the refactor
  (the second is the only pin left on a machine without history). A new render
  mode `skip_if_empty` prints **nothing at all** — not even the heading — when
  the companion is empty, so a clean database still shows exactly the one clean
  message it always did.
- **THE NULL GUARD'S COLUMN LIST WAS UNENFORCED.** The rule is *not* "every
  compared column is in the guard": `not_evaluable_trials` is deliberately
  outside it, because it is an added column legitimately NULL on pre-migration
  rows, and it carries its own NULL-aware branches instead. So the rule is
  **either treatment, never neither**, and File 49 derives *both* sets from the
  SQL — guard branch located by the category it emits, columns identified by
  intersection with the real schema — rather than listing them. **Two controls:
  a seventh compared column with neither treatment must fail, and one with a
  NULL-aware branch must pass.** Without the second, the rule collapses into
  "any new column fails", which would forbid the treatment already in use.
- **AN UNRECORDED COST PRINTED AS ZERO.** An unpriceable group contributes a
  real `0.0` to `recomputed_cost` — not a NULL — so anything summing the column
  under-reports by exactly the unpriceable spend and cannot tell. The reason was
  in a `note` column, and prose is not a field. **`cost_complete` is the one
  field a consumer asks**, False when the token sums are NULL or when the model
  is NULL while tokens exist, True for the ordinary no-candidates group that
  genuinely spent nothing. It says nothing about `stored_cost`, which carries
  its own NA a caller can inspect directly. `print_cost_by_model` marks the
  total `<- A FLOOR, NOT A TOTAL`, names the groups and rows excluded, and
  qualifies the 1000-patient projection; the dashboard warns in the same terms.
  **The priced value is unchanged** — NaN would propagate into every aggregate
  and produce no number at all.

- **`tests/test_storage_query_layer.py`** is the demonstration: 194 assertions,
  every query in the registry run against a seeded temporary database and every
  one required to come back NON-EMPTY, `report()` end to end, and the negative
  controls **unparsed out of git rather than retyped here** — the pre-fix SQL
  shown still to raise, the pre-fix `cost_by_model` shown to raise `ValueError`
  on the very frame the fixed one prices, the pre-fix `print_slowest_prompt`
  shown to raise `IndexError`. The commit is **derived**, not `HEAD`, so the
  controls survive this work being committed. It is **not** in
  `tests/run_serial_tests.py`'s collision matrix: it mutates no file in the repository
  and writes only into a fresh temp directory.

  **THE FIRST VERSION OF THAT DERIVATION WAS A SUBSTRING SEARCH AND IT BROKE THE
  MOMENT ITEM 38 WAS COMMITTED.** It selected the newest revision whose blob
  contained `expansion_input_tokens`, on the reasoning that only the broken query
  could name a column that does not exist. The **deletion comment left in its
  place quotes the query it removed**, twice — so the selector picked item 38's
  own revision, `_pre_fix_function("cost_by_model")` returned the *fixed*
  function, and two negative controls failed with `NameError` instead of
  controlling anything. It parses the blob and asks which query **keys the
  registry declares** now, which prose cannot satisfy. Same lesson as the BM25
  construction-site check: a substring is not a definition.

  **Two things in File 49 aborted the run instead of recording a failure**, found
  by actually reverting each fix in a copy of the package rather than reasoning
  about it. `QUERIES_BY_KEY["k"]` and `QUERY_KEYS.index("k")` raise when the
  companion query is deleted — the exact edit the section exists to catch — and
  `text.split(marker)[1]` raises when a report line is missing, which is what a
  reverted `cost_complete` produces. Both crashed at module level and hid every
  check below, so the run reported one traceback where it owed ten failures:
  File 16's original defect, reproduced inside the file written to remove it.
  `after()` and `registry_index()` are the fix. **All eight reverts now produce
  recorded failures** (10, 3, 1, 2, 5, 9, 8 and 2 respectively).

### Index lifecycle (Qdrant)

`COLLECTION_NAME = "trial_criteria"` is an **alias**, never a collection. `oncotriage/retrieval/indexer.py` (entry point `11-`) builds into a timestamped staging collection (`trial_criteria_20260226_140159`), creates payload indexes, then `swap_alias_atomic()` in a single `update_collection_aliases` call (zero downtime), then `cleanup_old_collections()`. Use `resolve_qdrant_collection()` (`oncotriage/utils.py`) whenever the *real* collection name is needed for logging — it retries and falls back gracefully. It takes an optional `client=`; with none supplied it uses `oncotriage.config.get_qdrant_client()`. (Before pass 20e, File 02's shim wrapper handed it the shared namespace's `qdrant_client`, so a fixture proxy was what it talked to; the fixture harnesses go through `oncotriage.agent.deps` now.)

`oncotriage/retrieval/index_validator.py` (entry point `12-`) reaches its clients and both MedCPT halves through `oncotriage/agent/deps.py`, not through `oncotriage.config` and not through File 13 (whose lazy proxies pass 20e deleted; the validator calls `deps.get_medcpt_tokenizer()` / `get_medcpt_model()` directly) — it is the one module in `oncotriage.retrieval` that uses the agent's seam, because the question it answers is "is this index healthy for the AGENT to query". The indexer deliberately does **not**: an index build must not be redirected by a stub installed for an agent test, and `retrieval` importing `agent` would be the wrong direction.

`23- Airflow DAG.py` writes the `trial_refresh_weekly` DAG (Sundays 02:00) into `{airflow_path}/dags/`; `22-` initializes the Airflow DB and `24-` starts/stops/triggers via the REST API v2. The DAG file is generated as a string, so DAG logic edits go in `oncotriage/orchestration/dag_generator.py`, not in the `dags/` output and not in File 23 (which is now a thin entry point).

**The outstanding regeneration is DONE (pass 20c-3c-2), and it was verified with Airflow's own parser rather than assumed.** Before: the DAG under `{airflow_path}/dags/` was 19,360 bytes, sha256 `1b6e2479…`, and `DagBag(dag_folder=...)` registered **zero** DAGs with one import error — `RuntimeError: AIRFLOW_DAG_SCHEDULE is not assigned at module level in .../03- Config.py`, exactly as predicted. After `rm` + `python "23- Airflow DAG.py"`: 20,542 bytes, sha256 `68963b0c…`, `import_errors == {}`, `trial_refresh_weekly` registered with all three tasks (`scrape_and_save`, `rebuild_index`, `verify_index`), tags `['production', 'trialmatch']`, timetable summary `None` (which is `AIRFLOW_DAG_SCHEDULE = None`, as configured).

### Orchestration (pass 20c-3c-2)

Files 22, 23, 24 and 29 are thin entry points over `oncotriage/orchestration/` and `oncotriage/retrieval/qdrant_backup.py`. **None of them keeps a re-export shim**, verified the same way as passes 3b and 3c-1: every top-level name each defines was grepped against every `.py`, `.md`, `.toml` and `.yml` in the tree. File 22's `setup_airflow`, all five of File 23's names and all twelve of File 24's names have **no hit outside their own file**; File 29's 27 leaked names hit only third-party imports (`Path`, `json`, `time`) and coincidental same-named locals elsewhere (`c`, `f`, `name`, `info`, `points`, `offset`, `size`, `output_dir`, …), while its ten distinctive ones (`all_points`, `point_data`, `scroll_result`, `serialized_vectors`, `backup_files`, `file_size_mb`, `total_size`, `vec_name`, `vec_val`, `collection_info`) have no hit anywhere.

The free-name lists were re-derived with **symtable, not a plain ast walk** — File 24's own comment records that a plain walk wrongly reported three function locals (`attempt`, `name`, `candidate`) as free. Measured: File 22 four (`Path`, `airflow_path`, `os`, `subprocess`), File 23 six (`Path`, `airflow_path`, `path_settings`, `code_path`, `keys_path`, `data_trial_path`), File 24 seven, File 29 two (`data_path`, `qdrant_client`).

**THE GENERATED DAG TEXT IS BYTE-IDENTICAL, and that is the only acceptance criterion that matters for File 23** — the scheduler parses the output and never sees the generator. Proved by generating from `git show HEAD:"23- Airflow DAG.py"` into one temporary directory (exec'd with a doctored `__file__` so its bootstrap could still find File 01) and from the package module into another: same 20,542 bytes, same sha256 `68963b0c…`.

**`dag_content` is a FUNCTION now, and it had to be.** File 23 assembled it at module level, and its middle third is `%`-formatted with `code_path`, `keys_path` and `data_trial_path` — three **lazy** paths. So importing the old shape resolved three directories and raised on any machine without the sibling tree. `build_dag_content()` is the fix; `dag_content_head` and `dag_content_tail` stay module-level constants because they are plain strings that resolve nothing. Note which half of File 23's own item-20b comment survives that: it said "dag_content itself stays at module level — it is a string, building it touches nothing". The first clause was right and the second was not. Under the exec chain it was invisible, because `01- Imports.py` had already resolved every path before File 23 was reached.

**Two lines of PROSE inside the generated DAG still say `03- Config.py` and are deliberately left wrong.** The module docstring's Schedule line and the comment above `DAG_SCHEDULE`. The code below them opens `oncotriage/config.py` — that is what item 20c retargeted and what now parses clean. Correcting the prose changes the bytes and would have made the equivalence proof for this pass meaningless. It is a one-line follow-up whose acceptance criterion is a **new** sha256.

**THE AIRFLOW PASSWORD HAS AN EXPLICIT ROUTE (`oncotriage/orchestration/airflow_manager.py`), because the old one silently stopped working.** File 24 held `AIRFLOW_PASSWORD = None` at module level, mutated through `global`, and `start_airflow()` printed `⚠️ SET AIRFLOW_PASSWORD in this file to use trigger/status functions!`. Once the functions moved into a package module, **"this file" is the wrong file**: a name bound in the entry point's namespace is not the module's global. An operator following that printed instruction would set a variable nothing reads, and **nothing would raise** — the module falls through to the generated password file and authenticates successfully with a password the operator did not choose. The instruction was already self-contradictory before the move: the comment on the same line said "Auto-read from password file (never set manually)", which is what the code actually did.

Four tiers, first match wins, and `password_source()` reports which answered without returning the secret:

1. `check_dag_status(password=...)` / `trigger_dag(password=...)` / `_get_token(password=...)` — **not cached**, so a one-off argument never becomes the process-wide answer;
2. `airflow_manager.set_airflow_password(...)` — refuses `""` rather than storing a value that would 401 with no diagnosis;
3. `ONCOTRIAGE_AIRFLOW_PASSWORD` — `settings.resolve_airflow_password()`, which is **deliberately not `_from_env`**: that helper appends a trailing separator, correct for every path and silent corruption for a credential;
4. the generated password file — what File 24 always did, unchanged, including both error messages (the `FileNotFoundError` now also names the other three routes).

`clear_airflow_password()` drops the JWT cache with the password, because a token minted with the previous one stays valid for its five-minute TTL. All of the above was demonstrated, including that the **old route is dead** — setting `AIRFLOW_PASSWORD` in File 24's namespace, and rebinding it on the package module, both leave `password_source()` reporting `password-file` — and every assertion was shown to FAIL when broken (four negative controls, all fired).

**`resolve_airflow_home()` lives in `oncotriage/orchestration/home.py` and is the ONE place `paths.airflow_path` is read.** The first draft wrote the same three lines into all three orchestration modules. That is the shape the BM25 sparse model had before pass 20c-3a — one job, several construction sites, nothing that fails when they disagree. Here disagreement means `airflow_setup` migrating a database under one AIRFLOW_HOME while `airflow_manager` starts a scheduler under another: two working processes, two metadata databases, and the only symptom is a DAG that never appears in the UI.

**File 29 was the last file in the repository with NO FUNCTION AT ALL, and
pass 20c-3d found the second unguarded one.** Read the claim narrowly: File
29 had no function, no `__main__` guard, no bootstrap — every statement at module level, so loading it created a directory, listed every Qdrant collection, scrolled every point with payloads **and** vectors over the network, and wrote one JSON per collection. Item 20b guarded Files 15, 16, 17, 22 and 24 and never reached it, because nothing loads it: the only documented invocation was `exec(open(code_path + "29- Download Qdrant Data.py").read())` from Spyder. That line is gone from the docstring rather than left as a trap — behind a guard it would exec cleanly and download **nothing**, which is worse than failing. Its header comment also claimed it used `results_path`; it never did, and the symtable measurement is what settled that. **`28- Select Evaluation Sample.py` was unguarded too** — it defined one function, `classify_cancer`, and ran every other statement at module level, so reading it opened the production `inferences.db`, sampled it, DELETED the existing `inferences_sample_30.db` and rewrote it. Nothing loads it either, which is why item 20b and pass 3c-2 both missed it. Guarded in pass 20c-3d.

`download_all_collections(output_dir, client=None)` takes the destination as a **required** argument with no default, on the `empty_database(db_path, flag)` precedent: a plausible thing to type while exploring a module must not start a full download of a cloud database. `default_output_dir()` resolves the historical destination lazily and **creates nothing** — the `output_dir()`/`ensure_output_dir()` lesson from pass 20c-3b. The client comes from `oncotriage.config`, **not** `agent.deps`, for the same reason `retrieval.indexer` does: a stub installed for an agent test that quietly redirected a BACKUP would be indistinguishable from a real one until the day it was restored from.

One change in `qdrant_backup` is **not** a path accessor, a client accessor or a guard, and is called out rather than folded in: File 29's `except Exception: pass` around `get_aliases()` is now logged. Continuing is right (nothing below uses the aliases, and `Exception and Fallback Audit.md` line 272 rules it acceptable) but silence is not — the project's standing rule is that no exception is caught without being recorded. The type and message are printed and the failure lands in the returned summary as `aliases_error`.

**File 24's `__main__` menu is kept BYTE-VERBATIM**, including its comment `# After setting AIRFLOW_PASSWORD: Check status`, which names the retired route. Replacing the commented menu with a real argparse CLI is the right end state, it is a redesign, and it is a recorded follow-up — not built here. The entry-point docstring carries a loud note immediately above the menu so no reader is misled by that one comment.

**File 47 grew two traps and six modules.** `subprocess.run` and `subprocess.Popen` are patched to raise before the imports, because three of the six new modules are the only ones in the package that spawn processes and **no existing trap could see one** — a subprocess opens no socket, no database and no file *in this process*, and before item 20b loading File 22 ran `airflow db migrate` while loading File 24 launched two long-lived servers. 48 modules now import under all traps; the sweep was **278 checks** as of pass 20c-3i, **283** as of pass 20c-3d, and is **235** as of pass 20e — the drop is four retired shim-surface sections, partly offset by two re-derived ones; see "What pass 20e retired and re-derived" below.

**Pass 20c-3i widened those two traps to twelve and added three sections.** The two subprocess patches were measured, not trusted, and the measurement changed the picture. The comment beside them claimed a `from subprocess import Popen` would escape; it does not — `from X import name` is an attribute read performed when the import *runs*, and every package import runs after the trap is armed, so the attribute, module-alias and from-import forms are all caught (each is now **fired**, not argued, as its own control). `subprocess.call` / `check_call` / `check_output` / `getoutput` and `os.popen` all funnel through the patched `Popen`, which is a CPython implementation detail rather than a guarantee, so they are trapped explicitly. What genuinely escaped was **`os.system`, `os.posix_spawn`, `os.execv` and `os.fork`** — not a reference form at all — plus one real pre-bound from-import, `prompt_toolkit.application.application.Popen`, taken before the patch and therefore out of reach of any attribute patch. The probe now sweeps `sys.modules`, **rebinds** every surviving reference to an original, sweeps again and asserts the second sweep is clean, with a planted holder as the control. Nothing in the package imports prompt_toolkit, which is exactly why reporting rather than closing would have been wrong: a trap whose coverage depends on which third-party packages happen to be installed is a coincidence, not a guarantee.

The three new sections: **2h** (nothing is declared and never read — see the method rule below; its exemption list is closed and each entry argued, and this file's own string literals are excluded from the read corpus so the scan cannot read its own exemptions), **2i** (the decorator inventory of the whole package, pinned at every nesting depth), and a **recursive** subpackage scan in section 1 with a negative control planted three deep.

### The ablation study, the two measurements, and the fixture harness (pass 20c-3d)

**This is the LAST conversion pass.** Files 26, 27, 28, 34, 45 and 46 become thin
entry points over `oncotriage/ablation/`, `oncotriage/evaluation/` and
`oncotriage/fixtures/`. **None of the six keeps a re-export shim**, and for File
45 that answer CHANGED DURING THE PASS.

**THE SHIM QUESTION WAS SETTLED BY GREP, NOT BY ASSUMPTION.** The pass began on
the premise that File 45 would need one, because `fixture_replay.py` (File 46)
exec-chains it and reads eighteen names out of the shared namespace. All 101 of
File 45's top-level names were grepped against every `.py`, `.md`, `.toml` and
`.yml` in the tree first, and **File 46 is the only consumer of any of them** —
every distinctive hit (`BUNDLE_DERIVED`, `BUNDLE_IN_COHORT`,
`FIXTURE_KIND_CONSTRUCTED`, `FIXTURE_ROOT`, `SCHEMA_VERSION`, `_HOOK_KEYS`,
`OpenAIProxy`, `QdrantProxy`, `RecordingSink`, `assert_hooks_reach_the_agent`,
`build_deterministic_prefix`, `compute_collection_digest`, `flatten_prefix`,
`list_fixtures`, `load_fixture`, `rebuild_derived_bundle`, `restore_hooks`,
`sha256_json`) is a line in File 46, and the rest are prose. This same pass
converts File 46, which imports them from `oncotriage.fixtures.capture`, so
after it nothing chains File 45. A shim would have been re-exports with no
reader — the dead declaration File 47 check 2h scans for. The other five files
have no consumer at all: File 26's only outside hits are File 27's own
`ABLATION_DB` over the same directory and two prose mentions of
`log_ablation_result`; Files 27, 28, 34 and 46 hit nothing but bootstrap locals
and prose.

**THE NAME GREP HAS A BLIND SPOT AND IT FIRED IMMEDIATELY.** It searches for
top-level NAMES, and `40- ECOG Logging Test.py` (now
`tests/test_storage_ecog_logging.py`) line 585 read File 26 by
FILENAME — `Path(_code_dir + "26- Ablation Study.py").read_text()` — to slice the
`ablation_results` CREATE TABLE out of it and assert no ECOG column was added.
The entry point holds no schema, so `split(...)[1]` raised `IndexError` and took
section 6 with it. It is retargeted at `oncotriage/ablation/study.py`, the same
way Files 38, 39, 42 and 43 point at package modules rather than the shims over
them, and it gained two guards — the file exists, and it carries the marker —
so a future move produces a named failure instead of a traceback. Both new
assertions, and the ECOG-column check they protect, were shown to FIRE against
mutated copies. **A repo-wide grep for each file's NAME as a string is now part
of the method, not just its symbols.**

**FILE 45's ISOLATION MECHANISM PARTLY CEASED TO EXIST, and the naive conversion
makes the guard VACUOUS rather than failing.** `_assert_database_is_isolated()`
made five checks; the fourth was `inferences_path != FIXTURE_SCRATCH_DB`, a
statement about a name in the shared exec namespace. A module has none. Dropping
it loses the guard; keeping it against `paths.inferences_path` compares the
production path to a temp path, finds them different, and **passes forever while
asserting nothing**. It is re-expressed as the module-world statement of the same
fact: *no name in this module's globals is bound to
`oncotriage.storage.database_logger.log_inference`* — a scan, so an alias does
not escape it, which is strictly stronger than the single identity test it
replaces. The other four are unchanged: the package default IS the production
database, it is NOT the probe path, `resolve_inference_db_path` honours an
explicit argument, and the neutralized `log_inference` still RAISES. **All five
were broken one at a time in an in-memory COPY of the module and shown to fire;
the unmodified module passes.**

**FILE 45's REASON FOR CHAINING FILE 14 HAD EXPIRED TWO PASSES EARLIER.** Its own
comment said File 14 was chained only for `_resolve_primary_cancer()`, which
File 13's terminal nodes need. Pass 20c-2b moved that function to
`oncotriage/registries/primary_cancer.py` and `oncotriage/agent/terminal.py`
imports it from there by name. Nothing else in File 45 used a File 14 name. The
chain is gone, and with it the `inferences_path = FIXTURE_SCRATCH_DB` redirect
that existed only to make the chain safe. `FIXTURE_SCRATCH_DB` survives as a
**comparison probe** — no file is ever created at that path — because the two
non-degeneracy checks need a second, definitely-different path to compare
against.

**THE DEFER ORDERING IS THE SHARPEST HAZARD IN THE PASS, and it is checked rather
than trusted.** `oncotriage/agent/deps.py` reads
`ONCOTRIAGE_DEFER_LOCAL_MODELS` **once, at its own import**. File 46 set it
before File 13 was exec'd. In a module every import runs at the top, `deps`
arrives transitively on the first `oncotriage` import, and an assignment
underneath it reaches nothing — so MedCPT (~110 MB) and FastEmbed load for real
on every replay while the run still prints `Local models: not loaded`. So
`oncotriage/fixtures/replay.py` sets the variable **above its own imports**,
which is the one module-level side effect anywhere in the package, and
`assert_local_models_deferred()` — called by `main()` before a fixture is read —
refuses on three separate facts: the assignment was not too late
(`_DEFERRAL_WAS_LATE`), `deps` observed it, and neither `torch` nor
`transformers` is in `sys.modules`. It is a recorded flag rather than an
import-time raise because File 47 imports the whole package in one process in
alphabetical order, where `oncotriage.agent.deps` is legitimately already there.

**Proved rather than asserted:** a replay was run with `torch` and
`transformers` blocked at the import hook and `fastembed.SparseTextEmbedding`
subclassed to raise on construction. It completed clean and touched none of the
three. The poison was shown to be real by the negative control — with `deps`
imported first at `=0`, `assert_local_models_deferred()` fires and all three
factories die on the blocked import or the poisoned constructor.

**THIS PASS CONVERTS THE HARNESS THAT VERIFIES EVERY OTHER PASS, so "12/12
replayed clean" proves nothing by itself.** A harness that has stopped OBSERVING
also replays clean. So a real behaviour change was planted in the pipeline and
the replay was required to report it. Two plants, in place, each hashed before
and after with the restore asserted byte-identical:

- **`agent/retrieval.py`, `RRF_K` 60 → 61.** Caught on **`normal_1`**, as a
  replay MISS on `recordings.cross_encoder` — the fused pool reordered, so the
  cross-encoder was handed a different set of trial texts and the recording had
  no entry for that digest. Exit 1.
- **`agent/terminal.py`, `matches.sort` descending → ascending** — a violation of
  `node_finalize`'s own documented contract. Caught on **`normal_1`**, 10 field
  differences, named: `stage5.verdicts[0].nct_id`
  (`NCT06058650` → `NCT06839001`), `stage5.verdicts[0].match_score`
  (0.24 → 0.08), `stage5.verdicts[0].trial_number`, and their pairs at index 1.
  Exit 1.

Both restored byte-identically and the suite went back to **12/12 clean, exit
0**. A THIRD plant is recorded because it did NOT fire and the reason is a fact
about the corpus, not a hole in the harness: dropping `deduplicate_by_display`
from `medication_count` changes nothing, because File 07 already de-duplicates
upstream — measured, raw and deduplicated counts are equal on all twelve
fixtures. A plant that is not a behaviour change is not a test of the harness.

**THE FIXTURE FORMAT IS FROZEN AND THAT WAS PROVED BY BYTES.** `SCHEMA_VERSION`
is 3, the twelve fixtures on disk are v3, `load_fixture` refuses a mismatch. The
pre-split `write_fixture` / `fixture_path` / `FIXTURE_SUFFIX` were **unparsed out
of `git show HEAD:` and exec'd into a throwaway namespace**, never retyped, and
handed the same twelve fixtures as the converted path: **12/12 byte-identical**,
same sha256 each. The negative control — one fixture written with `mtime=1`
instead of `mtime=0` — diverges, so the comparison can fail.

**`fixture_replay.py`'s FIVE REFUSALS RUN IN THE SAME ORDER**, and that order
is unchanged: the dependency seam **negative control first** (the assertion must
FAIL with no override installed, or it proves nothing), then the positive
control, then the OpenAI tripwire, then the pinned collection NAME, then its
CONTENTS digest, and only then a fixture. Note that the collection checks are
first among the checks about FIXTURES but the seam probe precedes them — that
ordering is what was shipped and what is kept.

**`diff_tunables()` HAD TO STOP READING `globals()`.** It compared each recorded
tunable against `globals().get(name)`, which under the exec chain was the shared
namespace File 03 had filled. In a module that expression sees the replay
module's own globals, where not one of the eighteen tunables is defined — so
**every fixture would have reported all eighteen as `<no longer defined>`, on
every run**, burying a real diff under eighteen lines of false finding. It reads
`oncotriage.config` now, which is where File 03 got them.

**`main()`'s local `paths` was renamed `fixture_paths`.** The module imports
`oncotriage.paths` as `paths`, and a name assigned anywhere in a function is
local for the whole of it. It would not have failed today — `main()` reads no
other `paths` attribute — which is exactly what makes it the trap File 47 check
2g exists to catch.

**FILE 34 IS DELIBERATELY BUILT THE WAY IT LOOKS WRONG, and both halves survive.**
`oncotriage/evaluation/cohort_diff.py` imports the LIVE `has_cancer_diagnosis`
from `oncotriage.fhir.clean`, so it measures the code that will actually build
the cohort, and it keeps its own `_LEGACY_EXCLUDE_VERIFICATION` — the pre-fix
File 05 defined a copy that overwrote File 08's under the exec chain, so
consolidating it would make the LEGACY arm agree with the CURRENT arm wherever
the two sets differ, which is the disagreement the file exists to find. The
registry comes from `clean.cancer_registry()`, **not** `agent.deps`: this
measures the deletion path, and a stub installed for an agent test must not
change what it reports.

**File 28 loads `oncotriage_settings.py` BY LOCATION no longer.** Its argument
was that it is not in the exec chain and should not pull in File 01's model and
client imports for two database queries — right at the time, obsolete now:
`oncotriage.paths` imports only `oncotriage.settings` and resolves lazily. The
by-location load would also register a SECOND copy of the settings module under
the name `oncotriage_settings`, beside the one `oncotriage.paths` already holds
— two `_RESOLVED` caches answering the same question. Both database paths are
now **required arguments with no defaults** (`select_samples(source_db,
output_db)`), on the `empty_database(db_path, flag)` precedent, because the
function `os.remove`s the output database before rebuilding it.

**`oncotriage/ablation/analysis.py` imports matplotlib at module scope**, the
second module allowed to after `oncotriage/fhir/explore.py` and for the same
reason: nine of its functions draw, and File 47 section 2 already pre-imports
matplotlib, seaborn and pandas before arming its traps. scipy stays inside the
three function bodies that use it. (**Pass 20f-4 moved those nine functions and
that import to `oncotriage/ablation/figures.py`**; the exception is unchanged in
kind and now lives in a small file.)

**TWO THINGS IN FILE 26 WERE REPORTED, NOT FIXED**, because a conversion pass
whose acceptance criterion is that nothing changed is the wrong place for
either. **PASS 20f-1 FIXED BOTH** — see "Pass 20f-1" below:

- **`save_ablation_checkpoint()` catches `OSError` and `pass`es with no record.**
  It is the inner handler around the unlink of the temp file after a failed
  atomic write (`oncotriage/ablation/study.py`, in the `except OSError` that
  follows the `os.replace`) — *not* line 188 of the old File 26, which is inside
  the `json.dump`. The exception audit lists it as SILENT and item 11a's sweep
  did not reach it. It is the one exception in the file caught without a counter
  or a message. **Closed at 20f-1: `CHECKPOINT_WRITE_FAILURES`.**
- **`ablation_db()` is the LAST IMPLICIT-PATH DATABASE WRITER in the project.**
  Every other writer takes its path as an argument — `log_inference(db_path=)`,
  `log_drift_metrics(db_path=)`, `empty_database(db_path, flag)`,
  `select_samples(source_db, output_db)` as of this pass — and this one does
  not, so there is no way to point a study run at a scratch database and no
  isolation test can be written for it. **Closed at 20f-1: `--db`, and
  `tests/test_ablation_db_isolation.py` is the test that became possible.**

**A THIRD FINDING, from File 47 check 2h:** `TERMINAL_ERROR` in
`fixtures/capture.py` is declared and read by nothing, and `git grep
TERMINAL_ERROR HEAD` returns exactly one line — its own assignment in File 45.
It was dead before the move; the move is what made check 2h able to see it. **PASS
20f-3 MADE IT LOAD-BEARING** — `verify_recording_complete()` names the
error-handler case explicitly and the exemption is gone. Pass 20e predicted that
branch would "improve the diagnosis and change no outcome"; the first half held
and the second did not, which is why it was worth doing in a pass that could say
so. The old arm refused an error run only when NO Stage 5 exchange was recorded,
so an exception thrown AFTER Stage 5 answered left the fixture written, with a
prefix stamped by the error handler's placeholders. That fixture is refused now.
**Twelve fixtures written by the changed writer are byte-identical to twelve
written by the pre-change writer** (`git show HEAD:`, exec'd rather than
retyped), so nothing on disk moved. What follows is what pass 20e argued at the
time. It was
**exempted with an argument** rather than deleted (it completes a closed
three-member vocabulary whose other two members are read, and naming only two
would tell a reader the third is impossible) and rather than made load-bearing
(the branch that would read it sits in `verify_recording_complete()`, which
already refuses that fixture through another arm, so the change would improve a
diagnosis and alter no outcome — a behaviour edit inside a pass that promises
none). Recorded as a follow-up.

**Equivalence: 164 definitions across the six modules, 151 matched to an origin
in `git show HEAD:`, 110 byte-identical after `ast.unparse`, 41 differing, 13
with no counterpart, 1 carrying a decorator.** Every diff was printed and
classified, and all 41 fall in five categories: path accessor
(`ABLATION_DB`/`OUTPUT_DIR`/`FIXTURE_ROOT`/`_REPORT_*`/`checkpoint_path`/
`data_fhir_path`/`PRODUCTION_INFERENCES_PATH`), client accessor
(`qdrant_client` → `config.get_qdrant_client()`, `_CANCER_REGISTRY`/`_MESH_FILTER`
→ `deps.get_*`, `_CANCER_REGISTRY` → `clean.cancer_registry()`), db_path
(`_database_logger.resolve_inference_db_path`), env ordering (the
`assert_local_models_deferred()` call), and guard (the re-expressed isolation
check, the `log_inference` tripwire). **Two diffs fall OUTSIDE those five and are
called out rather than folded in**: `diff_tunables`' `globals()` →
`getattr(config, ...)`, and `main()`'s `paths` → `fixture_paths` rename. Both are
argued above; both are required rather than cosmetic. The 13 without a
counterpart are the new accessors and guards, each named. The one decorated
definition, `compute_collection_digest._page` carrying `@qdrant_retry`, is
NESTED — a top-level walk would have reported it absent, which is the shape that
hid `api/server.py`'s four endpoints from the first version of File 47's
decorator scan.

**File 47 was 283 checks** at the end of pass 20c-3d (from 278), with the three new
subpackages in its recursive scan, the six new modules named in its per-module
import sweep, `compute_collection_digest._page` in its decorator inventory, and
the floors raised to 75 package files / 61 modules. All six new modules import
under all twelve traps with the project root pointed at a directory that does not
exist, opening nothing and pulling in no heavy library.

### The component tests (pass 20d-1)

**Eleven files, not a range.** Files 30, 31, 32, 33, 35, 36, 37, 38, 39, 40 and
41 moved into `tests/` and were renamed for what they cover. Each imports the
package with **no exec bootstrap at all** — no `exec()` of Files 01/02/03, no
`exec_chain` — and each reports the **identical pass count** it reported before:
103, 54, 58, 136, 39, 79, 103, 172, 105, 104, 112. Total 1,065, zero failures,
exit 0, before and after.

**[`tests/FILE NUMBER MAPPING.md`](tests/FILE%20NUMBER%20MAPPING.md) is the
mapping, and it is an artefact rather than a memory** — every note, commit
message and both Word documents name these files by number.

**File 34 is NOT one of them.** It is a read-only cohort-selector comparison,
converted in pass 20c-3d to `oncotriage/evaluation/cohort_diff.py`; the sequence
document's "Files 30 to 44 are test files" is wrong about it and about Files 42,
43 and 44, which are tests but stay in the collision matrix.

**THE HAZARD WAS THE PATHS, NOT THE IMPORTS.** Every one of the eleven computed
`_code_dir` from `__file__` and resolved things relative to it — the numbered
bootstrap files, and in seven cases a package module it ast-parses. Moved into
`tests/`, every one of those is one directory off. **The repair is not to walk up
a directory: a parsed module's path comes from THAT MODULE's own `__file__`**
(`os.path.abspath(_fhir_parser.__file__)`, `os.path.dirname(...(_agent_pkg.__file__))`),
so a future move cannot break it again — and so the file under inspection is
provably the one this process imported rather than a same-named copy. The two
places that genuinely need the repository root (File 20's shim, File 03) derive
it from `oncotriage.__file__`, never from the test's own location.

**FOUR FILES LOST A PROTECTION MECHANISM AND SAY SO.** Files 36, 37, 38 and 40
used to `exec("14- Database Logger.py")` *after* rebinding `inferences_path`, so
the File 14 shim's `log_inference` wrapper picked the rebound value up through
`globals().get(...)`. They import `oncotriage.storage.database_logger` directly
now, so that redirect reaches the writer **not at all**. Measured before the
move rather than assumed: every `log_inference` call site in all four already
passed `db_path=` explicitly and asserted on the path the writer returns, and
`initialize_database` already took its path as an argument. Each file's comment
block was rewritten to claim ONE mechanism instead of two — a comment claiming
two protections while one is inert is worse than having one. **File 14's wrapper
now has no consumer in the repository**; it is kept, and that fact is recorded
beside it, because removing it is a decision about File 14's surface.

**TWO DIFFS FALL OUTSIDE import / path / bootstrap, and both were required
rather than cosmetic:**

- **`tests/test_fhir_birth_date_and_demographics.py` section 3** rebound
  `DATA_SNAPSHOT_DATE` in its own globals and required
  `get_age_reference_date()` to raise on four bad values. That reached the
  function only through File 02's wrapper. The package function reads
  `config.DATA_SNAPSHOT_DATE` **at call time** — its own docstring names that as
  the supported patch point — so the value is set on the config module now. Same
  call shape, same four values, same raise, same count. Leaving it alone would
  not have gone green; it would have turned four raises into four failures.
- **`tests/test_monitoring_ecog_availability_drift.py` section 8b** asserted that
  File 20's shim re-exports nine names, by comparing **this file's globals**
  against the package module. Once the file imports those names, that is an
  imported name compared with itself: **true by construction, ten checks that can
  never fail** — the exact blind spot CLAUDE.md names, an equivalence proof
  cannot see a check that has stopped checking. The shim is exec'd into a
  throwaway namespace now and that namespace is inspected, which tests the shim
  itself rather than through one caller. **Shown to fail, 2026-08-06:** stripping
  `ks_test_drift` from an in-memory copy of the shim's source gives 111 passed,
  1 failed, the other nine still passing, shim sha256 unchanged.

**Equivalence: 1,784 top-level statements byte-identical after `ast.unparse`,
149 replaced, 0 added, 4 removed; 123 definitions byte-identical at every
nesting depth, 2 differing, 1 added, 0 removed, 2 decorated.** The comparison is
over the WHOLE module-level statement sequence, not only definitions — these are
procedural scripts whose content is mostly top-level `check()` calls, so a
definition-only proof would have covered almost nothing. Definitions are keyed by
*qualified* name and rendered with `ast.unparse`, which includes the decorator
list, so pass 20c-3c-1's `@st.fragment` defect cannot recur. The 4 removals are
the four `exec("14- Database Logger.py")` blocks; the 2 differing definitions are
`_function_source` / `_function_body` dropping their `_code_dir` prefix; the 1
addition is `_exec_shim`.

**References the move breaks were found by grepping the WHOLE repository for
each of the eleven filenames AS A STRING**, not for their symbols — pass 20c-3d
found File 40 reading File 26 by filename, which a name-grep cannot see. 46
occurrences across 17 files, all prose except one: **`44- Snapshot Date Rot
Test.py`'s `_SUITES`**, which runs two of the eleven as subprocesses. That is the
only functional edit made to a 42–49 file in this pass (plus its display line,
which sliced `s[:2]` to print the file number).

**A top-level `tests/` directory interacts with neither `pyproject.toml` nor
File 47's subpackage cross-check.** `pyproject.toml`'s `packages` list is
explicit and auto-discovery is off, so `tests/` is not shipped; there is no
`__init__.py`, so it is not a package; and File 47's subpackage scan walks
`oncotriage/` only, so it neither sees `tests/` nor should.

**FILE 47's CHECK 2h IS THE ONE THAT COULD HAVE BEEN AFFECTED, AND MEASUREMENT
SAYS IT WAS NOT — this pass predicted a failure that did not occur, and the
prediction is recorded because the risk it names is still there.** Check 2h asks
whether every module-level constant in the package is read by something,
somewhere in the repository, and its read corpus is `_PKG_FILES` plus the
top-level `.py` files of `03- Code/` — an `os.listdir`, not a walk. Moving
eleven readers into `tests/` therefore removed them from that corpus, and a
package constant that ONLY they read would now report as never-read. **It does
not happen: File 47 passes at 283 checks, zero failures, with no edit**, so no
such constant exists today. **The latent hole is real and File 47 was left
untouched by instruction:** a constant added tomorrow whose only reader is a test
in `tests/` will be reported as dead. The fix is one line — make `_REPO_PY` walk
`tests/` as well — and it belongs to whichever pass next opens File 47.

### The rest of the suite (pass 20d-2)

The second half of the test move: the six that patch and read source, plus the
runner, plus the two fixture entry points. **Every measured pass count is
identical** — 197, 16 (14 planted / 14 caught), 10 (6 subprocess runs), 283, 170,
194 — and the counts your notes carried were verified rather than trusted.

| Was | Is |
|---|---|
| `42- Cancer Code Registry Audit Test.py` | `tests/test_registries_cancer_code_claims_audit.py` |
| `43- Cancer Code Registry Audit Negative Control.py` | `tests/test_registries_cancer_code_claims_audit_control.py` |
| `44- Snapshot Date Rot Test.py` | `tests/test_config_snapshot_date_rot.py` |
| `47- Package Split Test.py` | `tests/test_package_invariants.py` |
| `48- Degraded Dependency Test.py` | `tests/test_degraded_dependencies.py` |
| `49- Database Query Layer Test.py` | `tests/test_storage_query_layer.py` |
| `run_serial_tests.py` | `tests/run_serial_tests.py` |
| `45- Fixture Capture.py` | `fixture_capture.py` — **renamed, not moved** |
| `46- Fixture Replay.py` | `fixture_replay.py` — **renamed, not moved** |

**FILE 47 WAS NAMED FOR A PASS AND THE PASS ENDS AT 20e.** It is
`test_package_invariants.py` now, and its docstring leads with the invariants it
actually holds — import purity under twelve traps, the config↔utils cycle, one
BM25 construction site, no shadowed imports, no never-read names, every
subpackage declared, the deps seam under `MAX_WORKERS` threads, the shims still
re-exporting. The audit/control pairing stays visible by construction: the two
names differ only by a `_control` suffix and sort adjacently.

**FILES 45 AND 46 STAYED AT THE TOP LEVEL, renamed without numbers.** They are
not tests — they are a manually-run gate that items 22 and 64 consume, capture
COSTS MONEY (twelve real end-to-end runs), and nothing runs them as part of a
suite. Putting them beside the suite would invite exactly that. **Their fixture
resolution was verified rather than assumed**: `fixtures/capture.py:fixture_root()`
globs `paths.main_path` — the PROJECT root — not the code directory, so where
the entry point sits has no bearing on where fixtures are found.

**THE RUNNER MOVED INTO `tests/`** because every test it names is there, and
`make serial-tests` is the documented entry so the path is typed once. It
imports nothing from the project deliberately: it is a process launcher, and
`python tests/run_serial_tests.py` must still report a missing test file rather
than dying on an ImportError when the package is what is broken.

**THREE FILES DECLINE THE `oncotriage.__file__` DERIVATION, and each says why.**
The audit control and the snapshot-date test may not import the package — one
would cache a pre-plant copy of the very module it patches, the other would bind
`DATA_SNAPSHOT_DATE` into a process whose whole point is that the subprocesses
read it from disk. `test_package_invariants.py` may not import it either, and
its reason is the strongest: section 2 proves that importing the package pulls
in no model-bearing library, and it proves it by arming traps in a subprocess
that has imported nothing yet. All three derive the root from `__file__`'s
parent and **carry a hard guard** — not a `check()` — naming the file they could
not find, because a wrong root there is not one failure but every failure, each
with a misleading message.

**File 43's bytecode guard is intact and was re-proved after the move**:
`_clear_pycache()` plus `PYTHONDONTWRITEBYTECODE=1` on every subprocess, with
`_PYCACHE_DIR` derived from `_FILE_08` so it follows the target automatically.
14 planted, 14 caught, `sha256` before == after.

**File 49's git selection was proved to still resolve**, not assumed: `_CODE_DIR`
now comes from `oncotriage.__file__` because it is also the **git cwd**, and
`git log -- <pathspec>` resolves the pathspec relative to cwd — run from
`tests/` it would match nothing, `_newest_revision_where` would return
`(None, None)`, and every negative control in sections 3, 4b, 5 and 7 would
report a failure for the wrong reason. Measured: `_PRE_FIX_REV` = `6a029ac`,
`_ITEM38_REV` = `835d2d9`, both blobs non-empty, no `[git]` errors, 194/0.

**File 47's never-read-name scan had a blind directory and now walks `tests/`.**
That scan is the only thing in the repository that can see a name which is
declared and never read — the shape the `ast.unparse` equivalence proof cannot
see by construction, and the shape that shipped `PASSWORD_SOURCE_ARGUMENT`. Its
corpus was `_PKG_FILES` plus an `os.listdir` of the code directory, so after
pass 20d-1 it was blind to eleven readers and after this pass to eighteen. **A
corpus that silently covers less does not fail — it reports FEWER findings,
which reads exactly like a clean package.** Demonstrated out of band, with both
touched files hashed and restored byte-identically:

| case | result |
|---|---|
| a package constant read **only** from `tests/`, corpus as shipped | **not** reported — 283 passed, 0 failed |
| the same plants against a copy with the `tests/` walk stripped | **reported** — 282 passed, 1 failed |
| a package constant read by nothing anywhere | **reported** — 282 passed, 1 failed |

The middle row is the one that matters: it is the false positive the widening
removes, and without it the first row would also be satisfied by a scan that had
stopped working.

**`tests/` is in `.dockerignore` now, and the two patterns that were supposed to
cover it never did.** Docker matches `.dockerignore` with Go's `filepath.Match`,
where `*` does not cross a `/`, so `test_*.py` matches only at the context root.
The tests were `30- Histology Extraction Test.py` before pass 20d-1 (no
underscore, and a space) and `tests/test_*.py` after it (nested) — **so the whole
suite has been shipping inside the image the entire time.**


### What pass 20e retired and re-derived

**Four pinned inventories retired, and each retirement is argued where the check
used to be** rather than in a commit message. All four answered one question —
"does this shim still deliver to the exec chain what the exec chain reads out of
it" — and the exec chain has no readers.

| Retired | Was pinned | Why it has no subject |
|---|---|---|
| `_PRE_20C_NAMES` / `_PRE_20C_COUNTS` | the ast-derived surfaces of Files 01, 02, 03 at commit `3780ba1` | all three deleted |
| `_PRE_2A_RUNTIME_NAMES` | the runtime surfaces of Files 08, 09, 10 | 08 and 10 deleted; 09 is a thin entry point with no re-exports |
| `_PRE_2B_RUNTIME_NAMES` | Files 07 and 14 | 14 deleted; 07 is a thin entry point |
| `_PRE_2C_` / `_PRE_3A_RUNTIME_NAMES` | Files 13 and 05 | both thin entry points |
| section 5c's `_LazyAgentDependency` demonstration | the proxy's six delegating protocols and its non-resolving `__repr__` | the class lived in File 13's shim |
| section 6's late-binding wrappers | File 02's three `globals().get(...)` wrappers | File 02 deleted |
| the drift test's section 8b | File 20's shim delivering sixteen names | shim deleted — **and its only consumer was that check** |

**THREE OF THEM WERE RE-DERIVED RATHER THAN DROPPED, and two of the three are
wider than what they replaced.**

- **Section 1c is new and is the widest thing in the file.** The old inventories
  asserted a property of ten named files; this scans **every `.py` in the
  repository** for a call to `exec_chain`, a call to `exec()` outside the
  argued allowlist (one member then, five now), or a by-location module load.
  The old checks could not have caught a *new* file that started exec'ing; this
  does — and it did, twice, in the promotion pass.
- **Section 5 keeps the half of the old section that still has a subject** —
  "does every name imported out of the package actually exist there" — and asks
  it of **every file in the tree** rather than of three, without running any of
  them (which matters: running Files 13, 18 or 19 costs money). It adds the
  structural definition of a thin entry point: no module-level
  `from oncotriage... import` whose name the file never reads, which is what a
  re-export IS.
- **Section 5c keeps the three facts about the PACKAGE** the proxy demonstration
  established — `peek` / `resolution_state` / `is_resolved` / `cached_keys` must
  not build (measured by counting factory calls, the only thing that separates
  the two shapes), `peek` must return `UNSET` rather than `None` because
  `MESH_FILTER` is legitimately `None`, and `RESOLUTION_STATES` is a closed set
  a caller may branch on exhaustively. Without the third, that tuple goes back
  to being a declaration nothing reads and check 2h reports it.

**The counts moved and here is every one of them.** 283 → **235** for
`test_package_invariants.py`, 112 → **111** for
`test_monitoring_ecog_availability_drift.py`, 170 → **172** for
`test_degraded_dependencies.py`. Every other test reports **exactly** what it
reported before: 103, 54, 58, 136, 39, 79, 103, 172, 105, 104 for the component
tests, and 197 / 16 / 10 / 194 for the rest.

**PASS 20e's OWN NEW CHECKS FOUND THREE DEFECTS IN THEMSELVES BEFORE THEY
SHIPPED, and all three came from negative controls.** Section 1c's first version
matched the bare substring `exec_chain` inside string literals and reported nine
DOCSTRINGS — including `oncotriage/utils.py`'s record of why `exec_chain` was
deleted — so the documentation of the fix read as the defect. Its second version
looked for a numbered filename *inside* the `exec()` call, which not one of the
five real bootstraps was written as (`open(_code_dir + "01- Imports.py")` on one
line, `exec(_fh.read(), globals())` on the next) — it would have missed every
file it was written to catch. Its third missed `ns["exec_chain"](...)`, the
subscript form, which is what the retired shim probe itself used, because the
quote characters sit between the name and the paren. **String literals are
RE-PARSED as Python now**, which catches every call form at once and, as a side
effect, stops reporting prose.

**AND THE SHIM DELETIONS UNMASKED SEVEN DEAD NAMES IN THE PACKAGE.** Check 2h
counts a `from oncotriage.X import NAME` as a read, so for as long as a shim
re-exported a module's whole surface the scan could not see a dead name in that
module — the shim read everything by construction. Removing them surfaced
`_NOT_EVALUABLE_REASONS`, `_HISTORICAL_MED_STATUSES`, `_ECOG_LOINC_PANEL_CODE`,
`_ECOG_LOINC_INTERPRETATION_CODE` (all four the `TERMINAL_ERROR` shape — closed
vocabularies and decision records, exempted with an argument each) and
`BATCH_SIZE`, `EXPANSION_TEMPERATURE`, `_PATIENT_STAGE_RE` (**genuinely dead**,
exempted and recorded as follow-ups: the first two are documented TUNABLES that
do nothing, which is a configuration-surface decision, and the third is an
unused compiled regex that should simply go). **Pass 20f-2 closed the first two
by deleting them** — see "Two dead tunables" below; **pass 20f-3 deleted
`_PATIENT_STAGE_RE` too**, which pass 20e had called "the one of the three that
should simply go": it differed from `_SNOMED_DISPLAY_STAGE_RE` only by an
optional `tnm ` prefix that the survivor's `\b` already admits (measured —
`extract_patient_stage()` still resolves "TNM stage 3 (disorder)" to 3). Deleting `exec_chain` also left
`import os` unread in `oncotriage/utils.py`, which check 2h(i) reported on the
first run — the smallest possible instance of the same thing.

**One re-export finding is real and pre-existing:** `24- Airflow Manager.py`
imports `stop_airflow` and `trigger_dag` at module scope for its byte-verbatim
COMMENTED menu, and comments are invisible to an AST walk. Exempted with the
argument the file already carries, and it goes when that menu becomes a real
argparse CLI — the follow-up pass 20c-3c-2 already recorded.


### Files 18 and 19 exit non-zero when they tested nothing (pass 20g)

**THREE RUNS THAT DID NOTHING USED TO LOOK LIKE RUNS THAT PASSED, and all three
are exit-code changes rather than behaviour changes to what either file tests.**
Neither file's request sequence, slice, timeout or verdict ordering moved.

| Was | Is |
|---|---|
| File 19: the batch loop counted errors, printed `Errors: 2`, and **nothing read the count** — every POST could return HTTP 500 and the process exited 0 | a non-zero `error_count` returns 1, and each failure is named with the bundle and the reason |
| File 19: `fhir_files[410:412]` on a corpus of fewer than 411 bundles is `[]`, so the loop ran zero times, `Success: 0/0` printed and the exit was 0 | an empty selection is a recorded failure naming the glob pattern, the number of bundles that matched it and the slice that emptied it |
| File 18: Test 3's `else: print("No FHIR files found.")` and Test 4's `else: print("Need at least 2 FHIR files…")` — two skips, neither affecting the outcome | each records a failure naming what was missing and which pattern was searched, routed into the summary a non-200 already used |

**FILE 19's IS A CONTRACT CHANGE AND IS STATED AS ONE.** Its exit code used to
be set by the production row-count verdict and by nothing else. There is no
caller reading it today — the filename appears only in prose — which is what
makes the change cheap now and expensive to postpone, because the harness item
20d builds would inherit the old contract.

**THE ROW-COUNT VERDICT IS STILL LAST AND STILL OVERRIDES.** File 18 already
printed its failure summary above the guard, for the reason recorded there: the
guard returns the moment it finds the count moved, so a summary below it would
be dropped by exactly the runs that had two things wrong instead of one. File 19
gained the same block in the same position. The guard's verdict is still the last
thing on the terminal and still returns 1 on its own finding whatever the tests
did.

**DEMONSTRATED AGAINST STUB SERVERS, TEN SCENARIOS, BEFORE AND AFTER, NO MONEY
SPENT.** The stub answers the same four routes with a configurable status and
calls no model. Each scenario runs with `ONCOTRIAGE_MAIN_PATH` pointed at a
throwaway project root, so `data_fhir_path` and `inferences_path` both resolve
inside it; the scratch `inferences.db` carries the **production `inferences`
schema read out of the real `sqlite_master` over a `mode=ro` connection**, so the
row-count control is exercised against the table it will meet for real. The
BEFORE arm is the committed file out of `git show HEAD:`, never a retyped copy.

| scenario | before | after |
|---|---|---|
| 18-A  2 bundles, stub answers 200 | 0 | 0 |
| 18-B  **no bundles** — Tests 3 and 4 both skip | **0** | **1** |
| 18-C  **one bundle** — Test 4 skips | **0** | **1** |
| 18-D  no bundles AND the server writes a production row | 1 | 1 |
| 18-E  all four tests pass but the server writes a row | 1 | 1 |
| 19-A  412 bundles, stub answers 200 | 0 | 0 |
| 19-B  **every POST returns HTTP 500** | **0** | **1** |
| 19-C  **corpus of 5: the slice selects nothing** | **0** | **1** |
| 19-D  POSTs 500 AND the server writes rows | 1 | 1 |
| 19-E  every POST succeeds but the server writes rows | 1 | 1 |

Rows A are the controls: the change does not break the passing path. Rows D and E
are the override check — the failure summary is asserted to print *before* the
string `Production database guard`, and 19-E's last three lines are the guard's
own failure message with every POST having succeeded. The production database
was read at 1,106 rows before the matrix and 1,106 after.

**File 18 also stopped printing `All tests complete.` above a run in which two
of the four never ran.** It reads `All tests attempted.` and the summary below it
says which.


### The Qdrant endpoint has one deliberate override, and a clean stack cannot report healthy while unusable (the Docker pass)

**FOUR THINGS, AND THE FIRST ONE SETTLES A DISAGREEMENT BETWEEN TWO DOCUMENTS
IN THIS REPOSITORY.** Measured 2026-08-06/07 by running it, not by reading:

| Question | Answer |
|---|---|
| `QDRANT_URL` in `05- Keys/.env` | `https://bd717e5f-…us-east-1-1.aws.cloud.qdrant.io` — **Qdrant Cloud**, mtime 2026-08-03, never repointed |
| `config.get_qdrant_url()` on the HOST | the same cloud URL |
| ...INSIDE the container (before this pass) | the same cloud URL |
| points at that URL | **12,067** on `trial_criteria` → `trial_criteria_20260803_104642` |
| the compose `qdrant` service | `{"collections":[]}` — **nothing**, and it was queried by nothing |

`DOCKER CLEAN BRING-UP.md` §2b and `docker-compose.yml`'s comment block both
recorded the cloud measurement and were **both right**. Nothing in this
repository ever claimed the .env had been repointed at a local Qdrant — the
whole-tree grep for such a claim is in the Docker pass's report. **The disagreement
is between the compose file and ITSELF**: its header advertises
`Qdrant: http://localhost:6333/dashboard` as one of the stack's four access
points while its own `fastapi` comment records that the service is used by
nothing.

**1. `ONCOTRIAGE_QDRANT_URL` IS THE DELIBERATE OVERRIDE, AND THE POP THAT MADE
IT NECESSARY IS KEPT.** `paths.load_env_keys()` POPS `OPENAI_API_KEY`,
`QDRANT_URL` and `QDRANT_API_KEY` out of `os.environ` and reloads all three from
the .env with `override=True`, so `QDRANT_URL: http://qdrant:6333` in
docker-compose.yml was set, popped, and reached nothing. That pop exists so a
stale exported credential cannot shadow the credentials file, and it is
untouched. What is added is a second tier that beats it, on the
`ENV_INFERENCES_DB` precedent: a project-prefixed name, resolved by its own
function (**not** `_from_env`, which appends `os.sep` — harmless-looking on a
URL and fatal on Windows), returning `(value, source)`, with
`oncotriage/config.py` printing which source won before the first request. All
four required behaviours were driven in separate subprocesses, because `config`
caches per process:

    no override                    -> .env url, .env key
    QDRANT_URL exported            -> .env url, .env key      (accident still loses)
    ONCOTRIAGE_QDRANT_URL          -> that url, NO KEY AT ALL
    ...and ONCOTRIAGE_QDRANT_API_KEY -> that url, that key
    BOTH set                       -> the project-prefixed one wins
    a value that is not a URL      -> RAISES, naming the variable

**THE KEY DOES NOT FOLLOW THE URL, and that is the point rather than an
omission.** With the URL overridden and no key named, **no key is sent** — the
.env's is deliberately not consulted. A key is issued by one endpoint for one
endpoint, and forwarding a live Qdrant Cloud credential to a host named in an
environment variable is credential exfiltration by configuration, of the kind
that leaves no trace because the request succeeds. Redirecting to a second
authenticated cluster without naming its key gets a 401 that names the host and
is one variable away from fixed.

**2. AN EMPTY INDEX RAISES INSTEAD OF ANSWERING.** Every Qdrant call in Stage 2
SUCCEEDS against a collection with zero points and returns an empty list; the
graph routes to `node_no_candidates`, the API returns 200 with "no eligible
trials found", and the stored row is well-formed — the same output a genuinely
unmatchable patient produces. Nothing raises, no counter moves.
`oncotriage/agent/readiness.py` answers with a **closed four-state vocabulary**
(`populated` / `empty` / `absent` / `unverifiable`) and the two callers apply
different policies, each written at its own call site:

- **Stage 2, per request:** `empty`/`absent` RAISE `EmptyIndexError`;
  `unverifiable` is COUNTED (`INDEX_PROBE_FAILURES`), printed and CONTINUES —
  a probe that could not run is not evidence of emptiness, and blocking on it
  would put a new hard dependency in front of machinery designed to degrade.
  The gate runs **before** the channel `try`, because each channel is wrapped in
  `except Exception` and a raise from inside would be absorbed into "one channel
  was unavailable" — the report that hides this exact fault.
- **API startup, and every `/health`:** `unverifiable` is NOT ready. A startup
  probe can afford to demand proof.

`absent` is deliberately not folded into `empty`: the operator's next command
differs. The probe itself RAISES NOTHING — it is a diagnostic, and
`/pipeline/info` routes its trial count through it precisely so the endpoint an
operator asks first survives the failure they are asking about.

**3. "HEALTHY" MEANS "SERVICEABLE".** `GET /health` returns **503** while a
required dependency is missing, which is what `curl -f` in the compose
healthcheck reads. It RE-PROBES rather than reporting what startup found, so a
stack recovers on its own once the index is populated — measured: 503 → 200
immediately, Docker's healthcheck following within one 10-second interval, no
restart. `pipeline_ready` is kept and still means "the graph compiled"; it is
now one field among several rather than the whole answer, because it was the
field that reported `true` while the server was unusable. Item 11a's
`DegradedDependencyError` is **surfaced earlier, not replaced**: its message,
naming both missing files and the rebuild command, is carried verbatim into the
`/health` body.

**4. THE MeSH CORE LOOKUPS ARE PROVISIONED WITH NO MANUAL STEP, AND
`DOCKER CLEAN BRING-UP.md` §3's DECIDING PREMISE WAS NEVER MEASURED.** That
section rejected "bake them into the image" partly because the lookups are
"generated from two multi-hundred-megabyte source files". That is the size of
the SOURCES. The two files `load_mesh_filter()` REQUIRES total **107,282 bytes**
and are built from `desc2026.xml` alone — NLM MeSH, public domain. They are
vendored at `docker/mesh-core/` and seeded into the volume by
`docker/prepare_paths.py` on every start: verified against a sha256 manifest
(a truncated lookup is still valid JSON, so a `json.load` guard would pass it),
written with write-to-temp + `os.replace` so five containers racing on a fresh
volume cannot tear a file, and **never overwriting** a file already there.
The three OPTIONAL crosswalks are UMLS-derived and are deliberately NOT vendored
— that is a licensing question, not an engineering one — so they keep the
documented `docker compose cp` step and their `NOTE:` lines.

They go to an image-only path and NOT to `/app/data/mesh/`: Docker initialises a
fresh named volume by copying the image content at the mount path, five
containers do it at once, and the concurrent `mkdir` fails — the intermittent
bring-up failure pass 20g fixed by emptying those mount points.

**5. THE VERSION LABEL IS DERIVED; THE COUNT OF HAND-MAINTAINED VERSION STRINGS
IS ZERO** (it was one). `LABEL version="1.0.0"` was the site pass 20f-2 named as
a follow-up and left, because "a Dockerfile LABEL cannot read a Python
attribute" — true, and the reason the value arrives as `ARG APP_VERSION`.
`docker/app_version.py` derives it by reading `oncotriage/__init__.py` as TEXT
(the host may not have the package installed, and an `import` there could
resolve to another copy); the Makefile and `docker-compose.yml`'s `build.args`
call it; and a `RUN --check` after the source `COPY` **fails the build** when the
ARG disagrees. **The cost is stated rather than hidden: a bare
`docker compose build` now fails**, printing `make build`. That is the trade for
"a stale label cannot ship", and `docker compose up` on a machine that already
has the image is unaffected.

**WHAT WAS VERIFIED BY RUNNING IT.** A genuinely clean `docker compose down -v`
+ `up`, twice, with nothing copied in by hand: `up -d` returns at t+11.6 s, five
services healthy at t+22 s, `fastapi` **unhealthy** at t+140.6 s naming the empty
index — which is the empty-index probe firing in production rather than in a
test. MeSH `loaded` with no manual step. After migrating the cloud collection
into the sidecar (12,067 points, 65–79 s, free — see `DOCKER CLEAN BRING-UP.md`
§5 and why re-scraping would produce a different corpus), 6/6 healthy. **One live
`POST /match`: HTTP 200 in 159.0 s, $0.18084, 1 inference row + 15 trial_matches
rows, surviving both a restart and a rebuild-and-recreate.** The production
`inferences.db` read 1,106 rows before and 1,106 after. **The cost is not in the
response** — `/match` returns token counts and no dollar figure; it is
`inferences.estimated_cost_usd`.

**EVERY NEW ASSERTION WAS SHOWN TO FAIL.**
`tests/test_docker_qdrant_override_and_readiness.py` is 122 checks, no network,
no keys, no spend, no Docker daemon, and not in the collision matrix (derived:
it writes only in a temp directory and the four repository files it READS are
written by neither of the suite's two writers). A revert harness broke each of
the seven changes in place — with `PYTHONDONTWRITEBYTECODE=1` and an explicit
`__pycache__` clear, the pass 20f-1 lesson — and **7/7 fired, every restore
byte-identical**.

**AND THE HARNESS FOUND THREE DEFECTS IN THIS PASS'S OWN WORK THAT READING DID
NOT.** (i) Section 1 originally asserted on `qdrant_endpoint_sources()`, the
REPORTER; reverting `get_qdrant_url()` to its pre-pass body — the whole defect —
left every check passing. It reads the two functions `get_qdrant_client()` is
built from now, and asserts by AST that those are the two it passes. (ii) A bare
`next(...)` in section 8 raised `StopIteration` when the gate was removed —
exactly the edit the section exists to catch — so the run reported one traceback
where it owed a summary and 114 results. **That is the third time this project
has shipped that shape**, after `tests/test_storage_query_layer.py` and
`tests/test_dashboard_reproducibility_tab.py`. (iii) Three assertions searched
`Dockerfile` and `docker-compose.yml` as strings and were satisfied or defeated
by the COMMENTS EXPLAINING THEM — a file that argues about its own settings
cannot be grepped for them; they read instruction lines and comment-stripped
settings now.

**One pre-existing defect was found and fixed in `tests/test_package_invariants.py`.**
Its config↔utils cycle control planted with the bare substring
`"from oncotriage import paths"`, which this pass's edit to that import line
matched IN THE MIDDLE — so the control spliced its plant into a statement and
produced a SyntaxError-free import of a name `utils` does not export. The copied
package then failed for a reason unrelated to the cycle, and the check whose
whole point is that this import order SUCCEEDS reported a failure that was true
of the control and false of the package. **A control that plants the wrong thing
is worse than no control: it fails, so it looks like it is working.** The needle
is line-anchored now and a future edit hits the `fail()` beneath it by name.

### The Docker image was a pre-20e build; item 21 re-verified against a rebuild (pass 20g)

**THE RUNNING CONTAINER HELD `01- Imports.py`, `02- Utility Functions.py`, `03-
Config.py`, `08-`, `10-`, `14-`, `oncotriage_settings.py` and all twenty numbered
test files** — every one deleted or moved by passes 20d and 20e — so item 21's
report described an image that no longer matched the repository. `/app` is **not**
bind-mounted (item 21 removed that), so the image's baked copy is what runs and a
`COPY . /app/` is the only way it changes.

**THE VOLUME WIPE WAS CONFIRMED SAFE BEFORE IT WAS RUN, three ways.** The local
`qdrant` service reported `{"collections":[]}` while `/pipeline/info` reported
12,067, and `load_env_keys()` inside the container resolves `QDRANT_URL` to the
Qdrant Cloud endpoint — so the container never talks to the sidecar and the
collection the twelve fixtures are pinned against is not in a volume at all. The
qdrant volume held 20 KB and an empty `aliases/data.json`. Two facts worth
recording beside it: `model-cache` was **12 KB**, so no model download was lost,
and `app-data/mesh` was **already empty**, so item 21's hand-copied lookups had
been gone before this session. What the wipe did destroy is `airflow-db` (5 MB):
the Airflow metadata database, the generated web password and the generated DAG —
all three regenerated on bring-up, the password as a new random one.

**PASS 20e BROKE NOTHING IN THE DOCKER FILES, and that was measured rather than
assumed.** Every numbered filename in `Dockerfile`, `docker-compose.yml`,
`.dockerignore`, `Makefile`, `pyproject.toml` and `docker/*` was grepped: the
only two naming a runnable file are `uvicorn "17- FastAPI Server:app"` and
`streamlit run "21- Streamlit Dashboard.py"`, both of which survive. The
`pyproject.toml` `packages` list matches the sixteen `__init__.py` directories
exactly. The image picks up deletions and renames for free because the Dockerfile
does `COPY . /app/` — verified: the rebuilt `/app` has the four renames and none
of the seven deletions.

**WHAT THE REBUILD DID EXPOSE IS AN INTERMITTENT CLEAN-BRING-UP FAILURE, and it
is fixed rather than documented as a retry.** Docker copies the image content at
a mount path into a named volume the first time that volume is mounted; five
services mount `/app/data` and three mount `/app/results`, and `docker compose
up` **creates** all of them at once. The copy is not serialized across
containers:

```
Error response from daemon: failed to mkdir
/var/lib/docker/volumes/Clinical-Trial-Patient-Match-app-results/_data/
fhir_exploration: file exists
```

One of four `down -v` + `up -d` cycles failed exactly that way, leaving three
containers in `Created` and three never created. A second `up` always worked,
because the volume was no longer empty and the copy was skipped — which is what
made it read as a fluke. **The Dockerfile now creates only the four mount-point
roots** (`/app/data`, `/app/results`, `/app/checkpoint`, `/app/airflow_home`),
so there is nothing to copy and the concurrent `mkdir` does not exist. The volume
root still takes its ownership from those directories, which is the property that
`RUN` is for, and the nested tree was **already** being created on every start by
`docker/prepare_paths.py` from `_DOCKER_PATHS` — the Dockerfile list was a
duplicate that could drift. `airflow_home/dags` is created by `write_dag_file()`
itself. **Five consecutive clean bring-ups after the change, all clean, 6/6
running.**

**ITEM 21's CRITERIA, RE-RUN AGAINST THE NEW IMAGE rather than carried over:**

| Criterion | Result |
|---|---|
| every service healthy from a clean checkout | 6/6 healthy |
| API, dashboard, Qdrant, Airflow webserver answering | 200 / 200 / 200 / 200; Airflow reports metadatabase, scheduler and dag_processor all healthy |
| every `_DOCKER_PATHS` name resolves to a path that exists | **14/14 exist and are writable** |
| the container imports `oncotriage` with `PYTHONPATH` unset | yes — `PYTHONPATH` is not set at all; `oncotriage`, `api.server` and `agent.graph` all import |
| the scheduler registers the generated DAG | `trial_refresh_weekly` listed, `list-import-errors` → `No data found`, all three tasks present |
| a second `up` behaves like the first | no-op, 6/6 still healthy; a *restart* re-runs both idempotent steps and reports `exists` ×14 and `current` with an unchanged sha256 |

Two more, checked because a 200 from a Streamlit shell proves nothing: all
**14 dashboard modules import inside the dashboard container**, and
`importlib.import_module("17- FastAPI Server").app is oncotriage.api.server.app`
returns **True** in the rebuilt container.

**ONE CRITERION WAS NOT RE-RUN AND IS NAMED AS SUCH.** "A real request writes a
row" is a live billed Stage 5 call. What was verified instead, and it is weaker:
`resolve_inference_db_path(None)` returns `/app/data/inferences.db`, its parent
exists and is writable, `initialize_database()` creates all three tables there,
and a row inserts and deletes. **The write through the actual pipeline was proven
once in item 21 and is not re-proven here.**

**THE CLEAN BRING-UP IS DOCUMENTED, NOT QUIETLY PATCHED.**
[`DOCKER CLEAN BRING-UP.md`](DOCKER%20CLEAN%20BRING-UP.md) records what a
`down -v` + `up` gives you, what it does not, and the `docker compose cp` step
for the five MeSH lookups that nobody had written down. **No files were copied in
during this pass**, so the numbers above are a genuinely clean stack's. The
missing lookups fail **loudly** — `load_mesh_filter()` raises
`DegradedDependencyError` naming both files and the rebuild command, which is
item 11a working; before it, a clean container would have passed every trial
through the Stage 4 site filter for every patient and said nothing. Why the step
is not automated is argued in §3 of that document; it remains item 21's
follow-up.

**`/pipeline/info` HAD TWO STALE STRINGS AND THE FIRST ONE WAS IN THE CURRENT
SOURCE, not only in the stale image.** `"architecture": "LangGraph StateGraph +
exec() chain"` named a mechanism pass 20e deleted, and `"5. GPT-4o
Criterion-Level Evaluation"` sat three lines above `"matching_model":
"gpt-5.6-terra"` — one response disagreeing with itself about which model Stage 5
calls. **The fix is derivation, not a retyped string:** the stage line
interpolates `MATCHING_MODEL`, so the two cannot disagree again. Same reasoning
as item 38 replacing `pipeline_consistency`'s literals with the config values
that produce its columns. Every other field was checked against the code rather
than assumed — stages 1, 2, 4 and 6 are current, stage 3 now names the checkpoint
exactly (`ncbi/MedCPT-Cross-Encoder`), and the seven `config` values are read
from `oncotriage.config` so they cannot be stale by construction. Two residuals:
`version` stays `"2.0.0"` and is hand-maintained in two places (recorded, not
invented here — where an API version lives is a release decision) — **pass 20f-2
closed that one and found a third site, `pyproject.toml`'s `version = "0.1.0"`;
see "One version number" below**, and the stage-3 checkpoint literal became
`CROSS_ENCODER_MODEL` in the same pass, on the same derivation argument as
stage 5 — and
`trials_indexed`'s `... if qdrant_client else 0` was **inventing a zero** for a
branch meaning "there was no client to ask", which is indistinguishable from an
empty index; it reports `null` with a named `trials_indexed_note` now.


### Five correctness fixes, each a behaviour change (pass 20f-1)

Each was measured before it was changed, each is argued at the code, and each
was demonstrated to FAIL when reverted in place — eight reverts, all eight
fired, all eight restores byte-identical by sha256. **No money was spent**: the
twelve fixtures replay 12/12 clean **without recapture**, which is the criterion
that says the pipeline path did not move.

**1. THE PATH GLOB WAS NONDETERMINISTIC.** `_glob_one` ended `return hits[0]` on
an unsorted `glob.glob`, and glob returns `os.scandir` order — not alphabetical,
not stable across a rename, a restore or a machine. Determinism is a stated
property of this pipeline and this was the one place a PATH resolved without it.
**Measured first: all fourteen local call sites match exactly one directory on
this machine** (code, data, patients, FHIR bundle, trials, MeSH, inferences,
results, FHIR exploration results, ablation results, keys, Airflow,
requirements, checkpoint), so the guard was free to add.
**More than one match now RAISES**, naming the pattern, every candidate sorted,
and which one the pre-20f-1 code would have returned. It raises rather than
taking the sorted winner because that is item 11a's line applied — an ambiguous
CONFIGURATION is fixed by one command, so it raises, where third-party DATA is
counted — and because the cost of guessing is a confidently wrong run:
`oncotriage/fhir/clean.py` UNLINKS bundles out of whichever `*Patients/` won.
**What `sorted()` buys is stated honestly: only a deterministic DIAGNOSIS.** Once
ambiguity raises, `hits[0]` is reached only when there is one hit. That
distinction is why the first version of the test could not fail — it built three
real directories in reverse order and APFS handed them back sorted anyway — and
why the shipped test injects the order through the module's own `glob` attribute
instead.

**2. THE WIPE RAISED ON A DATABASE WITH NO AUTOINCREMENT TABLE.**
`oncotriage/storage/maintenance.py` issued an unconditional `DELETE FROM
sqlite_sequence`; SQLite materialises that table only when something has been
declared AUTOINCREMENT. **The failure was total, not partial** — the raise landed
before `conn.commit()`, so a wipe that hit it deleted nothing and reported an
error about a table the caller never named — and `sqlite3.connect` CREATES a
missing file, so a mistyped path produced an empty database and then exactly
that error. Pass 20b reported it and did not fix it. The presence of the table is
read from `sqlite_master`, **not** wrapped in `try`/`except`: a bare
`except sqlite3.OperationalError: pass` would pass the same tests and swallow a
read-only or locked database too. The gate (`Flag`, both required arguments, no
defaults) is untouched and is checked first.

**3. THE SERVER WROTE A TEMP FILE ON EVERY REQUEST, NOT JUST UPLOADS.**
`parse_fhir_bundle` takes `bundle_or_path` now — a dict or anything `open()`
accepts — and `_run_matching_pipeline`'s `NamedTemporaryFile` → `json.dump` →
parse → `os.unlink` round trip is gone. Because that helper is SHARED, `POST
/match` was paying for a file it never had. `os` and `tempfile` left
`oncotriage/api/server.py` with it. The dispatch tests **for dict**, not against
`str`, so `str`, `Path` and every `os.PathLike` still take the unchanged file
route. **The bundle is read and never written**, asserted both ways. The proof
that no file is touched is behavioural: the helper runs with `builtins.open`,
`io.open` and `tempfile.NamedTemporaryFile` all trapped to raise, and the traps
are FIRED afterwards to show they were armed.

**4. `ablation_db()` RESOLVED ITS OWN PATH** — the last database writer in the
project that could not be pointed anywhere, and therefore the only one with no
isolation test. It takes `db_path` now, threaded through `init_ablation_db`,
`_create_run`, `_finalize_run`, `log_ablation_result` and `generate_summary`,
with `--db` on the entry point. `None` still means production, so every
documented command is unchanged. **An explicit argument is never cached** — the
cache answers a question about the machine, an argument answers one about a
call — and `ablation_summary_json()` follows the database so an isolated run
leaves no production artifact. `tests/test_ablation_db_isolation.py` runs the
whole writer surface twice, told and not told, and shows the "the default was
not touched" assertion holding in one arm and FAILING in the other — against a
**decoy** default, on File 41's precedent, because a demonstration that proved
the point by writing real rows would be the defect it is testing for. **Not
redirected, and recorded as a follow-up:** `_ablation_checkpoint_path()` still
resolves `paths.checkpoint_path`, and `oncotriage/ablation/analysis.py` reads the
production database through its own accessor (it is a reader, so outside this
item).

**5. `save_ablation_checkpoint()` CAUGHT `OSError` AND PASSED.** Item 11a's sweep
missed it because the exception audit's line number was eleven lines off — it
pointed inside the `json.dump` rather than at the unlink. Both handlers count
into `CHECKPOINT_WRITE_FAILURES` (module-level `Counter`, item 11a's shape,
keyed `write:{Type}` / `tmp_unlink:{Type}`), the unlink now prints what it could
not remove, and `main()`'s summary reports the total when non-zero. **The outer
handler is counted too**, not only the silent one: a `tmp_unlink` failure can
only follow a `write` failure, so a count of the second without the first is
uninterpretable. **Recovery is identical** — nothing raises that did not raise
before. Section 6 of the isolation test drives both handlers FOR REAL by making
the temp file's name a directory; no source is patched.

**THE REVERT HARNESS REPRODUCED THE HAZARD FILE 43 GUARDS AGAINST, and it is
worth recording.** Its first version had no bytecode guard and reported two of
eight reverts as MISSED while the identical edit fired when run alone. CPython
validates a `.pyc` on the source's mtime **in whole seconds** and its **size in
bytes**; the two paths reverts each shorten `oncotriage/paths.py` by exactly
eight characters and both writes landed in the same second, so the second run
imported the first one's compiled code. `PYTHONDONTWRITEBYTECODE=1` plus an
explicit `__pycache__` removal — the same two mechanisms
`tests/test_registries_cancer_code_claims_audit_control.py` carries — is what
made all eight fire. **A revert that reports MISSED can mean the check is weak
or that the revert never took effect, and those are not the same finding.**

**FOUR NEW TEST FILES, none of them in the collision matrix** (derived, not
assumed: each writes only inside a temporary directory, patches no repository
file, and the ablation one installs a registry STAND-IN through
`oncotriage/agent/deps.py` precisely so it does not depend on the source text
the audit control plants into): `tests/test_paths_glob_determinism.py` (25),
`tests/test_storage_wipe_all_tables.py` (22),
`tests/test_fhir_parser_dict_input.py` (29),
`tests/test_ablation_db_isolation.py` (43). **The eighteen existing files report
exactly the counts they reported before.**


### Settings and packaging: one name per fact (pass 20f-2)

Seven items, all about declarations that had drifted apart. **No money was
spent**: the twelve fixtures replay 12/12 clean **without recapture**, which is
what says the pipeline path did not move.

**1. THE MedCPT CHECKPOINT HAS ONE NAME: `config.CROSS_ENCODER_MODEL`.**
`"ncbi/MedCPT-Cross-Encoder"` was written out six times with no constant and no
check — `agent/deps.py` twice (the tokenizer and the weights),
`api/server.py`'s stage-3 line, `storage/database_logger.py`'s
`inferences.cross_encoder_model` column, `fixtures/capture.py`'s environment
block, and a seeded row in `tests/test_storage_query_layer.py`. Pass 20c-3a had
given `"Qdrant/bm25"` a constant, one construction site and an AST check, and
left this one alone.

**The operative pair is the tokenizer and the weights, and the failure is
silent.** A cross-encoder tokenizes its (query, document) pair with the
tokenizer trained alongside the weights; point one literal elsewhere and the
token IDs address a vocabulary the embedding matrix never saw. `transformers`
raises nothing — both halves are BERT-shaped and the call is type-correct — so
Stage 3 keeps scoring, `node_cross_encoder_rerank` keeps sorting, and only the
ranking is noise. The other four sites are REPORTS of what ran, and a report
naming a model the process did not load is the artefact somebody trusts later.

**WHERE THE CONSTANT LIVES IS DECIDED BY LAYERING, not by taste.**
`storage/database_logger.py` writes the string on every row and `storage` may
not import `agent`, so the name cannot sit beside its loader the way
`BM25_SPARSE_MODEL_NAME` sits beside `SparseTextEmbedding` in
`oncotriage/embedding.py`. `config` is already imported by all four package
readers and imports none of them; it is also where `EMBEDDING_MODEL` and
`MATCHING_MODEL` — the same kind of fact — already are. **The BM25 name stays
where it is**, because its only consumer that matters is the construction site
in the same file and its comment carries the "changing it rebuilds the index"
warning, which belongs against the line that builds the encoder.

**THE SIXTH SITE STAYS A LITERAL, ARGUED.** `tests/test_storage_query_layer.py`
seeds a row standing in for what a database written months ago holds, beside
`_MODEL_A = "gpt-4o-2024-08-06"` and a hardcoded `pricing_version`. Making a
stored historical value track what the pipeline loads today is the opposite of
what that column means. The check is scoped to the package for that reason and
says so.

**`tests/test_package_invariants.py` SECTION 2f(ii) IS THE ENFORCEMENT, 13
checks.** It counts non-docstring string literals naming the checkpoint by AST
(exactly one, in `config.py`, and it is `CROSS_ENCODER_MODEL`'s value), requires
both `from_pretrained` calls in `deps.py` to be handed that name in either
reference form, requires there to be exactly two of them, and requires no other
package module to call `from_pretrained` at all. Controls: a bare literal and an
**f-string** planted in a copy are both caught; a **docstring** mention is not
(so the prose arguing for the check does not fail it); and the `from_pretrained`
scan is fired on the bare-name form, the attribute form and the literal form.
Docstring tolerance is the same allowance 2f makes by counting calls rather than
text. **The stated limit:** a literal split across concatenation escapes, as it
does for 2f.

**2. `"Qdrant/bm25"` HAD ONE STRAY COPY**, in `fixtures/capture.py`, which now
imports `BM25_SPARSE_MODEL_NAME`. **Importing that name constructs nothing** —
`embedding.py` does `from fastembed import SparseTextEmbedding` inside the
accessor — so check 2f's construction count is unchanged at one, confirmed by
running it rather than assumed.

**3. ONE VERSION NUMBER: `oncotriage.__version__ = "2.0.0"`.** Three
declarations disagreed — `FastAPI(version="2.0.0")`, a second `"2.0.0"` typed
into `GET /pipeline/info`, and `pyproject.toml`'s `version = "0.1.0"` — so
`pip show oncotriage` and the API described the same build two major versions
apart. Pass 20g's follow-up in the server named only the first two, because it
had compared only what was in that file.

**2.0.0 wins, and the direction matters.** It is what the API has told clients
for its whole life; 0.1.0 described the package pyproject's own description
still called "the importable foundation: settings, paths, config, utils", true
at pass 20c-1. Raising the metadata is invisible to every consumer; lowering the
API would announce a two-major-version regression over HTTP that never happened.

**IT IS A MODULE ATTRIBUTE AND NOT `importlib.metadata`.** That reads the
installed dist-info FROM DISK, and `app = create_app()` runs at import — which
section 2 imports with `builtins.open` and `io.open` trapped to raise.
`pyproject.toml` takes the same attribute through `[tool.setuptools.dynamic]`,
which setuptools resolves from the AST at BUILD time, so the edge is
source → metadata and there is no runtime one. **What a reader of
`/pipeline/info` should see:** the same string `pip show` prints and
`/openapi.json` reports as `info.version` — one number for one artifact, not an
independent HTTP-contract version. If the contract ever needs to move on its
own that is a second named constant with its own argument.

**A FOURTH VERSION SITE WAS FOUND AND IS DELIBERATELY LEFT**: `Dockerfile`
STAGE 2's `LABEL version="1.0.0"`. A LABEL cannot read a Python attribute, so
closing it means an `ARG` here plus a `build.args` entry in `docker-compose.yml`
plus something to keep that in step — build plumbing, not the release decision.
Recorded in the Dockerfile header so the next reader sees the number is known to
be stale. **A FIFTH was a stale build artefact**: `oncotriage.egg-info/PKG-INFO`
still said `0.1.0`, and because the code directory is on `sys.path`,
`importlib.metadata.version("oncotriage")` run from there answered `0.1.0` while
the installed dist-info said `2.0.0`. It and `build/` (which held a whole stale
copy of the package) are gitignored leftovers and were deleted.

**4. NEITHER FIXTURE FIELD IS COMPARED, and that was measured before capture.py
was touched.** `oncotriage/fixtures/replay.py` reaches `fixture["environment"]`
on five lines and reads three keys out of it: `tunables` (`diff_tunables`),
`qdrant_collection` (the pinned-name refusal) and `collection_digest` (the
contents refusal). `cross_encoder_model` and `sparse_model` are recorded and
never compared — a repo-wide grep for both names returns only the writer.
So items 1 to 3 cannot move a fixture byte, and the twelve replay clean without
recapture. Recording a field nothing compares is still worth doing: it is the
provenance a human reads.

**5. TWO DEAD TUNABLES, DELETED.** `BATCH_SIZE` claimed to be "patients per
progress-reporting batch" and `oncotriage/batch/runner.py` has no batch — one
`ThreadPoolExecutor` over every pending patient and a tqdm bar that advances
once per patient, in `run_batch` and again in `run_resample`. Wiring it in would
have meant INVENTING a chunking layer whose only effect is a coarser progress
report. `EXPANSION_TEMPERATURE`'s own comment said why it was dead — "Stage 1
uses no LLM". Both were **documentation defects**, because CLAUDE.md tells an
operator every tunable lives in `config.py`. The runner's module docstring made
the same promise ("Process patients in configurable batch sizes") and was
corrected in the same commit. **The exemption entry in
`tests/test_package_invariants.py` had to go with them**, and the file's two
staleness guards force that in both directions: leaving the entry with the
constants deleted fails "every exempted constant still exists", and deleting the
entry with the constants present fails "every module-level constant is read".
Compare `MATCHING_TEMPERATURE`, also not sent to the API and deliberately kept:
it is recorded into every fixture's environment block, so its `None` is the
honest record of a parameter a recorded run did not set. That is a reader.

**6. THE DEPENDENCY LIST IS `pyproject.toml`'s, AND IT IS THE ONLY ONE.** Three
declarations disagreed: `requirements/requirements.txt` (30 pins measured from
the development interpreter), `pyproject.toml` (none, with a written argument),
and the stale sibling `07- Requirements/requirements.txt` outside the
repository. The in-repo file's own header called the duplication the top-ranked
follow-up and named the blocker — "needs the versions pinned from a working
environment, not guessed" — which item 21 had already supplied.

**`requirements/requirements.txt` IS DELETED**, not kept as a lock and not
generated: a second list is a second list however it is produced. Every argument
its header made for its own existence is served by `pyproject.toml`, which sits
in the same directory, in the same commit, inside the same Docker build context.
`requirements/README.md` replaced it and held no versions, and **pass 20f-3
deleted that directory too, with `requirements_path` from both path tables**.
Pass 20f-2 left it standing only because the variable named it, and recorded the
follow-up with the whole edit; that edit is done. The container's bring-up report
is **thirteen** paths, and no path variable in this project is unread by all
code. Everything the README said now lives in `pyproject.toml`'s header.

**THE DOCKERFILE READS `pyproject.toml` WITH `tomllib`** (standard library on
the image's Python 3.11 — nothing is installed in order to read the file that
says what to install), writes the list to `/tmp` and `pip install -r`s it.
**Copying only that one file is the point**: the expensive layer is torch, and
`pip install .` needs the source tree, so a one-character code edit would
invalidate it. This keeps the layer caching the separate `requirements.txt` copy
existed for, without a second list. The runtime stage's
`pip install --no-deps --editable /app` is unchanged, but its `--no-deps`
argument INVERTED — it used to mean "pyproject declares no dependencies", and it
now means "STAGE 1 already installed exactly this list; do not re-resolve it
over the network in the runtime stage".

**Consequence, stated:** `pip install .` into an empty environment now gives a
working package. The old note in `pyproject.toml` recorded the opposite — "an
importable package whose imports all fail" — as "the honest state of the split".

**7. THE QDRANT CLIENT PIN WAS BUMPED 1.16.2 → 1.18.0 AND THE WARNING IS GONE.**
Qdrant Cloud serves 1.18.3 and the client checks the pair on construction, so
every `get_qdrant_client()` call — indexer, validator, agent, backup, fixture
harness — emitted `UserWarning: Qdrant client version 1.16.2 is incompatible
with server version 1.18.3` and nothing acted on it. **1.18.0 rather than 1.19.0
(the latest)**: matching the server's minor exactly leaves a full version of
margin in BOTH directions, and 1.19.0 would not survive a rollback to 1.17.
`pip install --dry-run` showed the resolution touches qdrant-client alone.
Verified by running: the warning is absent from the replay log and the twelve
fixtures still replay clean, which is the test that matters because the
collection digest and every retrieval call go through that client.

**THE STREAMLIT MISMATCH IS REPORTED, NOT PAPERED OVER.** The pin is 1.46.0 and
1.45.1 is installed, and `pip check` still says
`streamlit 1.45.1 has requirement packaging<25,>=20, but you have packaging
26.0` — so the dashboard on this machine runs against a `packaging` its own
metadata declares incompatible. **What the machine needs to satisfy its own
pins is `pip install streamlit==1.46.0`**, bringing the install up to what the
declaration already says. The pin was not edited down to match a stale install:
1.45.1 is not installable alongside `apache-airflow-core` at all
(`packaging<25` versus `packaging>=25.0`, no version satisfies both — measured
against 3.1.7, which was the pin at the time; the pin is **3.3.0** now and lives
in the `orchestration` extra), so recording it would make the dependency list
unresolvable. **Still 1.45.1 on this machine at 2026-08-09**, re-measured.


### Dead code and small seams (pass 20f-3)

Eleven items, none of them large, all of them measured before they were changed.
**No money was spent**: the twelve fixtures replay 12/12 clean **without
recapture**, and — because this pass touches the fixture WRITER — a fixture
written by the changed writer was additionally shown to be **byte-identical** to
one written by the pre-change writer for the same input.

**A RULE THAT APPLIES TO EVERY DELETION BELOW, AND PASS 20f-2 PAID FOR IT.**
Check 2h counts a name inside **any string literal** as a read, so a constant
deleted from the code but still named in a docstring or a prose block is
invisible to the scan when somebody later reinstates it. Every deleted name here
was therefore purged from `.py` prose as well as from the code — every surviving
mention is a `#` comment, which no AST walk sees — and each deletion was
**reverted in place and shown to FIRE**, with the touched file hashed before and
after and the restore asserted byte-identical.

**1. `24- Airflow Manager.py` HAS AN ARGPARSE CLI**, which pass 20c-3c-2 recorded
as a follow-up and which two other items were waiting on. `start | stop | status
| trigger`, a global `--airflow-home`, and `--password-stdin` on the two
commands that authenticate. **There is deliberately no `--password VALUE`**: a
command line is in the process table for every user on the machine, so tier 1 is
reachable only through stdin, which is not cached and so does not become the
process-wide answer.

- **The commented menu is gone**, and with it the comment naming the retired
  `AIRFLOW_PASSWORD` route that pass 3c-2 kept byte-verbatim.
- **`_REEXPORT_EXEMPTIONS` IS DELETED, NOT EMPTIED.** It held one entry
  (`stop_airflow`, `trigger_dag`), because those two were named only in
  COMMENTED menu lines and no AST walk can see a comment. All four functions are
  called by `main()` now. The dict went with its own staleness check — "…and the
  one exemption is still needed" iterates nothing when the dict is empty and
  passes for free, which is a check that has stopped checking. **That is the −1
  in `tests/test_package_invariants.py`: 248 → 247.** The scan itself is
  unchanged and is now unconditional.
- **A BARE INVOCATION NO LONGER STARTS TWO SERVERS.** `python "24- Airflow
  Manager.py"` used to run `start_airflow()`. The subcommand is required; a bare
  invocation prints usage and exits 2. **A contract change, stated as one.**

**1b. THE GENERATED ADMIN PASSWORD IS NOT PRINTED.** `start_airflow()` echoed it
to the terminal — scrollback, `tee`, CI log, screen share, `script` recording.
Pass 3c-2 kept it on the argument that a local tool may print a locally-generated
credential; what closed it is that the four-tier route made the print **pointless
as well as leaky**, since tier 4 reads the same file and `status`/`trigger` never
needed a human to have seen it. The line now names the **path**, which is what
the one real consumer (a person logging into the web UI) needs.
**`DOCKER CLEAN BRING-UP.md` §2e says the same thing** for the container, where
`down -v` regenerates the password.

**MEASURED, AND THE BRIEF WAS WRONG ABOUT ONE HALF:** the password did **not**
also reach `api_server.log`. That file is the `airflow api-server` subprocess's
own stdout (`stdout=open(api_log_path, 'w')` on the Popen), while these prints go
to the manager's stdout. A 12 KB `api_server.log` from a real run on the
development machine contains **zero** occurrences of "password". The leak was the
terminal and only the terminal.

**2. `_PATIENT_STAGE_RE` IS DELETED** (`oncotriage/extraction/stage.py`), the one
of pass 20e's three findings it said "should simply go". It differed from
`_SNOMED_DISPLAY_STAGE_RE` — immediately above it, and the one
`extract_patient_stage()` uses at both match sites — only by an optional `tnm `
prefix that the survivor's `\b` already admits. Measured rather than argued:
`extract_patient_stage()` still resolves `"TNM stage 3 (disorder)"` to 3.

**3. `TRIAL_STATUS_FULL` IS DELETED, AND ITS STRING GOT A HOME.** It was dead
before the split (`git show ae3f6c6^`) and it was also **wrong**: the per-trial
classifiers return `'✅ Eligible'` for their top bucket, so it named a value the
per-trial vocabulary cannot produce — the `PASSWORD_SOURCE_ARGUMENT` shape.
`oncotriage/dashboard/tiers.py` now carries a **per-PATIENT** vocabulary
(`PATIENT_OUTCOME_FULL/PARTIAL/UNCONFIRMED/NO_MATCH` plus
`PATIENT_OUTCOME_LABELS`, a TUPLE in `MATCH_TIERS` order, with a `RuntimeError`
guard that the two correspond).

- **FIVE literals, not the three pass 20e's note recorded** — that note counted
  files. `match_quality`'s pie chart holds two of them, its `Outcome` list and
  its `color_discrete_map` key, previously kept in step by hand.
- **The pie chart was BORROWING from the per-trial vocabulary**
  (`TRIAL_STATUS_PARTIAL` sat in a per-patient list), so editing a per-trial
  label would silently have moved a per-patient chart's slice name and its
  colour key together. The labels and the colour map are now zipped from one
  source. **Values are character-identical**, so nothing renders differently.
- **DROPPING AN ENTRY FROM THE PINNED FILE 21 SURFACE IS A CHECK THAT STOPS
  RUNNING**, so it is argued in place (section 6f), on pass 20e's footing: that
  pin exists to catch a name LOST in the twelve-way split, the record shows this
  one was not lost but carried and then deliberately deleted, so keeping the
  entry would fail the probe with a message that is false. **22 → 21 names**, and
  the two counts beside it move with it. The check count does not.

**4. `TERMINAL_ERROR` IS LOAD-BEARING, WHICH ITS OWN EXEMPTION ASKED FOR** — the
one of the five follow-ups that wanted the opposite of a deletion.
`verify_recording_complete()` names the error-handler case explicitly.
**Pass 20e predicted "improve the diagnosis and change no outcome"; the first
half held and the second did not.** The old arm refused an error run only when
NO Stage 5 exchange had been recorded, so an exception thrown AFTER Stage 5
answered left the fixture WRITTEN — with a prefix stamped by the error handler's
placeholders. That fixture is refused now. Measured on synthetic sinks against
the pre-change writer: `n_chat=0` refused before and after (diagnosis moved),
`n_chat=1` **accepted before, refused now**, both other terminals untouched.
None of the twelve shipped fixtures ends at the error handler.

**THE FORMAT IS FROZEN, SO BYTE-IDENTITY WAS PROVED RATHER THAN INFERRED.**
`SCHEMA_VERSION` is 3 and `load_fixture()` refuses a mismatch; a replay compares
the deterministic prefix, not the bytes, so "12/12 replayed clean" would survive
a writer that had started emitting a different gzip stream. HEAD's `capture.py`
was taken out of git and exec'd into a throwaway namespace — never retyped — and
handed the same twelve fixtures: **12/12 byte-identical sha256**, with a
perturbed-field negative control that diverges.

**5. FOUR DEAD PARAMETERS DELETED FROM THREE PUBLIC SIGNATURES**
(`get_model_cost`'s `pricing_config`, `resolve_qdrant_collection`'s `client` and
`collection_name`, `get_age_reference_date`'s `snapshot_date`), plus the private
`_SNAPSHOT_NOT_SUPPLIED` sentinel that existed only to serve the last one. They
were the seam that let an exec-chain caller redirect a value, and there is no
exec chain. **Re-measured by AST: 29 call sites across the package, the entry
points and the tests, not one passing any of the four.** It IS a behaviour
change — an outside caller passing one now gets a `TypeError` — and the thing
pass 20e said had to be settled first, `get_age_reference_date`'s docstring
calling its argument "the supported patch point", **was wrong and had been since
pass 20d-1**: `tests/test_fhir_birth_date_and_demographics.py` section 3 sets
`config.DATA_SNAPSHOT_DATE`, which the function reads at CALL time. The
docstrings say that now.

**6. `tests/run_serial_tests.py` REFUSES A CONCURRENT RUN.** It had 239 lines, no
lock and no pid file, while its entire reason for existing is that two members
rewrite source in place and restore it from a backup taken at their own start.
Interleave two runs and the later restore writes back the earlier one's
**planted** tree, with both runs reporting 16/16 and exit 0 — the silent revert
that cost pass 20d-1 an edit to `config.py`, with no operator to have ignored a
warning. `flock(LOCK_EX | LOCK_NB)` on a file outside the repository, keyed on a
hash of the code directory, held for the whole run, **released by the kernel**
when the process exits however it exits — which is why it is not a pid file, a
shape that leaves a stale lock on every bad exit and whose "is that pid alive"
repair is a check-then-act race of its own. **Exit code 3**, naming the holder's
pid, host, user and start time. `--list` takes no lock. Demonstrated: a real
runner invocation against a held lock exits 3 in under a second, runs none of the
five, and the lock is free again once the holder dies.

**7. `_REPO_PY` COVERS `docker/`** — the same blind directory pass 20d-2 closed
for `tests/`, one level out. `docker/prepare_paths.py` and
`docker/generate_dag.py` both import from the package, and `prepare_paths.py` is
the **only reader of `oncotriage/paths.py:_DOCKER_PATHS` outside `paths.py`
itself**. Latent rather than live, because `paths.py` reads its own constant —
and the hole is what happens next: a constant added tomorrow whose only reader is
in `docker/` is reported as dead and the operator's fix is to delete a name the
container needs. Demonstrated out of band, both files restored byte-identically:
a plant read only from `docker/` is **not** reported with the corpus as shipped,
**is** reported against a copy with the `+ _DOCKER_PY` term stripped, and a plant
with no reader anywhere is reported either way.

**8. THE GENERATED DAG REFERENCES NO DELETED FILE, AND THE CRITERION WAS A NEW
sha256.** Two prose lines inside the generated string said `03- Config.py` — a
file pass 20e **deleted** — and a third, in the tail, described it as a shim that
no longer exists. Pass 3c-2 left them wrong on purpose (correcting a character
would have broken its byte-identity proof) and recorded the follow-up "whose
acceptance criterion is a NEW sha256". Delivered: **20,542 bytes /
`68963b0c…` → 20,689 bytes / `949283c4…`**, regenerated by deleting the deployed
file and re-running `23- Airflow DAG.py`, and parsed by **Airflow's own
`DagBag`** rather than assumed — `import_errors == {}`, `trial_refresh_weekly`
registered, all three tasks, tags `['production','trialmatch']`, timetable
summary `None`.

**9. `requirements_path` AND THE `requirements/` DIRECTORY ARE DELETED.** Pass
20f-2 wrote the whole edit down as a follow-up and named the blocker: the
variable named the directory, so the directory could not go first. Both go
together. **No path variable in this project is now unread by all code.** The
container's bring-up report is **thirteen** paths, and
`tests/test_paths_glob_determinism.py`'s resolver-count non-degeneracy check
moves 14 → 13 with the table. Everything the deleted `README.md` said already
lived in `pyproject.toml`'s header, which now records the follow-up as closed.
The stale sibling outside the repository is untouched and nothing resolves to it
any more.

**10. `--db` WITH AN ABSENT PARENT IS REFUSED BY NAME.** It used to reach
`sqlite3.connect` and come back as `unable to open database file`, which names
neither the path nor the flag, after the argument parsing and the banner.
`_require_writable_parent()` is the guard, and it is
`settings.resolve_inferences_db()`'s argument applied to the second redirectable
database: a database FILE that does not exist is normal — sqlite creates it — but
a missing PARENT is a configuration defect. **Not applied to the default**, which
resolves a directory `_glob_one` has already proved exists.

**11. `--db` REACHES THE CHECKPOINT, AND "RESUME" IS PER DATABASE.** Pass 20f-1
recorded this and named the decision it needed. The checkpoint is a set of
`(config, patient)` pairs whose only possible meaning is "already written", which
is a statement about a database and about nothing else — so an explicit `--db`
gets a checkpoint **beside that database, named after it**, on the same footing
as `ablation_summary_json()`. Before: an isolated run read the PRODUCTION resume
file, skipped every pair a production run had done, wrote nothing for them into
the scratch database, printed `Status: COMPLETE`, **and deleted the production
checkpoint on its way out**. `tests/test_ablation_db_isolation.py` section 5b
drives that defect through the real caller with a stand-in of the pre-20f-3 shape
and shows it inheriting the wrong resume state.

**PASS COUNTS.** Nineteen of the twenty-one test files report **exactly** what
they reported before. Two moved, each argued in place:
`tests/test_package_invariants.py` **248 → 247** (item 1a, the retired exemption
table taking its own staleness check with it) and
`tests/test_ablation_db_isolation.py` **43 → 72** (section 5b, the assertions
items 10 and 11 made possible — a behaviour change with nothing asserting it is
what this project calls the defect).


### The two function splits (pass 20f-4) — the last of item 20

Two files were one function too many. Neither split may change what is rendered
or computed, and neither does: **no money was spent**, the twelve fixtures
replay 12/12 clean **without recapture**, and both splits were proven by
BEHAVIOUR rather than by `ast.unparse`, because the point of both is that code
moves BETWEEN functions and a definition-level diff cannot see that.

**1. `oncotriage/dashboard/tabs/reproducibility.py` — 1,478 lines, ONE
definition.** symtable, not reading, is what established the shape: one
top-level function, five nested ones, and of those five only
`status_display_map` closed over anything (`_STATUS_DISPLAY_BASE_FLIP`).

**MECHANICAL, AND DONE:** four LITERAL tables hoisted to module scope
(`_STATUS_DISPLAY_BASE_FLIP`, `_FLIP_TYPE_SEVERITY`, `_FLIP_TYPE_COLORS`,
`_FAILURE_CATEGORIES`) and nineteen pure helpers extracted — fourteen computations
and five figure builders, the two already-pure nested defs among them. Every
one of them takes its inputs as arguments, calls no `st.*`, and closes over
nothing.

**`mode_colors` and `mode_fixes` STAYED**, and that is the same measurement
reaching the opposite answer. They are DERIVED (a comprehension over
`_FAILURE_CATEGORIES`, then one key assigned) and they are MUTATED after
construction. Hoisting a derived table is a behaviour change wearing the costume
of a move, and a module-level mutable rebuilt by every rerun is exactly the
hazard section 6a of `tests/test_package_invariants.py` exists to catch for
`MATCH_TIERS` / `MATCH_TIER_COLORS`.

**JUDGEMENT, AND LEFT:** every `st.*` call, every early return, both
`st.expander` blocks and the whole control flow. They share `grouped`,
`relevant_matches`, `patient_groups` and `flipped_comps_enriched` with what
follows, so cutting there means threading four to six arguments through a
wrapper that renders and returns nothing. A smaller honest split beats a
complete one that guesses.

**EXTRACT, DO NOT DECORATE.** Nothing extracted carries a decorator.
`render_reproducibility_tab` keeps its name, its module and its ONE
`@st.fragment`: a helper called from INSIDE the fragment changes nothing about
what re-runs, while a helper carrying its own `@st.fragment` would create a
NESTED fragment and change it. The decorator inventory (section 2i) is
**unchanged**, and so is `_F21_HOMES` — no pinned tab name or home moved.

**THE THREE WIDGET KEYS ARE UNMOVED AND CHECKED**: `repro_collection_filter`,
`flip_deep_dive_selector`, `drift_deep_dive_selector`. A key is session state,
so renaming one silently resets a widget for every user whose session carried
it. The AppTest capture records each key, its label, its option list and its
value.

**TWO PAIRS THAT LOOK LIKE DUPLICATES, MEASURED RATHER THAN ASSUMED.** The flip
deep dive and the score-drift deep dive carried CHARACTER-IDENTICAL copies of
the `criterion_details` parse and of the criterion-alignment loop, and two
`normalize_criterion` / `normalize_criterion_text` closures with identical
bodies; those are shared now. Their **diff-row builders are NOT the same and are
NOT shared** — the flip one has a `_rejected_` branch (GPT-4o stops evaluating
after the first disqualifier, so a rejected run renders "🚫 Not Evaluated
(Rejected)" rather than "—") and tracks patient values; the drift one has
neither, because every run that reaches the drift table was eligible. Merging
them would have put a rejected branch into a table that cannot contain one.

**HOW IT WAS PROVED: streamlit's `AppTest`, element for element**, the same
comparison pass 20c-3c-1 used. The tab is rendered from the real
`inferences.db` (1,106 inferences, 12,862 trial matches) and everything it
produces is captured in full — 32 metrics with labels/values/deltas, 52
markdown, 15 captions, 5 subheaders, 8 dataframes as complete CSV, 3 selectboxes
with their KEYS, 6 plotly figures as their complete JSON spec, and the type of
all 183 elements in document order. **Before and after are identical in every
one of those, with zero exceptions.**

**AND ON FIVE MORE SCENARIOS, because the production database cannot reach the
empty branches** — there are always flips and always score drift. Synthetic
frames drive `no_collection_column`, `no_repeats` (the two-metric branch),
`no_overlap`, `perfectly_stable` (both `st.success` else-branches) and
`empty_hash_column` (which forces `_build_patient_groups` onto its
(patient, collection) fallback key and renders the whole deep dive). All five
compare the pre-split module out of `git show HEAD:` against the shipped one and
all five are identical. **The first attempt at `no_repeats` returned at an
EARLIER guard than the one it was written for** — an empty `trial_matches`
frame — so it tested a branch it never reached; it is recorded because that is
the shape a scenario harness fails in silently.

**SEVEN PLANTED DEFECTS, SIX CAUGHT, AND THE SEVENTH IS A FINDING.** Plants ran
against a COPY of the package, never the shipped file, and the copy was restored
byte-identically. A used flip-type colour, a `fix` string, a metric percentage,
a helper's sort direction, a figure height and a WIDGET KEY were each caught, in
`plotly_specs` / `dataframes` / `metrics` / `selectboxes` respectively. Dropping
`.lower()` from `_normalize_criterion` was not caught, because the criteria in
the selected flip already agree in case.

**THREE EARLIER PLANTS WERE NO-OPS ON THIS DATA, AND MEASURING THAT IS THE
POINT** — a plant that is not a behaviour change is not a test of the harness
(pass 20c-3d's rule, restated as an event). Changing
`_FLIP_TYPE_COLORS['Full Match ↔ Partial Match']` changes nothing because only
two flip types occur in 1,106 inferences (`Rejection ↔ Partial Match` 63,
`Rejection ↔ Zero Score` 3). Dropping `'metastatic'` from a keyword list changes
nothing because four other keywords in the same category still match (9 → 9).
`n > 1` → `n > 2` in `_group_metrics` changes nothing because no group has n = 2
(they are 1,136 / 527 / 66 / 543).

**THAT THIRD FACT CAUGHT A REAL BUG IN THIS PASS, AND THE APPTEST PROOF COULD
NOT HAVE.** The first draft of `_FLIP_TYPE_COLORS` was hand-transcribed and had
`#2ecc71` where the original has `#2ca02c` — and since that entry is never
rendered on this corpus, the element-for-element comparison passed. What caught
it is a second check that does not depend on the data at all: **every hoisted
literal is lifted out of `git show HEAD:` by AST, evaluated with
`ast.literal_eval`, and compared to the module constant, VALUE and KEY ORDER**
(the failure-mode chart iterates `_FAILURE_CATEGORIES` keys, so order is
load-bearing), and the three moved function bodies are compared with
`ast.unparse`, docstring aside. 21 assertions, all passing after the fix. **Do
not hand-transcribe a literal in a move; lift it and compare it.**

**2. `oncotriage/ablation/analysis.py` — 1,976 lines, 24 top-level
definitions.** An AST walk over every `Name` load in each definition says NINE
of them touch `plt`, and `matplotlib.pyplot` was the only plotting import, at
module scope. Those nine are now `oncotriage/ablation/figures.py`, extracted
**by AST span and never retyped**, with exactly two mechanical edits per body,
each asserted rather than assumed: `output_dir() / "x.png"` → `out_dir / "x.png"`,
and `out_dir` added as a required second parameter. Required, not defaulting —
a default means a caller who forgets it during a `--db` run writes nine PNGs
into the PRODUCTION results directory describing a scratch database, which is
the `empty_database(db_path, flag)` argument again. `main()` is the only caller
in the repository, checked before the signature moved.

**`oncotriage/ablation/common.py` EXISTS TO BREAK A CYCLE, and the cycle is
real.** `analysis.main()` calls the nine, so `analysis` must import `figures` at
MODULE scope — check 1b forbids a package import in a function body — and the
figures need `CONFIG_ORDER`, `CONFIG_LABELS`, `BASELINE` and `output_dir()`,
which used to live in `analysis`. Importing them back would be the cycle. They
live in `common`, and both modules import DOWN into it.

**WHAT REMAINS LARGEST, REPORTED AND NOT SPLIT IN THIS PASS**, as instructed:
`generate_report` is **416 lines**, a third of what is left, and it is one
function that appends to one `lines` list. It is a genuine candidate — the
report has eight clearly-titled blocks — but splitting it is a second pass with
its own proof, and this pass promised the two it named.

**ITEM 6, AND IT WAS TWO DEFECTS RATHER THAN ONE.** `analysis.ablation_db()`
took no argument and HARDCODED `"ablation_results.db"` while
`study.ablation_db(db_path)` took a path and read `ABLATION_DB_FILENAME`. So a
study written with File 26's `--db` — the isolation pass 20f-1 added — **could
not be analysed at all**, and the filename existed as a constant AND as a
literal that can drift, the shape pass 20f-2 removed for the MedCPT checkpoint
and pass 20c-3a for the BM25 sparse model. Both constants and
`_require_writable_parent` moved to `common`; `study` imports them by NAME so
`study.ABLATION_DB_FILENAME` still resolves, which
`tests/test_ablation_db_isolation.py` reads.

**THE EXISTING PARENT-DIRECTORY GUARD IS REUSED, NOT REIMPLEMENTED.** Pass
20f-3 built it and gave it a message that names the directory; a second copy in
the reader is a second copy to drift. The ONE thing added is an
`example_command` argument, so File 26's message is byte-identical to what pass
20f-3 shipped (that is its default) and File 27's names File 27 — **verified by
running both**. `study.ablation_db()` and `study.ablation_summary_json()`
deliberately keep their own bodies and their own `_RESOLVED` cache, because
`tests/test_ablation_db_isolation.py` installs a decoy into `study._RESOLVED` by
name and a delegating function would leave that decoy unread and the test
passing for the wrong reason.

**THE OUTPUTS FOLLOW THE DATABASE, and that is a behaviour change beyond the
letter of the item.** `output_dir(db_path)` returns the database's directory,
on exactly the argument `study.ablation_summary_json()` already made: without
it, `--db` would read a scratch database and OVERWRITE the production tables and
figures with numbers computed from it. The default path is untouched, so no
documented command moves.

**THE TEST COUNTS DID NOT MOVE.** All twenty-one files report exactly what
they reported before — including `tests/test_package_invariants.py` at **247**,
whose decorator inventory (2i), `_F21_HOMES` pin (6f), fifteen-dashboard-module
count (6a) and never-read-name scan (2h) are the four this pass could most
easily have disturbed. `tests/run_serial_tests.py` passes 5/5, and
`oncotriage/config.py` and `oncotriage/registries/cancer_code_registry.py` were
confirmed restored afterwards. The twelve fixtures replay 12/12 clean.

**HOW IT WAS PROVED: every artifact, as bytes.** The analysis was run against
the real 525-row `ablation_results.db` before and after, into scratch
directories, and all **17 artifacts are byte-identical — 7 CSVs, 2 reports and
all 9 PNGs**. Byte comparison is valid because two BEFORE runs were first shown
to produce identical bytes for all 17, PNGs included. **Six planted defects were
each caught**: a `CONFIG_ORDER` reordering (16 of 17 artifacts move), a
`CONFIG_LABELS` edit (15), a figure colour (1), a figure size (1), the bootstrap
seed (3) and a changed output filename (1). The default path was then run for
real through `python "27- Ablation Analysis.py"` with no flag, and the
production results directory is **byte-unchanged**, all 19 files.

### The reproducibility tab has a standing guard (pass 20f-5)

Pass 20f-4's AppTest comparison **was never committed**, so the largest render
function in the dashboard was proved correct once and then unguarded. That
harness was searched for first — repository, git history and stash, every
sibling directory under the project root, both backup trees and every scratch
directory: `AppTest`, `no_collection_column`, `empty_hash_column` and
`perfectly_stable` appear in exactly one file, **CLAUDE.md, as prose**. It was
rebuilt, as `tests/test_dashboard_reproducibility_tab.py`. **It shipped at 163
checks and is 200 after pass 20f-6; 1.7 s, no keys, no spend, and "no network"
is measured rather than claimed — see "The template pool, the offline guard and
the enrichment divergence (pass 20f-6)" below.**

**THREE THINGS HAD TO CHANGE FOR IT TO BE A TEST RATHER THAN A FILE MOVE.**

- **There is no "before" any more.** The old harness rendered the pre-split
  module out of `git show`, and a commit recedes — a shallow clone, a squash or
  an export drops `e7c9742^` and the test then fails for something that is not a
  defect. The reference is a **golden snapshot committed beside the file**,
  `tests/snapshots/dashboard_reproducibility_tab.json`, 4,624 lines of plain
  JSON, regenerated only on purpose with `--update-snapshot` and **byte-identical
  across regenerations** (verified: three runs, one sha256). It was established
  against the pre-split source **once**: the four hoisted literals lifted from
  `git show e7c9742^:...` by AST, `ast.literal_eval`'d and compared to the module
  constants by value and by key order — **all four identical on both**. Nothing
  in the shipped test reads git.
- **It does not touch the production database.** A scratch SQLite file in a
  temp directory carries **both** tables, built by the project's own
  `initialize_database()` so the schema is real by construction. `dashboard/data.py`
  reads `paths.inferences_path`, which does **not** honour
  `ONCOTRIAGE_INFERENCES_DB` (that reaches the two writers), so the scratch path
  goes into `paths._RESOLVED` — the seam
  `tests/test_ablation_db_isolation.py` already uses — and is restored.
  Isolation is asserted **behaviourally**: `sqlite3.connect` is recorded for
  every render and no render may open anything else.
- **It drives the tab, not the app.** `AppTest.from_string` runs a four-line
  driver over one module and one function with a frame the test supplies.

**THE LITERAL CHECK IS SEPARATE FROM THE RENDER COMPARISON, AND THIS PASS
MEASURED WHY.** Pass 20f-4's hand-transcribed `#2ecc71` survived an
element-for-element comparison because that flip type never occurs — and that is
not luck. `_with_flip_types` runs only over `flipped_comps`, which by
construction holds two or more distinct classifications, so `'Rejected'` is
always in `tiers_seen` and `_classify_flip_type` **can never return** `'Full
Match ↔ Partial Match'` or `'Other'`. **Two of the five entries in
`_FLIP_TYPE_COLORS` are unreachable by any data**, as is
`_STATUS_DISPLAY_BASE_FLIP['violated']` (a run that violates an exclusion
criterion is rejected, and a rejected run stores no `criterion_details`).
Section 4 compares every literal by value **and key order** — the failure-mode
chart and the recommended-fix table both iterate `_FAILURE_CATEGORIES.keys()` —
and **section 5b plants pass 20f-4's actual shipped defect and requires the
render comparison to see NOTHING while the literal check fires.**

**SIX SCENARIOS FOR FIVE NAMED BRANCHES, and the sixth is forced.**
`perfectly_stable` (no flips) reaches the first `st.success`; the second one is
nested **inside** `if flip_count > 0`, so one render cannot satisfy both.
`flips_no_drift` is that second render. `no_repeats` is seeded as one patient
with two inferences on **different** collections, so `patients_with_multi` is 1
rather than 0 and the branch is exercised with a non-degenerate number.

**TWELVE PLANTED DEFECTS, TWELVE CAUGHT, EACH MEASURED — and one plant had to be
replaced because it was a no-op.** `max(200, len(sorted_types) * 60)` → `* 61`
moves nothing: three flip types render, and both arms evaluate to the floor. The
control moves the **floor**, which is what that figure's height actually is on
this data. `_normalize_criterion` losing `.lower()` was pass 20f-4's other
measured no-op; the seeded corpus spells one criterion in a different case
across runs, so here it bites. Plants are applied to a **copy** written into a
temp directory, never the shipped file, and section 6 hashes the shipped file to
say so. **Section 2 was also shown to fire against a real in-place edit** to the
shipped module (four scenarios failed; restore byte-identical by sha256).

**It is NOT in the collision matrix**, derived: it writes only inside a temp
directory, patches no repository file, and the only repository file it reads is
`oncotriage/dashboard/tabs/reproducibility.py`, which neither writer writes.
**It does not inventory decorators** — `tests/test_package_invariants.py`
section 2i already compares them as an exact dict keyed by
`path::qualified_name`, and that file still reports **247**.

### The template pool, the offline guard and the enrichment divergence (pass 20f-6)

**163 → 200 checks.** Three gaps, all of them places where the file said
something that nothing in it measured.

**1. THE POOLED PLOTLY TEMPLATE HAD NO CONTROL, AND THE POOL COULD COLLIDE.**
The snapshot hoists each figure's `layout.template` — ~7 KB of boilerplate on
every one of the 20 figures — into a digest-keyed pool so the file diffs
readably, and the docstring said the bytes were still compared. Nothing proved
it: the two existing plotly plants move a marker colour (5a) and a figure height
(5h), and **both live outside `layout.template`**. So the one part of the
snapshot with custom machinery between capture and comparison was the one part
with no planted defect behind it.

**Stated from the code that builds the pool, not from intent.** The key was
`sha256(json.dumps(template, sort_keys=True))[:16]` and the value the template
dict, assigned with `_TEMPLATE_POOL[ref] = template`.

- **Does a figure whose template changes get a new entry?** Yes — the ref is
  derived from the template, so a changed template is a changed ref, a changed
  `__template_ref__` in the spec, and a section 2b failure. Nothing is silently
  reused.
- **Can two figures with different templates collide onto one entry?** With
  `sort_keys=True`, **yes, by construction rather than by luck**: two templates
  differing only in KEY ORDER hash to one ref, the second assignment overwrites
  the first, and neither the ref in the spec nor the pool digest moves — the
  digest check sorted keys too, so it was blind to exactly the same thing.

`_template_blob()` serializes with **`sort_keys=False`** now, the exact bytes the
snapshot stores, so a distinct JSON document can share a ref only through a real
64-bit sha256 collision — which `_pack_plotly_spec` **records** into
`_TEMPLATE_POOL_COLLISIONS` rather than absorbing, and section 2c reads. 2c also
gained: the pool is non-degenerate, every ref a live figure carries is IN the
pool, the same holds INSIDE the snapshot file, and every pool key IS the digest
of the bytes under it.

**5m is the plant: one integer ~7 KB deep inside ONE template**
(`fig_flip_types.layout.template.layout.font.size = 99`). Caught by `plotly`, on
the `full` scenario. Measured, not assumed: **exactly one of the six figures
changed its ref, the other five did not, the original pool entry survived beside
the new one, and no collision was recorded.** Plotly **copies** a named template
into the figure on assignment — checked before the plant was chosen, because a
plant that mutated `plotly.io.templates` would have poisoned every render after
it and looked like a much larger defect. **5n** is the realistic shape of the
same regression: one figure switched to `plotly_dark`, giving two genuinely
different templates in one pool at once, both preserved, three distinct refs.

**THE REGENERATION IS THE DANGEROUS PART AND IT WAS PROVED, NOT ASSERTED.**
Keying on exact bytes changed every ref (`027bcf62442dfca9` →
`27989c9f28fc96fd`), so the golden file had to be rewritten — and a golden file
regenerated to accommodate a fix makes whatever the code does correct by
definition. Both files were **decoded back to the thing they are a compressed
record OF**: every pooled template spliced back inline, then compared directly.
**20 figures inlined on each side, 973 items compared across all seven
scenarios, identical**, with a control (one integer changed inside an inlined
template) shown to make that comparison fail. The new snapshot is
**byte-identical across three regenerations** (`a432b500…`), same 4,624 lines.

**2. "NO NETWORK" WAS A DOCSTRING CLAIM.** Every render now runs with
`socket.socket.connect`, `socket.socket.connect_ex`, `socket.create_connection`
and `socket.getaddrinfo` replaced by a recorder that **raises** and names the
calling frame — armed and disarmed around each render, beside the existing
`sqlite3.connect` recorder. Section 5o reads all seven scenarios and carries the
control the readings would otherwise be vacuous without: the identical guard
armed, a real outbound call made, blocked and recorded.

**The imports were measured separately, out of band, with networking genuinely
unavailable** — `sandbox-exec` with `(deny network*)`, verified to deny a
connection before it was trusted. The whole file: **exit 0, 200/200, and no
resolver or connection error anywhere in the output.** Not committed as the way
to run it, because a test that needs a macOS sandbox profile is a test that
stops running everywhere else.

**3. THE FRAME THE TEST HANDS THE TAB IS NOT THE FRAME PRODUCTION HANDS IT.**
The tab takes one argument, the `inferences` frame, and reads `trial_matches`
**itself** (`load_trial_matches_data()`, its fourth statement) — which is the
read the decoy control fires on, and why `no_collection_column`, which returns
above that line, opens nothing at all. But `oncotriage/dashboard/app.py` calls
`enrich_match_tiers(filtered_df, trial_matches)` **before** passing the frame, so
production carries four columns this file's frame does not. Equivalent only for
as long as the tab reads none of them, so section 1c asserts that against the
shipped source in **both** forms a column can be read by — `df['x']` is a string
literal, `df.x` is an attribute — **each with its own non-degeneracy probe**.
The first version had three probes that were all string literals, so the
attribute half of the walk could be deleted outright and it still passed;
measured, and it is why `columns` / `empty` (attribute-only in that module) sit
beside `patient_id` / `nct_id` (literal-only).

**TEN PLANTS, TEN FIRED, AND THREE OF THEM FOUND DEFECTS IN THIS PASS'S OWN
WORK.** Every new assertion was planted against a COPY of the test file in a
temp directory, with the shipped file hashed before and after and byte-identical.
The three defects the plants found — none of them by reading:

- **A short figure list ABORTED THE RUN.** `len({_refs_5m[2], _refs_5n[0],
  _BASE_REFS[0]}), 3` raises `IndexError` when a defect makes a render produce
  no figures — so the offline plant, which does exactly that, took section 5o
  and the whole of section 6 down with it and reported one traceback where it
  owed ninety-four failures. **This is the defect
  `tests/test_storage_query_layer.py` already had to fix once**, reproduced
  inside the file written to prevent that class. `_at()` converts the
  `IndexError` into a value that makes `check()` FAIL and name what was missing.
  Re-measured after the fix: the plant reports 101 passed / 94 failed and runs
  to the summary.
- **The enrichment scan's non-degeneracy control did not discriminate**, as
  above.
- **THE OFFLINE GUARD NAMED ITS OWN LAMBDA.** The four stand-ins were lambdas
  and `_network_caller` walked back a FIXED two frames, so the frame it reported
  was the lambda — every message said "this file, the line the lambda is on"
  whatever had actually called out. **Its control passed anyway**, because it
  asserted only that the caller string started with this file's basename, and
  the lambda IS in this file: satisfied for the wrong reason, which is the shape
  the project's rules exist to catch. The stand-ins are named functions, the
  guard's own frames are skipped by name, and the control now asserts the
  reported frame is `_offline_control_call` — an assertion a guard that names
  itself cannot satisfy.


### The MCP server (the MCP pass)

A second protocol over the same pipeline, beside `oncotriage/api/server.py`.
`oncotriage/mcp/server.py` exposes three tools on **stdio** — `parse_fhir_bundle`
and `match_patient` take a **path** to a FHIR bundle, `lookup_trial` takes an NCT
ID — and every one is a wrapper. The entry point is **`mcp_server.py`** at the
code root, unnumbered, on the `fixture_capture.py` precedent.

**`python -m oncotriage.mcp` WAS BUILT FIRST AND WAS WITHDRAWN.** It needed
`oncotriage/mcp/__main__.py` to import `oncotriage.mcp.server` from inside a
function, because the stdout guard has to wrap that import — and that is exactly
what `tests/test_package_invariants.py` **check 1b forbids**. The check caught
it. A top-level script is not a package module, so the deferred import is the
ordinary entry-point shape and no invariant had to be weakened.

**STDOUT IS THE PROTOCOL CHANNEL, AND THERE ARE TWO WINDOWS.** The client parses
this process's stdout as JSON-RPC, one message per line; one stray byte ends the
session. The **serving window** is protected by the SDK itself — mcp 2.0.0's
`stdio_server()` calls `_claim_fd(1, ...)`, which dups the real stdout to a
private descriptor and points fd 1 at stderr, so a `print` inside a tool cannot
reach the wire. The **import window** is not, and this project genuinely writes
there: `oncotriage/paths.py` line 121 prints `[Paths] Settings module loaded
from …` at module scope, and the six-line bootstrap prints on the
not-installed branch. `mcp_server.py` closes it with an fd-level `dup2`,
released before the transport starts — because `stdio_server()` serves the
protocol on a duplicate of whatever fd 1 points at, so leaving it diverted would
write the protocol to **stderr**. `oncotriage/mcp/server.py` adds a per-call
Python-level redirect on top, which is load-bearing only on the SDK's own
documented fallback path (`_claim_fd` returns `stream.buffer` unchanged when the
descriptor cannot be diverted, and then the protocol shares `sys.stdout.buffer`
with every `print` in the pipeline).

**AN UNUSABLE INDEX RETURNS A MESSAGE WITH NO `matches` KEY, NOT AN EMPTY LIST.**
The gate reads `oncotriage/agent/readiness.py:probe_index` and is the **third**
caller to apply its own policy to that module's four-state vocabulary:
`populated` proceeds; `empty`/`absent` refuse; **`unverifiable` also refuses**,
which is the API-startup policy and not Stage 2's — an MCP caller sees one JSON
payload with no channel report, and `match_patient` would otherwise spend a live
billed Stage 5 call on retrieval nobody could vouch for. The refusal carries no
`matches`, `result` or `trial` key at all, because a model summarising a payload
turns an empty list beside a caveat into "no matching trials".

**THE NOT-FOR-CLINICAL-USE FRAMING IS NEW, AND THAT IS A FINDING.** The pass was
told to reuse the project's existing wording. **There is none** — the whole tree
was searched and the only hits are Synthea's `-cs clinician seed` flag and a
comment about what a clinician has to read. `NOT_FOR_CLINICAL_USE` and
`NOT_FOR_CLINICAL_USE_SHORT` are in `oncotriage/constants.py`, the leaf of the
import graph, so the API and the dashboard can adopt them; **only the MCP server
reads them today**, and widening an HTTP response shape is a contract change for
a pass that measures it.

**`oncotriage/retrieval/trial_lookup.py` IS THE ONE NEW NON-WRAPPER**, and it is
new because no public "give me the trial with this NCT ID" existed anywhere —
only an inline `scroll` inside `node_hybrid_retrieval`'s payload backfill. It
takes the **agent's** client seam on `index_validator`'s precedent (the question
is about the collection the agent retrieves from), and it **raises** rather than
reporting `found: False` when the index could not be asked, because "no such
trial" and "the server is unreachable" are the same empty list at the Qdrant API.

**`mcp==2.0.0` ONCE FORCED A SECOND PIN. THAT PIN IS GONE AND THE CONFLICT NO
LONGER EXISTS (commit `ec2033a`).** The history, because the shape recurs: mcp
requires `sse-starlette>=3.0.0`, that package's releases from 3.1.0 on require
`starlette>=0.49.1`, and `fastapi==0.117.1` required `starlette<0.49.0`.
Installing mcp plain dragged starlette forward and `import oncotriage.api.server`
died at `FastAPI(...)` with `TypeError: Router.__init__() got an unexpected
keyword argument 'on_startup'` — measured, not predicted. `sse-starlette==3.0.2`
was pinned as the newest release whose starlette requirement is an **extra**, so
it constrained nothing, and the follow-up recorded here was "move fastapi past
its cap".

**THAT FOLLOW-UP IS CLOSED, AND WHAT CLOSED IT WAS THE AIRFLOW UPGRADE RATHER
THAN THE SERVING LAYER.** `apache-airflow` moves 3.1.7 → **3.3.0** to fix two
CRITICALs (`CVE-2025-57735` JWT-valid-after-logout, `CVE-2026-42252`
template-engine injection) that were previously *accepted* through
`.trivyignore`. Airflow 3.1.7 declared `fastapi<0.118.0`; 3.2.2 and 3.3.0
declare `>=0.129.0` (3.3.0 also `<0.137.0`), and pip refuses every combination
in between — so the Airflow move and the fastapi move are **one move**.
`fastapi` is **0.136.3**, the highest release inside 3.3.0's window, and it
declares `starlette>=0.46.0` with **no upper bound**; starlette resolves to
**1.6.0**. The `sse-starlette` pin is **deleted** rather than moved forward:
keeping a pin whose reason has expired is how a dependency gets held at an old
release by an argument nobody re-reads. It is a transitive dependency of `mcp`
now, the way `httpx` is a transitive dependency of `openai`.

Releasing the `starlette<0.49.0` cap is what made **six starlette advisories**
fixable; all six are deleted from `.github/scripts/audit_gate.py` and three from
`.trivyignore`, and `audit_gate.py` **fails on a stale accepted id**, so leaving
them behind would have turned the gate red rather than passing quietly.

**Airflow lives in an optional extra now**, `[project.optional-dependencies]`
`orchestration = ["apache-airflow==3.3.0", "pendulum==3.2.0"]`. The extra is a
**packaging boundary and was never a fix** — it narrows what a default
`pip install` resolves and therefore what CI's pip-audit sees; the image still
installs it explicitly, so Trivy always saw those packages. Nothing in
`oncotriage/orchestration/` imports airflow (verified by AST — the
`import pendulum` and `from airflow.sdk import ...` a grep finds live inside the
generated DAG *string*), so the package imports and bucket A passes with Airflow
absent:

```bash
pip install -e .                    # pipeline only, no Airflow
pip install -e ".[orchestration]"   # adds Airflow, for Files 22/23/24
```

**`caffeine` IS macOS-ONLY BY ENVIRONMENT MARKER** — `caffeine==0.5;
sys_platform == 'darwin'`. The old note said it had to be installed on Linux too
because `oncotriage/utils.py` did an unguarded module-scope `import caffeine`;
that stopped being true at item 21 (the import is inside `try/except Exception`,
the reason is recorded in `CAFFEINE_IMPORT_ERROR`, and `CaffeinateSession`
degrades). Installing it on Linux buys nothing and **costs**: the package's last
two module-level statements are `on()` and `atexit.register(off)`, and `on()` is
`subprocess.Popen(['caffeinate', ...])` — a process spawn at import time, which
is exactly what `tests/test_package_invariants.py` section 2 exists to forbid.

**THE DEVELOPMENT MACHINE'S INSTALL IS BEHIND THESE PINS, and that is a fact
about the machine, not about the declaration.** Measured 2026-08-09 with
`importlib.metadata`: `fastapi 0.117.1`, `starlette 0.48.0`,
`apache-airflow 3.1.7`, `sse-starlette 3.0.2` still present, `streamlit 1.45.1`.
`pip install -e ".[orchestration]"` is what brings it up to what
`pyproject.toml` says. **Nothing in this repository is pinned to the installed
set** — `pyproject.toml` is the one dependency list.

**The client config block is in `oncotriage/mcp/server.py`'s docstring.** Both
paths in it are absolute on purpose — that rule is about SOURCE, and a client
launches the server from a working directory nobody here chooses. No `cwd` key
is needed: the bootstrap finds the package from the script's own `__file__`.

### Structured logging (the logging pass)

**1,273 `print` CALLS IN THE PACKAGE AND ZERO LOGGERS OUTSIDE THREE MODULES.**
Measured by AST before anything moved, because a `grep -c 'print('` counts
docstrings and `print_slowest_prompt` and was wrong about several files:
`oncotriage/` held **1,273** print calls, **29** `tqdm.write` calls and **26**
`logger.*` calls across three modules (`registries/cancer_code_registry.py` 20,
`registries/mesh.py` 4, `utils.py` 1, `retrieval/indexer.py` 1). Nothing had a
severity, nothing carried a correlation ID, and in a container it all arrived as
unstructured text on stdout.

**`oncotriage/observability.py` IS THE ONE MODULE, AND IT HOLDS BOTH CHANNELS.**
They are not the same thing and the file says so at the top: LOGGING is
machine-readable (one JSON object per line, a severity, a correlation ID, an
allowlisted field set); CONSOLE UI is the tqdm bar, the mid-run drift banner and
`agent/display.py`'s per-patient match report, which turned into JSON would make
a 22,000-patient run unwatchable. Both stay. Both go through **one** function,
`_emit_line`, which is what makes a bar-aware rule possible at all.

**THE STREAM COLLISION, AND THE CHOICE.** tqdm draws its bar on **stderr**; any
other writer on stderr while a bar is live interleaves with the redraw and the
two shred each other. **Stdout was not available** — `mcp_server.py` serves
JSON-RPC there and one stray byte ends a client session — so the obvious split
(bar on stderr, logs on stdout) is exactly the one that cannot be taken. **A
third stream was rejected on its own merits**: logs to a file or to fd 3 keep
the terminal clean and take the logs out of `docker logs`, which is where a
containerised deployment reads them and half the reason for the pass. So **both
channels write to stderr and the writer is bar-aware**: `console.attach_bar()`
registers `tqdm.write` as the active writer, `_emit_line` routes every console
line AND every log record through it until `detach_bar()`. That is the mechanism
the monkey-patch was borrowing; what changes is that the bar registers itself
rather than a builtin being hijacked, and that the routing now covers the
logging handler, which a patch on `builtins.print` could not see at all.

**THE `builtins.print` MONKEY-PATCH IS DELETED, AND ITS DEFECT IS WORTH
NAMING.** Three sites (`batch/runner.py` ×2, `ablation/study.py` ×1) rebound
`builtins.print` to `def _tqdm_print(*args, **kwargs)` that took `**kwargs` and
**threw them away**. For the whole of a batch run — in every module, every
library, every dependency, not just the runner — `print(end="")` grew a newline,
`print(sep="")` grew spaces, `print(file=handle)` was redirected to the terminal
and `flush=` did nothing. `console.out()` honours all four, which
`tests/test_observability_logging.py` section 5 asserts one keyword at a time.

**THE CORRELATION ID IS A `contextvars.ContextVar`, NOT A FIELD IN
`TrialMatchState`.** The state reaches the six graph nodes and nothing else,
while the lines that most need correlating are emitted *below* them — the MedCPT
load in `agent/deps.py`, the alias retry in `utils.py`, the write in
`storage/database_logger.py` — so threading it through would be a signature
change on each and would still carry nothing for a non-agent caller. A
ContextVar isolates **by construction**: a thread starts with an empty context,
so a value set on the main thread is invisible in a worker and a value set in a
worker is unreachable from its sibling. Measured before it was relied on.
`correlation_scope()` is a context manager because `ThreadPoolExecutor` REUSES
workers — an ID merely `set` would be inherited by the next patient on that
thread — and its `finally` resets the token whether the body returns or raises.

**FOUR SITES OPEN A SCOPE, and the fourth was found by running the replay.**
`agent/graph.py:match_patient_to_trials` (the API, the batch runner and the MCP
server all reach it), `ablation/study.py:_process_one`, and both fixture
harnesses. The three direct `graph.invoke` callers bypass
`match_patient_to_trials`, so their lines came out carrying the `-` sentinel;
the ablation study is the one that mattered, because it drives `MAX_WORKERS`
pairs at once and its whole log would have been one undifferentiated stream. The
ablation scope sits in `_process_one`, not in `match_patient_ablation`: the
config NAME is not in that function, and a first draft logged
`ablation_flags.get("_config_name")`, which is **not a key of that dict** and
would have written `null` on every line of every study.

**LINES THAT BELONG TO NO PATIENT CARRY `correlation_id: "-"`** (`NO_CORRELATION`
— startup, the BM25 index build, shutdown). A documented sentinel, never a
missing key, so no consumer has to test for presence before every read.

**THE ID IS DELIBERATELY NOT STAMPED ONTO `result`.** A first draft did, so a
stored row could be tied back to its lines — and that is a contract change
wearing the costume of an observability edit, since `result` is what
`POST /match` serialises and what `log_inference` writes. The join available
today is `patient_id`, which is on every agent line and is a column of
`inferences`. Persisting the ID properly means a new column; recorded as a
follow-up rather than half-done.

**THE FIELD ALLOWLIST IS ENFORCED IN THE FORMATTER, NOT AT THE CALL SITES.** A
call site can be added by anyone; the formatter is the one place every record
passes through — including a record from a caller that reached for
`logging.getLogger("oncotriage.x").info(..., extra=...)` and went around every
helper in the module. Section 4e of the test drives exactly that caller and
requires it to be filtered. Anything not on `LOGGABLE_FIELDS` is **dropped**,
its KEY NAME (never its value) is reported in `dropped_fields` on the same
record and counted in `FIELD_DROPS`; a silent drop would be indistinguishable
from a caller that forgot the field.

**WHAT THE ALLOWLIST ACTUALLY KEPT OUT, because on Synthea data it looks like
paranoia.** The node prints this pass converted carried: the patient's MeSH C04
tree numbers (`C04.588.180` *is* breast), the patient's cancer stage ordinal,
`expanded_query` and every `rerank_queries` entry (built from the primary
diagnosis display, the histology and gene symbols), `disease_query`, the
per-query rerank breakdown with its query text, and a 300-character preview of
the model's criterion-level response. Each is replaced by the operational fact
that diagnoses a problem: a COUNT of trees, `status="known"|"unknown"` for the
stage, `query_count` and `query_length`, an aggregate `score_min`/`score_max`,
`response_chars`. **The response preview goes to the CONSOLE**, which is
transient and unindexed, because it is the one thing that diagnoses a malformed
answer; `response_preview` is absent from the allowlist and would be dropped if
anyone passed it.

**THE LIMIT IS STATED RATHER THAN GLOSSED, AND IT IS CLOSED STATICALLY.** The
allowlist governs FIELDS. It cannot police the free-text `message`, because by
the time an f-string reaches the formatter it is a `str` and no longer
distinguishable from a constant. The convention is "the message is a template,
the data goes in fields", and section 6c of the test walks `oncotriage/agent/`
by AST and fails on any `log.*()` whose message argument is an `ast.JoinedStr` —
with its own non-degeneracy probe, because a walk that cannot see an f-string
would pass for free.

**QUERY 5's PROMPT DUMP STAYS ON THE CONSOLE, ARGUED.**
`storage/queries.py:print_slowest_prompt` renders a whole Stage 5 prompt —
patient summary, conditions, labs. Its `out=` default moved from `print` to
`console.out`. It is an operator running File 16 interactively at a terminal,
not a durable record, and `llm_classifier_prompt` is not on the allowlist, so it cannot
enter one.

**`ONCOTRIAGE_LOG_LEVEL`** is named in `oncotriage/settings.py` and resolved by
`resolve_log_level()`, which is **deliberately not `_from_env`** — that helper
appends a trailing separator and `"DEBUG/"` is not a level name. Fourth victim
of that helper after the airflow password, the inferences DB and the degraded
flag, and the one worth naming again because it fails in the useful direction:
an unrecognised level read as "the default" would leave an operator hunting for
DEBUG lines that were never emitted. It **raises**. Unset means INFO.

**THE MCP fd GUARD IS NOW WITHOUT A KNOWN SUBJECT, AND SAYS SO RATHER THAN
BEING QUIETLY KEPT.** It existed because `oncotriage/paths.py` printed
`[Paths] Settings module loaded from ...` to stdout at module scope and
`mcp_server.py`'s own bootstrap printed there too. Both are on stderr now
(measured: `python -c "import oncotriage.mcp.server" 1>/dev/null` prints the
banner; `2>/dev/null` prints nothing). It is KEPT — the import window pulls in
openai, qdrant-client, langgraph, fastembed and transformers, and a banner from
any of them on any future version is a dead client session with no diagnosis —
and the retention cost is paid: **`tests/test_mcp_server_stdio_contract.py`
section 8c no longer depends on a defect existing and PLANTS one instead.** It
copies the package and `mcp_server.py` into a temp directory, appends a stdout
write to the copy's `oncotriage/__init__.py`, and runs both arms against it:
bypassed → corrupted, guarded → clean, plus a non-degeneracy probe that the
plant reaches stdout at all. `cwd` is the load-bearing detail — `python -c` puts
the working directory at `sys.path[0]` AHEAD of `PYTHONPATH`, so the first
version imported the real package in both arms and reported "no corruption" as
though the guard had done it. **135 → 142 checks.**

**TWO DEFECTS IN THIS PASS'S OWN CODE WERE FOUND BY RUNNING, NOT BY READING, AND
BOTH ARE RECORDED BECAUSE THE SECOND IS THE INSTRUCTIVE ONE.**

- The formatter stamped `formatTime(...) + "Z"` while `logging.Formatter`
  defaults to `time.localtime`. A local time suffixed `Z` parses cleanly, sorts
  cleanly and is wrong by the machine's UTC offset. `converter = time.gmtime`,
  and check 1b compares the stamp against `datetime.now(timezone.utc)`.
- **`tqdm.write`'s signature is `write(s, file=None, end="\n")` and it resolves
  `file=None` to `sys.stdout`.** So the first version of `attach_bar` installed
  a bare `tqdm.write` and sent every console line and every JSON record to
  **stdout** for as long as a bar was live — the MCP protocol stream, and the
  stream this pass promises is empty. It is also wrong about the bar: `tqdm(...)`
  draws on stderr, so the clear-write-redraw dance would have cleared one stream
  and written to the other. The writer passes `file=_console_stream()` now, and
  `progress()` sets the same default on the bar so the two cannot drift.
  **Section 5e could never have caught it** — it installs a FAKE writer and
  tests the routing — so section **5i** drives the real one and is shown to fail
  against a reverted copy.

**THE THREE PLANTED DEFECTS, each measured to fire.** They go into an exec'd
COPY of the module, never the shipped file, which is why
`tests/test_observability_logging.py` is the second member of
`test_package_invariants.py`'s `_EXEC_ALLOWLIST` — argued there, and it is
CLAUDE.md's own instruction to prefer a mutated copy over an in-place edit.

| plant | caught by |
|---|---|
| the ContextVar replaced by a module-level global | section 3, and **which** assertion was measured rather than assumed: a shared global does not give one patient several IDs, it gives **twelve patients one ID** — 3c, "every correlation ID belongs to exactly one patient", which is also the literal property the brief asks for. The first version of the control asserted 3b and reported the plant as uncaught |
| `filter_fields` not consulting the allowlist | section 4c — the clinical values appear in the record |
| a `os.write(1, ...)` in the pipeline driver | section 8b — stdout is no longer empty |

**VERIFIED BY RUNNING.** All 21 existing test files at their documented counts,
`tests/test_package_invariants.py` unchanged at **247**, the serial runner 5/5,
`fixture_replay.py` **12/12 clean without recapture** (which is what says the
pipeline path did not move) — and afterwards `oncotriage/config.py` and
`oncotriage/registries/cancer_code_registry.py` confirmed restored. **No money
was spent.** The bar and the banner were demonstrated on a real **pty** with 12
threads emitting log lines through a live bar: stdout empty, 48/48 JSON records
whole and parseable on the same stream as the bar, 48/48 console lines intact,
all 18 banner lines intact, the bar owning the bottom of the screen.

**WHAT THE NEXT PASS OWES, BY MODULE.** This pass moved every remaining print to
the console channel MECHANICALLY — `print(` → `console.out(`, position-based so
no file was reformatted, proved exactly reversible (all 83 touched files
byte-identical after undoing the transformation). Giving those lines severity,
structure and correlation is the next pass. **1,199 `console.out`/`banner` calls
remain**, against **65** structured log calls already in place:

| console | module | | console | module |
|---:|---|---|---:|---|
| 156 | `fhir/generate.py` | | 30 | `retrieval/index_validator.py` |
| 119 | `fhir/explore.py` | | 28 | `orchestration/airflow_setup.py` |
| 115 | `fhir/clean.py` | | 24 | `evaluation/sampling.py` |
| 82 | `ablation/study.py` | | 23 | `retrieval/qdrant_backup.py` |
| 82 | `batch/runner.py` | | 23 | `evaluation/cohort_diff.py` |
| 75 | `orchestration/airflow_manager.py` | | 12 | `ablation/figures.py` |
| 65 | `monitoring/drift.py` | | 12 | `fhir/parser.py` |
| 54 | `fixtures/replay.py` | | 10 | `api/server.py` |
| 53 | `registries/mesh_crosswalk_build.py` | | 9 | `registries/mesh.py` |
| 53 | `fixtures/capture.py` | | 8 | `utils.py` |
| 51 | `retrieval/indexer.py` | | 7 | `orchestration/dag_generator.py` |
| 49 | `ablation/analysis.py` | | 6 | `storage/database_logger.py` |
| **41** | **`agent/display.py` — STAYS CONSOLE** | | 4 | `paths.py` |
| | | | 2 each | `embedding.py`, `agent/evaluation.py`, `storage/maintenance.py` |
| | | | 1 each | `config.py`, `mcp/server.py` |

**`agent/display.py` IS NOT ON THAT WORKLIST.** Its own module docstring reads
"Console output only", it renders the per-patient match report a human reads,
and nothing in the pipeline consumes it. It is console UI by definition and
converting it to JSON would delete the report. Its 41 lines are the one entry
above that is finished, not pending.

**THE ENTRY POINTS AND `tests/` ARE OUT OF SCOPE AND STILL CALL `print`** — 291
and 1,216 calls respectively. A test's output IS its report, and an entry point
is a `__main__` block. Neither is package code and neither is affected by the
deleted monkey-patch, which lived in the package.

### The scrape admission filters (the admission pass)

**THREE FILTERS IN THE SCRAPER DECIDED WHICH TRIALS EVER ENTERED THE CORPUS, AND
NOTHING DOWNSTREAM COULD SEE WHAT THEY DISCARDED.** Stage-wise recall records
what each gate drops; it cannot see a trial that was never indexed. Every number
this project reports was computed against a corpus quietly missing 2,219 trials.

**MEASURED BY RUNNING, ONE INSTRUMENTED SCRAPE, 188 HTTP REQUESTS, FREE.** A
single pass records, per study, what the OLD filters would have done and what
the NEW ones do, so each defect is attributed separately without scraping a
moving registry twice.

| | |
|---|---|
| raw studies seen | 18,773 (14,342 INTERVENTIONAL) |
| admitted, OLD filters | **12,092** |
| admitted, NEW filters | **14,311**  (+2,219) |
| recovered by defect 1 alone (age) | 1,051 |
| recovered by defect 2 alone (keywords) | 1,070 |
| recovered by both at once | 98 |
| dropped by the NEW screen that the old one kept | **0** |

**DEFECT 1 — `if min_age > 18: continue` IS AN EXACTLY-18 FILTER, AND IT IS
DELETED, NOT WIDENED.** A trial requiring 19, 20 or 21 was discarded, and so was
every trial requiring 40, 50 or 65 — the recovered population's `minimumAge`
distribution is 189×19, 178×20, 108×50, 107×21, 99×40, 74×65, … The decision was
deleted because `agent/filtering.py:node_rule_based_filter` already enforces
`min_age <= patient_age <= max_age` against the actual patient and counts it into
`age_dropped`. **Widening it to "the oldest patient we serve" was rejected**: that
writes today's cohort into the corpus, so changing the cohort makes the corpus
silently wrong again in the identical undetectable way. `INDEX_AGE_PARSE_FAILURES`
went with it — see the item 11a list above.

**DEFECT 2 — THE ONCOLOGY SCREEN ROUTES THROUGH MeSH, AND MAY ONLY DROP ONE
WAY.** The old screen was a sixteen-word frozenset holding "glioma" but neither
"blastoma" nor "thelioma". Measured against the shipped list, Glioblastoma,
Mesothelioma, Neuroblastoma, Retinoblastoma and Hepatoblastoma **all drop**;
measured against the C04 crosswalk, **all five resolve** to specific tree
numbers. Recovered: 117 glioblastoma, 36 neuroblastoma, 8 mesothelioma, 8
retinoblastoma, 2 hepatoblastoma trials.

`registries/mesh.py:classify_trial_oncology()` answers a **closed three-member
vocabulary** (`TRIAL_ONCOLOGY_VERDICTS`). Three tests can vote KEEP; exactly one
can vote DROP:

- C04 crosswalk hit → `TRIAL_ONCOLOGY` (9,773 trials)
- oncology vocabulary in conditions/keywords/title → `TRIAL_ONCOLOGY` (4,362).
  A **keep-signal only**, so its gaps cost nothing.
- every registered condition a known MeSH term positively outside C04 →
  `TRIAL_NON_ONCOLOGY` (**31**). The only drop.
- anything else → `TRIAL_UNRESOLVED` (**176**) — KEPT and counted. **That number
  is the size of the uncertainty the screen absorbs**, and it is reported at the
  end of every scrape.

**A POSITIVE NON-ONCOLOGY DETERMINATION NEEDED A LOOKUP THAT DID NOT EXIST.**
`mesh_c04_lookup.json` is C04-only, so the one thing it can report is "resolved
to cancer" or nothing — and nothing conflates "Diabetes Mellitus, definitively
not a neoplasm" with "resolved to nothing at all". A screen built on it would
drop every trial it merely failed to parse.
`registries/mesh_crosswalk_build.py:build_mesh_non_oncology_lookup()` reads the
whole of `desc2026.xml` (NLM, public domain, already on disk) and keeps the
complement: **258,369 terms**, descriptor headings AND entry terms, every one
with tree numbers none of which is under C04. A name shared with any C04
descriptor is excluded outright, because ambiguity must not licence a drop. The
layer is OPTIONAL and its absence can only stop a drop, never cause one, so it
degrades to "admit everything" with a counter rather than raising.

**C04 IS BROADER THAN "CANCER", AND THAT IS A MeSH FACT RATHER THAN A DEFECT.**
It contains `C04.182` (Cysts) and `C04.588.614` (Paraneoplastic Syndromes), so
Polycystic Ovary Syndrome (`C04.182.612.765`) and Myasthenia Gravis
(`C04.588.614.550.500`) resolve as oncology and are admitted. Some of the 1,168
trials recovered by defect 2 are therefore not cancer trials. This is the false
keep the governing principle explicitly buys — Stage 4 filters per patient by
tree ancestry, so a breast-cancer patient never matches `C04.182.612.765` — and
it is **pre-existing**, since `trial_mesh_trees()` has always resolved them.

**DEFECT 3 — MEASURED FIRST, AND THE BRIEF NAMED ONLY HALF THE POPULATION.**
On the stored 12,067-trial corpus, by branch: `both` 11,218 (92.96%),
`inclusion_only` 299 (2.48%), `exclusion_only` 103 (0.85%), `neither` 447
(3.70%). The harm — whole criteria block to inclusion, exclusion empty — comes
from **two** branches, because `elif inclusion_start != -1` also sets
`exclusion_text = ""`. The real rate is **746, 6.18%**, of which 681 contain
exclusion vocabulary.

The policy is **BOTH a richer marker list AND the flag**, not either: they answer
different questions, and the flag is separately required. Markers are
**line-anchored** (a heading is at a line start, optionally behind a bullet or a
number), longest-alternative-first so "Key Exclusion Criteria" wins over the
"Exclusion Criteria" nested in it. **The anchored search FALLS BACK to the
original substring markers**, because anchoring alone lost 116 splits that the
old code found — the new split is a strict superset by construction, and
`LOST == 0` is a design constraint rather than an observation.

    empty exclusion   746 (6.18%)  ->  213 (1.77%)   recovered 533, LOST 0

`split_inclusion_exclusion` returns a **3-tuple** now; the third member is one of
the `CRITERIA_SPLIT_*` constants and is stored as `trial["criteria_split"]`,
which rides into Qdrant inside `full_trial_json`. A downstream ingestion gate
reads it without re-implementing the splitter — which is exactly how the two
copies in this repository drifted apart. **Unsplit trials are KEPT**: excluding
them would delete trials to fix a labelling bug.

**DEFECT 4 — VERIFY BEFORE THE SWAP, KEEP A ROLLBACK.** `main()` ran create →
index → payload index → swap → `cleanup_old_collections(keep_recent=1)`. Nothing
was verified, and the cleanup destroyed the previous good collection seconds
after promoting its replacement. Now: create → index → payload index →
**`verify_collection()`** → swap → `cleanup_old_collections(keep_recent=2)`.
`verify_collection` RAISES `IndexVerificationError` (a `RuntimeError` subclass,
deliberately not a `ValueError`) so the alias cannot move by a caller forgetting
to test a boolean. `cleanup_old_collections` **never deletes the alias target**
whatever its name sorts like — the old code assumed the alias pointed at the
newest collection, which after a failed swap is exactly false.

**WHAT VERIFICATION DOES NOT CHECK, stated at the code.** Not retrieval quality;
not that each vector belongs to its trial (detecting a shuffle means re-embedding,
which is a paid call); not that the BM25 vocabulary matches the query side
(static, File 47 check 2f); not completeness against ClinicalTrials.gov; and not
any comparison with the collection it replaces — a corpus that collapsed to 300
well-formed trials passes. The live count is REPORTED beside it, and reporting is
not checking.

**THE GENERATED AIRFLOW DAG CARRIED A SECOND, ALREADY-DIVERGED SCRAPER, and this
pass deleted it.** ~370 lines in `orchestration/dag_generator.py` reimplemented
the indexer, and every difference made it build a worse index: its inclusion
marker `"patients must"` is a **prefix** of its own exclusion marker
`"patients must not"`, so an exclusion heading matched both and the split
collapsed to empty; its `_parse_trial_metadata` read **no conditionsModule at
all**; its collection was created with `vectors_config` only, so **none of the
three BM25 sparse vectors and no `nct_id` payload index existed**; it embedded
one trial per API call with `time.sleep(0.1)`; it carried the same exactly-18
filter; and it verified AFTER the swap. The tasks delegate to
`oncotriage.retrieval.indexer` now, **with the import deferred inside each task**
— a module-scope import that fails makes the DAG vanish from the scheduler with
an import error, while a deferred one fails a run loudly. Regenerated and parsed
with Airflow's own `DagBag`: `import_errors == {}`, all three tasks, 20,689 bytes
`949283c4…` → 13,071 bytes `7caece14…`.

**`tests/test_indexer_admission_filters.py` — 175 checks (this note said 131; the
file has never reported that), no network, no keys, no
spend, not in the collision matrix.** **IT NEEDS GIT HISTORY AND ABORTS WITHOUT
IT** — the controls are lifted out of `git show`, and in a tree with no `.git`
`_old_split` comes back `None` and the file dies at `TypeError: 'NoneType'
object is not callable` rather than recording failures. Measured, not predicted:
run from a `git archive` export it crashes; run from a `git worktree` it reports
175/0. Same shape as the aborts `tests/test_storage_query_layer.py` and
`tests/test_dashboard_reproducibility_tab.py` had to fix, and it is a recorded
follow-up. Every check is paired with a control that
FIRES against the old implementation, and the old implementations are lifted out
of `git show` rather than retyped. **The revision is DERIVED by AST**, not by
substring: the current file quotes `if min_age > 18` verbatim in the comment
explaining its deletion, so a substring search selects the commit that REMOVED
it and every control then tests the fix against itself — the lesson
`tests/test_storage_query_layer.py` had to learn. It is the third member of
`test_package_invariants.py`'s `_EXEC_ALLOWLIST`, argued there.

### Three Stage 4 fixes, and the pass that committed their proof

**THE FIXES (commit `1fcecbb`). Each changes which trials survive.**

- **`oncotriage/extraction/histology.py`: `_NSCLC_ABBREV_RE` and
  `_SCLC_ABBREV_RE` gained `re.IGNORECASE`**, which every other pattern in the
  module already had. Lower-case "nsclc" produced NO tag, and an untagged trial
  is filtered by nobody — `is_histology_mismatch()` returns False the moment
  either side's tag set is empty — so a small cell trial reached a non-small
  cell patient. Case-folding widens what `_SCLC_ABBREV_RE`'s negative lookbehind
  excludes, and that costs nothing because **the lookbehind is unreachable in
  both cases**: `\bSCLC\b` needs a word boundary before the S and the preceding
  N is a word character. Measured on the 14,324-trial corpus: **0 trials change
  tags**, because ClinicalTrials.gov writes these abbreviations upper-case. The
  effect is on the PATIENT side, and trial tags are written at INDEX time, so
  the trial side would need a re-index to move at all.
- **`agent/filtering.py:_parse_age_bound` converts the unit** to FRACTIONAL
  years. It took the digits and discarded the unit, so `"240 Months"` — twenty
  years — read as two hundred and forty and stopped excluding anybody, and a
  `min_age` of `"6 Months"` read as six years. Nothing was recorded, because
  digits WERE found. Six months is 0.5: rounding moves the boundary in the
  direction the fix exists to correct. The number and its unit come from ONE
  match, so a unit belonging to a later number is not pulled back onto the
  first. An unrecognised unit is recorded in `AGE_PARSE_FAILURES` under a key
  NAMING the unit and the bound is unusable — **recovery unchanged: the trial is
  kept and the age check is skipped**. Measured: 167 bound values across 158
  trials change; at age 5 the filter now drops 6 it kept and keeps 104 it
  dropped.
- **`agent/filtering.py`: an unusable patient sex stops excluding.** The old
  predicate kept a trial when its sex was ALL or equalled the patient's, so a
  patient whose sex did not parse failed it against all 2,485 sex-specific
  trials. The same line called `.upper()` on the patient's sex unguarded, and
  `oncotriage/fhir/parser.py` sets `sex = patient_resource.get('gender',
  'unknown')` — so a `gender` present and **JSON-null arrives as None** (a
  `.get` default does not apply to a key that exists) and RAISED rather than
  dropping. There is no sentinel, so the rule is not "equals some magic string"
  but "can the trial vocabulary (ALL / MALE / FEMALE) express it".
  `SEX_UNKNOWN_KEPT` records the survivals **apart from `sex_dropped`**, because
  a real mismatch and a missing field are different findings with different
  owners. Measured: zero effect on today's cohort — all 1,000 bundles carry
  female or male.

Both new records are **module-level counters, not keys in Stage 4's returned
dict**, on the `AGE_PARSE_FAILURES` precedent: the twelve characterization
fixtures diff that dict field by field.

**THE PROOF IS COMMITTED (the promotion pass), and it lives in two files.**
`tests/test_extraction_histology.py` **Test 9** (103 → **133**) and
`tests/test_agent_age_units_and_sex_filter.py` (**112**, new). The second is a
NEW file rather than an addition, and the reason is in its docstring: five files
touch `node_rule_based_filter` or `_parse_age_bound`, and the only one that
covers both — `tests/test_degraded_dependencies.py` — is a **collision-matrix
member**, so forty checks needing no serialization would sit behind a six-minute
serial run. Pass 20f-1's precedent is four new files for four fixes.

**THE MOST VALUABLE CONTROL IS THE ONE THAT CANNOT BE WRITTEN WITH `git show`,
and it is worth reading before writing the next one.** The scratch harness
compared the shipped Stage 4 node against the pre-fix node read out of
`git show HEAD:`. It reported NO DIFFERENCE — and not because there is none:
exec'ing a pre-fix `agent/filtering.py` runs its
`from oncotriage.extraction.histology import ...`, **which resolves to the LIVE,
already-fixed module**, so the "old" side ran the NEW extractor and agreed with
itself. Committed unchanged it would have been worse still, because HEAD now
CARRIES the fix. The committed version therefore: plants the defect into an
**in-memory copy**, **asserts the trap directly** (a freshly exec'd filtering
copy IS wired to the live histology module), asserts the rebinding took, and
requires the two extractors to **disagree by behaviour** before comparing
anything built on them. It reads no git, so it does not die in a tree without
`.git` the way three files in this suite still do.

**A CONTROL THAT ABORTS IS NOT A CONTROL, and this pass shipped that defect
twice before catching it.** `_plant()` raises `_PlantFailed` — never
SyntaxError — so a malformed plant is a RECORDED failure instead of a traceback
hiding every check below. And `run()` converts a node RAISE into a marker
string: reverting the sex normalisation makes the node raise `AttributeError`
on a null sex, which is exactly what Test 3 exists to catch, and with a bare
call that raise escaped `check()` while its argument was being evaluated — the
file died with no summary, reporting one traceback where it owed 112 results.

**FIVE REVERTS, FIVE CAUGHT, run rather than argued.** The package and `tests/`
are copied to a temp tree, one production fix is reverted THERE, and the
committed tests are run against the copy (with a preflight asserting the COPY is
what imports, on realpaths, because macOS `/var` is a symlink to `/private/var`):
IGNORECASE removed (133 → 114 pass / 19 fail), the conversion removed (23 fail),
the result rounded (16 fail), the old sex predicate (19 fail), the unguarded
`.upper()` (7 fail). Baseline on the unreverted copy is green both files.
Nothing in the repository is written.

### The stage extractor reads the AJCC M category (the M-category pass)

**`extract_patient_stage()` GAINED A TIER AND STAGE 4 GAINED AN ARGUMENT.**
`oncotriage/fhir/parser.py` had been routing LOINC **21907-1**, the AJCC
clinical M category, into `cancer_metastasis_observations` since the metastasis
item — where it reached `compute_patient_hash` and the Stage 5 prompt and
**nothing that decides a stage**. A comment beside the routing recorded the
deferral. The tier is in `oncotriage/extraction/stage.py`; the argument that
makes it reachable is in `oncotriage/agent/filtering.py`, and
`oncotriage/fixtures/capture.py:scan_cohort` got the same one because its
docstring promises it classifies with the helpers Stage 4 calls.

**THE RULE READS ONE DIRECTION ONLY.** cM1 → 4. cM0 → **nothing**: it is a
POSITIVE statement that there is no distant metastasis, and a patient can be
cM0 and stage IIIC. cMX ("cannot be assessed") → nothing. The comment in
parser.py claiming 290 cM0 to 5 cM1 was **re-measured over all 1,000 bundles
rather than trusted, and is exactly right**: 295 observations, one per patient,
no patient carrying two, values `1229901006` / `1229903009` with full SNOMED
display text. So reading cM0 as an early stage would reach 58 patients wrongly
for every one it reached rightly, in the damaging direction — a stage floor low
enough to drop the advanced-disease trials they qualify for.

**THE MEASURED EFFECT ON THIS CORPUS IS ZERO, AND THE REASON OVERTURNS THE
ITEM'S PREMISE.** All five cM1 patients **also carry a stage GROUP Observation
reading "Stage 4 (qualifier value)"**, so the tier above the new one already
answered for them. 0 patients change stage; 0 trials newly dropped; 0 newly
kept. Nobody is "staged from their diagnosis text instead, or not at all"
today. Three things keep that zero from being vacuous:

- **The tier fires on real data.** Withhold the stage-group list from those same
  five real patients and the M rule reaches 4 on its own — **three of the five
  have no stage anywhere else in their record**, and a fourth resolves to 3
  without it.
- **The ruler is not blind.** Priced over the real 14,324-trial corpus, a
  None → 4 transition newly drops **1,093** trials; 1 → 4 drops 622 and keeps
  4,641; 3 → 4 drops 747 and keeps 3,745.
- **cM0 moves nobody.** All 290 cM0 patients, stage group withheld: 0 change.

**"CARRYING BOTH IS A CONTRADICTION" IS ALSO WRONG AS STATED.** Five patients
carry a stage group *and* cM1, and all five **agree** at IV. A contradiction is
a group BELOW IV beside cM1, and there are **zero** of those. The extractor
does not try to reconcile them — the tier order means the assigned group wins —
and that is argued at the function.

**THE LOINC HAS ONE SPELLING**, `constants.LOINC_AJCC_CLINICAL_M`, because
parser.py ROUTES by it and stage.py SELECTS by it and two literals that drift
make the rule **silently never fire** — the CROSS_ENCODER_MODEL shape. The
other three codes in `_METASTASIS_LOINCS` deliberately keep their literals:
each has one reader, and **44667-4 must not be treated as an M category** even
though it shares the axis, because it carries metastasis SITE names. The rule
therefore keys on the CODE, never on `metastasis_category == "M"`.

**`M_CATEGORY_UNREADABLE`** counts 21907-1 values the regex cannot read, keyed
by the capped text, on `AGE_PARSE_FAILURES`' footing (third-party data counts,
configuration raises). **cM0 and cMX are NOT counted** — they were read, and
"this axis contributes no stage" is a determinate answer, not a degradation.
It measures **zero** over the corpus.

**`tests/test_extraction_stage_m_category.py` — 119 checks**, no network, no
keys, no spend, no git history, no corpus, not in the collision matrix. Ten
planted defects, ten caught, each into an in-memory COPY with both touched
files hashed before any plant and compared at the end **plus a non-degeneracy
probe, because the first version of that comparison hashed one file twice in
one expression and was a tautology**. It is the sixth member of
`_EXEC_ALLOWLIST`, and its control 8 is why: it reverts **only Stage 4's call
site** while leaving the extractor entirely correct, which is a state no commit
ever had, so `git show` could not produce it.

**`fixture_replay.py` COULD NOT RUN, AND THE REASON IS NOT THIS CHANGE.** It
refuses at its pinned-collection gate, before any fixture is diffed: the alias
`trial_criteria` now resolves to **`trial_criteria_20260807_111807`** while all
twelve fixtures are pinned to `trial_criteria_20260803_104642`. That is the
scrape-admission re-index (the same run that produced today's
`trials_latest.json`), and it predates this pass — the refusal is item 20c-3d's
guard working. **Every pass that claims "12/12 clean without recapture" from
here on has to re-capture first, or point the alias back.**

> **CURRENT STATE, 2026-08-20 — that instruction has been discharged and is
> left standing above as this pass's own conclusion rather than rewritten.**
> The recapture happened. The twelve fixtures on disk are at the current
> `SCHEMA_VERSION` and `python fixture_replay.py` is **12/12 clean, exit 0,
> with no recapture**, measured on this date. A pass may claim it again —
> and must, because that claim is what says the deterministic prefix did not
> move. Read the fixture-state note in the test block near the top of this
> file for the current schema number.

What was proven instead, directly and without Qdrant: the **twelve fixture
patients were looked up in the corpus by the patient_id stored on each
fixture**, and Stage 4's `patient_stage` is **identical before and after for all
twelve, and equal to the stage each fixture recorded at capture** (2, 1, 3, 2,
4, 1, 1, 2, 4, 4, 1, None — five distinct values, so the comparison is not
degenerate). None of the twelve carries cM1. `patient_stage` is the only
pipeline value this change can move, so no fixture's deterministic prefix can
have moved either.

**explore.py's stage analysis is UNCHANGED, run rather than argued.** All 278
distinct condition displays in the corpus through the shipped extractor and
through `git show HEAD:`'s: **0 differ**. `analyze_cancer_stages()` then run end
to end on frames built from the real corpus, both ways: console output
identical (781 chars) and the PNG **byte-identical**. Note `06- FHIR Dataset
Characterization.py` cannot be run whole on this machine — it reads Synthea's
CSV export and `EXPORT_CSV` is off, so `.../01- Patients/csv/` does not exist.

**A LARGE PRE-EXISTING DEFECT WAS FOUND AND DELIBERATELY NOT FIXED HERE.**
`_SNOMED_DISPLAY_STAGE_RE` matches **"Chronic kidney disease stage 3
(disorder)"**. Of the 260 corpus patients with no stage group whose stage comes
from a condition display, **245 (94%) get it from chronic kidney disease** and
only 15 from a real cancer TNM display — so those 245 are filtered against a
kidney stage. Corpus-wide the regex matches CKD displays 1,025 times against 16
cancer ones. Folding it in here would have broken this pass's own acceptance
criterion that every patient not carrying cM1 resolves exactly as before.
**Closed by the next section.**

### The patient stage tier stopped reading kidney disease (the CKD guard pass)

**THE GUARD ALREADY EXISTED AND WAS WIRED UP, NOT REWRITTEN.**
`_is_non_oncology_stage` + `_NON_ONCOLOGY_STAGE_CONTEXT_RE` (CKD, GVHD, NYHA,
Child-Pugh, COPD, pressure ulcers, retinopathy, …) had been in
`oncotriage/extraction/stage.py` all along, used by the TRIAL-side extractor
through `_collect_stage_ordinals()`. `extract_patient_stage()`'s
condition-display tier never consulted it. It does now, with `finditer` +
`continue` mirroring the trial side exactly — **not** `search` + give up, so a
display carrying two stage mentions can still yield the cancer one.

**THE PRIOR PASS'S FIGURES WERE RE-MEASURED, NOT INHERITED, and are exactly
right:** 260 display-derived patients, **245 from CKD, 15 from a real cancer TNM
display**; corpus-wide **1,025 CKD matches against 16 cancer**. (The corpus
cache used was itself re-validated: 20 bundles re-parsed fresh with the real
parser, 0 differences.)

**244 OF 1,000 PATIENTS CHANGE STAGE**, and every one of them had a CKD display
as the source of the old stage — **zero non-CKD patients moved**:

| transition | patients |
|---|---|
| 1 → None | 33 |
| 2 → None | 61 |
| 3 → None | 65 |
| 4 → None | 84 |
| **3 → 4** | **1** |

**THE TWO PATIENTS WHOSE CKD STAGE WAS MASKING A REAL CANCER ARE THE POINT.**
`404d2880…` read as stage **3** from "Chronic kidney disease stage 3" while
carrying **"Metastatic malignant neoplasm to prostate (disorder)"** — suppressing
the CKD mention lets the metastatic tier answer, and they are now **4**.
`1c1fdc23…` read as **1** from "Chronic kidney disease stage 1" while carrying
**"Non-small cell carcinoma of lung, TNM stage 1"** — same number, and for the
first time the right reason.

**WHAT MUST STILL BE TRUE, ALL RUN:** the 295 patients carrying a resolving
stage GROUP — **0 moved** (the mCODE tier is deliberately unguarded: those
observations are cancer staging by their LOINC, so a guard there could only
suppress a legitimate stage). The 16 patients with a real cancer TNM display —
**0 moved**. Of the 260 display-derived patients, **17 still hold a stage**: the
15 genuine TNM ones plus the two above. And the disease-specific-phrase rule
holds by measurement rather than by citing the comment — `Stage IV renal cell
carcinoma` → 4, `Stage 2 carcinoma of kidney` → 2, `Hepatocellular carcinoma,
Stage 4` → 4, `Malignant neoplasm of kidney, TNM stage 1` → 1, all unchanged.

**THE MATCHING COST IS ENORMOUS AND IT IS ALMOST ALL RECOVERY.** Over the real
14,324-trial corpus, summed across the 244 changed patients:

- **trials NEWLY KEPT (dropped before, kept now): 827,665**
- **trials NEWLY DROPPED (kept before, dropped now): 747** — all of them the one
  3 → 4 patient, who is genuinely metastatic.

Per patient that is 5,112 recovered for a 1 → None, 4,877 for 2 → None, 4,091
for 3 → None and 1,093 for 4 → None. **Stated at the scale that matters**: a
stage of 1 drops **35.7%** of the trial corpus and Stage 4 only ever judges
`TOP_K_CANDIDATES = 40`, so a wrongly-CKD-staged patient was losing on the order
of **14 of their 40 candidate trials**.

**THE COUNTER KEY IS SEPARATE, AND THAT WAS CHECKED BEFORE IT WAS ADDED.**
`non_oncology_stage_skipped` is read by `oncotriage/retrieval/indexer.py` after
an index build to describe TRIAL text; the patient side fires at QUERY time on
every patient of every run, so sharing it would put an unbounded query-time
count into an index-time statistic. `_is_non_oncology_stage` takes a
`counter_key` defaulting to the trial-side key — one implementation, two
statistics, no existing call site changed — and the patient side passes
**`non_oncology_patient_stage_skipped`**, which reads **783** over one clean
pass of the corpus. **Nothing pins that dict's key set**: the only readers are
`tests/test_registries_cancer_codes_and_stage_extraction.py` (individual keys
and `any(values())`) and the indexer (prints the whole dict); no test compares
`.keys()`. Section 4e re-derives that finding by AST so it cannot rot. The dict
stays a **plain dict rather than a Counter** so an undeclared key raises
`KeyError` instead of silently creating a counter nobody reads.

**THE METASTATIC-KEYWORD TIER IS DELIBERATELY UNGUARDED, and the reason is that
the guard is the wrong instrument rather than that the tier is safe.**
`_is_non_oncology_stage` answers "is this stage NUMERAL qualified by a non-cancer
STAGING SYSTEM" — it needs a match span to window around and its vocabulary is
CKD/GVHD/NYHA/Child-Pugh/COPD. This tier has no numeral and no staging system.
Its real false-positive class is a different vocabulary — "metastatic
calcification" (classically secondary to CKD), "metastatic abscess", "metastatic
infection" — **none of which that regex contains**. Measured: exactly **one**
condition display in the whole corpus contains "metastatic", it is genuine
cancer, and this tier is the answering tier for **zero** patients. Inventing a
guard for it would be untested code guarding nothing; the vocabulary is a
recorded follow-up.

**FIVE OF THE TWELVE FIXTURE PATIENTS MOVE, AND THAT IS STATED PLAINLY RATHER
THAN ENGINEERED AROUND.** `ablation_bm25_only` 2 → None, `ablation_vector_only`
3 → None, `mcode_genomic_variant` 4 → None, `normal_2` 4 → None, `normal_3`
4 → None — **every one of them a CKD display**. Those five fixtures' pipeline
output has moved with the stage, so they are stale and **must be re-captured**.
The other seven are unchanged (1, 2, 1, 1, 2, 1, None). `fixture_replay.py`
could not be used as the check either way: it refuses at its pinned-collection
gate because the alias now resolves to `trial_criteria_20260807_111807` while the
fixtures are pinned to `…20260803_104642`.

**`oncotriage/fhir/explore.py`'s STAGE DISTRIBUTION MOVES A LOT, AND THE NEW ONE
IS THE HONEST ONE.** It calls `extract_patient_stage([{'display': d}])` per
condition row with **no observations at all**, so its entire chart was built from
condition displays — which are 98% CKD:

| bucket | before | after |
|---|---|---|
| Stage I | 34 (3.6%) | 16 (1.7%) |
| Stage II | 55 (5.8%) | 0 |
| Stage III | 58 (6.1%) | 0 |
| Stage IV/Metastatic | 180 (18.9%) | 1 (0.1%) |
| Unspecified | 624 (65.6%) | **934 (98.2%)** |

**That chart has been mostly a chart of chronic kidney disease stages.** It now
says what its inputs actually support, and its own ">50% unspecified" warning
fires at 98.2%. The fix is for `explore.py` to read the mCODE stage-group
observations the pipeline reads — 295 patients carry one — which its
CSV-derived frame does not currently carry. **Recorded as the top follow-up.**

**`tests/test_extraction_stage_non_oncology_guard.py` — 80 checks**, no network,
no keys, no spend, no git history, no corpus, not in the collision matrix.
**Eight planted defects, eight caught**, each into an in-memory copy with the
file hashed before any plant and compared at the end against a real baseline
plus a non-degeneracy probe. The controls include the two mistakes that no CKD
test would catch on its own: **widening the guard's vocabulary to the bare word
"renal"** (which suppresses `Stage IV renal cell carcinoma`) and **applying the
guard to the stage-GROUP tier** (which suppresses a legitimate stage for any
patient who also has CKD). It is the seventh member of `_EXEC_ALLOWLIST`, and
`git show` could not have supplied any of these controls: the patient side has
never had this guard, so there is no revision to compare against.

### Stage 5 stopped inventing rejections (the trial-verdict pass)

**AN UNRECOGNISED TRIAL-LEVEL VERDICT WAS RECORDED AS `not_eligible`.** The
post-processing loop in `oncotriage/agent/evaluation.py` opened with
`if eval_result.get("eligible") not in _TRIAL_LEVEL_LABELS: eval_result
["eligible"] = "not_eligible"` — a rejection, a statement that this trial
assessed the patient and turned them down, which the model never made. Every
other unreadable answer in that file resolves to "not evaluated" and says why:
Step 2 for a trial returned with no criteria, Step 3's remap branch for a
rejection whose every disqualifier was out of vocabulary, `_normalize_arm` one
level down for a criterion status. The zero-criteria branch rescued only the
entries with NO criteria; an entry WITH criteria kept the fabricated rejection
and flowed into the patient's near-miss list.

**IT WAS DESTROYING THE VALUES THE PIPELINE'S OWN NORMALIZER EXISTED TO RESCUE,
and that was found by running rather than by reading.** `node_finalize` has
always carried a six-entry map for boolean `True`, `"Eligible"` and `"yes"` —
and Stage 5 runs first, so the clobber reached those values before the map could
and the map could never be observed to disagree. Measured on the shipped code:
`True` → `not_eligible` → **near_misses**. So the vocabulary was written twice
and the two copies disagreed about the same input, invisibly.

**ONE VOCABULARY, ONE NORMALIZER, TWO CALLERS.** `oncotriage/agent/state.py`
now holds `TRIAL_VERDICT_*`, the closed `TRIAL_VERDICTS`, the closed
`VERDICT_SOURCES` and `normalize_trial_verdict(raw) -> (verdict, source)`. It
**returns `None` rather than a default**, because every default available there
is a claim: `not_eligible` asserts a rejection, `eligible` asserts a match, and
`not_evaluable` is a policy about an uninterpretable answer rather than a
reading of one. The policy lives at each call site, where the criteria are in
scope. The recovery vocabulary is deliberately small — case-folding and
whitespace are parsing, not guessing, and the four synonyms are Stage 6's own,
adopted verbatim rather than invented. **The bool test runs before any dict
lookup**: `True` and `1` are the same dict key in Python, so one map holding
both would answer for the integer 1 as though the model had written `true`.

**THE DISQUALIFICATION CHECK OUTRANKS AN UNREADABLE LABEL, and that is the one
decision not in the brief.** Criteria are the model's evidence; the trial-level
label is its summary of them. An unreadable summary does not delete a criterion
the model marked `not_met`, and recording such a trial as "not evaluated" would
hide a stated failure and hand a clinician a candidate the model had already
disqualified — the same fabrication pointing the other way. So the rejection
stands, and the label defect is recorded in `verdict_normalizations` rather than
in `unevaluable_trials`, which feeds a log line reading "these are not
rejections".

**A TOP-LEVEL ENTRY THAT IS NOT AN OBJECT CRASHED THE WHOLE PATIENT.** The
response was validated as a LIST and its MEMBERS were not, so a bare NCT id
string, a number, a null or a nested list reached the enrichment loop and raised
`AttributeError: 'str' object has no attribute 'get'` — confirmed by running,
at `evaluation.py:1091`, for all four shapes. Nothing catches it: `graph.invoke`
wraps nothing. Such an entry is **dropped**, counted in the module-level
`MALFORMED_EVALUATION_ENTRIES` (keyed by JSON type name, on `AGE_PARSE_FAILURES`'
footing) and logged — never repaired and never turned into a verdict, because it
carries no nct_id and there is nothing to attribute one to. **The trial is not
lost**: the reconciliation block already records an absent trial by nct_id, and
the test proves it does.

**MEASURED AGAINST THE PRODUCTION DATABASE, read-only, 1,106 rows before and
after.** The raw label is not a column, so the exact count is not recoverable;
what is, is the only stored population the changed line can reach — a
`not_eligible` row with criteria and no surviving disqualifier. **43 of 12,862
(0.334%), across 43 inferences, and all 43 carry the model's own "Known
disqualifier: …" explanation**, which a fabricated rejection would not (it keeps
the model's positive text). **Zero stored evaluations show the signature.** The
model's out-of-vocabulary rate is not zero, though: **212 stored criterion
entries carry an exclusion-arm status on an inclusion criterion**.

**VERIFIED BY RUNNING.** All 24 existing test files at their documented counts,
`tests/test_package_invariants.py` unchanged at **247** (the new file is the
**eighth** `_EXEC_ALLOWLIST` member, argued there — `git show` could supply none
of its controls: `normalize_trial_verdict` has no prior revision, several
controls revert one line while leaving the rest correct, and an exec'd copy of
`evaluation.py` binds the LIVE `state` module, which the test asserts as a
precondition before relying on it), the serial runner **5/5**, and
`fixture_replay.py` **12/12 clean without recapture**. **No money was spent.**
**Eight reverts, eight caught** — each production fix broken in a copytree'd
copy with `PYTHONPATH` pointed at it (the first version of that harness reported
**0/8** because the editable install meant the copy was never imported, so a
preflight now asserts on realpaths that it is), and the run's own summary
required each time.

**THE TEST FILE SHIPPED TWO OF THIS PROJECT'S RECURRING DEFECTS BEFORE THE
REVERT HARNESS FOUND THEM, and neither was visible by reading.** A bare call
into production code let a planted `AttributeError` and a planted `TypeError`
escape through `check()`'s argument list, and a bare `log_records(...)[0]` raised
`IndexError` when a defect stopped a record being emitted — the run reported one
traceback where it owed 161 results. Both are closed the way
`tests/test_storage_query_layer.py` and
`tests/test_dashboard_reproducibility_tab.py` had to close them: the drivers
return a result-shaped stand-in carrying `raised`, and `field(records, key)`
returns a named absence instead of indexing.

### A lost inference row is no longer invisible (the write-durability pass)

**`log_inference` CAUGHT `sqlite3.Error`, PRINTED "non-critical", AND RETURNED
`db_path` EXACTLY AS ON SUCCESS.** The caller could not tell the row was lost, so
the patient was recorded as successful and the run reported complete. Every
number in the paper comes from one final run; if that run loses rows and reports
complete, the result looks whole and is not. **`_WRITE_LOCK` IS UNTOUCHED** — it
closes the in-process race and section 5e measures it doing so. Everything here
is about the processes it cannot reach.

**WHO ACTUALLY WRITES `inferences.db`, established by reading rather than
assumed.** `oncotriage/batch/runner.py` (MAX_WORKERS threads, one process) and
`oncotriage/api/server.py` (`loop.run_in_executor`, once per in-flight request).
**NOT the Airflow DAG**: its three tasks are `scrape_and_save`, `rebuild_index`
and `verify_index`, all three delegate to `oncotriage.retrieval.indexer`, and
that module touches Qdrant and never opens the inference database.

**1. WAL, VERIFIED BY READING THE PRAGMA BACK.** Journal mode is a property of
the FILE, not the connection, and `PRAGMA journal_mode=WAL` does not raise when
it cannot be honoured — it returns the mode still in force. `_apply_journal_mode`
reads it back, and a mismatch is a `JOURNAL_MODE_DEGRADATIONS` entry keyed
`requested->actual` plus a WARNING naming both modes, the file, the two usual
causes (network filesystem, unwritable directory) and how to accept it
deliberately. `SQLITE_JOURNAL_MODE` is the opt-out for a network share, which is
why it is a tunable rather than a literal. **The production database is in
`delete` mode today (1,106 rows, 86 MB) and the next run converts it
permanently.** `mode=ro` URI readers were checked against a WAL database with a
live `-wal` file present and still read it, so Files 18/19's guards and every
read-only consumer survive.

**2. THE BUSY TIMEOUT IS A DECISION.** Python's sqlite3 defaults to 5.0 s, which
section 5e records measuring; `SQLITE_BUSY_TIMEOUT_SECONDS = 30.0` is chosen, and
it is applied through `_open_connection` — **every** `sqlite3.connect` in the
module is inside that function, asserted by AST rather than by inspection,
because the timeout is per connection and the migration connection is the one
that takes an exclusive lock.

**3. THE RETRY IS NARROW, AND THE EXCLUSION IS THE INTERESTING HALF.**
`_is_retryable` retries only contention — a `sqlite3.OperationalError` whose
message names a locked or busy database. **`duplicate column name` is
deliberately NOT retried even though retrying it would work**: that error is the
signature of the migration race `_WRITE_LOCK` exists to close, and section 5e
proves the lock necessary by STRIPPING it and requiring rows to be lost. A retry
broad enough to repair that race repairs the negative control too, and silently
deleting the evidence for a lock is worse than not retrying an error the lock
already prevents. **Confirmed by running: section 5e's control still fires, 6/8
trials lost rows.**

**4. THE OUTCOME REACHES THE CALLER, AND THE SHAPE WAS FORCED.**
`log_inference` returns an `InferenceWriteResult`, a **`str` subclass** carrying
`.ok`, `.error`, `.attempts` and `.inference_id`. Not a tuple: the return value
is a pinned contract at **seven call sites across four test files** (counted, not
recalled — `test_storage_ecog_logging.py:328`,
`test_storage_inference_logging_contract.py:811,910`,
`test_agent_retrieval_observability.py:994,1027,1061`,
`test_fhir_birth_date_and_demographics.py:896`), each comparing it with `==`
against its own scratch path, and that comparison is what makes those isolation
tests checkable. A str subclass compares, hashes, formats, os.path-joins
and JSON-serialises exactly as the path did. `__slots__` is non-empty and
verified to reject an arbitrary attribute, so a reader cannot trust a field
nothing set. Both production callers **discarded** the return value before this
pass, which is why the counters exist as well.

**5. RECONCILIATION IS EXACT, NOT STATISTICAL.** A rows-vs-patients count is
wrong in two directions here — the resample pass writes a SECOND row per
re-run patient, and a checkpoint resume leaves rows this process did not write —
so `oncotriage/batch/runner.py` keeps a **ledger of CALLS**, which sidesteps both
by construction rather than by correcting for them. The verdict then asks the
database whether **those specific ids** are present. The before/after count delta
is reported as a cross-check and deliberately does **not** decide, because
another process writing the same file inflates it, and a delta inflated by
exactly as many rows as the run lost reconciles perfectly while the data is gone.
`25- Batch Runner.py` exits **0 / 1 / 2** (complete / rows lost / never
reconciled) — **a contract change, stated as one**, on File 19's precedent; no
caller reads it today. `main()`'s return type is unchanged.

**THE MULTI-PROCESS CASE WAS TESTED, CHEAPLY, AND THE HONEST FINDING IS
REPORTED.** 8 and 16 real OS processes, synchronised on a start gate, each
writing 15 `trial_matches` children per row into one file:

| configuration | rows lost |
|---|---|
| pre-pass as shipped (delete journal, 5 s, no retry), 8 and 16 procs | **0** |
| shipped now (WAL, 30 s, 4 attempts) | **0** |
| squeezed to a 10 ms timeout, no retry, delete journal | **10 of 200** |
| squeezed to a 10 ms timeout, WITH the retry | **0** |

So at this machine's contention the old configuration was already sufficient —
the same shape as section 5e's honest finding about the steady-state insert path,
and real writes are separated by ~70 s of pipeline, so production contention is
LOWER than this harness's. **What the pass actually buys is headroom and, far
more importantly, visibility: in every arm the workers' reported losses matched
the table's shortfall exactly, and before it those 10 rows would have been
silent.** The first version of that harness lost nothing in ANY arm because
process spawn staggered the writers past each other — a control that produces no
contention proves nothing about a fix for contention, and it is recorded because
that is how it failed rather than how it was reasoned about.

**`tests/test_storage_write_durability.py` — 99 checks**, no network, no keys, no
spend, no git history, no corpus, not in the collision matrix, and it **execs
nothing**, so it needs no `_EXEC_ALLOWLIST` entry: every control creates the
failing condition for real (a genuine `BEGIN EXCLUSIVE` from a second connection,
an unwritable path, a row deleted behind the writer's back) or rebinds a module
attribute inside a `try/finally`, with both touched files' sha256 compared at the
end. **Nine reverts, nine caught**, each in a `copytree`'d copy with a realpath
preflight asserting the COPY is what imports and `PYTHONDONTWRITEBYTECODE=1` set
— including the original defect itself (a lost write reporting success: 15
failures). The strongest control is section 7: a row the writer reported as
written is deleted behind its back and the reconciliation must find it missing,
which a report-trusting counter cannot.

**VERIFIED BY RUNNING.** All 25 existing test files at their documented counts,
`tests/test_package_invariants.py` unchanged at **247**, the serial runner
**5/5**, and `fixture_replay.py` **12/12 clean without recapture**. **No money
was spent.** `oncotriage/config.py` and
`oncotriage/registries/cancer_code_registry.py` confirmed restored afterwards.

### The reproducibility hash covers what the pipeline reads (the hash pass)

**`compute_patient_hash`'s DOCSTRING PROMISED SOMETHING IT DID NOT DELIVER** —
"two inferences with the same hash are guaranteed to have identical input data"
— while three parsed fields were absent from it, each reaching the output by a
different route: `cancer_genomic_variants` (File 07 routes mCODE variants OUT of
`observations` entirely, so biomarkers drove the retrieval query and a named
Stage 5 section while being invisible to the hash), `allergies` (their own
prompt heading), and `cancer_stage_observations` (Tier 0 of
`extract_patient_stage`, whose ordinal drives the Stage 4 stage filter).

**THE BRIEF SAID FOUR FIELDS AND IT IS THREE.**
`cancer_metastasis_observations` was **already hashed** — the AJCC M-category
pass added it — and only the docstring never listed it. Measured before anything
was edited, by constructing a patient differing only in that field and watching
the hash move.

**`patient_data_hash` IS NOT IN THE DETERMINISTIC PREFIX, so no billed recapture
was needed.** Verified two ways: `build_deterministic_prefix` never reads it, and
flattening a real fixture gives **94,329 leaves and zero hash keys**. **But there
is a SECOND gate the brief did not name**, and it would have failed five
fixtures: `oncotriage/fixtures/replay.py` compared
`compute_patient_hash(rebuilt)` against `fixture["identity"]["patient_data_hash"]`
and made a mismatch **FATAL** for constructed fixtures. All twelve recorded
hashes move; five are constructed.

**THAT GATE IS FIXED RATHER THAN WORKED AROUND, AND THE FIX IS STRICTLY
STRONGER.** The property it exists to defend is "the recipe still reproduces its
input", and it tested that through a FUNCTION — so any legitimate change to what
that function hashes turns every constructed fixture fatal with a message
blaming the recipe, the donor bundle or the parser, none of which moved. It
compares the rebuilt `patient_data` against the recorded `patient_data` now: the
hash is 16 truncated hex characters over a chosen subset of sub-fields and can
collide, a dict comparison cannot, and it NAMES the field that moved. The
capture-time hash is **provenance**, like `captured_at_utc` beside it in the same
`identity` block — reported on every replay, never enforced, so drift is visible
rather than silent or fatal. **No fixture file was rewritten.**

**WHAT WENT IN, PER FIELD, WITH ITS READERS NAMED.** `allergies`: display,
category, criticality — the three `_create_patient_summary` renders; `code` and
`onset_date` are excluded (no reader), and `clinical_status`/`verification_status`
are read by the PARSER, which admits only active non-refuted allergies, so their
effect is already visible as presence. `cancer_genomic_variants`: display,
gene_symbol, hgvs_protein, hgvs_cdna, result_value, interpretation, date — one
per reader, including `result_value`/`interpretation` because
`filter_relevant_genomic_variants` DROPS Absent results, so a flip to "Absent"
removes a whole prompt line; `code`, `genomic_source` and `value` are excluded.
`cancer_stage_observations`: stage_display, date, loinc — the extractor reads the
first, sorts on the second, and the third is the staging AXIS (the analogue of
`metastasis_category`); `stage_code` is excluded as a second encoding with no
reader, **and if the extractor ever reads it this entry must gain it**.

**THE OBSERVATIONS ARE HASHED, NOT THE STAGE** — the `birth_date` rule again. The
ordinal is a function of these records AND of the extractor's tier order and
regexes, both changed twice recently, so hashing it would move every patient's
hash whenever the extractor was edited while their bundle had not changed.

**A SECOND, SEPARABLE DEFECT WAS FOUND WHILE CHECKING THE BRIEF'S OWN
REQUIREMENT, AND IT WAS PRE-EXISTING.** "Parsing it in a different order produces
the same hash" was listed under what must STILL be true. It was **not** true:
each collection was sorted by a KEY and emitted with MORE fields than the key
covered, so ties kept parse order. Measured — one real bundle shuffled six times
gave two different hashes, and the **pre-change** function did the same on the
same three shuffles; the culprit was `observations`, 3,660 records with one tied
`(display, date)` pair whose `value` differed. **20 of 30 bundles were
order-unstable before; 1 of 30 is now.** Every collection is emitted through
`_emit`, which sorts the **line** rather than a subset of its fields — equivalent
today, and it cannot go stale when a field is added to the string.

**THE TWO CHANGES CONFLICT AND THE NUMBERS ARE REPORTED SEPARATELY**, because
"a patient carrying none of the new fields hashes exactly as before" and "parse
order does not reach the hash" cannot both hold:

| change | patients whose hash moves (of 1,000) |
|---|---|
| the three new fields, alone | **392** — and **0** of the 608 carrying none |
| the tie-break canonicalisation, alone | **1,000** |

The corpus carries **131** patients with allergies, **295** with stage
observations and **0** with genomic variants (Synthea generates none; only the
constructed `mcode_genomic_variant` fixture has one). The production database
holds **1,106** rows carrying a hash, **1,004 of them distinct**, and the
canonicalisation invalidates every one — reported for the record, and per the
brief not a constraint: that database is disposable and every published number
comes from a fresh end-to-end run. Nothing was designed around them.

**THE ONE REMAINING ORDER DEPENDENCE IS THE PARSER'S, NOT THE HASH'S, and it is
reported rather than fixed.** `parse_fhir_bundle` keeps the FIRST record per
medication display, so a shuffle changes which duplicate survives — invisible to
the hash, which reads only the display SET, until a bundle carries two spellings
of one drug. One does: `Aspirin 81 MG Oral Tablet` and `aspirin 81 MG Oral
Tablet`. Choosing a canonical spelling changes the Stage 5 prompt text, which is
a decision about the prompt rather than about the hash.

**`tests/test_agent_patient_hash_coverage.py` — 69 checks.** Every included
sub-field is shown to move the hash and every EXCLUDED one shown not to, which is
"say which you included and why" made executable; a literal patient carrying none
of the five is PINNED, which is the only thing that catches an entry made
unconditional (revert r5 fails on that check alone). **Eleven reverts, eleven
caught.**

**THREE DEFECTS IN THIS PASS'S OWN WORK WERE FOUND BY RUNNING, NOT BY READING.**
The pin was written as a placeholder and had to be LIFTED from the function
rather than guessed — the pass-20f-4 lesson, reproduced. The docstring said
"ecog" where the code reads `ecog_performance_status`, caught by section 6's own
derived-key scan. And section 4e was written twice wrongly before it was right:
first comparing parsed dicts with `==` (which includes list ORDER, so shuffling
made every sample "different" and the check asserted over an empty set), then
comparing full record content (which differs on every shuffle because of the
medication de-duplication above). **Its non-degeneracy assertion is what caught
both** — without it, 4e would have passed vacuously, twice.

**VERIFIED BY RUNNING.** `fixture_replay.py` **12/12 clean without recapture**,
`tests/test_package_invariants.py` **247**, and every hash-adjacent existing test
at its documented count. **No money was spent.**

### The run-to-configuration index (the tracking pass)

**PROVING WHICH CONFIGURATION PRODUCED WHICH NUMBER WAS NOT POSSIBLE FROM THE
DATABASES, AND STILL IS NOT — THAT IS WHAT THIS ADDS.** `inferences.db` and
`ablation_results.db` store PER-PATIENT rows; neither stores a run's INPUTS. So
"which prompt version, model, retrieval sizes, thresholds and commit produced
the headline number" had no answer in the artifacts, only in whoever remembered.
`oncotriage/tracking.py` is an index over runs, above both databases and
replacing neither.

**THE PACKAGE IS `mlflow-skinny==3.15.1`, AND THE FILE STORE NEEDED AN OPT-OUT
NOBODY EXPECTED.** Skinny is the client-only distribution — no flask, no
alembic, no sqlalchemy, no server, no UI — and installing it into the
development environment adds exactly **two** distributions (`mlflow-skinny`,
`databricks-sdk`) and **moves no pin**, measured with `pip install --dry-run`.
`pip-audit` over the resulting tree reports **zero** findings, so nothing was
added to `audit_gate.py`'s accepted table. Every one of its own requirement
bounds is an open-ended upper bound (`fastapi<1`, `pydantic<3`, `starlette<2`,
`uvicorn<1`) that the existing pins already satisfy, so it does not drag the
serving layer forward the way `mcp` once dragged starlette.

**What it costs: MLflow 3.15 put the FILESYSTEM backend into maintenance mode
and it RAISES unless `MLFLOW_ALLOW_FILE_STORE` is set.** The message names
`sqlite:///mlflow.db` as the replacement and that route is **not available under
skinny** — measured, not assumed: a sqlite URI fails with
`UnsupportedModelRegistryStoreURIException`, because the model-registry store has
no sqlite implementation in this distribution, and a SQLAlchemy backend would
need the alembic tree skinny exists to avoid. The module sets the vendor's own
documented opt-out, only when it is unset, and prints which. Setting it at CALL
time is safe rather than lucky: MLflow reads the variable when the store is
CONSTRUCTED, proved both ways.

**THE PATH TABLES ARE A TRIPLE, NOT A PAIR.** `result_tracking_path` went into
the local glob branch, the Docker branch's literal `/app/...` table AND
`.github/scripts/provision_ci_paths.py:_skeleton()`. The first two are
cross-checked at import by `paths.py` itself; the third cross-checks itself
against `PATH_NAMES` at the end of its own `main()`. The local branch resolves
`{results}/*MLflow Tracking/`, under the existing results tree rather than as a
fourteenth root-level sibling glob. **Thirteen path variables became fourteen**,
and `tests/test_paths_glob_determinism.py`'s resolver-count non-degeneracy check
moved with them.

**THREE FUNCTIONS, AND NOTHING ELSE IMPORTS `mlflow`.** `start_run(kind,
params)` / `log_run_metrics(metrics)` / `end_run(status, artifacts)`. The
artifact store moves to S3 at the migration — recorded as a comment at
`tracking_uri()`, not as a half-built switch — and one wrapper makes that move
one file. The import is INSIDE the function bodies (the `import icd10` /
`import torch` third-party exemption), so importing the package pulls in no part
of a 33 MB tree and `tests/test_package_invariants.py` section 2 still passes
**247/0/0** with its twelve traps armed.

**WHERE IT RAISES AND WHERE IT COUNTS IS ITEM 11a's LINE.** `start_run` RAISES —
it runs before a cent is spent and everything that can fail there is
configuration; a missing package refuses by name with the install command and
**never** shrinks to a no-op. `log_run_metrics` and `end_run` DO NOT raise: they
run after the run has spent its money and written its rows, so they count into
`TRACKING_DEGRADATIONS` (registered in `oncotriage/degradation.py`, eighteenth
counter) and return False. A tracking layer that destroys the run it exists to
describe is worse than no tracking layer.

**PARAMS ARE NAMED CONSTANTS ONLY, AND THAT IS ENFORCED RATHER THAN CONVENED.**
`CONFIGURATION_PARAM_NAMES` enumerates 24 constants read off `oncotriage.config`
by name; `CALLER_PARAM_KEYS` is a CLOSED set of seven run-shape facts a constant
cannot carry (`sample_size`, `seed`, `configs`, `db_path`, …) and **an unknown
key raises**, `deps.OVERRIDE_KEYS`' shape. There is no `os.environ`, no
`config.__dict__`, no `vars()` and nothing from the keys directory anywhere in
the parameter path, asserted by AST — a credential in a tracking store outlives
every scrub. The test seeds a fake `OPENAI_API_KEY` and requires it in no key and
no value.

**"PROMPT SHA COVERAGE" IS TWO TEMPLATE FINGERPRINTS, NOT A RUN'S PROMPT SHA,
and the difference is stated at the code.** `render_system_prompt` takes three
arguments and two vary PER PATIENT, so a run has no single prompt sha — the
per-inference one already exists as `inferences.llm_classifier_prompt_sha256`.
What a run does have is a template with exactly one branch
(`mesh_filter_applied`), so one digest per branch is logged, rendered with
DECLARED probe arguments that are logged beside them.

**DEGRADE HONESTLY, TWICE.** The git commit is read by subprocess and records
`"unknown"` with a `git_commit_unknown` tag where git or `.git` is absent (the
container); the Qdrant collection goes through `resolve_qdrant_collection` and
does the same when the client cannot be built. **`git_dirty` is the one addition
beyond the brief**, argued: a commit identifies the code only if the tree matches
it, and the pass's stated goal is proving which CODE produced which number.

**THE BATCH RUNNER's METRICS SELECT, THEY DO NOT COMPUTE.** `print_summary`'s
`_stats` closure became the module-level `pass_stats()` — moved unchanged, it
closed over nothing — so the printed block and the index read ONE computation.
Proved: `print_summary`'s output is **byte-identical across 20 scenarios**
against `git show HEAD:`, with a control that differs. Five of its eleven
members are numbers and six are display strings ("12.3s"); only the five are
logged, because parsing a number back out of a display string would be inventing
a metric. An absent resample pass emits **no** resample metric rather than five
zeros, and no reconciliation emits **no** reconciliation metric — "not asked" is
not "rows were lost".

**THE ABLATION STUDY IS ONE PARENT AND ONE NESTED CHILD PER CONFIGURATION**, and
the children are opened AFTER `generate_summary()` rather than around each
config's loop: that function computes its numbers in one SQL query over the whole
database and reports the LATEST run per config, so a child per loop iteration
would both recompute the figures and miss every config a resumed study did not
re-run. An INTERRUPTED study gets no children and a `KILLED` parent — there are
no per-config numbers to index, and inventing them from a partial database is
the metric invention this pass forbids.

**A CRASHED RUN IS INDEXED AS `FAILED`, AND THE MEASUREMENT BEHIND THAT IS THE
SHARPEST THING IN THE PASS.** A process that opens an MLflow run and then dies
on an uncaught exception has that run recorded as **FINISHED** — MLflow's own
`atexit` hook ends it and does not know the process was failing. So a campaign
that crashed halfway would be indexed as a campaign that completed, which is
worse than an orphan left at RUNNING. Both callers wrap their body in
`except BaseException: tracking.end_run("FAILED"); raise` — the exception is
re-raised unchanged, so no pipeline behaviour moves and nothing is swallowed to
protect the index.

**THE REINDENT THAT GUARD NEEDED DAMAGED TWO DOCSTRINGS ON THE FIRST ATTEMPT.**
Adding four spaces to every line of the wrapped region also indented the
CONTINUATION LINES of multi-line string literals, silently editing two nested
docstrings in `study.py`. Caught by an AST comparison, not by reading. The
shipped version derives the protected line numbers from the AST and then PROVES
no literal moved — 40 and 117 string constants in the two `main()` bodies,
preserved exactly, plus the one `"FAILED"` the guard itself introduces — and a
`SequenceMatcher` over the flattened statement sequence shows **insertions only:
zero deletions and zero unexpected replacements** in either function.

**A RESUMED RUN IS A NEW TRACKING RUN TAGGED `resumed=true`**, in both entry
points. No run-continuation machinery was invented; the tag is what joins them.

**`tests/test_tracking_mlflow_index.py` — 99 checks**, bucket A, ~1.4 s, no
network, no keys, no spend, no live Qdrant, no corpus, **no git history
required** (section 8f accepts either outcome from the real probe, so a
`git archive` export reports rather than aborts), and not in the collision
matrix. It **execs nothing**: the missing-package control masks
`sys.modules["mlflow"]`, which drives the SHIPPED function precisely because the
import is deferred into it. Bucket A wall time **15.0 s → 15.8 s**.

**FOURTEEN REVERTS, FOURTEEN CAUGHT**, each applied to a `copytree`'d copy with a
realpath preflight asserting the COPY is what imports. **Two of them were initially "caught" by ABORTING the test file** — `git_commit` re-raising and
`start_run`'s validation moving below `mlflow.start_run` produced a traceback and
**zero** recorded failures where they owed several. That is the shape this
project has shipped four times before; every call into the module now goes
through a `drive()` / `raises()` wrapper that converts a raise into a value
`check()` fails on, and the same two reverts now report **5** and **26** failing
checks.

**THREE DEFECTS IN THIS PASS'S OWN CODE WERE FOUND BY RUNNING, NOT BY READING.**
(i) `start_run` resolved the Qdrant collection TWICE — once for the parameter and
once for the warning tag — two live calls that can disagree across an alias swap;
(ii) an `isinstance(value, (int, float))` metric test silently dropped every
`numpy.int64`, which is **not** a subclass of `int`, so every integer column the
ablation summary produces (`n`, `n_scored`, `errors`) would have been absent from
the index while every float column landed; (iii) a validation raise sat BELOW
`mlflow.start_run`, which would have left an orphan run at RUNNING forever. All
three are fixed and each has a check.

### A resumed run knows what it is resuming FROM (the fingerprint pass)

**THREE PAID HARNESSES PERSISTED PARTIAL STATE AND NONE OF THEM RECORDED WHAT
IT WAS PRODUCED UNDER.** `oncotriage/evaluation/run_harness.py` (a manifest plus
one JSON per patient), `oncotriage/batch/runner.py` (completed filename stems)
and `oncotriage/ablation/study.py` (completed `(config, patient)` pairs) all
recorded WHAT was done. So a resume after a prompt edit, a model change or an
index rebuild skipped the work the OLD configuration had completed, ran the rest
under the new one, and left ONE artifact holding two eras with nothing in it
saying so. Every mean, rate and comparison over that artifact is a number about
nothing.

**`oncotriage/run_fingerprint.py` IS THE ONE STAMP AND THE ONE COMPARATOR**,
beside `tracking.py` on the same layering argument (`config`, `utils`,
`agent.prompts`, `agent.readiness`; imported by `batch`, `ablation` and
`evaluation` alike, importing none of them). Five gated fields:
`llm_classifier_prompt_version`, `matching_model_configured`,
`qdrant_collection`, `collection_points`, `data_snapshot_date`. Three fields are
deliberately recorded and NOT gated, each argued at the code: `collection_alias`
(an alias may be repointed at the same backing collection, so gating it refuses
a rename that changed nothing), `age_reference_date` (a pure function of the
snapshot date, which IS gated — one fact counted twice otherwise) and
`probe_state`.

**THE MANIFEST'S `qdrant_collection` HELD THE ALIAS, AND THAT IS WHY THE FIELD
HAD TO CHANGE MEANING.** `readiness.probe_index()` defaults to
`config.COLLECTION_NAME`, so `manifest["environment"]["qdrant_collection"]` and
`collection_alias` beside it were **the same string on every manifest ever
written** — and an alias is a constant by design, so a gate on it is a gate that
can never fire. It holds the RESOLVED backing collection now, which is what the
per-record `run.qdrant_collection` has always held; the manifest and its own
records stop disagreeing about one field name.

**COLLECTION IDENTITY IS NAME PLUS POINT COUNT, AND IT IS WEAKER THAN THE
FIXTURE HARNESS'S GATE — STATED, NOT GLOSSED.**
`fixtures/capture.py:compute_collection_digest()` scrolls every `nct_id` and
hashes the sorted set, catching a same-count content swap. This does not. The
reason is layering rather than cost: two of the three consumers may not import
the fixture harness (`batch` and `ablation` are production and experiment code;
`fixtures.capture` imports the agent, the parser and the storage layer), and
moving the digest to a neutral module is a refactor with its own equivalence
proof — mixing a relocation into a gate pass is what makes an equivalence proof
stop meaning anything. So the limit is written into `COLLECTION_IDENTITY`, which
every consumer puts in its own artifact, and into every refusal that compared
fields. **Recorded as the top follow-up:** move `compute_collection_digest` to
`oncotriage/retrieval/`, prove it byte-identical, and raise all three gates.

**FIVE CLOSED OUTCOMES, BECAUSE EACH NAMES A DIFFERENT REMEDIATION.** `FP_MATCH`
resumes. `FP_CHANGED` is a field that genuinely differs. `FP_ABSENT` is unknown
provenance — nothing recorded, or a stamp with no version, which is **every
artifact written before this pass**. `FP_VERSION` is a different stamp SHAPE,
asked before any field is compared so a field this version gates and that
version never recorded is not reported as a configuration change. `FP_UNRESOLVED`
is *this* run's own configuration failing to establish, asked **first**, because
comparing against an UNKNOWN reports every field as changed and sends an
operator to clear a perfectly good checkpoint when the fault is an unreachable
endpoint. Clearing the artifact is right for three of the four refusals and
wrong for that one, which is exactly why they are not one "mismatch" member.

**THE LEGACY QUESTION HAS A MECHANICAL ANSWER, NOT A GUESS.** A stamp is
distinguished from a legacy artifact by `fingerprint_version`: absent means
"written before fingerprinting existed" and gets `FP_ABSENT` with that stated,
rather than having its missing fields compared against live values and reported
as a configuration change that never happened. A missing key is a DISAGREEMENT
and never a pass —
`ragas_harness.py:identity_disagreement`'s rule, adopted verbatim. **Nothing is
ever silently upgraded**: a legacy environment block is carried into
`environment_history` as era 0 exactly as found, unstamped, because writing
today's identity onto records produced by an unknown one is the single thing
this guard exists to prevent.

**RESOLUTION IS CACHED PER PROCESS AND THAT IS A CORRECTNESS ARGUMENT.** The
batch runner writes its checkpoint after every patient; a per-write stamp would
be tens of thousands of round trips — but the reason is that a run is ONE
configuration, and a per-write stamp straddling the weekly alias swap would put
two collections into one checkpoint and the file would then refuse itself. The
cache is behind an `RLock` (`deps._resolve`'s shape) because both consumers save
their checkpoint from a done-CALLBACK, which runs on a WORKER thread; the first
line of defence is that both `main()`s warm it on the main thread before their
pool exists, which is also what makes the value that gates the resume and the
value that stamps the writes ONE reading. The point count is taken of the
RESOLVED name (`probe_index(collection=resolved)`), closing the window in which
an alias swap between two round trips stamps collection A with B's count —
`tracking.configuration_params` records that exact defect, found by running.

**TASK 1 — `evaluation_run.py`.** `main()` OVERWROTE `manifest["environment"]`
unconditionally, `--only` re-runs into an existing directory included. It now
COMPARES first and writes nothing on a refusal.
`--allow-environment-change` admits a deliberate cross-era update and is not a
way to silence the guard: the stored environment is **preserved**, the new one
is APPENDED to `environment_history` as a numbered era, the invocation records
the override and the outcome that would have refused, and every record the
invocation writes carries its `environment_era`. A second invocation under the
same overridden configuration REUSES that era rather than appending a duplicate.
The override deliberately does **not** cover `FP_UNRESOLVED`: its contract is
that the new configuration is recorded, and an era whose identity is `unknown`
is what makes a mixed manifest unreadable.

  **A LIMIT WORTH KNOWING BEFORE USING THE OVERRIDE, and it is a finding rather
  than a defect of this pass:** neither downstream consumer reads the era.
  `evaluation/rater.py` and `evaluation/ragas_harness.py` both iterate
  `manifest["runs"]` whole (verified by reading both), so an overridden manifest
  is honest about its mix and will still be CONSUMED as one population until
  those two are taught to filter. The field they would read now exists.

**`--resume` SKIPS ON THREE FACTS AT ONCE**, never one: a manifest entry, a
status in `RESUME_SKIP_STATUSES`, AND the record file it names present on disk.
The third is what stops this becoming the defect every version gate here was
written to refuse — a patient counted as done because a table says so while the
artifact a downstream harness would read is missing. `ok` and
`nothing_to_evaluate` skip; `failed` and **`pipeline_error`** re-run. The last is
the one that could be argued either way and the argument is at the constants: a
`pipeline_error` record carries no verdicts, contributes nothing to either
evaluator and is reported by the post-check as a defect, so an operator resuming
after fixing it wants it retried — and the re-run is NAMED IN THE PLAN before
the first billed call rather than inferred from the bill afterwards. The two
lists must PARTITION `RUN_STATUSES`, enforced by a `RuntimeError` at import (not
an `assert`; `python -O` deletes those).

**`--resume` WITHOUT `--output-dir` IS A REFUSAL**, on this file's own "`--only`
with no ids" precedent: the default destination is a new timestamped directory,
so the flag can only ever run the whole slice at full price while looking like a
resume. `--resume` against a directory with no manifest is NOT an error and is
NOT silent — ragas' precedent — it names the reason and runs everything.
`--scan-only --resume` prints the whole plan for free and states that the
environment guard was not evaluated, because that needs the index probe.

**TASK 2 — THE BATCH CHECKPOINT, AND WHERE THE BRIEF'S OWN INSTRUCTION WAS
WRONG.** The brief said to preserve an unreadable checkpoint by RENAME, on
`CORRUPT_RESULTS_SUFFIX`'s pattern. Applied literally that produces the outcome
the brief forbids: a renamed checkpoint is GONE from its own path, so invocation
1 refuses loudly and invocation 2 finds nothing, starts fresh and **silently
re-bills the whole cohort**. It is COPIED instead (`preserve_corrupt_file(...,
keep_original=True)`), which leaves the refusal STICKY — every invocation
refuses until an operator clears it deliberately — while still putting the
evidence where that operator's fix cannot destroy it. The rename remains correct
for the RESULTS file and is unchanged there: that is a report, the checkpoint is
untouched, and the next run rebuilds it.

Three unreadable classes, each with its own phase key: a decode/OS failure
(`load:`), a payload that parses and is not an object (`shape:`), and a
`completed_stems` that is not a list (`shape:`). The last was previously
`set(data.get("completed_stems", []))` — a silent full re-run wearing the
clothes of a successful read. A non-dict payload used to raise `AttributeError`
uncaught.

**THE REMEDIATION FITS A NO-ARGUMENT ENTRY POINT WITHOUT CHANGING `main()`.**
`runner.main()` takes no arguments and its own docstring pins that; an embedder
calls it programmatically, and a `main()` that started reading `sys.argv` would
`SystemExit(2)` inside somebody else's process. So `--fresh` lives in
`25- Batch Runner.py`'s `__main__` guard — exactly where `05- FHIR Clean
Data.py` puts `--dry-run`, and for the reason recorded there. **Two contract
changes, stated:** a bare invocation is unchanged; an UNRECOGNISED argument now
exits 2 with usage, where it used to be ignored because nothing read `sys.argv`
at all — so a mistyped flag silently started a full-corpus billed run.

**TASK 3 — THE ABLATION CHECKPOINT** takes the same stamp with the same
semantics, inside each database's own checkpoint file, so pass 20f-3's
per-database isolation is untouched and now covers configuration as well as
destination. `--fresh-start` is the remediation and this entry point has
argparse, so the refusal names the flag with the operator's own `--db` rather
than a `python -c`. It sits ABOVE `--summary-only` deliberately: that mode reads
the database and never the checkpoint, so combining the two would otherwise look
like it had cleared something.

**A DEFECT IN THIS PASS'S OWN WORK, FOUND BY RUNNING RATHER THAN READING.**
Stamping the checkpoint gave `load_checkpoint()` and `save_checkpoint()` — which
had touched no network in their lives — a live Qdrant dependency, because the
default stamp resolves. `main()` hid it completely (it resolves after
`build_bm25_index_from_qdrant()`, so Qdrant is proven live and the stamp is
cached), and it surfaced only when a bucket-A test that runs with no keys was
measured: a caller without an endpoint got `FP_UNRESOLVED` and a refusal about
nothing to do with its checkpoint. Both loaders take `fingerprint=` now, the two
existing tests pass a literal stamp, and the new test asserts the seam with the
resolver made to RAISE — plus the non-degeneracy control that OMITTING the
argument does resolve, without which those checks would pass against a function
that never consults the resolver at all.

**WHAT WAS NOT CHANGED, AND WHY.** The resample pass and the write ledger are
untouched: derived, not assumed — `run_resample` never calls `save_checkpoint`
(its own comment records that resample entries are supplemental), the ledger
records `log_inference` CALLS and no checkpoint code path reaches it, and
`reconcile_writes` reads only the ledger and the database. The manifest's
`schema_version` is deliberately NOT bumped: it is shared with the per-record
version, records did not change shape, and bumping it would make `post_check`
refuse every record already written. The stamp carries its own
`fingerprint_version`, which is the right granularity.

```bash
# The fingerprint pass. Same shape, same directory. No network, no keys, no
# spend, no live Qdrant, no live server, no corpus, no git history, no
# database, and NOT in the collision matrix. It EXECS NOTHING -- every control
# is a different INPUT to a pure function or an attribute rebind inside
# try/finally with the restore asserted BY IDENTITY -- so it needs no
# _EXEC_ALLOWLIST entry. ~2 s.
python tests/test_resume_configuration_fingerprint.py            # 460 (was 446; the pre-migration pass drove the future-era stamp both directions)
```

**TEST COUNTS.** `tests/test_agent_degraded_run_and_reporting.py` **118 → 118**
and `tests/test_ablation_db_isolation.py` **72 → 72** — both were edited (their
checkpoint fabrication now passes an explicit stamp) and neither moved, which is
the point: the edit kept them offline rather than changing what they assert.
`tests/test_package_invariants.py` is unchanged at **247/0/0**. Every other file
reports exactly what it reported before.

### The fingerprint gates the code that renders the prompt (the renderer-digest pass)

**`llm_classifier_prompt_version` IS HAND-MAINTAINED AND
`oncotriage/agent/prompts.py` SAYS SO IN AS MANY WORDS.** The convention is
that a renderer change bumps it; nothing enforced the convention. For the
stored-column consumers that is recoverable — `prompt_sha256` records the bytes
per call, so a version that did not move beside a hash that did is visible in
the record. **A RESUME GATE HAS NO SECOND READING.** So an edit to
`_create_patient_summary`, to a temporal helper or to the stage extractor that
forgot the bump changed every rendered prompt while `run_fingerprint.compare()`
answered FP_MATCH, and the resumed batch, ablation or evaluation run mixed two
eras into one artifact. `oncotriage/run_fingerprint.py` gates a sixth field,
**`llm_classifier_renderer_digest`**, derived from source rather than declared
by a person.

**FREE, AND THE PIPELINE PATH IS UNTOUCHED BY CONSTRUCTION RATHER THAN BY
ASSERTION.** No network, no model call, no spend. `python fixture_replay.py` is
**12/12 clean, exit 0, no recapture** — and the stronger statement is the diff:
**not one of the five hashed modules is modified by this pass**, `oncotriage/
fixtures/` contains no reference to `run_fingerprint` or to either checkpoint
writer (grepped, not assumed), and the production `inferences.db` sha256 is
unchanged. The renderer digest is a fact *about* `patient.py`; nothing in it is
a fact *in* `patient.py`.

**THE ONE BEHAVIOUR CHANGE, STATED AS ONE.** `FINGERPRINT_VERSION` is **2**, so
**every artifact stamped at 1 answers FP_VERSION until an operator clears it
once** — the checkpoint, the `--fresh-start`, or a new `--output-dir` (the
evaluation harness additionally takes `--allow-environment-change`, which
RECORDS the new era instead of discarding the old one). That is the constant's
designed semantics for a shape change, not a defect: a version-1 stamp records
five facts and this version gates six, so comparing the sixth would put
`<not recorded>` against a live digest and report a renderer change that may
never have happened — a true refusal for a false reason. **The refusal now says
so**, in a clause printed for FP_VERSION and only for it, because it is the one
outcome whose cause may be nothing at all.

**WHAT SHAPES RENDERED TEXT, ENUMERATED BEFORE A MECHANISM WAS CHOSEN.** A
static closure from `patient._create_patient_summary` and
`prompts.render_system_prompt` over every module-level name each reaches,
transitively, reaches **exactly seven** package modules and nothing else:
`agent/patient.py`, `agent/prompts.py`, `constants.py`, `extraction/stage.py`,
`utils.py` — and `config.py` and `agent/deps.py`. Beyond the closure sit the
objects `deps` hands back (the cancer code registry, the oncology lab registry,
the MeSH filter) with their DATA, and `python-dateutil`, whose `relativedelta`
does the arithmetic behind every rendered interval.

| | |
|---|---|
| `RENDERER_MODULES` (hashed) | the five above |
| `RENDERER_MODULES_EXCLUDED` (reached, argued, **not** hashed) | `config.py` — its two render-relevant facts are ALREADY gated as fields of their own, and hashing it would make this a de-facto gate on every tunable, which the module explicitly declines to build. Its one other reachable value, `STALE_LAB_AGE_DAYS`, was **checked rather than assumed**: since 1.8.0 it keys a census counter and decides no character of output. `agent/deps.py` — the SEAM, whose contract is that it returns an object which may be an override, so a hash of the resolver describes neither the default nor what was installed |
| outside the closure entirely, named in `RENDERER_COVERAGE` | the registries' code **and data** (the four MeSH JSON lookups and the `icd10-cm` release are outside the repository and could not be hashed at any granularity), and `python-dateutil` |

**THE ROUND TRIP OVER THE MODULE SET IS CLOSED, WHICH IS WHAT A HAND-WRITTEN
LIST CANNOT DO ON ITS OWN.** `tests/test_resume_configuration_fingerprint.py`
section 1b re-derives the closure and requires every module it reaches to be in
one tuple or the other — so a helper moved to a new module fails with a name
rather than silently escaping the digest — **and requires every hashed module to
be one the closure reaches**, so a module in the tuple that nothing renders
through is a digest that refuses for no reason. The closure records an excluded
module by name from the import statement and **never descends into it**, which
is what keeps `oncotriage/config.py` — a file one of the collision matrix's two
writers rewrites in place — unopened, and this test out of the matrix.

**AST-NORMALIZED WITH DOCSTRINGS STRIPPED, NOT RAW BYTES, AND THE ARGUMENT IS
ABOUT WHICH OVER-REFUSAL IS TOLERABLE.** A comment cannot change rendered text,
and this project writes its arguments AT the code — a raw-byte digest would
refuse every resume for every documentation pass, and **a gate that refuses for
reasons the operator knows are spurious is a gate the operator learns to clear
without reading**. Two modules with the same `ast.unparse` output are
behaviourally identical, so what is excluded is exactly what provably cannot
move a character. Docstring stripping rests on a premise that is **asserted
rather than assumed**: no hashed module reads a `__doc__`, checked by AST over
the set. **The stated cost:** `ast.unparse` is the interpreter's, so a resume
across two Python versions refuses with the source unchanged — over-refusal in a
case that is arguably real coverage, named here rather than discovered.

**THE GRANULARITY IS THE MODULE, AND THAT IS THE WHOLE DESIGN.** A
per-definition closure would hash exactly the render path, and its failure mode
is **silent under-coverage**: a bug in the walker drops a helper and nothing
says so. A module hash is a strict SUPERSET, so its failure mode is
over-refusal — an edit to `utils.get_model_cost` refuses a resume it did not
need to. A refusal costs one deliberate clear; an artifact holding two eras
costs every number computed over it.

**MECHANISM (b), THE PROBE RENDER, WAS ANALYZED AND REJECTED, and three of the
four reasons are not about cost.** Hashing the rendered output of a fabricated
probe patient moves exactly when behaviour moves and would cover the registry
data this does not. But it would (i) make the resume gate load the ICD-10-CM
release and read the four MeSH JSON files, so a gate would stop being
computable on a machine `ONCOTRIAGE_ALLOW_DEGRADED_REGISTRIES` exists for, (ii)
**pollute `PROCEDURE_RENDER_COUNTS`, `TEMPORAL_RENDER_COUNTS` and
`LAB_UNIT_DEGRADATIONS`** — census counters reported at run end — with a render
no patient asked for, (iii) put the deps seam and the registries inside a module
whose docstring promises that importing it does nothing, and (iv) make the
probe patient a **hand-maintained** artifact that rots exactly the way
`PROMPT_VERSION` does, which is the trust this pass exists to remove. Stop
condition 9 applies and (a) is what shipped.

**THE BEFORE ARM WAS RUN, NOT REASONED ABOUT, AND IT IS THE WHOLE POINT.** In a
copied tree with `PYTHONPATH` pointed at it and a realpath preflight asserting
the COPY is what imports, the identical one-character renderer edit
(`"\nAllergies:\n"` → `"\nallergies:\n"` in `_create_patient_summary`) with no
`PROMPT_VERSION` bump, against **`git show HEAD:oncotriage/run_fingerprint.py`**
— the five-field gate as it shipped:

| | outcome | differing gated fields |
|---|---|---|
| the pre-pass five-field gate | **`match`** — the resume proceeds and the artifact holds two eras | `[]` |
| this pass's six-field gate | **`configuration_changed`**, naming the field | `['llm_classifier_renderer_digest']` |

`llm_classifier_prompt_version` reads `1.9.0` in all four cells.

**THE GAP IS DEMONSTRATED IN THE COMMITTED TEST TOO, NOT ONLY OUT OF BAND.** Section 1b copies the five hashed
modules to a scratch directory, points `_package_dir()` at the copy — first
proving the untouched copy reproduces the digest **exactly**, so the change
cannot be the copy rather than the edit — and then makes a ONE-CHARACTER edit
in the copied renderer (`"\nProcedures:\n"` → `"\nprocedures:\n"`, with the
marker's occurrence count pinned so an edit that matched nothing cannot look
like a gate that failed to fire). The digest moves; **`llm_classifier_prompt_
version` does not**; the list of gated fields that differ is exactly
`["llm_classifier_renderer_digest"]`; `compare()` answers FP_CHANGED naming it;
`renderer_module_digests()` says WHICH module moved and **only that one**; and a
comment-only edit to the same file moves nothing. Every hashed module's sha256
in the repository is compared before and after.

**THREE DEFECTS WERE FOUND BY RUNNING, AND TWO OF THEM ARE IN CODE THIS PASS DID
NOT WRITE.**

- **`is_resolved` READ A MISSING FIELD AS ESTABLISHED.** It was
  `fingerprint.get(f) != UNKNOWN`; `None` is not `UNKNOWN`, so a stamp missing a
  gated field reported RESOLVED, `disagreements()` then compared `NOT_RECORDED`
  with `NOT_RECORDED`, found them equal, and `compare()` answered **FP_MATCH** —
  a hand-built stamp missing the very field a version bump added would have been
  reported as AGREEING with a run that has it. The version gate catches that
  particular case first, **and a guard that depends on another guard running
  first is not a guard**. Now `get(f, UNKNOWN)`.
- **`compare()`'s FP_MATCH DETAIL COULD RAISE WHILE FORMATTING A SUCCESS.** It
  indexed `current_fp['...']` directly, so the same missing-field stamp produced
  a `KeyError` out of an f-string on the path that PERMITS a resume. That is the
  defect `disagreements()`' own docstring warns about, one branch over. Every
  read is a `.get` now. Surfaced by two existing tests, not by reading.
- **THE TWO CONSUMER BANNERS ENUMERATED THE FIELD LIST.**
  `batch/runner.py:main` and `ablation/study.py:main` each hand-wrote the same
  sentence `compare()` builds, so adding a gated field left both naming five of
  six — an operator who then met a refusal about the renderer digest had never
  been shown the value it moved from. `run_fingerprint.summary(fingerprint)` is
  that sentence's one owner and all three call it. **This is the answer to "no
  consumer should need to change": none needed to for CORRECTNESS** — `run_
  harness.build_environment` spreads `dict(fingerprint)` and both checkpoint
  writers store the stamp whole — **and two needed to for HONESTY.**

**THE THREE PERSISTERS WERE ENUMERATED BEFORE THE VERSION WAS BUMPED** (stop
condition 10), by grepping every `run_fingerprint.current()` call and every
serialization of a `fingerprint` or `environment` key in the tree:
`batch/runner.py:save_checkpoint` → `batch_runner_checkpoint.json`,
`ablation/study.py:save_ablation_checkpoint` → `<db>_checkpoint.json`, and
`evaluation/run_harness.py:build_environment` → `manifest["environment"]` and
each `environment_history[].environment`. **`oncotriage/fixtures/` and
`oncotriage/evaluation/ragas_harness.py` carry `environment` blocks of their
own that have nothing to do with this module** — checked, not assumed — and
`evaluation/rater.py` reads `manifest["environment"]` without enumerating a
field of it. No writer is outside this pass's reach.

**NOTHING ON DISK WAS WRITTEN.** The v1 refusal happens at the NEXT resume, not
now: the production `inferences.db`, `08- Checkpoint/batch_runner_checkpoint.
json`, `batch_runner_results.json`, `04- Results/02- Ablation/ablation_results.
db` and all 27 evaluation-run `manifest.json` files were sha256'd before and
after and are byte-identical.

**TWELVE REVERTS, TWELVE CAUGHT**, each applied to a `copytree`'d copy with
`PYTHONPATH` pointed at it, a realpath preflight asserting the COPY is what
imports, and `PYTHONDONTWRITEBYTECODE=1` set; every plant is asserted to have an
exact occurrence count, so a plant that matched nothing is a named
`PLANT-FAILED` rather than a revert reported as MISSED. The four repository
files this pass touches are sha256-compared before and after the harness.

**FOUR DEFECTS IN THIS PASS'S OWN TEST CODE, THREE OF THEM FOUND BY THE FIRST
RUN OF THE REVERT HARNESS AND NONE BY READING.**

- **TWO REVERTS ABORTED THE FILE INSTEAD OF FAILING IT.** R2 (the digest absent
  from the stamp) hit a bare `stamp["llm_classifier_renderer_digest"]` inside a
  `check(...)` argument list; R6 (an unreadable module raising instead of
  degrading) hit a bare `_fp.current()`. Both raised while the argument was
  being EVALUATED, so the run printed one traceback where it owed a summary and
  twenty-two recorded failures. **That is the ninth time this project has
  shipped that shape.** `at(stamp, key)` and `stamp_now()` are the fix; the same
  two reverts now report **22** and **6** failing checks and run to the summary.
- **R7 WAS MISSED ENTIRELY**, and that is what found the `is_resolved` defect
  above: the hardening had no check behind it, so reverting it changed nothing.
  Section (f2) is what was missing, and it now reports 3 failures on that
  revert.
- **THE ALGORITHM-TAG CHECK ENDED `... or True`** — a tautology that could not
  fail. Two checks replace it: recompute the digest with the tag perturbed and
  with the paths perturbed, and require both to differ. **A check with an `or
  True` in it is not a weak check, it is not a check.**
- **AND ONE BAD PLANT, recorded because a bad plant is worse than no plant.**
  R10's first version cut the words after "version bump" while the phrase the
  check searches for, "first contact", sits earlier in the same string — so it
  reported MISSED against a check that works. The rule pass 20f-1 wrote down
  again: a revert reporting MISSED can mean the check is weak **or** that the
  revert never took effect, and those are not the same finding.

**FOUR TEST FILES HAD ENUMERATED LITERAL STAMPS AND ALL FOUR ARE NOW DERIVED
FROM `FINGERPRINT_FIELDS`.** `tests/test_resume_configuration_fingerprint.py`
(two of them), `tests/test_ablation_db_isolation.py` and
`tests/test_agent_degraded_run_and_reporting.py` each hand-wrote a six-key
offline stamp whose VALUES are irrelevant — and a stamp short of a newly gated
field is FP_UNRESOLVED, so the bump made them fail for a reason that had nothing
to do with what they assert. Their keys come from the tuple now, so the next
bump costs them nothing.

### A run is a row now, not a gap between timestamps (the run-identity pass)

**`inferences` AND `trial_matches` ARE PER-PATIENT RECORDS AND NOTHING CARRIED
THE CAMPAIGN.** "Which rows belong to one batch run" was recovered by looking
for gaps between consecutive `timestamp` values, which is wrong in four ways
and silent in all of them: a RESUMED run reads as two campaigns (the gap is the
interruption); two campaigns started back to back read as one; an API row
written by `17- FastAPI Server.py` during a batch run is indistinguishable from
a batch row, because both land in the same file; and no gap between timestamps
says anything about the CONFIGURATION, which is what a run-level number has to
be attributed to.

**`runs` IS THE THING TO ATTACH TO**, created in `initialize_database` on the
existing `CREATE TABLE IF NOT EXISTS` footing, and `inferences.run_id` is an
additive `INTEGER` through `INFERENCE_COLUMN_ADDITIONS`. The batch runner opens
one row before its first patient and finalizes it after its last.

| column | holds |
|---|---|
| `id` / `started_at` / `finished_at` / `status` | the run. `finished_at` is NULLABLE and that is the whole crashed-run shape |
| `invocation_source` | which entry point ran. **REQUIRED, no default** (`empty_database(db_path, flag)`'s precedent), and the one place this module declines a closed vocabulary — a status is a fact it produces, a caller is not |
| `fingerprint_version` + the six `FINGERPRINT_FIELDS` | the configuration stamp, **as seven individual columns and not a JSON blob** |

**THE STAMP COLUMNS ARE RESTATED IN THE STORAGE LAYER AND A TEST CLOSES THE
ROUND TRIP, because the import is not available.** `oncotriage.run_fingerprint`
imports `agent.prompts` AND `agent.readiness`, and readiness builds a Qdrant
client; `oncotriage.tracking` imports `agent.prompts`. A storage module
importing either would put the agent — and a network probe's import graph —
behind `import oncotriage.storage.database_logger`, which is the edge pass
20c-2c moved `_resolve_primary_cancer` out of that module to remove. So
`RUN_FINGERPRINT_COLUMNS` and `RUN_RECORD_TERMINAL_STATUSES` are declared there
and `tests/test_storage_run_identity.py` requires them to equal
`("fingerprint_version",) + FINGERPRINT_FIELDS` and `tracking.RUN_STATUSES`
exactly. A test may import all three because a test is in nobody's import graph.

**THE STATUSES ARE `tracking.RUN_STATUSES` PLUS `RUNNING`.** That tuple excludes
`RUNNING` on the argument written beside it — passing it to `end_run` would
leave a finished run looking live forever — and that argument is about the END
of a run. This table records the start of one as well, so it needs the value
that tuple omits. `FINISHED` / `FAILED` on the success path is the MAIN PASS's
verdict, the same fact the checkpoint decision and `tracking.end_run` are made
on; **`KILLED` is the crash handler's and is a different finding** — "ran to
the end and some patients errored" is not "the process did not get to the end",
and only the second has patients that were never attempted.

**`collection_points` NULLs AN UNRESOLVED COUNT, AND THAT IS THE `ecog_date`
TRAP ONE COLUMN TYPE OVER.** `run_fingerprint` degrades an unresolvable field to
the STRING `"unknown"`; the five TEXT columns store it verbatim, which is right
for them. SQLite keeps a non-numeric string as TEXT whatever the declared
affinity and orders every TEXT value ABOVE every INTEGER — so
`WHERE collection_points > 1000` would return exactly the rows where the count
could not be established and `ORDER BY ... DESC` would rank them as the largest
collections there are. A `bool` is excluded from the int test too, because
`isinstance(True, int)` is True and a `collection_points` of 1 that was really a
`True` is a number nobody measured. **Nothing is lost:**
`fingerprint_version IS NULL` is "no stamp was recorded" and
`fingerprint_version IS NOT NULL AND collection_points IS NULL` is "a stamp was
recorded and the count was not established", with `qdrant_collection` reading
`'unknown'` when even the name did not resolve.

**NULL `run_id` IS A VALUE, NOT A LEGACY.** Three callers write it on purpose:
`17- FastAPI Server.py` (a request is not a campaign — it has no start, no end
and no configuration stamp, and a run per POST would put one `runs` row in the
table for every request), every direct `log_inference`, and every historical row.
So `run_id IS NULL` means "not part of a recorded batch run", never "the run is
unknown", and the campaign query is a JOIN rather than a timestamp window.

**THERE IS NO MODULE-LEVEL "CURRENT RUN", AND THAT ABSENCE IS THE MECHANISM.**
The id is a LOCAL of `main()`, threaded into `run_batch`, `run_resample`,
`process_patient` and `log_inference` as an argument. `clear_write_ledger()` and
`run_fingerprint.clear_cache()` at the top of `main()` exist because their state
IS module-level and both are one forgotten line away from describing the wrong
run; threading the id is the version of that rule that cannot be forgotten,
because there is nothing to clear. A second `main()` in one process therefore
creates a new row by construction. The test asserts it both ways — two
`start_run_record` calls give two ids and two rows, and an AST walk requires the
name to be a local, requires `main()` to declare no `global` at all, and scans
both modules for a module-level current-run global.

**`start_run_record` RAISES AND `finalize_run_record` NEVER DOES**, and the line
between them is item 11a's. Creation is before the first billed call, where a
failure costs nothing and where continuing would produce a whole campaign of
rows that cannot be attributed — `tracking.start_run` raises at the same point
in the same `main()` and is the precedent. Finalization runs after one live
Stage 5 call per patient has been paid for, so it counts into
`RUN_RECORD_FAILURES` (the twenty-second counter in `oncotriage/degradation.py`)
and returns False. "Never raises" means what it means everywhere else in that
module: `except Exception`, so `KeyboardInterrupt` and `MemoryError` still
escape exactly as they escape `_write_inference_row` — a finalizer that
swallowed a Ctrl-C would leave an operator holding a key down against a process
that will not stop. **There is no `start:` key in that counter and the absence
is not an omission.**

**THE ROW COUNT IS READ.** `UPDATE ... WHERE id = ?` against an id that is not
there SUCCEEDS and updates nothing; SQLite reports no error. A finalizer that
did not check `rowcount` would report success for a run row that was never
written — the "reported success, wrote nothing" shape the write-durability pass
removed one function down. An unrecognised status is replaced by `FAILED` and
counted, never by `FINISHED`, which is `tracking.end_run`'s rule adopted
verbatim; `RUNNING` is unrecognised HERE even though it is a member of
`RUN_RECORD_STATUSES`.

**THE SUCCESS-PATH FINALIZE IS THE LAST STATEMENT OF `main()`'s `try`, AND THE
POSITION IS A CORRECTNESS PROPERTY.** Every other statement in that block can
raise — `tracking_metrics` walks the results list, `_results_path` resolves a
path, `report_lines` formats a snapshot — and the handler finalizes to `KILLED`.
With the finalize anywhere ABOVE them a raise in between would OVERWRITE a
FINISHED row with KILLED and report a completed campaign as a crashed one. Being
last makes the two paths mutually exclusive by construction, which is stronger
than a "have I finalized yet" flag somebody has to remember to set. The cost is
stated: `finished_at` now includes the seconds the tracking store spent
attaching artifacts. **`tests/test_storage_run_identity.py` pins the ordering
and carries a control that reorders an AST copy.**

**`tracking.start_run` IS WRAPPED, because the run row is already open by that
line.** It raises when tracking is unavailable — which is its design — and
between the two there was no handler, so an unwrapped raise would leave a `runs`
row at RUNNING with a NULL `finished_at` forever, describing a campaign that
never started. **Reordering does not fix it and makes it worse:** MLflow's
atexit hook closes an open run as FINISHED, so a tracking run orphaned by a
failure below it is indexed as a campaign that COMPLETED. Moving it into the
main `try` does not fix it either — that handler calls `tracking.end_run`, which
with no active run counts `end_run:NoActiveRun`, a degradation that did not
happen reported by the code meant to report the one that did.

**THE FOREIGN KEY IS UNENFORCED, LIKE `trial_matches.inference_id`, AND FOUR
REASONS DECIDED IT** (argued at the `CREATE TABLE`): `PRAGMA foreign_keys` is
per CONNECTION and this module opens only some of them — `storage/queries.py`,
`storage/maintenance.py`, `monitoring/drift.py`, `dashboard/data.py`,
`evaluation/sampling.py` and every test open their own, so a constraint honoured
by one writer and ignored by six other openers of the same file reads like an
invariant and is not one; it would BREAK `empty_database`, which `DELETE FROM`s
every table in `sqlite_master` order, parents first, and would raise having
deleted nothing; a violation arrives as `sqlite3.IntegrityError`, which
`_is_retryable` classes as TERMINAL, so a row that today lands with a dangling
id would instead be GIVEN UP ON; and NULL is legitimate here while the only
non-NULL values are written by the process that created the row moments earlier.

**`oncotriage/evaluation/sampling.py` COPIES THE `runs` TABLE**, on the sentence
`COPIED_TABLES` already carried — "a sample database that silently lacked the
table would not open in a tool built against the production schema". Unlike
`drift_metrics` it is POPULATED, with the run rows the copied inferences
actually reference and no others; copying every run of the whole database would
describe campaigns the sample contains no patients from. A pre-migration source
degrades silently and correctly: the schema query is `name IN (...)`, so it
yields one row fewer and the row copy finds no ids.

**`inference_run_id` IS A NEW LOGGABLE FIELD AND IT IS DELIBERATELY NOT
`run_id`.** `oncotriage/observability.py` already records that `run_id` means
the ABLATION database's integer run id and that `tracking_run_id` was named
separately to avoid conflating two keys under one field name. Three id spaces
exist now and each has its own field.

**TWO PINNED EXPECTATIONS IN THE EXISTING SUITE MOVED, both argued in place.**
`tests/test_package_invariants.py` section 5e's `locks_stripped` goes **3 → 5**
— `start_run_record` and `finalize_run_record` take `_WRITE_LOCK` too, because
that module's invariant is "every database statement in this file is issued
under `_WRITE_LOCK`" and an invariant with an exception is a convention; the
assertion is a NON-DEGENERACY probe whose job is to say the control really did
strip something, so it has to move whenever a site is added. **And
`tests/test_storage_write_durability.py` section 5c stopped RETYPING that
number**: it was the literal `3` in a second file, so this pass had to change
one fact in two places and the second failed as a mystery about a lock it had
nothing to do with. It reads the expectation out of
`test_package_invariants.py` by AST now, with its own probe that the number was
actually found. `tests/test_agent_bedrock_adapter.py`'s table-set pin gains
`runs` and stays EXACT, because exact is what makes it fail when a lookup table
IS introduced under any name.

**`tests/test_tracking_mlflow_index.py`'s `_guard_shape` SELECTS BY WHAT THE
HANDLER CALLS, not by being the first one.** It took the first
`except BaseException` in the file, which was the tracking guard when that check
was written and stopped being it here; a positional selector reported the new
`tracking.start_run` wrapper as a tracking guard that closes no run. Its count
is unchanged at **99**.

**VERIFIED BY RUNNING.** Bucket A **51/51**, the serial runner **5/5** with
`oncotriage/config.py` and `oncotriage/registries/cancer_code_registry.py`
confirmed restored, `tests/test_package_invariants.py` **260/0/0**, every
bucket C/E file on this machine green (`test_storage_write_durability` 100,
`test_storage_ecog_logging` 155, `test_indexer_admission_filters` 403,
`test_mcp_server_stdio_contract` 142, and the rest at their documented counts),
`python fixture_replay.py` **12/12 clean, exit 0, with no recapture**, and the
production `inferences.db` sha256 **unchanged** — `ab1403e3…`, 90,185,728 bytes,
before and after. **No money was spent and no migration was run against the
production database**: the `runs` table and `inferences.run_id` appear there on
the next run that opens it, which is what the additive mechanism is for.

**WHAT WAS NOT DONE, STATED.** `main()` itself is not driven end to end — it
builds a BM25 index from a live Qdrant, compiles the graph and makes one billed
Stage 5 call per patient — so the run lifecycle is proved by driving what
`main()` does once per invocation plus an AST walk over `main()` itself, and
`run_batch` / `run_resample` are driven for real with a recording stand-in for
`process_patient`. The stand-in returns `status="error"` deliberately: a
"success" entry makes `_on_done` call `save_checkpoint()`, which with no
fingerprint argument resolves `run_fingerprint.current()` — a live Qdrant round
trip, in a file that is bucket A.


### A run's health record survives the process (the health-persistence pass)

**`oncotriage/degradation.py`'s TWENTY-ODD COUNTERS HAD EXACTLY ONE READER —
THE BLOCK `main()` PRINTS WHEN A RUN FINISHES.** So the whole health record of a
campaign lived in one process's memory until it exited, with two consequences: a
campaign that CRASHED printed nothing, and everything its counters held about
the 19,000 patients it did complete died with it; and nothing outside the
process could ask a LIVE run how it was going. `run_metrics` is that record on
disk, refreshed as the run proceeds.

**NARROW, ON `drift_metrics`' PRECEDENT** — `(run_id, category, name, value,
written_at)` — because one column per counter would mean a schema migration
every time a counter joins the registry, which is the trade `drift_metrics`
already declined. `value` is INTEGER, not REAL: every value is a `Counter` total
or a count of counters, and REAL would render `412.0` and invite an average over
a column of event counts.

**COUNTER NAMES AND TOTALS ONLY, AND IT IS ENFORCED RATHER THAN CONVENED.**
`snapshot()`'s KEYS carry third-party and clinical text — SEX_UNKNOWN_KEPT is
keyed by the patient's recorded sex, M_CATEGORY_UNREADABLE by a capped
observation display — and this is a DURABLE, run-keyed table, which is what
LOGGABLE_FIELDS exists to keep that text out of. `_run_metric_rows` refuses any
mapping that is not `{name: int}` and identifies a name by **`str.isidentifier()`**:
counter names are module-level Python variable names by construction and no key
this project produces is one. **The whole flush is refused on any bad member**,
not the member skipped — a name that is not an identifier means the mapping did
not come from `totals()`, so the rest of it is suspect too, and a partial write
reads as a complete one.

**DELETE-AND-INSERT, NOT AN UPSERT, and the two are not equivalent.** An upsert
keyed on (run_id, name) replaces the names the new flush carries and leaves
behind any counter that has LEFT the set — which never happens while counters
are cumulative, and happens the moment anything clears one, leaving a stale
non-zero total presented as current. One transaction also means a reader sees
the previous flush or this one, never a mixture. And there is no UNIQUE
constraint anywhere in this schema; adding the first one to enable `ON CONFLICT`
would make `IntegrityError` reachable on a write path where `_is_retryable`
classes it TERMINAL.

**THE META ROW IS WHAT MAKES SILENCE READABLE.** `totals()` drops every zero
counter, so a clean run contributes no `degradation` rows — which is also what a
run whose flushing was never wired contributes. `meta/counters_registered` and
`meta/counters_nonzero` separate the three states in one query: no rows at all is
"nothing ever flushed", `counters_nonzero = 0` is a MEASUREMENT of health.

**IT IS HOSTED IN `_on_done` AND DELIBERATELY NOT ON `save_checkpoint()`.** That
call is the obvious per-patient completion point and it sits inside
`if entry["status"] == "success":` — so a pass in which every patient ERRORED
would flush nothing, and errors are exactly when REFUSALS_OBSERVED,
MALFORMED_EVALUATION_ENTRIES and INFERENCE_WRITE_FAILURES move. **EVERY PATIENT,
MEASURED**: 0.492 ms per flush with the `run_id` index and a 50,000-row history,
1.212 ms without it, against a ~68-second per-patient median. A cadence knob was
rejected — its only safe value is 1.

**THE FINAL FLUSH USES `main()`'s ONE SNAPSHOT**, the same object
`degradation.log_summary` and `print_summary` are handed, so the rows, the
structured event and the printed block describe one instant. The crash handler
flushes too, BEFORE `finalize_run_record(..., "KILLED")`, so the record is
current at the moment the run is marked killed.

**`snapshot()` HAD NEVER BEEN CALLED WHILE A WORKER WAS WRITING, and this pass
is what made that happen.** Its dict comprehension over `counter.items()`
executes Python bytecode per item, so a key inserted by another thread in that
window raises `RuntimeError: dictionary changed size during iteration`. It is
`dict.copy(counter)` now — one call into `PyDict_Copy`, no Python bytecode
inside it — with the zero filter applied to the private copy, a bounded retry
kept as a second line of defence and `SNAPSHOT_CONTENTION` registered so a retry
or an abandonment is on the report rather than silent. **Found by running**: the
first implementation abandoned a counter on the test's first run.

**WHAT THE LOCK ACTUALLY BUYS, MEASURED RATHER THAN ARGUED.** Stripping
`_WRITE_LOCK` from the flush and driving MAX_WORKERS threads through it loses
NOTHING — with sqlite3's default isolation level the DELETE opens a transaction
the commit finishes, and SQLite's own file locking refuses a second write
transaction meanwhile. That is section 5e's honest finding about the
steady-state insert path, again. The lock is taken anyway: the module's
invariant is "every database statement in this file is under `_WRITE_LOCK`", it
converts busy-waiting into an in-process queue, and it is what makes
`isolation_level=None` — one keyword, a plausible future edit — a safe change
instead of a silently destructive one. **`_note_run_metric_shape` takes its own
`_ANNOUNCE_LOCK`**, because it guards a set rather than a statement and
borrowing the write lock would make section 5e's count mean two things at once.
That count moves **5 → 6**; `tests/test_storage_write_durability.py` reads it
out of that file by AST, so it needed no edit.

**FRESHNESS IS WEAKER THAN ATOMICITY AND IS STATED AS SUCH.** The snapshot is
taken in `flush_health`, outside the writer's lock, so two workers can snapshot
at 5 and 6 events and land in the other order — the table reads 5 until the next
patient finishes. Bounded by one patient, self-healing, and `written_at` is what
a reader consults. Not a corruption, and not fixed by holding a database lock
across a registry read every worker is writing to.

**`RUN_METRICS_FLUSH_FAILURES` IS ON THE RUN-END REPORT AND CANNOT BE ON THE
TABLE** — a row recording that the flush failed could only be written by the
flush that just failed. It is always one flush behind itself, and the last
flush's failure never reaches the table at all; that is inherent, and the
console block is the authority. `flush_health` guards the registry READ too and
counts into the same counter under `flush:registry_read:{Type}`: the fact an
operator acts on is "this run's health record did not land", and splitting it in
two by which half failed would put one event in two places on the report.

**THE SAMPLE DATABASE GETS THE SCHEMA AND NOT THE ROWS** — the `drift_metrics`
treatment, and a decision. A `runs` row holds a CONFIGURATION, equally true of
any subset of a campaign's patients; a `run_metrics` row holds a COUNT
aggregated over a population a 30-patient extract does not contain, and the
narrow shape has no column that could carry the denominator to contradict
"this sample had 412 age-unit assumptions".

**VERIFIED BY RUNNING.** `tests/test_storage_run_metrics_flush.py` is **123
checks, bucket A, ~12 s**, with **FIFTEEN REVERTS, FIFTEEN CAUGHT** (each
applied to a `copytree`'d copy with a realpath preflight and
`PYTHONDONTWRITEBYTECODE=1`; every plant asserted to have an exact occurrence
count, so a plant that matched nothing is a named PLANT-FAILED). **Three of the
fifteen were MISSED on the first run and each was a real gap in the checks
rather than a weak revert** — the `isidentifier` guard had no test that could
see it removed (`totals()` produces identifier names, so no assertion about the
TABLE can), the lock had no behavioural subject (see above; it is pinned
structurally now), and the racy comprehension was recovered by the retry often
enough that the thread pool did not catch it (a counter that mutates itself
while its `items()` view is walked catches it deterministically).
Bucket A **52/52**, the serial runner **5/5** with `oncotriage/config.py` and
`oncotriage/registries/cancer_code_registry.py` confirmed restored,
`tests/test_package_invariants.py` **260/0/0**, `python fixture_replay.py`
**12/12 clean, exit 0, with no recapture**, and the production `inferences.db`
sha256 **unchanged** — `ab1403e3…`, 90,185,728 bytes, before and after. **No
money was spent and no migration was run against the production database**: the
`run_metrics` table appears there on the next run that opens it, which is what
`CREATE TABLE IF NOT EXISTS` in the shared initialize path is for.

**ONE PRE-EXISTING DEFECT REPORTED AND NOT FIXED.**
`finalize_run_record`'s docstring says `KeyboardInterrupt` and `MemoryError` are
"not `Exception` subclasses" and therefore escape. `issubclass(MemoryError,
Exception)` is **True**, so that handler catches it; the same sentence appears at
`_write_inference_row`. Measured, corrected in the new function's own docstring,
and left as written in the two this pass does not otherwise touch.


### Three Stage 5 corrections became queryable (the provenance-persistence pass)

**THE NORMALIZER IN `oncotriage/agent/evaluation.py` MAKES THREE CORRECTIONS AND
TWO OF THEM REACHED NO STORED BYTE.** Traced before anything was edited:

| artifact | where it died |
|---|---|
| `not_evaluable_reason` | stamped on the ENTRY and **dropped at the write** — the `trial_matches` INSERT named nineteen columns and none of them was it, and `criterion_details` json.dumps exactly `"inclusion"` and `"exclusion"`. The field was present on the dict at the line that wrote the row, and `database_logger.py` already said so in a correction to an earlier comment |
| `verdict_normalizations` | a local list read by **one log line** and discarded. Not on the entry, not in the node's return, not a state channel, not a column |
| `label_remaps` | **half of it survived** — `len(label_remaps)` is `cross_vocab_remaps`, a count of remap EVENTS for the whole run. Which trial each belonged to, how many TRIALS carried one, and what each row's status was BEFORE the rewrite were lost; `_normalize_arm` rewrites `c["status"]` **in place** |

**THE DO-NOTHING-AND-QUERY-THE-JSON OPTION WAS BEATEN ON AVAILABILITY, NOT
ERGONOMICS.** B reaches no column and no JSON. C's original status is overwritten
before `criterion_details` is built, so that blob is not even a lossy record of
it. A is in no JSON either — `assessment` is a PROXY for six of the reasons
through six fixed sentences, which means recovering a machine field by `LIKE`
over English prose, breaking on any rewording, blind to the ABSENT case, and
wrong for every row written before PROMPT_VERSION 1.5.0 (which carries the
model's free-written draft in that column instead).

**SEVEN COLUMNS AND ONE JSON KEY.** `trial_matches` gains
`not_evaluable_reason`, `verdict_source`, `verdict_original_label`,
`verdict_original_type` and `criterion_remaps`; `inferences` gains
`verdict_normalizations` and `remapped_trials`; and a criterion row Stage 5
relabelled now carries **`remapped_from_status`** inside the existing
`criterion_details`, which still json.dumps exactly two keys.

**THREE PLACES THE SHIPPED DESIGN DIFFERS FROM THE OBVIOUS ONE, each argued at
the code.**

- **`verdict_source` is written on EVERY model-returned row, `'canonical'`
  included.** "NULL when the label was canonical" would conflate "the normalizer
  read this label and it was already canonical" with "no normalizer ran for this
  row". `canonical` is the `hallucinated = 0` of this column, and NULL then
  selects exactly the population `emission_index` / `call_index` select — the
  four `_unevaluable_entry` CONSTRUCTS, which are appended AFTER the normalizer
  loop and never carried a model-written label.
- **A per-trial COUNT, not a flag.** "at least one" is `> 0`, so the flag is
  derivable from the count and not the reverse; and the counts of a run's rows
  **sum to that run's `cross_vocab_remaps`**, an invariant one query checks. It
  is free: `len(label_remaps) - remaps_before`, at the line that already computed
  `remapped_here` from those two numbers.
- **Two run-level scalars and no stored breakdowns.** Per-reason and per-type are
  GROUP BYs over the child table; storing them would mean JSON (which no query
  groups on) or one column per constant. The two that ARE stored are the two a
  JOIN cannot express: `COUNT(*) ... WHERE verdict_source <> 'canonical'` returns
  0 for "measured none", for "these rows predate the columns" and for "no Stage 5
  ran" alike. That is `inferences.hallucinated_trials`' own argument beside
  `trial_matches.hallucinated`, adopted rather than invented.

**THE MODEL CANNOT FORGE ANY OF THE FIVE MARKERS**, and the test asserts it
against the REAL schema rather than assuming it: `agent/response_schema.py` sets
`additionalProperties: False` with a complete `required` list at every level, and
no name is in `TRIAL_FIELDS` or `CRITERION_FIELDS`. Same argument
`not_evaluable_reason` and `TEMPORAL_CONFLICT_FIELD` already carry.

**NO FIXTURE-COMPARED FIELD MOVED, AND THAT IS WHY.**
`build_deterministic_prefix` is a CLOSED enumeration: its `stage5.verdicts[]`
projects ten named keys and the criteria arrays are not among them, so a new
result key and a new match key are invisible to it, and `compose_assessment` —
whose output IS projected — reads only named keys. Proved by AST in the test.
`python fixture_replay.py` is **12/12 clean, exit 0, no recapture**, and the
production `inferences.db` sha256 is unchanged.

**ONE GAP IS STATED RATHER THAN CLOSED, AND THE STOP CONDITION IS WHY.** Stage
5's **Step 2** — a trial the model returned with NO criteria — records its reason
(`trial-level verdict label not recognised`, or `model returned no criteria`)
into the audit list only and does not stamp the entry, so the column is NULL for
that population. `not_evaluable_reason` is one of the ten keys the twelve
fixtures compare, so writing it where it was not written before is a change to a
fixture-compared field and costs a re-capture at live model prices. The
population stays IDENTIFIABLE without prose —
`eligible = 'not_evaluable' AND verdict_source IS NOT NULL AND
not_evaluable_reason IS NULL AND criterion_details = '{"inclusion": [], "exclusion": []}'`,
with `verdict_source` separating its two sub-cases — and the column note says so.

**A PRE-EXISTING DEFECT IN THE NEIGHBOURING COLUMN IS RECORDED AND NOT FIXED.**
`cross_vocab_remaps` is written as `state.get(..., 0)` by `_pipeline_provenance`
and as a literal `0` by `node_no_candidates`, so it reads **0 on runs whose
normalizer never ran**. Narrowing it now changes what an existing column means
for every reader; the two new counters are honest from their first row and the
asymmetry is stated at both ends.

**FOUR QUERIES, ONE PER CAMPAIGN QUESTION, NONE OF THEM PARSING JSON** —
`not_evaluable_reasons`, `verdict_normalization_sources`,
`criterion_remap_incidence`, `run_normalizer_provenance`. Every one groups on a
COALESCE label that NAMES the absence rather than dropping the row, because a
`COUNT(*)` with the NULLs dropped reports a clean audit over a population that
was never audited. `tests/test_storage_query_layer.py` stays at **194** and its
four `trial_matches` rows were widened to carry a measured value on every new
column, so those queries can no longer be satisfied by the all-NULL bucket.

**`tests/test_storage_provenance_persistence.py` — 126 checks, bucket A,
~2.5 s.** Both migrations fresh and pre-migration; the round trip for measured /
measured-zero / absent-key / explicit-None on all seven columns with NULL and 0
shown distinguishable IN SQL; the stamps driven through the real Stage 5 node and
the real `node_finalize` on a StateGraph over the real `TrialMatchState`, with
the reduced-schema control that loses both counters; the failure returns; the sum
invariant; and **five planted controls, all five shown to fire**. Two of them
were rewritten mid-pass because they passed for the wrong reason: a flag-instead-
of-count plant and an events-instead-of-trials plant both agree with the shipped
code on a body where every affected trial carries exactly ONE remap, so both are
now driven on a body with **two remaps on one trial**, where events = 2 and
trials = 1 and the two answers separate.

### The run tables have readers (the run-reader pass)

**`runs` AND `run_metrics` WERE WRITTEN BY TWO PASSES AND READ BY NOTHING.** A
table nobody reads rots: the writer keeps writing it, no consumer ever
contradicts it, and the first person to look discovers that a column has meant
something else since a pass nobody connected to it. Three registered queries and
a tenth dashboard tab are the readers. **Read-only throughout: no schema edit,
no writer edit, and the dashboard's new loaders open the database through a
`mode=ro` URI.**

**THE PRODUCTION DATABASE DOES NOT HAVE THOSE TABLES, AND THAT FACT DECIDED THE
DESIGN.** Measured before anything was written: `inferences.db` holds
`drift_metrics`, `inferences`, `sqlite_sequence`, `trial_matches` and **no
`runs`, no `run_metrics` and no `inferences.run_id`** — they are additive, and
the next writer to open the file adds them. A query naming an absent table
raises `no such table`, and `report()` runs its registry with the first raise
taking the process down. **So registering the first `runs` query without a guard
would have reinstated, exactly, the defect item 38 removed** —
`python "16- Database Query.py"` dying partway with everything after it never
executing.

`Query.requires` is that guard: a tuple of table names, empty for the
forty-three queries written before this pass. `report()` asks
`unavailable(conn)` **once**, before anything runs, **PRINTS** which queries it
is skipping and which tables are absent, and skips them; a report that quietly
covers less than its registry is a report that reads as complete.
`run()` raises **`MissingTableError`** — a `RuntimeError` subclass, on
`UnknownModelPricingError`'s precedent, so a broad `except sqlite3.Error`
cannot eat it — rather than returning an empty frame, because "this database
cannot answer that yet" and "the answer is no rows" are different findings and
an empty frame is the second. A skipped key is **absent from `report()`'s
returned dict**, so a caller indexing it gets a KeyError it can act on rather
than a frame of zeros about runs that were never asked.

**`requires` NAMES TABLES AND `requires_columns` NAMES COLUMNS, AND THE SECOND
IS NOT DERIVABLE FROM THE FIRST.** `inferences.run_id` is additive too, and two
of the three run queries JOIN on it — without it their SQL does not parse, which
is a different case from the ordinary `INFERENCE_COLUMN_ADDITIONS` one every
other query already handles by projecting NULL. "The tables exist, so the column
exists" is true of a database this project wrote, because
`initialize_database` creates all three in one call — **that is a coupling, not
an invariant, and a guard resting on it fails in exactly the shape it was
written for.**

**A CONTROL FOUND A DEFECT IN THIS PASS'S OWN WORK, AND READING DID NOT.** The
first draft declared the column on `run_attribution_coverage` only, under a
comment calling it "the only query that JOINS on an additive column".
`run_summary`'s patient rollup joins on the same column. The section that builds
a database with **both run tables and no `run_id`** is what reported it; the
expectation there is now **derived from the declarations** rather than retyped,
so the same mistake cannot be made silently a second time. The dashboard had the
same defect one layer up and it is fixed with it: that shape is
`RUN_TRACKING_PARTIAL`, not `present`, because reporting it as present sent the
tab down its normal path where two refused queries rendered as "the run tables
hold no rows" — a statement about a pipeline that has not run, made about a
database whose queries could not be asked.

**THREE QUERIES, AND THE JOINS ARE LEFT FOR TWO NAMED REASONS.**
`run_summary` (one row per campaign), `run_degradation_breakdown` (per counter,
**driven from `runs` so a clean run is a row too**) and
`run_attribution_coverage` (the inference-row census). A run with **no
patients** — killed before its first — and a run with **no degradations** are
both real states that an INNER JOIN reports as runs that do not exist. Each
child is **pre-aggregated to one row per `run_id` before it is joined**: a run
with 20 patients and 3 counters in one flat FROM clause produces 60 rows and
`SUM(value)` reports twenty times the events.

**"NO DEGRADATION ROWS" HAS TWO OPPOSITE MEANINGS AND ONE COLUMN SEPARATES
THEM.** `degradation.totals()` drops every zero counter, so a run that degraded
in no way contributes no rows — and so does a run whose flushing was never wired
up, and so does one that died before its first flush. `run_metrics`'
`counters_registered` meta row is what tells them apart, which is the entire
reason `flush_run_metrics` writes it. `health_record` is that reading, as a
closed three-member vocabulary (`measured clean` / `degraded` /
`no health record`) written **ONCE** as `_RUN_HEALTH_CASE_SQL` and interpolated
by both run queries — `_CONSISTENCY_CASE_SQL`'s precedent, so there is no second
copy to forget. `degradation_events` is deliberately **NOT** COALESCE'd to 0;
`patients`, `errored` and `cost_usd` are, because a LEFT JOIN with no match
there means no patient claimed the run, which is a measured zero.

**THE CRASHED SHAPE IS NAMED FOR THE AMBIGUITY IT ACTUALLY IS.** A `RUNNING` row
with a NULL `finished_at` is **either a live campaign or one whose process was
killed** — the schema has no pid, no heartbeat and no lease, so the two are the
same row. Calling it "crashed" would be an invention and calling it "running"
would hide every crash; `finalization` reports it as one state that says so,
with `started_at` beside it, which is what a reader actually uses. A **terminal**
status with a NULL `finished_at` is a separate and unambiguous state:
`finalize_run_record` writes both in one UPDATE, so that row was written by
something else.

**THE `run_metrics` CATEGORY AND META NAMES ARE IMPORTED FROM THE WRITER, NEVER
RETYPED.** `queries.py` imports `RUN_METRIC_CATEGORY_*` and
`RUN_METRIC_META_*` from `oncotriage/storage/database_logger.py`. Written out as
literals they would be the `CROSS_ENCODER_MODEL` shape one layer down: two
copies of one fact, no error when they disagree, and the only symptom a health
panel reporting every run as clean because `WHERE category = 'degredation'`
matches nothing. The edge was checked rather than assumed — `database_logger`
does not import `queries`, so there is no cycle.

**A NULL `run_id` IS A VALUE, NOT A LEGACY, AND THE LABEL SAYS SO.**
`17- FastAPI Server.py` writes one per request **on purpose** (a request is not
a campaign; a `runs` row per POST would put one row in that table per request),
and every row written before the run-identity pass has one too. The database
cannot separate those two populations and `RUN_ATTRIBUTION_NO_RUN` does not
pretend to. What it must not do is read as "the run is unknown", which is a
third thing and is what an unlabelled NULL reads as. `RUN_ATTRIBUTION_DANGLING`
is the third state — a `run_id` with no matching `runs` row, reachable because
that foreign key is **unenforced by design** (argued at the `runs` CREATE TABLE)
— and this census is **the only thing in the project that can report one**.

**THE TENTH TAB DOES NOT HONOUR THE SIDEBAR, AND IT SAYS SO ON SCREEN.**
`oncotriage/dashboard/tabs/run_health.py` is handed `filtered_df` like the other
nine, and uses it for exactly one thing: stating how much of the **current
selection** carries a run id. Every run figure comes from the database
unfiltered, because a run's patient count and cost are properties OF THE RUN and
a filtered subtotal under a total's heading is `print_cost_by_model`'s
"<- A FLOOR, NOT A TOTAL" defect, which item 38 had to fix rather than explain.
The tab **carries no SQL**: every frame comes from the query layer through four
`@st.cache_data(ttl=60)` loaders in `oncotriage/dashboard/data.py`, so its
questions and File 16's cannot drift — the direction the cost tab established
when it stopped carrying its own per-model arithmetic. A run with no health
record is **not plotted at zero** on the run-over-run chart; it is excluded and
the exclusion is counted in prose, because a zero bar would state that nothing
degraded, which is exactly what is not known about it.

**THE FOUR RUN LOADERS OPEN READ-ONLY AND THE THREE ORIGINAL ONES DELIBERATELY
DO NOT.** `sqlite3.connect(path)` on a path that does not exist **CREATES** an
empty database, and this tab's whole subject is "what does this database have" —
a loader that answered by bringing a database into existence would be File 41's
guard-that-creates-its-own-evidence defect. The three original loaders keep
`sqlite3.connect` because changing them is a behaviour change to eight tabs in a
pass that owes one.

**`tests/test_dashboard_run_health.py` — 155 checks, bucket A, ~0.9 s, and it
has NO GOLDEN SNAPSHOT ON PURPOSE.** It follows
`tests/test_dashboard_reproducibility_tab.py` point for point — `AppTest`
driving one module and one function, `initialize_database()` building the
scratch schema, `paths._RESOLVED` as the redirect seam, `sqlite3.connect`
recorded with a **DECOY** control showing the isolation assertion FAILING, an
offline guard that raises and records with a control showing it firing, plants
into copies in a temp directory — **except for the reference**, and that
exception is forced by that file's own stated rule: *a golden file refreshed to
accommodate a change makes whatever the code does correct by definition*. That
file could take a snapshot because it had a BEFORE. This tab is NEW, so a
snapshot recorded on day one is a photograph of whatever the pass happened to
write and would pass forever against a tab reporting a crashed run as finished.
**The reference is the SEED**: every expected value is computed from the rows
inserted, never read back out of the frame under test. It is also therefore not
pinned to a streamlit version's element vocabulary, which is what put the
reproducibility test's bucket-A entry in a state its own note calls "a
repository defect". Six scenarios (four runs / all-clean / tables-present-no-rows
/ no-tables / partial / dangling) and **eight planted defects, eight caught**,
each paired with the shipped module's opposite answer as its control.

**ONE DEFECT IN THIS PASS'S OWN CODE WAS FOUND BY RUNNING, NOT BY READING**, and
it was found by an existing check rather than a new one:
`tests/test_package_invariants.py` **2h** reported
`RUN_TRACKING_NO_DATABASE` imported into the tab and never read. The cause was a
bare `else:` catch-all handling that state, which would have rendered an
unrecognised availability value as "the file is not there" and sent an operator
to look for a file sitting where it should be. Every member of the closed
vocabulary is named now and the `else` reports the loader defect it actually is.

**FOUR PINS MOVED, EACH ARGUED IN PLACE**, all in
`tests/test_package_invariants.py`: the decorator inventory (five entries — four
loaders and the tab's `@st.fragment`, whose loss is what pass 20c-3c-1 shipped
silently), its `st.fragment` non-degeneracy list (four → five), the dashboard
module count (15 → 16) and `data.py`'s loader-decorator dict, which stays EXACT
and now declares the two private helpers with an empty list **because they must
not be cached** — `_readonly_connection` returns a live connection a later rerun
would find closed.

**VERIFIED BY RUNNING.** `python fixture_replay.py` **12/12 clean, exit 0, no
recapture**; `tests/run_serial_tests.py` **5/5** with `oncotriage/config.py` and
`oncotriage/registries/cancer_code_registry.py` confirmed restored;
`tests/test_package_invariants.py` **260/0/0**; CI bucket A **53/53**; and the
production `inferences.db` sha256 **unchanged** — `ab1403e3…` before and after.
The whole ten-tab dashboard was rendered end to end through `main()` with
`AppTest` (no exceptions, all ten headers present), and the new tab was rendered
against the **real production database** read-only, where it correctly reports
"no run tracking yet". **No money was spent and no migration was run.**


### A campaign is a row now, and one sparse row no longer takes the page down (the campaign pass)

**FIVE ITEMS, ALL IN THE DASHBOARD AND THE QUERY LAYER, AND TWO OF THEM ARE
CRASHES THAT WERE LIVE.** No schema change, no migration, no billed call:
`python fixture_replay.py` is **12/12 clean, exit 0, with no recapture**, and
the production `inferences.db` sha256 is unchanged.

**1. `oncotriage/dashboard/tabs/patient_explorer.py` CONVERTED EVERY NUMERIC
CELL WITH A BARE `int()`, AND MOST OF THE COLUMNS IT READS ARE NULLABLE.**
`int(nan)` raises `ValueError`, `int(None)` and `round(None, 2)` raise
`TypeError`, `f"{None:.2f}"` raises `TypeError`, `pd.NaT.strftime(...)` raises
`ValueError`, and `.astype(int)` over a column with one NaN raises. None of
them is caught: `oncotriage/dashboard/app.py` calls the tab inside `main()`
with no handler, so **one row cost all ten tabs, for every reader**. Measured
against the pre-fix module out of `git show HEAD:`, not reasoned about — it
raises, and the shipped one renders.

**THE SWEEP FOUND MORE THAN THE TWO COLUMNS THE ITEM NAMED**, and the census is
the finding. By AST over the pre-fix file: **41 conversion sites — 29 `int()`,
6 format-specs, 3 `round()`, 2 `strftime` and 1 `.astype(int)`** — across the
demographics tiles, the match-result tiles, the CSV export, the funnel (ten of
them), the rule-filter drop line, the trial table, the four performance tiles
and the two token tiles. Not `candidates_retrieved` and `mesh_dropped` alone.
**Three remain, and each is argued**: one `round()` INSIDE the CSV helper (its
argument has already been tested for absence), one over a mean the accessor
guarantees is a float, and the `.astype('Int64')` that is the fix rather than a
survivor.

**AND IT FOUND FOUR MORE TABS WITH THE SAME SHAPE, WHICH IS BEYOND THE LETTER
OF THE ITEM AND IS WHY IT WAS DONE ANYWAY.** The ten-tab render in item 2 is
the forcing function: a test that says "the dashboard survives a sparse
database" cannot pass while another tab takes it down.

**WHICH OF THEM WERE LIVE THROUGH `main()` IS MEASURED, NOT CLAIMED.** Each fix
was reverted in a `copytree`d copy with `PYTHONPATH` pointed at it and `main()`
rendered against the seed:

| reverted | `main()` on the seed |
|---|---|
| `patient_explorer.py` | **raises** `Cannot convert non-finite values (NA or inf) to integer` |
| `match_quality.py` | **raises** the same |
| `performance.py` | **raises** `LinAlgError` out of `gaussian_kde` |
| `trial_explorer.py` | renders clean |
| `demographics.py` | renders clean |

The last two are guards against states `main()` does not reach TODAY, and they
are kept and driven directly rather than dropped: the trial explorer renders the
SELECTED trial only and its selector is ordered by patient count, so the
unscored trial is never the default; and the sidebar drops NULL-`age` rows
before the demographics tab sees them. Both tab functions are public and take
whatever frame they are handed, so both have a subject — checks 4f and 4g drive
them against a database and a frame that reach the guard, each with a plant that
fires. **Recording which were live is the difference between a fix and a claim.**

| tab | what raised | fix |
|---|---|---|
| `trial_explorer.py` | `classify_trial_score(None)`, and `.astype(int)` on a NULL score | absence tested first; nullable `Int64` |
| `performance.py` | `classify_trial_score(None)` | absence tested first; unscored trials EXCLUDED from the panel and the exclusion stated on screen |
| `match_quality.py` | `.astype(int)` on a group whose every `match_score` is NULL, so `mean()` is NaN | nullable `Int64` |
| `demographics.py` | `(NaN // 10) * 10` then `.astype(int)` on a NULL age | rows with no age excluded from the age panel and COUNTED on screen |

**`classify_trial_score` ITSELF IS UNCHANGED, DELIBERATELY.** It is a pure
partition of a score into three, and "there is no score" is not a fourth bucket
of it — it RAISES on `None` and answers `'Unconfirmed Match'` on `nan`, which is
a real verdict about a measurement nobody made. `TRIAL_STATUS_NO_SCORE` is in
`oncotriage/dashboard/tiers.py` with the other three per-trial statuses, because
all three tabs that classify a trial need it and a status string typed out in
three files is what pass 20f-3 had to come back and fix for `'✅ Full Match'`.

**`oncotriage/dashboard/nullsafe.py` IS THE ONE OWNER OF "RENDER A CELL THAT MAY
BE NULL"**, and it exists because `tabs/run_health.py` already carried four
private readers of exactly that shape. Giving the Patient Explorer a second copy
would be two implementations of one reading, which ends with one tab rendering
an em dash and another rendering `0` for the same database cell. The four moved;
`run_health.py` imports them; **the run-health test's P6 plant was retargeted at
the CALL SITE rather than the helper**, which is the stronger control anyway
(the defect it models is "somebody used the wrong reader here", a decision taken
at the call site).

**WHICH helper is the judgement and it is made per column**, never once:
`optional_int_text` where NULL means "never measured" (its default must not be a
number — that is the whole reason it is a separate function); `as_int` where the
value feeds a CHART, which has no third state; `None` in the CSV export, because
an em dash in a numeric CSV column makes it a text column to every tool that
opens it. **A bar cannot draw "unknown", so the funnel NAMES the stages it drew
at zero underneath itself.** A stage a row never recorded is otherwise
indistinguishable at zero from one that genuinely passed nothing on — the
confusion `run_metrics`' meta row exists to remove, one layer up.

**2. NOTHING IN THE PROJECT HAD EVER CALLED `main()`.**
`tests/test_dashboard_run_health.py` and
`tests/test_dashboard_reproducibility_tab.py` each drive ONE tab function,
deliberately, and neither can see the wiring above them — so a tab dropped from
the strip, a tab renamed, or a `with tabN:` block deleted failed NOTHING.
`tests/test_dashboard_app_integration.py` renders it. **The expected tab set is
read out of `app.py` BY AST**, so the check is "the strip renders what the
source declares" rather than "the strip renders what it rendered last time" —
and `st.tabs` and the `render_*_tab` calls are pinned SEPARATELY, because a tab
added to one and not the other renders an empty panel that no assertion about
LABELS can see.

**3. `_kde_curve` GUARDED `len(scores) < 3` AND NOT ZERO VARIANCE**, and
`gaussian_kde` raises `numpy.linalg.LinAlgError` on a constant distribution —
which a BM25-only fallback run produces, since every `rerank_score` is then
unset. It is HOISTED to module scope (it closed over nothing and took two
arguments its body never read, so nesting bought nothing and cost the only thing
that matters: it could not be driven).

**THE RAISE IS VALUE-DEPENDENT, AND THAT WAS MEASURED RATHER THAN ASSUMED.**
Whether the estimator raises depends on the VALUE, because the covariance it
inverts is computed by subtracting a mean: for a constant that is exactly
representable in binary the residuals are exactly zero and the matrix is exactly
singular, and for one that is not they are denormal-but-non-zero and the
inversion SUCCEEDS. Measured on scipy 1.15.3:

    [0.5]*4  -> LinAlgError        [0.42]*6 -> no raise, a curve
    [0.0]*5  -> LinAlgError        [0.42]*3 -> no raise, a curve
    [1.0]*6  -> LinAlgError

So the unguarded function had two failure modes and only one of them was loud;
the other renders an enormously peaked curve over an interval of width zero. The
guard covers both, the test's seed uses **0.5** because 0.42 would have reported
the planted defect as UNCAUGHT, and check 4f records the silent half so a reader
does not come away believing it does not exist. The test is `np.ptp(...) == 0`
and not `std() == 0`: a sum-of-squares can come back denormal rather than an
exact zero, while the peak-to-peak of identical values is exactly 0 by
construction.

**4. `campaign_summary` IS THE READER FOR RUNS THAT CRASHED AND RESUMED.** A
`runs` row is a PROCESS: a batch run that dies leaves a KILLED row and the next
invocation opens a SECOND one, so one campaign that crashed twice is three rows
— each reporting a FRAGMENT of the cohort and a `started_at` that is when the
LAST process started. It is DERIVED, not stored: nothing was added to the
schema, and `resumed` plus the seven fingerprint columns are what make the
derivation possible.

**THE STITCH RULE, AND WHY EACH HALF IS THERE.** A run with `resumed = 1`
continues the campaign of the nearest PRECEDING run whose status is KILLED or
FAILED **and** whose fingerprint columns are identical; chains stitch
transitively. `resumed IS NULL` — a row written before that column — is NOT a
resume, because `NULL = 1` is NULL. A FINISHED predecessor is excluded because a
completed campaign has nothing to resume, and gluing a re-run onto one would
turn a repeat into a continuation. **The identical fingerprint is the reason the
query exists at all**: a prompt bump, a renderer edit, a re-index or a model
change between the crash and the resume breaks the chain, and the resumed run
becomes its own campaign — because "which configuration produced this number" is
the question a campaign total is asked, and mixed-configuration fragments must
not sum.

**THE PREDICATE IS GENERATED FROM `RUN_FINGERPRINT_COLUMNS`, NEVER RETYPED.**
Hand-listing six of the seven would leave one axis along which two
configurations merge into one campaign with nothing saying so, and a hand-written
list does not grow when the next field is gated. Generated, it does — and
`tests/test_storage_query_layer.py` section 8b-j re-derives that from the
writer's own tuple.

**SQLite's `IS` IS NULL-SAFE EQUALITY AND THAT CUTS BOTH WAYS.** A field that
degraded to NULL on both sides compares equal, which is right; two runs with NO
STAMP AT ALL would also compare equal on all seven, which is not. Both sides are
therefore additionally required to carry a `fingerprint_version` — an unknown
configuration is not a matching configuration, and `run_fingerprint` itself keys
FP_ABSENT on exactly that column.

**FOUR THINGS THE QUERY DELIBERATELY DOES NOT DO, stated at the code rather than
discovered later**: it reads run order off `runs.id`, which is AUTOINCREMENT and
therefore monotone in creation order within one database (`started_at` is a TEXT
two rows can share); "nearest preceding" is nearest among runs satisfying BOTH
halves, which is the literal reading — so a resume CAN attach across an
intervening crashed run of a different configuration, producing two campaigns
whose wall spans overlap, which is a reporting artifact and not a
misattribution, and `run_ids` is emitted in order so the gap is visible (the
other reading would report a genuine resume as a whole campaign, which IS a
misattribution); `last_finished_at` is `MAX(finished_at)`, so `unfinalized_runs`
is what says the span is open at that end and it is never extrapolated to `now`;
and it cannot see a campaign that was never recorded, which is every row the API
writes.

**THE ORDERED RUN-ID LIST IS BUILT BY RECURSION AND NOT BY `group_concat`.**
That function leaves its order arbitrary, and an `ORDER BY` inside it needs
SQLite 3.44+, which a CI runner's system SQLite may not be. Determinism is a
stated property of this project, so the string is assembled one member at a time
in ascending id order — guaranteed on every version.

**5. THE RUN HEALTH TAB GAINED A CAMPAIGNS PANEL**, beside the run list and not
instead of it. Both are true and they answer different questions: "which PROCESS
wrote these rows" is what an operator debugging a crash needs, and "what did
this CAMPAIGN cost and how many patients did it actually cover" is the only one
a reviewer can attribute a published number to. The tab still carries NO SQL —
the frame comes through `load_run_campaign_data()`, the eighth
`@st.cache_data(ttl=60)` loader.

**A DEFECT IN `_strip_sql_noise` WAS FOUND BY THE NEW QUERY AND IS FIXED.** It
was one regex substitution nested inside another — string literals masked
FIRST, comments second — so an APOSTROPHE INSIDE A `--` COMMENT was read as the
start of a string literal and swallowed everything up to the next quote anywhere
in the query. **Two ordinary English comments were enough to hide a whole CTE,
and the CTE hidden was the one naming `i.run_id`.** The failure is silent and in
the DANGEROUS direction: the query derives fewer additive columns than it names,
so a new query that declares nothing agrees with a derivation that found
nothing, `tests/test_storage_schema_guards.py` check 1a passes, and `report()`
dies on `no such column` against an older database — the exact defect item 38
removed from File 16. Measured: `campaign_summary` derived
`(('runs', 'resumed'),)` under the old implementation and
`(('inferences', 'run_id'), ('runs', 'resumed'))` under the scanner that
replaces it. Reversing the two passes would only move the hazard (a `--` inside
a string literal would then be read as a comment), so it is one left-to-right
scanner that knows both forms, and SQL's doubled-quote escape falls out of it
for free. The two now-dead regexes were DELETED rather than left standing, and
purged from the prose as well — check 2h counts a name inside any string literal
as a read.

**A SECOND DEFECT WAS FOUND BY THE CAMPAIGNS PANEL AND IS FIXED IN THE TEST.**
`tests/test_dashboard_run_health.py` selected rendered frames POSITIONALLY —
`dataframe_objects[0]`, `[1]`, `[2]` — so inserting one panel above the
attribution census silently re-pointed three checks at the wrong table, and the
first of them ABORTED the whole file on a `KeyError` rather than failing. **That
is the tenth time this project has shipped that shape.** Frames are selected by
the COLUMNS they carry now, which is also what those checks are actually about.

**WHAT WAS MEASURED BY RUNNING.** CI bucket A **60/60** (including all three
dashboard tests), `tests/test_package_invariants.py` **260/0/0**,
`tests/test_storage_query_layer.py` **349** (was 317),
`tests/test_dashboard_run_health.py` **192** (was 167; the run block said 155
and was stale), `tests/test_dashboard_app_integration.py` **110**, the serial
runner **5/5** with `oncotriage/config.py` and
`oncotriage/registries/cancer_code_registry.py` confirmed restored,
`python fixture_replay.py` **12/12 clean, exit 0, no recapture**, and the
production `inferences.db` sha256 **unchanged**. **No money was spent and no
migration was run.**

**FOUR PINS MOVED, EACH ARGUED IN PLACE**, all in
`tests/test_package_invariants.py`: the decorator inventory (the eighth loader),
the dashboard module count (16 → 17, `nullsafe.py`), `data.py`'s loader
decorator dict, and the dashboard-purity probe's import list — which was also
missing `tabs/run_health.py` and now has it.

**WHAT IS NOT DONE, NAMED RATHER THAN LEFT TO BE DISCOVERED.** The remaining six
tabs were not swept for the same shape; only the four that a sparse row actually
took down were fixed, and the ten-tab render is what would report the next one.
`classify_trial_score` is still called unguarded by nothing — all three call
sites now test for absence first — but the underlying function still raises on a
`None`, which is deliberate and is argued at `TRIAL_STATUS_NO_SCORE`.


### The Stage 5 call mode is a provenance fact everywhere run identity lives (the call-mode pass)

**`config.matching_call_mode()` DECIDED THE SINGLE LARGEST LEVER ON WHAT A
PATIENT COSTS AND NO PROVENANCE MECHANISM COULD SEE IT.** Measured before
anything was written: `FINGERPRINT_FIELDS` gated six facts and none was the
arm; `runs` stored those six as columns and none was the arm;
`campaign_summary` stitched on all seven stamp columns and none was the arm;
`tracking.CONFIGURATION_PARAM_NAMES` had 32 members and none was the arm; and
**zero of the fifty registered queries named `matching_call_mode`** even though
`inferences.matching_call_mode` had been an additive column since era 3. Three
consequences, and the first is the expensive one:

- a **grouped-mode checkpoint resumed under per-trial mode answered FP_MATCH**,
  skipped every patient the grouped process had completed, ran the rest in the
  other arm and put both into one `inferences` table with nothing in it saying
  so. Nothing else in the stamp moves with the flag -- it is a bool no other
  gated field is a function of, and `matching_model_configured` is the same
  wire id in both arms because it is the same judge;
- **campaign stitching would have merged the two arms**, summing a grouped
  fragment's cost and cohort with a per-trial fragment's;
- and no number could be attributed to an arm at all, so the three-arm
  comparison the flag exists for had nothing to read.

**FINGERPRINT_VERSION IS 3 AND THE BUMP'S COST IS THE MECHANISM WORKING.**
Every v2-stamped artifact answers FP_VERSION once -- the refusal already carries
the clause saying a shape change is not necessarily a configuration change, and
the remediation is the consumer's own (`--fresh`, `--fresh-start`, a new
`--output-dir`).

**REQUIREMENT 1's PROOF OBLIGATION WAS DISCHARGED BY RUNNING, NOT BY READING.**
No fixture surface carries the fingerprint or the arm: **577,588 leaves across
all twelve deterministic prefixes and all 15 environment keys of each, and zero
carry `fingerprint`, `call_mode` or the per-trial flag under any spelling**;
`oncotriage/fixtures/` contains no reference to `run_fingerprint` at all.
`python fixture_replay.py` is **12/12 clean, exit 0, with no recapture and zero
CONFIG MOVED lines**, and the twelve files' sha256 are byte-identical before and
after.

**WHAT WAS ADDED, AND ONE THING THAT DID NOT NEED TO BE.**

| where | what |
|---|---|
| `run_fingerprint.FINGERPRINT_FIELDS` | `matching_call_mode`, resolved by `_call_mode()` -- `_wire_model()`'s shape, degrading to UNKNOWN and counting rather than raising |
| `database_logger.RUN_FINGERPRINT_COLUMNS` | the same name, so `start_run_record` fills it and the round-trip test stays closed |
| `database_logger.RUN_COLUMN_ADDITIONS` | `"matching_call_mode": "TEXT"`, which is what MIGRATES an existing database and what `ADDITIVE_COLUMNS` reads so a query naming it can be SKIPPED rather than killing `report()` |
| `SCHEMA_USER_VERSION` | 3 -> 4, in the same commit, per the constant's own rule |
| `queries.campaign_summary` | the column in the projection; the stitch predicate needed NO edit because it is generated from `RUN_FINGERPRINT_COLUMNS` |
| `queries.run_summary` | the column in the projection, so a run row says which arm produced it |
| `queries.call_mode_comparison` | **new**: cost, patients and omissions per (run, observed arm) |
| `tracking.configuration_params` | `matching_call_mode`, through the `_prompt_params()` seam |
| the three resume gates | **nothing** -- see below |

**THE BATCH RUNNER ALREADY HAD THE GATE, AND THE BRIEF SAID IT MIGHT NOT.**
`batch/runner.py:load_checkpoint` calls `run_fingerprint.compare(data.get
("fingerprint"), current)` at the point the checkpoint is loaded and refuses on
anything but FP_MATCH; `ablation/study.py:load_ablation_checkpoint` and
`evaluation/run_harness.py:environment_gate` do the same. So gating the field is
what makes all three refuse a mode mismatch, with the existing message naming
the field AND both modes (`matching_call_mode: 'grouped' -> 'per_trial'`), and
**no refusal code was added anywhere**. Driven both directions through all three
shipped gates: mismatch refused with the state intact, match proceeds.
**Only the evaluation harness has `--allow-environment-change`**, and
FP_CHANGED is in its `OVERRIDABLE_OUTCOMES`, so the escape hatch covers the arm;
the batch runner and the ablation study have no such flag and their remediation
is `--fresh` / `--fresh-start`.

**TWO PREMISES IN THE BRIEF WERE WRONG AND THE CODE'S OWN RULE WAS APPLIED
INSTEAD.** `SCHEMA_USER_VERSION` was **3**, not 2 -- era 2 was `runs.resumed`
and era 3 was `inferences.matching_call_mode` -- so the bump is 3 -> 4. And the
arm could not join `CONFIGURATION_PARAM_NAMES`: that tuple is read through
`getattr(config, name)` and logged verbatim, and the owner is a FUNCTION, so a
member named for it would log a repr of a function object. Putting the raw
`MATCHING_PER_TRIAL_CALLS_ENABLED` there instead would have put a SECOND
derivation of the flag-to-arm mapping in a durable store -- the two-copies shape
pass 20f-2 removed for the cross-encoder checkpoint -- so it goes through the
derived seam `_prompt_params()` already established, and the tuple records why
it is not a member.

**`RUN_COLUMNS` HAD TO LEARN THAT ONE COLUMN CAN BE NAMED BY TWO SOURCES.**
`matching_call_mode` is a stamp field AND an additive column, for two orthogonal
reasons, and the plain concatenation named it twice -- `OperationalError:
duplicate column name` at the INSERT, on the first run of every campaign.
`_last_wins` de-duplicates keeping the LAST occurrence, which is not a detail:
keeping the first would put the column at its stamp position and make the tuple
describe a column order no database has. Measured rather than argued -- a fresh
database and one migrated from era 3 now report the **identical** physical
column order, which is also why the column is deliberately NOT in the `runs`
CREATE TABLE.

**THE DERIVATION CHECKER FOUND A DEFECT READING DID NOT.** The new query's first
`requires_columns` declaration omitted `("trial_matches", "not_evaluable_reason")`,
which its omission CTE tests -- so on a database predating that column it would
have raised `no such column` and taken `report()` down with it, reinstating item
38's defect exactly.

**AND THE QUERY-LAYER SEED FOUND TWO MORE.** The 8b campaign probe row kept the
pre-call-mode column list, so it carried a NULL arm against FPCRASH's `grouped`,
did not stitch, and three checks failed for a reason unrelated to what they
assert -- the new column being sharp. And the arm pair was first seeded with
fingerprint key "A", which made MODECRASH the nearest preceding qualifying run
for that probe and quietly took its campaign away from FPCRASH.

**ONE PRE-EXISTING TEST DEFECT WAS REPAIRED RATHER THAN WORKED AROUND.**
`tests/test_storage_run_identity.py` built its stamp from a hand-written VALUES
dict under a comment claiming "a field added to the stamp appears here
automatically". It did not: the first field added raised `KeyError` **at module
level** and took the whole file with it, reporting one traceback where it owed
134 results. **That is the abort shape this project has now shipped eleven
times.** The claim is true now, the fallback is REPORTED rather than silent, and
a field with no literal is named by a check instead of standing in as a
placeholder.

**WHAT WAS VERIFIED BY RUNNING.** `python fixture_replay.py` **12/12 clean,
exit 0, no recapture**; CI bucket A **61/61**;
`tests/test_package_invariants.py` **260/0/0**; every bucket B/C/E file on this
machine green at its documented count; the production `inferences.db` sha256
**unchanged** -- `ab1403e3...`, 90,185,728 bytes, before and after. **No money
was spent and no migration was run against the production database**: the
`runs.matching_call_mode` column appears there on the next run that opens it,
which is what the additive mechanism is for.


### A SKIP IS NOT A PASS (commit `ec2033a`)

`tests/test_package_invariants.py` has a third counter. `skip(label, reason)`
records coverage that could **not** be exercised on this platform, into
`_RESULTS["skipped"]` and `_SKIPS` — never into `passed`. The count is
**always printed, even at zero**, because a skip count that only appears when
non-zero is indistinguishable from a file that has no skip mechanism at all.

It exists because `caffeine` became darwin-only: sections 2 and 5 pre-import it
before arming their traps, and on Linux that import is a process spawn that
cannot succeed. Both preambles guard it and record a SKIP.

| platform | before | after |
|---|---|---|
| Linux | 234 passed / 6 failed | **245 / 2 / 2** |
| macOS | 247 / 0 | **247 / 0 / 0** (unchanged) |

**It is still NOT in CI bucket A.** The two remaining Linux failures are check
2b's non-degeneracy half, which restores the project root by unsetting
`ONCOTRIAGE_MAIN_PATH` and leaning on a fallback that exists on one machine.
That is an assumption in the test, not a defect in the package, and it is an
open follow-up.

### `gpt4o` IS `llm_classifier` EVERYWHERE (the naming pass)

Stage 5 stopped calling GPT-4o on **2026-08-04** (`MATCHING_MODEL` is
`gpt-5.6-terra`) and the name stayed in 476 places: a graph node, a router, a
config constant, ten state/result keys, nine database columns, three ablation
columns, nine recorded fixture fields, a drift metric and an API response key.
**A name that pins a vendor's model to a pipeline stage goes stale the first
time the model changes, and this one had.** The prefix is `llm_classifier` and
**not** a bare `classifier`, because the pipeline also contains a cross-encoder
ranker (Stage 3) and rule-based filters (Stage 4).

| was | is |
|---|---|
| `node_gpt4o_evaluation` | `node_llm_classifier_evaluation` |
| `route_after_gpt4o` | `route_after_llm_classifier` |
| `MAX_GPT4O_RETRIES` | `MAX_LLM_CLASSIFIER_RETRIES` |
| `CASE_GPT4O_RETRY = "gpt4o_retry"` | `CASE_LLM_CLASSIFIER_PARSE_RETRY = "llm_classifier_parse_retry"` |
| fixture `gpt4o_retry_constructed` | `llm_classifier_parse_retry_constructed` |
| every `gpt4o_*` key, column, field, metric | `llm_classifier_*` |

**THE FIXTURE CASE IS NOT A STRAIGHT PREFIX SWAP.** Its subject is the
**parse-failure** retry budget; truncation is a separate mechanism with a
separate budget (`MAX_TRUNCATION_SPLITS`) and its own fixture
(`truncation_split`), and `gpt4o_retry` did not say which of the two it meant.

**NO MIGRATION WAS WRITTEN, DELIBERATELY.** The column-additions dicts are the
only migration mechanism in `storage/database_logger.py` and they can only ADD
columns — `ALTER TABLE ... RENAME COLUMN` is not expressible through them, and a
compatibility shim reading both names is a second vocabulary that has to be kept
in step forever. The production database is disposable, a separate copy exists,
and every published number comes from a fresh end-to-end run. **An existing
database keeps the old column names until it is rebuilt**, and the readers will
not find them. Measured, by rebuilding the pre-rename `CREATE TABLE` out of
`git show HEAD:` and running the registry against it: **12 of the 39 queries
raise** (`DatabaseError` wrapping sqlite's `no such column`) and **0 succeed
silently**. That is the intended loud failure — no reader returns a wrong
number, and none returns zero.

**THE FIXTURE SCHEMA WENT 3 → 4** and the twelve recordings on disk are
therefore unreadable. `load_fixture()` refuses by version *before* any field is
read, naming both versions and the re-capture command, and `fixture_replay.py`
exits 1 on a load failure — the alternative is a replay that reports every
renamed field as absent and compares `None` with `None`. They were already
unreplayable for an unrelated reason (the alias moved past their pinned
collection digest at the M-category pass), so no working gate was lost; the
re-capture is scheduled separately and **no model call was made in this pass**.

> **CURRENT STATE, 2026-08-20 — the scheduled re-capture happened, so the two
> present-tense claims above ('the twelve recordings on disk are therefore
> unreadable', 'the re-capture is scheduled separately') describe the tree as
> this pass left it and not the tree today.** They are kept as written. The
> recordings on disk are at the current `SCHEMA_VERSION`, `load_fixture()`
> accepts them, and `python fixture_replay.py` is **12/12 clean, exit 0, with
> no recapture**. The same applies to every earlier account that states
> `SCHEMA_VERSION` is 3: true when written, superseded since, and left alone
> because each is a premise of that pass's own byte-identity proof.

**SIX HISTORICAL-RECORD SITES KEEP THE OLD NAME**, because renaming inside an
account of a past defect makes it describe something that never happened. The
rule applied: *a past-tense account keeps the old spelling when the thing it
names no longer exists under any name.*

| site | what it records |
|---|---|
| `storage/database_logger.py` (the `llm_classifier_retries` insert) | File 14 read `result["gpt4o_retries_exhausted"]`, a key only the error handler wrote |
| `agent/terminal.py` (the provenance block) | `gpt4o_retries` existed only on the error path |
| `storage/queries.py` (above `llm_classifier_efficiency_by_trial_count`) | File 16 line 545, `df_gpt4o_efficiency` — a variable in a deleted file |
| `storage/queries.py` (`_int_or_none`'s docstring) | a dated measurement of rows *stored under the old schema* |
| `tests/test_storage_inference_logging_contract.py` (module docstring) | the same File 14 read |
| this file, item 38's account | the same dated measurement |

**WHAT WAS DELIBERATELY NOT SWEPT.** The hyphenated `GPT-4o` prose family — ~161
mentions, mostly dashboard help text, console strings, chart labels and dated
calibration notes — is a **separate** family that a `gpt4o` search does not
match. Comments and docstrings documenting a *renamed identifier* were corrected
(the node, the router, the two terminal nodes, the Stage 5 state block); **no
emitted string changed**, so no console line, chart label or report heading
moved. Where prose needs a model name it should interpolate `MATCHING_MODEL`, on
`/pipeline/info`'s precedent — writing `gpt-5.6-terra` into a comment creates the
next stale site rather than removing one.

**ONE EMITTED VALUE DID CHANGE, and it is a contract change stated as one:**
`GET /pipeline/info` reports `config.max_llm_classifier_retries` where it
reported `max_gpt4o_retries`. `result["gpt4o_retries_exhausted"]` —
`node_error_handler`'s deliberate alias for an external consumer — became
`llm_classifier_retries_exhausted` with it. No caller in this repository reads
either.

### The accepted table gets a staleness gate (the trivyignore pass)

**`.trivyignore` HAD NO CHECK, AND `audit_gate.py` ALREADY REFUSED THE SAME
THING FOR pip-audit.** An id that has stopped appearing in the scan describes
nothing and is re-read by the next person as a live constraint; Trivy is
perfectly happy to be handed a file full of them and says so nowhere. The
BASE-LAG block had made that concrete — four util-linux ids whose whole point
is that they EXPIRE the moment a base image carrying Debian's fix is published,
with a removal condition its own comment says "can be checked by a script".

`.github/scripts/trivyignore_staleness.py` is that script. **It is not a second
gate on vulnerabilities** — it never reads severity to decide anything and it
cannot make the Trivy gate greener. It gates the HYGIENE of the accepted table,
which is the axis the gate itself cannot see.

**IT READS THE FULL, NON-GATING SCAN, AND ALL THREE OF THAT SCAN'S PROPERTIES
ARE LOAD-BEARING** — every severity, fixed AND unfixed, and **no
`--ignorefile`**. Read the GATE's output instead and every accepted entry looks
stale, always, because the gate suppresses these ids by construction; the check
would then demand the file delete itself.

**THREE EXIT CODES, THREE DIFFERENT INSTRUCTIONS.** 0 is clean. **2** is "the
accepted table needs a human" — a stale entry, a line the parser cannot read,
or a duplicated id. **1** is "the comparison could not be made" — the report is
missing, unreadable, not a Trivy report, or carries no vulnerability rows at
all. The 1/2 split is the design rather than a detail: a run that could not
read the scan has established nothing about the table, and exiting 2 there
would send somebody to delete a live exemption on the strength of a scan of
nothing. A degenerate report is the state the whole guard exists to catch, and
the workflow has already lost an image to a `docker builder prune` once.

**"NO LONGER APPEARS" IS THE WEAKEST TEST THAT IS STILL TRUE.** An entry that
suppresses nothing at the gate TODAY — currently MEDIUM, or currently unfixed —
is **INERT**, printed and never gated on: it is one vendor re-rating from
mattering again, and deleting the entry deletes the argument with it.

**THE COMMENT CORRECTIONS SHIPPED IN THE SAME COMMIT** (`6ab6d17`): a stale
apt-upgrade note and a stale finding-count note, both describing a Dockerfile
that had moved underneath them.

### The ragas venv was inside every image, and the suite could not have said so

**`03- Code/09- Testing/ragas-venv/` — 1.7 GB, 92,649 files — WAS IN `/app`.**
Measured on the image built 2026-08-20: `/app` was **1.8 GB**, of which the
whole of the rest of the project was **7 MB**. It matched none of the five venv
patterns in `.dockerignore` because those are root-level names and it is
nested, and `.dockerignore` has no marker-based form — it cannot be told "any
directory carrying a `pyvenv.cfg`", which is what `_is_virtualenv` in
`.github/scripts/static_checks.py` uses and is why that gate survived a venv
this file could not name.

**IT WAS INVISIBLE TO GIT, WHICH IS WHY NOTHING EVER MENTIONED IT.** The
environment writes its own `.gitignore` holding `*`, so `git status` never
reported it — and Docker does not read `.gitignore`.

**Measured with a `--no-cache` probe that `du`s the context inside the
container, because BuildKit's printed `transferring context` line is
INCREMENTAL and not comparable across builds** — the second build printed
16 kB, a delta, not a total:

| | context bytes | files | `/app` | image |
|---|---|---|---|---|
| before | 1,676,935,983 | 92,890 | 1.8 G | 1,053,343,840 |
| after | 7,256,315 | 241 | 7.5 M | 607,072,438 |

The whole directory is excluded rather than the venv inside it: those two
entries — `ragas-venv/` and a nested `.DS_Store` — are its entire contents.
**The project-root sibling `09- Testing/`** (the characterization fixtures, the
evaluation runs) **is outside this build context and is unaffected.**

**AND THE STALENESS GATE GOT ITS OWN STANDING TESTS** in the same pass, as
`tests/test_trivyignore_staleness.py` — 173 checks at the time. Every scenario
**drives the real script as a subprocess** (`sys.executable` plus its path)
rather than importing or exec'ing it, so section 1c of
`tests/test_package_invariants.py` has nothing to see and no `_EXEC_ALLOWLIST`
entry is needed — and so the thing asserted is the **exit code**, which is the
script's contract and is precisely what an in-process call does not produce.
Every run sets `cwd` to the temp directory, which is what turns the script's
claim that its defaults resolve off `__file__` rather than off the working
directory into a measurement.

**THE MINIATURE TRIVY REPORT IS A LITERAL IN THE TEST FILE AND ITS ADEQUACY IS
DERIVED, NOT ASSERTED.** The author of a fixture is the author of the
assertions, so "it has the right fields" cannot be a list retyped there: the
test parses the shipped script with `ast` and collects every key it reads out
of a report (`data.get(...)`, `result.get(...)`, `vuln.get(...)`, and the
`"Results" not in data` guard), then requires the miniature to carry all of
them at the right nesting, with a non-degeneracy pin on the counts so a walk
that matched nothing cannot pass for free.

**FIFTEEN REVERTS, FIFTEEN CAUGHT**, each planted into a four-file copy of the
repository with a realpath preflight asserting the COPY is what runs.

**THE BRIEF THIS WAS BUILT FROM WAS WRONG ABOUT ONE EXIT CODE AND THE TEST IS
WRITTEN TO THE CODE.** It asked for "unreadable lines exit 2 even alongside a
degenerate report". Measured: the exit is **1** — `main()` returns from the
degenerate-report guard above the final status check — and the UNREADABLE
section is still printed, above the FATAL. That is the right behaviour for the
1/2 reason above, and section 6b of the test argues it in place rather than
asserting the brief.

**THREE DEFECTS IN THE TEST'S OWN CODE WERE FOUND BY RUNNING, NOT BY READING**,
and the second is the instructive one:

- a positional argument bound to the wrong parameter **aborted the file** at
  section 12 with no summary — the abort class this project has now shipped six
  times, closed here by hardening every raise-capable expression (two
  `header_line(...).startswith(...)` and a pair of `str.index()` calls would
  each have raised on precisely the defect they test for);
- the `--help` default-path assertion **passed for the wrong reason**: argparse
  wraps long words MID-TOKEN, and collapsing whitespace to single spaces held
  against the real repository only because its path happens to wrap at a space.
  It failed against a byte-identical copy under a temp directory. **An
  assertion that holds because of where a line happened to break has not been
  tested**;
- the test derived its root with `abspath` while the script derives its own
  with `Path.resolve()`, which follows symlinks. Three checks failed in that
  same copy for that reason alone (macOS `/var` -> `/private/var`).

### Two depth patterns, a marker-based exclusion guard, and one tightened regex (the CI-hygiene cleanup pass)

Four riders off the two passes above, each of which had recorded them as
follow-ups it could not take. **No pipeline code, no gate weakened, and the
only image content change is the two exclusions.** `fixture_replay.py` is
**12/12 clean without recapture**, `tests/test_package_invariants.py` is
unchanged at **247**, and the production `inferences.db` sha256 is unchanged.

**1. `__pycache__` AND `.DS_Store` WERE STILL SHIPPING, FOR THE IDENTICAL
REASON.** Both patterns were in `.dockerignore` and both matched at the context
root only — and this project has neither at the root, so for the whole life of
the file they excluded **nothing at all**. Measured inside the image: **18
`__pycache__` directories holding 94 `.pyc` files, and 3 nested `.DS_Store`.**
`**/__pycache__` and `**/.DS_Store` are added beside the root forms, which are
kept.

**THE `**` FORM IS DOCKER'S DOCUMENTED EXTENSION TO `filepath.Match`, NOT A
COUNTEREXAMPLE TO THE NOTE ALREADY IN THE FILE.** Docker does not hand the
pattern to `filepath.Match` unaltered; it defines `**` itself, matching any
number of directories including none, precisely because that function's `*`
cannot cross a `/`. So the "Test Files" note is still exactly right about a
bare name or a `*` pattern.

**AND THE DOCUMENTATION IS NOT WHAT THE LINE IS TRUSTED ON.** `**` handling has
had real bugs in real build engines, and a pattern that silently matches
nothing looks identical to a tree with nothing to exclude — which is how the
root-level lines survived this long. It is trusted because the context was
**exported and diffed**: 241 files -> 144, **exactly 97 removed** (94 `.pyc` +
3 `.DS_Store`), **zero added, and zero removals outside those two classes** —
and because the rebuilt image measures **0** `__pycache__` directories and
**0** `.DS_Store` under `/app`. `/app` 7.5 M -> **4.6 M**; image 607,072,438 ->
**605,890,086**.

**2. THE EXCLUSION NOW HAS TO KEEP DESCRIBING SOMETHING**, in
`tests/test_dockerignore_exclusions.py` — a **separate file** from the
staleness one, argued there: same doctrine, different subject, different
failure owner (a Docker-context change must not turn red a file named for the
Trivy gate), and its evidence string, collision-matrix derivation and hygiene
list each enumerate the three files it reads by name.

**THE LOAD-BEARING CHECK IS MARKER-BASED, BECAUSE "THE LINE IS PRESENT" IS
ITSELF A NAME THAT ROTS.** Renumber `09- Testing/` to `10- Testing/` and the
line is still there, still true-looking, and 1.7 GB is silently back in the
context. So the invariant held is: **every directory in the context carrying a
`pyvenv.cfg` must have itself or an ancestor written out as a literal line in
`.dockerignore`** — the same marker `_is_virtualenv` uses, for the same reason
(the marker is what the thing IS; the name is what somebody called it). A
rename then fails in **two voices**: the moved venv is undeclared (naming the
venv) and the line covers nothing (naming the line).

**IT DOES NOT REIMPLEMENT `.dockerignore` MATCHING**, deliberately. A second
implementation of `filepath.Match` plus `**` would agree with Docker exactly
until the day it did not. The check asks a strictly simpler question — is this
path, or a directory above it, written out as a **literal** line — which is an
UNDER-approximation (a venv excluded by a glob would read as undeclared) and is
the right direction to be wrong in, because the repair is to name it, which is
what the file's own comment asks for.

**THE OBVIOUS FORM OF THIS CHECK IS RED IN CI FOREVER, AND THAT WAS MEASURED
BEFORE IT WAS WRITTEN.** "Assert a `pyvenv.cfg` exists under the excluded path"
cannot stand: `09- Testing/` is untracked and self-ignored, `git ls-files`
returns nothing for it, and **no hosted runner will ever have it** — so that
check passes on the author's machine and fails everywhere else, which is the
shape `static_checks.py` records for the gate it had to narrow. The
tree-dependent half is a **SKIP** when there is no environment to talk about,
counted separately and **printed even at zero** on
`tests/test_package_invariants.py`'s precedent. Everything reading the
committed `.dockerignore` still runs in CI, and so does **every control**,
because the controls drive pure functions with fabricated inputs rather than
the filesystem. Six out-of-band scenarios, all six behaving as required: line
deleted -> fails naming the path and the line; directory renamed -> fails in
both voices; **no venv at all -> exit 0 with 2 SKIPS, not a pass and not a
failure**; each depth pattern and each root pattern held independently.

**3. `_ID_RE` ERRED IN THE DIRECTION ITS OWN COMMENT FORBIDS.** The first group
was `[A-Za-z]`, so `not-an-id` — any hyphenated lower-case phrase, which is
what a half-written note looks like — satisfied it, became an ENTRY, matched
nothing in the scan and was reported **STALE**: a human sent to delete a line
that was never an entry. It is `[A-Z][A-Z0-9]*` now, and such a line is
UNREADABLE, which names itself and is one edit to fix.

**ONLY THE FIRST GROUP TIGHTENS.** Later groups keep `[A-Za-z0-9.]` because
they must: GHSA ids are lower-case after the prefix (`GHSA-6v7p-g79w-8964`).
Verified rather than assumed — **all 21 entries in the committed `.trivyignore`
parse, 0 unreadable**, and `CVE`, `GHSA`, `PYSEC`, `RUSTSEC`, `DLA`, `DSA`,
`TEMP`, `GO`, `ALAS`, `ELSA`, `WS`, the compact `DS002` form and the
`exp:YYYY-MM-DD` tail all still parse. **The set that changes class is exactly
"first group contains a lower-case letter"** — that follows from the diff
between the two patterns rather than from a sample, and no id class this
project has met is in it.

**THE RESIDUAL IS STATED RATHER THAN GLOSSED:** an id whose prefix is genuinely
lower-case would now be UNREADABLE. **A pre-existing limit that this did NOT
introduce and did not fix:** an id containing a colon (`RHSA-2024:0123`) was
rejected before and is rejected now — `[A-Za-z0-9.]` has never admitted one.

**Check 13h USED TO PIN THE OPPOSITE, and that is the point of it.** It
recorded the looseness as measured-not-desired; it now pins the closure, in
four checks, with **the old regex driven as its control** — a copy of the
script in the temp directory with the one group widened back, run as a
subprocess exactly as the shipped one is, so nothing is exec'd and the shipped
file is never written. `tests/test_trivyignore_staleness.py` is **181** checks,
was 173.

**4. THE TWO PASSES ABOVE COULD NOT RECORD THEMSELVES**, because each brief
constrained its own diff to the files it touched. This section and the two
before it are that record, written now.

**A DEFECT IN THE NEW TEST'S OWN CODE, FOUND BY RUNNING.** Its nested-
`__pycache__` counter was a bare `os.walk`, which descends into
`09- Testing/ragas-venv` and counted **4,521** — the venv's own caches, which
are not in the build context at all and are not this project's. The answer
about the context is **20**. A count that walks somewhere its subject does not
is not a smaller number, it is a different question.

### One owner for the alias and its staging family (the alias-ownership pass)

**THE ALIAS AND ITS STAGING FAMILY WERE WRITTEN OUT THIRTEEN TIMES IN
`oncotriage/retrieval/indexer.py` AND ONCE MORE IN THE GENERATED DAG.**
Re-enumerated by AST rather than taken from the brief, which said eleven: the
scan strips docstrings and reports every remaining string CONSTANT, so it also
catches the two inside console f-strings that a definition-level list misses.
Thirteen in the indexer — the `cleanup_old_collections` signature default, the
two in its family filter, `main()`'s staging name, the `resolve_alias_target`
read, the `swap_alias_atomic` call, the `cleanup_old_collections` call, the two
console lines, and the four in the direct-rebuild branch — plus
`dag_generator.py`'s `rebuild_index`, which is the twelfth SITE and the
fourteenth literal.

**IT IS VALUE-PRESERVING FOR EVERY CURRENT CALLER AND THAT IS MEASURED, NOT
CLAIMED.** `config.COLLECTION_NAME` is `"trial_criteria"`, so every rewritten
site evaluates to the byte-identical string it evaluated to before — including
both console lines, whose emitted text is unchanged. `python fixture_replay.py`
is **12/12 clean, exit 0, with no recapture and zero `CONFIG MOVED` lines**,
which is what says the pinned-collection refusal did not fire and the
deterministic prefix did not move.

**`staging_prefix(alias_name)` IS THE ONE OWNER, AND IT IS A FUNCTION OF THE
ALIAS RATHER THAN A MODULE CONSTANT.** That is the whole point rather than a
style choice: `cleanup_old_collections(alias_name=...)` is a PARAMETER, so a
module constant would let the selection half and the protection half read
different knobs — which is exactly the defect below. Two call sites,
`staging_prefix(COLLECTION_NAME)` in `main()` and `staging_prefix(alias_name)`
in the cleanup, and the test pins that there are exactly two.

**THE LOGIC DEFECT: THE CLEANUP ENUMERATED ONE FAMILY AND PROTECTED A TARGET
FROM ANOTHER.** The candidate filter hardcoded `"trial_criteria_"` while the
protection half honoured `alias_name`, so a caller passing any other alias got
**two wrongs at once**: the deletion loop removed members of a family it was
never asked about, and left the family it WAS asked about untouched. Nothing
raises, the log reads like a successful cleanup, and the only symptom is
collections gone. It is invisible on the default path by construction, which
is why nothing caught it — the literal and the parameter agreed for every call
site in this repository. **That is the one intended behaviour change, and it is
confined to a non-default `alias_name`.**

**THE DAG'S SPLIT WAS THE SAME DEFECT ONE LEVEL OUT.** `rebuild_index` already
held `alias = cfg["COLLECTION_NAME"]` and swapped onto it, while building
`staging_name` from the literal — and it calls `cleanup_old_collections(
alias_name=alias)`, which enumerates the ALIAS family. Repoint the alias and
the task would have built into one family, swapped onto another, and then
cleaned up a third thing. `staging_name = alias + "_" + timestamp` closes it
and keeps the DAG free of a config import for a fact it already holds.

**`tests/test_indexer_admission_filters.py` GREW SECTION 4b, 359 → 403 checks.**
Two disjoint families and two aliases through the existing `_RecordingClient`
stand-in, patched at `indexer.get_qdrant_client` — the from-import binding, not
`oncotriage.config`, which is this project's recorded namespace lesson. It runs
the default path three ways and requires all three to agree: the shipped
function, a reproduction of the pre-change logic, and an **AST-patched copy of
the shipped source** with only the derivation reverted. The third is what makes
the second trustworthy; a retyped control tests the retyping.

**THE DAG BYTE PIN AGAINST `git show HEAD:` WAS REPLACED, ARGUED IN PLACE
RATHER THAN DELETED.** `_dag3d == _dag3d_head` was the criteria-split pass's
one-time claim that IT had not moved the DAG, hardened into a standing check —
and pinning generated output to whatever HEAD happens to be has both failure
modes at once. It cannot fail once any change is committed (HEAD then carries
it, so it agrees with the code by construction, the defect this file's own
revision selectors exist to avoid), and it fails in the working tree of ANY
deliberate DAG change while naming nothing about what moved. What replaces it
derives the pre-change revision **by AST over the GENERATED text** — never by
substring over the generator, whose new comment records the deleted literal and
would select this commit — and requires the two generated DAGs to differ ONLY
in that one assignment, every other differing line being a comment. It survives
the commit, and it says what moved.

**THE REVERT HARNESS FOUND A DEFECT IN THIS PASS'S OWN TEST CODE THAT READING
DID NOT.** The plant's anchor is the very line the fix introduced, so the one
edit section 4b exists to catch is also the edit that makes the reverted copy
unbuildable — and `None(**kw)` raised `TypeError` at module level, reporting one
traceback where the run owed eleven failures. **That is the seventh time this
project has shipped that shape.** `_run_reverted` returns a named absence now
and `check()` fails on it; the same revert reports 392 passed / 11 failed and
runs to its summary. Two other raise-capable reads were hardened with it: the
`__defaults__` indexing, which raises `IndexError` exactly when a signature
loses a default — one of the things those two checks are for.

**EIGHT REVERTS, EIGHT CAUGHT**, each applied to a `copytree`'d copy with a
realpath preflight asserting the COPY is what imports and
`PYTHONDONTWRITEBYTECODE=1` set; `.git` is symlinked in, read-only, so the
file's git-derived controls resolve. R1 the family filter (11 failures), R2 the
signature default (1), R3 `main()`'s staging name (2), R4 the DAG's (3), R5 a
re-hardcoded RRF weight (4), R6 a re-hardcoded `RRF_K` (2), R7 a reintroduced
`RERANK_RRF_K` (1), R8 `RRF_WEIGHT_TITLE` renamed in config. **R8 is the one
whose failure mode is an ImportError rather than a recorded check**, and it is
inherent rather than a weakness left unaddressed: removing the name from config
makes `agent/retrieval.py` — the module under test — unimportable, so there is
no process in which a check could run. The traceback names the missing constant.

**`tests/test_agent_rrf_config_ownership.py` — 31 checks, ~0.8 s**, the control
the RRF-promotion commit (`09436e0`) left uncommitted at a scratchpad. **THE
PATCH POINT IS ITS REASON FOR EXISTING**: `retrieval.py` does
`from oncotriage.config import RRF_WEIGHT_TITLE`, which BINDS the value into
retrieval's own namespace, so a check written against
`oncotriage.config.RRF_WEIGHT_TITLE` reaches NOTHING and would pass forever.
Section 2 fires that wrong patch point deliberately and requires it to change
nothing; section 3 patches retrieval's own namespace and requires the fusion
order to MOVE. The fabricated rank fixture is built so it CAN flip — all four
ranks are 0, so the k term is identical on both sides and only the weights
decide, 3.0/k against 2.5/k — because a fixture that cannot flip is a vacuous
control. Section 6 closes the gap that sections 1–5 leave (they exercise a
re-derivation, not the shipped node) by requiring the four `score +=` terms
INSIDE `node_hybrid_retrieval` to name their config weight and `RRF_K`, with
the RRF numerator 1.0 the only numeric literal left in any of them, and
`RERANK_RRF_K` absent as a Name AND as a string. **No network, no keys, no
spend, no Qdrant, no model, no corpus, no database and NO GIT** — run green in a
tree with no `.git` at all. It **execs nothing**, so it needs no
`_EXEC_ALLOWLIST` entry, and it writes nothing, so it is not in the collision
matrix (derived: the one repository file it reads,
`oncotriage/agent/retrieval.py`, is written by neither of the suite's two
writers).

**THE DEPLOYED AIRFLOW DAG WAS STALE AND THE REDEPLOY HAPPENED — CLOSED
2026-08-20.** The alias-ownership pass proved the new text valid without
deploying it: generated into a temporary AIRFLOW_HOME and parsed by **Airflow
3.3.0's own `DagBag`** — `import_errors == {}`, `trial_refresh_weekly`
registered, all three tasks (`scrape_and_save`, `rebuild_index`,
`verify_index`), tags `['production','trialmatch']`, `NullTimetable`
(`AIRFLOW_DAG_SCHEDULE = None`) — **15,049 bytes, sha256
`32003ba494de1ef5…`**, a NEW sha256, which is pass 20f-3 item 8's acceptance
criterion. It then recorded the deployed file as still 14,480 bytes /
`6e3c8fdaf44a…` and left the redeploy as a follow-up.

**THAT FOLLOW-UP IS DONE AND THE NOTE WAS STALE IN THE WRONG DIRECTION** — it
described the deployed file as carrying a defect that had already been removed
from it, which is the reading that sends the next person to re-run a command
that would change nothing. MEASURED 2026-08-25 on this machine, by reading the
file rather than by re-running the generator:
`{airflow_path}/dags/trial_refresh_weekly.py` is **15,049 bytes, sha256
`32003ba494de1ef566534863cd107d736ec34b3e6d5b8f4efa6168d6c0213591`, mtime
2026-08-20T21:43:35** — byte-identical to what the pass generated and verified,
so the deployed DAG IS the DagBag-parsed one. The single remaining occurrence of
`trial_criteria_` in it is on line 262, inside the COMMENT recording the fix
("It used to read \"trial_criteria_\" + timestamp"); `staging_name` on line 269
is `alias + "_" + timestamp`, which is the fix. The file lives outside the
repository, so nothing in git records this and this paragraph is where it lands.
The redeploy command, if the generator ever moves again, is unchanged:
`rm "{airflow_path}/dags/trial_refresh_weekly.py" && python "23- Airflow DAG.py"`.

**THREE STALE COUNTS IN THE RUN BLOCK WERE CORRECTED, EACH RE-MEASURED**:
`test_fhir_ecog_surfacing.py` 105 → **108**, `test_storage_ecog_logging.py`
104 → **155**, `test_indexer_admission_filters.py` 175 → **403** (359 before
section 4b). The 175 in the admission pass's own account further up is left as
written, per the rule that a past-tense account keeps its wording; the run block
is the current-state owner.

### The cross-encoder's sequence limit belongs to its checkpoint (the sequence-limit pass)

**`512` WAS A BARE LITERAL AT TWO TOKENIZER CALL SITES AND NOTHING TIED IT TO
THE CHECKPOINT.** `config.CROSS_ENCODER_MAX_LENGTH` is that number now, declared
as the module-level statement **immediately after** `CROSS_ENCODER_MODEL`,
because it is a property OF that checkpoint: MedCPT is BERT-shaped and its
weights carry 512 learned position embeddings. **Value-preserving** — 512 stays
512, `python fixture_replay.py` is **12/12 clean, exit 0, no recapture and zero
`CONFIG MOVED` lines** — and no model was downloaded, no network call billed,
nothing spent.

**THE SECOND CALL SITE WAS NOT IN THE BRIEF AND IS THE MORE INTERESTING ONE.**
A repo-wide grep found `max_length=512` in `oncotriage/agent/models.py` (the
Stage 3 scorer, the one everybody knows about) **and in
`oncotriage/retrieval/index_validator.py`'s check 9 cross-encoder smoke test**.
That is the exact shape pass 20c-3a removed when this same module carried its
own `SparseTextEmbedding`: **a validator holding a private copy of a pipeline
constant cannot detect the drift it exists to catch**, because both halves of
its comparison move with the copy. It reads `CROSS_ENCODER_MAX_LENGTH` now, as
the **bare name** rather than `config.X`, because `stage1_index_health()` binds
a function-local called `config` — check 2g's trap, hit for the third time in
this project and avoided by checking rather than by reasoning.

**WHY DRIFT HERE IS SILENT, which is the whole argument for the pairing.** Every
tokenizer call passes `truncation=True`, so transformers does exactly what the
number says and raises nothing. Set the limit **below** the checkpoint's real
budget and Stage 3 keeps scoring, `node_cross_encoder_rerank` keeps sorting, the
Stage 4 quality gate keeps cutting — the cross-encoder is simply reading less of
every trial than it could, and the only symptom is a worse ranking. Set it
**above** and the failure is loud but late: an `IndexError` out of the embedding
lookup, per patient, thirty frames inside Stage 3. Same absence-of-any-error as
the tokenizer/weights hazard pass 20f-2 closed, one level down.

**THE BRIEF SAID TO CHECK `tokenizer.model_max_length`, AND MEASURING THE
CHECKPOINT SAYS THAT CHECK WOULD BE PERMANENTLY VACUOUS.** Read off the cached
`ncbi/MedCPT-Cross-Encoder` on 2026-08-21, from the files on disk, and then
confirmed by loading the tokenizer offline:

| declaration | value |
|---|---|
| `tokenizer_config.json` → `model_max_length` | `1000000000000000019884624838656` — transformers' `VERY_LARGE_INTEGER`, i.e. **no limit declared at all** |
| `config.json` → `max_position_embeddings` | **512** — the fact that makes 512 correct |

So a tokenizer-only check takes the "undeclared" branch on **every load of the
shipped checkpoint** and verifies nothing, forever, while looking exactly like a
check that passes. **The WEIGHTS are what verify this number.**
`_verify_cross_encoder_sequence_limit()` is therefore called from **both**
MedCPT factories — the tokenizer half is kept so a checkpoint that *does*
declare a limit is covered, and so that a tokenizer contradicting its own
weights is caught — and `tests/test_agent_cross_encoder_sequence_limit.py`
section 3 pins the asymmetry so nobody later deletes the load-bearing half.

**UNDECLARED IS NOT A MISMATCH, and that distinction is why this is not one
`!=`.** A placeholder means "this checkpoint states no limit", which has a
different remedy from "it states a different one" — nobody can make a vendor
declare one — so it is **counted** in `CROSS_ENCODER_LIMIT_DEGRADATIONS` (item
11a's line: third-party data counts, configuration raises) and never silent. A
missing attribute and a non-integer get their own keys, and a **`bool` is
`unreadable` rather than the integer 1**, because `True == 1` would otherwise be
reported as a mismatch against 512 and send an operator to the wrong constant.

**A DECLARED MISMATCH RAISES, IN BOTH DIRECTIONS.**
`CrossEncoderLimitMismatchError` is a `RuntimeError` subclass and deliberately
not a `ValueError`, on the `UnknownModelPricingError` / `IndexVerificationError`
precedent, so a stray `except ValueError` around a model load cannot eat it. It
fires at first model load — before Stage 3 scores anything and before Stage 5
spends a cent — so it costs one run and fixes the class. A deliberately smaller
budget is a **second named constant** with its own measurement, on the `RRF_K`
precedent, not a quiet inequality.

**NOTHING WAS ADDED TO A DEFERRED PATH.** Both calls sit **below** their
factory's `_DEFER_LOCAL_MODELS` early return, and an installed override
short-circuits the factory in `_resolve` before it runs. **That last fact is
also why the brief's suggested test harness could not work**: `deps.set_override
(deps.MEDCPT_TOKENIZER, stub)` can never reach a check that lives inside the
factory. The verifier is a pure function of its argument and is driven directly,
which is the natural control for a pure function; section 5 of the test installs
a stub carrying a deliberately wrong `model_max_length` and **proves** the
override never reaches the check rather than assuming it.

**THE 1600-CHARACTER TRIAL-TEXT CAP IS DERIVED FROM 512 AND STAYS A LITERAL,
argued rather than left as an oversight.** `agent/retrieval.py` builds each
`trial_text` as `title + criteria_text[:1600]`, a number derived against 512
(3–8 token queries leave ~500 tokens ≈ 1850 chars, 1600 for margin). Computing
it from the constant would silently couple a **paid artefact** to a config edit:
that string is what `score_pairs` is handed, so changing it changes every
recorded cross-encoder digest and all twelve fixtures would need recapturing.
Whoever moves `CROSS_ENCODER_MAX_LENGTH` re-derives it by hand and pays that
bill — which is the same bill the constant's own comment already names. The two
prose restatements of "512" in that file now name the constant instead of the
number, on `/pipeline/info`'s `MATCHING_MODEL` precedent.

**THE LIMIT IS RECORDED IN FUTURE FIXTURE CAPTURES**, on
`fixtures/capture.py`'s own stated doctrine: only what is in `tunables` is
compared by `diff_tunables()`, and this constant changes every ranking, so
without it an edit would reach a replay as an unexplained `cross_encoder`
difference with no cause attached. **Future captures only** — `diff_tunables()`
iterates the keys the FIXTURE recorded, not the keys the dict declares — so
nothing on disk moved, which is what the 12/12-without-recapture result above
measures. **Stop condition checked before it was relied on:** no fixture field
records a sequence limit under any name, so this pass could not have invalidated
one.

**THE STANDING GUARDS ARE TWO AND NEITHER REPLACES THE OTHER.**
`tests/test_package_invariants.py` **section 2f(iii)** (13 checks; **247 →
260**) is the structural half — the constant exists, is a positive int literal,
is the statement immediately after the checkpoint name, every `max_length=` in
the package is handed it in either reference form, there are exactly two of
them, and both factories call the verifier. It is its own section rather than
four more checks inside 2f(ii) **so a failure line can distinguish "somebody
re-hardcoded the model name" from "somebody re-hardcoded the sequence limit"**,
which are different edits with different fixes.
`tests/test_agent_cross_encoder_sequence_limit.py` (**42 checks, ~1.1 s**) is
the behavioural half. An AST scan cannot see a verifier that has stopped
verifying; a runtime check cannot see a second literal never routed through it.

**NINE REVERTS, NINE CAUGHT, every one a RECORDED failure with a summary rather
than a traceback**, each applied to a `copytree`'d copy with `PYTHONPATH`
pointed at it, a realpath preflight asserting the COPY is what imports and
`PYTHONDONTWRITEBYTECODE=1` set; both shipped files sha256-unchanged afterwards.
The literal restored in `models.py` (2 failures) and in the validator (2), each
factory's verifier call deleted (1 + 1 each file), the mismatch counted instead
of raised (7), the placeholder compared blindly (4), the `bool` guard removed
(2), the constant moved away from the checkpoint name (2), and the counter
unregistered from the run-end report (4).

**THE HARNESS FOUND THREE DEFECTS THAT READING DID NOT, and two of them are in
this pass's own test file.**

- **`2q` aborted the whole file** with `TypeError: '<' not supported between
  instances of 'tuple' and 'str'`. `verify()` returns a string for the three
  states and a TUPLE for a raise, and `sorted()` over a set holding both raises
  — **exactly when a defect makes an arm raise that should not**, i.e. precisely
  when the file owes recorded failures. **The eighth time this project has
  shipped that shape.** `marker()` and `at()` are the fix; R6 now reports 4
  failures and a summary instead of one traceback.
- **Section 6 indexed `degradation._REGISTRY[...]` and `_snap[...]` directly**,
  which raises `KeyError` in the one state 6a exists to catch. `.get` now.
- **`2q`'s first draft expected 3 distinct outcomes and there are 4** — a
  mismatch is a fourth OUTCOME, not a fourth spelling of one of the three return
  values, which is the distinction the section exists to hold. Recorded rather
  than quietly corrected: it is how a non-degeneracy probe fails when its author
  counts the vocabulary instead of the arms.

**AND ONE IN THE REVERT HARNESS ITSELF, worth recording because it produced a
false green in the honest direction.** Its first version copied only
`oncotriage/` and `tests/`, and `test_package_invariants.py` walks the WHOLE
tree — section 5 over every `.py`, check 2h's corpus including `docker/` and the
numbered entry points — so all five invariants reverts aborted with no summary
and read as "the check did not fire". They fire; the tree was truncated. A
revert reporting MISSED can mean the check is weak **or** that the revert never
ran, and those are not the same finding — pass 20f-1's lesson, met again.

**THREE FIELDS WERE ADDED TO `LOGGABLE_FIELDS`** —
`max_length_configured`, `max_length_declared`, `max_length_source`. All three
are model geometry (a token count, an attribute path) and none can carry a
patient, a trial or a diagnosis; they are fields rather than message text
because section 6c of `tests/test_observability_logging.py` forbids
interpolating data into a message, and because "unverified" is only actionable
when the record says *which* half was unverified and against what number.
`CROSS_ENCODER_LIMIT_DEGRADATIONS` is the **twentieth** counter in
`oncotriage/degradation.py`'s run-end registry.

**VERIFIED BY RUNNING**, against the real checkpoint loaded from the local HF
cache with `HF_HUB_OFFLINE=1` (no download): the tokenizer half reports
`undeclared` and counts, the weights half reports **`verified`**, and
`score_pairs` returns `[8.8584, -15.9533]` for a relevant and an irrelevant
trial. The production `inferences.db` sha256 is unchanged.

### A dedicated warmup writes the cache, and nothing is sent without one (the cache-warmup pass)

**PER-TRIAL MODE SHIPPED WITH A ONE-THEN-REST SCHEDULE AND THE FIRST REAL
TRIAL CALL WAS THE CACHE WRITER.** Two things were wrong with that and only one
of them is about money. **If that first call exhausted its transport retries
the remaining N-1 went out against a cache nothing had written**, at full input
price, and nothing in the record separated that from a provider that does not
cache — a cost leak reported as an ordinary patient. And **a real trial doubled
as cache infrastructure**, so "this trial could not be evaluated" and "the
cache was never established" were one event with one remedy when they are two
findings with two.

**THE RULE IS CACHE-OR-NOTHING.** `call_matching_model_warmup` carries the
identical system message — the shared prefix, byte for byte — with the smallest
user message and output budget the provider permits
(`MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE`,
`MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS = 1`). It is awaited alone; then
**ALL** the trial calls dispatch through the executor under
`MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS`, **none held back**. LIFO consumption
and the node-thread merge are unchanged.

**A WARMUP THAT CANNOT BE ESTABLISHED ISSUES NO TRIAL CALL AT ALL.**
`pending.clear()` is the one line that makes "there is no uncached fallback
anywhere" true, and the patient fails through the existing zero-success floor
so `MAX_LLM_CLASSIFIER_RETRIES` sees it and the batch checkpoint resumes it.

**THE FLOOR HAD TO STOP TESTING `calls_made`, AND THAT IS A CORRECTION RATHER
THAN A DETAIL.** The warmup is a billed call and is counted in `calls_made` —
correctly — so a floor that tested it would be satisfied by a successful warmup
and would **stop firing for the total outage it exists to catch**: every trial
call failing after the warmup answered would have been recorded as a patient
with no matches and no error. `per_trial_succeeded` counts TRIAL calls that
returned a response, and that is what the floor asks. Control c21 plants the
old test back.

**NO THIRD RETRY BUDGET WAS INVENTED.** The warmup's coverage is
`OPENAI_SDK_MAX_RETRIES` inside the SDK, with the SDK's own backoff honouring
Retry-After, and `MAX_LLM_CLASSIFIER_RETRIES` above the node, which re-enters
it through `route_after_llm_classifier`. Check 1k asserts by AST that the
warmup has exactly one call site and that it is inside no loop.

**THE PROVIDER'S REFUSAL OF THE REQUEST SHAPE IS DETECTED, NOT ASSUMED.**
A reasoning model bills reasoning against the same ceiling this call sets to 1
and may refuse a value that small. `classify_warmup_rejection` requires **both**
a 400 **and** a message naming the parameter — a context overflow is also a 400
and will fail every trial call too, so falling back for it would replace one
clean failure with N identical ones. Two members
(`minimal_output_rejected`, `prompt_cache_key_rejected`); both fall back to the
retired one-then-rest schedule and record the reason in
`PER_TRIAL_WARMUP_DEGRADATIONS`, which reaches the run-end degradation report.
The cache-key case additionally **drops the hint for the wave**, or every
fallback call would be refused for the parameter that was just refused.

**THE ROUTING HINT IS SENT IN PER-TRIAL MODE ONLY.** `prompt_cache_key` is a
declared parameter of `chat.completions.create` in the installed SDK (openai
1.99.9, measured) and does not enable caching — it asks the provider to route
requests sharing a prefix to one machine, which is what N simultaneous requests
need. `per_trial_prompt_cache_key()` derives it from the system prompt's
sha256, namespaced, so the warmup and its wave always share a key and two
patients never do. It reaches `call_matching_model` through a
**`**_extra_kwargs` expansion that is empty when no key is given** — NOT
`openai.NOT_GIVEN`, which is equivalent on the wire and would still change the
kwargs dict `oncotriage/fixtures/capture.py` records and
`oncotriage/fixtures/replay.py` digests, costing a re-capture of all twelve
fixtures.

**THE WARMUP'S LEDGER ROW IS MARKED AND CANNOT BE MISTAKEN FOR A TRIAL.**
`warmup: True` (present on that row and no other, the absent-rather-than-empty
convention `unconsumed` already follows), `trials: 0`, `entries_emitted: None`,
`depth: None` — zero is a real split depth and the warmup has no place in that
tree. Its tokens are visible and inside the patient's totals; the answering-
model check runs on it, so **a mismatched judge fails the patient before the
wave for the price of one one-token request**. `_account_unconsumed()` is
provably unaffected: the warmup is consumed on the node thread before
`_prefetched` is populated, asserted by check 3j and controlled by c26.

**PER-TRIAL MODE AGAINST A PROVIDER WHOSE WARMUP IS NOT BUILT IS REFUSED BY
NAME, BEFORE ANYTHING IS RENDERED OR SPENT.**
`assert_per_trial_provider_supported()` has one owner and two call sites — the
node top, on `PerTrialParallelismError`'s footing, and the warmup itself, which
is public. Without the first, the warmup's own refusal is caught by the
dispatch's `except`, classified as a transport failure and retried
`MAX_LLM_CLASSIFIER_RETRIES` times, so a configuration defect arrives as three
identical failed patients (control c27). The Bedrock and Anthropic branches are
**left explicit and unbuilt**: Bedrock owns its own caching controls, and
Anthropic warms its cache the other documented way entirely — a placeholder
message with an explicit `cache_control` breakpoint.

**VERIFIED BY RUNNING.** `tests/test_agent_stage5_per_trial_calls.py`
**206/0** (was 139), with **ten new controls (c18-c27) all firing**;
`tests/test_agent_bedrock_adapter.py` **275** (was 273);
`tests/test_package_invariants.py` **260/0/0**;
`tests/test_degradation_counter_readers.py` **138**; CI bucket A **61/61**;
`tests/run_serial_tests.py` **5/5** with `oncotriage/config.py` and
`oncotriage/registries/cancer_code_registry.py` confirmed byte-identical
afterwards; `python fixture_replay.py` **12/12 clean, exit 0, with no recapture
and zero CONFIG MOVED lines**; and the production `inferences.db` sha256
**unchanged** — `ab1403e3…`, 90,185,728 bytes. **No money was spent and no
migration was run.**


### The per-trial dispatch layer stops leaking money on its failure paths (the dispatch-hardening pass)

**THREE DEFECTS IN `oncotriage/agent/evaluation.py`'s PER-TRIAL DISPATCH, ALL
THREE ON PATHS THE HAPPY CASE NEVER TOUCHES, AND NONE OF THEM RAISED.** No
schema change, no migration, no billed call: `python fixture_replay.py` is
**12/12 clean, exit 0, with no recapture**, and grouped mode AND the healthy
per-trial path are **byte-identical to `git show HEAD:`** — the same request
dicts, field for field, and the same published result. **The per-trial arm is
compared as a canonically-ordered SET and that is not a weakening**: a recording
stub appends as calls ENTER, and with a 4-way pool that order is the pool's
rather than the node's, so comparing arrival sequences would be comparing the
scheduler. Stable across three processes: grouped `8ad2b29dc646`, per-trial
`8941d5e7111a`, both arms equal in every one.

**1. THE FALLBACK'S CACHE WRITER WAS ISSUED AND NEVER READ.** Cache-or-nothing
is a property of the NODE, not of the dedicated warmup: a wave may go out only
behind a request that provably wrote the shared prefix. When the provider
refuses the warmup's SHAPE the patient degrades to the retired one-then-rest
schedule, which has a writer of its own — the first trial call, held back and
awaited alone — and that writer's outcome was filed into `_prefetched` and
never inspected. A writer that exhausted its transport retries therefore
released **N-1 full-price requests against a prefix nothing had written**, the
exact leak the warmup design exists to prevent, reached through the door the
design opened. The only trace was one isolated per-trial failure among N-1
ordinary successes, which is what an unlucky trial looks like; no counter
moved, no error was returned, and the patient was recorded as a clean run.

`_writer[0] == "error"` is now read — a TAG ON A VALUE, not something a `try`
could see, because `_issue` returns its exception rather than raising, which is
the contract that makes the wave's merge deterministic. On error the node
clears `pending`, sets `_warmup_error`, empties `_rest`, and counts
`PER_TRIAL_WARMUP_DEGRADATIONS` under
`WARMUP_FALLBACK_WRITER_FAILURE_KEY_PREFIX` + the exception type. **A SEPARATE
PREFIX FROM `failed:`, argued at the constant**: a run that never attempted the
fallback and one that attempted it and lost are different findings, and only
the second says the rejection classification is worth revisiting.

**THE FAILED OUTCOME IS NOT FILED, and that is not tidiness.** Left in
`_prefetched` it would be folded a second time by `_account_unconsumed` under
`abandoned:` — one request reported as two findings — and it is not abandoned:
it was read, here, and is the reason the patient is failing.

**THE FLOOR'S SENTENCE HAD TO STOP LYING.** `_warmup_error` is deliberately the
mechanism (same state, same floor, same API-error return, same resume) rather
than a second failure shape for every consumer to agree about — but the
existing message reads "no trial call was issued", which is exactly false when
the writer failed: one trial call was issued and reached the provider.
`WARMUP_SOURCE_WARMUP` / `WARMUP_SOURCE_FALLBACK_WRITER` is a closed
two-member vocabulary read by the floor and by nothing else, and it is logged
under the already-allowlisted `reason` field rather than widening
`LOGGABLE_FIELDS` for a second name for the same kind of fact.

**2. TWO CHUNKS COULD SHARE ONE DISPATCH KEY.** `_prompts` and `_prefetched`
are keyed by `_chunk_key` — the chunk's nct_ids — and a repeat in
`filtered_trials` is three faults at once, none of which raises: the second
`_prompts` write wins, so **both** requests carry the second trial's rendered
block and the first trial's criteria are never sent while its verdict is filed
anyway; the second `_prefetched` write wins, so the send loop's second pop finds
nothing and `_obtain` issues a **live, uncached, unbounded-by-the-pool** request
for a response already paid for; and the overwritten response is folded by
nobody, because `_account_unconsumed` folds what is LEFT in `_prefetched`.
**The per-INDEX guard cannot see it** — it asks whether chunk *i* holds
`trials[i]`, which is TRUE for both members of a repeat.

Unreachable today (Stage 2 de-duplicates by nct_id) is **not** impossible: that
is a property of a stage three modules away that nothing here holds. The guard
is one `Counter` over N 1-tuples, raises `PackingBlockMismatchError` **naming
the repeated ids**, and fires before the warmup — so a repeat costs nothing at
all rather than one infrastructure call plus an extra billed trial call.

**AND IT IS WHAT CLOSES `_obtain`'s LIVE PATH IN THIS MODE, which is now
ASSERTED RATHER THAN REASONED.** With keys unique every dispatched chunk is
filed and popped exactly once, and every per-trial chunk is a SINGLETON — which
the reactive splitter refuses to halve, because `len(chunk) == 1` is its floor,
above the split. Measured by driving every response to
`finish_reason == "length"`: four trials, four wave calls, **zero** truncation
splits, every trial at the truncation floor. And structurally, so an edit that
moved the floor below the split fails even on a run in which nothing truncates
— the check is **scoped to the reactive branch**, because the node calls
`_split_in_half` TWICE and the other call is the proactive splitter, which
per-trial mode replaces outright.

**3. Ctrl-C MID-WAVE KEPT BUYING RESPONSES.** `ThreadPoolExecutor` as a context
manager calls `shutdown(wait=True)` with `cancel_futures` defaulting to FALSE,
so an exception out of the result loop — KeyboardInterrupt included — let every
QUEUED call run to completion before the exception surfaced. Minutes of
continued billing that read as a hang. The executor is now shut down explicitly
in a `finally` with `cancel_futures=True`.

**THE LIMIT IS STATED AT THE CODE RATHER THAN IMPLIED**: an HTTP request already
in flight is NOT interruptible, so this cancels only what has not STARTED and
`wait=True` then blocks for at most `_bound` calls — one request's duration, not
the whole queue's. `wait=False` would return sooner and buy nothing:
`concurrent.futures.thread` registers an atexit hook that joins every worker
anyway, at interpreter shutdown, with no traceback to explain it.

**A CANCELLED CALL IS BILLED BY NOBODY AND CANNOT BE MISCLASSIFIED**, by
construction rather than by a filter: only a RESOLVED future is filed into
`_prefetched`, and the node publishes no result at all on this path, so there is
no ledger and no token total that could carry a request that was never issued.
The interrupt is re-raised unchanged, which is what an interrupt is for.

**`tests/test_agent_stage5_per_trial_calls.py` — 206 -> 239 checks, ~10 s**,
still bucket A, still no network, no keys, **no spend**. Three new sections (2b,
3c, 3d) and four new controls (c28-c31). **FOUR REVERTS, FOUR CAUGHT**, each in
a `copytree`'d copy with `PYTHONPATH` pointed at it, a realpath preflight
asserting the COPY is what imports and `PYTHONDONTWRITEBYTECODE=1` set: the
un-inspected writer (9 recorded failures), the removed uniqueness guard (3), the
removed `cancel_futures` (2) and the `with`-form executor (3). Every one ran to
its own summary; none aborted.

**THE INTERRUPT IS RAISED INSIDE A WORKER, NOT AS A REAL SIGNAL, and the reason
is not squeamishness.** `_issue` catches `Exception`, so a BaseException that is
not an Exception travels out of the worker, into the future, and is re-raised on
the node thread at `future.result()` — byte for byte the propagation a real
SIGINT produces there. A real `os.kill(..., SIGINT)` in a process that also runs
sixty other test files in CI bucket A is a way to abort the run rather than
measure it.

**AND THE REVERT HARNESS FOUND A DEFECT IN THIS PASS'S OWN TEST CODE THAT
READING DID NOT.** The interrupt probe first used `run_node`, which clears the
dependency override in its `finally` the instant the node returns. Under the
`with`-form revert — the very defect shape the section exists to catch — worker
threads are still running at that moment, and their next
`deps.get_openai_client()` resolves to **whatever is installed next**: the
following scenario's stub, whose request count they corrupt (control c1 failed
for that reason and for no other), or, with nothing installed, a **REAL client
built from the real credentials file**. A test that can make a billed call when
the code under test regresses is not a stub-only test. The probe now drives the
node itself, MEASURES the leak at the moment the node returned, and only then
joins the survivors and clears the override — with `deps.peek(OPENAI_CLIENT) is
UNSET` asserted afterwards as the no-spend tripwire, because a real client that
had been built would be cached there.

**VERIFIED BY RUNNING.** `tests/test_agent_stage5_per_trial_calls.py`
**239/0**; `tests/test_package_invariants.py` **260/0/0**; CI bucket A
**61/61**; `tests/run_serial_tests.py` **5/5** with `oncotriage/config.py` and
`oncotriage/registries/cancer_code_registry.py` confirmed restored;
`python fixture_replay.py` **12/12 clean, exit 0, no recapture**; grouped mode
and the healthy per-trial path byte-identical to `git show HEAD:`; and the
production `inferences.db` sha256 **unchanged** — `ab1403e3…`, 90,185,728 bytes.
**No money was spent and no migration was run.**

**WHAT IS NOT DONE, NAMED RATHER THAN LEFT TO BE DISCOVERED.** There is still no
PROCESS MEMO of a warmup rejection: every patient re-discovers it, paying one
refused warmup each. A memo is a behaviour change with its own argument (a
transient 400 would disable the warmup for the life of the process) and belongs
to a pass that can measure it. And an interrupt still discards the responses
already resolved into `_prefetched` — nothing catches KeyboardInterrupt, which
is correct, and writing a ledger from a signal handler is a separate decision.


### The operator controls reach the ablation study and the grouped arm (the operator-control pass)

**TWO SURFACES ROUND THREE LEFT UNCOVERED, AND ONE OF THEM IS THE HARNESS THE
THREE-ARM MIGRATION MEASUREMENT RUNS THROUGH.** `oncotriage/ablation/study.py`
had none of the controls `25- Batch Runner.py` has -- no run lock, no stop
switch, a `with ThreadPoolExecutor` that DRAINED the rest of a configuration on
any exception, a `KeyboardInterrupt` that was caught and NOT re-raised so the
loop carried on to the NEXT configuration, and no SIGTERM disposition at all --
and the GROUPED send arm, which is the one that ships
(`MATCHING_PER_TRIAL_CALLS_ENABLED` is False), consulted the Stage 5 shutdown
flag nowhere.

**NO BILLED CALL WAS MADE.** `python fixture_replay.py` is **12/12 clean, exit
0, with no recapture**, the production `inferences.db` sha256 is unchanged, and
the production `ablation_results.db` sha256 is unchanged -- the new
`ablation_runs.status` column appears there on the next run that opens it,
which is what the additive mechanism is for.

**1. THE GROUPED ARM IS GATED, AND THE DRAIN IT LEFT OPEN WAS MEASURED RATHER
THAN ARGUED.** The gate is in `_obtain`, immediately above the live call and
BELOW the prefetched branch -- so only a request that would really reach the
provider is declined, and a per-trial response already paid for and sitting in
`_prefetched` is still consumed. Driven through the real node with a stub
client at `MAX_TRIALS_FOR_EVALUATION = 15`:

| criteria chars/trial | 1000 | 2000 | 4000 | 6000 | 8000 | 12000 |
|---|---|---|---|---|---|---|
| chunks per patient | 1 | 2 | 3 | 4 | 5 | 5 |

so a grouped patient in flight when an operator pressed Ctrl-C carried on
issuing **up to four further full-price requests**, and was then published as a
SUCCESS carrying all fifteen verdicts. Measured both arms at 15 trials of 8000
characters: **4 further requests before the gate and 0 after it**, with the
patient failing honestly instead. `_on_done` checkpoints a success, so the
published-partial shape is the c33 defect reached from the arm that ships -- a
resume would skip that patient forever.

**`SHUTDOWN_SKIP_SEND_KEY_PREFIX` IS A THIRD PHASE AND NOT A REUSE OF `wave:`.**
`wave:` is a per-trial worker declining an already-SUBMITTED task, N at once,
off the node thread; `send:` is the node's own thread declining the next
SEQUENTIAL call. `SHUTDOWN_SKIP_KEY_PREFIXES` is the closed tuple that makes
"these three partition the places a request can be declined" checkable.

**CHECK 8b-r WAS REWRITTEN FROM A PINNED LIMIT TO THE NEW CONTRACT**, and the
note it carried is kept at the check as the record of what changed. It read
"GROUPED MODE IS NOT GATED, and that is a stated limit rather than an
oversight". The limit was real and it covered the arm that ships. Four checks
replace it (8b-r/s/t/u), plus **8b-v, the non-degeneracy probe without which
`len(requests) == 0` would also be satisfied by a node that raised before its
first call**, and control **c36**, which plants the gate out of an in-memory
copy and requires all `_BASE_CHUNKS` requests to go out and the patient to be
published as a success.

**THE `docker-compose.yml` ARITHMETIC COMMENT WAS WRONG IN A WAY THE
MEASUREMENT EXPOSED, and correcting it is worth more than the gate.** It said
`stop_grace_period: 620s` "COVERS THE GROUPED ARM ... one request per patient,
so one in-flight call". Grouped mode is one request per **chunk**, so a
`docker stop` on `fastapi` can SIGKILL it with up to four further full-price
requests still to issue -- 2400 s of drain against a 620 s grace. **The number
is NOT raised**, because this service has no shutdown gate at all and giving it
one is the fix; the arithmetic is now written down beside the follow-up it
already was. The comment's OTHER claim -- that the batch runner's handler bounds
its drain "whichever arm it is on" -- was false before this pass and is true
after it.

**2. THE ABLATION STUDY HAS THE FIVE CONTROLS, ADAPTED RATHER THAN COPIED.**

| control | how it differs from the batch runner's, and why |
|---|---|
| **the run lock** | keyed on the **checkpoint FILE**, not its directory. The batch runner has one checkpoint so its key is the directory; this one's checkpoint follows `--db` (pass 20f-3), so two independent `--db` studies in `/tmp` must not refuse each other. And the lock **filename prefix** differs -- with no `--db` the study's state directory IS `paths.checkpoint_path`, so a shared lock file would make a batch run and an ablation study block each other |
| **the stop sentinel** | **DERIVED from the checkpoint path** (`<stem>_STOP`), so it is per database exactly as the checkpoint is, and **deliberately not named `STOP`**: sharing the batch runner's name would make each harness's stale-sentinel refusal fire for a request made of the other program and name the wrong `--clear-stop` |
| **the granularity** | polled between **configurations** AND between **pairs**. Between-configurations alone would be useless: one configuration is `sample_size` live calls, ~30 minutes at the default and one seventh of a full study. The coarse poll's job is that a stop in config 3 of 7 leaves 4-7 with no `ablation_runs` row to explain later |
| **the terminal state** | `ablation_runs` **had no status column and no status convention at all** -- the brief's "per the ablation database's own status conventions" named something that did not exist. `RUN_STATUSES` is `RUNNING / COMPLETE / STOPPED / KILLED`, additive, NULL for pre-migration rows and never backfilled |
| **`_finalize_run`** | takes `status` as a **required** argument with no default (`empty_database(db_path, flag)`'s precedent), **reads `rowcount`** (an `UPDATE` against an absent id succeeds and updates nothing), **refuses an unrecognised status rather than storing it**, and **never raises** -- it runs inside two shutdown handlers whose job is to leave a record |

**WHY THE STATUS IS LOAD-BEARING RATHER THAN TIDY.** `generate_summary()`
reports the LATEST `ablation_runs` row per config and joins its results by
`run_id`. A configuration cut short IS the latest for its config and its
results are a PREFIX of the sample -- so every mean over it was printed beside
the other configurations' full-sample means as though comparable, with nothing
in the database able to say which rows those were.
`_summary_status_warning` names them **between the table and the deltas**;
it does **not** filter them, on `print_cost_by_model`'s "<- A FLOOR, NOT A
TOTAL" precedent: filtering would change which rows every historical comparison
rests on, silently, and would answer a partial configuration with nothing at all.

**"A STOP WAS SEEN" IS NOT "THIS CONFIGURATION WAS CUT SHORT", AND THE NAIVE
PORT REPRODUCED A DEFECT THIS PROJECT HAD ALREADY FIXED ONCE.** The first
version wrote `RUN_STATUS_STOPPED if STOP_SWITCH.requested`. That is the
question the pre-migration pass had to remove from `oncotriage/batch/runner.py`
-- whether a sentinel was SEEN, rather than whether the work was COVERED. A
stop arriving while every pair of a configuration is already IN FLIGHT lets all
of them finish: nothing cancelled, nothing unsubmitted, results the WHOLE
sample. **Found by running**: the resume scenario reported `full_pipeline`
STOPPED and could never repair it, because a resume SKIPS a configuration whose
pairs are all checkpointed and therefore never writes a COMPLETE row for it --
so `_summary_status_warning` would have warned about a prefix that did not
exist, permanently. The status is now `pairs_unsubmitted == 0 and
config_cancelled == 0`, and the STUDY-level `stopped` is `not study_covered`
for the same reason.

**3. ONE HANDLER FOR ALL THREE ABRUPT PATHS, AND THAT WAS ALSO FOUND BY
RUNNING.** The first version put the finalize and the closing block in the
`except KeyboardInterrupt`. **`except KeyboardInterrupt` does not catch
`SystemExit`**, which is what the entry point's SIGTERM handler raises -- so a
`docker stop` exited 143 with the open configuration still reading `RUNNING`
and printed no closing block at all. Measured, then moved: the outer
`except BaseException` finalizes the open configuration KILLED, prints the
block, closes the tracking run FAILED and re-raises. `STUDY_STATUS_CRASHED` is
separate from `STUDY_STATUS_INTERRUPTED` because both reach that handler and
only one is a defect.

**THE COUNTERS ARE HOISTED ABOVE THE OUTER `try`** precisely because that
handler now reads them: an exception in the first few statements (tqdm's
constructor is one) would otherwise meet an unbound local and replace the
study's diagnosis with a `NameError` about a counter.

**`print_study_close` IS ONE TEXT WITH TWO CALLERS**, and it is where the
study's three counters are read. Before it existed the Ctrl-C path skipped the
closing block entirely, so an interrupted study reported **none of its
degradations** -- which is the whole reason those counters have a reader in
that file at all (`oncotriage/degradation.py` excludes them by name).

**`--summary-only` IS EXEMPT FROM THE STALE-SENTINEL REFUSAL, and the exemption
is as narrow as it can be.** That mode runs nothing and bills nothing, so the
refusal's premise is false of it -- and its remediation would tell an operator
to delete a sentinel they had not withdrawn just to LOOK at what the stopped
study produced, making the natural next command after a stop the one command
that un-stops the next study. **`--fresh-start` puts the refusal back** even
combined with it, because that flag deletes the resume state whatever else the
invocation does.

**THE ENTRY POINT CALLS `parse_args()` TWICE, AND IT IS ARGUED.** The lock key
depends on `--db` and the lock must be held before `main()` runs its preflight
and its `--fresh-start`, but `main()` owns its own parser (unlike the batch
runner, whose guard does). argparse is a pure function of argv, so the second
parse cannot disagree with the first, and a bad argv exits 2 having touched
nothing. Hoisting the whole parser into the guard would change `main()`'s
signature, which is a redesign of a file whose contract is that it takes none.

**4. `clear_stop_switch` NO LONGER RAISES, AND ITS RETURN STOPPED BEING A
BOOL.** `path.unlink()` on a checkpoint directory the run can read and cannot
write raises `PermissionError`, uncaught -- the operator's diagnosis was a
traceback ending in `Errno 13`, printed INSTEAD of the run they asked for. It
catches `Exception` and not `OSError`, and the difference is a real case:
`stop_switch_path()` reads `paths.checkpoint_path`, which globs the sibling
tree and raises a plain `RuntimeError` when it matches nothing or several.

**THE BOOL WAS THE DANGEROUS HALF.** `False` meant "there was nothing to
clear", and once the unlink could fail it would have meant that AND "there is
one and I could not remove it" -- with one line of entry-point output covering
both. `--clear-stop` deliberately SKIPS the stale-sentinel preflight, so a
failed clear reported as "nothing to clear" starts the run with the sentinel in
place, trips it at the first completed patient, and stops again after billing
that patient. `STOP_CLEAR_OUTCOMES` is the closed three-member vocabulary and
both entry points **refuse to run** on `STOP_CLEAR_FAILED`.

**5. A COUNTER-REGISTRY CONFLATION WAS FOUND BY READING, NOT BY A FAILING
TEST.** The study's new `STOP_SWITCH_FAULTS` and `RUN_RECORD_FAILURES` share
names with counters already registered by `oncotriage/batch/runner.py` and
`oncotriage/storage/database_logger.py`, and
`tests/test_degradation_counter_readers.py` section 1 short-circuits on
`_name in _registered` -- so **the section passed with two brand-new
write-only counters in the package**, crediting another module's registration
to them. That is exactly the conflation `_DUAL_OWNED` exists to prevent,
arrived at twice; both are in it now, and the follow-up checks are **driven
from the table** rather than written out per name, so a fourth dual-owned
counter cannot be added without being subjected to them.

**AND ONE PINNED CHECK WENT STALE FOR A REFACTOR THAT PRESERVED ITS PROPERTY.**
That file required `report_checkpoint_faults` to be a DIRECT statement of
`main()`; the reader moved one frame down into `print_study_close`. The
property is that the reader RUNS WHEN A STUDY ENDS, and which function contains
the call is an implementation detail -- so the check is a **transitive
call-graph walk** over the module's own top-level functions now, with controls
in both directions.

```bash
# The operator-control pass. Same shape, same directory. No network, no keys,
# NO SPEND, no live Qdrant, no model load, no corpus, no git history, no live
# server -- match_patient_ablation, the BM25 index, the graph, the tracking
# module and run_fingerprint.current are stand-ins and THE GRAPH IS NEVER
# INVOKED; every subprocess is additionally handed ONCOTRIAGE_QDRANT_URL
# pointed at a closed port. It uses REAL SUBPROCESSES, REAL SIGNALS and TWO
# REAL CONCURRENT invocations, because a signal cannot be delivered to the
# process asserting about it and a lock held by one process cannot be observed
# from inside it. NOT in the collision matrix. It EXECS NOTHING. Bucket A,
# ~55 s.
python tests/test_ablation_stop_and_lock.py                       # 160 (was 157; the spend-coverage pass FIXED 4g's migration fixture, which dropped `status` alone and so described a database carrying `stop_reason` and NOT `status` -- a shape no era of this schema has ever had -- and added 4g-i..4g-iii over the era that DOES exist. Before that 143; the degradation-report pass added section 5m-5o over the registry's two blocks in the study's closing text and FIXED 5j-5l, which were VACUOUS: `_lines.append` was passed as the sink and `print_study_close` emits a bare `emit()`, which `list.append` refuses -- so `drive` caught the TypeError and 5k and 5l were asserting `"..." not in ""`, satisfied by a function that printed nothing at all. Before that 142; the consolidation pass corrected 3k-b's argument -- half of it dissolved when control.py stopped importing anything from the project -- and added 3k-b2, without which the class separation is equally satisfied by two COPIES of the class, which is what it was. Before that 107; the lock-hardening pass added the symlink-resolved key, the per-user lock directory, the substitution refusal, the UTC record, the stripped truncation guard and a symlinked second real invocation)
```

**THE FOREGROUND-SIGNAL LESSON IS CLOSED IN CODE RATHER THAN BY A CONVENTION,
and it is written down here because it was not written down anywhere.** A shell
that backgrounds a job sets SIGINT to `SIG_IGN` for it, children INHERIT that
disposition, and **CPython does not override an inherited `SIG_IGN` at
startup**. So a signal-driving test launched in the background starts children
that are DEAF to SIGINT: the scenario delivers a signal that does nothing, the
run completes, and the shipped fix is reported as broken. "Do not background
it" is an unenforced convention; the `usercustomize` hook restores
`default_int_handler` in the child and **check 6b asserts the disposition it
ended up with**, so a scenario that cannot be built is a recorded failure and
never a pass.

**THREE DEFECTS IN THIS PASS'S OWN TEST CODE WERE FOUND BY RUNNING, NOT BY
READING.** (i) The harness waited for a shutdown handler's log marker BEFORE
releasing the parked workers -- but both handlers print only once those workers
have returned, so the wait timed out and a working handler was reported as one
that never ran. (ii) A `return` inside the `try` ran the `finally`, which
**killed the very process the lock scenario was meant to hold** -- the holder's
log was empty and its `poll()` was already an integer, so the refusal it was
supposed to measure could not happen. (iii) The two lock invocations shared a
state directory AND their control files, so the second wrote the `release` flag
the first was parked on, freed the holder, and then took the lock it was
supposed to be refused.

### Seven pre-migration findings (the pre-migration pass)

**SEVEN FINDINGS FROM VERIFICATION ROUND THREE, EACH WITH A DRIVEN
REPRODUCTION TURNED INTO A STANDING TEST.** No schema change, no migration, no
billed call: `python fixture_replay.py` is **12/12 clean, exit 0, with no
recapture**, and the production `inferences.db` sha256 is unchanged.

**F1 -- THE RUN LOCK.** Argued at THE RUN LOCK in `oncotriage/batch/runner.py`
and summarised in the stop-gesture block above. `tests/test_runner_preflight_and_state_faults.py`
drives it with REAL CONCURRENT SUBPROCESSES: the first parks its pool, the
second is refused with exit 3 having started NO patient, the first then
completes normally, and a SIGKILLed holder is shown to leave the lock free for a
successor -- the property a pid file cannot have.

**F2 -- THE WRITE FAILURES ARE COUNTED.** Also summarised above. Driven against
a checkpoint directory made read-only WHILE THE POOL IS PARKED, so every state
file write fails: the run-end block is measured DEGRADED rather than CLEAN, and
the closing line reads ABSENT rather than "Cleared for next fresh run."

**F3 -- THE PREFLIGHT ABOVE THE DESTRUCTIVE FLAG.** Also summarised above.
Driven end to end with the checkpoint sha256'd before and after the refusal.
**AND THE FLAG ANNOUNCEMENTS MOVED TO `console.out`**: `print` goes to STDOUT,
which Python block-buffers when it is not a tty, while every other line the run
emits goes to STDERR flushed per line -- so in the ordinary
`python "25- Batch Runner.py" --fresh > run.log 2>&1` those two lines surfaced at
interpreter exit, BELOW the summary of the run they preceded. The same trap the
SIGTERM handler already records having MEASURED.

**F4 and F9 -- THE BOUNDED DRAIN, AND WHAT `cancel_futures=True` CANNOT DO.**
The dispatch-hardening pass added `cancel_futures=True` to the Stage 5 wave's
`finally` and called it the operator-interrupt fix. **IT IS UNREACHABLE FROM A
REAL SIGNAL IN A BATCH RUN**, and that is a fact about CPython: signals are
delivered to the MAIN thread, and the node executes on a WORKER thread of the
batch pool. So the SystemExit lands in `future.result()`, the pool cancels
QUEUED PATIENTS, and every in-flight patient then finishes its WHOLE wave while
`shutdown(wait=True)` blocks. The note in `evaluation.py` is corrected to say
so.

`oncotriage/agent/evaluation.py` now carries a **module-level shutdown flag** --
a plain bool and a reason, no lock, because a signal handler that acquires a
lock the main thread may hold is how a shutdown path deadlocks. `_issue` reads
it before each queued call; a **gate above the warmup** makes a patient entered
after the request send NOTHING at all; and a shutdown is the ONE exception the
send loop does not isolate to its trial, because `_on_done` CHECKPOINTS a
success and a patient published with four verdicts and eleven not-evaluable
would be skipped by every resume forever.

**SET BY SIGTERM AND Ctrl-C, AND DELIBERATELY NOT BY THE STOP SENTINEL.** The
brief asked for all three; STOP promises in-flight patients RUN TO COMPLETION
and are written, and setting the flag there would break that AND COST MORE, not
less -- the in-flight round is discarded, the patient fails, it is not
checkpointed, and the resume re-bills the whole of it. Only the two gestures
that are already abrupt and already record the run KILLED set it.
`tests/test_runner_sigterm_shutdown.py` section 3b pins that STOP does not.

**MEASURED, BOTH ARMS, against the real entry point under a real SIGTERM with
twelve waves genuinely in flight** (stand-in client, no spend): with the flag,
**0 further requests** and exit at 2.85 s; with `_issue`'s check removed, **156
further requests** and exit at 4.71 s. In production the arithmetic is

    rounds per patient  = ceil(MAX_TRIALS_FOR_EVALUATION / parallel) = ceil(15/4) = 4
    without the flag    = 4 x MATCHING_REQUEST_TIMEOUT_SECONDS x (1 + OPENAI_SDK_MAX_RETRIES)
                        = 4 x 300 x 2 = 2400 s = 40 minutes
    with the flag       = 1 x 300 x 2 =  600 s = 10 minutes

**TEN MINUTES STILL EXCEEDS `docker stop`'s TEN-SECOND DEFAULT**, so
`docker-compose.yml` gives `fastapi` `stop_grace_period: 620s` with that
arithmetic written beside it -- 600 for the call plus 20 for the crash record.
It covers the GROUPED arm, which is what ships; the per-trial 2400 s is named
there as what must be raised to before that flag is turned on behind an
orchestrator, since the API has no shutdown gate of its own. The batch runner is
not a compose service, and the note says an operator running it under systemd
needs `TimeoutStopSec=620` for the same reason.

**F5 -- RESAMPLE-STOP STATUS HONESTY.** Above, in the stop-gesture block.

**F6 -- A PATIENT IS NOT A ROW.** `campaign_summary`'s `total_patients` was
`SUM(COUNT(*))` over the fragments' inference rows under a docstring calling it
"the campaign's real cohort size". It is wrong on EVERY real campaign: the
resample pass writes a second row for each of `RESAMPLE_COUNT` (100)
already-processed patients, so a 1,000-patient campaign reported 1,100 and a
reviewer dividing a cost or a rate by it used a denominator 10% too large. It is
`COUNT(DISTINCT patient_id)` **across the whole campaign** now, with
`inference_rows` beside it -- and the DISTINCT is campaign-level rather than
per-fragment-summed because a patient whose attempt ERRORED is not checkpointed,
so the resume re-runs it and its two rows are in different fragments. The Run
Health tab's campaigns panel carries both, because every money column there is
summed over the ROWS.

**F8 -- A STAMP FROM THE FUTURE IS THE OPPOSITE REMEDY.** Every
`fingerprint_version` mismatch got one message and that message said to clear the
artifact -- correct for an OLDER stamp and exactly wrong for a NEWER one, where
the artifact is fine and THIS BUILD IS BEHIND IT. `compare()` has one branch on
`recorded > FINGERPRINT_VERSION`; the outcome is still FP_VERSION (the fields
are equally uncomparable and a sixth member of a closed vocabulary would be a
change every consumer has to learn), and the DETAIL says NEWER, says nothing is
wrong with the artifact, and names the remedy as checking out the code that
wrote it. `refusal_lines` takes the stored stamp as an optional argument so it
can print **DO NOT RUN THE COMMANDS BELOW** above the caller's own `--fresh` /
`--fresh-start` remediation. The comparison is guarded -- `"4" > 3` is a
TypeError raised out of the one function deciding whether a refusal is safe --
and a `bool` is excluded on this project's usual footing. **It follows the
storage layer's precedent**: `initialize_database` refuses to LOWER a
`PRAGMA user_version` it finds ahead of its own. The difference is stated rather
than glossed: that schema is strictly additive, so the older writer carries on;
a fingerprint is a set of facts to be compared, so this still refuses.

**F7 (the ablation study's controls) IS DELIBERATELY EXCLUDED from this pass and
is its own item.**

**A DEFECT IN THIS PASS'S OWN WORK, FOUND BY ITS OWN NEW TEST AND NOT BY
READING.** The `with exclusive_run_lock():` reindent put the guard's tail at
eight spaces when the `with` body is at twelve -- so `main()` ran OUTSIDE the
lock, which was released before the first patient. Every lock check passed as a
unit; the two-real-subprocesses scenario is what caught it, with `lsof` showing
nobody holding the file while a run was live.

**AND A PRE-EXISTING FLAKE WORTH RECORDING.**
`tests/test_runner_stop_switch.py` and `tests/test_runner_sigterm_shutdown.py`
run green alone and green in CI bucket A's pool, and FAIL when the two are run
CONCURRENTLY BY THEMSELVES -- measured at HEAD in a `git worktree`, before any
of this pass's edits, at 112/10 and a failing sigterm arm. Both drive 40-patient
subprocesses with `MAX_WORKERS` threads each; the machine saturates and the
signal lands after the corpus has run. Not caused here and not fixed here.

### The prompt cache has a reader, and per-trial mode was verified as a whole (the cache-reader pass)

**THE MEASUREMENT PER-TRIAL MODE IS ONLY VIABLE ON HAD NO READER.** The mode
multiplies Stage 5 requests by `MAX_TRIALS_FOR_EVALUATION` and pays for itself
only if the shared prefix is billed at the cached rate from the second call of a
patient on. `inferences.llm_classifier_call_details` has carried the per-call
evidence since the packing pass and **not one of the 51 registered queries named
it for caching**, so "is the discount landing" was answerable only by parsing
JSON by hand. `stage5_cache_effectiveness` is that reader — the 52nd query.

**IT GROUPS ON (run, arm) EXACTLY AS `call_mode_comparison` DOES**, so a reader
can put cost beside hit rate row for row. That is what `MODE_NOT_RECORDED_LABEL`
is for: the label was written out twice as a literal inside
`call_mode_comparison` and this query needs the identical bucket, which is the
`CROSS_ENCODER_MODEL` shape one layer down — nothing raises when two copies
disagree, and the only symptom is two tables that will not join. **The
extraction is value-preserving and that was measured rather than claimed: zero
of the 51 pre-existing queries' RENDERED SQL moved**, compared against
`git show HEAD:` byte for byte.

**NULL AND 0 ARE THE WHOLE DESIGN AND THAT IS WHY IT IS NOT ONE NUMBER.** A
`cached_tokens` of NULL means the response carried no
`prompt_tokens_details.cached_tokens` **at all**; 0 means it reported and the
provider cached nothing. Averaging them lets a provider that has gone SILENT
read as a provider that is NOT CACHING, and only the second is a reason to turn
the mode off. So the rate is computed over **reporting calls only**, both
numerator and denominator, and `wave_calls_silent` sits beside it — a run where
those two are equal has **no** hit rate, which is not a hit rate of zero.

**THE WARMUP IS REPORTED BESIDE THE WAVE AND NEVER INSIDE IT.** It is the
request that WRITES the prefix, so it reports 0 cached on a perfectly healthy
patient; folding it in drags every arm's rate down by one call's prompt and
makes a healthy warmup read as a cache miss. Measured on the seed: 15,600/27,000
= 0.5778 with the warmup out, 15,600/35,000 with it in.

**A CHECK THIS PASS WROTE FOUND A DEFECT IN THE SAME PASS'S OWN NOTES.** The
first draft said a HIGH `warmup_cache_hit_rate` "should be impossible —
investigate", and it is not impossible: a **parse retry** re-enters the node and
issues a FRESH warmup against a prefix the failed attempt's own wave has
already written N times over (driven, and the two warmups carry the *identical*
cache key), and **the same patient re-run** — a resample row, a resumed patient
— asks to be routed to the machine that already holds its prefix. Both are the
key working. An operator meeting that reading on an ordinary retried patient
would have gone hunting a leak that is not there. The notes name all three
causes now and keep the warning for the third.

**IT DECLARES FOUR COLUMNS AND NO TABLE.** The arm is on the inference row, so
the query answers on a database with no run tables at all —
`dangling_run_references`' ruling. All four declarations make the SQL
unparseable when absent, and `derive_requires_columns` agrees with the hand
declaration exactly. On a pre-era database `report()` **runs to the end** and
says which key it skipped; a direct `run()` raises `MissingTableError` rather
than returning an empty frame, because "this database cannot answer" and "the
answer is no rows" are different findings.

**EIGHT PLANTED REVERTS, EIGHT CAUGHT, none of them an abort**, each into a
`copytree`'d copy with `PYTHONPATH` pointed at it and all three touched files
sha256-unchanged afterwards: the warmup folded into the wave rate (8 failures),
the silent calls counted in the denominator (5), an absent rate reported as 0
(5), the warmup uncounted (6), the unrecorded mode read as `grouped` (7), the
`run_id` declaration dropped (1, in the schema-guards file), `requires=("runs",)`
added (6), and the warmup rows counted as wave calls (7).

**WHAT THE ROUND-TWO VERIFICATION ESTABLISHED, DRIVEN RATHER THAN READ.** The
F1–F8 fixes were verified TOGETHER through the **real `main()`** — real
`run_batch`, real `_on_done`, real `process_patient`, real
`match_patient_to_trials`, real Stage 5 node, real `node_finalize`, real
`log_inference`, real `flush_health`, real `start_run_record` /
`finalize_run_record`, real reconciliation — with four patients of mixed
outcome (healthy, warmup-failed, fallback-writer-failed, partial wave) against
a stub client installed through `oncotriage/agent/deps.py`. **No billed call is
reachable**: Stages 1–4 are replaced by seeding `filtered_trials` and the graph
is never a real graph. 33/33.

| what was asked | what was measured |
|---|---|
| the checkpoint holds the failed patients | the two successes only; the two failures are re-attempted, and the stamp on it names the arm |
| `run_metrics` carries the warmup counters | `PER_TRIAL_WARMUP_DEGRADATIONS` = 3 and `PER_TRIAL_CALL_FAILURES` = 1, persisted |
| the run row and the rows agree on the mode | one run row, `per_trial`, and all four inference rows name it and that run |
| the run-end report prints the new counters | both named in the printed block |
| the crash blocks print under a mid-campaign kill | both, the run row `KILLED`, and the crash-path flush persisted the warmup counter |
| F1's own prefix separates the two writers | `failed:` vs `fallback_writer_failed:` on the same run |
| F8's wave-only cached total | the row column equals the sum over the **non-warmup** ledger rows |
| F5/F6 | `llm_classifier_packed_chunks` NULL, `llm_classifier_packing` naming `bypassed_by: per_trial` |

**THE RESUME MATRIX, END TO END (20/20).** A grouped checkpoint under a
per-trial run is refused by `load_checkpoint` naming the field AND both modes,
with the checkpoint **byte-unchanged** on disk and `--fresh` named as the
remedy; the same-mode checkpoint resumes; `--fresh` clears it, driven as a
subprocess against the shipped entry point. **Campaign stitching refuses to
stitch across the mode change**: a per-trial KILLED run followed by a per-trial
resume stitches into one campaign (`1 -> 2`), and a per-trial KILLED run
followed by a **grouped** resume produces two campaigns of one — so a mixed-arm
total is impossible by construction.

**THE MIXED-ERA DATABASE (20/20).** Pre-provenance rows (no `run_id`, no mode,
no ledger), grouped rows, and all four per-trial shapes in one file: every
registered query runs, `report()` returns every key, the per-trial arm's wave
rate excludes both the warmups and the silent calls, and the omission count is 1.
On the pre-era database `report()` reaches the end, names the skipped key, and
the skipped key is **absent from the returned dict** rather than present with an
empty frame.

**SIX NEW FAILURE MODES WERE PROBED AND EACH HAS A VERDICT.** See the ranked
list in the round-two report; the ones that changed how this file reads are:

- **SIGTERM IS NOT COVERED BY THE INTERRUPT FIX, AND THAT IS MEASURED.** Driven
  with real signals against a real process holding a real wave: SIGINT reaches
  the node as `KeyboardInterrupt`, the `finally` runs, `cancel_futures=True`
  fires and the queued calls are cancelled. **SIGTERM runs nothing** — no
  handler, no exception, no `finally`, exit `-15` — so in-flight requests are
  abandoned mid-read while still billed and no ledger row records them. The
  `runs` row is left at RUNNING with a NULL `finished_at`, which is the
  documented shape for a process that had no chance to run a handler. **SIGTERM
  is what `docker stop`, `kubectl delete pod` and systemd send FIRST.**
- **THE PROMPT CACHE'S INACTIVITY CLOCK IS BOUNDED BY ONE CALL, NOT BY THE
  WAVE.** All N tasks are submitted up front to a `max_workers=_bound` pool, so
  workers pull continuously and the largest inter-request gap is a small
  multiple of one call's latency — measured, with a stalled call shown NOT to
  open a proportional gap at `parallel > 1`. **What is written down nowhere is
  the arithmetic relating `MATCHING_REQUEST_TIMEOUT_SECONDS = 300` (exactly five
  minutes of read phase, twice that across the SDK's one retry) to the
  provider's eviction window.** At `MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS = 1`,
  documented as legal, the gap between requests IS one call's latency and the
  two constants can collide.
- **A PARSE RETRY COSTS 2 × (1 + N) REQUESTS AND STORES A LEDGER OF (1 + N).**
  At `MAX_LLM_CLASSIFIER_RETRIES = 3` and 15 trials that is **up to 64 requests
  for one patient against a stored ledger of 16**. Nothing in the record says
  the earlier attempts happened; `llm_classifier_retries` is the only tell and
  it does not carry their tokens.
- **PER-TRIAL MODE CANNOT MEASURE ITS OWN INPUT PRESSURE.** The packer is
  bypassed, so `llm_classifier_packing` carries no chunk list and
  `stage5_input_packing_pressure` reports NULL — correct, and it means an
  over-budget single request is invisible to every registered query. Driven: one
  trial estimated at **50,005 input tokens against a 12,000-token budget** is
  sent anyway, because the reactive splitter's floor is `len(chunk) == 1`.
- **A ZERO-TRIAL PATIENT ISSUES NO WARMUP.** No pointless billing; the ledger is
  `[]` (the node ran and no call produced usage) rather than NULL.
- **THE EXECUTOR ACCUMULATES NOTHING.** 60 consecutive per-trial patients leave
  the thread count and the descriptor count exactly where they found them, no
  `stage5` worker outlives the node, and the pool really reached its configured
  bound — measured from INSIDE the wave, which is the only place it is
  observable. The first version of that check sampled BETWEEN patients, read the
  base every time, and its own non-degeneracy probe is what caught it.
- **THE DASHBOARD DOES NOT CRASH ON PER-TRIAL ROWS.** All ten tabs render
  against a database whose every row is per-trial, including the sparse
  warmup-failed row and an unscored `trial_matches` child — 48 markdown, 91
  metrics, 11 dataframes, no exception. F9's deferral is confirmed as a
  **mislabel and not a crash**: ten rendered labels change meaning between the
  arms (`Avg Cost/Patient`, `Avg Tokens/Trial`, `Avg Input Tokens`, …) and
  **nothing on the page names the call mode**.
- **THE REJECTION-MEMO GAP IS A NUMBER NOW.** A provider that refuses the
  warmup's shape is re-discovered by every patient: **1,000 refused warmups,
  $0** (a 400 is refused before generation and the fallback still issues exactly
  N trial calls), **one extra serialised full-price call per patient**, and
  ~1,000 WARNING lines — about **22 minutes** added to a 1,000-patient campaign
  at `MAX_WORKERS = 12`. The argument AGAINST the memo is measured too: a
  **transient** 400 is retried by the next patient today, and a process-wide
  memo would have disabled the warmup for the whole campaign on one hiccup.

**TWO STALE COUNTS IN THE RUN BLOCK WERE CORRECTED, EACH RE-MEASURED**:
`test_storage_query_layer.py` 376 → **427** and
`test_agent_stage5_per_trial_calls.py` 239 → **255**.

### Stage 5 can be served by Amazon Bedrock, and the flag is OFF (the Bedrock pass)

**NOTHING IN THIS PASS RUNS.** `config.MATCHING_PROVIDER` is `"openai"`, and
under that default `oncotriage/agent/evaluation.py:call_matching_model` costs
two string comparisons and issues the byte-identical request it always did:
same client object, same seven kwargs, same values. **No AWS call and no billed
call of any kind was made building it** — every verification is stubs, literal
response dicts and the twelve recorded fixtures, which replay **12/12 clean
without recapture** before and after.

**THE ADAPTER IS `oncotriage/agent/bedrock_adapter.py` AND ITS DOCSTRING IS THE
SPEC.** Every field of the Stage 5 request is mapped explicitly with the AWS
page it was read from (2026-08-21), and the mapping is pinned field by field by
`tests/test_agent_bedrock_adapter.py`.

**THE RESPONSES API IS THE PRIMARY FORM, and the reason is measured rather than
stylistic.** Bedrock serves Chat Completions on both endpoints, so a literal
translation would have been no translation at all — and it is the wrong target
twice over. **Prompt caching for this model is Responses-only**: the GPT-5.6
Terra model card lists "Prompt caching (Responses API only)" under what
`bedrock-runtime` supports, and the prompt-caching page opens "OpenAI models on
Amazon Bedrock support prompt caching through the Responses API". Stage 5's
whole packing design rests on N requests sharing one prefix, so the API that
cannot cache that prefix throws away a 90% input discount. And Responses is the
one shape both endpoints agree on, which matters because **which endpoint the
quota lands on is not yet known** — so `BEDROCK_ENDPOINT` is configuration.

| chat today | Responses, when the flag is on |
|---|---|
| `model=MATCHING_MODEL` | `model=config.matching_wire_model()` — a cross-Region profile (`us.openai.gpt-5.6-terra`) on `bedrock-runtime`, the bare id on `bedrock-mantle` |
| `messages=[system, user]` | `input=[{"type":"message","role":BEDROCK_SYSTEM_ROLE,"content":[{"type":"input_text",...}]}, ...]` — **an input item, not `instructions=`**, because every documented cache breakpoint hangs off an `input_text` block |
| `max_completion_tokens` | `max_output_tokens`, value unchanged |
| `reasoning_effort` | `reasoning={"effort": ...}` |
| `seed` | **DROPPED, COUNTED, LOGGED** — there is no `seed` on the Responses API |
| `response_format={"type":...,"json_schema":{name,strict,schema}}` | `text={"format":{"type":...,name,strict,schema}}` — flattened, and the schema is **unwrapped from the chat builder rather than rebuilt** |
| `temperature` | still not sent |
| — | `store=False`, **overriding the vendor default of `True`** |

**`seed` IS THE ONE THING THE RESPONSES API CANNOT EXPRESS, and it is measured
rather than inferred**: absent from the installed SDK's `responses.create`
signature, present on `chat.completions.create` (openai 1.99.9). The drop lands
in `BEDROCK_ADAPTER_DEGRADATIONS["seed_not_expressible"]`, which is the
twenty-first counter in `oncotriage/degradation.py`'s run-end registry, plus one
WARNING per process. Config already recorded that the seed is "best-effort
only" and that the model returns no `system_fingerprint`, so Stage 5 was already
only best-effort reproducible — this widens it, and it is why k=3 agreement
across a provider flip cannot be read as a pure provider difference.
`BEDROCK_SEND_SEED_IN_EXTRA_BODY` turns it back on in one edit once the probe
says the field is tolerated; it defaults False because a 400 on an unknown field
fails **every** Stage 5 call of a run.

**`store=False` IS A DATA-RETENTION DECISION, NOT A DEFAULT.** AWS: "When
`store` is `true` (the default), Amazon Bedrock retains the response, including
the input and output, for 30 days." The Stage 5 input is a rendered patient
record. A default is not a decision, so the field is sent explicitly.

**THE MODEL ECHO IS NOT REWRITTEN.** It would have been one line to present
`MATCHING_MODEL` as the echo and keep `MatchingModelMismatchError` quiet, and it
would have made every stored row name a model that did not serve it — the exact
misattribution that check exists to prevent, pointed the other way. Instead
`evaluation.py` compares the echo against **`config.matching_wire_model()`**,
which returns `MATCHING_MODEL` exactly when the flag is off, and
`inferences.matching_model` stores what answered. `PRICING_CONFIG` therefore
gained rows for the three Bedrock wire ids, quoted from the model card's
**Standard tier, short-context (272K)** band with the routing option named per
row; an id absent from it raises `UnknownModelPricingError` before a row is
written, which is the loud failure this project requires of an unpriced model.

**`inferences.matching_provider` IS THE PROVENANCE COLUMN**, `"openai"` or
`"bedrock"`, **read live off `config` at INSERT time** rather than from the
result dict — which is what makes it unconditional: it lands on the
no-candidates rows, the error-handler rows and the Stage 5 failure returns
alike. A plain TEXT column on `matching_model`'s and `ecog_selection`'s
precedent; at the Postgres migration it becomes TEXT with a CHECK constraint.
**NULL means the row predates the column, and such a row is provably OpenAI.
Nothing is backfilled.**

**A `from oncotriage.config import MATCHING_PROVIDER` WAS THE FIRST DRAFT AND IT
WAS A DEFECT** — a from-import BINDS the value at import, so flipping
`config.MATCHING_PROVIDER` in a process (the probe does; a test does) reached
nothing and every row recorded the value the process started with. Caught by
running, not by reading. It is `_config.MATCHING_PROVIDER` now, and
`oncotriage/fixtures/capture.py`'s tunable entry is the same shape for the same
reason. This is `tests/test_agent_rrf_config_ownership.py`'s patch-point lesson
met again, one layer down.

**THE RESUME FINGERPRINT NEEDED THE FLAG AND COST NO VERSION BUMP.**
`matching_model_configured` is GATED, and `MATCHING_MODEL` does **not** move
when the provider flips — it is the priced identity of the judge, and
"gpt-5.6-terra" is the same judge on either provider — so a checkpoint written
against OpenAI would have been resumed against Bedrock with the gate answering
FP_MATCH, and one artifact would have held two providers' rows with nothing in
it saying so. The field now reads `config.matching_wire_model()`, which returns
`MATCHING_MODEL` byte-identically with the flag off, so **`FINGERPRINT_VERSION`
stays at 2 and no v2-stamped artifact refuses**. The alternative — a seventh
gated field and a bump — was rejected on that blast radius alone.
**STILL NOT GATED, stated rather than glossed:** `BEDROCK_ENDPOINT` and
`BEDROCK_REGION`. Same profile id in two Regions, or mantle against runtime with
the same id, are indistinguishable to the gate. Closing that IS the bump, and it
is a recorded follow-up.

**THE SEAM IS A SECOND KEY, NOT A REDIRECT OF THE FIRST.** `deps.BEDROCK_CLIENT`
joins the closed `OVERRIDE_KEYS`. It has to be separate: with the flag on, Stage
2 still embeds through **OpenAI**, so both clients are live at once and one key
could not redirect the judge without also redirecting the embeddings — and the
identity assertions both fixture harnesses make would stop distinguishing them.

**THE CREDENTIAL HAS TWO TIERS AND NEITHER IS IN THE .env.**
`ONCOTRIAGE_BEDROCK_API_KEY` wins; AWS's own `AWS_BEARER_TOKEN_BEDROCK` is read
second (it is set on purpose by an operator following AWS's getting-started
page, unlike `QDRANT_URL`, which is the recorded ACCIDENT). Neither goes through
`_from_env` — fifth victim of that helper's trailing separator, after the
airflow password, the inferences DB, the degraded flag and the log level. The
resolver reports the **source**, never the value. It is not a fourth line in
`05- Keys/.env` because `load_env_keys()` validates that all three of its names
are present, and a fourth would fail every process that has no Bedrock key.

**`bedrock_probe.py` IS DAY ONE'S FIRST COMMAND AND IT REFUSES WITHOUT ITS
FLAG** (`--i-understand-this-bills`; exit 2, nothing called, nothing billed).
Not a prompt — a prompt is answered by somebody who has stopped reading. It
forces the provider in its own process, prints the base URL, the key SOURCE, the
whole usage block, `response.text` and `response.reasoning`, validates the
output against the real Stage 5 schema with a self-contained walker, issues two
identical calls to see whether the cache warms, and prices itself from
PRICING_CONFIG.

**TEN VERIFY-AT-GO-LIVE ITEMS ARE ENUMERATED IN THE ADAPTER'S DOCSTRING**, each
naming the probe check that settles it and the edit if it differs. **The one to
read first is (3): structured output.** Terra's model card lists "Structured
outputs" under NOT SUPPORTED on `bedrock-runtime` — but that link points at
`structured-output.html`, which is the Bedrock-NATIVE feature
(`outputConfig.textFormat` on Converse, `output_config.format` / `response_
format` on InvokeModel), whose own supported-API table names Converse,
InvokeModel, cross-Region and batch inference and **does not mention the
OpenAI-compatible Responses API at all** — and the same card lists Invoke as
unsupported, which is consistent with the row being about the native feature. So
the card does not settle it either way and **no AWS page states whether
`text.format` is honoured on this surface.** The dangerous outcome is
"accepted, no error, silently not enforced", which is why the probe checks the
echo as well as the parse.

**TWO OF THE BRIEF'S "VERIFIED FACTS" WERE WRONG AGAINST THE LIVE DOCS**, and
both are recorded rather than quietly corrected: `bedrock-mantle` serves
**Responses AND Chat Completions AND the Anthropic Messages API**, not Responses
only; and its base URL is `/v1` on the general endpoint page while **the Terra
model card carries an explicit footnote that this model is served at
`/openai/v1/responses`** — the model card wins because it is the page about this
model, and `BEDROCK_BASE_URL_TEMPLATES` uses `/openai/v1` for both endpoints
with that argument written beside it. The 272K-vs-1M context claim is neither: it
is Terra's two **pricing bands**, both real, and nothing here hardcodes a window.

**THE FIXTURE HARNESSES DO NOT COVER BEDROCK, AND THEY NOW REFUSE RATHER THAN
DISCOVERING IT AT THE BILL.** `capture.py`'s `OpenAIProxy` wraps
`chat.completions.create` on the OPENAI seam. With the flag on, Stage 5 reaches
`responses.create` on the BEDROCK seam — a method and a seam neither proxy
covers — and the two outcomes are the two the seam exists to prevent: a CAPTURE
that issues real billed calls, records none of them, and still passes
`assert_hooks_reach_the_agent` (which asserts by identity on `OPENAI_CLIENT`,
and that object IS the harness's); and a REPLAY that bypasses the OpenAI
tripwire, sends all twelve fixtures' Stage 5 prompts to a live endpoint, is
billed for every one, and prints that they replayed clean. Verbatim the pass
20c-2c regression, reintroduced through a second provider.
`capture.assert_provider_is_hookable()` is called at the top of BOTH
`install_recording_hooks` and `install_replay_hooks`, before any client is
touched, and raises `UnsupportedMatchingProviderError` naming the constant.
**Teaching `OpenAIProxy` the Bedrock seam is the top-ranked follow-up** — it is
a fixture-FORMAT question (the recorded request block is chat-shaped), not a
one-line one, which is why this pass refuses instead of guessing.

### The fixture gate survives the default flip (the call-mode-pin pass)

**THE HARNESS'S REFUSAL WAS RIGHT AND ITS EXPIRY DATE WAS ALREADY SET.**
`oncotriage/fixtures/capture.py`'s `RecordingSink.add` stamps
`call_index = len(bucket)` under its lock, so a Stage 5 recording's index is
its ARRIVAL ordinal — deterministic while the stage is sequential, decided by
the thread scheduler the moment it is not — and `build_deterministic_prefix`
projects `request_sha256_by_call` and `finish_reasons` as LISTS in that order.
So both harnesses raised `UnsupportedCallModeError` before any hook was
installed whenever `MATCHING_PER_TRIAL_CALLS_ENABLED` was True. Free while
grouped was the default; **the day the default flips it takes the free
twelve-fixture replay gate out of service at exactly the moment a large
behaviour change lands.** Measured rather than predicted — see the
counterfactual below.

**THE DEFAULT DID NOT MOVE IN THIS PASS.**
`MATCHING_PER_TRIAL_CALLS_ENABLED` is still `False`. What changed is the
semantics of the harness's answer.

> **CURRENT STATE, 2026-08-24 — that sentence describes the tree as this pass
> left it and is kept as written.** The default HAS since moved:
> `MATCHING_PER_TRIAL_CALLS_ENABLED` is **True**, per-trial is the shipped arm,
> and grouped is the retained comparison arm. This pass's whole point was that
> the gate would survive that flip, and it did — `python fixture_replay.py` is
> **12/12 clean, exit 0, with no recapture** under the new default, with the
> pin line printed. See "The default call mode is per-trial (the default-flip
> pass)" below.

**THE PIN GOES THROUGH THE ONE OWNER, WHICH IS THE WHOLE DESIGN.**
`oncotriage/config.py` gained a private `_MATCHING_CALL_MODE_PIN` and three
functions beside `matching_call_mode()`: `pin_matching_call_mode(mode)`
(returns the previous pin), `clear_matching_call_mode_pin()` and the
diagnostic `matching_call_mode_pin()`. `matching_call_mode()` resolves
**pin, then constant**, so all four existing consumers —
`agent/evaluation.py`'s partition, `storage/database_logger.py`'s
`inferences.matching_call_mode`, `run_fingerprint._call_mode` and
`tracking.configuration_params` — follow it with no edit. That ordering is the
only one that can be correct: a process that has pinned an arm is going to RUN
that arm, so every consumer reporting on it must name the arm that ran.

**WHY NOT JUST SET THE CONSTANT FOR THE PROCESS.** The harness could do
`config.MATCHING_PER_TRIAL_CALLS_ENABLED = False` on the module and every
consumer would follow, because they all read through the owner. That is the
shape this project keeps removing: a second WRITER of a declared configuration
value, indistinguishable afterwards from the declaration itself, so
`config.MATCHING_PER_TRIAL_CALLS_ENABLED` read anywhere later — a report, a log
line, a future reader — would say the campaign was configured grouped when it
was configured per-trial and overridden. The pin keeps the two facts apart:
**the constant says what the project is configured to do, the pin says what
this process was forced to do**, and the owner resolves them in one place with
one rule. Measured both ways — behaviourally (check 1c: the constant is
unchanged across a pin, with a non-degeneracy probe that the two disagreed) and
structurally (check 4b: neither fixture module contains an assignment to that
name, with `config.py`'s own declaration as the non-degeneracy probe).

**IT IS NOT AN ENVIRONMENT VARIABLE.** Every `ONCOTRIAGE_*` name in
`oncotriage/settings.py` is a deployment knob an operator sets; this is a
declaration a PROGRAM makes about itself, and exporting it would let it leak
into a batch run that never asked for it — the campaign-corrupting direction.

**THE REFUSAL REMAINS, AND IT NOW ASKS THE RIGHT QUESTION.**
`assert_call_mode_is_hookable` read `config.MATCHING_PER_TRIAL_CALLS_ENABLED`;
it reads `config.matching_call_mode()`. Two consequences, and the second is not
obvious:

* it is what makes the pin work at all, without a second copy of the pin rule
  here to keep in step with `config.py` by hand;
* **pinning PER-TRIAL is refused exactly like inheriting it.** The guard asks
  what the node will actually DO, so the pin is not a way around it. All four
  (pin × constant) combinations are driven in
  `tests/test_fixture_call_mode_pin.py` section 2 and again, in subprocesses,
  in section 5.

The refusal fires for every path that did not come through the pin — a test, a
script, a future caller, or a harness whose pin has been deleted or moved below
the first hook install — and its message now names the owner, **both** inputs
to it (`MATCHING_PER_TRIAL_CALLS_ENABLED=…, pin=…`) and the remedy by name.

**LOUD, AND LOUD EVEN WHEN IT OVERRODE NOTHING.**
`pin_call_mode_for_fixture_process(what, out=None)` prints three lines: what was
pinned and by whom, what the process WOULD have run and the constant it read,
and `FIXTURE_CALL_MODE_NOTICE` — one module constant, printed verbatim by both
entry points, stating that the fixtures characterize the GROUPED arm and that
per-trial fixtures are a PENDING MIGRATION ITEM. **A notice that appeared only
when it had something to override would be absent from every log taken before
the flip and present afterwards**, so the reader most likely to be confused —
somebody comparing a fixture captured under one default with a replay run under
the other — is exactly the reader it would fail. Printing the default alongside
the pin is also the only thing in either log that says which arm the project was
configured for at capture time. `out` is injectable on
`degradation.print_report`'s footing: neither `main()` can be driven in a test
(one costs money, the other needs a live Qdrant and twelve fixtures), so the one
line they both depend on has to be exercisable on its own.

**IT IS THE FIRST STATEMENT OF EACH `main()` AFTER `parse_args`**, and that is a
correctness property rather than tidiness: anything above it reads the UNPINNED
mode — the guard, Stage 5's partition, and a fixture's own environment block.
Section 4a asserts the position by AST, relative to `parse_args` rather than as
a literal index, **with a control that swaps it down one statement and must
fail**.

**A FIXTURE NOW SAYS ON ITS FACE WHICH ARM PRODUCED IT.**
`build_environment_block` records `"matching_call_mode": config.matching_call_
mode()` — the durable form of the printed notice. **Deliberately NOT in
`tunables`, and the reason is a trap rather than a taxonomy:** File 46's
`diff_tunables()` resolves every recorded key with `getattr(config, name)`, so a
key must be the NAME OF A MODULE ATTRIBUTE. `MATCHING_CALL_MODE` is not one (the
owner is a function), so it would be reported `<no longer defined>` on every
future fixture forever; and `MATCHING_PER_TRIAL_CALLS_ENABLED` is one but is the
wrong fact — under the pin it can read True on a run that was grouped. Check 6c
turns that into a standing invariant for the whole dict: **every recorded
tunable must resolve as a config attribute**, with the two rejected spellings as
its control. **FUTURE CAPTURES ONLY**, on this block's standing doctrine, so the
twelve fixtures on disk are unmoved.

**WHAT WAS MEASURED BY RUNNING, both arms.** The "default flipped" arm is a
`usercustomize.py` on `PYTHONPATH` that sets the constant True at interpreter
startup — no repository file edited, so the fixtures and the tree are untouched.

| arm | `python fixture_replay.py` |
|---|---|
| shipped default (grouped) | **12/12 clean, exit 0**, no recapture |
| default forced per-trial | **12/12 clean, exit 0**, no recapture |
| default forced per-trial, **pin reverted in a copy** | **exit 1**, an uncaught `UnsupportedCallModeError` traceback at the first fixture |

The third row is the outage this pass exists to prevent, and it is worse than a
clean refusal: the guard raises inside `replay_fixture` → `install_replay_hooks`,
which nothing catches, so the gate dies with a traceback rather than a report.
All twelve fixture files are byte-identical by sha256 before and after, and the
production `inferences.db` sha256 is unchanged — `ab1403e3…`, 90,185,728 bytes.

**`tests/test_fixture_call_mode_pin.py` — 81 checks, bucket A, ~4.8 s (MEASURED).** No
network, no keys, **no spend**, no live Qdrant, no model load
(`ONCOTRIAGE_DEFER_LOCAL_MODELS` above the imports, asserted in-process and in
every subprocess), no corpus, no database, no git history, no live server. It
uses four subprocesses, for two reasons that are not convenience:
`oncotriage/fixtures/replay.py` sets `ONCOTRIAGE_DEFER_LOCAL_MODELS` at module
scope — the one deliberate import-time side effect in the package — so importing
it in-process would change the environment for every check after it; and **a pin
is process-global by design**, so exercising the "default is per-trial" arm
in-process would leave this file's own later sections running under a state they
did not ask for. Each subprocess is handed `ONCOTRIAGE_QDRANT_URL` pointed at a
closed port. It **execs nothing**, so it needs no `_EXEC_ALLOWLIST` entry, and
it writes nothing anywhere, so it is not in the collision matrix — but it READS
`oncotriage/config.py`, which `tests/test_config_snapshot_date_rot.py` rewrites
in place, so all three files it reads are sha256-compared at the end (check 6e)
and an interleaved serial run is visible rather than silent.

**THIRTEEN REVERTS, THIRTEEN CAUGHT**, each applied to a `copytree`'d copy with
`PYTHONPATH` pointed at it, a realpath preflight asserting the COPY is what
imports, `PYTHONDONTWRITEBYTECODE=1` set, and every plant asserted to have an
exact occurrence count so a plant that matched nothing is a named
`PLANT-FAILED`: the owner ignoring the pin (25 recorded failures), the guard
reading the constant (6), the harness writing the constant instead of pinning
(10), each entry point's pin deleted (2 each), the pin moved down one statement
(1), the notice printed only when it overrode something (9), the pin validation
removed (4), the guard dropped from `install_recording_hooks` (1), the
environment key removed (1), the arm recorded as a tunable (2), the
pin-did-not-take branch deleted (2) and `clear_matching_call_mode_pin` not
clearing (8). All three shipped files byte-identical afterwards.

**THREE DEFECTS IN THIS PASS'S OWN TEST CODE WERE FOUND BY RUNNING, NOT BY
READING, AND TWO OF THEM ARE THIS PROJECT'S RECURRING SHAPES.**

* **A DOCSTRING SATISFIED A SUBSTRING SCAN.** Check 2g asked whether the guard
  still reads `MATCHING_PER_TRIAL_CALLS_ENABLED` by looking for that text in
  `ast.unparse(guard)` — and the guard's own PROSE, arguing why it stopped
  reading that constant, contains it. **The argument was reported as the thing
  it argues against.** It walks NAME LOADS with the docstring stripped now, with
  the constant's genuine appearance inside `UnsupportedCallModeError.__init__`
  as the non-degeneracy probe. Same lesson as the Docker pass's "a file that
  argues about its own settings cannot be grepped for them", one directory over.
* **A SUBSTRING IS NOT A NAME.** Check 6c filtered the tunables on
  `"PER_TRIAL" in n` and reported `MATCHING_OUTPUT_TOKENS_PER_TRIAL` — a real,
  correct, unrelated tunable. It intersects an explicit set of five spellings
  now.
* **THREE REVERTS ABORTED THE FILE INSTEAD OF FAILING IT**, and each abort was
  triggered by exactly the defect its check exists to catch: `_main_of(None)`
  walked `None` when a revert DELETED the pin the control tries to move, and
  `len(absence or [])` raised when a revert made a subprocess die before it
  could report. **That is the twelfth time this project has shipped that
  shape.** `_Absent` is falsy now and `size()` / `joined()` are the fix; the same
  three reverts report 25, 2 and 2 recorded failures and run to their summaries.

**AND THREE MORE FOUND BY RE-READING THIS PASS'S OWN CODE AFTER IT WAS GREEN,
which is why "it passes" is not the end of a pass.**

* **A DOCSTRING ASSERTED SOMETHING FALSE ABOUT THE SUITE.**
  `pin_call_mode_for_fixture_process` argued its injectable `out` on "neither
  entry point's `main()` can be driven in a test". `tests/test_resume_capture_
  and_ragas.py` drives `capture.main()` for real. The argument is asymmetric —
  it is the REPLAY `main()` that cannot be driven — and it now says so, with
  the consequence recorded beside it: **a test that drives `capture.main()`
  installs this process-global pin and does not clear it.** Inert today,
  correct after a flip, and a real cross-check side effect a reader is entitled
  to know about.
* **THREE ANNOTATIONS SAID `-> str` FOR FUNCTIONS THAT RETURN `None`.**
  `pin_matching_call_mode` returns the previous pin, `clear_matching_call_mode_
  pin` returns what it cleared, and `matching_call_mode_pin` answers "nothing
  is pinned" — all three by returning `None`. Quoted `"str | None"` now, and
  quoted deliberately: `config.py` is imported by every entry point in the
  project and must not become the one file that refuses to import on an older
  interpreter.
* **THE NEW TEST HARD-CODED TODAY'S DEFAULT.** Check 1a asserted
  `MATCHING_PER_TRIAL_CALLS_ENABLED == False`, which would have made the file
  whose entire subject is that the gate SURVIVES the flip the first thing to
  fail when the default flips. It derives the expected mode from the constant
  now. **A test that fails on the change it exists to protect is a landmine,
  not a tripwire** — and every other default-dependent check in the file forces
  the constant itself, so this was the only one.

**VERIFIED BY RUNNING.** `tests/test_package_invariants.py` **260/0/0**,
unchanged; `tests/test_agent_stage5_per_trial_calls.py` **283/0**, unchanged
(its section 1l block was rewritten to say what it now checks — the guard, not
the whole answer — and one label that promised the refusal "names the constant
an operator has to change" was corrected, since the operator's remedy is now the
pin); CI bucket A green; the serial runner **5/5** with `oncotriage/config.py`
and `oncotriage/registries/cancer_code_registry.py` confirmed restored;
`python fixture_replay.py` **12/12 clean under BOTH defaults, exit 0, no
recapture**; twelve fixtures byte-identical; production `inferences.db` sha256
unchanged. **No money was spent and no migration was run.**

**WHAT IS NOT DONE, NAMED RATHER THAN LEFT TO BE DISCOVERED.** The migration
itself: `RecordingSink` still orders the `chat_completions` bucket by arrival,
so per-trial fixtures remain impossible and a per-trial campaign has no
characterization gate of its own. `call_index` is stamped by a generic bucket
appender shared with three other seams and the node's own `call_index`
numbering would have to agree with it, which makes it a fixture-FORMAT change
with a `SCHEMA_VERSION` bump and a paid recapture — the reason this pass pins
instead of guessing. Until it lands, **the twelve fixtures characterize the
grouped arm and say so on their face**, and a per-trial campaign's Stage 5
behaviour is covered by `tests/test_agent_stage5_per_trial_calls.py` alone.

### One owner per verdict, and the serial runner's lock caught up (the duplicated-derivation pass)

**EIGHT FINDINGS FROM VERIFICATION ROUND FOUR, PLUS A PORT.** Every one is a
fact stated twice — a SQL predicate, a status expression, a number, a docstring
argument — and in each case nothing failed when the two copies disagreed. **No
billed call was made**: `python fixture_replay.py` is **12/12 clean, exit 0,
with no recapture**, and the production `inferences.db` AND
`ablation_results.db` sha256 are unchanged, as are all twelve fixture files.

**F5 + F8 — "THE LATEST RUN PER CONFIG" HAD TWO COPIES AND BOTH WERE WRONG.**
`generate_summary` (which averages that run's results) and
`_summary_status_warning` (which reads its `status` and qualifies those
averages) each carried

    WHERE (config_name, run_timestamp) IN (
        SELECT config_name, MAX(run_timestamp) FROM ablation_runs
        GROUP BY config_name)

`_LATEST_RUN_PER_CONFIG_SQL` is the one owner now, and it is **`MAX(id)`**.
`run_timestamp` is `datetime.now().isoformat()` — NAIVE LOCAL TIME — and it
fails two ways, both silent:

* **AN EXACT TIE SELECTS MORE THAN ONE ROW.** `IN` matches every row carrying
  the maximum, so two runs of one configuration sharing a timestamp both
  qualify: the status reader prints that configuration TWICE, once per status,
  and the summary's INNER JOIN admits BOTH runs' results and averages them
  together — a mean over two runs presented as the latest run's. Driven: two
  runs, one timestamp, results 10/10/10 and 0, **pre-fix n=4 and mean 7.5, a
  number belonging to neither run**; fixed, n=1 and 0.0.
* **LOCAL TIME IS NOT MONOTONE.** At a DST fall-back the wall clock repeats an
  hour, so 01:45 EDT (earlier in real time) sorts ABOVE 01:15 EST (later), and
  `MAX` picks the superseded run. Driven with exactly that pair: pre-fix the
  summary reports the SUPERSEDED run's COMPLETE status and its numbers, so a
  STOPPED run is never qualified at all.

`id` is `INTEGER PRIMARY KEY AUTOINCREMENT`, so no tie is POSSIBLE and it is
monotone in insert order — `queries.campaign_summary`'s own argument for reading
run order off `runs.id` rather than `started_at`. **`run_timestamp` is not
deleted**: it is still what an operator reads, it just no longer DECIDES
anything.

**F7 — THE RUN'S TERMINAL STATUS WAS DERIVED THREE TIMES IN ONE FUNCTION.**
`oncotriage/batch/runner.py:main()` computed it for `finalize_run_record`, again
sixty lines earlier for the console block that PRINTS what the row will say, and
a third time for `tracking.end_run`. The console block's own text is the promise
— "run row FINISHED — NOT STOPPED, because STOPPED means the campaign covers a
PREFIX of the cohort" — so a comment argued the two must agree while the code
kept them in step by hand.

**THEY AGREED ONLY BY COINCIDENCE OF THE GUARD.** The console copy sits under
`if STOP_SWITCH.requested and not _stopped_mid_cohort:`, which collapses the
STOPPED arm — so the shorter two-way expression there was correct BECAUSE OF
WHERE IT SAT rather than because of what it computed. `_terminal_status` is
derived once, where `main_errors` is bound, and both readers name it.

**THE THREE MISSING CONSTANTS WERE ADDED, AND THE DOCSTRING THAT FORBADE IT WAS
RE-READ RATHER THAN OBEYED.** `RUN_RECORD_STATUS_RUNNING` and `_STOPPED` were
named; FINISHED / FAILED / KILLED were literals typed into
`RUN_RECORD_TERMINAL_STATUSES` under a paragraph saying they must be "written
out rather than derived… deriving them from anything in this module would make
the round-trip check agree with itself by construction". **The concern is real
and it is about `tracking`, not about this module**: the check compares this
tuple against `tracking.RUN_STATUSES`, so naming the three as literals HERE and
building the tuple from them leaves the comparison between two modules'
independent text. The paragraph says so now, and the three names exist because a
caller cannot import a literal.

**THE MLflow TRANSLATION IS A DECLARED MAPPING.** `TRACKING_STATUS_FOR` maps
each `runs.status` onto MLflow's three-member vocabulary, with **STOPPED →
KILLED the one row that is not an identity** — and a `RuntimeError` at import
when its keys stop matching `RUN_RECORD_TERMINAL_STATUSES`, so a fifth status
fails at load rather than reaching `end_run`, which substitutes FAILED for what
it does not recognise and says nothing. It is **not invertible**, and that is
recorded: two row statuses map onto KILLED, so the ROW and not the index is the
authority on how a campaign ended.

**ONE LITERAL IS DELIBERATELY LEFT AND MARKED.** The crash handler's
`tracking.end_run(status="FAILED")` beside a row finalized KILLED is an ARGUED
divergence, not a duplicate. Routing it through the mapping would give "KILLED"
and silently change what a crashed campaign is indexed as, in a pass whose whole
subject is removing UNINTENDED copies. The note beside it says so.

**F11 — `MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS` IS VALIDATED AT IMPORT.** The
node tested `_parallel_bound < 1`, which is not a type check, and this number
becomes `ThreadPoolExecutor(max_workers=...)`. Every plausible mistyping gets
past it: **`True` passes and means `max_workers=1`, so a campaign silently runs
per-trial mode SEQUENTIALLY while every report says otherwise and nothing ever
raises**; `4.5` passes and then raises inside the node, per patient, after the
warmup has been billed; `"4"` raises `TypeError`, which is not
`PerTrialParallelismError` and names nothing. `config.py` now carries the full
isinstance / not-bool / `>= 1` guard its warmup sibling already had, at import
and unconditionally. **The node's check stays** — the two ask different
questions, and a caller that sets the attribute AFTER import (which `run_node`
in the test suite does) bypasses the import guard entirely.

**F14 — `_account_unconsumed` APPLIES THE ANSWERING-MODEL CHECK.** Three places
fold a response's `model` into `model_answered`; the warmup and the send loop
both compared it against `config.matching_wire_model()` first and this one did
not. Its docstring argued that repeating the check would be wrong — true of the
DIAGNOSIS, and it overlooked what the function WRITES: `model_answered` is
returned as `matching_model` by all four failure returns that call it, and
`log_inference` STORES it and `get_model_cost` PRICES it. So an unchecked echo
folded from an abandoned response became the stored identity of the judge, **on
exactly the rows a reviewer reads when something went wrong**.

**RAISING IS THE RIGHT PRECEDENCE, AND THE SEVERITY ORDERING IS THE ARGUMENT.**
The failures that call it are RECOVERABLE — a refusal, a parse failure and a
non-list body all re-enter the node up to `MAX_LLM_CLASSIFIER_RETRIES` times —
and a model mismatch is not: every retry after it spends more on a judge nobody
chose.

**THE CHAINING CLAIM WAS MEASURED AND THE FIRST DRAFT SAID THREE.** It is **two
of four**: the API-error and JSON-parse branches call it from inside an
`except`, so the original exception travels as `__context__`; the refusal and
non-list branches are ordinary `if`s over a well-formed response and there is
nothing to chain. Both arms are asserted, so the asymmetry cannot rot into a
claim.

**F9** — `study.py`'s console `Status: COMPLETE` reads `STUDY_STATUS_COMPLETE`,
and the f-prefix went with it (it had no placeholder — pyflakes F541, which is
exactly the smell: a formatted string that formats nothing).

**F16 — `_create_run` RAISES, AND IT BORROWED THE WRONG ARGUMENT FOR WHY.**
`RUN_RECORD_FAILURES`' docstring said it "runs BEFORE the configuration's first
billed call, so a failure there costs nothing" — which is
`start_run_record`'s argument, true of the batch runner (one row, once, before
the first patient) and **FALSE HERE**: `_create_run` is called once per
CONFIGURATION, so on configuration 3 of 7 two whole configurations of live Stage
5 calls have already been billed. What makes raising affordable is the
CHECKPOINT, not the position. Both docstrings say that now, and three claims in
the first draft of the correction were re-measured and fixed before they shipped
— the crash handler finalizes the run row **KILLED** (the STUDY is CRASHED),
`open_run_id` is None at that point so nothing is left reading RUNNING, and
`ablation_results` **declares** a foreign key that nothing enforces (no
`PRAGMA foreign_keys` anywhere in the module).

**F3 — THE COMPOSE GRACE ARITHMETIC IS PINNED.**
`stop_grace_period: 620s` is `MATCHING_REQUEST_TIMEOUT_SECONDS × (1 +
OPENAI_SDK_MAX_RETRIES) + margin`, and nothing checked it — both terms are
config constants a later pass can move, and neither knows the YAML exists.
`tests/test_compose_shutdown_grace.py` asserts the **INEQUALITY, never `== 620`**,
so a legitimate timeout change moves it instead of failing it. The margin is
`SHUTDOWN_MARGIN_SECONDS`, a named constant carrying the uncalibrated label, and
it lives **in the test rather than in `config.py`** because nothing at runtime
reads it and that file's standing rule is that every tunable in it has a reader.
The per-trial arm's four-round worst case (2400s) is asserted to be a KNOWN,
DOCUMENTED shortfall, so turning that mode on cannot inherit a grace period
nobody re-derived.

**THE PORT — `tests/run_serial_tests.py` GOT THE FOUR LOCK HARDENINGS.** That
lock and `oncotriage/batch/runner.py`'s were the same shape and only one was
hardened. The mechanism is identical — a name in a world-writable directory,
derived from a path anybody can guess — and this one's blast radius is arguably
worse: a batch overlap bills a cohort twice, a serial overlap leaves a
deliberate defect in `cancer_code_registry.py` with both runs reporting success.

1. **`realpath`, not `abspath`, as the key.** One checkout reached through two
   names hashed to two digests, took two lock files, and BOTH RAN.
2. **A 0700 uid-keyed lock directory, `O_NOFOLLOW`, 0600.** The lock file's name
   is a SHA-256 of a path, so another user could pre-create it as a symlink and
   the first run to start would `O_CREAT` through it and `ftruncate` the target
   to zero.
3. **A UTC record with an explicit `Z`,** because the holder's start time is read
   by somebody deciding whether that run is stuck, often from a log written in
   another region.
4. **A typed `LockUnavailable` (a `RuntimeError`, converted at the acquisition
   site) and a new exit code 4.** `_run_all()` runs INSIDE the `with`, so an
   `except OSError` there would swallow every `OSError` the five subprocess
   launches can raise and report it as a lock failure.

**THEY ARE COPIED, NOT IMPORTED, AND THAT IS THAT FILE'S OWN RECORDED DESIGN:**
it imports nothing from the project so that it still reports a missing test file
rather than dying on an ImportError when the package is what is broken. The
copy's cost is paid by `tests/test_serial_runner_lock.py`, which asserts the
four properties THERE.

```bash
# The duplicated-derivation pass. Same shape, same directory. No network, no
# keys, NO SPEND, no live Qdrant, no model load, no corpus, no git history.
# None is in the collision matrix. Bucket A.
python tests/test_ablation_latest_run_selection.py   #  45, ~1.5s. EXECS NOTHING:
                                                     #  every control is a different
                                                     #  SQL STRING on one connection
python tests/test_compose_shutdown_grace.py          #  43, ~0.8s. NO DOCKER DAEMON
                                                    #  (was 30; the API-shutdown-gate
                                                    #  pass rewrote section 3 from
                                                    #  "the shortfall is real" to "the
                                                    #  GATE is the premise" and pins
                                                    #  it by AST over api/server.py,
                                                    #  with seven controls. Before
                                                    #  that 17; the default-flip pass
                                                    #  replaced section 3's arm-OFF
                                                    #  landmine with the GROUPED arm's
                                                    #  own worst case, a vocabulary pin
                                                    #  and two controls)
python tests/test_api_shutdown_gate.py                #  77, ~2s. NO BILLED CALL,
                                                     #  no network, no keys, no live
                                                     #  server, no live Qdrant, no
                                                     #  model load, no corpus, no git
                                                     #  history, no Docker daemon.
                                                     #  The SIGNAL half is installed
                                                     #  for real and INVOKED through
                                                     #  signal.getsignal over a
                                                     #  RECORDING previous handler, so
                                                     #  the chain is COUNTED rather
                                                     #  than inferred from a process
                                                     #  dying; a real signal is
                                                     #  deliberately not delivered,
                                                     #  because the chain reaches
                                                     #  SIG_DFL in a bare process. The
                                                     #  LIFESPAN half is driven with
                                                     #  TestClient as a context
                                                     #  manager. Section 3 drives the
                                                     #  REAL Stage 5 node against a
                                                     #  counting stub on either side
                                                     #  of the flag. EXECS NOTHING.
                                                     #  Not in the collision matrix.
python tests/test_ablation_write_durability.py        #  43, ~3s. No network, no
                                                     #  keys, no spend; every
                                                     #  database is a temp file and
                                                     #  the contention is real (a
                                                     #  second connection takes a
                                                     #  genuine BEGIN EXCLUSIVE). Not
                                                     #  in the collision matrix (was
                                                     #  33; the signal-safe-restore
                                                     #  pass added 3e-3k over
                                                     #  WRITE_RETRY_OUTCOMES, driven
                                                     #  on a callable that raises a
                                                     #  stated number of stated
                                                     #  exceptions -- 3a cannot say
                                                     #  how many attempts it took,
                                                     #  which is what makes it a good
                                                     #  test of "the write lands" and
                                                     #  a useless one of "the retry
                                                     #  fired")
python tests/test_serial_runner_lock.py              # 204, ~1.3s. REAL concurrent
                                                     #  subprocesses, a REAL symlinked
                                                     #  checkout, a REAL SIGKILL and a
                                                     #  REAL SIGTERM (was 122; the
                                                     #  signal-safe-restore pass added
                                                     #  section 10 over the pristine-copy
                                                     #  guard -- a run killed mid-plant,
                                                     #  the successor's repair, the
                                                     #  SIGTERM arm, the refusal, and
                                                     #  the pure pieces -- with TWELVE
                                                     #  planted reverts, twelve caught,
                                                     #  FOUR of which found defects in
                                                     #  the checks rather than in the
                                                     #  code.
                                                     #  Before that 85; the consolidation
                                                     #  pass added section 9, which pins
                                                     #  this file's COPY against
                                                     #  oncotriage/control.py by AST
                                                     #  with eleven planted controls)
```

**`tests/test_serial_runner_lock.py` DOES NOT RUN THE REAL SERIAL SUITE**, and
that is the design decision in it. It builds a throwaway checkout holding a
**byte-identical** copy of the entry point (sha256-compared, so the lock under
test is the shipped lock) beside five one-line STUB scripts at the five paths
`SERIAL_TESTS` names — read off the module rather than retyped. The entry point
is the real one and `_run_all` really launches subprocesses, but the payload is
harmless, **so a BROKEN lock costs two stub runs rather than two source
rewrites**. The holder PARKS ON A FILE rather than sleeping, so the refusal is a
statement about the lock and not about this machine's scheduler.

**TWENTY-EIGHT REVERTS, TWENTY-EIGHT CAUGHT**, each applied to a `copytree`'d
copy with `PYTHONPATH` pointed at it, a realpath preflight asserting the COPY is
what imports, `PYTHONDONTWRITEBYTECODE=1` set, and every plant asserting its own
occurrence count so a plant that matched nothing is a named `PLANT-FAILED`
rather than a working check reported as broken. **One of the twenty-eight is
caught by an IMPORT-TIME `RuntimeError` rather than by a recorded failure** —
deleting `TRACKING_STATUS_FOR`'s STOPPED row makes `runner.py` unimportable,
naming both sets — which is that guard working as designed.

**SIX PINNED CHECKS MOVED, AND EVERY ONE OF THEM WAS THE CHECK WORKING.**
`tests/test_storage_run_identity.py` **142 → 155** (its status walk read
`ast.Constant` only, so a Constant-only walk over the new named constants would
have found nothing and passed VACUOUSLY; its CONTROL 4 plant anchored on the
one-line literal call); `tests/test_storage_run_metrics_flush.py` **123 → 124**
(it located the crash handler by searching `ast.dump` for the string
`'KILLED'`); `tests/test_agent_stage5_per_trial_calls.py` **283 → 318**.
`tests/test_package_invariants.py` is unchanged at **260/0/0**.

**AND SECTION 1c CAUGHT THIS PASS'S OWN NEW TEST.** The first version of
`tests/test_serial_runner_lock.py` reached its subject with
`importlib.util.spec_from_file_location` and argued in its docstring that the
by-location rule "is about `oncotriage` PACKAGE modules". **That reasoning was
wrong and the check is unconditional, with no allowlist escape** —
`tests/test_runner_sigterm_shutdown.py` records being caught by exactly the same
check for exactly the same reason. It is an ordinary `import` with `tests/` on
`sys.path` now, which is stronger anyway: one entry in `sys.modules` rather than
a second copy with its own state. The `__main__` guard is asserted BEFORE the
import, because without one the import would launch the nine-minute
source-rewriting suite as a side effect of a test.

**FIVE DEFECTS IN THIS PASS'S OWN TEST CODE WERE FOUND BY RUNNING, NOT BY
READING**, and three of them are this project's recurring shapes:

- **A CHECK THAT ABORTED.** `time.strptime` on a stamp whose FORMAT a defect had
  changed — which is one of the two defects that section exists to catch — raised
  while `check()`'s argument was being evaluated, and the run reported one
  traceback where it owed a summary and thirty results. **The thirteenth time.**
- **TWO CHECKS THAT COULD NOT DISCRIMINATE, both reported by the revert
  harness as MISSED rather than by reading.** The record's `realpath` check
  compared against `realpath(_CODE_DIR)` — but `os.getcwd()` already resolves
  symlinks on this platform, so it was `x == x` and passed against a reverted
  writer; it drives a genuinely unresolved path now. And the `lstat`-vs-`stat`
  check had no case that separates them: a file is not a directory to both, and
  a real directory's mode bits read the same to both. **Only a SYMLINK TO A GOOD
  DIRECTORY separates them**, and that case is now the control.
- **A SCAN THAT REPORTED ITS OWN ARGUMENT.** `4f` asserted the module contains
  no `MAX(run_timestamp)` using `ast.unparse`, on the premise that it strips
  docstrings. **It does not** — they are ordinary `Expr(Constant(str))`
  statements — so the two occurrences it found were the owner's own prose
  explaining the correction. The obvious stripper would not have fixed it either:
  that docstring is an ATTRIBUTE docstring, a bare string FOLLOWING an
  assignment, which a `body[0]`-only stripper leaves in place.
- **A HARNESS THAT REPORTED FOUR WORKING CONTROLS AS MISSED.** The revert
  harness parsed only `N passed, M failed`; two summary formats live in
  `tests/` and the other reads `passed: N` / `failed: M`, in either case. A
  parser that knows one family returns 0/0 for the other, which reads exactly
  like an abort.

**VERIFIED BY RUNNING.** CI bucket A **69/69**; `tests/run_serial_tests.py`
**5/5** with `oncotriage/registries/cancer_code_registry.py` confirmed
byte-unchanged and `oncotriage/config.py` confirmed to carry only this pass's
own edit; `tests/test_package_invariants.py` **260/0/0**; every affected bucket
E file at its documented count (`test_ablation_db_isolation` 72,
`test_ablation_stop_and_lock` 142, `test_storage_write_durability` 100,
`test_agent_patient_hash_coverage` 69); `python fixture_replay.py` **12/12
clean, exit 0, with no recapture**; and the production `inferences.db`
(`ab1403e3…`), `ablation_results.db` (`f2bc23c6…`) and all twelve fixture files
byte-identical to the hashes taken before the pass began. **No money was spent
and no migration was run.**

**WHAT IS NOT DONE, NAMED RATHER THAN LEFT TO BE DISCOVERED.**
`lock_directory()` and `ensure_lock_directory()` now exist in THREE copies —
`oncotriage/batch/runner.py`, `oncotriage/ablation/study.py` and
`tests/run_serial_tests.py`. The consolidation is a recorded deferral, not an
oversight: the third copy is forced by that file's no-project-imports rule, and
the other two are a shared-module question of their own. What keeps them from
drifting silently is that all three verify the SAME directory and each has a
test asserting the properties in its own file; what separates the three locks is
the FILE PREFIX — `oncotriage-batch-run-`, `oncotriage-ablation-run-` and
`oncotriage-serial-tests-` — which is load-bearing and must stay distinct.

### The default call mode is per-trial (the default-flip pass)

**`MATCHING_PER_TRIAL_CALLS_ENABLED` IS `True`. THAT IS THE PIPELINE'S DESIGN,
RULED BY THE OPERATOR, NOT A MEASUREMENT THIS PASS TOOK.** Per-trial is the only
arm that removes cross-trial reasoning contamination by CONSTRUCTION rather than
bounding it — the input-packing block's own record is that "reasoning
demonstrably leaks between trials inside one prompt, which is the thing
constraint C4 asks the model not to do and cannot enforce", and a ~12K token
budget bounds how big a shared prompt gets without stopping two trials sharing
one. **Grouped is RETAINED behind the same switch as the migration's documented
comparison arm**, and section 8 of
`tests/test_agent_stage5_per_trial_calls.py` is what keeps it exact.

**NO BILLED CALL WAS MADE.** `python fixture_replay.py` is **12/12 clean, exit
0, with no recapture** UNDER THE NEW DEFAULT, the production `inferences.db`
sha256 is unchanged (`ab1403e3…`, 90,185,728 bytes), and no migration was run.

**═══ NO PAID PER-TRIAL RUN BEFORE THE THREE-CALL PROBE. IT IS THE MIGRATION
WINDOW'S FIRST COMMAND. ═══** Two facts this arm rests on have never been
observed against the live provider, and both fail in the expensive direction
rather than the loud one:

| | what has never been seen | what happens if it is false |
|---|---|---|
| **warmup acceptance** | that `MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS = 1` comes back 200 rather than 400 | the campaign RUNS. `evaluation.py` classifies the 400 and falls back to the retired one-then-rest schedule PER PATIENT, with no process memo — measured at 1,000 patients: **$0** in refused warmups (a 400 is refused before generation) and roughly **22 minutes** of added wall time at `MAX_WORKERS = 12` |
| **prefix warming** | that call 3 reports `cached_tokens > 0` after two identical-prefix requests | per-trial costs `MAX_TRIALS_FOR_EVALUATION` × the grouped input price and **NOTHING RAISES** — every request succeeds, every verdict is produced, and the only trace is `cached_tokens` reading 0 in `inferences.llm_classifier_call_details` |

The probe is **three calls**: one warmup, then two identical-prefix trial calls.
Read the answer out of the **usage block**, never the wall clock. It is the
`bedrock_probe.py` shape — a deliberate, flagged, tiny spend that answers a
configuration question before a campaign's worth of money rests on it — and
until it has been run, per-trial mode is a configuration nobody has seen serve a
request.

**PER-TRIAL FIXTURES ARE THE STANDING MIGRATION ITEM, AND THE GATE SURVIVED THE
FLIP BY PIN RATHER THAN BY LUCK.** `fixture_capture.py` and `fixture_replay.py`
call `capture.pin_call_mode_for_fixture_process()` as the first statement of
their `main()`, pinning Stage 5 to grouped for their own process and PRINTING
that they did — the call-mode-pin pass built exactly this, and the flip is what
it was built for. Measured: the replay log's fourth line reads
`Stage 5 call mode: PINNED to 'grouped' for this process by fixture_replay.py`.
So **the twelve fixtures characterize the GROUPED arm and say so on their face**,
and the shipped arm's Stage 5 behaviour is covered by
`tests/test_agent_stage5_per_trial_calls.py` alone. Closing it needs a
trial-stable ordering for `RecordingSink`'s `chat_completions` bucket (it numbers
by ARRIVAL, so a per-trial capture's "deterministic" prefix would be ordered by
the thread scheduler) plus a paid recapture of all twelve — a fixture-FORMAT
change with a `SCHEMA_VERSION` bump.

**THE FLIP FOUND TWO LANDMINES AND BOTH WERE THE SAME SHAPE: A TEST THAT FAILS
ON THE CHANGE IT EXISTS TO PROTECT.** Neither was a defect in the pipeline;
both were assertions written as a second copy of the shipped default.

- **`tests/test_compose_shutdown_grace.py` check 3c** read
  `MATCHING_PER_TRIAL_CALLS_ENABLED == False` under the comment "which is the
  premise under which section 1 is sufficient", and its own section header said
  "IF THE MODE EVER SHIPS ON, THIS SECTION MUST BE REVISITED RATHER THAN
  PASSING". **The premise was false when it was written.** Section 1's
  sufficiency rests on the BATCH RUNNER's Stage 5 shutdown gate, which bounds
  the drain to one in-flight request in BOTH arms (the operator-control pass
  gated the grouped send loop too) — not on the arm. On the `fastapi` service,
  which has no gate of any kind, the worst case is **2400 s in BOTH arms**: 4
  rounds × 600 per-trial, and `MATCHING_MAX_INPUT_PACKED_CHUNKS - 1` = 4 further
  chunks × 600 grouped. **So the shortfall was never a property of the arm and
  the flip did not move the number.** Section 3 is arm-INDEPENDENT now: both
  worst cases derived from the constants, both required to exceed the grace
  period, both derivations required to be present in the compose file, plus a
  vocabulary pin (3e) so a third call mode cannot inherit a grace period nobody
  re-derived, and two controls. **17 → 21.**
- **`tests/test_fixture_call_mode_pin.py` check 1c's non-degeneracy probe**
  pinned the LITERAL `per_trial` and then asserted the pin and the constant
  DISAGREED. After the flip, pinning per-trial pins what is already in force,
  the two agree, and the probe goes red naming a mechanism that works perfectly.
  It pins `_OTHER_ARM` — derived as the opposite of the configured one — so the
  disagreement holds in either arm by construction. **81 → 82.** That file's own
  docstring already recorded this lesson about its check 1a; the probe five
  lines below it had the identical defect.

**THE FLIP'S LARGEST FINDING WAS NOT IN THE BRIEF: SEVEN TEST FILES DROVE THE
STAGE 5 NODE WITHOUT SETTING THE ARM, AND EVERY ONE OF THEM SILENTLY CHANGED
SUBJECT.** CI bucket A went 69/0 to **69/7** on the flip alone. None is a defect
in the pipeline — the per-trial behaviour each of them met is correct — and none
could have been found by reading, because the arm was never mentioned in any of
them:

| file | what it measures | what per-trial did to it |
|---|---|---|
| `test_agent_stage5_input_packing.py` | the INPUT packer | per-trial BYPASSES the packer outright; every assertion was about a mechanism that did not run |
| `test_agent_emission_provenance.py` | `emission_index` restarting per CALL, `call_index` following the billed call | needs a packer that produced several chunks; per-trial chunks are singletons |
| `test_agent_out_of_set_detector.py` | cross-chunk reconciliation | "an id belonging to another chunk" requires more than one chunk |
| `test_agent_state_channel_coverage.py` | the packer's three provenance channels | `llm_classifier_packed_chunks` / `llm_classifier_packing` are NULL by design in per-trial |
| `test_storage_packing_and_cache_columns.py` | those channels' four persisted columns | same |
| `test_agent_patient_record_tokens.py` | the ONE system prompt rendered above the split loop | per-trial adds a warmup carrying the identical system message, so the render arithmetic moves |
| `test_agent_trial_verdict_normalization.py` | one response per patient | a stub serving the same body to N trial calls produces N of everything: a malformed-entry count of 1 becomes N. **Correct behaviour**, and not what the checks measure |

**THEY PIN THE GROUPED ARM THROUGH `config.pin_matching_call_mode()`, NEVER BY
WRITING THE CONSTANT**, and that is the same argument the fixture harness
already makes about itself. Assigning `MATCHING_PER_TRIAL_CALLS_ENABLED` would
be a second WRITER of a declared configuration value — the shape this project
keeps removing — and would leave that constant, read anywhere later in the
process, saying the project is configured grouped when it is not. The pin says
what the PROCESS is forced to do and `matching_call_mode_pin()` reports it;
every consumer the node reaches follows the owner, so one line redirects Stage
5's partition, `inferences.matching_call_mode`, the resume fingerprint and the
tracking index consistently.

**IT IS A HARD GUARD, NOT A `check()`.** A pin that did not take leaves every
assertion below silently measuring the other arm — not one failure but every
failure with a misleading message, which is the case this suite already reserves
`SystemExit` for (a wrong project root). **Demonstrated: the pin removed in a
copy of each of the seven, all seven abort on stderr naming the arm they found.**

**AND IT IS RELEASED BEFORE THE SUMMARY, WHICH THE FIRST VERSION GOT WRONG IN
THREE OF THE SEVEN.** The release is process-global state and `pytest tests/`
imports every module into ONE process, where a leaked grouped pin would make
`tests/test_agent_stage5_per_trial_calls.py`'s explicitly-per-trial sections run
grouped without a word — and would fail its check 1a-ii. The first placement put
the release BELOW the results line in the three files whose summary is
`print("\n" + "=" * 75)` / `RESULTS:` / `print("=" * 75)`, because the anchor
matched the CLOSING banner: the outcome still decided the exit code while being
absent from the number the summary printed. **A run that reports "0 failed" and
exits 1** — caught by comparing every count against a pristine `git worktree` at
HEAD rather than against the numbers in this file. It is above the summary now
and all seven moved by exactly +1. **Control: the restore skipped in a copy, the
release check fires in all three tried.**

**WHAT THIS COSTS, STATED RATHER THAN GLOSSED.** The shipped arm's behaviour on
those seven subjects is NOT covered by them any more, and two of the seven are
genuine gaps rather than mechanisms that do not exist in per-trial mode: the
per-CALL multiplicity of the normalizer's counters (`MALFORMED_EVALUATION_
ENTRIES` and friends counting once per response, N responses per patient), and
the render arithmetic with a warmup in it. Both are named in the files' own pin
blocks. `tests/test_agent_stage5_per_trial_calls.py` covers the partition, the
dispatch, the warmup, the isolation, the floor and the out-of-set semantics at
chunk size one; it does not cover those two.

**THE PROCESS-GLOBAL PIN LEAK IS CLOSED, AND IT STOPPED BEING INERT ON THE DAY
OF THE FLIP.** `capture.main()` installs a process-global pin and does not clear
it — correct for a real capture run, and its own docstring records that a test
driving it inherits the pin. `tests/test_resume_capture_and_ragas.py` drives it
**three times**. While grouped was the default the pin agreed with the default
and the leak changed nothing observable; now a leaked pin makes
`config.matching_call_mode()` answer "grouped" for every check after it and for
every file sharing the process — **silently, because the pin's whole design is
that consumers cannot tell it from the default**. `drive_main`'s `finally`
clears it, beside the two restores already there. Three checks make that a
measurement rather than untested code: the pin state is RECORDED before it is
cleared, so "no pin afterwards" cannot be satisfied by a `main()` that never
installed one. **207 → 210.**

**THE PER-TRIAL TEST FILE'S IDIOM WAS NOT CHANGED, AND THAT IS THE DECISION
RATHER THAN INERTIA.** Every drive goes through `run_node`, which writes the
flag explicitly and restores it in a `finally`; no section reads the shipped
default to decide what it exercises. Making the ON-arm sections read the default
would (i) exercise NOTHING the day the default moved back for a comparison
campaign — every arm section would silently become a second copy of section 8 —
and (ii) make each assertion a statement about two things at once, so a failure
could not distinguish "the mechanism broke" from "somebody moved the default".
**Explicit on both sides means the file measures the MECHANISM and check 1a
alone measures the DECISION.** What moved: 1a inverted (with the retired text
kept as the record), 1a-ii added over the unpinned owner, and 10e's restore
derived from `_START_CONFIG` — captured at import — rather than the literal
`(False, 4)`, with 10f as its non-degeneracy probe. **318 → 320.**

**ONE STALE CROSS-REFERENCE WAS FOUND AND CORRECTED WHILE REWRITING THE
CONSTANT'S BLOCK.** It pointed at "section 6 … and section 7's control" of the
per-trial test for the OFF-exactness proof; those are sections **8** and check
**8h**. Stale by two, and it is now written as a section AND a check so the next
renumber is visible.

**WHAT DID NOT MOVE, CHECKED RATHER THAN ASSUMED.** `docker-compose.yml`'s
`stop_grace_period: 620s` — the shortfall is arm-independent, so the flip
changes which arm the comment states FIRST and not the number. The three
resume gates (`batch/runner.py`, `ablation/study.py`,
`evaluation/run_harness.py`) already gate `matching_call_mode` as a fingerprint
field, so a grouped-mode checkpoint resumed under the new default refuses by
name with no edit — that was the call-mode pass's work and this pass only
inherits it. `FINGERPRINT_VERSION` is unchanged at **3** (verified): the flip
changes a VALUE the stamp already carries, not the stamp's shape, so no
v3-stamped artifact refuses for a SHAPE reason — it refuses, correctly, for a
CHANGED-FIELD reason if it was written in the other arm.
`SCHEMA_USER_VERSION` is unchanged at **4** for the same reason — `inferences.matching_call_mode` and `runs.matching_call_mode` already
exist and simply start carrying the other member.

**WHAT IS NOT DONE, NAMED RATHER THAN LEFT TO BE DISCOVERED.**

1. **Two of the seven pinned files have a REAL per-trial coverage gap**, named
   above and in their own pin blocks: the per-CALL multiplicity of the Stage 5
   normalizer's counters, and the fixed-prefix render arithmetic with a warmup
   in it. Neither is covered by
   `tests/test_agent_stage5_per_trial_calls.py`.
2. **`GET /pipeline/info` does not report the call mode.** Its stage-5 line
   interpolates `MATCHING_MODEL` and says nothing about how many requests that
   model gets, so an operator asking the API which pipeline it is running cannot
   see the arm. Adding it is a contract change to a served response and belongs
   to a pass that can measure it.
3. **The three-call probe has not been run** — it costs money and this pass made
   none. Nothing in the tree can turn per-trial mode's two live assumptions into
   measurements without it.
4. **Per-trial fixtures** (above). The shipped arm has no characterization gate.
5. **`MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS = 4` is still uncalibrated** and is
   still labelled one: it was derived from an ESTIMATED ~15 s single-trial
   latency. `inferences.llm_classifier_call_details` is the measurement it is
   missing, and it exists only after a real per-trial run.
6. **No shutdown gate on the `fastapi` service.** 620 s covers one in-flight
   request; a whole patient is 2400 s in either arm. The repair is a gate, not a
   bigger number, and it is now the shipped arm's problem as well as the
   retained one's.
7. **No process memo for a warmup rejection**, so every patient re-discovers it.
   Argued at the dispatch-hardening pass: a transient 400 memoized process-wide
   would disable the warmup for a whole campaign on one hiccup.


### One owner for the run lock and the stop switch (the consolidation pass)

**THREE COPIES OF A MECHANISM IS THREE PLACES TO FORGET A SECURITY FIX, AND
THAT IS AN OBSERVATION RATHER THAN A PREDICTION.** The lock-hardening pass
applied FOUR fixes — `realpath` rather than `abspath` as the key, a 0700
uid-keyed directory with `O_NOFOLLOW` and 0600 on the file, a UTC record with an
explicit marker, and a typed refusal rather than a bare `OSError` — and had to
apply each of them THREE times, one file at a time, with nothing that would have
failed had it stopped after two. The pass before it had applied the same fixes
to ONE of the three and left the other two, which is how the divergence arose in
the first place. **`oncotriage/control.py` is the one owner.** No behaviour
change: `python fixture_replay.py` is **12/12 clean, exit 0, with no
recapture**, and every operator-facing text is **byte-identical** (below).

**WHAT IS SHARED AND WHAT IS NOT, DECIDED PER ITEM.** Shared: the lock
directory and its three verification checks, the open/flock/record/release
sequence, both refusal classes' payloads, the mechanical half of both refusal
texts (the record loop, the symbolic errno, the `at:` line), the bounded stop
note reader with its tail probe, the three-member clear vocabulary, the
queued-work sweep, and the latching poll. **NOT shared, each a parameter or a
subclass with its argument at the site that keeps it:**

| kept per program | why |
|---|---|
| the lock file PREFIX (`oncotriage-batch-run-` / `-ablation-run-` / `-serial-tests-`) | all three locks live in ONE per-user directory, so the prefix is the only thing keeping them apart. A batch run and a study guard different things and must not refuse each other |
| the KEY (a checkpoint DIRECTORY / a study's checkpoint FILE / a checkout) | a `--db` study locks independently exactly as it checkpoints independently |
| `AlreadyRunning` / `LockUnavailable` | see below — the argument CHANGED and is restated rather than carried across |
| `EXIT_LOCK_UNAVAILABLE` (1 / 1 / 4) | its value is read off the entry point's OWN exit vocabulary. `EXIT_LOCKED` IS shared, because all three agree on 3 and agree on why |
| the refusal PROSE | a cohort billed twice, a configuration's sample split across two `ablation_runs` rows, and a restore writing back a planted tree are three consequences with three remediations |
| the `STOP_SWITCH_FAULTS` counters | a batch fault and a study fault are different findings, and `oncotriage/degradation.py` cannot import a module that imports nothing back |

**THE EXCEPTION-CLASS ARGUMENT EXPIRED IN HALF AND IS REWRITTEN RATHER THAN
MOVED INTACT.** Both classes argued that a shared one would drag the whole batch
module — its checkpoint, its ledger, its graph — into every study's import
graph. **That is no longer true of anything**: `control` imports nothing from
the project. What survives is that the refusals are raised by different
programs, name different consequences and are remediated with different
commands, and a caller holding more than one lock — this suite already has a
test that drives two — must tell them apart by TYPE rather than by parsing a
path out of a message. So they are **siblings under a shared base**: the
mechanism has one owner, the refusals keep two identities, and
`tests/test_ablation_stop_and_lock.py` gained **3k-b2**, without which the
separation is equally satisfied by two COPIES of the class, which is what it
was.

**`tests/run_serial_tests.py` KEEPS ITS COPY, AND THE RULE WAS READ BEFORE THAT
WAS DECIDED.** Its no-project-imports argument has two clauses. The first — an
import of the batch runner would drag the graph into a process launcher — is
dissolved by `control.py`. **The SECOND is not**: the rule exists so `python
tests/run_serial_tests.py` still reports a missing test file rather than dying
on an ImportError WHEN THE PACKAGE IS WHAT IS BROKEN, and `import
oncotriage.control` executes `oncotriage/__init__.py` and needs the package on
`sys.path`, which that launcher deliberately does not arrange. The by-location
escape is closed too: `tests/test_package_invariants.py` section 1c forbids
loading a module by location **unconditionally**, with no allowlist, and has
already caught one test file doing exactly that.

**SO THE COPY STOPS BEING UNPINNED.** `tests/test_serial_runner_lock.py`
**section 9** (85 → **119** checks) compares it against `control.py` by AST:
`lock_directory` byte-identical after `ast.unparse`; `ensure_lock_directory`
identical with string CONSTANTS blanked, which tolerates the one declared
difference (a noun in three messages) and nothing that decides anything — an
`lstat` turned into a `stat`, a dropped uid check, a widened mode mask and a
removed raise all survive the blanking and fail the comparison; both exception
`__init__` bodies byte-identical; and the acquisition compared FACT BY FACT
(open flags, flock flags, the five syscalls and their counts, the
ftruncate-BELOW-flock ordering, the close in a `finally`, the `gmtime` stamp,
the `realpath` key), because `control`'s is parameterized and this one is not.
**Eleven planted controls, eleven fired**, every plant into an in-memory copy
with the file hashed and compared at the end. Two functions in the launcher
gained a `-> str` annotation, which is the ONE difference that had to go so the
comparison could be equality rather than equality-with-a-tolerance.

**THE REVERT MATRIX IS THE ACCEPTANCE CRITERION AND IT FOUND A GAP THIS
CONSOLIDATION INHERITED.** Twenty-eight reverts, each into a `copytree`'d copy
with `PYTHONPATH` pointed at it, a realpath preflight and
`PYTHONDONTWRITEBYTECODE=1`; every plant asserts its own occurrence count, so a
plant that matched nothing is a named PLANT-FAILED rather than a working check
reported as weak. Twenty-six were caught. **Two were MISSED — `LOCK_DIRECTORY_MODE`
widened to 0777 and `LOCK_FILE_MODE` to 0666 — and that is PRE-EXISTING rather
than a regression:** measured against `git show HEAD:`, those values were pinned
for the SERIAL RUNNER's copy alone, and the batch runner's and the study's each
declared their own with no test anywhere asserting either. Section 9's **9d-j**
closes it at the one owner, with **9f-10** and **9f-11** as its controls.
Widening either is the substitution re-opened: a 0777 lock directory is one
another user can claim a name in, and a 0666 lock file is one that can be
rewritten under a holder that is still running.

**TWO DEFECTS IN THIS PASS'S OWN WORK WERE FOUND BY RUNNING, NOT BY READING.**

- **`console.out` WAS CAPTURED IN THE CONSTRUCTOR AND LATE BINDING WAS LOST.**
  The first `StopSwitch` took `out=console.out` and `log_warning=log.warning` as
  parameters; both subclasses had written `console.out(...)` INSIDE the poll, so
  the module attribute was looked up AT CALL TIME. A probe that patched
  `console.out` on the module captured **nothing** while the announcement went
  to the real stream. They are `_emit` / `_warn` METHODS now, overridden per
  program and resolving at call time.
- **THE BATCH SWITCH INHERITED `arm()` AND WOULD HAVE SILENTLY DONE NOTHING.**
  The base offers it because the study's sentinel follows `--db`; the runner
  resolves its own path every poll, so a bound path would be stored and never
  read. Before the consolidation this class had no `arm` at all, so the call
  raised `AttributeError` at the call site — **a silent no-op replacing a loud
  failure**, which is the exact shape this project removes. It raises
  `TypeError` naming the owner, and `tests/test_runner_stop_switch.py` **1m-x /
  1m-y** are the standing checks.

**HOW "NO BEHAVIOUR CHANGE" WAS ESTABLISHED: BY RENDERING, NOT BY READING.** A
probe imports `git archive HEAD`'s tree and the working tree in separate
processes and renders every operator-facing text the pass touches — both
refusals with a full holder record and with a garbage one, both unopenable-lock
diagnoses, both stop announcements, the `--clear-stop` failure block against a
genuinely read-only directory, and `_read_stop_message` across six edge cases
(empty, whitespace-only, short, exactly-cap-plus-newline, oversize, and
whitespace exactly at the boundary with content after it). **Identical, with
temp paths normalised.** The first run of that probe is what found the
late-binding defect above.

**F10 — `tests/_control_harness.py`, AND THE PARK PROTOCOL WAS THE POINT.** No
`test_` prefix, deliberately: every runner this project has selects on it, and a
file of no checks would report "0 passed" and be counted as a file that ran. It
owns `CLOSED_PORT_URL` (five copies of a magic string is five chances to typo
one into a port something is LISTENING on), the deadline waiter — whose `alive`
argument is the half that is easy to forget, since a wait for a marker a DEAD
process was never going to write burns the whole timeout — and **one park
protocol replacing three incompatible encodings**: `ONC_PARK` as `"1"`/`"0"`,
`ONC_PARK` as `"yes"`/`"no"`, and `ONC_PARK_PHASE` as a phase name with
`"none"`. **Two of them read the SAME variable with different vocabularies**, so
a hook copied from one file into the other parks on `"no"` — truthy in one and
"never park" in the other. The phase form survived because it is the general one
and the two booleans are its endpoints. It imports nothing from the project,
which is required rather than tidy: it is imported by `usercustomize.py`
stand-ins that run at INTERPRETER STARTUP, and an `oncotriage` import there would
change what the process under test had loaded before its own first line.

**F12 — `STOP_MESSAGE_MAX_CHARS`, `STOP_MESSAGE_TAIL_PROBE_CHARS` AND
`EXIT_LOCKED` LIVE IN `control.py` WITH A STATED EXCEPTION TO THE
CONFIG-TUNABLES RULE.** That rule exists so an operator has one file to look in,
and it comes with a matching promise — every constant there has a reader and
therefore an effect. A bound on a SHUTDOWN-PATH allocation, a probe width and an
exit code are properties of a mechanism, not settings of a pipeline; an operator
changing any of them changes nothing about what a run does or costs. And moving
them would make `oncotriage/config.py` an importer of this module, which is the
one thing it may not have.

**F15 — `llm_classifier_call_details`' `call_index` DOES NOT ORDER BY THE SAME
THING IN BOTH ARMS**, and the column note said "in the order they were issued"
flatly, which is true of one of them. GROUPED mode issues one request, waits,
counts it and issues the next, so issue order and accounting order are one
sequence. PER-TRIAL mode submits every trial call to a thread pool up front and
consumes responses in the order the node ASKS for them — the pending LIFO's pop
order, which is packing order — so `call_index` follows the ACCOUNTING order and
the order the requests actually reached the provider is the pool's and is
recorded nowhere. Deterministic in both arms, which is what the join to
`trial_matches.call_index` needs; what it is NOT, on the shipped arm, is a
wire-order timeline — **and that is the reading that matters, because whether
the cache warms is a question about what went out first.**

```bash
# The shared harness the five operator-control test files import. NOT a test
# and it has no `test_` prefix: every runner selects on that prefix and a file
# of no checks would report "0 passed" and be counted as a file that ran.
tests/_control_harness.py
```

**WHAT IS NOT DONE, NAMED RATHER THAN LEFT TO BE DISCOVERED.**

1. **`lock_directory()` / `ensure_lock_directory()` still exist twice** — in
   `oncotriage/control.py` and in `tests/run_serial_tests.py`. That is forced by
   the launcher's own rule, argued above, and it is now PINNED rather than
   merely duplicated. What the pin does NOT cover is the refusal prose and the
   `lock_path` signature, which are declared divergences.
2. **The stale-sentinel preflights are NOT consolidated.**
   `assert_no_stale_stop_switch` and `assert_no_stale_ablation_stop_switch`
   remain two functions. Their bodies are three lines of shared shape wrapped
   around wholly different multi-line refusal text naming different commands, so
   the shareable part is smaller than the parameter list it would need.
3. **`report_stop_switch_faults` is the study's alone.** The batch runner reports
   its counter through `oncotriage/degradation.py`'s registry instead, which is
   a genuine asymmetry rather than a duplicate — the study's counters are
   excluded from that registry by name.
4. **The two `_emit` / `_warn` overrides are four identical lines in two
   modules.** They cannot be shared without `control` importing
   `oncotriage.observability`, which is the one import it may not have; that is
   the price of the no-project-imports property and it is paid deliberately.


### The suite survives its own death, and the study reports its health (the signal-safe-restore pass)

**FOUR ITEMS. NO BILLED CALL WAS MADE**: `python fixture_replay.py` is **12/12
clean, exit 0, with no recapture and zero CONFIG MOVED lines**, and the
production `inferences.db` sha256 is unchanged.

**1. `tests/run_serial_tests.py` KEEPS A PRISTINE COPY THAT OUTLIVES THE
PROCESS.** The lock is about two runs; nothing was about ONE RUN DYING, and it
is the same consequence. Both source-mutating tests keep their pristine copy in
a `tempfile.mkdtemp()` of their own and restore it in their own `finally` -- so
a SIGTERM, a `docker stop`, a closed terminal or a SIGKILL that skips that
`finally` leaves `oncotriage/` holding a DELIBERATELY PLANTED DEFECT **and
destroys the only copy of what it replaced**, because the temp directory's name
existed nowhere but in the dead process. `tests/test_config_snapshot_date_rot.py`
rewrites `DATA_SNAPSHOT_DATE`, and a checkout left in that state computes every
patient's age against a fabricated reference date without a word. **This project
has already paid once for a silently-reverted edit to that exact file.**

**THE COPY IS A SIBLING WITH THE RUNNER'S PID IN ITS NAME**
(`oncotriage/config.py.serial-runner-pristine-4213`), taken inside the lock and
before the first test, and it has TWO arms:

| gesture | what puts the tree back |
|---|---|
| a failing test, a clean exit, Ctrl-C | `pristine_guard`'s own `finally` |
| SIGTERM, SIGHUP | the same `finally`, because `shutdown_signals_reach_cleanup` converts both to a SystemExit -- CPython's default for either terminates outright, no unwinding |
| **SIGKILL, a panic, a power cut** | **NOTHING RUNS.** The NEXT invocation repairs from the copy, announcing what it put back, before it runs anything |

**SIGKILL CANNOT BE CAUGHT AND THAT IS THE REASON THE DESIGN HAS A SECOND HALF,
not a gap in it.** A guard whose only arm is a `finally` is absent exactly when
the machine went down.

**STALENESS IS DECIDED BY THE LOCK, NOT BY ASKING WHETHER THE PID IS ALIVE, and
the pid test is the WEAKER of the two rather than a second opinion.** A copy is
taken after the lock is acquired and removed before it is released, both inside
one `with` -- so a copy that exists means some run never reached its cleanup,
and that run is either dead or holding the lock, which we hold. Every copy found
is stale by construction. A pid is REUSED: the pid of a run SIGKILLed yesterday
is very likely somebody else's process today, and a liveness test would read a
genuinely stale copy as live and REFUSE TO REPAIR IT, leaving the plant in the
tree. The pid is recorded and reported because it is what an operator correlates
with a CI log; it decides nothing.

**REPAIR RUNS ABOVE THE BACKUP.** Taking a copy of a corrupted file would freeze
the plant into the thing meant to undo it, and every later run would then
"repair" the tree back to the defect.

**EVERY WRITE IS ATOMIC, BOTH DIRECTIONS.** `shutil.copy2` is not: an
interrupted copy leaves a TRUNCATED file, and a truncated *backup* later restored
over a good target is worse than no backup at all. Both directions write a temp
name in the destination's own directory and `os.replace` it, so a copy that
EXISTS is complete. That is also the second reason the copy is a sibling --
`os.replace` is atomic within one filesystem and is not across them.

**THE COPY'S NAME DOES NOT END IN `.py`, AND THAT IS LOAD-BEARING.** It sits
inside the package, which `tests/test_package_invariants.py` walks for `*.py` --
section 1c re-parses every one, section 5 scans them for re-exports, check 2h
reads them for name usage. A `config_pristine.py` beside `config.py` would join
that corpus as a second module declaring every constant in it.

**A COPY THAT CANNOT BE TAKEN IS A REFUSAL, EXIT 5**, on `empty_database
(db_path, flag)`'s footing: planting into source with nothing to put it back
from is the state this exists to remove. `143` is SIGTERM's, the shell's own
encoding and the batch runner's.

**TWO OBVIOUS ARGUMENTS FOR `_terminate_child` WERE DRIVEN AND BOTH ARE FALSE,
and they are recorded because a check that cannot discriminate passes and
therefore looks like it is working.** "Without it the runner restores while a
live writer is still planting" -- FALSE: `subprocess.run`'s own bare `except:`
already calls `process.kill()`, so a revert to it produced no orphan and the
check written for one reported 182/0. "SIGTERM first lets the child's `finally`
run" -- ALSO FALSE for these five children: CPython does not convert SIGTERM
into an exception, so a plain Python script's `finally` does not run for it any
more than for SIGKILL, measured with a marker-writing stub. **What survives is
smaller and is what is checked**: the wait is BOUNDED (`subprocess.run`'s
`kill()` then `wait()` is not), the signal is CATCHABLE first, and the child is
not left running. THE PRISTINE COPY IS THE MECHANISM; that function is hygiene.

**`WRITER_OWNED_FILES` IS DECLARED AND HALF-CHECKED, AND THE OTHER HALF IS
NAMED RATHER THAN FAKED.** `tests/test_serial_runner_lock.py` section 1 checks
each PAIR against the named writer's own source -- it exists, it is in
`SERIAL_TESTS`, it names the target as a string constant, it really writes -- so
an entry pointing at a moved file or a test that stopped rewriting it FAILS. It
CANNOT see a brand-new third writer, and that limit is MEASURED: a cheap scan
for "names a path under `oncotriage/` AND calls a write" reports ELEVEN files in
this suite, NINE of which write only into a temp directory. An
over-approximation that flags nine innocents is not a check.

**2. THE ABLATION STUDY PRINTS THE REGISTRY'S TWO BLOCKS.** It drives the same
six-stage graph, the same Stage 5 and the same writer as a batch run, so it
moves every counter in `oncotriage/degradation.py`'s registry and census -- and
reported NONE of them. A study that dropped a retrieval channel on every patient
ended with a summary table and nothing saying so. `print_study_close` now prints
the census and then the degradation block, above this module's own three
readers, with the `Status:` line last: severity ascending, verdict last, which
is `oncotriage/batch/runner.py`'s ordering adopted rather than invented. **ONE
SNAPSHOT PER BLOCK, TAKEN AT THE CONSUMER**: the batch runner takes its two in
`main()` because it has three consumers of the degradation one and they must
describe one instant; this study has exactly one of each, so the snapshot is
taken there -- same guarantee, no parameter to forget -- and both are taken
BEFORE the first `emit`, because emitting a line can itself move `EMIT_FAILURES`.

**3. `tests/test_storage_write_durability.py` IS BUCKET A, AND ITS 9c COULD NOT
FAIL.** See the run-block entry above for both. The one-line summary: a hundred
checks needing nothing at all were out of CI to preserve a single production-
database probe, which is gated now; and the check that says the production
database was not written to was comparing two readings taken microseconds apart
at the END of the run, so a driver that wrote a hundred rows into it would have
been reported as a run that touched nothing.

**4. `run_with_write_retry` COUNTS WHAT IT DID.** It is the helper every write
outside `log_inference` retries through -- today the ablation study's three --
and it incremented nothing: a console line, a log record, and no total. A study
that retried four hundred times to lose nothing and one that met no contention
produced the identical run-end report, and those have opposite implications for
what the next increment of load costs. `WRITE_RETRY_OUTCOMES` is keyed
`{outcome}:{ExceptionType}` over a closed three: `retried:` per SLEEP,
`recovered:` per CALL that retried and then returned, `exhausted:` per call that
ran out of `SQLITE_WRITE_MAX_ATTEMPTS` while the error was still transient.
**`retried:` alone is not enough** -- it is equally consistent with a call that
retried and then gave up, which is why the outcome word is in the key.
**THERE IS DELIBERATELY NO `terminal:` KEY**: an error `_is_retryable` refuses
is not a retry outcome, nothing was retried, and the caller's own `except` is
what counts it with the caller's own meaning.

**WHAT WAS VERIFIED BY RUNNING.** CI bucket A **75 files, 0 failed, 0 not run**
-- and it now INCLUDES `test_storage_write_durability.py`;
`tests/test_package_invariants.py` **260/0/0**; `python fixture_replay.py`
**12/12 clean, exit 0, no recapture**; `tests/run_serial_tests.py` **5/5 in 386s**, with
both writer-owned files confirmed byte-identical afterwards, both pristine
copies observed PRESENT mid-run and ZERO left at the end; the production
`inferences.db` sha256 **unchanged** (`ab1403e3...`, 90,185,728 bytes) and
`ablation_results.db` unchanged (`f2bc23c6...`). **Twelve reverts on item 1, twelve
caught; three on item 3, three caught; one on item 4's arithmetic.** Counts
moved: `test_serial_runner_lock.py` 122 -> **204**,
`test_ablation_stop_and_lock.py` 143 -> **157**,
`test_ablation_write_durability.py` 33 -> **43**, and
`test_degradation_counter_readers.py` unchanged at **152**,
`test_storage_write_durability.py` 100 -> **111**.

**FIVE DEFECTS IN THIS PASS'S OWN WORK WERE FOUND BY RUNNING, NOT BY READING.**
(i) Two reverts ABORTED the new test file -- `str.index` on an announcement a
revert had removed, and `int(open(ready).read())` on a file a revert stopped
producing, both raising while `check`'s argument was being evaluated. **The
fourteenth time this project has shipped that shape.** (ii) A revert HUNG it:
the guard section gave the runner a stdout PIPE, and a child that outlives the
runner INHERITS it, so `communicate()` blocked on the ORPHAN rather than on the
runner -- two minutes of nothing, which reads exactly like an abort. Every
subprocess in that section writes to a FILE now. (iii) `_terminate_child`'s two
justifications, above. (iv) FOUR guards had NO control and were only found by reverting them: the
post-restore verification, the unreadable-copy refusal, the half-taken cleanup
and the keep-the-copy-on-restore-failed branch each reported a full green when
removed. The last of those needed a THIRD case to be measurable at all -- the
obvious one drives the failure with the directory read-only, where a buggy
`os.unlink(backup)` fails too and the copy survives for the wrong reason.
(v) `tests/test_ablation_stop_and_lock.py`'s 5j-5l were vacuous, above.

### The API bounds what a `docker stop` spends (the API-shutdown-gate pass)

**`oncotriage/api/server.py` WAS THE LAST STAGE 5 CALLER WITH NO SHUTDOWN
GATE.** `25- Batch Runner.py` and `26- Ablation Study.py` both ask Stage 5 to
stop issuing requests the moment a SIGTERM arrives; this service asked nothing,
so a `docker stop` could SIGKILL it with up to three further full-price rounds
still to be issued -- **2400 s of billable work against a 620 s grace period, in
either arm**. `docker-compose.yml` recorded it as a known, unfixed shortfall and
named the repair; this is that repair.

**NO BILLED CALL WAS MADE.** `python fixture_replay.py` is **12/12 clean, exit
0, with no recapture**, `tests/test_package_invariants.py` is unchanged at
**260/0/0**, and the production `inferences.db` (`ab1403e3…`, 90,185,728 bytes)
and `ablation_results.db` (`f2bc23c6…`) are byte-unchanged.

**THE BRIEF ASKED FOR A LIFESPAN SHUTDOWN HOOK AND A LIFESPAN HOOK ALONE BUYS
NOTHING. THAT IS A FACT ABOUT uvicorn AND IT WAS MEASURED RATHER THAN
REASONED.** Read `Server.shutdown()` in uvicorn 0.40:

```
for server in self.servers: server.close()          # stop accepting
for connection in ...: connection.shutdown()
await asyncio.wait_for(self._wait_tasks_to_complete(), ...)   # <-- the drain
if not self.force_exit:
    await self.lifespan.shutdown()                  # <-- only then
```

`_wait_tasks_to_complete()` spins while `server_state.tasks` is non-empty, and
an in-flight `POST /match` IS one of those tasks. **So the lifespan shutdown
event is delivered AFTER the drain it was meant to bound** -- and under
`force_exit` (a second Ctrl-C) it is never delivered at all.

**DRIVEN, BOTH ARMS, AGAINST A REAL uvicorn WITH A REAL SIGTERM AND A REQUEST
GENUINELY IN FLIGHT** (a stand-in graph, no billed call; the request polls the
flag and records when it flipped):

| arm | SIGTERM at | flag flipped | request ended | flag at end |
|---|---|---|---|---|
| **signal gate (shipped)** | 1.505 s | **1.517 s — mid-request** | 3.522 s | **true** |
| lifespan hook only (the brief's design) | 1.505 s | **never** | 16.44 s | **false** |

The second row is the whole argument: the in-flight request polled for twelve
seconds and never saw the flag, and the shutdown line printed *after* it
finished. **A lifespan-only implementation would have shipped a gate that
cannot gate anything, and every test written against it would have passed.**

**SO THE LOAD-BEARING HALF IS A SIGNAL HANDLER, CHAINED.** It is installed in
the lifespan STARTUP -- above the graph compile, so a SIGTERM arriving during a
slow bring-up is already gated -- and **chaining is mandatory rather than
polite**: uvicorn installs `Server.handle_exit` in `capture_signals()`, which
wraps `serve()`, so the lifespan startup runs inside it and
`signal.getsignal(SIGTERM)` is uvicorn's handler at the moment we look.
Replacing it without calling it would set the flag and leave the server running
forever -- trading a bounded drain for a container that has to be SIGKILLed.
`_chain_to` reproduces **every** disposition `getsignal` can return, and the two
that are not callables are the ones a naive `previous(signum, frame)` crashes
on: `SIG_IGN` is reproduced by doing nothing, and **`SIG_DFL` restores the
default and re-raises**, because doing nothing there converts a fatal signal
into a no-op. `None` -- a handler installed from C -- makes the gate **decline
to arm and count it**, rather than replace a working shutdown with one that
cannot chain.

**THE LIFESPAN HOOK IS KEPT AND IS NOT DECORATION.** It covers every shutdown
that arrives WITHOUT a signal: a host setting `Server.should_exit`,
`install_signal_handlers=False`, and -- the case that makes any of this drivable
in-process -- `starlette.testclient.TestClient`, which runs the application on
an anyio portal THREAD where `signal.signal` raises `ValueError` and no signal
gate can be installed at all. **Measured, not assumed:** the test asserts the
portal really is a non-main thread, so "and this is why the lifespan half
exists" is a measurement rather than a claim about somebody else's
implementation.

**WHICH HALF FIRED IS PRINTED RATHER THAN INFERRED.** The reason recorded with
the FIRST request wins, so a reason naming a signal says the handler ran and the
drain was bounded, and a reason naming the lifespan says it did not. An operator
reading a bill needs that difference and cannot recover it from anything else.

**IT IS SIGNAL-SAFE, and that decided the implementation rather than its
placement**, on `25- Batch Runner.py`'s footing: `request_stage5_shutdown`
assigns two module globals and takes no lock, and the announcement is
`os.write(2, ...)` rather than a `print` or a log call. **`os` is back in this
module for that one reader**, and the paragraph recording why pass 20f-1 removed
it is kept beside the one recording why it returned.

**THE FLAG IS CLEARED AT STARTUP**, on `oncotriage/batch/runner.py:main()`'s
footing. The cost of not clearing is sharper here than a wrong description: a
second application lifespan in one process would inherit the first one's
shutdown and **every request it served would fail with no call issued**.

**`SHUTDOWN_GATE_DEGRADATIONS` IS THE TWENTY-THIRD COUNTER AND IT IS EXEMPTED
RATHER THAN REGISTERED**, on `oncotriage/mcp/server.py`'s `TOOL_FAILURES`
precedent and for its reason -- a long-lived server has no run end for a run-end
report to attach to -- plus one this module adds: `oncotriage/degradation.py`
binds the counter OBJECTS of the modules it names, so registering this one would
put FastAPI, slowapi and pydantic into **every batch run's import graph**.
`shutdown_gate_report_lines()` is the reader and the STARTUP banner prints it,
so an operator learns at bring-up that the gate is not armed rather than from a
bill after a `docker stop`. **It always prints a line, even when armed**:
silence and "armed" would otherwise look identical.

**THE COMPOSE ARITHMETIC, BOTH NUMBERS.** `stop_grace_period` **did not move**,
and that is the point -- the gate brought the worst case down to the number 620
was always derived from:

```
BEFORE the gate   4 rounds      x 600 = 2400 s   (per-trial)
                  4 further chunks x 600 = 2400 s (retained grouped)
AFTER  the gate   1 in-flight round x 600 = 600 s, + 20 margin = 620 s
```

**CONCURRENT REQUESTS DO NOT MULTIPLY IT**, and that is the one API-specific
term the batch runner does not have: this service can hold several patients at
once, each on its own thread of the event loop's pool, but their in-flight
rounds run CONCURRENTLY, so the wall time is one round rather than one per
request.

**`tests/test_compose_shutdown_grace.py` SECTION 3 WAS INVERTED, 30 → 43.** It
asserted the shortfall as a live, unfixed fact; it now pins the **premise** --
by AST over `oncotriage/api/server.py`, that the gate is armed in the lifespan
STARTUP and asked for again at shutdown -- and keeps both ungated worst cases as
the record of what the gate removed. **A grace period whose sufficiency rests on
a mechanism nothing checks is a number nobody derived**, which is the retired
check 3c's mistake pointed the other way. Seven controls, including one that a
`lifespan` with no yield and one that unparseable source report both halves
missing rather than aborting.

**THE AVAILABILITY LOADER'S REQUIREMENT IS DERIVED NOW, AND THAT IS THE ACTUAL
FIX RATHER THAN ONE MORE COLUMN NAME.**
`oncotriage/dashboard/data.py:load_run_tracking_availability` hand-named ONE
column, `inferences.run_id`, because that was the only additive column the run
queries touched the day it was written. **`runs.resumed` and then
`runs.matching_call_mode` arrived afterwards, each declared on
`queries.run_summary` and `queries.campaign_summary`, and nothing there noticed
either time.** So an era-3 database -- both run tables, `inferences.run_id`
present, `runs.matching_call_mode` absent -- reported `present`, the tab went
down its normal path, `_load_run_query` caught the `MissingTableError`, called
`st.error`, handed back an empty frame, and the tab printed **"the run tables
are present and hold no rows" underneath the error saying they could not be
asked.**

It asks `queries.missing_requirements` for each of the tab's four query keys and
unions the answers. That helper is the ONE owner of "can this database answer
this query", it is what `report()` and `queries.run` already use, and it applies
the rule the loader would otherwise repeat -- **a column on an absent table is
reported ONCE, as the table**, because naming both tells an operator to add a
column to a table that is not there. The four keys are **named constants**
(`RUN_SUMMARY_QUERY` and its three siblings, closed as `RUN_TAB_QUERY_KEYS`)
read by both the loaders and the derivation, with a **`RuntimeError` at import**
on an unregistered key: two copies of that list is how a fifth run query joins
the tab without joining its availability check. Measured: era-4 `present`
missing `[]`; era-3 `partial` missing `['runs.matching_call_mode']`; era-2
`partial` missing `['runs.resumed']` -- **the era-2 case covered without
anybody having listed it, which is what "derived" buys.**

**`RUN_TRACKING_PARTIAL` MEANT SOMETHING FALSE AND THE TAB SAID IT OUT LOUD.**
Its docstring and the tab's warning both asserted flatly that the shape "was not
produced by the pipeline and a person should look at it" -- **a false accusation
against a perfectly ordinary database written before an additive column
existed.** PARTIAL now names TWO causes and the tab branches on which it is: an
**era gap** (every missing item is a COLUMN; `RUN_COLUMN_ADDITIONS` is additive,
so the next writer adds them and nothing is wrong with the rows already there)
and **a shape the pipeline cannot produce** (one run TABLE without the other).
A fifth vocabulary member was rejected: the state set is closed and consumed by
a tab that branches exhaustively, and this is a rendering decision inside an
existing state.

**FILE 18's `/health` CHECK: THE BRIEF'S PREMISE WAS WRONG AND THE REAL GAP IS
ONE FIELD OVER.** "Test 1 currently prints the JSON; a 503 passes silently" has
not been true since pass 20g -- `_json_or_report` returns `None` on any non-200
and the caller records a failure. What **could** pass silently is a **200 whose
body says `unhealthy`**: `/health` sets its code from `healthy` and then reports
that same verdict in the body, so the two disagreeing is a defect in the handler
rather than a fact about its dependencies, and the body was printed and never
read. Test 1 now records three distinct failures -- the status code (**named**,
so the summary distinguishes an endpoint that refused from one that answered
something unparseable), the body's `status`, and `pipeline_ready`, which a 200
requires and which was the field that reported true while the server was
unusable.

**`tests/test_api_shutdown_gate.py` -- 77 checks, bucket A, ~2 s**, verified
against ONLY the CI directory skeleton. **FIFTEEN REVERTS, FIFTEEN CAUGHT**,
each planted into a `copytree`'d copy with a realpath preflight asserting the
COPY is what imports, and **every one produced a summary rather than an abort**.

**THE REVERT HARNESS'S OWN PREFLIGHT CAUGHT THE FALSE GREEN BEFORE IT COULD
HAPPEN, TWICE.** `PYTHONPATH` alone does not win: the first attempt imported the
REAL tree because `python -c` puts the working directory at `sys.path[0]`, and
the second because setuptools' editable install installs a **MetaPathFinder**,
which takes precedence over `sys.path` entirely. A `sitecustomize.py` in the
copy strips it. Without the preflight every control would have reported MISSED
against checks that work -- pass 20f-1's lesson met again, one mechanism over.

**TWO DEFECTS IN THIS PASS'S OWN TEST CODE WERE FOUND BY RUNNING, NOT BY
READING.** Check 3b compared `result["error"] is None` on the success path;
**Stage 5 writes the EMPTY STRING there**, not None and not absent, so a working
node was reported as a failing one -- the failure was in the check. And the
first stub returned a FIXED response body naming a trial the chunk did not
carry, which the out-of-set detector reconciled into a completed patient
carrying an `error`: **a defect in the stub reported as a defect in the
pipeline.** The stub answers the trials it was asked about now.

**WHAT IS NOT DONE, NAMED RATHER THAN LEFT TO BE DISCOVERED.**

1. **AN IN-FLIGHT REQUEST STILL RETURNS HTTP 200** carrying a result whose
   `error` names the shutdown. That is the payload-level "fail honestly" the
   Stage 5 gate already gives; changing the STATUS CODE to 503 for that case is
   a contract change to a served response and belongs to a pass that can measure
   what a client does with it.
2. **A SIGNAL ARRIVING BEFORE THE LIFESPAN STARTUP IS NOT GATED.** uvicorn's own
   handler is installed before `serve()`, so the server still stops; the flag
   simply is not set. The window is the import plus the graph compile.
3. **`--workers N` WAS NOT DRIVEN.** Each worker is its own process with its own
   module state, so each arms its own gate, and the supervisor forwards the
   signal -- reasoned about, not measured.
4. **THE 20-SECOND MARGIN IS STILL UNCALIBRATED** and still labelled one. It is
   now the margin for a shutdown path that runs in the API as well as the batch
   runner, and neither has been timed under load.
5. **`GET /pipeline/info` DOES NOT REPORT THE GATE**, so an operator asking the
   API what it will do on a `docker stop` cannot see it over HTTP. Adding it is a
   contract change to a served response.
6. **A STARTUP THAT RAISES LEAVES THE HANDLER INSTALLED**, argued at the code:
   the statements after the `yield` do not run, and it is not wrapped in a
   `try`/`finally` because uvicorn's `capture_signals()` restores the
   pre-uvicorn disposition in ITS `finally` whatever happens here, and a failed
   startup produces a process that is about to exit. The reachable case is an
   embedding host that catches the error and carries on.
7. **`--workers N` WAS NOT DRIVEN** (see above), and neither was a real
   `docker stop` against the container -- the probe is a real uvicorn and a
   real SIGTERM in this process, which is the same mechanism one layer in.

**VERIFIED BY RUNNING.** `python fixture_replay.py` **12/12 clean, exit 0, no
recapture**; CI bucket A **79 files, 0 failed, 0 not run**;
`tests/test_package_invariants.py` **260/0/0**; `tests/run_serial_tests.py`
**5/5 in 404 s**, with `oncotriage/config.py` and
`oncotriage/registries/cancer_code_registry.py` confirmed byte-identical
afterwards; `tests/test_dashboard_run_health.py` **196**,
`tests/test_dashboard_app_integration.py` **110**,
`tests/test_storage_query_layer.py` **487**,
`tests/test_storage_schema_guards.py` **135** -- none moved;
`tests/test_degradation_counter_readers.py` **154** (was 152);
`tests/test_compose_shutdown_grace.py` **43** (was 30); and the production
`inferences.db` (`ab1403e3…`, 90,185,728 bytes) and `ablation_results.db`
(`f2bc23c6…`) byte-unchanged. **No money was spent and no migration was run.**

### Every billed path runs under the cap now (the spend-coverage pass)

**THE GATE COVERED ONE DOOR OF A BUILDING WITH FOUR, AND THE PASS THAT BUILT IT
SAID SO IN ITS OWN "WHAT IS NOT DONE" LIST.** `config.SPEND_CAP_USD`'s block
read "the gate instruments Stage 5, which is the batch runner's spend and
nothing else"; `oncotriage/spend.py`'s docstring said the rater and ragas "are
NOT instrumented". Both were true, and the consequence was that a program whose
operator-ruled shape is *campaign then judge* could spend the cap twice with
nothing anywhere able to notice. **NO BILLED CALL WAS MADE**: the production
`inferences.db` (`ab1403e3...`) and `ablation_results.db` (`f2bc23c6...`) are
byte-unchanged, the twelve fixture files are byte-unchanged, and
`python fixture_replay.py`'s differing-field set is **IDENTICAL to HEAD's --
69 fields, compared line for line against a `git worktree`** (the 0/12 is the
standing recapture item the de-identification and pre-diagnosis-ECOG passes
left; this pass adds nothing to it).

**THE MAP IS DERIVED, NOT DECLARED, AND THAT IS WHAT MAKES THE CLAIM
CHECKABLE.** `spend.BILLED_SITES` names every site in the repository that
touches a billed provider endpoint -- fifteen of them -- with one of three
dispositions and a written argument each, and
`tests/test_spend_coverage.py` section 1 walks every `.py` in the tree and
requires the derived set to equal the declared one EXACTLY, in both directions.

| disposition | sites | what it means |
|---|---|---|
| `gated_upstream` | Stage 5's two entry points and the two Bedrock adapters behind them | a NAMED caller gates it, and gating it twice would decline one request against one ledger for one reason. The gate stays in the dispatcher, or a fourth provider arrives ungated |
| `gated_here` | `agent/models.py::get_embedding`, `evaluation/rater.py::submit_batches`, `ragas_harness.py::build_judge`, `::build_embeddings` | the function itself calls `spend.require_budget` before the request. **These four are what this pass added** |
| `exempt` | the indexer's embed batch, the validator's diagnostic embedding, `bedrock_probe.py` (three sites) and the rater's free `count_tokens` | not gated, on purpose. Each argument is in the table AND is PRINTED by `report_lines()` on every run |

**THE SCAN IS ON ATTRIBUTE ACCESS AND NOT ON CALLS, and that is not
fastidiousness -- it is the only rule that can see the ragas harness.** That
module captures `real_create = client.messages.create` and calls it later
through the reference, so a call-shaped scan reports a file that spends real
money on two vendors as touching no billed endpoint at all. **That is measured
rather than hypothetical: it is what the first version of this derivation
reported.** You cannot bill without naming one of these attributes; you can
bill without a call node a scanner recognises. Both plants are driven -- an
ordinary ungated call site and a captured reference -- with the clean control
first.

**HOLE 3 WAS THE INTERESTING ONE, BECAUSE THE SHIPPED GATE WAS WRONG FOR A
SERVER IN BOTH DIRECTIONS AT ONCE.** `oncotriage/api/server.py` and
`mcp_server.py` charge the same ledger the batch runner does and write no
`runs` row, so nothing seeded that ledger and nothing reset it:

  * **unbounded before the cap** -- one process may serve for months, and until
    it has spent a whole campaign's budget by itself there is no brake at all;
  * **wrong refusals after it** -- the total only grows, so the request after
    the cap is declined and so is every request for the life of the process,
    for money a campaign somewhere else was budgeted. The remedy an operator
    reaches for is a restart, which empties the ledger and hands the process a
    fresh unbounded budget: **the brake off exactly when it was working.**

`spend.SPEND_POLICIES` is the fix: a closed two-member vocabulary, exactly one
in force per process. `campaign` compares a MONOTONE total against
`SPEND_CAP_USD`; `serving_window` compares a ROLLING window
(`SERVING_SPEND_WINDOW_SECONDS`, one hour) against `SERVING_SPEND_CAP_USD`
($25). It is bounded, it SELF-HEALS with no restart and no operator, and it
cannot be defeated by a restart loop -- restarting empties the window, which is
what waiting would have done anyway. **Driven both ways on one ledger**: under
`campaign` a process past its cap declines for ever; under `serving_window` the
same process recovers on its own.

**THE LATCH IS DERIVED FROM THE POLICY AND NOT PASSED, and that is the other
half of the same defect.** `spend.latch_on_limit()` is False under the window
policy, because a latch there would make the recovery unreachable -- `SPEND_STOP`
never un-trips. It is derived rather than parameterised because a parameter is
a thing a call site can get wrong and there are five of them across four
modules; a serving surface gets the right behaviour by installing its policy,
which is the one thing it must do anyway. **`evaluation._spend_gate` asks it
too**, so Stage 5 is correct inside a server as well as inside a batch run.
**The CEILING latches under both policies** and that asymmetry is deliberate: a
cap is a threshold a healthy run can cross, a ceiling is a defect report, and a
defect does not heal as a window rolls.

**WHAT AN OPERATOR AND A CLIENT SEE, DRIVEN THROUGH THE REAL ENDPOINTS RATHER
THAN READ.** `POST /match` answers **503** with a computed `Retry-After`, and
the gate is the FIRST statement of the shared helper -- above the validation and
above the parse -- so a declined request costs the server nothing at all,
measured by a pipeline stand-in that records zero invocations. **503 and not
429**, because 429 says the CLIENT sent too many (which is what `RATE_LIMIT`
already answers) while a budget is a server-side resource temporarily
exhausted for everyone. `Retry-After` is DERIVED from the events actually in
the window -- the instant the offending charge ages out -- so a server one
request over its budget says "come back in seconds" rather than "in an hour",
and it is OMITTED rather than faked when the condition will not clear. The MCP
server answers with a payload carrying **no `result` key** -- `_index_
unavailable_result`'s argument, verbatim: a model reading an empty result
beside a caveat summarises the caveat away.

**`/health` REPORTS THE BUDGET AND DELIBERATELY DOES NOT DECIDE `healthy`, and
the obvious version is actively harmful.** docker-compose probes it with
`curl -f`; an unhealthy container is RESTARTED; a restart empties the rolling
window. Folding a budget stop into `healthy` would make the health check the
mechanism that defeats the brake, on a loop, and the only symptom would be a
server that restarts every hour and never declines anything. Driven: 200 in
both arms, with `spend.declining` True in one and False in the other.

**HOLE 2 IS THE JUDGE, AND THE PRICING STAYS WITH THE PATH WHILE THE LIMIT
STAYS IN `spend.py`.** `SpendLedger.charge_usd(usd, source)` takes an
ALREADY-PRICED amount, because `config.PRICING_CONFIG` holds none of the
Anthropic Batches rates, none of the 50% batch discount and no cache-tier
multipliers -- so `rater.charge_batch_to_ledger` hands over what
`rater.price_usage` produced, and `UsageTally._charge` hands over what
`judge_pricing`/`embedding_pricing` produced. That split is what lets one cap
govern four paths priced four ways.

**THE DISJOINT COUNTS ARE PINNED SO NOBODY SUMS THEM.** Anthropic reports
`input_tokens` as the NON-CACHED input only, with the cache read and the two
cache-creation figures beside it -- the shape
`bedrock_anthropic_adapter.py` had to sum BACK for Converse, because OpenAI's
`prompt_tokens` INCLUDES its cached portion. Here they must NOT be summed: each
tier has its own rate and a cache read costs a tenth of an uncached token. The
arithmetic is pinned term by term, with the wrong version -- what a reader who
knew only the OpenAI shape would write -- shown to give a different number.

**THE RATER IS GATED PER CHUNK AND EXITS 3.** `submit_batches` asks the gate
immediately before each `batches.create`, so the overshoot is ONE BATCH rather
than all of them, and a retry pass submitted after the primary batches have
been collected and charged is declined on a ledger that knows what they cost.
The overshoot bound is stated rather than glossed: the Batches API puts the
gate and the charge further apart than Stage 5 does, and the smallest unit this
gate can decline is a whole chunk. `SpendLimitReached` is deliberately NOT a
`RaterRefusal` -- a refusal means "the configuration is wrong, fix it", this
means "the money is gone and everything already submitted is still
retrievable" -- so it exits **3**, distinct from 1 and 2. A session seeds from
`rater_state.json`'s running total, so `--resume` continues under the REMAINDER.

**RAGAS IS GATED AT THE ONE POINT IT CAN BE, AND THE LIMIT IS NAMED.** The gate
sits above the `await` in the `recording_create` closure each builder installs,
so a raise means NO REQUEST IS ISSUED -- which is verified. What is NOT verified
is what ragas does with the exception, because this harness is not exercised
here; both outcomes are SAFE, since every later ask meets the same gate.

**HOLE 1 IS THE ABLATION STUDY, AND IT IS THE BATCH RUNNER'S FIVE CONTROLS
ADAPTED RATHER THAN COPIED.** The ledger and the latch join the per-run module
state `main()` clears; the ledger is SEEDED from `ablation_spend_before(db)` --
this database's own rows, which is the study's campaign, because pass 20f-3
made the checkpoint follow `--db` so "what this database holds" and "what a
resume will skip" are the same set by construction; `spend.SPEND_STOP.poll` is
read at all three sites the operator switch is read at; `_run_pair_unless_
stopped` reads the latch as a plain attribute, closing the sweep's one-pair
edge; and `ablation_runs.stop_reason` records WHY.

**A COLUMN AND NOT TWO MORE STATUSES**, which is
`database_logger.RUN_STOP_REASONS`' ruling adopted rather than re-argued: a
spend stop's answer to "how did this end" is byte-identical to an operator
stop's, and two more members would be two more things `RUN_STATUSES_PARTIAL`,
`_summary_status_warning` and the stop-and-lock test must learn, all answering
identically for all three. `_stop_reason_now()` is the ONE derivation and the
OPERATOR OUTRANKS THE BUDGET, because reporting a budget when a person had
already asked for the stop sends that person to `SPEND_CAP_USD` to explain a
stop they caused. **A COVERED configuration stores no reason even when a latch
is set** -- a stop that arrived while every pair was in flight cut nothing
short -- and the CRASH path stores none either, because KILLED means the process
did not get to the end and attributing that to a budget would be false.

**DRIVEN, NOT READ: the real `main()`, to its cap and back.** With the sample
larger than `MAX_WORKERS` (so there IS queued work when the latch trips) the
study stops, records `('full_pipeline', 'STOPPED', 'spend_cap')`, opens no row
for the configuration it never started, and a resume under a raised cap runs
**exactly** the pairs the stopped run did not -- 40 of 40, no pair twice, no
duplicate row. A cap crossed by the LAST pair leaves the configuration
**COMPLETE with a NULL reason**, which is the batch runner's scenario C applied
here.

**SIXTEEN REVERTS, SIXTEEN CAUGHT**, each into a `copytree`'d copy with a
`sitecustomize` that strips the editable install's MetaPathFinder (which
otherwise beats `PYTHONPATH`), a realpath preflight asserting the COPY is what
imports, and `PYTHONDONTWRITEBYTECODE=1`. **TWO WERE MISSED ON THE FIRST RUN AND
BOTH WERE REAL GAPS IN THE CHECKS RATHER THAN WEAK REVERTS**, which is the whole
argument for running the matrix:

  * **the study's ledger seed could be deleted with every check still green.**
    The seed was exercised by CALLING `ablation_spend_before` directly, which
    proves the reader works and not that `main()` consults it -- and that is
    exactly the gap that would let a resumed study get a fresh budget every
    time. It is measured through its EFFECT now: a database already holding
    more than the cap must make the next study bill NOTHING, with the
    no-history clean control beside it.
  * **the per-pair guard likewise**, because the scenario's queued futures were
    cancelled by the sweep and the edge it closes never occurred. It is driven
    directly now.

**FOUR DEFECTS IN THIS PASS'S OWN WORK WERE FOUND BY RUNNING, NOT BY READING.**

  * **`UsageTally._charge` subscripted `rates["output"]`, and
    `embedding_pricing` returns no such key** -- an embedding produces no
    completion. The `KeyError` was swallowed by that method's own handler into
    a counted fault, so **every ragas embedding charge was silently dropped
    while the run reported a pricing fault a reader would have blamed on the
    price table**. Caught by check 7a, which requires the harness's own cost
    figure and the shared ledger to agree to the cent.
  * **`/health` read `config` where the module binds `_config`** -- a
    `NameError` that turned the endpoint an operator asks FIRST into a 500.
    Caught by driving it rather than by reading it.
  * **check 8k-ii shipped as `len({one item}) == 1`**, a tautology, found by
    re-reading the file after it was green.
  * **the closing-block probe passed `list.append` as the sink**, which
    `print_study_close`'s bare `emit()` refuses -- the exact vacuity
    `tests/test_ablation_stop_and_lock.py` had to fix in its own 5j-5l, met
    again. It is a function with a default now, with a non-degeneracy check
    that the sink collects anything at all.

**AND THE EXISTING SUITE CAUGHT THREE THINGS, every one of them the check
working.** `tests/test_spend_gate.py` 1j (SEED_SOURCES gained a third member --
the pin stays EXACT); `tests/test_clinical_use_framing.py` 5d (the MCP server
gained a SIXTH result payload and it carries the framing -- the pin stays EXACT,
because only an exact count fails when a seventh is added without it); and
`tests/test_ablation_stop_and_lock.py` 4g, whose migration fixture **dropped
`status` alone and so described a database carrying `stop_reason` and NOT
`status` -- a shape no era of this schema has ever had.** The fixture drops both
now, and 4g-i..4g-iii check the era that DOES exist on disk: `status` present,
`stop_reason` absent, which is every ablation database written between the
operator-control pass and this one.

**WHAT WAS VERIFIED BY RUNNING.** `tests/test_spend_coverage.py` **161/0**;
CI bucket A **89 files, 0 failed, 0 not run**;
`tests/test_package_invariants.py` **260/0/0**; `tests/run_serial_tests.py`
**5/5 in 404 s**, with `oncotriage/config.py` confirmed to carry only this
pass's own edit and `oncotriage/registries/cancer_code_registry.py` confirmed
byte-unchanged; `python fixture_replay.py`'s differing-field set identical to
HEAD's; and both production databases and all twelve fixture files
byte-unchanged. **No money was spent and no migration was run against a
production database**: `ablation_runs.stop_reason` appears on the next study
that opens the file, which is what the additive mechanism is for.

**WHAT IS NOT DONE, NAMED RATHER THAN LEFT TO BE DISCOVERED.**

1. **THE CAP DOES NOT NET ACROSS PROCESSES.** A campaign seeds from the `runs`
   chain and a rater session from its own state file, so each resumes under its
   remainder -- but the judge's spend is not counted against the campaign's,
   because no shared store exists that both write. Closing it means a
   cross-process ledger, which is a persistence design of its own.
2. **RAGAS HAS NO SEED.** `--resume` there re-scores from a partial journal
   which records SCORES rather than spend, so the cap binds within one ragas
   invocation and not across a resumed pair of them. Inventing a total from the
   journal's row count would be an estimate deciding a budget.
3. **WHAT RAGAS DOES WITH A REFUSAL IS NOT VERIFIED** (above). Only that no
   request is issued.
4. **`SERVING_SPEND_CAP_USD = 25.00` IS A RULING, NOT A MEASUREMENT**, and is
   labelled one. It admits ~60 patients/hour at the cache-absent price and ~139
   with the cache working; nobody has measured this project's real serving
   load, and the banner prints the number on every start so it cannot be
   inherited silently.
5. **THE INDEX BUILD HAS NO BRAKE OF ITS OWN.** It is exempt with an argument,
   and the brake it actually needs is a corpus-size refusal rather than a
   dollar cap.
6. **`/pipeline/info` DOES NOT REPORT THE POLICY OR THE WINDOW**, so an
   operator asking the API what it will refuse cannot see it there. `/health`
   carries it; widening `/pipeline/info` is a second contract change.
7. **THE RATER'S EXIT 3 HAS NO CALLER TODAY.** It is a contract change stated
   as one, on File 19's precedent.


## Persistence and observability

**`oncotriage/storage/database_logger.py`** (its shim, `14- Database Logger.py`, was deleted in pass 20e) opens no database at load time and never did since item 20b, which turned schema creation into a function because nine other files load 14 or are loaded beside it and every one of them was touching `inferences.db` just by being read. `initialize_database(db_path)` creates three tables: `inferences` (per-patient funnel counts, per-stage timings, token counts, cost), `trial_matches` (per-trial verdicts), `drift_metrics`. It is idempotent — every `CREATE` is `IF NOT EXISTS`, every `ALTER` is guarded by a `PRAGMA table_info` check — and `log_inference` ensures the schema once per resolved path before its first write. `16-` is a scratch query script; `15-` wipes all tables and is guarded by `Flag = False` — leave it False.

**`log_inference(result, patient_data, db_path=None)` takes the database as an argument, and the five isolation tests pass it.** `db_path=None` means `oncotriage.paths.inferences_path` — `resolve_inference_db_path()` is the resolver, and it deliberately does *not* consult the exec namespace. The **shim's** `log_inference` is a wrapper that supplies `globals().get("inferences_path")`, the same late-binding seam File 02 uses for `get_model_cost` / `resolve_qdrant_collection` / `get_age_reference_date`. That seam is what keeps the redirect working for Files 36, 37, 38, 40 and 45, all of which rebind `inferences_path` at a temporary database *before* loading File 14: a module function cannot see a caller's globals, so without the wrapper all five would have written real rows into the real `inferences.db` while still printing the name of the temporary file each thought it was using. All five now **also** pass `db_path` explicitly and assert on the path `log_inference` returns, so neither mechanism is a single point of failure. Each one first checks that `resolve_inference_db_path(None)` is the production database and is *not* its own scratch path, which is what makes the assertion discriminating rather than vacuous. **No writer anywhere in the repository depends on rebinding a shared global any more (pass 20c-3b).** File 41 was the last one — it rebound `inferences_path` for `log_drift_metrics` in File 20. `log_drift_metrics`, `get_baseline_and_current_data` and `run_drift_detection` all take `db_path` now, `log_drift_metrics` **returns the path it wrote to** so a caller can assert on it, and File 41 passes its scratch path, checks the default resolves to production and is *not* that scratch path, demonstrates the assertion failing against a decoy database, and confirms the production `drift_metrics` row count is unchanged at the end.

`_resolve_primary_cancer` lives in **`oncotriage/registries/primary_cancer.py`** as of pass 20c-2c, and both the agent's three terminal nodes and the storage logger import it from there. Pass 2b had already stopped it reading File 13's `_CANCER_REGISTRY` global — a layering violation that left it raising `NameError` in any chain loading 14 without 13 — in favour of `load_registry()`. Pass 2c finished the job: while the function lived in the storage module, the *agent* depended on the *storage* layer for a registry lookup. **`tests/test_fhir_birth_date_and_demographics.py` section 9b is the only place that exercises it**, because it is the only place in the repository that reaches the storage layer without the agent — an exec chain that loaded 14 without 13 before pass 20d-1, an import of `oncotriage.storage.database_logger` without `oncotriage.agent` after it; it calls the function directly on a stub condition list and asserts a real diagnosis comes back, having first asserted the result is not `None` (which an empty registry filter also returns).

`oncotriage/dashboard/` (thin entry point: `21- Streamlit Dashboard.py`) reads only from `inferences.db`, via the three `@st.cache_data(ttl=60)` loaders in `dashboard/data.py`. See "The dashboard (pass 20c-3c-1)" above.

**Cost accounting fails loudly.** Costs come from `get_model_cost()` (`oncotriage/utils.py`) against `PRICING_CONFIG` in `oncotriage/config.py`, dated `last_updated`. A model absent from that table raises `UnknownModelPricingError` (a `RuntimeError` subclass — deliberately *not* a `KeyError`, so a stray `except KeyError` cannot eat it); it does not return 0.0, because a zero cost row is indistinguishable from a genuinely free run and every aggregate over the column silently under-reports. Both writers — `log_inference` (14) and `log_ablation_result` (26) — call it **before** their `try` block for exactly this reason: their broad `except` exists to keep a database failure from killing the pipeline, and an unpriced model is a config defect that must reach the caller instead. If you add a model, add its pricing first; never wrap `get_model_cost()` in a recovery path.

**The Stage 5 packing and cache record — four columns, and NULL is never 0.**
`llm_classifier_cached_input_tokens`, `llm_classifier_call_details`,
`llm_classifier_packed_chunks` and `llm_classifier_packing` are written by
Stage 5, carried by `_pipeline_provenance()` and stored through
`INFERENCE_COLUMN_ADDITIONS`. They were measured and then **dropped at the
write** for as long as the table declared no column for them — the writer
stores the columns it declares and ignores the rest, silently, which is the
same class of silent drop `TrialMatchState` has for undeclared channels.

| column | NULL means | 0 / `[]` / `{}` means |
|---|---|---|
| `llm_classifier_cached_input_tokens` | no response of this run reported `prompt_tokens_details.cached_tokens` — a stub, a pre-field recording, a run that never reached Stage 5, or a run that ended at a failure return | a response DID report the field and reported zero: the provider cached nothing. That is the reading that says a prefix is not being reused |
| `llm_classifier_call_details` | `node_llm_classifier_evaluation` was never entered. Stronger than the other three: Stage 5 writes this key on **every** return, failures included | `'[]'` — the node ran and no call produced a usage object (the first request raised). **An empty list is not a NULL**, which is why the INSERT tests `is not None` rather than truthiness |
| `llm_classifier_packed_chunks` | the packer's record does not describe this run. Written on the **success return only**, on `hallucinated_trials`' convention: a chunk list is a plan, and a run that died at its first call must not publish the whole plan as though it had been sent | the packer ran and produced no chunk — an empty candidate set, not an absent packer |
| `llm_classifier_packing` | as above | `'{}'` — a packer that reported nothing, which is not an absent packer |

**`llm_classifier_cached_input_tokens` IS A SUBSET OF
`llm_classifier_input_tokens` AND NEVER A COSTING TERM**, exactly as
`llm_classifier_reasoning_tokens` is a subset of the output figure. Cached
input bills lower and that discount is deliberately **not** modelled by
`get_model_cost()`, so stored costs stay comparable with every historical row.
Subtracting it, or pricing it separately, re-bases the whole series.

**`llm_classifier_call_details` is the only column that can answer whether the
cache warms.** A summed figure of 5,000 cached tokens across three calls is
equally consistent with a cache that warms after the first request and one that
never warms, and those have opposite implications for what packing costs. The
ledger is one JSON object per request ISSUED, in order, carrying `call_index`
(1-based), `depth`, `trials`, `prompt_tokens`, `completion_tokens`,
`cached_tokens`, `reasoning_tokens`, `finish_reason` and `entries_emitted`.
`trial_matches.call_index` joins to it by equality.

**Stage 5's failure returns carry the tokens they were billed, and the figure is
a FLOOR.** All four early returns used to end the node with no token figure, so
`_pipeline_provenance()`'s `state.get(..., 0)` supplied a zero and the row said
0 input and 0 output tokens against requests that had been issued and billed —
six such rows are in the production database, each with
`llm_classifier_retries = 3` beside two zeros. A refusal, a JSON parse failure
and a non-list body now carry the accumulators, which are exact for that
invocation because usage is read *before* the fence strip and the parse; so does
an API error on a **later** chunk of a packed batch, where the earlier chunks'
tokens are known. An API error on the **first** call carries nothing: no usage
object was obtained, and an estimate from prompt length would put a number no
provider reported into a measurement column. `llm_classifier_calls` is written
on every return for the same reason, and it is what separates the two — `calls =
0` with `llm_classifier_prompt_sha256 IS NOT NULL` is "Stage 5 ran and counted
no usage", while a NULL hash is "Stage 5 never ran", where 0 is a measurement.

**WHAT THE FLOOR STILL EXCLUDES, and neither term is recoverable from a stored
row.** The accumulators are local to one invocation of the node and start at
zero each time, while a parse failure routes the graph back *into* it — so a run
with retries reports its LAST attempt only, and that is true of the success
return as well. And the OpenAI SDK's own transport-layer retries are invisible
to this process at every return. `run_harness.price_result` already states the
first in its docstring and reports the consequence through `cost_complete`;
nothing reports the second, because nothing can. **A consequence worth stating
plainly: `estimated_cost_usd` on a failed row is no longer 0.00. The pricing
CALCULATION did not change — its input stopped being a false zero.**

**Two files cover this and neither replaces the other.**
`tests/test_storage_inference_logging_contract.py` Test 2 is the STRUCTURAL
half: by AST, every one of the node's own dict returns reports its billed tokens
literally or through the `**_billed_so_far()` spread, with the walk scoped to
stop at nested definitions and a probe asserting that scoping is doing work.
`tests/test_storage_packing_and_cache_columns.py` is the BEHAVIOURAL half: the
real migration, the real `log_inference` and the real node, read back out of
SQLite. An AST scan cannot see a value that is carried and then serialized
wrongly; a round trip cannot see a return that was never written.

**Degradation record.** A run that lost a retrieval channel, fell back to the un-expanded query, or skipped the cancer site filter must be identifiable from its stored row alone. The relevant state keys are written by the stage that owns them, carried to all three terminal nodes by `_pipeline_provenance()` (file 13), and logged to `inferences.retrieval_channels` / `retrieval_degraded` / `retrieval_trials_lost` / `query_expansion_path` / `mesh_filter_applied` / `mesh_filter_skip_reason`. **NULL in these columns means the stage never reported and is not the same as a clean value** — never default them to 0 in a new writer or fold NULL into 0 in a reader. Stage 5's Section 2 is conditional on `mesh_filter_applied`: it only asserts to the model that disease relevance was confirmed when the filter actually ran.

**Age reference date.** Patient age is computed against `DATA_SNAPSHOT_DATE` (`oncotriage/config.py`), never `datetime.now()`, and so is the Stage 5 prompt's RULE 4 "Reference date" — a clock-derived age changes the prompt while `compute_patient_hash` (which keys on `birth_date`) cannot see it. `parse_partial_date()` / `get_age_reference_date()` live in `oncotriage/utils.py`; `get_age_reference_date()` resolves the constant through `oncotriage.config` and **raises** rather than falling back to `today()`, and `tests/test_config_snapshot_date_rot.py` rewrites that literal in `oncotriage/config.py` — not in File 03, which only re-exports it; `birthDate` may legally be `YYYY`, `YYYY-MM`, `YYYY-MM-DD` or a full ISO datetime, and missing components are filled from a mid-range anchor with the shape recorded as `inferences.birth_date_precision` (same NULL semantics as above). Race and ethnicity are read from the US Core extensions **by sub-extension url** (`ombCategory` → `detailed` → `text`), never by array position. `Exception and Fallback Audit.md` inventories every `except` and fallback in the codebase with a verdict and the open items.

## Degraded dependencies (item 11a)

**Two detection layers could DISAPPEAR without anything failing, and both now
raise.** Item 11b was about a stage that failed and left no trace; this is about
a layer that was never there and left no trace, which is worse because nothing
failed at all — the pipeline ran, produced matches, and every downstream number
was computed as though the missing layer had agreed.

| Layer | Was | Is |
|---|---|---|
| `registries/mesh.py:load_mesh_filter()` | printed a warning, returned `None`; Stage 4's cancer site filter never ran and **every trial passed it for the whole run** | raises `settings.DegradedDependencyError` naming both missing JSON files and `python "09- MeSH Cancer Site Relevance Filter.py"` |
| `registries/cancer_code_registry.py:_build_icd10_cancer_sets()` | caught `ImportError`, `logger.error`, returned **three empty sets** while `CancerCodeRegistry` logged "ready" | raises, naming the package and `pip install icd10-cm` |

**`None` IS STILL A REACHABLE STATE and every branch handling it is real and
tested.** Stage 4 records four distinct skip reasons, and `37- Retrieval
Observability Test.py` installs `deps.set_override(deps.MESH_FILTER, None)` to
exercise one. What changed is that the state can no longer be CREATED SILENTLY
from missing files: it arrives from an override, or from an operator who set the
variable below. `deps.get_mesh_filter()`'s docstring says so.

**`ONCOTRIAGE_ALLOW_DEGRADED_REGISTRIES` is the one opt-out**, named in
`oncotriage/settings.py` beside the other `ONCOTRIAGE_*` names and **deliberately
not routed through `_from_env`** — that helper appends a trailing separator, so
`=1` would come back `"1/"` and the flag could never match again. Same reasoning
as `resolve_airflow_password` and `resolve_inferences_db`, third victim. Set, the
run continues, logs at WARNING naming exactly which layer is absent and which
variable permitted it, and records it in a module-level counter
(`mesh.MESH_FILTER_DEGRADATIONS`, `cancer_code_registry.REGISTRY_DEGRADATIONS`);
per inference the MeSH case is already recorded by item 11b in
`mesh_filter_skip_reason`, so there is no new column and no new field. An
unrecognised value **raises** rather than being read as "off": a switch that
decides whether a missing layer may be tolerated must not itself be tolerant of
a value nobody meant.

**THE OPT-OUT DOES NOT REACH THE DELETION PATH, and that exemption is the point.**
`oncotriage/fhir/clean.py:require_intact_registry()` runs before anything is
scanned and refuses a degraded registry **whatever the variable says**, because
`filter_cancer_patients_inplace()` unlinks patient bundles on
`is_primary_cancer()` verdicts. A degraded registry there means a missing pip
package deletes the dataset, and it is wrong in BOTH directions at once:
ICD-10-coded cancers stop being recognised and are deleted as non-cancer, and
the D00-D49 / C77-C79 exclusion sets stop rejecting, so in-situ and
metastatic-only records can be admitted by the display-term fallback. It also
refuses an object that does not report `degraded_layers` at all — "cannot tell"
is not "is fine", and only one of the two may proceed to delete. Demonstrated
with the variable SET (File 48 section 5).

**`filter_cancer_patients_inplace(dry_run=True)`** scans and plans exactly as
usual, writes the full list to `{manifest_path()}.dryrun` and unlinks nothing.
A **PARAMETER, not a new exported helper**: File 47 section 5 pins the File 05
shim's surface at fourteen names, and a plan produced by a second implementation
is a plan that can disagree with the deletion. One `if` around the unlink;
everything else — the plan, the manifest shape, the flush interval, the phase
status — is the same code. Reachable as `python "05- FHIR Clean Data.py" --dry-run`
(argparse inside the `__main__` block, so no name leaks into the exec namespace).

**THREE PATHS COUNT RATHER THAN RAISE, and the line is not arbitrary.** A missing
file or package is a CONFIGURATION defect: one command fixes it and every run
afterwards is correct, so raising costs one run and fixes the class. An
unparseable trial age bound or an unrecognised lab unit is third-party DATA —
there is no operator action that would fix ClinicalTrials.gov — and raising
would turn a per-trial degradation into a per-patient outage.

- `agent/filtering.py:AGE_PARSE_FAILURES` — the `Exception and Fallback Audit.md`
  row ranked **Open, highest priority**. Recovery unchanged (the trial is kept,
  the age check is skipped for it), now recorded per bound with the exception
  type and the text, and printed in the Stage 4 line as `age UNPARSED N`.
- ~~`retrieval/indexer.py:INDEX_AGE_PARSE_FAILURES`~~ — **DELETED by the
  scrape-admission pass, with the decision it recorded.** It counted failures to
  evaluate `if min_age > 18: continue`, which was not an adult filter but an
  **exactly-18** filter: a trial whose `minimumAge` was 19, 20 or 21 was
  discarded, so a 70-year-old who qualifies for a trial requiring 21 could never
  be matched to it because the trial was never in the corpus. The skip was
  deleted rather than widened — Stage 4 already enforces
  `min_age <= patient_age <= max_age` against the actual patient — and with no
  age decision at scrape time the counter could only ever read zero, which is
  the dead-declaration shape check 2h exists to report.
  **Stage 4's `AGE_PARSE_FAILURES` is now the only age-parse record in the
  project**, which is correct: it is the only place an age bound is still
  parsed. `tests/test_degraded_dependencies.py` asserts the ABSENCE of both the
  counter and any age comparison in the scraper's executable code — a stronger
  check than the one it replaced, because it fires if anyone reintroduces an
  index-time age decision.
- `agent/patient.py:LAB_UNIT_DEGRADATIONS` — `_normalize_lab_unit` has **three**
  silent exits, not one, and counting them together would bury the one that
  matters: `no_value_or_unit` (expected and harmless), `conversion_error` (the
  exception exit, keyed with the exception type), and `unconverted` — every rule
  consulted, none matched — which is the real gap. The `unconverted` keys name
  the lab AND the unit, because the fix is a new row in `_LAB_UNIT_CONVERSIONS`.

All four counters are **module-level**, following `PARTIAL_DATE_DEGRADATIONS` in
`oncotriage/utils.py`, and **none is a new key in the Stage 4 result dict**: the
twelve characterization fixtures diff that dict field by field, and a new field
means recapturing all twelve at GPT-4o prices for something no stage reads.
File 48 pins the dict's exact key set.

**HISTOLOGY IS COMPUTED UNCONDITIONALLY, and that is a real behaviour change on
the degraded path.** `extract_patient_histology(conditions)` sat INSIDE
`if mesh_filter is not None:`, so a missing MeSH lookup file disabled the
histology filter — a filter that reads no MeSH data and resolves no tree numbers
— and `histology_dropped` came back 0, which is also what "checked, nothing to
drop" looks like. With no MeSH filter, trials whose histology contradicts the
patient's are now DROPPED instead of reaching GPT-4o. On the normal path nothing
changes at all, which is why all twelve fixtures (every one captured with a
filter loaded) replay clean without recapture.

**Two guards were added that the exception audit could not have asked for**,
because its AST sweep only sees `except` clauses:

- the ICD-10 build raises when the package **imports and yields no primary
  codes**. A release whose chapter-2 categories moved produces exactly the three
  empty sets the missing-package path produced, with the import succeeding.
  Its first form was `if not primary:` and **could never fire** —
  `_ICD10_SEED_PRIMARY` seeds `C97` unconditionally, so `primary` is `{"C97"}`
  even when the release contributed nothing. It asks `derived_primary_count`,
  captured before the seed. **The negative control in File 48 found that, not
  reading**, which is the argument for controls restated as an event.
- `require_intact_registry()` refuses a registry that cannot report its own
  state, as above.

`tests/test_degraded_dependencies.py` is the demonstration: 170 assertions, every
raise shown to fire with the thing absent **and** shown not to fire with it
present, the dry run shown against a copy of a real cohort with a real run on an
identical second copy as the control, and the pre-11a histology shape
reconstructed in an AST copy so the structural check has something to fail on.

### The hardcoding audit record (2026-08-25)

**THIS SECTION EXISTS SO THE NEXT SWEEP BUILDS ON A RECORD RATHER THAN ON
COMMIT ARCHAEOLOGY.** Two audits of hardcoded values have now run against this
tree. Neither left a durable note, so the second one re-derived the first one's
boundary from `git log` — which is exactly the cost this section removes.

**A NAMING COLLISION, DISAMBIGUATED FIRST.** "The promotion pass" already names
something else in this document — the Stage 4 histology/age/sex fixes and the
two test files that committed their proof. The work below is **the August
hardcoding-promotion pass**, and the two are unrelated.

**WHAT THE FIRST AUDIT REMEDIATED — SEVEN COMMITS, 2026-08-20 to 2026-08-21.**
Every one is the same shape: a literal with more than one site, or a literal
whose owner was the wrong module, promoted to ONE owner in
`oncotriage/config.py` and given a reader.

| commit | what it promoted |
|---|---|
| `09436e0` | the RRF fusion constants — `RRF_K` and the four channel weights — to config ownership, the tracking enumeration and the fixture tunables record; **the two-stage equality became structural rather than asserted in a comment** |
| `cf0a5b3` | the collection alias and its staging-family prefix under `config.COLLECTION_NAME` across the indexer and the generated DAG; fixed `cleanup_old_collections`' cross-family deletion |
| `43c25d4` | the embedding-batch throughput knobs; unified `CHARS_PER_TOKEN` and the derived method strings with their owner; corrected an 8x-stale batch comment |
| `875b441` | one config owner for the API port across the server and both harnesses; derived the harness POST budget from the server's own worst-case arithmetic |
| `d421f22` | `COHORT_CAP`, `COHORT_SELECTION_SEED`, the ablation seeds and the harness selection default, with the false-record rule on `tracking.CONFIGURATION_PARAM_NAMES` |
| `2a2a129` | the evaluation-sample output names, derived from the owning count rather than typed; parameterized and cache-keyed the default destination |
| `2cc2033` | `CROSS_ENCODER_MAX_LENGTH`, paired with its checkpoint under config ownership, with a loud load-time consistency check |

`3180145` (2026-08-22, "promote testing paths into the path tables") is the
same shape one layer out — it removed two PRIVATE globs that invented
`{root}/09- Testing` — and is recorded here as adjacent rather than as an
eighth member, because its subject is `oncotriage/paths.py` rather than the
tunables table. **THIS LIST WAS DERIVED FROM `git log`, NOT SUPPLIED**, and the
derivation is stated so the next reader can disagree with the boundary rather
than inherit it silently.

**WHAT THE SECOND AUDIT FOUND: THE TREE IS LARGELY CLEAN, AND THAT IS THE
FINDING.** A read-only sweep for surviving hardcoded values turned up no
category-1 defect — no literal with two owners that can disagree, no value
folded into a URL or a query that a config constant already names. What it left
was a short list of items each of which is about a value being *unreachable* or
*undated* rather than duplicated. Those are the pass recorded immediately below.

**THE STANDING CATEGORY-2 RULINGS — VALUES THAT LOOK HARDCODED AND STAY.**
Each has been argued at its own site and each will be found again by the next
sweep, so they are listed here to be recognised rather than re-litigated:

* **`FALLBACK_MAIN_PATH` in `oncotriage/settings.py`.** The one absolute path in
  any tracked file, deliberate, argued in place, and reachable only when
  `ONCOTRIAGE_MAIN_PATH` is unset. Promoting it to an environment variable is
  what `ONCOTRIAGE_MAIN_PATH` already is; deleting the fallback would make every
  invocation on the development machine need an export.
* **The Spyder footers.** Every file carries a `#!/usr/bin/env python3` and a
  creation-date docstring at the BOTTOM. They are generated, they are inert, and
  rewriting them changes bytes in files whose sha256 several tests compare.
* **`oncotriage/control.py`'s five bounds** — `EXIT_LOCKED = 3`,
  `LOCK_DIRECTORY_MODE = 0o700`, `LOCK_FILE_MODE = 0o600`,
  `STOP_MESSAGE_MAX_CHARS = 1000` and `STOP_MESSAGE_TAIL_PROBE_CHARS = 4096`.
  They stay OUT of `oncotriage/config.py` on that file's own rule that every
  tunable in it has a reader and therefore an effect: these are properties of a
  mechanism, not settings of a pipeline, and an operator changing any of them
  changes nothing about what a run does or costs. Moving them would also make
  `config.py` an importer of `control.py`, which is the one edge that module may
  not have — it imports nothing from the project, which is what lets a
  `usercustomize` hook load it at interpreter startup.
  `tests/test_serial_runner_lock.py` section 9 pins the two mode constants at
  the one owner, and the serial runner's deliberate COPY of the lock machinery
  is pinned against it by AST there.

**AND THE ONE CATEGORY THE SWEEP DOES NOT COVER, stated so it is not mistaken
for a clean result.** A grep-and-read sweep finds a literal that is WRITTEN
twice. It cannot find a literal that is written once and is nonetheless the
wrong owner, and it cannot find a number that is correct today and uncalibrated
— which is what the three provenance sentences below are about.


### Two Regions became settable, three numbers admitted they are uncalibrated (the region-and-provenance pass)

Six items off the second audit. **No billed call, no schema change, no
migration, and nothing on disk was written**: the twelve fixtures replay
**12/12 clean, exit 0, with no recapture**, and the production `inferences.db`
and `ablation_results.db` sha256 are unchanged.

**1. THE TWO DEPLOYMENT-VARYING REGIONS HAVE OVERRIDES.**
`ONCOTRIAGE_S3_STAGING_REGION` and `ONCOTRIAGE_BEDROCK_REGION`, documented in
the environment-variable block above. The short argument: a refusal whose only
remedy is a SOURCE EDIT is a dead end, and a tracked file edited for one machine
is a tracked file committed for every machine. The S3 preflight still refuses a
session in another Region — it must, because a bucket's Region is fixed for its
lifetime — and what moved is the EXPECTED side of that comparison plus the
remedy the message names.

**THE REFUSALS NAME THE SOURCE, AND THAT IS THE HALF THAT IS EASY TO SKIP.** An
exported variable is undone with `unset` and a wrong default is a source edit;
an un-sourced value sends both to the same page, and **the export is the half
that is invisible in a diff**. So `S3_STAGING_REGION_SOURCE` and
`BEDROCK_REGION_SOURCE` are resolved beside the values and rendered into the two
refusals — which is also what gives each of them a production reader.

**A NEW GUARD, BECAUSE THE OVERRIDE MADE A NEW FAILURE REACHABLE.**
`validate_matching_provider_config()` now refuses a Region carrying whitespace or
a `/`. A Region is interpolated into a HOSTNAME, so either character lands
inside `bedrock-runtime.{region}.amazonaws.com` and the resulting failure names
neither — **which is precisely the corruption `_from_env`'s trailing separator
would have caused and the reason both resolvers decline that helper**. Refusing
one shape of it while tolerating the other two would be inconsistent. It does
NOT check that the Region exists: nothing here can, without a network call this
project does not make at configuration time, and inventing a Region list would
refuse a Region AWS adds next quarter.

**2. THREE NUMBERS CARRY THEIR PROVENANCE NOW.** `COHORT_CAP = 1000` is the
**operator-ruled campaign size** — a decision about how much one campaign
spends, roughly $130–$170 of Stage 5 at the measured per-patient cost, not
derived from any statistic and with nothing outstanding to re-derive it against.
`ABLATION_SAMPLE_SIZE_DEFAULT = 75` and `EVALUATION_SELECTION_SIZE_DEFAULT = 10`
are **uncalibrated holding values**, and each now names the instrument that
would calibrate it: the Power/MDE block `27- Ablation Analysis.py` prints for the
first, and — explicitly NOT that block — a precision calculation on an agreement
RATE for the second, which is a different unit over a different n and which
nothing in the project computes today. **An n chosen by nobody has a specific
failure mode**: the study runs, every configuration produces a mean, and the
deltas that do not reach significance are indistinguishable from effects that
are genuinely absent. Reading the MDE block after the money is spent is reading
it too late to change n.

**3. THE TWO S3 RATES CARRY A MACHINE-READABLE DATE.** `S3_PRICING` replaces
`S3_STANDARD_USD_PER_GB_MONTH` and `S3_PUT_USD_PER_1000`, on `PRICING_CONFIG`'s
shape — a `last_updated` FIELD beside the rates — and
`manifest.render_report` prints it beside the dollar figures. **The date was a
`#` comment**, which is unreadable by any program, therefore unprintable by any
report, therefore invisible to the person deciding whether to spend money on the
strength of those two numbers. The dict also carries `quoted_region`, because
the region is now overridable and S3 prices differ by region: when the staged
region and the quoted one disagree the report says the figures are indicative
rather than pricing one region under another's name. **The cost heading's
`us-east-1` was a literal and is now the configured region**, for the same
reason. `estimate_cost` reads the two RATES by subscript and the DATE with
`.get`: a missing rate has no number to print and must fail loudly, and losing
the age of a rate must not cost the estimate.

**4. TWO BOUNDS IN `secrets_scan.py` SAY WHAT THEY ARE NOT.** `sha256_file`'s
1 MiB `chunk` is a READ BUFFER — every byte is hashed at any value of it — and
`scan_files`' `progress_every` is a CONSOLE CADENCE. Neither decides anything
about what the scanner sees. **`config.S3_STAGING_SCAN_PREFIX_BYTES` is the one
argued bound in that file** and it is a security property with a stated limit.
Conflating them is how a security bound gets tuned by somebody who thought they
were adjusting a buffer.

**5. THE COMPOSE GRACE PIN COVERED THE DERIVATION AND NOT THE SHORTFALL.**
`tests/test_compose_shutdown_grace.py` pinned the numbers 620 IS derived from
(4a–4c) and derived both arms' worst cases from the constants (section 3), but
**never compared the derived FIGURE against the 2400 the compose comment
states** — so moving `MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS` or
`MATCHING_MAX_INPUT_PACKED_CHUNKS` left that prose standing, correct-looking and
wrong, with section 3's INEQUALITY still passing (a larger real worst case still
exceeds the grace period). Checks 4d–4i close it. **Each figure is looked for in
its OWN region of the comment**, because the two worst cases are the same number
today (2400) and a whole-file substring test for one is satisfied by the other —
a check satisfied by the wrong evidence, which fails to fail. **17 → 30 checks.**

**6. THIS SECTION AND THE ONE ABOVE IT ARE ITEM 6.**

**WHAT IS NOT DONE, NAMED RATHER THAN LEFT TO BE DISCOVERED.**

1. **`docker-compose.yml` does not pass either new variable into any
   container.** That is CONSISTENT rather than a half-wiring — `s3_stage.py` is
   not a compose service, `MATCHING_PROVIDER` ships `"openai"`, and the Bedrock
   API key is not wired into compose either — so nothing in the container reads
   a Region an operator could have set on the host. **It becomes a live gap the
   day `MATCHING_PROVIDER` flips**, and the fix is one `environment:` line on
   `fastapi` beside the credential's, not a change here.
2. **`BEDROCK_ENDPOINT` and `BEDROCK_REGION` are still not gated by the resume
   fingerprint.** Making the Region settable does not change that; it makes it
   easier to reach, which raises the rank of the follow-up already recorded at
   `config.matching_wire_model()` rather than changing its shape. Two runs
   against `us.openai.gpt-5.6-terra` in different Regions remain
   indistinguishable to a resume gate, and closing it is a seventh gated field
   and a `FINGERPRINT_VERSION` bump.
3. **The two S3 rates are still `us-east-1`'s at any staged region.**
   `S3_PRICING["quoted_region"]` is what makes the gap visible — the report says
   the figures are indicative and `estimate_cost` carries both regions out as
   fields so a machine consumer can branch on it — and nothing RE-QUOTES them.
   Doing so is a source edit with a new `last_updated`, per region, and is a
   decision about how many regions this project intends to price.
4. **`_module_constants` in `tests/test_package_invariants.py` check 2h does
   not see a TUPLE-assigned module constant** — it collects `ast.Name` targets
   and a tuple target is an `ast.Tuple` — so `S3_STAGING_REGION`,
   `S3_STAGING_REGION_SOURCE`, `BEDROCK_REGION` and `BEDROCK_REGION_SOURCE` are
   outside that scan. All four have production readers today; what is missing
   is the check that would say so tomorrow. Found while writing this pass, not
   introduced by it: the blind spot predates these four names.

```bash
# The region-and-provenance pass. Same shape, same directory. NO NETWORK AND NO
# AWS SDK (the preflight probe runs on a stand-in session_factory inside its
# subprocess and asserts boto3 never entered THAT process's sys.modules), no
# keys, no spend, no live Qdrant, no model load, no corpus, no database, no git
# history, no live server, no Docker daemon. It uses FOUR SUBPROCESSES, because
# config.py resolves both Regions at MODULE SCOPE and the arm where the variable
# is SET is unreachable in a process that has already imported it -- the same
# answer tests/test_docker_qdrant_override_and_readiness.py takes for the same
# reason. It EXECS NOTHING and loads no module by location. NOT in the collision
# matrix: it writes NOTHING anywhere, not even a temp directory, and the one
# repository file it reads (oncotriage/config.py) IS rewritten in place by
# tests/test_config_snapshot_date_rot.py and is sha256-compared at the end.
# Bucket A, ~9 s.
python tests/test_settings_region_overrides.py                      #  59
```

`tests/test_staging_exclusions.py` is **148** (was 139; the identifier-cap pass
added section 4j-a..4j-g -- see "The identifier exemption is bounded by length"
below. Before that 117; section 6 gained the `S3_PRICING` shape, the report's
date line, the undated degradation, the region heading and the two
machine-readable region fields, each with its own control),
`tests/test_compose_shutdown_grace.py` is **30** (was 17), and
`tests/test_agent_bedrock_adapter.py` is **278** (was 275).

**THAT LAST ONE IS A LANDMINE THIS PASS CREATED AND THEN REMOVED, and it is
recorded because the same shape will recur the next time a literal becomes
settable.** That file's section 9 -- "nothing leaked" -- asked its question
against LITERALS, including `config.BEDROCK_REGION == "us-east-1"`. Correct
while that was a literal in `config.py`, and a landmine the moment it became
resolved from an environment variable: an operator who had exported
`ONCOTRIAGE_BEDROCK_REGION` would have met a failure about LEAKAGE naming a
constant rather than a defect. **Measured both ways rather than reasoned about**
-- with the export set, the pre-fix file reports 1 failure
(`expected: us-east-1, actual: eu-west-1`) and the shipped one reports 278/0 in
both arms. The restore comparison is against an IMPORT-TIME CAPTURE now, which
is the right instrument for a leakage check anyway, and **the literal claim
moved to the thing that is still a literal**: `BEDROCK_REGION_DEFAULT`, which no
variable can move.

**A DEFECT IN THIS PASS'S OWN WORK, FOUND BY RUNNING RATHER THAN BY READING,
AND IT IS THE INSTRUCTIVE ONE.** The region split in check 4d–4f first used the
two phrases section 3 already pins — `"rounds per patient"` and `"chunks per
patient"` — on the assumption that each opens its own arm's paragraph. **It does
not**: the grouped paragraph opens with a table whose FIRST row is
`criteria chars/trial` and whose second is `chunks per patient`, so the split
point sat two lines INSIDE the grouped derivation and the "per-trial" region
swallowed most of it. The two-directional phrase control caught it on the first
run, which is why the control is a phrase test rather than a number: every
number in that comment appears in several of its paragraphs, so no number could
have discriminated. The anchors are the two section HEADINGS now, and the two
derivation phrases are required to fall inside the region they belong to — which
ties the two anchor sets together so neither can be renamed silently.

**AND A SECOND, IN THE SAME FILE'S FIRST DRAFT.** `_region()` returned the rest
of the file when the END anchor was missing, which SILENTLY WIDENS the region to
everything below it — precisely the failure the scoping exists to prevent, and
one that shows up as a check that still passes. It returns `""` now, and check
4g2 is the control.

**A THIRD, CAUGHT BY READING AND WORTH RECORDING BECAUSE IT WOULD HAVE BROKEN
THE CONTAINER AND NOT THIS MACHINE.** The first version of the cost report's
date line put a MULTI-LINE conditional expression inside an f-string. That is
PEP 701 and needs Python 3.12; `pyproject.toml` declares `>=3.10` and the image
runs **3.11**, where it is a SyntaxError — so `import
oncotriage.staging.manifest` would have failed there while parsing cleanly on
the 3.13 development interpreter. All four touched modules were re-scanned by
AST for that construct afterwards and none remains.


### No direct identifier reaches the model (the de-identification pass)

**THE FINDING THAT SHAPED THE PASS: THE RENDERED RECORD ALREADY CARRIED NO
DIRECT IDENTIFIER, AND THE PARSER IS WHY.** Measured before anything was
written, over all 1,000 corpus bundles at a four-character floor -- every
bundle harvested for names, address lines, city, postal code, geolocation,
telephone, MRN, SSN, driver's licence, passport and every untyped
``identifier[]`` entry (2,610 on one bundle), every rendered summary scanned
for all of them: **ZERO HITS**. `_parse_demographics` reads ``birthDate``,
``gender`` and the two US Core extensions and NOTHING ELSE, and no other
per-resource parser reads a name or an address, so the whole of it is dropped
before ``patient_data`` exists. **So this pass's job is NARROWER than creating
that property -- it is GUARANTEEING it**, and that is worth saying plainly
because a reader who believes `oncotriage/deid.py` is what stops names reaching
the model will not understand what actually does.

**ONE DIRECT IDENTIFIER DID SURVIVE PARSING**: ``patient_id``, the FHIR
``Patient.id``, which on this corpus is byte-identical to the Medical Record
Number in ``identifier[]``. The renderer never printed it. It is not in
``deid.RENDERED_FIELDS``, so it cannot be.

**THE RULING IMPLEMENTED IS A LIMITED DATA SET WITH PSEUDONYMIZATION, NOT SAFE
HARBOR.** Full dates stay -- every elapsed interval, window and precision
behaviour of `PROMPT_VERSION` 1.8.0/1.9.0 is untouched, because Safe Harbor's
year-only dates would destroy machinery that is validated. Ages stay exact to
89 and render as ``deid.AGE_CAP_LABEL`` above it. **STRICTER THAN AN LDS ON ONE
AXIS AND THAT IS THE OPERATOR'S RULING**: 45 CFR 164.514(e)(2) permits city and
ZIP in an LDS; the ruling forbids everything below state, so both are treated
as identifiers here. ``address.state`` is not -- and could not usefully be:
``CA`` is two characters and is the prefix of the tumour marker ``CA 19-9``.

| module | holds |
|---|---|
| `oncotriage/deid.py` | the stage. `RENDERED_FIELDS`, `DEMOGRAPHIC_FIELDS`, `AGE_CAP_YEARS`, the pseudonym, `harvest_identifiers`, the five shape rules, `scan_for_identifiers`, `assert_no_identifiers`, `DEID_REFUSALS`, `DEID_CENSUS`. **IMPORTS NOTHING FROM THE PROJECT** |

**THE ARCHITECTURE IS THE GUARANTEE, NOT A RULE ABOUT WHAT TO PRINT.**
`_create_patient_summary` used to BE the renderer and take
``parse_fhir_bundle``'s output whole, so "no identifier is printed" was a
property of which keys 650 lines happened to read. It is a three-line wrapper
now over ``build_patient_record`` = the stage then ``render_patient_record``,
and **the renderer's parameter is a ``DeidentifiedRecord``** whose ``fields``
carry exactly ``RENDERED_FIELDS`` and whose demographics carry exactly the four
the renderer prints. ``patient_id`` is not a key of it, so no line can print
it; an eleventh key raises rather than yielding ``None``. The rename was
mechanical -- 11 `patient_data[...]`/`.get(...)` became `record.fields...`, one
comment left as prose -- and nothing else in the body moved.

**THE PSEUDONYM IS DERIVED FROM THE CLINICAL HASH, NOT FROM ``patient_id``, AND
THE REASON IS A MEASUREMENT.** ``patient_id`` is on almost every log line this
pipeline emits, so ``sha256(patient_id)`` would be re-identifiable by anyone
holding the logs and the prompts -- two artifacts that land in the same
observability store. ``patient_data_hash`` is **never logged** (checked across
the package: it appears only as an ``inferences`` column), so a token derived
from it is recoverable only by someone holding that database. **The mapping
therefore already exists in the one place the ruling allows and needs no schema
change**: ``inferences`` carries both columns and
`deid.pseudonym_for_identity` is the one function relating them. It is
domain-separated so the prompt's token is not the database's hash -- otherwise
any row carrying both would BE the mapping.

**IT IS RENDERED, AND ITS MARGINAL DISCLOSURE IS ZERO.** The token is a
function of the clinical record, and the whole of that record is already in the
same prompt below the line. Anyone who can act on it already holds everything
it was derived from. **Stated rather than glossed:** under 45 CFR 164.514(c) a
re-identification code must not be "derived from or related to information
about the individual", so this one would NOT satisfy Safe Harbor's coding
condition; it is offered under the LDS shape the ruling chose.

**CLINICAL FREE TEXT IS NOT SCRUBBED, AND THAT IS A DECISION.** A redactor
cannot tell a city called Ontario from a condition display, or a family name
from a syndrome eponym, and editing clinical text deletes evidence silently --
the failure `_classify_procedure_relevance` already argues is worse than the
tokens it saves. So structured fields are guaranteed BY CONSTRUCTION and free
text BY ENFORCEMENT: **Stage 5 scans the RENDERED text and refuses to send.**

**THE GUARD IS THREE LAYERS AND THE PRODUCTION PATH HAS TWO OF THEM.** An exact
scan over the identifiers derivable from ``patient_data``; five provenance-free
shape rules (SSN, phone, email, URL, UUID); and `harvest_identifiers(bundle)`
when a caller has the source. **THE THIRD IS THE STATED GAP**: the graph holds
only the parsed record by the time Stage 5 runs, so a name that reached an
observation display is caught only where a bundle is passed. Threading the
bundle means `TrialMatchState`, `build_initial_state`, both fixture harnesses,
the ablation study and the MCP server, which is a bigger change than this one.

**THE REFUSAL SPENDS THE RETRY BUDGET IMMEDIATELY**, unlike every other failure
return in that node. The condition is DETERMINISTIC -- the same record renders
the same text and the scan finds the same value -- so three attempts buy only
`RETRY_BASE_DELAY`'s backoff sleeps delaying a batch for a verdict that cannot
change. `assert_per_trial_provider_supported`'s argument, one node over. The
patient is recorded as an error, so it is NOT checkpointed and a resume
re-attempts it. **Nothing is billed and no prompt is built**: the guard runs
above `_neutralize_fence_markers` and `render_system_prompt`, which matters
because the prompt is also STORED.

**THE COST IS STATED RATHER THAN DISCOVERED.** A false positive fails a patient
who would otherwise be matched. On real data a patient named Hunter makes
``Hunter syndrome`` a hit and word boundaries do not help -- it IS a whole
word. Synthea's names carry digit suffixes so it is invisible here and would
not be on a hospital extract. `DEID_REFUSALS`' registry entry says so, and it
is the one counter in that block whose most likely cause is a false positive.

**WHAT WAS MEASURED BY RUNNING, ALL 1,000 PATIENTS, BEFORE AND AFTER:**

| | |
|---|---|
| guard hits against the FULL bundle inventory | **0** |
| shape rules fired on real clinical text | **0** |
| ages capped | **313** (age range 20-99; 20 patients are exactly 89) |
| byte-identical after removing the added `Patient:` line | **687** -- every patient at or under the cap |
| the other 313, with the exact age put back | **313/313 byte-identical** -- so the only difference for the capped population is the age field |
| distinct pseudonyms | **1000 of 1000** |
| FHIR files on disk | **byte-unchanged** |

**ELEVEN OF TWELVE FIXTURES ARE INVALIDATED AND WERE NOT RE-CAPTURED.** This is
an input change, so the prompt bytes move. `python fixture_replay.py` reports
**1/12 clean, exit 1**, and the one clean fixture is
`no_candidates_pediatric_age`, which reaches `node_no_candidates` and never
renders a prompt. **Only three field families moved and all three are prompt
HASHES** -- `stage5.llm_classifier_prompt_sha256`,
`stage5.llm_classifier_combined_prompt_sha256` and
`stage5.request_sha256_by_call[N]`. **No verdict, score, criterion or
eligibility field moved.** The recapture happens once, after the whole input
batch lands.

**`FINGERPRINT_VERSION` IS UNCHANGED AT 3 AND THE DIGEST MOVED.** `deid.py` is
in `RENDERER_MODULES`, so a v3-stamped artifact answers **FP_CHANGED** naming
`llm_classifier_renderer_digest` -- which is correct: the renderer genuinely
changed. The stamp's SHAPE did not, so no FP_VERSION refusal and no consumer
edit. **`PROMPT_VERSION` is unchanged at 1.9.0** by that file's own rule: the
TEMPLATE text did not move, only the record interpolated into it, and the
renderer digest is the mechanical half that covers exactly that.

```bash
# The de-identification pass. Same shape, same directory. No network, no keys,
# NO SPEND -- the OpenAI client is a stub that COUNTS AND RAISES, so a guard
# that failed to fire would make a call and the call itself is the failure --
# no live Qdrant, NO MODEL LOAD (ONCOTRIAGE_DEFER_LOCAL_MODELS above the
# imports; torch and transformers asserted absent), no database, no git
# history, no live server. The three registries the renderer resolves are
# STUBS installed through oncotriage/agent/deps.py and cleared in a finally
# whose restore is asserted, which is what lets it run against a checkout with
# no MeSH lookups and no ICD-10 release. Its ONE corpus section SKIPS on a
# runner rather than failing (tests/test_storage_write_durability.py's gating
# shape). It EXECS NOTHING and writes NOTHING anywhere, not even a temp
# directory; the three repository files it reads are sha256-compared at the
# end. Every identifier-shaped fixture value is ASSEMBLED at run time and
# section 11 scans this file with the scanner it tests. Bucket A, ~6 s.
python tests/test_deid_stage_and_guard.py                           # 137
```

**FIFTEEN PLANTED REVERTS, FOURTEEN CAUGHT, AND THE FIFTEENTH IS A FINDING
ABOUT THIS PASS'S OWN CODE RATHER THAN A GAP.** Removing `_cap_age`'s
`isinstance(age, bool)` exclusion changes NO outcome at the current cap --
`True` is 1 and `False` is 0, both below 89, so with or without it the answer
is `(value, False)`. The guard is KEPT as a type contract at the one place an
age is typed, and both its docstring and the check's label were rewritten to
say that rather than to claim a live branch. **A guard that cannot change an
outcome must not be described as one.**

**AND THE HARNESS FOUND A REAL WEAKNESS IN THIS PASS'S OWN TEST.** The check
that the evaluation harness runs the guard was `"assert_no_identifiers" in
_harness_src` -- satisfied by the MODULE DOCSTRING that argues for the guard
and by the IMPORT LINE, so deleting the CALL left it green. **The third time
this project has met "a file that argues about its own settings cannot be
grepped for them."** It walks for a real `ast.Call` inside `build_record` now,
with a non-degeneracy probe that the function was found at all.

**THREE EXISTING TESTS HAD STRUCTURAL CHECKS POINTED AT THE OLD RENDERER AND
ALL THREE WERE RETARGETED RATHER THAN RELAXED.**
`tests/test_agent_summary_temporal_tagging.py`'s vocabulary probe parsed
`_create_patient_summary` -- a three-line wrapper now -- and reported the walk
as broken, which is the OPPOSITE of what a non-degeneracy control exists to
say; `tests/test_agent_summary_cancer_stage.py`'s plant anchor carried
`patient_data.get(...)`; and `tests/test_agent_stage5_input_packing.py`'s
fence-neutralization control rebound `evaluation._create_patient_summary`,
which the node no longer resolves -- so the hostile text never reached a prompt
and c13 reported the neutralizer as broken. **A control that patches the wrong
seam fails, which makes it look like it is working.** Counts are unchanged
(216, 56, 116); only outcomes moved.

**WHAT IS NOT DONE, NAMED RATHER THAN LEFT TO BE DISCOVERED.**

1. **`oncotriage/mcp/server.py` HANDS A MODEL THE RECORD NUMBER, ON THREE
   SURFACES, AND THIS PASS DID NOT FIX IT.** `_patient_summary` reports
   `patient_id` and the UNCAPPED exact age; `parse_fhir_bundle_tool` returns
   the WHOLE parsed record under `patient_data`, `birth_date` included; and
   `match_patient_tool` returns `result`, which carries `patient_id`. The
   consumer of an MCP tool result IS a model. It is left because the fix is a
   contract change to three tool responses whose test needs a live Qdrant for
   sections 4-6, and shipping an unverified change to a serving surface is
   worse than reporting a measured one. **Top-ranked follow-up.**
2. **The bundle inventory does not reach the production guard** (above).
3. **No fixture covers the de-identified render**, because the twelve are
   stale until the batch's recapture.
4. **`GET /match` and `inferences` are unchanged**: `result["patient_id"]` is
   the DB join key and the API's own contract. Nothing model-facing reads them,
   which is why they are out of scope rather than overlooked.

### An ECOG that predates the diagnosis is refused (the pre-diagnosis ECOG pass)

**ONE MEASURED CHANGE, AND WHAT IT IS NOT MATTERS AS MUCH AS WHAT IT IS.** An
ECOG performance status recorded BEFORE the primary cancer was diagnosed
describes a person who did not yet have the disease, and it is wrong in the
FLATTERING direction -- a pre-diagnosis reading is systematically better than
the post-diagnosis one, so it makes an unwell patient look eligible for a gate
(`ECOG 0-1`, `ECOG 0-2`) that decides nearly every interventional oncology
trial. **A general STALENESS floor was measured and REJECTED** -- it demoted 96%
of the scored corpus and recovered nothing -- so an old POST-diagnosis score is
kept and there is no age-based cutoff anywhere in this change.
`tests/test_ecog_pre_diagnosis_suppression.py` check 2k pins a 27-year-old
post-diagnosis reading as KEPT, which is what fails if anyone widens it.

**MEASURED, THE WHOLE CORPUS, THROUGH THE SHIPPED CODE: 23 PATIENTS OF 1,000.**
Scored patients go 698 -> **675**; `ECOG_SELECTION_COUNTS` reads
`{'all_before_primary_diagnosis': 23, 'most_recent_on_or_before_reference_date':
675, 'none_recorded': 302}`. The gaps are not marginal: `1997-11-29` offered as
the performance status of a colon cancer diagnosed `2025-04-09`, `1993-11-14`
against `2025-03-16`, `1982-09-19` against `2007-03-11`; the 23 gaps run
**1.46 to 31.33 years, median 8.87**. **Exactly 23 hashes
move and 977 do not**, driven before-and-after over all 1,000 bundles, and the
23 are the same patient ids the pre-implementation measurement named. The
`ecog_date` column's own comment already recorded the underlying fact -- "the
median selected observation is roughly 17.7 years old, so a performance status
that gates nearly every trial is routinely being read off a reading older than
the disease".

**IT IS A SELECTION-TIME REFUSAL, NOT A RENDER-TIME ONE**, and that follows from
where the record is read. `inferences.ecog_value` / `ecog_selection` /
`ecog_date` are written from `patient_data['ecog_performance_status']` through
`_pipeline_provenance`, so a render-time suppression would leave the STORED row
asserting a grade the prompt did not carry. The refused observation lands in the
**present-but-unusable** family under its own selection value,
`all_before_primary_diagnosis`, and never in `none_recorded`: "this patient has
no ECOG on file" and "this patient's only ECOG predates their cancer" send an
operator to two different places, and `observations_found` still counts what was
on the bundle.

**ONLY THE WINNER IS TESTED, AND THAT IS EXACT RATHER THAN APPROXIMATE.** The
observation handed to the predicate is the MOST RECENT of the eligible pool, so
if it predates the diagnosis every other eligible observation does too --
there is no case where refusing the winner leaves a usable runner-up behind. The
observations the reference-date partition already excluded are later still, or
undated and unordered. Both directions are driven (checks 2n / 2o).

**THE ANCHOR IS THE ONE DERIVATION, AND GETTING IT MEANT SPLITTING A
PROJECTION.** `registries/primary_cancer.py` now holds
`_resolve_primary_cancer_condition()` -- filter, classify, tiebreak, return the
CONDITION -- with `_resolve_primary_cancer()` as `.get("display")` of it and the
new `primary_cancer_onset_date()` as `.get("onset_date")`. So the diagnosis
`inferences.primary_condition` records, the diagnosis Stage 1 expands on and the
diagnosis an ECOG is dated against cannot be three different conditions.
`UNKNOWN_DATE` is normalised to `None` there rather than carried: "unknown"
sorts lexically ABOVE every ISO date, so a caller taking it as a date reads the
least-known diagnosis in the corpus as the most recent one -- the `ecog_date`
trap one field over.

**THE ANCHOR IS RESOLVED FROM THE FILTERED, DEDUPLICATED CONDITION LIST**, which
is the one thing no unit test can see and which check 3g drives: a **refuted**
diagnosis does not anchor anything, because the resolution happens at the end of
`parse_fhir_bundle` where the refuted/entered-in-error filter has already run.
Resolved from the raw resource sweep it would refuse an ECOG against a diagnosis
no other part of the pipeline believes in.

**WHAT IT COSTS, STATED RATHER THAN DISCOVERED: `parse_fhir_bundle` NOW BUILDS
THE CANCER REGISTRY.** The parser's import list goes from `constants`, `utils`
to `constants`, `utils`, `registries.primary_cancer`. Importing it is still
free -- `import icd10` is deferred inside `_build_icd10_cancer_sets()` and
`tests/test_package_invariants.py` is unchanged at **260/0/0** -- but CALLING it
resolves the registry once per process. `icd10-cm==0.0.5` is a declared
dependency and every full-pipeline consumer built that registry a stage later
anyway; what is new is that a process which ONLY parses bundles needs it too,
and that a missing `icd10` raises at the parser rather than three modules
downstream. Item 11a's deliberate loud failure, reached one module earlier.

**BOTH DATES MUST RESOLVE TO DAY PRECISION OR NOTHING IS REFUSED.**
`parse_partial_date()` anchors a coarser date to the MIDDLE of its range, so a
year-only onset of "2019" resolves to 2019-07-15 and comparing against it would
refuse an observation from the first half of that year on the strength of an
anchor the record does not contain -- refusing a score that may well be
post-diagnosis, which is the direction this guard must never fail in. **The
stated limit**: an obviously pre-diagnosis reading is also kept when the onset is
coarse. That is a MISS in the safe direction, it fires **zero times** on this
corpus (every primary onset and every ECOG date is day-precision), and closing
it means deriving each precision's earliest-possible date and comparing bounds
-- a second date convention beside `parse_partial_date`'s mid-range anchor, and
a recorded follow-up rather than untested machinery.

**`ECOG_ANCHOR_COUNTS` IS WHAT MAKES THAT LIMIT COUNTABLE INSTEAD OF SILENT.**
One key per patient that reached the check, so the total is exactly the
population where a refusal was possible: `compared`, `no_primary_onset`,
`onset_precision:{p}`, `observation_precision:{p}`. A key other than `compared`
means the guard was ASKED FOR and could not run -- those patients keep a score
that may predate their own diagnosis and nothing else in the record would say
the question had been asked and abandoned. It reads **`{'compared': 698}`** on
the corpus. The suppressed/kept split is deliberately NOT repeated here: it
already lives in `ECOG_SELECTION_COUNTS`. `load_all_patients()` prints it, which
is the reader that puts it on `tests/test_degradation_counter_readers.py`'s
parser exemption on the same footing as the other four (**155 -> 157**).

**THE VOCABULARY MOVED TO `oncotriage/constants.py`, AND THE DRIFT IT PREVENTS
HAD ALREADY HAPPENED.** `oncotriage/dashboard/tabs/performance.py` keyed its
explanation table on `"most_recent_on_or_before_reference"` -- **no trailing
`_date`** -- while the parser has always written
`"most_recent_on_or_before_reference_date"`. So **the single most common path in
the whole pipeline rendered as "unrecognised path -- not one of the five this
pipeline writes", on every dashboard, for every corpus, and nothing failed**:
the fallback message is the only place a wrong key surfaces and it reads like a
data problem rather than like a typo. `ECOG_SELECTION_VALUES` is the closed
six-member set now, `ECOG_SELECTION_USABLE` the two that publish a grade, and
the dashboard keys its table off the NAMES -- a constant cannot drift from
itself, which is why the fix is the import and not a corrected literal.
`constants.py` is the right home for the same reason `SYSTEM_KEY_ABSENT` is: the
four consumers sit in subpackages that may not import each other (`storage` may
not import `fhir`; a dashboard tab importing the FHIR parser to read six strings
would drag a parser into a Streamlit rerun), and that module imports nothing at
all. Two truncated spellings survive in test SEEDS
(`tests/test_storage_query_layer.py`, `tests/test_clinical_use_framing.py`) and
are provably inert -- **no registered query names `ecog_selection` at all** --
and are reported rather than swept.

**THE DRIFT METRIC NEEDED NO EDIT AND THE ALERT TEXT DID.** The numerator is
DERIVED -- "reported, not `none_recorded`, no value" -- so the new path joined it
by construction, which is correct: an observation existed and no grade came out
of it. Driven: a corpus of suppressed rows reads 1.0, a half-and-half corpus
0.5. What was NOT right was the stored diagnosis: `ECOG_UNAVAILABLE_DIAGNOSIS`
named `DATA_SNAPSHOT_DATE` as THE cause, so an operator meeting the alert for
the new reason would check the snapshot, find nothing wrong and have nowhere to
go. It names both causes now and points at the selection-path breakdown as the
discriminator.

**THE RENDERED LINE NAMES THE CUTOFF THAT ACTUALLY REFUSED.** A pre-diagnosis
observation is well-formed and INSIDE the snapshot, so printing "reference date
2026-08-03" beside it would point a reader at a fault that is not there:

    - ECOG performance status: not available (1 observation(s) on file, none
      usable: all_before_primary_diagnosis; every observation predates the
      primary cancer diagnosis dated 2019-05-26)

The anchor is READ OFF THE RECORD, never re-derived -- a second derivation in
the renderer could state a diagnosis date the refusal was not measured against,
and check 5k pins that the renderer calls no primary-cancer resolver of its own.
The other unusable paths still name the reference date (check 5g).

**THE RECORD CARRIES `primary_diagnosis_date` ON EVERY PATIENT**, on
`reference_date`'s precedent -- the dict records the cutoffs it applied, so a
reader can CHECK the refusal rather than take it -- and it is deliberately **not
hashed**: it explains a refusal, and the refusal itself rides in `selection`,
which is. `tests/test_agent_patient_hash_coverage.py` gains 3d-i and its
non-degeneracy twin 3d-ii (**69 -> 71**).

**WHAT A SUPPRESSED PATIENT'S STAGE 5 RECORD ACTUALLY LOSES, diffed on a real
bundle: TWO lines.** The ECOG line, and the `Patient:` pseudonym -- which is
DERIVED from `compute_patient_hash`, so a hash that moved moves it. That is the
documented coupling in `oncotriage/deid.py` working, not a second change. **215
non-suppressed corpus patients render byte-identical summaries.**

**THIS IS AN INPUT CHANGE AND THE FIXTURES ARE NOT RE-CAPTURED.** Measured
against a `git worktree` at HEAD rather than assumed, twice, deterministically:
the baseline is **1/12 clean** -- the de-identification pass already invalidated
eleven and did not recapture, so the "CURRENT STATE, 2026-08-20 ... 12/12 clean"
note further up this file is itself stale. This change takes it to **0/12**:

| | |
|---|---|
| `normal_2` (patient `23cfa371`, ECOG 2 of 2013-03-14 against a 2019-05-26 breast cancer) | **the one genuinely suppressed fixture** -- gains 7 differing fields: the whole ECOG block plus `terminal.degradation.ecog_selection` / `ecog_value` |
| six other RECORDED fixtures | gain exactly ONE field, `patient_data.ecog_performance_status.primary_diagnosis_date` |
| `mcode_genomic_variant`, `mesh_fallback_siteless_code`, `no_candidates_pediatric_age` | **FATAL**, not DIFFERS -- the derivation gate compares the rebuilt `patient_data` against the recorded one and the added key makes them differ. Its message ("the recipe, the donor bundle or the parser changed") is TRUE: the parser changed |

**THE COST OF THE NEW KEY IS STATED PLAINLY: it widens fixture invalidation from
one file to twelve, and turns three constructed fixtures from a listed diff into
an abort until the recapture.** It was taken anyway, because `--resume` skips on
prompt version / model / collection name / digest and cannot see a `patient_data`
change at all -- so a full recapture was already owed either way -- and because
a refusal that cannot be audited from the record it is written into is worse
than one fixture's worth of diagnostic value in the interim.

**AN EXISTING TEST WAS ONE CORPUS AWAY FROM FAILING FOR THE WRONG REASON, and
this pass found it by running rather than by reading.**
`tests/test_fhir_ecog_surfacing.py` section 7 asserted
`_unusable_real[0]["selection"] == "all_after_reference_date"` -- one member,
against whichever bundle sorted first -- on the premise that the reference date
is the only thing that can make a present observation unusable. On the scratch
corpus that premise is now false in the strongest possible way: the ONLY
unusable path there is `all_before_primary_diagnosis` (4 patients) and
`all_after_reference_date` occurs **zero** times. The check asserts the FAMILY
now -- a present-but-unusable patient names a path from the closed unusable set,
derived from the vocabulary rather than listed -- and PRINTS which paths the
corpus produced. **108 -> 113.**

**AND A STALE ASSERTION IN A THIRD FILE WAS FIXED RATHER THAN LEFT.**
`tests/test_fhir_parser_dict_input.py` asserted "the server module imports
neither os nor tempfile"; the API-shutdown-gate pass re-added `import os` for
one reader (`os.write(2, ...)` in the SIGTERM handler, which must be
async-signal-safe) and did not update it, so that file has been failing 28/1 on
a developer tree. It is bucket **E**, so CI never ran it and nothing went red.
Banning the import was a PROXY for "no temp-file round trip"; the property
itself is that no filesystem call is reached anywhere in the module, which is
what is asserted now -- strictly stronger, since it fails on `os.unlink`,
`os.remove` or `mkstemp` however they were imported. Driven both ways: the
shipped tree reports `[]`, a copy with one planted `os.unlink` reports
`['unlink']`. **28 passed / 1 failed -> 31 / 0.**

```bash
# The pre-diagnosis ECOG pass. Same shape, same directory. No network, no keys,
# NO SPEND, no live Qdrant, no model load, no corpus, no database, no git
# history, no live server -- every bundle is a literal dict handed to
# parse_fhir_bundle. It DOES build the cancer registry (`import icd10`), which
# is not incidental: that is the dependency this change adds to the parser, and
# a test of the change that avoided it would be avoiding the thing under test.
# It writes NOTHING anywhere, not even a temp directory, and it EXECS NOTHING
# and loads no module by location -- the one plant is an attribute rebind
# inside try/finally with the restore asserted BY IDENTITY, which is the
# natural control for a module-global lookup and needs no _EXEC_ALLOWLIST
# entry. NOT in the collision matrix: the three repository files it reads
# (fhir/parser.py, agent/patient.py, dashboard/tabs/performance.py) are written
# by neither of the suite's two writers and are sha256-compared at the end.
# Bucket A, ~2.5 s (MEASURED against ONLY the provisioned CI skeleton).
python tests/test_ecog_pre_diagnosis_suppression.py                 #  94
```

**A DEFECT IN THIS PASS'S OWN CODE WAS FOUND BY RE-READING IT AFTER IT WAS
GREEN, AND MEASURED RATHER THAN REASONED ABOUT.** The first version of the
predicate took the OBSERVATION and re-parsed its date -- while the caller's
partition, four lines above, had already run `parse_partial_date()` over that
exact field. That function INCREMENTS `PARTIAL_DATE_DEGRADATIONS` on an
out-of-range component, so one bad ECOG date was recorded **twice**: driven,
`"2019-02-30"` scored `out_of_range:day = 1` through a bare parse and **`= 2`**
through the selection function. It is exactly the double-count the comment
inside that partition loop already forbids, arrived at from the other side, and
the comment I had written on the second call asserted the opposite. The
predicate takes `(observation_date, observation_precision, anchor)` now -- the
pair the partition already computed, carried with the winner -- so it parses
only the ANCHOR, which is a Condition field the ECOG partition never touched.
Check 1k is the measurement and 1k-i is its non-degeneracy probe, without which
it would be comparing two empty dicts.

**THE PLANT IS AN INVERTED PREDICATE AND THE CONTROL COMES FIRST.** Section 8
rebinds `parser._ecog_predates_primary_diagnosis` to a copy whose comparison is
the wrong way round -- and NOTHING ELSE, so the checks say which change they
caught -- then drives the REAL selection function, the REAL parser and the REAL
renderer. The pre-diagnosis reading is published, the post-diagnosis one is
refused, and the rendered record prints the grade the ruling forbids. The
CONTROL runs above it and requires the shipped predicate to give the clean
answer on the identical inputs; without it a probe that disagreed with
everything would report the plant as caught while measuring nothing. The seam is
then asserted (the rebind really reached the caller) and the restore is compared
**by identity**, not by equality, which any callable of the same name would
satisfy.

**VERIFIED BY RUNNING.** `tests/test_package_invariants.py` **260/0/0**,
unchanged; CI bucket A green; `tests/test_fhir_ecog_surfacing.py` **113**,
`tests/test_storage_ecog_logging.py` **155**,
`tests/test_monitoring_ecog_availability_drift.py` **111**,
`tests/test_agent_patient_hash_coverage.py` **71**,
`tests/test_fhir_birth_date_and_demographics.py` **172**,
`tests/test_registries_cancer_codes_and_stage_extraction.py` **136**,
`tests/test_degradation_counter_readers.py` **157**,
`tests/test_fhir_parser_dict_input.py` **31**; the dashboard panel rendered
through `AppTest` with the new value present (all six paths explained, the
unavailable rate 28% over a 23-suppressed / 5-after-reference / 60-scored
frame); `load_all_patients` driven to show the new counter line. **No money was
spent, no migration was run, no fixture was re-captured and the production
`inferences.db` was never opened.**

**WHAT IS NOT DONE, NAMED RATHER THAN LEFT TO BE DISCOVERED.**

1. **The twelve fixtures are stale and 0/12 replay clean.** Eleven were already
   stale before this pass. The recapture is one paid `python fixture_capture.py`
   run and is the standing item.
2. **A coarse onset date disables the guard for that patient** (above). The
   bounds refinement is the follow-up; `ECOG_ANCHOR_COUNTS` is what makes the
   population countable if it ever becomes non-zero.
3. **A pre-diagnosis SCREENING ECOG is refused with everything else.** The
   ruling is "strictly before the onset", and an ECOG taken two weeks before a
   diagnosis is arguably the baseline a trial screens against. It fires zero
   times on this corpus -- MEASURED, the 23 gaps run **1.46 to 31.33 years,
   median 8.87** -- and admitting a window is a clinical decision with its own
   measurement, not a parser one.
4. **The metastatic/recurrence case is not modelled.** The anchor is the PRIMARY
   diagnosis, so an ECOG taken after the primary and before a much later
   metastatic recurrence is kept, which is right for this ruling and is not the
   same question as "does this score describe the patient's CURRENT disease".
5. **Two truncated `ecog_selection` spellings survive in test seeds** and are
   provably inert (no registered query names the column). Reported, not swept.

### The identifier exemption is bounded by length (the identifier-cap pass)

**ONE PREDICATE CHANGED. `oncotriage/staging/secrets_scan.py`'s
`_is_program_identifier` -- the value validator for the `credential_assignment`
detector -- now refuses to exempt any capture longer than
`_IDENTIFIER_EXEMPTION_MAX_LENGTH`, whatever its shape.** No other detector, no
other file. `docker-compose.yml` is deliberately NOT fixed here.

> **CURRENT STATE, 2026-08-30 — the last sentence describes the tree as this
> pass left it and is kept as written.** `docker-compose.yml` HAS since been
> fixed: both Airflow secrets are interpolated from the environment with
> `${...:?}`, `scan_bytes` over that file returns `[]` again, and
> `python s3_stage.py` reports **1** finding rather than 2. The BEFORE/AFTER
> table below, and the two `[]` rows for the Fernet key and the user list,
> stay as measured on that date. See "The compose file holds no secret (the
> compose-secrets pass)" below for what replaced them, and for why the
> `SIMPLE_AUTH_MANAGER_USERS` row was never a credential in the first place.

**THE DEFECT WAS CERTAIN, NOT PROBABILISTIC, AND THE OLD DOCSTRING SAID
OTHERWISE.** That function exempted anything matching `[A-Za-z_][A-Za-z_\-]*`
with no digit, and accepted the residual as "about a 3% chance" for a
20-character base62 token. The figure is right for twenty characters and wrong
as a general claim, because it is `(52/62)^L` -- a statement about LENGTH.
`docker-compose.yml` sets `AIRFLOW__WEBSERVER__SECRET_KEY` to a **56-character**
literal of letters, underscores and hyphens with **no digit**, so the detector
matched, the validator exempted, and **`scan_bytes` over the real file returned
zero findings**. Measured before anything was edited, and again after.

| | BEFORE | AFTER |
|---|---|---|
| `scan_bytes(docker-compose.yml)` | `[]` | `credential_assignment`, byte 17072, 68 chars, line 299 |
| the four calibration identifiers | all exempt | **all still exempt** |
| `AIRFLOW__CORE__FERNET_KEY: ""` | `[]` | `[]` |
| `SIMPLE_AUTH_MANAGER_USERS: admin:admin` | `[]` | `[]` |
| `scan_filename("docker-compose.yml")` | `[]` | `[]` |

**THE CONSTANT IS 48 AND THE WINDOW IS [44, 55], NOT (29, 56] -- and the
difference is the whole point of measuring rather than choosing.** The brief for
this pass proposed the window `(29, 56]` from the four false positives the
docstring names (18, 20, 26, 29 characters). Two measurements over the real tree
moved the floor:

* **the actual capture population is twelve, not four.** Running the detector
  over all 285 files and asking which captures the validator exempts gives
  twelve distinct values -- `ONCOTRIAGE_AIRFLOW_PASSWORD` (27),
  `ONCOTRIAGE_QDRANT_API_KEY` (25), `resolve_api_key`, `_get_password` and the
  rest -- topping out at **29** (`ci-placeholder-not-a-real-key`), plus the
  56-character offender and nothing in between;
* **the longest DIGITLESS IDENTIFIER in the repository is 43**
  (`MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS`, with
  `MATCHING_PER_TRIAL_PROMPT_CACHE_KEY_ENABLED` beside it), and **six are 41 or
  longer**. Zero reach 44. Those names are not captures today, but a cap below
  them is a cap that reports this project's own constants the first time one
  lands next to a credential keyword.

**So 40 -- the obvious round number, and the one the brief's window admits --
would have been WRONG**, and the standing suite now fails on it: `4j-c` and
`4j-f` both fire at 40, measured against a copied tree.

**WHICH WAY TO ERR IS ARGUED RATHER THAN SPLIT.** A cap too LOW reports a real
identifier: the staging run refuses, an operator adds one allowlist row, nothing
leaves the machine. A cap too HIGH exempts a real credential and it uploads. The
costs are not comparable, so 48 sits five characters above its measured floor
rather than centred in the window.

**THE STAGING PATH IS BLOCKED, AND IT ALREADY WAS -- WHICH IS NOT WHAT THIS PASS
SET OUT TO CONFIRM.** `python s3_stage.py` exits **1** with `VERDICT: REFUSED`
and **two** findings: `docker-compose.yml` (new, this pass) and
`tests/test_tracking_mlflow_index.py`'s synthetic `_SENTINEL`
(`openai_anthropic_key`, 32 chars, PRE-EXISTING). Driving the pre-change module
from a scratch copy shows the second finding fires before this pass too, so the
honest statement is that this change adds the REAL finding to a refusal that was
already standing on a synthetic one. **Nothing uploads until both are ruled on**
-- the compose file by fixing it, the sentinel by an allowlist row or an
exclusion. Neither is this pass's to do.

**`tests/test_staging_exclusions.py` 139 -> 148**, section `4j-a`..`4j-g`, sited
in the value-validator block it extends. **THE REAL COMPOSE FILE IS DELIBERATELY
NOT ASSERTED ON**: it is a known defect scheduled to be fixed, and a check
reading "the shipped compose file has exactly one finding" would go red the day
somebody fixes it -- a test that fails on the change it exists to protect. What
is pinned is the PREDICATE and the window, which stay true afterwards. The
boundary values are DERIVED from the constant (`_identifier_of_length(_CAP)`,
`_CAP + 1`) so they move with it instead of rotting against it.

**FIVE REVERTS, FIVE CAUGHT**, each into a copied tree with `PYTHONPATH` pointed
at it, a realpath preflight asserting the COPY is what imports,
`PYTHONDONTWRITEBYTECODE=1`, and both shipped files sha256-identical afterwards.
None aborted:

| revert | fires |
|---|---|
| the cap deleted outright | 4j-a, 4j-e, 4j-f, 4j-g |
| cap lowered to 28 (past the 29-char capture) | **4i**, 4j-b, 4j-c, 4j-f |
| cap lowered to 40 | 4j-c, 4j-f |
| cap raised to 56 (offender exempt again) | 4j-f, 4j-g |
| `>` weakened to `>=` | 4j-d |

**TWO DEFECTS IN THIS PASS'S OWN WORK WERE FOUND BY RUNNING, NOT BY READING.**
(i) The new checks were first labelled `4p`..`4v`, which **collide with seven
existing checks** in the same section -- a failure reported as "4t" would have
been ambiguous, and this file's whole convention is that a label locates a
check. Renumbered to `4j-*` on the file's own `4b-control` precedent, with a
duplicate-label scan over all 103 labelled checks as the guard. (ii) `4j-d` and
`4j-e` fell back to `_CAP or 0` when the constant is absent, so the cap-removed
revert made `4j-e` ask whether a **one-character** value is exempt -- it fired,
but for a reason its own label denies. The fallback is the offender's length
(56) now, and the same revert fires it on a 57-character value.

**Every new fragment in the test file is assembled with `_shape()`**, because
`4b-control` scans that file with the scanner it tests and requires zero hits --
and a 56-character digitless value beside a `SECRET_KEY` keyword is, as of this
pass, exactly a hit. Verified by running: `4b-control` still passes.

**VERIFIED BY RUNNING.** `tests/test_staging_exclusions.py` **148/0**;
`tests/test_package_invariants.py` **260/0/0** (unchanged -- the new constant is
read, so check 2h is satisfied); CI bucket A **79 files, 0 failed, 0 not run**;
`python s3_stage.py` exits 1 as above. Exactly two files changed. Across the
whole repository **exactly one capture changed status**, and it is the offender.

## Conventions

- **All tunables live in `oncotriage/config.py`, and every one of them has a reader.** (`03- Config.py` used to re-export them for the exec chain; pass 20e deleted it.) Retrieval sizes, thresholds, rate limiting, drift windows, batch runner settings. Don't scatter magic numbers into node bodies. **The second half of that sentence is new in pass 20f-2 and it is enforced**, by `tests/test_package_invariants.py` check 2h: a constant here that nothing anywhere reads fails, and the exemption list is closed. That is what this promise is worth — an operator who sets a value in this file is entitled to an effect, and `BATCH_SIZE` and `EXPANSION_TEMPERATURE` were two that had none. Note the parenthesis that used to say "temperatures (both 0 for determinism)": `MATCHING_TEMPERATURE` is `None` because gpt-5.6-terra rejects the parameter, and `EXPANSION_TEMPERATURE` is deleted — so the phrase described neither of the two things it named.
- **The three model identities — `EMBEDDING_MODEL`, `MATCHING_MODEL`, `CROSS_ENCODER_MODEL` — live in `oncotriage/config.py` together**, and `BM25_SPARSE_MODEL_NAME` deliberately does not (it stays in `oncotriage/embedding.py`, beside the one construction site, with the "changing it rebuilds the index" warning it needs). The asymmetry is argued at both constants; the short version is that `storage` may not import `agent`, so the cross-encoder's name cannot live beside its loader.
- **There is no `print` anywhere in `oncotriage/`, and there is no `builtins.print` monkey-patch.** Output goes to one of two channels in `oncotriage/observability.py`: `log = get_logger(__name__)` for anything machine-readable, `console.out(...)` for anything a human watches. Both write to **stderr** — stdout is the MCP server's protocol stream. See "Structured logging (the logging pass)" below.
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

**A CHECK THAT NAMES A SYMBOL MUST COVER EVERY REFERENCE FORM,
each with its own negative control.** Python reaches a name three
ways — the bare name, the attribute form, and the from-import
binding — and a check written against one of them silently passes
over the other two. Three defects of this class have shipped:

- File 36's walk covered `ast.Name` and would have missed the
  `config.X` attribute form;
- File 47's BM25 construction-site check matched the bare
  `SparseTextEmbedding(...)` and `fastembed.SparseTextEmbedding(...)`
  evaded it;
- pass 20c-3c-2's subprocess traps patch module attributes, and
  the comment beside them asserted that a from-import escapes.

The third one is the instructive one, because when pass 20c-3i went
to fix it the claim turned out to be **false**: `from X import name`
is an attribute read performed when the import *runs*, and every
package import runs after the trap is armed, so all three forms were
already caught. Firing them is what established that; the comment had
been reasoning. What *did* escape, measured, was `os.system`,
`os.posix_spawn`, `os.execv` and `os.fork` — not a reference form at
all — plus one genuine pre-bound from-import
(`prompt_toolkit.application.application.Popen`, taken before the
patch). All are closed now, and the closure has a planted-module
control of its own. **So: fire each form, do not argue it.** A form
you reasoned about is a form you have not tested, and the reasoning
is wrong about as often as the code.

**THE EQUIVALENCE PROOF CANNOT SEE A NAME THAT IS NEVER READ.**
Every pass since 20c-2a has been accepted on an `ast.unparse`
comparison against `git show`, and that proof has one blind spot it
cannot close by construction: it only compares what is *there*. A
name that is declared and never read is equivalent to itself on both
sides of every diff, forever. That is how `PASSWORD_SOURCE_ARGUMENT`
shipped — a constant naming a value `password_source()` can never
return, through a full pass and 244 checks, found by reading. Pass
20c-3i made the scan standing (File 47 check 2h: unused imports,
never-read module constants; check 2g already covers shadowed names)
and it immediately found two more of the same shape —
`PASSWORD_SOURCE_ENV`, declared for callers to assert against and
read by nothing, and `deps.RESOLUTION_STATES`, documented as a closed
set that no caller consulted. Both are load-bearing now. The scan's
own first version counted assignment *targets* as reads and so passed
vacuously over the whole package; its negative controls caught that
before it shipped, which is the argument for controls restated as an
event rather than a principle.

**A SCAN OVER A DIRECTORY TREE MUST RECURSE.** File 47's subpackage
check was an `os.listdir` one level deep while `oncotriage.dashboard.tabs`
had been nested since pass 20c-3c-1 — which noticed, and answered by
hand-asserting that one nested name, which works for exactly as long
as someone remembers to add a line per nesting. The cost is not a
failing test: setuptools does not recurse into a listed package, so an
undeclared subpackage is present in an editable install and **absent
from a built wheel**, and nothing surfaces until someone builds one.
The scan walks to any depth now, and its negative control plants a
package *three* deep — because a control planted at the top level
would have fired against the old scan too and proved nothing about
what changed.

### Stage 5 has a second Bedrock branch: Claude Sonnet 4.6 over Converse (the Converse pass)

**THE BRIEF'S PREMISE WAS FALSE AND THE DOCUMENTATION SETTLES IT TWICE.** It
asked for an Anthropic branch and said "Converse and the Anthropic Messages API
are both candidates on bedrock-runtime". The Messages API is not a candidate for
this model at all. Read 2026-08-30:

* the **Claude Sonnet 4.6 model card** (`model-card-anthropic-claude-sonnet-4-6.html`)
  marks `bedrock-mantle` NOT SUPPORTED for this model outright, and on
  `bedrock-runtime` its API table reads Messages **no**, Responses **no**, Chat
  Completions **no**, Converse **yes**, Invoke **yes**;
* **`structured-output.html`**'s supported-API table names the Messages API and
  marks it **No**: "The output_config.format parameter is rejected with a 400
  error. To use structured outputs with Anthropic Claude models, send the
  request through the Converse API or the InvokeModel API on the bedrock-runtime
  endpoint."

**SO THE REAL CHOICE WAS CONVERSE versus InvokeModel, AND THE BRIEF'S DECIDING
CRITERION DOES NOT DISCRIMINATE.** Prompt caching is expressible on both --
`prompt-caching.html` documents `cachePoint` for Converse and `cache_control` for
InvokeModel and lists Sonnet 4.6 for both at 1,024 minimum tokens, 4 checkpoints,
5m/1h TTLs, `system`/`messages`/`tools`. Converse won on four secondary
arguments, each recorded at the module: **botocore validates the request shape
locally** (`ParamValidationError` before a signed request leaves the machine,
which is `validate_matching_provider_config()`'s own rule extended to the body,
where `invoke_model(body=json.dumps(...))` sends an opaque blob); the cache
counters are **modeled fields of the API** rather than of a vendor-versioned
body; `stopReason` is a **closed nine-member vocabulary** that distinguishes a
truncation from a `malformed_model_output`; and `outputConfig.effort` is
first-class.

**NOTHING RUNS AND THAT IS MEASURED THREE WAYS.** `MATCHING_PROVIDER` is
`"openai"`. The OpenAI kwargs the shipped `call_matching_model` sends were
compared against the kwargs the function at `git show HEAD:` sends -- lifted by
AST and exec'd into the same globals, never retyped -- and the two dicts are
**IDENTICAL**, timeout included (the same cached object). `python
fixture_replay.py` is **12/12 clean, exit 0, with no recapture**. And boto3 is
**not imported** by importing the package, importing the adapter, or driving the
OpenAI path -- asserted, and free to assert because it is not installed on this
machine at all.

| | |
|---|---|
| new module | `oncotriage/agent/bedrock_anthropic_adapter.py` -- the Converse translation, the error taxonomy, the A1..A10 go-live list |
| new provider | `MATCHING_PROVIDER_BEDROCK_ANTHROPIC = "bedrock_anthropic"`, a **third member** of the closed tuple |
| new deps key | `BEDROCK_ANTHROPIC_CLIENT` -- a third key, because the two Bedrock keys hold objects of different TYPES (`openai.OpenAI` with `.responses.create` vs a botocore client with `.converse`) |
| new counter | `BEDROCK_ANTHROPIC_DEGRADATIONS`, the 33rd in `oncotriage/degradation.py` |
| new test | `tests/test_agent_bedrock_anthropic_adapter.py` -- **261 checks, bucket A, ~0.9 s** measured against ONLY the CI skeleton |

**A THIRD MEMBER RATHER THAN A SUB-SELECTOR ON `"bedrock"`.** Both are Amazon
Bedrock on one bill, which is the argument FOR one member -- and it is the wrong
axis. What differs is the client library, the credential chain, the request
shape, the response shape, the error classes, the degradation vocabulary and the
model. A sub-selector leaves every consumer that asks "is this bedrock?"
answering yes for a configuration its code cannot serve (`get_bedrock_client()`
would build an OpenAI-SDK client for a boto3 branch) and creates nonsense
combinations that each need their own refusal. A third member keeps "a typo
fails loudly" with no new mechanism and no new state space.

**THE MAPPING'S TWO NON-RENAMES, WHICH ARE WHERE THE RISK IS.**

* **THE SCHEMA TRAVELS AS A SERIALIZED STRING.** Converse's
  `outputConfig.textFormat.structure.jsonSchema.schema` is a **String** -- both
  `structured-output.html`'s example and boto3's own request syntax say so --
  while the InvokeModel form is an object. It is `json.dumps`'d from the SAME
  object `build_response_format()` produces, unwrapped rather than rebuilt.
  **`strict` HAS NO TARGET FIELD**: on Converse the field IS the constrained
  decode (`strict` there belongs to `toolSpec`), so it is not forwarded and
  `_text_format_param` **RAISES** if the chat form ever returns `strict: False`
  -- because the Converse path would otherwise go on constraining a decode the
  caller had just asked not to constrain.
* **THE USAGE COUNTS ARE DISJOINT AND MUST BE SUMMED BACK.**
  `prompt-caching.html`, verbatim: "the `inputTokens` field represents only the
  non-cached input tokens ... `total input tokens = inputTokens +
  cacheReadInputTokens + cacheWriteInputTokens`". OpenAI's `prompt_tokens`
  INCLUDES cached tokens. **A direct rename would under-report Stage 5's input
  tokens by exactly the cached amount on every cache hit and under-price the
  run** -- silently, in the direction that flatters the migration. On the
  canonical fixture that is 1,080 reported against 19,000 sent: a 94%
  under-count. Nothing in the brief mentions it. It is plant P1 in the standing
  test.

**WHAT CANNOT BE EXPRESSED IS DROPPED, COUNTED AND LOGGED, on the
`seed_not_expressible` pattern.** `seed` has no Converse field and no
`extra_body` escape hatch (an unknown key in a modeled boto3 call is a local
`ParamValidationError`, so there is nothing for a probe to discover -- unlike
`BEDROCK_SEND_SEED_IN_EXTRA_BODY` on the Responses branch).
`MATCHING_REASONING_EFFORT` is dropped too and the reason is a vocabulary
mismatch rather than a missing field: OpenAI's is `none|minimal|low|medium|high`
and this project is calibrated at `'none'`; Anthropic's are `thinking`
(adaptive/disabled) and `effort` (low|medium|high|max), and `'none'` is a member
of neither. What is SENT instead is `BEDROCK_ANTHROPIC_THINKING` (default
`"disabled"`), **declared rather than computed**, and both halves of the
substitution are in the log line. **THIS IS A DIFFERENT JUDGE.** The 69.1%
agreement measurement behind the `'none'` choice was taken on another model and
nothing carries it across.

**CONVERSE RETURNS NO MODEL ECHO, SO `MatchingModelMismatchError` CANNOT FIRE
ON THIS BRANCH.** The response shape declares `additionalModelResponseFields`,
`metrics`, `output`, `performanceConfig`, `serviceTier`, `stopReason`, `trace`
and `usage`, and no `model`. Three things are done: the echo is **asked for**
(`additionalModelResponseFieldPaths=["/model"]`, which the API reference says is
ignored when the field is absent, so it is free if unsupported and a real
attestation if it works -- A3); when it does not arrive the REQUESTED id is used
**and the substitution is counted** under `model_echo_unavailable`, reaching the
run-end report; and pricing is unaffected, because Bedrock bills for the id the
request named. **`inferences.matching_model` on a `bedrock_anthropic` row is
therefore what was requested rather than what answered, unless that counter is
zero.** The alternative -- passing the requested id through silently -- would
make the mismatch check compare a value with itself.

**A DEFECT STAGE 5 HAD ALREADY REMOVED WAS ABOUT TO BE REINTRODUCED, AND IT WAS
FOUND BY READING WHAT THE CONSUMER DOES WITH THE FIELD.** Converse has no
refusal content block, so the first version of this adapter left
`message.refusal` permanently None. Stage 5's refusal route --
`REFUSAL_ERROR_PREFIX` in `evaluation.py` -- exists precisely to stop a decline
being read as a parse failure and RETRIED, and its own block records the cost it
removed: "three billed calls and a record that names the wrong fault". With
`refusal` never set, every `guardrail_intervened` or `content_filtered` reply
would have arrived as empty content, failed the parse, and been re-sent twice
more to a guardrail that blocks deterministically. **The fact IS expressible on
Converse -- it arrives in `stopReason` rather than in a content part -- so it is
mapped**, from `_STOP_REASON_REFUSALS` alone, with the raw stop reason named in
the text so `inferences.error` gets back to the API fact. Nothing else sets it,
which is what Stage 5's own extractor reads as "not refused". It is plant P11.

**`store` HAS NO ANALOGUE AND NEEDS NONE.** The Converse API reference states
its own contract: "Amazon Bedrock doesn't store any text, images, or documents
that you provide as content." The Responses branch's `store=False` exists
because that API's vendor default retains the request for 30 days.

**THE SHIPPED SCHEMA IS INSIDE BEDROCK'S SUBSET, MEASURED NOT ASSUMED.** That
page forbids recursive schemas, external `$ref`, numerical constraints, string
constraints and any `additionalProperties` other than `false`. A walk over
`build_response_schema()` finds **none** of them -- it uses only object, array,
string, number, string `enum`, `required` and `additionalProperties: false`. The
walk is re-derived in section 2 with two poisoned controls, so a schema edit that
introduced one fails there rather than as a 400 mid-campaign.

**THE PRICING IS PART MEASURED AND PART INFERRED, AND THE ROWS SAY WHICH.**
`global.anthropic.claude-sonnet-4-6` is **MEASURED** from the AWS Marketplace
listing the model card names (prod-ffvjxvh4ltq64, read 2026-08-30): $3.00 in /
$15.00 out / $0.30 cache read / $3.75 cache write (5m) / $6.00 (1h) per 1M. The
`us.` / `eu.` / `au.` / `jp.` / In-Region rows are **INFERRED** at +10%, carried
over from the GPT-5.6 Terra pattern, because that listing publishes Global
dimensions only. The shipped default is `us.` -- the geo profile -- on the
project's own recorded residency argument, not the cheaper `global.`; nothing
here flips a residency decision to make a pricing table cleaner. **A6 settles it
against a console bill.**

**THE STANDING TEST FOUND A DEFECT IN THIS PASS'S OWN WORK THAT READING DID
NOT.** `BEDROCK_ANTHROPIC_PROFILE_PREFIXES` has five members off the model card;
the first pricing table had three rows. So a `jp.`-prefixed configuration would
have passed validation, spent a live Stage 5 call, and only then raised
`UnknownModelPricingError` from inside the writer -- after the money, with no row
to show for it. Two rows closed the gap and a **new local refusal** closed the
class: `validate_matching_provider_config()` now reads `PRICING_CONFIG` directly
(never `get_model_cost`, which lives in `utils` and would be the forbidden
cycle) and refuses an unpriced model before anything is sent. **The Responses
branch has the same hole and it is NOT closed here** -- its three ids are all
priced today, so the check would change nothing, and widening a branch this pass
was told not to alter is scope creep. Recorded as a follow-up.

**ELEVEN PLANTED DEFECTS, ELEVEN CAUGHT**, each a one-token edit to an in-memory copy
(argued at `_EXEC_ALLOWLIST`; `git show` can supply none of them, because the
module has no prior revision), each paired with a control requiring the SHIPPED
module to give the clean answer -- without which a probe that always disagreed
would report every plant as caught while measuring nothing.

**TWO PINS MOVED IN `tests/test_agent_bedrock_adapter.py` AND BOTH ARE THE CHECK
WORKING**: the provider tuple 2 -> 3 members, and `call_matching_model`'s return
count 2 -> 3. What each protected is re-asserted in stronger form -- the
vocabulary is still pinned by exact composition AND order, and the OpenAI return
is now pinned as the LAST statement of the function and unguarded, which is what
"the default path is unchanged" actually means. That file is **281** (was 275).

**A CONSTANT THIS PASS ADDED WAS THEN DELETED.** `MATCHING_PROVIDERS_BEDROCK`
had no production reader -- only the test asserting it existed -- which is the
dead declaration check 2h reports and the shape pass 20f-2 deleted `BATCH_SIZE`
for. The argument for writing it when a second site needs it is left at the site.

**VERIFIED BY RUNNING.** `tests/test_agent_bedrock_anthropic_adapter.py`
**261/0**; `tests/test_agent_bedrock_adapter.py` **281/0**;
`tests/test_package_invariants.py` **260/0/0**, unchanged -- so import purity
holds, no never-read name was introduced and the new `_EXEC_ALLOWLIST` entry is
accepted; CI bucket A **81 files, 0 failed, 0 not run**;
`.github/scripts/ci_test_buckets.py --check` consistent at **100 files**;
`static_checks.py` compiles 248; `python fixture_replay.py` **12/12 clean, exit
0, no recapture**; the production `inferences.db` sha256 **unchanged**
(`ab1403e3...`). **No money was spent, no AWS call of any kind was made, and no
migration was run.**

**WHAT IS NOT DONE, NAMED RATHER THAN LEFT TO BE DISCOVERED.**

1. **THE PROBE HAS NOT BEEN RUN.** `bedrock_probe.py --provider
   bedrock_anthropic` is extended and gated behind `--i-understand-this-bills`;
   this pass spent nothing. Every A1..A10 item is documentation until it runs.
   A1 (is the schema accepted AND enforced) and A2 (does the cache warm, and
   does the disjointness formula hold against a real response) are ranked first
   and second because the first makes the branch useless and the second costs
   money silently.
2. ~~**PER-TRIAL MODE IS REFUSED ON THIS PROVIDER**~~ -- **CLOSED. See "Per-trial
   mode reaches the Converse branch" below.** The gap this named was real and
   the reason it gave was right: `cachePoint` with a 1,024-token minimum is
   exactly the mechanism a per-trial wave needs. It is built, and the write is
   CONFIRMED out of `usage.cacheWriteInputTokens` rather than assumed. The
   Responses branch is still refused, deliberately.
3. **THE FIXTURE HARNESSES REFUSE THIS PROVIDER ALREADY AND THAT IS INHERITED,
   NOT BUILT.** `capture.assert_provider_is_hookable()` admits OpenAI alone, so
   a capture or replay under `bedrock_anthropic` refuses by name rather than
   billing for calls no proxy wraps. There are no Converse fixtures.
4. **`BEDROCK_REGION` IS STILL NOT GATED BY THE RESUME FINGERPRINT.** Two runs
   against `us.anthropic.claude-sonnet-4-6` in different Regions are
   indistinguishable to the resume gate. Same follow-up the Responses branch
   already carries; a third provider raises its rank.
5. **`anthropic` IS AN UNDECLARED DEPENDENCY, found while working and not
   introduced here.** `oncotriage/evaluation/rater.py` imports it (deferred, at
   line 1952) and `pyproject.toml` declares it nowhere. Unrelated to this branch
   -- which uses boto3, declared -- and worth a line in the next packaging pass.
6. **THE INFERRED PRICING ROWS.** See A6.

### Per-trial mode reaches the Converse branch, and cache-or-nothing stops being an assumption (the Converse per-trial pass)

**THE BRANCH THAT SHIPPED REFUSED THE ARM THE CAMPAIGN RUNS.**
`MATCHING_PER_TRIAL_CALLS_ENABLED` is **True** and per-trial is the pipeline's
binding design; `assert_per_trial_provider_supported()` admitted OpenAI alone,
so `bedrock_anthropic` could only ever run GROUPED. A paid go-live probe would
have validated a mode the campaign does not use. **NO AWS CALL AND NO BILLED
CALL OF ANY KIND WAS MADE**, `MATCHING_PROVIDER` still ships `"openai"`, and
the OpenAI request is **byte-identical to the one `git show HEAD:` builds** --
`call_matching_model` with and without a cache key, and
`call_matching_model_warmup`, all three compared by driving the HEAD functions
exec'd into the live module's globals rather than by reading them. The twelve
fixtures replay **12/12 clean, exit 0, no recapture**, and their sha256 set and
the production `inferences.db` (`ab1403e3…`) are unchanged.

**WHAT AWS ACTUALLY SAYS, READ 2026-08-30 AND CITED PER FACT.** Every answer
below is from `prompt-caching.html`, `API_runtime_Converse.html`,
`API_runtime_OutputConfig.html` or `feature-retry-behavior.html`. None of it is
inferred from the Responses branch.

| question | answer |
|---|---|
| where a `cachePoint` may go | `system`, `messages`, `tools`. **This module places exactly one, at the end of `system`** -- the vendor's own advice: "place stable content (tools, system) before variable content (messages), and place cache checkpoints after the stable content" |
| how many | **4** per request for Claude Sonnet 4.6 |
| minimum prefix | **1,024 tokens** -- and evaluated against the **cumulative** tokens across tools+system+messages, not per section |
| **below the minimum** | **"your inference still succeeds, but your prefix isn't cached."** No error. No warning. Every wave call at the full input rate, and the only trace is a zero in a usage field |
| TTL | 5m (default) / 1h, and **"resets with each successful cache hit"** |
| confirmable? | **Yes, and AWS instructs it**: "Support for prompt caching doesn't guarantee a cache hit for any request. Check the cache usage fields in the model response." The fields are `usage.cacheReadInputTokens`, `cacheWriteInputTokens` and `cacheDetails` |

**WHAT THE TTL RESET MEANS FOR THE WARMUP, STATED AS THE CONSTRAINT IT
ACTUALLY IS.** It is NOT "the whole wave must finish inside 5 minutes" -- it is
that no GAP between two consecutive prefix-sharing requests may exceed the TTL.
The wave submits every request to the pool up front, so the largest gap is a
small multiple of one call's latency at any bound above 1, and at a bound of 1
it is exactly one call's latency. `MATCHING_REQUEST_TIMEOUT_SECONDS` is 300, so
a single call running to its full read budget sits inside the 5-minute window
with nothing to spare -- **the one arithmetic collision worth knowing about**,
and why `BEDROCK_ANTHROPIC_CACHE_TTL` accepts `"1h"`. Nothing depends on
wall-clock scheduling: the warmup is AWAITED, so the write always precedes the
reads.

**AND THE SHORT-PATIENT-RECORD QUESTION HAS A MEASURED ANSWER: THE FLOOR IS
UNREACHABLE FOR THIS PIPELINE.** `render_system_prompt` emits **21,142
characters of instructions -- ~5,285 tokens -- before a single character of
patient record**, five times the 1,024 floor, and the twelve fixtures' real
Stage 5 system prompts measure **8,115 to 10,464 tokens**, eight times the
floor at the smallest. A short record moves the total by the record; the
instructions alone clear it. `tests/test_agent_bedrock_anthropic_per_trial.py`
section 8 re-derives that from the LIVE renderer, so a prompt shortened past
the floor fails there rather than as a campaign that quietly stopped caching.

**CACHE-OR-NOTHING IS NOW ENFORCED ON THIS PROVIDER AND ASSUMED ON THE OTHER,
AND THE ASYMMETRY IS AN API FACT RATHER THAN A CHOICE.** Converse reports a
WRITE count; OpenAI's Chat Completions usage reports only a READ. So on OpenAI
a healthy first warmup and a warmup that cached nothing are the same response,
there is nothing to confirm against, and enabling the check there would fail
every patient of the SHIPPED arm -- an outage, not a conservative default.
`PER_TRIAL_CACHE_CONFIRMING_PROVIDERS` is where that is decided and argued, and
`tests/test_agent_bedrock_anthropic_per_trial.py` section 6 drives the real
node on the real OpenAI arm to prove the gate holds.

| outcome | what it means | what happens |
|---|---|---|
| `wrote` | `cacheWriteInputTokens > 0` | the wave goes out |
| `already_warm` | a read with no write -- a retry, a resumed or resampled patient, the same patient inside the TTL | **the wave goes out.** The prefix IS warm, which is what the wave needs. A naive `write > 0` test fails every retried patient in the campaign, and that is plant P4 |
| `reported_zero` | both present, both zero -- **the provider answered and the answer is no** | ZERO trial calls. The patient fails cleanly and the checkpoint resumes it |
| `not_reported` | neither field present | ZERO trial calls, counted under its OWN key -- a reported zero sends an operator to the prefix and an absent field sends them to the API |

**BOTH WRITERS ARE COVERED, AND THE SECOND IS THE ONE A NAIVE IMPLEMENTATION
MISSES.** When the provider refuses the warmup's SHAPE the schedule degrades to
one-then-rest and the first REAL trial call becomes the writer. It carries the
same `cachePoint` and reports the same fields, so `_confirm_cache_write` takes
the writer as an argument and the fallback obeys the identical rule -- plant P6.
A writer that answered and cached nothing has its verdict WITHHELD and the
patient failed, because publishing one verdict and N-1 not-evaluable trials
COMPLETES the patient and `_on_done` checkpoints a completed patient: the c33
lesson, reached from a new direction. Its response is still FILED, so
`_account_unconsumed` folds the tokens it was billed rather than losing them.

**A WAVE CALL THAT MISSED THE CACHE SURFACES AND IS NOT FATAL.**
`PER_TRIAL_CACHE_READ_MISSES` is the 34th counter in
`oncotriage/degradation.py`'s registry. The call was issued, answered and
BILLED, so the finding is a broken COST premise rather than a broken
JUDGEMENT -- discarding it would spend the money twice and lose an answer
nobody doubts. Plant P7 absorbs it; plant P8's checks are the opposite mistake,
"surface" read as "fail the patient".

**A PRE-EXISTING BUG WAS FOUND AND FIXED, AND IT WOULD HAVE MADE THE FALLBACK
UNREACHABLE ON THIS BRANCH.** `classify_warmup_rejection` reads an HTTP status
through `_http_status_of`, which looked at `exc.status_code` and
`exc.response.status_code`. **A botocore `ClientError` carries neither**: its
`.response` is a plain dict and the status lives at
`["ResponseMetadata"]["HTTPStatusCode"]`, so `getattr(dict, "status_code")` is
None and the function returned None for EVERY Converse error. A provider
refusing the warmup's `maxTokens` would have been read as a transport failure
and **failed the patient**, once per patient, for the whole campaign, instead
of degrading with a named counter. Fixed, with Converse's `maxTokens` spelling
added to the parameter-name list, and driven both directions in section 5a.

**THE PARALLEL BOUND IS CONFIGURATION AND IT IS THIS PROVIDER'S OWN.**
`BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS` (None = follow the shared bound)
overrides `MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS`, reconciled in one place by
`config.per_trial_parallel_bound()`. A bound derived from an estimated OpenAI
latency is not a bound for an AWS account whose Amazon Bedrock
requests-per-minute quota is applied below the default, and editing the shared
constant would silently re-pace the SHIPPED arm to suit a provider it does not
use. It is HONOURED, measured with a barrier rather than a clock -- a
dispatcher running sequentially cannot satisfy a barrier of 2, and section 9's
bound-of-1 arm is the control that breaks it.

**WHAT HAPPENS WHEN THE LIMIT IS HIT, IN TWO STAGES.** Converse answers
`ThrottlingException` / HTTP 429; botocore's `standard` mode classifies that as
a THROTTLING error and retries with a **1,000 ms base delay, exponential
backoff, full jitter, capped at 20 s**, honouring `x-amz-retry-after`, up to
`config.bedrock_anthropic_max_attempts()` TOTAL attempts (default
`OPENAI_SDK_MAX_RETRIES + 1` = **2**, so one retry). Past that: a trial call is
recorded `per_trial_call_failed` and the patient completes without it; the
WARMUP fails the patient cleanly and the checkpoint resumes it. **AND THERE IS
A SECOND FLOOR THAT A BIGGER RETRY BUDGET CANNOT REACH** -- standard mode's
retry QUOTA, a 500-token bucket charged 5 per throttling retry, where "when the
available tokens are exhausted, the SDK returns the error without retrying".
Sustained throttling above ~32% drains it and retries stop entirely. So the
remedy for SUSTAINED 429s is a smaller `BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS`,
and for BURSTY ones a larger `BEDROCK_ANTHROPIC_MAX_ATTEMPTS`;
`BEDROCK_ANTHROPIC_RETRY_MODE` offers AWS's own documented answer for a
throttled account (`"adaptive"`, which adds a client-side rate limiter) and is
NOT the default, because the same page says adaptive "can delay or block the
INITIAL request" and "is not recommended as a general default".

**A DOCUMENTATION DEFECT IN CODE THIS PASS DID NOT WRITE.** `_output_config`
claimed boto3's request syntax is
`outputConfig={'textFormat': {...}, 'effort': 'string'}`.
`API_runtime_OutputConfig.html` declares **exactly one member, `textFormat`,
and no `effort`**. `BEDROCK_ANTHROPIC_EFFORT` defaults to None so nothing is
sent today and no behaviour rests on it; an operator who sets it gets a
botocore `ParamValidationError` before a signed request leaves the machine,
which is the local-validation property the Converse choice was made for. The
claim is corrected in place and (A5) is the item that settles where the field
really lives.

**FOUR NEW GO-LIVE ITEMS, A11..A14, RANKED.** (A11) is the warmup's request
shape -- `maxTokens = 1` with the structured-output block dropped -- ranked
first because a warmup that cannot be issued makes the mode unavailable.
(A12) is the write-and-read pair, ranked second because it is the failure that
costs money silently, and it is now the one the shipped code REFUSES rather
than absorbs. (A13) records that `get_model_cost()` has no cached term, so a
cache hit makes every stored `estimated_cost_usd` on this branch an
OVER-estimate -- deliberate, in the safe direction, and a pricing-schema
decision rather than a go-live blocker. (A14) is the throttling response.

**THE WARMUP DROPS `outputConfig` AND THAT IS ARGUED FROM THE CACHED CHAIN
RATHER THAN FROM TASTE.** Converse processes checkpoints `tools -> system ->
messages` and `outputConfig` is in none of them, so dropping it cannot change
the prefix -- a stronger statement than the OpenAI warmup can make about
`response_format`, which carries a VERIFY-AT-GO-LIVE for exactly that reason.
What it costs is stated: `structured-output.html` warns a first-time schema
"compiles the grammar, which may take up to a few minutes", and with the warmup
not carrying the schema the WAVE pays that compile, N requests at once.
`BEDROCK_ANTHROPIC_WARMUP_SEND_OUTPUT_CONFIG` turns it back on in one edit once
(A1)'s timing says whether it is real.

**THE PROBE IS EXTENDED AND WAS NOT RUN.** `--probe-per-trial` issues three
extra billed calls -- one warmup-shaped, two trial-shaped over the same
prefix -- and reads the answer out of the USAGE BLOCK, never the wall clock. It
also checks for free, before spending anything, that the two requests carry a
byte-identical `system` block. **`--per-trial-prefix-file` exists because the
probe's own built-in prefix is BELOW the 1,024-token floor**, so a zero cache
write with it is the documented behaviour of a short prefix and says nothing
about Stage 5; the probe says so on screen rather than letting a reader draw
the wrong conclusion.

**TWO DEFECTS IN THIS PASS'S OWN TEST CODE WERE FOUND BY RUNNING, NOT BY
READING.** A stray no-op plant raised at module level and took eleven checks
with it -- **the abort shape this project has now shipped fifteen times** -- so
`planted()` converts a plant that matched nothing into a recorded
`PLANT-FAILED` failure and a named absence, and the run still goes red while
every other plant still reports. And three counter assertions read
`dict(counter)` OUTSIDE the `counters_zeroed()` block that restores it, so they
compared `{}` against `{}` and would have passed for a node that recorded
nothing; the snapshots are taken inside now.

**ONE PINNED EXPECTATION MOVED AND IT IS THE CHECK WORKING.**
`tests/test_agent_stage5_per_trial_calls.py` 5c(j) pinned `_account_unconsumed`
at four call sites; the floor is the fifth, added because the fallback-writer
withholding made a paid-but-unread response reachable there. **The number that
5c(j) is really about did not move**: the fifth site is an ordinary statement
rather than a handler, so "exactly two chain their original diagnosis" is as
true as it was. Both the pin and the docstring it pins say so.

```bash
# The Converse per-trial pass. Same shape, same directory. NO AWS CALL AND NO
# BILLED CALL OF ANY KIND -- the Converse client is a stand-in installed
# through oncotriage/agent/deps.py and every response is a literal dict. No
# network, no keys, no spend, no live Qdrant, NO MODEL LOAD
# (ONCOTRIAGE_DEFER_LOCAL_MODELS above the imports; torch and transformers
# asserted absent at the end), no corpus, no git history, no database, no live
# server -- and NO boto3, asserted rather than assumed. It DRIVES THE REAL
# STAGE 5 NODE end to end, warmup and wave, through the real adapter. It DOES
# read the LIVE prompt renderer, in section 8 only, to re-derive the
# measurement that Bedrock's 1,024-token cache floor is unreachable for this
# pipeline. It writes NOTHING anywhere, not even a temp directory. NOT in the
# collision matrix -- but it DOES read oncotriage/config.py, which
# tests/test_config_snapshot_date_rot.py rewrites in place, so all FOUR files
# it reads are sha256-compared at the end (the fourth is batch/runner.py, read
# as TEXT and PARSED in section 4e so that "a resume can pick this patient up"
# is a measurement against the runner's own checkpoint predicate rather than a
# sentence). It DOES exec: nine in-memory copies
# (evaluation.py, bedrock_anthropic_adapter.py and config.py), one plant each,
# argued at _EXEC_ALLOWLIST. Bucket A, ~6 s.
python tests/test_agent_bedrock_anthropic_per_trial.py              # 196

# The per-trial go-live probe for the Converse branch. NOT RUN by this pass.
python bedrock_probe.py --i-understand-this-bills --provider bedrock_anthropic \
    --probe-per-trial                                    # + 3 more calls, A11 + A12
python bedrock_probe.py --i-understand-this-bills --provider bedrock_anthropic \
    --probe-per-trial --per-trial-prefix-file <a real rendered system prompt>
#   ^ THE BUILT-IN PREFIX IS BELOW THE 1,024-TOKEN FLOOR. A zero cache write
#     with it is documented behaviour and says nothing about Stage 5.
```

**WHAT IS NOT DONE, NAMED RATHER THAN LEFT TO BE DISCOVERED.**

1. **THE PROBE HAS NOT BEEN RUN.** Every A-item on this branch, old and new, is
   documentation until it is. (A11) and (A12) are the two that decide whether
   per-trial mode is usable here at all.
2. **THE CACHED READ IS NOT PRICED.** `get_model_cost()` takes an
   {input, output} pair; a cache hit therefore makes `estimated_cost_usd` on
   this branch an over-estimate by the gap between $3.00 and $0.30 per 1M on
   the cached portion, which on a per-trial patient is most of the input.
   Closing it re-bases every historical row and is its own pass. (A13).
3. **THE RESPONSES BRANCH STILL REFUSES PER-TRIAL MODE**, deliberately and
   untouched by this pass: that endpoint owns its own caching controls and its
   warmup would be a third request shape with a third set of unknowns.
4. **THERE ARE NO CONVERSE FIXTURES.** `capture.assert_provider_is_hookable()`
   admits OpenAI alone, so a capture or replay under this provider refuses by
   name rather than billing for calls no proxy wraps. The twelve fixtures
   characterize the OpenAI GROUPED arm and this branch has no characterization
   gate of its own.
5. **`AWS_NEW_RETRIES_2026` IS NOT SET.** AWS's retry-behavior page documents
   the numbers quoted above as requiring that opt-in, "without this setting,
   your SDK uses pre-2026 retry behavior, which differs in backoff timing,
   retry quota costs, and service-specific defaults". It is an environment
   decision and is recorded rather than made on anyone's behalf.
6. **`BEDROCK_REGION` IS STILL NOT GATED BY THE RESUME FINGERPRINT.** Two runs
   against the same profile id in different Regions remain indistinguishable to
   a resume gate. Unchanged by this pass and now one provider more reachable.

### The compose file holds no secret (the compose-secrets pass)

**`docker-compose.yml` WAS THE LAST BLOCKER TO THIS REPOSITORY GOING PUBLIC,
AND ONE OF THE THREE VALUES IT WAS BLOCKED ON WAS NEVER A CREDENTIAL.** The
identifier-cap pass made the file's hardcoded Airflow signing key visible to
this project's own secrets scanner and deliberately left it standing; this pass
removes it. **`python s3_stage.py` goes from 2 findings to 1**, the survivor
being `tests/test_tracking_mlflow_index.py`'s synthetic `_SENTINEL`, which is
pre-existing and untouched. `scan_bytes(docker-compose.yml)` returns `[]`, for
the opposite reason it used to.

**WHAT THE THREE VALUES ACTUALLY WERE, measured against Airflow 3.3.0 rather
than read off the comment above them.**

| the line | what the file said it was | what it is |
|---|---|---|
| `AIRFLOW__WEBSERVER__SECRET_KEY` -- a 56-character literal of letters, underscores and hyphens, no digit, ending in the words "change in production" | "a literal secret key" | correct, **and the variable name was deprecated**: Airflow 3.3.0 has no `[webserver]` section. It still reached `[api] secret_key` through deprecated-option forwarding, warning on every start |
| `AIRFLOW__CORE__FERNET_KEY: ""` | "an empty Fernet key" | correct, **and worse than unconfigured**: `airflow db migrate` GENERATES a key into `{AIRFLOW_HOME}/airflow.cfg` when none is set, and an environment variable beats airflow.cfg -- so the empty string was switching OFF encryption Airflow had already configured for itself |
| `AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_USERS: admin:admin` | "a hardcoded admin password" | **wrong. It is a `username:ROLE` list**, it is byte-identical to Airflow's own default, and it contains no password |

**THE VALUE IS DESCRIBED AND NOT QUOTED, AND THAT IS THIS PASS'S OWN FIRST
DEFECT -- TWICE.** The first draft reproduced the 56-character literal verbatim
in TWO places: the table above, and the compose file's own comment explaining
the removal. That is not removing a secret, it is moving it. **Only one of the
two was caught by the scanner**, and the difference is punctuation rather than
design: in CLAUDE.md the literal sat inside a markdown table cell that put a
`:` between the keyword and the value, matching `credential_assignment`; in the
compose comment the next character after `secret` was `_`, so the detector
never fired. A whole-tracked-tree sweep reported `CLAUDE.md /
credential_assignment` and said nothing about `docker-compose.yml`. **The
second occurrence was found by grepping for the literal, not by the scanner** --
so a clean scan is not the same as a clean tree, and the check that closed it
is `git grep` for the value returning nothing.
`oncotriage/staging/secrets_scan.py` and `tests/test_staging_exclusions.py` had
already settled on describing the value rather than reproducing it; both new
sites now match them, and no tracked file contains it.

**AND THE LITERAL IS STILL IN GIT HISTORY.** Removing it from HEAD does not
remove it from any commit that carried it, so the value must be treated as
compromised and never reused -- which is what it always was, since the repo is
about to be public. Nothing here rewrites history; that is a separate decision
with its own blast radius.

**THE HEADER'S OWN CORRECTION WAS THE DEFECT.** It read
`Airflow: http://localhost:8080 (admin / admin)` above a note explaining that
the original wording ("admin / check api-server logs for password")
"contradicted the file itself ... so the api-server generates no password and
there is nothing in its log to look for". **Both halves are false, and the
ORIGINAL was right.** `SimpleAuthManager.init()` writes a random 16-character
password per user into
`{AIRFLOW_HOME}/simple_auth_manager_passwords.json.generated` and prints it once
-- driven directly with exactly this configuration, and confirmed on the
project's own pre-existing `airflow-db` volume, which has carried that file
since 2026-08-07. **The decisive measurement is the negative control**: against
the running stack, `POST /auth/token` with the generated password returns
**201**, and with `admin`/`admin` returns **401**. The pair the header
advertised has never worked.

**SO THE USER LIST STAYS A LITERAL, AND `airflow_manager`'s TIER 4 NEEDED
NOTHING.** `oncotriage/orchestration/airflow_manager.py` reads that generated
file as the fourth of its four password tiers; driven against the live
container, `_get_password(airflow_home=...)` reports `password-file`, the
password authenticates (201), and `check_dag_status` lists
`trial_refresh_weekly` and its three tasks. The line is KEPT rather than
deleted -- deleting it changes nothing today, and written down it stops an
upstream change to Airflow's default silently changing who can log in.

**`${VAR:?message}`, AND THE COLON IS THE MECHANISM.** Compose's behaviour for
an unset variable with no default is to WARN and substitute the empty string,
exit 0 -- the silent-weak-value case. Four forms, measured against Compose
v5.4.0:

| form | state | result |
|---|---|---|
| `${VAR}` | unset | warning, substitutes `""`, **exit 0** |
| `${VAR?msg}` | empty | substitutes `""`, **exit 0** |
| `${VAR:?msg}` | unset | error naming VAR and msg, **exit 1** |
| `${VAR:?msg}` | empty | error naming VAR and msg, **exit 1** |

The `?` form without the colon treats an empty value as supplied, and empty is
exactly the weak default being removed, so only `:?` will do. Driven as a real
`docker compose up -d` with both unset: **exit 1, eight error lines naming both
variables, and ZERO containers created.**

**THE FILE COMPOSE READS IS NOT THE FILE THE APPLICATION READS, AND THIS WAS
PROVED RATHER THAN ASSUMED.** This project bind-mounts `../05- Keys/.env` to
`/app/.env` and `paths.load_env_keys()` opens it at runtime. **Compose never
touches it.** Compose interpolates `${...}` from its own environment plus a
`.env` in the PROJECT DIRECTORY -- `03- Code/.env`, beside the compose file.
Measured in a fabricated layout carrying both files: a variable defined only in
`../05- Keys/.env` interpolates to nothing, one in `03- Code/.env`
interpolates, and a shell export beats the file. Conflating the two is the trap
this item exists around.

**THREE ROUTES FOR AN OPERATOR, AND THE THIRD EXISTS BECAUSE OF `s3_stage.py`.**
`03- Code/.env` is persistent and costs a staging refusal -- **measured, 1
finding becomes 3** (the `dotenv_file` filename detector and a content hit), and
the exclusion manifest is not this pass's to edit. A shell export costs nothing
on disk and must be repeated per shell, **including for `docker compose down`**
-- interpolation runs for every subcommand, so a stack started from a shell that
had the variables cannot be stopped from one that does not.
`--env-file "../05- Keys/airflow-compose.env"` is persistent AND outside the
staged tree, because `05- Keys/` is already excluded; it costs the flag on every
command. A symlink from `03- Code/.env` into `05- Keys/` does **not** buy the
third: the walk still meets a readable file at that path.

**AND THERE IS DELIBERATELY NO `.env.example`.** It was written, and then
deleted: `scan_filename(".env.example")` returns `['dotenv_file']`, so a tracked
template adds a permanent third finding that only a manifest entry could clear
-- and a name chosen to slip past that detector would be a dotenv template
disguised from this project's own scanner. The compose file carries the
instructions instead.

**INTRODUCING A FERNET KEY BREAKS NO EXISTING DATA, AND THE HAZARD IS ROTATION
-- WHOSE FAILURE MODE IS SILENT.** `is_encrypted` is a per-ROW column, so a row
written under `_NullFernet` carries `is_encrypted = 0` and `get_password()`
returns it verbatim without consulting any key. Driven end to end against a real
Airflow 3.3.0 sqlite metadata database:

| step | result |
|---|---|
| store a connection under `FERNET_KEY=""` | `password` column plaintext, `is_encrypted` 0 |
| read it back under a NEW real key | **reads fine**, still plaintext, still 0 |
| add a connection under that key | `gAAAAAB...`, `is_encrypted` 1 |
| read THAT one under a DIFFERENT key | **"Connection not found."** -- an absence, not a decryption error |

**Nothing had to be migrated**: the project's generated DAG defines no
Connection and no Variable, and the live `airflow-db` volume was inspected and
holds **0 connections, 0 variables, 0 encrypted rows**. That stops being true
the first time somebody adds one, which is why the compose comment says to keep
the key.

**THE SIGNING KEY IS REQUIRED RATHER THAN OMITTED, and that was measured too.**
Airflow's default for `[api] secret_key` is the template `{SECRET_KEY}`, which
resolves to `b64encode(os.urandom(16))` -- **a new random value per process**;
two invocations against one AIRFLOW_HOME with no `airflow.cfg` produced two
different keys. It is persisted once `airflow db migrate` has written a cfg, so
the container would in practice get a stable key out of the volume -- but one
that dies with `down -v`, is shared with nobody and cannot be rotated. The
variable is renamed to `AIRFLOW__API__SECRET_KEY` in the same edit; measured
inside the running container, `[api] secret_key` carries the supplied value,
`AIRFLOW__WEBSERVER__SECRET_KEY` is absent from the environment, and the
api-server log carries **zero** "has been moved to" lines.

**VERIFIED BY RUNNING.** A real `make up` from the existing volumes: **all six
services healthy** (`qdrant`, `fastapi`, `streamlit`, `airflow-webserver`,
`airflow-dag-processor`, `airflow-scheduler`), `GET /health` 200, `/docs` 200,
`:8501` 200, `:6333/healthz` 200, and Airflow's own `/api/v2/monitor/health`
reporting metadatabase, scheduler and dag_processor all healthy. Inside the
container `get_fernet().is_encrypted` is **True** for the first time. Neither
compose variable name appears anywhere under `/app`, and `/app/.env` is still
the `05- Keys` bind mount -- nothing was baked into the image, which
`.dockerignore` already guaranteed with `.env` and `.env.*`.

**WHAT IS NOT DONE, NAMED RATHER THAN LEFT TO BE DISCOVERED.**

1. **`03- Code/.env` is not in the exclusion manifest**, so an operator using
   route (a) meets a staging refusal naming their own file. The manifest was
   out of scope for this pass. It is the top follow-up: one `excluded` entry
   for `03- Code/.env` with the reason, which is strictly safer than an
   allowlist row because a `.env` should never upload whatever its contents.
2. **No test pins any of this.** The compose file's secret-freedom is asserted
   by nothing -- `tests/test_staging_exclusions.py` deliberately does not read
   the real file, and its 4j block was written that way precisely so it would
   not go red on this fix. A check that `scan_bytes(docker-compose.yml) == []`
   and that both Airflow secrets are `${...:?}`-shaped is cheap and belongs in
   `tests/test_docker_qdrant_override_and_readiness.py`, which already parses
   the compose file.
3. **`AIRFLOW__CORE__EXECUTOR: SequentialExecutor` is silently overridden.**
   Airflow 3.3.0 emits `FutureWarning: The 'executor' setting in [core] has the
   old default value of 'SequentialExecutor'. This value has been changed to
   'LocalExecutor' in the running config` -- observed in the running container.
   The compose file configures one executor and the stack runs another. Found
   while measuring this item; not this item's to fix.
4. **`oncotriage/orchestration/airflow_setup.py` writes
   `simple_auth_manager_users` into a `[simple_auth_manager]` section**, and in
   Airflow 3 the option lives in `[core]`. It is a user:role list rather than a
   credential, so it is not a secrets defect, but it is a setting that reaches
   nothing. Local (non-container) path only.
5. **`docker compose config` prints the resolved secret**, as it printed the
   hardcoded one before. Inherent to compose; worth knowing before pasting that
   output anywhere.

### A credential cannot enter this repository unnoticed (the secret-gate pass)

**TWO LAYERS, AND ONLY ONE OF THEM IS A GUARANTEE.** `.githooks/pre-commit` is
convenience — bypassable with `--no-verify`, and absent until somebody runs
`make hooks`. The `secret-scan` job in `.github/workflows/ci.yml` is the gate.
Both run **one script over different ranges**, so a hook that passes is a true
preview of CI rather than a second opinion. **No billed call, no schema change,
no migration**; the production `inferences.db` sha256 is unchanged.

**THE BRIEF ASKED FOR A PUSH-RANGE OR DIFF SCAN AND THAT IS PROVABLY WRONG FOR
THE REQUIREMENT IT STATES.** Measured, in a clone of this repository at 202
commits, with a shape-faithful AWS key id and Hugging Face token committed as an
**evil merge** — content in NEITHER parent, which `git log -p` prints no diff
for:

| | verdict |
|---|---|
| `gitleaks git --log-opts="--all --full-history"` — the job this repository already shipped | **202 commits scanned, "no leaks found", exit 0** |
| `secret_scan_gate.py --range objects` | **exit 1**, 4 findings, both engines, naming the file |

A push-range scan is strictly weaker than the full-history scan that already
misses this, so **no range narrower than the object database can be defended**.
The object walk also has no "before", which is what makes it immune to a
force-push moving one.

**AND THE BRIEF'S TWO COST FIGURES WERE THE WRONG WAY ROUND.** Measured here:
`gitleaks git` full history reads **15.08 MB in 1.2 s**; the object walk reads
**128.89 MB in ~18 s**. The log scan reads DIFFS — added lines only — while this
reads every version of every file in full, so the object walk is ~8.5x MORE
bytes, not less. Eighteen seconds once per push is the price of a range with no
hole in it.

**THE RANGE IS `--batch-all-objects`, NOT `rev-list --objects --all`, AND THE
DIFFERENCE WAS MEASURED TWICE.** (i) After a `reset --hard` — the local half of
a force-push — the abandoned blob is **unreachable from every ref and still in
the object database and still `git cat-file`-able**; a reachable-only walk
reports NOT FOUND and this gate reports it. (ii) `rev-list --objects --all`
lists a blob under exactly **one** path, whichever it reaches first: a blob
committed at `a.txt`, `dup1.txt` and `.env` is listed once, so whether the
FILENAME layer ever sees `.env` would be decided by traversal order. Basenames
are therefore taken from **every tree object**, which finds all three.

**BLOBS ARE READ WHOLE, NOT TO `config.S3_STAGING_SCAN_PREFIX_BYTES`.** That
64 KiB bound is a stated LIMIT of the staging scanner, and applying it here
would open a hole this repository is measurably inside: **63,977,491 of the
128,894,276 bytes in this object database — 49.6% — lie beyond 64 KiB of their
blob.** Half the history would be unscanned.

**TWO ENGINES, AND THIS REPOSITORY IS THE COUNTEREXAMPLE TO EACH DIRECTION.**
Over the same 128.89 MB: **this project's scanner finds 15 findings gitleaks
finds ZERO of** — twelve historical `docker-compose.yml` blobs carrying the
56-character digitless Airflow signing key (it clears no entropy floor gitleaks
has) and three blobs of a test file carrying a synthetic `sk-` sentinel (no
gitleaks rule matches it; its `openai-api-key` requires the literal infix
`T3BlbkFJ`). gitleaks finds 6, all of them the two false positives
`.gitleaksignore` already argues about. Both engines' findings are normalised
onto **one** fingerprint and gated by **one** accepted table, so neither gets a
second invisible gate of its own.

**THE FINGERPRINT IS THE BLOB OID, WHICH IS STRICTLY BETTER THAN
`.gitleaksignore`'S COMMIT KEY.** `<blob-oid>:<engine>:<detector>:<locator>`.
A blob oid is the sha1 of the CONTENT, so an entry survives a rebase, a
filter-branch and a rename — the cost `.gitleaksignore`'s own header records for
its form — and **can never suppress different content**. Parsed with
`split(":", 3)`, because the locator is a BASENAME for a filename finding and a
basename may legally contain a colon.

**`.gitleaksignore` IS UNCHANGED AND HONOURED, AND IT IS NOT LOAD-BEARING AT
THIS PIN — MEASURED, AGAINST THE BRIEF'S OWN CLAIM.** The full-history scan
reports **zero findings with the file and zero without it**. The cause is
isolated: the same gitleaks v8.30.1, the same blob of
`oncotriage/storage/queries.py` — `gitleaks git` over the commit that ADDED the
line reports **0**, `gitleaks dir` over that exact blob reports **1**
(`generic-api-key`, line 505). **git mode under-reports against dir mode on
identical content**, which is a second, independent reason it is not the
guarantee. The file is kept: a rule tightening upstream can be reverted
upstream, and the entries are argued.

**GREEN DOES NOT MEAN CLEAN, AND THE SUMMARY SAYS SO IN WORDS.** 22 accepted in
four argued blocks, printed grouped by reason on every clean run (it was 21 in
three until the CI-green pass added BLOCK 4, a prompt-cache routing label in a
test whose blob is already in public history). **Twelve of
them are a REAL credential still in this repository's history** — the Airflow
signing key — accepted because removing it means rewriting pushed history, which
is a decision with its own blast radius and is not made by a scanner. It is
treated as compromised. **A stale accepted entry is exit 2** on
`audit_gate.py`'s precedent; **a scan that could not run is exit 3**, because
"I could not look" is not "I looked and it was clean".

**THE ACCEPTED TABLE IS NOT CALLED `secret-scan-accepted.txt`, AND THAT WAS
FOUND BY RUNNING THE GATE OVER A CLONE CONTAINING IT.** The FILENAME layer
matches `(^|[._\-])secrets?([._\-]|$)`, so the table was a finding of its own —
and the only fingerprint that could suppress it is keyed on that file's own blob
oid, **which changes every time an entry is added**. The suppression file would
have had to suppress itself and would have gone stale on every edit. Same shape
`.gitleaksignore`'s header records through CONTENT, arrived at through the NAME.
It is `.github/scan-accepted-fingerprints.txt`, which is the more accurate name
anyway: it holds fingerprints and it holds no secret.

**THE HOOK USES `core.hooksPath`, NOT `.git/hooks`, AND NOT THE `pre-commit`
FRAMEWORK.** `.git/hooks` is not tracked, so a hook copied there exists on one
machine and nobody can review it. `core.hooksPath` points git at a TRACKED
directory: `make hooks` is the one command, it is idempotent, and every clone
already has the file. The framework is the other tracked option and would add a
dependency and a managed virtualenv to a project whose `pyproject.toml` is
deliberately the ONE dependency list. **The cost is stated rather than
discovered**: `core.hooksPath` REPLACES the hook directory, so any other hook in
`.git/hooks` stops running while it is set. **Measured runtime: ~1.0 s** on
staged content with both engines.

**THE CI JOB INSTALLS FOUR PACKAGES, DERIVED RATHER THAN LISTED.** The gate uses
two PURE functions, but they live in a module that imports `oncotriage.config`
at module scope, and config imports three third-party packages there — so
importing the pure functions costs those imports and, without them, the gate
exits 3. **Measured by AST**: the closure reaches exactly four third-party tops
— `dotenv`, `httpx`, `openai`, `qdrant_client` — and no torch, transformers,
streamlit or langgraph. `--print-requirements` is their one owner and the
workflow installs from it; **section 10 of the standing test re-derives the
closure by AST** and fails when a new module-scope import appears in that chain,
so the install list cannot silently start installing too little. Verified in a
**bare venv**: `--print-requirements` works with nothing installed, the gate
without them exits **3** naming the missing module, and with them the exact CI
command is green in 19.1 s against a 131 MB environment.

**gitleaks IS INSTALLED FROM THE RELEASE TARBALL, PINNED BY sha256** rather than
pulled as `zricethezav/gitleaks:v8.30.1`. A docker tag is a name somebody can
move; this repository's Dockerfile already takes the opposite position by
pinning base images by digest. **One binary for both steps**, because a rule
renamed between two gitleaks builds changes every fingerprint it emits and this
repository now has two accepted tables keyed on rule names.

**SEVEN CONTROLS, ALL DRIVEN, ALL FIRING.** In a clone of this repository: the
baseline is clean; the hook REFUSES a staged plant and **creates no commit**;
`--no-verify` walks past it; the object gate then FAILS on the same content;
`git rm` + commit leaves a **working-tree scan reporting CLEAN while the gate
still fails**; the evil merge fails the gate and passes the log scan; the
force-push residue is unreachable, still present and still found; and expiring
the object returns the gate to **exit 0 with all 21 accepted entries still
matched**.

**THE PLANTS ARE SHAPE-FAITHFUL BECAUSE PLACEHOLDERS DO NOT WORK, AND THE
BRIEF'S CLAIM ABOUT THAT WAS HALF RIGHT.** Measured on all four:

| value | project scanner | gitleaks |
|---|---|---|
| `AKIA` + `FAKE`×4 | `aws_access_key_id` | nothing |
| `sk-` + `FAKE`×8 | `openai_anthropic_key` | nothing |
| `hf_` + `FAKE`×8 | `huggingface_token` | nothing |
| AWS's own documented example key | `aws_access_key_id` | nothing |

gitleaks carries an entropy floor on all three rules (3, 3, 2) and an explicit
`.+EXAMPLE$` allowlist on the AWS one; this project's nine detectors carry
neither, deliberately — they are a REFUSAL layer that errs toward stopping a
run, which is exactly why they caught the digitless Airflow key. So a
placeholder control exercises ONE engine while looking like it exercised two.
The three shapes were **read out of gitleaks v8.30.1's own
`config/gitleaks.toml`**, not guessed: AWS is base32 (`[A-Z2-7]`, no 0/1/8/9),
OpenAI needs the `T3BlbkFJ` infix at offset 23 and one of two exact lengths, and
the HF rule is `hf_` plus exactly 34 LETTERS.

**NOT ONE SECRET-SHAPED LITERAL IS IN A TRACKED FILE, AND THE GUARD CAUGHT ITS
OWN AUTHOR.** Every plant is assembled at run time from a prefix, an alphabet
and an index arithmetic. Section 8 of the standing test scans this file with the
scanner it tests **and greps every tracked file for each of the fifteen values
it can generate** — and its first draft carried AWS's documented example key as
a literal in check 1k, which 8a failed on. That is the rule this project already
paid for once, when the 56-character Airflow key was reproduced into two
documentation files while being removed from the code and only one of the two
was caught, because of a punctuation accident. **Verified at the end: 276 tracked
and new paths through both layers of the scanner, and the only finding anywhere
is the pre-existing sentinel in `tests/test_tracking_mlflow_index.py`**, which is
Block 2 of the accepted table.

**VERIFIED BY RUNNING.** CI bucket A **80 files, 0 failed, 0 not run** in 93.9 s;
`tests/test_package_invariants.py` **260/0/0**;
`.github/scripts/ci_test_buckets.py --check` consistent at 99 files;
`.github/scripts/static_checks.py` compiles 246 files;
`tests/test_trivyignore_staleness.py` **181**,
`tests/test_dockerignore_exclusions.py` **36**,
`tests/test_staging_exclusions.py` **148** — none moved. The `tests` and `image`
jobs are **structurally byte-identical** to HEAD (same JSON digest of each job),
and every top-level workflow key is unchanged.

**WHAT IS NOT DONE, NAMED RATHER THAN LEFT TO BE DISCOVERED.**

1. **The Airflow signing key is still in history.** Accepted, not removed. It
   must be treated as compromised and never reused. Removing it is a history
   rewrite whose blast radius includes every `.gitleaksignore` commit
   fingerprint and several tests that lift controls out of `git show`.
2. **`tests/test_tracking_mlflow_index.py`'s `sk-` sentinel is a literal**, so
   any edit to that file changes its blob and needs a new accepted fingerprint.
   The fix is to assemble it at run time, exactly as
   `tests/test_secret_scan_gate.py` does; that is a change to a test whose own
   verification is out of this pass's scope.
3. **`.gitleaksignore`'s two entries are dead at this pin** and nothing fails
   because of it. A staleness check for that file would be red today, so it is
   reported rather than added.
4. **GitHub serves unreachable commits by SHA.** A secret force-pushed away is
   still retrievable from GitHub even though a fresh clone will not carry it, so
   no CI-side scan of a fresh clone can see it. The remedy is rotation and
   GitHub support, not a scanner.
5. **The `tests` job does not install gitleaks**, so the standing test's
   two-engine half SKIPS there. It runs fully on a developer machine and the
   guarantee itself is enforced in the `secret-scan` job with
   `--require-gitleaks`.

### Every counter has a production reader (the counter-reader pass)

**`oncotriage/degradation.py` EXISTS BECAUSE SIXTEEN COUNTERS HAD NO READER,
AND NOTHING IT BUILT COULD SEE THE SEVENTEENTH.** That pass added the registry
and the run-end block; it added no check that would notice the NEXT counter
declared without one, and by this pass there were **nine** more — two of them
(`MARKDOWN_ESCAPE_DECODE_UNRESOLVED`, `ESCAPED_ENTITY_DECODE_UNRESOLVED`)
added by the pass immediately before it.

**WHY NO EXISTING CHECK COULD SEE THEM, and the obvious answer is wrong.**
`tests/test_package_invariants.py` check 2h reports a module-level name that is
declared and never READ — but `C[key] += 1` binds the `ast.Name` in **LOAD**
context (the Store sits on the enclosing `Subscript`), so every increment reads
as a read and a write-only counter satisfies 2h on its first use. Widening 2h
is the wrong fix: its subject is dead declarations and this one's is live
declarations with a dead audience.

**THE AUDIT, AND IT IS REPRODUCIBLE FROM `HEAD`.** Every module-level
`Counter()` in the package plus the one at the repository root, in **both
declaration forms** — `NAME = Counter()` and `NAME: Dict[str, int] = Counter()`
— classified by an AST load/store walk covering all three reference forms.
**48 in the package, 9 write-only, and test files are not readers**: both decode
counters had four test files reading them and still nothing an operator could
see.

| counter | was | now |
|---|---|---|
| `MARKDOWN_ESCAPE_DECODE_UNRESOLVED` | write-only | `_REGISTRY_SPEC` |
| `ESCAPED_ENTITY_DECODE_UNRESOLVED` | write-only | `_REGISTRY_SPEC` |
| `ASSESSMENT_COMPOSITION_ANOMALIES` | write-only | `_REGISTRY_SPEC` |
| `retrieval/indexer.py:CLEANUP_FAILURES` | write-only | `report_cleanup_failures()`, at the indexer |
| `ablation/study.py:CHECKPOINT_FAULTS` | write-only | `report_checkpoint_faults()`, at the study |
| `PROCEDURE_RENDER_COUNTS` | write-only | `_CENSUS_SPEC` |
| `TEMPORAL_RENDER_COUNTS` | write-only | `_CENSUS_SPEC` |
| `TEMPORAL_CONFLICT_RESOLVED_MARKERS` | write-only | `_CENSUS_SPEC` |
| `TEMPORAL_CONFLICT_ACTIVE_MARKERS` | write-only | `_CENSUS_SPEC` |

**THE EXCLUSIONS WERE TREATED AS RULINGS, WHICH IS WHY THERE ARE THREE ANSWERS
AND NOT ONE.** `degradation.py` excludes the indexer's eight (index-time;
importing the indexer would put a scrape module in every batch run's import
graph) and the ablation study's (importing it would drag the graph, the
fixtures and the thread pool into `25- Batch Runner.py`). Those two got readers
**at their own entry points**, which is what the exclusion asked for. And
`TEMPORAL_CONFLICT_RESOLVED_MARKERS` argues at its own declaration that it is
"an observation, not a degradation" — a run that flags three suspect rows
correctly must not report a degradation that did not happen.

**SO THE FOURTH ANSWER IS A SECOND REGISTRY.** `_CENSUS_SPEC`,
`census_snapshot()`, `census_report_lines()`, `print_census_report()` — a
separate block, printed **above** the degradation block in `print_summary`
(severity ascending, verdict last), stating in its own heading that its
contents are NOT degradations. `_copy_counter` is reused rather than
reimplemented: it took a threaded test to get right and a second copy is a
second thing to get wrong. **An exclusion from ONE report is not a licence to
have NO report.**

**THEY ARE DELIBERATELY NOT IN `run_metrics`.** `RUN_METRIC_CATEGORIES` is
CLOSED at two members, and the three `runs` queries plus the dashboard's Run
Health tab derive `health_record` from `counters_registered` /
`counters_nonzero`. A census row would need a third category those shipped
consumers do not know, or would inflate the field that separates "measured
clean" from "no health record".

**`TEMPORAL_RENDER_COUNTS` IS A MIXED COUNTER AND THAT DECIDED ITS HOME.** It
holds genuine degradation keys (`*_unreadable:*`, `*_after_reference` — the
second means the corpus outran `DATA_SNAPSHOT_DATE`) **and** `lab_stale`, which
its own declaration argues is not a degradation and argues for keeping in the
same Counter. Registering it whole would put a census key in the degradation
report; splitting it would overrule a ruling. It goes to the census entire.

**THE INDEXER'S READER RUNS HOWEVER THE BUILD ENDS, and that gap was found by
reading the code rather than by the brief.** `verify_collection` increments
`CLEANUP_FAILURES` under `compare_count:` and then **RAISES**, so a call at the
end of `main()` is skipped by exactly the build whose size floor did not run.
`cleanup_failures_reported()` is a context manager on `main()`'s outermost
`with` — **one line**, where the obvious `try`/`finally` needs the whole
staging/direct fork re-indented by four spaces across ~86 lines carrying many
multi-line f-strings, which is the operation that silently indented the
CONTINUATION LINES of two nested docstrings in the run-identity pass. Every
pre-existing string constant in the module was compared before and after: 908
→ 909, all 908 byte-identical, the one addition the new docstring.

**BOTH NEW READERS TAKE AN INJECTABLE `out`**, on `degradation.print_report`'s
argument: neither `main()` can be driven — one needs a scrape and a live
Qdrant, the other a paid Stage 5 call per patient — and a reader nothing can
exercise is how a reader comes to be wrong.

**TWO STALE CLAIMS IN `degradation.py`'s OWN DOCSTRING WERE FOUND BY THE
AUDIT.** Its exclusion list said the indexer had "eight" and named **seven** —
`CRITERIA_RENORMALIZED` was missing, so the one thing a reader would use that
list for would have reported a registered, read counter as unaccounted. And it
recorded `CLEANUP_FAILURES` as a reported finding, which this pass closed.

**`tests/test_degradation_counter_readers.py` — 138 checks, bucket A, ~3 s.**
Section 1 is the standing invariant: every module-level `Counter` in the
package **and at the repository root** is registered, census-registered, or in
a CLOSED exemption table that **names its production reader** — and that reader
is then checked by AST to contain a genuine READ, so an exemption whose reader
was deleted or renamed FAILS and the table cannot rot into a permission slip.
`CHECKPOINT_FAULTS` is handled outside the table because it is the one name
owned by TWO modules, and a table keyed by name cannot say "one is registered
and the other is exempt" — which is exactly the conflation the first version of
the audit script made, crediting the batch runner's reader to the study's
counter.

**TWENTY REVERTS, TWENTY CAUGHT, and four defects in this pass's own work were
found by running rather than by reading.** (i) Two reverts ABORTED the file
instead of failing it — a bare `_REGISTRY.pop(name)` raises `KeyError` exactly
when the registration under test has been removed, the abort shape this project
has now shipped ten times; `.pop(name, None)` plus a check is the fix. (ii) The
classifier carried an `ast.AugAssign` branch that was **unreachable**: `C[k] +=
1` gives the Subscript `ctx=Store`, measured, so the live branch already caught
it — found by deleting the branch and watching nothing fail. (iii) The control
covered only the dangerous direction of the subscript branch; scoring a read as
a WRITE is the safe direction (it under-counts readers, producing a false
alarm) and went uncaught because every exempted counter has several reads and
the check asks for one. (iv) The import-time disjointness guard could only be
exercised by breaking the module and then failing to import it, so it became
`assert_registries_disjoint(registry, census)` with both arguments overridable.

**VERIFIED BY RUNNING.** All 69 non-serial test files green at their documented
counts; `tests/test_package_invariants.py` **260/0/0** (check 2i is an
exact-equality decorator pin, so the new `@contextlib.contextmanager` had to be
DECLARED — the serial run caught it, which is that pin working);
`tests/run_serial_tests.py` **5/5** with `oncotriage/config.py` and
`oncotriage/registries/cancer_code_registry.py` confirmed restored;
`python fixture_replay.py` **12/12 clean, exit 0, with no recapture**; and the
production `inferences.db` sha256 **unchanged** — `ab1403e3…`. **No money was
spent, no schema changed and no migration was run.** Re-running the audit that
found the nine now reports **zero**.

### The Cancer Stage line states when the patient was staged (the staging-date pass)

**ONE MEASURED CHANGE.** The parser has carried the staging Observation's date
since mCODE support existed and the Stage 5 record has never printed it, so a
restaging criterion -- "restaged within the last 6 months", "documented
progression since last staging" -- was unanswerable from a record the pipeline
had already read, and a decades-old staging read exactly like a current one.
The line now carries that date and the elapsed interval every other dated
section carries. **NO BILLED CALL, no schema change, no migration, no
recapture**; the production `inferences.db` was never opened.

**MEASURED OVER ALL 1,000 CORPUS BUNDLES, BEFORE AND AFTER, THROUGH A `git
worktree` AT HEAD rather than through a re-derivation:**

| | |
|---|---|
| summaries changed | **295**, every one of them a `stage_group_observation` patient |
| summaries byte-identical | **705** = 688 with no stage + 16 `condition_display` + 1 `metastatic_keyword` |
| changed summaries whose STAGE LINE did not change | **0** |
| stage lines changed whose SUMMARY did not change | **0** |
| ordinals moved / sources moved | **0 / 0** -- so **Stage 4's filter is untouched**, which is the strongest form of that claim: the ordinal is the only thing it reads |
| `patient_data_hash` moved | **0** |
| pseudonyms moved | **0** |
| `TEMPORAL_RENDER_COUNTS` delta | **none** -- all 295 dates resolved and none postdates the snapshot |

**THE HASH CLAIM WAS VERIFIED FROM SOURCE BEFORE IT WAS RELIED ON, and it holds
for BOTH dated tiers.** `compute_patient_hash`'s `stage_obs` entry emits
`stage_display|date|loinc` and its `met` entry emits
`display|value|unit|date|metastasis_category`, so the date this line renders was
already hashed. The pseudonym is a function of that hash, so it cannot move
either. Both are measured at 0/1000 rather than argued.

**THE DATE BELONGS TO THE OBSERVATION THAT PRODUCED THE ORDINAL, AND THAT IS THE
WHOLE DESIGN.** `extract_patient_stage_with_source` returns a `PatientStage`
NamedTuple -- `ordinal`, `source`, `observation_date` -- and each tier attaches
the date of the record IT read, at the line it answers on. There is no place in
the function where a date can be attached to an ordinal a different record
produced. The plausible wrong version, "the most recent staging observation's
date", is wrong for two reachable records at once: a patient whose newest
stage-group observation has a display the regex cannot read and whose stage
came from an older one, and a patient whose stage came from the M tier while an
unparseable stage-group observation sits above it. **Both are planted in
`tests/test_agent_summary_cancer_stage.py` section 8 and both are caught, each
beside a CLEAN control driving the unmutated module through the identical
harness.**

**A NAMED TUPLE RATHER THAN A BARE 3-TUPLE**, because the second and third
members are both `Optional[str]`: a caller unpacking them the wrong way round
gets two plausible strings and a summary that states a date as a provenance.
It is still a tuple, so `== (None, None, None)` works.

**THE BRANCH IS ON THE TIER, NOT ON THE DATE.** `STAGE_SOURCES_OBSERVATION_
BACKED` is a closed, argued, non-empty PROPER subset of `STAGE_SOURCES` --
guarded at import, because a set grown to cover every tier makes the branch
unconditional and an emptied one deletes the clause from every line. The two
tests differ in the case that matters: an observation-backed stage whose
Observation carries no date is a stage whose date is MISSING, and a stage read
out of diagnosis text has no staging date to be missing. Branching on the date
collapses them, which is the fallback accident rather than a decision;
`_stage_date_clause` states the first as `staging date not recorded`. Planted
and caught.

**A CONDITION'S `onset_date` IS NOT A STAGING DATE**, and the two
diagnosis-text tiers return None deliberately rather than reaching for it: it
is when the DIAGNOSIS began, so a tier that fell back to it would answer
"staged within the last six months" with the date the cancer started. Both
fixtures in the test carry an onset and neither line mentions it. A control
plants the leak into the extractor and catches it there -- **and asserts that
the RENDER is still clean under it**, which is the defence in depth the
source-based branch buys, measured rather than assumed.

**FOUR RENDERED STATES, ALL DRIVEN:**

    Cancer Stage: Stage III (from a recorded stage group observation; staged 2024-01-01, 2 years before reference date)
    Cancer Stage: Stage III (from a recorded stage group observation; staged 2030-01-01)          <- present, cannot anchor an interval; COUNTED
    Cancer Stage: Stage III (from a recorded stage group observation; staging date not recorded)  <- observation-backed, undated
    Cancer Stage: Stage II (from diagnosis text)                                                  <- byte-identical to before this pass

**`TEMPORAL_KEY_STAGE_DATE` IS THIS LINE'S OWN KEY**, on the one-prefix-per-
rendered-field rule: an unreadable staging date and an unreadable ECOG date are
different data problems with different owners. An ORDINARY date moves no
counter, which is asserted -- a usable date is not a degradation.

**THE INTERVAL IS CAPPED AT THE RECORD'S OWN PRECISION, AND THE CORPUS CANNOT
EXERCISE THAT.** All 295 stage-group dates are day-precise, so the year- and
month-precision arms are driven with CONSTRUCTED records and that is stated
rather than hidden. The corpus DOES exercise the fine end: **6 patients were
staged within 365 days** and render exact day counts (38, 98, 223, 230, 243 and
344 days), which is the grade a restaging window is written in.

**AND THE STALENESS THE RULING PREDICTED IS LARGER THAN IT SAID.** Across the
295: **median 6,872 days -- 18.8 years -- minimum 38 days, maximum 97 years**;
15 within two years, 40 within five. The line the model used to be handed said
"Stage I (from a recorded stage group observation)" for a staging recorded in
1928.

**THE M TIER IS THE ANSWERING TIER FOR ZERO CORPUS PATIENTS**, consistent with
the record already in this file: all five cM1 patients also carry an agreeing
stage GROUP, so the tier above answers first. Its date path is therefore covered
by literals only, in `tests/test_extraction_stage_m_category.py` Test 1b and
`tests/test_agent_summary_cancer_stage.py` section 7.

**`_m_category_stage_with_date` IS THE ONE IMPLEMENTATION OF THE M RULE and
`_stage_from_m_category` IS A THIN DELEGATE OVER IT** -- the same shape
`extract_patient_stage` already has over `extract_patient_stage_with_source`,
and for the same reason: two walks of that list is a disagreement nothing would
notice. The delegate is kept because forty checks in
`tests/test_extraction_stage_m_category.py` compare it against 4 or None, and
reading an ordinal out of a tuple in every one of them would obscure the mapping
that file exists to pin. **It has no production caller, which is stated rather
than hidden**, and a control plants a delegate with its own body and shows the
two readings coming apart.

**`FINGERPRINT_VERSION` IS UNCHANGED AT 3 AND `PROMPT_VERSION` AT 1.9.0.** The
stamp's SHAPE did not move, so no v3 artifact refuses for a shape reason;
`llm_classifier_renderer_digest` DID move -- `43f24b3b…` -> `1b1ca3a4…`, with
`renderer_module_digests()` showing exactly `agent/patient.py` and
`extraction/stage.py` changed and the other four byte-identical -- so a resume
across this pass answers **FP_CHANGED naming that field**, which is correct: the
renderer genuinely changed. `PROMPT_VERSION` identifies the TEMPLATE, and the
template did not move; the renderer digest is the mechanical half that covers
exactly this, which is the de-identification pass's ruling applied again.

**THIS IS AN INPUT CHANGE AND NOTHING WAS RECAPTURED. ELEVEN OF THE TWELVE
FIXTURES ARE AFFECTED** -- every one whose patient's stage comes from a
stage-group observation, measured by resolving each fixture's stored
`patient_id` against the corpus: `ablation_bm25_only`,
`ablation_no_cross_encoder`, `ablation_vector_only`,
`llm_classifier_parse_retry_constructed`, `mcode_genomic_variant`,
`mesh_fallback_siteless_code`, `no_candidates_pediatric_age`, `normal_1`,
`normal_2`, `normal_3`, `truncation_split`. **`unknown_stage` is unaffected**:
it has no stage, so it renders the absence line unchanged. They were already
stale before this pass -- the de-identification pass took the replay to 1/12 and
the pre-diagnosis ECOG pass to 0/12 -- so what this adds is one more field
family to the standing recapture, not a new one.

**TEST COUNTS.** `tests/test_agent_summary_cancer_stage.py` **56 -> 113**
(section 7, the date; six new controls, all firing, plus their clean arms),
`tests/test_extraction_stage_m_category.py` **119 -> 134** (Test 1b and three
controls), `tests/test_agent_summary_temporal_tagging.py` **216 -> 224**. Every
other file reports exactly what it reported before.

**AND TWO PRE-EXISTING TESTS SAID SOMETHING THAT STOPPED BEING TRUE.**
`tests/test_extraction_stage_m_category.py`'s tier plant anchor went stale and
REPORTED `plant target absent` rather than silently planting nothing, which is
that file's own mechanism working for the second time. And
`tests/test_agent_summary_temporal_tagging.py` listed "cancer stage" among "the
dateless sections" and swept for dated lines with a `startswith("- ")` filter --
correct while every dated line was a bullet, and blind to a NAMED line the
moment one gained a date. **A structural check whose corpus silently covers less
does not fail; it reports fewer findings, which reads exactly like a clean
render.** The filter is gone, the count is pinned in both directions, the stage
member moved out of that group with the reason written down, and a staged
variant of that file's mixed patient now pins the whole line and is required to
DIFFER with the temporal doors shut.

**WHAT IS NOT DONE, NAMED RATHER THAN LEFT TO BE DISCOVERED.**

1. **The stage-group tier sorts LEXICALLY on a raw ISO datetime string**
   (`o.get('date') or '0000-00-00'`), and every corpus date carries a UTC
   offset. Two observations recorded at the same instant in different offsets
   sort by their local text rather than by their instant. Pre-existing and
   unchanged here -- it decides which observation answers, which this pass did
   not touch -- but it is now VISIBLE, because the date it picked is printed.
   **CLOSED by the observation-sort pass**, which replaced that key; the
   residual it could not close (same-instant stamps sharing a local day) is
   named there.
2. ~~**An undated stage-group observation sorts as the OLDEST** under that same
   key, so it can only answer when nothing else parses.~~ **THIS WAS WRONG AND
   THE OBSERVATION-SORT PASS MEASURED IT.** It is true only of a FALSY date,
   and `_parse_mcode_stage_observation` never produces one -- it emits the
   LITERAL STRING `'unknown'`, which is truthy, so the `or '0000-00-00'`
   fallback never fired and `'u'` outranked every digit. An undated observation
   sorted as the NEWEST and answered for the patient. Left struck through
   rather than deleted, because the claim was load-bearing: it is why nobody
   looked.
3. **`staging date not recorded` fires ZERO times on this corpus.** Every one of
   the 295 observation-backed patients carries a date, so that branch is covered
   by constructed records only.
4. **The precision cap likewise** -- the corpus is 100% day-precise.
5. **`_stage_from_m_category` has no production caller.** It is one reading of a
   rule with one implementation, kept for the test surface; deleting it is a
   decision about that file's forty checks.
6. **The M tier's date is the FIRST cM1 in list order**, not the most recent,
   because the tier does not sort. Stated at the function and pinned by a check.

### The allergy line states when the allergy was recorded (the allergy-onset pass)

**ONE MEASURED CHANGE, AND THE HASH IS THE POINT RATHER THAN A SIDE EFFECT.**
`oncotriage/fhir/parser.py` has carried `allergies[].onset_date` since allergies
were parsed at all and `_create_patient_summary` never printed it -- so "no
severe hypersensitivity reaction within the last 12 months", a real oncology
exclusion criterion, was unanswerable from a field the pipeline had already
read, and a stamp from 1930 reached the model looking exactly like one from last
month. **NO BILLED CALL**; the production `inferences.db` was never opened and
no fixture was re-captured.

**THE RULING, BOTH HALVES.** APPROVED: the raw date on the allergy line.
REJECTED, BY MEASUREMENT: an elapsed-interval phrase. **Measured over the whole
1,000-bundle corpus, twice and from both ends: 471 of 471 `AllergyIntolerance`
resources take the `recordedDate` arm of `_parse_allergy`'s fallback chain --
not one carries an `onsetDateTime` or an `onsetPeriod` -- and the resulting
stamps run 19.1 to 99.1 years old, median 73.1, with NONE under five years.**
So an interval would be a near-constant restated on every allergy of every
patient. **The rejection is recorded IN THE CODE**, at the render site, because
an undocumented deliberate absence reads as a section somebody forgot.

    - Penicillin | medication | criticality: high | onset: 1979-06-06

**THE HASH HAD TO MOVE WITH IT, AND THE OLD EXCLUSION'S ARGUMENT IS WHAT SAYS
SO.** `compute_patient_hash` excluded `onset_date` under a comment reading "no
consumer reads it, so two allergies identical except for onset produce
byte-identical prompt text, and hashing it would move the hash without moving
the prompt -- the value_shape mistake". Correct while it held. Rendering the
date CREATES the consumer and reverses it exactly: leave it out and two patients
differing only in allergy onset render two different prompts under ONE hash,
which is the promise that function's own docstring makes.

**THE RAW FIELD IS HASHED, NOT THE `[:10]` SLICE THE LINE RENDERS**, matching
every sibling dated entry -- `proc`, `met`, `obs` and the stage observations all
hash a raw date beside a section that renders `date[:10]`. **THE COST IS STATED
AND PINNED RATHER THAN GLOSSED**: a re-serialisation that rewrites only a
stamp's time-of-day or UTC offset moves the hash, and therefore the `Patient:`
pseudonym, while no clinical line moves. Accepted because four sibling entries
already accept it and one collection hashing dates differently from the rest is
worse to have to remember.

**RECONCILED ACROSS ALL 1,000 BUNDLES, BEFORE AND AFTER, THROUGH A `git
worktree` AT HEAD:**

| | |
|---|---|
| summaries changed | **131** |
| hashes moved | **131** |
| allergy-bearing patients | **131** |
| patients with >= 1 DATED allergy | **131** (every allergy in the corpus is dated) |
| total allergy records | **471** |
| the other **869** patients | summary AND hash **byte-unchanged** |
| lines moved in a changed summary | the allergy lines, **and the `Patient:` pseudonym** -- which is DERIVED from the hash and therefore MUST move. Measured: with the allergy section and that one line removed, **0 of 1,000** summaries differ |

**TWO OF THE TWELVE FIXTURES ARE AFFECTED, established from each fixture's OWN
stored `patient_data` rather than by a corpus proxy**: `truncation_split` (4
dated allergies) and `unknown_stage` (3). **The other ten carry no allergies at
all**, so their summaries and their hashes are byte-unchanged and their recorded
`patient_data_hash` still matches. They were already stale before this pass --
the de-identification pass took the replay to 1/12 and the pre-diagnosis ECOG
pass to 0/12 -- so this adds one field family to a recapture already owed.

**`PROMPT_VERSION` IS UNCHANGED AT 1.9.0 AND `FINGERPRINT_VERSION` AT 3.** The
TEMPLATE did not move, only the record interpolated into it, which is the
staging-date pass's ruling applied again; the mechanical half is
`llm_classifier_renderer_digest`, and **exactly one hashed module moved --
`agent/patient.py`, the one that was edited** (`67e92481a6c0` -> `e2a63f3a057a`,
the other five byte-identical). A v3-stamped artifact therefore answers
FP_CHANGED naming that field, which is correct.

**THREE PINS INVERTED, AND THE BRIEF NAMED TWO.**

| pin | was | is |
|---|---|---|
| `tests/test_agent_patient_hash_coverage.py` 3a | `allergies.onset_date is NOT hashed (nothing downstream reads it)` | moved into the hashed loop, with the parenthesis's reversal argued in place. **+3a-ii**, which pins the raw/sliced split by perturbing ONLY what falls after the tenth character of the fixture's own stamp -- DERIVED from the fixture, because a literal typed beside it stops being that value the moment somebody edits it. **71 -> 73** |
| `tests/test_agent_summary_temporal_tagging.py` | `"2001" in <the Allergies section>` pinned at **False** | the WHOLE line pinned, plus "no elapsed phrase anywhere in the section". A substring test would have been satisfied by a date with an interval glued to it, which is the one thing the ruling forbids |
| **the same file's structural sweep** -- NOT in the brief and the load-bearing one | `every date-bearing line also states an interval`, an absolute invariant over the whole summary | PARTITIONED BY NAME. Everything OUTSIDE Allergies must state an interval (so a new section rendering a bare date still fails); everything INSIDE it must state NONE (so the exception cannot quietly grow one); each half with its own non-degeneracy count, because an empty partition satisfies either for free. Deleting the sweep, or widening it to "an interval OR a bare date", would have made it vacuous -- the second form is satisfied by every line it was written to catch. **224 -> 229** |

**AND TWO PROSE CLAIMS THE CHANGE MADE FALSE WERE CORRECTED RATHER THAN LEFT.**
The temporal doctrine block at the top of `oncotriage/agent/patient.py` read "the
rule is now **uniform**: WHEREVER THIS RENDERER PRINTS A DATE, IT PRINTS THE
ELAPSED TIME BESIDE IT"; the word "uniform" came out and the exception is named
under it. `tests/test_agent_summary_temporal_tagging.py`'s own docstring listed
Allergies among the sections that "render NO date and therefore gain NOTHING".

**THE LABEL IS `onset`, AND `recorded` WAS REJECTED FOR THE REASON IT LOOKED
RIGHT.** The parser collapses `onsetDateTime -> onsetPeriod.start ->
recordedDate -> "unknown"` into one string, so the renderer cannot tell a true
onset from a recording stamp. `recorded:` is right ONLY because 471 of 471
Synthea allergies take the recordedDate arm; a real EHR extract carrying an
`onsetDateTime` would then be labelled with a word that is false of it. **That
is writing today's data into the code** -- the argument the scrape-admission
pass used to DELETE an age filter rather than widen it to the cohort then in
hand. `ALLERGY_ONSET_LABEL` is a SEPARATE constant from `ONSET_CLAUSE_PREFIX`
even though the two spell the same word: that one heads a clause naming an
interval, this one labels a bare pipe-joined field, and sharing it would let a
change to the condition wording silently move an allergy line.

**ABSENCE FOLLOWS THE LINE'S OWN CONVENTION, NOT THE PARENTHESISED SECTIONS'.**
A missing value contributes NO part -- never the `date unknown` those sections
render -- so an allergy without a usable onset renders byte-identically to how
it rendered before this field was printed at all. The parser's literal
`"unknown"` is guarded and can never reach the line as text. All four spellings
(`"unknown"`, `""`, `None`, key absent) are driven.

**ONE ASYMMETRY IS RECORDED AND NOT FIXED, and the first draft of a check
asserted the opposite and failed on it, correctly.** The hash emits
`a.get('onset_date') or ''`, so the literal `"unknown"` hashes as `"unknown"`
while empty / None / absent hash as `""` -- four records that render identically
do not all hash identically. Not fixed, for three reasons together: it is the
SAME converse violation section 5 already accepts with a much larger reach;
every sibling dated entry has it; and **it is unreachable in production** --
`_parse_allergy` always writes the key as a stamp or as `"unknown"`, and
measured over the corpus all 471 records are dated and none is `"unknown"`. A
normalisation would be untested-in-production machinery guarding a state the
parser cannot produce.

```bash
# The allergy-onset pass. Same shape, same directory. No network, no keys, NO
# SPEND, no live Qdrant, no model load, no corpus, no database, no git history,
# no live server -- every patient is a literal dict and the registries
# _create_patient_summary resolves read no data file for these records. It
# writes NOTHING anywhere, not even a temp directory. NOT in the collision
# matrix: the one repository file it reads, oncotriage/agent/patient.py, is
# written by neither of the suite's two writers and is sha256-compared at the
# end. It DOES exec: two in-memory copies of that file, one plant each, argued
# at _EXEC_ALLOWLIST -- `git show` can supply neither, and cannot supply the
# first even in principle, because it needs a tree where the RENDERER prints
# the onset and the HASH ignores it and no revision has ever been in that
# state. Bucket A, ~1.2 s against ONLY the CI directory skeleton.
python tests/test_agent_summary_allergy_onset.py                    #  36
```

**FIVE REVERTS, FIVE CAUGHT**, each into a `copytree`'d copy with `PYTHONPATH`
pointed at it, a `sitecustomize` that strips the editable install's MetaPathFinder
(which otherwise beats `PYTHONPATH`), a realpath preflight asserting the COPY is
what imports, `PYTHONDONTWRITEBYTECODE=1`, and every plant asserting its own
occurrence count so a plant that matched nothing is a named PLANT-FAILED rather
than a working check reported as broken.

| revert | caught by |
|---|---|
| the hash drops `onset_date` while the renderer still prints it -- **THE COUPLING DEFECT** | allergy-onset (7 failures), hash-coverage (2) |
| the renderer drops the onset part while the hash keeps it | allergy-onset (8), temporal-tagging (5) |
| the `"unknown"` guard removed -- the placeholder reaches the line | allergy-onset (6) **only** |
| the `[:10]` slice dropped -- the raw stamp rendered | allergy-onset (5) **only** |
| the onset part prepended instead of appended | allergy-onset (6), temporal-tagging (2) |

**THREE DEFECTS IN THIS PASS'S OWN TEST CODE WERE FOUND BY RUNNING, NOT BY
READING.** (i) Three checks compared whole SUMMARIES where they meant clinical
text, and failed -- correctly -- because the pseudonym moves with the hash; the
mirror control in particular reported a WORKING plant as broken, because
"the prompts differ" is trivially true for any hashed field. (ii) A
non-degeneracy check was written `len(_COUNTS_BEFORE) >= 0`, a tautology, and
the probe behind it used a well-formed date -- `_resolve_temporal_date` counts
ONLY on its unreadable and after-reference branches, so over a resolvable date
the check could not have told a renderer that computes an interval from one that
does not. It drives an UNPARSEABLE onset now, with an unparseable PROCEDURE date
as the control that the registry moves at all. (iii) The raw/sliced perturbation
was a literal beside the fixture rather than derived from it.

**WHAT IS NOT DONE, NAMED RATHER THAN LEFT TO BE DISCOVERED.**

1. **The twelve fixtures are stale and 0/12 replay clean.** Ten were already
   stale; this pass adds two. The recapture is one paid `python
   fixture_capture.py` run and remains the standing item.
2. **An UNPARSEABLE onset renders verbatim** (sliced to ten characters). This
   section prints the field and derives nothing from it, which is the same
   treatment `criticality` gets and identical to every other dated section's
   `date[:10]`. It fires zero times on this corpus and is pinned as a decision.
3. **The "unknown"/absent hash asymmetry** (above).
4. **No interval, ever, for this field** -- so a criterion phrased purely as a
   window ("within the last 12 months") still needs the model to do the
   arithmetic this renderer does for every other date. That is the ruling, and
   it is right on THIS corpus; a real EHR extract carrying recent
   `onsetDateTime` values would make it worth revisiting, and the measurement
   that would settle it is the one recorded at the section.
5. **THE PARSER DISCARDS WHICH ARM OF ITS FALLBACK CHAIN ANSWERED, AND THAT IS
   THE PRINCIPLED FIX THE LABEL ARGUMENT WORKS AROUND.** `_parse_allergy`
   collapses `onsetDateTime` / `onsetPeriod.start` / `recordedDate` into one
   string, so neither this renderer nor the model can tell a true onset from a
   recording stamp -- and the choice between an `onset:` label that is wrong on
   this corpus and a `recorded:` label that would be wrong on a real extract is
   forced only by that collapse. An `onset_date_source` field beside it
   (`"onset" | "recorded" | "unknown"`) is a fact the SOURCE already carries
   and the parser throws away, and with it the line could say what it means on
   any corpus. It is out of this pass's scope -- a new parsed field, a decision
   about whether it is hashed, and a fixture impact of its own -- and it is the
   right end state.
6. **The corpus reconciliation is out of band.** The 131 / 131 / 471 numbers
   above were measured against a `git worktree` at HEAD and are recorded here,
   not pinned by a test. Pinning them would make the new file corpus-dependent
   and therefore bucket E, which is a real trade against a ~1.2 s bucket-A
   file; `tests/test_agent_patient_hash_coverage.py` already carries the
   corpus sections that would host such a check if it is wanted.

### Tier 0 stopped picking the winner by spelling (the observation-sort pass)

**ONE MEASURED CHANGE, AND IT MOVES NOTHING ON THIS CORPUS.** Tier 0 of
`extract_patient_stage_with_source` walks a patient's stage-group Observations
most-recent-first and answers on the first whose display parses; which one that
is decides the ordinal Stage 4 filters on and, since the staging-date pass, the
date printed into the Stage 5 prompt. It sorted on the RAW STRING --
`key=lambda o: o.get('date') or '0000-00-00', reverse=True` -- and that is
wrong in three ways.

**THE LIVE ONE IS THE PLACEHOLDER, AND THE PARSER IS WHY.**
`oncotriage/fhir/parser.py:_parse_mcode_stage_observation` emits the **literal
string `'unknown'`** when an Observation carries neither `effectiveDateTime`
nor `effectivePeriod.start` -- never `None`, never `''` -- so the
`or '0000-00-00'` fallback never fired for it, and `'u'` is greater than every
digit. **An UNDATED observation therefore outranked every dated one**, answered
for the patient, and rendered as "staging date not recorded" while a real dated
staging sat unused below it. Nothing raised: `_stage_date_clause` guards
`'unknown'` at the render site, so the only symptom was a stage read off the
wrong record and a date the prompt declined to state.

**SAY WHAT THAT IS AND IS NOT, because the two halves are different claims.**
The arm is REACHABLE BY ORDINARY INPUT -- the parser produces that exact value
and nothing between it and the sort guards it -- and it is UNEXERCISED BY THIS
CORPUS: measured over all 1,000 bundles, **0 of 585 stage-group Observations
carry it and 0 carry a falsy date**. The fix moves nothing today and removes a
defect that fires the first time a bundle omits a staging effective date, which
is a shape a real EHR extract produces and Synthea does not.

**CLAUDE.md'S OWN NOTE ON THIS WAS WRONG, in the direction that hides the
defect.** The staging-date pass recorded "An undated stage-group observation
sorts as the OLDEST under that same key, so it can only answer when nothing
else parses." That is true only of a FALSY date, and the parser never produces
one. Corrected in the follow-up list below.

**THE OTHER TWO.** A full ISO datetime carrying an offset was compared as local
TEXT with no stated semantic -- every one of this corpus's 585 stamps is such a
datetime and not one is a bare `YYYY-MM-DD`. And it disagreed with the
project's own convention: `OncologyLabRegistry._date_sort_key`, which orders
the labs, genomic variants and procedures of the SAME rendered summary, slices
to the day prefix and maps BOTH missing and `'unknown'` to oldest.

**WHAT THE `[:10]` SLICE DOES AND DOES NOT FIX, stated because it is easy to
overclaim.** It makes the PRIMARY comparison a question with a defined answer
-- the LOCAL CALENDAR DAY the staging was recorded on, which is the unit a
restaging criterion is written in and the unit the renderer prints. It does NOT
make two same-instant stamps in different offsets compare EQUAL, and nothing at
day granularity could: they can fall on different local days. **And for
well-formed ISO stamps it changes NO ordering at all** -- day-prefix and
whole-string comparison agree whenever two stamps differ inside their first ten
characters and tie together whenever they do not -- so the slice's entire
behavioural content is the placeholder mapping plus agreement with the
convention. Section 2 of the test MEASURES that rather than asserting it.

**A LOCAL TWIN OF `_date_sort_key`, NOT AN IMPORT, AND IT IS A LAYERING RULING.**
`oncotriage/extraction/` is a leaf -- `stage.py` imports `oncotriage.constants`
and `extraction.negation` and nothing else -- and it is read by the INDEXER as
well as by the agent, because the same module extracts a TRIAL's stage
requirements. Importing `registries.cancer_code_registry` here would invert the
direction (a registry is built on top of extracted facts, not underneath them),
put a 1,400-line patient-side cancer registry in the import graph of every
trial-side stage extraction, and reach across a package boundary for a name the
registry declares PRIVATE -- making it public by use while leaving it named
private. Promoting it to a neutral module is a RELOCATION with its own
equivalence proof, and folding a relocation into a fix is what makes an
equivalence proof stop meaning anything. **The drift that buys is closed by
measurement**: section 3 pins the two as answering identically over a shared
corpus of inputs, with a check that they are two different function objects so
the pin is not one function compared with itself.

**THE TIE-BREAK IS THE ECOG KEY'S SHAPE WITH ONE TERM DELIBERATELY REVERSED,
AND THE CORPUS IS WHAT DECIDED IT.** The key is `(day, raw stamp, -index)`,
sorted descending. The raw stamp participates as a DETERMINISM DEVICE rather
than a chronology claim: two stamps sharing a day differ only in time-of-day
and offset, and comparing them as text puts the later wall-clock time first
when the offsets agree, which is every stamp here. It is preferred over going
straight to the position because the raw stamp is a property of the RECORD
while the position is a property of how the parser walked the bundle -- with
it, re-ordering a bundle whose contents are unchanged yields the same winner.

**THE POSITION IS NEGATED, WHICH IS THE OPPOSITE OF `_select_ecog_performance_
status`'s, AND THAT WAS MEASURED RATHER THAN CHOSEN.** The first
implementation used the plain index, i.e. ECOG's "last in the bundle wins" --
and the corpus run reported **290 patients' winning observation changing** with
0 ordinals, 0 dates and 0 rendered clauses moving. The cause: **290 of 1,000
patients carry exactly TWO stage-group Observations with BYTE-IDENTICAL
stamps** -- Synthea emits one staging event twice, as "American Joint Committee
on Cancer stage IA (qualifier value)" and as "Stage 1 (qualifier value)" -- so
day and raw stamp both tie and the position decides for 29% of the cohort. The
raw-string sort this replaces put the FIRST one first, because Python's sort is
stable. Both records resolve to the same ordinal and carry the same date, so
nothing observable moved either way, **which is exactly why flipping them would
be gratuitous**: a fix moves what the defect moved and nothing else. The
negation preserves the shipped answer. `-index` also makes every key unique, so
the ordering is TOTAL and the determinism is a property of the key rather than
of sort stability -- a library guarantee a reader has to go and look up.

**MEASURED OVER ALL 1,000 BUNDLES, BEFORE AND AFTER, THROUGH A `git worktree`
AT HEAD rather than through a re-derivation:**

| | |
|---|---|
| winning observation changed | **0** |
| ordinal changed | **0** |
| source changed | **0** |
| observation_date changed | **0** |
| rendered stage clause changed | **0** |
| full sorted order changed | **0** |

The harness reports the ANSWERING OBSERVATION INDEX, which the shipped function
does not return, through a replica of each arm's Tier-0 walk -- and the replica
is trusted only after being shown to reproduce the SHIPPED function's
`(ordinal, source, date)` for **all 1,000 patients in both arms, 0
disagreements**.

**AND THE PROMPT AND THE HASH WERE COMPARED DIRECTLY, not inferred from the
clause.** 120 patients re-parsed and re-rendered in each arm -- 70 of them from
the duplicate-stamp class, plus all 5 single-observation patients and 45 with
none: **0 summaries differ by sha256, 0 `patient_data_hash` differ, 0 Cancer
Stage lines differ**, against 120 distinct summaries and 120 distinct hashes.
The hash was additionally proved unreachable STRUCTURALLY: `compute_patient_
hash` calls no stage extractor at all (AST over its call set), and it emits
stage observations through `_emit`, which SORTS -- so observation order cannot
reach it by construction.

**NO FIXTURE IS AFFECTED, established from each fixture's own stored
`identity.source_bundle` rather than by a corpus proxy.** All twelve resolve to
a cohort bundle; **eleven carry exactly the duplicate-stamp pair** and
`unknown_stage` carries none; **0 moved**. None of the four constructed
fixtures appends a stage Observation (each `construction.what_was_changed` was
read). Had the plain index shipped, eleven of the twelve would have had their
answering record flipped -- to no output change, but perturbing the very
records the fixture gate characterizes. `PROMPT_VERSION` is unchanged at 1.9.0
and `FINGERPRINT_VERSION` at 3; `extraction/stage.py` is in `RENDERER_MODULES`,
so `llm_classifier_renderer_digest` moves and a v3-stamped artifact answers
FP_CHANGED naming that field -- correct, since the renderer's input derivation
genuinely changed, even though no rendered byte does on this corpus.

**THE M TIER WAS EXAMINED AND NOT TOUCHED, as instructed, and the examination
is recorded as a check rather than as a sentence.** It does not SORT -- its
rule is "any cM1 anywhere answers" -- so no ordering exists there for a
placeholder to corrupt; what `'unknown'` can reach is the returned DATE, which
the render site guards. Checks 6e-6g pin both halves, so adding a sort to that
tier has to be a decision rather than an oversight.

```bash
# The observation-sort pass. Same shape, same directory. No network, no keys,
# NO SPEND, no live Qdrant, no model load, no database, no git history, no live
# server, no corpus -- every fixture is a literal dict. It is the FASTEST file
# in the suite because it imports neither oncotriage.paths nor any heavy
# library: the two modules it needs (extraction.stage,
# registries.cancer_code_registry) reach only constants and settings, so no
# glob fires and no registry is built. It writes NOTHING anywhere, not even a
# temp directory. NOT in the collision matrix: the two repository files it
# reads are written by neither of the suite's two writers and are
# sha256-compared at the end, with a non-degeneracy probe so that comparison
# cannot be one file hashed twice. It DOES exec: in-memory copies of stage.py,
# one plant each, argued at _EXEC_ALLOWLIST. Bucket A, ~0.05 s (MEASURED
# against ONLY the provisioned CI skeleton).
python tests/test_extraction_stage_observation_sort.py              #  57
```

**FIVE TREE-LEVEL REVERTS, FIVE CAUGHT**, each into a `copytree`'d copy with
`PYTHONPATH` pointed at it, a `sitecustomize` stripping the editable install's
MetaPathFinder (which otherwise beats `PYTHONPATH`), a realpath preflight
asserting the COPY is what imports, and `PYTHONDONTWRITEBYTECODE=1`: the whole
Tier-0 sort reverted to HEAD's raw-string form (7 recorded failures), the day
term reverted to the raw stamp (10), the placeholder mapping dropped (16), the
position left un-negated (9) and the raw-stamp term dropped (7). Both files
byte-identical afterwards.

**AND THE HARNESS FOUND THE ABORT SHAPE IN THIS PASS'S OWN TEST, WHICH READING
DID NOT.** The placeholder revert made `_date_sort_key(None)` raise inside a
`check()` ARGUMENT LIST, so the file reported **one traceback where it owed a
summary and 57 results**. **The sixteenth time this project has shipped that
shape.** The file already carried a `guarded()` helper and used it only inside
`_control`; every raise-capable expression in every section goes through it
now, and the same revert reports 41 passed / 16 failed and runs to its summary.

**TWO MORE DEFECTS IN THIS PASS'S OWN TEST, found by re-reading it after it was
green.** Check 4k's label claimed "a newer unreadable observation is skipped"
while its fixture put a READABLE newest record first, so it passed on a
condition it never created; it drives three observations now, the newest
unreadable, and asserts the middle one answers. And check 6b was labelled a
pure non-degeneracy probe while being an exact-equality pin on the COMPLETE set
of a leaf module's intra-package imports -- which fails on any added edge; it
is labelled as both, deliberately.

**WHAT IS NOT DONE, NAMED RATHER THAN LEFT TO BE DISCOVERED.**

1. **THE DUPLICATE-STAMP WINNER IS STILL BUNDLE-ORDER DEPENDENT**, and this
   pass chose which arbitrary answer to keep rather than removing the
   arbitrariness. Two Observations carrying byte-identical stamps and different
   stage displays have no principled winner; 5b/5c pin that the first in the
   list answers and that reversing the list reverses the answer. Nothing in
   this pipeline re-orders a bundle, so it is deterministic where it matters.
   The principled fix is upstream -- the parser emitting one record per staging
   EVENT rather than one per coding -- and is a parser change with its own
   fixture impact.
2. **SAME-INSTANT STAMPS IN DIFFERENT OFFSETS STILL ORDER BY SPELLING** when
   they share a local day. Resolving that needs a second, finer date convention
   than the `[:10]` day this project compares everywhere else, and introducing
   one here would make this collection order differently from every other
   section of the same summary. Argued at the key rather than left to be found.
3. **`_date_sort_key` NOW EXISTS TWICE**, in `oncotriage/extraction/stage.py`
   and on `OncologyLabRegistry`. That is the layering ruling's price, and it is
   PINNED rather than merely duplicated (section 3). The end state is one owner
   in a neutral module -- `oncotriage/constants.py` imports nothing at all and
   is the obvious candidate, though it holds facts rather than functions
   today -- reached by a relocation pass with its own equivalence proof.
4. **NEITHER COPY COERCES A NON-STRING DATE**, so both raise on one. That is
   deliberate and symmetric -- making one tolerant of a shape the other rejects
   is the drift the twin exists to avoid -- and no parser in this project
   produces one. Check 3e pins the shared limit as shared.
5. **THE STALE CLAIM IN THIS FILE IS CORRECTED ABOVE BUT ITS SOURCE IS NOT
   PINNED.** Nothing would catch the next prose claim about this ordering going
   stale; the checks pin the CODE, and the note that was wrong was prose.
6. **`tests/test_staging_exclusions.py` CHECK 7a FAILS, AND IT IS
   PRE-EXISTING** -- reproduced identically in a clean `git worktree` at HEAD
   with none of this pass's changes. `oncotriage/staging/exclusions.py:250`
   does `e["path"] for e in manifest["staged"]` and the SHIPPED manifest's
   `staged` list holds STRINGS, so it raises `TypeError: string indices must be
   integers`. That is the one bucket-A failure on this machine and it is not
   this pass's; it is reported rather than fixed because the manifest is
   another item's subject.

### Every non-evaluation states why (the reason-coverage pass)

**ELEVEN CODE PATHS CAN RECORD A TRIAL AS `not_evaluable` AND SIX OF THEM LEFT
THE COLUMN NULL.** `trial_matches.not_evaluable_reason` is what a campaign is
asked when a trial was not evaluated, and the enumeration was done from source
before anything was edited:

| # | path | reason | was it stamped |
|---|---|---|---|
| 1 | Stage 5, one trial sent alone still over the ceiling | `truncation_floor` | yes |
| 2 | Stage 5, split budget exhausted | `truncation_split_budget_exhausted` | yes |
| 3 | Stage 5 reconciliation, no entry came back | `omitted_from_model_response` | yes |
| 4 | Stage 5, the model answered twice and disagreed | `conflicting_duplicate_answers` | yes |
| 5 | Stage 5 per-trial, the request raised | `per_trial_call_failed` | yes |
| 6 | Step 3, a rejection citing no disqualifying row | `model rejection unsupported by its own criteria arrays` | yes |
| 7 | Step 3, a rejection whose disqualifiers did not survive normalisation | `no disqualifying row survived label normalisation` | yes |
| 8 | **Step 2, an unreadable label with no criteria** | `trial-level verdict label not recognised` | **NO -- audit list only** |
| 9 | **Step 2, a readable label with no criteria** | `model returned no criteria` | **NO -- and it was a bare literal, not a constant** |
| 10 | **Step 2, a model-DECLARED not_evaluable with no criteria** | -- | **NO -- nothing recorded at all** |
| 11 | **Step 3, an unreadable label over criteria that disqualify nobody** | `trial-level verdict label not recognised` | **NO -- audit list only** |
| 12 | **Step 3, a model-DECLARED not_evaluable with criteria present** | -- | **NO** |
| 13 | **Stage 6, a label `normalize_trial_verdict` could not resolve** | -- | **NO** |

Rows 10 and 12 are one population and rows 8 and 11 are another, so the eleven
DISTINCT reasons below cover thirteen sites. **All six are stamped now**, and
every one is driven end to end in the new test.

**THE DEFERRAL WAS FOUND WHERE IT WAS RECORDED, and it named ONE of the six.**
`oncotriage/storage/database_logger.py`'s `not_evaluable_reason` column note
said Stage 5's **Step 2** put its reason in the audit list only, that this was
"deliberately NOT closed by the provenance pass" because the field is one of
the per-verdict keys the twelve fixtures compare, and that the population was
still identifiable through

    eligible = 'not_evaluable' AND verdict_source IS NOT NULL
    AND not_evaluable_reason IS NULL
    AND criterion_details = '{"inclusion": [], "exclusion": []}'

**THAT PREDICATE DID NOT SEPARATE WHAT IT SAID IT SEPARATED, and finding that
out is what turned "close the deferral" into "close the class".** Its last term
was justified in the note by the model-DECLARED population having non-empty
arrays. The prompt's Section 1 requires a `not_evaluable` trial's arrays to be
EMPTY -- `compose_assessment`'s own block says so, one module over -- so a
model-declared non-evaluation satisfies all four terms and was reported as the
Step 2 defect. Two populations, one bucket, and no column able to tell them
apart. The note is rewritten rather than edited, with the old predicate quoted
and the correction beside it.

**THREE WRITER CLASSES, DISJOINT, GUARDED AT IMPORT.**
`NOT_EVALUABLE_REASONS_CONSTRUCTED` (the model never answered -- an alias for
the tuple that indexes `_unevaluable_entry`'s explanation table, not a second
copy of it), `NOT_EVALUABLE_REASONS_CORRECTED` (the model answered and the
answer could not be used) and `NOT_EVALUABLE_REASONS_DECLARED` (the model
declared the non-evaluation itself). `NOT_EVALUABLE_REASONS` is their
concatenation and a `RuntimeError` at import -- not an `assert`; `python -O`
deletes those -- refuses a value in two classes, because a value with two
answers to "who decided" is the one question the vocabulary exists to answer.

**THE DECLARED MARKER IS THE `verdict_source = 'canonical'` OF THIS COLUMN**,
and it is here for that constant's own argument rather than for symmetry.
`canonical` is written on a label that needed no recovery because "the
normalizer read this and found nothing to fix" and "no normalizer ran" are
different findings an absence cannot separate. With it, **NULL in this column
now means exactly one thing: the row predates the column** -- an invariant a
query can check, which a three-term predicate over three columns is not.

**THE STAGE 6 CONSTANT LIVES IN `oncotriage/agent/state.py`, AND THE SPLIT IS
LAYERING RATHER THAN TASTE.** `terminal.py` imports `state`,
`registries/primary_cancer` and `utils` and nothing else; importing
`evaluation.py` for one string would put a 7,700-line module carrying two AWS
adapter imports into Stage 6's import graph -- the shape pass 20c-2c moved
`_resolve_primary_cancer` out of the storage layer to remove. `state` is the
leaf both stages already import, and `NOT_EVALUABLE_REASONS` imports the name
from there, so every member still has one owner and one tuple still enumerates
all eleven. Stage 6's branch is **unreachable after Stage 5** (Step 0 resolves
every label into `TRIAL_VERDICTS`) and is stamped anyway, because the API, the
MCP server and every harness may build `evaluations` without Stage 5.

**THE FORGERY-PROOF CLAIM RESTED ON A REMOTE SCHEMA ENFORCER, AND ON TWO OF THE
THREE PROVIDER ARMS THAT IS AN OPEN GO-LIVE QUESTION.** Every "the model cannot
forge this" argument in `evaluation.py` rests on
`additionalProperties: false` being honoured. `bedrock_adapter.py` item (3)
records that no AWS page states whether `text.format` is enforced on the
Responses surface and names "accepted, no error, silently not enforced" as the
dangerous outcome; the Converse branch's A1 asks the same of `outputConfig`. So
`_strip_forged_provenance` removes the key from every model-returned entry at
the TOP of the normalizer loop, above Step 0, and counts what it took.

**WHAT THE STRIP BUYS IS STATED AT TWO STRENGTHS BECAUSE THEY ARE NOT THE
SAME.** On its own, today, it reaches the population NO branch stamps -- an
entry that ends `eligible` or `not_eligible` -- where a forged value lands in
the stored column beside a verdict saying the trial WAS evaluated (control 5a).
On a `not_evaluable` entry the branch's own stamp overwrites it, so the strip
and the stamping are two independent barriers and removing either ALONE changes
nothing there. Remove BOTH and the expensive case opens:
`assessment_composition_case` BRANCHES on this key, so the model selects the
corrected-rejection case and the pipeline stores fixed text asserting it
corrected a rejection it never saw (control 5b). **The first draft of this note
and of the function's docstring claimed the composition hazard was live on its
own; the control measured otherwise and both were corrected.**

**`NOT_EVALUABLE_REASON_ANOMALIES` IS THE 36th MEMBER OF
`oncotriage/degradation.py`'s RUN-END REGISTRY** (measured, 35 before this
pass; the counter-reader test's count does not move, because that file's
section 1 derives its subjects from the registry rather than listing them),
keyed
`forged:vocabulary_member` / `forged:foreign_value` (never the model's text --
a reason is free-written output of unbounded content and this counter reaches
the run-end console block) and `missing:{verdict_source or 'constructed'}`.
`_account_missing_not_evaluable_reason` runs after the reconciliation and above
the composition, so what it measures is what the composition is about to read.
**It only looks**: a missing reason is a defect in that module discovered after
the patient's calls are paid for, and raising would discard a completed
evaluation to report a bookkeeping fault.

**A PRE-EXISTING DEFECT IN THE READER WAS FOUND AND FIXED, AND IT WAS THE SAME
CLASS OF DRIFT.** `queries.not_evaluable_reasons` classified a reason into a
family with FOUR hand-written literals; the per-trial pass had added a fifth
CONSTRUCTED reason (`per_trial_call_failed`) and the CASE was never widened, so
**a trial whose own REQUEST failed was reported under "corrected from a model
verdict" -- a family that asserts the model answered -- and on the SHIPPED
per-trial arm that is the constructed reason most likely to occur.** Measured
by executing the pre-fix CASE over every member. The CASE is generated from
three tuples restated in `queries.py` (which may not import the agent -- the
`CALL_MODE_OMISSION_REASON` precedent, with the same mitigation: the test
imports both and requires equality), it gained a third family
`'declared by the model'`, its NULL arm moved to the TOP, and its `ELSE` now
reads `'(not a value this pipeline writes)'` instead of silently calling an
unknown value a correction.

**THE FIXTURE COST WAS MEASURED BEFORE IT WAS PAID AND IT IS ZERO.** Across all
twelve recorded fixtures there are **103 verdicts, of which exactly ONE is
`not_evaluable`** -- `mcode_genomic_variant` / `NCT05949983`, already carrying
`model rejection unsupported by its own criteria arrays` from a branch this
pass does not touch. `not_evaluable_reason` is projected into the deterministic
prefix, so the field IS fixture-compared; the VALUES that move are on
`not_evaluable` entries only, and there is one. Verified by running rather than
by that argument alone: `python fixture_replay.py` was run against a `git
worktree` at HEAD and against this tree, and **the set of differing fields is
IDENTICAL -- 26 fields, zero introduced, zero removed**, with the same
per-fixture counts. The twelve were already 0/12 (the de-identification and
pre-diagnosis-ECOG passes invalidated them and did not recapture); this pass
adds nothing to the standing recapture.

**THE DEVELOPMENT DATABASES HAVE NO ROWS TO REPORT, AND THAT IS THE ANSWER
RATHER THAN A GAP.** Every `.db` under the project root was queried read-only:
**62 files carrying `trial_matches` and 158,827 rows between them; 0 of those
files have a `not_evaluable_reason` column, 0 have a `verdict_source` column,
and 0 rows in any of them carry the `not_evaluable` verdict** -- the production
file's 12,862 are all `eligible` or `not_eligible`. Every stored row predates both the column AND the verdict: they
were written before the trial-verdict pass, when Step 0 clobbered an
unrecognised label to `not_eligible`. So the requested distribution is empty in
both directions, the "predates the column" split is 100%, and no database was
modified.

**TWO PINS MOVED IN THE EXISTING SUITE AND BOTH WERE THE CHECK WORKING.**
`tests/test_storage_provenance_persistence.py` pinned the SUBSTRING
`eval_result["not_evaluable_reason"] = ` at 2 under a label reading "unchanged
by this pass" -- the provenance pass's own statement that it had not widened
the write. This pass does widen it, to seven; and raising the literal would
have left the other half of that check's problem in place, because **the
substring also matches PROSE and `evaluation.py`'s own argument for the field
quotes the assignment it describes, so the count read 8 against 7 real
writes.** It is an AST walk over ASSIGNMENTS now and asserts the SET of reasons
the normalizer can write. And three plants in
`tests/test_agent_trial_verdict_normalization.py` plus one in each of the two
marker tests were re-anchored; **one of them, `test_agent_remap_no_survivor`'s
C5, had to be re-SITED rather than re-anchored**, because the defect it models
(the marker written at the top of the loop) is no longer expressible from there
-- the strip removes it and, more decisively, every branch overwrites it. It
plants into the DECLARED arm now, which is a population that must not carry the
remap marker.

```bash
# The reason-coverage pass. Same shape, same directory. No network, no keys, NO
# SPEND, no live Qdrant, NO MODEL LOAD (ONCOTRIAGE_DEFER_LOCAL_MODELS is set
# above the imports), no corpus, no database, no git history, no live server --
# every model response is a literal served by a stub installed through
# oncotriage/agent/deps.py and the one raising stub raises a plain
# RuntimeError. It writes NOTHING anywhere, not even a temp directory. NOT in
# the collision matrix: the three files it reads (agent/evaluation.py,
# agent/terminal.py, agent/response_schema.py) are written by neither of the
# suite's two writers and are sha256-compared at the end, with a
# non-degeneracy probe that the three hashes differ. It DOES exec: five
# in-memory copies, argued at _EXEC_ALLOWLIST. Bucket A, ~2.5 s.
python tests/test_agent_not_evaluable_reason_coverage.py            #  86
```

**EIGHT TREE-LEVEL REVERTS, EIGHT CAUGHT**, each applied to a `copytree`'d copy
with `PYTHONPATH` pointed at it, a realpath preflight asserting the COPY is what
imports, and every plant asserting its own occurrence count so a plant that
matched nothing is a named PLANT-FAILED rather than a working check reported as
broken. **Seven produce recorded failures and a summary; none aborts.**

| revert | caught |
|---|---|
| Step 2's three stamps dropped | 13 failures |
| Step 3's unrecognised stamp dropped | 5 |
| Step 3's declared stamp dropped | 5 |
| Stage 6's stamp dropped | 3 |
| the forgery strip removed | 7 |
| the missing-reason tripwire's CALL removed | 3 |
| the family CASE reverted to its four hand-written literals | 9, in `test_storage_query_layer.py` |
| a member put in TWO writer classes | an import-time `RuntimeError` NAMING the value -- the module is unimportable, so there is no process in which a check could run, which is that guard working rather than a gap |

**VERIFIED BY RUNNING.** CI bucket A **87 files, 0 failed, 0 not run** (a first
run reported one failure in `test_ablation_stop_and_lock.py`, which is green
alone twice and green on a second full run -- the load flake that file's own
real-subprocess/real-signal design already carries);
`tests/run_serial_tests.py` **5/5 in 399.6 s** with `oncotriage/config.py` and
`oncotriage/registries/cancer_code_registry.py` confirmed byte-identical
afterwards; `tests/test_package_invariants.py` **260/0/0**, unchanged;
`.github/scripts/ci_test_buckets.py --check` consistent at 106 files;
`static_checks.py` compiles 255; every bucket B/C/E file reachable on this
machine green at its documented count; `python fixture_replay.py` differing
fields identical to HEAD's; and the production `inferences.db`
(`ab1403e3...`, 90,185,728 bytes), `ablation_results.db` (`f2bc23c6...`) and
all twelve fixture files byte-unchanged. **No money was spent and no migration
was run.** Counts that moved, each argued in place:
`test_storage_query_layer.py` 487 -> **498**,
`test_storage_provenance_persistence.py` 126 -> **127**,
`test_agent_remap_no_survivor.py` 122 -> **123**. Every other file reports
exactly what it reported before.

**WHAT IS NOT DONE, NAMED RATHER THAN LEFT TO BE DISCOVERED.**

1. **THE TWELVE FIXTURES ARE STILL 0/12 AND THIS PASS DID NOT RECAPTURE.** That
   is the standing item, owed since the de-identification pass; this change is
   provably not part of what it owes.
2. **`node_finalize` DOES NOT SCAN.** The tripwire runs inside Stage 5, so an
   entry an OUTSIDE caller hands Stage 6 already carrying `not_evaluable` and
   no reason is bucketed without being counted. Stage 6 stamps only what IT
   decides. Closing it means a second scan in a second module and a decision
   about what a caller's own entry means.
3. **THE STRIP IS NOT APPLIED TO `TEMPORAL_CONFLICT_FIELD`, `LABEL_REMAP_FIELD`
   OR THE THREE `verdict_*` MARKERS.** They carry the identical forgery-proof
   argument and the identical dependency on a remote schema enforcer. Only the
   one this pass is about is closed; the general fix is one call site that pops
   every pipeline-owned key, and it belongs to a pass that can measure the
   effect on each.
4. **`per_trial_call_failed` HAS NEVER BEEN OBSERVED IN A STORED ROW**, because
   no database has the column. The family fix above is verified against the
   CASE rather than against data.
5. **THE MCP AND API SURFACES WERE NOT RE-RUN.** Neither module names
   `not_evaluable_reason` or enumerates an entry's keys -- both return `result`
   whole -- so the new key flows through untouched; that was established by
   reading, and `tests/test_mcp_server_stdio_contract.py` is bucket C and needs
   a live Qdrant this pass did not use.


### No run can outspend its budget (the spend-gate pass)

**AWS BUDGETS IS MONITORING AND NOT ENFORCEMENT.** It fires 8 to 24 hours after
the money is gone; OpenAI's usage page is a report. Nothing in this pipeline
could stop a request, and the only brake was the operator stop sentinel -- which
needs a human who already knows something is wrong. The failure this is built
for is the one nobody is watching: a mis-set constant, a defect that re-issues
calls, a corpus ten times the size somebody meant, running overnight.

`oncotriage/spend.py` is the brake: **measured cumulative cost, checked
immediately before every billed call, hard stop at the cap.** No billed call was
made building it; the twelve fixtures' replay outcome is **identical to HEAD,
fixture for fixture and field for field**, and the production `inferences.db`
and the twelve fixture files are byte-unchanged.

| | |
|---|---|
| new module | `oncotriage/spend.py` -- the ledger, the cap, the derived call ceiling, the run-level latch, three counters |
| new constants | `config.SPEND_CAP_USD` (**300.00**), `SPEND_CAP_ENFORCED`, `SPEND_CALL_CEILING_ENFORCED` |
| new column | `runs.stop_reason`, additive TEXT, **schema era 7** |
| new vocabulary | `database_logger.RUN_STOP_REASONS` -- operator / spend_cap / call_ceiling |
| new counters | `SPEND_GATE_SKIPS`, `SPEND_LEDGER_FAULTS`, `SPEND_CEILING_TRIPS` -- the 37th, 38th and 39th in the degradation registry |
| new test | `tests/test_spend_gate.py` -- **151 checks, bucket A, ~1.6 s** |

**THE LEDGER IS CHARGED AT THE RESPONSE, NOT AT THE PATIENT, AND THAT PLACEMENT
IS THE WHOLE OVERSHOOT BOUND.** Every billed call is bracketed -- the gate
immediately before, the charge immediately after -- so the only spend a trip
cannot prevent is what is already past the gate and not yet charged, which is
exactly the requests in flight:

    overshoot <= MAX_WORKERS x per_trial_parallel_bound() = 12 x 4 = 48 requests
              =  48 x $0.0108 = $0.52   (cache working)
              =  48 x $0.0262 = $1.26   (cache absent)

Charging where the node folds its accumulators instead would make the bound
MAX_WORKERS whole PATIENTS, because per-trial mode dispatches a patient's entire
wave before the node reads any of it. **Measured, both grains**: at the Stage 5
grain, a barrier of 4 proves the requests are in flight together and exactly
`1 + parallel` go out past a two-call budget; at the patient grain, a $5 cap at
$1 a patient with 4 workers starts **8** patients, finishes all 8 and writes all
8.

**IT IS NOT THE SAME NUMBER THE ROW STORES, AND THE DIFFERENCE IS IN THE GATE'S
FAVOUR.** `inferences.estimated_cost_usd` carries the token accumulators of ONE
Stage 5 invocation, and a parse failure routes the graph back in with every
accumulator reset -- so a patient that spent three attempts stores the last
one's tokens, which `run_harness.price_result` records as a known under-count
and surfaces through `cost_complete`. **This ledger has no such gap**: it is
charged once per RESPONSE, at the three sites where a response is obtained, and
those sites are INSIDE the retry rather than around it. A gate built on an
under-counting number under-enforces, which is why it is not a sum over the
rows. What it still cannot see is stated at the module: SDK transport retries,
a request that raised before any response, and anything billed outside Stage 5.

**THE CAP IS A CAMPAIGN BUDGET AND A RESUMED RUN INHERITS IT.**
`database_logger.campaign_spend_before` walks the chain BACKWARDS from the run
row just opened -- `resumed = 1`, the nearest preceding run with a resumable
status and an identical fingerprint, transitively -- and sums
`estimated_cost_usd` over its rows. That is `queries.campaign_summary`'s stitch
rule, walked forward instead of stitched with a recursive CTE, and the two are
**pinned against each other** rather than described: a restated rule is a rule
that can drift. Without it the cap is per INVOCATION, which is not a smaller
promise but a broken one -- a run that tripped the cap and was restarted by a
systemd `Restart=` or a cron entry would get a fresh $300 every time, and the
run lock forbids CONCURRENT runs and says nothing about sequential ones.

**A ROW WITH NO COST MAKES THE BASELINE A FLOOR AND EVERY CONSUMER SAYS SO** --
`print_cost_by_model`'s "<- A FLOOR, NOT A TOTAL" precedent. The direction
matters: a floor UNDER-counts, so the gate lets the campaign spend more than it
should. Refusing to resume instead would let one unpriceable historical row
block a campaign.

**`CAMPAIGN_RESUMABLE_STATUSES` MOVED TO `database_logger.py` AND IS IMPORTED,
NOT COPIED.** A second consumer appeared one layer DOWN, in the module that owns
the `runs` table, and `queries` imports that module so the reverse is a cycle.
The choice was a third copy of a three-member tuple whose whole value is that
adding a status is a deliberate edit, or one owner in the module that owns the
vocabulary it is a subset of. The guard that it is a PROPER subset stays in
`queries.py`, beside the SQL it protects.

**THE STOP IS THE OPERATOR STOP SWITCH'S SEMANTICS, REUSED RATHER THAN
REBUILT.** In-flight work completes and is written, nothing new starts, the
checkpoint is current, the run records STOPPED, and a resume continues under the
remaining budget. `spend.SpendStop` is `control.StopSwitch`'s SHAPE without its
sentinel -- a latching `poll(where=)` in the done-callback and in the submit
loop, a `requested` attribute on the hot path -- so the runner's integration
reads the same way for both. **It is NOT a subclass**, and the reason is that
class's own contract: `_resolve_path`, the note reader, the clear vocabulary and
the stale-sentinel preflight are all about a FILE an operator creates. There is
no file here and nothing to clear; the state is the ledger. Inheriting would
give it four methods that answer about a sentinel that does not exist, which is
the silent-no-op shape `_StopSwitch.arm` had to be re-broken to remove.

**AND `Stage5SpendStopped` SUBCLASSES `Stage5ShutdownRequested`, WHICH IS THE
WHOLE STAGE 5 INTEGRATION.** Two places single that class out and both are
exactly right about this one, so neither was edited and neither can drift away
from covering it: the send loop's `except`, which must NOT isolate it to its
trial (`_on_done` checkpoints a COMPLETED patient, so a resume would skip it
forever with most of its trials never judged -- the c33 lesson reached for money
instead of for a signal), and `_account_unconsumed`'s `abandoned:` skip, because
a request the gate declined was never issued. The diagnosis is not lost to the
subclassing: `SPEND_GATE_SKIPS` is a separate counter keyed by phase and by
limit, so "we stopped for money" and "an orchestrator sent SIGTERM" are never
one number.

**`runs.stop_reason` IS A COLUMN AND NOT TWO MORE STATUSES, AND THE ALTERNATIVE
WAS ARGUED BEFORE IT WAS REJECTED.** `runs.status` answers HOW A RUN ENDED, and
a spend stop's answer is byte-identical to an operator stop's: every patient it
started completed and was written, patients remain that it never began, the
checkpoint is intact, a resume continues. That is `RUN_RECORD_STATUS_STOPPED`'s
definition unamended. A fifth and sixth status would be two more members that
`TRACKING_STATUS_FOR`, `CAMPAIGN_RESUMABLE_STATUSES`, `campaign_summary`'s
generated predicate, the Run Health tab's `health_record` CASE and
`tests/test_storage_run_identity.py`'s composition pin would all have to learn
-- and every one of them would answer IDENTICALLY for all three. A distinction
on which no consumer branches differently belongs in a different column;
`verdict_source`'s argument, one table over, where `canonical` is a value of its
own column rather than a fourth member of `eligible`. **One correction it
forced, made in the same commit:** `RUN_RECORD_STATUS_STOPPED`'s docstring said
"a run AN OPERATOR ASKED TO STOP" and named the sentinel as "THE SWITCH THAT
PRODUCES IT". True of every STOPPED row ever written, and no longer true.

**AN UNRECOGNISED REASON IS REFUSED AND COUNTED, NEVER STORED.** The column
exists to be grouped on, and a value outside the closed vocabulary is a bucket
no `GROUP BY` consumer knows about -- the failure a closed vocabulary with an
open writer produces. It is refused rather than mapped to a default, because
every default available is a claim about a mechanism that may not have run.

### THERE IS NO CALLS-PER-MINUTE BREAKER, AND THE ARITHMETIC IS WHY

A rate detector needs a threshold in calls per minute, and **every derivation of
one from this configuration needs a LATENCY term this project does not own** --
`MATCHING_REQUEST_TIMEOUT_SECONDS` is a ceiling, not an expectation, and the
only measured figure (78.5 s per patient over 205 recorded evaluation runs) is a
GROUPED-arm number for a whole patient. A threshold built on an assumed
per-request latency is a literal wearing a derivation's clothes, and it fires on
a fast provider day.

**AND THE RATE IS ALREADY BOUNDED, STRUCTURALLY.** Every billed Stage 5 request
goes through one of three call sites, and each runs either on the node's own
thread (sequential) or on a wave pool of at most `per_trial_parallel_bound()`
workers. With `MAX_WORKERS` patients in flight the process cannot hold more than
**48** requests in flight at any instant, whatever a defect does. A retry loop
does not raise that number -- it raises the COUNT, which is what the cap governs
-- and `MAX_LLM_CLASSIFIER_RETRIES` bounds node re-entry at 4 anyway. So a rate
breaker would trip at the same three sites with the same 48-request overshoot
bound: **it could not save money the cap does not already save, only time.**

**WHAT IS NOT REDUNDANT IS A PER-INVOCATION CALL CEILING, AND THAT IS WHAT
SHIPPED INSTEAD.** The named failure mode is "a defect that re-issues calls" --
a loop that appends to the send queue without popping, a splitter that never
converges. Against THAT the cap is a poor instrument, because it lets ONE
patient spend the entire campaign budget. The number of billed calls one Stage 5
invocation can LEGITIMATELY make is exactly derivable from `config.py`:

    per-trial:  1 + MAX_TRIALS_FOR_EVALUATION                         = 16
    grouped:    MATCHING_MAX_INPUT_PACKED_CHUNKS
                  x (2 ** (MAX_TRUNCATION_SPLITS + 1) - 1)            = 75

-- the same `2**(D+1)-1` expression `HARNESS_POST_READ_TIMEOUT_SECONDS` is
already written over, for the same reason: raising the split depth must move it.
So the breaker is a ceiling on that COUNT rather than on a rate, it needs no
latency term, and **it bounds a runaway at one patient's worth of calls (~$1.7)
instead of the cap's ($300) -- a 175x difference in blast radius.** It is per
INVOCATION and not per patient because a retry re-enters the node with fresh
state and re-entry is already bounded by the router. There is no margin: the
ceiling is the exact number the configuration permits, and a multiplier would be
a literal.

**`SPEND_CEILING_TRIPS` COUNTS INVOCATIONS WHILE `SPEND_GATE_SKIPS` COUNTS
REQUESTS**, and the first draft counted requests in both -- two names for one
finding, which is what `degradation.register` refuses a duplicate NAME for.
`Stage5CallCounter.take()` returns `(granted, first_refusal)` with both decided
inside its own lock, because a caller deriving "first" from a `refusals == 1`
read after the lock was released would race: with four workers refused at once,
none of them or several could see the 1. **Measured: exactly 1 of 240 racing
refusals is told it was the first.**

### WHAT THE PROGRAM COSTS, DERIVED FROM MEASURED INPUTS

Every input is from an artifact in this repository, none invented. `S` = 8,575
tokens (the shared system prefix, mean over the ELEVEN characterization fixtures
carrying a Stage 5 exchange), `u` = 372 (per-trial user block, 1,544 chars at
the 4.15 chars/token this corpus measures), `o` = 696 (output per trial), `N` =
`MAX_TRIALS_FOR_EVALUATION`, rates $2.00/$12.00 per 1M from `PRICING_CONFIG` and
$0.20 per 1M for cached input, which that row records as published and
deliberately unmodelled.

    per patient, cache working   $0.017150 warmup + 15 x $0.010811 = $0.179390
    per patient, cache ABSENT    $0.017150 warmup + 15 x $0.026246 = $0.410840  (2.29x)

                                      cache working    cache absent
      300-patient campaign               $53.79          $123.25
      + resample pass (100)              $17.93           $41.08
      50-patient k=2 re-run (100 runs)   $17.93           $41.08
      100-patient judge pass              $7.50            $7.50
      ------------------------------------------------------------
      PROGRAM                            $97.15          $212.91

**THE DERIVED ESTIMATE DOES NOT EXCEED THE CAP IN EITHER ARM**, which is the
finding rather than the assumption -- and the second column is why the number is
300 and not 150. The cap sits at ~3.1x the expected program and ~1.4x the
program's own worst case.

**THE RESAMPLE PASS IS IN THE TABLE AND IS EASY TO FORGET**: the batch runner
re-runs `RESAMPLE_COUNT` (100) already-completed patients at full price, so a
"300-patient campaign" is 400 patient-runs.

**THE JUDGE PASS IS THE SOFTEST NUMBER AND IS NOT COVERED BY THIS GATE.** The
rater calls a DIFFERENT vendor through the Message Batches API, priced from
`RATER_PRICING` at claude-sonnet-4-6's $3.00/$15.00 with the flat 50% batch
discount, and it does not write `inferences.estimated_cost_usd`. The row assumes
25,000 in / 5,000 out per patient; at 40,000/8,000 it is $12.00. Either way it
is under 10% of the program and it moves no decision -- and an operator reading
"$300 cap" must not believe it bounds the judge.

**NO PER-TRIAL RUN HAS EVER BEEN MEASURED**, which is why the derivation is
arithmetic over grouped-arm token shapes rather than an average of recorded
per-trial runs: all 21 evaluation-run manifests on disk (205 patient runs with a
measured cost) predate the per-trial default, and the three-call probe has still
not been run. The cache-absent column is what that probe settles.

### WHAT THIS PASS FOUND IN ITS OWN WORK BY RUNNING RATHER THAN READING

* **The harness deadlocked and a 20-second timeout hid it.** Section 8's
  releaser waited for the spend latch to trip -- but the held workers are the
  only things that charge the ledger, so nothing could cross the cap while they
  were held. `gate.wait(timeout=20)` rescued it, so the scenario PASSED, took 20
  seconds, and was measuring the timeout rather than the gate. It releases on
  pool saturation now, check **8a-0** asserts the release was not a timeout, and
  the file went **21.6 s -> 1.6 s**.
* **A bypass control was masked by the gate one site over.** 9e removed the
  wave's gate and drove it on a budget already spent -- where the WARMUP gate
  declines first and clears the queue, so the planted module and the shipped one
  both reported 0 requests. It is driven on a budget that crosses MID-WAVE now,
  with its own clean control.
* **The measurement mode's own docstring claimed the opposite of the truth.**
  `SPEND_CAP_ENFORCED = False` was documented as still counting into
  `SPEND_GATE_SKIPS`; that counter counts DECLINED requests, and with nothing
  declined there is nothing to count. Corrected rather than deleted, because
  "the counter still moves" is exactly what somebody would rely on when deciding
  the mode is safe to run a campaign under.
* **An unreadable cap printed two contradictory lines.** `report_lines()` tested
  `cap is not None` after the failure branch had already appended its
  diagnosis, so it printed "UNREADABLE: ..." AND "NONE -- this run was
  unbounded". Three states now, decided before anything is printed.
* **Reusing `WARMUP_SOURCE_SHUTDOWN` would have printed "a shutdown was
  requested" on a row nobody interrupted** -- the exact misdiagnosis that
  constant's own note gives as the reason IT is not folded into `warmup`.
  `WARMUP_SOURCE_SPEND_LIMIT` is the fourth member, with its own floor sentence.

### THE EXISTING SUITE CAUGHT FIVE THINGS, AND EVERY ONE WAS THE CHECK WORKING

| check | what it caught | how it was closed |
|---|---|---|
| `test_storage_schema_guards` 3b-c/3b-d-i | `RUN_COLUMNS` declared `stop_reason` and `start_run_record` set no value -- a named `RuntimeError` at the INSERT, not a bare `KeyError` | `values["stop_reason"] = None` at open, on `note`'s precedent |
| `test_storage_run_identity` | a second `run row` console line -- and, once the check was widened to a PROPERTY, that TWO of the four printed a LITERAL `STOPPED` inside a guard that collapses the conditional | both read `_terminal_status` now, printed bytes unchanged; the check is `len(reads) == len(lines)` instead of `== 1` |
| `test_runner_preflight_and_state_faults` 6f | a fifth `describe_checkpoint_state` call | raised to 5, **and 6f-a added**: EVERY console line in `main()` mentioning the checkpoint must go through the reader, so a sixth verdict added as a literal fails even though the count still reads five |
| `test_package_invariants` 2i | ten new `@property` definitions | declared, with the argument for why the ledger has ten readers and no setter |
| `test_agent_stage5_per_trial_calls` 1f, 8a, c9, c12, c13 | three anchors moved by the bracketing, and `matching_call_mode()` called three times in one file | the node binds `_call_mode` ONCE and threads it, which is better code; 8b-q's name set is now DERIVED and its walk SCOPED past the declarations |

### WHAT IS NOT DONE, NAMED RATHER THAN LEFT TO BE DISCOVERED

1. **THE GATE COVERS STAGE 5 AND NOTHING ELSE.** Embeddings at index time
   (`retrieval/indexer.py`), the independent rater (`evaluation/rater.py`) and
   the ragas harness are NOT instrumented -- three separate billed paths with
   two separate price tables. `config.SPEND_CAP_USD` says so; a reader who takes
   "$300 cap" to mean the whole project would be wrong.
2. **THE ABLATION STUDY IS NOT GATED.** It drives the same Stage 5 node, so its
   requests ARE charged to the ledger and the Stage 5 gate DOES decline them --
   but `oncotriage/ablation/study.py` has no `SPEND_STOP` poll in its pools, no
   ledger seed, and no `stop_reason` on its own run rows. A capped study would
   fail every remaining pair rather than stopping cleanly. The runner's
   integration is ~30 lines and the study already has the stop-switch shape to
   copy.
3. **THE API AND THE MCP SERVER ARE NOT GATED.** Both reach
   `match_patient_to_trials` and therefore charge the ledger, and a long-lived
   server has no run to reset it -- so the ledger grows for the life of the
   process and the cap would eventually decline every request. Today the API
   writes no `runs` row and seeds nothing, so it starts at zero and the cap is
   reached only after $300 of served requests; that is a real behaviour change
   to a serving surface and is the reason it is named here rather than shipped.
4. **`main()`'s TWO NEW CLOSING BLOCKS WERE READ, NOT RUN.** They need a live
   Qdrant and a compiled graph. What IS driven is the derivation they read
   (`_stop_reason`, one `Assign`, pinned by AST), the finalize call that carries
   it, and the two existing structural checks that reach into them -- the
   checkpoint-reader property and the `run row` line property.
5. **THE PROBE HAS STILL NOT BEEN RUN**, so the cache-absent column of the cost
   table is arithmetic rather than a measurement, and it is the column that
   decides whether $300 is 1.4x or 3.1x the program.
6. **A CAMPAIGN'S SPEND IS RE-READ FROM THE DATABASE ONCE, AT START.** A second
   process writing rows for the same campaign while this one runs is not seen --
   the run lock forbids two batch runners on one checkpoint directory, but the
   API writes into the same file with a NULL `run_id` and is therefore outside
   the chain by construction. Stated rather than closed.


Data and keys live outside this folder. Never write an
absolute path. The one exception already exists and is
argued in place: FALLBACK_MAIN_PATH in oncotriage/settings.py.

When you finish, state which parts you verified by running
something, and which parts you only read.