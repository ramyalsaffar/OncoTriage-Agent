# Agent State Channel Coverage Test
###################################

"""A key a graph node returns and ``TrialMatchState`` does not declare is
DROPPED, silently, and this file is the standing guard against that.

WHAT SHIPPED, AND WHY NOTHING FAILED
------------------------------------
PROMPT_VERSION 1.6.0 added three provenance keys to Stage 5's success return --
``llm_classifier_packed_chunks``, ``llm_classifier_packing`` and
``llm_classifier_cached_input_tokens`` -- and ``_pipeline_provenance()`` read
all three off the state. None of the three was declared in ``TrialMatchState``.

LangGraph writes only the channels the state schema declares. An undeclared key
is not an error, not a warning and not a raise: the update is discarded. So the
three arrived at every terminal node as ``None``, and ``None`` is this project's
"the measurement was not made" -- the same value a run that never reached
Stage 5 reports. The provenance was indistinguishable from its own absence, on
every path, and no counter moved.

It could not have been caught by reading either half. The writer is correct, the
reader is correct, and the schema between them is where the fact dies.

WHAT THIS FILE HOLDS
--------------------
    1. THE STANDING SCAN. Every string key returned by every ``node_*``
       function in ``oncotriage/agent/`` is declared in ``TrialMatchState``.
       This is the check that generalises: it covers the next key somebody adds
       to any node, not the four this pass happened to fix. Negative control: a
       planted undeclared key in an AST copy must be reported.
    2. THE BEHAVIOURAL PROOF, on a StateGraph built over the REAL
       ``TrialMatchState`` with the REAL Stage 5 node and the REAL
       ``node_finalize`` -- the same schema and the same functions
       ``build_matching_graph()`` wires. The four keys arrive with the values
       the node produced. NEGATIVE CONTROL: the identical run over a schema
       with those four annotations REMOVED loses all four, which is what says
       the declaration is doing the work and the assertion can fail.
    3. THE PER-CALL LEDGER. ``llm_classifier_call_details`` carries one entry
       per call ISSUED, in order, with the per-call token readings the totals
       cannot reconstruct: a single ``cached_input_tokens`` figure is equally
       consistent with a cache that warms after the first chunk and one that
       never warms, and telling those apart is the whole point of measuring a
       packed run.
    4. ABSENCE IS RECORDED AS ABSENCE. A response whose usage carries no
       ``prompt_tokens_details`` leaves ``cached_tokens`` at None per call and
       the total at None -- never 0, which is a genuine provider reading.
    5. THE LEDGER SURVIVES THE FAILURE RETURNS. A run whose response is
       unparseable still reports the call it made and paid for. A list is not a
       count, so a short one understates nothing.
    6. THE EVALUATION RECORD PERSISTS ALL FOUR. ``run_harness.build_record``
       copies the result verbatim except four named keys, so this is a property
       of that omission list rather than of an enumeration -- asserted here
       because the validation run reads these fields off the persisted JSON.

NO NETWORK, NO KEYS, NO SPEND, NO CORPUS, NO GIT HISTORY, and it is not in the
collision matrix: it writes nothing anywhere in the repository. The OpenAI
client is a stand-in installed through ``oncotriage/agent/deps.py``; Qdrant and
the cross-encoder are never reached, because Stage 5 and Stage 6 are the only
stages driven.

Run from terminal:
    python tests/test_agent_state_channel_coverage.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# The package bootstrap every file in tests/ carries; the candidate directory
# is the PARENT of this file's. `pip install -e .` makes it a no-op.
import os
import sys

try:
    import oncotriage  # noqa: F401
except ImportError:
    for _candidate, _how in (
        (os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
         if "__file__" in globals() else None, "__file__"),
        (os.getcwd(), "cwd"),
    ):
        if _candidate and os.path.isdir(os.path.join(_candidate, "oncotriage")):
            if _candidate not in sys.path:
                sys.path.insert(0, _candidate)
            print(f"[Bootstrap] oncotriage package found at {_candidate} "
                  f"(via {_how}); added to sys.path")
            break
    else:
        raise
    del _candidate, _how

# Stage 5 loads no local model and this file never reaches one, but the flag is
# set before the agent is imported anyway: a stand-in forgotten in a future
# edit becomes a named RuntimeError instead of a 110 MB download.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

import ast
import glob
import json
from typing import Dict, List, Optional, TypedDict

from langgraph.graph import END, StateGraph

from oncotriage.agent import deps
from oncotriage.agent.evaluation import node_llm_classifier_evaluation
from oncotriage.agent.state import TrialMatchState
from oncotriage.agent.terminal import node_finalize


#------------------------------------------------------------------------------


_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label: str, actual, expected) -> None:
    """Assert equality, record the outcome, never abort the run."""
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          expected: {expected}\n"
                         f"          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


def drive(fn, *args, **kwargs):
    """Call into production code, converting a raise into a value check() fails on.

    A bare call would let a planted defect's exception escape while check()'s
    ARGUMENT was being evaluated, taking the whole file down and reporting one
    traceback where it owed a summary. Four files in this suite have had to fix
    that; this one starts with it.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                     # noqa: BLE001 -- reported
        return {"__raised__": f"{type(exc).__name__}: {exc}"}


