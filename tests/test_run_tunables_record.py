# Run Tunables Record Test
##########################

"""
EVERY RUN ROW NOW SAYS WHAT THE KNOBS WERE SET TO, FROM THE SAME OWNER THE
FIXTURES READ.

THE GAP. ``oncotriage/fixtures/capture.py`` recorded a ``tunables`` dict in every
characterization fixture's environment block -- twenty-nine config constants,
which ``oncotriage/fixtures/replay.py:diff_tunables()`` compares against the live
config so that a prefix difference caused by a one-line config edit is REPORTED
as a config edit rather than hunted as a refactor bug. ``runs`` recorded NONE of
it. A campaign's knob settings were recoverable only for as long as nobody
edited ``oncotriage/config.py``: the stamp columns carry the nine facts a resume
gates on and say nothing about the retrieval pools, the fusion weights, the
Stage 4 gate or the packing budget, so "what was RRF_K when this campaign ran"
had no answer in the schema at all.

THE FIX IS ONE OWNER AND A SECOND CONSUMER. ``config.TUNABLE_NAMES`` is the
closed key set and ``config.effective_tunables()`` builds the dict; the fixture
writer calls it and ``database_logger._run_tunables_json`` calls it for the new
``runs.tunables`` column (schema era 15). A second copy of a twenty-nine-member
literal is a second copy to keep in step by hand -- the shape this project has
had to remove for the MedCPT checkpoint, the BM25 sparse model and the per-model
cost arithmetic, each time after the two copies had already drifted.

WHAT THIS FILE HOLDS
--------------------
    1. THE OWNER. Closed, non-degenerate, no duplicates, every member the name
       of a config attribute, and every value the SAME OBJECT ``getattr(config,
       name)`` returns -- which is the read ``diff_tunables()`` makes, so the
       two cannot answer differently for one key.
    2. THE EFFECTIVE-VALUE DOCTRINE, driven: a constant rebound on the config
       module is what gets recorded, and the control shows the comparison can
       fail. This is the half that was CONVENTIONAL before -- twenty-five of the
       twenty-nine entries were import-bound names and the comment on the
       twenty-sixth claimed to be "the ONLY entry" read off the module, which
       had stopped being true.
    3. THE IMPORT-TIME GUARD, driven on the shipped function with the tuple
       rebound: a name that is not an attribute, a duplicate, and a value that
       will not serialize each RAISE and NAME the offender. Without it, each is
       a permanent phantom diff on every future fixture rather than a failure in
       the commit that caused it.
    4. ONE OWNER, TWO CONSUMERS, by AST: the fixture's ``"tunables"`` value IS
       ``config.effective_tunables()`` and the run-row writer calls the same
       function. Each with a non-degeneracy reader.
    5. THE COLUMN: declared TEXT, on a fresh table, written on every row,
       round-tripping to the exact dict, with SORTED keys and pinned separators
       so two identically-configured runs produce byte-identical strings.
    6. RECORD, NEVER GATE. It is absent from ``RUN_FINGERPRINT_COLUMNS`` and
       from ``run_fingerprint.FINGERPRINT_FIELDS``; ``finalize_run_record``
       cannot name it; nothing in the package reads it to decide anything; and a
       resume across a tunable change is PERMITTED, driven through the shipped
       ``run_fingerprint.compare()``.
    7. PER-INVOCATION. Two invocations write two rows and an earlier row's dict
       is never overwritten -- by BOTH mechanisms, each driven: a resume is a
       new invocation that INSERTs, and the only UPDATE's SET list cannot name
       this column.
    8. THE MIGRATION IS ADDITIVE. An era-14 database gains the column, its
       existing row reads NULL, the addition is ANNOUNCED, nothing else moves,
       and the era was bumped in the same commit with its ledger entry -- which
       is ``SCHEMA_USER_VERSION``'s own stated rule.
    9. THE FAULT PATH. A value that will not serialize is COUNTED and MARKED,
       never raised and never NULL -- because NULL in this column has exactly
       one meaning, "this row predates era 15", and a second meaning would make
       every query that separates the two eras wrong.
   10. CONTROLS -- each shown to FIRE.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO MODEL LOAD, NO CORPUS, NO GIT
HISTORY, NO LIVE SERVER, NO PROVIDER CLIENT OF ANY KIND -- ``deps.is_resolved``
is asserted False for all three client keys at the end, so a client that HAD
been built is caught. Every database is inside a ``tempfile.mkdtemp`` that is
removed and asserted gone, and ``paths._RESOLVED`` is seeded so nothing can
resolve to the production tree. It needs NO provider pin: nothing here drives
Stage 5, installs a stand-in or reaches a seam. NOT in the collision matrix --
it writes nothing in the repository, and it DOES read ``oncotriage/config.py``,
which ``tests/test_config_snapshot_date_rot.py`` rewrites in place, so all four
files it reads are sha256-compared at the end. It EXECS NOTHING and loads no
module by location: every control is a different INPUT to a shipped function, a
module attribute rebound inside try/finally with the restore asserted, or an
``ast`` walk over a parsed source file.

Run from terminal:
    python tests/test_run_tunables_record.py

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
import re
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
    project has shipped more than a dozen times.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                               # noqa: BLE001
        return ("<RAISED>", type(exc).__name__, str(exc)[:400])


def raised(value):
    """The exception TYPE of a `drive` result, or None if it returned."""
    return value[1] if isinstance(value, tuple) and value[:1] == ("<RAISED>",) \
        else None


def raise_text(value):
    """The message of a `drive` result, or "" if it returned normally."""
    return value[2] if isinstance(value, tuple) and value[:1] == ("<RAISED>",) \
        else ""


def as_dict(text):
    """`json.loads(text)` as a MAPPING, or a named absence.

    `drive` already turns a raise into a tuple -- but a caller that then does
    `.items()` on that tuple raises INSIDE a check() argument list, which is
    the abort shape all over again one level down. The revert matrix found it:
    planting "the column is never written" made `_raw` None, `json.loads`
    raised, `drive` returned its tuple, and `.items()` on the tuple took the
    file down with no summary -- in the section written to catch that revert.
    """
    value = drive(json.loads, text)
    return value if isinstance(value, dict) else {"<not-json>": str(value)[:200]}


def at(mapping, key, default="<absent>"):
    """`mapping[key]` that cannot raise -- the absence is a value check fails on."""
    try:
        return mapping[key]
    except BaseException:                                      # noqa: BLE001
        return default


_TMP = tempfile.mkdtemp(prefix="oncotriage-runtunables-")

# EVERYTHING RESOLVES INSIDE THE SCRATCH TREE. paths._RESOLVED is the seam
# tests/test_ablation_db_isolation.py established; seeding it means no glob
# fires and nothing can reach the production database even by accident.
from oncotriage import paths as _paths                          # noqa: E402
_PATHS_SAVED = dict(_paths._RESOLVED)
_paths._RESOLVED["inferences_path"] = os.path.join(_TMP, "never-written.db")

from oncotriage import config                                   # noqa: E402
from oncotriage import degradation as _deg                      # noqa: E402
from oncotriage import run_fingerprint as _fp                   # noqa: E402
from oncotriage.agent import deps as _deps                      # noqa: E402
from oncotriage.storage import database_logger as _dl           # noqa: E402

_CAPTURE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(_dl.__file__))),
    "fixtures", "capture.py")
_READ_FILES = [os.path.abspath(config.__file__),
               os.path.abspath(_dl.__file__),
               os.path.abspath(_fp.__file__),
               _CAPTURE_PATH]
_HASH_BEFORE = {p: hashlib.sha256(open(p, "rb").read()).hexdigest()
                for p in _READ_FILES}

_CAPTURE_TREE = ast.parse(open(_CAPTURE_PATH, encoding="utf-8").read())
_DL_TREE = ast.parse(open(os.path.abspath(_dl.__file__), encoding="utf-8").read())


def quiet(fn, *args, **kwargs):
    """Run `fn` with BOTH streams captured. `console.out` writes to STDERR."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        value = drive(fn, *args, **kwargs)
    return value, out.getvalue() + err.getvalue()


