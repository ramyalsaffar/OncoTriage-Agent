# Docker: what a clean bring-up gives you, and what it does not

Pass 20g wrote this because item 21's verification loaded the MeSH lookup files
**by hand**, with `docker compose cp`, and then reported a clean bring-up. That
is two different claims wearing one sentence: the stack came up, and somebody
typed a command that is written down nowhere.

**THE DOCKER PASS CHANGED THE ANSWER TO ALL OF IT.** Three of this document's
findings are closed and one of its arguments turned out to rest on a measurement
nobody had made:

* **The MeSH step is automated, not merely written down.** §3 rejected "bake
  them into the image" partly on the ground that the lookups are "generated from
  two multi-hundred-megabyte source files". The two files that actually block
  `POST /match` total **107,282 bytes**, and they come from the public-domain
  half of that pair. They are vendored at `docker/mesh-core/` and seeded into
  the volume on every start. §2a is rewritten; §3 keeps the part that survived.
* **The container uses the compose `qdrant` service.** §2b said it never talks
  to the sidecar and did not need to. It does now, through
  `ONCOTRIAGE_QDRANT_URL`, and the sidecar starting empty is no longer a
  quiet fact — see §2b and §5.
* **"Healthy" now means "serviceable".** `GET /health` returns **503** while a
  required dependency is missing, so the stack cannot report six green
  containers and refuse every request.
* **§4's un-re-run criterion is re-run.** A live `POST /match` was made against
  the rebuilt image on 2026-08-07 and its row was proved to survive both a
  restart and an image replacement. Cost and numbers in §4.

Everything below was measured against the image built on **2026-08-07** from the
current tree, on a stack brought up from `docker compose down -v` with every
volume destroyed and **nothing copied in by hand at any point**.

---

## 1. What `make up` gives you, with no further action

Six containers from a checkout of `03- Code` alone. **Five healthy and one
deliberately not**, which is the point of the pass:

| Service | Port | State after a clean `up` |
|---|---|---|
| `fastapi` | 8000 | **unhealthy** — `GET /health` **503**, naming an empty trial index (see §2b). Everything else about it is up: the graph compiled, the MeSH filter loaded, `/pipeline/info` answers 200 |
| `streamlit` | 8501 | healthy; 200 |
| `qdrant` | 6333 | healthy; 200, **zero collections** (see §2b) |
| `airflow-webserver` | 8080 | healthy; `/api/v2/monitor/health` reports metadatabase, scheduler and dag_processor all healthy |
| `airflow-scheduler` | — | healthy |
| `airflow-dag-processor` | — | healthy |

Measured 2026-08-07: `docker compose up -d` returns at **t+11.6 s**, five
services are healthy at **t+22 s**, and the API settles into `unhealthy` at
**t+140.6 s** (its `start_period` is 90 s, so that is the first probe after
startup). After the index is populated it goes healthy **within one 10-second
probe interval, with no restart** — see §5.

**Use `make up`, not `docker compose up`.** The image's version label is derived
from `oncotriage.__version__` through a build ARG, and a build that is not
handed one FAILS rather than labelling the image with a stale number. `make`
derives it; a bare `docker compose build` prints the one-liner that does the
same thing.

And these facts, each verified rather than assumed:

* **All thirteen `oncotriage/paths.py:_DOCKER_PATHS` names resolve to a path
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
  thirteen, `generate-dag` reports `current` with the sha256 unchanged.
  (It was fourteen until pass 20f-3 deleted the never-read `requirements_path`
  from both path tables, and the `requirements/` directory with it.)

---

## 2. What it does NOT give you

**Four**, where there were five. 2a used to lead this list — it was the one that
stopped `POST /match` — and it is closed; the heading is kept, and says how,
because this document is where somebody will look for the `docker compose cp`
loop that is no longer needed.

2b has changed character rather than closed: the local Qdrant was previously
unused and empty, and is now used and empty, which is a much louder fact.

### 2a. The MeSH core lookups — CLOSED, they are provisioned automatically

`/app/data/mesh/` used to be **empty** on a clean bring-up, `load_mesh_filter()`
raised, and the operator had to run a `docker compose cp` loop that existed only
in this document. It is now populated before any service starts, from files
inside the image:

```
[prepare-paths]   mesh-core  mesh_c04_lookup.json       seeded
[prepare-paths]   mesh-core  mesh_tree_to_name.json     seeded
```

