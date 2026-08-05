"""The agent's one seam onto every client, model and registry it uses.

Item 20c, pass 2c. This module exists because of a defect that would otherwise
have shipped silently and billed for it.

THE DEFECT THIS REPLACES
------------------------
``45- Fixture Capture.py`` and ``46- Fixture Replay.py`` redirect the pipeline by
REBINDING NAMES IN THE SHARED EXEC NAMESPACE::

    globals()["openai_client"]      = OpenAIProxy(...)
    globals()["qdrant_client"]      = QdrantProxy(...)
    globals()["_bm25_query_model"]  = SparseModelProxy(...)
    globals()["medcpt_score_pairs"] = _recording_medcpt(...)

That worked for exactly one reason: every project file was ``exec()``'d into one
dict, so File 13's functions resolved those four names out of that dict at CALL
time. The moment File 13 becomes a module, its functions resolve them out of the
MODULE's globals instead, and a rebinding in the caller reaches none of them.

Nothing would have raised. ``46- Fixture Replay.py`` would have gone on printing
that every fixture replayed clean while sending each Stage 5 prompt to the real
OpenAI endpoint and paying for it — the fixtures exist precisely because those
calls cost money, and the replay's entire claim is that it makes none of them.
A silent regression that spends money is the worst shape a regression can have,
so the seam is now explicit, and both harnesses assert that the object the agent
actually reaches is theirs.

HOW IT WORKS
------------
Every dependency is reached through an accessor. Each accessor answers in this
order:

  1. an OVERRIDE, if one is installed for that key;
  2. a cached value, if this process already built one;
  3. the real thing, built now and cached.

So a test harness installs an override and every call site inside the agent sees
it, with no knowledge of who the caller is and no import-order requirement.

    from oncotriage.agent import deps

    deps.set_override(deps.OPENAI_CLIENT, my_proxy)     # install
    ...
    deps.clear_override(deps.OPENAI_CLIENT)             # remove

    with deps.override(deps.QDRANT_CLIENT, stub):       # scoped
        ...

``set_overrides(mapping)`` installs several at once and returns the previous
values, which is the shape ``install_recording_hooks`` / ``install_replay_hooks``
want; ``restore_overrides(saved)`` puts them back, treating ``_UNSET`` as
"there was no override, so remove the one I installed".

WHAT IS OVERRIDABLE, and why each one is on the list
----------------------------------------------------
    OPENAI_CLIENT      Stage 2's embedding call and Stage 5's chat call.
    QDRANT_CLIENT      every retrieval query and both scroll paths.
    BM25_QUERY_MODEL   the FastEmbed sparse query encoder. As of pass 3a its
                       default is NOT built here — it comes from
                       ``oncotriage.embedding``, the one construction site in
                       the package, shared with the indexer that produced the
                       document vectors it is scored against. The deferral
                       switch is still consulted here, above that delegation.
    MEDCPT_TOKENIZER   the two halves of the cross-encoder. Overridable
    MEDCPT_MODEL       separately from the scorer so a caller can replace the
                       model without replacing the scoring function.
    MEDCPT_SCORER      the whole ``(query, trial_texts) -> scores`` function.
                       This is the one Files 45 and 46 actually use: the
                       fixtures record SCORES, not tensors, so replaying at the
                       model level would mean fabricating a logits tensor and a
                       tokenizer output shape to go with it.
                       ITS DEFAULT LIVES IN ``oncotriage.agent.models``, not
                       here, because that is where ``medcpt_score_pairs`` is
                       defined and this module must not import it — ``models``
                       imports ``deps`` for the tokenizer and the model, and the
                       reverse edge would be a cycle. ``models.score_pairs()``
                       is the dispatcher: it consults this registry and falls
                       back to its own function. Every other key resolves its
                       default here.
    CANCER_REGISTRY    ``load_registry()``     — File 35 stubs this one.
    LAB_REGISTRY       ``load_lab_registry()``
    MESH_FILTER        ``load_mesh_filter()``  — File 35 stubs this one too.
                       Legitimately ``None`` when the MeSH JSON files are
                       absent, which is why the cache uses a sentinel and not
                       ``if value is None: build()``. Caching ``None`` as "not
                       built yet" would re-read four JSON lookups on every
                       Stage 1, 3 and 4 call of every patient in a 22k run.

WHAT IMPORTING THIS MODULE DOES
-------------------------------
Nothing. No client is constructed, no model is downloaded or loaded, no registry
is built, no JSON is read, no path is resolved. Every accessor above is lazy and
``47- Package Split Test.py`` section 2 imports this module — and the eleven
others — under traps on ``open``, ``io.open``, ``socket.socket``,
``socket.create_connection`` and ``sqlite3.connect`` that are fired afterwards
to show they were armed.

That is a change from File 13, which loaded MedCPT (~110 MB, tens of seconds)
and FastEmbed AT EXEC TIME, lines 414-434. Twelve files chain File 13; every one
of them paid for both models just by being read, including the six that never
score a pair. ``ONCOTRIAGE_DEFER_LOCAL_MODELS`` existed to buy that back for the
replay harness, and it is kept — but it no longer decides anything at import,
because there is nothing at import to decide.

THE ENVIRONMENT VARIABLE IS STILL READ AT IMPORT, deliberately
--------------------------------------------------------------
``_DEFER_LOCAL_MODELS`` is evaluated once, here, from the same expression File 13
used at its line 386. It is CONSULTED lazily, on first model access. Reading it
at import rather than at first use keeps the contract callers already have —
``46- Fixture Replay.py`` sets it before the chain runs and says in a comment
that setting it later would be too late — and keeps one source of truth for a
value that must not change mid-process. An override always wins over it.
"""

