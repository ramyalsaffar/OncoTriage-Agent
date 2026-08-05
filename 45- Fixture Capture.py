# Characterization Fixture Capture
##################################

"""
Records what the pipeline does today, so item 20 can prove what it broke.

WHY THIS EXISTS
---------------
Item 20 restructures ~6,000 lines across 27 files. Nothing else in this repo
will tell you whether the restructured pipeline still produces the same
answers: 18-/19- hit a live server and check shapes, 30- through 44- test one
component each, and the ablation study measures configurations against each
other rather than against a past self. This file is the missing baseline.

It is a RECORDING, not a test suite. It asserts almost nothing about whether
the pipeline is correct. It captures what the pipeline currently does, byte for
byte where that is meaningful, and 46- Fixture Replay.py replays it and reports
every difference. A difference is not automatically a defect — but after a
6,000-line refactor, an unexplained difference is the only warning you get.

THE FIXTURE FORMAT (schema version 2)
=====================================
Stored gzipped as <fixture_id>.json.gz, so the directory is small enough to
live in the repository — item 64's deploy gate reads it from there.

Changes from version 1, all of them size or honesty:
  - recordings.cross_encoder_inputs holds each trial-text set ONCE, keyed by
    digest; the per-pass records reference it. v1 wrote four identical copies
    of ~170 KB per fixture, one per rerank query.
  - a derived fixture stores a `derivation` RECIPE instead of the derived FHIR
    bundle. v1 wrote two 107 MB bundles for two fixtures.
  - deterministic_prefix.terminal.terminal_node is read from the result, which
    each terminal node in File 13 now stamps, instead of being inferred from
    which keys happened to be present.
  - environment.collection_digest fingerprints the Qdrant collection's
    CONTENTS, not just its name.
One JSON file per fixture. Three consumers read it: this file writes it,
46- Fixture Replay.py diffs against it, and items 22 and 64 build a test suite
and a regression gate on the same shape. The version field exists so those two
can refuse a fixture they do not understand rather than mis-read one.

Top level
---------
    schema_version        int    — SCHEMA_VERSION below. Bump on ANY change to
                                   the meaning of a field, not just on removals.
    fixture_id            str    — stable slug, also the filename stem.
    fixture_kind          str    — "recorded" or "constructed". A consumer that
                                   treats the two identically is reporting
                                   something that did not happen on its own as
                                   evidence that it did.
    case_labels           [str]  — which branch cases this fixture covers, from
                                   CASE_* below.
    captured_at_utc       str    — ISO 8601. Provenance only; never diffed.
    construction          dict|None — present iff fixture_kind == "constructed".
                                   Always carries what_was_changed and why, and
                                   one of two provenance keys depending on which
                                   kind of construction it is:

                                   derived_from        — a recorded FIXTURE was
                                     edited. Only the retry fixture: its
                                     recorded Stage 5 response list gained a
                                     malformed first entry. No new pipeline run.
                                   derived_from_bundle — a real cohort BUNDLE
                                     was edited and the pipeline then ran on it
                                     for real, end to end. Used for the two
                                     branches no patient in the cohort reaches.
                                     The bundle is written beside the fixture as
                                     <fixture_id>.bundle.json and re-parsed on
                                     replay, so File 07 stays under test.

identity
--------
    patient_id            str    — the FHIR Patient.id, as parsed.
    patient_data_hash     str    — compute_patient_hash() (File 13).
    source_bundle         str    — bundle FILENAME, never a path. For a derived
                                   fixture this names the DONOR.
    source_bundle_location str   — "cohort" (resolve under data_fhir_path) or
                                   "derived" (rebuild from `derivation`).
    case_evidence         dict   — the cohort-scan facts that made this patient
                                   the one chosen for its case (resolution,
                                   expansion path, stage, ECOG). Stored so the
                                   coverage claim can be audited from the
                                   fixture rather than from a scrollback.

    patient_id and patient_data_hash are separate fields on purpose. Item 34
    rewrites patient identifiers; when it does, remapping a fixture must be an
    edit to one field, not a re-capture. They are also different facts — the
    hash keys on clinical content and demographics and deliberately not on the
    identifier — so folding them into one string would lose the ability to say
    "same patient, different record" or "same record, renamed patient".

environment
-----------
    collection_digest     dict   — {point_count, distinct_nct_ids,
                                   nct_id_sha256} over the pinned collection,
                                   fetched with only the nct_id payload and no
                                   vectors. The name pins WHICH index; this
                                   pins WHAT IS IN IT. Without it an in-place
                                   re-index passes the name check and then
                                   shows up as retrieval-order differences in
                                   every fixture, which reads exactly like a
                                   code regression. 46- refuses to replay on a
                                   mismatch and says the index changed.
    qdrant_collection     str    — the RESOLVED collection name from
                                   resolve_qdrant_collection(), never the
                                   COLLECTION_NAME alias. The alias rotates
                                   weekly (File 11 swaps it); a fixture diffed
                                   against a different index is measuring the
                                   corpus, not the code. 46- refuses to replay
                                   when this does not match the live resolution.
    collection_alias      str    — COLLECTION_NAME, for context.
    alias_resolved        bool   — False when resolve_qdrant_collection() fell
                                   back to the alias string itself.
    data_snapshot_date    str    — DATA_SNAPSHOT_DATE (File 03).
    age_reference_date    str    — get_age_reference_date(), the date ages and
                                   the Stage 5 prompt were anchored to.
    embedding_model       str
    matching_model        str  — the model REQUESTED (MATCHING_MODEL). The
                                 model that ANSWERED is recorded per call, in
                                 recordings.chat_completions[*].response.model.
    matching_temperature  num  — None once the judge is a model that rejects
                                 the parameter (gpt-5.6-terra does). "Not
                                 sent", not "sent as zero".
    matching_max_tokens   int  — sent as max_completion_tokens, which on a
                                 reasoning model caps reasoning AND visible
                                 output together.
    matching_reasoning_effort str — none|low|medium|high|xhigh, or None for a
                                 non-reasoning judge.
    matching_seed         int
    cross_encoder_model   str
    sparse_model          str
    tunables              dict   — the File 03 constants that shape the
                                   deterministic prefix. Recorded so a diff
                                   caused by a config edit is distinguishable
                                   from one caused by a code edit.

derivation (present iff identity.source_bundle_location == "derived")
---------------------------------------------------------------------
    recipe                str    — RECIPE_* below; names the transformation.
    donor_bundle          str    — the real cohort bundle it is built from.
    donor_patient_id      str    — recorded separately from the filename for
                                   the same reason identity does it: item 34
                                   rewrites identifiers.
    params                dict   — recipe arguments (e.g. {"age_years": 1}).

    46- rebuilds the bundle from the live corpus into a temporary file, parses
    it, checks the result against identity.patient_data_hash, and deletes it.
    A recipe that no longer reproduces its input fails loudly.

inputs
------
    ablation_flags        dict   — {} for a full-pipeline run; otherwise a
                                   File 26 config's flags verbatim.
    ablation_config_name  str|None

deterministic_prefix
--------------------
Everything that must reproduce EXACTLY, given the same patient bundle, the same
pinned Qdrant collection, and the recorded model outputs. Diffed field by
field. Sections:

    patient_data      the full parsed patient dict (File 07). Diffed because
                      46- RE-PARSES the source bundle rather than trusting the
                      copy stored here — a parser regression is exactly the
                      kind of thing item 20 can cause, and feeding the stored
                      dict back in would make that half of the diff vacuous.
    stage1            mesh_resolution, query_expansion_path, expanded_query,
                      rerank_queries, expansion_prompt.
    stage2            per-channel NCT IDs IN RETRIEVAL ORDER and per-channel
                      counts (observed at the Qdrant client, not inferred from
                      state), the fused pool in order with its fusion scores,
                      the payload-backfill scroll, and every Stage 2
                      degradation key.
    stage3            reranked NCT IDs in order with rerank_score,
                      rerank_score_raw, mesh_boost and mesh_boost_tier, plus
                      the resolved patient MeSH trees.
    stage4            the filtered set in order and EVERY drop count with its
                      reason (mesh, stage, histology, age, sex, quality gate,
                      cost cap), the score the quality gate cut at, and whether
                      the cancer site filter ran.
    stage5            request digest per call, retries actually spent, label
                      remaps, token counts, and the per-trial verdicts in order.
    terminal          which terminal node produced the result, the three
                      outcome lists, and every remaining degradation key.

deterministic_prefix is diffed with exact equality after a JSON round trip.
Floats are stored unrounded: Python's json round-trips a float exactly, the
cross-encoder scores are replayed as float32 (the dtype the model produced, so
argsort ties break identically), and the RRF sums are exact in binary floating
point. Rounding here would hide small real changes to buy nothing.

recordings
----------
Everything served back on replay instead of being recomputed. 46- makes NO
network call to OpenAI and loads NO local model.

    openai_embeddings   [ {call_index, model, input, vector} ]
                        Every text-embedding-3-small request with its returned
                        vector. Keyed on replay by (model, input).
    sparse_embeddings   [ {call_index, query, indices, values} ]
                        Every FastEmbed BM25 query vector. Recorded so replay
                        does not have to load FastEmbed either.
    cross_encoder_inputs { <trial_texts_sha256>: [text, ...] }
                        Each trial-text set stored ONCE. The cross-encoder
                        scores the same ~100 texts once per rerank query, so
                        storing them per pass wrote four identical copies.
    cross_encoder       [ {call_index, query, n_pairs, trial_texts_sha256,
                           scores, dtype} ]
                        Every MedCPT input with its output scores, referencing
                        the text set by digest. Keyed on replay by
                        (query, digest), so being asked to score a different
                        set of texts is a miss rather than a silent match.
    chat_completions    [ {call_index, request, response} ]
                        The Stage 5 request and response VERBATIM. request is
                        the literal kwargs sent to the client (model, both
                        messages, temperature, max_tokens,
                        max_completion_tokens, reasoning_effort, seed — each
                        read from the kwargs, so one the pipeline no longer
                        sends records as None). response carries content,
                        finish_reason, model and usage; usage carries
                        prompt_tokens, completion_tokens and reasoning_tokens,
                        the last being a SUBSET of completion_tokens and None
                        when the model reported no breakdown.
                        Served BY CALL INDEX, not by key, because the retry
                        loop deliberately issues the same request twice and
                        must receive different answers.

BRANCH COVERAGE, AND WHAT THE COHORT CANNOT REACH
-------------------------------------------------
Three of the five branch cases have real patients in the 1,000-bundle cohort.
Two do not, and that is a finding in its own right rather than a gap to paper
over — both branches are live production code that the corpus never exercises:

  EXPANSION_PATH_FALLBACK   0 of 1,000. Every patient resolves to MeSH with
                            terms (799 snomed, 160 fuzzy_stem, 27
                            fuzzy_stem+snomed, 14 fuzzy_substring+snomed),
                            because Synthea codes every diagnosis with a
                            specific, well-known SNOMED concept.
  node_no_candidates        0 of 250 probed against the live index. Every one
                            retrieved a full RRF_POOL_SIZE of 100 and kept
                            between 39 and 95 through Stage 4's hard filters.

Both are covered by running the real pipeline on a derived bundle — one field
of one real patient's record rewritten, everything else untouched — and the
fixture records exactly what was changed and why. See build_mesh_fallback_bundle
and build_no_candidates_bundle.

WHAT THIS DOES NOT CAPTURE
--------------------------
  - Wall-clock timings. stage_timings varies run to run and is excluded from
    the prefix entirely.
  - The Qdrant corpus. Retrieval is re-executed live against the pinned
    collection on replay. If that collection's contents change, the fixture is
    invalid and the diff will say so loudly and wrongly attribute it to code.
    Pinning the resolved name is what stops the weekly alias swap from doing
    this silently; it does not stop someone editing the collection in place.
  - Anything about correctness. A fixture records a wrong answer as faithfully
    as a right one.

SAFETY
------
This file must not write to the production inferences.db, and it cannot.

It does have to chain 14- Database Logger.py, which is not obvious and is worth
stating: File 13's three terminal nodes all call _resolve_primary_cancer(), and
that function is defined in File 14, not in File 13 or in its chain. Every
production caller happens to load 14 after 13, so the dependency has never
surfaced; invoking the graph without 14 raises NameError in node_finalize.

Rather than duplicate the function here — a second copy would drift from the
one production runs, and the fixtures would then characterize a function nobody
calls — 14 is chained with `inferences_path` REDIRECTED to a scratch database in
the system temp directory, and log_inference is rebound to a function that
raises, so even a stray call cannot write a row.

Two earlier statements here are no longer true and are corrected rather than
deleted, because both were load-bearing when they were written:

  * "14 opens that path at load time and creates its tables in it" — item 20b
    made schema creation a function, so loading 14 opens nothing at all. The
    scratch file may never be created, which is the correct outcome for a run
    that logs nothing.
  * the redirect is a rebinding of a shared global, and pass 20c-2b turned File
    14 into a shim over oncotriage/storage/database_logger.py. A module function
    cannot see this file's globals; the rebinding still works only because the
    shim keeps a wrapper that passes globals().get("inferences_path") down.

Three facts are asserted at startup by _assert_database_is_isolated(): the
redirect is in place, the neutralized log_inference still raises, and the
package's own default — what a caller that passed no db_path would get — is the
production database and NOT the scratch one, which is what makes the first two
checks discriminating rather than vacuous.

USAGE
-----
    python "45- Fixture Capture.py"                    # scan, select, capture all
    python "45- Fixture Capture.py" --scan-only        # cohort scan + case report
    python "45- Fixture Capture.py" --probe-limit 400  # widen the no-candidates hunt
    python "45- Fixture Capture.py" --only normal_a retry_constructed
"""


#------------------------------------------------------------------------------


# Run needed files
#-----------------
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

# 13- already chains 03, 08, 09 and 10 — listing them again would re-exec them
# and rebuild the registries. 07- is added because the parser is not in 13-'s
# chain and this file needs parse_fhir_bundle.
exec_chain(
    ["07- FHIR Parser.py", "13- LangGraph Agent.py"],
    caller_file=_code_dir + "45- Fixture Capture.py",
    caller_globals=globals(),
    chain_label="01 → 02 → 07 → 13 (→ 03 → 08 → 09 → 10)",
)

# Not in 01- Imports.py. copy deep-copies a recorded fixture when the
# constructed retry variant is built from it; gzip is how fixtures are stored
# (see FIXTURE_SUFFIX); tempfile holds a derived bundle for the seconds between
# rebuilding it from its recipe and parsing it.
import copy
import gzip
import tempfile
from types import SimpleNamespace


# ===========================================================================
# WHERE FIXTURES LIVE
# ===========================================================================
#
# Derived from main_path by glob prefix, the same way 01- Imports.py resolves
# every other sibling directory, so this file contains no absolute path and the
# directory can be renumbered. Fixtures are data: they do not belong in the
# version-controlled code folder, and they are large enough (a parsed patient
# bundle plus a 1536-float embedding plus a full GPT-4o exchange) that they
# would dominate the repo.
#
# Resolved BEFORE 14- is chained, because the scratch database below lives here.

