# Drift Redesign Test
######################

"""The designation store, the layered row membership, and the two state axes.

WHAT THIS FILE IS FOR, AND IT IS TWO DEFECTS RATHER THAN ONE FEATURE
---------------------------------------------------------------------
1. THE BASELINE WAS SELECTED BY TIME WINDOW. The earliest rows in `inferences`
   were the baseline and the latest were the comparison, so which rows the
   pipeline was measured against was decided by insertion order -- and a
   baseline captured AFTER something went wrong read as no drift at all.
2. EVERY METRIC THAT COULD NOT RUN WROTE `alert = 0`. The same byte a healthy
   reading writes, from every early return of every metric, rendered by the
   dashboard as a green tick.

Covers, in order:

    1.  The two vocabularies are CLOSED and the gate is TOTAL.
    2.  The digest: a mutation with the ids and the count intact is caught.
    3.  Campaign membership, resumes included, and its refusals.
    4.  Layer 1 -- dedup is the FIRST ATTEMPT and is applied BEFORE any
        success filtering.
    5.  Layer 2 -- per-metric eligibility, and the failed patient that must be
        visible to the metrics that exist to see failures.
    6.  Pairing: verified / unverified / incomparable, and the leftovers.
    7.  The reporting-only denominators, including the zero one that is
        counted rather than divided.
    8.  Strata, including the EXPLICIT unknown one.
    9.  The whole pipeline, end to end, on synthetic current-schema databases:
        happy path, missing designation, mutated reference, incomparable,
        zero variance, never-observed columns, identity-less database, the
        availability-before-reference ordering, and a fresh bootstrap on a
        TEMPORARY absent path.
    10. The logged ROWS -- not the return values -- carry both axes.
    11. The rendered DASHBOARD output -- not the return values -- distinguishes
        a refusal and a reporting-only value from an OK.
    12. TARGETED FAILURE CONTROLS: each critical safeguard is removed in a COPY
        of the package and the run is required to notice.

WHAT IT COSTS: nothing. No network, no keys, no spend, no live Qdrant, no model
load, no corpus, no git history, no live server. Every database is built by the
project's own `initialize_database()` inside a `tempfile.mkdtemp` this file
removes and then asserts gone, and `paths._RESOLVED` is seeded so nothing can
resolve to the production tree. The production database is NEVER OPENED: the
one place a default could reach it -- `resolve_drift_db_path(None)` -- is
RESOLVED and never connected to, which is what that function's own contract
allows.

IT EXECS NOTHING and loads no module by location. Section 12's controls are
`copytree` COPIES of the package with `PYTHONPATH` pointed at them and a
realpath preflight asserting the copy is what imports, which is this project's
established shape for a revert matrix.

NOT IN THE COLLISION MATRIX, derived rather than declared: it writes only
inside its own temp directory, and the package files it reads are written by
neither of the suite's two writers. They are sha256-compared at the end anyway.

Run from terminal:
    python tests/test_monitoring_drift_redesign.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""

import ast
import hashlib
import inspect
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
from contextlib import redirect_stderr, redirect_stdout

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

from oncotriage import paths as _paths
from oncotriage.monitoring import drift as _drift
from oncotriage.monitoring import drift_reference as _dr
from oncotriage.monitoring import drift_states as _ds
from oncotriage.registries import primary_cancer as _pc
from oncotriage.storage import database_logger as _dl


#------------------------------------------------------------------------------


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================

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


def check_true(label, condition):
    check(label, bool(condition), True)


def drive(fn, *args, **kwargs):
    """Call fn, converting a raise into a value ``check`` can FAIL on.

    NOT decoration. A bare call inside a ``check(...)`` argument list lets a
    planted defect's exception escape while the argument is being evaluated,
    which prints one traceback where the run owes a summary and every result
    below it. This project has shipped that shape eighteen times; the fix is
    mechanical and belongs in every file that drives production code.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                      # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


def at(mapping, key):
    """``mapping[key]`` that names an absence instead of raising KeyError."""
    try:
        return mapping[key]
    except BaseException as exc:                      # noqa: BLE001
        return f"<NO SUCH KEY {key!r}: {type(exc).__name__}: {exc}>"


def first(sequence, default="<EMPTY>"):
    """``sequence[0]`` that names an absence instead of raising IndexError."""
    try:
        return sequence[0]
    except BaseException:                             # noqa: BLE001
        return default


def quiet(fn, *args, **kwargs):
    """Run fn with BOTH channels captured. console and log both write to stderr."""
    err, out = io.StringIO(), io.StringIO()
    with redirect_stderr(err), redirect_stdout(out):
        value = drive(fn, *args, **kwargs)
    return value, err.getvalue() + out.getvalue()


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(oncotriage.__file__)))


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


# The package files this file READS. Hashed now, compared at the end, so the
# claim that it writes nothing in the repository is measured rather than made.
_WATCHED = {
    rel: _sha(os.path.join(_ROOT, rel))
    for rel in ("oncotriage/monitoring/drift.py",
                "oncotriage/monitoring/drift_reference.py",
                "oncotriage/monitoring/drift_states.py",
                "oncotriage/dashboard/tabs/drift.py",
                "oncotriage/storage/database_logger.py")
}
check_true("the watched package files hash to DIFFERENT values, so the "
           "comparison at the end is over five files rather than one hashed "
           "five times", len(set(_WATCHED.values())) == len(_WATCHED))


_TMP = tempfile.mkdtemp(prefix="oncotriage_drift_redesign_")

# NOTHING CAN RESOLVE TO THE PRODUCTION TREE. `paths._RESOLVED` is the seam the
# rest of this suite already uses; seeding it means a code path that forgot its
# db_path argument writes into the temp directory and is caught by an assertion
# there rather than by a mystery row in the real database months later.
_SAVED_RESOLVED = _paths._RESOLVED.get("inferences_path")
_paths._RESOLVED["inferences_path"] = os.path.join(_TMP, "seeded-not-production.db")


#------------------------------------------------------------------------------


# ===========================================================================
# FIXTURES
# ===========================================================================

_STAMP = {
    "fingerprint_version": 8,
    "llm_classifier_prompt_version": "1.11.0",
    "llm_classifier_renderer_digest": "deadbeef",
    "matching_model_configured": "us.anthropic.claude-sonnet-4-6",
    "matching_call_mode": "per_trial",
    "matching_temperature_sent": "0.0",
    "qdrant_collection": "trial_criteria_x",
    "collection_points": 14324,
    "data_snapshot_date": "2026-08-03",
    "campaign_cohort_size": 500,
    "campaign_cohort_seed": 42,
    "cross_encoder_revision": "abc123",
    "matching_per_trial_empty_retries": 1,
    "matching_per_trial_parallel_bound": 4,
}

_TUNABLES = json.dumps({"RRF_POOL_SIZE": 100, "TOP_K_CANDIDATES": 40},
                       sort_keys=True)


def make_db(name):
    """A database at the CURRENT schema, built by the project's own writer.

    NOT A HAND-WRITTEN CREATE TABLE. Every column, index and stamp comes from
    `initialize_database`, so a fixture cannot drift from the schema it stands
    in for -- which is the whole reason this file's databases are synthetic
    rather than copied.
    """
    path = os.path.join(_TMP, name)
    quiet(_dl.initialize_database, path)
    return path


def add_run(conn, *, status="FINISHED", resumed=None, tunables=_TUNABLES,
            started_at="2026-01-01T00:00:00Z", **stamp_overrides):
    values = dict(_STAMP)
    values.update(stamp_overrides)
    cols = (["started_at", "finished_at", "status", "invocation_source",
             "resumed", "tunables"] + list(values))
    params = ([started_at, started_at, status, "batch_runner", resumed,
               tunables] + [values[k] for k in values])
    return conn.execute(
        f"INSERT INTO runs ({','.join(cols)}) "
        f"VALUES ({','.join('?' for _ in cols)})", params).lastrowid


_INFERENCE_DEFAULTS = dict(
    timestamp="2026-01-01T00:00:00Z", age=60, condition_count=5,
    medication_count=7, candidates_retrieved=90, candidates_reranked=40,
    candidates_filtered=15, candidates_evaluated=15, eligible_matches=3,
    total_time=70.0, error=None, llm_classifier_retries=0,
    ecog_selection="most_recent_on_or_before_reference_date", ecog_value=1,
    retrieval_trials_lost=0,
    primary_condition="Malignant neoplasm of breast",
    patient_data_hash=None,
    # THE RECORDED CONFIGURATION, ON EVERY ORDINARY ROW. A fixture that left
    # these NULL would make every pair in this file UNVERIFIED -- which is the
    # rule working, and would leave nine scenarios measuring a refusal instead
    # of the metric they were written for. `PAIR_EVIDENCE_INFERENCE` is what
    # names them, so a member added to that tuple fails HERE (section 6's
    # completeness check) rather than silently reaching zero of these rows.
    matching_model="us.anthropic.claude-sonnet-4-6",
    matching_provider="bedrock_anthropic",
    matching_call_mode="per_trial",
    llm_classifier_prompt_version="1.11.0",
    cross_encoder_model="ncbi/MedCPT-Cross-Encoder",
    ablation_flags="{}",
)


def add_inference(conn, run_id, patient_id, **overrides):
    """Insert one inference row.

    `patient_data_hash` DEFAULTS ON ABSENCE, NOT ON `None`, and the difference
    is what makes section 6 mean anything: `patient_data_hash=None` is the
    state the pair rule calls UNVERIFIED, and a helper that treated it as
    "not supplied" would synthesise a hash and turn every unverified fixture
    into a verified or an incomparable one. Found by running, not by reading.
    """
    values = dict(_INFERENCE_DEFAULTS)
    values.update(overrides)
    if "patient_data_hash" not in overrides and values["patient_data_hash"] is None:
        values["patient_data_hash"] = f"hash-{patient_id}"
    cols = ["run_id", "patient_id"] + list(values)
    params = [run_id, patient_id] + [values[k] for k in values]
    return conn.execute(
        f"INSERT INTO inferences ({','.join(cols)}) "
        f"VALUES ({','.join('?' for _ in cols)})", params).lastrowid


def rows_of(db, sql, params=()):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def build_two_campaigns(name, *, comparison_extra=None, n=30):
    """A reference campaign and a later comparison campaign over one cohort."""
    db = make_db(name)
    conn = sqlite3.connect(db)
    try:
        ref = add_run(conn, started_at="2026-01-01T00:00:00Z")
        for i in range(n):
            add_inference(conn, ref, f"P{i:03d}",
                          timestamp=f"2026-01-01T00:{i:02d}:00Z",
                          age=50 + (i % 10), total_time=60.0 + i)
        cmp_run = add_run(conn, started_at="2026-06-01T00:00:00Z")
        for i in range(n):
            add_inference(conn, cmp_run, f"P{i:03d}",
                          timestamp=f"2026-06-01T00:{i:02d}:00Z",
                          age=55 + (i % 10), total_time=80.0 + i,
                          candidates_retrieved=70 + (i % 5),
                          retrieval_trials_lost=1 if i % 5 == 0 else 0)
        if comparison_extra is not None:
            comparison_extra(conn, cmp_run)
        conn.commit()
    finally:
        conn.close()
    return db, ref, cmp_run


print("\n" + "=" * 70)
print("DRIFT REDESIGN TEST")
print("=" * 70)


# ===========================================================================
# 1. THE TWO VOCABULARIES ARE CLOSED AND THE GATE IS TOTAL
# ===========================================================================

print("\n" + "=" * 70)
print("1. Closed vocabularies, and the one sentence that relates them")
print("=" * 70)

check("every status the brief asked for is a member",
      sorted(set(("computed", "not_computed_zero_variance",
                  "insufficient_data", "unverified_inputs",
                  "malformed_inputs", "read_failure", "no_data",
                  "reference_absent", "reference_mutated",
                  "refused_incomparable", "refused_column_absent"))
             - set(_ds.METRIC_STATUSES)), [])
# TWO DELIBERATE DEVIATIONS, BOTH RECORDED IN THE MODULE RATHER THAN MADE
# SILENTLY. `no_reference` was requested alongside `reference_absent` and names
# the same state; the same brief asked for CONSISTENT names, which two
# spellings of one state is the opposite of. And the two members below were NOT
# in the requested list while being required elsewhere in the same brief as
# DISTINCT refusals -- so a vocabulary without them would force two distinct
# refusals to share a member.
check("`no_reference` is NOT a member: it duplicates `reference_absent`",
      "no_reference" in _ds.METRIC_STATUSES, False)
check("`reference_unresolvable` IS a member: PART 1 requires it as a distinct "
      "refusal", "reference_unresolvable" in _ds.METRIC_STATUSES, True)
check("`no_run_identity` IS a member, for the same reason",
      "no_run_identity" in _ds.METRIC_STATUSES, True)

check("the statuses are unique", len(set(_ds.METRIC_STATUSES)),
      len(_ds.METRIC_STATUSES))
check("the policies are the three the ruling names",
      sorted(_ds.ALERT_POLICIES),
      ["alerting", "baseline_independent", "reporting_only"])

check("an unknown status RAISES rather than reaching the database",
      isinstance(drive(_ds.require_status, "insufficient"), str)
      and "RAISED UnknownDriftStateError" in drive(_ds.require_status,
                                                   "insufficient"), True)
check("an unknown policy raises too",
      "RAISED UnknownDriftStateError" in str(drive(_ds.require_policy, "loud")),
      True)
check("...and the exception is a RuntimeError, not a ValueError, so a broad "
      "`except ValueError` around a metric body cannot eat it",
      issubclass(_ds.UnknownDriftStateError, RuntimeError)
      and not issubclass(_ds.UnknownDriftStateError, ValueError), True)

# THE GATE, TOTAL OVER THE CROSS PRODUCT. Every (status, policy) pair is asked,
# so "alert is meaningful only when policy is alerting AND status is computed"
# is a measurement rather than a reading of the source.
_meaningful = {(s, p) for s in _ds.METRIC_STATUSES for p in _ds.ALERT_POLICIES
               if _ds.alert_is_meaningful(s, p)}
check("exactly the computed readings under an alerting policy are meaningful",
      sorted(_meaningful),
      sorted({("computed", "alerting"),
              ("computed", "baseline_independent")}))
check("...over a cross product that is not degenerate",
      len(_ds.METRIC_STATUSES) * len(_ds.ALERT_POLICIES), 39)

# RESOLVE IS BELT AND BRACES: a metric that computes an alert and then reports
# a status of `insufficient_data` has contradicted itself, and the safe reading
# of a contradiction is the one that does not assert a verdict.
#
# IT RETURNS A PAIR -- (value, outcome) -- AND THAT IS THE REPAIR RATHER THAN A
# CONVENIENCE. The shipped version returned a bare value and CONVERTED on the
# way: `int(bool(x))` read `None` as 0 (which the dashboard draws as a green
# OK) and `nan` as 1 (which it draws as an ALERT), so a verdict nobody computed
# arrived on the tab as a verdict somebody did. A caller cannot tell "no
# verdict, because the axes forbid one" from "no verdict, because the value was
# unreadable" out of a `None` alone, and only the second is a defect. The
# outcome is what carries that, and `metric()` downgrades a metric to
# `malformed_inputs` on seeing it.
check("a contradicting metric stores NO verdict",
      _ds.resolve_alert("insufficient_data", "alerting", 1), (None, "present"))
check("a reporting-only metric stores no verdict either, even at 1",
      _ds.resolve_alert("computed", "reporting_only", 1), (None, "present"))
check("a computed alerting metric stores 0 or 1",
      (_ds.resolve_alert("computed", "alerting", 0),
       _ds.resolve_alert("computed", "alerting", True)),
      ((0, "present"), (1, "present")))
check("...and it is an int, not a bool, so the column holds two values rather "
      "than however many spellings of truth its writers used",
      type(_ds.resolve_alert("computed", "alerting", True)[0]), int)

# ===========================================================================
# THE ALERT CONVERSION, CLASSIFIED RATHER THAN COERCED
# ===========================================================================
#
# `classify_alert` is the one place a raw value is READ, and the three outcomes
# are the whole vocabulary. MISSING is an absence the pipeline can legitimately
# produce (a NULL column, a pandas NaN round-tripping an INTEGER NULL, a
# pandas.NA); INVALID is a value that is present and is not a verdict, which no
# writer in this project produces and which must therefore never be converted
# into one.
check("the outcome vocabulary is closed and its members are distinct",
      (len(_ds.ALERT_OUTCOMES), len(set(_ds.ALERT_OUTCOMES))), (3, 3))

_missing = [None, float("nan")]
try:                                    # pandas is present; its NA is a third
    import pandas as _pd                # spelling the comparison path must
    _missing.append(_pd.NA)             # survive, and `NA != NA` is NA, not a
except Exception:                       # bool -- so a bare `if x != x` raises
    pass                                # rather than answering.
check("every spelling of an absent verdict classifies MISSING, never a value",
      sorted({_ds.classify_alert(a) for a in _missing}),
      [(_ds.ALERT_MISSING, None)])
check("...over more than one spelling, so the set is not one reading repeated",
      len(_missing) >= 2, True)

check("a real verdict classifies PRESENT and keeps its value",
      [_ds.classify_alert(a) for a in (0, 1, False, True, 0.0, 1.0)],
      [(_ds.ALERT_PRESENT, 0), (_ds.ALERT_PRESENT, 1),
       (_ds.ALERT_PRESENT, 0), (_ds.ALERT_PRESENT, 1),
       (_ds.ALERT_PRESENT, 0), (_ds.ALERT_PRESENT, 1)])
check("...and a float that round-trips exactly is the SAME int, so a pandas "
      "float64 column holding a real verdict is not rejected for its dtype",
      [type(_ds.classify_alert(a)[1]) for a in (0.0, 1.0)], [int, int])

check("a value that is present and is not a verdict classifies INVALID",
      sorted({_ds.classify_alert(a)[0]
              for a in (2, -1, 0.5, "1", "", [], {}, object())}),
      [_ds.ALERT_INVALID])
check("...and INVALID carries no value, so nothing downstream can read one",
      sorted({_ds.classify_alert(a)[1]
              for a in (2, -1, 0.5, "1")}, key=repr), [None])

