###################################################################
# Stage 5: where in the model's answer each verdict stood
###################################################################

"""
Emission Provenance Test

THE ORDER IN WHICH THE MODEL WROTE ITS ANSWERS USED TO BE DISCARDED.
``node_llm_classifier_evaluation`` parses each response into a list, drops the
entries it cannot use, and then sorts what survives by match_score descending;
``node_finalize`` afterwards assigns ``trial_number`` from the pipeline's own
retrieval ranking. Nothing anywhere kept the position an entry occupied in the
model's returned array, so the open analysis question -- whether the
first-written answer in a request predicts the rest of that request's answers --
was not askable from any artifact this pipeline produces.

WHAT WAS ADDED. Two integer fields, computed by the pipeline from the parsed
response and never asked of the model:

  ``emission_index``  0-based position of the entry in the array THIS call
                      returned;
  ``call_index``      the ordinal of the billed call that returned it.

The response schema, the prompt text and PROMPT_VERSION are untouched, and no
sort, drop or verdict decision reads either field. Section 7 proves that last
claim by AST rather than by assertion.

``call_index`` IS 1-BASED AND THAT IS DELIBERATE. The brief said 0-based and
also said "consistent with the existing per-call ledger
``llm_classifier_call_details``", and those two requirements contradict each
other: the ledger numbers its calls 1..N (``calls_made`` is incremented before
the append, and tests/test_agent_state_channel_coverage.py pins "call_index is
1..N in order"). Consistency won. Two fields of one result dict sharing a name
and disagreeing about their origin is an off-by-one join that a reader gets
wrong silently and forever, which is exactly the class of defect this project
exists to remove. Section 2 asserts the join by equality against the ledger, so
the decision is enforced rather than merely documented.

``emission_index`` stays 0-based: it is a position in a list and there is no
prior art in the result to disagree with.

PIPELINE-CONSTRUCTED ENTRIES CARRY None FOR BOTH, never 0. ``_unevaluable_entry``
builds the verdict-shaped record for a truncation floor, an exhausted split
budget, conflicting duplicates and a model omission; none of those ever stood in
a model response, and 0 would name the first entry of the first call -- a real
place another trial occupies. Section 4 covers all four.

NOT EVERY not_evaluable ENTRY IS PIPELINE-CONSTRUCTED, and the brief blurred
this. A trial whose trial-level verdict label was UNRECOGNISED is a MODEL entry
that the normalizer rewrote in place; it stood in the response, so it keeps its
real emission position. Section 4e is that distinction, and it is the one that
would silently be got wrong by anyone reading "the unevaluable entries carry
None" as covering all of them.

NO NETWORK, NO KEYS, NO SPEND, NO GIT HISTORY, NO CORPUS. Every model response
here is a literal built in this file and served by a stub installed through
``oncotriage/agent/deps.py``. NOT in tests/run_serial_tests.py's collision
matrix: it writes nothing anywhere -- every plant goes into an in-memory copy of
the module, with the source file hashed before any plant and compared at the end.

    python tests/test_agent_emission_provenance.py
"""

import ast
import contextlib
import hashlib
import io
import json
import os
import re
import sys
import types

try:
    import oncotriage                                          # noqa: F401
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

from oncotriage.agent import deps
from oncotriage.agent import evaluation as _evaluation_module
from oncotriage.agent.evaluation import (
    NOT_EVALUABLE_MODEL_OMITTED,
    NOT_EVALUABLE_SPLIT_BUDGET,
    NOT_EVALUABLE_TRUNCATION_FLOOR,
    NOT_EVALUABLE_CONFLICTING_DUPLICATES,
    node_llm_classifier_evaluation,
)
from oncotriage.agent.terminal import node_finalize


