# LangGraph Agentic Patient-Trial Matching
##########################################

"""
LangGraph-Orchestrated Patient-Trial Matching

Uses LangGraph StateGraph to orchestrate a 6-stage hybrid matching pipeline:
Stage 1: Deterministic MeSH expansion (no LLM). Correct.
Stage 2: Hybrid Retrieval: BM25 + Vector with RRF fusion. Vector retry with fallback to BM25-only. Batch scroll for missing trials.
Stage 3: Cross-Encoder: Multi-query MedCPT cross-encoder with RRF fusion across queries. Stable argsort for determinism. 
Stage 4: Rule-Based Filter: Rule filters (MeSH site, stage, histology, age, sex) + dynamic quality threshold + cost cap.
Stage 5: GPT-4o Evaluation: GPT-4o single-call criterion-level evaluation. JSON parse retry loop. Inline normalization and score recomputation.
Stage 6: Final Ranking: Split eligible/not_eligible/not_evaluable, normalize labels, assemble output.

Graph topology: conditional edges for empty results, retry loop, error handler.

LangGraph features used:
    - TypedDict state schema flowing through every node
    - Conditional edges:
        * After retrieval: skip cross-encoder if 0 results
        * After filtering: skip GPT-4o if 0 candidates
        * After GPT-4o: retry on JSON parse failure (up to 3 attempts)
    - Error handler node: catches failures, produces clean error output
    - Stage-level timing metadata
    - Visualizable via graph.get_graph().draw_mermaid()
    
"""


#------------------------------------------------------------------------------


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

exec_chain(
    ["03- Config.py", "08- Cancer Code Registry.py", "09- MeSH Cancer Site Relevance Filter.py", "10- Structured Eligibility Extractor.py"],
    caller_file=_code_dir + "13- LangGraph Agent.py",
    caller_globals=globals(),
    chain_label="01 → 02 → 03 → 08 → 09 → 10",
)


#------------------------------------------------------------------------------


# Registry initialization
_CANCER_REGISTRY = load_registry()     # ICD-10-CM 2024 + SNOMED primary cancer detection
_LAB_REGISTRY    = load_lab_registry() # LOINC filter for oncology-relevant labs
_MESH_FILTER     = load_mesh_filter()    # MeSH C04 cancer site relevance (None if files missing)


#------------------------------------------------------------------------------


# Pre-compiled regex for BM25 tokenization (module-level for performance)
_BM25_PUNCT_PATTERN = re.compile(r"[^\w\-]")  # keep word chars + hyphens


def tokenize_for_bm25(text: str) -> List[str]:
    """Tokenize text for BM25 indexing or querying.

    Applied at BOTH index time (corpus) and query time (search) to ensure
    consistent token matching. rank_bm25 does zero preprocessing — tokens
    are compared as exact strings, so "adenocarcinoma," ≠ "adenocarcinoma".

    Processing steps:
      1. Lowercase
      2. Split on whitespace
      3. Strip non-word characters from token boundaries
         (commas, periods, colons, parentheses, etc.)
      4. Discard empty tokens and pure-numeric tokens

    Preserves:
      - Hyphenated compounds: "instability-high", "HER2-positive"
      - Alphanumeric terms: "HER2", "BRAF", "BM25"

    Discards:
      - Empty tokens from consecutive delimiters
      - Pure numbers: "79", "18" (age values that match everywhere)

    Not applied:
      - Stemming: biomedical terms lose specificity
      - Stop-word removal: BM25 IDF handles this naturally
    """
    tokens = []
    for raw_token in text.lower().split():
        # Strip punctuation from boundaries, keep internal hyphens
        cleaned = _BM25_PUNCT_PATTERN.sub("", raw_token).strip("-")
        if not cleaned:
            continue
        # Discard pure-numeric tokens (age, years, doses — match everywhere)
        if cleaned.isdigit():
            continue
        tokens.append(cleaned)
    return tokens


#------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# State Schema
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Degradation vocabularies
# ---------------------------------------------------------------------------
# Fixed label sets for the three places where the pipeline can quietly run on
# less than it was built to run on. They are names for pipeline states, not
# tunables, so they live here rather than in 03- Config.py. Every one of them
# reaches a column in inferences.db.

# --- Stage 2, per retrieval channel ---
CHANNEL_OK = "ok"                        # query returned a (possibly empty) result list
CHANNEL_FAILED = "failed"                # query raised; the channel contributed nothing
CHANNEL_ABLATED = "ablated"              # retrieval_mode deliberately excluded it
CHANNEL_EMPTY_QUERY = "empty_query"      # query text tokenized to zero BM25 terms
                                         # (see the guard in _sparse_query below)

# Channels that must be present in retrieval_channels on every run, so a
# missing key is a bug rather than a channel that "did not happen".
RETRIEVAL_CHANNELS = ("title", "conditions", "criteria", "dense")

# --- Stage 1, which query the run actually searched with ---
EXPANSION_PATH_MESH = "mesh_expanded"          # MeSH walk produced descriptors
EXPANSION_PATH_FALLBACK = "base_query_fallback"  # degraded to demographics + display

# --- Genomic variant detection (Stages 1 and 5) ---
#
# LOINC 69548-6 is the mCODE genomic variant observation. It is a fact about an
# external standard, so it is a named constant here rather than a tunable.
# 07- FHIR Parser.py routes observations carrying it OUT of patient_data
# ["observations"] and into patient_data["cancer_genomic_variants"], the same
# way it routes ECOG — which is why a scan of ["observations"] alone can never
# find one.
GENOMIC_VARIANT_LOINC = "69548-6"

# Free-text fallback for observations that carry no structured variant fields.
# Anchored on both sides so a keyword only matches a whole word: the previous
# `"gene" in display.lower()` matched "gene" inside "Generalized anxiety
# disorder 7 item (GAD-7)" on 45,186 observations across the 1,000-patient
# cohort and inside "General activity scale [PEG]" on a further 656, against
# 295 genuine matches. Every patient in the cohort had a polluted query.
#
# The boundary is "not a letter or digit" rather than \b so that punctuation
# and hyphens still delimit: "c-MET", "MSI-H" and "PD-L1 expression" all match,
# "Generalized" does not.
_VARIANT_TEXT_PATTERN = re.compile(
    r"(?<![a-z0-9])(?:genetic|variant|mutation|gene)(?![a-z0-9])"
)

# --- Stage 4, whether the cancer site filter ran ---
MESH_FILTER_APPLIED = "applied"
MESH_FILTER_SKIP_ABLATED = "ablation_skipped"    # skip_mesh_filter flag set
MESH_FILTER_SKIP_NO_FILTER = "no_mesh_filter"    # MeSH data files never loaded
MESH_FILTER_SKIP_NO_TREES = "no_patient_trees"   # patient never resolved to C04 trees


class _EmptySparseQuery(Exception):
    """A BM25 query text tokenized to zero terms, so there is nothing to search.

    Raised by node_hybrid_retrieval's _sparse_query and caught by its own
    channel collector, which records CHANNEL_EMPTY_QUERY. Kept distinct from a
    Qdrant failure because the two need different responses: a failed channel
    means the index or the network is unwell, an empty query means the patient
    record produced no searchable disease text.
    """


class TrialMatchState(TypedDict):
    """Shared state that flows through every node in the pipeline.

    Each node reads what it needs and writes its outputs.
    LangGraph passes this dict from node to node automatically.
    """
    # --- Inputs (set once at invocation) ---
    patient_data: Dict                          # Parsed FHIR patient dict

    # --- Stage 1: Query Expansion ---
    expanded_query: str                         # Patient query + medical synonyms
    expansion_prompt: str                       # Prompt sent to expansion model
    expansion_input_tokens: int                 # Input tokens for expansion
    expansion_output_tokens: int                # Output tokens from expansion

    # Short queries for cross-encoder (MedCPT-native format)
    rerank_queries: List[str]

    # How the patient's MeSH C04 identity resolved: the layer name(s), or the
    # reason none applied ("pan_cancer_only", "unmapped", ...). Written once
    # in Stage 1; Stage 3 re-resolves the same way. Logged to
    # inferences.mesh_resolution so an unresolved patient is a queryable fact
    # rather than an inference from an empty tree list.
    mesh_resolution: str

    # Which branch Stage 1 took: EXPANSION_PATH_MESH when the MeSH walk
    # produced terms, EXPANSION_PATH_FALLBACK when it produced none and the
    # query degraded to demographics + diagnosis display. mesh_resolution says
    # WHY resolution failed; this says WHAT the run then searched with, and the
    # two are not the same fact — a resolution can name a layer and still yield
    # no descriptors. Logged to inferences.query_expansion_path so the
    # fallback rate is a query rather than an unread WARNING line.
    query_expansion_path: str

    # --- Stage 2: Hybrid Retrieval ---
    hybrid_results: List[Dict]                  # Trials from BM25 + Vector + RRF

    # Observed per-channel retrieval counts, written by Stage 2 and logged to
    # inferences.bm25_retrieved / inferences.vector_retrieved. These are counts
    # of what the channel actually returned, NOT the configured request sizes
    # (BM25_RETRIEVAL_SIZE / VECTOR_RETRIEVAL_SIZE): a channel that failed, was
    # ablated away, or hit a collection smaller than its limit returns fewer.
    # Logging the constants instead would make the columns a record of the
    # configuration rather than of the run.
    bm25_retrieved: int                         # unique NCT IDs across the 3 sparse fields
    vector_retrieved: int                       # unique NCT IDs from the dense channel

    # Per-channel outcome for the four retrieval channels (title, conditions,
    # criteria, dense). Shape:
    #     {"title": {"status": CHANNEL_OK, "count": 75, "error": ""}, ...}
    # status is one of the CHANNEL_* constants below. Written by Stage 2 and
    # logged to inferences.retrieval_channels as JSON.
    #
    # bm25_retrieved / vector_retrieved cannot carry this: a dense outage and a
    # dense channel that legitimately matched nothing both report 0, and three
    # sparse channels collapse into one union count in which a single failed
    # field is invisible. Fusion continues on whatever channels returned, so
    # without this field a run on two channels is indistinguishable from a
    # clean run in every stored record.
    retrieval_channels: Dict

    # Derived scalars over retrieval_channels, so degradation is queryable
    # without parsing JSON in SQL:
    #   expected — channels the retrieval mode called for (4 hybrid, 3
    #              bm25_only, 1 vector_only); ablated channels are not expected
    #              and never count as degradation
    #   ok       — expected channels that returned a result list
    #   degraded — 1 when ok < expected, else 0
    retrieval_channels_expected: int
    retrieval_channels_ok: int
    retrieval_degraded: int

    # Trials that won a place in the fusion pool but whose payload could not be
    # recovered from Qdrant, so they never reached Stage 3. The batch-scroll
    # fallback that loses them printed a line and nothing else.
    retrieval_trials_lost: int

    # --- Stage 3: Cross-Encoder Re-Ranking ---
    reranked_trials: List[Dict]                 # Top-K after cross-encoder scoring

    # --- Stage 4: Rule-Based Filtering ---
    filtered_trials: List[Dict]                 # Trials surviving rule filters + cap
    candidates_after_rule_filter: int           # Count after rule filters (before quality threshold)
    candidates_after_quality_filter: int        # Count after quality threshold (before cap)
    mesh_dropped: int                           # Trials dropped by MeSH cancer site filter
    stage_dropped: int                          # Trials dropped by cancer stage filter
    histology_dropped: int                      # Trials dropped by histology filter

    # The remaining two per-trial drops in Stage 4, and the two pool-level cuts
    # that follow them. Every other reason the pool shrinks was already a named
    # counter; these four were not, so "reranked 40 -> filtered 9" left 31
    # trials removed for reasons that could only be guessed at, and the age and
    # sex drops in particular were bare `continue`s with nothing recorded.
    #
    # quality_threshold is the score the gate actually used, not the configured
    # floor: it is max(percentile of the surviving pool, RERANK_SCORE_THRESHOLD),
    # so the configured constant alone does not say where the cut fell.
    age_dropped: int                            # Trials dropped by the age window
    sex_dropped: int                            # Trials dropped by the sex requirement
    quality_dropped: int                        # Trials dropped by the dynamic quality gate
    quality_threshold: float                    # Unboosted score the gate cut at


    patient_trees: set                           # Resolved MeSH C04 tree numbers (Stage 3 → Stage 4)
    patient_histology: set                       # Histology tags (Stage 3 → Stage 4)

    # Whether Stage 4's cancer site filter actually ran against the candidate
    # pool, and why not when it did not (one of the MESH_FILTER_SKIP_*
    # constants). The filter is conditional on _MESH_FILTER being loaded AND
    # the patient resolving to specific C04 trees, so "mesh_dropped == 0" has
    # always meant either "checked, nothing to drop" or "never checked".
    #
    # Stage 5 reads mesh_filter_applied to decide whether its system prompt may
    # assert that disease relevance was confirmed. Both are logged.
    mesh_filter_applied: bool
    mesh_filter_skip_reason: str

    # --- Stage 5: GPT-4o Evaluation ---
    evaluations: List[Dict]                     # Criterion-level match results
    gpt4o_retries: int                          # Current retry count for GPT-4o

    # Truncation control (Stage 5). A SEPARATE budget from gpt4o_retries: that
    # one counts whole-node retries for a malformed or failed response, this
    # counts levels of halving spent because a response was cut off at
    # MATCHING_MAX_TOKENS. A patient that hits one parse failure and then needs
    # two splits must not be failed for exhausting a shared counter.
    gpt4o_truncation_splits: int
    # The pre-call estimate, logged beside the actual so the calibration in
    # 03- Config.py can be re-derived from measured data rather than re-guessed.
    gpt4o_output_tokens_estimated: int
    # Trials that entered Stage 5 and left it with no verdict because of
    # truncation (the floor, or the split budget). Distinct from
    # not_evaluable_trials, which counts trials the model assessed and could
    # not conclude on.
    not_evaluable_truncated: int
    # How many model calls this stage actually made. 1 unsplit; more when a
    # batch was split. Without it a chunked run is indistinguishable from an
    # unsplit one in the token columns.
    gpt4o_calls: int
    gpt4o_raw_response: str                     # Raw GPT-4o text (for retry debugging)
    gpt4o_prompt: str                           # Prompt sent to matching model
    gpt4o_input_tokens: int
    gpt4o_output_tokens: int
    # The reasoning share OF gpt4o_output_tokens on a reasoning model, not an
    # amount on top of it. None when no response reported the breakdown; see
    # _pipeline_provenance() for why that is not 0.
    gpt4o_reasoning_tokens: Optional[int]
    # The model string the API answered with (response.model), which is not
    # necessarily MATCHING_MODEL: an alias can resolve to a dated snapshot.
    # This is what File 14 logs and prices against.
    matching_model: Optional[str]
    cross_vocab_remaps: int                     # Criterion labels resolved to not_evaluable
                                                # because the model used the other arm's
                                                # vocabulary (or returned a non-object entry)

    # --- Stage 6: Final Output ---
    result: Dict                                # Complete pipeline output
    
    # --- Pipeline Metadata ---
    error: str                                  # Error message (empty = no error)
    stage_timings: Dict                         # Latency per stage (seconds)
    
    # --- Ablation Study (optional, defaults to {} = all stages active) ---
    # Controls which pipeline stages are disabled during ablation runs.
    # Keys (all default False / "hybrid" when absent):
    #   skip_mesh_filter:      bool — skip BOTH MeSH uses: the Stage 3
    #                                 relevance boost and the Stage 4 drop
    #   skip_stage_filter:     bool — skip cancer stage mismatch filter
    #   skip_histology_filter: bool — skip histology mismatch filter
    #   skip_cross_encoder:    bool — skip MedCPT cross-encoder reranking
    #   retrieval_mode:        str  — "hybrid" (default), "bm25_only", "vector_only"
    # Populated by File 25 (Ablation Study). All other callers pass {}.
    ablation_flags: Dict


# ---------------------------------------------------------------------------
# Load MedCPT Cross-Encoder re-ranker (direct transformers API)
# ---------------------------------------------------------------------------
# Using transformers directly instead of sentence-transformers CrossEncoder
# wrapper because: (1) MedCPT's official usage is via AutoModelForSequenceClassification,
# (2) the CrossEncoder wrapper applies a default sigmoid that squashes MedCPT's
# raw values range -25 to 25.

# The two local models below are loaded at exec() time and cost tens of
# seconds plus a few hundred MB. A replay harness that serves every model
# output from a recording (46- Fixture Replay.py) needs neither, and "loads no
# model" is part of what makes a replay a replay rather than a second run.
#
# Opt-in only, and never in production: the variable is read once, here, and
# the default is to load. When it is set, both names are bound to a sentinel
# that RAISES on any use rather than to None, so a caller that forgot to
# install its own stand-in fails with a sentence explaining why instead of an
# AttributeError on None thirty frames down.
#
# Environment rather than 03- Config.py deliberately. It is not a tunable —
# it selects between two ways of running the process, has to be decided before
# this file is exec'd, and a value accidentally left in the config file would
# silently disarm the pipeline for every caller.
DEFER_LOCAL_MODELS_ENV = "ONCOTRIAGE_DEFER_LOCAL_MODELS"
_DEFER_LOCAL_MODELS = os.environ.get(DEFER_LOCAL_MODELS_ENV, "0") == "1"


class _DeferredLocalModel:
    """Stand-in for a local model that was deliberately not loaded.

    Raises on attribute access and on call, naming the model and the switch
    that skipped it, so the failure is one line to diagnose.
    """

    def __init__(self, name: str):
        object.__setattr__(self, "_name", name)

    def _explode(self, how: str):
        raise RuntimeError(
            f"{self._name} was not loaded: {DEFER_LOCAL_MODELS_ENV}=1 was set "
            f"when 13- LangGraph Agent.py was exec'd, and nothing replaced the "
            f"placeholder before {how}. A replay harness must bind its own "
            f"stand-in; a production run must not set that variable."
        )

    def __getattr__(self, attr):
        self._explode(f"attribute {attr!r} was read")

    def __call__(self, *args, **kwargs):
        self._explode("it was called")


if _DEFER_LOCAL_MODELS:
    print(f"{DEFER_LOCAL_MODELS_ENV}=1 — skipping MedCPT and FastEmbed loads.")
    medcpt_tokenizer = _DeferredLocalModel("MedCPT tokenizer")
    medcpt_model = _DeferredLocalModel("MedCPT cross-encoder")
    _bm25_query_model = _DeferredLocalModel("FastEmbed BM25 query model")
else:
    print("Loading MedCPT cross-encoder re-ranker...")
    medcpt_tokenizer = AutoTokenizer.from_pretrained("ncbi/MedCPT-Cross-Encoder")
    medcpt_model = AutoModelForSequenceClassification.from_pretrained("ncbi/MedCPT-Cross-Encoder")
    medcpt_model.eval()
    print("MedCPT re-ranker loaded!\n")

    # -----------------------------------------------------------------------
    # Load BM25 Sparse Embedding Model (FastEmbed, local, no API cost)
    # -----------------------------------------------------------------------
    # Used at query time to generate sparse query vectors for Qdrant BM25
    # search. Same model used at index time (File 11) to generate document
    # sparse vectors. Loaded once, reused for every patient.
    print("Loading BM25 sparse query model (FastEmbed)...")
    _bm25_query_model = SparseTextEmbedding(model_name="Qdrant/bm25")
    print("BM25 sparse query model loaded.\n")


# ---------------------------------------------------------------------------
# Cross-encoder scoring seam
# ---------------------------------------------------------------------------

def medcpt_score_pairs(query: str, trial_texts: List[str]) -> "np.ndarray":
    """Score one query against every trial text with the MedCPT cross-encoder.

    Lifted out of node_cross_encoder_rerank unchanged so the model call is a
    named function rather than a block inside a loop. Two callers need it to
    be one: the reranking loop, and a recording harness that has to capture
    every (query, trial_texts) -> scores pair and serve them back without a
    model (45-/46- Fixture Capture/Replay).

    Returns a 1-D float array, one score per trial text, in input order.
    """
    pairs = [[query, trial_text] for trial_text in trial_texts]

    with torch.no_grad():
        encoded = medcpt_tokenizer(
            pairs,
            truncation=True,
            padding=True,
            return_tensors="pt",
            max_length=512,
        )
        return (
            medcpt_model(**encoded)
            .logits.squeeze(dim=1)
            .detach()
            .cpu()
            .numpy()
        )

# ---------------------------------------------------------------------------
# Embedding Helper (self-contained, no dependency on RAG Indexer)
# ---------------------------------------------------------------------------

def get_embedding(text: str) -> List[float]:
    """Generate embedding for text using OpenAI.

    Defined here so the agent file is fully self-contained.
    The RAG Indexer (08) has its own copy used at indexing time.
    This copy is used at inference time only.

    ONE RETRY MECHANISM, NOT TWO. This function used to carry

        @retry(reraise=True, stop=stop_after_attempt(5),
               wait=wait_exponential(multiplier=1, min=2, max=60),
               retry=retry_if_exception_type((RateLimitError,
                     InternalServerError, APIConnectionError)))

    on top of the SDK's own retries. APITimeoutError SUBCLASSES
    APIConnectionError, so a timeout was retried by both: up to 5 x 2 = 10
    attempts for a single embedding, a number nobody chose. The decorator was
    removed in item 29d and the SDK retry (OPENAI_SDK_MAX_RETRIES, File 03)
    kept, because it is the only one that can be scoped without breaking the
    fixture harness, and because it honours Retry-After on a 429 where blind
    exponential backoff cannot. The full argument and the trade-off are in File
    03's budget reconciliation.

    Attempts now: 1 + OPENAI_SDK_MAX_RETRIES = 2. Worst case ~68s.

    NOT SILENTLY LESS ROBUST. The SDK retries the same conditions this
    decorator did -- connection errors, 408/409/429, 5xx -- so what was lost is
    attempt COUNT and backoff CEILING, not coverage of a failure class. A
    persistent failure now raises out of this function after 2 attempts instead
    of 10, which reaches Stage 2's channel accounting sooner and is recorded
    there rather than absorbed by a five-minute retry storm nobody sees.

    BOUNDED, on its own budget. EMBEDDING_REQUEST_TIMEOUT_SECONDS is a separate
    constant from MATCHING_REQUEST_TIMEOUT_SECONDS on purpose: 300s is sized
    for a request that generates thousands of tokens and this one generates
    none. File 03 records how the value was arrived at, and states plainly that
    it comes from the call's SHAPE rather than from a measurement, because no
    embedding latency has ever been measured here.
    """
    response = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
        # The STRUCTURED Timeout, so an unreachable host still fails on the
        # SDK's 5s connect phase rather than waiting out the 30s read budget.
        timeout=EMBEDDING_REQUEST_TIMEOUT,
    )
    return response.data[0].embedding


#------------------------------------------------------------------------------


# MAX_VARIANT_TERMS is in 03- Config.py with the other tunables.


def extract_genomic_variant_terms(patient_data: Dict) -> Dict:
    """Collect the patient's genomic variant terms for retrieval.

    Detection is STRUCTURAL first and textual only as a fallback, because the
    text was doing all the work and doing it badly in both directions:

      - It matched too much. `"gene" in display.lower()` fired on 45,842
        non-genomic observations across the cohort ("Generalized anxiety
        disorder 7 item (GAD-7)", "General activity scale [PEG]") against 295
        genuine ones, so essentially every expanded_query and every R4 rerank
        query carried a list of GAD-7 scores and bare integers.
      - It matched too little. Not one of the four keywords appears in a mCODE
        variant display — _parse_mcode_genomic_variant (File 07) emits
        "EGFR p.Leu858Arg: Present | Somatic" — and those observations are not
        in patient_data["observations"] at all, having been routed into
        patient_data["cancer_genomic_variants"]. The structured, highest
        fidelity variant record was the one source this could never see.

    Three paths, in descending fidelity. Each is counted and returned, so a
    query built from the free-text path alone is a queryable fact rather than
    an inference:

      mcode        patient_data["cancer_genomic_variants"], every entry of
                   which carries LOINC 69548-6 by construction.
      structured   an entry in ["observations"] whose code is
                   GENOMIC_VARIANT_LOINC or which carries a non-empty
                   gene_symbol. Covers a caller that pools variants into the
                   observation list rather than using File 07's routing.
      free_text    an observation whose display matches _VARIANT_TEXT_PATTERN
                   on a word boundary.

    Returns:
        {"terms": [str, ...],           # de-duplicated, order-stable, capped
         "counts": {"mcode": int, "structured": int, "free_text": int},
         "truncated": int}              # terms dropped by MAX_VARIANT_TERMS
    """
    counts = {"mcode": 0, "structured": 0, "free_text": 0}
    terms = []
    seen = set()

    def _add(term: str, path: str) -> None:
        term = (term or "").strip()
        if not term:
            return
        counts[path] += 1
        key = term.lower()
        if key in seen:
            return
        seen.add(key)
        terms.append(term)

    def _structured_term(record: Dict) -> str:
        """Gene symbol plus HGVS notation, or the display when neither exists.

        Preferred over the display because the display carries result text
        ("Present | Somatic") that is noise in a retrieval query, while the
        gene symbol and the protein-level change are the tokens trials are
        indexed by.
        """
        gene = (record.get("gene_symbol") or "").strip()
        if not gene:
            return (record.get("display") or "").strip()
        hgvs = (record.get("hgvs_protein") or record.get("hgvs_cdna") or "").strip()
        return f"{gene} {hgvs}".strip()

    # --- Path 1: mCODE variants, already separated by File 07 ---------------
    for record in (patient_data.get("cancer_genomic_variants") or []):
        _add(_structured_term(record), "mcode")

    # --- Paths 2 and 3: the general observation pool ------------------------
    for obs in (patient_data.get("observations") or []):
        display = (obs.get("display") or "").strip()

        if obs.get("code") == GENOMIC_VARIANT_LOINC or (obs.get("gene_symbol") or "").strip():
            _add(_structured_term(obs), "structured")
            continue

        if not _VARIANT_TEXT_PATTERN.search(display.lower()):
            continue

        # Free-text shapes differ, and the old rule — prefer value, else
        # display — was right for one of them and wrong for the other:
        #
        #   "Genetic variant: BRAF (V600E)" / value "BRAF (V600E)"
        #       the display is a label and the VALUE carries the finding.
        #   "ERBB2 gene duplication [Presence] in ... by FISH" / value "Positive"
        #       the display carries the gene and the value is a result word.
        #       Preferring the value put the literal string "Positive" into the
        #       retrieval query and left ERBB2 out of it — the one genuine
        #       match in this corpus contributed nothing usable.
        #
        # A colon in the display is what separates them: it marks the label
        # form. Without one, the display is the finding.
        value = obs.get("value")
        value_str = str(value).strip() if value is not None else ""
        if ":" in display and value_str:
            cleaned = display.split(":", 1)[-1].strip()
            _add(value_str or cleaned, "free_text")
        else:
            _add(display, "free_text")

    truncated = max(0, len(terms) - MAX_VARIANT_TERMS)
    return {"terms": terms[:MAX_VARIANT_TERMS], "counts": counts,
            "truncated": truncated}


