# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**OncoTriage Agent** — matches oncology patients (Synthea FHIR bundles) to recruiting ClinicalTrials.gov trials using a 6-stage LangGraph pipeline over hybrid BM25 + vector RAG on Qdrant, with GPT-4o for criterion-level eligibility evaluation.

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
`exec()` outside a one-member argued allowlist
(`tests/test_storage_query_layer.py`, which execs git blobs), or a by-location
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
| `oncotriage/utils.py` | `get_model_cost`, `qdrant_retry`, `resolve_qdrant_collection`, `parse_partial_date`, `get_age_reference_date`, `CaffeinateSession`. **`exec_chain` was here and is deleted (pass 20e)** | `config` |
| `oncotriage/embedding.py` | **the one** `SparseTextEmbedding("Qdrant/bm25")` construction site — `get_bm25_sparse_model`, `BM25_SPARSE_MODEL_NAME` | nothing from the project |
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
- **Nothing calls `exec_chain`, calls `exec()`, or loads a module by location.** `exec_chain` no longer exists. The one allowed `exec()` in the repository is `tests/test_storage_query_layer.py`, which execs two pre-fix functions unparsed out of a git blob so its negative controls run the real replaced code rather than a retyped copy; that allowlist is closed, argued, and checked for staleness. Section 1c enforces all of it with six planted controls.
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
python fixture_replay.py                            # free; exit 0 only if all 12 replay clean
python "20- Drift Detection.py"                      # KS / PSI / z-score vs 30-day baseline
python "06- FHIR Dataset Characterization.py"        # cohort tables + figures (item 9's source)
python "07- FHIR Parser.py"                          # smoke run: parse the corpus, print the count
python "09- MeSH Cancer Site Relevance Filter.py"    # rebuild the MeSH C04 + UMLS lookups
python "13- LangGraph Agent.py"                      # no-op unless RUN_TEST_ON_EXECUTE = True; COSTS MONEY

# Docker (all six services)
make up                                              # build + up -d; `make build` alone builds
docker compose logs -f fastapi
# A clean `docker compose down -v` + `up` leaves the API deliberately UNHEALTHY:
# its Qdrant volume is gone and an empty index raises rather than answering.
# The MeSH lookups need no manual copy. See "DOCKER CLEAN BRING-UP.md" §5.
ONCOTRIAGE_QDRANT_URL=http://localhost:6333 python "11- RAG Trial Indexer.py" --mode direct
```

```bash
# The eleven component tests (pass 20d-1). No quoting: the names have no spaces.
# None needs a network, a key, a live server, or a cent of spend.
python tests/test_extraction_histology.py                          # 103 checks
python tests/test_agent_mesh_boost_and_quality_gate.py             #  54
python tests/test_registries_mesh_pan_cancer_resolution.py         #  58
python tests/test_registries_cancer_codes_and_stage_extraction.py  # 136
python tests/test_agent_ablation_flag_passthrough.py               #  39
python tests/test_storage_inference_logging_contract.py            #  79
python tests/test_agent_retrieval_observability.py                 # 103
python tests/test_fhir_birth_date_and_demographics.py              # 172
python tests/test_fhir_ecog_surfacing.py                           # 105; needs 04-'s scratch corpus
python tests/test_storage_ecog_logging.py                          # 104
python tests/test_monitoring_ecog_availability_drift.py            # 111 (was 112; see pass 20e)

# The rest of the suite (pass 20d-2). Same shape, same directory.
python tests/test_registries_cancer_code_claims_audit.py           # 197
python tests/test_registries_cancer_code_claims_audit_control.py   #  16; 14 planted, 14 caught
python tests/test_config_snapshot_date_rot.py                      #  10; 6 subprocess runs, ~6 min
python tests/test_package_invariants.py                            # 247 (was 248; pass 20f-3 deleted the _REEXPORT_EXEMPTIONS staleness check with the table). No network, no keys, no corpus
python tests/test_degraded_dependencies.py                         # 172 (was 170; see pass 20e). Item 11a
python tests/test_storage_query_layer.py                           # 194; item 38, temp SQLite only

