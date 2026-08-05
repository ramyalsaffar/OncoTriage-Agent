# Logging Contract Test
#######################

"""
Logging Contract Test

Audits the path from a pipeline state to a row in inferences.db for the defect
class where a column is written from something other than what the pipeline
observed, so the column is constant and every consumer of it reports the
configuration instead of the run.

The three defects under test:

  1. gpt4o_retries — File 14 read result["gpt4o_retries_exhausted"], a key only
     node_error_handler wrote. Every successful inference logged 0 retries no
     matter how many were spent, so File 20's gpt4o_retry_rate_z_score watched
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
    2. STRUCTURAL — every dict return in node_gpt4o_evaluation carries
       gpt4o_retries, so the count reaches Stage 6 on the success path and on
       each failure path.
    3. Retry count and ablation flags survive all three terminal nodes.
    4. Stage 2 reports OBSERVED per-channel counts: sparse counts are the union
       of the three field queries (not their sum, not the request size), an
       ablated channel reports 0, and a channel whose query raises reports 0.
    5. END TO END — log_inference() writes a row whose gpt4o_retries,
       ablation_flags, bm25_retrieved and vector_retrieved match the run rather
       than the config, into a throwaway database.
    6. The hallucinated-trial columns exist and default to NULL (not measured),
       so item 33's detector has somewhere to write.

No network and no LLM: Qdrant, the sparse query model and the embedding call
are replaced with stubs, and no terminal node calls a model. The database is a
temporary file; the real inferences.db is never opened — File 14 is exec'd
after inferences_path is repointed, so it is not listed in the chain below.

Run from terminal (or F5 in Spyder):
    python "36- Logging Contract Test.py"

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# Item 20a: this file sits in the code directory, so __file__ locates it with
# no hardcoded path. __file__ is bound when the file is run as a script (every
# documented entry point for it) and when Spyder runfile()s it. In a bare
# interactive paste it is not bound, and the working directory is the only
# remaining candidate -- taken, but announced, never silently.
import os as _os_boot
if "__file__" in globals():
    _code_dir = _os_boot.path.dirname(_os_boot.path.abspath(__file__)) + _os_boot.sep
else:
    _code_dir = _os_boot.getcwd() + _os_boot.sep
    print(f"[Bootstrap] __file__ unbound; using the working directory as the code directory: {_code_dir}")
del _os_boot

for _bootstrap in ("01- Imports.py", "02- Utility Functions.py"):
    with open(_code_dir + _bootstrap) as _fh:
        exec(_fh.read(), globals())

# 13 chains 03, 08, 09, 10 itself — do not list them again here.
# 14 is deliberately NOT chained: it connects at load time, and this test
# repoints inferences_path at a temporary file first (see below).
exec_chain(
    ["13- LangGraph Agent.py"],
    caller_file=_code_dir + "36- Logging Contract Test.py",
    caller_globals=globals(),
    chain_label="01 → 02 → 13 (→ 03, 08, 09, 10)",
)


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
# inferences_path is rebound BEFORE File 14 is exec'd, because File 14 opens
# its connection and creates its tables at load time. The real inferences.db is
# never touched by this test.

_TMP_DIR = tempfile.mkdtemp(prefix="oncotriage_logging_contract_")
inferences_path = os.path.join(_TMP_DIR, "inferences_test.db")

with open(_code_dir + "14- Database Logger.py") as _fh:
    exec(_fh.read(), globals())


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
        "gpt4o_retries":                   0,
        "cross_vocab_remaps":              0,
        "gpt4o_prompt":                    "",
        "gpt4o_input_tokens":              0,
        "gpt4o_output_tokens":             0,
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
    "node_error_handler": {"gpt4o_retries_exhausted"},
}


print("\n" + "=" * 70)
print("LOGGING CONTRACT TEST")
print("=" * 70)


# ===========================================================================
# TEST 1: STRUCTURAL — the three terminal results declare the same keys
# ===========================================================================
# The defect was a key on one terminal path only: File 14 read it, the other
# two paths never wrote it, and the column was constant for every row those
# paths produced. This test does not know about gpt4o_retries specifically —
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
for _key in ("gpt4o_retries", "ablation_flags", "bm25_retrieved", "vector_retrieved"):
    check(f"{_key} is declared by all three terminal nodes", _key in _core, True)


# ===========================================================================
# TEST 2: STRUCTURAL — Stage 5 reports its retry count on every path
# ===========================================================================
# A retry count that reaches Stage 6 only on the success return would log 0 for
# exactly the runs that retried the most.

print("\n" + "=" * 70)
print("Test 2: every node_gpt4o_evaluation return carries gpt4o_retries")
print("=" * 70)

with open(_code_dir + "13- LangGraph Agent.py") as _fh:
    _tree = ast.parse(_fh.read())

_gpt4o_returns = []
for _fn in ast.walk(_tree):
    if isinstance(_fn, ast.FunctionDef) and _fn.name == "node_gpt4o_evaluation":
        for _node in ast.walk(_fn):
            if isinstance(_node, ast.Return) and isinstance(_node.value, ast.Dict):
                _gpt4o_returns.append((
                    _node.lineno,
                    {k.value for k in _node.value.keys
                     if isinstance(k, ast.Constant) and isinstance(k.value, str)},
                ))

check("found node_gpt4o_evaluation's dict returns", len(_gpt4o_returns) >= 2, True)
check("every return declares gpt4o_retries",
      sorted(ln for ln, keys in _gpt4o_returns if "gpt4o_retries" not in keys),
      [])

# File 14 must not reach for a retrieval constant: that was the bug. Checked
# over the parsed tree, not the text, so the comment explaining the defect does
# not count as a use of it.
with open(_code_dir + "14- Database Logger.py") as _fh:
    _logger_tree = ast.parse(_fh.read())

_logger_names = {
    _n.id for _n in ast.walk(_logger_tree) if isinstance(_n, ast.Name)
}
for _const in ("BM25_RETRIEVAL_SIZE", "VECTOR_RETRIEVAL_SIZE"):
    check(f"File 14 no longer reads {_const}", _const in _logger_names, False)


# ===========================================================================
# TEST 3: retry count and ablation flags survive every terminal node
# ===========================================================================

print("\n" + "=" * 70)
print("Test 3: retries and ablation flags reach the result dict")
print("=" * 70)

FLAGS = {"retrieval_mode": "bm25_only", "skip_mesh_filter": True}

for _name, _fn in TERMINAL_NODES.items():
    _out = _fn(make_terminal_state(
        gpt4o_retries=2,
        ablation_flags=FLAGS,
        error="GPT-4o JSON parse error (attempt 3)" if _name == "node_error_handler" else "",
    ))["result"]

    check(f"{_name}: logs the retries actually spent", _out["gpt4o_retries"], 2)
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
check("no-retry run logs 0 retries", _res_full["gpt4o_retries"], 0)


# ===========================================================================
# TEST 4: Stage 2 reports observed per-channel counts
# ===========================================================================

print("\n" + "=" * 70)
print("Test 4: bm25_retrieved / vector_retrieved are observations")
print("=" * 70)

_real_qdrant = qdrant_client
_real_bm25_model = _bm25_query_model
_real_get_embedding = get_embedding

qdrant_client = StubQdrantClient()
_bm25_query_model = StubBM25QueryModel()
get_embedding = stub_get_embedding

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
          sorted(qdrant_client.sparse_calls),
          ["conditions-bm25", "criteria-bm25", "title-bm25"])

    # --- bm25_only: the dense channel never runs ----------------------------
    qdrant_client = StubQdrantClient()
    _bm25_only = node_hybrid_retrieval(
        make_stage2_state({"retrieval_mode": "bm25_only"})
    )
    check("bm25_only: dense channel was never called", qdrant_client.dense_calls, 0)
    check("bm25_only: dense count is 0, not the request size",
          _bm25_only["vector_retrieved"], 0)
    check("bm25_only: sparse count still observed",
          _bm25_only["bm25_retrieved"], EXPECTED_BM25_UNIQUE)

    # --- vector_only: the sparse channels never run -------------------------
    qdrant_client = StubQdrantClient()
    _vector_only = node_hybrid_retrieval(
        make_stage2_state({"retrieval_mode": "vector_only"})
    )
    check("vector_only: sparse channels were never called",
          qdrant_client.sparse_calls, [])
    check("vector_only: sparse count is 0, not the request size",
          _vector_only["bm25_retrieved"], 0)
    check("vector_only: dense count still observed",
          _vector_only["vector_retrieved"], EXPECTED_VECTOR)

    # --- Dense channel fails: the fallback is visible in the counts ---------
    qdrant_client = StubQdrantClient(fail_dense=True)
    _fallback = node_hybrid_retrieval(make_stage2_state())
    check("dense failure: dense count is 0", _fallback["vector_retrieved"], 0)
    check("dense failure: sparse count is unaffected",
          _fallback["bm25_retrieved"], EXPECTED_BM25_UNIQUE)

finally:
    qdrant_client = _real_qdrant
    _bm25_query_model = _real_bm25_model
    get_embedding = _real_get_embedding

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
    gpt4o_retries=2,
    ablation_flags=FLAGS,
    bm25_retrieved=EXPECTED_BM25_UNIQUE,
    vector_retrieved=EXPECTED_VECTOR,
))["result"]

log_inference(_logged, PATIENT_DATA)

_conn = sqlite3.connect(inferences_path)
_conn.row_factory = sqlite3.Row
_row = _conn.execute(
    "SELECT * FROM inferences WHERE patient_id = ? ORDER BY id DESC LIMIT 1",
    (PATIENT_DATA["patient_id"],),
).fetchone()

check("a row was written", _row is not None, True)

if _row is not None:
    check("gpt4o_retries is the count spent", _row["gpt4o_retries"], 2)
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

    # ── TEST 6: hallucinated-trial columns exist and read as "not measured" ──
    print("\n" + "=" * 70)
    print("Test 6: hallucinated-trial marker exists and defaults to NULL")
    print("=" * 70)

    check("inferences.hallucinated_trials exists",
          "hallucinated_trials" in _row.keys(), True)
    check("no detector ran, so the count is NULL not 0",
          _row["hallucinated_trials"], None)

_trial_columns = {
    r[1] for r in _conn.execute("PRAGMA table_info(trial_matches)")
}
check("trial_matches.hallucinated exists", "hallucinated" in _trial_columns, True)
_conn.close()


# ===========================================================================
# TEST 7: reasoning tokens are inside the output figure, never added to it
# ===========================================================================
#
# On a reasoning model, usage.completion_tokens_details.reasoning_tokens is a
# SUBSET of usage.completion_tokens -- billed at the output rate, already
# inside the number File 13 stores as gpt4o_output_tokens. Adding the two would
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
    gpt4o_input_tokens=COST_INPUT,
    gpt4o_output_tokens=COST_OUTPUT,
))["result"]
_reasoning_result["patient_id"]             = "reasoning-cost-patient"
_reasoning_result["matching_model"]         = COST_MODEL
_reasoning_result["gpt4o_input_tokens"]     = COST_INPUT
_reasoning_result["gpt4o_output_tokens"]    = COST_OUTPUT
_reasoning_result["gpt4o_reasoning_tokens"] = COST_REASONING

log_inference(_reasoning_result, PATIENT_DATA)

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
    check("gpt4o_reasoning_tokens round-trips into the row",
          _row["gpt4o_reasoning_tokens"], COST_REASONING)
    check("gpt4o_output_tokens is stored as reported, reasoning included in it",
          _row["gpt4o_output_tokens"], COST_OUTPUT)

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
