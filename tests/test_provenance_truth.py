######################################################################
# Provenance truth: the model that answered, the cost with the cache,
# the quality gate's split, and the harness's own instruments
######################################################################

"""Provenance Truth Test

FOUR MEASUREMENTS THAT WERE TAKEN AND THEN LOST, AND ONE THAT WAS WRONG.

  1. WHICH MODEL PRODUCED A RECORD. ``oncotriage/evaluation/run_harness.py``
     stamped ``matching_model_configured: MATCHING_MODEL`` -- the OpenAI arm's
     priced identity, which does not move when ``MATCHING_PROVIDER`` does.
     Measured over the 240 records on disk when this file was written: 30 were
     answered by ``us.anthropic.claude-sonnet-4-6`` and every one of them named
     ``gpt-5.6-terra`` as the model it was configured for. The banner an
     operator reads before authorising a paid run said the same thing.

  2. WHAT THE PROMPT CACHE COST OR SAVED. ``get_model_cost()`` takes an
     {input, output} pair, so a cached read priced at the full input rate -- the
     A13 gap the Converse adapter records. It is closed ADDITIVELY: a SECOND
     figure from ``get_model_cost_cached()``, a SECOND column beside
     ``estimated_cost_usd``, and neither the old function nor the old column
     touched.

  3. WHICH KNOB OF STAGE 4's TWO-KNOB QUALITY GATE DROPPED WHICH TRIAL.
     ``oncotriage/agent/filtering.py`` has computed the split since that gate
     shipped and ``TrialMatchState`` has declared all three channels;
     ``_pipeline_provenance`` dropped every one of them at the terminal
     boundary.

  4. THE RUN'S OWN HEALTH RECORD. The harness drives all six stages, so every
     counter in ``oncotriage/degradation.py`` moves while it runs, and it
     printed none of them.

  5. AND THE ONE THAT WAS WRONG. ``tests/test_compose_shutdown_grace.py``
     derived the per-trial shutdown drain from
     ``MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS`` (4) where the owner is
     ``config.per_trial_parallel_bound()`` (2 on the shipped arm), so the check
     and the compose prose it pins were BOTH computed from the same wrong
     constant and agreed with each other. That is covered in that file; what is
     covered here is that the owner is now also in the tracking index.

WHAT THIS FILE COSTS TO RUN: nothing. No network, no keys, NO SPEND -- no
provider client of any kind is built and no request is issued. NO MODEL LOAD
(``ONCOTRIAGE_DEFER_LOCAL_MODELS`` is set above the imports and section 9
asserts torch and transformers never entered ``sys.modules``). No live Qdrant,
no corpus, no git history, no live server. Every database is a scratch file
inside a ``tempfile.mkdtemp`` this file removes and asserts gone, and
``paths._RESOLVED`` is seeded so nothing can resolve to the production tree.

IT EXECS NOTHING AND LOADS NO MODULE BY LOCATION. Every control is a different
INPUT to a pure function, a real failing condition built on disk, a module
attribute rebound inside ``try``/``finally`` with the restore asserted, or an
``ast`` walk over a source file it only READS. So it needs no
``_EXEC_ALLOWLIST`` entry.

NOT IN THE COLLISION MATRIX, derived rather than assumed: it writes nothing in
the repository, and of the six files it reads only ``oncotriage/config.py`` is
touched by either of the suite's two writers -- so all six are sha256-compared
at the end and an interleaved serial run is visible rather than silent.

Run:
    python tests/test_provenance_truth.py
"""

import ast
import hashlib
import inspect
import io
import os
import shutil
import sqlite3
import sys
import tempfile

# ABOVE THE PACKAGE IMPORTS ON PURPOSE. oncotriage/agent/deps.py reads this
# variable ONCE, at its own import, and `deps` arrives transitively on the
# first `oncotriage` import -- so an assignment underneath the imports reaches
# nothing and the local models load for real.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

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

from oncotriage import config
from oncotriage import degradation
from oncotriage import paths
from oncotriage import tracking
from oncotriage.agent import terminal as _terminal
from oncotriage.storage import database_logger as _dbl
from oncotriage.storage import queries as _queries
from oncotriage.utils import (
    UnknownCachePricingError,
    UnknownModelPricingError,
    get_model_cost,
    get_model_cost_cached,
)


# ===========================================================================
# HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label: str, actual, expected) -> None:
    """Assert equality, record the outcome, NEVER abort the run."""
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


def check_true(label: str, condition) -> None:
    check(label, bool(condition), True)