def rows(db, sql, args=()):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def open_run(db, **kwargs):
    """A REAL run row through the REAL start_run_record, silently."""
    value, _log = quiet(_dl.start_run_record, "tunables-test", db_path=db,
                        **kwargs)
    return value


def stored(db, run_id):
    """The raw `runs.tunables` text of one row, or a named absence.

    `run_id` IS GUARDED because `open_run` returns `drive`'s tuple when
    start_run_record raises -- which is exactly what the "the fault raises
    instead of being counted" revert makes it do -- and handing a tuple to
    sqlite3 raises InterfaceError from inside a helper, taking the file down in
    the section written to catch that revert. Measured by the revert matrix,
    not anticipated.
    """
    if not isinstance(run_id, int):
        return f"<no run id: {run_id!r}>"
    got = drive(rows, db, "SELECT tunables FROM runs WHERE id = ?", (run_id,))
    if not isinstance(got, list):
        return f"<unreadable: {got!r}>"
    return got[0]["tunables"] if got else "<no such row>"


def size(value):
    """`len(value)`, or -1 when there is nothing to measure.

    A bare `len()` on a column that a defect made NULL raises inside a check()
    argument list. The revert matrix found that too.
    """
    try:
        return len(value)
    except BaseException:                                      # noqa: BLE001
        return -1


print("=" * 78)
print("RUN TUNABLES RECORD TEST")
print("=" * 78)


# ===========================================================================
# 1. THE OWNER
# ===========================================================================
print("\n=== 1. config.TUNABLE_NAMES / config.effective_tunables() ===")

_NAMES = list(config.TUNABLE_NAMES)

check("1a  TUNABLE_NAMES is a tuple, so a caller cannot mutate the declared "
      "set through the module attribute",
      isinstance(config.TUNABLE_NAMES, tuple), True)
check("1b  ...and is non-degenerate (a set this small would mean the "
      "extraction dropped members)", len(_NAMES) >= 25, True)
check("1c  ...with no duplicate, so the dict it builds is as long as the tuple",
      len(_NAMES), len(set(_NAMES)))

_TUN = drive(config.effective_tunables)
check("1d  effective_tunables() returns a dict", isinstance(_TUN, dict), True)
check("1e  ...whose keys are the declared tuple, IN THE DECLARED ORDER -- the "
      "fixture serializes without sort_keys, so order is in the file",
      list(_TUN), _NAMES)
check("1f  EVERY MEMBER RESOLVES as a config attribute, which is the doctrine "
      "diff_tunables() depends on: a key that is not one is reported "
      "'<no longer defined>' on every future fixture, forever",
      sorted(n for n in _NAMES if not hasattr(config, n)), [])
check("1g  ...and every VALUE is the SAME OBJECT getattr(config, name) "
      "returns, so `globals()[name]` here and the consumer's read cannot "
      "answer differently for one key",
      [k for k, v in _TUN.items() if v is not getattr(config, k)], [])
check("1h  ...control: the resolvability test can fail, shown on a name this "
      "module deliberately does not define",
      hasattr(config, "MATCHING_CALL_MODE"), False)
check("1i  a fresh dict per call, never a shared one -- two callers may hold "
      "it across a config change",
      config.effective_tunables() is config.effective_tunables(), False)
check("1j  ...and they are EQUAL, so 'fresh' is about identity rather than "
      "about the values disagreeing",
      config.effective_tunables() == config.effective_tunables(), True)

# THE TWO SPELLINGS THIS SET DELIBERATELY DOES NOT USE. Each has a function for
# an owner or is the wrong fact; both are recorded beside the dict instead.
check("1k  no spelling of the Stage 5 arm is a member -- the owner is a "
      "function, so no key of it could be an attribute name",
      sorted(set(_NAMES) & {"MATCHING_CALL_MODE", "MATCHING_CALL_MODES",
                            "MATCHING_PER_TRIAL_CALLS_ENABLED"}), [])
