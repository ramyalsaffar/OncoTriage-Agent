# The compose grace period is derived from the request budget, not typed
###########################################################################

"""Compose Shutdown Grace Test

WHAT THIS PINS, AND THE DEFECT BEHIND IT.

`docker-compose.yml` gives the `fastapi` service `stop_grace_period: 620s`, and
620 is not a round number -- it is arithmetic:

    MATCHING_REQUEST_TIMEOUT_SECONDS x (1 + OPENAI_SDK_MAX_RETRIES) + margin
                                 300 x  2                          +  20   = 620

`docker stop` sends SIGTERM, waits the grace period, then SIGKILLs. The batch
runner's SIGTERM handler asks Stage 5 to stop issuing requests -- but a request
ALREADY IN FLIGHT is not interruptible, so the shortest honest wait is one full
request budget INCLUDING the SDK's own retry of it. Set the grace below that and
`docker stop` SIGKILLs a container mid-request: the call is billed, the response
is thrown away, and the run row is left RUNNING with nothing having run a
handler.

NOTHING CHECKED IT. Both terms are config constants that a later pass can move
for reasons of their own -- `MATCHING_REQUEST_TIMEOUT_SECONDS` has been moved
once already, and `OPENAI_SDK_MAX_RETRIES` is argued at length in config.py as a
number that could reasonably change -- and neither of them knows this YAML file
exists. Raising either silently makes 620 too small, and the symptom is a
container that dies mid-request under an ordinary `docker stop`.

THE MARGIN IS A NAMED CONSTANT CARRYING THE UNCALIBRATED LABEL, and it lives
HERE rather than in `oncotriage/config.py` for a reason this project has already
recorded twice: config.py's own rule is that every tunable in it has a reader,
enforced by `tests/test_package_invariants.py` check 2h, and NOTHING AT RUNTIME
READS THIS NUMBER. It is the tolerance of an assertion, so it belongs to the
assertion. See `SHUTDOWN_MARGIN_SECONDS`.

WHAT THIS FILE DELIBERATELY DOES NOT DO.

* IT DOES NOT ASSERT THE VALUE 620. A check written as `== 620` fails the day
  somebody legitimately raises a timeout, and names nothing about why. The
  assertion is the INEQUALITY, so the file passes for any grace period that
  covers the budget and fails for every one that does not.
* IT DOES NOT COVER THE PER-TRIAL ARM, AND SAYS SO. Per-trial mode issues
  `ceil(MAX_TRIALS_FOR_EVALUATION / MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS)`
  rounds, so its worst case is four times this one -- 2400s against a 620s
  grace. That is a KNOWN, DOCUMENTED shortfall recorded in the compose file
  itself, and the repair is a shutdown gate on the API service rather than a
  bigger number. Section 3 asserts the shortfall EXISTS and is documented,
  so that turning the mode on cannot quietly inherit a grace period nobody
  re-derived.
* IT DOES NOT PARSE YAML WITH A YAML LIBRARY. `docker-compose.yml` is read as
  TEXT, on `tests/test_harness_endpoint_budget.py`'s precedent -- and for a
  second reason that file learned the hard way: this compose file ARGUES about
  its own settings at length in comments, and three of the Docker pass's
  assertions were satisfied or defeated by the comments explaining them. Every
  scan below reads COMMENT-STRIPPED lines.

NO NETWORK, NO KEYS, NO SPEND, NO DOCKER DAEMON, NO LIVE SERVER, NO LIVE
QDRANT, NO MODEL LOAD, NO CORPUS, NO DATABASE, NO GIT HISTORY, NO SUBPROCESS.
It starts no container and runs no compose command. IT EXECS NOTHING and it
WRITES NOTHING ANYWHERE -- every plant is an in-memory string. NOT in
`tests/run_serial_tests.py`'s collision matrix, derived rather than asserted: it
writes no file, and of the two repository files it READS, `docker-compose.yml`
is written by neither of the suite's two writers and `oncotriage/config.py` IS
written by `tests/test_config_snapshot_date_rot.py` -- which rewrites only the
`DATA_SNAPSHOT_DATE` literal and restores it byte-identically, touching no
timeout and no retry constant. Both are sha256-compared in section 5 so an
interleaved run is visible rather than silent.

    python tests/test_compose_shutdown_grace.py
"""

