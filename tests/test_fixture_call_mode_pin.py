# The fixture harness PINS the Stage 5 call mode; it no longer refuses it
##########################################################################

"""Fixture Call-Mode Pin Test

WHAT CHANGED AND WHY IT HAD TO. ``oncotriage/fixtures/capture.py``'s
``RecordingSink.add`` stamps ``call_index = len(bucket)`` under its lock, so a
Stage 5 recording's index is its ARRIVAL ordinal -- deterministic while the
stage is sequential and decided by the thread scheduler the moment it is not.
``build_deterministic_prefix`` projects ``request_sha256_by_call`` and
``finish_reasons`` as LISTS in that order, so the twelve characterization
fixtures characterize the GROUPED arm and can characterize no other until the
sink learns a trial-stable ordering.

Both harnesses used to answer that with a flat REFUSAL
(``UnsupportedCallModeError`` before any hook was installed). That was free
while grouped was the default and becomes a self-inflicted outage the day the
default flips: the free twelve-fixture replay gate -- the one thing in this
project that says the pipeline still does what it did -- would stop running at
exactly the moment a large behaviour change landed. So each entry point now
PINS the mode to grouped for its own process, loudly, and the refusal is what
is left for every OTHER path.

THREE PROPERTIES, AND THE THIRD IS THE ONE MOST EASILY LOST.

  1. THE PIN GOES THROUGH THE ONE OWNER. ``config.matching_call_mode()``
     resolves pin-then-constant; nothing writes
     ``MATCHING_PER_TRIAL_CALLS_ENABLED``. A harness that set the constant on
     the config module would produce the same behaviour and destroy the
     distinction between what the project is CONFIGURED to do and what this
     process was FORCED to do -- so `config.MATCHING_PER_TRIAL_CALLS_ENABLED`
     read anywhere afterwards would be a lie. Sections 1 and 4b measure it
     both ways: behaviourally (the constant is unchanged across a pin) and
     structurally (neither fixture module assigns to it anywhere).
  2. THE GUARD STILL EXISTS AND STILL BITES. Section 2 drives all four
     (pin x constant) combinations. Pinning PER-TRIAL is refused exactly like
     inheriting it, because the guard asks what the node will actually do
     rather than which of the two knobs said so.
  3. THE PIN IS FIRST. Anything that reads the mode before it reads the
     unpinned value -- the guard, Stage 5's partition, and a fixture's own
     environment block. Section 4a requires the call to be the first statement
     of each ``main()`` after ``parse_args``, by AST, with a control that
     moves it down one and must fail.

WHY THE SCRATCH PROCESSES. Section 5 runs the whole gesture with the default
constant forced each way, in subprocesses, for two reasons that are not
convenience: ``oncotriage/fixtures/replay.py`` sets ONCOTRIAGE_DEFER_LOCAL_
MODELS at module scope -- the one deliberate import-time side effect in the
package -- so importing it here would change this process's environment for
every check after it; and a pin is process-global by design, so exercising the
"default is per-trial" arm in-process would leave this file's own later
sections running under a state they did not ask for.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO MODEL LOAD, NO CORPUS, NO
DATABASE, NO GIT HISTORY, NO LIVE SERVER. ONCOTRIAGE_DEFER_LOCAL_MODELS is set
above the imports and section 6c asserts torch and transformers never entered
sys.modules. The subprocesses import ``oncotriage.fixtures.capture`` and
``oncotriage.fixtures.replay``, which open no client at import (section 2 of
tests/test_package_invariants.py is what proves that in general), and they are
additionally handed ONCOTRIAGE_QDRANT_URL pointed at a closed port so a
regression that started opening one fails here rather than reaching a real
endpoint.

IT EXECS NOTHING, so it needs no ``_EXEC_ALLOWLIST`` entry: every control is a
different INPUT to a pure function, an attribute rebind inside try/finally
with the restore asserted BY IDENTITY, an ``ast`` walk over an in-memory copy,
or a subprocess running the SHIPPED modules.

NOT in tests/run_serial_tests.py's collision matrix, derived rather than
assumed: it writes nothing anywhere -- no temp files, no in-place edits -- and
of the three repository files it READS (oncotriage/fixtures/capture.py,
oncotriage/fixtures/replay.py, oncotriage/config.py) the third IS written by
tests/test_config_snapshot_date_rot.py. It is read here ONLY through
``config.__file__`` for an ast walk over the pin's own three functions, whose
text that writer does not touch (it rewrites DATA_SNAPSHOT_DATE), and every
byte read is sha256-compared at the end of section 6b -- so an interleaved
serial run is visible rather than silent.

    python tests/test_fixture_call_mode_pin.py
"""

import ast
import os
import subprocess
import sys
import hashlib

# ABOVE THE PACKAGE IMPORTS ON PURPOSE. oncotriage/agent/deps.py reads this
# variable ONCE, at its own import, and `deps` arrives transitively on the
# first `oncotriage` import -- so an assignment underneath the imports reaches
# nothing and the local models load for real.
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
from oncotriage.fixtures import capture as _capture


# ===========================================================================
# HARNESS
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


def section(title: str) -> None:
    print(f"\n{'-' * 74}\n{title}\n{'-' * 74}")


