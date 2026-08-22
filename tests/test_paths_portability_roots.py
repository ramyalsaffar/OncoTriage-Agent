# Path Portability Roots Test
############################

"""
Two directory roots used to be resolved OUTSIDE ``oncotriage/paths.py``, and
four temporary files and two model caches used to be written outside the
project root altogether. This file is the standing guard on all of it.

WHAT WAS WRONG
--------------
``oncotriage/fixtures/capture.py:fixture_root()`` and
``oncotriage/evaluation/run_harness.py:evaluation_root()`` each carried a
private ``sorted(glob.glob(main_path + "/*Testing"))`` and, when that matched
nothing, INVENTED ``main_path + "/09- Testing"``. Three consequences, all of
them silent:

  * a wrong or unset project root sent twelve captured fixtures -- each one a
    real, billed end-to-end run -- or a paid evaluation campaign's manifest and
    every per-patient record, into a directory nobody was looking at, and the
    run reported success;
  * two ``*Testing`` siblings resolved by ``sorted()[0]`` with no ambiguity
    guard, which is the nondeterminism pass 20f-1 removed from every other path
    in this project;
  * neither root was in ``PATH_NAMES``, so neither was in the Docker table and
    neither was in the CI skeleton -- a container and a CI checkout each got a
    silently invented directory instead of the loud failure every other path
    gives them.

Four ``tempfile.mkstemp`` calls in ``capture.py`` wrote DERIVED PATIENT BUNDLES
-- hundreds of megabytes each, and the exact input a captured fixture claims to
have been produced from -- into ``TMPDIR``. The MedCPT cross-encoder cached
under the user's HOME and the FastEmbed BM25 encoder cached under
``tempfile.gettempdir()``, which on macOS is a PURGEABLE ``/var/folders``
directory.

WHAT THIS FILE HOLDS
--------------------
    1. TABLE MEMBERSHIP. The four promoted names are in ``_LOCAL_PATHS``, in
       ``_DOCKER_PATHS`` and in ``.github/scripts/provision_ci_paths.py``'s
       ``_skeleton()`` -- and, derived rather than listed, EVERY path variable
       is in all three. A name in one table and not another resolves on a
       developer machine and raises in CI or in a container.
    2. THE PROMOTED PATHS RESOLVE on a healthy tree, against a fabricated root
       rather than this machine's.
    3. A MISSING DIRECTORY RAISES, naming the pattern -- which is precisely
       what the two private resolvers refused to do.
    4. AN AMBIGUOUS MATCH RAISES, naming every candidate.
    5. THE TWO CALLERS READ THE PATH. ``fixture_root()`` and
       ``evaluation_root()`` keep their names (``replay.py`` imports the first)
       and now return the path variable; neither module carries a ``*Testing``
       glob or the invented-directory literal any more.
    6. THE DERIVED BUNDLES LAND INSIDE THE ROOT. Every ``mkstemp`` in
       ``capture.py`` passes ``dir=``, with a planted control; and the shipped
       ``rebuild_derived_bundle`` is DRIVEN, with a recording ``mkstemp``, to
       show where it actually asks for the file.
    7. THE MODEL CACHES ARE PINNED INSIDE THE ROOT, the environment wins when
       it is set, an empty value is not a setting, the answer is cached so the
       reported source stays a statement about who DECIDED, and the two
       subdirectory names are the Dockerfile's -- read out of the Dockerfile,
       not retyped.
    8. THE PIN HAPPENS ABOVE THE IMPORT at all three load sites, which is the
       only position that works for huggingface_hub.

NO NETWORK, NO KEYS, NO SPEND, NO MODEL LOAD (``ONCOTRIAGE_DEFER_LOCAL_MODELS``
is set above the imports and section 8 asserts ``torch`` and ``transformers``
never entered ``sys.modules``), NO LIVE QDRANT, NO CORPUS, NO DATABASE and NO
GIT HISTORY. It is NOT in the collision matrix: it writes only inside a
``tempfile.mkdtemp`` it removes, and the five repository files it reads
(``oncotriage/paths.py``, ``oncotriage/fixtures/capture.py``,
``oncotriage/evaluation/run_harness.py``, ``oncotriage/agent/deps.py``,
``oncotriage/embedding.py``) are written by neither of the suite's two writers.
It EXECS NOTHING -- every control is a different INPUT to a function, an ``ast``
walk over an in-memory copy, or an attribute rebound inside ``try``/``finally``
with the restore asserted -- so it needs no ``_EXEC_ALLOWLIST`` entry.

Run from terminal:
    python tests/test_paths_portability_roots.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# The package bootstrap every file in tests/ carries. The candidate directory
# is the PARENT of this file's, because the package sits beside tests/ rather
# than inside it. `pip install -e .` makes the whole block a no-op.
import os
import sys

# ABOVE THE PACKAGE IMPORTS, deliberately: oncotriage/agent/deps.py reads this
# once, at its own import, and an assignment underneath would reach nothing.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

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
import contextlib
import io
import re
import shutil
import tempfile

from oncotriage import paths as _paths


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
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {detail}")
    print(f"  FAIL  {label}\n          {detail}")


def raises(fn):
    """Return (exception type name, message) for a call that must raise.

    ('' , '') when it did not raise, so the caller records a FAILURE rather
    than the run aborting on the happy path -- the abort shape this project has
    shipped repeatedly and closes by never letting a raise escape through
    check()'s argument list.
    """
    try:
        fn()
    except BaseException as exc:          # noqa: BLE001 -- the type is the answer
        return type(exc).__name__, str(exc)
    return "", ""


def value_or(fn, marker="<raised>"):
    """Call `fn`, returning `marker` plus the exception name if it raises."""
    try:
        return fn()
    except BaseException as exc:          # noqa: BLE001
        return f"{marker}:{type(exc).__name__}"


_CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(_paths.__file__)))
_TMP_ROOT = tempfile.mkdtemp(prefix="oncotriage-portability-")


def _source(relative):
    return io.open(os.path.join(_CODE_DIR, relative), encoding="utf-8").read()


def _tree(*relative_dirs):
    """Build a throwaway project root containing exactly these directories."""
    root = tempfile.mkdtemp(dir=_TMP_ROOT)
    for relative in relative_dirs:
        os.makedirs(os.path.join(root, *relative.split("/")))
    return root


@contextlib.contextmanager
def _root_pointed_at(root):
    """Point every lazy path at `root` for the duration, then put it all back.

    `paths._RESOLVED` is the seam several files in this suite already use.
    Seeding BOTH `main_path` and `_main_path_source` is what makes
    `_resolve_root()` return early instead of re-reading the environment; the
    guard there is on the pair, which is why both are seeded.

    The two caller-side caches are cleared with it. They memoise the resolved
    root, so a value left over from an earlier section would make the next one
    report the previous section's tree.
    """
    saved = dict(_paths._RESOLVED)
    _paths._RESOLVED.clear()
    _paths._RESOLVED["main_path"] = root + os.sep
    _paths._RESOLVED["_main_path_source"] = "test harness"
    try:
        yield root
    finally:
        _paths._RESOLVED.clear()
        _paths._RESOLVED.update(saved)


@contextlib.contextmanager
def _clean_model_cache_state():
    """Empty pin record and no cache variables set, restored afterwards."""
    saved_pins = dict(_paths._MODEL_CACHE_PINS)
    saved_env = {name: os.environ.get(name)
                 for name in _paths.MODEL_CACHE_ENV_VARS}
    _paths._MODEL_CACHE_PINS.clear()
    for name in _paths.MODEL_CACHE_ENV_VARS:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        _paths._MODEL_CACHE_PINS.clear()
        _paths._MODEL_CACHE_PINS.update(saved_pins)
        for name, was in saved_env.items():
            if was is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = was


_HEALTHY = ("02- Data", "02- Data/01- Patients", "02- Data/01- Patients/fhir",
            "02- Data/02- Trials", "02- Data/03- Inferences Storage",
            "02- Data/04- MeSH", "02- Data/07- Model Cache",
            "09- Testing", "09- Testing/Characterization Fixtures",
            "09- Testing/Evaluation Runs")

_PROMOTED = ("testing_path", "testing_fixture_path", "testing_evaluation_path",
             "model_cache_path")


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 1: THE THREE TABLES AGREE, AND THE FOUR NEW NAMES ARE IN ALL OF THEM
# ===========================================================================
# oncotriage/paths.py cross-checks the local and Docker tables against each
# other AT IMPORT and raises when they disagree, so that half is already
# enforced by the module. What is NOT enforced anywhere the developer will see
# is the third table: `.github/scripts/provision_ci_paths.py:_skeleton()` only
# cross-checks itself at the end of its own main(), i.e. inside a CI job.
#
# The skeleton module is reached by putting `.github/scripts` on sys.path and
# importing it -- an ordinary import, the same shape provision_ci_paths.py
# itself uses to reach docker/prepare_paths.py. NOT a by-location load: section
# 1c of tests/test_package_invariants.py scans every .py in the repository for
# one, and this file must not be the exception.

print("=" * 74)
print("Section 1: the four promoted names are in all three path tables")
print("=" * 74)

_scripts_dir = os.path.join(_CODE_DIR, ".github", "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)
import provision_ci_paths as _ci                                   # noqa: E402

_skeleton = _ci._skeleton("/probe-root")

for _name in _PROMOTED:
    check(f"1a    {_name} is in _LOCAL_PATHS", _name in _paths._LOCAL_PATHS, True)
    check(f"1b    {_name} is in _DOCKER_PATHS", _name in _paths._DOCKER_PATHS, True)
    check(f"1c    {_name} is in the CI skeleton", _name in _skeleton, True)

# NON-DEGENERATE FIRST. Every check above is also satisfied by a table that
# happens to contain the name while being otherwise empty or stale, and the
# derived comparison below is satisfied for free by three empty dicts.
check("1d    the three tables are non-degenerate (>= 15 names each)",
      (len(_paths._LOCAL_PATHS) >= 15, len(_paths._DOCKER_PATHS) >= 15,
       len(_skeleton) >= 15),
      (True, True, True))

# DERIVED, NOT LISTED: the invariant is about every path variable, not about
# the four this pass added. `main_path` and `_main_path_source` are computed
# rather than globbed and have no directory to create, which is the same
# exemption provision_ci_paths.py's own cross-check makes.
_computed = {"main_path", "_main_path_source"}
_declared = set(_paths.PATH_NAMES) - _computed
check("1e    every path variable is in the local table",
      sorted(_declared - set(_paths._LOCAL_PATHS)), [])
check("1f    ...and in the Docker table",
      sorted(_declared - set(_paths._DOCKER_PATHS)), [])
check("1g    ...and in the CI skeleton",
      sorted(_declared - set(_skeleton)), [])
check("1h    ...and the CI skeleton declares nothing the package does not",
      sorted(set(_skeleton) - _declared), [])

# The Docker values follow the table's own directory/file convention, which
# docker/prepare_paths.py:_classify RAISES on rather than guessing. A new entry
# that ends without a separator and without an extension would fail the
# container at bring-up, in the entrypoint, before any service starts.
_bad_docker = sorted(n for n in _PROMOTED
                     if not _paths._DOCKER_PATHS[n].endswith("/"))
check("1i    every promoted Docker value ends with a separator, so _classify "
      "reads it as a directory", _bad_docker, [])


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 2: THE PROMOTED PATHS RESOLVE ON A HEALTHY TREE
# ===========================================================================
# Against a FABRICATED root rather than this machine's, so the section says
# something about the resolvers rather than about one developer's layout.

print()
print("=" * 74)
print("Section 2: the promoted paths resolve on a healthy fabricated tree")
print("=" * 74)

_healthy = _tree(*_HEALTHY)
with _root_pointed_at(_healthy):
    _resolved = {n: value_or(lambda n=n: getattr(_paths, n)) for n in _PROMOTED}

check("2a    testing_path resolves to the Testing tree",
      _resolved["testing_path"],
      os.path.join(_healthy, "09- Testing") + os.sep)
check("2b    testing_fixture_path resolves to the fixture directory",
      _resolved["testing_fixture_path"],
      os.path.join(_healthy, "09- Testing", "Characterization Fixtures") + os.sep)
check("2c    testing_evaluation_path resolves to the evaluation directory",
      _resolved["testing_evaluation_path"],
      os.path.join(_healthy, "09- Testing", "Evaluation Runs") + os.sep)
check("2d    model_cache_path resolves under the data tree",
      _resolved["model_cache_path"],
      os.path.join(_healthy, "02- Data", "07- Model Cache") + os.sep)

# NON-DEGENERACY: the restore really happened, so the sections below are not
# reading a temporary directory that no longer exists.
check("2e    the real root is back after the context manager",
      _paths._RESOLVED.get("main_path") in (None, _paths.main_path), True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 3: A MISSING DIRECTORY RAISES, NAMING THE PATTERN
# ===========================================================================
# This is the whole point of the promotion. Before it, a root with no
# `*Testing` sibling produced `{root}/09- Testing/Characterization Fixtures`
# out of thin air and a capture wrote twelve billed fixtures into it.

print()
print("=" * 74)
print("Section 3: a missing directory raises instead of being invented")
print("=" * 74)

_no_testing = _tree("02- Data", "02- Data/07- Model Cache")
with _root_pointed_at(_no_testing):
    _t3, _m3 = raises(lambda: _paths.testing_fixture_path)
    _t3b, _m3b = raises(lambda: _paths.testing_evaluation_path)
    _no_cache = _tree("02- Data")

check("3a    an absent Testing tree raises RuntimeError for the fixture root",
      _t3, "RuntimeError")
check("3b    ...and the message names the pattern that matched nothing",
      "*Testing/" in _m3, True)
check("3c    ...and it names the root in use",
      _no_testing in _m3, True)
check("3d    ...and it does NOT invent a directory anywhere in the message",
      "09- Testing/Characterization Fixtures" in _m3, False)
check("3e    the evaluation root raises the same way",
      (_t3b, "*Testing/" in _m3b), ("RuntimeError", True))

with _root_pointed_at(_no_cache):
    _t3f, _m3f = raises(lambda: _paths.model_cache_path)
check("3f    an absent Model Cache directory raises, naming its pattern",
      (_t3f, "*Model Cache/" in _m3f), ("RuntimeError", True))

# The second-level failure is distinguishable from the first. A Testing tree
# that exists but has no Characterization Fixtures inside it must name THAT
# pattern, not the parent's -- an operator whose message named the parent would
# go and look at a directory that is already there.
_half = _tree("02- Data", "09- Testing", "09- Testing/Evaluation Runs")
with _root_pointed_at(_half):
    _t3g, _m3g = raises(lambda: _paths.testing_fixture_path)
check("3g    a Testing tree missing only the fixture directory names the "
      "SUBdirectory pattern",
      (_t3g, "*Characterization Fixtures/" in _m3g), ("RuntimeError", True))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 4: AN AMBIGUOUS MATCH RAISES, NAMING EVERY CANDIDATE
# ===========================================================================
# The two private resolvers took sorted()[0] and said nothing. That is the
# nondeterminism pass 20f-1 removed from `_glob_one`, reintroduced by two
# functions that did not go through it.

print()
print("=" * 74)
print("Section 4: an ambiguous match raises and names every candidate")
print("=" * 74)

_ambiguous = _tree("02- Data", "02- Data/07- Model Cache",
                   "09- Testing", "10- Old Testing")
with _root_pointed_at(_ambiguous):
    _t4, _m4 = raises(lambda: _paths.testing_path)
check("4a    two matching Testing siblings raise RuntimeError",
      _t4, "RuntimeError")
check("4b    ...the message says how many matched", "2 directories matched" in _m4, True)
check("4c    ...it names the first candidate",
      os.path.join(_ambiguous, "09- Testing") in _m4, True)
check("4d    ...and the second, so the ambiguity is visible rather than "
      "silently resolved", os.path.join(_ambiguous, "10- Old Testing") in _m4, True)
check("4e    ...and it names which one the pre-guard code would have taken",
      "would have resolved to" in _m4, True)

_ambiguous_sub = _tree("02- Data", "09- Testing",
                       "09- Testing/Characterization Fixtures",
                       "09- Testing/Archived Characterization Fixtures")
with _root_pointed_at(_ambiguous_sub):
    _t4f, _m4f = raises(lambda: _paths.testing_fixture_path)
check("4f    two matching fixture subdirectories raise too",
      (_t4f, "2 directories matched" in _m4f), ("RuntimeError", True))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 5: THE TWO CALLERS READ THE PATH VARIABLE
# ===========================================================================

print()
print("=" * 74)
print("Section 5: fixture_root() and evaluation_root() read the path variable")
print("=" * 74)

from oncotriage.evaluation import run_harness as _harness            # noqa: E402
from oncotriage.fixtures import capture as _capture                  # noqa: E402

_capture._RESOLVED.pop("fixture_root", None)
_harness._RESOLVED.pop("evaluation_root", None)
_healthy2 = _tree(*_HEALTHY)
with _root_pointed_at(_healthy2):
    _fixture_answer = value_or(_capture.fixture_root)
    _eval_answer = value_or(_harness.evaluation_root)
_capture._RESOLVED.pop("fixture_root", None)
_harness._RESOLVED.pop("evaluation_root", None)

check("5a    fixture_root() returns testing_fixture_path",
      _fixture_answer,
      os.path.join(_healthy2, "09- Testing", "Characterization Fixtures") + os.sep)
check("5b    evaluation_root() returns testing_evaluation_path",
      _eval_answer,
      os.path.join(_healthy2, "09- Testing", "Evaluation Runs") + os.sep)

# THE PUBLIC NAMES SURVIVE, because oncotriage/fixtures/replay.py imports
# fixture_root and section 5 of tests/test_package_invariants.py requires every
# imported name to exist.
check("5c    both public names still exist and are callable",
      (callable(getattr(_capture, "fixture_root", None)),
       callable(getattr(_harness, "evaluation_root", None))),
      (True, True))

# AND THE PRIVATE MACHINERY IS GONE FROM BOTH FILES, checked over string
# CONSTANTS by ast so that the prose recording the fix -- which necessarily
# quotes the old pattern -- does not satisfy or defeat the check. This is the
# repository's own lesson about grepping a file that argues about itself.
def _string_constants(source):
    """Every non-docstring string constant in a module, as a list."""
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


_capture_strings = _string_constants(_source("oncotriage/fixtures/capture.py"))
_harness_strings = _string_constants(_source("oncotriage/evaluation/run_harness.py"))

check("5d    capture.py has no live '*Testing' glob pattern",
      [s for s in _capture_strings if s == "*Testing"], [])
check("5e    capture.py has no invented-directory literal",
      [s for s in _capture_strings if "09- Testing" in s], [])
check("5f    run_harness.py has no live '*Testing' glob pattern",
      [s for s in _harness_strings if s == "*Testing"], [])
check("5g    run_harness.py has no invented-directory literal",
      [s for s in _harness_strings if "09- Testing" in s], [])

# NON-DEGENERACY, in both directions. The four checks above are satisfied by an
# extractor that returns nothing at all, and by one that cannot tell a
# docstring from a live constant.
check("5h    the extractor sees live constants (non-degeneracy)",
      ("Characterization Fixtures" not in _capture_strings,
       len(_capture_strings) > 200, len(_harness_strings) > 200),
      (True, True, True))
check("5i    ...and it excludes docstrings, where the old pattern is quoted "
      "(non-degeneracy)",
      ("*Testing" in _source("oncotriage/evaluation/run_harness.py"),
       "*Testing" in _harness_strings),
      (True, False))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 6: DERIVED BUNDLES LAND INSIDE THE PROJECT ROOT
# ===========================================================================
# A derived bundle is a rewritten copy of a Synthea record -- hundreds of
# megabytes -- and it is the exact input a captured fixture claims to have been
# produced from. All four mkstemp calls took tempfile's default, which is
# TMPDIR: outside the project root, and on macOS a purgeable /var/folders
# directory.

print()
print("=" * 74)
print("Section 6: every derived bundle is created inside the project tree")
print("=" * 74)


def _mkstemp_calls(source):
    return [n for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Call)
            and getattr(n.func, "attr", None) == "mkstemp"]


def _dir_kwarg(call):
    for kw in call.keywords:
        if kw.arg == "dir":
            return kw
    return None


_capture_src = _source("oncotriage/fixtures/capture.py")
_calls = _mkstemp_calls(_capture_src)
check("6a    capture.py still makes four mkstemp calls (non-degeneracy)",
      len(_calls), 4)
check("6b    every one of them passes dir=",
      sorted(c.lineno for c in _calls if _dir_kwarg(c) is None), [])
check("6c    ...and every dir= is _derived_bundle_dir(...), so there is one "
      "owner of the answer rather than four",
      sorted({getattr(_dir_kwarg(c).value.func, "id", None) for c in _calls
              if _dir_kwarg(c) is not None}),
      ["_derived_bundle_dir"])

# THE CONTROL. Strip dir= from one call in an in-memory copy and require 6b to
# report it. Nothing on disk is touched: the plant is a string, parsed.
_planted = _capture_src.replace(
    '        suffix=".bundle.json", dir=_derived_bundle_dir(root)\n', "", 1)
if _planted == _capture_src:
    _planted = _capture_src.replace(
        "                                             dir=_derived_bundle_dir(root))",
        "                                             )", 1)
if _planted == _capture_src:
    fail("6d    the control plant matched something",
         "no mkstemp dir= form was found to strip; the plant is stale")
else:
    _planted_calls = _mkstemp_calls(_planted)
    check("6d    a copy with one dir= removed is reported (control)",
          len([c for c in _planted_calls if _dir_kwarg(c) is None]) >= 1, True)
    check("6e    ...and the copy still parses to four calls, so the control "
          "removed a keyword rather than a call (non-degeneracy)",
          len(_planted_calls), 4)

check("6f    the shipped file is untouched by the control",
      _source("oncotriage/fixtures/capture.py") == _capture_src, True)

# THE BEHAVIOURAL HALF. The shipped rebuild_derived_bundle is driven with a
# recording mkstemp and a fabricated fixture. apply_derivation raises on the
# unknown recipe AFTER the temp file is asked for, which is exactly the point
# in the function this section is about.
_behaviour_root = _tree(*_HEALTHY)
_donor_dir = os.path.join(_behaviour_root, "02- Data", "01- Patients", "fhir")
_donor_name = "donor.json"
io.open(os.path.join(_donor_dir, _donor_name), "w", encoding="utf-8").write("{}")

_recorded = {}
_real_mkstemp = _capture.tempfile.mkstemp


def _recording_mkstemp(*args, **kwargs):
    _recorded.update(kwargs)
    return _real_mkstemp(*args, **kwargs)


_capture._RESOLVED.pop("fixture_root", None)
with _root_pointed_at(_behaviour_root):
    _paths._RESOLVED["data_fhir_path"] = _donor_dir + os.sep
    _capture.tempfile.mkstemp = _recording_mkstemp
    try:
        _t6, _m6 = raises(lambda: _capture.rebuild_derived_bundle({
            "fixture_id": "probe",
            "derivation": {"donor_bundle": _donor_name,
                           "recipe": "not-a-real-recipe", "params": {}},
        }))
    finally:
        _capture.tempfile.mkstemp = _real_mkstemp
_capture._RESOLVED.pop("fixture_root", None)

check("6g    the seam was in force and the real mkstemp is back",
      _capture.tempfile.mkstemp is _real_mkstemp, True)
check("6h    rebuild_derived_bundle got as far as asking for a temp file "
      "(non-degeneracy)", "dir" in _recorded, True)
check("6i    ...and it asked for it inside the fabricated project root",
      str(_recorded.get("dir", "")).startswith(_behaviour_root), True)
check("6j    ...specifically inside the fixture directory",
      os.path.normpath(str(_recorded.get("dir", ""))),
      os.path.normpath(os.path.join(_behaviour_root, "09- Testing",
                                    "Characterization Fixtures")))
check("6k    ...and it then failed on the unknown recipe, which is what put "
      "the raise after the mkstemp (non-degeneracy)",
      _t6, "ValueError")
# THE HONEST FORM OF "not in the system temporary directory". The fabricated
# root necessarily lives under `tempfile.gettempdir()` here -- the harness has
# nowhere else it may write -- so `startswith(gettempdir())` is True for the
# right answer as well as the wrong one, and the first version of this check
# failed for exactly that reason. What discriminates is that the directory is
# not the one `mkstemp()` WITH NO `dir=` would have chosen, which is
# `gettempdir()` itself.
check("6l    the directory is not the one a bare mkstemp() would have used",
      os.path.normpath(str(_recorded.get("dir", "!")))
      == os.path.normpath(tempfile.gettempdir()), False)
check("6m    ...stated the other way: it is strictly BELOW the project root, "
      "which a bare mkstemp() can never be",
      os.path.commonpath([str(_recorded.get("dir", "/")), _behaviour_root])
      == os.path.normpath(_behaviour_root), True)

# `dir=` is where the file was ASKED for; mkstemp created it. Clean up so the
# fabricated tree can be removed and so nothing survives this run.
for _leftover in os.listdir(os.path.join(_behaviour_root, "09- Testing",
                                         "Characterization Fixtures")):
    os.remove(os.path.join(_behaviour_root, "09- Testing",
                           "Characterization Fixtures", _leftover))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 7: THE MODEL CACHES ARE PINNED INSIDE THE ROOT
# ===========================================================================

print()
print("=" * 74)
print("Section 7: the model caches default inside the root, the environment wins")
print("=" * 74)

_cache_root = _tree(*_HEALTHY)

with _clean_model_cache_state():
    with _root_pointed_at(_cache_root):
        _hf = _paths.pin_model_cache(_paths.MODEL_CACHE_ENV_HF)
        _fe = _paths.pin_model_cache(_paths.MODEL_CACHE_ENV_FASTEMBED)
        _hf_dir = _paths.huggingface_cache_dir()
        _fe_dir = _paths.fastembed_cache_dir()
        _second = _paths.pin_model_cache(_paths.MODEL_CACHE_ENV_HF)
        _env_after = os.environ.get(_paths.MODEL_CACHE_ENV_HF)
        _pins = _paths.model_cache_pins()

    _cache_dir_root = os.path.join(_cache_root, "02- Data", "07- Model Cache") + os.sep
    check("7a    HF_HOME defaults inside the project root",
          _hf, (_paths.MODEL_CACHE_ENV_HF,
                os.path.join(_cache_dir_root, "huggingface"),
                _paths.MODEL_CACHE_SOURCE_PROJECT))
    check("7b    FASTEMBED_CACHE_PATH defaults inside the project root",
          _fe, (_paths.MODEL_CACHE_ENV_FASTEMBED,
                os.path.join(_cache_dir_root, "fastembed"),
                _paths.MODEL_CACHE_SOURCE_PROJECT))
    check("7c    both directories were created",
          (os.path.isdir(_hf[1]), os.path.isdir(_fe[1])), (True, True))
    check("7d    the environment variable was set too, which is what a "
          "subprocess inherits", _env_after, _hf[1])
    check("7e    a second call returns the recorded answer, so the source "
          "stays a statement about who DECIDED rather than about os.environ",
          _second, _hf)
    check("7f    model_cache_pins() reports both, and is a copy",
          (sorted(_pins), _pins is _paths._MODEL_CACHE_PINS),
          (sorted(_paths.MODEL_CACHE_ENV_VARS), False))

    # THE ARGUMENT, WHICH IS THE MECHANISM. huggingface_hub's own rule is
    # HF_HUB_CACHE = HF_HOME + "/hub"; a cache_dir that disagreed with the
    # exported variable would put one process's download somewhere else.
    check("7g    huggingface_cache_dir() is HF_HOME + /hub, so the argument "
          "and the exported variable name ONE location",
          _hf_dir, os.path.join(_hf[1], _paths.MODEL_CACHE_HUB_SUBDIR))
    check("7h    fastembed_cache_dir() is the root itself, because fastembed's "
          "argument and variable mean the same thing", _fe_dir, _fe[1])
    check("7i    both cache_dir answers are inside the project root",
          (str(_hf_dir).startswith(_cache_root),
           str(_fe_dir).startswith(_cache_root)), (True, True))

# THE ENVIRONMENT WINS, and it wins WITHOUT RESOLVING A PATH. The root here is
# a tree with no Model Cache directory at all, so a function that resolved
# first and consulted the environment second would raise -- which is what makes
# this the escape hatch for a machine that has no such directory.
_explicit = os.path.join(_TMP_ROOT, "operator-chosen-cache")
_bare = _tree("02- Data")
with _clean_model_cache_state():
    os.environ[_paths.MODEL_CACHE_ENV_HF] = _explicit
    with _root_pointed_at(_bare):
        _answer = value_or(lambda: _paths.pin_model_cache(_paths.MODEL_CACHE_ENV_HF))
        _arg = value_or(_paths.huggingface_cache_dir)
    check("7j    an operator-set HF_HOME is respected verbatim",
          _answer, (_paths.MODEL_CACHE_ENV_HF, _explicit,
                    _paths.MODEL_CACHE_SOURCE_ENVIRONMENT))
    check("7k    ...and huggingface_cache_dir() returns None, so the library's "
          "own resolution is left alone rather than second-guessed",
          _arg, None)
    check("7l    ...and nothing was created at it, because the project did not "
          "choose it", os.path.isdir(_explicit), False)

# HF_HUB_CACHE OUTRANKS HF_HOME IN huggingface_hub's OWN RESOLUTION, so an
# operator who set only that one has decided too. This module honours it and
# NEVER writes it.
_hub_only = os.path.join(_TMP_ROOT, "operator-hub-cache")
with _clean_model_cache_state():
    _saved_hub = os.environ.get(_paths.MODEL_CACHE_ENV_HF_HUB)
    os.environ[_paths.MODEL_CACHE_ENV_HF_HUB] = _hub_only
    try:
        with _root_pointed_at(_bare):
            _hub_answer = value_or(
                lambda: _paths.pin_model_cache(_paths.MODEL_CACHE_ENV_HF))
            _hub_arg = value_or(_paths.huggingface_cache_dir)
            _hf_home_written = os.environ.get(_paths.MODEL_CACHE_ENV_HF)
    finally:
        if _saved_hub is None:
            os.environ.pop(_paths.MODEL_CACHE_ENV_HF_HUB, None)
        else:
            os.environ[_paths.MODEL_CACHE_ENV_HF_HUB] = _saved_hub
    check("7m    HF_HUB_CACHE alone counts as the operator deciding",
          _hub_answer, (_paths.MODEL_CACHE_ENV_HF_HUB, _hub_only,
                        _paths.MODEL_CACHE_SOURCE_ENVIRONMENT))
    check("7n    ...huggingface_cache_dir() defers", _hub_arg, None)
    check("7o    ...and HF_HOME was NOT written, because writing it would "
          "change nothing and misreport who decided", _hf_home_written, None)

# AN EMPTY VALUE IS NOT A SETTING. Honouring "" would hand huggingface_hub a
# root that resolves relative to the working directory.
with _clean_model_cache_state():
    os.environ[_paths.MODEL_CACHE_ENV_HF] = "   "
    with _root_pointed_at(_cache_root):
        _blank = value_or(lambda: _paths.pin_model_cache(_paths.MODEL_CACHE_ENV_HF))
    check("7p    a blank HF_HOME is treated as unset",
          _blank[2] if isinstance(_blank, tuple) else _blank,
          _paths.MODEL_CACHE_SOURCE_PROJECT)

# THE SOURCE VOCABULARY IS CLOSED, so a caller may branch on it exhaustively
# -- the same standing `deps.RESOLUTION_STATES` has, and the same reason it is
# checked: a closed set nothing consults is a declaration, not a contract.
check("7q1   every source this module can report is in MODEL_CACHE_SOURCES",
      sorted({_hf[2], _fe[2], _answer[2], _hub_answer[2]})
      == sorted(set(_paths.MODEL_CACHE_SOURCES)), True)
check("7q2   ...and that set is exactly the two members, so an exhaustive "
      "branch on it is complete",
      sorted(_paths.MODEL_CACHE_SOURCES),
      sorted({_paths.MODEL_CACHE_SOURCE_ENVIRONMENT,
              _paths.MODEL_CACHE_SOURCE_PROJECT}))

with _clean_model_cache_state():
    _t7, _m7 = raises(lambda: _paths.pin_model_cache("PYTHONPATH"))
    check("7q    an unknown variable raises rather than being pinned",
          (_t7, "PYTHONPATH" in _m7), ("KeyError", True))
    check("7r    ...and the closed set is named in the message",
          _paths.MODEL_CACHE_ENV_HF in _m7, True)

# THE SUBDIRECTORY NAMES ARE THE DOCKERFILE'S, READ RATHER THAN RETYPED. If the
# two ever diverge, a cache written on the host and one written in the
# container have different shapes and neither is a drop-in for the other.
_dockerfile = _source("Dockerfile")
_docker_env = {
    var: (re.search(rf"^\s*{var}=(\S+)", _dockerfile, re.M) or [None, None])[1]
    for var in _paths.MODEL_CACHE_ENV_VARS
}
check("7s    the Dockerfile sets both cache variables (non-degeneracy)",
      sorted(k for k, v in _docker_env.items() if v),
      sorted(_paths.MODEL_CACHE_ENV_VARS))
check("7t    ...and each one's last component is the subdirectory this module "
      "appends",
      {var: os.path.basename(value.rstrip("/\\"))
       for var, value in _docker_env.items() if value},
      dict(_paths.MODEL_CACHE_SUBDIRS))
check("7u    ...and their common parent is the container's model_cache_path",
      sorted({os.path.dirname(v.rstrip("/\\")) + "/"
              for v in _docker_env.values() if v}),
      [_paths._DOCKER_PATHS["model_cache_path"]])


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 8: THE CACHE DIRECTORY REACHES THE LIBRARY AS AN ARGUMENT
# ===========================================================================
# THE ENVIRONMENT VARIABLE IS NOT THE MECHANISM FOR HUGGINGFACE, AND THIS
# SECTION MEASURES WHY RATHER THAN ASSERTING IT. huggingface_hub reads HF_HOME
# ONCE, at its own import, into module constants -- and in this project it is
# already imported before any pipeline code runs, because `qdrant_client`
# imports `fastembed` at module scope and `fastembed` imports
# `huggingface_hub` at module scope. So a pass that only exported HF_HOME would
# have reported a pinned cache, changed nothing, and gone on downloading into
# the user's home. That is the silent false report this project treats as worse
# than a failure, and the first draft of this pass shipped exactly it; the
# measurement below is what caught it.
#
# `cache_dir=` is resolved by transformers at CALL time and outranks every
# variable, so THAT is what the load sites pass.

print()
print("=" * 74)
print("Section 8: the cache directory reaches the library as an argument")
print("=" * 74)

import huggingface_hub.constants as _hf_constants                    # noqa: E402

check("8a    huggingface_hub is already imported before any factory runs, "
      "which is the whole reason the variable cannot be the mechanism",
      ("huggingface_hub" in sys.modules, "fastembed" in sys.modules),
      (True, True))

with _clean_model_cache_state():
    _before = _hf_constants.HF_HUB_CACHE
    os.environ[_paths.MODEL_CACHE_ENV_HF] = os.path.join(_TMP_ROOT, "late-pin")
    _after = _hf_constants.HF_HUB_CACHE
check("8b    setting HF_HOME after that import moves huggingface_hub's cache "
      "root not at all (MEASURED)", _after, _before)
check("8c    ...and the value it kept is non-empty, so 8b is not comparing "
      "two absences (non-degeneracy)", bool(_before), True)


def _calls_named(source, function_name, callee_attr=None, callee_id=None):
    """Every Call node inside `function_name` whose callee matches."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef) or node.name != function_name:
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            if callee_attr and getattr(inner.func, "attr", None) == callee_attr:
                out.append(inner)
            if callee_id and getattr(inner.func, "id", None) == callee_id:
                out.append(inner)
    return out


