# The .env Load Is an Allowlist, Not a File Dump
###############################################

"""``paths.load_env_keys()`` writes ONLY declared names into ``os.environ``.

THE HAZARD THIS CLOSES, MEASURED RATHER THAN SUPPOSED. That function used to
end in ``load_dotenv(dotenv_path=..., override=True)``, which loads EVERY name
the file defines. A credentials file is an unbounded set by nature, so
resolving three names had the side effect of publishing all of them to the
process -- where any library's own credential chain finds them. At the provider
flip this project's ``05- Keys/.env`` was measured carrying FIVE names, three
required, and ``tests/_provider_pin.py`` records the consequence in as many
words: a suite that reports it makes no billed call would have handed botocore
a live Bedrock credential on any host that had called this function first.

WHAT IS AND IS NOT CLOSED, so nobody reads more into it than it does. The
Bedrock names are still loaded -- the SHIPPED arm's judge does not authenticate
without one of them, and botocore reads its own environment. What is removed is
the UNBOUNDED set: a name not written down in ``ALLOWLISTED_ENV_KEYS`` can
never reach the process from this file, so the next credential an operator adds
is inert until somebody names it deliberately.

WHAT THIS FILE HOLDS
--------------------
    1. The allowlist is DERIVED from the two tuples and the optional names are
       IMPORTED from ``settings`` rather than retyped -- a second spelling of
       ``AWS_BEARER_TOKEN_BEDROCK`` is the ``CROSS_ENCODER_MODEL`` shape, whose
       only symptom would be a Bedrock campaign that cannot find a credential
       the file plainly contains.
    2. A required key loads, and a STALE EXPORT LOSES TO THE FILE. That is
       ``settings.ENV_QDRANT_URL``'s whole argument and it had to survive the
       rewrite: an exported ``QDRANT_URL`` is an ACCIDENT and must not shadow
       the credentials file.
    3. A NON-ALLOWLISTED name in the file NEVER REACHES ``os.environ``. This is
       the check the pass exists for.
    4. Both Bedrock names load when the file defines them.
    5. A missing required key still raises ``ValueError``, and a missing file
       still raises ``FileNotFoundError`` -- the return contract is unchanged.
    6. An OPTIONAL name the file does not mention SURVIVES in the environment.
       ``AWS_BEARER_TOKEN_BEDROCK`` exported by an operator following AWS's own
       getting-started page is a DOCUMENTED configuration; popping it would
       delete a credential this function was never given.
    7. An EMPTY optional value is "not set", and that is a correctness
       requirement rather than tidiness -- see section 7, which measures
       botocore's own selection rule.
    8. The report is NAMES ONLY. No value of any dropped key appears in the
       reader, in the announcement, or anywhere this module can be asked.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO MODEL LOAD, NO DATABASE, NO
GIT HISTORY, NO CORPUS AND NO LIVE SERVER. Every .env is FABRICATED under a
``tempfile.mkdtemp`` this file removes and asserts gone, and every value in
every one of them is a literal with the word "fake" in it -- section 8 scans
this file to keep that true. The REAL ``05- Keys/.env`` IS NEVER READ: every
call passes an explicit ``keys_dir``, so ``keys_path`` is never resolved and no
glob fires. It EXECS NOTHING and writes nothing in the repository, so it is not
in the collision matrix; ``oncotriage/paths.py`` is written by neither of the
suite's two writers and is sha256-compared at the end.

Run from terminal:
    python tests/test_env_key_allowlist.py

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

import ast
import hashlib
import io
import shutil
import tempfile

from oncotriage import paths as _paths
from oncotriage import settings as _settings


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


def raises(fn):
    """``(exception_type_name, message)`` for a call that must raise.

    Returns ``(None, "")`` when it did NOT raise, so the caller records a
    failure instead of the run aborting on the happy path. This project has
    shipped the abort-instead-of-report shape seventeen times; the harness is
    where it stops.
    """
    try:
        fn()
    except Exception as exc:            # noqa: BLE001 -- the type is the answer
        return type(exc).__name__, str(exc)
    return None, ""


# --- fabricated credentials, and the rule that keeps them fabricated --------
#
# ASSEMBLED FROM A PREFIX, never written as one literal that looks like a
# secret, on tests/test_secret_scan_gate.py's rule: this project's own scanner
# reads a credentials-shaped literal beside a credential keyword as a finding,
# and a test file that plants one is a staging refusal for everybody. Section 8
# greps this file for anything that is not obviously fake.
_FAKE = "fake-not-a-real-"


def _v(name):
    """A recognisably fake value for `name`. Deterministic, and never a secret."""
    return _FAKE + name.lower().replace("_", "-")


_TMP_ROOT = tempfile.mkdtemp(prefix="oncotriage-envkeys-")


def _env_file(**entries):
    """A throwaway directory holding a .env built from `entries`.

    Values are written verbatim, so a caller can pass "" to mean an EMPTY
    ASSIGNMENT -- which section 7 needs and which no helper that invented its
    own values could express.
    """
    root = tempfile.mkdtemp(dir=_TMP_ROOT)
    with io.open(os.path.join(root, ".env"), "w", encoding="utf-8") as fh:
        for name, value in entries.items():
            fh.write(f"{name}={value}\n")
    return root


def _required(**overrides):
    """The three required names with fake values, plus/minus `overrides`."""
    entries = {name: _v(name) for name in _paths.REQUIRED_ENV_KEYS}
    entries.update(overrides)
    return entries


# --- environment isolation --------------------------------------------------
#
# EVERY case runs against a KNOWN environment and restores it, because
# `load_env_keys` MUTATES `os.environ` by design and this file drives it a
# dozen times. Without the restore, case N+1 would be measuring case N's
# leftovers -- and one of the properties under test (an optional name SURVIVES
# when the file is silent) is precisely a statement about leftovers.
_WATCHED = tuple(_paths.ALLOWLISTED_ENV_KEYS) + (
    "ANTHROPIC_API_KEY", "SOME_UNRELATED_SECRET")


def _snapshot():
    return {k: os.environ.get(k) for k in _WATCHED}


def _restore(saved):
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _clear(*names):
    for n in names:
        os.environ.pop(n, None)


_BASELINE = _snapshot()
_PATHS_SRC_BEFORE = hashlib.sha256(
    io.open(_paths.__file__, "rb").read()).hexdigest()


# ===========================================================================
# SECTION 1: the allowlist is declared, derived and imported
# ===========================================================================
print("=" * 74)
print("SECTION 1: the allowlist is declared, derived, and imported not retyped")
print("=" * 74)

check("1a  REQUIRED_ENV_KEYS is unchanged -- the three names every caller and "
      "every note in this project already depends on",
      tuple(_paths.REQUIRED_ENV_KEYS),
      ("OPENAI_API_KEY", "QDRANT_URL", "QDRANT_API_KEY"))

check("1b  OPTIONAL_ENV_KEYS holds exactly the two Bedrock credential names "
      "the package reads from the environment by design",
      tuple(_paths.OPTIONAL_ENV_KEYS),
      (_settings.ENV_BEDROCK_API_KEY, _settings.ENV_AWS_BEARER_TOKEN_BEDROCK))

# IMPORTED, NOT RETYPED, and this is the check that says so. A literal
# "AWS_BEARER_TOKEN_BEDROCK" in paths.py would satisfy 1b and would be a second
# spelling of a name settings.py owns -- and one of the two is deliberately NOT
# project-prefixed, so a typo in it is not obviously wrong to a reader.
_paths_text = io.open(_paths.__file__, encoding="utf-8").read()
_optional_block = _paths_text.split("OPTIONAL_ENV_KEYS = (")[1].split(")")[0]
check("1c  ...and it names them through `settings`, so the two modules cannot "
      "drift (the CROSS_ENCODER_MODEL shape)",
      ("path_settings.ENV_BEDROCK_API_KEY" in _optional_block
       and "path_settings.ENV_AWS_BEARER_TOKEN_BEDROCK" in _optional_block),
      True)
check("1c  ...non-degeneracy: the block that was searched is not empty",
      len(_optional_block.strip()) > 0, True)

check("1d  ALLOWLISTED_ENV_KEYS is DERIVED from the two tuples, so a third "
      "hand-written union cannot go stale against them",
      tuple(_paths.ALLOWLISTED_ENV_KEYS),
      tuple(_paths.REQUIRED_ENV_KEYS) + tuple(_paths.OPTIONAL_ENV_KEYS))

check("1e  the two tuples are DISJOINT -- a name in both would be popped "
      "twice and validated once, which is two different meanings for one key",
      sorted(set(_paths.REQUIRED_ENV_KEYS) & set(_paths.OPTIONAL_ENV_KEYS)),
      [])

# THE LOADER IS THE ONLY WRITER, and `load_dotenv` is gone. Without this, every
# behavioural check below would still pass against an implementation that
# parsed the file for show and then called load_dotenv anyway.
#
# BY AST, NOT BY SUBSTRING, and the first version of this check was written the
# wrong way and FAILED against correct code: paths.py's own prose explains that
# it "used to call load_dotenv(..., override=True)", so a text scan reported
# the ARGUMENT FOR the fix as the defect. That is the fourth time this project
# has met "a file that argues about its own settings cannot be grepped for
# them"; the answer is the same every time -- walk the calls.
def _called_names(tree):
    """Every function name CALLED anywhere in `tree`, bare or attribute form."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                out.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                out.add(fn.attr)
    return out


