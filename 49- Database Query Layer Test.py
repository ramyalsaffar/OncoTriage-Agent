# Database Query Layer Test
###########################

"""
Database Query Layer Test  (item 38)

WHY THIS FILE COULD NOT HAVE EXISTED BEFORE
-------------------------------------------
"16- Database Query.py" was 915 lines of top-level statements, so there was no
way to run one query, no way to get a frame back instead of a printed table, and
no way to run any of it against anything but the production database. Pass 20c-3b
turned the queries into a registry with ``run()`` / ``run_all()`` / ``report()``,
all of which return data and none of which resolve a path unless asked. That is
what makes the checks below possible; item 38 is the first pass to use it.

It also matters that the sweep could not previously have COMPLETED. File 16's
Query 19 selected two columns that do not exist in `inferences`, raised
"no such column", and took the process with it -- so no query after it in the
registry had ever executed, in any invocation of File 16, ever. The first check
in section 2 is therefore not a formality: it is the first time every query in
this project has been run.

WHAT ITEM 38 CHANGED, and what each section here holds it to
-----------------------------------------------------------
  1. ``expansion_token_efficiency`` DELETED, not repaired. Stage 1 is
     deterministic and issues no LLM call, so there are no expansion tokens; the
     answerable version of the question is already ``expansion_stage_stats``.
  2. ``pipeline_consistency`` repaired: the stray WHEN removed (proved to be a
     duplicate of the one inside the CASE, so the logic is unchanged), the two
     hardcoded pipeline sizes resolved from ``oncotriage/config.py``, `!=`
     replaced by `>` because both numbers are CAPS, the count identity corrected
     to include ``not_evaluable_trials``, and a row whose counters are NULL
     flagged instead of silently reported clean.
  3. The per-model cost arithmetic reduced to ONE copy,
     ``queries.price_model_groups``, fed by the SQL GROUP BY on one side and by
     ``oncotriage/dashboard/tabs/cost_tokens.py`` on the other. Its four null
     tests all use ``pd.isna``; two of them used to be ``int(x or 0)``, which
     raises ValueError on ``nan``, and one used to be ``is None``, which never
     fires once a column is float64.
  4. Two custom renderers that raised on an empty or partly-NULL table fixed.

Sections:
    1. The seeded temporary database: real schema from ``initialize_database``,
       rows chosen to exercise every hard case at once.
    2. EVERY query in the registry executes, and every one returns a NON-EMPTY
       frame on the seeded data -- so a query that silently returns nothing
       cannot pass as fixed. Then ``report()`` end to end.
    3. The deleted expansion query: gone from the registry, its columns absent
       from the schema, and the pre-fix SQL shown still to raise.
    4. The consistency query: the stray WHEN proved redundant against the
       PRE-FIX TEXT READ OUT OF GIT rather than retyped here; the pre-fix SQL
       shown to be a syntax error; the bounds shown to come from config and to
       be derived from the slices that produce the columns; and a flagged row
       and an unflagged row for every branch.
    5. The cost arithmetic on the float64 case, with the pre-fix function
       EXTRACTED FROM GIT AND EXEC'd as the negative control.
    6. The dashboard consumes the query layer: identical frames, function
       identity, and a structural check that no second copy remains.
    7. The two custom renderers against an empty database, with the pre-fix
       renderer shown to raise.
    8. Neither docstring still claims the two queries are broken on purpose,
       with the scan shown to find the claim in the pre-fix text.
    9. The production database was never opened for writing and its row count
       is unchanged.

No network, no LLM, no API key, no Qdrant. Everything runs against a SQLite file
in a temporary directory that is removed at the end. The production database is
opened only through a ``mode=ro`` URI, and only to count rows.

Run from terminal (or F5 in Spyder):
    python "49- Database Query Layer Test.py"

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures

CONCURRENCY: it does NOT belong in run_serial_tests.py's collision matrix. That
matrix exists for Files 42, 43, 44 and 47, every one of which MUTATES A FILE IN
THE REPOSITORY -- planting defects into the registry, rewriting the
snapshot-date literal, or copytree()ing the package. This file writes only into
a fresh temporary directory, reads the repository's source text without
modifying a byte of it, and reads history through ``git show``, which touches no
working-tree file. Two copies of it could run at once.

The four source files it ASSERTS on -- queries.py, cost_tokens.py,
agent/retrieval.py, agent/terminal.py -- are not mutated by any of those four,
so nothing it checks can be caught mid-edit. It does IMPORT the package, which
means it shares with Files 30-41 the ordinary hazard of reading config.py or
cancer_code_registry.py inside File 44's or File 43's restore window. That is a
property of importing at all rather than a collision the matrix is for, and it
is why the four that MUTATE are the four that are serialized.
"""


# Run needed file
#----------------
import ast
import io
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile


# Make the oncotriage package importable
#---------------------------------------
# The same six-line block Files 04, 06, 11, 12 and 48 carry. `pip install -e .`
# makes it a no-op; without it the code directory goes on sys.path and the fact
# is printed rather than left silent.
try:
    import oncotriage  # noqa: F401
except ImportError:
    for _candidate, _how in (
        (os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals()
         else None, "__file__"),
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

import pandas as pd

from oncotriage.config import RRF_POOL_SIZE, TOP_K_CANDIDATES
from oncotriage.storage import queries
from oncotriage.storage.database_logger import initialize_database
from oncotriage.utils import UnknownModelPricingError, get_model_cost


_CODE_DIR = (os.path.dirname(os.path.abspath(__file__))
             if "__file__" in globals() else os.getcwd())

# The two priced models the seed uses. Read out of PRICING_CONFIG rather than
# written here, so this file cannot drift from the pricing table and cannot
# accidentally seed a model the arithmetic would refuse.
_PRICED_MODELS = sorted(queries.PRICING_CONFIG["models"])


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================
#
# The same shape as "48- Degraded Dependency Test.py"'s, deliberately: a check
# that aborts the run hides every check after it, which is the exact failure
# this file exists to have removed from File 16.

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label: str, actual, expected) -> None:
    """Assert equality, record the outcome, never abort the run."""
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(
            f"{label}\n          expected: {expected}\n          actual:   {actual}"
        )
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


def check_true(label: str, condition) -> None:
    check(label, bool(condition), True)


def check_raises(label: str, exc_type, fn, *args, **kwargs):
    """Assert `fn` raises `exc_type`. Returns the exception, or None.

    BOTH branches record and print, so this helper is never itself a silent
    handler.
    """
    try:
        fn(*args, **kwargs)
    except exc_type as exc:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
        return exc
    except Exception as exc:  # noqa: BLE001 - reporting the wrong type IS the point
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          raised {type(exc).__name__}: {exc}\n"
                         f"          expected {exc_type.__name__}")
        print(f"  FAIL  {label} — raised {type(exc).__name__}: {exc}")
        return None
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          nothing was raised")
    print(f"  FAIL  {label} — nothing was raised")
    return None


def check_does_not_raise(label: str, fn, *args, **kwargs):
    """Assert `fn` returns. Returns its value, or None.

    THE OTHER HALF OF EVERY RAISE IN THIS FILE. A check that only ever shows a
    raise firing cannot distinguish "fires on the broken input" from "fires on
    everything".
    """
    try:
        value = fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          raised {type(exc).__name__}: {exc}")
        print(f"  FAIL  {label} — raised {type(exc).__name__}: {exc}")
        return None
    _RESULTS["passed"] += 1
    print(f"  PASS  {label}")
    return value


