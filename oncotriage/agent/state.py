"""The state schema and the degradation vocabularies that travel in it.

Item 20c, pass 2c: "13- LangGraph Agent.py" lines 118-359, verbatim.

``TrialMatchState`` is the TypedDict every node reads and writes, and the
constants above it are the fixed label sets for the three places the pipeline
can quietly run on less than it was built to run on: a lost retrieval channel,
a query expansion that fell back, a cancer-site filter that never ran. Every one
of those labels reaches a column in inferences.db, which is why they are named
constants in a module of their own rather than string literals at their use
sites -- a typo in a literal would be stored, and would read as a state that
never happened.

They are NOT tunables and do not belong in oncotriage/config.py: they are names
for pipeline states, and changing one changes what a stored row means.

Imports nothing from the project. Importing it compiles one regex and builds a
TypedDict.
"""

import re
from typing import Dict, List, Optional, TypedDict


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

# --- Stage 4, whether the OTHER four per-trial filters ran -------------------
#
# THE SAME FACT MESH_FILTER_APPLIED RECORDS, FOR THE FOUR FILTERS THAT DID NOT
# RECORD IT. Every drop in Stage 4 had a counter; only the cancer site filter
# had a marker saying whether the filter that owns the counter ran at all. So
# ``stage_dropped = 0`` meant three different things -- "checked, nothing to
# drop", "the ablation flag disabled it", "this patient has no stage" -- and
# a stored funnel could not separate them. Same for histology, age and sex.
#
# The vocabularies are deliberately NOT shared with MESH_FILTER_SKIP_*. Each
# filter's reasons are its own: sharing a constant would mean a reader of one
# column having to know that a value it can never produce belongs to another
# filter, which is the shape TRIAL_STATUS_FULL had before pass 20f-3 deleted
# it. What IS shared is the word "applied" and the suffix "ablation_skipped",
# because those two mean the same thing everywhere and a second spelling of
# either would be the drift this file exists to prevent.
FILTER_APPLIED = "applied"
FILTER_SKIP_ABLATED = "ablation_skipped"

# Cancer stage filter. Gated on the ablation flag AND on the patient having a
# resolvable stage ordinal; extraction/stage.py returns None for both "no stage
# recorded" and "a stage was recorded that no tier could read", and
# inferences.mesh_resolution's sibling for that distinction does not exist, so
# this says only that the filter had nothing to compare with.
STAGE_FILTER_SKIP_NO_PATIENT_STAGE = "no_patient_stage"

# Histology filter. Gated on the ablation flag AND on the patient's condition
# displays producing at least one histology tag. An untagged patient is not a
# degraded run -- most cancers carry no histology keyword -- but it IS a run in
# which histology_dropped could not have been anything but 0.
HISTOLOGY_FILTER_SKIP_NO_PATIENT_HISTOLOGY = "no_patient_histology"

# Age filter. Never ablated. Gated on the patient having a computable age,
# which is birth_date_precision's business: "missing", "unparseable" and
# "after_reference" all yield age None and no trial's window can be tested.
#
# NOTE WHAT THIS DOES NOT COVER. It is a PATIENT-level marker. A trial whose
# own min_age/max_age text will not parse is skipped individually, kept, and
# recorded in agent/filtering.py's AGE_PARSE_FAILURES plus the `age_unparsed`
# field of the Stage 4 log line. So `age_filter_applied = 1` with
# `age_dropped = 0` still admits "every trial's bounds were unreadable"; the
# counter is where that is answered, not this column.
AGE_FILTER_SKIP_NO_PATIENT_AGE = "no_patient_age"

# Sex filter. Never ablated. Gated on the patient's recorded sex being
# expressible in the trial vocabulary (ALL / MALE / FEMALE) -- see
# _COMPARABLE_PATIENT_SEXES in agent/filtering.py. When it is not, sex-specific
# trials are KEPT rather than dropped and counted in SEX_UNKNOWN_KEPT, which is
# the governing rule stated at that counter: a filter that cannot decide keeps.
SEX_FILTER_SKIP_NOT_COMPARABLE = "sex_not_comparable"


