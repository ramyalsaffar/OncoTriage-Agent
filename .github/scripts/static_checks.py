# Static Checks
###############

"""Compile every Python file in the repository, and report what is NOT checked.

WHY THIS IS A SYNTAX GATE AND NOT A LINTER
-------------------------------------------
There is no lint or type-check configuration in this repository. Measured, not
assumed: `pyproject.toml` has exactly three tables -- `[build-system]`,
`[project]`, `[tool.setuptools*]` -- and there is no `setup.cfg`, no `tox.ini`,
no `.flake8`, no `ruff.toml`, no `mypy.ini` and no `.pre-commit-config.yaml`.

So there is no configured style to enforce, and inventing one here would mean
choosing a rule set for a 100k-line codebase in a CI pass, then reformatting
source to satisfy it. This file does the part that needs no configuration and
cannot be a matter of taste: every file must COMPILE, on the interpreter the
container actually runs.

THE INTERPRETER IS THE POINT. The Dockerfile pins
`python:3.11-slim@sha256:94c50be2...`, verified by running it to report
3.11.15, and `pyproject.toml` declares `requires-python = ">=3.10"`. The
development machine is on 3.13. A file using syntax newer than 3.11 imports
cleanly for the author and fails in the image -- and the Dockerfile's own header
records that this project has already shipped a base image two minor versions
away from what its tag claimed, found only by running it. So CI compiles on
3.11 and this file refuses to run on anything older than the floor.

IT WRITES NO BYTECODE. `compileall` exists for this and would drop `.pyc` files
through the tree; `tests/test_registries_cancer_code_claims_audit_control.py`
clears `__pycache__` deliberately and pass 20f-1 lost two hours to a stale one.
`compile(source, name, "exec")` answers the same question in memory.

SYNTAX WARNINGS ARE ERRORS HERE. An invalid escape sequence -- a backslash-d
outside a raw string -- is a `SyntaxWarning` on 3.12+ and becomes a
`SyntaxError` in a future release; catching it now costs nothing. This
docstring deliberately spells that out in words rather than showing it, because
this file is compiled by its own gate and the example would fail it.

WHAT THIS DOES NOT CHECK, stated so the badge is not read as more than it is:
no style, no import order, no type annotations, no unused names, no complexity.
The project's real static analysis is `tests/test_package_invariants.py` --
import purity under twelve traps, the config<->utils cycle, one BM25
construction site, no shadowed imports, no never-read names, the decorator
inventory -- and that file is a collision-matrix member, so it runs in the
serial job rather than here.

AND IT DOES NOT WALK A VIRTUAL ENVIRONMENT. `_is_virtualenv` below argues that
at length; the headline is that this gate's subject is code this project owns,
a venv in the tree is code it does not, and the report says how many were
skipped even when the answer is zero. That last part is the half that matters:
this file's first line promises to report what is NOT checked, and a narrowing
that printed nothing would make that promise false.

Run from terminal:
    python .github/scripts/static_checks.py

Exit codes:
    0 -- every file compiled
    1 -- at least one file failed to compile, or the interpreter is too old
"""

import os
import sys
import warnings


_CODE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mirrors what is not source: build artifacts, caches, and the VCS directory.
# The two venv NAMES in here are a convenience, not the guarantee -- see
# `_is_virtualenv` below, which is what actually keeps a third-party
# environment out of this gate.
_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "build", "dist",
              ".mypy_cache", ".pytest_cache", ".ruff_cache"}

# The floor from pyproject.toml's requires-python. Read as a literal rather than
# parsed: this file must run before anything is installed, and a tomllib read of
# a `>=3.10` specifier still needs interpreting.
_MIN = (3, 10)

# What the Dockerfile pins. A mismatch is a WARNING, not a failure: running the
# gate on a newer interpreter is better than not running it, and the workflow is
# what pins 3.11. Stating it keeps a local run honest about what it proved.
_CONTAINER = (3, 11)


def _is_virtualenv(path):
    """True when `path` is the root directory of a virtual environment.

    A VENV IS IDENTIFIED BY ITS ``pyvenv.cfg`` MARKER, NOT BY ITS NAME, which
    is what makes the two venv names in `_SKIP_DIRS` a convenience rather than
    the guarantee. ``09- Testing/ragas-venv/`` is the live proof that a name
    list rots: a real, deliberately un-pinned environment (see
    ``oncotriage/evaluation/ragas_harness.py`` for why ragas is NOT a pipeline
    dependency), untracked and self-ignored, matching neither ``venv`` nor
    ``.venv``. ``python -m venv`` writes ``pyvenv.cfg`` at the root of every
    environment it creates, so the marker is what the thing IS. Same rule, same
    argument, as tests/test_package_invariants.py and
    tests/test_extraction_stage_non_oncology_guard.py.

    WHY THIS GATE MUST NOT WALK ONE, measured on the development machine on
    2026-08-17 rather than argued: with that environment present this gate
    compiled 38,517 files in 23.2s and EXITED 1, on
    ``sknetwork/regression/diffusion.py`` -- an invalid escape sequence in a
    third-party package this project does not own, cannot fix and does not
    ship. Without it the same walk is 189 files in 0.2s. So the gate was RED on
    every development machine and GREEN on GitHub, where no environment is ever
    checked out, which is the worst of both: it reported a defect nobody could
    act on, and it reported it only where nobody was watching. A gate whose red
    is routinely ignored has stopped being a gate.

    ``isfile`` rather than ``exists``: the marker is a FILE, and a directory
    that happened to be named ``pyvenv.cfg`` is not a virtualenv.

    DUPLICATED RATHER THAN SHARED with the two test files above. This file's
    own docstring is the reason: it must run BEFORE anything is installed, so
    it may not import from the package, and `.github/scripts/` is four
    standalone scripts with no module to share a helper through.
    """
    return os.path.isfile(os.path.join(path, "pyvenv.cfg"))