# THE TWO ABSENCES ARE DISTINGUISHABLE AT THE RESOLVER, which is what the
# downgrade in `metric()` reads.
check("a missing verdict under meaningful axes resolves to None, MISSING",
      _ds.resolve_alert("computed", "alerting", None),
      (None, _ds.ALERT_MISSING))
check("an INVALID verdict under meaningful axes resolves to None, INVALID -- "
      "never to a 1, which is what `int(bool(nan))` produced",
      _ds.resolve_alert("computed", "alerting", 2),
      (None, _ds.ALERT_INVALID))
check("...and the two are different outcomes, so a caller can act on one and "
      "not the other",
      _ds.resolve_alert("computed", "alerting", None)[1]
      != _ds.resolve_alert("computed", "alerting", 2)[1], True)
check("the outcome is reported even when the axes make the verdict "
      "meaningless, so a malformed value on a reporting-only metric is still "
      "a finding rather than a silent None",
      _ds.resolve_alert("computed", "reporting_only", 2),
      (None, _ds.ALERT_INVALID))

# THE RENDERING, WHICH IS THE OTHER HALF OF THE DEFECT.
check("a reporting-only ZERO renders as a VALUE, never an OK",
      _ds.display_state("computed", "reporting_only", None),
      _ds.DISPLAY_REPORTING)
check("a computed alerting zero renders OK, which is the one reading that has "
      "earned one", _ds.display_state("computed", "alerting", 0),
      _ds.DISPLAY_OK)
check("insufficient data renders NOT COMPUTED, not OK",
      _ds.display_state("insufficient_data", "alerting", 0),
      _ds.DISPLAY_NOT_COMPUTED)
check("a reference refusal renders REFUSED -- something an operator can act "
      "on -- rather than NOT COMPUTED",
      _ds.display_state("reference_mutated", "reporting_only", None),
      _ds.DISPLAY_REFUSED)
check("...and a zero-variance reading is NOT COMPUTED rather than REFUSED: "
      "nobody can act on it, it is a property of the data",
      _ds.display_state("not_computed_zero_variance", "reporting_only", None),
      _ds.DISPLAY_NOT_COMPUTED)

# THE ONE THAT WOULD HAVE SHIPPED THE OLD DEFECT: an alert of 0 on a reading
# that never ran.
check("an alert of 0 on a reading that never ran still renders NOT COMPUTED, "
      "which is the exact byte the shipped writer used and the exact tick the "
      "shipped dashboard drew",
      _ds.display_state("read_failure", "baseline_independent", 0),
      _ds.DISPLAY_NOT_COMPUTED)

# AN ABSENT VERDICT ON A ROW WHOSE AXES PERMIT ONE IS NOT AN OK. `resolve_alert`
# cannot produce that pair, so nothing this project writes reaches it; what does
# is a pandas frame, where an INTEGER column holding NULLs reads back as float64
# and a NULL arrives as `nan`. The first version of `display_state` returned OK
# for both spellings -- the shipped defect, arriving through the database
# instead of through the code -- and it was found by driving the renderer rather
# than by reading it.
check("an alerting policy with a MISSING verdict renders NOT COMPUTED, not OK",
      sorted({_ds.display_state("computed", "baseline_independent", a)
              for a in (None, float("nan"))}), [_ds.DISPLAY_NOT_COMPUTED])
check("...and a real 0 still renders OK, so the guard did not swallow the "
      "verdict it exists to preserve",
      sorted({_ds.display_state("computed", "baseline_independent", a)
              for a in (0, 0.0)}), [_ds.DISPLAY_OK])
check("...and a real 1 still alerts, in both spellings pandas can hand it over",
      sorted({_ds.display_state("computed", "baseline_independent", a)
              for a in (1, 1.0)}), [_ds.DISPLAY_ALERT])

check("every display state has a marker",
      sorted(set(_ds.DISPLAY_STATES) - set(_ds.DISPLAY_MARKERS)), [])
check("...and the markers are distinct, so two states cannot render alike",
      len(set(_ds.DISPLAY_MARKERS.values())), len(_ds.DISPLAY_STATES))


# ===========================================================================
# 2. THE DIGEST: A MUTATION WITH THE IDS AND THE COUNT INTACT
# ===========================================================================

print("\n" + "=" * 70)
print("2. The content digest is what sees an UPDATE")
print("=" * 70)

check("the digest is a pure function of its inputs",
      _dr.content_digest([(1, "a", 2)], ("x", "y")),
      _dr.content_digest([(1, "a", 2)], ("x", "y")))
check("a changed VALUE changes it",
      _dr.content_digest([(1, "a", 2)], ("x", "y"))
      == _dr.content_digest([(1, "a", 3)], ("x", "y")), False)
check("a changed ORDER changes it, so a caller whose SELECT lost its ORDER BY "
      "is caught rather than tolerated",
      _dr.content_digest([(1, "a"), (2, "b")], ("x",))
      == _dr.content_digest([(2, "b"), (1, "a")], ("x",)), False)
check("NULL and the empty string are different content",
      _dr.content_digest([(1, None)], ("x",))
      == _dr.content_digest([(1, "")], ("x",)), False)
check("NULL and the string 'null' are different content, which JSON alone "
      "would not give",
      _dr.content_digest([(1, None)], ("x",))
      == _dr.content_digest([(1, "null")], ("x",)), False)
check("a BLOB is hashed rather than raising",
      isinstance(drive(_dr.content_digest, [(1, b"\x00\xff")], ("x",)), str),
      True)
check("...and hashes differently from its hex text, so the encoding is not a "
      "collision",
      _dr.content_digest([(1, b"\xff")], ("x",))
      == _dr.content_digest([(1, "ff")], ("x",)), False)

# THE COLUMN LIST IS DERIVED FROM THE METRICS AND IS CHECKED AGAINST THEM. A
# hand-maintained list goes stale, and the failure mode of THIS one is
# invisible: a changed input that no digest covers is a comparison quietly
# taken against something other than what was designated.
_metric_columns = set()
for _name, _cat, _rule, _kind, _column in _drift.COMPARISON_METRICS:
    if not _column.startswith("__"):
        _metric_columns.add(_column)
_metric_columns |= {"error", "eligible_matches", "candidates_evaluated"}
_metric_columns |= set(_drift.RETRIEVAL_UNDERFILL_COLUMNS)
_metric_columns |= set(_drift.RERANK_UNDERFILL_COLUMNS)
_metric_columns |= set(_drift.TRIALS_LOST_COLUMNS)
_metric_columns |= {"ecog_selection", "ecog_value"}      # availability
_metric_columns |= {"primary_condition"}                 # stratification
_metric_columns |= {"patient_id", "run_id", "patient_data_hash"}  # membership
check("the digest covers every input column any metric reads, derived from "
      "the metric declarations rather than retyped",
      sorted(_metric_columns - set(_dr.DIGEST_COLUMNS)), [])
check("...and the derivation is not degenerate", len(_metric_columns) >= 15,
      True)
check("`timestamp` is NOT covered: it is when a row was written, not an input "
      "to any metric, and covering it would make a re-import report as a "
      "mutation", "timestamp" in _dr.DIGEST_COLUMNS, False)


# ===========================================================================
# 3. CAMPAIGN MEMBERSHIP, RESUMES INCLUDED
# ===========================================================================

print("\n" + "=" * 70)
print("3. A campaign is its whole chain, not the run somebody named")
print("=" * 70)

_CHAIN_DB = make_db("chain.db")
_conn = sqlite3.connect(_CHAIN_DB)
_r1 = add_run(_conn, status="KILLED", started_at="2026-01-01T00:00:00Z")
_r2 = add_run(_conn, status="KILLED", resumed=1, started_at="2026-01-02T00:00:00Z")
_r3 = add_run(_conn, status="FINISHED", resumed=1, started_at="2026-01-03T00:00:00Z")
# A FOURTH RUN WITH A DIFFERENT CONFIGURATION, resumed=1. It must NOT stitch:
# "which configuration produced this number" is the question a campaign total
# is asked, and a re-configured run is a new campaign.
_r4 = add_run(_conn, status="FINISHED", resumed=1,
              started_at="2026-01-04T00:00:00Z",
              llm_classifier_prompt_version="2.0.0")
_conn.commit()
_conn.close()

for _label, _anchor in (("the head", _r1), ("the middle", _r2),
                        ("the tail", _r3)):
    _m = _dl.campaign_run_ids(_anchor, db_path=_CHAIN_DB)
    check(f"asked at {_label}, the campaign is the whole chain",
          (_m.resolved, _m.run_ids, _m.head_id), (True, (_r1, _r2, _r3), _r1))

_m4 = _dl.campaign_run_ids(_r4, db_path=_CHAIN_DB)
check("a run with a DIFFERENT fingerprint is its own campaign, even though it "
      "carries resumed=1", (_m4.resolved, _m4.run_ids), (True, (_r4,)))

_missing = _dl.campaign_run_ids(999999, db_path=_CHAIN_DB)
check("an id that names no row is an unresolved membership with a REASON, not "
      "a raise and not an empty success",
      (_missing.resolved, _missing.reason),
      (False, _dl.CAMPAIGN_MEMBERSHIP_UNRESOLVED))
check("None is tolerated the same way",
      _dl.campaign_run_ids(None, db_path=_CHAIN_DB).resolved, False)

# A RUN WITH NO STAMP IS ITS OWN CAMPAIGN OF ONE, AND SAYS SO. It exists, it
# produced rows, and nothing can be stitched to it -- which is a resolution
# with a caveat rather than a failure.
_NOSTAMP_DB = make_db("nostamp.db")
_conn = sqlite3.connect(_NOSTAMP_DB)
_ns = _conn.execute(
    "INSERT INTO runs (started_at, status, invocation_source) "
    "VALUES ('2026-01-01T00:00:00Z','FINISHED','batch_runner')").lastrowid
_conn.commit(); _conn.close()
_mns = _dl.campaign_run_ids(_ns, db_path=_NOSTAMP_DB)
check("an unstamped run resolves to itself alone, with the reason recorded",
      (_mns.resolved, _mns.run_ids, _mns.reason),
      (True, (_ns,), _dl.CAMPAIGN_MEMBERSHIP_NO_STAMP))

# PINNED AGAINST THE OTHER WALKER. `campaign_spend_before` walks the same rule
# BACKWARD only; a restated rule is a rule that can drift, so the two are
# checked against each other rather than promised to agree.
_spend = _dl.campaign_spend_before(_r3, db_path=_CHAIN_DB)
check("the backward walker and the two-way walker agree on the chain",
      tuple(sorted(set(_spend.run_ids) | {_r3})),
      _dl.campaign_run_ids(_r3, db_path=_CHAIN_DB).run_ids)
check("...and the backward walker really found something, so the agreement is "
      "not two empty sets", len(_spend.run_ids) >= 2, True)


# ===========================================================================
# 4. LAYER 1 -- DEDUP IS THE FIRST ATTEMPT, BEFORE ANY SUCCESS FILTERING
# ===========================================================================

print("\n" + "=" * 70)
print("4. First attempt per patient, applied before success filtering")
print("=" * 70)

_DEDUP_DB = make_db("dedup.db")
_conn = sqlite3.connect(_DEDUP_DB)
_run = add_run(_conn)
# PATIENT A: the first attempt ERRORED and the second succeeded. This is the
# resample/retry shape, and it is the case the ordering exists for.
_a_first = add_inference(_conn, _run, "A", timestamp="2026-01-01T00:00:00Z",
                         error="APIError: boom", candidates_evaluated=0)
_a_second = add_inference(_conn, _run, "A", timestamp="2026-01-01T00:05:00Z")
# PATIENT B: one row, clean.
_b = add_inference(_conn, _run, "B", timestamp="2026-01-01T00:01:00Z")
# PATIENT C: a timestamp that DISAGREES with the id order.
_c_first = add_inference(_conn, _run, "C", timestamp="2026-01-01T09:00:00Z")
_c_second = add_inference(_conn, _run, "C", timestamp="2026-01-01T08:00:00Z")
_conn.commit(); _conn.close()

_conn = sqlite3.connect(_DEDUP_DB)
_rows = _dr.campaign_rows(_conn, (_run,))

check("dedup keeps the FIRST attempt per patient by id",
      _rows.row_ids, tuple(sorted((_a_first, _b, _c_first))))
check("...which for patient A is the one that ERRORED -- dedup runs BEFORE "
      "success filtering, so a failure cannot be promoted away",
      _a_first in _rows.row_ids and _a_second not in _rows.row_ids, True)
check("the counts are reported", (_rows.rows_before_dedup,
                                  _rows.duplicate_rows, _rows.patients),
      (5, 2, 3))
check("the id/timestamp disagreement is DETECTED",
      _rows.order_agrees, False)
check("...and names the patient and both rows",
      all(token in first(_rows.order_disagreements, "")
          for token in ("C", str(_c_first), str(_c_second))), True)
check("...and exactly one patient disagrees, so the detector is not reporting "
      "everything", len(_rows.order_disagreements), 1)
check("...and the id answer was USED, not the timestamp one: it is the one "
      "with a guarantee behind it, and a disagreement is something to report "
      "rather than a reason to refuse a reading",
      _c_first in _rows.row_ids, True)

# THE WRONG ORDER, DEMONSTRATED. Filtering to successes first and THEN taking
# the first of what is left promotes A's SECOND attempt -- and the metric that
# exists to report failures is computed over a population with none in it.
_wrong_order = _dr.eligible_row_ids(
    _conn, _dr.campaign_rows(_conn, (_run,)).row_ids, _dr.ROW_RULE_STAGE5)
_success_rows = [r[0] for r in _conn.execute(
    "SELECT id FROM inferences WHERE COALESCE(error,'')='' "
    "AND candidates_evaluated > 0 ORDER BY id")]
_filter_then_dedup = {}
for _rid, _pid in _conn.execute(
        f"SELECT id, patient_id FROM inferences WHERE id IN "
        f"({','.join(str(r) for r in _success_rows)}) ORDER BY id"):
    _filter_then_dedup.setdefault(_pid, _rid)
check("filter-then-dedup would promote A's SECOND attempt to 'first'",
      _filter_then_dedup.get("A"), _a_second)
check("...and the shipped order does not: A's failure is simply absent from "
      "the Stage-5 population, and its FIRST row is what the ALL rule carries",
      (_a_second in _wrong_order, _a_first in _rows.row_ids), (False, True))
_conn.close()


# ===========================================================================
# 5. LAYER 2 -- PER-METRIC ELIGIBILITY, AND THE VISIBLE FAILURE
# ===========================================================================

print("\n" + "=" * 70)
print("5. A failed patient is visible to the metrics that exist to see it")
print("=" * 70)

_ELIG_DB = make_db("eligibility.db")
_conn = sqlite3.connect(_ELIG_DB)
_run = add_run(_conn)
_ok_row = add_inference(_conn, _run, "OK")
# A PATIENT WHOSE STAGE 5 FAILED. It has retrieval numbers and no verdicts.
_failed_row = add_inference(_conn, _run, "FAILED", error="APIError: boom",
                            candidates_evaluated=0, eligible_matches=0)
# A PATIENT THAT ENDED CLEANLY WITH NO CANDIDATES. No error, and nothing to
# judge -- the two terms of the Stage-5 rule, neither implying the other.
_empty_row = add_inference(_conn, _run, "EMPTY", candidates_evaluated=0,
                           eligible_matches=0)
# ...AND ONE THAT ERRORED *AFTER* STAGE 5 ANSWERED, which is the other half.
_late_row = add_inference(_conn, _run, "LATE", error="write failed",
                          candidates_evaluated=15)
_conn.commit(); _conn.close()

_conn = sqlite3.connect(_ELIG_DB)
_all_rows = _dr.campaign_rows(_conn, (_run,)).row_ids
_rule_all = _dr.eligible_row_ids(_conn, _all_rows, _dr.ROW_RULE_ALL)
_rule_s5 = _dr.eligible_row_ids(_conn, _all_rows, _dr.ROW_RULE_STAGE5)

check("the ALL rule keeps every deduplicated row, failures included",
      sorted(_rule_all), sorted(_all_rows))
check("the FAILED patient is in it -- which is the requirement: a patient "
      "failing Stage 5 must appear where failures are reported",
      _failed_row in _rule_all, True)
check("the Stage-5 rule keeps only the completed one",
      sorted(_rule_s5), [_ok_row])
check("...so the failed patient is ABSENT from Stage-5 comparisons",
      _failed_row in _rule_s5, False)
check("a clean run with no candidates is excluded too -- its match-quality "
      "denominator is zero, and neither term of the rule implies the other",
      _empty_row in _rule_s5, False)
check("...and so is a row that errored AFTER Stage 5 answered, which is why "
      "the rule has two terms", _late_row in _rule_s5, False)

check("an unknown rule raises rather than silently admitting everything",
      "RAISED ValueError" in str(drive(_dr.eligible_row_ids, _conn, _all_rows,
                                       "whatever")), True)
_conn.close()

# THE TWO METRICS THAT WOULD BE IDENTICALLY WRONG UNDER THE OTHER RULE.
_rules = {n: r for n, _c, r, _k, _col in _drift.COMPARISON_METRICS}
check("error_rate declares the ALL rule -- under the Stage-5 rule every "
      "surviving row has a clean error by construction and the metric would "
      "report 0.0 for a run that errored on half its cohort",
      _rules["error_rate_z_score"], _dr.ROW_RULE_ALL)
check("match_quality declares the Stage-5 rule -- its denominator is "
      "candidates_evaluated, and the shipped code substituted 0 for a zero "
      "denominator with np.where, pulling the mean toward zero for every "
      "no-candidates patient and reporting that as a quality drop",
      _rules["match_quality_z_score"], _dr.ROW_RULE_STAGE5)
check("every declared rule is a member of the closed vocabulary",
      sorted(set(_rules.values()) - set(_dr.ROW_RULES)), [])
check("...and BOTH rules are actually used, so the split is not decoration",
      sorted(set(_rules.values())), sorted(_dr.ROW_RULES))


# ===========================================================================
# 6. PAIRING -- VERIFIED, UNVERIFIED, INCOMPARABLE, AND THE LEFTOVERS
# ===========================================================================

print("\n" + "=" * 70)
print("6. Three outcomes, and the middle one is the point")
print("=" * 70)

_PAIR_DB = make_db("pairing.db")
_conn = sqlite3.connect(_PAIR_DB)
_ref_run = add_run(_conn, started_at="2026-01-01T00:00:00Z")
_cmp_run = add_run(_conn, started_at="2026-06-01T00:00:00Z")

