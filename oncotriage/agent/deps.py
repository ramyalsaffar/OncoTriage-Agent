"""The agent's one seam onto every client, model and registry it uses.

Item 20c, pass 2c. This module exists because of a defect that would otherwise
have shipped silently and billed for it.

THE DEFECT THIS REPLACES
------------------------
``fixture_capture.py`` and ``fixture_replay.py`` redirect the pipeline by
REBINDING NAMES IN THE SHARED EXEC NAMESPACE::

    globals()["openai_client"]      = OpenAIProxy(...)
    globals()["qdrant_client"]      = QdrantProxy(...)
    globals()["_bm25_query_model"]  = SparseModelProxy(...)
    globals()["medcpt_score_pairs"] = _recording_medcpt(...)

That worked for exactly one reason: every project file was ``exec()``'d into one
dict, so File 13's functions resolved those four names out of that dict at CALL
time. The moment File 13 becomes a module, its functions resolve them out of the
MODULE's globals instead, and a rebinding in the caller reaches none of them.

Nothing would have raised. ``fixture_replay.py`` would have gone on printing
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

ASKING WITHOUT BUILDING (pass 20c-3b). ``peek``, ``resolution_state``,
``is_resolved`` and ``cached_keys`` answer "what is installed for this key right
now" WITHOUT calling a factory. A query that resolves is a query that downloads
and loads MedCPT, on the DIAGNOSTIC path, at the moment someone is already
confused — the tool used to inspect the state must not be the thing that changes
it. See the block above ``_resolve`` for the full argument.

They were built for File 13's shim, whose ``_LazyAgentDependency.__repr__``
rendered itself from them, and pass 20e deleted that shim. The reason they
outlive it is the same reason they were right there: any caller asking "what is
installed for this key" — a debugger, a log line, a harness reporting what it
redirected — must be able to ask without paying ~110 MB for the answer.
``RESOLUTION_STATES`` is the closed set of values ``resolution_state`` can
return, so a caller may branch on it exhaustively;
``tests/test_package_invariants.py`` section 5c holds it to that.

WHAT DIED WITH FILE 13'S SHIM, AND WHY NONE OF IT NEEDS REBUILDING (pass 20e)
-----------------------------------------------------------------------------
The shim carried two mechanisms that existed ONLY because an exec-chain caller
could reach into its namespace. Both are recorded here rather than deleted with
the file, because a deleted argument is how a defect returns.

``_LazyAgentDependency`` bound ``medcpt_tokenizer``, ``medcpt_model`` and
``_bm25_query_model`` in the shim's namespace to proxies that resolved through
the accessors below on first use. It existed because an exec-chain caller reads
a NAME out of a namespace and cannot call an accessor, and because binding the
real objects eagerly would have loaded MedCPT (~110 MB) and FastEmbed for the
seven files that chained File 13 and never scored a pair. There is no exec-chain
caller now: ``12- RAG Trial Indexer Validator.py`` and every test call
``get_medcpt_tokenizer()`` / ``get_medcpt_model()`` / ``get_bm25_query_model()``
directly, which is lazier than the proxy was and cannot answer wrongly about the
object it wraps. THE RULE THE PROXY TAUGHT STILL APPLIES ANYWHERE ONE IS
REINTRODUCED: CPython looks an implicit special method up on the TYPE, never
through ``__getattr__``, so a proxy forwarding only ``__getattr__`` and
``__call__`` answers ``bool()``, ``==``, ``len``, ``iter``, ``in`` and ``repr``
about ITSELF — confidently, and wrongly — about an object it never consulted.

``_assert_no_legacy_rebinding()`` refused to run the pipeline if any of nine
names in the shim's namespace had been rebound, and named the ``deps`` key to
use instead. It was the answer to "a caller redirects the agent the old way and
nothing says so", and it could only ever see rebindings in that one namespace.
With the namespace gone the failure mode is gone with it: there is nowhere left
to rebind. The seam below is now the ONLY way to redirect anything the agent
reaches, ``OVERRIDE_KEYS`` is closed so an unknown key raises rather than being
silently ignored, and both fixture harnesses assert BY IDENTITY that the object
``deps`` hands the agent is theirs — ``fixture_replay.py`` running that
assertion as a negative control first, with no override installed, and refusing
to replay unless it fails.

WHAT IS OVERRIDABLE, and why each one is on the list
----------------------------------------------------
    OPENAI_CLIENT      Stage 2's embedding call and Stage 5's chat call.
    BEDROCK_CLIENT     Stage 5's Responses call when MATCHING_PROVIDER is
                       "bedrock". A SEPARATE key from OPENAI_CLIENT because
                       both are live in that configuration -- Stage 2 still
                       embeds through OpenAI -- so one key could not redirect
                       the judge without also redirecting the embeddings.
                       Unreachable with the flag at its default.
    BEDROCK_ANTHROPIC_CLIENT
                       Stage 5's Converse call when MATCHING_PROVIDER is
                       "bedrock_anthropic". A THIRD key rather than a reuse of
                       BEDROCK_CLIENT because the two hold objects of different
                       TYPES -- an openai.OpenAI and a botocore client -- so
                       one installed where the other was expected fails inside
                       the call rather than at the seam. Unreachable with the
                       flag at its default.
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
                       Legitimately ``None``, which is why the cache uses a
                       sentinel and not ``if value is None: build()``. Caching
                       ``None`` as "not built yet" would re-read four JSON
                       lookups on every Stage 1, 3 and 4 call of every patient
                       in a 22k run.
                       WHERE THE None COMES FROM CHANGED IN ITEM 11a. It used
                       to arrive on its own from missing MeSH JSON files:
                       ``load_mesh_filter()`` printed a warning, returned None,
                       and Stage 4 stopped filtering by cancer site for the
                       whole run. That function RAISES now unless
                       ONCOTRIAGE_ALLOW_DEGRADED_REGISTRIES is set. So None is
                       still a real, reachable, tested state — every branch on
                       ``is None`` stays, and File 37 installs exactly this
                       override — but it is now always somebody's decision:
                       an override installed here, or that variable set.

WHAT IMPORTING THIS MODULE DOES
-------------------------------
Nothing. No client is constructed, no model is downloaded or loaded, no registry
is built, no JSON is read, no path is resolved. Every accessor above is lazy and
``tests/test_package_invariants.py`` section 2 imports this module — and the eleven
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
``fixture_replay.py`` sets it before the chain runs and says in a comment
that setting it later would be too late — and keeps one source of truth for a
value that must not change mid-process. An override always wins over it.
"""