import os
import threading

from oncotriage import config, embedding
from oncotriage.registries.cancer_code_registry import load_lab_registry, load_registry
from oncotriage.registries.mesh import load_mesh_filter


#------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Override keys
# ---------------------------------------------------------------------------
# Named constants rather than bare strings so a caller cannot install an
# override under a misspelled key and have it silently ignored — set_override
# rejects any key not in OVERRIDE_KEYS.

OPENAI_CLIENT = "openai_client"
QDRANT_CLIENT = "qdrant_client"
BM25_QUERY_MODEL = "bm25_query_model"
MEDCPT_TOKENIZER = "medcpt_tokenizer"
MEDCPT_MODEL = "medcpt_model"
MEDCPT_SCORER = "medcpt_scorer"
CANCER_REGISTRY = "cancer_registry"
LAB_REGISTRY = "lab_registry"
MESH_FILTER = "mesh_filter"

OVERRIDE_KEYS = (
    OPENAI_CLIENT,
    QDRANT_CLIENT,
    BM25_QUERY_MODEL,
    MEDCPT_TOKENIZER,
    MEDCPT_MODEL,
    MEDCPT_SCORER,
    CANCER_REGISTRY,
    LAB_REGISTRY,
    MESH_FILTER,
)
"""Every key an override may be installed under. A caller that passes anything
else gets a KeyError naming the valid set, because the failure mode of a
silently-ignored override is a test that reports a pass while talking to the
real endpoint."""


class _Unset:
    """Sentinel type. Distinct from None, which is a legitimate MESH_FILTER."""

    def __repr__(self):
        return "<deps.UNSET>"


UNSET = _Unset()
"""Returned by get_override() when no override is installed, and accepted by
restore_overrides() to mean "remove it again"."""


#------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The override registry
# ---------------------------------------------------------------------------
#
# One lock guards both the overrides and the lazy caches. It is an RLock because
# an accessor holds it while calling a factory, and a factory may reach another
# accessor: get_medcpt_scorer's default path is in models, but a future default
# that composed two dependencies would deadlock on a plain Lock.
#
# The lock is not about the override dict, which is only written by test
# harnesses on one thread. It is about the CACHES: 25- Batch Runner.py drives
# twelve worker threads through this seam, and two of them entering
# get_qdrant_client() together must not build two clients — a second client is
# a second connection pool, and the per-patient latency figures in inferences.db
# would then describe two different transports with nothing in the row saying so.