# THE EVIDENCE IS THE DECLARED SET, NOT THE HASH. The shipped rule compared
# `patient_data_hash` alone, so two campaigns judged by DIFFERENT MODELS over
# the SAME patients produced pairs it called verified -- and a paired z-score
# over them measures the model change under the word "drift". Every member of
# `PAIR_EVIDENCE_INFERENCE` is driven here, one patient each, so a member added
# to that tuple and not checked leaves a named gap rather than a silent one.
_conn_items = _dr._pair_evidence_items(_conn)
check("the declared per-row evidence is what this database supplies",
      tuple(i for i in _conn_items if i != _dr.PAIR_EVIDENCE_RUN),
      _dr.PAIR_EVIDENCE_INFERENCE)
check("...and the run stamp is checked as ONE composite item beside it, "
      "because RUN_FINGERPRINT_COLUMNS is already this project's answer to "
      "'may these two populations be treated as one'",
      _dr.PAIR_EVIDENCE_RUN in _conn_items, True)
check("...over a set that is not degenerate",
      len(_dr.PAIR_EVIDENCE_INFERENCE) >= 5, True)

# DIFFERING VALUES, one per evidence item. Each is a real configuration change
# a campaign can undergo between a reference and a comparison.
_DIFFERENT = {
    "patient_data_hash": "hash-MOVED",
    "matching_model": "gpt-5.6-terra",
    "matching_provider": "openai",
    "matching_call_mode": "grouped",
    "llm_classifier_prompt_version": "1.10.0",
    "cross_encoder_model": "ncbi/MedCPT-Query-Encoder",
    "ablation_flags": '{"no_mesh_filter": true}',
}
check("every declared per-row item has a differing value to drive it with",
      sorted(_DIFFERENT), sorted(_dr.PAIR_EVIDENCE_INFERENCE))

_ref_ids, _cmp_ids = {}, {}


def _pairfix(pid, **cmp_overrides):
    _ref_ids[pid] = add_inference(_conn, _ref_run, pid)
    _cmp_ids[pid] = add_inference(_conn, _cmp_run, pid, **cmp_overrides)


_pairfix("SAME")                                    # verified
for _item, _value in sorted(_DIFFERENT.items()):    # incomparable, one each
    _pairfix("DIFF-" + _item, **{_item: _value})
_pairfix("NULLREF")                                 # unverified: unreadable
_conn.execute("UPDATE inferences SET patient_data_hash = NULL WHERE id = ?",
              (_ref_ids["NULLREF"],))
_pairfix("NULLCMP", patient_data_hash=None)
_pairfix("BOTHNULL", patient_data_hash=None)
_conn.execute("UPDATE inferences SET patient_data_hash = NULL WHERE id = ?",
              (_ref_ids["BOTHNULL"],))
_pairfix("EMPTYSTR", patient_data_hash="")          # '' is not a hash
_pairfix("NULLMODEL", matching_model=None)          # a non-hash item, unread

# THE RUN HALF. A comparison run under a different stamp makes every pair drawn
# from it incomparable -- and it is the item no per-row column can supply.
_other_cfg_run = add_run(_conn, started_at="2026-06-01T00:00:00Z",
                         matching_model_configured="gpt-5.6-terra")
_ref_ids["CFG"] = add_inference(_conn, _ref_run, "CFG")
_cmp_ids["CFG"] = add_inference(_conn, _other_cfg_run, "CFG")

# A run that recorded NO stamp at all is UNVERIFIED, never verified: two
# unrecorded configurations compared as equal is the null-safe-equality trap.
_unstamped_run = add_run(_conn, started_at="2026-06-01T00:00:00Z",
                         fingerprint_version=None)
_ref_ids["NOSTAMP"] = add_inference(_conn, _ref_run, "NOSTAMP")
_cmp_ids["NOSTAMP"] = add_inference(_conn, _unstamped_run, "NOSTAMP")

# INCOMPARABLE OUTRANKS UNVERIFIED: an OBSERVED difference is stronger evidence
# than an absence, so one unreadable column must not downgrade a real
# incompatibility into "we could not tell".
_pairfix("OUTRANK", matching_model="gpt-5.6-terra", patient_data_hash=None)

_ref_only = add_inference(_conn, _ref_run, "REFONLY")
_cmp_only = add_inference(_conn, _cmp_run, "CMPONLY")
_conn.commit(); _conn.close()

_conn = sqlite3.connect(_PAIR_DB)
_pairs = _dr.pair_rows(_conn,
                       sorted(list(_ref_ids.values()) + [_ref_only]),
                       sorted(list(_cmp_ids.values()) + [_cmp_only]))


def _patients(pairs):
    ref = {v: k for k, v in _ref_ids.items()}
    return sorted(ref[a] for a, b in pairs)


check("only the pair whose WHOLE recorded configuration agrees is VERIFIED",
      _patients(_pairs.verified), ["SAME"])
check("a difference in ANY declared evidence item is INCOMPARABLE -- not the "
      "patient hash alone, which is what the shipped rule compared",
      _patients(_pairs.incomparable),
      sorted(["CFG", "OUTRANK"] + ["DIFF-" + i for i in _DIFFERENT]))
check("...and the report NAMES which fact disagreed, once per pair, so an "
      "operator can tell a model change from a re-indexed corpus",
      dict(sorted(_pairs.incomparable_on.items())),
      dict(sorted({**{i: 1 for i in _DIFFERENT},
                   "matching_model": 2,        # DIFF-matching_model + OUTRANK
                   _dr.PAIR_EVIDENCE_RUN: 1}.items())))
check("every pair whose check could not be RUN is UNVERIFIED, on both sides "
      "and for the empty string as well as NULL",
      _patients(_pairs.unverified),
      ["BOTHNULL", "EMPTYSTR", "NOSTAMP", "NULLCMP", "NULLMODEL", "NULLREF"])
check("...and an unreadable item is named too, so 'no verified pair survives' "
      "can be told apart from a database that never recorded the fact",
      dict(sorted(_pairs.unverified_on.items())),
      {"matching_model": 1, "patient_data_hash": 4,
       _dr.PAIR_EVIDENCE_RUN: 1})
check("INCOMPARABLE OUTRANKS UNVERIFIED: a pair with one item differing and "
      "another unreadable is incomparable, and is NOT also counted unverified",
      ("OUTRANK" in _patients(_pairs.incomparable),
       "OUTRANK" in _patients(_pairs.unverified)), (True, False))
check("...and unverified is NOT folded into either of the others: calling it "
      "verified asserts a check that did not happen, and calling it "
      "incomparable asserts a difference nobody observed",
      len(_pairs.verified) + len(_pairs.unverified) + len(_pairs.incomparable),
      len(_ref_ids))
check("the unpaired leftovers are reported BY PATIENT, so a cohort change is "
      "visible rather than silent",
      (_pairs.reference_only, _pairs.comparison_only),
      (("REFONLY",), ("CMPONLY",)))
check("every count is on the result",
      sorted(_pairs.counts),
      ["comparison_only_patients", "incomparable_pairs",
       "reference_only_patients", "unverified_pairs", "verified_pairs"])
check("...and the census is DERIVED from the outcome vocabulary, so a fourth "
      "outcome cannot be added and silently left out of it",
      sorted(_pairs.by_outcome()), sorted(_dr.PAIR_OUTCOMES))

# REPORTING-ONLY DOES NOT WAIVE THIS. The metrics do not alert, so no machine
# acts on the number -- and a human reading a paired statistic beside two
# campaigns judged by different models is being handed a measurement of the
# model change under the word "drift". The deferral is of the ALERT.
check("a model change leaves NO verified pair at all, so a reporting-only "
      "comparison over it refuses rather than reporting a number",
      [p for p in _patients(_pairs.verified) if p.startswith("DIFF-")], [])

# A DUPLICATE ON EITHER SIDE RAISES. A silent last-one-wins would pair a
# patient's resample against another patient's first attempt.
_conn2 = sqlite3.connect(_PAIR_DB)
_extra = add_inference(_conn2, _ref_run, "SAME")
_conn2.commit()
check("a set that was not deduplicated RAISES rather than picking one",
      "RAISED ValueError" in str(drive(
          _dr.pair_rows, _conn2,
          sorted(list(_ref_ids.values()) + [_extra]),
          sorted(_cmp_ids.values()))), True)
_conn2.execute("DELETE FROM inferences WHERE id = ?", (_extra,))
_conn2.commit(); _conn2.close()
_conn.close()


# ===========================================================================
# 7. THE REPORTING-ONLY DENOMINATORS
# ===========================================================================

print("\n" + "=" * 70)
print("7. Denominators come from the RUN, and a zero one is never divided")
print("=" * 70)

_UNDER_DB = make_db("underfill.db")
_conn = sqlite3.connect(_UNDER_DB)
# ONE CAMPAIGN AT RRF_POOL_SIZE = 100, and a SECOND at 50. `tunables` is
# deliberately NOT a fingerprint field, so two runs may legitimately differ --
# which is why the denominator is resolved per ROW and not per campaign.
_run100 = add_run(_conn)
_run50 = add_run(_conn, tunables=json.dumps(
    {"RRF_POOL_SIZE": 50, "TOP_K_CANDIDATES": 40}, sort_keys=True))
_run_no_tunables = add_run(_conn, tunables=None)
_full = add_inference(_conn, _run100, "FULL", candidates_retrieved=100,
                      candidates_reranked=40)
_half = add_inference(_conn, _run100, "HALF", candidates_retrieved=50,
                      candidates_reranked=40)
_small_pool = add_inference(_conn, _run50, "SMALLPOOL",
                            candidates_retrieved=50, candidates_reranked=40)
_untunable = add_inference(_conn, _run_no_tunables, "UNTUNABLE",
                           candidates_retrieved=50, candidates_reranked=40)
_nothing = add_inference(_conn, _run100, "NOTHING", candidates_retrieved=0,
                         candidates_reranked=0, retrieval_trials_lost=0)
_lost = add_inference(_conn, _run100, "LOST", candidates_retrieved=90,
                      candidates_reranked=40, retrieval_trials_lost=10)
_unreported = add_inference(_conn, _run100, "UNREPORTED",
                            retrieval_trials_lost=None)
_conn.commit(); _conn.close()

_conn = sqlite3.connect(_UNDER_DB)

# --- retrieval_underfill: denominator is the RUN's own RRF_POOL_SIZE --------
_ru_full = _drift.retrieval_underfill(_conn, [_full])
check("a full pool is 0.0 underfilled", _ru_full["metric_value"], 0.0)
check("...and renders as a VALUE rather than an OK, because the metric is "
      "reporting-only",
      _ds.display_state(_ru_full["status"], _ru_full["policy"],
                        _ru_full["alert"]), _ds.DISPLAY_REPORTING)
check("half a pool of 100 is 0.5",
      _drift.retrieval_underfill(_conn, [_half])["metric_value"], 0.5)
check("...and the SAME 50 trials against a run whose recorded pool was 50 is "
      "0.0 -- the denominator is the run's, not today's config",
      _drift.retrieval_underfill(_conn, [_small_pool])["metric_value"], 0.0)

_ru_untunable = _drift.retrieval_underfill(_conn, [_untunable])
check("a run with no recorded tunables is unverified_inputs, NOT a fraction "
      "over today's config",
      _ru_untunable["status"], _ds.STATUS_UNVERIFIED_INPUTS)
check("...and the row is COUNTED in its own bucket",
      _ru_untunable["excluded"].get(_drift.EXCLUDED_UNVERIFIED_DENOMINATOR), 1)
check("...and no value is reported", _ru_untunable["metric_value"], None)

# --- rerank_underfill: min(retrieved, cap), and the zero denominator -------
check("rerank underfill is measured against min(retrieved, TOP_K): a patient "
      "with 40 reranked out of 90 retrieved and a cap of 40 is COMPLETE",
      _drift.rerank_underfill(_conn, [_lost])["metric_value"], 0.0)

_run_zero = _drift.rerank_underfill(_conn, [_nothing])
check("a patient who retrieved NOTHING has no rerank fraction at all",
      _run_zero["metric_value"], None)
check("...the row is EXCLUDED and COUNTED under zero_denominator, never "
      "divided -- 0.0 would read as a full rerank and 1.0 as one that dropped "
      "everything",
      _run_zero["excluded"].get(_drift.EXCLUDED_ZERO_DENOMINATOR), 1)
check("...and the status is no_data rather than a computed zero",
      _run_zero["status"], _ds.STATUS_NO_DATA)

_mixed = _drift.rerank_underfill(_conn, [_full, _nothing])
check("beside a divisible row, the zero-denominator row leaves the MEAN and "
      "stays in the census", (_mixed["metric_value"], _mixed["sample_size"],
                              _mixed["excluded"].get(
                                  _drift.EXCLUDED_ZERO_DENOMINATOR)),
      (0.0, 1, 1))

# --- trials_lost: the denominator quoted from the code that records it ------
_tl_ind = _drift.trials_lost_indicator(_conn, [_full, _lost])
check("the indicator is the fraction of rows that lost anything",
      _tl_ind["metric_value"], 0.5)

_tl_frac = _drift.trials_lost_fraction(_conn, [_lost])
check("the fraction is lost / (retrieved + lost) -- the ranked-in count, "
      "which is an IDENTITY of the recording code rather than an estimate: "
      "every ranked id ends in `trials` or in `trials_lost`, and "
      "candidates_retrieved is len(hybrid_results)",
      _tl_frac["metric_value"], 10 / 100)

_tl_none = _drift.trials_lost_fraction(_conn, [_nothing])
check("nothing ranked in at all is excluded, never divided",
      (_tl_none["metric_value"],
       _tl_none["excluded"].get(_drift.EXCLUDED_ZERO_DENOMINATOR)), (None, 1))
_tl_unreported = _drift.trials_lost_indicator(_conn, [_unreported])
check("a NULL retrieval_trials_lost is Stage 2 never having reported, and is "
      "counted rather than read as a zero",
      (_tl_unreported["status"],
       _tl_unreported["excluded"].get(_drift.EXCLUDED_NO_VALUE)),
      (_ds.STATUS_NO_DATA, 1))

# --- a never-observed column -----------------------------------------------
# `retrieval_trials_lost` IS in this schema, so "never observed" is the column
# being present and every row NULL -- which is `no_data`, and is a different
# finding from the column not being there (`refused_column_absent`). Both are
# driven, and section 9 drives the absent-column half against a real schema.
check("a column that exists and has never been written is no_data",
      _drift.trials_lost_indicator(_conn, [_unreported])["status"],
      _ds.STATUS_NO_DATA)

# --- a row that contradicts its own denominator ----------------------------
# The pipeline says this cannot happen: `candidates_retrieved` is capped at
# `RRF_POOL_SIZE` by the slice that produces it. It is COUNTED rather than
# clamped, because clamping hides a data-integrity finding inside a plausible
# mean -- and it resolves to `malformed_inputs` rather than `no_data`, because
# the rows ARE there and they are wrong, which is a different remedy from "run
# the pipeline again".
_conn2 = sqlite3.connect(_UNDER_DB)
_over = add_inference(_conn2, _run100, "OVERPOOL", candidates_retrieved=120,
                      candidates_reranked=40)
_conn2.commit()
_over_result = _drift.retrieval_underfill(_conn2, [_over])
check("a row exceeding its own denominator is NOT clamped into the mean",
      _over_result["metric_value"], None)
check("...it is counted in its own bucket",
      _over_result["excluded"].get(_drift.EXCLUDED_OVER_DENOMINATOR), 1)
check("...and the status is malformed_inputs, not no_data: the rows are there "
      "and they contradict the code that records them",
      _over_result["status"], _ds.STATUS_MALFORMED_INPUTS)
check("...and beside a divisible row it leaves the MEAN without taking the "
      "metric down with it",
      (_drift.retrieval_underfill(_conn2, [_full, _over])["metric_value"],
       _drift.retrieval_underfill(_conn2, [_full, _over])["sample_size"]),
      (0.0, 1))
_conn2.execute("DELETE FROM inferences WHERE id = ?", (_over,))
_conn2.commit(); _conn2.close()

# --- a bool denominator ----------------------------------------------------
# `isinstance(True, int)` is True in Python, so a tunable that arrived as True
# would otherwise be read as a pool size of 1 and every row would report 0%
# underfill against a pool of one.
check("a bool is not a positive int", _drift._positive_int(True), None)
check("...and neither is a bool zero", _drift._positive_int(False), None)
check("a real int is", _drift._positive_int(100), 100)
check("a non-negative int accepts 0 and refuses bools",
      (_drift._non_negative_int(0), _drift._non_negative_int(True)), (0, None))
_conn.close()


# ===========================================================================
# 8. STRATA, INCLUDING THE EXPLICIT UNKNOWN ONE
# ===========================================================================

print("\n" + "=" * 70)
print("8. Every row lands in exactly one stratum, `unknown` included")
print("=" * 70)

check("a recorded display maps through the project's one owner",
      _dr.stratum_for("Malignant neoplasm of breast"),
      _pc.cancer_group_key("Malignant neoplasm of breast"))
check("a NULL primary_condition is the EXPLICIT unknown stratum",
      _dr.stratum_for(None), _dr.STRATUM_UNKNOWN)
check("...and so is a blank one", _dr.stratum_for("   "), _dr.STRATUM_UNKNOWN)
check("...and `unknown` is the project's own CANCER_GROUP_UNRESOLVED rather "
      "than a new name", _dr.STRATUM_UNKNOWN, _pc.CANCER_GROUP_UNRESOLVED)
# THE DISTINCTION THAT MATTERS. `cancer_group_key` answers `other` for a NULL
# display -- right for its own contract, wrong here: a row whose
# primary_condition was never recorded has not been classified as `other`, it
# has not been classified at all.
check("...and it is NOT `other`, which is what cancer_group_key answers for a "
      "NULL and which would put an un-stratified population inside a named "
      "stratum",
      (_pc.cancer_group_key(None), _dr.stratum_for(None)),
      (_pc.CANCER_GROUP_OTHER, _pc.CANCER_GROUP_UNRESOLVED))
check("...and the two are different values, so that check is not comparing a "
      "name with itself",
      _pc.CANCER_GROUP_OTHER == _pc.CANCER_GROUP_UNRESOLVED, False)