Measured 2026-08-07 on a `down -v` bring-up with nothing copied in:
`GET /health` reports `mesh_site_filter: ok=true, "loaded"`, and both files are
in the volume at 0644.

**How it works, and why it is not a `COPY` into `/app/data`.** The two files are
vendored in the build context at `docker/mesh-core/` and copied to an image-only
path; `docker/prepare_paths.py:seed_mesh_core()` copies them into the volume on
every start. They cannot be baked into `/app/data/mesh/` directly: Docker
initialises a fresh named volume from the image content at the mount path, five
containers do that concurrently, and the concurrent `mkdir` FAILS — which is the
intermittent bring-up failure pass 20g fixed by emptying those mount points.
Seeding from the entrypoint keeps them empty. The copy is `write-to-temp` +
`os.replace`, so five containers racing on a fresh volume cannot leave a
half-written file; observed on 2026-08-07, `dashboard` won the race and the
other five reported `present`.

**It never overwrites.** A file already in the volume is left alone and reported
`present`, so the `docker compose cp` route below still works for anyone who
wants a newer lookup than the vendored one.

**It is verified, not merely copied.** Each vendored file's sha256 is checked
against `docker/mesh-core/PROVENANCE.json` before it is written, and a mismatch
**refuses to start the container**. That guard exists because a truncated lookup
is still valid JSON — both files are flat objects, so any prefix ending at a
complete entry loads — and a half-sized `mesh_c04_lookup.json` is a Stage 4
filter that silently recognises fewer descriptors.

**Only the two REQUIRED files are vendored.** The three optional crosswalks
(`snomed_to_mesh_trees.json`, `icd10_to_mesh_trees.json`,
`umls_synonym_to_mesh_trees.json`) are derived from UMLS `MRCONSO`, whose
redistribution is a licensing question rather than an engineering one; the two
core files come from the NLM MeSH descriptor file, which is public domain. See
`docker/mesh-core/PROVENANCE.md` for the full argument and the measurement that
overturned §3's claim that this could not be done. Without the optional three,
`load_mesh_filter()` prints a `NOTE:` naming each one and the patient side falls
back to fuzzy descriptor matching — weaker precision in Stage 4, announced, and
counted in `MESH_FILTER_DEGRADATIONS`.

**To add them anyway** (all five are built by
`python "09- MeSH Cancer Site Relevance Filter.py"` on the host):

```bash
# From 03- Code/, with the stack up. Source is the host sibling data tree.
MESH="../02- Data/04- MeSH"          # adjust to your own *MeSH* directory
for f in snomed_to_mesh_trees.json icd10_to_mesh_trees.json \
         umls_synonym_to_mesh_trees.json; do
  docker compose cp "$MESH/$f" "fastapi:/app/data/mesh/$f"
done
docker compose restart fastapi
```

Confirm, still without spending anything:

```bash
docker exec Clinical-Trial-Patient-Match-api python -c \
  "from oncotriage.registries import mesh; print(mesh.load_mesh_filter())"
```

### 2b. The local `qdrant` service is now the one the container USES, and it starts empty

**This section said the opposite until the Docker pass, and both halves of it
have changed.**

It used to read: the container never talks to the sidecar, because
`load_env_keys()` reads `QDRANT_URL` out of `05- Keys/.env` and that points at
Qdrant Cloud. That was true and measured — and the reason it was true is that
`load_env_keys()` **pops** `QDRANT_URL` out of `os.environ` and reloads it from
the .env, so the `QDRANT_URL: http://qdrant:6333` compose setting could not
reach anything.

The pop is deliberate (a stale exported credential must not shadow the
credentials file) and is kept. A second, project-prefixed variable now beats it,
and the compose file sets it:

```yaml
ONCOTRIAGE_QDRANT_URL: http://qdrant:6333
```

Measured inside the running container on 2026-08-07:

```
[Qdrant] endpoint http://qdrant:6333 (from ONCOTRIAGE_QDRANT_URL); api key from none (URL overridden, no key named)
```

and `GET /pipeline/info` reports `"qdrant_endpoint": {"url": "http://qdrant:6333",
"url_source": "ONCOTRIAGE_QDRANT_URL", ...}` with `"trials_indexed": 12067`.

**No API key is sent.** With the URL overridden and no key named, the .env's
Qdrant Cloud credential is deliberately NOT forwarded — see
`oncotriage/settings.py:ENV_QDRANT_API_KEY`. The sidecar sets no
`QDRANT__SERVICE__API_KEY`, so it ignores the header entirely.