# The four added by pass 20f-1. Same shape, same directory, no network, no keys,
# no spend, and none of them writes anything in the repository.
python tests/test_paths_glob_determinism.py                        #  25
python tests/test_storage_wipe_all_tables.py                       #  22
python tests/test_fhir_parser_dict_input.py                        #  29
python tests/test_ablation_db_isolation.py                         #  72 (was 43; pass 20f-3 added section 5b for the --db parent guard and the checkpoint)

# The render-snapshot test (pass 20f-5, extended by 20f-6). Same shape, same
# directory, no keys, no spend, and "no network" is now MEASURED rather than
# claimed. It reads a SEEDED SCRATCH database and never the production one,
# writes nothing in the repository, and is not in the collision matrix.
python tests/test_dashboard_reproducibility_tab.py                 # 200 (was 163; pass 20f-6 added the template-pool controls, the offline guard and the enrichment-divergence check); ~1.7 s
python tests/test_dashboard_reproducibility_tab.py --update-snapshot  # regenerate the golden snapshot ON PURPOSE

# The Docker pass. Same shape, same directory. No network, no keys, no spend,
# and no Docker daemon: every Qdrant client is a stand-in and section 1's
# subprocesses import oncotriage.config only. Not in the collision matrix.
python tests/test_docker_qdrant_override_and_readiness.py           # 122

# The MCP pass. Same shape, same directory. No keys and NO SPEND -- the judging
# is stubbed through oncotriage/agent/deps.py. It is NOT offline: sections 4, 5
# and 6 make real Qdrant round trips, because the readiness gate and the trial
# lookup are what it exists to prove. Not in the collision matrix. ~2 min.
python tests/test_mcp_server_stdio_contract.py                      # 135

# The structured-logging pass. Same shape, same directory. No keys and NO SPEND
# -- section 8 drives all six stages of the real graph with the Qdrant client,
# the cross-encoder and the OpenAI client replaced through
# oncotriage/agent/deps.py. Not in the collision matrix. ~40 s.
python tests/test_observability_logging.py                          #  82

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
| ~~`requirements_path`~~ | `07- Requirements/` | **DELETED (pass 20f-3)**, from both path tables, with the in-repo `requirements/` directory. It was read by no code, ever; `pyproject.toml` is the one dependency list. The stale sibling outside the repository is untouched and nothing resolves to it any more |

`ONCOTRIAGE_ALLOW_DEGRADED_REGISTRIES` permits a run to continue with the MeSH
site-relevance layer or the ICD-10-CM layer ABSENT rather than raising; it is
named in `oncotriage/settings.py`, does **not** go through `_from_env`, and does
**not** reach `oncotriage/fhir/clean.py`'s deletion path. See "Degraded
dependencies (item 11a)" below.

`ONCOTRIAGE_QDRANT_URL` moves the Qdrant endpoint, and `ONCOTRIAGE_QDRANT_API_KEY` the credential; both are named in `oncotriage/settings.py` and neither goes through `_from_env`. See "The Qdrant endpoint has one deliberate override (the Docker pass)" below — the short version is that `QDRANT_URL` in the environment is an ACCIDENT and still loses to the .env, because `load_env_keys()` pops it, and this one is a DECISION and wins.

`ONCOTRIAGE_INFERENCES_DB` overrides `inferences_path` for **both** database writers (`resolve_inference_db_path`, `resolve_drift_db_path`) and is the only way to redirect a running FastAPI server; it is named in `oncotriage/settings.py` and does **not** go through `_from_env`. See the Tests paragraph above for what it is for and why the helper would corrupt it.