_STRAT_DB = make_db("strata.db")
_conn = sqlite3.connect(_STRAT_DB)
_run = add_run(_conn)
_s_breast = add_inference(_conn, _run, "B",
                          primary_condition="Malignant neoplasm of breast")
_s_lung = add_inference(_conn, _run, "L",
                        primary_condition="Malignant neoplasm of lung")
_s_null = add_inference(_conn, _run, "N", primary_condition=None)
_s_blank = add_inference(_conn, _run, "W", primary_condition="")
_s_odd = add_inference(_conn, _run, "O", primary_condition="Kaposi sarcoma")
_conn.commit(); _conn.close()

_conn = sqlite3.connect(_STRAT_DB)
_strata = _dr.strata_for_rows(_conn, [_s_breast, _s_lung, _s_null, _s_blank,
                                      _s_odd])
check("every row is assigned a stratum", sorted(_strata),
      sorted([_s_breast, _s_lung, _s_null, _s_blank, _s_odd]))
check("the recorded ones get their group",
      (_strata[_s_breast], _strata[_s_lung]), ("breast", "lung"))
check("the unrecorded ones get the explicit unknown stratum",
      (_strata[_s_null], _strata[_s_blank]),
      (_dr.STRATUM_UNKNOWN, _dr.STRATUM_UNKNOWN))
check("a recorded display that matches no keyword gets `other`, which is a "
      "CLASSIFICATION and not an absence", _strata[_s_odd],
      _pc.CANCER_GROUP_OTHER)
check("...so `other` and `unknown` are separate populations here, which is "
      "the whole reason the unknown stratum is explicit",
      _strata[_s_odd] == _strata[_s_null], False)
check("every stratum is a member of the project's closed vocabulary",
      sorted(set(_strata.values()) - set(_pc.CANCER_GROUPS)), [])
_conn.close()


# ===========================================================================
# 9. THE WHOLE PIPELINE, END TO END, ON SYNTHETIC CURRENT-SCHEMA DATABASES
# ===========================================================================

print("\n" + "=" * 70)
print("9. Nine scenarios, each driven through the real run_drift_detection")
print("=" * 70)


def statuses_of(results):
    """``{metric_name: status}`` over every category of one run."""
    out = {}
    for category in _drift.METRIC_CATEGORIES:
        for name, data in results.get(category, {}).items():
            out[name] = data["status"]
    return out


# --- 9a. THE HAPPY PATH ----------------------------------------------------
_HAPPY_DB, _happy_ref_run, _happy_cmp_run = build_two_campaigns("happy.db")
_designation, _ = quiet(_dr.designate_reference, _HAPPY_DB, _happy_ref_run,
                        label="jan-baseline")
check("designation names the campaign, the rows and a digest",
      (at(_designation, "row_count"), at(_designation, "patient_count"),
       len(str(at(_designation, "content_digest")))), (30, 30, 64))
check("...and records which columns it covered, so a later resolution reads "
      "THAT list rather than today's constant",
      sorted(at(_designation, "digest_columns"))
      == sorted(c for c in _dr.DIGEST_COLUMNS
                if c in {r["name"] for r in rows_of(
                    _HAPPY_DB, "SELECT name FROM pragma_table_info('inferences')")}),
      True)

_happy, _happy_log = quiet(_drift.run_drift_detection,
                           _drift.COMPARISON_LATEST, db_path=_HAPPY_DB)
_happy_statuses = statuses_of(_happy)
check("the reference resolved", at(at(_happy, "summary"), "reference"),
      _dr.REFERENCE_OK)
check("availability computed",
      at(_happy_statuses, "ecog_unavailable_rate"), _ds.STATUS_COMPUTED)
check("a comparison metric computed",
      at(_happy_statuses, "age_ks_test"), _ds.STATUS_COMPUTED)
check("the reporting-only family computed",
      at(_happy_statuses, "retrieval_underfill"), _ds.STATUS_COMPUTED)
check("...and carries the REFERENCE's value beside its own, so a reader gets "
      "the comparison without the metric depending on one",
      at(at(at(_happy, "retrieval_drift"), "retrieval_underfill"),
         "baseline_mean") is not None, True)
check("every comparison metric is reporting_only -- calibration is deferred",
      sorted({at(at(_happy, c), n)["policy"]
              for n, c, _r, _k, _col in _drift.COMPARISON_METRICS}),
      [_ds.POLICY_REPORTING_ONLY])
check("...and NOT ONE of them carries a verdict",
      sorted({at(at(_happy, c), n)["alert"]
              for n, c, _r, _k, _col in _drift.COMPARISON_METRICS}), [None])
check("the only alerting family is availability",
      at(at(_happy, "data_availability"), "ecog_unavailable_rate")["policy"],
      _ds.POLICY_BASELINE_INDEPENDENT)

# --- 9b. MISSING DESIGNATION ----------------------------------------------
_NODESIG_DB, _, _ = build_two_campaigns("nodesig.db")
_nodesig, _ = quiet(_drift.run_drift_detection,
                    _drift.COMPARISON_LATEST, db_path=_NODESIG_DB)
_nodesig_statuses = statuses_of(_nodesig)
check("a missing designation is a NAMED refusal, not a fallback to the "
      "earliest rows", at(at(_nodesig, "summary"), "reference"),
      _dr.REFERENCE_ABSENT)
check("...and it downgrades ONLY the reference-consuming metrics",
      at(_nodesig_statuses, "age_ks_test"), _ds.STATUS_REFERENCE_ABSENT)
check("availability is untouched",
      at(_nodesig_statuses, "ecog_unavailable_rate"), _ds.STATUS_COMPUTED)
check("...and so is the reporting-only family, which computes without one",
      at(_nodesig_statuses, "retrieval_underfill"), _ds.STATUS_COMPUTED)
check("the refusal names the remedy",
      "--designate-reference" in str(at(at(_nodesig, "summary"),
                                        "reference_detail")), True)
check("...and, on a database that HAS the table, says the table is there and "
      "empty rather than absent",
      "no active row in drift_reference" in str(at(at(_nodesig, "summary"),
                                                   "reference_detail")), True)

# A DATABASE WITH NO `drift_reference` TABLE AT ALL is the same outcome -- the
# remedy is identical -- and the DETAIL says which, because a message reading
# "no active row" over a table that is not there sends an operator to look for
# rows in nothing.
_PRE16_REF = os.path.join(_TMP, "pre16_noref.db")
_conn = sqlite3.connect(_PRE16_REF)
_conn.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, started_at TEXT, "
              "status TEXT, invocation_source TEXT, resumed INTEGER, "
              "fingerprint_version INTEGER)")
_conn.execute("CREATE TABLE inferences (id INTEGER PRIMARY KEY, "
              "patient_id TEXT, timestamp TEXT, run_id INTEGER)")
_conn.commit(); _conn.close()
_no_table = _dr.resolve_reference(_PRE16_REF)
check("a database with no drift_reference table is still `reference_absent`",
      _no_table.outcome, _dr.REFERENCE_ABSENT)
check("...and the detail names the migration rather than the empty table",
      ("no drift_reference table" in str(_no_table.detail)
       and "no active row" not in str(_no_table.detail)), True)
check("...and resolving it created nothing: the connection is mode=ro, so a "
      "reader asking whether a designation exists cannot answer by making a "
      "database that has none",
      sorted(r["name"] for r in rows_of(
          _PRE16_REF, "SELECT name FROM sqlite_master WHERE type='table'")),
      ["inferences", "runs"])

# --- 9c. MUTATED REFERENCE: ids intact, content changed --------------------
_MUT_DB, _mut_ref_run, _ = build_two_campaigns("mutated.db")
_mut_designation, _ = quiet(_dr.designate_reference, _MUT_DB, _mut_ref_run)
_before = _dr.resolve_reference(_MUT_DB)
_conn = sqlite3.connect(_MUT_DB)
_mutated_rows = _conn.execute(
    "UPDATE inferences SET age = age + 1 WHERE id = ?",
    (first(at(_mut_designation, "row_ids"), 0),)).rowcount
_conn.commit(); _conn.close()
_after = _dr.resolve_reference(_MUT_DB)
_still_there = rows_of(_MUT_DB,
                       "SELECT COUNT(*) AS n FROM inferences WHERE id IN "
                       "(%s)" % ",".join(
                           str(i) for i in at(_mut_designation, "row_ids")))
check("the designation resolved BEFORE the edit", _before.outcome,
      _dr.REFERENCE_OK)
check("exactly one row was edited, and every designated id is STILL THERE -- "
      "so ids and counts cannot see this and only a content digest can",
      (_mutated_rows, first(_still_there, {}).get("n")),
      (1, at(_mut_designation, "row_count")))
check("the digest catches it", _after.outcome, _dr.REFERENCE_MUTATED)
check("...and says so rather than reporting an absence",
      "content has changed" in str(_after.detail), True)
check("...and the two digests are both recorded, so a reader can see they "
      "differ rather than take the verdict",
      _after.expected_digest != _after.actual_digest
      and _after.expected_digest is not None
      and _after.actual_digest is not None, True)

_mut, _ = quiet(_drift.run_drift_detection,
                _drift.COMPARISON_LATEST, db_path=_MUT_DB)
_mut_statuses = statuses_of(_mut)
check("the comparison metrics refuse with the mutation named",
      at(_mut_statuses, "age_ks_test"), _ds.STATUS_REFERENCE_MUTATED)
check("availability still computes -- a reference failure reaches only the "
      "metrics that consume a reference",
      at(_mut_statuses, "ecog_unavailable_rate"), _ds.STATUS_COMPUTED)
check("...and so does the reporting-only family",
      at(_mut_statuses, "retrieval_underfill"), _ds.STATUS_COMPUTED)

# --- 9d. INCOMPARABLE: every patient's input changed -----------------------
def _rehash_comparison(conn, cmp_run):
    conn.execute("UPDATE inferences SET patient_data_hash = 'moved-' || "
                 "patient_id WHERE run_id = ?", (cmp_run,))


_INCOMP_DB, _inc_ref_run, _ = build_two_campaigns(
    "incomparable.db", comparison_extra=_rehash_comparison)
quiet(_dr.designate_reference, _INCOMP_DB, _inc_ref_run)
_incomp, _ = quiet(_drift.run_drift_detection,
                   _drift.COMPARISON_LATEST, db_path=_INCOMP_DB)
_incomp_statuses = statuses_of(_incomp)
check("with no verified pair, a comparison metric REFUSES rather than "
      "computing over whatever overlapped",
      at(_incomp_statuses, "age_ks_test"), _ds.STATUS_REFUSED_INCOMPARABLE)
check("...and the refusal carries the pairing census",
      all(token in str(at(at(_incomp, "data_drift"), "age_ks_test")["notes"])
          for token in ("verified pairs=0", "incomparable=30")), True)
check("availability is unaffected",
      at(_incomp_statuses, "ecog_unavailable_rate"), _ds.STATUS_COMPUTED)

# --- 9e. ZERO VARIANCE -----------------------------------------------------
# Every row in both campaigns carries the SAME condition_count, so a PSI over
# it is 0.0 by construction rather than by measurement -- and a stored 0.0 is
# exactly the healthy zero this redesign removes.
check("a saturated column is named as saturated, not reported as 0.0",
      at(_happy_statuses, "condition_count_psi"), _ds.STATUS_ZERO_VARIANCE)
check("...and NO value is stored for it",
      at(at(_happy, "data_drift"), "condition_count_psi")["metric_value"],
      None)
check("...and the note says the arithmetic, not the finding",
      "single bin" in str(at(at(_happy, "data_drift"),
                             "condition_count_psi")["notes"]), True)
check("a zero-variance reference makes a z-score undefined, and it is NAMED "
      "rather than reported as no movement",
      at(_happy_statuses, "candidates_filtered_z_score"),
      _ds.STATUS_ZERO_VARIANCE)

# --- 9f. NEVER-OBSERVED COLUMNS -------------------------------------------
def _blank_trials_lost(conn, cmp_run):
    conn.execute("UPDATE inferences SET retrieval_trials_lost = NULL")


_NEVER_DB, _never_ref, _ = build_two_campaigns(
    "never_observed.db", comparison_extra=_blank_trials_lost)
quiet(_dr.designate_reference, _NEVER_DB, _never_ref)
_never, _ = quiet(_drift.run_drift_detection,
                  _drift.COMPARISON_LATEST, db_path=_NEVER_DB)
check("a column present and never written reports no_data, which is a "
      "different finding from the column not being there",
      at(statuses_of(_never), "trials_lost_indicator"), _ds.STATUS_NO_DATA)

# --- 9g. A DATABASE WITHOUT RUN IDENTITY -----------------------------------
# THE `drift_metrics` HALF IS BUILT BY THE PROJECT'S OWN WRITER AND THEN
# NARROWED, NOT RETYPED. A hand-written CREATE TABLE here was a second copy of
# that schema, and it went stale the first time a column was added to the real
# one: the refusals this scenario exists to see were computed, the INSERT hit a
# column the fixture did not have, and the check read 0 rows -- a fixture
# defect wearing a writer defect's clothes. What this scenario is ABOUT is the
# absence of run identity, so that is the only thing narrowed by hand.
_NOID_DB = make_db("no_identity.db")
_conn = sqlite3.connect(_NOID_DB)
_conn.execute("DROP TABLE runs")
_conn.execute("DROP TABLE inferences")
_conn.execute("CREATE TABLE inferences (id INTEGER PRIMARY KEY, "
              "patient_id TEXT, timestamp TEXT, ecog_selection TEXT, "
              "ecog_value INTEGER)")
_conn.commit(); _conn.close()
check("the narrowed fixture really has no run identity, so the refusal it "
      "drives is about the database rather than about the fixture",
      _dr.has_run_identity(sqlite3.connect(_NOID_DB))[0], False)
check("...and its `drift_metrics` is at the CURRENT era, so a write that "
      "fails there is the writer's fault and not the fixture's",
      set(_dl.DRIFT_METRIC_COLUMN_ADDITIONS) <= {
          r["name"] for r in rows_of(
              _NOID_DB, "SELECT name FROM pragma_table_info('drift_metrics')")},
      True)

_noid, _ = quiet(_drift.run_drift_detection,
                 _drift.COMPARISON_LATEST, db_path=_NOID_DB)
_noid_statuses = statuses_of(_noid)
check("an identity-less database is refused BY NAME on every metric",
      sorted(set(_noid_statuses.values())), [_ds.STATUS_NO_RUN_IDENTITY])
check("...over every metric, so the count is not one refusal standing in for "
      "the rest", len(_noid_statuses), 17)
check("...and the refusal says WHICH half is missing, because 'no runs table' "
      "and 'no run_id column' are different migrations",
      all(token in str(at(at(_noid, "data_availability"),
                          "ecog_unavailable_rate")["notes"])
          for token in ("table runs", "inferences.run_id")), True)
_noid_rows = rows_of(_NOID_DB, "SELECT status FROM drift_metrics")
check("...and the refusal is WRITTEN, so it is durable rather than a line on "
      "somebody's terminal",
      (len(_noid_rows), sorted({r["status"] for r in _noid_rows})),
      (17, [_ds.STATUS_NO_RUN_IDENTITY]))

# --- 9h. AVAILABILITY BEFORE THE REFERENCE, WITH A REFERENCE FAILURE -------
# The ordering is a correctness property and this is what measures it: the
# resolver is made to RAISE -- which it is documented never to do -- and the
# availability reading must be ON DISK anyway.
_ORDER_DB, _order_ref, _ = build_two_campaigns("ordering.db")
quiet(_dr.designate_reference, _ORDER_DB, _order_ref)


def _explode(db_path):
    raise RuntimeError("the reference machinery blew up")


_real_resolve = _dr.resolve_reference
_dr.resolve_reference = _explode
try:
    _boom, _ = quiet(_drift.run_drift_detection,
                     _drift.COMPARISON_LATEST, db_path=_ORDER_DB)
finally:
    _dr.resolve_reference = _real_resolve
check("the restore put the real resolver back, BY IDENTITY",
      _dr.resolve_reference is _real_resolve, True)
check("a raise out of the reference machinery does take the run down...",
      isinstance(_boom, str) and "RAISED RuntimeError" in _boom, True)
_order_rows = rows_of(_ORDER_DB, "SELECT metric_category, metric_name, status "
                                 "FROM drift_metrics")
check("...and the availability reading is ON DISK anyway, because it was "
      "computed and logged in its own transaction BEFORE the reference was "
      "resolved",
      [(r["metric_category"], r["metric_name"], r["status"])
       for r in _order_rows],
      [("data_availability", "ecog_unavailable_rate", _ds.STATUS_COMPUTED)])

# --- 9i. FRESH BOOTSTRAP ON A TEMPORARY ABSENT PATH ------------------------
# THE PATH IS TEMPORARY AND IS ASSERTED ABSENT FIRST. Never production: the
# whole point of the check is that `initialize_database` CREATES the file, and
# doing that against the real database is what `_readonly_connection` exists to
# prevent one layer up.
_FRESH_PATH = os.path.join(_TMP, "absent", "fresh.db")
os.makedirs(os.path.dirname(_FRESH_PATH), exist_ok=True)
check("the bootstrap path does not exist yet", os.path.exists(_FRESH_PATH),
      False)
quiet(_dl.initialize_database, _FRESH_PATH)
_fresh_tables = sorted(r["name"] for r in rows_of(
    _FRESH_PATH, "SELECT name FROM sqlite_master WHERE type='table' "
                 "AND name NOT LIKE 'sqlite_%'"))
check("a fresh database bootstraps at the CURRENT schema with the designation "
      "store in it", "drift_reference" in _fresh_tables, True)
check("...at the era this build writes",
      first(rows_of(_FRESH_PATH, "PRAGMA user_version"), {}).get("user_version"),
      _dl.SCHEMA_USER_VERSION)
check("...with the four state columns on drift_metrics",
      sorted(set(_dl.DRIFT_METRIC_COLUMN_ADDITIONS)
             - {r["name"] for r in rows_of(
                 _FRESH_PATH,
                 "SELECT name FROM pragma_table_info('drift_metrics')")}), [])
_fresh_run, _ = quiet(_drift.run_drift_detection,
                      _drift.COMPARISON_LATEST, db_path=_FRESH_PATH)