**THE CONSEQUENCE: `docker compose down -v` NOW DESTROYS THE INDEX THE CONTAINER
QUERIES.** That is the trade for a self-contained stack, and it is why the API
reports 503 rather than answering "no eligible trials" for every patient. §5 is
how to populate it.

The host is unaffected: with no override set, `oncotriage.config` still resolves
the .env, and the host still reads 12,067 points from Qdrant Cloud.

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

### 2e. The Airflow admin password is new, and nothing prints it (pass 20f-3)

`docker compose down -v` destroys `airflow-db`, so the next bring-up generates a
**new random** admin password. It lands in Airflow's own home:

```
{AIRFLOW_HOME}/simple_auth_manager_passwords.json.generated
```

which in the container is `/app/airflow_home/`.

**Nothing in this project prints it any more.** `start_airflow()` used to echo
it to the terminal; pass 20f-3 stopped, because the four-tier password route
means `status` and `trigger` read that file themselves and no human ever needed
to see the value. The only consumer who does is somebody logging into the web UI
by hand, and this is where they read it:

```bash
docker exec Clinical-Trial-Patient-Match-airflow-webserver \
  cat /app/airflow_home/simple_auth_manager_passwords.json.generated
```

**It is not in `api_server.log`** — checked rather than assumed, on a 12 KB log
from a real `airflow api-server` run: zero occurrences of "password". That file
is the server subprocess's own stdout, and Airflow 3.1.7 does not write the
credential into it.

**Scope of the measurement, stated:** that log was the **host's**, and pass 20f-3
did **not** rebuild or bring up the stack. The container path
`/app/airflow_home/` is derived from `oncotriage/paths.py`'s Docker branch and
`AIRFLOW_HOME`, both of which item 21 verified and neither of which this pass
changed; the `docker exec` line above is therefore a written-down step, not a
re-run one. Everything else in this document is item 21's and pass 20g's
measurement, unchanged.

To choose the password instead of reading one, set
`ONCOTRIAGE_AIRFLOW_PASSWORD` in the service's environment, or pipe it in:

```bash
printf '%s\n' "$PW" | python "24- Airflow Manager.py" status --password-stdin
```

---

## 3. Why the MeSH step IS automated now, and what §3 got wrong

This section used to argue that automating it was out of reach. Three options
were weighed:

1. **Bake the lookups into the image.** Rejected as *"impossible without moving
   data into the repository: the build context is `03- Code`, the lookups are in
   a sibling `02- Data/*MeSH/` … They are data, generated from two
   multi-hundred-megabyte source files."*
2. **Bind-mount the host MeSH directory into `/app/data/mesh`.**
3. **A loader service or an init container.**

**THE FIRST ARGUMENT'S DECIDING PREMISE WAS NEVER MEASURED.** The
"multi-hundred-megabyte" figure is the size of the SOURCES (`desc2026.xml`,
313 MB; `MRCONSO_2025AB.RRF`, 2.2 GB). The OUTPUTS were never weighed. Measured
2026-08-06:

| File | Bytes | Built from | Required? |
|---|---:|---|---|
| `mesh_c04_lookup.json` | 52,883 | `desc2026.xml` | **YES** |
| `mesh_tree_to_name.json` | 54,399 | `desc2026.xml` | **YES** |
| `snomed_to_mesh_trees.json` | 162,103 | `MRCONSO` | no |
| `icd10_to_mesh_trees.json` | 29,732 | `MRCONSO` | no |
| `umls_synonym_to_mesh_trees.json` | 1,596,502 | `MRCONSO` | no |

**107,282 bytes** is what stands between a clean stack and a served request, and
it comes from the public-domain half of the pair. That is not the category of
thing option 1 was rejecting. The part of option 1 that WAS right — Docker
cannot read a sibling directory, so they have to be in the context — is exactly
what `docker/mesh-core/` is.

Options 2 and 3 are still rejected, and for the reasons this document already
gave. A bind mount whose source is absent is created by Docker as an empty
*directory* — the same failure the `../04- Keys/.env` mount produced — so it
would need `create_host_path: false` and a per-machine path, and the stack would
stop being checkout-portable. A loader service reading from a mounted host tree
has the same problem one level out. Seeding from **inside the image** has
neither: there is no host path to be absent.

**What is NOT closed, and is the honest remainder of item 21's follow-up:** the
FHIR corpus (§2c) and the trial index (§2b, §5) are still not provisioned
automatically, and neither can be by this mechanism — one is gigabytes of
generated patient data and the other is a vector index. Where this project's
data comes from in a container remains a design decision for those two. MeSH is
simply no longer part of it. See `docker/mesh-core/PROVENANCE.md` for why only
the two public-domain files are vendored.

