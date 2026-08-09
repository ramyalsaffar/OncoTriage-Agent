# The pipeline, 01 to 29, in order

**This document exists because pass 20e deleted seven numbered files.** It is
the reading order the numbers used to carry.

Before pass 20e, reading `01- Imports.py` through `29- Download Qdrant Data.py`
in order was how a person learned what this pipeline does. That worked because
almost every stage had a numbered file: 01, 02 and 03 were the foundation, 07
parsed patients, 08, 09 and 10 were the clinical registries and the extractor,
13 was the agent, 14 was the database. Passes 20c-1 through 20d-2 moved the
content of all of them into the `oncotriage` package and left each numbered file
behind as a **re-export shim** — a file whose only job was to make the package's
names appear in an `exec()`'d caller's namespace.

Pass 20e measured every one of those shims for consumers, found that the last
five chainers were chaining for callers converted one or two passes earlier, and
deleted the seven that served nobody. **The numbers now say what you can RUN.
They no longer say what the pipeline DOES.** That is what this document is for.

Three end states were considered and this is the third:

| | |
|---|---|
| **(i) accept the gaps** | The numbers become "what you can run", which is true and useful, and the reading order is simply lost. |
| **(ii) keep the deleted files as pointer stubs** | Preserves the sequence at the cost of files that do nothing — which this project calls a defect everywhere else, and which is worse here than usual: a stub and a shim are indistinguishable at a glance, so the next person to need a name adds a re-export to one and the chain is back. |
| **(iii) delete them and write the sequence down once** | What was done. It **includes** (i) — the numbers do now say what you can run, with gaps — and adds back the only thing (i) loses. |

The argument for (iii) over (i) is that the reading order was always prose: a
number is not a dependency edge, and 01→29 was a narrative someone chose. The
argument for (iii) over (ii) is that a document can say things a file cannot.
Six of the modules below **never had a number** — `agent/deps.py`,
`registries/primary_cancer.py`, `embedding.py`, `extraction/negation.py`,
`orchestration/home.py`, `dashboard/tiers.py` — and several of them are the most
load-bearing code in the project. The numbered sequence could never have shown
them. This document does.

**The survivors were not renumbered.** Gaps are cheaper than every note, commit
message and Word document in the project resolving to the wrong file.

---

## The sequence

`x` in the "Run" column means the file is a runnable entry point and the command
is `python "<filename>"` from `03- Code/`. `—` means the number is now a gap.

