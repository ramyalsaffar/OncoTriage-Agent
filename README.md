# OncoTriage Agent

[![CI](https://github.com/ramyalsaffar/03--Code/actions/workflows/ci.yml/badge.svg)](https://github.com/ramyalsaffar/03--Code/actions/workflows/ci.yml)

Matches oncology patients (Synthea FHIR bundles) to recruiting ClinicalTrials.gov
trials using a 6-stage LangGraph pipeline over hybrid BM25 + vector RAG on
Qdrant.

- [`CLAUDE.md`](CLAUDE.md) — architecture, conventions and the change record.
- [`PIPELINE SEQUENCE.md`](PIPELINE%20SEQUENCE.md) — what the pipeline does,
  stage by stage, and which module holds each stage.
- [`DOCKER CLEAN BRING-UP.md`](DOCKER%20CLEAN%20BRING-UP.md) — what a
  `docker compose down -v` + `up` gives you, and what it does not.

## What CI covers, and what it does not

The badge above reports [`.github/workflows/ci.yml`](.github/workflows/ci.yml).
It is **not** a full-suite signal. Of 32 test files under `tests/`, a
GitHub-hosted run executes **15** — the ones needing no network, no API keys,
no spend, no live server, no live Qdrant and no data outside this repository.

Of the other 17: twelve need the UMLS Metathesaurus, a generated Synthea
corpus, the production inference database, UMLS-derived MeSH crosswalks or a
live Qdrant index. The remaining five are the collision matrix in
[`tests/run_serial_tests.py`](tests/run_serial_tests.py), which runs only
through `make serial-tests`; four of its members need licence-gated or
generated inputs, so on a hosted runner the whole serial suite is reported as
not run rather than partially run. `make serial-tests` is still the local and
self-hosted gate, and CI prints which member needed what.

`.github/scripts/ci_test_buckets.py --list` prints the full classification with
the observed evidence for every entry. The pipeline fails if a test file is
added without being classified, so the gap cannot widen silently.

Not run in CI, deliberately: `fixture_replay.py` (it requires a live Qdrant
whose collection digest matches the twelve captured fixtures), Files 18 and 19,
and `fixture_capture.py` (each makes billed Stage 5 calls).

Only `03- Code/` is version-controlled; the data, keys and results directories
are siblings outside it. See `oncotriage/paths.py`.
