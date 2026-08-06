# Package Split Test
####################

"""
Proves the item 20c package split.

WHAT WAS CHANGED
----------------
Files 01, 02, 03, 08, 09 and 10 stopped being the definitions and became
re-export shims over a real Python package:

    oncotriage/settings.py    env-var names, path resolution
    oncotriage/paths.py       IS_DOCKER, _glob_one, every path variable,
                              load_env_keys
    oncotriage/constants.py   SYSTEM_KEY_ABSENT / SYSTEM_KEY_UNRECOGNIZED
    oncotriage/config.py      every tunable + LAZY client/keys accessors
    oncotriage/utils.py       cost, retry, partial dates, exec_chain, caffeinate

    oncotriage/registries/cancer_code_registry.py   File 08, whole
    oncotriage/registries/mesh.py                   File 09's filter half
    oncotriage/registries/mesh_crosswalk_build.py   File 09's five offline
                                                    builders
    oncotriage/extraction/negation.py               _is_negated, the one name
                                                    File 10's two halves shared
    oncotriage/extraction/stage.py                  File 10 to line 698
    oncotriage/extraction/histology.py              File 10 from line 699

Pass 20c-3a added six more and turned four numbered files into thin entry points:

    oncotriage/embedding.py                 THE ONE construction site for the
                                            FastEmbed BM25 sparse model. It was
                                            built in three independent places --
                                            File 11 at index time, deps at query
                                            time, File 12 inside its own smoke
                                            test -- for the two halves of one
                                            job. See check 2f.
    oncotriage/fhir/clean.py                File 05, whole. File 05 keeps a full
                                            re-export shim: File 34 chains it.
    oncotriage/fhir/generate.py             File 04, whole
    oncotriage/fhir/explore.py              File 06, whole
    oncotriage/retrieval/indexer.py         File 11, whole
    oncotriage/retrieval/index_validator.py File 12, whole

    Files 04, 06, 11 and 12 have NO exec bootstrap at all now -- nothing in the
    repository chains them, so there is no shared namespace to feed.

Pass 20c-2b added two more, and corrected one thing pass 2a shipped:

    oncotriage/fhir/parser.py               File 07, whole
    oncotriage/storage/database_logger.py   File 14, whole -- with log_inference
                                            taking db_path and
                                            _resolve_primary_cancer calling
                                            load_registry()

    oncotriage/paths.py   resolution is LAZY now. It used to run at import, so
                          `import oncotriage.config` raised on any machine
                          without the sibling directory tree. See check 2b.

THE CYCLE THAT MADE THIS NON-TRIVIAL
------------------------------------
    '02- Utility Functions.py' read PRICING_CONFIG, COLLECTION_NAME,
                               qdrant_client and DATA_SNAPSHOT_DATE from File 03
    '03- Config.py'            called load_env_keys(), from File 02, at line 194

Under exec() into one shared namespace both directions resolve at call time and
nothing complains. As modules it is an ImportError. load_env_keys moved out of
the pair -- to oncotriage.settings in pass 20c-1, and to oncotriage.paths in
pass 20c-2a, beside the keys_path it defaults to, which is what let its own
import stop being deferred into a function body.

WHAT THIS FILE CHECKS, and how each check could fail
----------------------------------------------------
  1. THE CYCLE IS GONE. oncotriage.config and oncotriage.utils import cleanly in
     BOTH orders, from a directory that is not the code directory. Structurally,
     config.py's AST contains no reference to oncotriage.utils anywhere --
     module level or inside a function body.

     NEGATIVE CONTROL, and it changed what this file claims. A COPY of the
     package with `from oncotriage.utils import get_model_cost` added back to
     config.py is caught by the structural check, and `import oncotriage.utils`
     against it dies with "most likely due to a circular import" -- but
     `import oncotriage.config` against the same copy SUCCEEDS. A reintroduced
     cycle is order-dependent, so the import-order pair is a smoke test and the
     STRUCTURAL check is the actual guard. Both halves are asserted, including
     the one that is inconvenient.

  1b. NO ONCOTRIAGE MODULE IMPORTS ANOTHER FROM A FUNCTION BODY. An ast walk of
     every file in the package, ignoring third-party imports -- File 08's
     `import icd10` inside _build_icd10_cancer_sets() is deliberate and must
     stay. NEGATIVE CONTROL: a copy with a deferred package import added to
     settings.resolve_keys_path() is caught, AND is shown to still import
     cleanly, which is the whole reason a static scan is needed for this one.

  2. IMPORTING TOUCHES NOTHING LIVE. A subprocess replaces socket.socket with a
     class that raises on construction, and socket.create_connection,
     sqlite3.connect, builtins.open and io.open with functions that raise, then
     imports all thirteen package modules. Proved by patching, not by reading the
     source. Every trap is fired afterwards and must raise, so a run where the
     patches silently did nothing fails instead of passing vacuously. The `open`
     traps arrived with oncotriage.registries.mesh, whose load_mesh_filter()
     reads four JSON lookups and must do it in a function.

  2b. PATH RESOLUTION IS LAZY. A subprocess with ONCOTRIAGE_MAIN_PATH pointed at
     a directory that does not exist must still `import oncotriage.config` and
     read MAX_WORKERS out of it, and must resolve NO path at import — the fix
     for a defect pass 20c-2a shipped, where importing config globbed the whole
     sibling tree and raised on any machine that did not have it. Section 2
     could not see this: glob.glob() uses os.scandir, not open(). NON-DEGENERATE
     BOTH WAYS: reading a path against the unreachable root must still raise
     with a message naming the variable, and the same read against the real root
     must return a directory that exists.

  3. THE CLIENT FACTORIES ARE LAZY AND CACHED. Counting fakes are installed over
     oncotriage.config.OpenAI and .QdrantClient before any call. Construction
     count must be 0 after import (lazy), 1 after the first call, still 1 after
     the second (cached), and the two returned objects must be the same object.
     Identity alone would also hold for a module-level singleton built at
     import, which is exactly what this pass removed -- the 0-after-import count
     is what separates them.

  4. get_age_reference_date RESOLVES DATA_SNAPSHOT_DATE BY IMPORT AND STILL
     RAISES. A COPY of the package has its config.py rewritten to a partial date
     ("2026-08"); a subprocess against the copy must raise ValueError naming the
     constant, never fall back to today(). The copy is then rewritten back and
     shown to return date(2026, 8, 3) again. Nothing is edited in place.

  5. NO NAME WAS DROPPED. Two inventories, because the two passes need different
     evidence. Files 01/02/03 are checked against an ast-derived list from
     commit 3780ba1. Files 08/09/10 are checked against a RUNTIME-derived list:
     each was exec'd into a throwaway namespace before the move and every
     binding recorded, because File 08 assigns _seen_canonical at module level
     and then deletes it, and an ast list would have re-exported a name that
     never existed. Both directions are asserted -- nothing missing, nothing
     added.

  2f. EXACTLY ONE CONSTRUCTION SITE FOR THE BM25 SPARSE MODEL, counted by ast
     over every package file, with a negative control that plants a second one
     in a copy and shows the detector finds it. Both sides of the model -- the
     indexer that writes the document vectors and the agent that encodes the
     query scored against them -- must reach the same accessor. Two independent
     loaders of a token-ID vocabulary is a silent retrieval-quality failure: the
     dot product still computes, nothing raises, no counter moves.

  2g. NO FUNCTION-LOCAL SHADOWS A MODULE-LEVEL IMPORT. An ast scan over every
     package file. This caught two real defects during pass 3a -- a `config`
     local in stage1_index_health() and an `embedding` loop variable in
     _flush_embed_buffer() -- each of which would have turned a module attribute
     read into UnboundLocalError at RUN time, invisibly to any import test.
     NEGATIVE CONTROL: a copy with the module-level `import config` put back is
     shown to be caught.

  5b. THE FILE 10 SPLIT HAS EXACTLY ONE SHARED NAME. Re-derived against the
     shipped modules rather than asserted in a comment: stage.py and
     histology.py must reference nothing the other defines, and the one name
     they both reach for must be _is_negated, out of negation.py.

  5c. THE LAZY DEPENDENCY PROXY ANSWERS FOR WHAT IT WRAPS. _LazyAgentDependency
     forwarded __getattr__ and __call__ only, so bool(), len(), iter(), `in`,
     `==`, hash() and repr() answered about the WRAPPER. == in particular
     answered False when the wrapped object WAS the operand, which is the exact
     question a fixture harness asks of this seam. Demonstrated against a copy
     of the class with the six delegations stripped, which must get them wrong.

  6. THE THREE LATE-BINDING WRAPPERS STILL BIND LATE. File 02's shim is exec'd
     into a throwaway namespace holding a fake PRICING_CONFIG, a stub Qdrant
     client and a DATA_SNAPSHOT_DATE that differs from the package's. All three
     wrappers must use the namespace's values, because '36- Logging Contract
     Test.py', '37- Retrieval Observability Test.py', '38- Birth Date and
     Demographics Parser Test.py', '45- Fixture Capture.py' and
     '46- Fixture Replay.py' all depend on exactly that.

WHY THIS FILE DOES NOT EXEC-CHAIN 01 AND 02
-------------------------------------------
Every other test file starts by exec'ing "01- Imports.py". This one must not:
File 01 imports torch, transformers, streamlit and langgraph into the process,
and check 2 asserts those are ABSENT after a package import. A test of import
purity that first imports everything would be measuring its own bootstrap. So
this file imports the standard library it needs directly, and every check that
needs the package runs in a SUBPROCESS.

NO NETWORK, NO MODEL, NO DATABASE, NO API KEY. Every check here runs without
credentials. That is deliberate: it is the only test in the suite that can run
on a fresh checkout before a .env exists.

Run from terminal (or F5 in Spyder):
    python "47- Package Split Test.py"

Exit codes:
    0 -- every check passed
    1 -- at least one check failed
"""

import ast
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date


# Item 20a: this file sits in the code directory, so __file__ locates it with
# no hardcoded path. __file__ is bound when the file is run as a script (every
# documented entry point for it) and when Spyder runfile()s it. In a bare
# interactive paste it is not bound, and the working directory is the only
# remaining candidate -- taken, but announced, never silently.
if "__file__" in globals():
    _code_dir = os.path.dirname(os.path.abspath(__file__)) + os.sep
else:
    _code_dir = os.getcwd() + os.sep
    print(f"[Bootstrap] __file__ unbound; using the working directory as the code directory: {_code_dir}")

_PKG_DIR = os.path.join(_code_dir, "oncotriage")


#------------------------------------------------------------------------------


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================
# Same shape as Files 33, 42, 43 and 44: record every outcome, never abort on
# the first failure, exit non-zero at the end.

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label: str, actual, expected) -> None:
    """Assert equality, record the outcome, never abort the run."""
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          expected: {expected}\n          actual:   {actual}")
        print(f"  FAIL  {label}")


def fail(label: str, detail: str) -> None:
    """Record an outright failure that is not an equality comparison."""
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {detail}")
    print(f"  FAIL  {label}")


#------------------------------------------------------------------------------


# ===========================================================================
# THE NAMES FILES 01, 02 AND 03 DEFINED BEFORE ITEM 20c
# ===========================================================================
# Extracted from the three files at commit 3780ba1 -- the last commit before
# this pass -- by walking each module's AST for top-level FunctionDef, ClassDef,
# Assign, AnnAssign, Import and ImportFrom targets, including the targets inside
# 01's `if IS_DOCKER:` branch.
#
# Written down rather than recomputed from git, because the point is to pin what
# the contract WAS. A test that re-derives the list from whatever HEAD happens
# to be would agree with the code by construction, which is the defect CLAUDE.md
# records against File 42's boundary assertions.

_PRE_20C_NAMES = {
    "01- Imports.py": [
        "APIConnectionError", "AliasOperations", "Annotated", "Any",
        "AutoModelForSequenceClassification", "AutoTokenizer", "BM25Okapi",
        "BaseModel", "Counter", "CreateAlias", "CreateAliasOperation",
        "CrossEncoder", "DeleteAlias", "DeleteAliasOperation", "Dict",
        "Distance", "END", "ET", "FastAPI", "File", "FrozenSet", "HTTPException",
        "IS_DOCKER", "InternalServerError", "JSONResponse", "Limiter", "List",
        "Modifier", "OpenAI", "Optional", "Path", "PayloadSchemaType",
        "PointStruct", "QdrantClient", "RateLimitError", "RateLimitExceeded",
        "Request", "START", "SYSTEM_KEY_ABSENT", "SYSTEM_KEY_UNRECOGNIZED",
        "SearchRequest", "Set", "SparseIndexParams", "SparseTextEmbedding",
        "SparseVector", "SparseVectorParams", "StateGraph", "ThreadPoolExecutor",
        "Tuple", "TypedDict", "UnexpectedResponse", "UploadFile", "VectorParams",
        "_caffeine_mod", "_glob_one", "_load_path_settings", "_main_path_source",
        "_rate_limit_exceeded_handler", "airflow_path", "argparse",
        "asynccontextmanager", "asyncio", "builtins", "checkpoint_path",
        "code_path", "data_MeSH_path", "data_fhir_path", "data_path",
        "data_patient_path", "data_trial_path", "date", "datetime", "defaultdict",
        "get_remote_address", "glob", "go", "hashlib", "httpx", "importlib",
        "inferences_path", "json", "keys_path", "ks_2samp", "load_dotenv",
        "logging", "main_path", "make_subplots", "nest_asyncio", "np", "os",
        "path_settings", "pd", "plt", "px", "random", "re", "relativedelta",
        "requests", "requirements_path", "result_ablation_path",
        "result_fhir_explore_path", "results_path", "retry",
        "retry_if_exception_type", "shutil", "sns", "sqlite3", "st",
        "stop_after_attempt", "subprocess", "sys", "tempfile", "threading",
        "time", "timezone", "torch", "tqdm", "traceback", "uvicorn",
        "wait_exponential",
    ],
    "02- Utility Functions.py": [
        "CaffeinateSession", "PARTIAL_DATE_ANCHOR_DAY", "PARTIAL_DATE_ANCHOR_MONTH",
        "PARTIAL_DATE_DEGRADATIONS", "UnknownModelPricingError",
        "_PARTIAL_DATE_PATTERNS", "deduplicate_by_display", "exec_chain",
        "get_age_reference_date", "get_model_cost", "load_env_keys",
        "parse_partial_date", "qdrant_retry", "resolve_qdrant_collection",
    ],
    "03- Config.py": [
        "ABLATION_DESCRIPTIVE_METRICS", "ABLATION_FDR_ALPHA", "ABLATION_MIN_PAIRED",
        "ABLATION_OUTCOME_METRICS", "ABLATION_POWER_TARGET", "AIRFLOW_DAG_SCHEDULE",
        "BASELINE_WINDOW_DAYS", "BATCH_SIZE", "BM25_RETRIEVAL_SIZE",
        "CHARS_PER_TOKEN", "CHECKPOINT_FILENAME", "COHORT_MANIFEST_FILENAME",
        "COHORT_MANIFEST_FLUSH_EVERY", "COLLECTION_NAME", "COMPARISON_WINDOW_DAYS",
        "DATA_SNAPSHOT_DATE", "ECOG_MISSINGNESS_FRACTION", "ECOG_SCORE_DISTRIBUTION",
        "ECOG_UNAVAILABLE_RATE_THRESHOLD", "EMBEDDING_DIM", "EMBEDDING_MODEL",
        "EMBEDDING_REQUEST_TIMEOUT", "EMBEDDING_REQUEST_TIMEOUT_SECONDS",
        "ENABLE_RATE_LIMITING", "EXPANSION_TEMPERATURE", "KS_TEST_THRESHOLD",
        "MATCHING_MAX_TOKENS", "MATCHING_MODEL", "MATCHING_OUTPUT_SPLIT_FRACTION",
        "MATCHING_OUTPUT_TOKENS_PER_TRIAL", "MATCHING_REASONING_EFFORT",
        "MATCHING_REQUEST_TIMEOUT", "MATCHING_REQUEST_TIMEOUT_SECONDS",
        "MATCHING_SEED", "MATCHING_TEMPERATURE", "MAX_GPT4O_RETRIES",
        "MAX_TRIALS_FOR_EVALUATION", "MAX_TRUNCATION_SPLITS", "MAX_VARIANT_TERMS",
        "MAX_WORKERS", "MESH_BOOST_DIRECT_FLOOR", "MESH_BOOST_DIRECT_FRACTION",
        "MESH_BOOST_PAN_FLOOR", "MESH_BOOST_PAN_FRACTION", "MIN_SAMPLES_BASELINE",
        "MIN_SAMPLES_COMPARISON", "OPENAI_SDK_MAX_RETRIES", "PRICING_CONFIG",
        "PSI_BINS", "PSI_THRESHOLD", "Project_Name", "QUALITY_THRESHOLD_PERCENTILE",
        "RATE_LIMIT", "RERANK_SCORE_THRESHOLD", "RESAMPLE_COUNT", "RESAMPLE_SEED",
        "RESULTS_FILENAME", "RETRY_BASE_DELAY", "RRF_POOL_SIZE",
        "SDK_DEFAULT_CONNECT_TIMEOUT_SECONDS", "TOP_K_CANDIDATES",
        "VECTOR_RETRIEVAL_SIZE", "Z_SCORE_THRESHOLD", "_sdk_default_timeout",
        "_structured_timeout", "keys", "openai_api_key", "openai_client",
        "qdrant_api_key", "qdrant_client", "qdrant_url", "trial_dict",
    ],
}

# The counts these lists must have. Stated separately so that a list truncated
# by a bad edit fails here rather than passing a subset comparison silently --
# CLAUDE.md's rule about assertions that can be satisfied by a degenerate value.
_PRE_20C_COUNTS = {"01- Imports.py": 120, "02- Utility Functions.py": 14, "03- Config.py": 72}


# ===========================================================================
# THE NAMES FILES 08, 09 AND 10 DEFINED BEFORE PASS 2a
# ===========================================================================
# NOT ast-derived. RUNTIME-derived: each file was exec'd into a throwaway
# namespace with its handful of free names pre-seeded, and every resulting
# binding recorded. An ast walk would have been wrong in both directions:
#
#   TOO FEW   twenty of File 08's names and six of File 10's are ANNOTATED
#             assignments (`_SNOMED_PRIMARY: FrozenSet[str] = ...`). A
#             `grep "NAME ="` misses every one, and ast.Assign alone misses
#             them too -- they are ast.AnnAssign.
#   TOO MANY  File 08 assigns _seen_canonical at module level and then DELETES
#             it, along with _idx / _code / _name, in a globals().pop() cleanup
#             loop. It is not part of the surface and re-exporting it would
#             have invented a name that never existed at runtime.
#
# The same loop leaves `_var` itself bound to the string '_seen_canonical'.
# That leak is real and it is in the list below, because the list is a record of
# what File 08 BOUND before pass 2a and that is what it bound. Pass 2b removed
# the leak; the removal is recorded in _PASS_2B_DROPPED under the list rather
# than by editing the list, because a pinned historical inventory that gets
# rewritten every time the code changes is not pinned to anything.
_PRE_2A_RUNTIME_NAMES = {
    '08- Cancer Code Registry.py': [
        'CancerCodeRegistry', 'OncologyLabRegistry',
        '_CANCER_CLASSIFICATION_COUNTS', '_CANCER_DISPLAY_TERMS',
        '_CANONICAL_ORDER', '_CLINICAL_STATUS_PRIORITY',
        '_EXCLUDE_VERIFICATION', '_ICD10_ALPHA_NON_INVASIVE',
        '_ICD10_ALPHA_PRIMARY', '_ICD10_ALPHA_SECONDARY',
        '_ICD10_CONSULT_KEYS', '_ICD10_C_BLOCK_MAX',
        '_ICD10_C_SECONDARY_HI', '_ICD10_C_SECONDARY_LO',
        '_ICD10_D_NEOPLASM_BLOCK_MAX', '_ICD10_SEED_PRIMARY',
        '_LAB_REGISTRY', '_NON_INVASIVE_DISPLAY_TERMS', '_ONCOLOGY_LOINC',
        '_ONCOLOGY_LOINC_CODES', '_REGISTRY', '_REGISTRY_LOCK',
        '_SECONDARY_DISPLAY_TERMS', '_SNOMED_CONSULT_KEYS',
        '_SNOMED_PRIMARY', '_SNOMED_SECONDARY', '_build_icd10_cancer_sets',
        '_var', 'get_cancer_classification_stats', 'load_lab_registry',
        'load_registry', 'logger', 'reset_cancer_classification_stats',
    ],
    '09- MeSH Cancer Site Relevance Filter.py': [
        'MeSHCancerFilter', 'PAN_CANCER_TREE_MAX_DEPTH',
        'build_all_lookups', 'build_icd10_to_mesh_crosswalk',
        'build_mesh_lookup', 'build_snomed_to_mesh_crosswalk',
        'build_umls_synonym_crosswalk', 'load_mesh_filter',
        'specific_cancer_trees',
    ],
    '10- Structured Eligibility Extractor.py': [
        '_ADENOCARCINOMA_RE', '_CLAUSE_BOUNDARIES', '_EXCLUSIVE_PAIRS',
        '_HISTOLOGY_EXTRACTION_COUNTS', '_HISTOLOGY_SUFFIX_WINDOW',
        '_LOCALLY_ADVANCED_RE', '_LUNG_CONTEXT_RE', '_METASTATIC_RE',
        '_NEGATION_LOOKBACK', '_NEGATION_PREFIXES', '_NEGATION_SUFFIXES',
        '_NEUROENDOCRINE_RE', '_NON_METASTATIC_RE', '_NON_MORPH_LOOKBACK',
        '_NON_MORPH_PREFIX_RE', '_NON_ONCOLOGY_CONTEXT_WINDOW',
        '_NON_ONCOLOGY_STAGE_CONTEXT_RE', '_NON_SMALL_CELL_RE',
        '_NSCLC_ABBREV_RE', '_PATIENT_STAGE_RE', '_RANGE_RE',
        '_SCLC_ABBREV_RE', '_SINGLE_RE', '_SMALL_CELL_RE',
        '_SNOMED_DISPLAY_STAGE_RE', '_SQUAMOUS_RE', '_STAGE_ALT',
        '_STAGE_EXTRACTION_COUNTS', '_STAGE_FULL_RANGE_MIN_CEILING',
        '_STAGE_MAX_ORDINAL', '_STAGE_MIN_ORDINAL', '_STAGE_ORDINAL',
        '_TRACHEAL_RE', '_collect_stage_ordinals',
        '_extract_accepts_metastatic', '_extract_histology_tags',
        '_extract_stage_from_text',
        '_extract_stage_upper_bound_from_exclusion', '_find_exclusive_pair',
        '_has_affirmative_match', '_has_conflict', '_is_full_range_span',
        '_is_histology_negated', '_is_negated', '_is_non_oncology_stage',
        '_stage_negated', 'enrich_histology_tags',
        'enrich_structured_eligibility', 'extract_patient_histology',
        'extract_patient_stage', 'get_histology_extraction_stats',
        'get_stage_extraction_stats', 'is_histology_mismatch',
        'is_stage_mismatch', 'reset_histology_extraction_stats',
        'reset_stage_extraction_stats',
    ],
}

_PRE_2A_COUNTS = {
    '08- Cancer Code Registry.py': 33,
    '09- MeSH Cancer Site Relevance Filter.py': 9,
    '10- Structured Eligibility Extractor.py': 56,
}


# THE ONLY NAMES PASS 2b IS ALLOWED TO REMOVE FROM THE LIST ABOVE.
#
# Pass 2a's contract was "nothing File 08 defined disappears", which is why the
# shim re-exported three names nothing wants. Pass 2b removes them, and states
# the removal here instead of quietly shortening the inventory:
#
#   _REGISTRY, _LAB_REGISTRY   the module's private singleton slots. Re-exported
#                              they are SNAPSHOTS taken at shim load — None,
#                              permanently, whatever load_registry() later
#                              builds — so they read as "no registry yet" and
#                              can never read as anything else. Nothing consumes
#                              them; File 13's only mention is an assignment
#                              that shadows the name.
#   _var                       a leaked loop variable. The module's cleanup loop
#                              now names it, so it does not exist to re-export.
#
# Checked in BOTH directions below: each of these must be genuinely absent from
# the shim's namespace (so the exception is exercised rather than declared), and
# every other pre-2a name must still be present. A fourth name going missing is
# still a failure.
_PASS_2B_DROPPED = {
    '08- Cancer Code Registry.py': {'_REGISTRY', '_LAB_REGISTRY', '_var'},
    '09- MeSH Cancer Site Relevance Filter.py': set(),
    '10- Structured Eligibility Extractor.py': set(),
    '07- FHIR Parser.py': set(),
    '14- Database Logger.py': set(),
    '13- LangGraph Agent.py': set(),
    # Pass 3a drops nothing from File 05. Every one of the fourteen names it
    # bound is still bound, including the three that are now resolved eagerly
    # HERE from accessors that are lazy in the package.
    '05- FHIR Clean Data.py': set(),
}


# ===========================================================================
# THE NAMES FILE 05 DEFINED BEFORE PASS 3a
# ===========================================================================
# Same runtime method as every block above: File 05 was exec'd at commit aa1bddf
# into a throwaway namespace, after the same base chain its own bootstrap runs
# (01, 02, 03 raw, then exec_chain of 07 and 08), and every name it added was
# recorded.
#
# FILE 05 IS THE ONLY ONE OF THE FIVE FILES CONVERTED IN PASS 3a THAT KEEPS A
# SHIM, so it is the only one with an inventory to check. Files 04, 06, 11 and 12
# have no chain consumer anywhere in the repository -- every top-level name each
# of them defines was grepped against every .py, .md, .toml and .yml in the tree,
# and the only hits are prose and unrelated same-named locals other files define
# for themselves. They became thin entry points, so there is no shared-namespace
# surface left to pin.
#
# Three of the fourteen are BOOTSTRAP LEFTOVERS rather than definitions --
# _bootstrap and _fh from the three-file exec loop, _code_dir from the __file__
# derivation. They are here because they were bound, and the shim keeps the same
# bootstrap block, so it still binds them.
#
# PATIENTS_DIR, _MANIFEST_PATH and _CANCER_REGISTRY are in this list and must
# still be bound, and that is the load-bearing part: in the package they became
# patients_dir(), manifest_path() and cancer_registry(), lazy and cached, because
# a package module may not resolve a glob or build the ICD-10-CM registry at
# import. The shim CALLS all three at load, so a chain caller sees the same
# strings and the same registry object it always did -- and
# "34- Cohort Selector Diff.py" reads _CANCER_REGISTRY straight out of this
# namespace at three separate lines.
_PRE_3A_RUNTIME_NAMES = {
    '05- FHIR Clean Data.py': [
        'CAP', 'PATIENTS_DIR', 'RANDOM_SEED', '_CANCER_REGISTRY',
        '_DELETION_COUNTS', '_MANIFEST_PATH', '_bootstrap', '_code_dir',
        '_delete_manifested', '_fh', '_write_manifest',
        'filter_cancer_patients_inplace', 'has_cancer_diagnosis',
        'patient_death_status',
    ],
}

_PRE_3A_COUNTS = {'05- FHIR Clean Data.py': 14}

# NOTHING is added to File 05's shim surface. The three accessors it needs come
# in under private aliases, are called once, and are then DELETED -- the same
# bind-then-remove pattern File 08's cleanup loop uses -- so a name this file
# adds cannot be silently picked up by the next file in a chain.
_PASS_3A_ADDED = {'05- FHIR Clean Data.py': set()}


