# The Evaluation Sample's Output Names Derive From The Count That Owns Them
###########################################################################

"""Evaluation Sample Naming Test

``oncotriage/evaluation/sampling.py`` wrote the sample size into two module
constants as literals -- ``SAMPLE_DB_SUBDIR = "03- 30 Samples db"`` and
``SAMPLE_DB_FILENAME = "inferences_sample_30.db"`` -- and
``28- Select Evaluation Sample.py`` retyped the same number into its argparse
description and quoted the whole path in its ``--output-db`` help. Five
declarations of one fact, none of them checked.

THAT IS THE DEFECT PASS 20e ALREADY ARGUED ABOUT THIS FILE AND DID NOT FINISH.
The entry point was renamed from ``28- Select 30 Samples.py`` because "the old
name baked the SAMPLE SIZE into a filename, which is the one thing about this
file most likely to change" -- and the two constants and the two rendered
strings went on carrying it. Widening the draw to 20 per group would have
written 60 patients into ``inferences_sample_30.db``, under a ``--help`` still
advertising the 30 path, with nothing raising: ``select_samples`` asserts its
output's CONTENTS against the ALLOCATION and never its name.

WHAT THIS FILE HOLDS

  1  SAMPLE_TOTAL IS A RULING AND NO LONGER A PRODUCT, as of the
     cohort-stratification pass. It was ``PATIENTS_PER_CANCER *
     len(CANCER_TYPES)`` -- ten each of breast, colon and lung -- and that
     THREE-GROUP VOCABULARY WAS THE DEFECT: fitted to a retired corpus in
     which everything else was one patient, it excluded 289 of the current
     1,000 and RAISED whenever a group held fewer than ten. The draw is now
     proportional across every group the source database carries, so there is
     no "per cancer" number to multiply and the derivation this section pinned
     has no subject. What it pins instead is that the two NAMES are still
     rendered from the total rather than retyped, which is the property this
     whole file exists for and which the change did not touch.

  2  VALUE PRESERVATION, which is the acceptance criterion the change was made
     under: at today's constants both derived strings are BYTE-IDENTICAL to
     the historical ones. This is the check that would have refused the change
     had the derivation been wrong, and it is here rather than in a scratch
     script so it keeps refusing.

  3  DERIVATION, NOT COINCIDENCE. A doubled count puts 60 in BOTH names. Driven
     two ways, because either alone is weak: through the builders with an
     explicit total (behaviour), and by AST -- the two constants are assigned
     from no-argument calls to those same builders, and the builders' only
     string source is the two format constants. Composition then gives the
     property the behavioural half cannot reach on its own: doubling
     PATIENTS_PER_CANCER doubles both CONSTANTS, not merely both builders.

  4  The parameterised default and its CACHE. ``_RESOLVED`` used to key on the
     fixed string ``"output_db"``; a parameterised function keyed on a constant
     serves the first caller's count to every later one. Section 4c drives both
     orders and section 4d builds the OLD fixed-key cache beside it and
     requires it to cross-serve -- without that control, 4c would pass against
     a cache that had simply stopped caching.

  5  The entry point renders from the sampler's names. Patched at the ENTRY
     POINT's own namespace, never at ``oncotriage.evaluation.sampling`` --
     ``from ... import SAMPLE_TOTAL`` binds the value into the importing
     module, so a test that patched the sampler would reach nothing and pass
     forever. That is this project's recorded patch-point lesson
     (tests/test_agent_rrf_config_ownership.py section 2), applied to a
     different seam.

  6  THE PLANTS. A restored ``"30"`` literal in an in-memory copy of each of
     the two files must FIRE -- four of them, one per spelling this pass
     removed.

TWO LIMITS, STATED RATHER THAN GLOSSED.

  Section 2 pins today's strings as LITERALS, so a deliberate widening of the
  draw fails here and somebody has to edit this file. That is the intended
  shape, not an oversight -- it is the golden-snapshot arrangement
  (tests/test_agent_user_message_snapshot.py) applied to two filenames: the
  output location of a shipped artifact must not move because a constant was
  nudged. The expectation is written out rather than recomputed from the same
  constants the code uses, or it would agree with the code by construction.

  Section 4 resolves against a SCRATCH results root, so it proves the
  RELATIVE location and the join, never the absolute historical path on a
  machine that has the sibling tree. A conditional check that skipped on a
  runner would be worse; the absolute path was verified by running
  `default_output_db()` when the derivation was introduced.

  The count scan matches the total as a standalone number, so it would also
  fire on a positional prefix that happened to BE the count -- "30- Foo" at a
  total of 30. It is not: the prefix is "03- ", and "03" is not "30".

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO MODEL, NO CORPUS, NO
DATABASE, NO GIT HISTORY, NO SIBLING DATA TREE. ``paths._RESOLVED`` is seeded
with a scratch results root -- the seam
``tests/test_agent_degraded_run_and_reporting.py`` already uses -- so no glob
fires and the checks hold on a runner with only the directory skeleton. Nothing
is opened, created or removed: section 4e asserts that after resolving three
destinations, none of their directories exists.

IT EXECS NOTHING, so it needs no ``_EXEC_ALLOWLIST`` entry: every plant is an
``ast`` walk over an in-memory copy of the source, and the two files it READS
are written by neither of the collision matrix's two writers
(``oncotriage/registries/cancer_code_registry.py`` and
``oncotriage/config.py``), so it is not in the matrix either.

    python tests/test_evaluation_sample_naming.py
"""

