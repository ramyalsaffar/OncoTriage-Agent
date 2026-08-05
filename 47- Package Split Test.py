# Package Split Test
####################

"""
Proves the item 20c package split.

WHAT WAS CHANGED
----------------
Files 01, 02 and 03 stopped being the definitions and became re-export shims
over a real Python package:

    oncotriage/settings.py    env-var names, path resolution, load_env_keys()
    oncotriage/paths.py       IS_DOCKER, _glob_one, every path variable
    oncotriage/constants.py   SYSTEM_KEY_ABSENT / SYSTEM_KEY_UNRECOGNIZED
    oncotriage/config.py      every tunable + LAZY client/keys accessors
    oncotriage/utils.py       cost, retry, partial dates, exec_chain, caffeinate

THE CYCLE THAT MADE THIS NON-TRIVIAL
------------------------------------
    '02- Utility Functions.py' read PRICING_CONFIG, COLLECTION_NAME,
                               qdrant_client and DATA_SNAPSHOT_DATE from File 03
    '03- Config.py'            called load_env_keys(), from File 02, at line 194

Under exec() into one shared namespace both directions resolve at call time and
nothing complains. As modules it is an ImportError. load_env_keys moved to
oncotriage.settings, which config imports and utils does not, and that is the
edge that broke it.

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

  2. IMPORTING TOUCHES NOTHING LIVE. A subprocess replaces socket.socket with a
     class that raises on construction, replaces socket.create_connection and
     sqlite3.connect with functions that raise, and only then imports all five
     package modules. Proved by patching, not by reading the source. The trap is
     shown to be ARMED afterwards, so a run where the patch silently did nothing
     fails instead of passing vacuously.

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

  5. NO NAME WAS DROPPED. The set of names each of Files 01, 02 and 03 defined
     before item 20c is recorded below, extracted from the files at commit
     3780ba1. Each shim's AST must still bind every one of them, and every name
     the shims import from the package must actually exist on that package
     module.

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
check("...and the detector is not vacuous: config DOES import oncotriage.settings",
      _mentions_module(_CONFIG_PY, "oncotriage.settings"), True)
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
_needle = "from oncotriage import settings"
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
# 2. IMPORTING TOUCHES NO SOCKET, NO DATABASE, NO MODEL
# ===========================================================================

print("\n" + "=" * 78)
print("2. importing every package module under a socket / sqlite trap")
print("=" * 78)

# socket.socket is replaced by a SUBCLASS that raises in __init__, not by a
# plain function: `ssl.py` does `class SSLSocket(socket)` at import time, and a
# function cannot be subclassed. Raising before super().__init__ means no file
# descriptor is ever allocated.
#
# The heavy-module list is torch / transformers / sentence_transformers /
# streamlit / langgraph. fastembed is deliberately NOT in it: qdrant_client
# imports it transitively, so its presence in sys.modules says nothing about
# this package -- and importing fastembed loads no weights, which is what the
# claim is about. The MedCPT cross-encoder is what "loads a model" means here,
# and it needs torch and transformers.
_PURITY = r'''
import json, socket, sqlite3, sys

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

import oncotriage.constants
import oncotriage.settings
import oncotriage.paths
import oncotriage.config
import oncotriage.utils

heavy = [m for m in ("torch", "transformers", "sentence_transformers",
                     "streamlit", "langgraph") if m in sys.modules]

armed_socket = False
try:
    socket.socket(socket.AF_INET, socket.SOCK_STREAM)
except Blocked:
    armed_socket = True

armed_db = False
try:
    sqlite3.connect(":memory:")
except Blocked:
    armed_db = True

print(json.dumps({"heavy": heavy, "armed_socket": armed_socket, "armed_db": armed_db}))
'''

_rc, _out, _err = _run(_PURITY, cwd=_ELSEWHERE, extra_path=_FALLBACK_PATH)
check("all five package modules import with socket.socket and sqlite3.connect "
      "patched to raise", _rc, 0)
if _rc != 0:
    fail("import purity",
         f"exit {_rc}; stderr tail: {_err.strip().splitlines()[-4:]}")
else:
    _payload = _last_json(_out) or {}
    # NON-DEGENERATE, and this is the important one: a subprocess where the
    # patch silently did nothing would also exit 0. These two assert the trap
    # was live at the end of the run.
    check("the socket trap was ARMED (a socket built after the imports raises)",
          _payload.get("armed_socket"), True)
    check("the sqlite trap was ARMED (a connect after the imports raises)",
          _payload.get("armed_db"), True)
    check("no model-bearing library was imported",
          _payload.get("heavy"), [])


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