def at(seq, index, default=None):
    """seq[index], or a named absence. Never an IndexError inside a check()."""
    try:
        return seq[index]
    except (IndexError, KeyError, TypeError):
        return default


_CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_AGENT_DIR = os.path.join(_CODE_DIR, "oncotriage", "agent")

# The four keys this pass declared. Named here so section 2's negative control
# can strip exactly these annotations and nothing else.
PACKING_KEYS = (
    "llm_classifier_packed_chunks",
    "llm_classifier_packing",
    "llm_classifier_cached_input_tokens",
    "llm_classifier_call_details",
)


#------------------------------------------------------------------------------


# ===========================================================================
# THE STAND-IN STAGE 5 CLIENT
# ===========================================================================
#
# One class per usage shape, because "no cached figure was reported" and "a
# cached figure of zero was reported" are different facts and section 4 is
# about telling them apart.

class _CompletionDetails:
    reasoning_tokens = 7


class _PromptDetails:
    def __init__(self, cached):
        self.cached_tokens = cached


class _Usage:
    """prompt_tokens_details is ABSENT entirely when `cached` is None."""

    def __init__(self, cached, prompt_tokens=100, completion_tokens=50):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens
        self.completion_tokens_details = _CompletionDetails()
        if cached is not None:
            self.prompt_tokens_details = _PromptDetails(cached)


class _StubOpenAI:
    """Serves a canned Stage 5 answer and records the requests it was sent.

    ``cached_sequence`` is consumed one entry per call, so a run of three chunks
    can be given a cold first call and warm ones after it -- which is the shape
    the validation run is trying to measure and therefore the shape this file
    has to be able to produce.
    """

    def __init__(self, nct_ids, cached_sequence=(None,), body=None):
        self.nct_ids = list(nct_ids)
        self.cached_sequence = list(cached_sequence)
        self.body = body
        self.requests = []
        _outer = self

        class _completions:
            @staticmethod
            def create(**kwargs):
                index = len(_outer.requests)
                _outer.requests.append(kwargs)
                cached = _outer.cached_sequence[
                    min(index, len(_outer.cached_sequence) - 1)]
                return _outer._completion(kwargs, cached)

        class _chat:
            completions = _completions

        self.chat = _chat

    def _completion(self, kwargs, cached):
        # Answer about the trials THIS request asked about, so a packed run gets
        # a valid response per chunk rather than one response claiming every
        # trial in every chunk.
        sent = kwargs.get("messages") or []
        user = sent[-1].get("content", "") if sent else ""
        asked = [n for n in self.nct_ids if n in user] or self.nct_ids
        body = self.body if self.body is not None else json.dumps([
            {"nct_id": n, "eligible": "eligible", "assessment": "ok",
             "inclusion_criteria": [{"criterion": "adult",
                                     "patient_value": "63", "status": "met"}],
             "exclusion_criteria": []}
            for n in asked])

        class _Msg:
            content = body

        class _Choice:
            message = _Msg()
            finish_reason = "stop"

        class _Completion:
            choices = [_Choice()]
            usage = _Usage(cached)
            # None skips the answering-model check, which is a different
            # mechanism with its own test and would otherwise raise here.
            model = None

        return _Completion()


