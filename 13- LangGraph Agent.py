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

# ITEM 20c, PASS 2c: THIS FILE IS A SHIM.
#
# All 5,565 lines moved into oncotriage/agent/, twelve modules. This file
# survives because Files 12, 17, 25, 26, 31, 32, 35, 36, 37, 39, 40 and 45
# exec-chain it and read these names out of the shared namespace with no import
# statement of their own.
#
# The name list below is the RUNTIME surface of File 13 as it stood before this
# pass: the file was exec'd into a throwaway namespace, the chained files' own
# 315 names were subtracted, and the remaining 87 recorded. An ast walk would
# have been wrong in both directions -- six of them are ANNOTATED assignments
# (_ICD10_RELEVANT_BLOCKS, _SNOMED_RELEVANT_COMORBIDITIES,
# _IRRELEVANT_CONDITION_KEYWORDS, _IRRELEVANT_MEDICATION_KEYWORDS,
# _LAB_UNIT_CONVERSIONS and _EMPTY_BOOST_STATS is not), and three
# (_bootstrap, _code_dir, _fh) are bound by the bootstrap block rather than by
# any definition.
#
# WHAT CANNOT BE FIXED BY A SHIM WRAPPER, and this is the whole point of the
# pass. File 14's shim wraps log_inference and reads
# globals().get("inferences_path") at call time, which works because the shim's
# function IS the entry point -- the caller calls it, so the shim sits in the
# call path. That pattern does not transfer here. openai_client, qdrant_client,
# _bm25_query_model and medcpt_score_pairs are used INSIDE the agent and never
# called by the caller, so no wrapper defined here is ever in the path, and a
# caller that rebinds one of those names in this namespace changes nothing that
# the agent can see.
#
# The replacement is oncotriage/agent/deps.py: a real seam with named override
# keys. Files 35, 36, 45 and 46 install overrides there instead of rebinding.
# What this file CAN do, and does, is refuse to run a pipeline that was
# redirected the old way -- see _assert_no_legacy_rebinding() below, which is
# wired into match_patient_to_trials and turns a silent regression into a named
# RuntimeError.
#
# Explicit, by name, never a star import.


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


# The seam. Imported as a module rather than by name so a reader of any call
# site can see that the object came from the seam and not from a global.
from oncotriage.agent import deps

from oncotriage.agent.deps import (
    DEFER_LOCAL_MODELS_ENV,
    _DEFER_LOCAL_MODELS,
    _DeferredLocalModel,
)

from oncotriage.agent.text import (
    _BM25_PUNCT_PATTERN,
    tokenize_for_bm25,
)

from oncotriage.agent.state import (
    CHANNEL_ABLATED,
    CHANNEL_EMPTY_QUERY,
    CHANNEL_FAILED,
    CHANNEL_OK,
    EXPANSION_PATH_FALLBACK,
    EXPANSION_PATH_MESH,
    GENOMIC_VARIANT_LOINC,
    MESH_FILTER_APPLIED,
    MESH_FILTER_SKIP_ABLATED,
    MESH_FILTER_SKIP_NO_FILTER,
    MESH_FILTER_SKIP_NO_TREES,
    RETRIEVAL_CHANNELS,
    TrialMatchState,
    _EmptySparseQuery,
    _VARIANT_TEXT_PATTERN,
)

from oncotriage.agent.models import (
    get_embedding,
    medcpt_score_pairs,
    score_pairs,
)

from oncotriage.agent.patient import (
    _ICD10_RELEVANT_BLOCKS,
    _IRRELEVANT_CONDITION_KEYWORDS,
    _IRRELEVANT_MEDICATION_KEYWORDS,
    _LAB_UNIT_CONVERSIONS,
    _SNOMED_RELEVANT_COMORBIDITIES,
    _classify_condition_relevance,
    _classify_medication_relevance,
    _create_patient_summary,
    _is_icd10_relevant,
    _normalize_lab_unit,
    compute_patient_hash,
    extract_genomic_variant_terms,
)

from oncotriage.agent.mesh_expansion import (
    MESH_RESOLUTION_NO_CONDITIONS,
    MESH_RESOLUTION_NO_FILTER,
    _empty_mesh_resolution,
    expand_query_from_mesh,
    format_mesh_resolution,
    resolve_patient_mesh,
)