check("and drift detection on it REFUSES by name rather than raising",
      sorted(set(statuses_of(_fresh_run).values())), [_ds.STATUS_NO_DATA])
check("...naming the empty runs table",
      "runs table holds no rows" in str(at(at(_fresh_run, "summary"),
                                           "selection")), True)

# --- 9j. THE MIGRATION PATH: a pre-era-16 database gains the columns -------
_PRE16 = os.path.join(_TMP, "pre16.db")
_conn = sqlite3.connect(_PRE16)
_conn.execute(
    "CREATE TABLE drift_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "timestamp TEXT NOT NULL, metric_category TEXT NOT NULL, "
    "metric_name TEXT NOT NULL, metric_value REAL, baseline_mean REAL, "
    "baseline_std REAL, p_value REAL, z_score REAL, threshold REAL, "
    "alert INTEGER, baseline_window_days INTEGER, "
    "comparison_window_days INTEGER, notes TEXT)")
_conn.execute("INSERT INTO drift_metrics (timestamp, metric_category, "
              "metric_name, alert) VALUES ('2026-01-01','data_drift',"
              "'age_ks_test', 0)")
_conn.commit(); _conn.close()
check("the pre-era-16 fixture really lacks the four columns",
      sorted(set(_dl.DRIFT_METRIC_COLUMN_ADDITIONS)
             & {r["name"] for r in rows_of(
                 _PRE16, "SELECT name FROM pragma_table_info('drift_metrics')")}),
      [])
quiet(_dl.initialize_database, _PRE16)
check("initialize_database migrates them in",
      sorted(set(_dl.DRIFT_METRIC_COLUMN_ADDITIONS)
             - {r["name"] for r in rows_of(
                 _PRE16, "SELECT name FROM pragma_table_info('drift_metrics')")}),
      [])
_legacy = first(rows_of(_PRE16, "SELECT * FROM drift_metrics"), {})
check("...and NOTHING IS BACKFILLED: the legacy row keeps its alert of 0 and "
      "gains NULL axes, because there is no evidence in it from which to "
      "recover which of the four readings it was",
      (_legacy.get("alert"), _legacy.get("status"),
       _legacy.get("alert_policy")), (0, None, None))


# ===========================================================================
# 10. THE LOGGED ROWS -- not the return values -- CARRY BOTH AXES
# ===========================================================================

print("\n" + "=" * 70)
print("10. The SURFACE: what is on disk, not what was returned")
print("=" * 70)

_logged = rows_of(_HAPPY_DB, "SELECT * FROM drift_metrics ORDER BY id")
check("every metric of the run reached the table", len(_logged), 21)
check("every row carries a status from the closed vocabulary",
      sorted({r["status"] for r in _logged} - set(_ds.METRIC_STATUSES)), [])
check("every row carries a policy from the closed vocabulary",
      sorted({r["alert_policy"] for r in _logged} - set(_ds.ALERT_POLICIES)),
      [])
check("every category written is a member of the closed vocabulary",
      sorted({r["metric_category"] for r in _logged}
             - set(_drift.METRIC_CATEGORIES)), [])
check("...and all four categories are present, so the availability category "
      "is written as well as declared",
      sorted({r["metric_category"] for r in _logged}),
      sorted(_drift.METRIC_CATEGORIES))

# THE SHAPE IS TOTAL. Every result of every family carries every key in
# METRIC_KEYS, whatever branch produced it -- so a consumer never has to know
# which branch it is reading to know which keys it has. That was NOT true
# before: a threshold alert carried no `baseline_mean`, and the ECOG metric
# carried `numerator` / `denominator` / `rows_pre_migration` that no other
# metric had, which is how `oncotriage/dashboard/tabs/performance.py` came to
# read three keys only one metric could supply.
_all_results = [data for cat in _drift.METRIC_CATEGORIES
                for data in at(_happy, cat).values()]
check("every result carries every key in METRIC_KEYS",
      sorted({k for r in _all_results
              for k in set(_drift.METRIC_KEYS) - set(r)}), [])
check("...over a non-degenerate set of results", len(_all_results), 21)
check("`excluded` and `counts` are always dicts, never None -- an empty dict "
      "is a measurement and None is a metric that did not look",
      sorted({type(r[k]).__name__ for r in _all_results
              for k in ("excluded", "counts")}), ["dict"])

_stored_alerts = {(r["status"], r["alert_policy"], r["alert"])
                  for r in _logged}
check("NO ROW STORES AN ALERT ITS AXES DO NOT SUPPORT, which is the whole "
      "defect: the shipped writer wrote 0 from every early return",
      sorted({(s, p, a) for s, p, a in _stored_alerts
              if a is not None and not _ds.alert_is_meaningful(s, p)}), [])
check("...and the rows that COULD carry one do",
      sorted({a for s, p, a in _stored_alerts
              if _ds.alert_is_meaningful(s, p)}), [0])
check("...over a set that is not degenerate -- several distinct (status, "
      "policy) combinations were stored",
      len({(s, p) for s, p, _a in _stored_alerts}) >= 3, True)

_availability_row = first([r for r in _logged
                           if r["metric_category"] == "data_availability"], {})
check("the availability row consults no reference and stores NULL for one, "
      "which is that fact rather than a failure to resolve one",
      _availability_row.get("reference_id"), None)
_comparison_rows = [r for r in _logged
                    if r["alert_policy"] == _ds.POLICY_REPORTING_ONLY
                    and r["metric_category"] != "data_availability"]
check("every reference-consuming row names the designation it was taken "
      "against", sorted({r["reference_id"] for r in _comparison_rows}),
      [at(_designation, "reference_id")])

check("the two window columns are NULL on every row: selection is no longer "
      "by window, and a number there would describe a mechanism that did not "
      "run",
      sorted({(r["baseline_window_days"], r["comparison_window_days"])
              for r in _logged}), [(None, None)])

_strata_rows = [r for r in _logged if r["stratum"] is not None]
check("the stratified readings are written one row per stratum",
      len(_strata_rows) >= 4, True)
check("...each naming a member of the project's cancer-group vocabulary",
      sorted({r["stratum"] for r in _strata_rows} - set(_pc.CANCER_GROUPS)),
      [])
check("...and the overall reading carries a NULL stratum, which is the "
      "schema's own word for 'over the whole population'",
      first([r["stratum"] for r in _logged
             if r["metric_name"] == "retrieval_underfill"], "<MISSING>"), None)

# THE EXCLUSION CENSUS TRAVELS WITH THE VALUE. A mean over rows that were
# silently dropped is a mean over a population the writer chose and never named.
_rerank_row = first([r for r in _logged
                     if r["metric_name"] == "rerank_underfill"], {})
check("a fraction's notes carry the exclusion census when there is one, or no "
      "census when there is not -- either way the reader is not left to guess",
      ("excluded rows:" in str(_rerank_row.get("notes"))
       or _rerank_row.get("notes") is not None), True)

# THE WRITER IS THE GATE, NOT ONLY THE CONSTRUCTOR. A result assembled by any
# other means still cannot store a verdict its axes do not support.
_FORGED_DB = make_db("forged.db")
_forged = {"data_availability": {"forged": {
    "metric_value": 0.0, "status": _ds.STATUS_INSUFFICIENT_DATA,
    "policy": _ds.POLICY_BASELINE_INDEPENDENT, "threshold": 0.2,
    "alert": 1, "notes": None, "p_value": None, "baseline_mean": None,
    "baseline_std": None, "stratum": None, "sample_size": None,
    "reference_sample_size": None, "excluded": {}}}}
quiet(_drift.log_drift_metrics, _forged, db_path=_FORGED_DB)
check("a hand-built result claiming an alert it has not earned stores NULL",
      first([r["alert"] for r in rows_of(_FORGED_DB,
                                         "SELECT alert FROM drift_metrics")],
            "<NONE WRITTEN>"), None)

_BAD_DB = make_db("bad_status.db")
_bad = {"data_availability": {"bad": dict(_forged["data_availability"]["forged"],
                                          status="insufficient")}}
check("...and a status outside the vocabulary RAISES at the writer rather "
      "than reaching the dashboard",
      "RAISED UnknownDriftStateError" in str(drive(
          _drift.log_drift_metrics, _bad, db_path=_BAD_DB)), True)
check("...having written nothing",
      first(rows_of(_BAD_DB, "SELECT COUNT(*) AS n FROM drift_metrics"),
            {}).get("n"), 0)

# THE PATH IT WROTE TO IS RETURNED, so a caller can assert rather than assume.
check("log_drift_metrics reports the database it wrote to",
      quiet(_drift.log_drift_metrics, _forged, db_path=_FORGED_DB)[0],
      _FORGED_DB)
check("...and an empty bundle is refused rather than silently writing nothing",
      "RAISED ValueError" in str(drive(_drift.log_drift_metrics, {},
                                       db_path=_FORGED_DB)), True)

# NOTHING REACHED THE CONFIGURED DEFAULT. `resolve_drift_db_path(None)` RESOLVES
# and opens nothing, which is what that function's contract allows -- so asking
# is safe, and the answer must be the seeded temp path rather than production.
check("the configured default is the SEEDED path, so nothing in this file "
      "could have reached the production database even by omitting db_path",
      _drift.resolve_drift_db_path(None),
      os.path.join(_TMP, "seeded-not-production.db"))
check("...and no such file was ever created, so no code path omitted it",
      os.path.exists(os.path.join(_TMP, "seeded-not-production.db")), False)


# ===========================================================================
# 11. THE RENDERED DASHBOARD OUTPUT -- not the return values
# ===========================================================================

print("\n" + "=" * 70)
print("11. The SURFACE: what a reader sees on the tab")
print("=" * 70)

# streamlit is imported HERE rather than at module scope, so a machine without
# it reports eleven skipped checks instead of failing at the first import.
try:
    import streamlit as _st
    from streamlit.testing.v1 import AppTest
    _HAVE_STREAMLIT = True
except Exception as _exc:                                # noqa: BLE001
    _HAVE_STREAMLIT = False
    print(f"  SKIP  streamlit is not importable ({type(_exc).__name__}); the "
          f"eleven rendering checks below did not run")

_DRIVER = """
import pickle, sys
sys.path.insert(0, {root!r})
from oncotriage.dashboard.tabs.drift import render_drift_detection_tab
with open({frame!r}, "rb") as fh:
    df = pickle.load(fh)
render_drift_detection_tab(df)
"""


def render_tab(db_path):
    """Render the real tab against one scratch database. Returns a capture."""
    frame = pd.DataFrame({"timestamp": pd.to_datetime(["2026-06-01T00:00:00Z"]),
                          "patient_id": ["P000"]})
    frame_path = os.path.join(_TMP, "frame.pkl")
    with open(frame_path, "wb") as fh:
        import pickle
        pickle.dump(frame, fh)
    _paths._RESOLVED["inferences_path"] = db_path
    _st.cache_data.clear()
    try:
        app = AppTest.from_string(
            _DRIVER.format(root=_ROOT, frame=frame_path), default_timeout=120)
        app.run()
        return {
            "exception": [e.value for e in app.exception],
            "metrics": [(m.label, m.value, m.delta) for m in app.metric],
            "caption": [c.value for c in app.caption],
            "markdown": [m.value for m in app.markdown],
            "dataframes": [d.value for d in app.dataframe],
        }
    finally:
        _paths._RESOLVED["inferences_path"] = os.path.join(
            _TMP, "seeded-not-production.db")


from oncotriage.dashboard.tabs import drift as _dtab_early   # noqa: E402
_drift_tab_labels = _dtab_early.SELECTION_LABELS

if _HAVE_STREAMLIT:
    _render = drive(render_tab, _HAPPY_DB)
    check("the tab renders without an exception",
          at(_render, "exception"), [])

    _labels = [label for label, _v, _d in at(_render, "metrics")]
    # THE FOURTH TILE. `data_availability` was written by the shipped code and
    # rendered by NOTHING: the tab had three tiles and a three-member filter,
    # so the only ALERTING metric in the project was stored on every run and
    # never shown.
    for _expected in ("Data Availability", "Data Drift", "Retrieval Drift",
                      "Performance Drift"):
        check(f"a tile exists for {_expected}",
              any(_expected in label for label in _labels), True)

    _table = first(at(_render, "dataframes"), pd.DataFrame())
    check("the table renders BOTH axes beside the value",
          all(col in getattr(_table, "columns", [])
              for col in ("Status", "Computation", "Alert policy")), True)

    _states = sorted(set(_table["Status"])) if len(_table) else []
    check("a reporting-only value renders as a VALUE and NOT as an OK",
          any(_ds.DISPLAY_REPORTING in s for s in _states), True)
    check("...and NOT ONE row on this happy-path run renders as an OK it has "
          "not earned: the only OK-capable family is availability",
          sum(1 for s in _states if _ds.DISPLAY_OK in s
              and _ds.DISPLAY_NOT_COMPUTED not in s), 1)
    check("a not-computed reading renders as NOT COMPUTED rather than OK",
          any(_ds.DISPLAY_NOT_COMPUTED in s for s in _states), True)

    _captions = " ".join(at(_render, "caption"))
    check("the caption names the reference the reading was taken against",
          f"reference #{at(_designation, 'reference_id')}" in _captions, True)
    check("...AND how the comparison campaign was chosen, which is the half "
          "the column recorded and the page did not show",
          _drift_tab_labels[_drift.COMPARISON_SELECTION_LATEST] in _captions,
          True)
    check("...with its label, its campaign runs and its digest",
          all(token in _captions
              for token in ("jan-baseline", "campaign runs", "digest")), True)

    # THE REFUSAL SURFACE. A mutated reference must be visibly a REFUSAL on the
    # page, not a blank cell and not a tick.
    _render_mut = drive(render_tab, _MUT_DB)
    _table_mut = first(at(_render_mut, "dataframes"), pd.DataFrame())
    _states_mut = sorted(set(_table_mut["Status"])) if len(_table_mut) else []
    check("a mutated reference renders as REFUSED on the page",
          any(_ds.DISPLAY_REFUSED in s for s in _states_mut), True)
    check("...and the computation column names WHICH refusal",
          _ds.STATUS_REFERENCE_MUTATED in set(_table_mut["Computation"])
          if len(_table_mut) else False, True)

    # A PRE-ERA-16 ROW IS NOT GUESSED AT. It carries alert=0 whether or not
    # anything was measured, so rendering it OK would put the shipped defect
    # back on the page through the database instead of through the code.
    _render_legacy = drive(render_tab, _PRE16)
    _table_legacy = first(at(_render_legacy, "dataframes"), pd.DataFrame())
    check("a legacy row with alert=0 renders NOT COMPUTED, never OK",
          sorted(set(_table_legacy["Status"])) if len(_table_legacy) else [],
          [f"{_ds.DISPLAY_MARKERS[_ds.DISPLAY_NOT_COMPUTED]} "
           f"{_ds.DISPLAY_NOT_COMPUTED}"])
    check("...and says so rather than leaving the cell blank",
          "pre-era-16" in " ".join(str(v) for v
                                   in _table_legacy["Computation"])
          if len(_table_legacy) else False, True)

# THE TAB'S CATEGORY LIST IS PINNED AGAINST THE ENGINE'S, WITHOUT AN IMPORT.
# The tab writes the four out rather than importing `oncotriage.monitoring.drift`
# -- a dashboard tab must not put scipy, numpy and the whole drift engine into
# a Streamlit rerun's import graph -- so the two are checked against each other
# here instead.
from oncotriage.dashboard.tabs import drift as _drift_tab   # noqa: E402
check("the tab renders exactly the engine's categories, in its own order",
      [key for key, _label in _drift_tab.CATEGORY_LABELS],
      list(_drift.METRIC_CATEGORIES))
check("...and every one has help text, so a reader is told what a category "
      "means rather than inferring it from the name",
      sorted(set(k for k, _l in _drift_tab.CATEGORY_LABELS)
             - set(_drift_tab.CATEGORY_HELP)), [])
check("the tab imports the state vocabulary rather than restating it",
      _drift_tab.display_state is _ds.display_state, True)

# THE SELECTION VOCABULARY IS THE SECOND THE TAB WRITES OUT, for the reason the
# category list is written out: a dashboard tab may not put the drift engine
# into a Streamlit rerun's import graph. So the two are pinned against each
# other HERE, where importing both is free.
check("the tab labels exactly the engine's selection vocabulary",
      sorted(_drift_tab.SELECTION_LABELS),
      sorted(_drift.COMPARISON_SELECTIONS))
check("...and the labels are distinct, so two selections cannot read alike",
      len(set(_drift_tab.SELECTION_LABELS.values())),
      len(_drift.COMPARISON_SELECTIONS))
check("a value the map does not know is rendered VERBATIM rather than guessed "
      "at -- every reading here came out of a database, so an unknown value is "
      "a finding",
      "'who_knows'" in _drift_tab._selection_clause(
          pd.DataFrame({"comparison_selection": ["who_knows"]})), True)
check("a run that predates the column gains NO clause rather than a false one",
      (_drift_tab._selection_clause(pd.DataFrame({"x": [1]})),
       _drift_tab._selection_clause(
           pd.DataFrame({"comparison_selection": [None]}))), ("", ""))
check("...and a real selection DOES produce one, so the two readings above "
      "are not one empty string agreeing with itself",
      _drift_tab.SELECTION_LABELS[_drift.COMPARISON_SELECTION_LATEST]
      in _drift_tab._selection_clause(pd.DataFrame(
          {"comparison_selection": [_drift.COMPARISON_SELECTION_LATEST]})),
      True)
check("two selections on one reading are REPORTED rather than collapsed to "
      "the first: one run writes one selection, so two is a reading about two "
      "runs sharing a timestamp",
      all(_drift_tab.SELECTION_LABELS[s] in _drift_tab._selection_clause(
          pd.DataFrame({"comparison_selection":
                        list(_drift.COMPARISON_SELECTIONS)}))
          for s in _drift.COMPARISON_SELECTIONS), True)


# ===========================================================================
# 11b. THE ALERT SURFACE: a missing verdict, end to end
# ===========================================================================
#
# THE RENDERER GUARD ALONE IS INSUFFICIENT AND THAT IS WHY THIS SECTION EXISTS.
# `display_state` refuses to draw an OK for a NULL or a NaN, which is correct
# and is a LAST line of defence: it can only ever see what the database holds.
# The shipped `resolve_alert` CONVERTED before the database did -- `None` into
# 0 and `nan` into 1 -- so by the time the renderer was asked, the value it
# received was a well-formed verdict and there was nothing left to guard
# against. What is measured here is the whole path: the constructor, the row
# on disk, the read-back through pandas, and the rendered cell.

