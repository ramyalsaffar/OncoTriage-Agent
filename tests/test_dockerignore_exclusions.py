""".dockerignore: no virtual environment escapes, and no exclusion is dead.

WHAT THIS FILE IS ABOUT
=======================
`COPY . /app/` ships whatever the build context contains, and `.dockerignore`
is the only thing narrowing it. That file is a list of NAMES, and a name list
rots -- which is not a worry here, it is a measurement:

  * `09- Testing/ragas-venv/` -- a real, deliberately unpinned environment,
    1.7 GB and 92,649 files -- matched NONE of the five venv patterns in
    `.dockerignore`, because those are root-level names and it is nested. It
    shipped inside every image this project built until 2026-08-20, when
    `/app` measured 1.8 GB against 7 MB for the whole of the rest of it.
  * `__pycache__/` and `.DS_Store` matched at the context root and nowhere
    else, so 18 `__pycache__` directories holding 94 `.pyc` files, and three
    nested `.DS_Store`, were inside `/app` for the same reason.

Both are fixed. NOTHING FAILED WHEN THEY WERE BROKEN, and nothing would fail if
either fix were reverted tomorrow, which is what this file is for. It is the
same doctrine as `.github/scripts/trivyignore_staleness.py`: an exemption that
has stopped describing anything is re-read by the next person as a live
constraint, and a scanner -- or a build -- is perfectly happy to be handed one
and says so nowhere.

WHY A SEPARATE FILE FROM tests/test_trivyignore_staleness.py
------------------------------------------------------------
That file's doctrine is the same and its SUBJECT is not. Its whole docstring,
its bucket-A evidence string, its collision-matrix derivation and its section
15 hygiene list each enumerate the three files it reads BY NAME; adding
`.dockerignore` to it makes four statements wrong at once. More to the point,
the two have different failure owners: a change to the Docker build context
would turn red a file named for the Trivy gate, which is the misleading-failure
class this project keeps recording. And this file needs a SKIP mechanism, for
the reason in the next paragraph, which that one does not have and should not
grow.

THE OBVIOUS FORM OF THIS CHECK IS RED IN CI FOREVER, AND THAT IS MEASURED
--------------------------------------------------------------------------
"Assert a `pyvenv.cfg` still exists under the excluded path" cannot be a
standing check as written: `09- Testing/` is UNTRACKED and self-ignored (the
venv writes its own `.gitignore` holding `*`), so `git ls-files` returns
nothing for it and no hosted runner will ever have it. A check written that way
passes on the author's machine and fails on every runner -- the exact shape
`static_checks.py` records for the syntax gate it had to narrow, where a gate
was red where nobody was watching and green where they were.

So the tree-dependent half is a SKIP when the tree has no environment to talk
about, and a skip is NOT a pass: it is counted separately and printed even at
zero, on the precedent `tests/test_package_invariants.py` set. What survives in
CI is everything that reads the committed `.dockerignore` -- the line-presence
checks -- plus EVERY control, because the controls drive a pure function with
fabricated inputs rather than the filesystem.

THE INVARIANT IS MARKER-BASED WHERE IT CAN BE, WHICH IS WHAT MAKES IT
RENAME-PROOF
---------------------------------------------------------------------
"The line `09- Testing/` is present" is itself a name that rots: renumber the
directory to `10- Testing/` and the line is still present, still true-looking,
and 1.7 GB is silently back in the context. So the load-bearing check is the
other direction and is keyed on the MARKER `python -m venv` writes:

    EVERY directory in the build context carrying a `pyvenv.cfg` must have
    itself, or one of its ancestors, declared as a line in `.dockerignore`.

That is the same marker `_is_virtualenv` in `.github/scripts/static_checks.py`
uses, and for the same argument: the marker is what the thing IS, where the
name is what somebody called it. `.dockerignore` has no marker-based form --
it cannot be told "any directory carrying a pyvenv.cfg" -- so each one must be
NAMED, and this is what fails when one stops being.

A rename is then caught twice and in two different voices: the moved venv is no
longer covered (section 2 fails, naming the venv), and the line that used to
cover it now describes nothing (section 3 fails, naming the line).

WHAT IS DELIBERATELY NOT DONE HERE: `.dockerignore` PATTERN MATCHING
--------------------------------------------------------------------
Docker matches with Go's `filepath.Match` plus its own `**` extension, and a
second implementation of that in Python is a second implementation -- it would
agree with Docker exactly until the day it did not, and that day it would be
this file reporting a defect the build does not have, or missing one it does.
So section 2 asks a strictly simpler question with no globbing in it: is this
path, or a directory ABOVE it, written out as a literal line? That is an
UNDER-approximation -- a venv excluded by a glob rather than by name would be
reported here as undeclared -- and it is the right direction to be wrong in,
because the repair is to name it, which is what the `.dockerignore` comment
asks for anyway. It is stated here rather than discovered.

THE `**` PATTERNS ARE NOT TRUSTED FROM THIS FILE EITHER. All this checks is
that they are still WRITTEN. That they WORK was established by rebuilding: the
exported context lost exactly 97 files (94 `.pyc` + 3 `.DS_Store`) and nothing
else, and `/app` went 18 -> 0 `__pycache__` directories and 3 -> 0 `.DS_Store`.
A test cannot re-run that without a Docker daemon, which is what bucket A is
defined to exclude.

BUCKET A. No network, no keys, no spend, no Docker daemon, no live Qdrant, no
corpus, no database, no git history, no subprocess. It IMPORTS NOTHING from the
package and execs nothing. Everything it writes is inside one
`tempfile.mkdtemp()` -- four marker files, so the venv detector has a control
that can fire on a runner with no environment in the tree -- which it removes
and then asserts gone. NOT in the collision matrix: it writes no repository
file, and the one it reads (`.dockerignore`) is written by neither of the
suite's two writers; it is sha256-compared at the end anyway.
"""

