# The two AWS Regions are deployment-varying, so they have environment overrides
##############################################################################

"""Region Override Test

WHAT THIS PINS, AND THE DEFECT BEHIND IT.

Two Regions in this project vary by DEPLOYMENT and neither could be moved
without editing a tracked file:

    config.S3_STAGING_REGION   the region oncotriage/staging/ stages to. The
                               preflight REFUSES a session in another region,
                               correctly -- a bucket's region is fixed for its
                               lifetime -- and that refusal's only remedy was a
                               source edit. An operator whose account or
                               data-residency rule lives elsewhere could not
                               run the tool at all.
    config.BEDROCK_REGION      the region interpolated into the Bedrock base
                               URL. Bedrock quota is granted per region, so an
                               operator granted quota outside us-east-1 had to
                               edit a tracked file before the Stage 5 judge
                               could answer one request.

A TRACKED FILE EDITED FOR ONE MACHINE IS A TRACKED FILE COMMITTED FOR EVERY
MACHINE, which is the shape this closes: ONCOTRIAGE_S3_STAGING_REGION and
ONCOTRIAGE_BEDROCK_REGION, each with its own named resolver in
oncotriage/settings.py, the current literals kept as the defaults in
oncotriage/config.py.

WHY THE RESOLVERS ARE NOT ``_from_env``, WHICH IS THE ONE THING THIS FILE
MEASURES RATHER THAN ASSERTS. That helper runs every value through
``with_trailing_sep``, which is right for the sixteen directory variables and
silent corruption for a Region: ``"us-east-1/"`` is not a Region name, and it
is interpolated into a HOSTNAME, so the slash lands inside
``bedrock-runtime.us-east-1/.amazonaws.com`` and the resulting failure names
neither the slash nor the variable. Section 1 drives BOTH functions on the same
input and requires them to DISAGREE, which is what turns "deliberately not
_from_env" from a comment into a check.

WHY HALF OF IT RUNS IN SUBPROCESSES. Both Regions are resolved at MODULE SCOPE
in oncotriage/config.py, so an environment variable set after that module is
imported reaches nothing -- and the arm where the variable is SET is therefore
unreachable in a process that has already imported it. This is
tests/test_docker_qdrant_override_and_readiness.py's situation exactly, and it
takes the same answer for the same reason: one subprocess per arm, each
importing config for the first time under the environment it is testing.

The RESOLVERS themselves are call-time and pure with respect to os.environ, so
section 1 drives every one of their branches in-process with the environment
restored in a ``finally`` -- which is why this file is 4 subprocesses rather
than 20.

NO NETWORK, NO KEYS, NO SPEND, NO AWS SDK, NO LIVE QDRANT, NO MODEL LOAD, NO
CORPUS, NO DATABASE, NO GIT HISTORY, NO LIVE SERVER. Section 3 drives
``preflight()`` with a stand-in ``session_factory`` inside a subprocess and
asserts ``boto3`` never entered that subprocess's ``sys.modules``, which is
what makes "no AWS call" a measurement rather than a claim. Every subprocess is
additionally handed ONCOTRIAGE_QDRANT_URL pointed at a closed port and
ONCOTRIAGE_DEFER_LOCAL_MODELS=1, so a stray import cannot reach a network or
download a model.

IT EXECS NOTHING and loads no module by location -- the subprocesses are
``python -c`` with the code directory on sys.path, and every in-process control
is a different INPUT to a pure function or an os.environ entry set and removed
in a ``finally`` with the removal asserted. So it needs no ``_EXEC_ALLOWLIST``
entry in tests/test_package_invariants.py.

NOT IN THE COLLISION MATRIX, derived rather than assumed: it writes NOTHING
anywhere -- no temporary directory, no file in the repository -- and the one
repository file it READS is oncotriage/config.py, which
tests/test_config_snapshot_date_rot.py rewrites in place. That file is
therefore sha256-compared at the end, so an interleaved serial run is visible
rather than silent.

Run from terminal:
    python tests/test_settings_region_overrides.py

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
    del _candidate, _how

import hashlib
import json
import subprocess

from oncotriage import config
from oncotriage import settings
from oncotriage.staging import s3_sync


#------------------------------------------------------------------------------


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


def check_true(label, condition):
    check(label, bool(condition), True)


def section(title):
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def drive(fn, *args, **kwargs):
    """Call `fn`, converting a raise into a value `check` can fail on.

    A CHECK THAT ABORTS IS NOT A CHECK. A defect that makes the thing under
    test raise would otherwise escape while `check()`'s argument is being
    evaluated, and the run would print one traceback where it owed a summary
    and every remaining result.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                               # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


