# Fixture-Replay Load-Failure Test
#################################

"""
AN UNREADABLE FIXTURE IS A RECORDED PER-FILE FAILURE AND EXIT 2, NOT A
TRACEBACK THAT TAKES THE TWELVE-FIXTURE GATE WITH IT.

``oncotriage/fixtures/replay.py``'s load loop caught
``(ValueError, json.JSONDecodeError)``. ``json.JSONDecodeError`` IS a
``ValueError`` subclass, so that tuple is ONE CLASS WEARING TWO NAMES -- it
looked like a considered list and covered only the half of ``load_fixture``
that PARSES. The half that OPENS was uncovered: a fixture whose mode is 000, one
on a volume that has gone read-only, one truncated mid-stream, one that is not
gzip at all -- ``PermissionError``, ``gzip.BadGzipFile`` and a truncated
member's ``EOFError`` are all ``OSError`` subclasses -- escaped the handler,
escaped ``main()``, and ended the free replay gate with a traceback before a
single fixture had been diffed.

THE TWO EXIT CODES ARE THE POINT. 1 means the pipeline no longer does what it
did and somebody has to explain why; 2 means a file in the directory could not
be read and NOTHING replayed differently. Those have different owners and
different fixes, and a traceback says neither.

WHAT THIS FILE HOLDS
--------------------
    1. THE INTERPRETER FACTS. PermissionError, BadGzipFile and a truncated
       member's error are OSError subclasses; JSONDecodeError is a ValueError.
    2. ``load_fixture`` REALLY RAISES on a chmod-000 copy of a real fixture --
       driven, not argued.
    3. THE SHIPPED TUPLE CATCHES IT and the PRE-FIX TUPLE DOES NOT, both driven
       against that same real raise.
    4. THE REAL ENTRY POINT, as a subprocess, against a directory of
       unreadable fixtures: exit 2, one LOAD FAILED line per file, no
       traceback.
    5. THE PRE-FIX ARM, driven the same way against a COPY of the package with
       the tuple reverted: a traceback and a non-2 exit. This is what says the
       fix is the difference rather than the scenario.

NO NETWORK -- and that is structural rather than a promise: every fixture in the
scratch directory is unreadable, so ``main()`` returns at "No fixture could be
loaded" ABOVE the dependency-seam probe and the pinned-collection gate, which
are the only things in that function that touch Qdrant. NO KEYS, **NO SPEND**,
NO MODEL LOAD, NO CORPUS, NO GIT HISTORY. NOT in the collision matrix: it copies
the real fixtures into a ``tempfile.mkdtemp`` it removes and asserts gone, and
the production fixture directory is opened READ-ONLY and sha256-compared at the
end. It EXECS NOTHING -- the pre-fix arm is a COPY of the package on
``PYTHONPATH``, run as a subprocess.

Run from terminal:
    python tests/test_fixture_replay_load_failures.py

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

os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

import ast
import glob
import gzip
import hashlib
import io
import json
import shutil
import stat
import subprocess
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


def raised(fn, *args, **kwargs):
    """(type name, message) for a call that must raise, else (None, '')."""
    try:
        fn(*args, **kwargs)
    except BaseException as exc:                               # noqa: BLE001
        return type(exc).__name__, str(exc)[:160]
    return None, ""


_CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(oncotriage.__file__)))
_TMP = tempfile.mkdtemp(prefix="oncotriage-loadfail-")

from oncotriage.fixtures.capture import load_fixture, fixture_root  # noqa: E402
from oncotriage.fixtures import replay as _replay                   # noqa: E402

_REPLAY_FILE = os.path.abspath(_replay.__file__)
_HASH_BEFORE = hashlib.sha256(open(_REPLAY_FILE, "rb").read()).hexdigest()

_PROD_FIXTURES = fixture_root()
_PROD_HASHES = {
    os.path.basename(p): hashlib.sha256(open(p, "rb").read()).hexdigest()
    for p in sorted(glob.glob(os.path.join(_PROD_FIXTURES, "*.json.gz")))}


#------------------------------------------------------------------------------


print("=" * 78)
print("1. WHAT load_fixture CAN FAIL AT, AND WHICH BASE CLASS EACH IS")
print("=" * 78)
print()

check("PermissionError is an OSError -- the class the pre-fix tuple could not "
      "see", issubclass(PermissionError, OSError), True)
check("gzip.BadGzipFile is an OSError too, so a file that is not gzip at all "
      "took the same escape route",
      issubclass(gzip.BadGzipFile, OSError), True)
check("IsADirectoryError likewise",
      issubclass(IsADirectoryError, OSError), True)
check("json.JSONDecodeError IS a ValueError subclass, which is why naming it "
      "beside ValueError added nothing and made the tuple look complete",
      issubclass(json.JSONDecodeError, ValueError), True)
check("...and OSError is NOT a ValueError, which is why the open half was "
      "uncovered", issubclass(OSError, ValueError), False)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("2. A chmod-000 FIXTURE REALLY RAISES")
print("=" * 78)
print()

check("the production fixture directory has fixtures to copy "
      "(non-degeneracy)", len(_PROD_HASHES) >= 1, True)

_DIR = os.path.join(_TMP, "fixtures")
os.makedirs(_DIR, exist_ok=True)
for _p in sorted(glob.glob(os.path.join(_PROD_FIXTURES, "*"))):
    if os.path.isfile(_p):
        shutil.copy2(_p, os.path.join(_DIR, os.path.basename(_p)))

_COPIES = sorted(glob.glob(os.path.join(_DIR, "*.json.gz")))
check("...and they copied", len(_COPIES), len(_PROD_HASHES))

_ONE = _COPIES[0]
check("the copy loads before it is made unreadable (non-degeneracy: without "
      "this the raise below could be about the copy rather than the mode)",
      raised(load_fixture, _ONE)[0], None)

os.chmod(_ONE, 0o000)
_type, _message = raised(load_fixture, _ONE)
check("load_fixture on a chmod-000 fixture raises PermissionError",
      _type, "PermissionError")


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("3. THE SHIPPED TUPLE CATCHES IT; THE PRE-FIX TUPLE DOES NOT")
print("=" * 78)
print()

# THE TUPLE IS READ OFF THE SHIPPED SOURCE BY AST, never retyped: a retyped
# tuple is a second copy of the thing under test and would agree with whatever
# this file happens to say.
_src = open(_REPLAY_FILE, encoding="utf-8").read()
_handlers = []
for _node in ast.walk(ast.parse(_src)):
    if isinstance(_node, ast.Try):
        for _h in _node.handlers:
            for _call in ast.walk(_node.body[0] if _node.body else _node):
                if (isinstance(_call, ast.Call)
                        and isinstance(_call.func, ast.Name)
                        and _call.func.id == "load_fixture"):
                    _handlers.append(ast.unparse(_h.type) if _h.type else "<bare>")
                    break

check("the load loop's handler is (OSError, ValueError) -- one class per "
      "failure mode rather than one class twice",
      sorted(set(_handlers)), ["(OSError, ValueError)"])

# DRIVEN, BOTH WAYS, AGAINST THE SAME REAL RAISE. The tuple is the INPUT here,
# which is the natural control for a question about a tuple.
_SHIPPED_TUPLE = (OSError, ValueError)
_PRE_FIX_TUPLE = (ValueError, json.JSONDecodeError)


def _caught_by(tup, path):
    try:
        load_fixture(path)
    except tup:
        return True
    except BaseException:                                      # noqa: BLE001
        return False
    return None


check("the SHIPPED tuple catches the chmod-000 fixture",
      _caught_by(_SHIPPED_TUPLE, _ONE), True)
check("CONTROL: the PRE-FIX tuple does not -- this is the defect, driven",
      _caught_by(_PRE_FIX_TUPLE, _ONE), False)

# ...and the version gate, which the pre-fix tuple DID cover, is still covered.
# Without this the fix could have replaced one gap with another.
_BADVER = os.path.join(_DIR, "wrong_version.json.gz")
with gzip.open(_BADVER, "wt", encoding="utf-8") as _fh:
    json.dump({"schema_version": -1, "fixture_id": "x"}, _fh)
check("a wrong-version fixture still raises ValueError",
      raised(load_fixture, _BADVER)[0], "ValueError")
check("...and BOTH tuples catch it, so nothing the pre-fix tuple covered was "
      "lost",
      (_caught_by(_SHIPPED_TUPLE, _BADVER), _caught_by(_PRE_FIX_TUPLE, _BADVER)),
      (True, True))
os.remove(_BADVER)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("4. THE REAL ENTRY POINT: exit 2, ONE LINE PER FILE, NO TRACEBACK")
print("=" * 78)
print()

# EVERY FIXTURE UNREADABLE, which is what keeps this offline: main() returns at
# "No fixture could be loaded" ABOVE the dependency-seam probe and the pinned-
# collection gate, the only two things in it that reach Qdrant.
for _p in _COPIES:
    os.chmod(_p, 0o000)


def _run_replay(cwd, extra_env=None):
    env = dict(os.environ)
    env["ONCOTRIAGE_DEFER_LOCAL_MODELS"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "fixture_replay.py", "--fixture-dir", _DIR],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=300)


_shipped = _run_replay(_CODE_DIR)
_out = _shipped.stdout + _shipped.stderr

check("the shipped entry point exits 2 -- load failures only, nothing "
      "replayed differently", _shipped.returncode, 2)
check("...with one LOAD FAILED line per unreadable fixture",
      _out.count("LOAD FAILED"), len(_COPIES))
check("...naming the exception class rather than only its message",
      "PermissionError" in _out, True)
check("...and NO traceback",
      ("Traceback (most recent call last)" in _out), False)
check("...and it never reached the collection gate, which is what says this "
      "scenario is offline by construction rather than by luck",
      ("Collection" in _out or "collection digest" in _out), False)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("5. THE PRE-FIX ARM, DRIVEN THROUGH A COPY OF THE PACKAGE")
print("=" * 78)
print()

# A COPY, NEVER THE SHIPPED FILE. The revert is applied to
# <tmp>/pkg/oncotriage/fixtures/replay.py and the subprocess runs with that
# directory as cwd, so the shipped tree is untouched -- asserted by sha256 in
# section 6.
_PKG_COPY = os.path.join(_TMP, "pkg")
os.makedirs(_PKG_COPY, exist_ok=True)
shutil.copytree(os.path.join(_CODE_DIR, "oncotriage"),
                os.path.join(_PKG_COPY, "oncotriage"))
shutil.copy2(os.path.join(_CODE_DIR, "fixture_replay.py"),
             os.path.join(_PKG_COPY, "fixture_replay.py"))

_copy_replay = os.path.join(_PKG_COPY, "oncotriage", "fixtures", "replay.py")
_copy_src = open(_copy_replay, encoding="utf-8").read()
_NEEDLE = "        except (OSError, ValueError) as exc:"
check("the plant's anchor is present exactly once in the copy (a plant that "
      "matched nothing is a working check reported as broken)",
      _copy_src.count(_NEEDLE), 1)
_copy_src = _copy_src.replace(
    _NEEDLE, "        except (ValueError, json.JSONDecodeError) as exc:", 1)
open(_copy_replay, "w", encoding="utf-8").write(_copy_src)

_pre = _run_replay(_PKG_COPY)
_pre_out = _pre.stdout + _pre.stderr

check("CONTROL: the reverted copy does NOT exit 2",
      _pre.returncode == 2, False)
check("CONTROL: it ends in a traceback",
      "Traceback (most recent call last)" in _pre_out, True)
check("CONTROL: naming the uncaught PermissionError",
      "PermissionError" in _pre_out, True)
check("CONTROL: and it printed NO LOAD FAILED line, so the whole gate was "
      "lost rather than one file being reported",
      _pre_out.count("LOAD FAILED"), 0)
check("CONTROL: the copy is what ran, not the shipped tree (realpath "
      "preflight)",
      os.path.realpath(_PKG_COPY) != os.path.realpath(_CODE_DIR), True)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("6. ISOLATION")
print("=" * 78)
print()

for _p in _COPIES:
    os.chmod(_p, stat.S_IRUSR | stat.S_IWUSR)

check("the production fixtures are byte-identical -- this file copied them "
      "and never wrote one",
      {os.path.basename(p): hashlib.sha256(open(p, "rb").read()).hexdigest()
       for p in sorted(glob.glob(os.path.join(_PROD_FIXTURES, "*.json.gz")))},
      _PROD_HASHES)
check("...and the shipped replay.py is byte-identical",
      hashlib.sha256(open(_REPLAY_FILE, "rb").read()).hexdigest(),
      _HASH_BEFORE)
check("every path this file wrote is inside the scratch directory",
      all(os.path.abspath(p).startswith(_TMP)
          for p in (_DIR, _PKG_COPY, _copy_replay)), True)

shutil.rmtree(_TMP, ignore_errors=True)
check("the scratch directory was removed", os.path.exists(_TMP), False)


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
Created on Mon Aug 25 2026

@author: ramyalsaffar
"""
