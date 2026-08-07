# Degraded Dependency Test
##########################

"""
Degraded Dependency Test  (item 11a)

Item 11a made four paths that used to degrade silently either RAISE or COUNT.
This file is the demonstration the project's testing rule demands: every new
assertion is shown to FAIL when the condition it checks is broken, and shown to
PASS when it is not, so that no check here can be satisfied vacuously.

WHAT ITEM 11a CHANGED, and why each one is the shape it is
-----------------------------------------------------------
RAISE (a missing FILE or PACKAGE — a configuration defect one command fixes):

  1. ``registries/mesh.py:load_mesh_filter()`` returned None when
     mesh_c04_lookup.json / mesh_tree_to_name.json were absent. The None reached
     Stage 4 and every trial passed the cancer site filter for the whole run.
  2. ``registries/cancer_code_registry.py:_build_icd10_cancer_sets()`` caught
     ImportError, called logger.error and returned three EMPTY sets, so the
     entire ICD-10-CM layer vanished while the registry logged "ready". Item 11a
     also added the EMPTY-BUT-INSTALLED guard: a release that imports and yields
     no primary codes fails the same way, because the old handler could not have
     seen it and neither could the new raise on its own.

COUNT (third-party DATA — nothing an operator can fix, and raising would turn a
per-trial degradation into a per-patient outage):

  3. ``agent/patient.py:_normalize_lab_unit`` had THREE silent exits, not one,
     and only the third is the one the exception audit was worried about. All
     three are counted apart in ``LAB_UNIT_DEGRADATIONS``.
  4. an unparseable trial min_age / max_age at filter time
     (``agent/filtering.py:AGE_PARSE_FAILURES``). The RECOVERY IS UNCHANGED —
     the trial is kept — because changing which trials survive is a different
     decision, and it would break the twelve characterization fixtures without
     being an improvement.

     THE INDEX-TIME MIRROR IS GONE. ``retrieval/indexer.py`` used to carry
     ``INDEX_AGE_PARSE_FAILURES`` beside a ``min_age > 18`` skip; that skip was
     an exactly-18 filter and was deleted, so the counter had nothing left to
     record. Its ABSENCE is now asserted, along with the absence of any age
     comparison in the scraper's executable code.

Plus two behaviour changes with no exception behind them:

  5. ``extract_patient_histology`` is called UNCONDITIONALLY in Stage 4. It used
     to sit inside ``if mesh_filter is not None:``, so a missing MeSH file
     disabled the histology filter too — two unrelated checks wired to one
     file's presence, with ``histology_dropped = 0`` reported either way.
  6. ``fhir/clean.py:filter_cancer_patients_inplace(dry_run=True)`` reports
     exactly what it would delete and deletes nothing, and the same module now
     REFUSES to delete on a degraded registry however
     ONCOTRIAGE_ALLOW_DEGRADED_REGISTRIES is set.

Sections:
    1. The non-degraded path. Every raise below is shown NOT to fire with the
       files and the package present, so no later section can pass vacuously.
    2. The opt-out variable itself: every accepted spelling, both directions,
       and an unrecognised value raising rather than being read as "off".
    3. MeSH — raises with the files absent, degrades with the variable set.
    4. ICD-10 — raises with the package absent, raises when the package is
       present but yields nothing, degrades with the variable set.
    5. The deletion path refuses a degraded registry WITH THE VARIABLE SET, and
       does not refuse an intact one.
    6. dry_run deletes nothing, against a copy of a real cohort, with a real
       run on a second copy as the negative control.
    7. The lab-unit counters distinguish all three exits.
    8. The age-parse counters at filter time and at index time.
    9. STRUCTURAL — histology is computed outside the mesh_filter guard, the
       Stage 4 result dict is unchanged, and the File 05 shim still binds
       exactly fourteen names.

No network, no LLM, no API key, no Qdrant. Sections 5 and 6 copy real patient
bundles into a scratch directory under the system temp dir and operate only on
the copy; the production corpus is never opened for writing and its file count
is asserted unchanged at the end.

Run from terminal (or F5 in Spyder):
    python tests/test_degraded_dependencies.py
    (was: python "48- Degraded Dependency Test.py")

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures

CONCURRENCY: safe to run beside anything. It edits no file in the repository —
every degraded state is produced by shadowing a cached module attribute or by
planting a module in sys.modules, both restored in a finally block — so it does
not belong in run_serial_tests.py's collision matrix with Files 42, 43, 44
and 47.
"""


# Run needed file
#----------------
import ast
import collections
import hashlib
import inspect
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import types


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

from oncotriage import paths, settings
from oncotriage.agent import filtering, patient
from oncotriage.fhir import clean
from oncotriage.registries import cancer_code_registry as ccr
from oncotriage.registries import mesh
from oncotriage.retrieval import indexer


# PASS 20d-2: the repository root, derived from the PACKAGE's own location
# rather than from this file's. `oncotriage/__init__.py` -> `oncotriage/` -> the
# code directory. This file already imports the package unconditionally above,
# so there is no cost to asking it where it is, and the answer cannot be one
# directory off the way a __file__-relative guess became when this file moved
# into tests/. It also names the tree this process actually imported, which a
# hand-built path cannot promise.
_CODE_DIR = os.path.dirname(
    os.path.dirname(os.path.abspath(oncotriage.__file__))) + os.sep


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
    handler — the defect `tests/test_fhir_ecog_surfacing.py` (was File 39) had to
    argue about in its own harness.
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
    everything", and a guard that fires on everything is worse than none: it
    would stop the pipeline on a correctly configured machine.
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


# ===========================================================================
# CONTEXT MANAGERS THAT PRODUCE A DEGRADED STATE WITHOUT EDITING A FILE
# ===========================================================================
#
# NOTHING IN THE REPOSITORY OR THE DATA TREE IS MUTATED. Files 43 and 44 edit
# source in place and hash the restore because their subject IS the source text;
# this file's subject is runtime behaviour, so the project's stated preference —
# mutate a copy, or shadow, rather than edit in place — applies directly.

class env_var:
    """Set (or clear, with value=None) an environment variable for a block."""

    def __init__(self, name, value):
        self._name = name
        self._value = value
        self._previous = None
        self._was_set = False

    def __enter__(self):
        self._was_set = self._name in os.environ
        self._previous = os.environ.get(self._name)
        if self._value is None:
            os.environ.pop(self._name, None)
        else:
            os.environ[self._name] = self._value
        return self

    def __exit__(self, *exc):
        if self._was_set:
            os.environ[self._name] = self._previous
        else:
            os.environ.pop(self._name, None)
        return False


class mesh_dir_empty:
    """Point paths.data_MeSH_path at an empty directory for a block.

    Shadows the LAZY CACHE rather than moving the real files. `oncotriage.paths`
    resolves each path on first read into `paths._RESOLVED`; writing the key
    directly is what a caller would see, and restoring the previous entry
    afterwards leaves the module exactly as it was. Renaming the real MeSH
    directory would work too and would put a 19,415-entry crosswalk one crash
    away from staying renamed.
    """

    def __init__(self):
        self._tmp = None
        self._had = False
        self._previous = None

    def __enter__(self):
        self._tmp = tempfile.mkdtemp(prefix="oncotriage-mesh-absent-")
        self._had = "data_MeSH_path" in paths._RESOLVED
        self._previous = paths._RESOLVED.get("data_MeSH_path")
        paths._RESOLVED["data_MeSH_path"] = self._tmp + os.sep
        return self._tmp

    def __exit__(self, *exc):
        if self._had:
            paths._RESOLVED["data_MeSH_path"] = self._previous
        else:
            paths._RESOLVED.pop("data_MeSH_path", None)
        shutil.rmtree(self._tmp, ignore_errors=True)
        return False


