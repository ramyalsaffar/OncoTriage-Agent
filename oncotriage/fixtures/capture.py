"""Characterization fixture capture: record what the pipeline does today.

Moved out of ``fixture_capture.py`` by item 20c, pass 3d.
``fixture_capture.py`` survives as a THIN ENTRY POINT.

THE SHIM QUESTION WAS SETTLED BY MEASUREMENT, AND THE ANSWER CHANGED
--------------------------------------------------------------------
The pass began on the assumption that File 45 would need a re-export shim,
because ``fixture_replay.py`` exec-chains it and reads eighteen names out of
the shared namespace. All 101 of File 45's top-level names were grepped against
every ``.py``, ``.md``, ``.toml`` and ``.yml`` in the tree first, and the result
is that **File 46 is the only consumer** -- every distinctive hit
(``BUNDLE_DERIVED``, ``BUNDLE_IN_COHORT``, ``FIXTURE_KIND_CONSTRUCTED``,
``FIXTURE_ROOT``, ``SCHEMA_VERSION``, ``_HOOK_KEYS``, ``OpenAIProxy``,
``QdrantProxy``, ``RecordingSink``, ``assert_hooks_reach_the_agent``,
``build_deterministic_prefix``, ``compute_collection_digest``,
``flatten_prefix``, ``list_fixtures``, ``load_fixture``,
``rebuild_derived_bundle``, ``restore_hooks``, ``sha256_json``) is a line in
File 46, and the rest are prose in CLAUDE.md, ``oncotriage/agent/deps.py`` and
``oncotriage/config.py``, or the exec-bootstrap locals every numbered file
shares.

This same pass converts File 46. It imports the names above FROM HERE, so after
it nothing in the repository chains File 45 and nothing reads a name out of it.
A shim would therefore have been re-exports with no reader -- exactly the dead
declaration File 47 check 2h now scans for -- so File 45 keeps none, on the same
measured basis as Files 21, 22, 23, 24 and 29.

FIVE THINGS CHANGED, AND THEY ARE THE WHOLE DIFF
------------------------------------------------
1. **The File 14 chain is GONE, with its scratch-database redirect.**
   File 45 chained ``14- Database Logger.py`` for one reason, stated in its own
   comment: File 13's three terminal nodes call ``_resolve_primary_cancer()``,
   which used to live in File 14. Pass 20c-2b moved that function to
   ``oncotriage/registries/primary_cancer.py`` and
   ``oncotriage/agent/terminal.py`` imports it from there -- so the reason
   expired two passes ago and the chain survived it. Nothing else in File 45
   used a File 14 name: ``log_inference`` was neutralized rather than called,
   and ``resolve_inference_db_path`` was used only to assert that the
   neutralization was real.

   With the chain gone, the ``inferences_path = FIXTURE_SCRATCH_DB`` rebinding
   that existed only to make the chain safe goes with it. Nothing here opens,
   creates or names a database for writing.

2. **``_assert_database_is_isolated()`` was re-expressed for a module world**,
   and one of its five checks had to be, because it was a statement about a
   shared namespace that a module does not have. See that function.

3. **``FIXTURE_ROOT`` became ``fixture_root()``, and it no longer creates the
   directory.** ``_resolve_fixture_root()`` globbed ``main_path`` at module
   level AND called ``os.makedirs``, so importing this module resolved a
   sibling directory and created one. Both are forbidden at import. Resolution
   is lazy and cached; creation stays where File 45 already also had it, in
   ``main()``'s ``os.makedirs(root, exist_ok=True)``, which is the only line
   that handles ``--fixture-dir`` as well. That is the same ``output_dir()`` /
   ``ensure_output_dir()`` split pass 20c-3b made in
   ``oncotriage/fhir/explore.py``, for the same reason: a caller asking where a
   fixture would go must not make a directory appear.

4. **The two direct Qdrant calls go through ``oncotriage.config``, not
   ``oncotriage.agent.deps``.** ``compute_collection_digest()`` and
   ``_resolve_and_verify_collection()`` read a bare ``qdrant_client`` out of the
   shared namespace, which under the exec chain was File 03's eagerly-built REAL
   client -- never the ``QdrantProxy``, which is installed through ``deps``.
   ``config.get_qdrant_client()`` is that same object, and routing it through
   ``deps`` instead would be a behaviour change: the digest would be taken
   through a recording proxy and land in the sink. The digest is a fact about
   the live index and must be measured against the live server. Same rule, same
   direction, as ``oncotriage/retrieval/indexer.py`` and
   ``oncotriage/retrieval/qdrant_backup.py``.

   Everything the AGENT reaches still goes through ``deps`` -- that is what
   ``install_recording_hooks()`` installs and what
   ``assert_hooks_reach_the_agent()`` proves by identity.

5. **``_CANCER_REGISTRY`` and ``_MESH_FILTER`` became
   ``deps.get_cancer_registry()`` and ``deps.get_mesh_filter()``.** File 13's
   shim binds those two names from exactly those accessors (its lines 232 and
   234), so these are the same objects. It has to be ``deps`` here and not
   ``load_registry()``: ``scan_cohort()`` and ``probe_empty_candidate_pool()``
   exist to agree with what Stage 1 and Stage 4 will do, and the pipeline reads
   both through ``deps``. ``get_mesh_filter()`` returning ``None`` is a real
   answer and every ``is None`` branch below is unchanged.

Nothing else moved. The schema, the recording sink, all four proxies, the
deterministic prefix, the completeness checks, the three derivation recipes, the
constructed retry fixture, the cohort scan, the selection and ``main()`` are the
line slice of File 45 between its bootstrap and its ``__main__`` guard.

**THE FIXTURE FORMAT IS AT v8.** v8 widens the ``stage5`` VERDICT projection by
four fields, in two pairs, each closing a blind spot the prefix had rather than
recording a new fact: ``assessment`` and ``assessment_draft`` -- the composed
assessment and the model draft it was composed from, so that a regression of
the composition itself is a diffed fact instead of replaying clean -- and
``emission_index`` and ``call_index``, the two emission-provenance stamps, so
that where the model put an entry and which billed call answered for it are
held by the harness and not only by a stubbed-model unit test. See the per-bump
block at ``SCHEMA_VERSION``.

v7 was one field's null convention:
``stage5.llm_classifier_combined_prompt_sha256`` records ``None`` when Stage 5
rendered no prompt, where it used to record the sha256 of the empty string --
a real-looking digest for a prompt that never existed, on the one fixture that
terminates at ``node_no_candidates``. Its sibling
``llm_classifier_prompt_sha256`` has always used ``None`` for that case; there
is one convention now.

**v6 WAS PROVENANCE, AND THREE SEPARATE CHANGES GOT IT THERE.** ``stage3``
gained the two cross-encoder score lists
(``medcpt_score_max``, ``medcpt_queries_scored``) that MEDCPT_SCORE_FLOOR is
applied to, ``stage5`` gained ``llm_classifier_prompt_version`` and the
SYSTEM-only ``llm_classifier_prompt_sha256``, and the old combined system+user
hash was renamed ``llm_classifier_combined_prompt_sha256`` because it collided
with the database column of the former name while holding a different value.
The v5 and v4 changes are recorded immediately below and both still hold.

v5: the Structured Outputs pass added ``response_format`` -- the whole strict
json_schema -- to the recorded request block, and that block is hashed into
``stage5.request_sha256_by_call``, so every v4 digest is unreachable from here.

**THE FIXTURE FORMAT WAS FROZEN AT v3 AND THE llm_classifier RENAME BROKE IT.**
That was v4. Eight recorded fields under ``stage5`` and one key in
``environment`` carried the ``gpt4o`` prefix and now carry ``llm_classifier``;
the constructed fixture's id and case label changed with them. Every field's
MEANING is unchanged -- only its name moved -- and that is precisely the case
the version exists for, because a v3 fixture read by this code would report
every renamed field as absent and a replay would compare ``None`` with ``None``
and call it a match. ``load_fixture()`` refuses the mismatch by version, before
any field is read.

THE TWELVE FIXTURES ON DISK ARE AT THE CURRENT VERSION and replay clean. They
were at v3 through the v4/v5/v6 bumps -- unreadable, and already unreplayable
for an unrelated reason, the alias ``trial_criteria`` having resolved past the
collection digest they pin at the M-category pass -- so none of those three
bumps lost a working gate. The v6 re-capture cleared both. The v6 -> v7 move was
then applied to the files IN PLACE by a migration rather than by re-capturing,
because v7 changes one stored value on one fixture and a re-capture costs twelve
live Stage 5 calls to reproduce eleven fixtures byte-for-byte. **The v7 -> v8
move was NOT migratable and was re-captured**: the four fields it adds are read
off the evaluation entries of a run, and no migration can invent what the model
emitted on a run whose entries were never stored -- ``assessment_draft`` has no
database column, and ``emission_index`` / ``call_index`` are positions in a
response array that only the run itself saw.

Nothing here silently skips an unreadable fixture: ``fixture_replay.py`` prints
every load failure and exits 2 for them, distinctly from the 1 it exits on a
replay difference (a stale file lying in the directory and the pipeline having
changed are different findings with different owners).

Nothing else about the STORAGE moved: ``write_fixture``'s JSON and the gzip
settings are untouched -- ``compresslevel=9, mtime=0``, where the zeroed mtime
is what makes two captures of identical content produce identical bytes instead
of a git diff per re-capture.

WHAT IMPORTING THIS MODULE DOES
-------------------------------
Nothing. No path is resolved, no directory is created, no client is built, no
model is loaded, no database is opened, no fixture is read. Everything above is
behind an accessor that answers on first call.

See the original module docstring of ``fixture_capture.py`` in git history
for the full fixture-format reference; the field-by-field description is
reproduced in ``fixture_replay.py``'s consumer-side documentation and in
CLAUDE.md.

USAGE
-----
    python "fixture_capture.py"                    # scan, select, capture all
    python "fixture_capture.py" --scan-only        # cohort scan + case report
    python "fixture_capture.py" --probe-limit 400  # widen the no-candidates hunt
    python "fixture_capture.py" --only normal_1 llm_classifier_parse_retry_constructed
"""

import argparse
import copy
import glob
import gzip
import hashlib
import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Dict, List

from dateutil.relativedelta import relativedelta

from oncotriage import config, paths
from oncotriage.agent import deps
from oncotriage.agent import models as _agent_models
from oncotriage.agent.graph import build_initial_state, build_matching_graph
from oncotriage.agent.filtering import node_rule_based_filter
from oncotriage.agent.mesh_expansion import expand_query_from_mesh, resolve_patient_mesh
from oncotriage.agent.patient import compute_patient_hash, extract_genomic_variant_terms
from oncotriage.agent.prompts import PROMPT_VERSION
from oncotriage.agent.retrieval import node_hybrid_retrieval, node_query_expansion
from oncotriage.agent.state import (
    EXPANSION_PATH_FALLBACK,
    EXPANSION_PATH_MESH,
    RETRIEVAL_CHANNELS,
)
from oncotriage.config import (
    BM25_RETRIEVAL_SIZE,
    CHARS_PER_TOKEN,
    COLLECTION_NAME,
    CROSS_ENCODER_MAX_LENGTH,
    CROSS_ENCODER_MODEL,
    DATA_SNAPSHOT_DATE,
    EMBEDDING_MODEL,
    MATCHING_MAX_TOKENS,
    MATCHING_MODEL,
    MATCHING_OUTPUT_SPLIT_FRACTION,
    MATCHING_OUTPUT_TOKENS_PER_TRIAL,
    MATCHING_REASONING_EFFORT,
    MATCHING_SEED,
    MATCHING_TEMPERATURE,
    MAX_LLM_CLASSIFIER_RETRIES,
    MAX_TRIALS_FOR_EVALUATION,
    MAX_TRUNCATION_SPLITS,
    MAX_VARIANT_TERMS,
    MEDCPT_SCORE_FLOOR,
    MESH_BOOST_DIRECT_FLOOR,
    MESH_BOOST_DIRECT_FRACTION,
    MESH_BOOST_PAN_FLOOR,
    MESH_BOOST_PAN_FRACTION,
    Project_Name,
    QUALITY_THRESHOLD_PERCENTILE,
    RRF_K,
    RRF_POOL_SIZE,
    RRF_WEIGHT_CONDITIONS,
    RRF_WEIGHT_CRITERIA,
    RRF_WEIGHT_DENSE,
    RRF_WEIGHT_TITLE,
    TOP_K_CANDIDATES,
    VECTOR_RETRIEVAL_SIZE,
)
from oncotriage.embedding import BM25_SPARSE_MODEL_NAME
from oncotriage.extraction.stage import extract_patient_stage
from oncotriage.fhir.parser import parse_fhir_bundle
from oncotriage.storage import database_logger as _database_logger
from oncotriage.utils import (
    CaffeinateSession,
    UnknownModelPricingError,
    get_age_reference_date,
    get_model_cost,
    qdrant_retry,
    resolve_qdrant_collection,
)
from oncotriage.observability import console, correlation_scope


#------------------------------------------------------------------------------


# ===========================================================================
# WHERE FIXTURES LIVE
# ===========================================================================
#
# Derived from paths.main_path by glob prefix, the same way every other sibling
# directory is resolved, so this module contains no absolute path and the
# directory can be renumbered. Fixtures are data: they do not belong in the
# version-controlled code folder, and they are large enough (a parsed patient
# bundle plus a 1536-float embedding plus a full GPT-4o exchange) that they
# would dominate the repo.
#
# RESOLUTION AND CREATION ARE SEPARATE, and File 45 had them fused. Its
# _resolve_fixture_root() globbed AND called os.makedirs, and the result was
# assigned to FIXTURE_ROOT at module level -- so merely loading the file
# resolved a sibling directory and created one. Both are things no module in
# this package may do at import.
#
# This is the same split pass 20c-3b made in oncotriage/fhir/explore.py:
# output_dir() answers where, and something else makes it so. A caller that
# only wants to print the fixture directory, or list what is in it, must be able
# to ask without anything appearing on disk -- and fixture_replay.py is
# exactly that caller: it reads fixture_root() and globs it, and must not create
# a directory as a side effect of finding there are no fixtures in it.
#
# THERE IS NO ensure_fixture_root(). main() already carried
# `os.makedirs(root, exist_ok=True)` one line after resolving `root`, and that
# line handles BOTH cases -- the resolved root and an explicit --fixture-dir --
# where a dedicated accessor would handle only the first. It is the sole
# creation site now, which is one fewer than File 45 had.