class quiet:
    """Swallow stdout for a block. initialize_database prints a migration line
    per added column and report() prints ~40 tables; neither is under test."""

    def __enter__(self):
        self._saved = sys.stdout
        sys.stdout = io.StringIO()
        return self

    def __exit__(self, *exc):
        sys.stdout = self._saved
        return False


# ===========================================================================
# READING THE PRE-FIX SOURCE OUT OF GIT
# ===========================================================================
#
# EVERY NEGATIVE CONTROL IN THIS FILE USES THE REAL PRE-FIX TEXT, NOT A COPY OF
# IT TYPED HERE. That is not fastidiousness. The claim "removing the stray WHEN
# changes no logic" rests on the stray line being character-for-character the
# line already inside the CASE; if this file carried its own transcription of
# both lines, the check would compare my typing against my typing and agree by
# construction -- which is exactly the defect CLAUDE.md records File 42's
# boundary assertions having shipped with.
#
# THE COMMIT IS DERIVED, NOT DECLARED, AND THAT IS WHAT LETS THIS FILE SURVIVE
# BEING COMMITTED. `HEAD` is the pre-fix version only until item 38 lands, after
# which it is the fixed one and every control here would silently stop
# controlling anything. So: walk the file's history newest-first and take the
# first revision whose blob still contains the broken column name. Before the
# fix is committed that is HEAD; afterwards it is HEAD's parent; in ten commits'
# time it is still the same blob.

_QUERIES_REL = "oncotriage/storage/queries.py"
_BROKEN_MARKER = "expansion_input_tokens"


def _git(*args):
    """Run git in the code directory and return stdout, or None on any failure."""
    try:
        completed = subprocess.run(
            ["git", *args], cwd=_CODE_DIR, capture_output=True, text=True)
    except (OSError, ValueError) as exc:            # git absent, bad argv
        print(f"  [git] {args[0]} unavailable: {type(exc).__name__}: {exc}")
        return None
    if completed.returncode != 0:
        # Recorded rather than swallowed -- this project does not allow a
        # handler that leaves no trace, and a missing control must be visible.
        print(f"  [git] {' '.join(args[:2])} failed (exit "
              f"{completed.returncode}): {completed.stderr.strip()[:200]}")
        return None
    return completed.stdout


def _pre_fix_queries_source():
    """The newest committed queries.py that still contains the broken query.

    Returns (revision, source) or (None, None). A failure here is reported as a
    FAILED check by the caller rather than skipped: this repository has the
    history, and a control that quietly does not run is worse than one that
    fails.
    """
    log = _git("log", "--format=%H", "--", _QUERIES_REL)
    if not log:
        return None, None
    for rev in log.split():
        blob = _git("show", f"{rev}:{_QUERIES_REL}")
        if blob and _BROKEN_MARKER in blob:
            return rev, blob
    return None, None


_PRE_FIX_REV, _PRE_FIX_SRC = _pre_fix_queries_source()


def _pre_fix_string_constant(name_hint, must_contain):
    """Pull one SQL string constant out of the pre-fix module by content.

    Located by what it CONTAINS rather than by line number, because a line
    number would be a second thing to keep in step with history.
    """
    if not _PRE_FIX_SRC:
        return None
    for node in ast.walk(ast.parse(_PRE_FIX_SRC)):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and all(m in node.value for m in must_contain)):
            return node.value
    print(f"  [git] no pre-fix string constant matched {name_hint}")
    return None


def _pre_fix_function(name):
    """Unparse one top-level function out of the pre-fix module source."""
    if not _PRE_FIX_SRC:
        return None
    for node in ast.parse(_PRE_FIX_SRC).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    print(f"  [git] no pre-fix function named {name}")
    return None


# ===========================================================================
# SECTION 1 -- THE SEEDED TEMPORARY DATABASE
# ===========================================================================

print("=" * 78)
print("SECTION 1 -- the seeded temporary database")
print("=" * 78)

_TMP_DIR = tempfile.mkdtemp(prefix="oncotriage-queries-")
_DB_PATH = os.path.join(_TMP_DIR, "seeded.db")
_EMPTY_DB_PATH = os.path.join(_TMP_DIR, "empty.db")

# READ BEFORE ANY OF THIS FILE'S OWN DATABASE WORK. Section 9 compares this
# against the same count at the end; taken afterwards it would be comparing a
# number against itself and could never report a write.
_PRODUCTION_DB = queries.resolve_query_db_path(None)


def _production_inference_rows():
    """Count production rows through a mode=ro URI, or None if absent.

    ``mode=ro`` rather than a plain connect, on File 41's precedent: a plain
    ``sqlite3.connect`` on a missing path CREATES the file, so a guard written
    that way would bring its own database into existence, count 0 twice, and
    report success.
    """
    if not os.path.isfile(_PRODUCTION_DB):
        return None
    uri = "file:" + os.path.abspath(_PRODUCTION_DB).replace("?", "%3f") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM inferences").fetchone()[0]
    except sqlite3.Error as exc:
        print(f"  [production] could not be counted: {exc}")
        return None
    finally:
        conn.close()


_PRODUCTION_ROWS_BEFORE = _production_inference_rows()

# THE SCHEMA IS THE REAL ONE, produced by the writer rather than retyped here.
# A hand-written CREATE TABLE would let this file pass against a schema the
# pipeline does not have -- which is precisely how a query selecting a column
# that does not exist survived for the life of the project.
with quiet():
    initialize_database(_DB_PATH)
    initialize_database(_EMPTY_DB_PATH)

check_true("the seeded database file exists", os.path.isfile(_DB_PATH))

_conn = sqlite3.connect(_DB_PATH)
_SCHEMA_COLUMNS = {row[1] for row in
                   _conn.execute("PRAGMA table_info(inferences)")}
check_true("the inferences table came back with a real schema (non-degeneracy)",
           len(_SCHEMA_COLUMNS) > 50)


# --- the rows --------------------------------------------------------------
#
# Every hard case this item is about is present at once, because they interact:
# the float64 coercion that breaks the cost arithmetic only happens when an
# all-NULL group sits BESIDE a group carrying numbers, so seeding them in
# separate databases would test neither.

_MODEL_A = "gpt-4o-2024-08-06"
_MODEL_B = "gpt-5.6-terra"

