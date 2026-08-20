# Stage 5 Packing and Cache Column Persistence Test
##################################################

"""
FOUR MEASUREMENTS WERE TAKEN AND THEN DROPPED AT THE DATABASE WRITE, AND THE
FAILURE RETURNS RECORDED ZERO TOKENS AGAINST CALLS THAT WERE BILLED.

Stage 5 computes ``llm_classifier_cached_input_tokens``,
``llm_classifier_call_details``, ``llm_classifier_packed_chunks`` and
``llm_classifier_packing``; ``TrialMatchState`` declares all four and
``_pipeline_provenance()`` carries them onto every result dict. The
``inferences`` table declared no column for any of them, so
``oncotriage/storage/database_logger.py`` wrote the columns it knew about and
dropped the rest in silence -- the comment in ``oncotriage/agent/terminal.py``
recorded that as a deferred schema decision. Separately, every one of Stage 5's
four early returns ended the node without a token figure, so
``_pipeline_provenance()``'s ``state.get(..., 0)`` supplied a zero and the row
recorded 0 input and 0 output tokens against requests that had been issued and
billed. Six such rows are in the production database, each carrying
``llm_classifier_retries = 3`` beside two zeros.

Both were fixed. The STRUCTURAL half of that fix is guarded by
``tests/test_storage_inference_logging_contract.py`` Test 2, which asserts by
AST that every one of the node's own dict returns reports its billed tokens
(literally, or through the ``**_billed_so_far()`` spread). THIS FILE IS THE
BEHAVIOURAL COMPLEMENT: it runs the writer and the node and reads back what
actually landed. Neither replaces the other -- an AST scan cannot see a value
that is carried and then serialized wrongly, and a round-trip cannot see a
return that was never written.

WHAT THIS FILE HOLDS
--------------------
    1. SCHEMA IDEMPOTENCE, on a fresh database and on a hand-built
       PRE-MIGRATION one. ``ALTER TABLE ADD COLUMN`` has no ``IF NOT EXISTS``
       form -- the ``PRAGMA table_info`` check IS the guard -- so "running it
       twice is a no-op" is a property that has to be driven rather than read.
       A pre-migration row keeps NULL in all four, which is the honest value:
       the measurement was not recorded, as opposed to having been recorded as
       zero.
    2. THE ROUND TRIP, through the REAL ``log_inference``, over four rows that
       differ only in how the four fields are supplied: real values, measured
       zeros, keys absent, explicit None. NULL and 0 must stay distinguishable
       on every one of them, and an EMPTY CONTAINER MUST SURVIVE AS ITSELF --
       ``[]`` is "the node ran and no call produced a usage object" and None is
       "the node was never entered", which is why the writer tests
       ``is not None`` rather than truthiness. ``json.dumps(None)`` is the
       string ``'null'``, which is neither, and no column may hold it.
    3. THE FAILURE PATHS, driven through the REAL node with the OpenAI client
       replaced through ``oncotriage/agent/deps.py``. A parse error, a non-list
       body and a refusal each carry the accumulators -- usage is read BEFORE
       the fence strip and the parse, so at all three the figure is exact and
       includes the offending response. A first-call API error carries nothing,
       because no usage object was ever obtained and an estimate would be a
       number no provider reported.
    4. THE SPLIT BATCH, which is the case the brief for the fix did not
       separate: an API error on a LATER chunk is not "no tokens are known", it
       is "the earlier chunks' tokens are known exactly". Driven with the input
       packer starved into two chunks, and NON-DEGENERACY IS ASSERTED FIRST --
       a scenario that silently did not split would otherwise assert the
       success path's tokens and pass for the wrong reason. That is not
       hypothetical: it is how the first version of this scenario behaved, in
       the scratch harness this file replaces, because it patched the budget on
       ``oncotriage.config`` while the packer reads a ``from ... import``
       binding in the evaluation module's own globals.
    5. FIVE NEGATIVE CONTROLS, each shown to move an observable. Four plants
       into an in-memory copy of ``oncotriage/storage/database_logger.py`` (the
       two ``is not None`` guards made truthy, the ``json.dumps`` guard removed
       so None becomes ``'null'``, the cached column defaulted to 0) and one
       into a copy of ``oncotriage/agent/evaluation.py`` (``_billed_so_far``
       reverted to ``{}``). Every control asserts BOTH what the broken arm
       produces AND that it differs from the shipped arm, so a plant that
       matched nothing fails rather than reporting a clean control.

WHY IT EXECS, when tests/test_storage_write_durability.py deliberately does
not. Its controls are conditions that can be created for real -- a lock, an
unwritable path, a deleted row. These are not: ``_billed_so_far`` is a function
NESTED inside the node, created per call, so there is no attribute to rebind
and no argument to vary; and the four writer plants are one-token edits INSIDE
a function body (``is not None`` made truthy, a ``.get()`` default added) to
code that exists at HEAD and nowhere else, so a blob of the revision before it
does not carry an inverted guard -- it carries no column at all.

THE FIFTH CONTROL COULD COME FROM GIT AND DELIBERATELY DOES NOT. Reverting
``_billed_so_far`` removes a whole feature rather than inverting a token, so a
DERIVED pre-fix revision would serve it. Using one would make this file need
git history for one control out of five, and a file that dies in a tree without
``.git`` -- a ``git archive`` export, a shallow or squashed clone -- is an open
defect in three existing files in this suite. A patched copy costs nothing here
and keeps "no git history" true. The entry is argued at ``_EXEC_ALLOWLIST`` in
tests/test_package_invariants.py.

NO NETWORK, NO KEYS, NO SPEND, NO GIT HISTORY, NO CORPUS, NO LIVE QDRANT. Every
database is a temporary file, every patient and trial is a literal dict, the
OpenAI client is a stand-in installed through the dependency seam and no other
stage is driven. Nothing in the repository is written: the two package files
this file reads are hashed at the start and compared at the end, and the
PRODUCTION inference database is read read-only and asserted unchanged.

WHY IT IS NOT IN THE COLLISION MATRIX, derived rather than assumed: it writes
only inside a temporary directory and patches no file in the repository. The
two files it READS -- ``oncotriage/storage/database_logger.py`` and
``oncotriage/agent/evaluation.py`` -- are written by neither of the suite's two
writers (``oncotriage/registries/cancer_code_registry.py`` and
``oncotriage/config.py``).

Run from terminal:
    python tests/test_storage_packing_and_cache_columns.py

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

import contextlib
import hashlib
import io
import json
import sqlite3
import tempfile
import types
from pathlib import Path

from oncotriage import paths as _paths
from oncotriage.agent import deps
from oncotriage.agent import evaluation as _ev
from oncotriage.storage import database_logger as _dl


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

    THIS IS NOT DEFENSIVE PADDING. Five files in this suite have shipped the
    same defect: a bare call inside a `check(...)` argument, where a planted
    defect raises, the exception escapes while the argument is being evaluated,
    and the run reports ONE TRACEBACK where it owed a summary and N results.
    The controls below deliberately break things, so every driver goes through
    here.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                       # noqa: BLE001 -- reported
        return {"__raised__": f"{type(exc).__name__}: {exc}"}


def digest(path):
    """sha256 of a file, or the string 'absent'."""
    if not os.path.exists(path):
        return "absent"
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def silence(fn, *args, **kwargs):
    """Run fn with BOTH output channels captured; return its value.

    The writer announces every ALTER TABLE it issues and every row it writes,
    and this file migrates six databases and drives four controls -- several
    hundred lines that would bury the PASS/FAIL output. Nothing suppressed here
    is asserted on: every assertion reads the DATABASE or the returned dict.
    """
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        return guarded(fn, *args, **kwargs)


def column_set(db):
    """The column names of `inferences`, read read-only.

    A plain sqlite3.connect on an absent path CREATES the file, so a check
    written that way would bring its own subject into existence.
    """
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return {r[1] for r in conn.execute("PRAGMA table_info(inferences)")}
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return f"unreadable: {exc}"


def row_for(db, patient_id):
    """One row as a dict, or a named absence. Never an IndexError in a check()."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        found = conn.execute(
            "SELECT * FROM inferences WHERE patient_id = ?", (patient_id,)).fetchone()
        return dict(found) if found is not None else {"__missing__": patient_id}
    finally:
        conn.close()


