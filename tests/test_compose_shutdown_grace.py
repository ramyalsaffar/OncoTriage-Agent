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
* IT DOES NOT ASSERT THAT ONE REQUEST BUDGET IS ENOUGH ON ITS OWN. It is
  enough only because BOTH Stage 5 callers this grace period can reach have a
  shutdown gate: the batch runner's (since the operator-control pass, both
  arms) and -- since the API shutdown-gate pass -- this service's own. Without
  a gate the drain is a whole PATIENT rather than a whole REQUEST: per-trial
  mode issues `ceil(MAX_TRIALS_FOR_EVALUATION /
  MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS)` rounds and the retained grouped arm
  reaches the same figure by a different route,
  `MATCHING_MAX_INPUT_PACKED_CHUNKS - 1` further sequential chunks -- 2400s
  against a 620s grace, in both arms.

  SO SECTION 3 PINS THE PREMISE RATHER THAN THE SHORTFALL. It still derives
  both UNGATED worst cases from the constants -- they are what the gate
  removed, and the compose file states them -- and it additionally asserts, by
  AST over `oncotriage/api/server.py`, that the gate is armed in the lifespan
  STARTUP and asked for again at shutdown. A grace period whose sufficiency
  rests on a mechanism nothing checks is a number nobody derived, which is the
  failure this whole file exists to prevent.

  SECTION 1 IS SUFFICIENT BECAUSE OF THOSE GATES, NOT BECAUSE OF THE ARM.
  Section 3 used to end by pinning the arm OFF and calling that section 1's
  premise; the premise was false when it was written and the pin was a
  landmine that went red on the very flip it was watching for. See the
  argument at section 3.
* IT DOES NOT PIN THE 2400 PROSE BY SEARCHING THE WHOLE FILE. Section 4 also
  compares each arm's DERIVED worst case against the figure the compose comment
  states for THAT ARM -- a gap the first version of this file left, because
  section 3's inequality still passes while the prose is stale (a larger real
  worst case still exceeds the grace period), so the only symptom of the rot is
  an operator reading a number that is no longer true. The two figures coincide
  today, so each is looked for in its OWN region of the comment; a whole-file
  substring test for one would be satisfied by the other.
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
writes no file, and of the THREE repository files it READS,
`docker-compose.yml` and `oncotriage/api/server.py` are written by neither of
the suite's two writers and `oncotriage/config.py` IS written by
`tests/test_config_snapshot_date_rot.py` -- which rewrites only the
`DATA_SNAPSHOT_DATE` literal and restores it byte-identically, touching no
timeout and no retry constant. All three are sha256-compared in section 5 so an
interleaved run is visible rather than silent.

IT DOES NOT IMPORT THE API MODULE. `oncotriage/api/server.py` is read as TEXT
and `ast`-parsed, on the same footing as the compose file: importing it would
pull FastAPI, slowapi and pydantic into a test whose only other import is
`oncotriage.config`, for a question that is entirely structural.

    python tests/test_compose_shutdown_grace.py
"""

import ast
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

# THE API MODULE, AS TEXT. Section 3 asks a structural question of it -- is the
# gate this grace period's arithmetic depends on actually armed -- and reading
# it rather than importing it keeps FastAPI out of this test's import graph.
_SERVER_PY = os.path.join(_CODE_DIR, "oncotriage", "api", "server.py")
if not os.path.isfile(_SERVER_PY):
    raise SystemExit(f"[Compose] api/server.py not found at {_SERVER_PY}")
_SERVER_TEXT = open(_SERVER_PY, encoding="utf-8").read()
_SERVER_SHA_BEFORE = hashlib.sha256(
    _SERVER_TEXT.encode("utf-8")).hexdigest()


def calls_named(node, name):
    """Does `node` contain a call to `name`, in either reference form?

    Covers the bare name (`request_stage5_shutdown(...)`) and the attribute
    form (`signal.signal(...)`), because a check that knows one of the two
    silently passes over the other -- this project's recorded lesson about
    reference forms, applied to a walk rather than to a trap.
    """
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if isinstance(func, ast.Name) and func.id == name:
            return True
        if isinstance(func, ast.Attribute) and func.attr == name:
            return True
    return False


def lifespan_halves(source):
    """`(startup, shutdown)` -- the statements each side of the lifespan yield.

    Returns `(None, None)` when the function or its yield cannot be found, so a
    rename fails the checks below by name rather than passing over an empty
    walk. The yield is located at ANY depth inside the function body, because
    an `async with` or a `try` around it is an ordinary refactor that must not
    silently empty both halves.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return (None, None)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        if node.name != "lifespan":
            continue
        yields = [n for n in ast.walk(node) if isinstance(n, ast.Yield)]
        if not yields:
            return (None, None)
        cut = max(y.lineno for y in yields)
        before = [n for n in node.body if getattr(n, "end_lineno", 0) <= cut]
        after = [n for n in node.body if getattr(n, "lineno", 0) > cut]
        return (before, after)
    return (None, None)


