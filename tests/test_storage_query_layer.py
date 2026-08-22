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

THE RESIDUAL PASS AFTER ITEM 38 added sections 4b, 4c and 5b, and grew the seed
past the listing's cap so that the first of them has anything to measure:

  1. THE CONSISTENCY REPORT COULD NOT DISTINGUISH "20 ISSUES" FROM "20 OF 400",
     and the twenty it showed were whichever twenty SQLite chose -- ``LIMIT 20``
     sat on the outer select with no ORDER BY. A companion query counts by
     category over every row with no limit and prints immediately above the
     listing; the listing gained a total order. Both derive from ONE CASE
     expression, so they cannot disagree.
  2. THE NULL GUARD'S COLUMN LIST WAS UNENFORCED. Section 4c derives, from the
     SQL, which columns the CASE compares and which have a NULL treatment, and
     fails when a column has neither -- with a control for each direction,
     because "any new column fails" is a different and wrong rule.
  3. AN UNRECORDED COST PRINTED AS ZERO. ``cost_complete`` is the one field a
     consumer asks before summing ``recomputed_cost``, and every figure derived
     from that total now says when it is a floor.

Sections:
    1. The seeded temporary database: real schema from ``initialize_database``,
       rows chosen to exercise every hard case at once, and MORE inconsistent
       rows than the listing can show.
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
    4b. The companion totals, the listing's determinism over two runs, the clean
       case printing the clean message alone, the two queries proved to share
       one CASE by mutating it, and that CASE compared byte for byte against
       item 38's own committed blob.
    4c. Every column the CASE compares is guarded or NULL-aware, derived from
       the SQL, with both negative controls.
    5. The cost arithmetic on the float64 case, with the pre-fix function
       EXTRACTED FROM GIT AND EXEC'd as the negative control.
    5b. ``cost_complete``: what it is False for, what it is deliberately silent
       about, that it agrees with the note column, that the priced value is
       unchanged, and that every total says so -- with an all-complete control
       proving the report does not say it unconditionally.
    6. The dashboard consumes the query layer: identical frames, function
       identity, a structural check that no second copy remains, and the tab
       rendered end to end.
    7. The two custom renderers against an empty database, with the pre-fix
       renderer shown to raise.
    8. Neither docstring still claims the two queries are broken on purpose,
       with the scan shown to find the claim in the pre-fix text.
    9. The production database was never opened for writing and its row count
       is unchanged.

NOTHING IN THIS FILE MAY ABORT THE RUN, and two things did until the reverts
were actually run. ``QUERIES_BY_KEY["k"]`` and ``QUERY_KEYS.index("k")`` raise
when the companion is deleted -- the very edit the section exists to catch --
and ``text.split(marker)[1]`` raises when a report line is missing, which is
what a reverted ``cost_complete`` produces. Both crashed at module level and
hid every check below, so the run reported one traceback where it should have
reported ten failures. ``after()`` and ``registry_index()`` are the fix; see
their docstrings.

No network, no LLM, no API key, no Qdrant. Everything runs against a SQLite file
in a temporary directory that is removed at the end. The production database is
opened only through a ``mode=ro`` URI, and only to count rows.

Run from terminal (or F5 in Spyder):
    python tests/test_storage_query_layer.py
    (was: python "49- Database Query Layer Test.py")

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
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile


# Make the oncotriage package importable
#---------------------------------------
# The same block Files 04, 06, 11 and 12 carry, with the one difference pass
# 20d-2 forced: it looks at the PARENT of this file's directory, because this
# file now sits in tests/ and the package sits BESIDE tests/, not inside it.
# `pip install -e .` makes it a no-op; without it the code directory goes on
# sys.path and the fact is printed rather than left silent.
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

import pandas as pd

from oncotriage.config import RRF_POOL_SIZE, TOP_K_CANDIDATES
from oncotriage.storage import queries
from oncotriage.storage import database_logger as dblog
from oncotriage.storage.database_logger import initialize_database
from oncotriage.utils import UnknownModelPricingError, get_model_cost


# PASS 20d-2: the repository root, derived from the PACKAGE's own location
# rather than from this file's. `oncotriage/__init__.py` -> `oncotriage/` -> the
# code directory. This file already imports the package unconditionally above.
#
# IT IS ALSO THE git CWD, which is what makes this more than a cosmetic change:
# `_git("log", "--", "oncotriage/storage/queries.py")` run from tests/ still
# finds the repository (git walks up) but the PATHSPEC is resolved relative to
# the cwd, so it would match nothing and `_newest_revision_where` would return
# (None, None) for both revisions -- turning every negative control in sections
# 3, 4b, 5 and 7 into a reported failure. Loud, but for the wrong reason.
_CODE_DIR = os.path.dirname(
    os.path.dirname(os.path.abspath(oncotriage.__file__)))

# The two priced models the seed uses. Read out of PRICING_CONFIG rather than
# written here, so this file cannot drift from the pricing table and cannot
# accidentally seed a model the arithmetic would refuse.
_PRICED_MODELS = sorted(queries.PRICING_CONFIG["models"])


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================
#
# The same shape as "tests/test_degraded_dependencies.py"'s, deliberately: a check
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


def after(text, marker):
    """Everything in `text` after `marker`, or "" when the marker is absent.

    EVERY USE OF THIS WAS FIRST WRITTEN AS ``text.split(marker)[1]``, AND THAT
    ABORTS THE RUN. When the marker is missing -- which is precisely what a
    reverted fix produces -- the index raises IndexError at module level and
    every check below it never executes, so the run reports the ONE crash
    instead of the six failures it was built to report. Found by reverting the
    cost_complete fix in a copy of the package and watching this file crash
    rather than fail; the same shape as File 16's own original defect, in the
    file written to remove it.
    """
    _, separator, tail = text.partition(marker)
    return tail if separator else ""


def registry_index(key):
    """Position of `key` in QUERY_KEYS, or -1. ``.index()`` raises ValueError."""
    return (queries.QUERY_KEYS.index(key) if key in queries.QUERY_KEYS else -1)


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
# first revision whose blob still DEFINES the query under discussion.
#
# THE SELECTOR IS STRUCTURAL, AND THE FIRST VERSION OF IT WAS NOT -- WHICH IS
# HOW IT BROKE. It searched for the literal string `expansion_input_tokens`,
# reasoning that only the broken query could name a column that does not exist.
# That was wrong the moment item 38 was committed, because the DELETION COMMENT
# left in its place quotes the query it removed, twice. The selector then picked
# item 38's own revision, `_pre_fix_function("cost_by_model")` returned the
# FIXED function, and two negative controls failed with NameError instead of
# controlling anything. A substring is not a definition; this version parses the
# blob and asks which query KEYS the registry actually declares, which prose can
# never satisfy.

_QUERIES_REL = "oncotriage/storage/queries.py"


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