print("\n" + "=" * 70)
print("11b. The SURFACE: a missing verdict stays missing, end to end")
print("=" * 70)

_ALERT_DB = make_db("alert_surface.db")

# ONE RESULT PER INPUT SHAPE, all under axes that MAKE A VERDICT MEANINGFUL --
# which is the only arrangement in which a conversion could reach the column.
_surface = {
    _drift.CATEGORY_AVAILABILITY: {
        "real_zero": _drift.metric(_ds.STATUS_COMPUTED,
                                   _ds.POLICY_BASELINE_INDEPENDENT,
                                   value=0.1, alert=0, excluded={}, counts={}),
        "real_one": _drift.metric(_ds.STATUS_COMPUTED,
                                  _ds.POLICY_BASELINE_INDEPENDENT,
                                  value=0.9, alert=1, excluded={}, counts={}),
        "missing_none": _drift.metric(_ds.STATUS_COMPUTED,
                                      _ds.POLICY_BASELINE_INDEPENDENT,
                                      value=0.5, alert=None, excluded={},
                                      counts={}),
        "missing_nan": _drift.metric(_ds.STATUS_COMPUTED,
                                     _ds.POLICY_BASELINE_INDEPENDENT,
                                     value=0.5, alert=float("nan"),
                                     excluded={}, counts={}),
        "invalid": _drift.metric(_ds.STATUS_COMPUTED,
                                 _ds.POLICY_BASELINE_INDEPENDENT,
                                 value=0.5, alert=2, excluded={}, counts={}),
    },
}

# ---- THE CONSTRUCTOR -------------------------------------------------------
check("a real verdict survives the constructor, both spellings",
      [_surface[_drift.CATEGORY_AVAILABILITY][k]["alert"]
       for k in ("real_zero", "real_one")], [0, 1])
check("a MISSING verdict is None after the constructor, in every spelling -- "
      "never the 0 that `int(bool(None))` produced and the tab draws green",
      sorted({_surface[_drift.CATEGORY_AVAILABILITY][k]["alert"]
              for k in ("missing_none", "missing_nan")}, key=repr), [None])
check("...and the reading is still COMPUTED, because an absent verdict is not "
      "a malformed one: the metric ran and declined to assert",
      sorted({_surface[_drift.CATEGORY_AVAILABILITY][k]["status"]
              for k in ("missing_none", "missing_nan")}),
      [_ds.STATUS_COMPUTED])
check("an INVALID verdict REJECTS the reading rather than being converted -- "
      "`int(bool(2))` is 1, which the tab draws as an ALERT nobody computed",
      (_surface[_drift.CATEGORY_AVAILABILITY]["invalid"]["status"],
       _surface[_drift.CATEGORY_AVAILABILITY]["invalid"]["alert"],
       _surface[_drift.CATEGORY_AVAILABILITY]["invalid"]["metric_value"]),
      (_ds.STATUS_MALFORMED_INPUTS, None, None))
check("...and the rejection says so in the notes, so an operator is told "
      "which half of the reading was unreadable",
      "alert" in str(_surface[_drift.CATEGORY_AVAILABILITY]["invalid"]
                     ["notes"]).lower(), True)

# ---- THE ROW ON DISK -------------------------------------------------------
quiet(_drift.log_drift_metrics, _surface, db_path=_ALERT_DB,
      selection=_drift.COMPARISON_SELECTION_LATEST)
_stored = {r["metric_name"]: r for r in rows_of(
    _ALERT_DB, "SELECT metric_name, alert, status FROM drift_metrics")}
check("every reading reached the table, so the readings below are not an "
      "empty set agreeing with itself", sorted(_stored),
      ["invalid", "missing_nan", "missing_none", "real_one", "real_zero"])
check("a MISSING verdict is NULL IN THE COLUMN -- SQL, not Python",
      first(rows_of(_ALERT_DB,
                    "SELECT COUNT(*) AS n FROM drift_metrics "
                    "WHERE alert IS NULL"), {}).get("n"), 3)
check("...and a real 0 is a real 0 in the same column, so NULL and 0 are "
      "distinguishable in SQL rather than collapsed",
      first(rows_of(_ALERT_DB,
                    "SELECT COUNT(*) AS n FROM drift_metrics "
                    "WHERE alert = 0"), {}).get("n"), 1)
check("the stored verdicts, by name",
      {k: v["alert"] for k, v in sorted(_stored.items())},
      {"invalid": None, "missing_nan": None, "missing_none": None,
       "real_one": 1, "real_zero": 0})

# ---- THE READ-BACK ---------------------------------------------------------
# THROUGH PANDAS, WHICH IS WHAT THE TAB ACTUALLY DOES. An INTEGER column
# holding NULLs reads back float64, so a NULL arrives as `nan` -- a different
# object from the `None` that was written, and the one the renderer guard was
# added for after being found by driving rather than by reading.
_readback = pd.read_sql_query(
    "SELECT metric_name, alert FROM drift_metrics",
    sqlite3.connect(_ALERT_DB)).set_index("metric_name")["alert"]
check("a NULL reads back as a pandas absence, not as a zero",
      sorted({bool(pd.isna(_readback[k]))
              for k in ("missing_none", "missing_nan", "invalid")}), [True])
check("...and a real verdict reads back as its own value",
      (float(_readback["real_zero"]), float(_readback["real_one"])),
      (0.0, 1.0))

# ---- THE RENDERED CELL -----------------------------------------------------
if _HAVE_STREAMLIT:
    _render_alert = drive(render_tab, _ALERT_DB)
    check("the tab renders the alert surface without an exception",
          at(_render_alert, "exception"), [])
    _t = first(at(_render_alert, "dataframes"), pd.DataFrame())
    _by_name = ({str(r["metric_name"]): str(r["Status"])
                 for _i, r in _t.iterrows()}
                if len(_t) and "metric_name" in _t.columns else {})
    check("every reading reached the page, so the cells below are not an "
          "empty table agreeing with itself", sorted(_by_name),
          ["invalid", "missing_nan", "missing_none", "real_one", "real_zero"])
    check("a MISSING verdict renders NOT COMPUTED, never the green OK that "
          "`int(bool(None))` would have put there",
          sorted({_by_name.get(k) for k in ("missing_none", "missing_nan")}),
          [f"{_ds.DISPLAY_MARKERS[_ds.DISPLAY_NOT_COMPUTED]} "
           f"{_ds.DISPLAY_NOT_COMPUTED}"])
    check("an INVALID verdict renders NOT COMPUTED too, never the ALERT that "
          "`int(bool(nan))` would have put there",
          _by_name.get("invalid"),
          f"{_ds.DISPLAY_MARKERS[_ds.DISPLAY_NOT_COMPUTED]} "
          f"{_ds.DISPLAY_NOT_COMPUTED}")
    check("...and the rejected reading names its status on the page, so the "
          "cell is a finding rather than a blank",
          _ds.STATUS_MALFORMED_INPUTS in set(_t["Computation"])
          if len(_t) else False, True)
    check("a real 0 still renders OK and a real 1 still renders ALERT, so the "
          "guard did not swallow the verdicts it exists to preserve",
          (_by_name.get("real_zero"), _by_name.get("real_one")),
          (f"{_ds.DISPLAY_MARKERS[_ds.DISPLAY_OK]} {_ds.DISPLAY_OK}",
           f"{_ds.DISPLAY_MARKERS[_ds.DISPLAY_ALERT]} {_ds.DISPLAY_ALERT}"))


# ===========================================================================
# 11c. THE COMPARISON CAMPAIGN IS CHOSEN, NOT INFERRED
# ===========================================================================
#
# WHICH CAMPAIGN A DRIFT RUN MEASURES IS A DECISION. The shipped code took
# MAX(runs.id) -- so the population every number was computed over was decided
# by insertion order, which is the exact defect the reference designation
# removed from the OTHER side of the comparison. Half a designed comparison is
# not a designed comparison.

print("\n" + "=" * 70)
print("11c. The comparison campaign is an argument, never a default")
print("=" * 70)

check("the selection vocabulary is closed and its members are distinct",
      (len(_drift.COMPARISON_SELECTIONS),
       len(set(_drift.COMPARISON_SELECTIONS))), (2, 2))
check("...and `latest` is a member of it, because an opt-in that was not "
      "recorded would be indistinguishable from an explicit choice",
      _drift.COMPARISON_SELECTION_LATEST in _drift.COMPARISON_SELECTIONS, True)

check("an explicit run id resolves to that anchor and records it as explicit",
      _drift.resolve_comparison_selection(7),
      (7, _drift.COMPARISON_SELECTION_EXPLICIT))
check("the opt-in resolves to NO anchor -- 'ask the database' -- and records "
      "itself as the opt-in rather than as a choice somebody made",
      _drift.resolve_comparison_selection(_drift.COMPARISON_LATEST),
      (None, _drift.COMPARISON_SELECTION_LATEST))

# THERE IS NO DEFAULT, AND `None` IS NOT ONE. That is the repair: the shipped
# signature had `db_path` first and no comparison argument at all.
for _bad in (None, "", "newest", 1.0, [1], {"run": 1}):
    check(f"comparison={_bad!r} RAISES rather than selecting something",
          "RAISED ComparisonSelectionError" in str(drive(
              _drift.resolve_comparison_selection, _bad)), True)
check("...and True is refused explicitly, because isinstance(True, int) is "
      "True in Python and `comparison=True` would otherwise select run 1 -- a "
      "real campaign, silently, on a value that means nothing",
      "RAISED ComparisonSelectionError" in str(drive(
          _drift.resolve_comparison_selection, True)), True)
check("...and the refusal is a RuntimeError, not a ValueError, so a broad "
      "`except ValueError` cannot eat it",
      issubclass(_drift.ComparisonSelectionError, RuntimeError)
      and not issubclass(_drift.ComparisonSelectionError, ValueError), True)
check("run_drift_detection takes the comparison as its FIRST parameter and "
      "gives it NO default, so a caller cannot omit it",
      [(p.name, p.default is inspect.Parameter.empty) for p in list(
          inspect.signature(_drift.run_drift_detection).parameters.values())
       ][0], ("comparison", True))
check("...and so does main(), so the entry point cannot reinstate one",
      [(p.name, p.default is inspect.Parameter.empty) for p in list(
          inspect.signature(_drift.main).parameters.values())][0],
      ("comparison", True))
check("omitting it is a TypeError rather than a run against a campaign "
      "nobody chose",
      "RAISED TypeError" in str(drive(_drift.run_drift_detection)), True)

# ---- THE SELECTION IS RECORDED ON EVERY ROW --------------------------------
_SEL_DB, _sel_ref, _sel_cmp = build_two_campaigns("selection.db")
quiet(_dr.designate_reference, _SEL_DB, _sel_ref)
quiet(_drift.run_drift_detection, _sel_cmp, db_path=_SEL_DB)
_sel_rows = rows_of(_SEL_DB, "SELECT comparison_selection FROM drift_metrics")
check("an explicit run is recorded as EXPLICIT on every row written, "
      "availability included -- the population is what every metric was "
      "computed over, so how it was chosen is a fact about all of them",
      (len(_sel_rows) > 0,
       sorted({r["comparison_selection"] for r in _sel_rows})),
      (True, [_drift.COMPARISON_SELECTION_EXPLICIT]))

_LAT_DB, _lat_ref, _lat_cmp = build_two_campaigns("selection_latest.db")
quiet(_dr.designate_reference, _LAT_DB, _lat_ref)
quiet(_drift.run_drift_detection, _drift.COMPARISON_LATEST, db_path=_LAT_DB)
_lat_rows = rows_of(_LAT_DB, "SELECT comparison_selection FROM drift_metrics")
check("the opt-in is recorded AS THE OPT-IN, so a reader can tell a campaign "
      "somebody named from one the tool picked",
      sorted({r["comparison_selection"] for r in _lat_rows}),
      [_drift.COMPARISON_SELECTION_LATEST])
check("...and the two are different values on disk, so the distinction "
      "survives the round trip rather than being a Python-side label",
      sorted({r["comparison_selection"] for r in _sel_rows})
      != sorted({r["comparison_selection"] for r in _lat_rows}), True)

# THE OPT-IN AND THE EXPLICIT ID SELECT THE SAME POPULATION HERE, which is what
# makes the two readings above comparable: what differs is the RECORD of how it
# was chosen, not the rows.
check("the opt-in picked the campaign the explicit id names, so the two runs "
      "above differ in their record and not in their population",
      (_drift.select_comparison_population(_LAT_DB,
                                           _drift.COMPARISON_LATEST)[2],
       _drift.select_comparison_population(_LAT_DB, _lat_cmp)[0].run_ids),
      (_drift.COMPARISON_SELECTION_LATEST,
       _drift.select_comparison_population(
           _LAT_DB, _drift.COMPARISON_LATEST)[0].run_ids))
# ---- THE COLUMN IS A CLOSED VOCABULARY AND SOMETHING CLOSES IT -------------
# `status` and `alert_policy` are validated at the write; this one shipped in
# the repair without a guard, which is an asymmetry with no argument behind it.
# THE TRAP IT CLOSES IS A REAL ONE: the opt-in ARGUMENT is "latest" and the
# recorded SELECTION is "latest_opt_in", so a caller handing the write gate the
# argument value instead of the recorded one stored a bucket no consumer knows.
check("None is a member -- 'no drift run wrote this row', which is what every "
      "pre-era-16 row holds and what a direct log_drift_metrics call means",
      _drift.require_selection(None), None)
check("...and both real members pass",
      [_drift.require_selection(s) for s in _drift.COMPARISON_SELECTIONS],
      list(_drift.COMPARISON_SELECTIONS))
check("the opt-in ARGUMENT is refused as a SELECTION, which is the confusion "
      "the two spellings invite",
      "RAISED ComparisonSelectionError" in str(drive(
          _drift.require_selection, _drift.COMPARISON_LATEST)), True)
check("...and so is anything else",
      sorted({"RAISED ComparisonSelectionError" in str(drive(
          _drift.require_selection, v))
          for v in ("", "explicit", 0, 1, True, [], {})}), [True])
check("the write gate refuses it BEFORE opening the database, so a bad "
      "selection costs nothing and writes nothing",
      "RAISED ComparisonSelectionError" in str(drive(
          _drift.log_drift_metrics, _surface, db_path=_ALERT_DB,
          selection="latest")), True)
check("...having written nothing: the row count is what it was",
      first(rows_of(_ALERT_DB,
                    "SELECT COUNT(*) AS n FROM drift_metrics"), {}).get("n"), 5)

check("a run id that names nothing is a refusal rather than a fallback to the "
      "latest -- which is what a default would have made it",
      _drift.select_comparison_population(_LAT_DB, 99_999)[0], None)
check("...and the refusal still records HOW the campaign was being chosen, so "
      "a run that selected nothing still says what it was asked",
      _drift.select_comparison_population(_LAT_DB, 99_999)[2],
      _drift.COMPARISON_SELECTION_EXPLICIT)


# ===========================================================================
# 12. TARGETED FAILURE CONTROLS
# ===========================================================================
#
# ONE CONTROL PER DISTINCT CRITICAL SAFEGUARD. Each removes the safeguard in a
# COPY of the package and requires THIS FILE to notice -- so "the check can
# fail" is measured rather than asserted, which is the rule an assertion in
# this project is held to.
#
# THE COPY IS WHAT IS BROKEN, NEVER THE TREE. `copytree` into the temp
# directory, `PYTHONPATH` pointed at it, a `sitecustomize` that strips the
# editable install's MetaPathFinder (which otherwise BEATS `PYTHONPATH` -- this
# project has been caught by that twice), and a realpath preflight inside the
# child asserting that the copy is what imported. Without the preflight a
# control that silently imported the real package would report every plant as
# MISSED against checks that work.
#
# EVERY PLANT IS `ast.parse`d BEFORE IT RUNS, and its occurrence count is
# asserted. A plant that matched nothing is a named PLANT-FAILED rather than a
# working safeguard reported as broken; a plant that does not parse is a
# recorded failure rather than an abort.

print("\n" + "=" * 70)
print("12. Each safeguard's absence is detected")
print("=" * 70)

_CONTROL_ROOT = os.path.join(_TMP, "controls")
os.makedirs(_CONTROL_ROOT, exist_ok=True)

_SITECUSTOMIZE = '''
import sys
# STRIP THE EDITABLE INSTALL'S FINDER. setuptools installs a MetaPathFinder
# that takes precedence over sys.path entirely, so PYTHONPATH alone does not
# win -- this project has shipped that false green twice.
sys.meta_path = [f for f in sys.meta_path
                 if "__editable__" not in type(f).__module__
                 and "__editable__" not in getattr(f, "__name__", "")]
'''

_PREFLIGHT = '''
import os, sys
import oncotriage
_here = os.path.realpath(os.path.dirname(os.path.dirname(
    os.path.abspath(oncotriage.__file__))))
_want = os.path.realpath({root!r})
if _here != _want:
    print("PREFLIGHT-FAILED", _here, "!=", _want)
    sys.exit(3)
'''


def _make_control(name, edits):
    """A copy of the package with `edits` applied. Returns (root, problems).

    `edits` is a list of (relative path, old, new, expected count).
    """
    root = os.path.join(_CONTROL_ROOT, name)
    if os.path.isdir(root):
        shutil.rmtree(root)
    os.makedirs(root)
    shutil.copytree(os.path.join(_ROOT, "oncotriage"),
                    os.path.join(root, "oncotriage"))
    with open(os.path.join(root, "sitecustomize.py"), "w",
              encoding="utf-8") as fh:
        fh.write(_SITECUSTOMIZE)

    problems = []
    for rel, old, new, expected in edits:
        target = os.path.join(root, rel)
        text = open(target, encoding="utf-8").read()
        found = text.count(old)
        if found != expected:
            problems.append(f"{rel}: plant matched {found} times, "
                            f"expected {expected}")
            continue
        text = text.replace(old, new)
        try:
            ast.parse(text)
        except SyntaxError as exc:
            problems.append(f"{rel}: the plant does not parse: {exc}")
            continue
        open(target, "w", encoding="utf-8").write(text)
    return root, problems