import os
import threading
from collections import Counter

from oncotriage import config, embedding, paths
from oncotriage.observability import console, get_logger
from oncotriage.registries.cancer_code_registry import load_lab_registry, load_registry
from oncotriage.registries.mesh import load_mesh_filter


log = get_logger(__name__)


#------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Override keys
# ---------------------------------------------------------------------------
# Named constants rather than bare strings so a caller cannot install an
# override under a misspelled key and have it silently ignored — set_override
# rejects any key not in OVERRIDE_KEYS.

OPENAI_CLIENT = "openai_client"
BEDROCK_CLIENT = "bedrock_client"
BEDROCK_ANTHROPIC_CLIENT = "bedrock_anthropic_client"
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
    BEDROCK_CLIENT,
    BEDROCK_ANTHROPIC_CLIENT,
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


# ---------------------------------------------------------------------------
# Asking WITHOUT building
# ---------------------------------------------------------------------------
#
# Every accessor below BUILDS when it has to. These three do not, ever, and
# that is their whole reason for existing.
#
# WHAT THEY FIX (pass 20c-3b). "13- LangGraph Agent.py" binds three names --
# medcpt_tokenizer, medcpt_model, _bm25_query_model -- to a _LazyAgentDependency
# proxy, and pass 3a made that proxy's __repr__ delegate to the wrapped object.
# The argument was honesty: a proxy that printed "<lazy MedCPT cross-encoder>"
# while handing the agent a fixture stub is lying at the one moment a person is
# looking. The argument was right about the goal and wrong about the mechanism,
# because delegating __repr__ means REPR TRIGGERS A BUILD:
#
#   * a debugger that renders locals, a logging call that formats the object, or
#     a bare `medcpt_model` typed at a prompt now downloads and loads ~110 MB;
#   * and having paid for it, prints transformers' multi-thousand-line module
#     tree, which is not what anyone typing `medcpt_model` wanted to read;
#   * worst, it happens on the DIAGNOSTIC path -- the moment someone is already
#     confused -- so the tool used to inspect the state changes the state.
#
# A repr must be free of side effects. So the proxy asks these instead: what key
# am I, and has anything answered for it yet. Both questions are answerable from
# the two dicts, under the lock, with no factory call.
#
# THEY ARE DIAGNOSTIC, NOT AN ACCESS PATH. `peek()` returns the live object when
# there is one, which makes it tempting as a "cheap get". It is not one: it
# returns UNSET when nothing is built, so a caller using it as an accessor gets
# a sentinel instead of a client and will not find out until it dereferences it.
# Every real consumer inside the agent calls a typed accessor.

RESOLVED_OVERRIDE = "override"
RESOLVED_CACHED = "cached"
RESOLVED_UNRESOLVED = "unresolved"

RESOLUTION_STATES = (RESOLVED_OVERRIDE, RESOLVED_CACHED, RESOLVED_UNRESOLVED)
"""Every value resolution_state() can return. Closed, so a caller can branch on
it exhaustively."""