import ast
import importlib
import io
import os
import re
import shutil
import sys
import tempfile

# ABOVE THE PACKAGE IMPORTS ON PURPOSE -- oncotriage/agent/deps.py reads this
# once, at its own import. The sampler does not reach deps, but the entry point
# imported in section 5 puts the whole package on the same interpreter.
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

from oncotriage import paths
from oncotriage.registries import primary_cancer as _pc
from oncotriage.evaluation import sampling


# ===========================================================================
# HARNESS
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


def check_true(label: str, condition) -> None:
    check(label, bool(condition), True)


def section(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def drive(fn, *args, **kwargs):
    """Call into production code and turn a raise into a value check() fails on.

    A bare call would let a planted exception escape while check()'s ARGUMENT
    was being evaluated, and the run would report one traceback where it owed a
    summary. This project has shipped that shape seven times; see CLAUDE.md.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                    # noqa: BLE001 -- recorded
        return f"<RAISED {type(exc).__name__}: {exc}>"


# The two files under inspection, located through the modules that own them so
# a future move cannot point this file at a same-named copy.
_SAMPLING_PATH = os.path.abspath(sampling.__file__)
_CODE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_SAMPLING_PATH)))
_ENTRY_NAME = "28- Select Evaluation Sample"
_ENTRY_PATH = os.path.join(_CODE_DIR, _ENTRY_NAME + ".py")

if not os.path.isfile(_ENTRY_PATH):                              # hard guard
    raise SystemExit(
        f"cannot locate the entry point at {_ENTRY_PATH!r}; a wrong root here "
        f"is not one failure but every failure, each with a misleading message")

_SAMPLING_SRC = io.open(_SAMPLING_PATH, encoding="utf-8").read()
_ENTRY_SRC = io.open(_ENTRY_PATH, encoding="utf-8").read()

# THE HISTORICAL STRINGS, WRITTEN OUT ONCE. These are the bytes on disk today
# (`{results_path}/03- 30 Samples db/inferences_sample_30.db`) and the whole
# point of section 2 is that the derivation reproduces them exactly. They are
# literals here BECAUSE this is the place that must not derive them: a check
# that computed its own expectation from the same constants the code uses would
# agree with the code by construction.
_HISTORICAL_SUBDIR = "03- 30 Samples db"
_HISTORICAL_FILENAME = "inferences_sample_30.db"
_HISTORICAL_TOTAL = 30


# ===========================================================================
# A SCRATCH RESULTS ROOT
# ===========================================================================
# paths._RESOLVED is the documented seam. Seeding it means no glob fires, so
# this file needs no sibling data tree and holds on a CI runner.

_SCRATCH = tempfile.mkdtemp(prefix="oncotriage-sample-naming-")
_PATHS_HAD = "results_path" in paths._RESOLVED
_PATHS_WAS = paths._RESOLVED.get("results_path")
_SAMPLING_RESOLVED_WAS = dict(sampling._RESOLVED)


def _reset_caches():
    """A fresh scratch results root and an empty sampler cache."""
    paths._RESOLVED["results_path"] = _SCRATCH
    sampling._RESOLVED.clear()


_reset_caches()


# ===========================================================================
# SECTION 1 -- the derivation, and that it is not degenerate
# ===========================================================================
section("SECTION 1 -- SAMPLE_TOTAL is the product of the two constants")

check("SAMPLE_TOTAL is today's 30 -- the value both names render",
      sampling.SAMPLE_TOTAL, _HISTORICAL_TOTAL)

# THE TWO RETIRED CONSTANTS ARE GONE, NOT MERELY UNUSED. `CANCER_TYPES` was the
# second cancer grouping vocabulary in this package and
# tests/test_cancer_grouping_single_owner.py is the standing pin that no second
# one comes back; this is the half of that pin that belongs here, because a
# reintroduced `PATIENTS_PER_CANCER` would also reintroduce the per-group draw
# whose raise this pass removed.
check_true("PATIENTS_PER_CANCER is gone",
           not hasattr(sampling, "PATIENTS_PER_CANCER"))
check_true("CANCER_TYPES is gone -- one grouping owner",
           not hasattr(sampling, "CANCER_TYPES"))
check("classify_cancer IS the one owner's function, by identity",
      sampling.classify_cancer is _pc.cancer_group_key, True)

# _resolve_total is the one place `None` becomes the module derivation.
check("_resolve_total(None) is the module derivation",
      drive(sampling._resolve_total, None), sampling.SAMPLE_TOTAL)
check("_resolve_total coerces a numeric string",
      drive(sampling._resolve_total, "60"), 60)
check("_resolve_total coerces a float, so no name carries '.0'",
      drive(sampling._resolve_total, 60.0), 60)
_bad = str(drive(sampling._resolve_total, "twenty"))
check_true("_resolve_total RAISES on a value that cannot be a count",
           _bad.startswith("<RAISED ValueError"))
check_true("...and the message names what the argument is FOR, not just the "
           "value int() choked on -- int()'s own text says neither",
           "sample total" in _bad and "twenty" in _bad)


# ===========================================================================
# SECTION 2 -- VALUE PRESERVING: byte-identical to the historical strings
# ===========================================================================
section("SECTION 2 -- the derived names equal today's strings, byte for byte")

check("SAMPLE_DB_SUBDIR is byte-identical to the historical directory name",
      sampling.SAMPLE_DB_SUBDIR, _HISTORICAL_SUBDIR)
check("SAMPLE_DB_FILENAME is byte-identical to the historical filename",
      sampling.SAMPLE_DB_FILENAME, _HISTORICAL_FILENAME)
check("the builders called with no argument agree with the constants",
      (drive(sampling.sample_db_subdir), drive(sampling.sample_db_filename)),
      (_HISTORICAL_SUBDIR, _HISTORICAL_FILENAME))
check("...and called with the total explicitly",
      (drive(sampling.sample_db_subdir, _HISTORICAL_TOTAL),
       drive(sampling.sample_db_filename, _HISTORICAL_TOTAL)),
      (_HISTORICAL_SUBDIR, _HISTORICAL_FILENAME))

# NON-DEGENERACY: the equality above is only interesting because the strings
# genuinely carry the count. Two empty strings are also byte-identical.
check_true("NON-DEGENERATE: the historical subdir really carries the count",
           str(_HISTORICAL_TOTAL) in _HISTORICAL_SUBDIR)
check_true("NON-DEGENERATE: so does the historical filename",
           str(_HISTORICAL_TOTAL) in _HISTORICAL_FILENAME)

# THE "03- " PREFIX IS POSITIONAL SIBLING NUMBERING, NOT THE COUNT. It must
# survive a change of count untouched -- deriving it would be inventing a
# relationship that does not exist.
check_true("the '03- ' prefix is a literal and survives a different count",
           drive(sampling.sample_db_subdir, 60).startswith("03- "))


# ===========================================================================
# SECTION 3 -- derivation, not coincidence
# ===========================================================================
section("SECTION 3 -- a doubled count puts 60 in both names")

check("a doubled count in the subdir", drive(sampling.sample_db_subdir, 60),
      "03- 60 Samples db")
check("a doubled count in the filename",
      drive(sampling.sample_db_filename, 60), "inferences_sample_60.db")
check_true("NON-DEGENERATE: the doubled names differ from today's",
           drive(sampling.sample_db_subdir, 60) != _HISTORICAL_SUBDIR
           and drive(sampling.sample_db_filename, 60) != _HISTORICAL_FILENAME)

# --- 3b: the CONSTANTS are the builders' output, by AST -------------------
#
# The behavioural half above proves the BUILDERS derive. It cannot prove the
# two module constants do -- a module that kept `SAMPLE_DB_SUBDIR = "03- 30
# Samples db"` beside working builders passes every check so far. This links
# them without exec'ing a patched copy of the module.
_SAMPLING_TREE = ast.parse(_SAMPLING_SRC)


def _module_assign(tree, name):
    """The module-scope value node assigned to `name`, or None."""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
    return None


def _call_of(node):
    """(callee name, arg count) for a bare Call, else None."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id, len(node.args) + len(node.keywords)
    return None


check("SAMPLE_DB_SUBDIR is assigned from sample_db_subdir() with no argument",
      _call_of(_module_assign(_SAMPLING_TREE, "SAMPLE_DB_SUBDIR")),
      ("sample_db_subdir", 0))
check("SAMPLE_DB_FILENAME is assigned from sample_db_filename() with no "
      "argument",
      _call_of(_module_assign(_SAMPLING_TREE, "SAMPLE_DB_FILENAME")),
      ("sample_db_filename", 0))
# SAMPLE_TOTAL IS NOW A LITERAL AND THAT IS THE CHANGE RATHER THAN A
# REGRESSION -- see section 1. It used to be pinned as an ast.BinOp, on the
# argument that a product cannot go stale against the constants that produce
# it; with a proportional draw there are no such constants. What must still
# hold, and is what this file is actually about, is that the two NAMES are
# rendered from it rather than retyped, which the checks above and below pin.
check("SAMPLE_TOTAL is a plain integer ruling",
      isinstance(_module_assign(_SAMPLING_TREE, "SAMPLE_TOTAL"), ast.Constant)
      and isinstance(sampling.SAMPLE_TOTAL, int), True)

# ...and the builders' only string source is the two format constants.
_FUNC_BY_NAME = {n.name: n for n in _SAMPLING_TREE.body
                 if isinstance(n, ast.FunctionDef)}


def _string_constants(node, skip_docstring=True):
    """Every non-docstring str Constant under `node`, innermost included."""
    doc = ast.get_docstring(node, clean=False) if skip_docstring else None
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            if doc is not None and sub.value == doc:
                continue
            out.append(sub.value)
    return out


for _builder in ("sample_db_subdir", "sample_db_filename"):
    check_true(f"{_builder} spells no string of its own",
               _builder in _FUNC_BY_NAME
               and _string_constants(_FUNC_BY_NAME[_builder]) == [])

check("the two format constants are the module's only spelling of the names",
      sorted(n for n in (
          t.id for node in _SAMPLING_TREE.body if isinstance(node, ast.Assign)
          for t in node.targets if isinstance(t, ast.Name))
          if n.endswith("_FORMAT")),
      ["_SAMPLE_DB_FILENAME_FORMAT", "_SAMPLE_DB_SUBDIR_FORMAT"])


# ===========================================================================
# SECTION 4 -- the parameterised default, and the cache
# ===========================================================================
section("SECTION 4 -- default_output_db(total) and its count-keyed cache")

_EXPECTED_30 = os.path.join(_SCRATCH, _HISTORICAL_SUBDIR, _HISTORICAL_FILENAME)
_EXPECTED_60 = os.path.join(_SCRATCH, "03- 60 Samples db",
                            "inferences_sample_60.db")

_reset_caches()
check("4a. the no-argument default is the historical relative location",
      drive(sampling.default_output_db), _EXPECTED_30)
check("4a. an explicit default count gives the same path",
      drive(sampling.default_output_db, _HISTORICAL_TOTAL), _EXPECTED_30)
check("4b. a non-default count gives a truthfully named destination",
      drive(sampling.default_output_db, 60), _EXPECTED_60)
check_true("NON-DEGENERATE: the two destinations differ",
           _EXPECTED_30 != _EXPECTED_60)

# --- 4c: the cache cannot serve one count's path for another ---------------
#
# Both orders, because the old fixed-key cache failed in exactly one of them
# and a single-order check would have passed against it half the time.
_reset_caches()
_first_60 = drive(sampling.default_output_db, 60)
_then_default = drive(sampling.default_output_db)
check("4c. 60 first, then the default: the default is still the 30 path",
      _then_default, _EXPECTED_30)
check("4c. ...and the 60 call was right too", _first_60, _EXPECTED_60)

_reset_caches()
_first_default = drive(sampling.default_output_db)
_then_60 = drive(sampling.default_output_db, 60)
check("4c. default first, then 60: 60 is not served the 30 path",
      _then_60, _EXPECTED_60)
check("4c. ...and the default call was right too", _first_default,
      _EXPECTED_30)
check("4c. the cache holds one entry per count",
      sorted(sampling._RESOLVED), [("output_db", 30), ("output_db", 60)])

# --- 4d: THE CONTROL -- the OLD fixed-key cache does cross-serve -----------
#
# Without this, 4c would be satisfied by a function that had simply stopped
# caching, which is a different (and undetected) regression.
_old_cache = {}


def _old_default_output_db(total=None):
    """default_output_db as it was: parameterised, keyed on a fixed string."""
    resolved = sampling.SAMPLE_TOTAL if total is None else int(total)
    if "output_db" not in _old_cache:
        _old_cache["output_db"] = os.path.join(
            paths.results_path, sampling.sample_db_subdir(resolved),
            sampling.sample_db_filename(resolved))
    return _old_cache["output_db"]


_old_first = _old_default_output_db(60)
_old_then = _old_default_output_db()
check("4d. CONTROL: the old fixed-key cache serves 60's path to the default",
      _old_then, _EXPECTED_60)
check_true("4d. CONTROL: ...which is the wrong answer, so 4c discriminates",
           _old_then != _EXPECTED_30 and _old_first == _EXPECTED_60)

# --- 4e: it creates nothing ------------------------------------------------
check_true("4e. no destination directory was created",
           not os.path.exists(os.path.dirname(_EXPECTED_30))
           and not os.path.exists(os.path.dirname(_EXPECTED_60)))
check_true("4e. no destination file was created",
           not os.path.exists(_EXPECTED_30)
           and not os.path.exists(_EXPECTED_60))
check("4e. the scratch results root is still empty", os.listdir(_SCRATCH), [])


# ===========================================================================
# SECTION 5 -- the entry point renders from the sampler's names
# ===========================================================================
section("SECTION 5 -- --help cannot drift from the default")

_reset_caches()

if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

# A module name with a space and a leading digit imports fine through
# importlib -- the path finder does not require a valid identifier, only a
# file it can locate. The __main__ guard means nothing runs.
_entry = importlib.import_module(_ENTRY_NAME)


def _render_help(module):
    """argparse's --help text for that module's parser, as a string."""
    buf = io.StringIO()
    stdout_was = sys.stdout
    sys.stdout = buf
    try:
        module._parse_args(["--help"])
    except SystemExit:
        pass
    finally:
        sys.stdout = stdout_was
    return buf.getvalue()


def _squeeze(text):
    """argparse wraps, and it wraps MID-TOKEN on a long word. Collapsing all
    whitespace is what makes a wrapped path comparable -- the lesson from
    tests/test_trivyignore_staleness.py, where an assertion held only because
    the real path happened to wrap at a space."""
    return re.sub(r"\s+", "", text)


_help_now = _render_help(_entry)
check_true("5a. the help quotes the derived subdir",
           _squeeze(_HISTORICAL_SUBDIR) in _squeeze(_help_now))
check_true("5a. the help quotes the derived filename",
           _squeeze(_HISTORICAL_FILENAME) in _squeeze(_help_now))
check_true("5a. the description carries the derived total",
           _squeeze(f"stratified {_HISTORICAL_TOTAL}-patient")
           in _squeeze(_help_now))
check_true("5a. the {results_path} placeholder is kept verbatim, so --help "
           "fires no glob", "{results_path}" in _squeeze(_help_now))

# --- 5b: patched at the ENTRY POINT's namespace, which is the seam ---------
#
# `from ... import SAMPLE_TOTAL` binds the value into the importing module. A
# test that patched oncotriage.evaluation.sampling would reach nothing here and
# pass forever whatever the entry point rendered from.
_entry_was = (_entry.SAMPLE_TOTAL, _entry.SAMPLE_DB_SUBDIR,
              _entry.SAMPLE_DB_FILENAME)
try:
    _entry.SAMPLE_TOTAL = 60
    _entry.SAMPLE_DB_SUBDIR = "03- 60 Samples db"
    _entry.SAMPLE_DB_FILENAME = "inferences_sample_60.db"
    _help_60 = _render_help(_entry)
finally:
    (_entry.SAMPLE_TOTAL, _entry.SAMPLE_DB_SUBDIR,
     _entry.SAMPLE_DB_FILENAME) = _entry_was

check_true("5b. the help MOVES when the sampler's names move",
           _squeeze("03- 60 Samplesdb/inferences_sample_60.db")
           in _squeeze(_help_60)
           and _squeeze("stratified 60-patient") in _squeeze(_help_60))
check_true("5b. ...and no trace of the old count is left in it",
           _squeeze(_HISTORICAL_FILENAME) not in _squeeze(_help_60)
           and _squeeze(f"stratified {_HISTORICAL_TOTAL}-patient")
           not in _squeeze(_help_60))
check("5b. the restore put the entry point's names back",
      (_entry.SAMPLE_TOTAL, _entry.SAMPLE_DB_SUBDIR,
       _entry.SAMPLE_DB_FILENAME), _entry_was)
check("5b. CONTROL: the wrong patch point -- rendering again after the "
      "restore reproduces the original help exactly",
      _render_help(_entry), _help_now)

# The entry point imports all three names and READS all three, which is what
# section 5 of tests/test_package_invariants.py requires of a thin entry point.
_ENTRY_TREE = ast.parse(_ENTRY_SRC)
_entry_imported = sorted(
    a.name for n in ast.walk(_ENTRY_TREE) if isinstance(n, ast.ImportFrom)
    and (n.module or "").startswith("oncotriage.evaluation.sampling")
    for a in n.names)
check("5c. the entry point imports the three derived names",
      [n for n in _entry_imported if n.startswith("SAMPLE")],
      ["SAMPLE_DB_FILENAME", "SAMPLE_DB_SUBDIR", "SAMPLE_TOTAL"])
_entry_loads = {n.id for n in ast.walk(_ENTRY_TREE) if isinstance(n, ast.Name)
                and isinstance(n.ctx, ast.Load)}
check("5c. ...and reads all three, so none is a re-export",
      sorted(n for n in _entry_imported if n.startswith("SAMPLE")
             and n in _entry_loads),
      ["SAMPLE_DB_FILENAME", "SAMPLE_DB_SUBDIR", "SAMPLE_TOTAL"])


# ===========================================================================
# SECTION 6 -- no surviving literal, and the plants that prove it
# ===========================================================================
section("SECTION 6 -- a restored '30' literal is caught")

_COUNT_RE = re.compile(rf"(?<!\d){_HISTORICAL_TOTAL}(?!\d)")


def _module_scope_strings(tree):
    """Every string Constant reachable from a MODULE-SCOPE statement, with the
    module docstring and every nested definition's body excluded.

    Scoped to module scope on purpose: select_samples' console output contains
    `{'='*60}`, so a whole-file digit scan would be a check whose outcome
    depends on which number the count happens to be.
    """
    doc = ast.get_docstring(tree, clean=False)
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                if doc is not None and sub.value == doc:
                    continue
                out.append(sub.value)
    return out


def _offending(strings):
    return sorted(s for s in strings if _COUNT_RE.search(s))


_sampling_scope_strings = _module_scope_strings(_SAMPLING_TREE)
check("6a. no module-scope string in the sampler spells the count",
      _offending(_sampling_scope_strings), [])
check_true("NON-DEGENERATE: the scan does look at strings",
           len(_sampling_scope_strings) > 3)


def _parse_args_strings(tree):
    """Every string Constant inside _parse_args, docstring excluded."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_parse_args":
            return _string_constants(node)
    return None


_entry_arg_strings = _parse_args_strings(_ENTRY_TREE)
check_true("6b. _parse_args was found in the entry point",
           _entry_arg_strings is not None)
check("6b. no string in _parse_args spells the count",
      _offending(_entry_arg_strings or []), [])
check_true("NON-DEGENERATE: _parse_args does contain strings",
           len(_entry_arg_strings or []) > 3)

# --- THE PLANTS ------------------------------------------------------------
#
# Each restores one of the three literals this pass removed, into an in-memory
# copy. Nothing on disk is touched.
_PLANTS = (
    ("6c. a restored SAMPLE_DB_SUBDIR literal",
     _SAMPLING_SRC,
     "SAMPLE_DB_SUBDIR = sample_db_subdir()",
     'SAMPLE_DB_SUBDIR = "03- 30 Samples db"',
     _module_scope_strings),
    ("6d. a restored SAMPLE_DB_FILENAME literal",
     _SAMPLING_SRC,
     "SAMPLE_DB_FILENAME = sample_db_filename()",
     'SAMPLE_DB_FILENAME = "inferences_sample_30.db"',
     _module_scope_strings),
    ("6e. a restored --output-db help literal",
     _ENTRY_SRC,
     'f"{{results_path}}/{SAMPLE_DB_SUBDIR}/{SAMPLE_DB_FILENAME}"',
     '"{results_path}/03- 30 Samples db/inferences_sample_30.db"',
     lambda tree: _parse_args_strings(tree) or []),
)

for _label, _src, _needle, _replacement, _collect in _PLANTS:
    if _src.count(_needle) != 1:
        check(f"{_label}: PLANT ANCHOR (expected exactly one occurrence)",
              _src.count(_needle), 1)
        continue
    _planted = ast.parse(_src.replace(_needle, _replacement))
    check_true(f"CONTROL: {_label} is caught",
               _offending(_collect(_planted)) != [])

# A fourth, on the description -- the one the AST scan reaches through a
# JoinedStr rather than a bare Constant.
_desc_needle = 'f"Extract a seeded, stratified {SAMPLE_TOTAL}-patient "'
if _ENTRY_SRC.count(_desc_needle) == 1:
    _planted_desc = ast.parse(_ENTRY_SRC.replace(
        _desc_needle, '"Extract a seeded, stratified 30-patient "'))
    check_true("CONTROL: 6f. a restored description literal is caught",
               _offending(_parse_args_strings(_planted_desc) or []) != [])
else:
    check("6f. PLANT ANCHOR (expected exactly one occurrence)",
          _ENTRY_SRC.count(_desc_needle), 1)

# NON-DEGENERACY OF THE PLANT MECHANISM ITSELF: the unplanted sources must be
# clean under the same collector the plants are judged by, or every CONTROL
# above would fire for a reason unrelated to the plant.
check("the unplanted sampler is clean under the plant collector",
      _offending(_module_scope_strings(ast.parse(_SAMPLING_SRC))), [])
check("the unplanted entry point is clean under the plant collector",
      _offending(_parse_args_strings(ast.parse(_ENTRY_SRC)) or []), [])


# ===========================================================================
# SECTION 7 -- the same defect in the other module that draws this sample
# ===========================================================================
section("SECTION 7 -- medcpt_calibration renders its total, it does not retype it")

# oncotriage/evaluation/medcpt_calibration.py draws the SAME stratum as the
# sampler -- it imports SEED and classify_cancer from it -- and its
# --sample-total help renders the total rather than retyping it. It used to say
# "(default 10, so 30 total)" with the 30 written out, so raising the count
# would have left the flag's own help stating a total the run does not draw.
# The per-group draw both files shared is gone (see section 1), so its
# SAMPLE_TOTAL is a ruling here too. It is checked HERE rather than in a file
# of its own because the fact is the same fact and this is the file that owns
# it; medcpt_calibration itself is bucket C (a live Qdrant index), so nothing
# else can reach it for free.

from oncotriage.evaluation import medcpt_calibration as _mc      # noqa: E402

_MC_PATH = os.path.abspath(_mc.__file__)
_MC_SRC = io.open(_MC_PATH, encoding="utf-8").read()
_MC_TREE = ast.parse(_MC_SRC)

check("7a. its SAMPLE_TOTAL equals the sampler's -- both draw one stratum",
      _mc.SAMPLE_TOTAL, sampling.SAMPLE_TOTAL)
check("7a. ...and is a plain integer ruling, like the sampler's",
      isinstance(_module_assign(_MC_TREE, "SAMPLE_TOTAL"), ast.Constant), True)
check("7a. it reads the ONE grouper rather than a vocabulary of its own",
      _mc.classify_cancer is _pc.cancer_group_key, True)
check("7a. and its retired per-group constant is gone",
      hasattr(_mc, "PATIENTS_PER_CANCER"), False)


def _main_strings(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return _string_constants(node)
    return None


_mc_main_strings = _main_strings(_MC_TREE)
check_true("7b. main() was found", _mc_main_strings is not None)
check("7b. no string in main() spells the total",
      _offending(_mc_main_strings or []), [])
check_true("NON-DEGENERATE: main() does contain strings",
           len(_mc_main_strings or []) > 3)

_mc_needle = 'f"(default {SAMPLE_TOTAL})")'
if _MC_SRC.count(_mc_needle) == 1:
    _mc_planted = ast.parse(_MC_SRC.replace(_mc_needle, '"30 total)")'))
    check_true("CONTROL: 7c. a restored total literal in the help is caught",
               _offending(_main_strings(_mc_planted) or []) != [])
else:
    check("7c. PLANT ANCHOR (expected exactly one occurrence)",
          _MC_SRC.count(_mc_needle), 1)


# ===========================================================================
# RESTORE
# ===========================================================================

if _PATHS_HAD:
    paths._RESOLVED["results_path"] = _PATHS_WAS
else:
    paths._RESOLVED.pop("results_path", None)
sampling._RESOLVED.clear()
sampling._RESOLVED.update(_SAMPLING_RESOLVED_WAS)
shutil.rmtree(_SCRATCH, ignore_errors=True)

check("the results_path seam was restored exactly",
      ("results_path" in paths._RESOLVED,
       paths._RESOLVED.get("results_path")), (_PATHS_HAD, _PATHS_WAS))
check("the sampler cache was restored exactly",
      dict(sampling._RESOLVED), _SAMPLING_RESOLVED_WAS)
check_true("the scratch directory is gone", not os.path.exists(_SCRATCH))
check("none of the three source files was written",
      (io.open(_SAMPLING_PATH, encoding="utf-8").read() == _SAMPLING_SRC,
       io.open(_ENTRY_PATH, encoding="utf-8").read() == _ENTRY_SRC,
       io.open(_MC_PATH, encoding="utf-8").read() == _MC_SRC),
      (True, True, True))


# ===========================================================================
# SUMMARY
# ===========================================================================

print()
print("=" * 74)
print("SUMMARY")
print("=" * 74)
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
Created on Fri Aug 21 2026

@author: ramyalsaffar
"""