# ===========================================================================
# HARNESS
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
        _FAILURES.append(f"{label}\n          expected: {expected}\n"
                         f"          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


# The module under test, located from its OWN __file__ rather than from this
# test's directory, so a future move of either cannot silently point the plants
# at a same-named copy.
_EVAL_SRC = os.path.abspath(_evaluation_module.__file__)


def _sha256_of(path):
    return hashlib.sha256(
        open(path, encoding="utf-8").read().encode()).hexdigest()


# Taken before any plant runs, so the restore assertion in section 10 compares
# against a real baseline rather than against itself.
_SHA_BEFORE = _sha256_of(_EVAL_SRC)


class _PlantFailed(Exception):
    """A plant that did not apply or did not compile. Never escapes a check."""


_CONTROL_SEQ = [0]


def _plant(subs):
    """Exec an in-memory COPY of evaluation.py with `subs` applied.

    Raises _PlantFailed -- never SyntaxError -- so a malformed plant is a
    RECORDED failure instead of a traceback hiding every check below it. The
    file on disk is hashed before and after and a modification raises.
    """
    _CONTROL_SEQ[0] += 1
    source = open(_EVAL_SRC, encoding="utf-8").read()
    before = hashlib.sha256(source.encode()).hexdigest()
    try:
        for old, new in subs:
            if old not in source:
                raise _PlantFailed(f"plant target absent: {old[:70]!r}...")
            if source.count(old) != 1:
                raise _PlantFailed(
                    f"plant target is not unique ({source.count(old)} hits): "
                    f"{old[:70]!r}...")
            source = source.replace(old, new, 1)
        module = types.ModuleType(f"planted_evaluation_{_CONTROL_SEQ[0]}")
        module.__file__ = _EVAL_SRC
        exec(compile(source, _EVAL_SRC, "exec"), module.__dict__)
    except _PlantFailed:
        raise
    except Exception as exc:            # noqa: BLE001 - reported, not raised
        raise _PlantFailed(f"{type(exc).__name__}: {exc}") from None
    finally:
        after = _sha256_of(_EVAL_SRC)
        if before != after:
            raise AssertionError(f"{_EVAL_SRC} was modified on disk by a plant")
    return module


def control(label, subs, probe, planted_expected):
    """Plant, run the probe through BOTH arms, record two facts.

    The probe is called once with the SHIPPED node and once with the planted
    one, and two things are asserted: the planted arm produces exactly the wrong
    answer named in `planted_expected`, and it DIFFERS from the shipped arm.

    The second assertion is the one that makes the first mean anything. A
    control whose planted arm happens to agree with the shipped code is not a
    control -- it passes, so it looks like it is working, which is the failure
    mode this project has shipped before. Running the shipped arm here rather
    than trusting the sections above also means a control cannot be satisfied by
    a probe that stopped exercising anything.

    A raise IS an outcome in either arm, never a reason to abort.
    """
    def _run(node):
        try:
            return probe(node)
        except Exception as exc:        # noqa: BLE001 - a raise IS an outcome
            return f"raised {type(exc).__name__}"

    shipped = _run(node_llm_classifier_evaluation)
    try:
        module = _plant(subs)
    except _PlantFailed as exc:
        check(f"{label}  [THE PLANT ITSELF FAILED: {exc}]", "plant-failed",
              planted_expected)
        return
    planted = _run(module.node_llm_classifier_evaluation)
    check(label, planted, planted_expected)
    check(f"{label}  -- and it differs from the shipped node",
          planted != shipped, True)


# ===========================================================================
# FIXTURES: a patient, some trials, and a stub that serves chosen responses
# ===========================================================================

PATIENT = {
    "patient_id": "emission-provenance-patient",
    "demographics": {"age": 62, "sex": "male", "race": "white",
                     "ethnicity": "not hispanic or latino"},
    "conditions": [{"code": "254637007",
                    "display": "Non-small cell lung cancer",
                    "verification_status": "confirmed"}],
    "medications": [], "allergies": [], "observations": [], "procedures": [],
}


def trial(nct_id):
    """One trial, carrying every field Stage 5 and Stage 6 read off it."""
    return {
        "nct_id": nct_id, "title": "A Study of Something", "phase": "Phase 2",
        "conditions": ["Lung Neoplasms"], "mesh_terms": ["Lung Neoplasms"],
        "eligibility": {"inclusion_criteria": "Adults with NSCLC",
                        "exclusion_criteria": "Pregnancy",
                        "min_age": 18, "max_age": 99, "sex": "ALL"},
    }


def crit(status, patient_value="Documented in the patient record"):
    """One criterion. patient_value is deliberately NOT an absent-data phrase:
    the absent-data validator would otherwise rewrite the status underneath the
    case being tested."""
    return {"criterion": "an eligibility criterion", "status": status,
            "patient_value": patient_value}


def entry(nct_id, n_uneval=0, eligible="eligible", **extra):
    """One evaluation entry as the model returns it.

    Eligible by default with one met inclusion criterion, so nothing in the
    post-processing loop rewrites the verdict underneath a test that is about
    provenance rather than about verdicts.

    THE MODEL'S OWN ``match_score`` IS NOT THE ONE THAT SORTS, which is worth
    stating because the first version of this file set it directly and every
    ordering assertion in sections 3 and 5 was wrong. Stage 5 RECOMPUTES the
    score from the criteria arrays (``_compute_match_score``: confirmed over
    applicable) and overwrites whatever the model wrote. So the score is steered
    here the only way it can be -- through the arrays. ``n_uneval`` adds that
    many ``not_evaluable`` inclusion criteria, each of which lands in the
    denominator and not the numerator:

        n_uneval=0 -> 2/2 = 1.0     n_uneval=1 -> 2/3 = 0.67
        n_uneval=2 -> 2/4 = 0.5

    A field named ``match_score`` is still sent, at a value none of these tests
    depends on, because that is the shape a real response has.
    """
    payload = {
        "nct_id": nct_id,
        "eligible": eligible,
        "match_score": 0.5,
        "assessment": "No known disqualifiers.",
        "inclusion_criteria": [crit("met")] + [crit("not_evaluable")] * n_uneval,
        "exclusion_criteria": [crit("not_violated")],
    }
    payload.update(extra)
    return payload


class _StubMessage:
    def __init__(self, content):
        self.content = content


class _StubChoice:
    def __init__(self, content, finish_reason):
        self.message = _StubMessage(content)
        self.finish_reason = finish_reason


class _StubUsage:
    prompt_tokens = 1000
    completion_tokens = 200


class _StubResponse:
    def __init__(self, content, finish_reason):
        self.choices = [_StubChoice(content, finish_reason)]
        self.usage = _StubUsage()
        # None means "the response carried no model field", which
        # node_llm_classifier_evaluation handles explicitly and which keeps
        # MatchingModelMismatchError out of a test that is not about it.
        self.model = None


class StubOpenAI:
    """Serves a SEQUENCE of chosen responses, one per call.

    Each element is either a payload (answered with finish_reason "stop") or a
    ``(payload, finish_reason)`` pair. The last element is repeated if the node
    makes more calls than the script provides, so a script that is one short
    produces a comprehensible result rather than an IndexError inside the node.
    """

    def __init__(self, script):
        self._script = [s if isinstance(s, tuple) else (s, "stop")
                        for s in script]
        self.calls = 0
        self.chat = self
        self.completions = self

    def create(self, model, messages, **kwargs):
        payload, finish = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return _StubResponse(text, finish)


class ChunkAwareStub:
    """Answers each call for exactly the trials THAT call was sent.

    The nct_ids are read out of the rendered user prompt, which is what makes
    section 2b robust: a scripted stub has to know how the packer will divide
    the batch, and that division is a function of the prompt text and of a
    config constant. This one answers whatever it is asked, so a repack changes
    the shape of the run and not the correctness of the test.

    ``order`` decides the order the answers are WRITTEN in, which is the whole
    subject here: "sent" reproduces the order the chunk was sent in, "reversed"
    writes them backwards, so the emission order cannot be mistaken for the
    order the pipeline chose.
    """

    _NCT = re.compile(r"NCT\d{8}")

    def __init__(self, order="sent"):
        self.order = order
        self.calls = 0
        self.sent = []            # the ids each call was sent, in order
        self.answered = []        # the ids each call answered, in order
        self.chat = self
        self.completions = self

    def create(self, model, messages, **kwargs):
        # THE USER MESSAGE ONLY. The SYSTEM prompt carries worked examples whose
        # placeholder ids (NCT12345678, NCT87654321) are real-looking and are
        # not in any sent set -- reading the whole conversation made this stub
        # answer for two trials nobody asked about, which the node then dropped
        # as fabricated. Measured, not reasoned about: the first version of this
        # section failed with those two ids in its expectation.
        text = "\n".join(str(m.get("content", "")) for m in messages
                         if m.get("role") == "user")
        seen, ids = set(), []
        for nct in self._NCT.findall(text):
            if nct not in seen:
                seen.add(nct)
                ids.append(nct)
        self.sent.append(list(ids))
        if self.order == "reversed":
            ids = list(reversed(ids))
        self.answered.append(list(ids))
        self.calls += 1
        return _StubResponse(
            json.dumps({"evaluations": [entry(n) for n in ids]}), "stop")


# A RAISE IS AN OUTCOME, NOT A REASON TO ABORT. A plant that breaks the stamp
# can make the node raise, and with a bare call that raise would escape through
# check()'s argument list and take the file down at module level -- reporting
# one traceback where it owed every result below. The same defect has shipped in
# tests/test_storage_query_layer.py, tests/test_dashboard_reproducibility_tab.py,
# tests/test_docker_qdrant_override_and_readiness.py,
# tests/test_agent_age_units_and_sex_filter.py and
# tests/test_agent_trial_verdict_normalization.py.

def _raised_result(exc):
    return {"evaluations": [], "raised": type(exc).__name__,
            "llm_classifier_call_details": []}


def run_stage5(script, nct_ids=("NCT00000001",), node=None):
    """Drive Stage 5 with a stubbed model. Returns (result, stderr_text).

    `script` is either a response script for StubOpenAI or an already-built
    client stub (anything carrying ``create``), which is how section 2b installs
    the chunk-aware one.
    """
    node = node or node_llm_classifier_evaluation
    state = {
        "patient_data": PATIENT,
        "filtered_trials": [{"trial": trial(n), "rerank_score": 5.0,
                             "rerank_score_raw": 5.0} for n in nct_ids],
        "llm_classifier_retries": 0,
        "mesh_filter_applied": True,
        "mesh_filter_skip_reason": "applied",
        "stage_timings": {},
    }
    stub = script if hasattr(script, "create") else StubOpenAI(script)
    saved = deps.set_overrides({"openai_client": stub})
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            result = node(state)
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        result = _raised_result(exc)
    finally:
        deps.restore_overrides(saved)
    return result, err.getvalue()


def run_stage6(evaluations, nct_ids=("NCT00000001",)):
    """Drive Stage 6 over a chosen evaluation list."""
    state = {
        "patient_data": PATIENT, "evaluations": evaluations,
        "filtered_trials": [{"trial": trial(n), "rerank_score": 5.0,
                             "rerank_score_raw": 5.0} for n in nct_ids],
        "stage_timings": {},
    }
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            out = node_finalize(state)["result"]
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        out = {"matches": [], "near_misses": [], "not_evaluable": [],
               "raised": type(exc).__name__}
    return out


def stamps(result):
    """{nct_id: (emission_index, call_index)} off a Stage 5 result.

    ``<absent>`` rather than a KeyError for a missing field: a plant that stops
    the stamp being written is exactly what the controls exercise, and it must
    produce a recorded FAILURE with a readable value rather than an exception.
    """
    out = {}
    for e in result.get("evaluations") or []:
        out[e.get("nct_id", "<no id>")] = (
            e.get("emission_index", "<absent>"),
            e.get("call_index", "<absent>"),
        )
    return out


def stamps_from(entries):
    """The same mapping over a plain list of verdict dicts."""
    return {e.get("nct_id", "<no id>"): (e.get("emission_index", "<absent>"),
                                         e.get("call_index", "<absent>"))
            for e in entries}


def order_of(result):
    """The nct_ids of a Stage 5 result, in the order the node left them."""
    return [e.get("nct_id") for e in result.get("evaluations") or []]


def ledger_calls(result):
    """The call ordinals the per-call ledger recorded."""
    return [d.get("call_index")
            for d in result.get("llm_classifier_call_details") or []]


TRIALS_4 = ("NCT00000001", "NCT00000002", "NCT00000003", "NCT00000004")


# ===========================================================================
print("=" * 75)
print("SECTION 1 -- one call: emission_index is the array position")
print("=" * 75)
# ===========================================================================

# Three identically-scored entries, so this section is about the mapping from
# array position to field value and nothing else. Section 5 is the case where
# the emission order and the output order disagree.
_r1, _ = run_stage5(
    [{"evaluations": [entry("NCT00000001"),
                      entry("NCT00000002"),
                      entry("NCT00000003")]}],
    nct_ids=TRIALS_4[:3])

check("1a  three verdicts came back", len(_r1.get("evaluations") or []), 3)
check("1b  emission_index is 0,1,2 in array order",
      stamps(_r1),
      {"NCT00000001": (0, 1), "NCT00000002": (1, 1), "NCT00000003": (2, 1)})
check("1c  one call was made", ledger_calls(_r1), [1])
check("1d  every entry's call_index is IN the ledger",
      sorted({s[1] for s in stamps(_r1).values()}), ledger_calls(_r1))
# Non-degeneracy: the three emission indices are distinct, so 1b is not three
# readings of one constant.
check("1e  non-degeneracy: the emission indices are distinct",
      len({s[0] for s in stamps(_r1).values()}), 3)

# A bare array is the legacy response shape and it takes the same stamp: the
# unwrap happens above the stamp, so both envelopes reach it identically.
_r1b, _ = run_stage5(
    [[entry("NCT00000001"), entry("NCT00000002")]],
    nct_ids=TRIALS_4[:2])
check("1f  a bare-array response is stamped identically",
      stamps(_r1b), {"NCT00000001": (0, 1), "NCT00000002": (1, 1)})

# The fields are OURS. An entry that arrives carrying either key is overwritten
# rather than trusted -- the name means "where this pipeline saw it".
_r1c, _ = run_stage5(
    [{"evaluations": [entry("NCT00000001", emission_index=99,
                            call_index=99),
                      entry("NCT00000002")]}],
    nct_ids=TRIALS_4[:2])
check("1g  a model-supplied emission_index/call_index is overwritten",
      stamps(_r1c), {"NCT00000001": (0, 1), "NCT00000002": (1, 1)})

# Both fields are ints (or None) -- never a string, never a float. A consumer
# grouping by call has to be able to compare them with the ledger's ints.
check("1h  both fields are ints",
      sorted({type(v).__name__ for s in stamps(_r1).values() for v in s}),
      ["int"])


# ===========================================================================
print("\n" + "=" * 75)
print("SECTION 2 -- several calls: call_index tracks the billed call")
print("=" * 75)
# ===========================================================================

# A TRUNCATION SPLIT, which is the cheapest way to make the node issue more
# than one call without patching a config constant. Call 1 answers with
# finish_reason "length" over all four trials; the node halves the chunk and
# retries as two calls. So calls 2 and 3 carry the verdicts and call 1 carries
# none -- which is the interesting case, because it is exactly the situation an
# index built on "the Nth response" rather than "the Nth call" would get wrong.
_r2, _err2 = run_stage5(
    [({"evaluations": []}, "length"),
     {"evaluations": [entry("NCT00000001"), entry("NCT00000002")]},
     {"evaluations": [entry("NCT00000003"), entry("NCT00000004")]}],
    nct_ids=TRIALS_4)

check("2a  three calls were billed", ledger_calls(_r2), [1, 2, 3])
check("2b  the split happened", _r2.get("llm_classifier_truncation_splits"), 1)
check("2c  emission_index restarts at 0 in each call, call_index follows the "
      "billed call",
      stamps(_r2),
      {"NCT00000001": (0, 2), "NCT00000002": (1, 2),
       "NCT00000003": (0, 3), "NCT00000004": (1, 3)})
check("2d  every entry's call_index is IN the ledger",
      sorted({s[1] for s in stamps(_r2).values()}) ==
      sorted(set(ledger_calls(_r2)) & {2, 3}), True)
# THE JOIN, stated as the thing it is for: an entry's call_index selects the
# ledger row that carries that call's token counts.
_by_call = {d["call_index"]: d for d in _r2["llm_classifier_call_details"]}
check("2e  an entry joins its own ledger row by equality",
      [_by_call[c]["trials"] for _, c in sorted(stamps(_r2).values())],
      [2, 2, 2, 2])
# Non-degeneracy: two distinct call ordinals are represented, so 2c is not one
# call reported four times.
check("2f  non-degeneracy: two distinct call ordinals appear",
      sorted({s[1] for s in stamps(_r2).values()}), [2, 3])
# The wasted call is still in the ledger and owns no entry -- which is what
# makes 1-based-and-shared meaningful rather than cosmetic.
check("2g  the truncated call is in the ledger and owns no entry",
      1 in ledger_calls(_r2) and 1 not in {s[1] for s in stamps(_r2).values()},
      True)


# --- 2b  THE PACKING PATH, which is the NORMAL way a run makes several calls --
#
# Section 2 forces several calls by TRUNCATING the first one, which is the
# exceptional route. Input packing is the ordinary one: the batch is divided
# before anything is sent, every call succeeds, and nothing is retried. Both
# have to number correctly, and they reach `calls_made` by different code paths.
#
# The budget is lowered on the module under test so the packer divides a
# six-trial batch, and the assertions are DERIVED from what the packer actually
# did rather than pinned to a chunk shape -- a hardcoded split would silently
# stop testing anything the first time the prompt text changed.
_PACK_STUB = ChunkAwareStub(order="reversed")
_saved_budget = _evaluation_module.MATCHING_INPUT_TOKEN_BUDGET
try:
    _evaluation_module.MATCHING_INPUT_TOKEN_BUDGET = 4800
    _r2b, _ = run_stage5(_PACK_STUB,
                         nct_ids=tuple("NCT0000000%d" % i for i in range(1, 7)))
finally:
    _evaluation_module.MATCHING_INPUT_TOKEN_BUDGET = _saved_budget

_pack_ledger = ledger_calls(_r2b)
check("2h  NON-DEGENERACY: packing divided the batch into several calls",
      len(_pack_ledger) > 1, True)
check("2i  NON-DEGENERACY: and it did so WITHOUT a truncation split",
      _r2b.get("llm_classifier_truncation_splits"), 0)
check("2j  the ledger is 1..N in order", _pack_ledger,
      list(range(1, len(_pack_ledger) + 1)))
check("2k  every trial came back", len(_r2b.get("evaluations") or []), 6)

# Per call: the entries carrying that call_index occupy emission positions
# 0..k-1 with no gap, and there are as many of them as that call answered.
_by_call_2b = {}
for _e in _r2b["evaluations"]:
    _by_call_2b.setdefault(_e.get("call_index"), []).append(_e)
check("2l  every entry's call_index is a real ledger ordinal",
      sorted(_by_call_2b), _pack_ledger)
check("2m  each call's emission indices are 0..k-1 with no gap",
      {c: sorted(e["emission_index"] for e in es)
       for c, es in _by_call_2b.items()},
      {c: list(range(len(_PACK_STUB.answered[c - 1])))
       for c in _by_call_2b})
# THE ORDER IS THE MODEL'S, NOT THE PIPELINE'S. The stub answered each chunk
# BACKWARDS, so an implementation that numbered by the order the trials were
# SENT would disagree with every call.
check("2n  emission order is the order the model WROTE, not the order sent",
      {c: [e["nct_id"] for e in sorted(es, key=lambda x: x["emission_index"])]
       for c, es in _by_call_2b.items()},
      {c: _PACK_STUB.answered[c - 1] for c in _by_call_2b})
check("2o  NON-DEGENERACY: written and sent order really do differ",
      _PACK_STUB.answered != _PACK_STUB.sent, True)


# ===========================================================================
print("\n" + "=" * 75)
print("SECTION 3 -- the downstream sort and node_finalize do not strip them")
print("=" * 75)
# ===========================================================================

# THE SCORES ASCEND AS EMITTED (0.5, 1.0, 0.67), so the node's own
# match_score-descending sort MUST reorder the list. That is what makes 3b a
# statement about SURVIVAL rather than about a list nothing touched. And the
# retrieval order handed to node_finalize is a THIRD order again, so the
# pipeline rank cannot be mistaken for either.
_RANK_ORDER = ("NCT00000003", "NCT00000001", "NCT00000002")
_r3, _ = run_stage5(
    [{"evaluations": [entry("NCT00000001", 2),     # 2/4 = 0.5
                      entry("NCT00000002", 0),     # 2/2 = 1.0
                      entry("NCT00000003", 1)]}],  # 2/3 = 0.67
    nct_ids=_RANK_ORDER)

check("3a  the recomputed scores are the ones that sort",
      [e["match_score"] for e in _r3["evaluations"]], [1.0, 0.67, 0.5])
check("3b  the node's sort reordered the list",
      order_of(_r3), ["NCT00000002", "NCT00000003", "NCT00000001"])
check("3c  every entry still carries both fields after the sort",
      stamps(_r3),
      {"NCT00000001": (0, 1), "NCT00000002": (1, 1), "NCT00000003": (2, 1)})
check("3d  no entry lost a field to the sort",
      any("<absent>" in st for st in stamps(_r3).values()), False)

_f3 = run_stage6(_r3["evaluations"], nct_ids=_RANK_ORDER)
_merged = (_f3.get("matches", []) + _f3.get("near_misses", [])
           + _f3.get("not_evaluable", []))
check("3e  node_finalize published all three verdicts", len(_merged), 3)
check("3f  the fields survive node_finalize's grouping",
      stamps_from(_merged),
      {"NCT00000001": (0, 1), "NCT00000002": (1, 1), "NCT00000003": (2, 1)})
# node_finalize assigns trial_number from the PIPELINE's ranking, which is a
# different fact from the model's emission order. Asserting they differ is what
# says the new field carries information trial_number does not.
_tn = {e["nct_id"]: e.get("trial_number") for e in _merged}
check("3g  trial_number is the pipeline rank, not the emission order",
      [_tn[n] for n in _RANK_ORDER], [1, 2, 3])
check("3h  NON-DEGENERACY: trial_number and emission_index disagree",
      [_tn[n] - 1 for n in ("NCT00000001", "NCT00000002", "NCT00000003")]
      != [stamps_from(_merged)[n][0]
          for n in ("NCT00000001", "NCT00000002", "NCT00000003")], True)


# ===========================================================================
print("\n" + "=" * 75)
print("SECTION 4 -- pipeline-constructed entries carry None for both")
print("=" * 75)
# ===========================================================================

# 4a  RECONCILIATION: the model returned a well-formed response that simply had
# no entry for the second trial.
_r4a, _ = run_stage5(
    [{"evaluations": [entry("NCT00000001")]}],
    nct_ids=TRIALS_4[:2])
check("4a  a reconciliation entry carries (None, None)",
      stamps(_r4a),
      {"NCT00000001": (0, 1), "NCT00000002": (None, None)})
check("4a' and it is recorded as the model-omitted reason",
      [e.get("not_evaluable_reason") for e in _r4a["evaluations"]
       if e["nct_id"] == "NCT00000002"], [NOT_EVALUABLE_MODEL_OMITTED])

# 4b  TRUNCATION FLOOR: one trial, sent alone, still over the ceiling.
_r4b, _ = run_stage5([({"evaluations": []}, "length")],
                     nct_ids=TRIALS_4[:1])
check("4b  a truncation-floor entry carries (None, None)",
      stamps(_r4b), {"NCT00000001": (None, None)})
check("4b' and it is recorded as the truncation-floor reason",
      [e.get("not_evaluable_reason") for e in _r4b["evaluations"]],
      [NOT_EVALUABLE_TRUNCATION_FLOOR])

# 4c  SPLIT BUDGET EXHAUSTED: every response truncates, so the halving runs out
# of budget with more than one trial still in the chunk.
_r4c, _ = run_stage5([({"evaluations": []}, "length")], nct_ids=TRIALS_4)
_reasons_4c = sorted({e.get("not_evaluable_reason")
                      for e in _r4c["evaluations"]})
check("4c  every entry of an all-truncating run carries (None, None)",
      sorted(set(stamps(_r4c).values())), [(None, None)])
check("4c' the run ended on the split budget or the floor",
      set(_reasons_4c) <= {NOT_EVALUABLE_SPLIT_BUDGET,
                           NOT_EVALUABLE_TRUNCATION_FLOOR}, True)
check("4c'' non-degeneracy: all four trials are accounted for",
      len(_r4c["evaluations"]), 4)

# 4d  CONFLICTING DUPLICATES: two entries for one trial that disagree on the
# verdict. Both model entries are discarded and a constructed one replaces
# them, so the surviving entry must NOT inherit either emission position.
_r4d, _ = run_stage5(
    [{"evaluations": [
        entry("NCT00000001", eligible="eligible"),
        entry("NCT00000001", eligible="not_eligible",
              inclusion_criteria=[crit("not_met")]),
        entry("NCT00000002"),
    ]}],
    nct_ids=TRIALS_4[:2])
check("4d  a conflicting-duplicate entry carries (None, None)",
      stamps(_r4d).get("NCT00000001"), (None, None))
check("4d' the trial beside it keeps its real emission position",
      stamps(_r4d).get("NCT00000002"), (2, 1))
check("4d'' and it is recorded as the conflicting-duplicates reason",
      [e.get("not_evaluable_reason") for e in _r4d["evaluations"]
       if e["nct_id"] == "NCT00000001"],
      [NOT_EVALUABLE_CONFLICTING_DUPLICATES])

# 4e  THE DISTINCTION THE BRIEF BLURRED. A trial whose trial-level verdict label
# was UNRECOGNISED is recorded as not_evaluable -- but it is a MODEL entry,
# rewritten in place by the normalizer, and it DID stand in the response. It
# keeps its real emission position. Reading "unevaluable entries carry None" as
# covering this case would erase a genuine measurement.
_r4e, _ = run_stage5(
    [{"evaluations": [entry("NCT00000001"),
                      entry("NCT00000002", eligible="perhaps",
                            inclusion_criteria=[], exclusion_criteria=[])]}],
    nct_ids=TRIALS_4[:2])
check("4e  an unrecognised-verdict entry is not_evaluable...",
      [e.get("eligible") for e in _r4e["evaluations"]
       if e["nct_id"] == "NCT00000002"], ["not_evaluable"])
check("4e' ...and KEEPS its emission position, because the model emitted it",
      stamps(_r4e).get("NCT00000002"), (1, 1))


# ===========================================================================
print("\n" + "=" * 75)
print("SECTION 5 -- the negative control: a shuffled response")
print("=" * 75)
# ===========================================================================

# THE ONE THAT MATTERS. The model writes its answers in an order the node's sort
# then changes. emission_index must follow the SHUFFLED emission, not the sorted
# output position -- an implementation that renumbered after the sort would pass
# every check above and fail here.
_SHUFFLED = [{"evaluations": [
    entry("NCT00000001", 2),      # emitted first,  scores 0.50, sorts LAST
    entry("NCT00000002", 0),      # emitted second, scores 1.00, sorts FIRST
    entry("NCT00000003", 1)]}]    # emitted third,  scores 0.67, sorts SECOND
_r5, _ = run_stage5(_SHUFFLED, nct_ids=TRIALS_4[:3])

_final_order = order_of(_r5)
_emission_order = [n for n, _ in sorted(
    ((e["nct_id"], e["emission_index"]) for e in _r5["evaluations"]),
    key=lambda p: p[1])]

check("5a  the final order is the score order",
      _final_order, ["NCT00000002", "NCT00000003", "NCT00000001"])
check("5b  the emission order is the order the model wrote",
      _emission_order, ["NCT00000001", "NCT00000002", "NCT00000003"])
check("5c  NON-DEGENERACY: the two orders differ",
      _final_order != _emission_order, True)
check("5d  emission_index is NOT the final list position",
      [e["emission_index"] for e in _r5["evaluations"]], [1, 2, 0])
check("5e  emission_index IS the position in the model's array",
      stamps(_r5),
      {"NCT00000001": (0, 1), "NCT00000002": (1, 1), "NCT00000003": (2, 1)})


# ===========================================================================
print("\n" + "=" * 75)
print("SECTION 6 -- a dropped entry does not renumber the survivors")
print("=" * 75)
# ===========================================================================

# The stamp runs on the FULL parsed list, above every filter, so the positions
# an entry occupied in the model's array survive the removal of its neighbours.
# A bare string at position 0 (dropped as a non-object) and an out-of-set id at
# position 2 (dropped as fabricated) leave the survivors at 1 and 3 -- with a
# GAP, which is the honest record: the model wrote four things.
_r6, _ = run_stage5(
    [{"evaluations": ["NCT00000001",                       # 0: not an object
                      entry("NCT00000001"),                # 1: kept
                      entry("NCT09999999"),                # 2: fabricated id
                      entry("NCT00000002")]}],             # 3: kept
    nct_ids=TRIALS_4[:2])

check("6a  the two survivors keep their real positions, gaps and all",
      stamps(_r6), {"NCT00000001": (1, 1), "NCT00000002": (3, 1)})
check("6b  non-degeneracy: the drops really happened",
      (_r6.get("hallucinated_trials"), len(_r6["evaluations"])), (1, 2))
check("6c  a renumbered-after-drop implementation would have said (0,1),(1,1)",
      stamps(_r6) == {"NCT00000001": (0, 1), "NCT00000002": (1, 1)}, False)


# ===========================================================================
print("\n" + "=" * 75)
print("SECTION 7 -- provenance only: nothing in the agent READS either field")
print("=" * 75)
# ===========================================================================

# THE CLAIM IS "no behaviour change", and the only way to hold it is to show
# that no code branches on the two names. A walk over oncotriage/agent/, in
# BOTH forms a dict key is read by: a subscript in a Load context
# (``e["emission_index"]``) and a ``.get("emission_index")`` call.
#
# Scoped to oncotriage/agent/ deliberately: ``call_index`` is also the name of a
# field on the fixture harness's OWN client-call records
# (oncotriage/fixtures/capture.py), which is a different fact about a different
# object, and folding those in would make this check report a conflict that does
# not exist.
_AGENT_DIR = os.path.dirname(_EVAL_SRC)
_NAMES = ("emission_index", "call_index")


def _reads_of(path, names):
    """(lineno, form) for every READ of `names` as a dict key in `path`."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    hits = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Subscript)
                and isinstance(node.ctx, ast.Load)
                and isinstance(node.slice, ast.Constant)
                and node.slice.value in names):
            hits.append((node.lineno, "subscript"))
        elif (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in names):
            hits.append((node.lineno, "get"))
    return hits


_agent_py = sorted(
    os.path.join(dp, f)
    for dp, dn, fn in os.walk(_AGENT_DIR) if "__pycache__" not in dp
    for f in fn if f.endswith(".py"))
_agent_reads = {os.path.basename(p): _reads_of(p, _NAMES) for p in _agent_py}

check("7a  nothing in oncotriage/agent/ reads emission_index or call_index",
      {k: v for k, v in _agent_reads.items() if v}, {})
check("7b  non-degeneracy: the walk found the agent modules",
      len(_agent_py) >= 10, True)
# The scan must be able to SEE a read, in both forms, or 7a is vacuous.
_probe = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "__emission_probe_not_written__.py")
_probe_src = ('x = {}\n'
              'a = x["emission_index"]\n'
              'b = x.get("call_index")\n'
              'x["emission_index"] = 1\n')
_probe_tree = ast.parse(_probe_src)
_probe_hits = []
for _node in ast.walk(_probe_tree):
    if (isinstance(_node, ast.Subscript) and isinstance(_node.ctx, ast.Load)
            and isinstance(_node.slice, ast.Constant)
            and _node.slice.value in _NAMES):
        _probe_hits.append("subscript")
    elif (isinstance(_node, ast.Call) and isinstance(_node.func, ast.Attribute)
            and _node.func.attr == "get" and _node.args
            and isinstance(_node.args[0], ast.Constant)
            and _node.args[0].value in _NAMES):
        _probe_hits.append("get")
check("7c  non-degeneracy: the scan sees both read forms and ignores the write",
      sorted(_probe_hits), ["get", "subscript"])

# The two WRITE sites are where they are claimed to be: in evaluation.py, above
# the first filter. Asserted by line number ordering rather than by text, so a
# reformat does not fail it but a MOVE does.
_eval_tree = ast.parse(open(_EVAL_SRC, encoding="utf-8").read())
_write_lines = sorted(
    n.lineno for n in ast.walk(_eval_tree)
    if isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Store)
    and isinstance(n.slice, ast.Constant) and n.slice.value in _NAMES)
_extend_line = min(
    (n.lineno for n in ast.walk(_eval_tree)
     if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
     and n.func.attr == "extend" and isinstance(n.func.value, ast.Name)
     and n.func.value.id == "evaluations"), default=-1)
check("7d  there are exactly two subscript writes of the two names",
      len(_write_lines), 2)
check("7e  both writes precede the first `evaluations.extend(...)`",
      _extend_line > 0 and max(_write_lines) < _extend_line, True)

# The response schema was NOT touched: neither name may appear in it.
_schema_src = open(
    os.path.join(_AGENT_DIR, "response_schema.py"), encoding="utf-8").read()
check("7f  neither name appears in the Structured Outputs schema module",
      [n for n in _NAMES if n in _schema_src], [])


# The exact source spans the plants below replace. Every one is asserted to
# occur exactly once, so a reformat produces a RECORDED plant failure rather
# than a control that silently stopped controlling.
_STAMP = ('        for _emission_index, _entry in enumerate(parsed):\n'
          '            if isinstance(_entry, dict):\n'
          '                _entry["emission_index"] = _emission_index\n'
          '                _entry["call_index"] = calls_made\n')
_UNEVAL = ('        "emission_index": None,\n'
           '        "call_index": None,\n')
_EXTEND = '        evaluations.extend(_objects)\n'
_AFTER_SORT = ('    elapsed = time.time() - start\n'
               '    log.info("Stage 5 evaluation complete"')

# ===========================================================================
print("\n" + "=" * 75)
print("SECTION 8 -- equivalence: the run is otherwise byte-for-byte the same")
print("=" * 75)
# ===========================================================================

# "PROVENANCE ONLY, NO BEHAVIOUR CHANGE" IS A CLAIM ABOUT EVERY OTHER FIELD, and
# section 7's AST scan only shows that nothing READS the two names. This shows
# the stronger thing directly: the shipped node and a copy with the whole
# mechanism removed produce IDENTICAL results once the two fields are deleted --
# same verdicts, same scores, same order, same counters, same call ledger, same
# reconciliation.
#
# The copy has BOTH write sites removed, so it is the pre-change node rather than
# a half-reverted one. `git show` is not used and could not be: this is at HEAD,
# so a blob would compare the changed module with itself -- the defect
# tests/test_storage_query_layer.py had to learn.
#
# stage_timings is dropped from both sides: it holds wall-clock durations, which
# differ between any two runs of anything.

_NO_STAMP_SUBS = [(_STAMP, "        pass\n"),
                  (_UNEVAL, "")]


def _comparable(result):
    """A result with the two new fields and the wall-clock timings removed."""
    out = {k: v for k, v in result.items() if k != "stage_timings"}
    out["evaluations"] = [
        {k: v for k, v in e.items() if k not in _NAMES}
        for e in result.get("evaluations") or []
    ]
    return out


_EQUIV_SCRIPTS = [
    ("ordinary", _SHUFFLED, TRIALS_4[:3]),
    ("split", [({"evaluations": []}, "length"),
               {"evaluations": [entry("NCT00000001"), entry("NCT00000002")]},
               {"evaluations": [entry("NCT00000003"), entry("NCT00000004")]}],
     TRIALS_4),
    ("reconciliation", [{"evaluations": [entry("NCT00000001")]}], TRIALS_4[:2]),
    ("all truncated", [({"evaluations": []}, "length")], TRIALS_4),
    ("dropped entries",
     [{"evaluations": ["NCT00000001", entry("NCT00000001"),
                       entry("NCT09999999"), entry("NCT00000002")]}],
     TRIALS_4[:2]),
]

try:
    _pre_change = _plant(_NO_STAMP_SUBS)
    _pre_node = _pre_change.node_llm_classifier_evaluation
except _PlantFailed as _exc:
    _pre_node = None
    check(f"8   THE PRE-CHANGE COPY DID NOT BUILD: {_exc}", "plant-failed", "ok")

if _pre_node is not None:
    for _name, _script, _ids in _EQUIV_SCRIPTS:
        _new, _ = run_stage5(_script, nct_ids=_ids)
        _old, _ = run_stage5(_script, nct_ids=_ids, node=_pre_node)
        check(f"8   {_name}: identical once the two fields are removed",
              _comparable(_new), _comparable(_old))
        # Non-degeneracy: the arms are not both empty, and the fields really
        # were present on the new side before they were stripped.
        check(f"8   {_name}: non-degeneracy -- the run produced verdicts",
              len(_new.get("evaluations") or []) > 0, True)
        check(f"8   {_name}: non-degeneracy -- the pre-change arm has neither "
              "field",
              any(n in e for e in (_old.get("evaluations") or []) for n in _NAMES),
              False)
        check(f"8   {_name}: non-degeneracy -- the shipped arm has both",
              all(n in e for e in (_new.get("evaluations") or []) for n in _NAMES),
              True)


# ===========================================================================
print("\n" + "=" * 75)
print("SECTION 9 -- controls: every assertion above is shown to FAIL")
print("=" * 75)
# ===========================================================================

# The same shuffled response section 5 uses: emitted 1,2,3 and sorted 2,3,1, so
# a plant that renumbers after the sort produces a DIFFERENT mapping rather than
# the same one by luck.
_SIMPLE = _SHUFFLED


def _probe_stamps(script, nct_ids):
    """A probe that returns the stamp mapping produced by a given node."""
    def probe(node):
        result, _ = run_stage5(script, nct_ids=nct_ids, node=node)
        return stamps(result)
    return probe


# c1  THE STAMP DELETED. Section 1 and section 3 both collapse.
control("c1  no stamp at all -> the fields are absent",
        [(_STAMP, "        pass\n")],
        _probe_stamps(_SIMPLE, TRIALS_4[:3]),
        {"NCT00000001": ("<absent>", "<absent>"),
         "NCT00000002": ("<absent>", "<absent>"),
         "NCT00000003": ("<absent>", "<absent>")})

# c2  STAMPED AFTER THE DROPS, which is the mistake a reasonable person makes:
# it is one line later and it passes every check that has no dropped entry.
# Section 6's response is what separates them.
control("c2  stamped after the drops -> the survivors are renumbered",
        [(_STAMP, "        pass\n"),
         (_EXTEND,
          '        for _i, _e in enumerate(_objects):\n'
          '            _e["emission_index"] = _i\n'
          '            _e["call_index"] = calls_made\n'
          '        evaluations.extend(_objects)\n')],
        _probe_stamps(
            [{"evaluations": ["NCT00000001", entry("NCT00000001"),
                              entry("NCT09999999"),
                              entry("NCT00000002")]}],
            TRIALS_4[:2]),
        {"NCT00000001": (0, 1), "NCT00000002": (1, 1)})

# c3  STAMPED AFTER THE SORT, which is the mistake that destroys the whole
# point: it reports the pipeline's ranking back as the model's emission order.
control("c3  stamped after the sort -> emission_index is the sorted position",
        [(_STAMP, "        pass\n"),
         (_AFTER_SORT,
          '    for _i, _e in enumerate(evaluations):\n'
          '        _e["emission_index"] = _i\n'
          '        _e["call_index"] = 1\n'
          + _AFTER_SORT)],
        _probe_stamps(_SIMPLE, TRIALS_4[:3]),
        {"NCT00000002": (0, 1), "NCT00000003": (1, 1), "NCT00000001": (2, 1)})

# c4  call_index MADE 0-BASED, which is what the brief literally asked for. The
# join to llm_classifier_call_details then selects the wrong row -- silently,
# because every value is still a plausible call ordinal.
def _probe_join(node):
    """What the ledger says about the call each entry claims to have come from.

    A truncated first call over FOUR trials splits into 2 + 2, and each of the
    two retries answers exactly its own chunk. The true answer is therefore "the
    joined ledger row carried 2 trials" four times over. Under a 0-based
    call_index the first pair joins ledger row 1 -- the TRUNCATED call, which
    carried all four trials and produced no verdict at all -- and the second
    pair joins row 2. Nothing raises and every value stays a plausible call
    ordinal, which is precisely why an off-by-one here would never be noticed.

    Rendered as STRINGS so an ordinal the ledger does not have produces a
    comparable value instead of a TypeError out of sorting str beside int.
    """
    result, _ = run_stage5(
        [({"evaluations": []}, "length"),
         {"evaluations": [entry("NCT00000001"), entry("NCT00000002")]},
         {"evaluations": [entry("NCT00000003"), entry("NCT00000004")]}],
        nct_ids=TRIALS_4, node=node)
    by_call = {d["call_index"]: d for d in
               result.get("llm_classifier_call_details") or []}
    return sorted(str(by_call[c]["trials"]) if c in by_call
                  else f"<no ledger row for call {c}>"
                  for _, c in stamps(result).values())


control("c4  call_index made 0-based -> the ledger join lands on the wrong row",
        [('                _entry["call_index"] = calls_made\n',
          '                _entry["call_index"] = calls_made - 1\n')],
        _probe_join, ["2", "2", "4", "4"])

# c5  PIPELINE ENTRIES STAMPED 0 INSTEAD OF None -- indistinguishable from the
# first entry of the first call, which is a real place another trial occupies.
control("c5  _unevaluable_entry stamping 0 -> a constructed entry claims a "
        "position",
        [(_UNEVAL, '        "emission_index": 0,\n        "call_index": 0,\n')],
        _probe_stamps([{"evaluations": [entry("NCT00000001")]}],
                      TRIALS_4[:2]),
        {"NCT00000001": (0, 1), "NCT00000002": (0, 0)})

# c6  setdefault SEMANTICS instead of an overwrite: a model that emitted the key
# would dictate its own provenance.
control("c6  a model-supplied value left in place -> the stamp is not ours",
        [('            if isinstance(_entry, dict):\n',
          '            if isinstance(_entry, dict) and "emission_index" not in _entry:\n')],
        _probe_stamps(
            [{"evaluations": [entry("NCT00000001", emission_index=99,
                                    call_index=99),
                              entry("NCT00000002")]}],
            TRIALS_4[:2]),
        {"NCT00000001": (99, 99), "NCT00000002": (1, 1)})

# NON-DEGENERACY OF THE CONTROL MECHANISM ITSELF, and it deliberately does NOT
# go through control(): an UNPLANTED copy must AGREE with the shipped node, which
# is the opposite of what control() asserts. Without this, every "the plant
# changed the answer" above could be an artefact of exec'ing a copy at all
# rather than of the edit inside it.
try:
    _unplanted = _plant([(_STAMP, _STAMP)])
    _unplanted_stamps = _probe_stamps(_SIMPLE, TRIALS_4[:3])(
        _unplanted.node_llm_classifier_evaluation)
except Exception as _exc:               # noqa: BLE001 - a raise IS an outcome
    _unplanted_stamps = f"raised {type(_exc).__name__}"
check("c7  non-degeneracy: an UNPLANTED copy reproduces the shipped stamps",
      _unplanted_stamps,
      _probe_stamps(_SIMPLE, TRIALS_4[:3])(node_llm_classifier_evaluation))
check("c7' non-degeneracy: and that mapping is the real one",
      _unplanted_stamps,
      {"NCT00000001": (0, 1), "NCT00000002": (1, 1), "NCT00000003": (2, 1)})


# ===========================================================================
print("\n" + "=" * 75)
print("SECTION 10 -- every plant was in memory")
print("=" * 75)
# ===========================================================================

check("10a  evaluation.py is byte-identical to its pre-run state",
      _sha256_of(_EVAL_SRC), _SHA_BEFORE)
check("10b  non-degeneracy: the baseline hash is a real digest",
      _SHA_BEFORE == hashlib.sha256(b"").hexdigest(), False)
check("10c  non-degeneracy: eight in-memory copies were built "
      "(the pre-change arm of section 8, six controls and one unplanted copy)",
      _CONTROL_SEQ[0], 8)


# ===========================================================================

print("\n" + "=" * 75)
print(f"RESULTS: {_RESULTS['passed']} passed, {_RESULTS['failed']} failed")
print("=" * 75)
if _FAILURES:
    print("\nFAILURES:")
    for _f in _FAILURES:
        print(f"  - {_f}")

sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug 12 2026

@author: ramyalsaffar
"""
