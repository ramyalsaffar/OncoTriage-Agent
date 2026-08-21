# Ablation Database Isolation Test
##################################

"""
``oncotriage/ablation/study.py`` was the LAST database writer in this project
whose path could not be overridden. Every other one takes it as an argument --
``log_inference(db_path=)``, ``log_drift_metrics(db_path=)``,
``empty_database(db_path, flag)``, ``select_samples(source_db, output_db)`` --
and this one resolved its own, so a study run could not be pointed at a scratch
file and no isolation test could be written for it. That is why this file did
not exist before pass 20f-1: the test was not failing, it was IMPOSSIBLE.

WHAT THIS FILE HOLDS
--------------------
    1. The default is still the production database, and the scratch path is
       not it. Every assertion below is meaningless without this, which is why
       it is asserted before anything is written.
    2. An explicit path is honoured and is NOT CACHED -- the cache answers a
       question about the machine, an argument answers one about a call.
    3. THE WRITERS WRITE WHERE THEY ARE TOLD. The whole database surface --
       init, run row, result row, finalize, summary -- is driven against a
       scratch database, and the module DEFAULT (repointed at a decoy) stays
       empty.
    4. THE SAME ASSERTION FAILS WHEN THE PATH IS OMITTED. The identical calls
       without ``db_path`` land in the default, which is what section 1 proved
       is production when nothing is installed. This is the demonstration, and
       it is run against a DECOY rather than against the real database -- the
       File 41 precedent -- because a test that proved the point by writing
       real rows would be the defect it is testing for.
    5. ``--db`` exists on the command line and reaches ``main()``: every call
       ``main()`` makes to a writer passes ``db_path``, checked by AST with a
       negative control, because running ``main()`` for real costs money.
    6. THE CHECKPOINT WRITE FAILURES ARE COUNTED (item 11a, pass 20f-1). Both
       handlers in ``save_ablation_checkpoint`` are driven FOR REAL -- no
       source is patched -- by making the temp file's name a directory, and
       both keys must appear.

THE PRODUCTION ABLATION DATABASE IS MEASURED BEFORE AND AFTER and must be
byte-identical. NO NETWORK, NO KEYS, NO SPEND: the cancer registry is a
stand-in installed through ``oncotriage.agent.deps``, no graph is compiled and
no model is called.

WHY THIS FILE IS NOT IN THE COLLISION MATRIX, derived rather than assumed: it
writes only inside a temporary directory, it patches no file in the repository,
and it reads none -- ``oncotriage/ablation/study.py`` is parsed for section 5,
and that file is written by neither of the suite's two writers
(``oncotriage/registries/cancer_code_registry.py`` and ``oncotriage/config.py``).
The registry is a stand-in precisely so that this file does not depend on the
text the audit control plants into.

Run from terminal:
    python tests/test_ablation_db_isolation.py

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

import ast
import hashlib
import inspect
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from oncotriage.agent import deps as _deps
from oncotriage import run_fingerprint as _run_fingerprint
from oncotriage.ablation import study as _study


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
        _FAILURES.append(f"{label}\n          expected: {expected}\n          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


def raises(fn):
    """(exception type name, message) for a call that must raise, else (None, '')."""
    try:
        fn()
    except Exception as exc:            # noqa: BLE001 -- the type is the answer
        return type(exc).__name__, str(exc)
    return None, ""


def _digest(path):
    """sha256 of a file, or the string 'absent'."""
    if not os.path.exists(path):
        return "absent"
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rows(db, table):
    """Row count, or None when the table (or the database) is not there."""
    if not os.path.exists(db):
        return None
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        conn.close()


_TMP = tempfile.mkdtemp(prefix="oncotriage-ablation-")
_SCRATCH_DIR = os.path.join(_TMP, "told")
_DECOY_DIR = os.path.join(_TMP, "decoy")
os.makedirs(_SCRATCH_DIR)
os.makedirs(_DECOY_DIR)
_SCRATCH_DB = os.path.join(_SCRATCH_DIR, "ablation_results.db")
_DECOY_DB = os.path.join(_DECOY_DIR, "ablation_results.db")


class _StubRegistry:
    """The two methods _get_patient_group() calls, and nothing else.

    A stand-in rather than the real CancerCodeRegistry so this file neither
    builds the ICD-10-CM release nor depends on the source text that
    tests/test_registries_cancer_code_claims_audit_control.py plants defects
    into. Installed through oncotriage/agent/deps.py, which is the seam the
    study reaches the registry through.
    """

    @staticmethod
    def is_primary_cancer(condition):
        return bool(condition.get("display"))

    @staticmethod
    def sort_key(condition):
        return condition.get("display", "")


_PATIENT = {
    "patient_id": "isolation-probe-patient",
    "conditions": [{"display": "Malignant neoplasm of breast"}],
}

_RESULT = {
    "matches": [{"nct_id": "NCT00000001", "match_score": 0.5}],
    "near_misses": [],
    "not_evaluable": [],
    "stage_timings": {"query_expansion": 0.1, "llm_classifier_evaluation": 0.2},
    "primary_condition": "Malignant neoplasm of breast",
    "candidates_retrieved": 10,
    "candidates_reranked": 8,
    "candidates_after_rule_filter": 6,
    "candidates_after_quality_filter": 5,
    "candidates_evaluated": 4,
    "eligible_count": 1,
    "llm_classifier_input_tokens": 0,
    "llm_classifier_output_tokens": 0,
    "mesh_dropped": 0,
    "stage_dropped": 0,
    "histology_dropped": 0,
    "error": "",
}

_FLAGS = {"retrieval_mode": "hybrid"}


def _drive_writers(db_path):
    """Every function in the study that opens the database, in run order.

    `db_path=None` is the OMITTED arm -- the calls are otherwise identical, so
    the two arms differ in exactly the thing under test.
    """
    if db_path is None:
        _study.init_ablation_db()
        run_id = _study._create_run("full_pipeline", "probe", 1)
        _study.log_ablation_result(run_id, "full_pipeline", _PATIENT, _RESULT,
                                   _FLAGS)
        _study._finalize_run(run_id, 1.0)
        _study.generate_summary()
    else:
        _study.init_ablation_db(db_path=db_path)
        run_id = _study._create_run("full_pipeline", "probe", 1,
                                    db_path=db_path)
        _study.log_ablation_result(run_id, "full_pipeline", _PATIENT, _RESULT,
                                   _FLAGS, db_path=db_path)
        _study._finalize_run(run_id, 1.0, db_path=db_path)
        _study.generate_summary(db_path=db_path)


# ===========================================================================
# SECTION 1: THE DEFAULT IS PRODUCTION, AND THE SCRATCH PATH IS NOT
# ===========================================================================
# Read BEFORE any decoy is installed, so this is the real answer. Every
# assertion in this file is about the difference between "told" and "not told",
# and if the two were the same path they would all pass vacuously.

print("=" * 70)
print("Section 1: the default resolves to the production database")
print("=" * 70)

_PRODUCTION_DB = str(_study.ablation_db())
_PRODUCTION_SUMMARY = str(_study.ablation_summary_json())
_PRODUCTION_DIGEST_BEFORE = _digest(_PRODUCTION_DB)
_PRODUCTION_SUMMARY_DIGEST_BEFORE = _digest(_PRODUCTION_SUMMARY)

check("the default database is named ablation_results.db",
      os.path.basename(_PRODUCTION_DB), "ablation_results.db")
check("...and it is NOT this file's scratch database (non-degeneracy)",
      _PRODUCTION_DB == _SCRATCH_DB, False)
check("...and NOT the decoy either (non-degeneracy)",
      _PRODUCTION_DB == _DECOY_DB, False)
check("the default summary sits beside the default database",
      os.path.dirname(_PRODUCTION_SUMMARY), os.path.dirname(_PRODUCTION_DB))

print(f"        production database: {_PRODUCTION_DB}")
print(f"        digest before:       {_PRODUCTION_DIGEST_BEFORE[:16]}...")


# ===========================================================================
# SECTION 2: AN EXPLICIT PATH IS HONOURED AND NOT CACHED
# ===========================================================================

print()
print("=" * 70)
print("Section 2: an explicit path is honoured and never cached")
print("=" * 70)

check("ablation_db takes db_path with a default of None",
      [(p.name, p.default) for p in
       inspect.signature(_study.ablation_db).parameters.values()],
      [("db_path", None)])
check("an explicit path comes back as given",
      str(_study.ablation_db(_SCRATCH_DB)), _SCRATCH_DB)
check("...and the summary follows it into the same directory",
      str(_study.ablation_summary_json(_SCRATCH_DB)),
      os.path.join(_SCRATCH_DIR, "ablation_summary.json"))
check("...a SECOND explicit path is not answered with the first, so the "
      "argument was not cached",
      str(_study.ablation_db(_DECOY_DB)), _DECOY_DB)
check("...and the default is still the default afterwards",
      str(_study.ablation_db()), _PRODUCTION_DB)

check("every function that opens the database takes db_path",
      sorted(_name for _name in ("init_ablation_db", "_create_run",
                                 "_finalize_run", "log_ablation_result",
                                 "generate_summary")
             if "db_path" not in
             inspect.signature(getattr(_study, _name)).parameters),
      [])


# ===========================================================================
# THE DECOY GOES IN HERE, AND EVERYTHING BELOW RUNS BEHIND IT
# ===========================================================================
# From this line on, the module's DEFAULT is the decoy rather than production.
# Section 4 deliberately calls the writers with no path at all, and without
# this the demonstration would write real rows into the real database -- which
# is precisely the accident this whole file exists to make impossible.
#
# _RESOLVED is the module's own cache, and installing into it is the narrowest
# seam available: nothing in oncotriage.paths is touched, so no other module's
# view of the project tree moves.

_study._RESOLVED["ablation_db"] = Path(_DECOY_DB)
_study._RESOLVED["ablation_summary_json"] = Path(_DECOY_DIR) / "ablation_summary.json"

_registry_saved = _deps.set_override(_deps.CANCER_REGISTRY, _StubRegistry())


# ===========================================================================
# SECTION 3: TOLD -- THE WRITERS WRITE WHERE THEY ARE TOLD
# ===========================================================================

print()
print("=" * 70)
print("Section 3: given a path, every writer uses it")
print("=" * 70)

check("the decoy is now the module default (the arms differ only in the "
      "argument)",
      str(_study.ablation_db()), _DECOY_DB)
check("...and nothing has been written to it yet (non-degeneracy)",
      os.path.exists(_DECOY_DB), False)

_type, _message = raises(lambda: _drive_writers(_SCRATCH_DB))
check("the whole writer surface ran without raising", (_type, _message),
      (None, ""))

check("the scratch database exists", os.path.exists(_SCRATCH_DB), True)
check("...it holds the run row", _rows(_SCRATCH_DB, "ablation_runs"), 1)
check("...and the result row", _rows(_SCRATCH_DB, "ablation_results"), 1)
check("...the summary was exported beside it",
      os.path.exists(os.path.join(_SCRATCH_DIR, "ablation_summary.json")),
      True)

# THE ASSERTION THIS FILE IS FOR.
_TOLD_LEFT_DEFAULT_ALONE = not os.path.exists(_DECOY_DB)
check("THE DEFAULT DATABASE WAS NOT TOUCHED", _TOLD_LEFT_DEFAULT_ALONE, True)
check("...and neither was the default summary",
      os.path.exists(os.path.join(_DECOY_DIR, "ablation_summary.json")), False)


# ===========================================================================
# SECTION 4: OMITTED -- THE SAME ASSERTION FAILS
# ===========================================================================
# The identical calls with the argument dropped. Everything else is the same
# object, the same row, the same order.

print()
print("=" * 70)
print("Section 4: omit the path and that assertion stops holding")
print("=" * 70)

_type4, _message4 = raises(lambda: _drive_writers(None))
check("the omitted arm also ran without raising (it is not failing for some "
      "other reason)", (_type4, _message4), (None, ""))

_OMITTED_LEFT_DEFAULT_ALONE = not os.path.exists(_DECOY_DB)
check("the omitted arm wrote into the default", os.path.exists(_DECOY_DB), True)
check("...it put the run row there", _rows(_DECOY_DB, "ablation_runs"), 1)
check("...and the result row", _rows(_DECOY_DB, "ablation_results"), 1)
check("...and the summary too",
      os.path.exists(os.path.join(_DECOY_DIR, "ablation_summary.json")), True)

check("SECTION 3'S ASSERTION, EVALUATED AGAINST THE OMITTED ARM, IS FALSE -- "
      "so it can fail, and section 3 passing means something",
      (_TOLD_LEFT_DEFAULT_ALONE, _OMITTED_LEFT_DEFAULT_ALONE), (True, False))

check("...and the scratch database did not grow: the omitted arm went to the "
      "default and only to the default",
      _rows(_SCRATCH_DB, "ablation_results"), 1)


# ===========================================================================
# SECTION 5: --db REACHES main()
# ===========================================================================
# By AST, because running main() is 525 live Stage 5 calls. The flag is parsed
# for real; what is checked structurally is that every writer call inside
# main() passes db_path, which is the part a reader cannot see at a glance.

print()
print("=" * 70)
print("Section 5: --db is parsed, and main() threads it to every writer")
print("=" * 70)

_saved_argv = sys.argv
sys.argv = ["study", "--db", _SCRATCH_DB]
try:
    _args = _study.parse_args()
finally:
    sys.argv = _saved_argv

check("--db is parsed into args.db", _args.db, _SCRATCH_DB)

sys.argv = ["study"]
try:
    _default_args = _study.parse_args()
finally:
    sys.argv = _saved_argv
check("...and it defaults to None, so every documented command is unchanged",
      _default_args.db, None)

_STUDY_SRC = Path(os.path.abspath(_study.__file__)).read_text(encoding="utf-8")
_STUDY_TREE = ast.parse(_STUDY_SRC)
_WRITERS = ("init_ablation_db", "_create_run", "_finalize_run",
            "log_ablation_result", "generate_summary")


def _writer_calls_missing_db_path(tree, function_name):
    """Writer calls inside `function_name` that do not pass db_path.

    Nested functions count: _process_one() is defined inside main() and is
    where log_ablation_result() is called from, so a walk that stopped at the
    first def would report exactly the call most likely to be forgotten.
    """
    target = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == function_name),
                  None)
    if target is None:
        return ["<function not found>"]
    missing = []
    for node in ast.walk(target):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name in _WRITERS and not any(kw.arg == "db_path"
                                        for kw in node.keywords):
            missing.append(f"{name}@line{node.lineno}")
    return missing


check("main() reaches every writer with db_path",
      _writer_calls_missing_db_path(_STUDY_TREE, "main"), [])

# NEGATIVE CONTROL. The check above is "the list is empty", which a scan that
# has stopped finding calls also satisfies. One keyword is removed from an AST
# COPY -- the file on disk is not touched -- and the same scan must report it.
_MUTATED = ast.parse(_STUDY_SRC)
_removed = None
for _node in ast.walk(_MUTATED):
    if (isinstance(_node, ast.Call)
            and (getattr(_node.func, "id", None) in _WRITERS)
            and any(_kw.arg == "db_path" for _kw in _node.keywords)):
        _node.keywords = [_kw for _kw in _node.keywords if _kw.arg != "db_path"]
        _removed = getattr(_node.func, "id", None)
        break

check("a db_path keyword was found to remove (non-degeneracy)",
      _removed in _WRITERS, True)
check("...and the scan reports the call it was removed from",
      len(_writer_calls_missing_db_path(_MUTATED, "main")), 1)


# ===========================================================================
# SECTION 5b: --db NAMES AN ABSENT PARENT, AND THE CHECKPOINT FOLLOWS IT
# ===========================================================================
# Pass 20f-3. Two behaviour changes, both recorded as follow-ups by pass 20f-1
# and both untestable before it made the database redirectable at all.
#
# THIS SECTION IS WHY THIS FILE'S COUNT MOVED (43 -> 72). Every other count in
# the suite is unchanged; a behaviour change with no assertion behind it is what
# this project treats as the defect, so the two are added here rather than
# argued in a commit message.

print()
print("=" * 70)
print("Section 5b: an absent --db parent is refused by name; the checkpoint "
      "follows the database")
print("=" * 70)

_ABSENT_PARENT = os.path.join(_TMP, "does-not-exist", "ablation.db")
check("the probe parent really is absent (non-degeneracy)",
      os.path.isdir(os.path.dirname(_ABSENT_PARENT)), False)

for _label, _fn in (("ablation_db", _study.ablation_db),
                    ("ablation_summary_json", _study.ablation_summary_json),
                    ("_ablation_checkpoint_path", _study._ablation_checkpoint_path)):
    _t, _m = raises(lambda _f=_fn: _f(_ABSENT_PARENT))
    check(f"{_label}() RAISES on an absent parent", _t, "RuntimeError")
    check(f"...naming the directory", os.path.dirname(_ABSENT_PARENT) in _m, True)
    check(f"...and naming the flag the operator used", "--db" in _m, True)

# WHAT THE GUARD REPLACED, shown rather than described: the pre-20f-3 path was
# straight to sqlite3, whose message names neither the path nor the flag.
_t, _m = raises(lambda: sqlite3.connect(_ABSENT_PARENT))
check("control: sqlite3 alone raises OperationalError on the same path",
      _t, "OperationalError")
check("...and its message names NEITHER the directory NOR the flag, which is "
      "the whole reason for the guard",
      (os.path.dirname(_ABSENT_PARENT) in _m, "--db" in _m), (False, False))

# NON-DEGENERATE: the guard must not fire on the default, which resolves a
# directory _glob_one has already proved exists.
_t, _m = raises(lambda: _study.ablation_db())
check("the DEFAULT path is not subjected to the guard and still resolves",
      (_t, str(_study.ablation_db()).endswith(_study.ABLATION_DB_FILENAME)),
      (None, True))

# --- the checkpoint follows the database ----------------------------------
_PROD_CKPT = _study._ablation_checkpoint_path()
_SCRATCH_CKPT = _study._ablation_checkpoint_path(_SCRATCH_DB)

check("the default checkpoint is still the production one, unchanged",
      str(_PROD_CKPT).endswith(_study.ABLATION_CHECKPOINT_FILENAME), True)
check("...and an explicit --db gets a DIFFERENT one",
      _SCRATCH_CKPT == _PROD_CKPT, False)
check("...beside that database rather than in the production checkpoint "
      "directory",
      str(_SCRATCH_CKPT.parent), str(Path(_SCRATCH_DB).parent))
check("...named after it, so two scratch databases in one directory do not "
      "share resume state either",
      _study._ablation_checkpoint_path(
          os.path.join(os.path.dirname(_SCRATCH_DB), "other.db")) == _SCRATCH_CKPT,
      False)

# THE ASSERTION MUST BE ABLE TO FAIL, and the demonstration drives the DEFECT
# through the real caller rather than reasoning about it. The pre-20f-3 shape is
# a function that IGNORES db_path and always answers the production path; a
# stand-in of exactly that shape is installed on the module -- the mechanism
# section 6 below already uses, and the one the fixture harnesses use -- and
# `load_ablation_checkpoint(db_path=B)` is then asked what it sees.
#
# Two throwaway "databases" stand in for production and scratch, so nothing here
# reads or writes the real checkpoint at all. NO exec(), no source patching:
# tests/test_storage_query_layer.py holds the repository's one argued exec()
# allowance and this file has no business joining it.
# A LITERAL CONFIGURATION STAMP, so this file stays offline. The
# configuration-fingerprint pass made a checkpoint carry what produced it, and
# save/load default to run_fingerprint.current(), which resolves the backing
# Qdrant collection and counts its points -- two live round trips. Passing the
# stamp explicitly is the documented seam for a caller with no endpoint. Its
# VALUE is irrelevant to every check below; that both sides are handed the SAME
# one is what matters, because this section is about which FILE is read and
# never about which configuration wrote it.
# The KEYS are DERIVED from FINGERPRINT_FIELDS rather than enumerated, so a
# field added to the gate lands here without an edit. Enumerating them meant a
# version bump silently left this stamp short of a gated field, which
# `compare()` answers FP_UNRESOLVED for -- a refusal in a section that is not
# about refusals at all.
_STAMP = dict(
    {_f: f"test-{_f}" for _f in _run_fingerprint.FINGERPRINT_FIELDS},
    fingerprint_version=_run_fingerprint.FINGERPRINT_VERSION,
)

_ISO_DIR = os.path.join(_TMP, "resume-isolation")
os.makedirs(_ISO_DIR)
_DB_A = os.path.join(_ISO_DIR, "stands_in_for_production.db")
_DB_B = os.path.join(_ISO_DIR, "scratch.db")
_CKPT_A = _study._ablation_checkpoint_path(_DB_A)

_study.save_ablation_checkpoint({("full_pipeline", "already-done")},
                                db_path=_DB_A, fingerprint=_STAMP)
check("A's checkpoint was written (non-degeneracy: the defect below needs "
      "something to be wrongly inherited)", _CKPT_A.exists(), True)

_saved_ckpt_fn = _study._ablation_checkpoint_path
try:
    # The pre-20f-3 shape: db_path is accepted and discarded.
    _study._ablation_checkpoint_path = lambda db_path=None: _CKPT_A
    check("PRE-20f-3: a run told to write to B reads A's resume state -- it "
          "would skip those pairs and write nothing for them into B, then "
          "print COMPLETE. THIS IS THE DEFECT.",
          _study.load_ablation_checkpoint(db_path=_DB_B,
                                    fingerprint=_STAMP),
          {("full_pipeline", "already-done")})
finally:
    _study._ablation_checkpoint_path = _saved_ckpt_fn

check("...while the SHIPPED function gives B its own, empty, resume state",
      _study.load_ablation_checkpoint(db_path=_DB_B,
                                    fingerprint=_STAMP), set())
check("...and A's is still A's, so the fix isolates rather than disabling "
      "resume", _study.load_ablation_checkpoint(db_path=_DB_A,
                                    fingerprint=_STAMP),
      {("full_pipeline", "already-done")})
check("...and the two are different files (non-degeneracy)",
      _study._ablation_checkpoint_path(_DB_A)
      == _study._ablation_checkpoint_path(_DB_B), False)

# --- load / save / clear honour it, and the production file is untouched ---
_prod_ckpt_before = (_PROD_CKPT.exists(),
                     _PROD_CKPT.read_bytes() if _PROD_CKPT.exists() else None)

_study.save_ablation_checkpoint({("full_pipeline", "iso-1")},
                                db_path=_SCRATCH_DB, fingerprint=_STAMP)
check("save_ablation_checkpoint() wrote the scratch checkpoint",
      _SCRATCH_CKPT.exists(), True)
check("...and load_ablation_checkpoint() reads it back",
      _study.load_ablation_checkpoint(db_path=_SCRATCH_DB,
                                    fingerprint=_STAMP),
      {("full_pipeline", "iso-1")})
_study.clear_ablation_checkpoint(db_path=_SCRATCH_DB)
check("...and clear_ablation_checkpoint() removes THAT one",
      _SCRATCH_CKPT.exists(), False)

check("the production checkpoint was neither created nor modified by any of "
      "the three",
      (_PROD_CKPT.exists(),
       _PROD_CKPT.read_bytes() if _PROD_CKPT.exists() else None),
      _prod_ckpt_before)

# --- main() threads db_path to the checkpoint too --------------------------
_CKPT_FNS = ("load_ablation_checkpoint", "save_ablation_checkpoint",
             "clear_ablation_checkpoint")


def _ckpt_calls_missing_db_path(tree, function_name):
    target = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == function_name),
                  None)
    if target is None:
        return ["<function not found>"]
    return [f"{getattr(n.func, 'id', None)}@line{n.lineno}"
            for n in ast.walk(target)
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", None) in _CKPT_FNS
            and not any(kw.arg == "db_path" for kw in n.keywords)]


check("main() reaches all three checkpoint functions with db_path",
      _ckpt_calls_missing_db_path(_STUDY_TREE, "main"), [])

_MUTATED_CKPT = ast.parse(_STUDY_SRC)
_removed_ckpt = None
for _node in ast.walk(_MUTATED_CKPT):
    if (isinstance(_node, ast.Call)
            and getattr(_node.func, "id", None) in _CKPT_FNS
            and any(_kw.arg == "db_path" for _kw in _node.keywords)):
        _node.keywords = [_kw for _kw in _node.keywords if _kw.arg != "db_path"]
        _removed_ckpt = getattr(_node.func, "id", None)
        break
check("a db_path keyword was found to remove (non-degeneracy)",
      _removed_ckpt in _CKPT_FNS, True)
check("...and the scan reports the call it was removed from",
      len(_ckpt_calls_missing_db_path(_MUTATED_CKPT, "main")), 1)


# ===========================================================================
# SECTION 6: THE CHECKPOINT WRITE FAILURES ARE COUNTED (item 11a)
# ===========================================================================
# Both handlers are driven FOR REAL. The temp file's name is made a DIRECTORY,
# so the atomic write fails on open() and the cleanup then fails on unlink() --
# which is exactly the pair the outer and inner handlers cover, and the inner
# one is the `except OSError: pass` the exception audit lists as SILENT.
#
# No source is patched and no copy is exec'd: the real function runs.

print()
print("=" * 70)
print("Section 6: both checkpoint handlers record what they swallowed")
print("=" * 70)

check("CHECKPOINT_WRITE_FAILURES exists and starts empty (non-degeneracy)",
      dict(_study.CHECKPOINT_WRITE_FAILURES), {})

_CHECKPOINT_DIR = os.path.join(_TMP, "checkpoint")
os.makedirs(_CHECKPOINT_DIR)
_CHECKPOINT = Path(_CHECKPOINT_DIR) / "ablation_checkpoint.json"

# A healthy write first, so the failure below is a statement about the failure
# and not about the function never having worked here.
_saved_path_fn = _study._ablation_checkpoint_path
# `db_path=None` because pass 20f-3 gave the real function that parameter;
# the stand-in ignores it, which is right here -- this section is about the
# two OSError handlers and not about which checkpoint is chosen.
_study._ablation_checkpoint_path = lambda db_path=None: _CHECKPOINT
try:
    _study.save_ablation_checkpoint({("full_pipeline", "p1")},
                                    fingerprint=_STAMP)
    check("a healthy write produces the checkpoint file (non-degeneracy)",
          _CHECKPOINT.exists(), True)
    check("...and records no degradation",
          dict(_study.CHECKPOINT_WRITE_FAILURES), {})
    check("...and the file is readable JSON with the pair in it",
          json.loads(_CHECKPOINT.read_text())["count"], 1)

    # Now make the temp file's name a directory. open(..., "w") on a directory
    # is an OSError, and so is unlink() on one.
    _blocker = _CHECKPOINT.with_suffix(".tmp")
    os.makedirs(_blocker)

    _type6, _message6 = raises(
        lambda: _study.save_ablation_checkpoint({("full_pipeline", "p2")},
                                                fingerprint=_STAMP))
    check("the failing write still returns normally -- the recovery is "
          "unchanged", (_type6, _message6), (None, ""))

    _keys = sorted(_study.CHECKPOINT_WRITE_FAILURES)
    check("the write failure was counted",
          sum(1 for _k in _keys if _k.startswith("write:")), 1)
    check("THE SILENT ONE WAS COUNTED TOO -- the unlink that used to be "
          "`except OSError: pass`",
          sum(1 for _k in _keys if _k.startswith("tmp_unlink:")), 1)
    check("...and each key carries the exception type, which is what "
          "distinguishes a full disk from a permissions problem",
          sorted(_k.split(":")[1].endswith("Error") for _k in _keys),
          [True, True])
    print(f"        counter: {dict(_study.CHECKPOINT_WRITE_FAILURES)}")

    check("...and the earlier checkpoint file is still the one on disk, "
          "unchanged by the failed write",
          json.loads(_CHECKPOINT.read_text())["count"], 1)
finally:
    _study._ablation_checkpoint_path = _saved_path_fn


# ===========================================================================
# SECTION 7: THE PRODUCTION DATABASE IS EXACTLY AS IT WAS
# ===========================================================================

print()
print("=" * 70)
print("Section 7: the production ablation database is unchanged")
print("=" * 70)

# The registry stand-in comes out. set_override() returns UNSET when nothing
# was installed before, which is what distinguishes "put the previous override
# back" from "remove mine" -- the distinction deps.py added the sentinel for.
if _registry_saved is _deps.UNSET:
    _deps.clear_override(_deps.CANCER_REGISTRY)
else:
    _deps.set_override(_deps.CANCER_REGISTRY, _registry_saved)

check("the production database digest is unchanged across this whole run",
      _digest(_PRODUCTION_DB), _PRODUCTION_DIGEST_BEFORE)
check("...and so is the production summary export",
      _digest(_PRODUCTION_SUMMARY), _PRODUCTION_SUMMARY_DIGEST_BEFORE)
check("...and the digest is a real one, not 'absent' on both sides "
      "(non-degeneracy)",
      _PRODUCTION_DIGEST_BEFORE != "absent"
      or _PRODUCTION_SUMMARY_DIGEST_BEFORE != "absent",
      True)

shutil.rmtree(_TMP, ignore_errors=True)


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

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