from oncotriage.agent.retrieval import (
    RERANK_RRF_K,
    _EMPTY_BOOST_STATS,
    apply_mesh_relevance_boost,
    apply_quality_gate,
    build_bm25_index_from_qdrant,
    node_cross_encoder_rerank,
    node_hybrid_retrieval,
    node_query_expansion,
    unboosted_score,
)

from oncotriage.agent.filtering import (
    node_rule_based_filter,
)

from oncotriage.agent.evaluation import (
    FINISH_REASON_LENGTH,
    MatchingModelMismatchError,
    NOT_EVALUABLE_MODEL_OMITTED,
    NOT_EVALUABLE_SPLIT_BUDGET,
    NOT_EVALUABLE_TRUNCATION_FLOOR,
    _NOT_EVALUABLE_REASONS,
    _build_trials_text,
    _split_in_half,
    _unevaluable_entry,
    call_matching_model,
    estimate_output_tokens,
    node_gpt4o_evaluation,
)

from oncotriage.agent.terminal import (
    TERMINAL_NODE_ERROR,
    TERMINAL_NODE_FINALIZE,
    TERMINAL_NODE_NO_CANDIDATES,
    _pipeline_provenance,
    node_error_handler,
    node_finalize,
    node_no_candidates,
)

from oncotriage.agent.graph import (
    build_initial_state,
    build_matching_graph,
    route_after_filter,
    route_after_gpt4o,
    route_after_retrieval,
)
from oncotriage.agent.graph import match_patient_to_trials as _match_patient_to_trials_pkg

from oncotriage.agent.display import (
    _print_match_detail,
    display_match_results,
)


#------------------------------------------------------------------------------


# Registry initialization
#
# EAGER, exactly as File 13's lines 64-66 were, and through the seam so that the
# objects bound here are the SAME ones every agent module reaches. Files 26, 32,
# 34, 37 and 45 read these three names directly out of the shared namespace.
#
# _MESH_FILTER is bound to the real value, which is legitimately None when the
# MeSH JSON lookups are absent. It is NOT wrapped in a proxy for that reason:
# File 32 line 112 and File 37 line 651 both test `_MESH_FILTER is None`, and a
# proxy would make that test false on a machine where the filter genuinely
# failed to load -- turning a recorded degradation into a silent one.
_CANCER_REGISTRY = deps.get_cancer_registry()   # ICD-10-CM 2024 + SNOMED primary cancer detection
_LAB_REGISTRY    = deps.get_lab_registry()      # LOINC filter for oncology-relevant labs
_MESH_FILTER     = deps.get_mesh_filter()       # MeSH C04 cancer site relevance (None if files missing)


#------------------------------------------------------------------------------


# THE THREE LOCAL MODEL NAMES, bound LAZILY
#------------------------------------------
#
# File 13 loaded MedCPT (~110 MB, tens of seconds) and FastEmbed at exec() time,
# lines 414-434. Twelve files chain File 13, so every one of them paid for both
# models just by being read -- including the six that never score a pair.
#
# They cannot simply be dropped: 12- RAG Trial Indexer Validator.py calls
# medcpt_tokenizer(...) and medcpt_model(...) directly out of this namespace,
# and tests/test_agent_retrieval_observability.py reads _bm25_query_model to measure
# tokenization. And they cannot be bound eagerly here without reintroducing the
# exact cost this pass removes.
#
# So each is a thin proxy that resolves through the seam on FIRST USE and
# forwards everything. Attribute access and call are both forwarded, which is
# the whole surface any caller uses: medcpt_tokenizer(pairs, ...) and
# medcpt_model(**encoded) are calls, _bm25_query_model.query_embed(text) is an
# attribute.
#
# WHY THESE THREE AND NOT _MESH_FILTER: nothing tests `medcpt_model is None`.
# A proxy is safe for an object whose only use is "call it or read an attribute
# off it", and unsafe for one whose absence is a branch. See above.