def peek(key):
    """The value `key` would answer with RIGHT NOW, or UNSET. NEVER builds.

    Distinguishes "cached as None" from "nothing built" by returning the UNSET
    sentinel for the latter -- MESH_FILTER is legitimately None, so a bare
    ``is None`` test cannot separate the two.
    """
    if key not in OVERRIDE_KEYS:
        raise KeyError(
            f"unknown dependency override key {key!r}; valid keys are "
            f"{', '.join(OVERRIDE_KEYS)}"
        )
    with _LOCK:
        value = _OVERRIDES.get(key, UNSET)
        if not isinstance(value, _Unset):
            return value
        return _CACHE.get(key, UNSET)


def resolution_state(key):
    """Whether `key` is answered by an override, by the cache, or not yet.

    One of RESOLUTION_STATES. NEVER builds. This is the question __repr__ asks.
    """
    if key not in OVERRIDE_KEYS:
        raise KeyError(
            f"unknown dependency override key {key!r}; valid keys are "
            f"{', '.join(OVERRIDE_KEYS)}"
        )
    with _LOCK:
        if key in _OVERRIDES:
            return RESOLVED_OVERRIDE
        if key in _CACHE:
            return RESOLVED_CACHED
        return RESOLVED_UNRESOLVED


def is_resolved(key):
    """True when reading `key` would return without building. NEVER builds."""
    return resolution_state(key) != RESOLVED_UNRESOLVED


def cached_keys():
    """Keys this process has BUILT, sorted. Excludes overrides, which were not
    built by anyone here. Diagnostic; pairs with active_overrides()."""
    with _LOCK:
        return sorted(_CACHE)


#------------------------------------------------------------------------------


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
#   recording (fixture_replay.py) needs neither, and "loads no model" is
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
            f"when oncotriage.agent.deps was first imported, and nothing "
            f"replaced the placeholder before {how}. A replay harness must "
            f"install its own stand-in through deps.set_override(); a "
            f"production run must not set that variable."
        )

    def __getattr__(self, attr):
        self._explode(f"attribute {attr!r} was read")

    def __call__(self, *args, **kwargs):
        self._explode("it was called")


#------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The cross-encoder's sequence limit, verified against the checkpoint
# ---------------------------------------------------------------------------
#
# WHY THIS EXISTS. config.CROSS_ENCODER_MAX_LENGTH is a property OF
# config.CROSS_ENCODER_MODEL -- MedCPT is BERT-shaped and carries 512 learned
# position embeddings -- and the two are separate lines that can be edited
# apart. The failure that produces is the one this whole module is about: every
# tokenizer call passes `truncation=True`, so transformers does exactly what the
# number says and raises nothing, Stage 3 keeps returning scores, the Stage 4
# quality gate keeps cutting, and the only symptom of a limit that has stopped
# matching its checkpoint is that the ranking got worse. The same sentence, one
# level down, as the tokenizer/weights hazard argued at _build_medcpt_tokenizer.
#
# WHAT EACH HALF ACTUALLY DECLARES, MEASURED 2026-08-21 against the cached
# ncbi/MedCPT-Cross-Encoder rather than assumed. This is the decisive fact and
# it is why there are two call sites rather than one:
#
#   tokenizer.model_max_length          1000000000000000019884624838656
#                                       == transformers.VERY_LARGE_INTEGER,
#                                       i.e. NO declared limit
#   model.config.max_position_embeddings 512
#
# So a check written only against the tokenizer -- the obvious place, since the
# tokenizer is what takes `max_length` -- would take the "undeclared" branch on
# every load of the shipped checkpoint and verify NOTHING, forever. It would be
# a check that has stopped checking while looking exactly like one that passes.
# The WEIGHTS are what verify this number; the tokenizer half is kept because a
# checkpoint that DOES declare a model_max_length is then covered too, and a
# tokenizer whose declared limit contradicts the weights' is a real defect.
#
# UNDECLARED IS NOT A MISMATCH, and the distinction is the reason this is not
# one `!=`. A placeholder means "this checkpoint does not state a limit", which
# is a different fact from "this checkpoint states a different limit" and has a
# different remedy: nobody can make a vendor declare one, so raising would make
# the pipeline unrunnable against a whole class of perfectly good checkpoints.
# It is COUNTED, on the AGE_PARSE_FAILURES footing -- third-party DATA counts,
# configuration raises (item 11a) -- and never silent.
#
# A DECLARED MISMATCH RAISES, in BOTH directions, and that is a decision rather
# than an oversight:
#
#   configured > declared   the model is handed positions it has no embedding
#                           for. Loud but LATE: an IndexError out of the
#                           embedding lookup, per patient, thirty frames inside
#                           Stage 3, after Stage 2 has already spent its
#                           embedding call.
#   configured < declared   silent. Every pair is cut shorter than the model
#                           could read and only the ranking says so.
#
# Raising covers both at the one moment a process can still be fixed cheaply --
# first model load, before Stage 3 scores anything and before Stage 5 spends a
# cent -- and it costs one run rather than one class of runs. A deliberately
# smaller budget is a SECOND named constant in config with its own measurement
# (the RRF_K precedent), not a quiet inequality here.
#
# IT IS A RuntimeError SUBCLASS AND DELIBERATELY NOT A ValueError, on the
# UnknownModelPricingError / IndexVerificationError precedent: a stray
# `except ValueError` around a model load must not be able to eat it.
#
# NOTHING HERE ADDS A LOAD TO A DEFERRED PATH. Both callers run BELOW their own
# _DEFER_LOCAL_MODELS early return, so a deferred process never reaches this
# function; and an installed override short-circuits the factory in _resolve
# above, so a harness that supplies its own tokenizer never reaches it either.
# The verifier reads an attribute off an object the caller already built and
# imports nothing.


