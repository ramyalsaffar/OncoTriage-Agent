# Test file number → name mapping (pass 20d-1)

Every note, every commit message and both Word documents reference these files
**by number**. This file is the artefact that makes those references resolvable
after pass 20d-1 moved the component tests into `tests/` and renamed each of
them for what it covers. It is the mapping, not a memory of it.

## The eleven files

| Was | Is | Covers |
|---|---|---|
| `30- Histology Extraction Test.py` | [tests/test_extraction_histology.py](test_extraction_histology.py) | `oncotriage/extraction/histology.py` — negation-aware histology tag extraction |
| `31- MeSH Boost Gate Test.py` | [tests/test_agent_mesh_boost_and_quality_gate.py](test_agent_mesh_boost_and_quality_gate.py) | `agent/retrieval.py`'s MeSH relevance boost and `agent/filtering.py`'s quality gate |
| `32- Pan-Cancer Resolution Test.py` | [tests/test_registries_mesh_pan_cancer_resolution.py](test_registries_mesh_pan_cancer_resolution.py) | `registries/mesh.py` + `agent/mesh_expansion.py` — pan-cancer tree resolution |
| `33- Cancer Code and Stage Extraction Test.py` | [tests/test_registries_cancer_codes_and_stage_extraction.py](test_registries_cancer_codes_and_stage_extraction.py) | `registries/cancer_code_registry.py` and `extraction/stage.py` |
| `34- Cohort Selector Diff.py` | **not a test — unchanged.** Pass 20c-3d converted it to `oncotriage/evaluation/cohort_diff.py`; File 34 is its thin entry point | LEGACY vs CURRENT cohort selector, read only |
| `35- Ablation State Passthrough Test.py` | [tests/test_agent_ablation_flag_passthrough.py](test_agent_ablation_flag_passthrough.py) | `agent/retrieval.py` + `agent/filtering.py` — every ablation flag carries state |
| `36- Logging Contract Test.py` | [tests/test_storage_inference_logging_contract.py](test_storage_inference_logging_contract.py) | `storage/database_logger.py` — what a row must contain and what it must not |
| `37- Retrieval Observability Test.py` | [tests/test_agent_retrieval_observability.py](test_agent_retrieval_observability.py) | `agent/retrieval.py` + `agent/filtering.py` degradation record (item 11b) |
| `38- Birth Date and Demographics Parser Test.py` | [tests/test_fhir_birth_date_and_demographics.py](test_fhir_birth_date_and_demographics.py) | `fhir/parser.py` demographics + `utils.py` partial dates and the age reference date |
| `39- ECOG Performance Status Surfacing Test.py` | [tests/test_fhir_ecog_surfacing.py](test_fhir_ecog_surfacing.py) | `fhir/parser.py` ECOG routing + `agent/patient.py` summary and hash |
| `40- ECOG Logging Test.py` | [tests/test_storage_ecog_logging.py](test_storage_ecog_logging.py) | `storage/database_logger.py` ECOG columns + `agent/terminal.py` provenance |
| `41- ECOG Availability Metric Test.py` | [tests/test_monitoring_ecog_availability_drift.py](test_monitoring_ecog_availability_drift.py) | `monitoring/drift.py` — the ECOG availability metric and its alert |

**THE GROUP IS ELEVEN FILES, NOT THE RANGE THE SEQUENCE DOCUMENT NAMES.** That
document says Files 30 to 44 are test files. That is wrong in two directions and
the correction is part of this pass's record:

- **File 34 is not a test.** It is a read-only comparison tool, and pass 20c-3d
  already converted it to `oncotriage/evaluation/cohort_diff.py` with File 34 as
  a thin entry point. It stays where it is.
- **Files 42, 43 and 44 are tests but are NOT in this pass**, by instruction.
  They mutate files in the repository and belong to `run_serial_tests.py`'s
  collision matrix; converting them is a later pass's work.

## Not in this pass, and why

| File | Status |
|---|---|
| `18- FastAPI Server Test.py` | A test, sitting inside the pipeline numbering. **Needs a live server on `localhost:8000` and COSTS MONEY** — every POST is a live billed Stage 5 call, measured at $0.13–$0.17 per patient. Untouched by any pass so far. Pass 20e. |
| `19- FastAPI Server Batch Test.py` | Same. Also runs `fhir_files[410:412]`, two patients, while its own summary describes a full-corpus run. Pass 20e. |
| `42- Cancer Code Registry Audit Test.py` | Excluded by instruction. In `run_serial_tests.py`'s collision matrix. |
| `43- Cancer Code Registry Audit Negative Control.py` | Excluded by instruction. Plants defects into the registry source in place. |
| `44- Snapshot Date Rot Test.py` | Excluded from conversion, but **its `_SUITES` list was retargeted** — it runs two of the eleven as subprocesses, and the move would otherwise have broken it. That is the only functional edit made to a 42–49 file in this pass. |
| `45-`, `46-` | Fixture capture and replay. Converted in pass 20c-3d; entry points, not component tests. |
| `47-`, `48-`, `49-` | Already package-importing. **File 47 needed one edit**: its `_REPO_PY` read corpus was `_PKG_FILES` plus the top-level `.py` files, so moving eleven readers out of the top level would have made check 2h report package constants only they read as never-read. |