import hashlib
import os
import re
import sys

# ABOVE THE PACKAGE IMPORTS ON PURPOSE. oncotriage/agent/deps.py reads this
# variable ONCE, at its own import; an assignment underneath the imports reaches
# nothing. Nothing here needs a local model, and this is the second line of
# defence that says so.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

try:
    import oncotriage                                          # noqa: F401
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

from oncotriage import config


# ===========================================================================
# THE MARGIN
# ===========================================================================

SHUTDOWN_MARGIN_SECONDS = 20
"""Seconds the grace period must allow BEYOND one full request budget.

UNCALIBRATED, AND LABELLED ONE -- on the footing `ECOG_SCORE_DISTRIBUTION`,
`MESH_BOOST_DIRECT_FRACTION` and `MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS` are
labelled in this project. It is what the compose file's own arithmetic uses
("600 for the call plus 20 for the crash record"), and 20 is a guess at how long
the SIGTERM path needs AFTER the last request returns: flush the health record,
finalize the run row, print the crash block, close the tracking run. NONE OF
THAT HAS BEEN TIMED.

RE-DERIVE IT FROM A REAL SIGTERM ON A REAL CAMPAIGN. The measurement is the
wall time between the last `inferences.timestamp` a run wrote and the process
exiting -- `tests/test_runner_sigterm_shutdown.py` already drives that shutdown
with a stand-in and measured 2.85s there, which is a floor and not the answer:
that harness never opens SQLite under load, never flushes a populated
`run_metrics`, and never talks to an MLflow file store.

IT LIVES HERE AND NOT IN `oncotriage/config.py` BECAUSE NOTHING AT RUNTIME
READS IT. config.py's standing rule is that every tunable in it has a reader --
enforced, and two constants were DELETED for failing it -- and a number whose
only consumer is an assertion is that assertion's tolerance rather than a
pipeline tunable. If a shutdown gate is ever added to the API service and needs
a budget of its own, THAT constant belongs in config.py and this one becomes a
check on it.
"""


# ===========================================================================
# HARNESS
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


def check_true(label, condition):
    check(label, bool(condition), True)


