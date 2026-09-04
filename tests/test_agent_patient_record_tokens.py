# Stage 5 Patient Record Size Test
##################################

"""``llm_classifier_patient_record_tokens`` measures the PATIENT RECORD's own
share of Stage 5's fixed prefix, and this file is what says the number means
that rather than something adjacent to it.

WHAT THE FIELD IS
-----------------
Stage 5 renders ONE system prompt per inference, above the split loop. Since
PROMPT_VERSION 1.6.0 that message carries the patient record, so the fixed
prefix every request of a patient sends is template + record. The template is a
constant; the record is not. Nothing recorded how large the varying half was, so
no stored row could say how much of a patient's fixed prefix was that patient.

The field is the pipeline's own ``estimate_prompt_tokens`` applied to
``patient_record`` -- the NEUTRALIZED string that ``render_system_prompt`` is
handed, not the raw summary and not the rendered prompt.

WHAT CAN GO WRONG, AND IS THEREFORE ASKED HERE
----------------------------------------------
    1. IT MEASURES THE WRONG STRING. The raw summary, the whole system prompt
       and the fenced block are all within one line of the right answer at the
       render site, and all three are wrong. Section 1 pins the exact subject;
       section 2 makes the raw/neutralized distinction OBSERVABLE by driving a
       patient whose summary carries a fence-marker run, where the two differ.
    2. IT USES A SECOND FORMULA. A private ``len(x) // 4`` would agree with the
       estimator until the divisor moved or the rounding changed -- and the
       divisor is PER ARM now (``config.matching_chars_per_token()``), so such a
       formula would also be silently right on this file's pinned OpenAI arm and
       silently wrong on the shipped one. Section 1 compares against the shipped
       function and section 6c re-derives the relationship from the owner.
    3. IT REPORTS A FABRICATED ZERO. A run that never rendered a prompt has no
       record size, and 0 is a genuine reading of an empty record. Section 3
       drives both terminal nodes that never reach Stage 5 and requires None.
    4. IT DIES BETWEEN THE NODE AND THE ROW. Section 4 carries one run all the
       way to a scratch SQLite column, and a pre-change result shape to NULL in
       the same table.
    5. IT BECOMES PER-CALL. A split run makes N requests with one record;
       section 5 requires one value, unchanged by the split.

NEGATIVE CONTROLS ARE INPUTS, NOT PLANTS. Every control here is a different
ARGUMENT to the shipped code -- a fence-carrying summary, a state that never
rendered, a result dict without the key, a decoy database -- which is the
natural control for a measurement of its own input and is why this file execs
nothing and needs no _EXEC_ALLOWLIST entry. The reverts that break the
production code itself were run out of band against a copytree'd copy; see the
pass report.

NO NETWORK, NO KEYS, NO SPEND, NO CORPUS, NO GIT HISTORY. The OpenAI client is
a stand-in installed through ``oncotriage/agent/deps.py``; Qdrant and the
cross-encoder are never reached. It is NOT in the collision matrix: every write
goes to a scratch file in a temp directory that is asserted to differ from the
production database and is removed at the end, and no file in the repository is
touched.

Run from terminal:
    python tests/test_agent_patient_record_tokens.py

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
# set before the agent is imported anyway: a stand-in forgotten in a future edit
# becomes a named RuntimeError instead of a 110 MB download.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

import json
import shutil
import sqlite3
import tempfile

from langgraph.graph import END, StateGraph

from oncotriage.agent import deps
from oncotriage.agent.evaluation import (
    _neutralize_fence_markers,
    estimate_prompt_tokens,
    node_llm_classifier_evaluation,
)
from oncotriage.agent.patient import _create_patient_summary
from oncotriage.agent.prompts import render_system_prompt
from oncotriage.agent.state import TrialMatchState
from oncotriage.agent.terminal import (
    node_error_handler,
    node_finalize,
    node_no_candidates,
)
from oncotriage.config import CHARS_PER_TOKEN
from oncotriage.paths import inferences_path as PRODUCTION_INFERENCES_PATH
from oncotriage.storage.database_logger import (
    INFERENCE_COLUMN_ADDITIONS,
    initialize_database,
    log_inference,
    resolve_inference_db_path,
)
from oncotriage import config                            # noqa: E402

# ===========================================================================
# THIS FILE'S SUBJECT IS THE DORMANT OpenAI STAGE 5 REQUEST -- SO IT PINS IT
# ===========================================================================
#
# `config.MATCHING_PROVIDER` ships "bedrock_anthropic". Every Stage 5 stand-in
# below is installed at `deps.OPENAI_CLIENT` and wraps `chat.completions
# .create`, so at the shipped default the dispatch would reach
# `deps.BEDROCK_ANTHROPIC_CLIENT` and `converse` instead: the stand-in would
# never be called, every assertion here would compare against an empty
# recorder, and `config.get_bedrock_anthropic_client()` would BUILD -- boto3
# probing the instance metadata service from a suite that reports it makes no
# network call, and issuing live billed Converse requests on any host whose
# credential chain finds something.
#
# The pin, its cost and why it has one owner rather than a block per file are
# argued in tests/_provider_pin.py. THE SHIPPED ARM IS NOT COVERED BY THIS
# FILE; on Converse these subjects are covered by
# tests/test_agent_bedrock_anthropic_adapter.py and
# tests/test_agent_bedrock_anthropic_per_trial.py alone.
import _provider_pin                                             # noqa: E402

_PROVIDER_BEFORE_PIN = _provider_pin.pin_openai_arm(os.path.basename(__file__))


# ===========================================================================
# THIS FILE'S SUBJECT IS THE RETAINED GROUPED ARM, AND IT PINS IT
# ===========================================================================
#
# WHAT THIS FILE MEASURES IS THE ONE SYSTEM PROMPT RENDERED ABOVE THE SPLIT LOOP, and
# the field measures the fixed prefix's own share and the counting is written against a known number of renders per patient. Per-trial mode adds
# a warmup request carrying the identical system message, so the render arithmetic here is a grouped-arm statement.
#
# PINNED THROUGH THE OWNER, NEVER BY WRITING THE CONSTANT.
# `config.pin_matching_call_mode()` is what `oncotriage/config.py` built for
# exactly this: a declaration a PROGRAM makes about itself, kept apart from
# `MATCHING_PER_TRIAL_CALLS_ENABLED`, which says what the PROJECT is configured
# to do. Assigning the constant here would be a second WRITER of a declared
# configuration value -- the shape this project keeps removing -- and would
# leave `config.MATCHING_PER_TRIAL_CALLS_ENABLED` read anywhere later in this
# process saying the project is configured grouped when it is not. Every
# consumer the node reaches -- Stage 5's partition,
# `inferences.matching_call_mode`, the resume fingerprint, the tracking index
# -- follows the owner, so one line redirects all of them consistently.
#
# BEFORE ANY DRIVE, AND ASSERTED TO HAVE TAKEN. A pin that did not take would
# leave every check below silently measuring the other arm, which is not one
# failure but every failure with a misleading message -- so it is a HARD GUARD
# on this suite's own precedent for a wrong root, not a check().
#
# RELEASED BEFORE THE SUMMARY, not at interpreter exit. The pin is
# process-global; these files are run one per process, but `pytest tests/`
# imports them all into ONE process and a leaked grouped pin would make
# `tests/test_agent_stage5_per_trial_calls.py`'s explicitly-per-trial sections
# run grouped without a word.
_CALL_MODE_PIN_PREVIOUS = config.pin_matching_call_mode(
    config.MATCHING_CALL_MODE_GROUPED)
if config.matching_call_mode() != config.MATCHING_CALL_MODE_GROUPED:
    raise SystemExit(
        "[CallMode] the grouped pin did not take: config.matching_call_mode() "
        f"is {config.matching_call_mode()!r}. Everything below would measure "
        "the wrong Stage 5 arm.")


# The one name under test, spelled once.
KEY = "llm_classifier_patient_record_tokens"


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
    traceback where it owed a summary. Five files in this suite have had to fix
    that after the fact; this one starts with it.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                     # noqa: BLE001 -- reported
        return {"__raised__": f"{type(exc).__name__}: {exc}"}


def get(mapping, key, default="<not a mapping>"):
    """mapping[key] or a named absence. Never a TypeError inside a check()."""
    try:
        return mapping.get(key, default)
    except AttributeError:
        return default


def as_int(value):
    """The value if it is a real int, else None.

    EVERY INEQUALITY IN THIS FILE GOES THROUGH THIS, and the reason is the
    revert harness again: with the key dropped from Stage 5's success return,
    ``get()`` yields its absence marker, and ``0 < "<absent>" < fixed`` raises
    TypeError inside a check()'s argument -- so the file aborted on exactly the
    defect it exists to catch. A bool is excluded deliberately: True is an int
    in Python and would satisfy an ordering test while being no measurement.
    """
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def select(db_path, sql, params=()):
    """Query a scratch database, converting a database error into a value.

    A BARE ``execute`` HERE IS THE ABORT-INSTEAD-OF-REPORT SHAPE, and this
    file's own revert harness demonstrated it: with the column dropped from
    INFERENCE_COLUMN_ADDITIONS, ``SELECT llm_classifier_patient_record_tokens``
    raises ``no such column`` -- which is EXACTLY the defect section 4b exists
    to catch -- and the file died with a traceback where it owed a summary and
    the twenty-odd results below. Five files in this suite have had to fix that
    after the fact; here it was found before shipping, by the harness rather
    than by reading.
    """
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        return conn.execute(sql, params).fetchall()
    except sqlite3.Error as exc:                     # noqa: BLE001 -- reported
        return [(f"<sqlite error: {type(exc).__name__}: {exc}>",)]
    finally:
        if conn is not None:
            conn.close()


#------------------------------------------------------------------------------


# ===========================================================================
# THE STAND-IN STAGE 5 CLIENT
# ===========================================================================
#
# Answers about the trials the request it was handed actually asked about, so a
# packed run gets a valid response per chunk rather than one response claiming
# every trial in every chunk. Records every request, which is what section 5
# reads to prove the split really happened.

class _Usage:
    prompt_tokens = 100
    completion_tokens = 50
    total_tokens = 150
    completion_tokens_details = None


class _StubOpenAI:
    def __init__(self, nct_ids, body=None):
        self.nct_ids = list(nct_ids)
        self.body = body
        self.requests = []
        _outer = self

        class _completions:
            @staticmethod
            def create(**kwargs):
                _outer.requests.append(kwargs)
                return _outer._completion(kwargs)

        class _chat:
            completions = _completions

        self.chat = _chat

    def system_messages(self):
        """The system message of every request made, in order."""
        out = []
        for kwargs in self.requests:
            for message in kwargs.get("messages") or []:
                if message.get("role") == "system":
                    out.append(message.get("content"))
        return out

    def _completion(self, kwargs, ):
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
            usage = _Usage()
            # None skips the answering-model check, a different mechanism with
            # its own test that would otherwise raise here.
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


def _patient(patient_id, condition_display="Malignant neoplasm of breast"):
    return {
        "patient_id": patient_id,
        "demographics": {"age": 63, "sex": "female", "birth_date": "1962-04-03",
                         "race": "White",
                         "ethnicity": "Not Hispanic or Latino"},
        "conditions": [{"code": "254837009",
                        "system": "http://snomed.info/sct",
                        "display": condition_display,
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


# A fence marker run inside a condition display. It reaches the summary as text
# and _neutralize_fence_markers spaces the run out, which LENGTHENS the string
# -- so the raw and neutralized estimates differ and "which one was measured"
# becomes an observable fact rather than a reading of the source.
FENCE_RUN = "<" * 20
PATIENT = _patient("record-tokens-1")
FENCE_PATIENT = _patient(
    "record-tokens-fence",
    f"Malignant neoplasm of breast {FENCE_RUN} carcinoma")


def base_state(patient, trials):
    return {"patient_data": dict(patient), "filtered_trials": list(trials),
            "stage_timings": {}}


def run_through_graph(state):
    """Stage 5 -> node_finalize over the REAL TrialMatchState. Returns result."""
    graph = StateGraph(TrialMatchState)
    graph.add_node("stage5", node_llm_classifier_evaluation)
    graph.add_node("finalize", node_finalize)
    graph.set_entry_point("stage5")
    graph.add_edge("stage5", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile().invoke(state)["result"]


def expected_tokens(patient):
    """What the field must be, computed the way the node computes it.

    Through the SHIPPED summary builder, the SHIPPED neutralizer and the
    SHIPPED estimator, in that order. Retyping any of the three here would make
    this file agree with a model of the node rather than with the node.
    """
    neutralized, runs = _neutralize_fence_markers(_create_patient_summary(patient))
    return estimate_prompt_tokens(neutralized), runs


_NCTS = [f"NCT0000000{i}" for i in range(1, 6)]


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 1 -- the measurement is of the exact neutralized record
# ===========================================================================

print("=" * 70)
print("SECTION 1 -- a normal run measures estimate_prompt_tokens(record)")
print("=" * 70)

_stub = _StubOpenAI(_NCTS)
deps.set_override(deps.OPENAI_CLIENT, _stub)

_returned = drive(node_llm_classifier_evaluation,
                  base_state(PATIENT, [_trial(n) for n in _NCTS]))
_result = drive(run_through_graph,
                base_state(PATIENT, [_trial(n) for n in _NCTS]))

_expected, _runs = expected_tokens(PATIENT)
_summary = _create_patient_summary(PATIENT)

print("\n  1a. the node's value IS the estimator over the neutralized record")
check("the node ran without error", get(_returned, "error"), "")
check("the node reports the estimator's value", get(_returned, KEY), _expected)
check("and so does the result after node_finalize", get(_result, KEY), _expected)

print("\n  1b. non-degeneracy -- the number is a real measurement")
check("it is an int (and not a bool)",
      as_int(get(_returned, KEY)) is not None, True)
_measured = as_int(get(_returned, KEY))
check("it is well above zero (this patient has a record)",
      _measured is not None and _measured > 50, True)
check("the summary this run rendered is non-empty (non-degenerate)",
      len(_summary) > 200, True)
check("this patient's summary carries NO fence run, so raw == neutralized here",
      _runs, 0)

print("\n  1c. and it is not one of the three strings it could have been")
#
# EACH OF THESE IS ONE LINE AWAY AT THE RENDER SITE. The system prompt is what
# the hash beside it measures; the whole [SYSTEM]+[USER] prompt is what the
# stored llm_classifier_prompt column holds; the fenced block is the record plus
# its two delimiter lines, which are template. All three are larger than the
# record and none of them may be reported here.
_system_prompt = render_system_prompt(
    mesh_filter_applied=False, mesh_filter_skip_reason="unrecorded",
    patient_record=_summary)
check("it is NOT the whole system prompt's estimate",
      get(_returned, KEY) == estimate_prompt_tokens(_system_prompt), False)
check("it is NOT the stored system+user prompt's estimate",
      get(_returned, KEY)
      == estimate_prompt_tokens(get(_result, "llm_classifier_prompt") or ""),
      False)
check("the system prompt really is larger than the record (non-degenerate)",
      _measured is not None
      and estimate_prompt_tokens(_system_prompt) > _measured, True)
check("the record really is inside the prompt that was sent (non-degenerate)",
      _summary in (_stub.system_messages() or [""])[0], True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 2 -- a fence-carrying summary is measured AFTER neutralization
# ===========================================================================

print()
print("=" * 70)
print("SECTION 2 -- the subject is the neutralized text, not the raw summary")
print("=" * 70)

_fence_stub = _StubOpenAI(_NCTS)
deps.set_override(deps.OPENAI_CLIENT, _fence_stub)
_fence_returned = drive(node_llm_classifier_evaluation,
                        base_state(FENCE_PATIENT, [_trial(n) for n in _NCTS]))

_fence_summary = _create_patient_summary(FENCE_PATIENT)
_fence_neutralized, _fence_runs = _neutralize_fence_markers(_fence_summary)
_raw_estimate = estimate_prompt_tokens(_fence_summary)
_neutral_estimate = estimate_prompt_tokens(_fence_neutralized)

print(f"\n  (runs neutralized: {_fence_runs}; "
      f"raw {_raw_estimate} tokens vs neutralized {_neutral_estimate})")

print("\n  2a. the control discriminates -- the two estimates are NOT equal")
check("the summary really carried a fence run (non-degenerate)",
      _fence_runs >= 1, True)
check("neutralization really changed the text (non-degenerate)",
      _fence_neutralized != _fence_summary, True)
check("and really changed the token estimate (this is the whole control)",
      _raw_estimate != _neutral_estimate, True)
check("neutralization lengthens, so the neutralized estimate is the larger",
      _neutral_estimate > _raw_estimate, True)

print("\n  2b. the field reports the neutralized one")
check("the node ran without error", get(_fence_returned, "error"), "")
check("the field equals the NEUTRALIZED estimate",
      get(_fence_returned, KEY), _neutral_estimate)
check("the field does NOT equal the raw summary's estimate",
      get(_fence_returned, KEY) == _raw_estimate, False)
check("what was SENT carries the neutralized text, not the raw run",
      FENCE_RUN in (_fence_stub.system_messages() or [""])[0], False)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 3 -- None where Stage 5 never rendered a prompt
# ===========================================================================

print()
print("=" * 70)
print("SECTION 3 -- no prompt rendered means None, never 0")
print("=" * 70)

# The two terminal nodes reachable without Stage 5's render. Both spread
# _pipeline_provenance(), which is the single read under test.
_no_cand = drive(node_no_candidates,
                 {"patient_data": dict(PATIENT), "filtered_trials": [],
                  "stage_timings": {}})
_no_cand_result = get(_no_cand, "result", {})
_errored = drive(node_error_handler,
                 {"patient_data": dict(PATIENT), "filtered_trials": [],
                  "error": "upstream failure before Stage 5",
                  "stage_timings": {}})
_error_result = get(_errored, "result", {})

print("\n  3a. node_no_candidates")
check("the key is PRESENT (a consumer never tests for it)",
      KEY in _no_cand_result, True)
check("and its value is None", get(_no_cand_result, KEY), None)
check("it is None, not 0 (the two are different facts)",
      get(_no_cand_result, KEY) == 0, False)
check("the sha256 beside it is None too: the pair is never split",
      get(_no_cand_result, "llm_classifier_prompt_sha256"), None)
check("the node really produced a result (non-degenerate)",
      get(_no_cand_result, "terminal_node") is not None, True)

print("\n  3b. node_error_handler, on a state that never reached Stage 5")
check("the key is present", KEY in _error_result, True)
check("and its value is None", get(_error_result, KEY), None)
check("the error really was recorded (non-degenerate)",
      bool(get(_error_result, "error")), True)

print("\n  3c. CONTROL -- the same node WITH a Stage 5 render on the state")
#
# Without this, 3a and 3b are satisfied by a provenance that dropped the field
# altogether or by a state read that can never see anything. Here the identical
# node is handed a state carrying what Stage 5 writes, and must report it.
_errored_after_stage5 = drive(node_error_handler,
                              {"patient_data": dict(PATIENT),
                               "filtered_trials": [],
                               "error": "Stage 5 API error",
                               KEY: 4321,
                               "llm_classifier_prompt_sha256": "deadbeef",
                               "stage_timings": {}})
_after_result = get(_errored_after_stage5, "result", {})
check("a rendered run's value survives to the error result",
      get(_after_result, KEY), 4321)
check("so the None above is the absence of a render, not a dropped field",
      get(_after_result, KEY) is None, False)

print("\n  3d. a REAL Stage 5 failure return still carries it")
#
# THE RENDER PRECEDES THE FIRST CALL, so a run that failed rendered a prompt and
# has a record size -- and a failed run is exactly the row worth knowing the
# prompt shape of. Driven for real, with a stub whose answer cannot parse,
# rather than by handing a state to a terminal node.
_broken_stub = _StubOpenAI(_NCTS, body="not json at all {{")
deps.set_override(deps.OPENAI_CLIENT, _broken_stub)
_failed = drive(node_llm_classifier_evaluation,
                base_state(PATIENT, [_trial(n) for n in _NCTS]))
check("the parse really did fail (non-degenerate)",
      "parse error" in (get(_failed, "error") or ""), True)
check("the failure return carries the measurement",
      get(_failed, KEY), _expected)
check("beside the hash, which is carried on the same argument",
      get(_failed, "llm_classifier_prompt_sha256") is not None, True)

print("\n  3e. and so does the API-error return, which is a DIFFERENT return")
#
# STAGE 5 HAS FOUR FAILURE RETURNS AND THIS FILE HAS TO REACH MORE THAN ONE OF
# THEM. The four are separate dict literals; a key added to three of them looks
# identical from any single failing run. This one is reached when the client
# itself raises -- the earliest exit below the render, and therefore the
# strongest statement that the measurement precedes the first call. The gap was
# found by the revert harness (a cut that removed the key from this return
# alone was MISSED), not by reading.
class _RaisingOpenAI:
    class chat:
        class completions:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("connection reset by peer")


deps.set_override(deps.OPENAI_CLIENT, _RaisingOpenAI())
_api_failed = drive(node_llm_classifier_evaluation,
                    base_state(PATIENT, [_trial(n) for n in _NCTS]))
check("the API call really did fail (non-degenerate)",
      "API error" in (get(_api_failed, "error") or ""), True)
check("no model answered, so this is not the parse path (non-degenerate)",
      get(_api_failed, "llm_classifier_raw_response"), "")
check("the API-error return carries the measurement",
      get(_api_failed, KEY), _expected)

print("\n  3f. and the refusal return, which is the third of the four")
#
# THE FOURTH -- the unwrap failure -- shares its dict literal shape with 3d's
# parse-error return and is reached by the same stub answering a non-list; it is
# not driven separately because 3d already fails when that literal loses the
# key. The three driven here are the three that differ in what they carry.
class _RefusingMsg:
    content = None
    refusal = "I can't help with that."


class _RefusingOpenAI:
    class chat:
        class completions:
            @staticmethod
            def create(**kwargs):
                class _Choice:
                    message = _RefusingMsg()
                    finish_reason = "stop"

                class _Completion:
                    choices = [_Choice()]
                    usage = _Usage()
                    model = None

                return _Completion()


deps.set_override(deps.OPENAI_CLIENT, _RefusingOpenAI())
_refused = drive(node_llm_classifier_evaluation,
                 base_state(PATIENT, [_trial(n) for n in _NCTS]))
check("the model really refused (non-degenerate)",
      bool(get(_refused, "llm_classifier_refusal")), True)
check("the refusal return carries the measurement",
      get(_refused, KEY), _expected)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 4 -- it reaches the record and the database column
# ===========================================================================

print()
print("=" * 70)
print("SECTION 4 -- run harness record, and the inferences column")
print("=" * 70)

from oncotriage.evaluation.run_harness import (                      # noqa: E402
    RESULT_OMITTED_KEYS,
    build_record,
)

deps.set_override(deps.OPENAI_CLIENT, _StubOpenAI(_NCTS))
_e2e_result = drive(run_through_graph,
                    base_state(PATIENT, [_trial(n) for n in _NCTS]))
_e2e_expected, _ = expected_tokens(PATIENT)

print("\n  4a. the evaluation record persists it verbatim")
_record = drive(build_record,
                {"bundle": "b.json", "patient_id": PATIENT["patient_id"]},
                dict(PATIENT),
                {"filtered_trials": [_trial(n) for n in _NCTS],
                 "llm_classifier_refusal": None},
                dict(_e2e_result),
                {"reason": "spread", "note": "n/a"}, 1.0, [])
_persisted = get(_record, "result", {})
check("record['result'] carries the field", get(_persisted, KEY), _e2e_expected)
check("it is not on the omitted list", KEY in RESULT_OMITTED_KEYS, False)
# THE HARNESS STORES THE PRE-NEUTRALIZATION SUMMARY, so re-estimating from the
# persisted text reproduces this field EXACTLY when the run neutralized nothing
# -- which is every real patient to date. Stated as an assertion rather than as
# a comment because it is the property that makes the stored record auditable.
_persisted_summary = get(get(_record, "patient_summary", {}), "text", "")
check("re-estimating from the persisted summary matches (no runs on this patient)",
      estimate_prompt_tokens(_persisted_summary or ""), _e2e_expected)

print("\n  4b. and it lands in the inferences column")
_TMP_DIR = tempfile.mkdtemp(prefix="oncotriage-record-tokens-")
_SCRATCH_DB = os.path.join(_TMP_DIR, "inferences_test.db")

# The isolation assertions that make every check below meaningful: the package
# default is the production database, this scratch path is not it, and an
# explicit argument outranks the default.
check("the package default IS the production database",
      os.path.abspath(resolve_inference_db_path(None)),
      os.path.abspath(PRODUCTION_INFERENCES_PATH))
check("and the scratch path is NOT the production database",
      os.path.abspath(_SCRATCH_DB) == os.path.abspath(PRODUCTION_INFERENCES_PATH),
      False)
check("an explicit path outranks the default",
      resolve_inference_db_path(_SCRATCH_DB), _SCRATCH_DB)

check("the column is declared in INFERENCE_COLUMN_ADDITIONS",
      INFERENCE_COLUMN_ADDITIONS.get(KEY), "INTEGER")

drive(initialize_database, _SCRATCH_DB)
_cols = {row[1] for row in select(_SCRATCH_DB, "PRAGMA table_info(inferences)")}
check("a fresh database has the column", KEY in _cols, True)

_row_result = dict(_e2e_result)
_row_result["timestamp"] = "2026-08-13T00:00:00"
_write = drive(log_inference, _row_result, dict(PATIENT), db_path=_SCRATCH_DB)
check("the write reported success", getattr(_write, "ok", False), True)
check("and it went to the scratch database", str(_write), _SCRATCH_DB)

_stored = select(_SCRATCH_DB, f"SELECT {KEY} FROM inferences WHERE patient_id = ?",
                 (PATIENT["patient_id"],))
check("exactly one row was written (non-degenerate)", len(_stored), 1)
check("the stored value is the node's measurement",
      _stored[0][0] if _stored else "<no row>", _e2e_expected)

print("\n  4c. a pre-change result shape stores NULL, not 0")
#
# THE CONTROL FOR "NO DEFAULT". A result dict that never carried the key is
# exactly what a caller outside the pipeline, or a build before this column
# existed, produces -- and 0 there would be indistinguishable from an empty
# record. Driven through the real writer rather than asserted about the source.
_legacy = dict(_e2e_result)
_legacy.pop(KEY, None)
_legacy["patient_id"] = "record-tokens-legacy"
_legacy["timestamp"] = "2026-08-13T00:00:01"
_legacy_write = drive(log_inference, _legacy, dict(PATIENT), db_path=_SCRATCH_DB)
check("the legacy-shaped write also succeeded",
      getattr(_legacy_write, "ok", False), True)
_legacy_stored = select(_SCRATCH_DB,
                        f"SELECT {KEY} FROM inferences WHERE patient_id = ?",
                        ("record-tokens-legacy",))
check("its row exists (non-degenerate)", len(_legacy_stored), 1)
check("and its value is NULL",
      _legacy_stored[0][0] if _legacy_stored else "<no row>", None)
check("NULL and the measured value are distinguishable in one table",
      (_legacy_stored[0][0] if _legacy_stored else 0)
      == (_stored[0][0] if _stored else 0), False)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 5 -- one value per inference, however many calls it took
# ===========================================================================

print()
print("=" * 70)
print("SECTION 5 -- a split run reports one record size, not one per call")
print("=" * 70)

from oncotriage.config import MATCHING_INPUT_TOKEN_BUDGET             # noqa: E402

# Trials fat enough that the packer must cut them into more than one chunk. The
# filler is sized off the configured budget rather than guessed, so this section
# keeps splitting if the constant moves.
_FAT = "x" * (MATCHING_INPUT_TOKEN_BUDGET * CHARS_PER_TOKEN // 2)
_FAT_NCTS = [f"NCT0000001{i}" for i in range(1, 5)]
_fat_stub = _StubOpenAI(_FAT_NCTS)
deps.set_override(deps.OPENAI_CLIENT, _fat_stub)

_fat_result = drive(run_through_graph,
                    base_state(PATIENT, [_trial(n, _FAT) for n in _FAT_NCTS]))
_system_messages = _fat_stub.system_messages()

print(f"\n  (packed into {get(_fat_result, 'llm_classifier_packed_chunks')} "
      f"chunk(s); {len(_system_messages)} call(s) made)")

check("the batch really did split into more than one call (non-degenerate)",
      (get(_fat_result, "llm_classifier_calls") or 0) > 1, True)
check("every call sent the IDENTICAL system message",
      len(set(_system_messages)), 1)
check("the field is a single int, not a list",
      as_int(get(_fat_result, KEY)) is not None, True)
check("and it is the same value an unsplit run of this patient reports",
      get(_fat_result, KEY), _e2e_expected)
check("the split did not multiply it by the call count",
      get(_fat_result, KEY)
      == _e2e_expected * (get(_fat_result, "llm_classifier_calls") or 1), False)
# The prefix identity says the same thing about the hash, so the two provenance
# fields agree about what "one per inference" means.
check("the prefix sha256 is likewise one value",
      get(get(_fat_result, "llm_classifier_packing", {}), "prefix_sha256"),
      get(_fat_result, "llm_classifier_prompt_sha256"))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 6 -- the estimator relationship, and what is NOT stored
# ===========================================================================

print()
print("=" * 70)
print("SECTION 6 -- commensurable with the packer, and derivable elsewhere")
print("=" * 70)

print("\n  6a. it is the pipeline's estimator, re-derived from the constant")
#
# NOT a second formula: the value must be the ceiling division the shipped
# estimator performs, over the neutralized record's characters. Re-derived here
# from the divisor so that a change to either it or the rounding has to be made
# in both places deliberately.
#
# THE DIVISOR IS READ FROM `config.matching_chars_per_token()` RATHER THAN FROM
# `CHARS_PER_TOKEN`, and under this file's OpenAI pin the two are the same 4.
# Reading the OWNER is what keeps this section true of whatever arm it is run
# on: the divisor became PER ARM when the shipped judge's tokenizer was measured
# at ~3.5 against the OpenAI one's 4.2-4.4, and a section that re-derived the
# expectation from the OpenAI-arm constant while the estimator read the owner
# would be two formulas agreeing only because of the pin above it.
_DIVISOR = config.matching_chars_per_token()
check("the divisor this section re-derives with is the LIVE arm's, and under "
      "this file's OpenAI pin that is CHARS_PER_TOKEN",
      (_DIVISOR, CHARS_PER_TOKEN), (4, 4))
check("the value is ceil(len(record) / the live arm's divisor)",
      get(_returned, KEY),
      -(-len(_neutralize_fence_markers(_summary)[0]) // _DIVISOR))

# THE DISCRIMINATION USED TO RIDE ON THIS FIXTURE'S LENGTH AND NO LONGER DOES.
# It read `len(_summary) % CHARS_PER_TOKEN != 0` -- true of the record this
# patient happened to render, which is a one-in-CHARS_PER_TOKEN coincidence and
# not a property of anything. PROMPT_VERSION 1.8.0 changed what the renderer
# emits (an elapsed interval beside every date) and the length landed on a
# multiple of 4, so the probe failed while the assertion above it was entirely
# correct -- a test reporting a defect in a renderer it does not test.
#
# What replaces it cannot go stale, because it does not depend on any rendered
# text: the SHIPPED estimator is driven over a string at every remainder, and
# ceiling is required at each. A floor implementation agrees at remainder 0 and
# disagrees at the other three, so this fails for a truncating estimator on any
# day and for any record.
_CEIL_PROBE = [(n, estimate_prompt_tokens("x" * n))
               for n in range(_DIVISOR, 2 * _DIVISOR + 1)]
check("the shipped estimator rounds UP at every remainder, so 6a is "
      "discriminating whatever this patient's record happens to measure",
      _CEIL_PROBE,
      [(n, -(-n // _DIVISOR)) for n in range(_DIVISOR, 2 * _DIVISOR + 1)])
check("...and a truncating estimator would disagree on at least one of them, "
      "which is what makes the check above worth running",
      [n for n, got in _CEIL_PROBE if got != n // _DIVISOR] != [], True)
check("...and every one of those readings is an int, which the integer "
      "ceiling idiom stops being the moment a divisor is a float -- the reason "
      "the shipped estimator is math.ceil now",
      sorted({type(t).__name__ for _n, t in _CEIL_PROBE}), ["int"])

print("\n  6b. the record is a proper part of the fixed prefix")
#
# The packer's fixed_input_tokens is the system message plus the empty user
# wrapper, measured with the same estimator, so the record must be strictly
# smaller. This is what makes "template share = fixed - record - wrapper" a
# sound derivation and therefore what makes storing the template share
# unnecessary.
_fixed = get(get(_result, "llm_classifier_packing", {}), "fixed_tokens")
check("the packing record reports a fixed prefix (non-degenerate)",
      as_int(_fixed) is not None and _fixed > 0, True)
check("the record is a strict part of it",
      as_int(_fixed) is not None and _measured is not None
      and 0 < _measured < _fixed, True)
check("the template's own share is positive and derivable",
      as_int(_fixed) is not None and _measured is not None
      and (_fixed - _measured) > 0, True)

print("\n  6c. and the template share is NOT a column of its own")
#
# One fact, one home. A stored derived quantity is a second copy that can go
# stale on its own, so the schema must not grow one.
check("no template-share column was added",
      [c for c in INFERENCE_COLUMN_ADDITIONS
       if "template" in c and "token" in c], [])


#------------------------------------------------------------------------------


# ===========================================================================
# CLEANUP
# ===========================================================================

shutil.rmtree(_TMP_DIR, ignore_errors=True)
check("the scratch database was removed", os.path.exists(_SCRATCH_DB), False)

# The one dependency this file redirected, put back. Nothing else in this
# process reads it, so this is hygiene rather than a fix -- but a stand-in left
# installed is the kind of thing the next file to be run in the same process
# inherits, and tests/test_agent_stage5_input_packing.py already asserts this
# of itself.
deps.clear_override(deps.OPENAI_CLIENT)
check("the OpenAI override this file installed was cleared",
      deps.is_resolved(deps.OPENAI_CLIENT)
      and deps.peek(deps.OPENAI_CLIENT) is not None, False)
# ---------------------------------------------------------------------------
# RELEASE THE PROCESS-GLOBAL CALL-MODE PIN THIS FILE INSTALLED
# ---------------------------------------------------------------------------
#
# ABOVE THE SUMMARY ON PURPOSE, so the outcome is COUNTED. Below it the release
# would still decide the exit code while being absent from the number the
# summary prints -- a run that reported "0 failed" and exited 1.
#
# THE PREVIOUS PIN IS RESTORED RATHER THAN CLEARED OUTRIGHT, on
# `pin_matching_call_mode`'s own contract: it returns what it replaced so a
# caller can put it back, and an outer harness that had pinned something is
# entitled to keep it.
config.clear_matching_call_mode_pin()
if _CALL_MODE_PIN_PREVIOUS is not None:
    config.pin_matching_call_mode(_CALL_MODE_PIN_PREVIOUS)
if config.matching_call_mode_pin() != _CALL_MODE_PIN_PREVIOUS:
    _RESULTS["failed"] += 1
    print("  FAIL  the grouped call-mode pin this file installed was NOT "
          "released -- a later file sharing this process would silently "
          "measure the wrong Stage 5 arm")
else:
    _RESULTS["passed"] += 1
    print("  PASS  the grouped call-mode pin this file installed was released")



# ===========================================================================
# SUMMARY
# ===========================================================================

print()
print("=" * 70)

# --- RELEASE THE PROVIDER PIN, ABOVE THE SUMMARY ---------------------------
#
# ABOVE, NOT BELOW: a release under the results line still decides the exit
# code while being absent from the number the summary printed -- a run that
# reports "0 failed" and exits non-zero. The default-flip pass shipped exactly
# that in three of seven files, which is why the release is one function with
# one caller-visible answer rather than four hand-written lines here.
#
# THE OUTCOME IS RECORDED BEFORE THE RESTORE, so "there was a pin to release"
# cannot be satisfied by a process that never installed one.
_PIN_WHO, _PIN_PREVIOUS, _PIN_RESTORED = _provider_pin.release_openai_arm()
check("[provider pin] the OpenAI pin this file installed was released, and "
      "config.MATCHING_PROVIDER is back to the shipped provider",
      (_PIN_WHO == os.path.basename(__file__), _PIN_PREVIOUS, _PIN_RESTORED,
       _provider_pin.pin_state()),
      (True, _PROVIDER_BEFORE_PIN, True, (None, None)))

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
Created on Thu Aug 13 2026

@author: ramyalsaffar
"""