def _spreads_cache_kwargs(call):
    """True when the call spreads a mapping built from the cache decision."""
    return any(kw.arg is None and getattr(kw.value, "id", "").endswith("_kwargs")
               for kw in call.keywords)


_deps_src = _source("oncotriage/agent/deps.py")
_embed_src = _source("oncotriage/embedding.py")

for _fn in ("_build_medcpt_tokenizer", "_build_medcpt_model"):
    _loads = _calls_named(_deps_src, _fn, callee_attr="from_pretrained")
    check(f"8d    {_fn} makes exactly one from_pretrained call "
          "(non-degeneracy)", len(_loads), 1)
    if _loads:
        check(f"8e    ...and it is handed the cache decision in {_fn}",
              _spreads_cache_kwargs(_loads[0]), True)
    _decide = _calls_named(_deps_src, _fn, callee_id="_huggingface_cache_kwargs")
    check(f"8f    ...and {_fn} asks for that decision exactly once",
          len(_decide), 1)

_construct = _calls_named(_embed_src, "get_bm25_sparse_model",
                          callee_id="SparseTextEmbedding")
check("8g    get_bm25_sparse_model makes exactly one SparseTextEmbedding call "
      "(non-degeneracy)", len(_construct), 1)
if _construct:
    check("8h    ...and it is handed a cache_dir when the project decided",
          any(kw.arg is None for kw in _construct[0].keywords), True)