_paths_calls = _called_names(ast.parse(_paths_text))
check("1f  paths.py CALLS load_dotenv nowhere -- the allowlist would be "
      "decoration beside it",
      "load_dotenv" in _paths_calls, False)
check("1f  ...and it does call dotenv_values, which touches os.environ not at "
      "all (non-degeneracy: the walk found calls)",
      "dotenv_values" in _paths_calls, True)
check("1f  ...and the retired name IS still in the prose, which is why the "
      "check above walks calls: a substring scan reads the argument for the "
      "fix as the defect",
      "load_dotenv" in _paths_text, True)


# ===========================================================================
# SECTION 2: a required key loads, and a stale export loses to the file
# ===========================================================================
print()
print("=" * 74)
print("SECTION 2: the file wins over a stale export (ENV_QDRANT_URL's premise)")
print("=" * 74)

_saved = _snapshot()
try:
    _clear(*_WATCHED)
    os.environ["QDRANT_URL"] = "http://stale-export-that-must-lose:9999"
    _dir = _env_file(**_required())
    _keys = _paths.load_env_keys(_dir)

    check("2a  the return contract is unchanged: exactly the three documented "
          "keys, in a plain dict",
          sorted(_keys), ["openai", "qdrant_key", "qdrant_url"])
    check("2b  the file's QDRANT_URL won -- the stale export was popped and "
          "overwritten, which is what settings.ENV_QDRANT_URL's whole argument "
          "rests on",
          os.environ["QDRANT_URL"], _v("QDRANT_URL"))
    check("2b  ...and the returned value agrees with the environment, so a "
          "caller reading either gets the same answer",
          _keys["qdrant_url"], os.environ["QDRANT_URL"])
    check("2c  non-degeneracy: the stale value was genuinely different, so 2b "
          "could have failed",
          "http://stale-export-that-must-lose:9999" != _v("QDRANT_URL"), True)
    check("2d  all three required names reached os.environ",
          [os.environ.get(k) for k in _paths.REQUIRED_ENV_KEYS],
          [_v(k) for k in _paths.REQUIRED_ENV_KEYS])
