# Logging Contract Test
#######################

"""
Logging Contract Test

Audits the path from a pipeline state to a row in inferences.db for the defect
class where a column is written from something other than what the pipeline
observed, so the column is constant and every consumer of it reports the
configuration instead of the run.

The three defects under test:

  1. llm_classifier_retries — File 14 read result["gpt4o_retries_exhausted"], a key only
     node_error_handler wrote. Every successful inference logged 0 retries no
     matter how many were spent, so File 20's llm_classifier_retry_rate_z_score watched
     a column that could not vary.

  2. ablation_flags — no terminal node wrote the key at all, so the production
     column was '{}' on every row and a configuration-traceability query had no
     configuration to trace.

  3. bm25_retrieved / vector_retrieved — File 14 inserted the constants
     BM25_RETRIEVAL_SIZE and VECTOR_RETRIEVAL_SIZE, so every row read 75 and
     100, including under bm25_only / vector_only ablation where one channel
     never ran.

Covers:
    1. STRUCTURAL — the three terminal nodes declare the same result key set,
       so no future key can exist on one path only. This is the regression
       guard: it fails for any key added to node_finalize alone.
    2. STRUCTURAL — every dict return in node_llm_classifier_evaluation carries
       llm_classifier_retries, so the count reaches Stage 6 on the success path and on
       each failure path.
    3. Retry count and ablation flags survive all three terminal nodes.
    4. Stage 2 reports OBSERVED per-channel counts: sparse counts are the union
       of the three field queries (not their sum, not the request size), an
       ablated channel reports 0, and a channel whose query raises reports 0.
    5. END TO END — log_inference() writes a row whose llm_classifier_retries,
       ablation_flags, bm25_retrieved and vector_retrieved match the run rather
       than the config, into a throwaway database.
    6. The hallucinated-trial columns exist and separate MEASURED from NOT
       MEASURED. Stage 5's out-of-set detector now runs, so a result that came
       through a terminal node carrying the key stores 0 -- "checked, and every
       returned entry was in the candidate set" -- while NULL is reserved for a
       result dict that never passed through a terminal node and for a terminal
       path where Stage 5 never ran. This test used to assert NULL on a clean
       run; that premise ended with the detector, and asserting it still would
       be asserting the absence of the feature.

No network and no LLM: Qdrant, the sparse query model and the embedding call
are replaced with stubs, and no terminal node calls a model. The database is a
temporary file; the real inferences.db is never opened — every log_inference()
call passes db_path EXPLICITLY and asserts on the path the writer reports back.

Run from terminal (or F5 in Spyder):
    python tests/test_storage_inference_logging_contract.py
    (was: python "36- Logging Contract Test.py")

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# PASS 20d-1: THIS FILE IMPORTS THE PACKAGE. It used to exec "01- Imports.py"
# and "02- Utility Functions.py" into its own globals and then exec_chain()
# "13- LangGraph Agent.py", which is how every name below used to arrive.
#
# THE STORAGE LAYER IS IMPORTED HERE, NOT EXEC'D LATER, and that is the one
# behaviour this move actually changes. File 14 used to be exec'd further down,
# AFTER inferences_path was repointed at a temporary file, because the shim's
# log_inference wrapper reads globals().get("inferences_path") and that redirect
# only worked if the shim was in this namespace. That mechanism is gone; what
# replaces it is the second one this file already had and already relied on --
# EVERY log_inference() CALL PASSES db_path EXPLICITLY and asserts on the path
# the writer reports back. Measured before the move rather than assumed: the two
# call sites are lines 763 and 862, both `db_path=inferences_path`, and
# initialize_database is never called here at all. Test 0 is the standing guard:
# it asserts that omitting db_path resolves to PRODUCTION and that production is
# not this file's scratch path, so the explicit argument is doing real work.
#
# THE CANDIDATE DIRECTORY IS THE PARENT OF THIS FILE'S, not this file's own.
# The same block Files 47, 48 and 49 carry looks one level up because this file
# now sits in tests/ and the package sits BESIDE tests/, not inside it.
# `pip install -e .` makes the whole block a no-op.
import json
import os
import sqlite3
import sys

try:
    import oncotriage  # noqa: F401
except ImportError:
    for _candidate, _how in (
        (os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
         if "__file__" in globals() else None, "__file__"),
        (os.getcwd(), "cwd"),
    ):
        if _candidate and os.path.isdir(os.path.join(_candidate, "oncotriage")):
            if _candidate not in sys.path:
                sys.path.insert(0, _candidate)
            print(f"[Bootstrap] oncotriage package found at {_candidate} "
                  f"(via {_how}); added to sys.path")
            break
    else:
        raise
    del _candidate, _how

from oncotriage.agent import deps
from oncotriage.agent import evaluation as _agent_evaluation
from oncotriage.agent.retrieval import node_hybrid_retrieval
from oncotriage.agent.terminal import (
    node_error_handler,
    node_finalize,
    node_no_candidates,
)
from oncotriage.config import (
    BM25_RETRIEVAL_SIZE,
    MATCHING_MODEL,
    VECTOR_RETRIEVAL_SIZE,
)
from oncotriage.paths import inferences_path
from oncotriage.registries import primary_cancer as _primary_cancer
from oncotriage.storage import database_logger as _database_logger
from oncotriage.storage.database_logger import (
    log_inference,
    resolve_inference_db_path,
)
from oncotriage.utils import get_model_cost


#------------------------------------------------------------------------------


import ast
import shutil
import tempfile
import textwrap


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label: str, actual, expected) -> None:
    """Assert equality, record the outcome, never abort the run."""
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(
            f"{label}\n          expected: {expected}\n          actual:   {actual}"
        )
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


# ===========================================================================
# THROWAWAY DATABASE
# ===========================================================================
# ONE MECHANISM KEEPS THIS TEST OFF THE PRODUCTION DATABASE, and it is the one
# that never depended on a seam:
#
#   Every log_inference() call passes db_path EXPLICITLY, and asserts on the
#   path the writer reports back.
#
# THERE USED TO BE A SECOND, AND PASS 20d-1 RETIRED IT RATHER THAN LEAVING IT
# LOOKING LIVE. It was: rebind inferences_path, then exec "14- Database
# Logger.py" into this namespace so the shim's log_inference wrapper picked the
# rebound value up through globals().get("inferences_path"). That worked only
# while this file was part of the exec chain. It now imports
# oncotriage.storage.database_logger directly, so the rebinding below reaches
# the writer NOT AT ALL -- and saying so is the point, because a comment
# claiming two mechanisms while one of them is inert is worse than having one.
#
# The rebinding is kept because Test 5 and Test 7 read rows back through
# sqlite3.connect(inferences_path) and the two must agree on which file is under
# test.
#
# The production path is captured FIRST, before anything shadows it, so the
# discrimination check below has something real to compare against.

_PRODUCTION_INFERENCES_PATH = inferences_path

_TMP_DIR = tempfile.mkdtemp(prefix="oncotriage_logging_contract_")
inferences_path = os.path.join(_TMP_DIR, "inferences_test.db")

# PASS 20d-1: the exec of "14- Database Logger.py" that stood here is now an
# import at the top of the file. See the note in the bootstrap block for why
# that is safe: mechanism 1 (the shim's globals()-reading wrapper) is gone,
# mechanism 2 (an explicit db_path on every call, asserted on the returned
# path) is what this file already relied on, and Test 0 below is the standing
# guard that mechanism 2 is not vacuous.


def check_wrote_to_scratch(label: str, reported_path) -> None:
    """Assert log_inference reported the scratch database, not production.

    log_inference returns the path it resolved, so this is the path the writer
    ACTUALLY used rather than a path recomputed beside it.

    SHOWN TO FAIL, 2026-08-05, as CLAUDE.md requires of any new assertion.
    The demonstration mutated this file's SOURCE TEXT IN MEMORY -- nothing on
    disk was edited, and the sha256 was compared before and after to prove it --
    and redirected a log_inference call to a SECOND temporary database, never to
    the production one:

      run 1, both calls redirected
        FAIL  log_inference wrote to the scratch database, not production
                expected: .../inferences_test.db
                actual:   .../decoy_not_the_scratch.db
        the run then aborted at Test 5's readback with
        "sqlite3.OperationalError: no such table: inferences", because the
        schema had been created in the decoy. That abort is a second,
        independent signal that the mutation bit -- and it stopped the Test 7
        assertion from ever running, which is why there was a second run.

      run 2, only Test 7's call redirected
        62 passed, 2 failed:
          - the reasoning-cost row also went to the scratch database
          - a row was written for the reasoning case   (the consequence)
        sha256 before == sha256 after: True

    Unmutated, both assertions pass. Test 0 above is the standing guard on the
    same property: it checks that the value a caller gets by NOT passing
    db_path is the production database and is not this scratch file, so the
    comparison here cannot be satisfied by the two paths coinciding.
    """
    check(label, reported_path, inferences_path)


# --- THE ASSERTION ABOVE IS SHOWN TO DISCRIMINATE ---------------------------
# CLAUDE.md: an assertion that has only ever passed is not evidence that it can
# catch anything. check_wrote_to_scratch() would pass vacuously if
# resolve_inference_db_path() ignored its argument, or if the production path
# and the scratch path happened to be the same string.
#
# resolve_inference_db_path(None) is what a caller that forgot db_path gets --
# and it RESOLVES without connecting, so this control names the hazard without
# going anywhere near the production file.

_PACKAGE_DEFAULT_DB = resolve_inference_db_path(None)

print("\n" + "=" * 70)
print("Test 0: the database-isolation assertion can fail")
print("=" * 70)
check("the scratch path is non-empty (non-degeneracy)",
      bool(inferences_path) and inferences_path.endswith(".db"), True)
check("the production path is non-empty (non-degeneracy)",
      bool(_PRODUCTION_INFERENCES_PATH), True)
check("omitting db_path resolves to the PRODUCTION database",
      os.path.abspath(_PACKAGE_DEFAULT_DB),
      os.path.abspath(_PRODUCTION_INFERENCES_PATH))
check("...which is NOT this test's scratch database, so passing db_path is "
      "doing real work and the check above can fail",
      os.path.abspath(_PACKAGE_DEFAULT_DB) == os.path.abspath(inferences_path),
      False)
check("...and passing db_path resolves to exactly what was passed",
      resolve_inference_db_path(inferences_path), inferences_path)


# ===========================================================================
# FIXTURES
# ===========================================================================

PATIENT_DATA = {
    "patient_id": "logging-contract-patient",
    "demographics": {"age": 62, "sex": "male", "race": "white",
                     "ethnicity": "not hispanic or latino"},
    "conditions": [
        {
            "code": "254637007",
            "display": "Non-small cell lung cancer",
            "verification_status": "confirmed",
        }
    ],
    "medications": [],
    "allergies": [],
    "observations": [],
    "cancer_stage_observations": [],
}


# --- Stub retrieval corpus -------------------------------------------------
#
# Overlapping channels, and one NCT ID repeated inside a single channel. The
# sparse union is 8 distinct trials while the three sparse channels return 13
# points between them, so a count that summed the channels, counted raw points,
# or reported the request size cannot pass.

_TITLE_HITS      = ["NCT001", "NCT002", "NCT003", "NCT004", "NCT005", "NCT001"]
_CONDITIONS_HITS = ["NCT003", "NCT004", "NCT005", "NCT006", "NCT007"]
_CRITERIA_HITS   = ["NCT001", "NCT008"]
_DENSE_HITS      = ["NCT005", "NCT006", "NCT009", "NCT010", "NCT011", "NCT012"]

EXPECTED_BM25_UNIQUE = 8    # NCT001..NCT008
EXPECTED_VECTOR      = 6    # NCT005, NCT006, NCT009..NCT012


class _StubVector:
    """Sparse embedding shaped like FastEmbed's output object."""

    class _Arr:
        def __init__(self, values):
            self._values = values

        def tolist(self):
            return list(self._values)

    def __init__(self):
        self.indices = self._Arr([1, 2, 3])
        self.values = self._Arr([0.5, 0.4, 0.3])