class _Absent:
    """What a raise-capable read returns instead of raising.

    A bare call into production code inside a ``check(...)`` argument list
    raises while the argument is being EVALUATED, so the file reports one
    traceback where it owes a summary and N recorded failures -- the abort
    shape this project has shipped eleven times. Everything below that can
    raise goes through ``drive`` or ``at``.
    """

    def __init__(self, why: str):
        self.why = why

    def __repr__(self):
        return f"<absent: {self.why}>"

    def __eq__(self, other):
        return isinstance(other, _Absent) and other.why == self.why

    def __bool__(self):
        # FALSY ON PURPOSE, so `x or []` reaches the default rather than
        # handing an unsized object to len(). An absence that evaluated true
        # made three reverts ABORT this file instead of failing it.
        return False


def drive(fn, *args, **kwargs):
    """Call fn; return its value, or the exception it raised."""
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                       # noqa: BLE001
        return exc


def at(mapping, key):
    """mapping[key], or a named absence -- never an IndexError/KeyError."""
    try:
        return mapping[key]
    except (KeyError, IndexError, TypeError) as exc:
        return _Absent(f"{key!r}: {type(exc).__name__}")


def size(value):
    """len(value), or a named absence -- never a TypeError.

    A defect that stops a probe producing a result makes `value` an absence,
    and `len()` on one raises WHILE THE check() ARGUMENT IS BEING EVALUATED --
    the abort shape this project has shipped eleven times, and which three of
    this file's own reverts reproduced before it was closed here.
    """
    try:
        return len(value)
    except TypeError:
        return _Absent(f"len({value!r})")


def joined(value):
    """"\n".join(value), or "" -- never a TypeError on an absence."""
    try:
        return "\n".join(value)
    except TypeError:
        return ""


_CAPTURE_PATH = os.path.abspath(_capture.__file__)
_REPLAY_PATH = os.path.join(os.path.dirname(_CAPTURE_PATH), "replay.py")
_CONFIG_PATH = os.path.abspath(config.__file__)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _sha(path: str) -> str:
    return hashlib.sha256(_read(path).encode("utf-8")).hexdigest()


_HASHES_BEFORE = {p: _sha(p) for p in
                  (_CAPTURE_PATH, _REPLAY_PATH, _CONFIG_PATH)}

_CAPTURE_SRC = _read(_CAPTURE_PATH)
_REPLAY_SRC = _read(_REPLAY_PATH)
_CONFIG_SRC = _read(_CONFIG_PATH)
_CAPTURE_TREE = ast.parse(_CAPTURE_SRC)
_REPLAY_TREE = ast.parse(_REPLAY_SRC)
_CONFIG_TREE = ast.parse(_CONFIG_SRC)


def _body_without_docstring(node):
    """The statements of a definition, minus its docstring.

    A SUBSTRING SCAN OVER ast.unparse READS THE DOCSTRING TOO, and this file
    shipped that defect: `MATCHING_PER_TRIAL_CALLS_ENABLED` appears in
    assert_call_mode_is_hookable's own PROSE arguing why it stopped reading
    that constant, so a text scan reported the argument as the thing it argues
    against. The project's standing rule -- a check that names a symbol must
    look at the code, in every reference form -- applies to the checker as much
    as to the checked.
    """
    body = list(getattr(node, "body", []))
    if body and isinstance(body[0], ast.Expr) and \
            isinstance(body[0].value, ast.Constant) and \
            isinstance(body[0].value.value, str):
        return body[1:]
    return body


def _names_read(node, name):
    """Every LOAD of `name` in executable code: bare, attribute, or keyword.

    Docstrings are dropped first (see _body_without_docstring); a string
    literal that happens to contain the text is invisible to this by
    construction, because it walks nodes rather than characters.
    """
    hits = []
    for stmt in _body_without_docstring(node):
        for sub in ast.walk(stmt):
            if isinstance(sub, ast.Name) and sub.id == name and \
                    isinstance(sub.ctx, ast.Load):
                hits.append(ast.unparse(sub))
            elif isinstance(sub, ast.Attribute) and sub.attr == name:
                hits.append(ast.unparse(sub))
    return hits


def _calls_named(node, name):
    """Every call to `name`, in the bare and the attribute reference form."""
    if node is None:
        return []
    return [n for n in ast.walk(node)
            if isinstance(n, ast.Call)
            and ((isinstance(n.func, ast.Name) and n.func.id == name)
                 or (isinstance(n.func, ast.Attribute) and n.func.attr == name))]


def _defs_named(tree, name):
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == name]


print("=" * 74)
print("FIXTURE CALL-MODE PIN")
print("=" * 74)


# ===========================================================================
# SECTION 1 -- THE OWNER RESOLVES PIN THEN CONSTANT, AND WRITES NEITHER
# ===========================================================================

section("SECTION 1 -- config.matching_call_mode() is the one owner")

_GROUPED = config.MATCHING_CALL_MODE_GROUPED
_PER_TRIAL = config.MATCHING_CALL_MODE_PER_TRIAL

check("1a  the pin is clear in an ordinary process",
      config.matching_call_mode_pin(), None)