def any_call(stmts, name):
    """Is `name` called anywhere in `stmts`? False for a missing half."""
    if not stmts:
        return False
    return any(calls_named(st, name) for st in stmts)


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
# 3.  THE GATE IS THE PREMISE, AND THE UNGATED WORST CASES ARE WHAT IT REMOVED
# ===========================================================================
#
# ONE REQUEST BUDGET IS ENOUGH ONLY BECAUSE STAGE 5 STOPS ISSUING REQUESTS.
# Without a gate the drain on this service is a whole PATIENT -- 2400 s in
# either arm, derived below -- and 620 s would be a number that covers a
# fraction of it. This section used to assert that shortfall as a live, unfixed
# fact; the API shutdown-gate pass fixed it, so what is asserted now is the
# MECHANISM the arithmetic depends on, plus the two figures it removed.
#
# WHY THE PREMISE IS PINNED HERE RATHER THAN LEFT TO THE BEHAVIOURAL TEST.
# `tests/test_api_shutdown_gate.py` drives the gate and is where its behaviour
# lives. This file owns the NUMBER, and a number whose sufficiency rests on a
# mechanism nothing checks from here is a number nobody derived -- the retired
# check 3c's mistake, pointed the other way. Two checks, one structural
# question: is the gate armed before the server can be signalled, and is it
# asked for again on the path a signal never reaches.
#
# THIS SECTION USED TO END WITH A LANDMINE AND THE FLIP IS WHY IT DOES NOT.
# The retired check 3c read `MATCHING_PER_TRIAL_CALLS_ENABLED == False` under a
# comment saying "which is the premise under which section 1 is sufficient".
# Two things were wrong with it and only one was the value:
#
#   * IT WAS A TEST THAT FAILS ON THE CHANGE IT EXISTS TO PROTECT. The moment
#     per-trial shipped as the default -- the thing the check was watching for
#     -- it went red, naming a constant rather than a defect. A test that fails
#     on the flip is a landmine, not a tripwire; `tests/test_fixture_call_mode_
#     pin.py` records that exact lesson about its own check 1a.
#   * THE PREMISE WAS FALSE WHEN IT WAS WRITTEN. Section 1's sufficiency does
#     not rest on the arm at all. It rests on the BATCH RUNNER's Stage 5
#     shutdown gate, which bounds the drain to ONE in-flight request in BOTH
#     arms (the operator-control pass gated the grouped send loop too). On the
#     `fastapi` service, which has no gate of any kind, the worst case is
#     2400 s in BOTH arms -- 4 rounds x 600 per-trial, 4 further chunks x 600
#     grouped -- so the shortfall never was a property of the arm.
#
# WHAT REPLACES IT IS ARM-INDEPENDENT AND CANNOT BE DEFEATED BY A FLIP IN
# EITHER DIRECTION: both arms' worst cases are derived from the constants, both
# are required to exceed the grace period, and both derivations are required to
# be present in the compose file. Flip the default back for a comparison run
# and every check below still holds and still means the same thing.

section("3. The gate is the premise; the ungated worst cases are documented")

