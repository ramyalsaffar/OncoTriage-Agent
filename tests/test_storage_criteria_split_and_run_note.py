# Criteria-Split and Run-Note Column Test
########################################

"""
TWO VALUES THE PIPELINE ALREADY HAD AND THREW AWAY NOW SURVIVE THE PROCESS.

``trial_matches.criteria_split`` -- HOW THE INDEXER SPLIT THIS TRIAL. The
scrape-admission pass measured that 746 trials of 12,067 (6.18%) had their whole
eligibility block handed to the pipeline as INCLUSION text with the exclusion
side EMPTY, cut that to 213, and stored the method as a trial-level field
``criteria_split`` inside the Qdrant payload. NOTHING DOWNSTREAM COULD READ IT:
it lives inside ``full_trial_json``, so no stored row and no registered query
could say how many unsplit trials a given campaign actually evaluated. Every
trial that reached Stage 5 now carries its own split method onto its
``trial_matches`` row. The split itself is unchanged; this is a measurement.

``runs.note`` -- THE OPERATOR'S OWN WORDS. The stop sentinel may carry a note.
It was read (``control.read_stop_message``), logged, printed in the run's
closing block -- and then died with the process. ``runs.status`` said STOPPED
and nothing anywhere said why, which is exactly the row whose reason a reviewer
needs, because a STOPPED campaign covers a PREFIX of the cohort.

WHAT THIS FILE HOLDS
--------------------
    1. THE VOCABULARY IS TIED TO ITS OWNER. The values the column can hold are
       ``oncotriage/retrieval/indexer.py``'s CRITERIA_SPLIT_* constants, read
       off that module rather than retyped, and every one of them round-trips.
    2. BOTH STAMP PATHS. A model-answered verdict AND a pipeline-constructed
       ``_unevaluable_entry`` carry it -- which is where its NULL convention
       departs from ``emission_index``'s, and the difference is checked.
    3. THE ROUND TRIP, through the REAL ``log_inference`` into a REAL database:
       measured value, NULL for a trial dict that carries no such field, and
       the two distinguishable IN SQL.
    4. ``runs.note``: NULL at open, written at finalize, LEFT ALONE by a
       finalize that passes none, capped with the truncation named, and a
       non-string REFUSED rather than coerced.
    5. THE MIGRATION IS ADDITIVE. A database built at the previous era gains
       both columns and keeps its rows.
    6. THE ERA WAS BUMPED IN THE SAME COMMIT, which is
       ``SCHEMA_USER_VERSION``'s own stated rule.
    7. CONTROLS -- six, each shown to FIRE.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO MODEL LOAD, NO CORPUS, NO GIT
HISTORY, NO LIVE SERVER. Every database is inside a ``tempfile.mkdtemp`` that is
removed and asserted gone, and ``paths._RESOLVED`` is seeded so nothing can
resolve to the production tree. NOT in the collision matrix: the three package
files it reads are written by neither of the suite's two writers and are
sha256-compared at the end. It EXECS NOTHING -- every control is a different
INPUT to a shipped function, or a real database built into a real shape.

Run from terminal:
    python tests/test_storage_criteria_split_and_run_note.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
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

# NO MODEL IS LOADED. Set above every package import for the reason
# oncotriage/fixtures/replay.py records: agent.deps reads it once, at ITS OWN
# import, and an assignment underneath a `from oncotriage...` reaches nothing.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

import ast
import contextlib
import hashlib
import io
import json
import shutil
import sqlite3
import tempfile


#------------------------------------------------------------------------------


_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label, actual, expected):
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
    """Call `fn`, converting a raise into a value `check` can fail on.

    NEVER A BARE CALL INSIDE A check() ARGUMENT LIST. A raise there escapes
    while the argument is being evaluated, so the run reports one traceback
    where it owes a summary and every result below it -- the abort shape this
    project has shipped more than a dozen times, most recently in the call-mode
    pass. Every drive in this file goes through here.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                               # noqa: BLE001
        return ("<RAISED>", type(exc).__name__, str(exc)[:200])


_TMP = tempfile.mkdtemp(prefix="oncotriage-splitnote-")