_BASE_ROW = {
    "timestamp": "2026-08-01 10:00:00",
    "age": 60, "sex": "male", "race": "White", "ethnicity": "Not Hispanic",
    "primary_condition": "Malignant neoplasm of breast",
    "condition_count": 6, "medication_count": 12, "allergy_count": 1,
    "expanded_query": "breast carcinoma", "gpt4o_prompt": "PROMPT TEXT",
    "bm25_retrieved": 60, "vector_retrieved": 80,
    "candidates_after_rule_filter": 20, "candidates_after_quality_filter": 15,
    "mesh_dropped": 3, "mesh_resolution": "snomed",
    "stage_dropped": 1, "histology_dropped": 0, "cross_vocab_remaps": 0,
    "query_expansion_time": 0.01, "hybrid_retrieval_time": 1.5,
    "cross_encoder_time": 2.5, "rule_filter_time": 0.2,
    "gpt4o_evaluation_time": 60.0, "total_time": 64.2,
    "cross_encoder_model": "ncbi/MedCPT-Cross-Encoder",
    "pricing_version": "2026-08-04", "qdrant_collection": "trial_criteria_x",
    "error": "", "patient_data_hash": "deadbeef",
    "gpt4o_retries": 0, "ablation_flags": "{}",
    "retrieval_channels_expected": 4, "retrieval_channels_ok": 4,
    "retrieval_degraded": 0, "retrieval_trials_lost": 0,
    "query_expansion_path": "mesh_expanded",
    "mesh_filter_applied": 1, "mesh_filter_skip_reason": None,
    "age_reference_date": "2026-02-26", "birth_date_precision": "day",
    "ecog_value": 1, "ecog_selection": "most_recent_on_or_before_reference",
    "ecog_observations_found": 2,
    "gpt4o_truncation_splits": 0, "gpt4o_calls": 1,
    "not_evaluable_truncated": 0, "gpt4o_output_tokens_estimated": 5000,
}

# (label, overrides). The consistency expectation for each is asserted in
# section 4 by patient_id, so the seed and the expectation are one table rather
# than two lists that can drift apart.
_SEED_ROWS = [
    # Consistent: 5 + 8 + 2 == 15. Slow and drug-heavy, which is what makes
    # `extreme_cases`, `medication_duplication_suspects` and `slowest_prompt`
    # non-empty.
    ("P-CONSISTENT-A", dict(
        matching_model=_MODEL_A, gpt4o_input_tokens=10000,
        gpt4o_output_tokens=5000, gpt4o_reasoning_tokens=None,
        estimated_cost_usd=0.075, medication_count=120, condition_count=10,
        total_time=130.0, age=61,
        candidates_retrieved=100, candidates_reranked=40,
        candidates_filtered=15, candidates_evaluated=15,
        eligible_matches=5, near_misses=8, not_evaluable_trials=2)),
    # Consistent: 3 + 12 + 0 == 15. Same candidates_evaluated as the row above,
    # which is what satisfies `gpt4o_efficiency_by_trial_count`'s HAVING >= 2,
    # and >4000 output tokens, which is what makes `verbose_output` non-empty.
    ("P-CONSISTENT-B", dict(
        matching_model=_MODEL_A, gpt4o_input_tokens=20000,
        gpt4o_output_tokens=4500, gpt4o_reasoning_tokens=1200,
        estimated_cost_usd=0.095, age=72, sex="female", medication_count=60,
        candidates_retrieved=87, candidates_reranked=40,
        candidates_filtered=15, candidates_evaluated=15,
        eligible_matches=3, near_misses=12, not_evaluable_trials=0)),
    # THE ALL-NULL GROUP. Its own model, every token column and the stored cost
    # NULL. Beside the two rows above this is what makes the aggregate columns
    # float64 and turns `int(x or 0)` into a ValueError.
    ("P-NULL-TOKENS", dict(
        matching_model=_MODEL_B, gpt4o_input_tokens=None,
        gpt4o_output_tokens=None, gpt4o_reasoning_tokens=None,
        estimated_cost_usd=None, age=55, total_time=50.0,
        retrieval_degraded=1, retrieval_channels_ok=3,
        retrieval_channels='{"title": {"status": "ok", "count": 60}}',
        retrieval_trials_lost=2,
        candidates_retrieved=100, candidates_reranked=40,
        candidates_filtered=10, candidates_evaluated=10,
        eligible_matches=2, near_misses=7, not_evaluable_trials=1)),
    # NULL model, no tokens. A no-candidates run.
    ("P-NOMODEL-CLEAN", dict(
        matching_model=None, gpt4o_input_tokens=0, gpt4o_output_tokens=0,
        gpt4o_reasoning_tokens=None, estimated_cost_usd=0.0, age=44,
        sex="female", medication_count=2, condition_count=1, total_time=5.0,
        query_expansion_path="base_query_fallback", mesh_filter_applied=0,
        mesh_filter_skip_reason="no_mesh_filter", mesh_resolution="unmapped",
        candidates_retrieved=0, candidates_reranked=0,
        candidates_filtered=0, candidates_evaluated=0,
        eligible_matches=0, near_misses=0, not_evaluable_trials=0)),
    # NULL model WITH tokens. The logging defect the note is for.
    ("P-NOMODEL-TOKENS", dict(
        matching_model=None, gpt4o_input_tokens=1234, gpt4o_output_tokens=567,
        gpt4o_reasoning_tokens=None, estimated_cost_usd=0.0, age=66,
        candidates_retrieved=50, candidates_reranked=30,
        candidates_filtered=12, candidates_evaluated=12,
        eligible_matches=4, near_misses=8, not_evaluable_trials=0)),
    # 5 + 3 + 2 == 10, not 15. A genuine count mismatch.
    ("P-COUNT-MISMATCH", dict(
        matching_model=_MODEL_A, gpt4o_input_tokens=9000,
        gpt4o_output_tokens=3000, gpt4o_reasoning_tokens=None,
        estimated_cost_usd=0.06, age=50, sex="female",
        candidates_retrieved=100, candidates_reranked=40,
        candidates_filtered=15, candidates_evaluated=15,
        eligible_matches=5, near_misses=3, not_evaluable_trials=2)),
    # One past the fusion-pool cap. Counts otherwise consistent, so this row can
    # only be flagged for the reason it is here for.
    ("P-CAP-RETRIEVAL", dict(
        matching_model=_MODEL_A, gpt4o_input_tokens=1000,
        gpt4o_output_tokens=500, gpt4o_reasoning_tokens=None,
        estimated_cost_usd=0.008, age=58,
        candidates_retrieved=RRF_POOL_SIZE + 1, candidates_reranked=40,
        candidates_filtered=5, candidates_evaluated=5,
        eligible_matches=2, near_misses=2, not_evaluable_trials=1)),
    # One past the rerank cap, same discipline.
    ("P-CAP-RERANK", dict(
        matching_model=_MODEL_A, gpt4o_input_tokens=1100,
        gpt4o_output_tokens=520, gpt4o_reasoning_tokens=None,
        estimated_cost_usd=0.009, age=59,
        candidates_retrieved=100, candidates_reranked=TOP_K_CANDIDATES + 1,
        candidates_filtered=5, candidates_evaluated=5,
        eligible_matches=2, near_misses=2, not_evaluable_trials=1)),
    # Counters absent. Under three-valued logic every comparison against these
    # is NULL, so before item 38 this row reached ELSE 'OK' and was reported as
    # consistent.
    ("P-NULL-COUNTERS", dict(
        matching_model=_MODEL_A, gpt4o_input_tokens=100,
        gpt4o_output_tokens=50, gpt4o_reasoning_tokens=None,
        estimated_cost_usd=0.001, age=48,
        candidates_retrieved=None, candidates_reranked=None,
        candidates_filtered=None, candidates_evaluated=None,
        eligible_matches=None, near_misses=None, not_evaluable_trials=None)),
    # A failed run. Makes `error_types` non-empty, and retrieved > 0 with
    # evaluated == 0 makes `extreme_cases` non-empty for a second reason.
    ("P-ERROR", dict(
        matching_model=_MODEL_A, gpt4o_input_tokens=0, gpt4o_output_tokens=0,
        gpt4o_reasoning_tokens=None, estimated_cost_usd=0.0, age=70,
        error="Stage 5 timeout after 300s",
        candidates_retrieved=100, candidates_reranked=40,
        candidates_filtered=0, candidates_evaluated=0,
        eligible_matches=0, near_misses=0, not_evaluable_trials=0)),
    # A PRE-MIGRATION row: not_evaluable_trials NULL, and evaluated equal to
    # eligible + near_misses. The weak branch must NOT flag it.
    ("P-LEGACY-OK", dict(
        matching_model=_MODEL_A, gpt4o_input_tokens=800,
        gpt4o_output_tokens=400, gpt4o_reasoning_tokens=None,
        estimated_cost_usd=0.006, age=64,
        candidates_retrieved=90, candidates_reranked=35,
        candidates_filtered=9, candidates_evaluated=9,
        eligible_matches=4, near_misses=5, not_evaluable_trials=None)),
    # A PRE-MIGRATION row that is provably wrong even without the third term:
    # 9 evaluated cannot be fewer than 6 + 5.
    ("P-LEGACY-BAD", dict(
        matching_model=_MODEL_A, gpt4o_input_tokens=810,
        gpt4o_output_tokens=410, gpt4o_reasoning_tokens=None,
        estimated_cost_usd=0.006, age=65,
        candidates_retrieved=90, candidates_reranked=35,
        candidates_filtered=9, candidates_evaluated=9,
        eligible_matches=6, near_misses=5, not_evaluable_trials=None)),
]