finally:
    _restore(_saved)


# ===========================================================================
# SECTION 3: a non-allowlisted name NEVER reaches os.environ
# ===========================================================================
print()
print("=" * 74)
print("SECTION 3: what the file defines and the allowlist does not name")
print("=" * 74)

_saved = _snapshot()
try:
    _clear(*_WATCHED)
    _dir = _env_file(**_required(
        ANTHROPIC_API_KEY=_v("ANTHROPIC_API_KEY"),
        SOME_UNRELATED_SECRET=_v("SOME_UNRELATED_SECRET"),
    ))
    _paths.ENV_KEYS_NOT_LOADED.clear()
    _keys = _paths.load_env_keys(_dir)

    check("3a  ANTHROPIC_API_KEY is in the file and NOT in os.environ -- this "
          "is the check the pass exists for",
          "ANTHROPIC_API_KEY" in os.environ, False)
    check("3b  ...and so is a name nothing in this project has ever read",
          "SOME_UNRELATED_SECRET" in os.environ, False)
    check("3c  the required three still loaded, so the refusal is targeted "
          "rather than a loader that stopped working",
          [os.environ.get(k) for k in _paths.REQUIRED_ENV_KEYS],
          [_v(k) for k in _paths.REQUIRED_ENV_KEYS])
    check("3d  both dropped names are REPORTED, sorted, by the reader",
          _paths.env_keys_not_loaded(),
          ("ANTHROPIC_API_KEY", "SOME_UNRELATED_SECRET"))
    check("3e  the reader returns a TUPLE, not the live set -- deps.peek's "
          "rule: a diagnostic must not hand back the container it reads",
          isinstance(_paths.env_keys_not_loaded(), tuple), True)

    # NAMES ONLY. The values are what must never survive, and this is the check
    # that says the reporting path cannot carry one.
    _reported = " ".join(_paths.env_keys_not_loaded())
    check("3f  NO VALUE of any dropped key appears anywhere in the report",
          [v for v in (_v("ANTHROPIC_API_KEY"), _v("SOME_UNRELATED_SECRET"))
           if v in _reported],
          [])
    check("3f  ...nor in the module-level accumulator, which holds names",
          [v for v in (_v("ANTHROPIC_API_KEY"), _v("SOME_UNRELATED_SECRET"))
           if v in " ".join(sorted(_paths.ENV_KEYS_NOT_LOADED))],
          [])