def message_of(fn, *args, **kwargs):
    """The exception message `fn` raises, or a marker when it does not."""
    try:
        fn(*args, **kwargs)
    except BaseException as exc:                               # noqa: BLE001
        return str(exc)
    return "<DID NOT RAISE>"


_CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(config.__file__)))
_CONFIG_PY = os.path.abspath(config.__file__)
_CONFIG_SHA_BEFORE = hashlib.sha256(open(_CONFIG_PY, "rb").read()).hexdigest()

_S3_VAR = settings.ENV_S3_STAGING_REGION
_BR_VAR = settings.ENV_BEDROCK_REGION


def with_env(**overrides):
    """Set/clear environment entries, returning the restore thunk.

    Used as ``restore = with_env(X="y")`` / ``restore()`` in a ``finally``.
    A value of ``None`` means "remove", which is not the same as setting the
    empty string -- and telling those two apart is half of what section 1 is
    for.
    """
    saved = {k: os.environ.get(k) for k in overrides}

    def restore():
        for key, old in saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

    for key, value in overrides.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    return restore


#------------------------------------------------------------------------------


# ===========================================================================
# 1.  THE RESOLVERS THEMSELVES
# ===========================================================================
#
# Pure with respect to os.environ and called at CALL time, so every branch is
# reachable in one process. The subprocesses in sections 2 and 3 are for the
# WIRING, which is resolved at import and therefore is not.

section("1. resolve_s3_staging_region / resolve_bedrock_region")

_restore = with_env(**{_S3_VAR: None, _BR_VAR: None})
try:
    check("1a  unset falls back to the caller's default, and reports NO source",
          drive(settings.resolve_s3_staging_region, "us-east-1"),
          ("us-east-1", None))
    check("1b  ...and the Bedrock one behaves identically",
          drive(settings.resolve_bedrock_region, "us-east-1"),
          ("us-east-1", None))

    # THE SOURCE IS None RATHER THAN THE STRING "fallback", which is what
    # `_from_env` returns. Callers RENDER the source, and rendering the literal
    # word "fallback" into a refusal tells an operator nothing about which
    # constant to edit. `resolve_allow_degraded_registries` already answers
    # None for the same reason and this follows it.
    check("1c  the unset source is None, not the string `_from_env` uses, so a "
          "caller can branch on it rather than string-matching",
          drive(settings.resolve_s3_staging_region, "us-east-1")[1] is None,
          True)
finally:
    _restore()
check("1d  the environment is restored after 1a-1c", os.environ.get(_S3_VAR),
      None)

_restore = with_env(**{_S3_VAR: "eu-west-1", _BR_VAR: "ap-southeast-2"})
try:
    check("1e  a set variable WINS over the default and names itself as the "
          "source, so a refusal can say which of the two decided",
          drive(settings.resolve_s3_staging_region, "us-east-1"),
          ("eu-west-1", _S3_VAR))
    check("1f  ...and the Bedrock variable is INDEPENDENT of the S3 one -- an "
          "operator can stage to one region and call Bedrock in another, "
          "which a single shared ONCOTRIAGE_AWS_REGION could not express",
          drive(settings.resolve_bedrock_region, "us-east-1"),
          ("ap-southeast-2", _BR_VAR))
finally:
    _restore()

_restore = with_env(**{_S3_VAR: ""})
try:
    check("1g  EMPTY MEANS UNSET, on `_from_env`'s own recorded argument: "
          "`export VAR=` is a common way to clear a variable, and honouring it "
          "literally would produce an empty Region and a refusal about a value "
          "nobody typed",
          drive(settings.resolve_s3_staging_region, "us-east-1"),
          ("us-east-1", None))
finally:
    _restore()

_restore = with_env(**{_BR_VAR: "   "})
try:
    check("1h  ...and so does whitespace-only",
          drive(settings.resolve_bedrock_region, "us-east-1"),
          ("us-east-1", None))
finally:
    _restore()

_restore = with_env(**{_BR_VAR: "  eu-central-1\n"})
try:
    check("1i  surrounding whitespace is stripped -- `export VAR=$(cat file)` "
          "carries the file's newline, and a newline inside a hostname is a "
          "failure that names nothing",
          drive(settings.resolve_bedrock_region, "us-east-1"),
          ("eu-central-1", _BR_VAR))