# ===========================================================================
# THE NAMES FILE 13 DEFINED BEFORE PASS 2c
# ===========================================================================
# Same method, and for File 13 it is the only method that could work at all.
#
# File 13 CHAINS 03, 08, 09 and 10 in its own bootstrap, so exec'ing it into a
# throwaway namespace produces 402 bindings, 315 of which belong to the chained
# files. The list below is the DIFFERENCE: the same base chain was exec'd into
# one namespace, File 13 into another, and the names File 13 added recorded.
# An ast walk over File 13 alone would have missed that entirely and reported
# names the chain provides as File 13's own.
#
# Six are ANNOTATED assignments (_ICD10_RELEVANT_BLOCKS,
# _SNOMED_RELEVANT_COMORBIDITIES, _IRRELEVANT_CONDITION_KEYWORDS,
# _IRRELEVANT_MEDICATION_KEYWORDS, _LAB_UNIT_CONVERSIONS and _EMPTY_BOOST_STATS
# -- the last is a plain Assign, the other five are AnnAssign), which
# ast.Assign alone does not see and `grep "NAME ="` misses.
#
# Three are BOOTSTRAP LEFTOVERS rather than definitions: _bootstrap and _fh from
# the two-file exec loop, and _code_dir from the __file__ derivation. They are
# in the list because they were bound, and the shim keeps the same bootstrap
# block so it still binds them.
#
# Recorded with ONCOTRIAGE_DEFER_LOCAL_MODELS=1 so the extraction did not load
# MedCPT. That changes no binding: medcpt_tokenizer, medcpt_model and
# _bm25_query_model are bound on both branches of File 13's line 414 `if`.
_PRE_2C_RUNTIME_NAMES = {
    '13- LangGraph Agent.py': [
        'CHANNEL_ABLATED', 'CHANNEL_EMPTY_QUERY', 'CHANNEL_FAILED',
        'CHANNEL_OK', 'DEFER_LOCAL_MODELS_ENV', 'EXPANSION_PATH_FALLBACK',
        'EXPANSION_PATH_MESH', 'FINISH_REASON_LENGTH',
        'GENOMIC_VARIANT_LOINC', 'MESH_FILTER_APPLIED',
        'MESH_FILTER_SKIP_ABLATED', 'MESH_FILTER_SKIP_NO_FILTER',
        'MESH_FILTER_SKIP_NO_TREES', 'MESH_RESOLUTION_NO_CONDITIONS',
        'MESH_RESOLUTION_NO_FILTER', 'MatchingModelMismatchError',
        'NOT_EVALUABLE_MODEL_OMITTED', 'NOT_EVALUABLE_SPLIT_BUDGET',
        'NOT_EVALUABLE_TRUNCATION_FLOOR', 'RERANK_RRF_K',
        'RETRIEVAL_CHANNELS', 'RUN_TEST_ON_EXECUTE',
        'TERMINAL_NODE_ERROR', 'TERMINAL_NODE_FINALIZE',
        'TERMINAL_NODE_NO_CANDIDATES', 'TrialMatchState',
        '_BM25_PUNCT_PATTERN', '_CANCER_REGISTRY', '_DEFER_LOCAL_MODELS',
        '_DeferredLocalModel', '_EMPTY_BOOST_STATS', '_EmptySparseQuery',
        '_ICD10_RELEVANT_BLOCKS', '_IRRELEVANT_CONDITION_KEYWORDS',
        '_IRRELEVANT_MEDICATION_KEYWORDS', '_LAB_REGISTRY',
        '_LAB_UNIT_CONVERSIONS', '_MESH_FILTER', '_NOT_EVALUABLE_REASONS',
        '_SNOMED_RELEVANT_COMORBIDITIES', '_VARIANT_TEXT_PATTERN',
        '_bm25_query_model', '_bootstrap', '_build_trials_text',
        '_classify_condition_relevance', '_classify_medication_relevance',
        '_code_dir', '_create_patient_summary', '_empty_mesh_resolution',
        '_fh', '_is_icd10_relevant', '_normalize_lab_unit',
        '_pipeline_provenance', '_print_match_detail', '_split_in_half',
        '_unevaluable_entry', 'apply_mesh_relevance_boost',
        'apply_quality_gate', 'build_bm25_index_from_qdrant',
        'build_initial_state', 'build_matching_graph',
        'call_matching_model', 'compute_patient_hash',
        'display_match_results', 'estimate_output_tokens',
        'expand_query_from_mesh', 'extract_genomic_variant_terms',
        'format_mesh_resolution', 'get_embedding',
        'match_patient_to_trials', 'medcpt_model', 'medcpt_score_pairs',
        'medcpt_tokenizer', 'node_cross_encoder_rerank',
        'node_error_handler', 'node_finalize', 'node_gpt4o_evaluation',
        'node_hybrid_retrieval', 'node_no_candidates',
        'node_query_expansion', 'node_rule_based_filter',
        'resolve_patient_mesh', 'route_after_filter', 'route_after_gpt4o',
        'route_after_retrieval', 'tokenize_for_bm25', 'unboosted_score'
    ],
}

_PRE_2C_COUNTS = {'13- LangGraph Agent.py': 87}


# NAMES PASS 2c ADDED to File 13's shim surface. Declared, because the inventory
# check runs in both directions and an undeclared addition is a name some later
# file could pick up without asking.
#
#   deps                        THE SEAM. Files 35, 36, 37 and 45 install their
#                               overrides through it, so it has to be reachable
#                               from the shared namespace.
#   score_pairs                 the MEDCPT_SCORER dispatcher. medcpt_score_pairs
#                               keeps its old meaning -- the raw function -- so
#                               a caller that wants the dispatch asks for this.
#   _match_patient_to_trials_pkg  the package function the shim's wrapper calls.
#   _LazyAgentDependency        the proxy class behind medcpt_tokenizer,
#                               medcpt_model and _bm25_query_model, which are
#                               lazy now instead of loaded at exec time.
#   _LEGACY_REDIRECTABLE        the legacy-rebinding guard: the name -> deps-key
#   _LEGACY_BOUND_AT_LOAD       map, the identities recorded at load, and the
#   _detect_legacy_rebinding    two functions that turn a silent redirect-to-
#   _assert_no_legacy_rebinding nowhere into a named RuntimeError.
_PASS_2C_ADDED = {
    '13- LangGraph Agent.py': {
        'deps', 'score_pairs', '_match_patient_to_trials_pkg',
        '_LazyAgentDependency', '_LEGACY_REDIRECTABLE', '_LEGACY_BOUND_AT_LOAD',
        '_detect_legacy_rebinding', '_assert_no_legacy_rebinding',
    },
}


# ===========================================================================
# THE NAMES FILES 07 AND 14 DEFINED BEFORE PASS 2b
# ===========================================================================
# Same method as the block above, for the same reasons: each file was exec'd at
# commit aa2438b into a throwaway namespace, with only the free names it reads
# out of the shared exec namespace pre-seeded, and every resulting binding
# recorded.
#
# AST WOULD HAVE BEEN WRONG FOR FILE 07 IN THE "TOO FEW" DIRECTION. Eleven of
# its names are ANNOTATED assignments -- _SYSTEM_URI_TO_KEY, _SYSTEM_PREFERENCE,
# _MCODE_STAGE_LOINCS, _ECOG_LOINC_CODE, _ECOG_LOINC_PANEL_CODE,
# _ECOG_LOINC_INTERPRETATION_CODE, _ECOG_MIN_GRADE, _ECOG_MAX_GRADE,
# _MCODE_GENOMIC_VARIANT_LOINC, _METASTASIS_LOINCS and the four _COMPONENT_*
# codes -- which a `grep "NAME ="` misses and which ast.Assign does not see
# either, because they are ast.AnnAssign.
#
# AND IN THE "TOO MANY" DIRECTION FOR BOTH. _EXCLUDE_CONDITION_VERIFICATION is
# assigned inside parse_fhir_bundle, not at module level, so an ast walk that
# did not restrict itself to tree.body would have invented a twelfth constant
# for the shim to re-export. all_patients is bound by File 07's __main__ block,
# which exec_chain never fires.
#
# Neither file has a cleanup loop, so unlike File 08 there is no name here that
# is bound and then deleted.
_PRE_2B_RUNTIME_NAMES = {
    '07- FHIR Parser.py': [
        'BIRTH_DATE_PRECISION_COUNTS', 'DEMOGRAPHIC_SOURCE_COUNTS',
        'ECOG_SELECTION_COUNTS', 'ECOG_VALUE_SHAPE_COUNTS',
        '_ACTIVE_ALLERGY_STATUSES', '_ACTIVE_MED_STATUSES',
        '_COMPONENT_GENE_STUDIED', '_COMPONENT_GENOMIC_SOURCE',
        '_COMPONENT_HGVS_CDNA', '_COMPONENT_HGVS_PROTEIN',
        '_CONDITION_STATUS_PRIORITY', '_ECOG_LOINC_CODE',
        '_ECOG_LOINC_INTERPRETATION_CODE', '_ECOG_LOINC_PANEL_CODE',
        '_ECOG_MAX_GRADE', '_ECOG_MIN_GRADE',
        '_EXCLUDE_ALLERGY_VERIFICATION', '_EXCLUDE_OBS_STATUSES',
        '_EXCLUDE_PROC_STATUSES', '_HISTORICAL_MED_STATUSES',
        '_MCODE_GENOMIC_VARIANT_LOINC', '_MCODE_STAGE_LOINCS',
        '_METASTASIS_LOINCS', '_SYSTEM_PREFERENCE', '_SYSTEM_URI_TO_KEY',
        '_US_CORE_DETAILED', '_US_CORE_OMB_CATEGORY', '_US_CORE_TEXT',
        '_calculate_age', '_condition_sort_key', '_parse_allergy',
        '_parse_condition', '_parse_demographics', '_parse_ecog_observation',
        '_parse_mcode_genomic_variant', '_parse_mcode_stage_observation',
        '_parse_medication', '_parse_medication_statement',
        '_parse_observation', '_parse_procedure', '_read_us_core_category',
        '_select_best_coding', '_select_ecog_performance_status',
        'load_all_patients', 'parse_fhir_bundle',
    ],
    '14- Database Logger.py': [
        'INFERENCE_COLUMN_ADDITIONS', 'TRIAL_MATCH_COLUMN_ADDITIONS',
        '_INITIALIZED_DATABASES', '_ensure_database', '_resolve_primary_cancer',
        'initialize_database', 'log_inference',
    ],
}

_PRE_2B_COUNTS = {'07- FHIR Parser.py': 45, '14- Database Logger.py': 7}


# NAMES PASS 2b ADDED to a shim's surface, and why each one is not an accident.
#
# The inventory check runs in BOTH directions -- nothing missing, nothing extra
# -- because a shim that quietly puts an unexpected name into the shared exec
# namespace is how the next file to use that name silently picks this one up.
# So an addition has to be declared here to be allowed.
#
#   resolve_inference_db_path   NEW PUBLIC FUNCTION. It answers "which database
#                               will log_inference write to", which nothing
#                               could ask before, and it is what Files 36, 37,
#                               38, 40 and 45 use to show that passing db_path
#                               is doing work rather than agreeing with a
#                               default.
#   _package_log_inference      SHIM PLUMBING. File 14's shim cannot re-export
#                               log_inference directly: it wraps it to supply
#                               globals().get("inferences_path"), so it needs a
#                               name for the thing it is wrapping. Underscored
#                               and named for what it is.
_PASS_2B_ADDED = {
    '07- FHIR Parser.py': set(),
    '14- Database Logger.py': {'resolve_inference_db_path',
                               '_package_log_inference'},
}


def _bound_names(path: str) -> set:
    """Every top-level name a module binds: defs, classes, assignments, imports."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def _run(code: str, cwd: str, extra_path: str = None):
    """Run `code` in a subprocess. Returns (returncode, stdout, stderr)."""
    env = dict(os.environ)
    if extra_path:
        env["PYTHONPATH"] = extra_path + os.pathsep + env.get("PYTHONPATH", "")
    else:
        env.pop("PYTHONPATH", None)
    proc = subprocess.run([sys.executable, "-c", code], cwd=cwd, env=env,
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def _last_json(stdout: str):
    """Parse the last line of stdout as JSON.

    The package prints path-resolution lines on import, so a subprocess's
    result cannot be the whole of stdout. Returns None if the last line is not
    JSON, which the caller must report rather than swallow.
    """
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                return None
    return None


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("PACKAGE SPLIT TEST — item 20c")
print("=" * 78)
print(f"  code directory:  {_code_dir}")
print(f"  package:         {_PKG_DIR}")


# ===========================================================================
# 0. THE PACKAGE IS IMPORTABLE FROM A DIRECTORY THAT IS NOT THE CODE DIRECTORY
# ===========================================================================

print("\n" + "=" * 78)
print("0. `pip install -e .` — import oncotriage.config from anywhere")
print("=" * 78)

_ELSEWHERE = tempfile.mkdtemp(prefix="oncotriage_pkgtest_")

_rc, _out, _err = _run(
    "import oncotriage.config as c; import json; print(json.dumps({'name': c.Project_Name}))",
    cwd=_ELSEWHERE)
check("import oncotriage.config succeeds from a foreign working directory, "
      "with PYTHONPATH unset", _rc, 0)
if _rc != 0:
    fail("the editable install is in place",
         f"`pip install -e .` from {_code_dir} is what makes this work.\n"
         f"          stderr tail: {_err.strip().splitlines()[-1:]}")
    # Every later subprocess still needs to run, so fall back to PYTHONPATH and
    # SAY SO. A silent fallback here would make the rest of this file report on
    # an arrangement nobody is actually shipping.
    _FALLBACK_PATH = _code_dir
    print(f"  [Fallback] adding {_code_dir} to PYTHONPATH for the remaining checks")
else:
    _FALLBACK_PATH = None
    _payload = _last_json(_out)
    check("...and the module that answered is the real one",
          (_payload or {}).get("name"), "OncoTriage Agent")


# ===========================================================================
# 1. THE CYCLE IS GONE
# ===========================================================================

print("\n" + "=" * 78)
print("1. config and utils import in both orders; config never imports utils")
print("=" * 78)

for _first, _second in (("config", "utils"), ("utils", "config")):
    _rc, _out, _err = _run(
        f"import oncotriage.{_first}; import oncotriage.{_second}; print('{{\"ok\": true}}')",
        cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
    check(f"oncotriage.{_first} then oncotriage.{_second} imports cleanly", _rc, 0)
    if _rc != 0:
        fail(f"import order {_first} -> {_second}",
             f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-3:]}")


def _mentions_module(path: str, dotted: str) -> bool:
    """True if `path`'s AST imports `dotted` ANYWHERE — including inside a
    function body, which is where a deferred import would hide."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "") == dotted or (node.module or "").startswith(dotted + "."):
                return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == dotted or alias.name.startswith(dotted + "."):
                    return True
        # `from oncotriage import utils` — the module is the package, the name
        # is the submodule. Caught here rather than above.
        if isinstance(node, ast.ImportFrom) and (node.module or "") == "oncotriage":
            for alias in node.names:
                if "oncotriage." + alias.name == dotted:
                    return True
    return False


_CONFIG_PY = os.path.join(_PKG_DIR, "config.py")
_UTILS_PY = os.path.join(_PKG_DIR, "utils.py")

check("oncotriage/config.py does not import oncotriage.utils, anywhere",
      _mentions_module(_CONFIG_PY, "oncotriage.utils"), False)

# NON-DEGENERATE. The check above would also pass on a config.py with no
# imports at all, or on a detector that never returns True. Both are ruled out
# here: config must import settings, and utils must import config.
check("...and the detector is not vacuous: config DOES import oncotriage.paths",
      _mentions_module(_CONFIG_PY, "oncotriage.paths"), True)
check("...and utils DOES import oncotriage.config (the surviving direction)",
      _mentions_module(_UTILS_PY, "oncotriage.config"), True)


# --- NEGATIVE CONTROL: put the cycle back, in a COPY, and watch it bite ------
# CLAUDE.md: an assertion that has only ever passed is not evidence that it can
# catch anything. A copy of the package gets the removed edge added back to
# config.py.
#
# WHAT THIS CONTROL ACTUALLY FOUND, and it changed the design of this file.
# A reintroduced config -> utils import is ORDER-DEPENDENT:
#
#   import oncotriage.utils   -> ImportError: cannot import name
#                                'get_model_cost' from partially initialized
#                                module 'oncotriage.utils' (most likely due to
#                                a circular import)
#   import oncotriage.config  -> SUCCEEDS, silently
#
# The second one survives because config's cycle edge runs before utils has
# defined anything, while utils' own `from oncotriage import config` resolves
# to the half-built module in sys.modules and never touches an attribute until
# call time. So the import-order checks above are NOT a guard against the cycle
# coming back: they would both pass with it in place. The STRUCTURAL check is
# the guard, and this control is what demonstrates the difference rather than
# assuming it. Both facts are asserted below, including the uncomfortable one.

print("\n  Negative control: reintroducing the cycle in a COPY of the package")

_BROKEN_ROOT = tempfile.mkdtemp(prefix="oncotriage_cycle_")
shutil.copytree(_PKG_DIR, os.path.join(_BROKEN_ROOT, "oncotriage"))
_BROKEN_CONFIG = os.path.join(_BROKEN_ROOT, "oncotriage", "config.py")

_src = open(_BROKEN_CONFIG, encoding="utf-8").read()
_needle = "from oncotriage import paths"
if _needle not in _src:
    fail("the negative control can find its insertion point",
         f"{_needle!r} is not in the copied config.py; this control is not "
         f"testing what it claims to")
else:
    open(_BROKEN_CONFIG, "w", encoding="utf-8").write(
        _src.replace(_needle, _needle + "\nfrom oncotriage.utils import get_model_cost", 1))

    check("the structural detector CATCHES a reintroduced config -> utils import",
          _mentions_module(_BROKEN_CONFIG, "oncotriage.utils"), True)

    # The order that exposes it.
    _rc_u, _out_u, _err_u = _run("import oncotriage.utils", cwd=_ELSEWHERE,
                                 extra_path=_BROKEN_ROOT)
    check("with the cycle back, `import oncotriage.utils` FAILS", _rc_u != 0, True)
    check("...and it fails AS a circular import, not as something else",
          "circular import" in _err_u or "partially initialized module" in _err_u, True)

    # The order that hides it. Asserted, not glossed over: this is why the
    # structural check exists and why check 1's import-order pair is a smoke
    # test rather than the guard.
    _rc_c, _out_c, _err_c = _run("import oncotriage.config", cwd=_ELSEWHERE,
                                 extra_path=_BROKEN_ROOT)
    check("...while `import oncotriage.config` still succeeds, which is exactly "
          "why the structural check is the guard", _rc_c, 0)

    if _rc_u == 0:
        fail("the negative control actually broke something",
             "the copied package with the cycle restored imported cleanly in "
             "BOTH orders, so neither the import checks nor this control is "
             "detecting anything")

shutil.rmtree(_BROKEN_ROOT, ignore_errors=True)


# ===========================================================================
# 1b. NO ONCOTRIAGE MODULE IMPORTS ANOTHER FROM INSIDE A FUNCTION BODY
# ===========================================================================

print("\n" + "=" * 78)
print("1b. every oncotriage -> oncotriage import is at module scope")
print("=" * 78)

# WHY THIS RULE EXISTS. Pass 20c-1 put load_env_keys in oncotriage.settings and
# reached keys_path through `from oncotriage.paths import keys_path` written
# INSIDE the function body, because paths imports settings and a module-scope
# import would have been a cycle. It worked. It was also invisible: check 1
# above reads config.py's import block, and a dependency that is not in an
# import block cannot be seen by any scan of import blocks. Pass 20c-2a moved
# the function to paths, where the value already lives, and made the absence of
# deferred package imports a checked property rather than a habit.
#
# THIRD-PARTY IMPORTS IN FUNCTION BODIES ARE NOT COVERED AND MUST NOT BE.
# cancer_code_registry._build_icd10_cancer_sets() does `import icd10` in its
# body on purpose: hoisting it would make importing the registry load the whole
# ICD-10-CM release, which is exactly the import-time work section 2 proves the
# package does not do. The rule is about oncotriage-to-oncotriage edges, which
# are the ones that form cycles and the ones a reader needs the import block to
# be honest about.

_PKG_FILES = sorted(
    os.path.join(root, name)
    for root, _dirs, files in os.walk(_PKG_DIR)
    for name in files
    if name.endswith(".py") and "__pycache__" not in root
)

check("the package file list is non-empty and covers all six subpackages",
      len(_PKG_FILES) >= 75
      and any(f.endswith("registries/mesh.py") for f in _PKG_FILES)
      and any(f.endswith("extraction/negation.py") for f in _PKG_FILES)
      and any(f.endswith("fhir/parser.py") for f in _PKG_FILES)
      and any(f.endswith("storage/database_logger.py") for f in _PKG_FILES)
      and any(f.endswith("agent/deps.py") for f in _PKG_FILES)
      and any(f.endswith("retrieval/indexer.py") for f in _PKG_FILES)
      and any(f.endswith("embedding.py") for f in _PKG_FILES)
      # Pass 20c-3c-1: the dashboard. Named here so the two subpackages cannot
      # vanish from the tree without this scan noticing.
      and any(f.endswith("dashboard/data.py") for f in _PKG_FILES)
      and any(f.endswith("dashboard/tabs/reproducibility.py") for f in _PKG_FILES)
      # Pass 20c-3c-2: orchestration, and the Qdrant backup that joined
      # retrieval. Named for the same reason as the dashboard pair above.
      and any(f.endswith("orchestration/dag_generator.py") for f in _PKG_FILES)
      and any(f.endswith("orchestration/airflow_manager.py") for f in _PKG_FILES)
      and any(f.endswith("retrieval/qdrant_backup.py") for f in _PKG_FILES)
      # Pass 20c-3d: the last three subpackages. fixtures/capture.py is named
      # here for the same reason dashboard/data.py is -- it is the module whose
      # disappearance would take the whole characterization baseline with it,
      # and it is the one module in the package that carries a database
      # tripwire.
      and any(f.endswith("ablation/study.py") for f in _PKG_FILES)
      and any(f.endswith("evaluation/cohort_diff.py") for f in _PKG_FILES)
      and any(f.endswith("fixtures/capture.py") for f in _PKG_FILES)
      and any(f.endswith("fixtures/replay.py") for f in _PKG_FILES),
      True)

# EVERY SUBPACKAGE MUST BE DECLARED IN pyproject.toml. setuptools does not
# recurse into a listed package, so a subpackage present in the tree and absent
# from the `packages` list is importable from an EDITABLE install (which maps
# the source tree) and MISSING from a built wheel. That difference does not
# surface until someone builds one. Read as text rather than with a TOML parser
# because tomllib would only tell us the list parses, not that it matches the
# directory tree.
_PYPROJECT = open(os.path.join(_code_dir, "pyproject.toml"), encoding="utf-8").read()

# RECURSIVE AS OF PASS 20c-3i, and the one-level version had already been
# outrun by the tree it was scanning.
#
# It was an os.listdir of _PKG_DIR, so it could only ever see subpackages one
# directory deep. oncotriage.dashboard.tabs has been nested since pass 20c-3c-1
# -- which noticed, and answered by asserting that ONE nested name separately,
# as a literal string search against pyproject.toml. That works for exactly as
# long as somebody remembers to add a line per nesting, which is the same bet
# the pyproject `packages` list itself makes and the reason this check exists to
# cover it. The second nested subpackage would have been invisible to both
# halves: absent from the listdir, so absent from the "every subpackage on disk
# is declared" comparison, and absent from the hand-written string search too.
#
# The consequence is not a test failure, it is a shipping defect: setuptools
# does not recurse into a listed package, so an undeclared subpackage is present
# in an EDITABLE install (which maps the source tree) and MISSING from a built
# wheel. That difference does not surface until someone builds one.
#
# os.walk instead, keyed on the presence of __init__.py, to any depth.
_SUBPACKAGE_DIRS = sorted(
    "oncotriage." + os.path.relpath(root, _PKG_DIR).replace(os.sep, ".")
    for root, _dirs, files in os.walk(_PKG_DIR)
    if "__init__.py" in files and os.path.abspath(root) != os.path.abspath(_PKG_DIR)
    and "__pycache__" not in root
)
check("the tree has the subpackages this pass expects (non-degeneracy)",
      _SUBPACKAGE_DIRS,
      # api, batch and monitoring are new in pass 20c-3b -- the serving layer.
      # dashboard is new in pass 20c-3c-1. orchestration is new in pass
      # 20c-3c-2 -- Files 22, 23 and 24, the Airflow layer. (File 29 landed in
      # oncotriage.retrieval, which was already here, so that pass adds exactly
      # one name.) oncotriage.dashboard.tabs is in this list for the first time
      # in pass 20c-3i: the scan above reaches it now instead of it needing a
      # hand-written check of its own.
      # ablation, evaluation and fixtures are new in pass 20c-3d, the last
      # conversion pass: Files 26 and 27, Files 28 and 34, and Files 45 and 46.
      ["oncotriage.ablation", "oncotriage.agent", "oncotriage.api",
       "oncotriage.batch",
       "oncotriage.dashboard", "oncotriage.dashboard.tabs",
       "oncotriage.evaluation", "oncotriage.extraction", "oncotriage.fhir",
       "oncotriage.fixtures",
       "oncotriage.monitoring", "oncotriage.orchestration",
       "oncotriage.registries", "oncotriage.retrieval",
       "oncotriage.storage"])
check("the scan reaches NESTED subpackages, not just the top level -- "
      "oncotriage.dashboard.tabs is two deep and setuptools does not recurse "
      "into a listed package",
      "oncotriage.dashboard.tabs" in _SUBPACKAGE_DIRS, True)
check("every subpackage on disk is declared in pyproject.toml, so a built "
      "wheel carries it",
      sorted(p for p in _SUBPACKAGE_DIRS if f'"{p}"' not in _PYPROJECT), [])

# NEGATIVE CONTROL, and the reason it plants at DEPTH TWO: the check above is
# one that already existed in a one-level form and passed. A control that
# planted a top-level subpackage would have fired against the OLD scan too and
# would prove nothing about what this pass changed. This one is invisible to a
# listdir and visible to the walk.
_NESTED_CONTROL_ROOT = tempfile.mkdtemp(prefix="oncotriage-nested-")
try:
    _control_pkg = os.path.join(_NESTED_CONTROL_ROOT, "oncotriage")
    shutil.copytree(_PKG_DIR, _control_pkg,
                    ignore=shutil.ignore_patterns("__pycache__"))
    _planted = os.path.join(_control_pkg, "dashboard", "tabs", "planted")
    os.makedirs(_planted)
    open(os.path.join(_planted, "__init__.py"), "w").close()

    _control_found = sorted(
        "oncotriage." + os.path.relpath(root, _control_pkg).replace(os.sep, ".")
        for root, _dirs, files in os.walk(_control_pkg)
        if "__init__.py" in files
        and os.path.abspath(root) != os.path.abspath(_control_pkg)
        and "__pycache__" not in root
    )
    check("the recursive scan FINDS a subpackage planted three deep, where a "
          "one-level os.listdir would see nothing (negative control)",
          "oncotriage.dashboard.tabs.planted" in _control_found, True)
    check("...and it is undeclared in pyproject.toml, so the wheel check "
          "catches it too",
          sorted(p for p in _control_found if f'"{p}"' not in _PYPROJECT),
          ["oncotriage.dashboard.tabs.planted"])
    check("...while the one-level listdir the old scan used misses it entirely, "
          "which is what makes this control discriminating",
          "oncotriage.dashboard.tabs.planted" in sorted(
              "oncotriage." + name for name in os.listdir(_control_pkg)
              if os.path.isfile(os.path.join(_control_pkg, name, "__init__.py"))),
          False)
finally:
    shutil.rmtree(_NESTED_CONTROL_ROOT, ignore_errors=True)