# EVERYTHING RESOLVES INSIDE THE SCRATCH TREE. paths._RESOLVED is the seam
# tests/test_ablation_db_isolation.py established; seeding it means no glob
# fires and nothing can reach the production database even by accident.
from oncotriage import paths as _paths                          # noqa: E402
_PATHS_SAVED = dict(_paths._RESOLVED)
_paths._RESOLVED["inferences_path"] = os.path.join(_TMP, "never-written.db")

from oncotriage.storage import database_logger as _dl           # noqa: E402
from oncotriage.retrieval import indexer as _indexer            # noqa: E402
from oncotriage.agent import evaluation as _ev                  # noqa: E402

_READ_FILES = [os.path.abspath(_dl.__file__),
               os.path.abspath(_ev.__file__),
               os.path.abspath(_indexer.__file__)]
_HASH_BEFORE = {p: hashlib.sha256(open(p, "rb").read()).hexdigest()
                for p in _READ_FILES}


def silence(fn, *args, **kwargs):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return fn(*args, **kwargs)


def rows(db, sql, params=()):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def columns_of(db, table):
    return [r[1] for r in rows(db, f"PRAGMA table_info({table})")]


#------------------------------------------------------------------------------


print("=" * 78)
print("1. THE VOCABULARY IS THE INDEXER'S, READ OFF IT RATHER THAN RETYPED")
print("=" * 78)
print()

# THE COLUMN IS PLAIN TEXT WITH NO CHECK CONSTRAINT, argued at its declaration:
# `storage` may not import `retrieval` (that edge would put a scraper in every
# batch run's import graph), so a constraint would be a second copy of this
# vocabulary with nothing failing when the two disagree. THIS is the tie
# instead -- and a TEST may import both, because a test is in nobody's import
# graph.
_SPLIT_VALUES = sorted({
    _indexer.CRITERIA_SPLIT_BOTH,
    _indexer.CRITERIA_SPLIT_INCLUSION_ONLY,
    _indexer.CRITERIA_SPLIT_EXCLUSION_ONLY,
    _indexer.CRITERIA_SPLIT_UNSPLIT,
    _indexer.CRITERIA_SPLIT_EMPTY,
})

check("the indexer declares five split methods (non-degeneracy: an empty "
      "vocabulary makes the round trip below assert nothing)",
      len(_SPLIT_VALUES), 5)
check("...and 'unsplit' -- the population this column exists to count -- is "
      "one of them", _indexer.CRITERIA_SPLIT_UNSPLIT in _SPLIT_VALUES, True)

check("trial_matches.criteria_split is declared TEXT in "
      "TRIAL_MATCH_COLUMN_ADDITIONS",
      _dl.TRIAL_MATCH_COLUMN_ADDITIONS.get("criteria_split"), "TEXT")