import hashlib
import os
import shutil
import sys
import tempfile


# ===========================================================================
# WHERE THINGS ARE
# ===========================================================================
# realpath, not abspath: `.dockerignore` is compared against paths derived from
# a walk of the same root, and a symlinked checkout would otherwise make the
# two disagree about a prefix. tests/test_trivyignore_staleness.py records the
# measurement that established this -- three checks failed against a
# byte-identical copy under a temp directory only because of macOS's
# /var -> /private/var link.
_TESTS_DIR = os.path.dirname(os.path.realpath(__file__))
_CODE_DIR = os.path.dirname(_TESTS_DIR)
_DOCKERIGNORE = os.path.join(_CODE_DIR, ".dockerignore")

if not os.path.isfile(_DOCKERIGNORE):
    raise SystemExit(
        f"CANNOT RUN: {_DOCKERIGNORE} not found. This file derives the "
        f"repository root from its own location ({_CODE_DIR}); if the tests "
        f"directory moved, that derivation moved with it.")


def _sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


_SHA_BEFORE = _sha256(_DOCKERIGNORE)


# ===========================================================================
# THE HARNESS
# ===========================================================================
_passed = 0
_failed = 0
_skipped = 0
_SKIPS = []


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def check_true(label, cond):
    check(label, bool(cond), True)


def skip(label, reason):
    """Coverage that could NOT be exercised here. NEVER counted as a pass.

    Same mechanism and same argument as tests/test_package_invariants.py: the
    count is printed even at zero, because a skip count that appears only when
    it is non-zero is indistinguishable from a file that has no skip mechanism
    at all.
    """
    global _skipped
    _skipped += 1
    _SKIPS.append((label, reason))
    print(f"  SKIP  {label}\n          {reason}")


def section(title):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


