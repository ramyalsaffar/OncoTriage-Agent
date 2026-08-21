# Harness endpoint budgets: derived, ordered, and never absent
##############################################################

"""Harness Endpoint Budget Test

THE THREE THINGS THIS PINS, AND THE DEFECT BEHIND EACH.

1.  ``HARNESS_POST_READ_TIMEOUT_SECONDS`` IS DERIVED, NOT TYPED. The value it
    replaced was a literal ``180`` that sat BELOW
    ``MATCHING_REQUEST_TIMEOUT_SECONDS`` -- so a single Stage 5 call allowed to
    run its full server-side budget outlived the client waiting for it, and
    more than half of every request this pipeline has recorded would have been
    reported as a harness TIMEOUT against a server that was working. The
    replacement is an expression over ``MAX_TRUNCATION_SPLITS`` and the two
    measured allowances, so raising the split depth moves the client budget
    with it. A CHECK ON THE VALUE ALONE CANNOT SEE THAT: ``1845`` typed as a
    literal passes every arithmetic assertion, forever, and silently stops
    tracking the split depth. So section 1 checks the value AND the SHAPE, and
    its two controls separate them -- one de-derives the expression to the
    literal it currently equals (the value survives, the shape check fires),
    the other perturbs the exponent (the shape survives, the value check
    fires).

2.  THE READ BUDGET MUST NOT SIT BELOW THE SERVER'S OWN STALL BOUND. That is
    the original defect stated as an inequality, and section 2 plants ``180``
    to show the assertion can fail.

3.  ``ConnectTimeout`` PRECEDES ``Timeout`` IN FILE 19, BY POSITION. Its MRO is
    ConnectTimeout -> ConnectionError -> Timeout, so a single ``except
    Timeout`` catches both tiers of the pair and reports a five-second
    handshake failure as "TIMEOUT after 1845s" -- a false statement about a run
    that never reached the server. Python resolves handlers top to bottom, so
    the ordering IS the behaviour; section 3 reads it off the AST and its
    control reverses the two clauses in an in-memory copy.

AND THE FOURTH, WHICH IS THE ONE THE BRIEF EXPECTED TO FIND BROKEN.

4.  EVERY HARNESS ``requests`` CALL PASSES AN EXPLICIT ``timeout=``. The brief
    for this test asked what the GETs pass "now that GET_TIMEOUT_SECONDS is
    deleted" and named three possible states: inherit the POST budget, pass
    nothing at all, or already pinned. MEASURED: the third.
    ``HARNESS_GET_TIMEOUT_SECONDS`` was not deleted, it is 30, it is
    config-owned, and both of File 18's GETs already pass
    ``HARNESS_GET_TIMEOUT``; File 19 makes no GET at all. So there is nothing
    to replace and section 4 verifies and asserts, which is what that third
    state asks for.

    THE ASSERTION THAT MATTERS IS THE ONE ABOUT THE STATE THAT IS *NOT* THE
    CASE. ``requests`` defaults ``timeout`` to ``None``, which is not a long
    wait but an UNBOUNDED one: a harness that omits it against a server that
    accepts the connection and never answers hangs until a human kills it, with
    no verdict, no exit code and nothing written. Both harnesses had exactly
    that before pass 20f. Section 4 therefore walks every ``requests.<verb>``
    call in BOTH files and requires an explicit ``timeout`` keyword on each,
    with the tier it resolves to; its control strips the keyword from one call
    in an in-memory copy and requires the walk to name it. That is what makes
    the no-timeout state impossible to reintroduce silently rather than merely
    absent today.

WHY THE GET TIER IS SHORT AND THAT IS CORRECT, argued rather than inherited: a
healthy ``/health`` answers in milliseconds and ``/pipeline/info`` makes one
Qdrant metadata call, while a dead port fails in the 5s CONNECT phase and never
reaches the read tier at all. So the read tier bounds only "accepted the
connection and then went quiet on a request that does no pipeline work", and
30s is generous for that. Inheriting the POST budget would mean waiting 30.75
minutes to learn that a health endpoint is wedged.

WHAT THIS FILE DOES NOT DO. It never starts a server, never makes a request,
never imports ``requests`` and never runs either harness -- both are read as
TEXT and parsed. Nothing here can spend money: no harness is executed, so no
POST and no GET is issued.

Section 1's controls evaluate ONE arithmetic expression node in a namespace of
three integers, through ``eval`` on a compiled ``ast.Expression``. That is not
``exec``, it loads no module and it runs no file, so this file needs no
``_EXEC_ALLOWLIST`` entry in ``tests/test_package_invariants.py``.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE SERVER, NO LIVE QDRANT, NO CORPUS, NO
DATABASE, NO GIT HISTORY. NOT in ``tests/run_serial_tests.py``'s collision
matrix, derived rather than assumed: it writes nothing anywhere -- every plant
goes into an in-memory ``ast`` copy -- and the three repository files it READS
(``oncotriage/config.py``, ``18- FastAPI Server Test.py``,
``19- FastAPI Server Batch Test.py``) include one that IS written, by
``tests/test_config_snapshot_date_rot.py`` -- which rewrites only the
``DATA_SNAPSHOT_DATE`` literal and restores it byte-identically, and touches no
``HARNESS_*`` line. The membership rule is intersection in either direction, so
that is stated here rather than left implied: this file reads config.py and
never writes it, and the one writer's edit cannot move anything this file
asserts on.

    python tests/test_harness_endpoint_budget.py
"""