# Which rows the consistency query must flag, and with what. Written beside the
# seed above rather than derived from the query, so the expectation is
# independent of the implementation it checks.
_EXPECTED_ISSUES = {
    "P-COUNT-MISMATCH": "Count mismatch",
    "P-CAP-RETRIEVAL":  "Retrieval anomaly",
    "P-CAP-RERANK":     "Rerank anomaly",
    "P-NULL-COUNTERS":  "Counters not reported",
    "P-LEGACY-BAD":     "Count mismatch",
}

_cursor = _conn.cursor()
_INFERENCE_IDS = {}
for _label, _overrides in _SEED_ROWS:
    _row = dict(_BASE_ROW)
    _row.update(_overrides)
    _row["patient_id"] = _label
    _columns = [c for c in _row if c in _SCHEMA_COLUMNS]
    _cursor.execute(
        f"INSERT INTO inferences ({', '.join(_columns)}) "
        f"VALUES ({', '.join('?' for _ in _columns)})",
        [_row[c] for c in _columns])
    _INFERENCE_IDS[_label] = _cursor.lastrowid

# A column named in the seed that the schema does not have would be dropped by
# the filter above WITHOUT A WORD, which is the same silent-omission shape this
# item is removing. Checked rather than trusted.
_unknown_seed_columns = sorted(
    set(_BASE_ROW) | {k for _, o in _SEED_ROWS for k in o}
)
_unknown_seed_columns = [c for c in _unknown_seed_columns
                         if c not in _SCHEMA_COLUMNS]
check("every column the seed writes exists in the real schema",
      _unknown_seed_columns, [])

for _i, (_nct, _phase, _eligible, _score) in enumerate([
        ("NCT00000001", "Phase 2", "eligible", 0.91),
        ("NCT00000002", "Phase 3", "not_eligible", 0.42),
        ("NCT00000003", "Phase 1", "not_evaluable", 0.55),
        ("NCT00000001", "Phase 2", "eligible", 0.88)]):
    _cursor.execute(
        "INSERT INTO trial_matches (inference_id, nct_id, trial_title, "
        "trial_phase, trial_number, rerank_score, match_score, eligible, "
        "explanation, criterion_details) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_INFERENCE_IDS["P-CONSISTENT-A" if _i < 2 else "P-CONSISTENT-B"],
         _nct, f"Trial {_nct}", _phase, _i + 1, 3.5 - _i * 0.1, _score,
         _eligible, "because", '{"inclusion": [], "exclusion": []}'))

for _i, (_cat, _name, _value, _alert) in enumerate([
        ("performance", "total_time", 64.2, 0),
        ("retrieval", "candidates_retrieved", 91.0, 1),
        ("cost", "estimated_cost_usd", 0.08, 0)]):
    _cursor.execute(
        "INSERT INTO drift_metrics (timestamp, metric_category, metric_name, "
        "metric_value, baseline_mean, baseline_std, p_value, z_score, "
        "threshold, alert, baseline_window_days, comparison_window_days, notes)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (f"2026-08-0{_i + 1} 09:00:00", _cat, _name, _value, _value * 0.9,
         _value * 0.1, 0.03, 2.4 - _i, 3.0, _alert, 30, 7, "seeded"))

_conn.commit()

check("the seed wrote every inference row",
      _conn.execute("SELECT COUNT(*) FROM inferences").fetchone()[0],
      len(_SEED_ROWS))
check("...and the trial_matches rows",
      _conn.execute("SELECT COUNT(*) FROM trial_matches").fetchone()[0], 4)
check("...and the drift_metrics rows",
      _conn.execute("SELECT COUNT(*) FROM drift_metrics").fetchone()[0], 3)
check_true("the models the seed prices are all in PRICING_CONFIG "
           "(non-degeneracy: an unpriced one would make section 5 raise for "
           "the wrong reason)",
           _MODEL_A in _PRICED_MODELS and _MODEL_B in _PRICED_MODELS)


# ===========================================================================
# SECTION 2 -- EVERY QUERY RUNS, AND NONE OF THEM RUNS EMPTY
# ===========================================================================

print()
print("=" * 78)
print("SECTION 2 -- every query executes, non-empty, and report() completes")
print("=" * 78)

check_true("the registry is non-degenerate (non-empty, keys unique)",
           len(queries.QUERY_KEYS) > 30
           and len(set(queries.QUERY_KEYS)) == len(queries.QUERY_KEYS))

_all = queries.run_all(_conn, stop_on_error=False)
_raised = {k: v for k, v in _all.items() if isinstance(v, Exception)}
check("EVERY query in the registry executes without raising",
      sorted(f"{k}: {type(v).__name__}" for k, v in _raised.items()), [])

# NON-DEGENERACY. A query that silently comes back with nothing looks exactly
# like a query that ran and found nothing, and on THIS seed there is nothing
# any of them should legitimately find nothing about -- the rows were chosen so
# that each query has something to report. An empty frame here is a failure.
_empty = sorted(k for k, v in _all.items()
                if not isinstance(v, Exception) and len(v) == 0)
check("...and every one of them returns a NON-EMPTY frame on the seeded data",
      _empty, [])

check("run() on an unknown key raises KeyError rather than returning nothing",
      type(check_raises("  (unknown key)", KeyError, queries.run, _conn, "nope")
            ).__name__, "KeyError")

_report_lines = []
_report = check_does_not_raise(
    "report() runs end to end -- the first time in this project's history",
    queries.report, _conn, out=_report_lines.append)
check("...and it returns a result for every query in the registry",
      sorted(_report or {}), sorted(queries.QUERY_KEYS))
check_true("...having actually printed something (non-degeneracy)",
           len(_report_lines) > 40)

