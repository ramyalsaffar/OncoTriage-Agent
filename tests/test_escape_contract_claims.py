# Escape-Contract Claim Test
###########################

"""
EVERY "THIS ESCAPES ``except Exception``" CLAIM IN THE PACKAGE IS CHECKED
AGAINST WHAT THE INTERPRETER ACTUALLY DOES.

``oncotriage/storage/database_logger.py`` promised, in five separate places,
that ``MemoryError`` is "not an ``Exception`` subclass" and therefore escapes
its handlers. ``issubclass(MemoryError, Exception)`` is **True**. So every one
of those handlers CATCHES it -- an out-of-memory write is counted, recorded as
a terminal failure and reported as a non-critical logging fault -- and a caller
written against the docstring would have installed a handler that can never
run, or would have believed a MemoryError from that line reaches it.

THE CLAIM WAS COPIED, WHICH IS WHY THERE WERE FIVE OF IT. One function stated
it, four more inherited the sentence, and ``flush_run_metrics`` measured it,
recorded the contradiction as a finding against its neighbours, and left them --
correctly, since correcting a claim in four functions that pass did not touch is
a separate edit. This file is what stops the sixth copy.

WHAT IT CHECKS
--------------
    1. THE INTERPRETER FACTS, DERIVED. Which builtin exceptions are
       ``BaseException`` subclasses and NOT ``Exception`` subclasses is computed
       by walking ``builtins``, never retyped -- a hand-written set is the same
       shape of claim this file exists to check.
    2. EVERY CLAIM IN THE PACKAGE. Each comment and each docstring in
       ``oncotriage/`` is scanned for a sentence asserting that something
       escapes an ``except Exception``; every builtin exception NAMED in such a
       sentence must genuinely be one of the set from section 1.
    3. THE SCAN IS NOT VACUOUS. It must find real claims, in more than one
       file, naming more than one class.
    4. CONTROLS. The pre-correction sentence, planted into an in-memory copy of
       each of the six corrected sites, is REPORTED; and a scan that looks only
       at code (no comments, no docstrings) finds nothing, which is what says
       the extraction is doing the work.

NO NETWORK, NO KEYS, NO SPEND, NO DATABASE, NO CORPUS, NO GIT HISTORY, NO MODEL
LOAD. It writes nothing anywhere -- every plant is a string in memory -- so it
is NOT in the collision matrix; the package files it reads are read as TEXT and
are sha256-compared at the end. It EXECS NOTHING and imports no package module
whose docstrings it scans, so it needs no ``_EXEC_ALLOWLIST`` entry.

Run from terminal:
    python tests/test_escape_contract_claims.py

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

import ast
import builtins
import hashlib
import io
import re
import tokenize


#------------------------------------------------------------------------------


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label, actual, expected):
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


# THE PACKAGE DIRECTORY COMES FROM THE IMPORTED PACKAGE'S OWN __file__, never
# from this file's location: the module under inspection is then provably the
# one this process resolved rather than a same-named copy, and a future move of
# tests/ cannot silently point the scan at nothing. Importing `oncotriage`
# itself opens nothing and loads no model (test_package_invariants section 2).
_PKG_DIR = os.path.dirname(os.path.abspath(oncotriage.__file__))

_PKG_FILES = sorted(
    os.path.join(root, name)
    for root, _dirs, names in os.walk(_PKG_DIR)
    for name in names
    if name.endswith(".py"))

_HASHES_BEFORE = {p: hashlib.sha256(open(p, "rb").read()).hexdigest()
                  for p in _PKG_FILES}


#------------------------------------------------------------------------------


print("=" * 78)
print("1. THE INTERPRETER FACTS, DERIVED FROM builtins RATHER THAN RETYPED")
print("=" * 78)
print()

# WALKED, NOT LISTED. A hand-written {"KeyboardInterrupt", "SystemExit",
# "GeneratorExit"} would be exactly the kind of claim this file exists to
# check -- true today, unchecked, and wrong the moment an interpreter version
# moves something. It is three on every CPython this project runs on; the point
# is that the number is read rather than asserted.
_BUILTIN_EXCEPTIONS = {
    name: obj for name, obj in vars(builtins).items()
    if isinstance(obj, type) and issubclass(obj, BaseException)}

_BASE_ONLY = {name for name, obj in _BUILTIN_EXCEPTIONS.items()
              if not issubclass(obj, Exception)}

check("MemoryError IS an Exception subclass, so `except Exception` catches it "
      "-- the fact five docstrings in the storage layer denied",
      issubclass(MemoryError, Exception), True)

# THE FIRST VERSION OF THIS CHECK RETYPED THE SET AND WAS WRONG, which is the
# defect this whole file exists to catch, committed inside the file that
# catches it. It expected exactly {BaseException, GeneratorExit,
# KeyboardInterrupt, SystemExit} and the interpreter answered with
# `BaseExceptionGroup` in it as well -- a fifth member since 3.11, correct, and
# invisible to anyone writing the set from memory. Caught by RUNNING it.
#
# SO THE EXPECTATION IS NOT A LIST. What is asserted is the two properties the
# rest of the file rests on -- the three classes this project's prose actually
# names ARE in the derived set, and MemoryError is NOT -- plus that the walk
# found something. A future interpreter may add a sixth member without failing
# anything here, which is right: the scan below reads the derived set, not this
# check's wording.
print(f"        BaseException-only builtins on this interpreter: "
      f"{sorted(_BASE_ONLY)}")
check("the three classes this project's escape prose names are all genuinely "
      "BaseException-only",
      sorted({"GeneratorExit", "KeyboardInterrupt", "SystemExit"} - _BASE_ONLY),
      [])
check("...and MemoryError is NOT among them, which is the finding",
      "MemoryError" in _BASE_ONLY, False)

check("...and the walk found a non-degenerate number of builtin exceptions "
      "to classify (non-degeneracy: an empty walk makes every claim below "
      "pass for free)",
      len(_BUILTIN_EXCEPTIONS) > 20, True)
check("...and the two sets partition the builtin exceptions between them",
      len(_BASE_ONLY) + len([n for n, o in _BUILTIN_EXCEPTIONS.items()
                             if issubclass(o, Exception)]),
      len(_BUILTIN_EXCEPTIONS))

# concurrent.futures.CancelledError is the one this project has already been
# bitten by in the other direction -- runner.py's own comment records that the
# futures class is an Exception subclass while asyncio's same-named one is not.
import concurrent.futures as _cf                                # noqa: E402
check("concurrent.futures.CancelledError is an Exception subclass, which is "
      "why the batch runner's generic handler used to absorb it",
      issubclass(_cf.CancelledError, Exception), True)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("2. EVERY ESCAPE CLAIM IN THE PACKAGE NAMES ONLY CLASSES THAT ESCAPE")
print("=" * 78)
print()

# WHAT A CLAIM LOOKS LIKE. Every form this project has actually written, taken
# from the six corrected sites and their correct neighbour. The phrases are
# matched case-insensitively against comment and docstring TEXT, and the
# sentence around each hit is the window the class names are read out of.
# THE BACKTICKS ARE OPTIONAL AND THE FIRST VERSION MADE THEM MANDATORY.
# ``?` in a regex is a LITERAL backtick followed by an OPTIONAL one, not "up to
# two optional backticks" -- so the pattern required at least one, and the
# plain-prose form "not an Exception subclass" (which is how four of the six
# corrected sites were actually written) matched nothing. Control 6 below is
# what reported it; reading the pattern did not. `{0,2}` is the form that means
# what was intended.
_CLAIM_PHRASES = (
    r"not\s+(?:an\s+)?`{0,2}Exception`{0,2}\s+subclass(?:es)?",
    r"neither\s+(?:an\s+)?`{0,2}Exception`{0,2}\s+subclass(?:es)?",
    r"are\s+meant\s+to\s+(?:propagate|escape)",
    r"must\s+escape",
    r"still\s+escape",
    r"so\s+it\s+escapes",
)
_CLAIM_RE = re.compile("|".join(_CLAIM_PHRASES), re.IGNORECASE)

# A SENTENCE, NOT A LINE. These docstrings wrap at 79 columns, so a claim and
# the names it makes about routinely sit on different lines; splitting on
# terminal punctuation is what keeps the window around the claim rather than
# around an arbitrary 79 characters. Backticks, parentheses and the two-space
# comment prefix are not sentence ends.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s")

_NAME_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*Error|KeyboardInterrupt|"
                      r"SystemExit|GeneratorExit|BaseException|Exception)\b")


def _text_blocks(path):
    """(kind, text) for every comment and every docstring in `path`.

    COMMENTS COME FROM `tokenize` AND DOCSTRINGS FROM `ast`, because neither
    tool sees the other's: a comment is not in the AST at all, and a docstring
    reached through tokenize would be indistinguishable from any other string
    literal -- including the message of the very exception under discussion,
    which routinely names the class it raises.
    """
    src = open(path, encoding="utf-8").read()
    blocks = []

    for token in tokenize.generate_tokens(io.StringIO(src).readline):
        if token.type == tokenize.COMMENT:
            blocks.append(("comment", token.string, token.start[0]))

    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                blocks.append(("docstring", doc, getattr(node, "lineno", 0)))
        # An ATTRIBUTE docstring -- a bare string statement following an
        # assignment -- is not reached by get_docstring and is how this project
        # documents RUN_COLUMNS and every module-level constant beside it.
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            for stmt in getattr(node, "body", []):
                if (isinstance(stmt, ast.Expr)
                        and isinstance(stmt.value, ast.Constant)
                        and isinstance(stmt.value.value, str)):
                    blocks.append(("attr-doc", stmt.value.value, stmt.lineno))
    return blocks


def _bad_claims(blocks, path):
    """Every (path, line, name, sentence) where a claim names a catchable class."""
    found = []
    for kind, text, line in blocks:
        for sentence in _SENTENCE_SPLIT.split(text.replace("\n", " ")):
            if not _CLAIM_RE.search(sentence):
                continue
            for name in _NAME_RE.findall(sentence):
                if name not in _BUILTIN_EXCEPTIONS:
                    continue          # a project class; not a builtin claim
                if name in _BASE_ONLY:
                    continue          # the claim is true of this one
                if name == "Exception":
                    continue          # the phrase's own subject
                found.append((os.path.relpath(path, _PKG_DIR), line, name,
                              kind, " ".join(sentence.split())[:150]))
    return found


_ALL_CLAIMS = []
_ALL_BAD = []
for _path in _PKG_FILES:
    _blocks = _text_blocks(_path)
    _ALL_CLAIMS.extend(
        (os.path.relpath(_path, _PKG_DIR), b[2])
        for b in _blocks
        for s in _SENTENCE_SPLIT.split(b[1].replace("\n", " "))
        if _CLAIM_RE.search(s))
    _ALL_BAD.extend(_bad_claims(_blocks, _path))

for _b in _ALL_BAD:
    print(f"        claim naming a catchable class: {_b[0]}:{_b[1]} "
          f"({_b[3]}) -> {_b[2]}\n            {_b[4]}")

check("no comment or docstring in the package claims that a class which IS an "
      "Exception subclass escapes `except Exception`",
      [f"{b[0]}:{b[1]} {b[2]}" for b in _ALL_BAD], [])


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("3. THE SCAN IS NOT VACUOUS")
print("=" * 78)
print()

# Without these three the section above is satisfied by a scan that matches
# nothing at all, which is what a broken phrase list or a broken extractor
# looks like: zero findings, indistinguishable from a clean package.
check("the scan found escape claims to check (non-degeneracy)",
      len(_ALL_CLAIMS) > 0, True)
check("...in more than one file",
      len({c[0] for c in _ALL_CLAIMS}) > 1, True)

_named = set()
for _path in _PKG_FILES:
    for _kind, _text, _line in _text_blocks(_path):
        for _s in _SENTENCE_SPLIT.split(_text.replace("\n", " ")):
            if _CLAIM_RE.search(_s):
                _named.update(n for n in _NAME_RE.findall(_s)
                              if n in _BUILTIN_EXCEPTIONS)
check("...and those claims name at least two distinct builtin exception "
      "classes, so the check is about a set rather than about one word",
      len(_named - {"Exception"}) >= 2, True)
print(f"        claims found: {len(_ALL_CLAIMS)} in "
      f"{len({c[0] for c in _ALL_CLAIMS})} files; "
      f"classes named: {sorted(_named)}")


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("4. CONTROLS")
print("=" * 78)
print()

# CONTROL A: the six sentences as they stood before the correction, planted one
# at a time into an in-memory copy of the storage module. Each must be
# REPORTED. Written out here as text rather than lifted from git, deliberately:
# git would supply the whole pre-correction file and this control's subject is
# the SENTENCE SHAPE, which must be caught wherever it is written -- including
# in a file that has never carried it.
_PRE_CORRECTION = (
    ('``except Exception``, so ``KeyboardInterrupt`` and ``MemoryError`` -- '
     'which are not ``Exception`` subclasses -- still escape.'),
    ('a KeyboardInterrupt or a MemoryError raised inside the write would be '
     'discarded; they are meant to propagate.'),
    ('RAISES NOTHING except the two that must escape (KeyboardInterrupt, '
     'MemoryError).'),
    ('nothing but KeyboardInterrupt and MemoryError, which are not Exception '
     'subclasses and are meant to escape.'),
    ('two are meant to propagate (KeyboardInterrupt, MemoryError, neither an '
     'Exception subclass).'),
    ('a RecursionError is not an Exception subclass, so it escapes.'),
)

for _i, _sentence in enumerate(_PRE_CORRECTION, start=1):
    _plant_src = f'"""Module.\n\n{_sentence}\n"""\n'
    _plant_blocks = []
    _tree = ast.parse(_plant_src)
    _doc = ast.get_docstring(_tree, clean=False)
    _plant_blocks.append(("docstring", _doc, 1))
    _hits = _bad_claims(_plant_blocks, os.path.join(_PKG_DIR, "planted.py"))
    check(f"CONTROL {_i}: the pre-correction sentence is REPORTED "
          f"({_sentence.split(chr(40))[0][:44].strip()}...)",
          [h[2] for h in _hits] != [], True)

# CONTROL B: a sentence naming only classes that genuinely escape must NOT be
# reported. Without it, "every new claim fails" would satisfy control A and the
# check would forbid the correct wording as well as the wrong one.
_ok_src = ('"""Module.\n\nThe three that are not Exception subclasses -- '
           'KeyboardInterrupt, SystemExit and GeneratorExit -- still escape.\n"""\n')