check("1l  ...and neither is the EFFECTIVE temperature, for the same reason: "
      "matching_temperature_record() is a function and the raw constant is "
      "what this dict compares",
      ("MATCHING_TEMPERATURE" in _NAMES,
       "MATCHING_TEMPERATURE_SENT" in _NAMES), (True, False))
check("1m  ...non-degeneracy: the same intersection over a copy carrying one "
      "of those spellings finds it, so the empty result is a finding",
      sorted(set(_NAMES + ["MATCHING_CALL_MODE"]) & {"MATCHING_CALL_MODE"}),
      ["MATCHING_CALL_MODE"])


# ===========================================================================
# 2. THE EFFECTIVE-VALUE DOCTRINE
# ===========================================================================
print("\n=== 2. the values are read at CALL time, off the module ===")

_before = config.RRF_K
_saved_rrf = config.RRF_K
try:
    config.RRF_K = _saved_rrf + 7
    _during = at(drive(config.effective_tunables), "RRF_K")
finally:
    config.RRF_K = _saved_rrf
_after = at(drive(config.effective_tunables), "RRF_K")

check("2a  a constant rebound on the config module is what gets RECORDED, "
      "not the value any importer bound at import time",
      _during, _saved_rrf + 7)
check("2b  ...non-degeneracy: it really differed from the shipped value, so "
      "2a is not comparing a number with itself", _during != _before, True)
check("2c  ...and the restore took, so nothing below runs under the override",
      (_after, config.RRF_K), (_saved_rrf, _saved_rrf))

# THE SAME, FOR A STRING, AND FOR THE ONE MEMBER A PROBE REALLY MOVES.
_saved_provider = config.MATCHING_PROVIDER
_other = next(p for p in config.MATCHING_PROVIDERS if p != _saved_provider)
try:
    config.MATCHING_PROVIDER = _other
    _prov_during = at(drive(config.effective_tunables), "MATCHING_PROVIDER")
finally:
    config.MATCHING_PROVIDER = _saved_provider
check("2d  the provider a probe or a pin installs is the one recorded -- "
      "bedrock_probe.py and tests/_provider_pin.py both move it on the module",
      _prov_during, _other)
check("2e  ...and only THAT key moved: the doctrine is per-read, not a "
      "wholesale re-resolution that could disturb its neighbours",
      [k for k, v in config.effective_tunables().items()
       if v != _TUN[k]], [])
check("2f  ...and the provider was restored",
      config.MATCHING_PROVIDER, _saved_provider)


# ===========================================================================
# 3. THE IMPORT-TIME GUARD
# ===========================================================================
print("\n=== 3. _assert_tunable_names_resolve() ===")

_guard = getattr(config, "_assert_tunable_names_resolve", None)
check("3a  the guard exists and is callable", callable(_guard), True)
check("3b  ...and PASSES over the shipped tuple (non-degeneracy: a guard that "
      "always raised would make every case below vacuous)",
      raised(drive(_guard)), None)

_saved_names = config.TUNABLE_NAMES
try:
    config.TUNABLE_NAMES = _saved_names + ("A_NAME_CONFIG_DOES_NOT_DEFINE",)
    _missing = drive(_guard)
finally:
    config.TUNABLE_NAMES = _saved_names
check("3c  a member that is not an attribute RAISES a RuntimeError...",
      raised(_missing), "RuntimeError")
check("3d  ...and NAMES the offender, so the fix is the line that added it",
      "A_NAME_CONFIG_DOES_NOT_DEFINE" in raise_text(_missing), True)

try:
    config.TUNABLE_NAMES = _saved_names + (_saved_names[0],)
    _dup = drive(_guard)
finally:
    config.TUNABLE_NAMES = _saved_names
check("3e  a DUPLICATE member raises...", raised(_dup), "RuntimeError")
check("3f  ...and names it -- the dict would be shorter than the tuple, so a "
      "reader counting members would disagree with one reading them",
      _saved_names[0] in raise_text(_dup), True)

# A VALUE THAT WILL NOT SERIALIZE. Both artifacts this dict feeds are JSON, so
# the offender breaks every fixture and every run row of the era that adds it.
_saved_pn = config.Project_Name
try:
    config.Project_Name = object()
    config.TUNABLE_NAMES = _saved_names + ("Project_Name",)
    _bad = drive(_guard)
finally:
    config.TUNABLE_NAMES = _saved_names
    config.Project_Name = _saved_pn
check("3g  a member whose value will not serialize to JSON raises...",
      raised(_bad), "RuntimeError")
check("3h  ...and names it among the suspects, by type",
      "Project_Name" in raise_text(_bad), True)
check("3i  ...and the restores took",
      (config.TUNABLE_NAMES is _saved_names, config.Project_Name is _saved_pn),
      (True, True))
# A VALUE THAT SERIALIZES AND COMES BACK DIFFERENT. This is the trap the
# serializability check alone does NOT catch, and it is one plausible edit away:
# config.MATCHING_CALL_MODES is a tuple, json.dumps writes it as an array,
# json.loads returns a LIST, and diff_tunables() then compares a recorded list
# against a live tuple and reports the member MOVED on every replay of every
# fixture, forever.
try:
    config.TUNABLE_NAMES = _saved_names + ("MATCHING_CALL_MODES",)
    _tuple_member = drive(_guard)
finally:
    config.TUNABLE_NAMES = _saved_names
check("3i-a a TUPLE member is refused even though it serializes, because it "
      "comes back a LIST and would be reported MOVED forever",
      raised(_tuple_member), "RuntimeError")
check("3i-b ...and the refusal NAMES it and says why",
      ("MATCHING_CALL_MODES" in raise_text(_tuple_member),
       "round trip" in raise_text(_tuple_member)), (True, True))
