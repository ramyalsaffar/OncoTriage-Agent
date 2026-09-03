# Stage 5: entries for a trial that was never sent, and the rank that is stored
###############################################################################

"""
Out-of-Set Detector and Retrieval-Rank Test

TWO CHANGES, ONE OF WHICH IS THE MIRROR IMAGE OF SOMETHING ALREADY BUILT.

1. THE OUT-OF-SET DETECTOR. ``node_llm_classifier_evaluation`` has always
   reconciled in one direction: every trial SENT that came back with no entry is
   recorded, by nct_id, with a named reason. Nothing asked the reverse question
   -- is every entry that came BACK one we sent -- and the usual shape of the
   fault is a substitution. The model answers about a trial that is not in the
   candidate set, that entry displaces a real one, and the real one is then
   missing. The reconciliation records the omission; the fabricated verdict was
   enriched, scored, normalized, ranked, returned to the caller and written to
   ``trial_matches`` as an evaluation of a trial this patient was never a
   candidate for. Nothing raised, no counter moved, and the entry is
   indistinguishable from a real verdict by inspection: it carries an
   NCT-shaped id, criteria, a verdict and an explanation. The ONLY thing that
   separates it is the candidate set, which is known in that node and nowhere
   downstream.

   The entry is now DROPPED before enrichment, counted, and named in one
   structured log event. The displaced trial is left to the reconciliation --
   section 3 proves that handoff happens rather than duplicating it -- and the
   comparison is against THE CHUNK's sent set, not the node's, which section 4
   drives through a real proactive split.

   THE DROP HAS TWO CAUSES AND THEY ARE COUNTED APART. An id in the node's full
   sent set but not in the chunk that answered is the model answering the whole
   batch to every call of a split request: nothing is invented and nothing is
   lost, because that id's own chunk answers it. An id in no sent set at all is
   a fabrication. Only the second reaches
   ``inferences.hallucinated_trials``, whose own definition is "trials never in
   the candidate set sent to it"; the first is visible in the log event, under
   its own count and id list. One number for both would put a provider quirk
   into a column a reader treats as a hallucination rate, and would make a
   split run's figure incomparable with an unsplit run's.

1b. THE SAME TRIAL ANSWERED TWICE. A duplicated SENT id used to leave the stage
   twice -- two trial_matches rows for one trial and an over-counted
   candidates_evaluated -- because the detector cannot see it (the id WAS sent)
   and the reconciliation cannot either (it asks whether each sent trial
   appears AT LEAST once). Within each chunk, entries are now grouped by
   nct_id: identical verdicts keep the FIRST and drop the rest; conflicting
   verdicts are replaced by ONE not_evaluable entry carrying
   NOT_EVALUABLE_CONFLICTING_DUPLICATES, because a judge that contradicts
   itself about a trial has not evaluated it and picking either answer would be
   recording a verdict the model itself contradicted. Compared on the
   NORMALIZED verdict, so "Eligible" beside "eligible" is one answer typed
   twice rather than a contradiction.

   THE INVARIANT THIS FILE NOW PROVES: every sent nct_id leaves the stage
   EXACTLY once, no unsent id ever does, under every combination of fabricated,
   omitted and repeated.

2. trial_number IS THE RETRIEVAL RANK. ``node_finalize`` assigned it by
   ``enumerate`` over the evaluations list, which Stage 5 sorts by match_score
   descending immediately before returning it -- so the stored number was a
   rank within the model's own verdicts, and two runs over an identical
   candidate set could disagree about which trial is "1" because one criterion
   was scored differently. It is the position in ``filtered_trials`` now, which
   is the list Stage 5 was sent in the order Stages 3 and 4 left it.

WHAT THE COLUMNS MEAN AFTER THIS. ``inferences.hallucinated_trials`` and
``trial_matches.hallucinated`` existed and were deliberately NULL. 0 is a
MEASUREMENT now -- "every returned entry was compared against the candidate set
and every one belonged to it" -- and NULL is reserved for a run where no such
comparison completed. ``trial_matches.hallucinated`` can never be 1, by
construction: an out-of-set entry never becomes an evaluation, so it never
becomes a row. Section 5 writes all three states into a throwaway database.

NO NETWORK, NO KEYS, NO SPEND, NO GIT HISTORY, NO CORPUS. Every model response
is a literal built in this file and served by a stub installed through
``oncotriage/agent/deps.py``; the database is a temporary file and every
``log_inference`` call passes ``db_path`` explicitly and asserts on the path the
writer reports back. NOT in tests/run_serial_tests.py's collision matrix: it
writes nothing in the repository -- every plant goes into an in-memory copy,
with both source files hashed before any plant and compared at the end -- and
the two files it reads are written by neither of the suite's two writers.

    python tests/test_agent_out_of_set_detector.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
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
from oncotriage.agent import terminal as _terminal_module
from oncotriage.agent.evaluation import (
    DUPLICATE_CASE_CONFLICTING,
    DUPLICATE_CASE_IDENTICAL,
    DUPLICATE_CASES,
    HALLUCINATION_CHECKED_CLEAN,
    NOT_EVALUABLE_CONFLICTING_DUPLICATES,
    NOT_EVALUABLE_MODEL_OMITTED,
    _collapse_duplicate_entries,
    _out_of_set_label,
    _partition_out_of_set,
    node_llm_classifier_evaluation,
)
from oncotriage.agent.state import (
    TRIAL_VERDICT_ELIGIBLE,
    TRIAL_VERDICT_NOT_EVALUABLE,
)
from oncotriage.agent.terminal import (
    node_error_handler,
    node_finalize,
    node_no_candidates,
)
from oncotriage.storage.database_logger import (
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
# WHAT THIS FILE MEASURES IS CROSS-CHUNK RECONCILIATION AT CHUNK SIZE > 1, and
# an id 'belonging to another chunk' requires more than one chunk. Per-trial mode's chunks are singletons, and the out-of-set semantics at chunk size
# one are covered by section 6 of tests/test_agent_stage5_per_trial_calls.py.
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


# The modules under test, located from their OWN __file__ rather than from this
# test's directory, so a future move of either cannot silently point the plants
# at a same-named copy.
_EVAL_SRC = os.path.abspath(_evaluation_module.__file__)
_TERMINAL_SRC = os.path.abspath(_terminal_module.__file__)


def _sha256_of(path):
    return hashlib.sha256(
        open(path, encoding="utf-8").read().encode()).hexdigest()


# Taken before any plant runs, so the restore assertion in section 8 compares
# against a real baseline rather than against itself.
_SHA_BEFORE = {p: _sha256_of(p) for p in (_EVAL_SRC, _TERMINAL_SRC)}


class _PlantFailed(Exception):
    """A plant that did not apply or did not compile. Never escapes a check."""


def _plant(path, name, subs):
    """Exec an in-memory COPY of `path` with `subs` applied.

    Raises _PlantFailed -- never SyntaxError -- so a malformed plant is a
    RECORDED failure instead of a traceback hiding every check below it. Same
    shape as tests/test_agent_trial_verdict_normalization.py, and for the same
    reason: nothing on disk is touched.
    """
    source = open(path, encoding="utf-8").read()
    before = hashlib.sha256(source.encode()).hexdigest()
    try:
        for old, new in subs:
            if old not in source:
                raise _PlantFailed(f"plant target absent: {old[:70]!r}...")
            source = source.replace(old, new, 1)
        module = types.ModuleType(name)
        module.__file__ = path
        exec(compile(source, path, "exec"), module.__dict__)
    except _PlantFailed:
        raise
    except Exception as exc:            # noqa: BLE001 - reported, not raised
        raise _PlantFailed(f"{type(exc).__name__}: {exc}") from None
    finally:
        after = hashlib.sha256(
            open(path, encoding="utf-8").read().encode()).hexdigest()
        if before != after:
            raise AssertionError(f"{path} was modified on disk by a plant")
    return module


_CONTROL_SEQ = [0]


def _planted(path, subs):
    """Plant and return the module, or raise _PlantFailed."""
    _CONTROL_SEQ[0] += 1
    return _plant(path, f"planted_{_CONTROL_SEQ[0]}", subs)


def _control(label, path, subs, probe, expected):
    """Plant, probe the planted module, record. A raise IS an outcome."""
    try:
        module = _planted(path, subs)
    except _PlantFailed as exc:
        check(f"{label}  [THE PLANT ITSELF FAILED: {exc}]", "plant-failed",
              expected)
        return
    try:
        actual = probe(module)
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        actual = f"raised {type(exc).__name__}"
    check(label, actual, expected)


# ===========================================================================
# FIXTURES: a patient, some trials, and a stub that serves a chosen response
# ===========================================================================

PATIENT = {
    "patient_id": "out-of-set-detector-patient",
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


def entry(nct_id, eligible="eligible", inclusion=(), exclusion=(),
          assessment="text", omit_id=False):
    """One evaluation entry as the model returns it."""
    payload = {
        "match_score": 0.5, "assessment": assessment, "eligible": eligible,
        "inclusion_criteria": list(inclusion),
        "exclusion_criteria": list(exclusion),
    }
    if not omit_id:
        payload["nct_id"] = nct_id
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
    """Serves one chosen JSON payload, to every call. No network, no spend.

    Serving the SAME payload to every chunk is what makes section 4 work: under
    a proactive split each call is handed a response that answers about the
    other chunk's trials too, which is precisely the cross-chunk case the
    detector must reject.
    """

    def __init__(self, payload, finish_reason="stop", raw=False):
        # raw=True serves the string verbatim, which is the only way to reach
        # the JSONDecodeError branch: json.dumps of anything is valid JSON.
        self._payload = payload if raw else json.dumps(payload)
        self._finish_reason = finish_reason
        self.calls = 0
        self.chat = self
        self.completions = self

    def create(self, model, messages, **kwargs):
        self.calls += 1
        return _StubResponse(self._payload, self._finish_reason)


# A RAISE IS AN OUTCOME, NOT A REASON TO ABORT. Reverting a fix under test can
# make Stage 5 raise -- a TypeError out of an unhashable nct_id is one of the
# things the detector exists to prevent -- and with a bare call that raise would
# escape through check()'s argument list and take the file down, reporting one
# traceback where it owed every result below. The drivers return a
# result-shaped stand-in carrying `raised` instead.

def _raised_result(exc):
    return {"evaluations": [], "raised": type(exc).__name__,
            "hallucinated_trials": f"raised {type(exc).__name__}"}


def _raised_final(exc):
    return {"matches": [], "near_misses": [], "not_evaluable": [],
            "raised": type(exc).__name__}


def make_state(nct_ids):
    """The state Stage 5 is handed, with `nct_ids` as the candidate set."""
    return {
        "patient_data": PATIENT,
        "filtered_trials": [{"trial": trial(n), "rerank_score": 5.0,
                             "rerank_score_raw": 5.0} for n in nct_ids],
        "llm_classifier_retries": 0,
        "mesh_filter_applied": True,
        "mesh_filter_skip_reason": "applied",
        "stage_timings": {},
    }


def run_stage5(payload, nct_ids=("NCT00000001",), node=None,
               finish_reason="stop", raw=False):
    """Drive Stage 5 with a stubbed model. Returns (result, stderr_text).

    `nct_ids` IS THE SENT SET -- the whole point of this file. It is the
    candidate set the node believes it dispatched, and the payload is what came
    back; the two are independent here, which is what lets a fabricated id be
    served without inventing a candidate for it.
    """
    node = node or node_llm_classifier_evaluation
    state = make_state(nct_ids)
    saved = deps.set_overrides({"openai_client": StubOpenAI(payload,
                                                            finish_reason,
                                                            raw=raw)})
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            result = node(state)
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        result = _raised_result(exc)
    finally:
        deps.restore_overrides(saved)
    return result, err.getvalue()


def run_stage6(stage5_result, nct_ids=("NCT00000001",), node=None):
    """Drive Stage 6 over Stage 5's return, the way LangGraph merges state."""
    node = node or node_finalize
    state = make_state(nct_ids)
    state.update({k: v for k, v in stage5_result.items() if k != "raised"})
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            out = node(state)["result"]
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        out = _raised_final(exc)
    return out, err.getvalue()