class StubBM25QueryModel:
    """Stands in for the FastEmbed SparseTextEmbedding query model."""

    def __init__(self):
        self.calls = 0

    def query_embed(self, text):
        self.calls += 1
        yield _StubVector()


class _StubPoint:
    def __init__(self, nct_id):
        self.payload = {
            "nct_id": nct_id,
            "full_trial_json": {
                "nct_id": nct_id,
                "title": f"trial {nct_id}",
                "phase": "PHASE2",
            },
        }


class _StubResponse:
    def __init__(self, nct_ids):
        self.points = [_StubPoint(n) for n in nct_ids]


class StubQdrantClient:
    """Serves the four Stage 2 channels from the fixture corpus.

    Sparse calls are identified by the `using` kwarg (the vector name); the
    dense call has none. `fail_dense` makes the dense channel raise, which is
    the production fallback path — Stage 2 catches it per channel and continues
    with whatever the others returned.
    """

    def __init__(self, fail_dense=False):
        self.fail_dense = fail_dense
        self.sparse_calls = []
        self.dense_calls = 0

    def query_points(self, collection_name=None, query=None, using=None,
                     limit=None, with_payload=True, **kwargs):
        if using is None:
            self.dense_calls += 1
            if self.fail_dense:
                raise RuntimeError("stub dense channel unavailable")
            return _StubResponse(_DENSE_HITS)

        self.sparse_calls.append(using)
        if using == "title-bm25":
            return _StubResponse(_TITLE_HITS)
        if using == "conditions-bm25":
            return _StubResponse(_CONDITIONS_HITS)
        if using == "criteria-bm25":
            return _StubResponse(_CRITERIA_HITS)
        raise AssertionError(f"unexpected sparse vector name: {using}")


