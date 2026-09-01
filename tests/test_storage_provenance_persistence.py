# Stage 5 Normalizer Provenance Persistence Test
###############################################

"""Three facts Stage 5 computed and nothing could query, and the columns for them.

WHAT WAS LOST, AND WHERE
------------------------
``oncotriage/agent/evaluation.py``'s post-processing normalizer produces three
audit artifacts. Before the provenance pass:

  ``not_evaluable_reason``   was stamped on the in-memory entry and DROPPED AT
                             THE WRITE. The ``trial_matches`` INSERT named
                             nineteen columns and none of them was it, and
                             ``criterion_details`` json.dumps exactly
                             ``"inclusion"`` and ``"exclusion"``. The field was
                             present on the dict at the line that wrote the row.
  ``verdict_normalizations`` was a local list read by ONE log line and then
                             discarded. Not on the entry, not in the node's
                             return, not a state channel, not a column.
  ``label_remaps``           survived only as its own LENGTH, in
                             ``inferences.cross_vocab_remaps`` -- a count of
                             remap EVENTS for the whole run. Which trial each
                             belonged to, how many TRIALS carried one, and what
                             each row's status was BEFORE the rewrite were all
                             lost, because ``_normalize_arm`` rewrites
                             ``c["status"]`` IN PLACE.

WHAT THIS FILE HOLDS
--------------------
    1. THE MIGRATION IS ADDITIVE AND IDEMPOTENT ON BOTH TABLES, fresh and
       pre-migration, through the real ``initialize_database``.
    2. THE ROUND TRIP, through the real ``log_inference`` into a real SQLite
       file: a measured value, a measured 0, an absent key and an explicit
       None, for every new column, with NULL and 0 shown to be distinguishable
       in SQL rather than merely different in Python.
    3. THE PIPELINE STAMPS THEM, driven on a StateGraph over the REAL
       ``TrialMatchState`` with the REAL Stage 5 node and the REAL
       ``node_finalize``. NEGATIVE CONTROL: the identical run over a schema with
       the two new annotations removed loses both run-level counters, which is
       what says the channel declaration is doing the work -- LangGraph
       discards an undeclared key silently, and that is how four Stage 5 keys
       once shipped broken.
    4. THE MODEL CANNOT FORGE ANY OF THE FIVE MARKERS, asserted against the
       REAL response schema rather than assumed.
    5. A RUN THAT NEVER COMPLETED THE NORMALIZER STORES NULL, NEVER 0.
    6. THE SUM INVARIANT: the per-trial remap counts of one run sum to that
       run's ``cross_vocab_remaps``, end to end, checked in SQL.
    7. NO NEW KEY IS FIXTURE-COMPARED. ``build_deterministic_prefix`` is a
       closed enumeration; a key it does not name cannot move a replay.
    8. THE CAMPAIGN QUESTIONS, answered by the registry queries and by raw SQL
       against the database this file just wrote.
    9. TEN NEGATIVE CONTROLS, each shown to fire.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO CORPUS, NO GIT HISTORY, and
NOT in the collision matrix: every database is a temp file every call is pointed
at explicitly, the OpenAI client is a stand-in installed through
``oncotriage/agent/deps.py``, and the four repository files it READS --
``oncotriage/storage/database_logger.py``, ``oncotriage/agent/evaluation.py``,
``oncotriage/agent/response_schema.py`` and ``oncotriage/fixtures/capture.py`` --
are written by neither of the suite's two writers.

IT DOES EXEC: five controls plant into in-memory copies of
``database_logger.py`` and ``evaluation.py``. Argued at ``_EXEC_ALLOWLIST`` in
tests/test_package_invariants.py. Neither a real condition nor a git blob can
supply them: the plants are one-token edits INSIDE a function body to code that
exists at HEAD and nowhere else, so a blob of the revision before it does not
carry an inverted guard -- it carries no column at all.

Run from terminal:
    python tests/test_storage_provenance_persistence.py

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

# No local model is reached here, and the flag is set before the agent is
# imported anyway: a stand-in forgotten in a future edit becomes a named
# RuntimeError instead of a 110 MB download.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

import ast
import contextlib
import hashlib
import io
import json
import shutil
import sqlite3
import tempfile
import types
from pathlib import Path
from typing import Optional

from langgraph.graph import END, StateGraph

from oncotriage import paths as _paths
from oncotriage.agent import deps
from oncotriage.agent import evaluation as _ev
from oncotriage.agent import response_schema as _rs
from oncotriage.agent import state as _st
from oncotriage.agent import terminal as _tm
from oncotriage.agent.state import TrialMatchState
from oncotriage.fixtures import capture as _cap
from oncotriage.storage import database_logger as _dl
from oncotriage.storage import queries as _q


#------------------------------------------------------------------------------


# ===========================================================================
# MINIMAL ASSERTION HARNESS
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


def fail(label: str, detail: str) -> None:
    """Record a failure that is not an equality comparison."""
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {detail}")
    print(f"  FAIL  {label}")
    print(f"          {detail}")


def guarded(fn, *args, **kwargs):
    """Call into production code, turning ANY raise into a value check() fails on.

    NOT DEFENSIVE PADDING. Nine files in this suite have shipped the same
    defect: a bare call inside a ``check(...)`` argument, where a planted defect
    raises, the exception escapes while the argument is being evaluated, and the
    run reports ONE TRACEBACK where it owed a summary and N results. Section 9
    deliberately breaks things, so every driver goes through here.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                       # noqa: BLE001 -- reported
        return {"__raised__": f"{type(exc).__name__}: {exc}"}


def silence(fn, *args, **kwargs):
    """Run fn with BOTH output channels captured; return its value.

    The writer announces every ALTER TABLE and every row; this file migrates
    seven databases and drives five controls. Nothing suppressed is asserted on:
    every assertion reads the DATABASE or a returned dict.
    """
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        return guarded(fn, *args, **kwargs)


def digest(path):
    """sha256 of a file, or the string 'absent'."""
    if not os.path.exists(path):
        return "absent"
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def at(seq, index, default="<out of range>"):
    """seq[index] or a NAMED absence -- never an IndexError inside a check()."""
    try:
        return seq[index]
    except (IndexError, KeyError, TypeError):
        return default


def columns_of(db, table):
    """Column names of `table`, read read-only.

    A plain sqlite3.connect on an absent path CREATES the file, so a check
    written that way would bring its own subject into existence.
    """
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return f"unreadable: {exc}"


