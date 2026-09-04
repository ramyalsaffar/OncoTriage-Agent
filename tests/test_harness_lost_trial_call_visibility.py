# Harness Lost Trial Call Visibility Test
########################################

"""A patient whose per-trial Stage 5 wave LOST calls is not recorded as plain
``ok``, and the loss is a number in the record rather than a log line.

WHAT SHIPPED, AND WHY NOTHING FAILED
------------------------------------
Per-trial mode isolates a failed trial call to its own trial: the trial is
recorded ``per_trial_call_failed``, the patient completes, and the rest of the
wave is judged. That recovery is right and is unchanged by this file. What was
wrong is that the fact left no trace a consumer could see. ``oncotriage/agent/
evaluation.py`` counted the losses in two locals, read them in two log lines and
in the all-failed floor, and returned neither -- so ``oncotriage/evaluation/
run_harness.py`` recorded the patient ``ok``, which was TRUE and INCOMPLETE:
"a terminal result was produced and persisted" says nothing about the result
being short.

IT IS NOT HYPOTHETICAL. The 2026-09-03 sample run against
``us.anthropic.claude-sonnet-4-6``, at the then-shipped pacing, exceeded the
account's 10 requests-per-minute allowance and lost 2 of one patient's 15 trial
calls to ThrottlingException. Every artifact that patient produced said the run
was fine. The only surviving trace was ``PER_TRIAL_CALL_FAILURES``, a
module-level counter -- a question about the PROCESS, not about that patient,
and unreachable from a record on disk.

AND IT COULD NOT BE DERIVED AFTER THE FACT. A call that RAISED appends no
``llm_classifier_call_details`` row, so the per-call ledger can say how many
requests came back and cannot say how many were attempted. Counting verdicts
carrying ``per_trial_call_failed`` counts TRIALS, which equals the call count
only in the per-trial arm and only while no chunk carries two trials -- so the
node is the only place the fact exists.

WHAT THIS FILE HOLDS
--------------------
    1. THE VOCABULARY. ``TRIAL_CALL_COMPLETENESS`` is closed, its members are
       distinct, and it is a FIELD BESIDE the status rather than a fifth
       ``RUN_STATUSES`` member -- which is asserted, because the resume
       partition is what a new status would have had to join.
    2. ``trial_call_census`` AS A PURE FUNCTION, table-driven over all four
       input shapes, with the tri-state that separates "nothing was lost" (0)
       from "the wave's accounting does not describe this run" (None).
    3. THE NODE SURFACES IT. The REAL Stage 5 node, per-trial, against a stub
       that raises for named trials: the three keys carry the numbers, the
       grouped arm gets None for all three, and the all-failed floor carries
       them too.
    4. THE KEYS SURVIVE THE GRAPH. A real ``StateGraph`` over the real
       ``TrialMatchState``, with the negative control that a schema missing the
       three annotations loses all three -- the silent-drop class this project
       has already shipped once.
    5. THE RECORD AND THE MANIFEST. ``build_record`` puts the census in the run
       block, ``summarise`` totals it, and an entry written before the field
       existed buckets as ``not_recorded`` rather than as a member of the
       vocabulary.
    6. THE POST-CHECK REPORTS IT, as a FINDING (the record is well-formed and
       its verdict count is a floor) and as a line printed EVEN AT ZERO.
    7. SEVEN PLANTS, each with the shipped module's answer beside it.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO MODEL LOAD, NO CORPUS, NO
DATABASE, NO GIT HISTORY, NO LIVE SERVER, NO SUBPROCESS. Every model response is
a literal served by a stub installed through ``oncotriage.agent.deps``.

NOT IN THE COLLISION MATRIX, derived rather than assumed: it writes only inside
a ``tempfile.mkdtemp`` it removes and asserts gone, and the two repository files
it reads -- ``oncotriage/agent/evaluation.py`` and
``oncotriage/evaluation/run_harness.py`` -- are written by neither of the
suite's two writers. Both are sha256-compared at the end.

IT EXECS -- in-memory copies of those two modules, one plant each. Argued at
``_EXEC_ALLOWLIST`` in ``tests/test_package_invariants.py``: every branch under
test is NEW, so ``git show`` has no revision carrying a version with one of them
broken.

Run from terminal:
    python tests/test_harness_lost_trial_call_visibility.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# The package bootstrap every file in tests/ carries. The candidate directory
# is the PARENT of this file's, because the package sits beside tests/ rather
# than inside it. `pip install -e .` makes the whole block a no-op.
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

# NO MODEL IS LOADED. Set above every project import, because
# oncotriage/agent/deps.py reads it once at ITS import and an assignment
# underneath would reach nothing.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

import contextlib                                                  # noqa: E402
import hashlib                                                     # noqa: E402
import json                                                        # noqa: E402
import re                                                          # noqa: E402
import shutil                                                      # noqa: E402
import tempfile                                                    # noqa: E402
import types                                                       # noqa: E402
from typing import Optional, TypedDict                             # noqa: E402

from langgraph.graph import END, StateGraph                         # noqa: E402

from oncotriage import config                                      # noqa: E402
from oncotriage.agent import deps                                  # noqa: E402
from oncotriage.agent.evaluation import (                          # noqa: E402
    node_llm_classifier_evaluation)
from oncotriage.agent.state import TrialMatchState                 # noqa: E402
from oncotriage.agent.terminal import node_finalize                # noqa: E402
from oncotriage.evaluation import run_harness as _rh               # noqa: E402

# THE PROVIDER IS PINNED TO THE OPENAI ARM FOR THIS WHOLE FILE. Its subject is
# the census the dispatch loop keeps, which is shared by both per-trial
# providers -- and the shipped Converse arm would have every drive here build a
# real boto3 client from the real credential chain out of a file that claims to
# make no billed call. tests/_provider_pin.py is the one owner of that pin.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _provider_pin import pin_openai_arm, release_openai_arm        # noqa: E402

pin_openai_arm(os.path.abspath(__file__))


#------------------------------------------------------------------------------


# ===========================================================================
# HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def section(title):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def check(label, actual, expected):
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(label)
        print(f"  FAIL  {label}")
        print(f"          expected: {expected!r}")
        print(f"          actual:   {actual!r}")


class _Absent:
    """A named absence, so a missing value FAILS a check instead of raising.

    FALSY ON PURPOSE. This project has shipped the abort shape -- an
    ``IndexError`` or a ``KeyError`` raised while a ``check()`` argument is
    being evaluated, on exactly the defect the check exists to catch, taking the
    whole run's summary with it -- seventeen times by its own count. Every read
    in this file that a plant can make impossible goes through ``at`` or
    ``drive``.
    """

    def __init__(self, why):
        self.why = why

    def __bool__(self):
        return False

    def __eq__(self, other):
        return isinstance(other, _Absent) and other.why == self.why

    def __repr__(self):
        return f"<absent: {self.why}>"


def at(mapping, key):
    """``mapping[key]``, or a named absence."""
    if not isinstance(mapping, dict) or key not in mapping:
        return _Absent(f"{key} not present")
    return mapping[key]


def drive(fn, *args, **kwargs):
    """Call it; a raise becomes a value ``check`` can fail on."""
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                     # noqa: BLE001 -- recorded
        return _Absent(f"raised {type(exc).__name__}: {exc}")


def module_from(source, name):
    """exec a patched copy into its own namespace.

    A REAL ModuleType rather than a throwaway class, because a function's
    globals ARE the dict it was exec'd into.
    """
    module = types.ModuleType(name)
    module.__file__ = f"<{name}>"
    exec(compile(source, f"<{name}>", "exec"), module.__dict__)
    return module


def planted(path, old, new, name):
    """A planted copy, or a named absence if the plant matched nothing.

    A PLANT THAT MATCHED NOTHING IS AN AUTHORING ERROR AND MUST BE LOUD: a
    revert reported as MISSED against a check that works is a different finding
    from a weak check, and this project has paid for that confusion before.
    """
    source = _read(path)
    if source.count(old) != 1:
        return _Absent(f"plant matched {source.count(old)} times in {path}")
    return drive(module_from, source.replace(old, new, 1), name)


def _read(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


_EVAL_PATH = os.path.abspath(
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "oncotriage", "agent", "evaluation.py"))
_HARNESS_PATH = os.path.abspath(_rh.__file__)
_HASHES_BEFORE = {p: _sha256(p) for p in (_EVAL_PATH, _HARNESS_PATH)}

_TMP = tempfile.mkdtemp(prefix="oncotriage-lost-calls-")


#------------------------------------------------------------------------------


# ===========================================================================
# THE STUB — a per-trial wave that loses named trials
# ===========================================================================


class _Usage:
    def __init__(self):
        self.prompt_tokens = 1000
        self.completion_tokens = 100
        self.completion_tokens_details = None


class _Message:
    def __init__(self, content):
        self.content = content
        self.refusal = None


class _Choice:
    def __init__(self, content):
        self.message = _Message(content)
        self.finish_reason = "stop"


class _Response:
    def __init__(self, content):
        self.choices = [_Choice(content)]
        self.usage = _Usage()
        self.model = config.MATCHING_MODEL


def _body(nct_ids):
    return json.dumps({"evaluations": [
        {"assessment": "No known disqualifiers.", "eligible": "eligible",
         "inclusion_criteria": [{"criterion": "Age 18+",
                                 "patient_value": "61", "status": "met"}],
         "exclusion_criteria": [], "match_score": 0.0, "nct_id": i}
        for i in nct_ids]})


class _Stub:
    """Answers the trials it was asked about; raises for the named ones.

    THE WARMUP IS RECOGNISED BY ITS USER MESSAGE rather than by being the first
    request, because "the first request" is a property of the SCHEDULE and the
    schedule is not what this file is about: a defect that stopped sending the
    warmup would make request 0 a trial call and a test that DEFINED request 0
    as the warmup could not see it.
    """

    def __init__(self, fail_for=()):
        self.fail_for = set(fail_for)
        self.calls = 0
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls += 1
        user = kwargs["messages"][1]["content"]
        if user == config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE:
            return _Response(_body([]))
        # THE IDS ARE READ OUT OF THE FENCE THE RENDERER WROTE rather than by
        # substring-matching the fixture list, so the stub answers the trials
        # the node actually SENT -- which is what makes "the lost trials are
        # the ones that raised" a measurement.
        asked = re.findall(r"<<<TRIAL_DATA nct_id=(\S+) ", user)
        for nct in asked:
            if nct in self.fail_for:
                raise RuntimeError(f"stub transport failure for {nct}")
        return _Response(_body(asked))


_PATIENT = {
    "patient_id": "PT-LOST-0001",
    "demographics": {"age": 61, "sex": "female", "race": "white",
                     "ethnicity": "not hispanic or latino"},
    "conditions": [{"code": "254837009",
                    "display": "Malignant neoplasm of breast (disorder)",
                    "verification_status": "confirmed"}],
    "medications": [], "allergies": [], "observations": [], "procedures": [],
}

# THE TRIAL SHAPE `_build_trials_text` READS, copied from
# tests/test_agent_stage5_per_trial_calls.py rather than invented: the renderer
# indexes `phase` and the two `eligibility` halves directly, so a fixture short
# of them raises inside the node and every check below would fail for a reason
# that has nothing to do with the census.
_TRIALS = [
    {"trial": {"nct_id": "NCT%08d" % i,
               "title": f"Trial {i}",
               "phase": "PHASE2",
               "eligibility": {
                   "inclusion_criteria": "Inclusion Criteria:\n- age 18+",
                   "exclusion_criteria": "Exclusion Criteria:\n- none"}}}
    for i in range(1, 6)
]
_NCT = [t["trial"]["nct_id"] for t in _TRIALS]


def run_node(trials, *, per_trial=True, fail_for=()):
    """Drive the REAL Stage 5 node once. Returns ``(result, stub)``."""
    stub = _Stub(fail_for=fail_for)
    saved = config.MATCHING_PER_TRIAL_CALLS_ENABLED
    deps.set_override(deps.OPENAI_CLIENT, stub)
    try:
        config.MATCHING_PER_TRIAL_CALLS_ENABLED = per_trial
        state = {"patient_data": _PATIENT, "filtered_trials": trials,
                 "llm_classifier_retries": 0, "mesh_filter_applied": True,
                 "mesh_filter_skip_reason": "applied", "stage_timings": {}}
        return drive(node_llm_classifier_evaluation, state), stub
    finally:
        config.MATCHING_PER_TRIAL_CALLS_ENABLED = saved
        deps.clear_override(deps.OPENAI_CLIENT)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 1 — THE VOCABULARY, AND WHY IT IS NOT A STATUS
# ===========================================================================

section("1. The completeness vocabulary is closed, and is NOT a status")

check("the vocabulary has exactly three members",
      len(_rh.TRIAL_CALL_COMPLETENESS), 3)
check("...and they are distinct (non-degeneracy: two members spelled the same "
      "would make every branch below unfalsifiable)",
      len(set(_rh.TRIAL_CALL_COMPLETENESS)), 3)
check("...and the three names are the ones the record writes",
      sorted(_rh.TRIAL_CALL_COMPLETENESS),
      sorted([_rh.TRIAL_CALLS_COMPLETE, _rh.TRIAL_CALLS_INCOMPLETE,
              _rh.TRIAL_CALLS_NOT_APPLICABLE]))

# THE LOAD-BEARING HALF. A fifth RUN_STATUSES member would have had to join the
# RESUME_SKIP/RERUN partition -- which is guarded at import -- and every reader
# of `by_status`, for a distinction on which none of them branches differently:
# a patient whose wave lost two of fifteen calls still produced and persisted a
# terminal result, so its resume answer is byte-identical to a clean one's.
# `runs.stop_reason` was argued exactly this way one module over.
check("no completeness member leaked into RUN_STATUSES, so `--resume`'s "
      "partition and every reader of by_status keep meaning what they meant",
      sorted(set(_rh.TRIAL_CALL_COMPLETENESS) & set(_rh.RUN_STATUSES)), [])
check("...and RUN_STATUSES is still the four it was, which is the "
      "non-degeneracy half: an emptied tuple would satisfy the check above "
      "for free", len(_rh.RUN_STATUSES), 4)
check("...and the resume partition still covers it exactly, which is the "
      "guard a new status would have had to be added to",
      sorted(set(_rh.RESUME_SKIP_STATUSES) | set(_rh.RESUME_RERUN_STATUSES)),
      sorted(_rh.RUN_STATUSES))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 2 — trial_call_census AS A PURE FUNCTION
# ===========================================================================

section("2. trial_call_census: a pure function of one result dict")

_CASES = (
    # (label, result, attempted, failed, answered, completeness, problems)
    ("a whole per-trial wave",
     {"llm_classifier_per_trial_calls_attempted": 5,
      "llm_classifier_per_trial_calls_failed": 0,
      "llm_classifier_per_trial_calls_answered": 5},
     5, 0, 5, _rh.TRIAL_CALLS_COMPLETE, 0),
    ("a wave that lost two",
     {"llm_classifier_per_trial_calls_attempted": 5,
      "llm_classifier_per_trial_calls_failed": 2,
      "llm_classifier_per_trial_calls_answered": 3},
     5, 2, 3, _rh.TRIAL_CALLS_INCOMPLETE, 0),
    ("the grouped arm, where all three are None",
     {"llm_classifier_per_trial_calls_attempted": None,
      "llm_classifier_per_trial_calls_failed": None,
      "llm_classifier_per_trial_calls_answered": None},
     None, None, None, _rh.TRIAL_CALLS_NOT_APPLICABLE, 0),
    ("a result carrying none of the keys at all -- a run that never reached "
     "Stage 5, or a record from before the field existed",
     {}, None, None, None, _rh.TRIAL_CALLS_NOT_APPLICABLE, 0),
    ("a census that disagrees with itself is REPORTED rather than repaired",
     {"llm_classifier_per_trial_calls_attempted": 9,
      "llm_classifier_per_trial_calls_failed": 2,
      "llm_classifier_per_trial_calls_answered": 3},
     9, 2, 3, _rh.TRIAL_CALLS_INCOMPLETE, 1),
)

for _label, _result, _a, _f, _ans, _comp, _probs in _CASES:
    _census = drive(_rh.trial_call_census, _result)
    check(f"{_label}: attempted", at(_census, "attempted"), _a)
    check(f"{_label}: failed", at(_census, "failed"), _f)
    check(f"{_label}: answered", at(_census, "answered"), _ans)
    check(f"{_label}: completeness", at(_census, "completeness"), _comp)
    check(f"{_label}: problems", len(at(_census, "problems") or []), _probs)

# THE TRI-STATE IS THE POINT, AND IT IS ASSERTED AS ONE. `failed == 0` and
# `failed is None` must not collapse: the first is a MEASUREMENT that nothing
# was lost and the second is "the question does not arise here". A branch
# written on truthiness gives both the same answer, which is plant P3.
check("0 and None give DIFFERENT verdicts, which a truthiness test would "
      "collapse",
      at(drive(_rh.trial_call_census,
               {"llm_classifier_per_trial_calls_failed": 0}), "completeness")
      == at(drive(_rh.trial_call_census,
                  {"llm_classifier_per_trial_calls_failed": None}),
            "completeness"),
      False)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 3 — THE NODE SURFACES IT
# ===========================================================================

section("3. The REAL Stage 5 node returns the census")

_clean, _clean_stub = run_node(_TRIALS)
check("a whole per-trial wave reports every trial attempted",
      at(_clean, "llm_classifier_per_trial_calls_attempted"), len(_TRIALS))
check("...none failed", at(_clean, "llm_classifier_per_trial_calls_failed"), 0)
check("...and all answered",
      at(_clean, "llm_classifier_per_trial_calls_answered"), len(_TRIALS))
check("...NON-DEGENERACY: the stub really issued more calls than trials, "
      "because the warmup is one of them -- so `attempted` is the TRIAL count "
      "and not the request count",
      _clean_stub.calls > len(_TRIALS), True)

_lossy, _ = run_node(_TRIALS, fail_for=(_NCT[1], _NCT[3]))
check("a wave that lost two reports two failed",
      at(_lossy, "llm_classifier_per_trial_calls_failed"), 2)
check("...three answered",
      at(_lossy, "llm_classifier_per_trial_calls_answered"), 3)
check("...five attempted, which is the denominator a reader of `failed` needs "
      "and which no other field carries",
      at(_lossy, "llm_classifier_per_trial_calls_attempted"), len(_TRIALS))
check("...and the patient still COMPLETED, which is what made the loss "
      "invisible before this field existed",
      at(_lossy, "error"), "")
check("...with the lost trials recorded not evaluable rather than dropped",
      len([e for e in (at(_lossy, "evaluations") or [])
           if e.get("eligible") == "not_evaluable"]), 2)

# THE LEDGER CANNOT ANSWER THIS, and that is why the node has to. A failed call
# appends no row, so `llm_classifier_call_details` under-counts by exactly the
# losses -- which is the reason this file exists rather than a query.
check("the per-call ledger is SHORT by exactly the lost calls, which is the "
      "measured reason the census is not derivable from it",
      len(at(_lossy, "llm_classifier_call_details") or []),
      len(_TRIALS) - 2 + 1)          # answered trials + the warmup

_grouped, _ = run_node(_TRIALS, per_trial=False)
check("the GROUPED arm reports None rather than 0 for attempted",
      at(_grouped, "llm_classifier_per_trial_calls_attempted"), None)
check("...None for failed, so a healthy grouped patient cannot read as a "
      "per-trial patient whose whole wave was lost",
      at(_grouped, "llm_classifier_per_trial_calls_failed"), None)
check("...and None for answered",
      at(_grouped, "llm_classifier_per_trial_calls_answered"), None)
check("...NON-DEGENERACY: the grouped arm really did issue calls, so the three "
      "Nones above are a tri-state and not an empty run",
      (at(_grouped, "llm_classifier_calls") or 0) > 0, True)

# THE ALL-FAILED FLOOR carries it too. That return is reached only after
# `pending` is exhausted or cleared, so both counters have stopped moving --
# unlike the mid-loop returns, which would publish a prefix as a total.
_all_failed, _ = run_node(_TRIALS,
                          fail_for=_NCT)
check("the all-failed floor reports every trial call attempted",
      at(_all_failed, "llm_classifier_per_trial_calls_attempted"), len(_TRIALS))
check("...every one failed",
      at(_all_failed, "llm_classifier_per_trial_calls_failed"), len(_TRIALS))
check("...none answered",
      at(_all_failed, "llm_classifier_per_trial_calls_answered"), 0)
check("...and the patient FAILED, so the retry budget and the checkpoint see "
      "it -- which is the behaviour this pass did not change",
      bool(at(_all_failed, "error")), True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 4 — THE KEYS SURVIVE THE GRAPH
# ===========================================================================

section("4. The three keys are declared channels, so the graph keeps them")


def _unannotated(fn):
    """The same node with no first-parameter annotation.

    LANGGRAPH READS THE NODE CALLABLE'S ANNOTATION AND ADDS THAT SCHEMA'S
    CHANNELS TO THE GRAPH, so registering the real annotated function on a
    REDUCED schema silently reinstates every channel the reduction removed --
    and the control below would then report that an undeclared key survives,
    which is the opposite of the truth.
    """
    def _node(state):
        return fn(state)
    _node.__name__ = getattr(fn, "__name__", "node")
    return _node


def through_graph(schema):
    graph = StateGraph(schema)
    graph.add_node("stage5", _unannotated(node_llm_classifier_evaluation))
    graph.add_node("finalize", _unannotated(node_finalize))
    graph.set_entry_point("stage5")
    graph.add_edge("stage5", "finalize")
    graph.add_edge("finalize", END)
    stub = _Stub(fail_for=(_NCT[1],))
    saved = config.MATCHING_PER_TRIAL_CALLS_ENABLED
    deps.set_override(deps.OPENAI_CLIENT, stub)
    try:
        config.MATCHING_PER_TRIAL_CALLS_ENABLED = True
        state = {"patient_data": _PATIENT, "filtered_trials": _TRIALS,
                 "llm_classifier_retries": 0, "mesh_filter_applied": True,
                 "mesh_filter_skip_reason": "applied", "stage_timings": {}}
        return drive(lambda: graph.compile().invoke(state)["result"])
    finally:
        config.MATCHING_PER_TRIAL_CALLS_ENABLED = saved
        deps.clear_override(deps.OPENAI_CLIENT)


_KEYS = ("llm_classifier_per_trial_calls_attempted",
         "llm_classifier_per_trial_calls_failed",
         "llm_classifier_per_trial_calls_answered")

_declared = through_graph(TrialMatchState)
check("through the REAL schema the census reaches the result: failed",
      at(_declared, "llm_classifier_per_trial_calls_failed"), 1)
check("...attempted",
      at(_declared, "llm_classifier_per_trial_calls_attempted"), len(_TRIALS))
check("...answered",
      at(_declared, "llm_classifier_per_trial_calls_answered"), len(_TRIALS) - 1)

# THE NEGATIVE CONTROL. A schema without the three annotations DROPS all three
# in silence -- no error, no warning -- which is the class this project already
# shipped once, for four keys, and is why declaring them was not optional.
# THE FUNCTIONAL FORM IS LOAD-BEARING, and it is copied from
# tests/test_agent_state_channel_coverage.py with the lesson recorded there:
# subclassing with an empty body and then assigning __annotations__ leaves
# __required_keys__ and __optional_keys__ EMPTY, LangGraph builds its channels
# from those, and a schema declaring no keys filters NOTHING -- so every key
# survives and the control reports that an undeclared key is carried, which is
# the opposite of the truth from a control that is inert rather than wrong.
_ReducedState = TypedDict("_ReducedState", {
    k: v for k, v in TrialMatchState.__annotations__.items()
    if k not in _KEYS})
check("the control schema really is three annotations smaller",
      len(TrialMatchState.__annotations__)
      - len(_ReducedState.__annotations__), 3)
check("...and its key sets are NON-EMPTY, which is what stops the control "
      "being a schema that filters nothing and therefore proves nothing",
      len(_ReducedState.__required_keys__)
      + len(_ReducedState.__optional_keys__) > 0, True)
_undeclared = through_graph(_ReducedState)
check("CONTROL: with the three annotations removed the graph loses all three, "
      "silently -- so the declaration is what carries them",
      [at(_undeclared, k) for k in _KEYS], [None, None, None])
# NON-DEGENERACY OF THE CONTROL. `node_finalize` publishes the verdicts under
# `matches` / `near_misses` / `not_evaluable` rather than as `evaluations`, so
# this reads the count the terminal node writes. Without it, a control run that
# raised before Stage 5 would report three Nones and look like a caught defect.
check("...and the control run is otherwise the same run -- it judged the same "
      "trials -- which is what makes the comparison about the schema and "
      "nothing else",
      at(_undeclared, "candidates_evaluated"),
      at(_declared, "candidates_evaluated"))
check("...and that count is non-zero on both arms, so the comparison above is "
      "not two absent runs agreeing",
      (at(_declared, "candidates_evaluated") or 0) > 0, True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 5 — THE RECORD, THE MANIFEST AND THE TOTALS
# ===========================================================================

section("5. build_record, the manifest entry and summarise")


def _fake_record(result):
    """A record built by the REAL build_record over a minimal state."""
    row = {"bundle": "b.json", "patient_id": "PT-LOST-0001",
           "path": "/nowhere/b.json", "primary_diagnosis": "Breast"}
    entry = {"row": row, "reason": "r", "note": "n"}
    return drive(_rh.build_record, row, _PATIENT,
                 {"filtered_trials": []}, result, entry, 1.0, [])


_rec_lossy = _fake_record(dict(_lossy))
check("build_record puts the census in the run block",
      at(at(_rec_lossy, "run") or {}, "trial_calls") is not None
      if isinstance(_rec_lossy, dict) else False, True)
check("...with the loss it was handed",
      (at(_rec_lossy, "run") or {}).get("trial_calls", {}).get("failed"), 2)
check("...and the verdict",
      (at(_rec_lossy, "run") or {}).get("trial_calls", {}).get("completeness"),
      _rh.TRIAL_CALLS_INCOMPLETE)
check("...and the census's `problems` list is POPPED rather than persisted "
      "twice: the record's own `problems` is the one place a reader looks",
      "problems" in ((at(_rec_lossy, "run") or {}).get("trial_calls") or {}),
      False)

# A DISAGREEING CENSUS SURFACES IN `problems`, which is the record's own
# convention for "something was wrong with this record" -- the same list the
# stamp failures and the summary error already use.
_rec_bad = _fake_record({"llm_classifier_per_trial_calls_attempted": 9,
                         "llm_classifier_per_trial_calls_failed": 2,
                         "llm_classifier_per_trial_calls_answered": 3})
check("a self-disagreeing census is folded into the record's problems",
      any("census disagrees" in p
          for p in ((at(_rec_bad, "run") or {}).get("problems") or [])), True)

# --- summarise -------------------------------------------------------------
_MANIFEST = {"runs": {
    "a": {"status": _rh.STATUS_OK, "trial_calls":
          {"attempted": 5, "failed": 0, "answered": 5,
           "completeness": _rh.TRIAL_CALLS_COMPLETE}},
    "b": {"status": _rh.STATUS_OK, "trial_calls":
          {"attempted": 5, "failed": 2, "answered": 3,
           "completeness": _rh.TRIAL_CALLS_INCOMPLETE}},
    "c": {"status": _rh.STATUS_OK, "trial_calls":
          {"attempted": 5, "failed": 3, "answered": 2,
           "completeness": _rh.TRIAL_CALLS_INCOMPLETE}},
    # AN ENTRY FROM BEFORE THE FIELD EXISTED. Every manifest already on disk
    # looks like this, and it must keep totalling.
    "d": {"status": _rh.STATUS_OK},
    # AND ONE CARRYING None, which is what main() writes when a record's run
    # block has no census -- a replaced run_one_patient, or an embedder's.
    "e": {"status": _rh.STATUS_OK, "trial_calls": None},
}}
_tot = drive(_rh.summarise, _MANIFEST)
check("summarise totals the lost calls across patients",
      at(_tot, "trial_calls_lost"), 5)
check("...and counts the PATIENTS separately, because five losses on one "
      "patient and five spread over five are different findings",
      at(_tot, "patients_with_lost_trial_calls"), 2)
check("...and buckets an entry written before the field as `not_recorded` "
      "rather than as a member of the vocabulary, which would claim a "
      "measurement nobody took",
      (at(_tot, "by_trial_calls") or {}).get("not_recorded"), 2)
check("...with the two real members counted",
      [(at(_tot, "by_trial_calls") or {}).get(_rh.TRIAL_CALLS_COMPLETE),
       (at(_tot, "by_trial_calls") or {}).get(_rh.TRIAL_CALLS_INCOMPLETE)],
      [1, 2])
check("...and by_status is UNTOUCHED: every one of them is still `ok`, which "
      "is the whole reason the completeness is a separate field",
      at(_tot, "by_status"), {_rh.STATUS_OK: 5})


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 6 — THE POST-CHECK REPORTS IT
# ===========================================================================

section("6. The post-check reports a lossy record, and prints even at zero")


def _write_record(directory, name, failed):
    record = {
        "schema_version": _rh.RECORD_SCHEMA_VERSION,
        "patient_id": name,
        "run": {k: None for k in _rh.REQUIRED_RUN_KEYS},
        "patient_summary": {"text": "PATIENT RECORD", "error": None},
        "contexts": [],
        "verdicts": [{"nct_id": "NCT00000001", "eligible": "eligible",
                      "inclusion_criteria": [{"criterion": "c"}],
                      "exclusion_criteria": []}],
        "criterion_decision_count": 1,
        "result": {},
    }
    if failed is not None:
        record["run"]["trial_calls"] = {
            "attempted": 5, "failed": failed, "answered": 5 - failed,
            "completeness": (_rh.TRIAL_CALLS_INCOMPLETE if failed
                             else _rh.TRIAL_CALLS_COMPLETE)}
    with open(os.path.join(directory, f"{name}.json"), "w",
              encoding="utf-8") as handle:
        json.dump(record, handle)


_LOSSY_DIR = os.path.join(_TMP, "lossy")
os.makedirs(_LOSSY_DIR)
_write_record(_LOSSY_DIR, "p1", 2)
_write_record(_LOSSY_DIR, "p2", 0)
_write_record(_LOSSY_DIR, "p3", None)      # a record from before the field

_report = drive(_rh.post_check, _LOSSY_DIR)
check("the post-check re-derives the loss from the RECORDS on disk",
      (at(_report, "totals") or {}).get("trial_calls_lost"), 2)
check("...over the right number of records",
      (at(_report, "totals") or {}).get("records_with_lost_trial_calls"), 1)
check("...and reports it as a FINDING, because the record is well-formed and "
      "its verdict count is a floor",
      len([f for f in (at(_report, "findings") or []) if "were lost" in f]), 1)
check("...naming the patient, the loss and the denominator",
      all(t in ([f for f in (at(_report, "findings") or [])
                 if "were lost" in f] or [""])[0]
          for t in ("p1.json", "2 of 5", _rh.TRIAL_CALLS_INCOMPLETE)), True)
check("...and a record from before the field is NOT a finding, which is why "
      "`trial_calls` is deliberately absent from REQUIRED_RUN_KEYS",
      "trial_calls" in _rh.REQUIRED_RUN_KEYS, False)
check("...and that record raised nothing", len(at(_report, "files") or []), 3)

# --- the line is printed EVEN AT ZERO -------------------------------------
_CLEAN_DIR = os.path.join(_TMP, "clean")
os.makedirs(_CLEAN_DIR)
_write_record(_CLEAN_DIR, "q1", 0)


@contextlib.contextmanager
def _captured():
    lines = []
    original = _rh.console.out
    _rh.console.out = lambda *a, **k: lines.append(" ".join(str(x) for x in a))
    try:
        yield lines
    finally:
        _rh.console.out = original


with _captured() as _lines:
    drive(_rh.print_post_check, drive(_rh.post_check, _CLEAN_DIR))
check("the post-check prints the per-trial-loss line EVEN AT ZERO -- silence "
      "and 'nothing was lost' must not look the same",
      any("per-trial calls lost" in line for line in _lines), True)
check("...and it reads 0 on a clean run",
      any("per-trial calls lost         : 0" in line for line in _lines), True)
check("...NON-DEGENERACY: the capture really collected the other lines too",
      any("criterion decisions" in line for line in _lines), True)

with _captured() as _lines_lossy:
    drive(_rh.print_post_check, drive(_rh.post_check, _LOSSY_DIR))
check("...and the same line carries the real total on a lossy run",
      any("per-trial calls lost         : 2" in line
          for line in _lines_lossy), True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 7 — PLANTS, EACH WITH THE SHIPPED ANSWER BESIDE IT
# ===========================================================================

section("7. Planted defects, each paired with the shipped module's answer")

_EVAL_SRC = _read(_EVAL_PATH)
_HARNESS_SRC = _read(_HARNESS_PATH)

# --- P1: the node stops returning the census on the success path -----------
_p1 = planted(_EVAL_PATH,
              "        **_per_trial_call_census(),\n"
              "        \"llm_classifier_calls\": calls_made,",
              "        \"llm_classifier_calls\": calls_made,",
              "_p1_eval")
check("P1 the plant took (a plant that matched nothing is an authoring error)",
      isinstance(_p1, types.ModuleType), True)


def _run_planted(module, fail_for=(_NCT[1], _NCT[3])):
    stub = _Stub(fail_for=fail_for)
    saved = config.MATCHING_PER_TRIAL_CALLS_ENABLED
    deps.set_override(deps.OPENAI_CLIENT, stub)
    try:
        config.MATCHING_PER_TRIAL_CALLS_ENABLED = True
        state = {"patient_data": _PATIENT, "filtered_trials": _TRIALS,
                 "llm_classifier_retries": 0, "mesh_filter_applied": True,
                 "mesh_filter_skip_reason": "applied", "stage_timings": {}}
        return drive(module.node_llm_classifier_evaluation, state)
    finally:
        config.MATCHING_PER_TRIAL_CALLS_ENABLED = saved
        deps.clear_override(deps.OPENAI_CLIENT)


_p1_result = _run_planted(_p1) if isinstance(_p1, types.ModuleType) else _p1
check("P1 CAUGHT: with the census dropped from the success return the loss is "
      "invisible again",
      at(_p1_result, "llm_classifier_per_trial_calls_failed"),
      _Absent("llm_classifier_per_trial_calls_failed not present"))
check("P1 CONTROL: the SHIPPED module reports the loss on the same input",
      at(_lossy, "llm_classifier_per_trial_calls_failed"), 2)

# --- P2: the grouped arm reports 0 instead of None -------------------------
_p2 = planted(_EVAL_PATH,
              '        if not _per_trial_calls:\n'
              '            return {\n'
              '                "llm_classifier_per_trial_calls_attempted": None,\n'
              '                "llm_classifier_per_trial_calls_failed": None,\n'
              '                "llm_classifier_per_trial_calls_answered": None,\n'
              '            }',
              '        if not _per_trial_calls:\n'
              '            return {\n'
              '                "llm_classifier_per_trial_calls_attempted": 0,\n'
              '                "llm_classifier_per_trial_calls_failed": 0,\n'
              '                "llm_classifier_per_trial_calls_answered": 0,\n'
              '            }',
              "_p2_eval")
check("P2 the plant took", isinstance(_p2, types.ModuleType), True)
if isinstance(_p2, types.ModuleType):
    stub = _Stub()
    saved = config.MATCHING_PER_TRIAL_CALLS_ENABLED
    deps.set_override(deps.OPENAI_CLIENT, stub)
    try:
        config.MATCHING_PER_TRIAL_CALLS_ENABLED = False
        _p2_result = drive(_p2.node_llm_classifier_evaluation,
                           {"patient_data": _PATIENT,
                            "filtered_trials": _TRIALS,
                            "llm_classifier_retries": 0,
                            "mesh_filter_applied": True,
                            "mesh_filter_skip_reason": "applied",
                            "stage_timings": {}})
    finally:
        config.MATCHING_PER_TRIAL_CALLS_ENABLED = saved
        deps.clear_override(deps.OPENAI_CLIENT)
else:
    _p2_result = _p2
check("P2 CAUGHT: the grouped arm reporting 0 makes a healthy grouped patient "
      "read, through trial_call_census, as one whose wave was WHOLE -- a "
      "measurement where there was none",
      at(drive(_rh.trial_call_census, _p2_result if isinstance(_p2_result, dict)
               else {}), "completeness"),
      _rh.TRIAL_CALLS_COMPLETE)
check("P2 CONTROL: the SHIPPED grouped arm answers not_applicable",
      at(drive(_rh.trial_call_census, dict(_grouped)), "completeness"),
      _rh.TRIAL_CALLS_NOT_APPLICABLE)

# --- P3: the completeness branches on truthiness ---------------------------
_p3 = planted(_HARNESS_PATH,
              "    if failed is None:\n"
              "        completeness = TRIAL_CALLS_NOT_APPLICABLE\n"
              "    elif failed > 0:",
              "    if not failed and failed != 0:\n"
              "        completeness = TRIAL_CALLS_NOT_APPLICABLE\n"
              "    elif failed:",
              "_p3_harness")
check("P3 the plant took", isinstance(_p3, types.ModuleType), True)
# P3 IS A RECORDED NO-OP AND IS KEPT AS ONE. `not failed and failed != 0` is
# the truthiness rewrite an author reaches for first, and it happens to keep
# the tri-state: for 0 the second conjunct is False, so it falls through to the
# `elif` and answers `complete` exactly as the shipped `is None` does. A plant
# that is not a behaviour change is not a test of the harness -- this project's
# own rule -- so it is stated rather than claimed, and P3b below is the
# truthiness rewrite that DOES collapse the two.
check("P3 NO-OP (recorded, not claimed as a catch): this truthiness rewrite "
      "preserves the tri-state, so it proves nothing and P3b is the plant that "
      "matters",
      at(drive(_p3.trial_call_census, {"llm_classifier_per_trial_calls_failed": 0})
         if isinstance(_p3, types.ModuleType) else {}, "completeness"),
      _rh.TRIAL_CALLS_COMPLETE)

_p3b = planted(_HARNESS_PATH,
               "    if failed is None:\n"
               "        completeness = TRIAL_CALLS_NOT_APPLICABLE\n"
               "    elif failed > 0:",
               "    if not failed:\n"
               "        completeness = TRIAL_CALLS_NOT_APPLICABLE\n"
               "    elif failed > 0:",
               "_p3b_harness")
check("P3b the plant took", isinstance(_p3b, types.ModuleType), True)
check("P3b CAUGHT: a truthiness test collapses 'nothing was lost' into 'the "
      "question does not arise', so a clean per-trial wave stops being "
      "distinguishable from a grouped one",
      at(drive(_p3b.trial_call_census,
               {"llm_classifier_per_trial_calls_failed": 0})
         if isinstance(_p3b, types.ModuleType) else {}, "completeness"),
      _rh.TRIAL_CALLS_NOT_APPLICABLE)
check("P3b CONTROL: the SHIPPED function says `complete`",
      at(drive(_rh.trial_call_census,
               {"llm_classifier_per_trial_calls_failed": 0}), "completeness"),
      _rh.TRIAL_CALLS_COMPLETE)

# --- P4: the post-check stops reporting the loss ---------------------------
_p4 = planted(_HARNESS_PATH,
              '            findings.append(\n'
              '                f"{name}: {_lost} of {_tc.get(\'attempted\')} '
              'per-trial Stage 5 "',
              '            _unused = (\n'
              '                f"{name}: {_lost} of {_tc.get(\'attempted\')} '
              'per-trial Stage 5 "',
              "_p4_harness")
check("P4 the plant took", isinstance(_p4, types.ModuleType), True)
check("P4 CAUGHT: with the finding removed a lossy run reports no findings at "
      "all, which is what a clean run reports",
      len([f for f in (at(drive(_p4.post_check, _LOSSY_DIR)
                          if isinstance(_p4, types.ModuleType) else {},
                          "findings") or []) if "were lost" in f]), 0)
check("P4 CONTROL: the SHIPPED post-check reports it",
      len([f for f in (at(_report, "findings") or []) if "were lost" in f]), 1)

# --- P5: summarise stops totalling -----------------------------------------
_p5 = planted(_HARNESS_PATH,
              "        if _tc.get(\"failed\"):\n"
              "            trial_calls_lost += _tc[\"failed\"]\n"
              "            patients_with_lost_trial_calls += 1",
              "        if False:\n"
              "            trial_calls_lost += _tc[\"failed\"]\n"
              "            patients_with_lost_trial_calls += 1",
              "_p5_harness")
check("P5 the plant took", isinstance(_p5, types.ModuleType), True)
check("P5 CAUGHT: with the accumulation removed the manifest totals report a "
      "clean campaign",
      at(drive(_p5.summarise, _MANIFEST) if isinstance(_p5, types.ModuleType)
         else {}, "trial_calls_lost"), 0)
check("P5 CONTROL: the SHIPPED summarise reports 5",
      at(_tot, "trial_calls_lost"), 5)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 8 — HYGIENE
# ===========================================================================

section("8. Hygiene: nothing in the repository moved, and the temp tree is gone")

for _path, _before in _HASHES_BEFORE.items():
    check(f"{os.path.basename(_path)} is byte-unchanged",
          _sha256(_path), _before)
check("...NON-DEGENERACY: the two hashed files really are two different files",
      len(set(_HASHES_BEFORE.values())), 2)

shutil.rmtree(_TMP, ignore_errors=True)
check("the temp tree this file wrote is gone", os.path.exists(_TMP), False)

# THE PIN IS RELEASED ABOVE THE SUMMARY, not below it. A release below the
# results line still decides the exit code while being absent from the number
# the summary printed -- a run that reports "0 failed" and exits 1, which this
# project has shipped three times.
_who, _previous, _restored = release_openai_arm()
check("the process-global provider pin this file installed is released, so a "
      "runner importing every test module into one process does not inherit it",
      _restored, True)
check("...and there really WAS a pin to release, recorded before the restore "
      "-- without this the check above passes in a file whose pin was deleted",
      _who, os.path.abspath(__file__))
check("...and the value restored is the shipped default this file displaced",
      config.MATCHING_PROVIDER, _previous)


print()
print("=" * 78)
print("RESULTS:")
print(f"  passed: {_RESULTS['passed']}")
print(f"  failed: {_RESULTS['failed']}")
if _FAILURES:
    print("\nFAILURES:")
    for _f in _FAILURES:
        print(f"  - {_f}")
print("=" * 78)

sys.exit(1 if _RESULTS["failed"] else 0)


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 2026-09-03

@author: ramyalsaffar
"""
