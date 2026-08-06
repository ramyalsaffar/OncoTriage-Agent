# Docker: what a clean bring-up gives you, and what it does not

Pass 20g. This document exists because item 21's verification loaded the MeSH
lookup files **by hand**, with `docker compose cp`, and then reported a clean
bring-up. That is two different claims wearing one sentence: the stack came up,
and somebody typed a command that is written down nowhere. Item 21 recorded
"there is no documented data-loading step" as its own top-ranked follow-up. This
is that document — the step is **recorded, not automated**, and the last section
says why.

Everything below was measured against the image built on **2026-08-06** from the
current tree (pass 20e code, pass 20g Dockerfile), on a stack brought up from
`docker compose down -v` with every volume destroyed.

---

## 1. What `docker compose up -d` gives you, with no further action

Six containers, all healthy, from a checkout of `03- Code` alone:

| Service | Port | State after a clean `up` |
|---|---|---|
| `fastapi` | 8000 | healthy; `GET /health` 200, `GET /pipeline/info` 200 |
| `streamlit` | 8501 | healthy; 200 |
| `qdrant` | 6333 | healthy; 200, **zero collections** (see §2) |
| `airflow-webserver` | 8080 | healthy; `/api/v2/monitor/health` reports metadatabase, scheduler and dag_processor all healthy |
| `airflow-scheduler` | — | healthy |
| `airflow-dag-processor` | — | healthy |

And these facts, each verified rather than assumed:

* **All fourteen `oncotriage/paths.py:_DOCKER_PATHS` names resolve to a path
  that exists and is writable.** `docker/prepare_paths.py` creates them from
  that table on every start and prints one line per path.
* **`import oncotriage` works with `PYTHONPATH` unset** — the image does
  `pip install --editable /app`, and `PYTHONPATH` is not set at all.
* **The `trial_refresh_weekly` DAG is generated, registered and parses.**
  `airflow dags list` shows it; `airflow dags list-import-errors` reports
  `No data found`; its three tasks (`scrape_and_save`, `rebuild_index`,
  `verify_index`) are listed.
* **The inference database is writable.** `resolve_inference_db_path(None)`
  returns `/app/data/inferences.db`, `initialize_database()` creates all three
  tables there, and a row inserts and deletes.
* **The cancer code registry builds with no degraded layers** — `icd10-cm` is
  installed in the image and `REGISTRY_DEGRADATIONS` is empty.
* **Qdrant Cloud is reachable and reports 12,067 points** for the
  `trial_criteria` alias.
* **A second `up` is a no-op**, and a container *restart* re-runs both
  idempotent steps and says so: `prepare-paths` reports `exists` for all
  fourteen, `generate-dag` reports `current` with the sha256 unchanged.

---

## 2. What it does NOT give you

Four things, and they are not the same kind of missing.

### 2a. The MeSH lookup JSONs — this is the one that stops `POST /match`

`/app/data/mesh/` is **empty** on a clean bring-up. `load_mesh_filter()` then
raises, and this is the good outcome rather than the bad one — item 11a is what
made it raise. Verbatim, from inside the running container and without spending
a cent:

```
DegradedDependencyError
MeSH Cancer Filter core lookup file(s) not found:
    - /app/data/mesh/mesh_c04_lookup.json
    - /app/data/mesh/mesh_tree_to_name.json
  Without them the Stage 4 cancer site filter cannot run and EVERY trial passes
  it, for every patient.
```

So a clean container **fails Stage 1 loudly** on the first `POST /match` rather
than quietly matching every patient against every trial. Before item 11a it
would have done the second.

Two core files are required; three more are optional and each prints a `NOTE:`
naming itself when absent (`snomed_to_mesh_trees.json`,
`icd10_to_mesh_trees.json`, `umls_synonym_to_mesh_trees.json`). All five are
built by `python "09- MeSH Cancer Site Relevance Filter.py"` on the host, from
`desc2026.xml` (313 MB) and `MRCONSO_2025AB.RRF` (2.2 GB) — neither of which is
in this repository or in the build context.

**The load step, written down for the first time:**