def section(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def raises(fn, *args, **kwargs):
    """Call `fn` and return its exception TYPE NAME, or a marker.

    EVERY DRIVE INTO PRODUCTION CODE GOES THROUGH THIS OR THROUGH `drive`.
    A bare call inside a `check(...)` argument list raises while the argument
    is being EVALUATED, so the file reports one traceback where it owes a
    summary and every result below it -- the abort shape this project has
    shipped often enough to have a name for.
    """
    try:
        fn(*args, **kwargs)
    except BaseException as exc:                       # noqa: BLE001 -- reported
        return type(exc).__name__
    return "<did not raise>"


def drive(fn, *args, **kwargs):
    """Call `fn` and return its value, or a marker string on any exception."""
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                       # noqa: BLE001 -- reported
        return f"<RAISED {type(exc).__name__}: {exc}>"


def rounded(value, places=6):
    """`round(value, places)`, or a NAMED ABSENCE. NEVER raises.

    THIS HELPER EXISTS BECAUSE THIS FILE SHIPPED THE ABORT SHAPE ONCE. Check
    4e read `round(_rows["CACHED"][1], 6)` directly, and the revert that stops
    the cached-cost column being written -- the defect 4e is FOR -- makes that
    value None. `round(None, 6)` raises while `check`'s argument list is being
    evaluated, so the run reported one traceback where it owed a summary and
    every result below it, and the revert matrix scored the revert as MISSED.
    A check that cannot survive the defect it tests is not a check.
    """
    try:
        return round(value, places)
    except BaseException as exc:                       # noqa: BLE001 -- reported
        return f"<NOT A NUMBER: {value!r} ({type(exc).__name__})>"


def at(mapping, key, default="<ABSENT>"):
    """`mapping[key]` without raising on an absence a defect creates."""
    try:
        return mapping[key]
    except BaseException:                              # noqa: BLE001 -- reported
        return default


def lt(a, b):
    """`a < b`, or False when either side is a marker rather than a number."""
    try:
        return a < b
    except BaseException:                              # noqa: BLE001 -- reported
        return False


def sha256_file(path: str) -> str:
    with io.open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def repo(*parts) -> str:
    return os.path.join(_REPO, *parts)


# THE SIX FILES THIS TEST READS, HASHED BEFORE ANYTHING RUNS. Only config.py is
# written by either of the suite's two source-rewriting tests, but all six are
# compared: a hash of one file is a statement about one file, and the cheap
# thing is to make the statement about every file the conclusions rest on.
_WATCHED = (
    repo("oncotriage", "config.py"),
    repo("oncotriage", "utils.py"),
    repo("oncotriage", "agent", "evaluation.py"),
    repo("oncotriage", "agent", "terminal.py"),
    repo("oncotriage", "storage", "database_logger.py"),
    repo("oncotriage", "evaluation", "run_harness.py"),
)
_HASHES_BEFORE = {p: sha256_file(p) for p in _WATCHED}

_TMP = tempfile.mkdtemp(prefix="oncotriage-provenance-truth-")


# ===========================================================================
# 1. THE MODEL THAT ANSWERED
# ===========================================================================

section("1. run_harness stamps the model that ANSWERED, never the dormant "
        "arm's constant")

_HARNESS_PATH = repo("oncotriage", "evaluation", "run_harness.py")
_HARNESS_SRC = io.open(_HARNESS_PATH, encoding="utf-8").read()
_HARNESS_TREE = ast.parse(_HARNESS_SRC)


def name_loads(tree) -> set:
    """Every bare NAME this tree LOADS, docstrings excluded by construction.

    ATTRIBUTE AND STRING FORMS ARE DELIBERATELY NOT COUNTED. A docstring or a
    comment naming `MATCHING_MODEL` is prose ABOUT the defect -- this file's own
    module docstring does it twice -- and a substring scan would report the
    argument as the thing it argues against. That is a mistake this project has
    made three times; walking Name loads cannot make it.
    """
    return {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


_HARNESS_LOADS = name_loads(_HARNESS_TREE)

check("1a  run_harness LOADS `matching_wire_model`, the one owner of what "
      "this build would send", "matching_wire_model" in _HARNESS_LOADS, True)
check("1b  ...and LOADS `MATCHING_MODEL` nowhere -- every remaining mention "
      "is prose about the defect, which a Name walk cannot see",
      "MATCHING_MODEL" in _HARNESS_LOADS, False)
check("1c  ...and the name is not imported either, so it cannot come back by "
      "an attribute read",
      [a.name for n in ast.walk(_HARNESS_TREE)
       if isinstance(n, ast.ImportFrom) for a in n.names
       if a.name == "MATCHING_MODEL"], [])
# NON-DEGENERACY. Without this, 1b and 1c pass for a file that imports nothing
# at all -- including a file somebody emptied.
check("1d  NON-DEGENERACY: the walk really does see this module's names",
      ("PROMPT_VERSION" in _HARNESS_LOADS
       and "COLLECTION_NAME" in _HARNESS_LOADS), True)

# --- the record's four model fields, driven both ways ---------------------
from oncotriage.evaluation import run_harness as _rh    # noqa: E402

_WIRE = config.matching_wire_model()

check("1e  the source vocabulary is closed and has exactly two members",
      sorted(_rh.MODEL_SOURCES),
      sorted([_rh.MODEL_SOURCE_ECHO, _rh.MODEL_SOURCE_CONFIGURED]))


def record_models(answered):
    """The four model fields build_record produces for a given echo.

    Reproduces build_record's OWN four lines rather than calling it: that
    function needs a graph state, a selection entry and a parsed patient, and
    what is under test here is the derivation. Section 1h pins that the
    derivation in the source IS this one, so the reproduction cannot drift.
    """
    configured = config.matching_wire_model()
    source = (_rh.MODEL_SOURCE_ECHO if answered
              else _rh.MODEL_SOURCE_CONFIGURED)
    return {"matching_model": answered,
            "matching_model_configured": configured,
            "matching_model_effective": answered or configured,
            "matching_model_source": source}


_echoed = record_models("us.anthropic.claude-sonnet-4-6")
_silent = record_models(None)

check("1f  an ECHOED model is preferred and the source says so",
      (_echoed["matching_model_effective"], _echoed["matching_model_source"]),
      ("us.anthropic.claude-sonnet-4-6", _rh.MODEL_SOURCE_ECHO))
check("1g  with NO echo the effective id falls back to the WIRE model -- not "
      "to MATCHING_MODEL -- and the source says it was not observed",
      (_silent["matching_model_effective"], _silent["matching_model_source"]),
      (_WIRE, _rh.MODEL_SOURCE_CONFIGURED))
check("1h  ...and `matching_model` itself stays NULL on that path, because "
      "'nothing answered' is a measurement and must not be overwritten by an "
      "attestation nobody made", _silent["matching_model"], None)

# THE DERIVATION IN THE SOURCE IS THE ONE REPRODUCED ABOVE. Without this, the
# four checks above test this file's own arithmetic.
_BUILD_RECORD_SRC = None
for _n in ast.walk(_HARNESS_TREE):
    if isinstance(_n, ast.FunctionDef) and _n.name == "build_record":
        _BUILD_RECORD_SRC = ast.unparse(_n)
check("1i  build_record was found (non-degeneracy for 1j-1k)",
      _BUILD_RECORD_SRC is not None, True)
check("1j  build_record resolves the configured id through the OWNER "
      "function, once",
      (_BUILD_RECORD_SRC or "").count("matching_wire_model()"), 1)
check("1k  ...and the effective id is the echo `or` the configured one, which "
      "is the derivation reproduced above",
      "_effective_model = _echo_model or _configured_model"
      in (_BUILD_RECORD_SRC or ""), True)

# --- the banner -----------------------------------------------------------
_MAIN_SRC = None
for _n in ast.walk(_HARNESS_TREE):
    if isinstance(_n, ast.FunctionDef) and _n.name == "main":
        _MAIN_SRC = ast.unparse(_n)
check("1l  main() was found (non-degeneracy for 1m)", _MAIN_SRC is not None,
      True)
check("1m  the banner an operator reads before authorising a paid run names "
      "the WIRE model", "Model configured : {matching_wire_model()}"
      in (_MAIN_SRC or ""), True)

# --- and the wire model really does move with the provider ----------------
_saved_provider = config.MATCHING_PROVIDER
try:
    config.MATCHING_PROVIDER = config.MATCHING_PROVIDER_OPENAI
    _wire_openai = config.matching_wire_model()
    config.MATCHING_PROVIDER = config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC
    _wire_converse = config.matching_wire_model()
finally:
    config.MATCHING_PROVIDER = _saved_provider
check("1n  NON-DEGENERACY: the two arms name DIFFERENT models, so 1g is not "
      "one value compared with itself -- this is the whole defect",
      _wire_openai != _wire_converse, True)
check("1o  ...and MATCHING_MODEL is the SAME on both sides of that, which is "
      "why reading it produced a record naming a judge that never served it",
      _wire_openai == config.MATCHING_MODEL
      and _wire_converse != config.MATCHING_MODEL, True)
check("1p  ...and the provider was restored",
      config.MATCHING_PROVIDER, _saved_provider)


# ===========================================================================
# 2. THE CACHE-AWARE COST FUNCTION
# ===========================================================================

section("2. get_model_cost_cached prices the tiers; get_model_cost is UNTOUCHED")

_M = "us.anthropic.claude-sonnet-4-6"
_ROW = config.PRICING_CONFIG["models"][_M]

# HAND-COMPUTED, NOT RE-DERIVED FROM THE ROW. A check that recomputes the
# function's own arithmetic from the same table agrees with it by construction
# and cannot fail; these numbers were worked out on paper from the four rates.
#
#   input total 100,000 of which 90,000 read and 5,000 written -> 5,000 at full
#   uncached 5,000 x 3.30/1M  = 0.016500
#   output   2,000  x 16.50/1M = 0.033000
#   read     90,000 x 0.33/1M  = 0.029700
#   write    5,000  x 4.125/1M = 0.020625
#                               ----------
#                                0.099825
check("2a  the four tiers are summed as hand-computed",
      rounded(drive(get_model_cost_cached, _M, 100_000, 2_000, 90_000, 5_000,
                    "5m")), 0.099825)
# flat: 100,000 x 3.30/1M + 2,000 x 16.50/1M = 0.330000 + 0.033000
check("2b  ...and the FLAT figure beside it is unchanged and is the old one",
      rounded(drive(get_model_cost, _M, 100_000, 2_000)), 0.363)

check("2c  a run reporting NO cached tokens prices identically to the flat "
      "figure -- which is the honest answer, not a defect",
      drive(get_model_cost_cached, _M, 100_000, 2_000),
      drive(get_model_cost, _M, 100_000, 2_000))

# --- the A13 direction, BOTH ways -----------------------------------------
#
# THE BRIEF FOR THIS WORK SAID "cached < flat ALWAYS" AND THAT IS FALSE, which
# is why both directions are pinned. A cache READ bills at a tenth of input so
# it is a discount; a cache WRITE bills at 1.25x (5m) so it is a PREMIUM. The
# net is a large saving only because reads outnumber writes on a healthy
# per-trial patient.
_read_only = drive(get_model_cost_cached, _M, 100_000, 2_000, 90_000, 0)
_write_only = drive(get_model_cost_cached, _M, 100_000, 2_000, 0, 90_000, "5m")
_flat = drive(get_model_cost, _M, 100_000, 2_000)
check("2d  a read-heavy run prices BELOW the flat figure (the discount)",
      lt(_read_only, _flat), True)
check("2e  a write-only run prices ABOVE it (the premium) -- so 'the flat "
      "figure is always an over-estimate' is FALSE and the column's note says "
      "so", lt(_flat, _write_only), True)
check("2f  NON-DEGENERACY: the two arms really differ from each other",
      _read_only != _write_only, True)
check("2g  the 1h write rate is dearer than the 5m one, so the TTL is not a "
      "label on one number",
      lt(_write_only,
         drive(get_model_cost_cached, _M, 100_000, 2_000, 0, 90_000, "1h")),
      True)

# --- the refusals ---------------------------------------------------------
check("2h  an unpriced MODEL raises the same error get_model_cost raises",
      raises(get_model_cost_cached, "no-such-model", 10, 10),
      "UnknownModelPricingError")
check("2i  cached tokens with no cache_read rate RAISE rather than silently "
      "pricing them at the input rate, which is what the flat figure already "
      "is", raises(get_model_cost_cached, "gpt-4o-2024-08-06", 10, 10, 5, 0),
      "UnknownCachePricingError")
check("2j  a TTL the row does not price RAISES -- the two rates differ by 60% "
      "and are NOT interchangeable",
      raises(get_model_cost_cached, _M, 100, 10, 0, 50, "9h"),
      "UnknownCachePricingError")
check("2k  ...and so does a write with no TTL at all",
      raises(get_model_cost_cached, _M, 100, 10, 0, 50, None),
      "UnknownCachePricingError")
check("2l  a cached share LARGER than the input total it is a subset of is a "
      "contract violation and is not clamped",
      raises(get_model_cost_cached, _M, 100, 10, 90, 90, "5m"), "ValueError")
check("2m  a negative count is refused", raises(get_model_cost_cached, _M,
      100, 10, -1, 0), "ValueError")
check("2n  a bool is not an int here, on this project's standing footing",
      raises(get_model_cost_cached, _M, 100, 10, True, 0), "ValueError")
check("2o  ...but a model with no cache rates and NO cached tokens prices "
      "fine, so an arm that never caches is not broken by this",
      rounded(drive(get_model_cost_cached, "gpt-4o-2024-08-06", 1_000_000, 0)),
      2.5)

# --- get_model_cost itself -------------------------------------------------
_GMC = inspect.signature(get_model_cost).parameters
check("2p  get_model_cost's signature is UNTOUCHED -- 29 call sites and one "
      "column depend on it meaning what it has always meant",
      list(_GMC), ["model_name", "input_tokens", "output_tokens"])
_UTILS_TREE = ast.parse(io.open(repo("oncotriage", "utils.py"),
                                encoding="utf-8").read())
_GMC_SRC = None
for _n in ast.walk(_UTILS_TREE):
    if isinstance(_n, ast.FunctionDef) and _n.name == "get_model_cost":
        _GMC_SRC = ast.unparse(_n)
check("2q  ...and its body reads no cache key at all",
      any(k in (_GMC_SRC or "") for k in ("cache_read", "cache_write")), False)


# ===========================================================================
# 3. THE PRICING ROWS
# ===========================================================================

section("3. PRICING_CONFIG's cache rates")

_CONVERSE_ROWS = [k for k in config.PRICING_CONFIG["models"]
                  if "claude-sonnet-4-6" in k]
check("3a  every Converse row carries both cache keys",
      sorted(k for k in _CONVERSE_ROWS
             if "cache_read" not in config.PRICING_CONFIG["models"][k]
             or "cache_write" not in config.PRICING_CONFIG["models"][k]), [])
check("3b  NON-DEGENERACY: there is more than one such row",
      len(_CONVERSE_ROWS) >= 6, True)

# THE MEASURED ROW'S MULTIPLIERS ARE WHAT THE INFERRED ROWS APPLY, so a
# correction to the geo premium moves a row's five numbers together and cannot
# leave its cache rates describing a base nobody uses.
_G = config.PRICING_CONFIG["models"]["global.anthropic.claude-sonnet-4-6"]
def multipliers(row):
    """(read, write5m, write1h) as multiples of a row's own input rate.

    Every read goes through `at`: a revert that DELETES a rate must produce a
    recorded failure here, not a KeyError that takes the section with it.
    """
    base = at(row, "input", 0) or 0
    if not base:
        return "<NO INPUT RATE>"
    write = at(row, "cache_write", {})
    return (rounded(at(row, "cache_read", 0) / base, 4),
            rounded(at(write, "5m", 0) / base, 4),
            rounded(at(write, "1h", 0) / base, 4))


check("3c  the MEASURED row's rates are 0.10x / 1.25x / 2.00x its own input",
      multipliers(_G), (0.10, 1.25, 2.00))
_bad = [k for k in _CONVERSE_ROWS
        if multipliers(config.PRICING_CONFIG["models"][k])
        != (0.10, 1.25, 2.00)]
check("3d  every INFERRED row applies the same multipliers to its own base",
      _bad, [])
check("3e  the shipped TTL is one the shipped model prices",
      config.BEDROCK_ANTHROPIC_CACHE_TTL in at(
          at(config.PRICING_CONFIG["models"],
             config.matching_wire_model(), {}), "cache_write", {}), True)


# ===========================================================================
# 4. THE COLUMNS, THE ERA, AND THE ROUND TRIP
# ===========================================================================

section("4. The five era-14 columns round-trip, and NULL on an older row")

_NEW_COLUMNS = ("llm_classifier_cache_write_tokens", "estimated_cost_cached_usd",
                "quality_dropped_percentile", "quality_dropped_floor",
                "quality_dropped_floor_only")
check("4a  all five are declared as ADDITIVE columns, so an existing database "
      "gains them by migration and every existing row keeps NULL",
      sorted(c for c in _NEW_COLUMNS
             if c not in _dbl.INFERENCE_COLUMN_ADDITIONS), [])
check("4b  the schema era was bumped with them",
      _dbl.SCHEMA_USER_VERSION >= 14, True)
check("4c  ...and the era is RECORDED in the file's own era list, which is the "
      "only place a human can read what era 14 was",
      "# ERA 14:" in io.open(repo("oncotriage", "storage",
                                  "database_logger.py"),
                             encoding="utf-8").read(), True)

_DB = os.path.join(_TMP, "inferences.db")
_dbl.initialize_database(_DB)


def base_result(patient_id, **extra):
    out = {"patient_id": patient_id, "timestamp": "2026-09-03T00:00:00",
           "matching_model": _M,
           "llm_classifier_input_tokens": 100_000,
           "llm_classifier_output_tokens": 2_000,
           "matches": [], "near_misses": [], "not_evaluable": [],
           "stage_timings": {}}
    out.update(extra)
    return out


_w1 = drive(_dbl.log_inference,
            base_result("CACHED",
                        llm_classifier_cached_input_tokens=90_000,
                        llm_classifier_cache_write_tokens=5_000,
                        quality_dropped_percentile=7,
                        quality_dropped_floor=3,
                        quality_dropped_floor_only=1),
            {"patient_id": "CACHED", "demographics": {}}, db_path=_DB)
_w2 = drive(_dbl.log_inference, base_result("NOCACHE"),
            {"patient_id": "NOCACHE", "demographics": {}}, db_path=_DB)
check("4d  both writes landed", (getattr(_w1, "ok", None),
                                getattr(_w2, "ok", None)), (True, True))

_conn = sqlite3.connect(_DB)
_rows = {r[0]: r[1:] for r in _conn.execute(
    "SELECT patient_id, estimated_cost_usd, estimated_cost_cached_usd, "
    "llm_classifier_cached_input_tokens, llm_classifier_cache_write_tokens, "
    "quality_dropped_percentile, quality_dropped_floor, "
    "quality_dropped_floor_only FROM inferences")}

_CACHED = at(_rows, "CACHED", (None,) * 7)
_NOCACHE = at(_rows, "NOCACHE", (None,) * 7)
check("4e  the cached row stores the cache-aware figure BESIDE the flat one, "
      "and they differ",
      (rounded(_CACHED[0]), rounded(_CACHED[1])), (0.363, 0.099825))
check("4f  ...and both cached token counts survived the write",
      (_CACHED[2], _CACHED[3]), (90_000, 5_000))
check("4g  ...and so did the three-way quality split", _CACHED[4:], (7, 3, 1))
check("4h  a row reporting NO cached tokens stores NULL for both counts and "
      "the SAME figure in both cost columns",
      (_NOCACHE[2], _NOCACHE[3], _NOCACHE[0] == _NOCACHE[1]),
      (None, None, True))
check("4i  ...and NULL, never 0, for the quality split it never measured",
      _NOCACHE[4:], (None, None, None))

# --- a row written BEFORE era 14 ------------------------------------------
#
# BUILT BY DROPPING THE COLUMNS FROM A REAL DATABASE rather than by retyping a
# historical CREATE TABLE, so the pre-migration shape is derived from this
# module's own constants and cannot describe a shape no era ever had.
_OLD = os.path.join(_TMP, "old.db")
shutil.copy2(_DB, _OLD)
_oc = sqlite3.connect(_OLD)
for _col in _NEW_COLUMNS:
    _oc.execute(f"ALTER TABLE inferences DROP COLUMN {_col}")
_oc.commit()
_oc.close()
_missing_before = sorted(_NEW_COLUMNS)
_dbl.initialize_database(_OLD)
_oc = sqlite3.connect(_OLD)
_cols_after = {r[1] for r in _oc.execute("PRAGMA table_info(inferences)")}
check("4j  a pre-era-14 database gains all five by migration",
      sorted(c for c in _NEW_COLUMNS if c not in _cols_after), [])
_old_rows = list(_oc.execute(
    "SELECT estimated_cost_cached_usd, quality_dropped_floor_only "
    "FROM inferences"))
check("4k  ...and every row that was already in it reads NULL for them -- "
      "nothing is backfilled", sorted(set(_old_rows)), [(None, None)])
check("4l  NON-DEGENERACY: those columns really were absent before the "
      "migration", _missing_before, sorted(_NEW_COLUMNS))
_oc.close()

# --- the two new queries ---------------------------------------------------
check("4m  both reader queries are registered",
      sorted(k for k in ("quality_gate_knob_split", "stage5_cache_savings")
             if k not in _queries.QUERIES_BY_KEY), [])
for _key in ("quality_gate_knob_split", "stage5_cache_savings"):
    _frame = drive(_queries.run, _conn, _key)
    check(f"4n  `{_key}` runs against a current database and returns a row",
          getattr(_frame, "empty", "no frame"), False)
_report = drive(_queries.report, _conn)
check("4o  report() still reaches the end with the two new queries in the "
      "registry", isinstance(_report, dict), True)
_conn.close()

# THE PRE-ERA DATABASE MUST SKIP THEM RATHER THAN KILLING report(), which is
# the defect item 38 removed from File 16 and the one a new query naming an
# additive column reinstates if it forgets its declaration.
_oc = sqlite3.connect(_OLD)
for _col in _NEW_COLUMNS:
    _oc.execute(f"ALTER TABLE inferences DROP COLUMN {_col}")
_oc.commit()
_unavailable = drive(_queries.unavailable, _oc)
check("4p  a pre-era-14 database reports both new queries as unanswerable "
      "rather than raising inside report()",
      sorted(k for k in ("quality_gate_knob_split", "stage5_cache_savings")
             if k not in (_unavailable or {})), [])
_report_old = drive(_queries.report, _oc)
check("4q  ...and report() runs to the end against it",
      isinstance(_report_old, dict), True)
check("4r  ...with the skipped keys ABSENT from the result, so a caller "
      "indexing one gets a KeyError it can act on rather than a frame of "
      "zeros about runs that were never asked",
      sorted(k for k in ("quality_gate_knob_split", "stage5_cache_savings")
             if k in (_report_old or {})), [])
_oc.close()


# ===========================================================================
# 5. THE QUALITY SPLIT THROUGH PROVENANCE
# ===========================================================================

section("5. The gate's three-way split survives the terminal boundary")

_STATE = {"patient_data": {"patient_id": "P", "conditions": [],
                           "medications": [], "demographics": {}},
          "quality_dropped_percentile": 7,
          "quality_dropped_floor": 3,
          "quality_dropped_floor_only": 1}
_prov = drive(_terminal._pipeline_provenance, _STATE)
check("5a  _pipeline_provenance carries all three",
      [(_prov or {}).get(k) for k in
       ("quality_dropped_percentile", "quality_dropped_floor",
        "quality_dropped_floor_only")], [7, 3, 1])
_prov_empty = drive(_terminal._pipeline_provenance, {"patient_data": {}})
check("5b  ...and a run that never reached Stage 4 reports NULL, never 0 -- 0 "
      "asserts a gate that examined a pool and removed nothing",
      [(_prov_empty or {}).get(k) for k in
       ("quality_dropped_percentile", "quality_dropped_floor",
        "quality_dropped_floor_only")], [None, None, None])

# ALL THREE TERMINAL NODES, because a key carried by node_finalize alone makes
# the column populated for a minority of rows and constant for the rest.
_TERM_TREE = ast.parse(io.open(repo("oncotriage", "agent", "terminal.py"),
                               encoding="utf-8").read())
_spreads = []
for _n in ast.walk(_TERM_TREE):
    if isinstance(_n, ast.FunctionDef) and _n.name.startswith("node_"):
        if "_pipeline_provenance(state)" in ast.unparse(_n):
            _spreads.append(_n.name)
check("5c  every terminal node spreads _pipeline_provenance, so the split "
      "reaches the no-candidates and error rows too", len(_spreads), 3)

# LANGGRAPH WRITES ONLY THE CHANNELS THE STATE SCHEMA DECLARES, so an
# undeclared key returned by Stage 4 is DROPPED before provenance could ever
# see it -- and the drop is silent. The channels are read off the class's
# ANNOTATION TARGETS rather than by searching the file's text: state.py argues
# these three in a comment directly above them, so a substring scan would be
# satisfied by the prose after somebody deleted the declarations.
_STATE_TREE = ast.parse(io.open(repo("oncotriage", "agent", "state.py"),
                                encoding="utf-8").read())
_CHANNELS = set()
for _n in ast.walk(_STATE_TREE):
    if isinstance(_n, ast.ClassDef) and _n.name == "TrialMatchState":
        for _stmt in _n.body:
            if isinstance(_stmt, ast.AnnAssign) and isinstance(_stmt.target,
                                                               ast.Name):
                _CHANNELS.add(_stmt.target.id)
check("5d  NON-DEGENERACY: TrialMatchState's channels were actually found",
      len(_CHANNELS) > 20, True)
check("5e  all three quality channels are DECLARED, so Stage 4's return "
      "survives to the terminal boundary",
      sorted(k for k in ("quality_dropped_percentile", "quality_dropped_floor",
                         "quality_dropped_floor_only")
             if k not in _CHANNELS), [])
check("5f  ...and so is the cache-write channel this pass added, which is the "
      "same trap one measurement over",
      "llm_classifier_cache_write_tokens" in _CHANNELS, True)

# THE TOTAL IS DERIVABLE AND IS DELIBERATELY NOT STORED.
_SCHEMA_TEXT = io.open(repo("oncotriage", "storage", "database_logger.py"),
                       encoding="utf-8").read()
check("5g  `quality_dropped` gets NO column of its own, because it is exactly "
      "candidates_after_rule_filter - candidates_after_quality_filter",
      "quality_dropped" in _dbl.INFERENCE_COLUMN_ADDITIONS, False)
check("5h  ...and BOTH of the columns that derive it are in the base schema, "
      "so the total really is recoverable rather than lost",
      ("candidates_after_rule_filter INTEGER" in _SCHEMA_TEXT,
       "candidates_after_quality_filter INTEGER" in _SCHEMA_TEXT),
      (True, True))


# ===========================================================================
# 6. THE CACHE-WRITE ACCUMULATOR'S SCOPE
# ===========================================================================

section("6. Every fold site that assigns the accumulator can reach it")

# THIS SECTION EXISTS BECAUSE THE DEFECT SHIPPED. The first version of the
# accumulator added `cache_write_tokens += _cw` to `_account_unconsumed`
# WITHOUT adding the name to that function's `nonlocal` declaration, which
# makes it LOCAL to that function for the whole of it and raises
# UnboundLocalError on the first unconsumed response. It surfaced as a
# per-trial patient COMPLETING where the fallback-writer path must fail it --
# six failures in tests/test_agent_bedrock_anthropic_per_trial.py and nothing
# at all in the file that added it. It is exactly the failure mode
# `_account_warmup`'s own docstring predicts, reached from the other direction.
_EVAL_TREE = ast.parse(io.open(repo("oncotriage", "agent", "evaluation.py"),
                               encoding="utf-8").read())
_FUNC = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def own_scope(node):
    """Statements in `node`'s OWN scope. A nested def is a different scope."""
    out, stack = [], list(node.body)
    while stack:
        c = stack.pop()
        if isinstance(c, _FUNC):
            continue
        out.append(c)
        stack.extend(ast.iter_child_nodes(c))
    return out


def scope_report(names):
    """(function, assigns, declares-nonlocal) for every scope assigning `names`."""
    out = []
    for node in ast.walk(_EVAL_TREE):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        assigns, nl = set(), set()
        for c in own_scope(node):
            if isinstance(c, ast.Nonlocal):
                nl |= set(c.names)
            if isinstance(c, ast.AugAssign) and isinstance(c.target, ast.Name):
                assigns.add(c.target.id)
            if isinstance(c, ast.Assign):
                for t in c.targets:
                    if isinstance(t, ast.Name):
                        assigns.add(t.id)
        if assigns & names:
            out.append((node.name, sorted(assigns & names), sorted(nl & names)))
    return out


_PAIR = {"cache_write_tokens", "cache_write_reported"}
_report_pair = scope_report(_PAIR)
_OWNER = "node_llm_classifier_evaluation"
_unbound = [f for (f, a, nl) in _report_pair
            if f != _OWNER and set(a) - set(nl)]
check("6a  every scope OTHER than the owner that assigns the pair declares it "
      "nonlocal -- anything else is an UnboundLocalError waiting for the first "
      "response of its kind", _unbound, [])
check("6b  ...and the owner assigns it WITHOUT a nonlocal, because it is where "
      "the accumulator lives",
      sorted(nl for (f, _a, nl) in _report_pair if f == _OWNER), [[]])
check("6c  NON-DEGENERACY: more than one scope assigns the pair, so 6a is not "
      "vacuous", len(_report_pair) >= 3, True)

# THE SAME WALK OVER THE READ PAIR, which has always been correct, as the
# control that the walk can distinguish the two states at all.
_read_report = scope_report({"cached_input_tokens", "cached_input_reported"})
check("6d  CONTROL: the read pair -- unchanged by this work and known good -- "
      "passes the identical walk",
      [f for (f, a, nl) in _read_report if f != _OWNER and set(a) - set(nl)],
      [])

# AND THE ASYMMETRY THAT IS DELIBERATE, pinned so it cannot be tidied away.
_warmup = [(f, a, nl) for (f, a, nl) in _report_pair if f == "_account_warmup"]
_warmup_read = [(f, a, nl) for (f, a, nl) in _read_report
                if f == "_account_warmup"]
check("6e  the WARMUP folds the write pair -- it is the only request that "
      "writes, so excluding it would zero the column it feeds",
      bool(_warmup), True)
check("6f  ...and does NOT fold the read pair, which is the pre-existing rule "
      "and the opposite decision, argued at both", _warmup_read, [])


# ===========================================================================
# 7. THE HARNESS PRINTS ITS OWN INSTRUMENTS
# ===========================================================================

section("7. run_harness prints the degradation and census blocks at run end")

_calls = [ast.unparse(n) for n in ast.walk(_HARNESS_TREE)
          if isinstance(n, ast.Call)]
check("7a  main() prints the census block",
      any("print_census_report" in c for c in _calls), True)
check("7b  ...and the degradation block",
      any("degradation.print_report" in c for c in _calls), True)
check("7c  ...from ONE snapshot each, taken here rather than re-read per "
      "consumer",
      (sum("degradation.census_snapshot()" in c for c in _calls),
       sum(c == "degradation.snapshot()" for c in _calls)), (1, 1))

# THE ORDER IS THE BATCH RUNNER'S: census, degradations, then the verdict.
_MAIN_BODY = _MAIN_SRC or ""
_i_census = _MAIN_BODY.find("print_census_report")
_i_degr = _MAIN_BODY.find("degradation.print_report")
_i_post = _MAIN_BODY.find("print_post_check")
check("7d  the order is census, then degradations, then the post-check -- "
      "severity ascending, verdict last, which is the runner's ordering",
      _i_census < _i_degr < _i_post, True)
check("7e  NON-DEGENERACY: all three were actually found in main()",
      min(_i_census, _i_degr, _i_post) >= 0, True)

# BOTH BLOCKS REALLY RENDER, DRIVEN RATHER THAN ASSERTED FROM THE SOURCE --
# and the first version of these two checks was written from a GUESS about the
# contract and measured the guess. It asserted that a clean snapshot renders
# NOTHING. The opposite is the contract, argued at `report_lines`: an all-zero
# run produces a STATEMENT, because a run that prints nothing about degradation
# is indistinguishable from one whose reporting was never wired up -- which is
# precisely what this harness looked like before. Both checks now pin what the
# code does, which is the stronger property anyway.
_REGISTERED = degradation.registered_names()
check("7f  NON-DEGENERACY: the registry is non-empty, so 7g-7h are about "
      "something", len(_REGISTERED) > 0, True)
_lines = drive(degradation.report_lines, {_REGISTERED[0]: {"a_key": 3}})
check("7g  report_lines renders the counter that moved, by name",
      any(_REGISTERED[0] in str(line) for line in (_lines or [])), True)
_clean = drive(degradation.report_lines, {})
check("7h  ...and a CLEAN run still renders a block saying so, never silence "
      "-- silence and 'nothing degraded' must not look the same",
      isinstance(_clean, list) and len(_clean) > 0
      and any("CLEAN" in str(line) for line in _clean), True)
_census_clean = drive(degradation.census_report_lines, {})
check("7i  ...and the census block does the same, which is why main() calls "
      "both unconditionally",
      isinstance(_census_clean, list) and len(_census_clean) > 0, True)


# ===========================================================================
# 8. THE PACING BOUND IS IN THE TRACKING INDEX
# ===========================================================================

section("8. The tracking index carries the EFFECTIVE pacing bound")

_params = drive(tracking.configuration_params, "stub-collection")
check("8a  the bound is in the index under the column's own name",
      (_params or {}).get("matching_per_trial_parallel_bound"),
      config.per_trial_parallel_bound())
check("8b  ...and neither constant behind it is in the enumeration, which "
      "cannot express the reconciliation",
      sorted(n for n in ("MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS",
                         "BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS")
             if n in tracking.CONFIGURATION_PARAM_NAMES), [])
check("8c  NON-DEGENERACY: the owner and the shared constant actually DISAGREE "
      "on the shipped configuration, which is why reading the constant was "
      "wrong", config.per_trial_parallel_bound()
      != config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS, True)
check("8d  the excludes prose no longer claims this knob cannot explain a "
      "difference in a result",
      "per_trial_parallel_bound" in io.open(
          repo("oncotriage", "tracking.py"), encoding="utf-8").read(), True)


# ===========================================================================
# 9. ISOLATION
# ===========================================================================

section("9. Isolation")

check("9a  no model was loaded",
      sorted(m for m in ("torch", "transformers") if m in sys.modules), [])
check("9b  no AWS SDK was imported -- no client of any kind was built",
      sorted(m for m in ("boto3", "botocore") if m in sys.modules), [])
_after = {p: sha256_file(p) for p in _WATCHED}
check("9c  every repository file this test READ is byte-identical",
      sorted(os.path.basename(p) for p in _WATCHED
             if _after[p] != _HASHES_BEFORE[p]), [])
check("9d  NON-DEGENERACY: the six hashes are not one file hashed six times",
      len(set(_HASHES_BEFORE.values())), len(_WATCHED))
check("9e  every database this file opened is inside its own temp directory",
      sorted(p for p in (_DB, _OLD) if not p.startswith(_TMP)), [])
shutil.rmtree(_TMP, ignore_errors=True)
check("9f  ...and the temp directory was removed", os.path.exists(_TMP), False)


# ===========================================================================
# SUMMARY
# ===========================================================================

print(f"\n{'=' * 74}\nSUMMARY\n{'=' * 74}")
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
Created on Wed Sep  3 2026

@author: ramyalsaffar
"""