def stub_get_embedding(text):
    """Dense query embedding without an OpenAI call."""
    return [0.1] * 8


class _StubEmbeddingResponse:
    def __init__(self, vector):
        self.data = [type("Datum", (), {"embedding": vector})()]


class StubOpenAIClient:
    """The shape get_embedding() actually uses: .embeddings.create(...).

    PASS 20c-2c REPLACED A REBINDING OF get_embedding WITH THIS. Test 4 used to
    do `get_embedding = stub_get_embedding` in this namespace, which reached
    File 13's Stage 2 because every file shared one dict. The agent is a package
    now and resolves get_embedding in its own module, so that rebinding would
    have redirected nothing and the dense channel would have called the REAL
    OpenAI embedding endpoint -- billed, on a test that reports it made no
    network call.

    Stubbing the CLIENT rather than adding a second override key for the
    function is deliberate: it is the same seam Stage 5 and Files 45/46 use, so
    this test exercises the path production takes instead of a bypass built for
    it.
    """

    def __init__(self):
        self.embedding_calls = 0
        self.embeddings = self

    def create(self, model=None, input=None, timeout=None, **kwargs):
        self.embedding_calls += 1
        return _StubEmbeddingResponse(stub_get_embedding(input))


def make_stage2_state(ablation_flags=None) -> dict:
    """Input state for node_hybrid_retrieval."""
    return {
        "patient_data":   PATIENT_DATA,
        "expanded_query": "lung neoplasms carcinoma non-small-cell",
        "rerank_queries": ["lung neoplasms"],
        "stage_timings":  {},
        "ablation_flags": ablation_flags or {},
    }


def make_terminal_state(**overrides) -> dict:
    """State as LangGraph hands it to a terminal node, with every stage key
    present. Overrides stand in for whatever the run actually produced."""
    state = {
        "patient_data":                    PATIENT_DATA,
        "expanded_query":                  "lung neoplasms",
        "hybrid_results":                  [],
        "bm25_retrieved":                  0,
        "vector_retrieved":                0,
        "reranked_trials":                 [],
        "filtered_trials":                 [],
        "candidates_after_rule_filter":    0,
        "candidates_after_quality_filter": 0,
        "mesh_dropped":                    0,
        "mesh_resolution":                 "snomed_cui_mesh",
        "stage_dropped":                   0,
        "histology_dropped":               0,
        "evaluations":                     [],
        "llm_classifier_retries":                   0,
        # Stage 5 completed and its out-of-set detector found nothing. Present
        # for the same reason cross_vocab_remaps is: this fixture stands for a
        # state a terminal node is handed after a COMPLETE run, and Stage 5
        # writes this key on its success return. A test that wants the "Stage 5
        # never ran" shape omits it -- see Test 6.
        "hallucinated_trials":             0,
        "cross_vocab_remaps":              0,
        "llm_classifier_prompt":                    "",
        "llm_classifier_input_tokens":              0,
        "llm_classifier_output_tokens":             0,
        "expansion_prompt":                "",
        "expansion_input_tokens":          0,
        "expansion_output_tokens":         0,
        "stage_timings":                   {"query_expansion": 0.01},
        "error":                           "",
        "ablation_flags":                  {},
    }
    state.update(overrides)
    return state


TERMINAL_NODES = {
    "node_finalize":      node_finalize,
    "node_no_candidates": node_no_candidates,
    "node_error_handler": node_error_handler,
}

# Keys legitimately unique to one terminal path. Everything else must be
# declared by all three.
ALLOWED_EXTRA_KEYS = {
    "node_finalize":      set(),
    "node_no_candidates": {"message"},
    "node_error_handler": {"llm_classifier_retries_exhausted"},
}


print("\n" + "=" * 70)
print("LOGGING CONTRACT TEST")
print("=" * 70)


# ===========================================================================
# TEST 1: STRUCTURAL — the three terminal results declare the same keys
# ===========================================================================
# The defect was a key on one terminal path only: File 14 read it, the other
# two paths never wrote it, and the column was constant for every row those
# paths produced. This test does not know about llm_classifier_retries specifically —
# it fails for any key added to one terminal node and not the others.