def section(title):
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def guarded(fn, *args, **kwargs):
    """Call `fn` and convert a raise into a value `check` can fail on.

    A CHECK THAT ABORTS IS NOT A CHECK. This project has shipped that shape a
    dozen times: a defect makes the thing under test raise, the raise escapes
    while `check()`'s argument is being evaluated, and the run reports one
    traceback where it owed a summary and every remaining result.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:                                   # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


#------------------------------------------------------------------------------


# ===========================================================================
# THE TWO FILES, READ AS TEXT
# ===========================================================================
#
# `config.py`'s path is derived from the MODULE's own `__file__` rather than
# from this test's location, so a future move of either cannot leave this file
# parsing nothing -- and so the file inspected is provably the one this process
# imported rather than a same-named copy.

_CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(config.__file__)))
_CONFIG_PY = os.path.abspath(config.__file__)
_COMPOSE = os.path.join(_CODE_DIR, "docker-compose.yml")

if not os.path.isfile(_COMPOSE):
    # A HARD GUARD, NOT A check(). A wrong root here is not one failure but
    # every failure, each with a misleading message.
    raise SystemExit(f"[Compose] docker-compose.yml not found at {_COMPOSE}")

_COMPOSE_TEXT = open(_COMPOSE, encoding="utf-8").read()
_COMPOSE_SHA_BEFORE = hashlib.sha256(_COMPOSE_TEXT.encode("utf-8")).hexdigest()
_CONFIG_SHA_BEFORE = hashlib.sha256(open(_CONFIG_PY, "rb").read()).hexdigest()


def strip_comments(text):
    """Drop whole-line and trailing `#` comments.

    THE COMPOSE FILE ARGUES ABOUT ITS OWN SETTINGS AT LENGTH, so a scan that
    reads raw lines is reading the argument as well as the setting. Three of the
    Docker pass's assertions were satisfied or defeated by exactly that, and
    this is the correction it shipped, reused.

    A `#` INSIDE A QUOTED VALUE would be mis-stripped, and that limit is stated
    rather than glossed: no line this file reads carries one, and section 1's
    non-degeneracy probe fails if the setting it wants stops being findable.
    """
    out = []
    for line in text.splitlines():
        cut = line.split("#", 1)[0]
        if cut.strip():
            out.append(cut.rstrip())
    return out


_SETTINGS = strip_comments(_COMPOSE_TEXT)


def duration_seconds(value):
    """Parse a compose duration (`620s`, `10m30s`, `2h`) into seconds.

    Compose accepts Go's duration syntax. Only the units that can plausibly
    appear here are handled; anything else returns None so the check FAILS with
    the raw text rather than silently reading as zero.
    """
    value = value.strip()
    if not value:
        return None
    units = {"h": 3600, "m": 60, "s": 1}
    total, seen = 0, False
    for number, unit in re.findall(r"(\d+)\s*([hms])", value):
        total += int(number) * units[unit]
        seen = True
    if not seen:
        return int(value) if value.isdigit() else None
    # Reject trailing junk: the matched spans must cover the whole value.
    if re.fullmatch(r"(\d+\s*[hms])+", value) is None:
        return None
    return total


#------------------------------------------------------------------------------


# ===========================================================================
# 1.  THE GRACE PERIOD COVERS ONE FULL REQUEST BUDGET PLUS THE MARGIN
# ===========================================================================

section("1. stop_grace_period >= one request budget + margin")

_GRACE_LINES = [ln for ln in _SETTINGS if "stop_grace_period" in ln]
check("1a  exactly one service declares a stop_grace_period",
      len(_GRACE_LINES), 1)

_grace_raw = (_GRACE_LINES[0].split(":", 1)[1].strip()
              if _GRACE_LINES else "")
_GRACE = duration_seconds(_grace_raw)
check_true(f"1b  it parses as a duration ({_grace_raw!r})", _GRACE is not None)

# NON-DEGENERACY FOR THE PARSER. Without this, a parser that returned None for
# everything makes 1b the only failing check and 1d unreachable.
check("1c  the duration parser is not degenerate",
      (duration_seconds("620s"), duration_seconds("10m"),
       duration_seconds("1h1s"), duration_seconds("banana")),
      (620, 600, 3601, None))

_BUDGET = (config.MATCHING_REQUEST_TIMEOUT_SECONDS
           * (1 + config.OPENAI_SDK_MAX_RETRIES))
_REQUIRED = _BUDGET + SHUTDOWN_MARGIN_SECONDS

check_true(f"1d  the grace period ({_GRACE}s) covers one request budget "
           f"({_BUDGET}s) plus the margin ({SHUTDOWN_MARGIN_SECONDS}s) "
           f"= {_REQUIRED}s",
           _GRACE is not None and _GRACE >= _REQUIRED)

# NON-DEGENERACY FOR 1d. Both terms must be real: a config whose timeout or
# retry count read as zero would satisfy the inequality for free.
check_true("1e  both terms of the budget are non-degenerate",
           config.MATCHING_REQUEST_TIMEOUT_SECONDS > 0
           and config.OPENAI_SDK_MAX_RETRIES >= 0
           and _BUDGET >= config.MATCHING_REQUEST_TIMEOUT_SECONDS)

# THE INEQUALITY CAN FAIL. Without this, 1d passes for any grace period that
# happens to be large, including one nobody derived.
check("1f  CONTROL: a grace period one second short of the budget FAILS",
      (_REQUIRED - 1) >= _REQUIRED, False)
check("1g  CONTROL: the shipped Docker default of 10s FAILS",
      10 >= _REQUIRED, False)


#------------------------------------------------------------------------------


# ===========================================================================
# 2.  IT IS ON THE SERVICE THAT HOLDS AN IN-FLIGHT BILLED REQUEST
# ===========================================================================
#
# A grace period on the wrong service is worth nothing and looks like a fix.
# `fastapi` is the one that calls the pipeline; `streamlit`, `qdrant` and the
# three Airflow services hold no billed request, and the compose file records
# that a blanket value would slow every restart for nothing.

section("2. It is declared on the fastapi service")

_service = None
_current = None
for _line in _SETTINGS:
    _m = re.match(r"^  ([a-z][a-z0-9_-]*):\s*$", _line)
    if _m:
        _current = _m.group(1)
    if "stop_grace_period" in _line:
        _service = _current

check("2a  the grace period is declared under `fastapi`", _service, "fastapi")

# NON-DEGENERACY: the service scanner must actually find services, or 2a is
# satisfied by a walk that found nothing and left `_service` at None.
_services = [re.match(r"^  ([a-z][a-z0-9_-]*):\s*$", ln).group(1)
             for ln in _SETTINGS
             if re.match(r"^  ([a-z][a-z0-9_-]*):\s*$", ln)]
check_true("2b  the service scanner found the stack's services "
           "(non-degeneracy)",
           {"fastapi", "streamlit", "qdrant"} <= set(_services))


#------------------------------------------------------------------------------


# ===========================================================================
# 3.  THE PER-TRIAL SHORTFALL IS KNOWN, DOCUMENTED, AND STILL TRUE
# ===========================================================================
#
# This grace period does NOT cover per-trial mode, and that is a recorded
# decision rather than an oversight: the repair is a shutdown gate on this
# service, not a bigger number. What must not happen is the mode being turned on
# while this file quietly reports "the grace period is fine".

section("3. The per-trial arm is NOT covered, and the file says so")

_ROUNDS = -(-config.MAX_TRIALS_FOR_EVALUATION
            // config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS)   # ceil
_PER_TRIAL_WORST = _ROUNDS * _BUDGET

check_true(f"3a  per-trial mode's worst case ({_PER_TRIAL_WORST}s over "
           f"{_ROUNDS} rounds) EXCEEDS the grace period ({_GRACE}s) -- so the "
           f"shortfall this file declines to fix is still real",
           _GRACE is not None and _PER_TRIAL_WORST > _GRACE)

check_true("3b  ...and the compose file documents it, so an operator meets the "
           "arithmetic rather than discovering it",
           "rounds per patient" in _COMPOSE_TEXT
           and "SHUTDOWN GATE" in _COMPOSE_TEXT.upper())

# IF THE MODE EVER SHIPS ON, THIS SECTION MUST BE REVISITED RATHER THAN PASSING.
check("3c  the per-trial arm is OFF, which is the premise under which section "
      "1 is sufficient", config.MATCHING_PER_TRIAL_CALLS_ENABLED, False)


#------------------------------------------------------------------------------


# ===========================================================================
# 4.  THE ARITHMETIC IN THE COMMENT AGREES WITH THE CONSTANTS
# ===========================================================================
#
# The compose file spells the derivation out in prose. Prose goes stale, and
# this is the one place a scan of the COMMENTS is the right instrument -- the
# claim being checked is about what the comment SAYS.

section("4. The documented arithmetic still matches the constants")

check_true(f"4a  the compose file names the budget it was derived from "
           f"({_BUDGET}s)", str(_BUDGET) in _COMPOSE_TEXT)
check_true(f"4b  ...and the grace period it produced ({_GRACE}s)",
           str(_GRACE) in _COMPOSE_TEXT)
check_true("4c  ...and names MATCHING_REQUEST_TIMEOUT_SECONDS as the term",
           "MATCHING_REQUEST_TIMEOUT_SECONDS" in _COMPOSE_TEXT)


#------------------------------------------------------------------------------


# ===========================================================================
# 5.  NOTHING WAS WRITTEN
# ===========================================================================

section("5. Isolation")

check("5a  docker-compose.yml is byte-unchanged",
      hashlib.sha256(open(_COMPOSE, "rb").read()).hexdigest(),
      _COMPOSE_SHA_BEFORE)
check("5b  oncotriage/config.py is byte-unchanged",
      hashlib.sha256(open(_CONFIG_PY, "rb").read()).hexdigest(),
      _CONFIG_SHA_BEFORE)


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