def log_records(stderr_text, event=None):
    """Every structured record on the captured stream, optionally by event."""
    out = []
    for line in stderr_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if event is None or record.get("event") == event:
            out.append(record)
    return out


def field(records, key):
    """One field off the FIRST record, or a named absence.

    NEVER ``records[0][key]``: a defect that stops a record being emitted is
    exactly what these checks exist to catch, and a bare index turns that into
    an IndexError at module level.
    """
    if not records:
        return "<no such record>"
    return records[0].get(key, "<no such field>")


def verdict_of(result, nct_id):
    for e in result["evaluations"]:
        if e.get("nct_id") == nct_id:
            return e.get("eligible")
    return "<absent>"


def ids_of(result):
    return sorted(str(e.get("nct_id")) for e in result["evaluations"])


def reason_of(result, nct_id):
    for e in result["evaluations"]:
        if e.get("nct_id") == nct_id:
            return e.get("not_evaluable_reason")
    return "<absent>"


SENT = "NCT00000001"
SENT_2 = "NCT00000002"
FAKE = "NCT99999999"

# An entry whose nct_id is unhashable. `[] in {"a"}` raises TypeError, so this
# is the shape that would take a whole patient's run down inside the detector
# if the isinstance test were removed. Built once, used by section 7's control
# and by its positive arm.
_UNHASHABLE_ENTRY = dict(entry(SENT), nct_id=[])


print("=" * 75)
print("OUT-OF-SET DETECTOR AND RETRIEVAL-RANK TEST")
print("=" * 75)


# ===========================================================================
# SECTION 1 -- the partition, as a unit
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 1 -- _partition_out_of_set and _out_of_set_label")
print("=" * 75)

# The chunk's sent set and the node's. Equal here except where a case needs
# them to differ, which is what makes the cross-chunk bucket observable at the
# unit level as well as through a real split (section 4).
_sent = {SENT, SENT_2}

_in, _cross, _fab = _partition_out_of_set(
    [entry(SENT), entry(FAKE), entry(SENT_2)], _sent, _sent)
check("entries whose id this call asked about are kept, in order",
      [e["nct_id"] for e in _in], [SENT, SENT_2])
check("...an id in no sent set at all is FABRICATED", _fab, [FAKE])
check("...and nothing is miscounted as cross-chunk", _cross, [])

# THE SPLIT THE COLUMN DEPENDS ON. Same drop, two different faults: the id is a
# real candidate of this run, answered by another chunk.
_in2, _cross2, _fab2 = _partition_out_of_set(
    [entry(SENT), entry(SENT_2)], {SENT}, {SENT, SENT_2})
check("an id in the node's sent set but not this chunk's is CROSS-CHUNK",
      (_cross2, _fab2), ([SENT_2], []))
check("...and it is still dropped from what this call contributes",
      [e["nct_id"] for e in _in2], [SENT])

check("an empty chunk set rejects everything",
      _partition_out_of_set([entry(SENT)], set(), set())[2], [SENT])
check("an empty response reports nothing",
      _partition_out_of_set([], _sent, _sent), ([], [], []))

# A MISSING id is out of set, and this is where such an entry now stops. Before
# it reached enrichment as "", matched no trial, kept no title or phase, and
# left the stage as a verdict about nothing.
check("an entry with no nct_id at all is out of set, as a fabrication",
      _partition_out_of_set([entry(SENT, omit_id=True)], _sent, _sent)[2],
      ["<NoneType>"])

# THE UNHASHABLE CASE. `[] in {"a"}` raises TypeError, so without the isinstance
# test the detector added to stop a class of loss would itself take the whole
# patient's run down. Each is also, unambiguously, not one of the ids sent, and
# each is FABRICATED: it names no trial anywhere in this run.
for _bad, _label in (([], "<list>"), ({}, "<dict>"), (42, "<int>"),
                     (None, "<NoneType>"), (3.5, "<float>"), (True, "<bool>")):
    _e = entry(SENT)
    _e["nct_id"] = _bad
    check(f"a {type(_bad).__name__} nct_id is fabricated and does not raise",
          _partition_out_of_set([_e], _sent, _sent)[2], [_label])