_RESOLVED = {}

# Locked, matching oncotriage/agent/deps.py, oncotriage/paths.py and
# oncotriage/fhir/clean.py. Nothing here is multi-threaded -- capture runs one
# patient at a time -- but `if k not in d: d[k] = build()` is two atomic
# operations and one non-atomic sequence, and this is the pattern the next
# accessor added here will copy.
_RESOLVE_LOCK = threading.RLock()

FIXTURE_INDEX_FILENAME = "index.json"


def fixture_root() -> str:
    """Where fixtures live. Resolved on first call, cached. CREATES NOTHING."""
    with _RESOLVE_LOCK:
        if "fixture_root" not in _RESOLVED:
            matches = sorted(glob.glob(os.path.join(paths.main_path, "*Testing")))
            testing_dir = matches[0] if matches else os.path.join(
                paths.main_path, "09- Testing")
            _RESOLVED["fixture_root"] = os.path.join(
                testing_dir, "Characterization Fixtures")
        return _RESOLVED["fixture_root"]


#------------------------------------------------------------------------------


# ===========================================================================
# THE PRODUCTION DATABASE IS NOT REACHABLE FROM HERE
# ===========================================================================
#
# WHAT THIS REPLACES, AND WHY IT IS SMALLER THAN IT WAS.
#
# File 45 chained "14- Database Logger.py" and, because loading File 14 used to
# open a database, pointed `inferences_path` at a scratch file in the system
# temp directory first. Its own comment gave the reason for the chain:
#
#     File 13's terminal nodes call _resolve_primary_cancer(), which lives in
#     File 14. Every production entry point chains 14 after 13, so the
#     dependency is satisfied by accident of ordering; invoking the graph
#     without 14 raises NameError inside node_finalize.
#
# THAT REASON EXPIRED IN PASS 20c-2b. _resolve_primary_cancer moved to
# oncotriage/registries/primary_cancer.py, and oncotriage/agent/terminal.py --
# which is where all three terminal nodes now live -- imports it from there by
# name. The chain outlived its justification by two passes, which is exactly
# what a conversion pass is for finding.
#
# So the chain is gone, and with it the redirect that existed only to make the
# chain safe. Nothing in this module opens a database, creates one, or names one
# for writing. What is KEPT is the pair of guards, because their job was never
# the chain:
#
#   * log_inference below still RAISES. It is not a wrapper around anything --
#     there is nothing to wrap -- it is a tripwire, and it stays for the same
#     reason _OpenAITripwire stays in the replay harness: a capture run that
#     somehow logged an inference would write a row indistinguishable from a
#     production one into the real database, and "nothing here calls it" is a
#     property of today's code rather than a guarantee.
#   * _assert_database_is_isolated() still runs before anything is captured.
#
# FIXTURE_SCRATCH_DB survives as a COMPARISON PROBE and nothing else. No file is
# ever created at that path now; it exists so the non-degeneracy checks below
# have a second, definitely-different path to compare the production default
# against. Keeping the name rather than inventing one keeps the fixture-capture
# vocabulary continuous with git history.

FIXTURE_SCRATCH_DB = os.path.join(
    tempfile.gettempdir(), "oncotriage_fixture_capture_scratch.db"
)


def production_inferences_path() -> str:
    """The real inferences.db, resolved lazily. Opened by nothing here.

    File 45 captured this as a module-level PRODUCTION_INFERENCES_PATH over the
    lazy `inferences_path`, so importing it globbed the sibling data tree.
    """
    with _RESOLVE_LOCK:
        if "production_inferences_path" not in _RESOLVED:
            _RESOLVED["production_inferences_path"] = paths.inferences_path
        return _RESOLVED["production_inferences_path"]


def log_inference(*_args, **_kwargs):
    """A tripwire. Capture must never log an inference, and this makes it so.

    Accepts any signature, including the pass-2b (result, patient_data, db_path)
    one, so a caller written against the real function lands on this raise
    rather than on a TypeError -- a TypeError would also stop the write, but it
    would report the wrong reason, and _assert_database_is_isolated() below
    accepts only a RuntimeError as evidence that the tripwire is intact.
    """
    raise RuntimeError(
        "oncotriage.fixtures.capture must not log inferences. Capture records "
        "what the pipeline DOES; a row in inferences.db would be "
        "indistinguishable from a production inference and would reach drift "
        "detection and the Reproducibility dashboard."
    )


#------------------------------------------------------------------------------


# ===========================================================================
# SCHEMA
# ===========================================================================

# Bump on any change to the MEANING of a stored field, including a field that
# is added to deterministic_prefix — an older fixture has no value for it, and
# a replay that silently treats "absent" as "matched" is a gate that passes
# because it stopped looking.
# v4: the gpt4o -> llm_classifier rename. Same fields, same meanings, new names
# for nine of them (eight under stage5, one in environment). A v3 fixture read
# by v4 code answers None for every renamed field, and a diff of None against
# None is a gate that passes because it stopped looking -- which is the exact
# failure this counter exists to prevent.
# v5: Structured Outputs. The recorded request block gained `response_format`,
# which carries the whole strict json_schema. This is the PARENTHETICAL case in
# the rule above rather than a judgement call: `request_sha256_by_call` in the
# deterministic prefix is sha256_json(request), so the new key lands INSIDE the
# digest and every v4 fixture's stored digest is unreachable by v5 code. A v4
# recording replayed here would miss on the request digest of every call, and a
# reader would go looking for a pipeline change that did not happen.
# v6: four provenance fields, and one rename that had to ride the same bump
# because the version was already moving.
#   - stage3 gained `medcpt_score_max` and `medcpt_queries_scored`, two lists
#     parallel to `rerank_scores_raw` over the same reranked trials in the same
#     order. Stage 3 has written both onto every reranked trial since the
#     two-knob quality gate, and MEDCPT_SCORE_FLOOR -- one of the two knobs --
#     reads the first of them. So the input to half of Stage 4's gate was
#     invisible to every fixture: a cross-encoder change that moved the
#     absolute scores while leaving the RRF ORDER alone reordered nothing,
#     dropped nothing, and diffed as clean.
#   - stage5 gained `llm_classifier_prompt_version` and
#     `llm_classifier_prompt_sha256`, taken verbatim off the result. They are
#     the two identifiers the Stage 5 system prompt is tracked by, `inferences`
#     has stored both since the prompt-version guard, and the fixture recorded
#     neither -- so a template edit that did not move a verdict was not a
#     diffed fact.
#   - the pre-v6 `llm_classifier_prompt_sha256` key is RENAMED to
#     `llm_classifier_combined_prompt_sha256`. It never held what its name
#     said: it is sha256 of `result["llm_classifier_prompt"]`, which is system
#     + user concatenated, while the identically named database column holds
#     the SYSTEM-only hash. One name, two values, and the fixture's was the one
#     nothing else in the project could reproduce. The database rename window
#     is closed -- that column ships -- and the fixture one is open exactly
#     here, because the version is moving and every fixture on disk is already
#     unreadable.
# A v5 fixture read by v6 code answers None for all four new keys and for the
# renamed one, and a diff of None against None is a gate that passes because it
# stopped looking -- which is the mismatch this version gate exists to refuse.
# v7: `llm_classifier_combined_prompt_sha256` records None, not the sha256 of
# the empty string, when Stage 5 rendered no prompt. It was
# `sha256_text(result.get("llm_classifier_prompt") or "")`, so a terminal
# node_no_candidates run -- which never reaches Stage 5 -- stored
# e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855, the sha256
# of "". That is a well-known constant that a reader has no way to distinguish
# from a real digest by looking at it, and any consumer comparing hashes as
# STRINGS reads it as "a prompt was rendered and here is its hash". The field's
# own sibling three lines below, `llm_classifier_prompt_sha256`, has always
# recorded None for exactly this case, and the comment above it already argued
# the point -- "Coercing that None to '' here would record the hash of a prompt
# that never existed" -- while this field did precisely that. One convention
# now: None means nothing was rendered.
# This is the MEANING of a stored field changing, which is the first line of the
# rule at the top of this block, so the version moves. A v6 fixture read by v7
# code compares e3b0c442... against None on the one fixture that never called
# Stage 5 -- a real difference, reported as a pipeline change that did not
# happen -- and every OTHER fixture would compare equal, which is worse: it
# would look like a verified set with one inexplicable outlier.
# v8: the stage5 VERDICT projection gains four fields, in two pairs. Both pairs
# are ADDITIONS to deterministic_prefix -- the first line of the rule at the top
# of this block -- so the version moves for the reason the counter exists rather
# than by judgement.
#   - `assessment` and `assessment_draft`, copied as stored. Since
#     PROMPT_VERSION 1.5.0 the STORED assessment of an `eligible` or
#     `not_eligible` trial is COMPOSED, by this pipeline, from that trial's own
#     criterion / patient_value / status rows, so that it cannot assert
#     anything the arrays do not carry; the model's own prose is kept beside it
#     as `assessment_draft`, in memory only, with no database column. The
#     prefix projected NEITHER, so it was blind to the composition: a revert of
#     compose_assessment() to returning the draft unchanged, or to returning
#     the empty string, is a total regression of that mechanism and all twelve
#     fixtures would still have replayed clean. Both are projected because only
#     the PAIR is diagnostic -- the draft alone moves whenever the model's prose
#     moves, which the recordings already pin, and the composed value alone
#     cannot separate "the renderer changed" from "the draft it renders
#     changed".
#   - `emission_index` and `call_index`, copied as stored. Integers on a
#     model-returned verdict -- where in the parsed array it stood, and which
#     billed call returned it -- and None on a verdict this pipeline
#     CONSTRUCTED (a truncation floor, an exhausted split budget, a model
#     omission, conflicting duplicates), which never stood in a response at
#     all. None is not 0: 0 names the first entry of the first call, a real
#     place some other trial occupies. They are stamped on the FULL parsed list
#     BEFORE the first drop, and survive every later filter and the sort, which
#     is what makes them the only surviving record of the model's own emission
#     order. tests/test_agent_emission_provenance.py holds them against a
#     stubbed model; the fixture harness held nothing, so a stamp that moved to
#     after the sort -- reporting this pipeline's own ranking back as the
#     model's order -- was not a diffed fact on any of the twelve.
# A v7 fixture read by v8 code answers None for all four, and a diff of None
# against None is a gate that passes because it stopped looking -- which is the
# exact failure this counter exists to prevent.
SCHEMA_VERSION = 8

# Branch cases the fixture set must cover. Values are stored in case_labels.
CASE_NO_CANDIDATES = "no_candidates"        # a terminal node_no_candidates run
CASE_UNKNOWN_STAGE = "unknown_stage"        # extract_patient_stage() -> None
CASE_MESH_FALLBACK = "mesh_fallback"        # Stage 1 took EXPANSION_PATH_FALLBACK
CASE_ABLATION = "ablation"                  # run under a File 26 config
CASE_MCODE_VARIANT = "mcode_variant"        # structural genomic variant detection
CASE_LLM_CLASSIFIER_PARSE_RETRY = "llm_classifier_parse_retry"  # the MAX_LLM_CLASSIFIER_RETRIES loop
CASE_TRUNCATION = "truncation_split"        # the MAX_TRUNCATION_SPLITS loop
CASE_NORMAL = "normal"                      # full pipeline, no branch of note

ALL_BRANCH_CASES = (
    CASE_NO_CANDIDATES,
    CASE_UNKNOWN_STAGE,
    CASE_MESH_FALLBACK,
    CASE_ABLATION,
    CASE_LLM_CLASSIFIER_PARSE_RETRY,
    CASE_MCODE_VARIANT,
    CASE_TRUNCATION,
)

FIXTURE_KIND_RECORDED = "recorded"
FIXTURE_KIND_CONSTRUCTED = "constructed"

# Where a fixture's source bundle lives.
BUNDLE_IN_COHORT = "cohort"    # under paths.data_fhir_path, untouched
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
                # THE WHOLE SCHEMA, not a flag saying one was sent. Structured
                # Outputs constrains decoding, so response_format is the single
                # parameter here with the most direct effect on what comes back
                # -- an enum member added or a field made optional changes the
                # answer as surely as changing the prompt does, and a fixture
                # that recorded only "json_schema" could not see either.
                # Deep-copied for the same reason `messages` is: the SDK is
                # handed this object and the recording must be of what was sent,
                # not of whatever it looks like later.
                "response_format": copy.deepcopy(kwargs.get("response_format")),
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
# THIS USED TO REBIND FOUR NAMES IN THIS MODULE'S globals(), AND THAT STOPPED
# WORKING IN PASS 20c-2c. The old comment read:
#
#   Every project file is exec'd into THIS module's globals(), so File 13's
#   functions resolve `openai_client`, `qdrant_client`, `_bm25_query_model` and
#   `medcpt_score_pairs` out of this dict at call time. Rebinding a name here is
#   therefore all it takes to redirect the pipeline, and restoring it puts
#   things back exactly.
#
# Every sentence of that was true and every sentence of it is now false. File 13
# is a shim over oncotriage/agent/; its functions resolve their globals in their
# own modules, and a rebinding here reaches none of them.
#
# THE CONSEQUENCE, IF NOTHING HAD CHANGED HERE, is the worst shape a regression
# can have. Capture would have issued real OpenAI calls and recorded NOTHING
# into the fixture, and fixture_replay.py -- whose entire claim is that it
# makes no model call -- would have sent every Stage 5 prompt to the real
# endpoint and paid for it, while still reporting that all twelve fixtures
# replayed clean. Nothing would have raised.
#
# oncotriage/agent/deps.py is the replacement: a real seam with named override
# keys, which every call site inside the agent goes through. The four hooks are
# the same four objects; only the installation changed.
#
# MEDCPT_SCORER, not MEDCPT_MODEL. The fixture records SCORES -- one float per
# trial text, keyed by the query and a sha256 of the texts -- so the whole
# (query, trial_texts) -> scores function is the seam. Replaying at the model
# level would mean fabricating a logits tensor and a tokenizer output shape to
# go with it, which is a second implementation of the thing under test.