def _function_body_imports(path: str):
    """Every import statement nested inside a def/class in `path`.

    Returns [(qualified_module, enclosing_name, lineno)]. A relative import
    (``from . import x``) counts as an oncotriage import: node.level > 0 means
    it can only resolve inside this package.
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    found = []
    for scope in ast.walk(tree):
        if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for node in ast.walk(scope):
            if isinstance(node, ast.ImportFrom):
                module = ("." * node.level) + (node.module or "")
                found.append((module, scope.name, node.lineno))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    found.append((alias.name, scope.name, node.lineno))
    return found


def _deferred_package_imports(paths):
    """Function-body imports that resolve inside the oncotriage package."""
    out = []
    for path in paths:
        for module, scope, lineno in _function_body_imports(path):
            if module.startswith(".") or module == "oncotriage" or module.startswith("oncotriage."):
                out.append(f"{os.path.relpath(path, _code_dir)}:{lineno} "
                           f"in {scope}() -> {module}")
    return sorted(out)


# NON-DEGENERATE FIRST. The check below passes on an empty list, and an empty
# list is exactly what a broken walker returns. So: the walker must find at
# least one function-body import somewhere in the package, and it must find the
# specific one that is supposed to be there.
_ALL_BODY_IMPORTS = sorted(
    f"{os.path.relpath(p, _code_dir)}:{ln} in {scope}() -> {mod}"
    for p in _PKG_FILES for mod, scope, ln in _function_body_imports(p)
)
check("the walker finds function-body imports at all (non-degeneracy)",
      len(_ALL_BODY_IMPORTS) >= 1, True)
check("...specifically the deliberate third-party one, `import icd10` inside "
      "_build_icd10_cancer_sets",
      any("_build_icd10_cancer_sets() -> icd10" in e for e in _ALL_BODY_IMPORTS), True)

check("no oncotriage module imports another oncotriage module from a "
      "function body", _deferred_package_imports(_PKG_FILES), [])

# --- NEGATIVE CONTROL: put a deferred package import back, in a COPY --------
# Section 1's control showed that a reintroduced module-scope cycle is
# order-dependent and can import cleanly. A DEFERRED one is worse: it never
# fails at import at all, in any order, because it does not run until the
# function is called. Nothing but this scan would notice it, so this scan has
# to be shown to notice it.

print("\n  Negative control: reintroducing a deferred package import in a COPY")

_DEFERRED_ROOT = tempfile.mkdtemp(prefix="oncotriage_deferred_")
shutil.copytree(_PKG_DIR, os.path.join(_DEFERRED_ROOT, "oncotriage"))
_DEFERRED_SETTINGS = os.path.join(_DEFERRED_ROOT, "oncotriage", "settings.py")

# resolve_keys_path, deliberately: it is NOT called while paths.py is being
# imported. resolve_main_path IS -- putting the deferred import there makes the
# copy fail at import with a genuine partially-initialized-module error, which
# would be a different (and louder) defect than the silent one this control is
# about.
_src = open(_DEFERRED_SETTINGS, encoding="utf-8").read()
_needle = "def resolve_keys_path(fallback):\n"
if _needle not in _src:
    fail("the deferred-import control can find its insertion point",
         f"{_needle!r} is not in the copied settings.py; this control is not "
         f"testing what it claims to")
else:
    open(_DEFERRED_SETTINGS, "w", encoding="utf-8").write(_src.replace(
        _needle,
        _needle + '    """Reintroduced deferred import."""\n'
                  "    from oncotriage.paths import keys_path  # reintroduced\n", 1))

    _copied = sorted(
        os.path.join(root, name)
        for root, _dirs, files in os.walk(os.path.join(_DEFERRED_ROOT, "oncotriage"))
        for name in files
        if name.endswith(".py") and "__pycache__" not in root
    )
    _caught = _deferred_package_imports(_copied)
    check("the scan CATCHES a reintroduced deferred package import",
          len(_caught), 1)
    check("...and names the file, the function and the module it found",
          bool(_caught) and "settings.py" in _caught[0]
          and "resolve_keys_path()" in _caught[0]
          and "oncotriage.paths" in _caught[0], True)

    # And the reason the scan is needed at all: the copy still imports fine.
    _rc, _out, _err = _run("import oncotriage.settings, oncotriage.paths; print('{}')",
                           cwd=_ELSEWHERE, extra_path=_DEFERRED_ROOT)
    check("...while the copy still imports cleanly in both directions, which is "
          "why a runtime check could never find this", _rc, 0)

shutil.rmtree(_DEFERRED_ROOT, ignore_errors=True)


# ===========================================================================
# 2. IMPORTING TOUCHES NO SOCKET, NO DATABASE, NO MODEL
# ===========================================================================

print("\n" + "=" * 78)
print("2. importing every package module under a socket / sqlite trap")
print("=" * 78)

# WHAT IS TRAPPED, and why each one.
#
#   socket.socket        replaced by a SUBCLASS that raises in __init__, not by
#                        a plain function: `ssl.py` does `class SSLSocket(socket)`
#                        at import time and a function cannot be subclassed.
#                        Raising before super().__init__ means no file
#                        descriptor is ever allocated.
#   socket.create_connection   the other way a client opens a connection.
#   sqlite3.connect      "touches no database".
#   builtins.open        "reads no JSON". Added in pass 2a, when
#   io.open              oncotriage.registries.mesh arrived: load_mesh_filter()
#                        reads four JSON lookups and MUST do it in a function,
#                        not at import. Both bindings are patched because they
#                        are separate references to the same function -- and
#                        pathlib.Path.open() goes through io.open, so patching
#                        only builtins.open would leave every Path read open.
#
# io.open_code is deliberately NOT patched: that is what the import machinery
# itself uses to read a .py file, and trapping it would trap the very imports
# under test.
#
# THE THIRD-PARTY IMPORTS HAPPEN BEFORE THE TRAP IS ARMED. openai pulls in
# sysconfig, which on macOS reads /System/Library/CoreServices to work out the
# OS version, and that read is not this package's doing. Pre-importing them
# makes the claim exactly "importing an oncotriage module reads no file",
# which is the claim worth making. Verified: with the pre-imports removed, the
# run dies inside _osx_support, which is how this was found.
#
# The heavy-module list is what "loads no model" means: torch / transformers /
# sentence_transformers carry MedCPT, and icd10 is the full ICD-10-CM release
# that _build_icd10_cancer_sets() imports INSIDE its body. fastembed is
# deliberately absent from the list -- qdrant_client imports it transitively, so
# its presence says nothing about this package, and importing it loads no
# weights.
_PURITY = r"""
import builtins, io, json, socket, sqlite3, subprocess, sys

import caffeine, dotenv, httpx, openai, qdrant_client, tenacity           # noqa: F401
import collections, glob, logging, os, pathlib, re, threading, typing     # noqa: F401
import xml.etree.ElementTree                                              # noqa: F401
# Pre-imported for the same reason as the block above: the agent imports these
# at module scope, and their own import chains touch files that are not this
# package's doing. numpy and rank_bm25 read nothing; langgraph is listed here
# rather than in `heavy` because it is a graph library, not a model.
import numpy, rank_bm25, langgraph.graph                                  # noqa: F401
# Pass 20c-3a: oncotriage.fhir.explore imports these THREE AT MODULE SCOPE, and
# deliberately -- seven of its twelve functions plot, and nothing but
# "06- FHIR Explore.py" imports it. matplotlib reads matplotlibrc and its font
# cache at import, and pandas reads its own configuration, so without this
# pre-import the trap would fire on THEIR file access rather than on anything
# this package does. Same allowance, for the same reason, as the block above.
import matplotlib, matplotlib.pyplot, pandas, seaborn                     # noqa: F401
# Pass 20c-3b: the serving layer's three module-scope third-party dependencies.
# scipy.stats is imported at module scope by oncotriage.monitoring.drift and has
# to be -- SCIPY_AVAILABLE is that module's answer to "can this run", and a flag
# that could not be read until after the first call that needed it would be
# useless. fastapi and slowapi are oncotriage.api.server's whole subject. All
# three read files of their own at import (scipy loads its config, fastapi pulls
# in pydantic's compiled core), which is not this package's doing. Same
# allowance, for the same reason, as the two blocks above.
import scipy.stats, fastapi, slowapi                                      # noqa: F401
# tqdm and uvicorn: the batch runner draws a progress bar, and the API entry
# point serves. Neither loads a model; both are listed so the trap measures this
# package rather than its dependencies' import machinery.
import tqdm, uvicorn                                                      # noqa: F401
# Pass 20c-3c-2: oncotriage.orchestration.airflow_manager imports requests at
# module scope -- every one of its status and trigger functions is an HTTP call,
# so there is no version of that module that does not need it. requests reads
# certifi's CA bundle location and charset_normalizer's tables at import, which
# is not this package's doing. Same allowance, for the same reason, as the four
# blocks above. It is NOT on the `heavy` list: it opens no socket at import (the
# socket trap below is armed and would catch that), and it loads no model.
import requests                                                           # noqa: F401


class Blocked(RuntimeError):
    pass


_real_socket = socket.socket


class BlockedSocket(_real_socket):
    def __init__(self, *args, **kwargs):
        raise Blocked("socket.socket() was constructed")


def _blocked(*args, **kwargs):
    raise Blocked("a blocked call was made")


socket.socket = BlockedSocket
socket.create_connection = _blocked
sqlite3.connect = _blocked
builtins.open = _blocked
io.open = _blocked
# PASS 20c-3c-2 ADDED THE SUBPROCESS TRAPS, and this pass is why they exist.
# Before item 20b, LOADING "22- Airflow Database.py" ran `airflow db migrate`
# and `airflow db check` through subprocess.run, and loading
# "24- Airflow Manager.py" launched TWO long-lived server processes through
# subprocess.Popen and left them running -- the heaviest import-time side effect
# the project has ever had. None of the four traps above can see either one: a
# subprocess opens no socket, no database and no file IN THIS PROCESS.
#
# Both names are patched on the `subprocess` MODULE, which is what
# `subprocess.run(...)`/`subprocess.Popen(...)` resolve at CALL time -- and both
# orchestration modules write exactly that, `import subprocess` plus a qualified
# call, so the patch is in their path. A module that had done `from subprocess
# import Popen` at import would hold the original and escape this; check 7's AST
# scan is the backstop for that shape.
#
# PASS 20c-3i REWROTE THE TWO LINES BELOW INTO THE BLOCK THAT FOLLOWS, because
# the paragraph above contains a claim that is FALSE, and it was measured rather
# than argued about. It says a module doing `from subprocess import Popen` would
# hold the original and escape. It would not: `from X import name` is an
# ATTRIBUTE READ performed when the import statement RUNS, and every one of the
# 48 imports below runs AFTER this patch, so a from-import in any package module
# binds `_blocked`. Measured directly -- attribute form, module-alias form and
# from-import form were each fired against a patched subprocess and all three
# raised. The three controls at the bottom of this probe now assert that instead
# of the comment claiming it.
#
# WHAT DOES ESCAPE, also measured, and it is not a reference form at all:
#
#   * os.system, os.posix_spawn, os.posix_spawnp, os.fork and the os.exec*
#     family spawn a process WITHOUT going through the subprocess module. They
#     escaped both traps completely.
#   * subprocess.call / check_call / check_output / getoutput did NOT escape --
#     each builds a Popen through the subprocess module's own global, which is
#     patched. Same for os.popen. That is a CPython implementation detail rather
#     than a documented guarantee, so both are trapped explicitly below; relying
#     on an internal call graph for a safety property is how a trap goes quiet
#     across a version bump with nothing to show for it.
#   * a module imported BEFORE this point that did `from subprocess import run`
#     holds the ORIGINAL and is genuinely out of reach of an attribute patch.
#     That is the only true from-import escape, and the sweep at the bottom is
#     what finds it: it walks sys.modules for any surviving reference to the
#     originals. Today it finds none.
_SPAWN_ORIGINALS = {"subprocess.run": subprocess.run,
                    "subprocess.Popen": subprocess.Popen}

subprocess.Popen = _blocked
subprocess.run = _blocked

# CLOSE THE PRE-BOUND FROM-IMPORTS, rather than merely reporting them.
#
# The sweep at the bottom of this probe found a real one on its first run:
# prompt_toolkit.application.application.Popen, a `from subprocess import Popen`
# executed while some earlier import was loading. An attribute patch on the
# subprocess module cannot reach a binding that was taken before it, so that
# reference was a live spawn route through the whole trapped section.
#
# Nothing in this package imports prompt_toolkit, so no oncotriage module could
# have reached it -- which is the argument for reporting instead of fixing, and
# it is the wrong argument. A trap whose coverage depends on which third-party
# packages happen to be installed is not a trap, it is a coincidence; the next
# dependency to do the same thing arrives without warning. Every holder is
# rebound to _blocked here, and the sweep below then asserts none survive.
_CLOSED_HOLDERS = []
for _mname, _mod in list(sys.modules.items()):
    if _mod is None or _mname in ("subprocess", "__main__"):
        continue
    _d = getattr(_mod, "__dict__", None)
    if not isinstance(_d, dict):
        continue
    for _attr, _val in list(_d.items()):
        if any(_val is _orig for _orig in _SPAWN_ORIGINALS.values()):
            setattr(_mod, _attr, _blocked)
            _CLOSED_HOLDERS.append(f"{_mname}.{_attr}")
# The os-level spawn routes, which no subprocess patch can see.
os.system = _blocked
os.popen = _blocked
os.posix_spawn = _blocked
os.posix_spawnp = _blocked
os.execv = _blocked
os.execve = _blocked
os.execvp = _blocked
# fork is trapped last and named separately because trapping it would break
# multiprocessing if anything below used it. Nothing does -- this probe runs one
# process and imports 48 modules -- and a module that FORKED at import would be
# the worst offender the project has ever had.
os.fork = _blocked

import oncotriage.constants
import oncotriage.settings
import oncotriage.paths
import oncotriage.config
import oncotriage.utils
import oncotriage.embedding
import oncotriage.registries.cancer_code_registry
import oncotriage.registries.mesh
import oncotriage.registries.mesh_crosswalk_build
import oncotriage.extraction.negation
import oncotriage.extraction.stage
import oncotriage.extraction.histology
import oncotriage.fhir.parser
import oncotriage.fhir.clean
import oncotriage.fhir.generate
import oncotriage.fhir.explore
import oncotriage.retrieval.indexer
import oncotriage.retrieval.index_validator
# Pass 20c-3c-2. qdrant_backup is File 29, which was the LAST UNGUARDED FILE in
# the repository: every statement in it was at module level, so loading it
# created a directory, listed every collection over the network and wrote one
# JSON per collection. Three of this section's five traps -- socket, open,
# io.open -- are exactly the ones that file would have fired, and it is the
# single strongest reason this check exists at all.
import oncotriage.retrieval.qdrant_backup
import oncotriage.orchestration
import oncotriage.orchestration.home
# airflow_setup ran `airflow db migrate` and rewrote airflow.cfg at load before
# item 20b; airflow_manager launched TWO long-lived server processes with
# subprocess.Popen. Neither may do anything at import, and no trap here can see
# a subprocess -- so check 7 (below) reads their ASTs for module-level calls,
# and this import is what proves the cheaper properties.
import oncotriage.orchestration.airflow_setup
import oncotriage.orchestration.airflow_manager
# dag_generator is the one where the trap does real work: File 23 assembled
# dag_content at module level, %-formatted with THREE lazy paths, so importing
# the old shape resolved three directories. The `open` trap would not catch
# that (glob uses os.scandir) -- check 2c's per-module sweep is what does --
# but the module must also not WRITE the DAG at import, which this catches.
import oncotriage.orchestration.dag_generator
import oncotriage.storage.database_logger
import oncotriage.storage.maintenance
import oncotriage.storage.queries
import oncotriage.api
import oncotriage.api.server
import oncotriage.monitoring
import oncotriage.monitoring.drift
import oncotriage.batch
import oncotriage.batch.runner
import oncotriage.registries.primary_cancer
import oncotriage.agent
import oncotriage.agent.deps
import oncotriage.agent.state
import oncotriage.agent.text
import oncotriage.agent.models
import oncotriage.agent.patient
import oncotriage.agent.mesh_expansion
import oncotriage.agent.retrieval
import oncotriage.agent.filtering
import oncotriage.agent.evaluation
import oncotriage.agent.terminal
import oncotriage.agent.graph
import oncotriage.agent.display

# langgraph LEFT THE LIST in pass 20c-2c. oncotriage.agent.graph imports
# StateGraph at module scope, which it must -- build_matching_graph is the
# module's whole subject. It loads no weights. torch and transformers stay, and
# they are the ones that matter: pass 2c made the MedCPT load lazy, so an agent
# import that pulled either in would mean the laziness had been undone.
heavy = [m for m in ("torch", "transformers", "sentence_transformers",
                     "streamlit", "icd10") if m in sys.modules]

armed = {}
for _name, _fn, _args in (("socket", socket.socket, (socket.AF_INET, socket.SOCK_STREAM)),
                          ("sqlite3", sqlite3.connect, (":memory:",)),
                          # The path never has to exist: the trap raises
                          # before anything reaches the filesystem, and a path
                          # that does exist would let a FAILED trap silently
                          # succeed instead of raising FileNotFoundError.
                          ("open", builtins.open, ("/oncotriage-trap-probe",)),
                          ("io.open", io.open, ("/oncotriage-trap-probe",)),
                          # Pass 20c-3c-2. The command never has to be real:
                          # the trap raises before anything is spawned, and a
                          # real command would let a FAILED trap succeed
                          # quietly instead of raising FileNotFoundError.
                          ("subprocess.run", subprocess.run,
                           (["oncotriage-trap-probe"],)),
                          ("subprocess.Popen", subprocess.Popen,
                           (["oncotriage-trap-probe"],)),
                          # Pass 20c-3i: the os-level spawn routes, which no
                          # subprocess patch reaches. os.system was measured
                          # ESCAPING both original traps.
                          ("os.system", os.system, ("oncotriage-trap-probe",)),
                          ("os.popen", os.popen, ("oncotriage-trap-probe",)),
                          ("os.posix_spawn", os.posix_spawn,
                           ("/oncotriage-trap-probe", ["x"], {})),
                          ("os.execv", os.execv,
                           ("/oncotriage-trap-probe", ["x"])),
                          ("os.fork", os.fork, ())):
    try:
        _fn(*_args)
        armed[_name] = False
    except Blocked:
        armed[_name] = True

# EVERY REFERENCE FORM, ONE CONTROL EACH (pass 20c-3i).
#
# A trap that patches a module attribute is only as good as the way callers
# NAME the thing it patches, and this project has already shipped three defects
# of exactly that class -- File 36's walk covered ast.Name and would have missed
# the attribute form, File 47's own BM25 check matched the bare name and
# fastembed.SparseTextEmbedding evaded it, and the paragraph above this probe
# asserted a from-import escape that does not exist. So the three forms are
# fired rather than reasoned about:
#
#   attribute     subprocess.run(...)              -- what both orchestration
#                                                     modules actually write
#   module alias  import subprocess as sp; sp.run  -- same module object, so
#                                                     this must be caught too
#   from-import   from subprocess import run; run  -- reads the attribute when
#                                                     the import RUNS, which is
#                                                     after this patch
#
# A future edit that swapped the module patch for, say, a sys.modules
# replacement would keep the first two passing and break the third.
_forms = {}


def _fires(fn, *args):
    try:
        fn(*args)
        return False
    except Blocked:
        return True


_forms["attribute"] = _fires(subprocess.run, ["oncotriage-trap-probe"])

import subprocess as _sp_alias
_forms["module_alias"] = _fires(_sp_alias.run, ["oncotriage-trap-probe"])

from subprocess import run as _run_fromimport
_forms["from_import"] = _fires(_run_fromimport, ["oncotriage-trap-probe"])

from subprocess import Popen as _popen_fromimport
_forms["from_import_popen"] = _fires(_popen_fromimport,
                                     ["oncotriage-trap-probe"])

# THE ONE GENUINE FROM-IMPORT ESCAPE, swept again AFTER the closure above and
# after all 48 imports: a module that bound the original into its own globals
# before the patch, or one that arrived during the imports and did the same.
# Nothing may hold either original by the end.
#
# __main__ is excluded because _SPAWN_ORIGINALS itself lives there -- this
# probe's own dict of originals is not an escape hatch, it is the thing doing
# the looking.
_holders = sorted(
    f"{_mname}.{_attr}"
    for _mname, _mod in list(sys.modules.items())
    if _mod is not None and _mname not in ("subprocess", "__main__")
    and isinstance(getattr(_mod, "__dict__", None), dict)
    for _attr, _val in list(_mod.__dict__.items())
    if any(_val is _orig for _orig in _SPAWN_ORIGINALS.values())
)

# THE CLOSURE MECHANISM ITSELF NEEDS A CONTROL. An empty `_holders` is also
# what a sweep that looks at nothing returns. So plant a module holding the
# original -- the exact shape prompt_toolkit had -- re-run the closure over it,
# and require it to be caught and rebound.
import types as _types
_planted_mod = _types.ModuleType("oncotriage_planted_holder")
_planted_mod.Popen = _SPAWN_ORIGINALS["subprocess.Popen"]
sys.modules["oncotriage_planted_holder"] = _planted_mod
_planted_seen = []
for _mname, _mod in list(sys.modules.items()):
    if _mod is None or _mname in ("subprocess", "__main__"):
        continue
    _d = getattr(_mod, "__dict__", None)
    if not isinstance(_d, dict):
        continue
    for _attr, _val in list(_d.items()):
        if any(_val is _orig for _orig in _SPAWN_ORIGINALS.values()):
            setattr(_mod, _attr, _blocked)
            _planted_seen.append(f"{_mname}.{_attr}")
_planted_now_blocked = _fires(_planted_mod.Popen, ["oncotriage-trap-probe"])
del sys.modules["oncotriage_planted_holder"]

print(json.dumps({"heavy": heavy, "armed": armed, "forms": _forms,
                  "prebound_holders": _holders,
                  "closed_holders": sorted(_CLOSED_HOLDERS),
                  "control_found_planted":
                      "oncotriage_planted_holder.Popen" in _planted_seen,
                  "control_planted_now_blocked": _planted_now_blocked}))