# The label: the id and nothing else, capped.
check("a string id is reported as itself", _out_of_set_label(SENT), SENT)
check("an empty string id is named rather than printed as nothing",
      _out_of_set_label(""), "<empty>")
check("a long id is capped, so a sentence written into the field cannot "
      "enter a durable record",
      len(_out_of_set_label("N" * 400)),
      _evaluation_module._OUT_OF_SET_ID_PREVIEW_LEN)
check("a non-string id is reported by TYPE, never by content",
      _out_of_set_label({"secret": "clinical prose"}), "<dict>")

# Non-degeneracy: the partition can put entries on all three sides at once, so
# the assertions above are not all reading one branch.
_in3, _cross3, _fab3 = _partition_out_of_set(
    [entry(SENT), entry(SENT_2), entry(FAKE)], {SENT}, {SENT, SENT_2})
check("non-degeneracy: one call can produce a keep, a cross-chunk and a "
      "fabrication",
      (len(_in3), len(_cross3), len(_fab3)), (1, 1, 1))


# ===========================================================================
# SECTION 1b -- the duplicate collapse, as a unit
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 1b -- _collapse_duplicate_entries")
print("=" * 75)

check("the case vocabulary is closed and a caller may branch on it",
      DUPLICATE_CASES, (DUPLICATE_CASE_IDENTICAL, DUPLICATE_CASE_CONFLICTING))

_kept, _coll = _collapse_duplicate_entries([entry(SENT), entry(SENT_2)])
check("entries with no duplicate are untouched",
      ([e["nct_id"] for e in _kept], _coll), ([SENT, SENT_2], []))

# IDENTICAL: the model said one thing twice. The FIRST is kept, which is the
# deterministic choice rather than an arbitrary one.
_first = entry(SENT, "eligible", assessment="the first answer")
_second = entry(SENT, "eligible", assessment="the second answer")
_kept, _coll = _collapse_duplicate_entries([_first, _second])
check("identical verdicts collapse to one entry", len(_kept), 1)
check("...and it is the FIRST one, by identity", _kept[0] is _first, True)
check("...reported with its case and how many arrived",
      _coll, [{"nct_id": SENT, "case": DUPLICATE_CASE_IDENTICAL, "count": 2}])

# CASE AND WHITESPACE ARE NOT A CONTRADICTION. Compared on the normalized
# verdict, so "Eligible" beside "eligible" is one answer typed twice.
_kept, _coll = _collapse_duplicate_entries(
    [entry(SENT, "eligible"), entry(SENT, "Eligible")])
check("'Eligible' beside 'eligible' is identical, not conflicting",
      [d["case"] for d in _coll], [DUPLICATE_CASE_IDENTICAL])

# CONFLICTING: no entry survives. The caller replaces them with one
# not_evaluable record; building it here would need trial metadata this helper
# has no business knowing.
_kept, _coll = _collapse_duplicate_entries(
    [entry(SENT, "eligible"), entry(SENT, "not_eligible")])
check("conflicting verdicts leave NO entry for the caller to keep", _kept, [])
check("...reported as conflicting",
      _coll, [{"nct_id": SENT, "case": DUPLICATE_CASE_CONFLICTING, "count": 2}])

# TWO DIFFERENT UNREADABLE LABELS ARE ONE UNREADABLE ANSWER, and that choice is
# load-bearing: it leaves the existing rule intact, where the criteria decide
# and a stated "not_met" is still a rejection rather than being deleted by a
# conflict verdict.
_kept, _coll = _collapse_duplicate_entries(
    [entry(SENT, "elligible"), entry(SENT, "maybe")])
check("two different unreadable labels are treated as one unreadable answer",
      [d["case"] for d in _coll], [DUPLICATE_CASE_IDENTICAL])

# Three entries, and a clean one beside them.
_kept, _coll = _collapse_duplicate_entries(
    [entry(SENT), entry(SENT_2), entry(SENT), entry(SENT)])
check("the count is how many entries arrived for that id",
      _coll, [{"nct_id": SENT, "case": DUPLICATE_CASE_IDENTICAL, "count": 3}])
check("...and the entry beside them is untouched, in order",
      [e["nct_id"] for e in _kept], [SENT, SENT_2])


# ===========================================================================
# SECTION 2 -- Stage 5 drops, counts and announces an out-of-set entry
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 2 -- the entry is dropped, counted and logged")
print("=" * 75)

_res, _err = run_stage5([entry(FAKE, "eligible", inclusion=[crit("met")]),
                         entry(SENT, "eligible", inclusion=[crit("met")])],
                        nct_ids=(SENT,))

check("the fabricated entry does not raise", _res.get("raised"), None)
check("it becomes no verdict of any kind", verdict_of(_res, FAKE), "<absent>")
check("...and no entry of any kind survives under its id",
      [e for e in _res["evaluations"] if e.get("nct_id") == FAKE], [])
check("the well-formed entry beside it still gets its verdict",
      verdict_of(_res, SENT), TRIAL_VERDICT_ELIGIBLE)
check("...and its score was still recomputed over applicable criteria",
      [e["match_score"] for e in _res["evaluations"]
       if e["nct_id"] == SENT], [1.0])
check("the count reaches the result dict", _res["hallucinated_trials"], 1)

_ooset = log_records(_err, "out_of_set_entry")
check("one structured event was emitted", len(_ooset), 1)
check("...naming the total, and the fabricated count and ids",
      (field(_ooset, "count"), field(_ooset, "fabricated_count"),
       field(_ooset, "fabricated_nct_ids")), (1, 1, [FAKE]))
check("...and reporting the cross-chunk bucket as empty rather than omitting "
      "it, so a reader never has to guess which bucket a drop fell in",
      (field(_ooset, "cross_chunk_count"),
       field(_ooset, "cross_chunk_nct_ids")), (0, []))
check("...at WARNING, not swallowed at INFO",
      field(_ooset, "level"), "WARNING")
check("...and it carries no field beyond the ids -- no verdict, no criteria, "
      "no assessment",
      sorted(set(_ooset[0]) & {"eligible", "assessment", "match_score",
                               "criterion", "patient_value"}) if _ooset
      else "<no record>", [])

# Several at once, and a response that is ENTIRELY fabricated.
_all_fake, _af_err = run_stage5(
    [entry(FAKE), entry("NCT88888888"), entry("NCT77777777")], nct_ids=(SENT,))
check("a response that is entirely out of set counts every entry",
      _all_fake["hallucinated_trials"], 3)
check("...and one record names all three",
      sorted(field(log_records(_af_err, "out_of_set_entry"),
                   "fabricated_nct_ids")),
      ["NCT77777777", "NCT88888888", FAKE])
check("...and the only evaluation left is the sent trial, reconciled",
      [(e["nct_id"], e["eligible"]) for e in _all_fake["evaluations"]],
      [(SENT, TRIAL_VERDICT_NOT_EVALUABLE)])

# THE COUNT IS PER ENTRY, NOT PER DISTINCT ID. A model that invents the same id
# twice produced two fabricated verdicts, and reporting "1" would understate
# what it did.
_twice, _ = run_stage5([entry(FAKE), entry(FAKE), entry(SENT)], nct_ids=(SENT,))
check("the same fabricated id twice counts twice",
      _twice["hallucinated_trials"], 2)

# A CLEAN RUN REPORTS 0, WHICH IS A MEASUREMENT. Without this the count would be
# satisfied by a detector that only ever wrote a number when it found something.
_clean, _clean_err = run_stage5([entry(SENT, "eligible", inclusion=[crit("met")])],
                               nct_ids=(SENT,))
check("a clean run reports 0 rather than omitting the count",
      _clean["hallucinated_trials"], 0)
check("...and says nothing, because there was nothing to say",
      len(log_records(_clean_err, "out_of_set_entry")), 0)
check("...and every surviving evaluation is stamped checked-and-clean",
      [e.get("hallucinated") for e in _clean["evaluations"]],
      [HALLUCINATION_CHECKED_CLEAN])