_HOOKED_NAMES = (
    "openai_client",
    "qdrant_client",
    "_bm25_query_model",
    "medcpt_score_pairs",
)
"""The four seams, under their pre-2c names. Kept because fixture_replay.py
imports it and because the fixture schema and every diagnostic message in both
files speak in these terms. _HOOK_KEYS below is the mapping to the deps keys
that actually install them."""

_HOOK_KEYS = {
    "openai_client":      deps.OPENAI_CLIENT,
    "qdrant_client":      deps.QDRANT_CLIENT,
    "_bm25_query_model":  deps.BM25_QUERY_MODEL,
    "medcpt_score_pairs": deps.MEDCPT_SCORER,
}


def current_hook_targets() -> dict:
    """What the agent would reach RIGHT NOW for each of the four seams.

    Asked of deps, not of this namespace, because deps is what the agent asks.
    That is the whole point: a check that reads this module's globals would
    still pass with the hooks installed nowhere.

    MEDCPT_SCORER has no default inside deps -- oncotriage.agent.models owns it
    -- so an un-installed scorer reads back as deps.UNSET rather than as the
    real function. That asymmetry is deliberate and is what the assertions
    below compare against.
    """
    return {
        "openai_client":      deps.get_openai_client(),
        "qdrant_client":      deps.get_qdrant_client(),
        "_bm25_query_model":  deps.get_bm25_query_model(),
        "medcpt_score_pairs": deps.get_override(deps.MEDCPT_SCORER),
    }


def assert_hooks_reach_the_agent(expected: dict, what: str) -> None:
    """Refuse to run unless the agent reaches EXACTLY these four objects.

    Identity, not equality: a proxy forwards __eq__ to the object it wraps, so
    an equality test would happily accept the real client.

    This is the assertion that replaces "the rebinding worked because it always
    worked". It is called before every capture and before every replay, and
    fixture_replay.py demonstrates it FAILING when the overrides are not
    installed -- otherwise it would be one more thing that has only ever passed.
    """
    reached = current_hook_targets()
    wrong = sorted(
        name for name in _HOOKED_NAMES if reached[name] is not expected[name]
    )
    if wrong:
        raise RuntimeError(
            f"{what}: the agent does NOT reach the installed hook(s) for "
            f"{wrong}. oncotriage.agent.deps is the seam; rebinding a name in "
            f"this namespace redirects nothing, and the pipeline would run "
            f"against the real client while this file reported otherwise.\n"
            f"  installed via: deps.set_override(deps.<KEY>, proxy)\n"
            f"  active override keys: {deps.active_overrides()}"
        )


def install_recording_hooks(sink: RecordingSink,
                            truncate_first_call: bool = False) -> dict:
    """Redirect all four seams to recorders. Returns the saved override state.

    The return value goes to restore_hooks(), which is deps.restore_overrides()
    -- so a seam that had no override before is CLEARED rather than pinned to
    whatever it happened to resolve to.
    """
    proxies = {
        deps.OPENAI_CLIENT: OpenAIProxy(
            deps.get_openai_client(),
            _recording_embedding(sink),
            _recording_chat(sink, truncate_first_call),
        ),
        deps.QDRANT_CLIENT: QdrantProxy(deps.get_qdrant_client(), sink),
        deps.BM25_QUERY_MODEL: SparseModelProxy(deps.get_bm25_query_model(), sink),
        # Wraps the RAW function, not models.score_pairs, or the override would
        # dispatch back into itself on every call.
        deps.MEDCPT_SCORER: _recording_medcpt(
            _agent_models.medcpt_score_pairs, sink),
    }
    saved = deps.set_overrides(proxies)

    assert_hooks_reach_the_agent(
        {name: proxies[key] for name, key in _HOOK_KEYS.items()},
        "install_recording_hooks",
    )
    return saved


def restore_hooks(saved: dict) -> None:
    """Undo install_recording_hooks / install_replay_hooks."""
    deps.restore_overrides(saved)


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
            # The RAW cross-encoder score Stage 3 retained, and how many rerank
            # queries contributed to it. Parallel to the three lists above,
            # over the same reranked trials in the same order.
            #
            # RRF keeps ranks and throws the scores away, so the three lists
            # above describe the fused ORDER and nothing calibrated. These two
            # are what MEDCPT_SCORE_FLOOR -- one of Stage 4's two quality knobs
            # -- is applied to, which means a cross-encoder checkpoint change
            # that shifted the absolute scores without reordering the pool
            # moved the floor's drop set while every list above stayed
            # byte-identical.
            #
            # None is stored as JSON null and is NEVER coerced to 0.0: Stage 3
            # writes None when no rerank query scored the trial at all, and the
            # floor deliberately does not drop such a trial -- absence of a
            # score is not a low score. A 0.0 here would read as a real score
            # at the bottom of the distribution and would make the two cases
            # indistinguishable in the one record that exists to distinguish
            # them.
            "medcpt_score_max": [t.get("medcpt_score_max") for t in reranked],
            "medcpt_queries_scored": [
                t.get("medcpt_queries_scored") for t in reranked
            ],
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
            "llm_classifier_calls": len(sink.chat_completions),
            # Reported by the stage as well as counted at the client, because
            # the two agreeing is itself the check: a split run that recorded
            # fewer exchanges than it made would replay a different number of
            # requests than it captured.
            "llm_classifier_calls_reported": result.get("llm_classifier_calls"),
            # The truncation budget, separate from the retry budget below.
            "llm_classifier_truncation_splits": result.get("llm_classifier_truncation_splits"),
            "not_evaluable_truncated": result.get("not_evaluable_truncated"),
            # The pre-call estimate. Diffed exactly: it is a pure function of
            # the filtered trial set and the File 03 constants, so a change to
            # either shows up here rather than only in a log line.
            "llm_classifier_output_tokens_estimated": result.get("llm_classifier_output_tokens_estimated"),
            "finish_reasons": [
                c["response"].get("finish_reason") for c in sink.chat_completions
            ],
            "llm_classifier_retries": result.get("llm_classifier_retries"),
            "cross_vocab_remaps": result.get("cross_vocab_remaps"),
            "llm_classifier_input_tokens": result.get("llm_classifier_input_tokens"),
            "llm_classifier_output_tokens": result.get("llm_classifier_output_tokens"),
            # sha256 of result["llm_classifier_prompt"], which is the SYSTEM
            # message and the USER message concatenated. It therefore moves
            # with the patient record and with the filtered trial set, so it
            # identifies THIS RUN and not the prompt template -- which is why
            # it carries "combined" in its name from v6 on. Until v6 it was
            # called llm_classifier_prompt_sha256, colliding with the database
            # column of that name, which holds the SYSTEM-only hash.
            #
            # None -- NOT sha256("") -- when no prompt was rendered, from v7 on.
            # `or ""` stood here and hashed the empty string, so a terminal
            # node_no_candidates run stored e3b0c442..., which is a real-looking
            # digest for a prompt that never existed. Same convention as the
            # sibling field below, and for the reason its comment already gave.
            "llm_classifier_combined_prompt_sha256": (
                sha256_text(result["llm_classifier_prompt"])
                if result.get("llm_classifier_prompt") else None),
            # The two identifiers of the TEMPLATE, taken verbatim off the
            # result rather than re-rendered or re-hashed here: a fixture that
            # recomputed either would be comparing this file against itself
            # instead of against what Stage 5 published. The version is what a
            # human intended, the sha256 is what was actually sent, and both
            # are the same values stored in the identically named `inferences`
            # columns.
            #
            # THEY HAVE DIFFERENT NULL CONVENTIONS and both are stored as-is.
            # _pipeline_provenance falls the VERSION back to PROMPT_VERSION on
            # every path, including the two terminal nodes where Stage 5 never
            # ran -- it is a property of the build, not of the stage -- so this
            # is a string on every fixture. The HASH has no fallback and is
            # None for a run that rendered no prompt, which is how a reader
            # separates "Stage 5 ran" from "Stage 5 never ran". Coercing that
            # None to "" here would record the hash of a prompt that never
            # existed.
            "llm_classifier_prompt_version": result.get("llm_classifier_prompt_version"),
            "llm_classifier_prompt_sha256": result.get("llm_classifier_prompt_sha256"),
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
                    #
                    # IT CARRIES TWO FURTHER VALUES, and the wording above
                    # would otherwise send a reader looking for constants that
                    # are deliberately not in that tuple. Both are corrected
                    # rejections -- a trial the model DID answer for, unlike
                    # the four above, which is why neither is one of them. The
                    # Stage 5 normalizer stamps UNEVALUABLE_REJECTION_UNSUPPORTED
                    # on a rejection that cited no disqualifying criterion at
                    # all, and UNEVALUABLE_REMAP_NO_SURVIVOR on one whose
                    # disqualifying labels were out of vocabulary and did not
                    # survive normalisation. Projecting them is right: they are
                    # per-verdict outcomes, so a replay that stopped correcting
                    # one of these would diff.
                    "not_evaluable_reason": e.get("not_evaluable_reason"),
                    # --- The composed assessment and the draft it was composed
                    # from, both copied as stored (schema v8) ----------------
                    #
                    # `assessment` is what a reader of this trial's verdict is
                    # shown, and since PROMPT_VERSION 1.5.0 it is COMPOSED by
                    # this pipeline from that trial's own criterion /
                    # patient_value / status rows rather than taken from the
                    # model, so that it cannot assert anything the arrays do
                    # not carry. `assessment_draft` is the model's own prose,
                    # snapshotted before any validator touched it and kept in
                    # memory only -- there is no database column for it, so
                    # this fixture is the only durable record of it anywhere.
                    #
                    # BOTH, because only the pair is diagnostic. The draft
                    # alone moves whenever the model's prose moves, which
                    # `recordings.chat_completions` already pins; the composed
                    # value alone cannot separate "the renderer changed" from
                    # "the draft it renders changed". Projecting neither -- the
                    # pre-v8 state -- left a revert of compose_assessment() to
                    # returning the draft, or to returning "", replaying clean
                    # on all twelve.
                    #
                    # Copied as stored and diffed with exact equality: a
                    # not_evaluable trial's arrays are empty by contract, so
                    # its assessment is the model's text or one of the fixed
                    # strings _not_evaluable_entry() writes, and both are
                    # deterministic given the recordings. Neither is hashed --
                    # a digest of a composed sentence names no field when it
                    # moves, and the whole point of the diff is that it names
                    # the leaf.
                    "assessment": e.get("assessment"),
                    "assessment_draft": e.get("assessment_draft"),
                    # --- Where this pipeline saw this entry (schema v8) ------
                    #
                    # `emission_index` is the 0-based position the entry held
                    # in the array THAT CALL returned; `call_index` is the
                    # 1-based ordinal of the billed call that returned it,
                    # numbered to agree with `llm_classifier_call_details`.
                    # Both are None -- never 0 -- on an entry this node
                    # CONSTRUCTED (a truncation floor, an exhausted split
                    # budget, a model omission, conflicting duplicates), which
                    # never stood in a response: 0 would name the first entry
                    # of the first call, a real place some other trial
                    # occupies. Stored as-is, so None never compares equal to
                    # 0 here either.
                    #
                    # They are stamped on the FULL parsed list before the first
                    # drop and survive every later filter AND the sort, so they
                    # are the only surviving record of the order the model
                    # actually emitted in. That is precisely what a stamp moved
                    # to after the sort would destroy -- it would report this
                    # pipeline's own ranking back as the model's order, with
                    # every count, score and verdict unchanged, so nothing else
                    # in this prefix would move.
                    "emission_index": e.get("emission_index"),
                    "call_index": e.get("call_index"),
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
    return os.path.join(root or fixture_root(), f"{fixture_id}{FIXTURE_SUFFIX}")


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
            f"{os.path.basename(path)}: RE-CAPTURE REQUIRED. This fixture was "
            f"written at schema_version {version!r}; this code reads version "
            f"{SCHEMA_VERSION}. Re-capture with `python fixture_capture.py` "
            f"rather than diffing across versions — a field whose name or "
            f"meaning changed reads as absent and compares equal for the "
            f"wrong reason. (v3 -> v4 renamed the nine gpt4o_* recorded "
            f"fields to llm_classifier_*; v4 -> v5 put response_format inside "
            f"every request digest; v5 -> v6 added the two stage3 MedCPT score "
            f"lists and the two stage5 prompt-template identifiers, and "
            f"renamed the combined system+user hash to "
            f"llm_classifier_combined_prompt_sha256; v6 -> v7 records that "
            f"combined hash as None rather than the sha256 of the empty string "
            f"when Stage 5 rendered no prompt; v7 -> v8 projects four more "
            f"fields onto every stage5 verdict — the composed assessment, the "
            f"model's draft of it, and the two emission-provenance stamps "
            f"emission_index and call_index.)"
        )
    return fixture


# The fixtures whose Stage 5 recordings were COPIED from another fixture rather
# than produced by their own run. Counting them double-bills.
#
# THE TEST IS `construction.derived_from`, NOT `fixture_kind == "constructed"`,
# and the difference is most of the bill. Five of the twelve fixtures are
# `constructed`, and FOUR of those are real, live, billed runs on a derived
# INPUT -- mcode_genomic_variant, mesh_fallback_siteless_code,
# no_candidates_pediatric_age and truncation_split each made their own Stage 5
# calls. Only build_constructed_retry_fixture() copies another fixture's
# recordings, and it is the only writer that sets `construction.derived_from`
# (a FIXTURE id); the derived-input fixtures set `derived_from_bundle`
# (a BUNDLE filename) instead. Measured on the v6 set: excluding by
# `fixture_kind` would have reported $0.53 of a $1.14 run.
def _recordings_are_copied(fixture: Dict) -> bool:
    construction = fixture.get("construction")
    return bool(isinstance(construction, dict) and construction.get("derived_from"))