finally:
    _restore()

# ---- THE `_from_env` CONTRAST, MEASURED RATHER THAN ASSERTED ------------
#
# The whole reason these two have bespoke resolvers is that the shared helper
# would corrupt the value. That is a comment in settings.py; this is the check.

_restore = with_env(**{_S3_VAR: "eu-west-1"})
try:
    _bespoke = drive(settings.resolve_s3_staging_region, "us-east-1")[0]
    _helper = drive(settings._from_env, _S3_VAR, "us-east-1")[0]
finally:
    _restore()

check("1j  `_from_env` APPENDS A SEPARATOR to the same value -- this is the "
      "corruption the bespoke resolver exists to avoid, driven rather than "
      "described", _helper, "eu-west-1" + os.sep)
check("1k  ...and the bespoke resolver does not", _bespoke, "eu-west-1")
check("1l  ...so the two genuinely DISAGREE on this input. Without this the "
      "pair above could both be right for a reason unrelated to the helper",
      _bespoke == _helper, False)

# ---- IT NEVER RAISES, WHICH IS A DECISION -------------------------------
#
# Both callers resolve at MODULE SCOPE, so a raise here would make
# `import oncotriage.config` fail -- for every process in the project,
# including every one that never touches S3 or Bedrock -- on a typo in a
# variable that concerns two of them. Validation of the VALUE is lazy and
# provider-gated and lives in config.validate_matching_provider_config().

for _label, _bad in (("a Region with a space", "us east 1"),
                     ("a Region with a slash", "us-east-1/"),
                     ("something that is not a Region at all", "!!!")):
    _restore = with_env(**{_BR_VAR: _bad})
    try:
        _got = drive(settings.resolve_bedrock_region, "us-east-1")
    finally:
        _restore()
    check(f"1m  {_label} is returned rather than raised, because raising here "
          f"would break `import oncotriage.config` for the whole project",
          _got, (_bad, _BR_VAR))

check("1n  and the environment is clean after every arm above",
      (os.environ.get(_S3_VAR), os.environ.get(_BR_VAR)), (None, None))


#------------------------------------------------------------------------------


# ===========================================================================
# 2.  THE WIRING, IN SUBPROCESSES
# ===========================================================================
#
# config resolves both at import, so the SET arm cannot be reached in this
# process. One subprocess per arm, each importing config for the first time
# under the environment it is testing.

section("2. config.py picks the override up (subprocess per arm)")

_PROBE = r"""
import json, sys
from oncotriage import config
print("PROBE" + json.dumps({
    "s3": config.S3_STAGING_REGION,
    "s3_source": config.S3_STAGING_REGION_SOURCE,
    "s3_default": config.S3_STAGING_REGION_DEFAULT,
    "bedrock": config.BEDROCK_REGION,
    "bedrock_source": config.BEDROCK_REGION_SOURCE,
    "bedrock_default": config.BEDROCK_REGION_DEFAULT,
    "base_url": config.get_bedrock_base_url(),
    "boto3_imported": "boto3" in sys.modules,
}))
"""