def rows(db, sql, params=()):
    """Every row of `sql` as a list of dicts. Read-only."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def one(db, sql, params=()):
    """The first row of `sql`, or a NAMED absence."""
    found = rows(db, sql, params)
    return found[0] if found else {"__no_row__": sql}


def trial_row(db, nct):
    return one(db, "SELECT * FROM trial_matches WHERE nct_id = ?", (nct,))


def exec_copy(source_path, module_name, package, mutate):
    """Exec a MUTATED in-memory copy of a package module.

    A real ModuleType, not a dict-backed stand-in: a function's globals ARE the
    dict it was exec'd into, so a throwaway namespace would leave every module
    constant unread. `mutate` asserts its own match count, so a plant that
    matched nothing raises here rather than producing a control that quietly
    agrees with the shipped code.
    """
    text = Path(source_path).read_text(encoding="utf-8")
    planted = mutate(text)
    if planted == text:
        raise AssertionError(f"{module_name}: the plant matched nothing")
    module = types.ModuleType(module_name)
    module.__file__ = source_path
    module.__package__ = package
    sys.modules[module_name] = module
    exec(compile(planted, source_path, "exec"), module.__dict__)
    return module


def sub(text, old, new, expect):
    """Replace, refusing a plant that did not match exactly `expect` times."""
    seen = text.count(old)
    if seen != expect:
        raise AssertionError(
            f"plant matched {seen} time(s), expected {expect}: {old[:70]!r}")
    return text.replace(old, new)


#------------------------------------------------------------------------------


# ===========================================================================
# FIXTURES
# ===========================================================================

_TMP = tempfile.mkdtemp(prefix="oncotriage-provenance-")

_DL_PY = os.path.abspath(_dl.__file__)
_EV_PY = os.path.abspath(_ev.__file__)
_RS_PY = os.path.abspath(_rs.__file__)
_CAP_PY = os.path.abspath(_cap.__file__)
_DIGESTS_BEFORE = {p: digest(p) for p in (_DL_PY, _EV_PY, _RS_PY, _CAP_PY)}

# Read ONCE, read-only, before anything runs.
_PROD_DB = _paths.inferences_path
_PROD_DIGEST_BEFORE = digest(_PROD_DB)

# The seven columns this file is about, with the SQL type each is declared as.
INFERENCE_COLUMNS = {
    "verdict_normalizations": "INTEGER",
    "remapped_trials":        "INTEGER",
}
TRIAL_COLUMNS = {
    "not_evaluable_reason":   "TEXT",
    "verdict_source":         "TEXT",
    "verdict_original_label": "TEXT",
    "verdict_original_type":  "TEXT",
    "criterion_remaps":       "INTEGER",
}

# The five keys the pipeline stamps, and the level each lives at.
TRIAL_MARKERS = ("not_evaluable_reason", "verdict_source",
                 "verdict_original_label", "verdict_original_type",
                 "criterion_remaps")
RUN_MARKERS = ("verdict_normalizations", "remapped_trials")
CRITERION_MARKER = _ev.LABEL_REMAP_FIELD


def fresh_db(name):
    """A path in the scratch directory, with the per-process memo cleared.

    _INITIALIZED_DATABASES is keyed on the absolute path, so a stale entry would
    make the next database skip initialization entirely and every assertion
    after it prove nothing.
    """
    path = os.path.join(_TMP, name)
    _dl._INITIALIZED_DATABASES.discard(os.path.abspath(path))
    return path


PATIENT = {
    "patient_id": "provenance-patient",
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


def result_dict(patient_id, **extra):
    """The minimum a terminal node emits that log_inference will accept."""
    base = {
        "patient_id": patient_id,
        "timestamp": "2026-08-21T00:00:00",
        "matching_model": "gpt-4o-2024-08-06",
        "llm_classifier_input_tokens": 1000,
        "llm_classifier_output_tokens": 200,
        "matches": [], "near_misses": [], "not_evaluable": [],
        "stage_timings": {},
    }
    base.update(extra)
    return base


def match_dict(nct, **extra):
    base = {"nct_id": nct, "title": f"Trial {nct}", "phase": "Phase 2",
            "eligible": "eligible", "match_score": 0.5, "assessment": "text",
            "inclusion_criteria": [], "exclusion_criteria": []}
    base.update(extra)
    return base


#------------------------------------------------------------------------------


# ===========================================================================
# THE STAND-IN STAGE 5 CLIENT
# ===========================================================================
#
# `model` is None on every response so the answering-model check
# (MatchingModelMismatchError) is skipped: it is a different mechanism with its
# own test and it raises OUT of the node, past every return this file is about.

class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content, finish_reason="stop"):
        self.message = _Msg(content)
        self.finish_reason = finish_reason


class _Usage:
    prompt_tokens = 1000
    completion_tokens = 200
    total_tokens = 1200


class _Resp:
    def __init__(self, content, finish_reason="stop"):
        self.choices = [_Choice(content, finish_reason)]
        self.usage = _Usage()
        self.model = None


class _Client:
    """Answers every chat.completions.create with one canned body."""

    def __init__(self, body, raises=None):
        outer = self

        class _CC:
            @staticmethod
            def create(**_kw):
                if raises is not None:
                    raise raises
                return _Resp(json.dumps(outer._body))

        class _Chat:
            completions = _CC()

        self._body = body
        self.chat = _Chat()


def trial_obj(nct):
    return {"trial": {"nct_id": nct, "title": f"Trial {nct}", "phase": "Phase 2",
                      "conditions": ["Breast Neoplasms"],
                      "eligibility": {"inclusion_criteria": ["adult"],
                                      "exclusion_criteria": ["pregnancy"]}},
            "rerank_score": 0.5, "rerank_score_raw": 0.5, "medcpt_score_max": 0.5}


def _unannotated(fn):
    """The same node with no first-parameter annotation.

    LANGGRAPH READS THE NODE CALLABLE'S ANNOTATION AND ADDS THAT SCHEMA'S
    CHANNELS TO THE GRAPH. Both real nodes are declared ``(state:
    TrialMatchState)``, so registering either on a graph built over a REDUCED
    schema silently reinstates every channel the reduction removed -- and the
    control would then report that an undeclared key is carried, which is the
    opposite of the truth. Measured by tests/test_agent_state_channel_coverage.py
    and adopted here rather than rediscovered.
    """
    def _node(state):
        return fn(state)
    _node.__name__ = getattr(fn, "__name__", "node")
    return _node


def run_pipeline(body, trials, schema=TrialMatchState, annotated=True,
                 raises=None):
    """Stage 5 -> node_finalize over `schema`. Returns the result dict."""
    node5 = _ev.node_llm_classifier_evaluation
    node6 = _tm.node_finalize
    if not annotated:
        node5, node6 = _unannotated(node5), _unannotated(node6)
    graph = StateGraph(schema)
    graph.add_node("evaluate", node5)
    graph.add_node("finalize", node6)
    graph.set_entry_point("evaluate")
    graph.add_edge("evaluate", "finalize")
    graph.add_edge("finalize", END)
    app = graph.compile()
    state = {"patient_data": dict(PATIENT),
             "filtered_trials": [trial_obj(n) for n in trials],
             "stage_timings": {}}
    with deps.override(deps.OPENAI_CLIENT, _Client(body, raises=raises)):
        out = silence(app.invoke, state)
    if isinstance(out, dict) and "__raised__" in out:
        return out
    return out.get("result", {"__no_result__": True})


def verdict_of(result, nct):
    """The evaluation entry for `nct` in a terminal result, or a named absence."""
    for bucket in ("matches", "near_misses", "not_evaluable"):
        for entry in result.get(bucket, []) or []:
            if entry.get("nct_id") == nct:
                return entry
    return {"__missing__": nct}


# The response body every section drives, and what each entry is FOR.
#   NCT-CANON   canonical label, no remap  -> source 'canonical', remaps 0
#   NCT-BOOL    boolean True label, one out-of-vocabulary criterion status
#               -> source 'normalized', original ('True', 'bool'), remaps 1
#   NCT-BAD     an unreadable label and no criteria at all
#               -> source 'unrecognized', ends not_evaluable
#   NCT-DROP    a criterion entry that is not an object at all
#               -> remaps 1 with NO row carrying the criterion marker
BODY = {"evaluations": [
    {"nct_id": "NCT-CANON", "eligible": "eligible", "match_score": 0.9,
     "assessment": "fine",
     "inclusion_criteria": [{"criterion": "adult", "patient_value": "63",
                             "status": "met"}],
     "exclusion_criteria": []},
    {"nct_id": "NCT-BOOL", "eligible": True, "match_score": 0.7,
     "assessment": "fine",
     "inclusion_criteria": [{"criterion": "ECOG 0-1", "patient_value": "1",
                             "status": "violated"}],
     "exclusion_criteria": []},
    {"nct_id": "NCT-BAD", "eligible": "MAYBE", "match_score": 0.1,
     "assessment": "fine", "inclusion_criteria": [], "exclusion_criteria": []},
    {"nct_id": "NCT-DROP", "eligible": "eligible", "match_score": 0.4,
     "assessment": "fine",
     "inclusion_criteria": ["a bare string, not an object",
                            {"criterion": "adult", "patient_value": "63",
                             "status": "met"}],
     "exclusion_criteria": []},
]}
BODY_TRIALS = ("NCT-CANON", "NCT-BOOL", "NCT-BAD", "NCT-DROP")


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 1 -- THE MIGRATION IS ADDITIVE AND IDEMPOTENT, ON BOTH TABLES
# ===========================================================================

print("=" * 78)
print("SECTION 1 -- both tables migrate, fresh and pre-migration")
print("=" * 78)

_DB = fresh_db("roundtrip.db")
_first = silence(_dl.initialize_database, _DB)
_cols_i_1 = columns_of(_DB, "inferences")
_cols_t_1 = columns_of(_DB, "trial_matches")
_second = silence(_dl.initialize_database, _DB)

check("initialize_database returned the same path both times", _first, _second)
check("the second run raised nothing",
      isinstance(_second, dict) and "__raised__" in _second, False)
check("the inferences column set is unchanged by the second run",
      columns_of(_DB, "inferences"), _cols_i_1)
check("the trial_matches column set is unchanged by the second run",
      columns_of(_DB, "trial_matches"), _cols_t_1)

check("the two run-level columns exist on inferences",
      sorted(c for c in INFERENCE_COLUMNS if c in _cols_i_1),
      sorted(INFERENCE_COLUMNS))
check("the five per-trial columns exist on trial_matches",
      sorted(c for c in TRIAL_COLUMNS if c in _cols_t_1), sorted(TRIAL_COLUMNS))
check("...declared in INFERENCE_COLUMN_ADDITIONS with these types",
      {k: _dl.INFERENCE_COLUMN_ADDITIONS.get(k) for k in INFERENCE_COLUMNS},
      INFERENCE_COLUMNS)
check("...and in TRIAL_MATCH_COLUMN_ADDITIONS with these types",
      {k: _dl.TRIAL_MATCH_COLUMN_ADDITIONS.get(k) for k in TRIAL_COLUMNS},
      TRIAL_COLUMNS)

# A PRE-MIGRATION DATABASE: both tables built, then every new column dropped, so
# the migration has something real to add rather than a fresh CREATE that
# already carries them. This is the only shape that exercises the ALTER path.
_LEGACY = fresh_db("legacy.db")
silence(_dl.initialize_database, _LEGACY)
_legacy_conn = sqlite3.connect(_LEGACY)
for _table, _new in (("inferences", INFERENCE_COLUMNS),
                     ("trial_matches", TRIAL_COLUMNS)):
    for _col in _new:
        _legacy_conn.execute(f"ALTER TABLE {_table} DROP COLUMN {_col}")
_legacy_conn.commit()
_legacy_conn.close()

check("the legacy database starts without the two inferences columns",
      sorted(c for c in INFERENCE_COLUMNS if c in columns_of(_LEGACY, "inferences")),
      [])
check("...and without the five trial_matches columns",
      sorted(c for c in TRIAL_COLUMNS
             if c in columns_of(_LEGACY, "trial_matches")), [])

_dl._INITIALIZED_DATABASES.discard(os.path.abspath(_LEGACY))
silence(_dl.initialize_database, _LEGACY)
_legacy_i = columns_of(_LEGACY, "inferences")
_legacy_t = columns_of(_LEGACY, "trial_matches")
check("migrating a legacy database adds the two inferences columns",
      sorted(c for c in INFERENCE_COLUMNS if c in _legacy_i),
      sorted(INFERENCE_COLUMNS))
check("...and the five trial_matches columns",
      sorted(c for c in TRIAL_COLUMNS if c in _legacy_t), sorted(TRIAL_COLUMNS))
_dl._INITIALIZED_DATABASES.discard(os.path.abspath(_LEGACY))
_again = silence(_dl.initialize_database, _LEGACY)
check("migrating it twice raises nothing",
      isinstance(_again, dict) and "__raised__" in _again, False)
check("...and the second pass adds nothing to inferences",
      columns_of(_LEGACY, "inferences"), _legacy_i)
check("...nor to trial_matches", columns_of(_LEGACY, "trial_matches"), _legacy_t)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 2 -- THE ROUND TRIP: MEASURED, ZERO, ABSENT, EXPLICIT NONE
# ===========================================================================

print()
print("=" * 78)
print("SECTION 2 -- four shapes through the real log_inference")
print("=" * 78)

# One inference per shape, so SQL can separate them by patient_id.
_W_MEASURED = silence(_dl.log_inference, result_dict(
    "measured",
    verdict_normalizations=2, remapped_trials=1,
    matches=[match_dict(
        "NCT-M1", verdict_source="normalized", verdict_original_label="True",
        verdict_original_type="bool", criterion_remaps=3,
        inclusion_criteria=[{"criterion": "adult", "patient_value": "63",
                             "status": "not_evaluable",
                             CRITERION_MARKER: "violated"}])],
    not_evaluable=[match_dict(
        "NCT-M2", eligible="not_evaluable", match_score=0.0,
        not_evaluable_reason=_ev.NOT_EVALUABLE_TRUNCATION_FLOOR)],
), dict(PATIENT), db_path=_DB)

_W_ZERO = silence(_dl.log_inference, result_dict(
    "zeroed",
    verdict_normalizations=0, remapped_trials=0,
    matches=[match_dict("NCT-Z1", verdict_source="canonical",
                        criterion_remaps=0)],
), dict(PATIENT), db_path=_DB)

_W_ABSENT = silence(_dl.log_inference, result_dict(
    "absent", matches=[match_dict("NCT-A1")]), dict(PATIENT), db_path=_DB)

_W_NONE = silence(_dl.log_inference, result_dict(
    "explicit-none",
    verdict_normalizations=None, remapped_trials=None,
    matches=[match_dict("NCT-N1", verdict_source=None, criterion_remaps=None,
                        not_evaluable_reason=None,
                        verdict_original_label=None,
                        verdict_original_type=None)],
), dict(PATIENT), db_path=_DB)

check("all four writes reported ok",
      [getattr(w, "ok", w) for w in (_W_MEASURED, _W_ZERO, _W_ABSENT, _W_NONE)],
      [True, True, True, True])

_r_meas = one(_DB, "SELECT * FROM inferences WHERE patient_id = 'measured'")
_r_zero = one(_DB, "SELECT * FROM inferences WHERE patient_id = 'zeroed'")
_r_abs = one(_DB, "SELECT * FROM inferences WHERE patient_id = 'absent'")
_r_none = one(_DB, "SELECT * FROM inferences WHERE patient_id = 'explicit-none'")

check("a measured run-level count round-trips",
      (_r_meas.get("verdict_normalizations"), _r_meas.get("remapped_trials")),
      (2, 1))
check("a MEASURED zero is stored as 0, not folded into NULL",
      (_r_zero.get("verdict_normalizations"), _r_zero.get("remapped_trials")),
      (0, 0))
check("an absent key stores NULL",
      (_r_abs.get("verdict_normalizations"), _r_abs.get("remapped_trials")),
      (None, None))
check("an explicit None stores NULL",
      (_r_none.get("verdict_normalizations"), _r_none.get("remapped_trials")),
      (None, None))

_t_m1 = trial_row(_DB, "NCT-M1")
_t_m2 = trial_row(_DB, "NCT-M2")
_t_z1 = trial_row(_DB, "NCT-Z1")
_t_a1 = trial_row(_DB, "NCT-A1")
_t_n1 = trial_row(_DB, "NCT-N1")

check("the verdict-normalization triple round-trips",
      (_t_m1.get("verdict_source"), _t_m1.get("verdict_original_label"),
       _t_m1.get("verdict_original_type")), ("normalized", "True", "bool"))
check("the per-trial remap count round-trips", _t_m1.get("criterion_remaps"), 3)
check("not_evaluable_reason round-trips -- the field that was DROPPED here",
      _t_m2.get("not_evaluable_reason"), _ev.NOT_EVALUABLE_TRUNCATION_FLOOR)
check("a canonical row stores 'canonical', which is a MEASUREMENT",
      _t_z1.get("verdict_source"), "canonical")
check("...with the two original columns NULL, because there was no original",
      (_t_z1.get("verdict_original_label"), _t_z1.get("verdict_original_type")),
      (None, None))
check("...and a measured 0 remap count stays 0",
      _t_z1.get("criterion_remaps"), 0)
check("an absent key on the match dict stores NULL on all five",
      tuple(_t_a1.get(c) for c in TRIAL_MARKERS), (None,) * 5)
check("an explicit None does too",
      tuple(_t_n1.get(c) for c in TRIAL_MARKERS), (None,) * 5)

check("0 and NULL are distinguishable in SQL on the run-level column",
      (at(rows(_DB, "SELECT COUNT(*) c FROM inferences "
                    "WHERE verdict_normalizations IS NULL"), 0, {}).get("c"),
       at(rows(_DB, "SELECT COUNT(*) c FROM inferences "
                    "WHERE verdict_normalizations = 0"), 0, {}).get("c")),
      (2, 1))
check("...and on the per-trial column",
      (at(rows(_DB, "SELECT COUNT(*) c FROM trial_matches "
                    "WHERE criterion_remaps IS NULL"), 0, {}).get("c"),
       at(rows(_DB, "SELECT COUNT(*) c FROM trial_matches "
                    "WHERE criterion_remaps = 0"), 0, {}).get("c")),
      (3, 1))

_details = json.loads(_t_m1.get("criterion_details") or "{}")
check("criterion_details still json.dumps exactly two keys",
      sorted(_details), ["exclusion", "inclusion"])
check("the remapped row carries the status the model wrote, beside the "
      "corrected one",
      (at(_details.get("inclusion", []), 0, {}).get("status"),
       at(_details.get("inclusion", []), 0, {}).get(CRITERION_MARKER)),
      ("not_evaluable", "violated"))
check("a row that was NOT remapped carries no such key -- absent, not empty",
      CRITERION_MARKER in json.loads(
          _t_z1.get("criterion_details") or '{"inclusion": []}').get(
              "inclusion", [{}])[0] if json.loads(
          _t_z1.get("criterion_details") or '{"inclusion": []}').get(
              "inclusion") else False, False)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 3 -- THE PIPELINE STAMPS THEM, ON THE REAL GRAPH
# ===========================================================================

print()
print("=" * 78)
print("SECTION 3 -- the real Stage 5 node and the real node_finalize")
print("=" * 78)

_RES = run_pipeline(BODY, BODY_TRIALS)
check("the run produced a result", "__raised__" in _RES, False)
check("...and evaluated all four trials", _RES.get("candidates_evaluated"), 4)

_canon = verdict_of(_RES, "NCT-CANON")
_bool_ = verdict_of(_RES, "NCT-BOOL")
_bad = verdict_of(_RES, "NCT-BAD")
_drop = verdict_of(_RES, "NCT-DROP")

check("a canonical label is recorded as canonical -- a MEASUREMENT, not silence",
      _canon.get("verdict_source"), _st.VERDICT_SOURCE_CANONICAL)
check("...and carries no original, because the original IS `eligible`",
      (_canon.get("verdict_original_label"),
       _canon.get("verdict_original_type")), (None, None))
check("...and a measured 0 remaps", _canon.get("criterion_remaps"), 0)

check("a boolean True label is recorded as normalized",
      _bool_.get("verdict_source"), _st.VERDICT_SOURCE_NORMALIZED)
check("...with the repr and the TYPE the model wrote",
      (_bool_.get("verdict_original_label"),
       _bool_.get("verdict_original_type")), ("True", "bool"))
check("...and its one out-of-vocabulary criterion status counted",
      _bool_.get("criterion_remaps"), 1)
check("...and that criterion row carries what it said before the rewrite",
      [(c.get("status"), c.get(CRITERION_MARKER))
       for c in _bool_.get("inclusion_criteria", [])],
      [("not_evaluable", "violated")])

check("an unreadable label is recorded as unrecognized",
      _bad.get("verdict_source"), _st.VERDICT_SOURCE_UNRECOGNIZED)
check("...with the repr of what was written",
      _bad.get("verdict_original_label"), "'MAYBE'")

check("a non-object criterion entry counts as a remap EVENT",
      _drop.get("criterion_remaps"), 1)
check("...and leaves no row carrying the criterion marker, which is why the "
      "count can exceed the rows",
      [c.get(CRITERION_MARKER) for c in _drop.get("inclusion_criteria", [])],
      [None])

check("the run-level counters arrive at the terminal result",
      (_RES.get("verdict_normalizations"), _RES.get("remapped_trials")), (2, 2))
check("...and are NOT the same number as cross_vocab_remaps, which counts "
      "EVENTS", _RES.get("cross_vocab_remaps"), 2)
check("non-degeneracy: the four entries do not all agree on verdict_source",
      len({_canon.get("verdict_source"), _bool_.get("verdict_source"),
           _bad.get("verdict_source")}), 3)

# --- 3b: the CONSTRUCTED entries carry none of the five ---------------------
#
# A trial the model never mentioned is appended by the reconciliation AFTER the
# normalizer loop, so it never had a model-written label. Its markers must be
# ABSENT, which is what makes NULL in the database select exactly that
# population.
_RECON = run_pipeline({"evaluations": [BODY["evaluations"][0]]},
                      ("NCT-CANON", "NCT-GHOST"))
_ghost = verdict_of(_RECON, "NCT-GHOST")
check("the omitted trial was reconciled into the result",
      _ghost.get("eligible"), _st.TRIAL_VERDICT_NOT_EVALUABLE)
check("...and it carries its own not_evaluable_reason",
      _ghost.get("not_evaluable_reason"), _ev.NOT_EVALUABLE_MODEL_OMITTED)
check("...and NO verdict_source, because it never had a model label",
      "verdict_source" in _ghost, False)
check("...and NO criterion_remaps, because the normalizer never saw it",
      "criterion_remaps" in _ghost, False)
check("...which is the same population emission_index/call_index select",
      (_ghost.get("emission_index"), _ghost.get("call_index")), (None, None))

# --- 3c: NEGATIVE CONTROL, the channel declaration is doing the work --------
#
# LangGraph writes only the channels the state schema declares; an undeclared
# key returned by a node is DISCARDED, silently. Removing the two annotations
# must lose both counters -- and the wrapper is what stops the node's own
# annotation reinstating them.
_REDUCED = types.new_class("ReducedState", (dict,))
_REDUCED.__annotations__ = {k: v for k, v in TrialMatchState.__annotations__.items()
                            if k not in RUN_MARKERS}
_REDUCED.__required_keys__ = frozenset()
_REDUCED.__optional_keys__ = frozenset(_REDUCED.__annotations__)
_REDUCED.__total__ = False

check("the control schema really is two annotations smaller",
      len(TrialMatchState.__annotations__) - len(_REDUCED.__annotations__), 2)
_CTRL = run_pipeline(BODY, BODY_TRIALS, schema=_REDUCED, annotated=False)
check("CONTROL: with the channels removed both counters are lost",
      (_CTRL.get("verdict_normalizations"), _CTRL.get("remapped_trials")),
      (None, None))
check("CONTROL: a channel declared in BOTH schemas survives that run",
      _CTRL.get("cross_vocab_remaps"), 2)
check("CONTROL: the control arm produced the same verdicts, so the loss is "
      "the schema and not the run",
      len(_CTRL.get("matches", [])) + len(_CTRL.get("near_misses", []))
      + len(_CTRL.get("not_evaluable", [])), 4)
check("CONTROL: the PER-TRIAL markers survive it, because they ride on "
      "`evaluations` and need no channel of their own",
      verdict_of(_CTRL, "NCT-BOOL").get("verdict_source"),
      _st.VERDICT_SOURCE_NORMALIZED)

# --- 3d: every stamped key is a declared channel or an entry key ------------
check("both run-level keys are declared in TrialMatchState",
      sorted(k for k in RUN_MARKERS if k in TrialMatchState.__annotations__),
      sorted(RUN_MARKERS))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 4 -- THE MODEL CANNOT FORGE ANY OF THE FIVE
# ===========================================================================

print()
print("=" * 78)
print("SECTION 4 -- the strict-schema argument, against the REAL schema")
print("=" * 78)

_SCHEMA = guarded(_rs.build_response_schema)
_paths_in_schema = guarded(_rs.schema_object_paths, _SCHEMA)
check("the schema was built", isinstance(_SCHEMA, dict), True)
check("every object in it forbids additional properties",
      sorted({bool(node.get("additionalProperties", True))
              for _p, node in (_paths_in_schema or [])}), [False])
check("non-degeneracy: the walk found more than one object",
      len(_paths_in_schema or []) > 1, True)

check("no per-trial marker is a property the model may emit",
      sorted(k for k in TRIAL_MARKERS if k in _rs.TRIAL_FIELDS), [])
check("the criterion marker is not one either",
      CRITERION_MARKER in _rs.CRITERION_FIELDS, False)
check("non-degeneracy: a name that IS emittable is reported as such",
      ("eligible" in _rs.TRIAL_FIELDS, "status" in _rs.CRITERION_FIELDS),
      (True, True))
check("the constants the pipeline stamps with are the names asserted here",
      (_ev.VERDICT_SOURCE_FIELD, _ev.VERDICT_ORIGINAL_LABEL_FIELD,
       _ev.VERDICT_ORIGINAL_TYPE_FIELD, _ev.CRITERION_REMAPS_FIELD,
       _ev.LABEL_REMAP_FIELD),
      ("verdict_source", "verdict_original_label", "verdict_original_type",
       "criterion_remaps", "remapped_from_status"))

# THE STRONGEST FORM OF THE ARGUMENT: hand the model's own schema a payload
# carrying one of the markers and show the schema declares it invalid. The
# schema is data, so this is a property of the declaration rather than of any
# validator we happen to have installed.
check("the trial object's required list names exactly its properties",
      sorted(_SCHEMA["properties"]["evaluations"]["items"]["required"]),
      sorted(_SCHEMA["properties"]["evaluations"]["items"]["properties"]))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 5 -- A RUN THAT NEVER COMPLETED THE NORMALIZER STORES NULL
# ===========================================================================

print()
print("=" * 78)
print("SECTION 5 -- the failure paths carry absence, never a measured zero")
print("=" * 78)

# The node's own failure return: an unparseable response, driven through the
# real node. The counters are written on the SUCCESS return only.
_BAD_JSON = run_pipeline("not json at all", BODY_TRIALS)
check("the unparseable run still produced a result", "__raised__" in _BAD_JSON,
      False)
check("...and reports no normalizer counters at all",
      (_BAD_JSON.get("verdict_normalizations"),
       _BAD_JSON.get("remapped_trials")), (None, None))

# The two other terminal nodes, called directly: both must carry the keys (so
# the column is never populated for only one node's rows) and both must carry
# None.
_NOCAND = guarded(_tm.node_no_candidates,
                  {"patient_data": dict(PATIENT), "stage_timings": {}})
_ERR = guarded(_tm.node_error_handler,
               {"patient_data": dict(PATIENT), "stage_timings": {},
                "error": "boom"})
for _label, _node_out in (("node_no_candidates", _NOCAND),
                          ("node_error_handler", _ERR)):
    _res = (_node_out or {}).get("result", {})
    check(f"{_label} declares both keys, so the column is not node-dependent",
          sorted(k for k in RUN_MARKERS if k in _res), sorted(RUN_MARKERS))
    check(f"...and reports None for both on {_label}",
          tuple(_res.get(k) for k in RUN_MARKERS), (None, None))

_FDB = fresh_db("failure.db")
silence(_dl.initialize_database, _FDB)
silence(_dl.log_inference, _BAD_JSON | {"patient_id": "failed-run",
                                        "timestamp": "2026-08-21T00:00:00"},
        dict(PATIENT), db_path=_FDB)
_r_fail = one(_FDB, "SELECT * FROM inferences WHERE patient_id = 'failed-run'")
check("a failed run's row stores NULL on both, not 0",
      (_r_fail.get("verdict_normalizations"), _r_fail.get("remapped_trials")),
      (None, None))
check("non-degeneracy: that same row DOES carry the tokens it was billed, so "
      "the NULLs above are about the normalizer and not about the row",
      (_r_fail.get("llm_classifier_input_tokens") or 0) > 0, True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 6 -- THE SUM INVARIANT, END TO END
# ===========================================================================

print()
print("=" * 78)
print("SECTION 6 -- per-trial remaps sum to the run's event count")
print("=" * 78)

_IDB = fresh_db("invariant.db")
silence(_dl.initialize_database, _IDB)
_w = silence(_dl.log_inference,
             _RES | {"patient_id": "invariant-run",
                     "timestamp": "2026-08-21T00:00:00"},
             dict(PATIENT), db_path=_IDB)
check("the pipeline result was written", getattr(_w, "ok", _w), True)

_inv = one(_IDB, """
    SELECT i.cross_vocab_remaps                       AS events_stored,
           SUM(COALESCE(tm.criterion_remaps, 0))      AS events_summed,
           COUNT(tm.id)                               AS trial_rows,
           SUM(CASE WHEN tm.criterion_remaps > 0 THEN 1 ELSE 0 END)
                                                      AS trials_with_a_remap,
           i.remapped_trials                          AS remapped_trials_stored
    FROM inferences i JOIN trial_matches tm ON tm.inference_id = i.id
    WHERE i.patient_id = 'invariant-run' GROUP BY i.id