def compute_patient_hash(patient_data: Dict) -> str:
    """Compute a deterministic hash of patient data for reproducibility tracking.
    
    Captures the exact patient record state at inference time. Two inferences
    with the same hash are guaranteed to have identical input data, making
    score/eligibility differences attributable solely to GPT-4o non-determinism.
    
    Hash inputs (order-stable):
      - demographics: birth_date, sex, race, ethnicity
      - conditions: sorted by (display, onset_date)
      - medications: sorted by display
      - observations: sorted by (display, date)
      - procedures: sorted by (display, date)
      - ecog: value, date, shape and count — emitted ONLY when the bundle
        carried at least one ECOG observation (see below)
    """

    demographics = patient_data.get("demographics", {})
    conditions = patient_data.get("conditions", [])
    medications = patient_data.get("medications", [])
    observations = patient_data.get("observations", [])
    procedures = patient_data.get("procedures", [])
    ecog = patient_data.get("ecog_performance_status") or {}
    
    # Build deterministic string representation
    parts = []
    
    # Demographics (fixed order)
    # birth_date instead of age: birth_date is what the FHIR source actually
    # carries, so it is immutable across re-parses. age is derived from it
    # against DATA_SNAPSHOT_DATE (File 03) and is therefore also stable, but it
    # is a derived value — hashing the source keeps the hash independent of how
    # the derivation is configured. The reference date the age was computed
    # against is recorded separately, as age_reference_date.
    parts.append(f"birth_date={demographics.get('birth_date', '')}")
    parts.append(f"sex={demographics.get('sex', '')}")
    parts.append(f"race={demographics.get('race', '')}")
    parts.append(f"ethnicity={demographics.get('ethnicity', '')}")
    
    # Conditions (sorted for determinism)
    sorted_conds = sorted(conditions, key=lambda c: (c.get('display', ''), c.get('onset_date', '')))
    for c in sorted_conds:
        parts.append(f"cond={c.get('display', '')}|{c.get('onset_date', '')}|{c.get('clinical_status', '')}")
    
    # Medications (sorted, deduplicated by display)
    sorted_meds = sorted(set(m.get('display', '') for m in medications))
    for m in sorted_meds:
        parts.append(f"med={m}")
    
    # Observations (sorted)
    sorted_obs = sorted(observations, key=lambda o: (o.get('display', ''), o.get('date', '')))
    for o in sorted_obs:
        parts.append(f"obs={o.get('display', '')}|{o.get('value', '')}|{o.get('unit', '')}|{o.get('date', '')}")
    
    # Procedures (sorted)
    sorted_procs = sorted(procedures, key=lambda p: (p.get('display', ''), p.get('date', '')))
    for p in sorted_procs:
        parts.append(f"proc={p.get('display', '')}|{p.get('date', '')}")

    # ECOG performance status.
    #
    # It has to be in here or the docstring's promise stops holding: File 07
    # routes LOINC 89247-1 OUT of `observations` into its own field, so once
    # patients carry a score the ECOG contributes nothing to the hash above and
    # two patients differing only in performance status -- the single most
    # common gate in interventional oncology -- would hash identically.
    #
    # Emitted only when the bundle actually carried an ECOG observation. That is
    # deliberate, and it is not the same as defaulting absence to a value:
    #
    #   - A bundle with no ECOG produces no line, so its hash is byte-identical
    #     to what this function returned before ECOG existed. Every hash already
    #     logged against the current corpus -- which has no ECOG anywhere --
    #     stays comparable. An unconditional line would have invalidated all of
    #     them to record "still nothing".
    #   - observations_found, not value, is the switch. A patient whose only
    #     ECOG postdates the reference date has value None but found >= 1, and
    #     their input data genuinely differs from a patient with no observation
    #     at all, so they get a line and a different hash. Keying on value would
    #     have collapsed those two into one hash and broken the promise in the
    #     other direction.
    #
    # value_shape is deliberately NOT hashed. It records whether the source
    # bundle carried valueQuantity or valueInteger, which is a fact about
    # serialisation, not about the patient: the same score on the same date
    # produces byte-identical prompt text either way. Hashing it would make
    # running File 04's normalizer over a corpus change every ECOG patient's
    # hash while every prompt stayed the same, and the ablation study --
    # which reads a hash change as an input change -- would report a
    # difference that does not exist. Every other field here reaches the
    # prompt, so hash equality tracks prompt equality.
    if ecog.get("observations_found"):
        parts.append(
            f"ecog={ecog.get('value')}"
            f"|{ecog.get('date')}"
            f"|{ecog.get('observations_found')}"
            f"|{ecog.get('selection')}"
        )

    # Metastasis and nodal observations, for the same reason ECOG is here.
    # File 07 routes these out of `observations` into their own list, so
    # without this line the 701 observations that describe how far the disease
    # has spread would contribute nothing to the hash, and two patients
    # differing only in cM0 versus cM1 would hash identically.
    #
    # Emitted only when the list is non-empty, so a patient with no metastasis
    # observation hashes exactly as before the routing existed. Patients WITH
    # them do get a new hash: their observations moved between fields, which is
    # a real change to the parsed record and to the prompt built from it.
    metastasis = patient_data.get("cancer_metastasis_observations") or []
    for m in sorted(metastasis, key=lambda o: (o.get("display", ""),
                                               o.get("date", ""))):
        parts.append(
            f"met={m.get('display', '')}"
            f"|{m.get('value', '')}"
            f"|{m.get('unit', '')}"
            f"|{m.get('date', '')}"
            f"|{m.get('metastasis_category', '')}"
        )

    hash_input = "\n".join(parts)
    return hashlib.sha256(hash_input.encode('utf-8')).hexdigest()[:16]


#------------------------------------------------------------------------------


# ===========================================================================
# NODE FUNCTIONS
# ===========================================================================


# Resolution outcomes that never reach the MeSH filter itself, so they are
# named here rather than on MeSHCancerFilter. Everything else in the
# mesh_resolution column comes from resolve_patient_trees().
MESH_RESOLUTION_NO_FILTER = "no_mesh_filter"       # MeSH data files not loaded
MESH_RESOLUTION_NO_CONDITIONS = "no_valid_condition"  # nothing left to resolve


def _empty_mesh_resolution(reason: str) -> dict:
    """Same shape resolve_patient_trees() returns, for the pre-resolution exits."""
    return {
        "trees":               set(),
        "resolution":          reason,
        "layers":              [],
        "pan_only_layers":     [],
        "conditions_total":    0,
        "conditions_resolved": 0,
        "conditions_pan_only": 0,
        "conditions_unmapped": 0,
    }


def resolve_patient_mesh(conditions: list, cancer_registry, mesh_filter) -> dict:
    """
    The pipeline's single entry point for resolving a patient's MeSH identity.

    Stage 1 (query expansion) and Stage 3 (relevance boost, which feeds
    Stage 4's hard drop via state["patient_trees"]) both call this, so the
    patient's cancer identity — and the layer that produced it — is the same
    number in the expanded query, the boost, the filter and the log.

    Conditions marked refuted or entered-in-error are excluded. There is no
    fallback to the unfiltered list: a diagnosis the record retracted is not
    evidence of the patient's cancer, and 07- FHIR Parser.py already drops
    both statuses before the pipeline sees the bundle, so the only way this
    filter empties a non-empty list is a hand-built condition dict.

    Returns the resolve_patient_trees() dict (see 09- MeSH Cancer Site
    Relevance Filter.py), with "resolution" set to MESH_RESOLUTION_NO_FILTER
    or MESH_RESOLUTION_NO_CONDITIONS on the pre-resolution exits.
    """
    if mesh_filter is None:
        return _empty_mesh_resolution(MESH_RESOLUTION_NO_FILTER)

    valid_conditions = [
        c for c in conditions
        if (c.get("verification_status") or "unknown")
        not in cancer_registry.exclude_verification
    ]

    if not valid_conditions:
        return _empty_mesh_resolution(MESH_RESOLUTION_NO_CONDITIONS)

    return mesh_filter.resolve_patient_trees(valid_conditions, cancer_registry)


def format_mesh_resolution(diag: dict) -> str:
    """One-line summary of a resolve_patient_mesh() result, for stage logs."""
    parts = [
        f"{diag['conditions_resolved']}/{diag['conditions_total']} cancer conditions",
        f"{len(diag['trees'])} trees",
    ]
    if diag["pan_only_layers"]:
        parts.append(f"escalated past {'+'.join(diag['pan_only_layers'])}")
    if diag["conditions_pan_only"]:
        parts.append(f"{diag['conditions_pan_only']} pan-cancer-only")
    if diag["conditions_unmapped"]:
        parts.append(f"{diag['conditions_unmapped']} unmapped")
    return f"[{diag['resolution']}] " + ", ".join(parts)


def expand_query_from_mesh(conditions: list, cancer_registry, mesh_filter) -> dict:
    """
    Deterministic query expansion using MeSH C04 hierarchy.

    Replaces GPT-4o-mini query expansion with a pure lookup:
      1. Resolve patient's cancer conditions → MeSH tree numbers
      2. Walk the C04 tree: collect self + parent + sibling + child descriptors
      3. Return ordered MeSH descriptor names for downstream query building

    Tree-walking strategy (ordered by retrieval value):
      - SELF:     The patient's own MeSH descriptor(s)        — exact match
      - CHILDREN: One level deeper in the tree                — subtypes (higher specificity)
      - SIBLINGS: Same parent, different leaf                 — related cancers (lateral recall)
      - PARENT:   One level up                                — broader category (fallback recall)

    Sibling cap: max 10 siblings per tree number to prevent explosion at
    broad nodes like "Neoplasms by Site" (C04.588) which has ~15 children.

    Designed for:
      - Synthea FHIR bundles (SNOMED codes → Layer 1 UMLS crosswalk)
      - Real-world EHR data  (ICD-10 codes → Layer 2 fuzzy matching on display text)

    Args:
        conditions:      Patient's full condition list from FHIR parser.
                         May contain non-cancer conditions; those are filtered
                         internally using cancer_registry.
        cancer_registry: CancerCodeRegistry instance (_CANCER_REGISTRY).
                         Used for: is_primary_cancer(), sort_key(),
                         exclude_verification.
        mesh_filter:     MeSHCancerFilter instance (_MESH_FILTER), or None.
                         Provides: patient_mesh_trees(), tree_to_name,
                         name_to_trees.

    Returns:
        dict with keys:
          "mesh_terms"     : list[str]  — MeSH descriptor names, ordered by tree
                             proximity (self → children → siblings → parent).
                             Alphabetical within each group for determinism.
                             Empty list if resolution fails.
          "primary_mesh"   : str|None   — single best MeSH descriptor (for R1 rerank query)
          "parent_mesh"    : str|None   — parent MeSH descriptor (for R3 rerank query)
          "patient_trees"  : list[str]  — resolved C04 tree numbers, sorted for determinism.
                             Pan-cancer nodes are never among them.
          "resolution"     : str        — the layer(s) that resolved the patient
                             ("snomed", "icd10+fuzzy_synonym", ...), or one of
                             "no_mesh_filter" | "no_valid_condition" |
                             "no_cancer_condition" | "unmapped" | "pan_cancer_only".
                             Recorded in inferences.mesh_resolution.

    Edge cases handled:
      - mesh_filter is None (MeSH data files not loaded)     → no_mesh_filter
      - No cancer conditions in patient record                → no_cancer_condition
      - SNOMED code not in crosswalk (real EHR with ICD-10)  → falls to icd10, then fuzzy
      - Every layer resolves only to C04 / a depth-2 node    → pan_cancer_only,
        no terms, no query expansion (see the Stage 1 guard below)
      - Tree number resolves but not in tree_to_name          → skipped, uses what's available
      - Patient maps to multiple tree numbers                 → all trees walked
      - Self descriptor appears at multiple tree levels       → deduplicated
      - Broad parent node with 15+ siblings                  → capped at 10
      - Root-level tree (e.g., "C04" with no parent)         → cannot occur: a
        pan-cancer node is never accepted as the patient's identity
      - conditions list contains refuted/entered-in-error     → filtered out,
        with no fallback to the unfiltered list
    """
    MAX_SIBLINGS = 10

    result = {
        "mesh_terms": [],
        "primary_mesh": None,
        "parent_mesh": None,
        "patient_trees": [],
        # Overwritten by resolve_patient_mesh() on every path below; this is
        # only the value a caller would see if the filter were never consulted.
        "resolution": MESH_RESOLUTION_NO_FILTER,
    }

    # ── Resolve patient → MeSH tree numbers ───────────────────────────────
    # Delegates to resolve_patient_mesh() — the same call Stage 3 makes for
    # the relevance boost and for the trees Stage 4 filters on. This
    # guarantees the patient's cancer identity is resolved identically in
    # every stage, and that the layer that produced it is the one logged.
    #
    # It handles: mesh_filter=None, the refuted/entered-in-error filter,
    # the primary-cancer filter, the four resolution layers, and the
    # pan-cancer depth test applied to each of them.
    mesh_resolution = resolve_patient_mesh(conditions, cancer_registry, mesh_filter)
    result["resolution"] = mesh_resolution["resolution"]

    # ── Stage 1 guard: pan-cancer node is not an identity ─────────────────
    # A patient whose trees are only C04 / a depth-2 node builds
    # child_prefixes = {"C04."}, which matches every descriptor in the tree:
    # the expanded query would name every cancer type in MeSH and feed two of
    # the four fusion channels. resolve_patient_trees() already drops those,
    # so this is the second gate — it exists so a change there cannot silently
    # reopen the path, and it says so in the log when it fires.
    resolved_trees = mesh_resolution["trees"]
    patient_trees = specific_cancer_trees(resolved_trees)

    if patient_trees != resolved_trees:
        print(f"  Stage 1 MeSH guard: dropped "
              f"{len(resolved_trees) - len(patient_trees)} pan-cancer tree(s) "
              f"from the patient resolution")
        if not patient_trees:
            result["resolution"] = MeSHCancerFilter.RESOLUTION_PAN_ONLY

    if not patient_trees:
        return result

    # Sort for deterministic output (sets have arbitrary iteration order)
    patient_trees_sorted = sorted(patient_trees)

    result["patient_trees"] = patient_trees_sorted

    # ── Walk the C04 tree ─────────────────────────────────────────────────
    # Collect descriptor names at four proximity levels.

    self_names = set()       # exact descriptors for patient's tree numbers
    child_names = set()      # one level deeper (subtypes)
    sibling_names = set()    # same parent, different leaf (related cancers)
    parent_names = set()     # one level up (broader category)

    tree_to_name = mesh_filter.tree_to_name   # {tree_number: descriptor_name}

    # --- Pass 1: SELF + PARENT (direct lookups, O(1) per tree) ---
    for tree_num in patient_trees_sorted:
        # SELF
        if tree_num in tree_to_name:
            self_names.add(tree_to_name[tree_num])

        # PARENT: strip last dot-segment
        parts = tree_num.split(".")
        if len(parts) >= 2:
            parent_tree = ".".join(parts[:-1])
            if parent_tree in tree_to_name:
                parent_names.add(tree_to_name[parent_tree])

    # --- Pass 2: CHILDREN + SIBLINGS (single scan of tree_to_name) ---
    # Build prefix sets for efficient matching.
    # O(T) where T = ~2000 tree entries — single pass over the dictionary.
    child_prefixes = {tree_num + "." for tree_num in patient_trees_sorted}

    parent_prefixes = {}   # {parent_prefix_str: set_of_patient_trees_under_this_parent}
    for tree_num in patient_trees_sorted:
        parts = tree_num.split(".")
        if len(parts) >= 2:
            parent_prefix = ".".join(parts[:-1]) + "."
            if parent_prefix not in parent_prefixes:
                parent_prefixes[parent_prefix] = set()
            parent_prefixes[parent_prefix].add(tree_num)

    for candidate_tree, candidate_name in tree_to_name.items():
        # CHILDREN: candidate starts with any patient tree + "."
        for cp in child_prefixes:
            if candidate_tree.startswith(cp):
                # Only direct children (one level deeper, no grandchildren)
                remaining = candidate_tree[len(cp):]
                if "." not in remaining:
                    child_names.add(candidate_name)
                break   # matched one prefix, no need to check others

        # SIBLINGS: candidate shares a parent prefix with a patient tree,
        # but is not the patient tree itself
        for pp, pp_trees in parent_prefixes.items():
            if candidate_tree.startswith(pp):
                # Must not be one of the patient's own trees
                if candidate_tree not in patient_trees:
                    # Same depth as patient tree (sibling, not nephew/grandchild)
                    remaining = candidate_tree[len(pp):]
                    if "." not in remaining:
                        sibling_names.add(candidate_name)
                break   # matched one parent prefix, done

    # ── Deduplicate across levels ─────────────────────────────────────────
    # A descriptor can appear at multiple tree positions. Assign it to the
    # closest level only (self > child > sibling > parent).
    child_names -= self_names
    sibling_names -= self_names | child_names
    parent_names -= self_names | child_names | sibling_names

    # ── Cap siblings to prevent explosion ─────────────────────────────────
    # Broad nodes like "Digestive System Neoplasms" (C04.588.274) can have
    # 15+ sibling cancer sites. Cap to MAX_SIBLINGS most relevant.
    # Sort alphabetically for determinism, take first N.
    sibling_list = sorted(sibling_names)
    if len(sibling_list) > MAX_SIBLINGS:
        sibling_list = sibling_list[:MAX_SIBLINGS]

    # ── Build ordered term list ───────────────────────────────────────────
    # Priority: self → children → siblings → parent
    # Within each group: sorted alphabetically for determinism
    mesh_terms = (
        sorted(self_names)
        + sorted(child_names)
        + sibling_list
        + sorted(parent_names)
    )

    result["mesh_terms"] = mesh_terms
    
    if self_names:
        # Build inverse map: name -> max depth among patient's own tree numbers
        # O(P) where P = len(patient_trees_sorted), not O(T) scan of tree_to_name
        _name_to_max_depth = {}
        for t in patient_trees_sorted:
            n = tree_to_name.get(t)
            if n and n in self_names:
                depth = t.count(".") + 1
                if depth > _name_to_max_depth.get(n, 0):
                    _name_to_max_depth[n] = depth
        result["primary_mesh"] = max(
            sorted(self_names),
            key=lambda name: _name_to_max_depth.get(name, 0),
        )
    else:
        result["primary_mesh"] = None
    
    # Pick the parent with the most patient trees beneath it (most relevant)
    if parent_names:
        parent_counts = {}
        for tree_num in patient_trees_sorted:
            parts = tree_num.split(".")
            if len(parts) >= 2:
                parent_tree = ".".join(parts[:-1])
                if parent_tree in tree_to_name:
                    name = tree_to_name[parent_tree]
                    parent_counts[name] = parent_counts.get(name, 0) + 1
        result["parent_mesh"] = max(parent_counts, key=lambda k: (parent_counts[k], k))

        
    else:
        result["parent_mesh"] = None

    return result


# ===========================================================================
# REPLACEMENT node_query_expansion — paste over the existing function
# ===========================================================================


def node_query_expansion(state: dict) -> dict:
    """
    Stage 1: Deterministic query expansion via MeSH C04 hierarchy lookup.

    Replaces the previous GPT-4o-mini LLM call with a pure lookup against
    the MeSH neoplasm tree. This eliminates the primary source of pipeline
    non-determinism: LLM-generated search terms that vary across runs.

    How it works:
      1. Resolve patient cancer → MeSH tree numbers (SNOMED crosswalk or fuzzy)
      2. Walk the C04 tree: collect self + child + sibling + parent descriptors
      3. Build expanded_query from MeSH descriptor names (exact ClinicalTrials.gov vocabulary)
      4. Build rerank queries R1/R2/R3 deterministically

    Rerank query strategy:
      R1 — Primary MeSH descriptor (e.g., "Colonic Neoplasms")
           Best for: BM25 exact match against trial conditions field
      R2 — Patient's FHIR display text, capped to 8 words (e.g., "Non-small cell
           carcinoma of lung"). This IS the histological/molecular subtype when
           the EHR provides one. Best for: cross-encoder semantic matching.
      R3 — Parent MeSH descriptor (e.g., "Colorectal Neoplasms") or repeat R1
           Best for: Broader recall for related trials

    Fallback behavior:
      - If MeSH resolution fails entirely → falls back to base_query only
        (same as the previous GPT-4o-mini API-failure fallback)
      - If primary_mesh is None but mesh_terms exist → uses first mesh_term
      - If no parent exists → R3 repeats R1 (deterministic, no invention)

    Properties:
      - 100% deterministic: same patient record → same output, every time
      - Zero API cost: no LLM call, no tokens consumed
      - Near-zero latency: pure dictionary lookups + one O(T) tree scan
      - Consistent with Stages 3 and 4: same resolve_patient_mesh() call

    Outputs (unchanged contract from previous LLM-based version):
      - expanded_query:  str  — base_query + comma-separated MeSH terms
      - rerank_queries:  list — 3 short queries for MedCPT cross-encoder
      - expansion_prompt: str — diagnostic string (replaces LLM prompt)
      - expansion_input_tokens:  int — always 0 (no LLM)
      - expansion_output_tokens: int — always 0 (no LLM)
    """
    # MedCPT was trained on 2-10 word PubMed queries. Cap R2 to prevent
    # wasting the cross-encoder's 512-token budget on long EHR display texts
    # (real-world EHRs can have 20+ word diagnosis strings with staging,
    # receptor status, laterality, etc.).
    RERANK_QUERY_MAX_WORDS = 8

    start = time.time()

    patient_data = state["patient_data"]
    demographics = patient_data["demographics"]
    conditions = patient_data["conditions"]

    # ── Build base query ──────────────────────────────────────────────────
    age = demographics.get("age")
    age = age if age is not None else "unknown"
    
    sex = demographics.get("sex", "unknown")

    primary_diagnosis = "cancer"
    if conditions:
        valid = [c for c in conditions
                 if (c.get("verification_status") or "unknown")
                 not in _CANCER_REGISTRY.exclude_verification]
        if not valid:
            valid = conditions
        cancer_conditions = [c for c in valid if _CANCER_REGISTRY.is_primary_cancer(c)]
        if cancer_conditions:
            primary_condition = sorted(
                cancer_conditions, key=_CANCER_REGISTRY.sort_key
            )[0]
            primary_diagnosis = primary_condition["display"]

    # ── Extract genetic variant from observations ─────────────────────────
    # Precision oncology trials are indexed by gene/variant names (EGFR, BRAF,
    # IDH1, KIT, PIK3CA, etc.). Including the gene in the retrieval query is
    # critical for matching gene-specific trials via BM25 and vector search.
    # Structural detection, with a word-bounded text path as fallback — see
    # extract_genomic_variant_terms for what each path is and why the previous
    # substring test both over- and under-matched.
    observations = patient_data.get("observations") or []
    variant_result = extract_genomic_variant_terms(patient_data)
    gene_parts = variant_result["terms"]
    gene_string = ", ".join(gene_parts) if gene_parts else ""

    if gene_string:
        base_query = f"{age} year old {sex} patient with {primary_diagnosis}, {gene_string}"
    else:
        base_query = f"{age} year old {sex} patient with {primary_diagnosis}"
        
    # Append pan-cancer retrieval terms. "Solid tumor" and "solid neoplasm"
    # appear in basket/umbrella trial titles and conditions. Including them
    # boosts recall for pan-cancer trials that accept any solid tumor patient.
    # All TREC PM top systems included these terms.
    base_query += ", solid tumor, solid neoplasm"

    # ── Deterministic MeSH expansion ──────────────────────────────────────
    mesh_result = expand_query_from_mesh(conditions, _CANCER_REGISTRY, _MESH_FILTER)

    if mesh_result["mesh_terms"]:
        # ── SUCCESS: Build expanded_query from MeSH terms ─────────────────
        expansion_path = EXPANSION_PATH_MESH
        expanded_terms = ", ".join(mesh_result["mesh_terms"])
        expanded_query = f"{base_query}, {expanded_terms}"

        # ── Build rerank queries R1 / R2 / R3 ────────────────────────────

        # R1: Primary MeSH descriptor (exact ClinicalTrials.gov vocabulary).
        #     Falls back to first mesh_term if primary_mesh is None (edge case:
        #     tree numbers resolved via crosswalk but not found in tree_to_name,
        #     so self_names was empty but children/siblings/parents populated
        #     mesh_terms). mesh_terms[0] is guaranteed to exist here because
        #     we're inside the `if mesh_result["mesh_terms"]` branch.
        r1 = mesh_result["primary_mesh"] or mesh_result["mesh_terms"][0]

        # R2: Patient's FHIR display text — the most specific clinical
        #     description available. Contains histological/molecular subtype
        #     when the EHR provides one (e.g., "Non-small cell carcinoma of
        #     lung", "Infiltrating duct carcinoma of breast").
        #     Capped to RERANK_QUERY_MAX_WORDS words for MedCPT (trained on
        #     2-10 word PubMed queries; real-world EHRs can be 20+ words).
        #     Falls back to R1 if display is generic or missing.
        if primary_diagnosis != "cancer":
            r2_words = primary_diagnosis.split()
            r2 = " ".join(r2_words[:RERANK_QUERY_MAX_WORDS])
        else:
            r2 = r1

        # R3: Parent MeSH descriptor — broader category for recall.
        #     Falls back to R1 if no parent exists (e.g., tree is at root).
        r3 = mesh_result["parent_mesh"] if mesh_result["parent_mesh"] else r1

        rerank_queries = [r1, r2, r3]

        # R4: Genetic variant query for precision medicine matching.
        # Dedicated cross-encoder pass scores trials by gene relevance.
        # RRF fusion rewards trials matching BOTH cancer type (R1-R3)
        # and genetic variant (R4), which is the precision medicine signal.
        if gene_string:
            rerank_queries.append(gene_string)

        # Diagnostic string (replaces LLM prompt in output state)
        expansion_info = (
            f"MeSH deterministic expansion ({mesh_result['resolution']} resolution)\n"
            f"  Patient trees: {mesh_result['patient_trees']}\n"
            f"  Self: {mesh_result['primary_mesh']}\n"
            f"  Parent: {mesh_result['parent_mesh']}\n"
            f"  Total MeSH terms: {len(mesh_result['mesh_terms'])}"
        )

    else:
        # ── FALLBACK: MeSH resolution failed ──────────────────────────────
        # Same behavior as the previous GPT-4o-mini API-failure fallback.
        # Uses base_query only (demographics + primary diagnosis display).
        #
        # Recorded, not only printed: this WARNING was the sole trace of a
        # degraded query, so the rate at which the pipeline searched without
        # any MeSH expansion was unknowable from the stored records.
        expansion_path = EXPANSION_PATH_FALLBACK
        print(f"  WARNING: MeSH expansion failed (resolution={mesh_result['resolution']}). "
              f"Falling back to base query (degraded).")
        expanded_query = base_query
        
        rerank_queries = [primary_diagnosis] * 3
        if gene_string:
            rerank_queries.append(gene_string)
        
        expansion_info = (
            f"MeSH expansion FAILED — fallback to base query\n"
            f"  Resolution: {mesh_result['resolution']}\n"
            f"  Primary diagnosis display: {primary_diagnosis}"
        )

    # ── Logging (same format as previous version) ─────────────────────────
    elapsed = time.time() - start
    print(f"[Stage 1] Query expansion (MeSH deterministic): {elapsed:.2f}s")
    print(f"  Expanded query: {expanded_query[:150]}...")
    print(f"  Rerank queries ({len(rerank_queries)}):")
    for i, rq in enumerate(rerank_queries, 1):
        print(f"    R{i}: {rq}")
    print(f"  Expansion path: {expansion_path}")
    # Which detector found the variants, not just how many there were. A run
    # whose only variants came from the free-text path is searching on weaker
    # evidence than one backed by mCODE records, and the counts are the only
    # thing that distinguishes them.
    _vc = variant_result["counts"]
    if gene_parts or any(_vc.values()):
        print(f"  Genomic variants: {len(gene_parts)} term(s) "
              f"[mcode={_vc['mcode']} structured={_vc['structured']} "
              f"free_text={_vc['free_text']}]"
              + (f" — {variant_result['truncated']} dropped by "
                 f"MAX_VARIANT_TERMS={MAX_VARIANT_TERMS}"
                 if variant_result["truncated"] else ""))
    if mesh_result["mesh_terms"]:
        print(f"  MeSH resolution: {mesh_result['resolution']} | "
              f"trees: {len(mesh_result['patient_trees'])} | "
              f"terms: {len(mesh_result['mesh_terms'])}")

    return {
        "expanded_query": expanded_query,
        "rerank_queries": rerank_queries,
        "expansion_prompt": expansion_info,
        "expansion_input_tokens": 0,
        "expansion_output_tokens": 0,
        # Which branch above ran. Paired with mesh_resolution: that says why
        # the MeSH walk produced nothing, this says the query was degraded.
        "query_expansion_path": expansion_path,
        # Which layer resolved the patient's MeSH identity, or why none did.
        # Stage 3 re-resolves from the same conditions with the same helper,
        # so this one string describes the trees Stage 4 filters on too.
        "mesh_resolution": mesh_result["resolution"],
        "stage_timings": {
            **state.get("stage_timings", {}),
            "query_expansion": round(elapsed, 3),
        },
    }