# DERIVED FROM THE CONSTANT, NEVER PINNED TO TODAY'S VALUE. The first version
# of this check asserted `MATCHING_PER_TRIAL_CALLS_ENABLED == False`, which
# would have made this file -- whose entire subject is that the fixture gate
# SURVIVES the default flip -- the first thing to fail when the default flips.
# A test that fails on the change it exists to protect is a landmine, not a
# tripwire.
check("1a  ...and the owner then answers from the constant, whichever way it "
      "ships",
      config.matching_call_mode(),
      _PER_TRIAL if config.MATCHING_PER_TRIAL_CALLS_ENABLED else _GROUPED)

# THE FOUR COMBINATIONS, driven rather than argued. The pin must win in BOTH
# directions or it is not an override; a pin that only ever agreed with the
# constant would pass every check below for the wrong reason.
_saved_flag = config.MATCHING_PER_TRIAL_CALLS_ENABLED
_saved_pin = config.matching_call_mode_pin()
_matrix = {}
try:
    for _flag in (False, True):
        config.MATCHING_PER_TRIAL_CALLS_ENABLED = _flag
        config.clear_matching_call_mode_pin()
        _matrix[(_flag, None)] = config.matching_call_mode()
        for _pin in (_GROUPED, _PER_TRIAL):
            config.pin_matching_call_mode(_pin)
            _matrix[(_flag, _pin)] = config.matching_call_mode()
finally:
    config.MATCHING_PER_TRIAL_CALLS_ENABLED = _saved_flag
    config.clear_matching_call_mode_pin()
    if _saved_pin is not None:
        config.pin_matching_call_mode(_saved_pin)

check("1b  unpinned, the owner follows MATCHING_PER_TRIAL_CALLS_ENABLED",
      (at(_matrix, (False, None)), at(_matrix, (True, None))),
      (_GROUPED, _PER_TRIAL))
check("1b  pinned grouped, the owner says grouped whichever way the constant "
      "reads -- which is the whole mechanism",
      (at(_matrix, (False, _GROUPED)), at(_matrix, (True, _GROUPED))),
      (_GROUPED, _GROUPED))
check("1b  pinned per_trial, likewise -- so the pin is an override rather "
      "than a one-way clamp that happens to agree with the default",
      (at(_matrix, (False, _PER_TRIAL)), at(_matrix, (True, _PER_TRIAL))),
      (_PER_TRIAL, _PER_TRIAL))
check("1b  ...and the state was restored",
      (config.MATCHING_PER_TRIAL_CALLS_ENABLED,
       config.matching_call_mode_pin()), (_saved_flag, _saved_pin))

# THE PIN DOES NOT WRITE THE CONSTANT. This is property (1) of the docstring,
# measured behaviourally; section 4b measures it structurally.
_before_const = config.MATCHING_PER_TRIAL_CALLS_ENABLED
try:
    config.pin_matching_call_mode(_PER_TRIAL)
    _const_during = config.MATCHING_PER_TRIAL_CALLS_ENABLED
    _mode_during = config.matching_call_mode()
finally:
    config.clear_matching_call_mode_pin()
check("1c  pinning does not touch MATCHING_PER_TRIAL_CALLS_ENABLED, so the "
      "constant still says what the project is CONFIGURED to do while the "
      "owner says what this process will DO",
      (_const_during, _mode_during), (_before_const, _PER_TRIAL))
check("1c  ...non-degeneracy: the two disagreed, so the check above is a "
      "measurement rather than two readings of one value",
      _const_during == (_mode_during == _PER_TRIAL), False)

check("1d  pin_matching_call_mode returns the PREVIOUS pin, so a caller can "
      "restore it",
      (config.pin_matching_call_mode(_GROUPED),
       config.pin_matching_call_mode(_PER_TRIAL),
       config.clear_matching_call_mode_pin(),
       config.matching_call_mode_pin()),
      (None, _GROUPED, _PER_TRIAL, None))

# AN UNRECOGNISED PIN RAISES RATHER THAN BEING STORED. It is the one value in
# config.py no import-time check can validate, and a typo stored here would
# reach inferences.matching_call_mode, the resume fingerprint and the tracking
# index at once.
_bad = drive(config.pin_matching_call_mode, "PER_TRIAL")
check("1e  an unrecognised mode RAISES", isinstance(_bad, RuntimeError), True)
check("1e  ...naming the closed vocabulary",
      "MATCHING_CALL_MODES" in str(_bad), True)
check("1e  ...as a RuntimeError and NOT a ValueError, so a stray "
      "`except ValueError` around a pipeline call cannot eat it",
      isinstance(_bad, ValueError), False)
check("1e  ...and nothing was stored", config.matching_call_mode_pin(), None)
check("1e  ...non-degeneracy: a RECOGNISED mode does not raise, so 1e is not "
      "a guard that refuses everything",
      (drive(config.pin_matching_call_mode, _GROUPED),
       config.clear_matching_call_mode_pin()), (None, _GROUPED))


# ===========================================================================
# SECTION 2 -- THE GUARD REMAINS, AND READS THE OWNER
# ===========================================================================

section("SECTION 2 -- the refusal still fires on the non-pinned path")

_guard = _capture.assert_call_mode_is_hookable
_outcomes = {}
try:
    for _flag in (False, True):
        config.MATCHING_PER_TRIAL_CALLS_ENABLED = _flag
        config.clear_matching_call_mode_pin()
        _outcomes[(_flag, None)] = drive(_guard, "probe")
        for _pin in (_GROUPED, _PER_TRIAL):
            config.pin_matching_call_mode(_pin)
            _outcomes[(_flag, _pin)] = drive(_guard, "probe")