# ===========================================================================
# SECTION 3 -- the displaced trial is reconciled, not re-implemented
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 3 -- the two directions hand off by nct_id")
print("=" * 75)

# THE SUBSTITUTION, which is the realistic shape: two trials sent, and the model
# answers about one of them and about a trial that does not exist.
_sub, _sub_err = run_stage5(
    [entry(SENT, "eligible", inclusion=[crit("met")]),
     entry(FAKE, "eligible", inclusion=[crit("met")])],
    nct_ids=(SENT, SENT_2))

check("the fabricated entry is dropped", verdict_of(_sub, FAKE), "<absent>")
check("the displaced candidate is recorded as not evaluable",
      verdict_of(_sub, SENT_2), TRIAL_VERDICT_NOT_EVALUABLE)
check("...with the omission reason the existing reconciliation names",
      reason_of(_sub, SENT_2), NOT_EVALUABLE_MODEL_OMITTED)
check("...by the existing reconciliation, which announced it",
      field(log_records(_sub_err, "reconciliation"), "nct_ids"), [SENT_2])
check("...and the detector announced its own half separately",
      field(log_records(_sub_err, "out_of_set_entry"), "fabricated_nct_ids"),
      [FAKE])
check("the trial that WAS answered keeps its verdict",
      verdict_of(_sub, SENT), TRIAL_VERDICT_ELIGIBLE)

# THE INVARIANT, over every payload shape this file serves. Stated as an
# equality rather than as two subset checks, so neither a lost sent trial nor a
# surviving fabricated one can satisfy it.
_INVARIANT_CASES = (
    ("a clean response", [entry(SENT), entry(SENT_2)]),
    ("one fabricated entry", [entry(SENT), entry(FAKE), entry(SENT_2)]),
    ("a substitution", [entry(SENT), entry(FAKE)]),
    ("nothing but fabrications", [entry(FAKE), entry("NCT88888888")]),
    ("an entry with no id", [entry(SENT), entry(SENT_2, omit_id=True)]),
    ("a non-object beside a fabrication", ["NCT00000001", entry(FAKE)]),
    ("an empty response", []),
    # The three faults at once, which is what the invariant is for: one id
    # fabricated, one sent trial omitted, one sent trial answered twice.
    ("fabricated + omitted + duplicated",
     [entry(SENT), entry(FAKE), entry(SENT)]),
    ("identical duplicates of both sent trials",
     [entry(SENT), entry(SENT_2), entry(SENT), entry(SENT_2)]),
    ("conflicting duplicates of one, clean answer for the other",
     [entry(SENT, "eligible"), entry(SENT, "not_eligible"), entry(SENT_2)]),
)
for _label, _payload in _INVARIANT_CASES:
    _r, _ = run_stage5(_payload, nct_ids=(SENT, SENT_2))
    check(f"invariant ({_label}): every sent id leaves exactly once, and no "
          f"unsent id ever does",
          ids_of(_r), [SENT, SENT_2])

# A DUPLICATED SENT ID IS NOT A FABRICATION AND IS NOW COLLAPSED.
#
# THIS PIN USED TO RECORD THE OPPOSITE. It read "PRE-EXISTING, NOT FIXED HERE:
# a duplicated sent id leaves the stage twice" -- two evaluations for one
# trial, two trial_matches rows, a candidates_evaluated that over-counts --
# because the detector cannot see it (the id WAS sent) and the reconciliation
# cannot either (it asks whether each sent trial appears AT LEAST once). That
# defect is closed by the duplicate policy, so the pin now asserts the policy.
# Section 3b drives both cases through the node.
_dup, _ = run_stage5([entry(SENT), entry(SENT)], nct_ids=(SENT,))
check("a duplicated SENT id is not counted as a fabrication",
      _dup["hallucinated_trials"], 0)
check("...and it now leaves the stage exactly once", ids_of(_dup), [SENT])


# ===========================================================================
# SECTION 3b -- the duplicate policy, through the node
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 3b -- identical duplicates collapse, conflicting ones do not "
      "get to pick a winner")
print("=" * 75)

# IDENTICAL: one entry survives, and it is the FIRST answer -- checked on the
# assessment DRAFT, because the two entries are otherwise indistinguishable and
# an assertion that could not tell them apart would pass either way.
#
# IT USED TO READ `assessment`, AND SINCE PROMPT_VERSION 1.5.0 THAT FIELD CANNOT
# DISCRIMINATE. Stage 5 now composes the stored assessment from the criteria
# arrays (evaluation.py:compose_assessment), and these two entries carry
# IDENTICAL arrays by construction -- that is what makes them identical
# duplicates -- so both collapse to the same composed string and the check would
# pass whichever survived. `assessment_draft` is the model's own text, snapshot
# before any validator runs, and it is the only per-entry field left that still
# tells the two apart. The non-degeneracy probe below is what says so.
_ident, _ident_err = run_stage5(
    [entry(SENT, "eligible", inclusion=[crit("met")], assessment="first"),
     entry(SENT, "eligible", inclusion=[crit("met")], assessment="second")],
    nct_ids=(SENT,))
check("identical duplicates leave one evaluation", ids_of(_ident), [SENT])
check("...and it is the FIRST answer, not the last",
      [e.get("assessment_draft") for e in _ident["evaluations"]], ["first"])
check("...non-degeneracy: the two drafts really were distinguishable, and the "
      "composed assessment really is not (which is why the check above moved "
      "off it)",
      ("first" != "second",
       [e.get("assessment") for e in _ident["evaluations"]]
       != ["first"]),
      (True, True))
check("...which keeps its verdict", verdict_of(_ident, SENT),
      TRIAL_VERDICT_ELIGIBLE)
check("...and is not recorded as a non-evaluation",
      reason_of(_ident, SENT), None)

_dup_rec = log_records(_ident_err, "duplicate_answers")
check("one structured event was emitted", len(_dup_rec), 1)
check("...naming the identical case and its id",
      (field(_dup_rec, "count"), field(_dup_rec, "duplicate_identical_count"),
       field(_dup_rec, "duplicate_identical_nct_ids")), (1, 1, [SENT]))
check("...and reporting the conflicting bucket as empty rather than omitting "
      "it",
      (field(_dup_rec, "duplicate_conflicting_count"),
       field(_dup_rec, "duplicate_conflicting_nct_ids")), (0, []))
check("...at WARNING", field(_dup_rec, "level"), "WARNING")

# CONFLICTING: neither answer wins. Picking one would be recording a verdict
# the model itself contradicted.
_conf, _conf_err = run_stage5(
    [entry(SENT, "eligible", inclusion=[crit("met")]),
     entry(SENT, "not_eligible", inclusion=[crit("not_met")])],
    nct_ids=(SENT,))
check("conflicting duplicates leave one evaluation", ids_of(_conf), [SENT])
check("...and it is a non-evaluation, not either of the two verdicts",
      verdict_of(_conf, SENT), TRIAL_VERDICT_NOT_EVALUABLE)
check("...naming the conflicting-duplicate reason",
      reason_of(_conf, SENT), NOT_EVALUABLE_CONFLICTING_DUPLICATES)
check("...with an assessment that says what happened",
      "disagreed on the verdict" in str(
          [e.get("assessment") for e in _conf["evaluations"]]), True)
check("...and the event names it as conflicting",
      (field(log_records(_conf_err, "duplicate_conflicting_count")
             or log_records(_conf_err, "duplicate_answers"),
             "duplicate_conflicting_nct_ids"),
       field(log_records(_conf_err, "duplicate_answers"),
             "duplicate_identical_nct_ids")), ([SENT], []))
check("...and it is NOT reported as an omission, which would be false -- the "
      "model answered, twice",
      len(log_records(_conf_err, "reconciliation")), 0)

# THE NEIGHBOUR IS UNTOUCHED. A duplicate pair beside a clean entry must not
# cost the clean entry its verdict.
_pair, _ = run_stage5(
    [entry(SENT, "eligible", inclusion=[crit("met")]),
     entry(SENT, "not_eligible", inclusion=[crit("not_met")]),
     entry(SENT_2, "eligible", inclusion=[crit("met")])],
    nct_ids=(SENT, SENT_2))