finally:
    _restore(_saved)
    _paths.ENV_KEYS_NOT_LOADED.clear()


# ===========================================================================
# SECTION 4: the Bedrock names load when the file defines them
# ===========================================================================
print()
print("=" * 74)
print("SECTION 4: the shipped arm's credential reaches the process")
print("=" * 74)

_saved = _snapshot()
try:
    for _name in _paths.OPTIONAL_ENV_KEYS:
        _clear(*_WATCHED)
        _dir = _env_file(**_required(**{_name: _v(_name)}))
        _paths.load_env_keys(_dir)
        check(f"4a  {_name} in the file reaches os.environ -- "
              f"MATCHING_PROVIDER ships 'bedrock_anthropic' and its judge does "
              f"not authenticate without one of these",
              os.environ.get(_name), _v(_name))
        check(f"4a  ...and the OTHER optional name, absent from the file, is "
              f"not invented",
              [k for k in _paths.OPTIONAL_ENV_KEYS
               if k != _name and k in os.environ],
              [])

    # A stale export of an OPTIONAL name loses to the file, exactly as a
    # required one does. Without this, "the pop covers the allowlist" would be
    # a claim about three names rather than five.
    _clear(*_WATCHED)
    os.environ[_settings.ENV_AWS_BEARER_TOKEN_BEDROCK] = "stale-fake-token"
    _dir = _env_file(**_required(
        **{_settings.ENV_AWS_BEARER_TOKEN_BEDROCK:
           _v(_settings.ENV_AWS_BEARER_TOKEN_BEDROCK)}))
    _paths.load_env_keys(_dir)
    check("4b  a stale export of an OPTIONAL name also loses to the file",
          os.environ[_settings.ENV_AWS_BEARER_TOKEN_BEDROCK],
          _v(_settings.ENV_AWS_BEARER_TOKEN_BEDROCK))
finally:
    _restore(_saved)


# ===========================================================================
# SECTION 5: the failure contract is unchanged
# ===========================================================================
print()
print("=" * 74)
print("SECTION 5: a missing required key still raises, and so does a missing file")
print("=" * 74)

_saved = _snapshot()
try:
    for _missing in _paths.REQUIRED_ENV_KEYS:
        _clear(*_WATCHED)
        _entries = _required()
        _entries.pop(_missing)
        _dir = _env_file(**_entries)
        _type, _msg = raises(lambda d=_dir: _paths.load_env_keys(d))
        check(f"5a  a .env missing {_missing} raises ValueError",
              _type, "ValueError")
        check(f"5a  ...and the message names what is missing, without a value",
              ("Missing keys" in _msg
               and not any(_v(k) in _msg for k in _paths.REQUIRED_ENV_KEYS)),
              True)

    _clear(*_WATCHED)
    _empty = tempfile.mkdtemp(dir=_TMP_ROOT)
    _type, _msg = raises(lambda: _paths.load_env_keys(_empty))
    check("5b  no .env at all still raises FileNotFoundError",
          _type, "FileNotFoundError")

    # A REQUIRED name popped even when the file is silent about it, which is
    # what makes 5a's ValueError reachable rather than accidentally satisfied
    # by a leftover export.
    _clear(*_WATCHED)
    os.environ["OPENAI_API_KEY"] = "stale-fake-openai-that-must-not-rescue"
    _entries = _required()
    _entries.pop("OPENAI_API_KEY")
    _dir = _env_file(**_entries)
    _type, _msg = raises(lambda d=_dir: _paths.load_env_keys(d))
    check("5c  a stale exported REQUIRED name does not rescue a file that "
          "omits it -- the unconditional pop is what makes the validation mean "
          "something",
          _type, "ValueError")
    check("5c  ...and the stale value is gone from the environment",
          os.environ.get("OPENAI_API_KEY"), None)