finally:
    config.MATCHING_PER_TRIAL_CALLS_ENABLED = _saved_flag
    config.clear_matching_call_mode_pin()


def _refused(outcome) -> bool:
    return isinstance(outcome, _capture.UnsupportedCallModeError)


check("2a  a per-trial DEFAULT with no pin is refused -- the path that would "
      "record arrival-ordered recordings nondeterministically",
      _refused(at(_outcomes, (True, None))), True)
check("2b  ...and the pin is what lets the harness through it",
      at(_outcomes, (True, _GROUPED)), None)
check("2c  pinning PER-TRIAL is refused too, from either default: the guard "
      "asks what the node will DO, so the pin is not a way around it",
      (_refused(at(_outcomes, (False, _PER_TRIAL))),
       _refused(at(_outcomes, (True, _PER_TRIAL)))), (True, True))
check("2d  ...and grouped, pinned or not, passes -- so section 2 is a "
      "measurement rather than a guard that raises unconditionally",
      (at(_outcomes, (False, None)), at(_outcomes, (False, _GROUPED))),
      (None, None))

_msg = str(at(_outcomes, (True, None)))
check("2e  the refusal names the owner it read",
      "matching_call_mode()" in _msg, True)
check("2e  ...and BOTH inputs to it, so a reader can tell an inherited "
      "default from a deliberate pin without opening config.py",
      ("MATCHING_PER_TRIAL_CALLS_ENABLED" in _msg, "pin=" in _msg),
      (True, True))
check("2e  ...and the remedy, by name",
      "pin_call_mode_for_fixture_process" in _msg, True)
check("2f  it is a RuntimeError subclass, deliberately not a ValueError",
      (issubclass(_capture.UnsupportedCallModeError, RuntimeError),
       issubclass(_capture.UnsupportedCallModeError, ValueError)),
      (True, False))

# THE GUARD READS THE OWNER, ASSERTED STRUCTURALLY AS WELL. Behaviour alone
# cannot distinguish "reads matching_call_mode()" from "reads the constant and
# a second copy of the pin rule", and the second would have to be kept in step
# with config.py by hand.
_guard_fn = _defs_named(_CAPTURE_TREE, "assert_call_mode_is_hookable")
check("2g  assert_call_mode_is_hookable exists exactly once", len(_guard_fn), 1)
_GUARD_DOC = ast.get_docstring(_guard_fn[0]) if _guard_fn else None
# NAMED BY ITS CLASS, not "the first __init__ in the file" -- capture.py has
# several, and a positional selector would have made the probe below a
# statement about whichever class happened to sort first.
_ERR_CLASS = next((n for n in ast.walk(_CAPTURE_TREE)
                   if isinstance(n, ast.ClassDef)
                   and n.name == "UnsupportedCallModeError"), None)
_guard_body = _guard_fn[0] if _guard_fn else None
check("2g  ...and calls the owner",
      len(_calls_named(_guard_body, "matching_call_mode"))
      if _guard_body is not None else _Absent("no guard"), 1)
check("2g  ...and its EXECUTABLE code never reads "
      "MATCHING_PER_TRIAL_CALLS_ENABLED, which would be a second copy of the "
      "pin rule to keep in step with config.py by hand",
      _names_read(_guard_body, "MATCHING_PER_TRIAL_CALLS_ENABLED")
      if _guard_body is not None else _Absent("no guard"), [])
check("2g  ...non-degeneracy: the walk DOES see the constant in the "
      "UnsupportedCallModeError message that reports it, so an empty result "
      "above is a finding rather than a walk that matched nothing -- and a "
      "docstring ARGUING about that constant is invisible to it, which is the "
      "defect a substring scan of this same function shipped",
      (len(_names_read(_ERR_CLASS, "MATCHING_PER_TRIAL_CALLS_ENABLED")) >= 1
       if _ERR_CLASS is not None else _Absent("no UnsupportedCallModeError"),
       "MATCHING_PER_TRIAL_CALLS_ENABLED" in (_GUARD_DOC or "")),
      (True, True))


# ===========================================================================
# SECTION 3 -- THE HARNESS GESTURE: PINNED, AND LOUD
# ===========================================================================

section("SECTION 3 -- pin_call_mode_for_fixture_process")


def _pin_run(flag):
    """Run the shipped gesture with the default constant forced, capturing out.

    Restores both the constant and the pin, whatever happens.
    """
    lines = []
    saved = config.MATCHING_PER_TRIAL_CALLS_ENABLED
    try:
        config.MATCHING_PER_TRIAL_CALLS_ENABLED = flag
        config.clear_matching_call_mode_pin()
        returned = drive(_capture.pin_call_mode_for_fixture_process,
                         "probe", out=lines.append)
        return returned, lines, config.matching_call_mode(), \
            config.matching_call_mode_pin(), \
            config.MATCHING_PER_TRIAL_CALLS_ENABLED
    finally:
        config.MATCHING_PER_TRIAL_CALLS_ENABLED = saved
        config.clear_matching_call_mode_pin()


_off = _pin_run(False)
_on = _pin_run(True)