class CrossEncoderLimitMismatchError(RuntimeError):
    """config.CROSS_ENCODER_MAX_LENGTH contradicts the loaded checkpoint."""


CROSS_ENCODER_LIMIT_DEGRADATIONS = Counter()
"""Times the cross-encoder's sequence limit could not be VERIFIED, by cause.

A non-zero count is not a mismatch -- a mismatch raises. It means the loaded
object declined to state a limit (`undeclared_placeholder`, which is what the
shipped MedCPT tokenizer does on every load) or stated one this code could not
read (`unreadable:<type>`), so config.CROSS_ENCODER_MAX_LENGTH went unchecked
against that half. Keyed by cause and the source attribute, never by a value.

WHEN ONLY `undeclared_*` KEYS ARE PRESENT AND BOTH HALVES ARE IN THEM, nothing
in the process verified the limit at all. That is the state worth noticing in
the run-end report, and it is why the two sources are separate keys.
"""

# transformers signals "no limit declared" with VERY_LARGE_INTEGER, int(1e30).
# The test is a FLOOR rather than an equality against that exact value: the
# vendor may change the sentinel, and no transformer's positional budget is
# within nine orders of magnitude of this, so a floor cannot be reached by a
# genuine limit while surviving a sentinel that moves.
_UNDECLARED_LIMIT_FLOOR = 10 ** 12

LIMIT_VERIFIED = "verified"
LIMIT_UNDECLARED = "undeclared"
LIMIT_UNREADABLE = "unreadable"

LIMIT_VERIFICATION_STATES = (LIMIT_VERIFIED, LIMIT_UNDECLARED, LIMIT_UNREADABLE)
"""Every value _verify_cross_encoder_sequence_limit can RETURN.

Closed, so a caller may branch on it exhaustively, and declared here rather
than left implicit for the same reason RESOLUTION_STATES is -- a vocabulary
nobody can enumerate is one nobody can be sure they have handled.
A fourth outcome is not in it: a declared mismatch RAISES.
"""


