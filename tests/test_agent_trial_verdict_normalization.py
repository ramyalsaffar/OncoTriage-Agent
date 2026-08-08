# Stage 5: the trial-level verdict, and entries that are not objects
###################################################################

"""
Trial Verdict Normalization Test

TWO DEFECTS IN ``node_gpt4o_evaluation``'s post-processing loop, both about an
answer the model did not give.

1. AN UNRECOGNISED TRIAL-LEVEL VERDICT WAS RECORDED AS A REJECTION. The loop
   opened with ``if eval_result.get("eligible") not in _TRIAL_LEVEL_LABELS:
   eval_result["eligible"] = "not_eligible"`` -- so a verdict that was missing,
   misspelled or outside the vocabulary became a statement that this trial
   assessed the patient and turned them down. Every other unreadable answer in
   the same file resolves to "not evaluated" and says why: Step 2 for a trial
   returned with no criteria, Step 3's remap branch for a rejection whose every
   disqualifier was out of vocabulary, ``_normalize_arm`` for a criterion
   status. This one line went the other way, and the zero-criteria branch
   rescued only the entries that had no criteria at all -- an entry WITH
   criteria kept the fabricated rejection and flowed into the patient's
   near-miss list.

   IT WAS WORSE THAN A MISLABEL, and that was found by running rather than by
   reading. ``node_finalize`` has always carried a six-entry synonym map for
   exactly this -- boolean ``True``, ``"Eligible"``, ``"yes"`` -- and Stage 5
   runs first, so the clobber destroyed precisely the values that map existed
   to rescue and the map could never be reached to disagree. Measured on the
   shipped code: ``True`` -> ``not_eligible`` -> near_misses.

2. A TOP-LEVEL ENTRY THAT IS NOT AN OBJECT CRASHED THE RUN. The response was
   validated as a LIST and its MEMBERS were not, so a list holding a bare NCT
   id string, a number, a null or a nested list reached the metadata-enrichment
   loop and raised ``AttributeError: 'str' object has no attribute 'get'``.
   Nothing catches it -- ``graph.invoke`` wraps nothing -- so one malformed
   element cost the whole patient, including every well-formed entry beside it
   in the same response.

WHAT THE FIX DOES NOT DO. It does not guess. An unresolvable label yields no
verdict from the normalizer at all (``None``), and the POLICY -- what to do
with an uninterpretable answer -- is applied at the call site, where the
criteria are in scope. A non-object entry is DROPPED, never repaired and never
turned into a verdict of any kind: it carries no nct_id, so there is nothing to
attribute one to, and the trial it may have been meant to answer for is picked
up by the reconciliation block at the end of the node, by nct_id.

THE ONE DECISION THAT IS NOT IN THE BRIEF, argued in section 6 and controlled
in 7f. Step 3's disqualification check OUTRANKS an unrecognised label. If the
model wrote an unreadable summary but marked an inclusion criterion "not_met",
the rejection stands on that criterion. Recording such a trial as "not
evaluated" would delete a stated failure and hand a clinician a candidate the
model had already disqualified -- the same fabrication, pointing the other way.
The unreadable label is still recorded, in ``verdict_normalizations`` rather
than in ``unevaluable_trials``, because the latter feeds a log line that says
"these are not rejections" and this entry is one.

MEASURED AGAINST THE PRODUCTION DATABASE, 2026-08-08, read-only: of 12,862
stored evaluations, 43 (0.334%) are ``not_eligible`` with criteria present and
no surviving disqualifier -- the only stored population the changed line can
reach, and an UPPER BOUND, since the raw label is not a column. All 43 carry
the model's own "Known disqualifier: ..." explanation, so none of them shows
the signature of a fabricated rejection (which keeps the model's positive
explanation). ZERO stored evaluations are attributable to this defect. The
model's out-of-vocabulary rate is not zero, though: 212 stored criterion
entries carry an exclusion-arm status on an inclusion criterion.

NO NETWORK, NO KEYS, NO SPEND, NO GIT HISTORY, NO CORPUS. Every model response
here is a literal built in this file and served by a stub installed through
``oncotriage/agent/deps.py``. NOT in tests/run_serial_tests.py's collision
matrix: it writes nothing anywhere -- every plant goes into an in-memory copy,
with both source files hashed before any plant and compared at the end -- and
the two files it reads are written by neither of the suite's two writers.

    python tests/test_agent_trial_verdict_normalization.py
"""

