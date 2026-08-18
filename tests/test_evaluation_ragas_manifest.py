# Ragas Manifest Environment Stamp Test
######################################

"""
``ragas_manifest.json`` recorded ``ragas_version`` and nothing else about the
environment that produced a score. Ragas is deliberately not a pipeline
dependency and is not in ``pyproject.toml``, so NOTHING IN THIS REPOSITORY PINS
THAT ENVIRONMENT: a later run under a ragas whose metric prompts, statement
decomposition or defaults had moved would produce different numbers, and the
drift would be indistinguishable from pipeline drift. The harness already
documents faithfulness as non-reproducible sample to sample at temperature 0
(``REPRODUCIBILITY_NOTE``); the environment must not add a second, unrecorded
source of variation on top of a known one.

WHAT THIS FILE COVERS
---------------------
    1. ``environment_stamp()`` reports the interpreter and all four
       distributions, and ``sys.version`` / ``sys.executable`` verbatim.
    2. THE PRESENT PATH -- a real installed distribution comes back as its real
       version. See the environment inversion below for which one.
    3. THE ABSENT PATH -- a distribution that is not installed records
       ``absent`` and does not raise. Driven twice: naturally (whatever is
       genuinely missing here) and PLANTED through the ``version_fn`` seam, so
       the check does not depend on what happens to be installed.
    4. THE UNREADABLE PATH -- a metadata read that fails for a reason OTHER
       than absence must NOT be recorded as ``absent``. Collapsing the two
       would state "not installed" about an environment nobody could read.
    5. The manifest carries the block, keeps ``ragas_version``, and round-trips
       through JSON -- it is written with ``json.dump``, so a value that will
       not serialise is a run that scores for minutes and then cannot write its
       record.
    6. The dry-run plan PRINTS the same fields, through the same renderer.
    7. Neither the stamp nor the manifest builder imports ragas, the Anthropic
       SDK or the OpenAI client. That discipline is what lets ``--help`` and
       ``--dry-run`` run in an environment that has none of them, and a version
       stamp is exactly the kind of change that breaks it.

THE ENVIRONMENT INVERSION, AND WHAT EACH TEST THEREFORE EXERCISES
-----------------------------------------------------------------
The project environment that runs this suite does NOT have ragas installed --
by design; installing it here would drag ``openai`` from 1.x to 2.x and bump
``langgraph``. So the realistic subject of the ABSENT path is ``ragas`` itself,
and the PRESENT path cannot be exercised against ragas at all. Section 2
therefore exercises the present path against distributions that ARE installed
here -- ``anthropic``, ``openai`` and ``langchain-core`` are pipeline
dependencies -- and asserts each equals what ``importlib.metadata.version``
independently reports for it. It asserts NON-DEGENERACY first: at least one of
the four must be present, or every "present" assertion below it would hold
vacuously in an environment where nothing is installed.

Section 3 does not rely on that inversion holding forever. Someone installing
ragas here would flip the natural absent case to present, so the absent path is
ALSO driven through a planted ``version_fn`` that raises
``PackageNotFoundError``, which is true whatever is installed.

EVERY CHECK HAS A CONTROL THAT FIRES, AND NOTHING IS EXEC'D. Each assertion is
a named predicate over a stamp or a manifest; ``control()`` runs that same
predicate against a deliberately broken input and requires it to come back
False. A predicate that cannot fail is reported as a failure in its own right.
No source file is patched, in place or in a copy, so this file needs no
``_EXEC_ALLOWLIST`` entry in tests/test_package_invariants.py.

NO NETWORK, NO KEYS, NO SPEND, NO CORPUS, NO GIT HISTORY, NO LIVE QDRANT. It
reads no evaluation run: the ``RunInput`` in section 6 is built from literal
strings. It writes nothing anywhere -- section 5's round-trip goes through
``json.dumps``, never ``write_json``. Not in the collision matrix.

Run from terminal:
    python tests/test_evaluation_ragas_manifest.py

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

import contextlib
import importlib.metadata
import io
import json
import re

from oncotriage.evaluation import ragas_harness as _rh


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


def drive(fn):
    """Call ``fn`` and return its value, or a marker string if it raised.

    Every call into the harness goes through this. A planted defect that makes
    production code RAISE would otherwise escape through ``check()``'s argument
    list while the argument was being evaluated -- the file would die with no
    summary, reporting one traceback where it owed the whole run. This project
    has shipped that shape often enough to name it.
    """
    try:
        return fn()
    except Exception as exc:                # noqa: BLE001 -- the raise is data
        return f"<raised {type(exc).__name__}: {exc}>"


_RAISED_PREFIX = "<raised "


def raised(value):
    """True when ``value`` is a ``drive()`` marker rather than a real result.

    Needed because ``drive`` returns a STRING when the call raised, so
    ``isinstance(x, str)`` alone cannot tell a real message from a swallowed
    traceback -- and a check that accepts the marker passes for the wrong
    reason. Measured, not theorised: the first version of section 8 asserted
    that a missing ragas entry "is its own named failure rather than a
    KeyError" with ``drive(...) is not None``, which the KeyError marker
    satisfies, so the revert that reintroduced the KeyError was reported as
    caught by a neighbouring check while that one passed.
    """
    return isinstance(value, str) and value.startswith(_RAISED_PREFIX)


def contains(value, needle):
    """``needle in value`` that FAILS rather than raising on a non-string.

    A bare ``needle in value`` where ``value`` came back ``None`` from a
    reverted function raises ``TypeError`` while ``check()``'s argument is
    being evaluated -- so the file dies with no summary, reporting one
    traceback where it owed sixty-odd results. This project has shipped that
    shape in four test files; the revert harness for this one caught it here
    before it was committed.
    """
    return isinstance(value, str) and not raised(value) and needle in value


def field(obj, key):
    """``obj[key]`` that returns a marker rather than raising.

    Same argument as ``contains``: ``drive()`` hands back a string when the
    call raised, and ``.get`` on a string is an AttributeError that aborts the
    run.
    """
    if not isinstance(obj, dict):
        return f"<not a dict: {obj!r}>"
    return obj.get(key, f"<no key {key!r}>")


def control(label: str, predicate, broken_input) -> None:
    """Require ``predicate`` to return False on an input it should reject.

    This is how every assertion in this file is shown to be able to fail. The
    predicate is the SAME callable the positive check used, so a control that
    passes proves something about the check that ran rather than about a
    paraphrase of it. A predicate that raises on the broken input counts as
    firing -- it did not accept it -- and the raise is printed, because a
    control that fires for an unintended reason is worth seeing.
    """
    outcome = drive(lambda: predicate(broken_input))
    if isinstance(outcome, str) and outcome.startswith("<raised "):
        print(f"        (control fired by raising: {outcome})")
        outcome = False
    check(f"CONTROL: {label}", bool(outcome), False)


# ===========================================================================
# SECTION 1: THE STAMP'S SHAPE
# ===========================================================================

print("=" * 70)
print("Section 1: environment_stamp() shape")
print("=" * 70)

_STAMP = drive(_rh.environment_stamp)

check("environment_stamp() returned a dict",
      isinstance(_STAMP, dict), True)

# EVERY SECTION BELOW READS _STAMP, so a stamp that came back as a raise marker
# would abort the file at the first subscript and report one traceback where it
# owed sixty-odd results. The failure above is already recorded; this keeps the
# rest of the run alive to record its own.
if not isinstance(_STAMP, dict) or "packages" not in _STAMP:
    print("        (substituting a placeholder stamp so the remaining "
          "sections still run and report)")
    _STAMP = {"python_version": "<unavailable>",
              "python_executable": "<unavailable>",
              "packages": {n: "<unavailable>"
                           for n in _rh.ENVIRONMENT_PACKAGES}}


def _has_keys(stamp):
    return sorted(stamp) == ["packages", "python_executable", "python_version"]


check("...with exactly the three top-level keys",
      drive(lambda: _has_keys(_STAMP)), True)
control("a stamp missing python_executable is rejected", _has_keys,
        {"python_version": "x", "packages": {}})

check("python_version is sys.version verbatim -- the full string, newlines "
      "and all, not a truncated display form",
      field(_STAMP, "python_version"), sys.version)
check("python_executable is sys.executable verbatim",
      field(_STAMP, "python_executable"), sys.executable)


def _covers_all_four(stamp):
    return tuple(stamp["packages"]) == _rh.ENVIRONMENT_PACKAGES


check("packages covers every name in ENVIRONMENT_PACKAGES, in order",
      drive(lambda: _covers_all_four(_STAMP)), True)
control("a stamp missing langchain-core is rejected", _covers_all_four,
        {"packages": {"ragas": "x", "anthropic": "x", "openai": "x"}})

# The four names are asserted here rather than only derived from the constant,
# because the constant is what a regression would edit. Deriving the
# expectation from the thing under test is how a check agrees with the code by
# construction.
check("...and those names are the four the item asked for",
      _rh.ENVIRONMENT_PACKAGES,
      ("ragas", "anthropic", "openai", "langchain-core"))

check("every recorded version is a non-empty string",
      sorted(name for name, value in _STAMP["packages"].items()
             if not isinstance(value, str) or not value),
      [])

for _name, _value in _STAMP["packages"].items():
    print(f"        {_name:<15} {_value}")
print(f"        {'python':<15} {sys.version.splitlines()[0]}")


# ===========================================================================
# SECTION 2: THE PRESENT PATH
# ===========================================================================
# WHAT THIS ACTUALLY EXERCISES, stated because the obvious reading is wrong:
# NOT ragas. Ragas is absent from the project environment by design, so the
# present path is exercised against the pipeline's own dependencies --
# anthropic, openai and langchain-core -- which are installed here and are
# three of the four names the stamp records. The mechanism under test is
# identical for all four: one importlib.metadata lookup per name.

print()
print("=" * 70)
print("Section 2: an installed distribution reports its real version")
print("=" * 70)

_VERSION_RE = re.compile(r"^\d+(\.\d+)*")


def _present(name):
    """True when this environment really has ``name`` installed."""
    try:
        importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


_INSTALLED = [n for n in _rh.ENVIRONMENT_PACKAGES if _present(n)]

# NON-DEGENERACY FIRST. Everything below iterates _INSTALLED, so an empty list
# would satisfy all of it for free -- which is exactly what an environment with
# none of the four installed would produce, and it would read as a pass.
check("at least one of the four distributions is installed here, so the "
      "present-path assertions below are not vacuous (non-degeneracy)",
      len(_INSTALLED) >= 1, True)
print(f"        installed here: {_INSTALLED}")
print(f"        absent here:    "
      f"{[n for n in _rh.ENVIRONMENT_PACKAGES if n not in _INSTALLED]}")

for _name in _INSTALLED:
    _expected = importlib.metadata.version(_name)
    check(f"package_version({_name!r}) equals what importlib.metadata "
          f"independently reports ({_expected})",
          drive(lambda n=_name: _rh.package_version(n)), _expected)
    check(f"...and the stamp carries the same value for {_name!r}",
          _STAMP["packages"][_name], _expected)


def _plausible(value):
    """A version-shaped string: digits, then dot-separated components."""
    return (isinstance(value, str)
            and value != _rh.PACKAGE_ABSENT
            and not value.startswith(_rh.PACKAGE_UNREADABLE_PREFIX)
            and _VERSION_RE.match(value) is not None)


for _name in _INSTALLED:
    check(f"...and it is version-shaped rather than a marker string "
          f"({_name})",
          drive(lambda n=_name: _plausible(_STAMP["packages"][n])), True)

control("the absent marker is not accepted as a plausible version",
        _plausible, _rh.PACKAGE_ABSENT)
control("an unreadable marker is not accepted as a plausible version",
        _plausible, _rh.PACKAGE_UNREADABLE_PREFIX + "OSError: boom")
control("a non-string is not accepted as a plausible version",
        _plausible, 1.5)


# ===========================================================================
# SECTION 3: THE ABSENT PATH
# ===========================================================================
# Driven two ways on purpose. The NATURAL case is what this environment really
# is today and is the case the item is about; the PLANTED case is what keeps
# the check true after somebody installs ragas here, which would otherwise turn
# a real assertion into a skipped one without anything saying so.

print()
print("=" * 70)
print("Section 3: an absent distribution records 'absent' and does not raise")
print("=" * 70)

_MISSING = [n for n in _rh.ENVIRONMENT_PACKAGES if not _present(n)]

if _MISSING:
    print(f"        naturally absent here: {_MISSING}")
    for _name in _MISSING:
        check(f"package_version({_name!r}) returns the absent marker rather "
              f"than raising (natural case)",
              drive(lambda n=_name: _rh.package_version(n)),
              _rh.PACKAGE_ABSENT)
else:
    # Reported, never silently skipped: a section that quietly covers nothing
    # reads exactly like a section that passed.
    print("        SKIP  every one of the four is installed in this "
          "environment, so there is no natural absent case; the planted case "
          "below is what covers this path here.")


def _raise_not_found(name):
    raise importlib.metadata.PackageNotFoundError(name)


check("PLANTED: a lookup that raises PackageNotFoundError records 'absent'",
      drive(lambda: _rh.package_version("ragas", _raise_not_found)),
      _rh.PACKAGE_ABSENT)

check("...and a whole stamp taken through that lookup records every one of "
      "the four as absent, with no raise",
      drive(lambda: _rh.environment_stamp(_raise_not_found).get("packages")),
      {n: _rh.PACKAGE_ABSENT for n in _rh.ENVIRONMENT_PACKAGES})

check("...while python_version and python_executable still describe the real "
      "interpreter -- an absent package says nothing about the interpreter",
      drive(lambda: (_rh.environment_stamp(_raise_not_found)["python_version"],
                     _rh.environment_stamp(_raise_not_found)
                     ["python_executable"])),
      (sys.version, sys.executable))

# The seam must not be able to leak into the default. If passing version_fn
# left it installed anywhere, the next ordinary call would report absent for
# everything and nothing would say so.
check("the seam is per call: the very next default call reports the real "
      "environment again",
      drive(lambda: _rh.environment_stamp()["packages"]),
      _STAMP["packages"])


def _all_absent(stamp):
    return set(stamp["packages"].values()) == {_rh.PACKAGE_ABSENT}


control("a stamp in which something IS installed is not read as all-absent",
        _all_absent, _STAMP)


# ===========================================================================
# SECTION 4: THE UNREADABLE PATH IS NOT THE ABSENT PATH
# ===========================================================================
# This is the assertion that a bare `except Exception: return "absent"` would
# fail, and it is the reason the handler is split. A corrupt dist-info, an
# unreadable site-packages or a broken importlib would otherwise be recorded as
# "not installed" -- a false statement about the environment, in the one field
# that exists to be trusted, with nothing anywhere saying otherwise.

print()
print("=" * 70)
print("Section 4: an unreadable read is recorded as unreadable, not absent")
print("=" * 70)


def _raise_oserror(name):
    raise OSError(f"site-packages unreadable while looking up {name}")


_UNREADABLE = drive(lambda: _rh.package_version("ragas", _raise_oserror))

check("an OSError during the lookup does not raise out of package_version",
      raised(_UNREADABLE), False)
check("...it comes back as a string",
      isinstance(_UNREADABLE, str), True)
check("...it is NOT recorded as absent",
      _UNREADABLE == _rh.PACKAGE_ABSENT, False)
check("...it carries the unreadable prefix",
      contains(_UNREADABLE, _rh.PACKAGE_UNREADABLE_PREFIX), True)
check("...it names the exception type, so the record says what went wrong",
      contains(_UNREADABLE, "OSError"), True)
check("...and the message, so it says what went wrong with what",
      contains(_UNREADABLE, "site-packages unreadable"), True)


def _distinguishes(value):
    """True when the value states unreadable rather than claiming absence."""
    return (value != _rh.PACKAGE_ABSENT
            and value.startswith(_rh.PACKAGE_UNREADABLE_PREFIX))


control("the value a collapsed handler would have returned is rejected",
        _distinguishes, _rh.PACKAGE_ABSENT)


# ===========================================================================
# SECTION 5: THE MANIFEST CARRIES IT
# ===========================================================================

print()
print("=" * 70)
print("Section 5: build_manifest() records the environment block")
print("=" * 70)


class _Args(object):
    """The attribute surface build_manifest reads off the parsed arguments."""

    judge_model = "claude-sonnet-4-6"
    temperature = 0.0
    max_tokens = 4096
    embedding_model = "text-embedding-3-small"
    max_workers = 4
    limit = 0


_SUMMARY = {"total_pairs": 0, "total_scored": 0, "total_unscored": 0}
_PLAN = {"judge_calls_total": 0, "embedding_calls_total": 0}
_ACTIVE = {}
_RUN = _rh.RunInput("/nonexistent/run-dir", {}, [], [], [])


def _manifest(environment=None):
    return _rh.build_manifest(_RUN, _SUMMARY, {}, _Args(), 0.0, "0.4.3",
                              _PLAN, _ACTIVE, None, environment)


_MANIFEST = drive(lambda: _manifest(_STAMP))

check("build_manifest() returned a dict",
      isinstance(_MANIFEST, dict), True)


def _carries_environment(manifest):
    env = manifest.get("environment")
    return (isinstance(env, dict)
            and sorted(env) == ["packages", "python_executable",
                                "python_version"]
            and tuple(env["packages"]) == _rh.ENVIRONMENT_PACKAGES)


check("the manifest carries a well-formed environment block",
      drive(lambda: _carries_environment(_MANIFEST)), True)
control("a manifest with no environment block is rejected",
        _carries_environment, {"ragas_version": "0.4.3"})
control("a manifest whose block omits a package is rejected",
        _carries_environment,
        {"environment": {"python_version": "x", "python_executable": "y",
                         "packages": {"ragas": "0.4.3"}}})

check("...and it is the stamp that was passed in, not a second reading",
      field(_MANIFEST, "environment") is _STAMP, True)

check("the pre-existing ragas_version field is untouched",
      field(_MANIFEST, "ragas_version"), "0.4.3")
check("...and so are the judge and embedding model strings the item names",
      (field(_MANIFEST, "judge_model"), field(_MANIFEST, "judge_provider"),
       field(_MANIFEST, "embeddings_model"),
       field(_MANIFEST, "embeddings_provider")),
      ("claude-sonnet-4-6", "anthropic", "text-embedding-3-small", "openai"))

check("the schema version records that the field set moved",
      field(_MANIFEST, "schema_version"), 2)

# A caller that forgets the argument must write a truthful record, not a null.
_DEFAULTED = drive(lambda: _manifest(None))
check("omitting the environment argument stamps one rather than writing null",
      drive(lambda: _carries_environment(_DEFAULTED)), True)


def _round_trips(manifest):
    """The manifest survives json.dump, which is how it is really written."""
    return json.loads(json.dumps(manifest))["environment"] == \
        manifest["environment"]


check("the manifest round-trips through JSON -- a value that will not "
      "serialise is a run that scores for minutes and cannot write its record",
      drive(lambda: _round_trips(_MANIFEST)), True)
control("a block holding an unserialisable value is rejected", _round_trips,
        {"environment": {"packages": {"ragas": {1, 2}}}})


# ===========================================================================
# SECTION 6: THE DRY RUN PRINTS THE SAME FIELDS
# ===========================================================================
# console.out writes through observability._console_stream(), which returns
# sys.stderr at CALL time -- so redirect_stderr reaches it. The RunInput is
# built from literal strings: no evaluation run is read and nothing is written.

print()
print("=" * 70)
print("Section 6: --dry-run prints the environment block")
print("=" * 70)

_DRY_RUN = _rh.RunInput(
    "/nonexistent/run-dir", {},
    [_rh.RetrievalSample("patient-1", "summary text", ["context text"],
                         "NCT00000001: assessed eligible")],
    [_rh.GenerationSample("patient-1", "NCT00000001", "question text",
                          ["summary text", "context text"],
                          "assessment text", True, "eligible")],
    [])
_DRY_ACTIVE = _rh.active_dataset_metrics(list(_rh.ALL_METRICS))
_DRY_PLAN = drive(lambda: _rh.price_plan(_DRY_RUN, "claude-sonnet-4-6",
                                         "text-embedding-3-small",
                                         _DRY_ACTIVE))

_buffer = io.StringIO()
with contextlib.redirect_stderr(_buffer):
    _printed = drive(lambda: _rh.print_plan(_DRY_PLAN, _DRY_RUN, "/nonexistent",
                                            _DRY_ACTIVE, _STAMP))
_PLAN_TEXT = _buffer.getvalue()

check("print_plan() did not raise", _printed, None)
check("the capture is non-empty, so the assertions below are about real "
      "output (non-degeneracy)",
      len(_PLAN_TEXT) > 200, True)


def _prints_environment(text):
    if "environment:" not in text:
        return False
    if sys.version.splitlines()[0] not in text:
        return False
    if sys.executable not in text:
        return False
    for name in _rh.ENVIRONMENT_PACKAGES:
        if f"{name}" not in text:
            return False
        if _STAMP["packages"][name] not in text:
            return False
    return True


check("the plan prints the heading, the interpreter, the executable and every "
      "package name with its recorded version",
      drive(lambda: _prints_environment(_PLAN_TEXT)), True)
control("a plan that omits the block is rejected", _prints_environment,
        "RAGAS DRY RUN -- NOTHING WAS SUBMITTED\nrun dir: /x\n")
control("a plan naming the packages but not their versions is rejected",
        _prints_environment,
        "environment:\n" + sys.version.splitlines()[0] + "\n" + sys.executable
        + "\n" + "\n".join(_rh.ENVIRONMENT_PACKAGES) + "\n")

# THE SAME RENDERER, not a paraphrase: whatever print_plan emitted for the
# block must be exactly what print_environment emits on its own. A second
# formatter is how a dry run comes to describe a different environment than the
# run records.
_solo = io.StringIO()
with contextlib.redirect_stderr(_solo):
    drive(lambda: _rh.print_environment(_STAMP))
_SOLO_TEXT = _solo.getvalue()

check("the block print_plan emitted is character-identical to what "
      "print_environment emits alone (one renderer, two callers)",
      _SOLO_TEXT and _SOLO_TEXT in _PLAN_TEXT, True)

# The absent marker has to survive into the printed plan, because printing
# "ragas" with an empty value beside it is how an operator misses that the
# environment has no ragas in it at all.
_absent_stamp = drive(lambda: _rh.environment_stamp(_raise_not_found))
if not isinstance(_absent_stamp, dict):
    _absent_stamp = {"python_version": "<unavailable>",
                     "python_executable": "<unavailable>", "packages": {}}
_absent_buffer = io.StringIO()
with contextlib.redirect_stderr(_absent_buffer):
    drive(lambda: _rh.print_environment(_absent_stamp))
check("an absent distribution prints the word 'absent' beside its name rather "
      "than an empty column",
      all(f"{n}" in _absent_buffer.getvalue() for n in
          _rh.ENVIRONMENT_PACKAGES)
      and _absent_buffer.getvalue().count(_rh.PACKAGE_ABSENT)
      >= len(_rh.ENVIRONMENT_PACKAGES),
      True)


# ===========================================================================
# SECTION 7: THE LAZY-IMPORT DISCIPLINE SURVIVED
# ===========================================================================
# The reason --help and --dry-run work in an environment that has no ragas is
# that importing this module pulls in neither ragas nor the Anthropic SDK. A
# version stamp is exactly the kind of change that breaks it: the obvious
# implementation reads ``ragas.__version__``, which imports ragas, and the
# obvious way to report the SDK versions is to import the SDKs. Nothing else in
# the file would notice -- the stamp would still be correct, and the harness
# would simply stop running anywhere ragas is absent.
#
# OPENAI IS EXCLUDED FROM THE ASSERTION, AND THAT IS A MEASUREMENT RATHER THAN
# AN EXEMPTION. ``oncotriage/config.py`` line 81 does a module-scope
# ``from openai import OpenAI``, and ragas_harness imports ``config`` at module
# scope (line 47). Both are at HEAD and both predate the stamp -- checked with
# ``git show HEAD:`` rather than assumed -- so ``openai`` is in ``sys.modules``
# after importing this module and always was. It is also harmless for the
# property that matters: ``openai`` is a pipeline dependency, so it is present
# in every environment this repository runs in, whereas ragas deliberately is
# not. The assertion below is therefore about the two that decide whether
# --dry-run runs at all, and the openai reading is RECORDED so that this
# exclusion is a stated fact rather than a silence. If openai ever stops being
# a module-scope import of config, the recorded value moves and the check that
# pins it fails, which is the correct outcome: this section should be widened
# then, not quietly left narrow.
#
# ``ragas_run.py``'s docstring used to claim importing the harness "loads none
# of them" about all three. Measured false for openai, for the reason above,
# and corrected in the same commit as this section.
#
# This runs in a SUBPROCESS because this process has already imported things.

print()
print("=" * 70)
print("Section 7: stamping imports neither ragas nor the Anthropic SDK")
print("=" * 70)

_PROBE = r"""
import json, sys
WATCHED = ("ragas", "anthropic", "openai")
from oncotriage.evaluation import ragas_harness as rh
after_import = sorted(m for m in WATCHED if m in sys.modules)
stamp = rh.environment_stamp()
after_stamp = sorted(m for m in WATCHED if m in sys.modules)
print("PROBE" + json.dumps({
    "after_import": after_import,
    "after_stamp": after_stamp,
    "packages": stamp["packages"],
    "executable_matches": stamp["python_executable"] == sys.executable,
}))
"""

# The two that must not arrive, and the one that is known to. Kept as separate
# names so the assertions below read as what they are.
_MUST_NOT_IMPORT = ("ragas", "anthropic")
_KNOWN_TRANSITIVE = ["openai"]


def _probe():
    import subprocess
    code_dir = os.path.dirname(os.path.dirname(
        os.path.abspath(oncotriage.__file__)))
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=code_dir, capture_output=True,
        text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    for line in completed.stdout.splitlines():
        if line.startswith("PROBE"):
            return json.loads(line[len("PROBE"):])
    return {"stderr": completed.stderr[-2000:], "stdout": completed.stdout[-500:]}


_PROBED = drive(_probe)

check("the probe subprocess reported (non-degeneracy)",
      isinstance(_PROBED, dict) and "after_stamp" in _PROBED, True)

if isinstance(_PROBED, dict) and "after_stamp" in _PROBED:
    check("importing ragas_harness loads neither ragas nor the Anthropic SDK",
          sorted(m for m in _PROBED["after_import"]
                 if m in _MUST_NOT_IMPORT), [])
    check("...and neither does taking the stamp -- which is the whole risk "
          "here, since the obvious implementation reads ragas.__version__",
          sorted(m for m in _PROBED["after_stamp"]
                 if m in _MUST_NOT_IMPORT), [])
    # PINNED, not exempted. openai arrives transitively through
    # oncotriage.config and always did; recording the exact reading is what
    # makes this section fail rather than silently widen if that changes.
    check("openai IS loaded, transitively through oncotriage.config, exactly "
          "as it was before this stamp existed (the measured exclusion)",
          _PROBED["after_import"], _KNOWN_TRANSITIVE)
    check("...and the stamp adds nothing to that reading",
          _PROBED["after_stamp"], _PROBED["after_import"])
    check("...while the stamp still reports the same versions this process "
          "sees, so it did the work rather than skipping it "
          "(non-degeneracy)",
          _PROBED["packages"], _STAMP["packages"])
    check("...and the executable it recorded is its own interpreter",
          _PROBED["executable_matches"], True)
else:
    check("the probe subprocess produced a PROBE line", _PROBED, "<a PROBE line>")


# ===========================================================================
# SECTION 8: THE TWO READINGS OF THE RAGAS VERSION ARE COMPARED
# ===========================================================================
# The manifest carries the ragas version twice: ``ragas_version``, which is
# ``ragas.__version__`` -- the module that actually scored -- and
# ``environment.packages['ragas']``, which is the DISTRIBUTION on the path,
# the thing a reinstall reproduces. Two fields recording one fact with nothing
# comparing them is how they come to disagree unnoticed, which is the exact
# failure this whole item exists to prevent. This runs with no ragas installed
# and no run scored, because ``ragas_version_disagreement`` is a pure function
# of its two arguments -- which is why it was lifted out of main().

print()
print("=" * 70)
print("Section 8: a shadowed ragas distribution is a named failure")
print("=" * 70)


def _agree(stamped, imported):
    return _rh.ragas_version_disagreement(
        {"packages": {"ragas": stamped}}, imported)


check("agreement returns None -- nothing to report",
      drive(lambda: _agree("0.4.3", "0.4.3")), None)

_SHADOWED = drive(lambda: _agree("0.4.3", "0.5.0"))
check("a disagreement returns a failure string",
      isinstance(_SHADOWED, str) and not raised(_SHADOWED) and bool(_SHADOWED),
      True)
check("...naming BOTH versions, so the operator can see which is which",
      contains(_SHADOWED, "0.4.3") and contains(_SHADOWED, "0.5.0"), True)
check("...and saying what it means -- that reinstalling would not reproduce "
      "these scores",
      contains(_SHADOWED, "would not reproduce"), True)

_MISSING_ENTRY = drive(
    lambda: _rh.ragas_version_disagreement({"packages": {}}, "0.4.3"))
check("a stamp with no ragas entry does NOT raise -- a KeyError here would "
      "land after the money is spent and both files are written, and would "
      "take the failure summary with it",
      raised(_MISSING_ENTRY), False)
check("...it is reported as its own named failure",
      isinstance(_MISSING_ENTRY, str) and not raised(_MISSING_ENTRY)
      and bool(_MISSING_ENTRY), True)
check("...and that message names the constant an editor would have changed",
      contains(_MISSING_ENTRY, "ENVIRONMENT_PACKAGES"), True)

# THE ABSENT MARKER MUST DISAGREE WITH A REAL VERSION. This is the shape a run
# would take if the metadata lookup silently failed while ragas was importable
# -- the reading that says "not installed" beside a module that just scored 126
# samples -- and it must not be read as agreement.
_ABSENT_VS_REAL = drive(lambda: _agree(_rh.PACKAGE_ABSENT, "0.4.3"))
check("'absent' beside a real imported version is a disagreement, not a pass",
      isinstance(_ABSENT_VS_REAL, str) and not raised(_ABSENT_VS_REAL)
      and bool(_ABSENT_VS_REAL), True)


def _reports_disagreement(pair):
    return _rh.ragas_version_disagreement(
        {"packages": {"ragas": pair[0]}}, pair[1]) is not None


control("two identical versions are not reported as a disagreement",
        _reports_disagreement, ("0.4.3", "0.4.3"))


# ===========================================================================
# SECTION 9: --response-field SELECTS THE TEXT, REFUSES, AND CANNOT COLLIDE
# ===========================================================================
# WHY THE OPTION EXISTS. At PROMPT_VERSION 1.5.0 the stored ``assessment``
# became text COMPOSED from the trial's own criterion rows, quoting both of a
# generation sample's contexts verbatim -- so faithfulness over it is near its
# ceiling by construction and measures the RENDERER. ``assessment_draft`` is
# the model's own prose and measures the MODEL. Both readings are wanted, so
# the field is a selection whose three hazards are covered here:
#
#   a. a SILENT FALLBACK to the other field would score a mixture -- some
#      samples the model, some the renderer -- under one metric name;
#   b. ``write_json`` REPLACES, and the default output location is one
#      directory per run, so two passes would have written over each other for
#      dollars apiece with nothing raising;
#   c. the manifest's ``response_field`` is a CLAIM unless something checks the
#      samples were really built from it.
#
# Every run directory here is a literal dict passed to a temp-directory writer,
# so no evaluation run is read. Section 9d writes ONLY inside a
# ``tempfile.mkdtemp()`` tree and removes it; nothing in the repository or
# under 09- Testing is touched, so this file stays out of the collision matrix.

print()
print("=" * 70)
print("Section 9: --response-field -- selection, refusal, and collision")
print("=" * 70)

import shutil                                                    # noqa: E402
import tempfile                                                  # noqa: E402


_DROP = object()
"""Sentinel: remove this key from the verdict entirely, rather than empty it.