print("\n" + "=" * 70)
print("Test 1: no terminal node declares a key the others omit")
print("=" * 70)

_state = make_terminal_state()
_terminal_keys = {
    name: set(fn(_state)["result"].keys())
    for name, fn in TERMINAL_NODES.items()
}

_core = set.intersection(*_terminal_keys.values())

for _name, _keys in _terminal_keys.items():
    check(f"{_name}: declares no key outside the shared contract",
          sorted(_keys - _core - ALLOWED_EXTRA_KEYS[_name]), [])
    check(f"{_name}: omits no key from the shared contract",
          sorted(_core - _keys), [])

# The four columns this item is about must be in the shared set, not in any
# node's extras.
for _key in ("llm_classifier_retries", "ablation_flags", "bm25_retrieved", "vector_retrieved"):
    check(f"{_key} is declared by all three terminal nodes", _key in _core, True)


# ===========================================================================
# TEST 2: STRUCTURAL — Stage 5 reports its retry count on every path
# ===========================================================================
# A retry count that reaches Stage 6 only on the success return would log 0 for
# exactly the runs that retried the most.

print("\n" + "=" * 70)
print("Test 2: every node_llm_classifier_evaluation return carries llm_classifier_retries")
print("=" * 70)

# RETARGETED IN PASS 20c-2c, and this one COULD have gone silently green.
# node_llm_classifier_evaluation moved to oncotriage/agent/evaluation.py, and
# "13- LangGraph Agent.py" is now a re-export shim with no function definitions
# in it. The walk below would have found nothing, left _llm_classifier_returns EMPTY, and
# then:
#     len([]) >= 2                                  -> False -> that check FAILS
#     sorted(ln for ln, keys in [] if ...) == []    -> True  -> this one PASSES
# So one of the two would have gone quietly green on a file it had not read.
# That is the same shape as the File 36/14 defect pass 2b found, and it is why
# the non-degeneracy check below is not optional.
# PASS 20d-1: the path comes from the imported module's own __file__ rather than
# from os.path.join(_code_dir, ...). A directory guess was correct only while
# this file sat beside the package; from tests/ it would have been one level off
# and the open() below would have raised. It also cannot name a copy of the
# module this process did not import.
_EVALUATION_SOURCE = os.path.abspath(_agent_evaluation.__file__)
with open(_EVALUATION_SOURCE, encoding="utf-8") as _fh:
    _tree = ast.parse(_fh.read())

_llm_classifier_returns = []
for _fn in ast.walk(_tree):
    if isinstance(_fn, ast.FunctionDef) and _fn.name == "node_llm_classifier_evaluation":
        for _node in ast.walk(_fn):
            if isinstance(_node, ast.Return) and isinstance(_node.value, ast.Dict):
                _llm_classifier_returns.append((
                    _node.lineno,
                    {k.value for k in _node.value.keys
                     if isinstance(k, ast.Constant) and isinstance(k.value, str)},
                ))

# NON-DEGENERATE FIRST. "no return omits llm_classifier_retries" is vacuously true for a
# function that was never found, which is exactly what a stale filename gives.
check("the parsed source actually defines node_llm_classifier_evaluation",
      any(isinstance(n, ast.FunctionDef) and n.name == "node_llm_classifier_evaluation"
          for n in ast.walk(_tree)), True)
check("found node_llm_classifier_evaluation's dict returns", len(_llm_classifier_returns) >= 2, True)
check("every return declares llm_classifier_retries",
      sorted(ln for ln, keys in _llm_classifier_returns if "llm_classifier_retries" not in keys),
      [])

# The logger must not reach for a retrieval constant: that was the bug --
# bm25_retrieved / vector_retrieved were inserted as the CONFIGURED request
# sizes, so both columns were constant across every row and any ratio built on
# them described the config rather than the run. Checked over the parsed tree,
# not the text, so the comment explaining the defect does not count as a use
# of it.
#
# RETARGETED IN PASS 20c-2b, and the retarget matters. This used to parse
# "14- Database Logger.py", which is now a re-export shim: forty lines of import
# statements. The check would still have PASSED against it, and would have
# proved nothing at all, forever. The definitions are in the package module and
# that is what is parsed.
_LOGGER_SOURCE = os.path.abspath(_database_logger.__file__)
with open(_LOGGER_SOURCE, encoding="utf-8") as _fh:
    _logger_tree = ast.parse(_fh.read())

# Bare Names AND attribute names. Before the package split a constant could only
# arrive as a bare Name out of the shared exec namespace, so collecting Name was
# enough. A package module reaches a tunable as `config.BM25_RETRIEVAL_SIZE`,
# which is an ast.Attribute and would have walked straight past a Name-only
# scan. Widening the scan is what keeps this check meaning the same thing after
# the move as it did before it.
_logger_names = {
    _n.id for _n in ast.walk(_logger_tree) if isinstance(_n, ast.Name)
} | {
    _n.attr for _n in ast.walk(_logger_tree) if isinstance(_n, ast.Attribute)
}

# NON-DEGENERATE FIRST. Every check below is "this name is absent", and absent
# is what an empty set says about everything. A scan that read the wrong file,
# or that collected nothing, would certify the property vacuously. So: the set
# must be substantial, and it must contain names the logger provably does use --
# including one reached as an ATTRIBUTE, which is the half of the scan the
# package split made necessary.
check("the logger source was found and parsed to a substantial name set",
      len(_logger_names) >= 50, True)
check("...and it contains a name the logger certainly reads (PRICING_CONFIG)",
      "PRICING_CONFIG" in _logger_names, True)
check("...and an ATTRIBUTE name it certainly reads (paths.inferences_path), so "
      "the attribute half of the scan is doing work",
      "inferences_path" in _logger_names, True)

for _const in ("BM25_RETRIEVAL_SIZE", "VECTOR_RETRIEVAL_SIZE"):
    check(f"the logger no longer reads {_const}", _const in _logger_names, False)