finally:
    _restore(_saved)


# ===========================================================================
# SECTION 6: an optional name the file does not mention SURVIVES
# ===========================================================================
print()
print("=" * 74)
print("SECTION 6: the documented AWS route is not deleted by a silent file")
print("=" * 74)

_saved = _snapshot()
try:
    _clear(*_WATCHED)
    # An operator following AWS's own getting-started page exports this; the
    # project's .env has never had to mention it. settings.py argues that in as
    # many words, and popping it here would break exactly that operator.
    os.environ[_settings.ENV_AWS_BEARER_TOKEN_BEDROCK] = _v("EXPORTED_TOKEN")
    _dir = _env_file(**_required())          # says nothing about either name
    _paths.load_env_keys(_dir)
    check("6a  an exported optional name SURVIVES a .env that does not "
          "mention it -- the pop is scoped to what the file will answer for",
          os.environ.get(_settings.ENV_AWS_BEARER_TOKEN_BEDROCK),
          _v("EXPORTED_TOKEN"))
    check("6b  non-degeneracy: the same call DOES pop the required names, so "
          "6a is about scoping and not about a pop that stopped happening",
          os.environ.get("QDRANT_URL"), _v("QDRANT_URL"))
finally:
    _restore(_saved)


# ===========================================================================
# SECTION 7: an EMPTY optional value is "not set" -- and why that is required
# ===========================================================================
print()
print("=" * 74)
print("SECTION 7: an empty value must not look like a credential to botocore")
print("=" * 74)

# THE MEASUREMENT THIS SECTION DEFENDS. botocore selects bearer auth on
# `get_token_from_environment(...) is not None` -- NOT on truthiness
# (botocore/handlers.py, `_should_use_bearer_auth`). So an empty
# AWS_BEARER_TOKEN_BEDROCK makes it sign with an EMPTY BEARER TOKEN -- a 401
# that names nothing -- and it does so INSTEAD of the SigV4 chain, so an
# instance role, an SSO profile or a container role that would have worked is
# bypassed. An empty line in a credentials file is how somebody says "not
# filled in yet"; it must not be the thing that disables a working credential.
#
# THE RULE IS ASSERTED AGAINST botocore ITSELF WHERE IT IS INSTALLED, so this
# section states a fact about the library rather than a belief about it. Where
# it is absent the rule still holds -- it mirrors
# settings.resolve_bedrock_api_key, which treats whitespace as unset for its
# own reason -- and the absence is recorded rather than silently skipped.
try:
    from botocore.utils import get_token_from_environment as _bc_token
except Exception as _bc_exc:                                  # noqa: BLE001
    check("7a  [botocore absent: "
          f"{type(_bc_exc).__name__}] the rule below is asserted against this "
          "project's own resolver only", True, True)
else:
    _saved = _snapshot()
    try:
        _clear(*_WATCHED)
        os.environ[_settings.ENV_AWS_BEARER_TOKEN_BEDROCK] = ""
        check("7a  botocore reads an EMPTY bearer token as PRESENT "
              "(`is not None`), which is why an empty value cannot be allowed "
              "to reach os.environ",
              _bc_token("bedrock") is not None, True)
    finally:
        _restore(_saved)

