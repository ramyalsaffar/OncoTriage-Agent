"""Replay every characterization fixture and report every difference.

Moved out of ``fixture_replay.py`` by item 20c, pass 3d.
``fixture_replay.py`` survives as a THIN ENTRY POINT and keeps no re-export
shim: all 27 of its top-level names were grepped against every ``.py``, ``.md``,
``.toml`` and ``.yml`` in the tree, and the only hits are prose in File 45,
File 13 and ``oncotriage/agent/deps.py``, plus the exec-bootstrap locals every
numbered file shares. Nothing has ever read a name out of File 46.

WHAT IT DOES (unchanged)
------------------------
For each fixture:

  1. Re-parses the source FHIR bundle with the CURRENT parser, rather than
     feeding back the patient dict stored in the fixture -- reusing the stored
     dict would feed the recorded answer in as an input and make that half of
     the diff vacuous.
  2. Runs the pipeline with all three model boundaries served from the
     recording: OpenAI embeddings, the MedCPT cross-encoder, and the Stage 5
     chat completion. No request reaches OpenAI, and no local model is loaded at
     all.
  3. Rebuilds the deterministic prefix with the same function that wrote it
     (``build_deterministic_prefix``, in ``oncotriage.fixtures.capture``) and
     diffs it field by field.
  4. Reports every difference by dotted field path with both values.

Qdrant is the one boundary that is NOT replayed. The trial corpus is the fixed
input the fixture is pinned against, so retrieval runs live and the recorded
per-channel NCT order is what proves the two runs asked the same questions.

Exit code 0 only when every fixture replays clean. Any difference, any replay
miss, any collection mismatch, any load failure -> 1.

FOUR THINGS CHANGED IN THE CONVERSION
-------------------------------------
1. **THE DEFERRAL ENVIRONMENT VARIABLE IS SET BEFORE THE FIRST ``oncotriage``
   IMPORT, AND THE ORDERING IS CHECKED RATHER THAN TRUSTED.**

   ``oncotriage/agent/deps.py`` reads ``ONCOTRIAGE_DEFER_LOCAL_MODELS`` ONCE, at
   its own import, into ``_DEFER_LOCAL_MODELS``. File 46 set the variable before
   ``13- LangGraph Agent.py`` was exec'd and said in a comment that setting it
   later would be too late. In a module that hazard is sharper, not softer: a
   naive conversion puts every import at the top of the file, ``deps`` is
   imported transitively by the very first ``oncotriage`` import, and the
   assignment below it runs AFTER the read -- so MedCPT (~110 MB) and FastEmbed
   are constructed for real on every replay, silently, while the run still
   prints "Local models: not loaded".

   So the assignment is above every ``oncotriage`` import here, deliberately and
   with the blank-line grouping that would otherwise invite an import sorter to
   move it. That is a module-level side effect on ``os.environ``, which is the
   one this module is allowed and the only one it has.

   Setting it is not the same as it having worked. If something already imported
   ``oncotriage.agent.deps`` before this module was imported -- another test in
   the same process, an entry point that imported the agent first -- the read
   already happened and this assignment reaches nothing. That case is RECORDED
   at import (``_DEFERRAL_WAS_LATE``) and ``assert_local_models_deferred()``
   turns it into a refusal, which ``main()`` calls before it loads a fixture. It
   is a recorded flag rather than an import-time raise on purpose: File 47's
   per-module import sweep imports the whole package in one process, in
   alphabetical order, so ``oncotriage.agent.deps`` is legitimately already
   there and a raise would fail a check that is testing something else.

2. **``diff_tunables()`` reads ``oncotriage.config``, not ``globals()``.**
   File 46 compared each recorded tunable against ``globals().get(name)``, which
   under the exec chain was the shared namespace File 03 had filled. In a module
   that expression sees THIS module's globals, where not one of the eighteen
   tunables is defined -- so every fixture would report all eighteen as
   ``<no longer defined>``, on every run, and the report would drown the real
   diff in eighteen lines of noise while looking like a genuine finding. The
   values now come from ``oncotriage.config``, which is where File 03 got them.

3. **``main()``'s local ``paths`` was renamed ``fixture_paths``.** This module
   imports ``oncotriage.paths`` as ``paths``, and in Python a name assigned
   anywhere in a function is local for the whole of it -- the exact shadowing
   defect CLAUDE.md records twice (``index_validator.stage1_index_health``,
   ``indexer._flush_embed_buffer``) and File 47 check 2g scans for. It does not
   fail today, because ``main()`` reads no other ``paths`` attribute, which is
   precisely what makes it the kind of latent trap that only surfaces when
   somebody adds a line.

4. Every free name that used to arrive from ``01- Imports.py``, File 03 or
   File 45's namespace is an explicit import.

THE ORDER OF THE REFUSALS IN ``main()`` IS LOAD-BEARING AND IS UNCHANGED
------------------------------------------------------------------------
    1. the dependency seam: NEGATIVE control first (the assertion must FAIL with
       no override installed), then the positive control;
    2. the OpenAI tripwire, both halves;
    3. the pinned Qdrant collection NAME;
    4. the pinned collection CONTENTS digest;
    5. only then is anything replayed.

Every one of those is a refusal rather than a warning, and each precedes the
work whose result it would otherwise make meaningless. In particular the two
collection checks come before any fixture is replayed, so a difference is never
reported against a different index.

WHAT IMPORTING THIS MODULE DOES
-------------------------------
Sets one environment variable, as above. Nothing else: no path is resolved, no
client is built, no model is loaded, no fixture is read, no graph is compiled.
"""

import os
import sys

# ---------------------------------------------------------------------------
# THIS BLOCK MUST STAY ABOVE EVERY oncotriage IMPORT. See point 1 of the module
# docstring. oncotriage.agent.deps evaluates ONCOTRIAGE_DEFER_LOCAL_MODELS at
# ITS import; an assignment underneath these imports reaches nothing, loads
# MedCPT and FastEmbed for real on every replay, and changes no printed line.
# ---------------------------------------------------------------------------

DEFER_LOCAL_MODELS_ENV = "ONCOTRIAGE_DEFER_LOCAL_MODELS"

# True when deps was ALREADY imported by somebody else and the variable was not
# already set -- i.e. the assignment below is too late to have any effect.
# assert_local_models_deferred() turns this into a refusal; it is not raised
# here because File 47 imports the whole package in one process and would hit it
# for a reason that has nothing to do with a replay.
_DEFERRAL_WAS_LATE = (
    "oncotriage.agent.deps" in sys.modules
    and os.environ.get(DEFER_LOCAL_MODELS_ENV) != "1"
)

os.environ[DEFER_LOCAL_MODELS_ENV] = "1"

import argparse
import copy
import json
import threading
import traceback
from types import SimpleNamespace
from typing import Dict, List