class module_unimportable:
    """Make `import <name>` raise ImportError inside a block.

    `sys.modules[name] = None` is the documented CPython behaviour for this:
    the import system finds the None and raises ImportError rather than
    returning it. It is the only way to reach the handler under test without
    uninstalling the package from the machine running the test.
    """

    def __init__(self, name):
        self._name = name
        self._had = False
        self._previous = None

    def __enter__(self):
        self._had = self._name in sys.modules
        self._previous = sys.modules.get(self._name)
        sys.modules[self._name] = None
        return self

    def __exit__(self, *exc):
        if self._had:
            sys.modules[self._name] = self._previous
        else:
            sys.modules.pop(self._name, None)
        return False


class module_replaced:
    """Install a stand-in module under `name` for a block."""

    def __init__(self, name, module):
        self._name = name
        self._module = module
        self._had = False
        self._previous = None

    def __enter__(self):
        self._had = self._name in sys.modules
        self._previous = sys.modules.get(self._name)
        sys.modules[self._name] = self._module
        return self._module

    def __exit__(self, *exc):
        if self._had:
            sys.modules[self._name] = self._previous
        else:
            sys.modules.pop(self._name, None)
        return False


class captured_warnings:
    """Collect WARNING-level records from a named logger for a block.

    The WARNING is half the contract of degraded mode — the item's wording is
    "logs loudly naming exactly which layer is absent" — so it is asserted on
    rather than assumed from the fact that a logger.warning call is in the
    source.
    """

    def __init__(self, logger_name):
        self._logger = logging.getLogger(logger_name)
        self._records = []
        self._handler = None
        self._previous_level = None
        self._previous_propagate = None

    def __enter__(self):
        records = self._records

        class _Collector(logging.Handler):
            def emit(self, record):
                records.append(record)

        self._handler = _Collector(level=logging.WARNING)
        self._previous_level = self._logger.level
        self._previous_propagate = self._logger.propagate
        self._logger.setLevel(logging.WARNING)
        # Do not also print them: this run deliberately produces warnings and
        # a console full of them would bury the PASS/FAIL lines.
        self._logger.propagate = False
        self._logger.addHandler(self._handler)
        return self

    def __exit__(self, *exc):
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._previous_level)
        self._logger.propagate = self._previous_propagate
        return False

    @property
    def messages(self):
        return [r.getMessage() for r in self._records
                if r.levelno >= logging.WARNING]


def quiet(fn, *args, **kwargs):
    """Run `fn`, swallowing its console output. Returns (value, captured_text).

    The degraded paths announce as well as log, and this file calls them a
    dozen times. Captured rather than discarded, so a section that wants to
    assert on the announced text still can.

    BOTH STREAMS, AND STDERR IS THE ONE THAT MATTERS NOW. This captured stdout
    alone until the structured-logging pass, which moved every human-facing
    line in the package onto ``oncotriage.observability``'s console channel --
    and that channel writes to stderr, because stdout is the MCP server's
    protocol stream and nothing this project writes may land there. With stdout
    alone the buffer came back EMPTY and "the console said nothing was deleted"
    failed against a dry run that had said exactly that.

    Both are taken rather than swapping one for the other: a future line that
    goes back to stdout should be caught by the assertion, not silently missed
    by a helper that has stopped looking there.
    """
    out_buffer, err_buffer = io.StringIO(), io.StringIO()
    previous_out, previous_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out_buffer, err_buffer
    try:
        value = fn(*args, **kwargs)
    finally:
        sys.stdout, sys.stderr = previous_out, previous_err
    return value, out_buffer.getvalue() + err_buffer.getvalue()