class _LazyAgentDependency:
    """Resolves an agent dependency through deps on first use, then forwards.

    Deliberately NOT cached in the proxy: deps already caches, and an override
    installed after this file was chained must take effect. That is what keeps
    ONCOTRIAGE_DEFER_LOCAL_MODELS=1 plus a replay override working when both
    arrive after the chain has run.

    WHAT IT FORWARDS, and why the list grew in pass 20c-3a
    ------------------------------------------------------
    Pass 2c forwarded ``__getattr__`` and ``__call__`` and nothing else, on the
    stated grounds that "call it or read an attribute off it" is the whole
    surface any caller uses. That was true of the three callers that existed. It
    was not true of PYTHON, and the gap is not a missing feature -- it is a set
    of WRONG ANSWERS, because ``__getattr__`` is not consulted for an implicit
    special-method lookup. CPython looks those up on the TYPE:

        bool(proxy)     -> True, always, whatever the wrapped object says.
                           A wrapped object with __bool__ returning False, or
                           with __len__ returning 0, reads as truthy.
        proxy == other  -> False, always, by identity -- even when the wrapped
                           object IS `other`. This is the dangerous one: the
                           whole point of a proxy over this seam is that a
                           harness can ask "is the thing the agent reaches
                           mine?", and `==` answered no while the answer was
                           yes.
        len / iter / in -> TypeError naming _LazyAgentDependency, which sends a
                           reader to this class instead of to the model.

    Every one of those is a confident answer about an object the proxy never
    consulted. So each is now defined and each RESOLVES and DELEGATES.

    THE SET IS CLOSED, AND THAT IS THE CONTRACT. Anything outside

        __getattr__  __setattr__(no)  __call__  __bool__  __len__  __iter__
        __contains__  __eq__  __ne__(derived)  __hash__  __repr__(no resolve)

    is NOT forwarded and will answer for the PROXY rather than for the wrapped
    object -- ``+``, ``[]``, ``with``, ``str()``, ``format()``, ``copy``,
    ``pickle``, ``isinstance`` and the rest. This is a deliberate stopping point,
    not an oversight: a full transparent proxy means forwarding some ninety
    dunders, and every one of them is another place a resolution can fire from
    somewhere nobody expected. The three names bound below are a tokenizer, a
    cross-encoder and a sparse encoder; none of them is added, indexed, or
    entered as a context manager. If a caller ever needs one of those, add it
    HERE rather than reaching around the proxy.

    WHY EAGER BINDING IS NOT THE ANSWER. The obvious alternative is to drop the
    proxy and bind the real objects. That reintroduces exactly what pass 2c
    removed: ONCOTRIAGE_DEFER_LOCAL_MODELS appears in only two files in this
    repository -- this one and "fixture_replay.py" -- so Files 31, 32, 35,
    36, 37, 39 and 40 all chain File 13 with the switch unset and none of them
    scores a pair. Eager binding would load MedCPT (~110 MB) and FastEmbed for
    all seven.

    __repr__ RESOLVES NOTHING, AND THAT IS A CORRECTION TO PASS 3a.

    Pass 3a made __repr__ delegate -- `return repr(self._resolve())` -- on the
    grounds that a proxy printing "<lazy MedCPT cross-encoder>" while handing
    the agent a fixture stub is lying at the one moment a person is looking.
    The goal was right; delegation was the wrong mechanism, because it makes
    REPR TRIGGER A BUILD:

      * a debugger rendering locals, a logging call formatting the object, or a
        bare `medcpt_model` typed at a prompt downloads and loads ~110 MB;
      * having paid for it, it then prints transformers' multi-thousand-line
        module tree, which is not what anyone typing `medcpt_model` wanted;
      * and it happens on the DIAGNOSTIC path, so the tool used to inspect the
        state is the thing that changes it. A repr must be free of side effects.

    So __repr__ asks deps two questions that never call a factory --
    ``deps.resolution_state(key)`` and ``deps.peek(key)`` (pass 20c-3b) -- and
    reports the KEY, the STATE (override / cached / unresolved) and, when
    something is already there, the wrapped object's TYPE and id. That keeps the
    honesty the delegation was after: with a fixture stub installed the repr
    names the stub's class, not the model's, and it says "override" outright.
    What it deliberately does not do is print the object's own repr, which is
    the module tree nobody asked for.

    A repr that RAISES would still be worse than either -- it breaks every
    debugger, traceback and log line that formats the object -- so a failure
    (a proxy built with a key deps does not know) is caught, RECORDED in
    repr_failures (never silently), and reported as a description naming the
    exception.
    """

    __slots__ = ("_accessor", "_label", "_key")

    # Not a counter but the failures themselves, because there is exactly one
    # way to reach this list and a bare count would not say which accessor
    # failed. Class-level on purpose: a module-level name here would land in the
    # shared exec namespace that "tests/test_package_invariants.py" section 5 pins.
    repr_failures = []

    def __init__(self, accessor, label, key):
        object.__setattr__(self, "_accessor", accessor)
        object.__setattr__(self, "_label", label)
        # The deps key this proxy stands for. Carried so __repr__ can ask deps
        # about it without calling the accessor -- the accessor is the thing
        # that builds.
        object.__setattr__(self, "_key", key)

    def _resolve(self):
        return object.__getattribute__(self, "_accessor")()

    def __getattr__(self, attr):
        return getattr(self._resolve(), attr)

    def __call__(self, *args, **kwargs):
        return self._resolve()(*args, **kwargs)

    def __bool__(self):
        return bool(self._resolve())

    def __len__(self):
        return len(self._resolve())

    def __iter__(self):
        return iter(self._resolve())

    def __contains__(self, item):
        return item in self._resolve()

    def __eq__(self, other):
        # A proxy on the right-hand side is unwrapped too, so proxy == proxy
        # compares the two wrapped objects rather than comparing a resolved
        # object against a wrapper it can never equal.
        if isinstance(other, _LazyAgentDependency):
            other = other._resolve()
        return self._resolve() == other

    def __hash__(self):
        # Defining __eq__ sets __hash__ to None, which would make these three
        # names unhashable -- a silent new failure introduced by a fix. It
        # delegates rather than falling back to id() because a hash that
        # disagrees with __eq__ is a broken dict key, and __eq__ delegates.
        return hash(self._resolve())

    def __repr__(self):
        # NO RESOLUTION HAPPENS HERE. Both deps calls below read the override
        # and cache dicts under the lock and return; neither calls a factory.
        # See the class docstring for why that matters more than delegating.
        label = object.__getattribute__(self, "_label")
        key = object.__getattribute__(self, "_key")
        try:
            state = deps.resolution_state(key)
            if state == deps.RESOLVED_UNRESOLVED:
                return (f"<{label} via oncotriage.agent.deps[{key}]: "
                        f"unresolved — nothing built yet, and this repr did not "
                        f"build it>")
            # Already installed or already built, so reading it costs nothing.
            # The TYPE and id rather than the object's own repr: this is the
            # discriminating fact (a fixture stub's class is not the model's)
            # without the multi-thousand-line module tree transformers prints.
            value = deps.peek(key)
            return (f"<{label} via oncotriage.agent.deps[{key}]: {state} -> "
                    f"{type(value).__module__}.{type(value).__qualname__} "
                    f"at {hex(id(value))}>")
        except Exception as exc:                      # noqa: BLE001
            # Recorded, not swallowed. Re-raising is not an option: a raising
            # __repr__ breaks tracebacks, debuggers and logging for everyone,
            # including whoever is trying to diagnose this very failure. The one
            # way to get here is a proxy constructed with a key deps does not
            # know, which is a defect in this file rather than in the model.
            _LazyAgentDependency.repr_failures.append(
                f"{label}: {type(exc).__name__}: {exc}"
            )
            return (f"<undescribable {label} via oncotriage.agent.deps: "
                    f"{type(exc).__name__}: {exc}>")