def stage5_cost_summary(fixtures: List[Dict]) -> Dict:
    """What the Stage 5 calls in these fixtures cost, priced by recorded model.

    Pure: reads the passed fixtures, calls ``get_model_cost`` and nothing else.
    No network, no client, no config beyond the price table that function reads.

    Tokens come from each recorded call's own ``response.usage`` and the model
    from that call's own ``response.model`` -- what the API said it charged for,
    not what config asks for today. A capture that ran while MATCHING_MODEL was
    being changed prices each call against the model that answered it.

    ``reasoning_tokens`` are deliberately NOT added: ``completion_tokens``
    already includes them, and the evaluation module's comment records that
    adding them bills every reasoning token twice.

    An unpriced model does not raise here and does not become a zero. This is a
    terminal REPORT on a run whose money is already spent, so refusing to print
    a number would fail a capture that succeeded, and printing a silent zero is
    the defect item 38 removed from the cost query -- an unpriceable group
    contributing a real 0.0 that every aggregate absorbs. The unpriced models
    are named and ``cost_complete`` goes False, which is the one field a
    consumer asks before trusting the total.

    Returns a dict: cost_usd, cost_complete, calls_priced, calls_unpriced,
    input_tokens, output_tokens, models, unpriced_models, excluded_fixture_ids.
    """
    by_model = {}
    excluded = []
    unpriced = {}
    calls_priced = calls_unpriced = 0

    for fixture in fixtures:
        if _recordings_are_copied(fixture):
            excluded.append(fixture.get("fixture_id"))
            continue
        for call in (fixture.get("recordings") or {}).get("chat_completions") or []:
            response = call.get("response") or {}
            usage = response.get("usage") or {}
            model = response.get("model")
            entry = by_model.setdefault(model, {"input": 0, "output": 0, "calls": 0})
            entry["input"] += usage.get("prompt_tokens") or 0
            entry["output"] += usage.get("completion_tokens") or 0
            entry["calls"] += 1

    cost = 0.0
    for model, entry in by_model.items():
        try:
            cost += get_model_cost(model, entry["input"], entry["output"])
            calls_priced += entry["calls"]
        except UnknownModelPricingError:
            unpriced[model] = entry
            calls_unpriced += entry["calls"]

    return {
        "cost_usd": cost,
        "cost_complete": not unpriced,
        "calls_priced": calls_priced,
        "calls_unpriced": calls_unpriced,
        "input_tokens": sum(e["input"] for e in by_model.values()),
        "output_tokens": sum(e["output"] for e in by_model.values()),
        "models": sorted(m for m in by_model if m is not None),
        "unpriced_models": sorted(str(m) for m in unpriced),
        "excluded_fixture_ids": sorted(x for x in excluded if x),
    }


def _fixture_cost_line(fixture: Dict) -> str:
    """What ONE fixture's Stage 5 calls cost, as one line, priced now.

    THE POINT IS THE MOMENT. The end-of-run summary is a total printed after
    every call has been billed, so an operator watching a capture that is
    pricing three times what they expected finds out when there is nothing left
    to stop. This is the same arithmetic -- ``stage5_cost_summary`` over a
    one-element list, never a second implementation -- printed as each fixture
    lands.

    ONE FIXTURE, SO EVERY PLURAL FIELD OF THE SUMMARY COLLAPSES, and each of
    the three collapsed cases is named rather than printed as a bare number:

      - copied recordings (the constructed retry fixture) price as $0 with the
        fixture in ``excluded_fixture_ids``. Printing "$0.00000" for it would
        say it was free when what is true is that its calls were already billed
        to the fixture it was copied from.
      - an unpriced model prices as $0 with ``cost_complete`` False. Same
        defect item 38 removed from the cost query: a real 0.0 that every
        aggregate absorbs.
      - a run that made no Stage 5 call at all -- the terminal
        ``node_no_candidates`` fixture -- genuinely costs nothing, and that is
        a measurement rather than a gap.
    """
    spend = stage5_cost_summary([fixture])
    if spend["excluded_fixture_ids"]:
        return ("Stage 5 cost: not billed here -- this fixture's recordings are "
                "copied from another fixture, which already paid for them")
    calls = spend["calls_priced"] + spend["calls_unpriced"]
    if not calls:
        return "Stage 5 cost: $0.00000 (no Stage 5 call was made)"
    line = (f"Stage 5 cost: ${spend['cost_usd']:.5f} over {calls} call(s), "
            f"{spend['input_tokens']:,} in / {spend['output_tokens']:,} out")
    if not spend["cost_complete"]:
        line += (f"   <- A FLOOR: {spend['calls_unpriced']} call(s) on unpriced "
                 f"model(s) {', '.join(spend['unpriced_models'])} contribute "
                 f"nothing")
    return line


def read_recorded_donor_bundle(path: str) -> str:
    """The donor bundle filename a fixture records, read WITHOUT the version gate.

    Returns the bundle filename, or None when the file does not exist, cannot be
    read, or records no donor. Raises nothing: every failure is None, and the
    caller decides what to print.

    WHY THIS BYPASSES ``load_fixture()``, AND WHY THAT IS SAFE HERE
    --------------------------------------------------------------
    ``load_fixture()`` refuses any schema mismatch before a field is read, which
    is right for a REPLAY input -- a renamed field read as absent compares equal
    to absent and the gate passes because it stopped looking. It is wrong for
    this read, and the cost was measured: the recovery below is what stops a
    re-capture repointing a derived fixture at a different patient, and it was
    reached through the gate, so every schema bump erased the donor memory of
    every derived fixture at exactly the moment it forced the re-capture. At the
    v6 capture that rebound four of twelve fixtures -- three whose recorded donor
    the gate hid, and ``truncation_split``, which had no memory at all.

    Two properties make the bypass safe, and both are about THIS field rather
    than about fixtures in general:

      - ``derivation.donor_bundle`` and ``construction.derived_from_bundle``
        have carried the same NAME and the same MEANING -- the filename of the
        cohort bundle the derived input was built from -- since schema v2. Every
        bump since has moved something else: v4 renamed nine stage5/environment
        keys, v5 put ``response_format`` inside the request digest, v6 added two
        stage3 lists and two stage5 identifiers and renamed one hash, v7 changed
        one hash's null convention, v8 added four fields to every stage5
        verdict. None of them touched either key.
      - The read is ADVISORY. Its only effect is which cohort patient the NEXT
        capture derives from. It never enters a deterministic prefix, is never
        compared on replay, and is never stored as a recorded value. A wrong or
        stale answer costs a donor search, which is what happens anyway when it
        returns None -- so the failure mode of trusting an old file here is the
        failure mode of not reading it at all.

    ``construction.derived_from_bundle`` is the fallback because
    ``truncation_split`` rewrites no bundle and therefore carries no
    ``derivation`` block at all; its donor is recorded only there. Fixtures whose
    ``construction`` names another FIXTURE rather than a bundle
    (``derived_from``) have no donor of their own and answer None.
    """
    if not os.path.exists(path):
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            recorded = json.load(fh)
    except (OSError, EOFError, json.JSONDecodeError, UnicodeDecodeError):
        # Corrupt or truncated. The caller warns and searches; raising here
        # would take down a capture over a file it is about to overwrite.
        return None
    if not isinstance(recorded, dict):
        return None
    for block, key in (("derivation", "donor_bundle"),
                       ("construction", "derived_from_bundle")):
        section = recorded.get(block)
        if isinstance(section, dict) and section.get(key):
            return section[key]
    return None


# How choose_pool_donor() answered. A CLOSED set, so a caller may branch on it
# exhaustively and a new member is a change every caller has to see rather than
# a string that silently falls through an if/elif chain. Same shape as
# agent.deps.RESOLUTION_STATES and agent.state.TRIAL_VERDICTS.
DONOR_FROM_MEMORY = "from_memory"      # the recorded donor was still available
DONOR_NO_MEMORY = "no_memory"          # nothing recorded a donor to reuse
DONOR_NOT_IN_POOL = "not_in_pool"      # recorded, but no longer selectable
DONOR_OUTCOMES = (DONOR_FROM_MEMORY, DONOR_NO_MEMORY, DONOR_NOT_IN_POOL)


def choose_pool_donor(recorded_bundle: str, donors: List[Dict]) -> tuple:
    """Pick the donor for a fixture that derives NOTHING from its bundle.

    Returns ``(donor, outcome)`` where outcome is one of ``DONOR_OUTCOMES``.
    ``donor`` is None for both fallback outcomes, and the caller is expected to
    take the next pool donor and to print WHY -- the reason is a fact about this
    run that the operator has to see, and printing it here would put I/O in a
    function whose whole value is being callable without a paid capture.

    ``truncation_split`` is the caller. It runs a real, unmodified cohort bundle
    and injects the truncation into the RESPONSE, so its donor has no recipe to
    satisfy -- the only predicate is the one the donor pool already encodes: an
    ordinary patient (MeSH resolved, stage known) that no other fixture in this
    run has taken.

    MEMBERSHIP IN THE REMAINING POOL, NOT IN THE COHORT, IS THE PREDICATE, and
    that is deliberate. A recorded donor this run has already selected as an
    ablation or a normal is NOT reusable: two fixtures on one patient would
    differ only by the injected truncation, and the retrieval half of both is
    then the same run recorded twice -- the same argument the ablation selection
    makes for taking a distinct patient per config.

    THE ONE SIDE EFFECT IS THE POINT: a reused donor is POPPED from ``donors``,
    which is what stops a later ``_next_donor()`` handing the same patient out a
    second time. The list is mutated in place, so the caller's pool is the same
    object this function shortens. Nothing else is touched -- no file is read
    (the fixture read is ``read_recorded_donor_bundle()`` at the call site), no
    path resolved, nothing printed.
    """
    if not recorded_bundle:
        return None, DONOR_NO_MEMORY
    for index, candidate in enumerate(donors):
        # candidate["bundle"], not .get("bundle"): a pool row without a bundle
        # name is a malformed selection, and the loud KeyError this raises is
        # what the nested version raised. `.get` would skip such a row silently
        # and fall through to "recorded donor no longer available" -- a wrong
        # diagnosis for a broken pool, and a donor repointed on the strength of
        # it.
        if candidate["bundle"] == recorded_bundle:
            return donors.pop(index), DONOR_FROM_MEMORY
    return None, DONOR_NOT_IN_POOL


def list_fixtures(root: str = None) -> List[str]:
    """Every fixture in the directory.

    Matches on FIXTURE_SUFFIX rather than on "*.json", so the plain-JSON index
    is excluded structurally instead of by name. Schema v1 wrote derived FHIR
    bundles here too, which had to be filtered out by a second suffix; v2
    stores a recipe instead and writes no bundles at all.
    """
    root = root or fixture_root()
    return sorted(glob.glob(os.path.join(root, "*" + FIXTURE_SUFFIX)))


#------------------------------------------------------------------------------


# ===========================================================================
# THE --resume GATE
# ===========================================================================

# How resume_decision() answered. A CLOSED set, for the same reason
# DONOR_OUTCOMES and agent.state.TRIAL_VERDICTS are closed: a caller may branch
# on it exhaustively, and a new member is a change every caller has to see
# rather than a string that falls silently through an if/elif chain.
#
# EVERY MEMBER BUT THE FIRST IS A RE-CAPTURE, and each names the one check that
# refused. There is no "skip on a doubt" member and there deliberately is no
# "skip because the file is there" member: a fixture counted as done because a
# file of that name exists is the exact defect every version gate in this file
# was written to refuse, and a resume that reintroduces it would do so at the
# one moment nobody is looking -- after a crash, while re-running.
RESUME_CURRENT = "current"                       # every check passed: SKIP
RESUME_ABSENT = "absent"                         # nothing on disk
RESUME_UNREADABLE = "unreadable"                 # load_fixture refused it
RESUME_PROMPT_VERSION = "prompt_version"         # a different prompt built it
RESUME_MATCHING_MODEL = "matching_model"         # a different model answered it
RESUME_COLLECTION = "qdrant_collection"          # a different collection served it
RESUME_COLLECTION_DIGEST = "collection_digest"   # the same collection, different contents
RESUME_OUTCOMES = (RESUME_CURRENT, RESUME_ABSENT, RESUME_UNREADABLE,
                   RESUME_PROMPT_VERSION, RESUME_MATCHING_MODEL,
                   RESUME_COLLECTION, RESUME_COLLECTION_DIGEST)


def _fixture_prompt_version(fixture: Dict):
    """The PROMPT_VERSION recorded in a fixture's deterministic prefix.

    Read from the PREFIX rather than from the environment block, and that is
    forced rather than preferred: ``build_environment_block()`` does not carry
    the prompt version at all. There is an open ledger item to put it there;
    until it lands, the prefix is where the value exists, and reading it from
    the place it exists is what makes this gate checkable today rather than
    after that item.

    ``_pipeline_provenance`` falls the version back to PROMPT_VERSION on every
    path including the two terminal nodes, so this is a string on every fixture
    the current writer produces. ``None`` comes back for a fixture whose prefix
    predates the field, and the caller treats that as a mismatch rather than as
    a pass -- an absent value is not evidence of agreement.
    """
    prefix = fixture.get("deterministic_prefix")
    if not isinstance(prefix, dict):
        return None
    stage5 = prefix.get("stage5")
    if not isinstance(stage5, dict):
        return None
    return stage5.get("llm_classifier_prompt_version")