def python_files(root, pruned_out=None):
    """Yield every .py under `root` that is this project's own source.

    `pruned_out`, when a list is passed, receives the path of every virtual
    environment this declined to walk into. IT IS COMPLETE ONLY ONCE THIS
    GENERATOR IS EXHAUSTED -- the walk is lazy, so a caller reading the list
    mid-iteration sees only what has been reached so far. `main()` reads it
    after its loop has ended, which is what makes the report it prints true.

    The prune is a NARROWING of the corpus, which is the direction this
    project's scans are otherwise warned about: one that silently covers less
    does not fail, it reports FEWER findings, which reads exactly like a clean
    tree. Two things make it right here rather than merely convenient. The
    files removed are not this repository's -- they are a third-party
    environment that happens to sit inside it -- so compiling them was never
    coverage of anything this gate makes a claim about. And the removal is
    REPORTED, unconditionally and with a count, so it cannot be mistaken for a
    tree that had nothing to skip.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        keep = []
        for name in sorted(dirnames):
            if name in _SKIP_DIRS:
                continue
            child = os.path.join(dirpath, name)
            if _is_virtualenv(child):
                if pruned_out is not None:
                    pruned_out.append(child)
                continue
            keep.append(name)
        # In place, so os.walk does not descend into what was dropped. Sorted
        # for the reason every walk in this project is: determinism is a stated
        # property, and an unsorted walk makes the ORDER of a failure report
        # depend on os.scandir.
        dirnames[:] = keep
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                yield os.path.join(dirpath, filename)


def main():
    print("=" * 74)
    print("STATIC CHECKS — syntax gate")
    print("=" * 74)
    version = sys.version_info[:3]
    print(f"Interpreter: {'.'.join(map(str, version))}")

    if version[:2] < _MIN:
        print(f"FATAL: this gate needs at least Python {'.'.join(map(str, _MIN))} "
              f"(pyproject.toml requires-python).")
        return 1
    if version[:2] != _CONTAINER:
        print(f"WARNING: the Dockerfile pins Python "
              f"{'.'.join(map(str, _CONTAINER))}; this run proves nothing about "
              f"syntax that differs between {'.'.join(map(str, version[:2]))} "
              f"and {'.'.join(map(str, _CONTAINER))}.")
    print()

    print("NOT CHECKED HERE: style, import order, type annotations, unused")
    print("names, complexity. No linter or type-checker configuration exists in")
    print("pyproject.toml or setup.cfg, so none is invented. See this file's")
    print("docstring, and tests/test_package_invariants.py for the project's")
    print("own static invariants.")
    print()

    failures = []
    checked = 0
    pruned = []

    for path in python_files(_CODE_DIR, pruned_out=pruned):
        rel = os.path.relpath(path, _CODE_DIR)
        try:
            with open(path, "rb") as fh:
                source = fh.read()
        except OSError as exc:
            failures.append((rel, f"{type(exc).__name__}: {exc}"))
            continue

        with warnings.catch_warnings():
            warnings.simplefilter("error", SyntaxWarning)
            warnings.simplefilter("error", DeprecationWarning)
            try:
                compile(source, rel, "exec", dont_inherit=True)
            except SyntaxError as exc:
                failures.append(
                    (rel, f"SyntaxError: {exc.msg} (line {exc.lineno})"))
                continue
            except SyntaxWarning as exc:
                failures.append((rel, f"SyntaxWarning-as-error: {exc}"))
                continue
            except DeprecationWarning as exc:
                failures.append((rel, f"DeprecationWarning-as-error: {exc}"))
                continue
            except ValueError as exc:
                # e.g. a source file containing a null byte
                failures.append((rel, f"ValueError: {exc}"))
                continue
        checked += 1

    # ALWAYS PRINTED, EVEN AT ZERO, and that is the point rather than noise. A
    # count that appears only when it is non-zero is indistinguishable from a
    # gate that has no prune at all -- the identical argument
    # tests/test_package_invariants.py makes for printing its skip count
    # unconditionally. On a hosted runner this reads 0 and the walk is the
    # whole checkout; on a development machine it names what was left out, so
    # the difference between the two runs is on the terminal rather than
    # inferred.
    print(f"NOT WALKED: {len(pruned)} virtual environment(s). A directory "
          f"carrying a")
    print("pyvenv.cfg marker is a third-party environment this project does")
    print("not own, cannot fix and does not ship.")
    for directory in sorted(pruned):
        print(f"  {os.path.relpath(directory, _CODE_DIR)}{os.sep}")
    print()

    print(f"Compiled {checked} file(s).")
    if failures:
        print()
        print(f"FAILED: {len(failures)}")
        for rel, message in failures:
            print(f"  {rel}")
            print(f"      {message}")
        return 1

    # A gate that silently checks nothing looks exactly like a clean tree.
    if checked == 0:
        print("FATAL: no Python files were found, so this gate proved nothing.")
        return 1

    print("All files compiled.")
    return 0


if __name__ == "__main__":
    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Aug  8 2026

@author: ramyalsaffar
"""
