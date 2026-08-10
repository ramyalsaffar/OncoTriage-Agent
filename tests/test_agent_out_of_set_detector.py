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

   The entry is now DROPPED before enrichment, counted into
   ``inferences.hallucinated_trials``, and named in one structured log event.
   The displaced trial is left to the reconciliation -- section 3 proves that
   handoff happens rather than duplicating it -- and the comparison is against
   THE CHUNK's sent set, not the node's, which section 4 drives through a real
   proactive split.

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
    HALLUCINATION_CHECKED_CLEAN,
    NOT_EVALUABLE_MODEL_OMITTED,
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

_sent = {SENT, SENT_2}

_in, _out = _partition_out_of_set(
    [entry(SENT), entry(FAKE), entry(SENT_2)], _sent)
check("entries whose id was sent are kept, in order",
      [e["nct_id"] for e in _in], [SENT, SENT_2])
check("...and the one that was not is reported by id", _out, [FAKE])

check("an empty sent set rejects everything",
      _partition_out_of_set([entry(SENT)], set())[1], [SENT])
check("an empty response reports nothing",
      _partition_out_of_set([], _sent), ([], []))

# A MISSING id is out of set, and this is where such an entry now stops. Before
# it reached enrichment as "", matched no trial, kept no title or phase, and
# left the stage as a verdict about nothing.
check("an entry with no nct_id at all is out of set",
      _partition_out_of_set([entry(SENT, omit_id=True)], _sent)[1], ["<NoneType>"])

# THE UNHASHABLE CASE. `[] in {"a"}` raises TypeError, so without the isinstance
# test the detector added to stop a class of loss would itself take the whole
# patient's run down. Each is also, unambiguously, not one of the ids sent.
for _bad, _label in (([], "<list>"), ({}, "<dict>"), (42, "<int>"),
                     (None, "<NoneType>"), (3.5, "<float>"), (True, "<bool>")):
    _e = entry(SENT)
    _e["nct_id"] = _bad
    check(f"a {type(_bad).__name__} nct_id is out of set and does not raise",
          _partition_out_of_set([_e], _sent)[1], [_label])

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

# Non-degeneracy: the partition can put entries on both sides at once, so the
# assertions above are not all reading one branch.
check("non-degeneracy: one call can produce both a keep and a drop",
      (len(_in), len(_out)), (2, 1))


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
check("...naming the count and the offending id",
      (field(_ooset, "count"), field(_ooset, "nct_ids")), (1, [FAKE]))
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
      sorted(field(log_records(_af_err, "out_of_set_entry"), "nct_ids")),
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
      field(log_records(_sub_err, "out_of_set_entry"), "nct_ids"), [FAKE])
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
)
for _label, _payload in _INVARIANT_CASES:
    _r, _ = run_stage5(_payload, nct_ids=(SENT, SENT_2))
    check(f"invariant ({_label}): every sent id leaves exactly once, and no "
          f"unsent id ever does",
          ids_of(_r), [SENT, SENT_2])

# A DUPLICATED SENT ID IS NOT THE DETECTOR'S BUSINESS, AND THE MEASUREMENT
# BELOW RECORDS A PRE-EXISTING DEFECT RATHER THAN ASSERTING A FIX.
#
# The id WAS in the candidate set, so it is in set, and the detector reports 0.
# But nothing else deduplicates either: the reconciliation only asks whether
# each sent trial appears at least once, so a model that answers twice for one
# trial leaves TWO evaluations for it, two trial_matches rows, and a
# candidates_evaluated that over-counts. That is true of the shipped pipeline
# before this pass and is untouched by it -- fixing it is a decision about
# which of two verdicts for one trial wins, which is not this pass's -- so it
# is measured here rather than corrected, and the invariant loop above
# deliberately does not include the case.
_dup, _ = run_stage5([entry(SENT), entry(SENT)], nct_ids=(SENT,))
check("a duplicated SENT id is not counted as out of set",
      _dup["hallucinated_trials"], 0)
check("PRE-EXISTING, NOT FIXED HERE: a duplicated sent id leaves the stage "
      "twice",
      ids_of(_dup), [SENT, SENT])


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
# count would be 0 and every trial would be evaluated TWICE.
check("ids belonging to another chunk are out of set for the call that got "
      "them",
      _split["hallucinated_trials"], _SPLIT_N * (_stub.calls - 1))
check("...reported once per call",
      len(log_records(_split_err, "out_of_set_entry")), _stub.calls)
check("non-degeneracy: that count is not zero",
      _split["hallucinated_trials"] > 0, True)


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

_DETECTOR_CALL = """        _sent_ids = {t["trial"]["nct_id"] for t in chunk}
        _objects, _out_of_set = _partition_out_of_set(_objects, _sent_ids)"""

# 7a. THE DEFECT ITSELF, restored: no detector at all. The fabricated entry
#     flows on and becomes a verdict in the patient's results.
_control(
    "7a. removing the detector lets a fabricated entry become a verdict "
    "-- CAUGHT",
    _EVAL_SRC,
    [(_DETECTOR_CALL, "        _out_of_set = []")],
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
    [(_DETECTOR_CALL, "        _out_of_set = []")],
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
    [('        _sent_ids = {t["trial"]["nct_id"] for t in chunk}',
      '        _sent_ids = {t["trial"]["nct_id"] for t in trials}')],
    lambda m: (lambda r: (r["hallucinated_trials"],
                          len(r["evaluations"])))(
        _run_split(m.node_llm_classifier_evaluation)[0]),
    (0, _SPLIT_N * 2),
)

# 7d. The unhashable guard, removed. `[] in {...}` raises, and the raise is out
#     through graph.invoke, which wraps nothing.
_control(
    "7d. dropping the isinstance guard restores the unhashable-id crash "
    "-- CAUGHT",
    _EVAL_SRC,
    [("        if isinstance(raw_id, str) and raw_id in sent_ids:",
      "        if raw_id in sent_ids:")],
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
     ("            hallucinated_ids.extend(_out_of_set)\n"
      "            log.warning(",
      "            hallucinated_ids.extend(_out_of_set)\n"
      "            _swallow(")],
    lambda m: len(log_records(
        run_stage5([entry(FAKE), entry(SENT)], nct_ids=(SENT,),
                   node=m.node_llm_classifier_evaluation)[1],
        "out_of_set_entry")),
    0,
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


# ===========================================================================
# SUMMARY
# ===========================================================================

print("\n" + "=" * 75)
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