_LOCK = threading.RLock()
_OVERRIDES = {}
_CACHE = {}


def set_override(key, value):
    """Install `value` as the answer for `key`. Returns the previous override.

    The previous value is UNSET when none was installed, which is what
    restore_overrides() needs in order to distinguish "put the old override
    back" from "remove mine".
    """
    if key not in OVERRIDE_KEYS:
        raise KeyError(
            f"unknown dependency override key {key!r}; valid keys are "
            f"{', '.join(OVERRIDE_KEYS)}"
        )
    with _LOCK:
        previous = _OVERRIDES.get(key, UNSET)
        _OVERRIDES[key] = value
        return previous


def clear_override(key):
    """Remove the override for `key`. Returns the removed value, or UNSET."""
    if key not in OVERRIDE_KEYS:
        raise KeyError(
            f"unknown dependency override key {key!r}; valid keys are "
            f"{', '.join(OVERRIDE_KEYS)}"
        )
    with _LOCK:
        return _OVERRIDES.pop(key, UNSET)


def get_override(key):
    """The override installed for `key`, or UNSET.

    Used by ``oncotriage.agent.models`` to dispatch MEDCPT_SCORER, whose default
    cannot live here. Everything else goes through a typed accessor below.
    """
    if key not in OVERRIDE_KEYS:
        raise KeyError(
            f"unknown dependency override key {key!r}; valid keys are "
            f"{', '.join(OVERRIDE_KEYS)}"
        )
    with _LOCK:
        return _OVERRIDES.get(key, UNSET)


def set_overrides(mapping):
    """Install several overrides at once. Returns {key: previous}.

    The return value is exactly what restore_overrides() takes, so an install /
    restore pair is two lines and cannot get out of step.
    """
    return {key: set_override(key, value) for key, value in mapping.items()}


def restore_overrides(saved):
    """Undo a set_overrides(), removing any that had no previous value."""
    for key, previous in saved.items():
        if isinstance(previous, _Unset):
            clear_override(key)
        else:
            set_override(key, previous)


def active_overrides():
    """The keys currently overridden, sorted. For a harness to report on.

    Returns the KEYS, not the values: a proxy's repr is not something a log line
    should carry, and a caller that wants the object asks the accessor for it —
    which is the same question the agent asks, and therefore the right one to
    assert on.
    """
    with _LOCK:
        return sorted(_OVERRIDES)


class override:
    """Context manager installing one override for the duration of a block.

    Usage:
        with deps.override(deps.MESH_FILTER, stub):
            ...
    """

    def __init__(self, key, value):
        self._key = key
        self._value = value
        self._previous = UNSET

    def __enter__(self):
        self._previous = set_override(self._key, self._value)
        return self._value

    def __exit__(self, *exc):
        if isinstance(self._previous, _Unset):
            clear_override(self._key)
        else:
            set_override(self._key, self._previous)
        return False