**THE `../04- Keys/` MOUNT IS FIXED AND THIS PARAGRAPH USED TO SAY IT WAS NOT** — corrected during the Docker pass, which measured the compose file rather than re-reading this note. What was true: pass 20c-3c-2 found a **two-two split, not a stray line** — `fastapi` and `airflow-webserver` mounted `../04- Keys/.env`, which does not exist, so Docker created an empty *directory* at that host path and bind-mounted it as `/app/.env`, and `load_env_keys()` failed with `.env file not found` or an `IsADirectoryError` depending on how it was reached; `streamlit` and `airflow-scheduler` were correct. **Item 21 closed it**: all four (now five, with `airflow-dag-processor`) name `../05- Keys/.env`, and every one carries `create_host_path: false`, which turns a missing or misspelled credentials path from a silently-mounted empty directory into a failure at `up` that names the path. The only occurrences of `04- Keys` left in `docker-compose.yml` are the two comments recording the fix.

**The `AIRFLOW__CORE__DAGS_FOLDER` line is NOT a defect, and pass 20c-3c-2 checked rather than repeated the claim.** `docker-compose.yml` lines 148 and 192 set it to `/app/airflow_home/dags`; `oncotriage/paths.py` line 291 sets the Docker-branch `airflow_path` to `/app/airflow_home/`; and `write_dag_file(dags_root)` writes to `Path(dags_root) / 'dags'`. Those are the same directory, and `AIRFLOW_HOME=/app/airflow_home` agrees with both. What IS true is that **nothing in the container ever runs File 23** — the webserver's command is `mkdir -p /app/airflow_home/dags && airflow db migrate && airflow api-server`, and the scheduler's is `sleep 30 && airflow scheduler`. So the DAG folder is created empty and the scheduler parses an empty directory forever. That is the real Docker-item defect in this area, and it is a missing generation step, not a path mismatch.

## Pipeline architecture

`oncotriage/agent/` is the core (thin entry point: `13- LangGraph Agent.py`, 5,565 lines before pass 20c-2c split it twelve ways and pass 20e removed the shim it left). `build_matching_graph()` in `agent/graph.py` wires a `StateGraph` over `TrialMatchState` (`agent/state.py`):

1. **`node_query_expansion`** — deterministic, no LLM. Uses the cancer registry (08) + MeSH filter (09) to expand the patient's primary diagnosis into query terms.
2. **`node_hybrid_retrieval`** — Qdrant-native BM25 (FastEmbed sparse, `BM25_RETRIEVAL_SIZE=75`) + dense `text-embedding-3-small` (`VECTOR_RETRIEVAL_SIZE=100`), fused by RRF into `RRF_POOL_SIZE`. Falls back to BM25-only if vector search fails.
3. **`node_cross_encoder_rerank`** — MedCPT cross-encoder, multi-query with RRF across queries, stable argsort for determinism. It also **retains the raw MedCPT score**: `medcpt_score_max` (the best score across the rerank queries, `None` when no query scored the trial — never `0.0`) and `medcpt_queries_scored`. RRF keeps ranks and throws the scores away, which is right for fusion and leaves nothing calibrated for an absolute gate to read.
4. **`node_rule_based_filter`** — MeSH site relevance, cancer stage ordinal, histology, age, sex + a **two-knob quality gate** and cost cap (`MAX_TRIALS_FOR_EVALUATION = 15`). Both knobs must pass: `QUALITY_THRESHOLD_PERCENTILE = 25` of the **unboosted fused** score within the pool, and `MEDCPT_SCORE_FLOOR` on `medcpt_score_max`. A trial with no MedCPT score is not dropped by the floor — absence of a score is not a low score. Each knob reports its own count (`quality_dropped_percentile` / `quality_dropped_floor` / `quality_dropped_floor_only`); they **overlap**, so they do not sum to `quality_dropped`.