_ROUNDS = -(-config.MAX_TRIALS_FOR_EVALUATION
            // config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS)   # ceil
_PER_TRIAL_WORST = _ROUNDS * _BUDGET

# GROUPED: the INPUT packer emits at most MATCHING_MAX_INPUT_PACKED_CHUNKS
# sequential requests for one patient. A SIGKILL after the first leaves the rest
# unissued, so the drain is the chunks that FOLLOW the in-flight one.
#
# THIS IS A FLOOR AND IS USED AS ONE. The input packer is not the only thing
# that can add a request to a grouped patient: the OUTPUT pre-splitter splits a
# batch whose output estimate is too large, and the REACTIVE splitter halves a
# chunk whose response came back at `length`, up to MAX_TRUNCATION_SPLITS. A
# floor is the right instrument here because the assertion is an INEQUALITY in
# the direction the floor already settles -- if the smallest honest worst case
# already exceeds the grace period, so does the real one. Deriving the true
# maximum would mean modelling three interacting splitters for a number that
# changes no verdict.
_GROUPED_FURTHER = config.MATCHING_MAX_INPUT_PACKED_CHUNKS - 1
_GROUPED_WORST = _GROUPED_FURTHER * _BUDGET

check_true(f"3a  per-trial mode's UNGATED worst case ({_PER_TRIAL_WORST}s over "
           f"{_ROUNDS} rounds) EXCEEDS the grace period ({_GRACE}s) -- which "
           f"is what the gate removed, and why one is required rather than a "
           f"bigger number",
           _GRACE is not None and _PER_TRIAL_WORST > _GRACE)

check_true(f"3b  the RETAINED GROUPED arm's UNGATED worst case "
           f"({_GROUPED_WORST}s over {_GROUPED_FURTHER} further chunks, a "
           f"FLOOR -- the two output splitters can add more) EXCEEDS it too, "
           f"so the shortfall was a property of this service having no "
           f"shutdown gate, NOT of which arm is configured",
           _GRACE is not None and _GROUPED_WORST > _GRACE)

# NON-DEGENERACY. Both worst cases are products, and a zero in either factor
# would make 3a or 3b fail rather than pass -- but a factor of one would make
# them pass for a reason that is not the one claimed.
check_true("3c  both worst cases are genuinely multi-request (a single-request "
           "worst case would make 3a/3b statements about the budget alone)",
           _ROUNDS > 1 and _GROUPED_FURTHER > 1)

check_true("3d  ...and the compose file documents BOTH derivations, so an "
           "operator meets the arithmetic for whichever arm is configured "
           "rather than discovering it",
           "rounds per patient" in _COMPOSE_TEXT
           and "chunks per patient" in _COMPOSE_TEXT
           and "SHUTDOWN GATE" in _COMPOSE_TEXT.upper())

# THE ONE ARM-DEPENDENT THING WORTH PINNING: the configured arm must be one
# this file has arithmetic for. A third mode added to the vocabulary without a
# worst case derived here would otherwise inherit a grace period nobody
# re-derived -- which is what the retired check was reaching for.
check("3e  the configured arm is one of the two this section derives a worst "
      "case for, so a new mode cannot quietly inherit this grace period",
      (config.matching_call_mode() in config.MATCHING_CALL_MODES,
       len(config.MATCHING_CALL_MODES)), (True, 2))

# CONTROL: 3a and 3b can fail. Without this they pass for any grace period
# large enough, including one that genuinely covered a whole patient.
check("3f  CONTROL: a grace period at the per-trial worst case does NOT "
      "report a shortfall", _PER_TRIAL_WORST > _PER_TRIAL_WORST, False)
check("3g  CONTROL: nor does one at the grouped worst case",
      _GROUPED_WORST > _GROUPED_WORST, False)


# --- THE GATE ITSELF, BY AST OVER oncotriage/api/server.py -----------------
#
# THE GATED WORST CASE IS ONE IN-FLIGHT ROUND, which is `_BUDGET` -- exactly
# what section 1 already requires the grace period to cover. So there is no
# separate inequality to assert here; what there is, is the premise.

_STARTUP, _SHUTDOWN = lifespan_halves(_SERVER_TEXT)

check_true("3h  the API's lifespan was found and has both halves "
           "(non-degeneracy: a rename would otherwise empty both walks and "
           "make every check below pass over nothing)",
           bool(_STARTUP) and bool(_SHUTDOWN))

check("3i  the SIGNAL half of the gate is armed in the lifespan STARTUP, "
      "which is the only point that precedes a request's own drain",
      any_call(_STARTUP, "_install_shutdown_signal_gate"), True)

check("3j  ...and Stage 5 is asked to stop at the lifespan SHUTDOWN too, "
      "which covers every stop that arrives without a signal",
      any_call(_SHUTDOWN, "request_stage5_shutdown"), True)

check("3k  the gate covers SIGTERM, which is what `docker stop` sends, and "
      "SIGINT, which is what a terminal sends",
      guarded(lambda: sorted(
          n.attr for n in ast.walk(ast.parse(_SERVER_TEXT))
          if isinstance(n, ast.Attribute) and n.attr in ("SIGTERM", "SIGINT")
          and isinstance(n.value, ast.Name) and n.value.id == "signal")),
      ["SIGINT", "SIGTERM"])

check("3l  the installer really installs a handler and really asks for the "
      "shutdown from inside it (non-degeneracy: 3i is satisfied by a call to "
      "a function that does nothing)",
      guarded(lambda: [
          (calls_named(fn, "signal") and
           calls_named(fn, "request_stage5_shutdown") and
           calls_named(fn, "_chain_to"))
          for fn in ast.walk(ast.parse(_SERVER_TEXT))
          if isinstance(fn, ast.FunctionDef)
          and fn.name == "_install_shutdown_signal_gate"]),
      [True])

# CONTROLS. Each removes the thing the check above asserts, from an in-memory
# COPY of the source, and requires the same derivation to flip. Nothing is
# written and nothing is exec'd -- the plant is a different STRING handed to
# the same parser, which is the natural control for a walk over a parse tree.
_NO_INSTALL = _SERVER_TEXT.replace("    _install_shutdown_signal_gate()\n",
                                   "    pass\n", 1)
check("3m  CONTROL: the plant that removes the install took (non-degeneracy: "
      "a plant that matched nothing reports a working check as broken)",
      _NO_INSTALL != _SERVER_TEXT, True)
check("3n  CONTROL: with the install removed, 3i FAILS",
      any_call(lifespan_halves(_NO_INSTALL)[0],
               "_install_shutdown_signal_gate"), False)

_NO_REQUEST = _SERVER_TEXT.replace(
    'request_stage5_shutdown("the API lifespan shutdown event")',
    'pass', 1)
check("3o  CONTROL: the plant that removes the shutdown request took",
      _NO_REQUEST != _SERVER_TEXT, True)
check("3p  CONTROL: with it removed, 3j FAILS",
      any_call(lifespan_halves(_NO_REQUEST)[1], "request_stage5_shutdown"),
      False)

check("3q  CONTROL: a source with no `lifespan` at all reports both halves "
      "missing rather than raising",
      lifespan_halves("x = 1"), (None, None))
check("3r  CONTROL: a `lifespan` with no yield does too -- the shape that "
      "would otherwise put every statement in the startup half",
      lifespan_halves("async def lifespan(a):\n    pass\n"), (None, None))
check("3s  CONTROL: unparseable source reports both halves missing rather "
      "than aborting the run",
      lifespan_halves("def ("), (None, None))


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

# THE GAP 4a-4c LEFT, AND IT IS THE HALF THAT ROTS.
#
# 4a-4c pin the numbers the grace period IS derived from. The compose comment
# also spells out the two numbers it is NOT sufficient for -- "worst case = 4 x
# 600 = 2400 s" for per-trial and "4 further requests x 600 = 2400 s" for
# grouped -- and NOTHING PINNED THOSE. Section 3 derives both worst cases from
# the constants and asserts the two ANCHOR PHRASES are present, so it fails if
# the derivations are deleted; it never compares the derived FIGURE against the
# prose, so a change to `MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS` or to
# `MATCHING_MAX_INPUT_PACKED_CHUNKS` moved the real worst case and left 2400
# standing, correct-looking and wrong.
#
# THAT IS THE DANGEROUS DIRECTION. Section 3's inequality still passes when the
# prose is stale -- a larger real worst case still exceeds the grace period --
# so the only symptom of the rot is an operator reading a number that is no
# longer true about the service they are about to `docker stop`.
#
# EACH FIGURE IS LOOKED FOR IN ITS OWN REGION OF THE COMMENT, not in the file
# as a whole, and that is not fastidiousness: the two worst cases are the SAME
# NUMBER today (2400), so a whole-file substring test for the per-trial figure
# is satisfied by the GROUPED sentence and vice versa. Either check would then
# pass while the arm it names had gone stale -- a check satisfied by the wrong
# evidence, which this project treats as worse than no check because it fails
# to fail. The regions are delimited by the two anchor phrases section 3
# already pins, so a rename of either fails there rather than silently emptying
# a region here.

# THE ANCHORS ARE THE TWO SECTION HEADINGS, NOT THE TWO DERIVATION LINES, AND
# THE FIRST DRAFT GOT THAT WRONG IN A WAY ONLY RUNNING IT REVEALED. It split on
# "rounds per patient" and "chunks per patient" -- the phrases section 3
# already pins -- on the assumption that each opens its own arm's paragraph.
# It does not: the grouped paragraph opens with a TABLE whose first row is
# "criteria chars/trial" and whose SECOND row is "chunks per patient", so the
# split point sat two lines INSIDE the grouped derivation and the "per-trial"
# region swallowed most of it. The control caught it on the first run, which is
# the whole reason it is written as a two-directional phrase test rather than
# as a number.
#
# THE HEADINGS ARE WHAT ACTUALLY SEPARATE THE TWO ARMS, and the two derivation
# phrases are then required to be INSIDE the region they belong to -- which
# ties these regions to the anchors section 3 pins, so neither set can be
# renamed without a failure somewhere.

_PT_HEADING = "THE SHIPPED ARM IS PER-TRIAL"
_GR_HEADING = "THE RETAINED GROUPED ARM"
_PT_ANCHOR = "rounds per patient"
_GR_ANCHOR = "chunks per patient"


def _region(text, start_anchor, end_anchor=None):
    """The slice of `text` from `start_anchor` to `end_anchor`.

    RETURNS "" WHEN EITHER ANCHOR IS ABSENT, and the end anchor is the half
    that matters: returning the rest of the file when the end anchor is missing
    would SILENTLY WIDEN the region to everything below it, which is precisely
    the failure this scoping exists to prevent and would show up as a check
    that still passes. An empty region fails instead.
    """
    start = text.find(start_anchor)
    if start < 0:
        return ""
    rest = text[start:]
    if end_anchor is None:
        return rest
    end = rest.find(end_anchor)
    return "" if end < 0 else rest[:end]


_PT_REGION = _region(_COMPOSE_TEXT, _PT_HEADING, _GR_HEADING)
_GR_REGION = _region(_COMPOSE_TEXT, _GR_HEADING)

check_true(f"4d  the PER-TRIAL worst case the constants give ({_PER_TRIAL_WORST}s) "
           f"is the figure the compose file's per-trial derivation states, so "
           f"moving MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS rots that prose "
           f"loudly instead of leaving it correct-looking",
           str(_PER_TRIAL_WORST) in _PT_REGION)

check_true(f"4e  ...and the GROUPED worst case ({_GROUPED_WORST}s) is the "
           f"figure its own derivation states, so "
           f"MATCHING_MAX_INPUT_PACKED_CHUNKS cannot move without the same "
           f"failure. The two figures coincide today, which is exactly why "
           f"each is looked for in its own region",
           str(_GROUPED_WORST) in _GR_REGION)

check("4f  ...and each arm's derivation phrase -- the ones section 3 pins -- "
      "falls inside its OWN region, which is what ties the two anchor sets "
      "together and what the first draft of this split got wrong",
      (_PT_ANCHOR in _PT_REGION, _PT_ANCHOR in _GR_REGION,
       _GR_ANCHOR in _GR_REGION, _GR_ANCHOR in _PT_REGION),
      (True, False, True, False))

# CONTROLS. Without these, 4d and 4e pass for any compose file long enough to
# contain the digits somewhere, and 4f passes for any two disjoint strings.
check("4g  CONTROL: a missing START anchor yields an EMPTY region, so the "
      "check fails rather than searching the whole file",
      _region(_COMPOSE_TEXT, "no such heading in this file"), "")
check("4g2 CONTROL: a missing END anchor does too -- the dangerous case, "
      "because widening to the rest of the file would leave 4d passing on the "
      "grouped sentence",
      _region(_COMPOSE_TEXT, _PT_HEADING, "no such heading in this file"), "")
check("4h  CONTROL: the GROUPED table's own phrase is absent from the "
      "per-trial region, so 4d cannot be reading the grouped derivation",
      ("criteria chars/trial" in _GR_REGION,
       "criteria chars/trial" in _PT_REGION), (True, False))
check("4h2 CONTROL: and the PER-TRIAL derivation's own phrase is absent from "
      "the grouped region, so 4e cannot be reading the per-trial one",
      ("ceil(" in _PT_REGION, "ceil(" in _GR_REGION), (True, False))
check("4h3 CONTROL: a figure in NEITHER derivation is rejected by both, so "
      "4d/4e are not satisfied by any digits at all",
      ("999999" in _PT_REGION, "999999" in _GR_REGION), (False, False))
check_true("4i  CONTROL: both regions are non-empty and each is a proper "
           "SLICE of the file, not the file",
           0 < len(_PT_REGION) < len(_COMPOSE_TEXT)
           and 0 < len(_GR_REGION) < len(_COMPOSE_TEXT))


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
check("5c  oncotriage/api/server.py is byte-unchanged -- every plant above "
      "was an in-memory string",
      hashlib.sha256(open(_SERVER_PY, "rb").read()).hexdigest(),
      _SERVER_SHA_BEFORE)


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
