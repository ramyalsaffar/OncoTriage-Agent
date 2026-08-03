# Retrieval Observability Test
##############################

"""
Retrieval Observability Test

Audits the three places where the pipeline could run on less than it was built
to run on and leave no trace: Stage 2's four retrieval channels, Stage 1's MeSH
expansion fallback, and Stage 5's claim that disease relevance was already
confirmed.

The defects under test:

  1. Each of Stage 2's four retrieval channels sat behind its own try that
     caught, printed and yielded an empty list. Fusion then continued on the
     survivors with nothing written to state or the database, so a dense-search
     outage produced the same stored row as a clean run. bm25_retrieved and
     vector_retrieved could not stand in: 0 means both "returned nothing" and
     "never returned", and the three sparse fields collapse into one union
     count in which a single failed field is invisible.

  2. node_query_expansion printed a WARNING and fell back to the base query
     when MeSH resolution produced no terms. The rate at which the pipeline
     searched with no MeSH vocabulary at all was therefore unknown.

  3. Section 2 of the Stage 5 system prompt asserted that disease relevance
     "has already been confirmed" and then forbade the model from assessing
     relevance. Stage 4's cancer site filter only runs when the MeSH files
     loaded AND the patient resolved to specific C04 trees, so in the
     missing-data-file and pan-cancer-resolution cases the model was told a
     check passed that never ran, and was blocked from noticing.

THE EMPTY SPARSE VECTOR QUESTION, ANSWERED

Stage 2 passed the FastEmbed sparse embedding straight into a SparseVector with
no empty check. Two things were unproven; both were measured before any guard
was written, and both are re-measured here.

  Q1. Can the query text tokenize to nothing?
      YES. Section A drives the real Qdrant/bm25 model: empty string,
      whitespace, punctuation-only and stopword-only text all return zero
      indices, because the tokenizer lowercases, strips punctuation and drops
      stopwords. Section B then shows the pipeline can produce such a text:
      is_primary_cancer() matches on code alone, so a condition with a
      recognized code and no display text is a primary cancer whose display is
      "", and on the MeSH-fallback path Stage 1 sets every rerank query to that
      display. disease_query — which feeds title-bm25 and conditions-bm25 —
      is then "". The expanded query keeps its literal ", solid tumor, solid
      neoplasm" suffix, so criteria-bm25 and the dense channel are unaffected:
      the exposure is two of four channels, not all four.

  Q2. Does Qdrant reject an empty sparse vector, or return no results?
      IT RETURNS NO RESULTS. Measured against a real Qdrant server v1.18.3
      with three IDF sparse fields shaped like the production index: an empty
      SparseVector is accepted and yields zero points, indistinguishable from a
      well-formed query that matched nothing. No exception, no 4xx.

      So the failure mode was never a crash. It was two channels reporting a
      clean zero. The guard added is therefore an observability guard, not a
      crash guard: _sparse_query raises _EmptySparseQuery before the network
      call so the channel is recorded as CHANNEL_EMPTY_QUERY rather than as a
      successful search that found nothing. Section E re-runs Q2 against a live
      server when one is reachable and reports what it found either way.

Covers:
    A. TOKENIZATION — the real BM25 query model returns zero indices for
       degenerate text and non-zero for realistic queries.
    B. REACHABILITY — a patient record the registry accepts drives
       node_query_expansion onto the fallback path with an empty disease query,
       and the expansion path is recorded rather than only printed.
    C. NO WASTED CALL — an empty query text never reaches Qdrant.
    D. CHANNEL OUTCOMES — every channel is recorded on every run: ok, failed,
       ablated, empty_query. Ablated channels never count as degradation;
       failures and empty queries do. Fusion still returns trials when a
       channel drops out.
    E. LIVE QDRANT PROBE — Q2 above, against a real server if one is reachable.
    F. STAGE 4 — mesh_filter_applied and its reason for all four cases.
    G. SYSTEM PROMPT — Section 2 asserts confirmation only when the filter ran,
       and lifts the prohibition on relevance reasoning when it did not.
    H. END TO END — the whole record reaches a row in inferences.db, with NULL
       (not a clean value) for any stage that did not report.

No LLM and no production database. Qdrant, the embedding call and the OpenAI
client are replaced with stubs; the BM25 query model is the real one because
Section A is a measurement of it. The database is a temporary file — File 14 is
exec'd after inferences_path is repointed, so it is not listed in the chain.

Section E is the only part that touches the network, it is read-only, and it
reports SKIPPED rather than failing when no server is reachable. Point it at a
local server with:
    ONCOTRIAGE_QDRANT_PROBE_URL=http://localhost:6333 python "37- ..."

Run from terminal (or F5 in Spyder):
    python "37- Retrieval Observability Test.py"

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
_code_dir = "/Users/ramyalsaffar/Ramy/C.V..V/07- LLM Projects/03- Clinical Trial Patient Match/03- Code/"

for _bootstrap in ("01- Imports.py", "02- Utility Functions.py"):
    with open(_code_dir + _bootstrap) as _fh:
        exec(_fh.read(), globals())

# 13 chains 03, 08, 09, 10 itself — do not list them again here.
# 14 is deliberately NOT chained: it connects at load time, and this test
# repoints inferences_path at a temporary file first (see below).
exec_chain(
    ["13- LangGraph Agent.py"],
    caller_file=_code_dir + "37- Retrieval Observability Test.py",
    caller_globals=globals(),
    chain_label="01 → 02 → 13 (→ 03, 08, 09, 10)",
)


#------------------------------------------------------------------------------


import contextlib
import tempfile


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


def check_true(label: str, actual) -> None:
    check(label, bool(actual), True)


# ===========================================================================
# THROWAWAY DATABASE
# ===========================================================================
# inferences_path is rebound BEFORE File 14 is exec'd, because File 14 opens
# its connection and creates its tables at load time. The real inferences.db is
# never touched by this test.

_TMP_DIR = tempfile.mkdtemp(prefix="oncotriage_retrieval_observability_")
inferences_path = os.path.join(_TMP_DIR, "inferences_test.db")

with open(_code_dir + "14- Database Logger.py") as _fh:
    exec(_fh.read(), globals())


# ===========================================================================
# FIXTURES
# ===========================================================================

# A patient the cancer registry resolves normally.
PATIENT_DATA = {
    "patient_id": "retrieval-observability-patient",
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
    "procedures": [],
}

# The same patient with the display text missing. is_primary_cancer() matches
# on the SNOMED code alone, so this is still a primary cancer — and its display,
# which Stage 1 uses as the rerank query on the fallback path, is empty.
PATIENT_NO_DISPLAY = {
    **PATIENT_DATA,
    "patient_id": "retrieval-observability-no-display",
    "conditions": [
        {"code": "254637007", "display": "", "verification_status": "confirmed"}
    ],
}


def make_trial(nct_id: str, title: str = "A Study of Something") -> dict:
    """Minimal trial payload with every field the nodes under test read."""
    return {
        "nct_id": nct_id,
        "title": title,
        "phase": "PHASE2",
        "conditions": ["Lung Neoplasms"],
        "eligibility": {
            "min_age": "18 Years",
            "max_age": "99 Years",
            "sex": "ALL",
            "inclusion_criteria": "Histologically confirmed non-small cell lung cancer.",
            "exclusion_criteria": "Active brain metastases.",
        },
    }


TRIALS = [make_trial(f"NCT{i:08d}") for i in range(1, 6)]


# ===========================================================================
# STUBS
# ===========================================================================

class _StubPoint:
    def __init__(self, trial):
        self.payload = {"nct_id": trial["nct_id"], "full_trial_json": trial}


class _StubPoints:
    def __init__(self, points):
        self.points = points


class StubQdrant:
    """Stand-in for qdrant_client.

    fail_channels: channel names whose query_points call should raise.
    Records every call so a test can assert a channel never reached the wire.
    """

    # using= names map to the channel names Stage 2 records under.
    _USING_TO_CHANNEL = {
        "title-bm25": "title",
        "conditions-bm25": "conditions",
        "criteria-bm25": "criteria",
    }

    def __init__(self, fail_channels=()):
        self.fail_channels = set(fail_channels)
        self.calls = []

    def query_points(self, collection_name, query, limit, with_payload,
                     using=None, **kwargs):
        channel = self._USING_TO_CHANNEL.get(using, "dense")
        self.calls.append(channel)
        if channel in self.fail_channels:
            raise RuntimeError(f"stubbed {channel} outage")
        return _StubPoints([_StubPoint(t) for t in TRIALS])

    def scroll(self, *args, **kwargs):
        raise AssertionError("scroll should not be reached: payloads are inline")


class _StubMessage:
    def __init__(self, content):
        self.content = content


class _StubChoice:
    def __init__(self, content):
        self.message = _StubMessage(content)


class _StubUsage:
    prompt_tokens = 1000
    completion_tokens = 200


class _StubResponse:
    def __init__(self, content):
        self.choices = [_StubChoice(content)]
        self.usage = _StubUsage()


class StubOpenAI:
    """Captures the messages Stage 5 sends and returns a parseable evaluation."""

    def __init__(self, evaluations):
        self._payload = json.dumps(evaluations)
        self.captured_messages = None
        self.chat = self
        self.completions = self

    def create(self, model, messages, **kwargs):
        self.captured_messages = messages
        return _StubResponse(self._payload)


@contextlib.contextmanager
def swap_globals(**overrides):
    """Temporarily rebind module-level names in the shared exec namespace."""
    _sentinel = object()
    previous = {k: globals().get(k, _sentinel) for k in overrides}
    globals().update(overrides)
    try:
        yield
    finally:
        for k, v in previous.items():
            if v is _sentinel:
                globals().pop(k, None)
            else:
                globals()[k] = v


def sparse_token_count(text: str) -> int:
    """Number of BM25 terms the real query model produces for text."""
    return len(next(_bm25_query_model.query_embed(text)).indices)


def run_retrieval(expanded_query, rerank_queries, qdrant, ablation_flags=None):
    """Invoke Stage 2 against a stubbed Qdrant and a stubbed embedding call."""
    with swap_globals(qdrant_client=qdrant,
                      get_embedding=lambda text: [0.1] * 8):
        return node_hybrid_retrieval({
            "patient_data": PATIENT_DATA,
            "expanded_query": expanded_query,
            "rerank_queries": rerank_queries,
            "ablation_flags": ablation_flags or {},
            "stage_timings": {},
        })


# ===========================================================================
# SECTION A -- CAN A BM25 QUERY TOKENIZE TO NOTHING?
# ===========================================================================
# Measured against the real FastEmbed Qdrant/bm25 model that Stage 2 uses at
# query time. This is the first half of the question the fix was gated on.

print("\n" + "=" * 75)
print("SECTION A -- BM25 tokenization of degenerate query text")
print("=" * 75)

check("empty string yields no BM25 terms", sparse_token_count(""), 0)
check("whitespace-only yields no BM25 terms", sparse_token_count("   \t  "), 0)
check("punctuation-only yields no BM25 terms", sparse_token_count("... --- ,,,"), 0)
check("stopwords-only yields no BM25 terms",
      sparse_token_count("the of and a an is to"), 0)

check_true("MeSH descriptor yields BM25 terms",
           sparse_token_count("Lung Neoplasms") > 0)
check_true("full expanded query yields BM25 terms",
           sparse_token_count(
               "62 year old male patient with Non-small cell lung cancer, "
               "solid tumor, solid neoplasm") > 0)


# ===========================================================================
# SECTION B -- CAN THE PIPELINE PRODUCE SUCH A QUERY?
# ===========================================================================
# Section A shows the tokenizer can return nothing. This shows Stage 1 can hand
# it a text that does. _MESH_FILTER is None here, which is a real production
# configuration (the MeSH data files are optional and load_mesh_filter()
# returns None when they are absent), not a contrivance for the test.

print("\n" + "=" * 75)
print("SECTION B -- Stage 1 fallback produces an empty disease query")
print("=" * 75)

with swap_globals(_MESH_FILTER=None):
    expansion_ok = node_query_expansion({"patient_data": PATIENT_DATA,
                                         "stage_timings": {}})
    expansion_empty = node_query_expansion({"patient_data": PATIENT_NO_DISPLAY,
                                            "stage_timings": {}})

check("no MeSH filter takes the fallback path",
      expansion_ok["query_expansion_path"], EXPANSION_PATH_FALLBACK)
check("fallback path is recorded for the display-less patient",
      expansion_empty["query_expansion_path"], EXPANSION_PATH_FALLBACK)

# disease_query is rerank_queries[0]; Stage 2 feeds it to title and conditions.
check("display-less patient produces an empty disease query",
      expansion_empty["rerank_queries"][0], "")
check("that disease query carries no BM25 terms",
      sparse_token_count(expansion_empty["rerank_queries"][0]), 0)

# The expanded query keeps its "solid tumor, solid neoplasm" suffix, so the
# criteria and dense channels are never exposed to this.
check_true("expanded query still carries BM25 terms",
           sparse_token_count(expansion_empty["expanded_query"]) > 0)

# With the MeSH filter loaded and a resolvable patient, Stage 1 reports the
# expanded path — the field varies with the run rather than being constant.
expansion_mesh = node_query_expansion({"patient_data": PATIENT_DATA,
                                       "stage_timings": {}})
check("resolvable patient with MeSH loaded reports the expanded path",
      expansion_mesh["query_expansion_path"], EXPANSION_PATH_MESH)


# ===========================================================================
# SECTION C -- AN EMPTY QUERY NEVER REACHES QDRANT
# ===========================================================================
# Qdrant would accept it and return nothing (Section E), so the guard exists to
# name the outcome, not to prevent an error. It still fires before the call:
# there is nothing to ask.

print("\n" + "=" * 75)
print("SECTION C -- empty query text is not sent to Qdrant")
print("=" * 75)

stub = StubQdrant()
out = run_retrieval(expanded_query="62 year old male patient with cancer, "
                                   "solid tumor, solid neoplasm",
                    rerank_queries=["", "", ""],
                    qdrant=stub)

check("only the criteria and dense channels reached Qdrant",
      sorted(stub.calls), ["criteria", "dense"])
check("title never reached the wire", "title" in stub.calls, False)
check("conditions never reached the wire", "conditions" in stub.calls, False)


# ===========================================================================
# SECTION D -- PER-CHANNEL OUTCOMES REACH STATE
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION D -- Stage 2 records every channel on every run")
print("=" * 75)

# --- D1: clean hybrid run ---
clean = run_retrieval("lung neoplasms query", ["Lung Neoplasms"] * 3,
                      StubQdrant())
check("clean run: every channel present",
      sorted(clean["retrieval_channels"]), sorted(RETRIEVAL_CHANNELS))
check("clean run: every channel ok",
      {c["status"] for c in clean["retrieval_channels"].values()}, {CHANNEL_OK})
check("clean run: 4 channels expected", clean["retrieval_channels_expected"], 4)
check("clean run: 4 channels ok", clean["retrieval_channels_ok"], 4)
check("clean run: not degraded", clean["retrieval_degraded"], 0)
check("clean run: no trials lost", clean["retrieval_trials_lost"], 0)
check("clean run: counts are what the channel returned",
      clean["retrieval_channels"]["title"]["count"], len(TRIALS))

# --- D2: dense outage — the defect this item names ---
dense_down = run_retrieval("lung neoplasms query", ["Lung Neoplasms"] * 3,
                           StubQdrant(fail_channels=["dense"]))
check("dense outage: dense recorded as failed",
      dense_down["retrieval_channels"]["dense"]["status"], CHANNEL_FAILED)
check("dense outage: the error is kept",
      "stubbed dense outage" in dense_down["retrieval_channels"]["dense"]["error"],
      True)
check("dense outage: sparse channels still ok",
      {dense_down["retrieval_channels"][c]["status"]
       for c in ("title", "conditions", "criteria")}, {CHANNEL_OK})
check("dense outage: 3 of 4 expected channels returned",
      dense_down["retrieval_channels_ok"], 3)
check("dense outage: run is flagged degraded",
      dense_down["retrieval_degraded"], 1)
check_true("dense outage: fusion still produced trials",
           len(dense_down["hybrid_results"]) > 0)
check("dense outage: vector_retrieved is 0 and so is indistinguishable alone",
      dense_down["vector_retrieved"], 0)

# --- D3: one sparse field down ---
title_down = run_retrieval("lung neoplasms query", ["Lung Neoplasms"] * 3,
                           StubQdrant(fail_channels=["title"]))
check("sparse outage: title recorded as failed",
      title_down["retrieval_channels"]["title"]["status"], CHANNEL_FAILED)
check("sparse outage: run is flagged degraded",
      title_down["retrieval_degraded"], 1)
check("sparse outage: the union count alone hides it",
      title_down["bm25_retrieved"], len(TRIALS))

# --- D4: ablation is configuration, not degradation ---
bm25_only = run_retrieval("lung neoplasms query", ["Lung Neoplasms"] * 3,
                          StubQdrant(), {"retrieval_mode": "bm25_only"})
check("bm25_only: dense recorded as ablated",
      bm25_only["retrieval_channels"]["dense"]["status"], CHANNEL_ABLATED)
check("bm25_only: 3 channels expected",
      bm25_only["retrieval_channels_expected"], 3)
check("bm25_only: not degraded", bm25_only["retrieval_degraded"], 0)

vector_only = run_retrieval("lung neoplasms query", ["Lung Neoplasms"] * 3,
                            StubQdrant(), {"retrieval_mode": "vector_only"})
check("vector_only: sparse channels recorded as ablated",
      {vector_only["retrieval_channels"][c]["status"]
       for c in ("title", "conditions", "criteria")}, {CHANNEL_ABLATED})
check("vector_only: 1 channel expected",
      vector_only["retrieval_channels_expected"], 1)
check("vector_only: not degraded", vector_only["retrieval_degraded"], 0)

# --- D5: empty query is its own status, not a silent zero ---
empty_q = run_retrieval("62 year old male patient with cancer, solid tumor, "
                        "solid neoplasm", ["", "", ""], StubQdrant())
check("empty query: title recorded as empty_query",
      empty_q["retrieval_channels"]["title"]["status"], CHANNEL_EMPTY_QUERY)
check("empty query: conditions recorded as empty_query",
      empty_q["retrieval_channels"]["conditions"]["status"], CHANNEL_EMPTY_QUERY)
check("empty query: criteria unaffected",
      empty_q["retrieval_channels"]["criteria"]["status"], CHANNEL_OK)
check("empty query: run is flagged degraded", empty_q["retrieval_degraded"], 1)
check("empty query: 2 of 4 expected channels returned",
      empty_q["retrieval_channels_ok"], 2)

# --- D6: every failure mode is distinguishable from every other ---
check("the four statuses are distinct values",
      len({CHANNEL_OK, CHANNEL_FAILED, CHANNEL_ABLATED, CHANNEL_EMPTY_QUERY}), 4)


# ===========================================================================
# SECTION E -- LIVE QDRANT: EMPTY SPARSE VECTOR BEHAVIOUR
# ===========================================================================
# The second half of the gating question. Recorded finding, from a real Qdrant
# server v1.18.3 carrying three IDF sparse fields shaped like the production
# index: an empty SparseVector is ACCEPTED and returns zero points on every
# field — no exception, no 4xx. The failure mode is a silent empty result set,
# which is why the guard in _sparse_query records a status instead of
# preventing an error.
#
# Reported, never asserted: this section needs a reachable server, and a test
# that fails because a cluster is down would say nothing about the code.

print("\n" + "=" * 75)
print("SECTION E -- live Qdrant probe (informational)")
print("=" * 75)

_probe_url = os.environ.get("ONCOTRIAGE_QDRANT_PROBE_URL")
_probe_collection = os.environ.get("ONCOTRIAGE_QDRANT_PROBE_COLLECTION",
                                   COLLECTION_NAME)

try:
    _probe_client = (QdrantClient(url=_probe_url, timeout=15)
                     if _probe_url else qdrant_client)
    _probe_points = _probe_client.query_points(
        collection_name=_probe_collection,
        query=SparseVector(indices=[], values=[]),
        using="title-bm25",
        limit=5,
        with_payload=False,
    ).points
    print(f"  PROBE  empty SparseVector accepted by "
          f"{_probe_url or 'the configured server'}: "
          f"{len(_probe_points)} points returned, no exception")
    print("         -> matches the recorded finding: Qdrant does not reject it")
except Exception as _probe_error:
    print(f"  SKIP   no reachable Qdrant "
          f"({type(_probe_error).__name__}: {str(_probe_error)[:120]})")
    print("         Recorded finding stands: Qdrant v1.18.3 ACCEPTS an empty")
    print("         SparseVector and returns zero points. Re-run with")
    print("         ONCOTRIAGE_QDRANT_PROBE_URL set to reproduce.")


# ===========================================================================
# SECTION F -- DID THE CANCER SITE FILTER RUN?
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION F -- Stage 4 records whether the cancer site filter ran")
print("=" * 75)


def run_rule_filter(patient_trees, ablation_flags=None, mesh_filter=...):
    """Invoke Stage 4 with a controlled MeSH configuration."""
    scored = [
        {"trial": t, "rerank_score": 5.0, "rerank_score_raw": 5.0,
         "mesh_boost": 0.0, "mesh_boost_tier": "none"}
        for t in TRIALS
    ]
    state = {
        "patient_data": PATIENT_DATA,
        "reranked_trials": scored,
        "patient_trees": patient_trees,
        "patient_histology": set(),
        "mesh_resolution": "snomed_cui_mesh" if patient_trees else "unmapped",
        "ablation_flags": ablation_flags or {},
        "stage_timings": {},
    }
    if mesh_filter is ...:
        return node_rule_based_filter(state)
    with swap_globals(_MESH_FILTER=mesh_filter):
        return node_rule_based_filter(state)


# The real filter object, when the MeSH data files loaded. Skipped rather than
# faked when they did not: the point of this case is that the real filter ran.
if _MESH_FILTER is not None:
    applied = run_rule_filter({"C04.588.894.797.520"})
    check("filter loaded + trees resolved: applied",
          applied["mesh_filter_applied"], True)
    check("filter loaded + trees resolved: reason says applied",
          applied["mesh_filter_skip_reason"], MESH_FILTER_APPLIED)
else:
    print("  SKIP   MeSH data files not loaded — cannot exercise the applied case")

no_filter = run_rule_filter({"C04.588.894.797.520"}, mesh_filter=None)
check("MeSH files missing: not applied", no_filter["mesh_filter_applied"], False)
check("MeSH files missing: reason recorded",
      no_filter["mesh_filter_skip_reason"], MESH_FILTER_SKIP_NO_FILTER)

no_trees = run_rule_filter(set())
check("patient unresolved: not applied", no_trees["mesh_filter_applied"], False)
check("patient unresolved: reason recorded",
      no_trees["mesh_filter_skip_reason"], MESH_FILTER_SKIP_NO_TREES)

ablated = run_rule_filter({"C04.588.894.797.520"},
                          {"skip_mesh_filter": True})
check("ablation: not applied", ablated["mesh_filter_applied"], False)
check("ablation: reason recorded",
      ablated["mesh_filter_skip_reason"], MESH_FILTER_SKIP_ABLATED)

# mesh_dropped alone cannot separate these: it is 0 in three of the four cases
# and can be 0 in the fourth too.
check("mesh_dropped is 0 whether or not the filter ran",
      (no_filter["mesh_dropped"], no_trees["mesh_dropped"],
       ablated["mesh_dropped"]), (0, 0, 0))


# ===========================================================================
# SECTION G -- WHAT THE SYSTEM PROMPT ASSERTS
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION G -- Section 2 of the system prompt tracks the filter")
print("=" * 75)

_EVALUATION_PAYLOAD = [{
    "nct_id": TRIALS[0]["nct_id"],
    "eligible": "eligible",
    "match_score": 1.0,
    "explanation": "No known disqualifiers.",
    "inclusion_criteria": [{"criterion": "NSCLC", "status": "met",
                            "patient_value": "Non-small cell lung cancer"}],
    "exclusion_criteria": [],
}]


def run_evaluation(mesh_filter_applied, skip_reason):
    """Invoke Stage 5 against a stubbed model and return the stored prompt."""
    stub_openai = StubOpenAI(_EVALUATION_PAYLOAD)
    state = {
        "patient_data": PATIENT_DATA,
        "filtered_trials": [{"trial": TRIALS[0], "rerank_score": 5.0,
                             "rerank_score_raw": 5.0}],
        "gpt4o_retries": 0,
        "mesh_filter_applied": mesh_filter_applied,
        "mesh_filter_skip_reason": skip_reason,
        "stage_timings": {},
    }
    with swap_globals(openai_client=stub_openai):
        result = node_gpt4o_evaluation(state)
    system_message = stub_openai.captured_messages[0]["content"]
    return result, system_message


confirmed_result, confirmed_prompt = run_evaluation(True, MESH_FILTER_APPLIED)
unconfirmed_result, unconfirmed_prompt = run_evaluation(
    False, MESH_FILTER_SKIP_NO_TREES
)

check("filter ran: prompt asserts relevance was confirmed",
      "Disease relevance has already been confirmed" in confirmed_prompt, True)
check("filter ran: prompt forbids assessing relevance",
      "Do not assess disease relevance" in confirmed_prompt, True)

check("filter skipped: prompt does NOT assert confirmation",
      "Disease relevance has already been confirmed" in unconfirmed_prompt,
      False)
check("filter skipped: prompt says relevance is unconfirmed",
      "Disease relevance has NOT been confirmed" in unconfirmed_prompt, True)
check("filter skipped: prompt names the reason",
      MESH_FILTER_SKIP_NO_TREES in unconfirmed_prompt, True)
check("filter skipped: the prohibition is lifted",
      "Do not assess disease relevance" in unconfirmed_prompt, False)
check("filter skipped: RULE 3 is the route relevance may take",
      "RULE 3" in unconfirmed_prompt, True)

# Both variants keep the containment that stops the model rejecting a trial
# wholesale — lifting the prohibition is not a licence to disqualify.
_criterion_only = ('Do not disqualify a trial for any reason other than a '
                   'criterion-level "not_met" or "violated" classification.')
check("both variants keep the criterion-level-only rule",
      (_criterion_only in confirmed_prompt,
       _criterion_only in unconfirmed_prompt), (True, True))

# Absent state is treated as "did not run", never as "ran": the conservative
# direction, since the prompt is an assertion about the run.
_, unrecorded_prompt = run_evaluation(None, None)
check("unrecorded filter state: prompt does not assert confirmation",
      "Disease relevance has already been confirmed" in unrecorded_prompt,
      False)
check("unrecorded filter state: prompt says so",
      "unrecorded" in unrecorded_prompt, True)

# The stored prompt is the one that was sent, so the record is self-describing.
check("stored prompt carries the variant that was sent",
      "Disease relevance has NOT been confirmed"
      in unconfirmed_result["gpt4o_prompt"], True)


# ===========================================================================
# SECTION H -- THE RECORD REACHES THE DATABASE
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION H -- degradation record survives to inferences.db")
print("=" * 75)


def terminal_state(**overrides):
    """State as LangGraph hands it to a terminal node."""
    state = {
        "patient_data": PATIENT_DATA,
        "expanded_query": "lung neoplasms",
        "hybrid_results": [],
        "bm25_retrieved": 0,
        "vector_retrieved": 0,
        "reranked_trials": [],
        "filtered_trials": [],
        "candidates_after_rule_filter": 0,
        "candidates_after_quality_filter": 0,
        "mesh_dropped": 0,
        "mesh_resolution": "snomed_cui_mesh",
        "stage_dropped": 0,
        "histology_dropped": 0,
        "evaluations": [],
        "gpt4o_retries": 0,
        "cross_vocab_remaps": 0,
        "gpt4o_prompt": "",
        "gpt4o_input_tokens": 0,
        "gpt4o_output_tokens": 0,
        "expansion_prompt": "",
        "expansion_input_tokens": 0,
        "expansion_output_tokens": 0,
        "stage_timings": {"query_expansion": 0.01},
        "error": "",
        "ablation_flags": {},
    }
    state.update(overrides)
    return state


DEGRADED_KEYS = (
    "retrieval_channels", "retrieval_channels_expected",
    "retrieval_channels_ok", "retrieval_degraded", "retrieval_trials_lost",
    "query_expansion_path", "mesh_filter_applied", "mesh_filter_skip_reason",
)

_degraded_state = terminal_state(
    retrieval_channels=dense_down["retrieval_channels"],
    retrieval_channels_expected=dense_down["retrieval_channels_expected"],
    retrieval_channels_ok=dense_down["retrieval_channels_ok"],
    retrieval_degraded=dense_down["retrieval_degraded"],
    retrieval_trials_lost=2,
    query_expansion_path=EXPANSION_PATH_FALLBACK,
    mesh_filter_applied=False,
    mesh_filter_skip_reason=MESH_FILTER_SKIP_NO_TREES,
)

# All three terminal nodes must carry the record — a key that exists only on
# the happy path is the shape of the defect this file audits.
for _node_name, _node in (("node_finalize", node_finalize),
                          ("node_no_candidates", node_no_candidates),
                          ("node_error_handler", node_error_handler)):
    _result = _node(_degraded_state)["result"]
    check(f"{_node_name} carries every degradation key",
          sorted(k for k in DEGRADED_KEYS if k in _result),
          sorted(DEGRADED_KEYS))
    check(f"{_node_name} carries the degraded flag",
          _result["retrieval_degraded"], 1)
    check(f"{_node_name} carries the filter state",
          (_result["mesh_filter_applied"], _result["mesh_filter_skip_reason"]),
          (False, MESH_FILTER_SKIP_NO_TREES))


def db_rows(where=""):
    conn = sqlite3.connect(inferences_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            f"SELECT * FROM inferences {where} ORDER BY id"
        )]
    finally:
        conn.close()


degraded_result = node_finalize(_degraded_state)["result"]
degraded_result["patient_id"] = "degraded-run"
log_inference(degraded_result, PATIENT_DATA)

rows = db_rows("WHERE patient_id = 'degraded-run'")
check("degraded run wrote exactly one row", len(rows), 1)

if rows:
    row = rows[0]
    check("retrieval_degraded stored", row["retrieval_degraded"], 1)
    check("retrieval_channels_ok stored", row["retrieval_channels_ok"], 3)
    check("retrieval_channels_expected stored",
          row["retrieval_channels_expected"], 4)
    check("retrieval_trials_lost stored", row["retrieval_trials_lost"], 2)
    check("query_expansion_path stored",
          row["query_expansion_path"], EXPANSION_PATH_FALLBACK)
    check("mesh_filter_applied stored as 0",
          row["mesh_filter_applied"], 0)
    check("mesh_filter_skip_reason stored",
          row["mesh_filter_skip_reason"], MESH_FILTER_SKIP_NO_TREES)

    stored_channels = json.loads(row["retrieval_channels"])
    check("stored channel JSON names the failed channel",
          stored_channels["dense"]["status"], CHANNEL_FAILED)
    check("stored channel JSON keeps the healthy channels",
          {stored_channels[c]["status"]
           for c in ("title", "conditions", "criteria")}, {CHANNEL_OK})

# A run whose Stage 2 never reported must store NULL, not a clean value: 0
# would assert that four channels were checked and none failed.
_unreported = terminal_state()
unreported_result = node_error_handler(_unreported)["result"]
unreported_result["patient_id"] = "unreported-run"
log_inference(unreported_result, PATIENT_DATA)

rows = db_rows("WHERE patient_id = 'unreported-run'")
check("unreported run wrote exactly one row", len(rows), 1)

if rows:
    row = rows[0]
    check("unreported retrieval_degraded is NULL",
          row["retrieval_degraded"], None)
    check("unreported retrieval_channels is NULL",
          row["retrieval_channels"], None)
    check("unreported query_expansion_path is NULL",
          row["query_expansion_path"], None)
    check("unreported mesh_filter_applied is NULL",
          row["mesh_filter_applied"], None)
    check("NULL is not 0: the two runs differ in the column",
          row["retrieval_degraded"] == 0, False)

# A clean run stores 0, so "no degradation" is a recorded fact rather than an
# absence — the distinction the NULL case above depends on.
_clean_state = terminal_state(
    retrieval_channels=clean["retrieval_channels"],
    retrieval_channels_expected=clean["retrieval_channels_expected"],
    retrieval_channels_ok=clean["retrieval_channels_ok"],
    retrieval_degraded=clean["retrieval_degraded"],
    retrieval_trials_lost=clean["retrieval_trials_lost"],
    query_expansion_path=EXPANSION_PATH_MESH,
    mesh_filter_applied=True,
    mesh_filter_skip_reason=MESH_FILTER_APPLIED,
)
clean_result = node_finalize(_clean_state)["result"]
clean_result["patient_id"] = "clean-run"
log_inference(clean_result, PATIENT_DATA)

rows = db_rows("WHERE patient_id = 'clean-run'")
check("clean run wrote exactly one row", len(rows), 1)

if rows:
    row = rows[0]
    check("clean run stores degraded = 0", row["retrieval_degraded"], 0)
    check("clean run stores mesh_filter_applied = 1",
          row["mesh_filter_applied"], 1)
    check("clean run stores the expanded path",
          row["query_expansion_path"], EXPANSION_PATH_MESH)


# ===========================================================================
# SUMMARY
# ===========================================================================

print("\n" + "=" * 75)
print("SUMMARY")
print("=" * 75)
print(f"  Passed: {_RESULTS['passed']}")
print(f"  Failed: {_RESULTS['failed']}")

if _FAILURES:
    print("\nFAILURES:")
    for _f in _FAILURES:
        print(f"  - {_f}")

print(f"\nTemporary database: {inferences_path}")

if _RESULTS["failed"]:
    sys.exit(1)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Aug  2 19:40:00 2026

@author: ramyalsaffar
"""