**`RERANK_SCORE_THRESHOLD` IS DELETED AND THE REASON IS THE POINT.** It was `-10`, a floor on the *fused RRF* score, under a comment describing MedCPT's `-25 .. +10` range — true of the code it was written for, false of the code it sat above. A fused RRF value runs about **0.01 .. 0.06** and is a function of pool size and query count, not of quality (a trial ranked first by all three queries scores ~0.050 however good it is). The gate took `max(percentile, floor)`, so the floor could never be selected — **not rarely, never** — and the relative percentile was doing 100% of the filtering, cutting one trial from a patient whose four survivors were all excellent. `MEDCPT_SCORE_FLOOR` is measured, not chosen: `python measure_medcpt_scores.py` (thin entry point over `oncotriage/evaluation/medcpt_calibration.py`) runs Stages 1–3 only over a seeded 10-breast/10-colon/10-lung sample and reports the distribution plus what the floor would drop *that the percentile does not*. Re-run it after an index rebuild, a rerank-query change, or a cross-encoder checkpoint change.
5. **`node_gpt4o_evaluation`** — one GPT-4o call producing per-criterion verdicts; JSON-parse failures loop back up to `MAX_GPT4O_RETRIES = 3`.
6. **`node_finalize`** — splits eligible/not_eligible, normalizes labels.

Nodes 1–3 are in `agent/retrieval.py`, node 4 in `agent/filtering.py`, node 5 in `agent/evaluation.py`, node 6 and the other two terminal nodes in `agent/terminal.py`.

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
  repository** for a call to `exec_chain`, a call to `exec()` outside a
  one-member argued allowlist, or a by-location module load. The old checks
  could not have caught a *new* file that started exec'ing; this does.
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
1.45.1 is not installable alongside `apache-airflow-core 3.1.7` at all
(`packaging<25` versus `packaging>=25.0`, no version satisfies both), so
recording it would make the dependency list unresolvable.


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

**`mcp==2.0.0` FORCED A SECOND PIN, AND WITHOUT IT THE FASTAPI SERVER BREAKS.**
mcp requires `sse-starlette>=3.0.0`; that package's current release requires
`starlette>=0.49.1`; `fastapi==0.117.1` requires `starlette<0.49.0`. Installing
mcp plain drags starlette to 1.4.1 and `import oncotriage.api.server` then dies
at `FastAPI(...)` with `TypeError: Router.__init__() got an unexpected keyword
argument 'on_startup'` — measured, not predicted. `sse-starlette==3.0.2` is the
newest release whose starlette requirement is an **extra**, so it constrains
nothing; nothing here uses it, since the server speaks stdio only. The real fix
is moving fastapi past its cap, which is a serving-layer change item 21 and
Files 18/19 measure. **Recorded as a follow-up.**

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
not a durable record, and `gpt4o_prompt` is not on the allowlist, so it cannot
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

**`tests/test_indexer_admission_filters.py` — 131 checks, no network, no keys, no
spend, not in the collision matrix.** Every check is paired with a control that
FIRES against the old implementation, and the old implementations are lifted out
of `git show` rather than retyped. **The revision is DERIVED by AST**, not by
substring: the current file quotes `if min_age > 18` verbatim in the comment
explaining its deletion, so a substring search selects the commit that REMOVED
it and every control then tests the fix against itself — the lesson
`tests/test_storage_query_layer.py` had to learn. It is the third member of
`test_package_invariants.py`'s `_EXEC_ALLOWLIST`, argued there.

## Persistence and observability

**`oncotriage/storage/database_logger.py`** (its shim, `14- Database Logger.py`, was deleted in pass 20e) opens no database at load time and never did since item 20b, which turned schema creation into a function because nine other files load 14 or are loaded beside it and every one of them was touching `inferences.db` just by being read. `initialize_database(db_path)` creates three tables: `inferences` (per-patient funnel counts, per-stage timings, token counts, cost), `trial_matches` (per-trial verdicts), `drift_metrics`. It is idempotent — every `CREATE` is `IF NOT EXISTS`, every `ALTER` is guarded by a `PRAGMA table_info` check — and `log_inference` ensures the schema once per resolved path before its first write. `16-` is a scratch query script; `15-` wipes all tables and is guarded by `Flag = False` — leave it False.