# ===========================================================================
# THE TWO PURE FUNCTIONS EVERYTHING IS DRIVEN THROUGH
# ===========================================================================
# Pure, so the controls can be different ARGUMENTS rather than a mutated file
# on disk -- the shape tests/test_agent_patient_hash_coverage.py and
# tests/test_indexer_criteria_split_gate.py settled on. It also means every
# control below runs on a hosted runner, where the filesystem half cannot.
def active_patterns(text):
    """The non-blank, non-comment lines of a .dockerignore, in order.

    Docker's own parser strips a leading '#' comment line and trims space; it
    does NOT support a trailing comment on a pattern line (unlike
    `.trivyignore`), so nothing is split on '#' here beyond the full-line form.
    """
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def declaring_line(rel_path, patterns):
    """The literal line that names `rel_path` or a directory above it, or None.

    NO GLOBBING, on purpose -- see the docstring. `rel_path` is
    slash-separated and relative to the context root. A pattern is compared
    with and without a trailing '/', because `.dockerignore` writes directory
    exclusions both ways and Docker cleans the trailing separator off before
    matching.
    """
    wanted = set()
    parts = rel_path.strip("/").split("/")
    for i in range(1, len(parts) + 1):
        prefix = "/".join(parts[:i])
        wanted.add(prefix)
        wanted.add(prefix + "/")
    for line in patterns:
        if line in wanted:
            return line
    return None


def find_virtualenvs(root):
    """Directories under `root` carrying a `pyvenv.cfg`, relative and sorted.

    The marker `python -m venv` writes at the root of every environment it
    creates -- `isfile`, not `exists`, because a directory that happened to be
    named `pyvenv.cfg` is not a virtualenv. Once one is found the walk does not
    descend into it: this project's is 92,649 files and there is nothing
    underneath it that could be a second environment worth reporting
    separately.
    """
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        if os.path.isfile(os.path.join(dirpath, "pyvenv.cfg")):
            rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
            found.append(rel)
            dirnames[:] = []
    return sorted(found)


# The exclusion this pass exists to keep honest, and the two depth patterns.
# Written out here ONCE so a rename is a one-line edit in this file rather than
# a hunt -- and so section 3's failure message can name the line it is about.
TESTING_EXCLUSION = "09- Testing/"
DEPTH_PATTERNS = ("**/__pycache__", "**/.DS_Store")
ROOT_PATTERNS = ("__pycache__/", ".DS_Store")

_TEXT = open(_DOCKERIGNORE, encoding="utf-8").read()
_PATTERNS = active_patterns(_TEXT)
_VENVS = find_virtualenvs(_CODE_DIR)


# ===========================================================================
# SECTION 1 -- THE FILE PARSES AND IS NOT DEGENERATE
# ===========================================================================
section("SECTION 1 -- .dockerignore is readable and non-degenerate")

check_true("1a .dockerignore was read", bool(_TEXT))
check_true("1b it carries a non-trivial number of active patterns "
           f"(measured {len(_PATTERNS)})", len(_PATTERNS) >= 20)
check_true("1c ...and comments are excluded from that count, so the number is "
           "patterns rather than lines",
           len(_PATTERNS) < len(_TEXT.splitlines()))
check("1d no active pattern is a comment or blank",
      [p for p in _PATTERNS if not p or p.startswith("#")], [])

# CONTROL for 1b/1d: the parser on inputs whose answers are known. Without
# these, `active_patterns` could be returning the raw line list and 1b would
# still pass.
check("1e CONTROL: comments and blanks are dropped",
      active_patterns("# a\n\n  \nfoo/\n# b\nbar\n"), ["foo/", "bar"])
check("1f CONTROL: a file of only comments yields no patterns",
      active_patterns("# a\n# b\n"), [])
check("1g CONTROL: an indented pattern is trimmed, not dropped",
      active_patterns("   spam/   \n"), ["spam/"])


# ===========================================================================
# SECTION 2 -- NO VIRTUAL ENVIRONMENT ESCAPES THE BUILD CONTEXT
# ===========================================================================
section("SECTION 2 -- every venv in the tree is named in .dockerignore")