def count_where(db, sql):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM inferences WHERE {sql}").fetchone()[0]
    finally:
        conn.close()


#------------------------------------------------------------------------------


# ===========================================================================
# FIXTURES
# ===========================================================================

_TMP = tempfile.mkdtemp(prefix="oncotriage-packcols-")

_DL_PY = os.path.abspath(_dl.__file__)
_EV_PY = os.path.abspath(_ev.__file__)
_DL_DIGEST_BEFORE = digest(_DL_PY)
_EV_DIGEST_BEFORE = digest(_EV_PY)

# Read ONCE, read-only, so the "production is untouched" check at the end is a
# comparison against a value taken before anything ran.
_PROD_DB = _paths.inferences_path
_PROD_DIGEST_BEFORE = digest(_PROD_DB)

# The four columns this file is about, with the SQL type each is declared as.
NEW_COLUMNS = {
    "llm_classifier_cached_input_tokens": "INTEGER",
    "llm_classifier_call_details":        "TEXT",
    "llm_classifier_packed_chunks":       "INTEGER",
    "llm_classifier_packing":             "TEXT",
}
JSON_COLUMNS = ("llm_classifier_call_details", "llm_classifier_packing")


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
    "patient_id": "packcols-patient",
    "demographics": {"age": 61, "sex": "female", "birth_date": "1964-02-11"},
    "conditions": [], "medications": [], "allergies": [], "observations": [],
}