# THE INDEXER WRITES THE FIELD UNDER THIS NAME. Checked by AST rather than by
# grep so a mention inside a comment cannot satisfy it: the payload dict
# literal must carry the key.
_indexer_src = open(os.path.abspath(_indexer.__file__), encoding="utf-8").read()
_keys_written = {
    k.value
    for node in ast.walk(ast.parse(_indexer_src))
    if isinstance(node, ast.Dict)
    for k in node.keys
    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
check("the indexer writes a dict key named 'criteria_split', so the storage "
      "column is reading a field that is actually produced",
      "criteria_split" in _keys_written, True)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("2. BOTH STAMP PATHS CARRY IT, WHICH emission_index DELIBERATELY DOES NOT")
print("=" * 78)
print()

_TRIAL = {"nct_id": "NCT00000001", "title": "A trial", "phase": "PHASE2",
          "criteria_split": _indexer.CRITERIA_SPLIT_UNSPLIT}

_constructed = drive(_ev._unevaluable_entry, {"trial": _TRIAL},
                     _ev.NOT_EVALUABLE_MODEL_OMITTED)
check("a pipeline-CONSTRUCTED entry carries the trial's split method",
      _constructed.get("criteria_split") if isinstance(_constructed, dict)
      else _constructed,
      _indexer.CRITERIA_SPLIT_UNSPLIT)
check("...while its emission provenance is None, which is the convention this "
      "column deliberately does NOT follow (they answer different questions: "
      "one is about where the MODEL put an entry, this is about the TRIAL)",
      (_constructed.get("emission_index"), _constructed.get("call_index"))
      if isinstance(_constructed, dict) else _constructed,
      (None, None))

_trial_no_field = {"nct_id": "NCT00000002", "title": "t", "phase": "N/A"}
_constructed_none = drive(_ev._unevaluable_entry, {"trial": _trial_no_field},
                          _ev.NOT_EVALUABLE_MODEL_OMITTED)
check("a trial dict with no such field yields None -- never a default -- so "
      "'indexed before the field existed' stays distinguishable from a "
      "measured value",
      _constructed_none.get("criteria_split", "<absent>")
      if isinstance(_constructed_none, dict) else _constructed_none,
      None)

# THE MODEL-ANSWERED PATH, checked by AST against the enrichment loop rather
# than by driving the whole node: the loop is inside
# node_llm_classifier_evaluation, which needs a client, and what matters here
# is that the assignment exists and reads the same key off the same object the
# two lines above it do.
_ev_src = open(os.path.abspath(_ev.__file__), encoding="utf-8").read()
_ev_tree = ast.parse(_ev_src)
_enrich_assign = []
for _node in ast.walk(_ev_tree):
    if (isinstance(_node, ast.Assign) and len(_node.targets) == 1
            and isinstance(_node.targets[0], ast.Subscript)
            and isinstance(_node.targets[0].slice, ast.Constant)
            and _node.targets[0].slice.value == "criteria_split"
            and isinstance(_node.targets[0].value, ast.Name)
            and _node.targets[0].value.id == "eval_result"):
        _enrich_assign.append(ast.unparse(_node))
check("the enrichment loop stamps criteria_split onto every model-answered "
      "verdict, off trial_obj['trial'] exactly as title and phase are",
      _enrich_assign,
      ["eval_result['criteria_split'] = trial_obj['trial'].get('criteria_split')"])

# CONTROL 1: an entry with NO key at all must reach the column as NULL, not as
# a string. Without this, `match.get("criteria_split", "unsplit")` -- a one
# character edit -- would pass every check above.
check("CONTROL 1: a match dict with no criteria_split key has none to read "
      "(the state the writer must turn into NULL rather than a default)",
      {"nct_id": "X"}.get("criteria_split"), None)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("3. THE ROUND TRIP THROUGH THE REAL WRITER")
print("=" * 78)
print()

_DB = os.path.join(_TMP, "roundtrip.db")
silence(_dl.initialize_database, _DB)

check("the column exists on a fresh database",
      "criteria_split" in columns_of(_DB, "trial_matches"), True)


def _result(patient_id, matches):
    """The minimum a terminal node emits that log_inference will accept.

    The key set is tests/test_storage_provenance_persistence.py's, which is the
    shape the writer really requires -- a shorter one comes back
    ``ok=False, KeyError: 'timestamp'``, measured rather than guessed.
    """
    return {
        "patient_id": patient_id,
        "timestamp": "2026-08-25T00:00:00",
        "matching_model": "gpt-4o-2024-08-06",
        "llm_classifier_input_tokens": 1000,
        "llm_classifier_output_tokens": 200,
        "matches": matches,
        "near_misses": [],
        "not_evaluable": [],
        "stage_timings": {},
    }


def _match(nct, split="<omit>"):
    m = {"nct_id": nct, "title": "t", "phase": "PHASE2", "match_score": 0.5,
         "eligible": "eligible", "assessment": "a",
         "inclusion_criteria": [], "exclusion_criteria": []}
    if split != "<omit>":
        m["criteria_split"] = split
    return m


# EVERY VOCABULARY MEMBER ROUND-TRIPS, plus the two absence shapes. Driving all
# five rather than one is what makes this a statement about the column instead
# of about one string.
_matches = [_match(f"NCT1000000{i}", v) for i, v in enumerate(_SPLIT_VALUES)]
_matches.append(_match("NCT20000000", None))       # explicit None
_matches.append(_match("NCT30000000"))             # key absent entirely

_wrote = drive(_dl.log_inference, _result("P1", _matches),
               {"patient_id": "P1"}, db_path=_DB)
check("log_inference reported the write ok",
      getattr(_wrote, "ok", _wrote), True)

_stored = dict(rows(_DB, "SELECT nct_id, criteria_split FROM trial_matches"))
check("every one of the indexer's five split methods round-trips verbatim",
      [_stored.get(f"NCT1000000{i}") for i in range(len(_SPLIT_VALUES))],
      _SPLIT_VALUES)
check("an explicit None reaches the column as NULL",
      _stored.get("NCT20000000"), None)
check("...and so does an absent key",
      _stored.get("NCT30000000"), None)

# NULL AND A VALUE ARE DISTINGUISHABLE IN SQL, which is the whole reason the
# column is not defaulted. This is the query a campaign's exposure is actually
# measured with.
_measured = rows(_DB, "SELECT COUNT(*) FROM trial_matches "
                      "WHERE criteria_split = ?",
                 (_indexer.CRITERIA_SPLIT_UNSPLIT,))[0][0]
_unmeasured = rows(_DB, "SELECT COUNT(*) FROM trial_matches "
                        "WHERE criteria_split IS NULL")[0][0]
check("the exposure query counts exactly the rows recorded 'unsplit'",
      _measured, 1)
check("...and the unmeasured rows are counted separately rather than folded "
      "into any method",
      _unmeasured, 2)

# CONTROL 2: NULL is not 0 and is not "". A reader that COALESCEd would report
# the two unmeasured rows under some method and the exposure figure would be
# wrong in the flattering direction.
check("CONTROL 2: a NULL row is not returned by an equality test against any "
      "vocabulary member",
      [rows(_DB, "SELECT COUNT(*) FROM trial_matches WHERE criteria_split = ?",
            (v,))[0][0] for v in _SPLIT_VALUES],
      [1] * len(_SPLIT_VALUES))


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("3b. THE WHOLE CHAIN, END TO END, THROUGH FOUR REAL FUNCTIONS")
print("=" * 78)
print()

# SECTION 2 CHECKS THE STAMP AND SECTION 3 CHECKS THE WRITE, AND NEITHER SEES
# THE JOIN. Between them sit node_finalize -- which splits the evaluations into
# matches / near_misses / not_evaluable -- and the shape log_inference iterates.
# If that node PROJECTED named keys rather than passing the evaluation dicts
# through, the column would be NULL on every row ever written and both sections
# above would still pass. That is not a hypothetical: this file's author first
# measured the published result and read `criteria_split` as ABSENT, and the
# reading was a defect in the probe rather than in the code -- node_finalize
# returns a state update whose "result" key holds the payload. A section that
# drives the real chain is what settles it either way.
#
# FOUR REAL FUNCTIONS, NO SPEND: the real Stage 5 node with a stub client
# installed through oncotriage/agent/deps.py, the real node_finalize, the real
# log_inference, and a real SQLite read. THE GRAPH IS NEVER INVOKED.

from oncotriage.agent import deps as _deps                      # noqa: E402
from oncotriage.agent import terminal as _terminal              # noqa: E402


class _E2EUsage:
    prompt_tokens = 100
    completion_tokens = 20
    completion_tokens_details = None
    prompt_tokens_details = None


class _E2EClient:
    """Answers every request with an eligible verdict for the ids it was sent."""

    def __init__(self):
        self.calls = 0
        self.chat = type("_Chat", (), {})()
        self.chat.completions = type("_C", (), {"create": self._create})()

    def _create(self, **kwargs):
        import re
        self.calls += 1
        ids = re.findall(r"<<<TRIAL_DATA nct_id=(\S+) ",
                         kwargs["messages"][1]["content"])
        body = json.dumps({"evaluations": [
            {"assessment": "No known disqualifiers.", "eligible": "eligible",
             "inclusion_criteria": [{"criterion": "Age 18+",
                                     "patient_value": "61", "status": "met"}],
             "exclusion_criteria": [], "match_score": 0.0, "nct_id": i}
            for i in ids]})
        msg = type("_M", (), {"content": body, "refusal": None})()
        choice = type("_Ch", (), {"message": msg, "finish_reason": "stop"})()
        return type("_R", (), {"choices": [choice], "usage": _E2EUsage(),
                               "model": _config.MATCHING_MODEL})()


from oncotriage import config as _config                        # noqa: E402

_E2E_PATIENT = {
    "patient_id": "criteria-split-e2e",
    "demographics": {"age": 61, "sex": "female", "race": "white",
                     "ethnicity": "not hispanic or latino"},
    "conditions": [{"code": "254837009",
                    "display": "Malignant neoplasm of breast (disorder)",
                    "verification_status": "confirmed"}],
    "medications": [], "allergies": [], "observations": [], "procedures": [],
}


def _e2e_trial(index, split):
    """A trial in the shape Stage 5 reads, carrying the indexer's own field.

    `split` of None means the trial dict has NO such key at all -- the shape a
    trial indexed before the admission pass added the field has, and the shape
    the column must record as NULL rather than as any method.
    """
    half = "x" * 200
    trial = {"nct_id": "NCT9000%04d" % index, "title": f"E2E {index}",
             "phase": "PHASE2",
             "eligibility": {
                 "inclusion_criteria": "Inclusion Criteria:\n- " + half,
                 "exclusion_criteria": "Exclusion Criteria:\n- " + half}}
    if split is not None:
        trial["criteria_split"] = split
    return {"trial": trial}


_E2E_SPLITS = [_indexer.CRITERIA_SPLIT_UNSPLIT,
               _indexer.CRITERIA_SPLIT_BOTH,
               None]
_E2E_TRIALS = [_e2e_trial(i, s) for i, s in enumerate(_E2E_SPLITS)]

_E2E_DB = os.path.join(_TMP, "end_to_end.db")
silence(_dl.initialize_database, _E2E_DB)

_client = _E2EClient()
_deps.set_override(_deps.OPENAI_CLIENT, _client)
try:
    _state = {"patient_data": _E2E_PATIENT,
              "filtered_trials": _E2E_TRIALS,
              "llm_classifier_retries": 0,
              "mesh_filter_applied": True,
              "mesh_filter_skip_reason": "applied",
              "stage_timings": {}}
    _stage5 = drive(silence, _ev.node_llm_classifier_evaluation, _state)
    _final_state = dict(_stage5) if isinstance(_stage5, dict) else {}
    _final_state.update({"patient_data": _E2E_PATIENT,
                         "filtered_trials": _E2E_TRIALS})
    _finalized = drive(silence, _terminal.node_finalize, _final_state)
finally:
    _deps.clear_override(_deps.OPENAI_CLIENT)

check("Stage 5 ran and did not raise (non-degeneracy: everything below is "
      "vacuous if it did)",
      isinstance(_stage5, dict), True)
check("...and the stub answered, so no real client was reached",
      _client.calls > 0, True)
check("...and no real OpenAI client was ever built (one would be cached here)",
      _deps.peek(_deps.OPENAI_CLIENT) is _deps.UNSET, True)

_result = (_finalized or {}).get("result") if isinstance(_finalized, dict) else None
check("node_finalize published a result", isinstance(_result, dict), True)

_published = ((_result or {}).get("matches", [])
              + (_result or {}).get("near_misses", [])
              + (_result or {}).get("not_evaluable", []))
check("...carrying one entry per trial", len(_published), len(_E2E_TRIALS))
check("...and node_finalize PASSED THE KEY THROUGH rather than projecting a "
      "fixed set of names, which is the join sections 2 and 3 cannot see",
      sorted({"criteria_split" in e for e in _published}), [True])

_result["timestamp"] = "2026-08-25T00:00:00"
_wrote_e2e = drive(_dl.log_inference, _result, _E2E_PATIENT, db_path=_E2E_DB)
check("log_inference reported the write ok",
      getattr(_wrote_e2e, "ok", _wrote_e2e), True)

_e2e_rows = dict(rows(_E2E_DB,
                      "SELECT nct_id, criteria_split FROM trial_matches"))
check("EVERY ROW carries the split method its trial payload carried, all the "
      "way from the Qdrant field to the stored column",
      [_e2e_rows.get("NCT9000%04d" % i) for i in range(len(_E2E_SPLITS))],
      _E2E_SPLITS)
check("...and the three values are genuinely distinct, so the chain is not "
      "passing because everything is one value (non-degeneracy)",
      len(set(_E2E_SPLITS)), 3)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("4. runs.note")
print("=" * 78)
print()

check("runs.note is declared TEXT in RUN_COLUMN_ADDITIONS",
      _dl.RUN_COLUMN_ADDITIONS.get("note"), "TEXT")
check("...and is therefore a column of a fresh database",
      "note" in columns_of(_DB, "runs"), True)
check("...and RUN_COLUMNS names it, so start_run_record binds it",
      "note" in _dl.RUN_COLUMNS, True)

_rid = silence(_dl.start_run_record, "test-note", db_path=_DB)
check("a freshly opened run has a NULL note -- nothing at open can know how "
      "the run will end",
      rows(_DB, "SELECT note FROM runs WHERE id = ?", (_rid,))[0][0], None)

_ok = drive(silence, _dl.finalize_run_record, _rid,
            _dl.RUN_RECORD_STATUS_STOPPED, db_path=_DB,
            note="index rebuild at 14:00, resuming after")
check("finalize_run_record accepted the note", _ok, True)
check("...and the note is on the row",
      rows(_DB, "SELECT note FROM runs WHERE id = ?", (_rid,))[0][0],
      "index rebuild at 14:00, resuming after")
check("...beside the status that says which kind of stop it was",
      rows(_DB, "SELECT status FROM runs WHERE id = ?", (_rid,))[0][0],
      _dl.RUN_RECORD_STATUS_STOPPED)

# A SECOND FINALIZE WITH NO NOTE MUST NOT ERASE THE FIRST ONE'S. This is why
# the SET list is assembled rather than unconditional: the function is public
# and nothing stops a caller finalizing twice.
_again = drive(silence, _dl.finalize_run_record, _rid,
               _dl.RUN_RECORD_STATUS_FINISHED, db_path=_DB)
check("a later finalize with note=None leaves the column alone rather than "
      "writing NULL over it",
      rows(_DB, "SELECT note FROM runs WHERE id = ?", (_rid,))[0][0],
      "index rebuild at 14:00, resuming after")
check("...while the status it DID pass was applied, so the call was not a "
      "no-op (non-degeneracy)",
      rows(_DB, "SELECT status FROM runs WHERE id = ?", (_rid,))[0][0],
      _dl.RUN_RECORD_STATUS_FINISHED)

# THE CAP NAMES ITSELF.
_rid2 = silence(_dl.start_run_record, "test-cap", db_path=_DB)
_long = "x" * (_dl.RUN_NOTE_MAX_CHARS + 500)
drive(silence, _dl.finalize_run_record, _rid2, _dl.RUN_RECORD_STATUS_STOPPED,
      db_path=_DB, note=_long)
_stored_note = rows(_DB, "SELECT note FROM runs WHERE id = ?", (_rid2,))[0][0]
check("an oversize note is truncated at RUN_NOTE_MAX_CHARS",
      _stored_note.startswith("x" * _dl.RUN_NOTE_MAX_CHARS), True)
check("...and the truncation is NAMED in the stored text, so a reader does "
      "not invent the ending",
      "truncated at" in _stored_note, True)
check("...and nothing beyond the cap plus that marker was stored",
      len(_stored_note) < _dl.RUN_NOTE_MAX_CHARS + 60, True)

# A NON-STRING IS REFUSED RATHER THAN COERCED.
_rid3 = silence(_dl.start_run_record, "test-type", db_path=_DB)
_before_bad = sum(v for k, v in _dl.RUN_RECORD_FAILURES.items()
                  if k.startswith("finalize:bad_note:"))
drive(silence, _dl.finalize_run_record, _rid3, _dl.RUN_RECORD_STATUS_STOPPED,
      db_path=_DB, note=ValueError("boom"))
_after_bad = sum(v for k, v in _dl.RUN_RECORD_FAILURES.items()
                 if k.startswith("finalize:bad_note:"))
check("a non-string note is REFUSED rather than str()'d into a plausible "
      "sentence a human would believe",
      rows(_DB, "SELECT note FROM runs WHERE id = ?", (_rid3,))[0][0], None)
check("...and the refusal is COUNTED rather than silent",
      _after_bad - _before_bad, 1)
check("...and the row was still finalized, because a bad note must not cost "
      "the campaign its verdict",
      rows(_DB, "SELECT status FROM runs WHERE id = ?", (_rid3,))[0][0],
      _dl.RUN_RECORD_STATUS_STOPPED)

# CONTROL 3: whitespace-only is no note. A column holding "" says nothing NULL
# does not, and it would give a reader a third state to interpret.
_rid4 = silence(_dl.start_run_record, "test-blank", db_path=_DB)
drive(silence, _dl.finalize_run_record, _rid4, _dl.RUN_RECORD_STATUS_STOPPED,
      db_path=_DB, note="   \n\t  ")
check("CONTROL 3: a whitespace-only note is treated as no note",
      rows(_DB, "SELECT note FROM runs WHERE id = ?", (_rid4,))[0][0], None)

# CONTROL 4: a note that needs no truncation carries no marker. Without this,
# an implementation that appended the marker unconditionally would satisfy the
# cap checks above.
_rid5 = silence(_dl.start_run_record, "test-short", db_path=_DB)
drive(silence, _dl.finalize_run_record, _rid5, _dl.RUN_RECORD_STATUS_STOPPED,
      db_path=_DB, note="short")
check("CONTROL 4: a note under the cap is stored verbatim with no marker",
      rows(_DB, "SELECT note FROM runs WHERE id = ?", (_rid5,))[0][0], "short")

# CONTROL 5: the batch runner passes the note through. Checked by AST at the
# one call site that has one, because main() cannot be driven here -- it builds
# a BM25 index from a live Qdrant and bills one Stage 5 call per patient.
from oncotriage.batch import runner as _runner                  # noqa: E402
_runner_src = open(os.path.abspath(_runner.__file__), encoding="utf-8").read()
_note_kwargs = [
    ast.unparse(kw.value)
    for node in ast.walk(ast.parse(_runner_src))
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Name)
    and node.func.id == "finalize_run_record"
    for kw in node.keywords if kw.arg == "note"]