import numpy as np

from oncotriage import config, paths
from oncotriage.agent import deps
from oncotriage.agent.graph import build_initial_state, build_matching_graph
from oncotriage.agent.patient import compute_patient_hash
from oncotriage.config import COLLECTION_NAME, Project_Name
from oncotriage.fhir.parser import parse_fhir_bundle
from oncotriage.fixtures import capture as _capture
from oncotriage.fixtures.capture import (
    BUNDLE_DERIVED,
    BUNDLE_IN_COHORT,
    FIXTURE_KIND_CONSTRUCTED,
    OpenAIProxy,
    QdrantProxy,
    RecordingSink,
    SCHEMA_VERSION,
    _HOOK_KEYS,
    assert_hooks_reach_the_agent,
    build_deterministic_prefix,
    compute_collection_digest,
    fixture_root,
    flatten_prefix,
    list_fixtures,
    load_fixture,
    rebuild_derived_bundle,
    restore_hooks,
    sha256_json,
)
from oncotriage.utils import CaffeinateSession, resolve_qdrant_collection
from oncotriage.observability import console, correlation_scope


#------------------------------------------------------------------------------


# ===========================================================================
# THE DEFERRAL, PROVEN RATHER THAN ASSUMED
# ===========================================================================

def assert_local_models_deferred() -> None:
    """Refuse to replay unless the two local models really were skipped.

    Three separate facts, because each fails differently and only the third is
    about this process actually being clean:

      1. this module's environment assignment was not too late (see
         _DEFERRAL_WAS_LATE above);
      2. ``deps`` OBSERVED it -- ``deps._DEFER_LOCAL_MODELS`` is the single value
         every model factory in the package consults, read once at that module's
         import, and reading it here is the only way to know what that read saw.
         It is private and read anyway: a public accessor would be a second
         source of truth for a value whose whole point is that there is one;
      3. neither ``torch`` nor ``transformers`` is in ``sys.modules``. This is
         the empirical half. The first two say the switch is set; this says
         nothing has actually loaded the model behind its back, and it is the
         same measurement ``tests/test_package_invariants.py`` check 2d makes.

    A replay whose claim is "no model was loaded" must not rest on a comment.
    """
    if _DEFERRAL_WAS_LATE:
        raise RuntimeError(
            f"{DEFER_LOCAL_MODELS_ENV} was set AFTER oncotriage.agent.deps had "
            f"already been imported, so deps never saw it and both local models "
            f"will be loaded for real. Import oncotriage.fixtures.replay (or "
            f"set the variable) before anything imports the agent."
        )
    if not deps._DEFER_LOCAL_MODELS:
        raise RuntimeError(
            f"oncotriage.agent.deps did not observe {DEFER_LOCAL_MODELS_ENV}=1. "
            f"A replay that loads MedCPT and FastEmbed is not a replay; "
            f"refusing to run."
        )
    loaded = sorted(m for m in ("torch", "transformers") if m in sys.modules)
    if loaded:
        raise RuntimeError(
            f"{loaded} already imported before the first fixture was replayed. "
            f"Something loaded a local model in spite of "
            f"{DEFER_LOCAL_MODELS_ENV}=1; refusing to report a replay as "
            f"model-free when it was not."
        )


# ===========================================================================
# REPLAY MISS
# ===========================================================================

class FixtureReplayMiss(RuntimeError):
    """The pipeline asked for a model output the fixture does not contain.

    That is itself a finding, and a sharper one than a value difference: the
    code no longer embeds the same text, no longer scores the same trial texts,
    or no longer makes the same number of Stage 5 calls. It is raised rather
    than papered over with a zero vector, because a fabricated model output
    would propagate into the retrieval order and be reported as a dozen
    unrelated differences downstream.
    """


#------------------------------------------------------------------------------


# ===========================================================================
# REPLAY STANDS-INS
# ===========================================================================
#
# Each one mirrors the shape the real object returns, closely enough for the
# call sites in File 13 and no more. Anything the pipeline reads that is not
# reproduced here would raise an AttributeError naming the attribute, which is
# a better failure than a plausible stand-in that is subtly wrong.

def _embedding_response(vector):
    return SimpleNamespace(data=[SimpleNamespace(embedding=list(vector))])


def _chat_response(recorded):
    # completion_tokens_details mirrors the reasoning-model usage shape, and it
    # is set to None — not to a details object carrying 0 — when the recording
    # has no reasoning_tokens. Two recordings need that: one made before the
    # field existed (every GPT-4o-era fixture), and one made against a
    # non-reasoning model. File 13 reads the attribute defensively and logs
    # NULL for both, which is the honest answer; synthesising a 0 here would
    # make a replayed pre-migration fixture claim it spent no reasoning tokens
    # rather than that it never reported any.
    _reasoning = recorded["usage"].get("reasoning_tokens")
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=recorded["content"]),
            finish_reason=recorded.get("finish_reason"),
        )],
        model=recorded.get("model"),
        usage=SimpleNamespace(
            prompt_tokens=recorded["usage"]["prompt_tokens"],
            # Includes the reasoning tokens below, exactly as the API reports
            # it. Not a sum the replay computes.
            completion_tokens=recorded["usage"]["completion_tokens"],
            completion_tokens_details=(
                None if _reasoning is None
                else SimpleNamespace(reasoning_tokens=_reasoning)
            ),
        ),
    )


def _sparse_embedding(indices, values):
    # File 13 calls .indices.tolist() and .values.tolist(), so both have to be
    # numpy arrays and not lists. int32/float32 match what FastEmbed produces.
    return SimpleNamespace(
        indices=np.asarray(indices, dtype=np.int32),
        values=np.asarray(values, dtype=np.float32),
    )


class _ReplayState:
    """Indexes one fixture's recordings and tracks what has been consumed."""

    def __init__(self, fixture: Dict, sink):
        recordings = fixture["recordings"]
        self.fixture_id = fixture["fixture_id"]
        self.sink = sink
        self._lock = threading.Lock()

        # Embeddings and cross-encoder passes are served BY KEY: the pipeline
        # may issue them in any thread order, and what matters is that it asked
        # for the same thing, not that it asked in the same order.
        self.embeddings = {
            (r["model"], r["input"]): r["vector"]
            for r in recordings["openai_embeddings"]
        }
        self.sparse = {r["query"]: r for r in recordings["sparse_embeddings"]}
        self.cross_encoder = {
            (r["query"], r["trial_texts_sha256"]): r
            for r in recordings["cross_encoder"]
        }

        # Stage 5 is served BY CALL INDEX, not by key. The retry loop issues
        # the SAME request twice and must receive different answers; a
        # key-indexed lookup would hand back the malformed payload forever and
        # the run would exhaust its retries instead of recovering.
        self.chat_calls = list(recordings["chat_completions"])
        self.chat_cursor = 0