# A ledger with TWO entries whose cached readings differ, which is the shape the
# column exists for: one summed figure cannot say whether the shared prefix was
# served from cache on the second call, and this pair can.
LEDGER = [
    {"call_index": 1, "depth": 0, "trials": 8, "prompt_tokens": 9000,
     "completion_tokens": 1200, "cached_tokens": 0, "reasoning_tokens": 300,
     "finish_reason": "stop", "entries_emitted": 8},
    {"call_index": 2, "depth": 0, "trials": 7, "prompt_tokens": 8800,
     "completion_tokens": 1100, "cached_tokens": 8704, "reasoning_tokens": 280,
     "finish_reason": "stop", "entries_emitted": 7},
]
PACKING = {"estimator": "chars/CHARS_PER_TOKEN", "budget_tokens_configured": 100000,
           "budget_tokens_effective": 100000, "cap": 8,
           "cap_relaxed_budget": False, "over_budget_chunk": False,
           "chunks": [{"trials": 8, "tokens_estimated": 9000},
                      {"trials": 7, "tokens_estimated": 8800}],
           "prefix_sha256": "deadbeefdeadbeef"}


def result_dict(patient_id, **extra):
    """The minimum a terminal node emits that log_inference will accept."""
    base = {
        "patient_id": patient_id,
        "timestamp": "2026-08-20T00:00:00",
        "matching_model": "gpt-4o-2024-08-06",
        "llm_classifier_input_tokens": 17800,
        "llm_classifier_output_tokens": 2300,
        "matches": [], "near_misses": [], "not_evaluable": [],
        "stage_timings": {},
    }
    base.update(extra)
    return base


POPULATED = dict(llm_classifier_cached_input_tokens=8704,
                 llm_classifier_call_details=LEDGER,
                 llm_classifier_packed_chunks=2,
                 llm_classifier_packing=PACKING)
MEASURED_ZERO = dict(llm_classifier_cached_input_tokens=0,
                     llm_classifier_call_details=[],
                     llm_classifier_packed_chunks=0,
                     llm_classifier_packing={})
EXPLICIT_NONE = dict(llm_classifier_cached_input_tokens=None,
                     llm_classifier_call_details=None,
                     llm_classifier_packed_chunks=None,
                     llm_classifier_packing=None)


def write_four_rows(module, db, suffix=""):
    """Write the four shapes through `module`.log_inference. Returns the writes."""
    out = {}
    for label, extra in (("populated", POPULATED), ("zero", MEASURED_ZERO),
                         ("absent", {}), ("none", EXPLICIT_NONE)):
        pid = f"{label}{suffix}"
        out[label] = silence(module.log_inference,
                             result_dict(pid, **extra), dict(PATIENT), db_path=db)
    return out