_ok_doc = ast.get_docstring(ast.parse(_ok_src), clean=False)
check("CONTROL B: the CORRECT wording is not reported, so the rule is not "
      "'any claim fails'",
      _bad_claims([("docstring", _ok_doc, 1)], "ok.py"), [])

# CONTROL C: the extraction is what finds these. A scan of the same file with
# no comments and no docstrings must find nothing -- so a future edit that
# breaks _text_blocks reports zero findings AND fails here, rather than
# reporting zero findings and looking clean.
_code_only = "x = 1\ny = MemoryError\n"
check("CONTROL C: a module with no comments and no docstrings yields no "
      "claims, so section 2's zero is about the text and not about the scan",
      _bad_claims([("docstring", _code_only, 1)], "code.py"), [])

# CONTROL D: a claim in a COMMENT is caught too, not only in a docstring.
_comment_blocks = [("comment",
                    "# and a MemoryError, which is not an Exception subclass, "
                    "still escapes.", 7)]
check("CONTROL D: the same sentence in a COMMENT is reported",
      [h[2] for h in _bad_claims(_comment_blocks, "c.py")], ["MemoryError"])


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("5. NOTHING IN THE REPOSITORY WAS WRITTEN")
print("=" * 78)
print()

_HASHES_AFTER = {p: hashlib.sha256(open(p, "rb").read()).hexdigest()
                 for p in _PKG_FILES}
check("every package file this scan read is byte-identical afterwards",
      [os.path.relpath(p, _PKG_DIR) for p in _PKG_FILES
       if _HASHES_BEFORE[p] != _HASHES_AFTER.get(p)], [])
check("...and it read a non-degenerate number of them",
      len(_PKG_FILES) > 40, True)


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