check("3a  it returns the grouped mode under BOTH defaults",
      (at(_off, 0), at(_on, 0)), (_GROUPED, _GROUPED))
check("3a  ...and the owner agrees under both, which is the property the "
      "twelve fixtures depend on",
      (at(_off, 2), at(_on, 2)), (_GROUPED, _GROUPED))
check("3a  ...through the pin, not by writing the constant",
      (at(_off, 3), at(_on, 3), at(_off, 4), at(_on, 4)),
      (_GROUPED, _GROUPED, False, True))

_off_text = joined(at(_off, 1))
_on_text = joined(at(_on, 1))
check("3b  the loud line prints under BOTH defaults, including the one where "
      "it overrode nothing -- a notice that appeared only when it had "
      "something to override would be absent from every log taken before the "
      "flip and present after it",
      (size(at(_off, 1)), size(at(_on, 1))), (3, 3))
check("3b  ...it says the mode was PINNED, and to what",
      (f"PINNED to {_GROUPED!r}" in _off_text,
       f"PINNED to {_GROUPED!r}" in _on_text), (True, True))
check("3b  ...and names the caller",
      ("probe" in _off_text, "probe" in _on_text), (True, True))
check("3c  it reports what the process WOULD have run, which is the only "
      "thing in either log that says which arm the project was configured "
      "for at capture time",
      (f"would have run {_GROUPED!r}" in _off_text,
       f"would have run {_PER_TRIAL!r}" in _on_text), (True, True))
check("3c  ...and the constant it read",
      ("MATCHING_PER_TRIAL_CALLS_ENABLED=False" in _off_text,
       "MATCHING_PER_TRIAL_CALLS_ENABLED=True" in _on_text), (True, True))
check("3d  ...and states that the fixtures characterize the GROUPED arm",
      ("GROUPED" in _off_text and "GROUPED" in _on_text), True)
check("3d  ...and that per-trial fixtures are a PENDING MIGRATION ITEM",
      ("PENDING MIGRATION ITEM" in _off_text
       and "PENDING MIGRATION ITEM" in _on_text), True)
check("3d  ...one wording, not two: both entry points print the same "
      "constant, so a reader comparing a capture log with a replay log does "
      "not have to decide whether two sentences mean the same thing",
      (_capture.FIXTURE_CALL_MODE_NOTICE in _off_text,
       _capture.FIXTURE_CALL_MODE_NOTICE in _on_text), (True, True))
check("3e  the two runs differ ONLY in what they say the default was, so 3a's "
      "'identical under both defaults' is a measurement",
      _off_text.replace("'grouped' (MATCHING_PER_TRIAL_CALLS_ENABLED=False",
                        "X") ==
      _on_text.replace("'per_trial' (MATCHING_PER_TRIAL_CALLS_ENABLED=True",
                       "X"), True)

# THE DEFENSIVE BRANCH, DRIVEN. A pin that did not take is worse than no pin:
# everything downstream would believe the arm is grouped while the node
# partitioned per trial. The control is an attribute rebind inside
# try/finally, restore asserted BY IDENTITY -- nothing is exec'd and no file
# is written.
_real_pin = config.pin_matching_call_mode
_saved_flag2 = config.MATCHING_PER_TRIAL_CALLS_ENABLED
try:
    config.MATCHING_PER_TRIAL_CALLS_ENABLED = True
    config.pin_matching_call_mode = lambda mode: None      # a pin that no-ops
    _no_take = drive(_capture.pin_call_mode_for_fixture_process,
                     "probe", out=lambda _line: None)
finally:
    config.pin_matching_call_mode = _real_pin
    config.MATCHING_PER_TRIAL_CALLS_ENABLED = _saved_flag2
    config.clear_matching_call_mode_pin()

check("3f  a pin that does not take is REFUSED rather than trusted",
      isinstance(_no_take, _capture.UnsupportedCallModeError), True)
check("3f  ...saying so", "did not take" in str(_no_take), True)
check("3f  ...and the rebind was restored, by identity",
      config.pin_matching_call_mode is _real_pin, True)
check("3f  ...non-degeneracy: with the real owner back, the same call "
      "succeeds -- so 3f caught the no-op rather than refusing always",
      at(_pin_run(True), 0), _GROUPED)


# ===========================================================================
# SECTION 4 -- BOTH ENTRY POINTS PIN, FIRST, AND NEITHER WRITES THE CONSTANT
# ===========================================================================

section("SECTION 4 -- where the pin is installed")

_PIN_NAME = "pin_call_mode_for_fixture_process"


def _main_of(tree):
    # A MISSING TREE IS A VALUE, NOT A CRASH. _moved_down() returns None when
    # it cannot find the pin to move -- which is exactly what a revert that
    # DELETES the pin produces -- and ast.walk(None) raises there, turning the
    # control into an abort.
    if tree is None:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    return None


def _first_statement_after_parse_args(main_node):
    """The statement index of the pin relative to `args = parser.parse_args()`.

    Returns (index_of_parse_args, index_of_pin) over main's top-level body, or
    a named absence for either that is missing. Compared rather than asserted
    equal to a literal, because a future edit that adds an argument changes
    neither relationship.
    """
    if main_node is None:
        return _Absent("no main"), _Absent("no main")
    parse_at = pin_at = None
    for index, stmt in enumerate(main_node.body):
        text = ast.unparse(stmt)
        if parse_at is None and "parse_args()" in text:
            parse_at = index
        if pin_at is None and _PIN_NAME in text:
            pin_at = index
    return (parse_at if parse_at is not None else _Absent("no parse_args"),
            pin_at if pin_at is not None else _Absent("no pin"))