_report_text = "\n".join(str(line) for line in _report_lines)
for _expected in ("=== PIPELINE CONSISTENCY ISSUES ===",
                  "=== COST BREAKDOWN BY MODEL ===",
                  "=== EXPANSION (STAGE 1) STATS ===",
                  "=== LATEST DRIFT RUN ==="):
    check_true(f"report() reached {_expected!r}", _expected in _report_text)


# ===========================================================================
# SECTION 3 -- THE DELETED EXPANSION QUERY
# ===========================================================================

print()
print("=" * 78)
print("SECTION 3 -- expansion_token_efficiency is deleted, not patched")
print("=" * 78)

check_true("expansion_token_efficiency is gone from the registry",
           "expansion_token_efficiency" not in queries.QUERIES_BY_KEY)
check_raises("...and run() refuses the key rather than answering with an "
             "empty frame", KeyError, queries.run, _conn,
             "expansion_token_efficiency")

check("no surviving query names either column that does not exist",
      sorted(q.key for q in queries.QUERIES
             if "expansion_input_tokens" in q.sql
             or "expansion_output_tokens" in q.sql), [])
# Named exactly, not by an `expansion_` prefix: `expansion_prompt` IS a real
# column and a prefix scan would report it and fail for the wrong reason.
check("...and the schema does not have them either, so adding the query back "
      "would break again",
      sorted({"expansion_input_tokens", "expansion_output_tokens"}
             & _SCHEMA_COLUMNS), [])

check_true("the answerable version survives and says why the token version "
           "cannot exist",
           any("no LLM" in n for n in
               queries.QUERIES_BY_KEY["expansion_stage_stats"].notes))

# NEGATIVE CONTROL. "The query is gone" is also what a registry that lost an
# entry to a typo looks like. This shows the thing that was removed really was
# broken, against the same seeded database, using the SQL as it was committed.
_pre_expansion_sql = _pre_fix_string_constant(
    "expansion_token_efficiency", ["expansion_input_tokens", "over_limit_count"])
check_true(f"the pre-fix SQL was recovered from git (rev {_PRE_FIX_REV})",
           _pre_expansion_sql is not None)
if _pre_expansion_sql:
    check_raises("...and it still raises against the real schema, so the "
                 "deletion removed something genuinely broken",
                 pd.errors.DatabaseError,
                 pd.read_sql_query, _pre_expansion_sql, _conn)


# ===========================================================================
# SECTION 4 -- THE CONSISTENCY QUERY
# ===========================================================================

print()
print("=" * 78)
print("SECTION 4 -- pipeline_consistency")
print("=" * 78)

_pre_consistency_sql = _pre_fix_string_constant(
    "pipeline_consistency", ["Retrieval anomaly", "Rerank anomaly"])
check_true("the pre-fix consistency SQL was recovered from git",
           _pre_consistency_sql is not None)

if _pre_consistency_sql:
    # (a) THE STRAY WHEN WAS A DUPLICATE. Both lines come out of the committed
    # text; neither is retyped here, so this compares the code against itself
    # rather than against my transcription of it.
    _mismatch_lines = [l for l in _pre_consistency_sql.splitlines()
                       if "Count mismatch" in l]
    check("the pre-fix SQL carried the 'Count mismatch' line exactly twice",
          len(_mismatch_lines), 2)
    if len(_mismatch_lines) == 2:
        check("...and the two are identical once indentation is stripped, "
              "which is what makes removing the stray one a no-op for the logic",
              _mismatch_lines[0].strip(), _mismatch_lines[1].strip())
        # The comparison must be able to report inequality, or it proves nothing.
        check("...and the same comparison reports two DIFFERENT lines as "
              "different (negative control)",
              _mismatch_lines[0].strip()
              == _mismatch_lines[1].strip().replace("near_misses", "near_miss"),
              False)
        check_true("...and they really did differ in indentation, so the stray "
                   "one was a separate line and not a mis-parse",
                   _mismatch_lines[0] != _mismatch_lines[1])

    # (b) it was a syntax error, so it had never run once.
    check_raises("the pre-fix consistency SQL is a syntax error against the "
                 "real schema", pd.errors.DatabaseError,
                 pd.read_sql_query, _pre_consistency_sql, _conn)

    # (c) the two literals really were there, so replacing them is not cosmetic.
    check_true("the pre-fix SQL hardcoded 100 and 30",
               "!= 100" in _pre_consistency_sql and "!= 30" in _pre_consistency_sql)

_fixed_sql = queries.QUERIES_BY_KEY["pipeline_consistency"].sql
check_true("the fixed SQL carries no stray WHEN before its CASE",
           _fixed_sql.count("Count mismatch") == 2
           and _fixed_sql.index("CASE") < _fixed_sql.index("Count mismatch"))
check_true("the fused-pool bound is RRF_POOL_SIZE, interpolated from config",
           f"> {RRF_POOL_SIZE}" in _fixed_sql)
check_true("the rerank bound is TOP_K_CANDIDATES, interpolated from config",
           f"> {TOP_K_CANDIDATES}" in _fixed_sql)
check("...and neither literal survives as an equality test",
      [t for t in ("!= 100", "!= 30") if t in _fixed_sql], [])

# WHICH CONSTANT GOVERNS WHICH COLUMN IS DERIVED FROM THE CODE THAT PRODUCES
# THE COLUMN, NOT FROM THE NUMBER THAT USED TO BE THERE. `!= 100` was ambiguous
# by value (VECTOR_RETRIEVAL_SIZE and RRF_POOL_SIZE are both 100) and `!= 30`
# matched no constant in the project at all. The two slices below are the
# derivation; if either moves, this fails and the binding has to be re-derived
# rather than assumed to still hold.
_retrieval_src = open(os.path.join(_CODE_DIR, "oncotriage", "agent",
                                   "retrieval.py"), encoding="utf-8").read()
_terminal_src = open(os.path.join(_CODE_DIR, "oncotriage", "agent",
                                  "terminal.py"), encoding="utf-8").read()
check_true("candidates_retrieved is len(hybrid_results) and hybrid_results is "
           "capped at RRF_POOL_SIZE",
           '"candidates_retrieved": len(state.get("hybrid_results", []))' in _terminal_src
           and "[:RRF_POOL_SIZE]" in _retrieval_src)
check_true("candidates_reranked is len(reranked_trials) and reranked_trials is "
           "capped at TOP_K_CANDIDATES",
           '"candidates_reranked": len(state.get("reranked_trials", []))' in _terminal_src
           and "[:TOP_K_CANDIDATES]" in _retrieval_src)
check_true("the stale literal 30 is NOT the value of the constant that governs "
           "candidates_reranked -- reported, not guessed at",
           TOP_K_CANDIDATES != 30)

# (d) the behaviour, row by row.
_issues = queries.run(_conn, "pipeline_consistency")
check_true("the consistency query returns rows on the seeded data "
           "(non-degeneracy)", len(_issues) > 0)
_flagged = dict(zip(_issues["patient_id"], _issues["issue"]))
check("exactly the rows that are inconsistent are flagged, and with the right "
      "category", dict(sorted(_flagged.items())),
      dict(sorted(_EXPECTED_ISSUES.items())))