def _verify_cross_encoder_sequence_limit(declared, source, checkpoint):
    """Check one declaration against config.CROSS_ENCODER_MAX_LENGTH.

    `declared` is whatever the loaded object says its sequence budget is --
    `tokenizer.model_max_length` or `model.config.max_position_embeddings` --
    and `source` names which, so a counter key and a message say WHICH half
    could not be verified rather than only that something could not.

    Returns one of LIMIT_VERIFICATION_STATES. Raises
    CrossEncoderLimitMismatchError when the checkpoint declares a limit and it
    is not the configured one; the full argument is in the block above.

    `bool` is rejected with the non-integers on purpose: True == 1 in Python, so
    a tokenizer attribute that had somehow become a flag would otherwise be
    compared as the number 1 and reported as a mismatch against 512, which
    names the wrong defect.
    """
    configured = config.CROSS_ENCODER_MAX_LENGTH

    if declared is None:
        CROSS_ENCODER_LIMIT_DEGRADATIONS[f"undeclared_missing:{source}"] += 1
        log.warning("the cross-encoder checkpoint declares no sequence limit "
                    "here, so the configured one went unverified against it",
                    event="cross_encoder_limit_unverified",
                    status=LIMIT_UNDECLARED, model=checkpoint,
                    max_length_source=source,
                    max_length_configured=configured,
                    reason="attribute absent")
        return LIMIT_UNDECLARED

    if isinstance(declared, bool) or not isinstance(declared, int):
        CROSS_ENCODER_LIMIT_DEGRADATIONS[
            f"unreadable:{source}:{type(declared).__name__}"] += 1
        log.warning("the cross-encoder checkpoint declared a sequence limit "
                    "this code could not read, so the configured one went "
                    "unverified against it",
                    event="cross_encoder_limit_unverified",
                    status=LIMIT_UNREADABLE, model=checkpoint,
                    max_length_source=source,
                    max_length_configured=configured,
                    reason=type(declared).__name__)
        return LIMIT_UNREADABLE

    if declared >= _UNDECLARED_LIMIT_FLOOR:
        CROSS_ENCODER_LIMIT_DEGRADATIONS[f"undeclared_placeholder:{source}"] += 1
        log.warning("the cross-encoder checkpoint declares no sequence limit "
                    "here -- the transformers placeholder -- so the configured "
                    "one went unverified against it",
                    event="cross_encoder_limit_unverified",
                    status=LIMIT_UNDECLARED, model=checkpoint,
                    max_length_source=source,
                    max_length_configured=configured,
                    reason="transformers placeholder")
        return LIMIT_UNDECLARED

    if declared != configured:
        raise CrossEncoderLimitMismatchError(
            f"config.CROSS_ENCODER_MAX_LENGTH is {configured}, but the loaded "
            f"checkpoint {checkpoint!r} declares {declared} at {source}. Those "
            f"two constants are one fact: CROSS_ENCODER_MAX_LENGTH is the "
            f"sequence budget OF CROSS_ENCODER_MODEL, so a checkpoint change "
            f"has to move both. Nothing downstream would report this -- every "
            f"tokenizer call passes truncation=True, so a configured limit "
            f"below the checkpoint's silently feeds the cross-encoder less of "
            f"every trial and only the ranking degrades, and one above it "
            f"fails later, per patient, inside Stage 3. Fix the constant in "
            f"oncotriage/config.py; if a smaller budget is wanted on purpose, "
            f"add a second named constant there with the measurement that "
            f"justifies it rather than lowering this one."
        )

    log.info("the cross-encoder sequence limit matches the checkpoint",
             event="cross_encoder_limit_verified", status=LIMIT_VERIFIED,
             model=checkpoint, max_length_source=source,
             max_length_configured=configured, max_length_declared=declared)
    return LIMIT_VERIFIED


#------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The cross-encoder's numeric precision, verified against the loaded weights
# ---------------------------------------------------------------------------
#
# WHY THIS EXISTS, AND WHY IT DID NOT BEFORE THE transformers 5.x UPGRADE.
# 4.x forced float32 on every load that did not ask for something else, so the
# precision of Stage 3 was a property of the LIBRARY and there was nothing here
# to own. 5.0.0 changed the default to `"auto"` -- resolved from the
# checkpoint's own config.json, else from the first floating-point weight -- so
# it became a property of a JSON FILE ON A THIRD-PARTY HUB. The full argument,
# with the release note it comes from and the date, is at
# config.CROSS_ENCODER_DTYPE.
#
# THE FAILURE IS THE ONE THIS MODULE IS ALREADY FULL OF: silent. A checkpoint
# republished at bfloat16 loads without a word, scores every pair, and ranks --
# only worse. So the dtype is DECLARED and PASSED rather than inherited, and
# what came back is checked against what was asked for.
#
# MISMATCH RAISES; UNREADABLE COUNTS. That is item 11a's line applied here
# exactly as the sequence-limit verifier above applies it. A mismatch means
# transformers accepted `dtype=` and did something else, which is a library
# contract break that would put every stored score at a precision the record
# does not name -- loud, at first load, before Stage 5 spends a cent. An
# ABSENT `model.dtype` is a third-party object declining to say, which no edit
# here can fix, so it is counted and named.


class CrossEncoderDtypeMismatchError(RuntimeError):
    """The loaded cross-encoder is not in config.CROSS_ENCODER_DTYPE."""


CROSS_ENCODER_DTYPE_DEGRADATIONS = Counter()
"""Times the cross-encoder's loaded precision could not be VERIFIED, by cause.

A non-zero count is not a mismatch -- a mismatch raises. It means the loaded
object declined to report a dtype (`unreported`) or reported one this code
could not read (`unreadable:<type>`), so config.CROSS_ENCODER_DTYPE went
unchecked against the weights that were actually loaded and every Stage 3
score of that run was produced at a precision nothing confirmed.
"""

DTYPE_VERIFIED = "verified"
DTYPE_UNREPORTED = "unreported"
DTYPE_UNREADABLE = "unreadable"

DTYPE_VERIFICATION_STATES = (DTYPE_VERIFIED, DTYPE_UNREPORTED, DTYPE_UNREADABLE)
"""Every value _verify_cross_encoder_dtype can RETURN.

Closed, so a caller may branch on it exhaustively, and declared for the same
reason LIMIT_VERIFICATION_STATES is. A fourth outcome is not in it: a mismatch
RAISES.
"""