check("a conflicting pair beside a clean entry leaves the clean one alone",
      verdict_of(_pair, SENT_2), TRIAL_VERDICT_ELIGIBLE)
check("...with its score still recomputed",
      [e["match_score"] for e in _pair["evaluations"]
       if e["nct_id"] == SENT_2], [1.0])
check("...while the contradicted trial is not evaluable",
      verdict_of(_pair, SENT), TRIAL_VERDICT_NOT_EVALUABLE)
check("...and every sent id still leaves exactly once",
      ids_of(_pair), [SENT, SENT_2])

# A duplicate is not a fabrication, in either case.
check("neither duplicate case is counted into hallucinated_trials",
      (_ident["hallucinated_trials"], _conf["hallucinated_trials"]), (0, 0))

# THREE identical answers, so "collapse" is not just "drop the second".
_three, _ = run_stage5([entry(SENT, "eligible", inclusion=[crit("met")])] * 3,
                       nct_ids=(SENT,))
check("three identical answers collapse to one", ids_of(_three), [SENT])


# ===========================================================================
# SECTION 4 -- the sent set is the CHUNK's, driven through a real split
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 4 -- a cross-chunk id is out of set for the call that got it")
print("=" * 75)

# Enough trials that estimate_output_tokens exceeds the proactive split
# threshold, so the node really does issue more than one call. The number is
# DERIVED from the config rather than typed, so a re-tuned ceiling does not
# silently turn this section into a single-call test.
_SPLIT_N = (int(_evaluation_module.MATCHING_MAX_TOKENS
                * _evaluation_module.MATCHING_OUTPUT_SPLIT_FRACTION)
            // _evaluation_module.MATCHING_OUTPUT_TOKENS_PER_TRIAL) + 2
_SPLIT_IDS = tuple(f"NCT1000{i:04d}" for i in range(_SPLIT_N))

def _run_split(node=None):
    """Drive one batch big enough to be pre-split. Returns (result, stderr, stub).

    A function rather than a script block because section 7's chunk/node
    control has to run the identical scenario against a planted module.
    """
    node = node or node_llm_classifier_evaluation
    stub = StubOpenAI([entry(n, "eligible", inclusion=[crit("met")])
                       for n in _SPLIT_IDS])
    saved = deps.set_overrides({"openai_client": stub})
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            result = node(make_state(_SPLIT_IDS))
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        result = _raised_result(exc)
    finally:
        deps.restore_overrides(saved)
    return result, err.getvalue(), stub


_split, _split_err, _stub = _run_split()

check("non-degeneracy: the batch really did split into more than one call",
      _stub.calls > 1, True)
check("...and the node recorded that many calls",
      _split.get("llm_classifier_calls"), _stub.calls)
check("every sent trial still leaves the stage exactly once",
      ids_of(_split), sorted(_SPLIT_IDS))
check("...each with the verdict its own chunk's response carried",
      sorted({e.get("eligible") for e in _split["evaluations"]}),
      [TRIAL_VERDICT_ELIGIBLE])

# EACH CALL WAS HANDED THE WHOLE ANSWER, so each one sees the other chunk's ids
# as entries it did not ask about. Against the union of the node's sent ids the
# drop would not happen at all and every trial would be evaluated TWICE.
_split_records = log_records(_split_err, "out_of_set_entry")
_cross_total = sum(r.get("cross_chunk_count", 0) for r in _split_records)
_fab_total = sum(r.get("fabricated_count", 0) for r in _split_records)
check("ids belonging to another chunk are dropped by the call that got them",
      _cross_total, _SPLIT_N * (_stub.calls - 1))
check("...reported once per call", len(_split_records), _stub.calls)
check("non-degeneracy: that count is not zero", _cross_total > 0, True)

# AND THEY ARE NOT FABRICATIONS. This is the whole reason for the split: every
# one of those ids names a real candidate of this run, answered by another
# call, so it costs the patient nothing and must not enter the column a reader
# treats as a hallucination rate.
check("a cross-chunk id is NOT counted as a fabrication", _fab_total, 0)
check("...so the stored count stays 0 on a split with no invented id",
      _split["hallucinated_trials"], 0)
check("...and the ids named in the cross-chunk bucket are all real candidates",
      sorted(set(sum((r.get("cross_chunk_nct_ids", [])
                      for r in _split_records), []))) == sorted(_SPLIT_IDS),
      True)

# A FABRICATION INSIDE A SPLIT IS STILL A FABRICATION, which is what makes the
# two buckets discriminating rather than one of them just being empty.
_mixed_stub = StubOpenAI([entry(n, "eligible", inclusion=[crit("met")])
                          for n in _SPLIT_IDS] + [entry(FAKE)])
_saved = deps.set_overrides({"openai_client": _mixed_stub})
_mixed_err = io.StringIO()
try:
    with contextlib.redirect_stderr(_mixed_err):
        _mixed = node_llm_classifier_evaluation(make_state(_SPLIT_IDS))
except Exception as _exc:               # noqa: BLE001 - a raise IS an outcome
    _mixed = _raised_result(_exc)
finally:
    deps.restore_overrides(_saved)
_mixed_records = log_records(_mixed_err.getvalue(), "out_of_set_entry")
check("a fabricated id inside a split run is counted as fabricated, once per "
      "call that received it",
      _mixed["hallucinated_trials"], _mixed_stub.calls)
check("...while the cross-chunk ids beside it stay in their own bucket",
      sum(r.get("cross_chunk_count", 0) for r in _mixed_records),
      _SPLIT_N * (_mixed_stub.calls - 1))
check("...and every sent trial still leaves exactly once",
      ids_of(_mixed), sorted(_SPLIT_IDS))


# ===========================================================================
# SECTION 5 -- the count reaches a throwaway database
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 5 -- hallucinated_trials and the per-trial marker, stored")
print("=" * 75)

_TMP = tempfile.mkdtemp(prefix="oncotriage_out_of_set_")
_DB = os.path.join(_TMP, "inferences_test.db")

# THE ISOLATION IS ASSERTED BEFORE IT IS RELIED ON, on File 36's precedent: the
# explicit db_path is only doing work if the default resolves somewhere else.
_production = resolve_inference_db_path(None)
check("the DEFAULT database is production, not this file's scratch path",
      _production == _DB, False)
check("...and production is a real resolved path (non-degeneracy)",
      bool(_production), True)


def store(result, patient_id):
    """log_inference into the scratch database, asserting the path it used."""
    result = dict(result)
    result["patient_id"] = patient_id
    reported = log_inference(result, PATIENT, db_path=_DB)
    check(f"{patient_id}: the writer reported the scratch database",
          str(reported), _DB)
    check(f"{patient_id}: ...and the write succeeded",
          getattr(reported, "ok", "<no ok field>"), True)
    return reported


def stored(patient_id, column="hallucinated_trials"):
    conn = sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            f"SELECT {column} FROM inferences WHERE patient_id = ? "
            f"ORDER BY id DESC LIMIT 1", (patient_id,)).fetchone()
        return row[column] if row else "<no row>"
    finally:
        conn.close()


def stored_marks(patient_id):
    conn = sqlite3.connect(_DB)
    try:
        return dict(conn.execute(
            "SELECT nct_id, hallucinated FROM trial_matches WHERE "
            "inference_id = (SELECT id FROM inferences WHERE patient_id = ? "
            "ORDER BY id DESC LIMIT 1)", (patient_id,)).fetchall())
    finally:
        conn.close()


# --- a clean run: 0, which is a measurement ------------------------------
_clean_final, _ = run_stage6(_clean, nct_ids=(SENT,))
store(_clean_final, "clean-run")
check("a clean run stores 0, not NULL", stored("clean-run"), 0)
check("...and it is a stored 0 rather than a NULL", stored("clean-run") is None,
      False)
check("...with the per-trial marker on the row",
      stored_marks("clean-run"), {SENT: HALLUCINATION_CHECKED_CLEAN})