import contextlib
import hashlib
import io
import json
import os
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
from oncotriage.agent import state as _state_module
from oncotriage.agent import terminal as _terminal_module
from oncotriage.agent.evaluation import (
    MALFORMED_EVALUATION_ENTRIES,
    UNEVALUABLE_UNRECOGNIZED_VERDICT,
    node_gpt4o_evaluation,
)
from oncotriage.agent.state import (
    TRIAL_VERDICT_ELIGIBLE,
    TRIAL_VERDICT_NOT_ELIGIBLE,
    TRIAL_VERDICT_NOT_EVALUABLE,
    TRIAL_VERDICTS,
    VERDICT_SOURCE_CANONICAL,
    VERDICT_SOURCE_NORMALIZED,
    VERDICT_SOURCE_UNRECOGNIZED,
    VERDICT_SOURCES,
    normalize_trial_verdict,
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


# The modules under test, located from their OWN __file__ rather than from this
# test's directory, so a future move of either cannot silently point the plants
# at a same-named copy.
_EVAL_SRC = os.path.abspath(_evaluation_module.__file__)
_TERMINAL_SRC = os.path.abspath(_terminal_module.__file__)
_STATE_SRC = os.path.abspath(_state_module.__file__)


def _sha256_of(path):
    return hashlib.sha256(
        open(path, encoding="utf-8").read().encode()).hexdigest()


# Taken before any plant runs, so the restore assertion in section 8 compares
# against a real baseline rather than against itself.
_SHA_BEFORE = {p: _sha256_of(p) for p in (_EVAL_SRC, _TERMINAL_SRC, _STATE_SRC)}


class _PlantFailed(Exception):
    """A plant that did not apply or did not compile. Never escapes a check."""


def _plant(path, name, subs):
    """Exec an in-memory COPY of `path` with `subs` applied.

    Raises _PlantFailed -- never SyntaxError -- so a malformed plant is a
    RECORDED failure instead of a traceback hiding every check below it.
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
    "patient_id": "verdict-normalization-patient",
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


class _StubMessage:
    def __init__(self, content):
        self.content = content


class _StubChoice:
    def __init__(self, content):
        self.message = _StubMessage(content)
        self.finish_reason = "stop"


class _StubUsage:
    prompt_tokens = 1000
    completion_tokens = 200


class _StubResponse:
    def __init__(self, content):
        self.choices = [_StubChoice(content)]
        self.usage = _StubUsage()
        # None means "the response carried no model field", which
        # node_gpt4o_evaluation handles explicitly and which keeps
        # MatchingModelMismatchError out of a test that is not about it.
        self.model = None


class StubOpenAI:
    """Serves one chosen JSON payload. No network, no key, no spend."""

    def __init__(self, payload):
        self._payload = json.dumps(payload)
        self.calls = 0
        self.chat = self
        self.completions = self

    def create(self, model, messages, **kwargs):
        self.calls += 1
        return _StubResponse(self._payload)


# A RAISE IS AN OUTCOME, NOT A REASON TO ABORT, and this file shipped the
# opposite twice before a revert harness found it. Reverting the non-object
# drop makes Stage 5 raise AttributeError -- which is precisely what section 4
# exists to catch -- and with a bare call that raise escaped through check()'s
# argument list, so the run died at module level and reported one traceback
# where it owed 149 results. Reverting the bool test did the same with a
# TypeError out of an unhashable dict key. The same defect has now been fixed
# in tests/test_storage_query_layer.py, tests/test_dashboard_reproducibility_tab.py,
# tests/test_docker_qdrant_override_and_readiness.py and
# tests/test_agent_age_units_and_sex_filter.py.
#
# So the two drivers below never propagate: they return a result-shaped
# stand-in carrying `raised`, which makes every downstream check FAIL with a
# named exception instead of taking the file down.

def _raised_result(exc):
    return {"evaluations": [], "raised": type(exc).__name__,
            "cross_vocab_remaps": f"raised {type(exc).__name__}"}


def _raised_final(exc):
    return {"matches": [], "near_misses": [], "not_evaluable": [],
            "raised": type(exc).__name__}


def norm(raw):
    """normalize_trial_verdict, with a raise converted into a value."""
    try:
        return normalize_trial_verdict(raw)
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        return f"raised {type(exc).__name__}"


def run_stage5(payload, nct_ids=("NCT00000001",), node=None):
    """Drive Stage 5 with a stubbed model. Returns (result, stderr_text)."""
    node = node or node_gpt4o_evaluation
    state = {
        "patient_data": PATIENT,
        "filtered_trials": [{"trial": trial(n), "rerank_score": 5.0,
                             "rerank_score_raw": 5.0} for n in nct_ids],
        "gpt4o_retries": 0,
        "mesh_filter_applied": True,
        "mesh_filter_skip_reason": "applied",
        "stage_timings": {},
    }
    saved = deps.set_overrides({"openai_client": StubOpenAI(payload)})
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            result = node(state)
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        result = _raised_result(exc)
    finally:
        deps.restore_overrides(saved)
    return result, err.getvalue()


def run_stage6(evaluations, nct_ids=("NCT00000001",), node=None):
    """Drive Stage 6 over a chosen evaluation list."""
    node = node or node_finalize
    state = {
        "patient_data": PATIENT, "evaluations": evaluations,
        "filtered_trials": [{"trial": trial(n), "rerank_score": 5.0,
                             "rerank_score_raw": 5.0} for n in nct_ids],
        "stage_timings": {},
    }
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            out = node(state)["result"]
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        out = _raised_final(exc)
    return out, err.getvalue()


def log_records(stderr_text, event=None):
    """Every structured record on the captured stream, optionally by event.

    The audit lists this file asserts on are function locals whose only
    consumer is a log line, so the log IS the observation point -- and reading
    it exercises the real emission path rather than a private variable.
    """
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

    NEVER ``records[0][key]``. A defect that stops a record being emitted is
    exactly what these checks exist to catch, and a bare index turns that into
    an IndexError at module level -- the run then reports one traceback where
    it owed 155 results. Measured, not reasoned about: the first version of
    this file did index bare, and reverting the non-object drop aborted it at
    section 4. tests/test_storage_query_layer.py,
    tests/test_dashboard_reproducibility_tab.py and
    tests/test_docker_qdrant_override_and_readiness.py each had to fix the
    same shape.
    """
    if not records:
        return "<no such record>"
    return records[0].get(key, "<no such field>")


def verdict_of(result, nct_id="NCT00000001"):
    for e in result["evaluations"]:
        if e.get("nct_id") == nct_id:
            return e.get("eligible")
    return "<absent>"


def bucket_of(final_result, nct_id="NCT00000001"):
    for name in ("matches", "near_misses", "not_evaluable"):
        if any(e.get("nct_id") == nct_id for e in final_result[name]):
            return name
    return "<absent>"


def entry(nct_id, eligible, inclusion=(), exclusion=(), explanation="text",
          omit_verdict=False):
    """One evaluation entry as the model returns it."""
    payload = {
        "nct_id": nct_id, "match_score": 0.5, "explanation": explanation,
        "inclusion_criteria": list(inclusion),
        "exclusion_criteria": list(exclusion),
    }
    if not omit_verdict:
        payload["eligible"] = eligible
    return payload


# ===========================================================================
# SECTION 1 -- normalize_trial_verdict, as a unit
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 1 -- normalize_trial_verdict: the vocabulary and the parser")
print("=" * 75)

check("the vocabulary is the three trial-level labels, in report order",
      TRIAL_VERDICTS,
      ("eligible", "not_eligible", "not_evaluable"))
check("VERDICT_SOURCES is closed and a caller may branch on it exhaustively",
      VERDICT_SOURCES,
      (VERDICT_SOURCE_CANONICAL, VERDICT_SOURCE_NORMALIZED,
       VERDICT_SOURCE_UNRECOGNIZED))

for _label in TRIAL_VERDICTS:
    check(f"canonical {_label!r} passes through unchanged and says so",
          norm(_label), (_label, VERDICT_SOURCE_CANONICAL))

# Case and whitespace are PARSING, not guessing: the same token.
check("'Eligible' is the same token as 'eligible'",
      norm("Eligible"),
      (TRIAL_VERDICT_ELIGIBLE, VERDICT_SOURCE_NORMALIZED))
check("'  NOT_ELIGIBLE  ' strips and folds",
      norm("  NOT_ELIGIBLE  "),
      (TRIAL_VERDICT_NOT_ELIGIBLE, VERDICT_SOURCE_NORMALIZED))
check("'Not_Evaluable' folds",
      norm("Not_Evaluable"),
      (TRIAL_VERDICT_NOT_EVALUABLE, VERDICT_SOURCE_NORMALIZED))

# The four synonyms, adopted verbatim from the map node_finalize has carried
# for the whole life of the pipeline. Nothing new is invented here.
for _raw, _want in (("true", TRIAL_VERDICT_ELIGIBLE),
                    ("false", TRIAL_VERDICT_NOT_ELIGIBLE),
                    ("yes", TRIAL_VERDICT_ELIGIBLE),
                    ("no", TRIAL_VERDICT_NOT_ELIGIBLE),
                    ("TRUE", TRIAL_VERDICT_ELIGIBLE),
                    ("Yes", TRIAL_VERDICT_ELIGIBLE)):
    check(f"synonym {_raw!r} -> {_want}",
          norm(_raw), (_want, VERDICT_SOURCE_NORMALIZED))

check("boolean True -> eligible", norm(True),
      (TRIAL_VERDICT_ELIGIBLE, VERDICT_SOURCE_NORMALIZED))
check("boolean False -> not_eligible", norm(False),
      (TRIAL_VERDICT_NOT_ELIGIBLE, VERDICT_SOURCE_NORMALIZED))

# THE bool/int COLLISION. `True` and `1` are the same dict key in Python, so a
# single map holding both bool and string keys answers for the integer 1 as
# though the model had written `true`. The bool test runs first and the
# integers never reach a lookup.
check("the integer 1 is NOT read as boolean True",
      norm(1), (None, VERDICT_SOURCE_UNRECOGNIZED))
check("the integer 0 is NOT read as boolean False",
      norm(0), (None, VERDICT_SOURCE_UNRECOGNIZED))

for _raw in ("elligible", "maybe", "", "   ", "unknown", "eligible?",
             "not eligible", None, 3.5, [], {}, ["eligible"]):
    check(f"unresolvable {_raw!r} yields NO verdict",
          norm(_raw), (None, VERDICT_SOURCE_UNRECOGNIZED))

# Non-degeneracy: the function can return each of the three sources, so the
# assertions above are not all reading one branch.
_sources_seen = {norm(r)[1]
                 for r in ("eligible", "Eligible", "elligible")}
check("all three sources are reachable (the checks above are not degenerate)",
      sorted(_sources_seen), sorted(VERDICT_SOURCES))


# ===========================================================================
# SECTION 2 -- Stage 5: a well-formed response is untouched
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 2 -- valid entries are untouched")
print("=" * 75)

_valid_eligible, _ = run_stage5([entry("NCT00000001", "eligible",
                                       inclusion=[crit("met")],
                                       exclusion=[crit("not_violated")])])
check("valid 'eligible' with all criteria confirmed stays eligible",
      verdict_of(_valid_eligible), TRIAL_VERDICT_ELIGIBLE)
check("...and its match_score is recomputed over applicable criteria",
      _valid_eligible["evaluations"][0]["match_score"], 1.0)
check("...and it lands in matches",
      bucket_of(run_stage6(_valid_eligible["evaluations"])[0]), "matches")

_valid_reject, _ = run_stage5([entry("NCT00000001", "not_eligible",
                                     inclusion=[crit("not_met")])])
check("valid 'not_eligible' over a real disqualifier stays not_eligible",
      verdict_of(_valid_reject), TRIAL_VERDICT_NOT_ELIGIBLE)
check("...and it lands in near_misses",
      bucket_of(run_stage6(_valid_reject["evaluations"])[0]), "near_misses")

_valid_uneval, _ = run_stage5([entry("NCT00000001", "not_evaluable",
                                     inclusion=[crit("not_evaluable")])])
check("valid 'not_evaluable' with criteria stays not_evaluable",
      verdict_of(_valid_uneval), TRIAL_VERDICT_NOT_EVALUABLE)
check("...and it lands in not_evaluable",
      bucket_of(run_stage6(_valid_uneval["evaluations"])[0]), "not_evaluable")

# A well-formed response emits no verdict-normalization record at all.
_, _clean_err = run_stage5([
    entry("NCT00000001", "eligible", inclusion=[crit("met")]),
])
check("a well-formed response logs no verdict normalization",
      len(log_records(_clean_err, "verdict_normalization")), 0)
check("a well-formed response logs no not_evaluable record",
      len(log_records(_clean_err, "not_evaluable")), 0)
check("a well-formed response logs no malformed-entry record",
      len(log_records(_clean_err, "malformed_entry")), 0)


# ===========================================================================
# SECTION 3 -- an unrecognised verdict is NOT a rejection
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 3 -- an unrecognised verdict becomes not_evaluable")
print("=" * 75)

_misspelled, _mis_err = run_stage5([
    entry("NCT00000001", "elligible",
          inclusion=[crit("met")], exclusion=[crit("not_violated")],
          explanation="No known disqualifiers."),
])
check("a misspelled verdict over real, non-disqualifying criteria "
      "becomes not_evaluable",
      verdict_of(_misspelled), TRIAL_VERDICT_NOT_EVALUABLE)
check("...and it is kept OUT of near_misses",
      bucket_of(run_stage6(_misspelled["evaluations"])[0]), "not_evaluable")

_uneval_records = log_records(_mis_err, "not_evaluable")
check("...and it appears in the not_evaluable audit list", len(_uneval_records), 1)
check("...under its own reason",
      field(_uneval_records, "reason"),
      [UNEVALUABLE_UNRECOGNIZED_VERDICT])
check("...counted as exactly one trial",
      field(_uneval_records, "not_evaluable"), 1)

_norm_records = log_records(_mis_err, "verdict_normalization")
check("...and the label defect itself is recorded separately",
      len(_norm_records), 1)
check("...naming how the label failed to resolve",
      field(_norm_records, "reason"),
      [VERDICT_SOURCE_UNRECOGNIZED])
check("...and the TYPE of what the model wrote, which is the diagnosis",
      field(_norm_records, "error_type"), "str")

# The label TEXT is deliberately absent from the record: it is model output of
# unbounded content and `original_label` is not on LOGGABLE_FIELDS. Asserted
# rather than assumed, with a non-degeneracy probe that the record exists at all
# and that the text WAS distinctive enough to be found if it had leaked.
check("non-degeneracy: there is a record to inspect", len(_norm_records), 1)
check("the label text itself never reaches the structured record",
      any("elligible" in json.dumps(r) for r in _norm_records), False)
for _key in ("original_label", "original_type", "resolved_to"):
    check(f"...and {_key} is not a field of the record",
          _key in (_norm_records[0] if _norm_records else {}), False)

# A missing key is the same defect as a misspelled value.
_missing, _ = run_stage5([entry("NCT00000001", None, inclusion=[crit("met")],
                                omit_verdict=True)])
check("a MISSING verdict key becomes not_evaluable, not a rejection",
      verdict_of(_missing), TRIAL_VERDICT_NOT_EVALUABLE)

# The values node_finalize's map has always known, which Stage 5 used to
# destroy before that map could be reached.
for _raw, _want in (("Eligible", TRIAL_VERDICT_ELIGIBLE),
                    (True, TRIAL_VERDICT_ELIGIBLE),
                    ("yes", TRIAL_VERDICT_ELIGIBLE),
                    ("NOT_ELIGIBLE", TRIAL_VERDICT_NOT_ELIGIBLE)):
    _res, _ = run_stage5([entry("NCT00000001", _raw, inclusion=[crit("met")])])
    check(f"recoverable label {_raw!r} resolves to {_want}, not to a rejection",
          verdict_of(_res), _want)

# The zero-criteria branch, which rescued the verdict but recorded a label the
# model never wrote: original_label read "not_eligible", the value the clobber
# had just written.
_no_crit, _no_crit_err = run_stage5([entry("NCT00000001", "elligible")])
check("an unrecognised verdict with NO criteria is still not_evaluable",
      verdict_of(_no_crit), TRIAL_VERDICT_NOT_EVALUABLE)
_no_crit_records = log_records(_no_crit_err, "not_evaluable")
check("...and is recorded ONCE, under the label reason rather than twice",
      field(_no_crit_records, "not_evaluable"), 1)
check("...naming the label defect, not 'model returned no criteria'",
      field(_no_crit_records, "reason"),
      [UNEVALUABLE_UNRECOGNIZED_VERDICT])

# A recognised verdict with no criteria keeps the pre-existing reason: this
# path is unchanged and must stay unchanged.
_val_no_crit, _val_err = run_stage5([entry("NCT00000001", "eligible")])
check("a RECOGNISED verdict with no criteria still reports the old reason",
      field(log_records(_val_err, "not_evaluable"), "reason"),
      ["model returned no criteria"])


# ===========================================================================
# SECTION 4 -- a top-level entry that is not an object
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 4 -- non-object entries are dropped, counted and logged")
print("=" * 75)

_GOOD = entry("NCT00000002", "eligible", inclusion=[crit("met")])

for _junk, _type_name in (("NCT00000001", "str"), (42, "int"),
                          ([{"nct_id": "x"}], "list"), (None, "NoneType"),
                          (3.5, "float"), (True, "bool")):
    _before = sum(MALFORMED_EVALUATION_ENTRIES.values())
    _res, _err = run_stage5([_junk, _GOOD],
                            nct_ids=("NCT00000001", "NCT00000002"))
    check(f"a {_type_name} entry does not raise", _res.get("raised"), None)
    check(f"...and the well-formed entry beside it still gets its verdict",
          verdict_of(_res, "NCT00000002"), TRIAL_VERDICT_ELIGIBLE)
    check(f"...the {_type_name} entry becomes no verdict of any kind",
          [e.get("eligible") for e in _res["evaluations"]
           if e.get("nct_id") not in ("NCT00000001", "NCT00000002")], [])
    check(f"...and MALFORMED_EVALUATION_ENTRIES counts it as {_type_name}",
          sum(MALFORMED_EVALUATION_ENTRIES.values()) - _before, 1)
    _mal = log_records(_err, "malformed_entry")
    check(f"...and one malformed-entry record names the type {_type_name}",
          (field(_mal, "error_type"), field(_mal, "count")),
          (_type_name, 1))

# THE TRIAL IS NOT LOST. A dropped fragment carries no nct_id, so nothing can
# be attributed to it -- but the trial it was sent for is still missing from
# the response, and the reconciliation block records that BY nct_id.
_recon, _recon_err = run_stage5(["NCT00000001", _GOOD],
                                nct_ids=("NCT00000001", "NCT00000002"))
check("the trial the dropped fragment was sent for is reconciled, not lost",
      verdict_of(_recon, "NCT00000001"), TRIAL_VERDICT_NOT_EVALUABLE)
check("...with the reason naming the model's omission",
      [e.get("not_evaluable_reason") for e in _recon["evaluations"]
       if e.get("nct_id") == "NCT00000001"],
      [_evaluation_module.NOT_EVALUABLE_MODEL_OMITTED])
check("...so every trial sent still leaves the stage exactly once",
      sorted(e.get("nct_id") for e in _recon["evaluations"]),
      ["NCT00000001", "NCT00000002"])
check("...and the reconciliation was announced",
      len(log_records(_recon_err, "reconciliation")), 1)

# Several at once, and a response that is ENTIRELY junk.
_before = sum(MALFORMED_EVALUATION_ENTRIES.values())
_all_junk, _junk_err = run_stage5(["a", 1, None], nct_ids=("NCT00000001",))
check("a response that is entirely non-objects counts every one of them",
      sum(MALFORMED_EVALUATION_ENTRIES.values()) - _before, 3)
check("...and yields no verdict from any of them",
      [e.get("eligible") for e in _all_junk["evaluations"]],
      [TRIAL_VERDICT_NOT_EVALUABLE])     # the reconciliation entry only
check("...reported as one record naming all three types",
      sorted(str(field(log_records(_junk_err, "malformed_entry"),
                      "error_type")).split(",")),
      ["NoneType", "int", "str"])

# The counter is keyed by type so a run can answer "of what shape".
check("the counter is keyed by JSON type name",
      sorted(MALFORMED_EVALUATION_ENTRIES) != [], True)
check("...and every key it holds is a type name that was actually served",
      sorted(set(MALFORMED_EVALUATION_ENTRIES)
             - {"str", "int", "list", "NoneType", "float", "bool"}), [])


# ===========================================================================
# SECTION 5 -- Stage 6 makes the same decision
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 5 -- node_finalize splits on the shared vocabulary")
print("=" * 75)

for _raw, _want_bucket in (("eligible", "matches"),
                           ("Eligible", "matches"),
                           (True, "matches"),
                           ("yes", "matches"),
                           ("not_eligible", "near_misses"),
                           ("no", "near_misses"),
                           (False, "near_misses"),
                           ("not_evaluable", "not_evaluable")):
    _fin, _ = run_stage6([{"nct_id": "NCT00000001", "eligible": _raw,
                           "match_score": 0.5}])
    check(f"Stage 6: {_raw!r} lands in {_want_bucket}",
          bucket_of(_fin), _want_bucket)

# The fall-through that used to send an unreadable label to near_misses.
_fin_bad, _fin_err = run_stage6([{"nct_id": "NCT00000001",
                                  "eligible": "elligible", "match_score": 0.5}])
check("Stage 6: an unresolvable label is not a rejection either",
      bucket_of(_fin_bad), "not_evaluable")
check("...and it is announced rather than absorbed",
      [(r.get("count"), r.get("error_type"))
       for r in log_records(_fin_err) if r.get("level") == "WARNING"
       and "unresolvable" in r.get("message", "")],
      [(1, "str")])

# On every path the pipeline actually takes, Stage 6's normalization is a
# no-op, because Stage 5 emits only the canonical three. That is the claim, and
# it is asserted over every response this file has served rather than over one:
# a comparison of Stage 6's output with its own input would be true by
# construction, since node_finalize mutates the entries in place.
_EVERY_STAGE5_VERDICT = set()
for _payload in (
    [entry("NCT00000001", "eligible", inclusion=[crit("met")])],
    [entry("NCT00000001", "elligible", inclusion=[crit("met")])],
    [entry("NCT00000001", True, inclusion=[crit("met")])],
    [entry("NCT00000001", "Eligible", inclusion=[crit("met")])],
    [entry("NCT00000001", None, omit_verdict=True)],
    [entry("NCT00000001", 42, inclusion=[crit("not_met")])],
    [entry("NCT00000001", "not_evaluable")],
    [entry("NCT00000001", ["eligible"], exclusion=[crit("violated")])],
):
    _r, _ = run_stage5(_payload)
    _EVERY_STAGE5_VERDICT.update(e.get("eligible") for e in _r["evaluations"])

check("every verdict Stage 5 emits is in the canonical vocabulary",
      sorted(_EVERY_STAGE5_VERDICT - set(TRIAL_VERDICTS)), [])
check("non-degeneracy: it emitted more than one distinct verdict",
      len(_EVERY_STAGE5_VERDICT) > 1, True)


# ===========================================================================
# SECTION 6 -- the disqualification check outranks an unreadable label
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 6 -- criteria are evidence; the label is a summary of them")
print("=" * 75)

_bad_label_disq, _bld_err = run_stage5([
    entry("NCT00000001", "elligible", inclusion=[crit("not_met")]),
])
check("an unreadable label over a criterion the model marked not_met "
      "is still a rejection",
      verdict_of(_bad_label_disq), TRIAL_VERDICT_NOT_ELIGIBLE)
check("...it lands in near_misses, where a clinician sees the stated failure",
      bucket_of(run_stage6(_bad_label_disq["evaluations"])[0]), "near_misses")
check("...it is NOT in the not_evaluable audit list, which says it holds "
      "no rejections",
      len(log_records(_bld_err, "not_evaluable")), 0)
check("...but the unreadable label is still recorded, in its own list",
      len(log_records(_bld_err, "verdict_normalization")), 1)

_bad_label_viol, _ = run_stage5([
    entry("NCT00000001", "elligible", exclusion=[crit("violated")]),
])
check("the same holds for a violated exclusion criterion",
      verdict_of(_bad_label_viol), TRIAL_VERDICT_NOT_ELIGIBLE)

# The complement, so the pair discriminates: same unreadable label, criteria
# that disqualify nobody.
check("non-degeneracy: the SAME label with non-disqualifying criteria is "
      "not_evaluable",
      verdict_of(_misspelled), TRIAL_VERDICT_NOT_EVALUABLE)


# ===========================================================================
# SECTION 7 -- CONTROLS. Every assertion above, shown to fail.
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 7 -- planted defects, each required to be caught")
print("=" * 75)

# The plants go into an in-memory COPY. Nothing on disk is touched; section 8
# hashes all three sources to say so.
#
# THE TRAP THAT MAKES A COPY-BASED CONTROL LIE, and it is asserted rather than
# reasoned about: a freshly exec'd copy of evaluation.py runs its own
# `from oncotriage.agent.state import ...`, which resolves to the LIVE, already
# correct state module. So a control must revert something in THIS file, and a
# control that reverted normalize_trial_verdict instead would leave the copy
# calling the fixed function and agreeing with the shipped module about
# everything.
_probe_module = _planted(_EVAL_SRC, [])
check("PRECONDITION: an exec'd copy of evaluation.py is wired to the LIVE "
      "state module, so a plant must be made in evaluation.py itself",
      _probe_module.normalize_trial_verdict is normalize_trial_verdict, True)
check("PRECONDITION: an unplanted copy agrees with the shipped module",
      verdict_of(run_stage5([entry("NCT00000001", "elligible",
                                   inclusion=[crit("met")])],
                            node=_probe_module.node_gpt4o_evaluation)[0]),
      TRIAL_VERDICT_NOT_EVALUABLE)

_CLOBBER = """        eval_result["eligible"] = (
            verdict if verdict is not None else TRIAL_VERDICT_NOT_EVALUABLE
        )"""

# 7a. The defect itself, restored: an unrecognised label becomes a rejection.
_control(
    "7a. the original clobber (unrecognised -> not_eligible) is CAUGHT",
    _EVAL_SRC,
    [(_CLOBBER,
      '        eval_result["eligible"] = (\n'
      '            verdict if verdict is not None else TRIAL_VERDICT_NOT_ELIGIBLE\n'
      '        )')],
    lambda m: verdict_of(run_stage5(
        [entry("NCT00000001", "elligible", inclusion=[crit("met")])],
        node=m.node_gpt4o_evaluation)[0]),
    TRIAL_VERDICT_NOT_ELIGIBLE,
)

# 7b. ...and the bucket it lands in, which is what a clinician reads.
_control(
    "7b. the original clobber puts it in near_misses -- CAUGHT",
    _EVAL_SRC,
    [(_CLOBBER,
      '        eval_result["eligible"] = (\n'
      '            verdict if verdict is not None else TRIAL_VERDICT_NOT_ELIGIBLE\n'
      '        )')],
    lambda m: bucket_of(run_stage6(run_stage5(
        [entry("NCT00000001", "elligible", inclusion=[crit("met")])],
        node=m.node_gpt4o_evaluation)[0]["evaluations"])[0]),
    "near_misses",
)

# 7c. The audit append in the else branch, deleted. The verdict is still right;
#     the trial simply stops being reported, which is the silent-recovery shape
#     this project exists to remove.
_ELSE_APPEND = """            if verdict_unrecognized:
                unevaluable_trials.append({
                    "nct_id": nct_id,
                    "original_label": repr(raw_verdict)[:_MALFORMED_ENTRY_PREVIEW_LEN],
                    "reason": UNEVALUABLE_UNRECOGNIZED_VERDICT,
                })
            _record_zero_score(eval_result, inc, exc)"""
_control(
    "7c. dropping the audit append leaves the trial unreported -- CAUGHT",
    _EVAL_SRC,
    [(_ELSE_APPEND, "            _record_zero_score(eval_result, inc, exc)")],
    lambda m: len(log_records(run_stage5(
        [entry("NCT00000001", "elligible", inclusion=[crit("met")])],
        node=m.node_gpt4o_evaluation)[1], "not_evaluable")),
    0,
)

# 7d. The zero-criteria append, deleted: the pre-fix behaviour, where the
#     rescued trial was recorded under a label the model never wrote.
_ZERO_CRIT = """            if verdict_unrecognized:
                unevaluable_trials.append({
                    "nct_id": nct_id,
                    "original_label": repr(raw_verdict)[:_MALFORMED_ENTRY_PREVIEW_LEN],
                    "reason": UNEVALUABLE_UNRECOGNIZED_VERDICT,
                })
            elif eval_result["eligible"] != TRIAL_VERDICT_NOT_EVALUABLE:"""
_control(
    "7d. losing the zero-criteria label reason is CAUGHT",
    _EVAL_SRC,
    [(_ZERO_CRIT, '            if eval_result["eligible"] != TRIAL_VERDICT_NOT_EVALUABLE:')],
    lambda m: len(log_records(run_stage5(
        [entry("NCT00000001", "elligible")],
        node=m.node_gpt4o_evaluation)[1], "not_evaluable")),
    0,
)

# 7e. The non-object partition, reverted to the original `extend(parsed)`.
_control(
    "7e. reverting the non-object drop restores the AttributeError -- CAUGHT",
    _EVAL_SRC,
    [("    objects = []\n    dropped = []",
      "    objects = list(parsed)\n    dropped = []\n    return objects, dropped\n"
      "    objects = []\n    dropped = []")],
    lambda m: run_stage5(["NCT00000001", _GOOD],
                         nct_ids=("NCT00000001", "NCT00000002"),
                         node=m.node_gpt4o_evaluation)[0].get("raised"),
    "AttributeError",
)

# 7f. THE DECISION IN SECTION 6, controlled: make the unreadable label outrank
#     the criteria, and the stated failure disappears from near_misses.
_control(
    "7f. letting an unreadable label outrank a not_met criterion is CAUGHT",
    _EVAL_SRC,
    [("        if has_not_met or has_violated:",
      "        if (has_not_met or has_violated) and not verdict_unrecognized:")],
    lambda m: verdict_of(run_stage5(
        [entry("NCT00000001", "elligible", inclusion=[crit("not_met")])],
        node=m.node_gpt4o_evaluation)[0]),
    TRIAL_VERDICT_NOT_EVALUABLE,
)

# 7g. The counter, silenced. The behaviour is unchanged and the record is gone,
#     which is the one thing a behavioural probe cannot see.
_control(
    "7g. silencing MALFORMED_EVALUATION_ENTRIES is CAUGHT",
    _EVAL_SRC,
    [("                MALFORMED_EVALUATION_ENTRIES[type(_entry).__name__] += 1",
      "                pass")],
    lambda m: (run_stage5([42, _GOOD], nct_ids=("NCT00000001", "NCT00000002"),
                          node=m.node_gpt4o_evaluation)
               and sum(m.MALFORMED_EVALUATION_ENTRIES.values())),
    0,
)

# 7g-positive. Zero is also what a counter that was never reached reports, so
# the control above needs the other arm: the SAME probe against an unplanted
# copy, whose own counter must move. Without it, 7g would pass for a plant that
# broke the drop entirely.
_control(
    "7g-positive. an unplanted copy's counter DOES move (7g is not vacuous)",
    _EVAL_SRC, [],
    lambda m: (run_stage5([42, _GOOD], nct_ids=("NCT00000001", "NCT00000002"),
                          node=m.node_gpt4o_evaluation)
               and sum(m.MALFORMED_EVALUATION_ENTRIES.values())),
    1,
)

# 7h. Stage 6's fall-through, restored.
_control(
    "7h. Stage 6's 'leave as-is' fall-through to near_misses is CAUGHT",
    _TERMINAL_SRC,
    [("        if verdict is None:\n"
      "            _unresolved_verdicts.append(type(raw).__name__)\n"
      "            verdict = TRIAL_VERDICT_NOT_EVALUABLE\n"
      "        e[\"eligible\"] = verdict",
      "        if verdict is None:\n"
      "            _unresolved_verdicts.append(type(raw).__name__)\n"
      "            verdict = raw\n"
      "        e[\"eligible\"] = verdict")],
    lambda m: bucket_of(run_stage6(
        [{"nct_id": "NCT00000001", "eligible": "elligible",
          "match_score": 0.5}], node=m.node_finalize)[0]),
    "near_misses",
)

# 7i. The parser, in state.py, made to guess. Probed directly: evaluation.py
#     binds the live function at ITS import, so this control cannot be observed
#     through a planted evaluation module -- which is the trap asserted above.
_control(
    "7i. a normalizer that defaults an unreadable label to a verdict is CAUGHT",
    _STATE_SRC,
    [("    return None, VERDICT_SOURCE_UNRECOGNIZED",
      "    return TRIAL_VERDICT_NOT_ELIGIBLE, VERDICT_SOURCE_UNRECOGNIZED")],
    lambda m: m.normalize_trial_verdict("elligible")[0],
    TRIAL_VERDICT_NOT_ELIGIBLE,
)

# 7j. The bool/int collision, reintroduced by testing str before bool.
_control(
    "7j. resolving the integer 1 as boolean True is CAUGHT",
    _STATE_SRC,
    [("    if isinstance(raw, bool):\n"
      "        return (TRIAL_VERDICT_ELIGIBLE if raw else TRIAL_VERDICT_NOT_ELIGIBLE,\n"
      "                VERDICT_SOURCE_NORMALIZED)",
      "    _legacy = {True: TRIAL_VERDICT_ELIGIBLE, False: TRIAL_VERDICT_NOT_ELIGIBLE}\n"
      "    if raw in _legacy:\n"
      "        return _legacy[raw], VERDICT_SOURCE_NORMALIZED")],
    lambda m: m.normalize_trial_verdict(1)[0],
    TRIAL_VERDICT_ELIGIBLE,
)

# 7k. NON-DEGENERACY OF THE CONTROLS THEMSELVES. Each plant above must change
#     something; a plant that applied but altered no behaviour would report
#     "caught" for a reason that has nothing to do with the check.
_control(
    "7k. non-degeneracy: the 7a plant leaves a VALID entry untouched",
    _EVAL_SRC,
    [(_CLOBBER,
      '        eval_result["eligible"] = (\n'
      '            verdict if verdict is not None else TRIAL_VERDICT_NOT_ELIGIBLE\n'
      '        )')],
    lambda m: verdict_of(run_stage5(
        [entry("NCT00000001", "eligible", inclusion=[crit("met")])],
        node=m.node_gpt4o_evaluation)[0]),
    TRIAL_VERDICT_ELIGIBLE,
)


# ===========================================================================
# SECTION 8 -- a well-formed response is bit-for-bit what it was
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 8 -- the pre-fix module and this one agree on valid input")
print("=" * 75)

# The pre-fix module is RECONSTRUCTED by reverting this pass's two behavioural
# lines in an in-memory copy -- not read out of git. A git blob would be the
# fixed module the moment this work is committed, which is the failure mode
# tests/test_storage_query_layer.py had to fix; and a shallow clone or an export
# has no history to read.
_PRE_FIX_SUBS = [
    (_CLOBBER,
     '        eval_result["eligible"] = (\n'
     '            verdict if verdict is not None else TRIAL_VERDICT_NOT_ELIGIBLE\n'
     '        )'),
    ("    objects = []\n    dropped = []",
     "    objects = list(parsed)\n    dropped = []\n    return objects, dropped\n"
     "    objects = []\n    dropped = []"),
]

try:
    _pre_fix = _planted(_EVAL_SRC, _PRE_FIX_SUBS)
except _PlantFailed as _exc:
    _pre_fix = None
    check(f"the pre-fix reconstruction applied [{_exc}]", "plant-failed", "ok")

_WELL_FORMED = [
    ("all confirmed", [entry("NCT00000001", "eligible",
                             inclusion=[crit("met")],
                             exclusion=[crit("not_violated")])]),
    ("an unmet inclusion", [entry("NCT00000001", "not_eligible",
                                  inclusion=[crit("not_met"), crit("met")])]),
    ("a violated exclusion", [entry("NCT00000001", "not_eligible",
                                    exclusion=[crit("violated")])]),
    ("model says not_evaluable", [entry("NCT00000001", "not_evaluable",
                                        inclusion=[crit("not_evaluable")])]),
    ("no criteria at all", [entry("NCT00000001", "eligible")]),
    ("a rejection with no surviving disqualifier",
     [entry("NCT00000001", "not_eligible", inclusion=[crit("met")])]),
    ("an out-of-vocabulary CRITERION label",
     [entry("NCT00000001", "not_eligible",
            inclusion=[crit("violated")])]),
    ("a not-applicable criterion",
     [entry("NCT00000001", "eligible",
            inclusion=[crit("met"), crit("met", "Not applicable: male")])]),
    ("an absent-data disqualification",
     [entry("NCT00000001", "not_eligible",
            inclusion=[crit("not_met", "not documented")])]),
    ("two trials at once",
     [entry("NCT00000001", "eligible", inclusion=[crit("met")]),
      entry("NCT00000002", "not_eligible", inclusion=[crit("not_met")])]),
]


def _shape(result):
    """Everything about a Stage 5 result a downstream consumer reads."""
    return [
        (e.get("nct_id"), e.get("eligible"), e.get("match_score"),
         e.get("score_confirmed"), e.get("score_denominator"),
         e.get("criteria_not_applicable"), e.get("not_evaluable_reason"),
         [c.get("status") for c in e.get("inclusion_criteria", [])],
         [c.get("status") for c in e.get("exclusion_criteria", [])])
        for e in sorted(result["evaluations"], key=lambda x: x.get("nct_id", ""))
    ]


if _pre_fix is not None:
    _differing = 0
    for _name, _payload in _WELL_FORMED:
        _ids = tuple(sorted(e["nct_id"] for e in _payload))
        _new, _ = run_stage5(_payload, nct_ids=_ids)
        _old, _ = run_stage5(_payload, nct_ids=_ids,
                             node=_pre_fix.node_gpt4o_evaluation)
        _same = _shape(_new) == _shape(_old)
        if not _same:
            _differing += 1
        check(f"well-formed: {_name} -- identical before and after", _same, True)
        check(f"well-formed: {_name} -- cross_vocab_remaps identical",
              _new["cross_vocab_remaps"], _old["cross_vocab_remaps"])
    check("no well-formed case moved", _differing, 0)

    # NON-DEGENERACY. The comparison must be able to FAIL, or "identical" is
    # a statement about the harness rather than about the code.
    _bad = [entry("NCT00000001", "elligible", inclusion=[crit("met")])]
    check("non-degeneracy: the SAME comparison separates the two modules on "
          "an unrecognised label",
          _shape(run_stage5(_bad)[0])
          == _shape(run_stage5(_bad, node=_pre_fix.node_gpt4o_evaluation)[0]),
          False)


# ===========================================================================
# SECTION 9 -- nothing on disk was touched
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 9 -- every plant was in memory")
print("=" * 75)

for _path in (_EVAL_SRC, _TERMINAL_SRC, _STATE_SRC):
    check(f"{os.path.basename(_path)} is byte-identical to its pre-run state",
          _sha256_of(_path), _SHA_BEFORE[_path])

# Non-degeneracy: the hashes are real and distinct, so the three assertions
# above are not three readings of one constant.
check("non-degeneracy: the three baseline hashes are distinct",
      len(set(_SHA_BEFORE.values())), 3)
check("non-degeneracy: a hash of a different byte string differs",
      _sha256_of(_EVAL_SRC) == hashlib.sha256(b"").hexdigest(), False)


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
Created on Sat Aug  8 2026

@author: ramyalsaffar
"""