| # | File | Run | What the stage does | Where the code lives |
|---|---|:--:|---|---|
| 01 | *deleted (pass 20e)* | — | The third-party import block, the `sys.path` bootstrap, and eager path resolution — all for the exec chain. | Third-party imports are now each module's own. The bootstrap is the six-line `try: import oncotriage` block every entry point carries. Paths resolve lazily in `oncotriage/paths.py`. |
| 02 | *deleted (pass 20e)* | — | Shared utilities and `exec_chain` itself. | `oncotriage/utils.py` — cost, Qdrant retry, partial dates, `CaffeinateSession`. `exec_chain` is deleted outright; see that module's docstring. |
| 03 | *deleted (pass 20e)* | — | Every tunable, and the eager OpenAI / Qdrant clients. | `oncotriage/config.py` — the same tunables, plus lazy cached client factories. |
| 04 | `04- FHIR Generate Data.py` | x | Writes the custom ECOG Synthea module, runs the Synthea JAR, normalizes `valueQuantity` → `valueInteger`, writes the run manifest. | `oncotriage/fhir/generate.py` |
| 05 | `05- FHIR Clean Data.py` | x | **DELETES** non-cancer, deceased and over-cap patient bundles in place, manifest-backed. `--dry-run` writes the plan and deletes nothing. | `oncotriage/fhir/clean.py` |
| 06 | `06- FHIR Dataset Characterization.py` | x | Descriptive analysis of the cohort — the tables and figures the write-up cites. **Renamed in 20e** from `06- FHIR Explore.py`. | `oncotriage/fhir/explore.py` |
| 07 | `07- FHIR Parser.py` | x | `parse_fhir_bundle(path)` and the per-resource parsers. ECOG routing, birth-date precision, US Core race/ethnicity. The entry point is a corpus smoke run. | `oncotriage/fhir/parser.py` |
| 08 | *deleted (pass 20e)* | — | Primary-cancer detection: SNOMED → ICD-10-CM → display-term morphology, with metastatic and non-invasive rejected at every layer. | `oncotriage/registries/cancer_code_registry.py` |
| 09 | `09- MeSH Cancer Site Relevance Filter.py` | x | MeSH C04 tree-ancestry site relevance. The entry point runs the five **offline builders** that parse `desc2026.xml` and MRCONSO. | `oncotriage/registries/mesh.py` (the filter) and `oncotriage/registries/mesh_crosswalk_build.py` (the builders) |
| 10 | *deleted (pass 20e)* | — | Index-time, rule-based, zero-LLM extraction of stage requirements and histology tags from criteria text. | `oncotriage/extraction/{negation,stage,histology}.py` |
| 11 | `11- RAG Trial Indexer.py` | x | Scrape ClinicalTrials.gov, embed, index into a staging Qdrant collection, atomic alias swap, cleanup. | `oncotriage/retrieval/indexer.py` |
| 12 | `12- RAG Trial Indexer Validator.py` | x | Nine index-health and retrieval checks. Exit 1 on any CRITICAL failure. | `oncotriage/retrieval/index_validator.py` |
| 13 | `13- LangGraph Agent.py` | x | **The six-stage matching pipeline.** The entry point is a one-patient smoke run, off by default because it costs money. | `oncotriage/agent/` — twelve modules; see the stage table below |
| 14 | *deleted (pass 20e)* | — | The three-table SQLite schema, `initialize_database`, `log_inference`, and the inference write lock. | `oncotriage/storage/database_logger.py` |
| 15 | `15- Database Wipe All Tables.py` | x | `DELETE FROM` every table in the inference database. Gated by `Flag = False`. **Renamed in 20e** from `15- Database Empty.py`. | `oncotriage/storage/maintenance.py` |
| 16 | `16- Database Query.py` | x | ~40 read-only queries as an ordered registry, plus the report renderer. | `oncotriage/storage/queries.py` |
| 17 | `17- FastAPI Server.py` | x | The REST API on :8000. `docker-compose.yml` runs `uvicorn "17- FastAPI Server:app"`. | `oncotriage/api/server.py` |
| 18 | `18- FastAPI Server Test.py` | x | Hits all four endpoints on a **live** server. **COSTS MONEY** (~$0.30). | *(procedural, no package module)* |
| 19 | `19- FastAPI Server Batch Test.py` | x | POSTs one bundle per patient to a **live** server. **COSTS MONEY.** Runs two patients, not the corpus — see its docstring. | *(procedural, no package module)* |
| 20 | `20- Drift Detection.py` | x | KS / PSI / z-score against a 30-day baseline, plus the ECOG availability alert. | `oncotriage/monitoring/drift.py` |
| 21 | `21- Streamlit Dashboard.py` | `streamlit run` | Nine tabs over `inferences.db`. | `oncotriage/dashboard/` — fifteen modules |
| 22 | `22- Airflow Database.py` | x | `airflow db migrate` + check; rewrites `airflow.cfg`. | `oncotriage/orchestration/airflow_setup.py` |
| 23 | `23- Airflow DAG.py` | x | Generates `trial_refresh_weekly` into `{airflow_path}/dags/`. **The DAG is built as a string**, so DAG logic edits go in the generator. | `oncotriage/orchestration/dag_generator.py` |
| 24 | `24- Airflow Manager.py` | x | Start / stop / status / trigger via the REST API v2, and the four-tier password route. **argparse CLI since pass 20f-3** (`start\|stop\|status\|trigger`; a bare invocation prints usage and exits 2). | `oncotriage/orchestration/airflow_manager.py` |
| 25 | `25- Batch Runner.py` | x | Full-corpus run with no HTTP: checkpointed, two thread pools, summary. | `oncotriage/batch/runner.py` |
| 26 | `26- Ablation Study.py` | x | Seven configs over a stratified sample, into `ablation_results.db`. | `oncotriage/ablation/study.py` |
| 27 | `27- Ablation Analysis.py` | x | Comparison table, BH-FDR Wilcoxon family, MDE, two reports; `--db` analyses an isolated study. **Reads** the database, never writes it. | `oncotriage/ablation/analysis.py`, `oncotriage/ablation/figures.py` (the nine figures), `oncotriage/ablation/common.py` |
| 28 | `28- Select Evaluation Sample.py` | x | The seeded 10/10/10 stratified draw into a second database. **Renamed in 20e** from `28- Select 30 Samples.py`. | `oncotriage/evaluation/sampling.py` |
| 29 | `29- Download Qdrant Data.py` | x | Every Qdrant collection — payloads **and** vectors — to JSON on disk. | `oncotriage/retrieval/qdrant_backup.py` |
| 34 | `34- Cohort Selector Diff Read Only.py` | x | LEGACY vs CURRENT cohort selector, **read only**. Not a test. **Renamed in 20e** from `34- Cohort Selector Diff.py`. | `oncotriage/evaluation/cohort_diff.py` |

Numbers 30 to 33 and 35 to 49 are the test suite. They moved into `tests/` in
passes 20d-1 and 20d-2 and were renamed for what they cover;
[`tests/FILE NUMBER MAPPING.md`](tests/FILE%20NUMBER%20MAPPING.md) is the
old-to-new mapping. Files 45 and 46 became `fixture_capture.py` and
`fixture_replay.py` at the top level — they are a manually-run gate rather than
part of any suite, and capture costs money.