def sha256_of(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def tree_hashes(directory):
    """{filename: sha256} for every .json directly in `directory`."""
    return {name: sha256_of(os.path.join(directory, name))
            for name in sorted(os.listdir(directory))
            if name.endswith(".json")}


ENV = settings.ENV_ALLOW_DEGRADED_REGISTRIES


print("\n" + "=" * 78)
print("DEGRADED DEPENDENCY TEST — item 11a")
print("=" * 78)
print(f"  opt-out variable: {ENV}")
print(f"  currently set to: {os.environ.get(ENV, '(unset)')!r}")


# ===========================================================================
# 1. THE NON-DEGRADED PATH — nothing here fires when the data is present
# ===========================================================================
#
# FIRST, AND NON-NEGOTIABLE. Every raise this file demonstrates is demonstrated
# against a machine where the file or the package IS present, so a guard that
# fired unconditionally would fail here rather than passing sections 3 and 4 for
# the wrong reason. The item's Done-when names this explicitly: "demonstrated to
# fire ... AND demonstrated not to fire when it is present, so no check passes
# vacuously".

print("\n" + "=" * 78)
print("1. The non-degraded path: nothing raises, nothing is recorded")
print("=" * 78)

# The variable must be OFF for this section to mean anything: with it set, a
# raise would be suppressed and the absence of a raise would prove nothing.
with env_var(ENV, None):
    check("the opt-out is off for section 1",
          settings.resolve_allow_degraded_registries(), (False, None))

    _filter, _ = quiet(mesh.load_mesh_filter)
    check_true("load_mesh_filter returns a filter, not None", _filter is not None)
    check("and it is a MeSHCancerFilter",
          type(_filter).__name__, "MeSHCancerFilter")
    # NON-DEGENERATE: an object with no descriptors would satisfy "is not None"
    # and filter nothing.
    check_true("the filter carries C04 descriptors (non-degeneracy)",
               len(_filter.name_to_trees) > 100)

    # A TYPO IN THE VARIABLE MUST NOT TAKE DOWN A HEALTHY MACHINE. The resolver
    # raises on an unrecognised value (section 2), and the first draft of
    # _build_icd10_cancer_sets read it at the TOP of the function — so a stray
    # `export ONCOTRIAGE_ALLOW_DEGRADED_REGISTRIES=maybe` broke every registry
    # build on a machine where nothing was degraded, and disagreed with
    # load_mesh_filter(), which reads it inside its missing-file branch. Both
    # consult it only where a degradation is actually found.
    with env_var(ENV, "maybe"):
        check_does_not_raise("a nonsense opt-out value does not break an INTACT "
                             "ICD-10 build", ccr._build_icd10_cancer_sets)
        check_does_not_raise("nor an intact MeSH load",
                             lambda: quiet(mesh.load_mesh_filter))
    check("no core-layer degradation was recorded",
          mesh.MESH_FILTER_DEGRADATIONS[mesh.MESH_LAYER_CORE], 0)

    _primary, _secondary, _non_invasive, _degraded = _build = \
        ccr._build_icd10_cancer_sets()
    check("the ICD-10 build reports no degraded layer", _degraded, ())
    check_true("and it produced primary codes (non-degeneracy)",
               len(_primary) > 1000)
    check_true("and secondary codes", len(_secondary) > 0)
    check_true("and non-invasive codes", len(_non_invasive) > 0)

    _registry = ccr.load_registry()
    check("the shared registry reports no degraded layer",
          _registry.degraded_layers, ())
    check("no ICD-10 degradation was recorded",
          ccr.REGISTRY_DEGRADATIONS[ccr.ICD10_LAYER], 0)

    # And the deletion path's guard does not fire on it.
    check_does_not_raise("require_intact_registry() accepts the real registry",
                         clean.require_intact_registry)


# ===========================================================================
# 2. THE OPT-OUT VARIABLE
# ===========================================================================

print("\n" + "=" * 78)
print("2. The opt-out variable: both directions, and no silent third state")
print("=" * 78)

for _spelling in ("1", "true", "TRUE", "Yes", "on", " 1 "):
    with env_var(ENV, _spelling):
        check(f"{_spelling!r} permits a degraded run",
              settings.resolve_allow_degraded_registries(), (True, ENV))

for _spelling in ("0", "false", "No", "OFF"):
    with env_var(ENV, _spelling):
        check(f"{_spelling!r} forbids one",
              settings.resolve_allow_degraded_registries(), (False, ENV))

for _spelling in ("", "   "):
    with env_var(ENV, _spelling):
        check(f"{_spelling!r} is unset, so the default applies",
              settings.resolve_allow_degraded_registries(), (False, None))

# THE THIRD STATE MUST NOT EXIST. A value nobody meant read as "off" would leave
# an operator who typed it believing degradation was permitted while every run
# raised — and under any other default, believing it was forbidden while every
# run degraded.
for _bad in ("maybe", "$FLAG", "2", "y"):
    with env_var(ENV, _bad):
        check_raises(f"{_bad!r} raises rather than being read as off",
                     RuntimeError, settings.resolve_allow_degraded_registries)
        _msg = ""
        try:
            settings.resolve_allow_degraded_registries()
        except RuntimeError as _exc:
            _msg = str(_exc)
        check_true(f"and the message for {_bad!r} names the accepted values",
                   "true" in _msg and "false" in _msg and _bad in _msg)

# NOT ROUTED THROUGH _from_env, which is the whole reason resolve_* exists.
# Demonstrated rather than argued: _from_env would have appended a separator.
_via_from_env, _ = settings._from_env(ENV, "0")
check_true("_from_env would have corrupted the value (why it is not used)",
           _via_from_env == "1" + os.sep or _via_from_env == "0" + os.sep)
with env_var(ENV, "1"):
    _corrupted, _ = settings._from_env(ENV, "0")
    check("and specifically: _from_env('1') -> '1' + sep", _corrupted, "1" + os.sep)
    check_true("while the real resolver returns a bool",
               settings.resolve_allow_degraded_registries()[0] is True)


# ===========================================================================
# 3. MESH — RAISES WHEN THE CORE FILES ARE ABSENT
# ===========================================================================

print("\n" + "=" * 78)
print("3. MeSH: raises with the lookups absent, degrades with the variable set")
print("=" * 78)

with mesh_dir_empty() as _empty_dir:
    # NON-DEGENERATE: the directory really is empty, so the raise below is
    # caused by absence rather than by the shadow not having taken effect.
    check("the shadowed MeSH directory is empty (non-degeneracy)",
          os.listdir(_empty_dir), [])
    check("and load_mesh_filter reads the shadowed path",
          str(paths.data_MeSH_path).rstrip(os.sep), _empty_dir.rstrip(os.sep))

    with env_var(ENV, None):
        _exc = check_raises("missing core lookups raise DegradedDependencyError",
                            settings.DegradedDependencyError,
                            lambda: quiet(mesh.load_mesh_filter))
        if _exc is not None:
            _text = str(_exc)
            check_true("the message names mesh_c04_lookup.json",
                       "mesh_c04_lookup.json" in _text)
            check_true("and mesh_tree_to_name.json",
                       "mesh_tree_to_name.json" in _text)
            check_true("and the command that builds them",
                       "09- MeSH Cancer Site Relevance Filter.py" in _text)
            check_true("and the opt-out variable", ENV in _text)
            check_true("and says the opt-out does not reach the deletion path",
                       "clean.py" in _text)
            check("and it carries the layer name",
                  _exc.layer, mesh.MESH_LAYER_CORE)

        # NOT an ImportError or an OSError subclass: the raise replaces a
        # missing-file check, and code around it catches those.
        check_true("DegradedDependencyError is a RuntimeError",
                   issubclass(settings.DegradedDependencyError, RuntimeError))
        check_true("and NOT an OSError (a stray except OSError must not eat it)",
                   not issubclass(settings.DegradedDependencyError, OSError))
        check_true("and NOT an ImportError",
                   not issubclass(settings.DegradedDependencyError, ImportError))

    # --- Degraded mode: same input, variable set -------------------------
    _before = mesh.MESH_FILTER_DEGRADATIONS[mesh.MESH_LAYER_CORE]
    with env_var(ENV, "1"), captured_warnings(mesh.__name__) as _warnings:
        _result, _stdout = quiet(mesh.load_mesh_filter)

    check("with the variable set it returns None instead of raising",
          _result, None)
    check("and records the core layer exactly once",
          mesh.MESH_FILTER_DEGRADATIONS[mesh.MESH_LAYER_CORE], _before + 1)
    check_true("and logs at WARNING", len(_warnings.messages) >= 1)
    _joined = " ".join(_warnings.messages)
    check_true("naming the layer that is absent", mesh.MESH_LAYER_CORE in _joined)
    check_true("and the variable that permitted it", ENV in _joined)
    check_true("and saying what the consequence is (every trial passes)",
               "every trial" in _joined.lower())
    check_true("and it prints as well as logs", "DISABLED" in _stdout)

# The shadow is gone: the real filter loads again. This is the restore check —
# without it a later section could be running against a still-empty directory.
with env_var(ENV, None):
    _restored, _ = quiet(mesh.load_mesh_filter)
    check_true("outside the block the real filter loads again",
               _restored is not None)


# ===========================================================================
# 4. ICD-10 — RAISES WHEN THE PACKAGE IS ABSENT, AND WHEN IT ANSWERS EMPTY
# ===========================================================================

print("\n" + "=" * 78)
print("4. ICD-10: raises with the package absent and when it yields nothing")
print("=" * 78)

with module_unimportable("icd10"):
    # NON-DEGENERATE: the import really does fail now.
    def _try_import_icd10():
        import icd10  # noqa: F401
    check_raises("icd10 is genuinely unimportable in this block "
                 "(non-degeneracy)", ImportError, _try_import_icd10)

    with env_var(ENV, None):
        _exc = check_raises("a missing icd10-cm raises DegradedDependencyError",
                            settings.DegradedDependencyError,
                            ccr._build_icd10_cancer_sets)
        if _exc is not None:
            _text = str(_exc)
            check_true("the message names the package", "icd10-cm" in _text)
            check_true("and the install command",
                       "pip install icd10-cm" in _text)
            check_true("and the opt-out variable", ENV in _text)
            check_true("and says the EXCLUSION sets go too, not just the "
                       "primary ones", "EXCLUSION" in _text)
            check("and it carries the layer name", _exc.layer, ccr.ICD10_LAYER)

        # The registry constructor is the real call site and must raise too:
        # nothing catches it in between.
        check_raises("CancerCodeRegistry() raises rather than building empty",
                     settings.DegradedDependencyError, ccr.CancerCodeRegistry)

    # --- Degraded mode ----------------------------------------------------
    _before = ccr.REGISTRY_DEGRADATIONS[ccr.ICD10_LAYER]
    with env_var(ENV, "1"), captured_warnings(ccr.__name__) as _warnings:
        _p, _s, _n, _degraded = ccr._build_icd10_cancer_sets()

    check("with the variable set the build returns instead of raising",
          (_p, _s, _n), (set(), set(), set()))
    check("and names the absent layer", _degraded, (ccr.ICD10_LAYER,))
    check("and records it exactly once",
          ccr.REGISTRY_DEGRADATIONS[ccr.ICD10_LAYER], _before + 1)
    _joined = " ".join(_warnings.messages)
    check_true("and logs at WARNING naming the layer",
               ccr.ICD10_LAYER in _joined)
    check_true("and the variable that permitted it", ENV in _joined)

    # A registry built in degraded mode reports it, and says so in its own log.
    with env_var(ENV, "1"), captured_warnings(ccr.__name__) as _warnings:
        _degraded_registry = ccr.CancerCodeRegistry()
    check("a degraded registry reports the absent layer",
          _degraded_registry.degraded_layers, (ccr.ICD10_LAYER,))
    check_true("and its 'ready' line is a WARNING saying DEGRADED",
               any("DEGRADED" in m for m in _warnings.messages))
    # It still classifies through the layers it has — degraded is not broken.
    check_true("SNOMED classification still works on a degraded registry",
               _degraded_registry.is_primary_cancer(
                   {"codings": [{"system_key": "snomed", "code": "254837009"}],
                    "display": "Malignant neoplasm of breast"}))
    # And this is the loss the raise exists to prevent.
    check("an ICD-10-coded breast cancer is NOT recognised by a degraded "
          "registry (this is the damage)",
          _degraded_registry.is_primary_cancer(
              {"codings": [{"system_key": "icd10cm", "code": "C50.911"}],
               "display": "C50.911"}), False)

# The real registry, outside the block, still recognises it. Without this the
# check above would be consistent with the code never recognising C50.911.
with env_var(ENV, None):
    check("...while the intact registry DOES recognise it (non-degeneracy)",
          ccr.load_registry().is_primary_cancer(
              {"codings": [{"system_key": "icd10cm", "code": "C50.911"}],
               "display": "C50.911"}), True)

# --- The package imports but answers with nothing --------------------------
#
# THE CASE NEITHER HANDLER COULD SEE. The old `except ImportError` needed an
# import failure; the new raise above needs the same. A release whose table is
# empty, or whose chapter-2 categories moved, imports fine and produces exactly
# the three empty sets the missing-package path produced.
_empty_icd10 = types.ModuleType("icd10")
_empty_icd10.codes = []

# THIS CONTROL FOUND A REAL DEFECT IN THE GUARD IT WAS WRITTEN TO EXERCISE, and
# the note is here rather than in a commit message because it is the argument
# for the control existing. The guard was first written as `if not primary:`,
# which CAN NEVER FIRE: _ICD10_SEED_PRIMARY seeds "C97" whenever the release
# does not supply it, so `primary` is {"C97"} even when the release contributed
# nothing. It now asks `derived_primary_count`, captured before the seed —
# a hand-seeded code says nothing about whether the package's table is intact.
with module_replaced("icd10", _empty_icd10), env_var(ENV, None):
    _exc = check_raises("an installed release that yields no primary codes "
                        "raises too", settings.DegradedDependencyError,
                        ccr._build_icd10_cancer_sets)
    if _exc is not None:
        check_true("and the message distinguishes it from a missing package",
                   "imported, but yielded NO primary" in str(_exc))
    # And the seed is what would have hidden it — stated as a fact of this run.
    check_true("the seed set is non-empty, which is why the guard cannot ask "
               "about `primary` (non-degeneracy of the fix)",
               len(ccr._ICD10_SEED_PRIMARY) > 0)

# NON-DEGENERATE the other way: a release that yields codes must NOT raise, or
# the guard above would be firing on everything.
_full_icd10 = types.ModuleType("icd10")
_full_icd10.codes = ["C50.911", "C34.10", "C77.0", "D05.11"]
with module_replaced("icd10", _full_icd10), env_var(ENV, None):
    _p, _s, _n, _d = check_does_not_raise(
        "a release that yields codes does not raise",
        ccr._build_icd10_cancer_sets) or (None, None, None, None)
    check("and reports no degraded layer", _d, ())
    # C97 is the seed, added because this stand-in release omits it — the same
    # thing the real icd10-cm table does.
    check("primary picked up the C50/C34 codes, plus the C97 seed",
          sorted(_p), ["C34.10", "C50.911", "C97"])
    check("secondary picked up C77.0", sorted(_s), ["C77.0"])
    check("non-invasive picked up D05.11", sorted(_n), ["D05.11"])


# ===========================================================================
# 5. THE DELETION PATH REFUSES A DEGRADED REGISTRY, VARIABLE OR NO VARIABLE
# ===========================================================================

print("\n" + "=" * 78)
print("5. The deletion path refuses a degraded registry WITH the opt-out SET")
print("=" * 78)


class _resolved_override:
    """Shadow one entry in oncotriage.fhir.clean's lazy accessor cache."""

    def __init__(self, key, value):
        self._key = key
        self._value = value
        self._had = False
        self._previous = None

    def __enter__(self):
        with clean._RESOLVE_LOCK:
            self._had = self._key in clean._RESOLVED
            self._previous = clean._RESOLVED.get(self._key)
            clean._RESOLVED[self._key] = self._value
        return self._value

    def __exit__(self, *exc):
        with clean._RESOLVE_LOCK:
            if self._had:
                clean._RESOLVED[self._key] = self._previous
            else:
                clean._RESOLVED.pop(self._key, None)
        return False


# Build a genuinely degraded registry, the same way a machine without the
# package would: with the import blocked and the opt-out set.
with module_unimportable("icd10"), env_var(ENV, "1"), \
        captured_warnings(ccr.__name__):
    _DEGRADED_REGISTRY = ccr.CancerCodeRegistry()

check("the stand-in registry really is degraded (non-degeneracy)",
      _DEGRADED_REGISTRY.degraded_layers, (ccr.ICD10_LAYER,))

# THE VARIABLE IS SET FOR EVERY CHECK IN THIS SECTION. That is the point: the
# opt-out permits a degraded AGENT run and must not permit a degraded DELETION.
with _resolved_override("cancer_registry", _DEGRADED_REGISTRY), env_var(ENV, "1"):
    check("the opt-out is ON while these checks run",
          settings.resolve_allow_degraded_registries()[0], True)

    _exc = check_raises("require_intact_registry() refuses anyway",
                        settings.DegradedDependencyError,
                        clean.require_intact_registry)
    if _exc is not None:
        _text = str(_exc)
        check_true("the message says it is refusing to delete",
                   "REFUSING TO DELETE" in _text)
        check_true("and names the absent layer", ccr.ICD10_LAYER in _text)
        check_true("and says the variable does not apply here",
                   "DOES NOT APPLY" in _text)
        check_true("and points at the dry run", "dry_run=True" in _text)

    # And the whole entry point refuses, not merely the helper — a guard the
    # caller could skip is not a guard.
    check_raises("filter_cancer_patients_inplace() refuses",
                 settings.DegradedDependencyError,
                 lambda: quiet(clean.filter_cancer_patients_inplace))
    check_raises("...and so does the dry run (a plan from a degraded registry "
                 "is a wrong plan)",
                 settings.DegradedDependencyError,
                 lambda: quiet(clean.filter_cancer_patients_inplace,
                               dry_run=True))

# A registry that cannot say whether it is intact is refused too: "cannot tell"
# is not "is fine", and only one of the two may proceed to delete.
with _resolved_override("cancer_registry", object()):
    _exc = check_raises("a registry that does not report degraded_layers is "
                        "refused", settings.DegradedDependencyError,
                        clean.require_intact_registry)
    if _exc is not None:
        check_true("and the message says why",
                   "does not report" in str(_exc))

# NON-DEGENERATE: with the real registry installed the guard passes, so it is
# not simply refusing everything.
with env_var(ENV, None):
    check_does_not_raise("...and the real registry is accepted",
                         clean.require_intact_registry)


# ===========================================================================
# 6. dry_run DELETES NOTHING — AGAINST A COPY OF A REAL COHORT
# ===========================================================================

print("\n" + "=" * 78)
print("6. dry_run deletes nothing; a real run on a second copy does")
print("=" * 78)

_REAL_CORPUS = paths.data_fhir_path
_SAMPLE_SIZE = 12

_corpus_files = sorted(f for f in os.listdir(_REAL_CORPUS)
                       if f.endswith(".json"))[:_SAMPLE_SIZE]
_PRODUCTION_COUNT_BEFORE = len([f for f in os.listdir(_REAL_CORPUS)
                                if f.endswith(".json")])

check_true(f"there are real bundles to copy (non-degeneracy): "
           f"{len(_corpus_files)}", len(_corpus_files) >= 2)

_scratch = tempfile.mkdtemp(prefix="oncotriage-dryrun-")
try:
    _dry_dir = os.path.join(_scratch, "dry", "fhir")
    _real_dir = os.path.join(_scratch, "real", "fhir")
    _manifest_dry = os.path.join(_scratch, "dry", "cohort_manifest.json")
    _manifest_real = os.path.join(_scratch, "real", "cohort_manifest.json")
    os.makedirs(_dry_dir)
    os.makedirs(_real_dir)
    for _name in _corpus_files:
        shutil.copy2(os.path.join(_REAL_CORPUS, _name),
                     os.path.join(_dry_dir, _name))
        shutil.copy2(os.path.join(_REAL_CORPUS, _name),
                     os.path.join(_real_dir, _name))

    # TWO BUNDLES THAT MUST BE DELETED, ADDED TO BOTH COPIES.
    #
    # THE PRODUCTION CORPUS HAS ALREADY BEEN FILTERED, so twelve bundles taken
    # from it are twelve alive cancer patients and every deletion phase finds
    # nothing to do. A dry run over that cohort would satisfy every "nothing was
    # deleted" assertion below while proving only that there was nothing to
    # delete — the exact vacuous pass the project's rules forbid, and the first
    # version of this section did precisely that until the negative control at
    # the end reported zero.
    #
    # So the scratch cohort is given one patient of each kind the filter is
    # supposed to remove:
    #   * a bundle with no Condition resource at all -> the non_cancer phase;
    #   * a copy of a real cancer patient with deceasedDateTime set -> the
    #     deceased phase.
    # Both are written into BOTH copies, so the dry run and the control run see
    # the same input.
    _NON_CANCER = json.dumps({
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [{"resource": {"resourceType": "Patient", "id": "no-cancer",
                                "birthDate": "1970-01-01"}}],
    }, indent=2)

    with open(os.path.join(_REAL_CORPUS, _corpus_files[0])) as _fh:
        _donor = json.load(_fh)
    for _entry in _donor.get("entry", []):
        if _entry.get("resource", {}).get("resourceType") == "Patient":
            _entry["resource"]["deceasedDateTime"] = "2025-01-01T00:00:00+00:00"
            break
    _DECEASED = json.dumps(_donor)

    _INJECTED = {"zz-non-cancer.json": _NON_CANCER,
                 "zz-deceased-cancer.json": _DECEASED}
    for _dirpath in (_dry_dir, _real_dir):
        for _fname, _text in _INJECTED.items():
            with open(os.path.join(_dirpath, _fname), "w") as _fh:
                _fh.write(_text)

    _all_files = sorted(_corpus_files) + sorted(_INJECTED)

    _before_hashes = tree_hashes(_dry_dir)
    check("the two copies start identical",
          tree_hashes(_dry_dir), tree_hashes(_real_dir))

    # CAP is 1000 and the sample is 12, so the over_cap phase does nothing here.
    # The phases that DO fire are non_cancer and deceased, which is what makes
    # the comparison meaningful: the corpus is the filtered cohort, so most of
    # these are alive cancer patients and a few may not be.
    _dry_counts = dict(clean._DELETION_COUNTS)

    with _resolved_override("patients_dir", _dry_dir), \
            _resolved_override("manifest_path", _manifest_dry), \
            env_var(ENV, None):
        _dry_stats, _dry_out = quiet(clean.filter_cancer_patients_inplace,
                                     dry_run=True)

    check_true("the dry run returned stats", _dry_stats is not None)
    check("stats say dry_run", _dry_stats["dry_run"], True)
    check("every *_deleted count is zero",
          (_dry_stats["non_cancer_deleted"], _dry_stats["deceased_deleted"],
           _dry_stats["extra_deleted"]), (0, 0, 0))
    check("NOT ONE FILE WAS TOUCHED — same names, same bytes",
          tree_hashes(_dry_dir), _before_hashes)
    check("the file count is unchanged",
          len(os.listdir(_dry_dir)), len(_all_files))
    check_true("the real manifest path was NOT written",
               not os.path.exists(_manifest_dry))
    # A DRY RUN LEAVES NO TRACE IN THE MODULE COUNTERS EXCEPT would_delete. The
    # shim re-exports _DELETION_COUNTS itself, not a copy, so a number a dry run
    # left in `deleted` or `already_absent` would still be there when someone
    # read it after a real run.
    check("the dry run touched no real deletion counter",
          {k: clean._DELETION_COUNTS[k] - _dry_counts[k]
           for k in ("deleted", "already_absent", "failed")},
          {"deleted": 0, "already_absent": 0, "failed": 0})
    check("...only would_delete moved",
          clean._DELETION_COUNTS["would_delete"] - _dry_counts["would_delete"],
          _dry_stats["would_delete"]["total"])
    check_true("the plan was written to the .dryrun path",
               os.path.exists(_manifest_dry + clean.DRY_RUN_MANIFEST_SUFFIX))
    check("and stats name that file", _dry_stats["manifest_written"],
          _manifest_dry + clean.DRY_RUN_MANIFEST_SUFFIX)
    check_true("the console said nothing was deleted",
               "NOTHING IS DELETED" in _dry_out or "DRY RUN" in _dry_out)

    with open(_manifest_dry + clean.DRY_RUN_MANIFEST_SUFFIX) as _fh:
        _plan = json.load(_fh)
    check("the plan is marked dry_run", _plan["dry_run"], True)
    check("and its status is 'planned', never 'complete'",
          _plan["status"], "planned")
    check("it scanned every copied bundle", _plan["scanned"], len(_all_files))
    check_true("every phase in it is marked dry_run",
               all(p.get("dry_run") is True for p in _plan["phases"].values()))
    check_true("and no phase claims a deletion",
               all(p["deleted"] == [] for p in _plan["phases"].values()))

    _planned_names = sorted(
        n for p in _plan["phases"].values() for n in p.get("would_delete", []))
    check("the plan's would_delete list matches the reported total",
          len(_planned_names), _dry_stats["would_delete"]["total"])
    check_true("and every planned name is a file that is still there",
               all(os.path.exists(os.path.join(_dry_dir, n))
                   for n in _planned_names))

    # --- THE NEGATIVE CONTROL --------------------------------------------
    # A dry run over a cohort with nothing to delete would satisfy every check
    # above while proving nothing. The real run on the identical second copy is
    # what shows the plan was non-empty AND that it was the truth.
    with _resolved_override("patients_dir", _real_dir), \
            _resolved_override("manifest_path", _manifest_real), \
            env_var(ENV, None):
        _real_stats, _ = quiet(clean.filter_cancer_patients_inplace)

    _really_deleted = (_real_stats["non_cancer_deleted"]
                       + _real_stats["deceased_deleted"]
                       + _real_stats["extra_deleted"])
    check_true("the real run on the twin copy DID delete something "
               "(so the dry run's 'nothing deleted' is meaningful)",
               _really_deleted > 0)
    check("and it deleted exactly what the dry run planned",
          _really_deleted, _dry_stats["would_delete"]["total"])

    _remaining_real = sorted(f for f in os.listdir(_real_dir)
                             if f.endswith(".json"))
    _expected_remaining = sorted(set(_all_files) - set(_planned_names))
    check("the same FILENAMES the plan listed are the ones now gone",
          _remaining_real, _expected_remaining)
    check("...while the dry-run copy still has all of them",
          sorted(os.listdir(_dry_dir)), _all_files)
    check_true("the real run wrote the real manifest path",
               os.path.exists(_manifest_real))
    check("and its status is not 'planned'",
          json.load(open(_manifest_real))["status"] in ("complete", "partial"),
          True)
finally:
    shutil.rmtree(_scratch, ignore_errors=True)
    # _DELETION_COUNTS is module state shared with the shim. Restore it so this
    # file leaves no trace in a namespace another test might read.
    clean._DELETION_COUNTS.clear()
    clean._DELETION_COUNTS.update(_dry_counts)

# THE PRODUCTION CORPUS WAS NEVER TOUCHED. Asserted, not assumed — this file
# calls a function whose ordinary behaviour is to delete patient bundles, and
# the accessor shadow is the only thing standing between it and the real
# directory.
check("the production corpus file count is unchanged",
      len([f for f in os.listdir(_REAL_CORPUS) if f.endswith(".json")]),
      _PRODUCTION_COUNT_BEFORE)
check("and clean.patients_dir() points back at it",
      clean.patients_dir(), _REAL_CORPUS)


# ===========================================================================
# 7. THE LAB-UNIT COUNTERS DISTINGUISH ALL THREE EXITS
# ===========================================================================

print("\n" + "=" * 78)
print("7. _normalize_lab_unit: three exits, three counters")
print("=" * 78)

patient.LAB_UNIT_DEGRADATIONS.clear()

# (a) The conversion that WORKS records nothing. Without this the counters could
#     be incremented on every call and every assertion below would still pass.
check("a supported unit converts",
      patient._normalize_lab_unit("Creatinine", 88.42, "µmol/L"),
      (1.0, "mg/dL"))
check("and records no degradation", dict(patient.LAB_UNIT_DEGRADATIONS), {})

# (b) Exit 1 — no value or no unit. Ordinary, not a failure, and it must not be
#     confused with exit 3 or it would dominate the count.
check("a None value returns unchanged",
      patient._normalize_lab_unit("Creatinine", None, "mg/dL"), (None, "mg/dL"))
check("a None unit returns unchanged",
      patient._normalize_lab_unit("Creatinine", 1.0, None), (1.0, None))
check("both are counted under no_value_or_unit",
      patient.LAB_UNIT_DEGRADATIONS[
          f"{patient.LAB_UNIT_NO_VALUE_OR_UNIT}:creatinine"], 2)

# (c) Exit 2 — the exception exit. float("N/A") raises inside the loop.
check("a non-numeric value returns unchanged",
      patient._normalize_lab_unit("Creatinine", "N/A", "µmol/L"),
      ("N/A", "µmol/L"))
check("and is counted under conversion_error, WITH the exception type",
      patient.LAB_UNIT_DEGRADATIONS[
          f"{patient.LAB_UNIT_CONVERSION_ERROR}:creatinine:ValueError"], 1)

# (b2) AN EMPTY-STRING UNIT IS EXIT 1, NOT EXIT 3, and this is the check that
#      keeps the counter worth reading. _create_patient_summary passes
#      `obs.get("unit") or ""`, so a unit-less observation arrives as "" and
#      never as None; if that landed in `unconverted` the common harmless case
#      would swamp the one number the exception audit asked for.
check("an empty-string unit returns unchanged",
      patient._normalize_lab_unit("Creatinine", 1.0, ""), (1.0, ""))
check("and is counted as no_value_or_unit, not as unconverted",
      patient.LAB_UNIT_DEGRADATIONS[
          f"{patient.LAB_UNIT_NO_VALUE_OR_UNIT}:creatinine"], 3)
check("nothing landed in unconverted for it",
      patient.LAB_UNIT_DEGRADATIONS[
          f"{patient.LAB_UNIT_UNCONVERTED}:creatinine:"], 0)

# (d) Exit 3 — every rule consulted, none matched. THE ONE THAT MATTERS: a real
#     unit reached the judge unconverted.
check("an unknown unit returns unchanged",
      patient._normalize_lab_unit("Creatinine", 1.2, "mg/L"), (1.2, "mg/L"))
check("and is counted under unconverted, naming the lab AND the unit",
      patient.LAB_UNIT_DEGRADATIONS[
          f"{patient.LAB_UNIT_UNCONVERTED}:creatinine:mg/l"], 1)
check("and the unconverted bucket holds ONLY that — the unit-less rows did not "
      "dilute it",
      sum(v for k, v in patient.LAB_UNIT_DEGRADATIONS.items()
          if k.startswith(patient.LAB_UNIT_UNCONVERTED)), 1)

# (e) The three are separable, which is the item's requirement verbatim:
#     "Count them separately or the signal is lost."
_namespaces = {key.split(":", 1)[0] for key in patient.LAB_UNIT_DEGRADATIONS}
check("all three exits are present and distinguishable",
      sorted(_namespaces),
      sorted({patient.LAB_UNIT_NO_VALUE_OR_UNIT,
              patient.LAB_UNIT_CONVERSION_ERROR,
              patient.LAB_UNIT_UNCONVERTED}))
check("and the harmless exit does not hide the meaningful one",
      patient.LAB_UNIT_DEGRADATIONS[
          f"{patient.LAB_UNIT_UNCONVERTED}:creatinine:mg/l"], 1)

# (f) The contract that has to survive: it still never raises.
check("a non-string display no longer reaches .lower() unguarded",
      patient._normalize_lab_unit(None, 1.0, "mg/dL"), (1.0, "mg/dL"))

patient.LAB_UNIT_DEGRADATIONS.clear()


# ===========================================================================
# 8. THE AGE-PARSE COUNTERS, FILTER TIME AND INDEX TIME
# ===========================================================================

print("\n" + "=" * 78)
print("8. Age-parse counters at filter time and at index time")
print("=" * 78)

filtering.AGE_PARSE_FAILURES.clear()

# A parseable bound records nothing — the non-degeneracy check for the four
# below.
check("a parseable min_age parses",
      filtering._parse_age_bound("18 Years", 0, "min_age"), 18)
check("and records nothing", dict(filtering.AGE_PARSE_FAILURES), {})
check("an empty bound takes the default without recording",
      filtering._parse_age_bound("", 0, "min_age"), 0)
check("still nothing recorded", dict(filtering.AGE_PARSE_FAILURES), {})

# No digits at all -> IndexError; the bound is unusable.
check("a digit-less bound returns None",
      filtering._parse_age_bound("N/A", 0, "min_age"), None)
check("and is recorded with the bound, the exception and the text",
      filtering.AGE_PARSE_FAILURES["min_age:IndexError:N/A"], 1)
check("a digit-less max_age is recorded under its own bound",
      (filtering._parse_age_bound("no maximum", 999, "max_age"),
       filtering.AGE_PARSE_FAILURES["max_age:IndexError:no maximum"]),
      (None, 1))

# A pathological value cannot grow the key without bound.
_long = "x" * 500
filtering._parse_age_bound(_long, 0, "min_age")
check_true("a pathological value is truncated in the key",
           all(len(k) < 100 for k in filtering.AGE_PARSE_FAILURES))

filtering.AGE_PARSE_FAILURES.clear()

# THE INDEX-TIME AGE COUNTER IS GONE, AND ITS ABSENCE IS THE CHECK NOW.
#
# INDEX_AGE_PARSE_FAILURES recorded when the scrape's `if min_age > 18: continue`
# could not be evaluated. That skip was an EXACTLY-18 filter -- a trial
# requiring 19, 20 or 21 was discarded, so a 70-year-old could never be matched
# to it -- and it was deleted rather than widened, because
# agent/filtering.py:node_rule_based_filter already enforces the trial's full
# window against the actual patient (`min_age <= patient_age <= max_age`).
#
# With no age decision at scrape time there is nothing to fail, so a counter
# there could only ever read zero. Asserting its ABSENCE is strictly stronger
# than asserting it existed: it fails if anyone reintroduces an index-time age
# decision, which is the thing that must not come back.
check_true("no index-time age counter survives",
           not hasattr(indexer, "INDEX_AGE_PARSE_FAILURES"))
check_true("Stage 4 is now the only age-parse record in the project",
           isinstance(filtering.AGE_PARSE_FAILURES, collections.Counter))

_indexer_src = open(os.path.join(_CODE_DIR, "oncotriage", "retrieval",
                                 "indexer.py"), encoding="utf-8").read()
_indexer_tree = ast.parse(_indexer_src)


def _executable_comparisons(tree):
    """Every ast.Compare in `tree`, unparsed. COMMENTS ARE INVISIBLE HERE,
    which is the point: indexer.py's note about the deleted filter quotes the
    old `if min_age > 18` verbatim, and a substring search over the source
    reports that argument as the defect it describes."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            try:
                out.append(ast.unparse(node))
            except Exception:
                continue
    return out


_indexer_compares = _executable_comparisons(_indexer_tree)
check_true("the scraper makes NO age comparison in executable code",
           not any("min_age" in c or "minimum_age" in c
                   for c in _indexer_compares))
# Non-degeneracy: the walk must actually be finding comparisons, or the
# assertion above passes for free on an empty list.
check_true("...and that walk found comparisons at all (non-degeneracy)",
           len(_indexer_compares) > 5)


def _handlers_that_only_pass(tree):
    """Every `except ...: pass` in `tree`, as (line, exception text)."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if all(isinstance(s, ast.Pass) for s in node.body):
                found.append(node.lineno)
    return found


def _handler_bodies_naming(tree, name):
    """Line numbers of except handlers whose body reads `name`."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            reads = {n.id for n in ast.walk(node)
                     if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
            if name in reads:
                found.append(node.lineno)
    return found

check("no `except: pass` survives in indexer.py either",
      _handlers_that_only_pass(_indexer_tree), [])

_filtering_src = open(os.path.join(_CODE_DIR, "oncotriage", "agent",
                                   "filtering.py"), encoding="utf-8").read()
_filtering_tree = ast.parse(_filtering_src)
check("no `except: pass` survives in filtering.py",
      _handlers_that_only_pass(_filtering_tree), [])
check("nor in patient.py's lab normaliser",
      _handlers_that_only_pass(
          ast.parse(open(os.path.join(_CODE_DIR, "oncotriage", "agent",
                                       "patient.py"),
                          encoding="utf-8").read())), [])


# ===========================================================================
# 9. STRUCTURAL — histology, the result dict, and the shim surface
# ===========================================================================

print("\n" + "=" * 78)
print("9. Histology unconditional, result dict unchanged, shim surface intact")
print("=" * 78)


def _enclosing_ifs(tree, func_name, call_name):
    """Tests of the `if` statements enclosing a call to `call_name`.

    Returns a list of unparsed test expressions, outermost first. An empty list
    means the call is not inside any `if` within `func_name`.
    """
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            target = node
            break
    if target is None:
        return None

    def walk(node, stack):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Call):
                fn = child.func
                if isinstance(fn, ast.Name) and fn.id == call_name:
                    return list(stack)
                if isinstance(fn, ast.Attribute) and fn.attr == call_name:
                    return list(stack)
            if isinstance(child, ast.If):
                found = walk(child, stack + [ast.unparse(child.test)])
                if found is not None:
                    return found
                # The else-branch keeps the same enclosing tests for our
                # purposes; recursing into it with the same stack is fine.
            else:
                found = walk(child, stack)
                if found is not None:
                    return found
        return None

    return walk(target, [])


_hist_ifs = _enclosing_ifs(_filtering_tree, "node_rule_based_filter",
                           "extract_patient_histology")
check("extract_patient_histology is called in node_rule_based_filter",
      _hist_ifs is not None, True)
check("and it is inside NO `if` at all — in particular not the mesh_filter one",
      _hist_ifs, [])

# NEGATIVE CONTROL. The same scan against an AST copy with the call moved back
# inside the guard must report the guard, or the scan above proves nothing.
_control_tree = ast.parse(_filtering_src)
for _node in ast.walk(_control_tree):
    if isinstance(_node, ast.FunctionDef) and _node.name == "node_rule_based_filter":
        _hist_stmt = None
        for _i, _stmt in enumerate(_node.body):
            if (isinstance(_stmt, ast.Assign)
                    and "extract_patient_histology" in ast.unparse(_stmt)):
                _hist_stmt = _node.body.pop(_i)
                break
        _guard = next(s for s in _node.body
                      if isinstance(s, ast.If)
                      and "mesh_filter" in ast.unparse(s.test))
        _guard.body.insert(0, _hist_stmt)
        break
_control_ifs = _enclosing_ifs(_control_tree, "node_rule_based_filter",
                              "extract_patient_histology")
check("NEGATIVE CONTROL: the pre-11a shape IS reported as guarded",
      _control_ifs, ["mesh_filter is not None"])

# --- AND THE BEHAVIOUR, not just the shape --------------------------------
#
# The AST check says where the call sits. This says what that costs: with NO
# MeSH filter, a trial whose histology contradicts the patient's is dropped
# now and reached GPT-4o before. Run through the real Stage 4 node with
# mesh_filter overridden to None — the same override File 37 installs — so this
# exercises the degraded path exactly as a run with the opt-out set would.
from oncotriage.agent import deps as _deps  # noqa: E402 - local to this section
from oncotriage.extraction.histology import (  # noqa: E402
    extract_patient_histology as _extract_hist,
)

_SQUAMOUS_PATIENT = {
    "demographics": {"age": 62, "sex": "male"},
    "conditions": [{"display": "Squamous cell carcinoma of lung",
                    "code": "254632001"}],
}
_PATIENT_TAGS = _extract_hist(_SQUAMOUS_PATIENT["conditions"])
check_true("the stand-in patient really carries a histology tag "
           "(non-degeneracy)", len(_PATIENT_TAGS) > 0)

def _trial(nct, tags):
    return {"trial": {"nct_id": nct,
                      "title": "A study",
                      "eligibility": {"min_age": "18 Years",
                                      "max_age": "99 Years", "sex": "ALL"},
                      "histology_tags": tags},
            "rerank_score": 1.0, "rerank_score_raw": 1.0}

# "adenocarcinoma" is the exclusive partner of "squamous" in
# _EXCLUSIVE_PAIRS, so this trial genuinely conflicts.
_MISMATCHED = _trial("NCT00000001", ["adenocarcinoma"])
_COMPATIBLE = _trial("NCT00000002", sorted(_PATIENT_TAGS))
check_true("and the mismatched trial really conflicts with it "
           "(non-degeneracy)",
           filtering.is_histology_mismatch(_PATIENT_TAGS,
                                           _MISMATCHED["trial"]))

_STATE = {
    "patient_data": _SQUAMOUS_PATIENT,
    "reranked_trials": [_MISMATCHED, _COMPATIBLE],
    "stage_timings": {},
    "ablation_flags": {},
}

with _deps.override(_deps.MESH_FILTER, None):
    check("the override really removes the filter (non-degeneracy)",
          _deps.get_mesh_filter(), None)
    _degraded_result, _ = quiet(filtering.node_rule_based_filter,
                                dict(_STATE))

check("with NO MeSH filter the histology filter still runs and drops the "
      "mismatched trial",
      _degraded_result["histology_dropped"], 1)
check("and the compatible trial survives",
      [t["trial"]["nct_id"] for t in _degraded_result["filtered_trials"]],
      ["NCT00000002"])
check("the run is recorded as having skipped the cancer site filter",
      _degraded_result["mesh_filter_skip_reason"], "no_mesh_filter")
check("...and mesh_filter_applied is False, as before item 11a",
      _degraded_result["mesh_filter_applied"], False)

# NEGATIVE CONTROL for the behaviour: the pre-11a code path, reconstructed by
# running the same node with the histology set forced empty, keeps both trials.
# That is what the old shape produced, and it is what this change ends.
check("NEGATIVE CONTROL: a patient with no histology tags keeps both trials, "
      "which is what the pre-11a degraded path did for EVERY patient",
      quiet(filtering.node_rule_based_filter,
            {**_STATE, "patient_data": {**_SQUAMOUS_PATIENT,
                                        "conditions": [{"display": "Neoplasm"}]}}
            )[0]["histology_dropped"], 0)

# --- THE RESULT DICT IS UNCHANGED -----------------------------------------
#
# The item forbids any counter becoming a new key here, because the twelve
# characterization fixtures diff this dict and a new field means recapturing all
# twelve at GPT-4o prices. Pinned literally rather than derived, so a key added
# in either direction fails.
_STAGE4_KEYS = {
    "filtered_trials", "candidates_after_rule_filter",
    "candidates_after_quality_filter", "mesh_dropped", "histology_dropped",
    "stage_dropped", "age_dropped", "sex_dropped", "quality_dropped",
    "quality_threshold", "mesh_filter_applied", "mesh_filter_skip_reason",
    "stage_timings",
}

_returned = None
for _node in ast.walk(_filtering_tree):
    if isinstance(_node, ast.FunctionDef) and _node.name == "node_rule_based_filter":
        for _stmt in ast.walk(_node):
            if isinstance(_stmt, ast.Return) and isinstance(_stmt.value, ast.Dict):
                _returned = {k.value for k in _stmt.value.keys
                             if isinstance(k, ast.Constant)}
        break

check_true("the Stage 4 return dict was found (non-degeneracy)",
           _returned is not None and len(_returned) > 5)
check("and its keys are exactly what they were before item 11a",
      sorted(_returned or []), sorted(_STAGE4_KEYS))
check("no degradation counter leaked into it",
      sorted(k for k in (_returned or [])
             if "age_parse" in k or "unparsed" in k or "degrad" in k), [])

# --- FILE 05 IS A THIN ENTRY POINT AND EXPORTS NOTHING (pass 20e) ---------
#
# WHAT THIS WAS. Until pass 20e it asserted that "05- FHIR Clean Data.py"'s
# re-export shim bound exactly eleven names statically (fourteen at runtime,
# the difference being three exec-bootstrap leftovers), so that adding dry_run
# as a new exported HELPER instead of a parameter would fail here in thirty
# seconds rather than at the end of an eight-minute serial run.
#
# WHY IT CHANGED. Pass 20e deleted that shim. It existed for one consumer --
# "34- Cohort Selector Diff Read Only.py" chained File 05 and read has_cancer_diagnosis
# and _CANCER_REGISTRY out of the shared exec namespace -- and pass 20c-3d had
# already converted File 34 into a thin entry point over
# oncotriage/evaluation/cohort_diff.py, which imports both from the package.
# The shim spent two passes serving nobody.
#
# THE PROPERTY THIS SECTION GUARDS IS UNCHANGED, and it was never really about
# the count: it is that item 11a's dry run must be a PARAMETER on the existing
# function rather than a second exported entry point, because a plan produced
# by a second implementation is a plan that can disagree with the deletion it
# previews. So the assertion is now the stronger form of the same thing --
# File 05 exports NOTHING AT ALL, so there is no surface for a second helper to
# appear on. A new name in this file fails whatever it is called.
_entry_tree = ast.parse(open(os.path.join(_CODE_DIR, "05- FHIR Clean Data.py"),
                             encoding="utf-8").read())
_entry_pkg_imports = sorted(
    a.asname or a.name for n in _entry_tree.body
    if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("oncotriage")
    for a in n.names)
check("File 05 imports only what its __main__ block calls",
      _entry_pkg_imports, ["Project_Name", "filter_cancer_patients_inplace"])
# NON-DEGENERATE: the scan must be looking at a real file with real imports, or
# "only two names" is what an empty parse returns too.
check("...and the scan parsed a real file (non-degeneracy)",
      len(_entry_tree.body) > 5, True)
check("no helper is exported alongside them -- require_intact_registry and "
      "DRY_RUN_MANIFEST_SUFFIX in particular",
      sorted({"require_intact_registry", "DRY_RUN_MANIFEST_SUFFIX"}
             & set(_entry_pkg_imports)), [])
# And nothing is assigned at module level either, which is the other way a name
# reaches a caller. `Flag`-style module data is legitimate in File 15; here
# there is none, and a new one would be a surface this file is not supposed to
# have.
_entry_assigns = sorted(
    t.id for n in _entry_tree.body if isinstance(n, ast.Assign)
    for t in n.targets if isinstance(t, ast.Name))
check("...and File 05 binds no module-level name of its own", _entry_assigns, [])


# --- dry_run IS A PARAMETER, NOT A SECOND FUNCTION ------------------------
_sig = inspect.signature(clean.filter_cancer_patients_inplace)
check("filter_cancer_patients_inplace takes dry_run",
      list(_sig.parameters), ["dry_run"])
check("and it defaults to False, so the documented command is unchanged",
      _sig.parameters["dry_run"].default, False)


# ===========================================================================
# SUMMARY
# ===========================================================================

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