print(f"  (found {len(_VENVS)} virtualenv(s): {_VENVS or 'none'})")

if not _VENVS:
    # A CI checkout has none: the only one this project has is untracked and
    # self-ignored. That is not a pass -- there was nothing to check.
    skip("2a every virtualenv in the build context is declared",
         "no directory under the context root carries a pyvenv.cfg. On a "
         "hosted runner this is normal and permanent: `09- Testing/` is "
         "untracked and the venv writes its own .gitignore holding '*', so "
         "no checkout has ever contained it. The logic is still exercised "
         "below -- section 4's controls drive the same functions with "
         "fabricated inputs.")
else:
    for _venv in _VENVS:
        _line = declaring_line(_venv, _PATTERNS)
        check(f"2a {_venv} is excluded from the build context by a literal "
              f"line in .dockerignore",
              _line if _line else
              f"<UNDECLARED: nothing in .dockerignore names {_venv!r} or any "
              f"directory above it, so `COPY . /app/` ships it>",
              _line if _line else "<a declaring line>")
    check_true("2b ...and the answer is a real line rather than an empty "
               "string, so 2a is not satisfied by a falsy match",
               all(declaring_line(v, _PATTERNS) for v in _VENVS))


# ===========================================================================
# SECTION 3 -- THE TESTING-VENV EXCLUSION STILL DESCRIBES SOMETHING
# ===========================================================================
section("SECTION 3 -- the exclusion is present, and it is not dead")

# 3a READS THE COMMITTED FILE ONLY, so it runs everywhere including CI. This is
# the half that fails if somebody deletes the line.
check(f"3a the line excluding the testing venv is present: "
      f"{TESTING_EXCLUSION!r}",
      TESTING_EXCLUSION in _PATTERNS, True)

# 3b IS THE STALENESS HALF, and it needs the tree. The three states are
# distinguished rather than collapsed, because two of them are fine and one is
# a defect:
#
#   no venv anywhere        -> unevaluable (a CI checkout). SKIP.
#   a venv under the line   -> the exclusion describes something. PASS.
#   a venv, none under it   -> the line is dead AND 1.7 GB is back in the
#                              context. FAIL, naming the line.
#
# The last is what a rename of `09- Testing/` produces, and section 2 fails
# alongside it naming the venv -- the same defect said in two voices, which is
# deliberate: one tells you what is now shipping, the other tells you which
# line stopped being true.
_covered = [v for v in _VENVS
            if declaring_line(v, _PATTERNS) == TESTING_EXCLUSION]

if not _VENVS:
    skip(f"3b {TESTING_EXCLUSION!r} still describes something",
         "there is no virtualenv anywhere under the context root, so whether "
         "this exclusion is dead cannot be decided here. It is NOT assumed "
         "live: on a machine that has the environment this is a real check, "
         "and the same question is put to fabricated inputs in section 4.")
elif _covered:
    check(f"3b {TESTING_EXCLUSION!r} still describes something: it covers "
          f"{_covered}", bool(_covered), True)
else:
    check(f"3b {TESTING_EXCLUSION!r} DESCRIBES NOTHING -- the tree carries "
          f"virtualenv(s) at {_VENVS} and none of them is under it. Either "
          f"the directory was renamed (and 1.7 GB is back in the build "
          f"context; see section 2) or the line should go.",
          "dead exclusion", "an exclusion that covers a real venv")


# ===========================================================================
# SECTION 4 -- THE CONTROLS: every finding above, fired on demand
# ===========================================================================
section("SECTION 4 -- controls (pure functions, fabricated inputs)")

# These run EVERYWHERE, including on a runner where sections 2 and 3 skip.
# Without them this file would report nothing but "the line is present" in CI,
# and the logic that makes it rename-proof would be untested there.
_REAL = _PATTERNS