# The registry resolution that replaced File 13's _CANCER_REGISTRY global.
#
# Pass 2b made _resolve_primary_cancer call load_registry() INSIDE the logger.
# Pass 2c moved the whole function to oncotriage/registries/primary_cancer.py,
# because it is a domain question that opens no database and because the agent's
# terminal nodes call it too -- which had the AGENT depending on the STORAGE
# layer for a registry lookup. So the assertion moves with it: the logger must
# IMPORT the function, and the module that now owns it must be the one calling
# load_registry().
#
# A revert to the global would be a silent layering regression either way: the
# global is UNBOUND in any chain that loads 14 without 13, and the function
# would raise NameError rather than resolve a diagnosis. File 38 section 9b is
# the behavioural half of this check, in the one chain where that happens.
check("the logger imports _resolve_primary_cancer rather than defining it",
      "_resolve_primary_cancer" in _logger_names, True)
check("...and no longer reads File 13's _CANCER_REGISTRY global",
      "_CANCER_REGISTRY" in _logger_names, False)

_PRIMARY_CANCER_SOURCE = os.path.abspath(_primary_cancer.__file__)
with open(_PRIMARY_CANCER_SOURCE, encoding="utf-8") as _fh:
    _primary_tree = ast.parse(_fh.read())
_primary_names = (
    {_n.id for _n in ast.walk(_primary_tree) if isinstance(_n, ast.Name)}
    | {_n.attr for _n in ast.walk(_primary_tree) if isinstance(_n, ast.Attribute)}
)
check("the module that now owns it defines _resolve_primary_cancer",
      any(isinstance(_n, ast.FunctionDef) and _n.name == "_resolve_primary_cancer"
          for _n in ast.walk(_primary_tree)), True)
check("...and resolves the registry through load_registry()",
      "load_registry" in _primary_names, True)
check("...and reads no _CANCER_REGISTRY global either",
      "_CANCER_REGISTRY" in _primary_names, False)


# ===========================================================================
# TEST 3: retry count and ablation flags survive every terminal node
# ===========================================================================

print("\n" + "=" * 70)
print("Test 3: retries and ablation flags reach the result dict")
print("=" * 70)

FLAGS = {"retrieval_mode": "bm25_only", "skip_mesh_filter": True}

for _name, _fn in TERMINAL_NODES.items():
    _out = _fn(make_terminal_state(
        llm_classifier_retries=2,
        ablation_flags=FLAGS,
        error="GPT-4o JSON parse error (attempt 3)" if _name == "node_error_handler" else "",
    ))["result"]

    check(f"{_name}: logs the retries actually spent", _out["llm_classifier_retries"], 2)
    check(f"{_name}: logs the ablation flags of the run",
          _out["ablation_flags"], FLAGS)

# The flags are copied, not aliased: mutating the state's dict afterwards must
# not rewrite an already-produced result.
_flags_live = {"retrieval_mode": "vector_only"}
_res = node_finalize(make_terminal_state(ablation_flags=_flags_live))["result"]
_flags_live["retrieval_mode"] = "mutated_after_the_fact"
check("ablation_flags is snapshotted, not aliased",
      _res["ablation_flags"], {"retrieval_mode": "vector_only"})

# Full pipeline: an empty flag dict, not a missing key.
_res_full = node_finalize(make_terminal_state())["result"]
check("full pipeline logs an empty flag dict", _res_full["ablation_flags"], {})
check("no-retry run logs 0 retries", _res_full["llm_classifier_retries"], 0)


# ===========================================================================
# TEST 4: Stage 2 reports observed per-channel counts
# ===========================================================================

print("\n" + "=" * 70)
print("Test 4: bm25_retrieved / vector_retrieved are observations")
print("=" * 70)

# INSTALLED THROUGH THE DEPENDENCY SEAM (pass 20c-2c), not by rebinding.
#
# These three lines used to be
#
#     qdrant_client = StubQdrantClient()
#     _bm25_query_model = StubBM25QueryModel()
#     get_embedding = stub_get_embedding
#
# and they worked because every project file was exec'd into this namespace.
# File 13 is a shim over oncotriage/agent/ now, so its Stage 2 resolves all
# three in its own modules and a rebinding here reaches none of them: the test
# would have run against the REAL Qdrant collection and the REAL OpenAI
# embedding endpoint while asserting on stub call counts that stayed at zero.
_stub_qdrant = StubQdrantClient()
_stub_bm25 = StubBM25QueryModel()
_stub_openai = StubOpenAIClient()

_saved_deps = deps.set_overrides({
    deps.QDRANT_CLIENT:    _stub_qdrant,
    deps.BM25_QUERY_MODEL: _stub_bm25,
    deps.OPENAI_CLIENT:    _stub_openai,
})

# --- THE OVERRIDE IS SHOWN TO BE WHAT THE AGENT REACHES ---------------------
# Identity, not "set_overrides returned" -- the failure this replaces was a
# rebinding that also did not raise. The negative control underneath is what
# makes the identity check discriminating.
check("deps hands Stage 2 THIS stub Qdrant client",
      deps.get_qdrant_client() is _stub_qdrant, True)
check("deps hands Stage 2 THIS stub BM25 query model",
      deps.get_bm25_query_model() is _stub_bm25, True)
check("deps hands Stage 2 THIS stub OpenAI client",
      deps.get_openai_client() is _stub_openai, True)

_probe = deps.clear_override(deps.QDRANT_CLIENT)
check("...and with the override REMOVED the agent reaches something else, so "
      "the checks above can fail (negative control)",
      deps.get_qdrant_client() is _stub_qdrant, False)
deps.set_override(deps.QDRANT_CLIENT, _probe)
check("...and reinstalling it restores the stub",
      deps.get_qdrant_client() is _stub_qdrant, True)