for _clean in ("P-CONSISTENT-A", "P-CONSISTENT-B", "P-NULL-TOKENS",
               "P-NOMODEL-CLEAN", "P-ERROR", "P-LEGACY-OK"):
    check_true(f"...and {_clean} is NOT flagged", _clean not in _flagged)

check_true("a row whose only 'anomaly' is that it produced fewer candidates "
           "than the cap is clean -- which is what `>` buys over `!=`",
           "P-CONSISTENT-B" not in _flagged
           and int(_conn.execute(
               "SELECT candidates_retrieved FROM inferences "
               "WHERE patient_id = 'P-CONSISTENT-A'").fetchone()[0]) == RRF_POOL_SIZE
           and int(_conn.execute(
               "SELECT candidates_retrieved FROM inferences "
               "WHERE patient_id = 'P-CONSISTENT-B'").fetchone()[0]) < RRF_POOL_SIZE)

check_true("a row with not_evaluable trials that add up is clean -- which is "
           "what including not_evaluable_trials in the identity buys",
           "P-CONSISTENT-A" not in _flagged
           and int(_conn.execute(
               "SELECT not_evaluable_trials FROM inferences "
               "WHERE patient_id = 'P-CONSISTENT-A'").fetchone()[0]) > 0)

check("...and the flagged rows carry the counts a reader needs to act on them",
      [c for c in ("eligible_matches", "near_misses", "not_evaluable_trials")
       if c not in _issues.columns], [])

# NEGATIVE CONTROL for the identity change: under the PRE-FIX two-term identity
# (the one that ignores not_evaluable_trials) P-CONSISTENT-A would have been
# flagged. Computed here rather than asserted, using the seeded row itself.
_a = _conn.execute(
    "SELECT candidates_evaluated, eligible_matches, near_misses, "
    "not_evaluable_trials FROM inferences WHERE patient_id = 'P-CONSISTENT-A'"
).fetchone()
check_true("the two-term identity WOULD have flagged a perfectly ordinary row, "
           "which is why it was wrong", _a[0] != (_a[1] + _a[2]))
check("...and the three-term identity does not", _a[0], _a[1] + _a[2] + _a[3])


# ===========================================================================
# SECTION 5 -- THE COST ARITHMETIC
# ===========================================================================

print()
print("=" * 78)
print("SECTION 5 -- price_model_groups on the float64 case")
print("=" * 78)

_groups_sql = queries.run(_conn, "cost_by_model")
check_true("the SQL group frame is non-degenerate (three model groups, one of "
           "them NULL)", len(_groups_sql) == 3)

# THE PRECONDITION THIS WHOLE SECTION RESTS ON. If the aggregate columns did
# not come back float64 with NaN in them, every check below would pass for the
# wrong reason.
check("the token aggregates are float64, which is what a NULL group beside a "
      "numeric one produces", str(_groups_sql["input_tokens"].dtype), "float64")
_null_group = _groups_sql[_groups_sql["matching_model"] == _MODEL_B].iloc[0]
check_true("...and the all-NULL group's SUM really is NaN rather than 0",
           pd.isna(_null_group["input_tokens"]))
check_true("...so `int(x or 0)` on it raises, because nan is TRUTHY",
           isinstance(check_raises(
               "  (int(nan or 0) raises ValueError)", ValueError,
               lambda: int(_null_group["input_tokens"] or 0)), ValueError))
check_true("...and `x is None` on it is FALSE while pd.isna is True, which is "
           "why the reasoning-token test never fired",
           (_null_group["reasoning_tokens"] is not None)
           and pd.isna(_null_group["reasoning_tokens"]))

_priced = check_does_not_raise("cost_by_model prices the float64 frame without "
                              "raising", queries.cost_by_model, _conn)
# `_priced or []` would raise: a DataFrame has no truth value. `is not None`,
# every time.
check("...into one row per model group",
      len(_priced) if _priced is not None else -1, 3)
check("...with the pinned column set",
      list(_priced.columns) if _priced is not None else [],
      list(queries.PRICED_COST_COLUMNS))

_by_label = {r.matching_model: r for r in _priced.itertuples(index=False)}

# A NULL IS NOT REPORTED AS A ZERO.
check_true("the all-NULL group's token counts come back <NA>, not 0",
           pd.isna(_by_label[_MODEL_B].input_tokens)
           and pd.isna(_by_label[_MODEL_B].output_tokens)
           and pd.isna(_by_label[_MODEL_B].reasoning_tokens))
check_true("...and its note says the SUM was NULL rather than zero",
           "SUM was NULL" in _by_label[_MODEL_B].note)
check_true("...and its stored_cost is <NA> too, not 0.0",
           pd.isna(_by_label[_MODEL_B].stored_cost))
check("...and it is priced at zero spend, without raising",
      float(_by_label[_MODEL_B].recomputed_cost), 0.0)

# A GROUP THAT REALLY DID RECORD ZERO IS DIFFERENT FROM ONE THAT RECORDED
# NOTHING, and the frame keeps them apart.
check("the group with numbers reports them as integers",
      (int(_by_label[_MODEL_A].input_tokens),
       int(_by_label[_MODEL_A].output_tokens),
       int(_by_label[_MODEL_A].reasoning_tokens)),
      (10000 + 20000 + 9000 + 1000 + 1100 + 100 + 0 + 800 + 810,
       5000 + 4500 + 3000 + 500 + 520 + 50 + 0 + 400 + 410,
       1200))
check("...priced against its own model's rates, not a blended one",
      round(float(_by_label[_MODEL_A].recomputed_cost), 10),
      round(get_model_cost(_MODEL_A, int(_by_label[_MODEL_A].input_tokens), 0)
            + get_model_cost(_MODEL_A, 0, int(_by_label[_MODEL_A].output_tokens)), 10))

# THE NULL-MODEL GROUP IS REPORTED, NOT DROPPED.
check_true("the NULL-model group is present and labelled",
           queries.NO_MODEL_LABEL in _by_label)
check("...and marked as not having a recorded model",
      bool(_by_label[queries.NO_MODEL_LABEL].model_recorded), False)
check_true("...and, because it carries tokens, its note calls that a logging "
           "defect",
           "logging defect" in _by_label[queries.NO_MODEL_LABEL].note)

# The other half of that note, on a group that legitimately has no tokens.
_clean_null_model = queries.price_model_groups(pd.DataFrame({
    "matching_model": [None], "rows_n": [3], "input_tokens": [0],
    "output_tokens": [0], "reasoning_tokens": [None], "stored_cost": [0.0]}))
check("a NULL-model group with no tokens is reported as ordinary, not as a "
      "defect", _clean_null_model.iloc[0]["note"], "no model recorded")

# THE CONTRACT IS ENFORCED RATHER THAN ASSUMED.
check_raises("a group frame missing a required column raises with the column "
             "named, instead of producing a partial breakdown",
             ValueError, queries.price_model_groups,
             _groups_sql.drop(columns=["reasoning_tokens"]))
check_true("an unpriced model still raises, even with zero tokens",
           isinstance(check_raises(
               "  (unpriced model raises)", UnknownModelPricingError,
               queries.price_model_groups, pd.DataFrame({
                   "matching_model": ["not-a-model"], "rows_n": [1],
                   "input_tokens": [0], "output_tokens": [0],
                   "reasoning_tokens": [None], "stored_cost": [0.0]})),
               UnknownModelPricingError))