"""

# 33 as of pass 20c-3a: oncotriage.embedding, fhir.clean, fhir.generate,
# fhir.explore, retrieval.indexer and retrieval.index_validator joined the 27
# from pass 2c (which were the twelve agent modules, oncotriage.agent itself,
# oncotriage.registries.primary_cancer and the fourteen from earlier passes).
#
# THREE OF THE SIX WERE THE WORST OFFENDERS IN THE PROJECT before this pass.
# "11- RAG Trial Indexer.py" built SparseTextEmbedding at module level, so
# reading the indexer loaded a model. "06- FHIR Explore.py" resolved three globs,
# CREATED A DIRECTORY, built the whole ICD-10-CM registry and mutated matplotlib's
# global style, all at exec time. "05- FHIR Clean Data.py" resolved two globs and
# built the registry. Every one of those is now behind an accessor, and this
# probe is what says so.
#
# THE AGENT IS THE HARDEST CASE IN THIS FILE. "13- LangGraph Agent.py" loaded
# MedCPT (~110 MB) and FastEmbed at exec() time, so importing it was the single
# most expensive thing in the project, and twelve files chained it. The traps
# below say it now loads NOTHING -- and the `heavy` list is what says the models
# specifically did not arrive, which no open/socket trap could tell you.
#
# oncotriage.storage.database_logger stays the case that matters most for the
# sqlite3 trap: its whole subject is a SQLite database, and before item 20b this
# import created three tables in the production inferences.db.
#
# PASS 20c-3b ADDS NINE: storage.maintenance, storage.queries, api, api.server,
# monitoring, monitoring.drift, batch, batch.runner and the primary_cancer entry
# that was already counted -- five modules and three package __init__ files.
# Three of the five are the ones that matter here:
#
#   storage.maintenance   is the DELETE loop. "15- Database Empty.py" opened the
#                         production database at module level until item 20b;
#                         this import must open nothing at all.
#   storage.queries       is forty queries that File 16 ran AT LOAD TIME, every
#                         one of them against the production database. This is
#                         the single largest behaviour change of the pass and
#                         the sqlite3 trap is what says it landed.
#   api.server            builds the FastAPI app at import, which is the one
#                         deliberate exception to "importing does nothing". The
#                         traps are what bound that exception: an object is
#                         constructed, and no socket, database, file or model is
#                         touched while doing it.
#
# PASS 20c-3c-2 ADDS SIX: retrieval.qdrant_backup, orchestration,
# orchestration.home, orchestration.airflow_setup, orchestration.dag_generator
# and orchestration.airflow_manager -- five modules and one package __init__.
# It also adds TWO TRAPS, subprocess.run and subprocess.Popen, because three of
# the six are the only modules in the package that spawn processes and no
# existing trap could see one. The three that matter here:
#
#   retrieval.qdrant_backup   was "29- Download Qdrant Data.py", the LAST file
#                             in the repository with no __main__ guard and no
#                             function at all. Loading it created a directory,
#                             listed every Qdrant collection and scrolled every
#                             point over the network. The open, io.open and
#                             socket traps are all three of the things it did.
#   orchestration.airflow_setup    ran `airflow db migrate` and `airflow db
#                             check` at load before item 20b -- subprocess.run.
#   orchestration.airflow_manager  launched the API server AND the scheduler at
#                             load before item 20b and left them running --
#                             subprocess.Popen, the heaviest import-time side
#                             effect this project has ever had.
_MODULES_UNDER_TRAP = 48

_rc, _out, _err = _run(_PURITY, cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
check(f"all {_MODULES_UNDER_TRAP} package modules import with open, io.open, "
      f"socket.socket, socket.create_connection, sqlite3.connect, "
      f"subprocess.run and subprocess.Popen patched to raise", _rc, 0)
if _rc != 0:
    fail("import purity",
         f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")
else:
    _payload = _last_json(_out) or {}
    _armed = _payload.get("armed") or {}
    # NON-DEGENERATE, and this is the important part: a subprocess where the
    # patches silently did nothing would also exit 0. Each trap is fired after
    # the imports and must raise, so a run that proved nothing fails instead of
    # passing. The dict is checked whole, not key by key, so a trap that
    # disappears from the probe is a failure rather than an unnoticed omission.
    check("every trap was ARMED after the imports (socket, sqlite3, open, "
          "io.open, subprocess.run/Popen, os.system/popen/posix_spawn/execv/fork)",
          _armed,
          {"socket": True, "sqlite3": True, "open": True, "io.open": True,
           "subprocess.run": True, "subprocess.Popen": True,
           # Pass 20c-3i. os.system was MEASURED escaping the two subprocess
           # traps: it spawns through the C library and never touches the
           # subprocess module. So did os.posix_spawn, os.execv and os.fork.
           "os.system": True, "os.popen": True, "os.posix_spawn": True,
           "os.execv": True, "os.fork": True})
    check("no model-bearing library was imported (torch / transformers / "
          "sentence_transformers / streamlit / langgraph / icd10)",
          _payload.get("heavy"), [])

    # --- every reference form is covered, with a control each (20c-3i) -----
    #
    # THE RULE THIS ENFORCES, and it is in CLAUDE.md because three defects have
    # already come from breaking it: a check that names a symbol must cover the
    # bare name, the attribute form AND the from-import binding, each with its
    # own control. The comment beside the subprocess traps used to CLAIM the
    # from-import form escaped; it does not, because `from X import name` is an
    # attribute read performed when the import runs and the traps are armed
    # first. Measuring it is what replaced the claim.
    check("the subprocess trap catches all three reference forms -- attribute, "
          "module alias and from-import -- each fired rather than argued",
          _payload.get("forms"),
          {"attribute": True, "module_alias": True,
           "from_import": True, "from_import_popen": True})

    # THE ONE FORM AN ATTRIBUTE PATCH CANNOT REACH: a module imported BEFORE
    # the trap that bound the original into its own globals. Firing the three
    # forms above would never reveal it -- they all resolve through the patched
    # module. The probe walks sys.modules for surviving references, REBINDS
    # every one it finds, and then sweeps again; this asserts the second sweep
    # is clean.
    #
    # THE FIRST RUN OF THIS FOUND A REAL ONE. prompt_toolkit's application
    # module does `from subprocess import Popen`, and it is loaded transitively
    # before the traps are armed, so it held a live spawn route through the
    # entire trapped section. Nothing in this package imports prompt_toolkit,
    # which is why reporting it would have been tempting and wrong: a trap whose
    # coverage depends on which third-party packages happen to be installed is a
    # coincidence, not a guarantee.
    check("no module holds a pre-bound reference to the real subprocess.run or "
          "subprocess.Popen after the closure pass -- the only from-import form "
          "an attribute patch cannot reach",
          _payload.get("prebound_holders"), [])
    # NON-DEGENERATE. An empty result above is also what a sweep that inspects
    # nothing produces, so the probe plants a module holding the original --
    # exactly prompt_toolkit's shape -- and requires the closure to find it and
    # to leave it raising.
    check("...and the closure CATCHES a planted holder (negative control)",
          _payload.get("control_found_planted"), True)
    check("...and the planted holder's reference RAISES afterwards, so the "
          "closure rebinds rather than merely listing",
          _payload.get("control_planted_now_blocked"), True)


# ===========================================================================
# 2b. PATH RESOLUTION IS LAZY: IMPORTING config NEEDS NO SIBLING TREE
# ===========================================================================

print("\n" + "=" * 78)
print("2b. oncotriage.config imports with the project root made unreachable")
print("=" * 78)

# THE DEFECT THIS CHECKS FOR, which shipped in pass 20c-2a and is fixed in 2b.
#
# oncotriage/paths.py resolved every sibling directory as a module-level
# assignment, so importing it globbed the whole tree and RAISED if any pattern
# matched nothing. oncotriage/config.py imports paths for load_env_keys, so
# `import oncotriage.config` inherited that: on any machine without the sibling
# tree — a wheel installed into a fresh environment, a CI checkout of "03- Code"
# on its own, a container built before its data volume is mounted — importing
# the config module to read MAX_WORKERS died with a RuntimeError about a glob.
#
# Section 2 above could not catch it. glob.glob() uses os.scandir, not open(),
# so the resolution slipped through every trap in that probe while still being
# the single largest import-time dependency in the package.
#
# The probe below points ONCOTRIAGE_MAIN_PATH at a directory that does not
# exist, which is the loudest possible version of "the tree is not there":
# settings.require_existing_directory() rejects it before any glob runs. Then it
# imports config and reads a tunable, and only afterwards touches a path.

_UNREACHABLE_ROOT = os.path.join(tempfile.gettempdir(),
                                 "oncotriage-root-that-does-not-exist")


def _run_with_env(code: str, cwd: str, extra_env: dict, extra_path: str = None):
    """_run(), plus environment overrides. A None value deletes the variable."""
    env = dict(os.environ)
    if extra_path:
        env["PYTHONPATH"] = extra_path + os.pathsep + env.get("PYTHONPATH", "")
    else:
        env.pop("PYTHONPATH", None)
    for key, value in extra_env.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    proc = subprocess.run([sys.executable, "-c", code], cwd=cwd, env=env,
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


# The order inside the probe is the whole point: import, read a tunable, and
# only THEN read a path. Reporting all three in one payload means a failure
# says which of the three steps was the one that broke.
_LAZY_PATHS = r'''
import json
result = {}

import oncotriage.config as cfg
result["imported"] = True
result["tunable"] = cfg.MAX_WORKERS

import oncotriage.paths as paths
result["nothing_resolved_at_import"] = sorted(paths._RESOLVED)

# Importing the FUNCTION must not resolve anything either -- its default
# argument is keys_path, and a default evaluated at import would defeat this.
from oncotriage.paths import load_env_keys           # noqa: F401
result["nothing_resolved_by_importing_load_env_keys"] = sorted(paths._RESOLVED)

try:
    value = paths.data_fhir_path
    result["read_raised"] = None
    result["read_value"] = value
except Exception as exc:
    result["read_raised"] = type(exc).__name__
    result["read_message"] = str(exc)

print(json.dumps(result))
'''

_rc, _out, _err = _run_with_env(
    _LAZY_PATHS, cwd=_ELSEWHERE,
    extra_env={"ONCOTRIAGE_MAIN_PATH": _UNREACHABLE_ROOT},
    extra_path=_FALLBACK_PATH)

check("the lazy-paths probe ran", _rc, 0)
if _rc != 0:
    fail("importing oncotriage.config with the project root unreachable",
         f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")
else:
    _payload = _last_json(_out) or {}
    check("oncotriage.config imports with the project root unreachable",
          _payload.get("imported"), True)
    check("...and a tunable is readable out of it (12 = MAX_WORKERS)",
          _payload.get("tunable"), 12)
    check("...and importing oncotriage.paths resolved NO path",
          _payload.get("nothing_resolved_at_import"), [])
    check("...and importing load_env_keys resolved no path either",
          _payload.get("nothing_resolved_by_importing_load_env_keys"), [])
    # NON-DEGENERATE. Everything above would also hold for a paths module that
    # had simply stopped resolving anything, ever. The read must still fail, and
    # fail with the message that names the variable to set.
    check("...while actually READING a path still raises",
          _payload.get("read_raised"), "RuntimeError")
    # The variable name is on the SECOND line of require_existing_directory's
    # message ("Set ONCOTRIAGE_MAIN_PATH to the correct location"), so the whole
    # message is carried across, not just its first line.
    check("...and the message names ONCOTRIAGE_MAIN_PATH, so the fix is findable",
          "ONCOTRIAGE_MAIN_PATH" in (_payload.get("read_message") or ""), True)
    check("...and it did NOT quietly return a path",
          "read_value" in _payload, False)

# The other half of the non-degeneracy: with the root restored, the same read
# must SUCCEED and produce a real directory. Without this, a paths module that
# raised unconditionally would pass every check above.
_rc, _out, _err = _run_with_env(
    _LAZY_PATHS, cwd=_ELSEWHERE,
    extra_env={"ONCOTRIAGE_MAIN_PATH": None},
    extra_path=_FALLBACK_PATH)
if _rc != 0:
    fail("the same probe against the real tree",
         f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")
else:
    _payload = _last_json(_out) or {}
    check("with the root reachable again, the same read succeeds",
          _payload.get("read_raised"), None)
    check("...and returns a directory that exists",
          os.path.isdir(_payload.get("read_value") or ""), True)
    check("...and importing was STILL lazy on the machine that has the tree",
          _payload.get("nothing_resolved_at_import"), [])


# ===========================================================================
# 2c. NO PACKAGE MODULE RESOLVES A PATH AT IMPORT — checked one by one
# ===========================================================================

print("\n" + "=" * 78)
print("2c. every package module imports without resolving a single path")
print("=" * 78)

# WHY THIS IS PER-MODULE AND NOT ONE IMPORT OF EVERYTHING.
#
# Check 2b (pass 20c-2b) proved oncotriage.config imports without resolving the
# tree. It checked ONE module, and pass 20c-2c found the hole that left:
# oncotriage/registries/mesh.py carried
#
#     from oncotriage.paths import data_MeSH_path
#
# at module scope. A `from X import name` is an ATTRIBUTE READ, so it fires the
# lazy resolver — that one line globbed the whole sibling directory tree for
# anything that imported the MeSH filter, and oncotriage.agent.deps imports it.
# So importing the AGENT raised on a machine without the data tree, which is the
# exact defect pass 2b existed to remove, surviving one module over for a whole
# pass because nothing checked the other modules.
#
# Every module is now imported in ITS OWN subprocess, with ONCOTRIAGE_MAIN_PATH
# pointed at a directory that does not exist. Its own subprocess matters: import
# order would otherwise hide a second offender behind the first, and a module
# that resolved a path would be indistinguishable from one that merely imported
# a module that did.

_ALL_PKG_MODULES = sorted(
    os.path.relpath(f, _code_dir)[:-3].replace(os.sep, ".")
    for f in _PKG_FILES
    if not f.endswith("__init__.py")
)

check("the module list is the size the tree says it is (non-degeneracy)",
      len(_ALL_PKG_MODULES) >= 61, True)
check("...and includes the one that used to resolve a path at import",
      "oncotriage.registries.mesh" in _ALL_PKG_MODULES, True)
# Pass 20c-3a's three worst offenders, named individually so a module dropped
# from the tree cannot quietly leave this sweep. fhir.explore is the one that
# CREATED A DIRECTORY at import; retrieval.indexer is the one that LOADED A
# MODEL at import.
for _added in ("oncotriage.embedding", "oncotriage.fhir.clean",
               "oncotriage.fhir.generate", "oncotriage.fhir.explore",
               "oncotriage.retrieval.indexer",
               "oncotriage.retrieval.index_validator"):
    check(f"...and covers {_added} (new in pass 20c-3a)",
          _added in _ALL_PKG_MODULES, True)
# Pass 20c-3b's five. storage.queries is the one that matters most: File 16 ran
# forty queries against the production database at load time, so this sweep is
# what says it does not any more.
for _added in ("oncotriage.storage.maintenance", "oncotriage.storage.queries",
               "oncotriage.api.server", "oncotriage.monitoring.drift",
               "oncotriage.batch.runner"):
    check(f"...and covers {_added} (new in pass 20c-3b)",
          _added in _ALL_PKG_MODULES, True)

# Pass 20c-3c-1's thirteen. dashboard.data is the one that matters most here:
# it is the module that reads inferences.db, and the whole reason it says
# `paths.inferences_path` inside each function body rather than importing the
# name at module scope is so that THIS sweep -- run with the project root
# pointed at a directory that does not exist -- passes. A `from
# oncotriage.paths import inferences_path` there is an ATTRIBUTE read, which
# fires the lazy resolver at import; that is the exact defect pass 20c-2c found
# one module over in registries/mesh.py.
_DASHBOARD_MODULES = [
    "oncotriage.dashboard.app",
    "oncotriage.dashboard.data",
    "oncotriage.dashboard.sidebar",
    "oncotriage.dashboard.tiers",
    "oncotriage.dashboard.tabs.cost_tokens",
    "oncotriage.dashboard.tabs.demographics",
    "oncotriage.dashboard.tabs.drift",
    "oncotriage.dashboard.tabs.match_quality",
    "oncotriage.dashboard.tabs.overview",
    "oncotriage.dashboard.tabs.patient_explorer",
    "oncotriage.dashboard.tabs.performance",
    "oncotriage.dashboard.tabs.reproducibility",
    "oncotriage.dashboard.tabs.trial_explorer",
]
check("...and covers all thirteen dashboard modules (new in pass 20c-3c-1)",
      sorted(m for m in _DASHBOARD_MODULES if m not in _ALL_PKG_MODULES), [])

# Pass 20c-3d's six. fixtures.replay is the one that matters most: it is the
# only module in the package with a MODULE-LEVEL SIDE EFFECT -- it sets
# ONCOTRIAGE_DEFER_LOCAL_MODELS above its own imports, because
# oncotriage.agent.deps reads that variable once at ITS import and an assignment
# underneath would reach nothing and load ~110 MB of MedCPT on every replay.
# This sweep runs each module in its own subprocess with the project root
# pointed at a directory that does not exist, so it also proves that the side
# effect is the ONLY thing importing it does.
_PASS_3D_MODULES = [
    "oncotriage.ablation.study",
    "oncotriage.ablation.analysis",
    "oncotriage.evaluation.sampling",
    "oncotriage.evaluation.cohort_diff",
    "oncotriage.fixtures.capture",
    "oncotriage.fixtures.replay",
]
check("...and covers all six pass-20c-3d modules",
      sorted(m for m in _PASS_3D_MODULES if m not in _ALL_PKG_MODULES), [])

# Pass 20c-3c-2's five. retrieval.qdrant_backup is the one that matters most:
# "29- Download Qdrant Data.py" was the LAST file in the repository with no
# __main__ guard and no function -- every statement ran at module level, so
# loading it created a directory, listed every Qdrant collection and scrolled
# every point over the network. orchestration.dag_generator is second: File 23
# assembled dag_content at module level and the middle third of it is
# %-formatted with code_path, keys_path and data_trial_path, so building it
# resolved THREE lazy paths at import. That is precisely what this sweep, run
# with the project root pointed at a directory that does not exist, catches.
for _added in ("oncotriage.retrieval.qdrant_backup",
               "oncotriage.orchestration.home",
               "oncotriage.orchestration.airflow_setup",
               "oncotriage.orchestration.dag_generator",
               "oncotriage.orchestration.airflow_manager"):
    check(f"...and covers {_added} (new in pass 20c-3c-2)",
          _added in _ALL_PKG_MODULES, True)

_PER_MODULE_PROBE = (
    "import json, sys\n"
    "import oncotriage.paths as _p\n"
    "import %s\n"
    "print(json.dumps({'resolved': sorted(_p._RESOLVED)}))\n"
)


def _probe_one_module(module: str):
    """Import ONE module in its own subprocess. Returns (module, complaint|None)."""
    rc, out, err = _run_with_env(
        _PER_MODULE_PROBE % module, cwd=_ELSEWHERE,
        extra_env={"ONCOTRIAGE_MAIN_PATH": _UNREACHABLE_ROOT},
        extra_path=_FALLBACK_PATH)
    if rc != 0:
        return module, f"import FAILED: {(err.strip().splitlines() or ['?'])[-1][:90]}"
    payload = _last_json(out) or {}
    if payload.get("resolved"):
        return module, f"resolved {payload['resolved']}"
    return module, None


# RUN THEM CONCURRENTLY (pass 20c-3a). Serially, 26 modules took about nine
# minutes -- every one pays a fresh interpreter start plus openai, qdrant_client
# and (for the agent modules) langgraph, and pass 3a takes it to 33. A test
# nobody runs because it is slow is a test that is not protecting anything.
#
# A THREAD pool, not a process pool, and that is the right tool rather than a
# compromise: each unit of work is already its own subprocess, so the parent
# thread spends its entire life blocked in subprocess.run() with the GIL
# released. Adding worker PROCESSES would add a second layer of interpreter
# startup to fork off a process that only waits.
#
# The probes are independent BY CONSTRUCTION -- separate processes, no shared
# state, one read-only source tree -- which is the property that makes this safe
# and is why the sweep was written per-module in the first place. Results are
# collected into a dict and sorted before the assertion, so the report is
# deterministic however the pool happens to schedule them.
_eager = {}
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as _pool:
    for _module, _complaint in _pool.map(_probe_one_module, _ALL_PKG_MODULES):
        if _complaint:
            _eager[_module] = _complaint

check("no package module resolves a path (or fails) when imported with the "
      "project root unreachable",
      sorted(f"{m}: {why}" for m, why in _eager.items()), [])


# ===========================================================================
# 2d. IMPORTING THE AGENT LOADS NO MODEL, WITH THE DEFERRAL SWITCH UNSET
# ===========================================================================

print("\n" + "=" * 78)
print("2d. the agent imports with ONCOTRIAGE_DEFER_LOCAL_MODELS unset")
print("=" * 78)

# "13- LangGraph Agent.py" loaded MedCPT and FastEmbed at exec() time, lines
# 414-434, unless ONCOTRIAGE_DEFER_LOCAL_MODELS=1 was set BEFORE the exec. That
# switch existed for one caller — 46- Fixture Replay.py — and every other file
# that chained File 13 paid ~110 MB and tens of seconds just by being read.
#
# Pass 20c-2c made the loads lazy, so the switch must no longer matter AT IMPORT.
# Section 2 above already imports the agent under traps, but it inherits this
# process's environment; this probe DELETES the variable, so a regression that
# moved the load back to import time cannot hide behind a value someone else set.
#
# The switch itself is not gone and is checked to still exist: it is the second
# line of defence, turning a forgotten stand-in into a named RuntimeError rather
# than a silent real model call.

_NO_DEFER = r'''
import json, sys
import oncotriage.agent.deps as d
import oncotriage.agent.graph          # the module that imports every stage
print(json.dumps({
    "defer_flag_seen": d._DEFER_LOCAL_MODELS,
    "switch_name": d.DEFER_LOCAL_MODELS_ENV,
    "heavy": [m for m in ("torch", "transformers", "sentence_transformers")
              if m in sys.modules],
    "nothing_cached": sorted(d._CACHE),
}))
'''

_rc, _out, _err = _run_with_env(_NO_DEFER, cwd=_ELSEWHERE,
                                extra_env={"ONCOTRIAGE_DEFER_LOCAL_MODELS": None},
                                extra_path=_FALLBACK_PATH)
check("the agent imports with the deferral switch unset", _rc, 0)
if _rc != 0:
    fail("agent import without the deferral switch",
         f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")
else:
    _payload = _last_json(_out) or {}
    # NON-DEGENERATE: the switch really was unset, so "no model loaded" is not
    # the deferral placeholder path being taken.
    check("...with the switch genuinely OFF, so this is not the placeholder path",
          _payload.get("defer_flag_seen"), False)
    check("...and the switch still exists, as the second line of defence",
          _payload.get("switch_name"), "ONCOTRIAGE_DEFER_LOCAL_MODELS")
    check("...and NO model-bearing library was imported",
          _payload.get("heavy"), [])
    check("...and deps built and cached nothing at all",
          _payload.get("nothing_cached"), [])


# ===========================================================================
# 2e. THE DEPENDENCY SEAM
# ===========================================================================

print("\n" + "=" * 78)
print("2e. deps overrides are what the agent reaches, and they are checkable")
print("=" * 78)

# THE DEFECT THIS SEAM REPLACES, and it is the reason pass 20c-2c happened.
#
# Files 45 and 46 redirected the pipeline by rebinding four names --
# openai_client, qdrant_client, _bm25_query_model, medcpt_score_pairs -- in the
# shared exec namespace. That worked only because every project file was exec'd
# into one dict. A module function resolves its globals in its own module, so
# those rebindings would have reached NOTHING: 46- Fixture Replay.py would have
# sent every Stage 5 prompt to the real OpenAI endpoint, been billed for it, and
# still reported that all twelve fixtures replayed clean. Nothing would raise.
#
# Everything below runs in a subprocess with no credentials required: the
# accessors are exercised with overrides installed, so no real client is ever
# built.

_SEAM = r'''
import json
from oncotriage.agent import deps, models

sentinels = {k: object() for k in deps.OVERRIDE_KEYS}
result = {}

# 1. Nothing installed -> get_override is UNSET for every key.
result["unset_before"] = [k for k in deps.OVERRIDE_KEYS
                          if deps.get_override(k) is not deps.UNSET]

# 2. An unknown key is REFUSED, not ignored. A silently-dropped override is the
#    failure this whole module exists to make impossible.
try:
    deps.set_override("openai_clientt", object())
    result["typo_refused"] = False
except KeyError:
    result["typo_refused"] = True

# 3. Every typed accessor returns the override, by identity.
saved = deps.set_overrides(sentinels)
accessors = {
    "openai_client":    deps.get_openai_client,
    "qdrant_client":    deps.get_qdrant_client,
    "bm25_query_model": deps.get_bm25_query_model,
    "medcpt_tokenizer": deps.get_medcpt_tokenizer,
    "medcpt_model":     deps.get_medcpt_model,
    "cancer_registry":  deps.get_cancer_registry,
    "lab_registry":     deps.get_lab_registry,
    "mesh_filter":      deps.get_mesh_filter,
}
result["accessor_identity"] = sorted(
    k for k, fn in accessors.items() if fn() is not sentinels[k]
)

# 4. MEDCPT_SCORER has no accessor here on purpose: its default lives in
#    models, because deps must not import models. models.score_pairs dispatches.
calls = []
deps.set_override(deps.MEDCPT_SCORER, lambda q, t: calls.append((q, tuple(t))) or "scored")
result["scorer_dispatched"] = models.score_pairs("q", ["a", "b"]) == "scored"
result["scorer_saw_the_args"] = calls == [("q", ("a", "b"))]

# 5. restore_overrides puts everything back, and CLEARS what had no previous
#    value rather than pinning it to whatever it resolved to.
deps.restore_overrides(saved)
deps.clear_override(deps.MEDCPT_SCORER)
result["unset_after"] = [k for k in deps.OVERRIDE_KEYS
                         if deps.get_override(k) is not deps.UNSET]
result["active_after"] = deps.active_overrides()

# 6. The context manager restores on the way out, including on an exception.
probe = object()
try:
    with deps.override(deps.QDRANT_CLIENT, probe):
        result["ctx_inside"] = deps.get_qdrant_client() is probe
        raise ValueError("boom")
except ValueError:
    pass
result["ctx_cleared_after_raise"] = deps.get_override(deps.QDRANT_CLIENT) is deps.UNSET

print(json.dumps(result))
'''

_rc, _out, _err = _run(_SEAM, cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
check("the seam probe ran", _rc, 0)
if _rc != 0:
    fail("dependency seam", f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")
else:
    _payload = _last_json(_out) or {}
    check("no override is installed on a fresh import", _payload.get("unset_before"), [])
    check("an unknown override key raises rather than being ignored",
          _payload.get("typo_refused"), True)
    check("every typed accessor returns the installed override, by identity",
          _payload.get("accessor_identity"), [])
    check("models.score_pairs dispatches to the MEDCPT_SCORER override",
          _payload.get("scorer_dispatched"), True)
    check("...and hands it (query, trial_texts) unchanged",
          _payload.get("scorer_saw_the_args"), True)
    # NON-DEGENERATE, and this is the half that matters: an accessor that
    # ALWAYS returned the sentinel would pass check 3 and would also leave the
    # overrides installed forever. restore must actually restore.
    check("restore_overrides clears every override that had no previous value",
          _payload.get("unset_after"), [])
    check("...and active_overrides() then reports none",
          _payload.get("active_after"), [])
    check("the override context manager installs inside the block",
          _payload.get("ctx_inside"), True)
    check("...and restores even when the block raises",
          _payload.get("ctx_cleared_after_raise"), True)


# ===========================================================================
# 2f. EXACTLY ONE CONSTRUCTION SITE FOR THE BM25 SPARSE MODEL
# ===========================================================================

print("\n" + "=" * 78)
print("2f. the FastEmbed BM25 sparse model is constructed in exactly one place")
print("=" * 78)

# THE HAZARD THIS CLOSES, and it is a correctness hazard rather than a tidiness
# one.
#
# Before pass 20c-3a, SparseTextEmbedding("Qdrant/bm25") was constructed THREE
# times, independently:
#
#     "11- RAG Trial Indexer.py" line 53      index time, module level
#     oncotriage/agent/deps.py                query time, lazily
#     "12- RAG Trial Indexer Validator.py"    inside stage2_retrieval_tests()
#
# The first two are the two halves of ONE job: File 11 writes each trial's three
# BM25 fields into Qdrant's sparse vectors, and the agent encodes the patient
# query that is scored against them. BM25 sparse vectors are TOKEN-ID vectors
# over the model's vocabulary, so if the two sides ever named different models,
# the query's indices would address different terms than the documents' indices
# do. Qdrant computes a dot product over whatever indices it is handed: it would
# go on returning results, nothing would raise, no counter would move, and the
# only symptom would be that retrieval quality fell.
#
# The third one is worse. A VALIDATOR carrying its own encoder cannot detect the
# drift it exists to detect -- it would report "All 5 queries returned results"
# against an index built with a vocabulary it does not share.
#
# There is now one construction site and both sides reach it. This check is what
# stops a fourth appearing.
#
# COUNTED BY AST, NOT BY GREP. A grep cannot tell a call from a mention in a
# docstring, and three of this package's docstrings now name the class precisely
# because they explain why there is only one call.
#
# BOTH CALL SHAPES ARE MATCHED (pass 20c-3b). The pass-3a detector matched
# ast.Name only, so it saw
#
#     from fastembed import SparseTextEmbedding
#     SparseTextEmbedding(model_name="Qdrant/bm25")        <-- caught
#
# and was blind to the attribute form, which needs no import line at all and is
# what anyone reaching for the class from a module that already imports fastembed
# would naturally write:
#
#     import fastembed
#     fastembed.SparseTextEmbedding(model_name="Qdrant/bm25")   <-- MISSED
#
# A second construction site written that way would have passed this check
# silently, which is precisely the failure the check exists to prevent: two
# independently-loaded BM25 vocabularies produce a dot product over mismatched
# token IDs, so Qdrant keeps returning results, nothing raises, and only
# retrieval quality falls. Both forms are counted now, and the negative control
# below plants ONE OF EACH so neither branch of the detector can rot unnoticed.


def _sparse_model_constructions(path: str):
    """Line numbers where SparseTextEmbedding(...) is CALLED in `path`.

    Matches both `SparseTextEmbedding(...)` (ast.Name) and
    `something.SparseTextEmbedding(...)` (ast.Attribute).
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    hits = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        func = n.func
        if isinstance(func, ast.Name) and func.id == "SparseTextEmbedding":
            hits.append(n.lineno)
        elif isinstance(func, ast.Attribute) and func.attr == "SparseTextEmbedding":
            hits.append(n.lineno)
    return sorted(hits)


_construction_sites = {}
for _f in _PKG_FILES:
    _hits = _sparse_model_constructions(_f)
    if _hits:
        _construction_sites[os.path.relpath(_f, _code_dir)] = _hits

check("exactly one package file constructs SparseTextEmbedding",
      sorted(_construction_sites), ["oncotriage/embedding.py"])
check("...and it constructs it exactly once",
      len(_construction_sites.get("oncotriage/embedding.py", [])), 1)

# NON-DEGENERATE. Everything above would also hold if the detector simply never
# matched anything -- a renamed class, a broken walk, a _PKG_FILES list that had
# gone empty. The detector is shown to FIND constructions in a copy that has
# them planted in it, ONE OF EACH CALL SHAPE, so a detector that lost either
# branch fails here rather than going quietly green on the shipped tree.
_BM25_PLANT_ROOT = tempfile.mkdtemp(prefix="oncotriage_bm25_")
try:
    shutil.copytree(_PKG_DIR, os.path.join(_BM25_PLANT_ROOT, "oncotriage"))
    _PLANTED = os.path.join(_BM25_PLANT_ROOT, "oncotriage", "retrieval", "indexer.py")
    with open(_PLANTED, "a", encoding="utf-8") as _fh:
        # The bare-name form the pass-3a detector already caught...
        _fh.write('\n\ndef _planted_second_loader():\n'
                  '    return SparseTextEmbedding(model_name="Qdrant/bm25")\n')
    check("the detector CATCHES a bare-name construction planted in a copy",
          len(_sparse_model_constructions(_PLANTED)), 1)

    # ...and the ATTRIBUTE form, which it did NOT catch before pass 20c-3b.
    # This is the shape that needs no import line, so it is the one someone
    # writes without noticing they have created a second vocabulary.
    with open(_PLANTED, "a", encoding="utf-8") as _fh:
        _fh.write('\n\nimport fastembed\n\n'
                  'def _planted_third_loader():\n'
                  '    return fastembed.SparseTextEmbedding(model_name="Qdrant/bm25")\n')
    check("...and the ATTRIBUTE form, fastembed.SparseTextEmbedding(...), which "
          "the pass-3a bare-name detector was blind to",
          len(_sparse_model_constructions(_PLANTED)), 2)

    check("...and the shipped indexer has none of either, which is what makes "
          "the planted ones the only difference",
          _sparse_model_constructions(
              os.path.join(_PKG_DIR, "retrieval", "indexer.py")), [])
finally:
    shutil.rmtree(_BM25_PLANT_ROOT, ignore_errors=True)

# The two SIDES must reach the same accessor, which is a different claim from
# "there is one construction". Asserted structurally: both files must name
# get_bm25_sparse_model.
_DEPS_PY = os.path.join(_PKG_DIR, "agent", "deps.py")
_INDEXER_PY = os.path.join(_PKG_DIR, "retrieval", "indexer.py")
_VALIDATOR_PY = os.path.join(_PKG_DIR, "retrieval", "index_validator.py")


def _calls_name(path: str, name: str) -> bool:
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == name:
            return True
        if isinstance(func, ast.Attribute) and func.attr == name:
            return True
    return False


check("the agent's query encoder reaches the one accessor",
      _calls_name(_DEPS_PY, "get_bm25_sparse_model"), True)
check("...and so does the indexer, which wrote the vectors it is scored against",
      _calls_name(_INDEXER_PY, "get_bm25_sparse_model"), True)
check("...and the validator reaches it through the agent's own accessor, so it "
      "tests the encoder the agent actually uses",
      _calls_name(_VALIDATOR_PY, "get_bm25_query_model"), True)


# ===========================================================================
# 2g. NO FUNCTION-LOCAL SHADOWS A MODULE-LEVEL IMPORT
# ===========================================================================

print("\n" + "=" * 78)
print("2g. no function binds a local with the same name as a module-level import")
print("=" * 78)

# THE DEFECT THIS CAUGHT, in this very pass, twice.
#
# Converting a file that read names out of the shared exec namespace means
# prefixing those reads with the module they now come from. Two of the five
# conversions collided with a LOCAL VARIABLE that already had that name:
#
#   index_validator.stage1_index_health()   binds `config = info.config.params.vectors`
#   indexer._flush_embed_buffer()           binds `embedding` as a zip() loop variable
#
# In Python a name assigned ANYWHERE in a function is local for the WHOLE of it,
# so `config.COLLECTION_NAME` three lines above that assignment is not a module
# attribute read -- it is UnboundLocalError. The validator would have died in its
# first check on every run, and the indexer would have died the first time it
# flushed an embedding batch, i.e. partway through a real index build.
#
# Neither was caught by importing the module, because both are runtime paths.
# Both were caught by this scan, which is why it is now permanent.
#
# It is a WARNING-LEVEL smell in general and an ERROR here: the package's
# convention is that a module-level import name (`paths`, `config`, `deps`,
# `embedding`) is reachable from every function body, and a local that shadows
# one silently withdraws that.