# --- C1: the line deleted --------------------------------------------------
_no_line = [p for p in _REAL if p != TESTING_EXCLUSION]
check("4a CONTROL: with the line deleted, the venv path is undeclared and the "
      "failure names the path",
      declaring_line("09- Testing/ragas-venv", _no_line), None)
check("4b ...and the line really was removed, so 4a is not testing an empty "
      "edit", len(_REAL) - len(_no_line), 1)
check("4c ...while WITH the line the same path is declared, by that exact line",
      declaring_line("09- Testing/ragas-venv", _REAL), TESTING_EXCLUSION)

# --- C2: the directory renamed away ---------------------------------------
_renamed = ["10- Testing/ragas-venv"]
check("4d CONTROL: a renamed directory leaves the venv undeclared -- the line "
      "is still present and no longer covers it",
      declaring_line(_renamed[0], _REAL), None)
check("4e CONTROL: ...and the exclusion is then dead: no venv is under it",
      [v for v in _renamed
       if declaring_line(v, _REAL) == TESTING_EXCLUSION], [])
check_true("4f ...which is a DIFFERENT outcome from the real tree, so 4e is "
           "discriminating rather than always-empty",
           bool(_VENVS) is False
           or [v for v in _VENVS
               if declaring_line(v, _REAL) == TESTING_EXCLUSION] != [])

# --- C3/C4: the ancestor logic is not "always undeclared" -----------------
check("4g CONTROL: a venv at the context root is declared by the existing "
      "`venv/` line", declaring_line("venv", _REAL), "venv/")
check("4h CONTROL: a venv nested under an excluded directory is declared by "
      "that directory", declaring_line("tests/scratch-venv", _REAL), "tests/")
check("4i CONTROL: a venv under no excluded directory is undeclared",
      declaring_line("oncotriage/scratch-venv", _REAL), None)
check("4j CONTROL: the exact path, written out with no trailing slash, is "
      "matched too", declaring_line("09- Testing", _REAL), TESTING_EXCLUSION)

# --- C5: the marker walk finds a venv, and only by its marker -------------
# THE DETECTOR NEEDS A CONTROL THAT FIRES ON A RUNNER, so it is driven against
# a fabricated tree rather than against the real one -- which on CI holds no
# environment at all, and where `find_virtualenvs(tests/) == []` would be
# satisfied by a function that always returns []. Four markers, four claims:
# the marker is what is detected, a look-alike NAME is not, a nested
# environment inside one is not descended into, and a DIRECTORY called
# `pyvenv.cfg` is not a marker (the `isfile` rather than `exists` argument).
_FAKE = tempfile.mkdtemp(prefix="dockerignore-venv-detector-")
os.makedirs(os.path.join(_FAKE, "real-env", "deep-env"))
os.makedirs(os.path.join(_FAKE, "venv-lookalike"))
os.makedirs(os.path.join(_FAKE, "dir-marker", "pyvenv.cfg"))
open(os.path.join(_FAKE, "real-env", "pyvenv.cfg"), "w").close()
open(os.path.join(_FAKE, "real-env", "deep-env", "pyvenv.cfg"), "w").close()

check("4k CONTROL: the marker is what is detected -- and only the outermost, "
      "because the walk does not descend into an environment",
      find_virtualenvs(_FAKE), ["real-env"])
check_true("4l CONTROL: ...so a directory merely NAMED like an environment is "
           "not one", "venv-lookalike" not in find_virtualenvs(_FAKE))
check_true("4m CONTROL: ...nor is a directory called `pyvenv.cfg`, which is "
           "why the detector uses isfile and not exists",
           "dir-marker" not in find_virtualenvs(_FAKE))
check_true("4n CONTROL: ...and the nested environment really was there, so 4k "
           "is a non-descent claim rather than a claim about an empty tree",
           os.path.isfile(os.path.join(_FAKE, "real-env", "deep-env",
                                       "pyvenv.cfg")))