def _replay_embedding(state: _ReplayState):
    def handler(_inner, kwargs):
        key = (kwargs.get("model"), kwargs.get("input"))
        if key not in state.embeddings:
            detail = (f"model={key[0]!r} input[:120]={str(key[1])[:120]!r}")
            state.sink.note_miss("recordings.openai_embeddings", detail)
            raise FixtureReplayMiss(
                f"no recorded embedding for {detail} — the pipeline is "
                f"embedding text this fixture never saw"
            )
        return _embedding_response(state.embeddings[key])
    return handler


def _replay_chat(state: _ReplayState):
    def handler(_inner, kwargs):
        with state._lock:
            index = state.chat_cursor
            state.chat_cursor += 1

        if index >= len(state.chat_calls):
            detail = (f"call {index} requested, only {len(state.chat_calls)} "
                      f"recorded")
            state.sink.note_miss("recordings.chat_completions", detail)
            raise FixtureReplayMiss(
                f"Stage 5 made more calls than were recorded ({detail})"
            )

        recorded = state.chat_calls[index]
        # The request is diffed rather than enforced: serving the recorded
        # response for a request that changed is exactly what makes the change
        # visible downstream, in the verdicts and in stage5.request_sha256_by_call.
        # Same key set as the recorder in File 45, in the same order, because
        # the two are diffed against each other. A key present on one side only
        # would read as a request change on every fixture.
        state.sink.add("chat_completions", {
            "request": {
                "model": kwargs.get("model"),
                "messages": copy.deepcopy(kwargs.get("messages")),
                "temperature": kwargs.get("temperature"),
                "max_tokens": kwargs.get("max_tokens"),
                "max_completion_tokens": kwargs.get("max_completion_tokens"),
                "reasoning_effort": kwargs.get("reasoning_effort"),
                "seed": kwargs.get("seed"),
                # Same key, same position, same deepcopy as the recorder in
                # oncotriage/fixtures/capture.py -- the two blocks are diffed
                # against each other, so a key on one side only reads as a
                # request change on every fixture.
                "response_format": copy.deepcopy(kwargs.get("response_format")),
            },
            "response": copy.deepcopy(recorded["response"]),
            "served_from_recorded_index": recorded.get("call_index", index),
        })
        return _chat_response(recorded["response"])
    return handler


class ReplaySparseModel:
    """Serves recorded FastEmbed query vectors. Loads nothing."""

    def __init__(self, state: _ReplayState):
        self._state = state

    def query_embed(self, query_text, **_kwargs):
        recorded = self._state.sparse.get(query_text)
        if recorded is None:
            detail = f"query={str(query_text)[:160]!r}"
            self._state.sink.note_miss("recordings.sparse_embeddings", detail)
            raise FixtureReplayMiss(
                f"no recorded BM25 query vector for {detail}"
            )
        self._state.sink.add("sparse_embeddings", {
            "query": query_text,
            "indices": list(recorded["indices"]),
            "values": list(recorded["values"]),
        })
        yield _sparse_embedding(recorded["indices"], recorded["values"])


def _replay_medcpt(state: _ReplayState):
    def wrapper(query, trial_texts):
        digest = sha256_json(list(trial_texts))
        recorded = state.cross_encoder.get((query, digest))
        if recorded is None:
            detail = (f"query={str(query)[:80]!r} n_pairs={len(trial_texts)} "
                      f"trial_texts_sha256={digest[:16]}...")
            state.sink.note_miss("recordings.cross_encoder", detail)
            raise FixtureReplayMiss(
                f"no recorded cross-encoder pass for {detail} — either the "
                f"rerank query changed or the trial texts fed to it did"
            )
        # float32, the dtype the model produced. np.argsort ties break the same
        # way at either precision here, but the per-query min/max/mean logged
        # by Stage 3 would not round-trip identically as float64.
        scores = np.asarray(recorded["scores"], dtype=np.float32)
        state.sink.add("cross_encoder", {
            "query": query,
            "n_pairs": len(trial_texts),
            "trial_texts_sha256": digest,
            "trial_texts": list(trial_texts),
            "scores": [float(s) for s in scores],
            "dtype": str(scores.dtype),
        })
        return scores
    return wrapper


class _TripwireEndpoint:
    """A stand-in for one OpenAI sub-object. Raises the moment it is used.

    OpenAIProxy READS inner.embeddings and inner.chat.completions in its
    __init__, to build the two shims that serve them from the recording. Those
    two reads are structural and must succeed; what must never happen is a CALL
    reaching the network. So the tripwire hands back these placeholders for the
    two known paths, and they raise on any attribute access or call underneath.
    """

    def __init__(self, path):
        object.__setattr__(self, "_path", path)

    def _explode(self, what):
        raise FixtureReplayMiss(
            f"replay reached the real OpenAI client at {self._path}.{what}. "
            f"A replay must make no request to OpenAI; this would have been a "
            f"real, billed call. Either the pipeline now uses an OpenAI surface "
            f"the recording does not cover, or a hook is missing from "
            f"install_replay_hooks()."
        )

    def __getattr__(self, attr):
        if attr == "completions":
            return _TripwireEndpoint(f"{self._path}.completions")
        self._explode(attr)

    def __call__(self, *args, **kwargs):
        self._explode("()")


class _OpenAITripwire:
    """Stands where the real OpenAI client would be, and raises on any use.

    THIS IS THE MONEY GUARD, and it is the reason replay can claim "no request
    reaches OpenAI" rather than merely intending it.

    OpenAIProxy forwards UNKNOWN attributes to the object it wraps. In capture
    that object is the real client, which is correct -- capture is supposed to
    call OpenAI. In replay it must not be, because any surface the proxy does
    not intercept would go straight through to the endpoint and be billed while
    the replay reported clean. So replay wraps THIS instead: the two paths the
    proxy does intercept (embeddings, chat.completions) are handed inert
    placeholders that the proxy immediately shadows with its recording shims,
    and every other attribute raises by name.

    It is not a substitute for the identity assertion in
    install_replay_hooks() -- that one catches a hook that was never installed,
    this one catches a hook that was installed but incomplete. Different
    failures, both silent without a guard.
    """

    _STRUCTURAL = ("embeddings", "chat")

    def __init__(self):
        object.__setattr__(self, "touched", [])

    def __getattr__(self, attr):
        self.touched.append(attr)
        if attr in _OpenAITripwire._STRUCTURAL:
            # Read by OpenAIProxy.__init__ and then SHADOWED by its shim. The
            # placeholder is what makes an un-shadowed use raise instead of
            # reaching the network.
            return _TripwireEndpoint(f"openai_client.{attr}")
        raise FixtureReplayMiss(
            f"replay reached the OpenAI client for {attr!r}, which the fixture "
            f"does not serve. A replay must make no request to OpenAI; this "
            f"would have been a real, billed call. Either the pipeline now uses "
            f"an OpenAI surface the recording does not cover, or a hook is "
            f"missing from install_replay_hooks()."
        )