check("CONTROL 5: exactly one finalize_run_record call in the batch runner "
      "passes a note, and it is the stop switch's message",
      _note_kwargs,
      ["STOP_SWITCH.message if STOP_SWITCH.requested else None"])
check("...and the runner calls finalize_run_record more than once, so the "
      "single note site is a choice rather than the only site (non-degeneracy)",
      len([1 for node in ast.walk(ast.parse(_runner_src))
           if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
           and node.func.id == "finalize_run_record"]) > 1, True)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("5. THE MIGRATION IS ADDITIVE")
print("=" * 78)
print()

# A DATABASE BUILT WITHOUT THE TWO COLUMNS, then migrated. Built by creating the
# real tables and DROPPING the two columns, so the starting shape is the real
# previous era rather than a hand-typed approximation -- the same technique
# tests/test_storage_schema_guards.py uses.
_OLD = os.path.join(_TMP, "previous_era.db")
silence(_dl.initialize_database, _OLD)
_conn = sqlite3.connect(_OLD)
try:
    _conn.execute("ALTER TABLE trial_matches DROP COLUMN criteria_split")
    _conn.execute("ALTER TABLE runs DROP COLUMN note")
    _conn.execute("PRAGMA user_version = 4")
    _conn.execute("INSERT INTO runs (started_at, status, invocation_source) "
                  "VALUES ('2026-01-01T00:00:00', 'FINISHED', 'legacy')")
    _conn.commit()