def _resolve(key, factory):
    """Override, else cached, else build once and cache. ALL UNDER THE LOCK.

    THE UNLOCKED FAST PATH THIS REPLACES (pass 20c-3a).
    -------------------------------------------------
    Pass 2c read ``_OVERRIDES`` and then ``_CACHE`` OUTSIDE the lock, and took
    the lock only to build. The argument written beside it was that the dict
    read is atomic under the GIL and an override is installed exactly once, by
    a test harness, on one thread. Both halves of that are true and neither is
    sufficient:

      * ``_OVERRIDES.get`` being atomic says the reader sees either the old
        value or the new one. It says nothing about the SEQUENCE. A thread that
        reads ``_OVERRIDES`` (absent), is descheduled, and resumes after another
        thread has installed an override goes on to read ``_CACHE`` and returns
        the REAL client while an override is installed. That is the exact
        failure this module exists to prevent -- a live OpenAI call inside a
        harness that reports it made none -- and it is silent.
      * "one harness, one thread" is a property of today's five callers, not of
        the seam. ``25- Batch Runner.py`` drives MAX_WORKERS = 12 threads through
        every accessor here, and pass 20c-3a puts the indexer and the validator
        on the same seam. A correctness argument that rests on nobody else ever
        calling this is not a correctness argument.

    So the whole sequence is inside the lock. It is an RLock, so the factory
    calling back into another accessor still works, and the cost is one
    uncontended acquire per dependency read -- nanoseconds against a Qdrant
    round trip.
    """
    with _LOCK:
        value = _OVERRIDES.get(key, UNSET)
        if not isinstance(value, _Unset):
            return value

        cached = _CACHE.get(key, UNSET)
        if not isinstance(cached, _Unset):
            return cached

        built = factory()
        _CACHE[key] = built
        return built


#------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Local model deferral
# ---------------------------------------------------------------------------
#
# Lifted from "13- LangGraph Agent.py" lines 385-411, logic unchanged. What
# changed is WHEN it is consulted: File 13 branched on it at exec time and bound
# either the placeholders or the real models; here it is read on first model
# ACCESS, because there is no model load at import to branch over.
#
# The comment File 13 carried is still true and still the reason this exists:
#
#   The two local models are loaded at exec() time and cost tens of seconds plus
#   a few hundred MB. A replay harness that serves every model output from a
#   recording (46- Fixture Replay.py) needs neither, and "loads no model" is
#   part of what makes a replay a replay rather than a second run.
#
#   Opt-in only, and never in production: the variable is read once, here, and
#   the default is to load. When it is set, both names are bound to a sentinel
#   that RAISES on any use rather than to None, so a caller that forgot to
#   install its own stand-in fails with a sentence explaining why instead of an
#   AttributeError on None thirty frames down.
#
#   Environment rather than 03- Config.py deliberately. It is not a tunable --
#   it selects between two ways of running the process, has to be decided before
#   this file is exec'd, and a value accidentally left in the config file would
#   silently disarm the pipeline for every caller.
#
# It is now the SECOND line of defence rather than the first. The first is the
# override: a harness that installs MEDCPT_SCORER never reaches a model at all,
# whatever this variable says. Both are kept because they fail differently --
# the variable turns a forgotten stand-in into a named RuntimeError, the
# override makes the model unreachable in the first place.

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


#------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------
#
# THE THIRD-PARTY IMPORTS ARE INSIDE THESE FUNCTIONS, and that is deliberate and
# exempt from the package's no-deferred-import rule, which covers
# oncotriage-to-oncotriage edges only. `transformers` pulls in torch;
# `fastembed` pulls in onnxruntime and a tokenizer. Hoisting either to the
# module's import block would make `import oncotriage.agent.deps` — which
# `oncotriage.agent.retrieval` does, which the FastAPI server does — load
# hundreds of megabytes before serving its first request, which is the exact
# cost this pass removes. It is the same exemption, for the same reason, as
# `import icd10` inside _build_icd10_cancer_sets().


def _build_medcpt_tokenizer():
    if _DEFER_LOCAL_MODELS:
        print(f"{DEFER_LOCAL_MODELS_ENV}=1 — skipping the MedCPT tokenizer load.")
        return _DeferredLocalModel("MedCPT tokenizer")

    from transformers import AutoTokenizer

    print("Loading MedCPT cross-encoder tokenizer...")
    tokenizer = AutoTokenizer.from_pretrained("ncbi/MedCPT-Cross-Encoder")
    print("MedCPT tokenizer loaded!")
    return tokenizer