def node_hybrid_retrieval(state: TrialMatchState) -> dict:
    """
    Stage 2: Multi-field BM25 sparse + dense vector hybrid retrieval.

    Replaces the previous in-memory BM25Okapi with Qdrant-native sparse
    vector BM25 search across 3 independently indexed fields:

      title-bm25:      Searched with disease query (R1).
                        Highest-weight signal. A disease name in the trial
                        title is the strongest relevance indicator.
                        Weight: 2.0x in RRF fusion.

      conditions-bm25:  Searched with disease query (R1).
                        MeSH conditions + keywords + interventions.
                        Weight: 1.5x in RRF fusion.

      criteria-bm25:    Searched with full expanded query.
                        Contains gene names, biomarkers, staging.
                        Weight: 1.0x in RRF fusion.

      dense vector:     Searched with full expanded query.
                        Semantic similarity via OpenAI embeddings.
                        Weight: 1.0x in RRF fusion.

    All 4 Qdrant queries run in parallel via ThreadPoolExecutor.
    Total latency = max(single query time), not sum.

    Field-level BM25 with weighted RRF fusion is the production-grade
    equivalent of ElasticSearch dis_max with per-field boosting. This is
    the same architecture that JULIE Lab (TREC PM 2019 #1) used.

    Ablation flags:
      retrieval_mode="hybrid"      (default) all 4 queries
      retrieval_mode="bm25_only"   3 sparse queries, no dense
      retrieval_mode="vector_only" 1 dense query, no sparse
    """
    start = time.time()

    query = state["expanded_query"]
    rerank_queries = state.get("rerank_queries", [])

    # R1 = primary MeSH descriptor or disease name (best for title/conditions)
    # Full expanded_query = disease + gene + MeSH terms (best for criteria + dense)
    disease_query = rerank_queries[0] if rerank_queries else query

    # --- Ablation: retrieval mode ---
    _ablation = state.get("ablation_flags") or {}
    _retrieval_mode = _ablation.get("retrieval_mode", "hybrid")

    # --- RRF weights per retrieval channel ---
    # Title and conditions get higher weight because disease name match
    # in these fields is the strongest relevance signal.
    # Weights are applied as multipliers on the RRF contribution.
    WEIGHT_TITLE      = 2.0
    WEIGHT_CONDITIONS  = 1.5
    WEIGHT_CRITERIA    = 1.0
    WEIGHT_DENSE       = 1.0
    RRF_K              = 60

    # ------------------------------------------------------------------
    # Helper: run a single Qdrant sparse BM25 query
    # ------------------------------------------------------------------
    def _sparse_query(sparse_vector_name: str, query_text: str, limit: int):
        """Generate sparse query vector and search Qdrant.

        Raises _EmptySparseQuery when the text carries no BM25 terms. Measured
        behaviour, not a defensive guess (see 37- Retrieval Observability
        Test.py, which reproduces both halves against real components):

          - FastEmbed Qdrant/bm25 returns zero indices for an empty string,
            whitespace, punctuation-only text and stopword-only text. It
            lowercases, strips punctuation and drops stopwords, so a query is
            not required to be empty to tokenize to nothing.
          - Qdrant does NOT reject an empty SparseVector. A real server
            (v1.18.3, three IDF sparse fields) accepts the query and returns
            zero points, exactly as a well-formed query matching nothing does.

        So the failure mode is not a crash; it is a channel that returns an
        empty list for a reason no stored record could distinguish from "this
        query legitimately matched no trial". The raise exists to give that
        outcome its own status, and it fires before the network call because
        there is nothing to ask Qdrant.
        """
        sparse_emb = next(_bm25_query_model.query_embed(query_text))
        indices = sparse_emb.indices.tolist()
        if not indices:
            raise _EmptySparseQuery(
                f"{sparse_vector_name}: query text {query_text!r} carries no "
                f"BM25 terms after tokenization"
            )
        return qdrant_client.query_points(
            collection_name=COLLECTION_NAME,
            query=SparseVector(
                indices=indices,
                values=sparse_emb.values.tolist(),
            ),
            using=sparse_vector_name,
            limit=limit,
            with_payload=True,
        ).points

    # ------------------------------------------------------------------
    # Run all retrieval channels in parallel
    # ------------------------------------------------------------------
    title_results = []
    conditions_results = []
    criteria_results = []
    vector_results = []

    # Per-channel outcome record. Every channel is present on every run: the
    # ones this retrieval mode does not call for keep CHANNEL_ABLATED, the ones
    # it submits are overwritten below with what actually happened. A channel
    # is never silently absent from the record.
    retrieval_channels = {
        name: {"status": CHANNEL_ABLATED, "count": 0, "error": ""}
        for name in RETRIEVAL_CHANNELS
    }

    futures = {}

    with ThreadPoolExecutor(max_workers=4) as executor:
        # Sparse BM25 queries (3 fields)
        if _retrieval_mode != "vector_only":
            futures["title"] = executor.submit(
                _sparse_query, "title-bm25", disease_query, BM25_RETRIEVAL_SIZE
            )
            futures["conditions"] = executor.submit(
                _sparse_query, "conditions-bm25", disease_query, BM25_RETRIEVAL_SIZE
            )
            futures["criteria"] = executor.submit(
                _sparse_query, "criteria-bm25", query, BM25_RETRIEVAL_SIZE
            )
        else:
            print("  [Ablation] BM25 sparse search SKIPPED (vector_only mode)")

        # Dense vector query
        if _retrieval_mode != "bm25_only":
            def _dense_query():
                query_embedding = get_embedding(query)
                return qdrant_client.query_points(
                    collection_name=COLLECTION_NAME,
                    query=query_embedding,
                    limit=VECTOR_RETRIEVAL_SIZE,
                    with_payload=True,
                ).points
            futures["dense"] = executor.submit(_dense_query)
        else:
            print("  [Ablation] Dense vector search SKIPPED (bm25_only mode)")

    # Collect results, recording the outcome of every channel this mode ran.
    #
    # Fusion below proceeds on whatever came back, which is the right behaviour
    # — a dense outage should still return BM25 results rather than nothing —
    # but it is only defensible if the run says it happened. The status written
    # here is what reaches inferences.retrieval_channels.
    #
    # Error text is truncated: this column is a signal that a channel dropped
    # out and which one, not a place to store a stack trace.
    _CHANNEL_ERROR_MAX_CHARS = 200

    for channel_name, future in futures.items():
        try:
            results = future.result(timeout=30)
            if channel_name == "title":
                title_results = results
            elif channel_name == "conditions":
                conditions_results = results
            elif channel_name == "criteria":
                criteria_results = results
            elif channel_name == "dense":
                vector_results = results
            retrieval_channels[channel_name] = {
                "status": CHANNEL_OK,
                "count": len(results),
                "error": "",
            }
        except _EmptySparseQuery as e:
            # The channel ran on a query with no BM25 terms. Qdrant would have
            # accepted it and returned nothing (verified against a real server
            # — see _sparse_query), so without this branch the channel would
            # report a clean zero.
            retrieval_channels[channel_name] = {
                "status": CHANNEL_EMPTY_QUERY,
                "count": 0,
                "error": str(e)[:_CHANNEL_ERROR_MAX_CHARS],
            }
            print(f"  WARNING: {channel_name} search skipped — {e}")
        except Exception as e:
            retrieval_channels[channel_name] = {
                "status": CHANNEL_FAILED,
                "count": 0,
                "error": f"{type(e).__name__}: {e}"[:_CHANNEL_ERROR_MAX_CHARS],
            }
            print(f"  WARNING: {channel_name} search failed: {e}")

    # ------------------------------------------------------------------
    # Weighted RRF fusion across all channels
    # ------------------------------------------------------------------
    # Convert each channel's ranked list to {nct_id: rank} dict
    def _to_rank_dict(results):
        seen = {}
        for rank, r in enumerate(results):
            nct_id = r.payload["nct_id"]
            if nct_id not in seen:
                seen[nct_id] = rank
        return seen

    title_ranks      = _to_rank_dict(title_results)
    conditions_ranks = _to_rank_dict(conditions_results)
    criteria_ranks   = _to_rank_dict(criteria_results)
    vector_ranks     = _to_rank_dict(vector_results)

    all_nct_ids = (
        set(title_ranks.keys()) | set(conditions_ranks.keys())
        | set(criteria_ranks.keys()) | set(vector_ranks.keys())
    )

    fusion_scores = {}
    for nct_id in all_nct_ids:
        score = 0.0
        if nct_id in title_ranks:
            score += WEIGHT_TITLE * (1.0 / (RRF_K + title_ranks[nct_id]))
        if nct_id in conditions_ranks:
            score += WEIGHT_CONDITIONS * (1.0 / (RRF_K + conditions_ranks[nct_id]))
        if nct_id in criteria_ranks:
            score += WEIGHT_CRITERIA * (1.0 / (RRF_K + criteria_ranks[nct_id]))
        if nct_id in vector_ranks:
            score += WEIGHT_DENSE * (1.0 / (RRF_K + vector_ranks[nct_id]))
        fusion_scores[nct_id] = score

    ranked_nct_ids = sorted(
        fusion_scores.items(),
        key=lambda x: (x[1], x[0]),
        reverse=True,
    )[:RRF_POOL_SIZE]

    # ------------------------------------------------------------------
    # Retrieve full trial data from payload
    # ------------------------------------------------------------------
    # Build payload map from all channels that returned payload
    payload_map = {}
    for results in (title_results, conditions_results, criteria_results, vector_results):
        for r in results:
            nct_id = r.payload.get("nct_id", "")
            if nct_id and nct_id not in payload_map:
                full_json = r.payload.get("full_trial_json")
                if full_json:
                    payload_map[nct_id] = full_json

    trials = []
    missing_nct_ids = []
    # Trials that were ranked into the fusion pool but whose payload could not
    # be recovered, so they never reached Stage 3. Counted rather than only
    # printed: this is a second way the pool silently shrinks.
    trials_lost = 0

    for nct_id, fusion_score in ranked_nct_ids:
        trial_data = payload_map.get(nct_id)
        if trial_data:
            trials.append({"trial": trial_data, "fusion_score": fusion_score})
        else:
            missing_nct_ids.append(nct_id)

    # Batch-fetch missing trials
    if missing_nct_ids:
        try:
            scroll_filter = {
                "should": [
                    {"key": "nct_id", "match": {"value": nct_id}}
                    for nct_id in missing_nct_ids
                ]
            }

            @qdrant_retry
            def _batch_scroll():
                return qdrant_client.scroll(
                    collection_name=COLLECTION_NAME,
                    scroll_filter=scroll_filter,
                    limit=len(missing_nct_ids),
                    with_payload=True,
                    timeout=20,
                )

            fetched_points, _ = _batch_scroll()
            fetched_map = {
                p.payload["nct_id"]: p.payload["full_trial_json"]
                for p in fetched_points
            }
            for nct_id in missing_nct_ids:
                trial_data = fetched_map.get(nct_id)
                if trial_data:
                    trials.append({
                        "trial": trial_data,
                        "fusion_score": fusion_scores[nct_id],
                    })
                else:
                    # Ranked in, but the backfill did not return it either.
                    trials_lost += 1
        except Exception as e:
            trials_lost += len(missing_nct_ids)
            print(f"  WARNING: Batch scroll failed: {e}")
            print(f"  Lost {len(missing_nct_ids)} trials from retrieval pool")

    elapsed = time.time() - start

    # Logging
    channel_counts = {
        "title": len(title_results),
        "conditions": len(conditions_results),
        "criteria": len(criteria_results),
        "dense": len(vector_results),
    }
    active_channels = [f"{k}={v}" for k, v in channel_counts.items() if v > 0]

    # ── Observed per-channel counts (logged to inferences) ────────────────
    #
    # BM25 runs as THREE field queries of BM25_RETRIEVAL_SIZE each, so summing
    # them would triple-count any trial that matched on more than one field.
    # The union of the per-channel rank dicts is the number of distinct trials
    # the sparse side actually contributed to fusion, which is the quantity a
    # fusion-efficiency ratio needs as its denominator.
    #
    # Both counts are observations, not configuration: under retrieval_mode
    # "bm25_only" the dense count is 0 and under "vector_only" the sparse count
    # is 0, and a channel whose query raised above (caught per channel, logged
    # as a WARNING) lands here as 0 as well.
    bm25_retrieved = len(
        set(title_ranks) | set(conditions_ranks) | set(criteria_ranks)
    )
    vector_retrieved = len(vector_ranks)

    # ── Channel-level degradation, as counts rather than as printed lines ──
    #
    # "Expected" is the number of channels this retrieval mode called for, so
    # an ablated channel is never counted as a loss: under bm25_only the run is
    # not degraded for having no dense results, it is configured that way.
    # Anything else that did not return — a raise, or a query with no BM25
    # terms — is the real thing, and degraded=1 makes it a WHERE clause.
    channels_expected = len(futures)
    channels_ok = sum(
        1 for c in retrieval_channels.values() if c["status"] == CHANNEL_OK
    )
    retrieval_degraded = int(channels_ok < channels_expected)

    if _retrieval_mode != "hybrid":
        mode_label = f"{_retrieval_mode} (ablation)"
    elif vector_results and title_results:
        mode_label = "multi-field hybrid"
    elif title_results:
        mode_label = "BM25-only (dense fallback)"
    else:
        mode_label = "dense-only (BM25 fallback)"

    print(f"[Stage 2] {mode_label} retrieval: {elapsed:.2f}s | {len(trials)} trials")
    print(f"  Channels: {', '.join(active_channels)}")
    print(
        "  Channel status: "
        + ", ".join(
            f"{name}={retrieval_channels[name]['status']}"
            for name in RETRIEVAL_CHANNELS
        )
        + f" | {channels_ok}/{channels_expected} expected channels returned"
        + (" [DEGRADED]" if retrieval_degraded else "")
    )
    print(f"  Disease query: \"{disease_query}\"")
    print(f"  Fusion pool: {len(all_nct_ids)} unique NCTs -> top {len(ranked_nct_ids)}")
    print(
        f"  Observed: bm25={bm25_retrieved} unique (of "
        f"{3 * BM25_RETRIEVAL_SIZE} requested), vector={vector_retrieved} "
        f"(of {VECTOR_RETRIEVAL_SIZE} requested)"
    )
    if trials_lost:
        print(f"  Payload backfill lost {trials_lost} ranked trial(s)")

    return {
        "hybrid_results": trials,
        "bm25_retrieved": bm25_retrieved,
        "vector_retrieved": vector_retrieved,
        # Per-channel outcome + the three scalars derived from it. These are
        # the record that the run used the retrieval it was configured for.
        "retrieval_channels": retrieval_channels,
        "retrieval_channels_expected": channels_expected,
        "retrieval_channels_ok": channels_ok,
        "retrieval_degraded": retrieval_degraded,
        "retrieval_trials_lost": trials_lost,
        "stage_timings": {
            **state.get("stage_timings", {}),
            "hybrid_retrieval": round(elapsed, 3),
        },
    }


# RRF constant for cross-encoder fusion (same as Stage 2 hybrid retrieval)
RERANK_RRF_K = 60


# Shape of the boost report when no boost pass ran at all. 'path' names which
# branch was taken so the ablation and the production runs are distinguishable
# in the logs.
_EMPTY_BOOST_STATS = {
    "path":            "not_run",
    "direct_boosted":  0,
    "pan_boosted":     0,
    "unboosted":       0,
    "boost_direct":    0.0,
    "boost_pan":       0.0,
    "rrf_spread":      0.0,
}


def apply_mesh_relevance_boost(top_trials: List[Dict],
                               patient_trees: set,
                               mesh_filter) -> Dict:
    """Add the MeSH relevance boost to each trial's rerank_score, in place.

    The cross-encoder ranks by text similarity, which treats a trial
    explicitly targeting "Prostatic Neoplasms" the same as a generic trial
    that mentions prostate in passing. MeSH ancestry is an authoritative
    clinical signal that identifies disease-specific trials.

    Applied at the end of Stage 3 so the boosted order propagates to the
    Stage 4 rule filter, benchmark Tier 3 ranking, and Streamlit display.

    Boost tiers (a FRACTION of the RRF spread, from 03- Config.py):
        DIRECT MATCH:  shares MeSH C04 ancestry with the patient
        PAN-CANCER:    targets a broad neoplasm category (depth <= 2)
        UNMAPPABLE:    no MeSH C04 trees -> boost 0 (neutral)

    Each trial keeps three fields, so ranking and gating stay separable:
        rerank_score_raw  unboosted fused RRF score (Stage 4 gates on this)
        mesh_boost        the additive boost, 0.0 when none applied
        mesh_boost_tier   "direct" | "pan_cancer" | "none"

    Returns a report dict (same keys as _EMPTY_BOOST_STATS). Mutates
    top_trials and re-sorts it by boosted score.
    """
    stats = dict(_EMPTY_BOOST_STATS)

    if not top_trials:
        stats["path"] = "no_trials"
        return stats

    if not patient_trees:
        # Patient side unmappable — the same conservative stance the MeSH
        # filter takes. Every trial keeps its raw score.
        stats["path"] = "no_patient_trees"
        stats["unboosted"] = len(top_trials)
        return stats

    # Stage 3 guard: a pan-cancer node is not a cancer identity.
    # C04 is a prefix of every descriptor in the tree, so a patient carrying
    # only C04 / a depth-2 node shares "ancestry" with every mapped trial:
    # specific trials would take the full direct boost while the genuine
    # basket trials take only the smaller pan boost — the ranking signal
    # inverted. resolve_patient_trees() already drops those trees; this is the
    # second gate, and it reports its own path so the run is distinguishable
    # from an unmappable patient.
    specific_patient_trees = specific_cancer_trees(patient_trees)
    if not specific_patient_trees:
        stats["path"] = "pan_cancer_only_patient_trees"
        stats["unboosted"] = len(top_trials)
        return stats
    patient_trees = specific_patient_trees

    # Calibrate boost from the batch's own RRF score distribution
    rr_scores = [t.get("rerank_score_raw", t.get("rerank_score", 0.0))
                 for t in top_trials]
    rr_spread = max(rr_scores) - min(rr_scores)

    if rr_spread > 1e-6:
        stats["path"] = "spread"
        boost_direct = rr_spread * MESH_BOOST_DIRECT_FRACTION
        boost_pan    = rr_spread * MESH_BOOST_PAN_FRACTION
    else:
        # Degenerate distribution (every trial tied): a fraction of the
        # spread would be exactly 0, so fall back to absolute floors.
        stats["path"] = "degenerate_spread_floor"
        boost_direct = MESH_BOOST_DIRECT_FLOOR
        boost_pan    = MESH_BOOST_PAN_FLOOR

    for trial_obj in top_trials:
        trial = trial_obj["trial"]
        trial_trees = mesh_filter.trial_mesh_trees(trial)

        if not trial_trees:
            stats["unboosted"] += 1
            continue

        if mesh_filter._is_pan_cancer(trial_trees):
            trial_obj["rerank_score"] += boost_pan
            trial_obj["mesh_boost"] = boost_pan
            trial_obj["mesh_boost_tier"] = "pan_cancer"
            stats["pan_boosted"] += 1
            continue

        has_ancestry = False
        for pt in patient_trees:
            for tt in trial_trees:
                if pt.startswith(tt) or tt.startswith(pt):
                    has_ancestry = True
                    break
            if has_ancestry:
                break

        if has_ancestry:
            trial_obj["rerank_score"] += boost_direct
            trial_obj["mesh_boost"] = boost_direct
            trial_obj["mesh_boost_tier"] = "direct"
            stats["direct_boosted"] += 1
        else:
            stats["unboosted"] += 1

    # Re-sort after boost to update ranking order
    top_trials.sort(
        key=lambda x: (x.get("rerank_score", 0), x["trial"]["nct_id"]),
        reverse=True,
    )

    stats["boost_direct"] = float(boost_direct)
    stats["boost_pan"]    = float(boost_pan)
    stats["rrf_spread"]   = float(rr_spread)
    return stats


def unboosted_score(trial_obj: Dict, default: float = -999.0) -> float:
    """Rerank score with the MeSH boost excluded.

    rerank_score_raw is written by Stage 3 for every trial. The fallback to
    rerank_score covers trial dicts built elsewhere (older rows replayed
    through the filter, hand-built fixtures) — in those the two are equal
    because no boost was ever added.
    """
    raw = trial_obj.get("rerank_score_raw")
    if raw is None:
        raw = trial_obj.get("rerank_score", default)
    return raw


def apply_quality_gate(trials: List[Dict],
                       percentile: float = None,
                       floor: float = None) -> tuple:
    """Drop weak trials using a percentile of the UNBOOSTED rerank score.

    Gating on the boosted score would measure whether a trial received a
    MeSH boost rather than whether it is any good: with a boost of 0.25 of
    the spread the whole boosted cohort sits above the 25th percentile by
    construction, so the survivors are the boosted set and the trials the
    MeSH filter deliberately KEPT as unmappable get cut here instead. That
    would be a second, uncounted MeSH filter. Gating on rerank_score_raw
    keeps the boost a ranking signal only.

    Returns (kept, threshold). Input order is preserved; callers sort by the
    boosted score before calling.
    """
    if percentile is None:
        percentile = QUALITY_THRESHOLD_PERCENTILE
    if floor is None:
        floor = RERANK_SCORE_THRESHOLD

    if not trials:
        return [], floor

    raw_scores = [unboosted_score(t) for t in trials]
    threshold = max(float(np.percentile(raw_scores, percentile)), floor)
    kept = [t for t in trials if unboosted_score(t) >= threshold]
    return kept, threshold


def node_cross_encoder_rerank(state: dict) -> dict:
    """
    Stage 3: Multi-query cross-encoder reranking with RRF fusion.

    Runs MedCPT Cross-Encoder once per rerank query (typically 3 passes),
    then fuses the per-query rankings via Reciprocal Rank Fusion (RRF).

    Why multi-query:
      MedCPT was trained on 2-10 word PubMed queries. Each rerank query is
      3-8 words targeting a different vocabulary dimension (MeSH, molecular,
      disease state). Three short queries give MedCPT native-format input
      and cover more matching surfaces than a single query.

    Why RRF:
      MedCPT raw scores are not normalized across queries (different queries
      produce different score ranges). RRF converts scores to ranks first,
      making fusion scale-independent. Trials ranked highly by multiple
      queries get the strongest boost — exactly the desired behavior.
    """
    start = time.time()

    rerank_queries = state.get("rerank_queries", [])
    trials = state["hybrid_results"]

    # Guard: no trials to rerank — pass through empty
    if not trials:
        print("[Stage 3] Cross-encoder rerank: 0 trials — nothing to rerank")
        return {
            "reranked_trials": [],
            # Declared on every Stage 3 exit so no downstream reader has to
            # distinguish "not resolved" from "key never written". Empty here
            # is correct: there is no pool for Stage 4 to filter.
            "patient_trees": set(),
            "stage_timings": {
                **state.get("stage_timings", {}),
                "cross_encoder": 0.0,
            },
        }
    
    # --- Ablation flags (read once) ---
    _ablation = state.get("ablation_flags") or {}
    # skip_mesh_filter removes BOTH MeSH uses: the Stage 3 boost here and the
    # Stage 4 hard drop. Disabling only the drop would leave the ablation row
    # confounded, since the boost still reorders (and re-gates) the pool.
    _skip_mesh_boost = _ablation.get("skip_mesh_filter", False)

    # -----------------------------------------------------------------
    # Patient MeSH resolution (must precede the skip_cross_encoder guard)
    # -----------------------------------------------------------------
    # Stage 3 is the only producer of state["patient_trees"], and Stage 4's
    # cancer site filter is its only consumer. Resolving inside the reranking
    # body meant the skip_cross_encoder early return handed Stage 4 an empty
    # set, silently disabling the MeSH filter as well and confounding the
    # no_cross_encoder ablation row with no_mesh_filter.
    #
    # skip_mesh_filter is the one flag that must leave the trees empty: that
    # ablation removes BOTH MeSH uses (this boost and the Stage 4 hard drop),
    # so it deliberately does not resolve. Every other path resolves.
    patient_trees = set()

    if _skip_mesh_boost:
        print("  MeSH patient resolution [ablation_skipped]: skip_mesh_filter set")
    elif _MESH_FILTER is None:
        print("  MeSH patient resolution [no_mesh_filter]: filter unavailable")
    else:
        # Same helper, same conditions and same verification filter as Stage 1,
        # so the trees handed to Stage 4 match the identity the expanded query
        # was built from — and the layer that produced them is the one already
        # recorded in mesh_resolution.
        mesh_resolution = resolve_patient_mesh(
            state["patient_data"].get("conditions", []),
            _CANCER_REGISTRY,
            _MESH_FILTER,
        )
        patient_trees = mesh_resolution["trees"]
        print(f"  MeSH patient resolution {format_mesh_resolution(mesh_resolution)}")

    # --- Ablation: skip cross-encoder ---
    if _ablation.get("skip_cross_encoder", False):
        # Pass hybrid results through to rule filter without reranking.
        # Uses fusion_score (from Stage 2 RRF) as stand-in for rerank_score.
        #
        # Sort by fusion_score descending before capping at TOP_K_CANDIDATES.
        # hybrid_results is in insertion order (not score order) because
        # batch-scrolled trials are appended at the end. Without sorting,
        # [:TOP_K_CANDIDATES] would take the first 40 by insertion order,
        # potentially missing high-scoring scroll-fetched trials.
        #
        # The rule filter's dynamic quality threshold (25th percentile) and
        # MAX_TRIALS_FOR_EVALUATION cap still apply downstream. This only
        # removes the cross-encoder's contribution to ranking quality.
        print("[Stage 3] Cross-encoder rerank: SKIPPED (ablation)")
        sorted_trials = sorted(
            trials,
            key=lambda t: t.get("fusion_score", 0.0),
            reverse=True,
        )
        # No boost is applied on this path, so raw == boosted and the Stage 4
        # quality gate reads the same number it always did.
        passthrough = [
            {
                "trial":            t["trial"],
                "rerank_score":     t.get("fusion_score", 0.0),
                "rerank_score_raw": t.get("fusion_score", 0.0),
                "mesh_boost":       0.0,
                "mesh_boost_tier":  "none",
            }
            for t in sorted_trials[:TOP_K_CANDIDATES]
        ]
        return {
            "reranked_trials": passthrough,
            # Carried through: Stage 4 reads this and nothing else writes it.
            "patient_trees": patient_trees,
            "stage_timings": {
                **state.get("stage_timings", {}),
                "cross_encoder": 0.0,
            },
        }
    
    # Fallback: if no rerank queries, use expanded_query (degraded, old behavior)
    if not rerank_queries:
        print("  WARNING: No rerank queries available. "
              "Falling back to expanded_query (degraded).")
        rerank_queries = [state["expanded_query"]]

    # -----------------------------------------------------------------
    # Build trial texts once (shared across all query passes)
    # -----------------------------------------------------------------
    trial_texts = []
    for trial_obj in trials:
        trial = trial_obj["trial"]
        # MedCPT max 512 tokens. With 3-8 token queries, ~500 tokens for
        # trial text ≈ 1850 chars. Keep 1600 char cap for safety margin.
        trial_text = (
            f"{trial['title']} {trial['eligibility']['criteria_text'][:1600]}"
        )
        trial_texts.append(trial_text)

    # -----------------------------------------------------------------
    # Score trials per query, collect per-query rankings
    # -----------------------------------------------------------------
    # per_query_ranks[trial_index] = list of ranks across queries
    per_query_ranks: Dict[int, List[int]] = {
        i: [] for i in range(len(trials))
    }

    per_query_stats = []  # for logging

    for q_idx, query in enumerate(rerank_queries):
        scores = medcpt_score_pairs(query, trial_texts)

        # Log per-query score distribution
        per_query_stats.append({
            "query": query[:80],
            "min": float(scores.min()),
            "max": float(scores.max()),
            "mean": float(scores.mean()),
            "spread": float(scores.max() - scores.min()),
            "positive": int((scores > 0).sum()),
        })

        # Convert scores → ranks (0 = best)
        # NOTE: kind='stable' uses mergesort, which preserves input order for
        # tied values. Input order is the order trials appear in the hybrid
        # results list, which is now deterministic (from Edit C2). So the
        # cross-encoder ranking becomes fully deterministic.
        ranked_indices = np.argsort(-scores, kind='stable')  # descending, stable tiebreak
        
        for rank, trial_idx in enumerate(ranked_indices):
            per_query_ranks[trial_idx].append(rank)

    # -----------------------------------------------------------------
    # RRF fusion across queries
    # -----------------------------------------------------------------
    rrf_scores = {}
    for trial_idx, ranks in per_query_ranks.items():
        rrf_scores[trial_idx] = sum(
            1.0 / (RERANK_RRF_K + rank) for rank in ranks
        )

    # Sort by fused RRF score, keep top-K
    sorted_by_rrf = sorted(
         rrf_scores.items(),
         key=lambda x: (x[1], trials[x[0]]["trial"]["nct_id"]),  # tiebreak: NCT ID descending
         reverse=True
     )

    # rerank_score is the ranking score and may be boosted below.
    # rerank_score_raw is the untouched fused score the Stage 4 quality gate
    # is computed on; mesh_boost carries the difference between the two.
    top_trials = [
        {
            "trial":            trials[trial_idx]["trial"],
            "rerank_score":     float(rrf_score),
            "rerank_score_raw": float(rrf_score),
            "mesh_boost":       0.0,
            "mesh_boost_tier":  "none",
        }
        for trial_idx, rrf_score in sorted_by_rrf[:TOP_K_CANDIDATES]
    ]

    # -----------------------------------------------------------------
    # MeSH Relevance Boost (see apply_mesh_relevance_boost)
    # -----------------------------------------------------------------
    # patient_trees was resolved above, before the skip_cross_encoder guard.
    if _skip_mesh_boost:
        # The MeSH ablation must remove BOTH uses of MeSH, otherwise the
        # no_mesh_filter row still carries the boost's effect on ranking.
        print("  MeSH relevance boost [ablation_skipped]: skip_mesh_filter set")
    elif _MESH_FILTER is None:
        print("  MeSH relevance boost [no_mesh_filter]: filter unavailable")
    elif top_trials:
        boost_stats = apply_mesh_relevance_boost(
            top_trials, patient_trees, _MESH_FILTER
        )
        print(f"  MeSH relevance boost [{boost_stats['path']}]: "
              f"direct={boost_stats['direct_boosted']} "
              f"(+{boost_stats['boost_direct']:.5f}) "
              f"pan={boost_stats['pan_boosted']} "
              f"(+{boost_stats['boost_pan']:.5f}) "
              f"unboosted={boost_stats['unboosted']}")

    # -----------------------------------------------------------------
    # Logging
    # -----------------------------------------------------------------
    elapsed = time.time() - start

    print(f"[Stage 3] Multi-query cross-encoder rerank: {elapsed:.2f}s "
          f"| {len(rerank_queries)} queries x {len(trials)} trials "
          f"| {len(top_trials)} kept")
    for i, stats in enumerate(per_query_stats):
        print(f"  R{i+1}: [{stats['min']:.1f} to {stats['max']:.1f}] "
              f"spread={stats['spread']:.1f} "
              f"pos={stats['positive']}/{len(trials)} "
              f'"{stats["query"]}"')

    rrf_values = [s for _, s in sorted_by_rrf]
    if rrf_values:
        print(f"  RRF range: {rrf_values[-1]:.5f} to {rrf_values[0]:.5f}")

    return {
        "reranked_trials": top_trials,
        # Already empty when _MESH_FILTER is None or skip_mesh_filter is set.
        "patient_trees": patient_trees,
        "stage_timings": {
            **state.get("stage_timings", {}),
            "cross_encoder": round(elapsed, 3),
        },
    }