check("3i-c ...non-degeneracy: that member really does serialize, so 3i-a is "
      "about the ROUND TRIP rather than about json.dumps refusing it",
      raised(drive(json.dumps, {"x": config.MATCHING_CALL_MODES})), None)
check("3i-d ...and really does come back a different type, which is the "
      "mechanism the guard is protecting against",
      type(json.loads(json.dumps(config.MATCHING_CALL_MODES)))
      is type(config.MATCHING_CALL_MODES), False)

# A NON-FINITE FLOAT. Python's json writes `Infinity`, which is NOT valid JSON
# and which `json.loads` reads back -- so it SURVIVES a Python round trip and
# the equality above cannot see it, while every non-Python reader of this
# column rejects the token. `nan` is caught by the equality (nan != nan) and
# `inf` is not, which is the half-coverage that reads as coverage.
_saved_floor = config.MEDCPT_SCORE_FLOOR
try:
    config.MEDCPT_SCORE_FLOOR = float("inf")
    _inf = drive(_guard)
finally:
    config.MEDCPT_SCORE_FLOOR = _saved_floor
check("3i-e a non-finite float is refused, even though it survives a PYTHON "
      "round trip -- `Infinity` is not valid JSON and this column is queried",
      raised(_inf), "RuntimeError")
check("3i-f ...non-degeneracy: it really does survive a Python round trip, so "
      "3i-e is about allow_nan rather than about the equality catching it",
      json.loads(json.dumps(float("inf"))) == float("inf"), True)
check("3i-g ...and the floor was restored",
      config.MEDCPT_SCORE_FLOOR, _saved_floor)

check("3j  ...and the guard is green again afterwards, so section 3 left "
      "nothing behind", raised(drive(_guard)), None)

# AND IT IS ACTUALLY CALLED. Sections 3c-3h drive the function directly, which
# says the guard WORKS and nothing at all about whether it RUNS. Deleting the
# module-scope call leaves every one of them green while a bad name ships
# silently -- measured, not anticipated: the revert matrix reported exactly
# that as MISSED, which is why this check exists.
_CFG_TREE = ast.parse(open(os.path.abspath(config.__file__),
                           encoding="utf-8").read())
_MODULE_CALLS = sorted({ast.unparse(n.value.func) for n in _CFG_TREE.body
                        if isinstance(n, ast.Expr)
                        and isinstance(n.value, ast.Call)})
check("3k  ...and the guard is CALLED at config's module scope, so it runs in "
      "every process rather than only when a test drives it",
      "_assert_tunable_names_resolve" in _MODULE_CALLS, True)
check("3l  ...non-degeneracy: the reader really found config's module-scope "
      "calls, so 3k is not satisfied by a walk that matched nothing",
      len(_MODULE_CALLS) >= 1, True)


# ===========================================================================
# 4. ONE OWNER, TWO CONSUMERS
# ===========================================================================
print("\n=== 4. both artifacts read the same function ===")


def _env_key_source(key):
    """The unparsed VALUE expression capture.py's environment dict stores."""
    fns = [n for n in ast.walk(_CAPTURE_TREE)
           if isinstance(n, ast.FunctionDef)
           and n.name == "build_environment_block"]
    if not fns:
        return "<no build_environment_block>"
    for node in ast.walk(fns[0]):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == key:
                    return ast.unparse(v)
    return f"<no {key!r} key>"


check("4a  the FIXTURE records the owner's dict rather than a literal of its "
      "own -- which is what makes 'the two artifacts cannot disagree' a "
      "property of the code rather than of two lists agreeing today",
      _env_key_source("tunables"), "config.effective_tunables()")
check("4b  ...non-degeneracy: the same reader finds a neighbouring key, so an "
      "absence above would be a finding rather than a reader matching nothing",
      _env_key_source("sparse_model"), "BM25_SPARSE_MODEL_NAME")


def _calls_in(module_tree, fn_name):
    """Every dotted call name inside a top-level function of the module."""
    fns = [n for n in ast.walk(module_tree)
           if isinstance(n, ast.FunctionDef) and n.name == fn_name]
    if not fns:
        return ["<no such function>"]
    return sorted({ast.unparse(n.func) for n in ast.walk(fns[0])
                   if isinstance(n, ast.Call)})


_WRITER_CALLS = _calls_in(_DL_TREE, "_run_tunables_json")
check("4c  the RUN ROW's writer calls the same owner",
      "_config.effective_tunables" in _WRITER_CALLS, True)
check("4d  ...and builds no dict of its own: no other config attribute is read "
      "in that function",
      sorted(c for c in _WRITER_CALLS if c.startswith("_config.")),
      ["_config.effective_tunables"])
check("4e  ...non-degeneracy: the reader really found the function and its "
      "calls", len(_WRITER_CALLS) >= 2, True)
check("4f  ...and it serializes with SORTED keys, which the fixture "
      "deliberately does not -- one artifact is diffed, the other is queried",
      [kw.arg for n in ast.walk(
          [f for f in ast.walk(_DL_TREE)
           if isinstance(f, ast.FunctionDef)
           and f.name == "_run_tunables_json"][0])
       if isinstance(n, ast.Call) and ast.unparse(n.func) == "json.dumps"
       for kw in n.keywords if kw.arg == "sort_keys"], ["sort_keys"])


# ===========================================================================
# 5. THE COLUMN
# ===========================================================================
print("\n=== 5. runs.tunables ===")

check("5a  declared TEXT in RUN_COLUMN_ADDITIONS, which is what migrates an "
      "existing database and what queries.ADDITIVE_COLUMNS reads",
      _dl.RUN_COLUMN_ADDITIONS.get("tunables"), "TEXT")
check("5b  ...and is therefore written by the INSERT",
      "tunables" in _dl.RUN_COLUMNS, True)