medcpt_tokenizer  = _LazyAgentDependency(deps.get_medcpt_tokenizer, "MedCPT tokenizer",
                                         deps.MEDCPT_TOKENIZER)
medcpt_model      = _LazyAgentDependency(deps.get_medcpt_model, "MedCPT cross-encoder",
                                         deps.MEDCPT_MODEL)
_bm25_query_model = _LazyAgentDependency(deps.get_bm25_query_model, "FastEmbed BM25 query model",
                                         deps.BM25_QUERY_MODEL)


#------------------------------------------------------------------------------


# THE LEGACY-REBINDING GUARD
#---------------------------
#
# Before this pass, four names in this namespace were the pipeline's redirection
# points, and rebinding one was all it took:
#
#     fixture_capture.py  lines 805-821   install_recording_hooks
#     fixture_replay.py   lines 318-327   install_replay_hooks
#     tests/test_agent_ablation_flag_passthrough.py  lines 229-230
#     tests/test_storage_inference_logging_contract.py            line 439
#     tests/test_agent_retrieval_observability.py     swap_globals(_MESH_FILTER=...)
#
# All five now install a deps override instead. A sixth caller written the old
# way would be redirected NOWHERE, and — this is the part that made the seam
# worth building — nothing would say so. File 46 would have gone on printing
# that every fixture replayed clean while sending each Stage 5 prompt to the
# real OpenAI endpoint and paying for it.
#
# So the old pattern is DETECTED rather than ignored. This file records the
# identity of every redirectable name as it bound it, and match_patient_to_trials
# — which every caller does go through — refuses to run if any of them has been
# rebound to something else. That is the File 14 wrapper pattern applied where
# it actually works: at the one function that is genuinely in the call path.
#
# id() is compared, not equality: a stub registry may well compare equal to
# nothing, and equality on a proxy can dispatch into the object it wraps.

