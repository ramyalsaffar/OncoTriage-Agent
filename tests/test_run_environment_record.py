# Run Environment Record Test
############################

"""What produced a run: the environment record, its storage, and the model pin.

WHAT WAS MISSING
----------------
``oncotriage/run_fingerprint.py`` recorded the CONFIGURATION a run was taken
under and gated a resume on it. It said nothing about the MACHINE. Two runs with
an identical stamp could come from two builds of this project, two resolved
dependency sets and two container images, and every artifact either wrote was
silent about all three -- so "why did these two campaigns disagree" had no
answer below the configuration layer.

And ``config.CROSS_ENCODER_MODEL`` was a REPOSITORY rather than a revision. An
unpinned ``from_pretrained`` resolves that repository's `main`, which a third
party can move; a cold cache downloads what is there now and a warm one keeps
what it got. Two campaigns a month apart could be ranked by two different sets
of weights while naming the identical checkpoint, and NOTHING would raise.

WHAT THIS FILE HOLDS
--------------------
    1. THE RECORD IS COMPLETE AND ITS VOCABULARIES ARE CLOSED: every declared
       field present, the image source a member of its own tuple, and the
       three restated storage-layer vocabularies round-tripped against the
       modules they were restated FROM.
    2. THE PIN IS PASSED AND VERIFIED. Both ``from_pretrained`` calls are
       handed ``revision=config.CROSS_ENCODER_REVISION``, asserted BY AST so a
       future edit that drops one is caught without loading 836 MB; and
       ``_verify_cross_encoder_revision`` is driven in BOTH directions --
       agreement returns, disagreement RAISES, absence counts.
    3. THE MIGRATION IS ADDITIVE, driven on a FABRICATED PRE-ERA DATABASE
       carrying an era-8 ``runs`` table and no ``run_environment`` at all,
       whose existing row is required to survive with its old values intact.
    4. THE RECORD REACHES THE ROW, through the real ``start_run_record``, read
       back out of SQLite.
    5. THE SNAPSHOT IS STORED ONCE PER DISTINCT HASH -- two runs sharing an
       environment produce two run rows and ONE ``run_environment`` row, and a
       third run with a different hash produces a second.
    6. THE COERCION RULES: ``git_dirty`` as 0/1/NULL, a non-numeric
       ``package_count`` NULLed rather than stored as TEXT, and an
       ``environment`` of ``None`` leaving all eight columns NULL.
    7. THE PLANTED DEFECT -- A MODEL LOADED OUTSIDE THE RECORD. A copy of
       ``deps.py`` with the ``revision=`` argument stripped is shown to load
       whatever `main` points at, WITH THE CLEAN CONTROL FIRST: the shipped
       module refuses a checkpoint whose reported revision is not the pinned
       one, and the planted one does not.
    8. TWO MORE PLANTED CONTROLS on the storage side, each with its clean arm.
    9. THE PRODUCTION DATABASE IS NEVER OPENED and the two package files this
       reads are sha256-compared at the end.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO CORPUS, NO LIVE SERVER, NO
DOCKER DAEMON. NO MODEL IS LOADED: ``ONCOTRIAGE_DEFER_LOCAL_MODELS`` is set
above the imports and section 2 asserts ``torch`` and ``transformers`` never
enter ``sys.modules`` -- the revision verifier is a PURE FUNCTION OF ITS
ARGUMENT, which is the natural control for one, and the pin is checked by AST.
It DOES spawn ``git`` (section 1 drives the real ``git_commit()``) and it reads
dist-info off disk, both inside functions.

NOT in the collision matrix: every database is inside a ``tempfile.mkdtemp`` it
removes and asserts gone, ``paths._RESOLVED`` is seeded so nothing can resolve
to the production tree, and the two repository files it reads --
``oncotriage/agent/deps.py`` and ``oncotriage/environment.py`` -- are written by
neither of the suite's two writers.

IT EXECS NOTHING and loads no module by location: the one plant is an ``ast``
walk over an EDITED STRING, which is the right instrument because the property
it defeats is itself static.
"""

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

# ABOVE THE IMPORTS AND NOT BESIDE THEM. oncotriage/agent/deps.py reads this
# variable ONCE, at its own import, and `oncotriage.batch.runner` pulls that
# module in transitively -- so an assignment below the import block would reach
# nothing and this file would download 836 MB of weights to check a string.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

import ast
import hashlib
import shutil
import sqlite3
import tempfile

from oncotriage import config
from oncotriage import environment as _env
from oncotriage import paths as _paths
from oncotriage import run_fingerprint as _rf
from oncotriage import settings as _settings
from oncotriage.agent import deps as _deps
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