_DB = os.path.join(_TMP, "fresh.db")
_rid = open_run(_DB)
_cols = [r["name"] for r in rows(_DB, "PRAGMA table_info(runs)")]
check("5c  a fresh database has the column", "tunables" in _cols, True)
check("5d  ...stamped at the current era",
      at(rows(_DB, "PRAGMA user_version")[0], "user_version"),
      _dl.SCHEMA_USER_VERSION)

_raw = stored(_DB, _rid)
check("5e  the row carries text, not NULL", isinstance(_raw, str), True)
check("5f  ...which round-trips to the EXACT live dict",
      as_dict(_raw), config.effective_tunables())
check("5g  ...with SORTED keys, so two rows written under two eras of the "
      "tuple differ only where the tunables differ",
      list(as_dict(_raw)), sorted(config.TUNABLE_NAMES))
def _type_mismatches(text):
    """Keys whose stored type differs from the live one, or a named absence.

    THE ABSENCE IS A FINDING RATHER THAN AN EMPTY LIST. A column that is NULL
    or holds something that is not a tunables dict produces no mismatches for
    the trivial reason that it produces no keys, which would make this check
    PASS on exactly the defect it exists to catch.
    """
    got = as_dict(text)
    if not set(got) & set(config.TUNABLE_NAMES):
        return ["<not a tunables dict>"]
    return sorted(k for k, v in got.items()
                  if not hasattr(config, k)
                  or type(v) is not type(getattr(config, k)))


check("5h  ...and the same types, so a reader does not have to re-coerce",
      _type_mismatches(_raw), [])

_rid2 = open_run(_DB)
check("5i  two rows written under one configuration are BYTE-IDENTICAL in this "
      "column, which is what makes GROUP BY tunables a real grouping rather "
      "than a whitespace lottery", stored(_DB, _rid2), _raw)
check("5j  ...non-degeneracy: the value is not empty or a bare '{}'",
      size(_raw) > 100, True)
check("5k  ...and the writer refuses a non-finite float too, so the two "
      "allow_nan settings cannot disagree about what 'serializable' means",
      [kw.arg for n in ast.walk(
          [f for f in ast.walk(_DL_TREE)
           if isinstance(f, ast.FunctionDef)
           and f.name == "_run_tunables_json"][0])
       if isinstance(n, ast.Call) and ast.unparse(n.func) == "json.dumps"
       for kw in n.keywords if kw.arg == "allow_nan"], ["allow_nan"])


# ===========================================================================
# 6. RECORD, NEVER GATE
# ===========================================================================
print("\n=== 6. it is provenance: nothing gates on it ===")

check("6a  it is NOT a stamp column -- absent from RUN_FINGERPRINT_COLUMNS, so "
      "start_run_record does not fill it from a fingerprint",
      "tunables" in _dl.RUN_FINGERPRINT_COLUMNS, False)
check("6b  ...and absent from run_fingerprint.FINGERPRINT_FIELDS, so "
      "compare() has never heard of it",
      "tunables" in _fp.FINGERPRINT_FIELDS, False)
check("6c  ...non-degeneracy: a name that IS a stamp field is in both, so 6a "
      "and 6b can fail",
      ("matching_call_mode" in _dl.RUN_FINGERPRINT_COLUMNS,
       "matching_call_mode" in _fp.FINGERPRINT_FIELDS), (True, True))

# A RESUME ACROSS A TUNABLE CHANGE IS PERMITTED, DRIVEN. This is the property
# "record, never gate" actually means, and asserting the two absences above
# without it would leave it inferred.
_stamp_a = {f: "pinned" for f in _fp.FINGERPRINT_FIELDS}
_stamp_a["fingerprint_version"] = _fp.FINGERPRINT_VERSION
_stamp_b = dict(_stamp_a)
_compared = drive(_fp.compare, _stamp_a, _stamp_b)
_outcome_same = _compared[0] if isinstance(_compared, tuple) \
    and not raised(_compared) else _compared
check("6d  two identical stamps taken under DIFFERENT tunables still compare "
      "as a MATCH, because the tunables are in neither stamp",
      _outcome_same, _fp.FP_MATCH)
check("6e  ...non-degeneracy: moving a field that IS gated makes the same "
      "comparison refuse",
      drive(_fp.compare, _stamp_a,
            dict(_stamp_b, matching_call_mode="moved"))[0], _fp.FP_CHANGED)

# NOTHING IN THE PACKAGE READS THE COLUMN TO DECIDE ANYTHING.
#
# BY AST, NOT BY TEXT, AND THE FIRST VERSION OF THIS CHECK WAS THE TEXT ONE.
# It grepped every package file for "runs.tunables" and reported capture.py --
# whose new COMMENT explaining the extraction says "runs.tunables (schema era
# 15)". The argument for the change was reported as a use of the thing it
# argues about. That is the fifth time this project has met "a file that argues
# about its own settings cannot be grepped for them", and the instrument is an
# ast walk: a `#` comment is not in the tree at all.
#
# THE SHAPE THAT MATTERS IS A SUBSCRIPT. A run row reaches Python as a mapping,
# so reading this column means `row["tunables"]`; a SQL string naming it would
# also have to name `runs`, and check 7j has already established that the only
# UPDATE is finalize's and 6g that no registered query names it.
def _column_subscripts(name):
    """Every `x["<name>"]` in the package, as (file, expression, context)."""
    found = []
    _pkg = os.path.dirname(os.path.dirname(os.path.abspath(_dl.__file__)))
    for _root, _dirs, _files in os.walk(_pkg):
        _dirs[:] = [d for d in _dirs if d != "__pycache__"]
        for _f in sorted(_files):
            if not _f.endswith(".py"):
                continue
            _t = ast.parse(open(os.path.join(_root, _f), encoding="utf-8").read())
            for _n in ast.walk(_t):
                if isinstance(_n, ast.Subscript) \
                        and isinstance(_n.slice, ast.Constant) \
                        and _n.slice.value == name:
                    found.append((_f, ast.unparse(_n), type(_n.ctx).__name__))
    return sorted(found)