def _resolve_fixture_root() -> str:
    """Locate (and create) the fixture directory under the project root."""
    matches = sorted(glob.glob(os.path.join(main_path, "*Testing")))
    testing_dir = matches[0] if matches else os.path.join(main_path, "09- Testing")
    root = os.path.join(testing_dir, "Characterization Fixtures")
    os.makedirs(root, exist_ok=True)
    return root


FIXTURE_ROOT = _resolve_fixture_root()
FIXTURE_INDEX_FILENAME = "index.json"


# ===========================================================================
# 14- Database Logger, redirected away from production
# ===========================================================================
#
# File 13's terminal nodes call _resolve_primary_cancer(), which lives in File
# 14. Every production entry point (17-, 25-) chains 14 after 13, so the
# dependency is satisfied by accident of ordering and has never been noticed;
# invoking the graph without 14 raises NameError inside node_finalize.
#
# Copying the function here would be worse than chaining the file: two
# definitions drift, and the fixtures would end up characterizing a copy that
# no production run executes. So 14 is chained — with inferences_path pointed
# at a scratch database first, since 14 connects and runs its DDL at load time.
#
# This is a coupling defect in File 13, not a feature of this file. It is
# exactly the kind of thing item 20 should straighten out, and the redirect is
# the smallest way to work around it without editing a file this item does not
# own.

PRODUCTION_INFERENCES_PATH = inferences_path
# In the system temp directory, not in FIXTURE_ROOT: that directory is meant to
# be committed, and a SQLite file created as a side effect of loading a module
# is not a fixture. It is written once at load time and never read.
FIXTURE_SCRATCH_DB = os.path.join(
    tempfile.gettempdir(), "oncotriage_fixture_capture_scratch.db"
)

inferences_path = FIXTURE_SCRATCH_DB

exec_chain(
    ["14- Database Logger.py"],
    caller_file=_code_dir + "45- Fixture Capture.py",
    caller_globals=globals(),
    chain_label="14 (inferences_path redirected to a scratch database)",
)

# 14- is loaded for one pure function. Nothing here logs an inference, and the
# rebinding makes that structural rather than a promise: a stray call raises
# instead of writing a row that would look like a production inference.
#
# The signature accepts db_path so that a caller written against the pass-2b
# signature still lands on the raise rather than on a TypeError. A TypeError
# would also stop the write, but it would report the wrong reason, and
# _assert_database_is_isolated() below only accepts a RuntimeError as evidence
# that the neutralization is intact.
_UNUSED_LOG_INFERENCE = log_inference


def log_inference(*_args, **_kwargs):
    raise RuntimeError(
        "45- Fixture Capture.py must not log inferences. 14- Database Logger.py "
        "is chained only for _resolve_primary_cancer(), which File 13's terminal "
        "nodes depend on."
    )


#------------------------------------------------------------------------------


# ===========================================================================
# SCHEMA
# ===========================================================================

# Bump on any change to the MEANING of a stored field, including a field that
# is added to deterministic_prefix — an older fixture has no value for it, and
# a replay that silently treats "absent" as "matched" is a gate that passes
# because it stopped looking.
SCHEMA_VERSION = 3

# Branch cases the fixture set must cover. Values are stored in case_labels.
CASE_NO_CANDIDATES = "no_candidates"        # a terminal node_no_candidates run
CASE_UNKNOWN_STAGE = "unknown_stage"        # extract_patient_stage() -> None
CASE_MESH_FALLBACK = "mesh_fallback"        # Stage 1 took EXPANSION_PATH_FALLBACK
CASE_ABLATION = "ablation"                  # run under a File 26 config
CASE_MCODE_VARIANT = "mcode_variant"        # structural genomic variant detection
CASE_GPT4O_RETRY = "gpt4o_retry"            # the MAX_GPT4O_RETRIES loop
CASE_TRUNCATION = "truncation_split"        # the MAX_TRUNCATION_SPLITS loop
CASE_NORMAL = "normal"                      # full pipeline, no branch of note

ALL_BRANCH_CASES = (
    CASE_NO_CANDIDATES,
    CASE_UNKNOWN_STAGE,
    CASE_MESH_FALLBACK,
    CASE_ABLATION,
    CASE_GPT4O_RETRY,
    CASE_MCODE_VARIANT,
    CASE_TRUNCATION,
)

FIXTURE_KIND_RECORDED = "recorded"
FIXTURE_KIND_CONSTRUCTED = "constructed"

# Where a fixture's source bundle lives.
BUNDLE_IN_COHORT = "cohort"    # under data_fhir_path, untouched
BUNDLE_DERIVED = "derived"     # rebuilt at replay from `derivation` (schema v2;
                               # v1 wrote the bundle to disk instead)

# Terminal node labels, derived rather than reported: the three terminal nodes
# in File 13 do not stamp their own name onto the result, but they are
# distinguishable by the keys they set.
TERMINAL_FINALIZE = "node_finalize"
TERMINAL_NO_CANDIDATES = "node_no_candidates"
TERMINAL_ERROR = "node_error_handler"

# What _terminal_node() reports when the result carries no stamp at all. Not
# one of the three: a run that produced an unstamped result did not come from
# a terminal node this file knows about, and mapping it onto one would hide
# exactly the change this field exists to catch.
TERMINAL_UNSTAMPED = "unstamped"


#------------------------------------------------------------------------------


# ===========================================================================
# HASHING HELPERS
# ===========================================================================

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_json(obj) -> str:
    """Digest of a JSON-serialisable object, stable across key insertion order."""
    return sha256_text(
        json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    )


#------------------------------------------------------------------------------


# ===========================================================================
# RECORDING SINK
# ===========================================================================

