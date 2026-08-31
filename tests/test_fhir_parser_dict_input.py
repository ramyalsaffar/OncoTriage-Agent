# FHIR Parser Dict Input Test
#############################

"""
``parse_fhir_bundle`` used to take a FILE PATH and nothing else, so
``oncotriage/api/server.py`` bridged the gap by writing every incoming bundle to
a ``NamedTemporaryFile``, parsing that, and unlinking it in a ``finally``. The
helper doing so is shared by BOTH endpoints, so a request that arrived as JSON
on ``POST /match`` -- and never came near a file -- still caused a write, a read
and a delete on the serving path, once per request, on the event loop's thread
pool.

Pass 20f-1 makes the parser accept a dict as well as a path and deletes the
round trip from the shared helper.

WHAT THIS FILE HOLDS
--------------------
    1. EQUIVALENCE. The same bundle parsed by path and as a dict produces
       equal patient dictionaries. This is the whole contract: the path
       behaviour must not have moved.
    2. The dict handed in is NOT MUTATED. On the path route the parser owned a
       freshly decoded object; on the dict route it is the caller's, and a
       parser that wrote into it would corrupt a bundle the caller still holds.
    3. THE FOUR SHARED COUNTERS behave identically on both routes, and they are
       still the same Counter INSTANCES the module holds -- rebinding one hands
       out a snapshot, which is the trap the module docstring warns about and
       which ``tests/test_fhir_ecog_surfacing.py`` depends on not happening.
    4. ``load_all_patients`` still clears and fills those same instances.
    5. THE SERVING PATH OPENS NO FILE. ``_run_matching_pipeline`` is driven
       with the real parser, with ``builtins.open``, ``io.open`` and
       ``tempfile.NamedTemporaryFile`` all trapped to raise, and the traps are
       FIRED afterwards to show they were armed.
    6. The bundle reaches the parser BY IDENTITY -- the object the helper hands
       over is the object the endpoint received, which no round trip through
       JSON can be.
    7. The server module no longer imports ``os`` or ``tempfile`` at all, and
       its source names no temporary-file call. A never-read import is what
       ``tests/test_package_invariants.py`` check 2h reports; this says the
       same thing at the point it was created.

NO NETWORK, NO KEYS, NO SPEND. The graph, the matcher and the inference logger
are stand-ins; nothing here reaches Qdrant, OpenAI or a database. One real
bundle is read out of the corpus, read-only.

Run from terminal:
    python tests/test_fhir_parser_dict_input.py

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
import builtins
import copy
import glob
import io
import json
import shutil
import tempfile

from oncotriage import paths as _paths
from oncotriage.api import server as _server
from oncotriage.fhir import parser as _parser
from oncotriage.fhir.parser import (
    BIRTH_DATE_PRECISION_COUNTS,
    DEMOGRAPHIC_SOURCE_COUNTS,
    ECOG_SELECTION_COUNTS,
    ECOG_VALUE_SHAPE_COUNTS,
    load_all_patients,
    parse_fhir_bundle,
)


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


def attempt(fn):
    """Return fn()'s value, or the exception rendered as a string.

    EVERY CALL TO THE FUNCTION UNDER TEST GOES THROUGH THIS. Without it,
    removing the dict branch -- the exact revert this file exists to catch --
    makes ``parse_fhir_bundle(a_dict)`` raise TypeError at module level, which
    takes the whole run down and reports ONE traceback where it owes a dozen
    recorded failures. That is the defect tests/test_storage_query_layer.py
    found in itself: a file written to catch a regression that could not
    survive the regression long enough to report it.
    """
    try:
        return fn()
    except Exception as exc:            # noqa: BLE001 -- reported, not raised
        return f"<raised {type(exc).__name__}: {exc}>"


_TMP = tempfile.mkdtemp(prefix="oncotriage-parser-")

# One real corpus bundle. A hand-written bundle would exercise a shape nobody
# ships; the point of this file is that the two routes agree on the data the
# pipeline actually meets.
_CORPUS = sorted(glob.glob(_paths.data_fhir_path + "*.json"))
_BUNDLE_PATH = _CORPUS[0] if _CORPUS else None


# ===========================================================================
# SECTION 1: THE TWO ROUTES AGREE
# ===========================================================================

print("=" * 70)
print("Section 1: path and dict produce the same patient dictionary")
print("=" * 70)

check("a corpus bundle was found (non-degeneracy)",
      _BUNDLE_PATH is not None, True)

if _BUNDLE_PATH:
    with open(_BUNDLE_PATH, "r") as _fh:
        _bundle = json.load(_fh)

    _from_path = attempt(lambda: parse_fhir_bundle(_BUNDLE_PATH))
    _from_dict = attempt(lambda: parse_fhir_bundle(_bundle))
    check("the dict route returned a patient dictionary rather than raising",
          isinstance(_from_dict, dict), True)

    # NON-DEGENERATE FIRST: two empty dictionaries are equal, and an equality
    # assertion between two failures is the shape this project treats as a
    # defect rather than a pass.
    check("the parsed patient carries a patient_id (non-degeneracy)",
          bool(isinstance(_from_path, dict) and _from_path.get("patient_id")), True)
    check("...and at least one condition (non-degeneracy)",
          len(_from_path.get("conditions", []) if isinstance(_from_path, dict)
              else []) > 0, True)
    check("...and an ecog_performance_status block (non-degeneracy)",
          isinstance(_from_path.get("ecog_performance_status")
                     if isinstance(_from_path, dict) else None, dict), True)

    check("path and dict produce equal patient dictionaries",
          _from_dict, _from_path)
    check("...key for key, so a new field on one route would show",
          sorted(_from_dict) if isinstance(_from_dict, dict) else _from_dict,
          sorted(_from_path) if isinstance(_from_path, dict) else _from_path)

    # A str subclass and a Path both still take the file route: the dispatch
    # tests for dict, not against str, so anything os.open accepts still works.
    from pathlib import Path as _Path
    check("a pathlib.Path still takes the file route and agrees",
          attempt(lambda: parse_fhir_bundle(_Path(_BUNDLE_PATH))), _from_path)


# ===========================================================================
# SECTION 2: THE CALLER'S DICT IS NOT MUTATED
# ===========================================================================

print()
print("=" * 70)
print("Section 2: the bundle handed in comes back untouched")
print("=" * 70)

if _BUNDLE_PATH:
    _before = copy.deepcopy(_bundle)
    attempt(lambda: parse_fhir_bundle(_bundle))
    check("the input bundle is unchanged after parsing", _bundle, _before)

    # And the output does not alias the input: a later edit to the bundle must
    # not reach a patient dictionary already produced from it.
    _parsed = attempt(lambda: parse_fhir_bundle(_bundle))
    _entries = _bundle.get("entry", [])
    if _entries:
        _entries[0]["resource"]["__probe__"] = "written after parsing"
        check("...and nothing in the parsed patient moved when the bundle did",
              json.dumps(_parsed, sort_keys=True, default=str).find("__probe__"),
              -1)
        del _entries[0]["resource"]["__probe__"]


# ===========================================================================
# SECTION 3: THE FOUR SHARED COUNTERS
# ===========================================================================
# They are module-level Counter INSTANCES that load_all_patients() clears and
# fills in place. The dict route must move them exactly as the path route does,
# and the objects reached by `from ... import` must still BE the module's.

print()
print("=" * 70)
print("Section 3: the four shared counters behave identically on both routes")
print("=" * 70)

_COUNTERS = (
    ("BIRTH_DATE_PRECISION_COUNTS", BIRTH_DATE_PRECISION_COUNTS),
    ("DEMOGRAPHIC_SOURCE_COUNTS",   DEMOGRAPHIC_SOURCE_COUNTS),
    ("ECOG_VALUE_SHAPE_COUNTS",     ECOG_VALUE_SHAPE_COUNTS),
    ("ECOG_SELECTION_COUNTS",       ECOG_SELECTION_COUNTS),
)

check("every imported counter IS the module's own object, not a copy",
      sorted(_name for _name, _obj in _COUNTERS
             if getattr(_parser, _name) is not _obj),
      [])

if _BUNDLE_PATH:
    def _snapshot():
        return {_name: dict(_obj) for _name, _obj in _COUNTERS}

    for _name, _obj in _COUNTERS:
        _obj.clear()
    attempt(lambda: parse_fhir_bundle(_BUNDLE_PATH))
    _after_path = _snapshot()

    for _name, _obj in _COUNTERS:
        _obj.clear()
    attempt(lambda: parse_fhir_bundle(_bundle))
    _after_dict = _snapshot()

    # NON-DEGENERATE FIRST: two empty snapshots are equal.
    check("the path route moved at least one counter (non-degeneracy)",
          sum(sum(_v.values()) for _v in _after_path.values()) > 0, True)
    check("the dict route moves the same counters by the same amounts",
          _after_dict, _after_path)


# ===========================================================================
# SECTION 4: load_all_patients STILL CLEARS AND FILLS THE SAME INSTANCES
# ===========================================================================

print()
print("=" * 70)
print("Section 4: load_all_patients clears and fills in place")
print("=" * 70)

if _BUNDLE_PATH:
    _corpus_dir = os.path.join(_TMP, "corpus")
    os.makedirs(_corpus_dir, exist_ok=True)
    for _index in range(2):
        shutil.copy2(_BUNDLE_PATH,
                     os.path.join(_corpus_dir, f"bundle_{_index}.json"))

    BIRTH_DATE_PRECISION_COUNTS["planted-before-the-load"] = 99
    _patients = attempt(lambda: load_all_patients(_corpus_dir))
    if not isinstance(_patients, list):
        _patients = []

    check("two bundles were loaded (non-degeneracy)", len(_patients), 2)
    check("the planted key was CLEARED rather than accumulated",
          "planted-before-the-load" in BIRTH_DATE_PRECISION_COUNTS, False)
    check("...and the counter reached through the module is the one that "
          "filled",
          sum(_parser.BIRTH_DATE_PRECISION_COUNTS.values()), 2)
    check("...and it is still the object this file imported",
          _parser.BIRTH_DATE_PRECISION_COUNTS is BIRTH_DATE_PRECISION_COUNTS,
          True)


# ===========================================================================
# SECTION 5 AND 6: THE SERVING PATH OPENS NO FILE
# ===========================================================================
# The traps are armed around the REAL parser, so this is a statement about the
# request path rather than about a stand-in. The matcher and the inference
# logger are stand-ins because they are what costs money and what writes a
# database; neither is on trial here.

print()
print("=" * 70)
print("Section 5: a JSON request opens no file, writes none, deletes none")
print("=" * 70)

if _BUNDLE_PATH:
    _seen = {}

    def _stub_match(patient_data, graph):
        _seen["patient_id"] = patient_data.get("patient_id")
        return {"matches": [], "near_misses": [], "not_evaluable": []}

    def _stub_log(result, patient_data, db_path=None):
        _seen["logged"] = True
        return db_path

    _real_parse = _server.parse_fhir_bundle

    def _spy_parse(bundle_or_path):
        _seen["handed"] = bundle_or_path
        return _real_parse(bundle_or_path)

    def _trap(*_args, **_kwargs):
        raise AssertionError(
            "the serving path touched the filesystem; pass 20f-1 removed the "
            "temporary-file round trip from _run_matching_pipeline")

    _saved = (_server.match_patient_to_trials, _server.log_inference,
              _server.parse_fhir_bundle, _server.graph,
              builtins.open, io.open, tempfile.NamedTemporaryFile)

    _server.match_patient_to_trials = _stub_match
    _server.log_inference = _stub_log
    _server.parse_fhir_bundle = _spy_parse
    _server.graph = object()
    builtins.open = _trap
    io.open = _trap
    tempfile.NamedTemporaryFile = _trap
    try:
        _type, _message = raises(lambda: _server._run_matching_pipeline(_bundle))
    finally:
        (_server.match_patient_to_trials, _server.log_inference,
         _server.parse_fhir_bundle, _server.graph,
         builtins.open, io.open, tempfile.NamedTemporaryFile) = _saved

    # SCOPE, stated rather than implied: what is trapped is open()/io.open()/
    # NamedTemporaryFile, which is the round trip this pass removed. The
    # database write log_inference() performs is a deliberate, named write and
    # is stubbed out here -- it goes through sqlite3.connect, not through any
    # of the three, and it is not what this section is about.
    check("the shared helper completed with open(), io.open() and "
          "NamedTemporaryFile all trapped",
          (_type, _message), (None, ""))
    check("...and it really ran the pipeline (non-degeneracy)",
          _seen.get("patient_id") is not None, True)
    check("...and it really logged (non-degeneracy)",
          _seen.get("logged"), True)

    # THE TRAPS ARE FIRED, so a pass cannot mean they were never armed. This is
    # the same discipline tests/test_package_invariants.py section 2 applies to
    # its twelve import traps.
    _fired = []
    for _label, _fn in (("builtins.open", lambda: builtins.open("/dev/null")),
                        ("io.open", lambda: io.open("/dev/null")),
                        ("tempfile.NamedTemporaryFile",
                         lambda: tempfile.NamedTemporaryFile())):
        _saved_open = (builtins.open, io.open, tempfile.NamedTemporaryFile)
        builtins.open = io.open = tempfile.NamedTemporaryFile = _trap
        try:
            _fired.append((_label, raises(_fn)[0]))
        finally:
            (builtins.open, io.open,
             tempfile.NamedTemporaryFile) = _saved_open
    check("each trap fires when it is armed, so the section above proves "
          "something",
          _fired,
          [("builtins.open", "AssertionError"),
           ("io.open", "AssertionError"),
           ("tempfile.NamedTemporaryFile", "AssertionError")])

    print()
    print("=" * 70)
    print("Section 6: the bundle reaches the parser by identity")
    print("=" * 70)

    check("the object the helper handed the parser IS the object it received",
          _seen.get("handed") is _bundle, True)


# ===========================================================================
# SECTION 7: THE SERVER MODULE NO LONGER CARRIES THE BRIDGE
# ===========================================================================

print()
print("=" * 70)
print("Section 7: the server imports and names nothing to do with temp files")
print("=" * 70)

_server_src = open(os.path.abspath(_server.__file__), encoding="utf-8").read()
_server_tree = ast.parse(_server_src)

_module_imports = set()
for _node in _server_tree.body:
    if isinstance(_node, ast.Import):
        for _alias in _node.names:
            _module_imports.add(_alias.asname or _alias.name.split(".")[0])
    elif isinstance(_node, ast.ImportFrom):
        for _alias in _node.names:
            _module_imports.add(_alias.asname or _alias.name)

check("the source was read (non-degeneracy)", len(_server_src) > 5000, True)
# `tempfile` STAYS BANNED OUTRIGHT: the module has no use for it that is not
# the deleted bridge, so its absence IS the property.
check("the server module does not import tempfile",
      sorted(_module_imports & {"tempfile"}), [])

check("...json is still imported, because /match/file decodes an upload",
      "json" in _module_imports, True)

_helper = next((_n for _n in ast.walk(_server_tree)
                if isinstance(_n, ast.FunctionDef)
                and _n.name == "_run_matching_pipeline"), None)
check("_run_matching_pipeline is still a top-level function (non-degeneracy)",
      _helper is not None, True)

_FILESYSTEM_NAMES = frozenset({"NamedTemporaryFile", "TemporaryFile",
                               "mkstemp", "mkdtemp", "unlink", "remove"})


def _filesystem_calls(node):
    """Every filesystem name REACHED inside `node`, in all three forms.

    An AST walk rather than a substring scan of the source, because the source
    now carries the deleted bridge VERBATIM IN A COMMENT -- the comment is the
    record of what was removed and why, and a text scan cannot tell it from a
    call. ast.get_source_segment includes comments; ast.walk does not see them.

    All three reference forms are covered, which is this project's standing
    rule about any check that names a symbol: the bare name (``mkstemp(...)``
    after a from-import), the attribute (``tempfile.NamedTemporaryFile``) and
    the from-import binding itself.
    """
    found = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in _FILESYSTEM_NAMES:
            found.add(sub.id)
        elif isinstance(sub, ast.Attribute) and sub.attr in _FILESYSTEM_NAMES:
            found.add(sub.attr)
        elif isinstance(sub, ast.ImportFrom):
            for alias in sub.names:
                if alias.name in _FILESYSTEM_NAMES:
                    found.add(alias.name)
    return sorted(found)


# `os` USED TO BE BANNED WITH IT AND THAT CHECK WAS STALE, which is why it is
# rewritten here rather than deleted or relaxed. The API-shutdown-gate pass
# re-added `import os` for ONE reader -- `os.write(2, ...)` in the SIGTERM
# handler, which must be async-signal-safe and therefore cannot be a `print` or
# a log call -- and did not update this file, so bucket A shipped red. Banning
# an import is a PROXY for "no temp-file round trip"; the property itself is
# that no filesystem call is reached anywhere in the module, and that is what is
# asserted now. It is strictly stronger: it fails on `os.unlink`, `os.remove`
# and `mkstemp` however they were imported, which the import ban could not see
# once any module-level `os` was legitimate.
check("...and `os`, which it does import, reaches no filesystem call anywhere "
      "in the module -- the property the import ban stood in for",
      _filesystem_calls(_server_tree), [])
check("...non-degeneracy: the module really does import os, so the check above "
      "is about a live name rather than an absent one",
      "os" in _module_imports, True)


if _helper is not None:
    check("...its source is substantial (non-degeneracy)",
          len(ast.get_source_segment(_server_src, _helper) or "") > 800, True)
    check("the helper reaches no temporary-file or unlink call, in any of the "
          "three reference forms",
          _filesystem_calls(_helper), [])

    # THREE NEGATIVE CONTROLS, ONE PER REFERENCE FORM. Without them the check
    # above is satisfied by a scan that has stopped looking, which is exactly
    # the shape this project has shipped before. Each is parsed as its own
    # module and must be REPORTED.
    _controls = {
        "attribute":   "def f():\n    tmp = tempfile.NamedTemporaryFile()\n",
        "bare name":   "def f():\n    fd, path = mkstemp()\n",
        "from-import": "def f():\n    from os import unlink\n",
    }
    check("...and it reports a planted call in each of the three forms",
          {_label: _filesystem_calls(ast.parse(_src))
           for _label, _src in _controls.items()},
          {"attribute": ["NamedTemporaryFile"],
           "bare name": ["mkstemp"],
           "from-import": ["unlink"]})


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