_cap_main = _main_of(_CAPTURE_TREE)
_rep_main = _main_of(_REPLAY_TREE)

check("4a  capture's main() calls the pin exactly once",
      size(_calls_named(_cap_main, _PIN_NAME)), 1)
check("4a  ...and so does replay's",
      size(_calls_named(_rep_main, _PIN_NAME)), 1)

_cap_pos = _first_statement_after_parse_args(_cap_main)
_rep_pos = _first_statement_after_parse_args(_rep_main)
check("4a  ...as the FIRST statement after parse_args in capture's main(): "
      "anything above it reads the UNPINNED mode",
      (at(_cap_pos, 1), at(_cap_pos, 0) + 1
       if isinstance(at(_cap_pos, 0), int) else _Absent("no parse_args")),
      (at(_cap_pos, 0) + 1 if isinstance(at(_cap_pos, 0), int)
       else _Absent("no parse_args"),
       at(_cap_pos, 0) + 1 if isinstance(at(_cap_pos, 0), int)
       else _Absent("no parse_args")))
check("4a  ...and in replay's",
      (at(_rep_pos, 1), at(_rep_pos, 0) + 1
       if isinstance(at(_rep_pos, 0), int) else _Absent("no parse_args")),
      (at(_rep_pos, 0) + 1 if isinstance(at(_rep_pos, 0), int)
       else _Absent("no parse_args"),
       at(_rep_pos, 0) + 1 if isinstance(at(_rep_pos, 0), int)
       else _Absent("no parse_args")))

# THE CONTROL FOR 4a. Move the pin down one statement in an in-memory copy of
# each module and require the same walk to report it. Without this, "the pin is
# adjacent to parse_args" would pass for a checker that had stopped looking.
def _moved_down(tree):
    import copy as _copy
    clone = _copy.deepcopy(tree)
    main_node = _main_of(clone)
    if main_node is None:
        return None
    body = main_node.body
    for index, stmt in enumerate(body):
        if _PIN_NAME in ast.unparse(stmt) and index + 1 < len(body):
            body[index], body[index + 1] = body[index + 1], body[index]
            return clone
    return None


_cap_moved = _first_statement_after_parse_args(_main_of(_moved_down(_CAPTURE_TREE)))
_rep_moved = _first_statement_after_parse_args(_main_of(_moved_down(_REPLAY_TREE)))
check("4a  control: moved down one statement, capture's pin is no longer "
      "first and the walk says so",
      at(_cap_moved, 1) == (at(_cap_moved, 0) + 1
                            if isinstance(at(_cap_moved, 0), int) else None),
      False)
check("4a  control: ...and replay's",
      at(_rep_moved, 1) == (at(_rep_moved, 0) + 1
                            if isinstance(at(_rep_moved, 0), int) else None),
      False)

# NEITHER MODULE WRITES THE CONSTANT. Property (1), structurally: an assignment
# to config.MATCHING_PER_TRIAL_CALLS_ENABLED anywhere in either file would
# produce the same behaviour and destroy the distinction the pin exists for.
def _writes_constant(tree):
    hits = []
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Attribute) and \
                    target.attr == "MATCHING_PER_TRIAL_CALLS_ENABLED":
                hits.append(ast.unparse(node))
            if isinstance(target, ast.Name) and \
                    target.id == "MATCHING_PER_TRIAL_CALLS_ENABLED":
                hits.append(ast.unparse(node))
    return hits


check("4b  capture.py never assigns MATCHING_PER_TRIAL_CALLS_ENABLED",
      _writes_constant(_CAPTURE_TREE), [])
check("4b  ...and neither does replay.py", _writes_constant(_REPLAY_TREE), [])
check("4b  ...non-degeneracy: the same walk DOES find config.py's own "
      "declaration, so an empty result is a finding rather than a walk that "
      "matched nothing",
      len(_writes_constant(_CONFIG_TREE)), 1)

# BOTH GUARDS ARE STILL WIRED IN. The pin makes the guard PASS on this path;
# it does not replace it, and deleting the guard would leave every other
# caller unprotected.
def _guarded_functions(tree, attr):
    return sorted(fn.name for fn in ast.walk(tree)
                  if isinstance(fn, ast.FunctionDef)
                  and _calls_named(fn, attr))


check("4c  install_recording_hooks still calls the call-mode guard",
      _guarded_functions(_CAPTURE_TREE, "assert_call_mode_is_hookable"),
      ["install_recording_hooks"])
check("4c  ...and install_replay_hooks still does",
      _guarded_functions(_REPLAY_TREE, "assert_call_mode_is_hookable"),
      ["install_replay_hooks"])
check("4c  ...non-degeneracy: the same walk finds the PROVIDER guard in both",
      (_guarded_functions(_CAPTURE_TREE, "assert_provider_is_hookable"),
       _guarded_functions(_REPLAY_TREE, "assert_provider_is_hookable")),
      (["install_recording_hooks"], ["install_replay_hooks"]))