---

## The six stages of File 13, in order

| Node | Module | What it does |
|---|---|---|
| 1 `node_query_expansion` | `agent/retrieval.py` | Deterministic MeSH walk over the primary diagnosis. **No LLM.** |
| 2 `node_hybrid_retrieval` | `agent/retrieval.py` | Qdrant-native BM25 (sparse, 75) + dense `text-embedding-3-small` (100), fused by RRF into `RRF_POOL_SIZE`. Falls back to BM25-only if vector search fails. |
| 3 `node_cross_encoder_rerank` | `agent/retrieval.py` | MedCPT cross-encoder, multi-query RRF across queries, stable argsort for determinism. |
| 4 `node_rule_based_filter` | `agent/filtering.py` | MeSH site relevance, stage ordinal, histology, age, sex, dynamic quality threshold, cost cap at 15 trials. |
| 5 `node_llm_classifier_evaluation` | `agent/evaluation.py` | One call producing per-criterion verdicts. JSON-parse failures loop back, up to 3 attempts. |
| 6 `node_finalize` | `agent/terminal.py` | Splits eligible / not_eligible / not_evaluable and normalizes labels. |

`node_no_candidates` and `node_error_handler` are the other two terminal nodes,
also in `agent/terminal.py`. `build_matching_graph()` and
`match_patient_to_trials()` are in `agent/graph.py`.

---

## The modules that never had a number

These are the reason this document is more informative than the sequence it
replaces.

| Module | Why it matters |
|---|---|
| `oncotriage/agent/deps.py` | **The seam.** Every client, model and registry the agent reaches, behind an accessor answering override → cached → build once. It is the only way to redirect anything the agent uses, and its key set is closed so a dropped override raises instead of being ignored. It exists because the pre-package harnesses redirected by rebinding names in the shared exec namespace — which would have sent twelve fixture replays to the real OpenAI endpoint, billed, and still reported them clean. |
| `oncotriage/embedding.py` | **The one construction site** for `SparseTextEmbedding("Qdrant/bm25")`. There used to be three. BM25 sparse vectors are token-ID vectors over the model's vocabulary, so two sides naming different models is a silent retrieval-quality failure: the dot product still computes and nothing raises. |
| `oncotriage/registries/primary_cancer.py` | `_resolve_primary_cancer` — which condition is THE cancer. It lives here rather than in `storage` so that the agent does not depend on the storage layer for a registry lookup. |
| `oncotriage/extraction/negation.py` | `_is_negated`, the **one** name `stage.py` and `histology.py` share. The three-way split of File 10 rests on that measurement; a second shared name means the boundary was drawn wrong. |
| `oncotriage/orchestration/home.py` | The one place `paths.airflow_path` is read. Two modules disagreeing about AIRFLOW_HOME means two metadata databases and a DAG that never appears in the UI. |
| `oncotriage/dashboard/tiers.py` | `MATCH_TIERS` and `MATCH_TIER_COLORS` — the only two module-level mutable objects in the dashboard, which under Streamlit's rerun model would leak across every interaction for every user of the server if anything mutated them. |
| `oncotriage/constants.py` | The two coding-system sentinels. Imports nothing at all, from anywhere. |
| `oncotriage/settings.py` | The one place an `ONCOTRIAGE_*` environment variable is named. |

---

## What replaced the exec chain, mechanism by mechanism

| The chain provided | Now |
|---|---|
| `01- Imports.py` bound `np`, `pd`, `Path`, `OpenAI`, `torch` and eighty more into one shared namespace | Each module imports what it uses. **Nothing replaces this and nothing needs to** — it only ever existed because `exec()`'d files cannot have import statements of their own that reach each other. |
| `_ensure_oncotriage_importable()` put the code directory on `sys.path` | The same six-line `try: import oncotriage` / `sys.path.insert` block, in every entry point, printing the directory it added. `pip install -e .` makes it a no-op. |
| Every path resolved eagerly at chain load | Lazily, on first read, through `oncotriage/paths.py`'s PEP 562 `__getattr__`. A wheel install or a CI checkout of `03- Code` alone can now import the package. |
| `exec_chain([...])` loaded a file's names into your globals | `from oncotriage.X import Y`. |
| Rebinding `qdrant_client` / `openai_client` / `inferences_path` in the shared namespace redirected the pipeline | `deps.set_override(deps.QDRANT_CLIENT, ...)` for the agent; an explicit `db_path=` argument for the writers. |

`tests/test_package_invariants.py` **section 1c** scans the whole repository for
a call to `exec_chain`, a call to `exec()` outside a one-member argued
allowlist, or a by-location module load, and carries a planted control for each
form. That check is what keeps this table true.