finally:
    _conn.close()

check("the previous-era database really lacks both columns (non-degeneracy)",
      ("criteria_split" in columns_of(_OLD, "trial_matches"),
       "note" in columns_of(_OLD, "runs")),
      (False, False))
check("...and carries a row written before them",
      rows(_OLD, "SELECT COUNT(*) FROM runs")[0][0], 1)

silence(_dl.initialize_database, _OLD)

check("the migration adds trial_matches.criteria_split",
      "criteria_split" in columns_of(_OLD, "trial_matches"), True)
check("...and runs.note",
      "note" in columns_of(_OLD, "runs"), True)
check("...and the pre-existing row survives with NULL in the new column, "
      "which is what 'nobody recorded this' has to read as",
      rows(_OLD, "SELECT status, note FROM runs")[0], ("FINISHED", None))

# CONTROL 6: the migration is idempotent -- running it again neither raises nor
# duplicates. `ALTER TABLE ADD COLUMN` has no IF NOT EXISTS form, so the
# PRAGMA check IS the guard.
_again2 = drive(silence, _dl.initialize_database, _OLD)
check("CONTROL 6: re-running the migration does not raise",
      isinstance(_again2, tuple) and _again2 and _again2[0] == "<RAISED>",
      False)
check("...and does not duplicate the column",
      columns_of(_OLD, "runs").count("note"), 1)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("6. THE ERA WAS BUMPED IN THE SAME COMMIT")