def _normalise_dtype_name(value):
    """``torch.float32`` / ``"torch.float32"`` / ``"float32"`` -> ``"float32"``.

    A str() of a torch.dtype is ``"torch.float32"``; config names the bare
    ``"float32"`` because that is the spelling transformers accepts as a string
    argument. Both are the same fact, and comparing them as written would
    report a mismatch that is not one.
    """
    text = value if isinstance(value, str) else str(value)
    prefix = "torch."
    return text[len(prefix):] if text.startswith(prefix) else text


def _verify_cross_encoder_dtype(model, checkpoint):
    """Check the loaded model's precision against config.CROSS_ENCODER_DTYPE.

    Returns one of DTYPE_VERIFICATION_STATES. Raises
    CrossEncoderDtypeMismatchError when the model reports a precision that is
    not the configured one; the full argument is in the block above.
    """
    configured = config.CROSS_ENCODER_DTYPE
    reported = getattr(model, "dtype", None)

    if reported is None:
        CROSS_ENCODER_DTYPE_DEGRADATIONS["unreported"] += 1
        log.warning("the loaded cross-encoder reports no dtype, so the "
                    "configured precision went unverified against it",
                    event="cross_encoder_dtype_unverified",
                    status=DTYPE_UNREPORTED, model=checkpoint,
                    dtype_configured=configured, reason="attribute absent")
        return DTYPE_UNREPORTED

    try:
        actual = _normalise_dtype_name(reported)
    except Exception:                                    # pragma: no cover
        actual = None
    if not actual:
        CROSS_ENCODER_DTYPE_DEGRADATIONS[
            f"unreadable:{type(reported).__name__}"] += 1
        log.warning("the loaded cross-encoder reported a dtype this code could "
                    "not read, so the configured precision went unverified "
                    "against it", event="cross_encoder_dtype_unverified",
                    status=DTYPE_UNREADABLE, model=checkpoint,
                    dtype_configured=configured,
                    reason=type(reported).__name__)
        return DTYPE_UNREADABLE

    if actual != configured:
        raise CrossEncoderDtypeMismatchError(
            f"config.CROSS_ENCODER_DTYPE is {configured!r} and was passed to "
            f"from_pretrained, but the loaded checkpoint {checkpoint!r} came "
            f"back as {actual!r}. Every Stage 3 score this process would "
            f"produce is at a precision the record does not name, and nothing "
            f"downstream would report it -- the model loads, the ranker ranks, "
            f"and only the ordering moves. Either the installed transformers "
            f"stopped honouring `dtype=` on this path, or the checkpoint "
            f"cannot be materialised at the configured precision. Do not "
            f"widen this check to accept what came back: the constant is the "
            f"precision every score this project has recorded was computed "
            f"in, and changing it is a measurement (see "
            f"oncotriage/config.py:CROSS_ENCODER_DTYPE)."
        )

    log.info("the cross-encoder precision matches the configured one",
             event="cross_encoder_dtype_verified", status=DTYPE_VERIFIED,
             model=checkpoint, dtype_configured=configured,
             dtype_reported=actual)
    return DTYPE_VERIFIED


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


def _huggingface_cache_kwargs():
    """``{"cache_dir": ...}`` for from_pretrained, or ``{}``.

    Called from BOTH MedCPT factories, below each one's `_DEFER_LOCAL_MODELS`
    return so a replay resolves no path, and unreachable when an override is
    installed because `_resolve` never calls a factory then.

    THE ARGUMENT IS THE MECHANISM AND THE VARIABLE IS NOT. huggingface_hub
    reads HF_HOME once, at its own import, and in this project it is ALWAYS
    already imported by the time a factory runs -- `qdrant_client` imports
    `fastembed` at module scope and `fastembed` imports `huggingface_hub` at
    module scope, so `import oncotriage.agent.deps` alone puts both in
    sys.modules. Measured, not reasoned about; see the block above
    `pin_model_cache` in oncotriage/paths.py. `cache_dir=` is resolved by
    transformers at CALL time and outranks every variable.

    An empty dict means the operator's HF_HOME or HF_HUB_CACHE decided and
    huggingface_hub's own resolution is left completely alone.

    THE PATH GOES TO THE CONSOLE AND THE SOURCE GOES TO THE LOG. An
    operator-set HF_HOME is usually under their home directory; see the note
    beside `model_cache_source` in oncotriage/observability.py.
    """
    name, value, source = paths.pin_model_cache(paths.MODEL_CACHE_ENV_HF)
    cache_dir = paths.huggingface_cache_dir()
    log.info("model cache root pinned", event="model_cache_pinned",
             model=config.CROSS_ENCODER_MODEL, model_cache_source=source)
    console.out(f"[Model cache] {name}={value} (source: {source}; "
                f"cache_dir={cache_dir if cache_dir else 'library default'})")
    return {} if cache_dir is None else {"cache_dir": cache_dir}