def node_rule_based_filter(state: TrialMatchState) -> dict:
    """
    Stage 4: Rule-based filtering to remove obvious mismatches.

    Fast heuristic checks before expensive GPT-4o evaluation:
        - Cancer site: patient cancer type must match trial cancer type (MeSH)  # NEW
        - Age: patient age must fall within trial's min/max age
        - Sex: patient sex must match trial's sex requirement
        - Quality threshold: drop trials whose UNBOOSTED rerank score falls
          below the QUALITY_THRESHOLD_PERCENTILE of the surviving pool
          (hard floor RERANK_SCORE_THRESHOLD). Computed on rerank_score_raw
          so the gate measures trial quality, not MeSH boost membership.
        - Cost cap: limit to MAX_TRIALS_FOR_EVALUATION candidates
    """
    start = time.time()

    patient_data = state["patient_data"]
    trials = state["reranked_trials"]

    demographics = patient_data["demographics"]
    conditions = patient_data["conditions"]

    patient_age = demographics.get("age")
    patient_sex = demographics.get("sex", "unknown").lower()

    # --- Ablation flags (read once, not per-trial) ---
    _ablation = state.get("ablation_flags") or {}
    _skip_mesh      = _ablation.get("skip_mesh_filter", False)
    _skip_stage     = _ablation.get("skip_stage_filter", False)
    _skip_histology = _ablation.get("skip_histology_filter", False)

    # --- Get patient's MeSH cancer site tree numbers ---
    mesh_dropped = 0
    histology_dropped = 0
    patient_trees = set()
    patient_histology = set()
    if _MESH_FILTER is not None:

        patient_trees   = state.get("patient_trees") or set()
        patient_histology = extract_patient_histology(conditions)

        # Under the ablation Stage 3 never resolves the trees, so an empty set
        # here means "ablated", not "unmappable" — the ablation line below
        # says which, so do not also claim the trees were unresolvable.
        if not _skip_mesh:
            if patient_trees:
                print(f"  MeSH patient trees: {patient_trees}")
            else:
                # Say which outcome this is. "pan_cancer_only" is a resolution
                # that was deliberately rejected, not a lookup that missed.
                print(f"  MeSH: no patient cancer trees resolved "
                      f"[{state.get('mesh_resolution') or 'unrecorded'}] — "
                      f"cancer site filter skipped")

    # --- Did the cancer site filter actually run? ---
    #
    # The per-trial condition below is loop-invariant, so it is decided once
    # here and recorded. Stage 5's system prompt asserts to the model that
    # disease relevance "has already been confirmed"; that sentence is only
    # true when this is MESH_FILTER_APPLIED. In the other three cases the model
    # was told a check passed that never ran, and no stored record said so.
    if _skip_mesh:
        mesh_filter_skip_reason = MESH_FILTER_SKIP_ABLATED
    elif _MESH_FILTER is None:
        mesh_filter_skip_reason = MESH_FILTER_SKIP_NO_FILTER
    elif not patient_trees:
        # Covers both "unmapped" and "pan_cancer_only": state["mesh_resolution"]
        # carries which one, this carries the consequence.
        mesh_filter_skip_reason = MESH_FILTER_SKIP_NO_TREES
    else:
        mesh_filter_skip_reason = MESH_FILTER_APPLIED

    mesh_filter_applied = mesh_filter_skip_reason == MESH_FILTER_APPLIED

    # --- Extract patient cancer stage ---
    patient_stage = extract_patient_stage(
        conditions,
        cancer_stage_observations=patient_data.get('cancer_stage_observations') or []
    )
    
    stage_dropped = 0
    
    if patient_stage is not None:
        print(f"  Patient cancer stage: {patient_stage}")
    else:
        print("  Patient cancer stage: unknown — stage filter skipped")
    
    if _skip_mesh:
        print("  [Ablation] MeSH cancer site filter SKIPPED "
              "(Stage 3 relevance boost was skipped too)")
    if _skip_stage:
        print("  [Ablation] Cancer stage filter SKIPPED")
    if _skip_histology:
        print("  [Ablation] Histology mismatch filter SKIPPED")

    filtered = []

    # The age and sex cuts below used to be bare `continue`s. Every other drop
    # in this loop already had a counter, so the two that did not were the only
    # ones a stored funnel could not account for.
    age_dropped = 0
    sex_dropped = 0

    for trial_obj in trials:
        trial = trial_obj["trial"]
        eligibility = trial["eligibility"]

        # --- Cancer site filter ---
        if mesh_filter_applied:
            if not _MESH_FILTER.is_cancer_relevant(patient_trees, trial):
                mesh_dropped += 1
                continue

        # --- Cancer stage filter ---
        if not _skip_stage:
            if patient_stage is not None:
                if is_stage_mismatch(patient_stage, trial):
                    stage_dropped += 1
                    continue

        # --- Histology filter ---
        if not _skip_histology:
            if patient_histology and is_histology_mismatch(patient_histology, trial):
                histology_dropped += 1
                continue
        
        # --- Age filter ---
        min_age_str = eligibility.get("min_age", "0 Years")
        max_age_str = eligibility.get("max_age", "999 Years")

        try:
            min_age = int(re.findall(r'\d+', min_age_str)[0]) if min_age_str else 0
            max_age = int(re.findall(r'\d+', max_age_str)[0]) if max_age_str else 999

            if patient_age is not None and not (min_age <= patient_age <= max_age):
                age_dropped += 1
                continue
        except (IndexError, ValueError):
            pass  # Keep trial if age parsing fails

        # --- Sex filter ---
        trial_sex = eligibility.get("sex", "ALL").upper()
        if trial_sex not in ["ALL", patient_sex.upper()]:
            sex_dropped += 1
            continue

        filtered.append(trial_obj)

    # Sort by rerank_score (highest first) — this IS the boosted score, since
    # ranking order is what the MeSH boost exists to influence.
    filtered.sort(
         key=lambda x: (x.get("rerank_score", 0), x["trial"]["nct_id"]),
         reverse=True
     )

    # Dynamic quality threshold: percentile of the UNBOOSTED score, hard floor.
    quality_filtered, dynamic_threshold = apply_quality_gate(filtered)
    quality_dropped = len(filtered) - len(quality_filtered)

    candidates_after_quality = len(quality_filtered)

    # Cost cap: limit candidates sent to GPT-4o
    if len(quality_filtered) > MAX_TRIALS_FOR_EVALUATION:
        quality_filtered = quality_filtered[:MAX_TRIALS_FOR_EVALUATION]

    elapsed = time.time() - start
    
    if not mesh_filter_applied:
        print(f"  Cancer site filter DID NOT RUN [{mesh_filter_skip_reason}] — "
              f"Stage 5 will not assert that disease relevance was confirmed")

    print(
        f"[Stage 4] Rule-based filter: {elapsed:.2f}s | "
        f"{len(trials)} -> {len(quality_filtered)} trials"
        f"{f' (MeSH dropped {mesh_dropped})' if mesh_dropped else ''}"
        f"{f' (stage dropped {stage_dropped})' if stage_dropped else ''}"
        f"{f' (histology dropped {histology_dropped})' if histology_dropped else ''}"
        f"{f' (age dropped {age_dropped})' if age_dropped else ''}"
        f"{f' (sex dropped {sex_dropped})' if sex_dropped else ''}"
        f"{f' (quality dropped {quality_dropped} @ raw >= {dynamic_threshold:.5f})' if quality_dropped else ''}"
    )

    return {
        "filtered_trials": quality_filtered,
        "candidates_after_rule_filter": len(filtered),
        "candidates_after_quality_filter": candidates_after_quality,
        "mesh_dropped": mesh_dropped,
        "histology_dropped": histology_dropped,
        "stage_dropped": stage_dropped,
        # The two per-trial drops that had no counter, plus the pool-level cut
        # and the score it was made at. Together with the three above they
        # account for every trial that entered this stage and did not leave it.
        "age_dropped": age_dropped,
        "sex_dropped": sex_dropped,
        "quality_dropped": quality_dropped,
        "quality_threshold": float(dynamic_threshold),
        # Read by Stage 5 to decide what its system prompt may assert, and
        # logged so a stored inference says whether the check ran.
        "mesh_filter_applied": mesh_filter_applied,
        "mesh_filter_skip_reason": mesh_filter_skip_reason,
        "stage_timings": {**state.get("stage_timings", {}), "rule_filter": round(elapsed, 3)}
    }


# ---------------------------------------------------------------------------
# Stage 5 model-call seam
# ---------------------------------------------------------------------------


class MatchingModelMismatchError(RuntimeError):
    """The API answered as a different model than MATCHING_MODEL requested.

    WHY THIS STOPS THE RUN INSTEAD OF WARNING.

    Model aliases resolve. As of 2026-08-04 gpt-5.6-terra publishes exactly one
    snapshot and it is the alias string itself, so the requested and returned
    strings are identical and this never fires. That will not stay true: the
    moment OpenAI publishes a dated snapshot the alias resolves to it, every
    verdict in the pipeline shifts, and nothing in the request changes.

    Recording the returned model in inferences.matching_model (which File 13
    already does) makes the change auditable AFTER the fact. It does not stop a
    campaign from being run half on one judge and half on another, which is
    exactly the confound this project exists to remove -- an ablation study or
    a drift baseline built across that boundary is measuring the model, not the
    thing it claims to measure. A printed warning is worse than nothing here,
    because the runs that matter are multi-day batch runs whose console nobody
    reads.

    So: raise, at the first response that disagrees, before any verdict from it
    reaches a result dict.

    A None return does NOT raise. That means the response object carried no
    model field at all -- a stub (File 37) or a pre-migration recording (File
    46) -- which is a different condition with its own handling: model_answered
    stays None and File 14 logs NULL.

    Recovery is a human decision, not an automatic one: review what changed,
    then set MATCHING_MODEL to the returned string and re-baseline. The message
    carries both strings so that decision can be made from the traceback alone.

    A RuntimeError subclass rather than a ValueError, for the same reason
    UnknownModelPricingError is: a stray `except ValueError` around a parsing
    step must not be able to eat it.
    """

    def __init__(self, requested: str, returned: str):
        self.requested = requested
        self.returned = returned
        super().__init__(
            f"Stage 5 requested model {requested!r} but the API answered as "
            f"{returned!r}. The configured model resolved to something other "
            f"than what was configured -- almost certainly an alias that now "
            f"points at a dated snapshot. Every verdict from this point on "
            f"would come from a different judge than the rows already in "
            f"inferences.db, so the run is stopped rather than continued and "
            f"logged. After reviewing what changed, set MATCHING_MODEL in "
            f"'03- Config.py' to {returned!r}, add it to PRICING_CONFIG if it "
            f"is not there, and re-baseline; do not accept it silently."
        )
# MATCHING_MAX_TOKENS and MATCHING_SEED are in 03- Config.py, together with
# the truncation thresholds calibrated against the first of them.


def call_matching_model(system_prompt: str, user_prompt: str):
    """Issue the Stage 5 evaluation request and return the raw API response.

    Lifted out of node_gpt4o_evaluation unchanged. It is the single point where
    the pipeline talks to the matching model, which is what lets a recording
    harness capture the request and response verbatim and a replay harness
    serve them back without a network call (45-/46- Fixture Capture/Replay).

    The caller owns error handling: this raises whatever the client raises, and
    node_gpt4o_evaluation's except block turns that into a retry.

    REQUEST SHAPE, AND WHY EACH PARAMETER IS OR IS NOT HERE. Every one of these
    was probed live against gpt-5.6-terra on 2026-08-04 rather than inferred
    from documentation; the model page does not enumerate its own parameter
    restrictions.

      max_completion_tokens   REQUIRED. `max_tokens` is rejected outright:
                              400 unsupported_parameter, "Use
                              'max_completion_tokens' instead." Note this caps
                              reasoning AND visible output together.
      reasoning_effort        Accepted values for THIS model are none / low /
                              medium / high / xhigh. Set from File 03.
      seed                    Accepted (no error). Best-effort only; the model
                              returns no system_fingerprint.
      timeout                 CLIENT-SIDE, not a request parameter: it bounds
                              how long this process waits, and never reaches
                              the model. Set from File 03, replacing the SDK's
                              600s default. Because it cannot change the
                              response, File 45 does not record it and File 46
                              does not replay it -- unlike everything else in
                              this call, a change to it is invisible in a
                              fixture diff, and that is correct.
      max_retries             NOT SET HERE, and that is not an oversight: it
                              is set on the client itself in File 03
                              (OPENAI_SDK_MAX_RETRIES) because it cannot be
                              scoped to one call without breaking the fixture
                              harness. See the note on the return statement.
                              It is the other half of the bound -- a timeout
                              with the SDK's default of 2 retries is still
                              three attempts -- and it is the TRANSPORT budget,
                              deliberately distinct from MAX_GPT4O_RETRIES,
                              which covers a response that arrived and would
                              not parse. File 03 states both, plus the
                              truncation-split budget, and the worst-case wall
                              time the three produce together.
      temperature             NOT SENT. Rejected for every value but the
                              provider default of 1, so there is nothing to
                              send. MATCHING_TEMPERATURE is None.
      response_format         NOT SENT, deliberately. The model does support
                              it -- a json_object probe failed only on the
                              unrelated "messages must contain the word json"
                              rule, not on the parameter -- and Stage 5 would
                              be a good candidate for Structured Outputs. It is
                              held back so the model migration can be measured
                              on its own; adding it here would change the
                              parsing contract and the verdict distribution in
                              the same commit.

    Anything added to this call must also be added to the request block File 45
    records and File 46 replays, or a fixture stops being able to see it.
    """
    # NOTE THE ASYMMETRY: timeout is set HERE, the retry budget is not.
    #
    # OPENAI_SDK_MAX_RETRIES is applied once, on the client constructor in File
    # 03, and there is no way to scope it to this call that does not break
    # something else:
    #
    #   - create() has no max_retries parameter and no **kwargs, so passing it
    #     as a kwarg raises TypeError on every call. (timeout IS in create()'s
    #     signature, alongside extra_headers/extra_query/extra_body, which is
    #     the whole reason the two look like a pair but are not.)
    #   - openai_client.with_options(max_retries=...) is the SDK's supported
    #     way, and it is the trap. It returns a NEW client object, and File
    #     45's OpenAIProxy -- the shim that records Stage 5 exchanges into a
    #     fixture -- forwards unknown attributes straight to the real inner
    #     client via __getattr__. So with_options() here would hand back an
    #     UNWRAPPED client: capture would issue a real call and record nothing,
    #     and File 46's replay would go to the network instead of serving the
    #     recording. A per-call retry override costs the entire fixture
    #     harness.
    #
    # So the retry budget is client-wide by necessity, and File 03 says so.
    return openai_client.chat.completions.create(
        model=MATCHING_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_completion_tokens=MATCHING_MAX_TOKENS,
        reasoning_effort=MATCHING_REASONING_EFFORT,
        seed=MATCHING_SEED,
        # The STRUCTURED Timeout, not the bare number. A per-request timeout
        # replaces the client's Timeout object outright rather than merging with
        # it, so passing the float here would have re-flattened the connect
        # phase to 300s on this call no matter what the client carries. See
        # _structured_timeout() in File 03.
        timeout=MATCHING_REQUEST_TIMEOUT,
    )


# ---------------------------------------------------------------------------
# Stage 5 output-size estimation and batch splitting
# ---------------------------------------------------------------------------

# Why a trial can end up with no evaluation. Recorded per trial so a missing
# verdict is never inferred from an absence — every trial that entered Stage 5
# leaves it either evaluated or carrying one of these.
NOT_EVALUABLE_TRUNCATION_FLOOR = "truncation_floor"
# ^ this trial was sent alone and the response still hit the token ceiling.
#   There is nothing left to split; the model cannot answer it within the
#   ceiling. Distinct from a parse failure, which is a malformed answer.
NOT_EVALUABLE_SPLIT_BUDGET = "truncation_split_budget_exhausted"
# ^ the batch containing it truncated at MAX_TRUNCATION_SPLITS levels of
#   halving and could not be split further under the budget.
NOT_EVALUABLE_MODEL_OMITTED = "omitted_from_model_response"
# ^ the call succeeded and parsed, but the model returned no entry for this
#   trial. Not a truncation and not a parse failure; the reconciliation below
#   is the only thing that would ever have noticed.

_NOT_EVALUABLE_REASONS = (
    NOT_EVALUABLE_TRUNCATION_FLOOR,
    NOT_EVALUABLE_SPLIT_BUDGET,
    NOT_EVALUABLE_MODEL_OMITTED,
)

# Finish reason the API returns when it stopped because it hit max_tokens.
FINISH_REASON_LENGTH = "length"


def estimate_output_tokens(trials: List[Dict]) -> int:
    """Estimate the evaluation response size for a batch, before sending it.

    HOW THIS WAS CALIBRATED, so it can be re-derived when the model changes:

        SELECT candidates_evaluated, gpt4o_output_tokens, gpt4o_calls,
               gpt4o_reasoning_tokens, matching_model
        FROM inferences
        WHERE candidates_evaluated > 0 AND gpt4o_output_tokens > 0
          AND gpt4o_calls = 1            -- see below
        GROUP BY matching_model

    RESTRICT TO gpt4o_calls = 1 AND GROUP BY matching_model. Both matter now.
    A split run sums its tokens across chunks and, when the split was reactive,
    includes the wasted truncated call, so output/trials over those rows
    over-states the per-trial cost. And inferences.db holds rows from two
    judges since 2026-08-04; pooling them calibrates against neither.

    gpt4o_output_tokens ALREADY INCLUDES reasoning tokens (they are a subset of
    usage.completion_tokens, not an addition to it), so no term is added for
    them. gpt4o_reasoning_tokens is selected above only to see how much of the
    figure is invisible: at the configured effort of 'none' it is 0.

    2026-08-04, gpt-5.6-terra at reasoning_effort='none', over the 27
    single-call runs of the step 7 measurement: mean 959, median 974, p90
    1,048, p95 1,073, max 1,138, sd 92.

    HISTORICAL, gpt-4o-2024-08-06 over 1,094 rows in inferences.db: output per
    trial mean 714, median 744, p95 1,029, max 1,165; restricted to the 555
    rows at the 15-trial cap, median 712, p90 784, p95 861, p99 1,028, max
    1,062. Kept because those rows are still in the table and still priced.

    WHAT DID NOT PREDICT ANYTHING. Fitting output against both trial count and
    criteria length — the criteria text measured with File 11's characters/4
    proxy, taken from the gpt4o_prompt column — gives

        output ~= 708 * trials + (-0.0107) * criteria_tokens

    with a residual standard deviation of 1,935 tokens, identical to the
    trial-count-only model. The criteria-length term is negative, negligible,
    and carries no signal. That is not what one would assume: the response is
    one verdict block per trial with a bounded number of criteria in it, so a
    trial with 4,000 characters of criteria costs about the same to answer as
    one with 800. The estimate is therefore linear in trial count alone, and
    the CHARS_PER_TOKEN proxy is applied to the criteria only as a tie-breaker
    for pathological inputs, not as a driver.

    Returns the estimated output tokens for this batch.
    """
    if not trials:
        return 0

    base = MATCHING_OUTPUT_TOKENS_PER_TRIAL * len(trials)

    # The measured relationship is flat in criteria length, so this contributes
    # nothing on ordinary input. It exists for the case the calibration set
    # contains none of: a trial whose criteria text is so long that the model
    # must quote more of it. Bounded to a quarter of the per-trial allowance so
    # it can never dominate a figure the data says is driven by count.
    criteria_chars = 0
    for trial_obj in trials:
        eligibility = trial_obj.get("trial", {}).get("eligibility", {})
        criteria_chars += len(eligibility.get("inclusion_criteria") or "")
        criteria_chars += len(eligibility.get("exclusion_criteria") or "")
    criteria_tokens = criteria_chars / CHARS_PER_TOKEN
    criteria_component = min(
        criteria_tokens * 0.05,
        0.25 * MATCHING_OUTPUT_TOKENS_PER_TRIAL * len(trials),
    )

    return int(base + criteria_component)


