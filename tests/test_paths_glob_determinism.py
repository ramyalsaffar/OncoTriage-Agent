# Path Glob Determinism Test
############################

"""
Every sibling directory in the local branch of ``oncotriage/paths.py`` is
discovered by a prefix glob, and until pass 20f-1 the winner of an ambiguous
pattern was FILESYSTEM ORDER: ``_glob_one`` ended ``return hits[0]`` on an
unsorted ``glob.glob``. ``glob`` does not sort -- it returns ``os.scandir``
order, which is neither alphabetical nor stable across a rename, a restore, a
copy or a different machine. This project states determinism as a property
(temperature 0, stable argsort, seeded sampling), and that was the one place a
PATH resolved nondeterministically.

WHAT THIS FILE HOLDS
--------------------
    1. THE MEASUREMENT, as a standing check. Every ``_glob_one`` call the local
       branch makes is recorded with the number of directories it matched, and
       every one of them must match exactly one. On the machine pass 20f-1 was
       written on, all fourteen matched exactly one, which is what made the
       raise below free to add; it is eighteen since the portability pass.
    2. One match resolves, and the value is the directory.
    3. MORE THAN ONE MATCH RAISES, naming the pattern, the label, EVERY
       candidate, and which one the pre-20f-1 code would have returned. The
       last part is what makes the diagnosis actionable rather than merely
       loud.
    4. The candidate list in that message is SORTED, so two machines reporting
       the same ambiguity print the same message. That is the only thing the
       ``sorted()`` buys -- when exactly one directory matches, order cannot
       matter -- and it is stated that way rather than dressed up as the fix.
    5. No match still raises, with its message unchanged: the pattern, the
       root, the provenance of the root and the variable to set.

WHY AMBIGUITY RAISES rather than picking the sorted winner: see the block above
``_glob_one`` in oncotriage/paths.py. In short, it is item 11a's line --
a configuration defect that one command fixes raises, third-party data that no
operator can fix is counted -- and the cost of guessing here is not a degraded
run but a confidently wrong one, since ``oncotriage/fhir/clean.py`` UNLINKS
patient bundles out of whichever ``*Patients/`` directory won.

NO NETWORK, NO KEYS, NO SPEND. Sections 2-5 build throwaway directory trees
under a temporary directory and never touch the project tree. Section 1 reads
the real sibling layout and resolves it, which is what every other test in this
suite already does at import.

Run from terminal:
    python tests/test_paths_glob_determinism.py

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

import glob
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
        _FAILURES.append(f"{label}\n          expected: {expected}\n          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


def raises(fn):
    """Return (exception_type_name, message) for a call that must raise.

    Returns (None, '') when it did not raise, so the caller can check that as a
    failure rather than having the run abort on the happy path.
    """
    try:
        fn()
    except Exception as exc:            # noqa: BLE001 -- the type is the answer
        return type(exc).__name__, str(exc)
    return None, ""


_TMP_ROOT = tempfile.mkdtemp(prefix="oncotriage-glob-")


def _tree(*names):
    """Create a fresh scratch directory holding `names` as subdirectories.

    The subdirectories are created in REVERSE sorted order on purpose: the
    message assertion in section 4 is about sorted output, and a tree whose
    creation order already agrees with sorted order cannot tell the two apart.
    """
    root = tempfile.mkdtemp(dir=_TMP_ROOT)
    for name in sorted(names, reverse=True):
        os.makedirs(os.path.join(root, name))
    return root


# ===========================================================================
# SECTION 1: THE MEASUREMENT -- every local pattern matches exactly one
# ===========================================================================
# Recorded by spying on _glob_one while every lazy path is read for the first
# time. The lambdas in _LOCAL_PATHS resolve `_glob_one` as a MODULE GLOBAL at
# call time, so rebinding the module attribute is enough to observe every call
# without changing what any of them returns -- the spy delegates.
#
# THE CACHE IS CLEARED FIRST and that is the only reason this observes
# anything: another import in this process may already have resolved a path,
# and _resolve() caches per name, so a spy installed afterwards would see the
# calls that happen to be left rather than all of them.

print("=" * 70)
print("Section 1: every local glob pattern matches exactly one directory")
print("=" * 70)

if _paths.IS_DOCKER:
    # The Docker branch is a table of fixed strings and calls _glob_one zero
    # times. Reported rather than silently skipped: a section that quietly
    # covers nothing reads exactly like a section that passed.
    print("  SKIP  Docker branch: the local glob table is not in use here")
    _local_names = ()
else:
    _observed = []
    _real_glob_one = _paths._glob_one

    def _spy(pattern, label):
        _observed.append((label, pattern, len(glob.glob(pattern))))
        return _real_glob_one(pattern, label)

    _paths._RESOLVED.clear()
    _paths._glob_one = _spy
    try:
        for _name in _paths.PATH_NAMES:
            getattr(_paths, _name)
    finally:
        _paths._glob_one = _real_glob_one

    # NON-DEGENERATE FIRST. The two checks below are "nothing matched more than
    # once" and "every path resolved", both of which an EMPTY observation list
    # satisfies for free -- and an empty list is exactly what a spy installed
    # after the cache was warm would produce.
    # EIGHTEEN. It was thirteen from pass 20f-3, which deleted
    # `requirements_path` from both path tables -- a variable no code had ever
    # read -- fourteen when the tracking pass added `result_tracking_path`, and
    # eighteen since the portability pass promoted `testing_path`,
    # `testing_fixture_path`, `testing_evaluation_path` and `model_cache_path`
    # out of two private globs in oncotriage/fixtures/capture.py and
    # oncotriage/evaluation/run_harness.py, each of which INVENTED a directory
    # when nothing matched. The number is the count of LOCAL RESOLVERS, so it
    # moves with the table by construction; what it is guarding is that the spy
    # saw every one of them, and a spy installed after the cache was warm would
    # see none.
    check("the spy observed one _glob_one call per local path resolver "
          "(non-degeneracy)",
          len(_observed), 18)

    _ambiguous = [f"{_label}: {_n} matches for {_pattern}"
                  for _label, _pattern, _n in _observed if _n != 1]
    check("every local pattern matches exactly one directory on this machine",
          sorted(_ambiguous), [])

    for _label, _pattern, _n in _observed:
        print(f"        {_label:26s} {_n} match(es)  {_pattern}")

    _local_names = _paths.PATH_NAMES

    check("...and every path name resolved to a non-empty string",
          sorted(_name for _name in _local_names
                 if not isinstance(getattr(_paths, _name), str)
                 or not getattr(_paths, _name)),
          [])


# ===========================================================================
# SECTION 2: ONE MATCH RESOLVES
# ===========================================================================

print()
print("=" * 70)
print("Section 2: exactly one match returns that directory")
print("=" * 70)

_one = _tree("02- Data")
check("a pattern matching one directory returns it",
      _paths._glob_one(_one + "/*Data/", "data"),
      os.path.join(_one, "02- Data") + os.sep)

check("...and a sibling that does not match the suffix is not returned",
      _paths._glob_one(_tree("02- Data", "02- Datasets Archive") + "/*Data/",
                       "data").endswith("02- Data" + os.sep),
      True)


# ===========================================================================
# SECTION 3: MORE THAN ONE MATCH RAISES, AND NAMES EVERYTHING
# ===========================================================================
# The pre-20f-1 code returned hits[0] here, silently, and which directory that
# was depended on the order the filesystem happened to enumerate.

print()
print("=" * 70)
print("Section 3: an ambiguous pattern raises and names every candidate")
print("=" * 70)

_two = _tree("02- Data", "09- Old Data")
_type, _message = raises(lambda: _paths._glob_one(_two + "/*Data/", "data"))

check("two matching siblings raise RuntimeError",
      _type, "RuntimeError")
check("...the message says how many matched",
      "2 directories matched" in _message, True)
check("...it names the label it was called with",
      "the data pattern" in _message, True)
check("...it names the pattern",
      (_two + "/*Data/") in _message, True)
check("...it names the FIRST candidate",
      os.path.join(_two, "02- Data") in _message, True)
check("...and the SECOND, so the operator sees the ambiguity rather than a "
      "single arbitrary winner",
      os.path.join(_two, "09- Old Data") in _message, True)
check("...it names which one the pre-20f-1 code would have returned",
      "would have resolved to" in _message, True)
check("...it names the environment variable that overrides the root",
      _paths.path_settings.ENV_MAIN_PATH in _message, True)

# Three matches, to show the message is not written for the two-case only.
_three = _tree("02- Data", "09- Old Data", "10- Archived Data")
_type3, _message3 = raises(lambda: _paths._glob_one(_three + "/*Data/", "data"))
check("three matching siblings raise too, and the count is the real one",
      (_type3, "3 directories matched" in _message3),
      ("RuntimeError", True))


# ===========================================================================
# SECTION 4: THE CANDIDATE LIST IS SORTED
# ===========================================================================
# This is ALL the sorted() buys, and saying so is the point. When exactly one
# directory matches, order cannot affect the answer; when more than one does,
# the call raises rather than choosing. What sorting fixes is the DIAGNOSIS:
# two machines meeting the same ambiguity print the same message, and the
# "would have resolved to" line names one specific directory rather than
# whichever one scandir offered first.
#
# THE ORDER IS INJECTED RATHER THAN CREATED ON DISK, and the first version of
# this section got that wrong. It made three real directories in reverse-sorted
# order and asserted the message listed them sorted -- which passed with the
# sorted() REMOVED, because APFS handed those three names back in sorted order
# anyway. A check that cannot fail on the machine it runs on is not a check,
# and the whole subject here is that filesystem order is not something to rely
# on IN EITHER DIRECTION.
#
# paths.py does `import glob` and calls `glob.glob(...)`, so rebinding the
# module's own `glob` attribute is a seam that reaches exactly this one call
# and nothing else in the process.

print()
print("=" * 70)
print("Section 4: the candidates are listed in sorted order")
print("=" * 70)


class _ShuffledGlob:
    """Stands in for the `glob` module, returning a fixed unsorted answer."""

    def __init__(self, hits):
        self.hits = list(hits)

    def glob(self, _pattern):
        return list(self.hits)


_UNSORTED = ["/probe/05- Zulu Data/", "/probe/01- Alpha Data/",
             "/probe/03- Mike Data/"]

_real_glob_module = _paths.glob
_paths.glob = _ShuffledGlob(_UNSORTED)
try:
    _type4, _message4 = raises(lambda: _paths._glob_one("/probe/*Data/", "data"))
    _one_hit = _ShuffledGlob(["/probe/02- Data/"])
    _paths.glob = _one_hit
    _single = _paths._glob_one("/probe/*Data/", "data")
finally:
    _paths.glob = _real_glob_module

check("the injected ambiguity raises", _type4, "RuntimeError")

_positions = [_message4.find(_hit) for _hit in sorted(_UNSORTED)]
check("every candidate appears in the message (non-degeneracy)",
      all(_p >= 0 for _p in _positions), True)
check("...and they appear in SORTED order, not in the order glob returned "
      "them",
      _positions, sorted(_positions))
check("...and the 'would have resolved to' line names the sorted first, not "
      "glob's first",
      _message4.split("would have resolved to")[-1].split(",")[0].strip(),
      repr("/probe/01- Alpha Data/") + " on this machine")
check("the seam really was in force -- a single injected hit is returned "
      "unchanged (non-degeneracy)",
      _single, "/probe/02- Data/")
check("...and the real glob module is back",
      _paths.glob is _real_glob_module, True)


# ===========================================================================
# SECTION 5: NO MATCH STILL RAISES, WITH ITS ORIGINAL DIAGNOSIS
# ===========================================================================
# Unchanged by pass 20f-1, and checked because the pass rewrote the function
# around it. This is the message that replaced a bare IndexError.

print()
print("=" * 70)
print("Section 5: no match raises with the original diagnosis")
print("=" * 70)

_none = _tree()
_type5, _message5 = raises(lambda: _paths._glob_one(_none + "/*Data/", "data"))

check("no match raises RuntimeError",
      _type5, "RuntimeError")
check("...the message opens with 'No directory matched'",
      _message5.startswith("No directory matched the data pattern:"), True)
check("...it names the project root in use",
      "Project root in use:" in _message5, True)
check("...it names where that root came from",
      str(_paths._main_path_source) in _message5, True)
check("...and it names the environment variable that overrides it",
      _paths.path_settings.ENV_MAIN_PATH in _message5, True)


shutil.rmtree(_TMP_ROOT, ignore_errors=True)


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