def _build_medcpt_tokenizer():
    if _DEFER_LOCAL_MODELS:
        log.info("skipping the MedCPT tokenizer load",
                 event="local_model_deferred", model=config.CROSS_ENCODER_MODEL,
                 reason=f"{DEFER_LOCAL_MODELS_ENV}=1")
        return _DeferredLocalModel("MedCPT tokenizer")

    _cache_kwargs = _huggingface_cache_kwargs()
    from transformers import AutoTokenizer

    # THE CHECKPOINT NAME IS config.CROSS_ENCODER_MODEL AND MUST STAY THAT WAY
    # (pass 20f-2). This load and _build_medcpt_model() below are the operative
    # pair: a cross-encoder tokenizes its (query, document) pair with the
    # tokenizer trained alongside the weights, and two literals here could be
    # edited apart. transformers raises nothing for a mismatched pair -- it runs
    # one BERT vocabulary into another BERT's embedding matrix and returns
    # scores -- so Stage 3 would go on ranking, and only the ranking would be
    # wrong. tests/test_package_invariants.py section 2f(ii) asserts by ast that
    # both from_pretrained calls are handed this name and that the literal
    # itself appears exactly once in the package.
    log.info("loading the MedCPT cross-encoder tokenizer",
             event="local_model_load_started", model=config.CROSS_ENCODER_MODEL)
    tokenizer = AutoTokenizer.from_pretrained(config.CROSS_ENCODER_MODEL,
                                              **_cache_kwargs)
    log.info("MedCPT cross-encoder tokenizer loaded",
             event="local_model_load_finished", model=config.CROSS_ENCODER_MODEL)

    # AND THE SEQUENCE LIMIT, against what this tokenizer declares. Below the
    # deferral return above on purpose, and unreachable when an override is
    # installed because _resolve never calls this factory then.
    #
    # THE SHIPPED CHECKPOINT REPORTS THE PLACEHOLDER HERE, measured, so this
    # call is expected to return LIMIT_UNDECLARED and the load-bearing check is
    # the one in _build_medcpt_model below. It is kept anyway: a tokenizer that
    # DOES declare a limit is then covered, and one whose declared limit
    # contradicts the weights' is a real defect that nothing else would see.
    _verify_cross_encoder_sequence_limit(
        getattr(tokenizer, "model_max_length", None),
        "tokenizer.model_max_length",
        config.CROSS_ENCODER_MODEL)
    return tokenizer


def _build_medcpt_model():
    if _DEFER_LOCAL_MODELS:
        log.info("skipping the MedCPT cross-encoder load",
                 event="local_model_deferred", model=config.CROSS_ENCODER_MODEL,
                 reason=f"{DEFER_LOCAL_MODELS_ENV}=1")
        return _DeferredLocalModel("MedCPT cross-encoder")

    _cache_kwargs = _huggingface_cache_kwargs()
    from transformers import AutoModelForSequenceClassification

    # Using transformers directly instead of the sentence-transformers
    # CrossEncoder wrapper because: (1) MedCPT's official usage is via
    # AutoModelForSequenceClassification, (2) the CrossEncoder wrapper applies a
    # default sigmoid that squashes MedCPT's raw values, range -25 to 25.
    #
    # Same constant as the tokenizer above, deliberately: see the note there for
    # what a divergent pair costs and why nothing would raise.
    log.info("loading the MedCPT cross-encoder weights",
             event="local_model_load_started", model=config.CROSS_ENCODER_MODEL)
    # `dtype=` IS PASSED RATHER THAN LEFT TO THE LIBRARY, and it is the one
    # from_pretrained argument here that is about numerics rather than about
    # where the files live. transformers 5.x defaults it to "auto", which means
    # "whatever the checkpoint's own config.json says", so omitting it hands a
    # third-party file the decision that sets every Stage 3 score's precision.
    # The string form is used because this module must not import torch; see
    # config.CROSS_ENCODER_DTYPE for the whole argument and the release note.
    model = AutoModelForSequenceClassification.from_pretrained(
        config.CROSS_ENCODER_MODEL, dtype=config.CROSS_ENCODER_DTYPE,
        **_cache_kwargs)
    model.eval()
    log.info("MedCPT cross-encoder weights loaded",
             event="local_model_load_finished", model=config.CROSS_ENCODER_MODEL)

    # THIS IS THE CALL THAT VERIFIES config.CROSS_ENCODER_MAX_LENGTH. The
    # weights are what the limit is a property OF -- a BERT with N learned
    # position embeddings cannot accept position N -- and, measured, they are
    # the half of this checkpoint that declares a number at all. Reading an
    # attribute off a model that has just been built adds no load to any path.
    _verify_cross_encoder_sequence_limit(
        getattr(getattr(model, "config", None), "max_position_embeddings", None),
        "weights.config.max_position_embeddings",
        config.CROSS_ENCODER_MODEL)

    # AND THE PRECISION, against what actually came back rather than against
    # what was asked for. Passing `dtype=` above is a request; this is the
    # answer. Same placement argument as the line above it -- below the
    # deferral return, unreachable behind an override, and reading an attribute
    # off an object the caller already built.
    _verify_cross_encoder_dtype(model, config.CROSS_ENCODER_MODEL)
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
    # "tests/test_package_invariants.py" section 2f asserts the construction count is
    # exactly 1.
    #
    # THE DEFERRAL CHECK STAYS HERE, ABOVE THE DELEGATION, and that is the whole
    # reason this function survives rather than get_bm25_query_model pointing
    # straight at embedding. The switch is about the AGENT's replay path. The
    # indexer has no replay path, and an index built from a placeholder would be
    # written to Qdrant and swapped onto the live alias looking exactly like a
    # real one.
    if _DEFER_LOCAL_MODELS:
        log.info("skipping the FastEmbed BM25 query model load",
                 event="local_model_deferred",
                 model=embedding.BM25_SPARSE_MODEL_NAME,
                 reason=f"{DEFER_LOCAL_MODELS_ENV}=1")
        return _DeferredLocalModel("FastEmbed BM25 query model")

    return embedding.get_bm25_sparse_model()