def exec_copy(source_path, module_name, package, mutate):
    """Exec a MUTATED in-memory copy of a package module.

    A real ModuleType, not a dict-backed stand-in: a function's globals ARE the
    dict it was exec'd into, so a throwaway namespace would leave every module
    constant unread. `mutate` receives the source text and returns it changed;
    it is expected to assert its own match count, so a plant that matched
    nothing raises here rather than producing a control that quietly agrees
    with the shipped code.
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
# THE STAND-IN STAGE 5 CLIENT
# ===========================================================================
#
# `model` is None on every response so the answering-model check
# (MatchingModelMismatchError) is skipped: it is a different mechanism with its
# own test, and it raises OUT of the node, past every return this file is about.

PROMPT_TOKENS = 9000
COMPLETION_TOKENS = 1200


class _PromptDetails:
    def __init__(self, cached):
        self.cached_tokens = cached


class _CompletionDetails:
    reasoning_tokens = 300


class _Usage:
    def __init__(self, cached):
        self.prompt_tokens = PROMPT_TOKENS
        self.completion_tokens = COMPLETION_TOKENS
        self.total_tokens = PROMPT_TOKENS + COMPLETION_TOKENS
        self.completion_tokens_details = _CompletionDetails()
        if cached is not None:
            self.prompt_tokens_details = _PromptDetails(cached)


class _StubOpenAI:
    """Serves one canned Stage 5 answer per call and counts the requests.

    `bodies` is consumed one entry per call. An entry that IS an exception is
    RAISED rather than returned, which is how the API-error return is reached
    without a network and without a key.
    """

    def __init__(self, bodies, cached=512):
        self.bodies = list(bodies)
        self.cached = cached
        self.calls = 0
        self.requests = []
        outer = self

        class _completions:
            @staticmethod
            def create(**kwargs):
                index = outer.calls
                outer.calls += 1
                outer.requests.append(kwargs)
                body = outer.bodies[min(index, len(outer.bodies) - 1)]
                if isinstance(body, BaseException):
                    raise body
                return outer._completion(body)

        class _chat:
            completions = _completions

        self.chat = _chat

    def _completion(self, body):
        refusal = body[len("REFUSE:"):] if str(body).startswith("REFUSE:") else None

        class _Msg:
            content = None if refusal else body
            # Present on every message, because _refusal_text reads it through
            # getattr and "absent" is deliberately "not refused".
            pass

        _Msg.refusal = refusal

        class _Choice:
            message = _Msg()
            finish_reason = "stop"

        class _Completion:
            choices = [_Choice()]
            usage = _Usage(self.cached)
            model = None

        return _Completion()


NCTS = ("NCT90000001", "NCT90000002")


def trial(nct):
    return {"trial": {"nct_id": nct, "title": f"Trial {nct}", "phase": "Phase 2",
                      "conditions": ["Breast Neoplasms"],
                      "eligibility": {"inclusion_criteria": ["adult"],
                                      "exclusion_criteria": ["pregnancy"]}},
            "rerank_score": 0.5, "rerank_score_raw": 0.5, "medcpt_score_max": 0.5}


def stage5_state():
    return {"patient_data": dict(PATIENT),
            "filtered_trials": [trial(n) for n in NCTS],
            "stage_timings": {}}


def ok_body(ncts):
    return json.dumps([
        {"nct_id": n, "eligible": "eligible", "assessment": "ok",
         "inclusion_criteria": [{"criterion": "adult", "patient_value": "61",
                                 "status": "met"}],
         "exclusion_criteria": []} for n in ncts])


def run_stage5(node, stub):
    """Drive `node` with `stub` installed for the block, restored afterwards.

    THE SCOPED FORM IS LOAD-BEARING AND A BARE set_override IS A DEFECT HERE.
    Every scenario below deliberately makes the node fail, and one of them makes
    the client RAISE. A bare set_override followed by a raise would leave the
    stand-in installed for every later section of this process -- including the
    round-trip section, which asserts on a real writer -- and the resulting
    failures would name the wrong subject. deps.override restores in __exit__
    whether the block returns or raises.
    """
    with deps.override(deps.OPENAI_CLIENT, stub):
        return silence(node, stage5_state())


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 1 -- SCHEMA IDEMPOTENCE, FRESH AND PRE-MIGRATION
# ===========================================================================

print("=" * 70)
print("SECTION 1 -- the migration is additive and idempotent")
print("=" * 70)

# NON-DEGENERATE FIRST: the scratch path is not the production one, and the
# resolver's default IS the production one. Without both, every isolation claim
# below is satisfied by a test that never pointed anywhere.
_DB = fresh_db("roundtrip.db")
check("the scratch database is not the production one",
      os.path.abspath(_DB) == os.path.abspath(_PROD_DB), False)
check("...and resolve_inference_db_path(None) IS the production one",
      os.path.abspath(_dl.resolve_inference_db_path(None)) ==
      os.path.abspath(_PROD_DB), True)

_first = silence(_dl.initialize_database, _DB)
_cols_after_first = column_set(_DB)
_second = silence(_dl.initialize_database, _DB)
_cols_after_second = column_set(_DB)

check("initialize_database returned the same path both times", _first, _second)
check("the second run raised nothing",
      isinstance(_second, dict) and "__raised__" in _second, False)
check("the column set is unchanged by the second run",
      _cols_after_first == _cols_after_second, True)
for _name in NEW_COLUMNS:
    check(f"inferences.{_name} exists after migration",
          _name in _cols_after_second, True)
check("all four are declared in INFERENCE_COLUMN_ADDITIONS with these types",
      {k: _dl.INFERENCE_COLUMN_ADDITIONS.get(k) for k in NEW_COLUMNS},
      dict(NEW_COLUMNS))

print("\n  1b. a PRE-MIGRATION database, built without the four columns")
#
# Hand-built rather than copied from the shipped CREATE TABLE, because the point
# is a database written before these columns existed. A row is inserted BEFORE
# the migration so the NULL assertion below is about a row that predates it.
_LEGACY = fresh_db("legacy.db")
_lc = sqlite3.connect(_LEGACY)
_lc.execute("CREATE TABLE inferences ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, patient_id TEXT NOT NULL, "
            "timestamp TEXT NOT NULL, age INTEGER)")
_lc.execute("INSERT INTO inferences (patient_id, timestamp, age) "
            "VALUES ('pre-migration', '2026-01-01', 60)")
_lc.commit()
_lc.close()

_legacy_before = column_set(_LEGACY)
check("the legacy database starts without any of the four",
      sorted(set(NEW_COLUMNS) & _legacy_before), [])

silence(_dl.initialize_database, _LEGACY)
_legacy_once = column_set(_LEGACY)
_second_legacy = silence(_dl.initialize_database, _LEGACY)
check("migrating a legacy database twice raises nothing",
      isinstance(_second_legacy, dict) and "__raised__" in _second_legacy, False)
check("...and the second pass adds nothing", _legacy_once == column_set(_LEGACY), True)
for _name in NEW_COLUMNS:
    check(f"migration added inferences.{_name}", _name in _legacy_once, True)

_pre = row_for(_LEGACY, "pre-migration")
for _name in NEW_COLUMNS:
    check(f"the pre-migration row keeps NULL in {_name}", _pre.get(_name, "<absent>"), None)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 2 -- THE ROUND TRIP: NULL, 0 AND THE EMPTY CONTAINER
# ===========================================================================

print()
print("=" * 70)
print("SECTION 2 -- four rows through the real log_inference")
print("=" * 70)

_writes = write_four_rows(_dl, _DB)
for _label, _outcome in _writes.items():
    check(f"{_label}: the write reported success",
          (getattr(_outcome, "ok", None), _outcome == _DB), (True, True))

_ROW = {label: row_for(_DB, label) for label in _writes}

print("\n  2a. cached_input_tokens: a subset figure, never a cost term")
check("a real reading round-trips",
      _ROW["populated"].get("llm_classifier_cached_input_tokens"), 8704)
check("a MEASURED zero is stored as 0, not folded into NULL",
      _ROW["zero"].get("llm_classifier_cached_input_tokens"), 0)
check("an absent key stores NULL",
      _ROW["absent"].get("llm_classifier_cached_input_tokens", "<absent>"), None)
check("an explicit None stores NULL",
      _ROW["none"].get("llm_classifier_cached_input_tokens", "<absent>"), None)
# The whole point of the column, as one predicate: 0 and NULL are different
# rows. `is not` rather than `!=` because 0 == None is already False and would
# pass even if both were None.
check("0 and NULL are distinguishable in the table",
      _ROW["zero"].get("llm_classifier_cached_input_tokens")
      is _ROW["none"].get("llm_classifier_cached_input_tokens"), False)

print("\n  2b. the per-call ledger: an empty list is NOT an absent one")
check("the ledger round-trips to the same object",
      json.loads(_ROW["populated"].get("llm_classifier_call_details") or "null"),
      LEDGER)
check("...and the two calls' cached readings are preserved separately, which "
      "is the fact a summed figure cannot carry",
      [e["cached_tokens"] for e in
       json.loads(_ROW["populated"].get("llm_classifier_call_details") or "[]")],
      [0, 8704])
check("an EMPTY ledger is stored as '[]'",
      _ROW["zero"].get("llm_classifier_call_details"), "[]")
check("an absent ledger stores NULL",
      _ROW["absent"].get("llm_classifier_call_details", "<absent>"), None)
check("an explicit None stores NULL",
      _ROW["none"].get("llm_classifier_call_details", "<absent>"), None)

print("\n  2c. packed_chunks and the packing report")
check("packed_chunks round-trips",
      _ROW["populated"].get("llm_classifier_packed_chunks"), 2)
check("a measured 0 stays 0", _ROW["zero"].get("llm_classifier_packed_chunks"), 0)
check("an absent key stores NULL",
      _ROW["absent"].get("llm_classifier_packed_chunks", "<absent>"), None)
check("the packing report round-trips",
      json.loads(_ROW["populated"].get("llm_classifier_packing") or "null"), PACKING)
check("an EMPTY report is stored as '{}'",
      _ROW["zero"].get("llm_classifier_packing"), "{}")
check("an absent report stores NULL",
      _ROW["absent"].get("llm_classifier_packing", "<absent>"), None)
check("an explicit None stores NULL",
      _ROW["none"].get("llm_classifier_packing", "<absent>"), None)

print("\n  2d. json.dumps(None) is the string 'null' and no column may hold it")
for _col in JSON_COLUMNS + ("retrieval_channels", "ablation_flags"):
    check(f"no row stores the string 'null' in {_col}",
          count_where(_DB, f"{_col} = 'null'"), 0)
check("SQL IS NULL finds exactly the two rows that measured nothing",
      count_where(_DB, "llm_classifier_call_details IS NULL"), 2)

print("\n  2e. the cached figure is not priced")
#
# get_model_cost() charges the whole input at the uncached rate, deliberately,
# so that stored costs stay comparable with every historical row. Two rows with
# identical token counts and different cached readings must price identically.
check("non-degenerate: the two rows really do carry the same token counts",
      (_ROW["populated"].get("llm_classifier_input_tokens"),
       _ROW["absent"].get("llm_classifier_input_tokens")), (17800, 17800))
check("...and different cached readings",
      _ROW["populated"].get("llm_classifier_cached_input_tokens")
      == _ROW["absent"].get("llm_classifier_cached_input_tokens"), False)
check("the cached reading does not move estimated_cost_usd",
      _ROW["populated"].get("estimated_cost_usd"),
      _ROW["absent"].get("estimated_cost_usd"))
check("...and the price is non-degenerate (not 0.0)",
      (_ROW["populated"].get("estimated_cost_usd") or 0) > 0, True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 3 -- THE FAILURE RETURNS CARRY WHAT THEY WERE BILLED
# ===========================================================================

print()
print("=" * 70)
print("SECTION 3 -- Stage 5 failure paths, driven through the deps seam")
print("=" * 70)

# (label, stub bodies, how many calls will produce a usage object)
_FAILURE_SCENARIOS = (
    ("parse error", ["not json at all {{"], 1),
    ("non-list JSON", ['{"not": "a list"}'], 1),
    ("refusal", ["REFUSE:I cannot help with that."], 1),
    ("API error on the FIRST call", [RuntimeError("connection reset")], 0),
)

for _label, _bodies, _billed in _FAILURE_SCENARIOS:
    print(f"\n  -- {_label} --")
    _stub = _StubOpenAI(_bodies)
    _out = run_stage5(_ev.node_llm_classifier_evaluation, _stub)
    if not isinstance(_out, dict) or "__raised__" in _out:
        fail(f"{_label}: the node raised instead of returning", str(_out))
        continue

    # NON-DEGENERACY: the scenario must have produced the failure it names, and
    # must have issued the request it claims to have issued. Without both, the
    # token assertions below can be satisfied by a run that quietly succeeded.
    check(f"{_label}: a request was actually issued", _stub.calls >= 1, True)
    check(f"{_label}: the node really failed", bool(_out.get("error")), True)

    check(f"{_label}: the per-call ledger is present",
          isinstance(_out.get("llm_classifier_call_details"), list), True)
    check(f"{_label}: the ledger holds one entry per call that returned usage",
          len(_out.get("llm_classifier_call_details") or []), _billed)

    if _billed:
        check(f"{_label}: input tokens carried",
              _out.get("llm_classifier_input_tokens", "<absent>"),
              PROMPT_TOKENS * _billed)
        check(f"{_label}: output tokens carried",
              _out.get("llm_classifier_output_tokens", "<absent>"),
              COMPLETION_TOKENS * _billed)
        check(f"{_label}: the call count is carried too",
              _out.get("llm_classifier_calls", "<absent>"), _billed)
        # A token figure with no model beside it is the one shape File 16's
        # Query 10, the dashboard cost tab and run_harness.price_result all
        # refuse to price.
        check(f"{_label}: a matching_model key is present beside the tokens",
              "matching_model" in _out, True)
    else:
        # NO USAGE OBJECT WAS EVER OBTAINED. The keys are left ABSENT rather
        # than written as 0: the tokens that request may have been billed are
        # unknown to this process, and an estimate from prompt length would put
        # a number no provider reported into a measurement column.
        check(f"{_label}: input tokens ABSENT, not zero",
              _out.get("llm_classifier_input_tokens", "<absent>"), "<absent>")
        check(f"{_label}: output tokens ABSENT, not zero",
              _out.get("llm_classifier_output_tokens", "<absent>"), "<absent>")

print("\n  3e. the split batch: call 1 answers, call 2 raises")
#
# THE CASE THE INSTRUCTION SET FOR THE FIX DID NOT SEPARATE. An API error is not
# one population: on the first request nothing is known, and on a LATER chunk
# the earlier chunks' tokens are known exactly. The packer is starved into one
# chunk per trial to produce the second.
#
# THE BUDGET IS PATCHED ON THE EVALUATION MODULE, NOT ON oncotriage.config.
# The packer reads a `from oncotriage.config import ...` binding out of this
# module's own globals, so patching config reaches nothing -- measured: the
# first version of this scenario made ONE call, the run SUCCEEDED, and the
# token assertion passed against the success return.
_prev_budget = _ev.MATCHING_INPUT_TOKEN_BUDGET
try:
    _ev.MATCHING_INPUT_TOKEN_BUDGET = 1
    _split_stub = _StubOpenAI([ok_body([NCTS[0]]), RuntimeError("timeout on chunk 2")])
    _split = run_stage5(_ev.node_llm_classifier_evaluation, _split_stub)
finally:
    _ev.MATCHING_INPUT_TOKEN_BUDGET = _prev_budget

if not isinstance(_split, dict) or "__raised__" in _split:
    fail("split batch: the node raised instead of returning", str(_split))
else:
    check("non-degenerate: TWO requests were attempted", _split_stub.calls, 2)
    check("non-degenerate: the run really failed", bool(_split.get("error")), True)
    check("the first call's input tokens survive the second call's raise",
          _split.get("llm_classifier_input_tokens", "<absent>"), PROMPT_TOKENS)
    check("...and its output tokens",
          _split.get("llm_classifier_output_tokens", "<absent>"), COMPLETION_TOKENS)
    check("the call count is 1: one response was received, two were issued",
          _split.get("llm_classifier_calls", "<absent>"), 1)
    check("the ledger holds exactly the billed call",
          len(_split.get("llm_classifier_call_details") or []), 1)

print("\n  3f. the seam was restored, so no later section talks to a stand-in")
check("no OpenAI override is left installed",
      deps.OPENAI_CLIENT in deps.active_overrides(), False)

print("\n  3g. and the failure row REACHES THE DATABASE with its tokens")
#
# Section 3 asserts what the node returns; this asserts what is stored, which is
# the whole point of the pass. node_error_handler is the terminal node a
# retries-exhausted run ends at, and it reads these keys off state.
_FAILDB = fresh_db("failure.db")
_stub_pe = _StubOpenAI(["not json at all {{"])
_pe = run_stage5(_ev.node_llm_classifier_evaluation, _stub_pe)
_fail_result = result_dict("failed-parse",
                           llm_classifier_input_tokens=_pe.get("llm_classifier_input_tokens", 0),
                           llm_classifier_output_tokens=_pe.get("llm_classifier_output_tokens", 0),
                           llm_classifier_call_details=_pe.get("llm_classifier_call_details"),
                           llm_classifier_cached_input_tokens=None,
                           error=_pe.get("error", ""))
silence(_dl.log_inference, _fail_result, dict(PATIENT), db_path=_FAILDB)
_failrow = row_for(_FAILDB, "failed-parse")
check("the stored row records the billed input tokens, not 0",
      _failrow.get("llm_classifier_input_tokens"), PROMPT_TOKENS)
check("...and the billed output tokens",
      _failrow.get("llm_classifier_output_tokens"), COMPLETION_TOKENS)
check("...and the ledger, so the cache reading of a failed run is recoverable",
      [e["cached_tokens"] for e in
       json.loads(_failrow.get("llm_classifier_call_details") or "[]")], [512])
check("...while the summed cached total stays NULL on a failure row, because "
      "the ledger is where a failed run's readings live",
      _failrow.get("llm_classifier_cached_input_tokens", "<absent>"), None)
check("...and the run is priced above zero, because it was billed",
      (_failrow.get("estimated_cost_usd") or 0) > 0, True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 4 -- NEGATIVE CONTROLS
# ===========================================================================

print()
print("=" * 70)
print("SECTION 4 -- every assertion above is shown to be able to fail")
print("=" * 70)

print("\n  4a. _billed_so_far reverted to {}: the failure returns lose the tokens")
_ev_reverted = guarded(
    exec_copy, _EV_PY, "packcols_evaluation_reverted", "oncotriage.agent",
    lambda t: sub(t,
                  '        return {\n'
                  '            "llm_classifier_input_tokens": input_tokens,\n'
                  '            "llm_classifier_output_tokens": output_tokens,\n'
                  '            "llm_classifier_calls": calls_made,\n'
                  '        }',
                  '        return {}', 1))
if isinstance(_ev_reverted, dict):
    fail("the _billed_so_far revert could not be planted", str(_ev_reverted))
else:
    for _label, _bodies, _billed in _FAILURE_SCENARIOS:
        if not _billed:
            continue                       # already absent in the shipped arm
        _ctl = run_stage5(_ev_reverted.node_llm_classifier_evaluation,
                          _StubOpenAI(_bodies))
        check(f"control [{_label}]: the reverted node still fails the same way",
              bool(_ctl.get("error")), True)
        check(f"control [{_label}]: ...and now reports NO input tokens, so the "
              f"shipped assertion above can fail",
              _ctl.get("llm_classifier_input_tokens", "<absent>"), "<absent>")
        check(f"control [{_label}]: ...and no call count",
              _ctl.get("llm_classifier_calls", "<absent>"), "<absent>")
        # The ledger is written OUTSIDE the helper and must be unaffected, or
        # the control is moving more than the one thing it claims to move.
        check(f"control [{_label}]: the ledger is untouched by this revert",
              len(_ctl.get("llm_classifier_call_details") or []), _billed)

print("\n  4b. the writer's `is not None` guards made truthy: [] and {} vanish")
_dl_truthy = guarded(
    exec_copy, _DL_PY, "packcols_dl_truthy", "oncotriage.storage",
    lambda t: sub(sub(t,
                      'if result.get("llm_classifier_call_details") is not None else None',
                      'if result.get("llm_classifier_call_details") else None', 1),
                  'if result.get("llm_classifier_packing") is not None else None',
                  'if result.get("llm_classifier_packing") else None', 1))
if isinstance(_dl_truthy, dict):
    fail("the truthiness plant could not be applied", str(_dl_truthy))
else:
    _DB_T = fresh_db("control_truthy.db")
    write_four_rows(_dl_truthy, _DB_T)
    _t_zero = row_for(_DB_T, "zero")
    check("control: with truthiness, an EMPTY ledger stores NULL",
          _t_zero.get("llm_classifier_call_details", "<absent>"), None)
    check("control: ...and an empty packing report stores NULL",
          _t_zero.get("llm_classifier_packing", "<absent>"), None)
    check("control: which DIFFERS from the shipped writer, so 2b/2c can fail",
          (_t_zero.get("llm_classifier_call_details"),
           _ROW["zero"].get("llm_classifier_call_details")) == ("[]", "[]"), False)
    # ...and the plant moved nothing else it was not aimed at.
    check("control: a populated ledger is unaffected by this plant",
          json.loads(row_for(_DB_T, "populated").get("llm_classifier_call_details")
                     or "null"), LEDGER)

print("\n  4c. the json.dumps guard removed: None becomes the string 'null'")
_dl_null = guarded(
    exec_copy, _DL_PY, "packcols_dl_null", "oncotriage.storage",
    lambda t: sub(t,
                  '            (json.dumps(result["llm_classifier_call_details"])\n'
                  '             if result.get("llm_classifier_call_details") is not None else None),',
                  '            json.dumps(result.get("llm_classifier_call_details")),', 1))
if isinstance(_dl_null, dict):
    fail("the 'null' plant could not be applied", str(_dl_null))
else:
    _DB_N = fresh_db("control_null.db")
    write_four_rows(_dl_null, _DB_N)
    check("control: an absent ledger now stores the STRING 'null'",
          row_for(_DB_N, "absent").get("llm_classifier_call_details"), "null")
    check("control: so the shipped 2d assertion would find a row",
          count_where(_DB_N, "llm_classifier_call_details = 'null'") > 0, True)
    check("control: ...and IS NULL now finds none of them, which is the whole "
          "harm -- a query for unmeasured runs silently returns nothing",
          count_where(_DB_N, "llm_classifier_call_details IS NULL"), 0)

print("\n  4d. the cached column defaulted to 0: absence becomes a measurement")
_dl_zero = guarded(
    exec_copy, _DL_PY, "packcols_dl_zero", "oncotriage.storage",
    lambda t: sub(t,
                  '            result.get("llm_classifier_cached_input_tokens"),',
                  '            result.get("llm_classifier_cached_input_tokens", 0),', 1))
if isinstance(_dl_zero, dict):
    fail("the cached-default plant could not be applied", str(_dl_zero))
else:
    _DB_Z = fresh_db("control_zero.db")
    write_four_rows(_dl_zero, _DB_Z)
    check("control: an absent cached key now stores 0, not NULL",
          row_for(_DB_Z, "absent").get("llm_classifier_cached_input_tokens"), 0)
    check("control: so a measured zero and an absent reading are no longer "
          "distinguishable, which is what 2a asserts they are",
          row_for(_DB_Z, "absent").get("llm_classifier_cached_input_tokens")
          == row_for(_DB_Z, "zero").get("llm_classifier_cached_input_tokens"), True)
    # An explicit None still stores NULL under this plant -- .get's default only
    # applies to an ABSENT key -- so the plant is narrower than "everything
    # becomes 0", and saying so is what keeps 4d honest about what it proved.
    check("control: an explicit None still stores NULL under this plant, so it "
          "is the ABSENT case the default destroys",
          row_for(_DB_Z, "none").get("llm_classifier_cached_input_tokens", "<absent>"),
          None)

print("\n  4e. the migration entry removed: the column is never created")
_dl_nocol = guarded(
    exec_copy, _DL_PY, "packcols_dl_nocol", "oncotriage.storage",
    lambda t: sub(t, '    "llm_classifier_packed_chunks":          "INTEGER",\n', "", 1))
if isinstance(_dl_nocol, dict):
    fail("the migration-entry plant could not be applied", str(_dl_nocol))
else:
    check("control: the plant really removed the declaration",
          "llm_classifier_packed_chunks" in _dl_nocol.INFERENCE_COLUMN_ADDITIONS, False)
    _DB_C = fresh_db("control_nocol.db")
    silence(_dl_nocol.initialize_database, _DB_C)
    check("control: so the column is absent from a database it migrates, and "
          "section 1's existence check can fail",
          "llm_classifier_packed_chunks" in column_set(_DB_C), False)
    # The INSERT still names the column, so the write now fails outright rather
    # than silently dropping the value -- which is the better of the two, and is
    # asserted so that a future migration edit cannot make it the quiet one.
    _out_c = write_four_rows(_dl_nocol, _DB_C)
    check("control: and the write REPORTS the failure rather than losing the "
          "row silently",
          getattr(_out_c["populated"], "ok", "<no ok attribute>"), False)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 5 -- HYGIENE: NOTHING IN THE REPOSITORY OR THE DATA TREE MOVED
# ===========================================================================

print()
print("=" * 70)
print("SECTION 5 -- no source file and no production data was touched")
print("=" * 70)

check("oncotriage/storage/database_logger.py is byte-identical",
      digest(_DL_PY), _DL_DIGEST_BEFORE)
check("oncotriage/agent/evaluation.py is byte-identical",
      digest(_EV_PY), _EV_DIGEST_BEFORE)
check("the production inference database is byte-identical",
      digest(_PROD_DB), _PROD_DIGEST_BEFORE)
for _suffix in ("-wal", "-shm"):
    check(f"no {_suffix} file was created beside the production database",
          os.path.exists(_PROD_DB + _suffix), False)
check("every database this file wrote is inside the scratch directory",
      sorted({os.path.commonpath([os.path.abspath(p), _TMP])
              for p in (_DB, _LEGACY, _FAILDB)}), [_TMP])


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
print(f"\nScratch directory: {_TMP}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug 20 12:00:00 2026

@author: ramyalsaffar
"""