def install_replay_hooks(fixture: Dict, sink) -> tuple:
    """Point all four seams at the recording. Returns (saved, replay_state).

    INSTALLED THROUGH oncotriage.agent.deps SINCE PASS 20c-2c. This used to
    rebind four names in this module's globals(), which worked only because
    every project file was exec'd into one dict. File 13 is a shim over
    oncotriage/agent/ now, its functions resolve their globals in their own
    modules, and a rebinding here would have redirected NOTHING: every Stage 5
    prompt in every fixture would have gone to the real OpenAI endpoint, been
    billed, and the run would still have printed that all twelve replayed
    clean. Nothing would have raised. That is the defect the seam exists for.

    Qdrant alone still talks to the network: the corpus is the fixed input, not
    a recorded output. That is why this file cannot be run with all egress
    blocked, and why the OpenAI side gets a tripwire instead -- see
    _OpenAITripwire.

    Raises:
        capture.UnsupportedMatchingProviderError: before any hook is installed,
            when MATCHING_PROVIDER names a provider these proxies do not cover.
            A replay under that flag would bypass the tripwire and send all
            twelve fixtures' Stage 5 prompts to a live, billed endpoint while
            reporting that it made no calls -- the exact regression the seam
            was built to prevent, reintroduced through a second provider.
    """
    _capture.assert_provider_is_hookable("install_replay_hooks")
    _capture.assert_call_mode_is_hookable("install_replay_hooks")

    state = _ReplayState(fixture, sink)

    proxies = {
        deps.OPENAI_CLIENT: OpenAIProxy(
            # NOT the real client. See _OpenAITripwire.
            _OpenAITripwire(),
            _replay_embedding(state),
            _replay_chat(state),
        ),
        deps.QDRANT_CLIENT: QdrantProxy(deps.get_qdrant_client(), sink),
        deps.BM25_QUERY_MODEL: ReplaySparseModel(state),
        deps.MEDCPT_SCORER: _replay_medcpt(state),
    }
    saved = deps.set_overrides(proxies)

    # THE ASSERTION THAT REPLACES "IT ALWAYS WORKED". Identity, against what
    # deps hands the agent -- not against this namespace, which would pass with
    # the hooks installed nowhere.
    assert_hooks_reach_the_agent(
        {name: proxies[key] for name, key in _HOOK_KEYS.items()},
        "install_replay_hooks",
    )

    return saved, state


#------------------------------------------------------------------------------


# ===========================================================================
# DIFFING
# ===========================================================================

# How much of a long value to show. Whole GPT-4o prompts and 1536-float vectors
# do not belong in a diff report; the field path plus a recognisable head is
# what a reader acts on.
VALUE_PREVIEW_CHARS = 220


def _preview(value) -> str:
    text = repr(value)
    if len(text) <= VALUE_PREVIEW_CHARS:
        return text
    return f"{text[:VALUE_PREVIEW_CHARS]}... [{len(text)} chars]"


_ABSENT = object()


def diff_prefix(expected: Dict, actual: Dict) -> List[Dict]:
    """Field-by-field difference between two deterministic prefixes.

    Exact equality on flattened leaves. None never compares equal to 0: the
    degradation columns distinguish "the stage did not report" from "the stage
    reported nothing wrong" (see CLAUDE.md), and a diff that folded them
    together would hide the one regression those fields exist to catch.

    Fields present on one side only are reported as such, so a prefix that
    grew or lost a key is a difference rather than a silent pass.
    """
    flat_expected = flatten_prefix(expected)
    flat_actual = flatten_prefix(actual)

    differences = []
    for field in sorted(set(flat_expected) | set(flat_actual)):
        want = flat_expected.get(field, _ABSENT)
        got = flat_actual.get(field, _ABSENT)

        if want is _ABSENT:
            differences.append({"field": field, "kind": "added",
                                "expected": "<absent in fixture>",
                                "actual": _preview(got)})
        elif got is _ABSENT:
            differences.append({"field": field, "kind": "removed",
                                "expected": _preview(want),
                                "actual": "<absent in replay>"})
        elif type(want) is not type(got) or want != got:
            # type() before == so True does not compare equal to 1 and None
            # stays distinct from a falsy value of another type.
            differences.append({"field": field, "kind": "changed",
                                "expected": _preview(want),
                                "actual": _preview(got)})

    return differences


def diff_tunables(fixture: Dict) -> List[Dict]:
    """Config constants that moved since capture.

    Reported separately from the prefix diff because they are a different kind
    of finding: a tunable change EXPLAINS a prefix difference rather than being
    one, and a reader who does not see it will spend the afternoon looking for
    a refactor bug that is not there.
    """
    recorded = fixture["environment"].get("tunables") or {}
    moved = []
    for name, was in sorted(recorded.items()):
        now = getattr(config, name, _ABSENT)
        if now is _ABSENT:
            moved.append({"name": name, "was": was, "now": "<no longer defined>"})
        elif now != was:
            moved.append({"name": name, "was": was, "now": now})
    return moved


#------------------------------------------------------------------------------


# ===========================================================================
# ONE FIXTURE
# ===========================================================================

def obtain_bundle(fixture: Dict, root: str = None) -> tuple:
    """Get the fixture's input bundle. Returns (path, is_temporary).

    A cohort fixture names a file in paths.data_fhir_path. A derived fixture names a
    RECIPE (schema v2), which is rebuilt from the live corpus into a temporary
    file here — the derived bundle is 107 MB of Synthea record and storing it
    was what made the v1 fixture directory uncommittable.

    The fixture stores a filename, never a path: paths.data_fhir_path is
    resolved by glob prefix in oncotriage/paths.py and moves between
    renumberings and between host and container.
    """
    identity = fixture["identity"]
    location = identity.get("source_bundle_location", BUNDLE_IN_COHORT)

    if location == BUNDLE_DERIVED:
        # `root` is the fixture directory this replay is working in, threaded
        # from main() so that `--fixture-dir` is self-contained: the rebuilt
        # bundle lands beside the fixtures it is being replayed against rather
        # than in the system temporary directory (the portability pass) and
        # rather than in the DEFAULT fixture directory, which is not the one
        # this run was pointed at.
        return rebuild_derived_bundle(fixture, root), True

    return os.path.join(paths.data_fhir_path, identity["source_bundle"]), False