try:
    # --- Hybrid: all four channels run --------------------------------------
    _hybrid = node_hybrid_retrieval(make_stage2_state())

    check("hybrid: sparse count is the union of the three fields, deduped",
          _hybrid["bm25_retrieved"], EXPECTED_BM25_UNIQUE)
    check("hybrid: sparse count is not the sum of the channels",
          _hybrid["bm25_retrieved"] == len(_TITLE_HITS) + len(_CONDITIONS_HITS)
          + len(_CRITERIA_HITS), False)
    check("hybrid: sparse count is not the configured request size",
          _hybrid["bm25_retrieved"] in (BM25_RETRIEVAL_SIZE, 3 * BM25_RETRIEVAL_SIZE),
          False)
    check("hybrid: dense count is what the dense channel returned",
          _hybrid["vector_retrieved"], EXPECTED_VECTOR)
    check("hybrid: dense count is not the configured request size",
          _hybrid["vector_retrieved"] == VECTOR_RETRIEVAL_SIZE, False)
    check("hybrid: all three sparse fields were queried",
          sorted(_stub_qdrant.sparse_calls),
          ["conditions-bm25", "criteria-bm25", "title-bm25"])
    check("hybrid: the dense channel went through the stub OpenAI client",
          _stub_openai.embedding_calls, 1)

    # --- bm25_only: the dense channel never runs ----------------------------
    _stub_qdrant = StubQdrantClient()
    deps.set_override(deps.QDRANT_CLIENT, _stub_qdrant)
    _bm25_only = node_hybrid_retrieval(
        make_stage2_state({"retrieval_mode": "bm25_only"})
    )
    check("bm25_only: dense channel was never called", _stub_qdrant.dense_calls, 0)
    check("bm25_only: dense count is 0, not the request size",
          _bm25_only["vector_retrieved"], 0)
    check("bm25_only: sparse count still observed",
          _bm25_only["bm25_retrieved"], EXPECTED_BM25_UNIQUE)

    # --- vector_only: the sparse channels never run -------------------------
    _stub_qdrant = StubQdrantClient()
    deps.set_override(deps.QDRANT_CLIENT, _stub_qdrant)
    _vector_only = node_hybrid_retrieval(
        make_stage2_state({"retrieval_mode": "vector_only"})
    )
    check("vector_only: sparse channels were never called",
          _stub_qdrant.sparse_calls, [])
    check("vector_only: sparse count is 0, not the request size",
          _vector_only["bm25_retrieved"], 0)
    check("vector_only: dense count still observed",
          _vector_only["vector_retrieved"], EXPECTED_VECTOR)

    # --- Dense channel fails: the fallback is visible in the counts ---------
    _stub_qdrant = StubQdrantClient(fail_dense=True)
    deps.set_override(deps.QDRANT_CLIENT, _stub_qdrant)
    _fallback = node_hybrid_retrieval(make_stage2_state())
    check("dense failure: dense count is 0", _fallback["vector_retrieved"], 0)
    check("dense failure: sparse count is unaffected",
          _fallback["bm25_retrieved"], EXPECTED_BM25_UNIQUE)

finally:
    deps.restore_overrides(_saved_deps)

# The counts survive Stage 2 -> Stage 6.
_result_counts = node_finalize(make_terminal_state(
    bm25_retrieved=_hybrid["bm25_retrieved"],
    vector_retrieved=_hybrid["vector_retrieved"],
))["result"]
check("Stage 2 counts reach the result dict",
      (_result_counts["bm25_retrieved"], _result_counts["vector_retrieved"]),
      (EXPECTED_BM25_UNIQUE, EXPECTED_VECTOR))


# ===========================================================================
# TEST 5: END TO END — the row in the database matches the run
# ===========================================================================

print("\n" + "=" * 70)
print("Test 5: log_inference writes the run, not the config")
print("=" * 70)

_logged = node_finalize(make_terminal_state(
    llm_classifier_retries=2,
    ablation_flags=FLAGS,
    bm25_retrieved=EXPECTED_BM25_UNIQUE,
    vector_retrieved=EXPECTED_VECTOR,
))["result"]

check_wrote_to_scratch(
    "log_inference wrote to the scratch database, not production",
    log_inference(_logged, PATIENT_DATA, db_path=inferences_path))

_conn = sqlite3.connect(inferences_path)
_conn.row_factory = sqlite3.Row
_row = _conn.execute(
    "SELECT * FROM inferences WHERE patient_id = ? ORDER BY id DESC LIMIT 1",
    (PATIENT_DATA["patient_id"],),
).fetchone()

check("a row was written", _row is not None, True)

if _row is not None:
    check("llm_classifier_retries is the count spent", _row["llm_classifier_retries"], 2)
    check("ablation_flags records the configuration",
          json.loads(_row["ablation_flags"]), FLAGS)
    check("bm25_retrieved is the observed count",
          _row["bm25_retrieved"], EXPECTED_BM25_UNIQUE)
    check("vector_retrieved is the observed count",
          _row["vector_retrieved"], EXPECTED_VECTOR)
    check("bm25_retrieved is not the config constant",
          _row["bm25_retrieved"] == BM25_RETRIEVAL_SIZE, False)
    check("vector_retrieved is not the config constant",
          _row["vector_retrieved"] == VECTOR_RETRIEVAL_SIZE, False)

    # ── TEST 6: the hallucinated-trial columns separate 0 from NULL ─────────
    #
    # THIS TEST USED TO ASSERT NULL ON A CLEAN RUN, and that assertion was true
    # only because no detector existed: it was a record of an absent feature,
    # not a property of the writer. Stage 5 now compares every returned entry
    # against the candidate set it sent and writes the count, so the truth to
    # assert is the DISCRIMINATION -- 0 where the check ran, NULL where it did
    # not -- which is the mesh_resolution precedent and the one thing a single
    # default (either default) could not deliver.
    #
    # The two arms are each other's control. If _pipeline_provenance stopped
    # carrying the key, or the INSERT stopped reading it, the 0 arm fails; if
    # either of them defaulted the absent value to 0, the NULL arms fail. No
    # source is patched: each arm creates its own condition for real.
    print("\n" + "=" * 70)
    print("Test 6: hallucinated_trials is 0 when checked, NULL when not")
    print("=" * 70)

    check("inferences.hallucinated_trials exists",
          "hallucinated_trials" in _row.keys(), True)
    check("a completed run through node_finalize stores the measured count",
          _row["hallucinated_trials"], 0)
    check("...and it is a stored 0, not a NULL that compares equal to nothing",
          _row["hallucinated_trials"] is None, False)