def _own_scope_bindings(func):
    """Names bound in `func`'s OWN scope. Nested scopes excluded.

    Nested function and class bodies are NOT descended into: a name local to an
    inner function is not local to the outer one, so counting it would flag
    index_trials() for a variable that only _flush_embed_buffer() binds -- a
    false positive that would eventually make this check something people work
    around rather than fix.

    Comprehension targets are excluded for the same reason: since Python 3 a
    comprehension has its own scope, so `[x for config in items]` does not bind
    `config` in the enclosing function and cannot shadow anything there.
    """
    scopes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
    comprehensions = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
    bound = set()

    # Parameters belong to this scope. Defaults and decorators do not -- they are
    # evaluated in the enclosing one -- so only func.args is walked, not func.
    for node in ast.walk(func.args):
        if isinstance(node, ast.arg):
            bound.add(node.arg)

    def walk(nodes):
        for node in nodes:
            if isinstance(node, scopes):
                # `def inner():` binds `inner` HERE, but nothing inside it does.
                name = getattr(node, "name", None)
                if name:
                    bound.add(name)
                continue
            if isinstance(node, comprehensions):
                continue
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                bound.add(node.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    bound.add((alias.asname or alias.name).split(".")[0])
            walk(ast.iter_child_nodes(node))

    walk(func.body)
    return bound


def _shadowed_imports(path: str):
    """[(function, [shadowed names])] for `path`, every function scope in it."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    imported = set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                imported.add((alias.asname or alias.name).split(".")[0])

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        clash = sorted(_own_scope_bindings(node) & imported)
        if clash:
            found.append((node.name, clash))
    return found


_shadows = {}
for _f in _PKG_FILES:
    _hits = _shadowed_imports(_f)
    if _hits:
        _shadows[os.path.relpath(_f, _code_dir)] = _hits

check("no function in the package shadows one of its module's imports",
      sorted(f"{f}: {hits}" for f, hits in _shadows.items()), [])

# NON-DEGENERATE, IN BOTH DIRECTIONS. An empty result is also what a broken
# walker returns, and a walker that flagged everything would be worked around
# rather than fixed. The scanner is run against a table of six snippets written
# into a temporary file: the two REAL pass-3a defects reproduced verbatim, and
# four cases it must NOT report.
#
# The two false-positive guards are the reason the scanner is scope-precise
# rather than a flat ast.walk:
#
#   nested_only    a name local to an INNER function is not local to the outer,
#                  so a flat walk would flag index_trials() for a variable only
#                  _flush_embed_buffer() binds.
#   comprehension  since Python 3 a comprehension target has its own scope, so
#                  `[x for config in items]` shadows nothing.
_SHADOW_CASES = {
    # REAL DEFECT 1 -- index_validator.stage1_index_health, reproduced.
    "the real `config` local in stage1_index_health": (
        "from oncotriage import config\n"
        "def stage1_index_health():\n"
        "    if config.COLLECTION_NAME in x:\n"
        "        pass\n"
        "    config = info.config.params.vectors\n",
        [("stage1_index_health", ["config"])]),
    # REAL DEFECT 2 -- indexer._flush_embed_buffer: a zip() loop variable in a
    # NESTED function. The inner function is what must be named, not the outer.
    "the real `embedding` loop variable in a nested flush function": (
        "from oncotriage import embedding\n"
        "def index_trials(trials):\n"
        "    def _flush(buf):\n"
        "        m = embedding.get_bm25_sparse_model()\n"
        "        for item, embedding, t in zip(a, b, c):\n"
        "            pass\n"
        "    _flush(1)\n",
        [("_flush", ["embedding"])]),
    "a shadowing PARAMETER, which is the same defect by another route": (
        "from oncotriage import paths\n"
        "def g(paths):\n"
        "    return paths\n",
        [("g", ["paths"])]),
    "a nested-only local does NOT flag its enclosing function": (
        "import json\n"
        "def outer():\n"
        "    def inner():\n"
        "        json = 1\n"
        "        return json\n"
        "    return inner\n",
        [("inner", ["json"])]),
    "a comprehension target shadows nothing (its own scope since Python 3)": (
        "import json\n"
        "def f(items):\n"
        "    return [json for json in items]\n",
        []),
    "a clean function is reported clean": (
        "from oncotriage import paths\n"
        "def g(directory):\n"
        "    return paths.data_fhir_path + directory\n",
        []),
}

_SHADOW_DIR = tempfile.mkdtemp(prefix="oncotriage_shadow_")
try:
    for _label, (_code, _expected) in _SHADOW_CASES.items():
        _probe_path = os.path.join(_SHADOW_DIR, "probe.py")
        with open(_probe_path, "w", encoding="utf-8") as _fh:
            _fh.write(_code)
        check(f"shadow scan: {_label}", _shadowed_imports(_probe_path), _expected)
finally:
    shutil.rmtree(_SHADOW_DIR, ignore_errors=True)

# And the shipped fix itself: the validator must NOT import `config` as a module,
# because stage1_index_health() genuinely binds a local of that name.
check("...and the shipped validator imports the config NAMES, not the module, "
      "which is what makes its `config` local harmless",
      "from oncotriage import config\n" in open(
          os.path.join(_PKG_DIR, "retrieval", "index_validator.py"),
          encoding="utf-8").read(), False)
check("...while still reading COLLECTION_NAME out of the config module",
      "COLLECTION_NAME," in open(
          os.path.join(_PKG_DIR, "retrieval", "index_validator.py"),
          encoding="utf-8").read(), True)


# ===========================================================================
# 2h. NOTHING IS DECLARED AND NEVER READ  (pass 20c-3i)
# ===========================================================================

print("\n" + "=" * 78)
print("2h. no unused import, no module constant that nothing anywhere reads")
print("=" * 78)

# WHY THIS EXISTS, and it is a limit of the equivalence proofs rather than a
# style preference.
#
# Every conversion pass since 20c-2a has been accepted on an EQUIVALENCE PROOF:
# ast.unparse the definitions on both sides and require them to match. That
# proof is powerful and it has one blind spot it cannot close by construction --
# IT ONLY COMPARES WHAT IS THERE. A name that is DECLARED and never READ is
# equivalent to itself on both sides of every diff, forever, and no amount of
# unparsing will ever mention it.
#
# Pass 20c-3c-2 shipped one: orchestration/airflow_manager.py declared
# PASSWORD_SOURCE_ARGUMENT, a constant naming a value password_source() can
# NEVER return, and it survived a full pass with an equivalence proof and 244
# checks. It was found by reading, not by testing. Pass 20c-3i found two more of
# the same shape by running this scan for the first time:
#
#   PASSWORD_SOURCE_ENV      declared for callers to assert against, and the one
#                            place that could have used it stored the resolver's
#                            own string instead -- so the constant was inert and
#                            one rename away from naming a value the function
#                            could no longer return.
#   deps.RESOLUTION_STATES   documented as "every value resolution_state() can
#                            return ... so a caller can branch on it
#                            exhaustively", read by nothing.
#
# Both are now load-bearing (see airflow_manager and check 5c respectively), so
# this scan is what turned a comment into an invariant twice.
#
# So the scan runs EVERY TIME, not on suspicion. Three shapes:
#
#   (i)   a module-level import bound and never read in its own module;
#   (ii)  a module-level CONSTANT that no .py file in the repository reads;
#   (iii) shadowed names -- already covered by 2g above, which is the reason
#         this section does not repeat it. Named here so a reader looking for
#         the third shape finds where it lives rather than concluding it is
#         missing.

_REPO_PY = sorted(
    _PKG_FILES
    + [os.path.join(_code_dir, n) for n in os.listdir(_code_dir)
       if n.endswith(".py")]
)


def _all_reads(paths, blob_exclude=()):
    """Every identifier read anywhere in `paths`, plus every string literal.

    THIS FILE IS EXCLUDED FROM THE STRING CORPUS, and that exclusion is the
    difference between a check and a tautology. This file pins several
    historical name inventories as lists of string literals -- _PRE_20C_NAMES,
    _PRE_2A_RUNTIME_NAMES, the File 21 surface list -- and it also holds the
    exemption dict below. Counted as reads, those strings mask any constant they
    happen to mention, and the exemption list would be READ BY THE SCAN THAT
    CONSULTS IT: name a constant as exempt and it stops being reported for that
    reason alone, whether or not the exemption is removed later. That was the
    first version's behaviour and it reported zero findings; excluding this
    file's strings takes it to three, two of which are the ones the exemptions
    were written for and one of which was new.

    Its ast Name/Attribute/import reads still count. Only its string LITERALS
    are dropped.

    A READ, NOT A BINDING. ``ast.Name`` and ``ast.Attribute`` are only counted
    when their ctx is NOT Store, and this is the whole check rather than a
    detail: the first version of this function counted every Name node, so
    ``PASSWORD_SOURCE_ENV = ...`` counted as a read OF ITSELF, every constant in
    the package looked read, and the scan below passed VACUOUSLY. It was the two
    negative controls at the bottom of this section that said so -- they are the
    reason the defect lasted one run instead of shipping, which is the argument
    for controls stated as a fact rather than a principle.

    THE STRING LITERALS ARE NOT SLOPPINESS, they are the third reference form.
    This file reads oncotriage.paths.PATH_NAMES through
    ``getattr(module, "PATH_NAMES", ())`` and reads several package names inside
    the source of subprocess probes, which are ordinary Python strings to any
    AST walk. A scan that counted only ast.Name and ast.Attribute would report
    PATH_NAMES as dead -- which it did, on its first run, and which is exactly
    the "a check that names a symbol must cover every reference form" rule this
    section is an instance of. Substring matching over string constants is
    deliberately generous: a false NEGATIVE here is a missed defect the reader
    can still find, while a false POSITIVE is a check people learn to route
    around.

    A ``from X import NAME`` is a read too, and it is the fourth form -- it is
    how "03- Config.py" re-exports the tunables and how every shim reaches the
    package. It is neither a Name nor an Attribute node, so it is collected
    explicitly.
    """
    names, blobs = set(), []
    for path in paths:
        try:
            tree = ast.parse(open(path, encoding="utf-8").read(), path)
        except SyntaxError:
            continue
        collect_strings = os.path.abspath(path) not in blob_exclude
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                if not isinstance(node.ctx, ast.Store):
                    names.add(node.id)
            elif isinstance(node, ast.Attribute):
                if not isinstance(node.ctx, ast.Store):
                    names.add(node.attr)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    if alias.name != "*":
                        names.add(alias.name.split(".")[-1])
            elif (collect_strings and isinstance(node, ast.Constant)
                  and isinstance(node.value, str)):
                blobs.append(node.value)
    return names, "\n".join(blobs)


def _named_in(name, blob):
    """True if `name` appears in `blob` on WORD BOUNDARIES.

    A plain ``in`` test was the first version and it was too generous in a way
    that mattered: ``ECOG_LOINC_PANEL_CODE`` is a substring of
    ``_ECOG_LOINC_PANEL_CODE``, a DIFFERENT constant in a different module, so
    a bare substring match reported the first as read because the second was
    named somewhere. Two constants whose names differ by a leading underscore
    are exactly the pair this project keeps (fhir/generate.py and
    fhir/parser.py each name the same LOINC codes), so this is the common case
    rather than a corner.
    """
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
                     blob) is not None


def _unused_imports(path):
    """Module-level import bindings never read in their own module.

    ``__init__.py`` files are skipped whole: their entire job is to bind names
    for somebody else to import, so "unread here" is their normal state. A line
    carrying ``noqa`` is skipped for the same reason, explicitly declared.
    """
    if os.path.basename(path) == "__init__.py":
        return []
    src = open(path, encoding="utf-8").read()
    lines = src.splitlines()
    tree = ast.parse(src, path)
    bound = {}
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or (
                    alias.name.split(".")[0] if isinstance(node, ast.Import)
                    else alias.name)
                bound.setdefault(local, node.lineno)
    reads = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            reads.add(node.id)
        elif isinstance(node, ast.Attribute):
            reads.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            reads.add(node.value)
    return sorted(
        f"{local} (line {lineno})"
        for local, lineno in bound.items()
        if local not in reads and "noqa" not in lines[lineno - 1]
    )


def _module_constants(path):
    """Module-level ALL_CAPS assignment targets, with their line numbers."""
    tree = ast.parse(open(path, encoding="utf-8").read(), path)
    out = []
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        for name in targets:
            stripped = name.lstrip("_")
            if stripped and stripped.upper() == stripped and not stripped.isdigit():
                out.append((name, node.lineno))
    return out


# --- (i) unused imports ----------------------------------------------------
_unused = {}
for _f in _PKG_FILES:
    _hits = _unused_imports(_f)
    if _hits:
        _unused[os.path.relpath(_f, _code_dir)] = _hits

check("no package module binds an import it never reads",
      sorted(f"{f}: {hits}" for f, hits in _unused.items()), [])

# --- (ii) never-read module constants --------------------------------------
#
# THE EXEMPTIONS ARE A CLOSED LIST WITH AN ARGUMENT EACH, not a suppression
# file. A constant earns a place here only by being documentation whose whole
# purpose is to be visible in the source and never used; anything else is a
# defect and gets fixed instead. The list being closed is the point -- a new
# never-read constant fails this check rather than joining the list quietly.
_UNREAD_CONSTANT_EXEMPTIONS = {
    # fhir/generate.py names the two LOINC codes ADJACENT to the ECOG score
    # code, explicitly so that "nobody 'corrects' the score code to one of
    # them" -- its own comment says so, in the source, above the assignments.
    # A code whose purpose is to be read by a human and never by the program is
    # the one legitimate instance of this shape, and both are external-standard
    # facts, which CLAUDE.md requires be named constants rather than tunables.
    "oncotriage/fhir/generate.py": ["ECOG_LOINC_PANEL_CODE",
                                    "ECOG_LOINC_INTERPRETATION_CODE"],
    # TRIAL_STATUS_FULL IS DEAD, AND IT WAS DEAD BEFORE THE SPLIT.
    # "21- Streamlit Dashboard.py" bound it at its line 3853 and referenced it
    # nowhere -- checked against `git show ae3f6c6^` -- while writing the same
    # string as a literal in three places. The per-trial classifiers in
    # patient_explorer and trial_explorer return '✅ Eligible' for the top
    # bucket, so this constant names a value the per-trial vocabulary never
    # produces: the PASSWORD_SOURCE_ARGUMENT shape exactly.
    #
    # It is EXEMPTED RATHER THAN DELETED because section 6 of this file pins
    # every one of the 22 names File 21 bound and requires each to still be
    # exported by the module that owns it. Removing the constant would fail that
    # check correctly -- the pre-split surface really did include it -- so
    # deleting it is a change to the pinned surface and belongs in a pass that
    # says so. RECORDED AS A FOLLOW-UP: drop TRIAL_STATUS_FULL, drop its two
    # entries from the File 21 surface lists below, and replace the three
    # '✅ Full Match' literals with a per-patient constant that has a home.
    "oncotriage/dashboard/tiers.py": ["TRIAL_STATUS_FULL"],
    # TERMINAL_ERROR COMPLETES A CLOSED THREE-MEMBER VOCABULARY, and it was
    # already unread before pass 20c-3d moved it here: `git grep TERMINAL_ERROR
    # HEAD` over the whole repository returns exactly one line, its own
    # assignment in "45- Fixture Capture.py". This scan covers the PACKAGE, so
    # the move is what surfaced it rather than what created it.
    #
    # It is exempted rather than deleted, and rather than made load-bearing.
    # Deleting it would leave TERMINAL_FINALIZE and TERMINAL_NO_CANDIDATES named
    # beside each other and tell a reader that those are the only two values
    # result["terminal_node"] can carry -- which is false: node_error_handler
    # stamps this third one, and a fixture that recorded such a run is a real
    # (if useless) artifact. Making it load-bearing means adding a branch to
    # verify_recording_complete(), which already REFUSES that fixture through
    # its "no Stage 5 request/response pair was recorded" arm; the new branch
    # would improve the diagnosis and change no outcome, so it is a behaviour
    # edit inside a conversion pass whose acceptance criterion is that nothing
    # changed. RECORDED AS A FOLLOW-UP: name the error-handler case explicitly
    # in verify_recording_complete() and drop this entry.
    "oncotriage/fixtures/capture.py": ["TERMINAL_ERROR"],
}

_reads, _string_blob = _all_reads(
    _REPO_PY, blob_exclude={os.path.abspath(__file__)} if "__file__" in globals()
    else {os.path.abspath(os.path.join(_code_dir, "47- Package Split Test.py"))})
_never_read = []
for _f in _PKG_FILES:
    _rel = os.path.relpath(_f, _code_dir).replace(os.sep, "/")
    _exempt = _UNREAD_CONSTANT_EXEMPTIONS.get(_rel, [])
    for _name, _lineno in _module_constants(_f):
        if _name in _exempt or _name in _reads or _named_in(_name, _string_blob):
            continue
        _never_read.append(f"{_rel}:{_lineno} {_name}")

check("every module-level constant in the package is read by something, "
      "somewhere in the repository (or is exempted with an argument)",
      sorted(_never_read), [])

# THE EXEMPTIONS ARE NOT A GET-OUT: each one must still be genuinely unread, or
# it is a stale line hiding the fact that the code moved on. This is the same
# non-degeneracy discipline the rest of the file applies -- an exemption that
# suppresses nothing looks identical to one doing real work.
_dead_exemptions = []
for _rel, _names in _UNREAD_CONSTANT_EXEMPTIONS.items():
    for _name in _names:
        if _name in _reads or _named_in(_name, _string_blob):
            _dead_exemptions.append(f"{_rel}:{_name}")
check("...and every exemption is still needed -- an exempted constant that "
      "something now reads must lose its exemption, not keep it",
      sorted(_dead_exemptions), [])

# The exemptions must still EXIST. An exemption for a constant that has since
# been deleted or renamed is a line that silences nothing and hides the fact
# that the list is stale.
_missing_exemptions = []
for _rel, _names in _UNREAD_CONSTANT_EXEMPTIONS.items():
    _declared = {n for n, _ln in _module_constants(
        os.path.join(_code_dir, _rel.replace("/", os.sep)))}
    _missing_exemptions += [f"{_rel}:{n}" for n in _names if n not in _declared]
check("...and every exempted constant still exists, so the exemption list "
      "cannot go stale unnoticed",
      sorted(_missing_exemptions), [])

# --- NEGATIVE CONTROLS -----------------------------------------------------
#
# "[] unused" is also what a broken scanner returns. Both scans are fired
# against planted cases in a temporary file, and BOTH DIRECTIONS are covered:
# what must be reported, and what must not.
_UNREAD_DIR = tempfile.mkdtemp(prefix="oncotriage-unread-")
try:
    _probe = os.path.join(_UNREAD_DIR, "probe.py")

    # (i) unused-import controls
    for _label, (_code, _expected) in {
        "a bound-and-never-read import is REPORTED": (
            "import os\nimport sys\nprint(os.sep)\n", ["sys (line 2)"]),
        "an import read only through an attribute is NOT reported": (
            "import os\nprint(os.sep)\n", []),
        "an aliased import read under its alias is NOT reported": (
            "import subprocess as sp\nsp.run([])\n", []),
        "an aliased import NEVER read is REPORTED under its alias": (
            "import subprocess as sp\nprint('subprocess')\n", ["sp (line 1)"]),
        "a from-import read as a bare name is NOT reported": (
            "from typing import Dict\nx: Dict = {}\n", []),
        "a noqa line is exempt, as declared": (
            "import sys  # noqa: F401\n", []),
    }.items():
        with open(_probe, "w", encoding="utf-8") as _fh:
            _fh.write(_code)
        check(f"unused-import scan: {_label}", _unused_imports(_probe), _expected)

    # (ii) never-read-constant controls, INCLUDING the three reference forms a
    # constant can be read through. The string-literal form is the one that
    # produced a false positive on the first run of this scan (PATH_NAMES,
    # reached through getattr(module, "PATH_NAMES")), so it is controlled here
    # rather than trusted.
    _reader = os.path.join(_UNREAD_DIR, "reader.py")
    for _label, (_decl, _use, _expect_reported) in {
        "a constant nothing reads is REPORTED":
            ("PLANTED_DEAD = 1\n", "", True),
        "...read as a bare name is not":
            ("PLANTED_DEAD = 1\n", "from probe import PLANTED_DEAD\n"
                                   "print(PLANTED_DEAD)\n", False),
        "...read as an attribute is not":
            ("PLANTED_DEAD = 1\n", "import probe\nprint(probe.PLANTED_DEAD)\n",
             False),
        "...read through a string literal is not (getattr, or a name inside "
        "the source of a subprocess probe)":
            ("PLANTED_DEAD = 1\n",
             "import probe\nprint(getattr(probe, 'PLANTED_DEAD'))\n", False),
        "...named only in a COMMENT is still REPORTED, because a comment is "
        "not a read":
            ("PLANTED_DEAD = 1\n", "# PLANTED_DEAD is mentioned here\n", True),
    }.items():
        with open(_probe, "w", encoding="utf-8") as _fh:
            _fh.write(_decl)
        with open(_reader, "w", encoding="utf-8") as _fh:
            _fh.write(_use)
        _r, _b = _all_reads([_probe, _reader])
        _reported = not ("PLANTED_DEAD" in _r
                         or _named_in("PLANTED_DEAD", _b))
        check(f"unread-constant scan: {_label}", _reported, _expect_reported)

    # THE CONTROL THAT ALREADY EARNED ITS KEEP. The first version of
    # _all_reads() added every ast.Name node without checking ctx, so
    # `PLANTED_DEAD = 1` counted as a read OF ITSELF and the whole scan passed
    # vacuously over the real package. These two cases are what said so, on the
    # first run, before it shipped. Pinned explicitly so the ctx test cannot be
    # dropped again.
    with open(_probe, "w", encoding="utf-8") as _fh:
        _fh.write("PLANTED_DEAD = 1\nPLANTED_DEAD += 1\nfor PLANTED_DEAD in []:\n"
                  "    pass\n")
    _r, _b = _all_reads([_probe])
    check("unread-constant scan: an assignment TARGET is not a read -- the "
          "defect that made the first version of this scan vacuous",
          "PLANTED_DEAD" in _r, False)
    with open(_probe, "w", encoding="utf-8") as _fh:
        _fh.write("PLANTED_DEAD = 1\nprint(PLANTED_DEAD)\n")
    _r, _b = _all_reads([_probe])
    check("...while a genuine load in the same file IS a read, so the ctx test "
          "did not simply blind the scanner",
          "PLANTED_DEAD" in _r, True)

    # WORD BOUNDARIES. `ECOG_LOINC_PANEL_CODE` is a substring of
    # `_ECOG_LOINC_PANEL_CODE`, a different constant in a different module, and
    # this project keeps exactly that pair. A plain `in` test reported the first
    # as read because the second was named.
    check("unread-constant scan: a name is NOT considered read because a "
          "longer name contains it",
          _named_in("ECOG_LOINC_PANEL_CODE", "'_ECOG_LOINC_PANEL_CODE',"),
          False)
    check("...while the name itself, on a boundary, IS found",
          _named_in("ECOG_LOINC_PANEL_CODE", "getattr(m, 'ECOG_LOINC_PANEL_CODE')"),
          True)
finally:
    shutil.rmtree(_UNREAD_DIR, ignore_errors=True)


# ===========================================================================
# 2i. NO DEFINITION LOST A DECORATOR IN A CONVERSION PASS  (pass 20c-3i)
# ===========================================================================

print("\n" + "=" * 78)
print("2i. the decorator inventory of the whole package, pinned")
print("=" * 78)

# THE DEFECT THIS PINS, and it was found by an equivalence proof rather than by
# a check, which is why there is a check now.
#
# Pass 20c-3c-1 extracted the dashboard by slicing each definition from the
# lineno ast reports -- and AST REPORTS A DECORATED FUNCTION'S lineno AT THE
# `def`, NOT AT THE DECORATOR. So @st.fragment was silently dropped from four
# tabs. That is a real behaviour change (a fragment re-runs in isolation;
# without it every interaction in those tabs re-runs the whole app) and nothing
# but the ast.unparse comparison against git would have caught it. The SAME
# slicing approach moved Files 07 through 25.
#
# Pass 20c-3i swept it properly: every FunctionDef / AsyncFunctionDef /
# ClassDef in the package, AT EVERY NESTING DEPTH, compared against the
# pre-split version of the numbered file it came from, with the origin commit
# derived from `git log --diff-filter=A` rather than declared. 404 definitions,
# 314 matched to an origin, 18 carrying decorators, ZERO mismatches, and zero
# decorated origin definitions that failed to reproduce. Three negative controls
# (api/server.py, dashboard/tabs/drift.py, agent/retrieval.py) were each
# stripped in an AST copy and each stopped agreeing with its origin.
#
# WHY THE SWEEP IS NOT THIS CHECK. It needs git history, so it would fail in a
# checkout without it and would compare against whatever HEAD happened to be.
# What survives here is the INVENTORY: the exact decorator list of every
# decorated definition in the package. A future edit that drops one fails, and
# a new decorated definition has to be declared rather than merely appearing.
#
# DEPTH MATTERS AND IS THE REASON THE FIRST SWEEP WAS INCOMPLETE. A top-level
# walk reported api/server.py's four endpoints as having no counterpart at all,
# because create_app() nests them -- so the four definitions in the package
# carrying the MOST decorators were the four a top-level scan could not see.
# Those four also separate their decorators from the `def` with BLANK LINES,
# which is invisible to ast (decorator_list is on the node) and fatal to any
# check written against adjacency.

_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _decorated_definitions(path):
    """{qualified name: [unparsed decorators]} at EVERY depth, decorated only."""
    out = {}

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _DEF_TYPES):
                qual = f"{prefix}.{child.name}" if prefix else child.name
                if child.decorator_list:
                    out[qual] = [ast.unparse(d) for d in child.decorator_list]
                walk(child, qual)
            else:
                walk(child, prefix)

    walk(ast.parse(open(path, encoding="utf-8").read(), path), "")
    return out


_DECORATOR_INVENTORY = {
    # Both qdrant_retry helpers are NESTED inside the node that uses them.
    "oncotriage/agent/retrieval.py::build_bm25_index_from_qdrant._scroll_page":
        ["qdrant_retry"],
    "oncotriage/agent/retrieval.py::node_hybrid_retrieval._batch_scroll":
        ["qdrant_retry"],
    # Nested inside create_app(), with BLANK LINES between decorator and def.
    "oncotriage/api/server.py::lifespan": ["asynccontextmanager"],
    "oncotriage/api/server.py::create_app.health_check": ["app.get('/health')"],
    "oncotriage/api/server.py::create_app.pipeline_info":
        ["app.get('/pipeline/info')"],
    "oncotriage/api/server.py::create_app.match_patient_endpoint":
        ["app.post('/match', response_model=MatchResponse)",
         "limiter.limit(RATE_LIMIT)"],
    "oncotriage/api/server.py::create_app.match_patient_file":
        ["app.post('/match/file', response_model=MatchResponse)",
         "limiter.limit(RATE_LIMIT)"],
    "oncotriage/dashboard/data.py::load_inferences_data":
        ["st.cache_data(ttl=60)"],
    "oncotriage/dashboard/data.py::load_trial_matches_data":
        ["st.cache_data(ttl=60)"],
    "oncotriage/dashboard/data.py::load_drift_metrics_data":
        ["st.cache_data(ttl=60)"],
    # The four that pass 20c-3c-1 dropped and its equivalence proof recovered.
    "oncotriage/dashboard/tabs/drift.py::render_drift_detection_tab":
        ["st.fragment"],
    "oncotriage/dashboard/tabs/patient_explorer.py::render_patient_explorer_tab":
        ["st.fragment"],
    "oncotriage/dashboard/tabs/reproducibility.py::render_reproducibility_tab":
        ["st.fragment"],
    "oncotriage/dashboard/tabs/trial_explorer.py::render_trial_explorer_tab":
        ["st.fragment"],
    "oncotriage/registries/cancer_code_registry.py::CancerCodeRegistry._invert_date":
        ["staticmethod"],
    # _date_sort_key belongs to OncologyLabRegistry, NOT CancerCodeRegistry --
    # two classes in one module, and the qualified name is what distinguishes
    # them. A bare-name inventory could not.
    "oncotriage/registries/cancer_code_registry.py::OncologyLabRegistry._date_sort_key":
        ["staticmethod"],
    # Pass 20c-3d. compute_collection_digest()'s paging closure is the one
    # decorated definition in the three subpackages that pass added, and it is
    # NESTED -- a top-level walk would report it as absent, which is the shape
    # that hid api/server.py's four endpoints from the first version of this
    # scan.
    "oncotriage/fixtures/capture.py::compute_collection_digest._page":
        ["qdrant_retry"],
    "oncotriage/registries/mesh.py::MeSHCancerFilter._stem": ["staticmethod"],
    "oncotriage/retrieval/indexer.py::get_embeddings_batch._call": [
        "retry(reraise=True, stop=stop_after_attempt(5), "
        "wait=wait_exponential(multiplier=1, min=2, max=60), "
        "retry=retry_if_exception_type((RateLimitError, InternalServerError, "
        "APIConnectionError)))"],
}

_found_decorators = {}
for _f in _PKG_FILES:
    _rel = os.path.relpath(_f, _code_dir).replace(os.sep, "/")
    for _qual, _decs in _decorated_definitions(_f).items():
        _found_decorators[f"{_rel}::{_qual}"] = _decs

check("the decorator inventory of the package is exactly what it was after the "
      "conversion passes -- same definitions, same decorators, at every depth",
      _found_decorators, _DECORATOR_INVENTORY)
check("...and it is non-degenerate: the four @st.fragment tabs pass 20c-3c-1 "
      "silently dropped are in it",
      sorted(k for k, v in _found_decorators.items() if v == ["st.fragment"]),
      ["oncotriage/dashboard/tabs/drift.py::render_drift_detection_tab",
       "oncotriage/dashboard/tabs/patient_explorer.py::render_patient_explorer_tab",
       "oncotriage/dashboard/tabs/reproducibility.py::render_reproducibility_tab",
       "oncotriage/dashboard/tabs/trial_explorer.py::render_trial_explorer_tab"])
check("...and it reaches definitions NESTED inside a function, which a "
      "top-level walk cannot see -- the four api/server.py endpoints live "
      "inside create_app() and carry the most decorators in the package",
      sorted(k for k in _found_decorators if "create_app." in k),
      ["oncotriage/api/server.py::create_app.health_check",
       "oncotriage/api/server.py::create_app.match_patient_endpoint",
       "oncotriage/api/server.py::create_app.match_patient_file",
       "oncotriage/api/server.py::create_app.pipeline_info"])

# NEGATIVE CONTROL: strip a decorator from an AST COPY of the real file -- never
# the file itself -- and require the inventory to stop matching. Aimed at
# api/server.py because its decorators are the ones separated from the def by
# blank lines, which is the shape any adjacency-based check would miss.
_DEC_DIR = tempfile.mkdtemp(prefix="oncotriage-decorator-")
try:
    _server_src = open(os.path.join(_PKG_DIR, "api", "server.py"),
                       encoding="utf-8").read()
    _server_tree = ast.parse(_server_src)
    _stripped_any = False
    for _node in ast.walk(_server_tree):
        if isinstance(_node, _DEF_TYPES) and _node.name == "match_patient_endpoint":
            _node.decorator_list = []
            _stripped_any = True
    check("the control found the definition it strips (non-degeneracy)",
          _stripped_any, True)
    _copy = os.path.join(_DEC_DIR, "server_copy.py")
    with open(_copy, "w", encoding="utf-8") as _fh:
        _fh.write(ast.unparse(_server_tree))
    _control = _decorated_definitions(_copy)
    check("the inventory CATCHES a decorator removed from a copy: the "
          "definition disappears from the decorated set",
          "create_app.match_patient_endpoint" in _control, False)
    check("...while the three other endpoints in the same copy keep theirs, so "
          "the control removed one thing rather than breaking the parser",
          sorted(k for k in _control if "create_app." in k),
          ["create_app.health_check", "create_app.match_patient_file",
           "create_app.pipeline_info"])
finally:
    shutil.rmtree(_DEC_DIR, ignore_errors=True)


# ===========================================================================
# 3. THE CLIENT FACTORIES ARE LAZY AND CACHED
# ===========================================================================

print("\n" + "=" * 78)
print("3. get_openai_client / get_qdrant_client build once, on first call")
print("=" * 78)

# Counting fakes replace the two constructors BEFORE anything calls a factory,
# and get_keys is stubbed so no .env is needed. Counts, not just identity: two
# calls returning the same object would ALSO hold for a module-level singleton
# built at import, which is what this pass removed. `built_at_import == 0` is
# the check that separates the two.
_LAZY = r'''
import json
import oncotriage.config as cfg

calls = {"openai": 0, "qdrant": 0}

class FakeOpenAI:
    def __init__(self, *args, **kwargs):
        calls["openai"] += 1
        self.timeout = _FakeTimeout()

class _FakeTimeout:
    connect = 5.0

class FakeQdrant:
    def __init__(self, *args, **kwargs):
        calls["qdrant"] += 1

cfg.OpenAI = FakeOpenAI
cfg.QdrantClient = FakeQdrant
cfg.get_keys = lambda: {"openai": "sk-fake", "qdrant_url": "http://fake",
                        "qdrant_key": "fake"}

built_at_import = dict(calls)

a1 = cfg.get_openai_client()
after_first_openai = calls["openai"]
a2 = cfg.get_openai_client()
after_second_openai = calls["openai"]

q1 = cfg.get_qdrant_client()
after_first_qdrant = calls["qdrant"]
q2 = cfg.get_qdrant_client()
after_second_qdrant = calls["qdrant"]

print(json.dumps({
    "built_at_import": built_at_import,
    "openai": [after_first_openai, after_second_openai, a1 is a2, a1 is not None],
    "qdrant": [after_first_qdrant, after_second_qdrant, q1 is q2, q1 is not None],
}))
'''

_rc, _out, _err = _run(_LAZY, cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
check("the laziness probe ran", _rc, 0)
if _rc != 0:
    fail("client factory laziness",
         f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")
else:
    _payload = _last_json(_out) or {}
    check("no client was constructed at import time",
          _payload.get("built_at_import"), {"openai": 0, "qdrant": 0})
    # [after first call, after second call, same object, not None]
    # The OpenAI count is 2 after the first call, not 1: get_openai_client()
    # resolves its structured timeout first, and that builds one throwaway
    # client to read the SDK's default connect phase. The second call must not
    # move it -- both the client and the timeout are cached.
    check("get_openai_client: 2 constructions on first call (client + the "
          "throwaway the timeout reads), 2 after the second, same object, non-None",
          _payload.get("openai"), [2, 2, True, True])
    check("get_qdrant_client: 1 construction on first call, 1 after the "
          "second, same object, non-None",
          _payload.get("qdrant"), [1, 1, True, True])


# ===========================================================================
# 4. get_age_reference_date RESOLVES BY IMPORT AND STILL REFUSES TO GUESS
# ===========================================================================

print("\n" + "=" * 78)
print("4. get_age_reference_date reads config, and raises rather than today()")
print("=" * 78)

# STRUCTURAL first: the function must not read globals(), which is how it used
# to resolve the constant when every project file shared one exec namespace.
_utils_tree = ast.parse(open(_UTILS_PY, encoding="utf-8").read())
_fn = next((n for n in ast.walk(_utils_tree)
            if isinstance(n, ast.FunctionDef) and n.name == "get_age_reference_date"), None)
if _fn is None:
    fail("get_age_reference_date is defined in oncotriage/utils.py",
         "no FunctionDef of that name; the rest of section 4 tests nothing")
else:
    _calls = [n.func.id for n in ast.walk(_fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    _attrs = [n.attr for n in ast.walk(_fn) if isinstance(n, ast.Attribute)]
    check("get_age_reference_date does not call globals()", "globals" in _calls, False)
    check("...and it does not call today() or now()",
          ("today" in _attrs) or ("now" in _attrs), False)
    # NON-DEGENERATE: the two checks above would pass on an empty function.
    check("...and it does resolve the constant through the config module",
          any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id == "getattr"
              and any(isinstance(a, ast.Name) and a.id == "config" for a in n.args)
              for n in ast.walk(_fn)),
          True)

# BEHAVIOURAL, against a COPY of the package. CLAUDE.md prefers a copy over an
# in-place edit, and nothing here touches the shipped files at all.
_COPY_ROOT = tempfile.mkdtemp(prefix="oncotriage_snapshot_")
shutil.copytree(_PKG_DIR, os.path.join(_COPY_ROOT, "oncotriage"))
_COPY_CONFIG = os.path.join(_COPY_ROOT, "oncotriage", "config.py")

_GOOD_LINE = 'DATA_SNAPSHOT_DATE = "2026-08-03"'
_PARTIAL_LINE = 'DATA_SNAPSHOT_DATE = "2026-08"'

_copy_src = open(_COPY_CONFIG, encoding="utf-8").read()
check("the copied config carries the snapshot-date assignment to rewrite",
      _GOOD_LINE in _copy_src, True)

_PROBE = r'''
import json
from datetime import date
from oncotriage.utils import get_age_reference_date
try:
    value = get_age_reference_date()
    print(json.dumps({"raised": None, "value": value.isoformat(),
                      "is_today": value == date.today()}))
except ValueError as exc:
    print(json.dumps({"raised": "ValueError", "message": str(exc)}))
'''

if _GOOD_LINE in _copy_src:
    # -- broken: a partial date must raise
    open(_COPY_CONFIG, "w", encoding="utf-8").write(
        _copy_src.replace(_GOOD_LINE, _PARTIAL_LINE, 1))
    _rc, _out, _err = _run(_PROBE, cwd=_ELSEWHERE, extra_path=_COPY_ROOT)
    _payload = _last_json(_out) or {}
    check("a partial DATA_SNAPSHOT_DATE raises ValueError",
          _payload.get("raised"), "ValueError")
    check("...and the message names the constant, so the fix is findable",
          "DATA_SNAPSHOT_DATE" in (_payload.get("message") or ""), True)
    check("...and it did NOT quietly return a date",
          "value" in _payload, False)

    # -- restored: the real date comes back
    open(_COPY_CONFIG, "w", encoding="utf-8").write(_copy_src)
    check("the copy is restored byte-for-byte",
          open(_COPY_CONFIG, encoding="utf-8").read() == _copy_src, True)
    _rc, _out, _err = _run(_PROBE, cwd=_ELSEWHERE, extra_path=_COPY_ROOT)
    _payload = _last_json(_out) or {}
    check("with the constant restored, the reference date is 2026-08-03",
          _payload.get("value"), date(2026, 8, 3).isoformat())
    # NON-DEGENERATE: 2026-08-03 must not be today, or "never today()" would be
    # satisfied by coincidence. This check goes red on 2026-08-03 itself, which
    # is the correct behaviour -- on that day the test cannot tell the two apart
    # and should not claim to.
    check("...and that is not simply today's date",
          _payload.get("is_today"), False)

shutil.rmtree(_COPY_ROOT, ignore_errors=True)


# ===========================================================================
# 5. NO NAME FILES 01 / 02 / 03 DEFINED WAS DROPPED
# ===========================================================================

print("\n" + "=" * 78)
print("5. every pre-20c name is still bound by its shim")
print("=" * 78)

for _filename, _expected in _PRE_20C_NAMES.items():
    check(f"the recorded name list for {_filename[:2]} is the size it was "
          f"extracted at", len(_expected), _PRE_20C_COUNTS[_filename])
    _bound = _bound_names(os.path.join(_code_dir, _filename))
    _missing = sorted(set(_expected) - _bound)
    check(f"{_filename[:2]}: all {len(_expected)} pre-20c names still bound", _missing, [])

# The AST says the shim binds the name; it does not say the package actually
# exposes it. An import of a name the package lost fails at run time, so every
# `from oncotriage.X import ...` in the three shims is resolved for real.
# WHY THIS DOES NOT USE hasattr FOR EVERY NAME (corrected in pass 20c-2c).
#
# oncotriage.paths grew a PEP 562 __getattr__ in pass 2b, and a path name routed
# through it RESOLVES when it is read. On a healthy tree hasattr returns True.
# On a checkout without the sibling directories the resolver raises
# RuntimeError, and Python does not convert that to AttributeError -- so
# hasattr PROPAGATES it and this probe would abort with a traceback instead of
# reporting which names are missing. It would fail on exactly the machine this
# whole pass exists to make the package work on.
#
# The question being asked is "does the package expose this name", not "can this
# name be resolved right now", and PATH_NAMES answers the first without
# attempting the second. Membership is checked FIRST so a path name never
# reaches hasattr; every other module is unaffected and still uses hasattr,
# which is the right test for a function or a constant.
_IMPORT_PROBE = r'''
import importlib, json, sys
missing = []
checked_via_path_names = []
for module_name, names in json.loads(sys.argv[1] if len(sys.argv) > 1 else "{}").items():
    module = importlib.import_module(module_name)
    lazy = set(getattr(module, "PATH_NAMES", ()))
    for name in names:
        if name in lazy:
            checked_via_path_names.append(module_name + "." + name)
            continue
        if not hasattr(module, name):
            missing.append(module_name + "." + name)
print(json.dumps({"missing": missing,
                  "checked_via_path_names": sorted(checked_via_path_names)}))
'''

_wanted = {}
for _filename in _PRE_20C_NAMES:
    _tree = ast.parse(open(os.path.join(_code_dir, _filename), encoding="utf-8").read())
    for _node in ast.walk(_tree):
        if isinstance(_node, ast.ImportFrom) and (_node.module or "").startswith("oncotriage"):
            _wanted.setdefault(_node.module, []).extend(a.name for a in _node.names)

check("the shims import at least 80 names from the package (a probe over an "
      "empty set would prove nothing)",
      sum(len(v) for v in _wanted.values()) >= 80, True)

_rc, _out, _err = _run(
    _IMPORT_PROBE.replace("sys.argv[1] if len(sys.argv) > 1 else \"{}\"",
                          repr(json.dumps(_wanted))),
    cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
check("the package-surface probe ran", _rc, 0)
if _rc == 0:
    _payload = _last_json(_out) or {}
    check("every name the shims import actually exists on its package module",
          _payload.get("missing"), [])
    # NON-DEGENERATE. "missing == []" is also what a probe that checked nothing
    # returns. The lazy-path branch must have been taken for the sixteen names
    # File 01 imports out of oncotriage.paths -- if PATH_NAMES ever stopped
    # being exported, every one of them would silently fall through to hasattr
    # and this file would be back to aborting on a broken tree.
    _via_paths = [n for n in (_payload.get("checked_via_path_names") or [])
                  if n.startswith("oncotriage.paths.")]
    check("...and the sixteen lazy path names were checked by membership, not "
          "by hasattr, so a broken tree cannot abort this probe",
          len(_via_paths), 16)
else:
    fail("package surface probe",
         f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")


# --- Files 08, 09 and 10: exec the shim, compare the whole namespace --------
# A stronger check than the ast one above, and the right one for these three:
# their surface includes a name that is assigned and then deleted, so only
# executing the file can say what it really binds. The shim is exec'd into a
# bare namespace in a SUBPROCESS -- File 09's shim imports the MeSH filter,
# which resolves data_MeSH_path, and none of that should land in this process.
#
# Nothing is pre-seeded. That is deliberate: before pass 2a these files needed
# SYSTEM_KEY_ABSENT, data_MeSH_path and a dozen typing names out of the shared
# exec namespace, and after it they must need nothing at all, because a shim is
# import statements. If any of them still reached for a free name, the exec
# would raise NameError and this check would go red.

# FILE 13 NEEDS A BASE CHAIN SUBTRACTED, and the other five do not.
#
# Files 07, 08, 09, 10 and 14's shims are import statements: exec'd into a bare
# namespace they bind their own names and nothing else. File 13's shim keeps its
# BOOTSTRAP -- it exec's 01 and 02 and then chains 03, 08, 09 and 10, exactly as
# File 13 always did, because twelve callers rely on chaining 13 to get all of
# them. So exec'ing it bare yields 402 names, 315 of which belong to those
# files. The probe therefore runs the same base chain into the same namespace
# FIRST, records what is there, and reports only what the shim itself added.
#
# The base list is passed per file, so nothing about this is special-cased in
# the loop below: it is empty for the five import shims and is the real chain
# for File 13.
_SHIM_PROBE = r"""
import json, os, sys
path = sys.argv[1]
base_files = json.loads(sys.argv[2]) if len(sys.argv) > 2 else []
chain_list = json.loads(sys.argv[3]) if len(sys.argv) > 3 else []
code_dir = os.path.dirname(path) + os.sep

ns = {"__name__": "_exec_chain_", "__file__": path}
for name in base_files:
    with open(code_dir + name, encoding="utf-8") as fh:
        exec(fh.read(), ns)
if chain_list:
    ns["exec_chain"](chain_list, caller_file=path, caller_globals=ns,
                     chain_label="base")
before = set(ns)

with open(path, encoding="utf-8") as fh:
    exec(fh.read(), ns)
print(json.dumps(sorted(k for k in ns
                        if k not in before and not k.startswith("__"))))
"""

_SHIM_BASE_CHAIN = {
    "13- LangGraph Agent.py": ["01- Imports.py", "02- Utility Functions.py"],
    # File 05's shim exec's 01, 02 AND 03 raw, then chains 07 and 08 for File 34.
    # Same treatment as File 13: run the base first, record what is there, and
    # report only what the shim itself added.
    "05- FHIR Clean Data.py": ["01- Imports.py", "02- Utility Functions.py",
                               "03- Config.py"],
}

# WHICH CHAIN THE PROBE RUNS AFTER THE BASE FILES. File 13 chains 03, 08, 09 and
# 10; File 05 chains only 07 and 08. Keyed by file so nothing in the loop below
# is special-cased.
_SHIM_CHAIN_LIST = {
    "13- LangGraph Agent.py": ["03- Config.py", "08- Cancer Code Registry.py",
                               "09- MeSH Cancer Site Relevance Filter.py",
                               "10- Structured Eligibility Extractor.py"],
    "05- FHIR Clean Data.py": ["07- FHIR Parser.py", "08- Cancer Code Registry.py"],
}

# Files 08, 09 and 10 (pass 2a) and Files 07 and 14 (pass 2b) are checked by the
# same loop against the same rules. The two inventories stay in separate dicts
# above because each was extracted at a different commit and each is pinned to
# what its file bound at that commit; merging them here is just iteration.
_RUNTIME_INVENTORY = dict(_PRE_2A_RUNTIME_NAMES)
_RUNTIME_INVENTORY.update(_PRE_2B_RUNTIME_NAMES)
_RUNTIME_INVENTORY.update(_PRE_2C_RUNTIME_NAMES)
_RUNTIME_INVENTORY.update(_PRE_3A_RUNTIME_NAMES)

_RUNTIME_COUNTS = dict(_PRE_2A_COUNTS)
_RUNTIME_COUNTS.update(_PRE_2B_COUNTS)
_RUNTIME_COUNTS.update(_PRE_2C_COUNTS)
_RUNTIME_COUNTS.update(_PRE_3A_COUNTS)

_RUNTIME_ADDED = {name: set() for name in _PRE_2A_RUNTIME_NAMES}
_RUNTIME_ADDED.update(_PASS_2B_ADDED)
_RUNTIME_ADDED.update(_PASS_2C_ADDED)
_RUNTIME_ADDED.update(_PASS_3A_ADDED)

check("the inventory covers all seven converted files",
      sorted(_RUNTIME_INVENTORY), sorted(_PASS_2B_DROPPED))

for _filename, _expected in _RUNTIME_INVENTORY.items():
    check(f"the recorded runtime name list for {_filename[:2]} is the size it "
          f"was extracted at", len(_expected), _RUNTIME_COUNTS[_filename])

    _proc = subprocess.run(
        [sys.executable, "-c", _SHIM_PROBE, os.path.join(_code_dir, _filename),
         json.dumps(_SHIM_BASE_CHAIN.get(_filename, [])),
         json.dumps(_SHIM_CHAIN_LIST.get(_filename, []))],
        cwd=_ELSEWHERE, capture_output=True, text=True,
        env={**{k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
             **({"PYTHONPATH": _FALLBACK_PATH} if _FALLBACK_PATH else {}),
             # File 13's shim loads no model, but the base chain it runs would
             # have. Set so this probe cannot be the thing that pulls MedCPT in.
             "ONCOTRIAGE_DEFER_LOCAL_MODELS": "1"},
    )
    if _proc.returncode != 0:
        fail(f"{_filename[:2]}: the shim exec'd into a bare namespace",
             f"exit {_proc.returncode}; stderr tail: "
             f"{_proc.stderr.strip().splitlines()[-3:]}")
        continue

    _lines = [ln for ln in _proc.stdout.strip().splitlines() if ln.startswith("[")]
    _bound = json.loads(_lines[-1]) if _lines else None
    if _bound is None:
        fail(f"{_filename[:2]}: the shim probe returned a name list",
             f"stdout tail: {_proc.stdout.strip().splitlines()[-3:]}")
        continue

    _dropped = _PASS_2B_DROPPED[_filename]
    _added = _RUNTIME_ADDED[_filename]

    # NON-DEGENERATE FIRST. An exception list that names something the inventory
    # never held would silently excuse nothing, and an exception list that grew
    # to cover the whole inventory would excuse everything.
    check(f"{_filename[:2]}: every deliberately-dropped name was in the recorded "
          f"inventory to begin with", sorted(_dropped - set(_expected)), [])
    check(f"{_filename[:2]}: the exception list is a small minority of the "
          f"inventory", len(_dropped) < len(_expected) // 4, True)

    check(f"{_filename[:2]}: all {len(_expected) - len(_dropped)} recorded runtime "
          f"names still bound (minus {len(_dropped)} dropped by pass 2b)",
          sorted(set(_expected) - _dropped - set(_bound)), [])
    # The deletions are ASSERTED, not merely tolerated: a shim that quietly kept
    # re-exporting a permanently-None registry snapshot would pass the check
    # above and fail this one.
    check(f"{_filename[:2]}: and every dropped name really is gone",
          sorted(_dropped & set(_bound)), [])
    # Both directions. A shim that re-exported something the file never defined
    # would put a name into the shared exec namespace that no caller expects,
    # and the next file to use that name would silently pick this one up. The
    # only permitted additions are the ones declared in _PASS_2B_ADDED, and each
    # of those must actually BE there -- a declaration for a name that is not
    # bound would quietly widen the allowance for nothing.
    check(f"{_filename[:2]}: every declared addition is genuinely NEW, i.e. absent "
          f"from the recorded inventory (non-degeneracy)",
          sorted(_added & set(_expected)), [])
    check(f"{_filename[:2]}: and every declared addition really is bound",
          sorted(_added - set(_bound)), [])
    check(f"{_filename[:2]}: and nothing UNDECLARED was added to the shared "
          f"namespace", sorted(set(_bound) - set(_expected) - _added), [])


# ===========================================================================
# 5b. THE FILE 10 SPLIT HAS EXACTLY ONE SHARED NAME
# ===========================================================================

print("\n" + "=" * 78)
print("5b. stage and histology share exactly one name, and it lives in negation")
print("=" * 78)

# THE EVIDENCE FOR THE SPLIT, re-derived here rather than asserted in a comment.
#
# "10- Structured Eligibility Extractor.py" was two extractors in one file, and
# the claim that it splits cleanly rests on exactly one measurement: how many
# top-level names in one half are referenced by the other. The answer was 1 --
# _is_histology_negated() calls _is_negated() -- which is why negation.py
# exists and why the split is a fact rather than a preference.
#
# The measurement is repeated against the SHIPPED modules, so the claim decays
# into a failure if someone later adds a second edge instead of moving the
# shared name into negation.py where it belongs.
#
# A grep could not have settled this. It cannot tell a call from a mention in
# a docstring, and File 10's docstrings mention _is_negated by name.

_STAGE_PY = os.path.join(_PKG_DIR, "extraction", "stage.py")
_HIST_PY = os.path.join(_PKG_DIR, "extraction", "histology.py")
_NEG_PY = os.path.join(_PKG_DIR, "extraction", "negation.py")


def _top_level_names(path: str) -> set:
    """Names a module binds at top level, imports EXCLUDED.

    Imports are excluded on purpose: stage.py imports _is_negated, and counting
    that as a definition would make the two halves look like they define the
    same name rather than share one.
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _loaded_names(path: str) -> set:
    """Every Name read anywhere in the module."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    return {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


_STAGE_DEFS, _HIST_DEFS, _NEG_DEFS = (_top_level_names(f) for f in
                                      (_STAGE_PY, _HIST_PY, _NEG_PY))

# NON-DEGENERATE FIRST. Every one of the three checks below is an intersection,
# and an intersection with an empty set is empty. If any of these modules
# stopped defining anything -- a bad slice, a truncated file -- the edge counts
# would all read zero and this section would certify a split that no longer
# exists.
check("stage.py, histology.py and negation.py all define a plausible number "
      "of top-level names (non-degeneracy)",
      len(_STAGE_DEFS) >= 20 and len(_HIST_DEFS) >= 20 and len(_NEG_DEFS) == 4,
      True)

check("histology.py references nothing that stage.py defines",
      sorted(_loaded_names(_HIST_PY) & _STAGE_DEFS), [])
check("stage.py references nothing that histology.py defines",
      sorted(_loaded_names(_STAGE_PY) & _HIST_DEFS), [])
check("the one name they DO share is _is_negated, and it lives in negation.py",
      sorted(_NEG_DEFS & (_loaded_names(_STAGE_PY) | _loaded_names(_HIST_PY))),
      ["_is_negated"])
check("...and both halves import it rather than redefining it",
      "_is_negated" in _STAGE_DEFS or "_is_negated" in _HIST_DEFS, False)


# ===========================================================================
# 5c. THE LAZY DEPENDENCY PROXY ANSWERS FOR WHAT IT WRAPS, NOT FOR ITSELF
# ===========================================================================

print("\n" + "=" * 78)
print("5c. _LazyAgentDependency delegates bool / len / iter / in / == / repr")
print("=" * 78)

# THE DEFECT, and it is a set of WRONG ANSWERS rather than a missing feature.
#
# Pass 2c bound medcpt_tokenizer, medcpt_model and _bm25_query_model in File 13's
# shim to a proxy that forwarded __getattr__ and __call__ and nothing else, on
# the stated grounds that "call it or read an attribute off it" is the whole
# surface any caller uses. That was true of the three callers that existed then;
# it was not true of Python. CPython looks an IMPLICIT special method up on the
# TYPE, never through __getattr__, so:
#
#   bool(proxy)    -> True, always, whatever the wrapped object says
#   proxy == other -> False, always, by identity -- even when the wrapped object
#                     IS `other`. The whole point of a proxy over this seam is
#                     that a harness can ask "is the thing the agent reaches
#                     mine?", and == answered no while the answer was yes.
#   len / iter / in -> TypeError naming _LazyAgentDependency, which sends the
#                     reader to the wrapper instead of to the model.
#   repr(proxy)    -> "<lazy ...>" even when an override is installed, i.e. a
#                     description of the wrapper at the one moment a person is
#                     looking to find out what they actually got.
#
# EAGER BINDING IS NOT THE FIX and was rejected on evidence:
# ONCOTRIAGE_DEFER_LOCAL_MODELS appears in exactly two files in this repository,
# File 13 and File 46, so Files 31, 32, 35, 36, 37, 39 and 40 all chain File 13
# with the switch unset and none of them scores a pair. Binding eagerly would
# load MedCPT (~110 MB) and FastEmbed for all seven.
#
# THE DEMONSTRATION RUNS BOTH CLASSES. The current class is extracted from File
# 13 by ast and exec'd into a throwaway namespace; a copy of it with the six
# delegating methods REMOVED stands in for the pass-2c version. Both wrap the
# same sentinel, whose every protocol answer is unambiguous, and the old one must
# get them wrong. Nothing is edited in place.

_PROXY_DEMO = r'''
import ast, json, sys

from oncotriage.agent import deps

STRIPPED = {"__bool__", "__len__", "__iter__", "__contains__", "__eq__",
            "__hash__"}

source = open(sys.argv[1], encoding="utf-8").read()
node = next(n for n in ast.parse(source).body
            if isinstance(n, ast.ClassDef) and n.name == "_LazyAgentDependency")

# The class body references `deps` by name, so both copies are exec'd into a
# namespace that HAS it -- the shipped __repr__ asks deps two questions.
new_ns, old_ns = {"deps": deps}, {"deps": deps}
exec(ast.unparse(node), new_ns)

# The pass-2c/3a shape: the same class with the six delegating methods removed
# and __repr__ put back to the DELEGATING form pass 3a shipped -- the one that
# resolves. That is the defect pass 3b removes, so it is what the control must
# reproduce.
old_node = ast.parse(ast.unparse(node)).body[0]
old_node.body = [b for b in old_node.body
                 if not (isinstance(b, ast.FunctionDef) and b.name in STRIPPED)]
for b in old_node.body:
    if isinstance(b, ast.FunctionDef) and b.name == "__repr__":
        b.body = ast.parse("return repr(self._resolve())").body
exec(ast.unparse(old_node), old_ns)


class Sentinel:
    marker = "forwarded by __getattr__"

    def __call__(self, *args):
        return ["called", list(args)]

    def __bool__(self):
        return False                      # falsy: bool(proxy) must be False

    def __len__(self):
        return 3

    def __iter__(self):
        return iter(("a", "b", "c"))

    def __contains__(self, item):
        return item == "a"

    def __eq__(self, other):
        return other is self or other == "i am the sentinel"

    def __hash__(self):
        return 4242

    def __repr__(self):
        return "<Sentinel: the object the agent actually reaches>"


SENTINEL = Sentinel()

# `repr` LEFT THIS DICT IN PASS 20c-3b. It used to expect the SENTINEL's own
# repr, i.e. delegation. Delegation is the defect now: it makes repr() resolve,
# which downloads and loads MedCPT from a debugger, a log line, or a bare name
# typed at a prompt -- on the diagnostic path, where the tool used to inspect
# the state must not be the thing that changes it. repr is measured separately
# below, by whether the ACCESSOR WAS CALLED.
TRUTH = {
    "bool": False,
    "len": 3,
    "iter": ["a", "b", "c"],
    "contains": True,
    "eq_identity": True,
    "eq_value": True,
    "hash": True,
    "getattr": "forwarded by __getattr__",
    "call": ["called", [1, 2]],
}

# A real deps key, because the shipped __repr__ asks deps about it. MEDCPT_MODEL
# is one of the three this proxy is actually used for.
KEY = deps.MEDCPT_MODEL


def observe(cls):
    proxy = cls(lambda: SENTINEL, "sentinel dependency", KEY)
    out = {}
    for key, thunk in (
        ("bool", lambda: bool(proxy)),
        ("len", lambda: len(proxy)),
        ("iter", lambda: list(proxy)),
        ("contains", lambda: "a" in proxy),
        ("eq_identity", lambda: proxy == SENTINEL),
        ("eq_value", lambda: proxy == "i am the sentinel"),
        ("hash", lambda: hash(proxy) == 4242),
        ("getattr", lambda: proxy.marker),
        ("call", lambda: proxy(1, 2)),
    ):
        try:
            out[key] = thunk()
        except Exception as exc:                                # noqa: BLE001
            out[key] = "!!" + type(exc).__name__
    return out


old, new = observe(old_ns["_LazyAgentDependency"]), observe(new_ns["_LazyAgentDependency"])


# ---------------------------------------------------------------------------
# __repr__ MUST NOT RESOLVE (pass 20c-3b)
# ---------------------------------------------------------------------------
# Measured by COUNTING ACCESSOR CALLS, which is the only thing that can tell the
# two shapes apart: both return a string, and both strings look reasonable.
def counting_accessor():
    calls = []

    def accessor():
        calls.append(1)
        return SENTINEL

    return accessor, calls


new_acc, new_calls = counting_accessor()
old_acc, old_calls = counting_accessor()

new_proxy = new_ns["_LazyAgentDependency"](new_acc, "sentinel dependency", KEY)
old_proxy = old_ns["_LazyAgentDependency"](old_acc, "sentinel dependency", KEY)

new_repr_unresolved = repr(new_proxy)
old_repr = repr(old_proxy)

# With an override INSTALLED, the shipped repr must say so and must name the
# object's TYPE -- the discriminating fact, without transformers' module tree.
with deps.override(KEY, SENTINEL):
    new_repr_override = repr(new_proxy)
    state_with_override = deps.resolution_state(KEY)
    peeked_is_sentinel = deps.peek(KEY) is SENTINEL

state_after = deps.resolution_state(KEY)

# A __repr__ that RAISES would break every traceback, debugger and log line that
# formats one of these three names, so a failure is caught, RECORDED and
# described. The way to make it fail without resolving anything is a proxy built
# with a key deps does not know -- a defect in the shim rather than in a model.
Cls = new_ns["_LazyAgentDependency"]
before = len(Cls.repr_failures)
bad = Cls(lambda: SENTINEL, "bogus dependency", "not-a-real-deps-key")
bad_text = repr(bad)

print(json.dumps({
    "old_wrong": sorted(k for k, v in old.items() if v != TRUTH[k]),
    "new_wrong": sorted(k for k, v in new.items() if v != TRUTH[k]),
    # THE CONTROL: the pass-3a delegating repr calls the accessor. The shipped
    # one does not.
    "old_repr_resolutions": len(old_calls),
    "new_repr_resolutions": len(new_calls),
    "old_repr_is_the_wrapped_object": old_repr == repr(SENTINEL),
    "new_repr_names_the_key": KEY in new_repr_unresolved,
    "new_repr_says_unresolved": "unresolved" in new_repr_unresolved,
    "new_repr_says_override": "override" in new_repr_override,
    "new_repr_names_the_type": "Sentinel" in new_repr_override,
    "new_repr_avoids_the_object_repr":
        repr(SENTINEL) not in new_repr_override,
    "state_with_override": state_with_override,
    "peeked_is_sentinel": peeked_is_sentinel,
    "state_after_override_removed": state_after,
    # RESOLUTION_STATES IS A CLOSED SET AND THIS IS WHAT CLOSES IT (20c-3i).
    # Its docstring says "every value resolution_state() can return ... so a
    # caller can branch on it exhaustively", and until this pass no caller
    # anywhere in the repository read it -- it was a declaration with nothing
    # holding it to the code it described, the same shape as
    # PASSWORD_SOURCE_ARGUMENT in orchestration/airflow_manager.py. Both
    # observed states are checked for membership, so a fourth state added to
    # resolution_state() without being added to the tuple fails here.
    "states_are_in_the_closed_set":
        (state_with_override in deps.RESOLUTION_STATES
         and state_after in deps.RESOLUTION_STATES),
    "closed_set_is_non_degenerate": len(deps.RESOLUTION_STATES) == 3,
    "repr_did_not_raise": True,
    "repr_names_the_error": "KeyError" in bad_text,
    "repr_failure_recorded": len(Cls.repr_failures) == before + 1,
}))
'''

_rc, _out, _err = _run(_PROXY_DEMO.replace("sys.argv[1]",
                                           repr(os.path.join(_code_dir,
                                                             "13- LangGraph Agent.py"))),
                       cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
check("the lazy-proxy demonstration ran", _rc, 0)
if _rc != 0:
    fail("lazy-proxy demonstration",
         f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")
else:
    _payload = _last_json(_out) or {}
    # NON-DEGENERATE FIRST, and this is the half that makes the rest evidence
    # rather than assertion: the OLD shape must be shown to get these wrong. A
    # demonstration where both classes pass proves only that the sentinel was
    # too weak to tell them apart.
    check("the pass-2c proxy shape answers bool, len, iter, in, ==, and hash "
          "WRONGLY about the object it wraps",
          _payload.get("old_wrong"),
          ["bool", "contains", "eq_identity", "eq_value", "hash", "iter", "len"])
    check("...while __getattr__ and __call__ were right even then, so the "
          "difference is the six protocols and nothing else",
          sorted(set(_payload.get("old_wrong") or []) & {"getattr", "call"}), [])
    check("the shipped proxy gets every one of the nine right",
          _payload.get("new_wrong"), [])

    # --- __repr__ MUST NOT RESOLVE (pass 20c-3b) ---------------------------
    #
    # Pass 3a made __repr__ delegate, on the reasonable-sounding grounds that a
    # proxy printing "<lazy MedCPT cross-encoder>" while handing the agent a
    # fixture stub is lying. The goal was right and the mechanism was wrong:
    # delegation means REPR TRIGGERS A BUILD. A debugger rendering locals, a
    # logging call formatting the object, or a bare `medcpt_model` typed at a
    # prompt now downloads and loads ~110 MB and then prints transformers'
    # multi-thousand-line module tree -- on the DIAGNOSTIC path, so the tool
    # used to inspect the state is what changes it.
    #
    # MEASURED BY COUNTING ACCESSOR CALLS, because that is the only thing that
    # separates the two shapes: both return a plausible-looking string.
    check("the pass-3a delegating __repr__ RESOLVES (the defect, demonstrated)",
          _payload.get("old_repr_resolutions"), 1)
    check("...and it did so by producing the wrapped object's own repr, which "
          "is what made the cost invisible",
          _payload.get("old_repr_is_the_wrapped_object"), True)
    check("the shipped __repr__ resolves NOTHING",
          _payload.get("new_repr_resolutions"), 0)
    check("...and still names the deps key it stands for",
          _payload.get("new_repr_names_the_key"), True)
    check("...and says so when nothing is built yet",
          _payload.get("new_repr_says_unresolved"), True)

    # The honesty pass 3a was after, kept without the cost: with an override
    # installed the repr must say "override" and name the STUB's type, so a
    # person looking at it can tell what the agent is actually reaching.
    check("with an override installed the repr says 'override'",
          _payload.get("new_repr_says_override"), True)
    check("...and names the type of the object that is installed",
          _payload.get("new_repr_names_the_type"), True)
    check("...without printing the object's own repr, which is the module tree "
          "nobody asked for",
          _payload.get("new_repr_avoids_the_object_repr"), True)

    # The two non-building deps queries the repr is built on.
    check("deps.resolution_state reports an installed override",
          _payload.get("state_with_override"), "override")
    check("deps.peek returns the installed object without building",
          _payload.get("peeked_is_sentinel"), True)
    check("...and the key goes back to unresolved when the override is removed, "
          "so the state query is reading live state rather than a constant",
          _payload.get("state_after_override_removed"), "unresolved")
    # Pass 20c-3i. deps.RESOLUTION_STATES documents itself as the CLOSED set of
    # values resolution_state() can return, and nothing in the repository read
    # it -- a declaration with nothing holding it to the code. Both observed
    # states are now checked against it, so a fourth state added to the function
    # and not to the tuple fails here rather than silently widening a set
    # callers were told they could branch on exhaustively.
    check("both observed states are members of deps.RESOLUTION_STATES, which "
          "is what makes that tuple a closed set rather than a comment",
          _payload.get("states_are_in_the_closed_set"), True)
    check("...and the set is the size it claims (non-degeneracy: membership in "
          "an empty or one-element tuple would prove nothing)",
          _payload.get("closed_set_is_non_degenerate"), True)

    check("__repr__ over a proxy deps cannot answer for does NOT raise",
          _payload.get("repr_did_not_raise"), True)
    check("...and names the exception instead of hiding it",
          _payload.get("repr_names_the_error"), True)
    check("...and RECORDS the failure rather than swallowing it",
          _payload.get("repr_failure_recorded"), True)

# The forwarded set is CLOSED and documented. Asserted from the class body so
# that adding a delegation without saying so in the docstring fails here.
_AGENT_SHIM = os.path.join(_code_dir, "13- LangGraph Agent.py")
_PROXY_NODE = next(n for n in ast.parse(open(_AGENT_SHIM, encoding="utf-8").read()).body
                   if isinstance(n, ast.ClassDef) and n.name == "_LazyAgentDependency")
_PROXY_METHODS = sorted(b.name for b in _PROXY_NODE.body
                        if isinstance(b, ast.FunctionDef))
check("the proxy forwards exactly the documented set and nothing more",
      _PROXY_METHODS,
      ["__bool__", "__call__", "__contains__", "__eq__", "__getattr__",
       "__hash__", "__init__", "__iter__", "__len__", "__repr__", "_resolve"])


# ===========================================================================
# 5d. THE DEPS SEAM UNDER MAX_WORKERS THREADS
# ===========================================================================

print("\n" + "=" * 78)
print("5d. MAX_WORKERS threads through every accessor: one object, one build")
print("=" * 78)

# WHY THIS EXISTS NOW AND NOT EARLIER.
#
# oncotriage/agent/deps.py has only ever RUN single-threaded. Every harness that
# exercised it -- Files 35, 36, 37, 45, 46 -- drives one patient at a time on
# one thread. Pass 20c-3a moved the whole override-then-cache sequence inside
# the lock on the argument that "25- Batch Runner.py" drives MAX_WORKERS = 12
# threads through it, and that argument was correct and UNTESTED: the batch
# runner needs a Qdrant index, an OpenAI key and 22,000 bundles, so nothing in
# the test suite had ever put two threads through an accessor at once.
#
# Pass 20c-3b makes the batch runner a package module, so the claim is now
# checkable without any of that. This drives MAX_WORKERS threads at every
# accessor simultaneously, with COUNTING FACTORIES installed in place of the
# real ones, and asserts the two properties the seam exists to provide:
#
#   1. every thread gets THE SAME object for a key. Two Qdrant clients is two
#      connection pools, and the per-patient latency figures in inferences.db
#      would then describe two different transports with nothing in the row
#      saying so.
#   2. the factory ran EXACTLY ONCE per key. "same object" alone would also hold
#      if the factory ran twelve times and eleven results were discarded -- for
#      a client that opens a pool, or a model that loads 110 MB, that is a real
#      cost that identity cannot see.
#
# The counting factories are installed by monkeypatching deps' own private
# builders in the subprocess, which is legitimate here in a way it is not in
# production code: the object under test IS the caching machinery, so the thing
# that must be replaced is what it caches.
#
# A BARRIER, not just a thread pool. Threads that start staggered would let the
# first one finish building before the second even asks, and the race under test
# would never occur. threading.Barrier makes all twelve arrive at the accessor
# together.

_DEPS_CONCURRENCY = r'''
import json, threading
from concurrent.futures import ThreadPoolExecutor

from oncotriage.agent import deps
from oncotriage.config import MAX_WORKERS

# Counting stand-ins for every real factory. Each records that it ran and
# returns a distinct object, so "the same object" is a real claim rather than a
# consequence of everything being None.
BUILDS = {}
LOCK = threading.Lock()


class Built:
    def __init__(self, key):
        self.key = key

    def __repr__(self):
        return f"<Built {self.key}>"


def make_factory(key):
    def factory():
        with LOCK:
            BUILDS[key] = BUILDS.get(key, 0) + 1
        # A build that is instantaneous cannot lose a race. Yielding the GIL
        # here is what gives a second thread the chance to enter and get it
        # wrong, which is the whole point of the exercise.
        for _ in range(200):
            pass
        return Built(key)
    return factory


ACCESSORS = {
    deps.OPENAI_CLIENT:    ("get_openai_client", "_OPENAI"),
    deps.QDRANT_CLIENT:    ("get_qdrant_client", "_QDRANT"),
    deps.BM25_QUERY_MODEL: ("get_bm25_query_model", "_BM25"),
    deps.MEDCPT_TOKENIZER: ("get_medcpt_tokenizer", "_TOK"),
    deps.MEDCPT_MODEL:     ("get_medcpt_model", "_MODEL"),
    deps.CANCER_REGISTRY:  ("get_cancer_registry", "_CANCER"),
    deps.LAB_REGISTRY:     ("get_lab_registry", "_LAB"),
    deps.MESH_FILTER:      ("get_mesh_filter", "_MESH"),
}

# Rebuild each accessor over a counting factory, keeping deps' own _resolve --
# the machinery under test -- untouched.
PATCHED = {}
for key, (accessor_name, _label) in ACCESSORS.items():
    PATCHED[key] = (lambda k=key, f=make_factory(key): deps._resolve(k, f))

N = MAX_WORKERS
barrier = threading.Barrier(N)
results = {key: [] for key in ACCESSORS}
results_lock = threading.Lock()


def worker(_i):
    barrier.wait()                      # all N arrive together
    got = {}
    for key, call in PATCHED.items():
        got[key] = call()
    with results_lock:
        for key, value in got.items():
            results[key].append(value)


with ThreadPoolExecutor(max_workers=N) as pool:
    list(pool.map(worker, range(N)))

same_object = {key: (len({id(v) for v in vals}) == 1) for key, vals in results.items()}
counts = {key: len(vals) for key, vals in results.items()}

print(json.dumps({
    "workers": N,
    "keys": sorted(ACCESSORS),
    "observations_per_key": sorted(set(counts.values())),
    "keys_with_one_object": sorted(k for k, ok in same_object.items() if ok),
    "keys_with_more_than_one_object": sorted(k for k, ok in same_object.items() if not ok),
    "builds": {k: BUILDS.get(k, 0) for k in sorted(ACCESSORS)},
    "cached_keys": deps.cached_keys(),
}))
'''

_rc, _out, _err = _run(_DEPS_CONCURRENCY, cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
check("the deps concurrency probe ran", _rc, 0)
if _rc != 0:
    fail("deps concurrency", f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")
else:
    _payload = _last_json(_out) or {}
    _keys = _payload.get("keys") or []

    # NON-DEGENERATE FIRST. All of this passes trivially with one thread, or
    # with zero keys, or if every worker silently died.
    check("it ran with MAX_WORKERS threads, and MAX_WORKERS is more than one",
          (_payload.get("workers") or 0) > 1, True)
    check("every accessor key was exercised", len(_keys), 8)
    check("every key was observed by every worker (no worker died silently)",
          _payload.get("observations_per_key"), [_payload.get("workers")])

    check("every key handed the same object to all MAX_WORKERS threads",
          _payload.get("keys_with_more_than_one_object"), [])
    check("...for every one of the eight keys, not just the ones that happened "
          "to be fast", sorted(_payload.get("keys_with_one_object") or []),
          sorted(_keys))
    check("each factory ran EXACTLY ONCE, so 'same object' is not twelve builds "
          "with eleven thrown away",
          sorted(set((_payload.get("builds") or {}).values())), [1])
    check("...and deps reports exactly those eight keys as cached, through the "
          "non-building query added in pass 20c-3b",
          sorted(_payload.get("cached_keys") or []), sorted(_keys))


# ===========================================================================
# 5e. THE INFERENCE WRITE LOCK
# ===========================================================================

print("\n" + "=" * 78)
print("5e. concurrent log_inference writes are serialized, and every row lands")
print("=" * 78)

# WHAT THIS REPLACES.
#
# "25- Batch Runner.py" lines 65-73 wrapped log_inference in a lock IN ITS OWN
# NAMESPACE, by rebinding the name after chaining File 14. That protected the
# batch runner and NOTHING ELSE, and there is a second concurrent writer:
# "17- FastAPI Server.py" calls log_inference from loop.run_in_executor(...),
# once per in-flight request, on the event loop's thread pool. Two overlapping
# POST /match requests wrote to one SQLite file through two connections with
# nothing serializing them.
#
# Pass 20c-3b moves the lock into oncotriage/storage/database_logger.py, beside
# the writes it protects. Both callers get it; neither has to know.
#
# WHERE THE RACE ACTUALLY IS, MEASURED RATHER THAN ASSUMED.
#
# The first version of this check drove N threads at an ALREADY-INITIALIZED
# database and expected the unlocked control to lose rows. IT DID NOT: both
# arms landed every row. That result is reported here rather than tuned away,
# because it says something true. On the steady-state INSERT path, SQLite's own
# file locking plus the sqlite3 module's 5-second busy timeout already serialize
# two connections writing to one file, and at this project's contention -- a few
# milliseconds of writing at the end of a ~70-second per-patient pipeline --
# nothing waits long enough to time out. The lock is not what saves that path.
#
# THE PATH IT DOES SAVE IS THE SCHEMA MIGRATION, and there the loss is real,
# silent, and reproducible:
#
#   _ensure_database -> initialize_database runs CREATE TABLE IF NOT EXISTS and
#   then, for each entry in INFERENCE_COLUMN_ADDITIONS, a PRAGMA table_info
#   check followed by an ALTER TABLE ADD COLUMN. ALTER TABLE ADD COLUMN has no
#   IF NOT EXISTS form -- the PRAGMA check IS the guard. Two threads arriving at
#   a fresh database both read the PRAGMA (column absent) and both issue the
#   ALTER; the second gets
#
#       sqlite3.OperationalError: duplicate column name: retrieval_degraded
#
#   which propagates into log_inference's try block, is caught by its
#   `except sqlite3.Error` -- the handler that exists so a logging fault cannot
#   kill the pipeline -- printed as "Database logging failed (non-critical)",
#   and the row is GONE. A run that lost rows this way reports success.
#
# That is exactly the defect class this project exists to remove, and it was
# reachable from the API path, on every first request against a new or migrated
# database, for as long as the only lock in the project lived in File 25.
#
# BOTH SCENARIOS ARE MEASURED BELOW and both results are asserted, so the honest
# finding (the lock does not change the steady-state insert path) is recorded
# rather than quietly dropped.
#
# THE CONTROL IS A COPY, never an edit in place: the module source is parsed,
# every `with _WRITE_LOCK:` is replaced by its own body, and the result is
# exec'd under a different module name.
#
# REPEATED TRIALS, because a race is not deterministic. Measured over 20 trials
# at 24 threads: the unlocked arm lost rows in 18, the locked arm in 0. Eight
# trials puts the chance of a false "clean" run at roughly 1e-8, and the locked
# arm must be clean in ALL of them.

_WRITE_LOCK_DEMO = r'''
import ast, io, json, os, contextlib, sqlite3, tempfile, threading
from concurrent.futures import ThreadPoolExecutor

from oncotriage.config import MAX_WORKERS
from oncotriage.storage import database_logger


# Twice MAX_WORKERS, with two rows each rather than many: the race is at the
# FIRST write against a fresh database, so what matters is how many threads
# arrive at the migration together, not how much they write afterwards.
THREADS = max(2 * MAX_WORKERS, 24)
ROWS_PER_THREAD = 2
TOTAL = THREADS * ROWS_PER_THREAD
TRIALS = 8


def make_result(i):
    """The minimum a terminal node emits that log_inference will accept."""
    return {
        "patient_id": f"patient-{i:04d}",
        "timestamp": f"2026-08-05T00:00:{i % 60:02d}",
        "matching_model": "gpt-5.6-terra",
        "gpt4o_input_tokens": 10,
        "gpt4o_output_tokens": 5,
        "matches": [], "near_misses": [], "not_evaluable": [],
        "stage_timings": {},
    }


PATIENT = {"demographics": {}, "conditions": [], "medications": [], "allergies": []}


def drive(log_fn, db_path):
    """THREADS threads x ROWS_PER_THREAD writes each, all starting together."""
    barrier = threading.Barrier(THREADS)
    errors = []

    def worker(t):
        barrier.wait()
        for r in range(ROWS_PER_THREAD):
            try:
                log_fn(make_result(t * ROWS_PER_THREAD + r), PATIENT, db_path=db_path)
            except Exception as exc:                              # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}")

    # log_inference prints one line per row; silenced so the JSON payload is the
    # last thing on stdout.
    with contextlib.redirect_stdout(io.StringIO()):
        with ThreadPoolExecutor(max_workers=THREADS) as pool:
            list(pool.map(worker, range(THREADS)))

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT COUNT(*) FROM inferences").fetchone()[0]
        distinct = conn.execute(
            "SELECT COUNT(DISTINCT patient_id) FROM inferences").fetchone()[0]
    finally:
        conn.close()
    return {"rows": rows, "distinct": distinct, "errors": errors[:2],
            "error_count": len(errors)}


# --- the control: a COPY of the module with every `with _WRITE_LOCK:` removed
source = open(database_logger.__file__, encoding="utf-8").read()
tree = ast.parse(source)


class StripLock(ast.NodeTransformer):
    removed = 0

    def visit_With(self, node):
        self.generic_visit(node)
        names = [i.context_expr.id for i in node.items
                 if isinstance(i.context_expr, ast.Name)]
        if "_WRITE_LOCK" in names:
            StripLock.removed += 1
            return node.body
        return node


stripped = ast.fix_missing_locations(StripLock().visit(tree))
unlocked_ns = {"__name__": "database_logger_unlocked",
               "__file__": database_logger.__file__}
exec(compile(ast.unparse(stripped), "<database_logger_unlocked>", "exec"), unlocked_ns)

TMP = tempfile.mkdtemp(prefix="oncotriage_writelock_")

# --- SCENARIO A: the schema already exists --------------------------------
# Reported because it is the honest finding: the lock changes nothing here.
steady = {}
for label, log_fn, init_fn in (
        ("locked", database_logger.log_inference, database_logger.initialize_database),
        ("unlocked", unlocked_ns["log_inference"], unlocked_ns["initialize_database"])):
    db = os.path.join(TMP, f"steady-{label}.db")
    with contextlib.redirect_stdout(io.StringIO()):
        init_fn(db)
    steady[label] = drive(log_fn, db)

# --- SCENARIO B: a FRESH database, threads race the migration -------------
fresh = {"locked": [], "unlocked": []}
for label, log_fn in (("locked", database_logger.log_inference),
                      ("unlocked", unlocked_ns["log_inference"])):
    for trial in range(TRIALS):
        # A fresh file AND a fresh memo: _INITIALIZED_DATABASES is what stops
        # the second call re-running the migration, so a stale entry would make
        # every trial after the first one prove nothing.
        db = os.path.join(TMP, f"fresh-{label}-{trial}.db")
        if label == "locked":
            database_logger._INITIALIZED_DATABASES.clear()
        else:
            unlocked_ns["_INITIALIZED_DATABASES"].clear()
        fresh[label].append(drive(log_fn, db))

print(json.dumps({
    "threads": THREADS,
    "trials": TRIALS,
    "expected_rows": TOTAL,
    "locks_stripped": StripLock.removed,
    "steady": steady,
    "fresh_lossy_trials": {
        label: sum(1 for r in runs if r["rows"] != TOTAL)
        for label, runs in fresh.items()
    },
    "fresh_worst_loss": {
        label: TOTAL - min(r["rows"] for r in runs)
        for label, runs in fresh.items()
    },
    "fresh_raised": {
        label: sum(r["error_count"] for r in runs) for label, runs in fresh.items()
    },
}))
'''

_rc, _out, _err = _run(_WRITE_LOCK_DEMO, cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
check("the write-lock demonstration ran", _rc, 0)
if _rc != 0:
    fail("write lock", f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-6:]}")
else:
    _payload = _last_json(_out) or {}
    _expected = _payload.get("expected_rows")
    _trials = _payload.get("trials")
    _steady = _payload.get("steady") or {}
    _lossy = _payload.get("fresh_lossy_trials") or {}

    # NON-DEGENERATE FIRST. Every assertion below passes trivially with one
    # thread, one trial, or a "control" that is a copy of the same module.
    check("it ran with more than one thread", (_payload.get("threads") or 0) > 1, True)
    check("...writing more than one row", (_expected or 0) > 1, True)
    check("...over more than one trial, because a race is not deterministic",
          (_trials or 0) > 1, True)
    check("the control actually removed the lock (all three sites)",
          _payload.get("locks_stripped"), 3)

    # SCENARIO A, reported honestly: the lock does NOT change this path.
    check("steady state, WITH the lock: every row lands",
          (_steady.get("locked") or {}).get("rows"), _expected)
    check("steady state, WITHOUT the lock: every row ALSO lands -- SQLite's own "
          "file locking already serializes two connections on one file, so this "
          "is not what the lock is for",
          (_steady.get("unlocked") or {}).get("rows"), _expected)

    # SCENARIO B: the migration race, which is what the lock is for.
    check(f"fresh database, WITH the lock: zero lossy trials out of {_trials}",
          _lossy.get("locked"), 0)
    check("...and nothing raised in any of them",
          (_payload.get("fresh_raised") or {}).get("locked"), 0)

    # THE CONTROL. If this passed, the check above would be measuring nothing.
    check("fresh database, WITHOUT the lock: rows are LOST (negative control)",
          (_lossy.get("unlocked") or 0) >= 1, True)
    print(f"       control: {_lossy.get('unlocked')}/{_trials} trials lost rows, "
          f"worst loss {(_payload.get('fresh_worst_loss') or {}).get('unlocked')} "
          f"of {_expected}; locked arm lost nothing in "
          f"{_trials}/{_trials} trials")


# ===========================================================================
# 6. THE THREE LATE-BINDING WRAPPERS STILL BIND LATE
# ===========================================================================

print("\n" + "=" * 78)
print("6. File 02's wrappers still read the shared namespace at call time")
print("=" * 78)

# File 02's shim uses no name from File 01 -- it is imports, three defs and
# comments -- so it can be exec'd into a throwaway namespace on its own. Every
# value below differs from the package's, so a wrapper that ignored the
# namespace would produce the package's answer and fail.
_LATE = r'''
import json, os
from datetime import date

_ns = {"__name__": "_exec_chain_"}
with open(os.environ["ONCOTRIAGE_FILE_02"]) as fh:
    exec(fh.read(), _ns)

result = {}

# -- get_age_reference_date: the namespace's date, not the package's 2026-08-03
_ns["DATA_SNAPSHOT_DATE"] = "1999-01-02"
result["namespace_date"] = _ns["get_age_reference_date"]().isoformat()

_ns["DATA_SNAPSHOT_DATE"] = ""
try:
    _ns["get_age_reference_date"]()
    result["empty_raises"] = False
except ValueError:
    result["empty_raises"] = True

# -- get_model_cost: the namespace's price table
_ns["PRICING_CONFIG"] = {"last_updated": "1970-01-01",
                         "models": {"fake-model": {"input": 1000.0, "output": 2000.0}}}
result["fake_cost"] = _ns["get_model_cost"]("fake-model", 1_000_000, 1_000_000)
try:
    _ns["get_model_cost"]("gpt-5.6-terra", 1, 1)
    result["real_model_rejected"] = False
except _ns["UnknownModelPricingError"]:
    result["real_model_rejected"] = True

# -- resolve_qdrant_collection: the namespace's client, no network
class _Alias:
    def __init__(self, alias_name, collection_name):
        self.alias_name = alias_name
        self.collection_name = collection_name

class _Aliases:
    aliases = [_Alias("trial_criteria", "trial_criteria_19700101_000000")]

class _StubQdrant:
    def get_aliases(self):
        return _Aliases()

_ns["qdrant_client"] = _StubQdrant()
_ns["COLLECTION_NAME"] = "trial_criteria"
result["resolved"] = _ns["resolve_qdrant_collection"]()

print(json.dumps(result))
'''

_env_backup = os.environ.get("ONCOTRIAGE_FILE_02")
os.environ["ONCOTRIAGE_FILE_02"] = os.path.join(_code_dir, "02- Utility Functions.py")
_rc, _out, _err = _run(_LATE, cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
if _env_backup is None:
    os.environ.pop("ONCOTRIAGE_FILE_02", None)
else:
    os.environ["ONCOTRIAGE_FILE_02"] = _env_backup

check("File 02's shim exec's into a bare namespace", _rc, 0)
if _rc != 0:
    fail("late-binding wrappers",
         f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")
else:
    _payload = _last_json(_out) or {}
    check("get_age_reference_date uses the namespace's DATA_SNAPSHOT_DATE",
          _payload.get("namespace_date"), "1999-01-02")
    check("...and an empty one still raises (File 38 depends on this)",
          _payload.get("empty_raises"), True)
    check("get_model_cost uses the namespace's PRICING_CONFIG",
          _payload.get("fake_cost"), 3000.0)
    check("...so a model priced only in the package's table is rejected",
          _payload.get("real_model_rejected"), True)
    check("resolve_qdrant_collection asks the namespace's client",
          _payload.get("resolved"), "trial_criteria_19700101_000000")


# ===========================================================================
# 6. THE DASHBOARD (pass 20c-3c-1)
# ===========================================================================

print("\n" + "=" * 78)
print("6. the dashboard: mutable state, the cache contract, and import purity")
print("=" * 78)

# WHY THE DASHBOARD NEEDS CHECKS OF ITS OWN.
#
# STREAMLIT RE-RUNS THE WHOLE SCRIPT ON EVERY INTERACTION. Before this pass,
# "21- Streamlit Dashboard.py" exec'd Files 01 and 02 and chained File 03 at the
# top, and exec_chain caches NOTHING -- it opens and exec()s every file on every
# call. So every button, every filter and every tab click re-read and
# re-executed all three, and because "03- Config.py" calls the client factories
# at shim load, every one of them also constructed an OpenAI client and a Qdrant
# client for a dashboard that uses neither.
#
# Moving the code into modules means each body runs ONCE per process. That is a
# large improvement and it is also a semantic change: module-level mutable state
# now persists across reruns instead of being rebuilt from scratch each time.
# The checks below are the ones that make that change safe rather than merely
# hoped for.

_DASH_DIR = os.path.join(_PKG_DIR, "dashboard")
_TIERS_PY = os.path.join(_DASH_DIR, "tiers.py")
_DATA_PY = os.path.join(_DASH_DIR, "data.py")


# --- 6a. the tier vocabulary is never mutated ------------------------------
#
# MATCH_TIERS (a list) and MATCH_TIER_COLORS (a dict) are the ONLY module-level
# mutable objects the dashboard defines or touches. Everything else it binds at
# module level is a str, and everything it reads out of the package
# (Project_Name, MAX_TRIALS_FOR_EVALUATION, inferences_path) is immutable.
#
# Under the old bootstrap both were rebuilt on every rerun, so a mutation would
# have been erased before the next click and could not accumulate. As modules
# they live for the life of the process, and a mutation would leak into every
# subsequent rerun for every user of that server. Nothing mutates them today.
# This check is what makes that a property rather than an accident.

_MUTATORS = ("append", "extend", "insert", "remove", "pop", "clear", "update",
             "setdefault", "sort", "reverse", "popitem")


def _mutations_of(paths, targets):
    """Every write THROUGH one of `targets` in `paths`.

    Catches three shapes: a subscript store (``X[k] = v``), a mutating method
    call (``X.update(...)``), and a subscript delete. Aliases are followed one
    hop -- ``tier_colors = MATCH_TIER_COLORS`` appears in three tabs, and a
    write through the alias is a write through the object.
    """
    found = []
    for path in paths:
        tree = ast.parse(open(path, encoding="utf-8").read())
        watched = set(targets)
        # one alias hop: any local bound DIRECTLY to a watched name
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Name)
                    and node.value.id in targets):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        watched.add(tgt.id)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.Delete)):
                tgts = (node.targets if isinstance(node, (ast.Assign, ast.Delete))
                        else [node.target])
                for tgt in tgts:
                    if (isinstance(tgt, ast.Subscript)
                            and isinstance(tgt.value, ast.Name)
                            and tgt.value.id in watched):
                        found.append(f"{os.path.basename(path)}:{node.lineno} "
                                     f"{ast.unparse(node)[:60]}")
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in _MUTATORS
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in watched):
                found.append(f"{os.path.basename(path)}:{node.lineno} "
                             f"{ast.unparse(node)[:60]}")
    return sorted(found)


_DASH_FILES = sorted(
    os.path.join(root, name)
    for root, _dirs, files in os.walk(_DASH_DIR)
    for name in files
    if name.endswith(".py") and "__pycache__" not in root
)
check("the dashboard has the fifteen modules this pass created "
      "(non-degeneracy: a scan over an empty file list proves nothing)",
      len(_DASH_FILES), 15)

_TIER_NAMES = ("MATCH_TIERS", "MATCH_TIER_COLORS")
check("nothing in the dashboard mutates MATCH_TIERS or MATCH_TIER_COLORS, "
      "directly or through an alias",
      _mutations_of(_DASH_FILES, _TIER_NAMES), [])

# NON-DEGENERATE, and this is the part that matters: "[] mutations" is also what
# a scanner that looks at nothing returns. A COPY of the tree gets one write of
# each shape planted into it, and the same scanner must find all three. Mutating
# a copy rather than the shipped file is the rule CLAUDE.md sets out, and it
# means a crash here cannot leave the package edited.
_MUT_DIR = tempfile.mkdtemp(prefix="oncotriage-tiermut-")
try:
    _mut_copy = os.path.join(_MUT_DIR, "dashboard")
    shutil.copytree(_DASH_DIR, _mut_copy,
                    ignore=shutil.ignore_patterns("__pycache__"))
    _planted = os.path.join(_mut_copy, "tiers.py")
    _src = open(_planted, encoding="utf-8").read()
    _src += (
        "\n\ndef _planted_defect():\n"
        "    MATCH_TIERS.append('Sixth Tier')\n"          # mutating method
        "    MATCH_TIER_COLORS['Full Match'] = '#000000'\n"  # subscript store
        "    alias = MATCH_TIER_COLORS\n"
        "    alias.update({'No Match': '#ffffff'})\n"     # write through alias
    )
    open(_planted, "w", encoding="utf-8").write(_src)
    _caught = _mutations_of([_planted], _TIER_NAMES)
    check("...and the scanner CATCHES a planted mutation of each of the three "
          "shapes it claims to cover (method, subscript, alias)",
          len(_caught), 3)
finally:
    shutil.rmtree(_MUT_DIR, ignore_errors=True)

check("the tiers module still holds the tier vocabulary the tabs import "
      "(non-degeneracy for the scan above)",
      all(n in open(_TIERS_PY, encoding="utf-8").read()
          for n in _TIER_NAMES + ("classify_trial_score", "enrich_match_tiers")),
      True)


# --- 6b. the @st.cache_data contract ---------------------------------------
#
# @st.cache_data used to be APPLIED AFRESH on every rerun, because the whole
# script re-executed; it is now applied once, at import. The 60-second TTL is
# unchanged by that, and the reason is a fact about streamlit rather than about
# this project: a cached function's identity in the cache is
#
#     md5(func.__module__, func.__qualname__, inspect.getsource(func))
#
# -- streamlit/runtime/caching/cache_utils.py:_make_function_key -- and NOT the
# function OBJECT. So the cache already survived reruns before this pass (had it
# keyed on identity, ttl=60 would never have had anything to expire), and moving
# the loaders into a module changes only the __module__ component: a different
# key, still stable, still 60 seconds, with one cold miss on the first launch.
#
# This is asserted against the INSTALLED streamlit rather than trusted, because
# the whole argument for "the TTL still behaves as it did" rests on it. If a
# future streamlit keys on identity, the dashboard silently stops caching --
# three SQLite reads of the full inferences table on every widget interaction --
# and nothing else in this repository would notice.
_CACHE_KEY_PROBE = r"""
import inspect, json
from streamlit.runtime.caching import cache_utils
src = inspect.getsource(cache_utils._make_function_key)
print(json.dumps({
    "keys_on_module":   "__module__" in src,
    "keys_on_qualname": "__qualname__" in src,
    "keys_on_source":   "getsource" in src,
    "keys_on_id":       "id(func)" in src,
}))
"""
_rc, _out, _err = _run(_CACHE_KEY_PROBE, cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
check("the streamlit cache-key probe ran", _rc, 0)
if _rc == 0:
    check("streamlit keys a cached function on (module, qualname, source) and "
          "NOT on the function object's identity, which is what makes the "
          "60s TTL survive the move into a module",
          _last_json(_out),
          {"keys_on_module": True, "keys_on_qualname": True,
           "keys_on_source": True, "keys_on_id": False})
else:
    fail("streamlit cache-key probe",
         f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-3:]}")

# The three loaders must still CARRY the decorator, with the same TTL. An
# equivalence proof over ast.unparse would catch a dropped decorator on a
# function it compares -- and that is exactly how the decorator loss on four
# @st.fragment tabs was found while this pass was being written -- but this
# states the TTL itself, which no diff against the original would flag if
# someone later "tuned" it.
_data_tree = ast.parse(open(_DATA_PY, encoding="utf-8").read())
_decorated = {
    node.name: [ast.unparse(d) for d in node.decorator_list]
    for node in _data_tree.body if isinstance(node, ast.FunctionDef)
}
check("all three loaders carry @st.cache_data(ttl=60), unchanged",
      _decorated,
      {"load_inferences_data": ["st.cache_data(ttl=60)"],
       "load_trial_matches_data": ["st.cache_data(ttl=60)"],
       "load_drift_metrics_data": ["st.cache_data(ttl=60)"]})

# THE REFRESH BUTTON STILL REACHES THE LOADERS, WHICH NOW LIVE IN ANOTHER
# MODULE. st.cache_data.clear() is a CACHE-wide clear, not a per-function one --
# `render_sidebar` is in oncotriage.dashboard.sidebar and the three loaders are
# in oncotriage.dashboard.data, and before this pass they shared one namespace.
_sidebar_src = open(os.path.join(_DASH_DIR, "sidebar.py"), encoding="utf-8").read()
check("the sidebar's Refresh button still calls the cache-wide clear",
      "st.cache_data.clear()" in _sidebar_src and "st.rerun()" in _sidebar_src,
      True)
_CLEAR_PROBE = r"""
import json, streamlit as st
print(json.dumps({
    # A bound method on the cache object, not a per-function attribute: it
    # empties every @st.cache_data entry in the process whatever module
    # defined the function.
    "clear_is_callable": callable(st.cache_data.clear),
    "per_function_clear_also_exists": callable(
        getattr(st.cache_data, "clear", None)),
}))
"""
_rc, _out, _err = _run(_CLEAR_PROBE, cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
if _rc == 0:
    check("st.cache_data.clear() is a cache-wide clear, so it reaches loaders "
          "defined in a different module than the button",
          (_last_json(_out) or {}).get("clear_is_callable"), True)
else:
    fail("cache clear probe", f"exit {_rc}")


# --- 6c. importing the dashboard touches nothing ---------------------------
#
# SEPARATE FROM SECTION 2, AND DELIBERATELY SO. Section 2 asserts that no
# model-bearing library arrives, and STREAMLIT IS ON THAT LIST -- it is there to
# say that importing the agent does not drag the dashboard in. The dashboard's
# own modules import streamlit at module scope because every render function
# needs it, so folding them into section 2 would have meant deleting streamlit
# from that list and weakening a claim that is worth keeping. They get their own
# trap run instead, with streamlit and plotly pre-imported for the same reason
# section 2 pre-imports matplotlib and seaborn for oncotriage.fhir.explore, and
# with torch / transformers / icd10 still forbidden.
_DASHBOARD_PURITY = r"""
import builtins, io, json, socket, sqlite3, sys

import numpy, pandas                                                      # noqa: F401
import openai, qdrant_client, dotenv, httpx, tenacity, caffeine           # noqa: F401
# The dashboard's module-scope third-party dependencies. streamlit reads its
# config file and plotly loads its package data at import; neither is this
# package's doing, which is the same allowance section 2 makes for matplotlib.
import streamlit, plotly.express, plotly.graph_objects, plotly.subplots    # noqa: F401


class Blocked(RuntimeError):
    pass


_real_socket = socket.socket


class BlockedSocket(_real_socket):
    def __init__(self, *args, **kwargs):
        raise Blocked("socket.socket() was constructed")


def _blocked(*args, **kwargs):
    raise Blocked("a blocked call was made")


socket.socket = BlockedSocket
socket.create_connection = _blocked
sqlite3.connect = _blocked
builtins.open = _blocked
io.open = _blocked

import oncotriage.paths as _p
import oncotriage.dashboard
import oncotriage.dashboard.data
import oncotriage.dashboard.sidebar
import oncotriage.dashboard.tiers
import oncotriage.dashboard.app
import oncotriage.dashboard.tabs
import oncotriage.dashboard.tabs.overview
import oncotriage.dashboard.tabs.performance
import oncotriage.dashboard.tabs.cost_tokens
import oncotriage.dashboard.tabs.demographics
import oncotriage.dashboard.tabs.patient_explorer
import oncotriage.dashboard.tabs.match_quality
import oncotriage.dashboard.tabs.trial_explorer
import oncotriage.dashboard.tabs.drift
import oncotriage.dashboard.tabs.reproducibility

heavy = [m for m in ("torch", "transformers", "sentence_transformers", "icd10")
         if m in sys.modules]

armed = {}
for _name, _fn, _args in (("socket", socket.socket, (socket.AF_INET, socket.SOCK_STREAM)),
                          ("sqlite3", sqlite3.connect, (":memory:",)),
                          ("open", builtins.open, ("/oncotriage-trap-probe",)),
                          ("io.open", io.open, ("/oncotriage-trap-probe",))):
    try:
        _fn(*_args)
        armed[_name] = False
    except Blocked:
        armed[_name] = True

print(json.dumps({"heavy": heavy, "armed": armed,
                  "resolved": sorted(_p._RESOLVED)}))
"""

_rc, _out, _err = _run(_DASHBOARD_PURITY, cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
check("all fifteen dashboard modules import with open, io.open, socket.socket, "
      "socket.create_connection and sqlite3.connect patched to raise", _rc, 0)
if _rc != 0:
    fail("dashboard import purity",
         f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")
else:
    _payload = _last_json(_out) or {}
    check("every trap was ARMED after the dashboard imports",
          _payload.get("armed"),
          {"socket": True, "sqlite3": True, "open": True, "io.open": True})
    check("importing the dashboard loads no model-bearing library",
          _payload.get("heavy"), [])
    # THE ONE THAT MATTERS FOR data.py. Reading inferences_path at module scope
    # would resolve the sibling data tree at import; reading it inside the
    # function body resolves it on first call.
    check("importing the dashboard resolves NO path -- data.py reads "
          "paths.inferences_path inside the function body, not at module scope",
          _payload.get("resolved"), [])


# --- 6d. the three loaders read the path lazily and their SQL is unchanged --
#
# The path is the ONE thing this pass was allowed to change in the loaders, and
# the SQL is the thing it was specifically not allowed to change. Both are
# stated here so a later edit that "tidies" either one goes red.
# READ THE AST, NOT THE TEXT. data.py's docstring EXPLAINS the rule by quoting
# the very line it must not contain ("from oncotriage.paths import
# inferences_path"), so a substring scan reports the module as violating the
# rule its own documentation is stating. That is not a hypothetical: the first
# version of this check was a substring scan and it went red on a correct
# module. The same trap caught check 6e below.
_data_imports = [
    (node.module, sorted(a.name for a in node.names))
    for node in _data_tree.body if isinstance(node, ast.ImportFrom)
]
check("data.py does NOT import inferences_path by name (that would be an "
      "attribute read, and would resolve the tree at import)",
      [m for m, names in _data_imports
       if m == "oncotriage.paths" or "inferences_path" in names],
      [])
check("...it imports the paths MODULE instead",
      ("oncotriage", ["paths"]) in _data_imports, True)
_data_src = open(_DATA_PY, encoding="utf-8").read()
check("...and all three loaders read the attribute at call time",
      _data_src.count("sqlite3.connect(paths.inferences_path)"), 3)

_SQL = sorted(
    node.args[0].value
    for node in ast.walk(_data_tree)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and node.func.attr == "read_sql_query"
    and node.args and isinstance(node.args[0], ast.Constant)
)
check("the three loaders' SQL is byte-for-byte what File 21 had",
      _SQL,
      ["SELECT * FROM drift_metrics ORDER BY timestamp DESC",
       "SELECT * FROM inferences",
       "SELECT * FROM trial_matches"])


# --- 6e. File 21 is a thin entry point -------------------------------------
#
# Nothing in the repository chains it or reads a name out of it: every top-level
# name it bound was grepped against every .py, .md, .toml and .yml in the tree
# and every hit is inside File 21 itself, prose in a .md, or the `streamlit run`
# command in docker-compose.yml. So it keeps NO re-export shim -- unlike File 05
# (File 34 chains it), File 13 and File 20 (File 41 chains it).
_F21 = open(os.path.join(_code_dir, "21- Streamlit Dashboard.py"),
            encoding="utf-8").read()
_f21_tree = ast.parse(_F21)
# BY AST, for the same reason as check 6d: File 21's docstring explains at
# length that the exec bootstrap is gone and why, so it necessarily contains the
# words "exec()" and "exec_chain". A substring scan calls that a violation. The
# question is whether the file CALLS either one, which only the AST answers.
_f21_calls = sorted({
    node.func.id for node in ast.walk(_f21_tree)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    and node.func.id in ("exec", "exec_chain")
})
check("File 21 calls neither exec() nor exec_chain()", _f21_calls, [])
# ...and reads neither name at all, which also rules out `f = exec` or passing
# exec_chain somewhere. Names, not text.
_f21_names = sorted({
    node.id for node in ast.walk(_f21_tree)
    if isinstance(node, ast.Name) and node.id in ("exec", "exec_chain")
})
check("...and does not reference either name in any other position",
      _f21_names, [])
check("File 21 binds no function of its own",
      sorted(n.name for n in _f21_tree.body if isinstance(n, ast.FunctionDef)), [])
_f21_imports = sorted(
    f"{node.module}.{a.name}" for node in ast.walk(_f21_tree)
    if isinstance(node, ast.ImportFrom) for a in node.names
)
check("File 21 imports main from the package",
      "oncotriage.dashboard.app.main" in _f21_imports, True)
check("...and guards the call, so reading the file still does nothing",
      any(isinstance(n, ast.If)
          and "__name__" in ast.unparse(n.test)
          and "__main__" in ast.unparse(n.test)
          for n in _f21_tree.body), True)
# docker-compose must still launch the dashboard with `streamlit run` on File
# 21. The file being a valid script under that invocation is what makes the
# container keep working.
#
# THIS USED TO BE A SUBSTRING MATCH and item 21 broke it without breaking the
# property:
#
#     check(..., 'streamlit run "21- Streamlit Dashboard.py"' in _COMPOSE, True)
#
# Item 21 changed the command from a shell string, `bash -c 'exec streamlit run
# "21- ..."'`, to an argument VECTOR. The vector needs no shell and no quoting —
# which is the whole reason the quotes were there — so the literal substring is
# gone while the command is the same command. The check failed; nothing was
# wrong.
#
# That is the third instance in this project of the failure mode CLAUDE.md
# already records twice ("a substring is not a definition"): File 47's own BM25
# construction-site check, which `fastembed.SparseTextEmbedding(...)` evaded,
# and File 49's revision selector, which matched its own deletion comment. So
# this asks the actual question — parse the compose file, take the streamlit
# service's command, reduce it to tokens whichever form it is written in, and
# require `streamlit`, `run`, <script> to appear as three consecutive tokens.
# The script name is compared EXACTLY; a typo in it still fails.
_COMPOSE = open(os.path.join(_code_dir, "docker-compose.yml"), encoding="utf-8").read()


def _compose_command_tokens(compose_text, service):
    """The service's command as a token list, for either compose command form.

    Handles the three shapes that mean the same thing here:
      * a string            -> shlex
      * a list of arguments -> already tokens
      * ["bash", "-c", "..."] (or sh) -> shlex of the script, since that is
        where the real command lives. Without this the wrapper would hide it.
    """
    import shlex

    import yaml

    command = yaml.safe_load(compose_text)["services"][service].get("command")
    if command is None:
        return []
    if isinstance(command, str):
        return shlex.split(command)
    tokens = list(command)
    if len(tokens) >= 3 and os.path.basename(tokens[0]) in ("bash", "sh") \
            and tokens[1] in ("-c", "-lc"):
        return shlex.split(tokens[2])
    return tokens


def _launches_dashboard(tokens, script="21- Streamlit Dashboard.py"):
    want = ["streamlit", "run", script]
    return any(tokens[i:i + 3] == want for i in range(len(tokens)))


_STREAMLIT_TOKENS = _compose_command_tokens(_COMPOSE, "streamlit")
check("docker-compose still launches the dashboard by the same command",
      _launches_dashboard(_STREAMLIT_TOKENS), True)
# NON-DEGENERATE: an empty or unparsed command would satisfy nothing above but
# would also make the check meaningless if the helper silently returned [].
check("...and the command was actually parsed (not an empty token list)",
      len(_STREAMLIT_TOKENS) >= 3, True)
# NEGATIVE CONTROLS. Each is the real check run against a doctored compose text,
# and each must FAIL. Without these, "does the command launch the dashboard"
# would be indistinguishable from "does the helper return something truthy".
check("...control: a compose file running a DIFFERENT script does not pass",
      _launches_dashboard(_compose_command_tokens(
          _COMPOSE.replace("21- Streamlit Dashboard.py",
                           "21- Streamlit Dashboard BACKUP.py"), "streamlit")),
      False)
check("...control: dropping `run` from the command does not pass",
      _launches_dashboard([t for t in _STREAMLIT_TOKENS if t != "run"]), False)
check("...control: the shell-string form is still recognised (it is the form "
      "this file shipped with before item 21)",
      _launches_dashboard(_compose_command_tokens(
          'services: {streamlit: {command: '
          '[bash, -c, \'exec streamlit run "21- Streamlit Dashboard.py" '
          '--server.port=8501\']}}', "streamlit")),
      True)


# --- 6f. every name File 21 used to bind still exists ----------------------
#
# Pinned at the commit before this pass, extracted by exec'ing the original into
# a throwaway namespace and subtracting the base chain (01 -> 02 -> 03) -- the
# same method the other inventories in this file use. Written down rather than
# recomputed from HEAD, because the point is to pin what the surface WAS.
#
# The three bootstrap leftovers -- _bootstrap, _code_dir, _fh -- are deliberately
# absent: they were exec-chain scaffolding, they are private, and nothing read
# them. Everything else must still be reachable.
_PRE_3C_F21_NAMES = [
    "MATCH_TIERS", "MATCH_TIER_COLORS",
    "TRIAL_STATUS_FULL", "TRIAL_STATUS_PARTIAL", "TRIAL_STATUS_REJECTED",
    "TRIAL_STATUS_UNCONFIRMED",
    "classify_trial_score", "enrich_match_tiers",
    "load_drift_metrics_data", "load_inferences_data", "load_trial_matches_data",
    "main",
    "render_cost_tokens_tab", "render_drift_detection_tab",
    "render_match_quality_tab", "render_overview_tab",
    "render_patient_demographics_tab", "render_patient_explorer_tab",
    "render_performance_tab", "render_reproducibility_tab",
    "render_sidebar", "render_trial_explorer_tab",
]
check("the recorded File 21 name list is the size it was extracted at",
      len(_PRE_3C_F21_NAMES), 22)

_F21_SURFACE_PROBE = r"""
import importlib, json, sys
wanted = json.loads(sys.argv[1])
missing = []
for name, module_name in wanted.items():
    module = importlib.import_module(module_name)
    if not hasattr(module, name):
        missing.append(module_name + "." + name)
print(json.dumps({"missing": missing, "checked": len(wanted)}))
"""
_F21_HOMES = {
    "MATCH_TIERS": "oncotriage.dashboard.tiers",
    "MATCH_TIER_COLORS": "oncotriage.dashboard.tiers",
    "TRIAL_STATUS_FULL": "oncotriage.dashboard.tiers",
    "TRIAL_STATUS_PARTIAL": "oncotriage.dashboard.tiers",
    "TRIAL_STATUS_REJECTED": "oncotriage.dashboard.tiers",
    "TRIAL_STATUS_UNCONFIRMED": "oncotriage.dashboard.tiers",
    "classify_trial_score": "oncotriage.dashboard.tiers",
    "enrich_match_tiers": "oncotriage.dashboard.tiers",
    "load_drift_metrics_data": "oncotriage.dashboard.data",
    "load_inferences_data": "oncotriage.dashboard.data",
    "load_trial_matches_data": "oncotriage.dashboard.data",
    "main": "oncotriage.dashboard.app",
    "render_sidebar": "oncotriage.dashboard.sidebar",
    "render_overview_tab": "oncotriage.dashboard.tabs.overview",
    "render_performance_tab": "oncotriage.dashboard.tabs.performance",
    "render_cost_tokens_tab": "oncotriage.dashboard.tabs.cost_tokens",
    "render_patient_demographics_tab": "oncotriage.dashboard.tabs.demographics",
    "render_patient_explorer_tab": "oncotriage.dashboard.tabs.patient_explorer",
    "render_match_quality_tab": "oncotriage.dashboard.tabs.match_quality",
    "render_trial_explorer_tab": "oncotriage.dashboard.tabs.trial_explorer",
    "render_drift_detection_tab": "oncotriage.dashboard.tabs.drift",
    "render_reproducibility_tab": "oncotriage.dashboard.tabs.reproducibility",
}
check("...and every one of them has a recorded home in the package",
      sorted(_F21_HOMES), sorted(_PRE_3C_F21_NAMES))

_rc, _out, _err = _run(
    _F21_SURFACE_PROBE.replace('sys.argv[1]', repr(json.dumps(_F21_HOMES))),
    cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
check("the File 21 surface probe ran", _rc, 0)
if _rc == 0:
    _payload = _last_json(_out) or {}
    check("every name File 21 bound before this pass is still exported by the "
          "module that now owns it", _payload.get("missing"), [])
    check("...over all 22 names (a probe over an empty set proves nothing)",
          _payload.get("checked"), 22)
else:
    fail("File 21 surface probe",
         f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")


#------------------------------------------------------------------------------


shutil.rmtree(_ELSEWHERE, ignore_errors=True)


#------------------------------------------------------------------------------


# ===========================================================================
# REPORT
# ===========================================================================

print()
print("=" * 78)
print("SUMMARY")
print("=" * 78)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")

if _FAILURES:
    print("\nFailures:")
    for _f in _FAILURES:
        print(f"  - {_f}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug  5 2026

@author: ramyalsaffar
"""