def replay_fixture(fixture: Dict, graph: object, root: str = None) -> Dict:
    """Replay one fixture and return its report."""
    fixture_id = fixture["fixture_id"]
    console.out(f"\n{'-' * 78}\n{fixture_id}  [{', '.join(fixture['case_labels'])}]"
          f"{'  (CONSTRUCTED)' if fixture['fixture_kind'] == FIXTURE_KIND_CONSTRUCTED else ''}")
    console.out(f"{'-' * 78}")

    report = {
        "fixture_id": fixture_id,
        "fixture_kind": fixture["fixture_kind"],
        "case_labels": fixture["case_labels"],
        "differences": [],
        "replay_misses": [],
        "tunables_moved": diff_tunables(fixture),
        "fatal": None,
        "parse_source": None,
        "recipe_hash_ok": None,
    }

    # --- Obtain and re-parse the source bundle -----------------------------
    temporary = False
    try:
        bundle_path, temporary = obtain_bundle(fixture, root)
    except Exception as exc:
        report["fatal"] = (f"could not obtain the source bundle: "
                           f"{type(exc).__name__}: {exc}")
        console.out(f"  FATAL: {report['fatal']}")
        return report

    try:
        if not os.path.exists(bundle_path):
            # Only reachable for a cohort fixture whose patient has left the
            # corpus. Falling back to the stored dict keeps the rest of the
            # pipeline diffable but silently removes File 07 from the
            # comparison, so it is stated in the report, not merely printed.
            patient_data = copy.deepcopy(
                fixture["deterministic_prefix"]["patient_data"]
            )
            report["parse_source"] = "stored_dict_bundle_missing"
            console.out(f"  WARNING: source bundle not found at {bundle_path}; "
                  f"replaying from the stored patient dict. The parser is NOT "
                  f"under test for this fixture.")
        else:
            try:
                patient_data = parse_fhir_bundle(bundle_path)
                report["parse_source"] = "rebuilt_from_recipe" if temporary \
                    else "reparsed"
            except Exception as exc:
                report["fatal"] = (f"parse_fhir_bundle failed on "
                                   f"{fixture['identity']['source_bundle']}: "
                                   f"{type(exc).__name__}: {exc}")
                console.out(f"  FATAL: {report['fatal']}")
                return report

            # A recipe that no longer reproduces its input is a broken fixture,
            # not a code regression, and it has to say so in its own words
            # before the diff blames Stage 1 for a different patient.
            if temporary:
                # THE GATE COMPARES THE PARSED RECORD, NOT ITS HASH, and that
                # is a correctness fix rather than a preference.
                #
                # The property this gate exists to defend is "the recipe still
                # reproduces its input". It used to test that by comparing
                # compute_patient_hash(rebuilt) against the hash recorded at
                # capture time -- which couples a fixture-integrity check to a
                # FUNCTION, so any legitimate change to what that function
                # hashes turns every constructed fixture FATAL with a message
                # blaming the recipe, the donor bundle or the parser, none of
                # which moved. The reproducibility-hash pass hit exactly that:
                # adding allergies, genomic variants and stage observations
                # moved all twelve recorded hashes and would have failed five
                # constructed fixtures for a reason the message could not name.
                #
                # Comparing the dicts is STRICTLY STRONGER on top of being
                # stable: the hash is truncated to 16 hex characters and covers
                # only the sub-fields it chooses, so two genuinely different
                # patient records could share one. The dict comparison sees
                # every field the parser produced, and it can NAME the one that
                # moved instead of printing two opaque hashes.
                recorded_patient = fixture["deterministic_prefix"]["patient_data"]
                report["recipe_patient_ok"] = patient_data == recorded_patient
                if not report["recipe_patient_ok"]:
                    differing = sorted(
                        k for k in set(patient_data) | set(recorded_patient or {})
                        if patient_data.get(k) != (recorded_patient or {}).get(k)
                    )
                    report["recipe_differing_fields"] = differing
                    # BUILT ON ITS OWN LINE, NOT INSIDE THE f-STRING. The first
                    # version put this `or` expression inside a replacement
                    # field that spanned a line break, which is PEP 701 syntax
                    # and therefore Python 3.12+ ONLY. The development machine
                    # runs 3.13 and compiled it; the Docker image is
                    # python:3.11-slim, where it is a SyntaxError and this
                    # module would not import at all. Reproduced on a 3.9
                    # interpreter, which shares the pre-701 tokenizer.
                    differing_text = ", ".join(differing) or \
                        "(no top-level field — check ordering)"
                    report["fatal"] = (
                        f"the derivation recipe "
                        f"{fixture['derivation']['recipe']!r} rebuilt a patient "
                        f"that differs from the one the fixture recorded, in: "
                        f"{differing_text}. The recipe, the donor bundle or the "
                        f"parser changed — this is not a pipeline difference."
                    )
                    console.out(f"  FATAL: {report['fatal']}")
                    return report

                # The capture-time hash is PROVENANCE, like captured_at_utc
                # beside it: a record of what compute_patient_hash returned on
                # the day this fixture was made. It is reported, never enforced,
                # so a change to the hash function is VISIBLE here rather than
                # either silent or fatal.
                rebuilt_hash = compute_patient_hash(patient_data)
                expected_hash = fixture["identity"]["patient_data_hash"]
                report["recipe_hash_ok"] = rebuilt_hash == expected_hash
                console.out(
                    f"  rebuilt from recipe "
                    f"{fixture['derivation']['recipe']}: patient_data matches "
                    f"the recorded record"
                    + ("" if report["recipe_hash_ok"] else
                       f"; NOTE the capture-time patient_data_hash "
                       f"{expected_hash} is now {rebuilt_hash} — "
                       f"compute_patient_hash has changed since capture, which "
                       f"is not a fixture fault"))
    finally:
        if temporary and os.path.exists(bundle_path):
            os.remove(bundle_path)

    # --- Run with the model boundaries served from the recording ------------
    sink = RecordingSink()
    saved, _state = install_replay_hooks(fixture, sink)
    try:
        initial_state = build_initial_state(
            patient_data, fixture["inputs"]["ablation_flags"]
        )
        # Scoped: see oncotriage/agent/graph.py. One fixture is one patient.
        with correlation_scope():
            final_state = graph.invoke(initial_state)
        result = final_state["result"]
        result["qdrant_collection"] = resolve_qdrant_collection()
        result["patient_data_hash"] = compute_patient_hash(patient_data)
    except FixtureReplayMiss as exc:
        # A miss raised outside Stage 2's per-channel handler kills the run.
        # Inside it, the channel is recorded as failed and the run continues —
        # which is why sink.replay_misses is reported either way.
        report["fatal"] = f"replay miss: {exc}"
        report["replay_misses"] = list(sink.replay_misses)
        console.out(f"  FATAL: {report['fatal']}")
        return report
    except Exception as exc:
        report["fatal"] = (f"pipeline raised {type(exc).__name__}: {exc}\n"
                           f"{traceback.format_exc()}")
        report["replay_misses"] = list(sink.replay_misses)
        console.out(f"  FATAL: pipeline raised {type(exc).__name__}: {exc}")
        return report
    finally:
        restore_hooks(saved)

    report["replay_misses"] = list(sink.replay_misses)

    # --- Diff ---------------------------------------------------------------
    actual_prefix = build_deterministic_prefix(final_state, result, sink)
    # Through JSON first: the fixture's side has been through a round trip and
    # the live side has not, so comparing them raw would report a tuple that
    # became a list as a difference in the code.
    actual_prefix = json.loads(json.dumps(actual_prefix, ensure_ascii=False))

    report["differences"] = diff_prefix(
        fixture["deterministic_prefix"], actual_prefix
    )
    return report


