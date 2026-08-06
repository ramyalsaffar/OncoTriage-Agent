# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**OncoTriage Agent** — matches oncology patients (Synthea FHIR bundles) to recruiting ClinicalTrials.gov trials using a 6-stage LangGraph pipeline over hybrid BM25 + vector RAG on Qdrant, with GPT-4o for criterion-level eligibility evaluation.

## The exec() chain and the `oncotriage` package — read this before touching any file

Files 04 to 46 are numbered, space-containing filenames (`25- Batch Runner.py`) that are `exec()`'d into the caller's `globals()`, replicating a Spyder shared-namespace workflow. **None of those files is importable** — spaces and leading digits — and nothing in any pass has changed that.

Files 01, 02 and 03 are different as of item 20c. They are now **re-export shims over a real package**:

Files 01, 02, 03 (pass 20c-1), 08, 09, 10 (pass 20c-2a), 07, 14 (pass 20c-2b), 13 (pass 20c-2c), 05 (pass 20c-3a) and 20 (pass 20c-3b) are shims.

**Files 04, 06, 11, 12 (pass 20c-3a), 15, 16, 17, 25 (pass 20c-3b), 21 (pass 20c-3c-1) and 22, 23, 24, 29 (pass 20c-3c-2) are THIN ENTRY POINTS** — a `__main__` block, the imports it needs, and nothing else. They have **no exec bootstrap at all**: no `exec()` of Files 01/02/03, no `exec_chain`. That is allowed because nothing in the repository chains them, which was verified rather than assumed — every top-level name each of them defined was grepped against every `.py`, `.md`, `.toml` and `.yml` in the tree, and the only hits are prose in CLAUDE.md / `Exception and Fallback Audit.md`, Files 39 and 40 printing `python "04- FHIR Generate Data.py" --population 3000` as a suggested command, a comment in `02- Utility Functions.py` line 46 naming File 16, a comment in File 25 naming File 17's `_run_matching_pipeline`, and coincidental same-named locals elsewhere (`conn`, `cursor`, `_in`, `_out`, `df_cost`, `graph`, `bm25_index`, `print_summary`).

Two of those eight keep one re-exported name each, and both are load-bearing:

- **File 17 keeps `app`.** `docker-compose.yml` line 73 runs `uvicorn "17- FastAPI Server:app"`, and that WORKS — `importlib.import_module` does not require a valid Python identifier, only a file the path finder can locate, so a module name with a space and a leading digit imports fine as long as nobody writes an `import` STATEMENT for it. Verified rather than assumed. It is the same object as `oncotriage.api.server:app`.
- **File 05 keeps a full shim** and its chain of Files 07 and 08: `34- Cohort Selector Diff.py` line 68 chains it and calls `has_cancer_diagnosis`.
- **File 20 keeps a full shim** (pass 20c-3b): `41- ECOG Availability Metric Test.py` chains it and reads nine names out of the shared namespace. Its bootstrap is the lightweight six-line package-import block, **not** the 01/02 exec — running drift detection must not import torch, transformers and streamlit to run three statistical tests over a SQLite table.

| Module | Holds | Imports |
|---|---|---|
| `oncotriage/settings.py` | `ENV_*` names, `resolve_*_path()` | nothing from the project |
| `oncotriage/paths.py` | `IS_DOCKER`, `_glob_one`, every path variable (**lazy**), `load_env_keys()` | `settings` |
| `oncotriage/constants.py` | `SYSTEM_KEY_ABSENT` / `SYSTEM_KEY_UNRECOGNIZED` | nothing at all |
| `oncotriage/config.py` | every tunable, `PRICING_CONFIG`, `DATA_SNAPSHOT_DATE`, lazy client factories | `paths` |
| `oncotriage/utils.py` | `get_model_cost`, `qdrant_retry`, `resolve_qdrant_collection`, `parse_partial_date`, `get_age_reference_date`, `exec_chain`, `CaffeinateSession` | `config` |
| `oncotriage/embedding.py` | **the one** `SparseTextEmbedding("Qdrant/bm25")` construction site — `get_bm25_sparse_model`, `BM25_SPARSE_MODEL_NAME` | nothing from the project |
| `oncotriage/registries/cancer_code_registry.py` | File 08 whole — `CancerCodeRegistry`, `OncologyLabRegistry`, `load_registry`, `load_lab_registry` | `constants` |
| `oncotriage/registries/mesh.py` | File 09's filter half — `MeSHCancerFilter`, `load_mesh_filter`, `specific_cancer_trees` | `paths` |
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
| `oncotriage/dashboard/tiers.py` | `MATCH_TIERS`, `MATCH_TIER_COLORS`, the four `TRIAL_STATUS_*`, `classify_trial_score`, `enrich_match_tiers` | nothing at all |
| `oncotriage/dashboard/app.py` | File 21's `main` — page config, sidebar, the nine tabs | `config`, `dashboard.{data,sidebar,tiers}`, `dashboard.tabs.*` |
| `oncotriage/dashboard/tabs/*.py` | one `render_*_tab` each, nine of them | `dashboard.{data,tiers}`, `config`, `utils` |
| `oncotriage/retrieval/qdrant_backup.py` | File 29 whole — `default_output_dir`, `download_all_collections` | `config`, `paths` |
| `oncotriage/orchestration/home.py` | **the one** `airflow_path` read — `resolve_airflow_home` | `paths` |
| `oncotriage/orchestration/airflow_setup.py` | File 22 whole — `setup_airflow` | `orchestration.home` |
| `oncotriage/orchestration/dag_generator.py` | File 23 whole — the three DAG string pieces, `build_path_block`, `build_dag_content`, `write_dag_file` | `paths`, `settings`, `orchestration.home` |
| `oncotriage/orchestration/airflow_manager.py` | File 24 whole — start/stop, the four-tier password route, status, trigger | `settings`, `orchestration.home` |
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