#------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Accessors — the only way the agent reaches any of these
# ---------------------------------------------------------------------------

def get_openai_client():
    """The OpenAI client Stage 2's embedding and Stage 5's chat call use."""
    return _resolve(OPENAI_CLIENT, config.get_openai_client)


def get_bedrock_client():
    """The Amazon Bedrock client Stage 5 uses when MATCHING_PROVIDER is bedrock.

    A SECOND CLIENT RATHER THAN A REDIRECT OF THE FIRST, and the reason is the
    same one that gave `MEDCPT_SCORER` its own key: the two are reachable at
    once. `get_openai_client()` still serves Stage 2's embedding call whichever
    provider Stage 5 uses -- there is no Bedrock embedding path here -- so a
    run under MATCHING_PROVIDER="bedrock" holds BOTH clients, pointed at
    different hosts with different credentials. Overloading OPENAI_CLIENT would
    mean a harness that stubbed Stage 5's judge silently stubbed Stage 2's
    embeddings too, and the object identity assertions both fixture harnesses
    make would stop distinguishing them.

    NOT REACHED AT ALL WITH THE FLAG OFF. `oncotriage/agent/evaluation.py`
    dispatches on `config.MATCHING_PROVIDER` above the call, so under the
    default no factory here runs, no client is constructed and no Bedrock
    credential is resolved.
    """
    return _resolve(BEDROCK_CLIENT, config.get_bedrock_client)


def get_bedrock_anthropic_client():
    """The boto3 Bedrock client Stage 5 uses when the provider is bedrock_anthropic.

    A THIRD CLIENT KEY RATHER THAN A REUSE OF `BEDROCK_CLIENT`, and the reason
    is not tidiness: the two hold objects of DIFFERENT TYPES with different
    method surfaces -- an `openai.OpenAI` with `.responses.create`, and a
    botocore client with `.converse` -- so a harness that installed one where
    the other was expected would fail with an AttributeError from inside the
    call rather than at the seam. Sharing the key would also make the identity
    assertions both fixture harnesses make unable to say WHICH Bedrock branch
    they had gripped.

    NOT REACHED AT ALL WITH THE FLAG OFF. `oncotriage/agent/evaluation.py`
    dispatches on `config.MATCHING_PROVIDER` above the call, so under the
    default no factory here runs, no client is constructed, no AWS credential
    is resolved and boto3 is not imported.
    """
    return _resolve(BEDROCK_ANTHROPIC_CLIENT, config.get_bedrock_anthropic_client)


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
    """MeSHCancerFilter, or None when the filter was deliberately not built.

    None is a REAL ANSWER here, not "not built yet" — every caller inside the
    agent branches on `is None` and records MESH_FILTER_SKIP_NO_FILTER when it
    is. _resolve()'s sentinel is what keeps a legitimate None cached instead of
    re-reading four JSON files on every Stage 1, 3 and 4 call.

    Item 11a narrowed how None arises without removing it. Missing MeSH JSON
    files now raise out of ``load_mesh_filter()``; None reaches this accessor
    only from an override installed here, or from a run that set
    ONCOTRIAGE_ALLOW_DEGRADED_REGISTRIES and was warned about it. Both are
    decisions somebody made, which is the whole change.
    """
    return _resolve(MESH_FILTER, load_mesh_filter)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug  5 2026

@author: ramyalsaffar
"""