# ===========================================================================
# SECTION 5 -- THE WHOLE GESTURE, IN SCRATCH PROCESSES, BOTH DEFAULTS
# ===========================================================================

section("SECTION 5 -- scratch processes, the default forced each way")

_CODE_DIR = os.path.dirname(os.path.dirname(_CAPTURE_PATH))

_PROBE = r'''
import os, sys, json
sys.path.insert(0, {code_dir!r})
os.environ["ONCOTRIAGE_DEFER_LOCAL_MODELS"] = "1"
from oncotriage import config
config.MATCHING_PER_TRIAL_CALLS_ENABLED = {flag}
MODULE = {module!r}
if MODULE == "replay":
    from oncotriage.fixtures import replay as _m
    from oncotriage.fixtures import capture as _capture
else:
    from oncotriage.fixtures import capture as _capture
    _m = _capture
lines = []
before_mode = config.matching_call_mode()
before_pin = config.matching_call_mode_pin()
try:
    _capture.assert_call_mode_is_hookable("before")
    before_guard = "passed"
except _capture.UnsupportedCallModeError as exc:
    before_guard = "refused"
returned = _capture.pin_call_mode_for_fixture_process(MODULE, out=lines.append)
try:
    _capture.assert_call_mode_is_hookable("after")
    after_guard = "passed"
except _capture.UnsupportedCallModeError:
    after_guard = "refused"
print("__RESULT__" + json.dumps({{
    "before_mode": before_mode,
    "before_pin": before_pin,
    "before_guard": before_guard,
    "returned": returned,
    "after_mode": config.matching_call_mode(),
    "after_pin": config.matching_call_mode_pin(),
    "after_guard": after_guard,
    "constant": config.MATCHING_PER_TRIAL_CALLS_ENABLED,
    "lines": lines,
    "torch": "torch" in sys.modules,
    "transformers": "transformers" in sys.modules,
}}))
'''


def _run_probe(module: str, flag: bool):
    import json
    env = dict(os.environ)
    env["ONCOTRIAGE_DEFER_LOCAL_MODELS"] = "1"
    # A CLOSED PORT, so a regression that started opening a client at import
    # fails here instead of reaching a real endpoint. Nothing in this probe
    # should contact Qdrant at all.
    env["ONCOTRIAGE_QDRANT_URL"] = "http://127.0.0.1:1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    source = _PROBE.format(code_dir=_CODE_DIR, flag=flag, module=module)
    proc = subprocess.run([sys.executable, "-c", source],
                          cwd=_CODE_DIR, env=env, capture_output=True,
                          text=True, timeout=300)
    for line in proc.stdout.splitlines():
        if line.startswith("__RESULT__"):
            return json.loads(line[len("__RESULT__"):]), proc
    return _Absent(f"{module}/{flag}: no result line "
                   f"(rc={proc.returncode})"), proc


_probes = {}
for _module in ("capture", "replay"):
    for _flag in (False, True):
        _probes[(_module, _flag)], _proc = _run_probe(_module, _flag)
        if isinstance(_probes[(_module, _flag)], _Absent):
            print(f"        stderr tail: {_proc.stderr[-600:]}")


def _p(module, flag, key):
    return at(at(_probes, (module, flag)), key)


for _module in ("capture", "replay"):
    check(f"5a  {_module}: the process starts UNPINNED, and its mode follows "
          f"the default it was given",
          (_p(_module, False, "before_pin"), _p(_module, False, "before_mode"),
           _p(_module, True, "before_pin"), _p(_module, True, "before_mode")),
          (None, _GROUPED, None, _PER_TRIAL))
    check(f"5b  {_module}: with the default per-trial, the guard REFUSES "
          f"before the pin and PASSES after it",
          (_p(_module, True, "before_guard"), _p(_module, True, "after_guard")),
          ("refused", "passed"))
    check(f"5c  {_module}: with the default grouped, the guard passes either "
          f"way -- so 5b measured the pin rather than the ordering",
          (_p(_module, False, "before_guard"),
           _p(_module, False, "after_guard")), ("passed", "passed"))
    check(f"5d  {_module}: the pinned mode is grouped under BOTH defaults",
          (_p(_module, False, "after_mode"), _p(_module, True, "after_mode"),
           _p(_module, False, "after_pin"), _p(_module, True, "after_pin")),
          (_GROUPED, _GROUPED, _GROUPED, _GROUPED))
    check(f"5e  {_module}: the constant is untouched in both processes",
          (_p(_module, False, "constant"), _p(_module, True, "constant")),
          (False, True))
    check(f"5f  {_module}: the loud line printed in both processes",
          (size(_p(_module, False, "lines")),
           size(_p(_module, True, "lines"))), (3, 3))
    check(f"5f  {_module}: ...carrying the pending-migration notice",
          (_capture.FIXTURE_CALL_MODE_NOTICE
           in joined(_p(_module, False, "lines")),
           _capture.FIXTURE_CALL_MODE_NOTICE
           in joined(_p(_module, True, "lines"))), (True, True))
    check(f"5g  {_module}: no model was loaded in either process",
          (_p(_module, False, "torch"), _p(_module, False, "transformers"),
           _p(_module, True, "torch"), _p(_module, True, "transformers")),
          (False, False, False, False))

