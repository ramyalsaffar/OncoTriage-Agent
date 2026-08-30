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

## Secrets: two layers, and only one of them is a guarantee

**Install the local hook — one command, in a fresh clone:**

```bash
make hooks                      # == git config core.hooksPath .githooks
```

`.git/hooks` is not tracked, so a hook copied in there exists on one machine and
nobody can review it. `core.hooksPath` points git at the tracked
[`.githooks/`](.githooks/) directory instead, so the hook ships with the clone
and this one command arms it. It **replaces** the hook directory rather than
adding to it — any other hook you keep in `.git/hooks` stops running while it is
set.

The hook scans **staged content only** and takes about **1.0 s** (measured, with
gitleaks installed; without it the gate says so in capitals and runs one engine).
It is **convenience, not protection**: `git commit --no-verify` walks past it,
and a contributor who never runs `make hooks` never has it.

**The guarantee is the `secret-scan` job** in
[`.github/workflows/ci.yml`](.github/workflows/ci.yml). It runs
[`.github/scripts/secret_scan_gate.py`](.github/scripts/secret_scan_gate.py)
over **every object in the git object database** — reachable or not — with two
engines: gitleaks 8.30.1 (~170 provider rules) and this project's own
[`oncotriage/staging/secrets_scan.py`](oncotriage/staging/secrets_scan.py)
(nine content detectors *and* a filename layer). ~18 s, 128.89 MB on this
repository.

The range is the object database and not a commit range, because a commit range
cannot be defended — measured, in a clone of this repository:

| | gitleaks git, `--all --full-history` | this gate, object range |
|---|---|---|
| a secret in an **evil merge** (content in neither parent) | 202 commits scanned, **no leaks found, exit 0** | **exit 1**, 4 findings, both engines |
| a secret committed and then `git rm`'d | working-tree scan says **clean** | **exit 1**, the blob is still clonable |
| a secret left **unreachable** by a force-push | `rev-list --objects --all` **misses it** | **exit 1**, `--batch-all-objects` finds it |

Run it yourself against this checkout:

```bash
make secret-scan                # the same gate, ~18 s
```

Accepted findings live in
[`.github/scan-accepted-fingerprints.txt`](.github/scan-accepted-fingerprints.txt),
one per finding, keyed on the **blob oid** so an entry survives a rebase and can
never suppress different content. **Green does not mean clean**: 21 are accepted
today and twelve of them are a real Airflow signing key that is still in this
repository's history. Read the file.

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