---

## 4. The live request, re-run

Item 21 verified that a real `POST /match` writes a row. Pass 20g did **not**
re-run it and said so. **The Docker pass did**, against the image built on
2026-08-07, on the clean stack described above, with the local Qdrant populated
per §5.

| | |
|---|---|
| Patient | one ordinary Synthea bundle, 5,811 entries, 82F, overlapping malignant neoplasm of colon |
| Response | HTTP 200 in **159.0 s** |
| Pipeline | all four retrieval channels OK, `retrieval_degraded: 0`, `mesh_filter_applied: true`, ECOG 1 found and used, terminal node `node_finalize` |
| Stage 5 | 1 call, 11,850 input + 13,095 output tokens, 0 retries |
| **Cost** | **$0.18084**, `matching_model: gpt-5.6-terra` |
| Collection | `trial_criteria_20260803_104642`, resolved through the alias on the **local** Qdrant |
| Rows written | 1 in `inferences`, 15 in `trial_matches` |

**The row survives.** Counted 1/15 after `docker compose restart fastapi`, and
again after a full `docker compose up -d` onto a **rebuilt image** — a stronger
statement than a restart, since the container was replaced and only the named
volume carried the data across.

**The production database was not touched**, which the compose file's use of
named volumes rather than sibling bind mounts is what guarantees:
`02- Data/03- Inferences Storage/inferences.db` read 1,106 inferences and 12,862
trial matches before this request and reads the same afterwards.

**THE COST IS NOT IN THE RESPONSE**, and that is worth knowing before you look
for it. `POST /match` returns token counts (`gpt4o_input_tokens`,
`gpt4o_output_tokens`) but no cost field; the dollar figure is computed by
`log_inference` and lands in `inferences.estimated_cost_usd`. The number above
was read from the row the request wrote:

```bash
docker exec Clinical-Trial-Patient-Match-api python -c \
  "import sqlite3; print(sqlite3.connect('/app/data/inferences.db').execute(
   'select patient_id, estimated_cost_usd from inferences').fetchall())"
```

---

## 5. Populating the trial index

`docker compose down -v` destroys the `qdrant` volume, and since §2b the
container queries that volume. An empty index is **not** a quiet degradation:
Stage 2 refuses to run against one and `GET /health` returns 503 naming it.

**To build the index from scratch**, from the host with the stack up:

```bash
ONCOTRIAGE_QDRANT_URL=http://localhost:6333 \
    python "11- RAG Trial Indexer.py" --mode direct
```

(`localhost` from the host, `qdrant` from inside the network — the same server.
`--mode direct` because a staging build plus an alias swap exists to keep a live
index from blinking, and this one is empty.)

**Know what that command costs before you run it.** It re-scrapes
ClinicalTrials.gov and re-embeds every trial with `text-embedding-3-small`, so
it spends money, takes far longer than a bring-up, and — the part that matters
most — **produces a DIFFERENT corpus than the one the cloud index holds**,
because the registry changes daily. Nothing pinned against the existing index
stays comparable.

**To copy the existing index instead**, which is what the §4 verification used:

```bash
python - <<'PY'
from qdrant_client import QdrantClient
from qdrant_client.models import CreateAliasOperation, CreateAlias
from oncotriage import config
src = config.get_qdrant_client()                       # the .env: Qdrant Cloud
real = next(a.collection_name for a in src.get_aliases().aliases
            if a.alias_name == "trial_criteria")
dst = QdrantClient(url="http://localhost:6333", timeout=600)
src.migrate(dst, collection_names=[real], batch_size=256)
dst.update_collection_aliases(change_aliases_operations=[
    CreateAliasOperation(create_alias=CreateAlias(
        collection_name=real, alias_name="trial_criteria"))])
print(dst.count("trial_criteria", exact=True).count, "points")
PY
```

Free, exact, and measured at **12,067 points in 65–79 s** over two runs. Note
the alias: `COLLECTION_NAME` is an alias, never a collection, and `migrate`
copies collections only — without the second call the index is present and
`trial_criteria` still resolves to nothing.

**The stack goes green on its own.** `GET /health` re-probes rather than
reporting what startup found, so no restart is needed: measured 2026-08-07, the
API went 503 → 200 immediately and Docker's healthcheck followed within one
10-second interval.
