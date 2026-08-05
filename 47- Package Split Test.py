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

  5b. THE FILE 10 SPLIT HAS EXACTLY ONE SHARED NAME. Re-derived against the
     shipped modules rather than asserted in a comment: stage.py and
     histology.py must reference nothing the other defines, and the one name
     they both reach for must be _is_negated, out of negation.py.

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
import json
import os
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

check("the package file list is non-empty and covers all four subpackages",
      len(_PKG_FILES) >= 13
      and any(f.endswith("registries/mesh.py") for f in _PKG_FILES)
      and any(f.endswith("extraction/negation.py") for f in _PKG_FILES)
      and any(f.endswith("fhir/parser.py") for f in _PKG_FILES)
      and any(f.endswith("storage/database_logger.py") for f in _PKG_FILES),
      True)

# EVERY SUBPACKAGE MUST BE DECLARED IN pyproject.toml. setuptools does not
# recurse into a listed package, so a subpackage present in the tree and absent
# from the `packages` list is importable from an EDITABLE install (which maps
# the source tree) and MISSING from a built wheel. That difference does not
# surface until someone builds one. Read as text rather than with a TOML parser
# because tomllib would only tell us the list parses, not that it matches the
# directory tree.
_PYPROJECT = open(os.path.join(_code_dir, "pyproject.toml"), encoding="utf-8").read()
_SUBPACKAGE_DIRS = sorted(
    "oncotriage." + name
    for name in os.listdir(_PKG_DIR)
    if os.path.isfile(os.path.join(_PKG_DIR, name, "__init__.py"))
)
check("the tree has the subpackages this pass expects (non-degeneracy)",
      _SUBPACKAGE_DIRS,
      ["oncotriage.extraction", "oncotriage.fhir", "oncotriage.registries",
       "oncotriage.storage"])
check("every subpackage on disk is declared in pyproject.toml, so a built "
      "wheel carries it",
      sorted(p for p in _SUBPACKAGE_DIRS if f'"{p}"' not in _PYPROJECT), [])


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
import builtins, io, json, socket, sqlite3, sys

import caffeine, dotenv, httpx, openai, qdrant_client, tenacity           # noqa: F401
import collections, glob, logging, os, pathlib, re, threading, typing     # noqa: F401
import xml.etree.ElementTree                                              # noqa: F401


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

import oncotriage.constants
import oncotriage.settings
import oncotriage.paths
import oncotriage.config
import oncotriage.utils
import oncotriage.registries.cancer_code_registry
import oncotriage.registries.mesh
import oncotriage.registries.mesh_crosswalk_build
import oncotriage.extraction.negation
import oncotriage.extraction.stage
import oncotriage.extraction.histology
import oncotriage.fhir.parser
import oncotriage.storage.database_logger

heavy = [m for m in ("torch", "transformers", "sentence_transformers",
                     "streamlit", "langgraph", "icd10") if m in sys.modules]

armed = {}
for _name, _fn, _args in (("socket", socket.socket, (socket.AF_INET, socket.SOCK_STREAM)),
                          ("sqlite3", sqlite3.connect, (":memory:",)),
                          # The path never has to exist: the trap raises
                          # before anything reaches the filesystem, and a path
                          # that does exist would let a FAILED trap silently
                          # succeed instead of raising FileNotFoundError.
                          ("open", builtins.open, ("/oncotriage-trap-probe",)),
                          ("io.open", io.open, ("/oncotriage-trap-probe",))):
    try:
        _fn(*_args)
        armed[_name] = False
    except Blocked:
        armed[_name] = True

print(json.dumps({"heavy": heavy, "armed": armed}))
"""

# 13 as of pass 20c-2b: oncotriage.fhir.parser and
# oncotriage.storage.database_logger joined the list. The logger is the one that
# matters most here -- its whole subject is a SQLite database, and item 20b's
# claim that loading it opens nothing is exactly what the sqlite3.connect trap
# tests. Before 20b this import would have created three tables in the
# production inferences.db.
_MODULES_UNDER_TRAP = 13

_rc, _out, _err = _run(_PURITY, cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
check(f"all {_MODULES_UNDER_TRAP} package modules import with open, io.open, "
      f"socket.socket, socket.create_connection and sqlite3.connect patched to "
      f"raise", _rc, 0)
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
    check("every trap was ARMED after the imports (socket, sqlite3, open, io.open)",
          _armed,
          {"socket": True, "sqlite3": True, "open": True, "io.open": True})
    check("no model-bearing library was imported (torch / transformers / "
          "sentence_transformers / streamlit / langgraph / icd10)",
          _payload.get("heavy"), [])


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
_IMPORT_PROBE = r'''
import importlib, json, sys
missing = []
for module_name, names in json.loads(sys.argv[1] if len(sys.argv) > 1 else "{}").items():
    module = importlib.import_module(module_name)
    for name in names:
        if not hasattr(module, name):
            missing.append(module_name + "." + name)
print(json.dumps({"missing": missing}))
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

_SHIM_PROBE = r"""
import json, sys
path = sys.argv[1]
ns = {"__name__": "_exec_chain_", "__file__": path}
with open(path, encoding="utf-8") as fh:
    exec(fh.read(), ns)
print(json.dumps(sorted(k for k in ns if not k.startswith("__"))))
"""

# Files 08, 09 and 10 (pass 2a) and Files 07 and 14 (pass 2b) are checked by the
# same loop against the same rules. The two inventories stay in separate dicts
# above because each was extracted at a different commit and each is pinned to
# what its file bound at that commit; merging them here is just iteration.
_RUNTIME_INVENTORY = dict(_PRE_2A_RUNTIME_NAMES)
_RUNTIME_INVENTORY.update(_PRE_2B_RUNTIME_NAMES)

_RUNTIME_COUNTS = dict(_PRE_2A_COUNTS)
_RUNTIME_COUNTS.update(_PRE_2B_COUNTS)

_RUNTIME_ADDED = {name: set() for name in _PRE_2A_RUNTIME_NAMES}
_RUNTIME_ADDED.update(_PASS_2B_ADDED)

check("the inventory covers all five converted files",
      sorted(_RUNTIME_INVENTORY), sorted(_PASS_2B_DROPPED))

for _filename, _expected in _RUNTIME_INVENTORY.items():
    check(f"the recorded runtime name list for {_filename[:2]} is the size it "
          f"was extracted at", len(_expected), _RUNTIME_COUNTS[_filename])

    _proc = subprocess.run(
        [sys.executable, "-c", _SHIM_PROBE, os.path.join(_code_dir, _filename)],
        cwd=_ELSEWHERE, capture_output=True, text=True,
        env={**{k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
             **({"PYTHONPATH": _FALLBACK_PATH} if _FALLBACK_PATH else {})},
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