check("4o CONTROL: an undeclared fabricated venv is undeclared -- the two "
      "halves compose",
      declaring_line("real-env", _REAL), None)

shutil.rmtree(_FAKE, ignore_errors=True)
check("4p the fabricated tree is removed", os.path.isdir(_FAKE), False)


# ===========================================================================
# SECTION 5 -- THE DEPTH PATTERNS ARE STILL WRITTEN
# ===========================================================================
section("SECTION 5 -- **/__pycache__ and **/.DS_Store")

for _pattern in DEPTH_PATTERNS:
    check(f"5a {_pattern!r} is present", _pattern in _PATTERNS, True)
for _pattern in ROOT_PATTERNS:
    check(f"5b the root-level {_pattern!r} is kept beside it", 
          _pattern in _PATTERNS, True)

check_true("5c the depth and root forms are distinct patterns, so 5a and 5b "
           "are not the same check twice",
           set(DEPTH_PATTERNS).isdisjoint(ROOT_PATTERNS))

# CONTROL: the presence test discriminates.
check("5d CONTROL: a pattern that is not in the file reports absent",
      "**/definitely-not-a-pattern" in _PATTERNS, False)
check("5e CONTROL: with the depth pattern removed, 5a's test reports absent",
      "**/__pycache__" in [p for p in _REAL if p != "**/__pycache__"], False)

# WHAT THIS SECTION DOES NOT CLAIM, stated so the PASS is not read as more than
# it is: that `**` WORKS. That was established by rebuilding -- the exported
# build context lost exactly 97 files (94 .pyc, 3 .DS_Store) and nothing else,
# and /app went 18 -> 0 __pycache__ directories and 3 -> 0 .DS_Store -- and it
# cannot be re-established without a Docker daemon. `**` is Docker's documented
# extension to filepath.Match rather than part of it, and extensions have had
# real bugs; the rebuild is the evidence, this is only the guard against the
# line being deleted.
# PRUNED THE SAME WAY find_virtualenvs IS, and the first version was not:
# a bare os.walk descends into `09- Testing/ragas-venv` and counts ITS 4,500
# `__pycache__` directories, which are not in the build context at all (the
# whole directory is excluded) and are not this project's. It reported 4521
# where the answer about the context is 18. A count that walks somewhere the
# subject does not is not a smaller number, it is a different question.
_nested_pyc = 0
for _dp, _dn, _fn in os.walk(_CODE_DIR):
    _dn[:] = [d for d in _dn if d != ".git"]
    if os.path.isfile(os.path.join(_dp, "pyvenv.cfg")):
        _dn[:] = []
        continue
    _nested_pyc += sum(1 for d in _dn if d == "__pycache__") \
        if os.path.realpath(_dp) != _CODE_DIR else 0
print(f"  (the tree currently holds {_nested_pyc} nested __pycache__ "
      f"director(y/ies); reported, not gated on -- a clean checkout has none)")


# ===========================================================================
# SECTION 6 -- HYGIENE
# ===========================================================================
section("SECTION 6 -- nothing was written")

check("6a .dockerignore is byte-identical after the run",
      _sha256(_DOCKERIGNORE), _SHA_BEFORE)
check_true("6b ...and that is a real digest, not None on both sides",
           isinstance(_SHA_BEFORE, str) and len(_SHA_BEFORE) == 64)


# ===========================================================================
section("SUMMARY")
print(f"  passed:  {_passed}")
print(f"  failed:  {_failed}")
# ALWAYS PRINTED, EVEN AT ZERO. A skip count that appears only when it is
# non-zero is indistinguishable from a file that has no skip mechanism at all.
print(f"  skipped: {_skipped}   (a skip is NOT a pass and is not counted as one)")
for _label, _reason in _SKIPS:
    print(f"    - {_label}")

if __name__ == "__main__":
    sys.exit(1 if _failed else 0)


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug 21 2026

@author: ramyalsaffar
"""