def _build_medcpt_model():
    if _DEFER_LOCAL_MODELS:
        print(f"{DEFER_LOCAL_MODELS_ENV}=1 — skipping the MedCPT cross-encoder load.")
        return _DeferredLocalModel("MedCPT cross-encoder")

    from transformers import AutoModelForSequenceClassification

    # Using transformers directly instead of the sentence-transformers
    # CrossEncoder wrapper because: (1) MedCPT's official usage is via
    # AutoModelForSequenceClassification, (2) the CrossEncoder wrapper applies a
    # default sigmoid that squashes MedCPT's raw values, range -25 to 25.
    print("Loading MedCPT cross-encoder re-ranker...")
    model = AutoModelForSequenceClassification.from_pretrained("ncbi/MedCPT-Cross-Encoder")
    model.eval()
    print("MedCPT re-ranker loaded!\n")
    return model


def _build_bm25_query_model():
    # THE MODEL IS NOT CONSTRUCTED HERE ANY MORE (pass 20c-3a). It comes from
    # oncotriage.embedding, which is the ONE construction site in the package.
    #
    # It used to be built here, and File 11 built a second one at index time
    # from the same model name -- the two halves of one job, wired up
    # independently. BM25 sparse vectors are token-ID vectors over the model's
    # vocabulary, so a change on one side silently scores queries against the
    # wrong terms: Qdrant returns results, nothing raises, no counter moves, and
    # only retrieval quality falls. File 13's own comment said the two were the
    # same model; nothing enforced it. Now one accessor does, and
    # "47- Package Split Test.py" section 2f asserts the construction count is
    # exactly 1.
    #
    # THE DEFERRAL CHECK STAYS HERE, ABOVE THE DELEGATION, and that is the whole
    # reason this function survives rather than get_bm25_query_model pointing
    # straight at embedding. The switch is about the AGENT's replay path. The
    # indexer has no replay path, and an index built from a placeholder would be
    # written to Qdrant and swapped onto the live alias looking exactly like a
    # real one.
    if _DEFER_LOCAL_MODELS:
        print(f"{DEFER_LOCAL_MODELS_ENV}=1 — skipping the FastEmbed BM25 load.")
        return _DeferredLocalModel("FastEmbed BM25 query model")

    return embedding.get_bm25_sparse_model()


#------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Accessors — the only way the agent reaches any of these
# ---------------------------------------------------------------------------

def get_openai_client():
    """The OpenAI client Stage 2's embedding and Stage 5's chat call use."""
    return _resolve(OPENAI_CLIENT, config.get_openai_client)


def get_qdrant_client():
    """The Qdrant client every retrieval query and both scroll paths use."""
    return _resolve(QDRANT_CLIENT, config.get_qdrant_client)


def get_bm25_query_model():
    """The FastEmbed sparse query encoder. Loaded on first use, never at import."""
    return _resolve(BM25_QUERY_MODEL, _build_bm25_query_model)


def get_medcpt_tokenizer():
    """The MedCPT tokenizer. Loaded on first use, never at import."""
    return _resolve(MEDCPT_TOKENIZER, _build_medcpt_tokenizer)


def get_medcpt_model():
    """The MedCPT cross-encoder. Loaded on first use, never at import."""
    return _resolve(MEDCPT_MODEL, _build_medcpt_model)


def get_cancer_registry():
    """CancerCodeRegistry — primary-cancer detection over SNOMED / ICD-10."""
    return _resolve(CANCER_REGISTRY, load_registry)


def get_lab_registry():
    """OncologyLabRegistry — the LOINC filter for oncology-relevant labs."""
    return _resolve(LAB_REGISTRY, load_lab_registry)


def get_mesh_filter():
    """MeSHCancerFilter, or None when the MeSH JSON lookups are absent.

    None is a REAL ANSWER here, not "not built yet" — every caller inside the
    agent branches on `is None` and records MESH_FILTER_SKIP_NO_FILTER when it
    is. _resolve()'s sentinel is what keeps a legitimate None cached instead of
    re-reading four JSON files on every Stage 1, 3 and 4 call.
    """
    return _resolve(MESH_FILTER, load_mesh_filter)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug  5 2026

@author: ramyalsaffar
"""