check("8i    ...and it asks oncotriage.paths for that decision",
      len(_calls_named(_embed_src, "get_bm25_sparse_model",
                       callee_attr="fastembed_cache_dir")), 1)

# THE CONTROL: strip the spread from an in-memory copy and require 8e to
# report it. Without it these checks pass for a walker that cannot see a
# keyword at all.
_control = _deps_src.replace(
    "    tokenizer = AutoTokenizer.from_pretrained("
    "config.CROSS_ENCODER_MODEL,\n"
    "                                              **_cache_kwargs)",
    "    tokenizer = AutoTokenizer.from_pretrained("
    "config.CROSS_ENCODER_MODEL)", 1)
if _control == _deps_src:
    fail("8j    the control plant matched something",
         "the tokenizer load was not found to strip; the plant is stale")
else:
    _control_loads = _calls_named(_control, "_build_medcpt_tokenizer",
                                  callee_attr="from_pretrained")
    check("8j    a copy whose tokenizer load drops the cache decision is "
          "reported (control)",
          _spreads_cache_kwargs(_control_loads[0]) if _control_loads else None,
          False)

check("8k    the shipped deps.py is untouched by the control",
      _source("oncotriage/agent/deps.py") == _deps_src, True)

# THE VARIABLE IS STILL SET AND THE PIN STILL SITS ABOVE THE IMPORT. Neither is
# the mechanism any more, and both are still worth holding: the variable is
# what a subprocess inherits and what keeps the host arrangement the same shape
# as the container's, and pinning above the import is the rule that stays
# correct if a future release of either library goes back to reading the
# environment at import.
def _pin_and_import_lines(source, function_name, module_name):
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef) or node.name != function_name:
            continue
        pin = imp = None
        for inner in ast.walk(node):
            if (pin is None and isinstance(inner, ast.Call)
                    and (getattr(inner.func, "id", None)
                         in ("_huggingface_cache_kwargs", "pin_model_cache")
                         or getattr(inner.func, "attr", None) == "pin_model_cache")):
                pin = inner.lineno
            if (imp is None and isinstance(inner, ast.ImportFrom)
                    and inner.module == module_name):
                imp = inner.lineno
        return pin, imp
    return None, None