def resume_decision(fixture_id: str,
                    environment: Dict,
                    root: str = None,
                    prompt_version: str = None,
                    load=None) -> tuple:
    """Whether ``--resume`` may skip this fixture. ``(skip, outcome, detail)``.

    ``outcome`` is one of ``RESUME_OUTCOMES``; ``skip`` is True for exactly
    ``RESUME_CURRENT``. ``detail`` names every check that refused, not only the
    one in ``outcome``, because an operator reading a re-capture wants the whole
    disagreement rather than the first line of it.

    A FIXTURE IS SKIPPED ONLY IF IT WOULD BE CAPTURED THE SAME WAY TODAY, and
    the four checks are the four things this file already knows can change the
    answer without changing this file:

      - the PROMPT VERSION that rendered the Stage 5 request. It is the one
        input to a fixture that is neither a tunable nor a model nor an index,
        and a prompt edit moves every verdict.
      - the MATCHING MODEL that was asked. Note this is the model REQUESTED
        (``environment.matching_model``), which is what a re-capture would ask
        for; what answered is recorded per call and can legitimately be a dated
        snapshot of the same alias.
      - the QDRANT COLLECTION NAME, and
      - the COLLECTION DIGEST -- what is IN it. The name alone is not enough:
        ``11- RAG Trial Indexer.py --mode direct`` rebuilds in place, so a
        collection can keep its name and change every point in it. This is the
        same pair ``fixture_replay.py`` refuses on, in the same order, and for
        the same reason.

    THE DIGEST CHECK IS ALSO WHAT MAKES THE DONOR POOL DETERMINISTIC under
    resume, which is a second job it does for free: the one skip that could
    otherwise consume donors is ``no_candidates_pediatric_age``, whose recorded
    donor is re-probed against the live index and falls into a 60-donor search
    when the probe no longer empties the pool. A skipped fixture is by
    definition one whose index is byte-identical to the one it was captured
    against, so that probe would have succeeded and consumed nothing.

    WHAT IS DELIBERATELY NOT CHECKED, and why each is safe to leave out. The
    tunables block: ``fixture_replay.py:diff_tunables()`` already reports a
    tunable change on every replay, and a run that changed one is a run whose
    whole set is being re-captured on purpose rather than resumed. The
    cross-encoder and BM25 model names: neither is compared by the replay, so a
    change to either cannot make a fixture on disk disagree with one captured
    today. The snapshot and age reference dates: both are pinned constants that
    a re-capture of the remaining fixtures would carry identically, and both
    already ride in the environment block of every file written.

    PURE, AND CALLABLE WITHOUT A PAID CAPTURE. It reads the filesystem and
    nothing else -- no client, no model, no network. ``load`` is the seam: it
    defaults to ``load_fixture`` (version gate included, which is what turns a
    stale-schema file into ``RESUME_UNREADABLE`` rather than into a skip), and a
    test hands it a stand-in. ``prompt_version`` defaults to the live
    ``PROMPT_VERSION``.
    """
    loader = load if load is not None else load_fixture
    expected_version = (prompt_version if prompt_version is not None
                        else PROMPT_VERSION)
    path = fixture_path(fixture_id, root)

    if not os.path.exists(path):
        return False, RESUME_ABSENT, "no fixture of that id on disk"

    try:
        fixture = loader(path)
    except Exception as exc:                                  # noqa: BLE001
        # EVERY failure to read is a re-capture, never a crash and never a
        # skip. load_fixture raises ValueError on a schema mismatch, and gzip
        # or json raise their own on a file a killed capture left half-written
        # -- which is precisely the file a resumed run meets first.
        return False, RESUME_UNREADABLE, f"{type(exc).__name__}: {exc}"[:300]

    recorded_env = fixture.get("environment")
    if not isinstance(recorded_env, dict):
        recorded_env = {}

    failures = []
    found_version = _fixture_prompt_version(fixture)
    if found_version != expected_version:
        failures.append((RESUME_PROMPT_VERSION,
                         f"prompt_version {found_version!r} != {expected_version!r}"))

    found_model = recorded_env.get("matching_model")
    if found_model != environment.get("matching_model"):
        failures.append((RESUME_MATCHING_MODEL,
                         f"matching_model {found_model!r} != "
                         f"{environment.get('matching_model')!r}"))

    found_collection = recorded_env.get("qdrant_collection")
    if found_collection != environment.get("qdrant_collection"):
        failures.append((RESUME_COLLECTION,
                         f"qdrant_collection {found_collection!r} != "
                         f"{environment.get('qdrant_collection')!r}"))

    # COMPARED AS A WHOLE OBJECT, not by one of its keys. The digest carries a
    # point count, a distinct-NCT-id count and a sha256 over the ids; comparing
    # only the sha256 would pass a collection whose ids are the same and whose
    # point count is not, which is what a partially-failed re-index looks like.
    found_digest = recorded_env.get("collection_digest")
    if found_digest != environment.get("collection_digest"):
        failures.append((RESUME_COLLECTION_DIGEST,
                         f"collection_digest differs "
                         f"({_digest_brief(found_digest)} != "
                         f"{_digest_brief(environment.get('collection_digest'))})"))

    if not failures:
        return True, RESUME_CURRENT, (
            f"prompt {expected_version}, model "
            f"{environment.get('matching_model')}, collection "
            f"{environment.get('qdrant_collection')}, digest "
            f"{_digest_brief(environment.get('collection_digest'))}")
    return False, failures[0][0], "; ".join(detail for _, detail in failures)