def _split_in_half(trials: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """Halve a batch, keeping order. The larger half goes first on odd counts."""
    midpoint = (len(trials) + 1) // 2
    return trials[:midpoint], trials[midpoint:]


def _build_trials_text(trials: List[Dict]) -> str:
    """Render one batch of trials for the user prompt.

    Numbering restarts at 1 within each chunk. The model is told to key its
    output on nct_id, and every merge downstream matches on nct_id, so the
    ordinal is presentation only — but it is worth saying out loud, because a
    chunked run makes "Trial 1" appear more than once in a single inference.
    """
    trials_text = ""
    for idx, trial_obj in enumerate(trials):
        trial = trial_obj["trial"]
        trials_text += f"""Trial {idx + 1} ({trial['nct_id']}, {trial['phase']}):
{trial['eligibility']['inclusion_criteria']}
{trial['eligibility']['exclusion_criteria']}

---
"""
    return trials_text


def _unevaluable_entry(trial_obj: Dict, reason: str) -> Dict:
    """A verdict-shaped record for a trial that could not be evaluated.

    Carries the same keys node_finalize and File 14 read off a real evaluation,
    so it flows through the rest of the pipeline without special-casing, and it
    states its reason rather than being an absence someone has to explain.
    """
    trial = trial_obj["trial"]
    return {
        "nct_id": trial["nct_id"],
        "title": trial.get("title", "No title"),
        "phase": trial.get("phase", "N/A"),
        "eligible": "not_evaluable",
        "match_score": 0.0,
        "score_confirmed": 0,
        "score_denominator": 0,
        "criteria_not_applicable": 0,
        "criteria": [],
        "not_evaluable_reason": reason,
        "explanation": {
            NOT_EVALUABLE_TRUNCATION_FLOOR:
                "The model's response exceeded its output ceiling with this "
                "trial sent on its own, so there was no smaller batch to fall "
                "back to. Not assessed.",
            NOT_EVALUABLE_SPLIT_BUDGET:
                "The batch containing this trial kept exceeding the model's "
                "output ceiling and reached the split limit. Not assessed.",
            NOT_EVALUABLE_MODEL_OMITTED:
                "The model returned a well-formed response that contained no "
                "entry for this trial. Not assessed.",
        }[reason],
    }


def node_gpt4o_evaluation(state: TrialMatchState) -> dict:
    """
    Stage 5: GPT-4o criterion-level evaluation.

    Sends ALL filtered trials to GPT-4o in a SINGLE call.
    GPT-4o evaluates every inclusion/exclusion criterion for each trial
    and returns structured JSON with match scores and explanations.

    On JSON parse failure or API error, sets error flag so the retry
    router (conditional edge) can loop back for another attempt.
    Up to MAX_GPT4O_RETRIES attempts with exponential backoff.

    Temperature = 0 for deterministic, reproducible medical decisions.
    """
    
    start = time.time()

    patient_data = state["patient_data"]
    trials = state["filtered_trials"]
    retry_count = state.get("gpt4o_retries", 0)
    
    # Accumulate timing across retries (previous attempts' time is already in stage_timings)
    prior_gpt4o_time = state.get("stage_timings", {}).get("gpt4o_evaluation", 0.0)

    # Exponential backoff on retries (skip delay on first attempt)
    if retry_count > 0:
        delay = RETRY_BASE_DELAY * (2 ** (retry_count - 1))
        print(f"  [Retry {retry_count}/{MAX_GPT4O_RETRIES}] Waiting {delay}s before retry...")
        time.sleep(delay)

    # Build patient summary
    patient_summary = _create_patient_summary(patient_data)

    # Build trials text for prompt
    # Only eligibility criteria sent to GPT-4o. Title, conditions, brief
    # summary, interventions stripped to prevent GPT-4o from performing
    # its own disease relevance check. Disease relevance enforced upstream by
    # hybrid retrieval, cross-encoder reranking and — when it ran — the MeSH
    # site filter. Whether it ran is what Section 2 below is conditional on.
    trials_text = ""
    for idx, trial_obj in enumerate(trials):
        trial = trial_obj["trial"]

        trials_text += f"""Trial {idx + 1} ({trial['nct_id']}, {trial['phase']}):
{trial['eligibility']['inclusion_criteria']}
{trial['eligibility']['exclusion_criteria']}

---
"""


    # ------------------------------------------------------------------
    # Section 2 of the system prompt is an assertion about THIS run
    # ------------------------------------------------------------------
    # It told the model that disease relevance "has already been confirmed"
    # and then forbade it from assessing relevance at all. That pair of
    # sentences is only sound when Stage 4's cancer site filter ran, and it is
    # conditional on three things (the MeSH data files being loaded, the
    # patient resolving to specific C04 trees, and the ablation flag being
    # off). When any of them fails the model was handed a false premise
    # together with a rule preventing it from noticing.
    #
    # What limits the damage, and is recorded here rather than assumed:
    #   - The indexed corpus is oncology-only, so "every trial is a cancer
    #     trial" holds regardless. The claim that fails is the narrower one,
    #     that the trial matches THIS patient's cancer site.
    #   - Only eligibility criteria text is sent (see the trials_text build
    #     above), and RULE 3's categorically-different-diseases branch already
    #     turns an off-site trial into a criterion-level "not_met" whenever the
    #     criteria name the disease, which most oncology criteria do.
    # The residual exposure is a trial whose criteria never state the disease,
    # evaluated for a patient whose site was never checked. The unconfirmed
    # variant below lifts the prohibition for exactly that case.
    #
    # False when Stage 4 did not record the flag at all (the state key is
    # absent only if Stage 4 never ran), which is the conservative direction:
    # never assert a check that cannot be shown to have happened.
    _mesh_filter_applied = bool(state.get("mesh_filter_applied", False))
    _mesh_filter_reason = state.get("mesh_filter_skip_reason") or "unrecorded"

    if _mesh_filter_applied:
        scope_limitation = """Disease relevance has already been confirmed. An upstream filter compared this patient's cancer site against every trial below. Every trial you receive is disease-relevant.

Your ONLY job is to evaluate the eligibility criteria text (inclusion and exclusion) against the patient record. Do not assess disease relevance. Do not disqualify a trial for any reason other than a criterion-level "not_met" or "violated" classification."""
    else:
        scope_limitation = f"""Disease relevance has NOT been confirmed for this patient. The upstream cancer site filter did not run ({_mesh_filter_reason}), so the trials below were selected by text retrieval and re-ranking alone. They come from an oncology-only corpus, so each one is a cancer trial, but none has been checked against this patient's cancer site.

Your job is to evaluate the eligibility criteria text (inclusion and exclusion) against the patient record. Where a criterion names a disease categorically different from the patient's documented cancer, classify it under RULE 3 in the normal way (inclusion -> "not_met", exclusion -> "not_violated"). That is the only form in which disease relevance may enter your output: judge criteria, never the trial as a whole, and reason only from the criteria text you were given. Do not disqualify a trial for any reason other than a criterion-level "not_met" or "violated" classification."""


# The prompt engineering for the system prompt was:
#	•	A rule-based medical reasoning scaffold
#	•	With hallucination containment
#	•	With termination control to lower cost and increase speed
#	•	With temporal logic
#	•	With subtype hierarchy rules
# Closer to a deterministic symbolic overlay on GPT-4o.


# ================================================================
# SYSTEM MESSAGE
# ================================================================

    # RULE 4's "Reference date" is the data snapshot date, not date.today().
    # It is the same anchor the patient's age was computed against (File 07),
    # so the prompt's temporal reasoning and its stated age agree, and neither
    # moves between two runs of the same patient. Under date.today() every
    # washout window ("no platinum within 6 months") silently widened as the
    # clock advanced, while patient_data_hash stayed identical.
    system_prompt = f"""
You are a clinical trial pre-screening classifier.

Your job is NOT to determine full eligibility.
Your job is ONLY to detect whether a patient is CATEGORICALLY disqualified based on explicit, documented evidence in the patient record.

If a categorical disqualifier cannot be proven using explicit patient data, the trial remains "eligible".

=====================================================================
GLOBAL INVARIANT -- MISSING DATA (HIGHEST PRIORITY RULE)
=====================================================================

ABSENT PATIENT DATA IS NEVER A DISQUALIFIER.

If the patient record does NOT explicitly contain a data point addressing a clinical concept referenced in a trial criterion, the classification for that criterion MUST be:

    "not_evaluable"

This rule has ZERO exceptions.

Absence of data is NOT evidence of absence.

Do NOT assume:
- normal lab values
- absence of diseases
- absence of medications
- absence of biomarkers or molecular markers
- absence of treatments or procedures
- absence of symptoms or progression
- treatment outcomes from treatment status

If the patient record does not explicitly state the information, the information is UNKNOWN. UNKNOWN information ALWAYS produces:

    criterion status = "not_evaluable"

=====================================================================
DISQUALIFICATION PROOF REQUIREMENT
=====================================================================

Before classifying ANY criterion as "not_met" or "violated", you MUST answer:

"Can I quote a specific, explicit patient data point that directly and unambiguously contradicts this criterion?"

YES -> you may classify as "not_met" (inclusion) / "violated" (exclusion)
NO  -> the classification MUST be "not_evaluable"

This rule overrides clinical intuition and statistical likelihood. If you cannot quote the disqualifying evidence, disqualification is forbidden.

=====================================================================
SECTION 1 -- CLASSIFICATION STATUSES
=====================================================================

INCLUSION CRITERIA use exactly one status:

"met"             Explicit patient data directly satisfies the requirement.
"not_met"         Explicit patient data directly contradicts the requirement. Requires quotable evidence.
"not_evaluable"   The patient record does not contain sufficient information. Never disqualifying.

EXCLUSION CRITERIA use exactly one status:

"not_violated"    Explicit patient data confirms the patient does NOT have the excluded condition, including resolved/inactive/completed conditions.
"violated"        Explicit patient data confirms the patient HAS the excluded condition. Requires quotable evidence.
"not_evaluable"   The patient record does not contain sufficient information. Never disqualifying.

THE TWO VOCABULARIES ARE DISJOINT AND NON-INTERCHANGEABLE.

An inclusion criterion may ONLY be "met", "not_met", or "not_evaluable". It may NEVER be "violated" or "not_violated".
An exclusion criterion may ONLY be "not_violated", "violated", or "not_evaluable". It may NEVER be "met" or "not_met".

A status drawn from the wrong vocabulary is not a stronger or weaker form of the correct one. It carries no meaning and will be discarded as "not_evaluable". If you are tempted to write "violated" on an inclusion criterion, the criterion you mean is "not_met"; write that instead.

TRIAL-LEVEL CLASSIFICATION:

"eligible"        No disqualifying evidence was found.
"not_eligible"    At least one inclusion criterion is "not_met" OR at least one exclusion criterion is "violated".
"not_evaluable"   The trial's eligibility criteria text is empty, contains no parseable criteria, or is otherwise impossible to evaluate. Return empty inclusion_criteria and exclusion_criteria arrays. THIS IS NOT A REJECTION -- it records that the trial could not be assessed, which is different from assessing it and finding a disqualifier.

Empty inclusion_criteria and exclusion_criteria arrays are permitted ONLY with "not_evaluable". An "eligible" or "not_eligible" trial MUST list every criterion it evaluated. Never return empty arrays to signal a rejection.

NOT APPLICABLE CRITERIA:
A criterion is "Not applicable" ONLY when its subject matter is biologically or logically impossible for this patient — the criterion cannot ever apply regardless of any test, treatment, or future event. Examples: reproductive criteria for the opposite sex, pediatric criteria for adults, menopausal criteria for males.
- Exclusion: status = "not_violated", patient_value = "Not applicable -- [reason]"
- Inclusion: status = "met", patient_value = "Not applicable -- [reason]"
If no patient data exists to evaluate the criterion, that is "not_evaluable".
If patient data EXISTS and CONTRADICTS a criterion, that is "not_met" (inclusion) or "violated" (exclusion) with the actual patient data as patient_value — never "Not applicable".

=====================================================================
SECTION 2 -- SCOPE LIMITATION
=====================================================================

{scope_limitation}

=====================================================================
SECTION 3 -- CRITERION EVALUATION ORDER
=====================================================================

Evaluate each trial's criteria one at a time, in order received, in complete isolation from other trials. Reset reasoning completely before each new trial.

RULE 1 -- DATA AVAILABILITY (MANDATORY FIRST STEP, GATES ALL OTHER RULES)

Search the patient record for data addressing the same clinical concept as this criterion.

If the criterion contains AND-joined components (requires multiple conditions simultaneously):
    Check each component independently.
    If ANY component has no data in the patient record:
        classification = "not_evaluable" for the entire criterion.
        Stop. Do not evaluate the components that are documented.

If the criterion is a single requirement:
    If no relevant data exists in the patient record:
        classification = "not_evaluable"
        Stop. Do not proceed to any other rule.

A documented diagnosis satisfies any "histologically confirmed" or "cytologically confirmed" or "pathologically confirmed" qualifier attached to it. A diagnosis cannot exist without some form of clinical confirmation. Do not classify as "not_met" because the confirmation method is not separately documented.

This rule gates all subsequent rules. If Rule 1 produces "not_evaluable", no other rule may override it.

RULE 2 -- MEDICATION INTERPRETATION

If relevant data is a MEDICATION, check its status:

ACTIVE / ON-HOLD / no status documented:
    Treat as current therapy.

COMPLETED / STOPPED / CANCELLED:
    Treat as historical therapy. Use end date for temporal reasoning.

Completion of therapy does NOT indicate:
- treatment failure
- disease progression
- intolerance
- response

If a criterion requires a specific treatment outcome and the patient record documents only the treatment without the outcome:
    classification = "not_evaluable"

RULE 3 -- CLINICAL TERMINOLOGY MATCHING

When the patient record and criterion use different terminology:

Synonyms or child-to-parent match:
    Acceptable. Proceed.

Parent-to-child match:
    Not sufficient. classification = "not_evaluable"

Sibling conditions:
    Treat as different. classification = "not_evaluable"

Categorically different diseases:
    inclusion -> "not_met"
    exclusion -> "not_violated"

RULE 4 -- TEMPORAL REASONING

Reference date: {get_age_reference_date().isoformat()}

If the criterion contains a time window:
    If event end date is known: calculate elapsed time.
    If event end date is unknown: classification = "not_evaluable"

If the criterion uses past-tense wording ("history of", "prior", "previous"):
    Any documented occurrence (past or present) satisfies the criterion.
    Affirming ("history of X"): if documented -> "met"/"violated". If not -> "not_evaluable".
    Negating ("no prior X"): if documented -> "not_met"/"not_violated". If not -> "not_evaluable".

If the criterion requires an active/current condition:
    Resolved/inactive/in remission: inclusion -> "not_evaluable"; exclusion -> "not_violated".
    No resolution documented: inclusion -> "met"; exclusion -> "not_evaluable".
    Explicitly active/recurrence: inclusion -> "met"; exclusion -> "violated".

RULE 5 -- DIRECT CONTRADICTION CHECK

A contradiction requires ALL three conditions:
(a) Same clinical attribute, same temporal context.
(b) Clinically incompatible values (not merely different terminology or specificity).
(c) Unambiguous -- no reasonable interpretation resolves the conflict.

If all three: "not_met" (inclusion) or "violated" (exclusion).
If ANY uncertainty: classification = "not_evaluable"

RULE 6 -- OR-JOINED CRITERIA

If a criterion contains OR-connected branches:
    If ANY branch is satisfied: "met" / "violated"
    If ALL branches are explicitly contradicted: "not_met" / "not_violated"
    If ANY branch is not_evaluable: classification = "not_evaluable"

RULE 7 -- DEFAULT

If no rule produced a classification:
    classification = "not_evaluable"

=====================================================================
SECTION 4 -- BIOMARKERS AND MOLECULAR DATA
=====================================================================

Missing biomarker or molecular testing is NEVER disqualifying.

This includes but is not limited to: EGFR, PD-L1, HER2, KRAS, BRAF, ALK, ROS1, MSI-H, dMMR, BRCA, PIK3CA, DLL3, CALR, tumor mutational burden, and any other genomic or molecular assay.

If the patient record does not contain the biomarker result:
    classification = "not_evaluable"

=====================================================================
SECTION 5 -- OUTPUT FORMAT
=====================================================================

Return ONLY a valid JSON array. No markdown fences. No text outside the array.
Evaluate ALL {len(trials)} trials in one JSON array.

Fields MUST appear in this exact order:
trial_number, nct_id, match_score, inclusion_criteria, exclusion_criteria, explanation, eligible

match_score: always 0.0

inclusion_criteria and exclusion_criteria:
    For ALL trials (both "eligible" and "not_eligible"): list ALL evaluated criteria with criterion, patient_value, status.
    For "not_evaluable" trials only: both arrays are empty.
    Every status MUST come from that criterion's own vocabulary (Section 1).

patient_value: exact data point/s from patient record, OR "Not in patient record", OR "Not applicable -- [reason]". No interpretive statements.

explanation MUST be written BEFORE eligible and determines it:
    For "eligible" trials: begin with "No known disqualifiers."
    For "not_eligible" trials: begin with "Known disqualifier:" then quote the specific patient data.
    For "not_evaluable" trials: begin with "Not evaluable:" then state what was missing from the trial's criteria text.

JSON template:
[
  {{
    "trial_number": 1,
    "nct_id": "NCT12345678",
    "match_score": 0.0,
    "inclusion_criteria": [
      {{"criterion": "Age 18-75", "patient_value": "62", "status": "met"}},
      {{"criterion": "ECOG 0-1", "patient_value": "Not in patient record", "status": "not_evaluable"}}
    ],
    "exclusion_criteria": [
      {{"criterion": "Active autoimmune disease", "patient_value": "Not in patient record", "status": "not_evaluable"}}
    ],
    "explanation": "No known disqualifiers. Age confirmed. ECOG and autoimmune status not documented.",
    "eligible": "eligible"
  }},
  {{
    "trial_number": 2,
    "nct_id": "NCT87654321",
    "match_score": 0.0,
    "inclusion_criteria": [
      {{"criterion": "Adequate renal function (creatinine ≤ 1.5 x ULN)", "patient_value": "Creatinine: 3.4 mg/dL", "status": "not_met"}},
      {{"criterion": "ECOG 0-1", "patient_value": "Not in patient record", "status": "not_evaluable"}}
    ],
    "exclusion_criteria": [
      {{"criterion": "Active hepatitis B", "patient_value": "Not in patient record", "status": "not_evaluable"}}
    ],
    "explanation": "Known disqualifier: Creatinine 3.4 mg/dL contradicts inclusion criterion requiring creatinine ≤ 1.5 x ULN.",
    "eligible": "not_eligible"
  }}
]

=====================================================================
SECTION 6 -- ABSOLUTE CONSTRAINTS
=====================================================================

C1 -- NO FABRICATION: The patient record is the ONLY source of patient information.

C2 -- NO TRIAL INFERENCE: Evaluate only what is written in the trial criteria. Do not apply standard oncology requirements unless explicitly stated in the criteria.

C3 -- EXCLUSION CONSERVATISM: "violated" requires explicit positive evidence the patient HAS the excluded condition.

C4 -- TRIAL ISOLATION: Each trial evaluated independently. Never carry reasoning across trials.

C5 -- CONSERVATISM UNDER UNCERTAINTY: Uncertainty ALWAYS resolves to "not_evaluable". Never resolve uncertainty toward disqualification.

=====================================================================
FINAL REMINDER
=====================================================================

A trial can ONLY be classified "not_eligible" if you can quote explicit patient evidence that contradicts a trial criterion. If the patient record does not contain that evidence, the criterion status MUST be "not_evaluable".
"""


# ================================================================
# USER MESSAGE
# ================================================================

    def _user_prompt_for(chunk: List[Dict]) -> str:
        return f"""
PATIENT RECORD:
{patient_summary}

CLINICAL TRIALS:
{_build_trials_text(chunk)}
"""

    # ── Store full prompt for DB logging (system + user combined) ──────────
    # The WHOLE batch, not the chunk that happened to be sent last. When a run
    # splits, the stored prompt is the one the run would have sent unsplit,
    # which is the thing that is comparable across runs; the split itself is
    # recorded in gpt4o_truncation_splits, not by mutating this.
    prompt = f"[SYSTEM]\n{system_prompt}\n\n[USER]\n{_user_prompt_for(trials)}"

    # ------------------------------------------------------------------
    # Proactive: split before sending if the batch is expected to overflow
    # ------------------------------------------------------------------
    estimated_output = estimate_output_tokens(trials)
    split_threshold = int(MATCHING_MAX_TOKENS * MATCHING_OUTPUT_SPLIT_FRACTION)

    pending = []          # LIFO of (chunk, split_depth), so a split is depth-first
    proactive_splits = 0
    initial_chunks = [trials]
    if estimated_output > split_threshold:
        depth = 0
        while depth < MAX_TRUNCATION_SPLITS and any(
            estimate_output_tokens(c) > split_threshold and len(c) > 1
            for c in initial_chunks
        ):
            halved = []
            for chunk in initial_chunks:
                if estimate_output_tokens(chunk) > split_threshold and len(chunk) > 1:
                    left, right = _split_in_half(chunk)
                    halved.extend([left, right])
                    proactive_splits += 1
                else:
                    halved.append(chunk)
            initial_chunks = halved
            depth += 1
        print(f"  [Pre-split] estimate {estimated_output} tokens > threshold "
              f"{split_threshold} — sending {len(initial_chunks)} chunk(s) "
              f"instead of 1")
        pending = [(c, depth) for c in reversed(initial_chunks)]
    else:
        pending = [(trials, 0)]

    # ------------------------------------------------------------------
    # Evaluate, splitting reactively on finish_reason == "length"
    # ------------------------------------------------------------------
    evaluations = []
    unevaluable = []              # trials accounted for without a verdict
    truncation_splits = proactive_splits
    truncations_observed = 0
    input_tokens = 0
    output_tokens = 0
    # Reasoning tokens are a SUBSET of output_tokens, never an addition to it.
    # Verified live 2026-08-04: usage.completion_tokens minus
    # usage.completion_tokens_details.reasoning_tokens tracks the visible
    # content length across effort levels (52/0, 133/72, 147/98, 123/68 for
    # none/low/medium/high on the same prompt). OpenAI's reasoning guide states
    # the same. So this is recorded as a BREAKDOWN of output_tokens and must
    # never be added to it for costing -- doing so would bill every reasoning
    # token twice.
    reasoning_tokens = 0
    reasoning_tokens_reported = False   # any response carried the breakdown
    # The model string the API ANSWERED with, as opposed to MATCHING_MODEL,
    # which is what was asked for. They differ whenever an alias resolves to a
    # dated snapshot (gpt-4o-2024-08-06 is one). Last writer wins across a split
    # batch, which is correct: every chunk of one Stage 5 goes to the same
    # model, and if a provider ever routed them differently the last value is
    # still a model that genuinely served this run rather than a config string
    # that may have served none of it.
    model_answered = None
    response_text = ""
    calls_made = 0
    _finish_reason_warned = False

    while pending:
        chunk, depth = pending.pop()

        try:
            response = call_matching_model(system_prompt, _user_prompt_for(chunk))
            choice = response.choices[0]
            chunk_text = (choice.message.content or "").strip()
        except Exception as e:
            # API-level failure (timeout, rate limit, network error). This is
            # the parse/API budget, not the split budget.
            elapsed = time.time() - start
            error_msg = f"GPT-4o API error (attempt {retry_count + 1}): {str(e)}"
            print(f"  ERROR: {error_msg}")
            return {
                "evaluations": [],
                "gpt4o_retries": retry_count + 1,
                "gpt4o_truncation_splits": truncation_splits,
                "gpt4o_output_tokens_estimated": estimated_output,
                "gpt4o_raw_response": "",
                "error": error_msg,
                "stage_timings": {**state.get("stage_timings", {}), "gpt4o_evaluation": round(prior_gpt4o_time + elapsed, 3)}
            }

        calls_made += 1
        input_tokens += response.usage.prompt_tokens
        output_tokens += response.usage.completion_tokens
        response_text = chunk_text

        # Read defensively for the same reason finish_reason is below: File 37
        # drives this node with a stub response and File 46 serves recordings
        # made before the field existed. Absent is reported as NULL, never as
        # 0 -- "this response carried no reasoning breakdown" is not "this
        # response spent no reasoning tokens", and a non-reasoning model
        # legitimately reports 0.
        _usage_details = getattr(response.usage, "completion_tokens_details", None)
        _reasoning = getattr(_usage_details, "reasoning_tokens", None)
        if _reasoning is not None:
            reasoning_tokens += _reasoning
            reasoning_tokens_reported = True

        # The model that ANSWERED, checked against the one requested BEFORE its
        # verdicts are parsed or accumulated. Placed here rather than at logging
        # time so it fires on the first call of the first patient: a mismatch
        # discovered at log time has already spent a whole batch on the wrong
        # judge. See MatchingModelMismatchError for why this raises.
        #
        # None means the response carried no model field (a stub, or a
        # pre-migration recording). That is a different condition and falls
        # through to the existing NULL handling untouched.
        _model_returned = getattr(response, "model", None)
        if _model_returned is not None and _model_returned != MATCHING_MODEL:
            raise MatchingModelMismatchError(MATCHING_MODEL, _model_returned)

        model_answered = _model_returned or model_answered

        # finish_reason is read defensively because not every client object
        # that reaches here carries one: the retrieval-observability test
        # (File 37) drives this node with a stub response, and a stub is
        # exactly the case where silently assuming "truncated" would be wrong.
        #
        # Absent is treated as "not truncated", which is the behaviour this
        # node had before truncation was detected at all — so a client that
        # does not report it degrades to the old path rather than to a new
        # one. It is announced rather than assumed: without the field there is
        # no truncation detection on this run, and that is worth a line.
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason is None and not _finish_reason_warned:
            print("  WARNING: the response object carries no finish_reason; "
                  "truncation cannot be detected on this run. Falling back to "
                  "JSON-parse failure as the only signal.")
            _finish_reason_warned = True

        # ── Reactive: the response was cut off, not malformed ──────────────
        #
        # Read from finish_reason rather than inferred from a token count. The
        # previous guard compared output_tokens against 12000, printed a cost
        # warning, and let the truncated text fall through to json.loads, which
        # failed and retried an IDENTICAL request that truncated again — three
        # times, then an error result. finish_reason is the API stating the
        # fact directly.
        if finish_reason == FINISH_REASON_LENGTH:
            truncations_observed += 1

            if len(chunk) == 1:
                # THE FLOOR. One trial, still over the ceiling; there is
                # nothing left to halve. Recorded, not retried and not dropped.
                print(f"  TRUNCATION FLOOR: {chunk[0]['trial']['nct_id']} alone "
                      f"exceeds the output ceiling. Recording as not evaluable.")
                unevaluable.append(
                    _unevaluable_entry(chunk[0], NOT_EVALUABLE_TRUNCATION_FLOOR)
                )
                continue

            if depth >= MAX_TRUNCATION_SPLITS:
                print(f"  TRUNCATION: split budget exhausted at depth {depth}; "
                      f"recording {len(chunk)} trial(s) as not evaluable.")
                unevaluable.extend(
                    _unevaluable_entry(t, NOT_EVALUABLE_SPLIT_BUDGET)
                    for t in chunk
                )
                continue

            left, right = _split_in_half(chunk)
            truncation_splits += 1
            print(f"  TRUNCATION at depth {depth}: {len(chunk)} trial(s) -> "
                  f"{len(left)} + {len(right)}, retrying as two calls "
                  f"(split {truncation_splits})")
            # Pushed right-then-left so the LIFO pops left first and the
            # evaluation order stays the batch's original order.
            pending.append((right, depth + 1))
            pending.append((left, depth + 1))
            continue

        # Clean markdown fences if present
        if chunk_text.startswith("```"):
            chunk_text = chunk_text.split("```")[1]
            if chunk_text.startswith("json"):
                chunk_text = chunk_text[4:]
            chunk_text = chunk_text.strip()

        # Parse JSON response
        try:
            parsed = json.loads(chunk_text)
        except json.JSONDecodeError as e:
            # JSON parse failure: set error for the retry router. Separate
            # budget from the splits above -- a malformed answer is not a long
            # one, and re-sending is the right response to it.
            elapsed = time.time() - start
            error_msg = f"GPT-4o JSON parse error (attempt {retry_count + 1}): {str(e)}"
            print(f"  ERROR: {error_msg}")
            print(f"  Response preview: {chunk_text[:300]}")
            return {
                "evaluations": [],
                "gpt4o_retries": retry_count + 1,
                "gpt4o_truncation_splits": truncation_splits,
                "gpt4o_output_tokens_estimated": estimated_output,
                "gpt4o_raw_response": chunk_text,
                # A model DID answer here -- badly, but it answered -- so the
                # run is not anonymous. Carried so that a patient whose retries
                # all end in malformed JSON still logs which model produced
                # them instead of a NULL that reads as "Stage 5 never ran".
                # The token counters are deliberately not carried: they are not
                # accumulated on this path at all (a pre-existing gap), and
                # reporting a reasoning subtotal against a zero output total
                # would be arithmetically incoherent.
                "matching_model": model_answered,
                "error": error_msg,
                "stage_timings": {**state.get("stage_timings", {}), "gpt4o_evaluation": round(prior_gpt4o_time + elapsed, 3)}
            }

        if not isinstance(parsed, list):
            elapsed = time.time() - start
            error_msg = f"GPT-4o returned non-list JSON (type={type(parsed).__name__})"
            print(f"  ERROR: {error_msg}")
            print(f"  Response preview: {chunk_text[:300]}")
            return {
                "evaluations": [],
                "gpt4o_retries": retry_count + 1,
                "gpt4o_truncation_splits": truncation_splits,
                "gpt4o_output_tokens_estimated": estimated_output,
                "gpt4o_raw_response": chunk_text,
                # A model DID answer here -- badly, but it answered -- so the
                # run is not anonymous. Carried so that a patient whose retries
                # all end in malformed JSON still logs which model produced
                # them instead of a NULL that reads as "Stage 5 never ran".
                # The token counters are deliberately not carried: they are not
                # accumulated on this path at all (a pre-existing gap), and
                # reporting a reasoning subtotal against a zero output total
                # would be arithmetically incoherent.
                "matching_model": model_answered,
                "error": error_msg,
                "stage_timings": {**state.get("stage_timings", {}), "gpt4o_evaluation": round(prior_gpt4o_time + elapsed, 3)}
            }

        evaluations.extend(parsed)

    if truncations_observed:
        print(f"  [Truncation] {truncations_observed} response(s) hit the "
              f"{MATCHING_MAX_TOKENS}-token ceiling; {truncation_splits} split(s) "
              f"performed across {calls_made} call(s)")

    # ── Estimate against actual, so the calibration can be tightened ───────
    # Logged every run, not only when it matters. The constants in File 03 were
    # derived from this column pair on 1,094 historical rows; recording the
    # estimate beside the outcome is what lets the next derivation be better.
    # The reasoning share is printed beside the total rather than added to it,
    # so the line reads the way the billing does: one output figure, with the
    # invisible part of it named.
    _reasoning_note = (
        f", of which {reasoning_tokens} reasoning"
        if reasoning_tokens_reported else
        ", reasoning share not reported"
    )
    print(f"  [Output tokens] estimated {estimated_output}, actual "
          f"{output_tokens}{_reasoning_note} across {calls_made} call(s) "
          f"(ratio {output_tokens / estimated_output:.2f})"
          if estimated_output else
          f"  [Output tokens] actual {output_tokens}{_reasoning_note}")

    # SUCCESS: enrich evaluations with trial metadata (title, phase)
    for eval_result in evaluations:
        nct_id = eval_result.get("nct_id", "")
        for trial_obj in trials:
            if trial_obj["trial"]["nct_id"] == nct_id:
                eval_result["title"] = trial_obj["trial"].get("title", "No title")
                eval_result["phase"] = trial_obj["trial"].get("phase", "N/A")
                break
    
    # ── Inline parsing: normalize labels, consistency check, recompute score ──
    #
    # ORDER IS LOAD-BEARING. Criterion-label normalization runs FIRST, on every
    # trial, on every trial-level branch. Only then is the disqualification
    # check applied.
    #
    # The disqualification check scans one vocabulary per arm: "not_met" on
    # inclusions, "violated" on exclusions. A cross-vocabulary label -- e.g.
    # "violated" written on an INCLUSION criterion -- is matched by neither
    # scan. Running the check first therefore let such a trial pass as
    # "eligible" while the criterion was stored with a disqualifying label:
    # a record that is internally contradictory and a clinical false positive.
    # Normalizing first removes that state entirely.

    # Per-arm vocabularies (Section 1 of the system prompt). Disjoint by
    # construction, so a status from the wrong list is not a disguised
    # disqualifier -- it is uninterpretable output.
    _INCLUSION_STATUSES = frozenset({"met", "not_met", "not_evaluable"})
    _EXCLUSION_STATUSES = frozenset({"not_violated", "violated", "not_evaluable"})

    _TRIAL_LEVEL_LABELS = ("eligible", "not_eligible", "not_evaluable")

    label_remaps = []       # audit log: criterion labels outside their vocabulary
    unevaluable_trials = []  # audit log: trials that could not be evaluated

    # ── Not-applicable criteria are scored by neither party ──────────────────
    #
    # Section 3 of the system prompt maps a criterion whose subject matter is
    # biologically impossible for this patient onto "not_violated" (exclusion)
    # or "met" (inclusion). Those are the same labels a genuinely confirmed
    # criterion carries, so a naive confirmed/total ratio counts them as
    # evidence of fit.
    #
    # In oncology, inapplicable exclusions are near-universal and sex-linked:
    # pregnancy, lactation, and contraception criteria appear on most trials
    # and resolve to "not_violated" for every male patient. Counting them
    # inflates match_score for male patients over female patients on the SAME
    # trial, from criteria neither patient was actually evaluated against.
    #
    # A criterion that cannot apply is evidence of nothing. It is removed from
    # the numerator AND the denominator, so the score reports the fraction of
    # applicable criteria that were confirmed.
    _NOT_APPLICABLE_PREFIX = "not applicable"

    def _is_not_applicable_patient_value(pv) -> bool:
        """True if patient_value marks the criterion as inapplicable to this patient."""
        if not isinstance(pv, str):
            return False
        return pv.strip().lower().startswith(_NOT_APPLICABLE_PREFIX)

    not_applicable_excluded = []  # audit log: criteria dropped from scoring

    def _compute_match_score(inc, exc, nct_id):
        """
        Recompute match_score over APPLICABLE criteria only.

        Returns (score, confirmed, denominator, n_not_applicable). A trial whose
        every criterion is inapplicable has denominator 0 and scores 0.0: no
        applicable criterion was confirmed, so nothing supports a positive score.
        Every excluded criterion is appended to `not_applicable_excluded`.
        """
        confirmed = 0
        denominator = 0
        n_na = 0

        for arm, criteria, confirming_status in (
            ("inclusion", inc, "met"),
            ("exclusion", exc, "not_violated"),
        ):
            for c in criteria:
                pv = c.get("patient_value", "")
                if _is_not_applicable_patient_value(pv):
                    n_na += 1
                    not_applicable_excluded.append({
                        "nct_id": nct_id,
                        "arm": arm,
                        "criterion": str(c.get("criterion", ""))[:200],
                        "patient_value": pv,
                        "status": c.get("status", ""),
                    })
                    continue
                denominator += 1
                if c.get("status") == confirming_status:
                    confirmed += 1

        score = round(confirmed / denominator, 2) if denominator > 0 else 0.0
        return score, confirmed, denominator, n_na

    def _record_score(eval_result, inc, exc, nct_id):
        """Write match_score and its provenance fields onto one evaluation."""
        score, confirmed, denominator, n_na = _compute_match_score(inc, exc, nct_id)
        eval_result["match_score"] = score
        eval_result["score_confirmed"] = confirmed
        eval_result["score_denominator"] = denominator
        eval_result["criteria_not_applicable"] = n_na
        if denominator == 0:
            print(
                f"  [Validator] {nct_id or '(no NCT ID)'}: all "
                f"{n_na} criterion(s) inapplicable to this patient -- "
                f"match_score 0.0 over an empty denominator."
            )
        return score

    def _record_zero_score(eval_result, inc, exc):
        """
        Score fields for a trial whose score is 0.0 by verdict, not by ratio
        (rejected, or not evaluated). Denominator/confirmed are still recorded
        so a zero score is distinguishable from an unscored one downstream.
        """
        eval_result["match_score"] = 0.0
        eval_result["score_confirmed"] = 0
        eval_result["score_denominator"] = 0
        eval_result["criteria_not_applicable"] = sum(
            1 for c in list(inc) + list(exc)
            if isinstance(c, dict)
            and _is_not_applicable_patient_value(c.get("patient_value", ""))
        )

    def _normalize_arm(criteria, allowed, arm, nct_id):
        """
        Coerce every criterion in one arm into that arm's vocabulary.

        A status outside `allowed` resolves to "not_evaluable" rather than to
        the nearest same-meaning label in the correct vocabulary. Guessing the
        model's intent would let an unparseable label disqualify a patient with
        no quotable evidence behind it, which constraint C5 forbids.

        Non-dict entries are dropped: nothing downstream can read them.

        Returns the cleaned list. Every change is appended to `label_remaps`.
        """
        cleaned = []
        for c in criteria:
            if not isinstance(c, dict):
                label_remaps.append({
                    "nct_id": nct_id,
                    "arm": arm,
                    "criterion": str(c)[:200],
                    "original_status": None,
                    "corrected_status": None,
                    "reason": "criterion entry is not an object -- dropped",
                })
                continue

            status = c.get("status", "")
            if status not in allowed:
                label_remaps.append({
                    "nct_id": nct_id,
                    "arm": arm,
                    "criterion": str(c.get("criterion", ""))[:200],
                    "original_status": status,
                    "corrected_status": "not_evaluable",
                    "reason": f"status not in {arm} vocabulary",
                })
                c["status"] = "not_evaluable"

            cleaned.append(c)
        return cleaned

    for eval_result in evaluations:
        nct_id = eval_result.get("nct_id", "")

        # Normalize unexpected trial-level labels
        if eval_result.get("eligible") not in _TRIAL_LEVEL_LABELS:
            eval_result["eligible"] = "not_eligible"

        inc = eval_result.get("inclusion_criteria", [])
        exc = eval_result.get("exclusion_criteria", [])
        inc = inc if isinstance(inc, list) else []
        exc = exc if isinstance(exc, list) else []

        # ── Step 1: label normalization ─────────────────────────────────────
        # Unconditional. Runs before the verdict logic and on all three
        # trial-level branches, so no branch can store an out-of-vocabulary
        # criterion status.
        remaps_before = len(label_remaps)
        inc = _normalize_arm(inc, _INCLUSION_STATUSES, "inclusion", nct_id)
        exc = _normalize_arm(exc, _EXCLUSION_STATUSES, "exclusion", nct_id)
        eval_result["inclusion_criteria"] = inc
        eval_result["exclusion_criteria"] = exc
        remapped_here = len(label_remaps) > remaps_before

        total = len(inc) + len(exc)

        # ── Step 2: no criteria returned ────────────────────────────────────
        # A trial the model returned with no criteria at all was not evaluated.
        # That is NOT a rejection: recording it as "not_eligible" reports a
        # verdict the model never reached. It gets its own trial-level outcome
        # so non-evaluation is counted instead of masquerading as a rejection.
        if total == 0:
            if eval_result["eligible"] != "not_evaluable":
                unevaluable_trials.append({
                    "nct_id": nct_id,
                    "original_label": eval_result["eligible"],
                    "reason": "model returned no criteria",
                })
            eval_result["eligible"] = "not_evaluable"
            _record_zero_score(eval_result, inc, exc)
            continue

        # ── Step 3: disqualification check, on normalized labels ────────────
        has_not_met = any(c.get("status") == "not_met" for c in inc)
        has_violated = any(c.get("status") == "violated" for c in exc)

        if has_not_met or has_violated:
            eval_result["eligible"] = "not_eligible"
            _record_zero_score(eval_result, inc, exc)

        elif eval_result["eligible"] == "eligible":
            # Legitimate eligible: recompute match_score over applicable criteria
            _record_score(eval_result, inc, exc, nct_id)

        elif eval_result["eligible"] == "not_eligible" and remapped_here:
            # The model rejected this trial, but every disqualifying label it
            # wrote was out of vocabulary and Step 1 resolved them all away.
            # Keeping "not_eligible" would store a rejection with nothing left
            # to justify it; promoting to "eligible" would assert a match the
            # model never made. Neither verdict is supported, so the trial is
            # recorded as not evaluated.
            unevaluable_trials.append({
                "nct_id": nct_id,
                "original_label": "not_eligible",
                "reason": "sole disqualifier was an out-of-vocabulary label",
            })
            eval_result["eligible"] = "not_evaluable"
            _record_zero_score(eval_result, inc, exc)

        else:
            # Model-declared "not_eligible" with no surviving disqualifier and
            # no remap, or model-declared "not_evaluable" with criteria present.
            # Verdict left as the model wrote it.
            _record_zero_score(eval_result, inc, exc)

    if label_remaps:
        print(
            f"  [Validator] Remapped {len(label_remaps)} out-of-vocabulary criterion "
            f"label(s) to not_evaluable across "
            f"{len(set(r['nct_id'] for r in label_remaps))} trial(s)."
        )
    if unevaluable_trials:
        print(
            f"  [Validator] {len(unevaluable_trials)} trial(s) recorded as "
            f"not_evaluable (not rejections): "
            f"{', '.join(sorted(set(t['reason'] for t in unevaluable_trials)))}."
        )


    # ── Absent-data validator: catch GPT-4o absent-data disqualifications ──
    #
    # GPT-4o sometimes classifies criteria as "not_met" or "violated" when
    # the patient record contains no relevant data (absent-data error).
    # This deterministic post-processor detects and corrects these errors
    # by checking the patient_value field of every disqualifying criterion.
    #
    # A criterion with patient_value indicating absent data and a
    # disqualifying status is a provable contradiction: you cannot
    # contradict a criterion without possessing the relevant data.
    #
    # After correction, the trial-level verdict is re-evaluated.
    # All corrections are logged for auditability.
 
    # Canonical phrases GPT-4o uses when patient data is absent.
    # Matched case-insensitively after stripping whitespace.
    _ABSENT_VALUE_EXACT = frozenset({
        "not in patient record",
        "not in the patient record",
        "not available in patient record",
        "not available in the patient record",
        "not documented",
        "not documented in patient record",
        "not documented in the patient record",
        "no data available",
        "no data",
        "unknown",
        "not available",
        "none documented",
        "none recorded",
        "no record",
        "no record available",
        "not reported",
        "not reported in patient record",
        "absent",
        "n/a",
    })
 
    # Prefix patterns: patient_value starts with these (case-insensitive).
    _ABSENT_VALUE_PREFIXES = (
        "not in patient",
        "not in the patient",
        "not documented",
        "not available",
        "no documented evidence",
        "no evidence",
        "no record of",
        "no data",
        "none on record",
    )
 
    def _is_absent_patient_value(pv: str) -> bool:
        """Return True if patient_value indicates absent/missing data."""
        normalized = pv.strip().lower()
        if not normalized:
            return True  # empty string = no data
        if normalized in _ABSENT_VALUE_EXACT:
            return True
        for prefix in _ABSENT_VALUE_PREFIXES:
            if normalized.startswith(prefix):
                return True
        return False
 
    absent_data_corrections = []  # audit log
 
    for eval_result in evaluations:
        if eval_result.get("eligible") != "not_eligible":
            continue
 
        inc = eval_result.get("inclusion_criteria", [])
        exc = eval_result.get("exclusion_criteria", [])
 
        # Skip trials with no criteria (should not happen with early
        # termination removed, but defensive).
        if not inc and not exc:
            continue
 
        corrected_any = False
 
        # Scan inclusion criteria
        for criterion in inc:
            status = criterion.get("status", "")
            pv = criterion.get("patient_value", "")
            if status == "not_met" and _is_absent_patient_value(pv):
                absent_data_corrections.append({
                    "nct_id": eval_result.get("nct_id", ""),
                    "criterion": criterion.get("criterion", "")[:200],
                    "original_status": status,
                    "patient_value": pv,
                    "corrected_status": "not_evaluable",
                    "reason": "patient_value indicates absent data",
                })
                criterion["status"] = "not_evaluable"
                corrected_any = True
 
        # Scan exclusion criteria
        for criterion in exc:
            status = criterion.get("status", "")
            pv = criterion.get("patient_value", "")
            if status == "violated" and _is_absent_patient_value(pv):
                absent_data_corrections.append({
                    "nct_id": eval_result.get("nct_id", ""),
                    "criterion": criterion.get("criterion", "")[:200],
                    "original_status": status,
                    "patient_value": pv,
                    "corrected_status": "not_evaluable",
                    "reason": "patient_value indicates absent data",
                })
                criterion["status"] = "not_evaluable"
                corrected_any = True
 
        # Re-evaluate trial-level verdict after corrections
        if corrected_any:
            remaining_not_met = any(
                c.get("status") == "not_met" for c in inc
            )
            remaining_violated = any(
                c.get("status") == "violated" for c in exc
            )
 
            if not remaining_not_met and not remaining_violated:
                # No remaining disqualifiers: flip to eligible
                eval_result["eligible"] = "eligible"
 
                # Recompute match_score over applicable criteria only, by the
                # same rule as the inline validator above.
                _record_score(eval_result, inc, exc, eval_result.get("nct_id", ""))
 
                # Update explanation prefix
                original_explanation = eval_result.get("explanation", "")
                if original_explanation.startswith("Known disqualifier:"):
                    eval_result["explanation"] = (
                        "No known disqualifiers. [Validator corrected absent-data disqualification.] "
                        + original_explanation
                    )
            # else: legitimate disqualifiers remain, trial stays not_eligible
 
    if absent_data_corrections:
        flipped_trials = sum(
            1 for e in evaluations
            if any(
                c["nct_id"] == e.get("nct_id") and c["corrected_status"] == "not_evaluable"
                for c in absent_data_corrections
            ) and e.get("eligible") == "eligible"
        )
        print(
            f"  [Validator] Corrected {len(absent_data_corrections)} absent-data "
            f"criterion(s) across {len(set(c['nct_id'] for c in absent_data_corrections))} "
            f"trial(s). Flipped {flipped_trials} trial(s) to eligible."
        )

    # ── Report not-applicable exclusions ────────────────────────────────────
    #
    # Counted off the evaluations themselves rather than off
    # `not_applicable_excluded`, because a trial rescored by the absent-data
    # validator passes through _record_score twice and would be double-counted
    # in that append-only audit list.
    _na_total = sum(e.get("criteria_not_applicable", 0) for e in evaluations)
    if _na_total:
        _na_trials = sum(1 for e in evaluations if e.get("criteria_not_applicable", 0))
        _na_empty = sum(
            1 for e in evaluations
            if e.get("criteria_not_applicable", 0) and e.get("score_denominator", 0) == 0
        )
        print(
            f"  [Validator] Excluded {_na_total} not-applicable criterion(s) from "
            f"match_score across {_na_trials} trial(s)"
            + (f"; {_na_empty} trial(s) had no applicable criterion left."
               if _na_empty else ".")
        )

    # ── Reconciliation: every trial that entered Stage 5 must be accounted for ──
    #
    # Three ways a trial can leave this stage without a verdict, and until now
    # none of them was visible: a truncation floor, an exhausted split budget,
    # or the model simply not mentioning it in an otherwise valid response.
    # The first two are collected above; the third is only detectable here, by
    # comparing what came back against what was sent.
    #
    # The invariant this enforces is countable, not vague: after this block,
    # every nct_id in filtered_trials appears exactly once in evaluations.
    evaluations.extend(unevaluable)

    _evaluated_ids = {e.get("nct_id") for e in evaluations}
    _omitted = [t for t in trials if t["trial"]["nct_id"] not in _evaluated_ids]
    if _omitted:
        print(f"  [Reconciliation] {len(_omitted)} trial(s) sent to the model "
              f"came back with no entry; recording as not evaluable: "
              f"{[t['trial']['nct_id'] for t in _omitted]}")
        evaluations.extend(
            _unevaluable_entry(t, NOT_EVALUABLE_MODEL_OMITTED) for t in _omitted
        )

    not_evaluable_truncated = sum(
        1 for e in evaluations
        if e.get("not_evaluable_reason") in (NOT_EVALUABLE_TRUNCATION_FLOOR,
                                             NOT_EVALUABLE_SPLIT_BUDGET)
    )

    # Sort by match score descending
    evaluations.sort(
         key=lambda x: (x.get("match_score", 0), x.get("nct_id", "")),
         reverse=True
     )

    elapsed = time.time() - start
    print(f"[Stage 5] GPT-4o evaluation: {elapsed:.2f}s | {len(evaluations)} trials evaluated")
    print(f"  Scope limitation: relevance "
          f"{'confirmed upstream' if _mesh_filter_applied else 'NOT confirmed'} "
          f"[{_mesh_filter_reason}]")

    return {
        "evaluations": evaluations,
        "gpt4o_retries": retry_count,
        # Two budgets, two counters. gpt4o_retries counts whole-node retries
        # for malformed or failed responses; this counts levels of halving
        # spent because a response was cut off. Sharing one would have failed a
        # patient that hit a single parse error and then needed two splits.
        "gpt4o_truncation_splits": truncation_splits,
        "gpt4o_output_tokens_estimated": estimated_output,
        "not_evaluable_truncated": not_evaluable_truncated,
        "gpt4o_calls": calls_made,
        "gpt4o_raw_response": response_text,
        "gpt4o_prompt": prompt,
        "gpt4o_input_tokens": input_tokens,
        "gpt4o_output_tokens": output_tokens,
        # The reasoning share of gpt4o_output_tokens, NOT an extra charge on top
        # of it. None when no response carried the breakdown -- see the
        # accumulator above.
        "gpt4o_reasoning_tokens": (reasoning_tokens if reasoning_tokens_reported
                                   else None),
        # The model that actually answered. File 14 logs this into
        # inferences.matching_model and prices against it, so the stored cost is
        # computed from the model that produced the tokens rather than from
        # whatever MATCHING_MODEL happens to say at read time.
        "matching_model": model_answered,
        "cross_vocab_remaps": len(label_remaps),
        "error": "",  # Clear error on success
        "stage_timings": {**state.get("stage_timings", {}), "gpt4o_evaluation": round(prior_gpt4o_time + elapsed, 3)}
    }


# ---------------------------------------------------------------------------
# Terminal node identity
# ---------------------------------------------------------------------------
#
# Each terminal node stamps its own name into the result. A reader used to have
# to INFER which one ran from the keys that happened to be present -- a
# non-empty "error" meant the error handler, a "message" key meant no
# candidates -- which is a rule about incidental structure, not about identity.
# Adding a "message" key to node_finalize would have silently made every
# successful run report itself as a no-candidate run.
#
# Values are the function names so the string points at the code that produced
# it. 45-/46- Fixture Capture/Replay read this field; nothing infers it.
TERMINAL_NODE_FINALIZE = "node_finalize"
TERMINAL_NODE_NO_CANDIDATES = "node_no_candidates"
TERMINAL_NODE_ERROR = "node_error_handler"


# ---------------------------------------------------------------------------
# Provenance block shared by the three terminal nodes
# ---------------------------------------------------------------------------
#
# node_finalize, node_no_candidates and node_error_handler each end a run, and
# File 14 logs whichever one produced the result. A key written on only one of
# the three is a column that is populated for a minority of rows and constant
# for the rest — which reads downstream as a signal that never varies.
#
# That was the defect: gpt4o_retries existed only on the error path (so every
# successful inference logged 0 retries no matter how many were spent, and
# File 20's retry drift monitored a constant), ablation_flags was written by no
# terminal node at all (so the production column was '{}' on every row), and
# the per-channel retrieval counts were written nowhere, so File 14 inserted
# BM25_RETRIEVAL_SIZE / VECTOR_RETRIEVAL_SIZE in their place.
#
# Every value is read from state, so a stage that never ran contributes its
# initialized value rather than a fabricated one.

def _pipeline_provenance(state) -> Dict:
    """Run-level provenance keys that all three terminal results must carry."""

    # ECOG is read off state["patient_data"], the same route birth_date_precision
    # takes below, rather than being copied onto state by a node. It is a
    # property of the parsed patient, not something any stage computes, so a
    # second copy on state could only ever disagree with the first. All three
    # terminal nodes already bind state["patient_data"], so the value is
    # reachable on every path including the error path.
    #
    # {} when the key is absent, which is what a hand-built patient dict or a
    # bundle parsed before File 07 grew the field produces. That is deliberately
    # NOT the same as a parsed patient with no observation: the former leaves
    # ecog_selection None, the latter sets it to "none_recorded". File 14's
    # schema comment records the convention.
    _ecog = ((state.get("patient_data") or {}).get("ecog_performance_status") or {})

    return {
        # Retries actually spent in Stage 5. Stage 5 writes the count back into
        # state on its success return and on every failure return, so this is
        # the observed number of API / JSON-parse retries, not a ceiling.
        "gpt4o_retries": state.get("gpt4o_retries", 0),

        # --- Stage 5 truncation record ------------------------------------
        # Defaulted to 0 rather than None, unlike the degradation keys below,
        # and the difference is deliberate: those describe a stage that may
        # never have reported, while these describe work that either happened
        # or did not. A run that ended before Stage 5 genuinely performed zero
        # splits. The estimate is the exception -- it is None when Stage 5
        # never ran, because "we estimated nothing" and "we estimated 0 tokens"
        # are different claims.
        "gpt4o_truncation_splits": state.get("gpt4o_truncation_splits", 0),
        "gpt4o_output_tokens_estimated": state.get("gpt4o_output_tokens_estimated"),
        "not_evaluable_truncated": state.get("not_evaluable_truncated", 0),
        "gpt4o_calls": state.get("gpt4o_calls", 0),

        # --- Which model answered, and what it spent thinking ---------------
        #
        # BOTH BELONG HERE RATHER THAN ON node_finalize. File 14 reads them on
        # every row it writes; a key declared by one terminal node only makes
        # the column populated for a minority of rows and constant for the
        # rest, which is the exact defect this block exists to prevent, and
        # File 36's Test 1 fails for it.
        #
        # matching_model is the string the API ANSWERED with, read off
        # response.model by Stage 5. None means no Stage 5 response was ever
        # obtained -- the run ended at node_no_candidates, or died before the
        # first call returned. That is NOT the same as "it ran on the
        # configured model", which is what logging MATCHING_MODEL here would
        # assert on a run that never made a request. File 14 prices against
        # this value.
        #
        # gpt4o_reasoning_tokens is the reasoning SUBSET of
        # gpt4o_output_tokens, not an addition to it, so it is a breakdown
        # column and never a costing term. None -- not 0 -- when no response
        # carried the breakdown: a stub, a replayed pre-migration fixture, or a
        # run that never reached Stage 5. A non-reasoning model reporting a
        # genuine 0 is a different fact and stays 0.
        "matching_model": state.get("matching_model"),
        "gpt4o_reasoning_tokens": state.get("gpt4o_reasoning_tokens"),
        # Which stages were disabled for this run; {} = full pipeline. Copied
        # rather than aliased so the logged record cannot be mutated later.
        "ablation_flags": dict(state.get("ablation_flags") or {}),
        # Observed Stage 2 channel counts (see TrialMatchState). Absent from
        # state only when the run ended before Stage 2 returned, and 0 is then
        # the true count of what that channel retrieved.
        "bm25_retrieved": state.get("bm25_retrieved", 0),
        "vector_retrieved": state.get("vector_retrieved", 0),

        # The date this run's patient ages and the Stage 5 prompt's temporal
        # reasoning were anchored to. Not read from state: it is a property of
        # the run's configuration, identical on every path including the error
        # path, and it is recorded per run precisely so a stored row can be
        # reproduced without knowing when it was produced. Taken from
        # DATA_SNAPSHOT_DATE (File 03) rather than from the patient dict so it
        # is present even when demographics never parsed.
        "age_reference_date": get_age_reference_date().isoformat(),

        # How much of the patient's birthDate the record carried ("day" =
        # exact age, "month"/"year" = imputed from an anchor, "missing" /
        # "unparseable" / "after_reference" = no age at all). Written by File
        # 07 into demographics; None when the caller built the patient dict by
        # hand, which is not the same as "the date was exact".
        "birth_date_precision": ((state.get("patient_data") or {})
                                 .get("demographics") or {})
                                .get("birth_date_precision"),

        # --- Degradation record (see the vocabularies at the top of this file) ---
        #
        # These four default to None, not to a clean value, and File 14 writes
        # NULL for None. The distinction matters more here than anywhere else
        # in this dict: a run that ended before Stage 2 has no channel outcomes
        # to report, and writing "0 failures" for it would assert the opposite
        # of what happened. A caller reading these must treat NULL as "the
        # stage did not report", never as "nothing went wrong".
        "retrieval_channels": dict(state["retrieval_channels"])
                              if state.get("retrieval_channels") else None,
        "retrieval_channels_expected": state.get("retrieval_channels_expected"),
        "retrieval_channels_ok": state.get("retrieval_channels_ok"),
        "retrieval_degraded": state.get("retrieval_degraded"),
        "retrieval_trials_lost": state.get("retrieval_trials_lost"),

        # Which query Stage 1 handed to retrieval, and whether Stage 4's cancer
        # site filter ran. Both None when the stage that writes them did not
        # complete. mesh_filter_applied is stored as 0/1 by File 14.
        "query_expansion_path": state.get("query_expansion_path"),
        "mesh_filter_applied": state.get("mesh_filter_applied"),
        "mesh_filter_skip_reason": state.get("mesh_filter_skip_reason"),

        # --- ECOG performance status (see File 07) -------------------------
        #
        # The score printed into the Stage 5 prompt, the path that produced it,
        # and how many observations the bundle carried. All three belong in the
        # record of the inference they shaped: ECOG 0-1 or 0-2 gates nearly every
        # interventional oncology trial, so a corpus that resolved entirely to
        # "all_after_reference_date" would match systematically worse with
        # nothing in the row to say why.
        #
        # ecog_value is None both for a patient with no observation and for one
        # whose only observation postdates the snapshot. ecog_selection is what
        # separates them, and ecog_observations_found is what makes the second
        # case countable. Never read absence off ecog_value alone.
        "ecog_value": _ecog.get("value"),
        "ecog_selection": _ecog.get("selection"),
        "ecog_observations_found": _ecog.get("observations_found"),
    }


def node_finalize(state: TrialMatchState) -> dict:
    """
    Stage 6: Assemble final output with pipeline metadata.

    Splits evaluations into three groups based on the trial-level classification:

      matches:        "eligible"      — no known disqualifiers, pre-screening candidate
      near_misses:    "not_eligible"  — explicit disqualifying evidence found
      not_evaluable:  "not_evaluable" — the trial could not be assessed at all

    A "not_evaluable" trial is deliberately kept out of near_misses: it is a
    non-evaluation to be counted, not a rejection to be reported.

    Matches are sorted by match_score descending.
    """

    patient_data = state["patient_data"]
    evaluations = state.get("evaluations", [])

    # ── Normalize eligible field ─────────────────────────────────────────
    # GPT-4o returns "eligible" / "not_eligible" / "not_evaluable". Handle edge cases.
    _ELIGIBLE_NORM = {
        True:  "eligible",
        False: "not_eligible",
        "true":  "eligible",
        "false": "not_eligible",
        "yes":   "eligible",
        "no":    "not_eligible",
    }

    for e in evaluations:
        raw = e.get("eligible")
        if isinstance(raw, bool):
            e["eligible"] = _ELIGIBLE_NORM[raw]
        elif isinstance(raw, str):
            normalized = raw.strip().lower()
            e["eligible"] = _ELIGIBLE_NORM.get(normalized, normalized)
        # else: leave as-is (will fall through to near_misses)

    # ── Split into matches vs. near-misses vs. non-evaluations ───────────
    _ACTIONABLE = frozenset({"eligible"})
    _UNEVALUABLE = frozenset({"not_evaluable"})

    # Build score lookup from filtered_trials by nct_id.
    # The boosted score, the unboosted score and the boost itself are all
    # carried through so the boost's effect on ranking stays measurable
    # downstream (trial_matches.mesh_boost) instead of being folded away.
    _rerank_lookup = {
        t["trial"]["nct_id"]: (
            t.get("rerank_score", None),
            t.get("rerank_score_raw", None),
            t.get("mesh_boost", 0.0),
            t.get("mesh_boost_tier", "none"),
        )
        for t in state.get("filtered_trials", [])
        if "trial" in t and "nct_id" in t["trial"]
    }

    # Merge scores and trial_number into each evaluation
    for rank_pos, e in enumerate(evaluations, start=1):
        nct_id = e.get("nct_id", "")
        _scores = _rerank_lookup.get(nct_id, (None, None, None, None))
        e["rerank_score"]     = _scores[0]
        e["rerank_score_raw"] = _scores[1]
        e["mesh_boost"]       = _scores[2]
        e["mesh_boost_tier"]  = _scores[3]
        e["trial_number"] = rank_pos

    matches = [e for e in evaluations if e.get("eligible") in _ACTIONABLE]
    not_evaluable = [e for e in evaluations if e.get("eligible") in _UNEVALUABLE]
    near_misses = [
        e for e in evaluations
        if e.get("eligible") not in _ACTIONABLE and e.get("eligible") not in _UNEVALUABLE
    ]

    # Sort matches by match_score descending
    matches.sort(key=lambda e: -e.get("match_score", 0))

    conditions = patient_data.get("conditions", [])
    medications = patient_data.get("medications", [])
    
    result = {
        "patient_id": patient_data["patient_id"],
        "primary_condition": _resolve_primary_cancer(conditions),
        "condition_count": len(deduplicate_by_display(conditions)),
        "medication_count": len(deduplicate_by_display(medications)),
        "allergy_count": len(patient_data.get("allergies", [])),
        "expanded_query": state.get("expanded_query", ""),
        "candidates_retrieved": len(state.get("hybrid_results", [])),
        "candidates_reranked": len(state.get("reranked_trials", [])),
        "candidates_after_rule_filter": state.get("candidates_after_rule_filter", 0),
        "candidates_after_quality_filter": state.get("candidates_after_quality_filter", 0),
        "candidates_filtered": len(state.get("filtered_trials", [])),
        "mesh_dropped": state.get("mesh_dropped", 0),
        "mesh_resolution": state.get("mesh_resolution", ""),
        "stage_dropped": state.get("stage_dropped", 0),
        "histology_dropped": state.get("histology_dropped", 0),
        "candidates_evaluated": len(evaluations),
        # Criteria dropped from match_score because they cannot apply to this
        # patient (Section 3 "Not applicable"). Reported so a score computed
        # over a shrunken denominator is never mistaken for one computed over
        # the full criteria set.
        "criteria_not_applicable": sum(
            e.get("criteria_not_applicable", 0) for e in evaluations
        ),
        "matches": matches,
        "near_misses": near_misses,
        "not_evaluable": not_evaluable,
        "not_evaluable_trials": len(not_evaluable),
        "cross_vocab_remaps": state.get("cross_vocab_remaps", 0),
        "stage_timings": state.get("stage_timings", {}),
        "expansion_prompt": state.get("expansion_prompt", ""),
        "expansion_input_tokens": state.get("expansion_input_tokens", 0),
        "expansion_output_tokens": state.get("expansion_output_tokens", 0),
        "gpt4o_prompt": state.get("gpt4o_prompt", ""),
        "gpt4o_input_tokens": state.get("gpt4o_input_tokens", 0),
        "gpt4o_output_tokens": state.get("gpt4o_output_tokens", 0),
        "timestamp": datetime.now().isoformat(),
        "error": "",
        "patient_data_hash": "",
        # Which node produced this result, stated rather than inferred.
        "terminal_node": TERMINAL_NODE_FINALIZE,
        **_pipeline_provenance(state),
    }

    eligible_count = len(matches)
    print(
        f"[Stage 6] Finalized: {eligible_count} eligible, "
        f"{len(near_misses)} not_eligible, "
        f"{len(not_evaluable)} not_evaluable "
        f"for patient {patient_data['patient_id']}"
    )
    
    return {"result": result}


def node_no_candidates(state: TrialMatchState) -> dict:
    """
    Terminal node: no candidates survived retrieval or filtering.

    Returns a clean result indicating no trials were found,
    rather than wasting a GPT-4o call on an empty candidate set.
    """
    patient_data = state["patient_data"]

    conditions = patient_data.get("conditions", [])
    medications = patient_data.get("medications", [])
    
    result = {
        "patient_id": patient_data["patient_id"],
        "primary_condition": _resolve_primary_cancer(conditions),
        "condition_count": len(deduplicate_by_display(conditions)),
        "medication_count": len(deduplicate_by_display(medications)),
        "allergy_count": len(patient_data.get("allergies", [])),
        "expanded_query": state.get("expanded_query", ""),
        "candidates_retrieved": len(state.get("hybrid_results", [])),
        "candidates_reranked": len(state.get("reranked_trials", [])),
        "candidates_after_rule_filter": state.get("candidates_after_rule_filter", 0),
        "candidates_after_quality_filter": state.get("candidates_after_quality_filter", 0),
        "candidates_filtered": len(state.get("filtered_trials", [])),
        "mesh_dropped": state.get("mesh_dropped", 0),
        "mesh_resolution": state.get("mesh_resolution", ""),
        "stage_dropped": state.get("stage_dropped", 0),
        "histology_dropped": state.get("histology_dropped", 0),
        "candidates_evaluated": 0,
        # No evaluation ran, so no criterion was excluded from a score. Written
        # anyway: the three terminal results declare the same keys, so a
        # consumer never has to know which one produced the row it is reading.
        "criteria_not_applicable": 0,
        "matches": [],
        "near_misses": [],
        "not_evaluable": [],
        "not_evaluable_trials": 0,
        "cross_vocab_remaps": 0,
        "expansion_prompt": state.get("expansion_prompt", ""),
        "expansion_input_tokens": state.get("expansion_input_tokens", 0),
        "expansion_output_tokens": state.get("expansion_output_tokens", 0),
        "gpt4o_prompt": "",
        "gpt4o_input_tokens": 0,
        "gpt4o_output_tokens": 0,
        "message": "No trials passed retrieval or filtering for this patient.",
        "error": "",
        "patient_data_hash": "",
        "terminal_node": TERMINAL_NODE_NO_CANDIDATES,
        "stage_timings": state.get("stage_timings", {}),
        "timestamp": datetime.now().isoformat(),
        **_pipeline_provenance(state),
    }

    print(f"[No Candidates] No matching trials for patient {patient_data['patient_id']}")

    return {"result": result}


def node_error_handler(state: TrialMatchState) -> dict:
    """
    Error terminal node: GPT-4o failed after all retries.

    Packages whatever information is available into a clean error
    response so the caller gets structured output (not a crash).
    """
    patient_data = state["patient_data"]
    error_msg = state.get("error", "Unknown error")

    conditions = patient_data.get("conditions", [])
    medications = patient_data.get("medications", [])
    
    result = {
        "patient_id": patient_data["patient_id"],
        "primary_condition": _resolve_primary_cancer(conditions),
        "condition_count": len(deduplicate_by_display(conditions)),
        "medication_count": len(deduplicate_by_display(medications)),
        "allergy_count": len(patient_data.get("allergies", [])),
        "expanded_query": state.get("expanded_query", ""),
        "candidates_retrieved": len(state.get("hybrid_results", [])),
        "candidates_reranked": len(state.get("reranked_trials", [])),
        "candidates_after_rule_filter": state.get("candidates_after_rule_filter", 0),
        "candidates_after_quality_filter": state.get("candidates_after_quality_filter", 0),
        "candidates_filtered": len(state.get("filtered_trials", [])),
        "mesh_dropped": state.get("mesh_dropped", 0),
        "mesh_resolution": state.get("mesh_resolution", ""),
        "stage_dropped": state.get("stage_dropped", 0),
        "histology_dropped": state.get("histology_dropped", 0),
        "candidates_evaluated": 0,
        "criteria_not_applicable": 0,
        "matches": [],
        "near_misses": [],
        "not_evaluable": [],
        "not_evaluable_trials": 0,
        "cross_vocab_remaps": state.get("cross_vocab_remaps", 0),
        "expansion_prompt": state.get("expansion_prompt", ""),
        "expansion_input_tokens": state.get("expansion_input_tokens", 0),
        "expansion_output_tokens": state.get("expansion_output_tokens", 0),
        "gpt4o_prompt": state.get("gpt4o_prompt", ""),
        "gpt4o_input_tokens": state.get("gpt4o_input_tokens", 0),
        "gpt4o_output_tokens": state.get("gpt4o_output_tokens", 0),
        "error": error_msg,
        "patient_data_hash": "",
        "terminal_node": TERMINAL_NODE_ERROR,
        # Retired key. It said the same thing as gpt4o_retries but existed only
        # on this path, which is how the count came to be logged as 0 for every
        # run that did not end here. Kept as an alias for one release so an
        # external consumer of the API response is not broken by the rename;
        # nothing inside this repo reads it.
        "gpt4o_retries_exhausted": state.get("gpt4o_retries", 0),
        "stage_timings": state.get("stage_timings", {}),
        "timestamp": datetime.now().isoformat(),
        **_pipeline_provenance(state),
    }

    print(f"[ERROR] Pipeline failed for patient {patient_data['patient_id']}: {error_msg}")

    return {"result": result}


# ===========================================================================
# HELPER: Patient Summary (used by Stage 5)
# ===========================================================================


# ---------------------------------------------------------------------------
# Condition Relevance Filter for GPT-4o Prompt
# ---------------------------------------------------------------------------
#
# Three-layer classifier that splits conditions into:
#   Tier A (cancer):      primary cancer conditions (CancerCodeRegistry)
#   Tier B (relevant):    comorbidities that oncology trials gate on, or unknown
#   Tier C (background):  conditions confidently irrelevant to trial eligibility
#
# Layer 1 (ICD-10-CM blocks): systematic, code-based inclusion of trial-relevant
#   organ system categories.
# Layer 2 (SNOMED codes): curated high-level SNOMED concepts for the same
#   categories. Covers Synthea data where ICD-10 may not be present.
# Layer 3 (blacklist keywords): display-text exclusion of conditions that are
#   confidently irrelevant to any oncology trial eligibility criterion.
#   Only fires when Layers 1-2 produce no match.
# Default: Tier B (conservative). If uncertain, include.


# == Layer 1: ICD-10-CM relevant blocks ====================================
# Ranges define ICD-10-CM code blocks where oncology trials commonly have
# exclusion or inclusion criteria. Checked at the alpha + two-digit level.
#
# Source: ICD-10-CM 2024 chapter structure (CMS/NCHS) cross-referenced
# against common oncology trial exclusion criteria categories.
# Each tuple is (start_int, end_int) for the numeric portion after the
# alpha prefix.

_ICD10_RELEVANT_BLOCKS: Dict[str, List[Tuple[int, int]]] = {
    # A00-B99: Infectious/parasitic
    "A": [(15, 19)],                          # Tuberculosis
    "B": [(15, 20)],                          # Viral hepatitis (B15-B19) + HIV (B20)
    # D50-D89: Blood diseases + immune disorders (non-neoplastic)
    "D": [(50, 89)],
    # E00-E13: Thyroid + diabetes; E24-E27: adrenal
    "E": [(0, 13), (24, 27)],
    # F20-F31: Psychotic + bipolar disorders
    "F": [(20, 31)],
    # G40-G41: Epilepsy/seizure; G60-G65: neuropathy
    "G": [(40, 41), (60, 65)],
    # I00-I99: All circulatory (cardiac, vascular, cerebrovascular)
    "I": [(0, 99)],
    # J44-J45: COPD + asthma; J68: pneumonitis; J84: ILD/pulmonary fibrosis
    "J": [(44, 45), (68, 68), (84, 84)],
    # K70-K77: Liver diseases
    "K": [(70, 77)],
    # M05-M06: RA; M30-M36: systemic connective tissue (lupus, scleroderma, vasculitis)
    "M": [(5, 6), (30, 36)],
    # N00-N19: Renal diseases
    "N": [(0, 19)],
    # Z85: Personal history of malignant neoplasm; Z94: transplanted organ status
    "Z": [(85, 85), (94, 94)],
}


def _is_icd10_relevant(code: str) -> bool:
    """
    Check if an ICD-10-CM code falls within a trial-relevant block.
    Handles codes with or without dots.
    """
    normalized = code.upper().replace(".", "").strip()
    if len(normalized) < 3:
        return False

    alpha = normalized[0]
    blocks = _ICD10_RELEVANT_BLOCKS.get(alpha)
    if not blocks:
        return False

    try:
        num = int(normalized[1:3])
    except ValueError:
        return False

    for start, end in blocks:
        if start <= num <= end:
            return True
    return False


# == Layer 2: SNOMED relevant concepts =====================================
# High-level SNOMED CT codes for comorbidity categories that oncology trials
# commonly gate on. Covers Synthea-generated conditions where ICD-10 codes
# may not be present. Not exhaustive; Layer 3 blacklist provides safety net.

_SNOMED_RELEVANT_COMORBIDITIES: FrozenSet[str] = frozenset({
    # Cardiac
    "53741008",    # Coronary arteriosclerosis
    "22298006",    # Myocardial infarction
    "84114007",    # Heart failure
    "49436004",    # Atrial fibrillation
    "38341003",    # Hypertension
    # Diabetes
    "44054006",    # Diabetes mellitus type 2
    "73211009",    # Diabetes mellitus
    "46635009",    # Diabetes mellitus type 1
    # Renal
    "431855005",   # Chronic kidney disease
    "90708001",    # Kidney disease
    # Hepatic
    "19943007",    # Cirrhosis of liver
    "235856003",   # Hepatitis disorder
    "128302006",   # Chronic hepatitis C
    "66071002",    # Hepatitis B
    # Autoimmune / inflammatory
    "85828009",    # Autoimmune disease
    "69896004",    # Rheumatoid arthritis
    "55464009",    # Systemic lupus erythematosus
    "24700007",    # Multiple sclerosis
    "34000006",    # Crohn disease
    "64766004",    # Ulcerative colitis
    # Infectious
    "86406008",    # HIV infection
    "56717001",    # Tuberculosis
    # Hematologic
    "271737000",   # Anemia
    "74576004",    # Thrombocytopenia
    "128053003",   # Deep vein thrombosis
    "59282003",    # Pulmonary embolism
    # Neurologic
    "84757009",    # Epilepsy
    "230690007",   # Cerebrovascular accident (stroke)
    # Pulmonary
    "13645005",    # COPD
    "195967001",   # Asthma
    "700250006",   # Idiopathic pulmonary fibrosis
    # Psychiatric
    "58214004",    # Schizophrenia
    "13746004",    # Bipolar disorder
})


# == Layer 3: Blacklist keywords (display-text exclusion) ==================
# Conditions whose display text contains ANY of these keywords are classified
# as Tier C (background/summarized) when Layers 1-2 produce no match.
#
# These are confidently irrelevant to oncology trial eligibility criteria.
# Conservative: only categories where accidental exclusion of a relevant
# condition is essentially impossible.
#
# IMPORTANT: This is an EXCLUDE list. Conditions NOT matching this list
# default to Tier B (relevant). Missing a keyword here means an irrelevant
# condition stays in Tier B (wastes tokens but is safe). Adding a keyword
# incorrectly means a relevant condition drops to Tier C (unsafe).

_IRRELEVANT_CONDITION_KEYWORDS: FrozenSet[str] = frozenset({
    # Dental / oral hygiene
    "dental caries", "dental", "gingivitis", "periodont", "tooth",
    "caries",
    # Vision (non-drug-related)
    "myopia", "hypermetropia", "hyperopia", "astigmatism", "presbyopia",
    "macular degeneration",
    # Hearing
    "hearing loss", "tinnitus", "otitis media",
    # Dermatologic (cosmetic / non-inflammatory)
    "acne", "seborrheic", "alopecia", "onychomycosis",
    "contact dermatitis", "eczema", "callus", "corn of skin",
    # Musculoskeletal (mechanical / degenerative)
    "osteoarthritis", "osteoporosis", "low back pain",
    "back pain", "sprain", "strain of", "tendinitis",
    "plantar fasciitis", "bunion", "carpal tunnel",
    "rotator cuff", "meniscus",
    # Routine / preventive / administrative
    "immunization", "vaccination", "screening",
    "normal pregnancy", "finding of",
    "well child", "annual exam", "routine checkup", "encounter for",
    # Social / lifestyle (miscoded as conditions)
    "lack of physical exercise", "stress", "body mass index",
    "tobacco use", "smoker", "social isolation",
    "misuses drugs", "unhealthy alcohol",
    # Benign / minor
    "benign prostatic hyperplasia", "hemorrhoids",
    "varicose veins", "gastroesophageal reflux",
    "allergic rhinitis", "seasonal allergic",
    "sinusitis", "pharyngitis", "bronchitis",
    "urinary tract infection", "otitis externa",
    # Reproductive (non-pathological)
    "premenstrual", "menopausal", "menopause",
    "erectile dysfunction",
    # Metabolic (minor, non-trial-gating)
    "vitamin d deficiency", "iron deficiency",
    "hyperlipidemia", "hypercholesterolemia",
    # Pain syndromes
    "headache", "migraine", "fibromyalgia",
})


def _classify_condition_relevance(
    condition: Dict,
    cancer_registry,
) -> str:
    """
    Classify a condition into relevance tiers for GPT-4o prompt construction.

    Layer 0: CancerCodeRegistry -> "cancer" (Tier A)
    Layer 1: ICD-10-CM block check -> "relevant" (Tier B)
    Layer 2: SNOMED code check -> "relevant" (Tier B)
    Layer 3: Blacklist keyword check -> "background" (Tier C)
    Default: -> "relevant" (Tier B, conservative)

    Returns:
        "cancer"     : Tier A, primary cancer condition
        "relevant"   : Tier B, trial-relevant comorbidity or unknown
        "background" : Tier C, confidently irrelevant
    """
    # Layer 0: cancer
    if cancer_registry.is_primary_cancer(condition):
        return "cancer"

    # Gather all codes (backward compatible)
    codings = condition.get("codings", [])
    if not codings:
        code = (condition.get("code") or "").strip()
        if code and code.lower() not in ("unknown", "none"):
            codings = [{"system_key": "unknown", "code": code}]

    # Layer 1: ICD-10-CM block check
    for c in codings:
        if c.get("system_key") in ("icd10cm", "icd10"):
            if _is_icd10_relevant(c["code"]):
                return "relevant"

    # Layer 2: SNOMED code check
    for c in codings:
        code = c.get("code", "")
        if code in _SNOMED_RELEVANT_COMORBIDITIES:
            return "relevant"

    # Layer 3: Blacklist keyword check
    display_lower = (condition.get("display") or "").lower()
    if display_lower:
        for keyword in _IRRELEVANT_CONDITION_KEYWORDS:
            if keyword in display_lower:
                return "background"

    # Default: conservative, keep as relevant (Tier B)
    return "relevant"


_IRRELEVANT_MEDICATION_KEYWORDS: FrozenSet[str] = frozenset({
    # OTC pain / fever (NOT NSAIDs -- ibuprofen/naproxen/aspirin have
    # platelet and bleeding risk implications that some trials gate on)
    "acetaminophen",
    # Vitamins / supplements
    "vitamin", "multivitamin", "folic acid", "iron supplement",
    "fish oil", "omega-3", "zinc sulfate",
    "cholecalciferol", "ergocalciferol", "cyanocobalamin",
    "calcium carbonate", "calcium citrate",
    # Gastrointestinal (non-immunosuppressive)
    "omeprazole", "pantoprazole", "lansoprazole", "esomeprazole",
    "famotidine",
    "simethicone", "docusate", "polyethylene glycol", "bisacodyl",
    "loperamide", "antacid", "laxative", "stool softener",
    "senna", "miralax", "psyllium",
    # Allergy / cold / nasal
    "cetirizine", "loratadine", "fexofenadine", "diphenhydramine",
    "fluticasone nasal", "mometasone nasal", "oxymetazoline",
    "guaifenesin", "dextromethorphan", "saline nasal",
    # Eye care (specific safe agents only, NOT generic "eye drop")
    "artificial tears", "latanoprost", "brimonidine ophthalmic",
    # Dermatologic (topical only, non-systemic)
    "hydrocortisone cream", "moisturizer", "sunscreen",
    "benzoyl peroxide", "clotrimazole topical", "miconazole topical",
    "mupirocin", "bacitracin", "neosporin",
    # Dental / oral hygiene
    "fluoride", "chlorhexidine mouth",
    # Smoking cessation
    "nicotine patch", "nicotine gum", "varenicline",
    # Sleep (OTC)
    "melatonin",
    # Electrolytes / hydration (saline only, NOT potassium)
    "sodium chloride irrigation", "oral rehydration",
})


def _classify_medication_relevance(display: str) -> str:
    """
    Classify a medication as relevant or background for GPT-4o prompt.

    Args:
        display: Medication display name string.

    Returns:
        "relevant"   : Include with full detail (default)
        "background" : Confidently irrelevant, summarize
    """
    display_lower = display.lower()
    for keyword in _IRRELEVANT_MEDICATION_KEYWORDS:
        if keyword in display_lower:
            return "background"
    return "relevant"


# ── Lab Unit Normalization ─────────────────────────────────────────────────
# Converts common alternative units to canonical US clinical units before
# GPT-4o evaluation. Covers only labs in OncologyLabRegistry. Conversion
# factors are clinically validated per LOINC/UCUM standards.
# Raw FHIR data is never modified -- normalization is summary-only.
#
# Canonical units match standard US clinical trial reporting:
#   Creatinine : mg/dL   (SI: µmol/L ÷ 88.42)
#   Hemoglobin : g/dL    (SI: mmol/L × 1.6113, g/L ÷ 10)
#   Bilirubin  : mg/dL   (SI: µmol/L ÷ 17.1)
#   Calcium    : mg/dL   (SI: mmol/L × 4.008)
#   Glucose    : mg/dL   (SI: mmol/L × 18.016)
#   ANC/WBC    : cells/µL (10^3/µL × 1000, 10^9/L × 1000)
#   Platelets  : cells/µL (10^3/µL × 1000, 10^9/L × 1000)
#
# Edge cases:
#   - value/unit None   → original returned unchanged
#   - unrecognized unit → original returned unchanged
#   - float() failure   → caught, original returned unchanged
#   - Synthea data      → already in canonical units, no conversions triggered
# NOTE: substring matching on canonical_display (e.g. "bilirubin" matches both
# "Bilirubin (total)" and "Bilirubin (direct)"). Clinically harmless here
# since both use the same µmol/L → mg/dL factor, but review if new labs added.

_LAB_UNIT_CONVERSIONS: Dict[Tuple[str, str], Tuple[str, Any]] = {
    # Creatinine: µmol/L → mg/dL
    ("creatinine", "µmol/l"):   ("mg/dL", lambda v: round(v / 88.42, 2)),
    ("creatinine", "umol/l"):   ("mg/dL", lambda v: round(v / 88.42, 2)),
    # Hemoglobin: mmol/L → g/dL, g/L → g/dL
    ("hemoglobin", "mmol/l"):   ("g/dL",  lambda v: round(v * 1.6113, 1)),
    ("hemoglobin", "g/l"):      ("g/dL",  lambda v: round(v / 10, 1)),
    # Bilirubin (total + direct share same factor)
    ("bilirubin", "µmol/l"):    ("mg/dL", lambda v: round(v / 17.1, 2)),
    ("bilirubin", "umol/l"):    ("mg/dL", lambda v: round(v / 17.1, 2)),
    # Calcium: mmol/L → mg/dL
    ("calcium", "mmol/l"):      ("mg/dL", lambda v: round(v * 4.008, 1)),
    # Glucose: mmol/L → mg/dL
    ("glucose", "mmol/l"):      ("mg/dL", lambda v: round(v * 18.016, 1)),
    # ANC / Neutrophils: 10^3/µL or 10^9/L → cells/µL
    ("anc",         "10*3/ul"): ("cells/µL", lambda v: int(round(v * 1000))),
    ("anc",         "10^3/ul"): ("cells/µL", lambda v: int(round(v * 1000))),
    ("anc",         "10*9/l"):  ("cells/µL", lambda v: int(round(v * 1000))),
    ("neutrophils", "10*3/ul"): ("cells/µL", lambda v: int(round(v * 1000))),
    ("neutrophils", "10^3/ul"): ("cells/µL", lambda v: int(round(v * 1000))),
    ("neutrophils", "10*9/l"):  ("cells/µL", lambda v: int(round(v * 1000))),
    # Platelets: 10^3/µL or 10^9/L → cells/µL
    ("platelets", "10*3/ul"):   ("cells/µL", lambda v: int(round(v * 1000))),
    ("platelets", "10^3/ul"):   ("cells/µL", lambda v: int(round(v * 1000))),
    ("platelets", "10*9/l"):    ("cells/µL", lambda v: int(round(v * 1000))),
    # WBC: 10^3/µL or 10^9/L → cells/µL
    ("wbc", "10*3/ul"):         ("cells/µL", lambda v: int(round(v * 1000))),
    ("wbc", "10^3/ul"):         ("cells/µL", lambda v: int(round(v * 1000))),
    ("wbc", "10*9/l"):          ("cells/µL", lambda v: int(round(v * 1000))),
}


def _normalize_lab_unit(
    canonical_display: str,
    value: Any,
    unit: Optional[str],
) -> Tuple[Any, Optional[str]]:
    """
    Normalize a lab value+unit to canonical US clinical units if a conversion
    exists in _LAB_UNIT_CONVERSIONS. Returns original (value, unit) otherwise.
    Never raises.
    """
    if value is None or unit is None:
        return value, unit
    try:
        display_key = canonical_display.lower().strip()
        unit_key    = unit.lower().strip().replace(" ", "")
        for (disp, u), (target_unit, convert_fn) in _LAB_UNIT_CONVERSIONS.items():
            if disp in display_key and u == unit_key:
                return convert_fn(float(value)), target_unit
    except Exception:
        pass
    return value, unit


def _create_patient_summary(patient_data: Dict) -> str:
    """
    Create compact patient summary for GPT-4o criterion-level evaluation.

    Sections:
      Demographics      : age, sex, race, ethnicity
      Performance Status: ECOG (LOINC 89247-1), or an explicit statement that
                          none is recorded. Never defaults to 0.
      Conditions        : relevance-filtered into three tiers:
                          Tier A (cancer): full detail with [neoplasm] tag
                          Tier B (relevant): full detail with [comorbidity] tag
                          Tier C (background): one summary line with count + preview
      Medications       : all unique active medications
      Allergies         : active, non-refuted allergies with category and criticality
      Procedures        : all unique procedure types, most recent date per type
      Lab Values        : LOINC-filtered oncology-relevant observations,
                          most recent value per lab concept

    Condition filtering uses _classify_condition_relevance() (three-layer:
    ICD-10 blocks, SNOMED codes, blacklist keywords). Prevents real-world
    patients with 50-100+ conditions from overwhelming GPT-4o's context
    window and attention. Tier C conditions are summarized, never dropped.

    For each lab concept and procedure type, only the most recent value is
    included. Trial eligibility criteria evaluate current status, not history.

    Requires _CANCER_REGISTRY, _MESH_FILTER, _LAB_REGISTRY at module level.
    """
    demographics = patient_data["demographics"]
    conditions   = patient_data["conditions"]
    medications  = patient_data["medications"]
    observations = patient_data.get("observations") or []
    procedures   = patient_data.get("procedures") or []
    allergies    = patient_data.get("allergies") or []
    ecog         = patient_data.get("ecog_performance_status") or {}

    # ── Demographics ──────────────────────────────────────────────────────
    summary = (
        f"Age: {demographics.get('age', 'unknown')} | "
        f"Sex: {demographics.get('sex', 'unknown')} | "
        f"Race: {demographics.get('race', 'unknown')} | "
        f"Ethnicity: {demographics.get('ethnicity', 'unknown')}"
    )

    # ── Performance Status ────────────────────────────────────────────────
    # Its own named line, directly under demographics, because "ECOG 0-1" is
    # the most common single gate in interventional oncology and the model has
    # to be able to find it without inferring it from a lab list.
    #
    # `is None` is the test, never truthiness: ECOG 0 means fully active -- the
    # most eligible a patient can be -- and `if value:` would silently report
    # the best possible performance status as no performance status.
    #
    # When observations exist but none is usable, the count and the reason are
    # stated rather than collapsed into "not recorded". "No ECOG on file" and
    # "an ECOG exists but postdates this data snapshot" are different facts, and
    # a criterion evaluated against the wrong one is wrong in a way nothing
    # downstream can detect.
    summary += "\n\nPerformance Status:\n"
    ecog_value = ecog.get("value")
    if ecog_value is not None:
        ecog_date = ecog.get("date") or ""
        date_str  = ecog_date[:10] if ecog_date and ecog_date != "unknown" else "date unknown"

        # Counted against the pool the winner was actually drawn from, not
        # against every observation on the bundle. "most recent of 3" is false
        # when one of the three postdates the snapshot and is therefore later
        # than the one being reported.
        eligible = ecog.get("observations_on_or_before_reference") or 0
        excluded = ((ecog.get("observations_after_reference") or 0)
                    + (ecog.get("observations_undated") or 0))

        detail = [date_str]
        if eligible > 1:
            detail.append(f"most recent of {eligible}")
        if excluded:
            detail.append(f"{excluded} further observation(s) outside the "
                          f"{ecog.get('reference_date')} snapshot")
        summary += f"- ECOG performance status: {ecog_value} ({'; '.join(detail)})\n"
    elif ecog.get("observations_found"):
        summary += (
            f"- ECOG performance status: not available "
            f"({ecog.get('observations_found')} observation(s) on file, none usable: "
            f"{ecog.get('selection')}; reference date {ecog.get('reference_date')})\n"
        )
    else:
        summary += "- ECOG performance status: not recorded\n"

    # ── Conditions (relevance-filtered) ───────────────────────────────────
    # Tier A (cancer) and Tier B (relevant comorbidities) get full detail.
    # Tier C (background) is summarized in one line to save tokens.
    # Error mode is one-directional: can send slightly more, never less.
    summary += "\n\nConditions:\n"
    unique_conditions = deduplicate_by_display(conditions)

    tier_a = []  # cancer
    tier_b = []  # clinically significant comorbidity
    tier_c = []  # background (confidently irrelevant)

    for condition in unique_conditions:
        tier = _classify_condition_relevance(condition, _CANCER_REGISTRY)
        if tier == "cancer":
            tier_a.append(condition)
        elif tier == "relevant":
            tier_b.append(condition)
        else:
            tier_c.append(condition)

    def _format_condition_line(cond: Dict, tag: str) -> str:
        """Format a single condition for the GPT-4o prompt."""
        display = cond.get("display") or "Unknown condition"
        onset   = cond.get("onset_date") or ""
        year    = onset[:4] if onset and onset != "unknown" else None
        clinical_status = cond.get("clinical_status") or ""

        verification_status = cond.get("verification_status") or ""

        parts = [display]
        if verification_status == "unconfirmed":
            parts.append("unconfirmed")
        elif clinical_status and clinical_status not in ("unknown", ""):
            parts.append(clinical_status)
        if year:
            parts.append(year)
        parts.append(tag)
        return f"- {' | '.join(parts)}"

    def _is_neoplasm_verified(cond: Dict) -> bool:
        """Check if condition maps to MeSH C04 via any available crosswalk."""
        if _MESH_FILTER is None:
            return False
        codings = cond.get("codings", [])
        if codings:
            for c in codings:
                code = c.get("code", "")
                if code in _MESH_FILTER.snomed_to_trees:
                    return True
                if code in _MESH_FILTER.icd10_to_trees:
                    return True
        else:
            code = cond.get("code", "")
            if code in _MESH_FILTER.snomed_to_trees:
                return True
            if code in _MESH_FILTER.icd10_to_trees:
                return True
        return False

    if not unique_conditions:
        summary += "- None\n"
    else:
        # Tier A: cancer conditions (full detail, neoplasm verification tag)
        for cond in tier_a:
            tag = "[neoplasm]" if _is_neoplasm_verified(cond) else "[neoplasm-unverified]"
            summary += _format_condition_line(cond, tag) + "\n"

        # Tier B: clinically significant comorbidities (full detail)
        for cond in tier_b:
            summary += _format_condition_line(cond, "[comorbidity]") + "\n"

        # Tier C: background conditions (one summary line)
        if tier_c:
            other_names = [
                (c.get("display") or "unknown") for c in tier_c
            ]
            preview = ", ".join(other_names[:5])
            remaining = len(other_names) - 5
            if remaining > 0:
                summary += f"- Other conditions ({len(other_names)}): {preview}, +{remaining} more\n"
            elif other_names:
                summary += f"- Other conditions ({len(other_names)}): {preview}\n"

    # ── Medications (relevance-filtered, with status and dates) ────────────
    # Relevant medications (chemo, immunotherapy, anticoagulants, steroids, etc.)
    # get full detail including status and dates. Background medications (OTC,
    # vitamins, eye drops, etc.) are summarized in one line.
    #
    # Status is critical for trial matching:
    #   active/on-hold/unknown → current treatment criteria (met/violated)
    #   completed/stopped      → prior treatment criteria and washout periods
    #
    # Both active and historical medications are included so GPT-4o can evaluate:
    #   - Current treatment exclusions ("no concurrent systemic therapy")
    #   - Prior treatment exclusions ("no prior platinum within 6 months")
    #   - Prior treatment inclusions ("must have received prior chemotherapy")
    summary += "\nMedications:\n"
    unique_meds = medications  # already deduplicated by File 07
    med_relevant = []
    med_background = []

    _ACTIVE_STATUSES = {"active", "on-hold", "draft", "intended", "unknown"}

    for med in unique_meds:
        display = med.get("display")
        if not display:
            continue

        status     = med.get("status", "unknown").lower().strip()
        start_date = med.get("start_date", "unknown")
        end_date   = med.get("end_date", "unknown")

        # Build status label
        if status in _ACTIVE_STATUSES:
            status_label = "active"
        else:
            status_label = status  # completed, stopped, cancelled, etc.

        # Build date string
        date_parts = []
        if start_date and start_date != "unknown":
            date_parts.append(f"start: {start_date[:10]}")
        if end_date and end_date != "unknown":
            date_parts.append(f"end: {end_date[:10]}")
        date_str = f" | {', '.join(date_parts)}" if date_parts else ""

        med_line = f"{display} | status: {status_label}{date_str}"

        tier = _classify_medication_relevance(display)
        if tier == "relevant":
            med_relevant.append(med_line)
        else:
            med_background.append(display)  # background meds: name only, no detail

    if not med_relevant and not med_background:
        summary += "- None\n"
    else:
        for med_line in med_relevant:
            summary += f"- {med_line}\n"

        if med_background:
            preview = ", ".join(med_background[:5])
            remaining = len(med_background) - 5
            if remaining > 0:
                summary += f"- Other medications ({len(med_background)}): {preview}, +{remaining} more\n"
            elif med_background:
                summary += f"- Other medications ({len(med_background)}): {preview}\n"
    
    # ── Allergies ─────────────────────────────────────────────────────────
    # Drug allergies are a common exclusion criterion in oncology trials
    # (e.g., "No known allergy to platinum-based agents", "No history of
    # severe hypersensitivity to monoclonal antibodies"). Providing allergy
    # data converts these criteria from "not_evaluable" to "not_violated"
    # or "violated", improving match accuracy.
    #
    # Only active, non-refuted allergies are included (filtered in parser).
    # Category and criticality are shown when available to help GPT-4o
    # assess severity-gated exclusion criteria.
    summary += "\nAllergies:\n"
    if allergies:
        for allergy in allergies:
            display     = allergy.get("display") or "Unknown allergen"
            category    = allergy.get("category") or ""
            criticality = allergy.get("criticality") or ""

            parts = [display]
            if category and category != "unknown":
                parts.append(category)
            if criticality and criticality != "unknown":
                parts.append(f"criticality: {criticality}")
            summary += f"- {' | '.join(parts)}\n"
    else:
        summary += "- No known allergies\n"
    
    # ── Procedures ────────────────────────────────────────────────────────
    # All procedure types, deduplicated by display name, most recent date per type.
    # Prior chemotherapy, radiation, and surgery are standard eligibility gates.
    summary += "\nProcedures:\n"
    relevant_procs = _LAB_REGISTRY.filter_relevant_procedures(procedures)
    if relevant_procs:
        for proc in relevant_procs:
            display  = proc.get("display") or "Unknown procedure"
            date     = proc.get("date") or ""
            date_str = date[:10] if date and date != "unknown" else "date unknown"
            summary += f"- {display} ({date_str})\n"
    else:
        summary += "- None\n"

    # ── Relevant Lab Values ───────────────────────────────────────────────
    # LOINC-filtered: ANC, creatinine, bilirubin, AST/ALT, platelets, etc.
    # One row per lab concept (most recent reading). Routine vitals excluded.
    summary += "\nRelevant Lab Values (most recent):\n"
    relevant_obs = _LAB_REGISTRY.filter_relevant_observations(observations)
    if relevant_obs:
        for obs in relevant_obs:
            canonical = obs.get("canonical_display") or obs.get("display") or "Unknown"
            value     = obs.get("value")
            unit      = obs.get("unit") or ""
            date      = obs.get("date") or ""
            date_str  = date[:10] if date and date != "unknown" else "date unknown"
            value, unit = _normalize_lab_unit(canonical, value, unit)
            unit_str  = f" {unit}" if unit else ""
            summary += f"- {canonical}: {value}{unit_str} ({date_str})\n"
    else:
        summary += "- None\n"

    # ── Genomic & Molecular Biomarkers ────────────────────────────────────
    # Biomarker observations (EGFR, KRAS, ALK, PD-L1, HER2, MSI, TMB, etc.)
    # are NOT in OncologyLabRegistry (which covers organ function labs only).
    # Without this section, GPT-4o has no biomarker data and all mutation/
    # expression criteria return not_evaluable — a major loss of signal for
    # precision oncology trials.
    #
    # Detection uses keyword matching on observation display text, which
    # handles both Synthea (free-text genetic variant strings) and real EHRs
    # (LOINC-coded molecular panels).
    #
    # MATCHED ON WORD BOUNDARIES, not as substrings. Five of these keywords are
    # three-letter gene symbols and two of them fired constantly as substrings
    # of ordinary clinical English. Measured over the 1,000-patient cohort,
    # AFTER the lab-registry skip below:
    #
    #   "ret"  20,127 false matches — "Diabetic retinopathy severity level"
    #                                 (12,560), "Study observation Left/Right
    #                                 retina by OCT" (6,844), "Natriuretic
    #                                 peptide.B prohormone N-Terminal" (323)
    #   "met"   1,908 false matches — "...by High sensitivity method" (739),
    #                                 "Drugs of abuse 5 panel - Urine by Screen
    #                                 method" (408), the four metastasis
    #                                 displays below (701), "Human
    #                                 metapneumovirus RNA" (60)
    #   "alk"       0 — no display in the corpus contains the substring
    #   "msi"       0
    #   "tmb"       0
    #
    # alk, msi and tmb are kept: they never fired here, and deleting a keyword
    # that is genuinely a biomarker because this particular corpus happens not
    # to trip it would trade a false positive for a false negative on the next
    # corpus. The fix belongs at the matching layer, where it protects all five
    # at once, not in the vocabulary. Nothing is removed from this set.
    #
    # The one genuine biomarker match in the corpus, "ERBB2 gene duplication
    # [Presence] in Breast cancer specimen by FISH" (295), is word-bounded and
    # survives unchanged.
    _BIOMARKER_KEYWORDS = frozenset({
        "egfr", "kras", "alk", "ros1", "braf", "her2", "erbb2",
        "met", "ret", "ntrk", "pd-l1", "pdl1", "msi", "tmb",
        "brca", "idh1", "idh2", "pik3ca", "fgfr", "cdkn2a",
        "mutation", "variant", "fusion", "amplification",
        "deletion", "expression", "microsatellite", "tumor mutational",
        "genetic", "genomic", "molecular",
    })

    # One alternation over the keyword set. Boundary is "not a letter or digit"
    # so punctuation and hyphens still delimit: "PD-L1", "MSI-H" and "c-MET"
    # all match; "Generalized", "retinopathy" and "method" do not.
    #
    # This used to carry a _METASTASIS_KEYWORDS carve-out, because 701
    # metastasis observations reached the model only via "met" matching inside
    # "metastases" and word-bounding would have deleted them silently. They now
    # have their own routed list (File 07's _METASTASIS_LOINCS) and their own
    # prompt section, so the carve-out is gone and this set is gene and marker
    # vocabulary again.
    _BIOMARKER_PATTERN = re.compile(
        r"(?<![a-z0-9])(?:"
        + "|".join(sorted((re.escape(k) for k in _BIOMARKER_KEYWORDS),
                          key=len, reverse=True))
        + r")(?![a-z0-9])"
    )

    _BIOMARKER_STRIP_PREFIXES = (
        "genetic variant: ",
        "molecular: ",
        "genomic: ",
        "mutation analysis: ",
        "variant: ",
    )

    biomarker_obs = []
    loinc_filtered_codes = _LAB_REGISTRY.loinc_codes  # avoid re-showing lab obs

    for obs in observations:
        # Skip observations already shown in the lab values section
        if obs.get("code") in loinc_filtered_codes:
            continue
        display = (obs.get("display") or "").strip()
        value   = obs.get("value")
        date    = obs.get("date") or ""

        display_lower = display.lower()
        if not _BIOMARKER_PATTERN.search(display_lower):
            continue

        # Normalize display: strip verbose prefixes
        display_clean = display
        for prefix in _BIOMARKER_STRIP_PREFIXES:
            if display_lower.startswith(prefix):
                display_clean = display[len(prefix):].strip()
                break

        date_str = date[:10] if date and date != "unknown" else "date unknown"

        if value and str(value).strip():
            biomarker_obs.append(f"- {display_clean}: {value} ({date_str})")
        else:
            biomarker_obs.append(f"- {display_clean} ({date_str})")

    # mCODE structured genomic variants (LOINC 69548-6) — real EHR path.
    # These are parsed by _parse_mcode_genomic_variant in File 07 and
    # deduplicated/filtered by OncologyLabRegistry. Rendered first since
    # they are structured and higher-fidelity than keyword-matched obs.
    mcode_variants = _LAB_REGISTRY.filter_relevant_genomic_variants(
        patient_data.get('cancer_genomic_variants') or []
    )
    mcode_lines = []
    for v in mcode_variants:
        display  = v.get('display') or 'Unknown variant'
        date     = v.get('date') or ''
        date_str = date[:10] if date and date != 'unknown' else 'date unknown'
        mcode_lines.append(f"- {display} ({date_str})")

    # ── Metastasis & Nodal Status ─────────────────────────────────────────
    #
    # Its own section, named for what it is. These observations reached the
    # model only because "met" matched inside "metastases" in the biomarker
    # keyword set, which filed disease spread under "Genomic & Molecular
    # Biomarkers" — a section the model is told contains mutation and
    # expression findings. 701 observations across the cohort, on four LOINCs
    # (File 07's _METASTASIS_LOINCS), and nothing else carried them: they are
    # not in OncologyLabRegistry and not in _MCODE_STAGE_LOINCS.
    #
    # The M/N category is printed because it is the distinction that matters to
    # an eligibility criterion: "no distant metastases" is an M question and
    # "N0-N1 only" is an N question, and the display text alone
    # ("Lymph nodes with micrometastases [#] ...") does not say which axis it
    # is on unless the reader already knows the LOINC.
    metastasis_obs = patient_data.get("cancer_metastasis_observations") or []
    summary += "\nMetastasis & Nodal Status:\n"
    if metastasis_obs:
        for obs in metastasis_obs:
            display  = obs.get("display") or "Unknown observation"
            value    = obs.get("value")
            unit     = obs.get("unit") or ""
            category = obs.get("metastasis_category") or "?"
            date     = obs.get("date") or ""
            date_str = date[:10] if date and date != "unknown" else "date unknown"
            unit_str = f" {unit}" if unit else ""
            value_str = (f": {value}{unit_str}"
                         if value is not None and str(value).strip() else "")
            summary += f"- [{category}] {display}{value_str} ({date_str})\n"
    else:
        summary += "- None on record\n"

    summary += "\nGenomic & Molecular Biomarkers:\n"
    if mcode_lines or biomarker_obs:
        for line in mcode_lines:
            summary += line + "\n"
        for line in biomarker_obs:
            summary += line + "\n"
    else:
        summary += "- None on record\n"
        
    return summary.strip()


# ===========================================================================
# ROUTING FUNCTIONS (conditional edge logic)
# ===========================================================================

def route_after_retrieval(state: TrialMatchState) -> str:
    """
    Conditional edge after hybrid retrieval.

    If retrieval returned 0 results, skip cross-encoder and go
    directly to no_candidates. No point scoring empty results.
    """
    results = state.get("hybrid_results", [])

    if not results:
        return "no_candidates"
    return "cross_encoder_rerank"


def route_after_filter(state: TrialMatchState) -> str:
    """
    Conditional edge after rule-based filtering.

    If filtered_trials is empty, skip GPT-4o evaluation (saves cost)
    and go directly to the no_candidates terminal node.
    """
    filtered = state.get("filtered_trials", [])

    if not filtered:
        return "no_candidates"
    return "gpt4o_evaluation"


def route_after_gpt4o(state: TrialMatchState) -> str:
    """
    Conditional edge after GPT-4o evaluation.

    Three possible outcomes:
        1. Success (evaluations exist, no error) -> finalize
        2. Failure + retries remaining -> retry (loop back to gpt4o_evaluation)
        3. Failure + retries exhausted -> error_handler
    """
    error = state.get("error", "")
    retries = state.get("gpt4o_retries", 0)
    evaluations = state.get("evaluations", [])

    # Success: got valid evaluations
    if evaluations and not error:
        return "finalize"

    # Failure but retries remaining: loop back
    if retries < MAX_GPT4O_RETRIES:
        return "gpt4o_retry"

    # Retries exhausted: go to error handler
    return "error_handler"


# ===========================================================================
# GRAPH CONSTRUCTION
# ===========================================================================

def build_matching_graph() -> object:
    """
    Build and compile the LangGraph StateGraph for the pipeline.

    Graph topology:

        START
          |
        query_expansion (MeSH deterministic)
          |
        hybrid_retrieval
         / \\
       (has  (empty)
       trials)  |
         |   no_candidates ---> END
       cross_encoder_rerank
         |
       rule_based_filter
        / \\
      (has  (empty)
      trials)  |
        |   no_candidates ---> END
      gpt4o_evaluation
       /  |  \\
     (ok) |  (fail + retries left)
      |   |       |
      | (fail + exhausted)
      |   |       |
      |  error   gpt4o_evaluation  <-- RETRY LOOP (cyclic edge)
      |  handler
      |   |
    finalize
      |   |
     END  END
    """

    workflow = StateGraph(TrialMatchState)

    # --- Add Nodes ---
    workflow.add_node("query_expansion",      node_query_expansion)
    workflow.add_node("hybrid_retrieval",      node_hybrid_retrieval)
    workflow.add_node("cross_encoder_rerank",  node_cross_encoder_rerank)
    workflow.add_node("rule_based_filter",     node_rule_based_filter)
    workflow.add_node("gpt4o_evaluation",      node_gpt4o_evaluation)
    workflow.add_node("finalize",              node_finalize)
    workflow.add_node("no_candidates",         node_no_candidates)
    workflow.add_node("error_handler",         node_error_handler)

    # --- Linear Edges ---
    workflow.add_edge(START,                   "query_expansion")
    workflow.add_edge("query_expansion",       "hybrid_retrieval")

    # --- Conditional Edge 1: After Retrieval ---
    # Skip cross-encoder if retrieval returned nothing
    workflow.add_conditional_edges(
        "hybrid_retrieval",
        route_after_retrieval,
        {
            "cross_encoder_rerank": "cross_encoder_rerank",
            "no_candidates":       "no_candidates"
        }
    )

    # --- Linear: rerank -> filter ---
    workflow.add_edge("cross_encoder_rerank",  "rule_based_filter")

    # --- Conditional Edge 2: After Filtering ---
    # Skip GPT-4o if no candidates survived
    workflow.add_conditional_edges(
        "rule_based_filter",
        route_after_filter,
        {
            "gpt4o_evaluation": "gpt4o_evaluation",
            "no_candidates":    "no_candidates"
        }
    )

    # --- Conditional Edge 3: After GPT-4o (retry loop) ---
    # Success -> finalize | Parse failure + retries left -> retry | Exhausted -> error
    workflow.add_conditional_edges(
        "gpt4o_evaluation",
        route_after_gpt4o,
        {
            "finalize":       "finalize",
            "gpt4o_retry":    "gpt4o_evaluation",   # <-- CYCLIC EDGE (retry loop)
            "error_handler":  "error_handler"
        }
    )

    # --- Terminal Edges ---
    workflow.add_edge("finalize",       END)
    workflow.add_edge("no_candidates",  END)
    workflow.add_edge("error_handler",  END)

    # --- Compile ---
    graph = workflow.compile()

    print("LangGraph pipeline compiled successfully.")
    return graph


# ===========================================================================
# BM25 INDEX BUILDER (called once before matching)
# ===========================================================================

def build_bm25_index_from_qdrant() -> Tuple[BM25Okapi, List[str]]:
    """
    Build BM25 index from trials stored in Qdrant.

    Uses pagination via scroll offset to handle collections
    larger than a single scroll batch.

    Returns:
        Tuple of (BM25Okapi index, list of NCT IDs in same order)
    """
    print("Building BM25 index from Qdrant...")

    all_trials = []
    offset = None

    @qdrant_retry
    def _scroll_page(current_offset):
        return qdrant_client.scroll(
            collection_name=COLLECTION_NAME,
            limit=1000,
            offset=current_offset,
            with_payload=True,
            with_vectors=False
        )

    while True:
        scroll_response = _scroll_page(offset)

        points, next_offset = scroll_response
        all_trials.extend(points)

        if next_offset is None:
            break
        offset = next_offset

    # Extract texts and NCT IDs
    trial_texts = []
    nct_ids = []

    for trial in all_trials:
        bm25_text = trial.payload.get("bm25_text", "")
        nct_id = trial.payload.get("nct_id", "")

        tokenized = tokenize_for_bm25(bm25_text)
        trial_texts.append(tokenized)
        nct_ids.append(nct_id)

    bm25_index = BM25Okapi(trial_texts)

    print(f"\n{'='*50}")
    print(f"  BM25 index built: {len(trial_texts)} trials indexed")
    print(f"{'='*50}\n")

    return bm25_index, nct_ids


# ===========================================================================
# PUBLIC API: Match a Single Patient
# ===========================================================================

def match_patient_to_trials(
    patient_data: Dict,
    graph: object
) -> Dict:
    """
    Run the full matching pipeline for one patient.

    Args:
        patient_data: Parsed FHIR patient dictionary
        graph:        Compiled LangGraph StateGraph

    Returns:
        Result dictionary with ranked trials, explanations, and metadata
    """
    print(f"\n{'='*80}")
    print(f"{Project_Name}: Matching Patient {patient_data['patient_id']}")
    print(f"{'='*80}\n")

    initial_state = build_initial_state(patient_data)

    # Invoke the LangGraph pipeline
    final_state = graph.invoke(initial_state)

    result = final_state["result"]

    result["qdrant_collection"] = resolve_qdrant_collection()

    result["patient_data_hash"] = compute_patient_hash(patient_data)

    return result


def build_initial_state(patient_data: Dict, ablation_flags: Dict = None) -> Dict:
    """The state every run starts from, in one place.

    Extracted from match_patient_to_trials() because it is no longer that
    function's private business: 45- Fixture Capture.py and 46- Fixture
    Replay.py invoke the graph directly (they need the whole final state, not
    just state["result"]), and a second hand-written copy of this dict would
    drift from the real one exactly when it mattered — a key seeded here but
    not there is a run that starts from different ground.

    ablation_flags defaults to {} = full pipeline, which is what every
    production caller passes.
    """
    return {
        "patient_data":       patient_data,
        "expanded_query":     "",
        "hybrid_results":     [],
        "bm25_retrieved":     0,
        "vector_retrieved":   0,
        "reranked_trials":    [],
        "filtered_trials":    [],
        "candidates_after_rule_filter": 0,
        "candidates_after_quality_filter": 0,
        "evaluations":        [],
        "gpt4o_retries":      0,
        "gpt4o_raw_response": "",
        "cross_vocab_remaps": 0,
        "result":             {},
        "error":              "",
        "stage_timings":      {},
        "ablation_flags":     dict(ablation_flags or {}),
        "patient_trees":      set(),
        "patient_histology":  set(),
        "mesh_resolution":    "",
        # Degradation keys are deliberately NOT pre-seeded with clean values.
        # The stage that owns each one writes it; until then it is absent, and
        # _pipeline_provenance() turns absence into NULL rather than into a
        # claim that the stage ran and found nothing wrong.
    }


# ===========================================================================
# DISPLAY RESULTS
# ===========================================================================

def display_match_results(result: Dict):
    """
    Pretty-print match results for a single patient.

    Displays the trial-level classification tiers:
      ELIGIBLE:      "eligible"      — no known disqualifiers, pre-screening candidate
      NOT ELIGIBLE:  "not_eligible"  — explicit disqualifying evidence found
      NOT EVALUABLE: "not_evaluable" — the trial could not be assessed; counted, not reported as a rejection

    For each eligible match, lists criteria that could not be evaluated
    from the patient record so the coordinator knows what to verify.
    """

    print(f"\n{'='*80}")
    print(f"{Project_Name}: MATCH RESULTS FOR PATIENT {result['patient_id']}")
    print(f"{'='*80}\n")

    # Check for pipeline error
    if result.get("error"):
        print(f"PIPELINE ERROR: {result['error']}")
        retries = result.get("gpt4o_retries", 0)
        if retries:
            print(f"GPT-4o retries exhausted: {retries}/{MAX_GPT4O_RETRIES}")
        print()

    # Pipeline summary
    matches = result.get("matches", [])
    near_misses = result.get("near_misses", [])
    not_evaluable = result.get("not_evaluable", [])

    print(f"Pipeline Summary:")
    print(f"  BM25 Retrieved:        {result.get('bm25_retrieved', 0)}")
    print(f"  Vector Retrieved:      {result.get('vector_retrieved', 0)}")
    print(f"  Candidates Retrieved:  {result.get('candidates_retrieved', 0)}")

    print(f"  Candidates Re-Ranked:  {result.get('candidates_reranked', 0)}")
    print(f"  After Rule Filters:    {result.get('candidates_after_rule_filter', 0)}")
    print(f"  After Quality Filter:  {result.get('candidates_after_quality_filter', 0)}")
    print(f"  Candidates Filtered:   {result.get('candidates_filtered', 0)}")
    print(f"  Candidates Evaluated:  {result.get('candidates_evaluated', 0)}")
    print(f"  Matches:               {len(matches)}")
    print(f"  Not Eligible:          {len(near_misses)}")
    print(f"  Not Evaluable:         {len(not_evaluable)}")
    print(f"  Label Remaps:          {result.get('cross_vocab_remaps', 0)}")
    if result.get("gpt4o_retries", 0):
        print(f"  GPT-4o Retries:        {result['gpt4o_retries']}/{MAX_GPT4O_RETRIES}")
    if result.get("ablation_flags"):
        print(f"  Ablation Flags:        {result['ablation_flags']}")

    timings = result.get("stage_timings", {})
    if timings:
        print(f"\nStage Latencies:")
        for stage, seconds in timings.items():
            print(f"  {stage}: {seconds:.3f}s")
        total = sum(timings.values())
        print(f"  TOTAL: {total:.3f}s")

    print()

    # ── ELIGIBLE ─────────────────────────────────────────────────────────
    if matches:
        print(f"ELIGIBLE — Pre-Screening Candidates ({len(matches)}):\n")
        for idx, match in enumerate(matches[:10], 1):
            _print_match_detail(idx, match)

    # ── NOT ELIGIBLE ─────────────────────────────────────────────────────
    if not matches:
        print("No matching trials found for this patient.\n")

        if near_misses:
            print(f"NOT ELIGIBLE — Top 3 Near-Misses:\n")
            for idx, match in enumerate(near_misses[:3], 1):
                print(f"  {idx}. {match.get('nct_id', 'N/A')} | {match.get('title', 'No title')}")
                print(f"     {match.get('explanation', 'N/A')}")
                print()
    elif near_misses:
        # Matches exist, but also show count of rejected trials
        print(f"({len(near_misses)} additional trials evaluated but not eligible.)\n")

    # ── NOT EVALUABLE ────────────────────────────────────────────────────
    # Reported separately from rejections: these trials were never assessed.
    if not_evaluable:
        print(f"NOT EVALUABLE — could not be assessed ({len(not_evaluable)}):\n")
        for trial in not_evaluable:
            print(f"  - {trial.get('nct_id', 'N/A')} | {trial.get('explanation', 'No criteria returned.')}")
        print()


def _print_match_detail(idx: int, match: Dict):
    """
    Print a single match with criterion-level transparency.

    Shows the trial identification, score, explanation, and — critically —
    which criteria could not be evaluated from the patient record. This tells
    the research coordinator exactly what tests/data to obtain before referral.
    """
    print(f"  {idx}. {match.get('nct_id', 'N/A')} | {match.get('title', 'No title')}")
    print(f"     Score: {match.get('match_score', 0):.2f} | Status: {match.get('eligible', 'unknown')}")
    print(f"     {match.get('explanation', 'N/A')}")

    # Show criteria that need verification (not_evaluable from inclusions)
    needs_verification = [
        c.get("criterion", "Unknown criterion")
        for c in match.get("inclusion_criteria", [])
        if c.get("status") == "not_evaluable"
    ]
    # Also check exclusions that are not_evaluable (coordinator should verify
    # the patient does NOT have the excluded condition)
    needs_verification += [
        c.get("criterion", "Unknown criterion") + " (exclusion)"
        for c in match.get("exclusion_criteria", [])
        if c.get("status") == "not_evaluable"
    ]

    if needs_verification:
        print(f"     Needs verification ({len(needs_verification)}):")
        for criterion in needs_verification:
            print(f"       - {criterion}")

    print()                
                
    
# ===========================================================================
# MAIN EXECUTION
# ===========================================================================


RUN_TEST_ON_EXECUTE = False

if __name__ == "__main__" and RUN_TEST_ON_EXECUTE:

    print("\n" + "="*80)
    print(f"{Project_Name}: LangGraph Matching Agent")
    print("="*80 + "\n")

    # Step 1: Compile the LangGraph pipeline
    # BM25 index is now built at index time (File 11) and stored in Qdrant.
    # No in-memory BM25 index needed at inference time.
    graph = build_matching_graph()

    # Step 2: Load patients
    all_patients = load_all_patients(data_fhir_path)

    if not all_patients:
        print("No patients found. Run 07- FHIR Parser first.")
    else:
        # Filter to adult patients (age >= 18) for cancer trial matching
        adult_patients = [
            p for p in all_patients
            # age is None, not absent, when the bundle's birthDate was partial
            # beyond use or unparseable (File 07), so the key's default never
            # fires and the comparison would raise on None.
            if (p["demographics"].get("age") or 0) >= 18
        ]

        if not adult_patients:
            print("No adult patients found. Using first patient anyway.")
            test_patient = all_patients[0]
        else:
            test_patient = adult_patients[0]

        # Print patient details
        print(f"\n{'='*80}")
        print("PATIENT DEBUG INFO")
        print(f"{'='*80}")
        print(f"Patient ID: {test_patient['patient_id']}")
        print(f"Age: {test_patient['demographics'].get('age')} years")
        print(f"Sex: {test_patient['demographics'].get('sex')}")
        print(f"\nConditions ({len(test_patient['conditions'])} total):")
        for idx, condition in enumerate(test_patient["conditions"][:15], 1):
            print(f"  {idx}. {condition['display']} (onset: {condition.get('onset_date', 'unknown')})")
        
        unique_meds = list({med['display'] for med in test_patient['medications']})
        print(f"\nMedications ({len(unique_meds)} unique, {len(test_patient['medications'])} records):")
        for idx, med in enumerate(unique_meds[:10], 1):
            print(f"  {idx}. {med}")
        
        print(f"{'='*80}\n")

        # Step 3: Run matching pipeline
        result = match_patient_to_trials(test_patient, graph)

        # Step 4: Display results
        display_match_results(result)

        # Step 5: Save results
        output_file = Path(results_path) / f"match_result_{test_patient['patient_id']}.json"
        with open(output_file, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"Results saved to: {output_file}\n")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 14:01:38 2026

@author: ramyalsaffar
"""