# --- NULL arm 1: a terminal path on which Stage 5 never ran ----------------
# node_no_candidates ends the run before the model is called, so nothing was
# ever compared against a candidate set. The key is simply absent from state --
# exactly the shape a production no-candidates run produces -- and the terminal
# node must not invent a value for it. (Test 1 already proves all three
# terminal nodes DECLARE the key; this is about what they declare it AS.)
_never_ran = make_terminal_state()
del _never_ran["hallucinated_trials"]
_never_ran_result = node_no_candidates(_never_ran)["result"]
_never_ran_result["patient_id"] = "stage5-never-ran"
check("a run where Stage 5 never ran reports no count rather than 0",
      _never_ran_result["hallucinated_trials"], None)
check_wrote_to_scratch(
    "the never-ran result was written to the scratch database",
    log_inference(_never_ran_result, PATIENT_DATA, db_path=inferences_path))

# --- NULL arm 2: a result dict that never passed through a terminal node ---
_hand_built = {
    "patient_id": "hand-built-no-terminal-node",
    "timestamp": "2026-08-09T00:00:00",
    "matches": [], "near_misses": [], "not_evaluable": [],
    "stage_timings": {}, "error": "",
}
check_wrote_to_scratch(
    "a hand-built result was written to the scratch database",
    log_inference(_hand_built, PATIENT_DATA, db_path=inferences_path))

_conn2 = sqlite3.connect(inferences_path)
_conn2.row_factory = sqlite3.Row
for _pid, _label in (("stage5-never-ran",
                      "a terminal path where Stage 5 never ran"),
                     ("hand-built-no-terminal-node",
                      "a result dict that never met a terminal node")):
    _null_row = _conn2.execute(
        "SELECT hallucinated_trials FROM inferences WHERE patient_id = ? "
        "ORDER BY id DESC LIMIT 1", (_pid,)).fetchone()
    check(f"{_label}: a row was written", _null_row is not None, True)
    check(f"{_label} stores NULL",
          _null_row["hallucinated_trials"] if _null_row else "<no row>", None)
_conn2.close()

# --- The per-trial marker, both values, through the real INSERT ------------
# One evaluation carrying the flag Stage 5 stamps, one without it. The pair is
# written by one call, so a writer that defaulted or dropped the field cannot
# satisfy both rows.
_per_trial = node_finalize(make_terminal_state(
    evaluations=[
        {"nct_id": "NCT00000001", "eligible": "eligible", "match_score": 1.0,
         "hallucinated": 0},
        {"nct_id": "NCT00000002", "eligible": "eligible", "match_score": 0.5},
    ],
))["result"]
_per_trial["patient_id"] = "per-trial-hallucinated-marker"
check_wrote_to_scratch(
    "the per-trial result was written to the scratch database",
    log_inference(_per_trial, PATIENT_DATA, db_path=inferences_path))

_conn3 = sqlite3.connect(inferences_path)
_conn3.row_factory = sqlite3.Row
_marks = dict(_conn3.execute(
    "SELECT nct_id, hallucinated FROM trial_matches WHERE inference_id = "
    "(SELECT id FROM inferences WHERE patient_id = ? ORDER BY id DESC LIMIT 1)",
    ("per-trial-hallucinated-marker",)).fetchall())
check("an evaluation Stage 5 stamped stores 0 (checked, in the candidate set)",
      _marks.get("NCT00000001"), 0)
check("an evaluation carrying no marker stores NULL (never checked)",
      _marks.get("NCT00000002"), None)
check("non-degeneracy: both rows were written, so the pair discriminates",
      sorted(_marks), ["NCT00000001", "NCT00000002"])
_conn3.close()

_trial_columns = {
    r[1] for r in _conn.execute("PRAGMA table_info(trial_matches)")
}
check("trial_matches.hallucinated exists", "hallucinated" in _trial_columns, True)


# ── TEST 6b: degraded_run separates 0 from NULL, the same way ──────────────
#
# THE NEW KEY, ASSERTED BY THIS FILE'S CONVENTION AND NOT MORE. Test 1 above
# already proves all three terminal nodes DECLARE it -- it rides in
# _pipeline_provenance, so a key on one path only is structurally impossible --
# and tests/test_agent_degraded_run_and_reporting.py owns the derivation matrix
# (clean state, each contributing observation alone, the never-reached arm).
# What belongs HERE is the thing this file is about: that the column exists and
# that the writer's three states reach it.
#
# The two rows below were already written above for Test 6 and are re-read for
# a different column, which is deliberate: a second pair of log_inference calls
# would be a second chance for one of them to write a different result dict.
print("\n" + "=" * 70)
print("Test 6b: degraded_run is 0/1 from a terminal node, NULL without one")
print("=" * 70)

check("inferences.degraded_run exists", "degraded_run" in _row.keys(), True)
check("a clean run through node_finalize stores 0",
      _row["degraded_run"], 0)
check("...and it is a stored 0, not a NULL that compares equal to nothing",
      _row["degraded_run"] is None, False)

_conn4 = sqlite3.connect(inferences_path)
_conn4.row_factory = sqlite3.Row
_degraded_null = _conn4.execute(
    "SELECT degraded_run FROM inferences WHERE patient_id = ? "
    "ORDER BY id DESC LIMIT 1", ("hand-built-no-terminal-node",)).fetchone()
check("a result dict that never met a terminal node stores NULL",
      _degraded_null["degraded_run"] if _degraded_null else "<no row>", None)