def _declared_query_keys(src):
    """Every ``Query(key='...')`` the module source declares, as a set.

    AST rather than text search. A key is a keyword argument to a constructor
    call; a mention in a comment or a docstring is not, and the difference is
    exactly what the previous selector could not see.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    keys = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "Query"):
            continue
        for kw in node.keywords:
            if kw.arg == "key" and isinstance(kw.value, ast.Constant):
                keys.add(kw.value.value)
    return keys


def _newest_revision_where(predicate, label):
    """Newest revision of queries.py whose declared query keys satisfy predicate.

    Returns (revision, source) or (None, None). A failure is reported as a
    FAILED check by the caller rather than skipped: this repository has the
    history, and a control that quietly does not run is worse than one that
    fails.
    """
    log = _git("log", "--format=%H", "--", _QUERIES_REL)
    if not log:
        return None, None
    for rev in log.split():
        blob = _git("show", f"{rev}:{_QUERIES_REL}")
        if blob and predicate(_declared_query_keys(blob)):
            return rev, blob
    print(f"  [git] no revision of {_QUERIES_REL} matched: {label}")
    return None, None


# The last revision that still DECLARED the broken query. Everything in
# sections 3, 5 and 7 that says "and here is the thing it used to do" comes
# from this blob.
_PRE_FIX_REV, _PRE_FIX_SRC = _newest_revision_where(
    lambda keys: "expansion_token_efficiency" in keys,
    "declares expansion_token_efficiency")

# Item 38 as shipped: the consistency query exists, the companion totals query
# does not yet. Section 4b compares this pass's CASE against that blob, so
# "the classification is unchanged" is measured against the committed artefact
# rather than against a hash somebody typed.
_ITEM38_REV, _ITEM38_SRC = _newest_revision_where(
    lambda keys: ("pipeline_consistency" in keys
                  and "pipeline_consistency_totals" not in keys),
    "declares pipeline_consistency but not pipeline_consistency_totals")


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
    "expanded_query": "breast carcinoma", "llm_classifier_prompt": "PROMPT TEXT",
    "bm25_retrieved": 60, "vector_retrieved": 80,
    "candidates_after_rule_filter": 20, "candidates_after_quality_filter": 15,
    "mesh_dropped": 3, "mesh_resolution": "snomed",
    "stage_dropped": 1, "histology_dropped": 0, "cross_vocab_remaps": 0,
    "query_expansion_time": 0.01, "hybrid_retrieval_time": 1.5,
    "cross_encoder_time": 2.5, "rule_filter_time": 0.2,
    "llm_classifier_evaluation_time": 60.0, "total_time": 64.2,
    # STAYS A LITERAL, and pass 20f-2 checked rather than assumed it should.
    # That pass gave the checkpoint one name, oncotriage.config.
    # CROSS_ENCODER_MODEL, and replaced the five other copies of the string --
    # but every one of those was a LOAD or a live REPORT, where a stale copy
    # means the row says one thing and the process did another. This is neither:
    # it is a stored value in a seeded row, standing in for what a database
    # written months ago holds, exactly like _MODEL_A ("gpt-4o-2024-08-06")
    # above it and "pricing_version" below. Importing the constant here would
    # make a fixed historical row track whatever the pipeline loads today, which
    # is the opposite of what a stored column means. The check that enforces the
    # single name (test_package_invariants.py 2f(ii)) is scoped to the package
    # for this reason, and says so.
    "cross_encoder_model": "ncbi/MedCPT-Cross-Encoder",
    "pricing_version": "2026-08-04", "qdrant_collection": "trial_criteria_x",
    "error": "", "patient_data_hash": "deadbeef",
    "llm_classifier_retries": 0, "ablation_flags": "{}",
    "retrieval_channels_expected": 4, "retrieval_channels_ok": 4,
    "retrieval_degraded": 0, "retrieval_trials_lost": 0,
    "query_expansion_path": "mesh_expanded",
    "mesh_filter_applied": 1, "mesh_filter_skip_reason": None,
    "age_reference_date": "2026-02-26", "birth_date_precision": "day",
    "ecog_value": 1, "ecog_selection": "most_recent_on_or_before_reference",
    "ecog_observations_found": 2,
    "llm_classifier_truncation_splits": 0, "llm_classifier_calls": 1,
    "not_evaluable_truncated": 0, "llm_classifier_output_tokens_estimated": 5000,
}

# --- STAGE 5 SPLIT PRESSURE: the shapes the two pressure queries read ------
#
# BUILT HERE RATHER THAN IMPORTED. These two blobs stand in for what a database
# written months ago holds, exactly as `cross_encoder_model` above does, so they
# are literals of this file and not a call into the writer that produces them.
# What DOES have to agree with the writer is the KEY SET, and that is asserted
# in section 2c against oncotriage/agent/evaluation.py's own report rather than
# claimed here.
#
# THE VALUES ARE CHOSEN SO NO PRESSURE READING CAN PASS DEGENERATELY. A seed in
# which every chunk sits at the same fraction of its budget satisfies "the query
# returned a number" while proving nothing about which number: peak, mean and
# the two bucket counters would all agree for the wrong reason. So the rows
# below span the whole range the guards can be in -- one run tight against both
# budgets with a relaxed cap and an over-budget chunk, one comfortably clear,
# one whose packer never published, and a large population carrying NULL in
# every one of these columns.

def _packing_blob(budget, chunks, *, relaxed=False, configured=20000,
                  fixed=4000, max_chunks=4):
    """A `llm_classifier_packing` value, in the writer's own shape.

    ``chunks`` is a list of ``(trials, tokens_estimated, over_budget)``.
    ``budget`` is the EFFECTIVE budget -- the one a relaxed run was raised to,
    which is the denominator every pressure reading uses.
    """
    return json.dumps({
        "enabled": True, "method": "chars", "fixed_tokens": fixed,
        "budget_tokens_configured": configured, "budget_tokens": budget,
        "max_chunks": max_chunks, "cap_relaxed_budget": relaxed,
        "over_budget_chunk": any(o for _, _, o in chunks),
        "trials": sum(t for t, _, _ in chunks),
        "chunks": [{"trials": t, "tokens_estimated": e, "over_budget": o}
                   for t, e, o in chunks],
        "prefix_sha256": "0" * 64,
    })


def _call_details_blob(*completions):
    """A `llm_classifier_call_details` value: one entry per call issued.

    Only ``completion_tokens`` is read by the output-pressure query, but every
    key the writer emits is present -- a fixture carrying a SUBSET would let a
    query that reached for a missing sibling key pass by returning NULL.
    """
    return json.dumps([
        {"call_index": _i + 1, "depth": 0, "trials": 5,
         "prompt_tokens": 9000, "completion_tokens": _c,
         "cached_tokens": None, "reasoning_tokens": None,
         "finish_reason": "stop", "entries_emitted": 5}
        for _i, _c in enumerate(completions)])


# The configured pair for the two eras the seed spans. The first is what
# oncotriage/config.py holds today (32,000 x 0.90); the second stands in for the
# GPT-4o era, whose ceiling was 16,000 -- which is the whole reason the
# threshold is a stored column and not a constant a reader looks up. A campaign
# spanning both must report min != max rather than one averaged number.
_THRESHOLD_NOW, _CEILING_NOW = 28800, 32000
_THRESHOLD_OLD, _CEILING_OLD = 14400, 16000


# (label, overrides). The consistency expectation for each is asserted in
# section 4 by patient_id, so the seed and the expectation are one table rather
# than two lists that can drift apart.
_SEED_ROWS = [
    # Consistent: 5 + 8 + 2 == 15. Slow and drug-heavy, which is what makes
    # `extreme_cases`, `medication_duplication_suspects` and `slowest_prompt`
    # non-empty.
    ("P-CONSISTENT-A", dict(
        matching_model=_MODEL_A, llm_classifier_input_tokens=10000,
        llm_classifier_output_tokens=5000, llm_classifier_reasoning_tokens=None,
        estimated_cost_usd=0.075, medication_count=120, condition_count=10,
        total_time=130.0, age=61,
        candidates_retrieved=100, candidates_reranked=40,
        candidates_filtered=15, candidates_evaluated=15,
        eligible_matches=5, near_misses=8, not_evaluable_trials=2,
        llm_classifier_packing=_packing_blob(
            20000, [(8, 19600, False), (7, 12000, False)]),
        llm_classifier_packed_chunks=2,
        llm_classifier_output_split_threshold=_THRESHOLD_NOW,
        llm_classifier_output_ceiling=_CEILING_NOW,
        llm_classifier_output_tokens_estimated=20625,
        llm_classifier_call_details=_call_details_blob(9000, 7000))),
    # Consistent: 3 + 12 + 0 == 15. Same candidates_evaluated as the row above,
    # which is what satisfies `llm_classifier_efficiency_by_trial_count`'s HAVING >= 2,
    # and >4000 output tokens, which is what makes `verbose_output` non-empty.
    ("P-CONSISTENT-B", dict(
        matching_model=_MODEL_A, llm_classifier_input_tokens=20000,
        llm_classifier_output_tokens=4500, llm_classifier_reasoning_tokens=1200,
        estimated_cost_usd=0.095, age=72, sex="female", medication_count=60,
        candidates_retrieved=87, candidates_reranked=40,
        candidates_filtered=15, candidates_evaluated=15,
        eligible_matches=3, near_misses=12, not_evaluable_trials=0,
        llm_classifier_packing=_packing_blob(20000, [(15, 9000, False)]),
        llm_classifier_packed_chunks=1,
        llm_classifier_output_split_threshold=_THRESHOLD_NOW,
        llm_classifier_output_ceiling=_CEILING_NOW,
        llm_classifier_output_tokens_estimated=16500,
        llm_classifier_call_details=_call_details_blob(15900))),
    # THE ALL-NULL GROUP. Its own model, every token column and the stored cost
    # NULL. Beside the two rows above this is what makes the aggregate columns
    # float64 and turns `int(x or 0)` into a ValueError.
    ("P-NULL-TOKENS", dict(
        matching_model=_MODEL_B, llm_classifier_input_tokens=None,
        llm_classifier_output_tokens=None, llm_classifier_reasoning_tokens=None,
        estimated_cost_usd=None, age=55, total_time=50.0,
        retrieval_degraded=1, retrieval_channels_ok=3,
        retrieval_channels='{"title": {"status": "ok", "count": 60}}',
        retrieval_trials_lost=2,
        candidates_retrieved=100, candidates_reranked=40,
        candidates_filtered=10, candidates_evaluated=10,
        eligible_matches=2, near_misses=7, not_evaluable_trials=1,
        llm_classifier_packing=None,
        llm_classifier_packed_chunks=None,
        llm_classifier_output_split_threshold=_THRESHOLD_NOW,
        llm_classifier_output_ceiling=_CEILING_NOW,
        llm_classifier_output_tokens_estimated=16500,
        llm_classifier_call_details="[]")),
    # NULL model, no tokens. A no-candidates run.
    ("P-NOMODEL-CLEAN", dict(
        matching_model=None, llm_classifier_input_tokens=0, llm_classifier_output_tokens=0,
        llm_classifier_reasoning_tokens=None, estimated_cost_usd=0.0, age=44,
        sex="female", medication_count=2, condition_count=1, total_time=5.0,
        query_expansion_path="base_query_fallback", mesh_filter_applied=0,
        mesh_filter_skip_reason="no_mesh_filter", mesh_resolution="unmapped",
        candidates_retrieved=0, candidates_reranked=0,
        candidates_filtered=0, candidates_evaluated=0,
        eligible_matches=0, near_misses=0, not_evaluable_trials=0)),
    # NULL model WITH tokens. The logging defect the note is for.
    ("P-NOMODEL-TOKENS", dict(
        matching_model=None, llm_classifier_input_tokens=1234, llm_classifier_output_tokens=567,
        llm_classifier_reasoning_tokens=None, estimated_cost_usd=0.0, age=66,
        candidates_retrieved=50, candidates_reranked=30,
        candidates_filtered=12, candidates_evaluated=12,
        eligible_matches=4, near_misses=8, not_evaluable_trials=0)),
    # 5 + 3 + 2 == 10, not 15. A genuine count mismatch.
    ("P-COUNT-MISMATCH", dict(
        matching_model=_MODEL_A, llm_classifier_input_tokens=9000,
        llm_classifier_output_tokens=3000, llm_classifier_reasoning_tokens=None,
        estimated_cost_usd=0.06, age=50, sex="female",
        candidates_retrieved=100, candidates_reranked=40,
        candidates_filtered=15, candidates_evaluated=15,
        eligible_matches=5, near_misses=3, not_evaluable_trials=2,
        llm_classifier_packing=_packing_blob(
            24000, [(9, 23900, True), (6, 21000, False)], relaxed=True),
        llm_classifier_packed_chunks=2,
        llm_classifier_output_split_threshold=_THRESHOLD_OLD,
        llm_classifier_output_ceiling=_CEILING_OLD,
        llm_classifier_output_tokens_estimated=15000,
        llm_classifier_truncation_splits=1,
        llm_classifier_call_details=_call_details_blob(15900, 4000))),
    # One past the fusion-pool cap. Counts otherwise consistent, so this row can
    # only be flagged for the reason it is here for.
    ("P-CAP-RETRIEVAL", dict(
        matching_model=_MODEL_A, llm_classifier_input_tokens=1000,
        llm_classifier_output_tokens=500, llm_classifier_reasoning_tokens=None,
        estimated_cost_usd=0.008, age=58,
        candidates_retrieved=RRF_POOL_SIZE + 1, candidates_reranked=40,
        candidates_filtered=5, candidates_evaluated=5,
        eligible_matches=2, near_misses=2, not_evaluable_trials=1)),
    # One past the rerank cap, same discipline.
    ("P-CAP-RERANK", dict(
        matching_model=_MODEL_A, llm_classifier_input_tokens=1100,
        llm_classifier_output_tokens=520, llm_classifier_reasoning_tokens=None,
        estimated_cost_usd=0.009, age=59,
        candidates_retrieved=100, candidates_reranked=TOP_K_CANDIDATES + 1,
        candidates_filtered=5, candidates_evaluated=5,
        eligible_matches=2, near_misses=2, not_evaluable_trials=1)),
    # Counters absent. Under three-valued logic every comparison against these
    # is NULL, so before item 38 this row reached ELSE 'OK' and was reported as
    # consistent.
    ("P-NULL-COUNTERS", dict(
        matching_model=_MODEL_A, llm_classifier_input_tokens=100,
        llm_classifier_output_tokens=50, llm_classifier_reasoning_tokens=None,
        estimated_cost_usd=0.001, age=48,
        candidates_retrieved=None, candidates_reranked=None,
        candidates_filtered=None, candidates_evaluated=None,
        eligible_matches=None, near_misses=None, not_evaluable_trials=None)),
    # A failed run. Makes `error_types` non-empty, and retrieved > 0 with
    # evaluated == 0 makes `extreme_cases` non-empty for a second reason.
    ("P-ERROR", dict(
        matching_model=_MODEL_A, llm_classifier_input_tokens=0, llm_classifier_output_tokens=0,
        llm_classifier_reasoning_tokens=None, estimated_cost_usd=0.0, age=70,
        error="Stage 5 timeout after 300s",
        candidates_retrieved=100, candidates_reranked=40,
        candidates_filtered=0, candidates_evaluated=0,
        eligible_matches=0, near_misses=0, not_evaluable_trials=0)),
    # A PRE-MIGRATION row: not_evaluable_trials NULL, and evaluated equal to
    # eligible + near_misses. The weak branch must NOT flag it.
    ("P-LEGACY-OK", dict(
        matching_model=_MODEL_A, llm_classifier_input_tokens=800,
        llm_classifier_output_tokens=400, llm_classifier_reasoning_tokens=None,
        estimated_cost_usd=0.006, age=64,
        candidates_retrieved=90, candidates_reranked=35,
        candidates_filtered=9, candidates_evaluated=9,
        eligible_matches=4, near_misses=5, not_evaluable_trials=None)),
    # A PRE-MIGRATION row that is provably wrong even without the third term:
    # 9 evaluated cannot be fewer than 6 + 5.
    ("P-LEGACY-BAD", dict(
        matching_model=_MODEL_A, llm_classifier_input_tokens=810,
        llm_classifier_output_tokens=410, llm_classifier_reasoning_tokens=None,
        estimated_cost_usd=0.006, age=65,
        candidates_retrieved=90, candidates_reranked=35,
        candidates_filtered=9, candidates_evaluated=9,
        eligible_matches=6, near_misses=5, not_evaluable_trials=None)),
]

# MORE INCONSISTENT ROWS THAN THE LISTING CAN SHOW, ACROSS TWO CATEGORIES.
#
# Without these the whole companion-query question is untestable: a totals query
# that agrees with a listing which never hit its cap agrees for the wrong reason,
# and "20 issues" versus "20 of 400" is precisely the confusion the companion
# exists to remove. The named rows above contribute five issues; these add
# CONSISTENCY_LISTING_LIMIT more, split across two categories, so the total is
# comfortably past the cap and neither category alone fills it.
#
# Every one of these is consistent in EVERY respect but the one it is here for,
# so a row appearing under the wrong category is a real failure rather than an
# ambiguity.
_BULK_COUNT_MISMATCH = queries.CONSISTENCY_LISTING_LIMIT // 2 + 3   # 13
_BULK_RETRIEVAL_ANOMALY = queries.CONSISTENCY_LISTING_LIMIT - _BULK_COUNT_MISMATCH + 3  # 10

for _n in range(_BULK_COUNT_MISMATCH):
    _SEED_ROWS.append((f"P-BULK-COUNT-{_n:03d}", dict(
        matching_model=_MODEL_A, llm_classifier_input_tokens=700 + _n,
        llm_classifier_output_tokens=300 + _n, llm_classifier_reasoning_tokens=None,
        estimated_cost_usd=0.005, age=40 + _n,
        candidates_retrieved=95, candidates_reranked=38,
        candidates_filtered=12, candidates_evaluated=12,
        # 4 + 4 + 1 == 9, not 12.
        eligible_matches=4, near_misses=4, not_evaluable_trials=1)))

for _n in range(_BULK_RETRIEVAL_ANOMALY):
    _SEED_ROWS.append((f"P-BULK-RETRIEVAL-{_n:03d}", dict(
        matching_model=_MODEL_A, llm_classifier_input_tokens=650 + _n,
        llm_classifier_output_tokens=280 + _n, llm_classifier_reasoning_tokens=None,
        estimated_cost_usd=0.005, age=45 + _n,
        candidates_retrieved=RRF_POOL_SIZE + 2 + _n, candidates_reranked=38,
        candidates_filtered=11, candidates_evaluated=11,
        eligible_matches=5, near_misses=5, not_evaluable_trials=1)))


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
_EXPECTED_ISSUES.update(
    {f"P-BULK-COUNT-{_n:03d}": "Count mismatch"
     for _n in range(_BULK_COUNT_MISMATCH)})
_EXPECTED_ISSUES.update(
    {f"P-BULK-RETRIEVAL-{_n:03d}": "Retrieval anomaly"
     for _n in range(_BULK_RETRIEVAL_ANOMALY)})

# The per-category totals the companion query must reproduce, counted from the
# expectation rather than from the query.
_EXPECTED_ISSUE_COUNTS = {}
for _issue in _EXPECTED_ISSUES.values():
    _EXPECTED_ISSUE_COUNTS[_issue] = _EXPECTED_ISSUE_COUNTS.get(_issue, 0) + 1

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

# THE FOUR ROWS ALSO CARRY THE STAGE 5 NORMALIZER PROVENANCE, AND THE VALUES
# ARE CHOSEN SO THE FOUR QUERIES OVER THEM CANNOT PASS FOR THE WRONG REASON.
# Every one of those queries groups on a COALESCE label that names the absence,
# so a seed leaving all five columns NULL would put every row in the
# "(not reported)" / "(not checked)" bucket and the non-empty check in section 2
# would be satisfied without a single measured branch ever being exercised.
# Row 0 is canonical and clean, row 1 is a recovered boolean label with two
# criterion remaps, row 2 is an entry the pipeline CONSTRUCTED (every column
# NULL, which is the population the reader has to be able to select), and row 3
# is an unreadable label that still ended eligible off its criteria.
_TRIAL_MATCH_ROWS = [
    # nct, phase, eligible, score, ne_reason, v_source, v_label, v_type, remaps
    ("NCT00000001", "Phase 2", "eligible", 0.91,
     None, "canonical", None, None, 0),
    ("NCT00000002", "Phase 3", "not_eligible", 0.42,
     None, "normalized", "True", "bool", 2),
    ("NCT00000003", "Phase 1", "not_evaluable", 0.55,
     "truncation_floor", None, None, None, None),
    ("NCT00000001", "Phase 2", "eligible", 0.88,
     None, "unrecognized", "'MAYBE'", "str", 0),
]

for _i, (_nct, _phase, _eligible, _score, _ne_reason, _v_source,
         _v_label, _v_type, _remaps) in enumerate(_TRIAL_MATCH_ROWS):
    _cursor.execute(
        "INSERT INTO trial_matches (inference_id, nct_id, trial_title, "
        "trial_phase, trial_number, rerank_score, match_score, eligible, "
        "assessment, criterion_details, not_evaluable_reason, verdict_source, "
        "verdict_original_label, verdict_original_type, criterion_remaps) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_INFERENCE_IDS["P-CONSISTENT-A" if _i < 2 else "P-CONSISTENT-B"],
         _nct, f"Trial {_nct}", _phase, _i + 1, 3.5 - _i * 0.1, _score,
         _eligible, "because", '{"inclusion": [], "exclusion": []}',
         _ne_reason, _v_source, _v_label, _v_type, _remaps))

# ...and the two run-level counters on the inferences the rows hang off, so
# `run_normalizer_provenance` has a row whose HAVING is satisfied by a measured
# count rather than only by the presence of a not_evaluable child. The remap
# EVENT total on P-CONSISTENT-B is deliberately left at the _BASE_ROW's 0 while
# its child rows sum to 0 as well (rows 2 and 3 carry NULL and 0), so the
# consistency column that query renders agrees on the seeded data.
_cursor.execute(
    "UPDATE inferences SET verdict_normalizations = ?, remapped_trials = ?, "
    "cross_vocab_remaps = ? WHERE patient_id = ?", (1, 1, 2, "P-CONSISTENT-A"))
_cursor.execute(
    "UPDATE inferences SET verdict_normalizations = ?, remapped_trials = ? "
    "WHERE patient_id = ?", (1, 0, "P-CONSISTENT-B"))

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

# THE RUN TABLES. Four runs, chosen so that every branch of the two run queries'
# shared CASE fragments is exercised by a row -- a registry that returns a frame
# for the wrong reason is what section 2's non-emptiness check cannot see.
#
#   RUN-CLEAN    FINISHED, finalized, has patients, meta rows say 22 counters
#                consulted and 0 moved  -> 'measured clean'
#   RUN-CRASHED  RUNNING with a NULL finished_at and no meta row at all -- the
#                shape a killed process leaves. -> 'RUNNING, no finished_at' and
#                'no health record'
#   RUN-EMPTY    KILLED and finalized, and NO inference row references it. This
#                is the row an INNER JOIN would delete, so it is what makes the
#                LEFT JOIN in `run_summary` load-bearing rather than incidental.
#   RUN-DEGRADED FINISHED with two non-zero counters -> 'degraded', and the only
#                run that contributes a row to the breakdown's non-NULL arm.
#
# A FIFTH SHAPE IS DELIBERATELY PRESENT AND IS NOT A RUN: the eleven-plus
# inference rows seeded above keep their NULL run_id, which is what every row
# written before the run-identity pass has. `run_summary` must not invent a run
# for them, and section 2b asserts it does not.
_RUN_ROWS = [
    # label, status, finished_at, invocation_source
    ("RUN-CLEAN",    "FINISHED", "2026-08-20T11:04:00", "batch_runner"),
    ("RUN-CRASHED",  "RUNNING",  None,                  "batch_runner"),
    ("RUN-EMPTY",    "KILLED",   "2026-08-18T10:05:00", "batch_runner"),
    ("RUN-DEGRADED", "FINISHED", "2026-08-17T11:00:00", "batch_runner"),
]
_RUN_IDS = {}
for _i, (_label, _status, _finished, _source) in enumerate(_RUN_ROWS):
    _cursor.execute(
        "INSERT INTO runs (started_at, finished_at, status, invocation_source, "
        "fingerprint_version, llm_classifier_prompt_version, "
        "llm_classifier_renderer_digest, matching_model_configured, "
        "qdrant_collection, collection_points, data_snapshot_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (f"2026-08-{20 - _i}T10:00:00", _finished, _status, _source,
         2, "1.9.0", f"digest-{_i}", "gpt-5.6-terra",
         "trial_criteria_20260807_111807", 12067, "2026-02-26"))
    _RUN_IDS[_label] = _cursor.lastrowid

# Which seeded patients belong to which run. Chosen off the named rows above so
# the expected patient counts and costs are written here rather than read back
# out of the query being checked.
_RUN_MEMBERSHIP = {
    "RUN-CLEAN":    ["P-CONSISTENT-A", "P-CONSISTENT-B", "P-NULL-TOKENS"],
    "RUN-CRASHED":  ["P-ERROR", "P-NOMODEL-CLEAN"],
    "RUN-DEGRADED": ["P-COUNT-MISMATCH"],
    # RUN-EMPTY intentionally absent -- see above.
}
for _label, _patients in _RUN_MEMBERSHIP.items():
    for _patient in _patients:
        _cursor.execute("UPDATE inferences SET run_id = ? WHERE patient_id = ?",
                        (_RUN_IDS[_label], _patient))

# THE COUNTER NAMES ARE REAL REGISTERED ONES, not invented strings: a breakdown
# rendering a name no counter has ever had would look identical to one rendering
# a real name, and the point of the column is that an operator recognises it.
_RUN_METRIC_ROWS = [
    ("RUN-CLEAN",    dblog.RUN_METRIC_CATEGORY_META,
     dblog.RUN_METRIC_META_COUNTERS_REGISTERED, 22),
    ("RUN-CLEAN",    dblog.RUN_METRIC_CATEGORY_META,
     dblog.RUN_METRIC_META_COUNTERS_NONZERO, 0),
    ("RUN-DEGRADED", dblog.RUN_METRIC_CATEGORY_META,
     dblog.RUN_METRIC_META_COUNTERS_REGISTERED, 22),
    ("RUN-DEGRADED", dblog.RUN_METRIC_CATEGORY_META,
     dblog.RUN_METRIC_META_COUNTERS_NONZERO, 2),
    ("RUN-DEGRADED", dblog.RUN_METRIC_CATEGORY_DEGRADATION,
     "AGE_PARSE_FAILURES", 412),
    ("RUN-DEGRADED", dblog.RUN_METRIC_CATEGORY_DEGRADATION,
     "QDRANT_RETRIES", 3),
]
for _label, _category, _name, _value in _RUN_METRIC_ROWS:
    _cursor.execute(
        "INSERT INTO run_metrics (run_id, category, name, value, written_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (_RUN_IDS[_label], _category, _name, _value, "2026-08-20T11:04:00"))

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
# SECTION 2b -- THE RUN TABLES (the run-reader pass)
# ===========================================================================
#
# Section 2 above proves the two run queries EXECUTE and come back non-empty.
# That is necessary and it is not enough: a `run_summary` that dropped its LEFT
# JOINs, or multiplied its aggregates across three children, or read
# `counters_registered` as "clean", satisfies it exactly. This section asserts
# the VALUES, computed here from the seed rather than read out of the query.

print()
print("=" * 78)
print("SECTION 2b -- runs and run_metrics")
print("=" * 78)

_summary = queries.run(_conn, "run_summary")
_summary_by_id = {int(r.run_id): r for r in _summary.itertuples()}

check("run_summary returns exactly one row per run, and no more",
      len(_summary), len(_RUN_ROWS))
check("...keyed by every seeded run id",
      sorted(_summary_by_id), sorted(_RUN_IDS.values()))
check("...newest first, which is what ORDER BY r.id DESC means",
      list(_summary["run_id"]), sorted(_RUN_IDS.values(), reverse=True))

# --- THE ROW AN INNER JOIN WOULD DELETE -----------------------------------
_empty_run = _summary_by_id[_RUN_IDS["RUN-EMPTY"]]
check("a run NO inference row references is still in run_summary "
      "(this is what the LEFT JOIN buys)",
      int(_empty_run.run_id), _RUN_IDS["RUN-EMPTY"])
check("...with patients 0 -- a measured zero, not a NULL", int(_empty_run.patients), 0)
check("...and cost 0.0", float(_empty_run.cost_usd), 0.0)

# --- PATIENT ROLLUP, COUNTED FROM THE SEED --------------------------------
for _label, _patients in _RUN_MEMBERSHIP.items():
    _row = _summary_by_id[_RUN_IDS[_label]]
    check(f"{_label}: patients counted from the seed, not from the join",
          int(_row.patients), len(_patients))
    # THE MULTIPLICATION CONTROL. RUN-DEGRADED carries two degradation rows and
    # a meta pair; a naive three-way join would report its patient count
    # multiplied by four. A run with one patient makes that visible as 4.
    _expected_cost = round(sum(
        _conn.execute("SELECT COALESCE(estimated_cost_usd, 0) FROM inferences "
                      "WHERE patient_id = ?", (_p,)).fetchone()[0]
        for _p in _patients), 4)
    check(f"{_label}: cost_usd is the sum over its patients and nothing else",
          round(float(_row.cost_usd), 4), _expected_cost)

check_true("the patient counts are non-degenerate -- the three runs with "
           "patients do not all have the same number, so a multiplied or "
           "constant answer could not pass the checks above",
           len({len(v) for v in _RUN_MEMBERSHIP.values()}) > 1)

# P-NULL-TOKENS is in RUN-CLEAN and carries a NULL estimated_cost_usd, so the
# floor and the count that qualifies it must both be right.
_clean_row = _summary_by_id[_RUN_IDS["RUN-CLEAN"]]
check("an unpriced patient is counted, not silently folded into the total "
      "(cost_complete's rule, one table up)",
      int(_clean_row.rows_with_no_cost), 1)
check("...and the errored count is the seeded one",
      int(_clean_row.errored),
      _conn.execute(
          "SELECT COUNT(*) FROM inferences WHERE run_id = ? "
          "AND error IS NOT NULL AND error != ''",
          (_RUN_IDS["RUN-CLEAN"],)).fetchone()[0])

# --- health_record: THE THREE STATES, AND THEY ARE NOT THE SAME -----------
_expected_health = {
    "RUN-CLEAN":    queries.RUN_HEALTH_MEASURED_CLEAN,
    "RUN-CRASHED":  queries.RUN_HEALTH_NEVER_FLUSHED,
    "RUN-EMPTY":    queries.RUN_HEALTH_NEVER_FLUSHED,
    "RUN-DEGRADED": queries.RUN_HEALTH_DEGRADED,
}
for _label, _expected in _expected_health.items():
    check(f"{_label}: health_record",
          _summary_by_id[_RUN_IDS[_label]].health_record, _expected)

check_true("the three health_record states are all exercised (non-degeneracy: "
           "a CASE stuck on one arm would satisfy every check above that "
           "expects that arm)",
           set(_expected_health.values()) == set(queries.RUN_HEALTH_STATES))

check("a measured-clean run reports counters_nonzero = 0, which is the "
      "MEASUREMENT that separates it from a run that never flushed",
      int(_clean_row.counters_nonzero), 0)
check("...and a run that never flushed reports it as NULL, not 0",
      bool(pd.isna(_summary_by_id[_RUN_IDS["RUN-CRASHED"]].counters_nonzero)),
      True)

# --- finalization: the crashed shape is FLAGGED, not hidden ---------------
_expected_final = {
    "RUN-CLEAN":    queries.RUN_FINALIZATION_FINALIZED,
    "RUN-CRASHED":  queries.RUN_FINALIZATION_LIVE_OR_DIED,
    "RUN-EMPTY":    queries.RUN_FINALIZATION_FINALIZED,
    "RUN-DEGRADED": queries.RUN_FINALIZATION_FINALIZED,
}
for _label, _expected in _expected_final.items():
    check(f"{_label}: finalization", 
          _summary_by_id[_RUN_IDS[_label]].finalization, _expected)

# THE THIRD FINALIZATION STATE HAS NO SEEDED RUN, because a terminal status
# with a NULL finished_at is a shape finalize_run_record cannot produce. It is
# driven here directly rather than left unexercised -- an arm of a closed
# vocabulary that no test reaches is an arm nobody has ever seen fire.
_cursor.execute(
    "INSERT INTO runs (started_at, finished_at, status, invocation_source) "
    "VALUES (?, ?, ?, ?)",
    ("2026-08-10T10:00:00", None, "FAILED", "batch_runner"))
_UNSTAMPED_RUN = _cursor.lastrowid
_conn.commit()
_summary2 = queries.run(_conn, "run_summary")
_unstamped = _summary2[_summary2["run_id"] == _UNSTAMPED_RUN]
check("a terminal status with no finished_at is named as its own state, not "
      "folded into 'RUNNING or died'",
      list(_unstamped["finalization"]), [queries.RUN_FINALIZATION_NOT_STAMPED])
check_true("...so all three finalization states are exercised",
           set(_expected_final.values()) | {queries.RUN_FINALIZATION_NOT_STAMPED}
           == set(queries.RUN_FINALIZATION_STATES))
_cursor.execute("DELETE FROM runs WHERE id = ?", (_UNSTAMPED_RUN,))
_conn.commit()
check("...and the probe row is removed, so every later check sees the seed",
      len(queries.run(_conn, "run_summary")), len(_RUN_ROWS))

# --- ROWS WITH NO RUN ARE NOT INVENTED INTO ONE ---------------------------
_orphan_rows = _conn.execute(
    "SELECT COUNT(*) FROM inferences WHERE run_id IS NULL").fetchone()[0]
check_true("the seed has inference rows with a NULL run_id (non-degeneracy: "
           "without them the next check passes for free)", _orphan_rows > 0)
check("run_summary attributes no patient to a run that does not exist -- the "
      "sum over its patients column is the count of rows that DO carry a run_id",
      int(_summary["patients"].sum()),
      _conn.execute("SELECT COUNT(*) FROM inferences "
                    "WHERE run_id IS NOT NULL").fetchone()[0])

# --- THE BREAKDOWN --------------------------------------------------------
_break = queries.run(_conn, "run_degradation_breakdown")
_break_by_run = {}
for _r in _break.itertuples():
    _break_by_run.setdefault(int(_r.run_id), []).append((_r.counter, _r.events))

check("run_degradation_breakdown has a row for EVERY run, clean ones included",
      sorted(_break_by_run), sorted(_RUN_IDS.values()))
check("a clean run appears with the named no-counter label rather than being "
      "omitted",
      [_c for _c, _e in _break_by_run[_RUN_IDS["RUN-CLEAN"]]],
      [queries.RUN_HEALTH_NO_COUNTER_LABEL])
# pd.isna, NOT `is None`. The column holds numbers for other runs, so pandas
# makes it float64 and a SQL NULL arrives as nan -- which is TRUTHY and is not
# equal to None. That is the same trap this file's section 5 exists for, met in
# a new column, and the first version of this check compared against None.
check("...and its events cell is NULL, because no counter reported a number",
      [bool(pd.isna(_e)) for _c, _e in _break_by_run[_RUN_IDS["RUN-CLEAN"]]],
      [True])
check("the degraded run's counters come back worst-first, with the seeded "
      "totals",
      [(_c, int(_e)) for _c, _e in _break_by_run[_RUN_IDS["RUN-DEGRADED"]]],
      [("AGE_PARSE_FAILURES", 412), ("QDRANT_RETRIES", 3)])
check("the breakdown's per-run event total matches run_summary's, which are "
      "two different SQL shapes over the same rows",
      int(sum(int(_e) for _c, _e in _break_by_run[_RUN_IDS["RUN-DEGRADED"]])),
      int(_summary_by_id[_RUN_IDS["RUN-DEGRADED"]].degradation_events))

# --- ATTRIBUTION COVERAGE -------------------------------------------------
_cover = queries.run(_conn, "run_attribution_coverage")
_cover_by_label = {r.attribution: r for r in _cover.itertuples()}

check("the coverage census reports the two attributions the seed has",
      sorted(_cover_by_label),
      sorted([queries.RUN_ATTRIBUTION_ATTRIBUTED,
              queries.RUN_ATTRIBUTION_NO_RUN]))
check("...counting every row with a run_id",
      int(_cover_by_label[queries.RUN_ATTRIBUTION_ATTRIBUTED].inference_rows),
      sum(len(v) for v in _RUN_MEMBERSHIP.values()))
check("...and every row without one, which is the population requirement 3 is "
      "about",
      int(_cover_by_label[queries.RUN_ATTRIBUTION_NO_RUN].inference_rows),
      _orphan_rows)
check("...so the census covers the whole table and drops nothing",
      int(_cover["inference_rows"].sum()),
      _conn.execute("SELECT COUNT(*) FROM inferences").fetchone()[0])
check("a run-less row contributes no distinct run id",
      int(_cover_by_label[queries.RUN_ATTRIBUTION_NO_RUN].distinct_run_ids), 0)

# THE DANGLING ROW. The foreign key is unenforced by design, so this state is
# reachable, and nothing else in the project can report it. Driven for real by
# pointing a row at a runs id that is not there.
_cursor.execute("UPDATE inferences SET run_id = ? WHERE patient_id = ?",
                (999999, "P-CAP-RETRIEVAL"))
_conn.commit()
_cover2 = {r.attribution: r for r in
           queries.run(_conn, "run_attribution_coverage").itertuples()}
check("a row pointing at a runs id that does not exist is named as its own "
      "state, not counted as attributed",
      int(_cover2[queries.RUN_ATTRIBUTION_DANGLING].inference_rows), 1)
check("...and run_summary still attributes no patient to it",
      int(queries.run(_conn, "run_summary")["patients"].sum()),
      sum(len(v) for v in _RUN_MEMBERSHIP.values()))
check_true("...so all three attribution states are exercised (non-degeneracy)",
           set(_cover2) == set(queries.RUN_ATTRIBUTION_STATES))
_cursor.execute("UPDATE inferences SET run_id = NULL WHERE patient_id = ?",
                ("P-CAP-RETRIEVAL",))
_conn.commit()
check("...and the probe is undone, so later sections see the seed",
      sorted(r.attribution for r in
             queries.run(_conn, "run_attribution_coverage").itertuples()),
      sorted([queries.RUN_ATTRIBUTION_ATTRIBUTED,
              queries.RUN_ATTRIBUTION_NO_RUN]))

# --- THE MISSING-TABLE GUARD ----------------------------------------------
#
# THE CHECK THAT KEEPS ITEM 38's PROPERTY. Without `requires`, these two queries
# raise `no such table` against any database written before the run-identity
# pass -- which the production one is -- and report() would die there and stop
# executing everything after them in the registry.
#
# THE LEGACY DATABASE IS THE REAL SCHEMA WITH THE RUN TABLES REMOVED, not three
# stub CREATE TABLEs. A stub would make every OTHER query raise `no such column`,
# so `report()` would die for a reason that has nothing to do with what this
# block tests -- which is exactly what the first version of it did.
_LEGACY_DB = os.path.join(_TMP_DIR, "legacy.db")
with quiet():
    initialize_database(_LEGACY_DB)
_legacy_conn = sqlite3.connect(_LEGACY_DB)
_legacy_conn.execute("DROP TABLE runs")
_legacy_conn.execute("DROP TABLE run_metrics")
_legacy_conn.execute("ALTER TABLE inferences DROP COLUMN run_id")
_legacy_conn.commit()
check("the legacy database really lacks the run tables (non-degeneracy: with "
      "them present every check below passes for the wrong reason)",
      sorted(t for t in ("runs", "run_metrics")
             if t in queries.available_tables(_legacy_conn)), [])
check("...and lacks inferences.run_id too, which is the state a database "
      "written before the run-identity pass is actually in",
      "run_id" in {r[1] for r in
                   _legacy_conn.execute("PRAGMA table_info(inferences)")},
      False)

# THE TWO PRESSURE QUERIES ARE HERE TOO AND DECLARE NO TABLE. They read
# `inferences` alone, so nothing on this list is a table for them -- what makes
# them unavailable is `inferences.run_id`, the same absent column run_summary
# names, which is why they appear beside two queries that need whole tables. It
# is also why the non-degeneracy count below cannot be `sum(1 for q if
# q.requires)`: that expression counted TABLE requirements and would silently
# stop describing this list the moment a column-only query joined it.
check("a database with no run tables reports every run query as unavailable",
      sorted(queries.unavailable(_legacy_conn)),
      ["run_attribution_coverage", "run_degradation_breakdown", "run_summary",
       "stage5_input_packing_pressure", "stage5_output_split_pressure"])
# BOTH ABSENT TABLES AND THE ABSENT COLUMN, and the column is named even
# though `runs` is missing too, because `inferences` IS present and its column
# genuinely is not there. One action -- let a writer open the database -- fixes
# all three, which is why they are reported together rather than in stages.
check("...naming the tables AND the column it does not have",
      queries.unavailable(_legacy_conn)["run_summary"],
      queries.RUN_TABLES + ("inferences.run_id",))
check("...and the query that needs no column names only the tables",
      queries.unavailable(_legacy_conn)["run_degradation_breakdown"],
      queries.RUN_TABLES)
check("...and no OTHER query is reported unavailable (non-degeneracy: a check "
      "that reported everything would pass the line above too)",
      len(queries.unavailable(_legacy_conn)),
      sum(1 for q in queries.QUERIES if q.requires or q.requires_columns))
check("...and the count is not the whole registry, which is what the line "
      "above would also satisfy if `unavailable` had stopped discriminating",
      len(queries.unavailable(_legacy_conn)) < len(queries.QUERIES), True)
# A COLUMN-ONLY DECLARATION IS REPORTED AS THE COLUMN AND NEVER AS A TABLE.
# `inferences` is present on this database, so a query naming only columns of
# it must come back with column names alone -- the shape that tells an operator
# to let a writer open the file rather than to go looking for a missing table.
check("a query declaring only columns names only columns",
      queries.unavailable(_legacy_conn)["stage5_input_packing_pressure"],
      ("inferences.run_id",))
check("...and the output query names every additive column it reads, in "
      "declaration order",
      queries.unavailable(_legacy_conn)["stage5_output_split_pressure"],
      ("inferences.run_id",))
check("run() on such a database RAISES MissingTableError rather than returning "
      "an empty frame that reads as 'this run tracking recorded nothing'",
      type(check_raises("  (legacy db)", queries.MissingTableError,
                        queries.run, _legacy_conn, "run_summary")).__name__,
      "MissingTableError")
check("...and the seeded database, which HAS the tables, reports none "
      "unavailable (the control: a guard that always fires proves nothing)",
      queries.unavailable(_conn), {})

_legacy_lines = []
_legacy_result = check_does_not_raise(
    "report() on a database with no run tables RUNS rather than dying at the "
    "first one -- item 38's property, defended",
    queries.report, _legacy_conn, out=_legacy_lines.append)
_legacy_text = "\n".join(str(line) for line in _legacy_lines)
check_true("...and it SAYS which queries it skipped, rather than quietly "
           "covering less than its registry",
           "run_summary" in _legacy_text and "run_metrics" in _legacy_text)
check("...and the skipped keys are absent from the returned dict, not present "
      "with an empty frame",
      sorted(k for k in queries.QUERY_KEYS
             if queries.QUERIES_BY_KEY[k].requires
             and k in (_legacy_result or {})),
      [])

# THE COLUMN-ONLY SHAPE, which the `requires` tuple alone cannot survive. Both
# run tables present and `inferences.run_id` absent: the coverage query JOINS on
# that column, so without `requires_columns` it raises `no such column`, takes
# report() down, and everything registered after it stops executing -- the exact
# defect the whole mechanism exists to prevent, one granularity off.
#
# initialize_database creates the column and the tables in ONE call, so this
# shape is not producible by the pipeline today. That is a COUPLING, not an
# invariant, and a guard resting on it would fail in precisely the case it was
# written for.
_COLUMN_DB = os.path.join(_TMP_DIR, "no_run_id.db")
with quiet():
    initialize_database(_COLUMN_DB)
_column_conn = sqlite3.connect(_COLUMN_DB)
_column_conn.execute("ALTER TABLE inferences DROP COLUMN run_id")
_column_conn.commit()

check("the column-only database HAS both run tables (non-degeneracy: with them "
      "missing this would be the previous case again)",
      sorted(t for t in queries.RUN_TABLES
             if t in queries.available_tables(_column_conn)),
      sorted(queries.RUN_TABLES))
check("...and does not have inferences.run_id",
      "run_id" in queries.table_columns(_column_conn, "inferences"), False)
# THE EXPECTATION IS DERIVED FROM THE DECLARATIONS, not retyped: exactly the
# queries declaring the column are unavailable, and the one that declares only
# the tables is still answerable. Written this way because the first version of
# this check named ONE key from a reading of the SQL and was wrong --
# run_summary's patient rollup joins on the same column, and the failure of this
# section is what found it.
_expects_run_id = sorted(
    q.key for q in queries.QUERIES
    if ("inferences", "run_id") in q.requires_columns)
check_true("more than one query declares inferences.run_id, and at least one "
           "run query does not (non-degeneracy: an all-or-nothing set would "
           "make the check below indistinguishable from a blanket skip)",
           len(_expects_run_id) >= 2
           and any(q.requires and ("inferences", "run_id") not in q.requires_columns
                   for q in queries.QUERIES))
check("exactly the queries that JOIN on the absent column are unavailable -- "
      "the run query that does not is still answerable",
      sorted(queries.unavailable(_column_conn)), _expects_run_id)
check("...and the absence is reported as the COLUMN, named table.column, "
      "rather than as a missing table nobody can add",
      sorted({v for tup in queries.unavailable(_column_conn).values()
              for v in tup}),
      ["inferences.run_id"])
check("run() on it raises MissingTableError rather than the raw sqlite error "
      "that would have escaped report()'s handler",
      type(check_raises("  (no run_id)", queries.MissingTableError,
                        queries.run, _column_conn,
                        "run_attribution_coverage")).__name__,
      "MissingTableError")
_column_lines = []
check_does_not_raise(
    "report() on it still runs the two run queries it CAN answer and reaches "
    "the end",
    queries.report, _column_conn, out=_column_lines.append)
check_true("...having reached the degradation breakdown, which needs neither "
           "`inferences` nor the absent column -- which is what says the skip "
           "was surgical rather than blanket",
           any("RUNS: DEGRADATION BY COUNTER" in str(line)
               for line in _column_lines))
_column_conn.close()

_legacy_conn.close()



# ===========================================================================
# SECTION 2c -- STAGE 5 SPLIT PRESSURE
# ===========================================================================
#
# THE MEASUREMENT IS A JOIN OF TWO HALVES AND ONLY ONE OF THEM IS A COLUMN.
# The INPUT half is derived, in SQL, out of the packing blob the writer already
# stores; the OUTPUT half needs two denominators that are CONFIGURATION and are
# therefore stored. So this section has to prove two different things: that the
# derivation reads the writer's own key names, and that the two new columns
# behave like every other measurement column in this schema -- additive, NULL
# where nothing was measured, and never defaulted.

print()
print("=" * 78)
class _AbsentGroup:
    """A run group the query did not produce, as a VALUE rather than a KeyError.

    ``_grp(_ip, run)`` and ``_grp(_op, run)`` are the natural way to read a per-run frame
    and the wrong way to read one INSIDE a check(): a defect that makes a group
    vanish -- which is exactly what dropping the unmeasured population does --
    raises while check()'s argument is being evaluated, so the run reports one
    traceback where it owed a summary and every check below it. Every attribute
    of this object is a marker string, so the comparison FAILS and names the
    absence instead.
    """

    def __getattr__(self, name):
        return f"<no such run group: .{name} unreachable>"


_ABSENT_GROUP = _AbsentGroup()


def _grp(frame_map, key):
    """One run's row out of a per-run frame, or a named absence."""
    return frame_map.get(key, _ABSENT_GROUP)


