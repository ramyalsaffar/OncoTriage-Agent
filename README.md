# OncoTriage Agent

[![CI](https://github.com/ramyalsaffar/OncoTriage-Agent/actions/workflows/ci.yml/badge.svg)](https://github.com/ramyalsaffar/OncoTriage-Agent/actions/workflows/ci.yml)

OncoTriage Agent reads a cancer patient's medical record — in the FHIR format
electronic health records already speak — and finds the clinical trials that
patient might be eligible for, out of the thousands currently recruiting on
ClinicalTrials.gov. It does this in two moves: first it narrows thousands of
trials down to a handful using search and ranking, then it asks a language
model to read that handful's eligibility criteria one at a time and say, for
each, whether the patient meets it and which piece of their record says so.

The output is a ranked list of candidate trials with a per-criterion audit
trail, not a yes/no answer.

> NOT FOR CLINICAL USE. This is a research and engineering demonstration built
> on synthetic Synthea patient records. Its output is a retrieval and ranking
> suggestion produced by an automated pipeline that includes a large language
> model; it is not medical advice, not a clinical determination of trial
> eligibility, and not a substitute for review by a qualified clinician or the
> trial's own screening process. Eligibility verdicts and match scores are
> unvalidated, may be wrong in either direction, and must be independently
> confirmed against the trial record at ClinicalTrials.gov before any use
> involving a real patient.

That text has one owner, [`oncotriage/constants.py`](oncotriage/constants.py),
and every surface that shows it reads it from there.

---

## Pipeline

Six stages, wired as a LangGraph `StateGraph` in
[`oncotriage/agent/graph.py`](oncotriage/agent/graph.py) over the state defined
in [`agent/state.py`](oncotriage/agent/state.py). Node names below are the
graph's own.

| # | Node | What it does | Module |
|---|---|---|---|
| 1 | `query_expansion` | Expands the primary diagnosis into query terms using the cancer-code registry and the MeSH C04 tree. Deterministic; no model call. | `agent/retrieval.py` |
| 2 | `hybrid_retrieval` | Qdrant-native sparse BM25 (75) + dense `text-embedding-3-small` (100), fused by weighted reciprocal-rank fusion into a pool of 100. Falls back to BM25 alone if the vector channel fails. | `agent/retrieval.py` |
| 3 | `cross_encoder_rerank` | `ncbi/MedCPT-Cross-Encoder` reranks the pool across several queries, fused again and cut to 40. Stable argsort, so ties do not move between runs. | `agent/retrieval.py` |
| 4 | `rule_based_filter` | Deterministic exclusion on MeSH site relevance, cancer stage ordinal, histology, age and sex, plus a two-knob quality gate. Caps the survivors at 15 — the cost ceiling. | `agent/filtering.py` |
| 5 | `llm_classifier_evaluation` | Criterion-level eligibility verdicts from `config.MATCHING_MODEL`, one call per patient-trial pair behind a shared-prefix cache warmup. Malformed replies retry up to three times. | `agent/evaluation.py` |
| 6 | `finalize` | Splits eligible from not-eligible and normalises the verdict vocabulary. | `agent/terminal.py` |

Two more terminal nodes exist and are reached by conditional edges:
`no_candidates` when a stage empties the pool, and `error_handler`, which still
emits a well-formed result rather than raising. Stage 4 running before Stage 5
is the whole cost design — the deterministic filters are what keep the number
of model calls bounded.

[`PIPELINE SEQUENCE.md`](PIPELINE%20SEQUENCE.md) walks the same path in prose,
naming the module that holds each stage.

## Stack

Read from [`pyproject.toml`](pyproject.toml), which is the one dependency list
in this project.

| Component | Choice |
|---|---|
| Orchestration | LangGraph 1.0.10 — `StateGraph`, conditional edges, cyclic retry, error handler |
| Eligibility model | `config.MATCHING_MODEL` (`gpt-5.6-terra`), via the OpenAI SDK |
| Embeddings | OpenAI `text-embedding-3-small` |
| Reranker | `ncbi/MedCPT-Cross-Encoder` via Transformers 4.57.1 / Torch 2.9.0 |
| Sparse retrieval | `Qdrant/bm25` via FastEmbed 0.7.4 |
| Vector database | Qdrant 1.18.0, zero-downtime aliased collections |
| API | FastAPI 0.136.3 + Uvicorn, SlowAPI rate limiting |
| Tool interface | MCP 2.0.0 over stdio — three tools |
| Dashboard | Streamlit 1.46.0 + Plotly, ten tabs |
| Persistence | SQLite — inferences, trial matches, per-run configuration and health |
| Run tracking | `mlflow-skinny` 3.15.1, file-backed |
| Drift detection | SciPy — KS test, PSI, z-score |
| Scheduling | Apache Airflow 3.3.0, **optional extra** (`pip install -e ".[orchestration]"`) |
| Clinical vocabularies | MeSH 2026, ICD-10-CM 2024, SNOMED CT, LOINC, UMLS 2025AB |
| Input format | FHIR R4 Bundle |
| Deployment | Docker Compose (six services) or local; the runtime detects which |

A second Stage 5 provider — Amazon Bedrock — is implemented in
[`agent/bedrock_adapter.py`](oncotriage/agent/bedrock_adapter.py) and is
**off**: `config.MATCHING_PROVIDER` ships `"openai"`. Its go-live checklist is
in that module's docstring.

## Evaluation

**Results are pending.** Retrieval quality, criterion-level agreement,
reproducibility, cost and latency are all measured from a single final campaign
run, and that run has not been made. No figures are published here, and any
number quoted from an earlier run of this project is superseded rather than
provisional.

The measurement apparatus is in the repository and can be read now: the
seven-configuration ablation study with bootstrapped intervals and BH-corrected
Wilcoxon tests ([`oncotriage/ablation/`](oncotriage/ablation/)), the twelve
end-to-end characterization fixtures and their replay gate
([`fixture_replay.py`](fixture_replay.py)), the drift monitors
([`oncotriage/monitoring/drift.py`](oncotriage/monitoring/drift.py)), and the
per-run configuration fingerprint that decides whether two runs may be compared
at all ([`oncotriage/run_fingerprint.py`](oncotriage/run_fingerprint.py)).

## Running it

Only `03- Code/` — this repository — is version-controlled. The patient corpus,
trial index, credentials and results live in sibling directories outside it,
resolved by glob in [`oncotriage/paths.py`](oncotriage/paths.py). **None of them
is included here**, so a fresh clone cannot match a patient; what it can do is
install, import and run the offline test suite.

The clone directory name matters: `code_path` resolves `{project-root}/*Code/`
and raises if more or less than one directory matches.

```bash
mkdir oncotriage && cd oncotriage
git clone https://github.com/ramyalsaffar/OncoTriage-Agent.git "03- Code"

# Keep the virtualenv OUTSIDE the clone, and give it system site packages.
# Both flags matter; see the note under this block.
python3 -m venv --system-site-packages venv
. venv/bin/activate

cd "03- Code"
make install                    # pip install -e .

# Create the empty sibling skeleton the tests expect. It writes no patient
# data, no trial corpus, no database and no credential.
python .github/scripts/provision_ci_paths.py --root ..

# The 80 test files that need no network, no API key, no live server and
# nothing outside this repository.
ONCOTRIAGE_MAIN_PATH=.. python .github/scripts/ci_test_buckets.py --run A
```

Run verbatim from an empty directory on macOS with Python 3.13, `--run A`
reports **80 ran, 0 failed, 0 not run**.

Why those two virtualenv flags. Four of the test files inject a stand-in
through `usercustomize`, which Python imports only when user-site imports are
enabled — and a plain `python -m venv` disables them, so inside one those four
fail, saying so in as many words. `--system-site-packages` re-enables them;
running the interpreter directly, as CI does, works too. And a virtualenv
*inside* the clone is correctly reported by
`tests/test_dockerignore_exclusions.py` as an undeclared virtualenv in the
Docker build context, so keep it in the parent.

With the sibling data tree and credentials in place, the services are:

```bash
python "17- FastAPI Server.py"               # API on :8000, /docs for the schema
streamlit run "21- Streamlit Dashboard.py"   # dashboard on :8501
python mcp_server.py                         # MCP server on stdio
python "25- Batch Runner.py"                 # full-corpus run, checkpointed
make up                                      # all six services under Docker
```

`GET /health`, `GET /pipeline/info`, `POST /match` and `POST /match/file` are
the API surface; `/health` returns 503 while a dependency is missing, so an
empty index reports unhealthy rather than answering with an empty match list.

Commands that spend money say so in their own docstrings.
[`CLAUDE.md`](CLAUDE.md) is the full operational reference — every entry point,
every environment variable, and the reasoning behind each design decision.

## Tests and CI

99 test files. A GitHub-hosted run executes the **80** that need no network, no
key, no spend, no live server and no data outside this repository. The other 19
need a live Qdrant endpoint, the UMLS Metathesaurus, a generated Synthea corpus,
UMLS-derived MeSH crosswalks, or the production inference database — five of
them are additionally a collision matrix that must run one at a time, because
two of the five rewrite source files in place and three more read what those two
write.

```bash
python .github/scripts/ci_test_buckets.py --list   # the classification, with evidence
make serial-tests                                  # the five, in order, one at a time
```

The classification is checked in CI, so a test file added without being
classified fails the build and the gap cannot widen quietly. Nothing that makes
a billed model call is wired into CI.

These are not pytest. Every check runs at module level and the exit code is set
in a `__main__` block, so `pytest tests/` imports each file — running every
check and printing every result — and then reports that it collected nothing.

## Secret scanning

Two layers, and only one of them is a guarantee.

```bash
make hooks          # arm the local pre-commit scan (git config core.hooksPath)
make secret-scan    # the same gate CI runs, over the whole object database
```

The hook is convenience: `--no-verify` walks past it, and a contributor who
never arms it never has it. The guarantee is the `secret-scan` job in
[`.github/workflows/ci.yml`](.github/workflows/ci.yml), which scans **every
object in the git object database**, reachable or not, with two engines —
gitleaks and this project's own
[`staging/secrets_scan.py`](oncotriage/staging/secrets_scan.py).

`make secret-scan` needs the `gitleaks` binary on PATH. Without it the gate
runs one engine, says so in capitals, and exits **2** reporting the six
gitleaks-keyed entries in the accepted table as stale — they are not, they
simply cannot match when the engine that produced them did not run. CI installs
gitleaks and passes `--require-gitleaks`, so a missing binary there is a red
build rather than a quiet single-engine pass.

The range is the object database rather than a commit range because a commit
range cannot be defended. Measured in a clone of this repository: a secret
introduced by an evil merge does not appear even once in
`git log -p --all --full-history`; one committed and then `git rm`'d is gone
from the working tree; one left unreachable by `reset --hard` is matched zero
times by `git rev-list --objects --all`. All three are still `git cat-file`-able
by anyone who clones, and the object-range gate fails on all three.

**Green does not mean clean.** Accepted findings are listed with their reasons
in [`.github/scan-accepted-fingerprints.txt`](.github/scan-accepted-fingerprints.txt),
keyed on blob oid so an entry survives a rebase and can never suppress
different content. Some of them are real credentials still present in this
repository's history and treated as compromised. Read the file.

## Limitations

1. Built and validated on Synthea-generated synthetic patients, which are
   structurally cleaner than real EHR exports. Real-world validation has not
   been done.
2. No physician-annotated ground truth. The evaluation harness compares against
   an LLM judge, which is a weaker standard and is labelled as such wherever it
   reports.
3. Absent data is never treated as disqualifying. That is deliberate and
   conservative, and it means a large share of criteria resolve to
   *not evaluable* rather than to a verdict.
4. ECOG performance status and organ-function thresholds reach the model as
   text rather than being compared numerically in code.
5. Stage 5 determinism is best-effort. The model is called at a fixed
   configuration, but identical inputs are not guaranteed to produce identical
   verdicts, which is why reproducibility is measured rather than assumed.

## Documentation

- [`CLAUDE.md`](CLAUDE.md) — architecture, conventions, and the full change
  record with the reasoning behind each decision.
- [`PIPELINE SEQUENCE.md`](PIPELINE%20SEQUENCE.md) — the pipeline stage by
  stage, and the module holding each.
- [`DOCKER CLEAN BRING-UP.md`](DOCKER%20CLEAN%20BRING-UP.md) — what
  `docker compose down -v` + `up` gives you, and what it does not.
- [`FIXTURE CAPTURE RECORD.md`](FIXTURE%20CAPTURE%20RECORD.md) — provenance of
  the twelve characterization fixtures.

## Licence

See [`LICENSE`](LICENSE). **This is not an open source licence.** It grants
permission to read this repository and reserves everything else; any use beyond
reading needs prior written permission. [`CITATION.cff`](CITATION.cff) carries
the citation metadata.

## Contact

Ramy Alsaffar — <ramyalsaffar@gmail.com>, <ramyalsaffar@yahoo.com>