# --- a planted run: the real count ---------------------------------------
_planted_final, _ = run_stage6(_sub, nct_ids=(SENT, SENT_2))
store(_planted_final, "planted-run")
check("a run that saw a fabricated entry stores the real count",
      stored("planted-run"), 1)
check("...and the fabricated id is in no trial_matches row",
      FAKE in stored_marks("planted-run"), False)
check("...while both SENT trials are, both marked checked-and-clean",
      stored_marks("planted-run"),
      {SENT: HALLUCINATION_CHECKED_CLEAN, SENT_2: HALLUCINATION_CHECKED_CLEAN})

# --- THE COLUMN IS FABRICATED-ONLY (item 3), stored -----------------------
# The split run of section 4 dropped _SPLIT_N cross-chunk entries and invented
# nothing. The column must therefore read 0, and the drop must be visible only
# in the log. Written through the real terminal node and the real INSERT, not
# read off the node's return, because the claim is about what a reader of the
# database sees.
_split_final, _ = run_stage6(_split, nct_ids=_SPLIT_IDS)
store(_split_final, "cross-chunk-only")
check("a split run whose only drops were cross-chunk stores 0 fabrications",
      stored("cross-chunk-only"), 0)
check("non-degeneracy: that run really did drop cross-chunk entries",
      _cross_total, _SPLIT_N * (_stub.calls - 1))
check("...and every sent trial got a row, all marked checked-and-clean",
      sorted(stored_marks("cross-chunk-only")) == sorted(_SPLIT_IDS)
      and set(stored_marks("cross-chunk-only").values()) ==
      {HALLUCINATION_CHECKED_CLEAN}, True)

_mixed_final, _ = run_stage6(_mixed, nct_ids=_SPLIT_IDS)
store(_mixed_final, "cross-chunk-and-fabrication")
check("...while a fabrication in the same run IS stored",
      stored("cross-chunk-and-fabrication"), _mixed_stub.calls)
check("non-degeneracy: the two stored counts differ, so the column "
      "discriminates",
      stored("cross-chunk-only") != stored("cross-chunk-and-fabrication"), True)

# --- a run whose duplicates were collapsed: still 0 fabrications ----------
_conf_final, _ = run_stage6(_conf, nct_ids=(SENT,))
store(_conf_final, "conflicting-duplicates")
check("a collapsed duplicate is not a fabrication in the database",
      stored("conflicting-duplicates"), 0)
check("...and the contradicted trial is ONE row, not two",
      list(stored_marks("conflicting-duplicates")), [SENT])

# --- a path where Stage 5 never ran: NULL --------------------------------
# node_no_candidates ends the run before the model is called, so no comparison
# against a candidate set was ever made and the key is absent from state.
_no_cand = node_no_candidates(make_state(()))["result"]
check("node_no_candidates declares the key",
      "hallucinated_trials" in _no_cand, True)
check("...as None rather than 0", _no_cand["hallucinated_trials"], None)
store(_no_cand, "stage5-never-ran")
check("a run where Stage 5 never ran stores NULL",
      stored("stage5-never-ran"), None)

# --- a path where Stage 5 RAN AND FAILED: also NULL -----------------------
#
# A SECOND NULL, AND IT IS A DIFFERENT FACT FROM THE ONE ABOVE. Stage 5 was
# called, spent a request, and returned early on an unparseable response -- so
# some of the answer may have been compared against the candidate set and the
# rest was never seen. A partial count reported as a total would be worse than
# no count, which is why none of Stage 5's early returns carries the key.
_failed, _ = run_stage5("not json at all", nct_ids=(SENT,))
check("non-degeneracy: that response really did fail to parse",
      bool(_failed.get("error")), True)
check("Stage 5's failure return does not carry a partial count",
      "hallucinated_trials" in _failed, False)

_failed_state = make_state((SENT,))
_failed_state.update(_failed)
_err_result = node_error_handler(_failed_state)["result"]
check("the error handler declares the key as None",
      _err_result["hallucinated_trials"], None)
store(_err_result, "stage5-ran-and-failed")
check("a run where Stage 5 failed stores NULL, not 0",
      stored("stage5-ran-and-failed"), None)

check("non-degeneracy: the arms are three different stored values",
      len({repr(stored("clean-run")), repr(stored("planted-run")),
           repr(stored("stage5-never-ran"))}), 3)


# ===========================================================================
# SECTION 6 -- trial_number is the retrieval rank
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 6 -- trial_number comes from the sent list, not the answer")
print("=" * 75)

_RANKED = ("NCT00000001", "NCT00000002", "NCT00000003")


def numbers(final_result):
    out = {}
    for name in ("matches", "near_misses", "not_evaluable"):
        for e in final_result[name]:
            out[e.get("nct_id")] = e.get("trial_number")
    return out


# The model answers in REVERSE order, and with scores that would sort the list
# the other way round. Under the old enumerate-over-evaluations rule the
# top-ranked trial would not be number 1.
_rev, _ = run_stage5(
    [entry("NCT00000003", "eligible", inclusion=[crit("met")]),
     entry("NCT00000002", "eligible", inclusion=[crit("met"), crit("not_met")]),
     entry("NCT00000001", "eligible", exclusion=[crit("violated")])],
    nct_ids=_RANKED)
_rev_final, _ = run_stage6(_rev, nct_ids=_RANKED)

check("trial_number is the position in the list Stage 5 was sent",
      numbers(_rev_final),
      {"NCT00000001": 1, "NCT00000002": 2, "NCT00000003": 3})
check("non-degeneracy: Stage 5 really did reorder the evaluations, so the "
      "old rule would have disagreed",
      [e["nct_id"] for e in _rev["evaluations"]] != list(_RANKED), True)

# A RECONCILIATION ENTRY TAKES ITS RANK FROM THE SAME LIST. Under the old rule
# it landed wherever the score sort put it, which for a not-evaluable trial
# scoring 0.0 is the bottom -- so the number said "last" about a trial the
# pipeline had ranked first.
_omit, _ = run_stage5([entry("NCT00000003", "eligible", inclusion=[crit("met")])],
                      nct_ids=_RANKED)
_omit_final, _ = run_stage6(_omit, nct_ids=_RANKED)
check("a trial the model never mentioned still carries its retrieval rank",
      numbers(_omit_final),
      {"NCT00000001": 1, "NCT00000002": 2, "NCT00000003": 3})
check("non-degeneracy: the two omitted trials really are not evaluable",
      sorted(e["nct_id"] for e in _omit_final["not_evaluable"]),
      ["NCT00000001", "NCT00000002"])

# The model's own trial_number stays untrusted and overwritten, as it already
# was. Served deliberately wrong.
_lying = [entry("NCT00000002", "eligible", inclusion=[crit("met")])]
_lying[0]["trial_number"] = 99
_lie, _ = run_stage5(_lying, nct_ids=_RANKED)
_lie_final, _ = run_stage6(_lie, nct_ids=_RANKED)
check("the model's own trial_number is overwritten",
      numbers(_lie_final)["NCT00000002"], 2)

# An evaluation for a trial that is in no ranking gets no rank, on the same
# footing as the rerank_score lookup beside it. Unreachable from Stage 5 -- the
# detector drops such an entry -- and reachable by a caller building
# evaluations by hand.
_hand, _ = run_stage6({"evaluations": [{"nct_id": "NCT00000009",
                                        "eligible": "eligible",
                                        "match_score": 1.0}]},
                      nct_ids=_RANKED)
check("an evaluation absent from filtered_trials gets no rank rather than a "
      "fabricated one",
      numbers(_hand), {"NCT00000009": None})


# ===========================================================================
# SECTION 7 -- CONTROLS. Every assertion above, shown to fail.
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 7 -- planted defects, each required to be caught")
print("=" * 75)

# The plants go into an in-memory COPY. Nothing on disk is touched; section 8
# hashes both sources to say so.
_probe_module = _planted(_EVAL_SRC, [])
check("PRECONDITION: an unplanted copy of evaluation.py agrees with the "
      "shipped module",
      run_stage5([entry(FAKE), entry(SENT)], nct_ids=(SENT,),
                 node=_probe_module.node_llm_classifier_evaluation
                 )[0]["hallucinated_trials"], 1)