def _num(value, ndigits=None):
    """float(value), optionally rounded -- or a named absence. Never raises.

    A pressure column reads NULL whenever its denominator was not recorded, and
    pandas hands that back as None or NaN. ``float(None)`` raises TypeError, and
    inside a check() argument that is an abort rather than a failure -- so the
    one revert this file exists to catch (a query that stopped measuring) would
    take the whole run down instead of reporting eleven failures.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return f"<not a number: {value!r}>"
    if number != number:
        return "<nan>"
    return round(number, ndigits) if ndigits is not None else number


def _by_run(conn, key):
    """``{run: row}`` for a per-run query, or an EMPTY map when it raised.

    run() raises MissingTableError against a database that cannot answer, which
    is correct and is precisely what a defect removing the two columns from
    INFERENCE_COLUMN_ADDITIONS produces. At module level that raise is an ABORT:
    the file reports one traceback where it owes this section's failures and
    every section after it. An empty map instead makes every _grp() below return
    the named absence, so each check FAILS and says which reading was lost.
    """
    try:
        return {row.run: row for row in queries.run(conn, key).itertuples()}
    except Exception as exc:                       # noqa: BLE001 - reported
        print(f"  (frame unavailable for {key}: "
              f"{type(exc).__name__}: {str(exc)[:120]})")
        return {}


def _is_null(value) -> bool:
    """True for SQL NULL as pandas renders it: None or NaN. Never raises."""
    return value is None or (isinstance(value, float) and value != value)


print("SECTION 2c -- Stage 5 split pressure: input derived, output stored")
print("=" * 78)

# --- (a) the derivation reads the WRITER's key names -----------------------
#
# BY AST OVER THE SHIPPED PACKER, NOT BY IMPORTING IT. Importing
# oncotriage.agent.evaluation would pull openai, qdrant_client and langgraph
# into a file that needs none of them and is bucket A precisely because it does
# not. What is asked is narrow and static: every JSON key the input query
# extracts is a key the packer's report literal actually writes. A reader and a
# writer that disagree about one of these names produce no error at all --
# json_extract returns NULL, every pressure reads NULL, and the query reports a
# campaign with no measurable pressure.
_PACKER_SRC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "oncotriage", "agent", "evaluation.py")
check_true("the packer's source is where this file expects it (a wrong path "
           "would make every key check below pass over an empty set)",
           os.path.exists(_PACKER_SRC))

_packer_fn = None
for _node in ast.walk(ast.parse(io.open(_PACKER_SRC, encoding="utf-8").read())):
    if (isinstance(_node, ast.FunctionDef)
            and _node.name == "pack_trials_by_input_tokens"):
        _packer_fn = _node
check_true("pack_trials_by_input_tokens is still the packer's name",
           _packer_fn is not None)
_packer_keys = {k.value for _n in ast.walk(_packer_fn or ast.Module(body=[], type_ignores=[]))
                if isinstance(_n, ast.Dict)
                for k in _n.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)}
check_true("the packer writes a non-degenerate set of report keys",
           len(_packer_keys) > 8)

# The keys the SQL names, read out of the SQL rather than retyped, so a query
# edit that reaches for a new key is covered without touching this list.
_SQL_KEYS = set(re.findall(r"\$\.([A-Za-z_]+)",
                          queries.QUERIES_BY_KEY[
                              "stage5_input_packing_pressure"].sql))
check_true("the input-pressure SQL names a non-degenerate set of JSON keys",
           len(_SQL_KEYS) >= 4)
check("every JSON key the input-pressure query extracts is one the packer "
      "writes -- a name the two disagree about returns NULL and reads as "
      "'no pressure', not as an error",
      sorted(_SQL_KEYS - _packer_keys), [])
check_true("...and the check discriminates: a key the packer does not write is "
           "reported (negative control)",
           bool({"tokens_estimated_typo"} - _packer_keys))

# --- (b) the two columns are additive, and their absence is declared -------
#
# THE PRE-THIS-PASS PRODUCTION SHAPE. run_id present, the two pressure columns
# absent -- which is every database written before this pass and, on this
# machine, the production one. The output query SELECTS both, so without the
# declaration it raises `no such column`, report() dies at it and every query
# after it stops executing: item 38's defect, one column granularity down.
_PRESSURE_DB = os.path.join(_TMP_DIR, "no_pressure_cols.db")
with quiet():
    initialize_database(_PRESSURE_DB)
_pressure_conn = sqlite3.connect(_PRESSURE_DB)
check("the two pressure columns are created by initialize_database, which is "
      "what makes them additive rather than a migration",
      sorted(c for c in ("llm_classifier_output_ceiling",
                         "llm_classifier_output_split_threshold")
             if c in queries.table_columns(_pressure_conn, "inferences")),
      ["llm_classifier_output_ceiling",
       "llm_classifier_output_split_threshold"])
# DROPPED ONLY IF PRESENT, and that guard is not defensive clutter: a defect
# that removes them from INFERENCE_COLUMN_ADDITIONS is one this section exists
# to catch, and a bare ALTER ... DROP COLUMN on a column that is not there
# raises OperationalError at module level -- so the run would report one
# traceback where it owes the failure above plus everything below it.
for _drop in ("llm_classifier_output_split_threshold",
              "llm_classifier_output_ceiling"):
    if _drop in queries.table_columns(_pressure_conn, "inferences"):
        _pressure_conn.execute(
            f"ALTER TABLE inferences DROP COLUMN {_drop}")
_pressure_conn.commit()
check("...and dropping them leaves run_id and both run tables in place "
      "(non-degeneracy: this must be a COLUMN case, not the earlier one)",
      ("run_id" in queries.table_columns(_pressure_conn, "inferences")
       and set(queries.RUN_TABLES) <= queries.available_tables(_pressure_conn)),
      True)
check("a database without them reports the OUTPUT query unavailable, naming "
      "both columns",
      queries.unavailable(_pressure_conn).get("stage5_output_split_pressure"),
      ("inferences.llm_classifier_output_split_threshold",
       "inferences.llm_classifier_output_ceiling"))
check("...and the INPUT query, which reads neither, is still answerable -- the "
      "skip is surgical rather than blanket",
      "stage5_input_packing_pressure" in queries.unavailable(_pressure_conn),
      False)
check("run() on it raises MissingTableError rather than the raw sqlite error",
      type(check_raises("  (no pressure cols)", queries.MissingTableError,
                        queries.run, _pressure_conn,
                        "stage5_output_split_pressure")).__name__,
      "MissingTableError")
check_does_not_raise(
    "report() on it reaches the end rather than dying at the output query",
    queries.report, _pressure_conn, out=lambda _line: None)
_pressure_conn.close()

# --- (c) the input distribution, per run, against the seed -----------------
#
# EVERY EXPECTATION IS THE ARITHMETIC WRITTEN OUT, not a value read back from
# the frame under test. The seed's chunk estimates and budgets are literals a
# few hundred lines up; the numbers below are what they imply.
_ip = _by_run(_conn, "stage5_input_packing_pressure")
_RUN_CLEAN = str(_RUN_IDS["RUN-CLEAN"])
_RUN_DEGRADED = str(_RUN_IDS["RUN-DEGRADED"])
_RUN_CRASHED = str(_RUN_IDS["RUN-CRASHED"])

check_true("the input-pressure frame covers every run AND the run-less rows "
           "(non-degeneracy: a frame with one group would satisfy most of "
           "what follows)",
           {_RUN_CLEAN, _RUN_DEGRADED, _RUN_CRASHED,
            queries.NO_RUN_LABEL} <= set(_ip))

# RUN-CLEAN: P-CONSISTENT-A packed 19,600 and 12,000 into a 20,000 budget,
# P-CONSISTENT-B packed 9,000, and P-NULL-TOKENS published no packing record.
check("chunks are counted per REQUEST, not per patient", _num(_grp(_ip, _RUN_CLEAN).chunks), 3)
check("...over three inference rows", _num(_grp(_ip, _RUN_CLEAN).inferences), 3)
check("...one of which published no packing record and is counted as such "
      "rather than dropped", _num(_grp(_ip, _RUN_CLEAN).unpacked_inferences), 1)
check("peak pressure is the tightest chunk over its own budget: 19600/20000",
      _num(_grp(_ip, _RUN_CLEAN).peak_pressure, 4), 0.98)
check("mean pressure averages the CHUNKS, not the patients: "
      "(19600 + 12000 + 9000) / 3 / 20000",
      _num(_grp(_ip, _RUN_CLEAN).mean_pressure, 4),
      round((19600 + 12000 + 9000) / 3 / 20000, 4))
check("headroom is stated in tokens, tightest first: 20000 - 19600",
      _num(_grp(_ip, _RUN_CLEAN).min_headroom_tokens), 400)
check("the 90% bucket counts only the chunk that reached it",
      _num(_grp(_ip, _RUN_CLEAN).chunks_at_90pct), 1)
check("...and the 75% bucket is a SUPERSET of it, never a disjoint band",
      _num(_grp(_ip, _RUN_CLEAN).chunks_at_75pct), 1)
check("a clean run reports no over-budget chunk and no relaxed cap",
      (_num(_grp(_ip, _RUN_CLEAN).over_budget_chunks),
       _num(_grp(_ip, _RUN_CLEAN).relaxed_inferences)), (0, 0))

# RUN-DEGRADED: the cap was relaxed to 24,000 and one chunk shipped over it.
check("a relaxed run measures against the EFFECTIVE budget it was raised to, "
      "not the configured one: 23900/24000",
      _num(_grp(_ip, _RUN_DEGRADED).peak_pressure, 4),
      round(23900 / 24000, 4))
check("...and the effective budget is what the frame reports",
      (_num(_grp(_ip, _RUN_DEGRADED).budget_min), _num(_grp(_ip, _RUN_DEGRADED).budget_max)),
      (24000, 24000))
check("...with the relaxation counted per INFERENCE and the over-budget chunk "
      "per CHUNK -- the guard firing, not approaching",
      (_num(_grp(_ip, _RUN_DEGRADED).relaxed_inferences),
       _num(_grp(_ip, _RUN_DEGRADED).over_budget_chunks)), (1, 1))
check("...and both of its chunks are past 75% while only one is past 90%",
      (_num(_grp(_ip, _RUN_DEGRADED).chunks_at_75pct),
       _num(_grp(_ip, _RUN_DEGRADED).chunks_at_90pct)), (2, 1))

# RUN-CRASHED: two inference rows, neither carrying a packing record. This is
# the NULL-heavy legacy population, and it must appear rather than vanish.
check("a run whose rows carry no packing record is a ROW in the frame, with "
      "its unmeasured population named -- an omission would read as an "
      "absence of pressure",
      (_num(_grp(_ip, _RUN_CRASHED).inferences),
       _num(_grp(_ip, _RUN_CRASHED).unpacked_inferences),
       _num(_grp(_ip, _RUN_CRASHED).chunks)), (2, 2, 0))
check("...and every pressure reading for it is NULL rather than 0, which "
      "would assert a measured floor",
      [_is_null(_v)
       for _v in (_grp(_ip, _RUN_CRASHED).peak_pressure,
                  _grp(_ip, _RUN_CRASHED).mean_pressure,
                  _grp(_ip, _RUN_CRASHED).min_headroom_tokens,
                  _grp(_ip, _RUN_CRASHED).budget_min)],
      [True, True, True, True])
check("the run-less rows are the bulk legacy population and are reported, not "
      "filtered away",
      _num(_grp(_ip, queries.NO_RUN_LABEL).inferences)
      == _num(_grp(_ip, queries.NO_RUN_LABEL).unpacked_inferences)
      and _num(_grp(_ip, queries.NO_RUN_LABEL).inferences) > 10, True)

# --- (d) the output distribution, per run ----------------------------------
_op = _by_run(_conn, "stage5_output_split_pressure")
check_true("the output-pressure frame covers the same four groups",
           {_RUN_CLEAN, _RUN_DEGRADED, _RUN_CRASHED,
            queries.NO_RUN_LABEL} <= set(_op))
check("split pressure is the batch estimate over the threshold THAT RAN: "
      "20625/28800, which is the config comment's own arithmetic",
      _num(_grp(_op, _RUN_CLEAN).peak_split_pressure, 4),
      round(20625 / 28800, 4))
check("...and the headroom beside it is the 8,175 tokens that note states",
      _num(_grp(_op, _RUN_CLEAN).min_split_headroom_tokens), 28800 - 20625)
check("a run under the threshold reports no inference over it",
      _num(_grp(_op, _RUN_CLEAN).inferences_over_threshold), 0)
check("ceiling pressure comes from the LARGEST SINGLE response, out of the "
      "per-call ledger: 15900/32000",
      _num(_grp(_op, _RUN_CLEAN).peak_call_output_tokens, 4)
      == 15900.0
      and _num(_grp(_op, _RUN_CLEAN).peak_ceiling_pressure, 4)
      == round(15900 / 32000, 4), True)
check("...which is NOT the summed output column, whose value for that run "
      "would give a different answer (non-degeneracy)",
      _num(_grp(_op, _RUN_CLEAN).peak_call_output_tokens)
      == _num(_conn.execute(
          "SELECT MAX(llm_classifier_output_tokens) FROM inferences "
          "WHERE run_id = ?", (_RUN_IDS["RUN-CLEAN"],)).fetchone()[0]),
      False)
check("a run at the OLD ceiling reports its own threshold, not today's",
      (_num(_grp(_op, _RUN_DEGRADED).split_threshold_min),
       _num(_grp(_op, _RUN_DEGRADED).output_ceiling_max)),
      (_THRESHOLD_OLD, _CEILING_OLD))
check("...and its estimate is OVER that threshold, so the headroom is "
      "negative and the row is counted",
      (_num(_grp(_op, _RUN_DEGRADED).peak_split_pressure, 4),
       _num(_grp(_op, _RUN_DEGRADED).min_split_headroom_tokens),
       _num(_grp(_op, _RUN_DEGRADED).inferences_over_threshold)),
      (round(15000 / 14400, 4), 14400 - 15000, 1))
check("...and its worst response came within 100 tokens of the ceiling",
      _num(_grp(_op, _RUN_DEGRADED).min_ceiling_headroom_tokens), 16000 - 15900)
check("the splits actually spent are carried beside the pressure that "
      "predicts them", _num(_grp(_op, _RUN_DEGRADED).splits_spent), 1)
check("a run that never measured reports its whole population as unmeasured "
      "-- which is not the same as low pressure",
      (_num(_grp(_op, _RUN_CRASHED).inferences), _num(_grp(_op, _RUN_CRASHED).unmeasured)),
      (2, 2))
check("...and every pressure reading for it is NULL, not 0",
      [_is_null(_v)
       for _v in (_grp(_op, _RUN_CRASHED).peak_split_pressure,
                  _grp(_op, _RUN_CRASHED).peak_ceiling_pressure,
                  _grp(_op, _RUN_CRASHED).split_threshold_min,
                  _grp(_op, _RUN_CRASHED).peak_call_output_tokens)],
      [True, True, True, True])
check("a campaign spanning ONE configuration reports min == max, which is what "
      "makes a min != max readable as a config change rather than as noise",
      (_num(_grp(_op, _RUN_CLEAN).split_threshold_min)
       == _num(_grp(_op, _RUN_CLEAN).split_threshold_max) == _THRESHOLD_NOW), True)
check("...and the two runs disagree about the threshold, so the pair of "
      "columns is doing work (non-degeneracy)",
      _num(_grp(_op, _RUN_CLEAN).split_threshold_min)
      != _num(_grp(_op, _RUN_DEGRADED).split_threshold_min), True)

# --- (e) neither query is stored twice -------------------------------------
#
# THE RULE THIS PASS WAS BUILT UNDER, made checkable. The input side derives
# everything from llm_classifier_packing, so no input quantity may have acquired
# a column of its own; the output side stores exactly two, and both are
# denominators rather than measurements.
_INF_COLS = set(dblog.INFERENCE_COLUMN_ADDITIONS)
check("no INPUT pressure quantity was given a column: the packing blob is the "
      "one home for the estimate and both budgets",
      sorted(c for c in _INF_COLS
             if "input" in c and ("budget" in c or "pressure" in c)), [])
check("...and the output side added exactly the two denominators",
      sorted(c for c in _INF_COLS if "output_split_threshold" in c
             or "output_ceiling" in c),
      ["llm_classifier_output_ceiling",
       "llm_classifier_output_split_threshold"])


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
#
# THE PER-ROW ASSERTIONS RUN AGAINST AN UNCAPPED VARIANT, and they have to. The
# shipped listing stops at CONSISTENCY_LISTING_LIMIT rows, and this seed
# deliberately produces more than that, so "exactly the inconsistent rows are
# flagged" is not a question the capped query can answer. The variant is BUILT
# FROM THE SHIPPED SQL by raising its cap, never retyped: the cap is a named
# constant interpolated into the SQL, so replacing its rendered value is a
# mechanical edit whose success is asserted below rather than assumed.
_uncapped_sql = _fixed_sql.replace(
    f"LIMIT {queries.CONSISTENCY_LISTING_LIMIT}", "LIMIT 1000000")
check_true("the uncapped variant really differs from the shipped listing "
           "(non-degeneracy: a failed replace would silently re-test the cap)",
           _uncapped_sql != _fixed_sql
           and f"LIMIT {queries.CONSISTENCY_LISTING_LIMIT}" not in _uncapped_sql)

_all_issues = pd.read_sql_query(_uncapped_sql, _conn)
_flagged = dict(zip(_all_issues["patient_id"], _all_issues["issue"]))
check_true("the consistency classification returns rows on the seeded data "
           "(non-degeneracy)", len(_all_issues) > 0)
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

_issues = queries.run(_conn, "pipeline_consistency")
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
# SECTION 4b -- THE COMPANION TOTALS, AND THE LISTING'S DETERMINISM
# ===========================================================================

print()
print("=" * 78)
print("SECTION 4b -- the totals beside the capped listing")
print("=" * 78)

# THE PRECONDITION. Everything below is about a listing that cannot show
# everything; if the seed never exceeded the cap, every check would pass for the
# wrong reason and the companion would be agreeing with a listing that had
# nothing left over to disagree about.
check_true("the seed produces MORE inconsistent rows than the listing can show "
           "(non-degeneracy: this is what the companion exists for)",
           len(_EXPECTED_ISSUES) > queries.CONSISTENCY_LISTING_LIMIT)
check_true("...across at least two categories, neither of which fills the cap "
           "on its own", len(_EXPECTED_ISSUE_COUNTS) >= 2
           and max(_EXPECTED_ISSUE_COUNTS.values())
           < queries.CONSISTENCY_LISTING_LIMIT)

# EVERY REFERENCE TO THE COMPANION GOES THROUGH A LOOKUP THAT CANNOT RAISE.
# `QUERIES_BY_KEY["..."]` and `QUERY_KEYS.index("...")` both abort the run when
# the key is absent, which is exactly what deleting the companion produces --
# so the check written to catch that deletion would have crashed instead of
# reporting it, hiding the ninety checks below. Demonstrated, not imagined.
_TOTALS_KEY = "pipeline_consistency_totals"
_totals_query = queries.QUERIES_BY_KEY.get(_TOTALS_KEY)
check_true(f"the registry declares {_TOTALS_KEY!r}", _totals_query is not None)

check("the companion runs immediately BEFORE the listing, so the totals print "
      "above the sample",
      registry_index("pipeline_consistency") - registry_index(_TOTALS_KEY), 1)

_totals = (queries.run(_conn, _TOTALS_KEY) if _totals_query is not None
           else pd.DataFrame(columns=["issue", "n"]))
check("the companion reports every category, with the counts the seed put there",
      dict(sorted(zip(_totals["issue"], (int(n) for n in _totals["n"])))),
      dict(sorted(_EXPECTED_ISSUE_COUNTS.items())))

check("the listing is capped at exactly CONSISTENCY_LISTING_LIMIT rows",
      len(_issues), queries.CONSISTENCY_LISTING_LIMIT)
check_true("...and the companion's total EXCEEDS what the listing shows, which "
           "is the fact a reader could not previously recover",
           int(_totals["n"].sum()) > len(_issues))
check("...and the companion's total equals the uncapped row count",
      int(_totals["n"].sum()), len(_all_issues))

check("every row the listing DOES show is one the classification flagged, with "
      "the same category",
      sorted({(p, i) for p, i in zip(_issues["patient_id"], _issues["issue"])}
             - {(p, i) for p, i in _EXPECTED_ISSUES.items()}), [])
check_true("...and the sample spans more than one category, so ordering by "
           "issue has not collapsed it onto the first one",
           len(set(_issues["issue"])) >= 2)

# --- DETERMINISM ----------------------------------------------------------
#
# The listing had no ORDER BY, so SQLite was free to return a different twenty
# on each execution. Two runs, compared as ORDERED SEQUENCES rather than as
# sets: a set comparison would pass on a query that returned the same rows in a
# different order, which is exactly the failure being ruled out.
_run_a = queries.run(_conn, "pipeline_consistency")
_run_b = queries.run(_conn, "pipeline_consistency")
check("two runs of the listing return the same patient_ids in the same order",
      list(_run_a["patient_id"]), list(_run_b["patient_id"]))
check("...and the same row ids, which is the part patient_id cannot pin",
      list(_run_a["id"]), list(_run_b["id"]))
check_true("the ordering is TOTAL, not merely stable-looking: the sequence is "
           "sorted by (issue, patient_id, id) and every key is distinct",
           list(zip(_run_a["issue"], _run_a["patient_id"], _run_a["id"]))
           == sorted(zip(_run_a["issue"], _run_a["patient_id"], _run_a["id"]))
           and len(set(_run_a["id"])) == len(_run_a))

# patient_id ALONE would not have been a total order, which is why `id` is
# selected. Measured on the seeded table the same way it was measured on
# production (1,106 rows, 1,004 distinct patient_ids) -- here by planting a
# duplicate, because the seed's own ids are unique by construction.
_cursor.execute(
    "INSERT INTO inferences (patient_id, timestamp, matching_model, error, "
    "candidates_retrieved, candidates_reranked, candidates_filtered, "
    "candidates_evaluated, eligible_matches, near_misses, "
    "not_evaluable_trials, llm_classifier_input_tokens, llm_classifier_output_tokens, "
    "estimated_cost_usd) VALUES ('P-BULK-COUNT-000', '2026-08-01', ?, '', "
    "95, 38, 12, 12, 4, 4, 1, 700, 300, 0.005)", (_MODEL_A,))
_conn.commit()
_dupe_ids = [r[0] for r in _conn.execute(
    "SELECT id FROM inferences WHERE patient_id = 'P-BULK-COUNT-000' ORDER BY id")]
check_true("a duplicated patient_id really produces two rows (non-degeneracy)",
           len(_dupe_ids) == 2)
_dupe_run = queries.run(_conn, "pipeline_consistency")
check("...and the listing still returns them in a fixed order, because `id` "
      "breaks the tie",
      [int(i) for i in _dupe_run.loc[
          _dupe_run["patient_id"] == "P-BULK-COUNT-000", "id"]],
      sorted(int(i) for i in _dupe_ids))
_conn.execute("DELETE FROM inferences WHERE id = ?", (max(_dupe_ids),))
_conn.commit()
check("the duplicate is removed again, so later sections see the seed they "
      "expect", _conn.execute("SELECT COUNT(*) FROM inferences").fetchone()[0],
      len(_SEED_ROWS))

# --- THE CLEAN CASE STILL READS AS CLEAN ----------------------------------
#
# The whole point of render='skip_if_empty'. On a database with no issues the
# companion must print NOTHING -- not its heading, not its note, not an empty
# table -- so the listing's clean message stands alone exactly as it always did.
_clean_lines = []
_clean_conn = sqlite3.connect(_EMPTY_DB_PATH)
try:
    queries.report(_clean_conn, out=_clean_lines.append)
finally:
    _clean_conn.close()
_clean_text = "\n".join(str(l) for l in _clean_lines)
_TOTALS_HEADING = (_totals_query.heading if _totals_query is not None
                   else "(no companion query in the registry)")
_TOTALS_NOTES = _totals_query.notes if _totals_query is not None else ()

check("on a clean database the clean message is printed exactly once",
      _clean_text.count(queries.CONSISTENCY_CLEAN_MESSAGE), 1)
check("...and the companion prints nothing at all above it -- not its heading",
      _TOTALS_HEADING in _clean_text, False)
check("...not its note either",
      any(n in _clean_text for n in _TOTALS_NOTES), False)

# ...and on the seeded database it prints all three.
_seeded_lines = []
queries.report(_conn, out=_seeded_lines.append)
_seeded_text = "\n".join(str(l) for l in _seeded_lines)
check_true("with issues present the companion DOES print its heading and note "
           "(negative control for the three checks above)",
           _TOTALS_HEADING in _seeded_text
           and bool(_TOTALS_NOTES)
           and all(n in _seeded_text for n in _TOTALS_NOTES))
check_true("...and the totals appear ABOVE the listing in the printed report",
           _TOTALS_HEADING in _seeded_text
           and _seeded_text.index(_TOTALS_HEADING)
           < _seeded_text.index(
               queries.QUERIES_BY_KEY["pipeline_consistency"].heading))
check("...and the clean message does NOT appear when there are issues",
      queries.CONSISTENCY_CLEAN_MESSAGE in _seeded_text, False)

# --- ONE CASE, NOT TWO ----------------------------------------------------
#
# The instruction was that the two queries agree "by construction rather than by
# two copies of the same CASE". They do: there is one _CONSISTENCY_CASE_SQL and
# one _CONSISTENCY_CLASSIFIED_SQL, and both queries interpolate them. Asserted
# by containment first, then DEMONSTRATED by mutating the shared source and
# showing both derived queries move together -- which is what "cannot be edited
# in one place only" means operationally.
_totals_sql = _totals_query.sql if _totals_query is not None else ""
check_true("the shared CASE appears verbatim in the listing",
           queries._CONSISTENCY_CASE_SQL in _fixed_sql)
check_true("...and verbatim in the companion",
           queries._CONSISTENCY_CASE_SQL in _totals_sql)
check("the CASE appears exactly once in each, so neither carries a second copy",
      (_fixed_sql.count(queries._CONSISTENCY_CASE_SQL),
       _totals_sql.count(queries._CONSISTENCY_CASE_SQL)), (1, 1))

_mutated_case = queries._CONSISTENCY_CASE_SQL.replace(
    "'Rerank anomaly'", "'MUTATED CATEGORY'")
check_true("the mutation applies (non-degeneracy)",
           _mutated_case != queries._CONSISTENCY_CASE_SQL)
_mutated_classified = queries._CONSISTENCY_CLASSIFIED_SQL.replace(
    queries._CONSISTENCY_CASE_SQL, _mutated_case)
_rebuilt_listing = _fixed_sql.replace(
    queries._CONSISTENCY_CLASSIFIED_SQL, _mutated_classified)
_rebuilt_totals = _totals_sql.replace(queries._CONSISTENCY_CLASSIFIED_SQL,
                                      _mutated_classified)
check_true("editing the ONE CASE changes both derived queries together -- there "
           "is no second copy to forget",
           "MUTATED CATEGORY" in _rebuilt_listing
           and "MUTATED CATEGORY" in _rebuilt_totals)
check_true("...and both rebuilt queries execute, so the shared text really is "
           "the whole classification and not a fragment of it",
           len(pd.read_sql_query(_rebuilt_listing, _conn)) > 0
           and len(pd.read_sql_query(_rebuilt_totals, _conn)) > 0)
check_true("...and the mutated category reaches the RESULTS of both, not just "
           "their text",
           "MUTATED CATEGORY" in set(pd.read_sql_query(_rebuilt_listing,
                                                       _conn)["issue"])
           and "MUTATED CATEGORY" in set(pd.read_sql_query(_rebuilt_totals,
                                                           _conn)["issue"]))

# --- THE CASE ITSELF IS UNCHANGED BY THIS PASS ----------------------------
#
# The residual pass was permitted to add an ORDER BY to the listing and nothing
# else: item 38's categories, bounds and NULL handling are correct and this is
# the mechanism for "and nothing else". TWO INDEPENDENT PINS, because they fail
# in different circumstances and neither subsumes the other.
#
# (i) THE COMMITTED TEXT. Pulled out of item 38's own blob, rendered through the
# same two config constants the source interpolates, and compared byte for byte
# against what this module holds now. This is the authoritative pin: it compares
# code against code with nothing retyped in between.
_ITEM38_CASE = None
if _ITEM38_SRC:
    _start = _ITEM38_SRC.find("        CASE\n")
    _end = _ITEM38_SRC.find("        END as issue", _start)
    if _start != -1 and _end != -1:
        _raw = _ITEM38_SRC[_start:_end + len("        END as issue")]
        try:
            # The committed text is f-string SOURCE, so it still carries
            # {RRF_POOL_SIZE} / {TOP_K_CANDIDATES}. Rendering it with the same
            # constants is what makes the comparison apples-to-apples; the
            # category literals this pass moved into named constants render back
            # to the same strings, which is the point of naming them.
            _ITEM38_CASE = _raw.format(RRF_POOL_SIZE=RRF_POOL_SIZE,
                                       TOP_K_CANDIDATES=TOP_K_CANDIDATES)
        except (KeyError, IndexError, ValueError) as _exc:
            print(f"  [git] item-38 CASE would not render: "
                  f"{type(_exc).__name__}: {_exc}")

check_true(f"item 38's own CASE was recovered from git (rev {_ITEM38_REV}) "
           f"and is non-degenerate",
           _ITEM38_CASE is not None and len(_ITEM38_CASE) > 500)
if _ITEM38_CASE:
    check("the CASE is byte-identical to the one item 38 committed",
          queries._CONSISTENCY_CASE_SQL, _ITEM38_CASE)
    check("...and the same comparison rejects a one-category change (negative "
          "control)", _mutated_case == _ITEM38_CASE, False)

# (ii) A sha256 MEASURED FROM THE SHIPPED ARTEFACT BEFORE THE REFACTOR. It
# duplicates (i) on a machine with history and is the only pin left on one
# without -- a shallow clone, an exported tarball, a container build. Recorded
# rather than derived precisely so it does not depend on git.
_CASE_SHA256_AS_SHIPPED_BY_ITEM_38 = (
    "c73948cffb6d276582a6533bf8b7b2ed792894f3b75a6da8afe17d3ec3eaee10")
check("the CASE block hashes to what was measured before the refactor",
      hashlib.sha256(queries._CONSISTENCY_CASE_SQL.encode()).hexdigest(),
      _CASE_SHA256_AS_SHIPPED_BY_ITEM_38)
check("...and the hash comparison notices a one-character change (negative "
      "control)",
      hashlib.sha256(_mutated_case.encode()).hexdigest()
      == _CASE_SHA256_AS_SHIPPED_BY_ITEM_38, False)
# The two pins must agree, or one of them is measuring something else.
if _ITEM38_CASE:
    check("...and the two pins agree with each other",
          hashlib.sha256(_ITEM38_CASE.encode()).hexdigest(),
          _CASE_SHA256_AS_SHIPPED_BY_ITEM_38)

# The listing's own additions, pinned separately from the CASE.
check_true("the listing orders by (issue, patient_id, id) before its LIMIT",
           "ORDER BY issue, patient_id, id" in _fixed_sql
           and _fixed_sql.index("ORDER BY issue, patient_id, id")
           < _fixed_sql.index("LIMIT"))
check_true("`id` is selected, which is what makes that order total",
           "\n        id,\n" in queries._CONSISTENCY_CLASSIFIED_SQL)


# ===========================================================================
# SECTION 4c -- THE NULL GUARD'S COLUMN SET IS DERIVED FROM THE SQL
# ===========================================================================

print()
print("=" * 78)
print("SECTION 4c -- every compared column is guarded or NULL-aware")
print("=" * 78)

# THE RULE, and it is not "every compared column is in the guard". The guard
# names six columns; not_evaluable_trials is deliberately NOT among them,
# because it is an added column that is legitimately NULL on pre-migration rows
# and flagging those as "counters not reported" would report a schema migration
# as a pipeline defect. It has the other treatment instead: its own pair of
# NULL-aware branches.
#
# So: EVERY COLUMN THE CASE COMPARES MUST EITHER BE IN THE NULL GUARD, OR HAVE
# AN EXPLICIT BRANCH HANDLING ITS NULL CASE. Both sets are derived from the SQL
# text below rather than listed here, so a seventh counter added later cannot
# quietly skip both.


def _sql_identifiers(text):
    """Real `inferences` columns named in a fragment of SQL.

    String literals are stripped first, so 'Count mismatch' contributes nothing.
    Intersecting with the REAL SCHEMA rather than filtering a keyword list is
    what keeps this honest: SQL keywords, aliases and numbers are excluded
    because they are not columns, not because somebody remembered to list them.
    """
    without_literals = re.sub(r"'[^']*'", " ", text)
    return {w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", without_literals)
            if w in _SCHEMA_COLUMNS}


def _null_guard_and_compared(case_sql):
    """Split a CASE into (guarded, compared, null_aware) column sets.

    The guard is located by the category it emits, not by position: a branch
    order change must not silently turn a different branch into "the guard".
    """
    marker = f"'{queries.CONSISTENCY_GUARD_CATEGORY}'"
    if marker not in case_sql:
        return None, None, None
    head, tail = case_sql.split(marker, 1)
    # The guard branch is the last WHEN before the marker.
    guard_branch = head[head.rindex("WHEN"):]
    guarded = _sql_identifiers(guard_branch)
    compared = _sql_identifiers(tail)
    null_aware = {m for m in re.findall(
        r"([A-Za-z_][A-Za-z0-9_]*)\s+IS\s+(?:NOT\s+)?NULL", tail)
        if m in _SCHEMA_COLUMNS}
    return guarded, compared, null_aware


_guarded, _compared, _null_aware = _null_guard_and_compared(
    queries._CONSISTENCY_CASE_SQL)

check_true("the guard branch was located and is non-degenerate",
           _guarded is not None and len(_guarded) >= 5)
check_true("the compared set is non-degenerate too -- an empty one would make "
           "the rule below vacuous", _compared and len(_compared) >= 5)
check_true("not_evaluable_trials is compared but deliberately NOT guarded, "
           "which is why the rule is a disjunction",
           "not_evaluable_trials" in _compared
           and "not_evaluable_trials" not in _guarded)
check_true("...and it is the NULL-aware set that covers it",
           "not_evaluable_trials" in _null_aware)

check("EVERY column the CASE compares is either in the NULL guard or has its "
      "own NULL-aware branch",
      sorted(_compared - _guarded - _null_aware), [])

# --- TWO NEGATIVE CONTROLS, and the second is the one that matters --------
#
# The first shows the rule catches an unguarded new column. Alone it would be
# satisfied by a check that simply rejects every new column, which is a
# different and wrong rule -- it would forbid the treatment not_evaluable_trials
# already uses. The second shows a new column WITH a NULL-aware branch passes.
_CONTROL_COLUMN = "mesh_dropped"      # a real column, absent from the CASE
check_true(f"the control column {_CONTROL_COLUMN!r} is real and not already in "
           f"the CASE (non-degeneracy)",
           _CONTROL_COLUMN in _SCHEMA_COLUMNS
           and _CONTROL_COLUMN not in _compared)

_control_untreated = queries._CONSISTENCY_CASE_SQL.replace(
    "            ELSE '",
    f"            WHEN {_CONTROL_COLUMN} > 5 THEN 'Planted anomaly'\n"
    f"            ELSE '")
check_true("the untreated control was planted (non-degeneracy)",
           _control_untreated != queries._CONSISTENCY_CASE_SQL)
_g1, _c1, _n1 = _null_guard_and_compared(_control_untreated)
check(f"a seventh compared column with NEITHER treatment is REPORTED",
      sorted(_c1 - _g1 - _n1), [_CONTROL_COLUMN])

_control_treated = queries._CONSISTENCY_CASE_SQL.replace(
    "            ELSE '",
    f"            WHEN {_CONTROL_COLUMN} IS NOT NULL\n"
    f"             AND {_CONTROL_COLUMN} > 5 THEN 'Planted anomaly'\n"
    f"            ELSE '")
check_true("the treated control was planted (non-degeneracy)",
           _control_treated != queries._CONSISTENCY_CASE_SQL)
_g2, _c2, _n2 = _null_guard_and_compared(_control_treated)
check("...and a seventh compared column WITH a NULL-aware branch PASSES -- "
      "without this the rule would collapse into 'any new column fails'",
      sorted(_c2 - _g2 - _n2), [])
check_true("...and the treated control really did add the column to the "
           "compared set, so it passed for the right reason",
           _CONTROL_COLUMN in _c2 and _CONTROL_COLUMN in _n2)

# Both control CASEs must still be valid SQL, or the controls are testing a
# string rather than a query.
for _label, _case in (("untreated", _control_untreated),
                      ("treated", _control_treated)):
    check_does_not_raise(
        f"the {_label} control CASE is executable SQL",
        pd.read_sql_query,
        queries._PIPELINE_CONSISTENCY_SQL.replace(
            queries._CONSISTENCY_CASE_SQL, _case), _conn)


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
#
# The expected sums are SUMMED FROM THE SEED TABLE rather than written as a
# literal. A literal was the first version and it went stale the moment the
# consistency section needed more rows -- and a stale expectation in a check
# about arithmetic is the shape this project treats as a defect, because it
# fails for a reason that has nothing to do with the code under test.
_expected_a = {"in": 0, "out": 0, "reasoning": 0}
for _label, _overrides in _SEED_ROWS:
    if _overrides.get("matching_model") != _MODEL_A:
        continue
    for _key, _column in (("in", "llm_classifier_input_tokens"),
                          ("out", "llm_classifier_output_tokens"),
                          ("reasoning", "llm_classifier_reasoning_tokens")):
        _value = _overrides.get(_column)
        if _value is not None:
            _expected_a[_key] += _value
check_true("the expected sums are non-degenerate (a zero on both sides would "
           "make the comparison below vacuous)",
           all(v > 0 for v in _expected_a.values()))
check("the group with numbers reports them as integers",
      (int(_by_label[_MODEL_A].input_tokens),
       int(_by_label[_MODEL_A].output_tokens),
       int(_by_label[_MODEL_A].reasoning_tokens)),
      (_expected_a["in"], _expected_a["out"], _expected_a["reasoning"]))
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
           "into nan", "nan" not in after(_printed_text.lower(), "recomputed")[:200])
check_true("...and it says how many groups recorded no stored cost at all",
           "recorded no stored cost" in _printed_text)


# ===========================================================================
# SECTION 5b -- AN UNRECORDED COST IS VISIBLE AT EVERY TOTAL
# ===========================================================================

print()
print("=" * 78)
print("SECTION 5b -- cost_complete")
print("=" * 78)

# THE DEFECT THIS CLOSES. An unpriceable group contributes a REAL 0.0 to
# recomputed_cost -- not a NULL -- so nothing about the column reveals that the
# total is a floor. A consumer summing it under-reports by exactly the
# unpriceable spend and cannot tell. The note column said so in prose; prose is
# not a field, and a published cost-per-patient figure is computed from the
# number.

check_true("cost_complete is in the priced frame's pinned column set",
           "cost_complete" in queries.PRICED_COST_COLUMNS)
check("...immediately after the column it qualifies, so the relationship is "
      "visible in the printed table",
      queries.PRICED_COST_COLUMNS[
          queries.PRICED_COST_COLUMNS.index("recomputed_cost") + 1],
      "cost_complete")

# The seed carries all three shapes at once, which is the only arrangement in
# which the flag can be shown to discriminate rather than to be constant.
check("the priced frame reports completeness per group, and it is NOT constant "
      "(non-degeneracy: an all-True or all-False column would satisfy every "
      "check below)",
      sorted(set(bool(v) for v in _priced["cost_complete"])), [False, True])

check("the group with real tokens and a real model is COMPLETE",
      bool(_by_label[_MODEL_A].cost_complete), True)
check("the group whose token SUMs are NULL is INCOMPLETE -- nothing is known "
      "about its spend", bool(_by_label[_MODEL_B].cost_complete), False)
check("the NULL-model group carrying tokens is INCOMPLETE -- its consumption is "
      "known and there is no rate to price it at",
      bool(_by_label[queries.NO_MODEL_LABEL].cost_complete), False)
check("a NULL-model group carrying NO tokens is COMPLETE -- an ordinary "
      "no-candidates run really did spend nothing",
      bool(_clean_null_model.iloc[0]["cost_complete"]), True)

# A group missing only its stored cost is still complete on the recomputed side.
# This is the distinction the docstring makes and it is easy to get wrong by
# folding both nulls into one flag.
_stored_only_missing = queries.price_model_groups(pd.DataFrame({
    "matching_model": [_MODEL_A], "rows_n": [2], "input_tokens": [1000],
    "output_tokens": [500], "reasoning_tokens": [None], "stored_cost": [None]}))
check("a group whose STORED cost is NULL but whose tokens are recorded is "
      "cost_complete -- the flag qualifies recomputed_cost and nothing else",
      bool(_stored_only_missing.iloc[0]["cost_complete"]), True)
check("...and its stored_cost is still <NA>, which is the separate signal a "
      "consumer asks for that sum",
      bool(pd.isna(_stored_only_missing.iloc[0]["stored_cost"])), True)

# THE BOOLEAN AND THE PROSE MUST NOT DISAGREE. Computed independently -- the
# flag from the data, the note from the same data by a different path -- so this
# compares two derivations rather than a value against itself.
_disagreements = []
for _row in _priced.itertuples(index=False):
    _note_says_incomplete = any(frag in _row.note
                                for frag in queries.COST_INCOMPLETE_NOTES)
    if _note_says_incomplete == bool(_row.cost_complete):
        _disagreements.append(
            f"{_row.matching_model}: cost_complete={_row.cost_complete} "
            f"note={_row.note!r}")
check("cost_complete and the note column agree on every group",
      _disagreements, [])
check_true("...and both fired on at least one group, so the agreement is not "
           "between two empty sets",
           any(frag in _row.note for _row in _priced.itertuples(index=False)
               for frag in queries.COST_INCOMPLETE_NOTES))

# THE PRICED VALUE IS UNCHANGED -- the instruction was explicit that an
# incomplete group must keep pricing at $0.00 rather than becoming NaN, because
# NaN would propagate into every aggregate and produce no number at all.
for _label in (_MODEL_B, queries.NO_MODEL_LABEL):
    check("an incomplete group still prices at 0.0, not NaN "
          f"({_label})", float(_by_label[_label].recomputed_cost), 0.0)
check_true("...so the recomputed total is a real number rather than NaN",
           not pd.isna(_priced["recomputed_cost"].sum()))

# THE TOTALS SAY SO. This is the fix: not a new note, but a qualifier on every
# figure derived from the total.
check_true("print_cost_by_model marks the recomputed total as a FLOOR",
           "A FLOOR, NOT A TOTAL" in _printed_text)
check_true("...names how many groups and rows could not be priced",
           "could not be priced from what was recorded" in _printed_text)
check_true("...names the groups themselves, so the reader can go and look",
           _MODEL_B in after(_printed_text, "could not be priced")[:400])
check_true("...points at the field to ask rather than only at the prose",
           "cost_complete" in _printed_text)
check_true("...and qualifies the 1000-patient projection too, which is the "
           "number most likely to be quoted",
           "(a FLOOR" in after(_printed_text, "Projected cost")[:200])

# NEGATIVE CONTROL: with every group complete, none of those lines appears.
# Without this, "the report says FLOOR" is satisfied by a report that says it
# unconditionally, which is the same defect one step along.
_complete_only = queries.price_model_groups(pd.DataFrame({
    "matching_model": [_MODEL_A, _MODEL_B], "rows_n": [4, 2],
    "input_tokens": [1000, 2000], "output_tokens": [500, 600],
    "reasoning_tokens": [None, 10], "stored_cost": [0.01, 0.02]}))
check("the control frame really is all-complete (non-degeneracy)",
      sorted(set(bool(v) for v in _complete_only["cost_complete"])), [True])

_complete_printed = []
_saved_cost_by_model = queries.cost_by_model
try:
    queries.cost_by_model = lambda conn: _complete_only
    queries.print_cost_by_model(None, out=_complete_printed.append)
finally:
    queries.cost_by_model = _saved_cost_by_model
check_true("the module-level rebinding was undone",
           queries.cost_by_model is _saved_cost_by_model)
_complete_text = "\n".join(str(l) for l in _complete_printed)
check("with every group complete, the FLOOR marker is absent",
      "A FLOOR, NOT A TOTAL" in _complete_text, False)
check("...and so is the incomplete-groups line",
      "could not be priced from what was recorded" in _complete_text, False)
check("...and the projection is unqualified",
      "(a FLOOR" in after(_complete_text, "Projected cost"), False)
check_true("...while the report itself still printed (non-degeneracy)",
           "Recomputed total:" in _complete_text)


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
    ["llm_classifier_input_tokens"]].sum()
_naive_b = _naive.loc[_MODEL_B, "llm_classifier_input_tokens"]
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
        # llm_classifier_reasoning_tokens is the right column to drop -- the tab itself
        # never reads it, so only model_groups_from_frame can notice, which is
        # what makes this a control on the consolidation rather than on the
        # tab's own indexing (dropping estimated_cost_usd raises KeyError at
        # the metrics row above, long before the costing block).
        with quiet():
            _control = check_raises(
                "  (a frame missing llm_classifier_reasoning_tokens)", ValueError,
                _tab_module.render_cost_tokens_tab,
                _tab_frame.drop(columns=["llm_classifier_reasoning_tokens"]))
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