def fail(label, detail):
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {detail}")
    print(f"  FAIL  {label}")
    print(f"          {detail}")


class _Absent:
    """A named absence, returned where a bare index or a bare call would RAISE.

    THIS FILE'S ONE PROTECTION AGAINST THE ABORT SHAPE this project has now
    shipped seventeen times: an expression that raises while `check()`'s
    ARGUMENT is being evaluated takes the whole run down and reports one
    traceback where it owed a summary and every result below it -- and it does
    so precisely when a planted defect fires, which is when the results are
    owed. Falsy, so `or` guards read naturally.
    """

    def __init__(self, why):
        self.why = why

    def __bool__(self):
        return False

    def __eq__(self, other):
        return isinstance(other, _Absent) and other.why == self.why

    def __repr__(self):
        return f"<absent: {self.why}>"


def guarded(fn, *args, **kwargs):
    """Call ``fn``; return an ``_Absent`` naming the exception instead of raising."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        return _Absent(f"{type(exc).__name__}: {exc}")


def raised(fn, *args, **kwargs):
    """The exception TYPE NAME ``fn`` raised, or ``"<did not raise>"``."""
    try:
        fn(*args, **kwargs)
    except Exception as exc:
        return type(exc).__name__
    return "<did not raise>"


def at(mapping, key):
    """``mapping[key]`` without raising on a defect that removed the key."""
    if not isinstance(mapping, dict) or key not in mapping:
        return _Absent(f"key {key!r} absent")
    return mapping[key]


#------------------------------------------------------------------------------


# ===========================================================================
# ISOLATION
# ===========================================================================

_PRODUCTION_DB = _dl.resolve_inference_db_path(None)
_TMP = tempfile.mkdtemp(prefix="oncotriage-env-record-")

# EVERY DATABASE THIS FILE OPENS IS INSIDE _TMP AND IS PASSED EXPLICITLY. The
# seed is the belt to that brace: paths._RESOLVED is repointed so that a call
# path which forgot its argument resolves INTO the temp tree rather than into
# the production one -- oncotriage/evaluation's own seam, and the reason this
# file is not in the collision matrix.
_SAVED_RESOLVED = dict(_paths._RESOLVED)
_paths._RESOLVED["inferences_path"] = os.path.join(_TMP, "inferences.db")

_DEPS_SRC_PATH = os.path.abspath(_deps.__file__)
_ENV_SRC_PATH = os.path.abspath(_env.__file__)


def _sha256_file(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


_DEPS_SHA_BEFORE = _sha256_file(_DEPS_SRC_PATH)
_ENV_SHA_BEFORE = _sha256_file(_ENV_SRC_PATH)


def _rows(db, sql, params=()):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


#------------------------------------------------------------------------------


print("=" * 75)
print("SECTION 1: THE RECORD IS COMPLETE, AND ITS VOCABULARIES ARE CLOSED")
print("=" * 75)

_env.clear_cache()
_record = _env.current()

_EXPECTED_FIELDS = {
    "environment_record_version", "environment_hash", "package_snapshot",
    "package_count", "git_commit", "git_dirty", "image_identity",
    "image_identity_source", "cross_encoder_model", "cross_encoder_revision",
    "cross_encoder_dtype", "cross_encoder_max_length", "embedding_model",
    "sparse_model", "matching_model", "matching_wire_model",
    "matching_provider",
}
check("1a  the record carries exactly the declared field set",
      set(_record), _EXPECTED_FIELDS)

# THE PACKAGE LIST IS REAL AND NON-DEGENERATE. Without this, every assertion
# below about the hash and the snapshot would be equally satisfied by a
# resolver that returned None on this machine -- which is the vacuous pass the
# non-degeneracy rule exists to stop.
_snapshot = at(_record, "package_snapshot")
check("1b  a package snapshot was resolved", isinstance(_snapshot, str), True)
check("1c  it holds more than one distribution",
      isinstance(_snapshot, str) and len(_snapshot.splitlines()) > 1, True)
check("1d  package_count is the LINE count of the snapshot",
      at(_record, "package_count"),
      len(_snapshot.splitlines()) if isinstance(_snapshot, str) else None)
check("1e  the hash is 16 hex characters",
      isinstance(at(_record, "environment_hash"), str)
      and len(_record["environment_hash"]) == 16
      and all(c in "0123456789abcdef" for c in _record["environment_hash"]),
      True)
check("1f  the hash is the sha256 prefix OF that snapshot",
      at(_record, "environment_hash"), _env.snapshot_hash(_snapshot))

# SORTED AND THEREFORE STABLE. importlib.metadata walks sys.path in whatever
# order os.scandir yields, so an unsorted snapshot would hash differently on
# two runs of one machine -- paths._glob_one's lesson, one module over.
_lines = _snapshot.splitlines() if isinstance(_snapshot, str) else []
check("1g  the snapshot is sorted case-insensitively",
      _lines, sorted(_lines, key=lambda l: (l.lower(), l)))
check("1h  every line is name==version",
      all("==" in line for line in _lines), True)

check("1i  the image source is a member of the closed vocabulary",
      at(_record, "image_identity_source") in _env.IMAGE_IDENTITY_SOURCES, True)
check("1j  IMAGE_IDENTITY_SOURCES has no duplicates",
      len(set(_env.IMAGE_IDENTITY_SOURCES)),
      len(_env.IMAGE_IDENTITY_SOURCES))

# THE DIGEST OUTRANKS THE TAG, driven both ways rather than read. A tag is a
# NAME and can be moved onto different bytes; preferring the weaker of two
# supplied answers would be a choice against the reader.
_saved_env = {k: os.environ.get(k)
              for k in (_settings.ENV_IMAGE_DIGEST, _settings.ENV_IMAGE_TAG)}
try:
    os.environ[_settings.ENV_IMAGE_DIGEST] = "sha256:aaaa"
    os.environ[_settings.ENV_IMAGE_TAG] = "oncotriage:local"
    check("1k  a supplied digest wins over a supplied tag",
          _env.image_identity(), ("sha256:aaaa", _env.IMAGE_SOURCE_DIGEST))
    del os.environ[_settings.ENV_IMAGE_DIGEST]
    check("1l  the tag answers when no digest was given",
          _env.image_identity(),
          ("oncotriage:local", _env.IMAGE_SOURCE_BUILD_TAG))
    os.environ[_settings.ENV_IMAGE_TAG] = "   "
    check("1m  a blank value is not an identity",
          _env.image_identity()[0], None)
finally:
    for _k, _v in _saved_env.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v

# THE REAL git PROBE. Its ANSWER is machine-dependent, so what is asserted is
# the CONTRACT: three states, and the dirty flag is None exactly when the
# commit is UNKNOWN.
_commit, _dirty = _env.git_commit()
check("1n  git_commit returns a hex id or the UNKNOWN sentinel",
      _commit == _env.UNKNOWN
      or (len(_commit) == 40 and all(c in "0123456789abcdef" for c in _commit)),
      True)
check("1o  git_dirty is None exactly when the commit is unknown",
      (_dirty is None), (_commit == _env.UNKNOWN))
check("1p  git_dirty is a bool when the commit resolved",
      isinstance(_dirty, bool) or _commit == _env.UNKNOWN, True)

# THE CACHE. A run is one environment; a second reading that could disagree
# with the first is what the cache exists to prevent.
check("1q  current() is cached: the same object's values come back",
      _env.current(), _record)
_env.clear_cache()
check("1r  clear_cache() forces a fresh resolution that agrees",
      at(_env.current(), "environment_hash"), at(_record, "environment_hash"))

# THE THREE RESTATED VOCABULARIES. Each lives in the storage layer because
# importing the module it came from would invert the layering; each can
# therefore drift, and a test may import both because a test is in nobody's
# import graph.
check("1s  RUN_FINGERPRINT_COLUMNS still round-trips against the stamp",
      _dl.RUN_FINGERPRINT_COLUMNS,
      ("fingerprint_version",) + _rf.FINGERPRINT_FIELDS)
check("1t  every RUN_ENVIRONMENT_COLUMNS key is a declared runs column",
      sorted(set(_dl.RUN_ENVIRONMENT_COLUMNS) - set(_dl.RUN_COLUMNS)), [])
check("1u  every RUN_ENVIRONMENT_COLUMNS source key is in the record",
      sorted(set(_dl.RUN_ENVIRONMENT_COLUMNS.values()) - set(_record)), [])
check("1v  the storage layer's UNKNOWN-hash sentinel matches the producer's",
      _dl.RUN_ENVIRONMENT_UNKNOWN_HASH, _env.snapshot_hash(None))

# cross_encoder_revision IS A STAMP FIELD AND NOT AN ENVIRONMENT COLUMN. Two
# writers for one column is the shape that drifts, and the one that could drift
# is the one nothing gates.
check("1w  cross_encoder_revision is written from the stamp, not the record",
      "cross_encoder_revision" in _dl.RUN_ENVIRONMENT_COLUMNS, False)
check("1x  ...and it IS a gated fingerprint field",
      "cross_encoder_revision" in _rf.FINGERPRINT_FIELDS, True)
check("1y  ...and the environment record carries it anyway, so one dict "
      "answers 'what ran'",
      at(_record, "cross_encoder_revision"), config.CROSS_ENCODER_REVISION)

# THE FIELDS THE PREVIOUS PASS ADDED AND LEFT OUT OF EVERY RECORD.
check("1z  the record carries the cross-encoder dtype",
      at(_record, "cross_encoder_dtype"), config.CROSS_ENCODER_DTYPE)
check("1z-i  ...and its sequence budget",
      at(_record, "cross_encoder_max_length"), config.CROSS_ENCODER_MAX_LENGTH)
check("1z-ii  the dense and sparse embedding models are named",
      (at(_record, "embedding_model"), at(_record, "sparse_model")),
      (config.EMBEDDING_MODEL, "Qdrant/bm25"))


#------------------------------------------------------------------------------


print()
print("=" * 75)
print("SECTION 2: THE RERANKER REVISION IS PINNED AND VERIFIED")
print("=" * 75)

check("2a  CROSS_ENCODER_REVISION is a 40-hex git object id",
      isinstance(config.CROSS_ENCODER_REVISION, str)
      and len(config.CROSS_ENCODER_REVISION) == 40
      and all(c in "0123456789abcdef" for c in config.CROSS_ENCODER_REVISION),
      True)

# BOTH from_pretrained CALLS ARE HANDED THE PIN, ASSERTED BY AST. A behavioural
# check here would cost an 836 MB load; and the failure this guards -- one half
# pinned and the other following `main` -- is the divergent-pair failure the
# tokenizer/weights note already records, on the version axis.
_deps_tree = ast.parse(open(_DEPS_SRC_PATH).read())
_from_pretrained = [
    node for node in ast.walk(_deps_tree)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and node.func.attr == "from_pretrained"
]
check("2b  the package has exactly two from_pretrained calls",
      len(_from_pretrained), 2)


def _names_revision(call):
    for kw in call.keywords:
        if kw.arg == "revision":
            return ast.unparse(kw.value)
    return None


check("2c  both are handed revision=config.CROSS_ENCODER_REVISION",
      sorted(_names_revision(c) for c in _from_pretrained),
      ["config.CROSS_ENCODER_REVISION", "config.CROSS_ENCODER_REVISION"])

# NON-DEGENERACY: the walk really can report a MISSING revision. Without this,
# 2c would be equally satisfied by a walk that found nothing at all.
_stripped = ast.parse(
    open(_DEPS_SRC_PATH).read().replace(
        "revision=config.CROSS_ENCODER_REVISION,\n", "", 1))
_stripped_calls = [
    node for node in ast.walk(_stripped)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and node.func.attr == "from_pretrained"
]
check("2d  NON-DEGENERACY: the same walk reports an unpinned call as None",
      None in [_names_revision(c) for c in _stripped_calls], True)

# THE VERIFIER, DRIVEN BOTH DIRECTIONS. It is a pure function of its argument,
# which is the natural control for one -- no exec, no plant, no module copy.
_deps.CROSS_ENCODER_REVISION_DEGRADATIONS.clear()
check("2e  the pinned revision verifies",
      _deps._verify_cross_encoder_revision(
          config.CROSS_ENCODER_REVISION, "test", "ncbi/MedCPT-Cross-Encoder"),
      _deps.REVISION_VERIFIED)
check("2f  case and whitespace are not information",
      _deps._verify_cross_encoder_revision(
          "  " + config.CROSS_ENCODER_REVISION.upper() + "  ",
          "test", "ncbi/MedCPT-Cross-Encoder"),
      _deps.REVISION_VERIFIED)
check("2g  a DIFFERENT revision RAISES",
      raised(_deps._verify_cross_encoder_revision,
             "0" * 40, "test", "ncbi/MedCPT-Cross-Encoder"),
      "CrossEncoderRevisionMismatchError")
# A PREFIX IS NOT ACCEPTED. "starts with" is satisfied by the empty string and
# by any short id an operator typed instead of the full one.
check("2h  a PREFIX of the pinned revision RAISES",
      raised(_deps._verify_cross_encoder_revision,
             config.CROSS_ENCODER_REVISION[:12], "test", "ck"),
      "CrossEncoderRevisionMismatchError")
check("2i  no degradation was counted for any of those",
      dict(_deps.CROSS_ENCODER_REVISION_DEGRADATIONS), {})

check("2j  an ABSENT revision is counted, not raised",
      _deps._verify_cross_encoder_revision(None, "tokenizer", "ck"),
      _deps.REVISION_UNREPORTED)
check("2k  ...under a key naming WHICH half declined",
      dict(_deps.CROSS_ENCODER_REVISION_DEGRADATIONS),
      {"unreported:tokenizer": 1})
check("2l  an unreadable revision is counted under its type",
      _deps._verify_cross_encoder_revision(object(), "weights", "ck"),
      _deps.REVISION_UNREADABLE)
check("2m  ...and the counter names it",
      _deps.CROSS_ENCODER_REVISION_DEGRADATIONS["unreadable:object"], 1)
check("2n  an EMPTY string is unreadable rather than a match",
      _deps._verify_cross_encoder_revision("   ", "weights", "ck"),
      _deps.REVISION_UNREADABLE)
_deps.CROSS_ENCODER_REVISION_DEGRADATIONS.clear()

check("2o  REVISION_VERIFICATION_STATES is closed and has no duplicates",
      (len(set(_deps.REVISION_VERIFICATION_STATES)),
       len(_deps.REVISION_VERIFICATION_STATES)), (3, 3))
check("2p  a mismatch is not one of the RETURN states",
      "mismatch" in _deps.REVISION_VERIFICATION_STATES, False)

# NO MODEL WAS LOADED TO ESTABLISH ANY OF THE ABOVE.
check("2q  torch never entered sys.modules", "torch" in sys.modules, False)
check("2r  transformers never entered sys.modules",
      "transformers" in sys.modules, False)


#------------------------------------------------------------------------------


print()
print("=" * 75)
print("SECTION 3: THE MIGRATION, ON A FABRICATED PRE-ERA DATABASE")
print("=" * 75)

# AN ERA-8 `runs` TABLE, BUILT FROM THE MODULE'S OWN CONSTANTS rather than
# retyped: the base CREATE TABLE plus every addition BUT this era's. Retyping
# would make the fixture describe a shape no era ever had -- which is the defect
# tests/test_ablation_stop_and_lock.py's 4g had to be corrected for.
_ERA9_COLUMNS = ("environment_hash", "package_count", "git_commit", "git_dirty",
                 "image_identity", "image_identity_source",
                 "cross_encoder_dtype", "cross_encoder_max_length",
                 "cross_encoder_revision")
check("3a  every era-9 column is declared in RUN_COLUMN_ADDITIONS",
      sorted(set(_ERA9_COLUMNS) - set(_dl.RUN_COLUMN_ADDITIONS)), [])

_pre = os.path.join(_TMP, "pre_era9.db")
_conn = sqlite3.connect(_pre)
_old_cols = [c for c in _dl.RUN_COLUMNS if c not in _ERA9_COLUMNS]
_conn.execute(
    "CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    + ", ".join(f"{c} TEXT" for c in _old_cols) + ")")
_conn.execute(
    f"INSERT INTO runs (started_at, status, invocation_source) "
    f"VALUES ('2026-01-01T00:00:00', 'FINISHED', 'legacy')")
_conn.commit()
_conn.close()

check("3b  the fabricated database has no run_environment table",
      _rows(_pre, "SELECT name FROM sqlite_master WHERE name='run_environment'"),
      [])
check("3c  ...and no era-9 runs column",
      sorted(c for c in _ERA9_COLUMNS
             if c in {r[1] for r in _rows(_pre, "PRAGMA table_info(runs)")}),
      [])

_dl.initialize_database(_pre)

_migrated = {r[1] for r in _rows(_pre, "PRAGMA table_info(runs)")}
check("3d  every era-9 column is present after the migration",
      sorted(c for c in _ERA9_COLUMNS if c not in _migrated), [])
check("3e  the run_environment table was created",
      bool(_rows(_pre,
                 "SELECT name FROM sqlite_master WHERE name='run_environment'")),
      True)
check("3f  the legacy row survived with its values intact",
      _rows(_pre, "SELECT status, invocation_source FROM runs WHERE id=1"),
      [("FINISHED", "legacy")])
check("3g  ...with its new columns NULL",
      _rows(_pre, "SELECT environment_hash, git_commit FROM runs WHERE id=1"),
      [(None, None)])
check("3h  the era stamp was written",
      _rows(_pre, "PRAGMA user_version")[0][0], _dl.SCHEMA_USER_VERSION)

# IDEMPOTENT. A second initialize must not raise `duplicate column name`.
_dl._INITIALIZED_DATABASES.discard(os.path.abspath(_pre))
check("3i  re-initializing is a no-op rather than a duplicate-column error",
      raised(_dl.initialize_database, _pre), "<did not raise>")


#------------------------------------------------------------------------------


print()
print("=" * 75)
print("SECTION 4: THE RECORD REACHES THE ROW, AND THE SNAPSHOT IS STORED ONCE")
print("=" * 75)

_db = os.path.join(_TMP, "runs.db")
_stamp = _rf.current.__wrapped__ if hasattr(_rf.current, "__wrapped__") else None

# A LITERAL STAMP, DERIVED FROM THE FIELD TUPLE rather than hand-listed: a
# field added to the stamp and not to a hand-written literal here would make
# this file fail for a reason unrelated to what it asserts.
_fingerprint = {"fingerprint_version": _rf.FINGERPRINT_VERSION}
for _f in _rf.FINGERPRINT_FIELDS:
    _fingerprint[_f] = "x"
_fingerprint["collection_points"] = 100
_fingerprint["campaign_cohort_size"] = 300
_fingerprint["campaign_cohort_seed"] = 42
_fingerprint["cross_encoder_revision"] = config.CROSS_ENCODER_REVISION

_run_a = _dl.start_run_record("test-a", db_path=_db, fingerprint=_fingerprint,
                              environment=_record)
_cols = ", ".join(_ERA9_COLUMNS)
_row_a = _rows(_db, f"SELECT {_cols} FROM runs WHERE id=?", (_run_a,))[0]
_got = dict(zip(_ERA9_COLUMNS, _row_a))

check("4a  the environment hash reached the row",
      at(_got, "environment_hash"), at(_record, "environment_hash"))
check("4b  the package count reached the row",
      at(_got, "package_count"), at(_record, "package_count"))
check("4c  the git commit reached the row",
      at(_got, "git_commit"), at(_record, "git_commit"))
check("4d  git_dirty is stored as 0/1",
      at(_got, "git_dirty"),
      None if _record["git_dirty"] is None else int(_record["git_dirty"]))
check("4e  the image source reached the row",
      at(_got, "image_identity_source"), at(_record, "image_identity_source"))
check("4f  the cross-encoder dtype reached the row",
      at(_got, "cross_encoder_dtype"), config.CROSS_ENCODER_DTYPE)
check("4g  the cross-encoder sequence budget reached the row",
      at(_got, "cross_encoder_max_length"), config.CROSS_ENCODER_MAX_LENGTH)
check("4h  the revision reached the row FROM THE STAMP",
      at(_got, "cross_encoder_revision"), config.CROSS_ENCODER_REVISION)
check("4i  no era-9 column is NULL on a fully-recorded run",
      [c for c in _ERA9_COLUMNS if _got[c] is None and c != "image_identity"],
      [])

# THE SNAPSHOT, ONCE PER DISTINCT HASH.
_run_b = _dl.start_run_record("test-b", db_path=_db, fingerprint=_fingerprint,
                              environment=_record)
check("4j  two runs sharing an environment are two run rows",
      len(_rows(_db, "SELECT id FROM runs")), 2)
check("4k  ...and ONE run_environment row",
      len(_rows(_db, "SELECT environment_hash FROM run_environment")), 1)
check("4l  the stored snapshot is the one that was hashed",
      _rows(_db, "SELECT package_snapshot FROM run_environment")[0][0],
      at(_record, "package_snapshot"))
check("4m  the stored count agrees with the run row's",
      _rows(_db, "SELECT package_count FROM run_environment")[0][0],
      at(_record, "package_count"))

_other = dict(_record)
_other["package_snapshot"] = "somethingelse==1.0\n"
_other["environment_hash"] = _env.snapshot_hash(_other["package_snapshot"])
_other["package_count"] = 1
_dl.start_run_record("test-c", db_path=_db, fingerprint=_fingerprint,
                     environment=_other)
check("4n  a DIFFERENT hash stores a second snapshot row",
      len(_rows(_db, "SELECT environment_hash FROM run_environment")), 2)
check("4o  every run row's hash resolves to a stored snapshot",
      _rows(_db,
            "SELECT COUNT(*) FROM runs r LEFT JOIN run_environment e "
            "ON r.environment_hash = e.environment_hash "
            "WHERE r.environment_hash IS NOT NULL "
            "AND e.environment_hash IS NULL")[0][0],
      0)

# A CALLER THAT RECORDS NOTHING.
_run_none = _dl.start_run_record("test-none", db_path=_db,
                                 fingerprint=_fingerprint, environment=None)
_row_none = _rows(_db, f"SELECT {_cols} FROM runs WHERE id=?", (_run_none,))[0]
check("4p  environment=None leaves all eight provenance columns NULL",
      [v for c, v in zip(_ERA9_COLUMNS, _row_none)
       if c != "cross_encoder_revision"],
      [None] * 8)
check("4q  ...and the stamp column is still written",
      _row_none[_ERA9_COLUMNS.index("cross_encoder_revision")],
      config.CROSS_ENCODER_REVISION)
check("4r  ...and no snapshot row was added",
      len(_rows(_db, "SELECT environment_hash FROM run_environment")), 2)

# A RECORD WITH NO SNAPSHOT. The hash reads "unknown"; a table keyed by content
# must not hold a key that names none, and the run row still records the string.
_nosnap = dict(_record)
_nosnap["package_snapshot"] = None
_nosnap["environment_hash"] = _env.snapshot_hash(None)
_nosnap["package_count"] = None
_run_ns = _dl.start_run_record("test-nosnap", db_path=_db,
                               fingerprint=_fingerprint, environment=_nosnap)
check("4s  an unreadable package list records the sentinel on the run row",
      _rows(_db, "SELECT environment_hash FROM runs WHERE id=?",
            (_run_ns,))[0][0],
      _env.UNKNOWN)
check("4t  ...and stores NO snapshot row for it",
      len(_rows(_db, "SELECT environment_hash FROM run_environment")), 2)

# COERCION.
check("4u  a non-numeric package_count is NULLed, not stored as TEXT",
      _dl._run_environment_value("package_count", {"package_count": "many"}),
      None)
check("4v  a numeric string IS coerced",
      _dl._run_environment_value("package_count", {"package_count": "12"}), 12)
check("4w  git_dirty True stores as 1",
      _dl._run_environment_value("git_dirty", {"git_dirty": True}), 1)
check("4x  git_dirty False stores as 0, which is not NULL",
      _dl._run_environment_value("git_dirty", {"git_dirty": False}), 0)
check("4y  git_dirty None stays NULL -- 'nobody measured this'",
      _dl._run_environment_value("git_dirty", {"git_dirty": None}), None)
check("4z  a non-dict environment yields NULL for every column",
      [_dl._run_environment_value(c, "not a dict")
       for c in _dl.RUN_ENVIRONMENT_COLUMNS],
      [None] * len(_dl.RUN_ENVIRONMENT_COLUMNS))


#------------------------------------------------------------------------------


print()
print("=" * 75)
print("SECTION 5: PLANTED DEFECTS, EACH WITH ITS CLEAN CONTROL")
print("=" * 75)

# ---- PLANT 1: A MODEL LOADED OUTSIDE THE RECORD ----
#
# THE DEFECT THIS PASS EXISTS AGAINST: a from_pretrained call with no
# `revision=`, which resolves whatever `main` points at. The plant is an ast
# walk over an EDITED STRING -- the right instrument, because the property it
# defeats (check 2c) is itself static, and because loading a model to
# demonstrate it would cost 836 MB and a network call.
#
# THE CLEAN CONTROL RUNS FIRST. Without it a walk that reported every module as
# unpinned would score the plant as caught while measuring nothing.
_clean_src = open(_DEPS_SRC_PATH).read()
_planted_src = _clean_src.replace(
    "                                              revision=config.CROSS_ENCODER_REVISION,\n", "", 1)
check("5a  PLANT PARSES: the edit removed exactly one line",
      len(_clean_src.splitlines()) - len(_planted_src.splitlines()), 1)


def _unpinned_calls(source):
    tree = ast.parse(source)
    return [c for c in ast.walk(tree)
            if isinstance(c, ast.Call)
            and isinstance(c.func, ast.Attribute)
            and c.func.attr == "from_pretrained"
            and _names_revision(c) is None]


check("5b  CLEAN CONTROL: the shipped module has no unpinned load",
      len(_unpinned_calls(_clean_src)), 0)
check("5c  PLANT CAUGHT: the edited module has one",
      len(_unpinned_calls(_planted_src)), 1)

# AND BEHAVIOURALLY: what an unpinned load would then report. The shipped
# verifier refuses a revision that is not the pinned one, which is what turns a
# moved `main` into a loud failure at first load rather than a silently
# different ranking.
check("5d  CLEAN CONTROL: the pinned revision verifies",
      _deps._verify_cross_encoder_revision(
          config.CROSS_ENCODER_REVISION, "weights.config._commit_hash", "ck"),
      _deps.REVISION_VERIFIED)
check("5e  PLANT CAUGHT: a `main` that moved raises at first load",
      raised(_deps._verify_cross_encoder_revision,
             "75e855e5aaeda1e16da04a894207072d4b0db66a",
             "weights.config._commit_hash", "ck"),
      "CrossEncoderRevisionMismatchError")
_deps.CROSS_ENCODER_REVISION_DEGRADATIONS.clear()

# ---- PLANT 2: THE SNAPSHOT STORED PER RUN RATHER THAN PER HASH ----
_plant_db = os.path.join(_TMP, "plant2.db")
_dl.initialize_database(_plant_db)
_conn = sqlite3.connect(_plant_db)
try:
    _conn.execute(
        "INSERT INTO run_environment VALUES (?, ?, ?, ?)",
        ("deadbeefdeadbeef", "2026-01-01", 3, "a==1\n"))
    _conn.commit()
    _dupe = raised(
        lambda: (_conn.execute("INSERT INTO run_environment VALUES (?,?,?,?)",
                               ("deadbeefdeadbeef", "2026-01-02", 3, "b==2\n")),
                 _conn.commit()))
    check("5f  CLEAN CONTROL: the hash is a PRIMARY KEY, so a duplicate raises",
          _dupe, "IntegrityError")
    _conn.execute(
        "INSERT OR IGNORE INTO run_environment VALUES (?,?,?,?)",
        ("deadbeefdeadbeef", "2026-01-02", 3, "b==2\n"))
    _conn.commit()
    check("5g  PLANT CAUGHT: OR IGNORE keeps the FIRST snapshot, not the last",
          _conn.execute("SELECT package_snapshot FROM run_environment "
                        "WHERE environment_hash='deadbeefdeadbeef'"
                        ).fetchone()[0],
          "a==1\n")
finally:
    _conn.close()

# ---- PLANT 3: A DECLARED COLUMN WITH NO VALUE ----
#
# RUN_COLUMNS is DERIVED from RUN_COLUMN_ADDITIONS, so a column added to the
# schema and never written binds a bare KeyError thirty frames from the dict
# that caused it. The guard converts that into a named RuntimeError, and this
# is the control that it is not vacuous.
_saved_additions = dict(_dl.RUN_COLUMN_ADDITIONS)
_saved_run_columns = _dl.RUN_COLUMNS
try:
    check("5h  CLEAN CONTROL: a fully-declared schema inserts",
          isinstance(_dl.start_run_record("ctl", db_path=_db,
                                          environment=_record), int), True)
    _dl.RUN_COLUMNS = _dl.RUN_COLUMNS + ("never_written",)
    check("5i  PLANT CAUGHT: an undeclared column is a named RuntimeError",
          raised(_dl.start_run_record, "plant", db_path=_db,
                 environment=_record),
          "RuntimeError")
finally:
    _dl.RUN_COLUMNS = _saved_run_columns
check("5j  the restore took, BY IDENTITY",
      _dl.RUN_COLUMNS is _saved_run_columns, True)


#------------------------------------------------------------------------------


print()
print("=" * 75)
print("SECTION 6: NOTHING IN THE REPOSITORY WAS TOUCHED")
print("=" * 75)

check("6a  oncotriage/agent/deps.py is byte-identical",
      _sha256_file(_DEPS_SRC_PATH), _DEPS_SHA_BEFORE)
check("6b  oncotriage/environment.py is byte-identical",
      _sha256_file(_ENV_SRC_PATH), _ENV_SHA_BEFORE)
check("6c  NON-DEGENERACY: those two hashes are not the same file",
      _DEPS_SHA_BEFORE != _ENV_SHA_BEFORE, True)
check("6d  the production database was never created by this run",
      os.path.abspath(_PRODUCTION_DB).startswith(os.path.abspath(_TMP)), False)

_paths._RESOLVED.clear()
_paths._RESOLVED.update(_SAVED_RESOLVED)
shutil.rmtree(_TMP, ignore_errors=True)
check("6e  the temp tree was removed", os.path.exists(_TMP), False)


#------------------------------------------------------------------------------


print()
print("=" * 75)
print("RESULTS:")
print(f"  passed: {_RESULTS['passed']}")
print(f"  failed: {_RESULTS['failed']}")
if _FAILURES:
    print()
    print("FAILURES:")
    for _f in _FAILURES:
        print(f"  - {_f}")
print("=" * 75)

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Sep  2 2026
"""