_DETECTOR_CALL = """        _chunk_ids = {t["trial"]["nct_id"] for t in chunk}
        _objects, _cross_chunk, _fabricated = _partition_out_of_set(
            _objects, _chunk_ids, _batch_ids)"""
_NO_DETECTOR = "        _cross_chunk, _fabricated = [], []"

# 7a. THE DEFECT ITSELF, restored: no detector at all. The fabricated entry
#     flows on and becomes a verdict in the patient's results.
_control(
    "7a. removing the detector lets a fabricated entry become a verdict "
    "-- CAUGHT",
    _EVAL_SRC,
    [(_DETECTOR_CALL, _NO_DETECTOR)],
    lambda m: verdict_of(run_stage5([entry(FAKE, "eligible",
                                           inclusion=[crit("met")]),
                                     entry(SENT)], nct_ids=(SENT,),
                                    node=m.node_llm_classifier_evaluation)[0],
                         FAKE),
    TRIAL_VERDICT_ELIGIBLE,
)

# 7b. ...and the count it would have reported.
_control(
    "7b. removing the detector reports 0 fabrications -- CAUGHT",
    _EVAL_SRC,
    [(_DETECTOR_CALL, _NO_DETECTOR)],
    lambda m: run_stage5([entry(FAKE), entry(SENT)], nct_ids=(SENT,),
                         node=m.node_llm_classifier_evaluation
                         )[0]["hallucinated_trials"],
    0,
)

# 7c. THE CHUNK/NODE CONFUSION. Comparing against the whole node's sent set
#     instead of the chunk's accepts an answer to a question the call was not
#     asked -- and under a split every trial is then evaluated twice.
_control(
    "7c. comparing against the node's sent set instead of the chunk's is "
    "CAUGHT",
    _EVAL_SRC,
    [('        _objects, _cross_chunk, _fabricated = _partition_out_of_set(\n'
      '            _objects, _chunk_ids, _batch_ids)',
      '        _objects, _cross_chunk, _fabricated = _partition_out_of_set(\n'
      '            _objects, _batch_ids, _batch_ids)')],
    # PROBED ON THE CROSS-CHUNK COUNT AND THE EVALUATION COUNT, not on
    # hallucinated_trials: since item 3 split the buckets, the shipped code
    # ALSO reports 0 fabrications for this scenario, so that field can no
    # longer discriminate here and using it would be a control satisfied by
    # the correct code.
    lambda m: (lambda r, e: (sum(x.get("cross_chunk_count", 0) for x in
                                 log_records(e, "out_of_set_entry")),
                             len(r["evaluations"])))(
        *_run_split(m.node_llm_classifier_evaluation)[:2]),
    (0, _SPLIT_N * 2),
)

# 7c-positive. The same probe against an unplanted copy must report the drop
# and one evaluation per sent trial, or 7c would pass for a plant that failed
# to apply.
_control(
    "7c-positive. an unplanted copy drops the cross-chunk ids (7c is not "
    "vacuous)",
    _EVAL_SRC, [],
    lambda m: (lambda r, e: (sum(x.get("cross_chunk_count", 0) for x in
                                 log_records(e, "out_of_set_entry")),
                             len(r["evaluations"])))(
        *_run_split(m.node_llm_classifier_evaluation)[:2]),
    (_SPLIT_N, _SPLIT_N),
)

# 7c-bis. THE CLASSIFICATION SWAPPED: a cross-chunk id counted as a
#     fabrication. The drop is identical and the stored column is wrong, which
#     is the entire reason the two buckets exist.
_control(
    "7c-bis. counting a cross-chunk id as a fabrication is CAUGHT",
    _EVAL_SRC,
    [("        elif raw_id in batch_ids:\n"
      "            cross_chunk.append(_out_of_set_label(raw_id))",
      "        elif raw_id in batch_ids:\n"
      "            fabricated.append(_out_of_set_label(raw_id))")],
    lambda m: _run_split(m.node_llm_classifier_evaluation)[0][
        "hallucinated_trials"],
    _SPLIT_N,
)

# 7d. The unhashable guard, removed. `[] in {...}` raises, and the raise is out
#     through graph.invoke, which wraps nothing.
_control(
    "7d. dropping the isinstance guard restores the unhashable-id crash "
    "-- CAUGHT",
    _EVAL_SRC,
    [("        if not isinstance(raw_id, str):\n"
      "            fabricated.append(_out_of_set_label(raw_id))\n"
      "        elif raw_id in chunk_ids:",
      "        if raw_id in chunk_ids:")],
    lambda m: run_stage5([_UNHASHABLE_ENTRY], nct_ids=(SENT,),
                         node=m.node_llm_classifier_evaluation)[0].get("raised"),
    "TypeError",
)

# 7d-positive. The shipped module must survive the SAME entry, or 7d would be
# reporting a raise that the fix does not actually prevent.
check("7d-positive. the shipped module does not raise on that entry",
      run_stage5([_UNHASHABLE_ENTRY], nct_ids=(SENT,))[0].get("raised"), None)

# 7e. The log line, silenced. The behaviour is unchanged and the record is
#     gone, which is the one thing a behavioural probe cannot see.
_control(
    "7e. silencing the out_of_set_entry event is CAUGHT",
    _EVAL_SRC,
    [("def _out_of_set_label(raw_id) -> str:",
      "def _swallow(*a, **k):\n    return None\n\n\n"
      "def _out_of_set_label(raw_id) -> str:"),
     ("            cross_chunk_ids.extend(_cross_chunk)\n"
      "            log.warning(",
      "            cross_chunk_ids.extend(_cross_chunk)\n"
      "            _swallow(")],
    lambda m: len(log_records(
        run_stage5([entry(FAKE), entry(SENT)], nct_ids=(SENT,),
                   node=m.node_llm_classifier_evaluation)[1],
        "out_of_set_entry")),
    0,
)

# 7e-dup. The duplicate event, silenced. Same shape as 7e and it needs its own
#     plant: the two events are emitted at different call sites.
_control(
    "7e-dup. silencing the duplicate_answers event is CAUGHT",
    _EVAL_SRC,
    [("def _out_of_set_label(raw_id) -> str:",
      "def _swallow(*a, **k):\n    return None\n\n\n"
      "def _out_of_set_label(raw_id) -> str:"),
     ('            log.warning("the model returned more than one evaluation ',
      '            _swallow("the model returned more than one evaluation ')],
    lambda m: len(log_records(
        run_stage5([entry(SENT), entry(SENT)], nct_ids=(SENT,),
                   node=m.node_llm_classifier_evaluation)[1],
        "duplicate_answers")),
    0,
)

# ── THE DUPLICATE POLICY (item 2) ─────────────────────────────────────────

# 7p. THE DEFECT ITSELF, restored: no collapse at all. One trial leaves the
#     stage twice, which is two trial_matches rows and an over-counted
#     candidates_evaluated. This is exactly the state the old PRE-EXISTING pin
#     in section 3 recorded.
_COLLAPSE_CALL = "        _objects, _collapsed = _collapse_duplicate_entries(_objects)"
_control(
    "7p. removing the collapse lets one trial leave the stage twice -- CAUGHT",
    _EVAL_SRC,
    [(_COLLAPSE_CALL, "        _collapsed = []")],
    lambda m: ids_of(run_stage5([entry(SENT), entry(SENT)], nct_ids=(SENT,),
                                node=m.node_llm_classifier_evaluation)[0]),
    [SENT, SENT],
)

# 7q. THE CONFLICT ARM, made to pick a winner. The model said both "eligible"
#     and "not_eligible" about this trial and the run now reports one of them.
_control(
    "7q. letting a conflicting duplicate keep the first answer is CAUGHT",
    _EVAL_SRC,
    [("            collapsed.append({\"nct_id\": nct_id,\n"
      "                              \"case\": DUPLICATE_CASE_CONFLICTING,\n"
      "                              \"count\": len(entries)})",
      "            collapsed.append({\"nct_id\": nct_id,\n"
      "                              \"case\": DUPLICATE_CASE_CONFLICTING,\n"
      "                              \"count\": len(entries)})\n"
      "            kept.append(entries[0])")],
    lambda m: verdict_of(run_stage5(
        [entry(SENT, "eligible", inclusion=[crit("met")]),
         entry(SENT, "not_eligible", inclusion=[crit("not_met")])],
        nct_ids=(SENT,), node=m.node_llm_classifier_evaluation)[0], SENT),
    TRIAL_VERDICT_ELIGIBLE,
)