def _trial(nct, filler=""):
    return {
        "trial": {
            "nct_id": nct,
            "title": f"Trial {nct}",
            "phase": "Phase 2",
            "conditions": ["Breast Neoplasms"],
            "eligibility": {
                "inclusion_criteria": ["adult", "measurable disease" + filler],
                "exclusion_criteria": ["pregnancy"],
            },
        },
        "rerank_score": 0.5,
        "rerank_score_raw": 0.5,
        "medcpt_score_max": 0.5,
    }


PATIENT = {
    "patient_id": "channel-coverage-1",
    "demographics": {"age": 63, "sex": "female", "birth_date": "1962-04-03",
                     "race": "White", "ethnicity": "Not Hispanic or Latino"},
    "conditions": [{"code": "254837009", "system": "http://snomed.info/sct",
                    "display": "Malignant neoplasm of breast",
                    "clinical_status": "active",
                    "verification_status": "confirmed",
                    "onset": "2020-01-01"}],
    "observations": [], "medications": [], "procedures": [], "allergies": [],
    "cancer_stage_observations": [], "cancer_metastasis_observations": [],
    "cancer_genomic_variants": [],
    "ecog_performance_status": {"value": 1, "date": "2024-01-01",
                                "value_shape": "valueInteger",
                                "observation_count": 1,
                                "selection_path": "most_recent"},
}


def base_state(trials):
    return {"patient_data": dict(PATIENT), "filtered_trials": list(trials),
            "stage_timings": {}}


def _unannotated(fn):
    """The same node, called the same way, with no type annotation on it.

    LANGGRAPH READS THE NODE CALLABLE'S FIRST-PARAMETER ANNOTATION AND ADDS
    THAT SCHEMA'S CHANNELS TO THE GRAPH. Both real nodes are declared
    ``(state: TrialMatchState)``, so registering either one on a graph built
    over a REDUCED schema silently reinstates every channel the reduction
    removed -- and section 2c's control then reports that an undeclared key is
    carried, which is the opposite of the truth.

    Measured, not reasoned about: the identical control run reports the value 3
    with the real annotated function registered and None with this wrapper
    around it, on the same reduced schema, in the same process.

    In production it changes nothing and cannot: the graph schema and the
    annotation are both TrialMatchState there, so the injected channels are the
    graph's own. That is also why the shipped defect was real -- there was no
    second schema to inject anything.
    """
    def _node(state):
        return fn(state)
    _node.__name__ = getattr(fn, "__name__", "node")
    return _node


def run_through_graph(state, schema=TrialMatchState):
    """Stage 5 -> node_finalize over ``schema``. Returns the result dict.

    The two real node functions and a real StateGraph, so this exercises the
    channel layer rather than a dict copy. ``schema`` is a parameter for one
    reason: the negative control needs the identical run over a state schema
    missing the four annotations.

    Both nodes go through _unannotated() in BOTH arms, so the two differ in the
    schema and in nothing else.
    """
    graph = StateGraph(schema)
    graph.add_node("stage5", _unannotated(node_llm_classifier_evaluation))
    graph.add_node("finalize", _unannotated(node_finalize))
    graph.set_entry_point("stage5")
    graph.add_edge("stage5", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile().invoke(state)["result"]


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 1 -- every key a node returns is a declared channel
# ===========================================================================

print("=" * 70)
print("SECTION 1 -- every node_* return key is declared in TrialMatchState")
print("=" * 70)


def undeclared_return_keys(source: str, declared) -> List[str]:
    """Keys returned by any node_* function in ``source`` that ``declared`` lacks.

    A pure function of text and a name set, so the negative control can hand it
    a planted copy without touching a file.
    """
    found = []
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not fn.name.startswith("node_"):
            continue
        for ret in ast.walk(fn):
            if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Dict):
                for key in ret.value.keys:
                    if (isinstance(key, ast.Constant)
                            and isinstance(key.value, str)
                            and key.value not in declared):
                        found.append(f"{fn.name}:{key.value}")
    return sorted(set(found))