# NEGATIVE CONTROL: THE PRE-FIX ARITHMETIC, EXEC'd FROM ITS COMMITTED SOURCE.
# Not a transcription of it -- the function is unparsed out of the blob git
# holds and run against the very frame the fixed one just handled.
_pre_cost_src = _pre_fix_function("cost_by_model")
check_true("the pre-fix cost_by_model was recovered from git",
           _pre_cost_src is not None)
if _pre_cost_src:
    _ns = {"pd": pd, "get_model_cost": get_model_cost,
           "run": lambda conn, key: _groups_sql}
    exec(compile(_pre_cost_src, "<pre-fix cost_by_model>", "exec"), _ns)
    check_raises("...and it raises ValueError on exactly the frame the fixed "
                 "one prices, which is the defect item 38 removes",
                 ValueError, _ns["cost_by_model"], _conn)

# print_cost_by_model's totals.
_printed = []
_printed_frame = check_does_not_raise(
    "print_cost_by_model runs on the float64 frame",
    queries.print_cost_by_model, _conn, out=_printed.append)
_printed_text = "\n".join(str(l) for l in _printed)
check_true("...and its stored total excludes the NULL group instead of turning "
           "into nan", "nan" not in _printed_text.lower().split("recomputed")[-1][:200])
check_true("...and it says how many groups recorded no stored cost at all",
           "recorded no stored cost" in _printed_text)


# ===========================================================================
# SECTION 6 -- THE DASHBOARD CONSUMES THE QUERY LAYER
# ===========================================================================

print()
print("=" * 78)
print("SECTION 6 -- the duplication is gone, not moved")
print("=" * 78)

_full_frame = pd.read_sql_query("SELECT * FROM inferences", _conn)
check_true("the frame the dashboard path aggregates is non-empty "
           "(non-degeneracy)", len(_full_frame) == len(_SEED_ROWS))

_groups_pandas = queries.model_groups_from_frame(_full_frame)
check("the pandas aggregate carries the contract's columns",
      list(_groups_pandas.columns), list(queries.COST_GROUP_COLUMNS))

_priced_dashboard = check_does_not_raise(
    "the dashboard path prices the same seeded database without raising",
    queries.price_model_groups, _groups_pandas)

check_true(
    "THE DASHBOARD'S FIGURES EQUAL THE QUERY LAYER'S, frame for frame",
    _priced_dashboard is not None
    and _priced_dashboard.equals(_priced))

# NEGATIVE CONTROL: the equality check must be able to report a difference.
_perturbed = _priced_dashboard.copy()
_perturbed.loc[0, "recomputed_cost"] = float(_perturbed.loc[0, "recomputed_cost"]) + 1e-6
check("...and that comparison reports a one-microdollar difference as unequal",
      _perturbed.equals(_priced), False)

# min_count=1 IS WHAT MAKES THE TWO AGREE, and the disagreement it removes is
# demonstrated rather than described.
_naive = _full_frame.groupby("matching_model", dropna=False)[
    ["gpt4o_input_tokens"]].sum()
_naive_b = _naive.loc[_MODEL_B, "gpt4o_input_tokens"]
check("without min_count=1 pandas reports the all-NULL group as 0.0, where SQL "
      "reports NULL", float(_naive_b), 0.0)
check_true("...and with min_count=1 it agrees with SQL",
           pd.isna(_groups_pandas.loc[
               _groups_pandas["matching_model"] == _MODEL_B,
               "input_tokens"].iloc[0]))

# groupby(dropna=False) labels the missing group `nan`, NOT None -- so the
# `is None` test the query layer used would have called it a real model name
# and handed it to get_model_cost. Demonstrated on the actual label.
_pandas_labels = list(_groups_pandas["matching_model"])
_nan_labels = [l for l in _pandas_labels if pd.isna(l)]
check("the pandas group frame carries exactly one missing-model label",
      len(_nan_labels), 1)
check_true("...and `is None` does NOT recognise it, while pd.isna does -- which "
           "is the fault the consolidation would have inherited",
           (_nan_labels[0] is not None) and pd.isna(_nan_labels[0]))

# STRUCTURAL: no second copy of the arithmetic survives in the tab.
_TAB_REL = os.path.join("oncotriage", "dashboard", "tabs", "cost_tokens.py")
_tab_src = open(os.path.join(_CODE_DIR, _TAB_REL), encoding="utf-8").read()
_tab_tree = ast.parse(_tab_src)

_tab_calls = sorted({
    (node.func.attr if isinstance(node.func, ast.Attribute)
     else node.func.id if isinstance(node.func, ast.Name) else "")
    for node in ast.walk(_tab_tree) if isinstance(node, ast.Call)})
check_true("the cost tab no longer calls get_model_cost itself",
           "get_model_cost" not in _tab_calls)
check_true("...nor imports it",
           not any(isinstance(n, ast.ImportFrom)
                   and any(a.name == "get_model_cost" for a in n.names)
                   for n in ast.walk(_tab_tree)))
check_true("...and it does call the shared arithmetic",
           "price_model_groups" in _tab_calls
           and "model_groups_from_frame" in _tab_calls)

_matching_model_groupbys = [
    node for node in ast.walk(_tab_tree)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute) and node.func.attr == "groupby"
    and any(isinstance(a, ast.Constant) and a.value == "matching_model"
            for a in node.args)]
check("no groupby on matching_model survives in the tab -- the aggregate comes "
      "from the query layer now", len(_matching_model_groupbys), 0)

# The negative control for that scan: it must be able to see one.
_control_tree = ast.parse(
    "df.groupby('matching_model', dropna=False)[['a']].sum()")
check("...and the same scan DOES find a planted one (negative control)",
      len([n for n in ast.walk(_control_tree)
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
           and n.func.attr == "groupby"
           and any(isinstance(a, ast.Constant) and a.value == "matching_model"
                   for a in n.args)]), 1)

# IDENTITY, not just name. Importing the tab pulls in streamlit and plotly,
# which is what File 47 section 6 already does for these modules.
try:
    from oncotriage.dashboard.tabs import cost_tokens as _tab_module
except Exception as _exc:                                       # noqa: BLE001
    _tab_module = None
    print(f"  [import] the cost tab could not be imported: "
          f"{type(_exc).__name__}: {_exc}")