# The 1 arm, through the real INSERT rather than through the derivation alone:
# a Stage 5 failure is what node_error_handler is reached by, and `error` being
# non-empty is the first term of the predicate.
_degraded_result = node_error_handler(make_terminal_state(
    error="planted Stage 5 failure",
))["result"]
_degraded_result["patient_id"] = "degraded-run-error-path"
check_wrote_to_scratch(
    "the degraded result was written to the scratch database",
    log_inference(_degraded_result, PATIENT_DATA, db_path=inferences_path))
_degraded_row = _conn4.execute(
    "SELECT degraded_run FROM inferences WHERE patient_id = ? "
    "ORDER BY id DESC LIMIT 1", ("degraded-run-error-path",)).fetchone()
check("a run that ended at the error handler stores 1",
      _degraded_row["degraded_run"] if _degraded_row else "<no row>", 1)
# NON-DEGENERACY: without this the three arms above could all be reading one
# constant. They are three different values from one column in one database.
check("the three arms are three distinct stored values (non-degeneracy)",
      sorted({0, None, 1} - {_row["degraded_run"],
                             _degraded_null["degraded_run"],
                             _degraded_row["degraded_run"]}, key=str),
      [])
_conn4.close()

_conn.close()


# ===========================================================================
# TEST 7: reasoning tokens are inside the output figure, never added to it
# ===========================================================================
#
# On a reasoning model, usage.completion_tokens_details.reasoning_tokens is a
# SUBSET of usage.completion_tokens -- billed at the output rate, already
# inside the number File 13 stores as llm_classifier_output_tokens. Adding the two would
# bill every reasoning token twice.
#
# WHY THIS TEST IS SHAPED THE WAY IT IS. Item 29a's check for the same property
# passed vacuously: it ran at the shipped configuration, where
# MATCHING_REASONING_EFFORT is 'none' and reasoning_tokens is therefore 0, so
# "cost with reasoning added" equalled "cost without" for the wrong reason. An
# assertion that cannot distinguish the two states is not an assertion.
#
# So this test does not run the pipeline. It feeds log_inference a result whose
# reasoning count is NON-ZERO and whose correct and double-billed costs are
# arithmetically different, and it asserts that difference is real BEFORE
# asserting which side of it the stored value landed on. If a later edit sets
# the reasoning figure to zero, the discrimination check below fails rather
# than the cost check passing for free.

print("\n" + "=" * 70)
print("Test 7: reasoning tokens are not added to the output figure for costing")
print("=" * 70)

# Priced against MATCHING_MODEL rather than a literal, so the test also fails
# if the configured judge is ever absent from PRICING_CONFIG -- which is the
# condition get_model_cost() exists to refuse.
COST_MODEL     = MATCHING_MODEL
COST_INPUT     = 10_000
COST_OUTPUT    = 8_000
COST_REASONING = 3_000     # a SUBSET of COST_OUTPUT, not an addition to it

_cost_correct = get_model_cost(COST_MODEL, COST_INPUT, COST_OUTPUT)
_cost_double  = get_model_cost(COST_MODEL, COST_INPUT, COST_OUTPUT + COST_REASONING)

# --- anti-vacuity guards, asserted first -----------------------------------
# Without these two, every assertion below could pass on a run where reasoning
# was zero and the two costs coincided.
check("the reasoning figure under test is non-zero", COST_REASONING > 0, True)
check("correct and double-billed costs are distinguishable",
      _cost_correct != _cost_double, True)
check("reasoning tokens are a subset of output tokens, as the API reports them",
      COST_REASONING < COST_OUTPUT, True)

_reasoning_result = node_finalize(make_terminal_state(
    llm_classifier_input_tokens=COST_INPUT,
    llm_classifier_output_tokens=COST_OUTPUT,
))["result"]
_reasoning_result["patient_id"]             = "reasoning-cost-patient"
_reasoning_result["matching_model"]         = COST_MODEL
_reasoning_result["llm_classifier_input_tokens"]     = COST_INPUT
_reasoning_result["llm_classifier_output_tokens"]    = COST_OUTPUT
_reasoning_result["llm_classifier_reasoning_tokens"] = COST_REASONING

check_wrote_to_scratch(
    "the reasoning-cost row also went to the scratch database",
    log_inference(_reasoning_result, PATIENT_DATA, db_path=inferences_path))

_conn = sqlite3.connect(inferences_path)
_conn.row_factory = sqlite3.Row
_row = _conn.execute(
    "SELECT * FROM inferences WHERE patient_id = ? ORDER BY id DESC LIMIT 1",
    ("reasoning-cost-patient",),
).fetchone()

check("a row was written for the reasoning case", _row is not None, True)

if _row is not None:
    # The reasoning count must survive the round trip, or the cost assertion
    # below would be testing a value the writer never saw.
    check("llm_classifier_reasoning_tokens round-trips into the row",
          _row["llm_classifier_reasoning_tokens"], COST_REASONING)
    check("llm_classifier_output_tokens is stored as reported, reasoning included in it",
          _row["llm_classifier_output_tokens"], COST_OUTPUT)

    check("estimated_cost_usd is priced on input and output alone",
          _row["estimated_cost_usd"], _cost_correct)
    check("estimated_cost_usd is NOT the double-billed figure",
          _row["estimated_cost_usd"] == _cost_double, False)

    # The same claim stated as money, so a reader sees the size of the error
    # this test prevents rather than only that two floats differ.
    print(f"        priced ${_cost_correct:.6f}; double-billing reasoning "
          f"would have charged ${_cost_double:.6f} "
          f"(+{(_cost_double / _cost_correct - 1) * 100:.1f}%)")

    check("matching_model on the row is the model that was priced",
          _row["matching_model"], COST_MODEL)

_conn.close()


# ===========================================================================
# CLEANUP + SUMMARY
# ===========================================================================

shutil.rmtree(_TMP_DIR, ignore_errors=True)

print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")

if _FAILURES:
    print("\nFailures:")
    for _f in _FAILURES:
        print(textwrap.indent(f"  - {_f}", ""))

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Aug  2 2026

@author: ramyalsaffar
"""
