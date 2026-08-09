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


def python_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
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

    for path in python_files(_CODE_DIR):
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