# THE COLUMN GAINED ITS FIRST READER AT THE DRIFT REDESIGN, AND THE CHECK IS
# NARROWED TO THE PROPERTY IT ACTUALLY PROTECTS RATHER THAN RELAXED.
#
# The claim was "one place, and it is a WRITE -- no reader loads it to branch
# on". Two things were folded into that: (a) nothing GATES on the column, which
# is the property `runs.tunables`' own declaration makes ("nothing gates,
# branches or refuses on it"), and (b) nothing reads it at all, which was true
# only because nothing had needed it yet.
#
# `oncotriage/monitoring/drift_reference.py:_run_tunables` reads it to resolve
# a DENOMINATOR -- the RRF_POOL_SIZE and TOP_K_CANDIDATES the reporting-only
# underfill metrics divide by. That is the column's declared purpose: a
# campaign run six months ago at RRF_POOL_SIZE = 50 must be read against 50,
# and reading it against today's 100 would report a pool half empty that was in
# fact full. It DECIDES NOTHING: a run whose tunables cannot be read reports
# `unverified_inputs` and the metric declines to divide, which is a REFUSAL BY
# THE METRIC rather than a gate on the run.
#
# THE SET STAYS EXACT so a third reader has to be declared here, in the same
# commit, beside the argument for it -- which is the whole value of an exact
# pin. What replaces the "no reader" half is 6f-ii below, which is the property
# that must stay true.
check("6f  the column is reached in exactly TWO places in the package: the "
      "run-row WRITE, and the drift denominator READ",
      _column_subscripts("tunables"),
      [("database_logger.py", "values['tunables']", "Store"),
       ("drift_reference.py", "row['tunables']", "Load")])
# ...AND NOTHING GATES ON IT, which is what `runs.tunables`' declaration
# promises and what 6d has already measured from the other side: two stamps
# taken under different tunables compare as a MATCH. Asserted here as a
# structural fact about the one reader -- it is in `monitoring`, which no
# resume gate imports, and `run_fingerprint` does not import it.
check("6f-ii ...and the reader is in `monitoring`, which no resume gate "
      "imports -- so reading the column cannot become gating on it",
      sorted({_f for _f, _e, c in _column_subscripts("tunables")
              if c == "Load"}), ["drift_reference.py"])
# BY AST, NOT BY TEXT, AND THE FIRST VERSION OF THIS CHECK WAS THE TEXT ONE --
# which is the trap this very file records having hit at check 6f above, met
# again forty lines later. `oncotriage/run_fingerprint.py` names "tunables"
# FOUR times in prose: its own docstring argues at length that the tunables are
# deliberately OUT of the stamp, and the comment beside the gated-field list
# says so again. A grep reports the argument as a use of the thing it argues
# about. SIXTH time in this project; the instrument is an ast walk.
_FP_SRC = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
    _dl.__file__))), "run_fingerprint.py"), encoding="utf-8").read()
_FP_TREE = ast.parse(_FP_SRC)
check("6f-iii ...and run_fingerprint, which owns every gate, subscripts no "
      "'tunables' key",
      [ast.unparse(n) for n in ast.walk(_FP_TREE)
       if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant)
       and n.slice.value == "tunables"], [])
check("6f-iv  ...and imports the reader's module nowhere, so it cannot come "
      "to gate on it through one",
      sorted({(n.module or "") for n in ast.walk(_FP_TREE)
              if isinstance(n, ast.ImportFrom)
              and "monitoring" in (n.module or "")}), [])
check("6f-v   ...non-degeneracy: the same walk DOES find the subscripts "
      "run_fingerprint really makes, so an empty result is a finding rather "
      "than a broken walk",
      len([n for n in ast.walk(_FP_TREE)
           if isinstance(n, ast.Subscript)
           and isinstance(n.slice, ast.Constant)]) > 5, True)
check("6f  ...non-degeneracy: the same census over a column that IS read "
      "elsewhere finds Load contexts, so an empty reader set is a finding",
      sorted({c for _f, _e, c in _column_subscripts("note")}),
      ["Load", "Store"])
check("6g  ...and no registered query names it, so report() cannot come to "
      "depend on it",
      "tunables" in open(
          os.path.join(os.path.dirname(os.path.abspath(_dl.__file__)),
                       "queries.py"), encoding="utf-8").read(), False)


# ===========================================================================
# 7. PER-INVOCATION: A RESUME NEVER OVERWRITES AN EARLIER ROW
# ===========================================================================
print("\n=== 7. each row records its OWN invocation ===")

_RDB = os.path.join(_TMP, "resume.db")
_saved_rrf = config.RRF_K
try:
    config.RRF_K = 61
    _first = open_run(_RDB, resumed=False)
    config.RRF_K = 62
    quiet(_dl.finalize_run_record, _first, _dl.RUN_RECORD_STATUS_KILLED,
          db_path=_RDB)
    _second = open_run(_RDB, resumed=True)
    quiet(_dl.finalize_run_record, _second, _dl.RUN_RECORD_STATUS_FINISHED,
          db_path=_RDB)
finally:
    config.RRF_K = _saved_rrf

_rrows = rows(_RDB, "SELECT id, resumed, status, tunables FROM runs ORDER BY id")
check("7a  MECHANISM 1: a resume is a NEW INVOCATION, so start_run_record "
      "INSERTs a second row rather than updating the first", len(_rrows), 2)
check("7b  ...and the second is marked as the resume",
      [r["resumed"] for r in _rrows], [0, 1])
check("7c  the ORIGINAL kept the tunables IT ran under",
      at(as_dict(_rrows[0]["tunables"]), "RRF_K"), 61)
check("7d  the RESUME recorded its own, which legitimately differ",
      at(as_dict(_rrows[1]["tunables"]), "RRF_K"), 62)