import ast
import os
import sys

# ABOVE THE PACKAGE IMPORTS ON PURPOSE. oncotriage/agent/deps.py reads this
# variable ONCE, at its own import; an assignment underneath the imports
# reaches nothing. Nothing here needs a local model, and this is the second
# line of defence that says so.
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


def check_true(label: str, condition) -> None:
    check(label, bool(condition), True)


def section(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def guarded(fn, *args, **kwargs):
    """Call ``fn`` and convert a raise into a value ``check`` can fail on.

    A CONTROL THAT ABORTS IS NOT A CONTROL. This project has shipped that shape
    seven times: a plant makes production code raise, the raise escapes while
    ``check()``'s argument is being evaluated, and the run reports one traceback
    where it owed a summary and every remaining result. Every call below that a
    plant could make raise goes through here.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:                                   # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


def at(seq, index, what):
    """``seq[index]`` without ``IndexError``.

    Same rule as ``guarded``: a defect that shortens a list is exactly what
    these sections are looking for, so indexing it must produce a named absence
    rather than an abort.
    """
    try:
        return seq[index]
    except Exception:                                          # noqa: BLE001
        return f"<MISSING {what}: only {len(seq)} item(s)>"


#------------------------------------------------------------------------------


# ===========================================================================
# THE THREE FILES, READ AS TEXT
# ===========================================================================
#
# Each path is derived from the module's OWN __file__ rather than from this
# test's location, so a future move of either cannot leave this file silently
# parsing nothing. The two harnesses are numbered filenames with spaces and are
# not importable at all, which is why they are read rather than imported -- and
# is also why reading them cannot execute their __main__ blocks and cannot
# spend a cent.

_CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(config.__file__)))
_CONFIG_PY = os.path.abspath(config.__file__)
_FILE_18 = os.path.join(_CODE_DIR, "18- FastAPI Server Test.py")
_FILE_19 = os.path.join(_CODE_DIR, "19- FastAPI Server Batch Test.py")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


_CONFIG_SRC = _read(_CONFIG_PY)
_SRC_18 = _read(_FILE_18)
_SRC_19 = _read(_FILE_19)

_TREE_CONFIG = ast.parse(_CONFIG_SRC)
_TREE_18 = ast.parse(_SRC_18)
_TREE_19 = ast.parse(_SRC_19)


def _assignment(tree, name):
    """The module-level ``ast.Assign`` binding ``name``, or None.

    Module level only. A name bound inside a function is not the constant this
    file is about, and matching one would be a check that passes for the wrong
    reason.
    """
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node
    return None


def _names_in(node):
    return sorted({n.id for n in ast.walk(node) if isinstance(n, ast.Name)})


def _literals_in(node):
    return sorted(n.value for n in ast.walk(node)
                  if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
                  and not isinstance(n.value, bool))


def _eval_expr(node, namespace):
    """Evaluate one expression NODE in an explicit namespace.

    Used only by section 1's controls, which need to know what a PERTURBED
    formula would produce. ``eval`` on a compiled ``ast.Expression`` is not
    ``exec``: it loads no module, runs no file and reaches nothing this
    namespace does not hand it.
    """
    return eval(compile(ast.Expression(body=node), "<budget>", "eval"),
                {"__builtins__": {}}, dict(namespace))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 1 -- THE READ BUDGET IS DERIVED, IN VALUE AND IN SHAPE
# ===========================================================================

section("SECTION 1 -- the POST read budget is derived, not typed")

_INPUTS = {
    "MAX_TRUNCATION_SPLITS": config.MAX_TRUNCATION_SPLITS,
    "HARNESS_MATCHING_CALL_ALLOWANCE_SECONDS":
        config.HARNESS_MATCHING_CALL_ALLOWANCE_SECONDS,
    "HARNESS_NON_LLM_ALLOWANCE_SECONDS": config.HARNESS_NON_LLM_ALLOWANCE_SECONDS,
}


def recompute(splits, call_allowance, non_llm_allowance):
    """The worst-case arithmetic, restated independently of config.py.

    A batch halved to depth ``splits`` issues 1 + 2 + 4 + ... = 2**(splits+1)-1
    requests and every one of them SUCCEEDS, so the client budget is that many
    working calls plus everything in the request that is not Stage 5.
    """
    return (2 ** (splits + 1) - 1) * call_allowance + non_llm_allowance


_RECOMPUTED = recompute(
    splits=config.MAX_TRUNCATION_SPLITS,
    call_allowance=config.HARNESS_MATCHING_CALL_ALLOWANCE_SECONDS,
    non_llm_allowance=config.HARNESS_NON_LLM_ALLOWANCE_SECONDS,
)

check("1a  the shipped read budget equals the worst-case arithmetic "
      "recomputed here from the three config inputs",
      config.HARNESS_POST_READ_TIMEOUT_SECONDS, _RECOMPUTED)

check("1b  ...and the inputs are non-degenerate (a formula over zeros would "
      "satisfy 1a for any shape at all)",
      sorted(v for v in _INPUTS.values() if v > 0), sorted(_INPUTS.values()))

_BUDGET_ASSIGN = _assignment(_TREE_CONFIG, "HARNESS_POST_READ_TIMEOUT_SECONDS")
check("1c  HARNESS_POST_READ_TIMEOUT_SECONDS is assigned at module scope in "
      "oncotriage/config.py (non-degeneracy for every shape check below)",
      _BUDGET_ASSIGN is not None, True)

_BUDGET_NAMES = _names_in(_BUDGET_ASSIGN.value) if _BUDGET_ASSIGN else []
check("1d  its right-hand side READS the three config constants by name, so "
      "raising the split depth or re-measuring an allowance moves the client "
      "budget without anyone editing this number",
      _BUDGET_NAMES, sorted(_INPUTS))

check("1e  ...and the only bare numbers left in it are the two the shape "
      "itself needs -- the base 2 and the -1/+1 of the geometric sum. The "
      "value 1845 appears nowhere.",
      (_literals_in(_BUDGET_ASSIGN.value) if _BUDGET_ASSIGN else None,
       config.HARNESS_POST_READ_TIMEOUT_SECONDS
       in (_literals_in(_BUDGET_ASSIGN.value) if _BUDGET_ASSIGN else [])),
      ([1, 1, 2], False))

_POST_TUPLE = _assignment(_TREE_CONFIG, "HARNESS_POST_TIMEOUT")
check("1f  HARNESS_POST_TIMEOUT is the (connect, read) PAIR assembled from the "
      "two tiers by name, so no call site can pass one and forget the other",
      (_names_in(_POST_TUPLE.value) if _POST_TUPLE else None,
       config.HARNESS_POST_TIMEOUT),
      (["HARNESS_CONNECT_TIMEOUT_SECONDS", "HARNESS_POST_READ_TIMEOUT_SECONDS"],
       (config.HARNESS_CONNECT_TIMEOUT_SECONDS,
        config.HARNESS_POST_READ_TIMEOUT_SECONDS)))

# --- CONTROL 1: de-derive the expression to the literal it equals today -----
# THE VALUE SURVIVES AND THE SHAPE CHECK MUST FIRE. This is the regression the
# arithmetic assertions cannot see on their own, and it is the reason 1d and 1e
# exist beside 1a.
_DEDERIVED = ast.parse(f"X = {config.HARNESS_POST_READ_TIMEOUT_SECONDS}").body[0]
check("1g  CONTROL: a de-derived budget (the literal 1845, value unchanged) "
      "still satisfies the ARITHMETIC...",
      guarded(_eval_expr, _DEDERIVED.value, _INPUTS),
      config.HARNESS_POST_READ_TIMEOUT_SECONDS)
check("1h  ...and 1d FIRES on it: it reads no config name at all",
      _names_in(_DEDERIVED.value), [])
check("1i  ...and 1e FIRES on it too: the budget's own value is now a literal "
      "inside it",
      config.HARNESS_POST_READ_TIMEOUT_SECONDS in _literals_in(_DEDERIVED.value),
      True)

# --- CONTROL 2: perturb the exponent, leaving the shape intact --------------
# The mirror image: every NAME is still read, so 1d and 1e pass, and the value
# moves. Planted into an in-memory copy of the real right-hand side.
_PERTURBED = ast.parse(
    "(2 ** MAX_TRUNCATION_SPLITS - 1) * HARNESS_MATCHING_CALL_ALLOWANCE_SECONDS"
    " + HARNESS_NON_LLM_ALLOWANCE_SECONDS").body[0]
_PERTURBED_VALUE = guarded(_eval_expr, _PERTURBED.value, _INPUTS)
check("1j  CONTROL: dropping the +1 from the exponent keeps every config name "
      "in the expression, so 1d cannot see it...",
      _names_in(_PERTURBED.value), sorted(_INPUTS))
check("1k  ...and 1a FIRES on it: 7 successful calls budgeted where 15 are "
      "possible, which is the under-budget defect that 180 was",
      (_PERTURBED_VALUE != config.HARNESS_POST_READ_TIMEOUT_SECONDS,
       _PERTURBED_VALUE),
      (True, 7 * config.HARNESS_MATCHING_CALL_ALLOWANCE_SECONDS
       + config.HARNESS_NON_LLM_ALLOWANCE_SECONDS))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 2 -- THE CLIENT MUST OUTLIVE ONE SERVER-SIDE STALL BOUND
# ===========================================================================

section("SECTION 2 -- the read budget vs the server's own request timeout")

check("2a  the derived read budget is at least MATCHING_REQUEST_TIMEOUT_"
      "SECONDS: a client that gives up before the server's own bound on ONE "
      "call reports a timeout for a request the server was still legitimately "
      "working on",
      config.HARNESS_POST_READ_TIMEOUT_SECONDS
      >= config.MATCHING_REQUEST_TIMEOUT_SECONDS, True)

check("2b  ...and it is not merely equal, which would leave zero allowance for "
      "the non-Stage-5 work every request also does",
      config.HARNESS_POST_READ_TIMEOUT_SECONDS
      > config.MATCHING_REQUEST_TIMEOUT_SECONDS, True)

check("2c  non-degeneracy: the server-side bound is a positive number, so 2a "
      "is not satisfied by comparing against zero",
      config.MATCHING_REQUEST_TIMEOUT_SECONDS > 0, True)

# --- CONTROL: the value this replaced ---------------------------------------
_OLD_BUDGET = 180
check("2d  CONTROL: 180 -- the literal this budget replaced -- FAILS 2a "
      "against the shipped server bound",
      _OLD_BUDGET >= config.MATCHING_REQUEST_TIMEOUT_SECONDS, False)
check("2e  ...and the gap is the defect stated as a number: the client gave up "
      "before a single Stage 5 call could even reach its own limit",
      config.MATCHING_REQUEST_TIMEOUT_SECONDS - _OLD_BUDGET > 0, True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 3 -- FILE 19 CATCHES THE NARROW ARM FIRST
# ===========================================================================

section("SECTION 3 -- ConnectTimeout precedes Timeout in File 19")


def _handler_names(tree):
    """Every ``except`` clause in the file, as (dotted name, position).

    Positions are the index of the handler WITHIN its own ``try``, which is
    what Python resolves on. Comparing line numbers across different ``try``
    statements would be meaningless.
    """
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for idx, handler in enumerate(node.handlers):
                if handler.type is None:
                    out.append(("<bare>", idx))
                else:
                    out.append((ast.unparse(handler.type), idx))
    return out


def _order_of(tree, narrow, broad):
    """(narrow_index, broad_index) within the ONE try holding both, or None."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            names = [ast.unparse(h.type) if h.type is not None else "<bare>"
                     for h in node.handlers]
            if narrow in names and broad in names:
                return (names.index(narrow), names.index(broad))
    return None