""")
check("every evaluation became a trial_matches row", _inv.get("trial_rows"), 4)
check("the per-trial counts sum to the run's stored event count",
      _inv.get("events_summed"), _inv.get("events_stored"))
check("...non-degenerately (the sum is not zero on both sides)",
      (_inv.get("events_summed") or 0) > 0, True)
check("the count of trials carrying a remap matches the stored run-level one",
      _inv.get("trials_with_a_remap"), _inv.get("remapped_trials_stored"))
check("...and that number DIFFERS from the event count on real data, which is "
      "why the two columns are not one",
      _inv.get("trials_with_a_remap") == _inv.get("events_stored"), True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 7 -- NO NEW KEY IS FIXTURE-COMPARED
# ===========================================================================

print()
print("=" * 78)
print("SECTION 7 -- build_deterministic_prefix names none of the new keys")
print("=" * 78)


def _prefix_strings():
    """Every string constant inside build_deterministic_prefix, by AST.

    The prefix is a CLOSED ENUMERATION of keys: it projects what it names and
    nothing else, so a key whose NAME does not occur in that function cannot
    reach a fixture and cannot move a replay. Reading the source is therefore
    the whole question, and it needs no fixture on disk.
    """
    tree = ast.parse(Path(_CAP_PY).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and \
                node.name == "build_deterministic_prefix":
            return {n.value for n in ast.walk(node)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    return set()


_PREFIX_STRINGS = _prefix_strings()
check("the prefix builder was found and is non-degenerate",
      len(_PREFIX_STRINGS) > 40, True)
check("non-degeneracy: keys the prefix DOES project are present",
      sorted(k for k in ("nct_id", "eligible", "match_score", "assessment",
                         "not_evaluable_reason", "emission_index")
             if k in _PREFIX_STRINGS),
      ["assessment", "eligible", "emission_index", "match_score", "nct_id",
       "not_evaluable_reason"])
check("no per-trial marker this pass added is named by it",
      sorted(k for k in TRIAL_MARKERS
             if k != "not_evaluable_reason" and k in _PREFIX_STRINGS), [])
check("neither run-level counter is named by it",
      sorted(k for k in RUN_MARKERS if k in _PREFIX_STRINGS), [])
check("nor the criterion marker", CRITERION_MARKER in _PREFIX_STRINGS, False)

# not_evaluable_reason IS projected and IS therefore fixture-compared -- which
# is exactly why this pass persists it without widening where it is WRITTEN.
check("not_evaluable_reason IS projected, so its VALUES must not move",
      "not_evaluable_reason" in _PREFIX_STRINGS, True)
# THIS WAS A SUBSTRING COUNT PINNED AT 2 AND IT HAD TWO PROBLEMS, one of which
# only appeared when the count legitimately moved. The count is 7 now -- the
# provenance pass wrote the two corrections, and the pass that closed the Step 2
# gap added five more so that every not_evaluable population states why -- and
# raising the literal would have left the second problem in place: the string
# also matches PROSE, and evaluation.py's own argument for the field quotes the
# assignment it describes, so the substring count read 8 against 7 real writes.
# The check is over ASSIGNMENTS now, by AST, and it asserts the thing that
# actually matters: WHICH reasons the normalizer can write, as a set.
_ASSIGNED_REASONS = set()
for _n in ast.walk(ast.parse(Path(_EV_PY).read_text(encoding="utf-8"))):
    if not isinstance(_n, ast.Assign) or len(_n.targets) != 1:
        continue
    _t = _n.targets[0]
    if not (isinstance(_t, ast.Subscript)
            and isinstance(_t.value, ast.Name) and _t.value.id == "eval_result"
            and isinstance(_t.slice, ast.Constant)
            and _t.slice.value == "not_evaluable_reason"):
        continue
    _ASSIGNED_REASONS.add(
        _n.value.id if isinstance(_n.value, ast.Name) else ast.unparse(_n.value))
check("...and the reasons the normalizer can write are exactly the CORRECTED "
      "and DECLARED classes, by the constant NAME at every site",
      sorted(_ASSIGNED_REASONS),
      sorted({"UNEVALUABLE_UNRECOGNIZED_VERDICT",
              "UNEVALUABLE_NO_CRITERIA_RETURNED",
              "UNEVALUABLE_REJECTION_UNSUPPORTED",
              "UNEVALUABLE_REMAP_NO_SURVIVOR",
              "UNEVALUABLE_MODEL_DECLARED"}))
check("non-degeneracy: the walk found real assignments, not an empty set",
      len(_ASSIGNED_REASONS) > 1, True)

# The composed assessment IS compared. It must not read a key this pass added.
_compose_src = ""
_tree = ast.parse(Path(_EV_PY).read_text(encoding="utf-8"))
for _n in ast.walk(_tree):
    if isinstance(_n, ast.FunctionDef) and _n.name in (
            "compose_assessment", "assessment_composition_case"):
        _compose_src += ast.unparse(_n)
check("compose_assessment / assessment_composition_case were found",
      len(_compose_src) > 200, True)
check("neither reads any key this pass added, so the composed assessment "
      "cannot move",
      sorted(k for k in (CRITERION_MARKER,) + TRIAL_MARKERS + RUN_MARKERS
             if k != "not_evaluable_reason" and f'"{k}"' in _compose_src
             or k != "not_evaluable_reason" and f"'{k}'" in _compose_src), [])
check("non-degeneracy: it DOES read the keys it is documented to read",
      all(f"'{k}'" in _compose_src or f'"{k}"' in _compose_src
          for k in ("not_evaluable_reason", "status", "eligible")), True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 8 -- THE CAMPAIGN QUESTIONS, IN PLAIN SQL
# ===========================================================================

print()
print("=" * 78)
print("SECTION 8 -- the four questions, answered without parsing JSON")
print("=" * 78)

_QKEYS = ("not_evaluable_reasons", "verdict_normalization_sources",
          "criterion_remap_incidence", "run_normalizer_provenance")
check("all four campaign queries are in the registry",
      sorted(k for k in _QKEYS if k in _q.QUERIES_BY_KEY), sorted(_QKEYS))

_qconn = sqlite3.connect(f"file:{_IDB}?mode=ro", uri=True)
try:
    for _k in _QKEYS:
        _frame = guarded(_q.run, _qconn, _k)
        check(f"query {_k} executes and returns rows",
              hasattr(_frame, "empty") and not _frame.empty, True)
    # No query may reach a JSON function: the whole point of the columns is that
    # these are scalar GROUP BYs.
    _sql_all = " ".join(_q.QUERIES_BY_KEY[_k].sql.lower() for _k in _QKEYS)
    check("none of them parses JSON",
          any(fn in _sql_all for fn in ("json_each", "json_extract", "->>")),
          False)

    # Q1: how many trials landed not_evaluable, and the count per reason.
    _q1 = rows(_IDB, """
        SELECT COALESCE(not_evaluable_reason, '(not reported)') AS reason,
               COUNT(*) AS n
        FROM trial_matches WHERE eligible = 'not_evaluable'
        GROUP BY not_evaluable_reason ORDER BY reason
    """)
    # IT USED TO EXPECT '(not reported)', AND THAT WAS THE GAP RATHER THAN THE
    # PROPERTY. The trial in this run is a model-returned entry whose label the
    # normalizer could not read; Step 0 wrote not_evaluable for it and nothing
    # recorded why, so the campaign question "why was this not evaluated"
    # answered "(not reported)" for a trial the pipeline itself had classified.
    # The reason is on the row now, and it is the same string the audit log
    # carries for that trial.
    check("Q1 -- the one not_evaluable trial of this run names its reason",
          [(r["reason"], r["n"]) for r in _q1],
          [(_ev.UNEVALUABLE_UNRECOGNIZED_VERDICT, 1)])

    # Q2: how many verdicts were normalized, and from what original types.
    _q2 = rows(_IDB, """
        SELECT COALESCE(verdict_source, '(not checked)') AS src,
               COALESCE(verdict_original_type, '(n/a)')  AS typ,
               COUNT(*) AS n
        FROM trial_matches GROUP BY verdict_source, verdict_original_type
        ORDER BY src, typ
    """)
    check("Q2 -- the sources and original types are recoverable in one GROUP BY",
          [(r["src"], r["typ"], r["n"]) for r in _q2],
          [("canonical", "(n/a)", 2), ("normalized", "bool", 1),
           ("unrecognized", "str", 1)])

    # Q3: how many trials carried at least one criterion-label remap.
    _q3 = one(_IDB, """
        SELECT SUM(CASE WHEN criterion_remaps > 0 THEN 1 ELSE 0 END) AS affected,
               SUM(COALESCE(criterion_remaps, 0))                    AS events
        FROM trial_matches
    """)
    check("Q3 -- trials affected and events counted, and they differ",
          (_q3.get("affected"), _q3.get("events")), (2, 2))

    # Q4: the per patient-trial drill-down.
    _q4 = rows(_IDB, """
        SELECT i.patient_id, tm.nct_id, tm.eligible, tm.not_evaluable_reason,
               tm.verdict_source, tm.verdict_original_label,
               tm.verdict_original_type, tm.criterion_remaps
        FROM inferences i JOIN trial_matches tm ON tm.inference_id = i.id
        WHERE i.patient_id = 'invariant-run' ORDER BY tm.nct_id
    """)
    check("Q4 -- one drill-down row per patient-trial pair",
          [(r["nct_id"], r["verdict_source"], r["criterion_remaps"])
           for r in _q4],
          [("NCT-BAD", "unrecognized", 0), ("NCT-BOOL", "normalized", 1),
           ("NCT-CANON", "canonical", 0), ("NCT-DROP", "canonical", 1)])
finally:
    _qconn.close()


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 9 -- NEGATIVE CONTROLS
# ===========================================================================

print()
print("=" * 78)
print("SECTION 9 -- every assertion above is shown to be able to fail")
print("=" * 78)


def control(label, mutate, source, module_name, package, drive):
    """Plant into an in-memory copy, run `drive` against it, report the outcome."""
    try:
        module = exec_copy(source, module_name, package, mutate)
    except AssertionError as exc:
        fail(label, f"PLANT-FAILED: {exc}")
        return None
    finally:
        pass
    try:
        return guarded(drive, module)
    finally:
        sys.modules.pop(module_name, None)


# --- 9a: the trial_matches INSERT stops naming not_evaluable_reason ---------
def _drive_writer(module, marker_key, patient="ctl"):
    db = fresh_db(f"ctl-{marker_key}.db")
    silence(module.initialize_database, db)
    silence(module.log_inference, result_dict(
        patient,
        verdict_normalizations=2, remapped_trials=1,
        matches=[match_dict("NCT-C1", verdict_source="normalized",
                            verdict_original_label="True",
                            verdict_original_type="bool", criterion_remaps=3)],
        not_evaluable=[match_dict(
            "NCT-C2", eligible="not_evaluable",
            not_evaluable_reason=_ev.NOT_EVALUABLE_TRUNCATION_FLOOR)],
    ), dict(PATIENT), db_path=db)
    return db


_c1 = control(
    "CONTROL the writer dropping not_evaluable_reason is caught",
    lambda t: sub(t, "                match.get(\"not_evaluable_reason\"),\n",
                  "                None,\n", 1),
    _DL_PY, "_ctl_dl_1", "oncotriage.storage",
    lambda m: trial_row(_drive_writer(m, "ner"), "NCT-C2").get(
        "not_evaluable_reason"))
check("CONTROL: with the value replaced, the reason no longer round-trips",
      _c1, None)

# --- 9b: the run-level counter defaulted to 0 instead of NULL --------------
_c2 = control(
    "CONTROL a `, 0` default on the run-level counter is caught",
    lambda t: sub(t, 'result.get("verdict_normalizations"),',
                  'result.get("verdict_normalizations", 0),', 1),
    _DL_PY, "_ctl_dl_2", "oncotriage.storage",
    lambda m: one(_drive_writer(m, "vn0"),
                  "SELECT verdict_normalizations v FROM inferences "
                  "WHERE patient_id = 'ctl'").get("v"))
check("CONTROL: the defaulted writer still stores the MEASURED value here, "
      "so the plant is only visible on an absent key",
      _c2, 2)


def _drive_absent(module):
    db = fresh_db("ctl-absent.db")
    silence(module.initialize_database, db)
    silence(module.log_inference, result_dict("ctl-absent"),
            dict(PATIENT), db_path=db)
    return one(db, "SELECT verdict_normalizations v FROM inferences "
                   "WHERE patient_id = 'ctl-absent'").get("v")


_c3 = control(
    "CONTROL the same plant, driven by an ABSENT key",
    lambda t: sub(t, 'result.get("verdict_normalizations"),',
                  'result.get("verdict_normalizations", 0),', 1),
    _DL_PY, "_ctl_dl_3", "oncotriage.storage", _drive_absent)
check("CONTROL: a run that measured nothing would be stored as a measured 0",
      _c3, 0)
check("...while the SHIPPED writer stores NULL for it",
      _r_abs.get("verdict_normalizations"), None)

# --- 9c: the migration entry deleted ---------------------------------------
_c4 = control(
    "CONTROL removing the column from TRIAL_MATCH_COLUMN_ADDITIONS is caught",
    lambda t: sub(t, '    "criterion_remaps":        "INTEGER",\n', "", 1),
    _DL_PY, "_ctl_dl_4", "oncotriage.storage",
    lambda m: "criterion_remaps" in columns_of(
        _drive_writer(m, "nocol"), "trial_matches"))
check("CONTROL: without the migration entry a legacy database never gains it",
      _c4, False)

# --- 9d: the normalizer stops stamping the per-trial source -----------------
def _drive_stage5(module, body=None, trials=None, focus="NCT-BOOL"):
    body = BODY if body is None else body
    trials = BODY_TRIALS if trials is None else trials
    node = module.node_llm_classifier_evaluation
    graph = StateGraph(TrialMatchState)
    graph.add_node("evaluate", node)
    graph.set_entry_point("evaluate")
    graph.add_edge("evaluate", END)
    app = graph.compile()
    state = {"patient_data": dict(PATIENT),
             "filtered_trials": [trial_obj(n) for n in trials],
             "stage_timings": {}}
    with deps.override(deps.OPENAI_CLIENT, _Client(body)):
        out = silence(app.invoke, state)
    if isinstance(out, dict) and "__raised__" in out:
        return out
    by = {e.get("nct_id"): e for e in out.get("evaluations", [])}
    return {"source": by.get(focus, {}).get("verdict_source"),
            "remaps": by.get(focus, {}).get("criterion_remaps"),
            "row": [c.get(CRITERION_MARKER)
                    for c in by.get(focus, {}).get("inclusion_criteria", [])],
            "run": (out.get("verdict_normalizations"),
                    out.get("remapped_trials")),
            "events": out.get("cross_vocab_remaps")}


_c5 = control(
    "CONTROL the normalizer no longer stamping verdict_source is caught",
    lambda t: sub(t, "        eval_result[VERDICT_SOURCE_FIELD] = verdict_source\n",
                  "", 1),
    _EV_PY, "_ctl_ev_1", "oncotriage.agent", _drive_stage5)
check("CONTROL: the per-trial source disappears", (_c5 or {}).get("source"),
      None)
check("...while the shipped module stamps it",
      _bool_.get("verdict_source"), _st.VERDICT_SOURCE_NORMALIZED)

_c6 = control(
    "CONTROL the criterion row no longer recording what it said is caught",
    lambda t: sub(t, "                c[LABEL_REMAP_FIELD] = status\n", "", 1),
    _EV_PY, "_ctl_ev_2", "oncotriage.agent", _drive_stage5)
check("CONTROL: the stored row loses the status the model wrote",
      (_c6 or {}).get("row"), [None])
check("...while the shipped module keeps it",
      [c.get(CRITERION_MARKER) for c in _bool_.get("inclusion_criteria", [])],
      ["violated"])

# THE BODY THAT SEPARATES A COUNT FROM A FLAG, AND EVENTS FROM TRIALS: two
# out-of-vocabulary statuses on ONE trial. On BODY above, every affected trial
# carries exactly one remap, so a flag and a count agree and an event total and
# a trial total agree -- which would make the next two controls pass for the
# wrong reason. Both are therefore driven on THIS body.
_TWO_ON_ONE = {"evaluations": [
    {"nct_id": "NCT-CANON", "eligible": "eligible", "match_score": 0.9,
     "assessment": "fine",
     "inclusion_criteria": [{"criterion": "a", "patient_value": "x",
                             "status": "violated"},
                            {"criterion": "b", "patient_value": "y",
                             "status": "violated"}],
     "exclusion_criteria": []}]}
_TWO_RES = run_pipeline(_TWO_ON_ONE, ("NCT-CANON",))
check("SHIPPED, on that body: events = 2, trials = 1, per-trial count = 2 -- "
      "three numbers a flag or an event total alone could not tell apart",
      (_TWO_RES.get("cross_vocab_remaps"), _TWO_RES.get("remapped_trials"),
       verdict_of(_TWO_RES, "NCT-CANON").get("criterion_remaps")),
      (2, 1, 2))

_c7 = control(
    "CONTROL the per-trial remap count reduced to a boolean is caught",
    lambda t: sub(t,
                  "        eval_result[CRITERION_REMAPS_FIELD] = remaps_here\n",
                  "        eval_result[CRITERION_REMAPS_FIELD] = int(remaps_here > 0)\n",
                  1),
    _EV_PY, "_ctl_ev_3", "oncotriage.agent",
    lambda m: _drive_stage5(m, _TWO_ON_ONE, ("NCT-CANON",), "NCT-CANON"))
check("CONTROL: a flag reports 1 where the shipped count reports 2",
      (_c7 or {}).get("remaps"), 1)
check("...and the shipped module reports 2 on the identical body",
      verdict_of(_TWO_RES, "NCT-CANON").get("criterion_remaps"), 2)

_c8 = control(
    "CONTROL the run-level counter no longer returned is caught",
    lambda t: sub(t, '        "verdict_normalizations": len(verdict_normalizations),\n',
                  "", 1),
    _EV_PY, "_ctl_ev_4", "oncotriage.agent", _drive_stage5)
check("CONTROL: the node stops reporting it", at((_c8 or {}).get("run", ()), 0),
      None)
check("...while the shipped node reports 2", _RES.get("verdict_normalizations"),
      2)

_c9 = control(
    "CONTROL the counter counting EVENTS instead of TRIALS is caught",
    lambda t: sub(t, '        "remapped_trials": len({r["nct_id"] for r in label_remaps}),\n',
                  '        "remapped_trials": len(label_remaps),\n', 1),
    _EV_PY, "_ctl_ev_5", "oncotriage.agent",
    lambda m: _drive_stage5(m, _TWO_ON_ONE, ("NCT-CANON",), "NCT-CANON"))
check("CONTROL: counting events reports 2 trials where 1 trial was affected",
      at((_c9 or {}).get("run", ()), 1), 2)
check("...and the shipped module reports 1 on the identical body",
      _TWO_RES.get("remapped_trials"), 1)



#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 10 -- HYGIENE
# ===========================================================================

print()
print("=" * 78)
print("SECTION 10 -- no source file and no production data was touched")
print("=" * 78)

for _p, _before in _DIGESTS_BEFORE.items():
    check(f"{os.path.basename(_p)} is byte-identical after this run",
          digest(_p), _before)
check("non-degeneracy: those digests are real, not all 'absent'",
      sorted(set(_DIGESTS_BEFORE.values())) != ["absent"], True)

check("resolve_inference_db_path(None) is the production database and is NOT "
      "this file's scratch one -- which is what makes every check above "
      "discriminating",
      (os.path.abspath(guarded(_dl.resolve_inference_db_path, None))
       == os.path.abspath(_PROD_DB),
       os.path.abspath(_DB) == os.path.abspath(_PROD_DB)),
      (True, False))
check("the production database is byte-identical", digest(_PROD_DB),
      _PROD_DIGEST_BEFORE)

shutil.rmtree(_TMP, ignore_errors=True)
check("the scratch directory was removed", os.path.exists(_TMP), False)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("SUMMARY")
print("=" * 78)
print(f"  passed: {_RESULTS['passed']}")
print(f"  failed: {_RESULTS['failed']}")
if _FAILURES:
    print()
    for _f in _FAILURES:
        print(f"  FAILED  {_f}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Aug 21 12:00:00 2026

@author: ramyalsaffar
"""