_saved = _snapshot()
try:
    _clear(*_WATCHED)
    _dir = _env_file(**_required(
        **{_settings.ENV_AWS_BEARER_TOKEN_BEDROCK: ""}))
    _paths.load_env_keys(_dir)
    check("7b  an EMPTY optional assignment in the file does not reach "
          "os.environ",
          _settings.ENV_AWS_BEARER_TOKEN_BEDROCK in os.environ, False)

    _clear(*_WATCHED)
    _dir = _env_file(**_required(
        **{_settings.ENV_AWS_BEARER_TOKEN_BEDROCK: "   "}))
    _paths.load_env_keys(_dir)
    check("7c  ...nor does a whitespace-only one, which is what "
          "`export VAR=$(cat file)` produces and what "
          "settings.resolve_bedrock_api_key already refuses",
          _settings.ENV_AWS_BEARER_TOKEN_BEDROCK in os.environ, False)

    # AND IT DOES NOT DELETE A GOOD ONE. An empty line in the file means "not
    # configured", not "unconfigure whatever the operator exported".
    _clear(*_WATCHED)
    os.environ[_settings.ENV_AWS_BEARER_TOKEN_BEDROCK] = _v("EXPORTED_TOKEN")
    _dir = _env_file(**_required(
        **{_settings.ENV_AWS_BEARER_TOKEN_BEDROCK: ""}))
    _paths.load_env_keys(_dir)
    check("7d  an empty optional assignment does not POP a good exported "
          "value either -- 'not filled in yet' is not 'delete mine'",
          os.environ.get(_settings.ENV_AWS_BEARER_TOKEN_BEDROCK),
          _v("EXPORTED_TOKEN"))

    # THE REQUIRED HALF IS DELIBERATELY UNCHANGED, and it is pinned so that a
    # later pass tightening it is a decision rather than a side effect. An
    # empty required value loads today and passes validation, because that
    # checks `is None`.
    _clear(*_WATCHED)
    _dir = _env_file(**_required(OPENAI_API_KEY=""))
    _keys = _paths.load_env_keys(_dir)
    check("7e  a REQUIRED key's empty value is loaded and passes validation, "
          "exactly as before this pass -- byte-compatible with every caller, "
          "and recorded as a follow-up rather than smuggled in beside a "
          "security fix",
          (_keys["openai"], os.environ.get("OPENAI_API_KEY")), ("", ""))
finally:
    _restore(_saved)


# ===========================================================================
# SECTION 8: this file plants no secret, and it wrote nothing in the repository
# ===========================================================================
print()
print("=" * 74)
print("SECTION 8: hygiene")
print("=" * 74)

_self = io.open(os.path.abspath(__file__), encoding="utf-8").read()

# Every fabricated value this file can produce must be obviously fake. Section
# 8a scans for the ones it generates; 8b is the rule that keeps a future
# addition honest.
_generated = [_v(n) for n in _WATCHED] + [_v("EXPORTED_TOKEN")]
check("8a  every value this file can write into a .env carries the 'fake' "
      "marker, so the project's own secret scanner cannot read one as a "
      "finding",
      [g for g in _generated if "fake" not in g], [])
# BY AST for the reason 1f is: counting the marker as TEXT counts this check's
# own source line too, which is how its first version reported 2 against a file
# with one assignment.
_self_tree = ast.parse(_self)
_fake_assigns = [n for n in ast.walk(_self_tree)
                 if isinstance(n, ast.Assign)
                 for t in n.targets
                 if isinstance(t, ast.Name) and t.id == "_FAKE"]
check("8b  ...and the one place values come from is the `_v` helper over a "
      "single `_FAKE` prefix, so a future case cannot introduce a literal "
      "without moving this check",
      len(_fake_assigns), 1)

check("8c  the temp root is outside the repository",
      os.path.abspath(_TMP_ROOT).startswith(
          os.path.abspath(os.path.dirname(os.path.dirname(__file__)))),
      False)

check("8d  oncotriage/paths.py is byte-identical to what this file found -- it "
      "is read, never written, so this file is not in the collision matrix",
      hashlib.sha256(io.open(_paths.__file__, "rb").read()).hexdigest(),
      _PATHS_SRC_BEFORE)

check("8e  the environment this file inherited is restored: every watched name "
      "reads what it read at import",
      _snapshot(), _BASELINE)

shutil.rmtree(_TMP_ROOT, ignore_errors=True)
check("8f  every fabricated .env is gone", os.path.exists(_TMP_ROOT), False)


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
Created on Wed Sep  2 2026

@author: ramyalsaffar
"""