check_true("the tab module imports", _tab_module is not None)
if _tab_module is not None:
    check_true("...and the function it reaches IS this module's, by identity",
               _tab_module.queries.price_model_groups is queries.price_model_groups
               and _tab_module.queries.model_groups_from_frame
               is queries.model_groups_from_frame)

    # THE TAB ITSELF RENDERS. Identity proves it reaches the shared code;
    # this proves the render path around it still works with the reshaped
    # frame -- <NA> token counts, a model_recorded flag, and a NULL-model
    # group all at once. Streamlit runs its widget calls in "bare mode"
    # outside a script context; every call in this tab is a no-op there, which
    # is exactly what makes the traversal checkable without a browser.
    import logging as _logging
    from oncotriage.dashboard.tiers import enrich_match_tiers

    _st_logger = _logging.getLogger("streamlit")
    _st_level = _st_logger.level
    _st_logger.setLevel(_logging.CRITICAL)
    try:
        _tab_frame = _full_frame.copy()
        _tab_frame["timestamp"] = pd.to_datetime(_tab_frame["timestamp"])
        _tab_frame = enrich_match_tiers(
            _tab_frame, pd.read_sql_query("SELECT * FROM trial_matches", _conn))
        check_true("the frame handed to the tab carries all three group shapes "
                   "(non-degeneracy: a single priced model would exercise none "
                   "of what this item changed)",
                   len(_tab_frame["matching_model"].dropna().unique()) == 2
                   and _tab_frame["matching_model"].isna().any())
        with quiet():
            _rendered = True
            try:
                _tab_module.render_cost_tokens_tab(_tab_frame)
            except Exception as _render_exc:             # noqa: BLE001
                _rendered = _render_exc
        check("the cost tab renders end to end over the seeded frame",
              _rendered if _rendered is True
              else f"{type(_rendered).__name__}: {_rendered}", True)

        # NEGATIVE CONTROL for the render: drop a column the SHARED AGGREGATE
        # needs and the tab must fail loudly rather than draw a partial chart.
        # gpt4o_reasoning_tokens is the right column to drop -- the tab itself
        # never reads it, so only model_groups_from_frame can notice, which is
        # what makes this a control on the consolidation rather than on the
        # tab's own indexing (dropping estimated_cost_usd raises KeyError at
        # the metrics row above, long before the costing block).
        with quiet():
            _control = check_raises(
                "  (a frame missing gpt4o_reasoning_tokens)", ValueError,
                _tab_module.render_cost_tokens_tab,
                _tab_frame.drop(columns=["gpt4o_reasoning_tokens"]))
        check_true("...and a frame missing a column only the shared aggregate "
                   "reads makes it raise rather than render a partial breakdown",
                   isinstance(_control, ValueError))
    finally:
        _st_logger.setLevel(_st_level)


# ===========================================================================
# SECTION 7 -- THE CUSTOM RENDERERS AGAINST AN EMPTY DATABASE
# ===========================================================================

print()
print("=" * 78)
print("SECTION 7 -- the custom renderers on an empty table")
print("=" * 78)

_empty_conn = sqlite3.connect(_EMPTY_DB_PATH)
check("the control database really is empty (non-degeneracy)",
      _empty_conn.execute("SELECT COUNT(*) FROM inferences").fetchone()[0], 0)

_empty_lines = []
check_does_not_raise(
    "report() completes against an EMPTY database, which it never used to",
    queries.report, _empty_conn, out=_empty_lines.append)
check_true("...and says the prompt is unavailable rather than printing a "
           "half-formed one",
           queries.PROMPT_UNAVAILABLE_MESSAGE
           in "\n".join(str(l) for l in _empty_lines))

check_does_not_raise("cost_by_model on an empty table returns an empty frame "
                     "instead of raising", queries.cost_by_model, _empty_conn)

# NEGATIVE CONTROL: the pre-fix renderer, exec'd from its committed source.
_pre_prompt_src = _pre_fix_function("print_slowest_prompt")
check_true("the pre-fix print_slowest_prompt was recovered from git",
           _pre_prompt_src is not None)
if _pre_prompt_src:
    _ns2 = {"pd": pd, "run": lambda conn, key: queries.run(conn, key)}
    exec(compile(_pre_prompt_src, "<pre-fix print_slowest_prompt>", "exec"), _ns2)
    check_raises("...and it raises IndexError on the empty table, which is why "
                 "report() could never have completed against one",
                 IndexError, _ns2["print_slowest_prompt"], _empty_conn,
                 out=lambda *a: None)


# ===========================================================================
# SECTION 8 -- NEITHER DOCSTRING STILL CLAIMS THE QUERIES ARE BROKEN
# ===========================================================================

print()
print("=" * 78)
print("SECTION 8 -- the docstrings describe the code")
print("=" * 78)

_STALE_CLAIMS = ("STILL BROKEN", "still dies at", "ARE STILL BROKEN")

_queries_doc = ast.get_docstring(ast.parse(
    open(os.path.join(_CODE_DIR, _QUERIES_REL), encoding="utf-8").read())) or ""
_file16_doc = ast.get_docstring(ast.parse(
    open(os.path.join(_CODE_DIR, "16- Database Query.py"),
         encoding="utf-8").read())) or ""

check_true("the queries module docstring is non-degenerate", len(_queries_doc) > 500)
check_true("File 16's docstring is non-degenerate", len(_file16_doc) > 500)

check("the queries module no longer claims its queries are broken on purpose",
      [c for c in _STALE_CLAIMS if c in _queries_doc], [])
check("File 16 no longer claims it either",
      [c for c in _STALE_CLAIMS if c in _file16_doc], [])

# NEGATIVE CONTROL: the scan has to be able to find the claim, or "[] found" is
# indistinguishable from a scan that looks at the wrong text.
if _PRE_FIX_SRC:
    _pre_doc = ast.get_docstring(ast.parse(_PRE_FIX_SRC)) or ""
    check_true("...and the same scan DOES find the claim in the pre-fix "
               "docstring (negative control)",
               any(c in _pre_doc for c in _STALE_CLAIMS))

check_true("the queries module says instead that report() completes",
           "runs to the end" in _queries_doc.lower()
           or "completes" in _queries_doc.lower())


# ===========================================================================
# SECTION 9 -- THE PRODUCTION DATABASE WAS NEVER TOUCHED
# ===========================================================================

print()
print("=" * 78)
print("SECTION 9 -- the production database is unchanged")
print("=" * 78)

check_true("resolve_query_db_path(None) is the production database and is NOT "
           "this file's scratch one -- which is what makes every check above "
           "discriminating rather than vacuous",
           _PRODUCTION_DB != _DB_PATH and _PRODUCTION_DB != _EMPTY_DB_PATH)

_PRODUCTION_ROWS_AFTER = _production_inference_rows()
if _PRODUCTION_ROWS_BEFORE is None:
    print("  NOTE  the production database is absent or unreadable on this "
          "machine, so there was nothing to compare. Nothing here could have "
          "written to it either: every path used above is under "
          f"{_TMP_DIR}.")
else:
    check_true("the production row count is non-degenerate -- a database with "
               "no rows would make the comparison below pass whatever "
               "happened", _PRODUCTION_ROWS_BEFORE > 0)
    check("the production inference row count is unchanged by this run",
          _PRODUCTION_ROWS_AFTER, _PRODUCTION_ROWS_BEFORE)
    # The comparison has to be able to report a change, or "unchanged" is
    # indistinguishable from a counter that always returns the same thing.
    check("...and the same comparison reports a difference as a difference "
          "(negative control)",
          _PRODUCTION_ROWS_AFTER == _PRODUCTION_ROWS_BEFORE + 1, False)


# ===========================================================================
# CLEANUP AND SUMMARY
# ===========================================================================

_conn.close()
_empty_conn.close()
shutil.rmtree(_TMP_DIR, ignore_errors=True)
check("the temporary directory is removed", os.path.isdir(_TMP_DIR), False)

print("\n" + "=" * 78)
print("SUMMARY")
print("=" * 78)
print(f"  passed: {_RESULTS['passed']}")
print(f"  failed: {_RESULTS['failed']}")

if _FAILURES:
    print("\nFAILURES:")
    for _failure in _FAILURES:
        print(f"  - {_failure}")

print()
sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug  5 2026

@author: ramyalsaffar
"""