check("5h  capture and replay behave IDENTICALLY -- same mode, same pin, same "
      "guard outcomes -- under both defaults, which is what says the gate "
      "survives the default flip",
      [[_p("capture", f, k) for k in ("before_mode", "after_mode", "after_pin",
                                      "before_guard", "after_guard")]
       for f in (False, True)],
      [[_p("replay", f, k) for k in ("before_mode", "after_mode", "after_pin",
                                     "before_guard", "after_guard")]
       for f in (False, True)])


# ===========================================================================
# SECTION 6 -- THE RECORD, AND THE TUNABLES TRAP
# ===========================================================================

section("SECTION 6 -- what a fixture records about its arm")

# THE ENVIRONMENT BLOCK NAMES THE ARM, through the owner. Asserted by AST
# rather than by calling build_environment_block(), which resolves the live
# Qdrant collection and scrolls it for a digest.
_env_fn = _defs_named(_CAPTURE_TREE, "build_environment_block")
check("6a  build_environment_block exists exactly once", len(_env_fn), 1)


def _env_key_source(fn_node, key):
    """The unparsed VALUE expression the environment dict stores under `key`."""
    if fn_node is None:
        return _Absent("no build_environment_block")
    for node in ast.walk(fn_node):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value == key:
                    return ast.unparse(v)
    return _Absent(f"no {key!r} key")


check("6a  ...and records the arm through the OWNER, so a fixture says on its "
      "face which arm produced it and says the arm that RAN rather than the "
      "one the project is configured for",
      _env_key_source(_env_fn[0] if _env_fn else None, "matching_call_mode"),
      "config.matching_call_mode()")
check("6a  ...non-degeneracy: the same reader finds a neighbouring key, so an "
      "absence above would be a finding rather than a reader that matched "
      "nothing",
      _env_key_source(_env_fn[0] if _env_fn else None, "matching_seed"),
      "MATCHING_SEED")


def _tunable_keys(tree):
    """The string keys of the `"tunables"` dict literal, by AST."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "tunables" \
                        and isinstance(value, ast.Dict):
                    return [k.value for k in value.keys
                            if isinstance(k, ast.Constant)]
    return []


_TUNABLES = _tunable_keys(_CAPTURE_TREE)
check("6b  the tunables dict is non-degenerate", len(_TUNABLES) >= 25, True)

# EVERY RECORDED TUNABLE MUST BE THE NAME OF A CONFIG ATTRIBUTE. File 46's
# diff_tunables() resolves each recorded key with getattr(config, name) and
# reports "<no longer defined>" when it misses -- so a key that is not an
# attribute would be reported as moved on EVERY future fixture, forever. This
# is why the arm is a top-level environment field and not a tunable: the owner
# is a function, so "MATCHING_CALL_MODE" is not an attribute, and
# "MATCHING_PER_TRIAL_CALLS_ENABLED" is one but is the wrong fact -- under the
# pin it can read True on a run that was grouped.
_unresolvable = sorted(n for n in _TUNABLES if not hasattr(config, n))
check("6c  every recorded tunable resolves as a config attribute, so no "
      "future fixture reports a permanent phantom diff", _unresolvable, [])
check("6c  ...control: the two spellings this pass deliberately did NOT use "
      "would have behaved differently under that rule",
      (hasattr(config, "MATCHING_CALL_MODE"),
       hasattr(config, "MATCHING_PER_TRIAL_CALLS_ENABLED")), (False, True))
# EXACT NAMES, NOT A SUBSTRING. The first version of this check filtered on
# "PER_TRIAL" in n and reported MATCHING_OUTPUT_TOKENS_PER_TRIAL -- a real,
# correct, unrelated tunable. A substring is not a name; this project has had
# to relearn that at the BM25 construction-site check and at the query layer's
# revision selector, and it relearned it here.
_ARM_SPELLINGS = {"MATCHING_CALL_MODE", "MATCHING_CALL_MODES",
                  "MATCHING_CALL_MODE_GROUPED", "MATCHING_CALL_MODE_PER_TRIAL",
                  "MATCHING_PER_TRIAL_CALLS_ENABLED"}
check("6c  ...and no spelling of the arm is among the tunables",
      sorted(set(_TUNABLES) & _ARM_SPELLINGS), [])
check("6c  ...non-degeneracy: the same intersection over a copy carrying one "
      "of those spellings finds it, so the empty result is a finding",
      sorted(set(list(_TUNABLES) + ["MATCHING_CALL_MODE"]) & _ARM_SPELLINGS),
      ["MATCHING_CALL_MODE"])

# NO MODEL LOADED IN THIS PROCESS EITHER.
check("6d  no model-bearing library entered sys.modules",
      (("torch" in sys.modules), ("transformers" in sys.modules)),
      (False, False))

# THE THREE REPOSITORY FILES THIS FILE READ ARE UNCHANGED. config.py is
# rewritten in place by tests/test_config_snapshot_date_rot.py, so an
# interleaved serial run is visible here rather than silent.
_changed = sorted(p for p, h in _HASHES_BEFORE.items() if _sha(p) != h)
check("6e  every repository file this test read is byte-unchanged",
      [os.path.basename(p) for p in _changed], [])


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
Created on Sun Aug 24 2026

@author: ramyalsaffar
"""