**The lazy proxy answers for what it wraps, not for itself (pass 20c-3a).**
`_LazyAgentDependency` forwarded `__getattr__` and `__call__` only. CPython looks
an implicit special method up on the TYPE, never through `__getattr__`, so
`bool(proxy)` was always `True` whatever the model said, `proxy == other` was
always `False` even when the wrapped object *was* `other`, `len` / `iter` / `in`
raised a `TypeError` naming the wrapper, and `repr()` described the wrapper even
with an override installed. All six now resolve and delegate, plus `__hash__`
(defining `__eq__` would otherwise make the three names unhashable). **The
forwarded set is closed and File 47 check 5c asserts its exact membership** —
`+`, `[]`, `with`, `str()`, `isinstance` and `.__class__` are *not* forwarded and
answer for the proxy. Eager binding is not the alternative:
`ONCOTRIAGE_DEFER_LOCAL_MODELS` appears in only two files (13 and 46), so Files
31, 32, 35, 36, 37, 39 and 40 would all load MedCPT and FastEmbed for nothing.

**`__repr__` RESOLVES NOTHING — pass 20c-3b corrects pass 20c-3a.** Pass 3a made
it delegate (`return repr(self._resolve())`) so that a proxy handing the agent a
fixture stub would not still print `<lazy MedCPT cross-encoder>`. The goal was
right; delegation was the wrong mechanism, because **repr then triggers a build**:
a debugger rendering locals, a logging call formatting the object, or a bare
`medcpt_model` typed at a prompt downloads and loads ~110 MB and then prints
transformers' multi-thousand-line module tree — on the *diagnostic* path, where
the tool used to inspect the state must not be the thing that changes it. The
proxy carries its `deps` key now and `__repr__` reports the key, the state
(`override` / `cached` / `unresolved`) and, when something is already there, the
wrapped object's **type and id** — via `deps.resolution_state` and `deps.peek`,
neither of which calls a factory. That keeps the honesty (with a stub installed
the repr says `override` and names the stub's class) without the cost. A failure
— reachable only by constructing a proxy with a key `deps` does not know — is
caught rather than allowed to raise, because a raising `__repr__` breaks every
traceback and debugger, and is recorded in `_LazyAgentDependency.repr_failures`.
File 47 check 5c measures this by **counting accessor calls**, which is the only
thing that separates the two shapes: both return a plausible string.

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

The real rule, replacing "nothing is importable":

- **New shared code goes in `oncotriage/`, and is `import`ed.** Only put something in a numbered file if it needs the shared exec namespace. `import` of files 04-46 is still impossible; `from oncotriage.config import MAX_WORKERS` is now the normal way to reach a tunable from anything that is not in the chain.
- **A module-level import name must not be shadowed by a function-local.** In Python a name assigned anywhere in a function is local for the whole of it, so a module that does `from oncotriage import config` and a function that does `config = info.config.params.vectors` turns every earlier `config.X` in that function into `UnboundLocalError`. Pass 3a hit this twice — `index_validator.stage1_index_health` (`config`) and `indexer._flush_embed_buffer` (`embedding`, a `zip()` loop variable) — and neither shows up at import, only at run time. Both were fixed by importing the *names* rather than the module. **File 47 check 2g scans for it** and carries a negative control.
- **`oncotriage.config` must never import `oncotriage.utils`.** That was the cycle: File 02 read `PRICING_CONFIG` / `COLLECTION_NAME` / `qdrant_client` / `DATA_SNAPSHOT_DATE` out of File 03, while File 03 called `load_env_keys()` out of File 02. Under `exec()` both resolved at runtime; as modules it is an `ImportError`. `load_env_keys` moving out of the pair is what broke it — into `settings` in pass 20c-1, into `paths` in pass 20c-2a — and `47- Package Split Test.py` fails if the edge comes back. Note the reintroduced cycle is **order-dependent**: `import oncotriage.config` against it still succeeds, so the AST check is the guard, not the import test.
- **No `oncotriage` module may import another `oncotriage` module from inside a function body.** A deferred import is a dependency that no scan of an import block can see, and it never fails at import in any order, so nothing but a static scan finds it. File 47 scans for it and carries a negative control. **Third-party imports in function bodies are exempt and must stay** — `import icd10` inside `_build_icd10_cancer_sets()` is deliberate: hoisting it would make importing the cancer registry load the whole ICD-10-CM release.
- **Importing a package module opens no client, loads no model, touches no database, reads no file, creates no directory and resolves no directory.** `get_openai_client()` / `get_qdrant_client()` build once, on first call, and cache; `load_mesh_filter()` reads its four JSON lookups on call, never at import; `_build_icd10_cancer_sets()` imports `icd10` on first registry construction; every path in `oncotriage/paths.py` resolves on first read; MedCPT and FastEmbed load on first use through `oncotriage/agent/deps.py` and `oncotriage/embedding.py`. `03- Config.py` calls the client factories at shim load and binds the eager `openai_client` / `qdrant_client` names the chain expects — same objects, no second client. File 47 proves this by trapping `builtins.open`, `io.open`, `socket.socket`, `socket.create_connection` and `sqlite3.connect` **before** importing all thirty-three modules and firing each trap afterwards to show it was armed.
  Pass 20c-3a's three converted files were the worst offenders in the project: File 11 built the FastEmbed model at module level, File 06 resolved three globs, **created a directory**, built the whole ICD-10-CM registry and mutated matplotlib's global style, and File 05 resolved two globs and built the registry. Each is now behind an accessor — `patients_dir()`, `manifest_path()`, `cancer_registry()`, `csv_dir()`, `json_dir()`, `output_dir()` (which also does the mkdir, so it still happens before any write on every call path), `apply_plot_style()`, `synthea_jar_path()`, `synthea_modules_dir()`, `output_dir_full()`. The shims that need eager values (File 05) call the accessors at load, so the chain sees exactly what it saw before.
  **`oncotriage/fhir/explore.py` imports matplotlib, seaborn and pandas at module scope** and that is the one deliberate exception: seven of its twelve functions plot, nothing but File 06 imports it, and File 47 section 2 pre-imports those three before arming its traps — the same allowance it already makes for openai, qdrant_client, numpy and langgraph.
  The `glob` in `paths` was the one exception until pass 20c-2b, and pass 20c-2c found that the fix had a hole: `oncotriage/registries/mesh.py` still wrote `from oncotriage.paths import data_MeSH_path` at module scope, and a `from X import name` is an **attribute read**, so it fired the lazy resolver — meaning importing the *agent* (which imports `deps`, which imports `mesh`) globbed the whole sibling tree and raised on any machine without it. Check 2b only covered `config`, which is how it survived a whole pass. Check 2c now imports **every** package module in its own subprocess with the root pointed at a directory that does not exist. Note that no `open` trap could ever have caught this: `glob.glob` uses `os.scandir`.
- The three functions that read a value out of the shared namespace at call time — `get_model_cost`, `resolve_qdrant_collection`, `get_age_reference_date` — take that value as an **optional argument** in the package, and `02- Utility Functions.py` wraps each one to pass `globals().get(...)`. That seam is load-bearing: Files 36, 37, 45 and 46 rebind `qdrant_client`, and File 38 rebinds `DATA_SNAPSHOT_DATE` and requires a raise.
- `pip install -e .` from `03- Code/` makes the package importable from anywhere. Without it, `01- Imports.py` puts the code directory on `sys.path` itself and prints that it did.

Everything else about the chain is unchanged:

- A function used in file N may be *defined* in file 1, 2, 3, 8, 9, or 10 with no import statement at its use site. To find a definition, grep across all `*.py` **and** `oncotriage/*.py`.
- Every entry-point file **that is still in the chain** begins with the same bootstrap: raw `exec()` of `01- Imports.py` and `02- Utility Functions.py` (needed because `exec_chain` itself lives in 02), then `exec_chain([...])` for the rest. Every one of those bootstraps loads 01 first, and they have to — File 02 has always used `os`, `re`, `time`, `httpx` and `logging` out of File 01's import block.
  **Files 04, 06, 11 and 12 no longer have one** (pass 20c-3a). They carry instead a six-line block that imports `oncotriage`, falling back to putting their own directory on `sys.path` and *printing* that it did — the same three candidates, in the same order, as `01- Imports.py`'s `_ensure_oncotriage_importable()`. `pip install -e .` makes it a no-op. That is what makes `python "11- RAG Trial Indexer.py" --help` stop importing torch, transformers, streamlit and langgraph, stop building an OpenAI and a Qdrant client, and stop loading a FastEmbed model, just to print an argument list.
- `01- Imports.py` keeps its **third-party import block verbatim**. Files 04-46 reach for `np`, `pd`, `Path`, `OpenAI`, `torch` and eighty more with no import of their own, and only an exec'd file can bind those in the caller's globals. Do not move that block into the package.
- `exec_chain` sets `__name__ = "_exec_chain_"` while exec'ing, so `if __name__ == "__main__":` blocks in chained files do **not** fire. That is the mechanism that lets a file be both a runnable script and a library.
- **Do not double-load.** `13- LangGraph Agent.py` already chains 08, 09, 10 — callers of 13 (17, 25, 26) must not list them again. See the warning comment in `26- Ablation Study.py`.
- `_code_dir` is **derived from `__file__`** at the top of each entry point (item 20a); there is no hardcoded absolute path in any tracked file except `FALLBACK_MAIN_PATH` in `oncotriage/settings.py`, which is the deliberate one-machine fallback for `ONCOTRIAGE_MAIN_PATH`. Docker mounts the code at `/app` and `oncotriage/paths.py` switches all data paths on `IS_DOCKER`.

Adding a new script means: copy the bootstrap block, list its deps in `exec_chain`, and put any new shared library in `01- Imports.py` / any new constant in `oncotriage/config.py`. **Prefer the pass-3a shape** — logic in a package module, a thin entry point holding only `__main__` — unless the script genuinely has to feed the shared exec namespace.

## Running things

All commands run from `03- Code/`. Filenames contain spaces — always quote them.

```bash
# Pipeline services
python "17- FastAPI Server.py"                       # API on :8000 (/docs)
uvicorn oncotriage.api.server:app --port 8000        # the same app, package route
streamlit run "21- Streamlit Dashboard.py"           # dashboard on :8501
python "25- Batch Runner.py"                         # full-corpus run, no HTTP, checkpointed
python "15- Database Empty.py"                       # no-op unless Flag = True at its line 78
python "16- Database Query.py"                       # ~40 read-only queries (dies at Q19, item 38)

# Data + index build (one-time / weekly)
python "04- FHIR Generate Data.py"                   # Synthea JAR -> ~22k patients
python "04- FHIR Generate Data.py" --population 3000 --seed 1 --output-dir <scratch>
python "04- FHIR Generate Data.py" --module-only     # rewrite the ECOG module, no generation
python "05- FHIR Clean Data.py"                      # in-place DELETE of non-cancer patients
python "11- RAG Trial Indexer.py" --mode staging     # staging + atomic alias swap (default)
python "11- RAG Trial Indexer.py" --mode direct      # rebuilds in place, causes downtime
python "12- RAG Trial Indexer Validator.py"          # exit 1 on any CRITICAL check failure

# Airflow (orchestration) — run in this order the first time
python "22- Airflow Database.py"                     # airflow db migrate + check, rewrites airflow.cfg
python "23- Airflow DAG.py"                          # writes {airflow_path}/dags/trial_refresh_weekly.py
python "24- Airflow Manager.py"                      # runs start_airflow(); edit its menu for the rest

# Qdrant backup
python "29- Download Qdrant Data.py"                 # -> {data_path}/06- Qdrant Downloaded Data.../
python "29- Download Qdrant Data.py" --output-dir <scratch>

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
python "47- Package Split Test.py"                       # no network, no keys, no corpus; ~30s
pip install -e .                                         # makes `oncotriage` importable anywhere
```

**Files 42, 43, 44 and 47 cannot run concurrently, and that is now a mechanism
rather than a warning (pass 20c-3b).**

```bash
make serial-tests          # runs 42 → 43 → 44 → 47, one at a time, ~8 min
make serial-tests-list     # prints the order and why, runs nothing
python run_serial_tests.py # the same thing without make
```

`run_serial_tests.py` carries the collision matrix pair by pair. In summary: File
44 rewrites the `DATA_SNAPSHOT_DATE` literal in `oncotriage/config.py` **in
place** (hashing before and after, restoring byte-identically) and File 47 check
4 copies that file; File 43 plants defects into
`oncotriage/registries/cancer_code_registry.py` **in place** and File 42 reads
that same source text as the claims under audit; File 47 `copytree()`s the whole
package three times and a copy taken mid-edit carries the edit. Every collision
produces a real-looking failure with no defect behind it. The order is
load-bearing: 42 first against a pristine registry, 47 last over a tree every
earlier file has restored. It runs all four and reports every exit code rather
than stopping at the first failure — each restores its own tree, so one failure
does not invalidate the next.

File 47's per-module import sweep (check 2c) runs one subprocess per package
module through a `ThreadPoolExecutor`. Serially it took about nine minutes and
the module count has gone 26 → 33 (pass 3a) → 42 (pass 3b) → 50 (pass 3c-1,
which added thirteen dashboard modules, each of which imports streamlit in its
own subprocess) → **55** (pass 3c-2: the four orchestration modules and the
Qdrant backup); a test nobody runs
because it is slow protects nothing. A THREAD pool rather than a process pool is the right
tool, not a compromise: each unit of work is already its own subprocess, so the
parent thread spends its life blocked in `subprocess.run()` with the GIL
released.

**Tests** are not pytest — `18-` and `19-` are procedural scripts hitting a *live* server on `localhost:8000`; start `17-` in another terminal first. `19-` slices `fhir_files[410:412]` for a smoke run; widen that slice to go broader.

**START THAT SERVER WITH `ONCOTRIAGE_INFERENCES_DB` SET, OR FILES 18 AND 19 WILL FAIL YOU (pass 20c-3i).**

```bash
ONCOTRIAGE_INFERENCES_DB=/tmp/oncotriage-test.db python "17- FastAPI Server.py"
```

`oncotriage/api/server.py` calls `log_inference(result, patient_data)` with no path — correctly; it is a server, not a test that knows where its output belongs — so it resolves to the production `inferences.db`. Files 18 and 19 POST **real** bundles to it, so every run of either was writing real inference rows and their `trial_matches` children into the real database. Six such rows are in it, dated 2026-08-05, three runs of two patients each. They surfaced only because they changed **which query File 16 dies at**; nothing reported them.

`ONCOTRIAGE_INFERENCES_DB` is named in `oncotriage/settings.py` and resolved by `resolve_inferences_db()`, which is **deliberately not `_from_env`** — that helper appends a trailing separator, correct for every directory and, for a database file, a path `sqlite3.connect` refuses with an `OperationalError` that `log_inference` *catches by design*. One trailing slash would have produced one "Database logging failed (non-critical)" line per patient and a run that recorded nothing while reporting success. Same reasoning as `resolve_airflow_password`, different victim. It strips whitespace, expands `~`, and **raises** if the parent directory is absent — resolution happens outside both writers' `try` blocks precisely so a configuration defect reaches the operator instead of being swallowed as a logging fault.

**Both** `resolve_inference_db_path` (`storage/database_logger.py`) and `resolve_drift_db_path` (`monitoring/drift.py`) honour it, at tier 2 of three: explicit argument → variable → `paths.inferences_path`. They stay separate functions — `monitoring` must not depend on `storage` for a path string — and both reach the variable through `settings`, the module that names it. **The argument still outranks the variable**, and that ordering is what keeps the six isolation tests meaningful: they pass an explicit scratch path and assert on what comes back, and a stray export that outranked the argument would have those assertions reporting the export as the answer they wanted.

Files 18 and 19 cannot redirect the server — it is a separate process with its own environment — so they **detect** instead, on the `_production_drift_rows()` precedent from File 41: read the production inference row count before the run, read it again after, exit 1 naming the variable if it moved. The comparison is shown to discriminate before it is trusted: each file builds a scratch database carrying the **production `inferences` schema, read out of `sqlite_master` rather than retyped**, seeds two rows, inserts a third, and refuses to run unless the counter reports 2 then 3. The connections are `mode=ro` URIs, because a plain `sqlite3.connect` on an absent path *creates* the file — a guard that brought its own database into existence, counted 0 twice and reported success would be worse than no guard. The block is duplicated verbatim in both files on purpose: item 20d converts them, and a self-contained harness belongs in that pass.

**File 19 runs two patients, not the corpus.** Line 219 overwrites the file list with `fhir_files[410:412]` under a comment reading "For testing purposes", while its title, its `Found N patients` line and its `Batch evaluation complete` summary all describe a full-corpus run. Reported, documented in the file's new docstring, and deliberately left — widening it is a spending decision. Both files also state their cost in their docstrings: every POST is a live billed Stage 5 call, measured at $0.13–$0.17 per patient from the six rows above.

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

`ONCOTRIAGE_INFERENCES_DB` overrides `inferences_path` for **both** database writers (`resolve_inference_db_path`, `resolve_drift_db_path`) and is the only way to redirect a running FastAPI server; it is named in `oncotriage/settings.py` and does **not** go through `_from_env`. See the Tests paragraph above for what it is for and why the helper would corrupt it.

`docker-compose.yml` mounts `.env` from a mix of `../04- Keys/` and `../05- Keys/`; only `05- Keys/` exists. Measured per service in pass 20c-3c-2, and it is a **two-two split, not a stray line**: `fastapi` (line 65) and `airflow-webserver` (line 139) mount `../04- Keys/.env`, which **does not exist**, so Docker creates an empty *directory* at that host path and bind-mounts it as `/app/.env` — the container gets a directory where a file should be, and `load_env_keys()` fails with `.env file not found` or an `IsADirectoryError` depending on how it is reached. `streamlit` (line 101) and `airflow-scheduler` (line 184) mount `../05- Keys/.env`, which is correct. Left untouched: it belongs to the Docker item.

**The `AIRFLOW__CORE__DAGS_FOLDER` line is NOT a defect, and pass 20c-3c-2 checked rather than repeated the claim.** `docker-compose.yml` lines 148 and 192 set it to `/app/airflow_home/dags`; `oncotriage/paths.py` line 291 sets the Docker-branch `airflow_path` to `/app/airflow_home/`; and `write_dag_file(dags_root)` writes to `Path(dags_root) / 'dags'`. Those are the same directory, and `AIRFLOW_HOME=/app/airflow_home` agrees with both. What IS true is that **nothing in the container ever runs File 23** — the webserver's command is `mkdir -p /app/airflow_home/dags && airflow db migrate && airflow api-server`, and the scheduler's is `sleep 30 && airflow scheduler`. So the DAG folder is created empty and the scheduler parses an empty directory forever. That is the real Docker-item defect in this area, and it is a missing generation step, not a path mismatch.

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

- **`oncotriage/registries/cancer_code_registry.py`** (shim: `08- Cancer Code Registry.py`) — primary-cancer detection: SNOMED exact → ICD-10-CM 2024 exact (`icd10-cm` package, handles `C34.10` and `C3410`) → display-term morphology fallback. Metastatic/secondary terms are rejected at every layer. Never assume the first condition in a FHIR bundle is the cancer. `import icd10` stays inside `_build_icd10_cancer_sets()`; do not hoist it. **This module's SOURCE TEXT is read by two tests** — `42-` extracts the inline comment beside every code as the claim under audit, and `43-` plants defects into it and hashes the restore. Both point here, not at the shim, and File 42 refuses to run if either claim dict comes back empty.
  Three names pass 2a re-exported are **gone as of pass 20c-2b**: `_REGISTRY` and `_LAB_REGISTRY` were snapshots of the module's private singleton slots, permanently `None` however the real slot was later filled, so they read as "no registry built yet" and could never read as anything else — use `load_registry()` / `load_lab_registry()`. `_var` was a leaked loop variable; `'_var'` is now the last entry in the cleanup tuple that deletes the other four, so the module binds it and then removes it. File 47 holds the pre-2a inventory unedited plus an explicit list of these three as the only permitted deletions, and checks each one is genuinely absent — a fourth name going missing still fails.
- **`oncotriage/registries/mesh.py`** (shim: `09- MeSH Cancer Site Relevance Filter.py`) — MeSH C04 tree ancestry match. Patient side maps SNOMED→CUI→MeSH via UMLS `MRCONSO`, falling back to fuzzy descriptor matching. Trial side is a direct lookup (ClinicalTrials.gov conditions *are* MeSH terms). **Conservative by design: unmappable on either side ⇒ KEEP.**
- **`oncotriage/registries/mesh_crosswalk_build.py`** — File 09's five offline builders (`build_mesh_lookup`, the three crosswalks, `build_all_lookups`). They parse `desc2026.xml` and the 1.5 GB `MRCONSO_2025AB.RRF` and write the JSON that `mesh.py` reads back. **Called from nowhere in the pipeline** — File 09's `__main__` block is the only call site, and `python "09- MeSH Cancer Site Relevance Filter.py"` still runs them. `mesh` does not import this module.
- **`oncotriage/extraction/{negation,stage,histology}.py`** (shim: `10- Structured Eligibility Extractor.py`) — index-time, rule-based, zero-LLM extraction of stage requirements into a structured dict, so stage matching in node 4 is an integer comparison. Unknown ⇒ `None` ⇒ trial passes. The three-way split rests on one measurement: walking every top-level definition in each half for `Name` loads resolving into the other half finds **exactly one edge**, `_is_histology_negated()` → `_is_negated()`. That helper is what `negation.py` holds. File 47 re-derives the measurement against the shipped modules, so a second shared name fails rather than accumulating.
- **`oncotriage/fhir/parser.py`** (shim: `07- FHIR Parser.py`) — `parse_fhir_bundle(path)` takes a **file path**, not a dict (the API writes a temp file to bridge this). Historical medications are deliberately retained with status labels so prior-treatment criteria are evaluable. **LOINC 89247-1 (ECOG) is routed out of `observations`** into `patient_data['ecog_performance_status']`, a dict that is present on every patient. `value` is `None` when nothing was recorded and is **never defaulted to 0** — ECOG 0 is *fully active*, the most eligible a patient can be, so every consumer must test `is None`, never truthiness. Both `valueInteger` (mCODE) and `valueQuantity` (raw Synthea, unit `{score}`) parse, and which was found is kept as `value_shape`; a non-integral or out-of-range grade **raises** rather than rounding. The winner is the most recent observation dated on or before `get_age_reference_date()`, never `datetime.now()`, with the counts and the selection path recorded alongside. `compute_patient_hash` (13) hashes value/date/count/selection but deliberately **not** `value_shape` — normalizing a corpus must not change a hash when the prompt text is identical — and emits nothing at all when no ECOG was present, so hashes already logged against an ECOG-free corpus stay comparable. Covered by `39- ECOG Performance Status Surfacing Test.py`. **This module's SOURCE TEXT is read by two tests, and both point here, not at the shim** — `38-` ast-parses it to prove `_calculate_age` and `_parse_demographics` contain no clock call (and now checks both functions are actually present, because a stale filename made that assertion pass on an empty result), and `39-` slices named function bodies out of it. The shim keeps File 07's `__main__` block, which is the only place in the original 1,491 lines that named a path; it now resolves `data_fhir_path` from the shared namespace when there is one and from `oncotriage.paths` otherwise, prints which, and — unlike before — works when the file is run directly.

### Data preparation (pass 20c-3a)

- **`oncotriage/fhir/clean.py`** (shim: `05- FHIR Clean Data.py`) — the cohort filter. Three phases, in this order: `non_cancer`, `deceased`, `over_cap`. The deceased phase runs BEFORE the cap so the cap samples alive patients only. Every unlink is manifest-backed. `patients_dir()`, `manifest_path()` and `cancer_registry()` are lazy accessors; the shim calls all three at load and binds the eager `PATIENTS_DIR` / `_MANIFEST_PATH` / `_CANCER_REGISTRY` names, because `34- Cohort Selector Diff.py` reads `_CANCER_REGISTRY` straight out of the shared namespace. The registry comes from `load_registry()`, **not** from `oncotriage.agent.deps` — a stub installed for an agent test must not change which bundles a deletion pass removes.
- **`oncotriage/fhir/explore.py`** (entry point: `06- FHIR Explore.py`) — descriptive analysis. `output_dir()` **resolves and creates**: File 06 ran the `mkdir` at module level, and folding it into the accessor keeps the directory present before any write on every call path while keeping the import free of filesystem work. `apply_plot_style()` holds the `sns.set_style` / `plt.rcParams` statements that used to run at import; it is called by `main()` and by all seven functions that draw, so no call path loses the styling and no importer gains it.
- **`oncotriage/retrieval/indexer.py`** (entry point: `11- RAG Trial Indexer.py`) — reaches its clients through `oncotriage.config.get_*_client()`, deliberately **not** through `agent.deps`. Gets the BM25 encoder from `oncotriage.embedding`.
- **`oncotriage/retrieval/index_validator.py`** (entry point: `12- RAG Trial Indexer Validator.py`) — reaches everything through `agent.deps`, including both MedCPT halves, and imports `torch` **inside** `stage2_retrieval_tests()` (the third-party-in-a-function-body exemption; at module scope it would mean importing the validator pulled torch in).
- **`oncotriage/fhir/explore.py`: `output_dir()` is PURE as of pass 20c-3b.** Pass 3a folded the mkdir into it, so `print(f"Output directory: {output_dir()}")` created a directory as a side effect of printing and a caller who only wanted the path could not ask without creating it. The mkdir has its own name now — `ensure_output_dir()` — called by `main()` and by each of the eight functions that write, exactly the arrangement `apply_plot_style()` already had and for the same reason: a caller invoking `analyze_demographics()` directly must not lose the directory.
- **The lazy caches in `fhir/clean.py` and `fhir/explore.py` are locked (pass 20c-3b)**, matching `agent/deps.py` and `paths.py`. `if k not in d: d[k] = build()` is two atomic operations and one non-atomic sequence. Neither module runs multi-threaded today; the lock is about the pattern being copied when the next accessor is added.

### The serving layer (pass 20c-3b)

- **`oncotriage/storage/maintenance.py`** (thin entry point: `15- Database Empty.py`) — `empty_database(db_path, flag)`. **Both arguments stay required and neither gets a default.** `db_path=None` meaning "production" would turn `empty_database()` — a plausible thing to type while exploring a module — into a command that wipes the real `inferences.db`, and `flag=False` as a default would make `empty_database(path)` a no-op that looks like it worked. `Flag = False` stays at module level in File 15: it is data, and the one-line edit that arms the script belongs where a reader looks for it.
- **`oncotriage/storage/queries.py`** (thin entry point: `16- Database Query.py`) — the ~40 queries as an ordered registry of `Query` records. `run(conn, key)` returns one frame, `run_all(conn)` returns them all, `report(conn)` prints exactly what File 16 printed. **The SQL was extracted BY AST and never retyped**, so it is byte-for-byte what it was — including **the two queries item 38 owns and has not fixed**: `expansion_token_efficiency` selects columns that do not exist and `pipeline_consistency` has a stray `WHEN` outside its `CASE`. `report()` still dies at the same query with the same message, and that is the acceptance criterion for the move rather than a defect in it. **File 16 gained a `__main__` guard**, which is a behaviour change: it had none, so loading it ran forty queries against the production database as a side effect. `apply_display_options()` holds the six `pd.set_option` calls File 16 inherited invisibly from `01- Imports.py`; without them every wide frame prints truncated, which is a different report about the same data.
- **`oncotriage/api/server.py`** (thin entry point: `17- FastAPI Server.py`) — `create_app()` is the factory and `app = create_app()` the single call. **The app object at module level is the one deliberate exception to "importing a package module does nothing"**, and it is forced: ASGI takes a `module:attribute` reference. Building it opens no client, loads no model, touches no database and reads no file — the graph is compiled in the `lifespan` handler, on startup, where File 17 always had it. `/pipeline/info` reaches Qdrant through `agent.deps.get_qdrant_client()`, inside the handler, not at import.
- **`oncotriage/monitoring/drift.py`** (shim: `20- Drift Detection.py`) — **File 20 contained ZERO import statements.** Not few; zero. It resolved only inside a namespace somebody else had filled, so `python "20- Drift Detection.py"` — the command in its own `__main__` docstring, and in `21- Streamlit Dashboard.py` line 3609 — died on `PSI_BINS` at the first `def`. Both instructions are true for the first time. `SCIPY_AVAILABLE` is a real `except ImportError` around a real import now, not a `NameError` guard on somebody else's namespace, and `ks_2samp` is bound to `None` on failure so a caller reaching past the flag gets a `TypeError` at the call site.
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

Section 6 of `47- Package Split Test.py` is separate from section 2 **on
purpose**. Section 2 asserts no model-bearing library arrives and **streamlit is
on that list** — it is what says importing the agent does not drag the dashboard
in. The dashboard's modules import streamlit at module scope because every
render function needs it, so they get their own trap run with streamlit and
plotly pre-imported (the same allowance section 2 makes for matplotlib and
seaborn), and with torch / transformers / icd10 still forbidden.

`oncotriage.dashboard.tabs.reproducibility` is ~1,450 lines because it is **one
function**; the tab boundary is the finest cut available without restructuring
it, which is a redesign. Breaking it up is its own item.

### Index lifecycle (Qdrant)

`COLLECTION_NAME = "trial_criteria"` is an **alias**, never a collection. `oncotriage/retrieval/indexer.py` (entry point `11-`) builds into a timestamped staging collection (`trial_criteria_20260226_140159`), creates payload indexes, then `swap_alias_atomic()` in a single `update_collection_aliases` call (zero downtime), then `cleanup_old_collections()`. Use `resolve_qdrant_collection()` (`oncotriage/utils.py`, re-exported by file 02) whenever the *real* collection name is needed for logging — it retries and falls back gracefully. File 02's wrapper hands it the shared namespace's `qdrant_client`, so a fixture proxy or a test stub is what it talks to.

`oncotriage/retrieval/index_validator.py` (entry point `12-`) reaches its clients and both MedCPT halves through `oncotriage/agent/deps.py`, not through `oncotriage.config` and not through File 13's shim proxies — it is the one module in `oncotriage.retrieval` that uses the agent's seam, because the question it answers is "is this index healthy for the AGENT to query". The indexer deliberately does **not**: an index build must not be redirected by a stub installed for an agent test, and `retrieval` importing `agent` would be the wrong direction.

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

**File 29 was the LAST UNGUARDED FILE in the repository.** No function, no `__main__` guard, no bootstrap — every statement at module level, so loading it created a directory, listed every Qdrant collection, scrolled every point with payloads **and** vectors over the network, and wrote one JSON per collection. Item 20b guarded Files 15, 16, 17, 22 and 24 and never reached it, because nothing loads it: the only documented invocation was `exec(open(code_path + "29- Download Qdrant Data.py").read())` from Spyder. That line is gone from the docstring rather than left as a trap — behind a guard it would exec cleanly and download **nothing**, which is worse than failing. Its header comment also claimed it used `results_path`; it never did, and the symtable measurement is what settled that.

`download_all_collections(output_dir, client=None)` takes the destination as a **required** argument with no default, on the `empty_database(db_path, flag)` precedent: a plausible thing to type while exploring a module must not start a full download of a cloud database. `default_output_dir()` resolves the historical destination lazily and **creates nothing** — the `output_dir()`/`ensure_output_dir()` lesson from pass 20c-3b. The client comes from `oncotriage.config`, **not** `agent.deps`, for the same reason `retrieval.indexer` does: a stub installed for an agent test that quietly redirected a BACKUP would be indistinguishable from a real one until the day it was restored from.

One change in `qdrant_backup` is **not** a path accessor, a client accessor or a guard, and is called out rather than folded in: File 29's `except Exception: pass` around `get_aliases()` is now logged. Continuing is right (nothing below uses the aliases, and `Exception and Fallback Audit.md` line 272 rules it acceptable) but silence is not — the project's standing rule is that no exception is caught without being recorded. The type and message are printed and the failure lands in the returned summary as `aliases_error`.

**File 24's `__main__` menu is kept BYTE-VERBATIM**, including its comment `# After setting AIRFLOW_PASSWORD: Check status`, which names the retired route. Replacing the commented menu with a real argparse CLI is the right end state, it is a redesign, and it is a recorded follow-up — not built here. The entry-point docstring carries a loud note immediately above the menu so no reader is misled by that one comment.

**File 47 grew two traps and six modules.** `subprocess.run` and `subprocess.Popen` are patched to raise before the imports, because three of the six new modules are the only ones in the package that spawn processes and **no existing trap could see one** — a subprocess opens no socket, no database and no file *in this process*, and before item 20b loading File 22 ran `airflow db migrate` while loading File 24 launched two long-lived servers. 48 modules now import under all traps; the sweep is **278 checks** as of pass 20c-3i, all passing.

**Pass 20c-3i widened those two traps to twelve and added three sections.** The two subprocess patches were measured, not trusted, and the measurement changed the picture. The comment beside them claimed a `from subprocess import Popen` would escape; it does not — `from X import name` is an attribute read performed when the import *runs*, and every package import runs after the trap is armed, so the attribute, module-alias and from-import forms are all caught (each is now **fired**, not argued, as its own control). `subprocess.call` / `check_call` / `check_output` / `getoutput` and `os.popen` all funnel through the patched `Popen`, which is a CPython implementation detail rather than a guarantee, so they are trapped explicitly. What genuinely escaped was **`os.system`, `os.posix_spawn`, `os.execv` and `os.fork`** — not a reference form at all — plus one real pre-bound from-import, `prompt_toolkit.application.application.Popen`, taken before the patch and therefore out of reach of any attribute patch. The probe now sweeps `sys.modules`, **rebinds** every surviving reference to an original, sweeps again and asserts the second sweep is clean, with a planted holder as the control. Nothing in the package imports prompt_toolkit, which is exactly why reporting rather than closing would have been wrong: a trap whose coverage depends on which third-party packages happen to be installed is a coincidence, not a guarantee.

The three new sections: **2h** (nothing is declared and never read — see the method rule below; its exemption list is closed and each entry argued, and this file's own string literals are excluded from the read corpus so the scan cannot read its own exemptions), **2i** (the decorator inventory of the whole package, pinned at every nesting depth), and a **recursive** subpackage scan in section 1 with a negative control planted three deep.

## Persistence and observability

**`oncotriage/storage/database_logger.py`** (shim: `14- Database Logger.py`) opens no database at load time and never did since item 20b, which turned schema creation into a function because nine other files load 14 or are loaded beside it and every one of them was touching `inferences.db` just by being read. `initialize_database(db_path)` creates three tables: `inferences` (per-patient funnel counts, per-stage timings, token counts, cost), `trial_matches` (per-trial verdicts), `drift_metrics`. It is idempotent — every `CREATE` is `IF NOT EXISTS`, every `ALTER` is guarded by a `PRAGMA table_info` check — and `log_inference` ensures the schema once per resolved path before its first write. `16-` is a scratch query script; `15-` wipes all tables and is guarded by `Flag = False` — leave it False.

**`log_inference(result, patient_data, db_path=None)` takes the database as an argument, and the five isolation tests pass it.** `db_path=None` means `oncotriage.paths.inferences_path` — `resolve_inference_db_path()` is the resolver, and it deliberately does *not* consult the exec namespace. The **shim's** `log_inference` is a wrapper that supplies `globals().get("inferences_path")`, the same late-binding seam File 02 uses for `get_model_cost` / `resolve_qdrant_collection` / `get_age_reference_date`. That seam is what keeps the redirect working for Files 36, 37, 38, 40 and 45, all of which rebind `inferences_path` at a temporary database *before* loading File 14: a module function cannot see a caller's globals, so without the wrapper all five would have written real rows into the real `inferences.db` while still printing the name of the temporary file each thought it was using. All five now **also** pass `db_path` explicitly and assert on the path `log_inference` returns, so neither mechanism is a single point of failure. Each one first checks that `resolve_inference_db_path(None)` is the production database and is *not* its own scratch path, which is what makes the assertion discriminating rather than vacuous. **No writer anywhere in the repository depends on rebinding a shared global any more (pass 20c-3b).** File 41 was the last one — it rebound `inferences_path` for `log_drift_metrics` in File 20. `log_drift_metrics`, `get_baseline_and_current_data` and `run_drift_detection` all take `db_path` now, `log_drift_metrics` **returns the path it wrote to** so a caller can assert on it, and File 41 passes its scratch path, checks the default resolves to production and is *not* that scratch path, demonstrates the assertion failing against a decoy database, and confirms the production `drift_metrics` row count is unchanged at the end.

`_resolve_primary_cancer` lives in **`oncotriage/registries/primary_cancer.py`** as of pass 20c-2c, and both the agent's three terminal nodes and the storage logger import it from there. Pass 2b had already stopped it reading File 13's `_CANCER_REGISTRY` global — a layering violation that left it raising `NameError` in any chain loading 14 without 13 — in favour of `load_registry()`. Pass 2c finished the job: while the function lived in the storage module, the *agent* depended on the *storage* layer for a registry lookup. **`38- Birth Date and Demographics Parser Test.py` section 9b is the only place that exercises it**, because File 38 is the only chain in the repository that loads 14 without 13; it calls the function directly on a stub condition list and asserts a real diagnosis comes back, having first asserted the result is not `None` (which an empty registry filter also returns).

`oncotriage/dashboard/` (thin entry point: `21- Streamlit Dashboard.py`) reads only from `inferences.db`, via the three `@st.cache_data(ttl=60)` loaders in `dashboard/data.py`. See "The dashboard (pass 20c-3c-1)" above.

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