```bash
# From 03- Code/, with the stack up. Source is the host sibling data tree.
MESH="../02- Data/04- MeSH"          # adjust to your own *MeSH* directory
for f in mesh_c04_lookup.json mesh_tree_to_name.json \
         snomed_to_mesh_trees.json icd10_to_mesh_trees.json \
         umls_synonym_to_mesh_trees.json; do
  docker compose cp "$MESH/$f" "fastapi:/app/data/mesh/$f"
done
docker compose restart fastapi
```

`/app/data/mesh/` is inside the `app-data` named volume, so **one copy serves
every service** and it survives `docker compose down`. It does **not** survive
`docker compose down -v`, which is what makes this step recur.

Confirm it took, still without spending anything:

```bash
docker exec Clinical-Trial-Patient-Match-api python -c \
  "from oncotriage.registries import mesh; print(mesh.load_mesh_filter())"
```

### 2b. The local `qdrant` service holds nothing, and does not need to

`http://localhost:6333/collections` returns `{"collections":[]}` on a clean
bring-up **and on the stack that has been running for months**. That is not a
defect: `load_env_keys()` reads `QDRANT_URL` out of `05- Keys/.env` and it points
at Qdrant Cloud, so the container's client never talks to the sidecar. Measured
both ways — the sidecar reports zero collections while `/pipeline/info` reports
12,067.

The consequence worth knowing: **`docker compose down -v` destroys an empty
qdrant volume and cannot touch the indexed collection**, which is in the cloud.
The local service is there for an offline index build
(`11- RAG Trial Indexer.py` pointed at it) and for nothing else today.

### 2c. `/app/data/patients/fhir/` is empty

Nothing in the container needs it for `POST /match` — the bundle arrives in the
request body. It matters only if you run `25- Batch Runner.py`,
`05- FHIR Clean Data.py` or `06- FHIR Dataset Characterization.py` **inside** a
container. Files 18 and 19 read the **host's** corpus and POST it, so they are
unaffected.

Note that as of pass 20g both files now **exit non-zero** when that directory is
empty rather than printing a note and exiting 0 — which is exactly the state a
container's volume is in after `down -v`.

### 2d. `/app/data/inferences.db` starts empty

So the Streamlit dashboard comes up healthy and shows nothing. That is correct
for a fresh stack and is called out only because "healthy but blank" reads like
a fault.

---

## 3. Why this is not automated

Recorded rather than argued away. Three options were considered:

1. **Bake the lookups into the image.** Impossible without moving data into the
   repository: the build context is `03- Code`, the lookups are in a sibling
   `02- Data/*MeSH/`, and `.dockerignore` excludes `02- Data/` anyway. They are
   data, generated from two multi-hundred-megabyte source files, and they are
   not version-controlled here.
2. **Bind-mount the host MeSH directory into `/app/data/mesh`.** This works and
   it undoes the property item 21 bought: the stack currently runs from a clean
   checkout of `03- Code` with no sibling tree at all. A bind mount whose source
   is absent is created by Docker as an empty *directory*, which is the same
   failure the `../04- Keys/.env` mount produced — so it would need
   `create_host_path: false` and a per-machine path, and the stack would stop
   being checkout-portable.
3. **A loader service or an init container** that copies from a mounted source
   into the volume. The right end state, and it is a design decision about where
   this project's data comes from in a container — one that has to answer for
   the FHIR corpus and the trial scrape too, not only for MeSH.

Choosing between 2 and 3 is a data-provisioning item, not a rebuild. **It is
recorded here as item 21's outstanding follow-up, unchanged in substance and
now written down.** What pass 20g changed is that the step exists in a file
instead of in someone's terminal history.

---

## 4. The one criterion that was not re-run

Item 21 verified that **a real `POST /match` writes a row** into
`/app/data/inferences.db`. That is a live billed Stage 5 call — measured at
$0.13–$0.17 per patient — and pass 20g did **not** re-run it. What was verified
instead, and it is weaker: the resolved path exists, its parent is writable,
`initialize_database()` creates all three tables there, and a row inserts and
deletes. The write through the actual pipeline was **proven once, in item 21,
and is not re-proven here.**

The other half of that gap: with `/app/data/mesh/` empty, a `POST /match`
against a clean stack would die at Stage 1 before reaching Stage 5, so it would
not have cost anything and would not have proven anything either.