def _run_control(name, root, probe):
    """Run `probe` in a child interpreter against the control copy."""
    script = os.path.join(_CONTROL_ROOT, f"{name}_probe.py")
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(_PREFLIGHT.format(root=root) + probe)
    env = dict(os.environ)
    env["PYTHONPATH"] = root
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        done = subprocess.run([sys.executable, script], env=env, cwd=_TMP,
                              capture_output=True, text=True, timeout=300)
        return done.stdout + done.stderr
    except Exception as exc:                             # noqa: BLE001
        return f"<CONTROL RAISED {type(exc).__name__}: {exc}>"


# The probe body every control shares: build the happy-path database, designate,
# mutate, run, and PRINT what a reader would conclude.
_PROBE = '''
import io, json, os, sqlite3, sys, tempfile
from contextlib import redirect_stderr, redirect_stdout

from oncotriage import paths as P
from oncotriage.monitoring import drift as D
from oncotriage.monitoring import drift_reference as R
from oncotriage.storage import database_logger as L

_TMP = tempfile.mkdtemp()
P._RESOLVED["inferences_path"] = os.path.join(_TMP, "seeded.db")
STAMP = {stamp!r}
TUN = {tunables!r}


def q(fn, *a, **k):
    e, o = io.StringIO(), io.StringIO()
    with redirect_stderr(e), redirect_stdout(o):
        try:
            return fn(*a, **k)
        except BaseException as exc:
            return "<RAISED %s: %s>" % (type(exc).__name__, exc)


def run_row(conn, started, **over):
    v = dict(STAMP); v.update(over)
    cols = ["started_at","finished_at","status","invocation_source","resumed",
            "tunables"] + list(v)
    p = [started, started, "FINISHED", "batch_runner", None, TUN] + [v[k] for k in v]
    return conn.execute("INSERT INTO runs (%s) VALUES (%s)" % (
        ",".join(cols), ",".join("?" for _ in cols)), p).lastrowid


DEF = {defaults!r}


def inf(conn, run, pid, **over):
    v = dict(DEF); v.update(over)
    v["patient_data_hash"] = v["patient_data_hash"] or ("hash-" + pid)
    cols = ["run_id","patient_id"] + list(v)
    p = [run, pid] + [v[k] for k in v]
    return conn.execute("INSERT INTO inferences (%s) VALUES (%s)" % (
        ",".join(cols), ",".join("?" for _ in cols)), p).lastrowid


db = os.path.join(_TMP, "x.db")
q(L.initialize_database, db)
c = sqlite3.connect(db)
ref = run_row(c, "2026-01-01T00:00:00Z")
for i in range(30):
    inf(c, ref, "P%03d" % i, timestamp="2026-01-01T00:%02d:00Z" % i,
        age=50 + i % 10, total_time=60.0 + i)
# A FAILED PATIENT IN THE REFERENCE TOO, and it is what makes the C2 control
# able to discriminate. Without it the reference's error series is thirty zeros,
# so its standard deviation is zero and the error-rate z-score reports
# `not_computed_zero_variance` under BOTH row rules -- a correct reading, and
# one in which the row rule decides nothing. Measured: the first version of this
# probe had no reference failure and the C2 control reported a working row rule
# as broken.
inf(c, ref, "REFFAIL", timestamp="2026-01-01T05:00:00Z", error="boom",
    candidates_evaluated=0, eligible_matches=0)
cmp_run = run_row(c, "2026-06-01T00:00:00Z")
for i in range(30):
    inf(c, cmp_run, "P%03d" % i, timestamp="2026-06-01T00:%02d:00Z" % i,
        age=55 + i % 10, total_time=80.0 + i, candidates_retrieved=70 + i % 5)
# A FAILED PATIENT and a RESAMPLE DUPLICATE of an EARLIER-ERRORED one.
inf(c, cmp_run, "FAILED", error="boom", candidates_evaluated=0,
    candidates_retrieved=0, candidates_reranked=0, eligible_matches=0)
# ...and its CLEAN counterpart in the comparison, so the pair exists under the
# ALL rule and disappears under the Stage-5 one.
inf(c, cmp_run, "REFFAIL", timestamp="2026-06-01T05:00:00Z")
first_err = inf(c, cmp_run, "RETRIED", timestamp="2026-06-01T03:00:00Z",
                error="boom", candidates_evaluated=0)
inf(c, cmp_run, "RETRIED", timestamp="2026-06-01T04:00:00Z")
c.commit(); c.close()

rec = q(R.designate_reference, db, ref, label="jan")
mutated_id = rec["row_ids"][0] if isinstance(rec, dict) else None
res_before = q(R.resolve_reference, db)

# A READING BEFORE THE MUTATION AS WELL AS AFTER, AND IT MUST BE TAKEN HERE.
# After the UPDATE every comparison metric refuses with `reference_mutated`,
# which is a DIFFERENT safeguard firing -- so a control whose subject is a
# metric's ROW RULE would pass for the wrong reason. Measured: anchored one
# statement too low, this reading came back `reference_mutated` and the C2
# control reported a working row rule as broken.
out_before = q(D.run_drift_detection, D.COMPARISON_LATEST, db_path=db,
               log_to_db=False)

c = sqlite3.connect(db)
c.execute("UPDATE inferences SET age = age + 1 WHERE id = ?", (mutated_id,))
c.commit(); c.close()
res_after = q(R.resolve_reference, db)

out = q(D.run_drift_detection, D.COMPARISON_LATEST, db_path=db)
c = sqlite3.connect(db); c.row_factory = sqlite3.Row
rows = [dict(r) for r in c.execute("SELECT * FROM drift_metrics")]
c.close()

# ---- THE ALERT CONVERSION, DRIVEN INSIDE THE CONTROL COPY -----------------
# `metric()` is the only constructor, so asking it what five raw inputs produce
# measures the whole of Fix 1 without needing a metric body that happens to
# produce one of them. A copy whose resolver converts reports a value here.
alert_conversion = {{}}
for _n, _raw in (("none", None), ("nan", float("nan")), ("two", 2),
                 ("zero", 0), ("one", 1)):
    _r = q(D.metric, "computed", "baseline_independent", value=0.5,
           alert=_raw, excluded={{}}, counts={{}})
    alert_conversion[_n] = ([_r.get("status"), _r.get("alert")]
                            if isinstance(_r, dict) else str(_r))

# ---- THE COMPARISON ARGUMENT ---------------------------------------------
# A copy that reinstates a default RUNS here; the shipped one raises TypeError
# before reading a row.
omitted = q(D.run_drift_detection, db_path=db, log_to_db=False)
omitted_comparison = ("raised" if isinstance(omitted, str)
                      and omitted.startswith("<RAISED") else "ran")

# ---- THE PAIR RULE --------------------------------------------------------
# A THIRD CAMPAIGN over the SAME patients with the SAME patient hashes, judged
# by a DIFFERENT MODEL. A rule that compares the hash alone calls every one of
# these pairs verified. Built AFTER the run above, so it cannot change which
# campaign `latest` selected.
c = sqlite3.connect(db)
model_run = run_row(c, "2026-07-01T00:00:00Z")
model_ids = [inf(c, model_run, "P%03d" % i,
                 timestamp="2026-07-01T00:%02d:00Z" % i,
                 matching_model="gpt-5.6-terra") for i in range(5)]
c.commit(); c.close()
c = sqlite3.connect(db)
ref_ids_for_pairs = [r[0] for r in c.execute(
    "SELECT id FROM inferences WHERE run_id = ? AND patient_id IN "
    "('P000','P001','P002','P003','P004') ORDER BY id", (ref,))]
pair_res = q(R.pair_rows, c, ref_ids_for_pairs, model_ids)
c.close()
pair_counts = (pair_res.counts if not isinstance(pair_res, str)
               else {{"RAISED": pair_res}})
pair_named = (sorted(pair_res.incomparable_on) if not isinstance(pair_res, str)
              else [])


def status_of(name, where=None):
    where = out if where is None else where
    for cat in getattr(D, "METRIC_CATEGORIES", ()):
        fam = where.get(cat, {{}}) if isinstance(where, dict) else {{}}
        if name in fam:
            return fam[name]["status"]
    return None


print(json.dumps({{
    "reference_before": getattr(res_before, "outcome", str(res_before)),
    "reference_after": getattr(res_after, "outcome", str(res_after)),
    "availability": status_of("ecog_unavailable_rate"),
    "comparison": status_of("age_ks_test"),
    "underfill": status_of("retrieval_underfill"),
    "underfill_value": (out.get("retrieval_drift", {{}})
                        .get("retrieval_underfill", {{}}).get("metric_value")
                        if isinstance(out, dict) else None),
    "availability_n": (out.get("data_availability", {{}})
                       .get("ecog_unavailable_rate", {{}}).get("sample_size")
                       if isinstance(out, dict) else None),
    "availability_logged_first": (out.get("summary", {{}})
                                  .get("availability_logged_first")
                                  if isinstance(out, dict) else None),
    "error_rate": status_of("error_rate_z_score"),
    "error_rate_before_mutation": status_of("error_rate_z_score", out_before),
    "comparison_before_mutation": status_of("age_ks_test", out_before),
    "rerank_excluded": (out.get("retrieval_drift", {{}})
                        .get("rerank_underfill", {{}}).get("excluded")
                        if isinstance(out, dict) else None),
    "rows": len(rows),
    "alerts_stored": sorted(set((r["status"], r["alert_policy"], r["alert"])
                                for r in rows), key=str),
    "first_errored_row_in_population": None,
    "run_raised": isinstance(out, str),
    "alert_conversion": alert_conversion,
    "omitted_comparison": omitted_comparison,
    "selection_recorded": sorted(set(
        str(r.get("comparison_selection")) for r in rows)),
    "pair_counts": pair_counts,
    "pair_named": pair_named,
}}))
'''.format(stamp=_STAMP, tunables=_TUNABLES, defaults=_INFERENCE_DEFAULTS)


def control_reading(name, edits):
    """Run one control and return the parsed probe output, or a marker."""
    root, problems = _make_control(name, edits)
    if problems:
        return {"PLANT-FAILED": problems}
    raw = _run_control(name, root, _PROBE)
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except Exception:                            # noqa: BLE001
                continue
    return {"NO-JSON": raw[-800:]}


# --- THE CLEAN CONTROL, FIRST -----------------------------------------------
# Without it, every reading below could be a statement about a harness that
# does not work rather than about a safeguard that does.
_clean = control_reading("clean", [])
check("CLEAN CONTROL: the unmodified copy reproduces every safeguard",
      {k: _clean.get(k) for k in ("reference_before", "reference_after",
                                  "availability", "comparison", "underfill",
                                  "run_raised")},
      {"reference_before": _dr.REFERENCE_OK,
       "reference_after": _dr.REFERENCE_MUTATED,
       "availability": _ds.STATUS_COMPUTED,
       "comparison": _ds.STATUS_REFERENCE_MUTATED,
       "underfill": _ds.STATUS_COMPUTED,
       "run_raised": False})

# THE THREE REPAIRED SAFEGUARDS, READ ON THE CLEAN COPY FIRST. Each control
# below is a DIFFERENCE from one of these readings, so a harness that had
# stopped working would fail here rather than reporting every plant as caught.
check("CLEAN CONTROL: a missing verdict is None and a real one survives",
      _clean.get("alert_conversion"),
      {"none": [_ds.STATUS_COMPUTED, None],
       "nan": [_ds.STATUS_COMPUTED, None],
       "two": [_ds.STATUS_MALFORMED_INPUTS, None],
       "zero": [_ds.STATUS_COMPUTED, 0],
       "one": [_ds.STATUS_COMPUTED, 1]})
check("CLEAN CONTROL: omitting the comparison RAISES before a row is read",
      _clean.get("omitted_comparison"), "raised")
check("CLEAN CONTROL: the selection is recorded on every row",
      _clean.get("selection_recorded"),
      [_drift.COMPARISON_SELECTION_LATEST])
check("CLEAN CONTROL: a model change leaves no verified pair, and the rule "
      "NAMES the fact that disagreed",
      (at(_clean.get("pair_counts", {}), "verified_pairs"),
       at(_clean.get("pair_counts", {}), "incomparable_pairs"),
       _clean.get("pair_named")),
      (0, 5, ["matching_model"]))
check("...and no stored row carries an alert its axes do not support",
      [t for t in _clean.get("alerts_stored", [])
       if t[2] is not None and not _ds.alert_is_meaningful(t[0], t[1])], [])

# --- C1: THE DIGEST -----------------------------------------------------
_c1 = control_reading("c1_no_digest", [(
    "oncotriage/monitoring/drift_reference.py",
    """            actual = content_digest(found, columns)
            if actual != row["content_digest"]:""",
    """            actual = content_digest(found, columns)
            if False:""", 1)])
check("C1 the digest comparison removed: a mutated reference is reported OK, "
      "and the comparison metrics are computed against rows nobody designated",
      (_c1.get("reference_after"), _c1.get("comparison")),
      (_dr.REFERENCE_OK, _ds.STATUS_COMPUTED))
check("...so section 9c's assertion is what detects it",
      _c1.get("reference_after") == _dr.REFERENCE_MUTATED, False)

# --- C2: LAYERED ELIGIBILITY -------------------------------------------
# The Stage-5 rule applied to the ERROR-RATE metric. Under it every surviving
# row has a clean error by construction, so the metric that exists to see
# failures is computed over the population with none in it.
_c2 = control_reading("c2_error_rate_rule", [(
    "oncotriage/monitoring/drift.py",
    '''    ("error_rate_z_score", CATEGORY_PERFORMANCE,
     _reference.ROW_RULE_ALL, "z", "__error_rate__"),''',
    '''    ("error_rate_z_score", CATEGORY_PERFORMANCE,
     _reference.ROW_RULE_STAGE5, "z", "__error_rate__"),''', 1)])
# READ BEFORE THE PROBE'S MUTATION. After it every comparison metric refuses
# with `reference_mutated`, which is a different safeguard firing and would
# make this control pass for the wrong reason.
check("C2 CONTROL PRECONDITION: the clean copy's comparison metrics computed "
      "before the mutation, so the reading below is about the row rule",
      _clean.get("comparison_before_mutation"), _ds.STATUS_COMPUTED)
check("C2 the error-rate metric put under the Stage-5 rule: the population it "
      "is computed over no longer contains a single failure, so it reports "
      "zero variance instead of a rate",
      (_clean.get("error_rate_before_mutation"),
       _c2.get("error_rate_before_mutation")),
      (_ds.STATUS_COMPUTED, _ds.STATUS_ZERO_VARIANCE))

# --- C3: DEDUP BEFORE FILTERING ----------------------------------------
# Filter-then-dedup. A patient whose FIRST attempt errored has their SECOND
# promoted to "first", so the underfill population silently loses the failure.
_c3 = control_reading("c3_dedup_after_filter", [(
    "oncotriage/monitoring/drift.py",
    """    row_ids = _reference.eligible_row_ids(conn, comparison.row_ids,
                                          _reference.ROW_RULE_ALL)""",
    """    row_ids = _reference.eligible_row_ids(conn, comparison.row_ids,
                                          _reference.ROW_RULE_STAGE5)""", 1)])
# THE DISCRIMINATING READING IS THE DENOMINATOR, NOT THE STATUS. Both copies
# report `computed`; what changes is WHO WAS IN IT. The probe's comparison
# campaign carries two patients whose Stage 5 failed and one resample, and
# under the Stage-5 rule the failures leave the population that exists to say
# whether the inputs were usable -- silently, because the metric still answers.
#
# The FIRST version of this control asserted that the plant changed nothing
# observable, which is a check that passes by construction and demonstrates no
# safeguard at all. Recorded rather than quietly corrected.
check("C3 CONTROL PRECONDITION: the clean copy computed availability over a "
      "non-degenerate population",
      isinstance(_clean.get("availability_n"), int)
      and _clean.get("availability_n") >= 20, True)
check("C3 availability moved onto the Stage-5 rule: the metric still says "
      "`computed`, and its denominator SHRINKS -- the failed patients it "
      "exists to see are gone from it",
      (_c3.get("availability"),
       _c3.get("availability_n") < _clean.get("availability_n")),
      (_ds.STATUS_COMPUTED, True))
check("...and the rows it lost are exactly the ones that failed Stage 5",
      _clean.get("availability_n") - _c3.get("availability_n"), 2)

# --- C4: THE STATE GATE -------------------------------------------------
# THE PLANT IS THE SHIPPED FUNCTION, VERBATIM: one return value, the axes
# ignored, and `int(bool(...))` doing the reading. That expression is the whole
# defect -- it converts `None` into 0, which the tab draws as a green OK, and
# `nan` into 1, which it draws as an ALERT. The caller is planted with it,
# because the repaired `metric()` unpacks a pair and a one-value function would
# raise there rather than storing a manufactured verdict.
_c4 = control_reading("c4_no_gate", [
    ("oncotriage/monitoring/drift_states.py",
     """    outcome, value = classify_alert(alert)
    if not alert_is_meaningful(status, policy):
        return None, outcome
    if outcome != ALERT_PRESENT:
        return None, outcome
    return value, outcome""",
     """    return int(bool(alert)), ALERT_PRESENT""", 1),
])
_c4_bad = [t for t in _c4.get("alerts_stored", [])
           if t[2] is not None and not _ds.alert_is_meaningful(t[0], t[1])]
check("C4 the alert gate removed: rows that measured NOTHING store a verdict "
      "-- which is the shipped defect, byte for byte",
      len(_c4_bad) > 0, True)
check("...and the clean control stores none, so the reading above is about "
      "the plant", len([t for t in _clean.get("alerts_stored", [])
                        if t[2] is not None
                        and not _ds.alert_is_meaningful(t[0], t[1])]), 0)

# --- C5: THE AVAILABILITY ORDERING --------------------------------------
# The early write removed. Availability is still COMPUTED first; what goes is
# the guarantee that it is on disk when the reference machinery fails.
_c5 = control_reading("c5_no_early_log", [(
    "oncotriage/monitoring/drift.py",
    """        if log_to_db:
            try:
                log_drift_metrics(
                    {CATEGORY_AVAILABILITY: results[CATEGORY_AVAILABILITY]},
                    db_path=db_path, reference_id=None,
                    timestamp=run_timestamp, selection=selection)
                summary["availability_logged_first"] = True""",
    """        if False:
            try:
                log_drift_metrics(
                    {CATEGORY_AVAILABILITY: results[CATEGORY_AVAILABILITY]},
                    db_path=db_path, reference_id=None,
                    timestamp=run_timestamp, selection=selection)
                summary["availability_logged_first"] = True""", 1)])