_LEGACY_REDIRECTABLE = {
    "openai_client":      deps.OPENAI_CLIENT,
    "qdrant_client":      deps.QDRANT_CLIENT,
    "_bm25_query_model":  deps.BM25_QUERY_MODEL,
    "medcpt_score_pairs": deps.MEDCPT_SCORER,
    "medcpt_tokenizer":   deps.MEDCPT_TOKENIZER,
    "medcpt_model":       deps.MEDCPT_MODEL,
    "_CANCER_REGISTRY":   deps.CANCER_REGISTRY,
    "_LAB_REGISTRY":      deps.LAB_REGISTRY,
    "_MESH_FILTER":       deps.MESH_FILTER,
}

_LEGACY_BOUND_AT_LOAD = {
    _name: id(globals()[_name])
    for _name in _LEGACY_REDIRECTABLE
    if _name in globals()
}


def _detect_legacy_rebinding():
    """Names in this namespace that were rebound after this file was chained.

    Returns [(name, deps_key)], empty when nothing was rebound.
    """
    return [
        (name, _LEGACY_REDIRECTABLE[name])
        for name, bound_id in _LEGACY_BOUND_AT_LOAD.items()
        if name in globals() and id(globals()[name]) != bound_id
    ]


def _assert_no_legacy_rebinding():
    """Refuse to run a pipeline redirected by rebinding a shared global."""
    stale = _detect_legacy_rebinding()
    if not stale:
        return
    lines = "\n".join(
        f"    {name!r} -> install it as deps.{key.upper()} instead "
        f"(oncotriage.agent.deps.set_override)"
        for name, key in stale
    )
    raise RuntimeError(
        "13- LangGraph Agent.py is a shim over oncotriage/agent/, and the agent "
        "resolves its dependencies through oncotriage.agent.deps, NOT out of "
        "this namespace. Rebinding the name(s) below redirects nothing -- the "
        "pipeline would run against the real client while reporting whatever "
        "the caller expected:\n"
        f"{lines}\n"
        "This is refused rather than ignored because the failure it replaces "
        "was silent and cost money: fixture_replay.py would have called the "
        "real OpenAI endpoint for every fixture and still reported them clean."
    )


def match_patient_to_trials(patient_data, graph=None, *args, **kwargs):
    """Public entry point. Refuses to run a pipeline redirected the old way.

    See oncotriage/agent/graph.py for the real docstring. This wrapper adds one
    thing: the legacy-rebinding check above. It is here rather than inside the
    package because only this namespace can see what an exec-chain caller
    rebound.
    """
    _assert_no_legacy_rebinding()
    return _match_patient_to_trials_pkg(patient_data, graph, *args, **kwargs)


#------------------------------------------------------------------------------


    
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