**`log_inference(result, patient_data, db_path=None)` takes the database as an argument, and the five isolation tests pass it.** `db_path=None` means `oncotriage.paths.inferences_path` — `resolve_inference_db_path()` is the resolver, and it deliberately does *not* consult the exec namespace. The **shim's** `log_inference` is a wrapper that supplies `globals().get("inferences_path")`, the same late-binding seam File 02 uses for `get_model_cost` / `resolve_qdrant_collection` / `get_age_reference_date`. That seam is what keeps the redirect working for Files 36, 37, 38, 40 and 45, all of which rebind `inferences_path` at a temporary database *before* loading File 14: a module function cannot see a caller's globals, so without the wrapper all five would have written real rows into the real `inferences.db` while still printing the name of the temporary file each thought it was using. All five now **also** pass `db_path` explicitly and assert on the path `log_inference` returns, so neither mechanism is a single point of failure. Each one first checks that `resolve_inference_db_path(None)` is the production database and is *not* its own scratch path, which is what makes the assertion discriminating rather than vacuous. **No writer anywhere in the repository depends on rebinding a shared global any more (pass 20c-3b).** File 41 was the last one — it rebound `inferences_path` for `log_drift_metrics` in File 20. `log_drift_metrics`, `get_baseline_and_current_data` and `run_drift_detection` all take `db_path` now, `log_drift_metrics` **returns the path it wrote to** so a caller can assert on it, and File 41 passes its scratch path, checks the default resolves to production and is *not* that scratch path, demonstrates the assertion failing against a decoy database, and confirms the production `drift_metrics` row count is unchanged at the end.

`_resolve_primary_cancer` lives in **`oncotriage/registries/primary_cancer.py`** as of pass 20c-2c, and both the agent's three terminal nodes and the storage logger import it from there. Pass 2b had already stopped it reading File 13's `_CANCER_REGISTRY` global — a layering violation that left it raising `NameError` in any chain loading 14 without 13 — in favour of `load_registry()`. Pass 2c finished the job: while the function lived in the storage module, the *agent* depended on the *storage* layer for a registry lookup. **`tests/test_fhir_birth_date_and_demographics.py` section 9b is the only place that exercises it**, because it is the only place in the repository that reaches the storage layer without the agent — an exec chain that loaded 14 without 13 before pass 20d-1, an import of `oncotriage.storage.database_logger` without `oncotriage.agent` after it; it calls the function directly on a stub condition list and asserts a real diagnosis comes back, having first asserted the result is not `None` (which an empty registry filter also returns).

`oncotriage/dashboard/` (thin entry point: `21- Streamlit Dashboard.py`) reads only from `inferences.db`, via the three `@st.cache_data(ttl=60)` loaders in `dashboard/data.py`. See "The dashboard (pass 20c-3c-1)" above.

**Cost accounting fails loudly.** Costs come from `get_model_cost()` (`oncotriage/utils.py`) against `PRICING_CONFIG` in `oncotriage/config.py`, dated `last_updated`. A model absent from that table raises `UnknownModelPricingError` (a `RuntimeError` subclass — deliberately *not* a `KeyError`, so a stray `except KeyError` cannot eat it); it does not return 0.0, because a zero cost row is indistinguishable from a genuinely free run and every aggregate over the column silently under-reports. Both writers — `log_inference` (14) and `log_ablation_result` (26) — call it **before** their `try` block for exactly this reason: their broad `except` exists to keep a database failure from killing the pipeline, and an unpriced model is a config defect that must reach the caller instead. If you add a model, add its pricing first; never wrap `get_model_cost()` in a recovery path.

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

Data and keys live outside this folder. Never write an
absolute path. The one exception already exists and is
argued in place: FALLBACK_MAIN_PATH in oncotriage/settings.py.

When you finish, state which parts you verified by running
something, and which parts you only read.