check("C5 the early availability write removed: the run still completes and "
      "the reading is still `computed`, which is why a status check alone "
      "cannot see this",
      _c5.get("availability"), _ds.STATUS_COMPUTED)
check("...what DOES change is the run's own record of whether it wrote early",
      (_clean.get("availability_logged_first"),
       _c5.get("availability_logged_first")), (True, False))

_ORDER_CONTROL_ROOT, _order_problems = _make_control("c5b_order", [(
    "oncotriage/monitoring/drift.py",
    """        if log_to_db:
            try:
                log_drift_metrics(
                    {CATEGORY_AVAILABILITY: results[CATEGORY_AVAILABILITY]},
                    db_path=db_path, reference_id=None,
                    timestamp=run_timestamp, selection=selection)
                summary["availability_logged_first"] = True""",
    """        if False:
            try:
                log_drift_metrics(
                    {CATEGORY_AVAILABILITY: results[CATEGORY_AVAILABILITY]},
                    db_path=db_path, reference_id=None,
                    timestamp=run_timestamp, selection=selection)
                summary["availability_logged_first"] = True""", 1)])
_ORDER_PROBE = '''
import io, json, os, sqlite3, tempfile
from contextlib import redirect_stderr, redirect_stdout
from oncotriage import paths as P
from oncotriage.monitoring import drift as D
from oncotriage.monitoring import drift_reference as R
from oncotriage.storage import database_logger as L

_TMP = tempfile.mkdtemp()
P._RESOLVED["inferences_path"] = os.path.join(_TMP, "seeded.db")
db = os.path.join(_TMP, "x.db")
e, o = io.StringIO(), io.StringIO()
with redirect_stderr(e), redirect_stdout(o):
    L.initialize_database(db)
STAMP = {stamp!r}
TUN = {tunables!r}
DEF = {defaults!r}
c = sqlite3.connect(db)
cols = ["started_at","finished_at","status","invocation_source","resumed",
        "tunables"] + list(STAMP)
p = ["2026-01-01T00:00:00Z","2026-01-01T00:00:00Z","FINISHED","batch_runner",
     None, TUN] + [STAMP[k] for k in STAMP]
run = c.execute("INSERT INTO runs (%s) VALUES (%s)" % (
    ",".join(cols), ",".join("?" for _ in cols)), p).lastrowid
icols = ["run_id","patient_id"] + list(DEF)
for i in range(30):
    v = dict(DEF); v["timestamp"] = "2026-01-01T00:%02d:00Z" % i
    v["patient_data_hash"] = "h%d" % i
    c.execute("INSERT INTO inferences (%s) VALUES (%s)" % (
        ",".join(icols), ",".join("?" for _ in icols)),
        [run, "P%03d" % i] + [v[k] for k in v])
c.commit(); c.close()


def explode(db_path):
    raise RuntimeError("the reference machinery blew up")


R.resolve_reference = explode
with redirect_stderr(e), redirect_stdout(o):
    try:
        D.run_drift_detection(D.COMPARISON_LATEST, db_path=db)
    except BaseException:
        pass
c = sqlite3.connect(db)
n = c.execute("SELECT COUNT(*) FROM drift_metrics").fetchone()[0]
c.close()
print(json.dumps({{"rows_on_disk": n}}))
'''.format(stamp=_STAMP, tunables=_TUNABLES, defaults=_INFERENCE_DEFAULTS)

if _order_problems:
    check("C5b the ordering plant applied", _order_problems, [])
else:
    _raw = _run_control("c5b_order", _ORDER_CONTROL_ROOT, _ORDER_PROBE)
    _reading = {}
    for _line in reversed(_raw.splitlines()):
        _line = _line.strip()
        if _line.startswith("{") and _line.endswith("}"):
            _reading = json.loads(_line)
            break
    check("C5b with the early write removed, a raise in the reference "
          "machinery leaves the availability reading OFF DISK -- which is "
          "exactly what section 9h detects",
          _reading.get("rows_on_disk"), 0)

# --- C6: THE NEUTRAL-ZERO RENDERING -------------------------------------
# The shipped renderer, restored: alert == 1 is an ALERT and everything else
# is an OK.
if _HAVE_STREAMLIT:
    _shipped_render = """def _row_display_state(row):"""
    _tab_path = os.path.join(_ROOT, "oncotriage/dashboard/tabs/drift.py")
    _tab_src = open(_tab_path, encoding="utf-8").read()
    check("the shipped renderer routes through the SHARED display owner "
          "rather than reading `alert` directly, which is what stops the tab "
          "and the console disagreeing",
          "display_state(str(status), str(policy)" in _tab_src, True)

    # The plant: a copy of the tab whose Status column is the shipped
    # two-state expression. Driven through the REAL AppTest, so what is
    # measured is the rendered page rather than a helper's return value.
    _plant_root = os.path.join(_TMP, "tabplant")
    if os.path.isdir(_plant_root):
        shutil.rmtree(_plant_root)
    os.makedirs(_plant_root)
    _plant_mod = os.path.join(_plant_root, "planted_drift_tab.py")
    # THE ANCHOR MOVED WHEN THE EMPTY-CATEGORY GUARD WAS ADDED (the
    # dashboard-fixes pass): the three Status/Computation/Alert-policy
    # assignments are now inside an `else:` and carry four more spaces of
    # indentation. The plant is re-anchored rather than loosened -- the
    # expression it replaces is the same one, one indentation level in -- and
    # the "the plant matched" check below is what reported the move, which is
    # that check working.
    _planted = _tab_src.replace(
        """        display_df["Status"] = display_df.apply(
            lambda row: f"{DISPLAY_MARKERS[row['display_state']]} "
                        f"{row['display_state']}", axis=1)""",
        """        display_df["Status"] = display_df["alert"].apply(
            lambda x: '\\U0001F6A8 ALERT' if x == 1 else '\\u2705 OK')""")
    check("C6 the plant matched the shipped Status expression",
          _planted != _tab_src, True)
    try:
        ast.parse(_planted)
        _plant_parses = True
    except SyntaxError:
        _plant_parses = False
    check("...and it parses, so a broken plant is a recorded failure rather "
          "than an abort", _plant_parses, True)
    if _plant_parses:
        open(_plant_mod, "w", encoding="utf-8").write(_planted)
        _PLANT_DRIVER = """
import pickle, sys
sys.path.insert(0, {plant!r})
sys.path.insert(0, {root!r})
from planted_drift_tab import render_drift_detection_tab
with open({frame!r}, "rb") as fh:
    df = pickle.load(fh)
render_drift_detection_tab(df)
"""
        import pickle as _pickle
        _frame_path = os.path.join(_TMP, "frame.pkl")
        with open(_frame_path, "wb") as _fh:
            _pickle.dump(pd.DataFrame(
                {"timestamp": pd.to_datetime(["2026-06-01T00:00:00Z"]),
                 "patient_id": ["P000"]}), _fh)
        _paths._RESOLVED["inferences_path"] = _HAPPY_DB
        _st.cache_data.clear()
        try:
            _planted_app = AppTest.from_string(
                _PLANT_DRIVER.format(plant=_plant_root, root=_ROOT,
                                     frame=_frame_path), default_timeout=120)
            _planted_app.run()
            _planted_table = first([d.value for d in _planted_app.dataframe],
                                   pd.DataFrame())
            _planted_states = (sorted(set(_planted_table["Status"]))
                               if len(_planted_table) else [])
        finally:
            _paths._RESOLVED["inferences_path"] = os.path.join(
                _TMP, "seeded-not-production.db")
            _st.cache_data.clear()
        check("C6 with the shipped two-state renderer, a refusal and a "
              "not-computed reading both render as a green OK -- the defect, "
              "on the page",
              any("OK" in s for s in _planted_states)
              and not any(_ds.DISPLAY_REFUSED in s or
                          _ds.DISPLAY_NOT_COMPUTED in s
                          for s in _planted_states), True)
        _shipped_states = (sorted(set(_table["Status"])) if len(_table)
                           else [])
        check("...and the shipped tab does NOT, which is what section 11 "
              "detects",
              (any(_ds.DISPLAY_REPORTING in s for s in _shipped_states),
               len(_shipped_states) > 1), (True, True))


# --- C7: THE ALERT CONVERSION -------------------------------------------
# THE REPAIR'S OWN CONTROL, AND IT ISOLATES THE CONVERSION FROM THE GATE. C4
# removes both at once, so a copy that passed C4 could still be converting; this
# one keeps `alert_is_meaningful` exactly as shipped and restores ONLY
# `int(bool(alert))`. That expression is the whole defect: `None` becomes 0,
# which the tab draws as a green OK, and `nan` becomes 1, which it draws as an
# ALERT -- two verdicts nobody computed, on a reading the axes agreed was
# meaningful, which is the one arrangement in which the renderer guard has
# nothing left to guard against.
_c7 = control_reading("c7_alert_converted", [(
    "oncotriage/monitoring/drift_states.py",
    """    outcome, value = classify_alert(alert)
    if not alert_is_meaningful(status, policy):
        return None, outcome
    if outcome != ALERT_PRESENT:
        return None, outcome
    return value, outcome""",
    """    if not alert_is_meaningful(status, policy):
        return None, ALERT_PRESENT
    return int(bool(alert)), ALERT_PRESENT""", 1)])
_c7_conv = _c7.get("alert_conversion", {})
check("C7 the conversion restored: a MISSING verdict becomes a 0 -- which is "
      "the value the tab renders as a green OK",
      [at(_c7_conv, "none"), at(_c7_conv, "nan")],
      [[_ds.STATUS_COMPUTED, 0], [_ds.STATUS_COMPUTED, 1]])
check("...and an INVALID one becomes a 1 rather than rejecting the reading, "
      "so `malformed_inputs` is never reached and the tab draws an ALERT",
      at(_c7_conv, "two"), [_ds.STATUS_COMPUTED, 1])
check("...while the clean copy answers None to all three, which is what makes "
      "the readings above about the plant",
      [at(_clean.get("alert_conversion", {}), k)
       for k in ("none", "nan", "two")],
      [[_ds.STATUS_COMPUTED, None], [_ds.STATUS_COMPUTED, None],
       [_ds.STATUS_MALFORMED_INPUTS, None]])
check("...and a REAL verdict is unchanged under the plant, so what the plant "
      "moved is the unreadable inputs and nothing else",
      [at(_c7_conv, "zero"), at(_c7_conv, "one")],
      [[_ds.STATUS_COMPUTED, 0], [_ds.STATUS_COMPUTED, 1]])

# --- C8: THE IMPLICIT COMPARISON ----------------------------------------
# A DEFAULT REINSTATED ON THE COMPARISON ARGUMENT. Nothing raises, nothing is
# logged, and every number the run produces is computed over a campaign nobody
# chose -- which is the half of the designed comparison the reference
# designation had already removed from the other side.
_c8 = control_reading("c8_implicit_latest", [(
    "oncotriage/monitoring/drift.py",
    """def run_drift_detection(comparison, db_path=None, log_to_db: bool = True
                        ) -> Dict[str, Dict]:""",
    """def run_drift_detection(comparison=COMPARISON_LATEST, db_path=None,
                        log_to_db: bool = True) -> Dict[str, Dict]:""", 1)])
check("C8 with a default reinstated, a call that names NO campaign RUNS",
      _c8.get("omitted_comparison"), "ran")
check("...and the shipped signature refuses the same call, which is what "
      "section 11c detects",
      _clean.get("omitted_comparison"), "raised")
check("...and the plant is the only difference: the control still records the "
      "selection on its rows, so what moved is whether a caller may omit it",
      _c8.get("selection_recorded"), [_drift.COMPARISON_SELECTION_LATEST])

# --- C9: THE HASH-ONLY PAIR RULE ----------------------------------------
# THE SHIPPED RULE, RESTORED: the patient hash and nothing else. The probe's
# third campaign is the same patients with the same hashes judged by a
# DIFFERENT MODEL, so every pair it produces is one the rule calls verified --
# and a paired statistic over them measures the model change under the word
# "drift". Reporting-only does not waive it: the deferral is of the ALERT, and
# a human reading the number is the consumer this protects.
_c9 = control_reading("c9_hash_only_pairs", [(
    "oncotriage/monitoring/drift_reference.py",
    """    have = set(table_columns(conn, "inferences"))
    items = [c for c in PAIR_EVIDENCE_INFERENCE if c in have]
    if "run_id" in have and table_columns(conn, "runs"):
        items.append(PAIR_EVIDENCE_RUN)
    return tuple(items)""",
    """    return ("patient_data_hash",)""", 1)])
check("C9 the hash-only rule restored: two campaigns judged by DIFFERENT "
      "MODELS over the same patients produce pairs it calls VERIFIED",
      (at(_c9.get("pair_counts", {}), "verified_pairs"),
       at(_c9.get("pair_counts", {}), "incomparable_pairs")), (5, 0))
check("...and it names nothing, because it looked at nothing that could "
      "disagree", _c9.get("pair_named"), [])
check("...while the shipped rule verifies none of them and NAMES the model, "
      "which is what section 6 detects",
      (at(_clean.get("pair_counts", {}), "verified_pairs"),
       at(_clean.get("pair_counts", {}), "incomparable_pairs"),
       _clean.get("pair_named")), (0, 5, ["matching_model"]))
check("...and the two copies disagree about the SAME rows, so the reading is "
      "about the rule rather than about two different populations",
      at(_c9.get("pair_counts", {}), "verified_pairs")
      + at(_c9.get("pair_counts", {}), "incomparable_pairs")
      == at(_clean.get("pair_counts", {}), "verified_pairs")
      + at(_clean.get("pair_counts", {}), "incomparable_pairs"), True)


# --- C10: THE SELECTION VOCABULARY GUARD --------------------------------
# IN-PROCESS RATHER THAN IN A COPY, and that is the natural control for a
# MODULE-GLOBAL lookup: `log_drift_metrics` resolves `require_selection` in its
# own module at call time, so rebinding the name reaches the shipped writer
# without a second copy of the package. The restore is asserted BY IDENTITY,
# which any callable of the same name would not satisfy.
_C10_DB = make_db("c10_selection.db")
_real_require_selection = _drift.require_selection
_drift.require_selection = lambda s: s               # the guard removed
try:
    quiet(_drift.log_drift_metrics, _surface, db_path=_C10_DB,
          selection=_drift.COMPARISON_LATEST)
finally:
    _drift.require_selection = _real_require_selection
check("C10 the restore put the real guard back, BY IDENTITY",
      _drift.require_selection is _real_require_selection, True)
_c10_stored = sorted({r["comparison_selection"] for r in rows_of(
    _C10_DB, "SELECT comparison_selection FROM drift_metrics")})
check("C10 with the guard removed, the OPT-IN ARGUMENT lands in the column -- "
      "a bucket no consumer groups on, and indistinguishable in SQL from a "
      "value this module defines",
      _c10_stored, [_drift.COMPARISON_LATEST])
check("...and it is NOT a member of the vocabulary, which is what makes the "
      "reading above a defect rather than a spelling",
      _drift.COMPARISON_LATEST in _drift.COMPARISON_SELECTIONS, False)
check("...while the shipped guard refuses the same call and writes NOTHING, "
      "which is what section 11c detects",
      "RAISED ComparisonSelectionError" in str(drive(
          _drift.log_drift_metrics, _surface, db_path=make_db("c10_clean.db"),
          selection=_drift.COMPARISON_LATEST)), True)

# --- C11: THE SELECTION CLAUSE ON THE PAGE ------------------------------
# The clause removed. The column is still written, the row still records how
# the campaign was chosen, and the page stops saying so -- which is the shape
# the availability tile already shipped once: a value stored on every run and
# rendered by nothing.
if _HAVE_STREAMLIT:
    _real_clause = _drift_tab._selection_clause
    _drift_tab._selection_clause = lambda _df: ""
    try:
        _c11_caption = _drift_tab._reference_caption(
            pd.DataFrame({"reference_id": [None],
                          "comparison_selection":
                          [_drift.COMPARISON_SELECTION_LATEST]}),
            pd.DataFrame())
    finally:
        _drift_tab._selection_clause = _real_clause
    check("C11 the restore put the real clause back, BY IDENTITY",
          _drift_tab._selection_clause is _real_clause, True)
    check("C11 with the clause removed, the caption names no comparison at "
          "all -- while the row underneath it still records one",
          _drift_tab_labels[_drift.COMPARISON_SELECTION_LATEST]
          in _c11_caption, False)
    check("...and the shipped caption DOES name it on the same input, which "
          "is what makes the reading above about the plant",
          _drift_tab_labels[_drift.COMPARISON_SELECTION_LATEST]
          in _drift_tab._reference_caption(
              pd.DataFrame({"reference_id": [None],
                            "comparison_selection":
                            [_drift.COMPARISON_SELECTION_LATEST]}),
              pd.DataFrame()), True)


# ===========================================================================
# CLEAN-UP AND THE REPOSITORY GUARANTEE
# ===========================================================================

print("\n" + "=" * 70)
print("Clean-up")
print("=" * 70)

_paths._RESOLVED["inferences_path"] = _SAVED_RESOLVED
if _SAVED_RESOLVED is None:
    _paths._RESOLVED.pop("inferences_path", None)
check("paths._RESOLVED was restored to what it held at import",
      _paths._RESOLVED.get("inferences_path"), _SAVED_RESOLVED)

for _rel, _before in _WATCHED.items():
    check(f"{_rel} is byte-identical", _sha(os.path.join(_ROOT, _rel)),
          _before)

shutil.rmtree(_TMP, ignore_errors=True)
check("the temp directory was removed", os.path.isdir(_TMP), False)


# ===========================================================================
# SUMMARY
# ===========================================================================

print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"passed: {_RESULTS['passed']}")
print(f"failed: {_RESULTS['failed']}")

if _FAILURES:
    print("\nFailures:")
    for _f in _FAILURES:
        print(textwrap.indent(f"  - {_f}", ""))

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Sep 10 2026

@author: ramyalsaffar
"""