_NARROW = "requests.exceptions.ConnectTimeout"
_BROAD = "requests.exceptions.Timeout"

_ALL_19 = _handler_names(_TREE_19)
check("3a  File 19 was parsed and does catch exceptions (non-degeneracy for "
      "the ordering check)",
      len(_ALL_19) > 0, True)

_ORDER = _order_of(_TREE_19, _NARROW, _BROAD)
check("3b  both arms live in ONE try statement -- an ordering claim about two "
      "different try blocks would be meaningless",
      _ORDER is not None, True)

check("3c  the NARROW arm (ConnectTimeout) is caught BEFORE the broad one "
      "(Timeout, its base class), so a 5s handshake failure is not reported as "
      "a 1845s read timeout",
      (at(_ORDER or [], 0, "narrow index") < at(_ORDER or [], 1, "broad index")
       if _ORDER else "<no try holds both>"),
      True)

check("3d  ...and a bare `except:` does not precede either of them",
      [i for name, i in _ALL_19
       if name == "<bare>" and _ORDER and i < max(_ORDER)], [])

# --- CONTROL: reverse the two clauses in an in-memory copy ------------------
_REVERSED_TREE = ast.parse(_SRC_19)
_SWAPPED = False
for _node in ast.walk(_REVERSED_TREE):
    if isinstance(_node, ast.Try):
        _names = [ast.unparse(h.type) if h.type is not None else "<bare>"
                  for h in _node.handlers]
        if _NARROW in _names and _BROAD in _names:
            _i, _j = _names.index(_NARROW), _names.index(_BROAD)
            _node.handlers[_i], _node.handlers[_j] = (_node.handlers[_j],
                                                      _node.handlers[_i])
            _SWAPPED = True
            break