# 7r. THE COMPARISON MADE LITERAL. On the raw label, "Eligible" beside
#     "eligible" becomes a contradiction and a perfectly answered trial is
#     recorded as not evaluable -- a false non-evaluation, which is the
#     opposite failure from 7q and just as wrong.
_control(
    "7r. comparing raw labels instead of normalized verdicts is CAUGHT",
    _EVAL_SRC,
    [("        verdicts = {normalize_trial_verdict(e.get(\"eligible\"))[0]\n"
      "                    for e in entries}",
      "        verdicts = {repr(e.get(\"eligible\")) for e in entries}")],
    lambda m: verdict_of(run_stage5(
        [entry(SENT, "eligible", inclusion=[crit("met")]),
         entry(SENT, "Eligible", inclusion=[crit("met")])],
        nct_ids=(SENT,), node=m.node_llm_classifier_evaluation)[0], SENT),
    TRIAL_VERDICT_NOT_EVALUABLE,
)

# 7s. The conflicting trial's replacement entry, dropped. Nothing is kept and
#     nothing replaces it, so the reconciliation picks the trial up and calls
#     it OMITTED -- a false statement: the model answered, twice.
_control(
    "7s. dropping the replacement entry mislabels a contradiction as an "
    "omission -- CAUGHT",
    _EVAL_SRC,
    [("            unevaluable.extend(\n"
      "                _unevaluable_entry(_trial_by_id[nct_id],\n"
      "                                   NOT_EVALUABLE_CONFLICTING_DUPLICATES)\n"
      "                for nct_id in _conflicting\n"
      "            )",
      "            pass")],
    lambda m: reason_of(run_stage5(
        [entry(SENT, "eligible", inclusion=[crit("met")]),
         entry(SENT, "not_eligible", inclusion=[crit("not_met")])],
        nct_ids=(SENT,), node=m.node_llm_classifier_evaluation)[0], SENT),
    NOT_EVALUABLE_MODEL_OMITTED,
)

# 7f. The per-trial stamp, removed: a stored row can no longer say it was
#     checked.
_control(
    "7f. dropping the per-trial stamp leaves trial_matches.hallucinated NULL "
    "-- CAUGHT",
    _EVAL_SRC,
    [("    for _e in evaluations:\n"
      "        _e[\"hallucinated\"] = HALLUCINATION_CHECKED_CLEAN",
      "    pass")],
    lambda m: [e.get("hallucinated") for e in run_stage5(
        [entry(SENT)], nct_ids=(SENT,),
        node=m.node_llm_classifier_evaluation)[0]["evaluations"]],
    [None],
)

# 7g. The count, defaulted in the provenance block. NULL and 0 stop being
#     distinguishable, which is the whole point of the column.
_control(
    "7g. defaulting hallucinated_trials to 0 in _pipeline_provenance is CAUGHT",
    _TERMINAL_SRC,
    [('        "hallucinated_trials": state.get("hallucinated_trials"),',
      '        "hallucinated_trials": state.get("hallucinated_trials", 0),')],
    lambda m: m.node_no_candidates(make_state(()))["result"]["hallucinated_trials"],
    0,
)

# 7h. THE RANK, reverted to enumerate over the evaluations list.
_control(
    "7h. assigning trial_number by answer order is CAUGHT",
    _TERMINAL_SRC,
    [("    for e in evaluations:\n"
      "        nct_id = e.get(\"nct_id\", \"\")",
      "    for _rank_pos, e in enumerate(evaluations, start=1):\n"
      "        nct_id = e.get(\"nct_id\", \"\")"),
     ('        e["trial_number"] = _rank_by_nct.get(nct_id)',
      '        e["trial_number"] = _rank_pos')],
    lambda m: numbers(run_stage6(_rev, nct_ids=_RANKED, node=m.node_finalize)[0]),
    {"NCT00000001": 3, "NCT00000002": 2, "NCT00000003": 1},
)

# 7h-positive. Zero difference is also what a plant that failed to apply would
# produce, so the same probe against an UNPLANTED copy must give the shipped
# answer. Without it 7h would pass for a plant that did nothing.
_control(
    "7h-positive. an unplanted copy still ranks by the sent list (7h is not "
    "vacuous)",
    _TERMINAL_SRC, [],
    lambda m: numbers(run_stage6(_rev, nct_ids=_RANKED, node=m.node_finalize)[0]),
    {"NCT00000001": 1, "NCT00000002": 2, "NCT00000003": 3},
)

# 7j. A PARTIAL COUNT ON A FAILURE PATH. Stage 5's parse-error return is made
#     to carry the key, and the count then reaches the column from a run whose
#     response was never fully compared against the candidate set -- a 0 that
#     asserts a check nobody completed.
_control(
    "7j. a count on Stage 5's parse-error return is CAUGHT",
    _EVAL_SRC,
    [('            error_msg = f"GPT-4o JSON parse error (attempt {retry_count + 1}): {str(e)}"',
      '            error_msg = f"GPT-4o JSON parse error (attempt {retry_count + 1}): {str(e)}"\n'
      '            _partial_count = len(hallucinated_ids)'),
     ('                "llm_classifier_raw_response": chunk_text,',
      '                "llm_classifier_raw_response": chunk_text,\n'
      '                "hallucinated_trials": _partial_count,')],
    lambda m: run_stage5("{{{ not json", nct_ids=(SENT,), raw=True,
                         node=m.node_llm_classifier_evaluation
                         )[0].get("hallucinated_trials", "<absent>"),
    0,
)

# 7j-positive. The same probe against an unplanted copy must find the key
# ABSENT, or 7j would be reporting a value the shipped module also produces.
_control(
    "7j-positive. the shipped parse-error return carries no count (7j is not "
    "vacuous)",
    _EVAL_SRC, [],
    lambda m: run_stage5("{{{ not json", nct_ids=(SENT,), raw=True,
                         node=m.node_llm_classifier_evaluation
                         )[0].get("hallucinated_trials", "<absent>"),
    "<absent>",
)

# 7i. The reconciliation's rank, under the old rule: a trial the model never
#     mentioned scores 0.0, sorts last, and is numbered last.
_control(
    "7i. under the old rule an omitted top-ranked trial is numbered last "
    "-- CAUGHT",
    _TERMINAL_SRC,
    [("    for e in evaluations:\n"
      "        nct_id = e.get(\"nct_id\", \"\")",
      "    for _rank_pos, e in enumerate(evaluations, start=1):\n"
      "        nct_id = e.get(\"nct_id\", \"\")"),
     ('        e["trial_number"] = _rank_by_nct.get(nct_id)',
      '        e["trial_number"] = _rank_pos')],
    lambda m: numbers(run_stage6(_omit, nct_ids=_RANKED,
                                 node=m.node_finalize)[0])["NCT00000001"] == 1,
    False,
)


# ===========================================================================
# SECTION 8 -- nothing on disk was touched
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 8 -- the shipped sources are byte-identical")
print("=" * 75)

for _path, _sha in _SHA_BEFORE.items():
    check(f"{os.path.basename(_path)} was never edited on disk",
          _sha256_of(_path), _sha)
check("non-degeneracy: the two baseline hashes are distinct",
      len(set(_SHA_BEFORE.values())), 2)
check("...and section 7 really did plant something",
      _CONTROL_SEQ[0] > 0, True)

shutil.rmtree(_TMP, ignore_errors=True)
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

print("\n" + "=" * 75)

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

print(f"RESULTS: {_RESULTS['passed']} passed, {_RESULTS['failed']} failed")
print("=" * 75)
if _FAILURES:
    print("\nFAILURES:")
    for _f in _FAILURES:
        print(f"  - {_f}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


# ------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Aug  9 2026

@author: ramyalsaffar
"""