class RecordingSink:
    """Collects everything observed during one pipeline run.

    Every append is under a lock: Stage 2 submits four retrieval channels to a
    ThreadPoolExecutor, so the sparse-embedding and Qdrant recorders are
    genuinely concurrent. list.append is atomic under CPython's GIL, but the
    read-modify-write of the per-channel dicts is not, and a fixture that
    occasionally loses a channel is worse than one that never records it.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.openai_embeddings = []
        self.sparse_embeddings = []
        self.cross_encoder = []
        self.chat_completions = []
        # Qdrant is observed rather than replayed: retrieval runs live against
        # the pinned collection on both capture and replay.
        self.qdrant_queries = []     # ordered NCT IDs per retrieval channel
        self.qdrant_scrolls = []     # payload-backfill calls
        # Filled by the replay harness (46-) when it is asked for a recording
        # it does not have. Empty on a capture run by construction.
        self.replay_misses = []

    def add(self, bucket_name: str, record: dict) -> int:
        with self._lock:
            bucket = getattr(self, bucket_name)
            record["call_index"] = len(bucket)
            bucket.append(record)
            return record["call_index"]

    def note_miss(self, field: str, detail: str) -> None:
        with self._lock:
            self.replay_misses.append({"field": field, "detail": detail})


#------------------------------------------------------------------------------


# ===========================================================================
# HOOKS: OpenAI
# ===========================================================================
#
# The proxy wraps the CLIENT rather than get_embedding() and
# call_matching_model(), so what is recorded is the literal kwargs that go over
# the wire. Recording one level up would mean rebuilding the request from the
# same constants the pipeline used, which records the fixture's belief about
# the request instead of the request.

class _OpenAIEmbeddingsShim:
    def __init__(self, inner, on_call):
        self._inner = inner
        self._on_call = on_call

    def create(self, **kwargs):
        return self._on_call(self._inner, kwargs)


class _OpenAIChatCompletionsShim:
    def __init__(self, inner, on_call):
        self._inner = inner
        self._on_call = on_call

    def create(self, **kwargs):
        return self._on_call(self._inner, kwargs)


class _OpenAIChatShim:
    def __init__(self, completions):
        self.completions = completions


class OpenAIProxy:
    """Stands in for openai_client, intercepting the two calls the pipeline makes.

    Anything else is forwarded untouched, so a caller that reaches for a part
    of the client this file has never seen still works.
    """

    def __init__(self, inner, on_embedding, on_chat):
        self._inner = inner
        self.embeddings = _OpenAIEmbeddingsShim(inner.embeddings, on_embedding)
        self.chat = _OpenAIChatShim(
            _OpenAIChatCompletionsShim(inner.chat.completions, on_chat)
        )

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _recording_embedding(sink: RecordingSink):
    def handler(inner, kwargs):
        response = inner.create(**kwargs)
        sink.add("openai_embeddings", {
            "model": kwargs.get("model"),
            "input": kwargs.get("input"),
            "vector": list(response.data[0].embedding),
        })
        return response
    return handler


def _reasoning_tokens_of(usage):
    """The reasoning subtotal of a Chat Completions usage block, or None.

    Read through getattr rather than indexed, because a non-reasoning model
    omits completion_tokens_details entirely and an older client object may not
    define the attribute at all. None means "this response reported no
    breakdown", which is not the same as 0 and is stored as NULL downstream.
    """
    details = getattr(usage, "completion_tokens_details", None)
    return getattr(details, "reasoning_tokens", None) if details else None


def _relabelled_response(response, finish_reason: str):
    """The same response, reporting a different finish_reason.

    Only the field the pipeline branches on is replaced; content and usage are
    the real ones. Used to inject a truncation into a real run so the splitter
    can be captured executing, rather than having its expected output guessed
    at by hand.
    """
    choice = response.choices[0]
    return SimpleNamespace(
        choices=[SimpleNamespace(message=choice.message,
                                 finish_reason=finish_reason)],
        model=response.model,
        usage=response.usage,
    )


def _recording_chat(sink: RecordingSink, truncate_first_call: bool = False):
    """Record every Stage 5 exchange, optionally injecting one truncation.

    truncate_first_call relabels the FIRST response's finish_reason to
    "length". The pipeline then really splits, really issues the half-batch
    calls, and the recording captures all three genuinely — so the fixture's
    deterministic prefix is observed rather than hand-derived. The injected
    value is what gets recorded, so a replay reproduces the same split.
    """
    call_number = {"n": 0}

    def handler(inner, kwargs):
        response = inner.create(**kwargs)
        choice = response.choices[0]

        finish_reason = choice.finish_reason
        if truncate_first_call and call_number["n"] == 0:
            finish_reason = "length"
        call_number["n"] += 1

        sink.add("chat_completions", {
            # Verbatim: both messages in full, and every generation parameter
            # that can change the answer. Anything omitted here is a change a
            # replay cannot see.
            # Every generation parameter is read out of kwargs rather than
            # listed from config, so a parameter the pipeline STOPS sending
            # records as None and the change is visible in the fixture diff.
            # temperature and max_tokens are still recorded for exactly that
            # reason: gpt-5.6-terra rejects both (max_tokens outright,
            # temperature for any value but its default), so they are expected
            # to be None from 2026-08-04 onward, and a fixture where they
            # reappear is a fixture whose call shape regressed.
            "request": {
                "model": kwargs.get("model"),
                "messages": copy.deepcopy(kwargs.get("messages")),
                "temperature": kwargs.get("temperature"),
                "max_tokens": kwargs.get("max_tokens"),
                "max_completion_tokens": kwargs.get("max_completion_tokens"),
                "reasoning_effort": kwargs.get("reasoning_effort"),
                "seed": kwargs.get("seed"),
            },
            "response": {
                "content": choice.message.content,
                "finish_reason": finish_reason,
                "model": response.model,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens,
                    # Already includes reasoning_tokens below; the two are one
                    # billed total, not two. Recorded separately so a replay
                    # can reproduce the breakdown File 13 logs.
                    "completion_tokens": response.usage.completion_tokens,
                    # None when the response carried no breakdown at all (a
                    # non-reasoning model). Distinct from 0, and File 46
                    # reproduces the distinction.
                    "reasoning_tokens": _reasoning_tokens_of(response.usage),
                },
            },
        })
        if finish_reason != choice.finish_reason:
            return _relabelled_response(response, finish_reason)
        return response
    return handler


#------------------------------------------------------------------------------


# ===========================================================================
# HOOKS: FastEmbed sparse query model
# ===========================================================================

class SparseModelProxy:
    """Records every BM25 query vector FastEmbed produces.

    query_embed must return an ITERATOR: File 13 calls next() on it. Recording
    therefore happens when the caller pulls the value, not when the call is
    made, which is also when the real model does its work.
    """

    def __init__(self, inner, sink: RecordingSink):
        self._inner = inner
        self._sink = sink

    def query_embed(self, query_text, **kwargs):
        embedding = next(self._inner.query_embed(query_text, **kwargs))
        self._sink.add("sparse_embeddings", {
            "query": query_text,
            "indices": [int(i) for i in embedding.indices.tolist()],
            "values": [float(v) for v in embedding.values.tolist()],
        })
        yield embedding

    def __getattr__(self, name):
        return getattr(self._inner, name)


#------------------------------------------------------------------------------


# ===========================================================================
# HOOKS: Qdrant
# ===========================================================================
#
# Qdrant is NOT replayed. The corpus is the fixed input the fixture is pinned
# against, so retrieval runs live on both capture and replay and the diff over
# the recorded order is what proves the two runs asked the same questions.

class QdrantProxy:
    def __init__(self, inner, sink: RecordingSink):
        self._inner = inner
        self._sink = sink

    def query_points(self, **kwargs):
        response = self._inner.query_points(**kwargs)
        # `using` names the sparse vector field; its absence means the dense
        # channel. This is the same key File 13 records channel status under,
        # with "-bm25" stripped so the two line up.
        using = kwargs.get("using")
        channel = using.replace("-bm25", "") if using else "dense"
        self._sink.add("qdrant_queries", {
            "channel": channel,
            "using": using,
            "limit": kwargs.get("limit"),
            "nct_ids": [p.payload.get("nct_id", "") for p in response.points],
        })
        return response

    def scroll(self, **kwargs):
        points, next_offset = self._inner.scroll(**kwargs)
        self._sink.add("qdrant_scrolls", {
            "limit": kwargs.get("limit"),
            "nct_ids": [p.payload.get("nct_id", "") for p in points],
        })
        return points, next_offset

    def __getattr__(self, name):
        return getattr(self._inner, name)


#------------------------------------------------------------------------------


# ===========================================================================
# HOOKS: MedCPT cross-encoder
# ===========================================================================

def _recording_medcpt(inner, sink: RecordingSink):
    def wrapper(query, trial_texts):
        scores = inner(query, trial_texts)
        sink.add("cross_encoder", {
            "query": query,
            "n_pairs": len(trial_texts),
            "trial_texts_sha256": sha256_json(list(trial_texts)),
            "trial_texts": list(trial_texts),
            # float() on a numpy float32 is exact, and the replay side rebuilds
            # a float32 array from these doubles, so the model's own dtype —
            # and therefore its argsort tie-breaking — is preserved.
            "scores": [float(s) for s in scores],
            "dtype": str(getattr(scores, "dtype", "float32")),
        })
        return scores
    return wrapper


#------------------------------------------------------------------------------


# ===========================================================================
# HOOK INSTALLATION
# ===========================================================================
#
# Every project file is exec'd into THIS module's globals(), so File 13's
# functions resolve `openai_client`, `qdrant_client`, `_bm25_query_model` and
# `medcpt_score_pairs` out of this dict at call time. Rebinding a name here is
# therefore all it takes to redirect the pipeline, and restoring it puts things
# back exactly.

_HOOKED_NAMES = (
    "openai_client",
    "qdrant_client",
    "_bm25_query_model",
    "medcpt_score_pairs",
)


def install_recording_hooks(sink: RecordingSink,
                            truncate_first_call: bool = False) -> dict:
    """Redirect all four seams to recorders. Returns the saved originals."""
    saved = {name: globals()[name] for name in _HOOKED_NAMES}

    globals()["openai_client"] = OpenAIProxy(
        saved["openai_client"],
        _recording_embedding(sink),
        _recording_chat(sink, truncate_first_call),
    )
    globals()["qdrant_client"] = QdrantProxy(saved["qdrant_client"], sink)
    globals()["_bm25_query_model"] = SparseModelProxy(saved["_bm25_query_model"], sink)
    globals()["medcpt_score_pairs"] = _recording_medcpt(saved["medcpt_score_pairs"], sink)

    return saved


def restore_hooks(saved: dict) -> None:
    for name, value in saved.items():
        globals()[name] = value


#------------------------------------------------------------------------------


# ===========================================================================
# THE DETERMINISTIC PREFIX
# ===========================================================================

def _nct(trial_obj: Dict) -> str:
    return trial_obj.get("trial", {}).get("nct_id", "")


def _terminal_node(result: Dict) -> str:
    """Which of the three terminal nodes produced this result.

    Read from result["terminal_node"], which each terminal node in File 13 now
    stamps itself. This used to INFER it from incidental structure — a
    non-empty "error" meant the error handler, a "message" key meant no
    candidates — and that rule would have started lying the moment item 20
    added a "message" key to node_finalize.

    Absence is not defaulted to a node. A result with no stamp came from
    something that is not one of the three terminal nodes, and saying so is
    the only honest answer.
    """
    stamped = result.get("terminal_node")
    if stamped:
        return stamped
    return TERMINAL_UNSTAMPED


def build_deterministic_prefix(final_state: Dict,
                               result: Dict,
                               sink: RecordingSink) -> Dict:
    """Assemble everything that must reproduce exactly.

    Reads three sources and keeps them separate on purpose:
      - final_state, for what each stage computed;
      - result, for what the terminal node published (the two are not the same
        — node_no_candidates publishes zeros for stages that never ran);
      - sink, for what was observed at the client boundary, which is the only
        place the per-channel retrieval ORDER exists at all.
    """
    # --- Stage 2, from the Qdrant client rather than from state -------------
    # state carries per-channel COUNTS and statuses; only the client saw the
    # ranked NCT IDs, and the order is the thing a fusion change reorders
    # without changing any count.
    retrieval_order = {}
    retrieval_counts = {}
    for record in sink.qdrant_queries:
        channel = record["channel"]
        # A channel is queried once per run. If that ever stops being true the
        # later call would silently overwrite the earlier one, so keep both.
        if channel in retrieval_order:
            channel = f"{channel}#{record['call_index']}"
        retrieval_order[channel] = list(record["nct_ids"])
        retrieval_counts[channel] = len(record["nct_ids"])

    hybrid_results = final_state.get("hybrid_results") or []
    reranked = final_state.get("reranked_trials") or []
    filtered = final_state.get("filtered_trials") or []
    evaluations = result.get("matches", []) + result.get("near_misses", []) + \
        result.get("not_evaluable", [])

    # --- Stage 4 cost cap, which no counter reports directly ----------------
    # It is the only drop between the quality gate and the evaluated set, so it
    # is exactly the difference between those two counts.
    after_quality = final_state.get("candidates_after_quality_filter")
    cap_dropped = None
    if after_quality is not None:
        cap_dropped = max(0, after_quality - len(filtered))

    return {
        # --- The parsed patient record ------------------------------------
        # Diffed in full. 46- re-parses the source bundle instead of reusing
        # this, so a File 07 regression shows up here rather than being fed
        # back in as an input and cancelling itself out.
        "patient_data": final_state.get("patient_data"),

        "stage1": {
            "mesh_resolution": final_state.get("mesh_resolution"),
            "query_expansion_path": final_state.get("query_expansion_path"),
            "expanded_query": final_state.get("expanded_query"),
            "rerank_queries": list(final_state.get("rerank_queries") or []),
            # Which detector found the patient's genomic variants. Diffed so a
            # refactor that quietly loses the structural paths and falls back
            # to free-text matching shows up as a field, not as a subtly
            # different query.
            "variant_detection": extract_genomic_variant_terms(
                final_state.get("patient_data") or {}
            ),
            "expansion_prompt": final_state.get("expansion_prompt"),
            "expansion_input_tokens": final_state.get("expansion_input_tokens"),
            "expansion_output_tokens": final_state.get("expansion_output_tokens"),
        },

        "stage2": {
            "retrieval_order": retrieval_order,
            "retrieval_counts": retrieval_counts,
            "retrieval_channels": final_state.get("retrieval_channels"),
            "retrieval_channels_expected": final_state.get("retrieval_channels_expected"),
            "retrieval_channels_ok": final_state.get("retrieval_channels_ok"),
            "retrieval_degraded": final_state.get("retrieval_degraded"),
            "retrieval_trials_lost": final_state.get("retrieval_trials_lost"),
            "bm25_retrieved": final_state.get("bm25_retrieved"),
            "vector_retrieved": final_state.get("vector_retrieved"),
            "fusion_pool_order": [_nct(t) for t in hybrid_results],
            "fusion_scores": [t.get("fusion_score") for t in hybrid_results],
            "scroll_backfill": [
                {"limit": s["limit"], "nct_ids": list(s["nct_ids"])}
                for s in sink.qdrant_scrolls
            ],
        },

        "stage3": {
            "reranked_order": [_nct(t) for t in reranked],
            "rerank_scores": [t.get("rerank_score") for t in reranked],
            "rerank_scores_raw": [t.get("rerank_score_raw") for t in reranked],
            "mesh_boosts": [t.get("mesh_boost") for t in reranked],
            "mesh_boost_tiers": [t.get("mesh_boost_tier") for t in reranked],
            # A set has no stable iteration order, so it is sorted before it is
            # stored. The pipeline sorts it too wherever order matters.
            "patient_trees": sorted(final_state.get("patient_trees") or []),
        },

        "stage4": {
            "filtered_order": [_nct(t) for t in filtered],
            "candidates_after_rule_filter": final_state.get("candidates_after_rule_filter"),
            "candidates_after_quality_filter": after_quality,
            "candidates_filtered": len(filtered),
            "drops": {
                "mesh": final_state.get("mesh_dropped"),
                "stage": final_state.get("stage_dropped"),
                "histology": final_state.get("histology_dropped"),
                "age": final_state.get("age_dropped"),
                "sex": final_state.get("sex_dropped"),
                "quality": final_state.get("quality_dropped"),
                "cost_cap": cap_dropped,
            },
            "quality_threshold": final_state.get("quality_threshold"),
            "mesh_filter_applied": final_state.get("mesh_filter_applied"),
            "mesh_filter_skip_reason": final_state.get("mesh_filter_skip_reason"),
        },

        "stage5": {
            # One digest per call, in call order. The prompt is deterministic
            # given the patient and the filtered set, so a change to it is a
            # code change — and it is the single most consequential thing item
            # 20 could alter without changing any count.
            "request_sha256_by_call": [
                sha256_json(c["request"]) for c in sink.chat_completions
            ],
            "gpt4o_calls": len(sink.chat_completions),
            # Reported by the stage as well as counted at the client, because
            # the two agreeing is itself the check: a split run that recorded
            # fewer exchanges than it made would replay a different number of
            # requests than it captured.
            "gpt4o_calls_reported": result.get("gpt4o_calls"),
            # The truncation budget, separate from the retry budget below.
            "gpt4o_truncation_splits": result.get("gpt4o_truncation_splits"),
            "not_evaluable_truncated": result.get("not_evaluable_truncated"),
            # The pre-call estimate. Diffed exactly: it is a pure function of
            # the filtered trial set and the File 03 constants, so a change to
            # either shows up here rather than only in a log line.
            "gpt4o_output_tokens_estimated": result.get("gpt4o_output_tokens_estimated"),
            "finish_reasons": [
                c["response"].get("finish_reason") for c in sink.chat_completions
            ],
            "gpt4o_retries": result.get("gpt4o_retries"),
            "cross_vocab_remaps": result.get("cross_vocab_remaps"),
            "gpt4o_input_tokens": result.get("gpt4o_input_tokens"),
            "gpt4o_output_tokens": result.get("gpt4o_output_tokens"),
            "gpt4o_prompt_sha256": sha256_text(result.get("gpt4o_prompt") or ""),
            "verdicts": [
                {
                    "nct_id": e.get("nct_id"),
                    "eligible": e.get("eligible"),
                    "match_score": e.get("match_score"),
                    "trial_number": e.get("trial_number"),
                    "criteria_not_applicable": e.get("criteria_not_applicable"),
                    # None for an ordinary verdict; one of File 13's
                    # NOT_EVALUABLE_* constants for a trial that entered
                    # Stage 5 and left without one. This is what makes
                    # "no trial was lost" a diffed fact rather than a claim.
                    "not_evaluable_reason": e.get("not_evaluable_reason"),
                }
                for e in evaluations
            ],
        },

        "terminal": {
            "terminal_node": _terminal_node(result),
            "error": result.get("error"),
            "matches": [e.get("nct_id") for e in result.get("matches", [])],
            "near_misses": [e.get("nct_id") for e in result.get("near_misses", [])],
            "not_evaluable": [e.get("nct_id") for e in result.get("not_evaluable", [])],
            "candidates_retrieved": result.get("candidates_retrieved"),
            "candidates_reranked": result.get("candidates_reranked"),
            "candidates_evaluated": result.get("candidates_evaluated"),
            "criteria_not_applicable": result.get("criteria_not_applicable"),
            "primary_condition": result.get("primary_condition"),
            "condition_count": result.get("condition_count"),
            "medication_count": result.get("medication_count"),
            "allergy_count": result.get("allergy_count"),
            # --- Every degradation key -------------------------------------
            # NULL and 0 are different facts here (see CLAUDE.md); they are
            # stored as-is and diffed with exact equality, so None never
            # compares equal to 0.
            "degradation": {
                "query_expansion_path": result.get("query_expansion_path"),
                "mesh_resolution": result.get("mesh_resolution"),
                "mesh_filter_applied": result.get("mesh_filter_applied"),
                "mesh_filter_skip_reason": result.get("mesh_filter_skip_reason"),
                "retrieval_channels": result.get("retrieval_channels"),
                "retrieval_channels_expected": result.get("retrieval_channels_expected"),
                "retrieval_channels_ok": result.get("retrieval_channels_ok"),
                "retrieval_degraded": result.get("retrieval_degraded"),
                "retrieval_trials_lost": result.get("retrieval_trials_lost"),
                "bm25_retrieved": result.get("bm25_retrieved"),
                "vector_retrieved": result.get("vector_retrieved"),
                "birth_date_precision": result.get("birth_date_precision"),
                "age_reference_date": result.get("age_reference_date"),
                "ecog_value": result.get("ecog_value"),
                "ecog_selection": result.get("ecog_selection"),
                "ecog_observations_found": result.get("ecog_observations_found"),
                "ablation_flags": result.get("ablation_flags"),
            },
        },
    }


#------------------------------------------------------------------------------


def flatten_prefix(node, path: str = "") -> Dict:
    """Flatten a deterministic prefix to {dotted.path: leaf value}.

    Lists are indexed rather than compared whole so a diff names the element
    that moved instead of reprinting a 100-item list twice. A list whose LENGTH
    changed produces one entry per surplus index on the longer side, which is
    what a reader needs to see.
    """
    flat = {}
    if isinstance(node, dict):
        if not node:
            flat[path] = {}
        for key in sorted(node.keys(), key=str):
            child = f"{path}.{key}" if path else str(key)
            flat.update(flatten_prefix(node[key], child))
    elif isinstance(node, list):
        if not node:
            flat[path] = []
        for index, item in enumerate(node):
            flat.update(flatten_prefix(item, f"{path}[{index}]"))
    else:
        flat[path] = node
    return flat


#------------------------------------------------------------------------------


# ===========================================================================
# FIXTURE I/O
# ===========================================================================

# Fixtures are stored gzipped. They are machine-read only -- 45- writes them,
# 46- diffs them, items 22 and 64 consume them -- and the point of schema v2 is
# a directory small enough to live in the repository, which item 64's deploy
# gate needs. Uncompressed they are ~70% parsed patient record, and a Synthea
# patient parses to megabytes of observations; gzip takes the set from tens of
# megabytes to a few. Nothing about the format changes, only how it is stored.
FIXTURE_SUFFIX = ".json.gz"


def fixture_path(fixture_id: str, root: str = None) -> str:
    return os.path.join(root or FIXTURE_ROOT, f"{fixture_id}{FIXTURE_SUFFIX}")


def write_fixture(fixture: Dict, root: str = None) -> str:
    path = fixture_path(fixture["fixture_id"], root)
    # mtime=0 so two captures of identical content produce identical bytes;
    # a gzip header timestamp would make every re-capture a diff in git even
    # when nothing changed.
    with gzip.GzipFile(path, "wb", compresslevel=9, mtime=0) as fh:
        fh.write(json.dumps(fixture, ensure_ascii=False).encode("utf-8"))
    return path


def load_fixture(path: str) -> Dict:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        fixture = json.load(fh)

    version = fixture.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"{os.path.basename(path)}: schema_version {version!r}, but this "
            f"code reads version {SCHEMA_VERSION}. Re-capture the fixture "
            f"rather than diffing across versions — a field whose meaning "
            f"changed compares equal for the wrong reason."
        )
    return fixture


def list_fixtures(root: str = None) -> List[str]:
    """Every fixture in the directory.

    Matches on FIXTURE_SUFFIX rather than on "*.json", so the plain-JSON index
    is excluded structurally instead of by name. Schema v1 wrote derived FHIR
    bundles here too, which had to be filtered out by a second suffix; v2
    stores a recipe instead and writes no bundles at all.
    """
    root = root or FIXTURE_ROOT
    return sorted(glob.glob(os.path.join(root, "*" + FIXTURE_SUFFIX)))


#------------------------------------------------------------------------------


# ===========================================================================
# ENVIRONMENT BLOCK
# ===========================================================================

# Page size for the content digest scroll. Large enough that a ~10k-point
# collection is a handful of round trips, small enough not to hold the whole
# collection's ids in one response.
COLLECTION_DIGEST_PAGE = 4096


def compute_collection_digest(collection_name: str) -> tuple:
    """Fingerprint what is IN the collection, not just what it is called.

    Pinning the resolved name catches the weekly alias swap (File 11 builds a
    new timestamped collection and moves the alias). It does not catch an edit
    to the collection the alias already points at — a re-index in place, a
    partial load, a deleted trial. That shows up later as retrieval-order
    differences in every fixture, which reads exactly like a code regression
    and is the wrong thing to spend a day on during item 20.

    Fetches ONLY the nct_id payload field and no vectors: the digest has to be
    cheap enough to run on every replay, and pulling full_trial_json for ~10k
    trials to hash it would not be.

    Returns (digest, elapsed_seconds). The timing is returned rather than
    stored — a duration is not part of the collection's identity, and putting
    one inside the digest would make two identical indexes compare unequal.
    """
    start = time.time()

    @qdrant_retry
    def _page(offset):
        return qdrant_client.scroll(
            collection_name=collection_name,
            limit=COLLECTION_DIGEST_PAGE,
            offset=offset,
            with_payload=["nct_id"],
            with_vectors=False,
        )

    nct_ids = []
    offset = None
    while True:
        points, offset = _page(offset)
        nct_ids.extend((p.payload or {}).get("nct_id", "") for p in points)
        if offset is None:
            break

    # Sorted before hashing: scroll order is the server's business and is not
    # a property of the contents. The point count is kept alongside because it
    # separates "different trials" from "same trials, different number of
    # points per trial", which are different kinds of index change.
    nct_ids.sort()
    digest = {
        "point_count": len(nct_ids),
        "distinct_nct_ids": len(set(nct_ids)),
        "nct_id_sha256": sha256_text("\n".join(nct_ids)),
    }
    return digest, round(time.time() - start, 3)


def _resolve_and_verify_collection() -> tuple:
    """Pin the real collection name, and prove it is a real collection.

    resolve_qdrant_collection() falls back to the COLLECTION_NAME alias string
    when it cannot resolve, and a fixture pinned to that fallback would be
    pinned to whatever the alias points at next week — precisely the failure
    this field exists to prevent. So the fallback is detected, reported, and
    the name is checked against the server before it is written down.
    """
    resolved = resolve_qdrant_collection()
    alias_resolved = resolved != COLLECTION_NAME

    try:
        qdrant_client.get_collection(resolved)
    except Exception as exc:
        raise RuntimeError(
            f"Cannot pin Qdrant collection: get_collection({resolved!r}) failed "
            f"({type(exc).__name__}: {exc}). Refusing to write a fixture whose "
            f"pinned index cannot be confirmed to exist."
        )

    if not alias_resolved:
        print(f"  WARNING: '{COLLECTION_NAME}' did not resolve through an alias. "
              f"Pinning the alias name itself; a future alias swap will make "
              f"this fixture's retrieval diff meaningless rather than failing "
              f"the collection check.")

    return resolved, alias_resolved


def build_environment_block() -> Dict:
    resolved, alias_resolved = _resolve_and_verify_collection()
    digest, elapsed = compute_collection_digest(resolved)
    print(f"  Collection digest: {digest['point_count']} points, "
          f"{digest['distinct_nct_ids']} distinct NCT IDs, "
          f"sha256 {digest['nct_id_sha256'][:16]}... ({elapsed}s)")
    return {
        "qdrant_collection": resolved,
        "collection_alias": COLLECTION_NAME,
        "alias_resolved": alias_resolved,
        # What is in the collection, not just what it is called. Enforced by
        # 46- before any fixture is replayed.
        "collection_digest": digest,
        "data_snapshot_date": DATA_SNAPSHOT_DATE,
        "age_reference_date": get_age_reference_date().isoformat(),
        "embedding_model": EMBEDDING_MODEL,
        # The model REQUESTED. What the API answered with is recorded per call
        # in recordings.chat_completions[*].response.model; for gpt-5.6-terra
        # the two are the same string, for an alias that resolves to a dated
        # snapshot they are not, and the fixture keeps both.
        "matching_model": MATCHING_MODEL,
        # None from the 2026-08-04 migration onward: gpt-5.6-terra rejects
        # temperature for any value but its default, so the pipeline does not
        # send it. Kept in the environment block because a fixture recorded
        # before that date carries 0 here, and the difference between the two
        # is precisely what a cross-era fixture diff should show.
        "matching_temperature": MATCHING_TEMPERATURE,
        "matching_max_tokens": MATCHING_MAX_TOKENS,
        "matching_reasoning_effort": MATCHING_REASONING_EFFORT,
        "matching_seed": MATCHING_SEED,
        "cross_encoder_model": "ncbi/MedCPT-Cross-Encoder",
        "sparse_model": "Qdrant/bm25",
        # The File 03 constants the prefix is a function of. A diff caused by
        # editing one of these is a configuration change, not a refactor
        # regression, and without them recorded the two are indistinguishable.
        "tunables": {
            "BM25_RETRIEVAL_SIZE": BM25_RETRIEVAL_SIZE,
            "VECTOR_RETRIEVAL_SIZE": VECTOR_RETRIEVAL_SIZE,
            "RRF_POOL_SIZE": RRF_POOL_SIZE,
            "TOP_K_CANDIDATES": TOP_K_CANDIDATES,
            "MAX_TRIALS_FOR_EVALUATION": MAX_TRIALS_FOR_EVALUATION,
            "RERANK_SCORE_THRESHOLD": RERANK_SCORE_THRESHOLD,
            "QUALITY_THRESHOLD_PERCENTILE": QUALITY_THRESHOLD_PERCENTILE,
            "MESH_BOOST_DIRECT_FRACTION": MESH_BOOST_DIRECT_FRACTION,
            "MESH_BOOST_PAN_FRACTION": MESH_BOOST_PAN_FRACTION,
            "MESH_BOOST_DIRECT_FLOOR": MESH_BOOST_DIRECT_FLOOR,
            "MESH_BOOST_PAN_FLOOR": MESH_BOOST_PAN_FLOOR,
            "MAX_GPT4O_RETRIES": MAX_GPT4O_RETRIES,
            "MAX_TRUNCATION_SPLITS": MAX_TRUNCATION_SPLITS,
            "MATCHING_OUTPUT_TOKENS_PER_TRIAL": MATCHING_OUTPUT_TOKENS_PER_TRIAL,
            "MATCHING_OUTPUT_SPLIT_FRACTION": MATCHING_OUTPUT_SPLIT_FRACTION,
            # Both shape the Stage 5 request and therefore the verdicts. They
            # are duplicated from the environment block above ON PURPOSE: only
            # what is in "tunables" is compared by File 46's diff_tunables(),
            # so a reasoning-effort change would otherwise be recorded and
            # never reported, and a reader would hunt a refactor for a verdict
            # difference that a one-line config edit caused.
            "MATCHING_REASONING_EFFORT": MATCHING_REASONING_EFFORT,
            "MATCHING_MAX_TOKENS": MATCHING_MAX_TOKENS,
            "MAX_VARIANT_TERMS": MAX_VARIANT_TERMS,
            "CHARS_PER_TOKEN": CHARS_PER_TOKEN,
        },
    }


#------------------------------------------------------------------------------


# ===========================================================================
# COMPLETENESS CHECKS
# ===========================================================================

class IncompleteRecording(RuntimeError):
    """A run finished but its recording cannot serve a replay.

    Raised rather than warned: a fixture missing one of its model outputs
    replays as a MISS on every future run, and the first person to see it will
    be debugging item 20's refactor with a broken instrument.
    """


def verify_recording_complete(fixture: Dict, sink: RecordingSink) -> None:
    """Fail loudly if anything a replay will ask for was not recorded."""
    problems = []

    prefix = fixture["deterministic_prefix"]
    flags = fixture["inputs"]["ablation_flags"] or {}
    retrieval_mode = flags.get("retrieval_mode", "hybrid")
    terminal = prefix["terminal"]["terminal_node"]

    # --- Dense channel: exactly one embedding, unless it was ablated away ---
    n_embeddings = len(sink.openai_embeddings)
    if retrieval_mode == "bm25_only":
        if n_embeddings:
            problems.append(
                f"retrieval_mode=bm25_only but {n_embeddings} embedding call(s) "
                f"were made"
            )
    elif n_embeddings != 1:
        problems.append(f"expected 1 embedding call, recorded {n_embeddings}")

    # --- Sparse channels: three, unless they were ablated away -------------
    n_sparse = len(sink.sparse_embeddings)
    if retrieval_mode == "vector_only":
        if n_sparse:
            problems.append(
                f"retrieval_mode=vector_only but {n_sparse} sparse query "
                f"vector(s) were generated"
            )
    elif n_sparse != 3:
        problems.append(f"expected 3 sparse query vectors, recorded {n_sparse}")

    # --- Cross-encoder: one pass per rerank query --------------------------
    n_rerank_queries = len(prefix["stage1"]["rerank_queries"])
    n_medcpt = len(sink.cross_encoder)
    if flags.get("skip_cross_encoder"):
        if n_medcpt:
            problems.append(
                f"skip_cross_encoder set but {n_medcpt} cross-encoder pass(es) ran"
            )
    elif not prefix["stage2"]["fusion_pool_order"]:
        if n_medcpt:
            problems.append(
                f"retrieval returned nothing but {n_medcpt} cross-encoder "
                f"pass(es) ran"
            )
    elif n_medcpt != n_rerank_queries:
        problems.append(
            f"expected {n_rerank_queries} cross-encoder pass(es) (one per "
            f"rerank query), recorded {n_medcpt}"
        )
    for record in sink.cross_encoder:
        if len(record["scores"]) != record["n_pairs"]:
            problems.append(
                f"cross-encoder call {record['call_index']}: "
                f"{record['n_pairs']} pairs but {len(record['scores'])} scores"
            )

    # --- Stage 5: one exchange, unless the run never reached it ------------
    n_chat = len(sink.chat_completions)
    if terminal == TERMINAL_NO_CANDIDATES:
        if n_chat:
            problems.append(
                f"run ended at node_no_candidates but {n_chat} Stage 5 call(s) "
                f"were made"
            )
    elif n_chat < 1:
        problems.append("no Stage 5 request/response pair was recorded")

    # A split run issues one call per chunk plus the truncated one that caused
    # the split. The stage's own count and the number of recorded exchanges
    # have to agree, or a replay would issue a different number of requests
    # than the capture did.
    # Compared only on a run with no retries. Stage 5's counter is per node
    # INVOCATION and resets when the retry router re-enters the node, while the
    # sink counts every exchange across the whole run — so on a retried run the
    # two legitimately differ, and requiring them to match would fail a fixture
    # for recording the truth.
    _reported = prefix["stage5"].get("gpt4o_calls_reported")
    _retries = prefix["stage5"].get("gpt4o_retries") or 0
    if _reported is not None and not _retries and _reported != n_chat:
        problems.append(
            f"Stage 5 reported {_reported} call(s) but {n_chat} were recorded"
        )
    elif _reported is not None and _retries and n_chat < _reported:
        problems.append(
            f"Stage 5 reported {_reported} call(s) on its final attempt but "
            f"only {n_chat} exchange(s) were recorded in total"
        )
    if (prefix["stage5"].get("gpt4o_truncation_splits") or 0) and n_chat < 2:
        problems.append(
            f"truncation splits were reported but only {n_chat} call(s) recorded"
        )
    for record in sink.chat_completions:
        if not (record["response"].get("content") or "").strip():
            problems.append(
                f"Stage 5 call {record['call_index']} recorded an empty response"
            )
        if len(record["request"].get("messages") or []) != 2:
            problems.append(
                f"Stage 5 call {record['call_index']} did not record both messages"
            )

    # --- Every retrieval channel accounted for -----------------------------
    channels = prefix["stage2"]["retrieval_channels"]
    if channels is None:
        problems.append("Stage 2 reported no retrieval_channels at all")
    else:
        missing = [c for c in RETRIEVAL_CHANNELS if c not in channels]
        if missing:
            problems.append(f"retrieval_channels missing: {missing}")

    # --- The fixture must survive a JSON round trip ------------------------
    try:
        reloaded = json.loads(json.dumps(fixture, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        problems.append(f"fixture is not JSON-serialisable: {exc}")
    else:
        if flatten_prefix(reloaded["deterministic_prefix"]) != \
                flatten_prefix(fixture["deterministic_prefix"]):
            problems.append(
                "the deterministic prefix does not survive a JSON round trip; "
                "a replay would diff against a value that was never captured"
            )

    if problems:
        raise IncompleteRecording(
            f"{fixture['fixture_id']}: recording is incomplete —\n  - "
            + "\n  - ".join(problems)
        )


#------------------------------------------------------------------------------


# ===========================================================================
# CAPTURE
# ===========================================================================

def capture_fixture(fixture_id: str,
                    bundle_path: str,
                    case_labels: List[str],
                    graph: object,
                    ablation_flags: Dict = None,
                    ablation_config_name: str = None,
                    environment: Dict = None,
                    bundle_location: str = BUNDLE_IN_COHORT,
                    case_evidence: Dict = None,
                    construction: Dict = None,
                    derivation: Dict = None,
                    truncate_first_call: bool = False) -> Dict:
    """Run one patient end to end on the current code and record everything.

    bundle_location says where 46- gets the bundle to re-parse: the cohort, or
    rebuilt from `derivation`. construction is non-None when the BUNDLE was
    derived rather than taken from the cohort — the run is still a real one,
    but its input is not, and fixture_kind is set to "constructed" so no
    consumer can miss that. derivation carries the recipe that reproduces it.
    """
    print(f"\n{'#' * 78}\n# CAPTURE {fixture_id}  [{', '.join(case_labels)}]\n{'#' * 78}")

    patient_data = parse_fhir_bundle(bundle_path)

    sink = RecordingSink()
    # truncate_first_call relabels the first Stage 5 response as truncated, so
    # the splitter can be captured EXECUTING rather than have its expected
    # output hand-derived. Only the truncation fixture sets it.
    saved = install_recording_hooks(sink, truncate_first_call)
    try:
        # build_initial_state() rather than a local copy of that dict: the
        # fixture must start from the same ground production starts from, and a
        # second copy would drift silently (see File 13).
        initial_state = build_initial_state(patient_data, ablation_flags)
        final_state = graph.invoke(initial_state)

        result = final_state["result"]
        # The two things match_patient_to_trials() stamps on after invoke().
        # Repeated here rather than calling it because the fixture needs the
        # whole final state, which that function does not return.
        result["qdrant_collection"] = resolve_qdrant_collection()
        result["patient_data_hash"] = compute_patient_hash(patient_data)
    finally:
        restore_hooks(saved)

    # Split the cross-encoder recording into a digest-keyed text store and the
    # per-pass records that reference it.
    cross_encoder_inputs = {}
    cross_encoder_passes = []
    for record in sink.cross_encoder:
        cross_encoder_inputs.setdefault(
            record["trial_texts_sha256"], record["trial_texts"]
        )
        cross_encoder_passes.append({
            key: record[key] for key in
            ("call_index", "query", "n_pairs", "trial_texts_sha256",
             "scores", "dtype")
        })

    fixture = {
        "schema_version": SCHEMA_VERSION,
        "fixture_id": fixture_id,
        "fixture_kind": (FIXTURE_KIND_CONSTRUCTED if construction
                         else FIXTURE_KIND_RECORDED),
        "case_labels": list(case_labels),
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "construction": construction,
        "identity": {
            "patient_id": patient_data["patient_id"],
            "patient_data_hash": result["patient_data_hash"],
            # For a cohort fixture this is the bundle in data_fhir_path. For a
            # derived one it is the DONOR's filename, never the temporary file
            # the derivation was written to: v2 stores no bundle of its own,
            # only the recipe to rebuild one (see `derivation`).
            "source_bundle": (derivation["donor_bundle"] if derivation
                              else os.path.basename(bundle_path)),
            "source_bundle_location": bundle_location,
            "case_evidence": dict(case_evidence or {}),
        },
        "derivation": derivation,
        "environment": environment or build_environment_block(),
        "inputs": {
            "ablation_flags": dict(ablation_flags or {}),
            "ablation_config_name": ablation_config_name,
        },
        "deterministic_prefix": build_deterministic_prefix(final_state, result, sink),
        "recordings": {
            "openai_embeddings": sink.openai_embeddings,
            "sparse_embeddings": sink.sparse_embeddings,
            # Stored once, keyed by digest, and referenced from each pass. The
            # cross-encoder scores the SAME ~100 trial texts once per rerank
            # query, so v1 wrote four identical copies of ~170 KB per fixture.
            "cross_encoder_inputs": cross_encoder_inputs,
            "cross_encoder": cross_encoder_passes,
            "chat_completions": sink.chat_completions,
        },
    }

    # The collection the run actually resolved must be the one pinned, or the
    # fixture describes a different index from the one it was produced against.
    if result["qdrant_collection"] != fixture["environment"]["qdrant_collection"]:
        raise IncompleteRecording(
            f"{fixture_id}: run resolved collection "
            f"{result['qdrant_collection']!r} but the environment block pins "
            f"{fixture['environment']['qdrant_collection']!r}. The alias moved "
            f"mid-capture."
        )

    verify_recording_complete(fixture, sink)
    verify_case_coverage(fixture)
    return fixture


def verify_case_coverage(fixture: Dict) -> None:
    """Check that the fixture actually exercises the branch it claims to.

    A fixture is selected on evidence from the cohort scan, but selection and
    execution are different things: the scan calls the same helpers Stage 1 and
    Stage 4 call, and if the two ever disagree, the fixture set would report
    coverage it does not have. That is the specific way a characterization
    baseline goes quietly useless, so the claim is checked against the run.
    """
    prefix = fixture["deterministic_prefix"]
    labels = fixture["case_labels"]
    problems = []

    if CASE_MESH_FALLBACK in labels:
        path = prefix["stage1"]["query_expansion_path"]
        if path != EXPANSION_PATH_FALLBACK:
            problems.append(
                f"labelled {CASE_MESH_FALLBACK} but Stage 1 took {path!r}"
            )

    if CASE_NO_CANDIDATES in labels:
        terminal = prefix["terminal"]["terminal_node"]
        if terminal != TERMINAL_NO_CANDIDATES:
            problems.append(
                f"labelled {CASE_NO_CANDIDATES} but the run ended at {terminal}"
            )

    if CASE_ABLATION in labels:
        if not fixture["inputs"]["ablation_flags"]:
            problems.append(
                f"labelled {CASE_ABLATION} but ablation_flags is empty"
            )

    if CASE_UNKNOWN_STAGE in labels:
        # extract_patient_stage() is a Stage 4 local and never reaches state,
        # so the run cannot confirm this directly. The scan evidence is what is
        # checked, and it is stored on the fixture for the same reason.
        if fixture["identity"]["case_evidence"].get("stage") is not None:
            problems.append(
                f"labelled {CASE_UNKNOWN_STAGE} but the scan resolved stage "
                f"{fixture['identity']['case_evidence'].get('stage')!r}"
            )

    if CASE_NORMAL in labels:
        if prefix["stage1"]["query_expansion_path"] != EXPANSION_PATH_MESH:
            problems.append("labelled normal but Stage 1 fell back")
        if prefix["terminal"]["terminal_node"] != TERMINAL_FINALIZE:
            problems.append(
                f"labelled normal but the run ended at "
                f"{prefix['terminal']['terminal_node']}"
            )

    if problems:
        raise IncompleteRecording(
            f"{fixture['fixture_id']}: case coverage is not what it claims —\n  - "
            + "\n  - ".join(problems)
        )


#------------------------------------------------------------------------------


# ===========================================================================
# THE DERIVED MeSH-FALLBACK BUNDLE
# ===========================================================================
#
# No patient in the 1,000-bundle Synthea cohort takes EXPANSION_PATH_FALLBACK.
# All 1,000 resolve to MeSH with terms (799 snomed, 160 fuzzy_stem, 27
# fuzzy_stem+snomed, 14 fuzzy_substring+snomed), which is what a corpus built
# from Synthea's oncology modules produces: every diagnosis carries a specific,
# well-known SNOMED code. The branch is real, reachable and entirely
# unexercised by the corpus.
#
# So this fixture runs the real pipeline on a DERIVED bundle: a real patient
# whose cancer Condition codings are replaced with SNOMED 363346000,
# "Malignant neoplastic disease" — a genuine SNOMED concept with its genuine
# display, and the code a real EHR uses when the site was never coded. The
# cancer registry still recognises the patient as having a primary cancer; the
# MeSH walk resolves it to a pan-cancer node, which resolve_patient_trees()
# rejects as an identity, so Stage 1 has no descriptors and falls back.
#
# The run is real. The input is not, and the fixture says so in fixture_kind
# and in `construction`.

MESH_FALLBACK_SNOMED_CODE = "363346000"
MESH_FALLBACK_DISPLAY = "Malignant neoplastic disease (disorder)"

_SNOMED_SYSTEM_URLS = ("http://snomed.info/sct", "urn:oid:2.16.840.1.113883.6.96")


def build_mesh_fallback_bundle(source_bundle_path: str, out_path: str) -> Dict:
    """Rewrite a real bundle's primary-cancer codings to a site-less SNOMED code.

    Only the codings the cancer registry recognises as a primary cancer are
    touched. Comorbidities, medications, observations, procedures and
    demographics are left exactly as Synthea generated them, so the resulting
    run differs from an ordinary one in the one dimension being exercised.
    """
    with open(source_bundle_path) as fh:
        bundle = json.load(fh)

    rewritten = []
    for entry in bundle.get("entry", []):
        resource = entry.get("resource") or {}
        if resource.get("resourceType") != "Condition":
            continue

        codings = (resource.get("code") or {}).get("coding") or []
        for coding in codings:
            if coding.get("system") not in _SNOMED_SYSTEM_URLS:
                continue
            if str(coding.get("code")) not in _CANCER_REGISTRY.snomed_primary:
                continue
            rewritten.append({
                "was_code": coding.get("code"),
                "was_display": coding.get("display"),
            })
            coding["code"] = MESH_FALLBACK_SNOMED_CODE
            coding["display"] = MESH_FALLBACK_DISPLAY

        # code.text mirrors the display in Synthea output and reaches the fuzzy
        # descriptor layer, so leaving it would resolve the patient by text
        # after the code was made generic.
        if rewritten and (resource.get("code") or {}).get("text"):
            resource["code"]["text"] = MESH_FALLBACK_DISPLAY

    if not rewritten:
        raise RuntimeError(
            f"{os.path.basename(source_bundle_path)} carries no primary-cancer "
            f"SNOMED coding to rewrite; cannot derive a MeSH-fallback bundle "
            f"from it."
        )

    with open(out_path, "w") as fh:
        json.dump(bundle, fh)

    return {"codings_rewritten": rewritten}


#------------------------------------------------------------------------------


# ===========================================================================
# THE DERIVED NO-CANDIDATES BUNDLE
# ===========================================================================
#
# node_no_candidates is not reachable from this cohort either. Both routes into
# it were probed against the live index (see probe_empty_candidate_pool):
#
#   route_after_retrieval  needs an empty fusion pool. Every one of 250 probed
#                          patients retrieved a full RRF_POOL_SIZE of 100. The
#                          expanded query always contains "solid tumor, solid
#                          neoplasm", so BM25 always matches something.
#   route_after_filter     needs Stage 4 to drop everything. The worst observed
#                          case kept 39 of 100.
#
# So this fixture, like the MeSH-fallback one, runs the real pipeline on a
# derived bundle. The lever is age: the patient's birthDate is moved so they
# are a small child at the snapshot date, and the age window on adult oncology
# trials ("18 Years" and up) empties the pool in Stage 4.
#
# It is a scenario worth having a fixture for on its own merits — a paediatric
# record reaching an adult-oncology matcher is an ordinary integration mistake,
# and what the pipeline does with it (a clean empty result, not a crash and not
# a list of adult trials) is exactly the behaviour a refactor must preserve.
#
# The age is not assumed to work, and on the first donor tried it did not:
# every Stage 4 rule is conservative, so a trial that states no age, no sex, no
# stage and no histology cannot be dropped by any of them. NCT06940947 carries
# min_age = "" and max_age = "", which File 13 reads as 0 and 999, and it
# survived a one-year-old.
#
# Which trials are in the pool depends on the patient's cancer site, so the
# derivation searches: for each donor in turn it rewrites the age, probes the
# result against the live index, and accepts the first donor whose whole pool
# is filterable. The search is bounded and every candidate is measured — if no
# donor works, the case is reported as not covered rather than guessed at.

NO_CANDIDATES_AGE_YEARS = 1
NO_CANDIDATES_MAX_DONORS = 60


def build_no_candidates_bundle(source_bundle_path: str,
                               out_path: str,
                               age_years: int) -> Dict:
    """Rewrite a real bundle's Patient.birthDate to make the patient a child.

    Nothing else is touched: the cancer, its stage, the medications and the
    comorbidities stay as generated. Only the date the age is derived from
    moves, which is the single input the Stage 4 age window reads.
    """
    with open(source_bundle_path) as fh:
        bundle = json.load(fh)

    reference = get_age_reference_date()
    birth_date = (reference - relativedelta(years=age_years)).isoformat()

    patients_rewritten = 0
    for entry in bundle.get("entry", []):
        resource = entry.get("resource") or {}
        if resource.get("resourceType") != "Patient":
            continue
        resource["birthDate"] = birth_date
        patients_rewritten += 1

    if patients_rewritten != 1:
        raise RuntimeError(
            f"{os.path.basename(source_bundle_path)} carries "
            f"{patients_rewritten} Patient resource(s); expected exactly one."
        )

    with open(out_path, "w") as fh:
        json.dump(bundle, fh)

    return {"birth_date": birth_date, "age_years": age_years,
            "age_reference_date": reference.isoformat()}


#------------------------------------------------------------------------------


# ===========================================================================
# THE DERIVED mCODE GENOMIC VARIANT BUNDLE
# ===========================================================================
#
# Item 19b made variant detection structural: an observation is a genomic
# variant if its LOINC is 69548-6 or it carries a gene_symbol, with keyword
# matching demoted to a fallback for free text. Two of those three paths have
# never run on real data, because NOT ONE of the 1,000 bundles in the corpus
# contains a 69548-6 observation — Synthea's oncology modules do not emit them.
#
# So the paths exist, are wired into the query builder and the Stage 5 prompt,
# and are completely untested by the fixture set. A refactor could delete
# either one and every fixture would still replay clean. This recipe injects
# one real mCODE variant Observation into a donor bundle so both paths carry a
# fixture that would go red.
#
# The resource is built to the shape File 07's _parse_mcode_genomic_variant
# reads: LOINC 69548-6 on the Observation, gene symbol in the component coded
# by LOINC 48018-6, and the protein change in 48005-3. EGFR p.Leu858Arg is a
# real, common, well-documented lung adenocarcinoma driver.

MCODE_VARIANT_LOINC = "69548-6"
MCODE_GENE_STUDIED_LOINC = "48018-6"      # Gene studied [ID]
MCODE_PROTEIN_CHANGE_LOINC = "81252-9"    # HGVS protein change, as File 07 reads it
MCODE_VARIANT_GENE = "EGFR"
MCODE_VARIANT_PROTEIN = "p.Leu858Arg"


def build_mcode_variant_bundle(source_bundle_path: str, out_path: str) -> Dict:
    """Inject one mCODE genomic variant Observation into a real bundle.

    Everything else is left exactly as Synthea generated it, so the resulting
    run differs from an ordinary one only in carrying a variant.
    """
    with open(source_bundle_path) as fh:
        bundle = json.load(fh)

    # Anchor the new resource to the patient and to a date the pipeline will
    # accept, both taken from the bundle rather than invented: an observation
    # dated after the snapshot would be filtered out by the same reference-date
    # logic that governs ECOG, and the fixture would exercise nothing.
    patient_ref = None
    effective = None
    for entry in bundle.get("entry", []):
        resource = entry.get("resource") or {}
        if resource.get("resourceType") == "Patient" and patient_ref is None:
            patient_ref = f"urn:uuid:{resource.get('id')}"
        if resource.get("resourceType") == "Observation" and effective is None:
            effective = resource.get("effectiveDateTime")
    if patient_ref is None:
        raise RuntimeError(
            f"{os.path.basename(source_bundle_path)} carries no Patient "
            f"resource to attach a genomic variant to."
        )
    if effective is None:
        effective = f"{DATA_SNAPSHOT_DATE}T00:00:00+00:00"

    observation = {
        "resourceType": "Observation",
        "id": "fixture-mcode-genomic-variant",
        "status": "final",
        "category": [{"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/observation-category",
            "code": "laboratory", "display": "Laboratory",
        }]}],
        "code": {"coding": [{
            "system": "http://loinc.org",
            "code": MCODE_VARIANT_LOINC,
            "display": "Genetic variant assessment",
        }], "text": "Genetic variant assessment"},
        "subject": {"reference": patient_ref},
        "effectiveDateTime": effective,
        "valueCodeableConcept": {"coding": [{
            "system": "http://loinc.org", "code": "LA9633-4", "display": "Present",
        }], "text": "Present"},
        "component": [
            {
                "code": {"coding": [{
                    "system": "http://loinc.org",
                    "code": MCODE_GENE_STUDIED_LOINC,
                    "display": "Gene studied [ID]",
                }]},
                "valueCodeableConcept": {"coding": [{
                    "system": "http://www.genenames.org",
                    "code": "HGNC:3236", "display": MCODE_VARIANT_GENE,
                }], "text": MCODE_VARIANT_GENE},
            },
            {
                "code": {"coding": [{
                    "system": "http://loinc.org",
                    "code": MCODE_PROTEIN_CHANGE_LOINC,
                    "display": "Discrete genetic variant [Protein]",
                }]},
                "valueCodeableConcept": {"coding": [{
                    "system": "http://varnomen.hgvs.org",
                    "code": MCODE_VARIANT_PROTEIN,
                    "display": MCODE_VARIANT_PROTEIN,
                }], "text": MCODE_VARIANT_PROTEIN},
            },
        ],
    }

    bundle.setdefault("entry", []).append({
        "fullUrl": "urn:uuid:fixture-mcode-genomic-variant",
        "resource": observation,
    })

    with open(out_path, "w") as fh:
        json.dump(bundle, fh)

    return {"gene": MCODE_VARIANT_GENE, "protein_change": MCODE_VARIANT_PROTEIN,
            "loinc": MCODE_VARIANT_LOINC, "effective": effective}


#------------------------------------------------------------------------------


# ===========================================================================
# DERIVATION RECIPES
# ===========================================================================
#
# Schema v1 stored the derived bundle itself. Synthea records are enormous —
# the two donors' source bundles are 172 MB each — so two fixtures cost 214 MB
# of the 260 MB directory, and a directory that size cannot live in the
# repository, which is where item 64's deploy gate needs to read it from.
#
# v2 stores the RECIPE instead: which donor, which transformation, which
# parameters. 46- rebuilds the bundle from the live corpus at replay time into
# a temporary file, parses it, and deletes it. The bundle is a derived artifact
# and derived artifacts should not be checked in.
#
# It is also a stronger record than the bundle was. A stored bundle says what
# the input WAS; a recipe says what was DONE to a named patient, which is the
# thing a reader needs in order to judge whether the fixture is fair. The
# rebuild is verified against the recorded patient_data_hash on every replay,
# so a recipe that no longer reproduces its input fails loudly instead of
# quietly diffing a different patient.

RECIPE_MESH_FALLBACK = "mesh_fallback_siteless_code"
RECIPE_NO_CANDIDATES = "no_candidates_pediatric_age"
RECIPE_MCODE_VARIANT = "mcode_genomic_variant"


def apply_derivation(recipe: str, donor_path: str, out_path: str,
                     params: Dict) -> Dict:
    """Run a named derivation. The single dispatch point, shared by 45- and 46-.

    Both files must produce byte-identical output from the same recipe, so
    there is exactly one implementation and both call it.
    """
    if recipe == RECIPE_MESH_FALLBACK:
        return build_mesh_fallback_bundle(donor_path, out_path)
    if recipe == RECIPE_NO_CANDIDATES:
        return build_no_candidates_bundle(
            donor_path, out_path, params["age_years"]
        )
    if recipe == RECIPE_MCODE_VARIANT:
        return build_mcode_variant_bundle(donor_path, out_path)
    raise ValueError(
        f"unknown derivation recipe {recipe!r}; known recipes are "
        f"{[RECIPE_MESH_FALLBACK, RECIPE_NO_CANDIDATES, RECIPE_MCODE_VARIANT]}"
    )


def rebuild_derived_bundle(fixture: Dict) -> str:
    """Rebuild a derived fixture's input bundle into a temporary file.

    Returns the path. The caller owns it and must delete it — it is a copy of
    a Synthea record and those run to hundreds of megabytes.
    """
    derivation = fixture["derivation"]
    donor_path = os.path.join(data_fhir_path, derivation["donor_bundle"])
    if not os.path.exists(donor_path):
        raise FileNotFoundError(
            f"{fixture['fixture_id']}: donor bundle "
            f"{derivation['donor_bundle']} is not in {data_fhir_path}. The "
            f"recipe cannot be replayed against a corpus that no longer "
            f"contains the patient it was derived from."
        )

    handle, out_path = tempfile.mkstemp(
        prefix=f"{fixture['fixture_id']}_", suffix=".bundle.json"
    )
    os.close(handle)
    apply_derivation(
        derivation["recipe"], donor_path, out_path, derivation.get("params") or {}
    )
    return out_path


#------------------------------------------------------------------------------


# ===========================================================================
# THE CONSTRUCTED RETRY FIXTURE
# ===========================================================================

# Marker spliced into the truncated response so a reader of the fixture can see
# at a glance that the malformed payload was manufactured.
MALFORMED_MARKER = "  <<< TRUNCATED BY 45- Fixture Capture.py TO FORCE A JSON PARSE FAILURE"

# How much of the real response to keep before truncating. Long enough that the
# payload is recognisably the real one, short enough that it cannot close.
MALFORMED_PREFIX_CHARS = 400


def build_constructed_retry_fixture(base: Dict, fixture_id: str) -> Dict:
    """Assemble the MAX_GPT4O_RETRIES fixture from a recorded normal run.

    The retry loop cannot be found by scanning the cohort: it fires on a
    malformed model response, which is a property of the model on the day, not
    of any patient. So this fixture is built rather than observed — a real
    recorded run with one extra Stage 5 response spliced in FRONT of the real
    one, truncated so json.loads raises.

    On replay: attempt 1 parses the truncated payload and fails, Stage 5
    returns gpt4o_retries=1 with an error, route_after_gpt4o sends it back
    round the cyclic edge (1 < MAX_GPT4O_RETRIES), attempt 2 receives the real
    response and succeeds. Everything upstream of Stage 5 is untouched, so the
    only expected difference from the base fixture is the retry count and a
    second identical request.

    fixture_kind is set to "constructed" INSIDE the fixture, not just in a
    filename or an index, because that is the only place a consumer reading one
    file is guaranteed to look.
    """
    if base["fixture_kind"] != FIXTURE_KIND_RECORDED:
        raise ValueError(
            f"{base['fixture_id']} is {base['fixture_kind']}; the retry fixture "
            f"must be derived from a real recorded run"
        )

    recorded_calls = base["recordings"]["chat_completions"]
    if len(recorded_calls) != 1:
        raise ValueError(
            f"{base['fixture_id']} recorded {len(recorded_calls)} Stage 5 "
            f"call(s); the retry fixture needs exactly one to splice in front of"
        )

    fixture = copy.deepcopy(base)
    real_call = copy.deepcopy(recorded_calls[0])

    content = real_call["response"]["content"] or ""
    malformed_content = content[:MALFORMED_PREFIX_CHARS] + MALFORMED_MARKER

    # Prove the splice actually breaks the parser rather than assuming it does.
    # File 13 strips a leading markdown fence before parsing, so the check runs
    # the same two steps in the same order.
    check_text = malformed_content.strip()
    if check_text.startswith("```"):
        check_text = check_text.split("```")[1]
        if check_text.startswith("json"):
            check_text = check_text[4:]
        check_text = check_text.strip()
    try:
        json.loads(check_text)
    except json.JSONDecodeError:
        pass
    else:
        raise ValueError(
            "the truncated Stage 5 payload still parses as JSON, so it would "
            "not exercise the retry loop"
        )

    malformed_call = copy.deepcopy(real_call)
    malformed_call["response"]["content"] = malformed_content
    # finish_reason "stop", NOT "length". The case under test is a MALFORMED
    # response — one the model finished writing and that still does not parse.
    # Labelling it "length" made it a truncated response, which since item 19c
    # is intercepted by the split path before the JSON parser ever sees it: the
    # fixture then asked for three chunk responses it did not carry and failed
    # with replay misses. Truncation has its own fixture; this one is about the
    # parse-failure budget.
    malformed_call["response"]["finish_reason"] = "stop"
    malformed_call["call_index"] = 0
    # Usage is read BEFORE the JSON parse in node_gpt4o_evaluation, so the
    # failing attempt still needs a usage block. The real one is reused: the
    # attempt returns before those numbers reach the result.
    real_call["call_index"] = 1

    fixture["fixture_id"] = fixture_id
    fixture["fixture_kind"] = FIXTURE_KIND_CONSTRUCTED
    fixture["case_labels"] = [CASE_GPT4O_RETRY]
    fixture["captured_at_utc"] = datetime.now(timezone.utc).isoformat()
    fixture["construction"] = {
        "derived_from": base["fixture_id"],
        "what_was_changed": (
            "recordings.chat_completions gained a first entry whose response "
            f"content is the real content truncated to {MALFORMED_PREFIX_CHARS} "
            "characters. The real recorded response is unchanged and now sits "
            "at call_index 1. deterministic_prefix.stage5 was updated to expect "
            "one retry and two identical requests. Nothing else differs from "
            f"{base['fixture_id']}."
        ),
        "why": (
            "The GPT-4o retry loop fires on a malformed model response and "
            "cannot be found by scanning a patient cohort. This fixture is "
            "assembled, not observed."
        ),
    }
    fixture["recordings"]["chat_completions"] = [malformed_call, real_call]

    # The expected prefix: identical to the base run except that Stage 5 ran
    # twice and one retry was spent. Both attempts build the same prompt from
    # the same filtered set, so the request digest simply repeats.
    stage5 = fixture["deterministic_prefix"]["stage5"]
    request_sha = base["deterministic_prefix"]["stage5"]["request_sha256_by_call"][0]
    stage5["request_sha256_by_call"] = [request_sha, request_sha]
    stage5["gpt4o_calls"] = 2
    stage5["gpt4o_retries"] = 1
    # Both responses claim to have finished normally. That is what a parse
    # failure IS: the model stopped of its own accord and produced something
    # that does not parse. "length" would make it a truncation, which since
    # item 19c is a different mechanism with a different budget.
    stage5["finish_reasons"] = ["stop", "stop"]
    # gpt4o_calls_reported is left at the base value of 1 and that is correct,
    # not an oversight: it is what the SUCCESSFUL node invocation reports, and
    # Stage 5's own counter resets when the router re-enters the node. The
    # sink-side gpt4o_calls of 2 spans both invocations. The two disagreeing is
    # the signature of a retry, and is why both are recorded.

    return fixture


#------------------------------------------------------------------------------


# ===========================================================================
# COHORT SCAN
# ===========================================================================

def scan_cohort(bundle_paths: List[str]) -> List[Dict]:
    """Classify every patient by the branch cases that need no network call.

    Two of the five cases are decidable from the parsed record alone:
    EXPANSION_PATH_FALLBACK is a property of MeSH resolution, and an unknown
    stage is a property of extract_patient_stage(). Both are computed here with
    the SAME helpers Stage 1 and Stage 4 call, so a patient selected here is a
    patient the pipeline will agree with.
    """
    print(f"\n[Scan] Classifying {len(bundle_paths)} patient bundles "
          f"(no network calls)...")

    rows = []
    failures = 0

    for index, path in enumerate(bundle_paths, start=1):
        if index % 200 == 0:
            print(f"  ...{index}/{len(bundle_paths)}")
        try:
            patient_data = parse_fhir_bundle(path)
        except Exception as exc:
            # Counted, not swallowed: a cohort with unparseable bundles is a
            # fact about the corpus that the selection report has to state.
            failures += 1
            print(f"  WARNING: parse failed for {os.path.basename(path)}: "
                  f"{type(exc).__name__}: {exc}")
            continue

        conditions = patient_data.get("conditions") or []
        mesh_result = expand_query_from_mesh(conditions, _CANCER_REGISTRY, _MESH_FILTER)
        stage = extract_patient_stage(
            conditions,
            cancer_stage_observations=patient_data.get("cancer_stage_observations") or [],
        )

        primary = "cancer"
        cancer_conditions = [
            c for c in conditions
            if (c.get("verification_status") or "unknown")
            not in _CANCER_REGISTRY.exclude_verification
            and _CANCER_REGISTRY.is_primary_cancer(c)
        ]
        if cancer_conditions:
            primary = sorted(cancer_conditions, key=_CANCER_REGISTRY.sort_key)[0]["display"]

        rows.append({
            "path": path,
            "bundle": os.path.basename(path),
            "patient_id": patient_data.get("patient_id"),
            "primary_diagnosis": primary,
            "mesh_resolution": mesh_result["resolution"],
            "mesh_terms": len(mesh_result["mesh_terms"]),
            "patient_trees": list(mesh_result["patient_trees"]),
            "expansion_path": (EXPANSION_PATH_MESH if mesh_result["mesh_terms"]
                               else EXPANSION_PATH_FALLBACK),
            "stage": stage,
            "ecog": (patient_data.get("ecog_performance_status") or {}).get("value"),
        })

    print(f"[Scan] Parsed {len(rows)} bundle(s); {failures} failed.")
    return rows


def probe_empty_candidate_pool(bundle_path: str) -> Dict:
    """Cheap sufficient test for a run that ends at node_no_candidates.

    Runs Stage 1 and Stage 2 for real, then applies Stage 4's filter to the
    WHOLE fusion pool instead of to the top-40 the cross-encoder would keep.

    That is sound as a sufficient condition and not as a necessary one. The
    real run's reranked set is a subset of this pool, every filter in Stage 4
    is a per-trial predicate, and the quality gate can only remove more — so if
    nothing in the whole pool survives, nothing in any subset of it survives
    either. The converse does not hold, which is why a negative probe means
    "not proven", not "will not fire".

    Skipping the cross-encoder is the point: MedCPT over 100 trials x 3 queries
    on CPU is the expensive part, and this has to run over a few hundred
    patients to find a case that may not exist.
    """
    patient_data = parse_fhir_bundle(bundle_path)

    state = build_initial_state(patient_data)
    state.update(node_query_expansion(state))
    state.update(node_hybrid_retrieval(state))

    pool = state.get("hybrid_results") or []
    if not pool:
        return {"empty_pool": True, "route": "after_retrieval",
                "pool_size": 0, "survivors": 0}

    # Shape the pool like Stage 3's output so node_rule_based_filter can run on
    # it unmodified. fusion_score stands in for the rerank score; the hard
    # filters do not read it, and the quality gate's verdict is not used.
    state["reranked_trials"] = [
        {
            "trial": t["trial"],
            "rerank_score": t.get("fusion_score", 0.0),
            "rerank_score_raw": t.get("fusion_score", 0.0),
            "mesh_boost": 0.0,
            "mesh_boost_tier": "none",
        }
        for t in pool
    ]
    state["patient_trees"] = resolve_patient_mesh(
        patient_data.get("conditions", []), _CANCER_REGISTRY, _MESH_FILTER
    )["trees"] if _MESH_FILTER is not None else set()

    filter_out = node_rule_based_filter(state)
    survivors = filter_out["candidates_after_rule_filter"]

    return {
        "empty_pool": survivors == 0,
        "route": "after_filter",
        "pool_size": len(pool),
        "survivors": survivors,
    }


def select_cases(rows: List[Dict], probe_limit: int) -> Dict:
    """Choose which patient covers which branch case.

    Selection is deterministic: candidates are ordered by bundle filename and
    the first qualifying one wins, so re-running this file picks the same
    patients and a fixture set can be regenerated rather than only appended to.
    """
    by_bundle = sorted(rows, key=lambda r: r["bundle"])

    selection = {}
    taken = set()

    def _take(case: str, candidates: List[Dict]) -> Dict:
        for row in candidates:
            if row["bundle"] in taken:
                continue
            taken.add(row["bundle"])
            selection[case] = row
            return row
        selection[case] = None
        return None

    # --- Stage 1 fell back to the un-expanded base query -------------------
    _take(CASE_MESH_FALLBACK,
          [r for r in by_bundle if r["expansion_path"] == EXPANSION_PATH_FALLBACK])

    # --- Cancer stage could not be determined ------------------------------
    # Prefer a patient whose MeSH DID resolve, so this fixture isolates the
    # unknown-stage branch instead of doubling as a second fallback fixture.
    _take(CASE_UNKNOWN_STAGE,
          [r for r in by_bundle
           if r["stage"] is None and r["expansion_path"] == EXPANSION_PATH_MESH]
          or [r for r in by_bundle if r["stage"] is None])

    # --- No candidates survived --------------------------------------------
    # Probed rather than predicted, and probed rarest-diagnosis-first: a cancer
    # site with few patients is the one most likely to have no matching trial
    # in the corpus. The probe costs one embedding and four Qdrant queries per
    # patient, so it is capped and stops at the first hit.
    diagnosis_counts = {}
    for row in by_bundle:
        diagnosis_counts[row["primary_diagnosis"]] = \
            diagnosis_counts.get(row["primary_diagnosis"], 0) + 1

    probe_order = sorted(
        (r for r in by_bundle if r["bundle"] not in taken),
        key=lambda r: (diagnosis_counts[r["primary_diagnosis"]], r["bundle"]),
    )[:probe_limit]

    print(f"\n[Probe] Hunting for an empty candidate pool across "
          f"{len(probe_order)} patient(s), rarest cancer site first...")

    found = None
    for index, row in enumerate(probe_order, start=1):
        try:
            outcome = probe_empty_candidate_pool(row["path"])
        except Exception as exc:
            print(f"  WARNING: probe failed for {row['bundle']}: "
                  f"{type(exc).__name__}: {exc}")
            continue
        if index % 25 == 0 or outcome["empty_pool"]:
            print(f"  [{index}/{len(probe_order)}] {row['bundle'][:40]:<40} "
                  f"pool={outcome['pool_size']:>3} "
                  f"survivors={outcome['survivors']:>3} "
                  f"({row['primary_diagnosis'][:40]})")
        if outcome["empty_pool"]:
            row = dict(row, probe=outcome)
            found = row
            break

    if found:
        taken.add(found["bundle"])
        selection[CASE_NO_CANDIDATES] = found
    else:
        selection[CASE_NO_CANDIDATES] = None
        print(f"[Probe] No patient in the probed {len(probe_order)} produced an "
              f"empty candidate pool.")

    # --- Ablation run and the normal patients ------------------------------
    # Ordinary patients: MeSH resolved, stage known. The ablation fixture uses
    # one of them so its diff against a normal fixture isolates the flag.
    ordinary = [r for r in by_bundle
                if r["bundle"] not in taken
                and r["expansion_path"] == EXPANSION_PATH_MESH
                and r["stage"] is not None]

    # One distinct ordinary patient per ablation config. Distinct rather than
    # shared, so an ablation fixture and a normal fixture never differ only by
    # the flag: the point of these three is the channel accounting, and reusing
    # one patient would make three fixtures whose retrieval sections are the
    # same run three times.
    for spec in ABLATION_FIXTURES:
        _take(f"ablation::{spec['config_name']}", ordinary)
        ordinary = [r for r in ordinary if r["bundle"] not in taken]

    selection["normals"] = []
    for _ in range(NORMAL_FIXTURE_COUNT):
        row = _take(f"normal_{len(selection['normals'])}", ordinary)
        if row is None:
            break
        selection["normals"].append(row)
        ordinary = [r for r in ordinary if r["bundle"] not in taken]

    # Donors for the derived bundles. A case that no real patient hits is
    # covered by rewriting one input field of a real patient's bundle and
    # running the pipeline for real on the result (see the two build_*_bundle
    # functions). The donor list is what those draw from, in the same
    # deterministic filename order as everything else here.
    selection["donor_pool"] = [r for r in ordinary if r["bundle"] not in taken]

    return selection


# Three ordinary patients alongside the five branch cases puts the set at eight
# fixtures — inside the five-to-ten the item asks for, with room to add a
# branch case later without re-tuning this number.
NORMAL_FIXTURE_COUNT = 3

# Which File 26 configuration the ablation fixture runs under. no_cross_encoder
# is the structurally largest branch of the five: Stage 3 returns early through
# a completely different code path that passes fusion scores through in place
# of rerank scores, so it is the one most likely to be broken silently by a
# restructuring of that node.
# Three of File 26's seven configs, chosen because each one is the only thing
# that exercises a distinct piece of accounting:
#
#   no_cross_encoder  Stage 3 returns through a completely different branch
#                     that passes fusion scores through in place of rerank
#                     scores. The largest structural fork in the pipeline.
#   bm25_only         the dense channel is CHANNEL_ABLATED, so
#                     retrieval_channels_expected drops to 3 and
#                     retrieval_degraded must stay 0. Nothing else in the
#                     fixture set distinguishes "ablated" from "failed", which
#                     is the one thing that accounting exists to do.
#   vector_only       the mirror: three sparse channels ablated, expected 1.
#
# Each is a dict literal rather than a lookup into File 26, because 45- does
# not chain 26 and a fixture must record the flags it actually ran with.
ABLATION_FIXTURES = [
    {
        "fixture_id": "ablation_no_cross_encoder",
        "config_name": "no_cross_encoder",
        "flags": {
            "skip_mesh_filter": False,
            "skip_stage_filter": False,
            "skip_histology_filter": False,
            "skip_cross_encoder": True,
            "retrieval_mode": "hybrid",
        },
    },
    {
        "fixture_id": "ablation_bm25_only",
        "config_name": "bm25_only",
        "flags": {
            "skip_mesh_filter": False,
            "skip_stage_filter": False,
            "skip_histology_filter": False,
            "skip_cross_encoder": False,
            "retrieval_mode": "bm25_only",
        },
    },
    {
        "fixture_id": "ablation_vector_only",
        "config_name": "vector_only",
        "flags": {
            "skip_mesh_filter": False,
            "skip_stage_filter": False,
            "skip_histology_filter": False,
            "skip_cross_encoder": False,
            "retrieval_mode": "vector_only",
        },
    },
]


#------------------------------------------------------------------------------


# ===========================================================================
# MAIN
# ===========================================================================

def _assert_database_is_isolated() -> None:
    """Refuse to run if the production inference database is reachable.

    THREE independent checks, because no one of them is sufficient: the path
    could be restored by a later chain, the redirect could hold while a caller
    still reaches the real logger through a saved reference, and — the check
    added in pass 20c-2b — both of the first two could pass vacuously if the
    scratch path and the production path were the same string, or if
    resolve_inference_db_path() had stopped distinguishing them.

    The third check RESOLVES a path and connects to nothing, so asserting about
    the production database here does not open it.
    """
    # NON-DEGENERACY, asserted before the two isolation checks that depend on
    # it. What a caller who passed no db_path would get must be the production
    # database, and must not be the scratch one. If those two were ever the same
    # value, every check below would pass while providing no isolation at all.
    _package_default = resolve_inference_db_path(None)
    if os.path.abspath(_package_default) != os.path.abspath(PRODUCTION_INFERENCES_PATH):
        raise RuntimeError(
            f"resolve_inference_db_path(None) is {_package_default!r}, not the "
            f"production database {PRODUCTION_INFERENCES_PATH!r}. The isolation "
            f"checks below compare against the wrong thing; refusing to run."
        )
    if os.path.abspath(_package_default) == os.path.abspath(FIXTURE_SCRATCH_DB):
        raise RuntimeError(
            f"the production database and the scratch database resolve to the "
            f"same path ({_package_default!r}). The redirect below would pass "
            f"while isolating nothing; refusing to run."
        )
    if resolve_inference_db_path(FIXTURE_SCRATCH_DB) != FIXTURE_SCRATCH_DB:
        raise RuntimeError(
            "resolve_inference_db_path() does not honour an explicit db_path; "
            "refusing to run."
        )

    if inferences_path != FIXTURE_SCRATCH_DB:
        raise RuntimeError(
            f"inferences_path is {inferences_path!r}, not the scratch database "
            f"{FIXTURE_SCRATCH_DB!r}. Something re-chained 14- Database "
            f"Logger.py after the redirect; refusing to run."
        )

    try:
        log_inference(None, None)
    except RuntimeError:
        pass
    else:
        raise RuntimeError(
            "log_inference did not raise. The neutralized binding was "
            "overwritten; refusing to run."
        )

    if os.path.exists(PRODUCTION_INFERENCES_PATH):
        print(f"  Production inferences.db left untouched "
              f"({os.path.getsize(PRODUCTION_INFERENCES_PATH) / 1024:.0f} KB).")
    print(f"  Scratch database: {FIXTURE_SCRATCH_DB}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture characterization fixtures for the matching pipeline."
    )
    parser.add_argument("--scan-only", action="store_true",
                        help="Classify the cohort and report the selection; capture nothing.")
    parser.add_argument("--probe-limit", type=int, default=250,
                        help="How many patients to probe for an empty candidate pool.")
    parser.add_argument("--only", nargs="*", default=None,
                        help="Capture only these fixture ids.")
    parser.add_argument("--fixture-dir", default=None,
                        help="Override the fixture output directory.")
    args = parser.parse_args()

    root = args.fixture_dir or FIXTURE_ROOT
    os.makedirs(root, exist_ok=True)

    _assert_database_is_isolated()

    print(f"\n{'=' * 78}")
    print(f"{Project_Name}: Characterization Fixture Capture (schema v{SCHEMA_VERSION})")
    print(f"{'=' * 78}")
    print(f"  Fixture directory: {root}")

    bundle_paths = sorted(glob.glob(data_fhir_path + "*.json"))
    if not bundle_paths:
        print(f"[FATAL] No FHIR bundles found in {data_fhir_path}")
        return 1
    print(f"  Cohort: {len(bundle_paths)} bundles\n")

    rows = scan_cohort(bundle_paths)
    if not rows:
        print("[FATAL] No bundle parsed successfully.")
        return 1

    # --- Cohort summary ----------------------------------------------------
    n_fallback = sum(1 for r in rows if r["expansion_path"] == EXPANSION_PATH_FALLBACK)
    n_unknown_stage = sum(1 for r in rows if r["stage"] is None)
    print(f"\n[Scan] {n_fallback} patient(s) take EXPANSION_PATH_FALLBACK; "
          f"{n_unknown_stage} have no determinable cancer stage.")

    selection = select_cases(rows, args.probe_limit)

    # --- Report the selection ----------------------------------------------
    print(f"\n{'-' * 78}\nCASE COVERAGE\n{'-' * 78}")
    plan = []

    def _add(fixture_id, row, labels, flags=None, config_name=None,
             bundle_path=None, bundle_location=BUNDLE_IN_COHORT,
             construction=None, derivation=None, truncate_first_call=False):
        plan.append({
            "fixture_id": fixture_id,
            "row": row,
            "labels": labels,
            "flags": flags,
            "config_name": config_name,
            "bundle_path": bundle_path or row["path"],
            "bundle_location": bundle_location,
            "construction": construction,
            "derivation": derivation,
            "truncate_first_call": truncate_first_call,
        })

    donors = list(selection.get("donor_pool") or [])

    def _next_donor() -> Dict:
        if not donors:
            raise RuntimeError("no ordinary patient left to derive a bundle from")
        return donors.pop(0)

    def _wanted(fixture_id: str) -> bool:
        """Whether this run should touch this fixture at all.

        Derivation WRITES a bundle that an already-captured fixture depends on,
        so it must be gated by --only exactly like the capture is. It was not,
        and a `--only no_candidates_pediatric_age` run silently re-derived the
        MeSH-fallback bundle from a different donor, leaving that fixture
        pointing at a different patient's record.
        """
        return not args.only or fixture_id in args.only

    def _recorded_donor(derived_id: str) -> Dict:
        """The donor an already-captured derived fixture was built from.

        Re-deriving from the recorded donor rather than searching again makes
        the bundle regenerable from the fixture: the fixture is the record, and
        running this file twice cannot silently repoint it at someone else.
        Returns None when there is no such fixture, or its donor is no longer
        in the cohort.
        """
        path = fixture_path(derived_id, root)
        if not os.path.exists(path):
            return None
        try:
            recorded = load_fixture(path)
        except (ValueError, json.JSONDecodeError) as exc:
            # Not silent: an unreadable fixture here means the donor is about
            # to be re-chosen, which repoints a bundle that something on disk
            # may still reference. The reader has to see why.
            print(f"  WARNING: cannot read the existing {derived_id} fixture "
                  f"to recover its donor ({exc}). Searching for a new one.")
            return None
        bundle = (recorded.get("derivation") or {}).get("donor_bundle")
        if not bundle:
            return None
        for candidate in rows:
            if candidate["bundle"] == bundle:
                return candidate
        print(f"  WARNING: {derived_id} names donor {bundle}, which is no "
              f"longer in the cohort. Searching for a new one.")
        return None

    # --- no_candidates -----------------------------------------------------
    if selection.get(CASE_NO_CANDIDATES):
        _add("no_candidates", selection[CASE_NO_CANDIDATES], [CASE_NO_CANDIDATES])
    elif not args.scan_only and _wanted(RECIPE_NO_CANDIDATES):
        # Not reachable from this cohort — derive it. Every candidate is PROBED
        # against the live index before it is accepted, so the fixture is never
        # captured on an assumption about trial age windows.
        derived_id = RECIPE_NO_CANDIDATES
        # Temporary: schema v2 stores the recipe, not the bundle. This file is
        # only alive between deriving it and capturing the run on it.
        _handle, out_path = tempfile.mkstemp(prefix=f"{derived_id}_",
                                             suffix=".bundle.json")
        os.close(_handle)
        accepted = None
        tried = 0
        best = None

        known = _recorded_donor(derived_id)
        if known is not None:
            print(f"\n[Derive] {derived_id}: rebuilding from the donor the "
                  f"existing fixture records ({known['bundle'][:44]})")
            info = build_no_candidates_bundle(
                known["path"], out_path, NO_CANDIDATES_AGE_YEARS
            )
            outcome = probe_empty_candidate_pool(out_path)
            tried = 1
            print(f"  survivors={outcome['survivors']}")
            if outcome["empty_pool"]:
                accepted = (known, info, outcome)
            else:
                print("  The recorded donor no longer empties the pool — the "
                      "index has changed. Searching for another.")

        if accepted is None:
            print(f"\n[Derive] {derived_id}: no cohort patient empties the "
                  f"candidate pool. Searching donors, age forced to "
                  f"{NO_CANDIDATES_AGE_YEARS}y...")
        while accepted is None and tried < NO_CANDIDATES_MAX_DONORS and donors:
            donor = _next_donor()
            tried += 1
            info = build_no_candidates_bundle(
                donor["path"], out_path, NO_CANDIDATES_AGE_YEARS
            )
            outcome = probe_empty_candidate_pool(out_path)
            if best is None or outcome["survivors"] < best[1]["survivors"]:
                best = (donor, outcome)
            if outcome["empty_pool"] or tried % 10 == 0:
                print(f"  [{tried}/{NO_CANDIDATES_MAX_DONORS}] "
                      f"{donor['bundle'][:38]:<38} "
                      f"survivors={outcome['survivors']:>3} "
                      f"({donor['primary_diagnosis'][:34]})")
            if outcome["empty_pool"]:
                accepted = (donor, info, outcome)
                break
        if accepted is None:
            print(f"  [Derive] FAILED after {tried} donor(s). Best was "
                  f"{best[1]['survivors']} survivor(s) "
                  f"({best[0]['bundle'][:40]}). Every Stage 4 rule is "
                  f"conservative, so a trial stating no age, sex, stage or "
                  f"histology cannot be dropped by any of them.")
            if os.path.exists(out_path):
                os.remove(out_path)
        else:
            donor, info, outcome = accepted
            _add(derived_id, donor, [CASE_NO_CANDIDATES],
                 bundle_path=out_path,
                 bundle_location=BUNDLE_DERIVED,
                 derivation={
                     "recipe": RECIPE_NO_CANDIDATES,
                     "donor_bundle": donor["bundle"],
                     "donor_patient_id": donor["patient_id"],
                     "params": {"age_years": NO_CANDIDATES_AGE_YEARS},
                 },
                 construction={
                     "derived_from_bundle": donor["bundle"],
                     "what_was_changed": (
                         f"Patient.birthDate set to {info['birth_date']}, making "
                         f"the patient {info['age_years']} year(s) old at the age "
                         f"reference date {info['age_reference_date']}. Nothing "
                         f"else in the bundle was touched."
                     ),
                     "why": (
                         "node_no_candidates is not reachable from the 1,000-"
                         "patient cohort: 250 patients were probed against the "
                         "live index and every one retrieved a full pool of 100 "
                         "with 39-95 trials surviving Stage 4. Forcing the age "
                         "into the paediatric range empties it, but only for a "
                         "patient whose retrieved pool contains no trial with "
                         "blank age bounds — File 13 reads a blank min_age as 0 "
                         f"and a blank max_age as 999. {tried} donor(s) were "
                         "probed to find one. The pipeline run itself is real; "
                         f"only the input was derived. Probe at capture time: "
                         f"pool={outcome['pool_size']}, "
                         f"survivors={outcome['survivors']}."
                     ),
                 })

    # --- unknown_stage (a real cohort patient) -----------------------------
    if selection.get(CASE_UNKNOWN_STAGE):
        _add("unknown_stage", selection[CASE_UNKNOWN_STAGE], [CASE_UNKNOWN_STAGE])

    # --- mesh_fallback -----------------------------------------------------
    if selection.get(CASE_MESH_FALLBACK):
        _add("mesh_fallback", selection[CASE_MESH_FALLBACK], [CASE_MESH_FALLBACK])
    elif not args.scan_only and _wanted(RECIPE_MESH_FALLBACK):
        derived_id = RECIPE_MESH_FALLBACK
        _handle, out_path = tempfile.mkstemp(prefix=f"{derived_id}_",
                                             suffix=".bundle.json")
        os.close(_handle)
        # The donor the existing fixture names wins over a fresh pick, so
        # re-running this file regenerates the same bundle instead of
        # repointing a captured fixture at a different patient.
        donor = _recorded_donor(derived_id) or _next_donor()
        print(f"\n[Derive] {derived_id}: no cohort patient takes "
              f"EXPANSION_PATH_FALLBACK; deriving from {donor['bundle'][:44]}")
        info = build_mesh_fallback_bundle(donor["path"], out_path)
        was = ", ".join(f"{c['was_code']} ({c['was_display']})"
                        for c in info["codings_rewritten"])
        print(f"  rewrote {len(info['codings_rewritten'])} primary-cancer "
              f"coding(s): {was[:100]}")
        _add(derived_id, donor, [CASE_MESH_FALLBACK],
             bundle_path=out_path,
             bundle_location=BUNDLE_DERIVED,
             derivation={
                 "recipe": RECIPE_MESH_FALLBACK,
                 "donor_bundle": donor["bundle"],
                 "donor_patient_id": donor["patient_id"],
                 "params": {},
             },
             construction={
                 "derived_from_bundle": donor["bundle"],
                 "what_was_changed": (
                     f"Every primary-cancer SNOMED coding was replaced with "
                     f"{MESH_FALLBACK_SNOMED_CODE} "
                     f"({MESH_FALLBACK_DISPLAY}), and Condition.code.text with "
                     f"the same display. Was: {was}. Nothing else in the bundle "
                     f"was touched."
                 ),
                 "why": (
                     "All 1,000 patients in the cohort resolve to MeSH with "
                     "terms (799 snomed, 160 fuzzy_stem, 27 fuzzy_stem+snomed, "
                     "14 fuzzy_substring+snomed), so EXPANSION_PATH_FALLBACK is "
                     "unexercised by the corpus. SNOMED 363346000 is a real "
                     "concept with its real display and is what an EHR records "
                     "when the site was never coded; it resolves only to a "
                     "pan-cancer MeSH node, which is rejected as an identity, "
                     "so Stage 1 has no descriptors to expand with."
                 ),
             })

    # --- mCODE genomic variant (always derived; the corpus has none) --------
    if not args.scan_only and _wanted(RECIPE_MCODE_VARIANT):
        derived_id = RECIPE_MCODE_VARIANT
        _handle, out_path = tempfile.mkstemp(prefix=f"{derived_id}_",
                                             suffix=".bundle.json")
        os.close(_handle)
        donor = _recorded_donor(derived_id) or _next_donor()
        print(f"\n[Derive] {derived_id}: no cohort bundle carries LOINC "
              f"{MCODE_VARIANT_LOINC}; deriving from {donor['bundle'][:44]}")
        info = build_mcode_variant_bundle(donor["path"], out_path)
        print(f"  injected {info['gene']} {info['protein_change']} "
              f"(LOINC {info['loinc']}) dated {info['effective'][:10]}")
        _add(derived_id, donor, [CASE_MCODE_VARIANT],
             bundle_path=out_path,
             bundle_location=BUNDLE_DERIVED,
             derivation={
                 "recipe": RECIPE_MCODE_VARIANT,
                 "donor_bundle": donor["bundle"],
                 "donor_patient_id": donor["patient_id"],
                 "params": {},
             },
             construction={
                 "derived_from_bundle": donor["bundle"],
                 "what_was_changed": (
                     f"One Observation was appended: LOINC {info['loinc']} "
                     f"(genetic variant assessment), value Present, with "
                     f"component {MCODE_GENE_STUDIED_LOINC} = {info['gene']} "
                     f"and component {MCODE_PROTEIN_CHANGE_LOINC} = "
                     f"{info['protein_change']}, effective {info['effective']}. "
                     f"Nothing else in the bundle was touched."
                 ),
                 "why": (
                     "Item 19b made genomic variant detection structural, with "
                     "an 'mcode' path reading cancer_genomic_variants and a "
                     "'structured' path reading LOINC 69548-6 or a gene_symbol "
                     "off an observation. Not one of the 1,000 bundles in the "
                     "corpus contains a 69548-6 observation, so both paths were "
                     "wired in and never executed: a refactor could delete "
                     "either and every fixture would still replay clean. This "
                     "fixture makes them visible."
                 ),
             })

    # --- ablation and the normal patients ----------------------------------
    for spec in ABLATION_FIXTURES:
        row = selection.get(f"ablation::{spec['config_name']}")
        if row:
            _add(spec["fixture_id"], row, [CASE_ABLATION],
                 spec["flags"], spec["config_name"])
    for index, row in enumerate(selection.get("normals") or []):
        _add(f"normal_{index + 1}", row, [CASE_NORMAL])

    # --- truncation split --------------------------------------------------
    # A real run with one injected truncation. The splitter genuinely executes,
    # genuinely issues the two half-batch calls, and all three exchanges are
    # recorded — so the expected prefix is observed, not hand-derived. The
    # fixture is marked constructed because the truncation was injected.
    if not args.scan_only and _wanted("truncation_split"):
        _trunc_row = _next_donor()
        _add("truncation_split", _trunc_row, [CASE_TRUNCATION],
             truncate_first_call=True,
             construction={
                 "derived_from_bundle": _trunc_row["bundle"],
                 "what_was_changed": (
                     "Nothing in the bundle. During capture, the FIRST Stage 5 "
                     "response had its finish_reason relabelled to 'length' "
                     "before the pipeline saw it. Everything after that is a "
                     "real run: the splitter halved the batch and issued both "
                     "half-batch calls against the live model, and all three "
                     "exchanges are recorded as they happened."
                 ),
                 "why": (
                     "The truncation splitter fires on a response cut off at "
                     "MATCHING_MAX_TOKENS. Over 1,094 historical inferences the "
                     "largest output was 15,930 tokens of 16,000 — it happens, "
                     "but no cohort scan reliably produces one, and "
                     "hand-deriving the expected prefix for a split run would "
                     "mean guessing the half-batch request digests and the "
                     "merged verdicts."
                 ),
             })

    for entry in plan:
        row = entry["row"]
        print(f"  {entry['fixture_id']:<28} {row['bundle'][:44]:<44}")
        print(f"  {'':<28} patient_id={row['patient_id']}")
        print(f"  {'':<28} dx={row['primary_diagnosis'][:60]}")
        print(f"  {'':<28} mesh={row['mesh_resolution']} "
              f"path={row['expansion_path']} stage={row['stage']} "
              f"ecog={row['ecog']}")
        if entry["config_name"]:
            print(f"  {'':<28} ablation={entry['config_name']}")
        if entry["construction"]:
            print(f"  {'':<28} DERIVED BUNDLE from "
                  f"{entry['construction']['derived_from_bundle'][:40]}")
    print(f"  {'gpt4o_retry_constructed':<28} constructed from normal_1")

    if args.scan_only:
        print("\n--scan-only: nothing captured.")
        return 0

    # --- Capture -----------------------------------------------------------
    environment = build_environment_block()
    print(f"\n[Env] Pinned Qdrant collection: {environment['qdrant_collection']}"
          f"{'' if environment['alias_resolved'] else '  (alias fallback!)'}")

    graph = build_matching_graph()

    written = []
    normal_1_fixture = None

    with CaffeinateSession("fixture capture"):
        for entry in plan:
            if args.only and entry["fixture_id"] not in args.only:
                continue
            fixture = capture_fixture(
                fixture_id=entry["fixture_id"],
                bundle_path=entry["bundle_path"],
                case_labels=entry["labels"],
                graph=graph,
                ablation_flags=entry["flags"],
                ablation_config_name=entry["config_name"],
                environment=environment,
                bundle_location=entry["bundle_location"],
                case_evidence={
                    "scan_mesh_resolution": entry["row"]["mesh_resolution"],
                    "scan_expansion_path": entry["row"]["expansion_path"],
                    "stage": entry["row"]["stage"],
                    "ecog": entry["row"]["ecog"],
                    "primary_diagnosis": entry["row"]["primary_diagnosis"],
                    "donor_bundle": entry["row"]["bundle"],
                },
                construction=entry["construction"],
                derivation=entry["derivation"],
                truncate_first_call=entry.get("truncate_first_call", False),
            )
            path = write_fixture(fixture, root)
            written.append(fixture)
            print(f"  -> {os.path.basename(path)} "
                  f"({os.path.getsize(path) / 1024:.0f} KB, "
                  f"terminal={fixture['deterministic_prefix']['terminal']['terminal_node']})")
            if entry["fixture_id"] == "normal_1":
                normal_1_fixture = fixture

    # Temporary derived bundles are a copy of a Synthea record each, hundreds
    # of megabytes. Schema v2 stores the recipe, so nothing needs them once the
    # run they fed has been captured.
    for entry in plan:
        if entry["derivation"] and os.path.exists(entry["bundle_path"]):
            os.remove(entry["bundle_path"])
            print(f"  removed temporary bundle for {entry['fixture_id']}")

    # --- The constructed retry fixture -------------------------------------
    if normal_1_fixture is None:
        existing = fixture_path("normal_1", root)
        if os.path.exists(existing):
            normal_1_fixture = load_fixture(existing)

    if normal_1_fixture is not None and (
            not args.only or "gpt4o_retry_constructed" in args.only):
        retry_fixture = build_constructed_retry_fixture(
            normal_1_fixture, "gpt4o_retry_constructed"
        )
        path = write_fixture(retry_fixture, root)
        written.append(retry_fixture)
        print(f"\n  -> {os.path.basename(path)} (constructed from "
              f"{retry_fixture['construction']['derived_from']})")
    elif normal_1_fixture is None:
        print("\n  WARNING: normal_1 was not captured, so the constructed retry "
              "fixture was not built.")

    # --- Index -------------------------------------------------------------
    all_fixtures = []
    for path in list_fixtures(root):
        try:
            all_fixtures.append(load_fixture(path))
        except ValueError as exc:
            print(f"  WARNING: {exc}")

    index = {
        "schema_version": SCHEMA_VERSION,
        "written_at_utc": datetime.now(timezone.utc).isoformat(),
        "qdrant_collection": environment["qdrant_collection"],
        "fixtures": [
            {
                "fixture_id": f["fixture_id"],
                "fixture_kind": f["fixture_kind"],
                "case_labels": f["case_labels"],
                "patient_id": f["identity"]["patient_id"],
                "patient_data_hash": f["identity"]["patient_data_hash"],
                "source_bundle": f["identity"]["source_bundle"],
                "terminal_node": f["deterministic_prefix"]["terminal"]["terminal_node"],
                "ablation_config_name": f["inputs"]["ablation_config_name"],
            }
            for f in sorted(all_fixtures, key=lambda f: f["fixture_id"])
        ],
    }
    with open(os.path.join(root, FIXTURE_INDEX_FILENAME), "w") as fh:
        json.dump(index, fh, indent=1)

    covered = set()
    for f in all_fixtures:
        covered.update(f["case_labels"])

    print(f"\n{'=' * 78}")
    print(f"Wrote {len(written)} fixture(s); {len(all_fixtures)} in {root}")
    print(f"Branch cases covered: {sorted(covered & set(ALL_BRANCH_CASES))}")
    uncovered = sorted(set(ALL_BRANCH_CASES) - covered)
    if uncovered:
        print(f"Branch cases NOT covered: {uncovered}")
    print(f"{'=' * 78}\n")

    return 0 if not uncovered else 1


if __name__ == "__main__":
    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug  4 09:15:00 2026

@author: ramyalsaffar
"""