# --- Stages 5 and 6, the trial-level verdict ---------------------------------
#
# The three labels a trial can carry when it leaves the pipeline, and the one
# place that decides what an off-vocabulary label means. Stage 5 writes them and
# Stage 6 splits on them, so a fourth spelling anywhere is a trial that lands in
# the wrong bucket -- and every one of them reaches trial_matches.eligible.
#
# THEY LIVE HERE BECAUSE THE VOCABULARY WAS WRITTEN TWICE. Stage 5 held a
# three-member tuple and Stage 6 held a six-entry synonym map, and the two
# disagreed about the same input: Stage 5 forced "Eligible" and boolean True to
# "not_eligible" (a rejection nothing said), while Stage 6's map, had it ever
# been reached with those values, resolved both to "eligible". Stage 5 runs
# first, so the map could not be reached and the disagreement was invisible.
# One vocabulary, one normalizer, two callers.

TRIAL_VERDICT_ELIGIBLE = "eligible"
TRIAL_VERDICT_NOT_ELIGIBLE = "not_eligible"
TRIAL_VERDICT_NOT_EVALUABLE = "not_evaluable"

# Closed, and ordered as the pipeline reports them. A caller may branch on it
# exhaustively.
TRIAL_VERDICTS = (
    TRIAL_VERDICT_ELIGIBLE,
    TRIAL_VERDICT_NOT_ELIGIBLE,
    TRIAL_VERDICT_NOT_EVALUABLE,
)

# How normalize_trial_verdict() reached its answer. Recorded rather than
# inferred: "the model wrote the canonical label" and "the model wrote
# something else that meant the same thing" are different facts about the same
# verdict, and only the second is worth a log line.
VERDICT_SOURCE_CANONICAL = "canonical"        # already one of TRIAL_VERDICTS
VERDICT_SOURCE_NORMALIZED = "normalized"      # case/whitespace/synonym recovery
VERDICT_SOURCE_UNRECOGNIZED = "unrecognized"  # no verdict; the caller decides

VERDICT_SOURCES = (
    VERDICT_SOURCE_CANONICAL,
    VERDICT_SOURCE_NORMALIZED,
    VERDICT_SOURCE_UNRECOGNIZED,
)

# The recovery vocabulary, and it is deliberately SMALL. Case-folding and
# whitespace are parsing, not guessing: "Eligible " and "eligible" are the same
# token. The four synonyms below are the JSON spellings of a yes/no answer, and
# they are here because Stage 6 has carried exactly these four for the whole
# life of the pipeline -- adopting them changes no rule, it moves one that was
# already shipped to where the first reader can see it.
#
# Nothing else is added. A label this map cannot resolve is UNRECOGNIZED, and
# the caller must not guess at it: the same argument _normalize_arm makes at
# criterion level, that a guessed label can disqualify a patient with no
# quotable evidence behind it.
_TRIAL_VERDICT_SYNONYMS = {
    "true": TRIAL_VERDICT_ELIGIBLE,
    "false": TRIAL_VERDICT_NOT_ELIGIBLE,
    "yes": TRIAL_VERDICT_ELIGIBLE,
    "no": TRIAL_VERDICT_NOT_ELIGIBLE,
}