An ABSENT key and an EMPTY value are different facts about an artifact and the
loader treats them differently -- absent refuses, empty is a recorded problem.
A test that could only produce one of them would cover half the rule.
"""


def _verdict(nct_id, **overrides):
    """One verdict carrying both fields, unless an override removes one."""
    row = {"nct_id": nct_id, "eligible": "eligible",
           "verdict_group": "matches",
           "assessment": f"{nct_id} COMPOSED: criterion quoted verbatim.",
           "assessment_draft": f"{nct_id} DRAFT: the model's own prose."}
    row.update(overrides)
    for key, value in list(row.items()):
        if value is _DROP:
            del row[key]
    return row


def _write_run(tmp, verdicts, patient_id="patient-1"):
    """A minimal evaluation-run directory: manifest plus one record.

    Creates its own directory, so a caller is one expression rather than a
    makedirs whose return value has to be threaded through a conditional.
    """
    os.makedirs(tmp, exist_ok=True)
    record = {
        "patient_summary": {"text": "A 61-year-old with breast cancer."},
        "contexts": [{"rank": i + 1, "nct_id": v["nct_id"],
                      "trial_text": f"Trial {v['nct_id']} eligibility text."}
                     for i, v in enumerate(verdicts)],
        "verdicts": verdicts,
    }
    with io.open(os.path.join(tmp, f"{patient_id}.json"), "w",
                 encoding="utf-8") as fh:
        json.dump(record, fh)
    with io.open(os.path.join(tmp, "manifest.json"), "w",
                 encoding="utf-8") as fh:
        json.dump({"runs": {patient_id: {"file": f"{patient_id}.json",
                                         "verdicts": len(verdicts)}}}, fh)
    return tmp


_TMP = tempfile.mkdtemp(prefix="ragas_response_field_")
try:
    # --------------------------------------------------------------------
    # 9a. THE FIELD SELECTS THE TEXT, ON BOTH DATASETS
    # --------------------------------------------------------------------
    _BOTH = _write_run(os.path.join(_TMP, "both"),
                       [_verdict("NCT00000001")])

    _AS_DEFAULT = drive(lambda: _rh.load_run(_BOTH))
    _AS_DRAFT = drive(lambda: _rh.load_run(_BOTH, _rh.RESPONSE_FIELD_DRAFT))

    def _generation_response(run):
        return run.generation[0].response

    check("the default scores the composed assessment",
          drive(lambda: _generation_response(_AS_DEFAULT)),
          "NCT00000001 COMPOSED: criterion quoted verbatim.")
    check("--response-field assessment_draft scores the model's prose instead",
          drive(lambda: _generation_response(_AS_DRAFT)),
          "NCT00000001 DRAFT: the model's own prose.")

    # THE RETRIEVAL SIDE MOVES WITH IT. Pinning this side to `assessment` while
    # the manifest stamped `assessment_draft` would put one field name over two
    # different texts -- the mixture this option exists to prevent, one level up.
    check("...and the retrieval-side response follows the same field, so one "
          "recorded response_field is true of the whole run",
          drive(lambda: "DRAFT" in _AS_DRAFT.retrieval[0].response), True)
    check("...while the default retrieval response is still the composed text",
          drive(lambda: "COMPOSED" in _AS_DEFAULT.retrieval[0].response), True)

    def _selects_draft(run):
        return "DRAFT" in run.generation[0].response

    control("a run loaded at the default is not reported as carrying drafts",
            _selects_draft, _AS_DEFAULT)

    check("the RunInput records which field it was built from",
          (drive(lambda: _AS_DEFAULT.response_field),
           drive(lambda: _AS_DRAFT.response_field)),
          ("assessment", "assessment_draft"))

    # --------------------------------------------------------------------
    # 9b. A MISSING FIELD REFUSES. IT NEVER FALLS BACK.
    # --------------------------------------------------------------------
    # This is the whole safety argument: a pre-1.5.0 artifact has no
    # `assessment_draft` key, and a fallback would report the COMPOSED figure
    # -- which is near its ceiling by construction -- under the draft's name,
    # as a finding about the model.
    _PRE_1_5_0 = _write_run(
        os.path.join(_TMP, "pre150"),
        [_verdict("NCT00000001", assessment_draft=_DROP),
         _verdict("NCT00000002", assessment_draft=_DROP)])

    def _refusal_message(run_dir, field=_rh.RESPONSE_FIELD_DRAFT):
        """The refusal's own text, or a marker saying it did not refuse.

        NOT ``drive`` + ``contains``: ``drive`` wraps a raise as
        ``"<raised ...>"`` and ``contains`` REJECTS that marker on purpose, so
        every assertion about a refusal's wording would have come back False
        whatever the message said -- five checks that can only fail. Measured:
        the first version of this block did exactly that and reported four
        failures against a message that carried every string it asked for.
        """
        try:
            _rh.load_run(run_dir, field)
        except _rh.RagasRefusal as exc:
            return str(exc)
        return "<did not refuse>"

    _REFUSAL = drive(lambda: _refusal_message(_PRE_1_5_0))
    check("an artifact with no assessment_draft REFUSES rather than falling "
          "back to the composed assessment",
          _REFUSAL != "<did not refuse>" and not raised(_REFUSAL), True)
    check("...and the refusal names the field that was missing",
          contains(_REFUSAL, "assessment_draft"), True)
    check("...and how many verdicts lacked it, out of how many",
          contains(_REFUSAL, "2 of 2 verdict(s)"), True)
    check("...and names the offending verdicts rather than only counting them",
          contains(_REFUSAL, "patient-1/NCT00000001"), True)
    check("...and states the policy, so the reader is not left to infer that "
          "a fallback would have been wrong",
          contains(_REFUSAL, "never a fallback"), True)

    # NON-DEGENERACY: the helper must be able to report "no refusal", or every
    # assertion above would also hold for a loader that refuses nothing.
    check("CONTROL: the same helper reports a NON-refusal as such",
          drive(lambda: _refusal_message(_BOTH)), "<did not refuse>")

    # CODED, so a caller can branch on it rather than matching prose.
    def _refusal_code(run_dir):
        try:
            _rh.load_run(run_dir, _rh.RESPONSE_FIELD_DRAFT)
        except _rh.RagasRefusal as exc:
            return exc.code
        return None

    check("the refusal carries its own code",
          drive(lambda: _refusal_code(_PRE_1_5_0)), "response_field_missing")

    # THE CONTROL THAT MATTERS: the same artifact, at the default field, loads
    # fine. Without it, "it refused" would also be satisfied by a loader that
    # refuses everything.
    _PRE_AT_DEFAULT = drive(lambda: _rh.load_run(_PRE_1_5_0))
    check("CONTROL: the same artifact loads at the default field -- so the "
          "refusal is about the missing key, not about the artifact",
          drive(lambda: len(_PRE_AT_DEFAULT.generation)), 2)

    # AN EMPTY VALUE IS NOT A MISSING KEY. It is one trial with no text: a
    # recorded problem and no sample, which is what the harness already did.
    # Collapsing the two would let a whole pre-1.5.0 artifact read as "126
    # empty assessments", score nothing, and exit 0.
    _EMPTY = _write_run(
        os.path.join(_TMP, "empty"),
        [_verdict("NCT00000001", assessment_draft="  "),
         _verdict("NCT00000002")]
    )
    _EMPTY_RUN = drive(lambda: _rh.load_run(_EMPTY, _rh.RESPONSE_FIELD_DRAFT))
    check("a field that is PRESENT but empty is a recorded problem, not a "
          "refusal -- an absent key and an empty value are different facts",
          drive(lambda: (raised(_EMPTY_RUN), len(_EMPTY_RUN.generation))),
          (False, 1))
    check("...and the problem names the field that was empty",
          drive(lambda: any("empty assessment_draft" in p
                            for p in _EMPTY_RUN.problems)), True)

    # AN UNKNOWN FIELD BLAMES THE COMMAND LINE, NOT THE ARTIFACT. Without this
    # a typo would read as absent on every verdict and refuse with a message
    # accusing the run directory.
    def _unknown_code(field):
        try:
            _rh.load_run(_BOTH, field)
        except _rh.RagasRefusal as exc:
            return exc.code
        return None

    check("an unknown response field is its own refusal, blaming the argument",
          drive(lambda: _unknown_code("assesment")),  # sic: a typo
          "response_field_unknown")
    control("a real field is not reported as unknown", _unknown_code,
            _rh.RESPONSE_FIELD_DRAFT)

    # --------------------------------------------------------------------
    # 9c. THE CENSUS: THE POPULATION A DRAFT FIGURE IS READ OVER
    # --------------------------------------------------------------------
    # A verdict whose composed assessment is byte-identical to its draft is a
    # kept-text class, and the two figures cannot differ for it. Counted while
    # the record is open so the denominator behind any draft-side claim has one
    # provenance rather than coming from a side script.
    _MIXED = _write_run(
        os.path.join(_TMP, "mixed"),
        [_verdict("NCT00000001"),
         _verdict("NCT00000002", assessment_draft="NCT00000002 COMPOSED: "
                                                  "criterion quoted verbatim.",
                  assessment="NCT00000002 COMPOSED: criterion quoted "
                             "verbatim."),
         _verdict("NCT00000003")]
    )
    _CENSUS = drive(lambda: _rh.load_run(_MIXED).field_census)
    check("the census counts verdicts carrying both fields, split into "
          "byte-identical and differing",
          _CENSUS, {"verdicts_seen": 3, "both_fields_present": 3,
                    "identical": 1, "differing": 2})

    def _census_is_degenerate(census):
        return census.get("identical", 0) + census.get("differing", 0) == 0

    control("a census that counted nothing is not accepted as a measurement",
            _census_is_degenerate, _CENSUS)

    check("...and it reaches the manifest, so the denominator travels with "
          "the figure",
          drive(lambda: _rh.build_manifest(
              _rh.load_run(_MIXED), _SUMMARY, {}, _Args(), 0.0, "0.4.3",
              _PLAN, _ACTIVE, None, _STAMP)["response_field_census"]),
          {"verdicts_seen": 3, "both_fields_present": 3, "identical": 1,
           "differing": 2})

    # --------------------------------------------------------------------
    # 9d. OUTPUTS ARE NAMED AFTER THE FIELD AND CANNOT OVERWRITE EACH OTHER
    # --------------------------------------------------------------------
    _OUT = os.path.join(_TMP, "out")
    _DEFAULT_PATHS = drive(lambda: _rh.output_paths(_OUT))
    _DRAFT_PATHS = drive(lambda: _rh.output_paths(_OUT,
                                                  _rh.RESPONSE_FIELD_DRAFT))

    check("the DEFAULT field keeps the historical filenames, so the two "
          "schema-1 manifests already on disk and superseded_record's lookup "
          "of ragas/ragas_results.json still resolve",
          drive(lambda: [os.path.basename(p) for p in _DEFAULT_PATHS]),
          ["ragas_results.json", "ragas_manifest.json"])
    check("a non-default field takes a suffix naming it",
          drive(lambda: [os.path.basename(p) for p in _DRAFT_PATHS]),
          ["ragas_results__assessment_draft.json",
           "ragas_manifest__assessment_draft.json"])

    def _paths_are_distinct(pair):
        """True when two passes share no output path, so neither can replace
        the other. Phrased positively because ``control()`` requires the SAME
        predicate to come back False on a broken input -- a detector returning
        True on collision would make its own control read as a failure."""
        return not (set(pair[0]) & set(pair[1]))

    check("two passes over one directory therefore share NO output path -- "
          "write_json replaces, so this is what stops one pass destroying the "
          "other's measurement",
          drive(lambda: _paths_are_distinct((_DEFAULT_PATHS, _DRAFT_PATHS))),
          True)
    control("two passes at the SAME field are not reported as distinct -- so "
            "the check above is about the naming rule and not about set() "
            "arithmetic that can never intersect",
            _paths_are_distinct, (_DEFAULT_PATHS, _DEFAULT_PATHS))

    # THE SAME-PASS CASE, which naming cannot solve: re-running a pass replaces
    # a result that cost money and is not reproducible sample to sample.
    os.makedirs(_OUT, exist_ok=True)
    check("nothing on disk means nothing to refuse",
          drive(lambda: _rh.existing_outputs(_OUT)), [])
    with io.open(_DEFAULT_PATHS[0], "w", encoding="utf-8") as _fh:
        _fh.write("{}")
    check("an existing result for THIS field is reported so main() can refuse "
          "before spending",
          drive(lambda: [os.path.basename(p)
                         for p in _rh.existing_outputs(_OUT)]),
          ["ragas_results.json"])
    check("CONTROL: the OTHER field is unaffected by it -- the draft pass is "
          "not blocked by the assessment pass's output",
          drive(lambda: _rh.existing_outputs(_OUT,
                                             _rh.RESPONSE_FIELD_DRAFT)), [])

    # --------------------------------------------------------------------
    # 9e. THE MANIFEST'S response_field IS CHECKED, NOT MERELY CLAIMED
    # --------------------------------------------------------------------
    _DRAFT_MANIFEST = drive(lambda: _rh.build_manifest(
        _AS_DRAFT, _SUMMARY, {}, _Args(), 0.0, "0.4.3", _PLAN, _ACTIVE, None,
        _STAMP))
    check("the manifest records the field the SAMPLES were built from",
          field(_DRAFT_MANIFEST, "response_field"), "assessment_draft")
    check("...and carries the note saying what a figure over it means",
          contains(field(_DRAFT_MANIFEST, "response_field_note"),
                   "measures the MODEL"), True)
    check("...while the default's note says the opposite, so a reader cannot "
          "mistake a renderer figure for a model figure",
          contains(field(drive(lambda: _rh.build_manifest(
              _AS_DEFAULT, _SUMMARY, {}, _Args(), 0.0, "0.4.3", _PLAN,
              _ACTIVE, None, _STAMP)), "response_field_note"),
              "measures the RENDERER"), True)

    # THE POST-CHECK. A manifest field is a claim; this is what makes it a
    # checked fact. A silent fallback would land here as a mixture.
    def _provenance_failures(run):
        return [f for f in _rh.post_checks(
            run, [], _DEFAULT_PATHS[0], _DEFAULT_PATHS[1], _OUT, {})
            if f.startswith("provenance:")]

    check("a run whose samples all match the declared field raises no "
          "provenance failure",
          drive(lambda: _provenance_failures(_AS_DRAFT)), [])

    _MIXTURE = drive(lambda: _rh.RunInput(
        _AS_DRAFT.run_dir, {}, list(_AS_DRAFT.retrieval),
        list(_AS_DRAFT.generation) + list(_AS_DEFAULT.generation), [],
        _rh.RESPONSE_FIELD_DRAFT, {}))
    _MIXED_FAILURES = drive(lambda: _provenance_failures(_MIXTURE))
    check("a MIXTURE of the two fields under one declared name is a named "
          "post-check failure -- this is the shape a silent fallback takes",
          drive(lambda: len(_MIXED_FAILURES)), 1)
    check("...and the failure names both the declared field and the intruder",
          drive(lambda: ("assessment_draft" in _MIXED_FAILURES[0]
                         and "'assessment'" in _MIXED_FAILURES[0])), True)

    # --------------------------------------------------------------------
    # 9f. THE MATCH COUNT IS NOT RECOVERED FROM THE TEXT
    # --------------------------------------------------------------------
    # The dry run used to derive it as len(response.splitlines()), true only
    # while no scored field contains a newline. Measured on the real run: 0 of
    # 126 assessments carry one -- and assessment_draft is free model prose, so
    # making the field selectable made this reachable on real data.
    _NEWLINE = _write_run(
        os.path.join(_TMP, "newline"),
        [_verdict("NCT00000001", assessment_draft="line one\nline two\nthree"),
         _verdict("NCT00000002", eligible="not_eligible",
                  verdict_group="near_misses")]
    )
    _NL_RUN = drive(lambda: _rh.load_run(_NEWLINE, _rh.RESPONSE_FIELD_DRAFT))
    check("exactly one of the two trials was verdicted eligible, so the "
          "match count is 1 however many newlines its text carries",
          drive(lambda: _NL_RUN.retrieval[0].matched_count), 1)

    def _count_from_text(run):
        """The superseded derivation, run on the same sample."""
        return len(run.retrieval[0].response.splitlines())

    check("CONTROL: the superseded splitlines() derivation gets it wrong on "
          "this very sample, which is why the count is carried rather than "
          "recovered",
          drive(lambda: _count_from_text(_NL_RUN)), 3)

finally:
    shutil.rmtree(_TMP, ignore_errors=True)

check("the temporary tree this section wrote in was removed",
      os.path.exists(_TMP), False)


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
Created on Sun Aug 16 2026

@author: ramyalsaffar
"""