_DECLARED = set(TrialMatchState.__annotations__)
_AGENT_FILES = sorted(glob.glob(os.path.join(_AGENT_DIR, "*.py")))

_all_undeclared = []
_nodes_seen = 0
_keys_seen = 0
for _path in _AGENT_FILES:
    _src = open(_path, encoding="utf-8").read()
    for _fn in ast.walk(ast.parse(_src)):
        if isinstance(_fn, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and _fn.name.startswith("node_"):
            _nodes_seen += 1
            for _ret in ast.walk(_fn):
                if isinstance(_ret, ast.Return) and isinstance(_ret.value, ast.Dict):
                    _keys_seen += len([k for k in _ret.value.keys
                                       if isinstance(k, ast.Constant)])
    _all_undeclared += [f"{os.path.basename(_path)}:{h}"
                        for h in undeclared_return_keys(_src, _DECLARED)]

print(f"\n  1a. the scan over {len(_AGENT_FILES)} agent modules")
check("no node returns a key TrialMatchState does not declare",
      _all_undeclared, [])

# NON-DEGENERACY. A scan that found no node functions, or no return keys, would
# report exactly the same empty list. Both floors are well under the current
# counts (8 nodes, 100+ keys) so ordinary edits do not trip them.
check("the scan actually saw node functions (non-degenerate)",
      _nodes_seen >= 6, True)
check("the scan actually saw return keys (non-degenerate)",
      _keys_seen >= 40, True)
check("all four packing keys are declared",
      sorted(k for k in PACKING_KEYS if k in _DECLARED), sorted(PACKING_KEYS))

print("\n  1b. NEGATIVE CONTROL -- a planted undeclared key is reported")
_PLANT = '''
def node_planted(state):
    return {"llm_classifier_calls": 1, "a_key_nobody_declared": 2}
'''
check("planted undeclared key is found",
      undeclared_return_keys(_PLANT, _DECLARED),
      ["node_planted:a_key_nobody_declared"])
check("the declared key beside it is NOT reported",
      "node_planted:llm_classifier_calls"
      in undeclared_return_keys(_PLANT, _DECLARED), False)

# The control that matters most: the scan run against the state schema AS IT WAS
# before this pass -- the four annotations removed -- must report the four keys.
# Without this, "no undeclared keys" is satisfied by a scan that stopped working.
_DECLARED_BEFORE = _DECLARED - set(PACKING_KEYS)
_before = []
for _path in _AGENT_FILES:
    _before += undeclared_return_keys(
        open(_path, encoding="utf-8").read(), _DECLARED_BEFORE)
check("against the pre-fix schema the scan reports all four keys",
      sorted({h.split(":", 1)[1] for h in _before}), sorted(PACKING_KEYS))

print("\n  1c. the scan's premise: every node is annotated with THIS schema")
#
# Section 1 asks whether a returned key is declared in TrialMatchState, and that
# question is only the right one while every node's channels come from
# TrialMatchState. LangGraph takes a node's channels from the callable's
# first-parameter annotation when it has one, so a node annotated with some
# other TypedDict would be writing to a different channel set and this scan
# would be checking it against a schema it never uses.
_annotations = {}
for _path in _AGENT_FILES:
    for _fn in ast.walk(ast.parse(open(_path, encoding="utf-8").read())):
        if isinstance(_fn, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and _fn.name.startswith("node_"):
            _args = _fn.args.args
            _ann = _args[0].annotation if _args else None
            _annotations[_fn.name] = (
                ast.unparse(_ann) if _ann is not None else None)

# THE ANNOTATION SET IS {TrialMatchState, dict} AND BOTH ARE SAFE, which is a
# narrower claim than "they all say TrialMatchState" and is the true one:
# node_query_expansion and node_cross_encoder_rerank are declared
# ``(state: dict)``. Measured on this langgraph: a TypedDict annotation adds
# that schema's channels to the graph (67 vs 66 on a schema one key short),
# while ``dict`` and a bare parameter add none and the node simply reads the
# graph's own channels. So the only annotation that could invalidate this
# section is a DIFFERENT TypedDict, and that is what this forbids.
_INJECTING = sorted({v for v in _annotations.values()
                     if v not in (None, "dict", "Dict", "TrialMatchState")})
check("no node is annotated with a TypedDict other than TrialMatchState",
      _INJECTING, [])
check("the annotations seen are the two known-safe ones",
      sorted({str(v) for v in _annotations.values()}),
      ["TrialMatchState", "dict"])
check("and the scan saw every node (non-degenerate)",
      len(_annotations), _nodes_seen)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 2 -- the four keys survive the real channel layer
# ===========================================================================

print()
print("=" * 70)
print("SECTION 2 -- the graph carries them to the result")
print("=" * 70)

_NCTS = [f"NCT0000000{i}" for i in range(1, 6)]
_stub = _StubOpenAI(_NCTS, cached_sequence=(1024,))
deps.set_override(deps.OPENAI_CLIENT, _stub)

_returned = drive(node_llm_classifier_evaluation, base_state(
    [_trial(n) for n in _NCTS]))
_result = drive(run_through_graph, base_state([_trial(n) for n in _NCTS]))

print("\n  2a. the node produces them (non-degenerate values)")
check("node returned no error", _returned.get("error"), "")
check("packed_chunks is a positive int",
      isinstance(_returned.get("llm_classifier_packed_chunks"), int)
      and _returned["llm_classifier_packed_chunks"] >= 1, True)
check("packing report says enabled",
      (_returned.get("llm_classifier_packing") or {}).get("enabled"), True)
check("cached_input_tokens is the stub's 1024, not 0",
      _returned.get("llm_classifier_cached_input_tokens"), 1024)
check("call_details has one entry per call",
      len(_returned.get("llm_classifier_call_details") or []),
      _returned.get("llm_classifier_calls"))

print("\n  2b. and the result carries every one of them")
for _key in PACKING_KEYS:
    check(f"result[{_key}] equals what the node returned",
          _result.get(_key), _returned.get(_key))
check("result packed_chunks is not None",
      _result.get("llm_classifier_packed_chunks") is None, False)

print("\n  2c. NEGATIVE CONTROL -- the same run over the pre-fix schema loses them")


# TrialMatchState with exactly the four annotations removed, built by copying
# the annotation dict rather than by retyping the schema, so it cannot drift
# from the real one in any other respect -- which is what makes the difference
# between the two arms attributable to these four names and nothing else.
#
# THE FUNCTIONAL FORM IS LOAD-BEARING AND THE FIRST VERSION OF THIS CONTROL GOT
# IT WRONG. Subclassing TypedDict with an empty body and then assigning
# __annotations__ leaves __required_keys__ and __optional_keys__ EMPTY, and
# LangGraph builds its channels from those: a schema declaring no keys gets no
# channel filtering at all, so every key survived and the control reported that
# an undeclared key is carried -- the opposite of the truth, from a control that
# was inert rather than wrong. TypedDict(name, mapping) computes the key sets
# properly. The assertion below is what stops that recurring.
_StateWithoutPackingKeys = TypedDict("_StateWithoutPackingKeys", {
    k: v for k, v in TrialMatchState.__annotations__.items()
    if k not in PACKING_KEYS
})

_result_before = drive(run_through_graph,
                       base_state([_trial(n) for n in _NCTS]),
                       schema=_StateWithoutPackingKeys)

check("the control schema really is four annotations smaller",
      len(TrialMatchState.__annotations__)
      - len(_StateWithoutPackingKeys.__annotations__), 4)
# NON-DEGENERACY OF THE CONTROL ITSELF. A schema whose key sets are empty
# filters nothing, and every assertion below it would then pass or fail for a
# reason that has nothing to do with the declaration under test.
check("the control schema declares its keys as channels (not inert)",
      len(_StateWithoutPackingKeys.__required_keys__
          | _StateWithoutPackingKeys.__optional_keys__),
      len(_StateWithoutPackingKeys.__annotations__))
check("...and the real schema declares its own the same way",
      len(TrialMatchState.__required_keys__ | TrialMatchState.__optional_keys__),
      len(TrialMatchState.__annotations__))
for _key in PACKING_KEYS:
    check(f"undeclared -> result[{_key}] is None",
          _result_before.get(_key), None)

# The control's own control: a key that IS declared in both schemas travels the
# same route from the same stub, so its survival says the run happened and the
# four Nones above are about the declaration rather than about a broken arm.
check("CONTROL reasoning_tokens (declared in both) survives at 7",
      _result_before.get("llm_classifier_reasoning_tokens"), 7)
check("CONTROL the control arm produced verdicts",
      len(_result_before.get("matches") or []) >= 1, True)

print("\n  2d. why the control has to wrap the nodes, measured rather than argued")
#
# Registering the REAL annotated node on the reduced-schema graph reinstates
# the removed channels, because LangGraph reads the annotation. If this ever
# stops being true the wrapper becomes unnecessary rather than wrong, and the
# check below says which world we are in instead of leaving _unannotated()'s
# docstring asserting it.
_graph_annotated = StateGraph(_StateWithoutPackingKeys)
_graph_annotated.add_node("stage5", node_llm_classifier_evaluation)
_graph_annotated.add_node("finalize", node_finalize)
check("an ANNOTATED node re-adds the channels the reduced schema removed",
      sorted(k for k in PACKING_KEYS if k in _graph_annotated.channels),
      sorted(PACKING_KEYS))

_graph_wrapped = StateGraph(_StateWithoutPackingKeys)
_graph_wrapped.add_node("stage5", _unannotated(node_llm_classifier_evaluation))
_graph_wrapped.add_node("finalize", _unannotated(node_finalize))
check("the wrapped node does not, which is what makes 2c a control",
      [k for k in PACKING_KEYS if k in _graph_wrapped.channels], [])


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 3 -- the per-call ledger, on a run that really packs
# ===========================================================================

print()
print("=" * 70)
print("SECTION 3 -- one ledger entry per call, in order, with per-call tokens")
print("=" * 70)

# Trials fat enough that the packer must cut them into more than one chunk.
# The filler is sized off the configured budget rather than guessed, so this
# section keeps packing if the constant moves.
from oncotriage.config import (                                    # noqa: E402
    CHARS_PER_TOKEN,
    MATCHING_INPUT_TOKEN_BUDGET,
)

_FAT = "x" * (MATCHING_INPUT_TOKEN_BUDGET * CHARS_PER_TOKEN // 2)
_FAT_NCTS = [f"NCT0000001{i}" for i in range(1, 5)]
_fat_stub = _StubOpenAI(_FAT_NCTS, cached_sequence=(0, 4096, 4096, 4096))
deps.set_override(deps.OPENAI_CLIENT, _fat_stub)

_fat = drive(run_through_graph,
             base_state([_trial(n, _FAT) for n in _FAT_NCTS]))
_ledger = _fat.get("llm_classifier_call_details") or []
_packing = _fat.get("llm_classifier_packing") or {}

print(f"\n  (packed into {_fat.get('llm_classifier_packed_chunks')} chunk(s); "
      f"{len(_ledger)} call(s) recorded)")

check("the fat batch really did pack into more than one chunk (non-degenerate)",
      (_fat.get("llm_classifier_packed_chunks") or 0) > 1, True)
check("one ledger entry per call made",
      len(_ledger), _fat.get("llm_classifier_calls"))
# EVERY READING BELOW GOES THROUGH .get() AND NONE OF THEM DIVIDES BY A LENGTH.
# The first version of this section computed a mean over the ledger, so a defect
# that emptied it raised ZeroDivisionError and the file reported one traceback
# where it owed sixty results -- caught by the revert harness, not by reading,
# and it is the same abort-instead-of-report shape four other files in this
# suite have had to fix.
check("call_index is 1..N in order",
      [e.get("call_index") for e in _ledger],
      list(range(1, len(_ledger) + 1)))
check("every entry names its split depth",
      all(isinstance(e.get("depth"), int) for e in _ledger) and bool(_ledger),
      True)
check("every entry names how many trials it asked about",
      all(e.get("trials", 0) >= 1 for e in _ledger) and bool(_ledger), True)
check("the ledger's trial counts sum to the batch",
      sum(e.get("trials", 0) for e in _ledger), len(_FAT_NCTS))
check("the packer's chunk trial counts agree with the ledger's",
      [c.get("trials") for c in _packing.get("chunks") or []],
      [e.get("trials") for e in _ledger if e.get("depth") == 0])

print("\n  3b. the per-call cached figures the totals cannot reconstruct")
_cached_seq = [e.get("cached_tokens") for e in _ledger]
check("first call cached 0, later calls 4096 (cache warmed)",
      _cached_seq, [0] + [4096] * max(len(_ledger) - 1, 0))
check("the total is their sum",
      _fat.get("llm_classifier_cached_input_tokens"),
      sum(v for v in _cached_seq if isinstance(v, int)))
# THE POINT OF THE LEDGER, as an assertion rather than as prose: the readings
# VARY across the calls of one run, so the single total the pipeline reported
# before this pass is consistent with several different cache behaviours and
# cannot distinguish them. Expressed as a set rather than as a mean, which is
# both the honest statement and division-free.
check("the per-call readings are not all equal (a total cannot recover them)",
      len(set(_cached_seq)) > 1, True)
check("prompt_tokens are recorded per call",
      all(e.get("prompt_tokens") == 100 for e in _ledger) and bool(_ledger), True)
check("finish_reason is recorded per call",
      {e.get("finish_reason") for e in _ledger}, {"stop"})

# THE DENOMINATOR THE PER-ENTRY emission_index IS A POSITION OUT OF. Recorded
# here rather than only in tests/test_agent_emission_provenance.py because this
# file owns the ledger ROW's contract and drives the REAL graph, where that file
# drives the node with a stub -- and because the count is taken at a different
# point in the loop from every other field on the row, so "it is present" and
# "it is right" are separate facts. The stub answers one entry per trial it was
# asked about, so on this run the two columns must agree call for call.
check("every entry records how many verdicts the model emitted",
      [e.get("entries_emitted") for e in _ledger],
      [e.get("trials") for e in _ledger])
check("and it is an int on every call of a successful run",
      sorted({type(e.get("entries_emitted")).__name__ for e in _ledger}),
      ["int"])
check("the emitted counts sum to the batch (non-degenerate)",
      sum(e.get("entries_emitted") or 0 for e in _ledger), len(_FAT_NCTS))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 4 -- absence is absence, never zero
# ===========================================================================

print()
print("=" * 70)
print("SECTION 4 -- a provider that reports no cached figure gives None, not 0")
print("=" * 70)

_silent = _StubOpenAI(_NCTS, cached_sequence=(None,))
deps.set_override(deps.OPENAI_CLIENT, _silent)
_no_cache = drive(run_through_graph, base_state([_trial(n) for n in _NCTS]))
_no_cache_ledger = _no_cache.get("llm_classifier_call_details") or []

check("no prompt_tokens_details -> total is None",
      _no_cache.get("llm_classifier_cached_input_tokens"), None)
check("no prompt_tokens_details -> per call is None",
      [e["cached_tokens"] for e in _no_cache_ledger],
      [None] * len(_no_cache_ledger))
check("the run still happened (non-degenerate)",
      len(_no_cache_ledger) >= 1, True)

# A REPORTED ZERO IS A MEASUREMENT AND MUST NOT COLLAPSE INTO THE ABSENCE.
_zero = _StubOpenAI(_NCTS, cached_sequence=(0,))
deps.set_override(deps.OPENAI_CLIENT, _zero)
_zero_result = drive(run_through_graph, base_state([_trial(n) for n in _NCTS]))
check("a reported 0 stays 0, not None",
      _zero_result.get("llm_classifier_cached_input_tokens"), 0)
check("a reported 0 is distinguishable from an absent reading",
      _zero_result.get("llm_classifier_cached_input_tokens")
      is _no_cache.get("llm_classifier_cached_input_tokens"), False)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 5 -- the ledger survives a failure return
# ===========================================================================

print()
print("=" * 70)
print("SECTION 5 -- a call that was made and billed is recorded even on failure")
print("=" * 70)

_broken = _StubOpenAI(_NCTS, cached_sequence=(512,), body="not json at all {{")
deps.set_override(deps.OPENAI_CLIENT, _broken)
_failed = drive(node_llm_classifier_evaluation,
                base_state([_trial(n) for n in _NCTS]))
_failed_ledger = _failed.get("llm_classifier_call_details")

check("the parse really did fail (non-degenerate)",
      "parse error" in (_failed.get("error") or ""), True)
check("the ledger is present on the failure return",
      isinstance(_failed_ledger, list), True)
check("and it records the call that was billed",
      len(_failed_ledger or []), 1)
check("with its cached reading intact",
      at(_failed_ledger or [], 0, {}).get("cached_tokens"), 512)
# ...AND WITH ITS DENOMINATOR ABSENT. entries_emitted is written only once a
# response has parsed into a list, so a call that was billed and produced no
# list carries None -- the mechanism did not run. NOT 0: zero is what a model
# that answered with an empty array emitted, which is a measurement.
check("but the emitted count is None: no list was ever parsed",
      at(_failed_ledger or [], 0, {}).get("entries_emitted", "<absent>"), None)
check("the key is present even so (a consumer never tests for it)",
      "entries_emitted" in at(_failed_ledger or [], 0, {}), True)
# The totals ARE absent on this path, by the node's own design, which is why the
# ledger is the only record that money was spent here.
check("the summed total is absent on the failure return",
      _failed.get("llm_classifier_cached_input_tokens"), None)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 6 -- the evaluation record persists all four
# ===========================================================================

print()
print("=" * 70)
print("SECTION 6 -- run_harness.build_record carries them into the JSON")
print("=" * 70)

from oncotriage.evaluation.run_harness import (                    # noqa: E402
    RESULT_OMITTED_KEYS,
    build_record,
)

deps.set_override(deps.OPENAI_CLIENT, _StubOpenAI(_NCTS, cached_sequence=(1024,)))
_final_state = {"filtered_trials": [_trial(n) for n in _NCTS],
                "llm_classifier_refusal": None}
_res = drive(run_through_graph, base_state([_trial(n) for n in _NCTS]))
_record = drive(build_record,
                {"bundle": "b.json", "patient_id": PATIENT["patient_id"]},
                dict(PATIENT), _final_state, dict(_res),
                {"reason": "spread", "note": "n/a"}, 1.0, [])

_persisted = (_record.get("result") or {})
for _key in PACKING_KEYS:
    check(f"record['result'][{_key}] is persisted",
          _persisted.get(_key), _res.get(_key))
check("none of the four is on the omitted list",
      [k for k in PACKING_KEYS if k in RESULT_OMITTED_KEYS], [])
check("the persisted packed_chunks is non-degenerate",
      (_persisted.get("llm_classifier_packed_chunks") or 0) >= 1, True)
check("the persisted ledger is non-empty",
      len(_persisted.get("llm_classifier_call_details") or []) >= 1, True)


#------------------------------------------------------------------------------


# ===========================================================================
# SUMMARY
# ===========================================================================

print()
print("=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")

if _FAILURES:
    print("\nFailures:")
    for _f in _FAILURES:
        print(f"  - {_f}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug 12 2026

@author: ramyalsaffar
"""