def normalize_trial_verdict(raw):
    """Resolve one model-written trial-level label into the fixed vocabulary.

    Returns ``(verdict, source)``:

      * ``verdict`` is a member of TRIAL_VERDICTS, or ``None`` when the label
        could not be resolved at all.
      * ``source`` is a member of VERDICT_SOURCES, naming how the answer was
        reached, so a caller can log the fallback path it took.

    ``None`` is returned rather than a default verdict ON PURPOSE. Every default
    available here is a claim: "not_eligible" asserts a rejection the model
    never made, "eligible" asserts a match it never made, and "not_evaluable"
    is a policy about what to do with an uninterpretable answer rather than a
    reading of one. The policy belongs at the call site, beside the criteria
    that may or may not justify something better; the parsing belongs here.

    A bool is tested BEFORE str, and before any dict lookup, because ``True``
    and ``1`` are the same dict key in Python -- a single map holding both bool
    and string keys would silently answer for the integer 1 as though the model
    had written ``true``.
    """
    if isinstance(raw, bool):
        return (TRIAL_VERDICT_ELIGIBLE if raw else TRIAL_VERDICT_NOT_ELIGIBLE,
                VERDICT_SOURCE_NORMALIZED)

    if isinstance(raw, str):
        folded = raw.strip().lower()
        if folded in TRIAL_VERDICTS:
            return (folded,
                    VERDICT_SOURCE_CANONICAL if folded == raw
                    else VERDICT_SOURCE_NORMALIZED)
        if folded in _TRIAL_VERDICT_SYNONYMS:
            return _TRIAL_VERDICT_SYNONYMS[folded], VERDICT_SOURCE_NORMALIZED

    return None, VERDICT_SOURCE_UNRECOGNIZED


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
    # quality_threshold is the RELATIVE cut the gate actually used -- the
    # QUALITY_THRESHOLD_PERCENTILE of this pool's unboosted fused scores -- so
    # the configured percentile alone does not say where the cut fell.
    #
    # THE GATE IS TWO KNOBS AND THEY OVERLAP. quality_dropped is the total; the
    # three below split it. percentile + floor does NOT equal quality_dropped,
    # because a trial can fail both. floor_only is the one that answers "is the
    # absolute knob doing anything the relative knob was not already doing".
    # quality_threshold describes ONLY the relative knob; the absolute one cuts
    # at MEDCPT_SCORE_FLOOR, which is a MedCPT score and not comparable to it.
    age_dropped: int                            # Trials dropped by the age window
    sex_dropped: int                            # Trials dropped by the sex requirement
    quality_dropped: int                        # Trials dropped by the quality gate, both knobs
    quality_dropped_percentile: int             # ...of which, by the relative percentile
    quality_dropped_floor: int                  # ...of which, by the absolute MedCPT floor
    quality_dropped_floor_only: int             # ...by the floor and NOT by the percentile
    # NULL when the gate saw an EMPTY pool -- every trial was already removed
    # by the per-trial filters above, so no cut was made and any number here
    # would claim one. Same NULL convention as the degradation columns.
    quality_threshold: float                    # Unboosted fused score the relative knob cut at, or None


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

    # The same pair for the four filters that had a drop counter and no marker.
    # Each *_applied is a bool decided ONCE, outside the per-trial loop, because
    # every one of the conditions is loop-invariant; each *_skip_reason is
    # FILTER_APPLIED when it ran and one of that filter's own skip constants
    # when it did not. All eight are logged.
    #
    # Read them the way mesh_filter_applied is read: a drop count of 0 beside
    # applied=1 is "checked, nothing to drop", and beside applied=0 it is "the
    # filter never ran", and those are not the same run.
    stage_filter_applied: bool
    stage_filter_skip_reason: str
    histology_filter_applied: bool
    histology_filter_skip_reason: str
    age_filter_applied: bool
    age_filter_skip_reason: str
    sex_filter_applied: bool
    sex_filter_skip_reason: str

    # --- Stage 5: LLM Classifier Evaluation ---
    evaluations: List[Dict]                     # Criterion-level match results
    llm_classifier_retries: int                          # Current retry count for Stage 5

    # The model DECLINED to answer, as opposed to answering badly. Set only by
    # Stage 5's refusal path (see REFUSAL_ERROR_PREFIX in
    # oncotriage/agent/evaluation.py), carrying a capped copy of the refusal
    # text; absent on every other path.
    #
    # IT EXISTS BECAUSE THE ROUTER NEEDS A TERMINAL SIGNAL, and a bare error
    # string is not one. route_after_llm_classifier retries whenever there is an
    # error and the retry count is under the ceiling, so a refusal that sets an
    # error WITHOUT spending a retry -- which is the correct accounting, since
    # no retry was spent -- would loop the graph until LangGraph's recursion
    # limit raised, killing the patient with an error about recursion rather
    # than about a refusal. The two honest alternatives were this flag or
    # writing MAX_LLM_CLASSIFIER_RETRIES into the count, and the second records
    # three retries that never happened.
    #
    # NOT in the result dict and therefore not a database column: the refusal is
    # already in `error`, verbatim and prefixed, which is what a reader queries.
    # This is a routing fact, and the characterization fixtures diff the result
    # field by field.
    llm_classifier_refusal: Optional[str]

    # Truncation control (Stage 5). A SEPARATE budget from llm_classifier_retries: that
    # one counts whole-node retries for a malformed or failed response, this
    # counts levels of halving spent because a response was cut off at
    # MATCHING_MAX_TOKENS. A patient that hits one parse failure and then needs
    # two splits must not be failed for exhausting a shared counter.
    llm_classifier_truncation_splits: int
    # The pre-call estimate, logged beside the actual so the calibration in
    # 03- Config.py can be re-derived from measured data rather than re-guessed.
    llm_classifier_output_tokens_estimated: int
    # Trials that entered Stage 5 and left it with no verdict because of
    # truncation (the floor, or the split budget). Distinct from
    # not_evaluable_trials, which counts trials the model assessed and could
    # not conclude on.
    not_evaluable_truncated: int
    # Entries the model returned for a trial that was never in the chunk it was
    # answering. Dropped before enrichment, so they reach no verdict, no result
    # list and no trial_matches row; this is the only record that they arrived.
    #
    # Optional, and None is not 0: Stage 5 writes it on its success return
    # alone, so None means the response was never fully compared against the
    # candidate set (an API failure, a refusal, an unparseable answer, or a run
    # that never reached Stage 5). Same convention as
    # llm_classifier_prompt_sha256 below.
    hallucinated_trials: Optional[int]
    # How many model calls this stage actually made. 1 unsplit; more when a
    # batch was split. Without it a chunked run is indistinguishable from an
    # unsplit one in the token columns.
    llm_classifier_calls: int
    llm_classifier_raw_response: str                     # Raw classifier text (retry debugging)
    llm_classifier_prompt: str                           # Prompt sent to matching model
    # Which SYSTEM prompt template produced this run, and the sha256 of the
    # exact rendered bytes. Both come from oncotriage/agent/prompts.py; the
    # version is hand-maintained and says what a human intended, the hash is
    # computed per call and says what was actually sent. Note the hash covers
    # the SYSTEM message only -- llm_classifier_prompt above is system + user,
    # and the user half varies per patient, so hashing it would identify the
    # patient rather than the template.
    #
    # The version is present on every terminal path; the hash is None when no
    # prompt was ever rendered (node_no_candidates, or a failure upstream of
    # Stage 5). Hashing an unsent prompt would record an event that did not
    # happen.
    llm_classifier_prompt_version: str
    llm_classifier_prompt_sha256: Optional[str]
    # How much of that rendered system message was the PATIENT RECORD, in the
    # pipeline's own estimated tokens (evaluation.estimate_prompt_tokens over
    # the NEUTRALIZED record text -- the bytes actually interpolated). The
    # template is constant and the record is not, so this is what says how much
    # of a run's fixed prefix was patient rather than instruction.
    #
    # DECLARED HERE OR IT DOES NOT EXIST. See the packing block below: an
    # undeclared key a node returns is DROPPED by LangGraph silently, and this
    # project has already shipped that defect once, on four keys at a time.
    #
    # Optional, and None is not 0. Stage 5 writes it on EVERY return -- the
    # render precedes the first call, so a failed run has one too -- which
    # makes None mean "no system prompt was ever rendered for this run", the
    # same no-fallback convention as llm_classifier_prompt_sha256 above and the
    # same population: node_no_candidates, or a failure upstream of Stage 5.
    # A rendered prompt cannot report 0 here; an empty patient record would,
    # and that is a measurement.
    llm_classifier_patient_record_tokens: Optional[int]
    llm_classifier_input_tokens: int
    llm_classifier_output_tokens: int
    # The reasoning share OF llm_classifier_output_tokens on a reasoning model, not an
    # amount on top of it. None when no response reported the breakdown; see
    # _pipeline_provenance() for why that is not 0.
    llm_classifier_reasoning_tokens: Optional[int]

    # --- Stage 5 INPUT packing, and what the provider served from cache ------
    #
    # THESE FOUR MUST BE DECLARED HERE OR THEY DO NOT EXIST. A key a node
    # returns and this TypedDict does not declare is not an error in LangGraph:
    # it is DROPPED, silently, with no warning and no raise, because the schema
    # is what defines the channels a node may write. The first three shipped at
    # PROMPT_VERSION 1.6.0 written by node_llm_classifier_evaluation and read by
    # _pipeline_provenance() and were undeclared, so every result carried NULL
    # for all three -- and NULL is this project's "the measurement was not
    # made", which is exactly what a genuinely unpacked run reports. The
    # provenance was therefore indistinguishable from its own absence.
    #
    # Measured rather than reasoned about, on a StateGraph over this schema
    # with the real nodes: the node returned packed_chunks=1, a full packing
    # report and cached_tokens=1024, and the result carried None for all three
    # while llm_classifier_reasoning_tokens -- one line above, same stub, same
    # accumulate-and-return route, declared -- arrived intact.
    # tests/test_agent_state_channel_coverage.py is the standing guard.
    #
    # ALL FOUR ARE Optional AND None IS NOT 0. Stage 5 writes them on its
    # success return alone, so None means no Stage 5 run was completed (no
    # candidates, an API failure, a refusal, an unparseable answer) rather than
    # "one request of zero cached tokens". Same convention as
    # hallucinated_trials above and llm_classifier_prompt_sha256 below.

    # How many chunks the INPUT packer produced for this patient: the scalar a
    # query groups by. 1 means it packed and needed one request; None means it
    # did not run to completion. See oncotriage/config.py's
    # MATCHING_INPUT_TOKEN_BUDGET block.
    llm_classifier_packed_chunks: Optional[int]
    # The packer's own record behind that count: the estimator named, the
    # configured and effective budgets, the cap, the two degradation flags
    # (cap_relaxed_budget, over_budget_chunk) and one entry per chunk carrying
    # its trial count and estimated tokens.
    llm_classifier_packing: Optional[Dict]
    # The provider's report of how much of this request's prefix it served from
    # cache, summed over the calls this stage made. A SUBSET of
    # llm_classifier_input_tokens, exactly as reasoning is a subset of output,
    # and never a costing term: get_model_cost() prices the whole input at the
    # uncached rate deliberately, so stored costs stay comparable with every
    # historical row.
    llm_classifier_cached_input_tokens: Optional[int]
    # ONE ENTRY PER CALL ACTUALLY ISSUED, in the order they were issued, never
    # summed away. The three fields above are totals, and a total cannot answer
    # the question packing exists to raise: whether the shared prefix is being
    # served from cache on the SECOND and later chunks of the same patient. A
    # single cached_tokens figure of 5,000 across three calls is consistent both
    # with a cache that works and with one that never warms.
    #
    # A LIST IS NOT A COUNT, which is why this is written on the failure
    # returns too where hallucinated_trials and the packing keys are not. Each
    # element is a fact about a request that was made and billed; a short list
    # understates nothing, it simply ends where the run ended. A failed run is
    # exactly the run whose per-call token record is worth having, on the same
    # argument _pipeline_provenance() makes for carrying the prompt hash out of
    # the error paths.
    llm_classifier_call_details: Optional[List[Dict]]

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


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 14:01:38 2026

@author: ramyalsaffar
"""