for _fn in ("_build_medcpt_tokenizer", "_build_medcpt_model"):
    _pin, _imp = _pin_and_import_lines(_deps_src, _fn, "transformers")
    check(f"8l    {_fn} pins and imports (non-degeneracy)",
          (_pin is not None, _imp is not None), (True, True))
    if _pin is not None and _imp is not None:
        check(f"8m    ...and the pin is ABOVE the import in {_fn}", _pin < _imp, True)

_pin, _imp = _pin_and_import_lines(_embed_src, "get_bm25_sparse_model", "fastembed")
check("8n    get_bm25_sparse_model pins and imports (non-degeneracy)",
      (_pin is not None, _imp is not None), (True, True))
if _pin is not None and _imp is not None:
    check("8o    ...and the pin is ABOVE the import", _pin < _imp, True)

# NO MODEL WAS LOADED BY ANY OF THE ABOVE. `fastembed` and `huggingface_hub`
# are deliberately NOT on this list: qdrant_client imports both at module
# scope, which is the fact section 8 is about. torch and transformers are what
# a MedCPT load would drag in, and neither is here.
check("8p    torch and transformers never entered sys.modules",
      sorted(m for m in ("torch", "transformers") if m in sys.modules), [])


#------------------------------------------------------------------------------


shutil.rmtree(_TMP_ROOT, ignore_errors=True)
check("9a    the scratch tree was removed", os.path.exists(_TMP_ROOT), False)


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
Created on Sat Aug 22 2026

@author: ramyalsaffar
"""