check("3e  CONTROL: the plant took -- the two handlers were swapped in the "
      "in-memory copy", _SWAPPED, True)
_CTRL_ORDER = _order_of(_REVERSED_TREE, _NARROW, _BROAD)
check("3f  ...and 3c FIRES on it: the broad arm would now swallow every "
      "connect failure",
      (at(_CTRL_ORDER or [], 0, "narrow index")
       < at(_CTRL_ORDER or [], 1, "broad index")
       if _CTRL_ORDER else "<no try holds both>"),
      False)
check("3g  ...against a shipped file this test never wrote: the plant lives in "
      "an ast copy and File 19's text is unchanged",
      _read(_FILE_19) == _SRC_19, True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 4 -- THE GET BUDGET, AND NO CALL WITHOUT A TIMEOUT
# ===========================================================================

section("SECTION 4 -- the GET budget is config-owned, and every call is bounded")

# --- 4.1 the budget exists, is a pair, and is CHOSEN rather than inherited --

check("4a  HARNESS_GET_TIMEOUT_SECONDS exists in oncotriage/config.py -- the "
      "brief's 'inherits the POST budget' and 'passes no timeout at all' "
      "states are both absent; this is the third, 'already pinned'",
      (hasattr(config, "HARNESS_GET_TIMEOUT_SECONDS"),
       config.HARNESS_GET_TIMEOUT_SECONDS), (True, 30))

_GET_TUPLE = _assignment(_TREE_CONFIG, "HARNESS_GET_TIMEOUT")
check("4b  HARNESS_GET_TIMEOUT is the (connect, read) pair, built from the "
      "SHARED connect tier and its own read tier by name",
      (_names_in(_GET_TUPLE.value) if _GET_TUPLE else None,
       config.HARNESS_GET_TIMEOUT),
      (["HARNESS_CONNECT_TIMEOUT_SECONDS", "HARNESS_GET_TIMEOUT_SECONDS"],
       (config.HARNESS_CONNECT_TIMEOUT_SECONDS,
        config.HARNESS_GET_TIMEOUT_SECONDS)))

check("4c  the two budgets SHARE the connect tier: a dead port is a dead port "
      "whatever verb is about to be sent",
      at(config.HARNESS_GET_TIMEOUT, 0, "GET connect tier"),
      at(config.HARNESS_POST_TIMEOUT, 0, "POST connect tier"))

check("4d  the GET read tier is DELIBERATELY SHORTER than the POST one: "
      "/health touches nothing and /pipeline/info makes one Qdrant metadata "
      "call, so inheriting 30.75 minutes would mean waiting that long to learn "
      "a health endpoint is wedged",
      config.HARNESS_GET_TIMEOUT_SECONDS
      < config.HARNESS_POST_READ_TIMEOUT_SECONDS, True)

check("4e  ...and it is not zero or negative, which requests would treat as an "
      "instant failure rather than a budget",
      config.HARNESS_GET_TIMEOUT_SECONDS > 0, True)

# --- 4.2 every requests call in both harnesses carries an explicit timeout --
#
# THIS IS THE ASSERTION THAT MAKES THE NO-TIMEOUT STATE UNREINTRODUCIBLE.
# requests defaults timeout to None, which is UNBOUNDED, and both files were
# there before pass 20f. The walk covers the attribute form (`requests.post`),
# which is the only form either file uses -- and it says so by ALSO reporting
# any bare-name call to post/get/put/patch/delete, so a future
# `from requests import post` cannot slip past by changing reference form.

_VERBS = ("get", "post", "put", "patch", "delete", "head", "options", "request")


def _requests_calls(tree):
    """(dotted callee, lineno, timeout expression or None) for every call."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _VERBS:
            root = func.value
            if not (isinstance(root, ast.Name) and root.id == "requests"):
                continue
            callee = f"requests.{func.attr}"
        elif isinstance(func, ast.Name) and func.id in _VERBS:
            # The from-import form. Not used by either file today; reported so
            # that adopting it cannot silently escape this walk.
            callee = f"<bare>{func.id}"
        else:
            continue
        timeout = None
        for kw in node.keywords:
            if kw.arg == "timeout":
                timeout = ast.unparse(kw.value)
        found.append((callee, node.lineno, timeout))
    return found


_CALLS_18 = _requests_calls(_TREE_18)
_CALLS_19 = _requests_calls(_TREE_19)
_ALL_CALLS = [("18-", c) for c in _CALLS_18] + [("19-", c) for c in _CALLS_19]

check("4f  both harnesses were parsed and the walk found the calls it is "
      "about (non-degeneracy: a walk that matched nothing passes 4g for free)",
      (len(_CALLS_18), len(_CALLS_19)), (4, 1))

check("4g  EVERY requests call in both harnesses passes an explicit timeout= "
      "-- the omitted-timeout state is a hang with no verdict, not a long wait",
      [f"{f}{c[0]} line {c[1]}" for f, c in _ALL_CALLS if c[2] is None], [])

# `c[2] or "<no timeout>"` RATHER THAN `c[2]`, AND THE REVERT HARNESS IS WHY.
# A missing keyword makes it None, and sorted() comparing None with a str
# raises TypeError -- so the ONE plant section 4 exists to catch would abort
# the file here, one line after 4g had correctly reported it, and the run would
# print a traceback where it owed a summary and every remaining result. That is
# the eighth time this project has shipped that shape; it was found by reverting
# the fix for real, not by reading.
check("4h  every GET is bounded by the GET budget and every POST by the POST "
      "budget, so neither inherits the other's",
      sorted({(c[0], c[2] or "<no timeout>") for _f, c in _ALL_CALLS}),
      [("requests.get", "GET_TIMEOUT"), ("requests.post", "POST_TIMEOUT")])

check("4i  ...and those two module aliases ARE the config constants, so 4h is "
      "not satisfied by a local literal wearing the right name",
      sorted(
          (t.targets[0].id, ast.unparse(t.value))
          for tree in (_TREE_18, _TREE_19)
          for t in tree.body
          if isinstance(t, ast.Assign) and isinstance(t.targets[0], ast.Name)
          and t.targets[0].id in ("GET_TIMEOUT", "POST_TIMEOUT")),
      [("GET_TIMEOUT", "HARNESS_GET_TIMEOUT"),
       ("POST_TIMEOUT", "HARNESS_POST_TIMEOUT"),
       ("POST_TIMEOUT", "HARNESS_POST_TIMEOUT")])

check("4j  File 19 makes no GET at all, which is why it imports no GET budget "
      "-- stated so that adding one and forgetting the timeout fails 4g",
      [c for c in _CALLS_19 if c[0] == "requests.get"], [])

# --- CONTROL: strip the timeout keyword from one call -----------------------
_STRIPPED = ast.parse(_SRC_18)
_STRIP_TARGET = None
for _node in ast.walk(_STRIPPED):
    if (isinstance(_node, ast.Call) and isinstance(_node.func, ast.Attribute)
            and _node.func.attr == "get"
            and isinstance(_node.func.value, ast.Name)
            and _node.func.value.id == "requests"
            and any(kw.arg == "timeout" for kw in _node.keywords)):
        _STRIP_TARGET = _node.lineno
        _node.keywords = [kw for kw in _node.keywords if kw.arg != "timeout"]
        break

check("4k  CONTROL: the plant took -- one GET in the in-memory copy lost its "
      "timeout keyword", _STRIP_TARGET is not None, True)
_CTRL_CALLS = _requests_calls(_STRIPPED)
check("4l  ...and 4g FIRES on it, naming the call rather than reporting a "
      "count",
      [f"{c[0]} line {c[1]}" for c in _CTRL_CALLS if c[2] is None],
      [f"requests.get line {_STRIP_TARGET}"])
check("4m  ...and it is the ONLY one it names: the other three calls in the "
      "copy are untouched",
      len([c for c in _CTRL_CALLS if c[2] is not None]), len(_CALLS_18) - 1)
check("4n  ...against a shipped file this test never wrote",
      _read(_FILE_18) == _SRC_18, True)

# --- CONTROL: point a GET at the POST budget --------------------------------
# The brief's FIRST state -- "inherits the full POST budget" -- is not the case
# and must stay not the case. 4h is what would catch it, so 4h is shown to.
_INHERITED = ast.parse(_SRC_18)
for _node in ast.walk(_INHERITED):
    if (isinstance(_node, ast.Call) and isinstance(_node.func, ast.Attribute)
            and _node.func.attr == "get"):
        for _kw in _node.keywords:
            if _kw.arg == "timeout":
                _kw.value = ast.Name(id="POST_TIMEOUT", ctx=ast.Load())
_CTRL_INHERIT = {(c[0], c[2] or "<no timeout>")
                 for c in _requests_calls(_INHERITED)}
check("4o  CONTROL: a GET pointed at POST_TIMEOUT FIRES 4h -- the "
      "'inherited, not chosen' state is detected, not merely absent today",
      sorted(_CTRL_INHERIT),
      [("requests.get", "POST_TIMEOUT"), ("requests.post", "POST_TIMEOUT")])


#------------------------------------------------------------------------------


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
Created on Thu Aug 20 2026

@author: ramyalsaffar
"""