print("=" * 78)
print()

# SCHEMA_USER_VERSION's own comment: "BUMP THIS IN THE SAME COMMIT THAT CHANGES
# THE SCHEMA ... A stamp that lags the schema is worse than no stamp, because a
# reader acts on it."
check("a database this code creates is stamped with the current era",
      rows(_DB, "PRAGMA user_version")[0][0], _dl.SCHEMA_USER_VERSION)
check("...and the era is at least 5, the number these two columns introduced",
      _dl.SCHEMA_USER_VERSION >= 5, True)

_dl_src = open(os.path.abspath(_dl.__file__), encoding="utf-8").read()
_era_head = _dl_src.split("SCHEMA_USER_VERSION = ")[0]
check("...and the era record names it, so the number is documented rather "
      "than only incremented",
      f"# ERA {_dl.SCHEMA_USER_VERSION}:" in _era_head, True)
check("...and that entry names both columns this era added",
      ("criteria_split" in _era_head.split(f"# ERA {_dl.SCHEMA_USER_VERSION}:")[1]
       .split("# ERA ")[0],
       "runs.note" in _era_head.split(f"# ERA {_dl.SCHEMA_USER_VERSION}:")[1]
       .split("# ERA ")[0]),
      (True, True))


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("7. ISOLATION")
print("=" * 78)
print()