check("7e  ...so both rows are TRUE at once, which is the whole property",
      at(as_dict(_rrows[0]["tunables"]), "RRF_K")
      != at(as_dict(_rrows[1]["tunables"]), "RRF_K"), True)
check("7f  ...and finalize ran on BOTH without disturbing either",
      [r["status"] for r in _rrows],
      [_dl.RUN_RECORD_STATUS_KILLED, _dl.RUN_RECORD_STATUS_FINISHED])

# MECHANISM 2, STRUCTURAL: the only UPDATE in this module assembles its SET
# list, and this column cannot be in it. Without this, mechanism 1 alone would
# be a fact about today's callers rather than about the writer.
_FINALIZE = [n for n in ast.walk(_DL_TREE)
             if isinstance(n, ast.FunctionDef) and n.name == "finalize_run_record"]
# `endswith(" = ?")` WAS THE FIRST FILTER AND IT ALSO MATCHED " WHERE id = ?",
# which is the UPDATE's predicate rather than a member of its SET list -- so
# the check reported a fifth "assignment" that is not one. A column assignment
# is a bare identifier, a space, an equals and a placeholder; the predicate
# fragment has a leading space and a keyword, and the regex separates them.
_SET_STRINGS = sorted({n.value for f in _FINALIZE for n in ast.walk(f)
                       if isinstance(n, ast.Constant) and isinstance(n.value, str)
                       and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]* = \?", n.value)})
check("7g  MECHANISM 2: finalize_run_record's SET list names only the columns "
      "that describe how a run ENDED", _SET_STRINGS,
      ["finished_at = ?", "note = ?", "status = ?", "stop_reason = ?"])
check("7h  ...non-degeneracy: the reader really found the assembled SET list",
      len(_SET_STRINGS) >= 2, True)
check("7i  ...and 'tunables = ?' is not among them, so no UPDATE anywhere can "
      "overwrite an earlier invocation's record",
      "tunables = ?" in _SET_STRINGS, False)

_UPDATES = sorted({n.value for n in ast.walk(_DL_TREE)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)
                   and "UPDATE runs" in n.value})
check("7j  ...and there is exactly ONE UPDATE against `runs` in the module, so "
      "7g covers every writer of an existing row", len(_UPDATES), 1)


# ===========================================================================
# 8. THE MIGRATION IS ADDITIVE
# ===========================================================================
print("\n=== 8. an era-14 database gains the column ===")

_OLD = os.path.join(_TMP, "era14.db")
_old_rid = open_run(_OLD)
_conn = sqlite3.connect(_OLD)
_conn.execute("ALTER TABLE runs DROP COLUMN tunables")
_conn.execute(f"PRAGMA user_version = {_dl.SCHEMA_USER_VERSION - 1}")
_conn.commit()
_conn.close()
# THE PER-PROCESS SCHEMA CACHE MUST BE DISCARDED or the migration under test
# never runs: `_ensure_database` memoizes by resolved path, so a file this
# process has already opened is skipped. A real pre-era database is one this
# process has never seen; discarding the entry is how a harness reproduces
# that. tests/test_storage_schema_guards.py:fresh_db does exactly this.
_dl._INITIALIZED_DATABASES.discard(os.path.abspath(_OLD))

_pre_cols = [r["name"] for r in rows(_OLD, "PRAGMA table_info(runs)")]
_pre_tables = sorted(r["name"] for r in rows(
    _OLD, "SELECT name FROM sqlite_master WHERE type='table'"))
check("8a  the pre-migration database really LACKS the column (non-degeneracy: "
      "with it present the migration below proves nothing)",
      "tunables" in _pre_cols, False)
check("8b  ...and carries the earlier era",
      at(rows(_OLD, "PRAGMA user_version")[0], "user_version"),
      _dl.SCHEMA_USER_VERSION - 1)

_new_rid, _log = quiet(_dl.start_run_record, "tunables-test", db_path=_OLD)
_post_cols = [r["name"] for r in rows(_OLD, "PRAGMA table_info(runs)")]
check("8c  a writer opening it ADDS the column", "tunables" in _post_cols, True)
check("8d  ...and ANNOUNCES it, so the migration is not silent",
      "runs.tunables" in _log, True)
check("8e  ...and the era moved with the schema, in one open",
      at(rows(_OLD, "PRAGMA user_version")[0], "user_version"),
      _dl.SCHEMA_USER_VERSION)
check("8f  THE EXISTING ROW READS NULL rather than being given a value nobody "
      "recorded -- a run that predates the column did not measure anything",
      stored(_OLD, _old_rid), None)
check("8g  ...and the new row carries the dict",
      as_dict(stored(_OLD, _new_rid)), config.effective_tunables())
check("8h  NOTHING ELSE MOVED -- the other columns are unchanged and in order",
      [c for c in _post_cols if c != "tunables"], _pre_cols)
check("8i  ...and no table was added or dropped",
      sorted(r["name"] for r in rows(
          _OLD, "SELECT name FROM sqlite_master WHERE type='table'")),
      _pre_tables)

# THE ERA RECORD IS NOT DECORATION -- SCHEMA_USER_VERSION's own rule.
_ERA_SRC = open(os.path.abspath(_dl.__file__), encoding="utf-8").read()
_ERA_HEAD = _ERA_SRC.split("SCHEMA_USER_VERSION = ")[0]
check("8j  the era this file stamps has a ledger entry above the constant, "
      "which is SCHEMA_USER_VERSION's own stated rule",
      f"# ERA {_dl.SCHEMA_USER_VERSION}:" in _ERA_HEAD, True)
check("8k  ...and that entry names this column",
      "runs.tunables" in _ERA_HEAD, True)
check("8l  ...non-degeneracy: an era ABOVE this one has no entry, so 8j is not "
      "satisfied by a ledger that names everything",
      f"# ERA {_dl.SCHEMA_USER_VERSION + 1}:" in _ERA_HEAD, False)