## What changed inside each file, and what did not

**Changed** — bootstrap, imports and paths only:

- The `exec()` of `01- Imports.py` / `02- Utility Functions.py` and the
  `exec_chain([...])` are gone. Each file now carries the same package-import
  block Files 47, 48 and 49 carry, **looking one directory up**, because these
  files sit in `tests/` and the package sits beside `tests/`, not inside it.
- Every name that used to arrive through the shared exec namespace is imported
  from the module that defines it. That includes the third-party names File 01
  supplied verbatim — `np`, `pd`, `Path`, `json`, `sqlite3`, `date`,
  `QdrantClient`, `SparseVector`.
- **Every parsed module's path now comes from that module's own `__file__`**,
  never from a directory guess. `os.path.join(_code_dir, "oncotriage", ...)` was
  correct only while these files sat beside the package; from `tests/` every one
  of them would have been one level off. A module's own `__file__` also cannot
  name a copy of the module this process did not import.
- Four files (36, 37, 38, 40) used to `exec("14- Database Logger.py")` *after*
  rebinding `inferences_path`, so that the File 14 shim's `log_inference`
  wrapper picked the rebound value up through `globals().get(...)`. They import
  `oncotriage.storage.database_logger` directly now. **That mechanism is
  therefore retired, and each file says so** rather than leaving a comment
  claiming two protections while one is inert. The surviving protection is the
  one they already relied on: every call passes `db_path` explicitly and asserts
  on the path the writer returns, with a standing non-degeneracy check that the
  package default is production and is not the scratch path.

**Two diffs fall outside import / path / bootstrap and are called out rather
than folded in:**

1. **`tests/test_fhir_birth_date_and_demographics.py` section 3** set
   `DATA_SNAPSHOT_DATE` in its own globals and required
   `get_age_reference_date()` to raise. That worked because File 02's wrapper
   passed `globals().get("DATA_SNAPSHOT_DATE")` down. The package function reads
   `config.DATA_SNAPSHOT_DATE` **at call time** — its own docstring names that
   as the supported way to patch it — so the bad value is set on the config
   module now. Same call shape, same four values, same raise, same count.
2. **`tests/test_monitoring_ecog_availability_drift.py` section 8b** asserted
   that File 20's shim re-exports nine names, by comparing *this file's*
   globals against the package module. That was meaningful only while this file
   got its names by exec-chaining the shim. Left alone it would have compared an
   imported name with itself: **true by construction, ten checks that can never
   fail**. The shim is exec'd into a throwaway namespace now and that namespace
   is inspected — which tests the shim itself rather than testing it through one
   caller, and keeps working now that File 41 (its last exec-chain consumer) no
   longer chains it. Shown to fail out of band on 2026-08-06: stripping
   `ks_test_drift` from an in-memory copy of the shim's source gives 111 passed,
   1 failed, the other nine still passing, shim sha256 unchanged.

**Unchanged** — no assertion, threshold, seeded value or expected result was
touched. Item 22 owns the suite and its assertions.

## Pass counts, before and after

Recorded before the move (from the numbered files at `HEAD`) and required to be
identical after.

| File | Before | After |
|---|---|---|
| 30 → `test_extraction_histology` | 103 | 103 |
| 31 → `test_agent_mesh_boost_and_quality_gate` | 54 | 54 |
| 32 → `test_registries_mesh_pan_cancer_resolution` | 58 | 58 |
| 33 → `test_registries_cancer_codes_and_stage_extraction` | 136 | 136 |
| 35 → `test_agent_ablation_flag_passthrough` | 39 | 39 |
| 36 → `test_storage_inference_logging_contract` | 79 | 79 |
| 37 → `test_agent_retrieval_observability` | 103 | 103 |
| 38 → `test_fhir_birth_date_and_demographics` | 172 | 172 |
| 39 → `test_fhir_ecog_surfacing` | 105 | 105 |
| 40 → `test_storage_ecog_logging` | 104 | 104 |
| 41 → `test_monitoring_ecog_availability_drift` | 112 | 112 |
| **total** | **1065** | **1065** |

Every one exits 0 with zero failures, before and after.

## Running them

```bash
# From 03- Code/. Names no longer contain spaces, so no quoting is needed.
python tests/test_extraction_histology.py
python tests/test_fhir_ecog_surfacing.py        # needs the scratch corpus from 04-
```

**These are NOT pytest tests.** They are procedural scripts: every check runs at
module level and the exit code is set in a `__main__` block. `pytest tests/`
imports each module — which runs every check and prints the results — and then
reports "no tests collected", exit 5. That is a non-zero exit, so it cannot read
as a false green, but it is not how to run them. The `test_` prefix is for
discovery and for whatever item 22 decides; it is not a claim of pytest
compatibility.

**`tests/` is not a package.** There is no `__init__.py`, it is not in
`pyproject.toml`'s `packages` list (which is explicit, never auto-discovered),
and File 47's subpackage scan walks `oncotriage/` only — so a wheel build is
unaffected in both directions.