#------------------------------------------------------------------------------


# ===========================================================================
# REPORTING
# ===========================================================================

def print_report(report: Dict, max_diffs: int) -> None:
    if report["tunables_moved"]:
        console.out("  CONFIG MOVED SINCE CAPTURE (explains prefix differences):")
        for moved in report["tunables_moved"]:
            console.out(f"    {moved['name']}: {moved['was']!r} -> {moved['now']!r}")

    for miss in report["replay_misses"]:
        console.out(f"  REPLAY MISS  {miss['field']}: {miss['detail']}")

    if report["fatal"]:
        return

    differences = report["differences"]
    if not differences:
        console.out(f"  CLEAN — deterministic prefix identical "
              f"({report['parse_source']}).")
        return

    console.out(f"  {len(differences)} DIFFERENCE(S):")
    for difference in differences[:max_diffs]:
        console.out(f"    [{difference['kind']}] {difference['field']}")
        console.out(f"        fixture: {difference['expected']}")
        console.out(f"        replay : {difference['actual']}")
    if len(differences) > max_diffs:
        console.out(f"    ... and {len(differences) - max_diffs} more "
              f"(raise --max-diffs to see them)")


#------------------------------------------------------------------------------


# ===========================================================================
# MAIN
# ===========================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay characterization fixtures and report every difference."
    )
    parser.add_argument("--only", nargs="*", default=None,
                        help="Replay only these fixture ids.")
    parser.add_argument("--fixture-dir", default=None,
                        help="Directory to read fixtures from.")
    parser.add_argument("--max-diffs", type=int, default=25,
                        help="Differences printed per fixture.")
    args = parser.parse_args()

    # BEFORE ANYTHING ELSE, and before a single fixture is read: pin Stage 5
    # to the GROUPED arm for this process and say so. The twelve fixtures
    # characterize that arm and can characterize no other until RecordingSink
    # learns a trial-stable ordering, so a replay run in a per-trial process
    # would diff a grouped recording against a per-trial run and report the
    # partition as a pipeline regression. This used to be a REFUSAL, which
    # would have taken this free gate out of service the day the default
    # flips; see oncotriage.fixtures.capture.pin_call_mode_for_fixture_process.
    #
    # FIRST, so that nothing reads the mode before it: install_replay_hooks'
    # own guard, and Stage 5's partition on every fixture below.
    _capture.pin_call_mode_for_fixture_process("fixture_replay.py")

    # ======================================================================
    # AND THE PROVIDER, WHICH IS A REFUSAL AND NOT A PIN
    # ======================================================================
    #
    # `config.MATCHING_PROVIDER` ships "bedrock_anthropic". The proxies hook
    # `deps.OPENAI_CLIENT` and wrap `chat.completions.create`, so at the shipped
    # default `assert_provider_is_hookable` refuses -- correctly, and that is
    # the seam working. What it did NOT do is refuse READABLY: the guard fires
    # inside `install_replay_hooks`, which `replay_fixture` calls on the line
    # ABOVE its `try`, so nothing catches it and this program died with a
    # traceback out of module scope. Exit 1 either way and nothing billed, so
    # the money was never at risk -- but the call-mode-pin pass had already
    # recorded that shape as "worse than a clean refusal", one guard over, and
    # this is that finding closed.
    #
    # WHY A REFUSAL AND NOT A PIN, which is the obvious symmetry with the call
    # mode two lines up and is the wrong move here. That pin exists because
    # refusing would have taken a LIVE gate out of service: the fixtures
    # characterize the grouped arm and pinning it lets them keep doing so. A
    # provider pin would instead make this program QUIETLY REPLAY THE DORMANT
    # ARM and report "12/12 clean" for a pipeline nobody is running -- the gate
    # would look alive and be measuring the wrong branch, which is worse than a
    # gate that says it cannot run. The refusal is loud and one command from
    # fixed; the pin would be silent and wrong.
    #
    # THE REMEDY IS STATED HONESTLY, INCLUDING THE PART THAT DOES NOT EXIST.
    # "Re-capture on the shipped arm" is not available: `capture.py` refuses
    # the same providers this does, so there is no way to produce a Converse
    # fixture until the proxies learn that seam. What IS available is running
    # this gate against the arm the fixtures describe.
    if config.MATCHING_PROVIDER != config.MATCHING_PROVIDER_OPENAI:
        console.out("\n[REFUSED] THIS GATE CANNOT RUN AT THE CONFIGURED PROVIDER.")
        console.out(f"          configured provider : "
                    f"{config.MATCHING_PROVIDER!r}  (config.MATCHING_PROVIDER)")
        console.out(f"          fixtures' provider  : "
                    f"{config.MATCHING_PROVIDER_OPENAI!r}  (the only provider "
                    f"this harness can hook, so every fixture on disk was "
                    f"captured on it by construction)")
        console.out("          why                 : the proxies wrap "
                    "chat.completions.create on deps.OPENAI_CLIENT. At any "
                    "other provider Stage 5 reaches a seam they do not cover, "
                    "so every Stage 5 call would be REAL and BILLED while this "
                    "run reported it had made none.")
        console.out("          remedy              : set "
                    "config.MATCHING_PROVIDER to "
                    f"{config.MATCHING_PROVIDER_OPENAI!r} to replay the "
                    "fixtures against the arm they describe. RE-CAPTURING ON "
                    "THE SHIPPED ARM IS NOT AVAILABLE: fixture_capture.py "
                    "refuses the same providers, so a Converse fixture cannot "
                    "be produced until OpenAIProxy learns that seam -- a "
                    "fixture-FORMAT change with a SCHEMA_VERSION bump.")
        console.out("          NOTHING WAS READ, NOTHING WAS HOOKED AND "
                    "NOTHING WAS BILLED.")
        return 1

    # And before a single fixture is read: the deferral
    # this module's import block installs has to have actually reached
    # oncotriage.agent.deps. If it did not, MedCPT and FastEmbed load for real
    # on the first rerank and the run below still prints "Local models: not
    # loaded" -- a false statement about the one property that makes a replay a
    # replay rather than a second run. See assert_local_models_deferred().
    assert_local_models_deferred()

    root = args.fixture_dir or fixture_root()

    console.out(f"\n{'=' * 78}")
    console.out(f"{Project_Name}: Characterization Fixture Replay (schema v{SCHEMA_VERSION})")
    console.out(f"{'=' * 78}")
    console.out(f"  Fixture directory: {root}")

    fixture_paths = list_fixtures(root)
    if not fixture_paths:
        console.out(f"[FATAL] No fixtures in {root}. Run fixture_capture.py first.")
        return 1

    fixtures = []
    load_failures = 0
    for path in fixture_paths:
        try:
            fixtures.append(load_fixture(path))
        # ── WHAT A FIXTURE FILE CAN FAIL AT, AND WHY OSError IS ONE OF THEM ──
        #
        # `load_fixture` does three things and each has its own failure class:
        # it OPENS the file, it DECOMPRESSES it, and it checks the schema
        # version it finds inside.
        #
        #   OSError     the open or the decompression. A fixture whose mode is
        #               000, one on an unreadable volume, one truncated
        #               mid-stream, one that is not gzip at all --
        #               `gzip.BadGzipFile` and `EOFError` from a truncated
        #               member both arrive as OSError subclasses, and
        #               PermissionError and IsADirectoryError are OSErrors too.
        #   ValueError  the version gate's own refusal, and the JSON parse.
        #               `json.JSONDecodeError` IS a ValueError subclass, so it
        #               no longer needs naming beside it -- and naming it
        #               separately was what made the tuple look complete while
        #               the whole OPEN half of the function was uncovered.
        #
        # IT WAS `(ValueError, json.JSONDecodeError)`, WHICH IS ONE CLASS
        # WEARING TWO NAMES. So a single unreadable file in the fixture
        # directory took the twelve-fixture gate down with a traceback, before
        # any fixture was diffed and instead of the per-file `LOAD FAILED` line
        # and exit 2 that the branch one line down already exists to produce --
        # the loudest possible failure for the most housekeeping-shaped cause,
        # and one that says nothing about whether the pipeline changed.
        #
        # THE TWO CODES STAY DIFFERENT, which is the point of routing this here
        # rather than letting it escape: exit 1 means the pipeline no longer
        # does what it did, and exit 2 means a file in the directory could not
        # be read and NOTHING replayed differently. Those have different owners
        # and different fixes; see the block at the end of this function.
        except (OSError, ValueError) as exc:
            load_failures += 1
            console.out(f"  LOAD FAILED  {os.path.basename(path)}: "
                        f"{type(exc).__name__}: {exc}")

    if args.only:
        fixtures = [f for f in fixtures if f["fixture_id"] in args.only]
        if not fixtures:
            console.out(f"[FATAL] --only matched no fixture in {root}")
            return 1

    if not fixtures:
        # Every fixture present failed the version gate: the extreme of the
        # load-failure case, so it takes that code rather than the one meaning
        # "the pipeline changed". Nothing was replayed, so nothing differed.
        console.out("[FATAL] No fixture could be loaded.")
        if load_failures:
            console.out("  exit 2 (load failures only, nothing replayed "
                  "differently)")
            return 2
        console.out("  exit 1 (no fixture to replay)")
        return 1

    # --- THE SEAM, CHECKED BEFORE ANYTHING IS REPLAYED ----------------------
    #
    # This block exists because of the defect pass 20c-2c fixed, and it is
    # deliberately the first thing main() does.
    #
    # Until pass 2c, install_replay_hooks() redirected the pipeline by rebinding
    # four names in this module's globals(). That worked only because every
    # project file was exec'd into one dict. File 13 is now a shim over
    # oncotriage/agent/, whose functions resolve their globals in their own
    # modules -- so a rebinding here reaches nothing, every Stage 5 prompt in
    # every fixture goes to the REAL OpenAI endpoint and is BILLED, and this
    # file still prints that all twelve replayed clean. Nothing raises.
    #
    # assert_hooks_reach_the_agent() (File 45) is what makes that impossible: it
    # asks deps what the AGENT would reach and requires it to be the proxy, by
    # identity. Two things are checked here, in this order:
    #
    #   1. NEGATIVE CONTROL FIRST. With no overrides installed, the assertion
    #      must RAISE. An assertion that has only ever passed is not evidence it
    #      can catch anything -- and this one guards the only thing in this
    #      repository that costs money to get wrong.
    #   2. Then the real thing, installed and torn down, must pass.
    #
    # No fixture is loaded and no model is called by either step.
    console.out("\n  Dependency seam (oncotriage.agent.deps):")

    _probe_sink = RecordingSink()
    # An EMPTY fixture, of the shape _ReplayState indexes. Nothing is served
    # from it -- the probe never invokes the pipeline, it only checks which
    # objects deps hands out -- so every recording list is empty on purpose.
    _probe_state = _ReplayState(
        {
            "fixture_id": "_seam_probe",
            "recordings": {
                "openai_embeddings": [],
                "sparse_embeddings": [],
                "cross_encoder": [],
                "chat_completions": [],
            },
        },
        _probe_sink,
    )
    _probe_proxies = {
        deps.OPENAI_CLIENT: OpenAIProxy(_OpenAITripwire(),
                                        _replay_embedding(_probe_state),
                                        _replay_chat(_probe_state)),
        deps.QDRANT_CLIENT: QdrantProxy(deps.get_qdrant_client(), _probe_sink),
        deps.BM25_QUERY_MODEL: ReplaySparseModel(_probe_state),
        deps.MEDCPT_SCORER: _replay_medcpt(_probe_state),
    }
    _probe_expected = {name: _probe_proxies[key] for name, key in _HOOK_KEYS.items()}

    try:
        assert_hooks_reach_the_agent(_probe_expected, "negative control")
    except RuntimeError as _exc:
        console.out(f"    negative control: the assertion FAILS with no override "
              f"installed, as it must")
        console.out(f"      {str(_exc).splitlines()[0]}")
    else:
        console.out("\n[FATAL] THE SEAM CHECK PROVES NOTHING.")
        console.out("        assert_hooks_reach_the_agent() passed with NO override "
              "installed, so it would also pass if install_replay_hooks() "
              "redirected nothing -- and every Stage 5 call below would be a "
              "real, billed request to OpenAI.")
        return 1

    _probe_saved = deps.set_overrides(_probe_proxies)
    try:
        assert_hooks_reach_the_agent(_probe_expected, "positive control")
        console.out("    positive control: with the overrides installed, the agent "
              "reaches all four proxies")
    finally:
        deps.restore_overrides(_probe_saved)

    # And the OpenAI tripwire itself: the replay client wraps a stand-in that
    # raises, not the real client, so a model surface the recording does not
    # cover fails loudly instead of being forwarded and billed.
    # Both halves: an unknown surface must raise by name, and the two the proxy
    # shadows must raise as soon as anything actually CALLS them.
    _tripwire_armed = 0
    try:
        _OpenAITripwire().responses          # a surface the recording never covers
    except FixtureReplayMiss:
        _tripwire_armed += 1
    try:
        _OpenAITripwire().chat.completions.create(model="x")   # the shadowed path
    except FixtureReplayMiss:
        _tripwire_armed += 1
    if _tripwire_armed == 2:
        console.out("    OpenAI tripwire armed: an unrecorded surface AND an "
              "unshadowed chat.completions.create both raise instead of "
              "reaching the network")
    else:
        console.out(f"\n[FATAL] the OpenAI tripwire raised on {_tripwire_armed}/2 "
              f"probes; refusing to replay.")
        return 1

    # --- The pinned collection, checked before anything is replayed ---------
    #
    # A fixture diffed against a different index measures the corpus, not the
    # code, and would report dozens of retrieval-order differences that mean
    # nothing. This is a refusal, not a warning.
    live_collection = resolve_qdrant_collection()
    pinned = {f["environment"]["qdrant_collection"] for f in fixtures}
    console.out(f"  Live Qdrant collection:   {live_collection}")
    console.out(f"  Fixtures pinned to:       {', '.join(sorted(pinned))}")

    if pinned != {live_collection}:
        console.out(f"\n[FATAL] THE INDEX CHANGED, NOT THE CODE.")
        console.out(f"        The alias '{COLLECTION_NAME}' now resolves to "
              f"'{live_collection}', which is not what these fixtures were "
              f"captured against.")
        console.out(f"        Refusing to diff against a different index. Either "
              f"point Qdrant back at the pinned collection, or re-capture the "
              f"fixtures with fixture_capture.py.")
        for fixture in sorted(fixtures, key=lambda f: f["fixture_id"]):
            got = fixture["environment"]["qdrant_collection"]
            if got != live_collection:
                console.out(f"          {fixture['fixture_id']}: pinned {got}")
        return 1

    # --- ...and what is IN it -----------------------------------------------
    #
    # The name check above catches File 11 swapping the alias to a new
    # collection. It cannot catch the collection itself being re-indexed in
    # place, which changes every retrieval order while the name stays put. That
    # failure is indistinguishable from a code regression by the time it
    # reaches the diff, so it is caught here, before anything is replayed, and
    # named for what it is.
    live_digest, digest_seconds = compute_collection_digest(live_collection)
    console.out(f"  Collection contents:      {live_digest['point_count']} points, "
          f"{live_digest['distinct_nct_ids']} distinct NCT IDs, sha256 "
          f"{live_digest['nct_id_sha256'][:16]}... ({digest_seconds}s)")

    stale = []
    for fixture in sorted(fixtures, key=lambda f: f["fixture_id"]):
        recorded = fixture["environment"].get("collection_digest")
        if recorded is None:
            # Absent is not "matched". A fixture with no digest predates this
            # check and cannot be vouched for.
            stale.append((fixture["fixture_id"], "no digest recorded", None))
        elif recorded != live_digest:
            differing = [k for k in sorted(set(recorded) | set(live_digest))
                         if recorded.get(k) != live_digest.get(k)]
            stale.append((fixture["fixture_id"], "digest mismatch", differing))

    if stale:
        console.out(f"\n[FATAL] THE INDEX CHANGED, NOT THE CODE.")
        console.out(f"        '{live_collection}' still exists under the same name, "
              f"but its contents no longer match what these fixtures were "
              f"captured against.")
        console.out(f"        Every retrieval-order difference you would see below "
              f"would be the corpus, not item 20. Re-capture with "
              f"fixture_capture.py.")
        for fixture_id, reason, fields in stale:
            console.out(f"          {fixture_id}: {reason}"
                  + (f" on {fields}" if fields else ""))
        for fixture in fixtures:
            recorded = fixture["environment"].get("collection_digest")
            if recorded and recorded != live_digest:
                console.out(f"        fixture: {recorded}")
                console.out(f"        live   : {live_digest}")
                break
        return 1

    console.out(f"  Local models:             not loaded "
          f"({DEFER_LOCAL_MODELS_ENV}=1)")
    console.out(f"  Replaying {len(fixtures)} fixture(s)\n")

    graph = build_matching_graph()

    reports = []
    with CaffeinateSession("fixture replay"):
        for fixture in sorted(fixtures, key=lambda f: f["fixture_id"]):
            report = replay_fixture(fixture, graph, root)
            print_report(report, args.max_diffs)
            reports.append(report)

    # --- Summary ------------------------------------------------------------
    console.out(f"\n{'=' * 78}\nSUMMARY\n{'=' * 78}")
    failed = 0
    for report in reports:
        n_diff = len(report["differences"])
        n_miss = len(report["replay_misses"])
        if report["fatal"]:
            status = "FATAL"
        elif n_diff or n_miss:
            status = "DIFFERS"
        else:
            status = "clean"
        if status != "clean":
            failed += 1
        detail = []
        if n_diff:
            detail.append(f"{n_diff} field(s)")
        if n_miss:
            detail.append(f"{n_miss} replay miss(es)")
        if report["parse_source"] not in ("reparsed", "rebuilt_from_recipe"):
            detail.append(report["parse_source"])
        console.out(f"  {status:<8} {report['fixture_id']:<28} "
              f"{'; '.join(detail)}")

    if load_failures:
        console.out(f"\n  {load_failures} fixture(s) could not be loaded.")

    console.out(f"\n  {len(reports) - failed}/{len(reports)} replayed clean.")

    # A STALE FILE AND A CHANGED PIPELINE ARE DIFFERENT FINDINGS WITH DIFFERENT
    # OWNERS, and one exit code for both said neither. A load failure is a file
    # lying in the directory at the wrong schema version -- nothing replayed
    # differently, and the fix is to migrate or delete it. A difference is the
    # pipeline no longer doing what it did, and the fix is to explain why.
    #
    # Differences WIN when both occur: the loud finding must not be masked by
    # the housekeeping one, and a caller branching on the code has to reach the
    # pipeline change first. The load failures are still printed above, so
    # nothing is skipped silently -- only the code collapses.
    if failed:
        exit_code, why = 1, "replay differences or misses"
    elif load_failures:
        exit_code, why = 2, "load failures only, nothing replayed differently"
    else:
        exit_code, why = 0, "clean"
    console.out(f"  exit {exit_code} ({why})")
    console.out(f"{'=' * 78}\n")

    return exit_code
#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