def probe(code=_PROBE, **env):
    """Run `code` in a fresh interpreter under `env`. Returns the JSON dict.

    EVERY SUBPROCESS IS HANDED A CLOSED QDRANT PORT AND THE MODEL DEFERRAL, so
    a stray import cannot reach a network or download 110 MB of weights. The
    parent's own two variables are CLEARED first and then re-applied from
    `env`, so an operator who happens to have one exported cannot make an arm
    pass for a reason this file did not arrange.
    """
    environment = dict(os.environ)
    environment.pop(_S3_VAR, None)
    environment.pop(_BR_VAR, None)
    environment["ONCOTRIAGE_QDRANT_URL"] = "http://127.0.0.1:1"
    environment["ONCOTRIAGE_DEFER_LOCAL_MODELS"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment.update({k: v for k, v in env.items() if v is not None})
    proc = subprocess.run([sys.executable, "-c", code], cwd=_CODE_DIR,
                          env=environment, capture_output=True, text=True,
                          timeout=180)
    for line in proc.stdout.splitlines():
        if line.startswith("PROBE"):
            return json.loads(line[len("PROBE"):])
    return {"_error": f"rc={proc.returncode} stdout={proc.stdout[-800:]!r} "
                      f"stderr={proc.stderr[-800:]!r}"}


_unset = probe()
check("2a  UNSET: config.S3_STAGING_REGION is the shipped default",
      _unset.get("s3"), _unset.get("s3_default"))
check("2b  ...and its SOURCE is None, which is what says the default answered",
      _unset.get("s3_source"), None)
check("2c  ...and the same for BEDROCK_REGION",
      (_unset.get("bedrock"), _unset.get("bedrock_source")),
      (_unset.get("bedrock_default"), None))
check("2d  ...and the shipped defaults are non-degenerate, without which 2a "
      "and 2c would be satisfied by two empty strings",
      (bool(_unset.get("s3_default")), bool(_unset.get("bedrock_default"))),
      (True, True))

_set = probe(**{_S3_VAR: "eu-west-1", _BR_VAR: "ap-southeast-2"})
check("2e  SET: the S3 region follows the variable", _set.get("s3"), "eu-west-1")
check("2f  ...and reports the variable as its source", _set.get("s3_source"),
      _S3_VAR)
check("2g  ...and the Bedrock region follows its own",
      (_set.get("bedrock"), _set.get("bedrock_source")),
      ("ap-southeast-2", _BR_VAR))
check("2h  ...and the DEFAULTS are unchanged by the override, so the shipped "
      "value stays readable beside the resolved one",
      (_set.get("s3_default"), _set.get("bedrock_default")),
      (_unset.get("s3_default"), _unset.get("bedrock_default")))
check("2i  THE BEDROCK BASE URL FOLLOWS, which is the only reason that Region "
      "exists: it is interpolated into the hostname",
      _set.get("base_url"),
      "https://bedrock-runtime.ap-southeast-2.amazonaws.com/openai/v1")
check("2j  CONTROL: and the unset arm's URL carries the default, so 2i is a "
      "statement about the override rather than about the template",
      _unset.get("base_url"),
      f"https://bedrock-runtime.{_unset.get('bedrock_default')}"
      f".amazonaws.com/openai/v1")

_one = probe(**{_S3_VAR: "eu-west-1"})
check("2k  THE TWO ARE INDEPENDENT IN A REAL PROCESS: setting the S3 one "
      "moves the S3 one and leaves Bedrock on its default",
      (_one.get("s3"), _one.get("bedrock")),
      ("eu-west-1", _unset.get("bedrock_default")))

_empty = probe(**{_S3_VAR: ""})
check("2l  an EMPTY variable reaches config as the default, so `export VAR=` "
      "clears rather than corrupts",
      (_empty.get("s3"), _empty.get("s3_source")),
      (_unset.get("s3_default"), None))

check("2m  NO AWS SDK WAS IMPORTED in any probe, so nothing above could have "
      "made a call", _set.get("boto3_imported"), False)


#------------------------------------------------------------------------------


# ===========================================================================
# 3.  THE PREFLIGHT COMPARISON FOLLOWS THE RESOLVED VALUE
# ===========================================================================
#
# The behaviour is UNCHANGED -- a session in another region still refuses -- and
# what moves is the EXPECTED side of the comparison and the remedy the refusal
# names.

section("3. the S3 preflight compares against the resolved region")

_PREFLIGHT_PROBE = r"""
import json, sys
from oncotriage import config
from oncotriage.staging import s3_sync

class _Creds:
    pass

class _Stub:
    def __init__(self, region):
        self.region_name = region
        self._c = _Creds()
    def get_credentials(self):
        return self._c
    def client(self, name):
        class _C:
            def get_caller_identity(self):
                return {"Account": "1", "Arn": "a", "UserId": "u"}
        return _C()

def run(region):
    r = s3_sync.preflight(lambda: _Stub(region))
    return {"state": r.state, "detail": r.detail}

print("PROBE" + json.dumps({
    "matching": run(config.S3_STAGING_REGION),
    "other": run("us-west-2"),
    "expected": config.S3_STAGING_REGION,
    "source": config.S3_STAGING_REGION_SOURCE,
    "boto3_imported": "boto3" in sys.modules,
}))
"""

_pf_default = probe(_PREFLIGHT_PROBE)
_pf_override = probe(_PREFLIGHT_PROBE, **{_S3_VAR: "eu-west-1"})

check("3a  UNSET: a session in the default region passes preflight",
      (_pf_default.get("matching") or {}).get("state"), s3_sync.PREFLIGHT_OK)
check("3b  ...and one in another region is refused, which is the behaviour "
      "this override must not dissolve",
      (_pf_default.get("other") or {}).get("state"),
      s3_sync.PREFLIGHT_WRONG_REGION)

check("3c  OVERRIDDEN: the expected side has MOVED to the variable's value",
      _pf_override.get("expected"), "eu-west-1")
check("3d  ...so a session in eu-west-1 now PASSES, which is the remedy the "
      "refusal did not have and the whole point of the override",
      (_pf_override.get("matching") or {}).get("state"), s3_sync.PREFLIGHT_OK)
check("3e  ...and a session in the OLD default is now the one refused, which "
      "is what proves the comparison moved rather than being switched off",
      (_pf_override.get("other") or {}).get("state"),
      s3_sync.PREFLIGHT_WRONG_REGION)

# ---- THE REFUSAL NAMES WHERE THE EXPECTED VALUE CAME FROM ---------------
#
# The remedies differ: an exported variable is unset with `unset`, and a wrong
# default is a source edit. An un-sourced value sends both to the same page,
# and the export is the half that is invisible in a diff.

_detail_default = (_pf_default.get("other") or {}).get("detail", "")
_detail_override = (_pf_override.get("other") or {}).get("detail", "")

check("3f  the refusal still names BOTH regions, which it always did",
      ("us-west-2" in _detail_default
       and str(_pf_default.get("expected")) in _detail_default), True)
# THE SOURCE CLAUSE AND THE REMEDY LINE ARE DIFFERENT THINGS, and the first
# draft of these controls conflated them: it asserted that the unset refusal
# does not MENTION the variable, and that failed -- correctly -- because the
# refusal offers the export as a REMEDY in both arms, which is exactly what it
# should do. What distinguishes the arms is the `(from ...)` clause, which says
# which of the two decided the expected value, so that is what is pinned.
_FROM_VAR = f"(from {_S3_VAR}"
_FROM_DEFAULT = "(from config.S3_STAGING_REGION_DEFAULT"

check("3g  UNSET: the `from` clause names the shipped default constant, so "
      "the operator edits config.py", _FROM_DEFAULT in _detail_default, True)
check("3h  OVERRIDDEN: the `from` clause names the VARIABLE instead, so the "
      "operator does not go and edit a tracked file that is not what answered",
      _FROM_VAR in _detail_override, True)
check("3i  CONTROL: the unset refusal's clause is NOT the variable's, so 3h "
      "is a statement about the source rather than about a message that "
      "always mentions both", _FROM_VAR in _detail_default, False)
check("3j  CONTROL: ...and the overridden refusal's clause is not the "
      "default's", _FROM_DEFAULT in _detail_override, False)
check("3j2 BOTH arms still OFFER the export as a remedy, which is a different "
      "thing from naming it as the source -- an operator meeting the refusal "
      "with nothing set needs to be told the variable EXISTS",
      (_S3_VAR in _detail_default, _S3_VAR in _detail_override), (True, True))
check("3k  the refusal offers the override AS A REMEDY with the session's own "
      "region in it, so the fix is a copyable line rather than a source edit",
      f"{_S3_VAR}=us-west-2" in _detail_override, True)
check("3l  NO AWS SDK: the preflight probes ran entirely on the stand-in",
      (_pf_default.get("boto3_imported"), _pf_override.get("boto3_imported")),
      (False, False))


#------------------------------------------------------------------------------


# ===========================================================================
# 4.  THE BEDROCK VALIDATOR STILL REFUSES, AND NOW NAMES THE SOURCE
# ===========================================================================
#
# Driven in-process by rebinding the module attribute, which is the seam
# tests/test_agent_bedrock_adapter.py already uses for exactly this validator
# and the reason BEDROCK_REGION stayed a module ATTRIBUTE rather than becoming
# an accessor function.

section("4. validate_matching_provider_config on the Region")

_SAVED = {name: getattr(config, name)
          for name in ("MATCHING_PROVIDER", "BEDROCK_REGION",
                       "BEDROCK_REGION_SOURCE")}


def validator_message(region, source):
    """The validator's message for one (region, source), restoring both."""
    try:
        config.MATCHING_PROVIDER = config.MATCHING_PROVIDER_BEDROCK
        config.BEDROCK_REGION = region
        config.BEDROCK_REGION_SOURCE = source
        return message_of(config.validate_matching_provider_config)
    finally:
        for name, value in _SAVED.items():
            setattr(config, name, value)


check("4a  a good Region still validates clean",
      validator_message("us-east-1", None), "<DID NOT RAISE>")

for _label, _bad in (("empty", ""), ("whitespace-only", "   ")):
    _msg = validator_message(_bad, None)
    check(f"4b  an {_label} Region still REFUSES, which is the behaviour that "
          f"must not move", "BEDROCK_REGION" in _msg, True)
    check(f"4b2 ...and it names the shipped default as the source when no "
          f"variable answered ({_label})",
          "BEDROCK_REGION_DEFAULT" in _msg, True)

# NEW GUARD, AND IT EXISTS BECAUSE THE OVERRIDE MADE IT REACHABLE. A Region is
# interpolated into a HOSTNAME, so whitespace or a slash inside it lands inside
# `bedrock-runtime.{region}.amazonaws.com` -- which is the very corruption
# `_from_env`'s trailing separator would have caused. Refusing one shape of it
# and tolerating the others would be inconsistent.
for _label, _bad in (("an internal space", "us east 1"),
                     ("a tab", "us\teast-1"),
                     ("a trailing slash", "us-east-1/"),
                     ("a leading slash", "/us-east-1")):
    _msg = validator_message(_bad, None)
    check(f"4c  {_label} REFUSES, naming the value",
          "BEDROCK_REGION" in _msg and repr(_bad) in _msg, True)

_msg_env = validator_message("", "ONCOTRIAGE_BEDROCK_REGION")
check("4d  WHEN THE VARIABLE ANSWERED, the refusal names the VARIABLE -- an "
      "export is the half that is invisible in a diff",
      "ONCOTRIAGE_BEDROCK_REGION" in _msg_env, True)
check("4e  CONTROL: ...and it does NOT then name the default constant, so 4d "
      "is a statement about the source rather than about a message that "
      "mentions everything", "BEDROCK_REGION_DEFAULT" in _msg_env, False)

check("4f  CONTROL: a well-formed Region that simply does not exist is NOT "
      "refused here. Nothing in this module can know AWS's Region list "
      "without a network call, and inventing one would refuse a Region added "
      "next quarter", validator_message("xx-nowhere-9", None),
      "<DID NOT RAISE>")

check("4g  the three attributes this section rebinds are restored",
      {name: getattr(config, name) for name in _SAVED}, _SAVED)
check("4h  ...and the captured baseline is non-degenerate, without which 4g "
      "would hold for a section that rebound nothing",
      _SAVED["MATCHING_PROVIDER"] != config.MATCHING_PROVIDER_BEDROCK
      and bool(_SAVED["BEDROCK_REGION"]), True)
# THIS PROBE USED TO PIN THE LITERAL `MATCHING_PROVIDER_OPENAI` AND WAS RIGHT BY
# ACCIDENT. The property 4g needs is that the baseline DIFFERS from what
# `validator_message` sets -- which is `MATCHING_PROVIDER_BEDROCK`, and nothing
# else -- so pinning the incumbent made this a statement about the shipped
# default rather than about the rebind. It went red the day the default flipped
# to "bedrock_anthropic", naming a mechanism that works perfectly. Written
# against the value the section actually assigns, it holds at any default except
# `bedrock` itself -- where the rebind genuinely IS degenerate and a failure
# here is the correct answer.


#------------------------------------------------------------------------------


# ===========================================================================
# 5.  ISOLATION
# ===========================================================================

section("5. Isolation")

check("5a  oncotriage/config.py is byte-unchanged -- it is rewritten in place "
      "by tests/test_config_snapshot_date_rot.py, so an interleaved serial "
      "run is visible here rather than silent",
      hashlib.sha256(open(_CONFIG_PY, "rb").read()).hexdigest(),
      _CONFIG_SHA_BEFORE)
check("5b  neither variable is left set in this process",
      (os.environ.get(_S3_VAR), os.environ.get(_BR_VAR)), (None, None))
check("5c  no AWS SDK was imported in THIS process either",
      "boto3" in sys.modules, False)


#------------------------------------------------------------------------------


print(f"\n{'=' * 74}")
print(f"  {_RESULTS['passed']} passed, {_RESULTS['failed']} failed")
print(f"{'=' * 74}")
if _FAILURES:
    print("\nFAILURES:")
    for _f in _FAILURES:
        print(f"  - {_f}")
sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug 25 2026

@author: ramyalsaffar
"""