# ===========================================================================
# 9. THE FAULT PATH
# ===========================================================================
print("\n=== 9. a value that will not serialize ===")

check("9a  the counter exists and is registered in the run-end report, so a "
      "fault is not silent", "RUN_TUNABLES_FAULTS" in _deg.registered_names(),
      True)
check("9b  ...and is EMPTY before the fault is provoked (non-degeneracy)",
      dict(_dl.RUN_TUNABLES_FAULTS), {})

_FDB = os.path.join(_TMP, "fault.db")
_saved_provider = config.MATCHING_PROVIDER
try:
    # A REBINDING NO CONFIG CAN DECLARE. The declared set is refused at import
    # by section 3's guard, so this is the only way the runtime path is
    # reachable at all: a test harness, never a campaign.
    config.MATCHING_PROVIDER = object()
    _frid, _flog = quiet(_dl.start_run_record, "tunables-test", db_path=_FDB)
finally:
    config.MATCHING_PROVIDER = _saved_provider

check("9c  start_run_record did NOT raise -- killing a campaign because a "
      "PROVENANCE field could not be built inverts the value of the two",
      isinstance(_frid, int), True)
_fv = stored(_FDB, _frid)
check("9d  ...and the column is NOT NULL, because NULL here has exactly one "
      "meaning: this row predates the era", _fv is None, False)
check("9e  ...it is valid JSON, so every reader that parses this column keeps "
      "working", isinstance(drive(json.loads, _fv), dict), True)
check("9f  ...and names the failure rather than looking like a tunables dict",
      as_dict(_fv), {"__tunables_error__": "TypeError"})
check("9g  ...whose keys are not tunable names, so a reader cannot mistake the "
      "marker for a record",
      sorted(set(as_dict(_fv)) & set(config.TUNABLE_NAMES)), [])
check("9h  the fault was COUNTED, keyed by exception type",
      dict(_dl.RUN_TUNABLES_FAULTS), {"serialize:TypeError": 1})
check("9i  ...and reported on the console, so an operator sees it during the "
      "run rather than only in a counter", "tunables record" in _flog, True)

# AN EXCEPTION CLASS NAME IS NOT GUARANTEED TO BE AN IDENTIFIER. A dynamically
# created class can carry a quote, which unsanitized would put a bare `"`
# inside the marker literal and make the ONE value this path promises is always
# valid JSON invalid. Driven rather than argued.
_dl.RUN_TUNABLES_FAULTS.clear()
_HOSTILE = type('Hostile"Name', (Exception,), {})


_saved_effective = config.effective_tunables


def _hostile_tunables():
    raise _HOSTILE("planted")


try:
    config.effective_tunables = _hostile_tunables
    _hrid, _hlog = quiet(_dl.start_run_record, "tunables-test",
                         db_path=os.path.join(_TMP, "hostile.db"))
    _hostile_logged = "tunables record" in _hlog
finally:
    config.effective_tunables = _saved_effective

_hv = stored(os.path.join(_TMP, "hostile.db"), _hrid)
check("9l  an exception whose class name is not an identifier still produces "
      "VALID JSON -- the name is sanitized before it reaches the literal",
      isinstance(drive(json.loads, _hv), dict), True)
check("9m  ...with the offending characters removed rather than the row lost",
      as_dict(_hv), {"__tunables_error__": "HostileName"})
check("9n  ...and the counter key is sanitized the same way, so a Counter key "
      "cannot carry a quote either",
      dict(_dl.RUN_TUNABLES_FAULTS), {"serialize:HostileName": 1})
check("9o  ...non-degeneracy: the planted class really does carry a character "
      "that would break the literal",
      '"' in _HOSTILE.__name__, True)
check("9p  ...and the operator still saw a console line about it",
      _hostile_logged, True)
check("9q  ...and effective_tunables was restored BY IDENTITY",
      config.effective_tunables is _saved_effective, True)

_dl.RUN_TUNABLES_FAULTS.clear()
_ok_rid = open_run(_FDB)
check("9j  ...and the NEXT row is clean again -- the fault is per-call and "
      "latches nothing",
      as_dict(stored(_FDB, _ok_rid)), config.effective_tunables())
check("9k  ...and the provider was restored",
      config.MATCHING_PROVIDER, _saved_provider)


# ===========================================================================
# 10. NOTHING WAS BUILT, NOTHING WAS TOUCHED
# ===========================================================================
print("\n=== 10. no client, no model, no production database ===")

check("10a no provider client was built -- a real one would be cached on the "
      "seam, which is where this file would see it",
      [k for k in (_deps.OPENAI_CLIENT, _deps.BEDROCK_CLIENT,
                   _deps.BEDROCK_ANTHROPIC_CLIENT) if _deps.is_resolved(k)],
      [])
check("10b no model-bearing library entered sys.modules",
      ("torch" in sys.modules, "transformers" in sys.modules), (False, False))
check("10c the seeded scratch inferences path was never created, so nothing "
      "resolved to the production tree",
      os.path.exists(_paths._RESOLVED["inferences_path"]), False)

_paths._RESOLVED.clear()
_paths._RESOLVED.update(_PATHS_SAVED)
check("10d ...and paths._RESOLVED was restored",
      _paths._RESOLVED, _PATHS_SAVED)

_HASH_AFTER = {p: hashlib.sha256(open(p, "rb").read()).hexdigest()
               for p in _READ_FILES}
check("10e every repository file this test READ is byte-unchanged",
      sorted(os.path.basename(p) for p in _READ_FILES
             if _HASH_BEFORE[p] != _HASH_AFTER[p]), [])
check("10f ...non-degeneracy: the four hashes are not all one value, so 10e is "
      "not one file compared with itself",
      len(set(_HASH_BEFORE.values())), len(_READ_FILES))

shutil.rmtree(_TMP, ignore_errors=True)
check("10g the scratch tree was removed", os.path.exists(_TMP), False)


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
Created on Mon Sep  8 2026

@author: ramyalsaffar
"""