def _digest_brief(digest) -> str:
    """A collection digest in one short line, for a diagnostic.

    ``None`` and a non-dict both answer a named string rather than raising:
    this is only ever called to explain a mismatch, and a formatter that raises
    while formatting the reason a run is re-capturing would replace the
    diagnosis with a traceback.
    """
    if not isinstance(digest, dict):
        return "<none>" if digest is None else f"<{type(digest).__name__}>"
    return (f"{digest.get('point_count')}pts/"
            f"{digest.get('distinct_nct_ids')}ncts/"
            f"{str(digest.get('nct_id_sha256'))[:12]}")


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
        return config.get_qdrant_client().scroll(
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
        config.get_qdrant_client().get_collection(resolved)
    except Exception as exc:
        raise RuntimeError(
            f"Cannot pin Qdrant collection: get_collection({resolved!r}) failed "
            f"({type(exc).__name__}: {exc}). Refusing to write a fixture whose "
            f"pinned index cannot be confirmed to exist."
        )

    if not alias_resolved:
        console.out(f"  WARNING: '{COLLECTION_NAME}' did not resolve through an alias. "
              f"Pinning the alias name itself; a future alias swap will make "
              f"this fixture's retrieval diff meaningless rather than failing "
              f"the collection check.")

    return resolved, alias_resolved


def build_environment_block() -> Dict:
    resolved, alias_resolved = _resolve_and_verify_collection()
    digest, elapsed = compute_collection_digest(resolved)
    console.out(f"  Collection digest: {digest['point_count']} points, "
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
        # BOTH WERE LITERALS UNTIL PASS 20f-2, and both are now the constants
        # the pipeline actually loads: config.CROSS_ENCODER_MODEL is what
        # oncotriage/agent/deps.py hands from_pretrained, and
        # BM25_SPARSE_MODEL_NAME is what oncotriage/embedding.py hands
        # SparseTextEmbedding at the one construction site.
        #
        # THE HAZARD WAS SPECIFIC TO A FIXTURE. These two fields are the record
        # of which models produced the recorded scores, and a fixture is read
        # back months later to explain a diff. A literal here goes on saying
        # "MedCPT" after the loader has moved on, so the artifact that exists to
        # say what ran would be the one thing in the run that could not.
        #
        # NEITHER FIELD IS COMPARED BY THE REPLAY, measured rather than assumed:
        # oncotriage/fixtures/replay.py touches fixture["environment"] on five
        # lines and reads exactly three keys out of it -- "tunables"
        # (diff_tunables), "qdrant_collection" (the pinned-name refusal) and
        # "collection_digest" (the contents refusal). So changing how these two
        # values are PRODUCED cannot move a fixture byte, and the twelve
        # fixtures on disk replay clean without recapture. Recording a field
        # nothing compares is still worth doing -- it is the provenance a human
        # reads -- but it is why this edit was safe to make without spending.
        #
        # IMPORTING BM25_SPARSE_MODEL_NAME CONSTRUCTS NOTHING: oncotriage/
        # embedding.py holds the name at module level and does
        # `from fastembed import SparseTextEmbedding` INSIDE
        # get_bm25_sparse_model(). tests/test_package_invariants.py check 2f
        # counts construction SITES by ast and is unaffected by a name import;
        # the count is still exactly one, in embedding.py.
        "cross_encoder_model": CROSS_ENCODER_MODEL,
        "sparse_model": BM25_SPARSE_MODEL_NAME,
        # The File 03 constants the prefix is a function of. A diff caused by
        # editing one of these is a configuration change, not a refactor
        # regression, and without them recorded the two are indistinguishable.
        "tunables": {
            "BM25_RETRIEVAL_SIZE": BM25_RETRIEVAL_SIZE,
            "VECTOR_RETRIEVAL_SIZE": VECTOR_RETRIEVAL_SIZE,
            "RRF_POOL_SIZE": RRF_POOL_SIZE,
            # The RRF fusion constants themselves, per this dict's own
            # doctrine: only what is recorded HERE is compared by File 46's
            # diff_tunables(), so a fusion-weight edit would otherwise move
            # every Stage 2 pool and be reported as a prefix difference with no
            # cause attached -- an afternoon spent hunting a refactor bug for a
            # one-line config change. RRF_K is one entry, not two, because both
            # fusion sites read the one constant.
            #
            # FUTURE CAPTURES ONLY. diff_tunables() iterates the keys the
            # FIXTURE recorded, not the keys this dict declares, so adding
            # these cannot move, invalidate or re-report any of the twelve
            # fixtures already on disk -- they replay clean, unchanged and
            # without recapture. The five entries begin describing captures
            # taken after this change.
            "RRF_K": RRF_K,
            "RRF_WEIGHT_TITLE": RRF_WEIGHT_TITLE,
            "RRF_WEIGHT_CONDITIONS": RRF_WEIGHT_CONDITIONS,
            "RRF_WEIGHT_CRITERIA": RRF_WEIGHT_CRITERIA,
            "RRF_WEIGHT_DENSE": RRF_WEIGHT_DENSE,
            # The cross-encoder's sequence limit, on this dict's own doctrine
            # and for the same reason the five RRF entries above are here: it
            # decides how much of every trial text Stage 3 actually reads, so
            # editing it changes every ranking -- and every tokenizer call
            # passes truncation=True, so nothing anywhere would raise. Without
            # it recorded, that edit reaches a replay as an unexplained
            # cross_encoder difference with no cause attached.
            #
            # FUTURE CAPTURES ONLY, exactly as the RRF block above records:
            # diff_tunables() iterates the keys the FIXTURE recorded, not the
            # keys this dict declares, so adding this cannot move, invalidate
            # or re-report any fixture already on disk.
            "CROSS_ENCODER_MAX_LENGTH": CROSS_ENCODER_MAX_LENGTH,
            "TOP_K_CANDIDATES": TOP_K_CANDIDATES,
            "MAX_TRIALS_FOR_EVALUATION": MAX_TRIALS_FOR_EVALUATION,
            # Was RERANK_SCORE_THRESHOLD, a floor on the fused RRF score that
            # could never fire. A fixture captured before this change records
            # the old name, and File 46's diff_tunables() will report it as
            # "<no longer defined>" -- which is the correct finding, not a
            # harness fault: the tunable that shaped that capture is gone.
            "MEDCPT_SCORE_FLOOR": MEDCPT_SCORE_FLOOR,
            "QUALITY_THRESHOLD_PERCENTILE": QUALITY_THRESHOLD_PERCENTILE,
            "MESH_BOOST_DIRECT_FRACTION": MESH_BOOST_DIRECT_FRACTION,
            "MESH_BOOST_PAN_FRACTION": MESH_BOOST_PAN_FRACTION,
            "MESH_BOOST_DIRECT_FLOOR": MESH_BOOST_DIRECT_FLOOR,
            "MESH_BOOST_PAN_FLOOR": MESH_BOOST_PAN_FLOOR,
            "MAX_LLM_CLASSIFIER_RETRIES": MAX_LLM_CLASSIFIER_RETRIES,
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
    #
    # THE ERROR-HANDLER CASE IS NAMED (pass 20f-3), and it is the reason
    # TERMINAL_ERROR exists as a constant at all. Before this pass the three
    # terminal nodes were a closed vocabulary of which only two were read here,
    # and a run that ended at node_error_handler fell into one of two shapes:
    #
    #   n_chat == 0  -- refused by the `elif` below, with the message "no Stage 5
    #                   request/response pair was recorded". True, and the least
    #                   useful true thing available: the fixture is unusable
    #                   because the RUN FAILED, not because a recording hook
    #                   missed an exchange, and those two have completely
    #                   different fixes.
    #   n_chat >= 1  -- an exception thrown AFTER Stage 5 answered. Nothing here
    #                   said a word, and the fixture was written. A replay would
    #                   then diff against a prefix stamped by the error handler,
    #                   whose funnel counters and verdict lists are the
    #                   handler's own placeholders rather than the pipeline's
    #                   output.
    #
    # THAT SECOND SHAPE IS AN OUTCOME CHANGE AND IS STATED AS ONE: a fixture
    # that used to be written is now refused. It is the right way round --
    # capture_fixture() exists to record a run a replay can be diffed against,
    # and a run the pipeline itself declared failed is not one -- and it is
    # unreachable by the twelve shipped fixtures, every one of which ends at
    # node_finalize or node_no_candidates (verify_case_coverage() asserts the
    # first for every `normal` label and the second for CASE_NO_CANDIDATES).
    # Nothing on disk changes, which is why the byte-identity proof for this
    # pass is about the WRITER and not about this branch.
    n_chat = len(sink.chat_completions)
    if terminal == TERMINAL_NO_CANDIDATES:
        if n_chat:
            problems.append(
                f"run ended at node_no_candidates but {n_chat} Stage 5 call(s) "
                f"were made"
            )
    elif terminal == TERMINAL_ERROR:
        problems.append(
            f"the run ended at {TERMINAL_ERROR}: the pipeline raised and the "
            f"error handler produced the result, so this fixture records a "
            f"FAILURE rather than a baseline ({n_chat} Stage 5 call(s) were "
            f"recorded before it). Fix the run, then capture again."
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
    _reported = prefix["stage5"].get("llm_classifier_calls_reported")
    _retries = prefix["stage5"].get("llm_classifier_retries") or 0
    if _reported is not None and not _retries and _reported != n_chat:
        problems.append(
            f"Stage 5 reported {_reported} call(s) but {n_chat} were recorded"
        )
    elif _reported is not None and _retries and n_chat < _reported:
        problems.append(
            f"Stage 5 reported {_reported} call(s) on its final attempt but "
            f"only {n_chat} exchange(s) were recorded in total"
        )
    if (prefix["stage5"].get("llm_classifier_truncation_splits") or 0) and n_chat < 2:
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
    console.out(f"\n{'#' * 78}\n# CAPTURE {fixture_id}  [{', '.join(case_labels)}]\n{'#' * 78}")

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
        # Scoped for the same reason match_patient_to_trials() scopes: this is
        # one patient's run, and a capture whose log lines carry the "-"
        # sentinel cannot be read back against the fixture it produced.
        with correlation_scope():
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
            # For a cohort fixture this is the bundle in paths.data_fhir_path. For a
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
            if str(coding.get("code")) not in deps.get_cancer_registry().snomed_primary:
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
    donor_path = os.path.join(paths.data_fhir_path, derivation["donor_bundle"])
    if not os.path.exists(donor_path):
        raise FileNotFoundError(
            f"{fixture['fixture_id']}: donor bundle "
            f"{derivation['donor_bundle']} is not in {paths.data_fhir_path}. The "
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
MALFORMED_MARKER = "  <<< TRUNCATED BY fixture_capture.py TO FORCE A JSON PARSE FAILURE"

# How much of the real response to keep before truncating. Long enough that the
# payload is recognisably the real one, short enough that it cannot close.
MALFORMED_PREFIX_CHARS = 400

# The base this fixture has historically been built from. It is a PREFERENCE,
# not a requirement -- see choose_retry_base() for why the difference matters.
RETRY_BASE_PREFERRED = "normal_1"

# Closed vocabulary for choose_retry_base()'s second return value, so a caller
# can branch on the outcome instead of matching on prose.
RETRY_BASE_PREFERRED_OK = "preferred"      # the historical base still qualifies
RETRY_BASE_SUBSTITUTED = "substituted"     # it did not; another normal run did
RETRY_BASE_NONE = "none"                   # nothing in the set qualifies
RETRY_BASE_OUTCOMES = (RETRY_BASE_PREFERRED_OK, RETRY_BASE_SUBSTITUTED,
                       RETRY_BASE_NONE)


def retry_base_disqualification(fixture: Dict) -> str:
    """Why this fixture cannot be the retry fixture's base, or "" if it can.

    Every condition here is one build_constructed_retry_fixture() ALREADY
    refuses, or one the constructed fixture's own labelling depends on. Read it
    as the machine-checkable statement of that function's preconditions,
    evaluated before a base is chosen rather than after.
    """
    if fixture.get("fixture_kind") != FIXTURE_KIND_RECORDED:
        return f"fixture_kind is {fixture.get('fixture_kind')!r}, not recorded"

    calls = (fixture.get("recordings") or {}).get("chat_completions") or []
    if len(calls) != 1:
        # THE ONE THAT ACTUALLY FIRED. A Stage 5 run splits when its response
        # hits the output ceiling, and whether it does is a property of the
        # model and of that patient's filtered trial set on the day -- not of
        # the patient. So no fixture id is a durable answer to "which run made
        # exactly one call", which is precisely why this is measured.
        return (f"recorded {len(calls)} Stage 5 call(s), not exactly one to "
                f"splice a failing attempt in front of")

    if list(fixture.get("case_labels") or []) != [CASE_NORMAL]:
        # The constructed fixture stamps case_labels = [the retry case] and
        # deepcopies everything else from its base. A base carrying another
        # branch case therefore produces a fixture that exercises two branches
        # while declaring one -- an ablation base would carry ablation_flags
        # into a fixture whose labels say nothing about ablation.
        return (f"case_labels are {fixture.get('case_labels')}, not "
                f"[{CASE_NORMAL!r}]; the retry case must not be spliced onto "
                f"another branch case")

    return ""


def choose_retry_base(fixtures: List[Dict]) -> tuple:
    """Pick the recorded run the constructed retry fixture is spliced onto.

    Returns ``(fixture_or_None, outcome, detail)`` where ``outcome`` is one of
    RETRY_BASE_OUTCOMES and ``detail`` is a human-readable line naming what was
    chosen and what was rejected.

    WHY THIS EXISTS RATHER THAN A HARDCODED ``normal_1``
    ----------------------------------------------------
    build_constructed_retry_fixture() REQUIRES a shape -- one recorded Stage 5
    call -- while main() used to select by NAME. Those are different questions,
    and a capture measured the gap: ``normal_1`` came back having SPLIT into two
    calls (its filtered trial set hit the model's output ceiling), the
    constructor refused it, and eleven paid fixtures were written while the
    twelfth was left stale on disk at the previous schema version. Splitting is
    not a defect and not flakiness -- it is what the truncation path is for --
    so no fixture id is a durable answer to "which run made exactly one call".

    Selection is deterministic and it is ORDERED rather than arbitrary:

      1. RETRY_BASE_PREFERRED, when it qualifies. Keeping the historical base
         whenever it is usable is what stops the constructed fixture wandering
         between patients on captures where nothing was wrong.
      2. Otherwise the qualifying candidates sorted by fixture_id, first one
         wins. Sorted, so two captures over the same set agree.

    Returning None rather than raising is deliberate: the caller has already
    spent money on eleven fixtures by the time this runs, and the useful thing
    to do with that is report precisely what was rejected and why.
    """
    rejected = []
    qualified = {}
    for fixture in fixtures:
        fixture_id = fixture.get("fixture_id")
        reason = retry_base_disqualification(fixture)
        if reason:
            rejected.append(f"{fixture_id}: {reason}")
        else:
            qualified[fixture_id] = fixture

    if RETRY_BASE_PREFERRED in qualified:
        return (qualified[RETRY_BASE_PREFERRED], RETRY_BASE_PREFERRED_OK,
                f"{RETRY_BASE_PREFERRED} qualifies (one recorded Stage 5 call)")

    if qualified:
        chosen_id = sorted(qualified)[0]
        why_not = next((r for r in rejected
                        if r.startswith(f"{RETRY_BASE_PREFERRED}:")),
                       f"{RETRY_BASE_PREFERRED}: not in the captured set")
        return (qualified[chosen_id], RETRY_BASE_SUBSTITUTED,
                f"{chosen_id} substituted for {RETRY_BASE_PREFERRED} — {why_not}")

    return (None, RETRY_BASE_NONE,
            "no captured fixture qualifies as a retry base; rejected "
            + "; ".join(rejected or ["nothing was offered"]))


def build_constructed_retry_fixture(base: Dict, fixture_id: str) -> Dict:
    """Assemble the MAX_LLM_CLASSIFIER_RETRIES fixture from a recorded normal run.

    The retry loop cannot be found by scanning the cohort: it fires on a
    malformed model response, which is a property of the model on the day, not
    of any patient. So this fixture is built rather than observed — a real
    recorded run with one extra Stage 5 response spliced in FRONT of the real
    one, truncated so json.loads raises.

    On replay: attempt 1 parses the truncated payload and fails, Stage 5
    returns llm_classifier_retries=1 with an error, route_after_llm_classifier sends it back
    round the cyclic edge (1 < MAX_LLM_CLASSIFIER_RETRIES), attempt 2 receives the real
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
    # Usage is read BEFORE the JSON parse in node_llm_classifier_evaluation, so the
    # failing attempt still needs a usage block. The real one is reused: the
    # attempt returns before those numbers reach the result.
    real_call["call_index"] = 1

    fixture["fixture_id"] = fixture_id
    fixture["fixture_kind"] = FIXTURE_KIND_CONSTRUCTED
    fixture["case_labels"] = [CASE_LLM_CLASSIFIER_PARSE_RETRY]
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
    stage5["llm_classifier_calls"] = 2
    stage5["llm_classifier_retries"] = 1
    # Both responses claim to have finished normally. That is what a parse
    # failure IS: the model stopped of its own accord and produced something
    # that does not parse. "length" would make it a truncation, which since
    # item 19c is a different mechanism with a different budget.
    stage5["finish_reasons"] = ["stop", "stop"]
    # THE THREE PROMPT IDENTIFIERS ARE DELIBERATELY NOT TOUCHED, and that is a
    # statement about the case rather than an omission. The retry re-enters
    # node_llm_classifier_evaluation with the same patient and the same
    # filtered set, so attempt 2 renders the same system prompt and the same
    # user message as attempt 1: llm_classifier_prompt_version,
    # llm_classifier_prompt_sha256 and llm_classifier_combined_prompt_sha256
    # all carry through the deepcopy unchanged, which is the same reason
    # request_sha256_by_call above is the base digest REPEATED rather than a
    # second, different one.
    #
    # The key SET is asserted rather than assumed. Every edit in this function
    # is a subscript assignment, and a subscript assignment against a key name
    # that has moved does not raise -- it silently ADDS a key the replay will
    # then diff against a fixture that has no such field. That is exactly what
    # the v6 rename of llm_classifier_prompt_sha256 could have done here, and
    # the only thing that catches it is comparing the two key sets.
    _base_stage5_keys = set(base["deterministic_prefix"]["stage5"])
    if set(stage5) != _base_stage5_keys:
        raise ValueError(
            "the constructed retry fixture's stage5 key set diverged from "
            f"{base['fixture_id']}'s: added "
            f"{sorted(set(stage5) - _base_stage5_keys)}, lost "
            f"{sorted(_base_stage5_keys - set(stage5))}. An edit above wrote a "
            "key name that build_deterministic_prefix no longer produces, so "
            "the field it meant to override is still at its base value and a "
            "replay would diff a field that exists on only one side."
        )
    # llm_classifier_calls_reported is left at the base value of 1 and that is correct,
    # not an oversight: it is what the SUCCESSFUL node invocation reports, and
    # Stage 5's own counter resets when the router re-enters the node. The
    # sink-side llm_classifier_calls of 2 spans both invocations. The two disagreeing is
    # the signature of a retry, and is why both are recorded.
    #
    # THE VERDICTS ARE DELIBERATELY NOT RENUMBERED, AND THAT FOLLOWS FROM THE
    # SAME RESET (schema v8). `call_index` on a verdict is Stage 5's own
    # `calls_made` at the moment the response parsed, and the failing attempt
    # is a SEPARATE node invocation reached round the cyclic edge -- so the
    # successful attempt starts from zero and stamps 1, exactly as the base run
    # did. Were the retry an inner loop instead, every verdict here would carry
    # 2 while this fixture claimed 1, and the miss would surface at replay as a
    # dozen unexplained per-verdict diffs rather than as a statement about the
    # router. The assumption is asserted rather than left to that: the base is
    # already required to have recorded exactly ONE Stage 5 call above, so the
    # only call_index a model-returned verdict may carry is 1, and a
    # pipeline-constructed one carries None.
    _verdict_calls = {v.get("call_index")
                      for v in stage5.get("verdicts", [])}
    if not _verdict_calls <= {1, None}:
        _unexpected = sorted(c for c in _verdict_calls if c not in (1, None))
        raise ValueError(
            f"{base['fixture_id']} recorded exactly one Stage 5 call, but its "
            f"verdicts carry call_index values {_unexpected}. "
            "A model-returned verdict of a single-call run can only be call 1 "
            "(the ledger numbers calls 1..N) and a pipeline-constructed one "
            "carries None. Either Stage 5's numbering moved or the retry stopped "
            "being a fresh node invocation; in both cases the constructed "
            "fixture's copied verdicts no longer describe what a replay will "
            "produce."
        )

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
    console.out(f"\n[Scan] Classifying {len(bundle_paths)} patient bundles "
          f"(no network calls)...")

    rows = []
    failures = 0

    for index, path in enumerate(bundle_paths, start=1):
        if index % 200 == 0:
            console.out(f"  ...{index}/{len(bundle_paths)}")
        try:
            patient_data = parse_fhir_bundle(path)
        except Exception as exc:
            # Counted, not swallowed: a cohort with unparseable bundles is a
            # fact about the corpus that the selection report has to state.
            failures += 1
            console.out(f"  WARNING: parse failed for {os.path.basename(path)}: "
                  f"{type(exc).__name__}: {exc}")
            continue

        conditions = patient_data.get("conditions") or []
        mesh_result = expand_query_from_mesh(conditions, deps.get_cancer_registry(), deps.get_mesh_filter())
        # Every argument Stage 4 passes, for the reason this function's
        # docstring gives: a patient classified here as CASE_UNKNOWN_STAGE has
        # to be one the pipeline will also find unstaged. Omitting the
        # metastasis list would leave the scan blind to the AJCC M tier and it
        # would label a cM1 patient "unknown stage" while Stage 4 resolved them
        # to IV — a fixture that fails verify_recording_complete() for a reason
        # that is in this file rather than in the run it recorded.
        stage = extract_patient_stage(
            conditions,
            cancer_stage_observations=patient_data.get("cancer_stage_observations") or [],
            cancer_metastasis_observations=patient_data.get("cancer_metastasis_observations") or [],
        )

        primary = "cancer"
        cancer_conditions = [
            c for c in conditions
            if (c.get("verification_status") or "unknown")
            not in deps.get_cancer_registry().exclude_verification
            and deps.get_cancer_registry().is_primary_cancer(c)
        ]
        if cancer_conditions:
            primary = sorted(cancer_conditions, key=deps.get_cancer_registry().sort_key)[0]["display"]

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

    console.out(f"[Scan] Parsed {len(rows)} bundle(s); {failures} failed.")
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
        patient_data.get("conditions", []), deps.get_cancer_registry(), deps.get_mesh_filter()
    )["trees"] if deps.get_mesh_filter() is not None else set()

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

    console.out(f"\n[Probe] Hunting for an empty candidate pool across "
          f"{len(probe_order)} patient(s), rarest cancer site first...")

    found = None
    for index, row in enumerate(probe_order, start=1):
        try:
            outcome = probe_empty_candidate_pool(row["path"])
        except Exception as exc:
            console.out(f"  WARNING: probe failed for {row['bundle']}: "
                  f"{type(exc).__name__}: {exc}")
            continue
        if index % 25 == 0 or outcome["empty_pool"]:
            console.out(f"  [{index}/{len(probe_order)}] {row['bundle'][:40]:<40} "
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
        console.out(f"[Probe] No patient in the probed {len(probe_order)} produced an "
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
    """Refuse to capture unless the production inference database is out of reach.

    FIVE CHECKS. Three of them are the non-degeneracy checks File 45 already
    carried; one is re-expressed for a module world because the thing it used to
    assert about no longer exists; one is unchanged.

    WHAT CEASED TO EXIST, AND WHY THAT IS THE DANGEROUS PART.
    ---------------------------------------------------------
    File 45's fourth check was::

        if inferences_path != FIXTURE_SCRATCH_DB:
            raise RuntimeError("... Something re-chained 14- Database Logger.py
                                after the redirect; refusing to run.")

    ``inferences_path`` was a name in the SHARED EXEC NAMESPACE, rebound to a
    scratch database before ``14- Database Logger.py`` was chained. A module has
    no such namespace, and there is no chain any more (see the block above the
    tripwire). A naive conversion has two ways to go wrong and both are silent:
    drop the check, or keep it against ``paths.inferences_path`` -- where it
    would compare the production path to a temp path, find them different, and
    PASS FOREVER while asserting nothing about isolation.

    So it is re-expressed as the module-world statement of the same fact. The
    danger the old check guarded against was "the real writer got back into the
    namespace this file's pipeline resolves names from". Here that is: some name
    in THIS MODULE's globals is bound to
    ``oncotriage.storage.database_logger.log_inference``. Check 4 scans for
    exactly that, under every alias, which is strictly stronger than the single
    identity test it replaces -- ``from ... import log_inference as _writer``
    fires it just as ``import log_inference`` does.

    The module DOES import ``oncotriage.storage.database_logger`` under the
    private alias ``_database_logger``, for ``resolve_inference_db_path`` alone.
    That module object is not the writer function and does not trip check 4; the
    alias is the one sanctioned route, nothing in this module calls through it
    except the three resolver checks below, and check 4's message says so.

    WHY THE THREE NON-DEGENERACY CHECKS COME FIRST. If the production path and
    the probe path were ever the same string, or if
    ``resolve_inference_db_path`` stopped distinguishing an explicit argument
    from its default, checks 4 and 5 would still pass and would be isolating
    nothing. They are asserted before the two they make meaningful.

    Nothing here opens a database. ``resolve_inference_db_path`` resolves and
    returns; it is safe to call on a machine holding a database this must not
    touch.
    """
    # --- 1 and 2: NON-DEGENERACY of the two paths being compared ------------
    _package_default = _database_logger.resolve_inference_db_path(None)
    if os.path.abspath(_package_default) != os.path.abspath(production_inferences_path()):
        raise RuntimeError(
            f"resolve_inference_db_path(None) is {_package_default!r}, not the "
            f"production database {production_inferences_path()!r}. The checks "
            f"below compare against the wrong thing; refusing to run."
        )
    if os.path.abspath(_package_default) == os.path.abspath(FIXTURE_SCRATCH_DB):
        raise RuntimeError(
            f"the production database and the scratch probe resolve to the same "
            f"path ({_package_default!r}), so no comparison below can "
            f"discriminate; refusing to run."
        )

    # --- 3: NON-DEGENERACY of the resolver itself ---------------------------
    if _database_logger.resolve_inference_db_path(FIXTURE_SCRATCH_DB) != FIXTURE_SCRATCH_DB:
        raise RuntimeError(
            "resolve_inference_db_path() does not honour an explicit db_path; "
            "refusing to run."
        )

    # --- 4: THE REAL WRITER IS NOT BOUND IN THIS MODULE, under any name -----
    # The module-world replacement for File 45's `inferences_path !=
    # FIXTURE_SCRATCH_DB`. Identity against the function object, scanned over
    # every global, so an alias does not escape it.
    _package_writer = _database_logger.log_inference
    _leaked = sorted(
        name for name, value in globals().items() if value is _package_writer
    )
    if _leaked:
        raise RuntimeError(
            f"oncotriage.storage.database_logger.log_inference is bound in this "
            f"module as {_leaked}. Capture must not be able to reach the real "
            f"inference writer: a row written from here would be "
            f"indistinguishable from a production inference. The only "
            f"sanctioned route is the module alias `_database_logger`, used for "
            f"resolve_inference_db_path and nothing else; refusing to run."
        )

    # --- 5: THE TRIPWIRE IS INTACT ------------------------------------------
    try:
        log_inference(None, None)
    except RuntimeError:
        pass
    else:
        raise RuntimeError(
            "log_inference did not raise. The tripwire was overwritten; "
            "refusing to run."
        )

    if os.path.exists(production_inferences_path()):
        console.out(f"  Production inferences.db left untouched "
              f"({os.path.getsize(production_inferences_path()) / 1024:.0f} KB).")
    console.out(f"  No database is opened by this run; the scratch path "
          f"{FIXTURE_SCRATCH_DB} is a comparison probe only.")



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
    parser.add_argument("--resume", action="store_true",
                        help="Skip a fixture that is already on disk AND was "
                             "captured under this run's prompt version, "
                             "matching model, Qdrant collection and collection "
                             "digest. Anything else is re-captured, with the "
                             "failing check printed. Composes with --only: an "
                             "entry runs only if it passes both filters.")
    args = parser.parse_args()

    root = args.fixture_dir or fixture_root()
    os.makedirs(root, exist_ok=True)

    _assert_database_is_isolated()

    console.out(f"\n{'=' * 78}")
    console.out(f"{Project_Name}: Characterization Fixture Capture (schema v{SCHEMA_VERSION})")
    console.out(f"{'=' * 78}")
    console.out(f"  Fixture directory: {root}")

    bundle_paths = sorted(glob.glob(paths.data_fhir_path + "*.json"))
    if not bundle_paths:
        console.out(f"[FATAL] No FHIR bundles found in {paths.data_fhir_path}")
        return 1
    console.out(f"  Cohort: {len(bundle_paths)} bundles\n")

    rows = scan_cohort(bundle_paths)
    if not rows:
        console.out("[FATAL] No bundle parsed successfully.")
        return 1

    # --- Cohort summary ----------------------------------------------------
    n_fallback = sum(1 for r in rows if r["expansion_path"] == EXPANSION_PATH_FALLBACK)
    n_unknown_stage = sum(1 for r in rows if r["stage"] is None)
    console.out(f"\n[Scan] {n_fallback} patient(s) take EXPANSION_PATH_FALLBACK; "
          f"{n_unknown_stage} have no determinable cancer stage.")

    selection = select_cases(rows, args.probe_limit)

    # --- Report the selection ----------------------------------------------
    console.out(f"\n{'-' * 78}\nCASE COVERAGE\n{'-' * 78}")
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

    # THE ENVIRONMENT BLOCK IS BUILT AT MOST ONCE AND ONLY WHEN SOMETHING NEEDS
    # IT. It was a straight-line call below `if args.scan_only: return 0`, and
    # --resume needs it EARLIER -- the derived fixtures decide whether to
    # derive while the plan is being built, and that decision reads the pinned
    # collection and its digest. Hoisting the call unconditionally would have
    # made --scan-only contact Qdrant for a digest it never uses, so it is
    # lazy instead: with neither --resume nor a capture, nothing calls this and
    # the behaviour is byte-for-byte what it was.
    #
    # ONCE, not once per caller: `compute_collection_digest` scrolls the whole
    # collection, and two readings of a live index taken minutes apart can
    # disagree -- which would put one collection's digest in the resume gate and
    # another's in the fixtures the same run writes.
    _environment_cache = {}

    def _environment() -> Dict:
        if "env" not in _environment_cache:
            _environment_cache["env"] = build_environment_block()
        return _environment_cache["env"]

    # What --resume decided, per fixture id, so the decision is made once and
    # can be read again afterwards. The retry-base selection below is the second
    # reader and it is not optional -- see the comment there.
    resume_skipped = []
    _resume_seen = {}

    def _resume_skip(fixture_id: str) -> bool:
        """Whether --resume may skip this fixture. Prints the reason either way.

        Memoized because the derived fixtures ask during plan building and the
        capture loop asks again: two calls reading the filesystem at two moments
        could answer differently, and the second answer would be applied to a
        bundle the first had already decided not to derive.
        """
        if not args.resume or args.scan_only:
            return False
        if fixture_id in _resume_seen:
            return _resume_seen[fixture_id]
        skip, outcome, detail = resume_decision(fixture_id, _environment(), root)
        _resume_seen[fixture_id] = skip
        if skip:
            resume_skipped.append(fixture_id)
            console.out(f"  [Resume] SKIP     {fixture_id:<38} current ({detail})")
        else:
            console.out(f"  [Resume] CAPTURE  {fixture_id:<38} {outcome}: {detail}")
        return skip

    def _wanted_now(fixture_id: str) -> bool:
        """--only AND --resume. An entry runs only if it passes both."""
        return _wanted(fixture_id) and not _resume_skip(fixture_id)

    def _recorded_donor(derived_id: str) -> Dict:
        """The donor an already-captured derived fixture was built from.

        Re-deriving from the recorded donor rather than searching again makes
        the bundle regenerable from the fixture: the fixture is the record, and
        running this file twice cannot silently repoint it at someone else.
        Returns None when there is no such fixture, or its donor is no longer
        in the cohort.

        The donor is read by ``read_recorded_donor_bundle()``, which does NOT go
        through ``load_fixture()``'s version gate. That is argued in full at
        that function; the short form is that this read is advisory and the key
        it reads has been stable since v2, while routing it through the gate
        erased the donor memory of every derived fixture on every schema bump.
        """
        path = fixture_path(derived_id, root)
        if not os.path.exists(path):
            return None
        bundle = read_recorded_donor_bundle(path)
        if not bundle:
            # The file is there and names no donor: unreadable, corrupt, or
            # written before the field existed. Not silent -- the donor is about
            # to be re-chosen, which repoints a bundle that something on disk may
            # still reference, and the reader has to see why.
            console.out(f"  WARNING: the existing {derived_id} fixture records no "
                  f"donor bundle (missing, corrupt, or pre-v2). Searching for "
                  f"a new one.")
            return None
        for candidate in rows:
            if candidate["bundle"] == bundle:
                return candidate
        console.out(f"  WARNING: {derived_id} names donor {bundle}, which is no "
              f"longer in the cohort. Searching for a new one.")
        return None

    def _recorded_pool_donor(derived_id: str) -> Dict:
        """I/O and reporting around ``choose_pool_donor()``.

        The DECISION is module-level and pure so it can be exercised without a
        paid capture -- reaching this nested wrapper means running main() past
        `if not args.scan_only`, which captures fixtures with live billed Stage
        5 calls, so anything left in here is untestable for free. What stays is
        exactly the part that cannot be pure: resolving the path, reading the
        file, and printing why a fallback happened.

        THE ABSENT-FILE CASE IS SILENT AND THE EMPTY-MEMORY CASE IS NOT, which
        is why the existence check is here rather than folded into the read.
        ``read_recorded_donor_bundle()`` answers None for both, but they are
        different events: no file at all is the FIRST capture of this fixture
        and there is nothing to report, while a file that exists and records no
        donor means memory was expected and is missing.
        """
        path = fixture_path(derived_id, root)
        if not os.path.exists(path):
            return None
        # Read ONCE. The name is needed again for the not-in-pool message, and a
        # second read is both wasted I/O and a chance for the two to disagree.
        bundle = read_recorded_donor_bundle(path)
        donor, outcome = choose_pool_donor(bundle, donors)
        if outcome == DONOR_NO_MEMORY:
            console.out(f"  [Donor] {derived_id}: the existing fixture records no "
                  f"donor bundle; taking the next pool donor.")
        elif outcome == DONOR_NOT_IN_POOL:
            console.out(f"  [Donor] {derived_id}: recorded donor {bundle[:44]} is no "
                  f"longer an available donor (taken by another fixture this run, "
                  f"or no longer an ordinary patient); taking the next pool donor.")
        return donor

    # --- no_candidates -----------------------------------------------------
    if selection.get(CASE_NO_CANDIDATES):
        _add("no_candidates", selection[CASE_NO_CANDIDATES], [CASE_NO_CANDIDATES])
    elif not args.scan_only and _wanted_now(RECIPE_NO_CANDIDATES):
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
            console.out(f"\n[Derive] {derived_id}: rebuilding from the donor the "
                  f"existing fixture records ({known['bundle'][:44]})")
            info = build_no_candidates_bundle(
                known["path"], out_path, NO_CANDIDATES_AGE_YEARS
            )
            outcome = probe_empty_candidate_pool(out_path)
            tried = 1
            console.out(f"  survivors={outcome['survivors']}")
            if outcome["empty_pool"]:
                accepted = (known, info, outcome)
            else:
                console.out("  The recorded donor no longer empties the pool — the "
                      "index has changed. Searching for another.")

        if accepted is None:
            console.out(f"\n[Derive] {derived_id}: no cohort patient empties the "
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
                console.out(f"  [{tried}/{NO_CANDIDATES_MAX_DONORS}] "
                      f"{donor['bundle'][:38]:<38} "
                      f"survivors={outcome['survivors']:>3} "
                      f"({donor['primary_diagnosis'][:34]})")
            if outcome["empty_pool"]:
                accepted = (donor, info, outcome)
                break
        if accepted is None:
            console.out(f"  [Derive] FAILED after {tried} donor(s). Best was "
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
    elif not args.scan_only and _wanted_now(RECIPE_MESH_FALLBACK):
        derived_id = RECIPE_MESH_FALLBACK
        _handle, out_path = tempfile.mkstemp(prefix=f"{derived_id}_",
                                             suffix=".bundle.json")
        os.close(_handle)
        # The donor the existing fixture names wins over a fresh pick, so
        # re-running this file regenerates the same bundle instead of
        # repointing a captured fixture at a different patient.
        donor = _recorded_donor(derived_id) or _next_donor()
        console.out(f"\n[Derive] {derived_id}: no cohort patient takes "
              f"EXPANSION_PATH_FALLBACK; deriving from {donor['bundle'][:44]}")
        info = build_mesh_fallback_bundle(donor["path"], out_path)
        was = ", ".join(f"{c['was_code']} ({c['was_display']})"
                        for c in info["codings_rewritten"])
        console.out(f"  rewrote {len(info['codings_rewritten'])} primary-cancer "
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
    if not args.scan_only and _wanted_now(RECIPE_MCODE_VARIANT):
        derived_id = RECIPE_MCODE_VARIANT
        _handle, out_path = tempfile.mkstemp(prefix=f"{derived_id}_",
                                             suffix=".bundle.json")
        os.close(_handle)
        donor = _recorded_donor(derived_id) or _next_donor()
        console.out(f"\n[Derive] {derived_id}: no cohort bundle carries LOINC "
              f"{MCODE_VARIANT_LOINC}; deriving from {donor['bundle'][:44]}")
        info = build_mcode_variant_bundle(donor["path"], out_path)
        console.out(f"  injected {info['gene']} {info['protein_change']} "
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
    # A SKIPPED truncation_split STILL RESERVES ITS DONOR, and it is the only
    # fixture in the plan for which that sentence is not a no-op.
    #
    # The three recipe-derived fixtures take their remembered donor through
    # `_recorded_donor()`, which searches the whole cohort and pops NOTHING, so
    # skipping one leaves the pool exactly as deriving-from-memory would have.
    # truncation_split takes its remembered donor through `choose_pool_donor()`,
    # which POPS -- so skipping it silently leaves a donor in the pool that a
    # completed run would have consumed. Nothing observes that today, because
    # this is the last donor consumer in the plan; that is an argument from the
    # ORDER OF THIS FUNCTION, and the next fixture appended below it would
    # invalidate it without failing anything. The reservation makes the pool
    # state after a skip identical to the pool state after a capture, whatever
    # the order.
    if (args.resume and not args.scan_only and _wanted("truncation_split")
            and _resume_skip("truncation_split")):
        _reserved, _outcome = choose_pool_donor(
            read_recorded_donor_bundle(fixture_path("truncation_split", root)),
            donors)
        if _outcome == DONOR_FROM_MEMORY:
            console.out(f"  [Donor] truncation_split: skipped, and its recorded "
                        f"donor {_reserved['bundle'][:44]} is reserved out of "
                        f"the pool so a later derivation cannot take it.")
    if not args.scan_only and _wanted_now("truncation_split"):
        # Prefer the donor the existing fixture records, exactly as the three
        # recipe-derived fixtures above do. Without this it took _next_donor()
        # unconditionally and therefore rebound on EVERY capture -- and worse,
        # it rebound as a side effect of how many donors the no_candidates
        # search happened to burn first, so its patient was a function of an
        # unrelated probe loop.
        _trunc_row = _recorded_pool_donor("truncation_split")
        if _trunc_row is None:
            _trunc_row = _next_donor()
        else:
            console.out(f"  [Donor] truncation_split: reusing the recorded donor "
                  f"{_trunc_row['bundle'][:44]}")
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
        console.out(f"  {entry['fixture_id']:<28} {row['bundle'][:44]:<44}")
        console.out(f"  {'':<28} patient_id={row['patient_id']}")
        console.out(f"  {'':<28} dx={row['primary_diagnosis'][:60]}")
        console.out(f"  {'':<28} mesh={row['mesh_resolution']} "
              f"path={row['expansion_path']} stage={row['stage']} "
              f"ecog={row['ecog']}")
        if entry["config_name"]:
            console.out(f"  {'':<28} ablation={entry['config_name']}")
        if entry["construction"]:
            console.out(f"  {'':<28} DERIVED BUNDLE from "
                  f"{entry['construction']['derived_from_bundle'][:40]}")
    # The base is not known yet and this line must not claim it is: it is
    # chosen AFTER capture, from the runs that actually made one Stage 5 call.
    console.out(f"  {'llm_classifier_parse_retry_constructed':<28} constructed after "
                f"capture from a single-call normal run "
                f"(preferring {RETRY_BASE_PREFERRED})")

    if args.scan_only:
        console.out("\n--scan-only: nothing captured.")
        return 0

    # --- Capture -----------------------------------------------------------
    environment = _environment()
    console.out(f"\n[Env] Pinned Qdrant collection: {environment['qdrant_collection']}"
          f"{'' if environment['alias_resolved'] else '  (alias fallback!)'}")

    graph = build_matching_graph()

    written = []

    with CaffeinateSession("fixture capture"):
        for entry in plan:
            # BOTH FILTERS, AGAIN. The derived entries were already gated while
            # the plan was built (so a skipped one costs no derivation and no
            # temporary bundle); the cohort entries reach their first gate here.
            # _resume_skip is memoized, so asking twice cannot answer twice.
            if not _wanted_now(entry["fixture_id"]):
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
            console.out(f"  -> {os.path.basename(path)} "
                  f"({os.path.getsize(path) / 1024:.0f} KB, "
                  f"terminal={fixture['deterministic_prefix']['terminal']['terminal_node']})")
            console.out(f"     {_fixture_cost_line(fixture)}")

    # Temporary derived bundles are a copy of a Synthea record each, hundreds
    # of megabytes. Schema v2 stores the recipe, so nothing needs them once the
    # run they fed has been captured.
    for entry in plan:
        if entry["derivation"] and os.path.exists(entry["bundle_path"]):
            os.remove(entry["bundle_path"])
            console.out(f"  removed temporary bundle for {entry['fixture_id']}")

    # --- The constructed retry fixture -------------------------------------
    #
    # THE BASE IS CHOSEN BY SHAPE, NOT BY NAME. See choose_retry_base(): the
    # constructor requires one recorded Stage 5 call, and whether a given
    # patient's run makes one is a property of that run rather than of the
    # patient. Candidates are the recorded fixtures written by THIS run, and on
    # an --only run the ones already on disk, because there is nothing else to
    # offer then.
    retry_candidates = [f for f in written
                        if f.get("fixture_kind") == FIXTURE_KIND_RECORDED]

    # A FIXTURE --resume SKIPPED IS AS GOOD A BASE AS ONE THIS RUN WROTE, and
    # leaving it out silently changes which patient the retry fixture is built
    # from. `written` holds only what THIS invocation captured, so a resumed run
    # that had already finished `normal_1` offered a candidate set that did not
    # contain it -- `choose_retry_base()` would then report RETRY_BASE_SUBSTITUTED
    # and construct the fixture from a different patient than a single-pass run
    # produces. Nothing would fail: the fixture is written, it replays clean, and
    # it describes a different run. Adding the skipped ones back restores the
    # `RETRY_BASE_PREFERRED` preference and makes a resumed set identical to an
    # unresumed one.
    #
    # THE FILES ARE READ THROUGH `load_fixture`, gate included. They were read
    # through it already, by `resume_decision`, which is how they came to be
    # skipped; reading them again here is one extra decompression and keeps this
    # block's inputs the same shape as the `written` ones (whole fixtures, not
    # ids), rather than threading a second cache through the capture loop.
    for _skipped_id in resume_skipped:
        try:
            _skipped = load_fixture(fixture_path(_skipped_id, root))
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            # Unreachable through resume_decision, which already loaded it
            # cleanly -- so this is a file that changed underneath the run.
            # Named, not swallowed, and not a crash: the base selection below
            # simply has one candidate fewer.
            console.out(f"  (skipped fixture {_skipped_id} became unreadable "
                        f"after the resume decision: {exc})")
            continue
        if _skipped.get("fixture_kind") == FIXTURE_KIND_RECORDED:
            retry_candidates.append(_skipped)

    if not retry_candidates:
        for candidate_path in list_fixtures(root):
            try:
                candidate = load_fixture(candidate_path)
            except (ValueError, json.JSONDecodeError) as exc:
                # A stale-version file on disk is not a candidate and not a
                # crash: it is named and skipped, because this branch exists
                # precisely for the run that is replacing it.
                console.out(f"  (not a retry-base candidate) {exc}")
                continue
            if candidate.get("fixture_kind") == FIXTURE_KIND_RECORDED:
                retry_candidates.append(candidate)

    retry_incomplete = False
    # --resume DOES NOT GATE THIS ONE, and the asymmetry is deliberate. Every
    # other fixture is skipped because rebuilding it costs a live billed Stage 5
    # call; this one costs nothing -- `build_constructed_retry_fixture` splices a
    # synthetic failing attempt in front of a base fixture's RECORDED exchange
    # and issues no request. What it does depend on is which base it is handed,
    # and `choose_retry_base()` prefers `normal_1` deterministically, so a
    # resumed run and a single-pass run over the same set hand it the same base
    # and it writes the same fixture. Rebuilding it is therefore free AND is what
    # keeps it in step with a set some of whose members this run replaced;
    # skipping it would leave a fixture spliced onto a base that had since been
    # re-captured, which is the stale-artifact defect resume exists to avoid.
    if not args.only or "llm_classifier_parse_retry_constructed" in args.only:
        retry_base, retry_outcome, retry_detail = choose_retry_base(retry_candidates)
        if retry_base is None:
            retry_incomplete = True
            console.out(f"\n  ERROR: the constructed retry fixture was NOT built. "
                        f"{retry_detail}")
        else:
            if retry_outcome == RETRY_BASE_SUBSTITUTED:
                console.out(f"\n  [retry base] {retry_detail}")
            retry_fixture = build_constructed_retry_fixture(
                retry_base, "llm_classifier_parse_retry_constructed"
            )
            path = write_fixture(retry_fixture, root)
            written.append(retry_fixture)
            console.out(f"\n  -> {os.path.basename(path)} (constructed from "
                  f"{retry_fixture['construction']['derived_from']})")

    # --- Index -------------------------------------------------------------
    all_fixtures = []
    for path in list_fixtures(root):
        try:
            all_fixtures.append(load_fixture(path))
        except ValueError as exc:
            console.out(f"  WARNING: {exc}")

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

    # What this run cost. Priced from the recordings THIS run wrote, never from
    # the directory: `all_fixtures` includes fixtures an earlier capture left
    # there, and billing the operator for those would be a number that grows
    # every time --only is used.
    #
    # UNDER --resume THIS PRICES ONLY WHAT THIS RUN WROTE, which is not a new
    # behaviour and not a caveat added for resume: `written` has always been the
    # population, precisely so an --only run does not bill the operator for the
    # fixtures an earlier capture left in the directory. Resume makes it visible
    # rather than making it true -- a resumed run's total is the cost of
    # FINISHING the set, and the cost of the whole set is that number plus what
    # the interrupted run had already spent. The skipped count below is what
    # says how much of the set this figure is not about.
    spend = stage5_cost_summary(written)
    console.out(f"\n{'=' * 78}")
    console.out(f"Wrote {len(written)} fixture(s); {len(all_fixtures)} in {root}")
    if resume_skipped:
        console.out(
            f"--resume skipped {len(resume_skipped)} fixture(s) already current "
            f"on disk: {', '.join(sorted(resume_skipped))}")
        console.out(
            "  The Stage 5 spend below is what THIS run cost to finish the set, "
            "not what the set cost.")
    console.out(
        f"Stage 5 spend: ${spend['cost_usd']:.5f} over {spend['calls_priced']} "
        f"call(s), {spend['input_tokens']:,} in / {spend['output_tokens']:,} out"
        f"{'' if spend['cost_complete'] else '   <- A FLOOR, NOT A TOTAL'}"
    )
    if spend["excluded_fixture_ids"]:
        console.out(
            f"  excludes {len(spend['excluded_fixture_ids'])} fixture(s) whose "
            f"recordings are copied from another fixture, which would "
            f"double-bill: {', '.join(spend['excluded_fixture_ids'])}"
        )
    if not spend["cost_complete"]:
        console.out(
            f"  {spend['calls_unpriced']} call(s) on unpriced model(s) "
            f"{', '.join(spend['unpriced_models'])} contribute NOTHING to the "
            f"figure above; add them to PRICING_CONFIG (oncotriage/config.py)."
        )
    # Embeddings are EXCLUDED and that is not an oversight. The recordings store
    # each embedding's input TEXT and model, never a token count, so pricing them
    # would mean tokenizing here -- an estimate presented beside a measurement.
    # They would also be incomplete: only the per-fixture query embeddings are
    # recorded, while the cohort probe issues hundreds more that no fixture sees.
    console.out("  Stage 5 only: embeddings are not priced (the recordings store "
          "input text, not token counts) and the cohort probe's embeddings are "
          "recorded nowhere.")
    console.out(f"Branch cases covered: {sorted(covered & set(ALL_BRANCH_CASES))}")
    uncovered = sorted(set(ALL_BRANCH_CASES) - covered)
    if uncovered:
        console.out(f"Branch cases NOT covered: {uncovered}")
    if retry_incomplete:
        # NOT folded into `uncovered`, and the difference is the whole point.
        # `covered` is read off the fixtures ON DISK, so a retry fixture left
        # over from an earlier capture reports its case as covered while this
        # run failed to rebuild it -- a stale file counted as a fresh one, which
        # is the shape every version gate in this file exists to refuse. This
        # flag is about what THIS run did.
        console.out("The constructed retry fixture was not rebuilt by this run. "
                    "Any file of that name in the directory is from an earlier "
                    "capture and does not describe the pipeline as captured here.")
    console.out(f"{'=' * 78}\n")

    return 0 if not uncovered and not retry_incomplete else 1
#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