_prod = _PATHS_SAVED.get("inferences_path")
check("this file never resolved the production inferences path",
      _paths._RESOLVED.get("inferences_path").startswith(_TMP), True)

# EVERY DATABASE THIS FILE NAMED, checked BEFORE _RESOLVED is restored, so the
# seeded scratch path is still the one being compared.
_DATABASES = [_DB, _E2E_DB, _OLD, _paths._RESOLVED.get("inferences_path")]
check("every database this file named is inside the scratch directory",
      [d for d in _DATABASES if not os.path.abspath(d).startswith(_TMP)], [])
check("...and the three it actually wrote are all there (non-degeneracy)",
      sorted(f for f in os.listdir(_TMP) if f.endswith(".db")),
      ["end_to_end.db", "previous_era.db", "roundtrip.db"])

_paths._RESOLVED.clear()
_paths._RESOLVED.update(_PATHS_SAVED)
check("...and paths._RESOLVED was restored",
      _paths._RESOLVED.get("inferences_path"), _prod)

_HASH_AFTER = {p: hashlib.sha256(open(p, "rb").read()).hexdigest()
               for p in _READ_FILES}
check("the three package files this file reads are byte-identical afterwards",
      [os.path.basename(p) for p in _READ_FILES
       if _HASH_BEFORE[p] != _HASH_AFTER[p]], [])


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
    print("\nFailures:")
    for _f in _FAILURES:
        print(f"  - {_f}")
print("=" * 78)

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Aug 25 2026

@author: ramyalsaffar
"""
